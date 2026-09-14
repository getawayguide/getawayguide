"""Live Activity panel content over time. Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_activity.py"""
import asyncio, sys, pathlib, time
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *
async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        await stub_status(pg); await pg.evaluate("showView('library')"); await pg.wait_for_selector("#albums .alb", timeout=60000)
        tl = []
        pg.on("response", lambda r: tl.append((round(time.time()-t0, 1), r.status)) if "backup_activity" in r.url else None)
        t0 = time.time()
        await pg.evaluate("openActivity()")
        for i in range(6):
            await pg.wait_for_timeout(1500)
            print(f"  +{time.time()-t0:.1f}s body={(await pg.evaluate('document.getElementById(\"act-body\").innerText')).replace(chr(10), ' | ')[:160]!r} dot={await pg.evaluate('document.getElementById(\"act-dot\").style.background')!r}")
        print("  activity responses:", tl)
        print("  header state:", await pg.evaluate("document.getElementById('hdr-state').textContent"))
        await pg.evaluate("closeActivity()")
        report_errors(ctx, "activity")
        await b.close()
asyncio.run(main())
