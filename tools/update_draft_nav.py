# -*- coding: utf-8 -*-
"""Regenerate the Destinations mega-menu on every Drafts/ page.

Everything (the country set, continent grouping, the In-Depth Guide legend + green dots) is rebuilt from
destinations.html, the single source of truth, exactly like publish_country.py step 4c does for live pages.

2026-10-24: every draft now sits three folders deep (Drafts/<Upcoming|Full Articles|Field Notes>/<country>/), so the
prefix is worked out from each page's own path (it was a fixed "../../", which would have broken every link in the
menu), and it covers the articles too, not only field notes: the Full Articles still linked Armenia's retired field
notes after its launch.

Usage: python tools/update_draft_nav.py [--dry-run] [slug ...]     (no slugs = every draft)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publish_country import parse_countries, build_nav, NAV_RE
import draft_paths as dp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    dry = "--dry-run" in sys.argv
    slugs = [a for a in sys.argv[1:] if not a.startswith("--")] or ["*"]
    dest = open(f"{ROOT}/destinations.html", encoding="utf-8").read()
    by_cont = parse_countries(dest)
    navs = {}
    changed = skipped = 0
    for slug in slugs:
        for p in dp.draft_pages(slug):
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            pfx = dp.depth_prefix(rel)
            if pfx not in navs:
                navs[pfx] = build_nav(pfx, by_cont) + "\n    </li>"
            cur = open(p, encoding="utf-8", newline="").read()
            if '<div class="nav-dropdown' not in cur:
                skipped += 1
                continue
            new = NAV_RE.sub(lambda m: navs[pfx], cur, count=1)
            if new == cur:
                skipped += 1
                continue
            if not dry:
                open(p, "w", encoding="utf-8", newline="").write(new)
            print(f"  {'would update' if dry else 'ok'} {rel}")
            changed += 1
    print(f"nav {'would change' if dry else 'regenerated'} on {changed} draft page(s); {skipped} unchanged or without a menu")


if __name__ == "__main__":
    main()
