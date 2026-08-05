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
os.makedirs(os.path.join(ROOT, "Images", "web", "og"), exist_ok=True)

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
DEST = open(os.path.join(ROOT, "destinations.html"), encoding="utf-8").read()
def vbias_for(slug):
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

# A country's OG image and its MOBILE landscape dest-card are both 1.9:1, so they should
# show the same thing - otherwise the same country is framed two ways depending on where
# the link is seen. dest_card_source.resolve() reports what the card actually renders
# (the right photo, and the mobile background-position, which lives in styles.css and the
# old vbias_for() never read), so the preview is generated to match it.
import dest_card_source

slugs = sorted(os.path.basename(os.path.dirname(f)) for f in glob.glob(os.path.join(ROOT, "*", "field-notes.html")))
lowres = []
for slug in slugs:
    src, vbias, how = dest_card_source.resolve(slug)
    if not src:
        src = hi_res(slug) or os.path.join(ROOT, "Images", "web", "dest-cards", "%s.jpg" % slug)
        vbias, how = vbias_for(slug), "fallback"
    if "thumb" in how:
        lowres.append(slug)
    dst = os.path.join(ROOT, "Images", "web", "og", "%s.jpg" % slug)
    if not DRY:
        if not FORCE and thumb_lock.is_manual(dst):
            print("kept hand-edited %s (skipped)" % os.path.relpath(dst, ROOT).replace("\\", "/"))
        else:
            # replay the portrait step only when starting from a full original — a
            # landscape override or the thumbnail itself has already been through it
            par = None
            if "matched original" in how:
                tp = os.path.join(ROOT, "Images", "web", "dest-cards", "%s.jpg" % slug)
                if os.path.exists(tp):
                    tw, th = Image.open(tp).size
                    par = tw / th
            make_og(src, dst, vbias, par); thumb_lock.record(dst)

    # point the page's og:image / twitter:image at the new file
    page = os.path.join(ROOT, slug, "field-notes.html")
    html = open(page, encoding="utf-8").read()
    ogurl = "%s/Images/web/og/%s.jpg" % (DOMAIN, slug)
    new = re.sub(r'(<meta property="og:image" content=")[^"]*(")', r"\g<1>%s\g<2>" % ogurl, html)
    new = re.sub(r'(<meta name="twitter:image" content=")[^"]*(")', r"\g<1>%s\g<2>" % ogurl, new)
    # add width/height once (helps platforms render immediately)
    if 'property="og:image:width"' not in new:
        new = new.replace('<meta property="og:image" content="%s">' % ogurl,
                          '<meta property="og:image" content="%s">\n'
                          '<meta property="og:image:width" content="%d">\n'
                          '<meta property="og:image:height" content="%d">' % (ogurl, OG_W, OG_H), 1)
    if new != html and not DRY:
        open(page, "w", encoding="utf-8", newline="").write(new)
    print("%s%-16s <- %-30s y=%d%%  %s" % ("[dry] " if DRY else "", slug, os.path.basename(src), round(vbias*100), how))

print("\n%s%d country OG image(s)" % ("[dry-run] " if DRY else "", len(slugs)))
if lowres:
    print("NOTE: no hi-res match, generated from the 600px portrait thumb: " + ", ".join(lowres))


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
    new = re.sub(r'(<meta property="og:image" content=")[^"]*(")', r"\g<1>%s\g<2>" % ogurl, html)
    new = re.sub(r'(<meta name="twitter:image" content=")[^"]*(")', r"\g<1>%s\g<2>" % ogurl, new)
    if 'property="og:image:width"' not in new:
        new = new.replace('<meta property="og:image" content="%s">' % ogurl,
                          '<meta property="og:image" content="%s">\n'
                          '<meta property="og:image:width" content="%d">\n'
                          '<meta property="og:image:height" content="%d">' % (ogurl, OG_W, OG_H), 1)
    if new != html and not DRY:
        open(ppath, "w", encoding="utf-8", newline="").write(new)
    print("%s%-40s <- %s" % ("[dry] " if DRY else "", page, src or "page screenshot"))

print("%s%d page image(s) generated, %d page(s) repointed"
      % ("[dry-run] " if DRY else "", made, len(PAGE_SRC)))
