#!/usr/bin/env python3
"""Work out what a country's MOBILE landscape dest-card actually renders.

The link preview and the mobile card are both 1.9:1 but were built independently, so the
same country could show two different photos. Aligning them needs three things the card
knows and the OG generator didn't:

  1. the PHOTO. The portrait thumbnail (Images/web/dest-cards/<slug>.jpg) was not always
     made from <Name>_1 - 14 countries used a different shot. There's no record of which,
     so match it back by comparing every Images/dest-cards/<Name>_* candidate against the
     thumbnail and taking the closest.
  2. a dedicated landscape override (#dest-<slug> .dest-bg{background-image:url(...)}).
  3. the vertical framing: background-position Y from styles.css (mobile) if present,
     else the inline one in destinations.html.

Exposes resolve(slug) -> (source_path, bias, how) for gen_og_images.py.

  py tools/dest_card_source.py        # print the resolution table
"""
import glob, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEST = _CSS = None


def _load():
    global _DEST, _CSS
    if _DEST is None:
        _DEST = open(os.path.join(ROOT, "destinations.html"), encoding="utf-8").read()
        _CSS = open(os.path.join(ROOT, "styles.css"), encoding="utf-8").read()
    return _DEST, _CSS


def css_rule(slug):
    """(landscape image or None, bias or None) from the mobile #dest-<slug> override."""
    _, css = _load()
    m = re.search(r"#dest-%s\s+\.dest-bg\{([^}]*)\}" % re.escape(slug), css)
    if not m:
        return None, None
    body = m.group(1)
    mi = re.search(r"url\('([^']+)'\)", body)
    mp = re.search(r"background-position:\s*\d+%\s+(\d+)%", body)
    return (mi.group(1) if mi else None), (int(mp.group(1)) / 100 if mp else None)


def inline_bias(slug):
    dest, _ = _load()
    m = re.search(r"dest-cards/%s\.jpg'[^\"]*background-position:\s*\d+%%\s+(\d+)%%"
                  % re.escape(slug), dest)
    return int(m.group(1)) / 100 if m else 0.5


def match_original(slug):
    """The Images/dest-cards/<Name>_* original the portrait thumbnail was made from."""
    from PIL import Image, ImageChops, ImageStat
    thumb = os.path.join(ROOT, "Images", "web", "dest-cards", "%s.jpg" % slug)
    if not os.path.exists(thumb):
        return None
    t = Image.open(thumb).convert("RGB").resize((96, 96), Image.LANCZOS)
    cands = [slug, slug.replace("-", ""), slug.split("-")[-1]]
    best, score = None, 1e9
    for p in sorted(glob.glob(os.path.join(ROOT, "Images", "dest-cards", "*"))):
        b = os.path.basename(p).lower()
        if not any(b.startswith(c + "_") or b.startswith(c + ".") or b.startswith(c + " ")
                   for c in cands):
            continue
        try:
            from PIL import ImageOps
            im = ImageOps.exif_transpose(Image.open(p)).convert("RGB").resize((96, 96), Image.LANCZOS)
        except Exception:
            continue
        d = sum(ImageStat.Stat(ImageChops.difference(t, im)).mean) / 3
        if d < score:
            best, score = p, d
    return (best, score) if best else None


def resolve(slug):
    """(source image path, vertical bias 0-1, how) that the mobile landscape card shows."""
    land, cbias = css_rule(slug)
    bias = cbias if cbias is not None else inline_bias(slug)
    if land:
        p = os.path.join(ROOT, land.replace("/", os.sep))
        if os.path.exists(p):
            return p, (cbias if cbias is not None else 0.5), "landscape override"
    m = match_original(slug)
    if m and m[1] < 30:
        return m[0], bias, "matched original (d=%.0f)" % m[1]
    # No confident match: the thumbnail was hand-cropped, or made from a shot that isn't in
    # dest-cards. Fall back to the THUMBNAIL rather than a guessed original - a softer image
    # is a far smaller problem than the preview showing a different photo than the card.
    thumb = os.path.join(ROOT, "Images", "web", "dest-cards", "%s.jpg" % slug)
    if os.path.exists(thumb):
        return thumb, bias, "portrait thumb (no hi-res match%s)" % (
            ", best was d=%.0f" % m[1] if m else "")
    return (m[0] if m else None), bias, "unmatched original"


def main():
    slugs = sorted(os.path.basename(os.path.dirname(f))
                   for f in glob.glob(os.path.join(ROOT, "*", "field-notes.html")))
    print("%-18s %-34s %-6s %s" % ("slug", "source the mobile card shows", "y", "how"))
    for s in slugs:
        src, bias, how = resolve(s)
        print("%-18s %-34s %-6s %s" % (s, os.path.basename(src) if src else "-",
                                       "%d%%" % round(bias * 100), how))


if __name__ == "__main__":
    main()
