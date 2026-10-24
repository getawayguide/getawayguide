"""Swap shared-album copies in the backup for their full-resolution originals,
choosing the original by what the picture LOOKS like, not by metadata alone.

photo_backup matches on metadata: a photo on its capture second, a video on
its duration. Both guess wrong in exactly the cases that matter:

  * Photos. An original and its edited copy share a capture second
    ("IMG_6893 Copy.JPG" beside "IMG_6893(1).HEIC"). The metadata match hands
    the one original to the first album photo and, rightly, refuses to guess
    for the second - so ~11% of every 2023 album stayed at 2048px.
  * Videos. Two different clips of the same length are indistinguishable by
    duration. 205 album videos across 24 albums were pointed at another
    clip's original (Turkey 2.0: pirate ships, ruins, a waterfall - gone), and
    every 2023 album kept 720p copies while the videos index was missing.

This tool downloads the few plausible candidates for each item and compares
fingerprints (a 256-bit difference hash; for video, frames at 10/50/90%), then
takes a candidate only when it clearly matches and nothing else comes close.

    python tools/content_match.py --album "Croatia 2.0 (2023)"          # dry run
    python tools/content_match.py --album "Croatia 2.0 (2023)" --apply
    python tools/content_match.py --all --apply

Pause the backup watcher first (Editor Suite.cmd pause): both write the same
manifests. Works from the BACKUP, so an album already deleted from the phone
can still be upgraded - the backup's own file is the shared-album copy.
"""
import argparse
import collections
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_lib as pb         # noqa: E402  (BACKUP, manifests, unique_name, stem_key)
import photo_editor as pe       # noqa: E402  (also registers the HEIF opener)
from PIL import Image, ImageOps  # noqa: E402

CACHE = Path(__file__).resolve().parent.parent / ".tmp" / "content_match"
LOCAL_ONLY = False
SIGS = CACHE / "sigs.json"

# Measured on albums whose correct pairs are known (see calibrate()): the
# album copy against its own original sits far below these, and against a
# different shot of the same second far above them.
PHOTO_MAX = 40          # of 256 bits (right original 4-16, different shot 95+)
PHOTO_MARGIN = 15       # runner-up must be at least this much further away
VIDEO_MAX = 60          # mean over three frames (Turkey: right clip 9-42, wrong clip 116-130)
VIDEO_MARGIN = 15
VIDEO_DUR_TOL = 0.6     # seconds; Apple's re-encode can trim a frame or two
WINDOW_PAD = timedelta(hours=36)


# ---------------------------------------------------------------- fingerprints

def dhash(im):
    g = ImageOps.exif_transpose(im).convert("L").resize((17, 16), Image.LANCZOS)
    px = g.load()
    h = 0
    for y in range(16):
        for x in range(16):
            h = (h << 1) | (px[x, y] > px[x + 1, y])
    return h


def photo_sig(path):
    """{"h": fingerprint, "px": real pixel count}. The pixel count is read from
    the file, not the originals index: the index's Shell "Dimensions" column
    says 2000x1125 for every original, which is why it cannot be trusted to
    say whether an original is bigger than the album copy."""
    with Image.open(path) as im:
        px = im.size[0] * im.size[1]
        im.draft("RGB", (256, 256))
        return {"h": dhash(im), "px": px}


def video_sig(path):
    import cv2
    cap = cv2.VideoCapture(str(path))
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    out = []
    for frac in (0.1, 0.5, 0.9):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * frac))
        ok, fr = cap.read()
        if not ok:
            return None
        out.append(dhash(Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))))
    cap.release()
    return {"f": out, "dur": n / fps if fps else 0}


def ham(a, b):
    return bin(a ^ b).count("1")


def vdist(a, b):
    return sum(ham(x, y) for x, y in zip(a["f"], b["f"])) / 3


class SigCache:
    """Fingerprints of library originals, keyed by path + size, so a rerun
    (or the next album of the same trip) does not download them again."""

    def __init__(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        try:
            self.d = json.loads(SIGS.read_text(encoding="utf-8"))
        except Exception:
            self.d = {}

    def key(self, p):
        try:
            return f"{p}|{p.stat().st_size}"
        except OSError:
            return None

    def get(self, p, kind, keep=False):
        """Fingerprint of a library original, downloading it if needed. With
        keep=True the downloaded copy is left in CACHE for the caller."""
        k = self.key(p)
        if k and k in self.d and not keep:
            return self.d[k]
        if LOCAL_ONLY and pb.is_cloud(p):
            return None                                  # not on this PC yet: skip, don't wait
        tmp = CACHE / "dl" / p.name
        tmp.parent.mkdir(parents=True, exist_ok=True)
        if not tmp.exists() or tmp.stat().st_size != p.stat().st_size:
            try:
                pb.fetch(p, tmp)
            except Exception as e:
                print(f"      ! could not download {p.name}: {e}")
                tmp.unlink(missing_ok=True)
                return None
        try:
            sig = photo_sig(tmp) if kind == "photo" else video_sig(tmp)
        except Exception as e:
            print(f"      ! could not read {p.name}: {e}")
            sig = None
        if k and sig is not None:
            self.d[k] = sig
            pb.write_json(SIGS, self.d)
        if not keep:
            tmp.unlink(missing_ok=True)
        return sig

    def take(self, p):
        """The downloaded file for p (fetching again if it was discarded)."""
        tmp = CACHE / "dl" / p.name
        if not tmp.exists() or tmp.stat().st_size != p.stat().st_size:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            pb.fetch(p, tmp)
        return tmp


def pick(dists, max_d, margin):
    """(best candidate, its distance) only when it is a clear winner."""
    if not dists:
        return None, None
    ranked = sorted(dists, key=lambda x: x[1])
    best, d = ranked[0]
    if d > max_d:
        return None, d
    if len(ranked) > 1 and ranked[1][1] - d < margin:
        return None, d
    return best, d


# ---------------------------------------------------------------- photos

def match_photos(name, cache, apply):
    dest = pb.BACKUP / name
    mpath = dest / "_manifest.json"
    man = pb.load_manifest(mpath)
    files, entries = man["files"], man["entries"]
    idx = pe.originals_index()
    lib = Path(idx["library"])
    taken = {Path(r["orig"]).name.lower() for r in files.values() if r.get("orig")}
    targets = [f for f, r in files.items()
               if not r.get("full") and not r.get("noOriginal") and (dest / f).exists()]
    print(f"  photos: {len(targets)} still at album resolution")
    plan = []
    for f in targets:
        src = dest / f
        keys = pe.capture_key(src)
        if not keys:
            continue                           # screenshot / saved image: nothing to find
        cands, seen = [], set()
        for k in keys:
            for c in idx["byTime"].get(k) or []:
                if c["n"].lower() in taken or c["n"] in seen:
                    continue
                seen.add(c["n"])
                cands.append(lib / c["n"])
        if not cands:
            continue
        mine = photo_sig(src)
        dists = []
        for p in cands:
            s = cache.get(p, "photo")
            # only an original that is really bigger than the copy we hold (the
            # UUID-named .jpg files at the same second are small exports)
            if s is not None and s["px"] > mine["px"] * 1.3:
                dists.append((p, ham(mine["h"], s["h"])))
        best, d = pick(dists, PHOTO_MAX, PHOTO_MARGIN)
        tag = f"{best.name} (distance {d})" if best else f"no clear match (best {d}, {len(dists)} candidates)"
        print(f"    {f}: {tag}")
        if best:
            plan.append((f, best, d))
    # one original per photo: if two copies want the same one, the closer wins
    plan.sort(key=lambda x: x[2])
    used, final = set(), []
    for f, best, d in plan:
        if best.name.lower() in used:
            print(f"    {f}: {best.name} already claimed by a closer match; skipped")
            continue
        used.add(best.name.lower())
        final.append((f, best, d))
    print(f"  photos: {len(final)} upgradeable")
    if not apply or not final:
        return len(final)
    claimed = set(files) | set(entries.values())
    for f, best, d in final:
        tmp = cache.take(best)
        newname = pb.unique_name(best.name, claimed)
        claimed.add(newname)
        shutil.copy2(tmp, dest / newname)
        rec = dict(files.get(f) or {})
        rec.update({"full": True, "orig": str(best), "size": (dest / newname).stat().st_size,
                    "matched": f"content {d}"})
        try:
            with Image.open(dest / newname) as im:
                rec["w"], rec["h"] = ImageOps.exif_transpose(im).size
        except Exception:
            pass
        files.pop(f, None)
        files[newname] = rec
        for k, v in list(entries.items()):
            if v == f:
                entries[k] = newname
        (dest / f).unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    pb.write_json(mpath, {"files": files, "entries": entries})
    return len(final)


# ---------------------------------------------------------------- videos

def trip_window(dest, files):
    times = []
    for f in files:
        for k in pe.capture_key(dest / f)[:1]:
            try:
                times.append(datetime.strptime(k, "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                pass
    return (min(times) - WINDOW_PAD, max(times) + WINDOW_PAD) if times else (None, None)


def match_videos(name, cache, apply):
    dest = pb.BACKUP / name
    vdir = dest / "videos"
    vpath = vdir / "_manifest.json"
    if not vpath.exists():
        return 0
    vman = json.loads(vpath.read_text(encoding="utf-8"))
    man = pb.load_manifest(dest / "_manifest.json")
    lo, hi = trip_window(dest, list(man["files"]))
    lib_videos = [(p, dur, t) for p, dur, t in pb.library_videos()
                  if t and (lo is None or lo <= t <= hi)]

    # one record per distinct album video
    by_stem = collections.defaultdict(list)
    for entry, rec in vman.items():
        by_stem[pb.stem_key(entry)].append(entry)
    file_stems = collections.defaultdict(set)
    for stem, ents in by_stem.items():
        f = (vman[ents[0]] or {}).get("file")
        if f:
            file_stems[f].add(stem)

    def album_copy(stem):
        """This album video's own copy: the backup file if it is one, else the
        rescued copy, else the shared folder if the album still exists."""
        ents = by_stem[stem]
        f = vman[ents[0]].get("file")
        if f and not vman[ents[0]].get("full") and len(file_stems[f]) == 1 and (vdir / f).exists():
            return vdir / f
        for e in ents:
            for c in (dest / "_rescue" / "videos" / e,
                      *[r / e for r in pb.SHARED.glob(pb.canonical(name) + "*") if r.is_dir()]):
                if c.exists():
                    return c
        return None

    taken = {r["file"].lower() for r in vman.values() if r.get("full") and r.get("file")}
    targets = []
    # 1. files that stand in for two or more album videos: which one do they belong to?
    for f, stems in file_stems.items():
        if len(stems) < 2:
            continue
        if not (vdir / f).exists():
            targets += list(stems)
            continue
        have = video_sig(vdir / f)
        scored = []
        for s in stems:
            c = album_copy(s)
            sig = video_sig(c) if c else None
            scored.append((s, vdist(have, sig) if (have and sig) else 999))
        scored.sort(key=lambda x: x[1])
        owner, d = scored[0]
        print(f"    {f} is shared by {len(stems)} album videos; it belongs to {owner} (distance {d:.0f})")
        for s, _ in scored[1:]:
            targets.append(s)
        if d > VIDEO_MAX:                      # it matches none of them: re-find all
            targets.append(owner)
    # 2. album videos still at the 720p copy
    for stem, ents in by_stem.items():
        r = vman[ents[0]]
        if not r.get("full") and stem not in targets:
            targets.append(stem)
    print(f"  videos: {len(targets)} to match ({len(lib_videos)} library videos in the trip window)")

    plan = []
    for stem in targets:
        c = album_copy(stem)
        if not c:
            print(f"    {stem}: no album copy left to compare against")
            continue
        mine = video_sig(c)
        if not mine:
            continue
        cands = [p for p, dur, t in lib_videos
                 if abs(dur - mine["dur"]) <= VIDEO_DUR_TOL and p.name.lower() not in taken]
        dists = []
        for p in cands:
            s = cache.get(p, "video")
            if s:
                dists.append((p, vdist(mine, s)))
        best, d = pick(dists, VIDEO_MAX, VIDEO_MARGIN)
        tag = f"{best.name} (distance {d:.0f})" if best else \
            f"no clear match (best {d if d is None else round(d)}, {len(dists)} candidates)"
        print(f"    {stem}: {tag}")
        plan.append((stem, c, best, d))

    plan.sort(key=lambda x: (x[3] if x[3] is not None else 999))
    used = set()
    changed = 0
    claimed = {r.get("file") for r in vman.values() if r.get("file")}
    for stem, copy_path, best, d in plan:
        if best and best.name.lower() in used:
            best = None
        if best:
            used.add(best.name.lower())
        if not apply:
            changed += bool(best)
            continue
        if best:
            src = cache.take(best)
            newname = pb.unique_name(best.name, claimed)
            shutil.copy2(src, vdir / newname)
            src.unlink(missing_ok=True)
            rec = {"file": newname, "full": True, "size": (vdir / newname).stat().st_size,
                   "matched": f"content {d:.0f}"}
            changed += 1
        else:
            # no original found: at least give it ITS OWN album copy, so it is no
            # longer pointing at someone else's clip
            cur = vman[by_stem[stem][0]].get("file")
            if cur and len(file_stems.get(cur, ())) == 1 and not vman[by_stem[stem][0]].get("full"):
                continue                       # already its own copy
            newname = pb.unique_name(copy_path.name, claimed)
            shutil.copy2(copy_path, vdir / newname)
            rec = {"file": newname, "full": False, "size": (vdir / newname).stat().st_size}
        claimed.add(newname)
        old = vman[by_stem[stem][0]].get("file")
        for e in by_stem[stem]:
            vman[e] = dict(rec)
        # drop the old album copy if nothing points at it any more
        if old and old != newname and not any(r.get("file") == old for r in vman.values()) \
                and not (vman.get(old) or {}).get("full"):
            (vdir / old).unlink(missing_ok=True)
    if apply:
        pb.write_json(vpath, vman)
    print(f"  videos: {changed} {'upgraded' if apply else 'upgradeable'}")
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--album", action="append", help="backup folder name (repeatable)")
    ap.add_argument("--all", action="store_true", help="every album in the backup")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--photos-only", action="store_true")
    ap.add_argument("--videos-only", action="store_true")
    ap.add_argument("--local-only", action="store_true",
                    help="only use originals already downloaded to this PC (skip iCloud downloads, "
                         "which time out for minutes each while iCloud is throttling)")
    a = ap.parse_args()
    if a.apply:
        from photo_suite import watcher_pids
        if watcher_pids():
            sys.exit("The backup watcher is running and writes the same manifests. "
                     "Pause it first: Editor Suite.cmd pause")
    names = a.album or ([d.name for d in sorted(pb.BACKUP.iterdir())
                         if d.is_dir() and d.name != "_meta"] if a.all else [])
    if not names:
        ap.error("name an --album or pass --all")
    cache = SigCache()
    global LOCAL_ONLY
    LOCAL_ONLY = a.local_only
    for n in names:
        if not (pb.BACKUP / n).is_dir():
            print(f"{n}: no backup folder")
            continue
        print(f"{n}{'' if a.apply else '  (dry run)'}")
        t = time.time()
        if not a.videos_only:
            match_photos(n, cache, a.apply)
        if not a.photos_only:
            match_videos(n, cache, a.apply)
        print(f"  {time.time() - t:.0f}s")
        if a.apply:
            pb.verify_album(n, verbose=True)


if __name__ == "__main__":
    main()
