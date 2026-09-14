"""Photo Library robustness: failing / slow routes via monkeypatched fetch, view switching, unhandled rejections.
Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_robust.py"""
import asyncio, sys, pathlib, time, json
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *


async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        unh = []
        pg.on("console", lambda m: unh.append(m.text[:300]) if m.text.startswith("UNHANDLED") else None)
        await pg.evaluate("window.addEventListener('unhandledrejection', e => console.log('UNHANDLED ' + (e.reason && (e.reason.stack || e.reason))))")
        await stub_status(pg)
        await pg.evaluate("showView('library')")
        await pg.wait_for_selector("#albums .alb", timeout=60000); await pg.wait_for_timeout(1000)
        await pg.click("#albums .alb[data-album='Kosovo (2025)']"); await pg.wait_for_timeout(2500)

        print("== backup_status returns 500 once ==")
        await pg.evaluate(PATCH, {"pat": "/api/backup_status", "mode": "status", "status": 500})
        await pg.evaluate("__libRefresh()"); await pg.wait_for_timeout(800)
        print("  cards:", await pg.evaluate("document.querySelectorAll('#albums .alb').length"), "| sidebar text:", (await pg.evaluate("document.getElementById('albums').textContent"))[:80].strip(), "| hdr:", await pg.evaluate("document.getElementById('hdr-state').textContent"), "| grid tiles:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"))
        await pg.evaluate(UNPATCH); await pg.evaluate("__libRefresh()"); await pg.wait_for_timeout(1500)
        print("  recovered cards:", await pg.evaluate("document.querySelectorAll('#albums .alb').length"), "| unhandled:", unh)

        print("\n== backup_browse fails (network) / returns 500 ==")
        for mode in ("fail", "status"):
            unh.clear(); e0 = len(ctx.errors)
            await pg.evaluate(PATCH, {"pat": "/api/backup_browse", "mode": mode, "status": 500})
            await pg.click("#albums .alb[data-album='Vietnam (2024)']"); await pg.wait_for_timeout(1200)
            print(f"  {mode}: title={await pg.evaluate('document.getElementById(\"tb-title\").textContent')!r} tiles={await pg.evaluate('document.querySelectorAll(\"#grid .ph\").length')} empty={await pg.evaluate('document.getElementById(\"empty\").textContent')[:40] if False else (await pg.evaluate('document.getElementById(\"empty\").textContent'))[:40]!r} unhandled={unh} pageerrors={ctx.errors[e0:]}")
            await pg.evaluate(UNPATCH)
            await pg.click("#albums .alb[data-album='Kosovo (2025)']"); await pg.wait_for_timeout(2000)

        print("\n== /api/rate and /api/pick fail: is the user told? (server untouched) ==")
        e0 = len(ctx.errors); unh.clear()
        await pg.evaluate(PATCH, {"pat": "/api/(rate|pick)", "mode": "fail"})
        tile = pg.locator("#grid .ph").nth(3)
        await tile.locator(".stars span").nth(2).click(); await pg.wait_for_timeout(500)
        stars = await pg.evaluate("document.querySelectorAll('#grid .ph')[3].querySelectorAll('.stars .on').length")
        await tile.locator(".pick").click(); await pg.wait_for_timeout(500)
        picked = await pg.evaluate("({picked: document.querySelectorAll('#grid .ph')[3].classList.contains('picked'), badge: document.getElementById('pick-count').textContent})")
        print(f"  after failed rate: stars lit={stars}; after failed pick: {picked}; any message shown: {await pg.evaluate('document.body.innerText.includes(\"Can\") || document.body.innerText.includes(\"fail\")')} | unhandled={unh} errors={ctx.errors[e0:]}")
        # undo the optimistic state locally (nothing reached the server)
        await tile.locator(".pick").click(); await tile.locator(".stars span").nth(2).click(); await pg.wait_for_timeout(300)
        await pg.evaluate(UNPATCH)
        await pg.evaluate("__libRefresh()"); await pg.wait_for_timeout(2500)
        print("  after real refresh, tile 3:", await pg.evaluate("({stars: document.querySelectorAll('#grid .ph')[3].querySelectorAll('.stars .on').length, picked: document.querySelectorAll('#grid .ph')[3].classList.contains('picked'), badge: document.getElementById('pick-count').textContent})"))

        print("\n== /api/geo fails 3x (geoFetch retries) ==")
        await pg.evaluate(PATCH, {"pat": "/api/geo", "mode": "fail"})
        t0 = time.time()
        await pg.click("#albums .alb[data-album='Vietnam (2024)']"); await pg.wait_for_timeout(4000)
        print(f"  tiles={await pg.evaluate('document.querySelectorAll(\"#grid .ph\").length')} f-place display={await pg.evaluate('document.getElementById(\"f-place\").style.display')!r} geo attempts={len([h for h in await pg.evaluate('window.__hits') if 'geo' in h['url']])}")
        await pg.evaluate(UNPATCH)

        print("\n== slow bthumb (5 s) while opening the lightbox + picks fetch ==")
        await pg.click("#albums .alb[data-album='Kosovo (2025)']"); await pg.wait_for_timeout(2500)
        await pg.evaluate("document.querySelector('#app-library main').scrollTop = 0")
        reqs = []
        pg.on("response", lambda r: reqs.append((round(time.time() - t0, 2), r.url.split('5003')[-1][:60])) if "/api/picks" in r.url or "s=2000" in r.url else None)
        t0 = time.time()
        await pg.evaluate("openPicks()"); await pg.wait_for_timeout(1500)
        print("  picks fetch timing:", reqs[:3], "| picks rows:", await pg.evaluate("document.querySelectorAll('#pick-body .pk').length"))
        await pg.evaluate("closePicks()")

        print("\n== view switching storm editor <-> library <-> picks x10 ==")
        e0 = len(ctx.errors); unh.clear()
        n0 = len(await pg.evaluate("Object.keys(window)"))
        api = []
        pg.on("request", lambda r: api.append(r.url.split('/api/')[-1][:30]) if "/api/" in r.url else None)
        for i in range(10):
            await pg.evaluate("showView('editor')"); await pg.wait_for_timeout(60)
            await pg.evaluate("showView('library')"); await pg.wait_for_timeout(60)
            await pg.evaluate("openPicks()"); await pg.wait_for_timeout(60)
            await pg.evaluate("closePicks()")
        await pg.wait_for_timeout(4000)
        from collections import Counter
        print("  api requests fired:", Counter(api).most_common(6), "| errors:", ctx.errors[e0:], "| unhandled:", unh)
        print("  state:", await pg.evaluate("({view: location.hash, libHidden: document.getElementById('app-library').hidden, edDisplay: document.getElementById('app-editor').style.display, tiles: document.querySelectorAll('#grid .ph').length, picksOpen: document.getElementById('picks').classList.contains('open'), pickRows: document.querySelectorAll('#pick-body .pk').length})"))

        print("\n== Blog Picks panel (article editor PK) with NO article open ==")
        await pg.evaluate("showView('editor')"); await pg.wait_for_timeout(300)
        e0 = len(ctx.errors); unh.clear()
        await pg.evaluate("window.pkToggle()")
        await pg.wait_for_selector("#pk-album option", state="attached", timeout=15000)
        await pg.select_option("#pk-album", "Kosovo (2025)"); await pg.evaluate("window.pkLoad()")
        await pg.wait_for_function("document.querySelector('#pk-place').style.display !== 'none'", timeout=30000)
        await pg.select_option("#pk-place", "Prizren Fortress"); await pg.wait_for_timeout(400)
        print("  pk tiles:", await pg.evaluate("document.querySelectorAll('#pk-grid .pk-ph').length"), "| follow checkbox exists:", await pg.evaluate("!!document.querySelector('#pl-follow')"))
        await pg.evaluate("document.dispatchEvent(new Event('selectionchange'))"); await pg.wait_for_timeout(600)
        await pg.evaluate("window.pkFollowSection && window.pkFollowSection()")
        print("  after follow pass with no article: pk place:", await pg.evaluate("document.querySelector('#pk-place').value"), "| errors:", ctx.errors[e0:], "| unhandled:", unh)
        await pg.evaluate("window.pkToggle()")
        report_errors(ctx, "robust")
        await b.close()

asyncio.run(main())
