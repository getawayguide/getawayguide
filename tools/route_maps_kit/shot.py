import sys, urllib.parse
from playwright.sync_api import sync_playwright
page, slug, out = sys.argv[1], sys.argv[2], sys.argv[3]
URL = "http://127.0.0.1:5003/site/" + urllib.parse.quote(page)
with sync_playwright() as p:
    b = p.chromium.launch()
    for w, h in ((1440, 900), (393, 852)):
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
        pg.goto(URL, wait_until="load", timeout=90000); pg.wait_for_timeout(1500)
        el = pg.query_selector("[data-map='%s']" % slug)
        hd = pg.query_selector(".route-map")
        hd.evaluate("e => e.scrollIntoView({block:'start'})"); pg.wait_for_timeout(300)
        pg.evaluate("window.scrollBy(0,-80)"); pg.wait_for_timeout(400)
        r = pg.evaluate("""s => { const e=document.querySelector(s).closest('.route-map'); const h=document.querySelector('#getting-there'); const a=h.getBoundingClientRect(), b=e.getBoundingClientRect(); return [a.top+scrollY, b.bottom+scrollY]; }""", "[data-map='%s']" % slug)
        pg.screenshot(path="%s-sect-%d.png" % (out, w), full_page=True, clip={"x": 0, "y": r[0] - 20, "width": w, "height": r[1] - r[0] + 200})
        el.screenshot(path="%s-map-%d.png" % (out, w))
        print(w, "overflow", pg.evaluate("document.documentElement.scrollWidth-document.documentElement.clientWidth"), el.bounding_box())
        pg.close()
    b.close()
