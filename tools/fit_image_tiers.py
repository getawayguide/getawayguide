#!/usr/bin/env python3
"""Size every body photo's files to where the page actually shows it.

Kevin, 2026-09-30: "these two photos look blurry on the yerevan page" (the view from the Cascade
and the abandoned plane). Two causes, both fixed here:

  * the files were too small. tools/gen_image_tiers.py anchors a photo's 3x file on its existing
    desktop web copy, which was cut for a pair (600 px) or a 576 px column, so a photo shown 686 px
    wide, or a portrait photo filling a 3:2 landscape frame, had nothing sharp to serve: 771 px of
    plane from a 600 px file.
  * `sizes` was a guess. tools/tier_srcsets.py read a portrait photo as a pair photo (283 px) even
    in a full-width landscape frame, so the browser picked a file for a slot half the width.

So this measures instead of guessing. Each page is rendered at 1440 px and at 393 px (a phone);
for every body <picture> the rendered frame gives the width the photo must cover (with object-fit:
cover a photo is drawn at max(frame width, frame height x its aspect)). The 1x/2x/3x files are cut
again from the ARCHIVAL ORIGINAL at those widths (never past its own width; ICC carried through, as
CLAUDE.md requires: these are Display P3), JPEG + WebP, under the same names, and the <picture>
gets the real widths and a `sizes` that says how wide it is drawn: px on a monitor, vw on a phone.
A photo used on several pages is cut for the widest place it appears.

The desktop files are NOT cut at exactly 1x/2x/3x the drawn width (Kevin, 2026-09-30, on his 1x
monitor: "these photos look noticeably worse than before"). Chrome shrinks a photo by halving it
first (a mipmap) and resampling the rest of the way bilinearly, so a file at exactly 1x or exactly
2x the drawn width ends up resampled by a fraction of a pixel, the softest result there is: on
Yerevan's pairs that cost a third of the fine detail against the old 600 px files. Measured in
Chrome (.tmp/tier_sweep.py), detail climbs from 1.0x to a peak near 1.9x and falls off a cliff at
2.0x. So the desktop tiers are 1.85x / 2.8x / 3.7x the drawn width: a 1x monitor gets 1.85x,
1.25x gets 1.5x, a 2x Retina screen 1.4x, and no common screen lands on 1.0x or 2.0x. The names
still say which screen each file is for. The phone tier stays 1x/2x/3x (bytes matter there,
and at 3x density a fraction of a device pixel does not show).

Needs the photo server (http://127.0.0.1:5003) for /site/.

    python tools/fit_image_tiers.py "Drafts/.Full Articles/armenia/yerevan.html" [...] [--dry-run] [--only=TEXT ...]
"""
import io
import math
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import gen_image_tiers as git          # original_for, save, WEB
from tier_srcsets import enc, srcset, PICTURE, IMG

SITE = "http://127.0.0.1:5003/site/"
PHONE_MAX = 430                        # the widest phone the vw sizes are cut for (iPhone Pro Max)
DESK_MULT = (1.85, 2.8, 3.7)           # x the drawn width, for 1x/2x/3x screens: never on 1.0 or 2.0 (see above)
PHONE_MULT = (1, 2, 3)
MEASURE = """() => [...document.querySelectorAll('picture')].map(p => {
  const img = p.querySelector('img'); const r = img.getBoundingClientRect();
  return { src: img.getAttribute('src') || '', w: r.width, h: r.height, hero: !!p.closest('section'),
           shown: r.width > 0 && r.height > 0 && getComputedStyle(img).visibility !== 'hidden' };
})"""


def measure(rels):
    """{page: {width: [{src, w, h, hero, shown}, ...]}} in document order"""
    from playwright.sync_api import sync_playwright
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for rel in rels:
            out[rel] = {}
            for vw, vh in ((1440, 900), (393, 852)):
                pg = b.new_page(viewport={"width": vw, "height": vh})
                pg.goto(SITE + quote(rel), wait_until="load")
                pg.wait_for_timeout(500)
                out[rel][vw] = pg.evaluate(MEASURE)
                pg.close()
        b.close()
    return out


def base_of(src):
    """the page-relative base path of a body photo's files: .../X-mob-2x.jpg -> .../X"""
    m = re.match(r"(.*?)(?:-mob-[123]x|-mob|-[123]x)?\.(?:jpg|jpeg|webp)$", unquote(src), re.I)
    return m.group(1) if m and "/Images/web/" in unquote(src) else None


def widths(need, orig_w, mult=PHONE_MULT):
    """the files for 1x/2x/3x screens for a photo drawn `need` px wide, never past the original, no duplicates"""
    base = int(math.ceil(need / 10.0) * 10)
    out = []
    for k in mult:
        w = min(int(round(base * k)), orig_w)
        if not out or w > out[-1]:
            out.append(w)
    return out


def cut(orig, web_base, desk, mob, dry):
    """write web_base-1x/2x/3x and -mob-1x/2x/3x (.jpg + .webp) at those widths; returns {name: width}"""
    src = ImageOps.exif_transpose(Image.open(orig))
    icc = src.info.get("icc_profile")              # grab BEFORE convert(): Display P3
    rgb = src.convert("RGB")
    made = {}
    for tag, ws in (("", desk), ("-mob", mob)):
        for i, w in enumerate(ws):
            h = round(rgb.height * w / rgb.width)
            im = rgb if w == rgb.width else rgb.resize((w, h), Image.LANCZOS)
            for ext in (".jpg", ".webp"):
                dest = Path(str(web_base) + "%s-%dx%s" % (tag, i + 1, ext))
                git.save(im, dest, icc, dry)
            made["%s-%dx" % (tag, i + 1)] = w
    return made


def run(rels, dry=False, only=()):
    data = measure(rels)
    need = {}                                       # web base (repo path) -> [[desk px per place], phone px at 393, orig, orig width]
    seen = {}                                       # (page, index) -> (web base, desk px here, phone px here)
    for rel, res in data.items():
        page_dir = (ROOT / rel).parent
        for i, d in enumerate(res[1440]):
            m = res[393][i] if i < len(res[393]) else None
            if d["hero"] or not d["shown"]:
                continue
            b = base_of(d["src"])
            if not b:
                continue
            web_base = (page_dir / b).resolve()
            orig = git.original_for(web_base)
            if not orig:
                print("  no original for", web_base.relative_to(ROOT))
                continue
            with Image.open(orig) as im:
                ow, oh = ImageOps.exif_transpose(im).size
            ar = ow / oh
            nd = max(d["w"], d["h"] * ar)                # cover: drawn at the wider of the two
            nm = max(m["w"], m["h"] * ar) if m and m["shown"] else nd * 393 / 1440
            k = str(web_base)
            if only and not any(o.replace("\\", "/").lower() in k.replace("\\", "/").lower() for o in only):
                continue
            cur = need.get(k)
            need[k] = [(cur[0] if cur else []) + [nd], max(nm, cur[1]) if cur else nm, orig, ow]
            seen[(rel, i)] = (k, nd, nm)
    tiers = {}
    for k, (nds, nm, orig, ow) in need.items():
        # one set of files serves every place the photo appears. Cut for the narrowest place when the
        # places are within 1.42x of each other (then every page lands between 1.3x and 1.85x); cut
        # for the widest when they differ more (the narrow page then picks a larger tier). Cutting
        # for the widest always put the narrower page past the 2x cliff (Tacos Shalpa: 319 and 276).
        lo, hi = min(nds), max(nds)
        nd = lo if hi <= 1.42 * lo else hi
        desk = widths(nd, ow, DESK_MULT)            # an original too small for these is served as it is (cutting it smaller only loses pixels)
        mob = widths(nm * PHONE_MAX / 393, ow)
        tiers[k] = (desk, mob, nd, nm)
        made = cut(orig, Path(k), desk, mob, dry)
        print("  %-60s desk %s  phone %s" % (Path(k).relative_to(ROOT / "Images" / "web"), desk, mob))
    # the markup: real widths, and sizes as drawn
    for rel in rels:
        p = ROOT / rel
        raw = io.open(p, encoding="utf-8", newline="").read()
        page_dir = p.parent
        blocks = list(PICTURE.finditer(raw))
        s, pos, out = raw, 0, []
        for i, m in enumerate(blocks):
            hit = seen.get((rel, i))
            if not hit or hit[0] not in tiers:
                continue
            k, nd, nm = hit                            # sizes say how wide THIS page draws it
            desk, mob, _, _ = tiers[k]
            b = base_of(re.search(r'<img\b[^>]*\bsrc="([^"]+)"', m.group(0)).group(1))
            names = lambda tag, n: [b + "%s-%dx" % (tag, j + 1) for j in range(n)]
            d_set = lambda ext: srcset([(enc(u + ext), w) for u, w in zip(names("", len(desk)), desk)])
            m_set = lambda ext: srcset([(enc(u + ext), w) for u, w in zip(names("-mob", len(mob)), mob)])
            S, M = "%dpx" % round(nd), "%dvw" % min(100, round(nm / 393 * 100))
            tag = IMG.search(m.group(0)).group(0)
            keep = re.sub(r'\s(?:src|srcset|sizes)="[^"]*"', "", tag[4:-1].rstrip("/")).strip()
            fallback = names("-mob", len(mob))[min(1, len(mob) - 1)] + ".jpg"
            new = ('<picture><source type="image/webp" media="(min-width:769px)" srcset="%s" sizes="%s">'
                   '<source media="(min-width:769px)" srcset="%s" sizes="%s">'
                   '<source type="image/webp" srcset="%s" sizes="%s">'
                   '<img src="%s" %s srcset="%s" sizes="%s"></picture>'
                   % (d_set(".webp"), S, d_set(".jpg"), S, m_set(".webp"), M, enc(fallback), keep, m_set(".jpg"), M))
            out.append(raw[pos:m.start()])
            out.append(new)
            pos = m.end()
        out.append(raw[pos:])
        s = "".join(out)
        if s != raw and not dry:
            io.open(p, "w", encoding="utf-8", newline="").write(s)
        print("%s%s: %d picture(s) sized" % ("[dry] " if dry else "", rel, sum(1 for (r, _) in seen if r == rel)))


def main():
    rels = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not rels:
        print(__doc__)
        return 2
    # --only=TEXT (repeatable): recut only the photos whose web path contains TEXT; the rest are left alone
    only = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--only=")]
    run(rels, dry="--dry-run" in sys.argv, only=only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
