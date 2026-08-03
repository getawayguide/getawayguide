#!/usr/bin/env python3
"""Regenerate the Destinations dropdown on every LIVE page from destinations.html.

build_nav() in publish_country.py is the single source of truth (a publish runs it as
step 4c). This exposes it on its own so a nav change can be rolled out without publishing
a country. Drafts are handled by tools/update_draft_nav.py.

  py tools/regen_nav.py [--dry-run]
"""
import glob, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publish_country import parse_countries, build_nav, NAV_RE

DRY = "--dry-run" in sys.argv


def main():
    by_cont = parse_countries(open(os.path.join(ROOT, "destinations.html"), encoding="utf-8").read())
    pages = sorted(p.replace("\\", "/") for p in
                   glob.glob(os.path.join(ROOT, "*.html")) + glob.glob(os.path.join(ROOT, "*", "*.html")))
    changed = skipped = 0
    for p in pages:
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.startswith((".tmp/", "Drafts/")):
            continue
        html = open(p, encoding="utf-8").read()
        if not NAV_RE.search(html):
            skipped += 1
            continue
        pfx = "" if "/" not in rel else "../"
        new = NAV_RE.sub(lambda _m: build_nav(pfx, by_cont) + "\n    </li>", html, count=1)
        if new == html:
            continue
        if not DRY:
            open(p, "w", encoding="utf-8", newline="").write(new)
        changed += 1
        print("  %s %s" % ("[dry]" if DRY else "  ok", rel))
    print("\n%snav regenerated on %d page(s); %d without a dropdown"
          % ("[dry-run] " if DRY else "", changed, skipped))


if __name__ == "__main__":
    main()
