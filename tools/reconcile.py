"""One-command reconciliation: what does each album hold, and are we covered?

Counts move around during an iCloud Shared Albums rebuild - Apple recounts,
drops entries that never finished uploading, and refills each album into a
"<Name>_1" twin over hours. Chasing those numbers by hand is hopeless, so this
prints one table with every source side by side:

    phone   the count YOU typed off your iPhone (may be stale after a rebuild)
    album   what is actually in the shared folder on this PC right now
            (the twin if the rebuild has moved ahead, else the original)
    backup  full-resolution files under <Backup>/<Album>/

The important column is COVERED: does the backup hold a file for every entry
currently in the album folder? That is the only claim this machine can make.
"phone" is an independent sanity check on whether Apple has delivered
everything - it is NOT something to make the backup match by deleting.

The backup is deliberately a SUPERSET. When an album shrinks (Armenia went
1180 -> 1166 during a rebuild) the extra files stay: they were real photos
captured before Apple recounted, and throwing them away to make a number
line up would be destroying the thing we are trying to protect.

    python tools/reconcile.py            # the table
    python tools/reconcile.py --verify   # also re-run verify on every album
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_backup as pb                                   # noqa: E402


def album_count(d):
    try:
        return sum(1 for p in d.iterdir() if p.suffix.lower() in {".jpg", ".mp4"})
    except OSError:
        return -1


def backup_count(d):
    if not d.is_dir():
        return 0
    n = sum(1 for p in d.iterdir()
            if p.is_file() and not p.name.startswith("_manifest")
            and p.suffix.lower() in pb.IMG)
    v = d / "videos"
    if v.is_dir():
        n += sum(1 for p in v.iterdir() if p.suffix.lower() in pb.VID)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true",
                    help="also re-run verify_album on each (slower, updates the UI)")
    a = ap.parse_args()

    try:
        exp = json.loads((pb.BACKUP / "_meta" / "expected.json").read_text(encoding="utf-8"))
    except Exception:
        exp = {}

    bases = sorted({pb.canonical(d.name) for d in pb.SHARED.iterdir()
                    if d.is_dir() and d.name != "desktop.ini"} | set(exp))

    print(f"{'album':<28}{'phone':>7}{'album':>7}{'backup':>8}  status")
    tot_short = tot_surplus = 0
    import re
    for base in bases:
        # a skipped album stays skipped through its rebuild twin: "Houston
        # Trip_1" is not literally in SKIP, and without this the tool reported
        # its 171 items as "backup missing" - a deliberately excluded album
        # counted as a failure
        if base in pb.SKIP or re.sub(r"_\d+$", "", base) in pb.SKIP:
            continue
        orig, twin = pb.SHARED / base, pb.SHARED / (base + "_1")
        oc = album_count(orig) if orig.is_dir() else -1
        tc = album_count(twin) if twin.is_dir() else -1
        here = max(oc, tc)                      # whichever copy is further along
        b = backup_count(pb.BACKUP / base)
        p = exp.get(base)

        if here < 0 and b == 0:
            continue
        if here <= 0:
            status = "album folder empty (rebuild in progress?)"
        elif b >= here:
            extra = b - here
            status = "COVERED" + (f" (+{extra} archived beyond album)" if extra else "")
            tot_surplus += extra
        else:
            status = f"SHORT {here - b} - backup is missing items the album has"
            tot_short += here - b
        if p and here > 0 and here < p:
            status += f" | Apple still owes {p - here} vs your phone count"
        print(f"{base:<28}{p or '-':>7}{here if here >= 0 else '-':>7}{b:>8}  {status}")

        if a.verify:
            name = (base + "_1") if tc > oc else base
            if (pb.SHARED / name).is_dir():
                pb.verify_album(name, verbose=False)

    print()
    print(f"backup missing from albums : {tot_short}")
    print(f"backup archived beyond albums: {tot_surplus}  (kept deliberately)")
    if a.verify:
        print("verify records refreshed - the library page will reflect this")


if __name__ == "__main__":
    main()
