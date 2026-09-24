"""Stamp editor.html's local assets with a content hash, so an edited file is actually
picked up by the copy of the editor running off the desktop shortcut.

The editor is opened from file:// (photo_suite.EDITOR_URL), and a browser is free to serve
an unversioned file:// script from cache. fonts.css already carried a `?v=` hash, but
review.js did not, so editing the review pane and reloading could leave the OLD review.js
running with no sign that anything was stale. Hashing the content means the URL changes
whenever the file does, and never otherwise, so the reload picks it up and an unchanged file
still comes from cache.

    python tools/stamp_assets.py             # rewrite the stamps that are out of date
    python tools/stamp_assets.py --check     # report only, exit 1 if any are stale

photo_suite.start_all() runs this before opening the editor, so the double-click path is
always current. Run it by hand after editing review.js if the editor is already open.
"""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "editor.html"
# every local asset the editor pulls in over a URL; anything inline needs no stamp
ASSETS = ["review.js", "fonts.css"]


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


def main():
    check = "--check" in sys.argv
    html = PAGE.read_text(encoding="utf-8", newline="")
    before, stale = html, []

    for name in ASSETS:
        f = ROOT / name
        if not f.is_file():
            print(f"! {name} is missing, skipped")
            continue
        want = digest(f)
        # match the asset in a src=/href=, with or without an existing ?v=
        pat = re.compile(r'((?:src|href)=")' + re.escape(name) + r'(\?v=([0-9a-f]+))?(")')
        found = pat.search(html)
        if not found:
            print(f"! {name} is not referenced by a URL in editor.html, skipped")
            continue
        have = found.group(3)
        if have == want:
            print(f"  {name:12s} {want}  up to date")
            continue
        stale.append(name)
        print(f"  {name:12s} {have or '(none)'} -> {want}" + ("  STALE" if check else ""))
        html = pat.sub(lambda m: m.group(1) + name + "?v=" + want + m.group(4), html, count=1)

    if check:
        if stale:
            print(f"\n{len(stale)} stamp(s) out of date: " + ", ".join(stale))
        return 1 if stale else 0
    if html != before:
        PAGE.write_text(html, encoding="utf-8", newline="")
        print(f"\neditor.html restamped ({len(stale)} asset(s))")
    else:
        print("\nnothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
