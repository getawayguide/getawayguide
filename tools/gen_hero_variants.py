#!/usr/bin/env python3
"""Build the six hero files for one photo, so a page never has to serve the original.

Heroes were the one part of the image system with no tool: the body images have
recompress_desktop + gen_image_tiers, but a hero was made by hand. So the set was only as
consistent as whoever last made one, and top-10-el-salvador.html ended up pointing its CSS
background straight at a 13MB archival JPEG -- the exact thing CLAUDE.md forbids, arriving
through `background:url()` rather than an <img>, which is why the heroes check never saw it.

The convention this follows is the one already on disk (hero-el-salvador, hero-queenstown):

    hero-<slug>.jpg / .webp         2000px on the long edge   the desktop 1x
    hero-<slug>-2x.jpg / .webp      2561px                    the desktop 2x
    hero-<slug>-mob.jpg / .webp     1206px                    phones at 2x/3x
    hero-<slug>-mob-1x.jpg / .webp   603px                    phones at 1x

The -mob-1x is the one every existing hero was missing: the mobile <source> carried a single
file, so a 1x phone downloaded the same 1206px image as a 3x one. It halves that.

ICC PROFILE: these are Display P3. Pillow's convert("RGB") drops the profile and a browser
then reads P3 values as sRGB, which renders the photo visibly grey. Every write carries it.

The original is read and never written.

    python tools/gen_hero_variants.py "Images/El Salvador/.../Waterfall Main.JPEG" \
        --out "Images/web/El Salvador" --slug waterfalls
    python tools/gen_hero_variants.py <original> --out <dir> --slug <slug> --dry-run
"""
import argparse
import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
# long edge, suffix
SIZES = [(2000, ""), (2561, "-2x"), (1206, "-mob"), (603, "-mob-1x")]
JPEG_Q, WEBP_Q = 86, 82


def out(s=""):
    sys.stdout.buffer.write((s + "\n").encode("utf-8", "replace"))
    sys.stdout.buffer.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("original")
    ap.add_argument("--out", required=True, help="directory under Images/web/")
    ap.add_argument("--slug", required=True, help="hero-<slug>.jpg etc")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="rewrite files that already exist")
    a = ap.parse_args()

    src_path = (ROOT / a.original) if not Path(a.original).is_absolute() else Path(a.original)
    if not src_path.exists():
        raise SystemExit("no such original: %s" % src_path)
    outdir = (ROOT / a.out) if not Path(a.out).is_absolute() else Path(a.out)
    if "Images/web" not in outdir.as_posix():
        raise SystemExit("--out must be under Images/web/ (got %s)" % outdir)

    src = ImageOps.exif_transpose(Image.open(src_path))
    icc = src.info.get("icc_profile")          # grab BEFORE convert()
    if not icc:
        out("  ! the original carries no ICC profile; colours may already be sRGB")
    rgb = src.convert("RGB")
    w, h = rgb.size
    out("original: %dx%d  %.2f MB  icc=%s" % (w, h, src_path.stat().st_size / 1048576, bool(icc)))

    if not a.dry_run:
        outdir.mkdir(parents=True, exist_ok=True)
    total = 0
    done = set()
    for long_edge, suffix in SIZES:
        scale = min(1.0, long_edge / max(w, h))
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        # A source smaller than the tier cannot fill it. Writing it anyway produced a -2x
        # that was byte-for-byte the same picture as the 1x, which is not a tier: it is a
        # second copy the browser may download believing it is sharper.
        if size in done:
            out("  skip (source is only %dpx; -2x would duplicate the 1x)  hero-%s%s"
                % (max(w, h), a.slug, suffix))
            continue
        done.add(size)
        im = rgb if scale == 1.0 else rgb.resize(size, Image.LANCZOS)
        for ext, kw in ((".jpg", dict(quality=JPEG_Q, optimize=True, progressive=True)),
                        (".webp", dict(quality=WEBP_Q, method=6))):
            dst = outdir / ("hero-%s%s%s" % (a.slug, suffix, ext))
            if dst.exists() and not a.force and not a.dry_run:
                out("  skip (exists)  %s" % dst.relative_to(ROOT).as_posix())
                continue
            if a.dry_run:
                out("  would write    %-52s %dx%d" % (dst.relative_to(ROOT).as_posix(), *size))
                continue
            im.save(dst, icc_profile=icc, **kw)
            n = dst.stat().st_size
            total += n
            out("  wrote          %-52s %dx%d  %.2f MB" % (
                dst.relative_to(ROOT).as_posix(), size[0], size[1], n / 1048576))
    if not a.dry_run:
        out("\n%.2f MB written; the original is untouched at %.2f MB"
            % (total / 1048576, src_path.stat().st_size / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
