"""Does the editor show an article the way the site does? For each page: computed styles of the layout
elements on the PUBLISHED page vs inside the EDITOR (opened through the test seam, nothing written).
Prints every element that still differs; "elements still differing: 0" is the pass.

    python tools/test_editor_fidelity.py                     # the three reference pages
    python tools/test_editor_fidelity.py greece/meteora.html  # any article(s)

Needs the photo server on :5003. Written 2026-10-14 with the change that gave field-notes pages the site's
own sheets in the editor (before it, only guide pages had them)."""

import asyncio, json, os, sys
from playwright.async_api import async_playwright
PAGES = sys.argv[1:] or ["turkiye/antalya.html", "Drafts/Upcoming/north-macedonia/skopje.html", "greece/thessaloniki.html"]
PROPS = ["display", "margin-top", "margin-bottom", "font-family", "font-size", "line-height", "color", "font-weight", "letter-spacing",
         "text-align", "background-color", "border-top-width", "border-bottom-width", "grid-template-columns", "width"]
SEL = ["p", "p + p", "h2.article-h2", "h3.article-h3", "h2.article-h2 + *", ".img-pair", ".img-pair > span", ".img-landscape", ".img-caption",
       "ul", "li", "a", ".day-head", ".d-n2", ".d-t2", ".rm-h", ".route-map", ".tour-box", ".tour-row", ".tour-head .tc", ".tour-rec",
       ".tour-lab .len", ".tour-cell a", ".tour-cell .price b", "figure.itinerary-map", "figure.citymap-fig", "hr"]
JS = """([root, sels, props]) => { const o = {}; for (const s of sels) { const e = document.querySelector(root + ' ' + s); if (!e) continue;
  const cs = getComputedStyle(e); o[s] = Object.fromEntries(props.map(p => [p, cs.getPropertyValue(p)]));
  o[s].w = Math.round(e.getBoundingClientRect().width); o[s].h = Math.round(e.getBoundingClientRect().height); } return o; }"""
before = {}
IGNORE = {"w", "h", "width", "font-family"}          # the canvas is 729 wide inside a wider window; fonts are compared by first face


def norm(k, v):
    return v.split(",")[0].strip('"\' ') if k == "font-family" else v


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1500, "height": 950})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        total = 0
        for rel in PAGES:
            await pg.goto("http://127.0.0.1:5003/site/" + "/".join(rel.split("/")), wait_until="networkidle"); await pg.wait_for_timeout(500)
            site = await pg.evaluate(JS, [".article-body", SEL, PROPS])
            await pg.goto("http://127.0.0.1:5003/site/editor.html", wait_until="domcontentloaded"); await pg.wait_for_timeout(1500)
            html = await pg.evaluate("rel => fetch('http://127.0.0.1:5003/site/' + rel.split('/').map(encodeURIComponent).join('/')).then(r => r.text())", rel)
            await pg.evaluate("""([t, name]) => window.loadFile({ kind: 'file', name, getFile: async () => new File([t], 'x.html', { type: 'text/html' }),
                createWritable: async () => ({ write: async () => {}, close: async () => {} }) })""", [html, rel.split("/")[-1]])
            await pg.wait_for_timeout(4000)
            ed = await pg.evaluate(JS, ["#editor", SEL, PROPS])
            pass
            print("\n##", rel.split("/")[-1])
            for s in SEL:
                if s not in site or s not in ed: continue
                diffs = {k: (norm(k, site[s][k]), norm(k, ed[s][k])) for k in PROPS if k not in IGNORE and norm(k, site[s][k]) != norm(k, ed[s][k])}
                ff = (norm("font-family", site[s]["font-family"]), norm("font-family", ed[s]["font-family"]))
                if ff[0] != ff[1]: diffs["font-family"] = ff
                if diffs:
                    total += 1
                    was = before.get(rel, {}).get(s, {})
                    print("  %-22s site vs editor: %s" % (s, {k: v + ((norm(k, was.get(k, "")),) if was else ()) for k, v in diffs.items()}))
            same = [s for s in SEL if s in site and s in ed and not any(norm(k, site[s][k]) != norm(k, ed[s][k]) for k in PROPS if k not in IGNORE)]
            print("  matching the site:", len(same), "of", len([s for s in SEL if s in site and s in ed]))
        print("\nelements still differing:", total, "| JS errors:", errs[:3])
        await b.close()
asyncio.run(main())
