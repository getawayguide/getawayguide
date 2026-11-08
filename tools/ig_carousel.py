r"""
Itinerary carousel + Pinterest pins: the template mode of the IG post maker (tools/ig_post.py + tools/ig_post_page.html).

The free mode of the post maker is "any 10 photos, cropped". This is the branded TEMPLATE mode Kevin asked for
(2026-11-06, after BucketListly / peachananr's itinerary carousels; his own look, not theirs):

    1. cover        the best photo full bleed, a small kicker ("A complete itinerary") over a big title ("7 Days in Armenia")
    2. route map    the itinerary map (tools/itinerary_map.py) re-built at Instagram scale, with the day list under it
    3+. one per stop  a 2x2 grid of square photos over a green label bar ("DAY 1-3" / "Yerevan" / one line about it)
    last (optional) the call to action ("Comment GUIDE")
  plus a caption: a 2-paragraph hook, the CTA line, the day-by-day list and a keyword line (and hashtags).

The slide markup and CSS live ONCE, in tools/ig_post_page.html (<style id="cs-css"> and <script id="cs-js">); the page
previews them and this tool renders the very same markup in headless Chromium at 1080x1350, device_scale_factor 1, so the
text is drawn sharp at 1x and every photo is cut from its ORIGINAL to the exact pixel size of its box (no browser resample).

STEPS (Claude runs these; Kevin edits the plan in the artifact)
  python tools/ig_carousel.py armenia seed
      tools/ig_carousel/<slug>.json (the hand-picked first draft: title, stops, photo ids, caption, map overrides)
      -> .tmp/ig/<slug>/carousel/main.json  (the carousel/main doc for the artifact db)
         .tmp/ig/<slug>/carousel/map.json   (carousel/map: the route map SVG, built at Instagram scale)
      then  ArtifactData set url=<artifact> collection=carousel doc_id=main file_path=.tmp/ig/<slug>/carousel/main.json
            ArtifactData set url=<artifact> collection=carousel doc_id=map  file_path=.tmp/ig/<slug>/carousel/map.json
  python tools/ig_carousel.py armenia render [--state DIR] [--out DIR]
      DIR = an ArtifactData out_dir holding carousel/main.json (+ map.json); default = the seed output.
      -> NN-<slide>.png at 1080x1350 (sRGB profile embedded) + caption.txt, into --out
         (default .tmp/ig/<slug>/carousel/out). Photo ids are the ig_post manifest's (.tmp/ig/<slug>/manifest.json),
         so `python tools/ig_post.py "<Country>"` must have run first.
  python tools/ig_carousel.py armenia pins [--out DIR]
      the seed's "pins" list -> one 1000x1500 Pinterest pin per article (photo + the article's h1 + kicker).

PLAN SHAPE (carousel/main)
  {country, slug, status, title ("7 Days in|*Armenia*": | breaks the line, *x* is italic), kicker, swipe, mark,
   cover: {id, crop}, map: {kicker, title},
   stops: [{days: "Day 1–3", name, sub, list (map label, default name), caption (caption line, default list/name),
            slide: true|false (false = listed on the map and in the caption only), photos: [{id, crop} | null] x4}],
   cta: {on, kicker, headline, body, keyword, site, photo: {id, crop}},
   caption: {hook, cta, days, keywords, hashtags}}
  crop = {cx, cy, w, rot} on the EXIF-upright original, the same convention as the free mode (tools/ig_post.py).
"""

import argparse
import copy
import io
import json
import re
import shutil
import sys
from html import unescape
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ig_post  # noqa: E402  (open_upright, to_srgb, clamp_crop, SRGB_BYTES, slugify)

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "tools" / "ig_post_page.html"
SEEDS = ROOT / "tools" / "ig_carousel"
TMP = ROOT / ".tmp" / "ig"
FONTS_CSS = (ROOT / "fonts.css").as_uri()

BOX = {"cover": (1080, 1350), "cell": (497, 497), "cta": (1008, 672)}
PIN_W, PIN_H, PIN_PHOTO_H = 1000, 1500, 960


def out(s=""):
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


# ------------------------------------------------------------------ shared slide code from the page

def shared_blocks():
    t = PAGE.read_text(encoding="utf-8")
    css = re.search(r'<style id="cs-css">(.*?)</style>', t, re.S)
    js = re.search(r'<script id="cs-js">(.*?)</script>', t, re.S)
    if not css or not js:
        raise SystemExit("tools/ig_post_page.html has no cs-css / cs-js block")
    return css.group(1), js.group(1)


# ------------------------------------------------------------------ photos

def manifest(slug):
    p = TMP / slug / "manifest.json"
    if not p.is_file():
        raise SystemExit("no %s: run  python tools/ig_post.py \"<Country>\"  first" % p)
    return {x["id"]: x for x in json.loads(p.read_text(encoding="utf-8"))["photos"]}


def cut(orig, crop, w, h):
    """One photo box from the full-resolution original at exactly w x h: tilt + crop as one affine map (the same math
    as ig_post.render_one and the page's clampCrop), a Lanczos pre-reduce first, Display P3 -> sRGB at the end."""
    import math
    ratio = h / w
    im, icc = ig_post.open_upright(orig)
    W, H = im.size
    cx, cy, fw, rot = ig_post.clamp_crop(crop or {}, W, H, ratio)
    im = im.convert("RGB")
    k = fw / w
    if k > 1.6:
        f = k / 1.3
        im = im.resize((max(1, round(W / f)), max(1, round(H / f))), Image.LANCZOS)
        cx, cy, fw = cx / f, cy / f, fw / f
        k = fw / w
    th = math.radians(rot)
    a, b = k * math.cos(th), k * math.sin(th)
    d, e = -k * math.sin(th), k * math.cos(th)
    c0 = cx - a * (w / 2) - b * (h / 2)
    f0 = cy - d * (w / 2) - e * (h / 2)
    res = im.transform((w, h), Image.AFFINE, (a, b, c0, d, e, f0), resample=Image.BICUBIC)
    return ig_post.to_srgb(res, icc)


def photo_refs(plan):
    """{box key: (ref, (w, h))} for every filled photo box of the plan."""
    refs = {}
    if (plan.get("cover") or {}).get("id"):
        refs["cover"] = (plan["cover"], BOX["cover"])
    for i, s in enumerate(plan.get("stops") or []):
        if s.get("slide") is False:
            continue
        for j, r in enumerate((s.get("photos") or [])[:4]):
            if r and r.get("id"):
                refs["s%d-%d" % (i, j)] = (r, BOX["cell"])
    c = plan.get("cta") or {}
    if c.get("on") is not False and (c.get("photo") or {}).get("id"):
        refs["cta"] = (c["photo"], BOX["cta"])
    return refs


# ------------------------------------------------------------------ the route map at Instagram scale

def ig_map_svg(seed):
    """The country's itinerary map, re-built for a 1008 px box on a phone: the seed's "map" block names the
    tools/itinerary_maps config and lays overrides over it (top-level keys replace; "stops"/"dots"/"legs" patch
    entries by index), plus "afs" = the annotation scale (labels, pins and pills grow, the outline does not)."""
    import itinerary_map
    m = seed.get("map") or {}
    cfg = json.loads((ROOT / "tools" / "itinerary_maps" / (m.get("config", seed["slug"]) + ".json")).read_text(encoding="utf-8"))
    for k, v in (m.get("override") or {}).items():
        if k in ("stops", "dots", "legs") and isinstance(v, dict):
            for i, patch in v.items():
                cfg[k][int(i)].update(patch)
        else:
            cfg[k] = v
    svg, vw, vh = itinerary_map.build({**cfg, "_afs": float(m.get("afs", 1.0))})
    svg = re.sub(r"^<svg\b", '<svg preserveAspectRatio="xMidYMid meet"', svg.strip(), count=1)
    return svg, vw, vh


# ------------------------------------------------------------------ seed

def cmd_seed(args):
    slug = ig_post.slugify(args.country)
    seed = json.loads((SEEDS / (slug + ".json")).read_text(encoding="utf-8"))
    man = manifest(slug)
    plan = copy.deepcopy(seed["plan"])
    plan.update({"country": seed["country"], "slug": slug, "status": "draft"})
    missing = [r["id"] for r, _ in photo_refs(plan).values() if r["id"] not in man]
    if missing:
        raise SystemExit("photo ids not in the manifest: " + ", ".join(missing))
    if not (plan.get("caption") or {}).get("days"):
        plan.setdefault("caption", {})["days"] = "\n".join(
            "%s: %s" % (s["days"], s.get("caption") or s.get("list") or s["name"]) for s in plan["stops"])
    svg, vw, vh = ig_map_svg(seed)
    base = TMP / slug / "carousel"
    base.mkdir(parents=True, exist_ok=True)
    (base / "main.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    (base / "map.json").write_text(json.dumps({"svg": svg, "vw": vw, "vh": vh, "config": (seed.get("map") or {}).get("config", slug)},
                                              ensure_ascii=False), encoding="utf-8")
    out("seeded %s: %d stops (%d with a slide), map %dx%d (%d bytes)" % (
        base / "main.json", len(plan["stops"]), sum(1 for s in plan["stops"] if s.get("slide") is not False), vw, vh, len(svg)))


# ------------------------------------------------------------------ render

def load_plan(slug, state):
    """(plan, map svg) from an ArtifactData out_dir (carousel/main.json + carousel/map.json), a folder holding
    main.json / map.json, or one plan JSON file."""
    def body(p):
        return ig_post._doc(json.loads(p.read_text(encoding="utf-8")))
    p = Path(state) if state else TMP / slug / "carousel"
    if p.is_file():
        plan, mp = body(p), p.with_name("map.json")
    else:
        d = p / "carousel" if (p / "carousel" / "main.json").is_file() else p
        plan, mp = body(d / "main.json"), d / "map.json"
    if not mp.is_file():
        mp = TMP / slug / "carousel" / "map.json"
    svg = body(mp).get("svg", "") if mp.is_file() else ""
    return plan, svg


def chromium():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    b = pw.chromium.launch(args=["--allow-file-access-from-files", "--force-color-profile=srgb", "--font-render-hinting=none"])
    return pw, b


def save_png(raw, dst):
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.save(dst, "PNG", icc_profile=ig_post.SRGB_BYTES, optimize=True)
    return im.size


def cmd_render(args):
    slug = ig_post.slugify(args.country)
    man = manifest(slug)
    plan, svg = load_plan(slug, args.state)
    css, js = shared_blocks()
    work = TMP / slug / "carousel" / "cells"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    imgs = {}
    for key, (ref, (w, h)) in photo_refs(plan).items():
        p = man.get(ref["id"])
        if not p:
            out("  ! %s: %s is not in the manifest, left empty" % (key, ref["id"]))
            continue
        dst = work / (key + ".png")
        cut(ROOT / p["original"], ref.get("crop"), w, h).save(dst, "PNG")  # lossless: the slide PNG is the only encode
        imgs[key] = dst.as_uri()
        out("  cut %-6s %4dx%-4d %s" % (key, w, h, p["original"]))
    html = ("<!doctype html><html><head><meta charset='utf-8'><link rel='stylesheet' href='%s'><style>%s</style>"
            "<style>html,body{margin:0;background:#888;overflow:hidden}.cs{display:none}.cs.on{display:block}</style></head><body><div id='root'></div>"
            "<script>%s</script><script>const PLAN=%s, SVG=%s, IMGS=%s;"
            "const photo=(k,w,h)=>IMGS[k]?`<img src=\"${IMGS[k]}\" width=\"${w}\" height=\"${h}\" alt=\"\">`:'';"
            "const list=CS.slides(PLAN, photo, SVG); document.getElementById('root').innerHTML=list.map(s=>s.html).join('');"
            "window.__keys=list.map(s=>s.key);"
            "Promise.all([document.fonts.ready, ...[...document.images].map(i=>i.decode().catch(()=>0))]).then(()=>{document.querySelectorAll('.cs').forEach(e=>{e.classList.add('on');CS.fit(e);e.classList.remove('on');});"
            "window.__fonts=['400 30px Newsreader','italic 300 30px Newsreader','600 30px \"Hanken Grotesk\"'].map(f=>document.fonts.check(f));window.__ready=1;});"
            "</script></body></html>") % (FONTS_CSS, css, js, json.dumps(plan, ensure_ascii=False), json.dumps(svg), json.dumps(imgs))
    page_file = TMP / slug / "carousel" / "render.html"
    page_file.write_text(html, encoding="utf-8")
    dest = Path(args.out) if args.out else TMP / slug / "carousel" / "out"
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.png"):
        old.unlink()
    pw, b = chromium()
    try:
        pg = b.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(page_file.as_uri())
        pg.wait_for_function("window.__ready === 1", timeout=30000)
        fonts = pg.evaluate("window.__fonts")
        if not all(fonts):
            out("  ! fonts missing in the render: %s" % fonts)
        keys = pg.evaluate("window.__keys")
        for n, key in enumerate(keys, 1):
            # one slide on screen at a time, at the top-left of a 1080x1350 viewport: the capture is that viewport, 1:1
            pg.evaluate("k => { document.querySelectorAll('.cs').forEach(e => e.classList.toggle('on', e.dataset.slide === k)); "
                        "return new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); }", key)
            raw = pg.screenshot(type="png", clip={"x": 0, "y": 0, "width": 1080, "height": 1350}, animations="disabled")
            size = save_png(raw, dest / ("%02d-%s.png" % (n, key)))
            out("  slide %02d %-6s %dx%d" % (n, key, size[0], size[1]))
        if errs:
            out("  ! page errors: %s" % errs)
    finally:
        b.close()
        pw.stop()
    cap = caption_text(plan)
    (dest / "caption.txt").write_text(cap + "\n", encoding="utf-8")
    out("caption: %d characters (Instagram's cap is 2,200)" % len(cap))
    out("rendered %d slides into %s" % (len(keys), dest))


def caption_text(plan):
    c = plan.get("caption") or {}
    return "\n\n".join(str(c.get(k) or "").strip() for k in ("hook", "cta", "days", "keywords", "hashtags") if str(c.get(k) or "").strip())


# ------------------------------------------------------------------ Pinterest pins

PIN_CSS = """
.pin { position: relative; width: 1000px; height: 1500px; overflow: hidden; background: #F7F7F3; color: #1C2821;
  font-family: 'Hanken Grotesk', sans-serif; -webkit-font-smoothing: antialiased; margin: 0 0 20px }
.pin .ph { position: absolute; left: 0; top: 0; width: 1000px; height: %(ph)dpx }
.pin .ph img { display: block; width: 100%%; height: 100%% }
.pin .tag { position: absolute; top: 44px; left: 48px; background: rgba(247,247,243,.94); color: #2D6B50; font-weight: 600; font-size: 22px;
  letter-spacing: .2em; text-transform: uppercase; padding: 14px 22px 13px; border-radius: 999px }
.pin .panel { position: absolute; left: 0; right: 0; top: %(ph)dpx; bottom: 120px; padding: 0 64px; display: flex; flex-direction: column; justify-content: center }
.pin .k { font-weight: 600; font-size: 24px; letter-spacing: .22em; text-transform: uppercase; color: #2D6B50 }
.pin .t { font-family: 'Newsreader', Georgia, serif; font-weight: 400; font-size: 96px; line-height: 1.0; letter-spacing: -.02em; margin-top: 18px }
.pin .s { font-family: 'Newsreader', Georgia, serif; font-style: italic; font-weight: 300; font-size: 44px; line-height: 1.12; color: #2D6B50; margin-top: 14px }
.pin .foot { position: absolute; left: 64px; right: 64px; bottom: 50px; display: flex; align-items: center; gap: 22px }
.pin .foot i { flex: 0 0 64px; height: 3px; background: #2D6B50 }
.pin .foot b { font-weight: 600; font-size: 24px; letter-spacing: .2em; text-transform: uppercase; color: #1C2821 }
.pin .foot em { margin-left: auto; font-family: 'Newsreader', Georgia, serif; font-style: italic; font-weight: 300; font-size: 34px; color: #2D6B50 }
""" % {"ph": PIN_PHOTO_H}

PIN_FIT = """
function fitPin(el){ const t=el.querySelector('.t'), s=el.querySelector('.s'), foot=el.querySelector('.foot');
  let size=96; const room=()=>foot.getBoundingClientRect().top-24;
  const panel=el.querySelector('.panel'); const bottom=()=>Math.max((s||t).getBoundingClientRect().bottom, panel.scrollHeight>panel.clientHeight+1?1e9:0);
  while(size>54 && (bottom()>room() || t.scrollWidth>t.clientWidth+1)){ size-=2; t.style.fontSize=size+'px'; if(s) s.style.fontSize=Math.round(size*.5)+'px'; } }
"""


def article_meta(page):
    t = page.read_text(encoding="utf-8")
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", t, re.S)
    kick = re.search(r'class="kick[^"]*"[^>]*>(.*?)</', t, re.S)
    clean = lambda s: re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", s))).strip()
    return clean(h1.group(1)) if h1 else page.stem, (clean(kick.group(1)) if kick else "")


def split_title(title):
    """("Gyumri", "Armenia's Black City") from "Gyumri — Armenia's Black City"; a title without a dash stays whole."""
    parts = re.split(r"\s*[—–]\s*|\s+-\s+", title, maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (title, "")


def cmd_pins(args):
    slug = ig_post.slugify(args.country)
    seed = json.loads((SEEDS / (slug + ".json")).read_text(encoding="utf-8"))
    man = manifest(slug)
    dest = Path(args.out) if args.out else TMP / slug / "pins"
    dest.mkdir(parents=True, exist_ok=True)
    work = TMP / slug / "carousel" / "pins"
    work.mkdir(parents=True, exist_ok=True)
    blocks, names = [], []
    for pin in seed.get("pins") or []:
        page = ROOT / pin["page"]
        h1, kick = article_meta(page)
        title, sub = split_title(pin.get("title") or h1)
        if "sub" in pin:
            sub = pin["sub"]
        p = man[pin["photo"]]
        f = work / (page.stem + ".png")
        cut(ROOT / p["original"], pin.get("crop"), PIN_W, PIN_PHOTO_H).save(f, "PNG")
        name = "%s-%s" % (slug, page.stem)
        names.append(name)
        esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")
        blocks.append(
            '<div class="pin" data-name="%s"><div class="ph"><img src="%s" width="%d" height="%d" alt=""></div>%s'
            '<div class="panel"><div class="k">%s</div><div class="t">%s</div>%s</div>'
            '<div class="foot"><i></i><b>%s</b><em>getawayguide</em></div></div>' % (
                name, f.as_uri(), PIN_W, PIN_PHOTO_H,
                ('<div class="tag">%s</div>' % esc(pin["tag"])) if pin.get("tag") else "",
                esc(pin.get("kicker") or kick), esc(title), ('<div class="s">%s</div>' % esc(sub)) if sub else "",
                esc(pin.get("cta") or "Read the guide")))
        out("  pin %-28s %s | %s | %s" % (page.name, title, sub, p["original"]))
    html = ("<!doctype html><html><head><meta charset='utf-8'><link rel='stylesheet' href='%s'><style>%s</style>"
            "<style>body{margin:0;background:#888}</style></head><body>%s<script>%s"
            "Promise.all([document.fonts.ready,...[...document.images].map(i=>i.decode().catch(()=>0))]).then(()=>{"
            "document.querySelectorAll('.pin').forEach(fitPin);window.__ready=1;});</script></body></html>") % (
        FONTS_CSS, PIN_CSS, "".join(blocks), PIN_FIT)
    hf = work / "pins.html"
    hf.write_text(html, encoding="utf-8")
    pw, b = chromium()
    try:
        pg = b.new_page(viewport={"width": PIN_W, "height": PIN_H}, device_scale_factor=1)
        pg.goto(hf.as_uri())
        pg.wait_for_function("window.__ready === 1", timeout=30000)
        for name in names:
            size = save_png(pg.locator('.pin[data-name="%s"]' % name).screenshot(type="png"), dest / (name + ".png"))
            out("  %s.png %dx%d" % (name, size[0], size[1]))
    finally:
        b.close()
        pw.stop()
    out("wrote %d pins into %s" % (len(names), dest))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1].strip(), formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("country")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="tools/ig_carousel/<slug>.json -> the carousel/main + carousel/map docs")
    r = sub.add_parser("render", help="render the carousel slides + caption.txt")
    r.add_argument("--state", help="ArtifactData out_dir, a folder with main.json/map.json, or a plan JSON (default: the seed output)")
    r.add_argument("--out", help="output folder (default .tmp/ig/<slug>/carousel/out)")
    pn = sub.add_parser("pins", help="1000x1500 Pinterest pins for the seed's articles")
    pn.add_argument("--out", help="output folder (default .tmp/ig/<slug>/pins)")
    args = ap.parse_args()
    {"seed": cmd_seed, "render": cmd_render, "pins": cmd_pins}[args.cmd](args)


if __name__ == "__main__":
    main()
