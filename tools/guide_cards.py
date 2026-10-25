"""Guide cards on field-notes pages (Kevin, 2026-10-23: option 2 of the guide-badge mock-ups).

A field-notes section whose city has grown into its own published guide gets a card right under the city heading:
the guide's cover photo, "In-depth guide", the guide's title from the country page, and

    Want the detailed version? I wrote a full dedicated guide for <City> with maps, photos and the places I went.

A section belongs to a guide when it links to that guide (`meteora.html#varlaam` in the Meteora section), or, when it
links to none, when it is named like it (#athens -> athens.html, #naxos and #ios -> naxos-ios.html). The title
and photo are the ones the country page's card for that guide uses, so the three places never disagree. The photo is
cut into its own small files (square for the 168 px desktop thumbnail, 16:9 for the phone's full-width card) at the
site's tier multiples, with the ICC profile carried through.

Rerunnable: an existing card is replaced, a card whose guide is no longer linked is removed.

    python tools/guide_cards.py                       # every */field-notes.html
    python tools/guide_cards.py greece/field-notes.html --dry-run
"""
import argparse, glob, html, os, re, sys
from pathlib import Path
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
LINE = "Want the detailed version? I wrote a full dedicated guide for {city} with maps, photos and the places I went."
DESK_W = 168                           # the card's thumbnail column (artifact.css .gb-card)
DESK = (311, 470, 622)                 # 1.85x / 2.8x / 3.7x: never ~1.0x or ~2.0x the drawn width (CLAUDE.md)
MOB = (393, 786, 1179)                 # the site's phone tiers
# a section whose guide shares no word with it (Türkiye's "Sailing" notes became the Blue Cruise guide)
ALIASES = {"sailing": "blue-cruise.html"}
CARD_RE = re.compile(r'\n?[ \t]*<a class="gb-card"[^>]*data-guide="([^"]+)"[^>]*>.*?</a><!-- /gb-card -->', re.S)


def published(path):
    try:
        head = path.read_text(encoding="utf-8")[:4000]
    except OSError:
        return False
    return 'http-equiv="refresh"' not in head


def country_cards(folder):
    """{guide file: (title, image url relative to the folder, y focus %)} from the country page's article cards"""
    idx = folder / "index.html"
    out = {}
    if not idx.is_file():
        return out
    s = idx.read_text(encoding="utf-8")
    for m in re.finditer(r'<div class="country-article-card" onclick="location\.href=\'([^\']+)\'" style="([^"]*)"', s):
        href, style = m.group(1), m.group(2)
        t = re.search(r'country-article-card-title">(.*?)</h3>', s[m.end():m.end() + 1500], re.S)
        u = re.search(r"url\('([^']+\.jpg)'\)\s*(?:center|[\d.]+%)?\s*([\d.]+)?%?", style)
        if t and u:
            out[href] = (html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip(), u.group(1), float(u.group(2) or 50))
    return out


def best_source(jpg):
    """the widest derived file of that card image (the 3600w webp beats the 2000 px jpg)"""
    stem = jpg[:-4]
    cands = [Path(p) for p in glob.glob(glob.escape(stem) + "*.webp") + [jpg]]
    cands = [p for p in cands if p.is_file()]
    return max(cands, key=lambda p: Image.open(p).size[0]) if cands else None


def cut(src, out_stem, ratio, widths, yfocus, dry):
    im = ImageOps.exif_transpose(Image.open(src))
    icc = im.info.get("icc_profile")               # grab BEFORE convert(): these are Display P3
    W, H = im.size
    if W / H > ratio:
        cw, ch = round(H * ratio), H
        box = ((W - cw) // 2, 0, (W - cw) // 2 + cw, H)
    else:
        cw, ch = W, round(W / ratio)
        top = max(0, min(H - ch, round(yfocus / 100 * (H - ch))))
        box = (0, top, W, top + ch)
    crop = im.crop(box).convert("RGB")
    names = []
    for i, w in enumerate(widths, 1):
        h = round(w / ratio)
        for ext in ("webp", "jpg"):
            p = Path(f"{out_stem}-{i}x.{ext}")
            names.append(p)
            if not dry:
                small = crop.resize((w, h), Image.LANCZOS)
                kw = {"quality": 84, "icc_profile": icc} if ext == "jpg" else {"quality": 82, "method": 6, "icc_profile": icc}
                if ext == "jpg":
                    kw["optimize"] = True
                small.save(p, **kw)
    return names


def rel(p, page_dir):
    return os.path.relpath(p, page_dir).replace(os.sep, "/")


def card_html(guide, title, city, d_stem, m_stem, page_dir, indent):
    d = lambda i, e: rel(f"{d_stem}-{i}x.{e}", page_dir)
    m = lambda i, e: rel(f"{m_stem}-{i}x.{e}", page_dir)
    ds = lambda e: ", ".join(f"{d(i, e)} {w}w" for i, w in enumerate(DESK, 1))
    ms = lambda e: ", ".join(f"{m(i, e)} {w}w" for i, w in enumerate(MOB, 1))
    return (
        f'\n{indent}<a class="gb-card" href="{guide}" data-guide="{guide}">'
        f'<picture>'
        f'<source type="image/webp" media="(min-width:769px)" srcset="{ds("webp")}" sizes="{DESK_W}px">'
        f'<source media="(min-width:769px)" srcset="{ds("jpg")}" sizes="{DESK_W}px">'
        f'<source type="image/webp" srcset="{ms("webp")}" sizes="calc(100vw - 40px)">'
        f'<img src="{m(2, "jpg")}" srcset="{ms("jpg")}" sizes="calc(100vw - 40px)" alt="{html.escape(city)}" loading="lazy" width="{MOB[1]}" height="{round(MOB[1] * 9 / 16)}">'
        f'</picture>'
        f'<span class="t"><span class="k"><span class="dot"></span>In-depth guide</span>'
        f'<span class="h">{html.escape(title)}</span>'
        f'<span class="d">{html.escape(LINE.format(city=city))}</span>'
        f'<span class="b">Read the guide →</span></span></a><!-- /gb-card -->')


def pick_guide(sec_id, body, candidates):
    """the guide a section belongs to, among `candidates` (file names in the field notes' folder): the one it links
    to most, else the one named like it (#athens -> athens.html, #naxos and #ios -> naxos-ios.html,
    #oludeniz-fethiye -> fethiye-oludeniz.html). A field-notes section often links only Google Maps until its guide
    is out, so the name is what finds Athens on launch day. Itineraries and top-10 lists cover a whole country and
    belong to no one section."""
    links = [l for l in re.findall(r'href="([\w-]+\.html)(?:#[^"]*)?"', body) if l in candidates]
    if links:
        return max(set(links), key=links.count)
    if ALIASES.get(sec_id) in candidates:
        return ALIASES[sec_id]
    parts = set(sec_id.split("-"))
    named = [g for g in candidates if "itinerary" not in g and not g.startswith("top-")
             and (parts <= set(Path(g).stem.split("-")) or set(Path(g).stem.split("-")) <= parts)]
    return named[0] if len(named) == 1 else None


def guide_files(folder):
    """the published pages of a field-notes folder that could be a section's guide"""
    return {p.name for p in Path(folder).glob("*.html") if p.name not in ("field-notes.html", "index.html") and published(p)}


def section_body(s, start):
    """the section's own markup, to its closing div: what follows the last section (the Explore More cards, with
    links to the guides) used to count as part of it"""
    depth = 0
    for t in re.finditer(r"<(/?)div\b[^>]*>", s[start:]):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return s[start:start + t.end()]
    return s[start:]


def section_guides(page, candidates):
    """[(section id, guide file)] for the field-notes sections that belong to one of `candidates`: the launch check
    passes the live guides plus the drafts launching now, to say which sections get a card"""
    s = Path(page).read_text(encoding="utf-8")
    secs = list(re.finditer(r'<div class="fn-section" id="([^"]+)">', s))
    out = []
    for k, m in enumerate(secs):
        body = section_body(s, m.start())
        g = pick_guide(m.group(1), body, candidates)
        if g:
            out.append((m.group(1), g))
    return out


def has_card(page, guide):
    return ('data-guide="%s"' % guide) in Path(page).read_text(encoding="utf-8")


def process(page, dry):
    page = Path(page).resolve()
    folder = page.parent
    with open(page, encoding="utf-8", newline="") as fh:   # keep the page's own line endings (2026-10-24)
        s = fh.read()
    nl = "\r\n" if "\r\n" in s else "\n"
    cards = country_cards(folder)
    s = CARD_RE.sub("", s)                         # rebuilt from scratch: a stale card never survives
    report = []
    live = guide_files(folder)
    secs = list(re.finditer(r'<div class="fn-section" id="([^"]+)">', s))
    for k in reversed(range(len(secs))):
        m = secs[k]
        body = section_body(s, m.start())
        guide = pick_guide(m.group(1), body, live)
        if not guide:
            continue
        if guide not in cards:
            report.append((m.group(1), guide, "SKIPPED: the country page has no card for it"))
            continue
        title, img_url, yf = cards[guide]
        hd = re.search(r'<div class="fn-city-hd">\s*<h2[^>]*>(.*?)</h2>.*?</div>', body, re.S)
        if not hd:
            report.append((m.group(1), guide, "SKIPPED: no city heading"))
            continue
        city = html.unescape(re.sub(r"<[^>]+>", "", hd.group(1))).strip()
        src = best_source(str((folder / img_url).resolve()))
        if not src:
            report.append((m.group(1), guide, "SKIPPED: card photo not found: " + img_url))
            continue
        stem = src.parent / ("guide-card-" + Path(guide).stem)
        cut(src, f"{stem}", 1.0, DESK, yf, dry)
        cut(src, f"{stem}-mob", 16 / 9, MOB, yf, dry)
        hd_line = s[s.rfind("\n", 0, m.start() + hd.start()) + 1:]
        indent = re.match(r"[ \t]*", hd_line).group(0) or "    "      # the heading row's own indent
        block = card_html(guide, title, city, stem, f"{stem}-mob", folder, indent)
        at = m.start() + hd.end()
        s = s[:at] + block.replace("\n", nl) + s[at:]
        report.append((m.group(1), guide, "card: " + title))
    if not dry:
        with open(page, "w", encoding="utf-8", newline="") as f:
            f.write(s)
    return list(reversed(report))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    pages = a.pages or sorted(glob.glob(str(ROOT / "*" / "field-notes.html")))
    out = sys.stdout.buffer
    for p in pages:
        if not published(Path(p)):
            continue
        rep = process(p, a.dry_run)
        if rep:
            out.write(f"{os.path.relpath(p, ROOT)}\n".encode())
            for sec, guide, what in rep:
                out.write(f"  #{sec:<14} -> {guide:<22} {what}\n".encode("utf-8"))


if __name__ == "__main__":
    main()
