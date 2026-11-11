"""Cross-page consistency check for a country folder: do the cards, Explore More blocks and hero buttons that point at
a sibling article agree with that article, and with each other?

Built 2026-11-09 after three slips on the North Macedonia drafts were found by hand: cards and Explore More blocks on
five pages said "Perfect 7 Day Itinerary" while the itinerary itself said 8 Day; a country-page card reused the photo
of the card beside it; and a card in the draft folder linked to the old live page instead of its sibling draft.

    python tools/country_check.py                      # every live country folder and every draft folder
    python tools/country_check.py greece "Drafts/Upcoming/kosovo"
    python tools/country_check.py --json               # machine-readable
    python tools/country_check.py --md .tmp/country_check_report.md
    python tools/country_check.py <folder> --fix       # the one safe rewrite (below), nothing else

Reads HTML only (no browser), so a hero, lead paragraph or anything drawn by script is out of scope.

What it reads on every page of a folder:
  card      <div class="country-article-card" onclick="location.href='x.html'"> or <a href="x.html" class="country-article-card ..."> ... country-article-card-title
  xm        <a class="xm-c" href="x.html"> ... xm-t (title), xm-d (description)       (Explore More, tools/explore_more.py)
  gb        <a class="gb-card" href="x.html"> ... <span class="h">                   (guide cards, tools/guide_cards.py)
  button    <button class="country-place-btn" onclick="location.href='x.html'">      (hero "Places to visit")

Checks (kind, severity):
  number        high    a title, button or description gives a day count or an "N things"/"Top N" number that the
                        target's own <h1>/<title>/og:title (or, for a description, its description) gives differently
  title         medium  a card / Explore More / guide-card title is none of the target's <h1>, <title> or og:title
                        (compared loosely: case, curly vs straight quotes, "&" vs "and", ":" vs "—" don't count).
                        A field-notes target is exempt: its cards carry a template title by design. So is a link
                        with a #section: that card names the section, not the article.
  description   low     an Explore More description is not the target's description (it is copied from it)
  dup-photo     high    two cards (or two Explore More cards) on one page use the same photo
  live-link     high    in a draft folder, a card / button / Explore More card points at the live page although the
                        folder holds a sibling draft of that page
  missing       high    a card / button / Explore More card points at a page that does not exist
  order         medium  on a country index, the hero buttons list the same articles as the cards in another order
  reused-line   low     a run of 8 or more identical words in the body prose of 2 or more pages of the folder (see below)

Reused lines (added 2026-11-10, after Kevin found "you'll have most of the sights to yourself" on sibling North Macedonia
pages). His rule: "Overlap with his other articles is fine; just don't reuse the sentences." So the check compares words,
not facts. It reads paragraphs and bullets through tools/article_blocks.py (no nav, footers, contents boxes, maps, captions or
headings), and compares words with case, accents and punctuation folded. Each run is reported once, with every page and
the sentence on it, most repeated and longest first. It never compares across folders. Left out:
  - a clause that only points at a sibling page ("The whole route is in my Peru itinerary", "see my Sarajevo guide"), or the map
  - a run that only repeats a page's <h1> or <title>
  - mostly figures: fares, times and distances (a fifth or more of the words are numbers)
  - addresses, runs of proper names (Calle de la Cochera del Hobo and...), and quoted names
  - a field-notes page repeating its guide's FACT: a run shared only by field notes and one other page is kept only if it is 20+
    words (a copied sentence) or has his voice (I, my, you'll, loved, worth...) and no figures
Report only: a reworded line is Kevin's call, so --fix never touches these.

--fix changes ONE thing only: a card / Explore More / guide-card TITLE that differs from its target's own title is
replaced with that title, an exact mirror of the target's <h1> text. It does so only when the target's <h1> and <title>
agree (otherwise "its own title" is ambiguous and the finding stays a report), never on a field-notes target, and never
in a file Kevin has in review (a comment by him or any round: draft_check.review_state). Everything else in the file is
byte-preserved (read and written with newline="").
"""
import argparse
import html as htmlmod
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import article_blocks

ROOT = Path(__file__).resolve().parent.parent
NOT_COUNTRY = {"Drafts", "Images", "_content", "archive", "assets", "fonts", "tools", "workflows", ".tmp", ".git",
               ".github", ".vscode", ".claude", "node_modules"}
DRAFT_WORK = ("Upcoming", "Full Articles", "Field Notes")
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def review_guard(path):
    """why --fix must leave this file alone (Kevin has it in review), or []"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import draft_check
        return draft_check.review_state(Path(path))[0]
    except Exception as e:                              # can't tell: refuse rather than guess
        return ["review state unknown (%s)" % e]


# ---- reading ---------------------------------------------------------------------------------------------------------

def read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


def text(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", htmlmod.unescape(s)).strip()


def norm(s):
    """loose comparison form: case, quote style, '&'/'and', separators and the site suffix don't count"""
    t = text(s).lower()
    t = t.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "}))
    t = re.sub(r"\s*[|\-–—]\s*getawayguide\s*$", "", t)
    t = re.sub(r"\s*&\s*", " and ", t)
    t = re.sub(r"\s*(?::|—|–|\||\s-\s)\s*", " : ", t)
    t = re.sub(r"[.!]+$", "", t)
    return re.sub(r"\s+", " ", t).strip()


WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20}
UNIT = {"day": "day", "days": "day", "night": "night", "nights": "night", "hour": "hour", "hours": "hour",
        "week": "week", "weeks": "week", "thing": "count", "things": "count", "best": "count", "place": "count",
        "places": "count", "reason": "count", "reasons": "count", "waterfall": "waterfall", "waterfalls": "waterfall"}
_NUM = r"(\d+|" + "|".join(WORDNUM) + r")"
NUM_RE = re.compile(r"\b" + _NUM + r"[\s-]+(" + "|".join(UNIT) + r")\b", re.I)
TOP_RE = re.compile(r"\btop\s+(\d+)\b", re.I)
RANGE_RE = re.compile(r"\b(\d+)\s*(?:to|-|–)\s*(\d+)[\s-]+(days?|nights?|hours?|weeks?)\b", re.I)


def numbers(s):
    """{unit: {values}} for 'N day', 'N things', 'Top N' etc.; a range like '1 to 2 days' gives both ends"""
    t = text(s)
    out = {}
    for m in NUM_RE.finditer(t):
        v = m.group(1).lower()
        out.setdefault(UNIT[m.group(2).lower()], set()).add(int(v) if v.isdigit() else WORDNUM[v])
    for m in TOP_RE.finditer(t):
        out.setdefault("count", set()).add(int(m.group(1)))
    for m in RANGE_RE.finditer(t):
        out.setdefault(UNIT[m.group(3).lower()], set()).update({int(m.group(1)), int(m.group(2))})
    return out


def number_clash(mine, theirs):
    """[(unit, my values, their values)] where both name the unit and share no value"""
    return [(u, sorted(v), sorted(theirs[u])) for u, v in mine.items() if u in theirs and not (v & theirs[u])]


_OWN_CACHE = {}


def own(p):
    """the target page's own title fields and description"""
    p = Path(p)
    key = (str(p), p.stat().st_mtime_ns)
    if key in _OWN_CACHE:
        return _OWN_CACHE[key]
    s = read(p)
    t = re.search(r"<title>(.*?)</title>", s, re.S)
    h = re.search(r"<h1\b[^>]*>(.*?)</h1>", s, re.S)
    og = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', s)
    d = re.search(r'<meta\s+property="og:description"\s+content="([^"]*)"', s)
    d2 = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', s)
    out = {"title": t.group(1) if t else "", "h1": h.group(1) if h else "", "og": og.group(1) if og else "",
           "desc": d.group(1) if d else "", "desc2": d2.group(1) if d2 else ""}
    _OWN_CACHE[key] = out
    return out


# ---- the elements on a page ------------------------------------------------------------------------------------------

CARD_RE = re.compile(r'<(?:div|a)\b[^>]*?\bclass="country-article-card(?:\s[^"]*)?"[^>]*>')
CARD_HREF_RE = re.compile(r"onclick=\"location\.href='([^']*)'\"|\bhref=\"([^\"]*)\"")
CARD_TITLE_RE = re.compile(r'<h3 class="country-article-card-title">(.*?)</h3>', re.S)
XM_RE = re.compile(r'<a class="xm-c" href="([^"]*)"[^>]*>(.*?)</a>', re.S)
GB_RE = re.compile(r'<a class="gb-card" href="([^"]*)"[^>]*>(.*?)</a>', re.S)
BTN_RE = re.compile(r"""<button class="country-place-btn"[^>]*?onclick="location\.href='([^']*)'"[^>]*>(.*?)</button>""", re.S)
URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""")
IMG_RE = re.compile(r'<img\b[^>]*?\ssrc="([^"]+)"')


def photo_key(u):
    """one photo whatever its tier or format: card-skopje.jpg / card-skopje.webp / card-skopje-1499w.jpg / -mob-2x"""
    b = htmlmod.unescape(u).replace("%20", " ").split("?")[0].rsplit("/", 1)[-1]
    b = re.sub(r"\.(jpe?g|webp|png|avif)$", "", b, flags=re.I)
    b = re.sub(r"(-mob)?(-\d+x)?$", "", b)
    b = re.sub(r"-\d{3,4}w$", "", b)
    return b.lower()


def elements(s):
    """every card / xm / gb / button on the page: dicts with kind, href, title (+ raw span), desc, photos"""
    out = []
    starts = [m.start() for m in CARD_RE.finditer(s)]
    for i, m in enumerate(CARD_RE.finditer(s)):
        end = starts[i + 1] if i + 1 < len(starts) else len(s)
        body = s[m.start():end]
        t = CARD_TITLE_RE.search(body)
        tag = m.group(0)
        h = CARD_HREF_RE.search(tag)
        cls = re.search(r'class="([^"]*)"', tag).group(1).split()
        out.append({"kind": "card", "href": htmlmod.unescape((h.group(1) or h.group(2)) if h else ""), "at": m.start(),
                    "title": t.group(1) if t else None,
                    "span": (m.start() + t.start(1), m.start() + t.end(1)) if t else None,
                    "photos": {photo_key(u) for u in URL_RE.findall(tag)} | {c for c in cls if c.startswith("img-")}})
    for m in XM_RE.finditer(s):
        inner = m.group(2)
        t = re.search(r'<span class="xm-t">(.*?)</span>', inner, re.S)
        d = re.search(r'<span class="xm-d">(.*?)</span>', inner, re.S)
        img = IMG_RE.search(inner)
        out.append({"kind": "xm", "href": htmlmod.unescape(m.group(1)), "at": m.start(),
                    "title": t.group(1) if t else None,
                    "span": (m.start(2) + t.start(1), m.start(2) + t.end(1)) if t else None,
                    "desc": d.group(1) if d else None, "photos": {photo_key(img.group(1))} if img else set()})
    for m in GB_RE.finditer(s):
        inner = m.group(2)
        t = re.search(r'<span class="h">(.*?)</span>', inner, re.S)
        img = IMG_RE.search(inner)
        out.append({"kind": "gb", "href": htmlmod.unescape(m.group(1)), "at": m.start(),
                    "title": t.group(1) if t else None,
                    "span": (m.start(2) + t.start(1), m.start(2) + t.end(1)) if t else None,
                    "photos": {photo_key(img.group(1))} if img else set()})
    for m in BTN_RE.finditer(s):
        out.append({"kind": "button", "href": htmlmod.unescape(m.group(1)), "at": m.start(), "title": m.group(2),
                    "span": None, "photos": set()})
    return sorted(out, key=lambda e: e["at"])


# ---- folders ---------------------------------------------------------------------------------------------------------

def is_draft_folder(folder, root=None):
    root = Path(root or ROOT)
    try:
        parts = Path(folder).resolve().relative_to(root.resolve()).parts
    except ValueError:
        return False
    return len(parts) >= 3 and parts[0] == "Drafts" and parts[1] in DRAFT_WORK


def all_folders(root=None):
    root = Path(root or ROOT)
    live = sorted(d for d in root.iterdir() if d.is_dir() and d.name not in NOT_COUNTRY and not d.name.startswith(".")
                  and ((d / "index.html").is_file() or (d / "field-notes.html").is_file()))
    drafts = sorted(d for w in DRAFT_WORK if (root / "Drafts" / w).is_dir()
                    for d in (root / "Drafts" / w).iterdir() if d.is_dir() and any(d.glob("*.html")))
    return live + drafts


def resolve_folders(args, root=None):
    root = Path(root or ROOT)
    if not args:
        return all_folders(root)
    out = []
    for a in args:
        p = Path(a) if Path(a).is_absolute() else root / a
        if p.is_dir():
            out.append(p)
        else:                                            # a bare country name: its live folder and its draft folders
            out += [d for d in all_folders(root) if d.name == a]
    return out


def sibling_draft(folder, target, root):
    """the draft of this live page, for the same country: in the page's own folder first, then the other draft folders"""
    if Path(target).parent.name != Path(folder).name:
        return None
    root = Path(root)
    for d in [Path(folder)] + [root / "Drafts" / w / Path(folder).name for w in DRAFT_WORK]:
        if (d / Path(target).name).is_file():
            return d / Path(target).name
    return None


def lineno(s, at):
    return s.count("\n", 0, at) + 1


def resolve(page, href, root):
    """(target path or None, why not) for an internal .html link"""
    if not href or href.startswith(("http:", "https:", "mailto:", "#", "javascript:")):
        return None, "external"
    h = href.split("#")[0].split("?")[0]
    if not h:
        return None, "anchor"
    if h.endswith("/"):
        h += "index.html"
    if not h.endswith(".html"):
        return None, "not a page"
    t = (page.parent / h.replace("%20", " ")).resolve()
    try:
        t.relative_to(Path(root).resolve())
    except ValueError:
        return None, "outside the site"
    return t, ""


def rel(p, root):
    try:
        return Path(p).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(p)


# ---- the check -------------------------------------------------------------------------------------------------------

def check_page(page, folder, root, draft):
    s = read(page)
    found = []
    els = elements(s)

    def add(kind, sev, e, msg, **kw):
        d = {"kind": kind, "severity": sev, "page": rel(page, root), "line": lineno(s, e["at"]) if e else 1,
             "element": e["kind"] if e else "", "href": e["href"] if e else "", "message": msg}
        d.update(kw)
        found.append(d)

    for e in els:
        tgt, why = resolve(page, e["href"], root)
        if tgt is None:
            continue
        sibling = sibling_draft(folder, tgt, root) if draft and not is_draft_folder(tgt.parent, root) else None
        if not tgt.is_file():
            add("missing", "high", e, "%s points at %s, which does not exist%s" % (
                e["kind"], e["href"], "; a draft of it is at %s" % rel(sibling, root) if sibling else ""))
            continue
        # a draft that points at the live page while a draft of that page exists for the same country
        if sibling:
            add("live-link", "high", e, "%s points at the live page %s; the sibling draft %s exists"
                % (e["kind"], e["href"], rel(sibling, root)), target=rel(tgt, root), expected=rel(sibling, root))
        o = own(tgt)
        if e["title"] is None:
            continue
        mine = e["title"]
        fn_target = tgt.name == "field-notes.html"
        if "#" in e["href"]:                             # a card for a section ("A Day Trip to Prilep"), not the article
            continue
        theirs_title = " ".join((o["h1"], o["title"], o["og"]))
        clash = number_clash(numbers(mine), numbers(theirs_title))
        h1_agrees = bool(o["h1"]) and norm(o["h1"]) == norm(o["title"])
        fixable = e["kind"] in ("card", "xm", "gb") and e["span"] is not None and h1_agrees and not fn_target
        if clash:
            add("number", "high", e, "%s says %s; %s's own title says %s" % (
                e["kind"], ", ".join("%s %s" % (u, "/".join(map(str, a))) for u, a, _ in clash), tgt.name,
                ", ".join("%s %s" % (u, "/".join(map(str, b))) for u, _, b in clash)),
                target=rel(tgt, root), found=text(mine), expected=text(o["h1"] or o["title"]), fixable=fixable)
        elif e["kind"] != "button" and not fn_target and norm(mine) not in {norm(o["h1"]), norm(o["title"]), norm(o["og"])} - {""}:
            add("title", "medium", e, "%s title is not %s's own title" % (e["kind"], tgt.name),
                target=rel(tgt, root), found=text(mine), expected=text(o["h1"] or o["title"]), fixable=fixable,
                why_not_fixable="" if fixable else ("field-notes template" if fn_target else
                                                    "its <h1> and <title> disagree" if not h1_agrees else "not a title"))
        if e["kind"] == "xm" and e.get("desc") is not None:
            want = [x for x in (o["desc"], o["desc2"]) if x]
            stripped = [re.sub(r"^Field notes from (the )?[^:]{2,40}:\s*", "", htmlmod.unescape(x)) for x in want]
            dn = numbers(e["desc"])
            theirs = {}
            for x in want + [o["h1"], o["title"]]:
                for u, v in numbers(x).items():
                    theirs.setdefault(u, set()).update(v)
            dclash = [c for c in number_clash(dn, theirs) if c[0] in ("day", "night", "week", "count")]
            if dclash:
                add("number", "high", e, "Explore More description says %s; %s says %s" % (
                    ", ".join("%s %s" % (u, "/".join(map(str, a))) for u, a, _ in dclash), tgt.name,
                    ", ".join("%s %s" % (u, "/".join(map(str, b))) for u, _, b in dclash)),
                    target=rel(tgt, root), found=text(e["desc"]), expected=text(want[0]) if want else "", fixable=False)
            elif want and norm(e["desc"]) not in {norm(x) for x in want + stripped}:
                add("description", "low", e, "Explore More description is not %s's description" % tgt.name,
                    target=rel(tgt, root), found=text(e["desc"]), expected=text(stripped[0]), fixable=False)

    # two cards on one page with the same photo (cards with cards, Explore More with Explore More)
    for kind in ("card", "xm", "gb"):
        seen = {}
        for e in [x for x in els if x["kind"] == kind]:
            for ph in e["photos"]:
                if ph in seen and seen[ph]["href"] != e["href"]:
                    add("dup-photo", "high", e, "%s for %s uses the same photo (%s) as the %s for %s" % (
                        kind, e["href"], ph, kind, seen[ph]["href"]), photo=ph)
                seen.setdefault(ph, e)

    # hero buttons vs cards on a country index
    if page.name == "index.html":
        btn = [x["href"].split("#")[0] for x in els if x["kind"] == "button" and x["href"].split("#")[0].endswith(".html")]
        crd = [x["href"].split("#")[0] for x in els if x["kind"] == "card"]
        common_b = [h for h in dict.fromkeys(btn) if h in crd]
        common_c = [h for h in dict.fromkeys(crd) if h in btn]
        if common_b != common_c:
            first = next(x for x in els if x["kind"] == "button")
            add("order", "medium", first, "hero buttons and cards list the same articles in a different order",
                found=" > ".join(common_b), expected=" > ".join(common_c))
    return found


def check_folder(folder, root=None):
    root = Path(root or ROOT)
    folder = Path(folder)
    draft = is_draft_folder(folder, root)
    out = []
    for p in sorted(folder.glob("*.html")):
        out += check_page(p, folder, root, draft)
    out += check_reused(folder, root)
    for f in out:
        f["country"] = folder.name
        f["folder"] = rel(folder, root)
    return out


def check(folders=None, root=None):
    root = Path(root or ROOT)
    out = []
    for f in (folders if folders is not None else all_folders(root)):
        out += check_folder(f, root)
    return out


# ---- reused lines: the same run of words on two or more pages of one folder -----------------------------------------

RUN_MIN_WORDS = 8                                        # about the length of "you'll have most of the sights to yourself"
RUN_PROSE_TAGS = {"p", "li", "blockquote"}               # body prose: not headings, table rows or anything blocks_of skips
_WORD = re.compile(r"\d+(?:[.,:]\d+)*[^\W_]*|[^\W_]+(?:['’][^\W_]+)*|&")       # "1,200" and "7:30am" are one word each
_PART = re.compile(r"[^\W_]+")
_GAP = r"(?:<[^>]+>|&#?\w+;|[\W_])*"                     # what may sit between two words in the page source
_BODY_AT = re.compile(r'class="[^"]*\b(?:artbody|article-body|fn-body)\b')
_SENT_END = re.compile(r"[.!?…]+[\"”’')\]]*\s+(?=[\"“‘'(\[]?[A-Z0-9])")
_ABBREV = re.compile(r"\b(?:St|Mt|Dr|Mr|Mrs|Ms|Ave|Rd|vs|approx|No|e\.g|i\.e)\.$")


def _word_key(w):
    """the comparison form of one word: case, accents and punctuation don't count"""
    if w == "&":
        return "and"
    return "".join(c for c in unicodedata.normalize("NFKD", w) if c.isalnum()).lower()


def prose_blocks(page):
    """[{tag, text, spans: [(start, end)], words: [normalized]}] for each paragraph or bullet in the page's body prose"""
    out = []
    for b in article_blocks.blocks_of(read(page)):
        if b["tag"] not in RUN_PROSE_TAGS:
            continue
        ms = list(_WORD.finditer(b["text"]))
        out.append({"tag": b["tag"], "text": b["text"], "spans": [m.span() for m in ms],
                    "words": [_word_key(m.group()) for m in ms]})
    return out


def sentence_around(text, start, end, limit=320):
    """the whole sentence(s) holding text[start:end], trimmed around the run if it's very long"""
    cuts = [0] + [m.end() for m in _SENT_END.finditer(text) if not _ABBREV.search(text[:m.start() + 1])] + [len(text)]
    a = max(c for c in cuts if c <= start)
    b = min([c for c in cuts if c >= end] or [len(text)])
    s = text[a:b].strip()
    if len(s) <= limit:
        return s
    keep = text[start:end].strip()
    if len(keep) >= limit:
        return keep[:limit - 1].rstrip() + "…"
    room = limit - len(keep)
    lead = text[max(a, start - room // 2):start]
    tail = text[end:min(b, end + room - len(lead))]
    return ("…" if start - len(lead) > a else "") + lead + keep + tail + ("…" if end + len(tail) < b else "")


def find_runs(folder):
    """every group of 8+ word runs shared by 2+ pages of the folder, before anything is excluded.

    Each word position that starts a shared RUN_MIN_WORDS-gram is a node. Identical grams are one group, and so are
    neighboring positions in one paragraph, so a phrase, its longer version on two of the pages and the overlapping
    variants of a copied passage all end up as ONE group. Returns [{"pages": [{...one span per page...}], ...}]."""
    n = RUN_MIN_WORDS
    pages = [(p, prose_blocks(p)) for p in sorted(Path(folder).glob("*.html"))]
    where = {}
    for pi, (p, blocks) in enumerate(pages):
        for bi, b in enumerate(blocks):
            for i in range(len(b["words"]) - n + 1):
                where.setdefault(tuple(b["words"][i:i + n]), []).append((pi, bi, i))
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for locs in where.values():
        if len({l[0] for l in locs}) < 2:
            continue
        for l in locs:
            parent[find(l)] = find(locs[0])
    for pi, bi, i in list(parent):
        nxt = (pi, bi, i + 1)
        if nxt in parent:
            parent[find((pi, bi, i))] = find(nxt)
    groups = {}
    for l in parent:
        groups.setdefault(find(l), []).append(l)
    out = []
    for locs in groups.values():
        by_page = {}
        for pi, bi, i in sorted(locs):
            sp = by_page.setdefault(pi, [])
            if sp and sp[-1][0] == bi and i <= sp[-1][2]:
                sp[-1][2] = i + n
            else:
                sp.append([bi, i, i + n])
        spans = []
        for pi, sp in sorted(by_page.items()):
            bi, a, b = max(sp, key=lambda x: x[2] - x[1])     # the longest match on this page; ties go to the first
            spans.append({"pi": pi, "path": pages[pi][0], "block": pages[pi][1][bi], "a": a, "b": b, "words": b - a})
        out.append(spans)
    return out


def _span_text(sp):
    bl = sp["block"]
    return bl["text"][bl["spans"][sp["a"]][0]:bl["spans"][sp["b"] - 1][1]]


FIELD_NOTES = "field-notes.html"
_STOP = set("""a an the and or but of in on at to for from by with as into near is are was were it its this that these
               de del la las los el le les du des di da do dos das y e al van von der den""".split())
# a clause that exists to send the reader to a sibling page: template wording, not his voice
_POINTER = re.compile(r"\bmy\s+(?:[\w'’&-]+\s+){0,4}?(?:itinerary|field notes|travel tips|before you go)\b|\bshort version\b"
                      r"|\bwhole route is\b|\bfor more on\b|\b(?:on|in) the map below\b|\btop 10 things to do in\b", re.I)
_POINTER_GUIDE = re.compile(r"\b[Mm]y\s+(?:[A-Z][\w'’-]*|&)(?:\s+(?:[A-Z][\w'’-]*|&|and|to|the|of|day|trip|hike|city))*\s+guides?\b")
_CLAUSE = re.compile(r"[.,;:!?()—–]+\s*")
_ADDRESS = re.compile(r"\b\d+\w?\s+(?:[A-Z][\w'’.-]*\s+){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd)\b"
                      r"|\b(?:Calle|Carrera|Avenida|Av|Rua|Rruga|Rr|Ulica|Ul|Via|Strasse)\.?\s+(?:[A-Z][\w'’.-]*\s+){0,3}\d+")
_VERBISH = {"is", "are", "was", "were", "has", "have", "had", "i", "we", "my", "me", "took", "take", "flew", "went", "saw"}
_VOICE = re.compile(r"\b(?:i|im|ive|id|ill|my|mine|me|we|our|ours|us|youll|youd|youre|yourself|favorite|favorites|loved|love|"
                    r"honestly|worth|wouldnt|recommend|recommended|overrated|underrated|surprised|surprise|surprises|"
                    r"surprising|disappointed|hated)\b")
RUN_FIGURES_SHARE = 0.2
RUN_NAME_SHARE = 0.75
RUN_VERBATIM_WORDS = 20                                  # this long, a field-notes bullet and a guide share a sentence, not a fact


def _quoted(sp):
    """is the run inside quotation marks in its paragraph?"""
    bl = sp["block"]
    a, b = bl["spans"][sp["a"]][0], bl["spans"][sp["b"] - 1][1]
    before, inside = bl["text"][:a], bl["text"][a:b]
    if '"' in inside or "”" in inside or "“" in inside:
        return False                                     # the run crosses a quotation mark, so it isn't wholly inside one
    return before.count('"') % 2 == 1 or before.count("“") > before.count("”")


def _cap_share(sp):
    """of the run's content words, how many are capitalized (names, places)"""
    bl = sp["block"]
    words = [bl["text"][x:y] for x, y in bl["spans"][sp["a"]:sp["b"]]]
    content = [w for w in words if _word_key(w) not in _STOP and not w[:1].isdigit()]
    return sum(w[:1].isupper() for w in content) / len(content) if content else 1.0


def _is_pointer(run):
    """every clause of the run is a pointer to a sibling page; a sentence of real prose with a link at its end is not"""
    hit, longest = False, 0
    for c in _CLAUSE.split(run):
        if _POINTER.search(c) or _POINTER_GUIDE.search(c):
            hit = True
        else:
            longest = max(longest, len(_WORD.findall(c)))
    return hit and longest < RUN_MIN_WORDS


def reused_noise(spans, titles=()):
    """why this group is not a reused line Kevin should reword, or ''"""
    best = max(spans, key=lambda sp: sp["words"])
    words = best["block"]["words"][best["a"]:best["b"]]
    phrase = " ".join(words)
    run = _span_text(best)
    digits = sum(w[:1].isdigit() for w in words)
    if _is_pointer(run):
        return "pointer to a sibling page"
    if any(phrase in t for t in titles):
        return "repeats a page title"
    if digits / len(words) >= RUN_FIGURES_SHARE:
        return "figures"
    if _ADDRESS.search(run):
        return "address"
    share = _cap_share(best)
    if share >= RUN_NAME_SHARE and not _VERBISH & set(words):
        return "proper names"
    if share >= 0.4 and any(_quoted(sp) for sp in spans):
        return "quoted name"
    guides = [sp for sp in spans if sp["path"].name != FIELD_NOTES]
    if len(guides) < 2 and best["words"] < RUN_VERBATIM_WORDS and (digits or not _VOICE.search(phrase)):
        return "field notes repeating a guide's fact"
    return ""


def page_titles(folder):
    """each page's <h1> and <title>, normalized the way runs are, to spot a run that only repeats a title"""
    out = set()
    for p in Path(folder).glob("*.html"):
        o = own(p)
        for t in (o["h1"], o["title"], o["og"]):
            w = [_word_key(m.group()) for m in _WORD.finditer(text(t))]
            if len(w) >= RUN_MIN_WORDS:
                out.add(" ".join(w))
    return out


def _source_line(s, sp):
    """the line of the page source where the run starts; 1 if inline markup defeats the search"""
    bl = sp["block"]
    parts = [q for x, y in bl["spans"][sp["a"]:sp["a"] + 6] for q in _PART.findall(bl["text"][x:y])]
    if not parts:
        return 1
    start = _BODY_AT.search(s)
    m = re.compile(_GAP.join(re.escape(q) for q in parts), re.I).search(s, start.start() if start else 0)
    return lineno(s, m.start()) if m else 1


def check_reused(folder, root):
    """one low-severity finding per reused run; report only, nothing here is ever fixable"""
    out = []
    titles = page_titles(folder)
    src = {}
    for spans in find_runs(folder):
        if reused_noise(spans, titles):
            continue
        best = max(spans, key=lambda sp: sp["words"])
        rest = sum(sp["words"] for sp in spans) - best["words"]
        entries = []
        for sp in spans:
            bl = sp["block"]
            a, b = bl["spans"][sp["a"]][0], bl["spans"][sp["b"] - 1][1]
            s = src.setdefault(sp["path"], read(sp["path"]))
            entries.append({"page": rel(sp["path"], root), "line": _source_line(s, sp), "words": sp["words"],
                            "sentence": sentence_around(bl["text"], a, b)})
        run = _span_text(best)
        out.append({"kind": "reused-line", "severity": "low", "page": entries[0]["page"], "line": entries[0]["line"],
                    "element": "", "href": "", "fixable": False,
                    "message": '%d words shared by %d pages: "%s"' % (best["words"], len(spans), run if len(run) <= 200 else run[:199] + "…"),
                    "run": run, "words": best["words"], "pages": entries, "rank": rest * (len(spans) - 1)})
    return out


# ---- the one fix -----------------------------------------------------------------------------------------------------

def fix(findings, root=None, guard=None):
    """mirror the target's own title into each fixable card / Explore More / guide-card title. Returns lines."""
    root = Path(root or ROOT)
    guard = guard or review_guard
    by_page = {}
    for f in findings:
        if f.get("fixable") and f["kind"] in ("title", "number") and f["element"] in ("card", "xm", "gb"):
            by_page.setdefault(f["page"], []).append(f)
    lines = []
    for page_rel, fs in sorted(by_page.items()):
        page = root / page_rel
        why = guard(page)
        if why:
            lines.append("SKIPPED %s: in review (%s)" % (page_rel, "; ".join(why)))
            continue
        s = read(page)
        edits = []
        for e in elements(s):                            # find each element again by what it is, not by line
            if e["kind"] not in ("card", "xm", "gb") or e["span"] is None:
                continue
            tgt, _ = resolve(page, e["href"], root)
            if tgt is None or not tgt.is_file() or tgt.name == "field-notes.html" or "#" in e["href"]:
                continue
            o = own(tgt)
            if not o["h1"] or norm(o["h1"]) != norm(o["title"]):
                continue
            mirror = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", o["h1"])).strip()
            cur = s[e["span"][0]:e["span"][1]]
            if cur == mirror:
                continue
            if any(f["href"] == e["href"] and f["element"] == e["kind"] for f in fs):
                edits.append((e["span"], mirror, e))
        for (a, b), mirror, e in sorted(edits, key=lambda x: -x[0][0]):
            s = s[:a] + mirror + s[b:]
            lines.append("  %s: %s %s title -> %s" % (page_rel, e["kind"], e["href"], text(mirror)))
        if edits:
            write(page, s)
    return lines


# ---- output ----------------------------------------------------------------------------------------------------------

def grouped(findings):
    by = {}
    for f in findings:
        by.setdefault(f["country"], []).append(f)
    return sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def reused_only(findings):
    """the reused-line findings, most repeated and longest first (across every folder in the run)"""
    return sorted((f for f in findings if f["kind"] == "reused-line"),
                  key=lambda f: (-f["rank"], -len(f["pages"]), -f["words"], f["page"]))


def rest_of(findings):
    return [f for f in findings if f["kind"] != "reused-line"]


def plain(findings, folders_n):
    out = ["country_check: %d finding(s) in %d folder(s)" % (len(findings), folders_n)]
    for country, fs in grouped(rest_of(findings)):
        out.append("")
        out.append("%s: %d finding(s)" % (country, len(fs)))
        for f in sorted(fs, key=lambda f: (SEV_ORDER[f["severity"]], f["kind"], f["page"], f["line"])):
            out.append("  [%s %s] %s:%d  %s" % (f["severity"], f["kind"], f["page"], f["line"], f["message"]))
            if f.get("found") or f.get("expected"):
                out.append("      found:    %s" % f.get("found", ""))
                out.append("      expected: %s%s" % (f.get("expected", ""), "   (--fix mirrors this)" if f.get("fixable") else ""))
    reused = reused_only(findings)
    if reused:
        out.append("")
        out.append("Reused lines: %d (low; report only, most repeated and longest first)" % len(reused))
        for f in reused:
            out.append("  [low reused-line] %s  %s" % (f["folder"], f["message"]))
            for p in f["pages"]:
                out.append("      %s:%d (%d words)  %s" % (p["page"], p["line"], p["words"], p["sentence"]))
    return "\n".join(out) + "\n"


def reused_markdown(findings):
    """the ranked reused-line section of the Markdown report"""
    reused = reused_only(findings)
    if not reused:
        return ""
    per = {}
    for f in reused:
        per[f["folder"]] = per.get(f["folder"], 0) + 1
    out = ["## Reused lines (%d, low; report only)" % len(reused), "",
           "Runs of %d or more identical words (case and punctuation ignored) in the body prose of 2 or more pages of one folder, "
           "most repeated and longest first. `--fix` never touches these." % RUN_MIN_WORDS, "",
           "Per folder: %s." % ", ".join("%s %d" % kv for kv in sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))), ""]
    for f in reused:
        out.append("- **%s** `%s`  %s" % (f["country"], f["folder"], f["message"]))
        for p in f["pages"]:
            out.append("  - `%s:%d` (%d words) %s" % (p["page"], p["line"], p["words"], p["sentence"]))
    out.append("")
    return "\n".join(out)


def markdown(findings, folders):
    kinds = {}
    for f in findings:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    out = ["# Country consistency check", "",
           "`python tools/country_check.py` (report only), over %d folders (live country folders and every draft folder)." % len(folders),
           "", "**%d findings**: %s." % (len(findings), ", ".join("%s %d" % kv for kv in sorted(kinds.items(), key=lambda kv: -kv[1]))),
           "", "Severity: high = number, dup-photo, live-link, missing; medium = title, order; low = description, reused-line.",
           "Fixable = `--fix` would mirror the target's own title into that card (nothing else is ever rewritten).", ""]
    for country, fs in grouped(rest_of(findings)):
        sev = {}
        for f in fs:
            sev[f["severity"]] = sev.get(f["severity"], 0) + 1
        out.append("## %s (%d: %s)" % (country, len(fs), ", ".join("%s %d" % (k, sev[k]) for k in ("high", "medium", "low") if k in sev)))
        out.append("")
        for f in sorted(fs, key=lambda f: (SEV_ORDER[f["severity"]], f["kind"], f["page"], f["line"])):
            out.append("- **%s %s** `%s:%d` %s" % (f["severity"], f["kind"], f["page"], f["line"], f["message"]))
            if f.get("found") or f.get("expected"):
                out.append("  - found: %s" % f.get("found", ""))
                out.append("  - expected: %s%s" % (f.get("expected", ""), " *(fixable)*" if f.get("fixable") else ""))
        out.append("")
    extra = reused_markdown(findings)
    if extra:
        out.append(extra)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folders", nargs="*", help="country folders or country names; default: every live and draft folder")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", metavar="OUT.md", help="also write the report as Markdown")
    ap.add_argument("--fix", action="store_true", help="mirror the target's own title into mismatched card titles")
    a = ap.parse_args(argv)
    folders = resolve_folders(a.folders)
    if a.folders and not folders:
        ap.error("no such folder or country: %s" % ", ".join(a.folders))
    findings = check(folders)
    out = sys.stdout.buffer
    if a.fix:
        lines = fix(findings)
        out.write(("country_check --fix: %d title(s) mirrored\n" % sum(l.startswith("  ") for l in lines)
                   + "\n".join(lines) + "\n").encode("utf-8"))
        return 0
    if a.md:
        Path(a.md).write_text(markdown(findings, folders), encoding="utf-8")
    if a.json:
        out.write(json.dumps(findings, ensure_ascii=False, indent=1).encode("utf-8"))
    else:
        out.write(plain(findings, len(folders)).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
