#!/usr/bin/env python3
"""Undo HTML-escaping the editor applies to JavaScript inside <script> blocks.

Editing a page that contains an embedded city map can round-trip the <script> body through
an HTML serializer, which escapes the JS operators:

    cw<=vw    becomes  cw&amp;lt;=vw
    s>=maxS   becomes  s&amp;gt;=maxS
    n&&n      becomes  n&amp;amp;&amp;amp;n

The browser does NOT decode entities inside <script> (its content is raw text), so the JS
engine sees the literal characters and throws "Unexpected token '='" — the map's gestures,
hover sync and tap-to-open all stop working, silently, on a page that still looks fine.

This is a sibling of tools/strip_paste_artifacts.py: same cause (editor round-trip), same
place in the workflow. Only <script> bodies are touched; prose entities stay escaped.

  py tools/fix_script_escapes.py --dry-run
  py tools/fix_script_escapes.py [slug]
"""
import argparse, glob, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = re.compile(r"(<script\b[^>]*>)(.*?)(</script>)", re.S)

# most-escaped first, so &amp;amp; is resolved before &amp;
UNESCAPE = [("&amp;amp;", "&"), ("&amp;lt;", "<"), ("&amp;gt;", ">"),
            ("&amp;quot;", '"'), ("&amp;#39;", "'"),
            ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
            ("&amp;", "&")]


def fix_body(js):
    """Return (fixed_js, n_replacements). Leaves clean scripts untouched."""
    n = 0
    for ent, ch in UNESCAPE:
        # `&amp;` alone is legitimate inside a JS *string* that builds a URL, so only
        # rewrite it when the script is clearly already broken by the operator escapes
        if ent == "&amp;" and "&amp;lt;" not in js and "&amp;gt;" not in js:
            continue
        c = js.count(ent)
        if c:
            js = js.replace(ent, ch)
            n += c
    return js, n


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
        files = sorted(glob.glob(os.path.join(ROOT, "*", "*.html")) +
                       glob.glob(os.path.join(ROOT, "Drafts", "*", "field-notes.html")))

    total = touched = 0
    for p in files:
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.startswith((".tmp/", "Drafts/.")):
            continue
        s = open(p, encoding="utf-8").read()
        n_file = [0]

        def rep(m):
            js, n = fix_body(m.group(2))
            n_file[0] += n
            return m.group(1) + js + m.group(3)

        new = SCRIPT.sub(rep, s)
        if n_file[0]:
            total += n_file[0]
            touched += 1
            print("  %-42s %d escaped operator(s)" % (rel, n_file[0]))
            if not a.dry_run:
                open(p, "w", encoding="utf-8", newline="").write(new)
    print("%s%d fix(es) across %d file(s)"
          % ("[dry-run] " if a.dry_run else "", total, touched))


if __name__ == "__main__":
    main()
