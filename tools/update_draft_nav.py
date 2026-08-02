# -*- coding: utf-8 -*-
"""Regenerate the grouped nav dropdown on the Drafts/ field-notes pages.

Drafts sit one folder deeper than live country pages, so they need the `../../`
prefix. Everything else (the country set, continent grouping, the In-Depth Guide
legend + green dots) is rebuilt from destinations.html, the single source of truth
— exactly like publish_country.py step 4c does for live pages.

Usage: python tools/update_draft_nav.py [slug ...]     (no args = every draft)
"""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publish_country import parse_countries, build_nav, NAV_RE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    dest = open(f"{ROOT}/destinations.html", encoding="utf-8").read()
    by_cont = parse_countries(dest)
    nav = build_nav("../../", by_cont) + "\n    </li>"

    slugs = sys.argv[1:]
    paths = ([f"{ROOT}/Drafts/{s}/field-notes.html" for s in slugs] if slugs
             else sorted(glob.glob(f"{ROOT}/Drafts/*/field-notes.html")))
    changed = skipped = 0
    for p in paths:
        name = os.path.basename(os.path.dirname(p))
        if name.startswith("."):          # .Archive / .Full Articles are off-limits
            continue
        if not os.path.exists(p):
            print(f"  ?? {name}: no field-notes.html"); continue
        cur = open(p, encoding="utf-8").read()
        if '<div class="nav-dropdown' not in cur:
            print(f"  -- {name}: no nav dropdown"); skipped += 1; continue
        new = NAV_RE.sub(nav, cur, count=1)
        if new == cur:
            print(f"  == {name}: already current"); skipped += 1; continue
        open(p, "w", encoding="utf-8", newline="").write(new)
        print(f"  ok {name}"); changed += 1
    print(f"nav regenerated on {changed} draft page(s); {skipped} unchanged")

if __name__ == "__main__":
    main()
