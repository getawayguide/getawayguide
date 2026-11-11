#!/usr/bin/env python3
"""
gen_og_images.py — generate Open Graph images (1200x630) for social sharing and point
each page's og:image / twitter:image at them.

Covers BOTH:
  - every <slug>/field-notes.html country page (source: Images/dest-cards/<Name>_1.*)
  - the site pages and the El Salvador guide, via PAGE_SRC below

Those non-country pages used to share `og-image.svg`, and NO platform renders an SVG link
preview (Facebook, X, LinkedIn, Slack, Discord, WhatsApp and iMessage all ignore it), so
the homepage and the whole El Salvador guide shared with no image at all. Each site page
now previews as a render OF ITSELF, so About looks like About rather than the homepage.

Prefers the high-res source; falls back to the compressed portrait dest-card (flagged as
lower-res). Output -> Images/web/og/<name>.jpg.

Rendering a page needs network (fonts + the amCharts map), so run it from PowerShell.
Existing images are kept unless --force, so a hand-picked one is never clobbered.

Every live country page PAGE_SRC does not name gets one too (ensure(), below), and the launch tools call
ensure() over a launched country's pages, so a new page never ships without a share image.

Usage: python tools/gen_og_images.py [--dry-run] [--force]
"""
import os, re, glob, sys
from PIL import Image, ImageOps
import thumb_lock  # preserve hand-edited OG images across runs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRY = "--dry-run" in sys.argv
FORCE = "--force" in sys.argv  # regenerate even hand-edited thumbnails
DOMAIN = "https://getawayguide.io"
OG_W, OG_H = 1200, 630

# use a specific dest-card source instead of the default <Name>_1 (e.g. India's best shot is _3)
SRC_OVERRIDE = {"india": "India_3.JPG", "indonesia": "Bali_2.JPG", "australia": "Australia_3.JPG", "estonia": "Estonia_2.JPG", "denmark": "Denmark_3.jpeg"}

def hi_res(slug):
    if slug in SRC_OVERRIDE:
        p = os.path.join(ROOT, "Images", "dest-cards", SRC_OVERRIDE[slug])
        if os.path.exists(p):
            return p
    cands = [slug, slug.replace("-", ""), slug.split("-")[-1]]
    for s in sorted(glob.glob(os.path.join(ROOT, "Images", "dest-cards", "*"))):
        b = os.path.basename(s).lower()
        if any(b.startswith(c + "_") or b.startswith(c + ".") for c in cands):
            return s
    return None

# reuse the curated vertical framing (background-position Y%) from the destinations grid cards
def vbias_for(slug):
    DEST = open(os.path.join(ROOT, "destinations.html"), encoding="utf-8").read()
    m = re.search(r"dest-cards/%s\.jpg'[^\"]*background-position:\s*\d+%%\s+(\d+)%%" % re.escape(slug), DEST)
    return int(m.group(1)) / 100 if m else 0.5

def _cover(im, ar, vbias=0.5):
    """Cover-crop to aspect ratio `ar`, biased vertically like CSS background-position."""
    w, h = im.size
    if w / h > ar:                                   # too wide -> trim the sides
        nw = round(h * ar)
        x = (w - nw) // 2
        return im.crop((x, 0, x + nw, h))
    nh = round(w / ar)                               # too tall -> trim top/bottom
    y = max(0, min(h - nh, round((h - nh) * vbias)))
    return im.crop((0, y, w, y + nh))


def make_og(src, dst, vbias, portrait_ar=None):
    """1200x630 crop matching the mobile dest-card.

    `portrait_ar` replays the card's TWO-step crop: the portrait thumbnail is a centred
    cover-crop of the original, and the mobile card then cover-crops THAT to 1.9:1. Going
    straight from the original to 1.9:1 keeps more of the width, so the preview and the
    card end up framed differently (Colombia, Tanzania, India, Chile, Brazil).
    """
    src_im = ImageOps.exif_transpose(Image.open(src))
    icc = src_im.info.get("icc_profile")             # these photos are Display P3; dropping
    im = src_im.convert("RGB")                       # the profile renders them desaturated
    if portrait_ar:
        im = _cover(im, portrait_ar, 0.5)            # step 1: the portrait thumbnail
    im = _cover(im, OG_W / OG_H, vbias)              # step 2: the landscape card
    im.resize((OG_W, OG_H), Image.LANCZOS).save(dst, format="JPEG", quality=82,
                                                optimize=True, icc_profile=icc)


# ---------------------------------------------------------------------------
# site pages + the El Salvador guide.
# `None` = render a screenshot OF THAT PAGE (so About previews as About, not as the
# homepage) - a site render tells someone what they're about to land on, which a logo
# card can't. Each El Salvador article uses its OWN hero photo for the same reason.
ES = "Images/El Salvador"
PAGE_SRC = {
    "index.html": None, "destinations.html": None, "posts.html": None,
    "about.html": None, "contact.html": None, "privacy.html": None, "resources.html": None,
    "el-salvador/index.html":                 "%s/Santa Ana/Volcano de Santa Ana Horizontal.jpg" % ES,
    "el-salvador/santa-ana.html":             "%s/Santa Ana/Volcano de Santa Ana Horizontal.jpg" % ES,
    "el-salvador/ruta-de-las-flores.html":    "%s/La Ruta de las Flores/Ataco/Ataco Church Dome.JPEG" % ES,
    "el-salvador/7-waterfalls-hike.html":     "%s/La Ruta de las Flores/Waterfalls/Main Waterfalls.JPEG" % ES,
    "el-salvador/top-10-el-salvador.html":    "%s/La Ruta de las Flores/Waterfalls/Waterfall Main.JPEG" % ES,
    "el-salvador/el-salvador-itinerary.html": "%s/El Tunco and Taquillo/Taquillo View.JPEG" % ES,
    "el-salvador/el-tunco.html":              "%s/El Tunco and Taquillo/El Tunco/El Tunco Sunset.jpg" % ES,
    # an In-Depth Guide's country page shares the photo its destinations card shows (Armenia, 2026-10-02);
    # its articles' share images are cut by the Covers tool from each article's card photo
    "armenia/index.html":                     "Images/dest-cards/armenia_1.jpg",
    # Greece launched 2026-10-07 with two articles and its field notes kept live: the country page shares its
    # destinations card's photo, each article its card photo (the hero), until Covers cuts dedicated ones
    "greece/index.html":                      "Images/dest-cards/greece_1.jpeg",
    "greece/meteora.html":                    "Images/web/Greece/hero-meteora.jpg",
    "greece/thessaloniki.html":               "Images/web/Greece/hero-thessaloniki-2x.jpg",
    # Kosovo's country page went live 2026-11-08 ahead of its guides (field notes only): its destinations card photo
    "kosovo/index.html":                      "Images/dest-cards/kosovo_1.JPG",
    # North Macedonia launched 2026-11-10 (field notes kept): the country page shares its destinations card's photo
    "north-macedonia/index.html":             "Images/dest-cards/macedonia_1.jpeg",
}


def render_page(page, dst):
    """Screenshot a site page at OG proportions. Needs network (fonts/map CDN)."""
    import asyncio, io

    async def shot():
        from playwright.async_api import async_playwright
        url = "file:///" + os.path.join(ROOT, page).replace("/", os.sep).replace("\\", "/")
        async with async_playwright() as pw:
            br = await pw.chromium.launch()
            pg = await br.new_page(viewport={"width": OG_W, "height": OG_H}, device_scale_factor=2)
            await pg.goto(url, wait_until="networkidle", timeout=90000)
            await pg.wait_for_timeout(3200)
            await pg.evaluate("""() => {
              document.querySelectorAll('.hero-scroll,.nav-hamburger').forEach(e => e.style.display='none');
              window.scrollTo(0, 0);
            }""")
            await pg.wait_for_timeout(400)
            png = await pg.screenshot(clip={"x": 0, "y": 0, "width": OG_W, "height": OG_H})
            await br.close()
            return png
    png = asyncio.run(shot())
    Image.open(io.BytesIO(png)).convert("RGB").resize((OG_W, OG_H), Image.LANCZOS) \
        .save(dst, format="JPEG", quality=86, optimize=True)



# A country's OG image and its MOBILE landscape dest-card are both 1.9:1, so they should
# show the same thing - otherwise the same country is framed two ways depending on where
# the link is seen. dest_card_source.resolve() reports what the card actually renders
# (the right photo, and the mobile background-position, which lives in styles.css and the
# old vbias_for() never read), so the preview is generated to match it.
import dest_card_source


def _og_dir():
    d = os.path.join(ROOT, "Images", "web", "og")
    os.makedirs(d, exist_ok=True)
    return d


def country_source(slug):
    """(source, vbias, how, portrait aspect or None) for a country's card photo, as its mobile dest-card shows it"""
    src, vbias, how = dest_card_source.resolve(slug)
    if not src:
        src = hi_res(slug) or os.path.join(ROOT, "Images", "web", "dest-cards", "%s.jpg" % slug)
        vbias, how = vbias_for(slug), "fallback"
    # replay the portrait step only when starting from a full original — a
    # landscape override or the thumbnail itself has already been through it
    par = None
    if "matched original" in how:
        tp = os.path.join(ROOT, "Images", "web", "dest-cards", "%s.jpg" % slug)
        if os.path.exists(tp):
            tw, th = Image.open(tp).size
            par = tw / th
    return src, vbias, how, par


def point(html, ogurl):
    """the page's og:image / twitter:image name `ogurl`, with og:image:width/height once"""
    new = re.sub(r'(<meta property="og:image" content=")[^"]*(")', lambda m: m.group(1) + ogurl + m.group(2), html)
    new = re.sub(r'(<meta name="twitter:image" content=")[^"]*(")', lambda m: m.group(1) + ogurl + m.group(2), new)
    if 'property="og:image"' not in new:            # a page that never had one: after og:description, or before </head>
        nl = "\r\n" if "\r\n" in html else "\n"
        tag = '<meta property="og:image" content="%s">' % ogurl
        m = next((x for x in (re.search(r'<meta property="og:%s" content="[^"]*">' % k, new)
                              for k in ("description", "url", "title")) if x), None)
        new = (new[:m.end()] + nl + tag + new[m.end():]) if m else new.replace("</head>", tag + nl + "</head>", 1)
    # add width/height once (helps platforms render immediately)
    if 'property="og:image:width"' not in new:
        nl = "\r\n" if "\r\n" in html else "\n"
        new = new.replace('<meta property="og:image" content="%s">' % ogurl,
                          '<meta property="og:image" content="%s">%s'
                          '<meta property="og:image:width" content="%d">%s'
                          '<meta property="og:image:height" content="%d">' % (ogurl, nl, OG_W, nl, OG_H), 1)
    return new


def _card_source(slug, stem):
    """(photo, vbias) of the article's card on its country page: the share image is the card's photo (Kevin)"""
    idx = os.path.join(ROOT, slug, "index.html")
    if not os.path.exists(idx):
        return None
    s = open(idx, encoding="utf-8").read()
    m = re.search(r'<div class="country-article-card" onclick="location\.href=\'%s\.html\'" style="([^"]*)"' % re.escape(stem), s)
    if not m:
        return None
    u = re.search(r"url\('([^']+\.(?:jpe?g|png|webp))'\)", m.group(1), re.I)
    if not u:
        return None
    p = os.path.normpath(os.path.join(ROOT, slug, u.group(1).replace("%20", " ")))
    # the widest derived file of that photo (a card's -3x beats its 1x)
    stem_p = os.path.splitext(p)[0]
    stem_p = re.sub(r"-(?:\d+x|\d+w)$", "", stem_p)
    cands = [c for c in glob.glob(glob.escape(stem_p) + "*.jpg") + glob.glob(glob.escape(stem_p) + "*.webp")
             if os.path.isfile(c) and "-mob" not in os.path.basename(c)]
    if os.path.isfile(p):
        cands.append(p)
    if not cands:
        return None
    best = max(cands, key=lambda c: Image.open(c).size[0])
    pos = re.search(r"background-position:\s*[\d.]+%\s+([\d.]+)%", m.group(1))
    return best, (float(pos.group(1)) / 100 if pos else 0.5)


def has_own(rel, html):
    """True when the page's og:image is a share image of its own that exists. A country page or field notes may share
    the country's image; an article may not (the Launch tab's "cards" check says the same)."""
    m = re.search(r'<meta property="og:image" content="([^"]*)"', html)
    if not m:
        return False
    url = m.group(1)
    pre = DOMAIN + "/Images/web/og/"
    if not url.startswith(pre):
        return False
    name = url[len(pre):]
    if not os.path.isfile(os.path.join(ROOT, "Images", "web", "og", name)):
        return False
    slug, page = rel.split("/", 1)
    if page in ("index.html", "field-notes.html"):
        return True
    return name not in ("%s.jpg" % slug, "og-image.jpg")


def ensure(pages, dry=False):
    """Every page in `pages` (repo-relative, '<slug>/<page>.html') gets a share image if it has none of its own; a page
    that has one is left as it is, file and tags. The launch tools run it over the country's pages (North Macedonia,
    2026-11-10: its country page had no share image until PAGE_SRC was edited by hand).
      field notes   Images/web/og/<slug>.jpg, cut as the country loop cuts it
      country page  Images/web/og/<slug>-index.jpg, from the destinations card's photo (as PAGE_SRC's entries are)
      article       Images/web/og/<slug>-<page>.jpg, from its card photo on the country page (Covers can recut it from
                    the original later: Launch tab, "Cut the share image from the card")
    Returns log lines."""
    log = []
    for rel in pages:
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        if "/" not in rel or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", newline="") as f:
            html = f.read()
        if 'http-equiv="refresh"' in html[:4000]:
            continue
        if has_own(rel, html):
            log.append("%s: has its share image (%s)" % (rel, re.search(r'<meta property="og:image" content="[^"]*/([^"/]*)"', html).group(1)))
            continue
        slug, page = rel.split("/", 1)
        stem = os.path.splitext(page)[0]
        name = "%s.jpg" % slug if page == "field-notes.html" else "%s-%s.jpg" % (slug, stem)
        dst = os.path.join(ROOT, "Images", "web", "og", name)
        what = "existing " + name
        if not os.path.exists(dst):
            if page in ("index.html", "field-notes.html"):
                src, vbias, how, par = country_source(slug)
                what = "%s from the destinations card's photo (%s)" % (name, os.path.basename(src or "-"))
            else:
                cs = _card_source(slug, stem)
                if not cs:
                    log.append("ACTION NEEDED %s: no share image and no card photo on %s/index.html to cut one from "
                               "(set its card in Covers)" % (rel, slug))
                    continue
                src, vbias, par = cs[0], cs[1], None
                what = "%s from its card photo (%s)" % (name, os.path.basename(src))
            if not src or not os.path.exists(src):
                log.append("ACTION NEEDED %s: no share image, and its source photo is missing" % rel)
                continue
            if not dry:
                _og_dir()
                make_og(src, dst, vbias, par); thumb_lock.record(dst)
        new = point(html, "%s/Images/web/og/%s" % (DOMAIN, name))
        if new != html and not dry:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
        log.append("%s: %s share image %s" % (rel, "would get" if dry else "got", what))
    return log


def country_pages_missing_from_page_src():
    """every live country page (<slug>/index.html with article cards) that PAGE_SRC does not name"""
    out = []
    for p in sorted(glob.glob(os.path.join(ROOT, "*", "index.html"))):
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.startswith(("archive/", "Drafts/", ".tmp/", "tools/")) or rel in PAGE_SRC:
            continue
        s = open(p, encoding="utf-8", errors="replace").read()
        if "country-article-card" in s and 'http-equiv="refresh"' not in s[:4000]:
            out.append(rel)
    return out


def main():
    _og_dir()
    slugs = sorted(os.path.basename(os.path.dirname(f)) for f in glob.glob(os.path.join(ROOT, "*", "field-notes.html")))
    lowres = []
    for slug in slugs:
        src, vbias, how, par = country_source(slug)
        if "thumb" in how:
            lowres.append(slug)
        dst = os.path.join(ROOT, "Images", "web", "og", "%s.jpg" % slug)
        if not DRY:
            if not FORCE and thumb_lock.is_manual(dst):
                print("kept hand-edited %s (skipped)" % os.path.relpath(dst, ROOT).replace("\\", "/"))
            else:
                make_og(src, dst, vbias, par); thumb_lock.record(dst)

        # point the page's og:image / twitter:image at the new file
        page = os.path.join(ROOT, slug, "field-notes.html")
        html = open(page, encoding="utf-8").read()
        # a field-notes page with its own picture (<slug>-field-notes.jpg: Armenia, El Salvador) keeps it. The Nov 10 run
        # pointed both at the country picture; Kevin, 2026-11-11 (push call 10): put back the Nov 1 pictures.
        own = re.search(r'<meta property="og:image" content="[^"]*/og/(%s-field-notes\.jpg)"' % re.escape(slug), html)
        if own and os.path.isfile(os.path.join(ROOT, "Images", "web", "og", own.group(1))):
            print("%-16s keeps its own share picture %s" % (slug, own.group(1)))
            continue
        new = point(html, "%s/Images/web/og/%s.jpg" % (DOMAIN, slug))
        if new != html and not DRY:
            open(page, "w", encoding="utf-8", newline="").write(new)
        print("%s%-16s <- %-30s y=%d%%  %s" % ("[dry] " if DRY else "", slug, os.path.basename(src), round(vbias*100), how))

    print("\n%s%d country OG image(s)" % ("[dry-run] " if DRY else "", len(slugs)))
    if lowres:
        print("NOTE: no hi-res match, generated from the 600px portrait thumb: " + ", ".join(lowres))

    made = 0
    for page, src in sorted(PAGE_SRC.items()):
        ppath = os.path.join(ROOT, page.replace("/", os.sep))
        if not os.path.exists(ppath):
            print("  skip (no page): %s" % page); continue
        name = os.path.splitext(page.replace("/", "-"))[0]
        dst = os.path.join(ROOT, "Images", "web", "og", "%s.jpg" % name)
        if not DRY and (FORCE or not os.path.exists(dst)):
            if src is None:
                render_page(page, dst)
            else:
                sp = os.path.join(ROOT, src.replace("/", os.sep))
                if not os.path.exists(sp):
                    print("  !! source missing for %s: %s" % (page, src)); continue
                make_og(sp, dst, 0.5)
            thumb_lock.record(dst); made += 1

        ogurl = "%s/Images/web/og/%s.jpg" % (DOMAIN, name)
        html = open(ppath, encoding="utf-8").read()
        new = point(html, ogurl)
        if new != html and not DRY:
            open(ppath, "w", encoding="utf-8", newline="").write(new)
        print("%s%-40s <- %s" % ("[dry] " if DRY else "", page, src or "page screenshot"))

    print("%s%d page image(s) generated, %d page(s) repointed"
          % ("[dry-run] " if DRY else "", made, len(PAGE_SRC)))

    # a country page PAGE_SRC does not name (a guide launched since it was last edited) still gets one: its own if it
    # has none, from the destinations card's photo; one that already shares an image keeps it
    extra = country_pages_missing_from_page_src()
    for line in ensure(extra, DRY):
        print("%s%s" % ("[dry] " if DRY else "", line))


if __name__ == "__main__":
    main()
