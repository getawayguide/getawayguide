"""An article's body prose as an ordered list of text blocks. Read-only and deterministic.

Shared by tools/kevin_reviewer_corpus.py (hand-edit mining), tools/shadow_retrieve.py, tools/shadow_backtest.py and
tools/draft_distance.py so that "the prose of an article" means one thing everywhere.

What counts as prose: paragraphs, bullets (each nested bullet is its own block), h2/h3/h4 and the field-notes heading
divs, table rows. What does not: nav, hero, contents boxes, Explore More, guide cards, maps, photos, captions, alt text,
scripts, the currency widget and the hidden layout variants of an itinerary (`data-v` other than "a").

    blocks_of(html) -> [{"tag", "text", "section", "sub", "links": [href, ...], "i"}]
"""
import re
import unicodedata
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SKIP_TAGS = {"figure", "figcaption", "nav", "header", "footer", "script", "style", "svg", "noscript", "h1", "button",
             "picture", "iframe", "select", "form"}
# matched as whole class tokens, or as a prefix where the token ends in "*"
SKIP_CLASSES = {"img-caption", "img-pair", "img-landscape", "img-portrait", "img-single", "photo-block", "route-map",
                "route-map-wrap", "rm-h", "itinerary-map", "citymap", "citymap-fig", "cmhead", "cmmap", "cmkey",
                "cmworld", "cm-ov", "cm-ov*", "toc", "toc*", "xm", "xm-*", "gb-card", "gb-*", "cconv", "cconv*",
                "gg-updated", "gg-footnote", "article-foot", "artsub", "kick", "v-toc", "v-facts", "fn-time", "f-icons",
                "mm-*", "bc-*", "article-hero", "fn-hero", "aff-note", "affiliate-note"}
SKIP_IDS = {"toc", "toc-b", "explore-more"}
BLOCK_TAGS = {"p", "li", "h2", "h3", "h4", "blockquote", "tr"}
# headings written as divs: an itinerary's section and day heads, the field-notes sub-heads
HEAD_DIVS = {"comp-h": "h2", "h-a": "h2", "fn-sub-hd": "h3", "d-row": "h3"}
BODY_CLASSES = {"artbody", "article-body", "fn-body"}
STRIP = re.compile(r"<(script|style|svg|noscript)\b.*?</\1\s*>", re.S | re.I)
SPACE = re.compile(r"\s+")
ZERO_WIDTH = re.compile("[​‌‍⁠﻿­]")


def _skip_class(cls):
    for tok in (cls or "").split():
        if tok in SKIP_CLASSES:
            return True
        if any(p.endswith("*") and tok.startswith(p[:-1]) for p in SKIP_CLASSES):
            return True
    return False


def clean_text(s):
    """Whitespace, invisible characters and Unicode form the editor changes without anyone typing anything."""
    s = unicodedata.normalize("NFC", s or "").replace(" ", " ")
    s = ZERO_WIDTH.sub("", s)
    return SPACE.sub(" ", s).strip()


def compare_key(s):
    """For 'is this the same text?': also folds typographic variants the editor or a paste can swap."""
    s = clean_text(s)
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-"),
                 ("…", "...")):
        s = s.replace(a, b)
    s = re.sub(r"\s+([,.;:!?)])", r"\1", s)    # the space an editor leaves when a tag closes before punctuation
    s = re.sub(r"([(])\s+", r"\1", s)
    return s


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []            # (tag, skip, body, block or None)
        self.skip = self.in_body = 0
        self.open = []             # the blocks being filled, innermost last
        self.done = []
        self.seq = 0
        self.section = self.sub = ""

    def _begin(self, tag):
        self.seq += 1
        b = {"tag": tag, "text": "", "links": [], "seq": self.seq, "section": self.section, "sub": self.sub}
        self.open.append(b)
        return b

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            if tag == "br" and self.open and not self.skip:
                self.open[-1]["text"] += " "
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        tokens = cls.split()
        skip = (tag in SKIP_TAGS or _skip_class(cls) or (a.get("id") in SKIP_IDS)
                or (bool(a.get("data-v")) and a.get("data-v") != "a")
                or (a.get("hidden") is not None) or "display:none" in (a.get("style") or "").replace(" ", ""))
        body = any(t in BODY_CLASSES for t in tokens)
        block = None
        self.skip += skip
        self.in_body += body
        if self.in_body and not self.skip:
            head = next((HEAD_DIVS[t] for t in tokens if t in HEAD_DIVS), None) if tag == "div" else None
            if tag in BLOCK_TAGS or head:
                if tag == "tr" or not (self.open and self.open[-1]["tag"] == "tr"):
                    block = self._begin(head or tag)
            elif tag == "div" and "copy" in tokens:     # itinerary body copy sometimes sits in a div
                block = self._begin("p")
            elif tag == "a" and self.open:
                self.open[-1]["links"].append(a.get("href") or "")
            elif tag in ("td", "th") and self.open and self.open[-1]["tag"] == "tr" and self.open[-1]["text"]:
                self.open[-1]["text"] += " | "
        self.stack.append((tag, skip, body, block))

    def _close_block(self, block):
        if not self.open or block not in self.open:
            return
        while self.open:
            b = self.open.pop()
            b["text"] = clean_text(b["text"].replace("↗", ""))    # the arrow field notes draw after a link
            if b["text"]:
                self.done.append(b)
                if b["tag"] == "h2":
                    self.section, self.sub = b["text"], ""
                    b["section"], b["sub"] = b["text"], ""
                elif b["tag"] == "h3":
                    self.sub = b["sub"] = b["text"]
            if b is block:
                break

    def handle_endtag(self, tag):
        if tag in VOID or not any(s[0] == tag for s in self.stack):
            return
        while self.stack:
            t, skip, body, block = self.stack.pop()
            self.skip -= skip
            self.in_body -= body
            if block is not None:
                self._close_block(block)
            if t == tag:
                break

    def handle_data(self, d):
        if self.open and not self.skip and self.in_body:
            self.open[-1]["text"] += d


def blocks_of(html):
    p = _Parser()
    p.feed(STRIP.sub("", html or ""))
    p.close()
    while p.open:
        p._close_block(p.open[0])
    out = sorted(p.done, key=lambda b: b["seq"])
    for i, b in enumerate(out):
        b["i"] = i
        del b["seq"]
    return out


HEADING_TAGS = {"h2", "h3", "h4"}


def prose_words(blocks):
    """Words of the running text (headings included, since a heading rewrite is an edit)."""
    return [w for b in blocks for w in b["text"].split()]


def article_type(key_or_path):
    """city-guide | itinerary | top-10 | field-notes | country-page, from the comment-store key or a file path."""
    name = re.split(r"[\\/]|__", str(key_or_path).replace(".html", ""))[-1].lower()
    if "field-notes" in name or name.endswith("-fn"):
        return "field-notes"
    if name.startswith("top-10") or name.startswith("top10"):
        return "top-10"
    if "itinerary" in name:
        return "itinerary"
    if name == "index" or name.endswith("-index"):
        return "country-page"
    return "city-guide"


# ---------------------------------------------------------------- section kinds
# Derived from every h2/h3 across Drafts and the live site (2026-11-08). Order matters: first match wins.
SECTION_RULES = [
    (r"getting there|getting to\b|\bby car or bus\b|crossing into|how to get (?:here|there)", "getting-there"),
    (r"getting around|how to get around|\bget around\b", "getting-around"),
    (r"where to eat & stay|where to party", "where-to-eat"),
    (r"where to eat|what to eat|\bfood\b|restaurants?\b|\bcuisine\b", "where-to-eat"),
    (r"where to drink|nightlife|the clubs", "where-to-drink"),
    (r"where to stay|places to stay|accommodation", "where-to-stay"),
    (r"why visit|why go\b|is it worth", "why-visit"),
    (r"top things to do|top 3|top three", "top-things"),
    (r"suggested route|the route\b|route overview|suggested .*itinerary|day by day|stop by stop", "route"),
    (r"overview", "overview"),
    (r"what to know|key facts|^general$|before you go", "before-you-go"),
    (r"when to (?:visit|go)|best time", "when-to-visit"),
    (r"budget|booking & prices", "budget"),
    (r"safety", "safety"),
    (r"\bvisas?\b", "visa"),
    (r"currency", "currency"),
    (r"\blanguage\b", "language"),
    (r"what to bring|packing", "what-to-bring"),
    (r"other places|if you have more time|next stops?", "other-places"),
    (r"day trips?\b|as a base", "day-trips"),
    (r"(?:the )?(?:city|town) itself|^what to do$|^the town$|^the experience$|^other activities$", "the-city"),
]
_SECTION_RULES = [(re.compile(p, re.I), k) for p, k in SECTION_RULES]
SECTION_KINDS = sorted({k for _, k in SECTION_RULES} | {"intro", "sight", "day", "top-10-item", "city", "other"})


def section_kind(heading, kind_of_article="", h2=""):
    """intro, getting-there, where-to-eat, why-visit ... from a heading, its parent h2 and the article type.
    An h3 under "The City Itself" is a `sight`; a numbered itinerary h3 is a `day`; a numbered top-10 h2 a `top-10-item`."""
    heading, h2 = clean_text(heading), clean_text(h2)
    if not heading and not h2:
        return "intro"
    def rule(text):
        for rx, k in _SECTION_RULES:
            if text and rx.search(text):
                return k
        return None
    if kind_of_article == "top-10" and re.match(r"\d+[.)]?\s", heading or h2):
        return "top-10-item"
    if kind_of_article == "itinerary" and re.match(r"\d", heading or ""):
        return "day"
    found = rule(heading)
    if found:
        return found
    if h2 and h2 != heading:
        parent = rule(h2)
        if parent == "the-city":
            return "sight"
        if parent:
            return parent
    if kind_of_article == "field-notes":
        return "city"
    return "sight" if kind_of_article == "city-guide" and heading else "other"
