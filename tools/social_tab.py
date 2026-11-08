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


def ensure_store(slug, man):
    """first read of a country: a catalog from the manifest, and the seed's plan + map (ig_carousel.py seed wrote
    .tmp/ig/<slug>/carousel/main.json + map.json) copied in, so the template mode opens on the current plan"""
    docs = read_store(slug)
    changed = False
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
        return ["social render: %s %s" % (j["slug"], j["kind"]) for j in _jobs.values() if j["status"] in ("queued", "running")]


def _running(slug):
    return any(j["slug"] == slug and j["status"] in ("queued", "running") for j in _jobs.values())


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
        cmd = [sys.executable, str(TOOLS / "ig_post.py"), country, "render", "--state", str(st), "--force"]
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


# ------------------------------------------------------------------------------------- the page + its runtime shim
# The artifact page talks to a Firestore-like `db` from window.claude.use('db'); here the same surface is backed by
# /api/social/<slug>/db: one GET for everything, every write POSTed and echoed to the page's own listeners, and a
# poll every 6 s so a document Claude writes from a session (or an import) shows up. `sample` (Ask Claude) is not
# available locally: the page disables those buttons itself.
SHIM = r"""
(function(){
  const API = window.SOCIAL_API, SLUG = window.SOCIAL_SLUG;
  let docs = {}, version = null, loaded = false;
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
    try { const r = await fetch(API + '/api/social/' + SLUG + '/db?version=' + encodeURIComponent(version || ''), { cache: 'no-store' });
      if (r.status === 304) return; const j = await r.json(); if (!j.ok || j.version === version) return;
      const before = docs; docs = j.docs || {}; version = j.version;
      fire([...new Set([...Object.keys(before), ...Object.keys(docs)])].filter(k => JSON.stringify(before[k]) !== JSON.stringify(docs[k])));
    } catch (e) {}
  }
  const ready = load().then(() => { setInterval(poll, 6000); }).catch(e => { console.error(e); throw e; });
  async function write(path, data) {
    const r = await fetch(API + '/api/social/' + SLUG + '/db', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ op: data === null ? 'delete' : 'set', path, data }) });
    if (!r.ok) throw Object.assign(new Error('write ' + r.status), { code: 'unavailable' });
    const j = await r.json(); version = j.version;
    if (data === null) delete docs[path]; else docs[path] = JSON.parse(JSON.stringify(data));
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
  window.__socialDb = { docs: () => docs, version: () => version, loaded: () => loaded, poll };
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
    return request.get_json(force=True, silent=True) or {}


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
            docs = ensure_store(slug, man)
            return jsonify({"ok": True, "version": version_of(slug), "docs": docs, "slug": slug, "country": man.get("country")})
        body = _json()
        try:
            if body.get("op") == "delete":
                write_doc(slug, str(body.get("path") or ""), None)
            elif body.get("op") == "set":
                write_doc(slug, str(body.get("path") or ""), body.get("data"))
            elif body.get("op") == "batch":
                for o in body.get("ops") or []:
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
            return jsonify({"ok": False, "error": "a render for this country is already running"}), 409
        if kind in ("carousel", "stories-carousel") and not (state_dir(slug) / "carousel" / "main.json").is_file():
            return jsonify({"ok": False, "error": "no carousel plan yet: open the Itinerary template mode (or Seed) first"}), 400
        if kind == "pins" and not (SEEDS / (slug + ".json")).is_file():
            return jsonify({"ok": False, "error": "no tools/ig_carousel/%s.json: the pins list lives in the seed" % slug}), 400
        try:
            job = start_render(slug, kind, man.get("country") or slug)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "job": job_payload(job)})


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


if __name__ == "__main__":
    if "--serve" in sys.argv:
        port = int(sys.argv[sys.argv.index("--serve") + 1])
        print("social tab api: http://127.0.0.1:%d  store: %s  out: %s" % (port, ig_dir(), out_dir()), flush=True)
        make_app().run(port=port, debug=False, threaded=True)
    else:
        with make_app().test_request_context():
            sys.stdout.buffer.write(json.dumps(api_social().get_json(), indent=1, ensure_ascii=False).encode("utf-8"))
