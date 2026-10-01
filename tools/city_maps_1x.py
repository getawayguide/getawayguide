#!/usr/bin/env python3
"""A 1x copy of every desktop city map, and the srcset that serves it.

Kevin, 2026-10-01: the photo fix ("these photos look noticeably worse", on his 1x monitor) applied
to every image on the site. A desktop city map is rendered 760 px wide at 2x (a 1520 px PNG) and
drawn 729 px wide, so a 1x screen gets 2.09x: just past the point where Chrome halves the image
and resamples the rest by a fraction of a pixel, the softest result there is (measured in
.tmp/tier_sweep.py; see tools/fit_image_tiers.py). Roads and coastlines go soft. A 1350 px copy
(1.85x) fixes that, and as lossless WebP it is about half the PNG's size. 2x screens keep the 1520.
The labels and pins are SVG on top and were never affected.

tools/city_map.py calls write_1x() and desk_srcset() for every new map. Run this once to backfill:

    python tools/city_maps_1x.py [--dry-run]     every desktop map in Images/web/city-maps + every page
"""
import io
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MAPS = ROOT / "Images" / "web" / "city-maps"
DRAWN = 729                    # the article column a desktop map is drawn in, px
ONE_X = 1350                   # 1.85 x DRAWN: the 1x file
FULL = 1520                    # the 2x render (760 x 2)


def write_1x(png, dest):
    """the 1x copy of a desktop map render (PNG bytes or path) as lossless WebP"""
    src = Image.open(io.BytesIO(png) if isinstance(png, (bytes, bytearray)) else png)
    icc = src.info.get("icc_profile")
    im = src.convert("RGB")
    im = im.resize((ONE_X, round(im.height * ONE_X / im.width)), Image.LANCZOS)
    kw = {"icc_profile": icc} if icc else {}
    im.save(dest, "WEBP", lossless=True, method=6, **kw)


def desk_srcset(png_href):
    """srcset + sizes for a desktop map's <img>, from its 1520 px PNG's href"""
    one = re.sub(r"\.png$", "-1x.webp", png_href)
    return f'srcset="{one} {ONE_X}w, {png_href} {FULL}w" sizes="{DRAWN}px"'


IMG = re.compile(r'<img class="cmbase" src="([^"]*city-maps/[^"/]+(?<!-mobile)\.png)"(?![^>]*srcset)')   # desktop maps only


def pages():
    live = [f for f in subprocess.run(["git", "ls-files", "*.html"], cwd=ROOT, capture_output=True, text=True,
                                      encoding="utf-8").stdout.split("\n") if f and not f.startswith(("archive/", ".tmp/"))]
    drafts = [p.relative_to(ROOT).as_posix() for p in (ROOT / "Drafts").rglob("*.html")]
    return sorted(set(live + drafts))


def main(dry):
    made = 0
    for png in sorted(MAPS.glob("*.png")):
        if png.stem.endswith("-mobile"):
            continue
        w = Image.open(png).size[0]
        dest = png.with_name(png.stem + "-1x.webp")
        if w != FULL:
            print("  skipped (not a 1520 px render):", png.name, w)
            continue
        if not dest.exists() or dest.stat().st_mtime < png.stat().st_mtime:
            if not dry:
                write_1x(str(png), dest)
            made += 1
    print("%s%d 1x map file(s)" % ("[dry] " if dry else "", made))
    patched = 0
    for rel in pages():
        p = ROOT / rel
        raw = io.open(p, encoding="utf-8", newline="").read()
        new, n = IMG.subn(lambda m: m.group(0) + " " + desk_srcset(m.group(1))
                          if (MAPS / Path(m.group(1)).name).with_name(Path(m.group(1)).stem + "-1x.webp").exists() or dry
                          else m.group(0), raw)
        if new != raw:
            patched += 1
            print("  %s%s: %d map(s)" % ("[dry] " if dry else "", rel, n))
            if not dry:
                io.open(p, "w", encoding="utf-8", newline="").write(new)
    print("%s%d page(s) given the 1x maps" % ("[dry] " if dry else "", patched))


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
