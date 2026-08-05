#!/usr/bin/env python3
"""Give the MOBILE tier WebP, the way desktop already has it.

Each article <picture> currently reads:

    <source type="image/webp" media="(min-width:769px)" srcset="…-1x.webp …-3x.webp">
    <source                   media="(min-width:769px)" srcset="…-1x.jpg  …-3x.jpg">
    <img src="…original…" srcset="…-mob-1x.jpg …-mob-3x.jpg">

so desktop gets WebP and mobile is left on JPEG - backwards, since phones are the
bandwidth-constrained ones. There are 85 desktop WebP variants and 0 mobile ones.

This encodes a WebP beside every `-mob-*.jpg` and inserts a mobile
`<source type="image/webp">` ahead of the <img>, which browsers pick over the JPEG srcset.
It also repoints the <img src> fallback: it currently names the FULL-RESOLUTION original
(up to 13 MB), which any client ignoring srcset would download.

Originals under Images/ are never read for output beyond encoding, and never modified.

  py tools/gen_mobile_webp.py --dry-run
  py tools/gen_mobile_webp.py
"""
import glob, html as htmlmod, os, re, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRY = "--dry-run" in sys.argv
QUALITY = 80

IMG_TAG = re.compile(r"<img\b[^>]*>")
PICTURE = re.compile(r"<picture>.*?</picture>", re.S)


def encode(jpg):
    """Write <same>.webp next to a -mob-*.jpg. Returns (made, saved_kb)."""
    webp = os.path.splitext(jpg)[0] + ".webp"
    if os.path.exists(webp):
        return False, 0
    if DRY:
        return True, 0
    from PIL import Image
    im = Image.open(jpg).convert("RGB")
    im.save(webp, "WEBP", quality=QUALITY, method=6)
    return True, (os.path.getsize(jpg) - os.path.getsize(webp)) / 1024


def main():
    jpgs = sorted(glob.glob(os.path.join(ROOT, "Images", "web", "**", "*-mob-*.jpg"),
                            recursive=True))
    made = saved = 0
    for j in jpgs:
        ok, s = encode(j)
        made += ok
        saved += s
    print("%smobile webp: %d encoded (of %d -mob- jpgs), saved %.1f MB"
          % ("[dry] " if DRY else "", made, len(jpgs), saved / 1024))

    # ---- rewrite the <picture> blocks -------------------------------------------------
    pages = sorted(glob.glob(os.path.join(ROOT, "*", "*.html")))
    tot_src = tot_ws = 0
    for p in pages:
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.startswith(("Drafts/", ".tmp/")):
            continue
        s = open(p, encoding="utf-8").read()
        orig = s

        def fix(m):
            nonlocal tot_src, tot_ws
            blk = m.group(0)
            tag = IMG_TAG.search(blk)
            if not tag:
                return blk
            t = tag.group(0)
            ss = re.search(r'srcset="([^"]+)"', t)
            if not ss or "-mob-" not in ss.group(1):
                return blk
            jpg_set = ss.group(1)

            # a webp mobile <source>, only if every candidate file exists
            webp_set = jpg_set.replace(".jpg", ".webp")
            ok = True
            for part in webp_set.split(","):
                u = part.strip().split()[0]
                fp = os.path.join(os.path.dirname(p), urllib.parse.unquote(u).replace("/", os.sep))
                if not os.path.exists(fp):
                    ok = False
                    break
            # `<source>` is a void element, so there is no </source> to split on - detect an
            # existing MOBILE webp source by its srcset instead (the desktop one is always
            # present and would otherwise always match)
            if ok and webp_set not in blk:
                sizes = re.search(r'sizes="([^"]+)"', t)
                src_tag = '<source type="image/webp" srcset="%s"%s>' % (
                    webp_set, (' sizes="%s"' % sizes.group(1)) if sizes else "")
                blk = blk.replace("<img", src_tag + "<img", 1)
                tot_ws += 1

            # fallback src -> the mid mobile jpg, not the full-res original
            cands = [c.strip().split()[0] for c in jpg_set.split(",")]
            mid = cands[min(1, len(cands) - 1)]
            new_t = re.sub(r'src="[^"]*"', 'src="%s"' % mid, tag.group(0), count=1)
            if new_t != tag.group(0):
                blk = blk.replace(tag.group(0), new_t, 1)
                tot_src += 1
            return blk

        s = PICTURE.sub(fix, s)
        if s != orig and not DRY:
            open(p, "w", encoding="utf-8", newline="").write(s)
        if s != orig:
            print("  %s" % rel)
    print("%sadded %d mobile <source webp>, repointed %d src fallback(s)"
          % ("[dry] " if DRY else "", tot_ws, tot_src))


if __name__ == "__main__":
    main()
