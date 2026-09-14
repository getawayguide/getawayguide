"""One entry point for the editor suite's background helpers.

    python tools/photo_suite.py            start what isn't running, open the editor
    python tools/photo_suite.py status     what is up
    python tools/photo_suite.py pause      stop the backup watcher (photos stay put)
    python tools/photo_suite.py resume     start the watcher again
    python tools/photo_suite.py fix        restart Apple's iCloud photo services
    python tools/photo_suite.py stop       stop everything the suite started
    python tools/photo_suite.py site       preview the live site (the photo server
                                           serves the whole repo at /site/)

`Editor Suite.cmd` in the repo root is a one-line wrapper around this, and
tools/photo_editor.py exposes the same actions at /api/suite so the Live
Activity panel in editor.html can drive them. This replaced three separate
launchers (Start Photo Tools / Pause Backup / Fix Shared Albums).

The three helpers:
  * photo server  (port 5003)  photos + library + picks for editor.html
  * hero picker   (port 5004)  choosing hero banners, opens from the editor
  * backup watcher             pulls full-res originals for each Shared Album

Every helper runs under pythonw with -u and its output appended to
~/Backup/_meta/<name>.log|.err: under a bare pythonw a crash is SILENT, and
when the server dies every photo in the editor just stops loading.

backup_watchdog.py is deliberately NOT started. Restarting the watcher
abandons its in-flight copies, and those keep their place in iCloud's
hydration queue as ghosts, so every restart pushed fresh workers further back
in line. The watcher waits slow hydrations out itself.
"""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META = Path.home() / "Backup" / "_meta"

# the same interpreter the launcher used to run this file, but windowless
_pyw = Path(sys.executable).with_name("pythonw.exe")
PYW = str(_pyw if _pyw.exists() else Path(sys.executable))

CREATE_NO_WINDOW = 0x08000000
DETACHED = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

SERVICES = {
    "server":  {"label": "photo server",   "port": 5003,
                "args": ["-u", "tools/photo_editor.py"],            "log": "editor"},
    "heroes":  {"label": "hero picker",    "port": 5004,
                "args": ["-u", "tools/hero_picker.py"],             "log": "heroes"},
    "watcher": {"label": "backup watcher", "match": "photo_backup",
                "args": ["-u", "tools/photo_backup.py", "--watch",
                         "--workers", "4", "--interval", "90"],     "log": "watcher"},
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


def port_pids(port):
    out = _ps(f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
              "| Select-Object -ExpandProperty OwningProcess")
    return sorted({int(x) for x in out.split() if x.strip().isdigit()})


def watcher_pids():
    """PIDs of python processes running photo_backup.py (the watcher)."""
    out = _ps("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" "
              "| Where-Object { $_.CommandLine -match 'photo_backup' } "
              "| Select-Object -ExpandProperty ProcessId")
    return sorted({int(x) for x in out.split() if x.strip().isdigit()})


def is_up(name):
    s = SERVICES[name]
    return port_open(s["port"]) if "port" in s else bool(watcher_pids())


def start(name):
    """Start one helper unless it is already up. Returns True when started."""
    if is_up(name):
        return False
    s = SERVICES[name]
    META.mkdir(parents=True, exist_ok=True)
    out = open(META / f"{s['log']}.log", "ab")
    err = open(META / f"{s['log']}.err", "ab")
    subprocess.Popen([PYW] + s["args"], cwd=str(ROOT), stdout=out, stderr=err,
                     stdin=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)
    return True


def _kill(pids):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)          # TerminateProcess on Windows
        except OSError:
            pass


def pause():
    """Stop the watcher. Nothing is lost: every photo already downloaded
    stays put, and resume picks up exactly where this left off."""
    pids = watcher_pids()
    _kill(pids)
    return pids


def resume():
    return start("watcher")


def stop_all():
    """Stop everything the suite started (not iCloud itself)."""
    stopped = {}
    for name, s in SERVICES.items():
        pids = port_pids(s["port"]) if "port" in s else watcher_pids()
        _kill(pids)
        stopped[name] = pids
    return stopped


def fix_icloud(log=print):
    """Restart ApplePhotoStreams + iCloudPhotos (see tools/icloud_fix.ps1),
    streaming each line to `log`. Takes about a minute. Returns the exit
    code: 0 healthy, 2 still idle (run again), 1 iCloud not installed."""
    p = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-File", str(ROOT / "tools" / "icloud_fix.ps1")],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         creationflags=CREATE_NO_WINDOW)
    for line in p.stdout:
        log(line.rstrip())
    return p.wait()


def status():
    w = watcher_pids()
    return {"server": port_open(5003), "heroes": port_open(5004),
            "watcher": bool(w), "watcherPids": w}


CHROME = next((p for p in (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
) if p.is_file()), None)

# The editor keeps opening from file:// (not the :5003 copy): its settings and
# presets live in that origin's localStorage, and Kevin has always used it
# that way. Same URL to bookmark in Chrome.
EDITOR_URL = (ROOT / "editor.html").as_uri()


def open_editor():
    """Open the editor as a normal Chrome tab (Kevin keeps research tabs
    alongside it, so a separate app window got in the way). Falls back to
    the default browser without Chrome."""
    if CHROME:
        subprocess.Popen([str(CHROME), EDITOR_URL], creationflags=DETACHED, close_fds=True)
    else:
        os.startfile(str(ROOT / "editor.html"))


def start_all(open_editor_window=True, log=print):
    for name, s in SERVICES.items():
        log(f"Starting {s['label']}..." if start(name) else f"{s['label']} already running.")
    if open_editor_window:
        # give the server a moment so the first thumbnails don't 404
        for _ in range(20):
            if port_open(5003):
                break
            time.sleep(0.25)
        open_editor()


def main(argv):
    cmd = (argv[1] if len(argv) > 1 else "start").lower()
    if cmd == "start":
        start_all()
        print("Ready. The photo library, picks and hero picker live inside the editor.")
    elif cmd == "status":
        for k, v in status().items():
            print(f"{k:12} {v}")
    elif cmd == "pause":
        pids = pause()
        print(f"Backup paused (stopped {len(pids)} process(es)). "
              "Run `Editor Suite.cmd resume` or use the editor's Live Activity panel to resume.")
    elif cmd == "resume":
        print("Backup watcher started." if resume() else "Backup watcher was already running.")
    elif cmd == "fix":
        sys.exit(fix_icloud())
    elif cmd == "site":
        # what serve.ps1 used to do with its own listener on :8080; the photo
        # server already serves every file in the repo under /site/
        start("server")
        for _ in range(20):
            if port_open(5003):
                break
            time.sleep(0.25)
        os.startfile("http://127.0.0.1:5003/site/index.html")
    elif cmd == "stop":
        for name, pids in stop_all().items():
            print(f"{SERVICES[name]['label']}: stopped {pids or 'nothing'}")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv)
