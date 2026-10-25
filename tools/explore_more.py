"""Explore More: three related articles at the end of every live article and field-notes page (Kevin, 2026-10-23).

Desktop and tablet show three photo cards in a row; a phone shows three thumbnail rows. The three are picked by
Kevin's rules, in this order, until three are found (the page itself never counts):

  1. the same country's itinerary and top-10
  2. the same country's full articles (city guides, day trips), in the order of its country page
  3. the itinerary / top-10 of the neighboring countries, nearest first
  4. the full articles of the neighboring countries
  5. the field notes of the neighboring countries
  ...and when the neighbors run out, the same three tiers for the next-nearest countries.

"Neighboring" is a shared border (BORDERS below, only countries on the site); a country with no neighbor on the site
takes the nearest countries of its continent (Egypt -> Tanzania), and failing that the nearest anywhere
(Australia -> Indonesia, the Philippines). Distances are between CENTROIDS.

A slot Kevin pinned in the editor (tools/explore_pins.json: {page: [target|null x3]}) keeps its article; the other
slots follow the rules. A pin to a page that no longer exists is dropped. Drafts are never picked by the rules but
can be pinned.

Each card's title and photo are the ones the country page's card uses for that article (the share image for a
field-notes page), cut once per target into Images/web/explore/ at the site's tier multiples, ICC carried through.
The block is rebuilt from scratch on every run, so a changed pick or a renamed page never leaves a stale card.

    python tools/explore_more.py                      # every live article + field notes
    python tools/explore_more.py greece/meteora.html  # one page (a draft path works too)
    python tools/explore_more.py --dry-run --report   # the picks as JSON, nothing written
"""
import argparse, glob, html, json, math, os, re, sys
from pathlib import Path
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
import draft_paths as dp          # Drafts/Upcoming and Drafts/Full Articles (2026-10-24)
PINS = ROOT / "tools" / "explore_pins.json"
OUT_DIR = ROOT / "Images" / "web" / "explore"
DESK = (420, 636, 840)          # the card is ~227 px wide in the 729 px column: 1.85x / 2.8x / 3.7x (never ~1x/2x)
MOB = (104, 208, 312)           # the phone thumbnail is 104 px wide: 1x / 2x / 3x
WPM = 230
# "xm" alone (articles, field notes) or "xm measure" (the itinerary template). It matched only the first, so every run
# stacked another block on the two live itineraries (four each by 2026-10-24).
BLOCK_RE = re.compile(r'\n?[ \t]*<section class="xm(?: [^"]*)?"[^>]*>.*?</section><!-- /explore-more -->', re.S)
# the itinerary template's standing footnote; it belongs to the article, so it goes above the block, not under it
UPDATED_RE = re.compile(r'[ \t]*<div class="measure gg-updated">.*?</div>', re.S)

# the countries on the site: name, continent, centroid, and which site countries they border
COUNTRIES = {
    "albania": ("Albania", "europe", 41.15, 20.17), "argentina": ("Argentina", "americas", -38.4, -63.6),
    "armenia": ("Armenia", "asia", 40.07, 45.04), "australia": ("Australia", "oceania", -25.27, 133.78),
    "belgium": ("Belgium", "europe", 50.5, 4.47), "bosnia": ("Bosnia", "europe", 43.92, 17.68),
    "brazil": ("Brazil", "americas", -14.24, -51.93), "chile": ("Chile", "americas", -35.68, -71.54),
    "colombia": ("Colombia", "americas", 4.57, -74.3), "croatia": ("Croatia", "europe", 45.1, 15.2),
    "denmark": ("Denmark", "europe", 56.26, 9.5), "egypt": ("Egypt", "africa", 26.82, 30.8),
    "el-salvador": ("El Salvador", "americas", 13.79, -88.9), "estonia": ("Estonia", "europe", 58.6, 25.0),
    "finland": ("Finland", "europe", 61.9, 25.75), "georgia": ("Georgia", "europe", 42.3, 43.36),
    "germany": ("Germany", "europe", 51.17, 10.45), "greece": ("Greece", "europe", 39.07, 21.82),
    "guatemala": ("Guatemala", "americas", 15.78, -90.23), "hungary": ("Hungary", "europe", 47.16, 19.5),
    "india": ("India", "asia", 20.59, 78.96), "indonesia": ("Indonesia", "asia", -0.79, 113.92),
    "kosovo": ("Kosovo", "europe", 42.6, 20.9), "latvia": ("Latvia", "europe", 56.88, 24.6),
    "mexico": ("Mexico", "americas", 23.63, -102.55), "montenegro": ("Montenegro", "europe", 42.71, 19.37),
    "netherlands": ("Netherlands", "europe", 52.13, 5.29), "new-zealand": ("New Zealand", "oceania", -40.9, 174.89),
    "north-macedonia": ("North Macedonia", "europe", 41.6, 21.75), "peru": ("Peru", "americas", -9.19, -75.0),
    "philippines": ("Philippines", "asia", 12.88, 121.77), "serbia": ("Serbia", "europe", 44.02, 21.0),
    "slovenia": ("Slovenia", "europe", 46.15, 14.99), "sweden": ("Sweden", "europe", 60.13, 18.64),
    "switzerland": ("Switzerland", "europe", 46.82, 8.23), "tanzania": ("Tanzania", "africa", -6.37, 34.89),
    "turkiye": ("Türkiye", "asia", 38.96, 35.24),
}
BORDERS = {
    "albania": "montenegro kosovo north-macedonia greece", "argentina": "chile brazil", "armenia": "georgia turkiye",
    "belgium": "netherlands germany", "bosnia": "croatia serbia montenegro", "brazil": "argentina colombia peru",
    "chile": "argentina peru", "colombia": "brazil peru", "croatia": "slovenia hungary bosnia serbia montenegro",
    "denmark": "germany sweden", "el-salvador": "guatemala", "estonia": "latvia finland", "finland": "sweden estonia",
    "georgia": "armenia turkiye", "germany": "denmark netherlands belgium switzerland", "greece": "albania north-macedonia turkiye",
    "guatemala": "el-salvador mexico", "hungary": "slovenia croatia serbia", "kosovo": "albania north-macedonia serbia montenegro",
    "latvia": "estonia", "mexico": "guatemala", "montenegro": "croatia bosnia serbia kosovo albania", "netherlands": "belgium germany",
    "north-macedonia": "kosovo serbia albania greece", "peru": "colombia brazil chile", "serbia": "hungary croatia bosnia montenegro kosovo north-macedonia",
    "slovenia": "croatia hungary", "sweden": "finland denmark", "switzerland": "germany", "turkiye": "greece georgia armenia",
    "australia": "", "egypt": "", "india": "", "indonesia": "", "philippines": "", "tanzania": "", "new-zealand": "",
}
KIND_LABEL = {"itinerary": "Itinerary", "top10": "Top 10", "guide": "In-depth guide", "field-notes": "Field notes"}


def kind_of(name):
    if name == "index.html":
        return "country"
    if name == "field-notes.html":
        return "field-notes"
    if "itinerary" in name:
        return "itinerary"
    if name.startswith("top-10"):
        return "top10"
    return "guide"


def published(p):
    try:
        return 'http-equiv="refresh"' not in p.read_text(encoding="utf-8")[:4000]
    except OSError:
        return False


def dist(a, b):
    la1, lo1, la2, lo2 = (math.radians(x) for x in (COUNTRIES[a][2], COUNTRIES[a][3], COUNTRIES[b][2], COUNTRIES[b][3]))
    return math.acos(max(-1, min(1, math.sin(la1) * math.sin(la2) + math.cos(la1) * math.cos(la2) * math.cos(lo1 - lo2))))


def rings(country):
    """countries by closeness: the bordering ones nearest first, then the rest of the continent by distance, then
    everyone else by distance. A country with no border on the site starts at the continent."""
    near = lambda cs: sorted(cs, key=lambda c: dist(country, c))
    bord = [c for c in BORDERS.get(country, "").split() if c in COUNTRIES]
    cont = [c for c in COUNTRIES if c != country and c not in bord and COUNTRIES[c][1] == COUNTRIES[country][1]]
    rest = [c for c in COUNTRIES if c != country and c not in bord and c not in cont]
    return near(bord) + near(cont) + near(rest)


# ---- the catalog --------------------------------------------------------------------------------------------------
def _meta(s, prop):
    m = re.search(r'<meta (?:property|name)="%s" content="([^"]*)"' % re.escape(prop), s)
    return html.unescape(m.group(1)) if m else ""


def country_cards(folder):
    """{guide file: (title, image path relative to the folder)} from the country page's article cards"""
    idx = folder / "index.html"
    out = {}
    if not idx.is_file():
        return out
    s = idx.read_text(encoding="utf-8")
    for m in re.finditer(r'<div class="country-article-card" onclick="location\.href=\'([^\']+)\'" style="([^"]*)"', s):
        t = re.search(r'country-article-card-title">(.*?)</h3>', s[m.end():m.end() + 1500], re.S)
        u = re.search(r"url\('([^']+\.jpg)'\)", m.group(2))
        if t and u:
            out[m.group(1)] = (html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip(), u.group(1))
    return out


_CACHE = {}


def catalog():
    """every page a card can point to: live articles, live field notes, and drafts (pinnable only). Cached on the
    pages' mtimes: the editor's panel asks on every open, and reading 80 pages took seconds."""
    files = []
    for country in COUNTRIES:
        if (ROOT / country).is_dir():
            files += [(p, p.stat().st_mtime) for p in sorted((ROOT / country).glob("*.html"))]
        files += [(p, p.stat().st_mtime) for p in dp.articles(country)]
    sig = tuple(files)
    if _CACHE.get("sig") == sig:
        return _CACHE["cat"]
    cat = _catalog()
    _CACHE.update(sig=sig, cat=cat)
    return cat


def _catalog():
    cat = {}
    for country in COUNTRIES:
        folder = ROOT / country
        cards = country_cards(folder)
        pages = [p for p in sorted(folder.glob("*.html")) if p.name != "index.html"] if folder.is_dir() else []
        order = {h: i for i, h in enumerate(cards)}
        pages.sort(key=lambda p: order.get(p.name, 999))
        for p in pages:
            if not published(p):
                continue
            cat[p.relative_to(ROOT).as_posix()] = entry(p, country, cards, draft=False)
        for p in dp.articles(country):
            if p.name == "index.html" or p.name == "field-notes.html":
                continue
            cat[p.relative_to(ROOT).as_posix()] = entry(p, country, {}, draft=True)
    return cat


def entry(p, country, cards, draft):
    s = p.read_text(encoding="utf-8")
    kind = kind_of(p.name)
    name = COUNTRIES[country][0]
    if kind == "field-notes":
        title = "%s Travel Guide" % name
        img = "Images/web/og/%s.jpg" % country
    else:
        title = cards.get(p.name, (None, None))[0]
        if not title:
            t = re.search(r"<title>([^<]*)", s)
            title = re.sub(r"\s*[-–—]\s*getawayguide\s*$", "", html.unescape(t.group(1)).strip(), flags=re.I) if t else p.stem
        img = cards.get(p.name, (None, None))[1]
        img = re.sub(r"^(\.\./)+", "", img) if img else "Images/web/og/%s-%s.jpg" % (country, p.stem)
        if not (ROOT / img).is_file():
            img = "Images/web/og/%s-%s.jpg" % (country, p.stem)
    desc = _meta(s, "og:description") or _meta(s, "description")
    desc = re.sub(r"^Field notes from (the )?[^:]{2,40}:\s*", "", desc)
    words = len(re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<[^>]+>", " ", s, flags=re.S).split())
    return {"rel": p.relative_to(ROOT).as_posix(), "country": country, "name": name, "kind": kind, "title": title,
            "desc": desc, "img": img if (ROOT / img).is_file() else "", "mins": max(1, round(words / WPM)), "draft": draft}


# ---- the picks ----------------------------------------------------------------------------------------------------
def load_pins():
    try:
        return json.loads(PINS.read_text(encoding="utf-8")) if PINS.is_file() else {}
    except ValueError:
        return {}


def save_pins(pins):
    PINS.write_text(json.dumps(pins, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def country_of(rel):
    parts = rel.split("/")
    return parts[-2] if parts[-2] in COUNTRIES else None


def rule_order(rel, cat):
    """every candidate in rule order for this page"""
    me = country_of(rel)
    live = [e for e in cat.values() if not e["draft"] and e["rel"] != rel]
    by_c = {}
    for e in live:
        by_c.setdefault(e["country"], []).append(e)
    tier = lambda es, kinds: [e for e in es if e["kind"] in kinds]
    out = []
    own = by_c.get(me, [])
    out += tier(own, ("itinerary", "top10")) + tier(own, ("guide",))
    others = [c for c in (rings(me) if me in COUNTRIES else sorted(COUNTRIES, key=lambda c: COUNTRIES[c][0])) if c in by_c]
    bord = [c for c in BORDERS.get(me, "").split() if c in COUNTRIES] if me else []
    cont = [c for c in others if me and COUNTRIES[c][1] == COUNTRIES[me][1]]
    # the neighbors as one group (tiers 3, 4, 5 across all of them); a country with no neighbor on the site takes
    # its continent as the group (Egypt -> Tanzania); with nobody there either, the three nearest countries
    first = [c for c in others if c in bord] or cont or others[:3]
    later = [c for c in others if c not in first]
    tiers = (("itinerary", "top10"), ("guide",), ("field-notes",))
    for kinds in tiers:
        for c in first:
            out += tier(by_c.get(c, []), kinds)
    for c in later:                                   # then the next-nearest country, all its tiers, and the next
        for kinds in tiers:
            out += tier(by_c.get(c, []), kinds)
    seen, uniq = set(), []
    for e in out:
        if e["rel"] not in seen:
            seen.add(e["rel"]); uniq.append(e)
    return uniq


def picks(rel, cat, pins=None):
    """[(entry, 'pin'|'auto')] x3 for a page"""
    pins = load_pins() if pins is None else pins
    mine = (pins.get(rel) or [None, None, None])[:3] + [None] * 3
    chosen = []
    for t in mine[:3]:
        if t and t in cat and t != rel:
            chosen.append((cat[t], "pin"))
        else:
            chosen.append(None)
    taken = {e["rel"] for e, _ in [c for c in chosen if c]}
    auto = (e for e in rule_order(rel, cat) if e["rel"] not in taken)
    for i in range(3):
        if chosen[i] is None:
            e = next(auto, None)
            if e:
                chosen[i] = (e, "auto"); taken.add(e["rel"])
    return [c for c in chosen if c]


# ---- the images ---------------------------------------------------------------------------------------------------
def best_source(img):
    """the widest derived file of that image (a hero's 3600w webp beats its 2000 px jpg)"""
    p = ROOT / img
    stem = str(p)[:-4]
    cands = [Path(x) for x in glob.glob(glob.escape(stem) + "*.webp") + glob.glob(glob.escape(stem) + "*.jpg")]
    cands = [c for c in cands if c.is_file() and "-mob-" not in c.name]
    return max(cands, key=lambda c: Image.open(c).size[0]) if cands else p


def cut(src, out_stem, widths, dry, force=False):
    files = [Path(f"{out_stem}-{i}x.{ext}") for i in range(1, len(widths) + 1) for ext in ("webp", "jpg")]
    if dry or (not force and all(f.is_file() and f.stat().st_mtime >= src.stat().st_mtime for f in files)):
        return files
    im = ImageOps.exif_transpose(Image.open(src))
    icc = im.info.get("icc_profile")                 # BEFORE convert(): Display P3
    W, H = im.size
    if W / H > 1.5:
        cw = round(H * 1.5); box = ((W - cw) // 2, 0, (W - cw) // 2 + cw, H)
    else:
        ch = round(W / 1.5); top = max(0, (H - ch) // 3); box = (0, top, W, top + ch)
    crop = im.crop(box).convert("RGB")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for i, w in enumerate(widths, 1):
        small = crop.resize((w, round(w / 1.5)), Image.LANCZOS)
        small.save(f"{out_stem}-{i}x.jpg", quality=84, optimize=True, icc_profile=icc)
        small.save(f"{out_stem}-{i}x.webp", quality=82, method=6, icc_profile=icc)
    return files


def card_files(e, dry):
    if not e["img"]:
        return None
    key = "%s-%s" % (e["country"], Path(e["rel"]).stem)
    src = best_source(e["img"])
    stem = OUT_DIR / key
    cut(src, str(stem), DESK, dry)
    cut(src, str(stem) + "-mob", MOB, dry)
    return stem


# ---- the block ----------------------------------------------------------------------------------------------------
def rel_to(target, page_rel):
    return os.path.relpath(target, os.path.dirname(page_rel)).replace(os.sep, "/")


def block_html(page_rel, chosen, dry=False):
    page_dir = os.path.dirname(page_rel)
    cards = []
    for e, how in chosen:
        stem = card_files(e, dry)
        href = rel_to(e["rel"], page_rel)
        kind = KIND_LABEL[e["kind"]]
        k = ('<span class="xm-k g"><span class="dot"></span>%s · %s</span>' if e["kind"] in ("guide", "itinerary", "top10") else '<span class="xm-k">%s · %s</span>') % (html.escape(e["name"]), kind)
        pic = ""
        if stem:
            r = lambda sfx: rel_to(str(Path(str(stem) + sfx).relative_to(ROOT).as_posix()), page_rel)
            ds = lambda ext: ", ".join("%s %dw" % (r("-%dx.%s" % (i, ext)), w) for i, w in enumerate(DESK, 1))
            ms = lambda ext: ", ".join("%s %dw" % (r("-mob-%dx.%s" % (i, ext)), w) for i, w in enumerate(MOB, 1))
            pic = ('<picture><source type="image/webp" media="(min-width:769px)" srcset="%s" sizes="227px">'
                   '<source media="(min-width:769px)" srcset="%s" sizes="227px">'
                   '<source type="image/webp" srcset="%s" sizes="104px">'
                   '<img src="%s" srcset="%s" sizes="104px" alt="%s" loading="lazy" width="%d" height="%d"></picture>'
                   % (ds("webp"), ds("jpg"), ms("webp"), r("-mob-2x.jpg"), ms("jpg"), html.escape(e["title"]), MOB[1], round(MOB[1] / 1.5)))
        cards.append('<a class="xm-c" href="%s" data-xm="%s" data-rel="%s">%s%s<span class="xm-t">%s</span><span class="xm-d">%s</span><span class="xm-m">%d min read</span></a>'
                     % (href, how, html.escape(e["rel"]), pic, k, html.escape(e["title"]), html.escape(e["desc"]), e["mins"]))
    return ('<section class="xm" id="explore-more"><h2 class="xm-h">Explore More</h2><div class="xm-g">'
            + "".join(cards) + '</div></section><!-- /explore-more -->')


def slot_info(rel, cat, pins=None):
    """the three slots as the editor's panel shows them"""
    out = []
    for e, how in picks(rel, cat, pins):
        key = "%s-%s" % (e["country"], Path(e["rel"]).stem)
        thumb = "Images/web/explore/%s-1x.jpg" % key
        out.append({"rel": e["rel"], "title": e["title"], "kind": e["kind"], "label": KIND_LABEL[e["kind"]], "country": e["name"],
                    "how": how, "thumb": thumb if (ROOT / thumb).is_file() else e["img"], "draft": e["draft"]})
    return out


def set_pins(rel, slots):
    """slots: [target rel | None] x3; all None clears the page's entry"""
    pins = load_pins()
    slots = [(t if t and t != rel else None) for t in (list(slots) + [None] * 3)[:3]]
    if any(slots):
        pins[rel] = slots
    else:
        pins.pop(rel, None)
    save_pins(pins)
    return pins


def anchor(s):
    """where the block goes: inside .article-body (articles), after the last .fn-section (field notes), or as the
    last child of .artbody before the Updated line (the itinerary template). Returns the insert offset and the
    class the section needs there."""
    for cls, extra in (("article-body", ""), ("artbody", " measure")):
        m = re.search(r'<div class="%s[" ]' % cls, s)
        if m:
            depth, i = 0, m.start()
            for t in re.finditer(r"<(/?)div\b[^>]*>", s[m.start():]):
                depth += -1 if t.group(1) else 1
                if depth == 0:
                    return m.start() + t.start(), extra
    secs = list(re.finditer(r'<div class="fn-section"', s))
    if secs:
        last = secs[-1]
        depth = 0
        for t in re.finditer(r"<(/?)div\b[^>]*>", s[last.start():]):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                return last.start() + t.end(), ""
    return None, ""


def apply_page(rel, cat, pins, dry=False):
    p = ROOT / rel
    with open(p, encoding="utf-8", newline="") as fh:      # keep the page's own line endings (a mixed CRLF/LF file lost its CRLFs, 2026-10-24)
        s = fh.read()
    nl = "\r\n" if "\r\n" in s else "\n"
    s = BLOCK_RE.sub("", s)
    chosen = picks(rel, cat, pins)
    at, extra = anchor(s)
    if at is None or not chosen:
        return chosen, "SKIPPED: no place for the block" if at is None else "SKIPPED: nothing to point to"
    if extra:
        # the template puts "Last updated" after .artbody closes, which left it under the cards; move it in as the
        # column's last line so the page ends like every article: text, the block, the footer (Kevin, 2026-10-24:
        # "have all articles conform to the gyumri draft")
        u = UPDATED_RE.search(s, at)
        if u and not s[at:u.start()].replace("</div>", "", 1).strip():
            line = u.group(0).strip()
            s = s[:at] + s[at:u.start()] + s[u.end():]
            s = s[:at] + "  " + line + nl + s[at:]
            at, extra = anchor(s)
    blk = block_html(rel, chosen, dry)
    if extra:
        blk = blk.replace('<section class="xm"', '<section class="xm%s"' % extra, 1)
    line_start = s.rfind("\n", 0, at) + 1
    indent = re.match(r"[ \t]*", s[line_start:at]).group(0)
    s = s[:at] + nl + indent + blk + nl + s[at:] if s[at - 1:at] not in ("\n", "\r") else s[:at] + indent + blk + nl + s[at:]
    if not dry:
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(s)
    return chosen, "ok"


def live_pages():
    out = []
    for country in COUNTRIES:
        folder = ROOT / country
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.html")):
            if p.name != "index.html" and published(p):
                out.append(p.relative_to(ROOT).as_posix())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true", help="print the picks as JSON")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cat = catalog()
    pins = load_pins()
    pages = [p.replace("\\", "/") for p in a.pages] or live_pages()
    report = {}
    for rel in pages:
        chosen, what = apply_page(rel, cat, pins, a.dry_run)
        report[rel] = {"status": what, "slots": [{"rel": e["rel"], "title": e["title"], "kind": e["kind"], "country": e["name"], "how": how} for e, how in chosen]}
        if not a.report:
            print("%-45s %s" % (rel, what if what != "ok" else " | ".join("%s%s" % (e["title"][:38], "*" if how == "pin" else "") for e, how in chosen)))
    if a.report:
        print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
