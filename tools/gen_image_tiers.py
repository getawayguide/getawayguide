#!/usr/bin/env python3
"""Build the 1x/2x/3x density tiers (JPEG + WebP) for article body images.

THE MISSING STEP. CLAUDE.md documents a two-tier responsive system where every
article photo has desktop `-1x/-2x/-3x` and mobile `-mob-1x/-mob-2x/-mob-3x`,
each with a WebP beside the JPEG. But nothing in tools/ ever created those
files: `recompress_desktop.py` writes ONE desktop JPEG, `gen_mobile_jpg.py`
writes ONE `-mob-2x.jpg`, and `gen_mobile_webp.py` adds a WebP beside whatever
already exists. gen_mobile_jpg.py says so in its own docstring -- "The El
Salvador set was built by a process no longer in the repo."

So Kosovo shipped with a desktop <source> pointing at a single un-tiered JPEG
with no WebP at all, which is what the 2026-09-23 hygiene audit caught.

SIZING is anchored on what the site already serves, not invented:

    desktop 3x = the existing Images/web/<Country>/<name>.jpg size
    mobile  2x = the existing <name>-mob-2x.jpg size

and the other tiers follow at the exact 1:2:3 ratio. So the largest desktop
tier is pixel-for-pixel what desktop gets today (no regression at the top end),
smaller screens simply stop over-downloading, and the numbers land in the same
range as the shipped El Salvador set (portrait desktop 1x ~200px, mobile 1x
~230px). Guessing fixed widths instead would have made one country inconsistent
with the other nine.

Every tier is resampled from the ARCHIVAL ORIGINAL under Images/<Country>/,
never from the already-compressed web copy, so a 1x is not a downscale of a
downscale. Originals are opened read-only and never written.

ICC: these photos are Display P3. The profile is read BEFORE convert("RGB") and
passed to every save. Dropping it makes the photos render visibly grey.

  py tools/gen_image_tiers.py --country Kosovo --dry-run
  py tools/gen_image_tiers.py --country Kosovo
  py tools/gen_image_tiers.py                      # every country
"""
import argparse, os, sys
from pathlib import Path
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "Images" / "web"
SKIP_DIRS = {"web", "Index", "Bio", "dest-cards", "flags", "icons", "og"}
JPG_Q, WEBP_Q = 88, 82
ORIG_EXTS = (".jpg", ".JPG", ".jpeg", ".JPEG", ".png", ".PNG", ".HEIC", ".heic")


def original_for(web_stem: Path):
    """Images/web/<Country>/<path>/<Name>  ->  Images/<Country>/<path>/<Name>.<ext>"""
    rel = web_stem.relative_to(WEB)
    for ext in ORIG_EXTS:
        p = ROOT / "Images" / rel.parent / (rel.name + ext)
        if p.exists():
            return p
    # tolerate case drift in the folder name (Prishtina / Pristina)
    parent = ROOT / "Images" / rel.parent
    if parent.is_dir():
        for f in parent.iterdir():
            if f.stem.lower() == rel.name.lower() and f.suffix in ORIG_EXTS:
                return f
    return None


def base_images(country=None):
    """Every web image that is a BASE (not already a -1x/-2x/-3x or -mob-* file)."""
    roots = [WEB / country] if country else [d for d in WEB.iterdir()
                                             if d.is_dir() and d.name not in SKIP_DIRS]
    for r in roots:
        if not r.is_dir():
            continue
        for p in sorted(r.rglob("*.jpg")):
            stem = p.stem
            if "-mob-" in stem or stem.endswith(("-1x", "-2x", "-3x")):
                continue
            yield p


def save(img, dest: Path, icc, dry):
    if dry:
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    kw = {"icc_profile": icc} if icc else {}
    if dest.suffix == ".webp":
        img.save(dest, "WEBP", quality=WEBP_Q, method=6, **kw)
    else:
        img.save(dest, "JPEG", quality=JPG_Q, optimize=True, progressive=True, **kw)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--country")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="rewrite tiers that exist")
    a = ap.parse_args()

    made = skipped = missing = 0
    for web in base_images(a.country):
        stem = web.with_suffix("")
        orig = original_for(stem)
        if not orig:
            print("  NO ORIGINAL for %s" % web.relative_to(ROOT))
            missing += 1
            continue

        with Image.open(web) as im:
            dw, dh = im.size                      # desktop 3x anchor
        mob2 = Path(str(stem) + "-mob-2x.jpg")
        if mob2.exists():
            with Image.open(mob2) as im:
                mw, mh = im.size                  # mobile 2x anchor
        else:
            mw, mh = round(dw * 0.77), round(dh * 0.77)

        targets = {}
        for i, n in ((1, 1), (2, 2), (3, 3)):     # desktop: 3x == today's file
            targets["-%dx" % n] = (round(dw * n / 3), round(dh * n / 3))
        for n in (1, 2, 3):                       # mobile: 2x == today's file
            targets["-mob-%dx" % n] = (round(mw * n / 2), round(mh * n / 2))

        todo = [(suf, wh) for suf, wh in targets.items()
                for ext in (".jpg", ".webp")
                if a.force or not Path(str(stem) + suf + ext).exists()]
        if not todo:
            skipped += 1
            continue

        src = ImageOps.exif_transpose(Image.open(orig))
        icc = src.info.get("icc_profile")         # BEFORE convert(), always
        rgb = src.convert("RGB")

        for suf, (w, h) in targets.items():
            for ext in (".jpg", ".webp"):
                dest = Path(str(stem) + suf + ext)
                if dest.exists() and not a.force:
                    continue
                made += save(rgb.resize((max(w, 1), max(h, 1)), Image.LANCZOS),
                             dest, icc, a.dry_run)
        src.close()
        print("  %-46s %s" % (stem.relative_to(WEB), 
              " ".join(sorted({s for s, _ in [(k, v) for k, v in targets.items()]}))))

    print("\n%s%d file(s) written, %d image(s) already complete, %d without an original"
          % ("[dry-run] " if a.dry_run else "", made, skipped, missing))


if __name__ == "__main__":
    main()
