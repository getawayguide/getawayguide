"""Photo Library grid: 1082-photo album, thumb pump, lazy load, filters, album-switch race, resize, refresh while scrolled.
Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_grid.py"""
import asyncio, sys, pathlib, time, json
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *


VIS = """() => { const m = document.querySelector('#app-library main'); const r = m.getBoundingClientRect(); const imgs = [...document.querySelectorAll('#grid .ph img')].filter(i => { const b = i.getBoundingClientRect(); return b.bottom > r.top && b.top < r.bottom; });
  return { total: imgs.length, complete: imgs.filter(i => i.complete && i.naturalWidth > 0).length, loading: imgs.filter(i => i.src && !i.complete).length, nosrc: imgs.filter(i => !i.src).length, queued: imgs.filter(i => i.dataset.q).length, scrollTop: m.scrollTop, scrollH: m.scrollHeight }; }"""

async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        await pg.evaluate("window.addEventListener('unhandledrejection', e => console.error('UNHANDLED ' + (e.reason && (e.reason.stack || e.reason))))")
        inflight = {"n": 0, "max": 0}; thumbs = []
        def on_req(r):
            if "/bthumb" in r.url:
                inflight["n"] += 1; inflight["max"] = max(inflight["max"], inflight["n"]); thumbs.append((time.time(), r.url))
        def on_done(r):
            if "/bthumb" in r.url: inflight["n"] -= 1
        pg.on("request", on_req); pg.on("requestfinished", on_done); pg.on("requestfailed", on_done)
        await stub_status(pg)
        await pg.evaluate("showView('library')")
        await pg.wait_for_selector("#albums .alb", timeout=60000); await pg.wait_for_timeout(1000)

        print("== Armenia (2026): load + pump ==")
        t0 = time.time(); thumbs.clear(); inflight["max"] = 0
        await pg.click("#albums .alb[data-album='Armenia (2026)']")
        await pg.wait_for_function("document.querySelectorAll('#grid .ph').length > 1000", timeout=60000)
        t1 = time.time() - t0
        await pg.wait_for_timeout(4000)
        print(f"  1082 tiles rendered in {t1:.2f}s; thumbs requested so far {len(thumbs)}; max concurrent bthumb {inflight['max']} (expect <=4); visible {await pg.evaluate(VIS)}")
        names = await pg.evaluate("VIEW ? 0 : 0") if False else None
        order = await pg.evaluate("[...document.querySelectorAll('#grid .ph .res')].slice(0,0).length")
        srt = await pg.evaluate("() => { const ns = [...document.querySelectorAll('#grid .ph img')].map(i => i.dataset.q || i.src || '').filter(Boolean).map(u => decodeURIComponent(u.split('name=')[1].split('&')[0])); const s = ns.slice().sort((a,b) => a.toLowerCase() < b.toLowerCase() ? -1 : 1); return { first: ns.slice(0,3), last: ns.slice(-2), sortedByNameLower: JSON.stringify(ns) === JSON.stringify(s) }; }")
        print("  order:", srt)

        print("\n== fast scroll to bottom and back ==")
        thumbs.clear(); inflight["max"] = 0; t0 = time.time()
        for y in range(0, 60000, 3000):
            await pg.evaluate(f"document.querySelector('#app-library main').scrollTop = {y}"); await pg.wait_for_timeout(40)
        await pg.evaluate("document.querySelector('#app-library main').scrollTop = 1e9"); await pg.wait_for_timeout(300)
        for y in range(60000, -1, -3000):
            await pg.evaluate(f"document.querySelector('#app-library main').scrollTop = {y}"); await pg.wait_for_timeout(40)
        await pg.evaluate("document.querySelector('#app-library main').scrollTop = 0")
        await pg.wait_for_timeout(5000)
        print(f"  swept in {time.time()-t0:.1f}s: thumbs requested {len(thumbs)}, max concurrent {inflight['max']}; top visible now {await pg.evaluate(VIS)}")
        await pg.evaluate("document.querySelector('#app-library main').scrollTop = 1e9"); await pg.wait_for_timeout(5000)
        print(f"  bottom visible after 5s: {await pg.evaluate(VIS)}")
        await pg.evaluate("document.querySelector('#app-library main').scrollTop = 20000"); await pg.wait_for_timeout(6000)
        mid = await pg.evaluate(VIS); print(f"  middle after 6s: {mid}")
        await pg.screenshot(path=str(SHOTS / "lib-grid-middle.png"))

        print("\n== 6 s refresh while scrolled (rebuilds the grid?) ==")
        thumbs.clear()
        pre = await pg.evaluate(VIS)
        seen = []
        for i in range(16):
            await pg.wait_for_timeout(500)
            v = await pg.evaluate(VIS); seen.append((i*0.5, v["scrollTop"], v["complete"], v["total"], v["nosrc"]))
        print("  pre:", pre)
        print("  (t, scrollTop, complete, visible, nosrc):", seen)
        print("  thumb requests during 8 s idle:", len(thumbs), "| bthumb URLs identical to earlier (cache)?", len({u for _, u in thumbs}))

        print("\n== filters ==")
        await pg.evaluate("document.querySelector('#app-library main').scrollTop = 0")
        man = await pg.evaluate("async () => { const d = await (await fetch('/api/backup_browse?album=Armenia%20(2026)')).json(); return { n: d.photos.length, edited: d.photos.filter(p => p.edited).length, rated3: d.photos.filter(p => (p.rating||0) >= 3).length, notfull: d.photos.filter(p => !p.full).length, picked: d.photos.filter(p => p.picked).length }; }")
        print("  api:", man)
        await pg.click("#f-edited"); await pg.wait_for_timeout(300)
        print("  Edited only tiles:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"), "badges:", await pg.evaluate("document.querySelectorAll('#grid .badge').length"))
        await pg.click("#f-rated"); await pg.wait_for_timeout(300)
        print("  Rated 3+ tiles:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"), "| stars on:", await pg.evaluate("[...document.querySelectorAll('#grid .ph')].map(t => t.querySelectorAll('.stars .on').length)"))
        await pg.wait_for_function("document.getElementById('f-place').style.display !== 'none'", timeout=40000)
        opts = await pg.evaluate("[...document.querySelectorAll('#f-place option')].map(o => [o.value, o.textContent])")
        print("  place options:", len(opts), opts[:3], opts[-1])
        await pg.click("#f-all"); await pg.select_option("#f-place", "city:Gyumri"); await pg.wait_for_timeout(300)
        print("  All + Gyumri:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"))
        await pg.click("#f-edited"); await pg.wait_for_timeout(300)
        print("  Edited + Gyumri:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"), "| empty msg:", repr(await pg.evaluate("document.getElementById('empty').textContent")))
        await pg.select_option("#f-place", " nogps"); await pg.wait_for_timeout(300)
        print("  Edited + nogps:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"))
        await pg.click("#f-all"); await pg.wait_for_timeout(300)
        ng = await pg.evaluate("document.querySelectorAll('#grid .ph').length"); print("  All + nogps:", ng, "titles:", await pg.evaluate("[...new Set([...document.querySelectorAll('#grid .ph')].map(t => t.title))]"))
        print("  2048px-copy badges:", await pg.evaluate("[...document.querySelectorAll('#grid .res')].filter(r => r.textContent.includes('2048')).length"))
        # filter persists across album switch?
        await pg.click("#albums .alb[data-album='Kosovo (2025)']"); await pg.wait_for_timeout(2500)
        print("  after switching to Kosovo with nogps chosen: place value:", repr(await pg.evaluate("document.getElementById('f-place').value")), "tiles:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"), "FILTER btn:", await pg.evaluate("document.querySelector('#lib-toolbar button.on').id"))
        await pg.click("#f-edited"); await pg.wait_for_timeout(200)
        await pg.click("#albums .alb[data-album='Vietnam (2024)']"); await pg.wait_for_timeout(2500)
        print("  Edited filter kept across album switch:", await pg.evaluate("document.querySelector('#lib-toolbar button.on').id"), "tiles:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"))
        await pg.click("#f-all")

        print("\n== album switch while the previous album's browse is still loading (3 s delay on Armenia) ==")
        await pg.evaluate(PATCH, {"pat": "backup_browse\\?album=Armenia", "mode": "delay", "delay": 3000})
        thumbs.clear()
        await pg.click("#albums .alb[data-album='Armenia (2026)']"); await pg.wait_for_timeout(300)
        await pg.click("#albums .alb[data-album='Kosovo (2025)']"); await pg.wait_for_timeout(1500)
        k = await pg.evaluate("({title: document.getElementById('tb-title').textContent, tiles: document.querySelectorAll('#grid .ph').length, on: document.querySelector('#albums .alb.on').dataset.album})")
        print("  1.5 s after clicking Kosovo:", k)
        await pg.wait_for_timeout(2500)
        bad = await pg.evaluate("({title: document.getElementById('tb-title').textContent, tiles: document.querySelectorAll('#grid .ph').length, on: document.querySelector('#albums .alb.on').dataset.album, firstThumb: decodeURIComponent((document.querySelector('#grid .ph img').src || document.querySelector('#grid .ph img').dataset.q || '')).split('bthumb?')[1], place: document.getElementById('f-place').options.length })")
        n404 = await pg.evaluate("[...document.querySelectorAll('#grid .ph img')].filter(i => i.src && i.complete && i.naturalWidth === 0).length")
        print("  4 s after clicking Kosovo:", bad, "| broken thumbs:", n404)
        await pg.screenshot(path=str(SHOTS / "lib-switch-race.png"))
        await pg.evaluate(UNPATCH)

        print("\n== window resize ==")
        await pg.click("#albums .alb[data-album='Kosovo (2025)']"); await pg.wait_for_timeout(2000)
        for w, h in [(700, 500), (2200, 1200), (1500, 1000)]:
            await pg.set_viewport_size({"width": w, "height": h}); await pg.wait_for_timeout(600)
            g = await pg.evaluate("() => { const g = document.getElementById('grid'); const c = getComputedStyle(g); const tiles = [...g.children].slice(0, 12).map(t => Math.round(t.getBoundingClientRect().width)); return { cols: c.gridTemplateColumns.split(' ').length, tileW: [...new Set(tiles)], overflowX: document.querySelector('#app-library main').scrollWidth > document.querySelector('#app-library main').clientWidth, albumsW: document.getElementById('albums').clientWidth }; }")
            print(f"  {w}x{h}: {g} visible {await pg.evaluate(VIS)}")
            if w == 700: await pg.screenshot(path=str(SHOTS / "lib-700x500.png"))
        report_errors(ctx, "grid")
        await b.close()

asyncio.run(main())
