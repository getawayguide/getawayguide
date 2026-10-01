"""The Launch tab's render check: load each page the way a visitor does, at phone, tablet and desktop
width, and report broken images, sideways scroll and script errors. Pages come from the photo
server's /site/ route, so drafts render with their real relative paths.

    python tools/prelaunch_render.py "Drafts/.Full Articles/armenia/gyumri.html" [...]

Prints one JSON line: {rel: {width: {"broken": [...], "overflow": px, "wide": "...", "errors": [...]}}}"""
import json
import sys
from urllib.parse import quote

from playwright.sync_api import sync_playwright

SITE = "http://127.0.0.1:5003/site/"
# at the screen densities people read on; 1440 at 1x is an external monitor, where Kevin saw Armenia's
# photos go soft (2026-09-30) and the 2x/3x passes saw nothing
WIDTHS = [(393, 852, 3), (820, 1180, 2), (1440, 900, 2), (1440, 900, 1)]

# every lazy image loads, then the page is measured; the widest element names the overflow
PROBE = """async () => {
  document.querySelectorAll('img[loading="lazy"]').forEach(i => { i.loading = 'eager'; });
  const imgs = [...document.images];
  await Promise.race([Promise.all(imgs.map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; }))),
                      new Promise(r => setTimeout(r, 12000))]);
  const broken = imgs.filter(i => i.complete && i.naturalWidth === 0 && (i.currentSrc || i.src) && !/^data:/.test(i.src))
                     .map(i => decodeURIComponent((i.currentSrc || i.src).split('/').pop()));
  const W = document.documentElement.clientWidth, over = document.documentElement.scrollWidth - W;
  let wide = '';
  if (over > 1) {
    let best = null;
    for (const e of document.body.querySelectorAll('*')) {
      const r = e.getBoundingClientRect();
      if (r.right > W + 1 && (!best || r.right > best.r)) best = { r: r.right, e };
    }
    if (best) wide = best.e.tagName.toLowerCase() + (best.e.id ? '#' + best.e.id : '') + (best.e.className && typeof best.e.className === 'string' ? '.' + best.e.className.trim().split(/\\s+/).slice(0, 2).join('.') : '');
  }
  // soft: a body photo whose chosen file is narrower than it is drawn (cover draws it at the wider of
  // frame width and frame height x aspect), at this screen's density (Kevin, 2026-09-30: blurry photos)
  const dpr = window.devicePixelRatio || 1, soft = [];
  for (const i of imgs) {
    if (!i.currentSrc || i.closest('section') || i.closest('nav') || !i.complete || !i.naturalWidth) continue;
    const r = i.getBoundingClientRect(); if (r.width < 60) continue;
    const real = await new Promise(res => { const t = new Image(); t.onload = () => res([t.naturalWidth, t.naturalHeight]); t.onerror = () => res(null); t.src = i.currentSrc; });
    if (!real) continue;
    const need = Math.max(r.width, r.height * real[0] / real[1]) * dpr, k = real[0] / need;
    const name = i.alt || decodeURIComponent(i.currentSrc.split('/').pop());
    if (k < 0.85) soft.push(name + ' (' + real[0] + ' px file, drawn ' + Math.round(need) + ')');
    // Chrome halves a photo (a mipmap) and resamples the rest bilinearly, so a file just over 1x or
    // 2x the drawn width is resampled by a fraction of a pixel: the softest result there is, and it
    // shows on a 1x screen (measured: .tmp/tier_sweep.py; the cure is tools/fit_image_tiers.py)
    else if (dpr < 1.5 && (k < 1.3 || (k >= 1.97 && k < 2.3))) soft.push(name + ' (' + real[0] + ' px file drawn ' + Math.round(need) + ': ' + k.toFixed(2) + 'x is resampled soft on a 1x screen)');
  }
  return { broken: [...new Set(broken)], overflow: over > 1 ? over : 0, wide, soft };
}"""


def main(rels):
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for rel in rels:
            out[rel] = {}
            for w, h, dpr in WIDTHS:
                pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=dpr)
                errs = []
                pg.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
                try:
                    pg.goto(SITE + quote(rel), wait_until="load", timeout=60000)
                    pg.wait_for_timeout(800)
                    r = pg.evaluate(PROBE)
                    r["errors"] = errs
                except Exception as e:
                    r = {"error": str(e).splitlines()[0]}
                out[rel][str(w) + ("@1x" if dpr == 1 else "")] = r
                pg.close()
        b.close()
    sys.stdout.write(json.dumps(out, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
