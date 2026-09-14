"""Photo Library 9-minute soak of the 6 s refresh: DOM nodes, listeners, heap, requests, scroll stability.
Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_soak.py [minutes]"""
import asyncio, sys, pathlib, time, json
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 9

async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-gl=swiftshader", "--enable-precise-memory-info"])
        pg = await b.new_page(viewport={"width": 1500, "height": 1000})
        pg.on("pageerror", lambda e: ctx.errors.append(f"pageerror: {e}"))
        pg.on("console", lambda m: ctx.errors.append(f"console.error: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
        reqs = []
        pg.on("request", lambda r: reqs.append((time.time(), r.url)))
        await pg.goto(f"{API}/site/editor.html#library", wait_until="load")
        await pg.wait_for_timeout(1500)
        cdp = await pg.context.new_cdp_session(pg)
        await pg.click("#albums :text('Armenia (2026)')", timeout=20000)
        await pg.wait_for_function("document.querySelectorAll('#grid .ph').length > 1000", timeout=60000)
        await pg.wait_for_timeout(3000)
        await pg.evaluate("document.querySelector('#app-library main').scrollTop = 4000")
        await pg.wait_for_timeout(2000)
        t0 = time.time(); last = t0
        print(f"soak {MIN} min on Armenia (2026); sample every 30 s")
        print("  t(s)  nodes  listeners  heapMB  tiles  scrollTop  visibleImgs(complete/total)  api/thumb reqs in window  errors")
        rows = []
        while time.time() - t0 < MIN * 60:
            await pg.wait_for_timeout(30000)
            c = (await cdp.send("Memory.getDOMCounters"))
            heap = await pg.evaluate("performance.memory ? Math.round(performance.memory.usedJSHeapSize/1048576) : -1")
            tiles = await pg.evaluate("document.querySelectorAll('#grid .ph').length")
            st = await pg.evaluate("document.querySelector('#app-library main').scrollTop")
            vis = await pg.evaluate("""() => { const m = document.querySelector('#app-library main'); const r = m.getBoundingClientRect(); const imgs = [...document.querySelectorAll('#grid .ph img')].filter(i => { const b = i.getBoundingClientRect(); return b.bottom > r.top && b.top < r.bottom; }); return { total: imgs.length, complete: imgs.filter(i => i.complete && i.naturalWidth > 0).length, nosrc: imgs.filter(i => !i.src).length }; }""")
            now = time.time()
            win = [u for t, u in reqs if t > last]
            last = now
            api = len([u for u in win if "/api/" in u]); th = len([u for u in win if "/bthumb" in u])
            row = (round(now - t0), c["nodes"], c["jsEventListeners"], heap, tiles, st, f"{vis['complete']}/{vis['total']} nosrc={vis['nosrc']}", f"api={api} thumb={th}", len(ctx.errors))
            rows.append(row); print("  ", *row)
        # activity panel open/close cycling: does the poll stop after close?
        for i in range(5):
            await pg.evaluate("openActivity()"); await pg.wait_for_timeout(300)
            await pg.evaluate("closeActivity()")
        await pg.evaluate("openActivity()"); await pg.evaluate("openActivity()"); await pg.wait_for_timeout(4500)
        n_open = len([1 for t, u in reqs if t > time.time() - 4.5 and "backup_activity" in u])
        await pg.evaluate("closeActivity()"); mark = time.time(); await pg.wait_for_timeout(6500)
        n_closed = len([1 for t, u in reqs if t > mark and "backup_activity" in u])
        print(f"activity: double-open -> {n_open} polls in 4.5 s (expect ~3); after close -> {n_closed} polls in 6.5 s (expect 0)")
        c = await cdp.send("Memory.getDOMCounters")
        print("final counters:", c, "errors:", len(ctx.errors))
        for e in ctx.errors[:10]: print("   ", e[:300])
        await b.close()

asyncio.run(main())
