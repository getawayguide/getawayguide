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
from werkzeug.exceptions import HTTPException
import struct, zlib
from PIL import UnidentifiedImageError

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_OK = True
except ImportError:
    HEIC_OK = False

import numpy as np
from PIL import Image, ImageOps

# every child gets a hidden console of its own: under the pythonw launcher a console program
# (git, PowerShell, python.exe) otherwise opens a visible window (QA 2026-10-08)
_NOWIN = 0x08000000 if __import__("sys").platform == "win32" else 0
# the compress tools run below normal priority: they now work while the editor is open on another article
# (2026-10-16), and typing and the photo dialog come first
_LOWPRI = 0x00004000 if __import__("sys").platform == "win32" else 0

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


def _origin_ok(o):
    """The callers: editor.html from file:// (Origin "null"), the pages this server and the suite's
    other servers serve (http://127.0.0.1:<port>), and scripts (no Origin). Any other origin is a
    web page in Kevin's browser, and with CORS open it could drive every route that writes
    (QA 2026-10-08: /api/launch/fix rewrote a file outside the repo from a cross-origin POST)."""
    if not o or o == "null":
        return True
    from urllib.parse import urlparse
    try:
        u = urlparse(o)
    except ValueError:
        return False
    return u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")


@app.before_request
def same_machine_only():
    if not _origin_ok(request.headers.get("Origin")):
        return jsonify({"ok": False, "error": "origin not allowed"}), 403


@app.errorhandler(TypeError)
@app.errorhandler(AttributeError)
@app.errorhandler(KeyError)
@app.errorhandler(ValueError)
@app.errorhandler(ZeroDivisionError)
@app.errorhandler(OverflowError)
@app.errorhandler(IndexError)
@app.errorhandler(struct.error)
@app.errorhandler(zlib.error)
@app.errorhandler(UnidentifiedImageError)
@app.errorhandler(FileNotFoundError)
@app.errorhandler(Image.DecompressionBombError)
def _bad_input(e):
    """A route handed the wrong shape (a JSON array, a number, a key missing, ?s=abc) used to answer
    with a 500 and a traceback (QA 2026-10-08: 25 routes). It is the request that is wrong, so 400.
    The traceback still goes to the log, so a real bug of the same type is not hidden."""
    import traceback
    traceback.print_exc()
    return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}), 400


@app.errorhandler(HTTPException)
def _http_error(e):
    """abort(400/403/404/410/413/415) used to answer Flask's HTML page; the editor's r.json() then threw and
    reported "the photo server isn't answering" (stress test 2026-10-15). JSON for the API, HTML for pages."""
    if request.path.startswith(("/api/", "/comments/", "/review/", "/redline/")):
        return jsonify({"ok": False, "error": e.name, "detail": (e.description or "")[:300]}), e.code
    return e


@app.errorhandler(Exception)
def _server_error(e):
    """anything else is still a 500, but a JSON one the editor can read; the traceback goes to the log"""
    import traceback
    traceback.print_exc()
    if request.path.startswith(("/api/", "/comments/", "/review/", "/redline/")):
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}), 500
    raise e


# A request body over this is refused with a 413 before it is read (a 50 MB body cost 100 MB of memory
# and up to 11 s, stress test 2026-10-09). The largest real one, a photo upload, is ~25 MB.
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024


@app.after_request
def _send_in_pieces(resp):
    """A large response leaves in 64 KB pieces. Werkzeug closes the connection after every response, and
    on this machine's loopback a big body written in one go and then closed lost its tail: the merge of
    a 2 MB article failed 10 times in 20, a 0.75 MB preview hung for minutes (stress test 2026-10-09).
    Written in pieces, 0 in 20 were lost. Files from send_file already go out in pieces."""
    try:
        if resp.direct_passthrough or resp.is_streamed:
            return resp
        data = resp.get_data()
        if len(data) > 65536:
            resp.response = [data[i:i + 65536] for i in range(0, len(data), 65536)]
            resp.headers["Content-Length"] = str(len(data))
    except Exception:
        pass
    return resp


def _rel_clean(rel):
    """a repo path as a request names it: separators made forward, a leading ./ or / dropped. NOT
    .lstrip("./"), which also ate the dot of ".tmp/..." and turned it into "tmp/..."."""
    return re.sub(r"^(?:\./|/)+", "", (rel or "").replace("\\", "/"))


@app.after_request
def cors(resp):
    """editor.html runs on a different origin (file:// or another local port)
    and its photo sidebar calls this API directly."""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Content-Encoding"   # the merge is sent gzipped
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
    after you press Insert. tools/backup_lib.py has the explicit hydration call.
    """
    try:
        st = os.stat(p)
    except OSError:
        return
    if not (st.st_file_attributes & (0x400000 | 0x1000)):    # RECALL | OFFLINE
        return
    try:
        import backup_lib
        backup_lib.hydrate(Path(p), timeout_s)
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
    s = unicodedata.normalize("NFC", str(s or ""))
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f\u200e\u200f\u202a-\u202e\u2066-\u2069]', "", s)[:80].strip(". ").strip()   # cut THEN strip: a cut ending in "." made a folder Windows silently renamed; a 300-char city was WinError 123; bidi controls hid a folder's real name
    if s.split(".")[0].upper() in ("CON", "PRN", "AUX", "NUL") or re.fullmatch(r"(?i)(COM|LPT)[1-9](\..*)?", s):
        s += "_"                                  # Windows reserved device names cannot be folders
    return s


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
    # & + ' are out too: a file name reaches the page inside href/srcset, where an & must be escaped and the
    # tier tools do not escape it (El Camino Pub & Hostel.jpg, 2026-10-19)
    s = re.sub(r'[<>:"/\\|?*%#&+' + "']", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()[:80].strip() or "photo"   # with -N and tier suffixes a long stem passed Windows' path limit
    return s


# ---------------------------------------------------------------- AI eraser
# Local LaMa inpainting (the model class behind commercial "magic erasers").
# Model: .tmp/photo_editor/models/big-lama.pt (~196MB TorchScript, CPU).
ERASED = ROOT / ".tmp" / "photo_editor" / "erased"
ERASED.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- background jobs (2026-10-16)
# An AI erase takes 40 s to minutes and used to hold the photo dialog hostage; a result that landed after the
# dialog moved on was the root of the 2026-10-15 "photo A's pixels under photo B's name" bug. Heavy work now runs
# here, owned by a job the editor watches: the dialog stays usable, the chip in the sidebar shows progress, and the
# result is applied only to the stage that asked for it (or kept for that photo's next open).
_JOBS, _JOBS_LOCK, _JOB_SEQ = {}, threading.Lock(), [0]
_JOB_POOL = ThreadPoolExecutor(max_workers=2)


def job_new(kind, label, **meta):
    with _JOBS_LOCK:
        _JOB_SEQ[0] += 1
        jid = "j%d%s" % (_JOB_SEQ[0], uuid_hex(6))
        _JOBS[jid] = dict(id=jid, kind=kind, label=label, status="queued", progress=0.0, note="",
                          started=time.time(), ended=None, result=None, error=None, cancel=False, **meta)
        # the list never grows past 40: finished jobs older than an hour go first
        done = sorted((j for j in _JOBS.values() if j["status"] in ("done", "failed", "cancelled")), key=lambda j: j["ended"] or 0)
        for j in done[:max(0, len(_JOBS) - 40)]:
            _JOBS.pop(j["id"], None)
    return jid


def uuid_hex(n):
    import uuid
    return uuid.uuid4().hex[:n]


def job_progress(jid, frac, note=None):
    # the worker's progress callback; False = cancelled, stop
    with _JOBS_LOCK:
        j = _JOBS.get(jid)
        if not j:
            return False
        j["progress"] = max(0.0, min(1.0, float(frac)))
        if note:
            j["note"] = str(note)[:120]
        return not j["cancel"]


def job_submit(jid, fn):
    def run():
        with _JOBS_LOCK:
            j = _JOBS.get(jid)
            if not j or j["cancel"]:
                return
            j["status"] = "running"; j["started"] = time.time()
        try:
            result = fn(lambda f, n=None: job_progress(jid, f, n))
            with _JOBS_LOCK:
                j = _JOBS.get(jid)
                if j:
                    j.update(status="cancelled" if j["cancel"] else "done", progress=1.0, result=result, ended=time.time())
        except Exception as e:
            cancelled = "cancelled" in str(e)
            with _JOBS_LOCK:
                j = _JOBS.get(jid)
                if j:
                    j.update(status="cancelled" if (cancelled or j["cancel"]) else "failed", error=None if cancelled else str(e)[:300], ended=time.time())
    _JOB_POOL.submit(run)


def jobs_list():
    with _JOBS_LOCK:
        out = [{k: v for k, v in j.items() if k != "cancel"} for j in _JOBS.values()]
    out.sort(key=lambda j: -(j["started"] or 0))
    cs = _compress_status()
    if cs.get("running"):
        out.insert(0, {"id": "compress:" + cs["running"], "kind": "compress", "label": "Compressing photos: " + cs["running"].split("/")[-1],
                       "status": "running", "progress": 0.0, "note": "", "started": None, "ended": None})
    for r in cs.get("queued", []):
        if r != cs.get("running"):
            out.append({"id": "compress:" + r, "kind": "compress", "label": "Photos to compress: " + r.split("/")[-1],
                        "status": "queued", "progress": 0.0, "note": "after the page is closed", "started": None, "ended": None})
    return out


@app.route("/api/jobs", methods=["GET", "OPTIONS"])
def api_jobs():
    if request.method == "OPTIONS":
        return "", 204
    return jsonify({"ok": True, "jobs": jobs_list()})


@app.route("/api/jobs/<jid>", methods=["DELETE", "OPTIONS"])
def api_job_delete(jid):
    """cancel a running job (the worker stops at its next level) or dismiss a finished one"""
    if request.method == "OPTIONS":
        return "", 204
    with _JOBS_LOCK:
        j = _JOBS.get(jid)
        if not j:
            abort(404)
        if j["status"] in ("queued", "running"):
            j["cancel"] = True
            if j["status"] == "queued":
                j.update(status="cancelled", ended=time.time())
        else:
            _JOBS.pop(jid, None)
    return jsonify({"ok": True})


def _cache_sweep():
    """the caches that only grew (thumbs 4 GB, erased 150 MB; stress test 2026-10-16): thumbs kept under 2 GB
    (oldest first), erase sessions older than 7 days removed. At start and every 6 hours."""
    while True:
        try:
            now = time.time()
            for f in ERASED.glob("*.jpg"):
                try:
                    if now - f.stat().st_mtime > 7 * 86400:
                        f.unlink()
                except OSError:
                    pass
            files = []
            for f in THUMBS.rglob("*"):
                try:
                    if f.is_file():
                        st = f.stat(); files.append((st.st_mtime, st.st_size, f))
                except OSError:
                    pass
            total = sum(s for _, s, _ in files)
            # a working set, not a leak: an evicted thumbnail costs a 1-2 s HEIC decode when its album is next
            # opened (the first sweep at 2.5 GB made a 1,100-photo cull crawl), so the cap sits above today's size
            if total > 6.0 * 1024 ** 3:
                files.sort()
                freed = 0
                for _, s, f in files:
                    if total - freed <= 5.0 * 1024 ** 3:
                        break
                    try:
                        f.unlink(); freed += s
                    except OSError:
                        pass
                print("thumbs cache: %.2f GB, removed %.2f GB of the oldest" % (total / 1024 ** 3, freed / 1024 ** 3), flush=True)
        except Exception as e:
            print("cache sweep:", e, flush=True)
        time.sleep(6 * 3600)
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


def _context_box(im, m, pad=0.45, extra=32):
    """The box the eraser works in: the mask's bounds plus `pad` of their size and `extra` px."""
    ys, xs = np.where(m)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    ph, pw = int((y1 - y0 + 1) * pad) + extra, int((x1 - x0 + 1) * pad) + extra
    return (max(0, x0 - pw), max(0, y0 - ph), min(im.width, x1 + pw + 1), min(im.height, y1 + ph + 1))


def lama_fill(crop, mcrop):
    """LaMa's suggestion for the hole, at its working size (<= 1200 px): the structure of the fill. Returns
    an RGB PIL image of the crop's size (scaled back up: a guide, not the final pixels)."""
    import torch
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
    return res.resize(crop.size, Image.LANCZOS) if res.size != crop.size else res


def lama_erase(im, mask, progress=None):
    """The AI eraser: LaMa decides the structure of the fill at <= 1200 px, then the hole is rebuilt at
    FULL resolution from patches of its surroundings (tools/inpaint_v2.complete, PhotoCraft's Remove Tool
    recipe) and blended in without a seam. Works on a context crop around the mask."""
    import inpaint_v2
    m = np.asarray(mask, dtype=np.uint8) > 127
    if not m.any():
        return im
    x0, y0, x1, y1 = _context_box(im, m)
    crop = im.crop((x0, y0, x1, y1))
    mcrop = mask.crop((x0, y0, x1, y1))
    # a budget: the patch search holds ~1 KB per hole pixel, so a 40% hole of a 24 MP photo asked for 9 GB and
    # died (stress test 2026-10-15). Over the budget the hole is rebuilt at a smaller size and scaled back in;
    # the surroundings keep their full resolution.
    hole_px = int((np.asarray(mcrop, dtype=np.uint8) > 127).sum())
    scale = min(1.0, 2400 / max(crop.size), (1.2e6 / max(1, hole_px)) ** 0.5)
    if scale < 0.999:
        work = crop.resize((max(16, int(crop.width * scale)), max(16, int(crop.height * scale))), Image.LANCZOS)
        wmask = mcrop.resize(work.size, Image.NEAREST)
        whole = np.asarray(wmask, dtype=np.uint8) > 127
        if progress: progress(0.05, "LaMa is sketching the fill")
        guide = np.asarray(lama_fill(work, wmask))
        small = inpaint_v2.complete(np.asarray(work), whole, init=guide, progress=(lambda f, n=None: progress(0.1 + 0.85 * f, n)) if progress else None)
        up = np.asarray(Image.fromarray(small).resize(crop.size, Image.LANCZOS)).astype(np.float32)
        base = np.asarray(crop).astype(np.float32)
        import cv2
        a = cv2.GaussianBlur((np.asarray(mcrop, dtype=np.uint8) > 127).astype(np.float32), (0, 0), 3)[..., None]
        out = (base * (1 - a) + up * a).clip(0, 255).astype(np.uint8)
    else:
        hole = np.asarray(mcrop, dtype=np.uint8) > 127
        if progress: progress(0.05, "LaMa is sketching the fill")
        guide = np.asarray(lama_fill(crop, mcrop))
        out = inpaint_v2.complete(np.asarray(crop), hole, init=guide, progress=(lambda f, n=None: progress(0.1 + 0.85 * f, n)) if progress else None)
    im.paste(Image.fromarray(out), (x0, y0))
    return im


def spot_erase(im, mask):
    """The Spot eraser: LightCraft's Remove (an automatically chosen source patch, healed in with a Poisson
    clone): instant, right for dust, small marks and thin wires; a large hole wants the AI mode."""
    import inpaint_v2
    m = np.asarray(mask, dtype=np.uint8) > 127
    if not m.any():
        return im
    x0, y0, x1, y1 = _context_box(im, m, pad=4.5, extra=24)
    crop = im.crop((x0, y0, x1, y1))
    hole = np.asarray(mask.crop((x0, y0, x1, y1)), dtype=np.uint8) > 127
    out = inpaint_v2.heal(np.asarray(crop), hole)
    im.paste(Image.fromarray(out), (x0, y0))
    return im


# ---------------------------------------------------------------- develop bake
# The slider math lives in tools/develop_v2.py (LightCraft's, ported); the WebGL shader in editor.html
# implements EXACTLY the same formulas. DEV_KEYS is the shared contract.
import develop_v2 as _dev2    # noqa: E402

DEV_KEYS = _dev2.DEV_KEYS


def develop(im, p, masks=None):
    """Bake develop parameters (each -100..100) and local masks into a PIL image."""
    return _dev2.develop(im, p, masks)


def brush_alphas(masks, size):
    """A painted mask comes from the editor as a PNG of its strokes at the preview's size (the alpha is the
    mask); here it becomes the full-size alpha the bake reads (develop_v2.mask_alpha, kind "brush")."""
    import base64
    import io
    out = []
    for m in masks or []:
        if m.get("type") == "brush":
            png = m.get("png") or ""
            if "," not in png:
                continue
            try:
                raw = base64.b64decode(png.split(",", 1)[1])
                a = Image.open(io.BytesIO(raw)).convert("RGBA").split()[-1].resize(size, Image.BILINEAR)
            except Exception:
                continue
            m = dict(m)
            m["_alpha"] = np.asarray(a, dtype=np.float32) / 255.0
        out.append(m)
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


RECIPE_TAG = b"GGRECIPE"


def recipe_segments(recipe):
    """The edit recipe (sliders, masks, erases, source, orientation) as JPEG APP11 segments, appended to the
    baked file (LightCraft keeps a photo's settings beside it; here they travel inside the archive copy, so a
    placed photo can be reopened with every slider). JSON, zlib, split under the 64 KB segment limit."""
    import json
    import struct
    import zlib
    data = zlib.compress(json.dumps(recipe, ensure_ascii=False).encode("utf-8"), 9)
    chunk = 65000
    parts = [data[i:i + chunk] for i in range(0, len(data), chunk)] or [b""]
    out = b""
    for i, part in enumerate(parts):
        body = RECIPE_TAG + struct.pack(">BB", i, len(parts)) + part
        out += b"\xff\xeb" + struct.pack(">H", len(body) + 2) + body
    return out


def read_recipe(path):
    """The recipe written by recipe_segments, or None."""
    import json
    import struct
    import zlib
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if raw[:2] != b"\xff\xd8":
        return None
    parts = {}
    i = 2
    while i + 4 <= len(raw) and raw[i] == 0xFF:
        marker = raw[i + 1]
        if marker == 0xDA:                                   # start of scan: no more headers
            break
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        ln = struct.unpack(">H", raw[i + 2:i + 4])[0]
        body = raw[i + 4:i + 2 + ln]
        if marker == 0xEB and body.startswith(RECIPE_TAG):
            k, n = struct.unpack(">BB", body[len(RECIPE_TAG):len(RECIPE_TAG) + 2])
            parts[k] = body[len(RECIPE_TAG) + 2:]
        i += 2 + ln
    if not parts:
        return None
    try:
        data = b"".join(parts[k] for k in sorted(parts))
        return json.loads(zlib.decompress(data).decode("utf-8"))
    except Exception:
        return None


def import_photo(src, country_dir, city, rot=0, flip=None, dev=None, name_hint=None,
                 angle=0, masks=None, recipe=None):
    """Copy a library photo into the Images/ archive. HEIC becomes JPEG
    (quality 95, ICC + EXIF preserved); untouched non-HEIC files are copied
    byte-for-byte. Rotation/flip/develop re-encode the archive COPY only —
    the file in the source library is never modified. Returns the Path."""
    city = clean_dirname(city)
    dest_dir = country_dir / city if city else country_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    rot = int(rot or 0) % 360
    try:
        angle = float(angle or 0)
    except (TypeError, ValueError):
        angle = 0.0
    angle = max(-45.0, min(45.0, angle)) if angle == angle and abs(angle) != float("inf") else 0.0   # finite, a real tilt
    flip = flip if flip in ("h", "v", "hv", "vh") else None
    dev = {k: v for k, v in (dev or {}).items() if k in DEV_KEYS and abs(float(v)) > 1e-4}
    masks = masks or []
    # anything that is not a JPEG is re-encoded to one (ICC kept): a .png/.webp copied as it was had no tiers
    # cut for it, since the tier tools only know jpg/heic originals (Blue Cruise's Mr Ceyhun photos, 2026-10-16)
    reencode = (src.suffix.lower() not in (".jpg", ".jpeg") or rot or flip or dev
                or abs(angle) > 1e-3 or masks)
    base = clean_name((name_hint or src).stem if hasattr(name_hint or src, "stem")
                      else str(name_hint))
    ext = ".jpg" if reencode else src.suffix
    dest, n = dest_dir / f"{base}{ext}", 2
    while True:                                   # the name is RESERVED, not just checked: 8 parallel imports of one
        try:                                      # photo shared one file and read each other half-written (2026-10-16)
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.close(fd)
            break
        except FileExistsError:
            dest, n = dest_dir / f"{base}-{n}{ext}", n + 1
    try:
        _import_write(src, dest, reencode, rot, flip, angle, dev, masks, recipe)
    except Exception as e:
        dest.unlink(missing_ok=True)              # a truncated or unreadable source leaves nothing behind
        raise ValueError("the source could not be imported: %s" % str(e)[:120]) from e
    return dest


def _import_write(src, dest, reencode, rot, flip, angle, dev, masks, recipe):
    if reencode:
        im = ImageOps.exif_transpose(Image.open(src))
        icc = im.info.get("icc_profile")           # grab BEFORE convert()
        exif = im.info.get("exif")
        im = apply_orient(im, rot, flip)
        im = im.convert("RGB")
        im = straighten(im, angle)
        if dev or masks:
            im = develop(im, dev, brush_alphas(masks, im.size))
        kw = {"quality": 95}
        if icc:
            kw["icc_profile"] = icc
        if exif and not (rot or flip):             # stale Orientation would re-rotate
            kw["exif"] = exif
        if recipe:
            kw["extra"] = recipe_segments(recipe)
        im.save(dest, "JPEG", **kw)
    else:
        with Image.open(src) as chk:
            chk.load()                            # verify() misses a truncated JPEG; a full decode does not
        shutil.copy2(src, dest)
        if recipe and dest.suffix.lower() in (".jpg", ".jpeg"):
            _inject_app11(dest, recipe_segments(recipe))   # an erase-only or untouched insert could not be reopened or synced


def _recipe_point_at_archive(dest, d):
    """A photo added from a file: its staging copy under .tmp is disposable, so the recipe names the archive copy
    itself as the source (✎ Edit then reopens Images/<Country>/<City>/<file>, which is always there)."""
    if dest.suffix.lower() not in (".jpg", ".jpeg"):
        return
    label = next((sd["label"] for sd in SOURCES if Path(sd["path"]).resolve() == IMAGES.resolve()), None)
    if not label:
        return
    rec = read_recipe(dest) or recipe_for(d)
    rec["src"] = {"label": label, "path": dest.relative_to(IMAGES).as_posix()}
    # the archive copy IS the developed photo: rotation, straighten, sliders, masks and erases are baked into it,
    # so a recipe that still carried them made Edit apply them a second time (stress test 2026-10-23: a photo
    # rotated once came back sideways, exposure +60 came back at 227 mean brightness)
    rec.update({"rot": 0, "flip": None, "angle": 0.0, "dev": {}, "masks": [], "erases": []})
    b = dest.read_bytes()
    if b[:2] != b"\xff\xd8":
        return
    out, i = b[:2], 2                                     # the old recipe segments out, the new ones in
    while i + 4 <= len(b) and b[i] == 0xFF and 0xE0 <= b[i + 1] <= 0xEF:
        ln = int.from_bytes(b[i + 2:i + 4], "big")
        seg = b[i:i + 2 + ln]
        if not (b[i + 1] == 0xEB and seg[4:4 + len(RECIPE_TAG)] == RECIPE_TAG):
            out += seg
        i += 2 + ln
    dest.write_bytes(out + recipe_segments(rec) + b[i:])


def _inject_app11(path, segments):
    """Splice ready-made APPn segments into a JPEG right after its leading APP segments, without re-encoding."""
    b = path.read_bytes()
    if b[:2] != b"\xff\xd8":
        return
    i = 2
    while i + 4 <= len(b) and b[i] == 0xFF and 0xE0 <= b[i + 1] <= 0xEF:
        i += 2 + int.from_bytes(b[i + 2:i + 4], "big")
    path.write_bytes(b[:i] + segments + b[i:])


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
    resp = send_from_directory(ROOT, rel)
    if rel.replace("\\", "/").startswith("Images/web/flags/"):
        # the Library redraws its album cards every 6 s and asked for every flag again each time: 480
        # revalidations a minute from one idle tab (stress test 2026-10-09)
        resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/api/proof_rules")
def api_proof_rules():
    """The editor's live proofing reads the same British -> American table and protected names as
    tools/americanize.py, so the underline and the batch sweep never disagree."""
    import americanize
    return jsonify({"amer": dict(americanize.AMER), "keep": list(americanize.KEEP)})


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
    _save_sources()                                # the guarded writer: a test instance never rewrites the tracked file


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
# Thumbnails come first. A thumbnail being built for a request raises this; background work that
# decodes the same kind of file (the preview warm-up) waits while it is up, so the grid you are
# looking at never shares the CPU with viewer images you have not opened (2026-10-05).
_thumb_live = 0
_thumb_live_lock = threading.Lock()


class _ThumbLive:
    def __enter__(self):
        global _thumb_live
        with _thumb_live_lock:
            _thumb_live += 1

    def __exit__(self, *a):
        global _thumb_live
        with _thumb_live_lock:
            _thumb_live -= 1


def _yield_to_thumbs(max_wait=30.0):
    """Background decodes call this first: while a thumbnail request is in flight they sleep."""
    t0 = time.time()
    while _thumb_live > 0 and time.time() - t0 < max_wait:
        time.sleep(0.05)
_thumb_gate = threading.Semaphore(6)   # decode a few at a time (6: HEIC decoding scales to 8 on 16 threads, 2026-10-05); a burst of iCloud
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
    return send_file(thumb_file(p, size, rot, flip), mimetype="image/jpeg")


def thumb_file(p, size, rot, flip):
    """The cached preview JPEG of `p` at `size` (built if missing). The stage shows this file, and the
    planes / level routes read the same pixels."""
    key = f"{p}-{p.stat().st_mtime_ns}-{size}-{rot}-{flip}"
    cache = THUMBS / (re.sub(r"\W", "_", key)[-120:] + ".jpg")
    # A backed-up photo is decoded ONCE, into the library's 400 px copy, and every smaller grid tile
    # is cut from that (Kevin, 2026-10-02: 13,773 of the albums' photos are HEIC originals with no JPEG
    # twin, and the sidebar's 320 px tile and the library's 400 px one each decoded the full 24 MP file)
    shared = None
    if size <= 2000 and not rot and not flip:
        try:
            if BACKUP.resolve() in p.parents:
                with _ThumbLive():
                    shared = _build_bthumb(p, 400 if size <= 400 else 2000)
        except Exception:
            shared = None
    if not cache.exists():
        with _ThumbLive(), _one_build(cache), _thumb_gate:
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
                tmp = _part_name(cache)
                im.convert("RGB").save(tmp, "JPEG", **kw)
                os.replace(tmp, cache)                        # never a half-written file for another reader
    return cache


def _stage_image():
    """The pixels the stage shows for this request: the erased session (?token) at ?s, a repo file (?site)
    at ?s, or the library photo's cached preview (?root&path&s&rot&flip)."""
    size = int(request.args.get("s", 2000))
    if request.args.get("token"):
        p = ERASED / (re.sub(r"\W", "", request.args["token"]) + ".jpg")
        if not p.exists():
            abort(410)
        im = Image.open(p).convert("RGB")
        im.thumbnail((size, size))
        return im, None
    if request.args.get("site"):
        rel = _rel_clean(request.args["site"])
        p = (ROOT / rel).resolve()
        if ROOT.resolve() not in p.parents or not p.exists():
            abort(404)
        im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
        im.thumbnail((size, size))
        return im, None
    if not request.args.get("root") or not request.args.get("path"):
        abort(400)
    try:
        p = source_path(request.args["root"], request.args["path"])
        p.stat()
    except FileNotFoundError:
        abort(404)
    except (ValueError, KeyError, OSError):
        abort(400)
    rot = int(request.args.get("rot", 0)) % 360
    flip = request.args.get("flip") or None
    f = thumb_file(p, size, rot, flip)
    return Image.open(f).convert("RGB"), f


@app.route("/api/planes")
def api_planes():
    """The photo's spatial planes for the live preview (develop_v2.planes, packed): computed from the very
    preview the stage shows, so the shader and the bake read the same base layers. Cached beside the preview."""
    import numpy as np
    im, f = _stage_image()
    cache = f.with_suffix(".planes") if f else None
    if cache and cache.exists():
        return Response(cache.read_bytes(), mimetype="application/octet-stream")
    data = _dev2.pack_planes(_dev2.planes(np.asarray(im, dtype=np.float32) / 255.0))
    if cache:
        try:
            tmp = _part_name(cache)
            tmp.write_bytes(data)
            os.replace(tmp, cache)
        except OSError:
            pass
    return Response(data, mimetype="application/octet-stream")


@app.route("/api/level")
def api_level():
    """Auto straighten: the angle that levels the photo's horizon / plumb lines (develop_v2.level_angle), or
    null when its lines do not agree."""
    import numpy as np
    im, _ = _stage_image()
    try:
        angle = _dev2.level_angle(np.asarray(im.convert("L")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "angle": angle})


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
    exif_b = None
    if d.get("token"):
        im0 = Image.open(ERASED / (re.sub(r"\W", "", d["token"]) + ".jpg"))
        exif_b = im0.info.get("exif")
        im = im0.convert("RGB")
        icc = im.info.get("icc_profile")
    else:
        src = resolve_full_res(source_path(d["root"], d["path"]))
        im0 = Image.open(src)
        try:                                     # the capture date and GPS travel on (orientation reset: pixels are upright now)
            ex = im0.getexif(); ex[0x0112] = 1; exif_b = ex.tobytes()
        except Exception:
            exif_b = None
        im = ImageOps.exif_transpose(im0)
        icc = im.info.get("icc_profile")
        im = apply_orient(im, d.get("rot", 0), d.get("flip")).convert("RGB")
        im = straighten(im, d.get("angle", 0))   # erase masks are painted on
    raw = base64.b64decode(d["mask"].split(",", 1)[1])   # the straightened view
    mask = Image.open(io.BytesIO(raw)).convert("L").resize(im.size, Image.NEAREST)
    mode = d.get("mode") or "ai"

    def work(progress):
        out = spot_erase(im, mask) if mode == "spot" else lama_erase(im, mask, progress=progress)
        if progress(0.97, "saving") is False:
            raise RuntimeError("cancelled")
        token = uuid.uuid4().hex[:16]
        kw = {"quality": 95}
        if icc:
            kw["icc_profile"] = icc
        if exif_b:
            kw["exif"] = exif_b
        out.save(ERASED / (token + ".jpg"), "JPEG", **kw)
        return {"token": token}

    if d.get("bg"):
        # a job: the dialog stays usable and the editor watches /api/jobs for the token
        name = Path(d.get("path") or "photo").name
        jid = job_new("erase", ("Spot heal: " if mode == "spot" else "Erasing: ") + name, photo=d.get("path") or "", mode=mode)
        job_submit(jid, work)
        return jsonify({"ok": True, "job": jid})
    try:
        r = work(lambda f, n=None: True)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "token": r["token"]})


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
    d = request.get_json(force=True) or {}
    # a change another tab accepted AND saved is in the article: a later Reject from a stale tab would record
    # "Rejected" over text that is already there (stress test 2026-10-23); it stays accepted and the tab is told
    done = set()
    for fn in ("progress.json", "applied.json"):
        f = _redline.STORE / slug / fn
        if f.exists():
            try:
                done |= set(json.loads(f.read_text(encoding="utf-8")).get("ids") or [])
            except Exception:
                pass
    kept = [k for k, v in (d.items() if isinstance(d, dict) else []) if v is False and k in done]
    for k in kept:
        d[k] = True
    with _decisions_lock:
        return jsonify({"ok": True, "decisions": _redline.merge_decisions(slug, d), "kept": kept})


_decisions_lock = threading.Lock()


@app.route("/review/<slug>/commit", methods=["POST", "OPTIONS"])
def review_commit(slug):
    if request.method == "OPTIONS":
        return "", 204
    if not (_redline.STORE / slug / "proposal.json").exists():
        abort(404)
    d = request.get_json(force=True) or {}
    return jsonify(_redline.commit(slug, d.get("accepted", [])))


@app.route("/review/<slug>/precheck", methods=["POST", "OPTIONS"])
def review_precheck(slug):
    """asked by the editor BEFORE it writes the article: would this save strand a moved sentence or
    take a Maps link off the site? (redline.precheck)"""
    if request.method == "OPTIONS":
        return "", 204
    if not (_redline.STORE / slug / "proposal.json").exists():
        abort(404)
    raw = request.get_data()
    if request.headers.get("Content-Encoding") == "gzip":
        dec = zlib.decompressobj(16 + zlib.MAX_WBITS)          # bounded, like merge3: a 306 KB bomb inflated to 300 MB here
        raw = dec.decompress(raw, 48 * 1024 * 1024)
        if dec.unconsumed_tail:
            abort(413)
    d = json.loads(raw.decode("utf-8") or "{}")
    if not isinstance(d, dict) or not isinstance(d.get("html"), str):
        abort(400)
    return jsonify(_redline.precheck(slug, d.get("accepted") or [], d["html"]))


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
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800, creationflags=_NOWIN)
    line = (r.stdout or "").strip().splitlines()
    try:
        urls = json.loads(line[-1]) if line else {}
    except Exception:
        return jsonify({"ok": False, "log": (r.stdout + r.stderr)[-1500:]}), 500
    return jsonify({"ok": True, "urls": urls})


_PREVIEWS = {}       # rel -> html the editor holds right now, served back under the page's own path


def _sweep_stale_temps():
    """temp files a killed server left behind (thumbnail .part files, merge3 work folders), over an hour old"""
    import tempfile
    cut = time.time() - 3600
    try:
        for f in THUMBS.glob("*.part*"):
            try:
                if f.stat().st_mtime < cut:
                    f.unlink()
            except OSError:
                pass
        for d in Path(tempfile.gettempdir()).glob("merge3-*"):
            try:
                if d.is_dir() and d.stat().st_mtime < cut:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
    except Exception:
        pass


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
    rel = _rel_clean(rel)
    if not rel or not html:
        abort(400)
    _PREVIEWS.pop(rel, None)
    _PREVIEWS[rel] = html
    while len(_PREVIEWS) > 20:                       # every preview was kept: 100 of them held 108 MB (stress 2026-10-09)
        _PREVIEWS.pop(next(iter(_PREVIEWS)))
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
    if request.method == "POST" and (request.content_length or 0) > 8 * 1024 * 1024:
        abort(413)                                 # a comment store is kilobytes; a 117 MB one was accepted once
    if request.method == "POST":
        raw = request.get_data(cache=True)
        if len(raw) > 8 * 1024 * 1024:             # a chunked body has no Content-Length: the cap held only on paper (stress test 2026-10-23)
            abort(413)
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
    if os.environ.get("PHOTO_EDITOR_NO_WORKER") == "1" or str(key).startswith("test__"):
        return                                      # a test server or a test store: no answers with Kevin's login
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
                if f.stem.startswith("test__") or f.stem == "x" or ".before-" in f.name or ".corrupt-" in f.name:
                    continue                      # backups are not live stores (a task was answered into one, 2026-10-16)
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or not isinstance(data.get("threads"), list):
                        continue
                    _answer_tasks(f.stem, data)
                except Exception:
                    continue                      # one bad store must not stop the stores after it
        except Exception:
            pass
        time.sleep(2)


# only as the server: a tool that imports this module for its helpers (the ingest, the backup) must
# not answer comments or compress pages on the side (2026-10-03)
if __name__ == "__main__" and os.environ.get("PHOTO_EDITOR_NO_WORKER") != "1":
    threading.Thread(target=_task_watch, daemon=True, name="task-watch").start()
    if not _env_key("ANTHROPIC_API_KEY"):
        threading.Thread(target=_worker.warm, daemon=True, name="claude-warm").start()   # boot the worker with the editor
        # and the alt-text writer, so the first photo is quick (after a pause: _alt is defined further down this file)
        threading.Thread(target=lambda: (time.sleep(8), _alt().warm()), daemon=True, name="alt-warm").start()


def _compute_albums():
    """Every album under ~/Backup with what it holds. The shared-album backup and its
    verification (phone counts, "safe to delete", hold, server counts) went with the
    watcher on 2026-10-12: albums come from icloud.com downloads now, so a card only
    says what is on disk."""
    out = []
    if not BACKUP.is_dir():
        return {"albums": out}
    for d in sorted(BACKUP.iterdir(), key=lambda x: x.name.lower()):
        if not d.is_dir() or d.name.startswith("_") or d.name.endswith(".replacing"):   # ingest staging is not an album
            continue
        # Exclude our metadata by NAME and count only real media. Filtering on a leading
        # "_" dropped India's _DSC4284.JPG, so the card read 798 of 800 on a complete album.
        files = [p for p in d.iterdir()
                 if p.is_file() and not p.name.startswith("_manifest")
                 and p.suffix.lower() in IMG_EXTS
                 and not is_stray_thumb(p.name)]
        vdir = d / "videos"
        vids = [p for p in vdir.iterdir()
                if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".m4v"}] if vdir.is_dir() else []
        if not files and not vids:
            continue
        out.append({"name": d.name, "onDisk": len(files),
                    "mb": round(sum(p.stat().st_size for p in files) / 1e6, 1),
                    "videos": len(vids),
                    "videosMb": round(sum(p.stat().st_size for p in vids) / 1e6, 1)})
    return {"albums": out}


# The listing stats ~13k backup files: seconds idle, longer under load. The library
# polls it every 6 s, and computed per request ten calls piled up in one tab and
# starved every other route. So: computed once up front, then refreshed on a
# background thread at most every BS_TTL seconds, and every request is answered
# from the last result at once.
_bs = {"v": None, "t": 0.0, "busy": False, "lock": threading.Lock()}
BS_TTL = 6


def _refresh_albums():
    try:
        v = _compute_albums()
        _bs["v"], _bs["t"] = v, time.time()
    except Exception as e:
        print("album listing failed:", e)
    finally:
        _bs["busy"] = False


@app.route("/api/albums")
def api_albums():
    with _bs["lock"]:
        if _bs["v"] is None and not _bs["busy"]:
            _bs["busy"] = True
            mode = "compute"
        elif _bs["v"] is None:
            mode = "wait"
        else:
            mode = "serve"
            # a refresh stuck on a slow disk read froze every card at its old numbers for
            # good (2026-10-03, Australia showed 930 of 1,029 until a restart): past 120 s,
            # start another
            stuck = _bs["busy"] and time.time() - _bs.get("started", 0) > 120
            if time.time() - _bs["t"] > BS_TTL and (not _bs["busy"] or stuck):
                _bs["busy"] = True
                _bs["started"] = time.time()
                threading.Thread(target=_refresh_albums, daemon=True).start()
    if mode == "compute":
        _refresh_albums()
    elif mode == "wait":
        for _ in range(900):
            if _bs["v"] is not None:
                break
            time.sleep(0.1)
    return jsonify(_bs["v"] or {"albums": []})


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


# ---- the suite's status for the editor's Live Activity panel ----
# What is up (photo server, covers, map editor, keeper), the tidy-up preview and the
# compression queue. The watcher controls and "Fix iCloud sync" that used to sit here
# went with the shared-album backup (2026-10-12).
import photo_suite
_suite_cache = {"t": 0, "v": None}


def _suite_status():
    """Never computed on a request: the editor polls /api/suite every 2 s while Live
    Activity is open, and a status computed per request once starved the photo library
    ("photo editor is having trouble loading", 2026-09-26). One daemon thread refreshes
    it every 15 s; a request only ever reads the last value."""
    if _suite_cache["v"] is None:
        _suite_cache["v"] = {"server": True, "heroes": photo_suite.port_open(5004),
                             "maps": photo_suite.port_open(5002), "keeper": photo_suite.port_open(5009)}
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


@app.route("/api/suite", methods=["GET", "OPTIONS"])
def api_suite():
    if request.method == "OPTIONS":
        return "", 204
    return jsonify({"status": _suite_status(), "tidy": _tidy_preview(),
                    "compress": _compress_status()})


def _merge_lines(mine, base, theirs):
    """Three-way merge on lines, finer than git's: within a stretch both sides changed, lines that only
    one side changed (or both changed the same way) merge one by one when the stretch kept its length
    on both sides. Returns the merged text, or None when any line was changed differently by both."""
    import difflib
    B, M, T = base.split("\n"), mine.split("\n"), theirs.split("\n")

    def matches(a, b):
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        out = {}
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                out[blk.a + k] = blk.b + k
        return out
    mb, tb = matches(B, M), matches(B, T)
    out, i, j, k = [], 0, 0, 0

    def chunk(b, m, t):
        if m == b:
            return t
        if t == b or m == t:
            return m
        if len(b) == len(m) == len(t):
            res = []
            for x, y, z in zip(b, m, t):
                if y == x:
                    res.append(z)
                elif z == x or y == z:
                    res.append(y)
                else:
                    return None
            return res
        # different lengths (one side split a paragraph, the other edited the next one): each side's edits
        # as ranges of base lines; when no base line was touched by both, both sets of edits go in. An
        # insertion touches the line it lands before. (A split next to another tab's edit was a false
        # clash that paused its autosave; stress test 2026-10-09.)
        def ops(x):
            sm = difflib.SequenceMatcher(None, b, x, autojunk=False)
            return [(i1, i2, x[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
        om, ot = ops(m), ops(t)
        span = lambda o: set(range(o[0], o[1])) | {o[0]}
        if any(span(x) & span(y) for x in om for y in ot):
            return None
        res, pos = [], 0
        for i1, i2, new in sorted(om + ot):
            if i1 < pos:
                return None
            res.extend(b[pos:i1]); res.extend(new); pos = i2
        res.extend(b[pos:])
        return res
    for b in range(len(B) + 1):
        if b < len(B):
            if b not in mb or b not in tb or mb[b] < j or tb[b] < k:
                continue
            m_end, t_end = mb[b], tb[b]
        else:
            m_end, t_end = len(M), len(T)
        got = chunk(B[i:b], M[j:m_end], T[k:t_end])
        if got is None:
            return None
        out.extend(got)
        if b < len(B):
            out.append(B[b])
        i, j, k = b + 1, m_end + 1, t_end + 1
    return "\n".join(out)


@app.route("/api/merge3", methods=["POST", "OPTIONS"])
def api_merge3():
    """Three-way merge for the editor's save (saveFile in editor.html).

    The editor keeps the article as it last read or wrote it (base). When the file on disk
    no longer matches that (theirs: a review round applied by this server, a second tab,
    Claude, fit_backgrounds, the compression pipeline), saving the editor's copy (mine)
    used to overwrite that change without a word. Accepted Armenia review changes and a
    whole paragraph vanished that way (2026-09-27). git merge-file does the merge: line
    based, identical changes on both sides merge cleanly, and edits on the same or
    neighbouring lines are a conflict, which the editor puts to Kevin instead of guessing.

    Returns {ok, conflicts, merged}; `merged` is only usable when conflicts == 0.
    """
    if request.method == "OPTIONS":
        return "", 204
    raw = request.get_data()
    if request.headers.get("Content-Encoding") != "gzip" and len(raw) > 48 * 1024 * 1024:
        return jsonify({"ok": False, "error": "the three copies are over 48 MB"}), 413     # the same cap the gzip path has (stress test 2026-10-23)
    if request.headers.get("Content-Encoding") == "gzip":
        # the editor gzips the three copies: a big article sent plain is a 4 MB+ body, and the dev
        # server reset the connection on those about a third of the time (QA 2026-10-08)
        import zlib
        dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
        # capped: a 248 KB gzip bomb inflated to 256 MB and took the server to 2.6 GB (stress 2026-10-09)
        raw = dec.decompress(raw, 48 * 1024 * 1024)
        if dec.unconsumed_tail:
            return jsonify({"ok": False, "error": "the three copies are over 48 MB unpacked"}), 413
    d = json.loads(raw.decode("utf-8") or "{}")
    if not isinstance(d, dict) or not all(isinstance(d.get(k), str) for k in ("mine", "base", "theirs")):
        abort(400)                                      # all three sides, as text: an empty request merged to ""
    texts = [d[k].replace("\r\n", "\n") for k in ("mine", "base", "theirs")]
    import tempfile
    with tempfile.TemporaryDirectory(prefix="merge3-") as td:
        paths = []
        for name, t in zip(("mine", "base", "theirs"), texts):
            p = Path(td) / name
            p.write_bytes(t.encode("utf-8"))
            paths.append(str(p))
        try:
            r = subprocess.run(["git", "merge-file", "-p", *paths], capture_output=True, timeout=60,
                               creationflags=0x08000000 if sys.platform == "win32" else 0)
        except (OSError, subprocess.TimeoutExpired) as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    # exit status = number of conflicts (0 = clean); negative or > 127 = git failed
    if r.returncode < 0 or r.returncode > 127:
        return jsonify({"ok": False, "error": (r.stderr or b"").decode("utf-8", "replace")[:300]}), 500
    merged = r.stdout.decode("utf-8") if r.returncode == 0 else None
    if merged is None:
        # git calls edits on NEIGHBOURING lines a conflict, and a paragraph is one line: accepting a review
        # change next to another changed paragraph raised the dialog over two edits that never touch
        # (QA 2026-10-08). A line-by-line pass settles those; anything truly overlapping stays a conflict.
        merged = _merge_lines(*texts)
    mine, base, theirs = texts
    # A file cut short on disk (a tool that died mid-write, an empty file) is a DELETION to git, and
    # with the editor's side unchanged there it merged "cleanly" into the cut-short file, which the
    # editor would then write (QA 2026-10-08: an empty disk file merged to ""). An article that loses
    # its closing tag, or most of its length, is never a clean merge.
    def whole(t):
        return "</html>" in t[-4000:].lower()
    if merged is not None and ((whole(base) or whole(mine)) and not whole(merged)
                               or len(merged) < 0.6 * min(len(base), len(mine))):
        return jsonify({"ok": True, "conflicts": 1, "merged": None, "why": "truncated"})
    return jsonify({"ok": True, "conflicts": 0 if merged is not None else max(r.returncode, 1), "merged": merged})


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


UPLOADS = ROOT / ".tmp" / "photo_editor" / "uploads"


def ensure_uploads_source():
    """Index of the toolbar's upload folder in SOURCES, registering it if absent (the Backup's pattern): a photo
    picked with the toolbar's image button is staged here, then goes through the same crop/import as any other
    photo, which copies it into Images/<Country>/<City>/. Staging only; .tmp is disposable."""
    UPLOADS.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(SOURCES):
        if Path(s["path"]).resolve() == UPLOADS.resolve():
            return i
    SOURCES.append({"label": "Uploaded photos", "path": str(UPLOADS)})
    _save_sources()
    return len(SOURCES) - 1


@app.route("/api/upload_photo", methods=["POST", "OPTIONS"])
def api_upload_photo():
    """The toolbar's image button (Kevin, 2026-10-06): the picked file's bytes, as they are, into the upload
    folder under its own name (never overwriting); the editor then opens the crop step on {root, path}."""
    if request.method == "OPTIONS":
        return ("", 204)
    raw = os.path.basename(request.args.get("name", "") or "photo.jpg")
    ext = Path(raw).suffix.lower()
    if ext not in IMG_EXTS:
        return jsonify(ok=False, error="not a photo file (%s)" % (ext or "no extension")), 415
    data = request.get_data(cache=False)
    if not data:
        return jsonify(ok=False, error="empty file"), 400
    try:                                              # a file named .jpg that is not a photo is refused (stress 2026-10-09)
        import io as _io
        Image.open(_io.BytesIO(data)).verify()
    except Exception:
        return jsonify(ok=False, error="not a readable photo"), 415
    root = ensure_uploads_source()
    base = clean_name(Path(raw).stem) or "photo"
    dest, n = UPLOADS / f"{base}{ext}", 2
    while True:                                   # reserved, not just checked: 4 parallel uploads of "image.jpg" shared a file (2026-10-23)
        try:
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.close(fd)
            break
        except FileExistsError:
            dest, n = UPLOADS / f"{base}-{n}{ext}", n + 1
    dest.write_bytes(data)
    return jsonify(ok=True, root=root, path=dest.name, name=dest.name)


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
    _save_sources()
    return len(SOURCES) - 1


def _save_sources():
    """the tracked tools/photo_editor_sources.json. A test instance (PHOTO_EDITOR_NO_WORKER=1, often with a
    made-up home folder) keeps its sources in memory: one added ".tmp/qa/stressB/home/Backup" to the real
    file during the stress test (2026-10-09)."""
    if os.environ.get("PHOTO_EDITOR_NO_WORKER") == "1":
        return
    try:
        SRC_CFG.write_text(json.dumps(SOURCES, indent=2), encoding="utf-8")
    except OSError:
        pass


@app.route("/api/resolve_original")
def api_resolve_original():
    """Archive original for any article img ref - web variant or original.

    The editor's Edit button needs to reopen the crop window on the ORIGINAL,
    but after the pipeline runs, an article img points at a derived variant
    ("Images/web/<C>/x-mob-2x.jpg"). Strip the web/ tier and the density
    suffix, then find the archive file case-insensitively by stem (imports
    re-encode HEIC to .jpg but byte-copies keep .JPG/.JPEG as they came).
    """
    rel = _rel_clean(unquote(request.args.get("path", "")))
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


_STRAIGHTEN_LOCK = threading.Lock()


def straighten_record(dest, src_root, src_path, angle):
    with _STRAIGHTEN_LOCK:                        # read-modify-write: 8 parallel imports left 4 of 180 records (2026-10-16)
        return _straighten_record(dest, src_root, src_path, angle)


def _straighten_record(dest, src_root, src_path, angle):
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
        _atomic_write(STRAIGHTEN_FILE, json.dumps(recs, indent=1))
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
    """Cache location for a backed-up photo's thumbnail (the ingest used to
    pre-generate these on import so the
    library grid is instant instead of decoding 5712px HEICs on demand."""
    key = f"bk-{p}-{p.stat().st_mtime_ns}-{size}"
    return THUMBS / (re.sub(r"\W", "_", key)[-120:] + ".jpg")


# One build per cache file, and a temp name per writer. Twelve requests for the same new tile all
# decoded the photo and wrote the same "x.part.jpg": half answered 500 (the file was gone or locked)
# and good photos answered 415 (stress test 2026-10-09). Now the first builds and the rest wait for it.
_build_locks, _build_locks_guard = {}, threading.Lock()


class _one_build:
    def __init__(self, path):
        self.key = str(path)

    def __enter__(self):
        with _build_locks_guard:
            ent = _build_locks.setdefault(self.key, [threading.Lock(), 0])
            ent[1] += 1
        self.ent = ent
        ent[0].acquire()
        return self

    def __exit__(self, *a):
        self.ent[0].release()
        with _build_locks_guard:
            self.ent[1] -= 1
            if self.ent[1] <= 0:
                _build_locks.pop(self.key, None)


def _part_name(path):
    return path.with_name("%s.%d.%d.part%s" % (path.stem, os.getpid(), threading.get_ident(), path.suffix))


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
    with _one_build(cache), gate:
        if cache.exists():
            return cache
        small = bthumb_path(p, 400) if size > 400 else None   # the grid's size, written from the same decode
        if small is not None and small.exists():
            small = None
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
        im = im.convert("RGB")
        tmp = _part_name(cache)
        im.save(tmp, "JPEG", **kw)
        os.replace(tmp, cache)                                # atomic: a reader never sees a half-written file
        if small is not None and not small.exists():          # a 2000 px preview was decoded: the 400 is free
            s4 = im.copy(); s4.thumbnail((400, 400), reducing_gap=2.0)
            t4 = _part_name(small)
            s4.save(t4, "JPEG", **kw)
            os.replace(t4, small)
    return cache


# the library grid asks for s=400; build the missing ones the moment an album
# is opened, on a pool of two so a big album never starves the live requests
_lib_warm_pool = ThreadPoolExecutor(max_workers=2)
_lib_warmed = set()
_lib_warm_lock = threading.Lock()


def _warm_library_album(album, paths):
    for p in paths:
        try:
            _yield_to_thumbs()                                # the grid's own requests come first
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


_warm_thumbs_queued = set()


@app.route("/api/warm_thumbs", methods=["POST", "OPTIONS"])
def api_warm_thumbs():
    """The editor's sidebar shows an album (the library grid or the picks): build its 400 px tiles in the
    background, the way the Photo Library has always had done for it, so scrolling is instant (2026-10-05).
    Names first (what is on screen), then the rest of the album; live tile requests still go first."""
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    album = clean_dirname(body.get("album", ""))
    d = BACKUP / album
    if not album or not d.is_dir():
        return jsonify(ok=False), 404
    # An album checked whole lately, with nothing missing and the folder unchanged since, is not checked
    # again: every article open, album switch and Load more re-checked every photo (about five file
    # calls each), 0.7 s for Armenia on a calm machine and up to 26 s under load (stress test 2026-10-09)
    full = not body.get("only")
    try:
        stamp = d.stat().st_mtime_ns
    except OSError:
        stamp = None
    memo = _warm_seen.get(album)
    if full and memo and memo[0] == stamp and time.time() - memo[1] < 600:
        return jsonify(ok=True, queued=0)
    names = [os.path.basename(n) for n in (body.get("names") or [])[:600]]
    rest = [] if not full else sorted(p.name for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS and not is_stray_thumb(p.name))
    todo = []
    root, seen = BACKUP.resolve(), set(names)
    for n in names + [n for n in rest if n not in seen]:
        p = (d / n).resolve()
        if root not in p.parents or p in _warm_thumbs_queued or not p.is_file() or bthumb_path(p, 400).exists():
            continue
        _warm_thumbs_queued.add(p); todo.append(p)
    if todo:
        _lib_warm_pool.submit(_warm_library_album, album, todo)
    elif full:
        _warm_seen[album] = (stamp, time.time())
    return jsonify(ok=True, queued=len(todo))


_warm_seen = {}      # album -> (folder mtime, when): checked whole, nothing was missing


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
        _preview_warm_pool.submit(lambda q=p: (_yield_to_thumbs(), _build_bthumb(q, 2000) if not bthumb_path(q, 2000).exists() else None))
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
        if size < 1000:                                       # a grid tile: background decodes wait for it
            with _ThumbLive():
                cache = _build_bthumb(p, size)
        else:
            cache = _build_bthumb(p, size)
    except Exception:
        abort(415)
    return send_file(cache, mimetype="image/jpeg")


def recipe_for(d):
    """The recipe an import carries: the library source (by the source's LABEL, since the root indexes shift
    when sources are added), orientation, straighten, sliders, masks (a brush mask as its PNG) and the erases
    done (each as its painted mask and mode, so a re-edit can replay them)."""
    try:
        label = SOURCES[int(d["root"])]["label"]
    except (KeyError, ValueError, IndexError, TypeError):
        label = ""
    r = d.get("recipe") or {}
    return {"v": 1, "src": {"label": label, "path": d["path"]},
            "rot": int(d.get("rot", 0) or 0), "flip": d.get("flip") or None, "angle": float(d.get("angle", 0) or 0),
            "dev": {k: v for k, v in (d.get("dev") or {}).items() if abs(float(v or 0)) > 1e-4},
            "masks": d.get("masks") or [], "erases": r.get("erases") or []}


def _source_index(label):
    for i, srcd in enumerate(SOURCES):
        if srcd.get("label") == label:
            return i
    return None


def _archive_for_ref(ref):
    """The Images/ file an article <img> ref points at (a web variant walks back to its original)."""
    rel = _rel_clean(unquote(ref or ""))
    rel = re.sub(r"^(\.\./)+", "", rel)
    if rel.startswith("Images/"):
        rel = rel[len("Images/"):]
    if rel.startswith("web/"):
        rel = rel[len("web/"):]
    rel = re.sub(r"-(?:mob-)?[123]x(?=\.[^.]+$)", "", rel)
    cand = IMAGES / rel
    folder, stem = cand.parent, cand.stem
    if folder.is_dir():
        low = stem.lower()
        for f in folder.iterdir():
            if f.is_file() and f.stem.lower() == low and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
                return f
    return None


@app.route("/api/recipe")
def api_recipe():
    """The edit recipe of a placed photo (read out of the archive JPEG), with its source resolved to the
    current root index, or {ok: false} when the photo has none (placed before 2026-10-14, or copied as is)."""
    hit = _archive_for_ref(request.args.get("path", ""))
    if not hit:
        return jsonify({"ok": False, "error": "no archive original"}), 404
    rec = read_recipe(hit)
    if not rec:
        return jsonify({"ok": False, "error": "no recipe in this photo"})
    root = _source_index((rec.get("src") or {}).get("label", ""))
    if root is None:
        return jsonify({"ok": False, "error": "its photo source is no longer configured"})
    src = source_path(root, rec["src"]["path"])
    if not src.exists():
        return jsonify({"ok": False, "error": "its source photo is gone"})
    return jsonify({"ok": True, "recipe": rec, "root": root, "path": rec["src"]["path"], "name": src.name,
                    "archive": hit.relative_to(ROOT).as_posix()})


def replay_erases(im, erases):
    """Run a recipe's erases again on the (straightened) source: each one's painted mask at its mode."""
    import base64
    import io
    for e in erases or []:
        png = e.get("png") or ""
        if "," not in png:
            continue
        raw = base64.b64decode(png.split(",", 1)[1])
        mask = Image.open(io.BytesIO(raw)).convert("L").resize(im.size, Image.NEAREST)
        im = spot_erase(im, mask) if e.get("mode") == "spot" else lama_erase(im, mask)
    return im


@app.route("/api/recipe_session", methods=["POST", "OPTIONS"])
def api_recipe_session():
    """A re-edit of a photo whose recipe holds erases: the erases are replayed on the source (oriented and
    straightened as the recipe says) and the result becomes an erase session token, like a fresh erase."""
    if request.method == "OPTIONS":
        return "", 204
    import uuid
    d = request.get_json(force=True) or {}
    if not isinstance(d, dict) or not d.get("path"):
        abort(400)
    src = resolve_full_res(source_path(d["root"], d["path"]))
    im0 = Image.open(src)
    try:                                     # capture date and GPS travel on through the replayed erases
        ex = im0.getexif(); ex[0x0112] = 1; exif_b = ex.tobytes()
    except Exception:
        exif_b = None
    im = ImageOps.exif_transpose(im0)
    icc = im.info.get("icc_profile")
    im = apply_orient(im, d.get("rot", 0), d.get("flip")).convert("RGB")
    im = straighten(im, d.get("angle", 0))
    try:
        im = replay_erases(im, d.get("erases"))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    token = uuid.uuid4().hex[:16]
    kw = {"quality": 95}
    if icc:
        kw["icc_profile"] = icc
    if exif_b:
        kw["exif"] = exif_b
    im.save(ERASED / (token + ".jpg"), "JPEG", **kw)
    return jsonify({"ok": True, "token": token})


@app.route("/api/sync_look", methods=["POST", "OPTIONS"])
def api_sync_look():
    """LightCraft's Sync: one photo's sliders (not its masks, crop or erases) onto the other photos of the
    article that carry a recipe; each is re-rendered from its own source and written as a new archive file."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True) or {}
    src_hit = _archive_for_ref(d.get("source", ""))
    rec = read_recipe(src_hit) if src_hit else None
    if not rec:
        return jsonify({"ok": False, "error": "the chosen photo has no edit recipe"}), 400
    dev = rec.get("dev") or {}
    out = []
    targets = list(dict.fromkeys(t for t in (d.get("targets") or []) if isinstance(t, str)))   # each photo once
    if len(targets) > 500:
        abort(400, "too many targets (max 500)")
    for ref in targets:
        hit = _archive_for_ref(ref)
        tr = read_recipe(hit) if hit else None
        if not tr or hit == src_hit:
            out.append({"ref": ref, "skipped": "no recipe"})
            continue
        root = _source_index((tr.get("src") or {}).get("label", ""))
        if root is None:
            out.append({"ref": ref, "skipped": "source missing"})
            continue
        try:
            tr = dict(tr, dev=dev)
            srcp = resolve_full_res(source_path(root, tr["src"]["path"]))
            rel = hit.relative_to(IMAGES).as_posix().split("/")
            country, city = rel[0], rel[1] if len(rel) > 2 else ""
            masks = brush_alphas  # (resolved inside import_photo)
            if tr.get("erases"):
                im = ImageOps.exif_transpose(Image.open(srcp))
                icc = im.info.get("icc_profile")
                im = straighten(apply_orient(im, tr.get("rot", 0), tr.get("flip")).convert("RGB"), tr.get("angle", 0))
                im = replay_erases(im, tr["erases"])
                tmp = ERASED / ("sync-" + hit.stem + ".jpg")
                kw = {"quality": 95}
                if icc:
                    kw["icc_profile"] = icc
                im.save(tmp, "JPEG", **kw)
                dest = import_photo(tmp, country_dir(country), city, dev=dev, masks=tr.get("masks"), name_hint=srcp, recipe=tr)
            else:
                dest = import_photo(srcp, country_dir(country), city, rot=tr.get("rot", 0), flip=tr.get("flip"), dev=dev,
                                    angle=tr.get("angle", 0), masks=tr.get("masks"), recipe=tr)
            out.append({"ref": ref, "src": dest.relative_to(ROOT).as_posix()})
        except Exception as e:
            out.append({"ref": ref, "skipped": str(e)})
    return jsonify({"ok": True, "results": out})


# ---------------------------------------------------------------- MobileSAM: click to select a subject
SAM_PT = ROOT / ".tmp" / "photo_editor" / "models" / "mobile_sam.pt"
_sam = {"pred": None, "key": None, "lock": threading.Lock()}


def sam_predictor():
    """MobileSAM (Apache-2.0; EffectCraft's Roto Brush uses the same model), loaded once."""
    if _sam["pred"] is None:
        import warnings
        warnings.filterwarnings("ignore")
        from mobile_sam import SamPredictor, sam_model_registry
        if not SAM_PT.exists():
            raise RuntimeError("mobile_sam.pt missing from .tmp/photo_editor/models")
        model = sam_model_registry["vit_t"](checkpoint=str(SAM_PT))
        model.eval()
        _sam["pred"] = SamPredictor(model)
    return _sam["pred"]


@app.route("/api/segment", methods=["POST", "OPTIONS"])
def api_segment():
    """A mask for the clicked subject: points (x, y, label 1 = include / 0 = exclude) in the pixels of the
    stage image (?token / root+path at ?s, as /api/planes reads it). The image embedding is kept for the
    next click on the same photo (the encoder is the slow part; the decoder answers in milliseconds)."""
    if request.method == "OPTIONS":
        return "", 204
    import base64
    import io
    d = request.get_json(force=True) or {}
    pts = d.get("points") or []
    if not pts:
        abort(400)
    with app.test_request_context(query_string={k: str(v) for k, v in d.items() if k in ("token", "root", "path", "s", "rot", "flip", "site") and v is not None}):
        im, f = _stage_image()
    key = "%s|%s|%s|%s|%s|%s" % (d.get("token"), d.get("root"), d.get("path"), d.get("s"), d.get("rot"), d.get("flip"))
    try:
        with _sam["lock"]:
            pred = sam_predictor()
            if _sam["key"] != key:
                pred.set_image(np.asarray(im))
                _sam["key"] = key
            coords = np.array([[float(p[0]), float(p[1])] for p in pts])
            labels = np.array([int(p[2]) if len(p) > 2 else 1 for p in pts])
            masks, scores, _ = pred.predict(point_coords=coords, point_labels=labels, multimask_output=len(pts) == 1)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    # one click: SAM answers at three granularities (whole / part / sub-part) with a confidence each. EffectCraft
    # takes the most confident; a click on a wall then selects a window. Lightroom's Select Object means the
    # object, so of the answers within 0.05 of the best confidence the LARGEST is taken.
    top = float(scores.max())
    cands = [i for i in range(len(scores)) if scores[i] >= top - 0.05]
    best = masks[max(cands, key=lambda i: int(masks[i].sum()))]
    # white with the mask as ALPHA: the editor draws it straight into a brush mask's canvas (alpha is the mask)
    a8 = (best.astype(np.uint8) * 255)
    out = Image.fromarray(np.dstack([np.full_like(a8, 255)] * 3 + [a8]), "RGBA")
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return jsonify({"ok": True, "png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
                    "w": out.width, "h": out.height, "score": float(scores.max())})


# ---------------------------------------------------------------- culling scores (LightCraft cull.rs)
CULL_DIR = ROOT / ".tmp" / "photo_editor" / "cull"
CULL_DIR.mkdir(parents=True, exist_ok=True)


def cull_measure(path):
    """Focus (detail energy of the sharpest 16 px blocks, 90th percentile), clipped fraction and a small
    luminance signature for finding bursts; measured on the 400 px thumb so scores compare across photos."""
    import cv2
    im = Image.open(path).convert("L")
    im.thumbnail((400, 400))
    g = np.asarray(im, dtype=np.float32) / 255.0
    lap = cv2.Laplacian(g, cv2.CV_32F, ksize=3)
    h, w = g.shape
    bh, bw = h // 16, w // 16
    if bh < 1 or bw < 1:
        return {"sharp": 0, "clip": 0, "sig": [0.0] * 64}
    blocks = (lap[:bh * 16, :bw * 16] ** 2).reshape(bh, 16, bw, 16).mean(axis=(1, 3))
    sharp = float(np.sqrt(np.percentile(blocks, 90)))
    clip = float(((g >= 0.98) | (g <= 0.02)).mean())
    sig = cv2.resize(g, (8, 8), interpolation=cv2.INTER_AREA).ravel()
    sig = (sig - sig.mean()) / (sig.std() + 1e-6)
    return {"sharp": round(sharp, 4), "clip": round(clip, 4), "sig": [round(float(v), 3) for v in sig]}


_cull_locks, _cull_locks_guard = {}, threading.Lock()


@app.route("/api/cull")
def api_cull():
    """The album's culling scores (cached per file), and its bursts: runs of consecutive photos whose
    signatures agree, each with the sharpest one named. One scan per album at a time: a cold 1,100-photo
    album took 26 s, and every 6 s tick in between started another full scan (five at once, the last answering
    93 s after the first; stress test 2026-10-15). Later callers wait for the first and read its cache."""
    album = clean_dirname(request.args.get("album", ""))
    d = BACKUP / album
    if not album or not d.is_dir():
        return jsonify({"photos": {}, "bursts": []})
    with _cull_locks_guard:
        lock = _cull_locks.setdefault(album, threading.Lock())
    quick = request.args.get("quick") == "1"
    if quick and not lock.acquire(blocking=False):
        # the background scan holds the lock: answer from the cache as it stands (it is written every few
        # seconds during a scan), so the tiles get their marks while the scan goes on
        return jsonify(_cull_now(album, d, budget=0))
    if not quick:
        lock.acquire()
    try:
        res = _cull_now(album, d, budget=1.5 if quick else None)
    finally:
        lock.release()
    if quick and res.get("pending"):
        # the rest of the album is measured in the background; the editor polls until pending is 0
        def finish():
            if not lock.acquire(blocking=False):
                return
            try:
                _cull_now(album, d)
            finally:
                lock.release()
        threading.Thread(target=finish, daemon=True, name="cull-" + album[:20]).start()
    return jsonify(res)


def _cull_now(album, d, budget=None):
    """budget: seconds of measuring before answering with what is known (quick=1); the rest is `pending`.
    A cold 1,100-photo album took 26 s before the first stack showed (stress test 2026-10-16)."""
    import json
    cache_p = CULL_DIR / (re.sub(r"\W", "_", album) + ".json")
    cache = load_json(cache_p, {})
    files = [p for p in sorted(d.iterdir(), key=lambda x: x.name.lower())
             if p.is_file() and p.suffix.lower() in IMG_EXTS and not p.name.startswith("_manifest") and not is_stray_thumb(p.name)]
    out, changed, pending, t0, last_write = {}, False, 0, time.time(), time.time()
    for p in files:
        key = f"{p.name}|{p.stat().st_mtime_ns}"
        ent = cache.get(p.name)
        if not ent or ent.get("k") != key:
            if budget is not None and time.time() - t0 > budget:
                pending += 1
                continue
            try:
                thumb = _build_bthumb(p, 400)
                ent = dict(cull_measure(thumb), k=key)
            except Exception:
                ent = {"k": key, "sharp": 0, "clip": 0, "sig": [0.0] * 64}
            cache[p.name] = ent
            changed = True
            if time.time() - last_write > 4:          # a long scan shows its progress to the quick polls
                try:
                    _atomic_write(cache_p, json.dumps(cache)); last_write = time.time()
                except OSError:
                    pass
        out[p.name] = {"sharp": ent["sharp"], "clip": ent["clip"]}
    if changed:
        try:
            _atomic_write(cache_p, json.dumps(cache))      # never a half-written cache for the next reader
        except OSError:
            pass
    # bursts: neighbours in capture order (file names sort by it) that look alike
    bursts, run = [], []
    names = [p.name for p in files if p.name in out]        # unmeasured (pending) photos are not in any burst yet
    for i, n in enumerate(names):
        if run:
            a, b = np.array(cache[run[-1]]["sig"]), np.array(cache[n]["sig"])
            sim = float((a * b).mean())
            if sim > 0.9:
                run.append(n)
                continue
            if len(run) > 1:
                bursts.append({"names": run, "best": max(run, key=lambda x: cache[x]["sharp"])})
        run = [n]
    if len(run) > 1:
        bursts.append({"names": run, "best": max(run, key=lambda x: cache[x]["sharp"])})
    sharps = sorted(v["sharp"] for v in out.values()) or [0]
    median = sharps[len(sharps) // 2]
    return {"photos": out, "bursts": bursts, "soft_below": round(median * 0.35, 4), "pending": pending}


# ---------------------------------------------------------------- insert results (2026-10-16)
# An insert bakes while Kevin keeps working, so the tab that asked for it may be closed, or showing another article,
# by the time the file is ready: the photo was archived and never placed. Each insert now carries an id (cid) and its
# outcome is kept here, so the editor places the photo when that article opens again. The first tab to claim a
# result places it; any other gets won=false and leaves it, so two tabs never place one photo twice.
IMPORT_RESULTS = ROOT / ".tmp" / "photo_editor" / "import_results.json"
_IR_LOCK = threading.Lock()
_CID_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")


def _ir_load():
    try:
        d = json.loads(IMPORT_RESULTS.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _ir_save(d):
    # two weeks, at most 300: an insert nobody came back for is not kept forever
    cutoff = time.time() - 14 * 86400
    d = {k: v for k, v in d.items() if isinstance(v, dict) and (v.get("at") or 0) >= cutoff}
    if len(d) > 300:
        for k in sorted(d, key=lambda k: d[k].get("at") or 0)[:len(d) - 300]:
            d.pop(k, None)
    _atomic_write(IMPORT_RESULTS, json.dumps(d))


def _ir_update(cid, **fields):
    with _IR_LOCK:
        d = _ir_load()
        rec = d.get(cid) if isinstance(d.get(cid), dict) else {"at": time.time()}
        rec.update(fields)
        d[cid] = rec
        try:
            _ir_save(d)
        except OSError:
            pass                                      # the insert itself must not fail over its bookkeeping
        return dict(rec)


def _ir_view(rec):
    """a record as the editor sees it; one left 'running' by a server that has since restarted never finishes"""
    if rec.get("state") == "running" and rec.get("pid") != os.getpid():
        rec = dict(rec, state="failed", error="the photo server restarted while it baked")
    return {k: rec.get(k) for k in ("state", "src", "w", "h", "orient", "error", "claimed", "at")}


@app.route("/api/import", methods=["POST", "OPTIONS"])
def api_import():
    """Copy a library photo into the Images/ archive WITHOUT touching any
    page: editor.html inserts the markup itself in its contenteditable and
    only needs the archived file + its repo-relative path. With a cid, the
    outcome is also kept for /api/import_result."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True) or {}
    cid = d.get("cid") if isinstance(d, dict) else None
    cid = cid if isinstance(cid, str) and _CID_RE.match(cid) else None
    if cid:
        with _IR_LOCK:
            prev = _ir_load().get(cid)
        if isinstance(prev, dict) and prev.get("state") == "done":
            # the same insert sent again (a reopened tab re-sending one that never arrived): it is already archived
            return jsonify({"ok": True, "src": prev["src"], "w": prev["w"], "h": prev["h"], "orient": prev["orient"]})
        _ir_update(cid, state="running", pid=os.getpid(), at=time.time())
    try:
        body, status = _import_run(d)
    except HTTPException as e:
        if cid:
            _ir_update(cid, state="failed", error=(e.description or e.name or "refused")[:300])
        raise
    except Exception as e:
        if cid:
            _ir_update(cid, state="failed", error=("%s: %s" % (type(e).__name__, e))[:300])
        raise
    if cid:
        if body.get("ok"):
            _ir_update(cid, state="done", src=body["src"], w=body["w"], h=body["h"], orient=body["orient"])
        else:
            _ir_update(cid, state="failed", error=body.get("error") or "import failed")
    return jsonify(body), status


@app.route("/api/import_result", methods=["GET", "POST", "OPTIONS"])
def api_import_result():
    """What became of an insert, by its cid: running, done (with the archived file), failed, or unknown (it
    never arrived). POST {cid, claim: <tab id>} takes the placement of a finished one for that tab: the first
    claim wins, and a later one from another tab answers won=false."""
    if request.method == "OPTIONS":
        return "", 204
    d = (request.get_json(force=True, silent=True) or {}) if request.method == "POST" else request.args
    cid = d.get("cid") if hasattr(d, "get") else None
    if not (isinstance(cid, str) and _CID_RE.match(cid)):
        abort(400)
    claim = d.get("claim") if request.method == "POST" else None
    claim = str(claim)[:40] if claim else None
    with _IR_LOCK:
        all_ = _ir_load()
        rec = all_.get(cid)
        if not isinstance(rec, dict):
            return jsonify({"state": "unknown", "won": False})
        out = _ir_view(rec)
        won = False
        if claim and out["state"] == "done":
            if not rec.get("claimed"):
                rec["claimed"] = claim
                rec["claimed_at"] = time.time()
                all_[cid] = rec
                try:
                    _ir_save(all_)
                except OSError:
                    pass
            won = rec["claimed"] == claim
            out["claimed"] = rec["claimed"]
    out["won"] = won
    return jsonify(out)


def _import_run(d):
    """the import itself: (json body, status)"""
    # An empty or wrong-shaped body answered 500, because d["root"] raised straight out of
    # the handler. The request is malformed, so say that instead.
    if not isinstance(d, dict) or d.get("root") in (None, "") or not d.get("path"):   # root 0 is the first source tab, not "missing"
        abort(400)
    try:
        src = source_path(d["root"], d["path"])
    except (ValueError, KeyError, OSError):
        abort(400)
    # the source must be an image BEFORE it is copied into the archive: a 0-byte, truncated or non-image
    # file used to land in Images/<Country>/ and then 500 (stress test 2026-10-15)
    try:
        with Image.open(src) as _im:
            _im.verify()
    except Exception:
        abort(400, "the source is not a readable image")
    # A file already IN the backup is the full-resolution original - that is the
    # whole point of the backup. Re-resolving it against the library wastes a
    # lookup and, worse, find_original() matches on capture SECOND, so a burst
    # frame can come back as a DIFFERENT shot. Publish the file as it stands.
    from_backup = BACKUP.resolve() in src.resolve().parents
    from_upload = UPLOADS.resolve() in src.resolve().parents     # a file added from the computer IS the original
    if d.get("full", True) and not from_backup and not from_upload:
        src = resolve_full_res(src)      # publish from the original, not a 2048px copy
        hydrate_if_cloud(src)            # pull it via the API, not by blind reading
    if d.get("token"):
        # erased pixels replace the library source; orientation is already baked
        erased_src = ERASED / (re.sub(r"\W", "", d["token"]) + ".jpg")
        if not erased_src.exists():
            return {"ok": False, "error": "erase session expired"}, 410
        dest = import_photo(erased_src, country_dir(d["country"]), d.get("city", "").strip(),
                            dev=d.get("dev"), masks=d.get("masks"), name_hint=src, recipe=recipe_for(d))
    else:
        dest = import_photo(src, country_dir(d["country"]), d.get("city", "").strip(),
                            rot=d.get("rot", 0), flip=d.get("flip"), dev=d.get("dev"),
                            angle=d.get("angle", 0), masks=d.get("masks"), recipe=recipe_for(d))
        if abs(float(d.get("angle", 0) or 0)) > 1e-3:
            straighten_record(dest, d["root"], d["path"], d.get("angle", 0))
    if from_upload:
        _recipe_point_at_archive(dest, d)
    with Image.open(dest) as im:
        im = ImageOps.exif_transpose(im)
        w, h = im.size
    return {"ok": True, "src": dest.relative_to(ROOT).as_posix(),
            "w": w, "h": h,
            "orient": "landscape" if w >= h * 1.05 else "portrait" if h >= w * 1.05 else "square"}, 200


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
                           capture_output=True, text=True, cwd=ROOT, timeout=1800, encoding="utf-8", errors="replace", creationflags=_NOWIN | _LOWPRI)
        tail = "\n".join((r.stdout or r.stderr or "").strip().splitlines()[-12:])
        log = ["$ draft_images.py\n" + tail]
        if r.returncode == 0:
            _fit_page(page, log)
        return r.returncode == 0, "\n\n".join(log)
    log = []
    # A published page is compressed on a STAGED copy in the same folder (so every ../Images path stays right)
    # and written back only if nobody saved the page meanwhile, the way draft_images.py already works for
    # drafts. The tools read the page, cut for minutes, then write their old copy back: a save made in that
    # window was lost, and the next merge read the loss as a deliberate undo (stress test 2026-10-23).
    p = ROOT / page
    stage = p.with_name("_live-" + p.name)
    try:
        src = p.read_bytes(); stamp = p.stat().st_mtime_ns
        stage.write_bytes(src)
        srel = stage.relative_to(ROOT).as_posix()
        for tool in PIPELINE:
            args = [sys.executable, str(ROOT / "tools" / tool)]
            if tool == "recompress_desktop.py":
                args.append("--new-only")            # only build missing web variants
            args += ["--page", srel]                 # this page alone: another article may be open in the editor
            r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=1800, creationflags=_NOWIN | _LOWPRI)
            tail = (r.stdout or r.stderr or "").strip().splitlines()[-3:]
            log.append(f"$ {tool}\n" + "\n".join(tail))
            if r.returncode != 0:
                return False, "\n\n".join(log)
        if p.stat().st_mtime_ns != stamp or p.read_bytes() != src:
            return False, "\n\n".join(log) + "\n(the page was saved during the run: nothing written back, it runs again)"
        out = stage.read_bytes()
        if out != src:
            p.write_bytes(out)
    finally:
        stage.unlink(missing_ok=True)
    _fit_page(page, log)
    return True, "\n\n".join(log)


def _fit_page(page, log):
    """the two fitters after the six tools (CLAUDE.md image steps 5-6 and the heroes rule): each body
    photo cut at the width this page draws it, and the hero's per-window files (a hero built in Covers
    had none until the page was fitted; QA 2026-10-02). Their failures are logged, not fatal."""
    for tool in ("fit_image_tiers.py", "fit_backgrounds.py"):
        try:
            r = subprocess.run([sys.executable, str(ROOT / "tools" / tool), page], capture_output=True, text=True,
                               cwd=ROOT, timeout=1800, encoding="utf-8", errors="replace", creationflags=_NOWIN | _LOWPRI)
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
# Every tool in a run works on the queued page alone (--page; they used to run SITE-WIDE, so nothing ran while
# ANY article was open, which meant not until the editor was closed). A page now waits only for itself, and for
# an open article that shows one of its photos, whose files the run re-cuts (2026-10-16). The queue is a file,
# so a server restart does not forget it.
OPEN_GRACE = 75
_cq_lock = threading.Lock()
_cq_open = {}                                   # rel -> {tab id: time of that tab's last heartbeat}
_cq_state = {"running": None, "last": None}
CQ_FILE = photo_suite.META / "compress_queue.json"


def _cq_load():
    try:
        return json.loads(CQ_FILE.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except ValueError:
        # a torn file is kept beside the live one, never silently read as "nothing queued" and overwritten
        # (stress test 2026-10-23: nine queued pages vanished that way)
        try:
            bad = CQ_FILE.with_name("compress_queue.corrupt-%d.json" % int(time.time()))
            if not bad.exists():
                shutil.copy2(CQ_FILE, bad)
                print("compress queue file unreadable; copy kept at %s" % bad.name, file=sys.stderr)
        except Exception:
            pass
        return {}


def _atomic_write(path, text):
    """write to a temp file beside it, then replace: a kill mid-write left compress_queue.json and the
    Recent list at 0 bytes, read back as empty (stress test 2026-10-09)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%d.%d.tmp" % (path.name, os.getpid(), threading.get_ident()))
    tmp.write_text(text, encoding="utf-8")
    for i in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:                        # a reader has it open for a moment
            if i == 5:
                raise
            time.sleep(0.1 * (i + 1))


def _cq_save(q):
    """True when the queue is on disk; a failure is the caller's to report (it used to answer ok)"""
    try:
        _atomic_write(CQ_FILE, json.dumps(q, indent=1))
        return True
    except OSError as e:
        print("compress queue not saved:", e, file=sys.stderr)
        return False


def _cq_is_open(rel, now):
    # open while ANY tab still reports it: one tab closing used to release a page a second tab
    # (the review tab, a test run) still had open, and its photos were re-cut under it (2026-10-03)
    return any(now - t < OPEN_GRACE for t in _cq_open.get(rel, {}).values())


def _cq_ready(rel, q, now):
    if _cq_is_open(rel, now):
        return False
    # the tools touch this page alone now (a draft's run once rewrote a live page under the open editor, stress
    # test 2026-10-16, when they ran site-wide); an open article that shows one of its photos still waits it out
    mine = _page_photos(rel)
    return not any(mine & _page_photos(o) for o in list(_cq_open) if o != rel and _cq_is_open(o, now))


_PHOTOS_CACHE = {}
_FURNITURE = {"flags", "icons", "og", "ui", "search", "continents", "Index", "Bio", "dest-cards", "city-maps", "itinerary-maps"}


def _page_photos(rel):
    """the photos a page shows, by what they are (<Country>/<path>/<Name>, lower case), whichever copy it names:
    the original, the desktop file, a tier or a mobile file"""
    f = ROOT / rel
    try:
        mt = f.stat().st_mtime_ns
    except OSError:
        return set()
    hit = _PHOTOS_CACHE.get(rel)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        txt = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    out = set()
    for u in re.findall(r'Images/[^"\',\s)]+\.(?:jpe?g|png|webp|heic)', txt, re.I):
        p = unquote(u)[len("Images/"):]
        p = p[len("web/"):] if p.startswith("web/") else p
        if "/" not in p or p.split("/", 1)[0] in _FURNITURE:
            continue                             # site furniture that many pages share (the nav's map, flags)
        out.add(re.sub(r"-(?:mob-)?[123]x$", "", p.rsplit(".", 1)[0]).lower())
    _PHOTOS_CACHE[rel] = (mt, out)
    return out


def _cq_tick():
    """One pass of the worker: compress the oldest page that is ready. Returns the page or None."""
    with _cq_lock:
        q, now = _cq_load(), time.time()
        # a page deleted or moved while queued used to sit there as "failed" forever (stress test 2026-10-15)
        gone = [r for r in q if not (ROOT / r).is_file()]
        for r in gone:
            q.pop(r, None); _cq_open.pop(r, None)
        if gone:
            _cq_save(q)
            _cq_state["last"] = {"page": gone[0], "ok": False, "at": time.strftime("%H:%M"), "log": "the page is gone; dropped from the queue"}
        rel = next((r for r in sorted(q, key=lambda r: q[r].get("queued", 0))
                    if _cq_ready(r, q, now) and q[r].get("tries", 0) < 3), None)
        if not rel:
            return None
        _cq_state["running"] = rel
        queued_at = q[rel].get("queued")
    try:
        ok, log = _run_pipeline(rel)
    except Exception as e:                       # a crash is a failed try, not a dead worker
        ok, log = False, "error: %s" % e
    with _cq_lock:
        q = _cq_load()
        if ok and q.get(rel, {}).get("queued") == queued_at:
            q.pop(rel, None)
        elif ok:
            # saved and queued again while this run went: that save still needs its photos cut
            q.setdefault(rel, {"queued": time.time()})["tries"] = 0     # the file may have been reset mid-run
            log += "\n(queued again during the run: it will run once more)"
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


# only as the server, which is the one process that knows which article is open: the iCloud ingest
# imports this module, and its copy of the worker compressed a page under the open editor (2026-10-03)
if __name__ == "__main__" and os.environ.get("PHOTO_EDITOR_NO_WORKER") != "1":
    threading.Thread(target=_cq_worker, daemon=True, name="compress-queue").start()
    threading.Thread(target=_cache_sweep, daemon=True, name="cache-sweep").start()


@app.route("/api/pipeline/queue", methods=["POST", "OPTIONS"])
def api_pipeline_queue():
    """The editor's save: note the page, compress later."""
    if request.method == "OPTIONS":
        return "", 204
    body = request.get_json(force=True) or {}
    # an article page in the repo, and not empty: the old check let an absolute path (ROOT / "C:/x"
    # is that path on Windows), styles.css or a 0-byte page into the queue (QA 2026-10-08)
    page = _prelaunch.safe_rel(body.get("page")) if isinstance(body, dict) else None
    if not page or (ROOT / page).stat().st_size == 0:
        abort(400)
    with _cq_lock:
        q = _cq_load()
        q[page] = {"queued": time.time(), "tries": 0}
        saved = _cq_save(q)
        _cq_open.setdefault(page, {})[str(body.get("tab") or "_")] = time.time()   # the save came from the open editor
    if not saved:                                     # it answered ok over a queue that never reached the disk (stress 2026-10-09)
        return jsonify({"ok": False, "error": "the compression queue could not be saved"}), 500
    return jsonify({"ok": True, "queued": sorted(q)})


@app.route("/api/editor/open", methods=["POST", "OPTIONS"])
def api_editor_open():
    """Heartbeat from the editor: which article is open. closing:true on switch or tab close.
    Sent with sendBeacon on the way out, as text/plain, so it is parsed with force=True."""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True, silent=True) or {}
    if not isinstance(d, dict):
        d = {}
    page, tab = d.get("page") or "", str(d.get("tab") or "_")[:80]
    if not isinstance(page, str):
        return jsonify({"ok": True, "compressing": False})
    canon = _prelaunch.safe_rel(page) if page else ""
    if page and not canon:
        if d.get("closing"):                                        # a page that is gone: let go of it anyway
            with _cq_lock:
                for k in [k for k in _cq_open if k.replace("\\", "/").lower() == page.replace("\\", "/").lower()]:
                    _cq_open.pop(k, None)
        return jsonify({"ok": True, "compressing": False})          # not an article: nothing to track
    page = canon
    with _cq_lock:
        # tabs that stopped beating long ago are gone (a closed laptop, a crashed tab); without this
        # the map only grew (QA 2026-10-08)
        now = time.time()
        for rel in list(_cq_open):
            live = {t: at for t, at in _cq_open[rel].items() if now - at < 3600}
            if live:
                _cq_open[rel] = live
            else:
                _cq_open.pop(rel, None)
        if d.get("closing"):
            _cq_open.get(page, {}).pop(tab, None)        # this tab only; another may still have it
            if not _cq_open.get(page):
                _cq_open.pop(page, None)
        elif page:
            _cq_open.setdefault(page, {})[tab] = time.time()
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
        try:
            _atomic_write(RECENT, json.dumps(keep, indent=1))
        except OSError:
            pass


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


def _xm_page(rel):
    page = (ROOT / rel).resolve()
    return page if ROOT.resolve() in page.parents and page.suffix == ".html" and page.is_file() else None


@app.route("/api/explore")
def api_explore():
    """the open article's Explore More slots: the three the rules (or Kevin's pins) chose, every page a slot can be
    switched to, and the block as it would be written (tools/explore_more.py; Kevin, 2026-10-23)"""
    import explore_more as xm
    rel = (request.args.get("rel") or "").replace("\\", "/").lstrip("./")
    if not _xm_page(rel):
        return jsonify({"ok": False, "error": "no such article"}), 404
    cat, pins = xm.catalog(), xm.load_pins()
    return jsonify({"ok": True, "rel": rel, "slots": xm.slot_info(rel, cat, pins), "pinned": (pins.get(rel) or [None, None, None])[:3],
                    "catalog": [{"rel": e["rel"], "title": e["title"], "kind": e["kind"], "label": xm.KIND_LABEL[e["kind"]], "country": e["name"], "draft": e["draft"]}
                                for e in cat.values() if e["rel"] != rel],
                    "block": xm.block_html(rel, xm.picks(rel, cat, pins))})


@app.route("/api/explore/set", methods=["POST", "OPTIONS"])
def api_explore_set():
    """pin (or unpin) the three slots of a page; the pins live in tools/explore_pins.json and the page itself is
    redrawn by the editor, which saves it the normal way"""
    if request.method == "OPTIONS":
        return "", 204
    import explore_more as xm
    d = request.get_json(force=True) or {}
    rel = (d.get("rel") or "").replace("\\", "/").lstrip("./")
    if not _xm_page(rel):
        return jsonify({"ok": False, "error": "no such article"}), 404
    cat = xm.catalog()
    slots = [t if isinstance(t, str) and t in cat and t != rel else None for t in (list(d.get("slots") or []) + [None] * 3)[:3]]
    pins = xm.set_pins(rel, slots)
    return jsonify({"ok": True, "rel": rel, "slots": xm.slot_info(rel, cat, pins), "pinned": (pins.get(rel) or [None, None, None])[:3],
                    "block": xm.block_html(rel, xm.picks(rel, cat, pins))})


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
    print(f"Photo editor: http://127.0.0.1:{int(os.environ.get('PHOTO_EDITOR_PORT', 5003))}   sources: {[s['label'] for s in SOURCES]}")
    # threaded: thumbnail generation must not block imports/saves behind it
    # an idle connection is closed after 2 minutes: 500 idle sockets pinned 504 threads and 2,000 handles
    # until the client let go (stress test 2026-10-09)
    from werkzeug.serving import WSGIRequestHandler
    WSGIRequestHandler.timeout = 120
    _sweep_stale_temps()
    app.run(port=int(os.environ.get("PHOTO_EDITOR_PORT", 5003)), debug=False, threaded=True)   # a test instance runs beside the live one
