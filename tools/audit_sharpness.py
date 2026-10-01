#!/usr/bin/env python3
"""Is every image on the site sharp on a 1x monitor?

Kevin reviews on a 1x monitor, where Chrome draws an image softest when its file is about 1.0x or
2.0x the width it is drawn (it halves the image, then resamples the rest by a fraction of a pixel;
sharpness peaks near 1.9x). For every <img> and CSS background image on every page (live + drafts),
at 1440 and 1920 wide and 1x: the file Chrome picks, the width it is drawn (object-fit / background-
size aware), the ratio, and a verdict:
    too small        under 0.85x
    soft (near 1x)   0.85x to 1.3x, unless drawn at exactly its own size
    soft (near 2x)   1.97x to 2.3x
Body photos are fixed by tools/fit_image_tiers.py, city maps by tools/city_maps_1x.py (2026-10-01).

Needs the photo server (http://127.0.0.1:5003).
    python tools/audit_sharpness.py OUT.json [page ...]      no pages: the whole site
"""
import json, subprocess, sys, glob
from urllib.parse import quote, unquote
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
OUT = sys.argv[1]
pages = sys.argv[2:]
if not pages:
    live = [f for f in subprocess.run(["git", "ls-files", "*.html"], capture_output=True, text=True, encoding="utf-8").stdout.split("\n")
            if f and not f.startswith(("archive/", "Drafts/", ".tmp/")) and f != "editor.html"]
    pages = live + sorted(f.replace("\\", "/") for f in glob.glob("Drafts/.Full Articles/*/*.html") + glob.glob("Drafts/*/field-notes.html"))
PROBE = r"""async () => {
  document.querySelectorAll('img[loading="lazy"]').forEach(i => { i.loading = 'eager'; });
  const imgs = [...document.images].filter(i => !i.closest('nav') || i.classList.contains('nav-map-thumb'));
  await Promise.race([Promise.all(imgs.map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; }))), new Promise(r => setTimeout(r, 15000))]);
  const out = [];
  for (const i of imgs) {
    if (!i.currentSrc || /[.]svg($|[?])/i.test(i.currentSrc) || !i.naturalWidth) continue;
    const r = i.getBoundingClientRect(); if (r.width < 40 || getComputedStyle(i).visibility === 'hidden') continue;
    const real = await new Promise(res => { const t = new Image(); t.onload = () => res([t.naturalWidth, t.naturalHeight]); t.onerror = () => res(null); t.src = i.currentSrc; });
    if (!real) continue;
    const fit = getComputedStyle(i).objectFit, ar = real[0] / real[1];
    const drawn = fit === 'cover' ? Math.max(r.width, r.height * ar) : fit === 'contain' ? Math.min(r.width, r.height * ar) : r.width;
    const kind = i.closest('section') ? 'hero' : i.classList.contains('cmbase') ? 'city map' : i.classList.contains('nav-map-thumb') ? 'nav' : i.closest('picture') ? 'photo' : 'other';
    out.push({ alt: i.alt, kind, src: decodeURIComponent(new URL(i.currentSrc).pathname), file: real[0], drawn: Math.round(drawn * 100) / 100, x: Math.round(r.left * 100) / 100 });
  }
  // CSS background images (heroes, country cards): the first url of an image-set is the one Chrome
  // takes for a type() set; drawn size follows background-size (cover/contain/auto)
  for (const e of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(e), bi = cs.backgroundImage;
    if (!bi || bi === 'none' || !/url\(/.test(bi)) continue;
    const r = e.getBoundingClientRect(); if (r.width < 100 || r.height < 60 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    const u = (bi.match(/url\("?([^")]+)"?\)/) || [])[1]; if (!u || /[.]svg/i.test(u) || u.startsWith('data:')) continue;
    const real = await new Promise(res => { const t = new Image(); t.onload = () => res([t.naturalWidth, t.naturalHeight]); t.onerror = () => res(null); t.src = u; });
    if (!real) continue;
    const bs = cs.backgroundSize, sx = r.width / real[0], sy = r.height / real[1];
    const scale = bs === 'cover' ? Math.max(sx, sy) : bs === 'contain' ? Math.min(sx, sy) : /^\d/.test(bs) && bs.endsWith('px') ? parseFloat(bs) / real[0] : bs.startsWith('100%') ? sx : 1;
    const kind = e.closest('section') || /hero|banner/.test(e.className) ? 'hero (css)' : /card/.test(e.className) ? 'card (css)' : 'other (css)';
    out.push({ alt: (e.className || e.tagName).toString().slice(0, 40), kind, src: decodeURIComponent(new URL(u, location.href).pathname), file: real[0], drawn: Math.round(real[0] * scale * 100) / 100, x: Math.round(r.left * 100) / 100 });
  }
  return out;
}"""


def verdict(k, drawn, file):
    if k < 0.85: return "too small"
    if abs(file - drawn) < 0.01: return "ok"          # drawn at exactly its own size: no resampling
    if k < 1.3: return "soft (near 1x)"
    if 1.97 <= k < 2.3: return "soft (near 2x)"
    return "ok"


res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    for n, rel in enumerate(pages):
        res[rel] = {}
        for vw in (1440, 1920):
            pg = b.new_page(viewport={"width": vw, "height": 1000}, device_scale_factor=1)
            try:
                pg.goto("http://127.0.0.1:5003/site/" + quote(rel), wait_until="load", timeout=60000); pg.wait_for_timeout(500)
                rows = pg.evaluate(PROBE)
                for r in rows:
                    r["k"] = round(r["file"] / r["drawn"], 3); r["verdict"] = verdict(r["k"], r["drawn"], r["file"])
                res[rel][vw] = rows
            except Exception as e:
                res[rel][vw] = {"error": str(e).splitlines()[0]}
            pg.close()
        bad = sum(1 for vw in res[rel] if isinstance(res[rel][vw], list) for r in res[rel][vw] if r["verdict"] != "ok")
        print("%3d/%d %-62s %s" % (n + 1, len(pages), rel[:62], ("%d soft" % bad) if bad else "ok"), flush=True)
    b.close()
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
