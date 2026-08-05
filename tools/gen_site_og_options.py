#!/usr/bin/env python3
"""OG image options for the SITE pages (index, destinations, posts, about, …).

Right now they share a flat brand card. A rendered screenshot of the page itself is the
other common convention - it shows a person what they'll actually land on. Neither is
strictly better, so this builds both (plus hybrids) at 1200x630 and writes a compare page.

  py tools/gen_site_og_options.py            # -> .tmp/previews/site-og-*.jpg + compare page
  py tools/gen_site_og_options.py --pick shot-home
        -> installs that option as Images/web/og/site.jpg (all site pages point at it)

Needs network for the homepage's map/fonts, so run it from PowerShell.
"""
import asyncio, io, os, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VARDIR = os.path.join(ROOT, ".tmp", "previews")
LIVE = os.path.join(ROOT, "Images", "web", "og", "site.jpg")
W, H = 1200, 630


async def shoot(page, width, height, full_top):
    """Screenshot a page at a viewport shaped like the OG card."""
    from playwright.async_api import async_playwright
    url = "file:///" + os.path.join(ROOT, page).replace("\\", "/")
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        pg = await br.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
        await pg.goto(url, wait_until="networkidle", timeout=90000)
        await pg.wait_for_timeout(3500)
        # kill anything that reads as chrome rather than content
        await pg.evaluate("""() => {
          document.querySelectorAll('.hero-scroll,.nav-hamburger').forEach(e => e.style.display='none');
          window.scrollTo(0, 0);
        }""")
        await pg.wait_for_timeout(500)
        png = await pg.screenshot(clip={"x": 0, "y": 0, "width": width,
                                        "height": min(height, full_top)})
        await br.close()
        return png


def fit(png, bg="#F4F2EC"):
    # a page screenshot is already sRGB, so there is no profile to carry here - noted so
    # the omission doesn't read as the same bug as the photo pipeline
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    s = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x, y = (im.width - W) // 2, 0
    return im.crop((x, y, x + W, y + H))


def framed(png, bg="#1C2821", pad=54, radius=14):
    """Browser-ish: the render inset on the brand colour, so it reads as a site preview."""
    from PIL import Image, ImageDraw
    im = Image.open(io.BytesIO(png)).convert("RGB")
    iw, ih = W - pad * 2, H - pad * 2
    s = max(iw / im.width, ih / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    im = im.crop(((im.width - iw) // 2, 0, (im.width - iw) // 2 + iw, ih))
    mask = Image.new("L", (iw, ih), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, iw - 1, ih - 1), radius=radius, fill=255)
    out = Image.new("RGB", (W, H), bg)
    out.paste(im, (pad, pad), mask)
    return out


OPTIONS = {
    # name            page                viewport w,h    builder
    "shot-home":     ("index.html",        (1200, 630),  "fit"),
    "shot-dest":     ("destinations.html", (1200, 630),  "fit"),
    "framed-home":   ("index.html",        (1200, 700),  "framed"),
    "framed-dest":   ("destinations.html", (1200, 700),  "framed"),
}


def main():
    os.makedirs(VARDIR, exist_ok=True)
    made = []
    for name, (page, (vw, vh), how) in OPTIONS.items():
        png = asyncio.run(shoot(page, vw, vh, vh))
        img = fit(png) if how == "fit" else framed(png)
        p = os.path.join(VARDIR, "site-og-%s.jpg" % name)
        img.save(p, "JPEG", quality=86, optimize=True)
        made.append(name)
        print("  %-13s %s (%.0f KB)" % (name, os.path.relpath(p, ROOT).replace("\\", "/"),
                                        os.path.getsize(p) / 1024))
    # include the current brand card for side-by-side
    if os.path.exists(LIVE):
        shutil.copy(LIVE, os.path.join(VARDIR, "site-og-brand.jpg"))
        made.insert(0, "brand")
        print("  %-13s (current)" % "brand")

    cards = "".join(
        "<figure><figcaption>%s%s</figcaption><div class='card'>"
        "<img src='site-og-%s.jpg'><div class='m'><div class='d'>GETAWAYGUIDE.IO</div>"
        "<div class='t'>Getawayguide &mdash; A Travel Blog</div>"
        "<div class='s'>A personal travel blog with detailed itineraries, honest city guides&hellip;</div>"
        "</div></div><code>--pick %s</code></figure>"
        % (n, " (current)" if n == "brand" else "", n, n) for n in made)
    doc = ("<!doctype html><meta charset='utf-8'><title>Site OG options</title>"
           "<style>body{margin:0;padding:26px;background:#eceae4;color:#1C2821;"
           "font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}"
           "h1{font:600 20px Georgia,serif;margin:0 0 6px}p{opacity:.7;margin:0 0 18px}"
           ".g{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:24px}"
           "figure{margin:0}figcaption{font:600 12px ui-monospace,Menlo,monospace;margin-bottom:7px}"
           ".card{border:1px solid #d4d2cb;border-radius:10px;overflow:hidden;background:#fff}"
           "img{display:block;width:100%;aspect-ratio:1200/630;object-fit:cover}"
           ".m{padding:10px 12px;border-top:1px solid #ecebe6}"
           ".d{font-size:10.5px;letter-spacing:.06em;color:#65635e}"
           ".t{font-weight:600;font-size:15px;margin:3px 0 2px}"
           ".s{font-size:12.5px;color:#5d5b56}code{display:block;margin-top:7px;font-size:11px;opacity:.6}"
           "</style><h1>Site pages &mdash; link preview options</h1>"
           "<p>Shown as the card Facebook / LinkedIn / X / Slack draw.</p>"
           "<div class='g'>" + cards + "</div>")
    page = os.path.join(VARDIR, "site-og-options.html")
    open(page, "w", encoding="utf-8").write(doc)
    print("\ncompare: %s" % page)


if __name__ == "__main__":
    if "--pick" in sys.argv:
        name = sys.argv[sys.argv.index("--pick") + 1]
        src = os.path.join(VARDIR, "site-og-%s.jpg" % name)
        if not os.path.exists(src):
            sys.exit("no such option: %s (run without --pick first)" % name)
        shutil.copy(src, LIVE)
        print("picked %r -> Images/web/og/site.jpg" % name)
    else:
        main()
