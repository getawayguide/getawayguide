"""Give every search result a thumbnail, and write it into search-index.js.

The search panel listed countries and articles as bare text while the design it
was built from shows a small photo on every row. The index had no image data at
all, so this generates it.

Two sources, and the difference matters:

  countries -> Images/web/dest-cards/<slug>.jpg already exists and is already
               compressed, so it is reused as-is, along with the
               background-position destinations.html gives it.

  articles  -> their cards point at the FULL-RESOLUTION originals under
               Images/ (some are over 10MB). Those are the archive and are
               never served, so a real thumbnail is cut here instead, into
               Images/web/search/.

The ICC profile is carried through every write. These photos are Display P3;
Pillow's convert("RGB") drops the profile silently and a browser then reads P3
values as sRGB, which renders them visibly grey.

    python tools/build_search_thumbs.py --dry-run
    python tools/build_search_thumbs.py
"""
import argparse
import json
import pathlib
import re
import sys

from PIL import Image, ImageOps

SITE = pathlib.Path(__file__).resolve().parent.parent
OUT = SITE / "Images" / "web" / "search"
INDEX = SITE / "search-index.js"

THUMB_W, THUMB_H = 240, 180          # 3x the ~80x60 the row paints
QUALITY = 82


def read_index():
    s = INDEX.read_text(encoding="utf-8")
    head, _, body = s.partition("=")
    return head + "=", json.loads(body.rstrip().rstrip(";"))


def dest_card_positions():
    """slug -> (file, background-position) as destinations.html sets them."""
    h = (SITE / "destinations.html").read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(
            # the url may carry a ?v= cache-buster, which must not be captured
            r"background-image:url\('(Images/web/dest-cards/([a-z0-9-]+)\.jpg)"
            r"(?:\?[^']*)?'\)"
            r"[^\"]*?background-position:([^;\"]+)", h):
        out[m.group(2)] = (m.group(1), m.group(3).strip())
    return out


def article_card_images():
    """.img-<name> -> (original path, css position) from styles.css."""
    css = (SITE / "styles.css").read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"\.(img-[a-z0-9-]+)\s*\{\s*background:\s*url\('([^']+)'\)\s*([^;}]*)",
                         css):
        pos = m.group(3).replace("/cover", "").replace("no-repeat", "").strip()
        out[m.group(1)] = (m.group(2), pos or "center")
    return out


def card_class_for(url, home_html):
    """the .img-* class the home/archive card uses for this article url"""
    m = re.search(r'href="' + re.escape(url) + r'"[^>]*>\s*<div class="card-img[^"]*?'
                  r'(img-[a-z0-9-]+)', home_html)
    return m.group(1) if m else None


def cut(src, dst, dry):
    """cover-crop to the thumbnail box, ICC profile carried through"""
    if dst.exists():
        return "exists"
    if dry:
        return "would cut"
    try:
        im = ImageOps.exif_transpose(Image.open(src))
    except Exception as e:                      # noqa: BLE001
        return f"FAILED to open ({e})"
    icc = im.info.get("icc_profile")            # BEFORE convert()
    im = ImageOps.fit(im.convert("RGB"), (THUMB_W, THUMB_H),
                      method=Image.LANCZOS, centering=(0.5, 0.5))
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, quality=QUALITY, icc_profile=icc, optimize=True)
    im.save(dst.with_suffix(".webp"), quality=QUALITY, icc_profile=icc)
    return "cut"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    head, idx = read_index()
    cards = dest_card_positions()
    imgs = article_card_images()
    home = (SITE / "index.html").read_text(encoding="utf-8")
    posts = (SITE / "posts.html").read_text(encoding="utf-8")

    n_country = n_article = 0

    for c in idx.get("countries", []):
        slug = c["url"].split("/")[0]
        if slug in cards:
            c["thumb"], c["pos"] = cards[slug]
            n_country += 1
        else:
            print(f"  no dest-card for {c['name']} ({slug})", file=sys.stderr)

    for art in idx.get("articles", []):
        cls = card_class_for(art["url"], home) or card_class_for(art["url"], posts)
        if not cls or cls not in imgs:
            print(f"  no card image for {art['url']}", file=sys.stderr)
            continue
        original, pos = imgs[cls]
        src = SITE / original
        if not src.exists():
            print(f"  missing original {original}", file=sys.stderr)
            continue
        name = art["url"].replace("/", "__").replace(".html", "") + ".jpg"
        dst = OUT / name
        status = cut(src, dst, a.dry_run)
        art["thumb"] = f"Images/web/search/{name}"
        art["pos"] = pos
        n_article += 1
        print(f"  {status:10s} {name}  <- {original}")

    if not a.dry_run:
        INDEX.write_text(head + json.dumps(idx, ensure_ascii=False) + ";\n",
                         encoding="utf-8")
    print(f"\n{'would write' if a.dry_run else 'wrote'} thumbnails for "
          f"{n_country} countries and {n_article} articles")


if __name__ == "__main__":
    main()
