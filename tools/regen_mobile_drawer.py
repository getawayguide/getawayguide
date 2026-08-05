#!/usr/bin/env python3
"""Regenerate the Destinations submenu inside the mobile hamburger drawer on every page.

The drawer is hand-written per page (unlike the desktop dropdown, which build_nav()
owns), so a change to it otherwise means editing ~47 files by hand and they drift.
This rewrites just the <div class="mobile-nav-continents ...">…</div> block, so the
rest of each drawer is untouched.

Mirrors the desktop dropdown: the interactive-map preview card first, then an
"Explore by Continent" label over the continent jumps and All Destinations.

  py tools/regen_mobile_drawer.py [--dry-run] [--drafts]
"""
import glob, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRY = "--dry-run" in sys.argv
DRAFTS = "--drafts" in sys.argv

BLOCK = re.compile(r'<div class="mobile-nav-continents(?P<open>[^"]*)">.*?</div>', re.S)
CONTS = [("europe", "Europe"), ("americas", "Americas"), ("asia", "Asia"),
         ("africa", "Africa"), ("oceania", "Oceania")]


def ico(pfx, key):
    """Same continent silhouettes as the desktop dropdown (tools/gen_continent_icons.py)."""
    return ('<img class="mnc-ico" src="%sImages/web/continents/%s.svg" width="15" height="15" '
            'alt="" aria-hidden="true" loading="lazy">' % (pfx, key))


def block(pfx, open_cls):
    """Map link first, then the continents.

    No thumbnail and no "Explore by Continent" label on mobile: at drawer width both cost
    more room than they earn, and the continent names are self-explanatory in a list. The
    desktop dropdown keeps both."""
    L = ['<div class="mobile-nav-continents%s">' % open_cls,
         '        <a href="%sdestinations.html#explore-map" class="mnc-map">Explore the Interactive Map</a>' % pfx]
    L += ['        <a href="%sdestinations.html#%s">%s%s</a>'
          % (pfx, slug, ico(pfx, slug), label) for slug, label in CONTS]
    L += ['        <a href="%sdestinations.html">All Destinations</a>' % pfx,
          '      </div>']
    return "\n".join(L)


def main():
    pats = [os.path.join(ROOT, "*.html"), os.path.join(ROOT, "*", "*.html")]
    if DRAFTS:
        pats.append(os.path.join(ROOT, "Drafts", "*", "field-notes.html"))
    changed = skipped = 0
    for p in sorted({q for pat in pats for q in glob.glob(pat)}):
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.startswith(".tmp/"):
            continue
        if rel.startswith("Drafts/") and not DRAFTS:
            continue
        html = open(p, encoding="utf-8").read()
        m = BLOCK.search(html)
        if not m:
            skipped += 1
            continue
        depth = rel.count("/")
        pfx = "../" * depth
        new = html[:m.start()] + block(pfx, m.group("open")) + html[m.end():]
        if new == html:
            continue
        if not DRY:
            open(p, "w", encoding="utf-8", newline="").write(new)
        changed += 1
        print("  %s %s" % ("[dry]" if DRY else "  ok", rel))
    print("\n%sdrawer updated on %d page(s); %d without one"
          % ("[dry-run] " if DRY else "", changed, skipped))


if __name__ == "__main__":
    main()
