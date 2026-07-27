#!/usr/bin/env python3
"""Build search-index.json for the site's client-side search.

Three result types the UI shows:
  - Country  : field-notes countries + the two full guides (El Salvador, NZ)
  - Articles : published entries from _content/articles.json
  - Mentions : full body text of every content page, so the client can find a
               word inside an article/field-notes and show a snippet.

Sources: the nav dropdown in index.html (countries + flags), _content/articles.json
(articles), and the rendered body text of each field-notes + article page.
Run after adding/renaming content:  python tools/gen_search_index.py
"""
import json, re, glob, os
from html.parser import HTMLParser
from html import unescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# chrome to exclude from the searchable body text
SKIP_TAGS = {"script", "style", "nav", "footer", "head", "noscript", "svg"}
SKIP_IDS = {"mobile-nav-drawer", "toc-b"}
SKIP_CLASS = ("country-breadcrumb", "country-places-row", "mobile-nav", "mobile-drawer", "toc-")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}


class TextExtractor(HTMLParser):
    """Collect visible body text, skipping chrome (nav, footer, mobile drawer,
    breadcrumb, places row, table of contents, scripts, svg)."""
    def __init__(self):
        super().__init__()
        self.stack = []        # per open element: did it start a new skip region
        self.skip = 0          # >0 while inside any skip region
        self.parts = []
        self.title = None
        self._grab_h1 = False

    def _is_skip(self, tag, a):
        return (tag in SKIP_TAGS or a.get("id") in SKIP_IDS
                or any(sub in a.get("class", "") for sub in SKIP_CLASS))

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in VOID:
            s = self._is_skip(tag, a)
            self.stack.append(s)
            if s:
                self.skip += 1
        if tag == "h1" and self.skip == 0:
            self._grab_h1 = True

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.stack and self.stack.pop():
            self.skip -= 1
        if tag == "h1":
            self._grab_h1 = False

    def handle_data(self, data):
        if self.skip == 0 and data.strip():
            self.parts.append(data)
            if self._grab_h1 and not self.title:
                self.title = " ".join(data.split())

    def text(self):
        s = unescape(" ".join(self.parts))
        return re.sub(r"\s+", " ", s).strip()


def page_text(path):
    html = open(path, encoding="utf-8").read()
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    doctitle = unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else ""
    p = TextExtractor()
    p.feed(html)
    return (p.title or doctitle or "").strip(), p.text()


def rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def main():
    index_html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

    # 1) Countries + flags from the nav dropdown (single source of truth)
    countries, seen = [], set()
    for href, flag, name in re.findall(
            r'<a href="([^"]+)" class="nav-dropdown-item">'
            r'<img[^>]*flagcdn\.com/\d+x\d+/([a-z]+)\.png[^>]*>'
            r'<span class="country-name">([^<]+)</span>', index_html):
        url = href.lstrip("./")
        if url in seen:
            continue
        seen.add(url)
        kind = "guide" if url.endswith("index.html") else "field-notes"
        countries.append({"name": unescape(name.strip()), "url": url,
                          "flag": flag, "kind": kind})

    # 2) Articles from the CMS content file (published only)
    arts = json.load(open(os.path.join(ROOT, "_content", "articles.json"), encoding="utf-8"))
    articles = [{"title": unescape(a["title"]), "url": a["path"].lstrip("./"),
                 "country": a.get("country", ""), "tag": a.get("tag", ""),
                 "date": a.get("date", "")}
                for a in arts["articles"] if a.get("status") == "published"]

    # 3) Body text of every content page (field-notes + article pages) for Mentions
    content_paths = sorted(glob.glob(os.path.join(ROOT, "*", "field-notes.html")))
    content_paths += [os.path.join(ROOT, a["url"].replace("/", os.sep)) for a in articles]
    pages, done = [], set()
    for path in content_paths:
        url = rel(path)
        if url in done or not os.path.exists(path):
            continue
        done.add(url)
        title, text = page_text(path)
        if text:
            pages.append({"url": url, "title": title, "text": text})

    out = {"countries": countries, "articles": articles, "pages": pages}
    # Written as a .js that assigns a global (not .json) so it loads via a
    # <script> tag — which works when the site is opened from file:// (offline),
    # where fetch() of a local file is blocked by the browser.
    dst = os.path.join(ROOT, "search-index.js")
    with open(dst, "w", encoding="utf-8") as f:
        f.write("window.__SEARCH_INDEX__=")
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";")
    # remove the old fetch-based artifact if present
    old = os.path.join(ROOT, "search-index.json")
    if os.path.exists(old):
        os.remove(old)
    kb = os.path.getsize(dst) / 1024
    print(f"wrote search-index.js  ({len(countries)} countries, "
          f"{len(articles)} articles, {len(pages)} pages, {kb:.0f} KB)")


if __name__ == "__main__":
    main()
