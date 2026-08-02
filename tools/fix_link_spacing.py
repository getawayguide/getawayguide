#!/usr/bin/env python3
"""Restore the space lost at a link boundary by Google-Docs paste.

Pasting from Google Docs puts the linked word and the text that follows into adjacent
elements and drops the separating space, producing `<a>Hvar</a><span>is Croatia's…` which
renders as "Hvaris Croatia's". Same pipeline that leaves the `font-size:1rem` spans behind.

Only PROSE is considered (inside <p>/<li>), and only when the character after the link is a
lowercase letter or a digit - so `</a>'s`, `</a>,`, `</a>.` and `</a></li>` are untouched, as
are SVG/script/style and the nav, TOC and footer (whose links legitimately abut other elements).

  python tools/fix_link_spacing.py --dry-run
  python tools/fix_link_spacing.py croatia
"""
import argparse, glob, io, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOCK = re.compile(r"<(style|script|svg|nav|footer)\b.*?</\1>", re.S | re.I)
# </a> then optional inline tags, then a lowercase letter/digit = a sentence continuing
GLUED = re.compile(r"(</a>)((?:<(?:span|b|i|em|strong)\b[^>]*>)*)(?=[a-z0-9])")


def masked_spans(html):
    return [(m.start(), m.end()) for m in BLOCK.finditer(html)]


def in_prose(html, pos):
    """inside a <p> or <li>, and not in a TOC / card / nav-ish container"""
    open_p = html.rfind("<p", 0, pos); close_p = html.rfind("</p>", 0, pos)
    open_li = html.rfind("<li", 0, pos); close_li = html.rfind("</li>", 0, pos)
    if not (open_p > close_p or open_li > close_li):
        return False
    start = max(open_p, open_li)
    return "toc-" not in html[start:pos] and "class=\"nav" not in html[start:pos]


def fix(html):
    skip = masked_spans(html)
    edits = []
    for m in GLUED.finditer(html):
        p = m.start()
        if any(a <= p < b for a, b in skip):
            continue
        if not in_prose(html, p):
            continue
        edits.append(m)
    if not edits:
        return html, []
    out, shown = html, []
    for m in reversed(edits):
        ctx = re.sub(r"<[^>]+>", "", html[max(0, m.start() - 26):m.start() + 26])
        shown.append(re.sub(r"\s+", " ", ctx).strip())
        out = out[:m.end(1)] + " " + out[m.end(1):]      # space right after </a>
    return out, list(reversed(shown))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", nargs="?", help="country slug (live or draft); default = all")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.slug:
        files = [p for p in (os.path.join(ROOT, "%s/field-notes.html" % a.slug),
                             os.path.join(ROOT, "Drafts/%s/field-notes.html" % a.slug))
                 if os.path.exists(p)]
    else:
        files = sorted(glob.glob(os.path.join(ROOT, "Drafts/*/field-notes.html")))
    total = 0
    for f in files:
        html = io.open(f, encoding="utf-8").read()
        new, shown = fix(html)
        if not shown:
            continue
        if not a.dry_run:
            io.open(f, "w", encoding="utf-8", newline="").write(new)
        rel = os.path.relpath(f, ROOT).replace("\\", "/")
        print("  %-38s %d" % (rel, len(shown)))
        for s in shown[:4]:
            print("       …%s…" % s)
        total += len(shown)
    print("\n%s %d missing space(s)" % ("would fix" if a.dry_run else "fixed", total))


if __name__ == "__main__":
    main()
