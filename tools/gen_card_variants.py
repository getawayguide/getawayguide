#!/usr/bin/env python3
"""Build an article's card (thumbnail) photo from the archive original.

The card is the photo on every listing that points at an article: the country page's article
cards (portrait on a monitor, a landscape strip on a phone) and the home and posts pages.
Each slot crops the SAME file with its own background-position, the way a hero is one photo
with a position per breakpoint, so the file keeps the whole photo and is sized by its SHORT
edge: a portrait slot crops the height of a landscape photo, a landscape slot the width of a
portrait one. The biggest card box is the country card at a 1100px window, two across at about
442x663; at 2x that is 1326 px tall, so the short edge is 1400.

    card-<slug>.jpg / .webp       short edge 1400px   every card slot

(Kevin, 2026-09-29: "make thumbnails ... exactly like how this works for the heroes". The
Orgov card that prompted it was cut by hand from IMG_0438-2 at 900x1125; this is that step as
a tool, driven by the Hero Picker's Thumbnail mode.)

ICC PROFILE: the originals are Display P3; every write carries the profile (a dropped profile
renders the photo grey). The original is read and never written.

    python tools/gen_card_variants.py <original> --out "Images/web/Armenia" --slug orgov [--angle 1.5]
"""
import argparse
import math
import sys
from pathlib import Path

from PIL import Image, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
SHORT = 1400
JPEG_Q, WEBP_Q = 84, 82


def out(s=""):
    sys.stdout.buffer.write((s + "\n").encode("utf-8", "replace"))
    sys.stdout.buffer.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("original")
    ap.add_argument("--out", required=True, help="directory under Images/web/")
    ap.add_argument("--slug", required=True, help="card-<slug>.jpg")
    ap.add_argument("--angle", type=float, default=0.0, help="straighten first (same as the hero picker's slider)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    src_path = (ROOT / a.original) if not Path(a.original).is_absolute() else Path(a.original)
    if not src_path.exists():
        raise SystemExit("no such original: %s" % src_path)
    outdir = (ROOT / a.out) if not Path(a.out).is_absolute() else Path(a.out)
    if "Images/web" not in outdir.as_posix():
        raise SystemExit("--out must be under Images/web/ (got %s)" % outdir)

    src = ImageOps.exif_transpose(Image.open(src_path))
    icc = src.info.get("icc_profile")          # grab BEFORE convert()
    if abs(a.angle) > 1e-3:                    # the hero generator's rotate-and-inscribe, to the pixel
        w0, h0 = src.size
        rad = math.radians(abs(a.angle))
        scale = 1.0 / (math.cos(rad) + (max(w0, h0) / min(w0, h0)) * math.sin(rad))
        rot = src.convert("RGB").rotate(a.angle, resample=Image.BICUBIC, expand=True)
        cw, ch = int(w0 * scale), int(h0 * scale)
        cx, cy = rot.width / 2, rot.height / 2
        src = rot.crop((int(cx - cw / 2), int(cy - ch / 2), int(cx - cw / 2) + cw, int(cy - ch / 2) + ch))
    rgb = src.convert("RGB")
    w, h = rgb.size
    s = min(1.0, SHORT / min(w, h))
    size = (max(1, round(w * s)), max(1, round(h * s)))
    im = rgb if s == 1.0 else rgb.resize(size, Image.LANCZOS)
    for ext, kw in ((".jpg", dict(quality=JPEG_Q, optimize=True, progressive=True)), (".webp", dict(quality=WEBP_Q, method=6))):
        dst = outdir / ("card-%s%s" % (a.slug, ext))
        if a.dry_run:
            out("  would write %s %dx%d" % (dst.relative_to(ROOT).as_posix(), *size))
            continue
        outdir.mkdir(parents=True, exist_ok=True)
        im.save(dst, icc_profile=icc, **kw)
        out("  wrote %s %dx%d  %.0f KB" % (dst.relative_to(ROOT).as_posix(), size[0], size[1], dst.stat().st_size / 1024))
    if not icc:
        out("  ! the original carries no ICC profile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
