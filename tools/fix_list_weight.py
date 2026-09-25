#!/usr/bin/env python3
"""Make hand-styled list items read at the same weight as body text.

The site's lists come in two shapes. A plain <ul> is styled by artifact.css
(`.b-a li { font-weight:400 }`) and matches the prose around it. A hand-styled
`<ul style="list-style:none">` gets no weight from the stylesheet at all, so the
draft builder wrote `font-weight:300` onto each <li> inline - which renders those
bullets LIGHTER than every paragraph beside them.

Kevin, 2026-09-24: "make the list the same weight as normal text". Measured in
the editor first: paragraphs 400, plain bullets 400, hand-styled bullets 300.
So the fix is to drop the inline weight, not to level the others down. That also
matches the published El Salvador guide, where no <li> carries an inline style.

Only `font-weight` is removed. Padding, borders and every other inline rule stay,
and the bold lead inside a bullet still comes through at 600 from artifact.css.

archive/ is deliberately skipped: it is the frozen pre-redesign snapshot, kept
for comparison, and editing it is churn in every future diff for no live benefit
(the same rule tools/fix_img_perf.py follows).

  py tools/fix_list_weight.py --dry-run
  py tools/fix_list_weight.py
"""
import glob
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRY = "--dry-run" in sys.argv
SKIP = (".tmp/", "preview/", "node_modules/", "archive/", "Drafts/.Archive")

UL = re.compile(r"<ul[^>]*>.*?</ul>", re.S)
LI = re.compile(r"<li([^>]*)>")
HAND_STYLED = re.compile(r"list-style\s*:\s*none")
WEIGHT = re.compile(r"\s*font-weight\s*:\s*[^;\"]+;?")


def fix(html):
    """Returns (new_html, items_changed)."""
    n = [0]

    def one_list(m):
        block = m.group(0)
        if not HAND_STYLED.search(block):
            return block                      # artifact.css already styles this one

        def one_item(li):
            attrs = li.group(1)
            st = re.search(r'style="([^"]*)"', attrs)
            if not st or "font-weight" not in st.group(1):
                return li.group(0)
            n[0] += 1
            cleaned = WEIGHT.sub("", st.group(1)).strip().strip(";").strip()
            attrs = (attrs.replace(st.group(0), 'style="%s"' % cleaned) if cleaned
                     else attrs.replace(st.group(0), ""))
            return "<li" + re.sub(r"\s+", " ", attrs).rstrip() + ">"

        return LI.sub(one_item, block)

    return UL.sub(one_list, html), n[0]


def pages():
    seen = set()
    for pat in ("**/*.html", "Drafts/.Full Articles/*/*.html"):
        for p in glob.glob(os.path.join(ROOT, pat), recursive=True):
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            if rel.startswith(SKIP) or rel in seen:
                continue
            seen.add(rel)
            yield p, rel


def main():
    total = touched = 0
    for path, rel in sorted(pages(), key=lambda x: x[1]):
        s = io.open(path, encoding="utf-8", newline="").read()
        new, n = fix(s)
        if not n:
            continue
        total += n
        touched += 1
        print("  %-58s %3d item(s)" % (rel, n))
        if not DRY:
            io.open(path, "w", encoding="utf-8", newline="").write(new)
    print("%s%d list item(s) across %d page(s) now read at the body weight"
          % ("[dry-run] " if DRY else "", total, touched))


if __name__ == "__main__":
    main()
