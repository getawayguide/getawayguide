"""Remove byte-identical duplicate copies from the backup.

Apple's Shared Albums rebuild emits the same photo more than once - the same
content hash with "_00001" appended - and the backup used to keep one file per
album ENTRY, so those became "IMG_1301.HEIC" and "IMG_1301-2.HEIC" on disk.
Identical bytes, twice the space, and the counts still did not match the phone.

This deletes the redundant copies, keeping one file per distinct photo.

SAFETY - a copy is only deleted when ALL of these hold:
  * its name is "<base>-N.<ext>" and "<base>.<ext>" also exists
  * both files are the same size
  * both files have the same MD5, read in full
Anything that fails a check is kept and reported. Nothing outside the Backup
folder is touched, and no file is ever the last copy of its image.

    python tools/dedupe_backup.py            # dry run - shows what it WOULD do
    python tools/dedupe_backup.py --apply    # actually delete
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_backup as pb                                   # noqa: E402

DUP = re.compile(r"^(.*)-(\d+)(\.[^.]+)$")


def md5(p, chunk=1 << 20):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="delete (default is a dry run)")
    a = ap.parse_args()

    total = kept = freed = 0
    skipped = []
    for album in sorted(pb.BACKUP.iterdir()):
        if not album.is_dir() or album.name.startswith("_"):
            continue
        n_alb = 0
        for folder in (album, album / "videos"):
            if not folder.is_dir():
                continue
            for f in sorted(folder.iterdir()):
                if not f.is_file():
                    continue
                m = DUP.match(f.name)
                if not m:
                    continue
                base = folder / (m.group(1) + m.group(3))
                if not base.exists():
                    skipped.append((f, "no base file to keep"))
                    kept += 1
                    continue
                if f.stat().st_size != base.stat().st_size:
                    skipped.append((f, "size differs - NOT a duplicate"))
                    kept += 1
                    continue
                if md5(f) != md5(base):
                    skipped.append((f, "content differs - NOT a duplicate"))
                    kept += 1
                    continue
                freed += f.stat().st_size
                total += 1
                n_alb += 1
                if a.apply:
                    try:
                        f.unlink()
                    except OSError as e:
                        skipped.append((f, f"delete failed: {e}"))
        if n_alb:
            print(f"  {album.name:<30} {n_alb:>5} duplicate(s)")

    verb = "deleted" if a.apply else "would delete"
    print(f"\n{verb} {total} files, {freed/1e9:.2f} GB")
    if skipped:
        print(f"KEPT {len(skipped)} file(s) that failed a safety check:")
        for f, why in skipped[:10]:
            print(f"  {f.name}: {why}")
        if len(skipped) > 10:
            print(f"  ... and {len(skipped)-10} more")
    if not a.apply:
        print("\ndry run - nothing was changed. re-run with --apply to delete.")


if __name__ == "__main__":
    main()
