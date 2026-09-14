"""Album-switch race timeline + card highlight after rapid clicks. Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_race2.py"""
import asyncio, sys, pathlib, time
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

ST = "({title: document.getElementById('tb-title').textContent, tiles: document.querySelectorAll('#grid .ph').length, on: [...document.querySelectorAll('#albums .alb.on')].map(e => e.dataset.album), first: (i => i && decodeURIComponent(i.dataset.q || i.src || '').split('album=')[1]?.split('&')[0])(document.querySelector('#grid .ph img')), placeOpts: document.getElementById('f-place').options.length, placeShown: document.getElementById('f-place').style.display !== 'none'})"

async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        await lib_open(pg, ctx, "Vietnam (2024)")
        print("== rapid clicks without delay: Armenia then Kosovo 150 ms apart ==")
        await pg.click("#albums .alb[data-album='Armenia (2026)']"); await pg.wait_for_timeout(150)
        await pg.click("#albums .alb[data-album='Kosovo (2025)']")
        for t in (0.5, 1.5, 3, 6):
            await pg.wait_for_timeout(int((t - (0 if t == 0.5 else [0.5,1.5,3][[0.5,1.5,3,6].index(t)-1])) * 1000))
            print(f"  +{t}s:", await pg.evaluate(ST))
        print("\n== Armenia browse delayed 3 s, then click Kosovo ==")
        await pg.evaluate(PATCH, {"pat": "backup_browse\?album=Armenia", "mode": "delay", "delay": 3000})
        await pg.click("#albums .alb[data-album='Armenia (2026)']"); await pg.wait_for_timeout(300)
        await pg.click("#albums .alb[data-album='Kosovo (2025)']")
        t0 = time.time()
        for i in range(12):
            await pg.wait_for_timeout(500)
            print(f"  +{time.time()-t0:.1f}s:", await pg.evaluate(ST))
        print("  patched hits:", [h["url"][-60:] for h in await pg.evaluate("window.__hits")])
        await pg.evaluate(UNPATCH)
        await pg.screenshot(path=str(SHOTS / "lib-race2.png"))
        print("\n== geo delayed 4 s on Armenia, then switch to Kosovo: does Armenia's place list land on Kosovo? ==")
        await pg.evaluate(PATCH, {"pat": "/api/geo\?album=Armenia", "mode": "delay", "delay": 4000})
        await pg.click("#albums .alb[data-album='Armenia (2026)']"); await pg.wait_for_timeout(1500)
        await pg.click("#albums .alb[data-album='Kosovo (2025)']")
        t0 = time.time()
        for i in range(6):
            await pg.wait_for_timeout(1000)
            print(f"  +{time.time()-t0:.1f}s:", await pg.evaluate(ST), "| first optgroup:", await pg.evaluate("(document.querySelector('#f-place optgroup')||{}).label"))
        await pg.evaluate(UNPATCH)
        report_errors(ctx, "race2")
        await b.close()
asyncio.run(main())
