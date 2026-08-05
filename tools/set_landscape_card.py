#!/usr/bin/env python3
"""Rebuild a country's dedicated MOBILE landscape dest-card from its hi-res original.

A few countries override the mobile card with a fixed landscape image
(#dest-<slug> .dest-bg{background-image:url('…-landscape.jpg')}) instead of cover-cropping
the portrait thumbnail. Those were cropped by hand and can end up much tighter than the
link preview of the same country - Finland's card was bare roof and sky while its preview
showed the whole Porvoo waterfront.

This regenerates the landscape file at the SAME framing the OG image uses, so the card and
the link preview show the same thing.

  py tools/set_landscape_card.py finland            # use the OG framing (y from destinations.html)
  py tools/set_landscape_card.py finland --y 45     # or force a vertical bias
"""
import argparse, glob, os, re, sys
from PIL import Image, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1200, 630                      # same as the OG, so the two stay identical


def hi_res(slug):
    """The best original for this country (largest matching file in Images/dest-cards)."""
    cands = [slug, slug.replace("-", ""), slug.split("-")[-1]]
    hits = [p for p in sorted(glob.glob(os.path.join(ROOT, "Images", "dest-cards", "*")))
            if any(os.path.basename(p).lower().startswith(c + s)
                   for c in cands for s in ("_", ".", " "))]
    return max(hits, key=os.path.getsize) if hits else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--src", help="explicit source image (default: best Images/dest-cards match)")
    ap.add_argument("--y", type=float, help="vertical bias 0-100 (default: the OG's)")
    a = ap.parse_args()

    src = os.path.join(ROOT, a.src) if a.src else hi_res(a.slug)
    if not src or not os.path.exists(src):
        sys.exit("no source found for %s" % a.slug)

    if a.y is not None:
        bias = a.y / 100
    else:
        dest = open(os.path.join(ROOT, "destinations.html"), encoding="utf-8").read()
        m = re.search(r"dest-cards/%s\.jpg'[^\"]*background-position:\s*\d+%%\s+(\d+)%%"
                      % re.escape(a.slug), dest)
        bias = int(m.group(1)) / 100 if m else 0.5

    src_im = ImageOps.exif_transpose(Image.open(src))
    icc = src_im.info.get("icc_profile")   # Display P3; without it the card looks grey
    im = src_im.convert("RGB")
    s = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x = (im.width - W) // 2
    y = max(0, min(im.height - H, round((im.height - H) * bias)))
    out = os.path.join(ROOT, "Images", "web", "dest-cards", "%s-landscape.jpg" % a.slug)
    im.crop((x, y, x + W, y + H)).save(out, "JPEG", quality=84, optimize=True,
                                       icc_profile=icc)
    print("%s  <- %s  y=%d%%  (%dx%d, %.0f KB)"
          % (os.path.relpath(out, ROOT).replace("\\", "/"), os.path.basename(src),
             round(bias * 100), W, H, os.path.getsize(out) / 1024))


if __name__ == "__main__":
    main()
