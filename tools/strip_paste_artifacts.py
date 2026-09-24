#!/usr/bin/env python3
"""Strip Google-Docs paste artifacts from inline styles on live field-notes pages.

Freshly promoted drafts carry `font-size: 1rem` and `color: rgb(28, 40, 33)` on inline spans
(pasted from Google Docs). Both fight the page CSS: 1rem is already the body size, and that
colour is darker than the body grey. Everything else in a style attribute is left byte-identical,
and <style>/<script> blocks are skipped so embedded city-map CSS/JS can never be touched.

  python tools/strip_paste_artifacts.py            # live field-notes pages AND drafts
  python tools/strip_paste_artifacts.py croatia    # one country
  python tools/strip_paste_artifacts.py --live     # published pages only, never Drafts/
                                                   # (what tools/autofix.py runs)
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
    # Live pages AND drafts. This globbed only "*/field-notes.html" from the
    # repo root, so it never saw Drafts/ at all -- and paste artifacts are a
    # DRAFT problem by definition (they arrive with Google-Docs text and should
    # be gone before a page ships). Same blind spot as lint_prose, americanize
    # and lint_site had: the tools were pointed at the published site while the
    # work happens in Drafts/.
    # --live: the PUBLISHED pages only, never Drafts/. Drafts are unfinished writing
    # and go through the review margin, so a routine (tools/autofix.py) must not
    # rewrite them on its own; and Drafts/ is its own repo, so an edit there would
    # not even land in the routine's commit. It would just leave a draft silently
    # changed.
    args = [x for x in sys.argv[1:] if not x.startswith("-")]
    live = "--live" in sys.argv
    slug = args[0] if args else None
    if live:
        pats = ["%s/*.html" % (slug or "*")]
    elif slug:
        pats = ["%s/field-notes.html" % slug,
                "Drafts/%s/field-notes.html" % slug,
                "Drafts/.Full Articles/%s/*.html" % slug]
    else:
        pats = ["*/field-notes.html", "Drafts/*/field-notes.html",
                "Drafts/.Full Articles/*/*.html"]
    total = 0
    for f in sorted({q for pat in pats for q in glob.glob(os.path.join(ROOT, pat))}):
        rel0 = os.path.relpath(f, ROOT).replace("\\", "/")
        if live and rel0.startswith(("archive/", "Drafts/", ".tmp/", "tools/")):
            continue
        h = open(f, encoding="utf-8").read()
        new, n = strip(h)
        if n:
            open(f, "w", encoding="utf-8", newline="").write(new)
            print("  %-34s removed %d" % (os.path.relpath(f, ROOT).replace("\\", "/"), n))
            total += n
    print("paste artifacts removed:", total)


if __name__ == "__main__":
    main()
