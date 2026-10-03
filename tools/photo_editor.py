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
import functools
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import unicodedata
from html.parser import HTMLParser
from urllib.parse import unquote
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory, Response

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

# The shared-album sync leaves orphaned video poster frames behind, named
# <hash>.jpgthumb_00001.jpg -- 670 of them, all 480px, none with an original in
# its album. They are .jpg, so an extension filter alone lets them through and
# they show up as unusable tiles. Videos are already excluded by extension.
THUMB_RE = re.compile(r"\.[A-Za-z0-9]+thumb_\d+\.[A-Za-z0-9]+$")


def is_stray_thumb(name):
    """True for a sync artifact that can never be used as a photo."""
    return bool(THUMB_RE.search(str(name)))

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


def esc(s):
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


PAIR_SLOT = ('<span style="display:block;position:relative;overflow:hidden;'
             'width:100%;aspect-ratio:2/3;border-radius:2px;">{img}</span>')


# ---------------------------------------------------------------------- routes
@app.route("/")
def ui():
    # the standalone photo tool that lived here was retired 2026-09-27; the article editor
    # (with the photo library inside it) is the one place to work now
    from flask import redirect
    return redirect("/browser")


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
                elif (os.path.splitext(e.name)[1].lower() in IMG_EXTS
                      and not is_stray_thumb(e.name)):
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
        with Image.open(p) as im:            # the header only: exif_transpose() decoded the whole HEIC (1-8 s)
            w, h = im.size
            try:
                if int(im.getexif().get(0x0112, 1) or 1) in (5, 6, 7, 8):
                    w, h = h, w                  # stored sideways: upright it is the other way round
            except Exception:
                pass
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


@app.route("/api/geo")
def api_geo():
    """Where the photos in one folder were taken, in the site's place names.

    ?album=<Backup album>  or  ?root=<source idx>&path=<subfolder>. Returns the
    per-photo place plus grouped counts for a filter menu; `pending` > 0 means
    EXIF is still being read in the background and the client should poll.
    See tools/photo_geo.py.
    """
    import photo_geo
    if request.args.get("album") is not None:
        d = BACKUP / clean_dirname(request.args["album"])
    else:
        d = source_path(request.args.get("root", 0), request.args.get("path", ""))
    if not d.is_dir():
        return jsonify({"photos": {}, "groups": [], "noGps": 0, "pending": 0, "located": 0, "total": 0})
    files = []
    try:
        with os.scandir(d) as it:
            for e in it:
                if (e.is_file() and not e.name.startswith((".", "_"))
                        and os.path.splitext(e.name)[1].lower() in IMG_EXTS
                        and not is_stray_thumb(e.name)):
                    files.append(Path(e.path))
    except OSError:
        pass
    return jsonify(photo_geo.folder_geo(files))


_preview_gate = threading.Semaphore(2)   # the 2000px viewer images: their own lane (2026-09-28)
_thumb_gate = threading.Semaphore(4)   # decode a few at a time; a burst of iCloud
                                       # HEICs must not starve browse/import calls


@app.route("/thumb")
def thumb():
    # A missing or unreadable path is the caller's mistake, not a server fault. This used to
    # raise out of source_path/stat and answer 500, so the UI got an opaque failure where a
    # 404 it could actually show was the right answer.
    if not request.args.get("root") or not request.args.get("path"):
        abort(400)
    try:
        p = source_path(request.args["root"], request.args["path"])
        p.stat()
    except FileNotFoundError:
        abort(404)
    except (ValueError, KeyError, OSError):
        abort(400)
    size = int(request.args.get("s", 320))
    rot = int(request.args.get("rot", 0)) % 360
    flip = request.args.get("flip") or None
    key = f"{p}-{p.stat().st_mtime_ns}-{size}-{rot}-{flip}"
    cache = THUMBS / (re.sub(r"\W", "_", key)[-120:] + ".jpg")
    # A backed-up photo is decoded ONCE, into the library's 400 px copy, and every smaller grid tile
    # is cut from that (Kevin, 2026-10-02: 13,773 of the albums' photos are HEIC originals with no JPEG
    # twin, and the sidebar's 320 px tile and the library's 400 px one each decoded the full 24 MP file)
    shared = None
    if size <= 2000 and not rot and not flip:
        try:
            if BACKUP.resolve() in p.parents:
                shared = _build_bthumb(p, 400 if size <= 400 else 2000)
        except Exception:
            shared = None
    if not cache.exists():
        with _thumb_gate:
            if not cache.exists():
                try:
                    im = Image.open(shared) if shared else ImageOps.exif_transpose(Image.open(p))
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
    d = request.get_json(force=True) or {}
    # Same as /api/import: a body with neither a token nor a photo to work on is a 400.
    if not isinstance(d, dict) or not (d.get("token") or d.get("path")):
        abort(400)
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
    """Read a JSON file. A file that is being replaced under a reader (Windows
    refuses the rename for an instant) or is mid-write reads as broken for a
    moment, so retry briefly before giving up."""
    for i in range(4):
        try:
            return json.loads(p.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return default
        except Exception:
            time.sleep(0.05 * (i + 1))
    return default


_meta_lock = threading.RLock()


def load_json_for_write(p, default):
    """Like load_json, but a file that EXISTS and cannot be parsed raises
    instead of reading as empty. Every writer does write(load() + change), so
    one unreadable read used to persist `{}` - a 34-pick shortlist and the
    ratings file both went that way under two concurrent saves."""
    if not p.exists() or p.stat().st_size == 0:
        return default
    last = None
    for i in range(6):
        try:
            return json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception as e:
            last = e
            time.sleep(0.05 * (i + 1))
    raise RuntimeError(f"{p.name} is unreadable, refusing to overwrite it: {last}")


def save_json(p, obj, indent=1):
    """Atomic write: temp file + os.replace, retried while a concurrent reader
    holds the target open (Windows refuses the rename for that instant)."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, indent=indent), encoding="utf-8")
    for _ in range(40):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, p)


def _serialized(fn):
    """Meta-file writers (ratings, picks, counts, hold, archived) run one at
    a time and never write over a file they could not read. The server is
    threaded, and two rating clicks landing together lost one of them - or,
    when one read caught the other's rename, wiped the file."""
    @functools.wraps(fn)
    def w(*a, **k):
        with _meta_lock:
            try:
                return fn(*a, **k)
            except RuntimeError as e:
                return jsonify({"ok": False, "error": str(e)}), 500
    return w


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


# editor.html links its fonts and icons by relative path, which resolve against /browser and so
# against the server root; without these the editor rendered in the system fallback font
@app.route("/fonts.css")
def fonts_css():
    return send_file(ROOT / "fonts.css")


@app.route("/fonts/<path:name>")
def font_file(name):
    return send_from_directory(ROOT / "fonts", name)


@app.route("/Images/<path:rel>")
def image_file(rel):
    return send_from_directory(ROOT / "Images", rel)


# ---- redline review of AI edits ------------------------------------------------
# Claude writes a proposal to .tmp/redline/<slug>/ instead of editing an article, then opens
# /redline/<slug> in a NEW tab. Served here so the page gets the site's real stylesheets and
# photos through /site/, which the standalone preview could not. A separate tab, so an article
# open in the editor is untouched until Apply, which also backs the originals up.
import redline as _redline
# the editor's Launch tab: the prelaunch checklist (tools/prelaunch.py, 2026-09-30)
import prelaunch as _prelaunch
_prelaunch.register(app)


# ---- the review inside the editor (review.js) -----------------------------------
@app.route("/review.js")
def review_js():
    # editor.html is served from /browser, a route, so a relative review.js has nowhere to resolve
    return send_file(ROOT / "review.js", mimetype="application/javascript")


@app.route("/review/for")
def review_for():
    rel = request.args.get("rel", "")
    if not rel:
        abort(400)
    return jsonify(_redline.review_state(rel))


@app.route("/review/<slug>/decisions", methods=["POST", "OPTIONS"])
def review_decisions(slug):
    if request.method == "OPTIONS":
        return "", 204
    if not (_redline.STORE / slug / "proposal.json").exists():
        abort(404)
    # Accept all posts one decision per change at once; unserialized, two requests read the
    # file while a third was writing it and got 500 (JSONDecodeError), 2026-09-27
    with _decisions_lock:
        return jsonify({"ok": True, "decisions": _redline.merge_decisions(slug, request.get_json(force=True) or {})})


_decisions_lock = threading.Lock()


@app.route("/review/<slug>/commit", methods=["POST", "OPTIONS"])
def review_commit(slug):
    if request.method == "OPTIONS":
        return "", 204
    if not (_redline.STORE / slug / "proposal.json").exists():
        abort(404)
    d = request.get_json(force=True) or {}
    return jsonify(_redline.commit(slug, d.get("accepted", [])))


@app.route("/review/lint", methods=["POST", "OPTIONS"])
def review_lint():
    """every prose check on the HTML the editor holds (unsaved edits included)"""
    if request.method == "OPTIONS":
        return "", 204
    import prose_check
    d = request.get_json(force=True) or {}
    return jsonify(prose_check.check_full(d.get("html") or ""))


def _env_key(name):
    """a key from the repo's .env (the only place secrets live) or the environment"""
    if os.environ.get(name):
        return os.environ[name]
    f = Path(ROOT) / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""




@app.route("/review/resolve-maps", methods=["POST", "OPTIONS"])
def review_resolve_maps():
    """Maps search placeholders the editor can see -> real place URLs. The resolver drives a
    browser, so it runs as its own process (tools/resolve_draft_maps.py --queries) and the
    editor rewrites its own DOM with the answer; nothing on disk is touched."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True) or {}
    qs = [q for q in (d.get("queries") or []) if isinstance(q, str) and q.strip()][:200]
    if not qs:
        return jsonify({"urls": {}})
    tmp = ROOT / ".tmp" / "resolve_queries.json"
    tmp.write_text(json.dumps(qs, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "resolve_draft_maps.py"), "--queries", str(tmp)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    line = (r.stdout or "").strip().splitlines()
    try:
        urls = json.loads(line[-1]) if line else {}
    except Exception:
        return jsonify({"ok": False, "log": (r.stdout + r.stderr)[-1500:]}), 500
    return jsonify({"ok": True, "urls": urls})


_PREVIEWS = {}       # rel -> html the editor holds right now, served back under the page's own path


@app.route("/preview", methods=["POST", "OPTIONS"])
def preview_put():
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True) or {}
    # Types, not just truthiness. `{"rel": ["a"]}` reached .replace() and came back a 500;
    # a request whose shape is wrong is the caller's mistake and deserves a 400.
    rel, html = d.get("rel"), d.get("html")
    if not isinstance(rel, str) or not isinstance(html, str):
        abort(400)
    rel = rel.replace("\\", "/").lstrip("./")
    if not rel or not html:
        abort(400)
    _PREVIEWS[rel] = html
    return jsonify({"ok": True, "url": "/preview/" + rel})


@app.route("/preview/<path:rel>")
def preview_get(rel):
    """The unsaved article as a page. A <base> pointing at the page's real folder makes every
    relative path (../styles.css, ../../../Images/...) resolve exactly as it will on disk."""
    html = _PREVIEWS.get(rel)
    if html is None:
        abort(404)
    base = "/site/" + rel.rsplit("/", 1)[0] + "/" if "/" in rel else "/site/"
    tag = '<base href="%s">' % base
    html = re.sub(r"(<head[^>]*>)", r"\1" + tag, html, count=1) if "<head" in html else tag + html
    # the editor tells the preview which paragraph the caret is in; the preview scrolls to it
    follow = ("<script>addEventListener('message',function(e){var d=e.data||{};if(d.type!=='pv-scroll'||!d.text)return;"
              "var t=d.text.slice(0,60),els=document.querySelectorAll('.article-body p,.article-body li,.article-body h2,.article-body h3,.artbody p,.artbody li');"
              "for(var i=0;i<els.length;i++){if(els[i].textContent.indexOf(t)>=0){els[i].scrollIntoView({block:'center'});"
              "els[i].style.transition='background .6s';els[i].style.background='rgba(255,243,176,.7)';"
              "(function(el){setTimeout(function(){el.style.background=''},900)})(els[i]);break;}}});"
              "if(parent!==window)parent.postMessage({type:'pv-ready'},'*');</script>")
    html = html.replace("</body>", follow + "</body>") if "</body>" in html else html + follow
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/comments/<key>", methods=["GET", "POST", "OPTIONS"])
def comments(key):
    if request.method == "OPTIONS":
        return "", 204
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", key):
        abort(400)
    if request.method == "POST":
        data = request.get_json(force=True) or {"threads": []}
        data = _redline.comments_save(key, data)
        _answer_tasks(key, data)
        return jsonify({"ok": True, "threads": data.get("threads", [])})
    return jsonify(_redline.comments_load(key))


_answering = set()


@app.route("/api/tasks/stream")
def api_tasks_stream():
    """Server-sent events: one 'changed' whenever the comment store is written, so an answer
    reaches the editor the moment it lands instead of on the next 1 s poll. A heartbeat every
    15 s keeps the connection open through proxies and sleep."""
    def gen():
        last, beat = None, time.time()
        yield "retry: 2000\n\n"
        while True:
            try:
                m = max((f.stat().st_mtime_ns for f in _redline.COMMENTS.glob("*.json")), default=0)
            except Exception:
                m = 0
            if last is not None and m != last:
                yield "data: changed\n\n"
            last = m
            if time.time() - beat > 15:
                beat = time.time(); yield ": beat\n\n"
            time.sleep(0.3)
    resp = Response(gen(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/api/publish", methods=["POST", "OPTIONS"])
def api_publish():
    """Publish a Drafts/<country>/field-notes.html with tools/publish_country.py. dry=true
    (the default) only reports what would change; the editor shows that log before it lets
    the real run happen. Nothing is pushed: publishing ends in the working tree."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True) or {}
    slug = re.sub(r"[^a-z0-9-]", "", (d.get("slug") or "").lower())
    name, iso2, cont = (d.get("name") or "").strip(), re.sub(r"[^a-z]", "", (d.get("iso2") or "").lower()), d.get("continent") or ""
    if not slug or not name or len(iso2) != 2 or cont not in ("europe", "americas", "asia", "africa", "oceania"):
        return jsonify({"ok": False, "log": "Needs the country's name, its two-letter code and a continent."}), 400
    if not (Path(ROOT) / "Drafts" / slug / "field-notes.html").exists():
        return jsonify({"ok": False, "log": "No Drafts/%s/field-notes.html to publish." % slug}), 400
    args = [sys.executable, str(Path(ROOT) / "tools" / "publish_country.py"), slug, "--name", name, "--iso2", iso2, "--continent", cont]
    if d.get("dry", True):
        args.append("--dry-run")
    r = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _articles_cache["at"] = 0
    return jsonify({"ok": r.returncode == 0, "dry": bool(d.get("dry", True)), "log": ((r.stdout or "") + (r.stderr or ""))[-6000:]})


class _ClaudeWorker:
    """One warm Claude Code CLI (the VS Code extension's claude.exe, Kevin's login) kept
    alive in stream-json mode. A fresh process per task cost ~10 s of boot for ~2 s of model
    time; a warm one answers in about a second. The standing brief (rules, voice guide) goes
    in once; each task is a short message. The worker is restarted after 15 answers or 25 min
    idle so the conversation never grows into a slow, expensive one."""
    MAX_ANSWERS, MAX_IDLE = 15, 25 * 60

    def __init__(self):
        self.p = None; self.n = 0; self.last = 0; self.lock = threading.Lock()

    def _start(self):
        import claude_answer
        exe = claude_answer.cli_path()
        if not exe:
            return False
        work = Path(ROOT) / ".tmp" / "claude_cli"; work.mkdir(parents=True, exist_ok=True)
        nomcp = work / "no-mcp.json"
        if not nomcp.exists():
            nomcp.write_text('{"mcpServers": {}}', encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        model = _env_key("ANSWER_MODEL") or "sonnet"     # measured 2026-09-27: Sonnet 1.5-3.5 s per answer, Haiku 8-15 s (it thinks for 1k tokens)
        self.p = subprocess.Popen([exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                                   "--model", model, "--strict-mcp-config", "--mcp-config", str(nomcp)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                  encoding="utf-8", errors="replace", cwd=str(work), env=env, bufsize=1,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.n = 0; self.last = time.time()
        # the brief, once; its reply is discarded
        self._ask(claude_answer.guide_text() + "\n\nReply with the single word READY.", 120)
        return True

    def _ask(self, text, timeout):
        self.p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"); self.p.stdin.flush()
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("worker exited")
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("type") == "result":
                return j.get("result") or ""
        raise RuntimeError("worker timed out")

    def stop(self):
        try:
            if self.p: self.p.stdin.close(); self.p.terminate()
        except Exception:
            pass
        self.p = None

    def alive(self):
        return self.p is not None and self.p.poll() is None

    def warm(self):
        with self.lock:
            if not self.alive():
                try: self._start()
                except Exception: self.stop()

    def answer(self, key, tid):
        import claude_answer
        with self.lock:
            if self.alive() and (self.n >= self.MAX_ANSWERS or time.time() - self.last > self.MAX_IDLE):
                self.stop()
            if not self.alive() and not self._start():
                return False
            t, html, text = claude_answer.task_text(key, tid)
            try:
                txt = self._ask(text, 180)
            except Exception as e:
                self.stop()
                claude_answer.write_answer(key, tid, "Claude's worker dropped mid-answer (%s); send it again." % str(e)[:80], status="failed")
                return True
            self.n += 1; self.last = time.time()
            claude_answer.finish(key, tid, t, html, txt)
            return True


_worker = _ClaudeWorker()


def _answer_safely(key, tid):
    """the worker's errors reach the thread as a failed status instead of a silent forever-wait"""
    try:
        _worker.answer(key, tid)
    except Exception as e:
        try:
            import claude_answer
            claude_answer.write_answer(key, tid, "Couldn't answer this one: %s. Edit the comment and send it again." % str(e)[:140], status="failed")
        except Exception:
            pass
    finally:
        _answering.discard(tid)


def _answer_tasks(key, data):
    """A thread sent to Claude is answered here: through the API when .env holds
    ANTHROPIC_API_KEY (tools/claude_answer.py --ask), else by the warm Claude Code worker.
    The edit lands on the thread for the editor's poll to show as a tracked change."""
    for t in data.get("threads", []):
        if t.get("kind") in ("task", "rewrite") and t.get("status") == "sent" and not t.get("edit") and t["id"] not in _answering:
            _answering.add(t["id"])
            if _env_key("ANTHROPIC_API_KEY"):
                log = open(Path(ROOT) / ".tmp" / "claude_answer.log", "a", encoding="utf-8")
                subprocess.Popen([sys.executable, str(Path(ROOT) / "tools" / "claude_answer.py"), key, t["id"], "--ask"],
                                 cwd=str(ROOT), stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                threading.Thread(target=_answer_safely, args=(key, t["id"]), daemon=True).start()


def _task_watch():
    """Armed with the server, so it is armed when the editor starts (Kevin, 2026-09-27): every
    2 s, any task sent from the margin that nobody has answered is handed to _answer_tasks.
    Test stores (test__*) are left alone."""
    while True:
        try:
            for f in (_redline.COMMENTS.glob("*.json") if _redline.COMMENTS.exists() else []):
                if f.stem.startswith("test__") or f.stem == "x":
                    continue
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                _answer_tasks(f.stem, data)
        except Exception:
            pass
        time.sleep(2)


if os.environ.get("PHOTO_EDITOR_NO_WORKER") != "1":
    threading.Thread(target=_task_watch, daemon=True, name="task-watch").start()
    if not _env_key("ANTHROPIC_API_KEY"):
        threading.Thread(target=_worker.warm, daemon=True, name="claude-warm").start()   # boot the worker with the editor
        # and the alt-text writer, so the first photo is quick (after a pause: _alt is defined further down this file)
        threading.Thread(target=lambda: (time.sleep(8), _alt().warm()), daemon=True, name="alt-warm").start()


# Live Activity polls this every 2 s while the panel is open, and the scan below walks
# every album folder under ~/Backup and every placeholder under iCloudPhotos/Shared, which
# takes 6 to 8 s on this box. Answered on the request thread, each poll outlasted the next,
# the threads piled up, and the photo library stopped loading ("photo editor is having
# trouble loading", 2026-09-26). The same fix backup_status got: one daemon thread scans
# every 3 s, and a request only ever returns the last snapshot.
_act_cache = {"v": None}


def _backup_activity_refresh():
    while True:
        try:
            _act_cache["v"] = _backup_activity_compute()
        except Exception as e:
            print("backup_activity compute failed:", e)
        time.sleep(3)


@app.route("/api/backup_activity")
def api_backup_activity():
    v = _act_cache["v"]
    if v is None:
        return jsonify({"todo": 0, "throttled": False, "throttleLeftSec": 0, "throttleTrips": 0, "running": False,
                        "watcherAlive": True, "album": None, "stage": None, "pass": {}, "inFlight": [],
                        "activeCount": 0, "orphanCutoffMin": 30, "warming": True})
    return jsonify(v)


def _backup_activity_compute():
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
            if not d.is_dir() or d.name.startswith("_") or d.name.endswith(".replacing"):   # ingest staging is not an album
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
    skip = {"Houston Trip", "New Zealand", "tomorrowland x bvi", "Sean’s Wedding"}
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
    return {
        "todo": todo,
        "throttled": thr_left > 0, "throttleLeftSec": thr_left,
        "throttleTrips": thr.get("trips", 0),
        "running": running_album is not None,
        "watcherAlive": (now - st.get("updated", 0)) < 200,
        "album": running_album, "stage": stage, "pass": pass_info,
        "inFlight": in_flight, "activeCount": active,
        "orphanCutoffMin": 30,       # sweep_parts() threshold, for the UI's benefit
    }


def _compute_backup_status():
    st = load_json(BACKUP / "_meta" / "status.json", {"albums": {}})
    shared = Path.home() / "iCloudPhotos" / "Shared"
    out = []
    names = set(st.get("albums", {}))
    if BACKUP.is_dir():
        names |= {d.name for d in BACKUP.iterdir() if d.is_dir() and not d.name.startswith("_") and not d.name.endswith(".replacing")}
    if shared.is_dir():
        names |= {d.name for d in shared.iterdir()
                  if d.is_dir() and d.name not in
                  {"Houston Trip", "New Zealand", "tomorrowland x bvi", "Sean’s Wedding"}}
    import re as _re
    SKIP_ALBUMS = {"Houston Trip", "New Zealand", "tomorrowland x bvi", "Sean’s Wedding"}

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
                 and p.suffix.lower() in IMG_EXTS
                 and not is_stray_thumb(p.name)] if d.is_dir() else []
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
        stems, vstems = set(), set()
        if shared.is_dir():
            for sd in shared.iterdir():
                if not sd.is_dir() or _base(sd.name) != n:
                    continue
                for p in sd.iterdir():
                    if (p.is_file() and p.suffix.lower() in IMG_EXTS
                            and not is_stray_thumb(p.name)):
                        stems.add(_re.sub(r"_\d+$", "", p.stem))
                    elif p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".m4v"}:
                        vstems.add(_re.sub(r"_\d+$", "", p.stem))
        a["inAlbum"] = len(stems)
        # photos + videos, for the card's count before the watcher has verified
        # the album (verify's albumItems is the exact figure once it has)
        a["inAlbumItems"] = len(stems) + len(vstems)
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
    # "running" is the watcher's own flag; a watcher that died mid-pass leaves
    # it true forever (it read "backup running" off a 15-day-old file), so it
    # only counts while the heartbeat is fresh - the same test the activity
    # panel applies.
    now = time.time()
    running = bool(st.get("running", False)) and (now - st.get("updated", 0)) < 200
    return {"albums": out, "running": running, "updated": st.get("updated", 0)}


# The compute above stats ~13k backup files and walks ~56k iCloud placeholders:
# 3-4 s idle, a minute under load. The library polls it every 6 s, and computed
# per request ten calls piled up in one tab and starved every other route. So:
# computed once up front, then refreshed on a background thread at most every
# BS_TTL seconds, and every request is answered from the last result at once.
_bs = {"v": None, "t": 0.0, "busy": False, "lock": threading.Lock()}
BS_TTL = 6


def _refresh_backup_status():
    try:
        v = _compute_backup_status()
        _bs["v"], _bs["t"] = v, time.time()
    except Exception as e:
        print("backup_status compute failed:", e)
    finally:
        _bs["busy"] = False


def _bs_patch(album, **fields):
    """A count/hold/tick-off just changed: fix the cached card straight away
    rather than showing the old value until the next refresh."""
    with _bs["lock"]:
        v = _bs["v"]
        if v:
            for a in v["albums"]:
                if a["name"] == album:
                    a.update(fields)
        _bs["t"] = 0.0                      # and recompute on the next poll


@app.route("/api/backup_status")
def api_backup_status():
    with _bs["lock"]:
        if _bs["v"] is None and not _bs["busy"]:
            _bs["busy"] = True
            mode = "compute"
        elif _bs["v"] is None:
            mode = "wait"
        else:
            mode = "serve"
            if time.time() - _bs["t"] > BS_TTL and not _bs["busy"]:
                _bs["busy"] = True
                threading.Thread(target=_refresh_backup_status, daemon=True).start()
    if mode == "compute":
        _refresh_backup_status()
    elif mode == "wait":
        for _ in range(900):
            if _bs["v"] is not None:
                break
            time.sleep(0.1)
    return jsonify(_bs["v"] or {"albums": [], "running": False, "updated": 0})


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
                or p.suffix.lower() not in IMG_EXTS
                or is_stray_thumb(p.name)):
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
    warm_library(album, [BACKUP / album / x["n"] for x in photos])
    return jsonify({"album": album, "photos": photos})


EXPECTED = BACKUP / "_meta" / "expected.json"


@app.route("/api/expected", methods=["POST", "OPTIONS"])
@_serialized
def api_expected():
    """The item count YOU see on your iPhone for an album. It is the only
    trustworthy definition of 'complete' — iCloud pauses an album mid-upload
    while it works on others, so 'stopped growing' means nothing."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True)
    exp = load_json_for_write(EXPECTED, {})
    n = str(d.get("count", "")).strip()
    # an empty box clears the count; anything else must be a plain whole
    # number (0, -5 and 1e3 used to silently delete the stored count)
    if n == "":
        exp.pop(d["album"], None)
        val = None
    elif n.isdigit() and 0 < int(n) <= 10_000_000:
        val = exp[d["album"]] = int(n)
    else:
        return jsonify({"ok": False, "error": "Enter a whole number of items, like 119."}), 400
    save_json(EXPECTED, exp)
    _bs_patch(d["album"], expected=val)
    return jsonify({"ok": True, "expected": exp})


HOLD = BACKUP / "_meta" / "hold.json"


# ---- the suite's own controls: pause/resume the watcher, fix iCloud sync ----
# The editor's Live Activity panel drives these so there is one launcher file
# (Editor Suite.cmd) instead of three. The work lives in tools/photo_suite.py;
# long actions run on a thread and the panel polls the job log.
import photo_suite
_suite_job = {"name": None, "log": [], "done": True, "code": None, "started": 0}
_suite_lock = threading.Lock()
_suite_cache = {"t": 0, "v": None}


def _suite_status():
    """Never computed on a request. photo_suite.status() shells out to PowerShell for the
    watcher pids, and on this box that takes 5 to 8 s, not the 0.5 it once did. The
    editor polls /api/suite every 2 s while Live Activity is open, so every request thread
    was sitting in PowerShell and the photo library stopped answering ("photo editor is
    having trouble loading", 2026-09-26). The status is refreshed by one daemon thread
    every 15 s; a request only ever reads the last value."""
    if _suite_cache["v"] is None:
        _suite_cache["v"] = {"server": True, "heroes": photo_suite.port_open(5004),
                             "maps": photo_suite.port_open(5002), "watcher": True, "watcherPids": []}
    return _suite_cache["v"]


def _suite_refresh():
    while True:
        try:
            _suite_cache["v"] = photo_suite.status()
            _suite_cache["t"] = time.time()
        except Exception:
            pass
        time.sleep(15)


threading.Thread(target=_suite_refresh, daemon=True, name="suite-status").start()
threading.Thread(target=_backup_activity_refresh, daemon=True, name="backup-activity").start()


def _suite_run(action):
    log = _suite_job["log"]
    try:
        if action == "pause":
            pids = photo_suite.pause()
            log.append(f"Backup paused (stopped {len(pids)} process(es)). "
                       "Nothing is lost; resume picks up where it left off.")
            code = 0
        elif action == "resume":
            log.append("Backup watcher started." if photo_suite.resume()
                       else "Backup watcher was already running.")
            code = 0
        elif action == "fix":
            code = photo_suite.fix_icloud(log.append)
        else:
            log.append(f"unknown action {action!r}")
            code = 2
    except Exception as e:                      # the panel must always hear back
        log.append(f"failed: {e}")
        code = 1
    try:                                        # an action changed the state; show it now
        _suite_cache["v"] = photo_suite.status()
    except Exception:
        pass
    _suite_job["code"] = code
    _suite_job["done"] = True


@app.route("/api/suite", methods=["GET", "POST", "OPTIONS"])
def api_suite():
    if request.method == "OPTIONS":
        return "", 204
    if request.method == "POST":
        action = (request.get_json(force=True) or {}).get("action")
        if action not in ("pause", "resume", "fix"):
            abort(400)
        with _suite_lock:
            if not _suite_job["done"]:
                return jsonify({"ok": False, "busy": _suite_job["name"]}), 409
            _suite_job.update({"name": action, "log": [], "done": False,
                               "code": None, "started": time.time()})
            threading.Thread(target=_suite_run, args=(action,), daemon=True).start()
        return jsonify({"ok": True, "job": _suite_job})
    return jsonify({"status": _suite_status(), "job": _suite_job, "tidy": _tidy_preview(),
                    "compress": _compress_status()})


def _tidy_preview():
    """What tools/autofix.py would tidy, as photo_suite wrote it at launch.

    The launcher runs under pythonw from the shortcut, so its console output does not
    exist; this file is the only place the startup preview lands, and the Live Activity
    panel is where Kevin reads it. First line is the timestamp of the launch."""
    try:
        lines = (photo_suite.META / "autofix.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    return {"at": lines[0], "lines": lines[1:]}


@app.route("/api/hold", methods=["POST", "OPTIONS"])
@_serialized
def api_hold():
    """Turn an album's backup on or off. Albums arrive on hold because a batch
    of them can appear at once (reviving Apple's shared-album agent surfaced
    nine), and pulling full-resolution originals for all of them uses real
    disk. The choice belongs here, not in a message to someone else."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True)
    hold = set(load_json_for_write(HOLD, []))
    if d.get("hold"):
        hold.add(d["album"])
    else:
        hold.discard(d["album"])
    save_json(HOLD, sorted(hold))
    _bs_patch(d["album"], held=d["album"] in hold)
    return jsonify({"ok": True, "held": sorted(hold)})


ARCHIVED = BACKUP / "_meta" / "archived.json"


@app.route("/api/archived", methods=["POST", "OPTIONS"])
@_serialized
def api_archived():
    """You ticking 'I deleted the shared album' — the end of the line for this
    country. The backup is proven, the phone is clear, and the card has nothing
    left to warn you about, so it collapses to a single done line."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True)
    arch = load_json_for_write(ARCHIVED, {})
    if d.get("done"):
        arch[d["album"]] = time.strftime("%Y-%m-%d")
    else:
        arch.pop(d["album"], None)
    save_json(ARCHIVED, arch)
    _bs_patch(d["album"], archived=arch.get(d["album"]))
    return jsonify({"ok": True, "archived": arch})


@app.route("/api/rate", methods=["POST", "OPTIONS"])
@_serialized
def api_rate():
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True)
    key = f"{clean_dirname(d['album'])}/{d['name']}"
    rat = load_json_for_write(RATINGS, {})
    r = int(d.get("rating", 0))
    if r:
        rat[key] = r
    else:
        rat.pop(key, None)
    save_json(RATINGS, rat, indent=0)
    return jsonify({"ok": True, "rating": r})


_PICKS_VERSION = [int(time.time())]


@app.route("/api/picks_version")
def api_picks_version():
    """bumped on every pick write; the editor polls it so a pick made in another tab shows"""
    return jsonify({"v": _PICKS_VERSION[0]})


def _save_picks(picks):
    _PICKS_VERSION[0] += 1
    save_json(PICKS, picks, indent=0)    # atomic: never a half-written shortlist


@app.route("/api/pick", methods=["POST", "OPTIONS"])
@_serialized
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
    picks = load_json_for_write(PICKS, {})
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
    out = {"ok": True, "root": root, "path": hit.relative_to(IMAGES).as_posix(), "name": hit.name}
    # a photo that was straightened remembers its angle and the file it was cut from, so the
    # crop window can show the tilt instead of 0 and a new tilt replaces rather than stacks
    rec = straighten_records().get(hit.relative_to(ROOT).as_posix())
    if rec:
        out["straighten"] = rec
    return jsonify(out)


STRAIGHTEN_FILE = ROOT / ".tmp" / "straighten.json"


def straighten_records():
    try:
        return json.loads(STRAIGHTEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def straighten_record(dest, src_root, src_path, angle):
    """dest is the Images/ file just written; src is the {root, path} it was cut from.
    A source that is itself a recorded straighten passes its own source through, so the
    chain always points at the unrotated file."""
    recs = straighten_records()
    key = dest.relative_to(ROOT).as_posix()
    base = {"root": src_root, "path": src_path}
    try:
        src_file = source_path(src_root, src_path)
        prior = recs.get(src_file.relative_to(ROOT).as_posix())
        if prior and prior.get("src"):
            base = prior["src"]
    except Exception:
        pass
    recs[key] = {"angle": float(angle), "src": base}
    try:
        STRAIGHTEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        STRAIGHTEN_FILE.write_text(json.dumps(recs, indent=1), encoding="utf-8")
    except OSError:
        pass


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


def _build_bthumb(p, size):
    """Build (or find) the cached thumb for a backed-up photo. Raises on an
    undecodable file so the endpoint can 415 and the warmer can skip."""
    cache = bthumb_path(p, size)
    if cache.exists():
        return cache
    # large previews (the viewers) get their own lane, so they never queue behind a burst of
    # grid thumbnails; and the photo is shrunk BEFORE it is turned upright, which spares a full
    # 24-megapixel rotate and cut a first preview from ~14 s to a few (2026-09-28)
    gate = _preview_gate if size >= 1000 else _thumb_gate
    with gate:
        if cache.exists():
            return cache
        src = Image.open(p)
        icc = src.info.get("icc_profile")
        try:
            orient = int(src.getexif().get(0x0112, 1) or 1)
        except Exception:
            orient = 1
        src.thumbnail((size, size), reducing_gap=2.0)
        T = Image.Transpose
        ops = {2: [T.FLIP_LEFT_RIGHT], 3: [T.ROTATE_180], 4: [T.FLIP_TOP_BOTTOM], 5: [T.TRANSPOSE],
               6: [T.ROTATE_270], 7: [T.TRANSVERSE], 8: [T.ROTATE_90]}.get(orient, [])
        im = src
        for op in ops:
            im = im.transpose(op)
        kw = {"quality": 82}
        if icc:
            kw["icc_profile"] = icc
        im.convert("RGB").save(cache, "JPEG", **kw)
    return cache


# the library grid asks for s=400; build the missing ones the moment an album
# is opened, on a pool of two so a big album never starves the live requests
_lib_warm_pool = ThreadPoolExecutor(max_workers=2)
_lib_warmed = set()
_lib_warm_lock = threading.Lock()


def _warm_library_album(album, paths):
    for p in paths:
        try:
            _build_bthumb(p, 400)
        except Exception:
            pass


def warm_library(album, paths):
    with _lib_warm_lock:
        if album in _lib_warmed:
            return
        _lib_warmed.add(album)
    missing = [p for p in paths if not bthumb_path(p, 400).exists()]
    if missing:
        _lib_warm_pool.submit(_warm_library_album, album, missing)


_alt_writer = None


def _alt():
    global _alt_writer
    if _alt_writer is None:
        import alt_writer
        _alt_writer = alt_writer.AltWriter()
    return _alt_writer


@app.route("/api/alt_text", methods=["POST", "OPTIONS"])
def api_alt_text():
    """Alt text for the photo being added (tools/alt_writer.py: Claude looks at it). The editor
    fills the alt field with it unless you have started typing (2026-09-28)."""
    if request.method == "OPTIONS":
        return ("", 204)
    b = request.get_json(silent=True) or {}
    try:
        p = source_path(str(b.get("root", "")), b.get("path", ""))
        p.stat()
    except Exception:
        return jsonify(ok=False, error="no such photo"), 404
    small = None                         # the sidebar preview's 2000px JPEG: no 6 s HEIC decode
    try:
        small = str(bthumb_path(p, 2000))
    except Exception:
        pass
    try:
        alt = _alt().describe(str(p), b.get("place", ""), b.get("city", ""), b.get("country", ""), b.get("section", ""),
                              small=small)
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:200]), 502
    return jsonify(ok=True, alt=alt)


@app.route("/api/alt_warm", methods=["POST", "OPTIONS"])
def api_alt_warm():
    """The photo sidebar calls this when it opens: if the alt-text session is not running (it takes
    up to a minute to start), start it now rather than when the first photo is chosen."""
    if request.method == "OPTIONS":
        return ("", 204)
    w = _alt()
    if not w.alive() and not w.lock.locked():
        threading.Thread(target=w.warm, daemon=True, name="alt-warm").start()
    return jsonify(ok=True, alive=w.alive())


@app.route("/api/alt_status")
def api_alt_status():
    """what the alt-text session is doing (alive, busy, its last few events), for diagnosing a stall"""
    return jsonify(_alt().status())


_preview_warm_pool = ThreadPoolExecutor(max_workers=1)   # one: background work, never in your way
_preview_queued = set()


@app.route("/api/warm_previews", methods=["POST", "OPTIONS"])
def api_warm_previews():
    """The editor's photo sidebar sends the photos it is showing; their 2000px viewer images are
    built in the background, so opening one is instant (2026-09-28)."""
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    album = clean_dirname(body.get("album", ""))
    queued = 0
    for n in (body.get("names") or [])[:400]:
        p = (BACKUP / album / os.path.basename(n)).resolve()
        if BACKUP.resolve() not in p.parents or not p.is_file() or p in _preview_queued or bthumb_path(p, 2000).exists():
            continue
        _preview_queued.add(p); queued += 1
        _preview_warm_pool.submit(lambda q=p: (_build_bthumb(q, 2000) if not bthumb_path(q, 2000).exists() else None))
    return jsonify(ok=True, queued=queued)


@app.route("/bthumb")
def bthumb():
    """Thumbnail for a backed-up photo (same disk cache as /thumb)."""
    album = clean_dirname(request.args.get("album", ""))
    name = os.path.basename(request.args.get("name", ""))
    p = (BACKUP / album / name).resolve()
    if BACKUP.resolve() not in p.parents or not p.is_file():
        abort(404)
    size = int(request.args.get("s", 320))
    try:
        cache = _build_bthumb(p, size)
    except Exception:
        abort(415)
    return send_file(cache, mimetype="image/jpeg")


@app.route("/api/import", methods=["POST", "OPTIONS"])
def api_import():
    """Copy a library photo into the Images/ archive WITHOUT touching any
    page: editor.html inserts the markup itself in its contenteditable and
    only needs the archived file + its repo-relative path."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True) or {}
    # An empty or wrong-shaped body answered 500, because d["root"] raised straight out of
    # the handler. The request is malformed, so say that instead.
    if not isinstance(d, dict) or not d.get("root") or not d.get("path"):
        abort(400)
    try:
        src = source_path(d["root"], d["path"])
    except (ValueError, KeyError, OSError):
        abort(400)
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
        if abs(float(d.get("angle", 0) or 0)) > 1e-3:
            straighten_record(dest, d["root"], d["path"], d.get("angle", 0))
    with Image.open(dest) as im:
        im = ImageOps.exif_transpose(im)
        w, h = im.size
    return jsonify({"ok": True, "src": dest.relative_to(ROOT).as_posix(),
                    "w": w, "h": h,
                    "orient": "landscape" if w >= h * 1.05 else "portrait" if h >= w * 1.05 else "square"})


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


def _run_pipeline(page):
    """The six compression tools for one page. Returns (ok, log)."""
    if page.startswith("Drafts/"):
        # a draft goes through tools/draft_images.py, which stages it as a temporary live
        # page so the same six tools can run on it, then copies the result back
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "draft_images.py"), page],
                           capture_output=True, text=True, cwd=ROOT, timeout=1800, encoding="utf-8", errors="replace")
        tail = "\n".join((r.stdout or r.stderr or "").strip().splitlines()[-12:])
        log = ["$ draft_images.py\n" + tail]
        if r.returncode == 0:
            _fit_page(page, log)
        return r.returncode == 0, "\n\n".join(log)
    log = []
    for tool in PIPELINE:
        args = [sys.executable, str(ROOT / "tools" / tool)]
        if tool == "recompress_desktop.py":
            args.append("--new-only")            # only build missing web variants
        r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=1800)
        tail = (r.stdout or r.stderr or "").strip().splitlines()[-3:]
        log.append(f"$ {tool}\n" + "\n".join(tail))
        if r.returncode != 0:
            return False, "\n\n".join(log)
    _fit_page(page, log)
    return True, "\n\n".join(log)


def _fit_page(page, log):
    """the two fitters after the six tools (CLAUDE.md image steps 5-6 and the heroes rule): each body
    photo cut at the width this page draws it, and the hero's per-window files (a hero built in Covers
    had none until the page was fitted; QA 2026-10-02). Their failures are logged, not fatal."""
    for tool in ("fit_image_tiers.py", "fit_backgrounds.py"):
        try:
            r = subprocess.run([sys.executable, str(ROOT / "tools" / tool), page], capture_output=True, text=True,
                               cwd=ROOT, timeout=1800, encoding="utf-8", errors="replace")
            tail = (r.stdout or r.stderr or "").strip().splitlines()[-2:]
            log.append(f"$ {tool}\n" + "\n".join(tail))
        except Exception as e:
            log.append(f"$ {tool}\nskipped: {e}")


# ---- compression after you leave the article -------------------------------------------
# Kevin, 2026-09-25: "the compress photos is pretty disruptive to the editor. Can we just do
# that after an article is saved and we're not in the editor?" It used to run on every save
# that brought new photos, then RELOAD the article from disk so the canvas matched the
# rewritten <picture> markup, which threw away the caret, the undo history and the review
# state in the middle of a session. Now a save only QUEUES the page. The editor reports which
# article it has open every 30 s; a page is compressed once no report for it has arrived for
# OPEN_GRACE seconds, i.e. after you switch article or close the tab. Nothing is reloaded
# under you, because the page is not open when its file changes; the next open reads the
# finished markup like any other.
#
# The live-page tools run SITE-WIDE (no page argument), so a live page is only compressed
# when no queued live page is open either; a draft goes through draft_images.py, which works
# on the one file, so drafts only wait for themselves. The queue is a file, so a server
# restart does not forget it.
OPEN_GRACE = 75
_cq_lock = threading.Lock()
_cq_open = {}                                   # rel -> time of the last heartbeat
_cq_state = {"running": None, "last": None}
CQ_FILE = photo_suite.META / "compress_queue.json"


def _cq_load():
    try:
        return json.loads(CQ_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cq_save(q):
    try:
        CQ_FILE.parent.mkdir(parents=True, exist_ok=True)
        CQ_FILE.write_text(json.dumps(q, indent=1), encoding="utf-8")
    except OSError:
        pass


def _cq_is_open(rel, now):
    return now - _cq_open.get(rel, 0) < OPEN_GRACE


def _cq_ready(rel, q, now):
    if _cq_is_open(rel, now):
        return False
    if not rel.startswith("Drafts/"):
        # site-wide tools: wait until no queued live page is open either
        if any(_cq_is_open(o, now) for o in q if not o.startswith("Drafts/")):
            return False
    return True


def _cq_tick():
    """One pass of the worker: compress the oldest page that is ready. Returns the page or None."""
    with _cq_lock:
        q, now = _cq_load(), time.time()
        rel = next((r for r in sorted(q, key=lambda r: q[r].get("queued", 0))
                    if _cq_ready(r, q, now) and q[r].get("tries", 0) < 3), None)
        if not rel:
            return None
        _cq_state["running"] = rel
    try:
        ok, log = _run_pipeline(rel)
    except Exception as e:                       # a crash is a failed try, not a dead worker
        ok, log = False, "error: %s" % e
    with _cq_lock:
        q = _cq_load()
        if ok:
            q.pop(rel, None)
        else:
            e = q.setdefault(rel, {"queued": time.time()})
            e["tries"] = e.get("tries", 0) + 1
            e["error"] = log[-400:]
        _cq_save(q)
        _cq_state["running"] = None
        _cq_state["last"] = {"page": rel, "ok": ok, "at": time.strftime("%H:%M"), "log": log[-400:]}
    return rel


def _cq_worker():
    while True:
        time.sleep(15)
        try:
            _cq_tick()
        except Exception as e:                   # the worker must never die quietly
            _cq_state["running"] = None
            _cq_state["last"] = {"page": None, "ok": False, "at": time.strftime("%H:%M"),
                                 "log": "worker error: %s" % e}


if os.environ.get("PHOTO_EDITOR_NO_WORKER") != "1":
    threading.Thread(target=_cq_worker, daemon=True, name="compress-queue").start()


@app.route("/api/pipeline/queue", methods=["POST", "OPTIONS"])
def api_pipeline_queue():
    """The editor's save: note the page, compress later."""
    if request.method == "OPTIONS":
        return "", 204
    page = (request.get_json(force=True) or {}).get("page") or ""
    if not page or ".." in page or not (ROOT / page).is_file():
        abort(400)
    with _cq_lock:
        q = _cq_load()
        q[page] = {"queued": time.time(), "tries": 0}
        _cq_save(q)
        _cq_open[page] = time.time()             # the save came from the open editor
    return jsonify({"ok": True, "queued": sorted(q)})


@app.route("/api/editor/open", methods=["POST", "OPTIONS"])
def api_editor_open():
    """Heartbeat from the editor: which article is open. closing:true on switch or tab close.
    Sent with sendBeacon on the way out, as text/plain, so it is parsed with force=True."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True, silent=True) or {}
    page = d.get("page") or ""
    with _cq_lock:
        if d.get("closing"):
            _cq_open.pop(page, None)
        elif page:
            _cq_open[page] = time.time()
            _note_recent(page)
    return jsonify({"ok": True, "compressing": _cq_state["running"] == page})


# ---------------------------------------------------------------- the Articles panel
RECENT = Path(ROOT) / ".tmp" / "recent_articles.json"
_recent_lock = threading.Lock()


def _note_recent(rel):
    """the heartbeat's first beat after an article opens; keeps the last 40 with a time"""
    rel = rel.replace("\\", "/")
    with _recent_lock:
        try:
            rec = json.loads(RECENT.read_text(encoding="utf-8")) if RECENT.exists() else {}
        except Exception:
            rec = {}
        if rec.get(rel, 0) > time.time() - 60:
            return                                   # the 30 s heartbeat, not a new open
        rec[rel] = time.time()
        _articles_cache["at"] = 0                # the Recent list must show this open at once
        keep = dict(sorted(rec.items(), key=lambda kv: -kv[1])[:40])
        RECENT.parent.mkdir(exist_ok=True)
        RECENT.write_text(json.dumps(keep, indent=1), encoding="utf-8")


_SKIP_TOP = {"archive", "Images", "tools", "workflows", "_content", "assets", "fonts", ".tmp", "node_modules", ".git", ".claude"}
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"<(script|style|svg|nav|header|footer)\b.*?</\1>", re.S | re.I)


def _article_kind(rel):
    name = rel.rsplit("/", 1)[-1]
    if name == "field-notes.html":
        return "field notes"
    if name == "index.html":
        return "country page"
    if "itinerary" in name:
        return "itinerary"
    if name.startswith("top-10"):
        return "top 10"
    return "guide"


def _article_row(path, rel, status):
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    t = _TITLE_RE.search(html); h = _H1_RE.search(html)
    title = _TAG_RE.sub("", (h.group(1) if h else (t.group(1) if t else rel))).strip()
    title = re.sub(r"\s+", " ", title.replace("&mdash;", "\u2014").replace("&amp;", "&"))
    if not title and t:
        title = t.group(1)
    body = _BLOCK_RE.sub(" ", html)
    words = len(re.findall(r"\b\w+\b", _TAG_RE.sub(" ", body)))
    parts = rel.split("/")
    country = parts[-2] if len(parts) >= 2 else ""
    return {"rel": rel, "title": title[:120] or rel, "country": country, "kind": _article_kind(rel), "status": status,
            "words": words, "modified": path.stat().st_mtime}


_FLAG_RE = re.compile(r'href="([a-z-]+)/field-notes\.html"[^>]*>(?:(?!</a>).)*?flags/([a-z]{2})\.png', re.S)


def _country_flags():
    """country folder -> ISO code, read off the nav in index.html (the published pairing)"""
    try:
        html = (Path(ROOT) / "index.html").read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    found = {m.group(1): m.group(2) for m in _FLAG_RE.finditer(html)}
    # a country that isn't in the nav yet (a draft, or a nav entry that doesn't link to field
    # notes, like El Salvador's) still gets its flag: the nav wins where it has one
    return {**ISO2, **found}


ISO2 = {"albania": "al", "argentina": "ar", "armenia": "am", "australia": "au", "belgium": "be", "bosnia": "ba",
        "brazil": "br", "chile": "cl", "colombia": "co", "croatia": "hr", "cuba": "cu", "denmark": "dk", "egypt": "eg",
        "el-salvador": "sv", "estonia": "ee", "finland": "fi", "france": "fr", "georgia": "ge", "germany": "de",
        "greece": "gr", "guatemala": "gt", "hungary": "hu", "india": "in", "indonesia": "id", "italy": "it",
        "japan": "jp", "kosovo": "xk", "latvia": "lv", "mexico": "mx", "montenegro": "me", "morocco": "ma",
        "netherlands": "nl", "new-zealand": "nz", "nicaragua": "ni", "north-macedonia": "mk", "peru": "pe",
        "philippines": "ph", "portugal": "pt", "serbia": "rs", "slovenia": "si", "spain": "es", "sweden": "se",
        "switzerland": "ch", "tanzania": "tz", "thailand": "th", "turkiye": "tr", "vietnam": "vn", "uk": "gb"}


def _articles_inventory():
    root = Path(ROOT)
    flags = _country_flags()
    rows = []
    for p in sorted(root.glob("*/*.html")):
        top = p.parts[len(root.parts)]
        if top in _SKIP_TOP or top.startswith("."):
            continue
        r = _article_row(p, p.relative_to(root).as_posix(), "live")
        if r:
            rows.append(r)
    for p in sorted((root / "Drafts").glob("*/*.html")):
        if p.parts[len(root.parts) + 1].startswith("."):
            continue
        r = _article_row(p, p.relative_to(root).as_posix(), "draft")
        if r:
            rows.append(r)
    for p in sorted((root / "Drafts" / ".Full Articles").glob("*/*.html")):
        if p.parts[len(root.parts) + 2].startswith("."):      # a dot-folder is scratch, not a country
            continue
        r = _article_row(p, p.relative_to(root).as_posix(), "draft")
        if r:
            rows.append(r)
    # the review state: open comments and a pending round, per article
    comments = {}
    for f in (_redline.COMMENTS.glob("*.json") if _redline.COMMENTS.exists() else []):
        try:
            th = json.loads(f.read_text(encoding="utf-8")).get("threads", [])
        except Exception:
            continue
        comments[f.stem] = {"open": sum(1 for t in th if not t.get("resolved")),
                            "tasks": sum(1 for t in th if t.get("kind") in ("task", "rewrite") and not t.get("resolved") and not t.get("edit"))}
    rounds = {}
    try:
        for pr in _redline.pending():
            if pr.get("applied"):
                continue
            pp = _redline.load(pr["slug"])
            rounds[(pp["article_dir"] + "/" + pp["article"]).replace("\\", "/")] = {"slug": pr["slug"], "changes": pr["changes"], "title": pr["title"]}
    except Exception:
        pass
    try:
        recent = json.loads(RECENT.read_text(encoding="utf-8")) if RECENT.exists() else {}
    except Exception:
        recent = {}
    for r in rows:
        r["flag"] = flags.get(r["country"], "")
        c = comments.get(_redline.comments_key(r["rel"]), {})
        r["comments"] = c.get("open", 0); r["tasks"] = c.get("tasks", 0)
        r["round"] = rounds.get(r["rel"])
        r["opened"] = recent.get(r["rel"], 0)
    return rows


@app.route("/api/articles")
def api_articles():
    """Every article under the Travel Blog folder, live and draft, for the editor's Articles
    panel: no more walking the Windows folder tree to find a page (Kevin, 2026-09-26)."""
    now = time.time()
    if not _articles_cache["rows"]:                              # first ask: build it now
        _articles_cache.update(rows=_articles_inventory(), at=now)
    elif now - _articles_cache["at"] > 20 and not _articles_cache.get("busy"):
        # QA 2026-10-02: the panel waited 11-32 s on a re-read of every page; the last list is
        # served at once and refreshed behind it
        _articles_cache["busy"] = True
        def _refresh():
            try:
                _articles_cache.update(rows=_articles_inventory(), at=time.time())
            finally:
                _articles_cache["busy"] = False
        threading.Thread(target=_refresh, daemon=True, name="articles-refresh").start()
    return jsonify({"articles": _articles_cache["rows"], "root": str(ROOT)})


_articles_cache = {"at": 0, "rows": []}


def find_card(h, name):
    """(start, end, tag) of the country-article-card element linking to `name`, any attribute order"""
    for m in re.finditer(r'<(?:div|a)\b[^>]*\bclass="country-article-card(?:\s[^"]*)?"[^>]*>', h):
        t = m.group(0)
        if ("location.href='%s'" % name) in t or ('href="%s"' % name) in t:
            return m.start(), m.end(), t
    return None


@app.route("/api/thumbs")
def api_thumbs():
    """The open article's thumbnails as they are today, one per slot, for the photo sidebar's
    Thumbnails panel (Kevin, 2026-09-30: "a preview of every thumbnail type with a heading").
    Slots follow the site audit: the country page card (2:3 on anything wider than 768px, a 16:9
    strip on a phone), the home and posts card (4:3, 16:9 on a phone; published articles only)
    and the 1200x630 share image."""
    import posixpath
    rel = (request.args.get("rel") or "").replace("\\", "/")
    page = (ROOT / rel).resolve()
    if ROOT.resolve() not in page.parents or page.suffix != ".html" or not page.is_file():
        return jsonify({"ok": False, "error": "no such article"}), 404
    base = posixpath.dirname(rel)

    def norm(url, at):
        u = url.replace("&amp;", "&")
        if u.startswith("http"):
            u = re.sub(r"^https?://[^/]+/", "", u)
        else:
            u = posixpath.normpath(posixpath.join(at, u))
        return u if (ROOT / u).is_file() else None

    def parse_style(style, at):
        # prefer the JPEG of an image-set (the 1x/2x webp pair in older cards resolves too)
        url = (re.search(r"url\(\s*'([^']+\.(?:jpg|jpeg|png))'\s*\)", style)
               or re.search(r"url\(\s*'([^']+\.webp)'\s*\)", style))
        pos = re.search(r"url\([^)]*\)\s+(-?[\d.]+%\s+-?[\d.]+%)", style) or re.search(r"background-position:\s*([^;]+)", style)
        pm = re.search(r"--pm:\s*([^;]+)", style)
        return (norm(url.group(1), at) if url else None, pos.group(1).strip() if pos else "50% 50%",
                pm.group(1).strip() if pm else None)

    slots = []
    idx = ROOT / base / "index.html"
    card = phone = None
    if idx.is_file():
        h = idx.read_text(encoding="utf-8", errors="replace")
        found = find_card(h, page.name)
        if found:
            tag = found[2]
            st = re.search(r'\bstyle="([^"]*)"', tag)
            img, pos, pm = parse_style(st.group(1) if st else "", base)
            if not img:                                  # the photo is a class in styles.css (El Salvador)
                cls = re.findall(r"img-[a-z0-9-]+", re.search(r'class="([^"]*)"', tag).group(1))
                css = (ROOT / "styles.css").read_text(encoding="utf-8", errors="replace")
                rule = re.search(r"\.%s\{([^}]*)\}" % re.escape(cls[0]), css) if cls else None
                if rule:
                    img, pos2, pm2 = parse_style(rule.group(1), "")
                    pos, pm = (pos if st and "background-position" in st.group(1) else pos2), (pm or pm2)
            card = {"img": img, "pos": pos}
            phone = {"img": img, "pos": pm or pos, "note": "" if pm else "same crop as the country card: none set for phones yet"}
    slots.append(dict(key="card", label="Country page card", where="the country page, on a monitor or tablet", ratio="2 / 3",
                      **(card or {"img": None, "pos": "50% 50%", "note": "this article has no card on its country page"})))
    live = rel.replace("Drafts/.Full Articles/", "")
    lst = None
    for name in ("index.html", "posts.html"):
        f = ROOT / name
        if not f.is_file():
            continue
        h = f.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'<a href="%s"[^>]*>\s*<div class="card-img bg([^"]*)"(?: style="([^"]*)")?' % re.escape(live), h)
        if not m:
            continue
        if m.group(2):
            img, pos, pm = parse_style(m.group(2), "")
        else:                                       # a class in styles.css (the El Salvador guides)
            cls = re.findall(r"img-[a-z0-9-]+", m.group(1))
            css = (ROOT / "styles.css").read_text(encoding="utf-8", errors="replace")
            rule = re.search(r"\.%s\{([^}]*)\}" % re.escape(cls[0]), css) if cls else None
            img, pos, pm = parse_style(rule.group(1), "") if rule else (None, "50% 50%", None)
        lst = {"img": img, "pos": pos, "note": "on " + ("the home page" if name == "index.html" else "posts.html")}
        break
    slots.append(dict(key="list", label="Home & posts card", where="Recent Dispatches and the posts list", ratio="4 / 3",
                      **(lst or {"img": (card or {}).get("img"), "pos": "50% 50%",
                                 "note": "not on the home page yet (drafts aren't): shown with the card photo"})))
    slots.append(dict(key="phone", label="Phone card", where="both cards on a phone", ratio="16 / 9",
                      **(phone or {"img": None, "pos": "50% 50%", "note": ""})))
    og = None
    m = re.search(r'<meta property="og:image" content="([^"]+)"', page.read_text(encoding="utf-8", errors="replace"))
    if m:
        og = norm(m.group(1), base)
    slots.append(dict(key="og", label="Share image", where="link previews in messages and social posts", ratio="1200 / 630",
                      img=og, pos="50% 50%", note="" if og else "no share image yet"))
    return jsonify({"ok": True, "rel": rel, "slots": slots})


def _compress_status():
    with _cq_lock:
        q = _cq_load()
        return {"queued": sorted(q), "running": _cq_state["running"], "last": _cq_state["last"],
                "failed": {r: e.get("error", "") for r, e in q.items() if e.get("tries", 0) >= 3}}


# --------------------------------------------------------------------- UI page
if __name__ == "__main__":
    # 127.0.0.1 rather than localhost: localhost resolves ::1 first on this
    # box (Bonjour) and every request pays a ~2s IPv6 timeout
    print(f"Photo editor: http://127.0.0.1:5003   sources: {[s['label'] for s in SOURCES]}")
    # threaded: thumbnail generation must not block imports/saves behind it
    app.run(port=int(os.environ.get("PHOTO_EDITOR_PORT", 5003)), debug=False, threaded=True)   # a test instance runs beside the live one
