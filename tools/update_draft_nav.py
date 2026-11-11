# -*- coding: utf-8 -*-
"""Regenerate the Destinations mega-menu on every Drafts/ page.

Everything (the country set, continent grouping, the In-Depth Guide legend + green dots) is rebuilt from
destinations.html, the single source of truth, exactly like publish_country.py step 4c does for live pages.

2026-10-24: every draft now sits three folders deep (Drafts/<Upcoming|Full Articles|Field Notes>/<country>/), so the
prefix is worked out from each page's own path (it was a fixed "../../", which would have broken every link in the
menu), and it covers the articles too, not only field notes: the Full Articles still linked Armenia's retired field
notes after its launch.

2026-11-11, NAV BLOCK ONLY, byte-safe (the North Macedonia launch left every draft's menu stale, because the launch
skipped drafts with uncommitted work). The drafts are where Kevin's unfinished work lives, so this run touches nothing
but the menu, and is safe on a draft with uncommitted changes:
  * only the span from <div class="nav-dropdown ...> to the menu's last </div> is replaced; the whitespace before
    </li>, and every byte before and after, are kept. The run checks that, and writes nothing for a page where the
    bytes outside the menu would differ.
  * the menu keeps its own line endings (CRLF or LF, as the old menu had them), so a CRLF draft never gets LF lines
    and an unchanged menu is a no-op, not a line-ending diff (the 2026-10-24 Explore More lesson: rewriting a CRLF
    file's endings broke open review rounds' finds).
  * written through a temp file and os.replace, so an editor or a reader never sees half a page. The editor's save
    merges an outside change (/api/merge3), and the menu is chrome Kevin never edits, so that merge is clean.
  * the menu is generated, never hand-edited, so his work cannot be inside the span that changes. Review rounds find
    their text by content at apply time (tools/redline.py), not by offset, so a longer menu moves nothing they need.
Skipping dirty drafts instead would leave exactly the pages being worked on stale, which is what happened.

Usage: python tools/update_draft_nav.py [--dry-run] [slug ...]     (no slugs = every draft)
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publish_country import parse_countries, build_nav, NAV_RE
import draft_paths as dp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LAST_DIV = re.compile(r"</div>(?=\s*</li>\Z)")


def nav_swap(cur, nav):
    """`cur` with only its menu replaced by `nav` (build_nav's output, LF), or None when it has no menu. Byte-safe: the
    text outside the menu span, and the whitespace before its </li>, are returned unchanged."""
    m = NAV_RE.search(cur)
    if not m:
        return None
    span = m.group(0)
    d = _LAST_DIV.search(span)                     # the menu ends at its last </div>; "\s*</li>" after it is the page's
    if not d:
        return None
    start, end = m.start(), m.start() + d.end()
    old = cur[start:end]
    nl = "\r\n" if "\r\n" in old else ("\n" if "\n" in old else ("\r\n" if "\r\n" in cur else "\n"))
    new = cur[:start] + nav.replace("\r\n", "\n").replace("\n", nl) + cur[end:]
    # the guard: nothing outside the menu moved
    if new[:start] != cur[:start] or new[len(new) - (len(cur) - end):] != cur[end:]:
        raise AssertionError("nav swap would change bytes outside the menu")
    return new


def write_atomic(path, text):
    tmp = path + ".navtmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def refresh(pages, by_cont, dry=False, root=None, say=print):
    """rebuild the menu on each page (absolute paths); returns (changed, skipped)"""
    root = root or ROOT
    navs, changed, skipped = {}, 0, 0
    for p in pages:
        rel = os.path.relpath(p, root).replace("\\", "/")
        pfx = dp.depth_prefix(rel)
        if pfx not in navs:
            navs[pfx] = build_nav(pfx, by_cont)
        with open(p, encoding="utf-8", newline="") as f:
            cur = f.read()
        if '<div class="nav-dropdown' not in cur:
            skipped += 1
            continue
        try:
            new = nav_swap(cur, navs[pfx])
        except AssertionError as e:
            say(f"  REFUSED {rel}: {e}")
            skipped += 1
            continue
        if new is None or new == cur:
            skipped += 1
            continue
        if not dry:
            write_atomic(p, new)
        say(f"  {'would update' if dry else 'ok'} {rel}")
        changed += 1
    return changed, skipped


def main():
    dry = "--dry-run" in sys.argv
    slugs = [a for a in sys.argv[1:] if not a.startswith("--")] or ["*"]
    dest = open(f"{ROOT}/destinations.html", encoding="utf-8").read()
    by_cont = parse_countries(dest)
    pages = [str(p) for slug in slugs for p in dp.draft_pages(slug)]
    changed, skipped = refresh(pages, by_cont, dry)
    print(f"nav {'would change' if dry else 'regenerated'} on {changed} draft page(s); {skipped} unchanged or without a menu")


if __name__ == "__main__":
    main()
