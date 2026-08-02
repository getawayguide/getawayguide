#!/usr/bin/env python3
"""Strip Google-Docs paste artifacts from inline styles on live field-notes pages.

Freshly promoted drafts carry `font-size: 1rem` and `color: rgb(28, 40, 33)` on inline spans
(pasted from Google Docs). Both fight the page CSS: 1rem is already the body size, and that
colour is darker than the body grey. Everything else in a style attribute is left byte-identical,
and <style>/<script> blocks are skipped so embedded city-map CSS/JS can never be touched.

  python tools/strip_paste_artifacts.py            # all live field-notes pages
  python tools/strip_paste_artifacts.py croatia    # one country
"""
import re, sys, glob, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = re.compile(r'(?:font-size:\s*1rem|color:\s*rgb\(28,\s*40,\s*33\))\s*;?\s*', re.I)
BLOCK = re.compile(r'<(style|script)\b.*?</\1>', re.S | re.I)


def scrub(seg):
    n = len(ART.findall(seg))
    if not n:
        return seg, 0
    def fix_attr(m):
        inner = ART.sub("", m.group(1)).strip().strip(";").strip()
        return ' style="%s"' % inner if inner else ""
    return re.sub(r'\s*style="([^"]*)"', fix_attr, seg), n


def strip(html):
    out, last, removed = [], 0, 0
    for m in BLOCK.finditer(html):
        seg, n = scrub(html[last:m.start()])
        out.append(seg); removed += n
        out.append(m.group(0))              # keep style/script verbatim
        last = m.end()
    seg, n = scrub(html[last:]); out.append(seg); removed += n
    return "".join(out), removed


def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else None
    pat = "%s/field-notes.html" % slug if slug else "*/field-notes.html"
    total = 0
    for f in sorted(glob.glob(os.path.join(ROOT, pat))):
        h = open(f, encoding="utf-8").read()
        new, n = strip(h)
        if n:
            open(f, "w", encoding="utf-8", newline="").write(new)
            print("  %-34s removed %d" % (os.path.relpath(f, ROOT).replace("\\", "/"), n))
            total += n
    print("paste artifacts removed:", total)


if __name__ == "__main__":
    main()
