#!/usr/bin/env python3
"""Apply the approved wording changes: <title>, og:title, <h1>, meta description, img alt.

Reads the reviewed proposals (.tmp/seo_proposals.json, .tmp/seo_alt.json) rather than
re-deriving anything, so what ships is exactly what was signed off. The mechanical head
changes (canonical / og:type / Article dates) live in tools/seo_meta.py.

Every value is HTML-escaped on the way in: titles carry "&" ("Split, Dubrovnik & Hvar")
and writing that raw into markup produces an invalid document.

  py tools/seo_apply.py --dry-run
  py tools/seo_apply.py
"""
import argparse, html, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROPS = os.path.join(ROOT, ".tmp", "seo_proposals.json")
ALTS = os.path.join(ROOT, ".tmp", "seo_alt.json")


def set_title(s, new):
    m = re.search(r"(<title>)(.*?)(</title>)", s, re.S)
    if not m:
        return s, False
    return s[:m.start(2)] + html.escape(new) + s[m.end(2):], True


def set_meta(s, attr, key, new):
    # The value is delimited by whichever quote opened it, so capture that and match to the
    # SAME one. A [^"']* class instead stops dead at the first apostrophe inside the value
    # ("Field notes from Australia's east coast"), which replaces a fragment and leaves the
    # rest of the old text stranded after the new.
    m = re.search(r'(<meta[^>]*%s=["\']%s["\'][^>]*content=)(["\'])(.*?)\2'
                  % (attr, re.escape(key)), s, re.I | re.S)
    if not m:
        return s, False
    return s[:m.start(3)] + html.escape(new, quote=True) + s[m.end(3):], True


def set_h1(s, new):
    m = re.search(r"(<h1[^>]*>)(.*?)(</h1>)", s, re.S)
    if not m:
        return s, False
    return s[:m.start(2)] + html.escape(new) + s[m.end(2):], True


def set_jsonld(s, headline, description):
    """Keep the Article node's headline/description in step with the meta tags.

    Structured data is read independently of the <title>, so leaving the old
    "Croatia - Field Notes - Getawayguide" headline behind means the page asserts two
    different titles. Values are JSON-escaped, NOT HTML-escaped: a <script> body is raw
    text, so writing &amp; in there would put a literal "&amp;" in the headline.
    """
    n = [0]

    def block(m):
        body = m.group(1)
        if '"@type": "Article"' not in body and '"@type":"Article"' not in body:
            return m.group(0)
        for key, val in (("headline", headline), ("description", description)):
            if val is None:
                continue
            cur = re.search(r'("%s"\s*:\s*)("(?:[^"\\]|\\.)*")' % key, body)
            if cur and json.loads(cur.group(2)) != val:
                body = body[:cur.start(2)] + json.dumps(val, ensure_ascii=False) + body[cur.end(2):]
                n[0] += 1
        return m.group(0).replace(m.group(1), body)

    return re.sub(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', block, s, flags=re.S), n[0]


def set_alts(s, alts):
    """Fill empty alt="" on article photos, in document order.

    Matched positionally rather than by filename: the same photo can legitimately appear
    twice on a page, and a filename-keyed lookup would collapse those into one entry.
    """
    n = [0]
    todo = list(alts)

    def rep(m):
        tag = m.group(0)
        src = re.search(r'src="([^"]*)"', tag)
        if not src or not todo:
            return tag
        if not re.search(r"Images/web/(?!flags/|city-maps/|continents/|og/|nav)[A-Za-z][^/]*/",
                         src.group(1)):
            return tag
        a = re.search(r'alt="([^"]*)"', tag)
        if a and a.group(1).strip():
            return tag
        text = html.escape(todo.pop(0)["alt"], quote=True)
        n[0] += 1
        if a:
            return tag[:a.start(1)] + text + tag[a.end(1):]
        return tag[:-1].rstrip() + ' alt="%s">' % text

    return re.sub(r"<img\b[^>]*>", rep, s), n[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    props = {r["file"]: r for r in json.load(open(PROPS, encoding="utf-8"))}
    alts = json.load(open(ALTS, encoding="utf-8"))
    by_file = {}
    for r in alts:
        by_file.setdefault(r["file"], []).append(r)

    tot = dict(title=0, ogtitle=0, h1=0, desc=0, jsonld=0, alt=0)
    for rel in sorted(set(props) | set(by_file)):
        p = os.path.join(ROOT, rel)
        s0 = open(p, encoding="utf-8", errors="replace").read()
        s = s0
        r = props.get(rel)
        if r:
            if r["title_old"] != r["title_new"]:
                s, ok = set_title(s, r["title_new"]); tot["title"] += ok
                s, ok = set_meta(s, "property", "og:title", r["title_new"]); tot["ogtitle"] += ok
            if r["h1_old"] != r["h1_new"]:
                s, ok = set_h1(s, r["h1_new"]); tot["h1"] += ok
            if r["desc_change"] != "none":
                s, ok = set_meta(s, "name", "description", r["desc_new"]); tot["desc"] += ok
                s, _ = set_meta(s, "property", "og:description", r["desc_new"])
            s, n = set_jsonld(s, r["title_new"], r["desc_new"]); tot["jsonld"] += n
        if rel in by_file:
            s, n = set_alts(s, by_file[rel]); tot["alt"] += n
        if s != s0 and not a.dry_run:
            open(p, "w", encoding="utf-8", newline="").write(s)

    tag = "[dry-run] " if a.dry_run else ""
    print("%stitles %d | og:title %d | h1 %d | descriptions %d | json-ld %d | alt %d"
          % (tag, tot["title"], tot["ogtitle"], tot["h1"], tot["desc"], tot["jsonld"], tot["alt"]))


if __name__ == "__main__":
    main()
