#!/usr/bin/env python3
"""Compare each country's OG image against the crop its MOBILE landscape dest-card shows.

Both are 1.9:1, but they're produced from different sources: the OG from the hi-res
Images/dest-cards/<Name>_1.*, the mobile card by CSS cover-cropping the 600x750 portrait
thumbnail at its background-position (or a dedicated <slug>-landscape.jpg). So the same
country can be framed two different ways depending on where the link is seen.

  py tools/compare_dest_crops.py     ->  .tmp/previews/dest-crop-compare.html
"""
import glob, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, ".tmp", "previews")
ASPECT = 1.9                       # .home-dest-card mobile aspect-ratio


def dest_card_rule(dest_html, slug):
    """(image, vertical bias 0-1) the MOBILE landscape card actually renders."""
    img = "Images/web/dest-cards/%s.jpg" % slug
    bias = 0.5
    m = re.search(r"dest-cards/%s\.jpg'[^\"]*background-position:\s*\d+%%\s+(\d+)%%"
                  % re.escape(slug), dest_html)
    if m:
        bias = int(m.group(1)) / 100
    css = open(os.path.join(ROOT, "styles.css"), encoding="utf-8").read()
    o = re.search(r"#dest-%s\s+\.dest-bg\{([^}]*)\}" % re.escape(slug), css)
    if o:
        body = o.group(1)
        mi = re.search(r"url\('([^']+)'\)", body)
        if mi:
            img = mi.group(1)
        mp = re.search(r"background-position:\s*\d+%\s+(\d+)%", body)
        if mp:
            bias = int(mp.group(1)) / 100
    return img, bias


def mobile_crop(img_rel, bias, out):
    from PIL import Image, ImageOps
    p = os.path.join(ROOT, img_rel.replace("/", os.sep))
    if not os.path.exists(p):
        return None
    src = ImageOps.exif_transpose(Image.open(p))
    icc = src.info.get("icc_profile")   # else the comparison is a different colour space
    im = src.convert("RGB")             # than what actually ships
    band = round(im.width / ASPECT)
    if band > im.height:                       # already landscape enough
        band = im.height
    top = round((im.height - band) * bias)
    im.crop((0, top, im.width, top + band)).resize((570, 300), Image.LANCZOS) \
        .save(out, "JPEG", quality=86, icc_profile=icc)
    return True


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    dest = open(os.path.join(ROOT, "destinations.html"), encoding="utf-8").read()
    slugs = sorted(os.path.basename(os.path.dirname(f))
                   for f in glob.glob(os.path.join(ROOT, "*", "field-notes.html")))
    rows = []
    for slug in slugs:
        og = "Images/web/og/%s.jpg" % slug
        if not os.path.exists(os.path.join(ROOT, og)):
            continue
        img, bias = dest_card_rule(dest, slug)
        mc = os.path.join(OUTDIR, "mob-%s.jpg" % slug)
        ok = mobile_crop(img, bias, mc)
        rows.append((slug, og, ("mob-%s.jpg" % slug) if ok else None, img, bias))

    cards = "".join(
        "<figure><figcaption>%s <span>%s &middot; y=%d%%</span></figcaption>"
        "<div class=p><span>link preview (OG)</span><img src='../../%s'></div>"
        "<div class=p><span>mobile landscape card</span>%s</div></figure>"
        % (s, os.path.basename(src), round(b * 100), og,
           ("<img src='%s'>" % m) if m else "<div class=x>source missing</div>")
        for s, og, m, src, b in rows)
    doc = ("<!doctype html><meta charset='utf-8'><title>Dest crops: OG vs mobile card</title>"
           "<style>body{margin:0;padding:24px;background:#eceae4;color:#1C2821;"
           "font:13px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}"
           "h1{font:600 20px Georgia,serif;margin:0 0 6px}p.s{opacity:.7;margin:0 0 18px}"
           ".g{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:22px}"
           "figure{margin:0;background:rgba(255,255,255,.5);padding:12px;border-radius:9px}"
           "figcaption{font:600 13px ui-monospace,Menlo,monospace;margin-bottom:9px;"
           "display:flex;justify-content:space-between;gap:8px}"
           "figcaption span{font-weight:400;opacity:.55;font-size:11px}"
           ".p{margin-bottom:9px}.p>span{display:block;font-size:10.5px;letter-spacing:.05em;"
           "text-transform:uppercase;opacity:.55;margin-bottom:4px}"
           "img{display:block;width:100%;aspect-ratio:1.9;object-fit:cover;border-radius:5px;"
           "border:1px solid #d6d4cd}"
           ".x{padding:22px;text-align:center;color:#8d2b1c;font-size:12px;border:1px dashed #c9a}"
           "</style><h1>Destination crops &mdash; link preview vs mobile card</h1>"
           "<p class=s>Both are 1.9:1. Where the two differ, the same country is framed "
           "differently depending on where it's seen.</p><div class='g'>" + cards + "</div>")
    p = os.path.join(OUTDIR, "dest-crop-compare.html")
    open(p, "w", encoding="utf-8").write(doc)
    print("wrote %s  (%d countries)" % (p, len(rows)))


if __name__ == "__main__":
    main()
