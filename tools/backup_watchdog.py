"""Restart the backup WATCHER ITSELF when it silently stalls.

This is deliberately separate from icloud_watchdog.py, which handles a
different failure: Apple's own iCloudPhotos/ApplePhotoStreams services dying
or degrading. This one handles a failure inside tools/photo_backup.py that
Apple has nothing to do with:

    A stalled cloud read spawns a background thread that Python cannot kill.
    When abandoned, that thread keeps running - and keeps competing for
    bandwidth - for as long as the WATCHER PROCESS lives. Over a long enough
    run, especially on an album with a high failure rate, enough of these
    accumulate that every real worker ends up starved and the whole watcher
    goes to 0% CPU and 0 network connections while still claiming
    state="running". Observed repeatedly tonight (13-25 zombie threads
    against a --workers 4 config).

There is no way to fix this from inside the process - the thread cannot be
freed short of ending the process that owns it. Restarting the WATCHER
SCRIPT is what clears it, and that has been safe every time tonight: copies
write to unique per-attempt temp files and are retried cleanly, so killing
it mid-copy loses no data. This is NOT the same as restarting Apple's
services (iCloudPhotos.exe) mid-transfer, which corrupts in-flight hydration
and caused 2,365 failures earlier - this script never touches those.

    python tools/backup_watchdog.py            # run it
    python tools/backup_watchdog.py --once      # check once and exit
    (stop it by killing the process)
"""
import argparse
import subprocess
import time
from pathlib import Path

BACKUP = Path.home() / "Backup"
SHARED = Path.home() / "iCloudPhotos" / "Shared"
SKIP = {"Houston Trip", "New Zealand", "tomorrowland x bvi"}
IMG = {".jpg", ".jpeg", ".heic", ".png"}
VID = {".mp4", ".mov", ".m4v"}

CHECK_EVERY = 60           # seconds between checks
STALL_TICKS = 5            # this many consecutive no-progress checks = stalled
MIN_GAP_MIN = 4            # never restart more often than this

PS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]
# Every shell-out from this (windowless) process still flashes a console on
# screen unless told not to. This ran every 60s and was the blank windows
# popping up all over the desktop.
_NOWIN = {"creationflags": 0x08000000}          # CREATE_NO_WINDOW
FIND_PID = (
    "(Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
    "Where-Object { $_.CommandLine -match 'photo_backup.py' -and "
    "$_.CommandLine -match '--watch' }).ProcessId"
)
RESTART = (
    "$old = " + FIND_PID + ";"
    "foreach ($id in $old) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue };"
    "Start-Sleep -Seconds 2;"
    "$pyw = 'C:\\Users\\kevin\\AppData\\Local\\Python\\pythoncore-3.14-64\\pythonw.exe';"
    # -ArgumentList as an ARRAY silently fails to launch here: Start-Process
    # reports success but Get-CimInstance shows nothing actually running.
    # Tested and confirmed. A single pre-quoted argument STRING works - do
    # not revert this to an array without re-testing end to end.
    "$argStr = '\"C:\\Users\\kevin\\OneDrive\\Documents\\Travel Blog\\tools\\photo_backup.py\" "
    "--watch --workers 4 --interval 90';"
    "Start-Process $pyw -WindowStyle Hidden -ArgumentList $argStr "
    "-WorkingDirectory 'C:\\Users\\kevin\\OneDrive\\Documents\\Travel Blog'"
)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def watcher_running():
    try:
        out = subprocess.run(PS + [FIND_PID], capture_output=True, text=True,
                              timeout=20, **_NOWIN).stdout.strip()
        return bool(out)
    except Exception:
        return None                          # unknown - don't act on a guess


def backup_total():
    """Total files backed up across every non-skipped album. Same counting
    logic used by hand all night: photos in the album dir + videos/."""
    n = 0
    try:
        for d in BACKUP.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            n += sum(1 for p in d.iterdir() if p.is_file()
                     and not p.name.startswith("_manifest") and p.suffix.lower() in IMG)
            vd = d / "videos"
            if vd.is_dir():
                n += sum(1 for p in vd.iterdir() if p.suffix.lower() in VID)
    except OSError:
        return None
    return n


def work_left():
    try:
        for d in SHARED.iterdir():
            if not d.is_dir() or d.name in SKIP:
                continue
            have_p = sum(1 for p in d.iterdir() if p.suffix.lower() in {".jpg", ".mp4"})
            bd = BACKUP / d.name
            have_b = 0
            if bd.is_dir():
                have_b = sum(1 for p in bd.iterdir() if p.is_file()
                             and not p.name.startswith("_manifest") and p.suffix.lower() in IMG)
                vd = bd / "videos"
                if vd.is_dir():
                    have_b += sum(1 for p in vd.iterdir() if p.suffix.lower() in VID)
            if have_b < have_p:
                return True
    except OSError:
        return None
    return False


def restart():
    try:
        subprocess.run(PS + [RESTART], capture_output=True, timeout=60, **_NOWIN)
        return True
    except Exception as e:
        log(f"restart failed: {e}")
        return False


def check(state):
    if watcher_running() is False:
        log("watcher process not found - starting it")
        restart()
        state["stall"], state["total"], state["last"] = 0, backup_total(), time.time()
        return

    left = work_left()
    if left is False:
        state["stall"] = 0
        return                               # nothing left to do, nothing to watch
    if left is None:
        return                               # couldn't tell, don't guess

    total = backup_total()
    if total is None:
        return
    if state["total"] is None or total != state["total"]:
        state["stall"] = 0
    else:
        state["stall"] += 1
    state["total"] = total

    if state["stall"] >= STALL_TICKS:
        if (time.time() - state["last"]) / 60 < MIN_GAP_MIN:
            return                           # already restarted very recently
        log(f"STALLED - {total} files, no change for "
            f"{STALL_TICKS * CHECK_EVERY // 60} min - restarting the watcher")
        if restart():
            log("restarted photo_backup.py --watch")
            state["last"] = time.time()
        state["stall"] = 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    state = {"stall": 0, "total": backup_total(), "last": 0.0}
    log(f"watchdog up - {state['total']} files backed up, "
        f"restart if unchanged for {STALL_TICKS * CHECK_EVERY // 60} min")
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
