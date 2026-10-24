"""One entry point for the editor suite's background helpers.

    python tools/photo_suite.py            start what isn't running, open the editor
    python tools/photo_suite.py status     what is up
    python tools/photo_suite.py stop       stop everything the suite started
    python tools/photo_suite.py keep       the keeper: restart a server that died (start runs it)
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
    return sorted(pids)


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
    subprocess.Popen([PYW] + s["args"], cwd=str(ROOT), stdout=out, stderr=err,
                     stdin=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)
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
    import json
    restarts = (keeper_restarts() + [entry])[-50:]
    try:
        META.mkdir(parents=True, exist_ok=True)
        KEEPER_STATE.write_text(json.dumps({"restarts": restarts}, indent=1), encoding="utf-8")
    except OSError:
        pass
    print(time.strftime("%Y-%m-%d %H:%M:%S"), entry["service"], entry["what"], flush=True)


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
    # the map editor and Covers are restarted when their code changed (stress test 2026-10-23: :5002 ran code from
    # the 16th for a week and its Save embedded a degraded map). Their work is short, so a restart ten minutes after
    # the last edit to their files cuts nothing off; the photo server (queue, imports) is restarted by hand.
    CODE = {"maps": ["tools/map_editor.py", "tools/itinerary_map.py", "tools/city_map.py", "tools/embed_itinerary_map.py", "tools/country_outline.py"],
            "heroes": ["tools/hero_picker.py", "tools/gen_hero_variants.py", "tools/fit_backgrounds.py"]}
    def code_mtime(n):
        try:
            return max(os.path.getmtime(ROOT / f) for f in CODE.get(n, []) if (ROOT / f).exists())
        except ValueError:
            return 0.0
    code_seen = {n: code_mtime(n) for n in CODE}   # what the running processes are assumed to carry (they are started fresh with the keeper)

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
            if n in CODE and port_open(port) and now >= hold[n]:
                newest = code_mtime(n)
                if newest > code_seen[n] + 1 and now - newest > 600:
                    pids = live_pids(n)
                    if pids:
                        _kill(pids)
                        time.sleep(1)
                    code_seen[n] = newest
                    try:
                        started = start(n)
                    except Exception as e:
                        started = False
                    started_at[n] = now
                    answered[n] = now + KEEP_GRACE
                    grace[n] = now + KEEP_GRACE
                    misses[n] = 0
                    _keeper_note({"service": n, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "what": "restarted for new code (%s changed)" % ", ".join(f.split("/")[-1] for f in CODE[n] if (ROOT / f).exists() and os.path.getmtime(ROOT / f) > now - 86400),
                                  "err": [] if started else ["start failed"]})
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
    elif cmd == "stop":
        for name, pids in stop_all().items():
            print(f"{SERVICES[name]['label']}: stopped {pids or 'nothing'}")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv)
