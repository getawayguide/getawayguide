"""The helpers the photo archive still needs now that the shared-album backup watcher is gone.

~/Backup/<Album>/ holds one file per album item (full-resolution originals, imported from
icloud.com downloads by tools/ingest_icloud_download.py) plus a _manifest.json per album.
These functions used to live in tools/photo_backup.py, the retired watcher; they were kept
when it was deleted (2026-10-12) because the ingest, content_match and the photo server
import them. Nothing here reads iCloud's Shared Albums or writes status files.
"""
import ctypes
import json
import os
import re
import threading
import time
from pathlib import Path

HOME = Path.home()
BACKUP = HOME / "Backup"                       # the same path tools/photo_editor.py uses
SHARED = HOME / "iCloudPhotos" / "Shared"      # Apple's shared-album copies; empty of albums since 2026-10-04
IMG = {".jpg", ".jpeg", ".heic", ".heif", ".png"}
VID = {".mp4", ".mov", ".m4v"}


def write_json(path, data, tries=6):
    """Atomic write that survives another process reading the same file.

    On Windows, replacing a file that something else has open raises WinError 32 or 5.
    Retry briefly, never share a temp name between processes, and never let a status
    update take down the work that produced it."""
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


def load_manifest(path):
    """{"files": {backup name: meta}, "entries": {album item: backup name}}.
    Older manifests were keyed by backup name with a single "from" field, which
    could not represent two album entries of the same photo: migrate them."""
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
    """A distinct filename per ALBUM ENTRY. Apple keeps duplicate entries of the same
    shot; each one gets its own file so the backup count matches the album count."""
    if base not in claimed:
        return base
    stem, ext = os.path.splitext(base)
    i = 2
    while f"{stem}-{i}{ext}" in claimed:
        i += 1
    return f"{stem}-{i}{ext}"


def stem_key(name):
    """The distinct-photo identity of an album entry: a shared-album filename is a content
    hash, and Apple emits the same photo twice as "<hash>.jpg" and "<hash>_00001.jpg".
    Strip the copy suffix and one stem is one real photo."""
    return re.sub(r"_\d+$", "", Path(name).stem)


# ---------------------------------------------------------------------------
# Explicit hydration via the Windows Cloud Files API.
#
# Reading a cloud-only placeholder (shutil.copy2, PIL.open) triggers Windows'
# read-driven hydration, which Microsoft's Cloud Files FAQ calls opportunistic:
# it can block for 20+ minutes or never release the handle. CfHydratePlaceholder
# is the sanctioned way: ask the filter driver to fetch the whole file, and copy
# the now-local file afterwards. Windows 10 1709+; without CldApi.dll callers
# fall back to a plain read.
# ---------------------------------------------------------------------------
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
    """Explicitly pull a cloud placeholder's bytes onto disk. True on success, False on
    failure or timeout. The blocking API runs in a daemon thread so a wedged hydration
    can be abandoned after timeout_s without holding the caller."""
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
    began = time.time()
    while t.is_alive():
        t.join(5)
        if time.time() - began > timeout_s:
            return False
    return out.get("hr") == 0


RECALL, OFFLINE = 0x400000, 0x1000


def is_cloud(p):
    """True for an iCloud-for-Windows placeholder whose bytes are not on this PC."""
    try:
        return bool(getattr(p.stat(), "st_file_attributes", 0) & (RECALL | OFFLINE))
    except OSError:
        return False


def fetch(srcf, tmp, hydrate_budget_s=120, per_mb_s=3):
    """Copy a library file to tmp, hydrating a cloud placeholder explicitly first (the
    read-driven copy jams for 20+ minutes on a cold placeholder). Raises TimeoutError
    when the placeholder does not come down within its budget."""
    srcf, tmp = Path(srcf), Path(tmp)
    if HYDRATE_AVAILABLE and is_cloud(srcf):
        try:
            mb = srcf.stat().st_size / 1e6
        except OSError:
            mb = 0
        budget = hydrate_budget_s + per_mb_s * mb
        if not hydrate(srcf, budget):
            raise TimeoutError(f"hydration did not complete in {budget:.0f}s ({mb:.1f} MB)")
    import shutil
    shutil.copy2(srcf, tmp)
    return True
