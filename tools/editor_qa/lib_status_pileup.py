"""How many /api/backup_status calls does ONE Library tab stack up when the route is slow?
Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_status_pileup.py"""
import asyncio, sys, pathlib, time
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        t0 = time.time(); started = {}; done = []; inflight = {"n": 0, "max": 0}; others = []
        def on_req(r):
            if "backup_status" in r.url:
                started[r.url + str(id(r))] = time.time(); inflight["n"] += 1; inflight["max"] = max(inflight["max"], inflight["n"])
            elif "/api/" in r.url or "/bthumb" in r.url: others.append((r, time.time()))
        def on_fin(r):
            if "backup_status" in r.url:
                inflight["n"] -= 1; done.append(round(time.time() - started.pop(r.url + str(id(r)), time.time()), 1))
        pg.on("request", on_req); pg.on("requestfinished", on_fin); pg.on("requestfailed", on_fin)
        slow = []
        pg.on("requestfinished", lambda r: slow.append((r.url.split('5003')[-1][:50], round(time.time() - dict((id(x), t) for x, t in others).get(id(r), time.time()), 1))) if ("/api/picks" in r.url or "/bthumb" in r.url or "backup_browse" in r.url) else None)
        await pg.evaluate("showView('library')")
        for i in range(8):
            await pg.wait_for_timeout(10000)
            print(f"  t={round(time.time()-t0)}s backup_status in flight={inflight['n']} max={inflight['max']} completed={len(done)} durations={done[-4:]} cards={await pg.evaluate('document.querySelectorAll(\"#albums .alb\").length')}")
        sl = sorted(slow, key=lambda x: -x[1])[:5]
        print("  slowest other requests while status calls were queued:", sl)
        print("  errors:", ctx.errors[:5])
        await b.close()
asyncio.run(main())
