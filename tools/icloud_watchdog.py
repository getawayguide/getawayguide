"""Restart Apple's photo services when they wedge, so the backup can finish
unattended.

Both services fail in ways that look like "slow" but are actually "stopped",
and both recover instantly from a restart:

  * ApplePhotoStreams (shared albums) loses a startup race and runs as a COM
    stub that never syncs - see "Fix Shared Albums.cmd" for the full story.
  * iCloudPhotos (main library) hangs individual cloud hydrations. Each hung
    file blocks one backup worker, and with 3 workers, 3 hangs is a total
    standstill that can last 20 minutes before Windows gives up with
    WinError 426.

Observed overnight: hydrations hung for 16 minutes with all three workers
blocked and zero files copied, then resumed the moment the services restarted.

    python tools/icloud_watchdog.py            # run it
    python tools/icloud_watchdog.py --once     # check once and exit
    (stop it by killing the process)

It only ever restarts Apple's own services. An interrupted copy fails and the
backup retries it on the next pass, so this is safe to fire at any time.
"""
import argparse
import subprocess
import time
from pathlib import Path

BACKUP = Path.home() / "Backup"
# Tuned from the log. Hangs are not caused by particular files - a fresh trio
# wedges every cycle, which reads as Apple throttling a bulk pull rather than
# anything local. So the win is not in preventing hangs, it is in not sitting
# through them: at a 10-minute threshold each cycle was ~1 min of copying and
# ~11 min of dead waiting. Cutting the threshold roughly doubles throughput.
CHECK_EVERY = 60           # seconds between checks
PART_AGE_MIN = 4           # a .part older than this is a hung hydration
PART_COUNT = 3             # this many hung = every worker is blocked
IDLE_MIN = 8               # no new file in this long, with work outstanding
MIN_GAP_MIN = 5            # never restart more often than this

PS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]
RESTART = (
    "$pkg = Get-AppxPackage -Name 'AppleInc.iCloud';"
    "foreach ($n in 'ApplePhotoStreams','iCloudPhotos') {"
    "  $p = Get-Process $n -ErrorAction SilentlyContinue;"
    "  if ($p) { Stop-Process -Id $p.Id -Force } };"
    "Start-Sleep -Seconds 4;"
    "foreach ($n in 'ApplePhotoStreams','iCloudPhotos') {"
    "  $e = Join-Path $pkg.InstallLocation ('iCloud\\' + $n + '.exe');"
    "  if (Test-Path $e) { Start-Process $e } }"
)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def hung_parts():
    now = time.time()
    out = []
    try:
        for p in BACKUP.rglob("*.part"):
            try:
                age = (now - p.stat().st_ctime) / 60
                if age > PART_AGE_MIN:
                    out.append((p.name, round(age)))
            except OSError:
                pass
    except OSError:
        pass
    return out


def backup_files():
    n = 0
    try:
        for d in BACKUP.iterdir():
            if d.is_dir() and not d.name.startswith("_"):
                try:
                    n += sum(1 for p in d.rglob("*")
                             if p.is_file() and p.suffix.lower() != ".part"
                             and not p.name.startswith("_manifest"))
                except OSError:
                    pass
    except OSError:
        pass
    return n


def work_left():
    """Is there anything still to copy? Without this the watchdog would keep
    restarting Apple's services forever once the backup is simply finished.

    Returns None when it cannot tell. An error used to fall through to False,
    which reads as "all done" and silently disarms the watchdog - exactly the
    wrong way for this to fail, since the whole point is to notice a stall."""
    shared = Path.home() / "iCloudPhotos" / "Shared"
    skip = {"Houston Trip", "New Zealand", "tomorrowland x bvi"}
    img = {".jpg", ".jpeg", ".heic", ".png"}
    vid = {".mp4", ".mov", ".m4v"}
    try:
        for d in shared.iterdir():
            if not d.is_dir() or d.name in skip:
                continue
            n = sum(1 for p in d.iterdir() if p.suffix.lower() in {".jpg", ".mp4"})
            bd = BACKUP / d.name
            b = 0
            if bd.is_dir():
                b = sum(1 for p in bd.iterdir() if p.is_file()
                        and not p.name.startswith("_manifest") and p.suffix.lower() in img)
                vdir = bd / "videos"
                if vdir.is_dir():
                    b += sum(1 for p in vdir.iterdir() if p.suffix.lower() in vid)
            if b < n:
                return True
    except OSError:
        return None
    return False


def restart():
    try:
        subprocess.run(PS + [RESTART], capture_output=True, timeout=120)
        return True
    except Exception as e:
        log(f"restart failed: {e}")
        return False


def check(state):
    left = work_left()
    if left is None:
        return                              # couldn't tell; leave the clock alone
    if not left:
        log("nothing left to back up - idling")
        state["files"], state["since"] = backup_files(), time.time()
        return
    now = time.time()
    # A long gap means the machine slept rather than the sync wedging. Reset
    # instead of blaming a stall on time that simply did not happen.
    if now - state.get("tick", now) > 600:
        state["since"] = now
    state["tick"] = now
    files = backup_files()
    if files != state["files"]:
        state["files"], state["since"] = files, now
    idle = (now - state["since"]) / 60
    parts = hung_parts()
    why = None
    if len(parts) >= PART_COUNT:
        why = ("hung hydrations: "
               + ", ".join(f"{n} ({a}m)" for n, a in parts[:3]))
    elif idle > IDLE_MIN:
        why = f"no new file in {idle:.0f} min ({files} backed up)"
    if not why:
        return
    if (now - state["last"]) / 60 < MIN_GAP_MIN:
        return                              # already restarted very recently
    log(f"WEDGED - {why}")
    if restart():
        log("restarted ApplePhotoStreams + iCloudPhotos")
        state["last"], state["since"] = now, now


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    state = {"files": backup_files(), "since": time.time(), "last": 0.0}
    log(f"watchdog up - {state['files']} files backed up, "
        f"restart if {PART_COUNT} hydrations hang past {PART_AGE_MIN}m "
        f"or nothing lands for {IDLE_MIN}m")
    if a.once:
        check(state)
        return
    while True:
        try:
            check(state)
        except Exception as e:
            log(f"check error: {e}")
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    main()
