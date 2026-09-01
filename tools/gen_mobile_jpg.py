"""Build the MOBILE tier for article images, so phones stop downloading originals.

The documented pipeline had a hole. `recompress_desktop.py` writes the desktop
file and `add_picture_mobile.py` wraps the image in <picture>, but the markup it
produces points the <img src> fallback at the FULL-RESOLUTION original:

    <source media="(min-width:769px)" srcset="../Images/web/Kosovo/IMG_5421.jpg">
    <img src="../Images/Kosovo/IMG_5421.jpg">          <-- 4.3 MB on a phone

Desktop is fine (it takes the <source>), but every phone pulls the original into
a 195px-wide frame. `gen_mobile_webp.py` repoints that fallback - but only for
images that already have `-mob-*.jpg` variants, and nothing in tools/ created
them. The El Salvador set was built by a process no longer in the repo.

This fills the gap: for each article body image still served from Images/, it
writes `Images/web/<path>/<Name>-mob-2x.jpg` and repoints the fallback at it.
Run it after add_picture_mobile.py and before gen_mobile_webp.py, which then
adds the WebP beside it.

Sizing matches the existing variants: a 2:3 portrait renders in a ~195px slot on
a 393px phone, and the shipped El Salvador mob-2x files are 461px wide.

ICC IS CARRIED THROUGH. These photos are Display P3; dropping the profile makes
a browser read P3 values as sRGB and the photo renders visibly grey.
Originals under Images/ are read only, never written.

    py tools/gen_mobile_jpg.py --dry-run
    py tools/gen_mobile_jpg.py
"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from PIL import Image, ImageOps

BASE = Path(__file__).resolve().parent.parent
IMAGES = BASE / "Images"
WEB = IMAGES / "web"
EXCLUDE = {"web", "Index", "Bio", "dest-cards"}
DRY = "--dry-run" in sys.argv

MOB_W = 461          # matches the shipped -mob-2x variants
QUALITY = 88


def article_pages():
    for p in sorted(BASE.glob("*/*.html")):
        try:
            t = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if "article-body" in t:
            yield p, t


def body_span(text):
    i = text.find("article-body")
    return (i, len(text)) if i >= 0 else (0, 0)


def make_variant(orig, dest):
    """One mobile-width JPEG from the original, profile intact."""
    src = ImageOps.exif_transpose(Image.open(orig))
    icc = src.info.get("icc_profile")           # grab BEFORE convert()
    w, h = src.size
    if w >= h:                                   # landscape: cap the long edge
        nw = min(w, int(MOB_W * 1.6))
    else:
        nw = min(w, MOB_W)
    nh = max(1, round(h * nw / w))
    out = src.convert("RGB").resize((nw, nh), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    kw = {"quality": QUALITY}
    if icc:
        kw["icc_profile"] = icc
    out.save(dest, "JPEG", **kw)
    return nw, nh


def main():
    made = repointed = 0
    saved = 0
    for page, text in article_pages():
        start, end = body_span(text)
        if end == 0:
            continue
        body = text[start:end]
        new_body = body
        # <img src="../Images/<Country>/...">  - an original, still the fallback
        for m in re.finditer(r'<img\b[^>]*?\bsrc="([^"]+)"', body):
            raw = m.group(1)
            if "/Images/" not in raw or "/web/" in raw:
                continue
            rel = unquote(raw.split("Images/", 1)[1])
            country = rel.split("/", 1)[0]
            if country in EXCLUDE:
                continue
            orig = IMAGES / rel
            if not orig.is_file():
                continue
            stem = Path(rel)
            dest = WEB / stem.parent / (stem.stem + "-mob-2x.jpg")
            if not dest.exists():
                if DRY:
                    print(f"  would create {dest.relative_to(BASE)}")
                else:
                    w, h = make_variant(orig, dest)
                    saved += orig.stat().st_size - dest.stat().st_size
                    print(f"  {dest.relative_to(BASE)}  {w}x{h}  "
                          f"{orig.stat().st_size/1e6:.1f}MB -> {dest.stat().st_size/1e6:.2f}MB")
                made += 1
            # repoint the fallback at the mobile file
            depth = len(page.relative_to(BASE).parts) - 1
            newsrc = "../" * depth + dest.relative_to(BASE).as_posix().replace(" ", "%20")
            new_body = new_body.replace(f'src="{raw}"', f'src="{newsrc}"')
            repointed += 1
        if new_body != body and not DRY:
            page.write_text(text[:start] + new_body + text[end:], encoding="utf-8")
            print(f"{page.relative_to(BASE)}: fallback repointed")

    verb = "would create" if DRY else "created"
    print(f"\n{verb} {made} mobile variant(s), repointed {repointed} fallback(s)"
          + (f", {saved/1e6:.1f} MB saved per phone load" if saved else ""))
    if DRY:
        print("dry run - nothing changed.")


if __name__ == "__main__":
    main()
