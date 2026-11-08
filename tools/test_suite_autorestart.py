"""The editor shortcut gives Kevin servers running the CURRENT code (tools/build_id.py, photo_suite.refresh_stale, the
keeper's deferred restarts; 2026-11-07). Throwaway ports and a scratch META folder only; the real suite on
5002/5003/5004/5009 is never touched.

  A  the fingerprint: stable, changes with any file in the set, follows imports into tools/ (lazy ones too)
  B  a stale idle server is restarted by the launcher and comes back on the new code; launch.json + suite.log say so
  C  a stale BUSY server is left running and marked pending; the keeper restarts it the moment it reports idle
  D  a POST in flight counts as busy; an exit asked with the wrong build is refused (409) and nothing exits
  E  a pre-fingerprint server (no /api/build): restarted when its job routes say idle, left + pending when they say busy
  F  a server that is down or listening-but-silent is never restarted here; a stale keeper is restarted
  G  the real photo server (PHOTO_EDITOR_NO_WORKER=1, a test port): /api/build == the disk build, /api/suite carries
     `launch`, the exit action takes it down
  H  editor.html: the pending notice, then "The editor was updated."; the Launch card in Live Activity

    python tools/test_suite_autorestart.py
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_id
import photo_suite as ps

checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond or not info else "  :: " + str(info)[:500]), flush=True)


def getj(port, path, timeout=4):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as r:
        return json.loads(r.read())


def wait_http(port, t=30):
    end = time.time() + t
    while time.time() < end:
        if ps.http_answers(port, 2.0):
            return True
        time.sleep(0.2)
    return False


def wait_closed(port, t=10):
    end = time.time() + t
    while time.time() < end:
        if not ps.port_open(port):
            return True
        time.sleep(0.1)
    return False


TMP = ROOT / ".tmp" / "autorestart-test"
P_FAKE, P_LEGACY, P_KEEPER, P_REAL = 5036, 5037, 5039, 5038


def kill_test_procs():
    """every python this harness (or an earlier crashed run of it) left behind: the fake servers by their command line
    (it names this folder), the test photo server by the pid written to TMP/real.pid. Found this way because a
    sandboxed session's netstat shows no owner for a listening port, so port_pids cannot see them. Nothing else is
    ever matched: the live suite's command lines name neither."""
    script = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -match '%s' } "
              "| ForEach-Object { $_.ProcessId }" % TMP.as_posix().replace("/", "[\\\\/]"))
    pids = []
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=40,
                             creationflags=ps.CREATE_NO_WINDOW).stdout
        pids += [int(t) for t in out.split() if t.isdigit()]
    except Exception:
        pass
    try:
        pids.append(int((TMP / "real.pid").read_text().strip()))
    except (OSError, ValueError):
        pass
    ps._kill([p for p in pids if p != os.getpid() and ps._alive(p)])


kill_test_procs()
time.sleep(0.5)
if TMP.exists():
    shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)
for p in (P_FAKE, P_LEGACY, P_REAL):
    assert not ps.port_open(p), "port %d is taken by something that is not this harness; stop it first" % p

# a tiny suite server built on build_id: busy = the lines of busy.txt; POST /slow holds a request for 3 s
FAKE = TMP / "fake_srv.py"
FAKE_SRC = '''import sys, time, pathlib
sys.path.insert(0, %r)
from flask import Flask
import build_id
app = Flask(__name__)
HERE = pathlib.Path(__file__).resolve().parent
def busy():
    try: return [l for l in (HERE / "busy.txt").read_text().splitlines() if l.strip()]
    except OSError: return []
build_id.install(app, __file__, busy=busy)
@app.route("/slow", methods=["POST"])
def slow():
    time.sleep(3); return "done"
app.run(port=int(sys.argv[1]), debug=False, threaded=True)
''' % str(ROOT / "tools")
FAKE.write_text(FAKE_SRC, encoding="utf-8")
# a server from before the fingerprint: no /api/build, but /api/jobs and /api/suite like the old photo server
LEGACY = TMP / "legacy_srv.py"
LEGACY.write_text('''import http.server, json, sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/api/jobs":
            body = {"ok": True, "jobs": ([{"kind": "erase", "label": "x", "status": "running"}] if (HERE / "legacy_busy").exists() else [])}
        elif self.path == "/api/suite":
            body = {"compress": {"running": None, "queued": []}}
        else:
            self.send_response(404); self.end_headers(); return
        raw = json.dumps(body).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
''', encoding="utf-8")

ps.META = TMP
ps.LAUNCH_STATE = TMP / "launch.json"
ps.SUITE_LOG = TMP / "suite.log"
ps.KEEPER_STATE = TMP / "keeper.json"
ps.SERVICES = {
    "keeper": {"label": "keeper", "port": P_KEEPER, "args": ["-u", "tools/photo_suite.py", "keep"], "log": "keeper"},
    "server": {"label": "fake photo server", "port": P_FAKE, "args": ["-u", str(FAKE), str(P_FAKE)], "log": "fake"},
    "heroes": {"label": "legacy covers", "port": P_LEGACY, "args": ["-u", str(FAKE), str(P_LEGACY)], "log": "legacy"},
}
ps.KEPT = ("server", "heroes")
# the launcher's checks must be about the FAKE's code, not tools/photo_suite.py: entry_of() reads the service's args
assert ps.entry_of("server") == str(FAKE)

# ----------------------------------------------------------------------------------------------- A
f1 = build_id.fingerprint(ROOT / "tools" / "photo_editor.py")
f2 = build_id.fingerprint(ROOT / "tools" / "photo_editor.py")
ok("A1 the fingerprint is stable", f1["build"] == f2["build"] and len(f1["build"]) == 12, f1["build"])
ok("A2 it follows imports into tools/ (queue_tab, photo_suite, build_id, a lazy alt_writer)",
   all(("tools/%s.py" % m) in f1["files"] for m in ("queue_tab", "photo_suite", "build_id", "alt_writer", "prelaunch")), sorted(f1["files"]))
ok("A3 it names no module outside tools/", all(r.startswith("tools/") for r in f1["files"]), [r for r in f1["files"] if not r.startswith("tools/")])
fa = build_id.fingerprint(FAKE)
FAKE.write_text(FAKE_SRC + "# change 1\n", encoding="utf-8")
fb = build_id.fingerprint(FAKE)
ok("A4 any change to a file in the set changes the build", fa["build"] != fb["build"] and "tools/build_id.py" in fb["files"], (fa["build"], fb["build"]))

# ----------------------------------------------------------------------------------------------- B
ok("B0 (setup) the fake server starts", ps.start("server") and wait_http(P_FAKE))
c = ps.check_server("server")
ok("B1 a server on the current code reads as current and idle", c["answers"] and not c["stale"] and c["busy"] == [] and c["live"] == c["disk"], c)
pid0 = getj(P_FAKE, "/api/build")["pid"]
FAKE.write_text(FAKE_SRC + "# change 2\n", encoding="utf-8")
c = ps.check_server("server")
ok("B2 after an edit it reads as stale", c["stale"] and not c["missing"] and c["live"] != c["disk"], c)
lines = []
st = ps.refresh_stale(log=lines.append)
back = wait_http(P_FAKE)
info = getj(P_FAKE, "/api/build") if back else {}
ok("B3 the launcher restarts a stale idle server", back and info.get("pid") != pid0 and info.get("build") == ps.disk_build("server"), (pid0, info))
ok("B4 launch.json says so, and there is nothing pending", any("restarted on the current code" in l for l in st["lines"]) and st["pending"] == {}, st)
ok("B5 suite.log has the line", ps.SUITE_LOG.exists() and "restarted on the current code" in ps.SUITE_LOG.read_text(encoding="utf-8"))
ok("B6 /api/suite's launch_state reads the file", ps.launch_state().get("lines") == st["lines"])
lines = []
st = ps.refresh_stale(log=lines.append)
ok("B7 a second launch finds it current and restarts nothing", any(": current (" in l for l in st["lines"]) and getj(P_FAKE, "/api/build")["pid"] == info["pid"], st["lines"])

# ----------------------------------------------------------------------------------------------- C
(TMP / "busy.txt").write_text("compressing skopje.html\n", encoding="utf-8")
FAKE.write_text(FAKE_SRC + "# change 3\n", encoding="utf-8")
pid1 = getj(P_FAKE, "/api/build")["pid"]
st = ps.refresh_stale(log=lambda s: None)
ok("C1 a stale BUSY server is left running", getj(P_FAKE, "/api/build")["pid"] == pid1)
ok("C2 ...and marked pending with the reason", "server" in st["pending"] and "compressing skopje.html" in st["pending"]["server"]["why"]
   and any("busy" in l and "restarts by itself" in l for l in st["lines"]), st)
# the keeper: while busy it waits (one note); the moment busy clears it restarts, clears pending, adds a launch line
# (only the fake server is kept here: the legacy port is down and the keeper would otherwise start a fake on it)
ps.KEPT = ("server",)
real_every, ps.KEEP_HTTP_EVERY, ps.KEEP_GRACE = ps.KEEP_HTTP_EVERY, 1, 8
ps.KEEPER_STATE.unlink(missing_ok=True)
ps.keep(every=0.3, rounds=3)
ok("C3 the keeper leaves a busy stale server alone and says it is waiting", getj(P_FAKE, "/api/build")["pid"] == pid1
   and any("waiting for it to finish" in r["what"] for r in ps.keeper_restarts()), [r["what"] for r in ps.keeper_restarts()])
ok("C3b the keeper wrote its own build into keeper.json", ps._keeper_meta().get("build") == ps.disk_build("keeper"), ps._keeper_meta())
(TMP / "busy.txt").write_text("", encoding="utf-8")
ps.keep(every=0.3, rounds=4)
back = wait_http(P_FAKE)
info = getj(P_FAKE, "/api/build") if back else {}
ok("C4 once idle, the keeper restarts it on the new code", back and info.get("pid") != pid1 and info.get("build") == ps.disk_build("server"), info)
ls = ps.launch_state()
ok("C5 pending is cleared and the launch lines record the keeper's restart", ls.get("pending") == {} and any("(the keeper)" in l for l in ls.get("lines", [])), ls)
ok("C6 keeper.json notes 'restarted for new code'", any("restarted for new code" in r["what"] for r in ps.keeper_restarts()), [r["what"] for r in ps.keeper_restarts()])
ps.keep(every=0.3, rounds=2)
ok("C7 the keeper restarts nothing when the code is current", getj(P_FAKE, "/api/build")["pid"] == info["pid"])
ps.KEEP_HTTP_EVERY = real_every
ps.KEPT = ("server", "heroes")

# ----------------------------------------------------------------------------------------------- D
def slow_post():
    try:
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/slow" % P_FAKE, data=b"{}", method="POST"), timeout=20).read()
    except Exception:
        pass
t = threading.Thread(target=slow_post, daemon=True); t.start(); time.sleep(0.8)
b = getj(P_FAKE, "/api/build")
ok("D1 a POST in flight counts as busy", any("in flight" in r for r in b["busy"]), b)
stt, d = ps._http_json(P_FAKE, "/api/build", 5, {"action": "exit", "build": b["build"]})
ok("D2 an exit asked while busy is refused and nothing exits", stt == 200 and d and d.get("ok") is False and not d.get("exiting") and ps.port_open(P_FAKE), (stt, d))
t.join(10)
ok("D3 the counter goes back to idle when the request ends", getj(P_FAKE, "/api/build")["busy"] == [])
stt, d = ps._http_json(P_FAKE, "/api/build", 5, {"action": "exit", "build": "000000000000"})
time.sleep(1.0)
ok("D4 an exit with the wrong build is a 409 and the server stays", stt == 409 and ps.port_open(P_FAKE) and getj(P_FAKE, "/api/build")["pid"] == info["pid"], (stt, d))

# ----------------------------------------------------------------------------------------------- E
leg = subprocess.Popen([sys.executable, "-u", str(LEGACY), str(P_LEGACY)], cwd=str(ROOT), creationflags=ps.CREATE_NO_WINDOW)
ps._known_pids[P_LEGACY] = leg.pid          # a sandboxed session's netstat shows no owner for a listening port; start() records its own
ok("E0 (setup) the legacy server answers", wait_http(P_LEGACY) and ps.build_info(P_LEGACY) == {"missing": True})
(TMP / "legacy_busy").write_text("1")
c = ps.check_server("heroes")
ok("E1 a pre-fingerprint server reads as stale; its job routes decide busy", c["missing"] and c["stale"] and c["busy"] == [], c)   # not the photo server: covers' legacy busy is []
ps.SERVICES["server"], ps.SERVICES["heroes"] = ps.SERVICES["heroes"], ps.SERVICES["server"]      # make the legacy one "server" so legacy_busy asks its jobs
c = ps.check_server("server")
ok("E2 ...the photo server's /api/jobs running job counts", c["missing"] and c["busy"] and "erase job" in c["busy"][0], c)
st = ps.refresh_stale(log=lambda s: None)
ok("E3 busy pre-fingerprint server: left running, pending", leg.poll() is None and "server" in st["pending"] and "from before the fingerprint" in " ".join(st["lines"]), st)
(TMP / "legacy_busy").unlink()
st = ps.refresh_stale(log=lambda s: None)
time.sleep(0.5)
gone = leg.poll() is not None or not any(ps._alive(p) for p in [leg.pid])
nb = ps.build_info(P_LEGACY)
ok("E4 idle pre-fingerprint server: stopped and started fresh on the new code (now with /api/build)", gone and nb and nb.get("build") == ps.disk_build("server") and st["pending"] == {}, (gone, nb, st))
ps.SERVICES["server"], ps.SERVICES["heroes"] = ps.SERVICES["heroes"], ps.SERVICES["server"]

# ----------------------------------------------------------------------------------------------- F
ps._kill(ps.port_pids(P_LEGACY)); wait_closed(P_LEGACY)
st = ps.refresh_stale(log=lambda s: None)
ok("F1 a server that is down is not mentioned (start_all starts it)", not any("legacy covers" in l for l in st["lines"]), st["lines"])
silent = socket.socket(); silent.bind(("127.0.0.1", P_LEGACY)); silent.listen(1)
real_start, started = ps.start, []
ps.start = lambda n: started.append(n) or True
try:
    st = ps.refresh_stale(log=lambda s: None)
finally:
    ps.start = real_start
silent.close()
ok("F2 a port that accepts but never answers is left to the keeper, never restarted", any("not answering; left to the keeper" in l for l in st["lines"]) and not started, (st["lines"], started))
# a stale keeper: its recorded build differs from the disk -> killed (we record the kill instead) and restarted by start_all
lock = socket.socket(); lock.bind(("127.0.0.1", P_KEEPER)); lock.listen(1)
ps._keeper_state_update(build="oldoldoldold", pid=0)
killed = []
real_kill, real_pids, real_alive = ps._kill, ps.port_pids, ps._alive
ps._kill, ps.port_pids, ps._alive = (lambda pids: killed.append(list(pids))), (lambda port: [424242] if port == P_KEEPER else real_pids(port)), (lambda pid: True if pid == 424242 else real_alive(pid))
try:
    st = ps.refresh_stale(log=lambda s: None)
finally:
    ps._kill, ps.port_pids, ps._alive = real_kill, real_pids, real_alive
    lock.close()
ok("F3 a keeper running old code is stopped (start_all then starts it)", killed == [[424242]] and any(l.startswith("keeper: restarted") for l in st["lines"]), (killed, st["lines"]))

# ----------------------------------------------------------------------------------------------- G
env = dict(os.environ, PHOTO_EDITOR_PORT=str(P_REAL), PHOTO_EDITOR_NO_WORKER="1")
real_log = open(TMP / "real.log", "wb")
real = subprocess.Popen([sys.executable, "-u", str(ROOT / "tools" / "photo_editor.py")], cwd=str(ROOT), env=env,
                        stdout=real_log, stderr=subprocess.STDOUT, creationflags=ps.CREATE_NO_WINDOW)
(TMP / "real.pid").write_text(str(real.pid))
ps._known_pids[P_REAL] = real.pid
up = wait_http(P_REAL, 90)
b = getj(P_REAL, "/api/build") if up else {}
want = build_id.fingerprint(ROOT / "tools" / "photo_editor.py")["build"]
ok("G1 the real photo server reports the disk build and reads idle", up and b.get("build") == want and b.get("busy") == [] and b.get("files", 0) > 20, b)
s = getj(P_REAL, "/api/suite") if up else {}
ok("G2 /api/suite carries `launch` for Live Activity", up and "launch" in s and isinstance(s["launch"], dict), list(s))
stt, d = ps._http_json(P_REAL, "/api/build", 5, {"action": "exit", "build": want})
ok("G3 the exit action takes it down within a few seconds", stt == 200 and d and d.get("exiting") and wait_closed(P_REAL, 8) and real.wait(10) == 0, (stt, d))
real_log.close()

# ----------------------------------------------------------------------------------------------- H
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1280, "height": 800})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        state = {"pending": {"server": {"since": "2026-11-07 09:00", "why": "compressing skopje.html"}},
                 "lines": ["photo server: running old code (abc, disk def) but busy: compressing skopje.html. It restarts by itself once that finishes."],
                 "at": "2026-11-07 09:00"}
        def suite(route):
            route.fulfill(status=200, body=json.dumps({"status": {"server": True, "heroes": True, "maps": True, "keeper": True}, "tidy": None,
                                                       "compress": {"queued": [], "running": None, "last": None, "failed": {}}, "launch": state}),
                          headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})
        # everything else the editor asks :5003 for is answered here too, so the live server is never touched
        pg.route("http://127.0.0.1:5003/**", lambda route: suite(route) if route.request.url.endswith("/api/suite")
                 else route.fulfill(status=404, body="{}", headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}))
        pg.goto((ROOT / "editor.html").as_uri() + "#editor")
        pg.wait_for_selector("#notice-launch", timeout=15000)
        txt = pg.inner_text("#notice-launch")
        ok("H1 the pending notice shows on load", "Updating the editor after the current photo job finishes" in txt and "photo server" in txt and "compressing skopje.html" in txt, txt)
        state = {"pending": {}, "lines": state["lines"] + ["photo server: restarted for new code (abc -> def) at 09:05 (the keeper)"], "at": "2026-11-07 09:00"}
        pg.evaluate("() => {}")
        # the watcher looks again every 15 s; wait for the green notice
        pg.wait_for_function("() => { const n = document.getElementById('notice-launch'); return n && /was updated/.test(n.textContent); }", timeout=25000)
        ok("H2 once pending clears it says the editor was updated", True)
        pg.evaluate("() => openActivity()")
        pg.wait_for_function("() => /Launch/.test(document.querySelector('#act-ctl').textContent)", timeout=8000)
        card = pg.inner_text("#act-ctl")
        ok("H3 Live Activity has the Launch card with the lines", "Launch" in card and "servers updated" in card and "(the keeper)" in card, card[:400])
        ok("H4 no page errors", not errs, errs)
        br.close()
except Exception as e:
    ok("H  editor.html checks ran", False, repr(e))

# ----------------------------------------------------------------------------------------------- cleanup
for p in (P_FAKE, P_LEGACY, P_REAL):
    ps._kill(ps.port_pids(p))
kill_test_procs()
print()
print(f"{sum(1 for c in checks if not c[1])} failed of {len(checks)}")
sys.exit(1 if any(not c[1] for c in checks) else 0)
