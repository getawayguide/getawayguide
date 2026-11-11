"""Cross-link a country's articles (Kevin, 2026-11-10: "I want the articles to cross link" ... "make the
crosslink a tool that can be replicated").

One pass over a country's live pages, the same way at every launch:

  mention   the first bare mention of a sibling article's place in each section gets a link to that article:
            "Skopje", "Lake Ohrid", "Krushevo guide", "my itinerary", "top 10", "field notes". The whole name is
            linked ("Lake Ohrid", never just "Ohrid").

NO RELINK PASS. Until 2026-11-10 a second pass rewrote Google Maps links to places his guides cover into links to the
guide ("Kale Fortress" -> skopje.html#kale). Kevin, 2026-11-10: "map links should be linked to map pins like google
maps or booking links". An existing link (a Maps pin, Booking.com, Hostelworld, GetYourGuide... or anything else) is
never touched: the mention pass only wraps bare text, and apply_items() refuses to write a page if any <a> that was
there before is not there afterwards, byte for byte. Do not bring the relink pass back.

Judgment for a mention (each is a reason the report prints; "review" items are written to a file, never guessed):
  self          a page never links to itself
  longer-name   the name is part of a longer proper name ("Bitola City Park", "Skopje 2014", "St. Clement of Ohrid")
  attributive   "Skopje bus station", "Ohrid trout": a thing, not the place
  already       the section already links to that page: no second link
  in-link       text already inside an <a> is never linked again (hard guard, scan and apply both)
  zones         nothing inside headings, captions, figures, alt text, the contents box, cards, key facts, Explore More

Report-only by default; --apply writes. Byte-preserving (newline=""): only new <a> tags are added around bare text,
and every written href is checked to resolve (file + #id) before the file is saved. Repoints (other pages linking
<country>/field-notes.html#<town> once the town has a guide) are review-only, written to
.tmp/crosslink/<country>-review.md (+ .json).

    python tools/crosslink.py north-macedonia                 # report
    python tools/crosslink.py north-macedonia --apply         # write the mention links
    python tools/crosslink.py north-macedonia --pages north-macedonia/skopje.html --no-repoints

Callers: the launch tools (tools/retire_field_notes.py, tools/publish_country.py) call run(country, apply=True), which
has no relink pass. The Launch tab's relink check (tools/prelaunch.py) is retired.
Extra names per article: tools/relink_aliases.json {live path: [aliases]}.
"""
import argparse
import html as H
import io
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import draft_check as dc                     # the name/context rules tuned on 209 drafts (2026-11-09/10)
from page_variants import blank_hidden        # hidden layout variants are not content (same length)

ALIASES = TOOLS / "relink_aliases.json"
REVIEW_DIR = ROOT / ".tmp" / "crosslink"

# ----------------------------------------------------------------------------- names
GENERIC_NAMES = {"coast", "the coast", "valleys", "the valleys", "crowds", "city", "town", "turquoise coast",
                 "the turquoise coast", "capital", "guide", "itinerary"}
SLUG_TAILS = {"hike", "guide", "day", "trip", "tour", "itinerary"}
# a one-word place name also matches its link written with one of these after it ("Acatenango Volcano",
# "Orgov Observatory", "Lake Ohrid"), never with anything else ("Gyumri Train Station", "Bitola City Park")
PLACE_TAILS = {"volcano", "observatory", "telescope", "monastery", "city", "town", "village", "island", "islands",
               "lake", "national", "park", "hike", "radio", "space", "fortress", "cathedral", "canyon", "bay",
               "beach", "mountain", "mountains", "peninsula", "valley", "gorge", "springs", "falls", "region", "the", "of"}
ATTRIBUTIVE_EXTRA = re.compile(r"(?:'s|’s)?\s+(?:bus|buses|minibus|minibuses|taxis?|trout|pearls?|side|border|airport|dialect|accent)\b", re.I)

ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
HREF = re.compile(r'\bhref="([^"]*)"', re.I)
A_OPEN = re.compile(r"<a\b", re.I)
A_CLOSE = re.compile(r"</a\s*>", re.I)
MASK = re.compile(r"<(script|style|svg)\b.*?</\1>|<!--.*?-->", re.S | re.I)
TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
VOID = {"br", "img", "source", "input", "hr", "meta", "link", "wbr", "col", "track"}
SKIP_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "figure", "figcaption", "nav", "header", "footer", "button", "thead", "title"}
SKIP_CLASS = re.compile(r"(?:^|\s)(?:img-caption|[\w-]*caption[\w-]*|toc[\w-]*|hero[\w-]*|[\w-]*card[\w-]*|xm[\w-]*|fn-sub-hd|"
                        r"fn-time|breadcrumb[\w-]*|updated[\w-]*|footnote[\w-]*|key-facts|kf[\w-]*|bm-map|[\w-]*legend[\w-]*|"
                        r"btn[\w-]*|cta[\w-]*|gl|artsub|bio[\w-]*)(?:\s|$)")
BLOCK_TAGS = {"p", "li", "td", "th", "dd", "dt", "blockquote"}
SAFE_HREF = re.compile(r"^(?:\.\./)*[\w-]+(?:/[\w-]+)*\.html(?:#[\w-]+)?$")


def _read(rel):
    return io.open(ROOT / rel, encoding="utf-8", errors="replace", newline="").read()


def _text(s):
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _fold(s):
    """lowercase, accents off, punctuation to spaces: 'El Paredón' and 'el paredon' are one name"""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _tokens(s):
    return re.findall(r"[a-z0-9]+", _fold(re.sub(r"['’]s\b", "", s or "")))


def _distinct(s):
    return [t for t in _tokens(s) if t not in dc.NAME_STOP and t not in dc.NAME_TYPE]


def kind_of(rel):
    name = rel.rsplit("/", 1)[-1]
    if name == "index.html":
        return "country"
    if name == "field-notes.html":
        return "field-notes"
    if "itinerary" in name:
        return "itinerary"
    if name.startswith("top-10"):
        return "top10"
    return "guide"


def title_of(html):
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    return _text(m.group(1)) if m else ""


def body_span(html):
    """where the article's own text is: from the article body to the footer (hero, nav, footer out)"""
    m = re.search(r'class="(?:article-body|artbody|fn-body|fn-content)[^"]*"', html)
    start = m.start() if m else 0
    end = html.find("<footer", start)
    return start, (end if end > 0 else len(html))


def resolve_rel(href, page_rel):
    """a page-relative href to a repo path, or None for anything that is not a local file"""
    h = H.unescape(href).split("#")[0].split("?")[0].strip()
    if not h or re.match(r"^[a-z]+:|^//", h, re.I):
        return None
    from urllib.parse import unquote
    if h.startswith("/"):
        return os.path.normpath(unquote(h).lstrip("/")).replace("\\", "/")
    return os.path.normpath(os.path.join(os.path.dirname(page_rel), unquote(h))).replace("\\", "/")


def relpath(target, page_rel):
    return os.path.relpath(target, os.path.dirname(page_rel) or ".").replace("\\", "/")


def ids_of(html):
    return set(re.findall(r'\bid="([^"]+)"', MASK.sub(" ", html))) | set(re.findall(r'\bname="([^"]+)"', html))


def is_stub(html):
    return 'http-equiv="refresh"' in html[:4000]


def live_articles():
    """every published page in a country folder: the pages a mention may point at"""
    out = []
    for d in sorted(ROOT.iterdir()):
        idx = d / "index.html"
        if d.is_dir() and d.name not in ("Drafts", "archive", ".tmp", "tools") and idx.is_file() and "country-article-card" in _read(idx.relative_to(ROOT).as_posix()):
            out += [p.relative_to(ROOT).as_posix() for p in sorted(d.glob("*.html"))]
    return out


def country_pages(country):
    d = ROOT / country
    if not d.is_dir():
        return []
    return [p.relative_to(ROOT).as_posix() for p in sorted(d.glob("*.html")) if not is_stub(_read(p.relative_to(ROOT).as_posix()))]


def aliases_for(rel):
    try:
        return json.loads(ALIASES.read_text(encoding="utf-8")).get(rel, []) if ALIASES.is_file() else []
    except Exception:
        return []


def article_names(rel, html):
    """the place names an article is about, from its headline and its file name (guides only)"""
    k = kind_of(rel)
    if k in ("country", "itinerary", "top10", "field-notes"):
        return []
    t = title_of(html)
    head = re.split(r"\s*[—–:]\s*|\s+-\s+", t)[0]
    head = re.sub(r"^(the ultimate guide to|the)\s+", "", head, flags=re.I)
    head = re.sub(r"\s+guide$", "", head, flags=re.I)
    head = head.split(",")[0] if not re.search(r"\s(&|and)\s", head) and "," in head and len(head.split(",")) == 2 else head
    parts = [p.strip() for p in re.split(r",\s*|\s+&\s+|\s+and\s+", head) if p.strip()]
    names = []
    for p in parts:
        p = re.sub(r"^the\s+", "", p, flags=re.I)
        if len(p) >= 4 and _fold(p) not in GENERIC_NAMES:
            names.append(p)
    slug = [w for w in Path(rel).stem.split("-") if w not in SLUG_TAILS]
    if slug and len(slug) <= 3 and not any(w.isdigit() for w in slug) and slug[0] in _fold(t).split():
        names.append(" ".join(w.capitalize() for w in slug))
    live = re.sub(r"^Drafts/[^/]+/", "", rel)
    seen, out = set(), []
    for n in names + list(aliases_for(live)):
        f = _fold(n)
        if f and f not in seen:
            seen.add(f)
            out.append(n)
    return out


def name_matches(link_text, name):
    """the link text IS this place: the name, or the name plus place words ("Lake Ohrid", "Pelister National Park"),
    never the name inside a longer proper name ("Bitola City Park", "Old Town Hostel Ohrid", "Lake Ohrid Boat Tour")"""
    lt, nt = _fold(link_text).split(), _fold(name).split()
    if not lt or not nt:
        return False
    if len(nt) == 1:
        # one place word may come with it ("Lake Ohrid", "Ohrid Lake", "Pelister National Park"); two make a narrower
        # thing ("Bitola City Park"), and any other word makes it another place ("Gyumri Train Station")
        if nt[0] not in lt:
            return False
        rest = [w for w in lt if w != nt[0]]
        return not rest or rest == ["national", "park"] or (len(rest) == 1 and rest[0] in PLACE_TAILS)
    extra = [w for w in lt if w not in nt]
    return set(nt) <= set(lt) and len(extra) <= 1 and all(w in PLACE_TAILS for w in extra)


def inside_anchor(s, pos):
    """True when pos sits inside an existing <a ...>...</a> (or inside any tag's own <...>). The mention pass never
    links such text: an existing link, a Maps pin or a booking link above all, is never altered (Kevin, 2026-11-10)."""
    lt, gt = s.rfind("<", 0, pos), s.rfind(">", 0, pos)
    if lt > gt:
        return True                                   # inside a tag's own markup
    last_open = max((m.start() for m in A_OPEN.finditer(s, 0, pos)), default=-1)
    if last_open < 0:
        return False
    return not A_CLOSE.search(s, last_open, pos)


def target_info(rel, html):
    kind = kind_of(rel)
    return {"rel": rel, "kind": kind, "title": title_of(html), "names": article_names(rel, html), "ids": ids_of(html)}


# ----------------------------------------------------------------------------- zones of a page
def zones(h, s0, e0):
    """(skip, ina, block) byte masks over h: 1 where a link may not go (headings, captions, figures, contents, cards...),
    1 inside an <a>, 1 inside a prose block (p, li, td...). h is already MASK-blanked."""
    n = len(h)
    skip, ina, block = bytearray(n), bytearray(n), bytearray(n)
    stack = []                                      # (tag, start, is_skip)
    for m in TAG.finditer(h, s0, e0):
        closing, tag = m.group(1), m.group(2).lower()
        if tag in VOID or m.group(0).endswith("/>"):
            continue
        if closing:
            while stack and stack[-1][0] != tag:
                t, st, sk = stack.pop()             # unclosed tag: ends where its parent ends
                _mark(skip, ina, block, t, st, m.end(), sk)
            if stack:
                t, st, sk = stack.pop()
                _mark(skip, ina, block, t, st, m.end(), sk)
            continue
        cls = re.search(r'\bclass="([^"]*)"', m.group(0))
        sk = tag in SKIP_TAGS or bool(cls and SKIP_CLASS.search(cls.group(1)))
        stack.append((tag, m.start(), sk))
    for t, st, sk in stack:
        _mark(skip, ina, block, t, st, e0, sk)
    return skip, ina, block


def _mark(skip, ina, block, tag, a, b, sk):
    if sk:
        skip[a:b] = b"\x01" * (b - a)
    if tag == "a":
        ina[a:b] = b"\x01" * (b - a)
    if tag in BLOCK_TAGS:
        block[a:b] = b"\x01" * (b - a)


def sections_of(h, s0, e0):
    """[(start, end, label)]: the intro, then one per h2 (every page), per fn-sub-hd (field notes) and per id'd
    div.measure (the itinerary template, whose days have no headings of their own)"""
    bounds = [(s0, "intro")]
    rx = re.compile(r'<h2\b[^>]*>(.*?)</h2>|<div\b[^>]*class="fn-sub-hd"[^>]*>(.*?)</div>|<div\b(?=[^>]*\bid="([^"]+)")(?=[^>]*class="[^"]*\bmeasure\b)[^>]*>', re.S)
    for m in rx.finditer(h, s0, e0):
        bounds.append((m.start(), _text(m.group(1) or m.group(2) or "") or (m.group(3) or "").replace("-", " ")))
    out = []
    for i, (a, label) in enumerate(bounds):
        b = bounds[i + 1][0] if i + 1 < len(bounds) else e0
        if b > a:
            out.append((a, b, label))
    return out


BLOCK_OPEN = re.compile(r"<(li|p|td|th|dd|dt|blockquote|figcaption|h[1-6])\b[^>]*>")
TEXT_RUN = re.compile(r">([^<]+)<")


def block_span(h, pos, lo):
    """the bullet or paragraph around pos"""
    last = None
    for m in BLOCK_OPEN.finditer(h, max(lo, pos - 4000), pos):
        last = m
    if not last:
        return max(lo, pos - 300), pos + 300
    end = h.find("</%s>" % last.group(1), pos)
    return last.start(), (end + len(last.group(1)) + 3 if end > 0 else pos + 300)


def _context(h, a, b, lo):
    bs, be = block_span(h, a, lo)
    t = _text(h[bs:a]) + " [[" + _text(h[a:b]) + "]] " + _text(h[b:be])
    i = t.find("[[")
    return (("…" if i > 160 else "") + t[max(0, i - 160):i + 240] + ("…" if len(t) > i + 240 else "")).strip()


def _links_in(h, a, b, page):
    """repo paths this stretch of html links to"""
    return {resolve_rel(m.group(1), page) for m in HREF.finditer(h, a, b)} - {None}


def _short(t):
    return re.sub(r"^(?:The\s+)?(?:Ultimate\s+Guide\s+to\s+)?", "", t["title"].split("—")[0].split(":")[0].strip(), flags=re.I) or Path(t["rel"]).stem


# ----------------------------------------------------------------------------- mention: first bare name per section
def mention_patterns(src, targets, country_label):
    """[(target rel, compiled regex, proper)] in longest-name-first order"""
    pats = []
    for t in targets.values():
        if t["rel"] == src or t["kind"] == "country":
            continue
        k = t["kind"]
        if k == "guide":
            # "my Krushevo guide" is the natural place for the link, so that phrase is tried before a bare "Krushevo"
            names = sorted(set(t["names"]), key=len, reverse=True)
            pats.append((t["rel"], re.compile(r"(?<![\w'’-])(%s)(?![\w-])" % "|".join(re.escape(n + " guide") for n in names)), True))
            pats.append((t["rel"], re.compile(r"(?<![\w'’-])(%s)(?![\w-])" % "|".join(re.escape(n) for n in names)), True))
        elif k == "itinerary":
            pats.append((t["rel"], re.compile(r"(?<![\w'’-])(%s itinerary|(?:(?<=\bmy )|(?<=\bthe )|(?<=\bthis )|(?<=\bMy )|(?<=\bThe )|(?<=\bThis ))itinerary)(?![\w-])" % re.escape(country_label), re.I), False))
        elif k == "top10":
            pats.append((t["rel"], re.compile(r"(?<![\w'’-])(top 10(?: things to do(?: in %s)?| list)?)(?![\w-])" % re.escape(country_label), re.I), False))
        elif k == "field-notes":
            pats.append((t["rel"], re.compile(r"(?<![\w'’-])((?:%s )?field notes)(?![\w-])" % re.escape(country_label), re.I), False))
    return pats


def mention_problem(h, a, b, text, proper, lo):
    """why this mention should not carry a link, or None. The context is the mention's own bullet or paragraph: a
    heading just above ("1. Krushevo") must not read as the word before the name."""
    bs, be = block_span(h, a, lo)
    before = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h[max(bs, a - 160):a]))
    after = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h[b:min(be, b + 120)]))
    before, after = H.unescape(before), H.unescape(after)
    if not proper:
        return None
    if dc._part_of_name(before, after) or re.match(r"\s+\d", after):
        return "longer-name"
    if dc.ATTRIBUTIVE_AFTER.match(after) or ATTRIBUTIVE_EXTRA.match(after):
        return "attributive"
    clause = re.split(r"[.!?;:—]\s", before)[-1]
    if dc.ASIDE_BEFORE.search(clause):
        return "aside"
    if dc._is_demonym(text) if hasattr(dc, "_is_demonym") else False:
        return "demonym"
    return None


def scan_mentions(sources, html_of, targets, country_label):
    items = []
    for src in sources:
        if kind_of(src) == "country":
            continue                                   # a card grid by design
        raw = html_of(src)
        h = MASK.sub(lambda mm: " " * len(mm.group(0)), raw)
        s0, e0 = body_span(h)
        skip, ina, block = zones(h, s0, e0)
        pats = mention_patterns(src, targets, country_label)
        for a, b, label in sections_of(h, s0, e0):
            linked = _links_in(h, a, b, src)
            for trel, rx, proper in pats:
                if trel in linked:
                    continue
                for tm in TEXT_RUN.finditer(h, a, b):   # text between tags
                    found = None
                    for mm in rx.finditer(tm.group(1)):
                        s, e = tm.start(1) + mm.start(1), tm.start(1) + mm.end(1)
                        if skip[s] or ina[s] or not block[s]:
                            continue
                        if inside_anchor(h, s) or inside_anchor(h, e - 1):
                            continue                   # hard guard: text inside an existing link is never linked again
                        if proper and not mm.group(1)[:1].isupper():
                            continue
                        why = mention_problem(h, s, e, mm.group(1), proper, a)
                        if why:
                            continue
                        found = (s, e, mm.group(1))
                        break
                    if found:
                        s, e, text = found
                        items.append({"kind": "mention", "page": src, "text": text, "at": s, "end": e, "target": trel,
                                      "href": relpath(trel, src), "verdict": "apply", "reason": "first-in-section",
                                      "why": "first mention in the section “%s”" % label,
                                      "detail": "“%s” is not linked to your %s article" % (text, _short(targets[trel])),
                                      "context": _context(h, s, e, a)})
                        linked.add(trel)
                        break
    return items


# ----------------------------------------------------------------------------- repoint: other pages -> old field-notes anchors
def scan_repoints(country, targets):
    """live pages elsewhere that link to <country>/field-notes.html#<town> where the town now has a guide: review only"""
    slugs = {Path(t["rel"]).stem: t for t in targets.values() if t["kind"] == "guide"}
    for t in list(slugs.values()):
        for n in t["names"]:
            slugs.setdefault(_fold(n).replace(" ", "-"), t)
    items = []
    pages = [p.relative_to(ROOT).as_posix() for p in ROOT.glob("*.html")] + live_articles()
    for rel in pages:
        if rel.startswith(country + "/") or rel in ("editor.html",):
            continue
        h = _read(rel)
        if is_stub(h):
            continue
        for m in re.finditer(r'<a\b[^>]*href="((?:\.\./)*%s/field-notes\.html#([\w-]+))"[^>]*>(.*?)</a>' % re.escape(country), h, re.S):
            base = re.sub(r"-\d+$", "", m.group(2))
            if base not in slugs:
                continue
            t = slugs[base]
            items.append({"kind": "repoint", "page": rel, "text": _text(m.group(3)), "at": m.start(), "end": m.end(),
                          "target": t["rel"], "href": relpath(t["rel"], rel), "old": m.group(1), "verdict": "review",
                          "reason": "repoint", "why": "links to the field notes' %s section; %s is live now. Point it at the guide where the sentence is about visiting, keep the notes where it is a quick reference." % (m.group(2), Path(t["rel"]).name),
                          "detail": "“%s” -> %s" % (_text(m.group(3)), m.group(1)), "context": _context(h, m.start(), m.end(), 0)})
    return items


# ----------------------------------------------------------------------------- apply + verify
def _anchor_counts(s):
    c = {}
    for m in ANCHOR.finditer(s):
        c[m.group(0)] = c.get(m.group(0), 0) + 1
    return c


def apply_items(items, root=None):
    """write the 'apply' mention items: each wraps bare text in a new <a>. Nothing else is ever written: an item of any
    other kind is refused, a span that sits inside an existing <a> (or touches markup) is refused, and a page is not
    saved at all unless every <a>...</a> it had before is still there byte for byte (Kevin, 2026-11-10: map pins and
    booking links stay). Offsets are checked against the file text first; every written href must resolve.
    Returns (done, missed)."""
    root = Path(root) if root else ROOT                # resolved at call time: tests point ROOT at a scratch tree
    by_page, done, missed = {}, [], []
    for it in items:
        if it.get("verdict") != "apply":
            continue
        if it.get("kind") != "mention":
            missed.append(dict(it, miss="only bare-text mentions are written; existing links are never rewritten"))
            continue
        by_page.setdefault(it["page"], []).append(it)
    for page, its in by_page.items():
        p = root / page
        raw = io.open(p, encoding="utf-8", newline="").read()
        s, page_done = raw, []
        for it in sorted(its, key=lambda x: -int(x["at"])):
            a, b = int(it["at"]), int(it["end"])
            href = it["href"]
            if not SAFE_HREF.match(href) or not _resolves(href, page, root):
                missed.append(dict(it, miss="href does not resolve"))
                continue
            if s[a:b] != it["text"]:
                missed.append(dict(it, miss="text moved"))
                continue
            if "<" in it["text"] or ">" in it["text"] or inside_anchor(raw, a) or inside_anchor(raw, b - 1):
                missed.append(dict(it, miss="inside an existing link or tag: never touched"))
                continue
            s = s[:a] + '<a href="%s">%s</a>' % (href, it["text"]) + s[b:]
            page_done.append(it)
        before, after = _anchor_counts(raw), _anchor_counts(s)
        lost = [k for k, n in before.items() if after.get(k, 0) < n]
        if lost:                                       # belt and braces: an existing link changed, so write nothing
            missed += [dict(it, miss="an existing link would change: page not written") for it in page_done]
            continue
        done += page_done
        if s != raw:
            io.open(p, "w", encoding="utf-8", newline="").write(s)
    return done, missed


def _resolves(href, page, root=None):
    root = Path(root) if root else ROOT
    path, _, anchor = H.unescape(href).partition("#")
    if not path:
        return bool(anchor)
    if "Drafts/" in href or "127.0.0.1" in href or "localhost" in href:
        return False
    t = resolve_rel(path, page)
    if not t or not (root / t).is_file():
        return False
    return not anchor or anchor in ids_of(io.open(root / t, encoding="utf-8", errors="replace").read())


def verify(rels, root=None):
    """every internal link and #anchor in these pages' bodies resolves; nothing points into Drafts/ or at a local server.
    The nav, hero and footer are site-wide (tools/publish_country.py builds them) and are the site lint's business."""
    root = Path(root) if root else ROOT
    problems = []
    for rel in rels:
        h = io.open(root / rel, encoding="utf-8", errors="replace").read()
        ids = ids_of(h)
        s0, e0 = body_span(h)
        for m in HREF.finditer(MASK.sub(lambda mm: " " * len(mm.group(0)), h), s0, e0):
            href = H.unescape(m.group(1))
            if re.search(r"Drafts/|127\.0\.0\.1|localhost", href):
                problems.append((rel, href, "points at a draft or a local server"))
                continue
            if href.startswith("#"):
                if len(href) > 1 and href[1:] not in ids:
                    problems.append((rel, href, "anchor with no target"))
                continue
            path, _, anchor = href.partition("#")
            t = resolve_rel(path, rel)
            if not t or not t.endswith(".html"):
                continue
            if t == "destinations.html" and anchor:
                continue                               # #europe, #asia...: continent filters the page reads from the hash, not ids
            if not (root / t).is_file():
                problems.append((rel, href, "file missing"))
            elif anchor and anchor not in ids_of(io.open(root / t, encoding="utf-8", errors="replace").read()):
                problems.append((rel, href, "anchor missing in target"))
    return problems


# ----------------------------------------------------------------------------- the run
def build_targets(country_rels, html_of, extra_live=True):
    targets = {rel: target_info(rel, html_of(rel)) for rel in country_rels}
    if extra_live:
        for rel in live_articles():
            if rel not in targets and kind_of(rel) == "guide":
                targets[rel] = target_info(rel, _read(rel))
    return targets


def country_label(country, targets):
    idx = next((t for t in targets.values() if t["kind"] == "country"), None)
    return (idx and idx["title"]) or country.replace("-", " ").title()


def run(country, apply=False, pages=None, mentions=True, repoints=True, log=None, root=None):
    """cross-link one country: mention links on bare text only (there is no relink pass, and no switch to turn one on:
    removed 2026-11-10, Kevin wants map pins and booking links kept). Returns {"applied", "review", "rejected",
    "problems", "review_file"}; log(line) gets a plain-text account (the launch tools append it to their log)."""
    global ROOT
    if root is not None:
        ROOT = Path(root)
    out = []
    say = log or out.append
    rels = country_pages(country)
    if not rels:
        say("crosslink: no live pages under %s/" % country)
        return {"applied": [], "review": [], "rejected": [], "problems": [], "review_file": None, "missed": [], "log": out}
    cache = {}

    def html_of(rel):
        if rel not in cache:
            cache[rel] = blank_hidden(_read(rel))
        return cache[rel]
    targets = build_targets(rels, html_of)
    label = country_label(country, targets)
    sources = [r for r in rels if not pages or r in pages]
    items = []
    if mentions:
        items += scan_mentions(sources, html_of, {r: targets[r] for r in rels}, label)
    rev = [i for i in items if i["verdict"] == "review"]
    if repoints:
        rev += scan_repoints(country, targets)
    rej = [i for i in items if i["verdict"] == "reject"]
    todo = [i for i in items if i["verdict"] == "apply"]
    done, missed = ([], [])
    if apply:
        done, missed = apply_items(todo)
        for it in missed:
            say("crosslink MISSED %s “%s” -> %s: %s" % (it["page"], it["text"], it["href"], it.get("miss")))
    problems = verify(sorted({i["page"] for i in (done if apply else todo)})) if (done or (not apply and todo)) else []
    review_file = write_review(country, rev) if rev else None
    say("crosslink %s: %s%d sibling mentions linked (existing links never touched); %d kept as they are (%s); %d for review%s" % (
        country, "" if apply else "would: ", len(todo), len(rej), _reasons(rej), len(rev),
        (" -> " + str(review_file.relative_to(ROOT).as_posix())) if review_file else ""))
    for p in problems:
        say("crosslink LINK PROBLEM %s: %s (%s)" % p)
    return {"applied": done if apply else todo, "review": rev, "rejected": rej, "problems": problems,
            "review_file": review_file, "missed": missed, "log": out}


def _reasons(items):
    c = {}
    for i in items:
        c[i["reason"]] = c.get(i["reason"], 0) + 1
    return ", ".join("%d %s" % (n, r) for r, n in sorted(c.items(), key=lambda x: -x[1])) or "none"


def write_review(country, items):
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    jp = REVIEW_DIR / ("%s-review.json" % country)
    mp = REVIEW_DIR / ("%s-review.md" % country)
    io.open(jp, "w", encoding="utf-8").write(json.dumps(items, ensure_ascii=False, indent=1))
    lines = ["# Cross-link review: %s" % country, "", "Links the tool would not decide. Change by hand or leave them as they are.", ""]
    for it in items:
        lines.append("- **%s** “%s” -> `%s` (%s): %s" % (it["page"], it["text"], it["href"], it["reason"], it["why"]))
        lines.append("  > %s" % it["context"])
    io.open(mp, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return mp


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("country", help="folder name, e.g. north-macedonia")
    ap.add_argument("--apply", action="store_true", help="write the mention links (default: report only)")
    ap.add_argument("--pages", nargs="*", default=None, help="only these source pages (repo paths)")
    ap.add_argument("--no-mentions", action="store_true")
    ap.add_argument("--no-repoints", action="store_true")
    ap.add_argument("--json", help="write every item (applied, review, rejected) to this file")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    res = run(a.country, apply=a.apply, pages=[p.replace("\\", "/") for p in a.pages] if a.pages else None,
              mentions=not a.no_mentions, repoints=not a.no_repoints, log=print)
    for it in res["applied"]:
        print("  %s %s: “%s” -> %s   (%s)" % ("APPLIED" if a.apply else "WOULD", it["page"], it["text"], it["href"], it["reason"]))
    for it in res["rejected"]:
        print("  kept    %s: “%s” (%s: %s)" % (it["page"], it["text"], it["reason"], it["why"]))
    if a.json:
        io.open(a.json, "w", encoding="utf-8").write(json.dumps({k: v for k, v in res.items() if k not in ("review_file", "log")},
                                                              ensure_ascii=False, indent=1, default=str))
    return 1 if res["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
