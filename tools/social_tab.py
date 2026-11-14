"""The editor's Social tab (Kevin, 2026-11-08: "add integrating the new social tool to the editor as a 'social' tab
between launch and queue").

The IG post maker (tools/ig_post_page.html: the free 10-photo mode and the itinerary template mode) runs INSIDE the
editor, per country, against a local store, so Kevin needs no claude.ai artifact to shortlist photos, lay out slides,
crop and write captions; the renders (tools/ig_carousel.py, tools/ig_post.py) run here on the photo server and the
tab shows the finished slides and pins. The three pilot artifacts keep working untouched: their state lives in each
artifact's own db, this tab's state lives on disk, and an ArtifactData export can be imported into it.

Store (override the roots with SOCIAL_IG_DIR and SOCIAL_OUT_DIR for tests):

  .tmp/ig/<slug>/manifest.json         the photos (tools/ig_post.py "<Country>" wrote it; the tab lists the slugs that have one)
  .tmp/ig/<slug>/web/<id>.jpg          the 1280 px copies the page shows (served at /api/social/<slug>/blob/<id>)
  .tmp/ig/<slug>/state/<coll>/<doc>.json   the page's documents, one file each, PLAIN bodies: photos/<id>, post/main,
                                       captions/<id>, carousel/main, carousel/map, catalog/main. This is exactly the
                                       shape ArtifactData's out_dir leaves, so `ig_post.py render --state` and
                                       `ig_carousel.py render --state` read it as they are, and an artifact export
                                       drops straight in (POST .../import).
  .tmp/previews/social-pilot/<slug>/   the Travel Blog post's carousel slides (NN-<slide>.png) + caption.txt
  .tmp/previews/social-pilot/<slug>/stories/   its story frames, 1080x1920 (tools/ig_story.py --kind carousel)
  .tmp/previews/social-pilot/pins/     the pins (<slug>-<article>.png)
  .tmp/ig/<slug>/out/                  the Personal post's render (ig_post.py also copies it to Pictures\\IG\\<slug>)
  .tmp/ig/<slug>/stories/              its story frames (ig_story.py --kind free; copied to Pictures\\IG\\<slug>\\stories)

The tab (social.js) offers TWO output types (Kevin, 2026-11-08): the Personal post (the page's free mode: any 10
photos) and the Travel Blog post (the itinerary template), each with its own state, renders, stories and caption.

Routes (CORS open like the rest of the photo server):

  GET  /api/social                          -> {ok, countries: [{slug, country, photos, pages, artifact, has_plan, has_seed, renders, updated}]}
  GET  /social/<slug>/page[?embed=1]        -> tools/ig_post_page.html with the local runtime shim injected (window.claude.use);
                                               embed=1 sets window.SOCIAL_EMBED so the page drops its header and takes the
                                               editor's look (html.embed), driven by the tab over postMessage
  GET  /api/social/<slug>/db                -> {ok, version, docs: {"<coll>/<doc>": body}}  (catalog/main built from the manifest
                                               when there is none; the seed's carousel/main + map copied in on first read)
  POST /api/social/<slug>/db  {op: set|delete, path, data}   -> {ok, version}
  GET  /api/social/<slug>/blob/<id>         -> the web copy
  GET  /api/social/<slug>/renders           -> {ok, carousel: [{name, url, mtime}], caption, free: [...], free_caption, pins: [...],
                                               stories_carousel: [...], stories_free: [...]}
  POST /api/social/<slug>/render {kind: carousel|free|pins|stories-carousel|stories-free}  -> {ok, job}   one job per slug at a time
  GET  /api/social/job/<id>                 -> {ok, job: {id, slug, kind, status, log, started, ended, error}}
  POST /api/social/<slug>/seed              -> runs ig_carousel.py seed, copies the plan + map into the store
  POST /api/social/<slug>/import {dir}      -> copies an ArtifactData out_dir (under .tmp) into the store; {ok, copied}
                                               (the tab's Import button went 2026-11-11; Claude calls this route directly)
  GET  /api/social/<slug>/library?album=    -> {ok, names}: that album's photos already in this country
  GET  /api/social/<slug>/post              -> the Post view: {handle, avatar, country, aspect, caption, hashtags, slides: [{id, aspect,
                                               crop, w, h, blob}], render: {state none|ok|stale|rendering, reason, files, total_bytes,
                                               emails}, email: the last request's status, direct: Gmail keys in .env}
  POST /api/social/<slug>/email             -> Email to my phone. With GMAIL_SMTP_USER + GMAIL_APP_PASSWORD (+ IG_EMAIL_TO) in .env:
                                               sends now by Gmail SMTP (smtp.gmail.com:465) as a job {ok, email, job}. Without
                                               them: writes .tmp/ig/<slug>/email_request.json for Claude {ok, email, job: null}.
                                               400 not rendered, 409 stale / rendering / sending. GET -> {ok, email}
  CLI (Claude):  --email-requests  (pending requests as JSON: per email part its subject, body and attachments as
                 "sha256:<hex>:<absolute path>" + filename)  |  --email-sent <slug> <id> <message ids...>  |
                 --email-failed <slug> <id> <reason>
  POST /api/social/<slug>/add {album, names} -> photos from the photo library (~/Backup/<album>, SOCIAL_BACKUP_DIR for tests):
                                               1280 px sRGB web copies + manifest entries; {ok, added, skipped, version}

2026-11-11 redesign (Kevin: "remove the Shortlist, the slots are the shortlist"): a photo's state is pool, archived or
final; slots 1..post/main.nfinal (10, up to Instagram's 20) are the post and every later slot is the Shortlist. Second
round, same day ("no blanks in between photos, only at the end"; "drag ... pushes everything down one instead of
swapping"): each row is packed from its first slot and a drop inserts. A store still holding the old 'shortlist' state,
gaps between photos or two photos in one slot is migrated on the page's first read (migrate_store; never on a poll), after
a copy of the state folder to .tmp/ig/<slug>/state.bak-<time>. post/main.nshort = how many Shortlist slots to draw.

Standalone (tests):  python tools/social_tab.py --serve 5041
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_from_directory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queue_tab import COUNTRIES, FLAGS_DIR  # noqa: E402  (the nav's local flags, by country folder)

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PAGE = TOOLS / "ig_post_page.html"
SEEDS = TOOLS / "ig_carousel"
bp = Blueprint("social_tab", __name__)
_lock = threading.RLock()
_jobs = {}
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# the claude.ai artifacts (their state is theirs; the tab never writes them)
ARTIFACTS = {"armenia": "https://claude.ai/artifact/B7ADJ4a5TSZK8LDq22291X",
             "el-salvador": "https://claude.ai/artifact/AQWGsvG4arZicHckc9twnP",
             "north-macedonia": "https://claude.ai/artifact/EwKsi6Cc1QD6shophWumx7"}
COLLECTIONS = ("catalog", "photos", "post", "captions", "carousel")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
DOC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$")


def ig_dir():
    return Path(os.environ.get("SOCIAL_IG_DIR") or (ROOT / ".tmp" / "ig"))


def out_dir():
    return Path(os.environ.get("SOCIAL_OUT_DIR") or (ROOT / ".tmp" / "previews" / "social-pilot"))


def state_dir(slug):
    return ig_dir() / slug / "state"


def _rel(p: Path):
    """a path under the repo as the photo server's /site/<rel> wants it, else None"""
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return None


def _slug(s):
    s = str(s or "").strip().lower()
    return s if SLUG_RE.match(s) else None


def manifest(slug):
    p = ig_dir() / slug / "manifest.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ------------------------------------------------------------------------------------- the store
def _body(obj):
    """the document body from a plain file or an ArtifactData export ({data, version})"""
    if isinstance(obj, dict) and isinstance(obj.get("data"), dict) and ("version" in obj or "id" in obj):
        return obj["data"]
    return obj


def read_store(slug):
    d = state_dir(slug)
    docs = {}
    for coll in COLLECTIONS:
        cd = d / coll
        if not cd.is_dir():
            continue
        for f in sorted(cd.glob("*.json")):
            try:
                docs[f"{coll}/{f.stem}"] = _body(json.loads(f.read_text(encoding="utf-8-sig")))
            except Exception:
                continue
    return docs


def write_doc(slug, path, data):
    coll, _, doc = path.partition("/")
    if coll not in COLLECTIONS or not DOC_RE.match(doc or ""):
        raise ValueError("bad document path %r" % path)
    f = state_dir(slug) / coll / (doc + ".json")
    if data is None:
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        return
    if not isinstance(data, dict):
        raise ValueError("a document is an object")
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for _ in range(40):
        try:
            os.replace(tmp, f)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, f)


def version_of(slug):
    """changes whenever any document changes (names + mtimes + sizes)"""
    d = state_dir(slug)
    h = hashlib.sha1()
    if d.is_dir():
        for f in sorted(d.rglob("*.json")):
            st = f.stat()
            h.update(("%s %d %d\n" % (f.relative_to(d).as_posix(), st.st_mtime_ns, st.st_size)).encode())
    return h.hexdigest()[:16]


def catalog_from_manifest(man, slug):
    """the catalog/main doc the page wants, with the photo id as its asset (the blob route resolves it)"""
    items = [{k: p.get(k) for k in ("id", "article", "article_title", "section", "caption", "alt", "time", "w", "h")} | {"asset": p["id"]}
             for p in man.get("photos") or []]
    return {"country": man.get("country"), "slug": slug, "pages": man.get("pages"), "items": items, "source": "manifest"}


NFINAL_DEFAULT, NFINAL_MAX = 10, 20          # Instagram takes up to 20 photos in one post (Meta's content-publishing docs)


def nfinal_of(post):
    """how many slots the Final row has: post/main.nfinal, 10 by default, never more than Instagram's 20"""
    try:
        n = int((post or {}).get("nfinal") or NFINAL_DEFAULT)
    except (TypeError, ValueError):
        n = NFINAL_DEFAULT
    return max(1, min(NFINAL_MAX, n))


SLOTCAP = 2000                                # the page's own bound on a slot number


def _num(v):
    """a slot or order that is not a number counts as none"""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def packed_rows(photos, n_final):
    """the two rows in order, exactly as the page's rows() reads them: every 'final' photo sorted by (slot, id), a slot
    that is missing or out of range sorting last; the Final row is the ones on slots 1..n_final (at most n_final of
    them, any more head the Shortlist), the Shortlist the rest"""
    key = lambda pid: (lambda s: s if 1 <= s <= SLOTCAP else SLOTCAP + 1)(_num(photos[pid].get("slot")))
    finals = sorted((pid for pid, d in photos.items() if d.get("state") == "final"), key=lambda pid: (key(pid), pid))
    fin = [p for p in finals if key(p) <= n_final]
    sho = [p for p in finals if key(p) > n_final]
    if len(fin) > n_final:
        fin, sho = fin[:n_final], fin[n_final:] + sho
    return fin, sho


def migrate_store(slug, docs):
    """Kevin, 2026-11-11: "remove the Shortlist: the slots are the shortlist", then "no blanks in between photos, only
    at the end". The slots are PACKED: the Final row's photos on slots 1..k, the Shortlist's on n_final+1..n_final+j,
    empty slots only after a row's last photo. A store with gaps (or two photos on one slot) is packed in its own
    order; a photo still in the old 'shortlist' state goes to the end of the Shortlist (by its order); nothing he
    picked is lost. The state folder is copied to state.bak-<time> first, once per migration. Returns True when it
    wrote anything."""
    photos = {k.split("/", 1)[1]: v for k, v in docs.items() if k.startswith("photos/") and isinstance(v, dict)}
    n_final = nfinal_of(docs.get("post/main"))
    fin, sho = packed_rows(photos, n_final)
    olds = sorted((pid for pid, d in photos.items() if d.get("state") == "shortlist"),
                  key=lambda pid: (_num(photos[pid].get("order")), pid))
    want = {pid: i + 1 for i, pid in enumerate(fin)}
    want.update({pid: n_final + 1 + j for j, pid in enumerate(sho + olds)})
    moves = [pid for pid, s in want.items() if photos[pid].get("state") != "final" or _num(photos[pid].get("slot")) != s]
    if not moves:
        return False
    bak = state_dir(slug).parent / ("state.bak-" + time.strftime("%Y%m%d-%H%M%S"))
    if not bak.exists():
        import shutil
        shutil.copytree(state_dir(slug), bak)
    for pid in moves:
        d = dict(photos[pid])
        d.update(state="final", slot=want[pid], order=0)
        write_doc(slug, "photos/" + pid, d)
    return True


def ensure_store(slug, man, migrate=True):
    """first read of a country: a catalog from the manifest, and the seed's plan + map (ig_carousel.py seed wrote
    .tmp/ig/<slug>/carousel/main.json + map.json) copied in, so the template mode opens on the current plan; then the
    slots packed and the old shortlist migrated into them (migrate_store). migrate=False for the page's polls: a poll
    can land between the documents of one move the page is writing, and must not "repair" that half-written order."""
    docs = read_store(slug)
    changed = migrate_store(slug, docs) if migrate else False
    if "catalog/main" not in docs or (docs["catalog/main"].get("source") == "manifest" and len(docs["catalog/main"].get("items") or []) != len(man.get("photos") or [])):
        write_doc(slug, "catalog/main", catalog_from_manifest(man, slug))
        changed = True
    seed_out = ig_dir() / slug / "carousel"
    for name in ("main", "map"):
        if f"carousel/{name}" not in docs and (seed_out / f"{name}.json").is_file():
            try:
                write_doc(slug, f"carousel/{name}", _body(json.loads((seed_out / f"{name}.json").read_text(encoding="utf-8-sig"))))
                changed = True
            except Exception:
                pass
    return read_store(slug) if changed else docs


# ------------------------------------------------------------------------------------- renders
def _files(folder: Path, pattern, slug, kind):
    """[{name, url, path, mtime}]; the url is this blueprint's own file route, so an out folder outside the repo works too"""
    out = []
    if folder.is_dir():
        for f in sorted(folder.glob(pattern)):
            out.append({"name": f.name, "url": "/api/social/%s/file/%s/%s" % (slug, kind, _q(f.name)), "path": str(f), "mtime": f.stat().st_mtime})
    return out


def render_folder(slug, kind):
    return {"carousel": out_dir() / slug, "free": ig_dir() / slug / "out", "pins": out_dir() / "pins",
            "stories-carousel": out_dir() / slug / "stories", "stories-free": ig_dir() / slug / "stories"}.get(kind)


def _q(s):
    from urllib.parse import quote
    return quote(s, safe="")


def renders(slug):
    car = out_dir() / slug
    free = ig_dir() / slug / "out"
    cap = (car / "caption.txt")
    fcap = (free / "caption.txt")
    return {"carousel": _files(car, "*.png", slug, "carousel"), "caption": cap.read_text(encoding="utf-8") if cap.is_file() else "",
            "free": _files(free, "*.jpg", slug, "free"), "free_caption": fcap.read_text(encoding="utf-8") if fcap.is_file() else "",
            "free_notes": (free / "notes.txt").read_text(encoding="utf-8") if (free / "notes.txt").is_file() else "",
            "pins": _files(out_dir() / "pins", slug + "-*.png", slug, "pins"),
            "stories_carousel": _files(render_folder(slug, "stories-carousel"), "*.png", slug, "stories-carousel"),
            "stories_free": _files(render_folder(slug, "stories-free"), "*.png", slug, "stories-free")}


def busy():
    """photo_editor's /api/build busy list: a render under way must not be cut by a restart"""
    with _lock:
        return ["social %s: %s %s" % ("email" if j["kind"] == "email" else "render", j["slug"], j["kind"]) for j in _jobs.values() if j["status"] in ("queued", "running")]


def _running(slug, kind=None):
    """a job for this country under way (kind 'render' = any render, 'email' = a send; None = either: a render must not
    rewrite the files an email is attaching, and an email must not take files a render is writing)"""
    return any(j["slug"] == slug and j["status"] in ("queued", "running") and (kind is None or (j["kind"] == "email") == (kind == "email"))
               for j in list(_jobs.values()))


def start_render(slug, kind, country):
    """ig_carousel.py render / pins, ig_post.py render or ig_story.py render as a child process, its output kept in the job"""
    # named here (never run in this process) so tools/build_id.py counts the renderers in the photo server's fingerprint:
    # a change to one of them restarts the server at the next shortcut click like any other code change
    import ig_carousel  # noqa: F401
    import ig_post  # noqa: F401
    import ig_story  # noqa: F401
    st = state_dir(slug)
    if kind == "carousel":
        dest = out_dir() / slug
        cmd = [sys.executable, str(TOOLS / "ig_carousel.py"), country, "render", "--state", str(st), "--out", str(dest)]
    elif kind == "pins":
        cmd = [sys.executable, str(TOOLS / "ig_carousel.py"), country, "pins", "--out", str(out_dir() / "pins")]
    elif kind == "free":
        cmd = [sys.executable, str(TOOLS / "ig_post.py"), country, "render", "--state", str(st), "--force", "--out", str(render_folder(slug, "free"))]
        if os.environ.get("SOCIAL_IG_DIR"):
            cmd.append("--no-copy")                     # a test run never writes Pictures\IG
    elif kind in ("stories-carousel", "stories-free"):
        k = kind.split("-")[1]
        cmd = [sys.executable, str(TOOLS / "ig_story.py"), country, "render", "--kind", k, "--state", str(st), "--out", str(render_folder(slug, kind))]
        if k == "free" and os.environ.get("SOCIAL_IG_DIR"):
            cmd.append("--no-copy")
    else:
        raise ValueError("kind must be carousel, free, pins, stories-carousel or stories-free")
    jid = "s%s" % uuid.uuid4().hex[:8]
    job = {"id": jid, "slug": slug, "kind": kind, "status": "queued", "log": "", "started": time.time(), "ended": None, "error": None, "cmd": cmd}
    with _lock:
        _jobs[jid] = job
        done = sorted((j for j in _jobs.values() if j["status"] in ("done", "failed")), key=lambda j: j["ended"] or 0)
        for j in done[:max(0, len(_jobs) - 30)]:
            _jobs.pop(j["id"], None)

    def run():
        job["status"] = "running"
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, creationflags=NO_WINDOW)
            for line in p.stdout:
                job["log"] = (job["log"] + line.decode("utf-8", "replace"))[-6000:]
            p.wait()
            if p.returncode:
                job["status"] = "failed"
                job["error"] = "exit %d" % p.returncode
            else:
                job["status"] = "done"
        except Exception as e:
            job["status"] = "failed"
            job["error"] = str(e)[:300]
        job["ended"] = time.time()
    threading.Thread(target=run, daemon=True, name="social-" + jid).start()
    return job


def job_payload(j):
    return {k: v for k, v in j.items() if k != "cmd"}


# ------------------------------------------------------------------------------------- the Post view: Instagram preview + email
# Kevin, 2026-11-11: "can the post tab show how it would look on IG?" and "add a button to email to my phone ... at full
# quality and easy to download and post with caption". The preview is drawn by social.js from GET .../post (the Final
# row with its crops, the caption + hashtags as they stand now, and the render with a stale flag). The email is a
# REQUEST (Kevin: "you send me emails already, can't we use that mechanism?"): the button writes
# .tmp/ig/<slug>/email_request.json and Claude's session, which has the Gmail connector, sends it with the render files
# attached by reference, then marks it sent (`--email-sent`). No mail server, no password, no address anywhere here:
# Claude sends to Kevin's own address at send time.
POST_ASPECTS = ("4:5", "3:4", "1:1", "1.91:1")
# Gmail's 25 MB cap counts the message as sent, and base64 makes an attachment 4/3 its size: 18 MiB of JPEGs is ~24 MiB
# on the wire, so each email carries at most this much (a 1080 px q92 slide is ~0.4 to 1.2 MB: 20 slides usually fit one)
EMAIL_PART_BYTES = 18 * 1024 * 1024


def ig_handle():
    """the account the preview shows: the site's own Instagram link (index.html), else getawayguide"""
    global _HANDLE
    try:
        return _HANDLE
    except NameError:
        pass
    _HANDLE = "getawayguide"
    try:
        m = re.search(r"instagram\.com/([A-Za-z0-9_.]{1,30})", (ROOT / "index.html").read_text(encoding="utf-8", errors="replace"))
        if m:
            _HANDLE = m.group(1)
    except OSError:
        pass
    return _HANDLE


def post_state(slug, man):
    """the Personal post as it stands now: the Final row in order with each slide's ratio and crop (what ig_post.py
    render would cut), the caption and hashtags (ig_post's own rule: post/main.finalCaption, else the caption marked
    final). Reads the store as it is (no migration)."""
    docs = read_store(slug)
    post = docs.get("post/main") or {}
    photos = {k.split("/", 1)[1]: v for k, v in docs.items() if k.startswith("photos/") and isinstance(v, dict)}
    fin, _ = packed_rows(photos, nfinal_of(post))
    aspect = post.get("aspect") if post.get("aspect") in POST_ASPECTS else "4:5"
    by_id = {p["id"]: p for p in man.get("photos") or []}
    slides = []
    for pid in fin:
        d, p = photos[pid], by_id.get(pid)
        if not p:                                           # ig_post.py skips a photo the manifest lost: so does the preview
            continue
        crop = d.get("crop") or {}
        slides.append({"n": len(slides) + 1, "id": pid, "aspect": crop.get("aspect") if crop.get("aspect") in POST_ASPECTS else aspect,
                       "crop": crop, "w": p.get("w"), "h": p.get("h"), "blob": "/api/social/%s/blob/%s" % (slug, _q(pid))})
    cap = (post.get("finalCaption") or "").strip()
    if not cap:
        fc = [v for k, v in docs.items() if k.startswith("captions/") and isinstance(v, dict) and v.get("final")]
        cap = (fc[0].get("text") or "").strip() if fc else ""
    return {"aspect": aspect, "nfinal": nfinal_of(post), "caption": cap, "hashtags": (post.get("hashtags") or "").strip(), "slides": slides}


def free_render(slug, slides):
    """the Personal post's render and whether it still matches the Final row: state none | ok | stale | rendering, with
    a plain reason when stale. render.json (ig_post.py writes it) holds each slide's photo, ratio and crop; a render made
    before it existed is compared on its file names (the photos and their order) only."""
    folder = render_folder(slug, "free")
    files = sorted(folder.glob("*.jpg")) if folder.is_dir() else []
    out = {"files": [{"name": f.name, "url": "/api/social/%s/file/free/%s" % (slug, _q(f.name)), "bytes": f.stat().st_size, "mtime": f.stat().st_mtime}
                     for f in files], "rendered": None, "reason": ""}
    out["total_bytes"] = sum(f["bytes"] for f in out["files"])
    out["emails"] = len(split_parts([f["bytes"] for f in out["files"]]))       # how many emails it would take
    if _running(slug, "render"):
        return dict(out, state="rendering", reason="a render is running")
    if not files:
        return dict(out, state="none", reason="not rendered yet")
    rec = None
    try:
        rec = json.loads((folder / "render.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    names = [f.name for f in files]
    now_ids = [s["id"] for s in slides]
    if rec and isinstance(rec.get("slides"), list):
        out["rendered"] = rec.get("rendered")
        was = rec["slides"]
        if [x.get("file") for x in was] != names:
            return dict(out, state="stale", reason="the render folder no longer matches its record: render again")
        was_ids = [x.get("id") for x in was]
    else:
        was = None
        was_ids = [re.sub(r"^\d+-", "", n[:-4]) for n in names]
    if not now_ids:
        return dict(out, state="stale", reason="the Final row is empty now")
    if was_ids != now_ids:
        if sorted(was_ids) == sorted(now_ids):
            why = "the slides were reordered after the render"
        else:
            why = "the Final row has other photos now (%d, the render has %d)" % (len(now_ids), len(was_ids)) if len(now_ids) != len(was_ids) \
                else "a photo in the Final row was swapped after the render"
        return dict(out, state="stale", reason=why)
    if was:
        for i, (x, s) in enumerate(zip(was, slides)):
            if x.get("aspect") != s["aspect"]:
                return dict(out, state="stale", reason="the ratio is %s now, the render is %s" % (s["aspect"], x.get("aspect")))
            if json.dumps(x.get("crop") or {}, sort_keys=True) != json.dumps(s["crop"] or {}, sort_keys=True):
                return dict(out, state="stale", reason="slide %d's crop changed after the render" % (i + 1))
    return dict(out, state="ok")


def email_file(slug):
    return ig_dir() / slug / "email_request.json"


def read_email(slug):
    try:
        r = json.loads(email_file(slug).read_text(encoding="utf-8"))
        return r if isinstance(r, dict) else None
    except (OSError, ValueError):
        return None


def write_email(slug, req):
    f = email_file(slug)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".%d.%s.tmp" % (os.getpid(), uuid.uuid4().hex[:6]))
    tmp.write_text(json.dumps(req, ensure_ascii=False, indent=1), encoding="utf-8")
    for _ in range(40):
        try:
            os.replace(tmp, f)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, f)


def email_summary(req):
    """what the page needs (no paths)"""
    if not req:
        return None
    s = {k: req.get(k) for k in ("id", "status", "requested", "sent_at", "failed_at", "error", "subject", "slides", "total_bytes", "via")} | {
        "emails": len(req.get("parts") or [])}
    if s["status"] == "sending" and not _running(req.get("slug") or "", "email"):
        s.update(status="failed", error="the send was cut off (the photo server restarted): press Email again")
    return s


def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def split_parts(sizes, limit=None):
    """consecutive runs of file indexes, each run's total under the limit (order kept; a file over the limit goes alone).
    SOCIAL_EMAIL_PART_BYTES lowers the limit for a test, so a few small files split too."""
    limit = limit or int(os.environ.get("SOCIAL_EMAIL_PART_BYTES") or EMAIL_PART_BYTES)
    parts, cur, tot = [], [], 0
    for i, n in enumerate(sizes):
        if cur and tot + n > limit:
            parts.append(cur)
            cur, tot = [], 0
        cur.append(i)
        tot += n
    if cur:
        parts.append(cur)
    return parts


def email_texts(country, caption, tags, n, aspect, parts):
    """[(subject, body)] per email: the caption + hashtags at the very top of the first (a long-press copies them),
    then how to post; a later part says which photos it holds"""
    subject = "IG post: %s (%d slide%s)" % (country, n, "" if n == 1 else "s")
    k = len(parts)
    top = (caption or "(No caption marked final yet: write it in the Caption tab.)") + ("\n\n" + tags if tags else "")
    how = ["----", "How to post (%d slide%s, %s):" % (n, "" if n == 1 else "s", aspect)]
    if k > 1:
        how.append("This post comes in %d emails. Save the photos from all of them, in order, before you start." % k)
    how += ["1. Save the photos in order, 01.jpg first: tap each attachment, then Share, then Save Image.",
            "2. Open Instagram, tap +, then Post.",
            "3. Tap Select (the stacked squares) and pick the photos in order, 01 to %02d. They are the newest in Recents." % n]
    if aspect != "1:1":
        how.append("4. If Instagram squares them, tap the crop button at the bottom left of the preview so they stay %s." % aspect)
    how.append("%d. Back in this email, long-press the caption at the top, Copy, and paste it into the caption box."
               % (1 + sum(1 for x in how if x[:1].isdigit())))
    first = top + "\n\n" + "\n".join(how) + "\n"
    out = []
    for i, idx in enumerate(parts):
        subj = subject if k == 1 else "%s %d of %d" % (subject, i + 1, k)
        if i == 0:
            body = first
        else:
            which = ("Photos %02d to %02d, in order." % (idx[0] + 1, idx[-1] + 1)) if len(idx) > 1 else ("Photo %02d." % (idx[0] + 1))
            body = "Part %d of %d of your IG post: %s. %s\nThe caption and how to post are in part 1.\n" % (i + 1, k, country, which)
        out.append((subj, body))
    return out


class EmailRefused(Exception):
    def __init__(self, msg, status=400, code=""):
        super().__init__(msg)
        self.status, self.code = status, code


def build_email_request(slug, man):
    """the request for the render as it is, or EmailRefused: not rendered, rendering, or stale. The files are the
    render's own JPEGs, untouched: their absolute paths, sizes and sha256, named 01.jpg ... in slide order."""
    ps = post_state(slug, man)
    rs = free_render(slug, ps["slides"])
    if rs["state"] == "none":
        raise EmailRefused("Not rendered yet: press Render first, then email it.", 400, "none")
    if rs["state"] == "rendering":
        raise EmailRefused("A render is running: email it when it finishes.", 409, "rendering")
    if rs["state"] == "stale":
        raise EmailRefused("The render is out of date (%s). Render again, then email it." % rs["reason"], 409, "stale")
    folder = render_folder(slug, "free")
    files = []
    for i, f in enumerate(rs["files"]):
        p = (folder / f["name"]).resolve()
        files.append({"path": str(p).replace("\\", "/"), "filename": "%02d.jpg" % (i + 1), "bytes": p.stat().st_size, "sha256": _sha256(p),
                      "source": f["name"]})
    parts = split_parts([x["bytes"] for x in files])
    country = man.get("country") or slug
    aspect = ps["slides"][0]["aspect"] if ps["slides"] else ps["aspect"]
    texts = email_texts(country, ps["caption"], ps["hashtags"], len(files), aspect, parts)
    return {"id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6], "slug": slug, "country": country,
            "requested": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": "requested",
            "subject": "IG post: %s (%d slide%s)" % (country, len(files), "" if len(files) == 1 else "s"), "body": texts[0][1],
            "files": files, "parts": parts, "slides": len(files), "total_bytes": sum(x["bytes"] for x in files),
            "messages": [{"part": i + 1, "of": len(parts), "subject": s, "body": b, "files": parts[i]} for i, (s, b) in enumerate(texts)],
            "rendered": rs.get("rendered")}


def _env_values(names):
    """keys from the environment, else the repo's .env (SOCIAL_ENV_FILE for tests), read at SEND time so a key Kevin
    adds works without a restart. The values never leave this process: not logged, not returned."""
    vals = {n: os.environ.get(n, "") for n in names}
    f = Path(os.environ.get("SOCIAL_ENV_FILE") or (ROOT / ".env"))
    if f.is_file() and not all(vals.values()):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k.strip() in vals and not vals[k.strip()]:
                vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def smtp_config():
    """Gmail SMTP from .env (GMAIL_SMTP_USER + GMAIL_APP_PASSWORD; IG_EMAIL_TO, else the sender), or None when the keys
    are missing (then the button writes a request for Claude instead). SOCIAL_SMTP_HOST / _PORT / _SSL point a test at a
    local sink; a test store (SOCIAL_IG_DIR) never reaches smtp.gmail.com."""
    v = _env_values(("GMAIL_SMTP_USER", "GMAIL_APP_PASSWORD", "IG_EMAIL_TO"))
    if not (v["GMAIL_SMTP_USER"] and v["GMAIL_APP_PASSWORD"]):
        return None
    host = os.environ.get("SOCIAL_SMTP_HOST") or "smtp.gmail.com"
    if host == "smtp.gmail.com" and os.environ.get("SOCIAL_IG_DIR"):
        return None
    return {"host": host, "port": int(os.environ.get("SOCIAL_SMTP_PORT") or 465), "ssl": os.environ.get("SOCIAL_SMTP_SSL", "1") != "0",
            "user": v["GMAIL_SMTP_USER"], "password": v["GMAIL_APP_PASSWORD"], "to": v["IG_EMAIL_TO"] or v["GMAIL_SMTP_USER"]}


def _smtp_send(cfg, req, job):
    """one email per part over one SMTP session: the render's JPEG bytes attached as they are (checked against the
    request's sha256 first, never re-encoded), plain-text body. Returns the Message-IDs."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import make_msgid
    files = req["files"]
    blobs = []
    for x in files:
        data = Path(x["path"]).read_bytes()
        if len(data) != x["bytes"] or hashlib.sha256(data).hexdigest() != x["sha256"]:
            raise RuntimeError("%s changed since the request (re-rendered?): press Email again" % x["filename"])
        blobs.append(data)
    ids = []
    domain = cfg["user"].rpartition("@")[2] or "localhost"
    conn = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=120, context=ssl.create_default_context()) if cfg["ssl"] else smtplib.SMTP(cfg["host"], cfg["port"], timeout=120)
    try:
        conn.login(cfg["user"], cfg["password"])
        for m in req["messages"]:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = cfg["user"], cfg["to"], m["subject"]
            msg["Message-ID"] = make_msgid(domain=domain)
            msg.set_content(m["body"])
            for i in m["files"]:
                msg.add_attachment(blobs[i], maintype="image", subtype="jpeg", filename=files[i]["filename"])
            job["log"] += "Email %d of %d: %d photo%s, %.1f MB ... " % (m["part"], m["of"], len(m["files"]), "" if len(m["files"]) == 1 else "s",
                                                                       sum(files[i]["bytes"] for i in m["files"]) / 1e6)
            conn.send_message(msg)
            job["log"] += "sent\n"
            ids.append(msg["Message-ID"])
    finally:
        try:
            conn.quit()
        except Exception:
            pass
    return ids


def _smtp_error(e):
    """a plain reason, without anything from .env"""
    import smtplib
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return "Gmail refused the sign-in: check GMAIL_SMTP_USER and GMAIL_APP_PASSWORD in .env (an app password, not your Google password)"
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return "Gmail refused the recipient: check IG_EMAIL_TO in .env"
    if isinstance(e, smtplib.SMTPDataError) and e.smtp_code == 552:
        return "Gmail says the email is too big"
    if isinstance(e, (OSError, smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)) and not isinstance(e, smtplib.SMTPException):
        return "could not reach the mail server (%s)" % (type(e).__name__)
    return str(e)[:200] or type(e).__name__


def start_email(slug, req, cfg):
    """the send as a job in this process (the render jobs' pattern and list, so /api/build counts it busy)"""
    jid = "s%s" % uuid.uuid4().hex[:8]
    job = {"id": jid, "slug": slug, "kind": "email", "status": "queued", "log": "", "started": time.time(), "ended": None, "error": None, "cmd": None}
    with _lock:
        _jobs[jid] = job

    def run():
        job["status"] = "running"
        try:
            ids = _smtp_send(cfg, req, job)
            req.update(status="sent", sent_at=time.strftime("%Y-%m-%dT%H:%M:%S"), message_ids=ids)
            req.pop("error", None)
            job["status"] = "done"
        except Exception as e:
            why = _smtp_error(e)
            req.update(status="failed", error=why, failed_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            job["status"], job["error"] = "failed", why
            job["log"] += "failed: %s\n" % why
        with _lock:
            write_email(slug, req)
        job["ended"] = time.time()
    threading.Thread(target=run, daemon=True, name="social-" + jid).start()
    return job


def request_email(slug, man):
    """Email to my phone: with the Gmail keys in .env, send now (a job); without them, write the request for Claude
    (a pending one for the same files is kept, not doubled). Returns (request, job or None, already)."""
    old = read_email(slug)
    if _running(slug, "email"):
        raise EmailRefused("It is being sent right now.", 409, "sending")
    req = build_email_request(slug, man)
    cfg = smtp_config()
    if cfg:
        req.update(status="sending", via="smtp")
        write_email(slug, req)
        return req, start_email(slug, req, cfg), False
    req["via"] = "claude"
    if old and old.get("status") == "requested" and [x.get("sha256") for x in old.get("files") or []] == [x["sha256"] for x in req["files"]]:
        return old, None, True
    write_email(slug, req)
    return req, None, False


def pending_email_requests(verify=True):
    """every request still waiting for Claude, each file checked against its sha256 (a re-render since = a problem);
    verify=False skips the hashing (.tmp/answers_watch.py only needs to know that one is waiting)"""
    out = []
    base = ig_dir()
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        if not d.is_dir() or not _slug(d.name):
            continue
        req = read_email(d.name)
        if not req or req.get("status") != "requested":
            continue
        problems = []
        for x in (req.get("files") or []) if verify else []:
            p = Path(x.get("path") or "")
            if not p.is_file():
                problems.append("%s is gone" % x.get("filename"))
            elif p.stat().st_size != x.get("bytes") or _sha256(p) != x.get("sha256"):
                problems.append("%s changed since the request (re-rendered?)" % x.get("filename"))
        files = req.get("files") or []
        msgs = req.get("messages") or [{"part": 1, "of": 1, "subject": req.get("subject"), "body": req.get("body"), "files": list(range(len(files)))}]
        out.append({"slug": d.name, "id": req.get("id"), "requested": req.get("requested"), "subject": req.get("subject"),
                    "slides": len(files), "total_bytes": req.get("total_bytes"), "problems": problems,
                    "emails": [{"part": m["part"], "of": m["of"], "subject": m["subject"], "body": m["body"],
                                "attachments": [{"ref": "sha256:%s:%s" % (files[i]["sha256"], files[i]["path"]), "filename": files[i]["filename"],
                                                 "bytes": files[i]["bytes"]} for i in m["files"]]} for m in msgs]})
    return out


def mark_email(slug, rid, status, message_ids=None, error=None):
    """Claude's side: status sent (with the Gmail message ids) or failed (with the reason). Returns the request."""
    slug = _slug(slug)
    req = read_email(slug) if slug else None
    if not req or req.get("id") != rid:
        raise ValueError("no email request %s for %s" % (rid, slug))
    req["status"] = status
    if status == "sent":
        req["sent_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        req["message_ids"] = list(message_ids or [])
        req.pop("error", None)
    else:
        req["error"] = str(error or "unknown")[:300]
        req["failed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_email(slug, req)
    return req


# ------------------------------------------------------------------------------------- the page + its runtime shim
# The artifact page talks to a Firestore-like `db` from window.claude.use('db'); here the same surface is backed by
# /api/social/<slug>/db: one GET for everything, every write POSTed and echoed to the page's own listeners, and a
# poll every 6 s so a document Claude writes from a session (or an import) shows up. `sample` (Ask Claude) is not
# available locally: the page disables those buttons itself.
SHIM = r"""
(function(){
  const API = window.SOCIAL_API, SLUG = window.SOCIAL_SLUG;
  let docs = {}, version = null, loaded = false;
  // Stress test 2026-11-11: a poll's answer that was read BEFORE one of this page's writes but arrived after it put the
  // older documents back (a photo popped out of the slot it had just been given, for up to 6 s), and a save made on that
  // photo meanwhile wrote the old fields back for good. A poll that crossed a write is dropped; the next one is current.
  let wseq = 0, writing = 0;
  const docL = new Map(), collL = new Map();
  const snapDoc = p => { const d = docs[p]; return { exists: d !== undefined, data: () => d, id: p.split('/')[1], metadata: { fromCache: false } }; };
  const snapColl = c => { const ds = Object.keys(docs).filter(k => k.startsWith(c + '/')).map(snapDoc); return { docs: ds, size: ds.length, metadata: { fromCache: false }, empty: !ds.length }; };
  const fire = (paths) => {
    const colls = new Set(paths.map(p => p.split('/')[0]));
    for (const p of paths) for (const cb of docL.get(p) || []) { try { cb(snapDoc(p)); } catch (e) { console.error(e); } }
    for (const c of colls) for (const cb of collL.get(c) || []) { try { cb(snapColl(c)); } catch (e) { console.error(e); } }
  };
  async function load() {
    const r = await fetch(API + '/api/social/' + SLUG + '/db', { cache: 'no-store' });
    if (!r.ok) throw Object.assign(new Error('db ' + r.status), { code: 'unavailable' });
    const j = await r.json();
    const before = docs; docs = j.docs || {}; version = j.version; loaded = true;
    const changed = new Set([...Object.keys(before), ...Object.keys(docs)].filter(k => JSON.stringify(before[k]) !== JSON.stringify(docs[k])));
    return [...changed];
  }
  async function poll() {
    const w0 = wseq;
    try { const r = await fetch(API + '/api/social/' + SLUG + '/db?version=' + encodeURIComponent(version || ''), { cache: 'no-store' });
      if (r.status === 304) return; const j = await r.json(); if (!j.ok || j.version === version) return;
      if (wseq !== w0 || writing) return;
      const before = docs; docs = j.docs || {}; version = j.version;
      fire([...new Set([...Object.keys(before), ...Object.keys(docs)])].filter(k => JSON.stringify(before[k]) !== JSON.stringify(docs[k])));
    } catch (e) {}
  }
  const ready = load().then(() => { setInterval(poll, 6000); }).catch(e => { console.error(e); throw e; });
  async function write(path, data) {
    writing++;
    try {
      const r = await fetch(API + '/api/social/' + SLUG + '/db', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ op: data === null ? 'delete' : 'set', path, data }) });
      if (!r.ok) throw Object.assign(new Error('write ' + r.status), { code: 'unavailable' });
      const j = await r.json(); version = j.version;
      if (data === null) delete docs[path]; else docs[path] = JSON.parse(JSON.stringify(data));
    } finally { writing--; wseq++; }
    fire([path]);
  }
  const db = {
    doc: path => ({
      onSnapshot(cb, err) { const l = docL.get(path) || []; l.push(cb); docL.set(path, l); ready.then(() => cb(snapDoc(path)), e => err && err(e)); return () => { const a = docL.get(path) || []; const i = a.indexOf(cb); if (i >= 0) a.splice(i, 1); }; },
      get: () => ready.then(() => snapDoc(path)),
      set: data => write(path, data),
      update: patch => write(path, Object.assign({}, docs[path] || {}, patch)),
      delete: () => write(path, null)
    }),
    collection: name => ({
      onSnapshot(cb, err) { const l = collL.get(name) || []; l.push(cb); collL.set(name, l); ready.then(() => cb(snapColl(name)), e => err && err(e)); return () => { const a = collL.get(name) || []; const i = a.indexOf(cb); if (i >= 0) a.splice(i, 1); }; },
      get: () => ready.then(() => snapColl(name)),
      doc: id => db.doc(name + '/' + id)
    })
  };
  window.claude = { use: name => name === 'db' ? Promise.resolve(db) : Promise.resolve(null), local: true };
  window.__socialDb = { docs: () => docs, version: () => version, loaded: () => loaded, poll, write };
  // the editor suite's own faces, so the page sits in the tab like the other tabs
  document.addEventListener('DOMContentLoaded', () => { document.documentElement.style.setProperty('--display', "'Newsreader', Georgia, serif"); document.documentElement.style.setProperty('--ui', "'Hanken Grotesk', sans-serif"); });
})();
"""


def page_html(slug, api_base, embed=False):
    """tools/ig_post_page.html with the shim and the suite's fonts injected before its first script; embed = inside the
    Social tab's iframe (the page drops its header, the tab drives it)"""
    html = PAGE.read_text(encoding="utf-8")
    inject = ("<script>window.SOCIAL_API=%s;window.SOCIAL_SLUG=%s;window.SOCIAL_BLOB=%s;window.SOCIAL_EMBED=%s;</script><script>%s</script>"
              "<link rel=\"stylesheet\" href=\"%s/site/fonts.css\">" % (
                  json.dumps(api_base), json.dumps(slug), json.dumps("%s/api/social/%s/blob/" % (api_base, slug)), "true" if embed else "false", SHIM, api_base))
    i = html.find("<script")
    return html[:i] + inject + html[i:] if i >= 0 else inject + html


# ------------------------------------------------------------------------------------- routes
def _json():
    b = request.get_json(force=True, silent=True)
    return b if isinstance(b, dict) else {}


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@bp.route("/api/social", methods=["GET", "OPTIONS"])
def api_social():
    if request.method == "OPTIONS":
        return "", 204
    out = []
    base = ig_dir()
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if not d.is_dir() or not _slug(d.name):
                continue
            man = manifest(d.name)
            if not man:
                continue
            st = state_dir(d.name)
            plan = st / "carousel" / "main.json"
            r = renders(d.name)
            iso = COUNTRIES.get(d.name, (None, None))[1]
            flag = f"{FLAGS_DIR}/{iso}.png" if iso and (ROOT / FLAGS_DIR / f"{iso}.png").is_file() else None
            out.append({"slug": d.name, "country": man.get("country") or d.name.title(), "photos": len(man.get("photos") or []), "flag": flag,
                        "pages": len(man.get("pages") or []), "drafts": bool(man.get("drafts")), "artifact": ARTIFACTS.get(d.name),
                        "has_plan": plan.is_file() or (base / d.name / "carousel" / "main.json").is_file(),
                        "has_seed": (SEEDS / (d.name + ".json")).is_file(),
                        "renders": {"carousel": len(r["carousel"]), "free": len(r["free"]), "pins": len(r["pins"]),
                                    "stories_carousel": len(r["stories_carousel"]), "stories_free": len(r["stories_free"])},
                        "updated": max([f.stat().st_mtime for f in st.rglob("*.json")] or [0]) if st.is_dir() else 0,
                        "running": _running(d.name)})
    return jsonify({"ok": True, "countries": out, "out_dir": _rel(out_dir()) or str(out_dir())})


@bp.route("/social/<slug>/page")
def social_page(slug):
    slug = _slug(slug)
    if not slug or not manifest(slug):
        return Response("no such country in .tmp/ig", status=404, mimetype="text/plain")
    api_base = request.url_root.rstrip("/")
    return Response(page_html(slug, api_base, embed=request.args.get("embed") in ("1", "true")), mimetype="text/html", headers={"Cache-Control": "no-store"})


@bp.route("/api/social/<slug>/db", methods=["GET", "POST", "OPTIONS"])
def api_db(slug):
    if request.method == "OPTIONS":
        return "", 204
    slug = _slug(slug)
    man = manifest(slug) if slug else None
    if not man:
        return jsonify({"ok": False, "error": "no such country in .tmp/ig (run tools/ig_post.py \"<Country>\" first)"}), 404
    with _lock:
        if request.method == "GET":
            v = version_of(slug)
            if request.args.get("version") and request.args["version"] == v and (state_dir(slug) / "catalog" / "main.json").is_file():
                return "", 304
            docs = ensure_store(slug, man, migrate=not request.args.get("version"))   # the page's first read, not its polls
            return jsonify({"ok": True, "version": version_of(slug), "docs": docs, "slug": slug, "country": man.get("country")})
        body = _json()
        try:
            if body.get("op") == "delete":
                write_doc(slug, str(body.get("path") or ""), None)
            elif body.get("op") == "set":
                write_doc(slug, str(body.get("path") or ""), body.get("data"))
            elif body.get("op") == "batch":
                ops = body.get("ops")
                if not isinstance(ops, list) or not all(isinstance(o, dict) for o in ops):
                    raise ValueError("a batch needs ops: a list of {op, path, data}")
                for o in ops:
                    write_doc(slug, str(o.get("path") or ""), None if o.get("op") == "delete" else o.get("data"))
            else:
                return jsonify({"ok": False, "error": "op must be set, delete or batch"}), 400
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "version": version_of(slug)})


@bp.route("/api/social/<slug>/blob/<pid>")
def api_blob(slug, pid):
    slug = _slug(slug)
    if not slug or not DOC_RE.match(pid or ""):
        return Response("", status=404)
    web = ig_dir() / slug / "web"
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if (web / (pid + ext)).is_file():
            resp = send_from_directory(web, pid + ext)
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp
    return Response("", status=404)


@bp.route("/api/social/<slug>/file/<kind>/<name>")
def api_file(slug, kind, name):
    slug = _slug(slug)
    folder = render_folder(slug, kind) if slug and re.match(r"^[a-z-]{1,24}$", kind or "") else None
    if not folder or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,120}\.(png|jpg|jpeg|txt)$", name or "") or not (folder / name).is_file():
        return Response("", status=404)
    if kind == "pins" and not name.startswith(slug + "-"):
        return Response("", status=404)
    resp = send_from_directory(folder, name)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/api/social/<slug>/renders", methods=["GET", "OPTIONS"])
def api_renders(slug):
    if request.method == "OPTIONS":
        return "", 204
    slug = _slug(slug)
    if not slug or not manifest(slug):
        return jsonify({"ok": False, "error": "no such country"}), 404
    return jsonify(dict(renders(slug), ok=True, running=_running(slug)))


@bp.route("/api/social/<slug>/render", methods=["POST", "OPTIONS"])
def api_render(slug):
    if request.method == "OPTIONS":
        return "", 204
    slug = _slug(slug)
    man = manifest(slug) if slug else None
    if not man:
        return jsonify({"ok": False, "error": "no such country"}), 404
    kind = (_json().get("kind") or "carousel").strip()
    with _lock:
        if _running(slug):
            return jsonify({"ok": False, "error": "a render or an email for this country is already running"}), 409
        if kind in ("carousel", "stories-carousel") and not (state_dir(slug) / "carousel" / "main.json").is_file():
            return jsonify({"ok": False, "error": "no carousel plan yet: open the Itinerary template mode (or Seed) first"}), 400
        if kind == "pins" and not (SEEDS / (slug + ".json")).is_file():
            return jsonify({"ok": False, "error": "no tools/ig_carousel/%s.json: the pins list lives in the seed" % slug}), 400
        try:
            job = start_render(slug, kind, man.get("country") or slug)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "job": job_payload(job)})


@bp.route("/api/social/<slug>/post", methods=["GET", "OPTIONS"])
def api_post(slug):
    """the Post view: the Instagram preview's data (account, Final row with crops, caption, hashtags), the render and
    whether it is stale, and the last email"""
    if request.method == "OPTIONS":
        return "", 204
    slug = _slug(slug)
    man = manifest(slug) if slug else None
    if not man:
        return jsonify({"ok": False, "error": "no such country"}), 404
    ps = post_state(slug, man)
    return jsonify(dict(ps, ok=True, country=man.get("country") or slug, handle=ig_handle(), avatar="Images/web/icons/icon-192.png",
                        render=free_render(slug, ps["slides"]), email=email_summary(read_email(slug)), direct=smtp_config() is not None))


@bp.route("/api/social/<slug>/email", methods=["GET", "POST", "OPTIONS"])
def api_email(slug):
    """GET -> {ok, email}; POST -> sends the render now (Gmail keys in .env: {ok, email, job}) or writes the request for
    Claude ({ok, email, job: null}); 400 not rendered, 409 stale / rendering / sending"""
    if request.method == "OPTIONS":
        return "", 204
    slug = _slug(slug)
    man = manifest(slug) if slug else None
    if not man:
        return jsonify({"ok": False, "error": "no such country"}), 404
    if request.method == "GET":
        return jsonify({"ok": True, "email": email_summary(read_email(slug))})
    try:
        with _lock:
            req, job, already = request_email(slug, man)
    except EmailRefused as e:
        return jsonify({"ok": False, "error": str(e), "code": e.code}), e.status
    return jsonify({"ok": True, "email": email_summary(req), "job": job_payload(job) if job else None, "already": already})


@bp.route("/api/social/job/<jid>", methods=["GET", "OPTIONS"])
def api_job(jid):
    if request.method == "OPTIONS":
        return "", 204
    with _lock:
        j = _jobs.get(jid)
    if not j:
        return jsonify({"ok": False, "error": "no such job"}), 404
    return jsonify({"ok": True, "job": job_payload(j)})


@bp.route("/api/social/<slug>/seed", methods=["POST", "OPTIONS"])
def api_seed(slug):
    if request.method == "OPTIONS":
        return "", 204
    slug = _slug(slug)
    man = manifest(slug) if slug else None
    if not man:
        return jsonify({"ok": False, "error": "no such country"}), 404
    if not (SEEDS / (slug + ".json")).is_file():
        return jsonify({"ok": False, "error": "no tools/ig_carousel/%s.json to seed from" % slug}), 400
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.run([sys.executable, str(TOOLS / "ig_carousel.py"), man.get("country") or slug, "seed"], cwd=str(ROOT),
                       capture_output=True, env=env, creationflags=NO_WINDOW, timeout=300)
    log = (p.stdout + p.stderr).decode("utf-8", "replace")
    if p.returncode:
        return jsonify({"ok": False, "error": "seed failed", "log": log[-2000:]}), 500
    seed_out = ROOT / ".tmp" / "ig" / slug / "carousel"          # ig_carousel writes under the repo's .tmp/ig, always
    with _lock:
        for name in ("main", "map"):
            f = seed_out / (name + ".json")
            if f.is_file():
                write_doc(slug, "carousel/" + name, _body(json.loads(f.read_text(encoding="utf-8-sig"))))
    return jsonify({"ok": True, "log": log[-2000:], "version": version_of(slug)})


@bp.route("/api/social/<slug>/import", methods=["POST", "OPTIONS"])
def api_import(slug):
    if request.method == "OPTIONS":
        return "", 204
    slug = _slug(slug)
    if not slug or not manifest(slug):
        return jsonify({"ok": False, "error": "no such country"}), 404
    src = Path(str(_json().get("dir") or "")).expanduser()
    if not src.is_absolute():
        src = ROOT / src
    try:
        src.resolve().relative_to((ROOT / ".tmp").resolve())
    except ValueError:
        return jsonify({"ok": False, "error": "the export folder must be under .tmp"}), 400
    if not src.is_dir():
        return jsonify({"ok": False, "error": "no folder %s" % src}), 400
    copied = 0
    with _lock:
        for coll in COLLECTIONS:
            cd = src / coll
            if not cd.is_dir():
                continue
            for f in sorted(cd.glob("*.json")):
                try:
                    write_doc(slug, "%s/%s" % (coll, f.stem), _body(json.loads(f.read_text(encoding="utf-8-sig"))))
                    copied += 1
                except Exception:
                    continue
    return jsonify({"ok": True, "copied": copied, "version": version_of(slug)})


# ------------------------------------------------------------------------------------- photos from the photo library
# Kevin, 2026-11-11: "let me add more photos to the Photos tab from the photo library". The library is the backed-up
# albums (~/Backup/<album>/, the editor's Library app); a picked photo gets the same 1280 px sRGB web copy and manifest
# entry ig_post.py collect makes for an article photo, with its original being the album file itself (read only), so
# the renders cut it from full resolution like any other. Its "article" is "library" and its title the album's name.
LIB_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}


def backup_dir():
    return Path(os.environ.get("SOCIAL_BACKUP_DIR") or (Path.home() / "Backup"))


def _album(name):
    """one folder name under the backup root, or None (no separators, no dot tricks)"""
    s = str(name or "").strip()
    if not s or s in (".", "..") or re.search(r'[\\/:*?"<>|\x00-\x1f]', s):
        return None
    p = backup_dir() / s
    try:
        p.resolve().relative_to(backup_dir().resolve())
    except ValueError:
        return None
    return p if p.is_dir() else None


def _orig_key(p):
    return str(p).replace("\\", "/").lower()


def _write_json(f, data):
    tmp = f.with_name(f.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def add_library_photos(slug, album, names):
    """web copies + manifest entries for the picked album photos; returns (added ids, skipped names)"""
    import ig_post                                            # PIL + the ICC-aware helpers (and the HEIC opener)
    folder = _album(album)
    if folder is None:
        raise ValueError("no album %r in the photo library" % album)
    base = ig_dir() / slug
    man_f = base / "manifest.json"
    man = json.loads(man_f.read_text(encoding="utf-8"))
    photos = man.setdefault("photos", [])
    have = {_orig_key((ROOT / p["original"]) if not Path(p["original"]).is_absolute() else p["original"]) for p in photos if p.get("original")}
    ids = {p["id"] for p in photos}
    web = base / "web"
    web.mkdir(parents=True, exist_ok=True)
    added, skipped = [], []
    for name in names:
        name = os.path.basename(str(name or ""))
        f = folder / name
        if not name or f.suffix.lower() not in LIB_EXTS or not f.is_file() or _orig_key(f.resolve()) in have:
            skipped.append(name)
            continue
        try:
            im, icc = ig_post.open_upright(f)
            t, gps = ig_post.exif_info(ig_post.Image.open(f))
            w, h = im.size
            pid0 = re.sub(r"[^a-z0-9_.-]+", "-", ("lib-%s-%s" % (album, f.stem)).lower()).strip("-")[:110]
            pid, k = pid0, 2
            while pid in ids:
                pid, k = "%s-%d" % (pid0, k), k + 1
            s = ig_post.WEB_EDGE / max(w, h)
            small = im.resize((round(w * s), round(h * s)), ig_post.Image.LANCZOS) if s < 1 else im
            dst = web / (pid + ".jpg")
            ig_post.save_srgb(ig_post.to_srgb(small, icc), dst, ig_post.WEB_Q)
        except Exception as e:                               # an unreadable file is skipped, the rest still go in
            skipped.append("%s (%s)" % (name, str(e)[:80]))
            continue
        rel = _rel(f)
        photos.append({"id": pid, "original": rel or str(f.resolve()).replace("\\", "/"), "article": "library",
                       "article_title": "Photo library: " + album, "section": "", "caption": "", "alt": "", "time": t, "gps": gps,
                       "w": w, "h": h, "web": _rel(dst) or str(dst), "web_bytes": dst.stat().st_size, "icc": bool(icc),
                       "source": "library", "album": album, "name": name})
        ids.add(pid)
        have.add(_orig_key(f.resolve()))
        added.append(pid)
    if added:
        _write_json(man_f, man)
        cat = read_store(slug).get("catalog/main")
        if cat and cat.get("source") != "manifest":            # an imported artifact catalog: the new photos are appended to it
            new = [p for p in photos if p["id"] in set(added)]
            cat["items"] = (cat.get("items") or []) + [{k: p.get(k) for k in ("id", "article", "article_title", "section", "caption", "alt", "time", "w", "h")} | {"asset": p["id"]} for p in new]
            write_doc(slug, "catalog/main", cat)
        ensure_store(slug, man, migrate=False)                 # a manifest catalog is rebuilt, so the version moves and the page's poll sees it
    return added, skipped


@bp.route("/api/social/<slug>/library", methods=["GET", "OPTIONS"])
def api_library(slug):
    """the names of one album's photos that are already in this country's photos (the picker marks them)"""
    if request.method == "OPTIONS":
        return "", 204
    slug = _slug(slug)
    man = manifest(slug) if slug else None
    if not man:
        return jsonify({"ok": False, "error": "no such country"}), 404
    folder = _album(request.args.get("album"))
    if folder is None:
        return jsonify({"ok": True, "names": []})
    root = _orig_key(folder.resolve()) + "/"
    names = []
    for p in man.get("photos") or []:
        o = p.get("original") or ""
        k = _orig_key((ROOT / o).resolve() if not Path(o).is_absolute() else Path(o).resolve())
        if k.startswith(root):
            names.append(Path(o).name)
    return jsonify({"ok": True, "names": names})


@bp.route("/api/social/<slug>/add", methods=["POST", "OPTIONS"])
def api_add(slug):
    """POST {album, names: [...]} -> {ok, added: [ids], skipped: [names], version}"""
    if request.method == "OPTIONS":
        return "", 204
    slug = _slug(slug)
    if not slug or not manifest(slug):
        return jsonify({"ok": False, "error": "no such country"}), 404
    body = _json()
    names = body.get("names") or []
    if not isinstance(names, list) or not names or len(names) > 300:
        return jsonify({"ok": False, "error": "names: 1 to 300 photo file names"}), 400
    try:
        with _lock:
            added, skipped = add_library_photos(slug, str(body.get("album") or ""), names)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "added": added, "skipped": skipped, "version": version_of(slug)})


def register(app):
    """photo_editor.py calls this: the Social tab's API lives on the photo server"""
    app.register_blueprint(bp)


# ------------------------------------------------------------------------------------- standalone
def make_app():
    from flask import Flask
    app = Flask("social_tab")
    register(app)
    app.after_request(_cors)

    @app.route("/site/<path:rel>")
    def site(rel):
        return send_from_directory(ROOT, rel)
    return app


def _say(obj):
    sys.stdout.buffer.write((json.dumps(obj, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--email-requests"]:
        _say(pending_email_requests())
    elif a[:1] in (["--email-sent"], ["--email-failed"]):
        if len(a) < 3 or (a[0] == "--email-failed" and len(a) < 4):
            raise SystemExit("usage: --email-sent <slug> <id> [message ids...] | --email-failed <slug> <id> <reason>")
        try:
            r = mark_email(a[1], a[2], "sent" if a[0] == "--email-sent" else "failed",
                           message_ids=a[3:] if a[0] == "--email-sent" else None, error=" ".join(a[3:]) if a[0] == "--email-failed" else None)
        except ValueError as e:
            raise SystemExit(str(e))
        _say(email_summary(r))
    elif "--serve" in sys.argv:
        port = int(sys.argv[sys.argv.index("--serve") + 1])
        print("social tab api: http://127.0.0.1:%d  store: %s  out: %s" % (port, ig_dir(), out_dir()), flush=True)
        make_app().run(port=port, debug=False, threaded=True)
    else:
        with make_app().test_request_context():
            sys.stdout.buffer.write(json.dumps(api_social().get_json(), indent=1, ensure_ascii=False).encode("utf-8"))
