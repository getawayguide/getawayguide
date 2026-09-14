"""Library misc: backup_status cost on the editor page, long tasks from the 6 s rebuild, Activity panel content.
Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_misc.py"""
import asyncio, sys, pathlib, time
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-gl=swiftshader"])
        pg = await b.new_page(viewport={"width": 1500, "height": 1000})
        pg.on("pageerror", lambda e: ctx.errors.append(f"pageerror: {e}"))
        reqs = []
        pg.on("request", lambda r: reqs.append(r.url.split('/api/')[-1][:40]) if "/api/" in r.url else None)
        t0 = time.time()
        await pg.goto(f"{API}/site/editor.html#editor", wait_until="load")
        await pg.wait_for_timeout(8000)
        print("== article-editor page load (Library hidden): /api requests in first 8 s ==")
        print("  ", reqs, "| status calls:", reqs.count("backup_status"))
        await pg.evaluate("window.addEventListener('unhandledrejection', e => console.error('UNHANDLED ' + (e.reason && (e.reason.stack || e.reason))))")

        print("\n== long tasks during 20 s on Armenia (6 s rebuild of 1082 tiles) ==")
        await lib_open(pg, ctx, "Armenia (2026)")
        await pg.wait_for_timeout(3000)
        await pg.evaluate("() => { window.__lt = []; new PerformanceObserver(l => l.getEntries().forEach(e => window.__lt.push(Math.round(e.duration)))).observe({ entryTypes: ['longtask'] }); }")
        await pg.evaluate("document.querySelector('#app-library main').scrollTop = 12000")
        await pg.wait_for_timeout(20000)
        lt = await pg.evaluate("window.__lt")
        print(f"  long tasks (>50 ms): {len(lt)}; durations ms: {sorted(lt, reverse=True)[:10]}; nodes: {await pg.evaluate('document.getElementsByTagName(\"*\").length')}")
        # measure one refresh directly
        t = await pg.evaluate("async () => { const t0 = performance.now(); await __libRefresh(); return Math.round(performance.now() - t0); }")
        r = await pg.evaluate("async () => { const t0 = performance.now(); const g = document.getElementById('grid'); const c0 = g.firstElementChild; __libRefresh(); await new Promise(r => setTimeout(r, 1500)); return { rebuilt: g.firstElementChild !== c0, scrollTop: document.querySelector('#app-library main').scrollTop }; }")
        print(f"  __libRefresh() wall {t} ms; grid rebuilt on refresh: {r}")

        print("\n== Live Activity panel ==")
        act = []
        pg.on("request", lambda r: act.append(time.time()) if "backup_activity" in r.url else None)
        await pg.evaluate("openActivity()"); await pg.wait_for_timeout(2500)
        print("  dot:", await pg.evaluate("document.getElementById('act-dot').className + ' ' + document.getElementById('act-dot').style.background"))
        print("  body:", (await pg.evaluate("document.getElementById('act-body').innerText")).replace("\n", " | ")[:500])
        d = await pg.evaluate("async () => await (await fetch('/api/backup_activity')).json()")
        print("  api:", {k: d[k] for k in d if k != "inFlight"}, "inFlight:", len(d.get("inFlight", [])))
        await pg.evaluate("closeActivity()"); n0 = len(act); await pg.wait_for_timeout(4500)
        print("  polls after close:", len(act) - n0, "| panel open:", await pg.evaluate("document.getElementById('activity').classList.contains('open')"))
        # backdrop click closes; Escape does not (documented?)
        await pg.evaluate("openActivity()"); await pg.keyboard.press("Escape"); await pg.wait_for_timeout(100)
        print("  Escape closes activity:", not await pg.evaluate("document.getElementById('activity').classList.contains('open')"))
        await pg.evaluate("closeActivity()")
        report_errors(ctx, "misc")
        await b.close()
asyncio.run(main())
