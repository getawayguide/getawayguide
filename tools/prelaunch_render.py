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
WIDTHS = [(393, 852), (820, 1180), (1440, 900)]

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
  return { broken: [...new Set(broken)], overflow: over > 1 ? over : 0, wide };
}"""


def main(rels):
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for rel in rels:
            out[rel] = {}
            for w, h in WIDTHS:
                pg = b.new_page(viewport={"width": w, "height": h})
                errs = []
                pg.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
                try:
                    pg.goto(SITE + quote(rel), wait_until="load", timeout=60000)
                    pg.wait_for_timeout(800)
                    r = pg.evaluate(PROBE)
                    r["errors"] = errs
                except Exception as e:
                    r = {"error": str(e).splitlines()[0]}
                out[rel][str(w)] = r
                pg.close()
        b.close()
    sys.stdout.write(json.dumps(out, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
