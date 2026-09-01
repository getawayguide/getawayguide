#!/usr/bin/env python3
"""
Back up FULL-RESOLUTION originals into per-country folders.

A Shared Album folder only holds Apple's 2048px copies. For each one this
finds the full-resolution original in the iCloud library (matched on capture
timestamp — see photo_editor.find_original), pulls it down and files it under

    <Backup>/<Album name>/<original filename>

Photos already backed up are skipped, so it is safe to re-run and it resumes
after an interruption. --watch keeps running and picks up new albums as they
finish syncing from the phone.

    python tools/photo_backup.py                 # one pass
    python tools/photo_backup.py --watch         # keep going as albums appear
    python tools/photo_backup.py --album Kosovo  # just one

Status for the browser UI is written continuously to <Backup>/_meta/status.json.
Nothing in the iCloud library or the Shared albums is modified.
"""
import argparse
import json
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_editor as pe                                   # noqa: E402

HOME = Path.home()
SHARED = HOME / "iCloudPhotos" / "Shared"
BACKUP = pe.BACKUP          # single source of truth (C:\Users\<you>\Backup)
SKIP = {"Houston Trip", "New Zealand", "tomorrowland x bvi"}
IMG = {".jpg", ".jpeg", ".heic", ".heif", ".png"}
VID = {".mp4", ".mov", ".m4v"}
VIDEO_INDEX = Path(__file__).resolve().parent.parent / ".tmp" / "photo_editor" / "videos_index.json"
WINDOW_PAD_H = 12          # hours of slack around a trip's photo date range
RECALL, OFFLINE = 0x400000, 0x1000

_lock = threading.Lock()
_status = {"albums": {}, "started": time.time(), "updated": 0, "running": True}


def set_background_priority():
    """Run below interactive apps. The heavy CPU during a backup is Apple's
    iCloudPhotos service (measured ~190% of a core) doing the hydration, not
    this script (~20%) — but yielding costs nothing and keeps the machine
    responsive while it grinds through thousands of photos."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        # HANDLE is 64-bit: without these the pseudo-handle gets truncated and
        # every call silently fails (it did — GetPriorityClass returned 0).
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k.GetPriorityClass.argtypes = [ctypes.c_void_p]
        BELOW_NORMAL = 0x00004000
        h = k.GetCurrentProcess()
        # BELOW_NORMAL only: PROCESS_MODE_BACKGROUND_BEGIN also throttles I/O
        # hard, which would slow the downloads this script exists to do.
        if not k.SetPriorityClass(h, BELOW_NORMAL):
            return False
        return k.GetPriorityClass(h) == BELOW_NORMAL      # verify, don't assume
    except Exception:
        return False


def is_cloud(p):
    try:
        return bool(getattr(p.stat(), "st_file_attributes", 0) & (RECALL | OFFLINE))
    except OSError:
        return False


def was_edited(orig_path, meta_cache):
    """iOS keeps the pre-edit frame beside the edited one as <stem>_Original.<ext>.
    Failing that, a modify-date later than the capture date means the pixels
    were changed after the shutter."""
    stem, ext = os.path.splitext(orig_path.name)
    if (orig_path.parent / f"{stem}_Original{ext}").exists():
        return True
    try:
        from PIL import Image
        with Image.open(orig_path) as im:
            ex = im.getexif()
            sub = {}
            try:
                sub = dict(ex.get_ifd(0x8769))
            except Exception:
                pass
            shot = str(sub.get(36867) or ex.get(36867) or "")[:19]
            mod = str(ex.get(306) or "")[:19]
            if shot and mod and mod > shot:
                return True
    except Exception:
        pass
    return False


def status_write():
    """Merge into the file rather than replacing it. More than one process
    touches this — the watcher, a manual repair run, a one-off verify — and a
    blind overwrite silently erases whatever the others recorded. That is how a
    verified album went back to reading 'verifying...' on the library screen."""
    BACKUP.mkdir(parents=True, exist_ok=True)
    meta = BACKUP / "_meta"
    meta.mkdir(exist_ok=True)
    with _lock:
        _status["updated"] = time.time()
        out = dict(_status)
        try:
            disk = json.loads((meta / "status.json").read_text(encoding="utf-8"))
        except Exception:
            disk = {}
        albums = dict(disk.get("albums") or {})
        for name, mine in _status["albums"].items():
            merged = dict(albums.get(name) or {})
            merged.update(mine)
            # a verify this process never ran stays as whoever ran it left it
            if "verify" not in mine and "verify" in (albums.get(name) or {}):
                merged["verify"] = albums[name]["verify"]
            albums[name] = merged
        out["albums"] = albums
        write_json(meta / "status.json", out)


def write_json(path, data, tries=6):
    """Atomic write that survives another process reading the same file.

    On Windows, replacing a file that something else has open raises WinError
    32 or 5. That killed Greece's whole photo pass at its very last line, after
    every copy had already succeeded, and then killed the watcher outright when
    the library page happened to be polling status.json. Retry briefly, never
    share a temp name between processes, and never let a status update take
    down a backup that is working."""
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        return False
    for i in range(tries):
        try:
            tmp.replace(path)
            return True
        except OSError:
            time.sleep(0.25 * (i + 1))
    try:                        # last resort: leave the data somewhere findable
        tmp.replace(path.with_suffix(".recovered.json"))
    except OSError:
        pass
    return False


# A copy is abandoned only when it STOPS MAKING PROGRESS, never on elapsed
# time alone. A flat 150s deadline killed 16 of every 20 copies here: iCloud
# was hydrating them fine, just slower than that, and each abandoned attempt
# threw away every byte it had fetched so the next pass started over. Watch
# the bytes instead - a transfer that is still growing is still working.
STALL_S = 120              # partial write frozen this long = actually wedged
HARD_MAX_S = 900           # backstop so nothing can hold a worker forever
# Measured queue wait for a 1.17 MB photo under load: 287s before a single
# byte arrived. A flat cap that is generous for photos is still too tight for
# a 40 MB video sitting in the same queue, so the cap grows with file size -
# the queue wait is paid regardless, the transfer itself scales with bytes.
HARD_MAX_PER_MB_S = 30     # extra seconds of patience per MB of source
# Budget for an EXPLICIT hydration (CfHydratePlaceholder). Measured: photos
# land in 3-8s, a 100 MB video in ~1-2 min. 120s flat + 3s/MB covers that with
# room; a file still missing at that point is being refused, not served.
HYDRATE_BUDGET_S = 120
HYDRATE_PER_MB_S = 3
_orphans = 0

# ---------------------------------------------------------------------------
# CloudKit throttle back-off.
#
# Apple Technote TN3162 ("Understanding CloudKit throttles"): when a client
# issues too many CloudKit requests in too short a period, the server returns
# 503 + a retry-after and REFUSES EVERYTHING until it expires - and "turning
# iCloud off and on again has no effect on the retry interval, and could
# trigger further throttling." That is the state seen repeatedly today:
# iCloudPhotos pinned at 100% CPU, zero files landing, every hydration - even
# a 0.3 MB photo - timing out, for 10-30 minutes, then it clears by itself.
#
# Four workers firing a new hydration the instant each one lands (~1000/hr)
# is precisely the "too many requests too fast" pattern. So when hydrations
# start failing in a CLUSTER, treat it as a throttle, not as bad files:
# pause all workers, wait, and retry gently - don't keep hammering, and
# definitely don't restart the service (that made it worse).
# ---------------------------------------------------------------------------
THROTTLE_WINDOW_S = 300        # look at failures over this window
THROTTLE_TRIP = 3              # this many hydration timeouts in the window...
THROTTLE_BACKOFF_S = 180       # ...=> every worker sleeps this long (first time)
THROTTLE_BACKOFF_MAX_S = 900   # doubles each consecutive trip, capped here
_throttle = {"fails": [], "until": 0.0, "backoff": THROTTLE_BACKOFF_S, "trips": 0}
_throttle_lock = threading.Lock()


def _throttle_note_fail():
    """A hydration timed out. If several did recently, trip the breaker."""
    now = time.time()
    with _throttle_lock:
        f = _throttle["fails"]
        f.append(now)
        del f[:-50]
        recent = [t for t in f if now - t < THROTTLE_WINDOW_S]
        if len(recent) >= THROTTLE_TRIP and now >= _throttle["until"]:
            _throttle["until"] = now + _throttle["backoff"]
            _throttle["trips"] += 1
            _throttle["backoff"] = min(_throttle["backoff"] * 2, THROTTLE_BACKOFF_MAX_S)
            _throttle["fails"] = []
            st = _status.setdefault("throttle", {})
            st.update(until=_throttle["until"], trips=_throttle["trips"],
                      backoffS=_throttle["until"] - now)
            try:
                status_write()
            except Exception:
                pass


def _throttle_note_ok():
    """A hydration landed. Forget old failures; ease the back-off down."""
    with _throttle_lock:
        _throttle["fails"] = []
        if _throttle["backoff"] > THROTTLE_BACKOFF_S:
            _throttle["backoff"] = max(THROTTLE_BACKOFF_S, _throttle["backoff"] // 2)


def _throttle_wait():
    """Block the calling worker while a back-off is in force."""
    while True:
        with _throttle_lock:
            left = _throttle["until"] - time.time()
        if left <= 0:
            return
        time.sleep(min(left, 10))

# Files that hit the hard cap, with when. Consulted at plan time so they are
# tried LAST on the next pass instead of first; forgotten after CAPPED_TTL_S
# so a file that was merely unlucky does get a genuine retry later.
_capped = {}
_capped_lock = threading.Lock()
CAPPED_TTL_S = 600         # deferred files come back after 10 min, not an
                           # hour: with the queue empty the watcher has
                           # nothing better to do than retry them, and
                           # each retry now gets a longer budget
_CAPPED_FILE = BACKUP / "_meta" / "capped.json"


def _load_capped():
    """Persisted, because a watcher restart otherwise forgets every jammed
    file and re-parks all its workers on them for another full cap cycle."""
    global _capped
    try:
        _capped = json.loads(_CAPPED_FILE.read_text(encoding="utf-8"))
    except Exception:
        _capped = {}


def _attempts(path):
    """How many times this file has already blown its hydration budget."""
    with _capped_lock:
        v = _capped.get(str(path))
        if isinstance(v, dict):
            return v.get("n", 0)
        return 1 if v else 0


def _note_capped(path):
    with _capped_lock:
        k = str(path)
        prev = _capped.get(k)
        n = (prev.get("n", 0) if isinstance(prev, dict) else (1 if prev else 0)) + 1
        _capped[k] = {"t": time.time(), "n": n}
        try:
            _CAPPED_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CAPPED_FILE.write_text(json.dumps(_capped), encoding="utf-8")
        except OSError:
            pass


def _recently_capped(path):
    with _capped_lock:
        v = _capped.get(str(path))
        if v is None:
            return False
        t = v.get("t", 0) if isinstance(v, dict) else v
        if time.time() - t > CAPPED_TTL_S:
            del _capped[str(path)]
            return False
        return True


_load_capped()

# A thread that hits the backstop does NOT stop - Python threads cannot be
# killed in Python, so it keeps running invisibly, competing with real
# workers for cloud bandwidth until Windows' own read eventually gives up.
#
# A semaphore capping total concurrent attempts was tried here and made
# things WORSE: once the cap filled with zombies, every real worker blocked
# waiting for a slot that could take up to HARD_MAX_S to free, turning a slow
# leak into a hard stop - Kosovo's failure rate filled it in about 13 minutes
# and froze the watcher completely (0% CPU, 0 connections). Reverted.
#
# What is left is just a short leash: 300s bounds how long any one zombie can
# drag on, so a bad stretch degrades gradually instead of compounding forever
# AND instead of walling off entirely. It does not fix the leak - only a
# process-based hydration (so a stuck copy can be actually killed, not just
# abandoned) would - so periodic restarts are still the real mitigation.


# ---------------------------------------------------------------------------
# Explicit hydration via the Windows Cloud Files API.
#
# Everything above this line fights the symptoms of ONE root cause: the
# backup pulled cloud-only originals by simply READING them (shutil.copy2) and
# hoping Windows would download them on the way past. Microsoft's own Cloud
# Files FAQ says exactly that is the wrong way at scale - "relying on
# copy-driven access to trigger hydration can be unreliable... providers
# should explicitly make data available rather than depending on the copy
# operation itself." Read-triggered hydration is opportunistic; it can block
# for 20+ minutes, or return every byte and then never release the handle.
#
# CfHydratePlaceholder is the sanctioned way: ask the filter driver to fetch
# the whole file, it returns S_OK when the data is on disk, THEN copy what is
# now a plain local file in milliseconds. Measured on IMG_4368(1).HEIC - a
# file read-hydration had jammed on for 20+ min - explicit hydration finished
# in 56s and the follow-up read took 0.01s.
#
# Windows 10 1709+ only; if CldApi.dll is missing we fall back to the old
# read-driven copy, so nothing breaks elsewhere.
# ---------------------------------------------------------------------------
import ctypes

try:
    _cld = ctypes.WinDLL("CldApi")
    _cld.CfHydratePlaceholder.argtypes = [ctypes.c_void_p, ctypes.c_longlong,
                                          ctypes.c_longlong, ctypes.c_uint,
                                          ctypes.c_void_p]
    _cld.CfHydratePlaceholder.restype = ctypes.c_long
    _k32 = ctypes.windll.kernel32
    _k32.CreateFileW.restype = ctypes.c_void_p
    _k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint,
                                 ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                                 ctypes.c_void_p]
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
    HYDRATE_AVAILABLE = True
except (OSError, AttributeError):
    _cld = None
    HYDRATE_AVAILABLE = False

_INVALID_HANDLE = ctypes.c_void_p(-1).value


def hydrate(path, timeout_s):
    """Explicitly pull a cloud placeholder's bytes onto disk. Returns True on
    success, False on failure/timeout. Runs the (blocking) API in a daemon
    thread so a genuinely wedged hydration can be abandoned after timeout_s
    without holding the worker."""
    if not HYDRATE_AVAILABLE:
        return False
    out = {}

    def run():
        GENERIC_READ = 0x80000000
        SHARE_RW = 0x1 | 0x2
        OPEN_EXISTING = 3
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        h = _k32.CreateFileW(str(path), GENERIC_READ, SHARE_RW, None,
                             OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if not h or h == _INVALID_HANDLE:
            out["err"] = f"CreateFileW failed ({ctypes.GetLastError()})"
            return
        try:
            hr = _cld.CfHydratePlaceholder(h, 0, -1, 0, None)   # -1 == CF_EOF
            out["hr"] = hr & 0xFFFFFFFF
        finally:
            _k32.CloseHandle(h)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    # Wait in slices, writing a heartbeat as we go. A single long join() left
    # status.json untouched for the whole hydration, so anything reading it -
    # the library's Live Activity panel - decided the watcher was dead and
    # showed nothing, while four workers were in fact busy. (The heartbeat
    # added to copy_deadline does not help here: explicit hydration never
    # goes through that function.)
    began = last_beat = time.time()
    while t.is_alive():
        t.join(5)
        now = time.time()
        if now - last_beat > 30:
            last_beat = now
            try:
                status_write()
            except Exception:
                pass
        if now - began > timeout_s:
            return False                               # abandoned; see leak note
    return out.get("hr") == 0


def fetch(srcf, tmp):
    """The one way a file gets from iCloud into the backup.

    1. If the source is a cloud placeholder, hydrate it EXPLICITLY first
       (CfHydratePlaceholder). This is the documented, reliable path and
       what actually fixed the 20-minute jams.
    2. Then copy - which for a hydrated file is a local copy and takes ms.

    If explicit hydration is unavailable or fails within its budget, fall
    through to the old read-driven copy_deadline so the file still gets one
    honest attempt the old way rather than being skipped.
    """
    if HYDRATE_AVAILABLE and is_cloud(srcf):
        try:
            mb = srcf.stat().st_size / 1e6
        except OSError:
            mb = 0
        # Explicit hydration is fast when it works (photos: seconds, a 100 MB
        # video: a couple of minutes). A file that has not come back after a
        # generous fixed budget is one the sync engine is refusing to serve
        # right now - hanging a worker on it for 45 min (the old MB-scaled cap
        # on a 60 MB video) does not make it arrive, it just starves every
        # file behind it. Give up sooner, defer it, move on.
        # Each failed attempt buys the next one more time. A 66 MB Albania
        # video kept dying at exactly its 318s budget - it was still coming
        # down, just slower than the formula allowed - and every retry
        # repeated the identical deadline. Escalate to 3x over four tries.
        budget = (HYDRATE_BUDGET_S + HYDRATE_PER_MB_S * mb) * (
            1 + 0.5 * min(_attempts(srcf), 4))
        # If CloudKit has throttled us, every worker waits here. Hammering a
        # throttled endpoint extends the blackout (TN3162); patience ends it.
        _throttle_wait()
        # Tell the library page what is hydrating. The page used to infer
        # activity from .part files, but with explicit hydration the copy
        # takes milliseconds, so the .part barely exists - the slow, visible
        # part is now the hydration itself, which leaves no file behind.
        _inflight_add(srcf, tmp, mb)
        try:
            ok = hydrate(srcf, budget)
        finally:
            _inflight_remove(srcf)
        if ok:
            _throttle_note_ok()
            shutil.copy2(srcf, tmp)          # now local: fast, no deadline needed
            return True
        # Did not hydrate in budget. Do NOT fall into the slow read-driven
        # path - that just parks the worker on the same file for another 15+
        # minutes. Mark it so the next pass tries it LAST, and fail fast so
        # the worker moves on to files that will actually land.
        #
        # Only failures on MAIN-LIBRARY originals count toward the throttle
        # breaker: those are served by iCloudPhotos/CloudKit, where a cluster
        # of timeouts means "slow down". Files under the SHARED folder are
        # served by ApplePhotoStreams, and their timeouts mean that agent is
        # dead again - backing off everything for 15 minutes doesn't revive
        # it, it just pauses the library work that would have succeeded.
        if not srcf.is_relative_to(SHARED):
            _throttle_note_fail()
        _note_capped(srcf)
        raise TimeoutError(f"hydration did not complete in {budget:.0f}s ({mb:.1f} MB)")
    return copy_deadline(srcf, tmp)


# In-flight hydrations, for the UI. Written to a small JSON beside status.json.
_inflight = {}
_inflight_lock = threading.Lock()
_INFLIGHT_FILE = BACKUP / "_meta" / "inflight.json"


def _inflight_write():
    try:
        _INFLIGHT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _INFLIGHT_FILE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(list(_inflight.values())), encoding="utf-8")
        tmp.replace(_INFLIGHT_FILE)
    except OSError:
        pass


def _inflight_add(srcf, tmp, mb):
    with _inflight_lock:
        _inflight[str(srcf)] = {
            "name": srcf.name, "album": tmp.parent.name if tmp.parent.name != "videos"
            else tmp.parent.parent.name, "mb": round(mb, 2),
            "since": time.time(), "pid": os.getpid(), "phase": "hydrating",
        }
        _inflight_write()


def _inflight_remove(srcf):
    with _inflight_lock:
        _inflight.pop(str(srcf), None)
        _inflight_write()


def copy_deadline(srcf, tmp, stall_s=STALL_S, hard_max_s=HARD_MAX_S):
    """shutil.copy2 that gives up WAITING once a transfer stalls.

    Reading a cloud placeholder blocks until Windows has pulled the file down,
    and a wedged one can sit for 15-20 minutes before erroring. A thread
    cannot be killed, so the copy runs in a daemon thread we stop waiting on;
    it keeps running and unwinds by itself later, still using bandwidth in
    the meantime - a real limitation this function cannot fully solve, only
    bound with hard_max_s. Each attempt writes its OWN temp file, so an
    orphan finishing late can never scribble over a retry.

    CRITICAL: "size stopped growing" does NOT mean stalled. Every byte can be
    written to the destination while copyfile has not yet RETURNED - the final
    read blocks while Windows finalises the hydration. Measured: a 1.84 MB
    photo wrote 100% of its bytes early, then copyfile took 252s total to
    return (copystat was 0.00s, so the delay is entirely inside copyfile).
    Treating that flat size as a stall aborted hundreds of copies that were
    already complete, and every retry hit the exact same wall - which is what
    made small-file albums like Kosovo look permanently broken. So once the
    temp file reaches the source size, the byte transfer IS done: stop
    applying the stall timeout and let it finish, bounded only by hard_max_s.
    """
    global _orphans
    out = {}
    try:
        src_size = srcf.stat().st_size
    except OSError:
        src_size = 0
    # bigger file, more patience: the queue wait is fixed, the transfer is not
    hard_max_s = hard_max_s + HARD_MAX_PER_MB_S * (src_size / 1e6)

    def run():
        try:
            shutil.copy2(srcf, tmp)
            out["ok"] = True
        except BaseException as e:           # noqa: BLE001 - reported to caller
            out["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    began = time.time()
    last_size, last_move = -1, time.time()
    last_beat = began
    while t.is_alive():
        t.join(5)
        try:
            size = tmp.stat().st_size
        except OSError:
            size = 0
        now = time.time()
        # Heartbeat while waiting. status.json was only written on file
        # completion, so during iCloud's 5+ minute queue wait the watcher
        # looked dead to anything reading it (the library's Live Activity
        # panel went blank and called it "not running"). A cheap periodic
        # write keeps "alive" and "idle" distinguishable from "hung".
        if now - last_beat > 30:
            last_beat = now
            try:
                status_write()
            except Exception:
                pass
        # The byte-level shape of a cloud hydration, measured on a 1.17 MB
        # photo: 0 bytes for 286s, then the ENTIRE file lands at once, then
        # copyfile returns. Windows hands over nothing until the whole file
        # is fetched from Apple, and that fetch queues behind everything else
        # iCloud is doing. So a flat size means one of only two things, and
        # NEITHER is a stall we should abort:
        #   size == 0          waiting in iCloud's queue (the normal state for
        #                      almost the whole transfer - a 120s "no progress"
        #                      timeout here killed copies 3+ minutes before
        #                      their bytes would ever have arrived, which is
        #                      why retries could never succeed)
        #   size >= src_size   all bytes across, copyfile is finalising
        # Only a PARTIAL write that then freezes is a real stall. Everything
        # else runs to the hard cap.
        queued = size == 0
        finalising = src_size and size >= src_size
        if size > last_size:                 # still pulling bytes down
            last_size, last_move = size, now
        elif now - began > hard_max_s or (
                not queued and not finalising and now - last_move > stall_s):
            with _lock:
                _orphans += 1
            if now - began > hard_max_s:
                _note_capped(srcf)           # try this one LAST next pass
            phase = "queued" if queued else "finalising" if finalising else "no progress"
            raise TimeoutError(
                f"{phase} for {now - last_move:.0f}s at {size/1e6:.1f} MB")
    if "err" in out:
        raise out["err"]
    return True


def sweep_parts(dest, older_than_min=30):
    """Delete abandoned .part files.

    A copy that blows its deadline is abandoned, not killed - the thread keeps
    running and eventually leaves its temp file behind. Anything older than a
    deadline can no longer be a live transfer; a file the OS still has open
    simply refuses to delete, which is the outcome we want.
    """
    cut = time.time() - older_than_min * 60
    gone = 0
    for d in (dest, dest / "videos"):
        if not d.is_dir():
            continue
        for p in d.glob("*.part"):
            try:
                if p.stat().st_ctime < cut:
                    p.unlink()
                    gone += 1
            except OSError:
                pass                        # still open, or already gone
    return gone


def load_manifest(path):
    """{"files": {backup name: meta}, "entries": {album item: backup name}}.
    Older manifests were keyed by backup name with a single "from" field, which
    could not represent two album entries of the same photo — migrate them."""
    if not path.exists():
        return {"files": {}, "entries": {}}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"files": {}, "entries": {}}
    if "files" in d and "entries" in d:
        return d
    files, entries = {}, {}
    for fname, rec in d.items():
        files[fname] = rec
        if rec.get("from"):
            entries[rec["from"]] = fname
    return {"files": files, "entries": entries}


def unique_name(base, claimed):
    """A distinct filename per ALBUM ENTRY. Apple keeps duplicate entries of
    the same shot; each one gets its own file so the backup count matches the
    album count exactly instead of silently merging."""
    if base not in claimed:
        return base
    stem, ext = os.path.splitext(base)
    i = 2
    while f"{stem}-{i}{ext}" in claimed:
        i += 1
    return f"{stem}-{i}{ext}"


def stem_key(name):
    """The distinct-photo identity of an album entry.

    A shared-album filename is a content hash, and Apple emits the same photo
    twice - "<hash>.jpg" beside "<hash>_00001.jpg". Strip that copy suffix and
    one stem is one real photo: Peru's 1613 entries collapse to 883 stems, and
    883 is exactly the count on the phone. The hash is stable across rebuild
    rounds, so a round-3 entry matches the file backed up in round 1.
    """
    return re.sub(r"_\d+$", "", Path(name).stem)


def canonical(name):
    """The backup folder an album belongs in, with iCloud's rebuild twins
    mapped home.

    A shared-albums rebuild recreates every album as "<Name>_1" and later
    retires the original folder. At that point "<Name>_1" IS the album - but
    backing it up under the twin name would re-download ~14k files into
    duplicate folders. If a backup for the base name already exists, the twin
    inherits it: same photos, same manifest, everything already there gets
    skipped as usual. Applied everywhere a backup path is derived from an
    album name, plus the expected/archived lookups in the editor."""
    m = re.match(r"^(.+)_\d+$", name)
    if m:
        base = m.group(1)
        # Map the twin home if EITHER an existing backup or the base album
        # folder is there. Checking only the backup fails for a brand-new
        # album that first appears as a twin: Italy's real folder was
        # "Italy (2023)_1" (the original arrived empty), so with no
        # "Italy (2023)" backup to find, it started writing a backup folder
        # literally called "Italy (2023)_1" - a rebuild artefact baked into
        # the archive's directory names forever.
        if (BACKUP / base).is_dir() or (SHARED / base).is_dir():
            return base
    return name


def backup_album(name, workers=4, verbose=True):
    src = SHARED / name
    dest = BACKUP / canonical(name)
    dest.mkdir(parents=True, exist_ok=True)
    photos = sorted(p for p in src.iterdir()
                    if p.is_file() and p.suffix.lower() in IMG)

    manifest_path = dest / "_manifest.json"
    man = load_manifest(manifest_path)
    files, entries = man["files"], man["entries"]

    st = _status["albums"].setdefault(canonical(name), {})
    st.update(total=len(photos), done=0, copied=0, skipped=0, fallback=0,
              failed=0, bytes=0, state="running", started=time.time())
    status_write()

    # plan first (single-threaded) so filename assignment can't race
    claimed = set(entries.values())
    plan, deferred = [], []

    # Existing backups grouped by the ORIGINAL they came from. Every rebuild
    # round renames album entries (the hash filenames change), so an entry
    # from round 3 is not in `entries` even though its photo was backed up in
    # round 1 - and unique_name() then happily wrote IMG_1301-2.HEIC beside
    # IMG_1301.HEIC. Nicaragua reached 662 files for 381 distinct photos that
    # way. Match on the original instead of the album's throwaway entry name:
    # a photo already on disk is reused, not copied again.
    pool = {}                                # stem -> the one file for that photo
    for f, rec in files.items():
        frm = rec.get("from")
        if frm and (dest / f).exists():
            pool.setdefault(stem_key(frm), f)
    # Originals already spoken for. find_original() matches on capture SECOND
    # and returns the largest file at that second, so every frame of a burst
    # resolves to the SAME original - five distinct shots would all be "backed
    # up" as one photo. An original therefore belongs to exactly one stem.
    taken = {rec["orig"].lower() for rec in files.values() if rec.get("orig")}
    used = set(entries.values())

    for sp in photos:
        assigned = entries.get(sp.name)
        if assigned and (dest / assigned).exists():
            plan.append((sp, None, assigned))
            continue
        # ONE FILE PER DISTINCT PHOTO. Identity is the album entry's stem: the
        # filename is a content hash, and Apple emits the same photo twice as
        # "<hash>.jpg" and "<hash>_00001.jpg". Strip that suffix and the stem
        # count matches the phone exactly - Peru 1613 entries -> 883 stems and a
        # phone count of 883; Turkey 1711 -> 912 -> 912.
        #
        # This used to key on the ORIGINAL's filename, which quietly merged
        # unrelated photos: iPhone filenames recycle (many different photos are
        # called IMG_3736.jpg) and a burst shares one original. 4,482 entries
        # ended up pointing at another photo's file, and because every entry
        # still resolved to SOMETHING, verify reported the album fully covered.
        key = stem_key(sp.name)
        existing = pool.get(key)
        if existing:
            entries[sp.name] = existing      # Apple's twin -> the same file
            plan.append((sp, None, existing))
            continue
        orig = pe.find_original(sp)
        if orig and str(orig).lower() in taken:
            orig = None                      # a different photo already owns it
        # No original of its own: keep the 2048px album copy. A smaller file of
        # the RIGHT photo beats a full-res file of the wrong one.
        srcf = orig if orig else sp
        if orig:
            taken.add(str(orig).lower())
        target = unique_name(srcf.name, claimed)
        claimed.add(target)
        used.add(target)
        pool[key] = target
        # A file that just hit the hard cap (iCloud wrote every byte but never
        # released the handle) goes to the BACK of the line. Otherwise, because
        # the plan is sorted, the next pass grabs the same jammed files first,
        # every worker re-parks on them, and the whole album freezes while
        # hundreds of copyable files sit behind. Seen: 5 such files held all
        # 4 workers for 20+ min with the pass counter frozen at 231/710.
        if _recently_capped(srcf):
            deferred.append((sp, srcf, target))
        else:
            plan.append((sp, srcf, target))
    plan.extend(deferred)

    def one(item):
        sp, srcf, target = item
        try:
            tpath = dest / target
            if srcf is None:                  # already backed up
                with _lock:
                    st["skipped"] += 1
            else:
                if not tpath.exists() or tpath.stat().st_size == 0:
                    # unique per attempt: a timed-out copy may still be
                    # writing its own temp file minutes from now
                    tmp = dest / f"{target}.{os.getpid()}.{threading.get_ident()}.part"
                    fetch(srcf, tmp)
                    tmp.replace(tpath)
                    with _lock:
                        st["copied"] += 1
                        st["bytes"] += tpath.stat().st_size
                else:
                    with _lock:
                        st["skipped"] += 1
                full = srcf.parent != src
                rec = {"from": sp.name, "full": full, "size": tpath.stat().st_size}
                if full:
                    # which original this photo consumed, so a later pass can
                    # not hand the same one to a different photo
                    rec["orig"] = str(srcf)
                try:
                    from PIL import Image
                    with Image.open(tpath) as im:
                        rec["w"], rec["h"] = im.size
                except Exception:
                    pass
                rec["edited"] = was_edited(tpath, None)
                with _lock:
                    files[target] = rec
                    entries[sp.name] = target
                    if not full:
                        st["fallback"] += 1
            # grid thumbnail while the file is hot (download is network-bound)
            try:
                cp = pe.bthumb_path(tpath, 400)
                if not cp.exists():
                    from PIL import Image, ImageOps
                    with Image.open(tpath) as im:
                        im = ImageOps.exif_transpose(im)
                        icc = im.info.get("icc_profile")
                        im.thumbnail((400, 400))
                        kw = {"quality": 82}
                        if icc:
                            kw["icc_profile"] = icc
                        im.convert("RGB").save(cp, "JPEG", **kw)
            except Exception:
                pass
        except Exception as e:
            with _lock:
                st["failed"] += 1
                errs = st.setdefault("errors", [])
                errs.append(f"{sp.name}: {e}"[:180])
                # keep a sample, not a ledger: 2,365 failures once bloated
                # status.json enough to truncate the API response and blank
                # the library page. The count in st["failed"] is the record.
                del errs[:-20]
        finally:
            with _lock:
                st["done"] += 1
                flush = st["done"] % 10 == 0
            if flush:
                status_write()
                with _lock:
                    snap = {"files": dict(files), "entries": dict(entries)}
                write_json(manifest_path, snap)

    sweep_parts(dest)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, plan))

    st["state"] = "done"
    st["seconds"] = round(time.time() - t0, 1)
    write_json(manifest_path, {"files": files, "entries": entries})
    status_write()
    if verbose:
        print(f"  {name}: {st['copied']} copied ({st['bytes']/1e6:.0f} MB), "
              f"{st['skipped']} already had, {st['fallback']} no-original, "
              f"{st['failed']} failed  ->  {len(entries)} files for {len(photos)} album photos")
    return st


def mp4_duration(path):
    """Seconds, read from the MP4/MOV mvhd atom. Apple re-encodes a shared
    album's videos (destroying their capture date) but the DURATION survives,
    so it identifies which library original a copy came from."""
    import struct
    try:
        with open(path, "rb") as f:
            data = f.read(500000)
        i = data.find(b"mvhd")
        if i < 0:
            return None
        ver = data[i + 4]
        off = i + 8
        if ver == 1:
            off += 16
            ts = struct.unpack(">I", data[off:off + 4])[0]
            dur = struct.unpack(">Q", data[off + 4:off + 12])[0]
        else:
            off += 8
            ts = struct.unpack(">I", data[off:off + 4])[0]
            dur = struct.unpack(">I", data[off + 4:off + 8])[0]
        return dur / ts if ts else None
    except Exception:
        return None


def album_window(name):
    """(first, last) capture time of an album's photos. Used only to break
    ties when several library clips share a duration — the album folder, not
    the date, decides which videos belong to a country."""
    from datetime import datetime
    times = []
    for p in (SHARED / name).iterdir():
        if p.is_file() and p.suffix.lower() in IMG:
            ks = pe.capture_key(p)
            if ks:
                try:
                    times.append(datetime.strptime(ks[0], "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    pass
    return (min(times), max(times)) if times else (None, None)


def library_videos():
    """[(path, seconds, datetime)] for every full-res video in the library."""
    from datetime import datetime
    try:
        idx = json.loads(VIDEO_INDEX.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    lib = Path(idx["library"])
    out = []
    for v in idx.get("videos", []):
        dur = (v.get("dur") or 0) / 1e7          # 100-ns ticks -> seconds
        try:
            t = datetime.strptime(v["t"], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            t = None
        out.append((lib / v["n"], dur, t))
    return out


def backup_videos(name, workers=3, verbose=True):
    """One backed-up video per video IN THE ALBUM FOLDER — the album is the
    identifier. Each is upgraded to its full-resolution library original when
    one can be identified (matched on DURATION, which survives Apple's
    re-encode); otherwise the album's own copy is kept so nothing is lost.
    Anything already downloaded is left alone."""
    from datetime import timedelta
    st = _status["albums"].setdefault(canonical(name), {})
    src = SHARED / name
    if not src.is_dir():
        st["vidState"] = "album no longer on this PC"
        status_write()
        return st
    album_vids = sorted(p for p in src.iterdir()
                        if p.is_file() and p.suffix.lower() in VID)
    dest = BACKUP / canonical(name) / "videos"
    dest.mkdir(parents=True, exist_ok=True)

    lo, hi = album_window(name)
    lib = library_videos()
    if lo:
        lo, hi = lo - timedelta(hours=WINDOW_PAD_H), hi + timedelta(hours=WINDOW_PAD_H)

    man_path = dest / "_manifest.json"
    man = {}
    if man_path.exists():
        try:
            man = json.loads(man_path.read_text(encoding="utf-8"))
        except Exception:
            man = {}

    st.update(vidTotal=len(album_vids), vidDone=0, vidBytes=0, vidFailed=0,
              vidFull=0, vidCopy=0, vidState="running")
    status_write()

    claimed = set()
    plan, deferred_v = [], []

    # Same rebuild trap as the photo planner: each round renames the album's
    # video entries, so a round-3 entry is absent from the manifest even
    # though its clip was backed up in round 1, and unique_name() would write
    # IMG_0246-2.MOV beside IMG_0246.MOV. Match on the resolved original and
    # adopt a copy that already exists instead of making another.
    vpool = {}                               # stem -> the one file for that clip
    for ename, r in man.items():
        if isinstance(r, dict) and r.get("file") and (dest / r["file"]).exists():
            vpool.setdefault(stem_key(ename), r["file"])
    # Originals already claimed. A clip is matched to the library by DURATION
    # (+-0.25s), and plenty of unrelated clips are the same length, so without
    # this two different videos would be "backed up" as one file.
    vtaken = {r["orig"].lower() for r in man.values()
              if isinstance(r, dict) and r.get("orig")}
    vused = {r["file"] for r in man.values() if isinstance(r, dict) and r.get("file")}
    # backup filename -> the record that describes it, so an alias entry can
    # inherit the real size/full flag instead of inventing one
    vrec = {r["file"]: r for r in man.values()
            if isinstance(r, dict) and r.get("file")}

    for p in album_vids:
        rec = man.get(p.name)
        # reuse an existing file only if no EARLIER album entry already claimed
        # it — otherwise this entry needs its own copy so the counts tie
        if rec and (dest / rec["file"]).exists() and rec["file"] not in claimed:
            claimed.add(rec["file"])
            plan.append((p, None, rec["file"]))
            continue
        # one file per distinct clip - see stem_key(). Apple's duplicate entry
        # of the same clip shares the file; a genuinely different clip never
        # does, even when the duration match points at the same original.
        key = stem_key(p.name)
        existing = vpool.get(key)
        if existing:
            # The owner may only be PLANNED, not copied yet - vpool is filled
            # as the plan is built, so an alias can point at a file that will
            # not exist for another few minutes. stat()-ing it here killed the
            # whole watcher with FileNotFoundError and took every album after
            # Bali with it. Inherit the owner's record when there is one, and
            # otherwise leave the size to be filled in after the copies run.
            own = vrec.get(existing)
            man[p.name] = dict(own) if own else {
                "file": existing, "full": False, "size": 0}
            man[p.name]["file"] = existing
            plan.append((p, None, existing))
            continue
        dur = mp4_duration(p)
        best = None
        if dur:
            for path, ldur, t in lib:
                if not ldur or abs(ldur - dur) > 0.25:
                    continue
                if str(path).lower() in vtaken:
                    continue                 # another clip already owns it
                score = (0 if (lo and t and lo <= t <= hi) else 1, abs(ldur - dur))
                if best is None or score < best[0]:
                    best = (score, path)
        orig = best[1] if best else None
        if orig:
            vtaken.add(str(orig).lower())
        srcname = (orig or p).name
        target = unique_name(srcname, claimed)
        claimed.add(target)
        vused.add(target)
        vpool[key] = target
        # same deferral the photo planner uses: a video whose hydration just
        # timed out goes to the BACK of the line, otherwise the next pass
        # re-grabs the identical 4 hung files first and parks every worker on
        # them again (exactly what happened - see the capped list)
        if orig is not None and _recently_capped(orig):
            deferred_v.append((p, orig, target))
        else:
            plan.append((p, orig, target))
    plan.extend(deferred_v)

    def one(item):
        album_file, orig, target_name = item
        try:
            if orig is None and album_file.name in man and (dest / target_name).exists() \
                    and man[album_file.name].get("file") == target_name:
                with _lock:
                    st["vidFull" if man[album_file.name].get("full") else "vidCopy"] += 1
                return
            srcf = orig or album_file              # fall back to the album copy
            target = dest / target_name
            if not target.exists() or target.stat().st_size == 0:
                tmp = dest / f"{target_name}.{os.getpid()}.{threading.get_ident()}.part"
                fetch(srcf, tmp)
                tmp.replace(target)
            with _lock:
                man[album_file.name] = {"file": target_name, "full": orig is not None,
                                        "size": target.stat().st_size}
                if orig is not None:
                    man[album_file.name]["orig"] = str(orig)
                st["vidBytes"] += target.stat().st_size
                st["vidFull" if orig else "vidCopy"] += 1
        except Exception as e:
            with _lock:
                st["vidFailed"] += 1
                ve = st.setdefault("vidErrors", [])
                ve.append(f"{album_file.name}: {e}"[:160])
                del ve[:-20]                  # a sample, not a ledger
        finally:
            with _lock:
                st["vidDone"] += 1
                flush = st["vidDone"] % 5 == 0
            if flush:
                status_write()

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, plan))
    # Aliases recorded before their owner was copied carry size 0. The file
    # exists now, so give them the real numbers rather than leaving a manifest
    # that says a 300 MB clip is empty.
    for ename, r in man.items():
        if isinstance(r, dict) and not r.get("size") and r.get("file"):
            f = dest / r["file"]
            if f.exists():
                own = next((x for x in man.values()
                            if isinstance(x, dict) and x.get("file") == r["file"]
                            and x.get("size")), None)
                r["size"] = f.stat().st_size
                if own:
                    r["full"] = own.get("full", r.get("full", False))
    write_json(man_path, man)
    st["vidState"] = "done"
    st["vidSeconds"] = round(time.time() - t0, 1)
    status_write()
    if verbose:
        print(f"  {name}: videos {st['vidDone']}/{len(album_vids)} "
              f"({st['vidFull']} full-res, {st['vidCopy']} album copies, "
              f"{st['vidFailed']} failed)")
    return st


EXPECTED = BACKUP / "_meta" / "expected.json"


def expected_count(name):
    """The item count YOU read off your iPhone for this album. It outranks
    every local number: the album FOLDER only shows what iCloud has pushed so
    far, and iCloud pauses albums mid-upload for hours."""
    try:
        return json.loads(EXPECTED.read_text(encoding="utf-8")).get(name)
    except Exception:
        return None


def verify_album(name, verbose=True):
    """Prove the backup holds every item, measured against your iPhone's count.

    Two tests have to pass, and the second is the one that matters:
      1. every entry in the album folder owns its own backed-up file
      2. the number of backed-up files equals the count you entered

    Test 1 alone can pass on a half-synced album, because the folder only
    contains what iCloud has delivered so far."""
    src = SHARED / name
    dest = BACKUP / canonical(name)
    if not src.is_dir():
        return None
    photos = [p for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMG]
    vids = [p for p in src.iterdir() if p.is_file() and p.suffix.lower() in VID]

    man = load_manifest(dest / "_manifest.json")
    entries = man["entries"]

    def shortfall(items, resolve, folder):
        """Every album entry must resolve to a file that exists.

        Entries are allowed to SHARE a file now. They used to need one each,
        so the backup count tied with the album count - but Apple's rebuild
        emits the same image twice ("<hash>.jpg" and "<hash>_00001.jpg"), and
        honouring that meant storing byte-identical copies while drifting even
        further from the phone count. One file per distinct photo; the phone
        count is what completeness is measured against."""
        missing = []
        for p in items:
            f = resolve(p.name)
            if not f or not (folder / f).exists():
                missing.append(p.name)
        return missing

    missing_p = shortfall(photos, lambda n: entries.get(n), dest)

    vman = {}
    vm_path = dest / "videos" / "_manifest.json"
    if vm_path.exists():
        try:
            vman = json.loads(vm_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    missing_v = shortfall(vids, lambda n: (vman.get(n) or {}).get("file"),
                          dest / "videos")

    # what is actually sitting in the backup folder, counted from disk
    files = sum(1 for p in dest.iterdir()
                if p.is_file() and p.suffix.lower() in IMG) if dest.is_dir() else 0
    vdir = dest / "videos"
    files += sum(1 for p in vdir.iterdir()
                 if p.is_file() and p.suffix.lower() in VID) if vdir.is_dir() else 0

    exp = expected_count(canonical(name))
    covered = not missing_p and not missing_v
    res = {"photos": f"{len(photos) - len(missing_p)}/{len(photos)}",
           "videos": f"{len(vids) - len(missing_v)}/{len(vids)}",
           "files": files, "expected": exp,
           "albumItems": len(photos) + len(vids),
           "covered": covered,
           # unverified until you tell us what the phone says
           "ok": covered and exp is not None and files >= exp,
           # What iCloud still owes is measured against the ALBUM FOLDER, not
           # the backup. Armenia and Greece carry surplus files from an earlier
           # download round, and counting those made the gap look smaller than
           # it is — Greece read "45 to arrive" when the true figure was 55.
           "shortOfPhone": (exp - (len(photos) + len(vids))) if exp is not None else None,
           "missingPhotos": len(missing_p), "missingVideos": len(missing_v),
           "examples": (missing_p + missing_v)[:5]}
    st = _status["albums"].setdefault(canonical(name), {})
    st["verify"] = res
    if covered:
        # A pass that died partway leaves state="running" behind forever, and
        # the card then reads "downloading 30/522" over a folder that is fully
        # copied. Proof beats whatever the last run happened to write.
        st.update(state="done", done=len(photos), total=len(photos),
                  vidState="done", vidDone=len(vids), vidTotal=len(vids))
    status_write()
    if verbose:
        if res["ok"]:
            flag = f"VERIFIED — {files}/{exp} vs your iPhone"
        elif exp is None:
            flag = f"UNVERIFIED — {files} files, no iPhone count entered"
        elif not covered:
            flag = f"INCOMPLETE — {files} files, album entries not all copied"
        else:
            flag = f"INCOMPLETE — {files}/{exp}, {exp - files} still to arrive from iCloud"
        print(f"  {name}: {flag} (photos {res['photos']}, videos {res['videos']})")
        if res["examples"]:
            print(f"      missing e.g. {res['examples'][:3]}")
    return res


def held():
    """Albums on hold: present on disk but deliberately not backed up yet.

    Reviving Apple's shared-album agent made nine albums appear at once, and
    the watcher would otherwise have started pulling full-resolution originals
    for all of them without anyone deciding that was wanted. Disk space is the
    scarce thing here, so a new album waits for a yes."""
    try:
        return set(json.loads((BACKUP / "_meta" / "hold.json").read_text(encoding="utf-8")))
    except Exception:
        return set()


def album_covered(name):
    """True when every entry in the album folder maps to a file that exists.

    Counting files against album entries stopped working once entries were
    allowed to SHARE a file: an album with duplicate entries will always have
    fewer files than entries, so a count test reads as permanently incomplete
    and the watcher re-runs the same pass forever. Coverage is the real
    question - is there a backed-up file for every entry."""
    src = SHARED / name
    dest = BACKUP / canonical(name)
    if not src.is_dir() or not dest.is_dir():
        return False
    man = load_manifest(dest / "_manifest.json")
    entries = man["entries"]
    # A file may be shared only by Apple's twins of ONE photo. Sharing it
    # across two stems is how 459 photos went missing while every entry still
    # resolved to something and this function happily returned True.
    owner = {}
    for p in src.iterdir():
        if p.is_file() and p.suffix.lower() in IMG:
            f = entries.get(p.name)
            if not f or not (dest / f).exists():
                return False
            k = stem_key(p.name)
            if owner.setdefault(f, k) != k:
                return False
    vman = {}
    vm = dest / "videos" / "_manifest.json"
    if vm.exists():
        try:
            vman = json.loads(vm.read_text(encoding="utf-8"))
        except Exception:
            vman = {}
    vowner = {}
    for p in src.iterdir():
        if p.is_file() and p.suffix.lower() in VID:
            rec = vman.get(p.name)
            f = rec.get("file") if isinstance(rec, dict) else None
            if not f or not (dest / "videos" / f).exists():
                return False
            k = stem_key(p.name)
            if vowner.setdefault(f, k) != k:
                return False       # two different clips on one file
    return True


def albums():
    if not SHARED.is_dir():
        return []
    skip = SKIP | held()
    import re
    def _base(n):
        m = re.match(r"^(.+)_\d+$", n)
        return m.group(1) if m else n

    # a skipped album stays skipped through its rebuild twin too: "New
    # Zealand" is in SKIP but "New Zealand_1" is not, and without this the
    # twin quietly re-enables an album that was deliberately excluded
    names = [d.name for d in SHARED.iterdir()
             if d.is_dir() and d.name not in skip and _base(d.name) not in skip
             and not d.name.startswith("_")]
    # iCloud's shared-album rebuild recreates every album as "<Name>_1" and
    # fills the twin before retiring the original. Backing the twin up as a
    # separate album would re-download all ~14k files into duplicate folders.
    # While the original folder still exists, the twin is a transient copy of
    # the SAME album - skip it. (If iCloud later retires the original, the
    # twin no longer has a base sibling and gets picked up normally; its
    # backup then needs the canonical name - handled in backup_album.)
    # ...but "skip every twin" is wrong once a twin gets AHEAD of its
    # original. Albania's rebuild twin reached 480 items - exactly the iPhone
    # count - while the original sat at 471, so the 9 long-missing items were
    # sitting in a folder we refused to look at.
    #
    # Rule: for each album, process whichever folder currently holds MORE
    # items, and ignore the other. canonical() sends both to the same backup
    # folder, so the winner just tops up what is already there. Ties prefer
    # the original (stable while a twin is still filling).
    def _count(n):
        try:
            return sum(1 for p in (SHARED / n).iterdir()
                       if p.suffix.lower() in {".jpg", ".mp4"})
        except OSError:
            return 0

    # ...and "process only the richest folder" is wrong too. Each rebuild round
    # is a slightly different cut of the album: Apple drops items it can no
    # longer serve, so a photo can sit in round 1 and be absent from round 3.
    # Picking one folder left 2,024 photos unplanned - they existed on this PC
    # and were never looked at. Process EVERY round instead, richest first;
    # canonical() sends them all to the one backup folder and stem_key() means
    # the later rounds mostly recognise what earlier ones already copied. The
    # backup is deliberately a superset: a photo Apple has since dropped is
    # exactly the thing a backup exists to keep.
    all_dirs = {d.name for d in SHARED.iterdir() if d.is_dir()}
    rounds = {}
    for n in names:
        if _count(n) == 0:
            continue                          # empty twin - nothing to do
        m = re.match(r"^(.+)_\d+$", n)
        base = m.group(1) if m and m.group(1) in all_dirs else n
        rounds.setdefault(base, []).append(n)
    out = []
    for base in sorted(rounds):
        out += sorted(rounds[base], key=lambda n: (-_count(n), n))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--album")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--normal-priority", action="store_true",
                    help="don't run at background priority")
    ap.add_argument("--no-videos", action="store_true",
                    help="photos only (videos are matched by trip date window)")
    ap.add_argument("--interval", type=int, default=120)
    a = ap.parse_args()

    if not a.normal_priority:
        print("priority:      background" if set_background_priority() else "priority: normal")
    BACKUP.mkdir(parents=True, exist_ok=True)
    print(f"workers:       {a.workers}")
    print(f"shared albums: {SHARED}")
    print(f"backup to:     {BACKUP}")
    print(f"skipping:      {', '.join(sorted(SKIP))}")
    if not pe.originals_index()["byTime"]:
        print("! originals index missing — run tools/index_originals.ps1 first")
        return

    while True:
        todo = [a.album] if a.album else albums()
        for name in todo:
            src = SHARED / name
            if not src.is_dir():
                continue
            n = sum(1 for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMG)
            if not n:
                # An album with zero PHOTOS is not necessarily empty - Cologne
                # (2023) is a single video, and this branch used to park it on
                # "waiting for photos from phone" forever, so its video was
                # never backed up. Only skip when there is nothing at all.
                nv_only = sum(1 for p in src.iterdir()
                              if p.is_file() and p.suffix.lower() in VID)
                if not nv_only:
                    _status["albums"].setdefault(canonical(name), {}).update(
                        total=0, state="waiting for photos from phone")
                    status_write()
                    continue
                if not a.no_videos:
                    print(f"[{time.strftime('%H:%M:%S')}] {name}: "
                          f"{nv_only} videos, no photos")
                    backup_videos(name, workers=a.workers)
                    verify_album(name)
                continue
            # Track whether the album is still arriving from the phone. A
            # verified backup only proves we captured what REACHED the PC —
            # Armenia was deleted mid-upload and lost 44 items that way.
            nv = sum(1 for p in src.iterdir()
                     if p.is_file() and p.suffix.lower() in VID)
            st = _status["albums"].setdefault(canonical(name), {})
            now = time.time()
            if st.get("albumItems") != n + nv:
                st["albumItems"] = n + nv
                st["lastChange"] = now
            st["stableMin"] = round((now - st.get("lastChange", now)) / 60, 1)
            status_write()

            # "done" used to mean "a pass finished", not "every photo is here".
            # 2,365 copies failed with WinError 404 when the cloud file provider
            # was restarted underneath them, the album still read done, and
            # nothing ever retried them. Require the files to actually exist.
            have = 0
            bdir = BACKUP / canonical(name)
            if bdir.is_dir():
                # "_manifest*", not any leading underscore: real photos can be
                # named _DSC4284.JPG, and excluding them made this gate think
                # the album was short forever, re-running a finished pass
                have = sum(1 for p in bdir.iterdir()
                           if p.is_file() and not p.name.startswith("_manifest")
                           and p.suffix.lower() in IMG)
            # Same trap as photos: vidState=="done" means a pass FINISHED,
            # not that every video is here. Once photos hit have>=n this
            # branch is the ONLY place videos get touched again, so a video
            # that failed here would never retry - Kosovo's 10/10 failed
            # videos sat at vidBytes=0 forever until this was checked too.
            have_vid = 0
            vdir = bdir / "videos"
            if vdir.is_dir():
                have_vid = sum(1 for p in vdir.iterdir()
                               if p.is_file() and p.suffix.lower() in VID)
            if (st.get("state") == "done" and st.get("total") == n
                    and album_covered(name)):
                if not a.no_videos and (st.get("vidState") != "done"
                                         or have_vid < (st.get("vidTotal") or 0)):
                    backup_videos(name, workers=a.workers)   # photos done, videos pending
                    verify_album(name)
                elif not st.get("verify"):
                    verify_album(name)
                continue                     # nothing new since the last pass
            # Download whatever is here NOW. An album can take hours to finish
            # uploading from the phone; waiting for the count to settle (the old
            # behaviour) stalled big albums indefinitely. Copies are skipped if
            # already present, so later passes just pick up the new arrivals.
            prev = st.get("total") or 0
            extra = f" (+{n - prev} new since last pass)" if n > prev > 0 else ""
            print(f"[{time.strftime('%H:%M:%S')}] {name}: {n} photos{extra}")
            backup_album(name, workers=a.workers)
            if not a.no_videos:
                backup_videos(name, workers=a.workers)
            verify_album(name)
        if not a.watch:
            break
        _status["running"] = True
        status_write()
        time.sleep(a.interval)

    _status["running"] = False
    status_write()
    print("done")


if __name__ == "__main__":
    main()
