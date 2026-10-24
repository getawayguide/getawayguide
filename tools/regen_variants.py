"""Re-render a source photo's EXISTING web variants from the (new) source, each at
the exact pixel size it has now.

When a source under Images/ is swapped for a better one (tools/rebuild_from_original.py),
its variants in Images/web/ still hold the old pixels. gen_image_tiers.py can't
refresh them for sets that have no base web JPEG (the El Salvador set was built by a
process no longer in the repo), and sizing from the current files means no page's
width/height attributes, srcset "w" descriptors or layout change.

    python tools/regen_variants.py "Images/El Salvador/Santa Ana/Casa Coco.JPEG" ...
    python tools/regen_variants.py --from-dir ".tmp/replaced_sources/Images/El Salvador"

ICC: read before convert("RGB") and written to every file (Display P3; dropping it
renders the photos grey). Quality matches gen_image_tiers.py.
"""
import argparse
import re
import sys
from pathlib import Path

from PIL import Image, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
JPG_Q, WEBP_Q = 88, 82


def variants(src):
    rel = src.relative_to(ROOT / "Images")
    d = ROOT / "Images" / "web" / rel.parent
    if not d.is_dir():
        return []
    pat = re.compile(re.escape(rel.stem) + r"(-card|-mob)?(-[123]x)?\.(jpg|webp)", re.I)
    return sorted(v for v in d.iterdir() if v.is_file() and pat.fullmatch(v.name))


def regen(src):
    im = ImageOps.exif_transpose(Image.open(src))
    icc = im.info.get("icc_profile")
    im = im.convert("RGB")
    out = []
    for v in variants(src):
        with Image.open(v) as old:
            size = old.size
        r = im.resize(size, Image.LANCZOS)
        if v.suffix.lower() == ".webp":
            r.save(v, "WEBP", quality=WEBP_Q, method=6, icc_profile=icc)
        else:
            r.save(v, "JPEG", quality=JPG_Q, optimize=True, progressive=True, icc_profile=icc)
        out.append(v)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sources", nargs="*")
    ap.add_argument("--from-dir", help="a mirror of Images/ (e.g. .tmp/replaced_sources/Images/...): "
                                       "regenerate every source it holds a copy of")
    a = ap.parse_args()
    srcs = [ROOT / s for s in a.sources]
    if a.from_dir:
        base = Path(a.from_dir)
        mirror_root = next(p for p in [base, *base.parents] if p.name == "Images")
        srcs += [ROOT / "Images" / p.relative_to(mirror_root) for p in base.rglob("*") if p.is_file()]
    if not srcs:
        ap.error("name sources or --from-dir")
    total = 0
    for s in sorted(set(srcs)):
        n = len(regen(s))
        total += n
        print(f"  {s.relative_to(ROOT)}: {n} variants")
    print(f"{total} variants rewritten from {len(set(srcs))} sources")


if __name__ == "__main__":
    sys.exit(main())
