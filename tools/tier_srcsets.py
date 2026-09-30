#!/usr/bin/env python3
"""Rewrite single-size <picture> blocks into the site's full responsive form.

THE OTHER MISSING STEP. tools/gen_image_tiers.py builds every -1x/-2x/-3x and -mob-1x/-2x/-3x
file (JPEG + WebP), but nothing rewrote a page's <picture> to USE them (workflows/publish_article.md,
Stage 3: "no tool yet turns a <picture> block's srcsets into the 1x/2x/3x form"). The Kosovo field
notes were fixed by a one-off script that is gone; every Armenia article still served one desktop
JPEG with no WebP, and where the folder name has a space ("Orgov Observatory") the unencoded path
split the srcset in two, so desktop fell through to the phone image.

A body photo becomes exactly what the live El Salvador pages carry:

    <picture>
      <source type="image/webp" media="(min-width:769px)" srcset="X-1x.webp W1w, X-2x.webp W2w, X-3x.webp W3w" sizes="S">
      <source                   media="(min-width:769px)" srcset="X-1x.jpg  W1w, X-2x.jpg  W2w, X-3x.jpg  W3w" sizes="S">
      <source type="image/webp" srcset="X-mob-1x.webp w, X-mob-2x.webp w, X-mob-3x.webp w" sizes="M">
      <img src="X-mob-2x.jpg" ... srcset="X-mob-1x.jpg w, X-mob-2x.jpg w, X-mob-3x.jpg w" sizes="M">
    </picture>

The w descriptors are each file's real width. `sizes` is the rendered width the live articles use:
a portrait photo (a pair, 283px on a monitor, 192px on a phone), a landscape one (576px, 393px).
A hero keeps its own shape and only gains its phone 1x (hero-<slug>-mob-1x, 603w) where that file
exists. A block is left alone unless every file it would name is on disk, and a block already in
this form is never touched, so the tool can run again at any time.

    python tools/tier_srcsets.py "Drafts/.Full Articles/armenia/gyumri.html" [...] [--dry-run]
"""
import io
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PICTURE = re.compile(r"<picture>.*?</picture>", re.S)
IMG = re.compile(r"<img\b[^>]*>", re.I)


def attr(tag, name):
    m = re.search(r'\b%s="([^"]*)"' % name, tag)
    return m.group(1) if m else None


def width(p):
    try:
        with Image.open(p) as im:
            return im.width
    except Exception:
        return None


def enc(path):
    """a page-relative URL with every segment percent-encoded once (spaces break a srcset)"""
    return "/".join(quote(unquote(seg), safe="-_.~%()'!,") if seg not in ("..", ".") else seg for seg in path.split("/"))


def tiered(base_url, page_dir, names):
    """[(url, width)] for base + each suffix, or None if any file is missing"""
    out = []
    for n in names:
        url = base_url + n
        f = (page_dir / unquote(url)).resolve()
        w = width(f)
        if not w:
            return None
        out.append((enc(url), w))
    return out


def srcset(pairs):
    return ", ".join("%s %dw" % (u, w) for u, w in pairs)


def body_block(b, page_dir):
    img = IMG.search(b)
    if not img:
        return None, "no <img>"
    tag = img.group(0)
    src = attr(tag, "src") or ""
    m = re.match(r"(.*?)(?:-mob-2x|-mob)?\.(?:jpg|jpeg|webp)$", unquote(src), re.I)
    if not m or "/Images/web/" not in src:
        return None, "not a web copy"
    base = m.group(1)
    d = {}
    for key, names in (("dw", ["-1x.webp", "-2x.webp", "-3x.webp"]), ("dj", ["-1x.jpg", "-2x.jpg", "-3x.jpg"]),
                       ("mw", ["-mob-1x.webp", "-mob-2x.webp", "-mob-3x.webp"]), ("mj", ["-mob-1x.jpg", "-mob-2x.jpg", "-mob-3x.jpg"])):
        d[key] = tiered(base, page_dir, names)
        if not d[key]:
            return None, "missing %s tiers for %s" % (key, Path(base).name)
    try:
        with Image.open((page_dir / (base + "-mob-2x.jpg")).resolve()) as im:
            portrait = im.height > im.width
    except Exception:
        portrait = True
    S, M = ("283px", "192px") if portrait else ("576px", "393px")
    keep = re.sub(r'\s(?:src|srcset|sizes)="[^"]*"', "", tag[4:-1].rstrip("/")).strip()
    new_img = '<img src="%s" %s srcset="%s" sizes="%s">' % (enc(base + "-mob-2x.jpg"), keep, srcset(d["mj"]), M)
    return ('<picture><source type="image/webp" media="(min-width:769px)" srcset="%s" sizes="%s">'
            '<source media="(min-width:769px)" srcset="%s" sizes="%s">'
            '<source type="image/webp" srcset="%s" sizes="%s">%s</picture>'
            % (srcset(d["dw"]), S, srcset(d["dj"]), S, srcset(d["mw"]), M, new_img)), None


def hero_block(b, page_dir):
    """a hero keeps its markup; its phone sources gain the 603px tier when the file exists"""
    out, changed = b, False
    for m in re.finditer(r'(<source media="\(max-width:768px\)"(?: type="image/webp")? srcset=")([^"]+)(")(?: sizes="[^"]*")?(>)', b):
        cands = m.group(2).split(",")
        if len(cands) > 1:
            continue
        url = cands[0].strip().split()[0]
        mm = re.match(r"(.*hero-[a-z0-9-]+?)-mob\.(jpg|webp)$", unquote(url))
        if not mm:
            continue
        pairs = tiered(mm.group(1), page_dir, ["-mob-1x." + mm.group(2), "-mob." + mm.group(2)])
        if not pairs:
            continue
        new = m.group(1) + srcset(pairs) + m.group(3) + ' sizes="100vw"' + m.group(4)
        out = out.replace(m.group(0), new, 1)
        changed = True
    return (out if changed else None), None


def run(rel, dry=False):
    page = ROOT / rel
    raw = io.open(page, encoding="utf-8", newline="").read()
    page_dir = page.parent
    done, skipped = 0, []

    def sub(m):
        nonlocal done
        b = m.group(0)
        if "hero-" in b:
            new, why = hero_block(b, page_dir)
        elif re.search(r"-1x\.(?:webp|jpg) \d+w", b) and re.search(r"-mob-1x\.(?:webp|jpg) \d+w", b):
            return b                                 # already in the full form
        else:
            new, why = body_block(b, page_dir)
        if new is None:
            if why:
                skipped.append(why)
            return b
        done += 1
        return new
    s = PICTURE.sub(sub, raw)
    if s != raw and not dry:
        io.open(page, "w", encoding="utf-8", newline="").write(s)
    return done, skipped


def main():
    dry = "--dry-run" in sys.argv
    pages = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not pages:
        print(__doc__)
        return 2
    for rel in pages:
        n, skipped = run(rel, dry)
        print("%s%s: %d picture(s) tiered%s" % ("[dry] " if dry else "", rel, n,
                                                ", %d left: %s" % (len(skipped), "; ".join(sorted(set(skipped))[:4])) if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
