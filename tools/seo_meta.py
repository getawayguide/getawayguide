#!/usr/bin/env python3
"""Add the head-level SEO metadata every page is missing: canonical, article dates, og:type.

Three independent passes, each opt-in, all safe to re-run (they update in place rather than
appending a second tag):

  --canonical  <link rel="canonical"> on every page. GitHub Pages will happily serve the
               same document at /croatia/field-notes.html, /croatia/field-notes.html?x=1 and
               (behind a proxy) apex vs www, so without this those variants compete with
               each other. The URL is taken from the page's own og:url when present, which
               is already correct sitewide, rather than re-derived from the path.

  --dates      datePublished / dateModified into the Article JSON-LD, read from
               .tmp/seo_dates.json (see .tmp/seo_content_dates.py). Those dates come from
               when the page's PROSE last changed, not `git log -1` - sitewide asset passes
               touched all 48 files on one day and publishing that as dateModified would be
               untrue and would look like a bulk refresh.

  --og-type    og:type article (currently "website" on article pages).

  py tools/seo_meta.py --canonical --dry-run
  py tools/seo_meta.py --canonical
"""
import argparse, json, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://getawayguide.io"
DATES = os.path.join(ROOT, ".tmp", "seo_dates.json")


def pages():
    out = subprocess.run(["git", "ls-files", "*.html"], capture_output=True, text=True,
                         cwd=ROOT).stdout.split()
    return [f for f in out if not f.startswith(("Drafts/", ".tmp/"))
            and os.path.basename(f) not in ("editor.html", "404.html")]


def canonical_url(s, rel):
    """Prefer the page's own og:url - it is already correct sitewide.

    Note the backreference: the value ends at whichever quote OPENED it. A [^"']+ class
    would instead stop at the first apostrophe inside the value, which silently truncates
    any content containing one.
    """
    m = re.search(r'<meta[^>]*property=["\']og:url["\'][^>]*content=(["\'])(.*?)\1', s, re.I)
    if m:
        return m.group(2).strip()
    if rel == "index.html":
        return SITE + "/"
    return "%s/%s" % (SITE, rel.replace("\\", "/"))


def add_canonical(s, rel):
    url = canonical_url(s, rel)
    tag = '<link rel="canonical" href="%s">' % url
    cur = re.search(r'<link[^>]*rel=["\']canonical["\'][^>]*>', s, re.I)
    if cur:
        return (s if cur.group(0) == tag else s.replace(cur.group(0), tag)), url
    # sit it right after og:url when there is one, else just before </head>
    anchor = re.search(r'<meta[^>]*property=["\']og:url["\'][^>]*>\s*', s, re.I)
    if anchor:
        return s[:anchor.end()] + tag + "\n    " + s[anchor.end():], url
    i = s.lower().find("</head>")
    return s[:i] + "    " + tag + "\n" + s[i:], url


def add_og_type(s):
    # ONLY pages that actually carry Article schema. About/Privacy/Contact and the index
    # are genuinely og:type website, and relabelling them "article" would be a lie that
    # also breaks how they unfurl when shared.
    if '"@type": "Article"' not in s and '"@type":"Article"' not in s:
        return s, False
    m = re.search(r'(<meta[^>]*property=["\']og:type["\'][^>]*content=)(["\'])(.*?)\2',
                  s, re.I)
    if not m or m.group(3) == "article":
        return s, False
    return s[:m.start(3)] + "article" + s[m.end(3):], True


def add_dates(s, rel, dates):
    d = dates.get(rel)
    if not d:
        return s, False
    pub, mod = d["published"], d["content_modified"]
    changed = False

    def fix(block):
        nonlocal changed
        if '"@type": "Article"' not in block and '"@type":"Article"' not in block:
            return block
        for key, val in (("datePublished", pub), ("dateModified", mod)):
            cur = re.search(r'"%s"\s*:\s*"([^"]*)"' % key, block)
            if cur:
                if cur.group(1) != val:
                    block = block[:cur.start(1)] + val + block[cur.end(1):]
                    changed = True
            else:
                # insert after the headline so the JSON stays readable
                a = re.search(r'"headline"\s*:\s*"[^"]*",?', block)
                if a:
                    block = (block[:a.end()] + '\n      "%s": "%s",' % (key, val)
                             + block[a.end():])
                    changed = True
        return block

    out = re.sub(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
                 lambda m: m.group(0).replace(m.group(1), fix(m.group(1))), s, flags=re.S)
    return out, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canonical", action="store_true")
    ap.add_argument("--dates", action="store_true")
    ap.add_argument("--og-type", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not (a.canonical or a.dates or a.og_type):
        ap.error("pick at least one pass: --canonical / --dates / --og-type")

    dates = {}
    if a.dates:
        if not os.path.exists(DATES):
            sys.exit("missing %s - run .tmp/seo_content_dates.py first" % DATES)
        dates = json.load(open(DATES, encoding="utf-8"))

    n_can = n_og = n_dt = 0
    for rel in pages():
        p = os.path.join(ROOT, rel)
        s0 = open(p, encoding="utf-8", errors="replace").read()
        s = s0
        if a.canonical:
            s, url = add_canonical(s, rel)
            if s != s0:
                n_can += 1
                if a.dry_run and n_can <= 4:
                    print('  %-34s + <link rel="canonical" href="%s">' % (rel, url))
        if a.og_type:
            s, ch = add_og_type(s)
            n_og += ch
        if a.dates:
            s, ch = add_dates(s, rel, dates)
            if ch:
                n_dt += 1
                if a.dry_run and n_dt <= 4:
                    d = dates[rel]
                    print('  %-34s + datePublished %s / dateModified %s'
                          % (rel, d["published"], d["content_modified"]))
        if s != s0 and not a.dry_run:
            open(p, "w", encoding="utf-8", newline="").write(s)

    tag = "[dry-run] " if a.dry_run else ""
    if a.canonical:
        print("%scanonical added/updated on %d page(s)" % (tag, n_can))
    if a.og_type:
        print("%sog:type -> article on %d page(s)" % (tag, n_og))
    if a.dates:
        print("%sArticle dates set on %d page(s)" % (tag, n_dt))


if __name__ == "__main__":
    main()
