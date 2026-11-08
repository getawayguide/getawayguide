"""Make every article-link card (`a.gb-card`) show the linked article's own thumbnail (Kevin, 2026-11-07, Skopje:
"why is the photo in the link above not the Kosovo thumbnail? it should be that way for all of these items").

The thumbnail is the one the site already shows for that article: its card on the country page (`index.html` next to
it, the same source tools/guide_cards.py uses), else its card on posts.html (field notes, articles without a country
page, e.g. Kosovo). The photo is cut with guide_cards.cut (square-ish desktop tiers + 16:9 phone tiers, ICC kept) into
Images/web/<Country>/card-<slug>-{1x..3x, mob-1x..3x}, and only the card's <picture> is rewritten; title and text stay.

    python tools/card_thumbs.py --dry-run                 # report every card: OK / WRONG / NO THUMBNAIL
    python tools/card_thumbs.py                           # fix the WRONG ones (drafts + live pages with gb-cards)
    python tools/card_thumbs.py Drafts/Upcoming/north-macedonia/skopje.html
"""
import argparse, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import guide_cards as gc

ROOT = gc.ROOT
CARD = re.compile(r'<a class="gb-card" href="([^"]+)"[^>]*>(<picture>.*?</picture>)', re.S)
POSTS_RE = r'<a href="{href}"><div class="card-img bg" style="background-image:url\(\'([^\']+)\'\)[^"]*?background-position:\s*([\d.]+)%\s*([\d.]+)%'


def live_rel(target):
    """the article's path relative to the site root (Drafts/Upcoming/<c>/x.html -> <c>/x.html)"""
    p = target.resolve().relative_to(ROOT).as_posix()
    m = re.match(r"Drafts/(?:Upcoming|Full Articles)/(.+)", p)
    return m.group(1) if m else p


def country_cards(folder):
    """{file: (image url relative to the folder, y focus %)} from index.html's country-article-cards, any image type
    (guide_cards.country_cards reads .jpg only, so El Salvador's .webp cards were invisible to it)"""
    idx = folder / "index.html"
    out = {}
    if not idx.is_file():
        return out
    for m in re.finditer(r"""<div class="country-article-card" onclick="location\.href='([^']+)'" style="([^"]*)\"""", idx.read_text(encoding="utf-8")):
        u = re.search(r"url\('([^']+\.(?:jpg|webp))'\)", m.group(2))
        pos = re.search(r"background-position:\s*(?:center|[\d.]+%)\s+([\d.]+)%", m.group(2))
        if u:
            out[m.group(1)] = (u.group(1), float(pos.group(1)) if pos else 50.0)
    # cards whose photo comes from a styles.css class (El Salvador: <a href="el-tunco.html" class="country-article-card img-eltunco">)
    css = (ROOT / "styles.css").read_text(encoding="utf-8")
    page = idx.read_text(encoding="utf-8")
    for m in re.finditer(r'<a href="([^"]+\.html)" class="country-article-card (img-[\w-]+)"', page):
        if m.group(1) in out:
            continue
        rule = re.search(r"\." + re.escape(m.group(2)) + r"\{([^}]*)\}", css)
        if not rule:
            continue
        u = re.search(r"url\('([^']+\.(?:jpg|webp))'\)", rule.group(1))
        pos = re.search(r"background-position:\s*(?:center|[\d.]+%)\s+([\d.]+)%", rule.group(1))
        if u:   # styles.css urls are relative to the site root; make them relative to the folder
            out[m.group(1)] = (os.path.relpath(ROOT / u.group(1), folder).replace(os.sep, "/"), float(pos.group(1)) if pos else 50.0)
    return out


def thumbnail(target):
    """(absolute jpg path, y focus %) of the article's thumbnail, or None"""
    cc = country_cards(target.parent)
    if target.name in cc:
        url, y = cc[target.name]
        return (target.parent / url).resolve(), y
    lr = live_rel(target)
    live_idx = ROOT / Path(lr).parent
    cc = country_cards(live_idx) if live_idx != target.parent else {}
    if Path(lr).name in cc:
        url, y = cc[Path(lr).name]
        return (live_idx / url).resolve(), y
    posts = (ROOT / "posts.html").read_text(encoding="utf-8")
    m = re.search(POSTS_RE.format(href=re.escape(lr)), posts)
    if m:
        return (ROOT / m.group(1)).resolve(), float(m.group(3))
    return None


SAME = 14.0   # mean abs difference (0-255) of 48x48 greyscale crops below which the card already shows the thumbnail


def looks_different(pic, page_dir, src, y):
    """compare the card's current desktop 1x file with a fresh crop of the thumbnail; None if unreadable"""
    from PIL import Image, ImageOps, ImageChops, ImageStat
    m = re.search(r'srcset="([^" ]+\.jpg) 311w', pic) or re.search(r'srcset="([^" ]+\.jpg)', pic)
    if not m:
        return None
    cur = (page_dir / m.group(1).replace("%20", " ")).resolve()
    if not cur.is_file():
        return None
    a = ImageOps.exif_transpose(Image.open(cur)).convert("L")
    im = ImageOps.exif_transpose(Image.open(src)).convert("L")
    ratio = a.width / a.height
    W, H = im.size
    if W / H > ratio:
        cw = round(H * ratio); box = ((W - cw) // 2, 0, (W - cw) // 2 + cw, H)
    else:
        ch = round(W / ratio); top = max(0, min(H - ch, round(y / 100 * (H - ch)))); box = (0, top, W, top + ch)
    b = im.crop(box).resize((48, 48)); a = a.resize((48, 48))
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def card_picture(d_stem, m_stem, page_dir, alt):
    # a space in a srcset URL splits it ("North Macedonia"), so the relative paths are %20-encoded first; card_html's
    # relpath then returns them unchanged (page_dir joined to an already-relative, encoded path)
    enc = lambda st: os.path.join(str(page_dir), os.path.relpath(st, page_dir).replace(" ", "%20"))
    full = gc.card_html("x", "t", alt, enc(d_stem), enc(m_stem), page_dir, "")
    return re.search(r"<picture>.*?</picture>", full, re.S).group(0)


def process(page, dry):
    s = page.read_text(encoding="utf-8")
    out, report = s, []
    for m in CARD.finditer(s):
        href, pic = m.group(1), m.group(2)
        target = (page.parent / href.split("#")[0]).resolve()
        th = thumbnail(target) if target.is_file() else None
        if not th or not th[0].is_file():
            report.append((href, "NO THUMBNAIL", "")); continue
        jpg, y = th
        src = gc.best_source(str(jpg))
        slug = live_rel(target).replace("/", "-")[:-5]
        country_dir = jpg.parent if "dest-cards" not in jpg.as_posix() else (ROOT / "Images" / "web" / "dest-cards")
        d_stem = country_dir / f"card-{slug}"
        m_stem = country_dir / f"card-{slug}-mob"
        diff = looks_different(pic, page.parent, src, y)
        if diff is not None and diff < SAME:
            report.append((href, "OK", f"{src.name} (diff {diff:.1f})")); continue
        report.append((href, "WRONG" + (" (dry run)" if dry else " -> fixed"), f"{src.name} @ y {y:g}%"))
        if dry:
            continue
        gc.cut(src, str(d_stem), 1.0 if False else gc_desk_ratio(), gc.DESK, y, False)
        gc.cut(src, str(m_stem), 16 / 9, gc.MOB, y, False)
        alt = re.search(r'alt="([^"]*)"', pic)
        new = card_picture(str(d_stem), str(m_stem), page.parent, alt.group(1) if alt else "")
        out = out.replace(pic, new, 1)
    if out != s and not dry:
        page.write_text(out, encoding="utf-8")
    return report


def gc_desk_ratio():
    """the desktop thumbnail's shape, read from guide_cards so both tools cut alike"""
    src = Path(gc.__file__).read_text(encoding="utf-8")
    m = re.search(r'cut\(src,\s*\S+,\s*([\d./ ]+),\s*DESK', src)
    return eval(m.group(1)) if m else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    pages = [Path(p).resolve() for p in a.pages] or sorted(
        p for p in ROOT.rglob("*.html")
        if "gb-card" in p.read_text(encoding="utf-8", errors="ignore") and not re.search(r"(^|/)(archive|\.tmp|Drafts/\.Archive|node_modules)/", p.relative_to(ROOT).as_posix()) and p.name != "editor.html")
    for p in pages:
        for href, st, info in process(p, a.dry_run):
            print(f"{p.relative_to(ROOT).as_posix()}: {href}: {st} {info}")


if __name__ == "__main__":
    main()
