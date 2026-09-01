#!/usr/bin/env python3
"""
Photo editor — browse your photo library in a sidebar, drag photos straight
into an article, and adjust the crop by dragging the image inside its frame.

Run:
    python tools/photo_editor.py
Then open http://localhost:5003

What it does:
  - Left sidebar browses one or more photo source folders (Downloads, OneDrive
    Pictures, and the iCloud Photos folder automatically when iCloud for
    Windows is installed). HEIC/HEIF iPhone files are supported.
  - Pick an article (live pages + Drafts), and the page renders exactly as it
    will look. Drag a photo into any gap between blocks:
        landscape photo -> a full-width  <div class="img-landscape"> block
        portrait  photo -> a two-up      <div class="img-pair"> block
                           (drop a second portrait onto the empty half)
  - The photo file is copied into Images/<Country>/<City>/ (the archive of
    originals; HEIC sources are converted to full-quality JPEG with the ICC
    profile and EXIF preserved — the file in your library is never touched).
  - Click any body image and drag it inside its frame to adjust the crop
    (writes object-position — non-destructive, the pixels are never cropped).
  - Blocks can be moved up/down, removed, and pair photos swapped.
  - "Run pipeline" runs the 5 compression tools (recompress_desktop,
    add_picture_mobile, gen_mobile_webp, fix_img_perf, fix_case) for live
    pages. Draft pages skip this: the publish protocol runs it later, and the
    local preview reads the originals directly.

The HTML file is edited by splicing at exact source offsets (html.parser with
a line-offset table), so everything outside the touched block stays
byte-identical.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
from html.parser import HTMLParser
from urllib.parse import unquote
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_OK = True
except ImportError:
    HEIC_OK = False

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "Images"
THUMBS = ROOT / ".tmp" / "photo_editor" / "thumbs"
THUMBS.mkdir(parents=True, exist_ok=True)
SRC_CFG = ROOT / "tools" / "photo_editor_sources.json"

IMG_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
VOID_TAGS = {"img", "br", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "track"}
# gen_mobile_jpg runs between the wrap and the webp step: add_picture_mobile
# leaves the <img> fallback pointing at the full-size original, and
# gen_mobile_webp only repoints images that already have -mob- variants.
PIPELINE = ["recompress_desktop.py", "add_picture_mobile.py", "gen_mobile_jpg.py",
            "gen_mobile_webp.py", "fix_img_perf.py", "fix_case.py"]

app = Flask(__name__)


@app.after_request
def cors(resp):
    """editor.html runs on a different origin (file:// or another local port)
    and its photo sidebar calls this API directly."""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


# ---------------------------------------------------------------- photo sources
def default_sources():
    cands = [
        Path.home() / "Pictures" / "iCloud Photos" / "Photos",
        Path.home() / "Pictures" / "iCloud Photos",
        Path.home() / "iCloudPhotos" / "Photos",
        Path.home() / "Downloads",
        Path.home() / "OneDrive" / "Pictures",
        IMAGES,
    ]
    out, seen = [], set()
    for p in cands:
        if p.is_dir() and str(p).lower() not in seen:
            # skip the generic parent when the /Photos child exists
            if p.name == "iCloud Photos" and (p / "Photos").is_dir():
                continue
            seen.add(str(p).lower())
            out.append({"label": ("Images/ (repo archive)" if p == IMAGES else
                                  "iCloud Photos" if "icloud" in str(p).lower() else p.name),
                        "path": str(p)})
    return out


def load_sources():
    if SRC_CFG.exists():
        srcs = json.loads(SRC_CFG.read_text(encoding="utf-8"))
    else:
        srcs = default_sources()
        SRC_CFG.write_text(json.dumps(srcs, indent=2), encoding="utf-8")
    # Folders that may appear AFTER the config was first written (iCloud gets
    # installed, or Shared Albums gets switched on) are picked up live. The
    # Shared root is added whole so each shared album shows up as a folder —
    # that is how the phone's per-country albums reach the sidebar.
    for c in (Path.home() / "iCloudPhotos" / "Shared",
              Path.home() / "Pictures" / "iCloud Photos" / "Shared",
              Path.home() / "iCloudPhotos" / "Photos",
              Path.home() / "Pictures" / "iCloud Photos" / "Photos",
              Path.home() / "Pictures" / "iCloud Photos"):
        if c.is_dir() and not any(Path(s["path"]) == c for s in srcs):
            if c.name == "iCloud Photos" and (c / "Photos").is_dir():
                continue
            # plain ASCII label: this gets printed to a cp1252 console at startup
            label = "Shared Albums" if c.name == "Shared" else "iCloud Photos"
            srcs.insert(0, {"label": label, "path": str(c)})
            SRC_CFG.write_text(json.dumps(srcs, indent=2), encoding="utf-8")
    return [s for s in srcs if Path(s["path"]).is_dir()]


SOURCES = load_sources()


# ------------------------------------------------- full-resolution originals
# A Shared Album folder holds 2048px copies (Apple downscales them), which is
# below what the site's desktop tier wants. The full-res original of the same
# photo is in the main iCloud library under an unrelated hash/IMG name, but
# BOTH carry the same capture timestamp — so we match on that and swap in the
# original whenever we are about to produce final pixels (erase / import).
# The index is built for free by tools/index_originals.ps1 (no downloads).
ORIG_INDEX = ROOT / ".tmp" / "photo_editor" / "originals_index.json"
_orig = {"loaded": 0, "library": None, "byTime": {}}


def originals_index():
    try:
        m = ORIG_INDEX.stat().st_mtime
    except OSError:
        return _orig
    if _orig["loaded"] != m:
        try:
            d = json.loads(ORIG_INDEX.read_text(encoding="utf-8-sig"))
            by = {}
            for k, v in (d.get("byTime") or {}).items():
                by[k] = v if isinstance(v, list) else [v]
            _orig.update(loaded=m, library=d.get("library"), byTime=by)
        except Exception:
            pass
    return _orig


def capture_key(path):
    """Index key for a LOCAL file (free to read).

    The index (System.Photo.DateTaken on the iCloud originals) stores TRUE UTC,
    so the local EXIF time must be shifted by the photo's own recorded offset:
    2021-05-30 23:14:40 with OffsetTimeOriginal -05:00 -> 2021-05-31T04:14:40.
    Returns a list of candidate keys, best first (the PC-timezone reading is
    kept as a fallback for files that carry no offset tag).
    """
    from datetime import datetime, timedelta, timezone
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            sub = {}
            try:
                sub = dict(ex.get_ifd(0x8769))
            except Exception:
                pass
            v = sub.get(36867) or ex.get(36867) or ex.get(306)
            if not v:
                return []
            local = datetime.strptime(str(v)[:19], "%Y:%m:%d %H:%M:%S")
            keys = []
            off = sub.get(0x9011) or sub.get(0x9010)      # OffsetTimeOriginal
            if off and len(str(off)) >= 6:
                s = str(off)
                try:
                    sign = -1 if s[0] == "-" else 1
                    delta = timedelta(hours=int(s[1:3]), minutes=int(s[4:6])) * sign
                    keys.append((local - delta).strftime("%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    pass
            keys.append(local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
            keys.append(local.strftime("%Y-%m-%dT%H:%M:%S"))
            seen, out = set(), []
            for k in keys:
                if k not in seen:
                    seen.add(k)
                    out.append(k)
            return out
    except Exception:
        return []


def find_original(path):
    """Full-res original for a (small) local copy, or None. Never downloads:
    matching uses the index built from placeholder metadata."""
    idx = originals_index()
    if not idx["library"] or not idx["byTime"]:
        return None
    cands = []
    for key in capture_key(path):
        cands = idx["byTime"].get(key) or []
        if cands:
            break
    if not cands:
        return None
    lib = Path(idx["library"])
    try:
        src_sz = os.path.getsize(path)
    except OSError:
        src_sz = 0
    # Pick the largest FILE at that capture second that is clearly heavier than
    # the copy we already have. File size is exact and free to read (directory
    # metadata); the Shell's Dimensions column proved unreliable in bulk.
    best = None
    for c in cands:
        sz = c.get("sz", 0)
        if sz <= src_sz * 1.15:              # not meaningfully better
            continue
        p = lib / c["n"]
        if p.exists() and (best is None or sz > best[0]):
            best = (sz, p)
    return best[1] if best else None


def hydrate_if_cloud(p, timeout_s=180):
    """Pull a cloud-only original down through the Cloud Files API first.

    Over half the main library is on-demand placeholders. Letting PIL open one
    triggers Windows' read-driven hydration, which Microsoft documents as
    "opportunistic" - it can stall for minutes, and the editor just looks hung
    after you press Insert. photo_backup solved this already; reuse it rather
    than keep a second, worse copy of the logic.
    """
    try:
        st = os.stat(p)
    except OSError:
        return
    if not (st.st_file_attributes & (0x400000 | 0x1000)):    # RECALL | OFFLINE
        return
    try:
        import photo_backup as pb                            # lazy: pb imports us
        pb.hydrate(Path(p), timeout_s)
    except Exception:
        pass                                                 # fall back to a plain read


def resolve_full_res(p):
    """Swap a small copy for its original when one exists (import/erase only)."""
    try:
        orig = find_original(p)
    except Exception:
        orig = None
    return orig or p


def source_path(root_idx, rel):
    """Resolve rel inside the chosen source root; refuse path escapes."""
    try:
        i = int(root_idx)
        if i < 0:                      # -1 would silently mean "last source"
            abort(400)
        base = Path(SOURCES[i]["path"]).resolve()
    except (IndexError, ValueError):
        abort(400)
    p = (base / rel).resolve() if rel else base
    if base != p and base not in p.parents:
        abort(403)
    return p


# ---------------------------------------------------------------- article pages
def article_pages():
    pages = []
    for pat in ("*/*.html", "Drafts/*/field-notes.html"):
        for p in sorted(ROOT.glob(pat)):
            if ".tmp" in p.parts or p.parts[0] == ".tmp":
                continue
            try:
                # .artbody is the transplanted itinerary's wrapper; without it
                # that page was missing from the editor's article list
                _t = p.read_text(encoding="utf-8")
                if 'class="article-body"' in _t or "artbody" in _t:
                    rel = p.relative_to(ROOT).as_posix()
                    label = ("[draft] " + p.parent.name if rel.startswith("Drafts/")
                             else rel.removesuffix(".html"))
                    pages.append({"path": rel, "label": label})
            except (UnicodeDecodeError, OSError):
                continue
    pages.sort(key=lambda x: (not x["path"].startswith("Drafts/"), x["label"]))
    return pages


def page_file(rel):
    p = (ROOT / rel).resolve()
    if ROOT not in p.parents or p.suffix != ".html" or not p.is_file():
        abort(404)
    return p


# ------------------------------------------------- article-body block indexing
class ChildScan(HTMLParser):
    """Record the source spans of the direct element children of an HTML
    fragment (the *content* of some container element)."""

    def __init__(self, frag, shift):
        super().__init__(convert_charrefs=False)
        self.frag, self.shift = frag, shift
        self.line_off = [0]
        for i, ch in enumerate(frag):
            if ch == "\n":
                self.line_off.append(i + 1)
        self.depth = 0
        self.open = None         # (start, tag, class, content_start) of open child
        self.children = []       # [{start,end,tag,cls,cstart,cend}] shifted offsets
        self.feed(frag)

    def off(self):
        line, col = self.getpos()
        return self.line_off[line - 1] + col

    def tag_end(self, start):
        return self.frag.index(">", start) + 1

    def handle_starttag(self, tag, attrs):
        start = self.off()
        if self.depth == 0 and self.open is None:
            if tag in VOID_TAGS:
                self._leaf(start, tag, attrs)
            else:
                self.open = (start, tag, dict(attrs).get("class") or "", self.tag_end(start))
        if tag not in VOID_TAGS:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        start = self.off()
        if self.depth == 0 and self.open is None:
            self._leaf(start, tag, attrs)

    def _leaf(self, start, tag, attrs):
        s = self.shift
        self.children.append({"start": s + start, "end": s + self.tag_end(start),
                              "tag": tag, "cls": dict(attrs).get("class") or "",
                              "cstart": None, "cend": None})

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        start = self.off()
        self.depth = max(0, self.depth - 1)
        if self.depth == 0 and self.open is not None:
            s = self.shift
            o = self.open
            self.children.append({"start": s + o[0], "end": s + self.tag_end(start),
                                  "tag": o[1], "cls": o[2],
                                  "cstart": s + o[3], "cend": s + start})
            self.open = None


def body_span(text):
    """(content_start, content_end) of the .article-body div. Script/style
    content is skipped verbatim: embedded JS is full of < and > that must not
    be read as tags."""
    m = re.search(r'<div\b[^>]*class="[^"]*\barticle-body\b[^"]*"[^>]*>', text)
    if not m:
        abort(400, "no article-body or artbody in page")
    tag_re = re.compile(r"<(/?)([a-zA-Z][\w-]*)")
    depth, i = 1, m.end()
    while True:
        t = tag_re.search(text, i)
        if not t:
            abort(400, "unclosed article-body")
        closing, tag = t.group(1) == "/", t.group(2).lower()
        gt = text.find(">", t.end())
        if gt < 0:
            abort(400, "unclosed tag")
        i = gt + 1
        if tag in ("script", "style") and not closing:
            j = text.find(f"</{tag}", i)     # skip content AND the closing tag,
            if j < 0:                        # or it would be re-read as a depth
                abort(400, f"unclosed {tag}")  # decrement
            i = text.index(">", j) + 1
            continue
        if tag in VOID_TAGS or (not closing and text[gt - 1] == "/"):
            continue                       # void or XML self-closing (SVG)
        depth += -1 if closing else 1
        if depth == 0:
            return m.end(), t.start()


class PageIndex:
    """Flat, document-ordered list of editable leaf blocks: the direct
    children of .article-body, except that the children of fn-section divs
    are used in the section's place (so photos can go inside sections).
    Mirrored exactly by the client's DOM walk."""

    def __init__(self, text):
        self.text = text
        self.body_start, self.body_end = body_span(text)
        self.blocks = []
        for c in ChildScan(text[self.body_start:self.body_end], self.body_start).children:
            if "fn-section" in c["cls"].split() and c["cstart"] is not None:
                self.blocks.extend(ChildScan(text[c["cstart"]:c["cend"]], c["cstart"]).children)
            else:
                self.blocks.append(c)

    def place(self, at, markup):
        """at = {"before": i} | {"after": i} | {"end": true} ->
        (source offset, text to splice in) keeping one block per line."""
        if at.get("end") or "after" in at and int(at.get("after", -1)) >= len(self.blocks):
            return self.body_end, "\n    " + markup
        if "before" in at:
            i = int(at["before"])
            if 0 <= i < len(self.blocks):
                pos = self.blocks[i]["start"]
                m = re.search(r"\n[ \t]*$", self.text[:pos])
                sep = m.group(0) if m else "\n    "   # reuse the block's own
                return pos, markup + sep             # separator so a removal
            return self.body_end, "\n    " + markup  # restores it byte-exactly
        i = int(at["after"])
        return self.blocks[i]["end"], "\n    " + markup


def index_page(rel):
    text = read_page(rel)
    return text, PageIndex(text)


# ---------------------------------------------------------------- image import
def clean_dirname(s):
    """One path COMPONENT: no separators, no drive colons, no dot-traversal.
    Country/City come from free-text fields — a stray '/' or '..' must not
    create nested folders or escape Images/."""
    return re.sub(r'[<>:"/\\|?*]', "", str(s or "")).strip(". ").strip()


def country_dir(slug):
    """Map a country slug or free-text name to its Images/<Country> folder,
    matching the existing folder case-insensitively; created on first import.
    Typed capitalization is preserved on create — .title() would fold
    'SpainMadrid' into 'Spainmadrid'."""
    name = clean_dirname(str(slug).replace("-", " ")) or "Unsorted"
    want = name.lower()
    for d in IMAGES.iterdir():
        if d.is_dir() and d.name.lower() == want and d.name != "web":
            return d
    return IMAGES / (name if any(c.isupper() for c in name) else name.title())


def country_for(rel):
    """Map a page path to its Images/<Country> folder (create for drafts)."""
    slug = Path(rel).parts[1] if rel.startswith("Drafts/") else Path(rel).parts[0]
    return country_dir(slug)


def clean_name(stem):
    s = unicodedata.normalize("NFC", stem)
    s = re.sub(r'[<>:"/\\|?*%#]', "", s).strip() or "photo"
    return s


# ---------------------------------------------------------------- AI eraser
# Local LaMa inpainting (the model class behind commercial "magic erasers").
# Model: .tmp/photo_editor/models/big-lama.pt (~196MB TorchScript, CPU).
ERASED = ROOT / ".tmp" / "photo_editor" / "erased"
ERASED.mkdir(parents=True, exist_ok=True)
LAMA_PT = ROOT / ".tmp" / "photo_editor" / "models" / "big-lama.pt"
_lama = None
_lama_gate = threading.Lock()


def lama():
    global _lama
    with _lama_gate:
        if _lama is None:
            import torch
            if not LAMA_PT.exists():
                raise RuntimeError("big-lama.pt missing — see project_photo_editor memory")
            _lama = torch.jit.load(str(LAMA_PT), map_location="cpu").eval()
    return _lama


def lama_erase(im, mask):
    """Inpaint masked pixels. Works on a context crop around the mask so a
    small erase on a 12MP photo stays fast, then blends the patch back at
    full resolution."""
    import numpy as np
    import torch
    m = np.asarray(mask, dtype=np.uint8) > 127
    ys, xs = np.where(m)
    if not len(ys):
        return im
    # context box: mask bounds + 45% padding
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    ph, pw = int((y1 - y0 + 1) * .45) + 32, int((x1 - x0 + 1) * .45) + 32
    y0, y1 = max(0, y0 - ph), min(im.height, y1 + ph + 1)
    x0, x1 = max(0, x0 - pw), min(im.width, x1 + pw + 1)
    crop = im.crop((x0, y0, x1, y1))
    mcrop = mask.crop((x0, y0, x1, y1))
    # cap the working resolution; LaMa CPU time grows fast with pixels
    scale = min(1.0, 1200 / max(crop.size))
    work = crop.resize((max(8, int(crop.width * scale)), max(8, int(crop.height * scale))),
                       Image.LANCZOS) if scale < 1 else crop
    wmask = mcrop.resize(work.size, Image.NEAREST)

    def pad8(t):
        h, w = t.shape[-2:]
        return torch.nn.functional.pad(t, (0, (8 - w % 8) % 8, 0, (8 - h % 8) % 8), mode="reflect")

    img_t = pad8(torch.from_numpy(np.asarray(work).copy()).permute(2, 0, 1)[None].float() / 255)
    mask_t = pad8((torch.from_numpy(np.asarray(wmask).copy())[None, None].float() / 255 > .5).float())
    with torch.inference_mode():
        out = lama()(img_t, mask_t)
    res = (out[0].permute(1, 2, 0).clamp(0, 1).numpy() * 255).astype("uint8")
    res = Image.fromarray(res[:work.height, :work.width])
    if res.size != crop.size:
        res = res.resize(crop.size, Image.LANCZOS)
    # blend only the masked pixels (slightly feathered) back into the crop
    from PIL import ImageFilter
    feather = mcrop.filter(ImageFilter.GaussianBlur(3))
    crop.paste(res, (0, 0), feather)
    im.paste(crop, (x0, y0))
    return im


# ---------------------------------------------------------------- develop bake
# The Lightroom-style develop math. The WebGL shader in editor.html implements
# EXACTLY these formulas IN THIS ORDER — if you change one, change both, or
# the live preview will not match the baked file.
DEV_KEYS = ("exposure", "contrast", "highlights", "shadows", "whites", "blacks",
            "temp", "tint", "vibrance", "saturation", "sharpen", "vignette",
            "dehaze", "clarity", "noise", "vigmid", "vigfeather",
            "hsl_o_h", "hsl_o_s", "hsl_o_l", "hsl_g_h", "hsl_g_s", "hsl_g_l",
            "hsl_a_h", "hsl_a_s", "hsl_a_l", "hsl_b_h", "hsl_b_s", "hsl_b_l")
HSL_CENTERS = {"o": 30.0, "g": 110.0, "a": 185.0, "b": 230.0}   # hue degrees
HSL_WIDTH = 55.0


def _smoothstep(e0, e1, x):
    import numpy as np
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def _hue_weight(hue_deg, center):
    """Raised-cosine window around a channel's hue center (degrees, wraps)."""
    import numpy as np
    dist = np.abs(((hue_deg - center + 180) % 360) - 180)
    w = np.clip(1 - dist / HSL_WIDTH, 0, 1)
    return w * w * (3 - 2 * w)


def develop(im, p, masks=None):
    """Bake develop parameters (each -100..100) into a PIL image."""
    import numpy as np
    from PIL import ImageFilter
    v = {k: float(p.get(k, 0)) / 100.0 for k in DEV_KEYS}
    masks = [m for m in (masks or []) if any(abs(float(m.get(k, 0))) > 1e-4
                                             for k in ("exposure", "temp", "sat"))]
    if not any(abs(x) > 1e-4 for x in v.values()) and not masks:
        return im
    a = np.asarray(im, dtype=np.float32) / 255.0
    h, w = a.shape[:2]
    # 1. exposure + white balance in (approx) linear light, +-2.5 EV full-scale
    lin = np.power(np.clip(a, 0, 1), 2.2)
    lin *= 2.0 ** (v["exposure"] * 2.5)
    lin[..., 0] *= 1 + 0.25 * v["temp"]
    lin[..., 2] *= 1 - 0.25 * v["temp"]
    lin[..., 1] *= 1 - 0.12 * v["tint"]
    d = np.power(np.clip(lin, 0, 4), 1 / 2.2)
    # 2. tone: highlights/shadows/whites/blacks, then contrast
    luma = d[..., 0] * .2126 + d[..., 1] * .7152 + d[..., 2] * .0722
    hl = _smoothstep(.45, 1.0, luma)[..., None]
    sh = (1 - _smoothstep(0.0, .55, luma))[..., None]
    dc = np.clip(d, 0, 1)
    bell = dc * (1 - dc)
    d = d + v["highlights"] * 1.5 * hl * bell
    d = d + v["shadows"] * 1.5 * sh * bell
    d = d * (1 + 0.25 * v["whites"])
    b = v["blacks"]
    d = np.where(b >= 0, d * (1 - 0.25 * b) + 0.25 * b,
                 (d - 0.25 * -b) / (1 - 0.25 * -b))
    d = (d - 0.5) * (1 + 0.6 * v["contrast"]) + 0.5
    d = np.clip(d, 0, 1)
    # 3. HSL mixer: hue-windowed hue-shift / sat / luminance per channel
    if any(abs(v[k]) > 1e-4 for k in DEV_KEYS if k.startswith("hsl_")):
        mx = d.max(-1)
        mn = d.min(-1)
        delta = mx - mn
        hue = np.zeros_like(mx)
        nz = delta > 1e-5
        r_, g_, b_ = d[..., 0], d[..., 1], d[..., 2]
        rmax = nz & (mx == r_)
        gmax = nz & (mx == g_) & ~rmax
        bmax = nz & ~rmax & ~gmax
        hue[rmax] = (60 * ((g_ - b_) / np.maximum(delta, 1e-6))[rmax]) % 360
        hue[gmax] = 60 * ((b_ - r_) / np.maximum(delta, 1e-6))[gmax] + 120
        hue[bmax] = 60 * ((r_ - g_) / np.maximum(delta, 1e-6))[bmax] + 240
        lum3 = (d[..., 0] * .2126 + d[..., 1] * .7152 + d[..., 2] * .0722)[..., None]
        hshift = np.zeros_like(mx)
        for ch, center in HSL_CENTERS.items():
            wgt = _hue_weight(hue, center)
            if abs(v[f"hsl_{ch}_s"]) > 1e-4:
                d = lum3 + (d - lum3) * (1 + v[f"hsl_{ch}_s"] * wgt)[..., None]
            if abs(v[f"hsl_{ch}_l"]) > 1e-4:
                d = d * (1 + 0.4 * v[f"hsl_{ch}_l"] * wgt)[..., None]
            if abs(v[f"hsl_{ch}_h"]) > 1e-4:
                hshift = hshift + v[f"hsl_{ch}_h"] * 30.0 * wgt
        if np.any(np.abs(hshift) > 1e-3):
            # rotate hue by re-deriving rgb from shifted hue (constant sat/luma
            # approximation identical to the shader's)
            rad = np.radians(hshift)
            cosA = np.cos(rad)[..., None]
            sinA = np.sin(rad)[..., None]
            sq = 0.57735
            d = np.clip(d * cosA + np.cross(np.broadcast_to([sq, sq, sq], d.shape), d) * sinA +
                        (sq * (d[..., 0] + d[..., 1] + d[..., 2]))[..., None] * sq * (1 - cosA), 0, 4)
        d = np.clip(d, 0, 1)
    # 4. saturation + vibrance
    l2 = (d[..., 0] * .2126 + d[..., 1] * .7152 + d[..., 2] * .0722)[..., None]
    satur = (d.max(-1) - d.min(-1))[..., None]
    d = l2 + (d - l2) * (1 + v["saturation"])
    d = l2 + (d - l2) * (1 + v["vibrance"] * 0.8 * (1 - satur))
    # 5. dehaze: pull the haze floor down (or lift it, for negative), plus a
    # small saturation compensation — matched in the shader
    if abs(v["dehaze"]) > 1e-4:
        k = 0.12 * v["dehaze"]
        d = (d - k) / (1 - k)
        l3 = (d[..., 0] * .2126 + d[..., 1] * .7152 + d[..., 2] * .0722)[..., None]
        d = l3 + (d - l3) * (1 + 0.15 * v["dehaze"])
    d = np.clip(d, 0, 1)
    # 6. clarity: midtone local contrast. The detail signal comes from the
    # SOURCE luma (a single-pass shader cannot ring-blur its own processed
    # output), weighted into midtones of the current image. Ring radius is
    # 1.2% of the min dimension on both sides.
    if abs(v["clarity"]) > 1e-4:
        srcl = (np.clip(a, 0, 1)[..., 0] * .2126 +
                np.clip(a, 0, 1)[..., 1] * .7152 +
                np.clip(a, 0, 1)[..., 2] * .0722)
        r = max(2, int(min(w, h) * 0.012))
        ring = np.zeros_like(srcl)
        for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r),
                       (r, r), (r, -r), (-r, r), (-r, -r)):
            ring += np.roll(np.roll(srcl, dy, axis=0), dx, axis=1)
        ring /= 8.0
        lm = d[..., 0] * .2126 + d[..., 1] * .7152 + d[..., 2] * .0722
        mid = 4 * lm * (1 - lm)
        d = d + ((srcl - ring) * v["clarity"] * 1.1 * mid)[..., None]
        d = np.clip(d, 0, 1)
    # 7. noise reduction: 4-neighbor bilateral computed on the SOURCE (same
    # single-pass constraint), its smoothing delta applied to the output
    if v["noise"] > 1e-4:
        a0 = np.clip(a, 0, 1)
        acc = a0.copy()
        wsum = np.ones(a0.shape[:2], dtype=np.float32)
        sig = 0.08
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = np.roll(np.roll(a0, dy, axis=0), dx, axis=1)
            dist2 = ((n - a0) ** 2).sum(-1)
            wgt = np.exp(-dist2 / (2 * sig * sig)).astype(np.float32)
            acc += n * wgt[..., None]
            wsum += wgt
        d = d + (acc / wsum[..., None] - a0) * v["noise"]
        d = np.clip(d, 0, 1)
    # 8. local masks (linear / radial): exposure, temp, sat weighted by falloff
    if masks:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        u = xx / w
        vv = yy / h
        for m in masks:
            if m.get("type") == "radial":
                cx, cy = float(m["x0"]), float(m["y0"])
                rx = max(1e-4, abs(float(m["x1"]) - cx))
                ry = max(1e-4, abs(float(m["y1"]) - cy))
                dist = np.sqrt(((u - cx) / rx) ** 2 + ((vv - cy) / ry) ** 2)
                t = 1 - _smoothstep(0.7, 1.3, dist)
            else:
                x0, y0 = float(m["x0"]), float(m["y0"])
                x1, y1 = float(m["x1"]), float(m["y1"])
                dx2, dy2 = x1 - x0, y1 - y0
                ln = max(1e-6, dx2 * dx2 + dy2 * dy2)
                t = 1 - _smoothstep(0.0, 1.0, ((u - x0) * dx2 + (vv - y0) * dy2) / ln)
            t3 = t[..., None]
            me = float(m.get("exposure", 0)) / 100.0
            mt = float(m.get("temp", 0)) / 100.0
            ms = float(m.get("sat", 0)) / 100.0
            if abs(me) > 1e-4:
                d = d * (2.0 ** (me * 1.5 * t3))
            if abs(mt) > 1e-4:
                d[..., 0] *= 1 + 0.20 * mt * t
                d[..., 2] *= 1 - 0.20 * mt * t
            if abs(ms) > 1e-4:
                lm = (d[..., 0] * .2126 + d[..., 1] * .7152 + d[..., 2] * .0722)[..., None]
                d = lm + (d - lm) * (1 + ms * t3)
        d = np.clip(d, 0, 1)
    # 9. vignette with midpoint + feather controls
    if abs(v["vignette"]) > 1e-4:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        rd = np.sqrt(((xx / w - .5) * 2) ** 2 + ((yy / h - .5) * 2) ** 2) / 1.4142
        mid = 0.35 + 0.35 * v["vigmid"]                 # -1..1 -> 0.0..0.7
        feather = max(0.05, 0.75 + 0.55 * v["vigfeather"])
        d *= (1 + v["vignette"] * 0.6 * _smoothstep(mid, mid + feather, rd))[..., None]
    out = Image.fromarray((np.clip(d, 0, 1) * 255).astype("uint8"), "RGB")
    # 10. sharpen last
    if v["sharpen"] > 1e-4:
        out = out.filter(ImageFilter.UnsharpMask(radius=1.6,
                                                 percent=int(v["sharpen"] * 130),
                                                 threshold=2))
    return out


def straighten(im, angle):
    """Arbitrary-angle straighten: rotate, then crop to the largest inscribed
    rectangle of the ORIGINAL aspect so no blank corners survive. The shader
    previews the identical window (same inscribed-scale formula)."""
    import math
    angle = float(angle or 0)
    if abs(angle) < 1e-3:
        return im
    w, h = im.size
    rad = math.radians(abs(angle))
    # largest same-aspect inscribed rect scale for |angle| <= 45deg
    scale = 1.0 / (math.cos(rad) + (max(w, h) / min(w, h)) * math.sin(rad))
    rot = im.rotate(angle, resample=Image.BICUBIC, expand=True)
    cw, ch = int(w * scale), int(h * scale)
    cx, cy = rot.width / 2, rot.height / 2
    return rot.crop((int(cx - cw / 2), int(cy - ch / 2),
                     int(cx - cw / 2) + cw, int(cy - ch / 2) + ch))


def apply_orient(im, rot, flip):
    """User-requested rotation (CCW degrees) / mirror on top of EXIF upright."""
    if flip == "h":
        im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    elif flip == "v":
        im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    rot = int(rot or 0) % 360
    if rot:
        im = im.rotate(rot, expand=True)
    return im


def import_photo(src, country_dir, city, rot=0, flip=None, dev=None, name_hint=None,
                 angle=0, masks=None):
    """Copy a library photo into the Images/ archive. HEIC becomes JPEG
    (quality 95, ICC + EXIF preserved); untouched non-HEIC files are copied
    byte-for-byte. Rotation/flip/develop re-encode the archive COPY only —
    the file in the source library is never modified. Returns the Path."""
    city = clean_dirname(city)
    dest_dir = country_dir / city if city else country_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    rot = int(rot or 0) % 360
    angle = float(angle or 0)
    dev = {k: v for k, v in (dev or {}).items() if k in DEV_KEYS and abs(float(v)) > 1e-4}
    masks = masks or []
    reencode = (src.suffix.lower() in (".heic", ".heif") or rot or flip or dev
                or abs(angle) > 1e-3 or masks)
    base = clean_name((name_hint or src).stem if hasattr(name_hint or src, "stem")
                      else str(name_hint))
    ext = ".jpg" if reencode else src.suffix
    dest, n = dest_dir / f"{base}{ext}", 2
    while dest.exists():
        dest, n = dest_dir / f"{base}-{n}{ext}", n + 1
    if reencode:
        im = ImageOps.exif_transpose(Image.open(src))
        icc = im.info.get("icc_profile")           # grab BEFORE convert()
        exif = im.info.get("exif")
        im = apply_orient(im, rot, flip)
        im = im.convert("RGB")
        im = straighten(im, angle)
        if dev or masks:
            im = develop(im, dev, masks)
        kw = {"quality": 95}
        if icc:
            kw["icc_profile"] = icc
        if exif and not (rot or flip):             # stale Orientation would re-rotate
            kw["exif"] = exif
        im.save(dest, "JPEG", **kw)
    else:
        shutil.copy2(src, dest)
    return dest


def img_markup(dest, page_rel, alt, kind):
    depth = len(Path(page_rel).parts) - 1
    src = "../" * depth + dest.relative_to(ROOT).as_posix()
    src = src.replace(" ", "%20")
    style = "position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;object-position:50% 50%;"
    return f'<img src="{src}" alt="{esc(alt)}" style="{style}">'


def esc(s):
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


PAIR_SLOT = ('<span style="display:block;position:relative;overflow:hidden;'
             'width:100%;aspect-ratio:2/3;border-radius:2px;">{img}</span>')


def block_markup(kind, imgs, page_rel):
    if kind == "landscape":
        return f'<div class="img-landscape">{imgs[0]}</div>'
    slots = "".join(PAIR_SLOT.format(img=i) for i in imgs)
    return f'<div class="img-pair">{slots}</div>'


# ---------------------------------------------------------------------- routes
@app.route("/")
def ui():
    return Response_UI


@app.route("/site/<path:rel>")
def site(rel):
    return send_from_directory(ROOT, rel)


@app.route("/api/state")
def api_state():
    return jsonify({"articles": article_pages(),
                    "sources": [{"label": s["label"]} for s in SOURCES],
                    "heic": HEIC_OK})


_browse_cache = {}          # str(path) -> (dir_mtime, when, payload)


@app.route("/api/browse")
def api_browse():
    p = source_path(request.args.get("root", 0), request.args.get("path", ""))
    try:
        dir_mtime = p.stat().st_mtime_ns
        hit = _browse_cache.get(str(p))
        # exact-mtime hit, or a listing under 15s old — during an active iCloud
        # sync the folder mtime changes on every request, and enumerating ~10k
        # cloud-placeholder files costs ~2s each time
        if hit and (hit[0] == dir_mtime or time.time() - hit[1] < 15):
            return _browse_page(hit[2])
    except OSError:
        dir_mtime = None
    dirs, photos = [], []
    # os.scandir, not iterdir+stat: scandir returns each entry's stat data from
    # the directory walk itself. Per-file Path.stat() on a 9k-photo iCloud
    # folder of on-demand placeholders took ~7s; this is near-instant.
    # Cloud placeholders (iCloud/OneDrive on-demand files) are flagged so the
    # client can avoid silently downloading a 120GB library just by scrolling.
    RECALL, OFFLINE = 0x400000, 0x1000
    cloud = []
    try:
        with os.scandir(p) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                if e.is_dir():
                    if e.name != "web" or Path(p) != IMAGES:
                        dirs.append(e.name)
                elif os.path.splitext(e.name)[1].lower() in IMG_EXTS:
                    try:
                        st = e.stat()
                        photos.append((st.st_mtime, e.name))
                        attrs = getattr(st, "st_file_attributes", 0)
                        if attrs & RECALL or attrs & OFFLINE:
                            cloud.append(e.name)
                    except OSError:
                        photos.append((0, e.name))
    except OSError:
        pass
    dirs.sort(key=str.lower)
    # newest first: an iCloud library dump has UUID filenames, so name order is
    # meaningless while shot/added date is exactly the order you think in
    photos.sort(reverse=True)
    payload = {"dirs": dirs, "photos": [n for _, n in photos], "cloud": cloud}
    if dir_mtime is not None:
        _browse_cache[str(p)] = (dir_mtime, time.time(), payload)
    return _browse_page(payload)


def _browse_page(payload):
    """Serve one page of the listing: a 10k-photo iCloud library is ~400KB of
    JSON, and large responses from the dev server get reset often enough under
    sync load that the sidebar showed nothing."""
    off = int(request.args.get("offset", 0))
    n = int(request.args.get("limit", 300))
    page = payload["photos"][off:off + n]
    cloud = set(payload.get("cloud", ()))
    return jsonify({"dirs": payload["dirs"] if off == 0 else [],
                    "photos": page,
                    "cloud": [f for f in page if f in cloud],
                    "cloudTotal": len(cloud),
                    "total": len(payload["photos"]), "offset": off})


def save_sources():
    SRC_CFG.write_text(json.dumps(SOURCES, indent=2), encoding="utf-8")


@app.route("/api/sources_add", methods=["POST", "OPTIONS"])
def api_sources_add():
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True)
    p = Path(d["path"].strip().strip('"'))
    if not p.is_dir():
        return jsonify({"ok": False, "error": f"Not a folder: {p}"}), 400
    if any(Path(s["path"]) == p for s in SOURCES):
        return jsonify({"ok": True, "sources": [{"label": s["label"]} for s in SOURCES]})
    SOURCES.append({"label": d.get("label", "").strip() or p.name, "path": str(p)})
    save_sources()
    return jsonify({"ok": True, "sources": [{"label": s["label"]} for s in SOURCES]})


@app.route("/api/sources_remove", methods=["POST", "OPTIONS"])
def api_sources_remove():
    if request.method == "OPTIONS":
        return "", 204
    i = int(request.get_json(force=True)["index"])
    if 0 <= i < len(SOURCES):
        SOURCES.pop(i)
        save_sources()
    return jsonify({"ok": True, "sources": [{"label": s["label"]} for s in SOURCES]})


@app.route("/api/photo_meta")
def api_photo_meta():
    p = source_path(request.args["root"], request.args["path"])
    try:
        with Image.open(p) as im:
            im = ImageOps.exif_transpose(im)
            w, h = im.size
    except Exception:
        return jsonify({"error": "unreadable"}), 415
    out = {"w": w, "h": h,
           "orient": "landscape" if w >= h * 1.05 else "portrait" if h >= w * 1.05 else "square"}
    if request.args.get("full"):          # is a bigger original available?
        o = find_original(p)
        if o:
            out["hasOriginal"] = True
            try:
                out["origMB"] = round(os.path.getsize(o) / 1e6, 1)
                out["copyMB"] = round(os.path.getsize(p) / 1e6, 1)
            except OSError:
                pass
    return jsonify(out)


_thumb_gate = threading.Semaphore(4)   # decode a few at a time; a burst of iCloud
                                       # HEICs must not starve browse/import calls


@app.route("/thumb")
def thumb():
    p = source_path(request.args["root"], request.args["path"])
    size = int(request.args.get("s", 320))
    rot = int(request.args.get("rot", 0)) % 360
    flip = request.args.get("flip") or None
    key = f"{p}-{p.stat().st_mtime_ns}-{size}-{rot}-{flip}"
    cache = THUMBS / (re.sub(r"\W", "_", key)[-120:] + ".jpg")
    if not cache.exists():
        with _thumb_gate:
            if not cache.exists():
                try:
                    im = ImageOps.exif_transpose(Image.open(p))
                except Exception:
                    abort(415)
                icc = im.info.get("icc_profile")
                im = apply_orient(im, rot, flip)
                im.thumbnail((size, size))
                kw = {"quality": 82}
                if icc:
                    kw["icc_profile"] = icc
                im.convert("RGB").save(cache, "JPEG", **kw)
    return send_file(cache, mimetype="image/jpeg")


@app.route("/api/erase", methods=["POST", "OPTIONS"])
def api_erase():
    """Run the AI eraser on a pending photo. Erases stack: pass the previous
    token to keep going. Returns a token the client previews and imports."""
    if request.method == "OPTIONS":
        return "", 204
    import base64
    import io
    import uuid
    d = request.get_json(force=True)
    if d.get("token"):
        im = Image.open(ERASED / (re.sub(r"\W", "", d["token"]) + ".jpg")).convert("RGB")
        icc = im.info.get("icc_profile")
    else:
        src = resolve_full_res(source_path(d["root"], d["path"]))
        im = ImageOps.exif_transpose(Image.open(src))
        icc = im.info.get("icc_profile")
        im = apply_orient(im, d.get("rot", 0), d.get("flip")).convert("RGB")
        im = straighten(im, d.get("angle", 0))   # erase masks are painted on
    raw = base64.b64decode(d["mask"].split(",", 1)[1])   # the straightened view
    mask = Image.open(io.BytesIO(raw)).convert("L").resize(im.size, Image.NEAREST)
    try:
        im = lama_erase(im, mask)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    token = uuid.uuid4().hex[:16]
    kw = {"quality": 95}
    if icc:
        kw["icc_profile"] = icc
    im.save(ERASED / (token + ".jpg"), "JPEG", **kw)
    return jsonify({"ok": True, "token": token})


@app.route("/erased")
def erased_view():
    token = re.sub(r"\W", "", request.args["token"])
    p = ERASED / (token + ".jpg")
    if not p.exists():
        abort(404)
    size = int(request.args.get("s", 2200))
    im = Image.open(p)
    icc = im.info.get("icc_profile")
    im.thumbnail((size, size))
    import io
    buf = io.BytesIO()
    kw = {"quality": 88}
    if icc:
        kw["icc_profile"] = icc
    im.convert("RGB").save(buf, "JPEG", **kw)
    buf.seek(0)
    return send_file(buf, mimetype="image/jpeg")


# ------------------------------------------------------------ photo browser
# Deliberately OUTSIDE ~/iCloudPhotos: that tree is managed by iCloud, and a
# 40GB folder of our own inside it risks being synced back up or cleaned out.
BACKUP = Path.home() / "Backup"
RATINGS = BACKUP / "_meta" / "ratings.json"
# Photos shortlisted for the blog, keyed "<album>/<file>" like ratings. The
# value carries the note, so a pick and its note are one record: shortlisting a
# photo is only half the thought, the other half is where it might go.
PICKS = BACKUP / "_meta" / "picks.json"


def load_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


_MS_CACHE = {"key": None, "data": {}}


def shared_album_server_counts():
    """What APPLE'S SERVER holds for each shared album, from iCloud's own
    MediaStream database.

    This is the only way to tell the two failure modes apart, and they need
    opposite responses:

      pending > 0   this PC is behind. It knows about the items and is still
                    downloading them. Waiting works.
      pending == 0
      and short     the server itself doesn't have them. Your iPhone never
                    finished uploading, and no amount of restarting iCloud on
                    Windows will conjure them. The fix is on the phone.

    Rows whose filename ends .jpgthumb are poster frames iCloud generates per
    video, not album items, so they are excluded from the count."""
    try:
        base = Path(os.environ["LOCALAPPDATA"]) / "Packages"
        db = next(base.glob("AppleInc.iCloud_*/LocalCache/Roaming/Apple Computer/"
                            "MediaStream/local.db"))
    except (StopIteration, KeyError, OSError):
        return {}
    try:
        key = (str(db), db.stat().st_mtime_ns, db.stat().st_size)
    except OSError:
        return {}
    if _MS_CACHE["key"] == key:
        return _MS_CACHE["data"]
    # iCloud keeps the file open, so read a snapshot rather than fighting it
    tmp = BACKUP / "_meta" / "_mediastream.tmp.db"
    out = {}
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(db, tmp)
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        try:
            rows = con.execute("""
                select a.albumName, s.assetfilename, s.downloaded
                from MSASAlbums a join MSASAlbumAssets s on s.albumGuid = a.albumGuid
                where coalesce(s.deleted, 0) = 0
            """)
            for name, fn, dl in rows:
                if (fn or "").lower().endswith(".jpgthumb"):
                    continue                     # video poster, not an album item
                d = out.setdefault(name, {"onServer": 0, "pending": 0})
                d["onServer"] += 1
                if not dl:
                    d["pending"] += 1
        finally:
            con.close()
    except Exception:
        return _MS_CACHE["data"]                 # keep the last good answer
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    _MS_CACHE.update(key=key, data=out)
    return out


@app.route("/browser")
def browser_page():
    # the library merged into the editor; #library selects the view client-side
    return send_file(ROOT / "editor.html")


@app.route("/api/backup_activity")
def api_backup_activity():
    """What the backup is doing RIGHT NOW: which album, which files, how fast.

    api_backup_status only reports settled totals - it can't show a transfer
    in progress. This scans the live .part files on disk (each one IS an
    in-flight copy) and pairs them with the current pass counters from
    status.json, so the library page can show real activity instead of asking
    someone to run a script to find out if anything is happening.
    """
    st = load_json(BACKUP / "_meta" / "status.json", {"albums": {}})
    now = time.time()

    in_flight = []
    if BACKUP.is_dir():
        for d in BACKUP.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            for sub, album in ((d, d.name), (d / "videos", d.name)):
                if not sub.is_dir():
                    continue
                for p in sub.glob("*.part"):
                    try:
                        st_p = p.stat()
                    except OSError:
                        continue
                    # temp names are "<realname>.<pid>.<tid>.part" - strip the
                    # two numeric suffixes back off to show the real filename
                    stem = p.name[:-5]                          # drop ".part"
                    parts = stem.rsplit(".", 2)
                    real = parts[0] if len(parts) == 3 and all(x.isdigit() for x in parts[1:]) else stem
                    age = now - st_p.st_ctime
                    # A cloud hydration sits at 0 bytes for 5+ minutes in
                    # iCloud's queue and then lands all at once - that is the
                    # NORMAL shape, not a stall, and copy_deadline no longer
                    # aborts it. So age alone says nothing. A .part is only
                    # truly abandoned if it belongs to a watcher process that
                    # no longer exists: the temp name carries the owning PID.
                    owner_pid = int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else None
                    in_flight.append({
                        "album": album, "name": real,
                        "mb": round(st_p.st_size / 1e6, 2),
                        "ageSec": round(age),
                        "pid": owner_pid,
                        "queued": st_p.st_size == 0,   # waiting on iCloud, no bytes yet
                    })
    # Is the PID that owns each .part still alive? Asked via the Win32 API
    # directly. An earlier version shelled out to PowerShell here, and because
    # this endpoint is polled every 2 seconds while the Live Activity panel is
    # open, that flashed a fresh console window on screen every 2 seconds -
    # subprocess from a windowless pythonw still pops a console on Windows.
    import ctypes
    _k32 = ctypes.windll.kernel32
    _k32.OpenProcess.restype = ctypes.c_void_p
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
    STILL_ACTIVE = 259
    def _pid_alive(pid):
        h = _k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if _k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            _k32.CloseHandle(h)
    # Hydrations in progress. With explicit CfHydratePlaceholder the copy step
    # is milliseconds, so the .part scan above almost never catches anything -
    # the slow, visible work is the hydration, which the watcher records here.
    for h in load_json(BACKUP / "_meta" / "inflight.json", []):
        in_flight.append({
            "album": h.get("album"), "name": h.get("name"),
            "mb": h.get("mb", 0), "ageSec": round(now - h.get("since", now)),
            "pid": h.get("pid"), "queued": False, "phase": "hydrating",
        })
    alive_cache = {}
    for f in in_flight:
        # orphan = owned by a dead process. That is the only real "stalled".
        pid = f["pid"]
        if pid is None:
            f["stalled"] = False
            continue
        if pid not in alive_cache:
            alive_cache[pid] = _pid_alive(pid)
        f["stalled"] = not alive_cache[pid]
    in_flight.sort(key=lambda x: (x["stalled"], -x["ageSec"]))
    active = sum(1 for f in in_flight if not f["stalled"])

    # Which album is genuinely being worked on RIGHT NOW - decided from the
    # real .part files above, never from status.json's state=="running" flag.
    # That flag is written once when a pass STARTS and is never cleared if
    # the process gets killed mid-pass (a restart, a hold taking effect
    # mid-run, anything) - it can point at an album nothing has touched in
    # an hour. Kosovo sat there as "now backing up" long after it was put on
    # hold and its pass was killed, simply because no later pass had
    # overwritten the label. Ground truth is which album owns a fresh
    # (non-stalled) transfer, not what the label says.
    running_album, stage, pass_info = None, None, {}
    live = [f for f in in_flight if not f["stalled"]]
    if live:
        running_album = live[0]["album"]
        a = st.get("albums", {}).get(running_album, {})
        # whichever counter is presently mid-pass tells us photos vs videos
        stage = "photos" if a.get("state") == "running" else "videos"
        if stage == "photos":
            pass_info = {"done": a.get("done"), "total": a.get("total"),
                         "copied": a.get("copied"), "failed": a.get("failed"),
                         "skipped": a.get("skipped")}
        else:
            pass_info = {"done": a.get("vidDone"), "total": a.get("vidTotal"),
                         "copied": a.get("vidFull"), "failed": a.get("vidFailed")}

    # How much is actually left to copy, so the panel can say "all caught up"
    # instead of rendering an empty card when there is simply nothing to do.
    todo = 0
    hold = set(load_json(BACKUP / "_meta" / "hold.json", []))
    skip = {"Houston Trip", "New Zealand", "tomorrowland x bvi"}
    try:
        import re as _re2
        shared = Path.home() / "iCloudPhotos" / "Shared"
        def _canon(n):
            m = _re2.match(r"^(.+)_\d+$", n)
            return m.group(1) if m and (BACKUP / m.group(1)).is_dir() else n
        for d in shared.iterdir():
            if not d.is_dir() or d.name in skip or d.name in hold:
                continue
            base = _re2.sub(r"_\d+$", "", d.name)
            if base in skip:
                continue
            n_items = sum(1 for p in d.iterdir()
                          if p.suffix.lower() in {".jpg", ".mp4"})
            if not n_items:
                continue
            # Count album ENTRIES with no backed-up file - not the difference
            # between entry count and file count. Entries share files now
            # (Apple's rebuild lists the same photo twice), so an album with
            # duplicates always has fewer files than entries and a count-based
            # figure reads as thousands outstanding on a complete backup.
            bd = BACKUP / _canon(d.name)
            if not bd.is_dir():
                todo += n_items
                continue
            ent = (load_json(bd / "_manifest.json", {}) or {}).get("entries", {})
            vent = load_json(bd / "videos" / "_manifest.json", {}) or {}
            for f in d.iterdir():
                sfx = f.suffix.lower()
                if sfx == ".jpg":
                    t = ent.get(f.name)
                    if not t or not (bd / t).exists():
                        todo += 1
                elif sfx == ".mp4":
                    rec = vent.get(f.name)
                    t = rec.get("file") if isinstance(rec, dict) else None
                    if not t or not (bd / "videos" / t).exists():
                        todo += 1
    except OSError:
        todo = -1

    thr = st.get("throttle") or {}
    thr_left = max(0, round((thr.get("until") or 0) - now))
    return jsonify({
        "todo": todo,
        "throttled": thr_left > 0, "throttleLeftSec": thr_left,
        "throttleTrips": thr.get("trips", 0),
        "running": running_album is not None,
        "watcherAlive": (now - st.get("updated", 0)) < 200,
        "album": running_album, "stage": stage, "pass": pass_info,
        "inFlight": in_flight, "activeCount": active,
        "orphanCutoffMin": 30,       # sweep_parts() threshold, for the UI's benefit
    })


@app.route("/api/backup_status")
def api_backup_status():
    st = load_json(BACKUP / "_meta" / "status.json", {"albums": {}})
    shared = Path.home() / "iCloudPhotos" / "Shared"
    out = []
    names = set(st.get("albums", {}))
    if BACKUP.is_dir():
        names |= {d.name for d in BACKUP.iterdir() if d.is_dir() and not d.name.startswith("_")}
    if shared.is_dir():
        names |= {d.name for d in shared.iterdir()
                  if d.is_dir() and d.name not in
                  {"Houston Trip", "New Zealand", "tomorrowland x bvi"}}
    import re as _re
    SKIP_ALBUMS = {"Houston Trip", "New Zealand", "tomorrowland x bvi"}

    def _base(n):
        m = _re.match(r"^(.+)_\d+$", n)
        return m.group(1) if m else n

    # A rebuild twin is not an album. iCloud recreates each shared album as
    # "<Name>_1", "_2", "_3" while it refills them, and the backup already
    # routes every round into the one folder - so listing them separately meant
    # 83 cards for 27 albums, most of them empty shells with their own "enter
    # your iPhone count" box. Fold each twin into its base, and drop the
    # excluded albums by base name so "tomorrowland x bvi_1" goes too.
    names = {_base(n) for n in names}
    names = {n for n in names
             if n not in SKIP_ALBUMS
             and ((BACKUP / n).is_dir() or (shared / n).is_dir())}
    for n in sorted(names):
        a = dict(st.get("albums", {}).get(n, {}))
        errs = (a.pop("errors", None) or []) + (a.pop("vidErrors", None) or [])
        if errs:                              # can be thousands of strings
            a["lastError"] = errs[-1]
        d = BACKUP / n
        # Exclude our metadata by NAME and count only real media. Filtering on
        # a leading "_" dropped India's _DSC4284.JPG and _DSC4284-2.JPG, so the
        # card read 798 of 800 on an album that was actually complete. Third
        # place this same filter was wrong - the others were the todo count
        # and the watcher's completeness gate.
        files = [p for p in d.iterdir()
                 if p.is_file() and not p.name.startswith("_manifest")
                 and p.suffix.lower() in IMG_EXTS] if d.is_dir() else []
        a["name"] = n
        a["onDisk"] = len(files)
        a["mb"] = round(sum(p.stat().st_size for p in files) / 1e6, 1)
        # count videos from disk, not just the live status file — that resets
        # whenever the backup process restarts
        vdir = d / "videos"
        vids = [p for p in vdir.iterdir()
                if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".m4v"}] if vdir.is_dir() else []
        a["videos"] = len(vids)
        a["videosMb"] = round(sum(p.stat().st_size for p in vids) / 1e6, 1)
        # Count DISTINCT photos across every round of this album, not the items
        # in one folder. Each round is a different cut - Apple drops what it can
        # no longer serve - and the filename is a content hash, so the stem is
        # the photo. This is the number that lines up with the iPhone.
        stems = set()
        if shared.is_dir():
            for sd in shared.iterdir():
                if not sd.is_dir() or _base(sd.name) != n:
                    continue
                for p in sd.iterdir():
                    if p.is_file() and p.suffix.lower() in IMG_EXTS:
                        stems.add(_re.sub(r"_\d+$", "", p.stem))
        a["inAlbum"] = len(stems)
        out.append(a)
    exp = load_json(EXPECTED, {})
    arch = load_json(ARCHIVED, {})
    srv = shared_album_server_counts()
    hold = set(load_json(BACKUP / "_meta" / "hold.json", []))
    for a in out:
        a["held"] = a["name"] in hold             # present, deliberately not backed up
        a["expected"] = exp.get(a["name"])
        a["archived"] = arch.get(a["name"])       # ISO date you ticked it off
        s = srv.get(a["name"]) or {}
        a["onServer"] = s.get("onServer")         # what Apple actually holds
        a["pending"] = s.get("pending")           # queued for download to this PC
    return jsonify({"albums": out, "running": st.get("running", False),
                    "updated": st.get("updated", 0)})


@app.route("/api/backup_browse")
def api_backup_browse():
    album = clean_dirname(request.args.get("album", ""))
    d = BACKUP / album
    if not d.is_dir():
        return jsonify({"photos": []})
    man = load_json(d / "_manifest.json", {})
    man = man.get("files", man)          # new shape keys metadata under "files"
    rat = load_json(RATINGS, {})
    picks = load_json(PICKS, {})
    photos = []
    for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
        if (not p.is_file() or p.name.startswith("_manifest")
                or p.suffix.lower() not in IMG_EXTS):
            continue
        m = man.get(p.name, {})
        pick = picks.get(f"{album}/{p.name}")
        photos.append({"n": p.name, "mb": round(p.stat().st_size / 1e6, 2),
                       "w": m.get("w"), "h": m.get("h"),
                       "edited": bool(m.get("edited")),
                       "full": m.get("full", True),
                       "rating": rat.get(f"{album}/{p.name}", 0),
                       "picked": pick is not None,
                       "note": (pick or {}).get("note", "")})
    return jsonify({"album": album, "photos": photos})


EXPECTED = BACKUP / "_meta" / "expected.json"


@app.route("/api/expected", methods=["POST", "OPTIONS"])
def api_expected():
    """The item count YOU see on your iPhone for an album. It is the only
    trustworthy definition of 'complete' — iCloud pauses an album mid-upload
    while it works on others, so 'stopped growing' means nothing."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True)
    exp = load_json(EXPECTED, {})
    n = str(d.get("count", "")).strip()
    if n.isdigit() and int(n) > 0:
        exp[d["album"]] = int(n)
    else:
        exp.pop(d["album"], None)
    EXPECTED.parent.mkdir(parents=True, exist_ok=True)
    EXPECTED.write_text(json.dumps(exp, indent=1), encoding="utf-8")
    return jsonify({"ok": True, "expected": exp})


HOLD = BACKUP / "_meta" / "hold.json"


@app.route("/api/hold", methods=["POST", "OPTIONS"])
def api_hold():
    """Turn an album's backup on or off. Albums arrive on hold because a batch
    of them can appear at once (reviving Apple's shared-album agent surfaced
    nine), and pulling full-resolution originals for all of them uses real
    disk. The choice belongs here, not in a message to someone else."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True)
    hold = set(load_json(HOLD, []))
    if d.get("hold"):
        hold.add(d["album"])
    else:
        hold.discard(d["album"])
    HOLD.parent.mkdir(parents=True, exist_ok=True)
    HOLD.write_text(json.dumps(sorted(hold), indent=1), encoding="utf-8")
    return jsonify({"ok": True, "held": sorted(hold)})


ARCHIVED = BACKUP / "_meta" / "archived.json"


@app.route("/api/archived", methods=["POST", "OPTIONS"])
def api_archived():
    """You ticking 'I deleted the shared album' — the end of the line for this
    country. The backup is proven, the phone is clear, and the card has nothing
    left to warn you about, so it collapses to a single done line."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True)
    arch = load_json(ARCHIVED, {})
    if d.get("done"):
        arch[d["album"]] = time.strftime("%Y-%m-%d")
    else:
        arch.pop(d["album"], None)
    ARCHIVED.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVED.write_text(json.dumps(arch, indent=1), encoding="utf-8")
    return jsonify({"ok": True, "archived": arch})


@app.route("/api/rate", methods=["POST", "OPTIONS"])
def api_rate():
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True)
    key = f"{clean_dirname(d['album'])}/{d['name']}"
    rat = load_json(RATINGS, {})
    r = int(d.get("rating", 0))
    if r:
        rat[key] = r
    else:
        rat.pop(key, None)
    RATINGS.parent.mkdir(parents=True, exist_ok=True)
    RATINGS.write_text(json.dumps(rat, indent=0), encoding="utf-8")
    return jsonify({"ok": True, "rating": r})


def _save_picks(picks):
    PICKS.parent.mkdir(parents=True, exist_ok=True)
    tmp = PICKS.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(picks, indent=0), encoding="utf-8")
    tmp.replace(PICKS)                   # atomic: never a half-written shortlist


@app.route("/api/pick", methods=["POST", "OPTIONS"])
def api_pick():
    """Shortlist a photo for the blog, and/or write the note that goes with it.

    Send "picked" to toggle, "note" to set the note, or both. Writing a note on
    an unpicked photo picks it - typing a thought about where a photo should go
    IS the act of shortlisting it, and making that two clicks helps nobody.
    Clearing the note leaves the pick alone; unpicking drops the note too.
    """
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True)
    key = f"{clean_dirname(d['album'])}/{d['name']}"
    picks = load_json(PICKS, {})
    rec = picks.get(key)

    if "picked" in d and not d["picked"]:
        picks.pop(key, None)
        _save_picks(picks)
        return jsonify({"ok": True, "picked": False, "note": ""})

    if rec is None:
        rec = {"note": "", "at": round(time.time())}
    if "note" in d:
        rec["note"] = str(d["note"])[:2000]
    picks[key] = rec
    _save_picks(picks)
    return jsonify({"ok": True, "picked": True, "note": rec["note"]})


def ensure_backup_source():
    """Index of the ~/Backup root in SOURCES, registering it if absent.

    Blog picks live in the backup, not in one of the browse folders. Rather
    than a second import path, the editor treats the backup as just another
    photo source - so a pick reaches the crop/develop/import pipeline as the
    ordinary {root, path} pair and every existing feature works on it.
    """
    for i, s in enumerate(SOURCES):
        if Path(s["path"]).resolve() == BACKUP.resolve():
            return i
    SOURCES.append({"label": "Backup (blog picks)", "path": str(BACKUP)})
    try:
        SRC_CFG.write_text(json.dumps(SOURCES, indent=2), encoding="utf-8")
    except OSError:
        pass
    return len(SOURCES) - 1


@app.route("/api/resolve_original")
def api_resolve_original():
    """Archive original for any article img ref - web variant or original.

    The editor's Edit button needs to reopen the crop window on the ORIGINAL,
    but after the pipeline runs, an article img points at a derived variant
    ("Images/web/<C>/x-mob-2x.jpg"). Strip the web/ tier and the density
    suffix, then find the archive file case-insensitively by stem (imports
    re-encode HEIC to .jpg but byte-copies keep .JPG/.JPEG as they came).
    """
    rel = unquote(request.args.get("path", "")).replace("\\", "/").lstrip("./")
    rel = re.sub(r"^(\.\./)+", "", rel)
    if rel.startswith("Images/"):
        rel = rel[len("Images/"):]
    if rel.startswith("web/"):
        rel = rel[len("web/"):]
    rel = re.sub(r"-(?:mob-)?[123]x(?=\.[^.]+$)", "", rel)
    cand = (IMAGES / rel)
    folder, stem = cand.parent, cand.stem
    hit = None
    if folder.is_dir():
        low = stem.lower()
        for f in folder.iterdir():
            if f.is_file() and f.stem.lower() == low                     and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
                hit = f
                break
    if not hit:
        return jsonify({"ok": False, "error": f"no archive original for {rel}"}), 404
    # index of the Images/ source, so the editor can hand this straight to
    # the crop modal as an ordinary {root, path} pair
    root = None
    for idx, src in enumerate(SOURCES):
        if Path(src["path"]).resolve() == IMAGES.resolve():
            root = idx
            break
    if root is None:
        return jsonify({"ok": False, "error": "Images/ is not a photo source"}), 500
    return jsonify({"ok": True, "root": root,
                    "path": hit.relative_to(IMAGES).as_posix(), "name": hit.name})


@app.route("/api/pick_root")
def api_pick_root():
    return jsonify({"root": ensure_backup_source()})


@app.route("/api/picks")
def api_picks():
    """Every shortlisted photo, newest first - the review sidebar's whole feed.

    A pick whose file has since gone is dropped from the response but kept in
    the file: albums get re-copied under new names during an iCloud rebuild,
    and silently deleting the note would throw away the only part a person
    actually wrote.
    """
    picks = load_json(PICKS, {})
    only = request.args.get("album")          # scope to the album being reviewed
    out = []
    counts = {}
    for key, rec in picks.items():
        album, _, name = key.partition("/")
        if not (BACKUP / album / name).is_file():
            continue
        counts[album] = counts.get(album, 0) + 1
        if only and album != only:
            continue
        out.append({"album": album, "n": name,
                    "note": rec.get("note", ""), "at": rec.get("at", 0)})
    out.sort(key=lambda x: (x["album"], -x["at"]))
    # "albums" lets a picker list every album holding picks without a second
    # round trip, and stays correct when the response itself is filtered
    return jsonify({"picks": out, "total": sum(counts.values()),
                    "albums": [{"album": k, "n": v} for k, v in sorted(counts.items())]})


def bthumb_path(p, size):
    """Cache location for a backed-up photo's thumbnail. Shared with
    photo_backup.py, which pre-generates these while downloading so the
    library grid is instant instead of decoding 5712px HEICs on demand."""
    key = f"bk-{p}-{p.stat().st_mtime_ns}-{size}"
    return THUMBS / (re.sub(r"\W", "_", key)[-120:] + ".jpg")


@app.route("/bthumb")
def bthumb():
    """Thumbnail for a backed-up photo (same disk cache as /thumb)."""
    album = clean_dirname(request.args.get("album", ""))
    name = os.path.basename(request.args.get("name", ""))
    p = (BACKUP / album / name).resolve()
    if BACKUP.resolve() not in p.parents or not p.is_file():
        abort(404)
    size = int(request.args.get("s", 320))
    cache = bthumb_path(p, size)
    if not cache.exists():
        with _thumb_gate:
            if not cache.exists():
                try:
                    im = ImageOps.exif_transpose(Image.open(p))
                except Exception:
                    abort(415)
                icc = im.info.get("icc_profile")
                im.thumbnail((size, size))
                kw = {"quality": 82}
                if icc:
                    kw["icc_profile"] = icc
                im.convert("RGB").save(cache, "JPEG", **kw)
    return send_file(cache, mimetype="image/jpeg")


@app.route("/api/import", methods=["POST", "OPTIONS"])
def api_import():
    """Copy a library photo into the Images/ archive WITHOUT touching any
    page: editor.html inserts the markup itself in its contenteditable and
    only needs the archived file + its repo-relative path."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True)
    src = source_path(d["root"], d["path"])
    # A file already IN the backup is the full-resolution original - that is the
    # whole point of the backup. Re-resolving it against the library wastes a
    # lookup and, worse, find_original() matches on capture SECOND, so a burst
    # frame can come back as a DIFFERENT shot. Publish the file as it stands.
    from_backup = BACKUP.resolve() in src.resolve().parents
    if d.get("full", True) and not from_backup:
        src = resolve_full_res(src)      # publish from the original, not a 2048px copy
        hydrate_if_cloud(src)            # pull it via the API, not by blind reading
    if d.get("token"):
        # erased pixels replace the library source; orientation is already baked
        erased_src = ERASED / (re.sub(r"\W", "", d["token"]) + ".jpg")
        if not erased_src.exists():
            return jsonify({"ok": False, "error": "erase session expired"}), 410
        dest = import_photo(erased_src, country_dir(d["country"]), d.get("city", "").strip(),
                            dev=d.get("dev"), masks=d.get("masks"), name_hint=src)
    else:
        dest = import_photo(src, country_dir(d["country"]), d.get("city", "").strip(),
                            rot=d.get("rot", 0), flip=d.get("flip"), dev=d.get("dev"),
                            angle=d.get("angle", 0), masks=d.get("masks"))
    with Image.open(dest) as im:
        im = ImageOps.exif_transpose(im)
        w, h = im.size
    return jsonify({"ok": True, "src": dest.relative_to(ROOT).as_posix(),
                    "w": w, "h": h,
                    "orient": "landscape" if w >= h * 1.05 else "portrait" if h >= w * 1.05 else "square"})


@app.route("/api/blocks")
def api_blocks():
    _, ix = index_page(request.args["page"])
    return jsonify({"count": len(ix.blocks), "tags": [b["tag"] for b in ix.blocks]})


def read_page(rel):
    """Read without newline translation: offsets must map to the bytes on
    disk, and writing back must not flip CRLF files to LF."""
    return page_file(rel).read_text(encoding="utf-8", newline="")


def splice(rel, edits):
    """Apply [(start, end, replacement)] edits (descending order) to the page."""
    f = page_file(rel)
    text = read_page(rel)
    for start, end, rep in sorted(edits, reverse=True):
        text = text[:start] + rep + text[end:]
    f.write_text(text, encoding="utf-8", newline="")
    return text


@app.route("/api/insert", methods=["POST"])
def api_insert():
    d = request.get_json(force=True)
    rel = d["page"]
    text, ix = index_page(rel)
    country = country_for(rel)
    imgs = []
    for ph in d["photos"]:
        src = source_path(ph["root"], ph["path"])
        dest = import_photo(src, country, d.get("city", "").strip())
        imgs.append(img_markup(dest, rel, ph.get("alt") or clean_name(src.stem), d["kind"]))
    block = block_markup(d["kind"], imgs, rel)
    pos, chunk = ix.place(d["at"], block)
    splice(rel, [(pos, pos, chunk)])
    return jsonify({"ok": True})


@app.route("/api/pair_add", methods=["POST"])
def api_pair_add():
    d = request.get_json(force=True)
    rel = d["page"]
    text, ix = index_page(rel)
    b = ix.blocks[int(d["index"])]
    ph = d["photo"]
    src = source_path(ph["root"], ph["path"])
    dest = import_photo(src, country_for(rel), d.get("city", "").strip())
    img = img_markup(dest, rel, ph.get("alt") or clean_name(src.stem), "pair")
    close = text.rindex("</div>", b["start"], b["end"])
    splice(rel, [(close, close, PAIR_SLOT.format(img=img))])
    return jsonify({"ok": True})


@app.route("/api/remove", methods=["POST"])
def api_remove():
    d = request.get_json(force=True)
    text, ix = index_page(d["page"])
    b = ix.blocks[int(d["index"])]
    start = b["start"]
    m = re.search(r"\n[ \t]*$", text[:start])       # swallow the leading newline+indent
    if m:
        start = m.start()
    splice(d["page"], [(start, b["end"], "")])
    return jsonify({"ok": True})


@app.route("/api/move", methods=["POST"])
def api_move():
    d = request.get_json(force=True)
    text, ix = index_page(d["page"])
    src = int(d["from"])
    if not (0 <= src < len(ix.blocks)):
        return jsonify({"ok": True})
    b = ix.blocks[src]
    pos, chunk = ix.place(d["at"], text[b["start"]:b["end"]])
    if b["start"] <= pos <= b["end"]:
        return jsonify({"ok": True})
    rm_start = b["start"]
    m = re.search(r"\n[ \t]*$", text[:rm_start])
    if m:
        rm_start = m.start()
    splice(d["page"], [(rm_start, b["end"], ""), (pos, pos, chunk)])
    return jsonify({"ok": True})


@app.route("/api/pair_swap", methods=["POST"])
def api_pair_swap():
    d = request.get_json(force=True)
    text, ix = index_page(d["page"])
    b = ix.blocks[int(d["index"])]
    seg = text[b["start"]:b["end"]]
    spans = re.findall(r"<span[^>]*>.*?</span>", seg, re.S)
    if len(spans) == 2:
        seg2 = seg.replace(spans[0], "\x00").replace(spans[1], spans[0]).replace("\x00", spans[1])
        splice(d["page"], [(b["start"], b["end"], seg2)])
    return jsonify({"ok": True})


@app.route("/api/set_pos", methods=["POST"])
def api_set_pos():
    d = request.get_json(force=True)
    text, ix = index_page(d["page"])
    b = ix.blocks[int(d["index"])]
    seg = text[b["start"]:b["end"]]
    tags = list(re.finditer(r"<img\b[^>]*>", seg, re.S))
    t = tags[int(d["img"])]
    tag = t.group(0)
    pos = d["pos"]                                   # e.g. "37% 62%"
    if not re.fullmatch(r"\d{1,3}% \d{1,3}%", pos):
        abort(400)
    if "object-position" in tag:
        new = re.sub(r"object-position:[^;\"']*", f"object-position:{pos}", tag)
    elif re.search(r'style="', tag):
        new = tag.replace('style="', f'style="object-position:{pos};', 1)
    else:
        new = tag.replace("<img ", f'<img style="object-position:{pos};" ', 1)
    start = b["start"] + t.start()
    splice(d["page"], [(start, start + len(tag), new)])
    return jsonify({"ok": True})


@app.route("/api/pipeline", methods=["POST"])
def api_pipeline():
    d = request.get_json(force=True)
    if d["page"].startswith("Drafts/"):
        return jsonify({"ok": True, "log": "Draft page: pipeline runs at publish "
                                           "(preview reads the originals directly)."})
    log = []
    for tool in PIPELINE:
        args = [sys.executable, str(ROOT / "tools" / tool)]
        if tool == "recompress_desktop.py":
            args.append("--new-only")            # only build missing web variants
        r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=1800)
        tail = (r.stdout or r.stderr or "").strip().splitlines()[-3:]
        log.append(f"$ {tool}\n" + "\n".join(tail))
        if r.returncode != 0:
            return jsonify({"ok": False, "log": "\n\n".join(log)}), 500
    return jsonify({"ok": True, "log": "\n\n".join(log)})


# --------------------------------------------------------------------- UI page
Response_UI = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Photo Editor</title>
<style>
:root{--ink:#1C2821;--terra:#2D6B50;--cream:#F7F7F3;--mist:#EDEDE7;--line:#D8E5DC}
*{box-sizing:border-box;margin:0}
body{font:13px/1.45 "Segoe UI",system-ui,sans-serif;color:var(--ink);height:100vh;
     display:grid;grid-template-rows:44px 1fr;grid-template-columns:340px 1fr;overflow:hidden}
header{grid-column:1/3;display:flex;align-items:center;gap:10px;padding:0 12px;
       background:var(--ink);color:#fff}
header select,header input{font:inherit;padding:4px 8px;border:0;border-radius:4px}
header select{max-width:340px}
header input#city{width:150px}
header .btn{background:var(--terra);color:#fff;border:0;border-radius:4px;
            padding:6px 12px;cursor:pointer;font:inherit}
header .btn:disabled{opacity:.5}
#status{margin-left:auto;font-size:12px;opacity:.85;max-width:420px;white-space:nowrap;
        overflow:hidden;text-overflow:ellipsis}
aside{border-right:1px solid var(--mist);display:flex;flex-direction:column;min-height:0;background:var(--cream)}
#srcbar{display:flex;gap:4px;padding:8px;flex-wrap:wrap}
#srcbar button{font:12px inherit;border:1px solid var(--line);background:#fff;
               border-radius:12px;padding:3px 10px;cursor:pointer}
#srcbar button.on{background:var(--terra);color:#fff;border-color:var(--terra)}
#crumbs{padding:0 10px 6px;font-size:12px;color:#555;word-break:break-all}
#crumbs a{color:var(--terra);cursor:pointer;text-decoration:none}
#folders{padding:0 8px 4px;display:flex;flex-wrap:wrap;gap:4px}
#folders div{background:#fff;border:1px solid var(--mist);border-radius:4px;
             padding:3px 8px;cursor:pointer;font-size:12px}
#folders div:hover{border-color:var(--terra)}
#grid{flex:1;overflow-y:auto;padding:8px;display:grid;
      grid-template-columns:repeat(auto-fill,minmax(92px,1fr));gap:8px;align-content:start}
.ph{position:relative;aspect-ratio:1;border-radius:4px;overflow:hidden;cursor:grab;
    background:var(--mist)}
.ph img{width:100%;height:100%;object-fit:cover;display:block;pointer-events:none}
.ph .b{position:absolute;top:4px;left:4px;background:rgba(28,40,33,.75);color:#fff;
       font-size:10px;padding:1px 5px;border-radius:3px}
.ph:hover::after{content:attr(data-name);position:absolute;bottom:0;left:0;right:0;
       background:rgba(28,40,33,.8);color:#fff;font-size:10px;padding:2px 4px;
       white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
main{position:relative;min-width:0}
iframe{width:100%;height:100%;border:0;background:#fff}
#hint{position:absolute;bottom:12px;left:50%;transform:translateX(-50%);
      background:rgba(28,40,33,.9);color:#fff;padding:8px 16px;border-radius:20px;
      font-size:12px;pointer-events:none;opacity:0;transition:opacity .3s}
#hint.show{opacity:1}
dialog{border:1px solid var(--mist);border-radius:8px;padding:18px;min-width:340px}
dialog h3{margin-bottom:10px;font-size:15px}
dialog label{display:block;margin:8px 0 2px;font-size:12px;color:#555}
dialog input,dialog select{width:100%;font:inherit;padding:6px;border:1px solid var(--line);border-radius:4px}
dialog .row{display:flex;gap:8px;justify-content:flex-end;margin-top:14px}
dialog .row button{font:inherit;padding:6px 14px;border-radius:4px;border:1px solid var(--line);
                   background:#fff;cursor:pointer}
dialog .row button.go{background:var(--terra);color:#fff;border-color:var(--terra)}
#log{position:absolute;top:10px;right:10px;max-width:440px;max-height:60%;overflow:auto;
     background:rgba(28,40,33,.95);color:#cfe3d7;font:11px/1.5 Consolas,monospace;
     padding:10px 14px;border-radius:6px;white-space:pre-wrap;display:none}
</style></head><body>
<header>
  <b>Photo&nbsp;Editor</b>
  <select id="article"></select>
  <input id="city" placeholder="City folder (e.g. Antigua)" title="Subfolder inside Images/<Country>/ that imported photos are filed into">
  <button class="btn" id="pipeline">Run pipeline</button>
  <span id="status"></span>
</header>
<aside>
  <div id="srcbar"></div>
  <div id="crumbs"></div>
  <div id="folders"></div>
  <div id="grid"></div>
</aside>
<main>
  <iframe id="frame"></iframe>
  <div id="hint"></div>
</main>
<dialog id="dlg">
  <h3 id="dlgTitle">Insert photo</h3>
  <label>Layout</label><select id="dlgKind"></select>
  <label>Alt text</label><input id="dlgAlt">
  <div class="row"><button onclick="dlg.close()">Cancel</button>
  <button class="go" id="dlgGo">Insert</button></div>
</dialog>
<div id="log"></div>
<script>
const $=q=>document.querySelector(q);
let SRC=0, PATH="", ARTICLE="", metaCache={};
const hint=t=>{const h=$("#hint");h.textContent=t;h.classList.add("show");
               clearTimeout(h._t);h._t=setTimeout(()=>h.classList.remove("show"),3500)};
const api=(u,d)=>fetch(u,d?{method:"POST",headers:{"Content-Type":"application/json"},
                           body:JSON.stringify(d)}:{}).then(r=>r.json());

async function boot(){
  const st=await api("/api/state");
  const sel=$("#article");
  st.articles.forEach(a=>{const o=document.createElement("option");
    o.value=a.path;o.textContent=a.label;sel.appendChild(o)});
  const bar=$("#srcbar");
  st.sources.forEach((s,i)=>{const b=document.createElement("button");
    b.textContent=s.label;b.onclick=()=>{SRC=i;PATH="";[...bar.children].forEach(c=>c.classList.remove("on"));
      b.classList.add("on");browse()};bar.appendChild(b)});
  if(bar.firstChild)bar.firstChild.classList.add("on");
  sel.onchange=()=>loadArticle(sel.value);
  if(st.articles.length)loadArticle(st.articles[0].path);
  browse();
  if(!st.heic)hint("pillow-heif missing: HEIC files hidden (pip install pillow-heif)");
}

async function browse(){
  const d=await api(`/api/browse?root=${SRC}&path=${encodeURIComponent(PATH)}`);
  const crumbs=$("#crumbs");crumbs.innerHTML="";
  const parts=PATH?PATH.split("/"):[];
  const mk=(t,p)=>{const a=document.createElement("a");a.textContent=t;
    a.onclick=()=>{PATH=p;browse()};crumbs.appendChild(a);
    crumbs.appendChild(document.createTextNode(" / "))};
  mk("⌂","");let acc="";parts.forEach(p=>{acc=acc?acc+"/"+p:p;mk(p,acc)});
  const fol=$("#folders");fol.innerHTML="";
  d.dirs.forEach(n=>{const v=document.createElement("div");v.textContent="📁 "+n;
    v.onclick=()=>{PATH=PATH?PATH+"/"+n:n;browse()};fol.appendChild(v)});
  const g=$("#grid");g.innerHTML="";
  d.photos.forEach(n=>{
    const rel=PATH?PATH+"/"+n:n;
    const div=document.createElement("div");div.className="ph";div.draggable=true;
    div.dataset.rel=rel;div.dataset.name=n;
    const im=document.createElement("img");im.loading="lazy";
    im.src=`/thumb?root=${SRC}&path=${encodeURIComponent(rel)}`;
    div.appendChild(im);
    api(`/api/photo_meta?root=${SRC}&path=${encodeURIComponent(rel)}`).then(m=>{
      if(m.orient){metaCache[rel]=m;const b=document.createElement("span");
        b.className="b";b.textContent=m.orient==="landscape"?"H":m.orient==="portrait"?"V":"□";
        div.appendChild(b)}});
    div.addEventListener("dragstart",e=>{
      e.dataTransfer.setData("text/photo",JSON.stringify({root:SRC,path:rel,name:n}));
      e.dataTransfer.effectAllowed="copy"});
    g.appendChild(div)});
}

function loadArticle(p){
  const changed=ARTICLE!==p;
  ARTICLE=p;
  const f=$("#frame");
  if(changed)f._scroll=0;
  f.src="/site/"+p+"?t="+Date.now();
  f.onload=()=>{
    decorate(f);
    // originals load slowly and reflow the page; keep re-applying the saved
    // scroll position until layout settles so actions don't jump to the top
    const y=f._scroll||0;
    [0,250,700,1500].forEach(ms=>setTimeout(()=>{
      if(Math.abs(f.contentWindow.scrollY-y)>4)f.contentWindow.scrollTo(0,y)},ms));
  };
  if(changed){
    const cityGuess=p.split("/").pop().replace(".html","").replace("field-notes","")
                     .replace(/-/g," ").trim();
    $("#city").value=cityGuess?cityGuess.replace(/\b\w/g,c=>c.toUpperCase()):"";
  }
}

function decorate(f){
  const doc=f.contentDocument;
  const body=doc.querySelector(".article-body");
  if(!body){hint("no .article-body on this page");return}
  // Flat leaf-block walk — MUST mirror the server's PageIndex: direct children
  // of article-body, with fn-section divs replaced by their own children.
  const flat=[];            // [{el, section}]
  [...body.children].forEach(c=>{
    if(c.classList.contains("fn-section"))
      [...c.children].forEach(k=>flat.push({el:k,section:c}));
    else flat.push({el:c,section:null});
  });
  const zone=at=>{
    const z=doc.createElement("div");z.className="pe-zone";
    const rest="height:10px;margin:2px 0;border-radius:5px;transition:all .15s";
    z.style.cssText=rest;
    z.addEventListener("dragover",e=>{e.preventDefault();
      z.style.cssText=rest+";height:34px;background:#2D6B5033;border:2px dashed #2D6B50"});
    z.addEventListener("dragleave",()=>z.style.cssText=rest);
    z.addEventListener("drop",e=>{e.preventDefault();onDrop(e,at)});
    return z};
  flat.forEach((b,i)=>{
    const k=b.el;
    k.parentElement.insertBefore(zone({before:i}),k);
    if(k.classList.contains("img-pair")||k.classList.contains("img-landscape")){
      k.addEventListener("dragover",e=>{if(k.classList.contains("img-pair")&&
        k.querySelectorAll("img").length<2){e.preventDefault();e.stopPropagation();
        k.style.outline="3px dashed #2D6B50"}});
      k.addEventListener("dragleave",()=>k.style.outline="");
      k.addEventListener("drop",e=>{k.style.outline="";
        if(k.classList.contains("img-pair")&&k.querySelectorAll("img").length<2){
          e.preventDefault();e.stopPropagation();onPairAdd(e,i)}});
      attachTools(doc,k,i);
    }
    k.querySelectorAll("img").forEach((img,j)=>attachPan(f,img,i,j));
  });
  // trailing zone at the end of each section, and at the end of the body
  const lastIn=parent=>{for(let i=flat.length-1;i>=0;i--)
    if(flat[i].section===parent||(!parent&&!flat[i].section&&flat[i].el.parentElement===body))
      return i;return -1};
  [...body.children].filter(c=>c.classList.contains("fn-section")).forEach(sec=>{
    const i=lastIn(sec);
    if(i>=0)sec.appendChild(zone({after:i}));
  });
  body.appendChild(zone({end:true}));
  f.contentWindow.addEventListener("scroll",()=>f._scroll=f.contentWindow.scrollY);
}

function attachTools(doc,k,i){
  k.style.position=k.style.position||"relative";
  const bar=doc.createElement("div");
  bar.style.cssText="position:absolute;top:6px;right:6px;z-index:60;display:none;gap:4px";
  const mk=(t,title,fn)=>{const b=doc.createElement("button");b.textContent=t;b.title=title;
    b.style.cssText="font:12px sans-serif;border:0;border-radius:4px;padding:4px 8px;"+
      "background:rgba(28,40,33,.85);color:#fff;cursor:pointer";
    b.onclick=e=>{e.stopPropagation();fn()};bar.appendChild(b)};
  mk("↑","Move up",()=>mut("/api/move",{page:ARTICLE,from:i,at:{before:Math.max(0,i-1)}}));
  mk("↓","Move down",()=>mut("/api/move",{page:ARTICLE,from:i,at:{after:i+1}}));
  if(k.classList.contains("img-pair"))mk("⇄","Swap pair",()=>mut("/api/pair_swap",{page:ARTICLE,index:i}));
  mk("✕","Remove block",()=>{if(confirm("Remove this image block? (The photo stays in Images/)"))
    mut("/api/remove",{page:ARTICLE,index:i})});
  bar.style.display="none";
  k.appendChild(bar);
  k.addEventListener("mouseenter",()=>bar.style.display="flex");
  k.addEventListener("mouseleave",()=>bar.style.display="none");
}

function attachPan(f,img,i,j){
  let drag=null;
  img.style.cursor="move";
  img.addEventListener("mousedown",e=>{
    e.preventDefault();
    const cs=f.contentWindow.getComputedStyle(img);
    const m=/(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%/.exec(cs.objectPosition)||[0,50,50];
    drag={x:e.clientX,y:e.clientY,px:+m[1],py:+m[2]};
    const move=ev=>{
      const r=img.getBoundingClientRect();
      let px=drag.px-(ev.clientX-drag.x)/r.width*100;
      let py=drag.py-(ev.clientY-drag.y)/r.height*100;
      px=Math.max(0,Math.min(100,px));py=Math.max(0,Math.min(100,py));
      img.style.objectPosition=`${px.toFixed(0)}% ${py.toFixed(0)}%`;
      drag.cur=[px.toFixed(0),py.toFixed(0)]};
    const up=()=>{f.contentDocument.removeEventListener("mousemove",move);
      f.contentDocument.removeEventListener("mouseup",up);
      if(drag.cur)api("/api/set_pos",{page:ARTICLE,index:i,img:j,
        pos:`${drag.cur[0]}% ${drag.cur[1]}%`}).then(()=>hint("crop saved"));
      drag=null};
    f.contentDocument.addEventListener("mousemove",move);
    f.contentDocument.addEventListener("mouseup",up);
  });
}

const dlg=$("#dlg");
function onDrop(e,at){
  const raw=e.dataTransfer.getData("text/photo");if(!raw)return;
  const ph=JSON.parse(raw);
  const meta=metaCache[ph.path]||{orient:"landscape"};
  const kindSel=$("#dlgKind");kindSel.innerHTML="";
  const opts=meta.orient==="portrait"
    ?[["pair","Portrait pair (drop 2nd photo onto the empty half)"],["landscape","Full-width landscape frame"]]
    :[["landscape","Full-width landscape frame"],["pair","Portrait pair slot"]];
  opts.forEach(([v,t])=>{const o=document.createElement("option");o.value=v;o.textContent=t;
    kindSel.appendChild(o)});
  $("#dlgAlt").value=ph.name.replace(/\.[^.]+$/,"");
  $("#dlgTitle").textContent="Insert "+ph.name;
  $("#dlgGo").onclick=()=>{dlg.close();
    mut("/api/insert",{page:ARTICLE,at,kind:kindSel.value,city:$("#city").value,
      photos:[{root:ph.root,path:ph.path,alt:$("#dlgAlt").value}]})};
  dlg.showModal();
}
function onPairAdd(e,index){
  const raw=e.dataTransfer.getData("text/photo");if(!raw)return;
  const ph=JSON.parse(raw);
  mut("/api/pair_add",{page:ARTICLE,index,city:$("#city").value,
    photo:{root:ph.root,path:ph.path,alt:ph.name.replace(/\.[^.]+$/,"")}});
}

async function mut(url,data){
  $("#status").textContent="saving…";
  const r=await api(url,data);
  $("#status").textContent=r.ok?"saved":"error";
  if(r.ok)loadArticle(ARTICLE);else hint(JSON.stringify(r));
}

$("#pipeline").onclick=async()=>{
  const b=$("#pipeline");b.disabled=true;$("#status").textContent="pipeline running…";
  const r=await api("/api/pipeline",{page:ARTICLE});
  b.disabled=false;$("#status").textContent=r.ok?"pipeline done":"pipeline FAILED";
  const log=$("#log");log.textContent=r.log;log.style.display="block";
  setTimeout(()=>log.style.display="none",12000);
  loadArticle(ARTICLE);
};

boot();
</script></body></html>"""


if __name__ == "__main__":
    # 127.0.0.1 rather than localhost: localhost resolves ::1 first on this
    # box (Bonjour) and every request pays a ~2s IPv6 timeout
    print(f"Photo editor: http://127.0.0.1:5003   sources: {[s['label'] for s in SOURCES]}")
    # threaded: thumbnail generation must not block imports/saves behind it
    app.run(port=5003, debug=False, threaded=True)
