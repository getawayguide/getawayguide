"""Does a chosen place filter survive the background refresh? Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/qa_place_persist.py"""
import asyncio, sys, pathlib, time
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

LIB = """() => { const s = document.getElementById('f-place'); const g = document.getElementById('grid'); return { display: s.style.display, value: s.value, tiles: document.querySelectorAll('#grid .ph').length, scroll: g.scrollTop, hdr: document.getElementById('hdr-state').textContent }; }"""
SB = """() => { const s = document.querySelector('#pl-place'); const g = document.querySelector('#pl-grid'); return { value: s && s.value, tiles: document.querySelectorAll('#pl-grid .pl-ph').length, scroll: g.scrollTop, state: document.querySelector('#pl-geo-state').textContent, dbg: window.plDebug().place }; }"""

async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        net = []
        t0 = time.time()
        pg.on("response", lambda r: net.append((round(time.time() - t0, 1), r.url.split('/api/')[-1][:50])) if "/api/" in r.url and "thumb" not in r.url else None)
        print("== Photo Library ==")
        await pg.evaluate("showView('library')"); await pg.wait_for_timeout(1500)
        await pg.click("#albums :text('Armenia (2026)')", timeout=15000)
        await pg.wait_for_function("document.getElementById('f-place').style.display !== 'none' && document.getElementById('f-place').options.length > 3", timeout=40000)
        await pg.select_option("#f-place", "city:Gyumri"); await pg.wait_for_timeout(500)
        await pg.evaluate("document.getElementById('grid').scrollTop = 1500")
        print("  chose city:Gyumri:", await pg.evaluate(LIB))
        net.clear(); t0 = time.time()
        for i in range(5):
            await pg.wait_for_timeout(2000)
            print(f"  +{(i+1)*2}s:", await pg.evaluate(LIB))
        print("  net:", net)
        await pg.screenshot(path=str(SHOTS / "library-place-persist.png"))
        await pg.evaluate("showView('editor')")

        print("\n== Sidebar (article editor) ==")
        await pg.evaluate("window.togglePhotoPanel()")
        # The sidebar now opens on TRIPS (the Photo Library's albums); the raw
        # source tabs this test drives live behind the Folders button.
        await pg.evaluate("() => { if (plDebug().mode === 'country') plToggleMode(); }")
        await pg.wait_for_selector("#pl-tabs :text('Backup')", timeout=15000)
        await pg.click("#pl-tabs :text('Backup')"); await pg.wait_for_selector("#pl-folders :text('Armenia (2026)')", timeout=15000)
        await pg.click("#pl-folders :text('Armenia (2026)')")
        await pg.wait_for_function("document.querySelector('#pl-placebar') && document.querySelector('#pl-placebar').style.display !== 'none' && !(document.querySelector('#pl-geo-state').textContent||'').startsWith('locating')", timeout=60000)
        await pg.select_option("#pl-place", "city:Gyumri"); await pg.wait_for_timeout(500)
        await pg.evaluate("document.querySelector('#pl-grid').scrollTop = 800")
        print("  chose city:Gyumri:", await pg.evaluate(SB))
        net.clear(); t0 = time.time()
        for i in range(5):
            await pg.wait_for_timeout(2000)
            print(f"  +{(i+1)*2}s:", await pg.evaluate(SB))
        print("  net:", net)
        report_errors(ctx, "place-persist")
        await b.close()
asyncio.run(main())
