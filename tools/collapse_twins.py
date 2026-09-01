"""Collapse redundant copies of the SAME image into one file, per album.

Two separate causes put the same picture on disk twice:

  1. Apple lists a photo twice in one album - "<hash>.jpg" beside
     "<hash>_00001.jpg". Same stem, so stem_key() catches it.
  2. The same photo was added to an album twice under DIFFERENT hashes, so the
     entries have different stems, but both resolved to the same original in
     the library and each got its own copy ("IMG_0150.jpg", "IMG_0150-2.jpg").
     Only the bytes reveal this one.

Case 2 is the bigger half - 489 files / 5.76 GB - and no stem rule can see it,
so this works on content: within one album folder, files with the same size AND
the same full MD5 are the same picture, and one of them is enough.

SAFETY - before any file is removed:
  * sizes must match, then the full MD5 must match (no short-circuit on name)
  * every manifest entry pointing at a doomed file is repointed at the survivor
  * only then is anything unlinked

That order is the whole point. Deleting first and remapping later is what
orphaned 2,068 entries the last time, and a de-duplicator that goes by filename
alone cannot tell "IMG_0150-2.jpg" (a real duplicate) from "Tezza-1574.jpg" (a
real photo whose name simply ends in a number).

Photos and videos both, and videos use their own manifest shape: {entry: {...}},
NOT the photo manifest's {"files","entries"} - reading one through the other
silently reports an empty album.

    python tools/collapse_twins.py            # dry run
    python tools/collapse_twins.py --apply
"""
import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_backup as pb                                   # noqa: E402


def md5(p, chunk=1 << 20):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def dupe_groups(folder, exts):
    """[[keep, drop, ...]] - files in this folder that are byte-identical.

    Size buckets first: hashing 88 GB to find 5 GB of duplicates is pointless
    when a stat() rules out almost every pair for free.
    """
    files = [p for p in folder.iterdir()
             if p.is_file() and not p.name.startswith("_manifest")
             and p.suffix.lower() in exts]
    bysize = collections.defaultdict(list)
    for p in files:
        bysize[p.stat().st_size].append(p)
    out = []
    for ps in bysize.values():
        if len(ps) < 2:
            continue
        byhash = collections.defaultdict(list)
        for p in ps:
            byhash[md5(p)].append(p)
        for g in byhash.values():
            if len(g) > 1:
                # keep the shortest name: "IMG_0150.jpg" over "IMG_0150-2.jpg"
                g.sort(key=lambda p: (len(p.name), p.name))
                out.append([p.name for p in g])
    return out


def collapse_photos(d, apply):
    mp = d / "_manifest.json"
    if not mp.exists():
        return 0, 0
    man = pb.load_manifest(mp)
    files, entries = man.get("files", {}), man.get("entries", {})
    n = freed = 0
    for group in dupe_groups(d, pb.IMG):
        keep, drop = group[0], set(group[1:])
        if apply:
            for e, f in list(entries.items()):
                if f in drop:
                    entries[e] = keep
            for f in drop:
                rec = files.pop(f, None)
                if rec and rec.get("orig") and keep in files \
                        and not files[keep].get("orig"):
                    files[keep]["orig"] = rec["orig"]
        for f in drop:
            freed += (d / f).stat().st_size
            n += 1
            if apply:
                try:
                    (d / f).unlink()
                except OSError as err:
                    print(f"    could not delete {f}: {err}")
    if n and apply:
        pb.write_json(mp, {"files": files, "entries": entries})
    return n, freed


def collapse_videos(d, apply):
    v = d / "videos"
    if not v.is_dir():
        return 0, 0
    mp = v / "_manifest.json"
    try:
        man = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    except Exception:
        man = {}
    n = freed = 0
    for group in dupe_groups(v, pb.VID):
        keep, drop = group[0], set(group[1:])
        if apply:
            for e, r in man.items():
                if isinstance(r, dict) and r.get("file") in drop:
                    r["file"] = keep
        for f in drop:
            freed += (v / f).stat().st_size
            n += 1
            if apply:
                try:
                    (v / f).unlink()
                except OSError as err:
                    print(f"    could not delete {f}: {err}")
    if n and apply:
        pb.write_json(mp, man)
    return n, freed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    tot = freed = 0
    for d in sorted(pb.BACKUP.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        np_, fp = collapse_photos(d, a.apply)
        nv, fv = collapse_videos(d, a.apply)
        if np_ or nv:
            print(f"  {d.name:<30} {np_:>4} photo  {nv:>4} video")
            tot += np_ + nv
            freed += fp + fv

    verb = "removed" if a.apply else "would remove"
    print(f"\n{verb} {tot} redundant copy(ies), {freed/1e9:.2f} GB")
    if not a.apply:
        print("dry run - nothing changed. re-run with --apply")


if __name__ == "__main__":
    main()
