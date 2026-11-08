"""One entry point for the editor suite's background helpers.

    python tools/photo_suite.py            start what isn't running, open the editor
    python tools/photo_suite.py status     what is up
    python tools/photo_suite.py stop       stop everything the suite started
    python tools/photo_suite.py keep       the keeper: restart a server that died (start runs it)
    python tools/photo_suite.py builds     which servers run the code on disk (what `start` acts on)
    python tools/photo_suite.py site       preview the live site (the photo server
                                           serves the whole repo at /site/)

`Editor Suite.cmd` in the repo root is a one-line wrapper around this, and
tools/photo_editor.py reports the same status at /api/suite for the Live
Activity panel in editor.html.

The helpers:
  * photo server  (port 5003)  photos + library + picks for editor.html
  * hero picker   (port 5004)  choosing hero banners, opens from the editor
  * map editor    (port 5002)  moving labels on route and city maps
  * keeper        (port 5009)  restarts any of the three servers that dies (see keep())

Every helper runs under pythonw with -u and its output appended to
~/Backup/_meta/<name>.log|.err: under a bare pythonw a crash is SILENT, and
when the server dies every photo in the editor just stops loading.

The shared-album backup watcher (photo_backup.py) and the iCloud sync fix were
removed on 2026-10-12: albums come from icloud.com downloads
(tools/ingest_icloud_download.py), so nothing copies in the background.
"""
import json
import re
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META = Path.home() / "Backup" / "_meta"
sys.path.insert(0, str(ROOT / "tools"))
import build_id            # noqa: E402  (the code fingerprint each server reports at /api/build)

# The helpers run as python.exe with an INVISIBLE console (CREATE_NO_WINDOW), not as pythonw.
# pythonw has no console at all, and Windows then gives every console program it starts (git,
# PowerShell, node) a new VISIBLE console window: "a command window opens every time I open"
# (Kevin, 2026-10-05). Under a hidden console, children share it and nothing ever shows.
_pyc = Path(sys.executable).with_name("python.exe")
PYW = str(_pyc if _pyc.exists() else Path(sys.executable))   # (name kept: every launch site uses it)

CREATE_NO_WINDOW = 0x08000000

# The safety net: while this file runs windowless (the shortcut's pythonw), anything it starts without
# saying otherwise gets CREATE_NO_WINDOW. A bare subprocess.run(["git", ...]) in the tidy-up preview still
# opened a "git.exe" window after the first fix (2026-10-05); this way the next one cannot.
if sys.platform == "win32" and Path(sys.executable).name.lower() == "pythonw.exe":
    _popen_init = subprocess.Popen.__init__

    def _windowless_init(self, *a, **kw):
        if not kw.get("creationflags"):
            kw["creationflags"] = CREATE_NO_WINDOW
        _popen_init(self, *a, **kw)
    subprocess.Popen.__init__ = _windowless_init

DETACHED = 0x08000000 | 0x00000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP (was DETACHED_PROCESS:
                                            # no console at all, which is what made the windows appear)

SERVICES = {
    # FIRST, so `stop` takes the keeper down before the servers it would otherwise bring back.
    # It holds port 5009 only as a lock (one keeper at a time) and serves nothing.
    "keeper":  {"label": "keeper",         "port": 5009,
                "args": ["-u", "tools/photo_suite.py", "keep"],    "log": "keeper"},
    "server":  {"label": "photo server",   "port": 5003,
                "args": ["-u", "tools/photo_editor.py"],            "log": "editor"},
    "heroes":  {"label": "covers",         "port": 5004,
                "args": ["-u", "tools/hero_picker.py"],             "log": "heroes"},
    # the map label editor used to be launched by hand and killed after; it is part of the
    # suite now because the article editor's Edit button on a map opens it in place
    "maps":    {"label": "map editor",     "port": 5002,
                "args": ["-u", "tools/map_editor.py"],              "log": "maps"},
}


def _ps(script, timeout=20):
    """Run a PowerShell one-liner windowless and return its stdout."""
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, text=True, timeout=timeout,
                       creationflags=CREATE_NO_WINDOW)
    return r.stdout


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_answers(port, timeout=5.0):
    """Does the server answer HTTP at all, with any status? A TCP connect proves nothing: Windows completes
    it even when the server app answers nothing (stress test 2026-10-09). The path is one no server has,
    so the answer is a cheap 404 that touches nothing."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as c:
            c.settimeout(timeout)
            c.sendall(b"GET /__keeper HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            got = b""
            while len(got) < 5:
                more = c.recv(64)
                if not more:
                    break
                got += more
            return got.startswith(b"HTTP/")
    except OSError:
        return False


def port_pids(port):
    """PIDs listening on a local port. netstat, not Get-NetTCPConnection: on this box that took 9 s
    warm and often hit the 20 s timeout, which made `stop` raise halfway through (QA 2026-10-08)."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             timeout=15, creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids = set()
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0] == "TCP" and f[3] == "LISTENING" and f[1].rsplit(":", 1)[-1] == str(port):
            if f[4].isdigit() and f[4] != "0":
                pids.add(int(f[4]))
    if not pids and port_open(port):
        # netstat showed no owner for an open port (a sandboxed session sees LISTENING rows with pid 0, 2026-11-07):
        # the process this launcher started on that port, if it still runs, is the one
        pid = _known_pids.get(port)
        if pid and _alive(pid):
            pids.add(pid)
    return sorted(pids)


_known_pids = {}            # port -> pid of the process start() put there (this process's own launches only)


def is_up(name):
    return port_open(SERVICES[name]["port"])


def start(name):
    """Start one helper unless it is already up. Returns True when started."""
    if is_up(name):
        return False
    s = SERVICES[name]
    META.mkdir(parents=True, exist_ok=True)
    for ext in ("log", "err"):
        _rotate(META / f"{s['log']}.{ext}")
    out = open(META / f"{s['log']}.log", "ab")
    err = open(META / f"{s['log']}.err", "ab")
    p = subprocess.Popen([PYW] + s["args"], cwd=str(ROOT), stdout=out, stderr=err,
                         stdin=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)
    _known_pids[s["port"]] = p.pid
    return True


def _rotate(path, limit=20 * 1024 * 1024):
    """A server's .err holds every request line and only grew: the photo server's reached 59 MB (stress
    test 2026-10-09). Past 20 MB it becomes <name>.1 when the server next starts; one old copy is kept."""
    try:
        if path.exists() and path.stat().st_size > limit:
            old = path.with_name(path.name + ".1")
            if old.exists():
                old.unlink()
            path.rename(old)
    except OSError:
        pass


def _kill(pids):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)          # TerminateProcess on Windows
        except OSError:
            pass


def stop_all():
    """Stop everything the suite started (not iCloud itself)."""
    stopped = {}
    for name, s in SERVICES.items():
        try:                    # one slow lookup must not leave the rest running
            pids = port_pids(s["port"])
        except Exception:
            pids = []
        _kill(pids)
        stopped[name] = pids
    return stopped


def status():
    # an HTTP answer, not a TCP connect: Windows completes the connect even when the app answers nothing, so a
    # hung server showed as up in Live Activity (stress test 2026-10-23). The keeper has no HTTP; its lock port stays.
    return {"server": http_answers(5003, 2.0), "heroes": http_answers(5004, 2.0), "maps": http_answers(5002, 2.0),
            "keeper": port_open(5009)}


# ---------------------------------------------------------------- current code, every launch
# Kevin opens the editor from the shortcut and nothing else; a server left running since last week kept
# serving last week's code, and the new Queue tab told him "the photo server needs a restart" (2026-11-07).
# Now every server reports a fingerprint of its code at /api/build (tools/build_id.py) and the launcher
# compares it with the files on disk. A stale server is restarted on the spot when it is idle; one with
# work under way (a compress run, a job, a save in flight) is left alone and marked pending in launch.json,
# and the keeper restarts it the moment it reports idle. What happened lands in launch.json (shown in the
# editor's Live Activity through /api/suite) and in suite.log.
LAUNCH_STATE = META / "launch.json"
SUITE_LOG = META / "suite.log"
RESTART_WAIT = 20            # seconds the launcher gives a restarted server to answer HTTP again before it opens the editor
_launch_cache = {"key": None, "v": {}}


def suite_log(line):
    """one timestamped line in ~/Backup/_meta/suite.log (the launcher runs under pythonw, where print goes nowhere)"""
    try:
        META.mkdir(parents=True, exist_ok=True)
        _rotate(SUITE_LOG, 5 * 1024 * 1024)
        with open(SUITE_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def launch_state():
    """launch.json as a dict ({} without one): at, lines, pending {service: {since, why}}. Cached by mtime: the
    photo server hands it to the editor on every /api/suite poll."""
    try:
        st = LAUNCH_STATE.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _launch_cache["key"] != key:
        try:
            v = json.loads(LAUNCH_STATE.read_text(encoding="utf-8"))
            _launch_cache["v"] = v if isinstance(v, dict) else {}
        except (OSError, ValueError):
            _launch_cache["v"] = {}
        _launch_cache["key"] = key
    return _launch_cache["v"]


def _launch_write(state):
    try:
        META.mkdir(parents=True, exist_ok=True)
        tmp = LAUNCH_STATE.with_name("launch.json.tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(tmp, LAUNCH_STATE)
    except OSError:
        pass


def entry_of(name):
    """the script a service runs (tools/photo_editor.py for the photo server, tools/photo_suite.py for the keeper)"""
    return next((a for a in SERVICES[name]["args"] if a.endswith(".py")), None)


def disk_build(name):
    """the fingerprint of the service's code as it is on disk right now"""
    e = entry_of(name)
    return build_id.fingerprint(ROOT / e)["build"] if e else None


def code_age(name):
    """seconds since the newest change to any file in the service's code (None without an entry)"""
    e = entry_of(name)
    if not e:
        return None
    try:
        newest = max(p.stat().st_mtime for p in build_id.files_of(ROOT / e))
    except (ValueError, OSError):
        return None
    return time.time() - newest


def _http_json(port, path, timeout=3.0, body=None):
    """GET (or POST a JSON body to) a local server. Returns (status, dict|None); status 0 when it did not answer."""
    import urllib.request
    import urllib.error
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, None
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None
    except Exception:
        return 0, None


def build_info(port, timeout=3.0):
    """what a running server says about itself. None when it does not answer HTTP; {"missing": True} when it
    answers but has no /api/build (a build from before the fingerprint existed)."""
    status, d = _http_json(port, "/api/build", timeout)
    if status == 0:
        return None
    if status != 200 or not isinstance(d, dict) or not d.get("build"):
        return {"missing": True}
    return d


def legacy_busy(name, port):
    """for a server from before /api/build: is it working? The photo server has job and compress routes to ask;
    Covers and the map editor have none, and their work is short, so they read as idle."""
    if name != "server":
        return []
    out = []
    st, jobs = _http_json(port, "/api/jobs", 4.0)
    if st == 200 and isinstance(jobs, dict):
        for j in jobs.get("jobs", []):
            if j.get("status") in ("queued", "running"):
                out.append("%s job: %s" % (j.get("kind", "?"), j.get("label", "")))
    elif st != 404:
        out.append("could not ask about its jobs")
    st, suite = _http_json(port, "/api/suite", 4.0)
    if st == 200 and isinstance(suite, dict):
        run = (suite.get("compress") or {}).get("running")
        if run:
            out.append("compressing " + str(run).split("/")[-1])
    elif st != 404:
        out.append("could not ask about the compress queue")
    st, alt = _http_json(port, "/api/alt_status", 4.0)
    if st == 200 and isinstance(alt, dict) and alt.get("busy"):
        out.append("alt text being written")
    return out


def check_server(name):
    """{"up", "answers", "stale", "missing", "live", "disk", "busy": [...]} for one service"""
    port = SERVICES[name]["port"]
    out = {"name": name, "port": port, "up": port_open(port), "answers": False, "stale": False, "missing": False,
           "live": None, "disk": None, "busy": []}
    if not out["up"]:
        return out
    info = build_info(port)
    if info is None:
        return out                                    # listening, not answering: the keeper's business, not a restart
    out["answers"] = True
    try:
        out["disk"] = disk_build(name)
    except Exception:
        return out                                    # a half-written file on disk: say nothing, restart nothing
    if info.get("missing"):
        out["missing"] = out["stale"] = True
        out["busy"] = legacy_busy(name, port)
    else:
        out["live"] = info.get("build")
        out["stale"] = out["live"] != out["disk"]
        out["busy"] = list(info.get("busy") or [])
    return out


def restart_server(name, check, log=print, wait=RESTART_WAIT):
    """Stop a server (asked to leave through /api/build when it has the route, else TerminateProcess like `stop`),
    start it fresh and wait for it to answer HTTP. "back" when it answers again, "started" when it was started but
    has not answered within `wait`, None when the old one could not be stopped (nothing was started)."""
    port = SERVICES[name]["port"]
    if check.get("live"):
        st, d = _http_json(port, "/api/build", 5.0, {"action": "exit", "build": check["live"]})
        if not (st == 200 and isinstance(d, dict) and d.get("exiting")):
            why = (d or {}).get("busy") or (d or {}).get("error") or ("no answer" if st == 0 else "status %s" % st)
            log("%s: did not leave when asked (%s); left running" % (SERVICES[name]["label"], why))
            return None
    else:
        pids = [pid for pid in port_pids(port) if _alive(pid)]
        if not pids:
            log("%s: no process found behind port %d; left as it is" % (SERVICES[name]["label"], port))
            return None
        _kill(pids)
    for _ in range(60):                               # the old one lets go of the port
        if not port_open(port):
            break
        time.sleep(0.1)
    else:
        log("%s: the old process is still holding port %d; left as it is" % (SERVICES[name]["label"], port))
        return None
    start(name)
    t0 = time.time()
    while time.time() - t0 < wait:
        if http_answers(port, 2.0):
            return "back"
        time.sleep(0.25)
    log("%s: restarted but not answering yet after %d s (the keeper watches it)" % (SERVICES[name]["label"], wait))
    return "started"


def refresh_stale(log=print, restart=True):
    """The launcher's pass over the servers that are already up: restart the ones running old code when they are
    idle, mark the busy ones pending for the keeper. Returns the launch state it wrote."""
    lines, pending = [], {}
    prev = launch_state().get("pending") or {}
    for n in KEPT:
        label = SERVICES[n]["label"]
        try:
            c = check_server(n)
        except Exception as e:
            lines.append("%s: check failed (%s)" % (label, e)); continue
        if not c["up"]:
            continue                                  # start_all starts it, on the current code
        if not c["answers"]:
            lines.append("%s: listening but not answering; left to the keeper" % label); continue
        if not c["stale"]:
            lines.append("%s: current (%s)" % (label, c["disk"])); continue
        what = "from before the fingerprint" if c["missing"] else "%s, disk %s" % (c["live"], c["disk"])
        if c["busy"]:
            since = (prev.get(n) or {}).get("since") or time.strftime("%Y-%m-%d %H:%M")
            pending[n] = {"since": since, "why": "; ".join(c["busy"])[:200]}
            lines.append("%s: running old code (%s) but busy: %s. It restarts by itself once that finishes."
                         % (label, what, "; ".join(c["busy"])))
            continue
        if not restart:
            lines.append("%s: running old code (%s)" % (label, what)); continue
        res = restart_server(n, c, log=lambda s: lines.append(s))
        if res:
            lines.append("%s: restarted on the current code (%s)%s" % (label, what, "" if res == "back" else "; it has not answered yet"))
        else:
            lines.append("%s: still running old code (%s)" % (label, what))
    # the keeper itself runs photo_suite.py; its build is in keeper.json. It holds no work, so a stale one is restarted.
    try:
        if port_open(SERVICES["keeper"]["port"]):
            kb = _keeper_meta().get("build")
            want = disk_build("keeper")
            if kb != want:
                _kill([pid for pid in port_pids(SERVICES["keeper"]["port"]) if _alive(pid)])
                for _ in range(40):
                    if not port_open(SERVICES["keeper"]["port"]):
                        break
                    time.sleep(0.1)
                lines.append("keeper: restarted on the current code (%s -> %s)" % (kb or "from before the fingerprint", want))
    except Exception as e:
        lines.append("keeper: check failed (%s)" % e)
    state = {"at": time.strftime("%Y-%m-%d %H:%M"), "lines": lines, "pending": pending}
    _launch_write(state)
    for l in lines:
        suite_log("launch: " + l)
        log(l)
    return state


def _keeper_meta():
    try:
        d = json.loads(KEEPER_STATE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- the keeper
# A dead photo server used to stay dead until Kevin noticed: every photo in the editor stopped
# loading and a red banner told him to double-click a .cmd and reload. The keeper checks the
# three servers every KEEP_EVERY seconds and starts one that is down.
#  * Two misses in a row before it acts: a session restarting a server by hand (kill the port's
#    pid, start it again) is back within a few seconds, and the keeper must not race it.
#  * Three restarts of one server inside ten minutes means it is crashing on start (a session
#    halfway through editing it, say). It is then left alone for ten minutes instead of looping.
#  * `stop` kills the keeper first (it is first in SERVICES), so a stop stays stopped.
# Every restart is a line in ~/Backup/_meta/keeper.log and an entry in keeper.json with the tail
# of the server's .err log, which is where the cause of the crash is.
KEPT = ("server", "heroes", "maps")
KEEP_EVERY = 10
# After a restart, this long before the port is checked again. A server that takes longer than two
# checks to bind looked dead again, and the keeper started a second and a third copy; Windows lets
# them all bind the same port, and the newest takes the connections (QA 2026-10-08). The photo
# server binds in about a second; 60 s is room for a slow disk.
KEEP_GRACE = 60
# A server that does not answer but is still LISTENING is busy or hung, not dead. Starting a second
# copy then would bind the same port (Windows allows it) and take the connections with none of the
# first one's state. It is left alone. It is stopped and started fresh only when it has not answered
# HTTP for KEEP_HUNG while ANOTHER server answered in the last 90 s.
# Why both: the stress test (2026-10-09) found that a burst of connections freezes ALL loopback HTTP
# on this machine for 4 to 5 minutes (Norton's network filter is the suspect). Every server goes quiet
# at once and comes back by itself. A server that is silent alone, for 10 minutes, is really stuck.
KEEP_HUNG = 600
KEEP_HTTP_EVERY = 3          # rounds between HTTP probes (one 404 line in each server's log per probe)
# Restarts count toward the ten-minute hold only when the server died soon after the keeper started
# it. A server that ran for an hour and was then killed is not crashing on start.
KEEP_QUICK = 150
# Covers and the map editor are restarted for a code change once their files have been quiet this long
KEEP_CODE_QUIET = 600
KEEPER_STATE = META / "keeper.json"


def _err_tail(name, n=8):
    try:
        with open(META / f"{SERVICES[name]['log']}.err", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 6000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    # the request log shares the .err file; the crash is in what is not a request line
    return [l for l in lines if '" 200 -' not in l and '" 304 -' not in l and "/__keeper" not in l][-n:]


def _alive(pid):
    """Is this process still running? netstat lists a killed server's socket for a moment after the
    kill, and the keeper must not take that for a busy one. (Never os.kill(pid, 0) on Windows: any
    signal but the two console ones TERMINATES the process.)"""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def keeper_restarts():
    """Restarts the keeper has made, newest last ([] if none or no file)."""
    try:
        import json
        return json.loads(KEEPER_STATE.read_text(encoding="utf-8")).get("restarts", [])
    except (OSError, ValueError):
        return []


def _keeper_note(entry):
    restarts = (keeper_restarts() + [entry])[-50:]
    _keeper_state_update(restarts=restarts)
    suite_log("keeper: %s %s" % (entry["service"], entry["what"]))
    print(time.strftime("%Y-%m-%d %H:%M:%S"), entry["service"], entry["what"], flush=True)


def _keeper_state_update(**fields):
    """keeper.json keeps the restarts AND the running keeper's own build/pid (the launcher restarts a keeper whose
    code changed, since it holds no work)"""
    d = _keeper_meta()
    d.update(fields)
    try:
        META.mkdir(parents=True, exist_ok=True)
        KEEPER_STATE.write_text(json.dumps(d, indent=1), encoding="utf-8")
    except OSError:
        pass


def keep(every=KEEP_EVERY, rounds=None):
    """Run the keeper (blocks). Returns False at once if another keeper holds the lock.
    `rounds` limits the loop for tests."""
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", SERVICES["keeper"]["port"]))
        lock.listen(5)
    except OSError:
        lock.close()
        return False
    # status() and the suite panel probe the lock port to see the keeper; a connection nobody accepts
    # sits in the backlog and the probes after it time out, so the keeper read as down. Accept and
    # close each one as it comes.
    # The accept waits at most half a second, so the keeper can stop it: a close() on Windows does not
    # break a blocked accept(), and the port stayed taken after the keeper had finished.
    import threading
    stop = threading.Event()
    lock.settimeout(0.5)

    def _answer():
        while not stop.is_set():
            try:
                c, _a = lock.accept()
                c.close()
            except socket.timeout:
                continue
            except OSError:
                return
    answerer = threading.Thread(target=_answer, daemon=True)
    answerer.start()
    misses = {n: 0 for n in KEPT}
    recent = {n: [] for n in KEPT}
    hold = {n: 0.0 for n in KEPT}
    grace = {n: 0.0 for n in KEPT}
    t0 = time.time()
    answered = {n: t0 for n in KEPT}              # silence runs from here: keeper start, a restart, an answer
    heard = {n: 0.0 for n in KEPT}                # the last REAL answer
    started_at = {n: 0.0 for n in KEPT}           # the last time the keeper started it
    noted = {n: False for n in KEPT}              # "busy, left running" is said once per spell
    # Servers running old code (their /api/build fingerprint differs from the files on disk) are restarted here
    # when they are idle, in two cases:
    #  * the map editor and Covers, once their code has been quiet for KEEP_CODE_QUIET (stress test 2026-10-23: :5002
    #    ran code from the 16th for a week and its Save embedded a degraded map; a restart ten minutes after the last
    #    edit cuts nothing off);
    #  * ANY server the launcher found stale but busy (launch.json "pending"): the photo server with a compress run or
    #    a job under way when Kevin clicked the shortcut (2026-11-07). The moment it reports idle it is restarted, and
    #    the launch lines say so for the editor's Live Activity. The photo server is otherwise never restarted for a
    #    code change alone: a session edits photo_editor.py while Kevin writes, and the shortcut is his moment.
    try:
        _keeper_state_update(build=disk_build("keeper"), pid=os.getpid(), started=time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass
    CODE_QUIET = ("maps", "heroes")
    code_said = {n: 0.0 for n in KEPT}             # when "stale but busy" was last noted (once an hour, not once a round)

    def code_check(n, now):
        """restart n if it runs old code and may be restarted now; True when it was"""
        try:
            c = check_server(n)
        except Exception:
            return False
        if not c["answers"] or not c["stale"]:
            return False
        pend = (launch_state().get("pending") or {})
        due = n in pend
        if not due and n in CODE_QUIET:
            age = code_age(n)
            due = age is not None and age > KEEP_CODE_QUIET
        if not due:
            return False
        if c["busy"]:
            if now - code_said[n] > 3600:
                code_said[n] = now
                _keeper_note({"service": n, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "what": "runs old code, waiting for it to finish: " + "; ".join(c["busy"])[:200], "err": []})
            return False
        res = restart_server(n, c, log=lambda s: print(time.strftime("%Y-%m-%d %H:%M:%S"), s, flush=True), wait=KEEP_GRACE)
        if not res:
            if now - code_said[n] > 3600:
                code_said[n] = now
                _keeper_note({"service": n, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "what": "runs old code and could not be stopped; left as it is", "err": []})
            return False
        what = "restarted for new code (%s -> %s)%s" % (c["live"] or "pre-fingerprint", c["disk"], "" if res == "back" else "; not answering yet")
        _keeper_note({"service": n, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "what": what, "err": [] if res == "back" else _err_tail(n)})
        st = dict(launch_state())
        if n in (st.get("pending") or {}):
            st["pending"] = {k: v for k, v in st["pending"].items() if k != n}
            st["lines"] = (st.get("lines") or []) + ["%s: %s at %s (the keeper)" % (SERVICES[n]["label"], what, time.strftime("%H:%M"))]
            _launch_write(st)
        return True

    def stuck(n, now):
        """silent over HTTP for KEEP_HUNG while another server answered lately: this server, not the box"""
        return now - answered[n] >= KEEP_HUNG and any(now - heard[m] < 90 for m in KEPT if m != n)

    def stop_stuck(n, pids, now):
        _kill(pids)
        time.sleep(1)
        _keeper_note({"service": n, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "what": "no answer for %d s while the other servers answered: stopped pid %s"
                              % (now - answered[n], pids),
                      "err": _err_tail(n)})

    def live_pids(n):
        try:
            return [pid for pid in port_pids(SERVICES[n]["port"]) if _alive(pid)]
        except Exception:
            return []

    print(time.strftime("%Y-%m-%d %H:%M:%S"), "keeper up, watching", ", ".join(KEPT), flush=True)
    i = 0
    while rounds is None or i < rounds:
        i += 1
        probe = (i - 1) % KEEP_HTTP_EVERY == 0
        for n in KEPT:
            now = time.time()
            port = SERVICES[n]["port"]
            if probe and now >= hold[n] and now >= grace[n] and port_open(port) and code_check(n, now):
                now = time.time()
                started_at[n] = now
                answered[n] = now + KEEP_GRACE
                grace[n] = now + KEEP_GRACE
                misses[n] = 0
                continue
            if port_open(port):
                misses[n] = 0
                grace[n] = 0.0
                if not probe:
                    continue
                if http_answers(port):
                    answered[n] = heard[n] = time.time()
                    noted[n] = False
                    continue
                now = time.time()
                if now < hold[n] or not stuck(n, now):
                    continue
                pids = live_pids(n)
                if not pids:
                    continue
                stop_stuck(n, pids, now)                # then started fresh, below
            else:
                if now < grace[n]:
                    continue                            # just started, may still be binding
                misses[n] += 1
                if misses[n] < 2 or now < hold[n]:
                    continue
                pids = live_pids(n)
                if pids:
                    if not noted[n]:
                        noted[n] = True
                        _keeper_note({"service": n, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                      "what": "not answering but still listening (pid %s): left running" % pids,
                                      "err": []})
                    if not stuck(n, now):
                        continue
                    stop_stuck(n, pids, now)
            recent[n] = [t for t in recent[n] if now - t < 600]
            if started_at[n] and now - started_at[n] < KEEP_QUICK:
                recent[n].append(now)                   # it died soon after the keeper started it
            if len(recent[n]) >= 3:
                hold[n] = now + 600
                recent[n] = []
                started_at[n] = 0.0
                _keeper_note({"service": n, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "what": "keeps crashing on start: left alone for 10 minutes",
                              "err": _err_tail(n)})
                continue
            err = _err_tail(n)
            try:
                started = start(n)
            except Exception as e:                       # the keeper must outlive a failed start
                started = False
                err = err + ["start failed: %s" % e]
            started_at[n] = now
            answered[n] = now + KEEP_GRACE              # time to come up before silence counts
            noted[n] = False
            misses[n] = 0
            grace[n] = now + KEEP_GRACE
            _keeper_note({"service": n, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                          "what": "restarted" if started else "was back before the restart",
                          "err": err})
        time.sleep(every)
    stop.set()
    answerer.join(timeout=2)
    lock.close()
    return True


CHROME = next((p for p in (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
) if p.is_file()), None)

# The editor keeps opening from file:// (not the :5003 copy): its settings and
# presets live in that origin's localStorage, and Kevin has always used it
# that way. Same URL to bookmark in Chrome.
# #editor on the end: the page remembers the last view in localStorage, so a session that
# ended in the Photo Library reopened there from the shortcut. The hash outranks the
# memory, and the shortcut is the article editor (Kevin, 2026-09-26).
EDITOR_URL = (ROOT / "editor.html").as_uri() + "#editor"


def open_editor():
    """Open the editor as a normal Chrome tab (Kevin keeps research tabs
    alongside it, so a separate app window got in the way). Falls back to
    the default browser without Chrome."""
    if CHROME:
        subprocess.Popen([str(CHROME), EDITOR_URL], creationflags=DETACHED, close_fds=True)
    else:
        os.startfile(str(ROOT / "editor.html"))


def autofix_preview(log=print):
    """Say what tools/autofix.py would tidy, on the way into the editor.

    This is the local answer to "should the routine fix things automatically". It could have
    gone in the cloud routine, but that runs from a fresh clone, so for a fix to survive it
    would have to PUSH -- an unattended write to the live site every two days. Here Kevin is
    already sitting at the machine, the repo is his working copy, and every fix is a local
    commit he can read and revert before anything is pushed.

    Deliberately a DRY RUN. It reports and prints the one command that applies it; nothing is
    written by opening the editor. It also never blocks the launch: a failure here is a line
    of log, not a reason the editor does not open.

    The lines also go to ~/Backup/_meta/autofix.txt, because under the shortcut this runs
    in pythonw and `print` goes nowhere: the preview had no visible output at all on the
    one path Kevin actually launches from. The photo server hands that file to the editor's
    Live Activity panel, which is where he will see it.
    """
    lines = []
    _log = log

    def log(s):
        lines.append(s)
        _log(s)

    try:
        _autofix_preview(log)
    finally:
        try:
            META.mkdir(parents=True, exist_ok=True)
            (META / "autofix.txt").write_text("\n".join([time.strftime("%Y-%m-%d %H:%M")] + lines),
                                              encoding="utf-8")
        except OSError:
            pass


def _autofix_preview(log):
    try:
        r = subprocess.run([PYW, str(ROOT / "tools" / "autofix.py"), "--dry-run"],
                           cwd=str(ROOT), capture_output=True, timeout=240, creationflags=CREATE_NO_WINDOW)
        out = (r.stdout or b"").decode("utf-8", "replace")
        # Each fixer prints its own tally, and a tally of zero is not work. Counting the
        # CLASSES it ran and calling that "would tidy 4 things" was the first version, which
        # announced work on a clean tree every single launch -- the same crying-wolf the
        # routines are built to avoid.
        hits, needs_person = [], []
        for l in out.splitlines():
            t = l.strip()
            if t.startswith("links needing a person:"):
                # NOT something autofix tidies -- a dead link is Kevin's call, and
                # listing it under "would tidy" would promise a fix that is never
                # coming. It gets its own line below.
                needs_person.append(t)
                continue
            if re.match(r"^(would change|changed)\s+0\b", t) or re.match(r"^prose issues:\s*0\b", t):
                continue                            # an explicit nothing
            if re.search(r"\.html\b", t) or re.match(r"^(would change|changed)\s+\d", t) \
               or re.match(r"^prose issues:\s*[1-9]", t):
                hits.append(t)
        if not hits and not needs_person:
            log("autofix: nothing to tidy")
            return
        if not hits:
            for t in needs_person:
                log(t)
            return
        log("autofix would tidy:")
        for h in hits[:6]:
            log("   " + h[:110])
        if len(hits) > 6:
            log("   ... and %d more" % (len(hits) - 6))
        # A dry run deliberately skips autofix's dirty-tree guard, because it writes nothing.
        # The APPLY does not, so printing the command while the tree is dirty would hand over
        # a command that is about to refuse. Say what has to happen first instead.
        dirty = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=str(ROOT),
                               capture_output=True, text=True, timeout=60,
                               creationflags=CREATE_NO_WINDOW).stdout.splitlines()
        if dirty:
            log("   to apply, commit or stash these first: " + ", ".join(dirty[:3])
                + (" +%d more" % (len(dirty) - 3) if len(dirty) > 3 else ""))
        else:
            log("   apply with:  python tools/autofix.py")
        for t in needs_person:
            log(t)
    except Exception as e:
        log(f"autofix preview skipped: {e}")


def refresh_link_scan(log=print):
    """Re-resolve the outbound links in the background, for NEXT launch.

    fix_links.py reads a cached scan so the preview above costs nothing. Something
    has to fill that cache, and it cannot be the preview: ~400 HTTP requests is
    three or four minutes, and nothing that slow belongs in front of the editor
    opening. So it is kicked off detached here and nobody waits for it -- this
    launch reports on yesterday's scan, and the one after reports on today's.
    """
    try:
        META.mkdir(parents=True, exist_ok=True)
        # One scan at a time. Every click on the shortcut used to start another one, and
        # three ran side by side today, each writing the same cache file and interleaving
        # in the log. A live pid in the lock means one is running; a stale pid means the
        # last one died without cleaning up, and that must not block scans for ever.
        lock = META / "linkscan.pid"
        try:
            pid = int(lock.read_text().strip())
            if _ps(f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).Id").strip() == str(pid):
                return
        except (OSError, ValueError):
            pass
        out = open(META / "linkscan.log", "ab")
        p = subprocess.Popen([PYW, "-u", "tools/fix_links.py", "--scan"], cwd=str(ROOT),
                             stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=DETACHED, close_fds=True)
        lock.write_text(str(p.pid))
    except Exception as e:                          # never block the launch on this
        log(f"link scan not started: {e}")


def start_all(open_editor_window=True, log=print):
    # servers already up run the code they were started with; the ones behind the files on disk are restarted
    # first (idle) or marked for the keeper (busy), so the editor that opens below talks to current code
    try:
        refresh_stale(log=log)
    except Exception as e:                          # never block the launch on this
        log(f"code check skipped: {e}")
    for name, s in SERVICES.items():
        log(f"Starting {s['label']}..." if start(name) else f"{s['label']} already running.")
    if open_editor_window:
        # The editor opens from file://, where a browser may serve an unversioned script
        # from cache: edit review.js, reload, and still be running the old one. Restamping
        # the content hashes here means the desktop shortcut always picks up a code change.
        try:
            r = subprocess.run([PYW, str(ROOT / "tools" / "stamp_assets.py")],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=30,
                               creationflags=CREATE_NO_WINDOW)
            for line in r.stdout.splitlines():
                if "->" in line or "restamped" in line:
                    log(line.strip())
        except Exception as e:                      # never block the launch on a stamp
            log(f"asset stamp skipped: {e}")
        # give the server a moment so the first thumbnails don't 404
        for _ in range(20):
            if port_open(5003):
                break
            time.sleep(0.25)
        # THE EDITOR OPENS BEFORE THE PREVIEW, not after. The shortcut runs this under
        # pythonw, with no console, and the first version ran the preview first: two
        # minutes of nothing on screen, which from a double-click is indistinguishable
        # from "the shortcut is broken" (2026-09-25). Only the asset stamp has to
        # precede the browser, and that is a second.
        open_editor()
        autofix_preview(log)
        refresh_link_scan(log)


def main(argv):
    cmd = (argv[1] if len(argv) > 1 else "start").lower()
    if cmd == "start":
        start_all()
        print("Ready. The photo library, picks and hero picker live inside the editor.")
    elif cmd == "status":
        for k, v in status().items():
            print(f"{k:12} {v}")
    elif cmd == "site":
        # what serve.ps1 used to do with its own listener on :8080; the photo
        # server already serves every file in the repo under /site/
        start("server")
        for _ in range(20):
            if port_open(5003):
                break
            time.sleep(0.25)
        os.startfile("http://127.0.0.1:5003/site/index.html")
    elif cmd == "keep":
        if not keep():
            print("A keeper is already running.")
    elif cmd == "builds":
        for n in KEPT:
            c = check_server(n)
            state = ("down" if not c["up"] else "not answering" if not c["answers"] else
                     "pre-fingerprint build, STALE" if c["missing"] else
                     "STALE (%s, disk %s)" % (c["live"], c["disk"]) if c["stale"] else "current (%s)" % c["disk"])
            print(f"{SERVICES[n]['label']:14} {state}" + (f"   busy: {'; '.join(c['busy'])}" if c["busy"] else ""))
        kb = _keeper_meta().get("build")
        print(f"{'keeper':14} " + ("down" if not port_open(SERVICES['keeper']['port']) else
                                  "current (%s)" % kb if kb == disk_build("keeper") else "STALE (%s, disk %s)" % (kb or "pre-fingerprint", disk_build("keeper"))))
        pend = launch_state().get("pending") or {}
        if pend:
            print("pending restarts:", ", ".join("%s (%s)" % (SERVICES[k]["label"], v.get("why")) for k, v in pend.items()))
    elif cmd == "stop":
        for name, pids in stop_all().items():
            print(f"{SERVICES[name]['label']}: stopped {pids or 'nothing'}")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv)
