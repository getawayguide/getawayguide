#!/usr/bin/env python3
"""
gen_og_images.py — generate per-country Open Graph images (1200x630) for social
sharing and point each field-notes page's og:image / twitter:image at them.

Prefers the high-res source in Images/dest-cards/<Name>_1.*; falls back to the
compressed portrait dest-card (flagged as lower-res). Output -> Images/web/og/<slug>.jpg.

Usage: python tools/gen_og_images.py [--dry-run]
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

def make_og(src, dst, vbias):
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB"); W, H = im.size
    scale = max(OG_W / W, OG_H / H)
    rw, rh = round(W * scale), round(H * scale)
    im = im.resize((rw, rh), Image.LANCZOS)
    x = (rw - OG_W) // 2
    y = max(0, min(rh - OG_H, round((rh - OG_H) * vbias)))
    im = im.crop((x, y, x + OG_W, y + OG_H))
    im.save(dst, format="JPEG", quality=82, optimize=True)

slugs = sorted(os.path.basename(os.path.dirname(f)) for f in glob.glob(os.path.join(ROOT, "*", "field-notes.html")))
lowres = []
for slug in slugs:
    src = hi_res(slug)
    if not src:
        src = os.path.join(ROOT, "Images", "web", "dest-cards", "%s.jpg" % slug)
        lowres.append(slug)
    dst = os.path.join(ROOT, "Images", "web", "og", "%s.jpg" % slug)
    if not DRY:
        if not FORCE and thumb_lock.is_manual(dst):
            print("kept hand-edited %s (skipped)" % os.path.relpath(dst, ROOT).replace("\\", "/"))
        else:
            make_og(src, dst, vbias_for(slug)); thumb_lock.record(dst)

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
    print("%s%-16s <- %s" % ("[dry] " if DRY else "", slug, os.path.relpath(src, ROOT).replace("\\", "/")))

print("\n%s%d OG images generated" % ("[dry-run] " if DRY else "", len(slugs)))
if lowres:
    print("NOTE: used lower-res compressed fallback (add a hi-res Images/dest-cards/<Name>_1.jpg): " + ", ".join(lowres))
