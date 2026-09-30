"""The prelaunch checklist behind the editor's Launch tab (Kevin, 2026-09-30).

Kevin: "a prelaunch checklist tab ... should list out all the checks claude does before launch.
Drop down to choose a country and article. it will show all the claude checks and the checks that
I should do ... There should also be buttons taking me to the right tool to clean these up."

Two lists:
  CLAUDE   deterministic checks that run here, built from workflows/publish_article.md, the
           publish protocol and tools/audit.py, which they import rather than copy (prose_check,
           audit.check_tiers/check_heroes, voice_check, coverage_check, the review store).
  KEVIN    the judgment calls. Each shows what the page says today, so a tick is informed rather
           than from memory, and names the tool that changes it. Ticks live in .tmp/prelaunch/.

The RELINK check is new: a Google Maps link to a place that has its own article (published, or in
the same launch) should link to the article instead. Kevin: "if I'm publishing all armenia
articles, any google maps links to Orgov should be replaced with links to my article instead."

Checks read the files on disk. Nothing here writes an article except an explicit Fix from the tab
(the relink Apply, the photo pipeline, article_dates --fix)."""
import html as H
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
DRAFTS = ROOT / "Drafts" / ".Full Articles"
STATE = ROOT / ".tmp" / "prelaunch"
TICKS = STATE / "ticks.json"
ALIASES = TOOLS / "relink_aliases.json"
bp = Blueprint("prelaunch", __name__)
_lock = threading.Lock()


# ----------------------------------------------------------------------------- pages
def _read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def _text(s):
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _fold(s):
    """lowercase, accents off, punctuation to spaces: 'El Paredón' and 'el paredon' are one name"""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


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


def live_twin(rel):
    """where a draft goes when it is published: Drafts/.Full Articles/<c>/<p> -> <c>/<p>"""
    if rel.startswith("Drafts/.Full Articles/"):
        return rel[len("Drafts/.Full Articles/"):]
    if rel.startswith("Drafts/") and rel.endswith("/field-notes.html"):
        return rel[len("Drafts/"):]
    return rel


def countries():
    """Every country with something to launch: the draft articles, the draft field notes, and
    the live countries whose articles can be re-checked (El Salvador)."""
    out = {}

    def add(country, rel, status):
        p = ROOT / rel
        h = _read(p)
        c = out.setdefault(country, {"country": country, "label": "", "articles": []})
        c["articles"].append({"rel": rel, "title": title_of(h) or p.stem, "kind": kind_of(rel), "status": status,
                              "live": status == "live" or (ROOT / live_twin(rel)).is_file()})
    if DRAFTS.is_dir():
        for d in sorted(DRAFTS.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                for p in sorted(d.glob("*.html")):
                    add(d.name, p.relative_to(ROOT).as_posix(), "draft")
    for d in sorted((ROOT / "Drafts").iterdir()):
        fn = d / "field-notes.html"
        if d.is_dir() and not d.name.startswith(".") and fn.is_file():
            add(d.name, fn.relative_to(ROOT).as_posix(), "draft")
    for d in sorted(ROOT.iterdir()):
        idx = d / "index.html"
        if d.is_dir() and d.name not in ("Drafts", "archive", ".tmp", "tools") and idx.is_file() and "country-article-card" in _read(idx):
            for p in sorted(d.glob("*.html")):
                rel = p.relative_to(ROOT).as_posix()
                if not any(a["rel"] == rel for a in out.get(d.name, {}).get("articles", [])):
                    add(d.name, rel, "live")
    order = {"country": 0, "itinerary": 1, "top10": 2, "guide": 3, "field-notes": 4}
    for c in out.values():
        c["articles"].sort(key=lambda a: (order.get(a["kind"], 9), a["title"].lower()))
        idx = next((a for a in c["articles"] if a["kind"] == "country"), None)
        c["label"] = (idx["title"] if idx and idx["title"] else c["country"].replace("-", " ").title())
        c["live"] = (ROOT / c["country"] / "index.html").is_file() or (ROOT / c["country"] / "field-notes.html").is_file()
    return sorted(out.values(), key=lambda c: c["label"].lower())


def live_articles():
    """every published full article: the pages a relink may point at today"""
    out = []
    for d in sorted(ROOT.iterdir()):
        idx = d / "index.html"
        if d.is_dir() and d.name not in ("Drafts", "archive", ".tmp", "tools") and idx.is_file() and "country-article-card" in _read(idx):
            out += [p.relative_to(ROOT).as_posix() for p in sorted(d.glob("*.html"))]
    return out


# ----------------------------------------------------------------------------- helpers
ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
HREF = re.compile(r'\bhref="([^"]*)"', re.I)
GMAPS = re.compile(r"https?://(?:www\.)?(?:google\.[a-z.]+/maps|maps\.google\.[a-z.]+|maps\.app\.goo\.gl|goo\.gl/maps)", re.I)
MASK = re.compile(r"<(script|style|svg)\b.*?</\1>", re.S | re.I)


def body_span(html):
    """where the article's own text is: from the article body to the footer (hero, nav, footer out)"""
    m = re.search(r'class="(?:article-body|artbody|fn-body|fn-content)[^"]*"', html)
    start = m.start() if m else 0
    end = html.find("<footer", start)
    return start, (end if end > 0 else len(html))


def body_of(html):
    s, e = body_span(html)
    return html[s:e]


def resolve_rel(href, page_rel):
    """a page-relative href to a repo path, or None for anything that is not a local file"""
    h = H.unescape(href).split("#")[0].split("?")[0].strip()
    if not h or re.match(r"^[a-z]+:|^//", h, re.I):
        return None
    from urllib.parse import unquote
    return os.path.normpath(os.path.join(os.path.dirname(page_rel), unquote(h))).replace("\\", "/")


def relpath(target, page_rel):
    return os.path.relpath(target, os.path.dirname(page_rel) or ".").replace("\\", "/")


def result(status, summary, items=None, fix=None, tool=None, note=None):
    r = {"status": status, "summary": summary, "items": items or []}
    if fix:
        r["fix"] = fix
    if tool:
        r["tool"] = tool
    if note:
        r["note"] = note
    return r


def count_label(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


# ----------------------------------------------------------------------------- relink
GENERIC_NAMES = {"coast", "the coast", "valleys", "the valleys", "crowds", "city", "town", "turquoise coast",
                 "the turquoise coast", "capital", "guide", "itinerary"}
SLUG_TAILS = {"hike", "guide", "day", "trip", "tour", "itinerary"}
# a one-word place name also matches its link written with one of these after it
# ("Acatenango Volcano", "Orgov Observatory"), never with anything else ("Gyumri Train Station")
PLACE_TAILS = {"volcano", "observatory", "telescope", "monastery", "city", "town", "village", "island", "islands",
               "lake", "national", "park", "hike", "radio", "space", "fortress", "cathedral"}


def article_names(rel, html):
    """the place names an article is about, from its headline and its file name"""
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
        names.append(" ".join(slug))
    try:
        extra = json.loads(ALIASES.read_text(encoding="utf-8")).get(live_twin(rel), []) if ALIASES.is_file() else []
    except Exception:
        extra = []
    seen, out = set(), []
    for n in names + list(extra):
        f = _fold(n)
        if f and f not in seen:
            seen.add(f)
            out.append(n)
    return out


def name_matches(link_text, name):
    lt, nt = _fold(link_text).split(), _fold(name).split()
    if not lt or not nt:
        return False
    if len(nt) == 1:
        return lt[0] == nt[0] and all(w in PLACE_TAILS for w in lt[1:]) and len(lt) <= 3
    return set(nt) <= set(lt) and len(lt) <= len(nt) + 2


def relink_scan(ctx):
    """Google Maps links to places that have their own article, in every launching page and in
    the country's live pages"""
    targets = []                                      # (rel, [names], live)
    for rel in ctx["set"]:
        targets.append((rel, article_names(rel, ctx["html"][rel]), (ROOT / live_twin(rel)).is_file() or not rel.startswith("Drafts/")))
    for rel in live_articles():
        if rel not in ctx["set"] and all(live_twin(r) != rel for r in ctx["set"]):
            targets.append((rel, article_names(rel, _read(ROOT / rel)), True))
    targets = [t for t in targets if t[1]]
    sources = list(ctx["set"])
    for c in ctx["countries"]:                         # the country's LIVE pages link to these too
        for p in (ROOT / c).glob("*.html") if (ROOT / c).is_dir() else []:
            r = p.relative_to(ROOT).as_posix()
            if r not in sources and all(live_twin(s) != r for s in sources):
                sources.append(r)
    items = []
    for src in sources:
        h = ctx["html"].get(src) or _read(ROOT / src)
        masked = MASK.sub(lambda m: " " * len(m.group(0)), h)
        # map key rows and figures keep their Google links: they belong to the pins
        for fig in re.finditer(r"<figure\b.*?</figure>", masked, re.S | re.I):
            masked = masked[:fig.start()] + " " * (fig.end() - fig.start()) + masked[fig.end():]
        s0, e0 = body_span(masked)
        for m in ANCHOR.finditer(masked, s0, e0):
            hm = HREF.search(m.group(1))
            if not hm or not GMAPS.search(hm.group(1)):
                continue
            text = _text(h[m.start(2):m.end(2)])
            for trel, names, live in targets:
                if live_twin(trel) == live_twin(src):
                    continue
                if any(name_matches(text, n) for n in names):
                    now = live or src.startswith("Drafts/")      # a live page may not link to a page that is not live yet
                    dest = trel if src.startswith("Drafts/") or not trel.startswith("Drafts/") else live_twin(trel)
                    items.append({"page": src, "text": text, "target": trel, "href": relpath(dest, src),
                                  "at": m.start(), "tag": h[m.start():m.start(2)],
                                  "detail": "“%s” goes to Google Maps; your %s article covers it" % (text, _title(trel, ctx)),
                                  "apply": bool(now),
                                  "why": "" if now else "Apply after %s is published: until then the live page would link to a missing page." % Path(trel).name})
                    break
    return items


def _title(rel, ctx):
    h = ctx["html"].get(rel) or _read(ROOT / rel)
    return re.sub(r"^The\s+", "", title_of(h).split("—")[0].split(":")[0].strip()) or Path(rel).stem


def relink_apply(items):
    """rewrite the chosen links in place: the <a> keeps its classes, loses target/rel, gets the
    article's address. Each item is found again by its exact tag and text, never by offset alone."""
    by_page, done, missed = {}, [], []
    for it in items:
        by_page.setdefault(it["page"], []).append(it)
    for page, its in by_page.items():
        p = ROOT / page
        raw = io.open(p, encoding="utf-8", newline="").read()
        s = raw
        for it in sorted(its, key=lambda x: -int(x.get("at", 0))):
            tag = it["tag"]
            pos = [m.start() for m in re.finditer(re.escape(tag), s)]
            pos = sorted(pos, key=lambda x: abs(x - int(it.get("at", 0))))
            hit = next((x for x in pos if _text(s[x + len(tag):s.find("</a>", x)]) == it["text"]), None)
            if hit is None:
                missed.append(it)
                continue
            attrs = tag[2:-1]
            attrs = re.sub(r'\s(?:target|rel)="[^"]*"', "", attrs)
            attrs = HREF.sub(lambda _m: 'href="%s"' % it["href"], attrs)
            s = s[:hit] + "<a" + attrs + ">" + s[hit + len(tag):]
            done.append(it)
        if s != raw:
            io.open(p, "w", encoding="utf-8", newline="").write(s)
    return done, missed


# ----------------------------------------------------------------------------- CLAUDE's checks
def c_prose(ctx):
    import prose_check
    items, errs = [], 0
    for rel in ctx["set"]:
        for f in prose_check.check(ctx["html"][rel]):
            if f["kind"] == "maps":
                continue
            a = f.get("anchor") or {}
            q = (a.get("before", "")[-30:] + "[" + a.get("quote", "") + "]" + a.get("after", "")[:24]).strip()
            errs += f.get("severity") == "error"
            items.append({"page": rel, "detail": f["message"], "value": q, "rule": f["kind"]})
    if not items:
        return result("pass", "No typos, spacing slips, British spellings, em dashes in body prose or leftover notes.")
    return result("fail" if errs else "warn", "%s: %s." % (count_label(len(items), "finding"), ", ".join(sorted({i["rule"] for i in items}))),
                  items, tool={"kind": "review", "label": "Fix in the review margin"})


def c_voice(ctx):
    import voice_check
    items, low = [], 0
    for rel in ctx["set"]:
        if ctx["kind"][rel] in ("country",):
            continue
        try:
            r = voice_check.score(ctx["html"][rel])
        except Exception as e:
            items.append({"page": rel, "detail": "voice check failed: %s" % e})
            continue
        if r.get("score") is None:
            continue
        low += r["score"] < 70
        off = [l for l in r.get("lines", []) if isinstance(l, dict) and not l.get("ok")]
        items.append({"page": rel, "ok": r["score"] >= 70, "detail": "Voice %s (%d words)" % (r["score"], r.get("words", 0)),
                      "value": "; ".join("%s %s, you %s" % (l.get("label"), l.get("value"), l.get("base")) for l in off[:3]) or "on your baseline"})
    if not items:
        return result("pass", "Nothing long enough to score.")
    return result("warn" if low else "pass", "Measured against your published pages: " + ", ".join(
        "%s %s" % (Path(i["page"]).stem, i["detail"].split()[1]) for i in items[:8]), items,
                  tool={"kind": "review", "label": "See it in the editor"})


def c_repeats(ctx):
    """a sentence that appears word for word in two of the launching pages (the de-template and
    overlap passes of workflows/publish_article.md, stage 2)"""
    if len(ctx["set"]) < 2:
        return result("skip", "Needs two or more pages.")
    seen = {}
    for rel in ctx["set"]:
        t = _text(MASK.sub(" ", body_of(ctx["html"][rel])))
        for sent in re.split(r"(?<=[.!?])\s+", t):
            w = _fold(sent).split()
            if len(w) >= 9:
                seen.setdefault(" ".join(w), set()).add(rel)
    items = [{"page": sorted(v)[0], "detail": "Also in " + ", ".join(Path(x).name for x in sorted(v)[1:]), "value": k[:140]}
             for k, v in seen.items() if len(v) > 1]
    if not items:
        return result("pass", "No sentence is repeated word for word across these pages.")
    return result("warn", count_label(len(items), "sentence") + " appear in more than one page.", items[:40],
                  tool={"kind": "editor", "label": "Open in the editor"})


def c_relink(ctx):
    items = relink_scan(ctx)
    if not items:
        return result("pass", "No Google Maps link points at a place you have an article about.")
    n = sum(1 for i in items if i["apply"])
    return result("warn", "%s could link to your own article instead of Google Maps%s." % (
        count_label(len(items), "link"), "" if n == len(items) else " (%d once the article is live)" % (len(items) - n)),
                  items, fix={"kind": "relink", "label": "Apply selected"})


def c_unlaunched(ctx):
    """links from a launching page to a draft that is not launching with it: after the publish
    they point at a page that does not exist"""
    items = []
    for rel in ctx["set"]:
        for m in HREF.finditer(ctx["html"][rel]):
            t = resolve_rel(m.group(1), rel)
            if not t or not t.endswith(".html"):
                continue
            if t.startswith("Drafts/") and t not in ctx["set"] and not (ROOT / live_twin(t)).is_file():
                items.append({"page": rel, "detail": "Links to %s, a draft that is not in this launch" % Path(t).name, "value": t})
    uniq = {(i["page"], i["value"]): i for i in items}
    items = list(uniq.values())
    if not items:
        return result("pass", "Every article these pages link to is live or launching with them.")
    return result("fail", "%s to drafts that are not launching: they 404 once these are live." % count_label(len(items), "link"),
                  items, tool={"kind": "editor", "label": "Open in the editor"},
                  note="Launch those articles too (choose All articles), or unlink them.")


def c_internal(ctx):
    items = []
    for rel in ctx["set"]:
        h = ctx["html"][rel]
        ids = re.findall(r'\bid="([^"]+)"', MASK.sub(" ", h))
        for d in sorted({i for i in ids if ids.count(i) > 1}):
            items.append({"page": rel, "detail": "Duplicate id", "value": d})
        idset = set(ids) | set(re.findall(r'\bname="([^"]+)"', h))
        for m in HREF.finditer(MASK.sub(" ", h)):
            href = H.unescape(m.group(1))
            if href.startswith("#") and len(href) > 1 and href[1:] not in idset:
                items.append({"page": rel, "detail": "Anchor with no target", "value": href})
                continue
            t = resolve_rel(href, rel)
            if t and not (ROOT / t).exists():
                items.append({"page": rel, "detail": "Link to a file that does not exist", "value": href})
    if not items:
        return result("pass", "Every internal link, anchor and table-of-contents entry resolves.")
    return result("fail", count_label(len(items), "broken link") + ".", items, tool={"kind": "editor", "label": "Open in the editor"})


def c_maps_search(ctx):
    items = []
    for rel in ctx["set"]:
        for m in ANCHOR.finditer(MASK.sub(" ", ctx["html"][rel])):
            hm = HREF.search(m.group(1))
            if hm and re.search(r"google\.[a-z.]+/maps/search/|maps\?q=|/maps/search\?", H.unescape(hm.group(1))):
                items.append({"page": rel, "detail": "Still a search", "value": _text(m.group(2))[:60]})
    if not items:
        return result("pass", "Every Google Maps link opens a place, not a results page.")
    return result("fail", "%s still drop the reader on a Google search." % count_label(len(items), "map link"), items,
                  tool={"kind": "review", "label": "Resolve in the editor"})


IMGTAG = re.compile(r"<img\b[^>]*>", re.I)


def _attr(tag, name):
    m = re.search(r'\b%s="([^"]*)"' % name, tag, re.I)
    return H.unescape(m.group(1)) if m else None


def c_photos(ctx):
    items = []
    for rel in ctx["set"]:
        for t in IMGTAG.findall(body_of(ctx["html"][rel])):
            src = _attr(t, "src") or ""
            if "/Images/" in src and "/Images/web/" not in src:
                items.append({"page": rel, "detail": "Full-resolution original", "value": src.rsplit("/", 1)[-1]})
    if not items:
        return result("pass", "Every body photo is a compressed web copy.")
    fixable = [r for r in {i["page"] for i in items} if r.startswith("Drafts/.Full Articles/")]
    return result("fail", "%s still at full resolution: a phone downloads the original." % count_label(len(items), "photo"), items,
                  fix={"kind": "photos", "label": "Run the photo pipeline", "pages": fixable} if fixable else None)


def c_tiers(ctx):
    import audit
    pages = [(ROOT / r, r) for r in ctx["set"]]
    have = lambda f: (ROOT / f).is_file()
    items = []
    for g in audit.check_tiers(pages, have) + audit.check_heroes(pages, have):
        for f in g["findings"]:
            if f["rule"] in ("hero-unchecked",):
                continue
            items.append({"page": g["name"], "detail": f["detail"], "value": f.get("value", ""), "sev": f["severity"]})
    if not items:
        return result("pass", "Every <picture> and hero has its phone, tablet and 2x/3x files.")
    high = any(i["sev"] == "high" for i in items)
    fixable = sorted({i["page"] for i in items if i["page"].startswith("Drafts/.Full Articles/")})
    kinds = {}
    for i in items:
        k = "missing files" if "not published" in i["detail"] else "single-size sources" if "single size" in i["detail"] else "no WebP" if "WebP" in i["detail"] else "originals as fallback" if "original" in i["detail"] else "other"
        kinds[k] = kinds.get(k, 0) + 1
    return result("fail" if high else "warn", ", ".join("%d %s" % (n, k) for k, n in sorted(kinds.items(), key=lambda x: -x[1])) + ".", items[:80],
                  fix={"kind": "photos", "label": "Run the photo pipeline", "pages": fixable} if fixable else None)


def c_alt(ctx):
    items = []
    for rel in ctx["set"]:
        for t in IMGTAG.findall(body_of(ctx["html"][rel])):
            full = _attr(t, "src") or ""
            src = full.rsplit("/", 1)[-1]
            if "/flags/" in full or "city-maps" in full or full.lower().endswith(".svg"):
                continue
            alt = _attr(t, "alt")
            if alt is None or not alt.strip():
                items.append({"page": rel, "detail": "No alt text", "value": src})
            elif re.match(r"^(img|dsc|pxl|photo|image)[\s_-]?\d", alt.strip(), re.I) or re.search(r"\.(jpe?g|png|heic|webp)$", alt, re.I) or re.match(r"^[0-9a-f]{16,}$", alt.strip()):
                items.append({"page": rel, "detail": "Alt text is a file name", "value": alt})
    if not items:
        return result("pass", "Every photo describes itself for screen readers and image search.")
    return result("fail", count_label(len(items), "photo") + " without real alt text.", items,
                  tool={"kind": "editor", "label": "Write them in the editor"})


def c_dup_photos(ctx):
    guides = [r for r in ctx["set"] if ctx["kind"][r] == "guide"]
    if len(guides) < 2:
        return result("skip", "Needs two or more guides.")
    seen = {}
    for rel in guides:
        for t in IMGTAG.findall(body_of(ctx["html"][rel])):
            src = _attr(t, "src") or ""
            if "/flags/" in src or "city-maps" in src or "itinerary-maps" in src:
                continue
            stem = re.sub(r"-(?:mob-)?(?:[123]x)$|-mob$", "", src.rsplit("/", 1)[-1].rsplit(".", 1)[0])
            seen.setdefault(stem, set()).add(rel)
    items = [{"page": sorted(v)[0], "detail": "Also in " + ", ".join(Path(x).name for x in sorted(v)[1:]), "value": k}
             for k, v in seen.items() if len(v) > 1]
    if not items:
        return result("pass", "No photo is used in two guides (the itinerary and the top 10 may borrow).")
    return result("warn", count_label(len(items), "photo") + " appear in more than one article.", items,
                  tool={"kind": "editor", "label": "Open in the editor"})


def c_city_maps(ctx):
    import math
    items, n = [], 0
    for cf in sorted((TOOLS / "city_maps").glob("*.json")):
        try:
            c = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            continue
        art = (c.get("article") or "").replace("\\", "/")
        if art not in ctx["set"]:
            continue
        n += 1
        h = ctx["html"][art]
        slug = c.get("slug", cf.stem)
        fig = next((m.group(0) for m in re.finditer(r'<figure class="citymap-fig".*?</figure>', h, re.S)
                    if "city-maps/%s.png" % slug in m.group(0)), None)
        if not fig:
            items.append({"page": art, "detail": "The %s map is not embedded" % cf.stem, "value": "python tools/city_map.py %s" % cf.stem})
            continue
        pos = {p["name"]: (p["lat"], p["lon"]) for p in c.get("pois", [])}
        for u, name in re.findall(r'<a class="cmrow"[^>]*href="([^"]+)"[^>]*>.*?<span class="nm">([^<]+)</span>', fig, re.S):
            name = H.unescape(name)
            if name not in pos:
                continue
            if "/maps/place/" not in u:
                items.append({"page": art, "detail": "%s map: %s links to a search" % (cf.stem, name), "value": u[:80]})
                continue
            pts = re.findall(r"!3d(-?[\d.]+)!4d(-?[\d.]+)", u) or re.findall(r"@(-?[\d.]+),(-?[\d.]+)", u)
            if pts:
                la, lo = map(float, pts[-1])
                d = math.hypot((la - pos[name][0]) * 111, (lo - pos[name][1]) * 111 * math.cos(math.radians(la)))
                if d > 3:
                    items.append({"page": art, "detail": "%s map: %s's link lands %.1f km from its pin" % (cf.stem, name, d), "value": u.split("/data=")[0][-60:]})
    for rel in ctx["set"]:
        if ctx["kind"][rel] == "itinerary" and "itinerary-map" not in ctx["html"][rel] and "Suggested Route Overview" not in ctx["html"][rel]:
            items.append({"page": rel, "detail": "No route map", "value": "tools/itinerary_map.py + embed_itinerary_map.py"})
    if not items:
        return result("pass", "%s embedded; every key row lands on its pin." % count_label(n, "city map") if n else "No city maps belong to these pages.")
    return result("warn", count_label(len(items), "map problem") + ".", items, tool={"kind": "maps", "label": "Open the Maps tab"})


def _meta(html, prop, attr="property"):
    m = re.search(r'<meta[^>]*%s="%s"[^>]*content="([^"]*)"' % (attr, re.escape(prop)), html, re.I) or \
        re.search(r'<meta[^>]*content="([^"]*)"[^>]*%s="%s"' % (attr, re.escape(prop)), html, re.I)
    return H.unescape(m.group(1)) if m else None


def c_seo(ctx):
    items = []
    for rel in ctx["set"]:
        h = ctx["html"][rel]
        t = re.search(r"<title>(.*?)</title>", h, re.S)
        title = H.unescape(t.group(1).strip()) if t else ""
        desc = _meta(h, "description", "name") or ""
        if not title:
            items.append({"page": rel, "detail": "No search title", "sev": "high"})
        elif len(title) > 70:
            items.append({"page": rel, "detail": "Search title is %d characters (up to ~70)" % len(title), "value": title})
        if "field notes" in title.lower():
            items.append({"page": rel, "detail": "“Field Notes” in the title is brand, nobody searches it", "value": title})
        if not desc:
            items.append({"page": rel, "detail": "No description", "sev": "high"})
        elif len(desc) >= 160:
            items.append({"page": rel, "detail": "Description is %d characters (under 160)" % len(desc), "value": desc})
        if not _meta(h, "og:title"):
            items.append({"page": rel, "detail": "No og:title"})
        ogd = _meta(h, "og:description")
        if ogd is not None and ogd != desc:
            items.append({"page": rel, "detail": "og:description differs from the description", "value": ogd[:120]})
        if ctx["kind"][rel] not in ("country",) and (_meta(h, "og:type") or "") != "article":
            items.append({"page": rel, "detail": "og:type is %r, an article page is \"article\"" % _meta(h, "og:type")})
        ld = re.search(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', h, re.S)
        if ld and '"headline"' in ld.group(1) and not rel.startswith("Drafts/"):
            hm = re.search(r'"headline"\s*:\s*"([^"]*)"', ld.group(1))
            if hm and title and hm.group(1).strip() not in title:
                items.append({"page": rel, "detail": "The JSON-LD headline does not match the title", "value": hm.group(1)})
        if ctx["kind"][rel] == "field-notes":
            h1 = title_of(h)
            if h1 and not h1.lower().endswith("travel guide"):
                items.append({"page": rel, "detail": "A country guide headline reads “<Country> Travel Guide”", "value": h1})
    note = "The canonical link, og:url and the JSON-LD dates are written by the publish (publish_country.py step 8)."
    if not items:
        return result("pass", "Titles, descriptions and share tags are in place and within length.", note=note)
    high = any(i.get("sev") == "high" for i in items)
    return result("fail" if high else "warn", count_label(len(items), "metadata problem") + ".", items,
                  tool={"kind": "seo", "label": "Open the Search tab"}, note=note)


def c_dates(ctx):
    import article_dates as ad
    items = []
    for rel in ctx["set"]:
        h = ctx["html"][rel]
        b, j = ad.BYLINE.search(h), ad.JSONLD.search(h)
        if b and j and ad.iso(b.group(2)) and ad.iso(b.group(2)) != j.group(2):
            items.append({"page": rel, "detail": "The date under the subtitle says %s, dateModified says %s" % (b.group(2), j.group(2))})
    if not items:
        return result("pass", "The date under each subtitle agrees with the page's dateModified.")
    return result("warn", count_label(len(items), "page") + " state two different dates.", items,
                  fix={"kind": "dates", "label": "Settle them (article_dates --fix)", "pages": [i["page"] for i in items]})


def _thumbs(ctx, rel):
    try:
        from urllib.parse import quote
        return ctx["client"].get("/api/thumbs?rel=" + quote(rel)).get_json() or {}
    except Exception:
        return {}


def c_cards(ctx):
    """the country-page card and the share image: present, and this article's own"""
    items = []
    for rel in ctx["set"]:
        if ctx["kind"][rel] in ("country", "field-notes"):
            continue
        d = _thumbs(ctx, rel)
        sl = {s["key"]: s for s in d.get("slots", [])}
        card, og = (sl.get("card") or {}).get("img") or "", (sl.get("og") or {}).get("img") or ""
        if not card:
            items.append({"page": rel, "detail": "No card on the country page"})
        elif "/dest-cards/" in card:
            items.append({"page": rel, "detail": "The card is still the country's placeholder photo", "value": card.rsplit("/", 1)[-1]})
        country = rel.split("/")[-2]
        if not og:
            items.append({"page": rel, "detail": "No share image"})
        elif og.rsplit("/", 1)[-1] in ("%s.jpg" % country, "og-image.jpg"):
            items.append({"page": rel, "detail": "The share image is the country's, not this article's", "value": og.rsplit("/", 1)[-1]})
    if not items:
        return result("pass", "Every article has its own card photo and share image.")
    return result("warn", count_label(len(items), "thumbnail problem") + ".", items,
                  tool={"kind": "thumbs", "label": "Open Thumbnails"})


def c_country_page(ctx):
    idx = next((r for r in ctx["all_country"] if r.endswith("/index.html")), None)
    if not idx:
        return result("skip", "This country has no country page.")
    h = _read(ROOT / idx)
    items = []
    for rel in ctx["set"]:
        if rel == idx or ctx["kind"][rel] in ("country", "field-notes"):
            continue
        name = rel.rsplit("/", 1)[-1]
        if ("location.href='%s'" % name) not in h and ('href="%s"' % name) not in h:
            items.append({"page": rel, "detail": "No card on the country page", "value": idx})
    if not items:
        return result("pass", "Every launching article has a card on the country page.")
    return result("warn", count_label(len(items), "article") + " missing from the country page.", items,
                  tool={"kind": "editor-page", "label": "Open the country page", "rel": idx})


def c_coverage(ctx):
    import coverage_check as cc
    out = []
    for country in ctx["countries"]:
        fn = ROOT / country / "field-notes.html"
        if not fn.exists():
            fn = ROOT / "Drafts" / country / "field-notes.html"
        arts = sorted((DRAFTS / country).glob("*.html")) or sorted(p for p in (ROOT / country).glob("*.html") if p.name != "field-notes.html")
        if not fn.exists() or not arts:
            continue
        texts = [cc.prose(p) for p in arts]
        for n, u in cc.linked_names(fn):
            if not cc.covered(n, texts):
                out.append({"page": fn.relative_to(ROOT).as_posix(), "detail": "In the field notes, in no article", "value": n})
    if not out:
        return result("pass", "Every place in the field notes made it into an article.")
    return result("warn", count_label(len(out), "place") + " from the field notes are in no article.", out,
                  tool={"kind": "editor", "label": "Open in the editor"})


def c_render(ctx):
    """load each page at phone, tablet and desktop width: broken images, sideways scroll, script errors"""
    args = [sys.executable, str(TOOLS / "prelaunch_render.py")] + list(ctx["set"])
    try:
        r = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        data = json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        return result("error", "The render check could not run: %s" % str(e)[:160])
    items = []
    for rel, widths in data.items():
        for w, f in widths.items():
            for b in f.get("broken", []):
                items.append({"page": rel, "detail": "Broken image at %spx" % w, "value": b, "sev": "high"})
            if f.get("overflow"):
                items.append({"page": rel, "detail": "Scrolls sideways at %spx (%dpx wide)" % (w, f["overflow"]), "value": f.get("wide", "")})
            for e in f.get("errors", [])[:3]:
                items.append({"page": rel, "detail": "Script error at %spx" % w, "value": e[:140]})
            if f.get("error"):
                items.append({"page": rel, "detail": "Did not load at %spx" % w, "value": f["error"][:140], "sev": "high"})
    if not items:
        return result("pass", "Loads clean at 393, 820 and 1440 px: no broken images, no sideways scroll, no script errors.")
    high = any(i.get("sev") == "high" for i in items)
    return result("fail" if high else "warn", count_label(len(items), "render problem") + ".", items,
                  tool={"kind": "preview", "label": "Preview it"})


CLAUDE = [
    # (id, group, title, why, fn)
    ("prose", "Writing", "Typos, spacing, American spelling, em dashes, leftover notes",
     "The mechanical writing checks (prose_check: lint_prose, americanize, paste artifacts, [ ] notes).", c_prose),
    ("voice", "Writing", "Sounds like you", "voice_check.py: contractions, numerals and pet words per 1,000 words against your live pages.", c_voice),
    ("repeats", "Writing", "No sentence repeated across articles", "The de-template and overlap passes: one sentence, one page.", c_repeats),
    ("relink", "Links", "Places you wrote about link to your article", "A Google Maps link to a place with its own article (live, or launching now) should open the article.", c_relink),
    ("unlaunched", "Links", "No links to drafts that aren't launching", "A link to a draft left behind is a 404 the day these go live.", c_unlaunched),
    ("internal", "Links", "Internal links, anchors and the table of contents resolve", "Missing files, #anchors with no target, duplicate ids.", c_internal),
    ("maps-search", "Links", "Map links open a place, not a search", "A search link drops the reader on a Google results page.", c_maps_search),
    ("photos", "Photos", "Photos are compressed", "No full-resolution original in the body (some are 13 MB).", c_photos),
    ("tiers", "Photos", "Every photo has its phone, 2x and 3x files", "audit.py's tier and hero checks, against the files on disk.", c_tiers),
    ("alt", "Photos", "Every photo has real alt text", "Not missing, not a file name.", c_alt),
    ("dup-photos", "Photos", "No photo used in two articles", "Across the pages launching together.", c_dup_photos),
    ("city-maps", "Maps", "City and route maps are embedded and point true", "Each key row's link lands on its pin; itineraries have a route map.", c_city_maps),
    ("seo", "Search & sharing", "Search title, description and share tags", "CLAUDE.md: title up to ~70, description under 160, og tags, headline matches.", c_seo),
    ("cards", "Search & sharing", "Each article has its own card and share image", "Not the country's placeholder photo or share image.", c_cards),
    ("dates", "Search & sharing", "One date per page", "The date under the subtitle and dateModified agree.", c_dates),
    ("country-page", "Country", "The country page has a card for every article", "An article with no card is only findable by search.", c_country_page),
    ("coverage", "Country", "Everything in the field notes made it into an article", "coverage_check.py (Sevanavank, the Black Wall and the plane once fell through).", c_coverage),
    ("render", "Rendering", "Loads clean at phone, tablet and desktop width", "Broken images, sideways scroll and script errors at 393, 820 and 1440 px.", c_render),
]

# the checks that are about the launch as a whole; every other one is reported per article
COUNTRY_SCOPE = {"repeats", "dup-photos", "country-page", "coverage"}

KEVIN = [
    # (id, title, why, tool kind, tool label, scope)
    ("k-review", "Review is clear", "Every comment resolved and every proposed change accepted or rejected.", "review", "Open the review", "article"),
    ("k-search", "Search title and description read right", "What Google shows. Lead with what people search, then real place names.", "seo", "Open the Search tab", "article"),
    ("k-thumbs", "Thumbnails and share image", "The card on the country page, the home and posts card, the phone card and the link preview.", "thumbs", "Open Thumbnails", "article"),
    ("k-hero", "Hero photo, crop and scrim", "The first thing anyone sees, at desktop and phone crops.", "covers", "Open Covers", "article"),
    ("k-headline", "Headline and subtitle", "The words over the hero.", "editor", "Open in the editor", "article"),
    ("k-read", "Read it once on a phone, top to bottom", "Where most readers are. Catches what a desktop read misses.", "preview", "Preview on a phone", "article"),
    ("k-photos", "Photo order, crops and pairs", "Each photo next to the paragraph it belongs to, portraits paired.", "editor", "Open in the editor", "article"),
    ("k-sensitive", "Nothing in the photos you'd rather not publish", "Signs and slogans you can't read, faces, plates, anything political.", "editor", "Open in the editor", "article"),
    ("k-facts", "Prices, hours and places still current", "Anything that could have changed since the trip.", "editor", "Open in the editor", "article"),
    ("k-links", "Recommendations link where you want", "Hostels, tours and restaurants: the booking site you'd actually send a friend to.", "editor", "Open in the editor", "article"),
    ("k-maps", "Maps look right", "Pins, labels, nothing hidden under another label.", "maps", "Open the Maps tab", "article"),
    ("k-destcard", "The country's destination card", "The photo on the destinations page and the country's share image.", "covers", "Open Covers", "country"),
    ("k-home", "Home page placement", "Whether it goes into Recent Dispatches or the featured row.", "", "", "country"),
]


def kevin_hints(ctx, rel):
    """what each of Kevin's checks can see today, as one line"""
    import redline
    h = ctx["html"].get(rel) or _read(ROOT / rel)
    out = {}
    try:
        st = redline.review_state(rel)
        open_c = [t for t in (st.get("comments") or {}).get("threads", []) if not t.get("resolved")]
        pend = [c for c in st.get("changes", []) if c["id"] not in (st.get("decisions") or {})]
        out["k-review"] = {"text": "Clear: no open comments, no undecided changes." if not (open_c or pend) else
                           "%s, %s." % (count_label(len(open_c), "open comment"), count_label(len(pend), "undecided change")),
                           "ok": not (open_c or pend)}
    except Exception as e:
        out["k-review"] = {"text": "Could not read the review: %s" % e}
    t = re.search(r"<title>(.*?)</title>", h, re.S)
    title = H.unescape(t.group(1).strip()) if t else ""
    desc = _meta(h, "description", "name") or ""
    out["k-search"] = {"text": "%s (%d/70) · %s (%d/160)" % (title or "no title", len(title), desc or "no description", len(desc)),
                       "ok": bool(title) and len(title) <= 70 and bool(desc) and len(desc) < 160}
    if ctx["kind"].get(rel) not in ("country", "field-notes"):
        d = _thumbs(ctx, rel)
        sl = {s["key"]: s for s in d.get("slots", [])}
        card = ((sl.get("card") or {}).get("img") or "").rsplit("/", 1)[-1]
        og = ((sl.get("og") or {}).get("img") or "").rsplit("/", 1)[-1]
        out["k-thumbs"] = {"text": "Card: %s · share image: %s" % (card or "none", og or "none"),
                           "ok": bool(card) and "dest-cards" not in ((sl.get("card") or {}).get("img") or ""), "img": (sl.get("card") or {}).get("img")}
    hero = re.search(r"hero-([a-z0-9-]+?)(?:-2x|-mob-1x|-mob)?\.(?:jpg|webp)", h)
    out["k-hero"] = {"text": "hero-%s" % hero.group(1) if hero else "No hero image found"}
    lead = re.search(r'class="(?:article-lead|artsub|fn-hero-sub)[^"]*"[^>]*>(.*?)</p>', h, re.S)
    out["k-headline"] = {"text": title_of(h) + ((" · " + _text(lead.group(1))) if lead else "")}
    words = len(_text(MASK.sub(" ", body_of(h))).split())
    out["k-read"] = {"text": "%d words, about %d minutes" % (words, max(1, round(words / 230)))}
    body = body_of(h)
    imgs = [t for t in IMGTAG.findall(body) if "/flags/" not in t and "city-maps" not in t]
    out["k-photos"] = {"text": count_label(len(imgs), "photo")}
    txt = _text(MASK.sub(" ", body))
    prices = re.findall(r"[$€£]\s?\d[\d,.]*|\b\d[\d,.]*\s?(?:AMD|dram|USD|EUR|GEL|lari|TRY|lira|GTQ|quetzales?|MKD|denars?|NZD)\b", txt, re.I)
    hours = re.findall(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b", txt, re.I)
    out["k-facts"] = {"text": "%s and %s mentioned" % (count_label(len(prices), "price"), count_label(len(hours), "time"))}
    ext = [H.unescape(m.group(1)) for m in HREF.finditer(body) if m.group(1).startswith("http") and not GMAPS.search(m.group(1))
           and "getawayguide.io" not in m.group(1)]
    doms = {}
    for u in ext:
        d = re.sub(r"^https?://(www\.)?", "", u).split("/")[0]
        doms[d] = doms.get(d, 0) + 1
    out["k-links"] = {"text": ", ".join("%s %d" % kv for kv in sorted(doms.items(), key=lambda x: -x[1])[:5]) or "No outside links"}
    maps = len(re.findall(r'class="citymap-fig"', h)) + ("Suggested Route Overview" in h)
    out["k-maps"] = {"text": count_label(maps, "map") + " embedded"}
    return out


# ----------------------------------------------------------------------------- jobs
JOBS = {}


def _ctx(rels, client):
    rels = [r for r in rels if (ROOT / r).is_file()]
    cs = sorted({r.split("/")[-2] for r in rels})
    all_country = []
    for c in cs:
        all_country += [p.relative_to(ROOT).as_posix() for p in sorted((DRAFTS / c).glob("*.html"))] if (DRAFTS / c).is_dir() else []
        all_country += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / c).glob("*.html"))] if (ROOT / c).is_dir() else []
    return {"set": rels, "html": {r: _read(ROOT / r) for r in rels}, "kind": {r: kind_of(r) for r in rels},
            "countries": cs, "all_country": all_country, "client": client}


def _key(rels):
    return re.sub(r"[^a-z0-9]+", "-", ("|".join(sorted(rels))).lower())[-120:]


def _run(jid, rels, only, client):
    job = JOBS[jid]
    ctx = _ctx(rels, client)
    for cid, group, title, why, fn in CLAUDE:
        if only and cid not in only:
            continue
        job["current"] = cid
        t0 = time.time()
        try:
            r = fn(ctx)
        except Exception as e:
            import traceback
            r = result("error", "The check crashed: %s" % e, [{"detail": traceback.format_exc().splitlines()[-1]}])
        r["ms"] = int((time.time() - t0) * 1000)
        job["results"][cid] = r
    job["current"] = None
    job["state"] = "done"
    job["finished"] = datetime.now().isoformat(timespec="seconds")
    STATE.mkdir(parents=True, exist_ok=True)
    last = STATE / ("last-" + _key(rels) + ".json")
    prev = json.loads(last.read_text(encoding="utf-8")) if last.is_file() and only else {"results": {}}
    prev["results"].update(job["results"])
    prev.update({"rels": rels, "finished": job["finished"]})
    last.write_text(json.dumps(prev, indent=1, ensure_ascii=False), encoding="utf-8")


def _ticks():
    try:
        return json.loads(TICKS.read_text(encoding="utf-8")) if TICKS.is_file() else {}
    except Exception:
        return {}


# ----------------------------------------------------------------------------- routes
@bp.route("/launch.js")
def launch_js():
    return send_file(ROOT / "launch.js", mimetype="application/javascript")


@bp.route("/api/launch/articles")
def api_articles():
    return jsonify({"ok": True, "countries": countries()})


@bp.route("/api/launch/checks")
def api_checks():
    return jsonify({"ok": True,
                    "claude": [{"id": c[0], "group": c[1], "title": c[2], "why": c[3], "scope": "country" if c[0] in COUNTRY_SCOPE else "article"} for c in CLAUDE],
                    "kevin": [{"id": k[0], "title": k[1], "why": k[2], "tool": {"kind": k[3], "label": k[4]} if k[3] else None, "scope": k[5]} for k in KEVIN]})


@bp.route("/api/launch/run", methods=["POST", "OPTIONS"])
def api_run():
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True) or {}
    rels = [r for r in d.get("rels") or [] if isinstance(r, str)]
    if not rels:
        return jsonify({"ok": False, "error": "choose an article"}), 400
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"state": "running", "results": {}, "current": None, "rels": rels,
                 "started": datetime.now().isoformat(timespec="seconds")}
    client = bp_app["app"].test_client()
    threading.Thread(target=_run, args=(jid, rels, set(d.get("only") or []), client), daemon=True).start()
    return jsonify({"ok": True, "job": jid})


@bp.route("/api/launch/job/<jid>")
def api_job(jid):
    j = JOBS.get(jid)
    if not j:
        abort(404)
    return jsonify(dict(j, ok=True))


@bp.route("/api/launch/last", methods=["POST", "OPTIONS"])
def api_last():
    if request.method == "OPTIONS":
        return "", 204
    rels = (request.get_json(force=True) or {}).get("rels") or []
    f = STATE / ("last-" + _key(rels) + ".json")
    return jsonify(dict(json.loads(f.read_text(encoding="utf-8")), ok=True) if f.is_file() else {"ok": True, "results": {}})


@bp.route("/api/launch/kevin", methods=["POST", "OPTIONS"])
def api_kevin():
    if request.method == "OPTIONS":
        return "", 204
    rels = [r for r in (request.get_json(force=True) or {}).get("rels") or [] if (ROOT / r).is_file()]
    ctx = _ctx(rels, bp_app["app"].test_client())
    ticks = _ticks()
    hints = {}
    for r in rels:
        try:
            hints[r] = kevin_hints(ctx, r)
        except Exception as e:
            hints[r] = {"_error": {"text": str(e)}}
    countries_ = {r.split("/")[-2] for r in rels}
    home = _read(ROOT / "index.html") if (ROOT / "index.html").is_file() else ""
    for c in countries_:
        dc = "Images/web/dest-cards/%s.jpg" % c
        n = len(re.findall(r'href="%s/' % re.escape(c), home))
        hints["country:" + c] = {"k-destcard": {"text": dc.split("/")[-1] if (ROOT / dc).is_file() else "No destination card yet",
                                                "ok": (ROOT / dc).is_file(), "img": dc if (ROOT / dc).is_file() else None},
                                 "k-home": {"text": "%s from the home page today" % count_label(n, "link")}}
    return jsonify({"ok": True, "hints": hints, "ticks": {k: v for k, v in ticks.items() if k in rels or k.split(":", 1)[-1] in countries_}})


@bp.route("/api/launch/tick", methods=["POST", "OPTIONS"])
def api_tick():
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True) or {}
    key, cid = d.get("key") or "", d.get("id") or ""
    if not key or not cid:
        return jsonify({"ok": False}), 400
    with _lock:
        t = _ticks()
        row = t.setdefault(key, {})
        if d.get("done"):
            row[cid] = {"at": datetime.now().isoformat(timespec="seconds")}
        else:
            row.pop(cid, None)
        STATE.mkdir(parents=True, exist_ok=True)
        TICKS.write_text(json.dumps(t, indent=1, ensure_ascii=False), encoding="utf-8")
    return jsonify({"ok": True, "tick": t.get(key, {}).get(cid)})


@bp.route("/api/launch/fix", methods=["POST", "OPTIONS"])
def api_fix():
    """the fixes the tab can run itself; every other check opens the tool that fixes it"""
    if request.method == "OPTIONS":
        return "", 204
    d = request.get_json(force=True) or {}
    kind = d.get("kind")
    if kind == "relink":
        items = [i for i in d.get("items") or [] if isinstance(i, dict) and i.get("apply") and (ROOT / i.get("page", "")).is_file()]
        done, missed = relink_apply(items)
        return jsonify({"ok": True, "log": "%s relinked%s." % (count_label(len(done), "link"), ", %d not found (the page changed; run again)" % len(missed) if missed else ""),
                        "pages": sorted({i["page"] for i in done})})
    if kind == "photos":
        pages = [p for p in d.get("pages") or [] if p.startswith("Drafts/.Full Articles/") and (ROOT / p).is_file()]
        logs = []
        for p in pages:
            r = subprocess.run([sys.executable, str(TOOLS / "draft_images.py"), p], cwd=str(ROOT), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=3600)
            logs.append("%s: %s" % (Path(p).name, "done" if r.returncode == 0 else "failed: " + (r.stderr or r.stdout)[-300:]))
        return jsonify({"ok": True, "log": "\n".join(logs), "pages": pages})
    if kind == "dates":
        pages = [p for p in d.get("pages") or [] if (ROOT / p).is_file()]
        logs = []
        for p in pages:
            args = [sys.executable, str(TOOLS / "article_dates.py"), "--fix", "--match", p]
            if p.startswith("Drafts/"):
                args.append("--drafts")
            r = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            logs.append((r.stdout or r.stderr).strip()[-300:])
        return jsonify({"ok": True, "log": "\n".join(logs), "pages": pages})
    return jsonify({"ok": False, "error": "no such fix"}), 400


bp_app = {}


def register(app):
    """photo_editor.py calls this: the Launch tab's API lives on the photo server"""
    bp_app["app"] = app
    app.register_blueprint(bp)
