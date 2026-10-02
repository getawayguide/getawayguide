#!/usr/bin/env python3
"""Keep the posts page and the home page's article count in step with what is live (Kevin, 2026-10-02,
Armenia launch: "update the '40 and counting' in the subtitle to reflect the latest number of articles
and armenia articles on the post page go under indepth guides. These should both be wired as permanent
changes").

  1. posts.html  every article of an In-Depth Guide country (publish_country.FULL_GUIDES) has a card in
                 the Guides section. Cards already there are kept as they are (El Salvador's were written
                 by hand); a missing one is built from the country page's card (title, tag, photo and
                 its framing) and the article's own hero lead, which is what the hand-written cards
                 use. Countries run newest launch first, and a guide country's card leaves the Field
                 notes section, since its field notes are retired.
  2. index.html  the hero's "N and counting." is every live article: each field-notes page that is
                 still a page (a retired one is a redirect stub) plus every article of a guide country
                 (its country page is not an article).

retire_field_notes.py and publish_country.py run this, so a launch keeps both right. Re-run
tools/fit_backgrounds.py on posts.html afterwards: new cards need their per-window photo files.

    python tools/sync_guides.py --dry-run
    python tools/sync_guides.py
"""
import html
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
SKIP = ("archive/", "Drafts/", ".tmp/", "tools/", "preview/", "node_modules/")
# a country-page tag -> the label posts uses before " · Country" (a city guide is named for its city)
LABEL = {"Travel Guide": "Itinerary"}


def _read(p):
    return io.open(ROOT / p, encoding="utf-8", newline="").read()


def _write(p, s):
    io.open(ROOT / p, "w", encoding="utf-8", newline="").write(s)


def guides():
    """[(name, slug)] in launch order, newest first. FULL_GUIDES is kept alphabetical, so launch order
    comes from the country pages' first commit (a page not committed yet is the newest)."""
    import subprocess
    from publish_country import FULL_GUIDES
    out = []
    for name, _iso, _cont, href in FULL_GUIDES:
        slug = href.split("/")[0]
        t = subprocess.run(["git", "log", "--diff-filter=A", "--format=%ct", "--", href], cwd=ROOT,
                           capture_output=True, text=True).stdout.split()
        out.append((int(t[-1]) if t else 1 << 40, name, slug))
    return [(n, s) for _t, n, s in sorted(out, reverse=True)]


def is_stub(raw):
    return 'http-equiv="refresh"' in raw and "location.replace(" in raw


def live_articles():
    """every live article: field-notes pages that are still pages + each guide country's articles"""
    arts = []
    for p in sorted(ROOT.glob("*/field-notes.html")):
        r = p.relative_to(ROOT).as_posix()
        if not r.startswith(SKIP) and not is_stub(_read(r)):
            arts.append(r)
    for _name, slug in guides():
        for p in sorted((ROOT / slug).glob("*.html")):
            if p.name not in ("index.html", "field-notes.html"):
                arts.append(p.relative_to(ROOT).as_posix())
    return arts


def country_cards(slug):
    """the article cards on <slug>/index.html, in page order: href, tag, title, photo, framing"""
    raw = _read(slug + "/index.html")
    out = []
    for m in re.finditer(r'<div class="country-article-card"[^>]*?onclick="location\.href=\'([^\']+)\'"[^>]*?'
                         r'style="([^"]*)"[^>]*>(.*?)</h3>', raw, re.S):
        href, style, body = m.groups()
        tag = re.search(r'country-article-card-tag">([^<]*)<', body)
        title = re.search(r'country-article-card-title">(.*?)$', body, re.S)
        img = re.search(r"url\('([^']+\.(?:jpe?g|png|webp))'\)\s*([\d.]+%\s+[\d.]+%)?", style)
        out.append({"href": href, "tag": tag.group(1).strip() if tag else "",
                    "title": title.group(1).strip() if title else "",
                    "img": img.group(1) if img else "", "pos": (img.group(2) if img and img.group(2) else "50% 50%")})
    return out


def lead(page):
    """the line under the article's title: an article's hero lead, or the itinerary template's
    subtitle (.artsub), read from the layout variant the page actually shows"""
    from page_variants import blank_hidden
    raw = blank_hidden(_read(page))
    m = re.search(r'class="article-lead"[^>]*>(.*?)</p>', raw, re.S) or \
        re.search(r'class="artsub"[^>]*>(.*?)</(?:p|div)>', raw, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""


def card_html(name, slug, c):
    label = LABEL.get(c["tag"], c["tag"])
    if c["tag"] == "City Guide":
        label = " ".join(w.capitalize() for w in c["href"][:-5].split("-"))
    img = re.sub(r"^(\.\./)+", "", c["img"])
    return ('<a href="%s/%s"><div class="card-img bg" style="background-image:url(\'%s\');background-size:cover;'
            'background-position:%s"></div><div class="kick">%s · %s</div><div class="serif card-h">%s</div>'
            '<div class="card-p">%s</div></a>' % (slug, c["href"], img, c["pos"], html.escape(label), html.escape(name),
                                                  c["title"], lead("%s/%s" % (slug, c["href"]))))


CARD = re.compile(r'<a href="([^"]+)"><div class="card-img bg[^"]*"[^>]*></div>.*?</a>', re.S)


def sync_posts(dry=False):
    raw = _read("posts.html")
    g0 = raw.index('<div class="sec pad gg-dispatch">')
    gs = raw.index('<div class="cards">', g0) + len('<div class="cards">')
    ge = raw.index('</div></div><div class="sec pad gg-notes">', gs)
    have = {m.group(1): m.group(0) for m in CARD.finditer(raw[gs:ge])}
    order, log, used = [], [], set()
    for name, slug in guides():
        for c in country_cards(slug):
            href = "%s/%s" % (slug, c["href"])
            if href in have:
                order.append(have[href])
            else:
                order.append(card_html(name, slug, c))
                log.append("posts.html: Guides card added for %s" % href)
            used.add(href)
        for href, card in have.items():            # a guide's article the country page has no card for
            if href.startswith(slug + "/") and href not in used:
                order.append(card)
                used.add(href)
    order += [card for href, card in have.items() if href not in used]
    new = raw[:gs] + "".join(order) + raw[ge:]
    # a guide country's field notes are retired: its card leaves the Field notes section
    n0 = new.index('<div class="sec pad gg-notes">')
    notes = new[n0:]
    for _name, slug in guides():
        rx = re.compile(r'<a href="%s/(?:field-notes|index)\.html"><div class="card-img bg[^"]*"[^>]*></div>.*?</a>' % re.escape(slug), re.S)
        notes, k = rx.subn("", notes, count=1)
        if k:
            log.append("posts.html: %s's card left the Field notes section" % slug)
    new = new[:n0] + notes
    if new != raw and not dry:
        _write("posts.html", new)
    return log or ["posts.html: Guides section already up to date"]


def sync_count(dry=False):
    raw = _read("index.html")
    n = len(live_articles())
    new, k = re.subn(r"\b\d+(?= and counting\.)", str(n), raw, count=1)
    if not k:
        return ["index.html: no 'N and counting.' in the hero - count is %d" % n]
    if new != raw and not dry:
        _write("index.html", new)
    return ["index.html: %d and counting%s" % (n, "" if new != raw else " (unchanged)")]


def run(dry=False):
    return sync_posts(dry) + sync_count(dry)


if __name__ == "__main__":
    for line in run("--dry-run" in sys.argv):
        print(("[dry] " if "--dry-run" in sys.argv else "") + line)
