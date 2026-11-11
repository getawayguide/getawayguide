"""The Queue tab's top strip (Kevin, 2026-11-11, "let's queue 1-3,5"): what is running now, the system status line, one
"Waiting on you" count, and the held-back drawer. Registered on the photo server by queue_tab.register, like the Push
package; the standalone `python tools/queue_tab.py --serve <port>` serves it too.

Everything here is read from files that already exist; nothing is invented:

  Running now     the queue items marked [~] (or [>]) in away_queue.md, with the "(started YYYY-MM-DD HH:MM)" stamp Claude
                  adds when it marks one (runbook step 3); an item without a stamp says its start was not recorded.
                  The last hourly check is .tmp/away/lock.json ({"owner", "at", "pid"?}), written by every tick.
                  Background agents leave no file, so they show only through their queue line.
  System status   watchdog   on = .tmp/watchdog_off absent. The toggle only creates/removes that flag (and, for off, the
                             stop file .tmp/sleep_watchdog.stop, which a running watchdog obeys within a minute and which a
                             new one deletes at start). It never starts a process and never sleeps the PC; with the flag
                             gone, the next hourly check starts a watchdog (runbook steps 1a/2).
                  screen     the AC/DC "turn off display after" of the active power plan, read with `powercfg /q`
                             (read-only; the watchdog itself sets 2 min while it runs, and restores his value on exit)
                  emails     on = .tmp/emails_off absent (runbook step 3b reads the flag). The toggle creates/removes it.
                  credits    the weekly reset, Fridays 2 pm local (runbook): display only.
  Waiting on you  rounds: open "round-*" items with changes left; push: push-package items in state "open"
                  (push_package.statuses); writer: unanswered Writer threads (wr-) in .tmp/redline/_comments plus open
                  "question" to-dos; shadow: open "answer the Shadow reviewer" items (one per article; the thread count
                  rides along). Each part carries the ids the tab jumps to.
  Held back       .tmp/held_back/held_back.json (the bus maps, Yerevan) plus the Meteora map and the Kosovo page from its
                  README. A map goes back with the documented `restore.py <key>` (POST, after an in-page confirmation
                  that shows its --dry-run); the push item it belongs to reopens through push_package.mark_reopened
                  (reopened.json; his answers.json is never touched). Meteora and Kosovo are steps to copy, not run:
                  Meteora has no restore script and Kosovo is a relaunch (sitemap, search index, nav).

Routes:
  GET  /api/queue/status                       -> {ok, now, running, last_tick, system, waiting, held}
  POST /api/queue/status {op: "watchdog"|"emails", on: bool}   -> the GET payload (403 when read-only)
  POST /api/queue/held   {op: "preview", key}                  -> {ok, key, text}   (restore.py --dry-run)
                         {op: "restore", key, confirm: true}   -> {ok, key, text, reopened, ...GET payload}

Folders: the flags live in queue_tab.store_dir() (.tmp, or a test's QUEUE_TAB_DIR); the held-back folder is
<store>/held_back and the pages it restores are under its grandparent (restore.py's own ROOT rule), so a test's scratch
tree <scratch>/.tmp/held_back restores pages under <scratch>. Probes (powercfg, the process list) are skipped under a
test's QUEUE_TAB_DIR unless QUEUE_STATUS_PROBE=1.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request

import queue_tab as qt

bp = Blueprint("queue_status", __name__)
NOWIN = 0x08000000 if sys.platform == "win32" else 0
_cache = {}
_cache_lock = threading.Lock()


def store():
    return qt.store_dir()


def held_dir():
    return store() / "held_back"


def site_root():
    """the folder the held-back pages live under: restore.py's ROOT (two levels above held_back/)"""
    return held_dir().resolve().parent.parent


def probing():
    return os.environ.get("QUEUE_STATUS_PROBE") == "1" or not os.environ.get("QUEUE_TAB_DIR")


def _iso(ts):
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else None


def _log_toggle(text):
    try:
        with open(store() / "system_toggles.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
    except OSError:
        pass


_refreshing = set()


def _cached(key, ttl, fn):
    """the probe's last answer. Only the very first call waits for it; after that a stale answer is served at once and
    refreshed in the background, so the tab never waits on PowerShell (and its strip never pops in late)."""
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        if hit and key not in _refreshing:
            _refreshing.add(key)

            def bg():
                try:
                    v = fn()
                    with _cache_lock:
                        _cache[key] = (time.time(), v)
                finally:
                    with _cache_lock:
                        _refreshing.discard(key)
            threading.Thread(target=bg, daemon=True).start()
        if hit:
            return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (time.time(), val)
    return val


# ------------------------------------------------------------------------------------- running now
STARTED_RE = re.compile(r"\(started (\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?\)")


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    try:
        k.GetExitCodeProcess(h, ctypes.byref(code))
        return code.value == 259                       # STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def running():
    doc = qt.load_doc()
    out = []
    if doc is not None:
        parsed = qt.parse(doc)
        names = {g["id"]: g["name"] for g in parsed["groups"]}
        for it in parsed["queue"]:
            if it["mark"] not in ("~", ">"):            # the explicit marks only: a line that merely says "running" is not a job
                continue
            out.append({"id": it["id"], "num": it["num"], "title": it["title"], "started": it.get("started"),
                        "group": names.get(it["group"])})
    return out


def last_tick():
    p = store() / "away" / "lock.json"
    if not p.is_file():
        return None
    try:
        lk = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"at": None, "owner": None, "error": "lock.json is unreadable"}
    t = {"at": lk.get("at"), "owner": lk.get("owner")}
    if lk.get("pid") is not None:
        t["pid"] = lk["pid"]
        t["alive"] = pid_alive(lk["pid"])
    return t


# ------------------------------------------------------------------------------------- system status
def parse_powercfg(text):
    """`powercfg /q SCHEME_CURRENT SUB_VIDEO VIDEOIDLE` -> {ac, dc} in seconds (None when not found)"""
    ac = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", text or "")
    dc = re.search(r"Current DC Power Setting Index:\s*0x([0-9a-fA-F]+)", text or "")
    return {"ac": int(ac.group(1), 16) if ac else None, "dc": int(dc.group(1), 16) if dc else None}


def screen_off():
    if not probing():
        return None

    def read():
        try:
            r = subprocess.run(["powercfg", "/q", "SCHEME_CURRENT", "SUB_VIDEO", "VIDEOIDLE"], capture_output=True, timeout=10, creationflags=NOWIN)
            got = parse_powercfg(r.stdout.decode("utf-8", "replace"))
            return got if got["ac"] is not None or got["dc"] is not None else None
        except Exception:
            return None
    return _cached("screen", 30, read)


def watchdog_running():
    """True/False from the process list (the same test as .tmp/wake_test/status.ps1), None when unknown"""
    if not probing():
        return None

    def read():
        cmd = ("@(Get-CimInstance Win32_Process -Filter \"Name='powershell.exe' OR Name='pwsh.exe'\" | "
               "Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like '*sleep_watchdog.ps1*' }).Count")   # not this probe itself
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, timeout=15, creationflags=NOWIN)
            m = re.search(r"\d+", r.stdout.decode("utf-8", "replace"))
            return int(m.group(0)) > 0 if m else None
        except Exception:
            return None
    return _cached("watchdog", 20, read)


def _last_log_line(p, skip=("busy:",)):
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8000))
            lines = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip() and not any(s in l for s in skip)]
        return lines[-1][:200] if lines else None
    except OSError:
        return None


def next_reset(now=None):
    """the next Friday 2 pm local after `now` (Kevin's weekly credits reset; runbook)"""
    now = now or datetime.now()
    cand = now.replace(hour=14, minute=0, second=0, microsecond=0) + timedelta(days=(4 - now.weekday()) % 7)
    if cand <= now:
        cand += timedelta(days=7)
    return cand


def system():
    s = store()
    off = s / "watchdog_off"
    em = s / "emails_off"
    note = None
    if off.is_file():
        try:
            note = off.read_text(encoding="utf-8-sig").strip().splitlines()[0][:200]
        except (OSError, IndexError):
            note = None
    nr = next_reset()
    return {
        "watchdog": {"on": not off.is_file(), "since": _iso(off.stat().st_mtime) if off.is_file() else None, "note": note,
                     "running": watchdog_running(), "last_log": _last_log_line(s / "sleep_watchdog.log"),
                     "stop_pending": (s / "sleep_watchdog.stop").is_file()},
        "screen": screen_off(),
        "emails": {"on": not em.is_file(), "since": _iso(em.stat().st_mtime) if em.is_file() else None},
        "credits": {"next": nr.isoformat(timespec="minutes"), "rule": "Fridays 2 pm"},
    }


def set_flag(which, on):
    """the only things the toggles change: two flag files, plus the watchdog's stop file when it is turned off"""
    s = store()
    s.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
    if which == "watchdog":
        off = s / "watchdog_off"
        if on:
            if off.is_file():
                off.unlink()
            _log_toggle("watchdog ON from the Queue tab (watchdog_off removed; the next hourly check starts one)")
        else:
            off.write_text(f"Kevin, {stamp}: turned the watchdog off from the Queue tab. Hourly ticks must not start one until he turns it back on.\n", encoding="utf-8")
            (s / "sleep_watchdog.stop").write_text(f"stop requested from the Queue tab {stamp}\n", encoding="utf-8")
            _log_toggle("watchdog OFF from the Queue tab (watchdog_off + sleep_watchdog.stop created)")
        with _cache_lock:
            _cache.pop("watchdog", None)
    elif which == "emails":
        em = s / "emails_off"
        if on:
            if em.is_file():
                em.unlink()
            _log_toggle("emails ON from the Queue tab (emails_off removed)")
        else:
            em.write_text(f"Kevin, {stamp}: emails turned off from the Queue tab. Runbook step 3b sends nothing while this file exists.\n", encoding="utf-8")
            _log_toggle("emails OFF from the Queue tab (emails_off created)")
    else:
        return f"unknown toggle {which!r}"
    return None


# ------------------------------------------------------------------------------------- waiting on you
def _kevin_replied(t):
    return any((r.get("author") or "").lower().startswith("kevin") for r in t.get("replies") or [])


def writer_threads():
    """[{stem, rel, title, open}] for articles with Writer questions (wr- threads) he has not answered"""
    out = []
    d = qt.redline_dir() / "_comments"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            th = json.loads(f.read_text(encoding="utf-8-sig")).get("threads") or []
        except Exception:
            continue
        n = sum(1 for t in th if not t.get("resolved") and (t.get("author") == "Writer" or str(t.get("id", "")).startswith("wr-")) and not _kevin_replied(t))
        if n:
            slug = f.stem.split("__")[-1]
            out.append({"stem": f.stem, "rel": qt._rel_for_comments(f.stem), "title": qt._slug_name(slug), "open": n})
    return out


def waiting():
    try:
        obj, _v = qt.load_todo()
        items = obj["items"]
    except RuntimeError as e:
        items, err = [], str(e)
    else:
        err = None
    live = [i for i in items if isinstance(i, dict) and i.get("status") != "done"]

    def num(v):                                         # an agent's item may carry a count as text ("3"): read it, never crash
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def round_open(i):
        rd = i.get("round") if isinstance(i.get("round"), dict) else {}
        return not rd.get("applied") and not rd.get("gone") and (rd.get("left") is None or num(rd.get("left")) > 0)
    rounds = [i for i in live if str(i.get("id", "")).startswith("round-") and round_open(i)]
    shadow = [i for i in live if str(i.get("id", "")).startswith("shadow-") and num(i.get("shadow_open")) > 0]
    questions = [i for i in live if i.get("kind") == "question"]
    wr = writer_threads()
    n_wr = sum(w["open"] for w in wr)
    push_ids, push_reopened, push_err = [], [], None
    try:
        import push_package as pp
        pkg, _v = pp.load_package()
        if pkg:
            store_, _o = pp.load_answers(pkg["id"])
            st = pp.statuses(pkg, store_, pp.load_done(pkg["id"]))
            push_ids = [i for i, s in st.items() if s["state"] == "open"]
            push_reopened = [i for i, s in st.items() if s.get("reopened")]
    except Exception as e:                               # a half-written package must not take the strip down
        push_err = str(e)
    parts = [
        {"id": "rounds", "n": len(rounds), "label": "round" if len(rounds) == 1 else "rounds", "ids": [i["id"] for i in rounds],
         "changes": sum(num((i.get("round") or {}).get("left")) for i in rounds)},
        {"id": "push", "n": len(push_ids), "label": "push item" if len(push_ids) == 1 else "push items", "ids": push_ids, "reopened": push_reopened},
        {"id": "writer", "n": n_wr + len(questions), "label": "writer question" if n_wr + len(questions) == 1 else "writer questions",
         "articles": wr, "ids": [i["id"] for i in questions]},
        {"id": "shadow", "n": len(shadow), "label": "Shadow pass" if len(shadow) == 1 else "Shadow passes", "ids": [i["id"] for i in shadow],
         "threads": sum(num(i.get("shadow_open")) for i in shadow)},
    ]
    out = {"total": sum(p["n"] for p in parts), "parts": parts}
    if err or push_err:
        out["error"] = err or push_err
    return out


# ------------------------------------------------------------------------------------- held back
def _push_changes():
    try:
        import push_package as pp
        pkg, _v = pp.load_package()
    except Exception:
        return None, []
    if not pkg:
        return None, []
    return pkg, [e for _k, e in pp.all_entries(pkg)]


def _clean_title(t):
    t = re.sub(r"\s*\((held back|deleted|reverted[^)]*)\)\s*$", "", re.sub(r"\*\*|`", "", t or "")).strip()
    return t


def held_meta():
    """held_back.json's entries that can be read: a damaged file, or an entry without its page and anchors, lists nothing
    instead of taking the whole strip down (stress test 2026-11-11)"""
    p = held_dir() / "held_back.json"
    if not p.is_file():
        return {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return {}
    if not isinstance(obj, dict):
        return {}
    need = ("page", "data_map", "text_before_cut", "text_after_cut")
    return {k: m for k, m in obj.items() if isinstance(m, dict) and all(isinstance(m.get(f), str) for f in need)}


def held():
    """every parked thing, with its live state: held (can go back), back (on its page again), changed (the page moved
    around the cut: restore.py would stop), missing (no page)"""
    meta = held_meta()
    _pkg, changes = _push_changes()
    root = site_root()
    restored = _restored_log()
    out = []
    for key, m in meta.items():
        ch = next((e for e in changes if e.get("page") == m["page"] and str(e.get("id", "")).startswith("map-")), None)
        page = root / m["page"]
        state = "missing"
        if page.is_file():
            html = page.read_bytes().decode("utf-8", "replace")      # as restore.py reads it: CRLF kept, so the anchors match
            if f'data-map="{m["data_map"]}"' in html:
                state = "back"
            elif html.count(m["text_before_cut"] + m["text_after_cut"]) == 1 and (held_dir() / f"{key}.html").is_file():
                state = "held"
            else:
                state = "changed"
        out.append({"key": key, "kind": "map", "title": _clean_title(ch["title"]) if ch else f"Route map on {m['page']}",
                    "page": m["page"], "reason": m.get("reason"), "state": state, "push_id": ch["id"] if ch else None,
                    "action": "restore" if state == "held" else None, "restored": restored.get(key),
                    "command": f"python .tmp/held_back/restore.py {key}"})
    if (held_dir() / "meteora-new-route-map.html").is_file():
        ch = next((e for e in changes if e.get("id") == "map-meteora"), None)
        out.append({"key": "meteora", "kind": "map", "title": "New Bus Routes to Meteora map", "page": "greece/meteora.html",
                    "reason": "Kevin: \"keep old route map no changes here\" (2026-11-10); the old route map stays live",
                    "state": "held", "push_id": ch["id"] if ch else None, "action": "copy", "restored": None,
                    "command": "Claude, put the new Meteora route map back on greece/meteora.html (.tmp/held_back/README.md, Meteora section)",
                    "why_copy": "There is no restore script for it: the page's old map has to be swapped out and its config replaced."})
    draft, live = root / "Drafts" / "Upcoming" / "kosovo" / "index.html", root / "kosovo" / "index.html"
    if draft.is_file() or live.is_file():
        ch = next((e for e in changes if e.get("id") == "kosovo-page"), None)
        out.append({"key": "kosovo-page", "kind": "page", "title": "Kosovo country page",
                    "page": "kosovo/index.html" if live.is_file() or not draft.is_file() else "Drafts/Upcoming/kosovo/index.html",
                    "reason": "Kevin: \"this gets held back until there are at least 2 articles\" (2026-11-10)",
                    "state": "back" if live.is_file() else "held" if draft.is_file() else "missing", "push_id": ch["id"] if ch else None,
                    "action": "copy" if draft.is_file() and not live.is_file() else None, "restored": None,
                    "command": 'New-Item -ItemType Directory -Force kosovo | Out-Null; Move-Item "Drafts/Upcoming/kosovo/index.html" "kosovo/index.html"; '
                               "python tools/gen_sitemap.py; python tools/gen_search_index.py",
                    "why_copy": "Putting it back is a relaunch: the sitemap and search index are rebuilt, its share picture joins the commit, "
                                "and you decide whether the nav, destinations and home tile open it."})
    return out


def _restored_path():
    return held_dir() / "restored.json"


def _restored_log():
    p = _restored_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except Exception:
        return {}


def run_restore(key, dry):
    hb = held_dir()
    cmd = [sys.executable, str(hb / "restore.py"), key] + (["--dry-run"] if dry else [])
    try:
        r = subprocess.run(cmd, cwd=str(site_root()), capture_output=True, timeout=60, creationflags=NOWIN,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except subprocess.TimeoutExpired:
        return False, "restore.py took longer than a minute; nothing reported."
    text = (r.stdout + b"\n" + r.stderr).decode("utf-8", "replace").strip()
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith(("Traceback", "  File"))]
    return r.returncode == 0, (lines[-1] if lines else text)[:400]


# ------------------------------------------------------------------------------------- routes
def payload():
    return {"ok": True, "now": datetime.now().isoformat(timespec="seconds"), "running": running(), "last_tick": last_tick(),
            "system": system(), "waiting": waiting(), "held": held(), "readonly": qt.readonly()}


@bp.route("/api/queue/status", methods=["GET", "POST", "OPTIONS"])
def api_status():
    if request.method == "OPTIONS":
        return "", 204
    if request.method == "POST":
        if qt.readonly():
            return jsonify({"ok": False, "error": "this server is read-only"}), 403
        body = request.get_json(force=True, silent=True)
        body = body if isinstance(body, dict) else {}
        if not isinstance(body.get("on"), bool):
            return jsonify({"ok": False, "error": "say on: true or false"}), 400
        err = set_flag(body.get("op"), body["on"])
        if err:
            return jsonify({"ok": False, "error": err}), 400
    return jsonify(payload())


@bp.route("/api/queue/held", methods=["POST", "OPTIONS"])
def api_held():
    if request.method == "OPTIONS":
        return "", 204
    if qt.readonly():
        return jsonify({"ok": False, "error": "this server is read-only"}), 403
    body = request.get_json(force=True, silent=True)
    body = body if isinstance(body, dict) else {}
    key, op = body.get("key"), body.get("op")
    entry = next((h for h in held() if h["key"] == key), None)
    if not entry:
        return jsonify({"ok": False, "error": f"nothing held back under {key!r}"}), 404
    if entry["action"] != "restore":
        why = entry.get("why_copy") or ("It is already back on its page." if entry["state"] == "back" else
                                        "The page changed around the spot since it was cut, so the script would stop; put it back by hand.")
        return jsonify({"ok": False, "error": why, "command": entry["command"]}), 409
    if op == "preview":
        ok, text = run_restore(key, True)
        return jsonify({"ok": ok, "key": key, "text": text, "command": entry["command"]}), (200 if ok else 409)
    if op != "restore":
        return jsonify({"ok": False, "error": f"unknown op {op!r}"}), 400
    if body.get("confirm") is not True:
        return jsonify({"ok": False, "error": "a put-back needs confirm: true (the page asks first)"}), 400
    with qt.FileLock(_restored_path()):
        ok, text = run_restore(key, False)
        if not ok:
            return jsonify({"ok": False, "key": key, "error": text, "command": entry["command"]}), 409
        stamp = datetime.now()
        log = _restored_log()
        log[key] = {"at": stamp.isoformat(timespec="seconds"), "page": entry["page"], "push_id": entry["push_id"], "via": "Queue tab"}
        qt.write_bytes_atomic(_restored_path(), json.dumps(log, indent=1, ensure_ascii=False).encode("utf-8"))
    reopened = None
    if entry["push_id"]:
        try:
            import push_package as pp
            reopened = pp.mark_reopened(entry["push_id"], f"Put back on the page from the held-back drawer ({stamp:%b} {stamp.day}, "
                                                          f"{stamp:%H:%M}). Your earlier answer was to keep it out, so it needs your call again.")
        except Exception as e:
            reopened = {"error": str(e)}
    return jsonify(dict(payload(), key=key, text=text, reopened=reopened))


def register(app):
    app.register_blueprint(bp)
    if probing():                                       # warm the probes, so the tab's first read does not wait on them
        threading.Thread(target=lambda: (screen_off(), watchdog_running()), daemon=True).start()
