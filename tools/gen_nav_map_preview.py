#!/usr/bin/env python3
"""Capture a still preview of the interactive world map for the nav dropdown + mobile drawer.

The map on destinations.html is amCharts rendering from a CDN, so this needs network -
run it from PowerShell, not the Bash tool. Output is a TRANSPARENT PNG: a solid backing (and the grey panel amCharts draws
behind the map) reads as a grey box sitting in the dropdown. Still a static image, so
the nav costs nothing extra and works even if the CDN is slow.

The whole map has to FIT (contain, never cover): a cover-crop trims Alaska, Iceland, New
Zealand and the southern cone, which is exactly what makes a world map look broken.

  py tools/gen_nav_map_preview.py                 # -> Images/web/nav-map-preview.png (transparent)
  py tools/gen_nav_map_preview.py --variants      # -> .tmp/previews/nav-map-*.png + a compare page
"""
import asyncio, io, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(ROOT, "Images", "web", "nav-map-preview.png")
VARDIR = os.path.join(ROOT, ".tmp", "previews")
W, H = 560, 300                      # displayed ~280x150, 2x for retina

# name -> (background, pad fraction, zoom-out factor applied to the chart before capture)
VARIANTS = {
    "tight":  ("#F4F2EC", 0.02, 1.00),
    "framed": ("#F4F2EC", 0.07, 1.00),
    "airy":   ("#FFFFFF", 0.12, 1.00),
    "wide":   ("#F4F2EC", 0.05, 0.86),
}


async def grab(zoom_out):
    """Screenshot #world-map with the whole world in frame. Returns PNG bytes."""
    from playwright.async_api import async_playwright
    url = "file:///" + os.path.join(ROOT, "destinations.html").replace("\\", "/")
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        pg = await br.new_page(viewport={"width": 1600, "height": 950}, device_scale_factor=2)
        await pg.goto(url, wait_until="networkidle", timeout=90000)
        try:
            await pg.wait_for_selector("#world-map svg", timeout=45000)
        except Exception:
            await br.close()
            return None
        await pg.wait_for_timeout(3500)
        await pg.evaluate("""(z) => {
          document.querySelectorAll('.map-legend,.map-mobile-hd,.map-close-btn')
            .forEach(e => e.style.display = 'none');
          const a = document.querySelector('a[href*="amcharts"]');
          if (a) a.style.display = 'none';
          // the +/- zoom control is drawn INSIDE the chart svg, so it lands in the capture
          document.querySelectorAll('#world-map g[class*="ZoomControl"],'
            + '#world-map g[class*="zoomControl"],#world-map .amcharts-ZoomControl')
            .forEach(e => e.style.display = 'none');
          // amCharts paints a light-grey panel behind the map; captured, it reads as a
          // grey box sitting in the dropdown. Clear it (and the container) so the preview
          // is transparent and only the landmasses show.
          document.querySelectorAll('#world-map, .dest-map-wrap, body, html')
            .forEach(e => { e.style.background = 'transparent'; });
          try { const c = am4core.registry.baseSprites
                  .find(s => s.svgContainer && s.svgContainer.htmlElement.id === 'world-map');
                if (c) {
                  if (c.zoomControl) c.zoomControl.disabled = true;
                  if (c.background) { c.background.fillOpacity = 0; c.background.strokeOpacity = 0; }
                }
          } catch (e) {}
          // amCharts4: go home, then optionally pull back so nothing touches the edges
          try {
            const ch = (window.am4core && am4core.registry.baseSprites || [])
              .find(s => s.svgContainer && s.svgContainer.htmlElement.id === 'world-map');
            if (ch) { ch.goHome(0); if (z !== 1) ch.zoomLevel = (ch.zoomLevel || 1) * z; }
          } catch (e) {}
        }""", zoom_out)
        await pg.wait_for_timeout(1600)
        png = await (await pg.query_selector("#world-map")).screenshot(omit_background=True)
        await br.close()
        return png


def compose(png, bg, pad):
    """Fit the capture inside WxH with padding — contain, so no continent is clipped.

    Output keeps ALPHA: a solid backing would show as a box against the dropdown, which
    is what the grey panel amCharts draws did before it was cleared."""
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    im = im.crop(im.split()[-1].getbbox() or im.getbbox())      # trim transparent margin
    iw, ih = round(W * (1 - pad * 2)), round(H * (1 - pad * 2))
    s = min(iw / im.width, ih / im.height)
    im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(im, ((W - im.width) // 2, (H - im.height) // 2), im)
    return out


def main():
    variants = "--variants" in sys.argv
    if not variants:
        png = asyncio.run(grab(1.0))
        if not png:
            sys.exit("!! map SVG never appeared (CDN blocked?)")
        bg, pad, _ = VARIANTS["tight"]     # the picked variant
        compose(png, bg, pad).save(LIVE, "PNG", optimize=True)
        print("wrote %s (%dx%d, %.0f KB)" % (os.path.relpath(LIVE, ROOT).replace("\\", "/"),
                                             W, H, os.path.getsize(LIVE) / 1024))
        return

    os.makedirs(VARDIR, exist_ok=True)
    shots, made = {}, []
    for name, (bg, pad, z) in VARIANTS.items():
        if z not in shots:
            shots[z] = asyncio.run(grab(z))
        if not shots[z]:
            print("!! capture failed for zoom %s" % z); continue
        p = os.path.join(VARDIR, "nav-map-%s.png" % name)
        compose(shots[z], bg, pad).save(p, "PNG", optimize=True)
        made.append((name, p))
        print("  %-8s %s  (%.0f KB)" % (name, os.path.relpath(p, ROOT).replace("\\", "/"),
                                        os.path.getsize(p) / 1024))

    cards = "".join(
        '<figure><figcaption>%s</figcaption>'
        '<div class="col"><span class="ttl">Explore Interactive Map</span>'
        '<img src="nav-map-%s.png" width="280" height="150"></div>'
        '<code>py tools/gen_nav_map_preview.py --pick %s</code></figure>' % (n, n, n)
        for n, _ in made)
    doc = ("<!doctype html><meta charset='utf-8'><title>Nav map preview options</title>"
           "<style>body{margin:0;padding:26px;background:#eceae4;color:#1C2821;"
           "font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}"
           "h1{font:600 20px Georgia,serif;margin:0 0 16px}"
           ".g{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:24px}"
           "figure{margin:0}figcaption{font:600 12px ui-monospace,Menlo,monospace;margin-bottom:8px}"
           ".col{background:rgba(250,249,246,.96);border:1px solid #d9d7d0;border-radius:8px;padding:14px}"
           ".ttl{display:block;font:300 italic .95rem Georgia,serif;color:#8a7f6d;margin-bottom:.45rem}"
           "img{display:block;width:280px;height:150px;object-fit:cover;border:1px solid #d9d7d0;border-radius:6px}"
           "code{display:block;margin-top:8px;font-size:11px;opacity:.65}</style>"
           "<h1>Nav map preview &mdash; options</h1>"
           "<p>Each shown at its real size inside a mock dropdown column.</p>"
           "<div class='g'>" + cards + "</div>")
    page = os.path.join(VARDIR, "nav-map-options.html")
    open(page, "w", encoding="utf-8").write(doc)
    print("\ncompare: %s" % page)


if __name__ == "__main__":
    if "--pick" in sys.argv:
        import shutil
        name = sys.argv[sys.argv.index("--pick") + 1]
        src = os.path.join(VARDIR, "nav-map-%s.png" % name)
        if not os.path.exists(src):
            sys.exit("no such variant: %s (run --variants first)" % name)
        shutil.copy(src, LIVE)
        print("picked %r -> %s" % (name, os.path.relpath(LIVE, ROOT).replace("\\", "/")))
    else:
        main()
