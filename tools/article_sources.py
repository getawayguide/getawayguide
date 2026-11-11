#!/usr/bin/env python3
"""What Kevin's own record says about the place an article covers (draft quality program, item 5).

    python tools/article_sources.py <article> [--full] [--out file]

<article> is a path ("Drafts/Upcoming/north-macedonia/ohrid.html"), a slug ("ohrid") or "country/slug"
("armenia/field-notes"). Writes .tmp/kevin_reviewer/sources/<slug>.md: where and when his photos place him, plus the
dated journal entries from those days. Read-only: Images/ and the journal are never written; the only cache touched is
fill_slots' own under .tmp/fill_slots/.

How it stays on the right trip. Place names repeat (Istanbul is a layover on three trips, "Santa Ana" is also in
California, "Nice" is how he opens a sentence), so a journal entry needs two things to get in: its heading names the
place AND the date falls where his photos have him in that country. EXIF decides the year, never the album name
(the Guatemala album says 2026; the trip was 2025). Entries that name the place but fail the date test are listed at the
end of the pack as left out, so a reader can see what was dropped.

City guide: full entries, cut to the paragraphs about this place on a day that covers several places.
Itinerary, top 10, field notes: the trip's route and a short digest per day (full text with --full, or see the city packs).
"""
import argparse
import html as _html
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
OUT = ROOT / ".tmp" / "kevin_reviewer" / "sources"
JOURNALS = [Path.home() / "Projects" / "Travel Journals" / "Travel Journals.html",
            Path.home() / "OneDrive" / "Documents" / "Travel Journals" / "Travel Journals.html"]
BACKUP = Path.home() / "Backup"

# slug words that say what kind of article it is, not where it is
STOP = set("""the and for with from into day days trip trips tour tours hike hikes guide guides top lake blue cruise
observatory monastery monasteries waterfalls waterfall canyon national park island islands north south east west
beach beaches city town old new san santa santo saint las los del de el la le road route crossing""".split())

# countries whose albums aren't named after them. "mixed" albums cover several countries, so only the dates the
# article's own photos come from are used
ALBUMS = {
    "turkiye": ["Turkey"], "netherlands": ["Amsterdam"], "indonesia": ["Bali"], "denmark": ["Copenhagen"],
    "france": ["French Riviera", "Normandy"], "germany": ["Germany", "Cologne", "Munich"],
    "belgium": ["Belgium", "Tomorrowland"], "spain": ["Ibiza", "Morocco, Portugal", "Portugal + Barcelona"],
    "portugal": ["Morocco, Portugal", "Portugal + Barcelona"], "morocco": ["Morocco, Portugal"],
    "chile": ["Patagonia", "Chile"], "argentina": ["Argentina", "Patagonia"], "paraguay": ["Argentina & Paraguay"],
    "thailand": ["Thailand"], "vietnam": ["Vietnam"], "united-kingdom": ["London"], "england": ["London"],
}
MIXED = re.compile(r"[,&+]|Patagonia", re.I)
MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
CITY_WORDS = 5000      # a city pack's journal text, in words, before long entries are cut
DIGEST_DAY = 90        # words per day in an overview's digest
TOURISH = re.compile(r"\b(?:tour|sailing|boat|cruise|hike|trek|trail|way|ferry)\b", re.I)     # a heading that names an outing, not a place
SENT = re.compile(r"(?<![Ss]t\.)(?<=[.!?])\s+")


def fold(s):
    """lowercase, no accents, straight apostrophes: Ölüdeniz and oludeniz are the same word"""
    s = s.replace("’", "'")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def has(name, text_folded):
    return re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", text_folded) is not None


def mentions(names, text_folded):
    return any(has(n, text_folded) for n in names)


def resolve(arg):
    p = Path(arg)
    if not p.is_absolute():
        p = ROOT / arg
    if p.is_file():
        return p
    import draft_paths as dp
    stem = arg.replace("\\", "/").removesuffix(".html")
    country, _, page = stem.rpartition("/")
    hits = list(dp.draft_pages(country or "*", page + ".html"))
    if not hits:
        hits = [q for q in ROOT.glob("%s/%s.html" % (country or "*", page))
                if q.parts[-2] not in ("tools", "Drafts", "archive")]
    if len(hits) != 1:
        sys.exit("article_sources: %d pages match %r%s" % (len(hits), arg, ": " + ", ".join(
            str(h.relative_to(ROOT)) for h in hits[:8]) if hits else ""))
    return hits[0]


def describe(path):
    """(rel, country, stem, kind, slug)"""
    import draft_paths as dp
    rel = path.relative_to(ROOT).as_posix()
    country = dp.country_of(rel) if rel.startswith("Drafts/") else rel.split("/")[0]
    stem = path.stem
    kind = ("top-10" if stem.startswith("top-10") else "itinerary" if "itinerary" in stem
            else "field-notes" if stem == "field-notes" else "city-guide")
    slug = "%s-%s" % (country, stem) if stem in ("field-notes", "index") else stem
    return rel, country, stem, kind, slug


# ------------------------------------------------------------------------------------------------ the article
def article_body(h):
    i = max(h.find('class="article-body"'), h.find('class="artbody"'))
    return h[i:] if i >= 0 else h


def headings(h):
    return [_html.unescape(re.sub(r"<[^>]+>", "", m)).strip()
            for m in re.findall(r"<h[23]\b[^>]*>(.*?)</h[23]>", article_body(h), re.S)]


def article_photo_files(h):
    """originals behind the article's own photos, read-only: (stem, Path or None)"""
    import urllib.parse
    seen, out = set(), []
    for src in re.findall(r'Images/(?!web/)[^"\'\s>,]+\.(?:jpe?g|png|heic)', h, re.I):
        rel = urllib.parse.unquote(src)
        if rel not in seen:
            seen.add(rel)
            q = ROOT / rel
            out.append((Path(rel).stem, q if q.is_file() else None))
    for src in re.findall(r'Images/web/[^"\'\s>,]+?-(?:mob-)?\dx\.(?:jpg|webp)', h, re.I):
        rel = urllib.parse.unquote(src)
        base = re.sub(r"-(?:mob-)?\dx$", "", Path(rel).stem)
        key = str(Path(rel).parent) + "/" + base
        if key in seen:
            continue
        seen.add(key)
        parent = Path("Images") / Path(rel).parent.relative_to("Images/web")
        orig = next((ROOT / parent / (base + e) for e in (".jpg", ".jpeg", ".JPG", ".png")
                     if (ROOT / parent / (base + e)).is_file()), None)
        out.append((base, orig))
    return out


def article_dates(h, album_rows):
    """Counter of the days the article's own photos were taken (EXIF of the original, else the album row by file number)"""
    import fill_slots as fs
    import photo_geo
    by_key = defaultdict(set)
    for r in album_rows:
        by_key[fs._key(Path(r["name"]).stem)].add(r["day"])
    c = Counter()
    for stem, q in article_photo_files(h):
        d = None
        if q is not None:
            try:
                d = (photo_geo.exif_geo(q)[2] or "")[:10] or None
            except Exception:
                d = None
        if not d:
            days = by_key.get(fs._key(stem), set())
            d = next(iter(days)) if len(days) == 1 else None
        if d:
            c[d] += 1
    return c


# ------------------------------------------------------------------------------------------------ the photos
def country_albums(country):
    wanted = ALBUMS.get(country) or [country.replace("-", " ")]
    return [d.name for d in sorted(BACKUP.iterdir())
            if d.is_dir() and re.search(r"\(\d{4}\)$", d.name) and any(w.lower() in d.name.lower() for w in wanted)]


def to_date(s):
    y, m, d = s.split(":")[:3]
    return date(int(y), int(m), int(d[:2]))


def key_of(d):
    return d.strftime("%Y:%m:%d")


def load_rows(albums):
    import fill_slots as fs
    rows = []
    for a in albums:
        for r in fs.photos(a):
            if r.get("time"):
                r["album"] = a
                r["day"] = r["time"][:10]
                rows.append(r)
    return rows


# ------------------------------------------------------------------------------------------------ the journal
def clean(s):
    return _html.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ").strip()


def load_journal():
    f = next((p for p in JOURNALS if p.is_file()), None)
    if not f:
        sys.exit("article_sources: journal not found in " + ", ".join(map(str, JOURNALS)))
    h = open(f, encoding="utf-8").read()
    trips = {m.group(1): clean(m.group(2)) for m in re.finditer(r'<h2 class="trip-h" id="(t\d+)">(.*?)</h2>', h, re.S)}
    entries = []
    starts = list(re.finditer(r'<div class="entry" id="(\w+)"><div class="when">(.*?)</div><div class="where">(.*?)</div>', h, re.S))
    for k, m in enumerate(starts):
        end = starts[k + 1].start() if k + 1 < len(starts) else h.find("</section>", m.end())
        body = h[m.end():end]
        d = re.search(r"(\d{1,2}) (\w{3}) (\d{4})", clean(m.group(2)))
        if not d or d.group(2) not in MONTHS:
            continue
        blocks = [(t, clean(x)) for t, x in re.findall(r"<(p|li)\b[^>]*>(.*?)</\1>", body, re.S)]
        entries.append({"id": m.group(1), "trip": trips.get(m.group(1).split("l")[0], "?"), "when": clean(m.group(2)),
                        "date": date(int(d.group(3)), MONTHS[d.group(2)], int(d.group(1))), "where": clean(m.group(3)),
                        "blocks": [(t, x) for t, x in blocks if x]})
    return entries


def segments(where):
    return [s.strip() for s in re.split(r"\s*(?:/|,|->|→|&|\bto\b)\s*", where) if s.strip()]


def seg_names(seg):
    """the words a paragraph would use for this segment: the whole thing, and its first word if that's a real name"""
    f = fold(seg)
    first = f.split()[0] if f.split() else ""
    return [f] + ([first] if len(first) >= 4 and first not in STOP else [])


def primary_names(stem):
    whole = fold(stem.replace("-", " "))
    words = [w for w in whole.split() if len(w) >= 3 and w not in STOP and not w.isdigit()]
    return ([whole] if words or len(whole.split()) == 1 else []) + words


def keep_blocks(entry, primary, secondary):
    """on a day that covers several places, the paragraphs about this one. The heading's places run in the order he went,
    so the day only moves forward: a paragraph that names a later place and not the current one moves it on. (Naming Ohrid
    while already in Krushevo, "the opening of the Ohrid Airport", doesn't take the day back.)"""
    segs = segments(entry["where"])
    is_here = [any(has(n, fold(s)) or has(fold(s), n) for n in primary + secondary) for s in segs]
    if len(segs) < 2 or not primary or not any(is_here):
        return entry["blocks"], []  # one place, or no name to split on
    i, kept, dropped, reached = 0, [], [], is_here[0]
    last_here = max(k for k, v in enumerate(is_here) if v)
    for t, x in entry["blocks"]:
        f = fold(x)
        said = [k for k, s in enumerate(segs) if k > i and mentions(seg_names(s), f)]
        if said and not mentions(seg_names(segs[i]), f):
            i = said[0]
        reached = reached or is_here[i]
        if is_here[i] or (i < last_here and mentions(primary, f)):
            kept.append((t, x))
        else:
            dropped.append((t, x))
    # he never named the place in a paragraph ("Fethiye / Skopje", then "my Airbnb"), so the day can't be split: all of it.
    # None: he left first thing and nothing in the entry is about this place
    if not reached:
        return None, []
    return kept, dropped


def sec_for(entry, primary, heads):
    """other places on this day that the article itself has a heading for (Krushevo's "A Day Trip to Prilep")"""
    return [fold(s) for s in segments(entry["where"]) if not mentions(primary, fold(s))
            and any(any(has(n, x) for n in seg_names(s)) for x in heads)]


def render(blocks):
    return "\n\n".join(("- " + x) if t == "li" else x for t, x in blocks)


def first_sentences(blocks, limit):
    out, n = [], 0
    for _t, x in blocks:
        s = SENT.split(x)[0]
        w = s.split()
        if len(w) > 30:
            s = " ".join(w[:30]) + " ..."
            w = w[:30]
        if n + len(w) > limit:
            break
        out.append(s)
        n += len(w)
    return " ".join(out)


# ------------------------------------------------------------------------------------------------ the pack
def fmt_day(d):
    return d.strftime("%a ") + str(d.day) + d.strftime(" %b %Y")


def day_label(e):
    return e["when"].replace("\xb7", "-")


def day_table(days, where_by_date):
    lines = ["| date | journal says | photos (first to last) | starred | where the photos place him |", "|---|---|---|---|---|"]
    for d in sorted(days):
        rows = days[d]
        ts = sorted(r["time"][11:16] for r in rows)
        c = Counter(r["place"] for r in rows if r.get("place") and not r["place"].startswith("Unplaced"))
        top = ", ".join("%s %d" % (p.replace("Elsewhere in ", "around ").replace("Near ", "near "), n) for p, n in c.most_common(4)) or "(no GPS)"
        lines.append("| %s | %s | %d (%s to %s) | %d | %s |" % (fmt_day(to_date(d)), where_by_date.get(to_date(d), "(no entry)"),
                     len(rows), ts[0], ts[-1], sum(1 for r in rows if r.get("picked")), top))
    return lines


def route(rows):
    """the trip as a run of cities: [city, first day, last day]"""
    by_day = defaultdict(Counter)
    for r in rows:
        if r.get("city") and r["city"] != "Other":
            by_day[r["day"]][r["city"]] += 1
    runs = []
    for d in sorted(by_day):
        c = by_day[d].most_common(1)[0][0]
        if runs and runs[-1][0] == c:
            runs[-1][2] = d
        else:
            runs.append([c, d, d])
    return runs


def build(arg, full=False):
    path = resolve(arg)
    rel, country, stem, kind, slug = describe(path)
    h = open(path, encoding="utf-8", errors="replace").read()
    overview = kind != "city-guide"
    albums = country_albums(country)
    rows = load_rows(albums)
    own = article_dates(h, rows)
    own_days = {to_date(d) for d in own}
    near_own = {d + timedelta(days=i) for d in own_days for i in (-1, 0, 1)}
    flags = []
    # a mixed album only counts on the days the article's own photos come from
    if any(MIXED.search(a) for a in albums):
        if own_days:
            rows = [r for r in rows if not MIXED.search(r["album"]) or to_date(r["day"]) in near_own]
        else:
            flags.append("This country shares an album with another country and the article has no dated photos yet, "
                         "so the photos below may include the other country.")
    # a handful of photos inside a couple of minutes on a day he wasn't there: a re-save, not a day of the trip
    stray_days = set()
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r["time"][11:16])
    for d, ts in by_day.items():
        if len(ts) <= 5 and len(set(ts)) <= 2:
            stray_days.add(d)
    if stray_days:
        rows = [r for r in rows if r["day"] not in stray_days]
    windows = []
    for a in albums:
        ds = sorted(to_date(r["day"]) for r in rows if r["album"] == a)
        if ds:
            windows.append((ds[0], ds[-1]))

    def in_window(d):
        return any(a - timedelta(days=1) <= d <= b + timedelta(days=1) for a, b in windows)

    entries = load_journal()
    primary = [] if overview else primary_names(stem)
    heads = [fold(x) for x in headings(h)]
    if overview or not primary:
        place_rows = rows if overview else [r for r in rows if own[r["day"]] >= 2]
    else:
        # the city first; a place name only when no city matches ("St. Clement of Ohrid" is a cathedral in Skopje)
        place_rows = ([r for r in rows if mentions(primary, fold(r.get("city") or ""))]
                      or [r for r in rows if mentions(primary, fold(r.get("place") or ""))]
                      or [r for r in rows if own[r["day"]] >= 2])     # an old album with no GPS: the days of the article's own photos
    place_days = {to_date(r["day"]) for r in place_rows}
    near_place = {d + timedelta(days=i) for d in place_days | own_days for i in (-1, 0, 1)}
    cities = {fold(r["city"]) for r in rows if r.get("city") and r["city"] != "Other"}
    others = {fold(r["city"]) for r in rows if r.get("city") and r["city"] != "Other"} - set(primary)
    for p in path.parent.glob("*.html"):
        if p.stem not in ("index", "field-notes", stem) and not p.stem.startswith("top-10") and "itinerary" not in p.stem:
            others.add(fold(p.stem.replace("-", " ")))
    busy = Counter(r["day"] for r in rows)

    accepted, excluded = [], []
    for e in entries:
        segs = segments(e["where"])
        named = bool(primary) and any(mentions(primary, fold(s)) for s in segs)
        d, dk = e["date"], key_of(e["date"])
        if overview:
            if (d in place_days and busy[dk] >= 3) or own[dk] >= 2:
                accepted.append((e, "photos"))
            elif in_window(d) and any(has(o, fold(s)) for o in cities for s in segs):
                accepted.append((e, "photos"))      # the last morning: journal names the city, the album's photos stopped the day before
        elif named:
            if d in near_place:
                accepted.append((e, "photos"))
            elif windows and in_window(d):
                accepted.append((e, "window"))
            elif not windows:
                accepted.append((e, "no album"))
            else:
                excluded.append(e)
        elif own[dk] >= 2 and (not primary or TOURISH.search(e["where"])) and any(not any(has(o, fold(s)) or has(fold(s), o) for o in others) for s in segs):
            accepted.append((e, "own photos"))     # "Sailing Tour Day 2": no place name, but the article's photos are from this day

    if not primary and not overview and not own_days:
        flags.append("The slug names no place and the article has no dated photos, so no journal entries could be matched.")

    where_by_date = {e["date"]: e["where"] for e in entries}
    days = defaultdict(list)
    for r in place_rows:
        days[r["day"]].append(r)

    label = {"city-guide": "city guide", "itinerary": "itinerary", "top-10": "top 10", "field-notes": "field notes"}[kind]
    L = ["# Source pack: %s (%s, %s)" % (slug, label, country.replace("-", " ").title()), "",
         "Built by tools/article_sources.py from `%s`." % rel,
         "Kevin's own record of this place. Facts, order, names and prices come from here; do not invent any. Where this and his",
         "field notes disagree, the journal wins on raw events and numbers, the field notes win on opinions and verdicts. The",
         "journal is private: use it for facts and human detail, never repeat what it says about people's private lives.", ""]
    L += ["**Note:** " + f + "\n" for f in flags]

    all_days = sorted(days)
    L += ["## Where and when", ""]
    if all_days:
        L += ["Albums: %s." % ", ".join(albums),
              "Photos here: %d on %d days, %s to %s (EXIF capture time, local to the camera)." % (
                  sum(len(v) for v in days.values()), len(all_days), fmt_day(to_date(all_days[0])), fmt_day(to_date(all_days[-1]))), ""]
        if overview:
            L += ["Route as his photos show it (one line per stay, by the city most photos were taken in):", ""]
            L += ["- %s: %s%s" % (c, fmt_day(to_date(a)), "" if a == b else " to " + fmt_day(to_date(b))) for c, a, b in route(rows)] + [""]
        L += day_table(days, where_by_date) + [""]
        if stray_days:
            L += ["Ignored as re-saves, not days of the trip: %s." % ", ".join(fmt_day(to_date(d)) for d in sorted(stray_days)), ""]
    else:
        L += ["No geotagged photos match this place%s." % ("" if albums else " (no album found for this country)"), ""]

    L += ["## Journal", ""]
    if not accepted:
        L += ["No journal entry matched. Field notes and the article itself are the only record.", ""]
    trips = Counter(e["trip"] + ", " + str(e["date"].year) for e, _ in accepted)
    if trips:
        L += ["Trip(s): " + "; ".join(trips), ""]
    if overview and not full and accepted:
        L += ["Digest only: the first sentences of each day. Full text is in the city guide packs, or rerun with --full.", ""]
    total = sum(len(x.split()) for e, _ in accepted for _t, x in e["blocks"])
    per = max(200, CITY_WORDS // max(1, len(accepted))) if total > CITY_WORDS and not full else 10 ** 9
    for e, why in accepted:
        if not overview and keep_blocks(e, primary, sec_for(e, primary, heads))[0] == []:
            continue                            # the day he left: a flight line and nothing about this place
        L += ["### %s: %s  (%s)" % (day_label(e), e["where"], e["trip"]), ""]
        if why == "window":
            L += ["*(Heading names the place; no photo that day confirms it, but it falls inside the trip.)*", ""]
        elif why == "no album":
            L += ["*(Heading names the place; no album for this country to confirm the date.)*", ""]
        if overview and not full:
            L += [first_sentences(e["blocks"], DIGEST_DAY) or "(empty entry)", ""]
            continue
        keep, dropped = keep_blocks(e, primary, sec_for(e, primary, heads))
        if keep is None:
            keep = e["blocks"]
            L += ["*(The day covers several places and he never names this one in a paragraph, so the whole day is here.)*", ""]
        n, cut, shown = 0, 0, []
        for t, x in keep:
            w = len(x.split())
            if n + w > per and shown:
                cut += w
                continue
            shown.append((t, x))
            n += w
        L += [render(shown), ""]
        notes = []
        if dropped:
            notes.append("%d paragraphs about other places on this day left out" % len(dropped))
        if cut:
            notes.append("%d words cut for length" % cut)
        if notes:
            L += ["*(%s; journal entry %s.)*" % ("; ".join(notes), e["id"]), ""]

    # the same trip, other days: one sentence each
    if primary and accepted:
        got = {e["id"] for e, _ in accepted}
        trip_ids = {e["id"].split("l")[0] for e, _ in accepted}
        extra = []
        for e in entries:
            if e["id"] in got or e["id"].split("l")[0] not in trip_ids or not in_window(e["date"]):
                continue
            for _t, x in e["blocks"]:
                extra += [(e, s) for s in SENT.split(x) if mentions(primary, fold(s))]
        if extra:
            L += ["## Mentioned on other days of the trip", ""]
            L += ["- %s (%s): %s" % (day_label(e), e["where"], " ".join(s.split()[:45])) for e, s in extra[:8]] + [""]
    if excluded:
        L += ["## Left out (the name matches, the dates don't)", ""]
        L += ["- %s: %s (%s). No photos from this country then." % (day_label(e), e["where"], e["trip"]) for e in excluded] + [""]

    fn = ROOT / country / "field-notes.html"
    if fn.is_file() and kind != "field-notes":
        L += ["Also check his field notes: `%s/field-notes.html`." % country, ""]
    return slug, "\n".join(L), {"entries": len(accepted), "excluded": len(excluded), "days": len(all_days), "kind": kind}


def main():
    ap = argparse.ArgumentParser(description="Assemble what Kevin's journal and photos say about an article's place.")
    ap.add_argument("article")
    ap.add_argument("--full", action="store_true", help="full journal text, no digest or cuts")
    ap.add_argument("--out", help="write here instead of .tmp/kevin_reviewer/sources/<slug>.md")
    a = ap.parse_args()
    slug, text, info = build(a.article, a.full)
    target = Path(a.out) if a.out else OUT / (slug + ".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    sys.stdout.buffer.write(("%s: %s, %d journal entries, %d photo days, %d left out, %d words\n" % (
        target, info["kind"], info["entries"], info["days"], info["excluded"], len(text.split()))).encode("utf-8"))


if __name__ == "__main__":
    main()
