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
POOL = ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 6)))

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
        if not p.is_dir() or p.name.startswith("_"):
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
                    "stars": starred.get(country, [])})
    return out


def cache_path(src, w, crop, ratio):
    key = (f"{src}|{src.stat().st_mtime_ns}|{w}|"
           f"{('c%.4f' % ratio) if crop else 'f'}")
    return CACHE / (hashlib.md5(key.encode()).hexdigest() + ".jpg")


def build(src, w, crop, ratio=None):
    """Render one cached variant. `crop` cuts the same shape the hero uses."""
    ratio = ratio or HERO_RATIO
    cp = cache_path(src, w, crop, ratio)
    if cp.exists():
        return cp
    im = Image.open(src)
    try:                                  # JPEG decodes at 1/2, 1/4, 1/8 for free
        im.draft("RGB", (w * 2, int(w * 2 / ratio)))
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    icc = im.info.get("icc_profile")      # Display P3, must survive the convert
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
    """Cut every thumbnail and score every frame, off the request thread."""
    def one(r):
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
    files = [f for f in sorted(folder.rglob("*"))
             if f.suffix.lower() in EXT and f.is_file()
             and not is_stray_thumb(f.name)]
    rows = [r for r in POOL.map(measure, files) if r]
    rows.sort(key=lambda r: r["name"])
    _PHOTO_CACHE[folder.name] = (stamp, rows)
    idx.write_text(json.dumps({"stamp": stamp, "rows": rows}), encoding="utf-8")
    warm(folder.name, rows)
    return jsonify(rows)


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
    src = (BACKUP / request.args["p"]).resolve()
    assert BACKUP in src.parents, "outside the backup"
    # 4000 covers a full-bleed hero on a 2x monitor (the site itself never
    # serves more); the strip and the plain stage ask for far less
    w = min(int(request.args.get("w", 480)), 4000)
    crop = request.args.get("crop") == "1"
    ratio = shape_ratio(request.args.get("shape", "home"),
                        request.args.get("vw", DEFAULT_VW))
    r = send_file(build(src, w, crop, ratio), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "public, max-age=604800"
    return r


@app.post("/save")
def save():
    picks = read_picks()
    body = dict(request.json)
    body["path"] = _posix(body.get("path", ""))
    picks[body["country"]] = body
    PICKS.write_text(json.dumps(picks, indent=1, ensure_ascii=False), encoding="utf-8")
    return jsonify(ok=True, saved=str(PICKS))


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
<title>Hero picker</title>
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
  .strip .t.star .fav { color:#F2C124; }
  .strip .fav { position:absolute; left:6px; top:4px; z-index:3; border:0;
    background:transparent; padding:2px 5px; font-size:15px; line-height:1;
    cursor:pointer; color:rgba(255,255,255,.55);
    text-shadow:0 1px 3px rgba(0,0,0,.75); }
  .strip .fav:hover { color:#F2C124; }
  .strip .t.saved::after { content:'SAVED HERO'; position:absolute; left:0; top:0;
    background:var(--terra); color:#fff; font-size:8.5px; letter-spacing:.1em;
    padding:2px 6px; }
  /* the strip cuts the same shape the chosen hero does, so the crop is no
     surprise when you pick */
  .strip img { width:100%; display:block; aspect-ratio:var(--shape,2.118);
    object-fit:cover; }
  .strip .n { position:absolute; left:6px; bottom:6px; font-size:9px; letter-spacing:.06em;
    color:#fff; text-shadow:0 1px 2px rgba(0,0,0,.7); }
  .flag { position:absolute; top:6px; right:6px; font-size:8.5px; color:#fff;
    padding:2px 6px; letter-spacing:.08em; }
  .flag.low { background:var(--warn); }
  .flag.fair { background:var(--amber); }
  .flag.blur { background:var(--warn); top:26px; }
  .hint { font-size:11.5px; color:rgba(28,40,33,.5); margin:8px 0 0; }
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
  .embed select, .embed input[type=text] { font-size:.74rem; padding:.36rem .55rem;
    border:1px solid rgba(28,40,33,.14); border-radius:3px; color:#1C2821; }
  .embed select:focus, .embed input[type=text]:focus { outline:none; border-color:#2D6B50;
    box-shadow:0 0 0 2px rgba(45,107,80,.14); }
  .embed button { font-family:'Hanken Grotesk',sans-serif; font-size:.6rem; font-weight:600;
    letter-spacing:.1em; padding:.48rem .85rem; border-radius:3px; border:1px solid
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
  <span class="logo">hero picker</span>
  <select id="album"></select>
  <select id="filter">
    <option value="all">Every photo</option>
    <option value="ok" selected>Hide low res</option>
    <option value="best">Hero-ready only</option>
    <option value="star">Starred only</option>
  </select>
  <span class="tally" id="tally"></span>
  <span class="tally" id="count"></span>
  <input type="text" id="title" placeholder="Article title shown on the hero">
  <select id="shape" title="Which hero on the site this photo is destined for">
    <option value="home" selected>Home hero</option>
    <option value="article">Article banner</option>
    <option value="wide">Plain 16:9</option>
  </select>
  <select id="vw" title="Which of the site's three hero rules you are setting.
Each keeps its own crop, because the hero is a fixed height and changes shape
with the window.">
  </select>
  <span class="scrim-ctl" title="How heavy the overlay sits on this photo">
    Scrim <input type="range" id="scrim" min="0" max="160" value="100"><b id="scrimv">100%</b>
  </span>
  <button class="q" id="bleed" title="Hide the strip and show the hero edge to edge, the size the site cuts it (F)">Full bleed</button>
  <button class="q" id="reset">Center crop</button>
  <button id="save">Save pick</button>
</header>
<div class="main">
  <div class="stage">
    <div class="hero" id="hero">
      <img id="pic" draggable="false" alt="">
      <div class="veil"></div>
      <div class="navband" id="navband"><span class="nb-logo">getawayguide</span>
        <span class="nb-links" id="nb-links"><span>Home</span><span>Destinations</span><span>Resources</span><span>About Me</span></span>
        <span class="nb-burger" id="nb-burger" hidden><i></i><i></i><i></i></span></div>
      <div class="cpy">
        <div class="eb" id="eb">Country &middot; Field notes</div>
        <h1 id="h1">Pick a photo from the strip</h1>
        <span class="cta">Read the guide &rarr;</span>
      </div>
    </div>
    <div class="meta" id="meta"></div>
    <p class="hint" id="hint">Drag the photo to set the crop.</p>
    <pre class="cropcss" id="cropcss"></pre>
    <p class="key">
      <span><i class="flag low" style="position:static">LOW RES</i> under 1920px, blurry at any width</span>
      <span><i class="flag fair" style="position:static">1080p-ish</i> fine at 1x, soft at 2x</span>
      <span><i class="flag blur" style="position:static">SOFT FOCUS</i> less fine detail than the rest of the album</span>
    </p>
  </div>
  <div class="strip" id="strip">Loading&hellip;</div>
</div>
<div class="toast" id="toast"></div>
<script>
const TITLES = %TITLES%;
const SHAPES = %SHAPES%;
let cur = null, country = '', album = '', qTimer = null;
const BP = %BREAKPOINTS%;
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
             data-stars="${(r.stars || []).join('|')}">${r.picked ? '✓ ' : '· '}${albumLabel(r)}</option>`
  ).join('');
  const done = rows.filter(r => r.picked).length;
  $('tally').innerHTML = `<b>${done}</b> of ${rows.length} picked`;
  $('album').onchange = loadStrip;
  await loadStrip();
}

async function loadStrip() {
  const sel = $('album').selectedOptions[0];
  album = sel.value;
  country = sel.dataset.country;
  $('eb').textContent = country + ' · Field notes';
  // B9: a title edited at save time comes back next visit instead of the default
  $('title').value = sel.dataset.pickTitle || TITLES[country] || (country + ' Travel Guide');
  $('h1').textContent = $('title').value;
  $('strip').textContent = 'Loading…';
  STARS = (sel.dataset.stars || '').split('|').filter(Boolean);
  ALL = await (await fetch('/photos?album=' + encodeURIComponent(album))).json();
  render(true);          // a fresh album restores whatever was saved for it
  pollQuality();
}

/* the strip is for choosing, so by default it hides what could never be a hero */
function render(restore) {
  const sel = $('album').selectedOptions[0];
  const keep = cur;              // the innerHTML rebuild below drops .on
  const mode = $('filter').value;
  const shape = $('shape').value;
  const saved0 = sel.dataset.pick;
  // whatever is already saved stays visible, even when the filter would hide it
  const rows = ALL.filter(r => r.path === saved0 || (
      mode === 'all'  ? true
    : mode === 'star' ? STARS.includes(r.path)
    : mode === 'best' ? r.tier === 'good'
    :                   r.tier !== 'low'));
  const hidden = ALL.length - rows.length;
  const saved = sel.dataset.pick;
  $('count').textContent = hidden
    ? `${rows.length} shown, ${hidden} hidden` : `${rows.length} photos`;
  $('strip').innerHTML = rows.map(r => `
    <div class="t${r.path === saved ? ' saved' : ''}${STARS.includes(r.path) ? ' star' : ''}"
         data-p="${r.path}" data-w="${r.heroW}" data-name="${r.name}" data-tier="${r.tier}">
      <button class="fav" title="Shortlist this photo">${STARS.includes(r.path) ? '★' : '☆'}</button>
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
  $('count').textContent += STARS.length ? ` · ${STARS.length} starred` : '';
  if (restore && saved) {
    const t = document.querySelector(`.strip .t[data-p="${CSS.escape(saved)}"]`);
    if (t) { pick(t); crops = readCrops(sel); applyCrop();
             setScrim(+sel.dataset.scrim || 100);
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
function geomFor(i) {
  const img = $('pic');
  if (!img.naturalWidth) { return { axis: 'y', travel: 0, shown: 1 }; }
  const b = boxOf(i);
  const src = img.naturalWidth / img.naturalHeight;
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
  if (shape === 'wide') { set('auto', '16 / 9', '0'); }
  else if (vw <= 768) { set((shape === 'article' ? 420 : 470) + 'px', 'auto', '0'); }
  else if (shape === 'article') { set('auto', '1440 / 560', '420px'); }
  else { set('680px', 'auto', '0'); }
  // the band is a share of the frame, so it is sized after the frame is
  requestAnimationFrame(() => navBand($('hero').getBoundingClientRect()));
}

function applyShape() {
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
  const w = full ? wantW() : 1600;
  if (!force && w <= picW) { return; }
  picW = w;
  const img = $('pic');
  img.onload = () => {              // the axis is not known until the photo is
    applyCrop();
    if (full && wantW() > picW) { loadPic(); }   // the first guess used a stand-in ratio
  };
  img.src = '/img?w=' + w + '&p=' + encodeURIComponent(cur.path);
}
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

function pick(t) {
  document.querySelectorAll('.strip .t.on').forEach(x => x.classList.remove('on'));
  t.classList.add('on');
  cur = { path: t.dataset.p, name: t.dataset.name, w: +t.dataset.w,
          tier: t.dataset.tier };
  crops = { desktop: 50, laptop: 50, phone: 50 };
  picW = 0;
  loadPic(true);
  applyCrop();
  const q = cur.tier === 'good'
    ? '<span class="good">sharp at 2× on a 1440 hero</span>'
    : cur.tier === 'fair'
      ? '<span class="mid">fine at 1×, soft at 2×</span>'
      : '<span class="bad">too low resolution for a hero</span>';
  const f = t.dataset.focus;
  const soft = f && t.dataset.cut && +f < +t.dataset.cut
    ? ' <span class="bad">soft focus</span>' : '';
  $('meta').innerHTML = `<span><b>${cur.name}</b></span>` +
    `<span>${cur.w}px wide</span>` + q + soft + `<span id="oyv">crop 50%</span>`
    + `<span id="frv"></span>`;
}

async function toggleStar(t) {
  const r = await (await fetch('/star', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ country, path: t.dataset.p }) })).json();
  t.classList.toggle('star', r.starred);
  t.querySelector('.fav').textContent = r.starred ? '★' : '☆';
  STARS = r.starred ? STARS.concat([t.dataset.p])
                    : STARS.filter(x => x !== t.dataset.p);
  const sel = $('album').selectedOptions[0];
  sel.dataset.stars = STARS.join('|');
  if ($('filter').value === 'star' && !r.starred) { render(); }
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

$('title').addEventListener('input', () => { $('h1').textContent = $('title').value; });
$('reset').onclick = () => { crops[BP[bpi].key] = 50; applyCrop(); };

function slug(x) {
  return (x || 'hero').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

/* the value goes in whichever slot moves; the pinned one stays at 50% because
   putting anything else there would be a number that does nothing */
function posFor(i) {
  const v = Math.round(crops[BP[i].key] * 10) / 10;
  return geomFor(i).axis === 'x' ? v + '% 50%' : '50% ' + v + '%';
}

function applyCrop() {
  const g = geomFor(bpi);
  const key = BP[bpi].key;
  crops[key] = Math.max(0, Math.min(100, crops[key]));
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
      + ($('shape').value === 'wide' ? '' : ' The top '
         + (BP[bpi].vw <= 768 ? NAV_H_PHONE : NAV_H)
         + 'px sits under the site’s nav, so nothing you put there is seen.')
      + (full ? ' <b>← →</b> step through the strip, <b>F</b> brings it back.' : '')
    : 'Drag the photo to set the crop.';

  const cls = '.hero .slide img.hp-' + slug(country);
  const line = i => cls + ' { object-position:' + posFor(i).replace(
    /([\d.]+%)(?!\s*})/, m => m) + '; }';
  const mark = i => {
    const g2 = geomFor(i), v2 = Math.round(crops[BP[i].key] * 10) / 10;
    const val = g2.axis === 'x'
      ? '<b>' + v2 + '%</b> <i>50%</i>' : '<i>50%</i> <b>' + v2 + '%</b>';
    return cls + ' { object-position:' + val + '; }';
  };
  $('cropcss').innerHTML =
    mark(0) + '\n\n@media ' + BP[1].media + ' {\n  ' + mark(1) + '\n}\n\n'
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
  const on = $('shape').value !== 'wide';
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
  const src = img.naturalWidth / img.naturalHeight;
  // travel measured on the RENDERED frame, on whichever axis is free. The old
  // version always used height and clamped the spare to 1, so a photo wider
  // than its box swung the full range on a single pixel of movement.
  const spare = drag.axis === 'x'
    ? box.height * src - box.width
    : box.width / src - box.height;
  if (spare <= 0.5) { return; }
  const now = drag.axis === 'x' ? e.clientX : e.clientY;
  crops[BP[bpi].key] = drag.from - (now - drag.at) / spare * 100;
  applyCrop();
});
['pointerup', 'pointercancel'].forEach(ev => $('hero').addEventListener(ev, () => {
  drag = null; $('pic').classList.remove('dragging');
}));

$('save').onclick = async () => {
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
                 title: $('title').value };
  const r = await (await fetch('/save', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
  toast('Saved to ' + r.saved);
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
  $('vw').innerHTML = BP.map((b, i) =>
    `<option value="${i}">${b.label} ${b.vw}${b.media ? '  ' + b.media : ''}</option>`
  ).join('');
  $('vw').value = '0';
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
}
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
        app.run(host="127.0.0.1", port=5004, debug=False, threaded=True)
