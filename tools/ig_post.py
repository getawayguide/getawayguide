r"""
IG post maker: the photos of one country's articles, ready to pick, crop and post on Instagram.

HOW IT WORKS (three steps; Claude runs 1 and 3, Kevin does 2 in the artifact)

  1. Collect + browser copies
       python tools/ig_post.py "North Macedonia" --drafts
     Reads every ARTICLE page of the country (Drafts/Upcoming/<slug>/*.html with --drafts, else
     <slug>/*.html at the repo root; index.html, map pages and _scratch files are skipped), finds
     every body photo (a <picture> or bare <img> inside the article body; heroes, nav flags,
     Explore More cards and maps are not body photos), resolves each back to its archive ORIGINAL
     under Images/<Country>/..., de-duplicates, and writes
       .tmp/ig/<slug>/web/<id>.jpg   ~1280 px long edge, Display P3 -> sRGB (a real ImageCms
                                      conversion, sRGB profile embedded), JPEG q82, EXIF-upright
       .tmp/ig/<slug>/manifest.json  id, original, article, section, caption, alt, time, gps, size
     The originals are never touched.

     Then Claude publishes the artifact page (tools/ig_post_page.html is the template), uploads the
     web copies as its assets (Artifact tool, asset:true, <=25 files a call), and writes the catalog
     into the artifact's db:
       python tools/ig_post.py "North Macedonia" catalog --assets .tmp/ig/<slug>/assets.json
     where assets.json maps photo id -> asset id (from the upload results). That writes
     .tmp/ig/<slug>/catalog.json, which goes in with
       ArtifactData set  url=<artifact>  collection=catalog  doc_id=main  file_path=.tmp/ig/<slug>/catalog.json

  2. Kevin opens the artifact: shortlists, archives, drags photos into the 30-slot template (1-10 = the
     carousel, 11-30 = backlog), crops and
     tilts each one, writes a note under each slot, ranks caption ideas, asks Claude for variations,
     marks a caption final and presses "Ready to render". Everything lives in the artifact's db.

  3. Render (Claude, when the post says ready)
       ArtifactData list url=<artifact> collection=photos   out_dir=.tmp/ig/<slug>/state
       ArtifactData list url=<artifact> collection=post     out_dir=.tmp/ig/<slug>/state
       ArtifactData list url=<artifact> collection=captions out_dir=.tmp/ig/<slug>/state
       python tools/ig_post.py "North Macedonia" render --state .tmp/ig/<slug>/state
     Renders the ten slots in order from the FULL-RESOLUTION originals: 1080x1350 (4:5) or
     1080x1080 (1:1), tilt applied before the crop with zoom-to-cover (never an empty corner),
     P3 -> sRGB, one Lanczos/bicubic resample, JPEG q92 4:4:4, named 01-<id>.jpg ... into
       .tmp/ig/<slug>/out/            and a copy in
       C:\Users\kevin\Pictures\IG\<slug>\   (--copy-to changes it, --no-copy skips it)
     plus caption.txt (the final caption, then the hashtags) and notes.txt (each slot's note).
     `render --preview` draws the same crops at 540 px for a quick look (.tmp/ig/<slug>/preview/).

ITINERARY TEMPLATE MODE (2026-11-07): the same page has a "Free 10 | Itinerary template" switch. The template is a
branded carousel (cover, route map, a 4-photo slide per stop, call to action) plus a caption builder; its plan lives in
carousel/main + carousel/map and tools/ig_carousel.py seeds and renders it (Pinterest pins too). See that file.

DB SHAPE the page writes (what `render` reads)
  post/main        {status: "draft"|"ready", aspect: "4:5"|"1:1", finalCaption, hashtags, updatedAt}
  photos/<id>      {state: "pool"|"shortlist"|"archived"|"final", slot: 1..30|null, order: n,
                    note: "", crop: {aspect, cx, cy, w, rot}}
                    slots 1-10 are the carousel (rendered, in order); 11-30 are his backlog while trimming.
                    crop is on the EXIF-upright original: (cx, cy) = the crop center as a fraction
                    of the width / height, w = the crop width as a fraction of the width, rot = the
                    tilt in degrees (positive = clockwise on screen, CSS rotate()), the frame's
                    height follows the aspect. A photo with no doc is in the pool.
  captions/<id>    {text, stars 0-5, order, source: "me"|"claude", final: bool}
  catalog/main     {country, slug, items: [{id, asset, article, section, caption, alt, time, w, h}]}

Output on this box goes through sys.stdout.buffer (redirected stdout is cp1252 here).
"""

import argparse
import io
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime
from html import unescape
from pathlib import Path
from urllib.parse import unquote

from PIL import Image, ImageCms, ImageOps

Image.MAX_IMAGE_PIXELS = None

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "Images"
TMP = ROOT / ".tmp" / "ig"
PICTURES = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Pictures" / "IG"

SRGB = ImageCms.createProfile("sRGB")
SRGB_BYTES = ImageCms.ImageCmsProfile(SRGB).tobytes()

WEB_EDGE = 1280
WEB_Q = 82
OUT_W = 1080
OUT_Q = 92
ASPECTS = {"4:5": 5 / 4, "1:1": 1.0}
SKIP_WEB_FOLDERS = ("flags/", "continents/", "explore/", "city-maps/", "Index/", "dest-cards/", "Bio/")


def out(s=""):
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


# ------------------------------------------------------------------ country / pages

def slugify(name):
    s = name.strip().lower().replace("ü", "u").replace("ö", "o").replace("ä", "a")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def images_folder(country):
    """Images/<Country> for a name given either way ("North Macedonia", "north-macedonia", "Turkiye")."""
    want = slugify(country)
    for d in IMAGES.iterdir():
        if d.is_dir() and slugify(d.name) == want:
            return d
    raise SystemExit("no Images/ folder for %r" % country)


def article_pages(slug, drafts):
    folder = (ROOT / "Drafts" / "Upcoming" / slug) if drafts else (ROOT / slug)
    if not folder.is_dir():
        raise SystemExit("no article folder %s" % folder)
    pages = []
    for p in sorted(folder.glob("*.html")):
        n = p.name.lower()
        if n == "index.html" or n.startswith("_") or "map" in n:
            continue
        pages.append(p)
    return pages


def archive_original(ref, country_dir):
    """The archive original behind an article img ref (a web tier, a photo-server URL or the original itself).
    Same rules as photo_editor._archive_original; only ever resolves inside Images/."""
    rel = unquote(str(ref or "")).replace("\\", "/")
    rel = re.sub(r"^https?://127\.0\.0\.1:\d+/site/", "", rel)
    rel = re.sub(r"^(\.\./)+", "", rel)
    if rel.startswith("Images/"):
        rel = rel[len("Images/"):]
    if rel.startswith("web/"):
        rel = rel[len("web/"):]
    if not rel or ".." in rel.split("/") or rel.lower().endswith((".svg", ".png", ".gif")):
        return None
    if any(rel.startswith(x) for x in SKIP_WEB_FOLDERS):
        return None
    rel = re.sub(r"-(?:mob-)?[123]x(?=\.[^.]+$)", "", rel)
    cand = IMAGES / rel
    try:
        cand.resolve().relative_to(country_dir.resolve())
    except ValueError:
        return None  # another country's photo, or a hero that lives at Images/web/<Country>/hero-*.jpg
    folder, low = cand.parent, cand.stem.lower()
    if not folder.is_dir():
        return None
    for f in folder.iterdir():
        if f.is_file() and f.stem.lower() == low and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
            return f
    return None


def text_of(html):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", html))).strip()


TOKEN = re.compile(
    r"<picture\b.*?</picture>|<img\b[^>]*>|<h([123])\b[^>]*>(.*?)</h\1>|<div class=\"img-caption\"[^>]*>(.*?)</div>",
    re.S | re.I,
)


def body_html(html):
    """The article body: from .article-body (or the h1) to Explore More / the footer."""
    m = re.search(r'class="article-body"', html)
    start = m.start() if m else (re.search(r"<h1\b", html) or re.search(r"<body\b", html)).start()
    ends = [x.start() for x in (re.search(r'<section class="xm"', html), re.search(r"<footer\b", html)) if x]
    end = min(ends) if ends else len(html)
    return html[start:end]


def page_title(html, path):
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    t = text_of(m.group(1)) if m else path.stem
    return re.sub(r"\s*[-\u2014|]\s*getawayguide\s*$", "", t)


def collect(pages, country_dir):
    """[{original, article, article_title, section, caption, alt}] in page order, de-duplicated on the original."""
    found, seen = [], {}
    for page in pages:
        html = page.read_text(encoding="utf-8", errors="replace")
        body = body_html(html)
        title = page_title(html, page)
        section = ""
        pending = []  # photos waiting for the caption that follows their block
        for m in TOKEN.finditer(body):
            tok = m.group(0)
            if m.group(1):  # heading
                section = text_of(m.group(2))
                pending = []
                continue
            if m.group(3) is not None:  # caption
                cap = text_of(m.group(3))
                for item in pending:
                    if not item["caption"]:
                        item["caption"] = cap
                pending = []
                continue
            img = re.search(r"<img\b[^>]*>", tok, re.I)
            if not img:
                continue
            src = re.search(r'\ssrc="([^"]*)"', img.group(0))
            alt = re.search(r'\salt="([^"]*)"', img.group(0))
            orig = archive_original(src.group(1) if src else "", country_dir)
            if orig is None:
                continue
            key = str(orig).lower()
            if key in seen:
                continue
            item = {
                "original": orig,
                "article": page.stem,
                "article_title": title,
                "section": section,
                "caption": "",
                "alt": unescape(alt.group(1)) if alt else "",
            }
            seen[key] = item
            found.append(item)
            pending.append(item)
    return found


def photo_id(orig, country_dir, taken):
    rel = orig.relative_to(country_dir)
    base = "-".join(list(rel.parts[:-1]) + [orig.stem])
    pid = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-").lower()
    return pid if pid not in taken else pid + "-" + str(sum(1 for t in taken if t.startswith(pid)) + 1)


# ------------------------------------------------------------------ EXIF

def _ratio(v):
    try:
        return float(v)
    except Exception:
        return float(v[0]) / float(v[1])


def exif_info(im):
    """(capture time 'YYYY-MM-DD HH:MM:SS' or '', (lat, lon) or None)"""
    t, gps = "", None
    try:
        ex = im.getexif()
        raw = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        if raw:
            t = datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
        g = ex.get_ifd(0x8825)
        if g and 2 in g and 4 in g:
            lat = sum(_ratio(x) / 60 ** i for i, x in enumerate(g[2]))
            lon = sum(_ratio(x) / 60 ** i for i, x in enumerate(g[4]))
            if str(g.get(1, "N")).upper().startswith("S"):
                lat = -lat
            if str(g.get(3, "E")).upper().startswith("W"):
                lon = -lon
            gps = (round(lat, 5), round(lon, 5))
    except Exception:
        pass
    return t, gps


# ------------------------------------------------------------------ color

def open_upright(path):
    """(EXIF-upright image, its ICC bytes). The profile is read BEFORE any convert()."""
    im = Image.open(path)
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)
    return im, icc


def to_srgb(im, icc):
    """A real profile conversion into sRGB. Without a source profile the pixels are taken as sRGB already."""
    im = im.convert("RGB")
    if not icc:
        return im
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in (ImageCms.getProfileDescription(src) or "").lower():
            return im
        return ImageCms.profileToProfile(im, src, SRGB, outputMode="RGB")
    except Exception as e:
        out("  ! ICC conversion failed (%s); keeping the pixels and the source profile" % e)
        return im


def save_srgb(im, dst, q, subsampling=None):
    dst.parent.mkdir(parents=True, exist_ok=True)
    kw = dict(quality=q, optimize=True, icc_profile=SRGB_BYTES)
    if subsampling is not None:
        kw["subsampling"] = subsampling
    im.save(dst, "JPEG", **kw)


# ------------------------------------------------------------------ step 1: collect

def cmd_collect(args):
    country_dir = images_folder(args.country)
    slug = slugify(country_dir.name)
    pages = article_pages(slug, args.drafts)
    out("pages: " + ", ".join(p.name for p in pages))
    items = collect(pages, country_dir)
    out("%d body photos (de-duplicated)" % len(items))
    base = TMP / slug
    web = base / "web"
    web.mkdir(parents=True, exist_ok=True)
    manifest, taken = [], set()
    for it in items:
        orig = it["original"]
        im, icc = open_upright(orig)
        t, gps = exif_info(Image.open(orig))
        pid = photo_id(orig, country_dir, taken)
        taken.add(pid)
        dst = web / (pid + ".jpg")
        if not dst.exists() or dst.stat().st_mtime < orig.stat().st_mtime or args.force:
            w, h = im.size
            s = WEB_EDGE / max(w, h)
            small = im.resize((round(w * s), round(h * s)), Image.LANCZOS) if s < 1 else im
            save_srgb(to_srgb(small, icc), dst, WEB_Q)
        manifest.append({
            "id": pid,
            "original": str(orig.relative_to(ROOT)).replace("\\", "/"),
            "article": it["article"],
            "article_title": it["article_title"],
            "section": it["section"],
            "caption": it["caption"],
            "alt": it["alt"],
            "time": t,
            "gps": gps,
            "w": im.size[0],
            "h": im.size[1],
            "web": str(dst.relative_to(ROOT)).replace("\\", "/"),
            "web_bytes": dst.stat().st_size,
            "icc": bool(icc),
        })
        out("  %-40s %5dx%-5d %s | %s" % (pid, im.size[0], im.size[1], it["article"], (it["caption"] or it["alt"])[:60]))
    (base / "manifest.json").write_text(json.dumps({
        "country": country_dir.name, "slug": slug, "drafts": bool(args.drafts),
        "pages": [p.name for p in pages], "photos": manifest,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    total = sum(m["web_bytes"] for m in manifest)
    out("wrote %s (%d photos, web copies %.1f MB)" % (base / "manifest.json", len(manifest), total / 1e6))


# ------------------------------------------------------------------ step 1b: catalog for the db

def cmd_catalog(args):
    country_dir = images_folder(args.country)
    slug = slugify(country_dir.name)
    base = TMP / slug
    man = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    assets = json.loads(Path(args.assets).read_text(encoding="utf-8"))
    items, missing = [], []
    for p in man["photos"]:
        a = assets.get(p["id"])
        if not a:
            missing.append(p["id"])
            continue
        items.append({k: p[k] for k in ("id", "article", "article_title", "section", "caption", "alt", "time", "w", "h")}
                     | {"asset": a})
    cat = {"country": man["country"], "slug": slug, "pages": man["pages"], "items": items}
    (base / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    out("catalog.json: %d items, %d bytes%s" % (len(items), (base / "catalog.json").stat().st_size,
                                                 ("; MISSING assets for " + ", ".join(missing)) if missing else ""))


# ------------------------------------------------------------------ step 3: render

def _doc(obj):
    """The document body from an ArtifactData out_dir file (plain body, or wrapped {data, version})."""
    if isinstance(obj, dict) and isinstance(obj.get("data"), dict) and ("version" in obj or "id" in obj):
        return obj["data"]
    return obj


def load_state(path):
    """{photos: {id: doc}, post: doc, captions: [doc]} from an ArtifactData out_dir, or one JSON of that shape."""
    p = Path(path)
    if p.is_file():
        st = json.loads(p.read_text(encoding="utf-8"))
        st.setdefault("photos", {}); st.setdefault("post", {}); st.setdefault("captions", [])
        return st
    photos, captions, post = {}, [], {}
    for f in sorted((p / "photos").glob("*.json")) if (p / "photos").is_dir() else []:
        photos[f.stem] = _doc(json.loads(f.read_text(encoding="utf-8")))
    for f in sorted((p / "captions").glob("*.json")) if (p / "captions").is_dir() else []:
        captions.append(_doc(json.loads(f.read_text(encoding="utf-8"))))
    if (p / "post" / "main.json").is_file():
        post = _doc(json.loads((p / "post" / "main.json").read_text(encoding="utf-8")))
    return {"photos": photos, "post": post, "captions": captions}


def clamp_crop(crop, W, H, ratio):
    """The crop the page could have saved, clamped the same way the page clamps it: the tilted frame must lie
    inside the photo (zoom-to-cover). Returns (cx, cy, w, rot) with cx/cy in pixels and w the frame width in px."""
    rot = float(crop.get("rot") or 0)
    rot = max(-45, min(45, rot))
    th = math.radians(rot)
    c, s = abs(math.cos(th)), abs(math.sin(th))
    # largest frame width that fits at this tilt: half extents hx = fw/2*c + fh/2*s, hy = fw/2*s + fh/2*c
    fw_max = min(W / (c + ratio * s), H / (s + ratio * c))
    fw = min(float(crop.get("w") or 1) * W, fw_max)
    fw = max(fw, min(fw_max, 0.05 * W))
    fh = fw * ratio
    hx, hy = fw / 2 * c + fh / 2 * s, fw / 2 * s + fh / 2 * c
    cx = min(max(float(crop.get("cx", 0.5)) * W, hx), W - hx)
    cy = min(max(float(crop.get("cy", 0.5)) * H, hy), H - hy)
    return cx, cy, fw, rot


def render_one(orig, crop, aspect, out_w):
    """One slide from the full-resolution original. Output pixel -> source pixel is one affine map, so the
    photo is resampled once (after a Lanczos pre-reduce when the crop is much larger than the output)."""
    ratio = ASPECTS[aspect]
    im, icc = open_upright(orig)
    W, H = im.size
    cx, cy, fw, rot = clamp_crop(crop or {}, W, H, ratio)
    placed = (cx, cy, fw, rot)  # in the original's pixels, for the log
    out_h = round(out_w * ratio)
    k = fw / out_w  # source px per output px
    im = im.convert("RGB")
    if k > 1.6:
        # pre-reduce with a proper filter so the affine step (bicubic, no prefilter) does not alias
        f = k / 1.3
        im = im.resize((max(1, round(W / f)), max(1, round(H / f))), Image.LANCZOS)
        cx, cy, fw = cx / f, cy / f, fw / f
        k = fw / out_w
    th = math.radians(rot)
    cos, sin = math.cos(th), math.sin(th)
    # screen vector v -> image vector R(-rot) v (image rotated by +rot clockwise on screen, y down)
    a, b = k * cos, k * sin
    d, e = -k * sin, k * cos
    c0 = cx - a * (out_w / 2) - b * (out_h / 2)
    f0 = cy - d * (out_w / 2) - e * (out_h / 2)
    res = im.transform((out_w, out_h), Image.AFFINE, (a, b, c0, d, e, f0), resample=Image.BICUBIC)
    return to_srgb(res, icc), placed


def cmd_render(args):
    country_dir = images_folder(args.country)
    slug = slugify(country_dir.name)
    base = TMP / slug
    man = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    by_id = {p["id"]: p for p in man["photos"]}
    st = load_state(args.state)
    post = st["post"] or {}
    if post.get("status") != "ready" and not args.force:
        out("post/main.status is %r, not 'ready' (use --force to render anyway)" % post.get("status"))
    slots = []
    for pid, doc in st["photos"].items():
        if doc.get("state") == "final" and doc.get("slot") and 1 <= int(doc["slot"]) <= 10:
            slots.append((int(doc["slot"]), pid, doc))  # slots 11-30 are the backlog: never rendered
    slots.sort()
    if not slots:
        raise SystemExit("no photos in the final slots")
    size = 540 if args.preview else OUT_W
    out_dir = base / ("preview" if args.preview else "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*"):
        old.unlink()
    post_aspect = post.get("aspect") if post.get("aspect") in ASPECTS else "4:5"
    notes = []
    for n, pid, doc in slots:
        p = by_id.get(pid)
        if not p:
            out("  ! %s is not in the manifest, skipped" % pid)
            continue
        crop = doc.get("crop") or {}
        aspect = crop.get("aspect") if crop.get("aspect") in ASPECTS else post_aspect
        img, (cx, cy, fw, rot) = render_one(ROOT / p["original"], crop, aspect, size)
        dst = out_dir / ("%02d-%s.jpg" % (n, pid))
        save_srgb(img, dst, OUT_Q if not args.preview else 86, subsampling=0 if not args.preview else None)
        out("  %02d %-36s %s %dx%d  crop cx=%.0f cy=%.0f w=%.0f rot=%+.1f  %s" % (
            n, pid, aspect, img.size[0], img.size[1], cx, cy, fw, rot, p["original"]))
        notes.append("%02d %s: %s" % (n, pid, (doc.get("note") or "").strip()))
    cap = (post.get("finalCaption") or "").strip()
    if not cap:
        fin = [c for c in st["captions"] if c.get("final")]
        cap = (fin[0].get("text") if fin else "").strip()
    tags = (post.get("hashtags") or "").strip()
    (out_dir / "caption.txt").write_text(cap + ("\n\n" + tags if tags else "") + "\n", encoding="utf-8")
    (out_dir / "notes.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    out("caption: %s" % (cap[:100] or "(none marked final)"))
    if not args.preview and not args.no_copy:
        dest = Path(args.copy_to) if args.copy_to else PICTURES / slug
        dest.mkdir(parents=True, exist_ok=True)
        for f in out_dir.iterdir():
            shutil.copy2(f, dest / f.name)
        out("copied to %s" % dest)
    out("rendered %d slides into %s" % (len(slots), out_dir))


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("country")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("collect", help="(default) find the photos and write the web copies + manifest")
    c.add_argument("--drafts", action="store_true")
    c.add_argument("--force", action="store_true", help="rewrite web copies that already exist")
    k = sub.add_parser("catalog", help="catalog.json for the artifact db from manifest + asset ids")
    k.add_argument("--assets", required=True, help="JSON {photo id: asset id}")
    r = sub.add_parser("render", help="render the final slots from the originals")
    r.add_argument("--state", required=True, help="ArtifactData out_dir (photos/, post/, captions/) or one JSON")
    r.add_argument("--preview", action="store_true", help="540 px proofs instead of the 1080 px files")
    r.add_argument("--force", action="store_true", help="render even if the post is not marked ready")
    r.add_argument("--copy-to", help="folder for the copies (default %s\\<slug>)" % PICTURES)
    r.add_argument("--no-copy", action="store_true")
    # "collect" is the default subcommand: `ig_post.py "North Macedonia" --drafts`
    argv = sys.argv[1:]
    if len(argv) >= 1 and (len(argv) == 1 or argv[1] not in ("collect", "catalog", "render")):
        argv = [argv[0], "collect"] + argv[1:]
    args = ap.parse_args(argv)
    {"collect": cmd_collect, "catalog": cmd_catalog, "render": cmd_render}[args.cmd](args)


if __name__ == "__main__":
    main()
