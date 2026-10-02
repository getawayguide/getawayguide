#!/usr/bin/env python3
"""Size every hero and card background to where the page draws it, at every window width.

Kevin, 2026-10-01: "yes to heroes and cards". A hero fills the window and a card scales with it,
so one file is sharp at some widths and soft at others: Chrome draws an image softest when its
file is about 1.0x or 2.0x the width it is drawn (it halves the image, then resamples the rest by
a fraction of a pixel; sharpness peaks near 1.9x; see tools/fit_image_tiers.py). The Santa Ana
hero, one 3024 px file, was 2.1x on a 1440 window and fine on a 1920 one.

So each background gets files at the widths it needs, and the page chooses between them:

  * the page is loaded at the edges of seven desktop window bands (769, 1024, 1280, 1440, 1680,
    1920, 2240 and up) and every element with a background photo is measured: its box, and so the
    width the photo is drawn at (background-size: cover draws it at the wider of box width and
    box height x the photo's aspect).
  * per band it picks a file 1.35x-1.95x the drawn width for a 1x screen, and 2.0x-2.9x for a
    1.5x-2x one (about 1x of its device pixels, where a fraction of a pixel does not show),
    from a ladder of widths cut from the ARCHIVE ORIGINAL (found the way the Hero Picker finds
    it: its saved picks, the file name, or the pixels), never past the original's own width.
    ICC carried through: these are Display P3.
  * the choices go in a <style id="bg-fit"> in the page's head, keyed on a data-bgfit number the
    tool puts on each element. Only background-image is replaced, so the element's own
    background-position (the crop the Hero Picker sets) still applies. URLs live in the page's
    own style block, not in CSS variables: Chrome resolves a url() inside a variable against the
    stylesheet that uses it, which broke every path under the photo server's /site/ prefix.
  * phones (under 769 px) keep what they have.

Files: <web stem>-<width>w.webp next to the web copy. A file older than the web copy is cut again
(the Hero Picker saved a new photo under the same name). Needs the photo server for /site/.

    python tools/fit_backgrounds.py PAGE.html [...] [--dry-run]
"""
import html as H
import io
import math
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from PIL import Image, ImageChops, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
SITE = "http://127.0.0.1:5003/site/"
# Desktop window bands, each about 8% wide, starting at the site's CSS breakpoints (769, 900, 1100,
# 1200, 1400, 1700) and at common monitor widths. A band must stay sharp up to its last pixel, so a
# coarse band made every window in it take the file for its widest end: seven bands gave a 1440
# window the file for 1679, 2300 px where 2000 had been, and the home page 0.7 MB more (2026-10-01).
_STARTS = [769, 830, 900, 970, 1024, 1100, 1200, 1280, 1366, 1400, 1440, 1536, 1600, 1700, 1800, 1920, 2048, 2200, 2400]
BANDS = [(a, b - 1) for a, b in zip(_STARTS, _STARTS[1:])] + [(_STARTS[-1], 2560)]
ONE_X = (1.35, 1.95)                          # a 1x screen: the sharp band
MAX_W = 3600                                  # 1.35 x a 2560 window; wider files cost storage for 5K screens only


def hi_band(k):
    """A 1.5x-2x screen at band k draws about what a 1x screen draws at 1.5 times that width, so it
    takes that band's 1x file: 0.9x-1.4x of its device pixels, sharp at that density, and no files
    of its own. (A tier of its own made 175 MB of files, 2026-10-01.)"""
    target = min(1.5 * BANDS[k][0], BANDS[-1][0])
    return max(j for j, (a, z) in enumerate(BANDS) if a <= target)
WEBP_Q = 82
CSS_BG = re.compile(r"\.([A-Za-z0-9_-]+)\{[^}]*background-image:url")
MARK = 'data-bgfit="'
BAND_SRC = r'<source type="image/webp" media="\(min-width:\d+px\)[^"]*" srcset="[^"]*-\d+w\.webp">'
DESK_SRC = r'<source type="image/webp" srcset="[^"]+"(?: sizes="[^"]*")?>'   # a hero picture's desktop WebP: no media

MEASURE = """() => [...document.querySelectorAll('[data-bgfit]')].map(e => {
  const cs = getComputedStyle(e), r = e.getBoundingClientRect(), img = e.tagName === 'IMG';
  const m = img ? null : (cs.backgroundImage || '').match(/url\\("?([^")]+)"?\\)/);
  const desk = img ? e.closest('picture').querySelector('source[type="image/webp"]:not([media])') : null;
  const url = img ? (desk ? new URL(desk.getAttribute('srcset').split(',')[0].trim().split(' ')[0], location.href).href : '') : (m ? m[1] : '');
  return { n: e.getAttribute('data-bgfit'), url, w: r.width, h: r.height, img,
           size: img ? (cs.objectFit === 'contain' ? 'contain' : 'cover') : cs.backgroundSize,
           shown: r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden' && !e.closest('nav') };
})"""


def css_bg_classes():
    css = (ROOT / "styles.css").read_text(encoding="utf-8")
    return set(CSS_BG.findall(css))


def tag_page(raw, classes):
    """put data-bgfit="n" on every start tag with an inline background url or a background class"""
    raw = re.sub(r'\s' + MARK + r'\d+"', "", raw)
    n = [0]

    def tag(m):
        t = m.group(0)
        style = re.search(r'\sstyle="([^"]*)"', t)
        cls = re.search(r'\sclass="([^"]*)"', t)
        has = (style and re.search(r"background(?:-image)?\s*:[^;]*url\(", style.group(1), re.I)) or \
              (cls and set(cls.group(1).split()) & classes)
        if not has:
            return t
        n[0] += 1
        end = -2 if t.endswith("/>") else -1
        return t[:end] + ' %s%d"' % (MARK, n[0]) + t[end:]
    body_at = raw.find("<body")
    head, body = raw[:body_at], raw[body_at:]
    body = re.sub(r"<(?:div|section|a|span|li|figure|header|article)\b[^>]*>", tag, body)

    def hero(m):                         # a hero <picture>: its desktop WebP <source> has no media query
        pic = m.group(0)                 # (body photos carry media="(min-width:769px)": fit_image_tiers' job)
        if not (re.search(DESK_SRC, pic)
                and 'media="(max-width:768px)"' in pic and 'media="(min-width:769px)"' not in pic):
            return pic
        n[0] += 1
        return re.sub(r"<img\b", '<img %s%d"' % (MARK, n[0]), pic, count=1)
    body = re.sub(r"<picture>.*?</picture>", hero, body, flags=re.S)
    return head + body, n[0]


def measure(rel, html_text):
    """{window width: {n: box}}, cached by the tagged page's content: a run the laptop's sleep cut
    short (net::ERR_NETWORK_IO_SUSPENDED, 2026-10-01) picks up where it stopped"""
    import hashlib, json
    cache = ROOT / ".tmp" / "bgfit_cache" / (hashlib.sha1((html_text + repr(BANDS)).encode("utf-8")).hexdigest()[:16] + ".json")
    if cache.exists():
        return {int(k): v for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
    out = _measure(rel, html_text)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def _measure(rel, html_text):
    from playwright.sync_api import sync_playwright
    tmp = ROOT / rel
    tmp = tmp.with_name("_bgfit-" + tmp.name)
    tmp.write_text(html_text, encoding="utf-8")
    out = {}
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            # one load, then the window is resized through every band edge: the layout reflows on a
            # resize, and a reload per width took five minutes a page
            pg = b.new_page(viewport={"width": BANDS[0][0], "height": 1000})
            # layout and CSS are all a measurement needs, not the photos
            pg.route(re.compile(r"\.(?:jpe?g|png|webp|gif|avif)(?:\?|$)", re.I), lambda r: r.abort())
            # nor other sites (videos, maps, analytics); fonts stay, since they set text height
            pg.route(re.compile(r"^https?://(?!127\.0\.0\.1|fonts\.(?:googleapis|gstatic)\.com)"), lambda r: r.abort())
            for attempt in range(4):                # a sleeping laptop suspends the network for a while
                try:
                    pg.goto(SITE + quote(tmp.relative_to(ROOT).as_posix()), wait_until="load")
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    pg.wait_for_timeout(15000)
            pg.wait_for_timeout(300)
            for vw in sorted({w for band in BANDS for w in band}):
                pg.set_viewport_size({"width": vw, "height": 1000})
                pg.wait_for_timeout(60)
                out[vw] = {x["n"]: x for x in pg.evaluate(MEASURE)}
            pg.close()
            b.close()
    finally:
        tmp.unlink()
    return out


def web_path(url):
    p = unquote(urlparse(url).path)
    return p.split("/site/", 1)[1] if "/site/" in p else p.lstrip("/")


_src_cache = {}


SRC_CACHE = ROOT / ".tmp" / "bgfit_sources.json"


def _open(path, angle):
    im0 = Image.open(path)
    icc = im0.info.get("icc_profile")
    im = ImageOps.exif_transpose(im0).convert("RGB")
    if abs(angle) > 1e-3:
        import hero_picker as HP
        im = HP.straighten(im, angle)
    return im, icc


def source_for(img, rel):
    """(PIL image of the best source, its ICC) for a web copy: the archive original when one is found
    and matches the web copy's pixels, else the largest local version of the same photo. The choice
    is remembered (path and angle, against the web copy's time), since finding it decodes candidate
    originals: about 13 seconds a photo, an hour over the site."""
    import json
    key = (img, rel)
    if key in _src_cache:
        return _src_cache[key]
    mt = (ROOT / img).stat().st_mtime
    try:
        known = json.loads(SRC_CACHE.read_text(encoding="utf-8")) if SRC_CACHE.exists() else {}
    except Exception:
        known = {}
    k = known.get(img)
    if k and k.get("mtime") == mt and Path(k["path"]).exists():
        _src_cache[key] = _open(k["path"], k.get("angle", 0.0))
        return _src_cache[key]
    web = ImageOps.exif_transpose(Image.open(ROOT / img)).convert("RGB")
    cands = []                                       # (path, angle)
    try:
        import hero_picker as HP
        hit = HP._web_original(img, rel)
        if hit:
            cands.append((str(HP.resolve_src(hit[0])), float(hit[1] or 0)))
    except Exception as e:
        print("   (no original via the Hero Picker for %s: %s)" % (img, str(e)[:80]))
    stem = re.sub(r"(-[123]x|-2x|-mob(-1x)?)$", "", Path(img).stem)
    for f in (ROOT / img).parent.glob(stem + "*.*"):
        if f.suffix.lower() in (".jpg", ".jpeg", ".webp", ".png") and not re.search(r"-\d+w$|-mob", f.stem):
            cands.append((str(f), 0.0))
    ref = web.resize((64, max(1, round(64 * web.height / web.width))))
    best_path, best_angle, best_w = str(ROOT / img), 0.0, web.width
    opened = []
    for path, angle in cands:
        try:
            opened.append((path, angle) + _open(path, angle))
        except Exception:
            continue
    for path, angle, im, icc in sorted(opened, key=lambda c: -c[2].width):
        if abs(im.width / im.height - web.width / web.height) > 0.02 or im.width <= best_w:
            continue
        d = ImageStat.Stat(ImageChops.difference(im.resize(ref.size), ref)).mean
        if sum(d) / 3 <= 10:                         # the same photo, same framing
            best_path, best_angle, best_w = path, angle, im.width
            break
    known[img] = {"mtime": mt, "path": best_path, "angle": best_angle}
    try:
        SRC_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SRC_CACHE.write_text(json.dumps(known, indent=0, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    _src_cache[key] = _open(best_path, best_angle)
    return _src_cache[key]


def cover_points(intervals, src_w):
    """The fewest widths such that every acceptable range [lo, hi] holds one of them: the classic
    interval point cover, solved exactly by taking ranges in order of their upper end and placing
    a width at that end whenever the range is not covered yet. A range the original cannot reach
    is served the original's own width."""
    pts = []
    for lo, hi in sorted(intervals, key=lambda x: min(x[1], src_w)):
        if lo >= src_w:
            lo = hi = src_w
        hi = min(hi, src_w)
        if any(lo - 0.5 <= p <= hi + 0.5 for p in pts):
            continue
        pts.append(src_w if hi >= src_w else int(math.floor(hi)))
    return sorted(set(pts))


def pick(lo, hi, pts, src_w):
    """the lightest of the chosen widths that fits [lo, hi]"""
    if lo >= src_w:
        return src_w
    hi = min(hi, src_w)
    fits = [p for p in pts if lo - 0.5 <= p <= hi + 0.5]
    return min(fits) if fits else max(pts)


def drawn(m, ar):
    if m["size"] == "contain":
        return min(m["w"], m["h"] * ar)
    return max(m["w"], m["h"] * ar)                  # cover (and anything that fills the box)


def scope_for(tagged, n, img):
    """A rule applies only while the element still shows this photo: the file name as its inline
    style spells it. The Hero Picker may point the card at another photo later; the rule then stops
    matching and the element shows what the Hero Picker wrote until the next fit, instead of the
    old photo. A background that comes from a class has no inline style, and no condition."""
    m = re.search(r"<[^>]*\s%s%s\"[^>]*>" % (re.escape(MARK), n), tagged)
    style = re.search(r'\sstyle="([^"]*)"', m.group(0)) if m else None
    if not style:
        return ""
    stem = Path(img).stem
    for u in re.findall(r"url\(\s*['\"]?([^'\")]+)", style.group(1)):
        base = u.rsplit("/", 1)[-1]
        if unquote(base).rsplit(".", 1)[0].replace("-1x", "").replace("-2x", "") .startswith(stem.replace("-1x", "")):
            return '[style*="%s"]' % base.rsplit(".", 1)[0].replace('"', "")
    return ""


def refresh(web_file):
    """Cut every <stem>-<width>w.webp of this web copy again from its source: the Hero Picker saved a
    new photo under the same name. No page is measured; the widths stay what the pages ask for."""
    img = Path(web_file).as_posix()
    stem = re.sub(r"(-[123]x)$", "", Path(img).stem)
    files = [f for f in (ROOT / img).parent.glob(stem + "-*w.webp") if re.fullmatch(re.escape(stem) + r"-\d+w", f.stem)]
    if not files:
        return 0
    src, icc = source_for(img, "")
    for f in files:
        w = min(int(re.search(r"-(\d+)w$", f.stem).group(1)), src.width)
        im = src if w == src.width else src.resize((w, round(src.height * w / src.width)), Image.LANCZOS)
        im.save(f, "WEBP", quality=WEBP_Q, method=6, **({"icc_profile": icc} if icc else {}))
    print("refreshed %d file(s) for %s" % (len(files), img))
    return len(files)


def file_for(img, w):
    stem = re.sub(r"(-[123]x)$", "", Path(img).stem)
    return (ROOT / img).with_name("%s-%dw.webp" % (stem, w))


def run(rels, dry=False):
    """all pages at once, so a photo used on several (a destination card on the home, posts and
    destinations pages) is cut once, at the fewest widths that serve every place it appears"""
    classes = css_bg_classes()
    pages, ivals, srcs = {}, {}, {}
    for rel in rels:
        raw = (ROOT / rel).read_text(encoding="utf-8", newline="")
        clean = re.sub(r'\s*<style id="bg-fit">.*?</style>', "", raw, flags=re.S)
        clean = re.sub(BAND_SRC, "", clean)              # a previous run's band sources for hero images
        tagged, count = tag_page(clean, classes)
        if not count:
            print("%s: no background photos" % rel)
            continue
        meas = measure(rel, tagged)
        items = []
        for n in sorted({k for v in meas.values() for k in v}, key=int):
            first = meas[BANDS[0][0]].get(n)
            if not first or not first["url"] or "/Images/web/" not in "/" + web_path(first["url"]) or not first["shown"]:
                continue
            if first["size"] not in ("cover", "contain"):
                continue
            img = web_path(first["url"])
            if not (ROOT / img).exists() or img.endswith(".svg"):
                continue
            if img not in srcs:
                srcs[img] = source_for(img, rel)
            ar = srcs[img][0].width / srcs[img][0].height
            iv = {"1x": [None] * len(BANDS)}
            for k, (a, z) in enumerate(BANDS):
                ma, mz = meas[a].get(n), meas[z].get(n)
                if not ma or not mz or not ma["shown"]:
                    continue
                da, dz = drawn(ma, ar), drawn(mz, ar)
                for tier, (f_lo, f_hi) in (("1x", ONE_X),):
                    lo, hi = f_lo * max(da, dz), f_hi * min(da, dz)
                    if lo > hi:                       # a band too wide for one file: its middle
                        lo = hi = (lo + hi) / 2
                    iv[tier][k] = (lo, hi)
                    ivals.setdefault(img, []).append((lo, hi))
            items.append((n, img, bool(first.get("img")), iv))
        pages[rel] = (raw, tagged, items)

    cap = {img: min(srcs[img][0].width, MAX_W) for img in ivals}
    chosen = {img: cover_points(v, cap[img]) for img, v in ivals.items()}
    made = [0]

    def ensure(img, w):                             # cut a file only when a band actually uses it
        f = file_for(img, w)
        if not dry and (not f.exists() or f.stat().st_mtime < (ROOT / img).stat().st_mtime):
            src, icc = srcs[img]
            im = src if w == src.width else src.resize((w, round(src.height * w / src.width)), Image.LANCZOS)
            im.save(f, "WEBP", quality=WEBP_Q, method=6, **({"icc_profile": icc} if icc else {}))
            made[0] += 1
        return f

    sig = lambda t: re.findall(r"<[^>]*\sdata-bgfit=\"\d+\"[^>]*>", t)
    for rel, (raw, tagged, items) in pages.items():
        # a page saved while this ran (the editor is open while it measures): fit the saved page, if its
        # photos are the ones measured; otherwise leave it for the next run rather than write over it
        now = (ROOT / rel).read_text(encoding="utf-8", newline="")
        if now != raw:
            fresh = re.sub(BAND_SRC, "", re.sub(r'\s*<style id="bg-fit">.*?</style>', "", now, flags=re.S))
            fresh_tagged, _ = tag_page(fresh, classes)
            if sig(fresh_tagged) != sig(tagged):
                print("%s: changed while this ran, and its photos with it; left as saved. Run again." % rel)
                continue
            raw, tagged = now, fresh_tagged
        up = "../" * (len(Path(rel).parts) - 1)
        rules = {"1x": [[] for _ in BANDS], "hi": [[] for _ in BANDS]}
        pics = {}
        for n, img, is_img, iv in items:
            per = {t: [None] * len(BANDS) for t in iv}
            per["hi"] = [None] * len(BANDS)
            for t in iv:
                for k, x in enumerate(iv[t]):
                    if x:
                        w = pick(x[0], x[1], chosen[img], cap[img])
                        per[t][k] = up + quote(ensure(img, w).relative_to(ROOT).as_posix())
            for k in range(len(BANDS)):
                per["hi"][k] = per["1x"][hi_band(k)] or per["1x"][k]
            if is_img:
                pics[n] = per
                continue
            scope = scope_for(tagged, n, img)
            for t in per:                               # a background: a rule wherever its file changes
                prev = None
                for k, href in enumerate(per[t]):
                    if href and href != prev:
                        rules[t][k].append("[data-bgfit=\"%s\"]%s{background-image:url('%s')!important}" % (n, scope, href))
                        prev = href
        out = tagged
        if any(rules[t][k] for t in rules for k in range(len(BANDS))):
            css = ["/* tools/fit_backgrounds.py: each background photo at the width this window draws it. Do not edit by hand. */"]
            for t, res in (("1x", ""), ("hi", " and (min-resolution:1.5dppx)")):
                for k, (a, z) in enumerate(BANDS):
                    if rules[t][k]:
                        css.append("@media (min-width:%dpx)%s{%s}" % (a, res, "".join(rules[t][k])))
            out = out.replace("</head>", '<style id="bg-fit">\n' + "\n".join(css) + "\n</style>\n</head>", 1)
        for n, per in pics.items():                     # a hero <img>: one <source> per run of bands, sharper screens first
            parts = []
            for t, res in (("hi", " and (min-resolution:1.5dppx)"), ("1x", "")):
                runs = []
                for k, href in enumerate(per[t]):
                    if not href:
                        continue
                    if runs and runs[-1][2] == href and runs[-1][1] == k - 1:
                        runs[-1][1] = k
                    else:
                        runs.append([k, k, href])
                for k0, k1, href in runs:
                    top = "" if k1 == len(BANDS) - 1 else " and (max-width:%.2fpx)" % (BANDS[k1][1] + 0.98)
                    parts.append('<source type="image/webp" media="(min-width:%dpx)%s%s" srcset="%s">' % (BANDS[k0][0], top, res, href))
            joined = "".join(parts)

            def swap(m, n=n, joined=joined):
                if (MARK + n + '"') not in m.group(0):
                    return m.group(0)
                # in front of the picture's own desktop source, which stays: the bands cover every desktop
                # width, and a later run still recognises the hero by it
                return re.sub(DESK_SRC, lambda d: joined + d.group(0), m.group(0), count=1)
            out = re.sub(r"<picture>.*?</picture>", swap, out, flags=re.S)
        if not items:
            out = raw
        if not dry and out != raw:
            (ROOT / rel).write_text(out, encoding="utf-8", newline="")
        print("%s%s: %d photo(s) fitted (%d hero image(s))" % ("[dry] " if dry else "", rel, len(items), len(pics)))
    files = sum(len(v) for v in chosen.values())
    print("%s%d photo(s), %d file(s) in all (%.1f each), %d cut now" % ("[dry] " if dry else "", len(chosen), files,
          files / max(1, len(chosen)), made[0]))


def main():
    rels = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not rels:
        print(__doc__)
        return 2
    if "--refresh" in sys.argv:                 # the Hero Picker saved a new photo: recut its files
        for r in rels:
            refresh(r)
        return 0
    if "--rescope" in sys.argv:                 # add each rule's still-this-photo condition, no measuring
        for rel in rels:
            p = ROOT / rel
            h = p.read_text(encoding="utf-8", newline="")

            def fix(m):
                n, href = m.group(1), m.group(3)
                stem = re.sub(r"-\d+w$", "", Path(unquote(href)).stem)
                return '[data-bgfit="%s"]%s{background-image:url(\'%s\')!important}' % (n, scope_for(h, n, stem + ".x"), href)
            new = re.sub(r'\[data-bgfit="(\d+)"\](\[style\*="[^"]*"\])?\{background-image:url\(\'([^\']+)\'\)!important\}', fix, h)
            if new != h:
                p.write_text(new, encoding="utf-8", newline="")
            print("%s: %d rule(s) scoped" % (rel, len(re.findall(r'\]\[style\*=', new))))
        return 0
    run(rels, dry="--dry-run" in sys.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
