"""A code fingerprint for each suite server, so the launcher and the keeper can tell a server
running yesterday's code from the files on disk (Kevin, 2026-11-07: "the queue should just work
with clicking the shortcut that I've been using to open up the editor").

    fingerprint(entry)          -> {"build": <12 hex>, "files": {rel: <8 hex>}, "entry": rel}
    install(app, entry, busy=)  -> gives a Flask server GET/POST /api/build

The fingerprint is a hash of the entry script plus every module it imports from tools/, found
by reading the source (a static scan), so a module imported inside a function (alt_writer,
ask_gemini, explore_more...) counts too, and nothing is imported to compute it. Each server
computes its own at startup and reports it at /api/build; photo_suite computes the same from
the files on disk and restarts a server whose build differs, but only when the server says it
is not busy (/api/build's `busy` list is empty): no background job running or queued, no POST
in flight (a save merge, an import, a caption, a hero or map Save).

POST /api/build {"action": "exit", "build": <the build the caller saw>} asks the server to
leave on its own: it re-checks busy, answers, and exits half a second later. The launcher or
the keeper then starts it fresh. A stale build in the request (409) or a busy server (ok:false)
exits nothing.
"""
import hashlib
import os
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
_IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w]*)\s+import\b|import\s+([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)*))", re.M)
_cache = {}          # path -> (mtime_ns, size, sha256 hex, [tools modules it names])


def _file(p):
    st = p.stat()
    key = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(p)
    if hit and hit[0] == key:
        return hit[1], hit[2]
    data = p.read_bytes()
    text = data.decode("utf-8", "replace")
    names = []
    for m in _IMPORT_RE.finditer(text):
        for n in (m.group(1) or m.group(2)).split(","):
            n = n.strip()
            if n and (TOOLS / (n + ".py")).is_file() and n not in names:
                names.append(n)
    h = hashlib.sha256(data).hexdigest()
    _cache[p] = (key, h, names)
    return h, names


def files_of(entry):
    """the entry script and, transitively, every tools/*.py it names in an import"""
    entry = Path(entry).resolve()
    seen, todo = {}, [entry]
    while todo:
        p = todo.pop()
        if p in seen or not p.is_file():
            continue
        try:
            h, names = _file(p)
        except OSError:
            continue
        seen[p] = h
        for n in names:
            todo.append((TOOLS / (n + ".py")).resolve())
    return seen


def fingerprint(entry):
    seen = files_of(entry)
    rels = {}
    for p, h in seen.items():
        try:
            rel = p.relative_to(ROOT).as_posix()
        except ValueError:
            rel = p.as_posix()
        rels[rel] = h[:8]
    whole = hashlib.sha256("\n".join("%s %s" % kv for kv in sorted(rels.items())).encode()).hexdigest()
    try:
        entry_rel = Path(entry).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        entry_rel = str(entry)
    return {"build": whole[:12], "files": rels, "entry": entry_rel}


# ------------------------------------------------------------------ the route
_exit = os._exit          # tests swap this for a recorder


def install(app, entry, busy=None, on_exit=None, exclude=("/api/build", "/__keeper")):
    """Give a Flask app /api/build. `busy()` returns a list of reasons the server must not be restarted now
    (empty when it may be); the in-flight write counter is added to it here. `on_exit()` runs before the
    process leaves (close a child process, say)."""
    from flask import g, jsonify, request
    info = fingerprint(entry)
    state = {"inflight": 0, "lock": threading.Lock(), "started": time.time(), "exiting": False}

    @app.before_request
    def _bid_in():
        if request.method in ("GET", "HEAD", "OPTIONS") or request.path in exclude:
            return
        g._bid_counted = True
        with state["lock"]:
            state["inflight"] += 1

    @app.teardown_request
    def _bid_out(exc=None):
        if getattr(g, "_bid_counted", False):
            with state["lock"]:
                state["inflight"] = max(0, state["inflight"] - 1)

    def reasons():
        out = []
        n = state["inflight"]
        if n:
            out.append("%d request%s in flight" % (n, "" if n == 1 else "s"))
        if busy:
            try:
                out.extend(str(r) for r in (busy() or []))
            except Exception as e:                      # a broken busy check reads as busy, never as idle
                out.append("busy check failed: %s" % e)
        return out

    def payload():
        return {"ok": True, "build": info["build"], "entry": info["entry"], "files": len(info["files"]),
                "pid": os.getpid(), "started": state["started"], "busy": reasons(), "exiting": state["exiting"]}

    @app.route("/api/build", methods=["GET", "POST", "OPTIONS"])
    def api_build():
        if request.method == "OPTIONS":
            return "", 204
        resp = None
        if request.method == "GET":
            resp = jsonify(payload())
        else:
            d = request.get_json(force=True, silent=True) or {}
            if d.get("action") != "exit":
                resp = jsonify({"ok": False, "error": "unknown action"}); resp.status_code = 400
            elif d.get("build") and d["build"] != info["build"]:
                resp = jsonify(dict(payload(), ok=False, error="that is not this build")); resp.status_code = 409
            else:
                why = reasons()
                if why:
                    resp = jsonify(dict(payload(), ok=False, busy=why))
                else:
                    state["exiting"] = True

                    def leave():
                        time.sleep(0.5)
                        try:
                            if on_exit:
                                on_exit()
                        except Exception:
                            pass
                        _exit(0)
                    threading.Thread(target=leave, daemon=True, name="build-exit").start()
                    resp = jsonify(dict(payload(), exiting=True))
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    app.config["BUILD_ID"] = info
    return info


if __name__ == "__main__":
    import json
    for a in [a for a in sys.argv[1:] if not a.startswith("-")] or ["tools/photo_editor.py", "tools/hero_picker.py", "tools/map_editor.py", "tools/photo_suite.py"]:
        f = fingerprint(ROOT / a)
        print(f["build"], f["entry"], "(%d files)" % len(f["files"]))
        if "-v" in sys.argv:
            print(json.dumps(f["files"], indent=1))
