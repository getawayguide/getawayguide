"""Hero picker: browse a backup album, see any photo as the article hero it would
become (16:9 crop, scrim, headline), nudge the crop up and down, and save picks.

    python tools/hero_picker.py          then open http://127.0.0.1:5004

Read-only against ~/Backup and Images/. Thumbnails are cached in
.tmp/hero_picker_cache/, picks are written to .tmp/hero_picks.json. Meant to be
folded into the article editor once the theme ships.

Thumbnails are cut to the same 16:9 the hero uses, so what you see in the strip
is the crop you get. They are built by a small thread pool the moment an album
is opened, and JPEGs are decoded at reduced scale, which is most of the reason
the strip fills quickly now.

Full bleed (the header button, or F) hides the strip and shows the hero exactly
as the site cuts it for the rule being set: the real window width at the fixed
680 / 560 / 470 height, the phone frame centered at 402px, and the photo served
at the device pixels that frame needs (up to 4000 wide) rather than the 1600 the
stage uses beside the strip. Left and right arrows step through the photos.

Two things the frame gets from the site rather than from a constant: the article
banner is an aspect ratio (1440/560, floor 420px), not a fixed height, so it grows
with the window; and the nav is position:fixed over a hero that starts at top:0, so
it hides the top 60px (63 on a phone) behind 96% white. The picker draws that band,
because a crop judged on pixels the nav covers is judged on pixels nobody sees.
"""
import hashlib
import os
import json
import re
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file
from PIL import Image, ImageFilter, ImageOps, ImageStat

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

SITE = Path(__file__).resolve().parent.parent
BACKUP = Path.home() / "Backup"
CACHE = SITE / ".tmp" / "hero_picker_cache"
PICKS = SITE / ".tmp" / "hero_picks.json"
STARS = SITE / ".tmp" / "hero_stars.json"
CACHE.mkdir(parents=True, exist_ok=True)
EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}

# The shared-album sync leaves orphaned video poster frames behind, named
# <hash>.jpgthumb_00001.jpg -- 670 of them, all 480px, none with an original in
# its album. They are .jpg, so an extension filter alone lets them through and
# they show up as unusable tiles. Videos are already excluded by extension.
THUMB_RE = re.compile(r"\.[A-Za-z0-9]+thumb_\d+\.[A-Za-z0-9]+$")


def is_stray_thumb(name):
    """True for a sync artifact that can never be used as a photo."""
    return bool(THUMB_RE.search(str(name)))


# The hero is a FIXED HEIGHT inside a viewport of whatever width, so its shape
# is not a constant: 1440 gives 2.12:1 and a 1920 monitor gives 2.82:1 off the
# same 680px. Judging a crop at 1440 when the screen is 1920 shows a frame
# noticeably taller than the one that will exist. Heights are read off the
# live site; the ratio is computed per width.
# The article banner is the exception: it is NOT a fixed height. artifact.css
# gives .article-hero.gg-banner `aspect-ratio:1440/560; min-height:420px`, so it
# grows with the window (741px tall at 1905, 498 at 1280) and only the phone
# pins it, at 420. The picker used to cut it at a flat 560, which on a monitor
# showed a banner a third shorter than the page does.
SHAPE_H = {"home": 680}
SHAPE_H_PHONE = {"home": 470, "article": 420}
ARTICLE_RATIO, ARTICLE_MIN_H = 1440 / 560, 420
DEFAULT_VW = 1440


def shape_box(shape="home", vw=DEFAULT_VW):
    """The (width, height) the site cuts for this hero at this viewport width."""
    try:
        vw = max(320, min(3840, int(vw)))
    except (TypeError, ValueError):
        vw = DEFAULT_VW
    if shape == "wide":
        return vw, vw / (16 / 9)
    if vw <= 768:
        return vw, SHAPE_H_PHONE.get(shape, SHAPE_H_PHONE["home"])
    if shape == "article":
        return vw, max(ARTICLE_MIN_H, vw / ARTICLE_RATIO)
    return vw, SHAPE_H["home"]


def shape_ratio(shape="home", vw=DEFAULT_VW):
    """The aspect the site cuts for this hero at this viewport width."""
    w, h = shape_box(shape, vw)
    return w / h


SHAPES = {"home": shape_ratio("home"), "article": shape_ratio("article"),
          "wide": 16 / 9}
HERO_RATIO = SHAPES["home"]

# The site cuts the hero at three widths and its own media queries are the
# breakpoints, so judging at exactly these widths is what makes a saved crop
# pasteable straight into artifact.css.
BREAKPOINTS = [
    {"key": "desktop", "label": "Monitor", "vw": 1905, "media": ""},
    {"key": "laptop", "label": "Laptop", "vw": 1280, "media": "(max-width:1400px)"},
    {"key": "phone", "label": "Phone", "vw": 402, "media": "(max-width:768px)"},
]


def free_axis(iw, ih, bw, bh):
    """Which axis object-fit:cover leaves free, and how much of the photo shows.

    cover scales the image until the box is filled, so whichever dimension
    runs out first is pinned and the other one overflows and can slide. A
    portrait in a wide hero is pinned by width and slides up and down; a
    16:9 photo in the phone's nearly square box is pinned by HEIGHT and
    slides SIDEWAYS. Setting the pinned axis does nothing at all, which is
    why the picker used to look stuck on some photos.
    """
    if iw / ih > bw / bh:
        return "x", bw / (bh * iw / ih)
    return "y", bh / (bw * ih / iw)


def pick_crops(pick):
    """A crop per breakpoint, upgrading a pick saved under the old one-value
    schema so nothing already chosen is lost."""
    if pick and isinstance(pick.get("crops"), dict):
        return {b["key"]: pick["crops"].get(b["key"], 50) for b in BREAKPOINTS}
    legacy = pick.get("objectPositionY", 50) if pick else 50
    # the old value was judged on a desktop shape, so it carries to the two
    # wide breakpoints; the phone is a different shape and starts centred
    return {"desktop": legacy, "laptop": legacy, "phone": 50}
HERO_MIN = 2880          # native px across the crop for a sharp 1440 hero at 2x
HERO_FAIR = 1920         # below this it is visibly soft even at 1x
THUMB_W = 340            # strip thumbnail width
POOL = ThreadPoolExecutor(max_workers=min(4, (os.cpu_count() or 4)))   # was 8: decodes starved the requests
_ACTIVE = {"album": None}            # the album on screen: warm-up for any other is skipped
# QA 2026-10-02: scan_album() blocked the /photos request behind every warm() job already queued
# (thousands after a few albums), so a changed album never loaded. Scans measure on their own pool.
SCAN_POOL = ThreadPoolExecutor(max_workers=3)

# album folder stem -> country page it feeds
ALBUM_COUNTRY = {
    "albania": "Albania", "argentina & paraguay": "Argentina", "armenia": "Armenia",
    "australia": "Australia", "bali": "Indonesia", "bosnia": "Bosnia", "brazil": "Brazil",
    "cologne": "Germany", "colombia": "Colombia", "el salvador": "El Salvador",
    "georgia": "Georgia", "greece 2.0": "Greece", "guatemala": "Guatemala",
    "india": "India", "italy": "Italy", "japan": "Japan", "kosovo": "Kosovo",
    "mexico": "Mexico", "new zealand": "New Zealand", "nicaragua": "Nicaragua",
    "north macedonia": "North Macedonia", "patagonia": "Chile", "peru": "Peru",
    "philippines": "Philippines", "serbia": "Serbia", "turkey 2.0": "Türkiye",
    "vietnam": "Vietnam",
}
TITLES = {
    "Albania": "Albania Travel Guide: Himarë, Valbona to Theth Hike",
    "El Salvador": "El Salvador Travel Guide with Perfect 10 Day Itinerary",
    "Georgia": "Georgia Travel Guide: Tbilisi & the Kakheti Wine Region",
    "Kosovo": "Kosovo Travel Guide: Pristina, Prizren & the Rugova Valley",
    "Peru": "Peru Travel Guide: Cusco, Machu Picchu & Sacred Valley",
    "Philippines": "Philippines Travel Guide: Coron, El Nido & Port Barton",
    "Türkiye": "Türkiye Travel Guide: Istanbul, Cappadocia & the Coast",
}

app = Flask(__name__)


def _posix(p):
    """Photo paths are compared as strings against /photos output, which is
    as_posix(). An older save wrote them with Windows separators, so a stored
    pick never matched its own photo: the strip highlighted nothing and the
    stage came up blank even though the country read "1 of 27 picked"."""
    return p.replace("\\", "/") if isinstance(p, str) else p


def read_picks():
    if not PICKS.exists():
        return {}
    picks = json.loads(PICKS.read_text(encoding="utf-8"))
    for v in picks.values():
        if isinstance(v, dict) and "path" in v:
            v["path"] = _posix(v["path"])
    return picks


def read_stars():
    """country -> [photo path], the shortlist you keep while you compare."""
    if not STARS.exists():
        return {}
    stars = json.loads(STARS.read_text(encoding="utf-8"))
    return {k: [_posix(p) for p in v] for k, v in stars.items()}


def albums():
    picked = read_picks()
    starred = read_stars()
    out = []
    for p in sorted(BACKUP.iterdir()):
        if not p.is_dir() or p.name.startswith("_") or p.name.endswith(".replacing"):
            continue
        stem = p.name.split("(")[0].strip().lower()
        # B8: an unmapped folder used its FULL name (year included) as the
        # country, so picks saved under "Cuba (2025)" instead of "Cuba"
        country = ALBUM_COUNTRY.get(stem, p.name.split("(")[0].strip())
        pick = picked.get(country)
        out.append({"folder": p.name, "country": country,
                    "picked": bool(pick),
                    "pickName": pick["name"] if pick else "",
                    "pickPath": pick["path"] if pick else "",
                    "pickCrops": pick_crops(pick),
                    "pickScrim": pick.get("scrim", 100) if pick else 100,
                    "pickTitle": pick.get("title", "") if pick else "",
                    "pickAngle": pick.get("angle", 0) if pick else 0,
                    "stars": starred.get(country, [])})
    return out


def cache_path(src, w, crop, ratio, angle=0.0):
    key = (f"{src}|{src.stat().st_mtime_ns}|{w}|"
           f"{('c%.4f' % ratio) if crop else 'f'}|a{angle:.2f}")
    return CACHE / (hashlib.md5(key.encode()).hexdigest() + ".jpg")


def straighten(im, angle):
    """Rotate by `angle` degrees (counter-clockwise for positive, as PIL and the photo
    editor's bake do) and crop to the largest inscribed rectangle of the original aspect,
    so no blank corner survives. The same formula tools/photo_editor.py bakes with, so the
    stage shows the exact frame gen_hero_variants --angle will cut."""
    import math
    angle = float(angle or 0)
    if abs(angle) < 1e-3:
        return im
    w, h = im.size
    rad = math.radians(abs(angle))
    scale = 1.0 / (math.cos(rad) + (max(w, h) / min(w, h)) * math.sin(rad))
    rot = im.rotate(angle, resample=Image.BICUBIC, expand=True)
    cw, ch = int(w * scale), int(h * scale)
    cx, cy = rot.width / 2, rot.height / 2
    return rot.crop((int(cx - cw / 2), int(cy - ch / 2), int(cx - cw / 2) + cw, int(cy - ch / 2) + ch))


def build(src, w, crop, ratio=None, angle=0.0):
    """Render one cached variant. `crop` cuts the same shape the hero uses."""
    ratio = ratio or HERO_RATIO
    cp = cache_path(src, w, crop, ratio, angle)
    if cp.exists():
        return cp
    im = Image.open(src)
    try:                                  # JPEG decodes at 1/2, 1/4, 1/8 for free
        im.draft("RGB", (w * 2, int(w * 2 / ratio)))
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    icc = im.info.get("icc_profile")      # Display P3, must survive the convert
    if abs(float(angle or 0)) > 1e-3:
        # Rotate a cached, already-scaled JPEG of the photo, not the 5712px HEIC: decoding
        # the HEIC and rotating it full size took 7 s per drag step, and the stage sat
        # untilted until it arrived, which read as the slider doing nothing (Kevin,
        # 2026-09-26). The un-rotated variant at 1.25x the served width is built once and
        # cached like every other render; each angle then costs a JPEG decode and a
        # rotate at that size.
        base = Image.open(build(src, int(w * 1.25), False, ratio))
        base.load()
        im = straighten(base.convert("RGB"), angle)
    if crop:
        im = ImageOps.fit(im, (w, round(w / ratio)), Image.LANCZOS,
                          centering=(0.5, 0.5))
    elif im.width > w:
        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    # written aside first so a half-encoded file is never served: two threads
    # can ask for the same variant at once
    tmp = cp.with_name(cp.stem + f".{threading.get_ident()}.part.jpg")
    im.convert("RGB").save(tmp, "JPEG", quality=80, icc_profile=icc)
    try:
        tmp.replace(cp)
    except OSError:
        # the warm pool and the request thread can build the same variant at
        # once; whoever lands first wins and the loser just cleans up
        tmp.unlink(missing_ok=True)
        if not cp.exists():
            raise
    return cp


def focus_score(src):
    """How much fine detail the frame holds. Blurry and heavily compressed shots
    score low; it is only meaningful relative to the rest of the album.

    Measured on the strip thumbnail, which is already being built, so scoring
    the album costs nothing beyond the thumbnails themselves."""
    try:
        g = Image.open(build(src, THUMB_W, False)).convert("L")
        return round(ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).stddev[0], 2)
    except Exception:
        return None


_PHOTO_CACHE = {}
_QUALITY = {}
_QLOCK = threading.Lock()


def warm(folder, rows):
    """Cut every thumbnail and score every frame, off the request thread, for the album on screen
    only: a job for an album you have left is dropped when its turn comes (2026-10-02)."""
    _ACTIVE["album"] = folder
    def one(r):
        if _ACTIVE["album"] != folder:
            return
        with _QLOCK:
            if r["path"] in _QUALITY.get(folder, {}):
                return                                   # scored already (a revisit)
        src = BACKUP / r["path"]
        try:
            # focus_score builds the ONE uncropped thumb the strip also serves;
            # cutting a per-ratio crop here is what used to double the decodes
            s = focus_score(src)
        except Exception:
            s = None
        with _QLOCK:
            _QUALITY.setdefault(folder, {})[r["path"]] = s
    for r in rows:
        POOL.submit(one, r)


@app.get("/")
def index():
    return PAGE


@app.get("/albums")
def get_albums():
    return jsonify(albums())


# ---- fonts, served from the repo so the picker works with no internet -------
# This tool used to pull its two typefaces from fonts.googleapis.com. That is a
# blocking stylesheet in the head, so with no connection the browser sat on the
# request until it failed and then fell back to system type: the picker came up
# late and looking wrong, which is no way to judge a hero crop. The site already
# self-hosts both families (fonts.css + fonts/*.woff2, the same localisation the
# nav flags got), so the picker now serves those. Same reasoning as the flagcdn
# removal: no third-party origin in the critical path.
@app.get("/fonts.css")
def fonts_css():
    f = SITE / "fonts.css"
    if not f.is_file():
        abort(404)
    return send_file(f, mimetype="text/css")


@app.get("/fonts/<path:name>")
def font_file(name):
    f = (SITE / "fonts" / name).resolve()
    if (SITE / "fonts").resolve() not in f.parents or not f.is_file():
        abort(404)              # same containment check /img and /photos make
    return send_file(f, mimetype="font/woff2")


def resolve_src(p, display=False):
    """A photo path from the picker: an album photo under ~/Backup, or 'site:Images/...' for a
    photo the article already uses (its archive original in the repo, never a web variant).
    'web:Images/web/...' is a site copy whose original could not be found: the stage may SHOW it
    (display=True, the /img route) so a thumbnail opens as it is, but nothing is ever cut from it."""
    if p.startswith("web:"):
        src = (SITE / p[4:]).resolve()
        if not display or (SITE / "Images" / "web").resolve() not in src.parents:
            abort(404)
        return src
    if p.startswith("site:"):
        src = (SITE / p[5:]).resolve()
        images = (SITE / "Images").resolve()
        if images not in src.parents or (images / "web") in src.parents or (images / "web") == src:
            abort(404)
        return src
    src = (BACKUP / p).resolve()
    if BACKUP.resolve() not in src.parents:
        abort(404)
    return src


def measure(f):
    """Dimensions only: opening an image reads the header, not the pixels."""
    try:
        with Image.open(f) as im:
            w, h = im.size
            o = im.getexif().get(274, 1)
        if o in (5, 6, 7, 8):               # EXIF rotation swaps the axes
            w, h = h, w
    except Exception:
        return None
    # the hero crops 16:9 out of the frame, so the width is what has to carry
    tier = "good" if w >= HERO_MIN else "fair" if w >= HERO_FAIR else "low"
    return {"path": f.relative_to(BACKUP).as_posix(), "name": f.name,
            "w": w, "h": h, "heroW": w, "tier": tier, "sharp": w >= HERO_MIN}


@app.get("/photos")
def photos():
    folder = (BACKUP / request.args["album"]).resolve()
    # B10: /img asserts containment, /photos did not - ?album=.. walked out
    if BACKUP.resolve() not in folder.parents or not folder.is_dir():
        abort(404)
    stamp = folder.stat().st_mtime_ns
    hit = _PHOTO_CACHE.get(folder.name)
    if hit and hit[0] == stamp:
        warm(folder.name, hit[1])
        return jsonify(hit[1])
    # the scan survives a restart: 400 headers off a synced folder is 15 seconds
    idx = CACHE / (hashlib.md5(folder.name.encode()).hexdigest() + ".index.json")
    if idx.exists():
        saved = json.loads(idx.read_text(encoding="utf-8"))
        if saved.get("stamp") == stamp:
            _PHOTO_CACHE[folder.name] = (stamp, saved["rows"])
            warm(folder.name, saved["rows"])
            return jsonify(saved["rows"])
    rows = scan_album(folder, stamp, idx)
    warm(folder.name, rows)
    return jsonify(rows)


def scan_album(folder, stamp, idx, mapper=None):
    """The album's photos with their sizes. Incremental (2026-09-28): a folder that is still
    syncing changes its timestamp with every new file, and a full re-measure of 1,100 headers off
    a synced folder took 16 s each time; only files not measured before are read now."""
    known = {}
    if idx.exists():
        try:
            known = {r["path"]: r for r in json.loads(idx.read_text(encoding="utf-8")).get("rows", [])}
        except (OSError, ValueError):
            known = {}
    files = [f for f in sorted(folder.rglob("*"))
             if f.suffix.lower() in EXT and f.is_file()
             and not is_stray_thumb(f.name)]
    fresh = [f for f in files if f.relative_to(BACKUP).as_posix() not in known]
    new = {r["path"]: r for r in (mapper or SCAN_POOL.map)(measure, fresh) if r}
    rows = [known.get(p) or new.get(p) for p in (f.relative_to(BACKUP).as_posix() for f in files)]
    rows = sorted((r for r in rows if r), key=lambda r: r["name"])
    _PHOTO_CACHE[folder.name] = (stamp, rows)
    try:
        idx.write_text(json.dumps({"stamp": stamp, "rows": rows}), encoding="utf-8")
    except OSError:
        pass
    return rows


def prescan_all():
    """At startup, in the background: every album's photo list ready before it is asked for."""
    for d in sorted(p for p in BACKUP.iterdir() if p.is_dir() and not p.name.startswith("_")):
        try:
            if d.name in _PHOTO_CACHE:
                continue
            # one file at a time: this is background work and must not slow what you are doing
            scan_album(d, d.stat().st_mtime_ns, CACHE / (hashlib.md5(d.name.encode()).hexdigest() + ".index.json"), mapper=map)
        except Exception:
            pass


@app.get("/article_photos")
def article_photos():
    """The photos an article already shows, as archive originals, in the order they appear.
    Kevin (2026-09-29) picked the Orgov thumbnail from the article's own edited photo
    (IMG_0438-2), which lives in Images/, not in the backup albums the strip browses."""
    rel = request.args.get("rel", "")
    page = (SITE / rel).resolve()
    if SITE.resolve() not in page.parents or page.suffix != ".html" or not page.is_file():
        abort(404)
    return jsonify(_article_rows(page))


def _article_rows(page):
    import urllib.parse as up
    html = page.read_text(encoding="utf-8", errors="replace")
    rows, seen = [], set()
    for m in re.finditer(r'(?:src|srcset)="([^"]*Images/web/[^"]+)"', html):
        for part in m.group(1).split(","):
            u = up.unquote(part.strip().split(" ")[0])
            if "Images/web/" not in u or "/flags/" in u or "/city-maps/" in u or "/og/" in u:
                continue
            sub = u.split("Images/web/", 1)[1]
            stem = re.sub(r"-(?:mob-)?(?:[123]x)$|-mob$", "", sub.rsplit(".", 1)[0])
            if stem in seen:
                continue
            seen.add(stem)
            base = SITE / "Images" / stem
            orig = next((c for c in base.parent.glob(base.name + ".*") if c.suffix.lower() in EXT), None) if base.parent.is_dir() else None
            if not orig:
                continue
            try:
                with Image.open(orig) as im:
                    w, h = im.size
                    if im.getexif().get(274, 1) in (5, 6, 7, 8):
                        w, h = h, w
            except Exception:
                continue
            tier = "good" if w >= HERO_MIN else "fair" if w >= HERO_FAIR else "low"
            rows.append({"path": "site:" + orig.relative_to(SITE).as_posix(), "name": orig.name,
                         "w": w, "h": h, "heroW": w, "tier": tier, "sharp": w >= HERO_MIN})
    return rows


@app.get("/quality")
def quality():
    """Focus scores for whatever the pool has finished, plus the album median so
    the page can say which frames are soft relative to their neighbours."""
    folder = request.args["album"]
    with _QLOCK:
        scores = dict(_QUALITY.get(folder, {}))
    vals = [v for v in scores.values() if v is not None]
    return jsonify({"scores": scores, "done": len(scores),
                    "median": round(statistics.median(vals), 2) if vals else None})


@app.get("/img")
def img():
    src = resolve_src(request.args["p"], display=True)
    # 4000 covers a full-bleed hero on a 2x monitor (the site itself never
    # serves more); the strip and the plain stage ask for far less
    w = min(int(request.args.get("w", 480)), 4000)
    crop = request.args.get("crop") == "1"
    ratio = shape_ratio(request.args.get("shape", "home"),
                        request.args.get("vw", DEFAULT_VW))
    try:
        angle = max(-10.0, min(10.0, float(request.args.get("a", 0) or 0)))
    except ValueError:
        angle = 0.0
    r = send_file(build(src, w, crop, ratio, angle), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "public, max-age=604800"
    return r


@app.post("/save")
def save():
    picks = read_picks()
    body = dict(request.json)
    body["path"] = _posix(body.get("path", ""))
    # one pick per ARTICLE when one is linked; explore mode (no article) keeps the country slot
    picks[body.get("article") or body["country"]] = body
    PICKS.write_text(json.dumps(picks, indent=1, ensure_ascii=False), encoding="utf-8")
    out = {"ok": True, "saved": str(PICKS)}
    # With an article open in the editor, Save also BUILDS the hero: the eight files under
    # Images/web/<Country>/ cut from the archive original at the chosen angle. The editor
    # then rewrites the page's hero block from the result; nothing here touches an article.
    article, slug = body.get("article") or "", body.get("slug") or ""
    if article and slug:
        import re as _re, subprocess, sys as _sys
        slug = _re.sub(r"[^a-z0-9-]", "", slug.lower())
        web = SITE / "Images" / "web" / _web_country(body["country"])
        src = resolve_src(body["path"])
        if src.is_file() and slug:
            args = [_sys.executable, str(SITE / "tools" / "gen_hero_variants.py"), str(src),
                    "--out", str(web), "--slug", slug, "--force"]
            if abs(float(body.get("angle") or 0)) > 1e-3:
                args += ["--angle", str(float(body["angle"]))]
            r = subprocess.run(args, capture_output=True, text=True, cwd=str(SITE), timeout=600)
            if r.returncode == 0:
                _refit(["Images/web/%s/hero-%s.webp" % (body["country"], slug)], [])
            out.update({"built": r.returncode == 0, "slug": slug,
                        "web": "Images/web/" + body["country"],
                        "log": "\n".join((r.stdout or r.stderr or "").strip().splitlines()[-4:])})
    return jsonify(out)


THUMB_PICKS = SITE / ".tmp" / "thumb_picks.json"


def _web_country(name):
    """the Images/web/<Country> folder this country already has, accents and case aside (Türkiye's
    album is "Türkiye", its folder "Turkiye"; QA 2026-10-02)"""
    import unicodedata
    fold = lambda s: unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    web = SITE / "Images" / "web"
    for d in (web.iterdir() if web.is_dir() else []):
        if d.is_dir() and fold(d.name) == fold(name):
            return d.name
    return name


def _refit(web_files, pages):
    """tools/fit_backgrounds.py after a save (Kevin, 2026-10-01: heroes and cards sized to every window
    width). The photo's size files are cut again at once, since a new photo keeps its name; the pages
    written are fitted in the background, since that loads each one at fourteen window widths."""
    import subprocess, sys as _sys
    tool = str(SITE / "tools" / "fit_backgrounds.py")
    try:
        if web_files:
            subprocess.run([_sys.executable, tool, "--refresh"] + web_files, cwd=str(SITE), capture_output=True, timeout=600)
        if pages:
            subprocess.Popen([_sys.executable, tool] + pages, cwd=str(SITE), stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        print("refit after save failed:", e)

def find_card(h, name):
    """(start, end, tag) of the country-article-card element linking to `name`, any attribute order"""
    for m in re.finditer(r'<(?:div|a)\b[^>]*\bclass="country-article-card(?:\s[^"]*)?"[^>]*>', h):
        t = m.group(0)
        if ("location.href='%s'" % name) in t or ('href="%s"' % name) in t:
            return m.start(), m.end(), t
    return None



def _country_card(index_path, file_name, up, slug, country, pos):
    """Point the country page's card for `file_name` at card-<slug> with a position per slot.
    The inline background carries the 2:3 position (every width over 768); --pm carries the phone
    16:9 one, which artifact.css applies at 768 and below."""
    if not index_path.is_file():
        return False
    h = index_path.read_text(encoding="utf-8")
    found = find_card(h, file_name)
    if not found:
        return False
    s0, e0, tag = found
    web = "%sImages/web/%s/card-%s" % (up, country, slug)
    style = ("background:url('{w}.jpg') {d}/cover no-repeat;background-image:image-set(url('{w}.webp') type('image/webp'), "
             "url('{w}.jpg') type('image/jpeg'));--pm:{p};cursor:pointer").format(w=web, d=pos.get("card", "50% 50%"), p=pos.get("phone", "50% 50%"))
    if tag.startswith("<a"):                     # a link card keeps its own display rules
        style = "display:block;text-decoration:none;" + style
    new = re.sub(r'\sstyle="[^"]*"', "", tag)
    new = new[:-1].rstrip("/").rstrip() + ' style="%s">' % style
    h = h[:s0] + new + h[e0:]
    index_path.write_text(h, encoding="utf-8")
    return True


def _listing_cards(rel, slug, country, pos):
    """The home page's Recent Dispatches and posts.html's Guides link to PUBLISHED articles through a
    `.card-img` block (4:3 on a monitor, 16:9 on a phone). When one points at this article, it gets
    the card photo inline with both crops; a draft has no such card yet, so nothing is touched."""
    live = rel.replace("Drafts/.Full Articles/", "")
    wrote = []
    for name in ("index.html", "posts.html"):
        f = SITE / name
        if not f.is_file():
            continue
        h = f.read_text(encoding="utf-8")
        pat = re.compile(r'(<a href="%s"[^>]*>\s*<div class="card-img bg[^"]*")(?: style="[^"]*")?(>)' % re.escape(live))
        if not pat.search(h):
            continue
        web = "Images/web/%s/card-%s" % (country, slug)
        style = (' style="background-image:image-set(url(\'{w}.webp\') type(\'image/webp\'), url(\'{w}.jpg\') type(\'image/jpeg\'));'
                 'background-size:cover;background-position:{d};--pm:{p}"').format(w=web, d=pos.get("list", "50% 50%"), p=pos.get("phone", "50% 50%"))
        h = pat.sub(lambda m: m.group(1) + style + m.group(2), h)
        f.write_text(h, encoding="utf-8")
        wrote.append(name)
    return wrote


def _share_image(src, dst, pos, angle=0.0):
    """The 1200x630 share image, cut from the same original at the 'og' crop (x%, y%)."""
    im0 = ImageOps.exif_transpose(Image.open(src)); icc = im0.info.get("icc_profile")
    im = straighten(im0.convert("RGB"), angle) if abs(angle) > 1e-3 else im0.convert("RGB")
    w, h = im.size; ar = 1200 / 630
    try:
        px, py = [float(v.strip().rstrip("%")) / 100 for v in pos.split()]
    except Exception:
        px, py = 0.5, 0.5
    if w / h > ar:
        nw = round(h * ar); x = round((w - nw) * px); im = im.crop((x, 0, x + nw, h))
    else:
        nh = round(w / ar); y = round((h - nh) * py); im = im.crop((0, y, w, y + nh))
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.resize((1200, 630), Image.LANCZOS).save(dst, "JPEG", quality=82, optimize=True, progressive=True, icc_profile=icc)


@app.post("/save_thumb")
def save_thumb():
    """Thumbnail mode's Save (Kevin, 2026-09-29: "make thumbnails ... exactly like how this works
    for the heroes"). Builds card-<slug>.jpg/.webp from the archive original, points every card
    for the article at it with that slot's crop, and recuts the share image."""
    import subprocess, sys as _sys
    body = dict(request.json or {})
    rel, country = body.get("article") or "", body.get("country") or ""
    slug = re.sub(r"[^a-z0-9-]", "", (body.get("slug") or "").lower())
    if not (rel and country and slug and body.get("path")):
        return jsonify({"ok": False, "error": "open the article in the editor first"}), 400
    if body["path"].startswith("web:"):
        return jsonify({"ok": False, "error": "the cards' photo is a web copy; pick its original from the strip first"}), 400
    src = resolve_src(body["path"])
    angle = float(body.get("angle") or 0)
    pos = body.get("positions") or {}
    og_path = body.get("og_path") or body["path"]
    og_angle = float(body.get("og_angle") if body.get("og_path") else angle)
    picks = json.loads(THUMB_PICKS.read_text(encoding="utf-8")) if THUMB_PICKS.exists() else {}
    picks[rel] = body
    THUMB_PICKS.write_text(json.dumps(picks, indent=1, ensure_ascii=False), encoding="utf-8")
    web = SITE / "Images" / "web" / country
    args = [_sys.executable, str(SITE / "tools" / "gen_card_variants.py"), str(src), "--out", str(web), "--slug", slug]
    if abs(angle) > 1e-3:
        args += ["--angle", str(angle)]
    r = subprocess.run(args, capture_output=True, text=True, cwd=str(SITE), timeout=300)
    if r.returncode != 0:
        return jsonify({"ok": False, "error": (r.stderr or r.stdout)[-300:]}), 500
    page = (SITE / rel)
    up = "../" * (len(Path(rel).parts) - 1)
    wrote = []
    if _country_card(page.parent / "index.html", page.name, up, slug, country, pos):
        wrote.append((page.parent / "index.html").relative_to(SITE).as_posix())
    wrote += _listing_cards(rel, slug, country, pos)
    og = None
    if pos.get("og") and not og_path.startswith("web:"):     # a web-copy share image stays as it is
        stem = Path(rel).stem
        dst = SITE / "Images" / "web" / "og" / ("%s-%s.jpg" % (page.parent.name, stem))
        _share_image(resolve_src(og_path), dst, pos["og"], og_angle)
        og = dst.relative_to(SITE).as_posix()
        _point_og(page, og)
    _refit(["Images/web/%s/card-%s.webp" % (country, slug)], wrote)
    return jsonify({"ok": True, "card": "Images/web/%s/card-%s.jpg" % (country, slug), "pages": wrote, "og": og,
                    "log": r.stdout.strip().splitlines()[-2:]})


def _thumb_picks():
    try:
        return json.loads(THUMB_PICKS.read_text(encoding="utf-8")) if THUMB_PICKS.exists() else {}
    except Exception:
        return {}


def _web_original(img, rel):
    """(path, angle, how) of the archive photo a site image was cut from, or None"""
    import glob as _glob
    name = Path(img).name
    tp = _thumb_picks().get(rel) or {}
    if re.match(r"card-.+\.(?:jpg|webp)$", name) and tp.get("path"):
        return tp["path"], float(tp.get("angle") or 0), "thumbnail"
    m = re.match(r"hero-(.+?)(?:-2x|-mob-1x|-mob)?\.(?:jpg|webp)$", name)
    if m:
        picks = read_picks()
        hp = picks.get(rel)
        if not (isinstance(hp, dict) and hp.get("slug") == m.group(1) and hp.get("path")):
            hp = next((v for v in picks.values() if isinstance(v, dict) and v.get("slug") == m.group(1) and v.get("path")), None)
        if hp:
            return hp["path"], float(hp.get("angle") or 0), "hero"
    if img.startswith("Images/web/"):
        stem = re.sub(r"-(?:mob-)?[123]x$|-mob$|-card(?:-[123]x)?$", "", img.split("Images/web/", 1)[1].rsplit(".", 1)[0])
        base = SITE / "Images" / stem
        found = [c for c in base.parent.glob(_glob.escape(base.name) + ".*") if c.suffix.lower() in EXT] if base.parent.is_dir() else []
        root = SITE / "Images" / stem.split("/")[0]
        if not found and root.is_dir():          # El Salvador's web copies sit one folder above their originals
            found = sorted(c for c in root.rglob(_glob.escape(Path(stem).name) + ".*") if c.suffix.lower() in EXT)
        if found:
            return "site:" + found[0].relative_to(SITE).as_posix(), 0.0, "original"
        hit = _match_original(img, rel)
        if hit:
            return hit, 0.0, "original"
    return None


def _match_original(img, rel):
    """The photo a web copy was downscaled from, found by its pixels (whole photo, same shape)"""
    from PIL import ImageChops
    try:
        with Image.open(SITE / img) as w0:
            web = ImageOps.exif_transpose(w0).convert("L")
    except Exception:
        return None
    ar = web.width / web.height
    ref = web.resize((64, max(1, round(64 / ar))), Image.BILINEAR)
    cands = []
    m = re.match(r"Images/web/dest-cards/([a-z0-9-]+)\.", img)
    if m:                                        # the country placeholder: its dest-card originals
        keys = {m.group(1), m.group(1).replace("-", ""), m.group(1).split("-")[-1]}
        for c in sorted((SITE / "Images" / "dest-cards").glob("*")):
            if c.suffix.lower() in EXT and any(c.name.lower().startswith(k + "_") or c.name.lower().startswith(k + ".") for k in keys):
                try:
                    with Image.open(c) as im:
                        w, h = im.size
                        if im.getexif().get(274, 1) in (5, 6, 7, 8):
                            w, h = h, w
                except Exception:
                    continue
                cands.append(("site:" + c.relative_to(SITE).as_posix(), w, h))
    else:
        page = SITE / rel
        if page.is_file():
            cands = [(r["path"], r["w"], r["h"]) for r in _article_rows(page)]
    best = None
    for path, w, h in cands:
        if abs((w / h) / ar - 1) > 0.02:
            continue
        try:
            im = Image.open(build(resolve_src(path), 480, False, None, 0.0)).convert("L").resize(ref.size, Image.BILINEAR)
        except Exception:
            continue
        d = ImageStat.Stat(ImageChops.difference(im, ref)).mean[0]
        if best is None or d < best[0]:
            best = (d, path)
    return best[1] if best and best[0] <= 12 else None


def _pos_xy(pos):
    """A CSS background-position ("40.5% 50%", "center 70%", "left top") as [x, y] percentages"""
    kw = {"left": 0.0, "top": 0.0, "center": 50.0, "right": 100.0, "bottom": 100.0}
    vals = []
    for p in (pos or "").split()[:2]:
        if p in kw:
            vals.append((p, kw[p]))
        else:
            try:
                vals.append((None, float(p.rstrip("%"))))
            except ValueError:
                vals.append((None, 50.0))
    if not vals:
        return [50.0, 50.0]
    if len(vals) == 1:
        k, v = vals[0]
        return [50.0, v] if k in ("top", "bottom") else [v, 50.0]
    (k1, a), (k2, b) = vals
    if k1 in ("top", "bottom") or k2 in ("left", "right"):
        a, b = b, a
    return [a, b]


def _photo_row(path, angle):
    """What the stage needs to show a photo: name, size (after rotation and straightening) and tier"""
    src = resolve_src(path, display=True)
    try:
        with Image.open(src) as im:
            w, h = im.size
            if im.getexif().get(274, 1) in (5, 6, 7, 8):
                w, h = h, w
    except Exception:
        return None
    tier = "good" if w >= HERO_MIN else "fair" if w >= HERO_FAIR else "low"
    return {"path": path, "name": src.name, "w": w, "h": h, "heroW": w, "tier": tier, "angle": angle}


def _slide(small, ref0):
    """(difference, pct, axis) of the best full-height or full-width placement of ref0 in small.
    Coarse at 64 px, then the three best local minima re-measured at up to 512 px: a symmetric
    subject has twin minima a coarse pass cannot tell apart (Yerevan's card read 40%; it is 50%).
    The difference is the coarse one, which the match thresholds were tuned on."""
    from PIL import ImageChops
    ar = ref0.width / ref0.height
    w, h = small.size
    axis = "x" if w / h > ar else "y"

    def scan(n, offs=None):
        if axis == "x":
            im = small.resize((max(1, round(w * n / h)), n), Image.BILINEAR)
            ref = ref0.resize((max(1, round(n * ar)), n), Image.BILINEAR); span = im.width - ref.width
        else:
            im = small.resize((n, max(1, round(h * n / w))), Image.BILINEAR)
            ref = ref0.resize((n, max(1, round(n / ar))), Image.BILINEAR); span = im.height - ref.height
        if span < 0:
            return None, []
        out = []
        for off in (range(span + 1) if offs is None else sorted({min(span, max(0, o)) for o in offs})):
            box = (off, 0, off + ref.width, ref.height) if axis == "x" else (0, off, ref.width, off + ref.height)
            out.append((ImageStat.Stat(ImageChops.difference(im.crop(box), ref)).mean[0], off))
        return span, out

    span, coarse = scan(64)
    if span is None:
        return None
    best = min(coarse)
    if span == 0:
        return best[0], 50.0, axis
    d = {o: v for v, o in coarse}
    minima = sorted((v, o) for o, v in d.items() if v <= d.get(o - 1, 1e9) and v <= d.get(o + 1, 1e9))[:3]
    n2 = min(512, h if axis == "x" else w)
    span2, _ = scan(n2, [0])
    if not span2 or span2 <= span:
        return best[0], round(best[1] / span * 100, 1), axis
    k = span2 / span
    offs = []
    for v, o in minima:
        c = round(o * k)
        offs += list(range(c - int(k) - 2, c + int(k) + 3))
    _, fine = scan(n2, offs)
    f = min(fine)
    return best[0], round(f[1] / span2 * 100, 1), axis


def _album_photos(rel):
    """the backup album photos of an article's country (Drafts/.Full Articles/armenia/x -> Armenia (2026))"""
    folder = Path(rel).parent.name.lower()
    out = []
    for a in albums():
        if a["country"].lower().replace(" ", "-") == folder:
            out += [a["folder"] + "/" + f.name for f in sorted((BACKUP / a["folder"]).iterdir()) if f.suffix.lower() in EXT]
    return out


MATCH_CACHE = SITE / ".tmp" / "thumb_match_cache.json"


def _crop_match(img, rel):
    """The photo a CROPPED web copy was cut from, and where: (path, pct, axis) or None. Kevin's
    Yerevan and day-trip cards were cut at an edited crop, so no whole-photo match can see them.
    The article's photos first, then the country's album as the strip has already rendered it
    (an unrendered photo is skipped rather than decoded: a HEIC takes seconds)."""
    f = SITE / img
    try:
        key = "%s|%d" % (img, f.stat().st_mtime_ns)
        cache = json.loads(MATCH_CACHE.read_text(encoding="utf-8")) if MATCH_CACHE.is_file() else {}
    except Exception:
        return None
    if key in cache:
        return tuple(cache[key]) if cache[key] else None
    try:
        with Image.open(f) as i0:
            ref0 = ImageOps.exif_transpose(i0).convert("L")
    except Exception:
        return None
    best = None
    page = SITE / rel
    for r in (_article_rows(page) if page.is_file() else []):
        try:
            m = _slide(Image.open(build(resolve_src(r["path"]), 480, False, None, 0.0)).convert("L"), ref0)
        except Exception:
            continue
        if m and (best is None or m[0] < best[0]):
            best = (m[0], r["path"], m[1], m[2])
    if not best or best[0] > 12:
        for path in _album_photos(rel):
            try:
                cp = cache_path(resolve_src(path), 340, False, HERO_RATIO, 0.0)
                if not cp.exists():
                    continue
                m = _slide(Image.open(cp).convert("L"), ref0)
            except Exception:
                continue
            if m and (best is None or m[0] < best[0]):
                best = (m[0], path, m[1], m[2])
    hit = [best[1], best[2], best[3]] if best and best[0] <= 12 else None
    cache[key] = hit
    try:
        MATCH_CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return tuple(hit) if hit else None


def _view_center(W, H, card_ar, crop, slot_ar, pos):
    """the centre, in the original's pixels, of what the site shows in a slot: the card image (a
    crop of the original at `crop`, or all of it) cover-cut to slot_ar at pos"""
    pct, axis = crop
    if axis == "x":
        cw, ch = H * card_ar, H; x0, y0 = (W - cw) * pct / 100, 0.0
    else:
        cw, ch = W, W / card_ar; x0, y0 = 0.0, (H - ch) * pct / 100
    if cw / ch > slot_ar:
        vw, vh = ch * slot_ar, ch; vx, vy = (cw - vw) * pos[0] / 100, 0.0
    else:
        vw, vh = cw, cw / slot_ar; vx, vy = 0.0, (ch - vh) * pos[1] / 100
    return x0 + vx + vw / 2, y0 + vy + vh / 2


def _frame_xy(W, H, slot_ar, cx, cy):
    """the [x, y] that puts a slot_ar frame cut from the whole original on that centre"""
    clamp = lambda v: round(max(0.0, min(100.0, v)), 1)
    if W / H > slot_ar:
        fw = H * slot_ar
        return [clamp((cx - fw / 2) / (W - fw) * 100) if W > fw else 50.0, 50.0]
    fh = W / slot_ar
    return [50.0, clamp((cy - fh / 2) / (H - fh) * 100) if H > fh else 50.0]


def _site_to_orig(W, H, card_ar, crop, slot_ar, pos):
    """A slot's crop on the site, where the card image is itself a crop of the original, as the
    [x, y] Covers uses for that slot cut from the whole original: the same centre."""
    return _frame_xy(W, H, slot_ar, *_view_center(W, H, card_ar, crop, slot_ar, pos))


SLOT_AR = {"card": 294 / 441, "list": 395 / 296, "phone": 353 / 199, "og": 1200 / 630}


def _og_offset(og_file, path, angle):
    """Where the 1200x630 share image sits in a photo, as [x, y] percentages, or None when it was
    not cut from this photo. Compared small and in grey: the cut is the same pixels at another scale."""
    try:
        small = Image.open(build(resolve_src(path, display=True), 480, False, None, angle)).convert("L")
        og = Image.open(og_file).convert("L")
    except Exception:
        return None
    m = _slide(small, og)
    if not m or m[0] > 16:                          # not this photo
        return None
    return [m[1], 50.0] if m[2] == "x" else [50.0, m[1]]


@app.post("/thumb_current")
def thumb_current():
    """The article's thumbnails as the site shows them now: the cards' photo, as an archive original
    where it can be traced, with each slot's crop; the share image is always the cards' photo."""
    body = request.json or {}
    return jsonify(_current(body.get("rel") or "", body.get("slots") or []))


def _current(rel, slot_list):
    slots = {x["key"]: x for x in slot_list if isinstance(x, dict) and x.get("key")}
    tp = _thumb_picks().get(rel) or {}
    card = slots.get("card") or {}
    img = card.get("img") or (slots.get("list") or {}).get("img")
    out = {"ok": True, "photo": None, "xy": {}, "og": None, "notes": []}
    crop = None
    if img:
        found = _web_original(img, rel)
        if not found and img.startswith("Images/web/"):
            cm = _crop_match(img, rel)                # a card cut at a crop of its original
            if cm:
                found, crop = (cm[0], 0.0, "crop"), (cm[1], cm[2])
        path, angle, how = found if found else ("web:" + img, 0.0, "web")
    elif tp.get("path"):
        path, angle, how = tp["path"], float(tp.get("angle") or 0), "thumbnail"
    else:
        return out                               # nothing set yet: the stage stays on whatever you pick
    photo = _photo_row(path, angle)
    if not photo:
        return out
    photo["how"] = how
    out["photo"] = photo
    if how == "web":
        out["notes"].append("The cards' photo is a web copy whose original was not found, so pick it again from the strip before saving.")
    tpos = (tp.get("positions") or {}) if tp.get("path") == path else {}
    for k in ("card", "list", "phone"):
        sl = slots.get(k) or {}
        if k == "list" and sl.get("img") and img and sl["img"] != img:
            out["notes"].append("The home and posts card uses another photo today; saving gives it this one.")
            out["xy"][k] = [50.0, 50.0]
        elif k == "list" and "not on the home page" in (sl.get("note") or ""):
            out["xy"][k] = _pos_xy(tpos.get("list", "50% 50%"))
        else:
            out["xy"][k] = _pos_xy((tpos.get(k) if not sl.get("img") else None) or sl.get("pos") or tpos.get(k) or "50% 50%")
    card_ar = None
    if crop:                                     # the site's crops are on the card image, not the photo
        try:
            with Image.open(SITE / img) as ci:
                card_ar = ci.width / ci.height
        except Exception:
            card_ar = SLOT_AR["card"]
        for k in ("card", "list", "phone"):
            out["xy"][k] = _site_to_orig(photo["w"], photo["h"], card_ar, crop, SLOT_AR[k], out["xy"][k])
    # the share image: find which photo it was cut from, and where
    og_img = (slots.get("og") or {}).get("img")
    if og_img and (SITE / og_img).is_file():
        cands = [(path, angle)]
        hp = read_picks().get(rel)
        for c in ([(tp["path"], float(tp.get("angle") or 0))] if tp.get("path") else []) + \
                 ([(hp["path"], float(hp.get("angle") or 0))] if isinstance(hp, dict) and hp.get("path") else []):
            if c[0] not in [x[0] for x in cands]:
                cands.append(c)
        for cp, ca in cands:
            if cp.startswith("web:"):
                continue
            xy = _og_offset(SITE / og_img, cp, ca)
            if xy:
                row = photo if cp == path else _photo_row(cp, ca)
                out["og"] = dict(row or {}, xy=xy, how="same" if cp == path else "other")
                break
    # Kevin (2026-09-30): the share image is the cards' photo. Cut from anything else, it is shown
    # on the cards' photo centred where the card is, and Save cuts it from there.
    if not out["og"] or out["og"].get("how") != "same":
        if crop:                                 # centred where the country card is
            cx, cy = _view_center(photo["w"], photo["h"], card_ar, crop, SLOT_AR["card"], [50.0, 50.0])
        else:
            cx, cy = _view_center(photo["w"], photo["h"], photo["w"] / photo["h"], (50.0, "x"), SLOT_AR["card"],
                                  out["xy"].get("card") or [50.0, 50.0])
        xy = _frame_xy(photo["w"], photo["h"], SLOT_AR["og"], cx, cy)
        had = out["og"]
        out["og"] = dict(photo, xy=xy, how="recut")
        if og_img:
            out["notes"].append("The share image was cut from %s; saving cuts it from the cards' photo." %
                                ("another photo" if had and had.get("how") == "other" else "a photo other than the card's"))
    return out


def _point_og(page, dst):
    """og:image and twitter:image name the share image Save wrote"""
    url = "https://getawayguide.io/" + dst
    h = page.read_text(encoding="utf-8")
    h2 = re.sub(r'(<meta\s+(?:property|name)="(?:og:image|twitter:image)"\s+content=")[^"]*(")', lambda m: m.group(1) + url + m.group(2), h)
    if h2 != h:
        page.write_text(h2, encoding="utf-8")
    return h2 != h


@app.post("/recut_share")
def recut_share():
    """The Launch tab's fix: cut the share image from the cards' photo, centred on the card."""
    body = request.json or {}
    rel = body.get("rel") or ""
    page = SITE / rel
    if not page.is_file():
        return jsonify({"ok": False, "error": "no such article"}), 404
    cur = _current(rel, body.get("slots") or [])
    ph, og = cur.get("photo"), cur.get("og")
    if not ph or not og:
        return jsonify({"ok": False, "error": "this article has no card photo to cut from"}), 400
    if og.get("how") == "same":
        return jsonify({"ok": True, "log": "%s: the share image is already the card's photo." % page.name})
    if ph["path"].startswith("web:"):
        return jsonify({"ok": False, "error": "the card's original was not found; pick it in Covers"}), 400
    dst = "Images/web/og/%s-%s.jpg" % (page.parent.name, page.stem)
    _share_image(resolve_src(ph["path"]), SITE / dst, "%s%% %s%%" % tuple(og["xy"]), float(ph.get("angle") or 0))
    _point_og(page, dst)
    return jsonify({"ok": True, "log": "%s: share image cut from %s." % (page.name, ph["name"]), "og": dst})


@app.get("/thumb_picks")
def get_thumb_picks():
    return jsonify(json.loads(THUMB_PICKS.read_text(encoding="utf-8")) if THUMB_PICKS.exists() else {})


@app.get("/picks")
def get_picks():
    return jsonify(read_picks())


@app.post("/star")
def star():
    """Toggle one photo on a country's shortlist."""
    body = request.json
    stars = read_stars()
    lst = stars.setdefault(body["country"], [])
    body["path"] = _posix(body["path"])
    if body["path"] in lst:
        lst.remove(body["path"])
        on = False
    else:
        lst.append(body["path"])
        on = True
    stars = {k: v for k, v in stars.items() if v}
    STARS.write_text(json.dumps(stars, indent=1, ensure_ascii=False), encoding="utf-8")
    return jsonify(ok=True, starred=on, count=len(stars.get(body["country"], [])))


@app.get("/stars")
def get_stars():
    return jsonify(read_stars())


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Covers</title>
<link rel="stylesheet" href="/fonts.css">
<script>
  // ?embed=1: running as the Hero Picker view inside editor.html, which draws
  // the dark suite bar and the app switcher itself
  if (new URLSearchParams(location.search).has('embed'))
    document.documentElement.classList.add('embed');
</script>
<style>
  :root { --ink:#1C2821; --terra:#2D6B50; --line:rgba(28,40,33,.14); --warn:#B4553C;
          --amber:#9A7B2E; }
  * { box-sizing:border-box; }
  body { margin:0; background:#fff; color:rgba(28,40,33,.86);
    font:400 14px/1.6 'Hanken Grotesk',-apple-system,'Segoe UI',sans-serif; }
  header { display:flex; gap:10px 12px; align-items:center; padding:12px 18px;
    border-bottom:1px solid var(--line); flex-wrap:wrap; }
  header .tally { white-space:nowrap; }
  header .logo { font-family:Newsreader,Georgia,serif; font-style:italic;
    color:var(--terra); font-size:18px; margin-right:8px; }
  select, input[type=text] { font:inherit; padding:7px 10px; border:1px solid var(--line);
    background:#fff; }
  input[type=text] { flex:1 1 220px; min-width:180px; }
  button { font:inherit; padding:8px 16px; border:1px solid var(--ink); background:var(--ink);
    color:#fff; cursor:pointer; letter-spacing:.08em; text-transform:uppercase;
    font-size:11px; }
  button.q { background:#fff; color:var(--ink); }
  .tally { font-size:11.5px; letter-spacing:.06em; text-transform:uppercase;
    color:rgba(28,40,33,.55); }
  .tally b { color:var(--terra); }
  .main { display:grid; grid-template-columns:minmax(0,1fr) 400px; height:calc(100vh - 59px); }
  .stage { padding:22px 26px; overflow:auto; }
  .hero { position:relative; aspect-ratio:var(--shape,2.118);
    overflow:hidden; background:#eee; }
  .hero img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover;
    object-position:var(--ox,50%) var(--oy,50%); user-select:none;
    -webkit-user-drag:none; cursor:grab; }
  .hero[data-axis="x"] img { cursor:ew-resize; }
  .hero[data-axis="y"] img { cursor:ns-resize; }
  .hero img.dragging { cursor:grabbing; }
  /* ---- the site's nav is position:fixed over a hero that starts at top:0, so
     it COVERS the top 60px of every hero (63 on a phone) behind 96% white. The
     picker showed the whole frame uncovered, which is why a photo looked bigger
     here than on the page and why a subject near the top edge could be judged
     on pixels no visitor ever sees. ---- */
  /* an explicit display: on a class beats the UA's [hidden] rule, so each
     one needs saying: the desktop band was drawing a hamburger too, and the
     phone band the whole menu, overflowing a 402px frame */
  .navband[hidden], .navband .nb-links[hidden], .navband .nb-burger[hidden] { display:none; }
  .navband { position:absolute; left:0; right:0; top:0; height:var(--nav,0px);
    background:rgba(255,255,255,.96); border-bottom:1px solid rgba(28,40,33,.1);
    z-index:6; pointer-events:none; display:flex; align-items:center;
    padding:0 calc(38px * var(--navk,1)); gap:calc(30px * var(--navk,1));
    overflow:hidden; }
  .navband .nb-logo { font-family:Newsreader,Georgia,serif; font-style:italic;
    color:var(--terra); font-size:calc(19px * var(--navk,1)); }
  .navband .nb-links { margin-left:auto; display:flex; gap:calc(34px * var(--navk,1));
    font-size:calc(11px * var(--navk,1)); letter-spacing:.14em; text-transform:uppercase;
    color:rgba(28,40,33,.8); white-space:nowrap; }
  .navband .nb-burger { margin-left:auto; display:grid; gap:calc(4px * var(--navk,1)); }
  .navband .nb-burger i { display:block; width:calc(20px * var(--navk,1));
    height:calc(1.5px * var(--navk,1)); background:rgba(28,40,33,.8); }
  /* the scrim strength is live: not every photo needs the same weight */
  .thumb-mode .cpy, .thumb-mode .navband, .thumb-mode #scrim-ctl { display:none !important; }
  /* a portrait card at the stage's full width ran far below the window: fit the frame to the height */
  .thumb-mode .hero { width:min(100%, calc((100vh - 230px) * var(--shape, 1))); margin:0 auto; }
  .thumb-mode .veil { opacity:1 !important; background:linear-gradient(transparent 35%, rgba(6,10,8,.88)) !important; }
  .thumb-mode .hero[data-slot="og"] .veil { display:none; }
  .thumb-mode .hero::after { content:attr(data-card-title); position:absolute; left:1.25rem; right:1.25rem; bottom:1.5rem;
    font:400 1.05rem/1.35 Fraunces, Georgia, serif; color:#F4F1EA; pointer-events:none; }
  .thumb-mode .hero[data-slot="og"]::after { display:none; }
  .veil { position:absolute; inset:0; pointer-events:none; opacity:var(--scrim,1);
    background:linear-gradient(180deg,
    rgba(18,26,21,.46) 0%, rgba(18,26,21,.16) 38%, rgba(18,26,21,.86) 100%); }
  /* The overlay is the site's, so every size here comes from the site's rule
     for the width being previewed and is then scaled by however much the frame
     is shrunk to fit the window. Sized in rem and vw of the PICKER's window, a
     402px phone frame drew a 57px headline and full-size padding. */
  .cpy { position:absolute; left:var(--cx,96px); right:var(--cx,96px);
    bottom:var(--cb,54px); pointer-events:none; }
  .cropcss { background:#12160f; color:#93a199; border:1px solid #27322b;
    border-radius:7px; padding:11px 13px; font-size:11.5px; line-height:1.6;
    overflow-x:auto; margin:.6rem 0 0; white-space:pre; }
  .cropcss b { color:#7fd1a4; font-weight:400; }
  .cropcss i { color:#65736b; font-style:normal; }
  .cpy .eb { font-size:var(--ceb,10px); letter-spacing:.18em; text-transform:uppercase;
    color:rgba(255,255,255,.78); margin-bottom:.9em; }
  .cpy h1 { font-family:Newsreader,Georgia,serif; font-weight:300; margin:0;
    font-size:var(--ch1,54px); line-height:var(--clh,1.06); letter-spacing:-.018em;
    color:#fff; max-width:22ch; }
  .cpy .lead { font-family:'Hanken Grotesk',Helvetica,Arial,sans-serif; font-weight:300;
    color:rgba(255,255,255,.92); font-size:calc(var(--ch1,54px) * .3); line-height:1.5;
    margin:.9em 0 0; max-width:44ch; }
  .cpy .lead[hidden] { display:none; }
  .cpy .cta { display:inline-block; margin-top:1.5em; font-size:var(--ccta,10px);
    letter-spacing:.16em; text-transform:uppercase; color:#fff;
    border-bottom:1px solid rgba(255,255,255,.5); padding-bottom:.35em; }
  .meta { display:flex; gap:22px; margin-top:12px; font-size:12px;
    color:rgba(28,40,33,.6); flex-wrap:wrap; align-items:center; }
  .meta b { color:var(--ink); font-weight:500; }
  .meta .bad { color:var(--warn); font-weight:600; }
  .meta .mid { color:var(--amber); font-weight:600; }
  .meta .good { color:var(--terra); font-weight:600; }
  .strip { border-left:1px solid var(--line); overflow:auto; padding:12px;
    display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:8px;
    align-content:start; }
  .strip .t { position:relative; cursor:pointer; border:2px solid transparent;
    background:#f2f2ef; }
  .strip .t.on { border-color:var(--terra); }
  /* the library's pick button: a round +, green with a star once picked (2026-09-27) */
  .strip .fav, .embed .strip .fav { position:absolute; left:6px; top:6px; z-index:3; width:21px; height:21px; padding:0;
    border-radius:50%; background:rgba(28,40,33,.55); border:1px solid rgba(255,255,255,.45); color:#fff;
    display:flex; align-items:center; justify-content:center; font-size:11px; letter-spacing:0; line-height:1;
    cursor:pointer; opacity:0; transition:opacity .15s; text-transform:none; }
  .strip .t:hover .fav, .strip .t.star .fav { opacity:1; }
  .strip .t.star .fav, .embed .strip .t.star .fav { background:#2D6B50; border-color:#2D6B50; }
  .strip .t.star { outline:2px solid #2D6B50; outline-offset:-2px; }
  .picks-bar { grid-column:1/-1; position:sticky; top:-12px; z-index:5; background:#fff; margin:-12px -12px 0; padding:10px 12px;
    border-bottom:1px solid var(--line); display:flex; align-items:center; }
  .picks-bar button, .embed .picks-bar button { width:100%; height:34px; justify-content:center; border-radius:8px !important; box-sizing:border-box;
    border:1px solid #F0F0EB !important; background:#F0F0EB !important; color:#1C2821 !important;
    font-size:.62rem !important; font-weight:600 !important; letter-spacing:.1em !important; text-transform:uppercase !important; }
  .picks-bar button:hover { background:#E7E7E0 !important; border-color:#E7E7E0 !important; }
  .picks-bar button.on, .embed .picks-bar button.on { background:#2D6B50 !important; border-color:#2D6B50 !important; color:#fff !important; }
  .picks-bar-old button { font-family:'Hanken Grotesk',sans-serif; font-size:12px; font-weight:500; letter-spacing:0;
    text-transform:none; border:1px solid rgba(28,40,33,.16); background:#fff; color:#1C2821; border-radius:999px; padding:5px 12px;
    display:inline-flex; align-items:center; gap:6px; cursor:pointer; }
  .picks-bar button.on, .embed .picks-bar button.on { background:#2D6B50; color:#fff; border-color:#2D6B50; }
  .picks-bar button span:empty { display:none; }
  .picks-bar button span { font-weight:600; }
  .strip .t.saved::after { content:'SAVED HERO'; position:absolute; right:0; bottom:0;   /* bottom right: the pick button owns the top left */
    background:var(--terra); color:#fff; font-size:8.5px; letter-spacing:.1em;
    padding:2px 6px; }
  /* the strip cuts the same shape the chosen hero does, so the crop is no
     surprise when you pick */
  .strip img { width:100%; display:block; aspect-ratio:var(--shape,2.118);
    object-fit:cover; }
  .strip .n { position:absolute; left:6px; right:6px; bottom:6px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:9px; letter-spacing:.06em;
    color:#fff; text-shadow:0 1px 2px rgba(0,0,0,.7); opacity:0; transition:opacity .15s; }
  .strip .t:hover .n, .strip .t.on .n { opacity:1; }      /* names on hover only (2026-09-27) */
  /* the adjustments, right under the preview's top edge */
  .tools { display:flex; flex-wrap:wrap; align-items:center; gap:.5rem .9rem; margin:0 0 12px; }
  .tools-gap { flex:1; }
  .tools select { width:auto; max-width:12rem; }
  .tools .scrim-ctl input[type=range] { width:96px; }
  .full .tools { margin:12px 26px; }
  .link-chip { display:inline-flex; align-items:center; gap:.4rem; font-size:11px; letter-spacing:.04em;
    padding:.35rem .75rem; border-radius:999px; background:#EEF1EE; color:#4f5c54; white-space:nowrap;
    max-width:32ch; overflow:hidden; text-overflow:ellipsis; }
  .link-chip.on { background:rgba(45,107,80,.1); color:#2D6B50; }
  .link-chip::before { content:''; width:7px; height:7px; border-radius:50%; background:currentColor; opacity:.8; flex:none; }
  .cssbox { margin:.7rem 0 0; }
  .css-actions { display:flex; align-items:center; gap:10px; margin:.5rem 0 0; }
  .css-actions button, .embed .css-actions button { font-family:'Hanken Grotesk',sans-serif; font-size:11px; font-weight:600; letter-spacing:.1em;
    text-transform:uppercase; padding:.45rem .9rem; border-radius:999px; background:#fff; color:#1C2821; border:1px solid rgba(28,40,33,.16); cursor:pointer; }
  .css-actions button:hover { border-color:#2D6B50; color:#2D6B50; background:#fff; }
  #copied { font-size:11px; color:#2D6B50; }
  .cssbox summary { cursor:pointer; font-size:11px; letter-spacing:.1em; text-transform:uppercase; color:#4f5c54; width:max-content; }
  .cssbox summary:hover { color:var(--terra); }
  .full .cssbox { margin:.7rem 26px 0; }
  .flag { position:absolute; top:6px; right:6px; font-size:8.5px; color:#fff;
    padding:2px 6px; letter-spacing:.08em; }
  .flag.low { background:var(--warn); }
  .flag.fair { background:var(--amber); }
  .flag.blur { background:var(--warn); top:26px; }
  .hint { display:none !important; }
  .meta .lowres, .key .lowres { font-style:normal; font-size:8.5px; letter-spacing:.08em; padding:1px 6px;
    color:#6b7a70; background:#fff; border:1px solid rgba(28,40,33,.22); }
  .hint-old { font-size:11.5px; color:rgba(28,40,33,.5); margin:8px 0 0; }
  .key { font-size:11px; color:rgba(28,40,33,.5); margin:10px 0 0;
    display:flex; gap:14px; flex-wrap:wrap; align-items:center; }
  .key i { font-style:normal; color:#fff; padding:1px 6px; font-size:8.5px;
    letter-spacing:.08em; }
  .scrim-ctl { display:flex; align-items:center; gap:8px; font-size:11px;
    letter-spacing:.08em; text-transform:uppercase; color:rgba(28,40,33,.55); }
  .scrim-ctl input[type=range] { width:120px; accent-color:var(--terra); }
  .scrim-ctl b { color:var(--ink); font-variant-numeric:tabular-nums;
    min-width:34px; text-align:right; }
  /* ---- full bleed: the strip hides and the hero runs edge to edge at the
     site's own size for the rule being set (the real window width, the fixed
     680 / 560 / 470 height, the phone frame centered at its 402px), so the
     photo is judged in the exact frame, at the exact pixels, the page will show.
     A rule wider than the window falls back to the window, which is what the
     site would do in a window that size too. ---- */
  .full .main { grid-template-columns:minmax(0,1fr); }
  .full .strip { display:none; }
  .full .stage { padding:0 0 22px; }
  /* the frame takes the site's own rule for the shape, not a height computed
     from the rule's nominal width: the article banner is an aspect ratio with a
     floor, so in a window narrower than the rule it still keeps the page's
     proportion instead of coming out squarer and taller than the page does */
  .full .hero { width:min(100%, calc(var(--bw, 1440) * 1px)); margin:0 auto;
    height:var(--fh, auto); aspect-ratio:var(--far, auto); min-height:var(--fmin, 0); }
  .full .meta, .full .hint, .full .key { margin-left:26px; margin-right:26px; }
  .full .cropcss { margin:.6rem 26px 0; }

  /* the hide-the-sidebar arrow, the same one the Article Editor and Photo Library use (2026-09-27) */
  .side-handle{width:24px;height:24px;border-radius:50%;border:1px solid rgba(28,40,33,.16);background:#fff;
    color:#4f5c54;box-shadow:0 1px 4px rgba(28,40,33,.12);display:flex;align-items:center;justify-content:center;
    cursor:pointer;padding:0;z-index:30;transition:margin .2s,right .2s,left .2s}
  .side-handle:hover{color:#2D6B50;border-color:#2D6B50}
  .side-handle:focus-visible{outline:2px solid #2D6B50;outline-offset:2px}
  .side-handle svg{width:12px;height:12px;transition:transform .2s}
  /* the strip on the left, like the other apps' sidebars (2026-09-27) */
  .main{grid-template-columns:400px minmax(0,1fr) !important}
  .main > .strip{grid-column:1;grid-row:1;border-left:0 !important;border-right:1px solid var(--line)}
  .main > .stage{grid-column:2;grid-row:1}
  #bleed{display:none !important}
  .embed #linkchip{display:none !important}   /* the suite bar's file box names the linked article now (2026-09-28) */   /* the sidebar arrow does this now (2026-09-28); F still toggles full bleed */
  .main > .side-handle{grid-column:1;grid-row:1;align-self:start;justify-self:end;margin:10px -12px 0 0}
  /* the picker's own button rules (padding, uppercase, dark fill) would win on specificity */
  .main > button.side-handle, .embed .main > button.side-handle{padding:0;width:24px;height:24px;border-radius:50%;background:#fff;color:#4f5c54;border:1px solid rgba(28,40,33,.16);letter-spacing:0;font-size:0}
  .main > button.side-handle:hover{color:#2D6B50;border-color:#2D6B50;background:#fff}
  .strip-hidden .main{grid-template-columns:0 minmax(0,1fr) !important}
  .strip-hidden .strip{visibility:hidden;padding:0;border:0}
  .strip-hidden .main > .side-handle{margin-right:-30px}
  .strip-hidden .main > .side-handle svg{transform:rotate(180deg)}
  .full .main > .side-handle{display:none}       /* full bleed already hides the strip */
  .full .main{grid-template-columns:minmax(0,1fr) !important}
  .full .main > .stage{grid-column:1}
/* ============== FLAGGED DROPDOWN + ALBUM FLAGS (2026-09-27) ============== */
  .fsel-native{display:none !important}
  .fsel{position:relative;flex:1;min-width:0}
  .fsel-btn{width:100%;display:flex;align-items:center;gap:8px;height:34px;padding:0 10px;border:1px solid rgba(28,40,33,.16);border-radius:6px;
    background:#fff;font-family:'Hanken Grotesk',sans-serif;font-size:.8rem;color:#1C2821;cursor:pointer;text-align:left}
  .fsel-btn:hover{border-color:rgba(28,40,33,.32)}
  .fsel.open .fsel-btn{border-color:#2D6B50;box-shadow:0 0 0 3px rgba(45,107,80,.14)}
  .fsel-t{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .fsel-chev{width:6px;height:6px;border-right:1.5px solid #1C2821;border-bottom:1.5px solid #1C2821;transform:rotate(45deg) translateY(-2px);flex:none}
  .fsel-list{position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:60;background:#fff;border:1px solid rgba(28,40,33,.14);border-radius:8px;
    box-shadow:0 12px 32px rgba(28,40,33,.16);max-height:340px;overflow:auto;padding:4px}
  .fsel-o{display:flex;align-items:center;gap:9px;padding:7px 9px;border-radius:5px;font-family:'Hanken Grotesk',sans-serif;font-size:.8rem;color:#1C2821;cursor:pointer}
  .fsel-o:hover{background:#F3F6F4}
  .fsel-o.on{background:rgba(45,107,80,.09);color:#2D6B50;font-weight:600}
  .afl{width:18px;height:13px;border-radius:2px;object-fit:cover;flex:none;display:inline-block}
  .afl.none{background:#E8E7E1}
  .afl.emo{width:18px;height:13px;font-size:13px;line-height:13px;text-align:center;overflow:visible}
  
  header .fsel{flex:0 1 250px}
  .embed header .fsel-btn, header .fsel-btn{height:34px;text-transform:none;letter-spacing:0;font-size:.8rem;font-weight:400;padding:0 10px;border-radius:6px;background:#fff;color:#1C2821;border:1px solid rgba(28,40,33,.16)}

  /* thin scrollbars everywhere (2026-09-27) */
  ::-webkit-scrollbar{width:4px;height:4px}
  ::-webkit-scrollbar-track{background:transparent}
  ::-webkit-scrollbar-thumb{background:transparent;border-radius:2px}
  *:hover::-webkit-scrollbar-thumb{background:rgba(28,40,33,.3)}
  ::-webkit-scrollbar-thumb:hover{background:rgba(28,40,33,.5)}
  
  /* LOW RES: the only badge, dark green with white text (2026-09-27) */
  .strip .flag.low, .meta .lowres { background:#2D6B50 !important; color:#fff !important; border:0 !important; font-style:normal;
    font-size:8.5px; letter-spacing:.08em; padding:2px 6px; font-weight:600; }
  .strip .flag.fair, .strip .flag.blur, .key { display:none !important; }
  /* one type standard for controls, the same as the editor's (2026-09-27) */
  select, .fsel-btn, .fsel-o, input[type=text]{font-family:'Hanken Grotesk',sans-serif !important;font-size:13px !important;font-weight:400 !important;letter-spacing:0 !important;text-transform:none !important}
  header button:not(.fsel-btn), .tools button, .picks-bar button, .css-actions button,
  .embed header button:not(.fsel-btn), .embed .tools button, .embed .picks-bar button, .embed .css-actions button{font-family:'Hanken Grotesk',sans-serif !important;font-size:11px !important;font-weight:600 !important;letter-spacing:.08em !important;text-transform:uppercase !important}
  .toast { position:fixed; left:50%; bottom:22px; transform:translateX(-50%);
    background:var(--ink); color:#fff; padding:10px 18px; font-size:12px;
    letter-spacing:.06em; opacity:0; transition:opacity .25s; pointer-events:none; }
  .toast.on { opacity:1; }

  /* ---- embedded in the editor suite: the controls become the suite's light
     toolbar under its dark bar, matching the Article Editor's toolbar ---- */
  .embed body { background:#F7F7F3; height:100vh; display:flex; flex-direction:column;
    overflow:hidden; }
  .embed header { background:#fff; border-bottom:1px solid rgba(28,40,33,.14);
    padding:.55rem 1.1rem; gap:.45rem .55rem; flex:0 0 auto; }
  .embed header .logo, .embed header .tally { display:none; }   /* both live in the suite bar */
  .embed select, .embed input[type=text] { font-size:.74rem; padding:.4rem .6rem;
    border:1px solid rgba(28,40,33,.16); border-radius:6px; color:#1C2821; }
  .embed select { -webkit-appearance:none; appearance:none; padding-right:1.7rem; cursor:pointer;
    background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%231C2821' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat right .6rem center/10px 6px; }
  .embed .scrim-ctl { color:#4f5c54; }
  .embed select:focus, .embed input[type=text]:focus { outline:none; border-color:#2D6B50;
    box-shadow:0 0 0 2px rgba(45,107,80,.14); }
  .embed button { font-family:'Hanken Grotesk',sans-serif; font-size:.6rem; font-weight:600;
    letter-spacing:.1em; padding:.5rem .95rem; border-radius:999px; border:1px solid
    rgba(28,40,33,.14); background:#fff; color:#1C2821; transition:background .15s; }
  .embed button:hover { background:#EDEDE7; }
  .embed button#save { background:#2D6B50; border-color:#2D6B50; color:#fff; }
  .embed button#save:hover { background:#255c43; }
  .embed .scrim-ctl { font-size:.58rem; }
  .embed .main { height:auto; flex:1 1 auto; min-height:0; }
  .embed .stage { background:#F7F7F3; }
  .embed .strip { background:#fff; }
</style></head>
<body>
<header>
  <span class="logo">covers</span>
  <select id="album"></select>
  <select id="filter">
    <option value="all">Every photo</option>
    <option value="ok" selected>Hide low res</option>
    <option value="best">Hero-ready only</option>
  </select>
  <span class="tally" id="tally"></span>
  <span class="tally" id="count"></span>
  <input type="text" id="title" placeholder="Article title shown on the hero">
  <select id="shape" title="Which hero on the site this photo is destined for">
    <option value="home" selected>Home hero</option>
    <option value="article">Article banner</option>
    <option value="thumb">Article thumbnail</option>
  </select>
  <span class="link-chip" id="linkchip" title="Where Save pick writes: the article open in the Article Editor, or (with none open) the country's own hero"></span>
</header>
<div class="main">
  <div class="stage">
    <div class="tools" id="tools">
  <select id="vw" title="Which of the site's three hero rules you are setting.
Each keeps its own crop, because the hero is a fixed height and changes shape
with the window.">
  </select>
  <span class="scrim-ctl" title="Straighten: degrees of rotation, counter-clockwise for positive. Cut to the largest frame with no blank corners, the same way the photo editor bakes it. Double-click the word to reset.">
    <span id="anglename" style="cursor:pointer">Straighten</span>
    <input type="range" id="angle" min="-5" max="5" step="0.1" value="0"><b id="anglev">0.0&deg;</b>
  </span>
  <span class="scrim-ctl" id="scrim-ctl" title="How heavy the overlay sits on this photo">
    Scrim <input type="range" id="scrim" min="0" max="160" value="100"><b id="scrimv">100%</b>
  </span>
  <button class="q" id="bleed" title="Hide the strip and show the hero edge to edge, the size the site cuts it (F)">Full bleed</button>
  <span class="tools-gap"></span>
  <button class="q" id="same-hero" hidden title="Use the photo this article's hero was cut from, for continuity">Same photo as the hero</button>
  <button id="save">Save pick</button>
    </div>
    <div class="hero" id="hero">
      <img id="pic" draggable="false" alt="">
      <div class="veil"></div>
      <div class="navband" id="navband"><span class="nb-logo">getawayguide</span>
        <span class="nb-links" id="nb-links"><span>Home</span><span>Destinations</span><span>Resources</span><span>About Me</span></span>
        <span class="nb-burger" id="nb-burger" hidden><i></i><i></i><i></i></span></div>
      <div class="cpy">
        <div class="eb" id="eb">Country &middot; Field notes</div>
        <h1 id="h1">Pick a photo from the strip</h1>
        <p class="lead" id="lead" hidden></p>
        <span class="cta">Read the guide &rarr;</span>
      </div>
    </div>
    <div class="meta" id="meta"></div>
    <p class="hint" id="hint">Drag the photo to set the crop.</p>
    <details class="cssbox" id="cssbox"><summary>Show CSS</summary>
      <div class="css-actions"><button type="button" id="copy-claude" title="Copy a prompt for Claude: this photo, where it goes, and the CSS">Copy for Claude</button><span id="copied" hidden>Copied</span></div>
      <pre class="cropcss" id="cropcss"></pre></details>
    <p class="key">
      <span><i class="flag low" style="position:static">LOW RES</i> under 1920px, blurry at any width</span>
      <span><i class="flag fair" style="position:static">1080p-ish</i> fine at 1x, soft at 2x</span>
      <span><i class="flag blur" style="position:static">SOFT FOCUS</i> less fine detail than the rest of the album</span>
    </p>
  </div>
  <div class="strip" id="strip">Loading&hellip;</div>
  <button class="side-handle" id="side-handle" type="button" title="Hide the photo strip" aria-expanded="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg></button>
</div>
<div class="toast" id="toast"></div>
<script>
(function sideHandle() {
  const btn = document.getElementById('side-handle');
  const apply = on => { document.documentElement.classList.toggle('strip-hidden', on); btn.title = (on ? 'Show ' : 'Hide ') + 'the photo strip (Ctrl+\\)';
    btn.setAttribute('aria-label', (on ? 'Show ' : 'Hide ') + 'the photo strip'); btn.setAttribute('aria-expanded', String(!on)); };
  let on = false; try { on = localStorage.getItem('side:heroes') === '1'; } catch (e) {}
  apply(on);
  btn.onclick = () => { on = !on; apply(on); try { localStorage.setItem('side:heroes', on ? '1' : '0'); } catch (e) {} };
  document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === '\\') { e.preventDefault(); btn.click(); } });
  // the suite's shortcuts work from inside this frame too: Ctrl+Alt+1..4 switch apps, and the
  // suite asks this frame to toggle its strip when Ctrl+\ is pressed outside it (QA 2026-09-27)
  document.addEventListener('keydown', e => { if (e.ctrlKey && e.altKey && /^[1-5]$/.test(e.key) && window.parent !== window) { e.preventDefault(); window.parent.postMessage({ type: 'suite-view', n: +e.key }, '*'); } });
  window.addEventListener('message', e => { if (e.data && e.data.type === 'suite-side') btn.click(); });
})();

// Copy for Claude: everything a session needs to set this hero without the picker open
async function copyForClaude() {
  if (!cur) return;
  const target = ARTICLE ? ARTICLE.rel : ((country || 'this country') + "'s country hero (explore mode)");
  const shapeName = $('shape').selectedOptions[0] ? $('shape').selectedOptions[0].textContent : $('shape').value;
  const txt = `Set the hero for ${target} to the photo ${cur.name} (album: ${album}, file: ${cur.path}).\n` +
    `Placement: ${shapeName}. Scrim: ${$('scrimv').textContent}. Straighten: ${$('anglev').textContent.replace('\u00b0', ' degrees')}.\n` +
    `Title on the hero: ${$('title').value}\n\nCrop CSS from the hero picker:\n\n${$('cropcss').textContent.trim()}\n`;
  let ok = false;
  try { await navigator.clipboard.writeText(txt); ok = true; } catch (e) {
    const ta = document.createElement('textarea'); ta.value = txt; document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand('copy'); } catch (e2) {} ta.remove();
  }
  const c = $('copied'); c.textContent = ok ? 'Copied' : 'Could not reach the clipboard'; c.hidden = false; setTimeout(() => { c.hidden = true; }, 2200);
}
let PICKS_ONLY = false; try { PICKS_ONLY = localStorage.getItem('heroPicksOnly') === '1'; } catch (e) {}
const TITLES = %TITLES%;
const SHAPES = %SHAPES%;
let cur = null, country = '', album = '', qTimer = null;
const HERO_BP = %BREAKPOINTS%;
const THUMB_BP = [{"key": "card", "label": "Country page card", "w": 294, "h": 441, "vw": 1440, "media": "(min-width:769px)"}, {"key": "list", "label": "Home & posts card", "w": 395, "h": 296, "vw": 1440, "media": "(min-width:769px)"}, {"key": "phone", "label": "Phone card", "w": 353, "h": 199, "vw": 393, "media": "(max-width:768px)"}, {"key": "og", "label": "Share image", "w": 1200, "h": 630, "vw": 1200, "media": "link previews"}];
let BP = HERO_BP;                              // the hero rules, or the thumbnail slots in Thumbnail mode
function fillVW() {
  $('vw').innerHTML = BP.map((b, i) =>
    `<option value="${i}">${b.label} ${b.w ? b.w + '\u00d7' + b.h : b.vw}${b.media ? '  ' + b.media : ''}</option>`).join('');
  $('vw').value = String(Math.min(bpi, BP.length - 1));
}
function setModeBP() {
  const thumb = $('shape').value === 'thumb', want = thumb ? THUMB_BP : HERO_BP;
  document.body.classList.toggle('thumb-mode', thumb);
  $('same-hero').hidden = !(thumb && ARTICLE);
  $('save').textContent = thumb ? 'Save thumbnail' : 'Save pick';
  if (BP !== want) { BP = want; bpi = 0; fillVW(); if (thumb && !THUMB_QUIET) restoreThumb(); }
  $('hero').dataset.slot = BP[bpi].key;
  $('hero').dataset.cardTitle = thumb && BP[bpi].key !== 'og' ? (ARTICLE ? ARTICLE.h1 : $('title').value) : '';
}
let bpi = 0;                                   // which hero rule is being set
let crops = { desktop: 50, laptop: 50, phone: 50 };
let ALL = [], QUAL = { scores: {}, median: null, done: 0 };
let STARS = [], scrim = 100;

const $ = id => document.getElementById(id);

// The ALBUM is what you are choosing between: the trips you took. The country
// is only where the pick publishes to, which matters on save. Listing by
// country hid "Patagonia (2024)" behind Chile, "Bali" behind Indonesia and
// "Turkey 2.0" behind Türkiye, so those trips looked missing.
function albumLabel(r) {
  const album = r.folder || r.country || '';
  const bare = album.replace(/\s*\(\d{4}\)\s*$/, '').trim().toLowerCase();
  const same = bare === (r.country || '').toLowerCase();
  return album + (same ? '' : '  → ' + r.country);
}

async function loadAlbums() {
  const rows = await (await fetch('/albums')).json();
  $('album').innerHTML = rows.map(r =>
    `<option value="${r.folder}" data-country="${r.country}"
             data-picked="${r.picked ? 1 : 0}" data-pick="${r.pickPath}"
             data-crops='${JSON.stringify(r.pickCrops)}' data-scrim="${r.pickScrim}"
             data-pick-title="${(r.pickTitle || '').replace(/"/g, '&quot;')}"
             data-pick-angle="${r.pickAngle || 0}"
             data-stars="${(r.stars || []).join('|')}">${r.picked ? '✓ ' : '· '}${albumLabel(r)}</option>`
  ).join('');
  const done = rows.filter(r => r.picked).length;
  $('tally').innerHTML = `<b>${done}</b> of ${rows.length} picked`;
  $('album').onchange = loadStrip;
  await loadStrip();
  if (ARTICLE) articleAlbum();          // the editor may have spoken before the list existed
}

// The article open in the editor, when embedded there: its headline and lead go on the
// stage in place of the country default, so the crop is judged under the real copy.
let ARTICLE = null, PICKS = {};
fetch('/picks').then(r => r.json()).then(p => { PICKS = p || {}; }).catch(() => {});
// the linked article's own saved pick, if it has one (picks are stored per article)
const articlePick = () => ARTICLE && PICKS[ARTICLE.rel] && (PICKS[ARTICLE.rel].country || '').toLowerCase() === (country || '').toLowerCase() ? PICKS[ARTICLE.rel] : null;
window.addEventListener('message', async ev => {
  const d = ev.data;
  if (!d || d.type !== 'article-context') return;
  const before = ARTICLE && ARTICLE.rel;
  ARTICLE = d.h1 ? d : null;             // {rel, country, h1, lead, heroSlug}
  // the editor re-sends the linked article whenever you come back to this tab; only a DIFFERENT
  // article moves the album, so a country you picked by hand is not switched back under you.
  // Switch first, then fetch the saved picks: waiting on them made the switch feel stuck.
  if ((ARTICLE && ARTICLE.rel) !== before) articleAlbum(); else applyArticle();
  try { PICKS = await (await fetch('/picks')).json(); } catch (e) {}
  if (ARTICLE && $('album').options.length) render(true);   // restore this article's pick
});
// switch to the open article's album; if the album list is not in yet, loadAlbums() calls
// this again when it is
function articleAlbum() {
  const d = ARTICLE;
  if (!d || !$('album').options.length) { applyArticle(); return; }
  const fold = s => (s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();   // Türkiye = Turkiye
  const opts = [...$('album').options].filter(o => o.value !== '__article__');
  const opt = d.country && (opts.find(o => fold(o.dataset.country) === fold(d.country)) || opts.find(o => fold(o.value).startsWith(fold(d.country))));
  if (opt && $('album').value !== opt.value) { $('album').value = opt.value; loadStrip(); }
  else applyArticle();
}
function articleOption() {
  // "This article's photos": the photos the open article already shows (Kevin picked the Orgov
  // thumbnail from the article's own edited IMG_0438-2, which no backup album has)
  const sel = $('album'); let o = sel.querySelector('option[value="__article__"]');
  if (ARTICLE && !o) {
    o = document.createElement('option'); o.value = '__article__';
    sel.insertBefore(o, sel.firstChild);
  }
  if (o) {
    if (!ARTICLE) { o.remove(); return; }
    o.dataset.country = ARTICLE.country || '';
    o.textContent = '\u2605 This article\'s photos (' + (ARTICLE.rel || '').split('/').pop().replace(/\.html$/, '') + ')';
  }
}
function applyArticle() {
  articleOption();
  if ($('shape').value === 'thumb') setModeBP();
  const chip = $('linkchip');
  if (chip) {
    chip.classList.toggle('on', !!ARTICLE);
    chip.textContent = ARTICLE ? 'Linked to ' + (ARTICLE.rel || '').split('/').pop().replace(/\.html$/, '').replace(/-/g, ' ')
                               : 'Explore mode';
    chip.title = ARTICLE ? 'Save pick builds this hero into ' + ARTICLE.rel
                         : 'No article is open in the Article Editor, so Save pick stores the ' + (country || 'country') + ' hero';
  }
  if (ARTICLE) {
    $('eb').textContent = country + ' · ' + (ARTICLE.rel || '').split('/').pop().replace(/\.html$/, '').replace(/-/g, ' ');
    $('title').value = ARTICLE.h1;
    $('h1').textContent = ARTICLE.h1;
    $('lead').textContent = ARTICLE.lead || '';
    $('lead').hidden = !ARTICLE.lead;
  } else {
    $('lead').hidden = true;
    $('eb').textContent = country + ' · Field notes';
  }
}

async function loadStrip() {
  const sel = $('album').selectedOptions[0];
  album = sel.value;
  country = sel.dataset.country;
  $('eb').textContent = country + ' · Field notes';
  // B9: a title edited at save time comes back next visit instead of the default
  $('title').value = sel.dataset.pickTitle || TITLES[country] || (country + ' Travel Guide');
  $('h1').textContent = $('title').value;
  applyArticle();
  STARS = (sel.dataset.stars || '').split('|').filter(Boolean);
  // (2026-09-28) the last album CHOSEN wins: a slow answer for an album you have already left
  // used to arrive late and draw that country's photos over the one you picked. An album seen
  // before is drawn from memory at once and refreshed behind it.
  if (!STRIP_CACHE) { STRIP_CACHE = new Map(); STRIP_GEN = 0; }
  if (album === '__article__') { country = ARTICLE ? ARTICLE.country : country; STRIP_CACHE.delete('__article__'); }
  const my = ++STRIP_GEN, want = album;
  const ctx = want + '|' + (ARTICLE ? ARTICLE.rel : '');
  if (window.__stripCtx !== undefined && window.__stripCtx !== ctx) {   // another album or article: the old photo is not its pick
    cur = null; picW = 0; crops = { desktop: 50, laptop: 50, phone: 50 }; if (angle) setAngle(0);
    document.querySelectorAll('.strip .t.on').forEach(x => x.classList.remove('on'));
  }
  window.__stripCtx = ctx;
  if (STRIP_CACHE.has(want)) { ALL = STRIP_CACHE.get(want); render(true); }
  else $('strip').textContent = 'Loading…';
  let rows;
  const url = want === '__article__'
    ? '/article_photos?rel=' + encodeURIComponent(ARTICLE ? ARTICLE.rel : '')
    : '/photos?album=' + encodeURIComponent(want);
  try { rows = await (await fetch(url)).json(); } catch (e) { return; }
  STRIP_CACHE.set(want, rows);
  if (my !== STRIP_GEN || album !== want) return;          // you have moved on
  const changed = ALL !== rows && JSON.stringify(ALL) !== JSON.stringify(rows);
  ALL = rows;
  if (changed || !document.querySelector('.strip .t')) render(true);   // a fresh album restores whatever was saved for it
  if (want !== '__article__') pollQuality();
  if (THUMB_PENDING) { const tp = THUMB_PENDING; THUMB_PENDING = null; tp(); }
}
var STRIP_GEN = STRIP_GEN || 0; var STRIP_CACHE = STRIP_CACHE || new Map();   // var: loadStrip can run before this line does

/* the strip is for choosing, so by default it hides what could never be a hero */
function render(restore) {
  const sel = $('album').selectedOptions[0];
  const keep = cur;              // the innerHTML rebuild below drops .on
  const mode = $('filter').value;
  const shape = $('shape').value;
  const ap = articlePick();
  const saved0 = ap ? ap.path : sel.dataset.pick;
  // whatever is already saved stays visible, even when the filter would hide it
  const rows = ALL.filter(r => r.path === saved0 || (PICKS_ONLY && !STARS.includes(r.path) ? false : (
      mode === 'all'  ? true
    : mode === 'best' ? r.tier === 'good'
    :                   r.tier !== 'low')));
  const hidden = ALL.length - rows.length;
  const saved = ap ? ap.path : sel.dataset.pick;
  $('count').textContent = hidden
    ? `${rows.length} shown, ${hidden} hidden` : `${rows.length} photos`;
  $('strip').innerHTML = `<div class="picks-bar"><button id="picks-only" aria-pressed="${PICKS_ONLY}" class="${PICKS_ONLY ? 'on' : ''}" title="Show only your hero picks for this album (click again for every photo)">&#9733; Blog picks <span>${STARS.length || ''}</span></button></div>` + rows.map(r => `
    <div class="t${r.path === saved ? ' saved' : ''}${STARS.includes(r.path) ? ' star' : ''}"
         data-p="${r.path}" data-w="${r.heroW}" data-name="${r.name}" data-tier="${r.tier}">
      <button class="fav" title="${STARS.includes(r.path) ? 'In your picks: click to remove' : 'Add to your picks'}">${STARS.includes(r.path) ? '★' : '+'}</button>
      <img loading="lazy" decoding="async"
           src="/img?w=340&p=${encodeURIComponent(r.path)}">
      ${r.tier === 'low' ? '<span class="flag low">LOW RES</span>'
        : r.tier === 'fair' ? '<span class="flag fair">' + r.w + 'px</span>' : ''}
      <span class="n">${r.name}</span>
    </div>`).join('');
  document.querySelectorAll('.strip .t').forEach(t => t.onclick = () => pick(t));
  document.querySelectorAll('.strip .fav').forEach(f => f.onclick = ev => {
    ev.stopPropagation();          // starring is not choosing
    toggleStar(f.closest('.t'));
  });
  $('count').textContent += STARS.length ? ` · ${STARS.length} pick${STARS.length === 1 ? '' : 's'}` : '';
  $('picks-only').onclick = () => { PICKS_ONLY = !PICKS_ONLY; try { localStorage.setItem('heroPicksOnly', PICKS_ONLY ? '1' : '0'); } catch (e) {} render(true); };
  if (restore && saved && shape !== 'thumb') {
    const t = document.querySelector(`.strip .t[data-p="${CSS.escape(saved)}"]`);
    const src = ap ? { dataset: { crops: JSON.stringify(ap.crops || {}), scrim: ap.scrim, pickAngle: ap.angle } } : sel;
    if (t) { pick(t); crops = readCrops(src); applyCrop();
             setScrim(+src.dataset.scrim || 100);
             setAngle(+src.dataset.pickAngle || 0);
             t.scrollIntoView({block:'center'}); }
  } else if (keep) {
    // keep the selection visible without touching the crop in progress
    const t = document.querySelector(`.strip .t[data-p="${CSS.escape(keep.path)}"]`);
    if (t) { t.classList.add('on'); }
  }
  // The CSS box is the one thing here you copy verbatim into artifact.css, and its
  // class comes from `country`. Only applyCrop() rewrites it, and that ran solely
  // when an album had a saved pick to restore -- so opening Philippines after
  // Albania left it reading `.hp-albania`. Redraw it whenever the album changed.
  if (restore && cur) { applyCrop(); }
  applyFocusFlags();
}

$('filter').onchange = () => { render(); };   // keeps the crop in progress

/* The picker shows whichever hero this photo is destined for, at whichever
   screen width it is being judged for, in the stage and the strip alike. The
   hero is a fixed height, so width is half the shape: the same photo is a
   2.12:1 strip on a laptop and a 2.82:1 one on a 1920 monitor. */
function boxOf(i) {
  const shape = $('shape').value;
  const vw = BP[i].vw;
  if (shape === 'thumb') { return { w: BP[i].w, h: BP[i].h }; }
  if (shape === 'wide') { return { w: vw, h: vw / (16 / 9) }; }
  if (vw <= 768) { return { w: vw, h: shape === 'article' ? 420 : 470 }; }
  // the article banner is aspect-ratio 1440/560 with a 420px floor, not a
  // fixed height: it is 741 tall on a 1905 monitor and 498 on a 1280 laptop
  if (shape === 'article') { return { w: vw, h: Math.max(420, vw * 560 / 1440) }; }
  return { w: vw, h: 680 };
}
function heroRatio() { const b = boxOf(bpi); return b.w / b.h; }

/* cover pins whichever dimension runs out first; the other is the one you can
   actually drag. For a 16:9 photo that is sideways on the phone and vertical
   on the two desktop shapes, so the picker has to ask per photo per shape. */
function slotPhoto(i) { return $('shape').value === 'thumb' && BP[i] && BP[i].key === 'og' && OG ? OG : cur; }
function geomFor(i) {
  const img = $('pic'), ph = slotPhoto(i);
  // the slot's own photo's shape: in Thumbnail mode the stage may be showing the share image's photo
  const src = ph && ph.ar ? ph.ar : img.naturalWidth ? img.naturalWidth / img.naturalHeight : 0;
  if (!src) { return { axis: 'y', travel: 0, shown: 1 }; }
  const b = boxOf(i);
  if (src > b.w / b.h) {
    const sw = b.h * src;
    return { axis: 'x', travel: sw - b.w, shown: b.w / sw };
  }
  const sh = b.w / src;
  return { axis: 'y', travel: sh - b.h, shown: b.h / sh };
}

function readCrops(sel) {
  try {
    const c = JSON.parse(sel.dataset.crops || '{}');
    return { desktop: +c.desktop || 50, laptop: +c.laptop || 50, phone: +c.phone || 50 };
  } catch (e) { return { desktop: 50, laptop: 50, phone: 50 }; }
}

/* the site's literal sizing for the full-bleed frame: a fixed height where the
   site fixes one, the aspect ratio with its floor where the site uses that */
function siteFrame() {
  const shape = $('shape').value, vw = BP[bpi].vw, r = document.documentElement.style;
  r.setProperty('--bw', vw);
  const set = (h, ar, min) => { r.setProperty('--fh', h); r.setProperty('--far', ar); r.setProperty('--fmin', min); };
  if (shape === 'thumb') { set('auto', BP[bpi].w + ' / ' + BP[bpi].h, '0'); r.setProperty('--bw', BP[bpi].w); }
  else if (shape === 'wide') { set('auto', '16 / 9', '0'); }
  else if (vw <= 768) { set((shape === 'article' ? 420 : 470) + 'px', 'auto', '0'); }
  else if (shape === 'article') { set('auto', '1440 / 560', '420px'); }
  else { set('680px', 'auto', '0'); }
  // the band is a share of the frame, so it is sized after the frame is
  requestAnimationFrame(() => navBand($('hero').getBoundingClientRect()));
}

function applyShape() {
  setModeBP();
  document.documentElement.style.setProperty('--shape', heroRatio());
  siteFrame();
  applyCrop();
  render();
  loadPic();
}

/* ---- full bleed ---- */
let full = false, picW = 0;      // picW: the width the stage photo was last asked at

function setFull(on, quiet) {
  full = !!on;
  document.documentElement.classList.toggle('full', full);
  $('bleed').textContent = full ? 'Show strip' : 'Full bleed';
  try { localStorage.setItem('hp-full', full ? '1' : '0'); } catch (e) {}
  if (!quiet) { applyShape(); }
}
$('bleed').onclick = () => setFull(!full);

/* Enough device pixels to fill the rendered frame with no upscaling. cover
   scales the photo to the frame's HEIGHT when it is pinned there, so the width
   it needs can be well over the frame's own width. Rounded up in 200px steps
   so the cache holds a handful of variants per photo, not one per window size. */
function wantW() {
  const box = $('hero').getBoundingClientRect(), img = $('pic');
  const dpr = window.devicePixelRatio || 1;
  const src = img.naturalWidth ? img.naturalWidth / img.naturalHeight : 1.5;
  const w = Math.max(box.width, box.height * src) * dpr;
  return Math.min(4000, Math.ceil(w / 200) * 200);
}

/* the stage photo is 1600 wide beside the strip, which is plenty for a frame
   under 1200px; at full bleed it is whatever the frame needs, and never a
   downgrade once a bigger one is loaded */
function loadPic(force) {
  if (!cur) { return; }
  const sp = stagePhoto(), a = sp === cur ? angle : (+sp.angle || 0);
  if (sp.path + '|' + a !== picKey) { force = true; picKey = sp.path + '|' + a; }
  const w = full ? wantW() : 1600;
  if (!force && w <= picW) { return; }
  picW = w;
  const img = $('pic');
  img.onload = () => {              // the axis is not known until the photo is
    if (img.naturalWidth) sp.ar = img.naturalWidth / img.naturalHeight;
    applyCrop();
    if (full && wantW() > picW) { loadPic(); }   // the first guess used a stand-in ratio
  };
  img.src = '/img?w=' + w + '&p=' + encodeURIComponent(sp.path) + (a ? '&a=' + a : '');
}
let angle = 0, picKey = '';
// Thumbnail mode: the share image is its own file, so it can be a different photo from the cards
let OG = null;
function onOgSlot() { return $('shape').value === 'thumb' && BP[bpi] && BP[bpi].key === 'og'; }
function stagePhoto() { return onOgSlot() && OG ? OG : cur; }
function setAngle(v, reload) {
  angle = Math.max(-5, Math.min(5, Math.round(v * 10) / 10));
  $('angle').value = angle;
  $('anglev').textContent = angle.toFixed(1) + '\u00b0';
  if (reload !== false) { picW = 0; loadPic(true); }     // a new cut of the photo
}
let angleT = null;
$('angle').addEventListener('input', e => {
  // preview the tilt at once with CSS, and fetch the real cut once the drag settles. The
  // CSS tilt stays on until the cut has LOADED, so the photo never snaps back untilted
  // while the server is working.
  const v = +e.target.value;
  angle = Math.round(v * 10) / 10; $('anglev').textContent = angle.toFixed(1) + '\u00b0';
  $('pic').style.transform = 'rotate(' + (-angle) + 'deg) scale(1.08)';
  clearTimeout(angleT);
  angleT = setTimeout(() => {
    const img = $('pic');
    img.addEventListener('load', () => { img.style.transform = ''; }, { once: true });
    setAngle(angle);
  }, 400);
});
$('anglename').addEventListener('dblclick', () => setAngle(0));
let resizeT = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeT);
  resizeT = setTimeout(() => { if (full) { applyCrop(); loadPic(); } }, 250);
});

/* with the strip hidden the arrow keys are how you move between photos */
document.addEventListener('keydown', e => {
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) { return; }
  if (e.key === 'f' || e.key === 'F') { setFull(!full); e.preventDefault(); return; }
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') { return; }
  const ts = [...document.querySelectorAll('.strip .t')];
  if (!ts.length) { return; }
  let i = ts.findIndex(t => t.classList.contains('on'));
  i = (i + (e.key === 'ArrowRight' ? 1 : -1) + ts.length) % ts.length;
  pick(ts[i]);
  ts[i].scrollIntoView({ block: 'nearest' });
  e.preventDefault();
});
$('shape').onchange = applyShape;
$('vw').onchange = () => { bpi = +$('vw').value; applyShape(); };

/* the focus scores arrive as the pool finishes; label the soft frames when they do */
function applyFocusFlags() {
  if (!QUAL.median) { return 0; }
  const cut = QUAL.median * 0.62;
  let pending = 0;
  document.querySelectorAll('.strip .t').forEach(t => {
    const s = QUAL.scores[t.dataset.p];
    if (s === undefined) { pending += 1; return; }
    const soft = s !== null && s < cut;
    const has = t.querySelector('.flag.blur');
    if (soft && !has) {
      const el = document.createElement('span');
      el.className = 'flag blur';
      el.textContent = 'SOFT FOCUS';
      t.appendChild(el);
    } else if (!soft && has) { has.remove(); }
    t.dataset.focus = s === null ? '' : s;
    t.dataset.cut = cut.toFixed(2);
  });
  return pending;
}

function pollQuality() {
  clearInterval(qTimer);
  const mine = album;
  qTimer = setInterval(async () => {
    if (album !== mine) { return clearInterval(qTimer); }
    QUAL = await (await fetch('/quality?album=' + encodeURIComponent(mine))).json();
    if (!applyFocusFlags() && QUAL.done >= ALL.length) { clearInterval(qTimer); }
  }, 1200);
}

// a strip tile's shape, from its thumbnail (the same photo, smaller), until the stage photo loads
function tileAr(t) { const i = t.querySelector('img'); return i && i.naturalWidth ? i.naturalWidth / i.naturalHeight : 0; }
function pick(t, allSlots) {
  document.querySelectorAll('.strip .t.on').forEach(x => x.classList.remove('on'));
  t.classList.add('on');
  OG = null;                     // the share image is the cards' photo (Kevin, 2026-09-30)
  if (angle) setAngle(0);        // a tilt belongs to one photo (QA 2026-10-02: it carried over); a saved pick sets its own after this
  cur = { path: t.dataset.p, name: t.dataset.name, w: +t.dataset.w,
          tier: t.dataset.tier, ar: tileAr(t) };
  crops = { desktop: 50, laptop: 50, phone: 50 };
  picW = 0;
  loadPic(true);
  applyCrop();
  const q = cur.tier === 'good'
    ? '<span class="good">sharp at 2× on a 1440 hero</span>'
    : cur.tier === 'fair'
      ? '<span class="mid">fine at 1×, soft at 2×</span>'
      : '<i class="lowres" title="Too low resolution for a hero">LOW RES</i>';
  const f = t.dataset.focus;
  const soft = f && t.dataset.cut && +f < +t.dataset.cut
    ? ' <span class="bad">soft focus</span>' : '';
  $('meta').innerHTML = `<span><b>${cur.name}</b></span>` +
    q + soft;
}

async function toggleStar(t) {
  const r = await (await fetch('/star', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ country, path: t.dataset.p }) })).json();
  t.classList.toggle('star', r.starred);
  t.querySelector('.fav').textContent = r.starred ? '★' : '+';
  STARS = r.starred ? STARS.concat([t.dataset.p])
                    : STARS.filter(x => x !== t.dataset.p);
  const sel = $('album').selectedOptions[0];
  sel.dataset.stars = STARS.join('|');
  { const n = document.querySelector('#picks-only span'); if (n) n.textContent = STARS.length || ''; }
  if (PICKS_ONLY && !r.starred) { render(true); }
}

/* the scrim is what makes a bright photo readable and a dark one muddy, so it
   is adjustable per pick and saved with it */
function setScrim(v) {
  scrim = Math.max(0, Math.min(160, Math.round(v)));
  $('scrim').value = scrim;
  $('scrimv').textContent = scrim + '%';
  $('hero').style.setProperty('--scrim', scrim / 100);
}
$('scrim').addEventListener('input', e => setScrim(+e.target.value));
$('copy-claude').onclick = copyForClaude;

$('title').addEventListener('input', () => { $('h1').textContent = $('title').value; });
// (Center crop removed 2026-09-27; dragging sets the crop)

function slug(x) {
  return (x || 'hero').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

/* the value goes in whichever slot moves; the pinned one stays at 50% because
   putting anything else there would be a number that does nothing */
function posFor(i) {
  const c = crops[BP[i].key];
  const v = Math.round((Number.isFinite(c) ? c : 50) * 10) / 10;
  return geomFor(i).axis === 'x' ? v + '% 50%' : '50% ' + v + '%';
}

function applyCrop() {
  const g = geomFor(bpi);
  const key = BP[bpi].key;
  crops[key] = Math.max(0, Math.min(100, Number.isFinite(crops[key]) ? crops[key] : 50));
  $('hero').dataset.slot = key;
  const v = crops[key];
  $('pic').style.setProperty('--ox', g.axis === 'x' ? v + '%' : '50%');
  $('pic').style.setProperty('--oy', g.axis === 'y' ? v + '%' : '50%');
  $('hero').dataset.axis = g.axis;

  const read = document.getElementById('oyv');
  if (read) { read.textContent = 'crop ' + Math.round(v) + '%'; }
  const fr = document.getElementById('frv');
  const r = $('hero').getBoundingClientRect();
  if (fr) {
    fr.textContent = 'frame ' + Math.round(r.width) + '×' + Math.round(r.height)
      + (full ? '' : ' (scaled)');
  }
  navBand(r);
  $('hint').innerHTML = cur
    ? 'Drag the photo <b>' + (g.axis === 'x' ? 'left or right' : 'up or down')
      + '</b> to set the ' + BP[bpi].label.toLowerCase() + ' crop. This photo is '
      + $('pic').naturalWidth + '\u00d7' + $('pic').naturalHeight
      + ', so in this frame it is pinned by ' + (g.axis === 'x' ? 'height' : 'width')
      + ' and only that one axis moves. It keeps ' + Math.round(g.shown * 100)
      + '% of the photo.'
      + (['wide', 'thumb'].includes($('shape').value) ? '' : ' The top '
         + (BP[bpi].vw <= 768 ? NAV_H_PHONE : NAV_H)
         + 'px sits under the site’s nav, so nothing you put there is seen.')
      + (full ? ' <b>← →</b> step through the strip, <b>F</b> brings it back.' : '')
    : 'Drag the photo to set the crop.';

  const cls = '.hero .slide img.hp-' + slug(country);
  const bake = angle ? '<i>/* cut with: python tools/gen_hero_variants.py &lt;original&gt; --angle ' + angle + ' ... */</i>\n' : '';
  const line = i => cls + ' { object-position:' + posFor(i).replace(
    /([\d.]+%)(?!\s*})/, m => m) + '; }';
  const mark = i => {
    const g2 = geomFor(i), v2 = Math.round(crops[BP[i].key] * 10) / 10;
    const val = g2.axis === 'x'
      ? '<b>' + v2 + '%</b> <i>50%</i>' : '<i>50%</i> <b>' + v2 + '%</b>';
    return cls + ' { object-position:' + val + '; }';
  };
  $('cropcss').innerHTML = $('shape').value === 'thumb'
    ? BP.map((b, i) => '<i>/* ' + b.label + ' ' + b.w + '\u00d7' + b.h + ' */</i> background-position: <b>' + posFor(i) + '</b>;').join('\n')
    : mark(0) + '\n\n@media ' + BP[1].media + ' {\n  ' + mark(1) + '\n}\n\n'
    + '@media ' + BP[2].media + ' {\n  ' + mark(2) + '\n}';
}

/* The nav is a fixed 60px (63 on a phone) and the hero runs under it, so the
   band is drawn at the site's height, scaled by however much this frame is
   smaller than the rule it stands for. */
const NAV_H = 60, NAV_H_PHONE = 63;
function navBand(r) {
  const vw = BP[bpi].vw, phone = vw <= 768;
  const k = r.width ? r.width / vw : 1;
  const hero = $('hero');
  // "Plain 16:9" is not a page hero, so no site nav sits over it
  const on = !['wide', 'thumb'].includes($('shape').value);
  $('navband').hidden = !on;
  hero.style.setProperty('--navk', k);
  hero.style.setProperty('--nav', on ? Math.round((phone ? NAV_H_PHONE : NAV_H) * k) + 'px' : '0px');
  $('nb-links').hidden = phone;
  $('nb-burger').hidden = !phone;

  // the site's own type for this rule, then shrunk with the frame. The headline
  // is clamp(2rem,3.6vw,3.4rem) above 768 and a flat 26px below it.
  const px = n => (n * k).toFixed(1) + 'px';
  hero.style.setProperty('--ch1', px(phone ? 26 : Math.min(54.4, Math.max(32, vw * .036))));
  hero.style.setProperty('--clh', phone ? '1.14' : '1.06');
  hero.style.setProperty('--ceb', px(10.24));            // .64rem, the kicker
  hero.style.setProperty('--ccta', px(9.92));            // .62rem
  // measured off the page rather than read off artifact.css: a later rule wins
  // on a phone, where the gutter renders at 20px and the copy sits 19.2 up
  hero.style.setProperty('--cx', px(phone ? 20 : 96));
  hero.style.setProperty('--cb', px(phone ? 19.2 : 54.4));
}

let drag = null;
$('hero').addEventListener('pointerdown', e => {
  if (!cur) { return; }
  const g = geomFor(bpi);
  drag = { at: g.axis === 'x' ? e.clientX : e.clientY,
           from: crops[BP[bpi].key], axis: g.axis };
  $('pic').classList.add('dragging');
  $('hero').setPointerCapture(e.pointerId);
});
$('hero').addEventListener('pointermove', e => {
  if (!drag) { return; }
  const img = $('pic'), box = $('hero').getBoundingClientRect();
  // A drag that starts while the stage photo is still decoding (a 5712px HEIC takes a
  // few seconds at 2000px) saw naturalWidth 0, computed NaN, and wrote NaN into the crop:
  // the photo then sat frozen even after it loaded, until another one was picked. That is
  // the "some photos don't move when dragged" Kevin saw on IMG_0596 and IMG_1043, the two
  // biggest in the album (2026-09-26). Until the pixels are in, the drag does nothing.
  if (!img.complete || !img.naturalWidth) { return; }
  const src = img.naturalWidth / img.naturalHeight;
  // travel measured on the RENDERED frame, on whichever axis is free. The old
  // version always used height and clamped the spare to 1, so a photo wider
  // than its box swung the full range on a single pixel of movement.
  const spare = drag.axis === 'x'
    ? box.height * src - box.width
    : box.width / src - box.height;
  if (spare <= 0.5) { return; }
  const now = drag.axis === 'x' ? e.clientX : e.clientY;
  const v = drag.from - (now - drag.at) / spare * 100;
  if (!Number.isFinite(v)) { return; }
  crops[BP[bpi].key] = Math.max(0, Math.min(100, v));
  applyCrop();
});
['pointerup', 'pointercancel'].forEach(ev => $('hero').addEventListener(ev, () => {
  drag = null; $('pic').classList.remove('dragging');
}));

let THUMB_PICKS = null, THUMB_PENDING = null;
function tileFor(path) { return document.querySelector(`.strip .t[data-p="${CSS.escape(path)}"]`); }
// open the album a photo lives in, then select it (fn runs once the strip has loaded)
function showPhoto(path, then) {
  const folder = path.startsWith('site:') ? '__article__' : path.split('/')[0];
  const done = () => { const t = tileFor(path); if (t) { pick(t, true); t.scrollIntoView({ block: 'center' }); } if (then) then(!!t); };
  if ($('album').value === folder && tileFor(path)) return done();
  if (![...$('album').options].some(o => o.value === folder)) { toast('That photo\'s album is not in the list'); return; }
  THUMB_PENDING = done; $('album').value = folder; loadStrip();
}
// Thumbnail mode opens on what the site shows today (Kevin, 2026-09-30: "the covers tab should open
// with the active images that are already set at their current crops"): the photo server reads the
// cards and the share image off the site, this server traces them to their originals
const PHOTO_API = 'http://127.0.0.1:5003';
let THUMB_GEN = 0, THUMB_QUIET = false;
const axisOf = (p, b) => (p.w / p.h > b.w / b.h ? 'x' : 'y');
async function restoreThumb(slots) {
  if (!ARTICLE) return;
  const my = ++THUMB_GEN, rel = ARTICLE.rel;
  if (!slots) {
    try { const d = await (await fetch(PHOTO_API + '/api/thumbs?rel=' + encodeURIComponent(rel), { cache: 'no-store' })).json(); slots = d && d.ok ? d.slots : []; }
    catch (e) { slots = []; }
  }
  let r;
  try { r = await (await fetch('/thumb_current', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rel, slots }) })).json(); }
  catch (e) { return toast('The Covers server did not answer'); }
  if (my !== THUMB_GEN || !ARTICLE || ARTICLE.rel !== rel || !r || !r.photo) return;
  const P = r.photo;
  const place = () => {
    if (my !== THUMB_GEN) return;
    crops = {};
    THUMB_BP.forEach(b => { if (b.key === 'og') return; const xy = r.xy[b.key] || [50, 50]; crops[b.key] = axisOf(P, b) === 'x' ? xy[0] : xy[1]; });
    if (cur && !cur.ar) cur.ar = P.w / P.h;
    OG = null;                   // always the cards' photo: a share image cut from another is re-cut on Save
    const oxy = (r.og && r.og.xy) || [50, 50];
    crops.og = axisOf(P, THUMB_BP[3]) === 'x' ? oxy[0] : oxy[1];
    picKey = ''; loadPic(true); applyCrop();
    if (r.notes && r.notes.length) toast(r.notes.join(' '));
  };
  const direct = () => {
    cur = { path: P.path, name: P.name, w: P.heroW, tier: P.tier, ar: P.w / P.h };
    document.querySelectorAll('.strip .t.on').forEach(x => x.classList.remove('on'));
    $('meta').innerHTML = `<span><b>${P.name}</b></span>`;
    place();
  };
  setAngle(+P.angle || 0, false);
  const folder = P.path.startsWith('site:') ? '__article__' : P.path.split('/')[0];
  if (P.path.startsWith('web:') || ![...$('album').options].some(o => o.value === folder)) return direct();
  showPhoto(P.path, ok => { if (my !== THUMB_GEN) return; if (ok) place(); else direct(); });
}
// the editor's Thumbnails panel: Edit opens this article's thumbnails as they are (2026-09-30)
window.addEventListener('message', ev => {
  if (!ev.data || ev.data.type !== 'thumb-edit') return;
  if (window.parent !== window) parent.postMessage({ type: 'thumb-edit-ok' }, '*');
  THUMB_QUIET = true;                          // the restore below reads the site; not a second one
  if ($('shape').value !== 'thumb') { $('shape').value = 'thumb'; applyShape(); }
  THUMB_QUIET = false;
  const i = Math.max(0, BP.findIndex(b => b.key === ev.data.slot));
  if (i !== bpi) { bpi = i; $('vw').value = String(i); applyShape(); }
  restoreThumb(ev.data.slots || null);
});
$('same-hero').onclick = () => {
  const hp = ARTICLE && PICKS[ARTICLE.rel];
  if (!hp || !hp.path) return toast('This article\'s hero was not saved from the Hero Picker, so pick its photo from the strip');
  showPhoto(hp.path, ok => { if (ok) { setAngle(+hp.angle || 0); applyCrop(); toast('The hero photo: now set each thumbnail crop'); } });
};
async function saveThumb() {
  if (!cur) return toast('Pick a photo first');
  if (!ARTICLE) return toast('Open the article in the editor first: a thumbnail belongs to an article');
  const positions = {}, rounded = {};
  BP.forEach((b, i) => { positions[b.key] = posFor(i); rounded[b.key] = Math.round((crops[b.key] ?? 50) * 10) / 10; });
  const body = { article: ARTICLE.rel, country: ARTICLE.country || country, path: cur.path, name: cur.name,
                 slug: (ARTICLE.rel || '').split('/').pop().replace(/\.html$/, ''), angle: angle,
                 positions: positions, crops: rounded,
                 og_path: OG ? OG.path : cur.path, og_angle: OG ? (+OG.angle || 0) : angle };
  let r;
  try { r = await (await fetch('/save_thumb', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json(); }
  catch (e) { return toast('The Covers server did not answer'); }
  if (!r.ok) return toast('Thumbnail not saved: ' + (r.error || ''));
  toast('Thumbnail saved: ' + r.card.split('/').pop() + (r.pages.length ? ', ' + r.pages.map(p => p.split('/').slice(-2).join('/')).join(', ') : '') + (r.og ? ' and the share image' : ''));
  if (window.parent !== window) parent.postMessage({ type: 'thumb-saved', card: r.card, pages: r.pages, og: r.og }, '*');
}
$('save').onclick = async () => {
  if ($('shape').value === 'thumb') return saveThumb();
  if (!cur) { return toast('Pick a photo first'); }
  const rounded = {};
  BP.forEach(b => { rounded[b.key] = Math.round(crops[b.key] * 10) / 10; });
  // objectPositionY is kept so anything reading the old schema still works; it
  // is the desktop value, and only when the desktop axis is the vertical one
  const legacy = geomFor(0).axis === 'y' ? rounded.desktop : 50;
  const body = { country, path: cur.path, name: cur.name,
                 crops: rounded,
                 objectPosition: { desktop: posFor(0), laptop: posFor(1), phone: posFor(2) },
                 objectPositionY: legacy,
                 scrim: scrim,
                 angle: angle,
                 title: $('title').value,
                 article: ARTICLE ? ARTICLE.rel : '',
                 slug: ARTICLE ? (ARTICLE.heroSlug || (ARTICLE.rel || '').split('/').pop().replace(/\.html$/, '')) : '' };
  const r = await (await fetch('/save', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
  if (ARTICLE) PICKS[ARTICLE.rel] = body;
  if (r && r.built !== undefined) {
    if (!r.built) { toast('Pick saved, but the hero files did not build: ' + (r.log || '').slice(-120)); }
    else if (window.parent !== window) {
      parent.postMessage({ type: 'heroes-saved', slug: r.slug, web: r.web, country: country, scrim: scrim,
        positions: { desktop: posFor(0), laptop: posFor(1), phone: posFor(2) }, angle: angle }, '*');
      toast('Hero built. The article editor has the new hero; save the article to keep it.');
    }
  }
  if (!(r && r.built)) toast(ARTICLE ? 'Pick saved.' : 'Pick saved for ' + country + '.');   // a built hero keeps its own message
  const keep = $('album').value;
  await loadAlbums.reload(keep);
};

/* re-read the picks so the tick and the tally update without losing your place */
loadAlbums.reload = async keep => {
  const rows = await (await fetch('/albums')).json();
  const done = rows.filter(x => x.picked).length;
  $('tally').innerHTML = `<b>${done}</b> of ${rows.length} picked`;
  [...$('album').options].forEach(o => {
    const row = rows.find(x => x.folder === o.value);
    if (!row) { return; }
    o.dataset.picked = row.picked ? 1 : 0;
    o.dataset.pick = row.pickPath;
    o.dataset.crops = JSON.stringify(row.pickCrops);
    o.dataset.pickTitle = row.pickTitle || '';
    o.dataset.scrim = row.pickScrim;
    o.dataset.stars = (row.stars || []).join('|');
    // The ALBUM name is the label, because that is what you are choosing
    // between: the trips you took. The country is where the pick publishes to,
    // which matters on save, not while you are picking. Listing by country hid
    // "Patagonia (2024)" behind Chile, "Bali" behind Indonesia and
    // "Turkey 2.0" behind Türkiye.
    var album = row.folder || row.country;
    var same = album.replace(/\s*\(\d{4}\)\s*$/, '').trim().toLowerCase()
               === (row.country || '').toLowerCase();
    o.textContent = (row.picked ? '✓ ' : '· ') + album +
                    (same ? '' : '  → ' + row.country);
  });
  $('album').value = keep;
  document.querySelectorAll('.strip .t.saved').forEach(t => t.classList.remove('saved'));
  const t = document.querySelector('.strip .t.on');
  if (t) { t.classList.add('saved'); }
};

function toast(t) {
  const el = $('toast');
  el.textContent = t; el.classList.add('on');
  setTimeout(() => el.classList.remove('on'), 2600);
}

/* the three rules the site actually has, and the widest one judged at this
   monitor's width rather than a nominal one: any screen over 1400 uses it */
(function () {
  const mine = Math.max(320, (window.screen && screen.width ? screen.width : 1905) - 15);
  if (mine > 1400) { BP[0].vw = mine; }
  fillVW();
})();
document.documentElement.style.setProperty('--shape', heroRatio());
siteFrame();
try { setFull(localStorage.getItem('hp-full') === '1', true); } catch (e) {}
setScrim(100);
loadAlbums();

// the picked / shown counts ride up to the suite bar when embedded, the way the
// Photo Library shows its album totals there
if (document.documentElement.classList.contains('embed') && window.parent !== window) {
  const send = () => parent.postMessage({ type: 'heroes-stat',
    tally: $('tally').textContent, count: $('count').textContent }, '*');
  new MutationObserver(send).observe($('tally'), { childList: true, subtree: true, characterData: true });
  new MutationObserver(send).observe($('count'), { childList: true, subtree: true, characterData: true });
  send();
  parent.postMessage({ type: 'heroes-ready' }, '*');     // ask for the open article
}
</script>
<script>
/* ============== ALBUM FLAGS + THE FLAGGED DROPDOWN (2026-09-27) ============== */
(function () {
  // album name -> ISO code for Images/web/flags (a trip is named for where it went, not the country)
  const ALIAS = { 'bali': 'id', 'patagonia': 'cl', 'cologne': 'de', 'munich': 'de', 'turkey': 'tr', 'türkiye': 'tr',
    'amsterdam': 'nl', 'ibiza': 'es', 'normandy': 'fr', 'paris': 'fr', 'french riviera': 'fr', 'copenhagen': 'dk' };
  // albums that get an emoji instead of a flag
  const EMOJI = { 'tomorrowland': '🛸' };
  const ISO = { albania:'al', argentina:'ar', armenia:'am', australia:'au', bosnia:'ba', brazil:'br', colombia:'co', egypt:'eg',
    'el salvador':'sv', georgia:'ge', greece:'gr', guatemala:'gt', india:'in', israel:'il', italy:'it', japan:'jp', jordan:'jo',
    kosovo:'xk', mexico:'mx', 'new zealand':'nz', nicaragua:'ni', 'north macedonia':'mk', peru:'pe', philippines:'ph',
    serbia:'rs', tanzania:'tz', vietnam:'vn', croatia:'hr', montenegro:'me', spain:'es', portugal:'pt', france:'fr', thailand:'th',
    morocco:'ma', cuba:'cu', chile:'cl', indonesia:'id', germany:'de', netherlands:'nl', belgium:'be', uk:'gb',
    estonia:'ee', finland:'fi', hungary:'hu', latvia:'lv', slovenia:'si', sweden:'se', switzerland:'ch' };
  // How an album is SHOWN. iCloud turns characters Windows can't keep in a
  // folder name into "_", so the folder reads "Amsterdam _ Utrecht (2023)";
  // the folder name stays the album's identity everywhere else.
  const LABEL = { 'Amsterdam _ Utrecht (2023)': 'Amsterdam & Utrecht (2023)',
                  'Normandy + Paris (2023)': 'Normandy & Paris (2023)' };
  const labelOf = name => LABEL[name] || name;
  const albumKey = name => String(labelOf(name) || '').toLowerCase().replace(/\s*\(\d{4}\).*$/, '').replace(/\s+\d+(\.\d+)?$/, '').split(/\s*[&+,]\s*/)[0].trim();
  window.albumEmoji = name => EMOJI[albumKey(name)] || '';
  window.albumFlag = function (name) {
    const k = String(labelOf(name) || '').toLowerCase().replace(/\s*\(\d{4}\).*$/, '').replace(/\s+\d+(\.\d+)?$/, '').split(/\s*[&+,]\s*/)[0].trim();
    const code = ALIAS[k] || ISO[k];
    return code ? 'http://127.0.0.1:5003/site/Images/web/flags/' + code + '.png' : '';
  };
  const flagImg = (name, cls) => { const e = window.albumEmoji(name); if (e) return `<span class="${cls || 'afl'} emo" aria-hidden="true">${e}</span>`;
    const u = albumFlag(name); return u ? `<img class="${cls || 'afl'}" src="${u}" alt="" width="18" height="13">` : `<span class="${cls || 'afl'} none"></span>`; };
  window.flagImg = flagImg;
  // a native <select> dressed as the flagged dropdown; the select keeps its value and change event
  window.flagSelect = function (sel) {
    if (!sel || sel.__flagged) return; sel.__flagged = true;
    const box = document.createElement('div'); box.className = 'fsel'; box.dataset.for = sel.id;
    box.innerHTML = '<button type="button" class="fsel-btn" aria-haspopup="listbox"></button><div class="fsel-list" role="listbox" hidden></div>';
    sel.after(box); sel.classList.add('fsel-native');
    const btn = box.firstChild, list = box.lastChild;
    const label = o => o ? o.textContent.replace(/\s+/g, ' ').trim() : '';
    const paint = () => { const o = sel.selectedOptions[0]; btn.innerHTML = flagImg(o && o.value) + `<span class="fsel-t">${label(o) || '—'}</span><i class="fsel-chev"></i>`; };
    const open = on => {
      list.hidden = !on; box.classList.toggle('open', on);
      if (!on) return;
      list.innerHTML = [...sel.options].filter(o => o.value).map(o =>
        `<div class="fsel-o${o.selected ? ' on' : ''}" role="option" data-v="${o.value.replace(/"/g, '&quot;')}">${flagImg(o.value)}<span>${label(o)}</span></div>`).join('');
      const on_ = list.querySelector('.on'); if (on_) on_.scrollIntoView({ block: 'nearest' });
    };
    btn.onclick = e => { e.stopPropagation(); open(list.hidden); };
    list.onclick = e => { const o = e.target.closest('.fsel-o'); if (!o) return; sel.value = o.dataset.v; open(false); paint(); sel.dispatchEvent(new Event('change', { bubbles: true })); };
    document.addEventListener('click', e => { if (!box.contains(e.target)) open(false); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !list.hidden) { open(false); btn.focus(); } });
    new MutationObserver(paint).observe(sel, { childList: true, subtree: true, attributes: true });
    sel.addEventListener('change', paint);
    const setv = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    Object.defineProperty(sel, 'value', { get() { return setv.get.call(this); }, set(v) { setv.set.call(this, v); paint(); } });
    paint();
  };
  flagSelect(document.getElementById('album'));
})();

</script>
</body></html>
""".replace("%TITLES%", json.dumps(TITLES, ensure_ascii=False)
          ).replace("%SHAPES%", json.dumps(SHAPES)
          ).replace("%BREAKPOINTS%", json.dumps(BREAKPOINTS))


def warm_all():
    """Pre-bake every album's thumbnails and focus scores, then exit. Run it
    once (or after adding photos) and the picker never decodes interactively:
        python tools/hero_picker.py --warm-all
    """
    import sys as _s
    dirs = [p for p in sorted(BACKUP.iterdir())
            if p.is_dir() and not p.name.startswith("_")]
    done = 0
    for d in dirs:
        files = [f for f in sorted(d.rglob("*"))
                 if f.suffix.lower() in EXT and f.is_file()
                 and not is_stray_thumb(f.name)]
        fresh = [f for f in files if not cache_path(f, THUMB_W, False, 1).exists()]
        print(f"  {d.name:34s} {len(files):4d} photos, {len(fresh):4d} to build",
              flush=True)
        for _ in POOL.map(lambda f: (focus_score(f), None)[1], files):
            done += 1
    print(f"  warmed {done} photos across {len(dirs)} albums")


if __name__ == "__main__":
    import sys as _sys
    if "--warm-all" in _sys.argv:
        warm_all()
    else:
        import threading as _th
        _th.Thread(target=prescan_all, daemon=True, name="prescan").start()
        app.run(host="127.0.0.1", port=5004, debug=False, threaded=True)
