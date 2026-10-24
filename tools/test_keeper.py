"""The keeper (tools/photo_suite.py keep) brings a dead server back, backs off from one that
crashes on start, and runs one at a time. Throwaway ports only; the real suite is untouched.

    python tools/test_keeper.py
"""
import pathlib
import socket
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import photo_suite as ps

checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))


TMP = pathlib.Path(__file__).resolve().parent.parent / ".tmp" / "keeper-test"
TMP.mkdir(parents=True, exist_ok=True)
(TMP / "fake_server.py").write_text(
    "import http.server, sys\n"
    "http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), http.server.BaseHTTPRequestHandler).serve_forever()\n",
    encoding="utf-8")
(TMP / "crasher.py").write_text("import sys; sys.exit(3)\n", encoding="utf-8")
(TMP / "slow_server.py").write_text(
    "import http.server, sys, time\ntime.sleep(float(sys.argv[2]))\n"
    "http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), http.server.BaseHTTPRequestHandler).serve_forever()\n",
    encoding="utf-8")

# the keeper under test: its own lock port, one healthy fake server, one that dies on start
ps.META = TMP
ps.SERVICES = {
    "keeper": {"label": "keeper", "port": 5019, "args": [], "log": "keeper"},
    "server": {"label": "fake server", "port": 5018, "args": ["-u", str(TMP / "fake_server.py"), "5018"], "log": "fake"},
    "heroes": {"label": "crasher", "port": 5017, "args": ["-u", str(TMP / "crasher.py")], "log": "crasher"},
}
ps.KEPT = ("server", "heroes")
ps.KEEPER_STATE = TMP / "keeper.json"
ps.KEEP_GRACE = 0              # the grace has its own check below
if ps.KEEPER_STATE.exists():
    ps.KEEPER_STATE.unlink()

ps._kill(ps.port_pids(5018))
time.sleep(0.5)
ok("(setup) the fake server starts", ps.start("server") and any(ps.port_open(5018) for _ in range(20) if not time.sleep(0.25)))

# 1. one miss is not acted on; two misses in a row restart the server
log = []
t = threading.Thread(target=lambda: log.append(ps.keep(every=0.5, rounds=12)), daemon=True)
t.start()
time.sleep(1.2)
ok("the keeper holds its lock port", ps.port_open(5019))
ok("a second keeper refuses to run", ps.keep(every=0.5, rounds=1) is False)
ps._kill(ps.port_pids(5018))
for _ in range(40):                         # TerminateProcess returns before the socket closes: a check
    if not ps.port_open(5018):              # made at once still reached the dying server, and the
        break                               # wait below ended before the keeper had done anything
    time.sleep(0.1)
for _ in range(80):                         # 20 s: two missed checks, a netstat, and a cold Python start
    if ps.port_open(5018):
        break
    time.sleep(0.25)
back = ps.port_open(5018)
ok("a killed server is back within a few seconds", back)
t.join(timeout=90)          # a refused probe can take its full 0.4 s on Windows: rounds run long
rs = ps.keeper_restarts()
mine = [r for r in rs if r["service"] == "server"]
ok("the restart is noted in keeper.json", len(mine) == 1 and mine[0]["what"] == "restarted", rs)
ok("the keeper exits after its rounds and frees the lock", not t.is_alive() and not ps.port_open(5019))

# 2. a server that crashes on start is tried three times, then left alone
if ps.KEEPER_STATE.exists():
    ps.KEEPER_STATE.unlink()
t0 = time.time()
ps.keep(every=0.3, rounds=16)
rs = ps.keeper_restarts()
cr = [r for r in rs if r["service"] == "heroes"]
ok("a crashing server gets three restarts", sum(1 for r in cr if r["what"] == "restarted") == 3, [r["what"] for r in cr])
ok("then it is left alone instead of looping", any("left alone" in r["what"] for r in cr) and sum(1 for r in cr if r["what"] == "restarted") == 3)
ok("the hold is one note, not one per round", sum(1 for r in cr if "left alone" in r["what"]) == 1, [r["what"] for r in cr])
ok("keeper.log line per event", (TMP / "keeper.json").exists())

# 3. a server that takes a while to bind is started once, not once per check (the grace)
ps.SERVICES["heroes"] = {"label": "slow", "port": 5017, "args": ["-u", str(TMP / "slow_server.py"), "5017", "4"], "log": "slow"}
ps.KEEPER_STATE.unlink(missing_ok=True)
ps.KEEP_GRACE = 8
ps._kill(ps.port_pids(5017))
ps.keep(every=0.4, rounds=30)                 # ~12 s: without the grace this restarted it 3+ times
starts = [r for r in ps.keeper_restarts() if r["service"] == "heroes"]
ok("a slow-binding server is started once (the grace)", len(starts) == 1 and ps.port_open(5017), [r["what"] for r in starts])
ok("only one process listens on its port", len(ps.port_pids(5017)) == 1, ps.port_pids(5017))
ps._kill(ps.port_pids(5017))

# 3b. a server that is still LISTENING but does not answer is left alone (no second copy on its port);
#     only after KEEP_HUNG is it stopped and started fresh
real_open, real_pids, real_kill, real_start = ps.port_open, ps.port_pids, ps._kill, ps.start
calls = {"start": 0, "kill": []}
ps.SERVICES["heroes"] = {"label": "busy", "port": 5017, "args": [], "log": "busy"}
ps.port_open = lambda port: False if port == 5017 else real_open(port)       # never answers
ps.port_pids = lambda port: [424242] if port == 5017 else real_pids(port)    # but is listening
real_alive = ps._alive; ps._alive = lambda pid: pid == 424242 or real_alive(pid)
ps._kill = lambda pids: calls["kill"].append(list(pids))
ps.start = lambda n: calls.__setitem__("start", calls["start"] + 1) or True
ps.KEEPER_STATE.unlink(missing_ok=True)
ps.KEEP_GRACE, ps.KEEP_HUNG = 0, 3
real_http = ps.http_answers
try:
    ps.keep(every=0.4, rounds=5)                  # ~2 s: listening, not answering, under KEEP_HUNG
    ok("a busy server that still listens gets no second copy", calls["start"] == 0 and not calls["kill"], calls)
    ps.keep(every=0.5, rounds=12)                 # past KEEP_HUNG, the fake server answering: stopped, started fresh
    ok("after KEEP_HUNG alone without an answer it is stopped and started fresh", calls["kill"] and calls["start"] >= 1, calls)

    # 3c. every server silent at once is the machine (a loopback freeze), not a server: nothing is killed
    calls["start"], calls["kill"] = 0, []
    ps.http_answers = lambda port, timeout=5.0: False
    ps.keep(every=0.5, rounds=12)
    ok("a machine-wide freeze (every server silent) kills nothing", calls["start"] == 0 and not calls["kill"], calls)

    # 3d. a server that accepts connections but never answers HTTP, while the other answers: an app hang
    calls["start"], calls["kill"] = 0, []
    ps.port_open = lambda port: True if port == 5017 else real_open(port)
    ps.http_answers = lambda port, timeout=5.0: False if port == 5017 else real_http(port, timeout)
    ps.keep(every=0.5, rounds=12)
    ok("a server that accepts but never answers, alone, is stopped and started fresh", calls["kill"] and calls["start"] >= 1, calls)
finally:
    ps.port_open, ps.port_pids, ps._kill, ps.start, ps._alive = real_open, real_pids, real_kill, real_start, real_alive
    ps.http_answers = real_http
    ps.KEEP_HUNG = 600

# 4. stop_all keeps going when a lookup fails, and stops the keeper first
order = []
real_pids, real_kill = ps.port_pids, ps._kill
def fake_pids(port):
    order.append(port)
    if port == 5018:
        raise TimeoutError("slow lookup")
    return []
ps.port_pids, ps._kill = fake_pids, (lambda pids: None)
try:
    res = ps.stop_all()
    ok("stop_all survives a failed lookup and reaches every service", set(res) == set(ps.SERVICES) and order[0] == 5019, (order, list(res)))
finally:
    ps.port_pids, ps._kill = real_pids, real_kill

ps._kill(ps.port_pids(5018))
for name, good, info in checks:
    print(("PASS " if good else "FAIL ") + name + ("" if good or not info else f"  {info}"))
print(f"{sum(1 for c in checks if not c[1])} failed")
sys.exit(1 if any(not c[1] for c in checks) else 0)
