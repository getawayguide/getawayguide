"""Drop manifest entries that point at a DIFFERENT photo's file.

The planner used to decide "this photo is already backed up" by the filename of
the original it resolved to. Two things break that:

  * iPhone filenames recycle - a great many unrelated photos are IMG_3736.jpg
  * find_original() matches on capture SECOND and returns the largest file
    there, so every frame of a burst resolves to the same original

So unrelated photos were merged onto one file. Australia had five distinct
album entries all recorded as "IMG_4169 Copy.JPG", and because every entry
still resolved to a file that existed, verify called the album fully covered
while four photos were simply absent.

The manifest records who really produced each file (files[target]["from"]), and
an album entry's filename is a content hash, so stem_key() says whether two
entries are the same photo. Any entry whose stem differs from the stem of the
file's producer is a false alias: this deletes that mapping, and the next
backup pass copies the photo it was hiding.

Nothing on disk is touched - only the mapping. Files stay exactly where they
are; entries that are genuinely Apple's "<hash>_00001" twin keep sharing.

    python tools/repair_manifests.py            # dry run
    python tools/repair_manifests.py --apply
"""
import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_backup as pb                                   # noqa: E402


def repair_photos(mp, apply):
    m = pb.load_manifest(mp)
    files, entries = m.get("files", {}), m.get("entries", {})
    drop = []
    for e, f in entries.items():
        rec = files.get(f)
        if not rec:
            continue
        owner = rec.get("from")
        if owner and pb.stem_key(owner) != pb.stem_key(e):
            drop.append(e)
    if drop and apply:
        for e in drop:
            del entries[e]
        pb.write_json(mp, {"files": files, "entries": entries})
    return len(drop)


def load_video_manifest(mp):
    """Raw JSON - the video manifest is {entry: {...}} and does NOT have the
    photo manifest's {"files","entries"} shape.

    pb.load_manifest() migrates anything unshaped into that photo form, so
    reading a video manifest through it silently returns a 2-key dict and every
    caller sees an empty album. That is what made this repair report "0 video
    aliases" for every album while it had checked nothing at all."""
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def repair_videos(mp, apply):
    """No "from" field here - the record IS the entry. So group by file and let
    the first stem keep it; every other stem is a different clip and must get
    its own copy."""
    man = load_video_manifest(mp)
    if not isinstance(man, dict):
        return 0
    byfile = collections.defaultdict(list)
    for e, r in man.items():
        if isinstance(r, dict) and r.get("file"):
            byfile[r["file"]].append(e)
    drop = []
    for f, es in byfile.items():
        if len(es) < 2:
            continue
        keep = pb.stem_key(sorted(es)[0])
        drop += [e for e in es if pb.stem_key(e) != keep]
    if drop and apply:
        for e in drop:
            del man[e]
        pb.write_json(mp, man)
    return len(drop)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    tot = 0
    for d in sorted(pb.BACKUP.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        np = repair_photos(d / "_manifest.json", a.apply) if (d / "_manifest.json").exists() else 0
        nv = repair_videos(d / "videos" / "_manifest.json", a.apply) \
            if (d / "videos" / "_manifest.json").exists() else 0
        if np or nv:
            print(f"  {d.name:<30} {np:>5} photo  {nv:>4} video")
            tot += np + nv

    verb = "dropped" if a.apply else "would drop"
    print(f"\n{verb} {tot} false alias(es) - those photos are now unbacked "
          f"and the next pass will copy them")
    if not a.apply:
        print("dry run - nothing changed. re-run with --apply")


if __name__ == "__main__":
    main()
