"""Replace archived files that show the WRONG picture with the album's own copy.

find_original() matches by capture SECOND and takes the largest file at that
second, so in a burst every frame resolved to the same "original" - and 129
entries ended up archived as a full-resolution file of a DIFFERENT shot. A
perceptual sweep (dHash of the archived file vs the album's own rendition of
that entry) found every case; its JSON is this tool's input.

The fix per entry:
  * copy the ALBUM RENDITION (the 2048px file Apple serves for that entry) into
    the backup - it is the right picture by construction, since it is exactly
    what the sweep compared against
  * point the manifest entry at the new file, marked full=False
  * delete the old wrong file ONLY if no other entry still references it

A smaller file of the RIGHT photo beats a full-res file of the wrong one.
Entries repaired this way can be upgraded to full resolution later only by a
matcher that verifies CONTENT, never capture time alone.

    python tools/repair_wrong_photos.py <sweep.json>            # dry run
    python tools/repair_wrong_photos.py <sweep.json> --apply
"""
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_backup as pb                                   # noqa: E402


def album_rendition(album, entry):
    """The album's own file for this entry, whichever rebuild round has it."""
    for d in pb.SHARED.iterdir():
        if not d.is_dir() or re.sub(r"_\d+$", "", d.name) != album:
            continue
        p = d / entry
        if p.is_file():
            return p
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    apply = "--apply" in sys.argv

    fixed = skipped = deleted = kept = 0
    for album, rec in sorted(report.items()):
        if not rec.get("cases"):
            continue
        dest = pb.BACKUP / album
        mp = dest / "_manifest.json"
        man = pb.load_manifest(mp)
        files, entries = man["files"], man["entries"]
        n_alb = 0
        for case in rec["cases"]:
            entry, wrong = case["entry"], case["file"]
            if entries.get(entry) != wrong:
                skipped += 1                 # manifest moved on since the sweep
                continue
            src = album_rendition(album, entry)
            if not src:
                print(f"  {album}: no album rendition for {entry[:24]}… - left as is")
                skipped += 1
                continue
            target = pb.unique_name(entry, set(entries.values()) | {wrong})
            if apply:
                shutil.copy2(src, dest / target)
                rec2 = {"from": entry, "full": False,
                        "size": (dest / target).stat().st_size, "edited": False}
                try:
                    from PIL import Image
                    with Image.open(dest / target) as im:
                        rec2["w"], rec2["h"] = im.size
                except Exception:
                    pass
                files[target] = rec2
                entries[entry] = target
                # the wrong file goes only when nothing else still points at it
                if not any(f == wrong for e, f in entries.items()):
                    files.pop(wrong, None)
                    try:
                        (dest / wrong).unlink()
                        deleted += 1
                    except OSError:
                        kept += 1
                else:
                    kept += 1
            fixed += 1
            n_alb += 1
        if n_alb:
            print(f"  {album:<30} {n_alb:>3} repaired")
            if apply:
                pb.write_json(mp, {"files": files, "entries": entries})

    verb = "repaired" if apply else "would repair"
    print(f"\n{verb} {fixed} entries "
          f"({deleted} wrong files deleted, {kept} kept - still referenced), "
          f"{skipped} skipped")
    if not apply:
        print("dry run - nothing changed. re-run with --apply")


if __name__ == "__main__":
    main()
