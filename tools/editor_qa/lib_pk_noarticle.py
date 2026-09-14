"""Editor sidebar Blog Picks (PK) with no article open. Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_pk_noarticle.py"""
import asyncio, sys, pathlib
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
        await pg.evaluate("window.togglePhotoPanel()")
        await pg.wait_for_selector("#pl-tabs :text('Backup')", timeout=20000)
        await pg.evaluate("window.pkToggle()")
        await pg.wait_for_selector("#pk-album option", state="attached", timeout=15000)
        print("pk albums:", await pg.evaluate("[...document.querySelectorAll('#pk-album option')].map(o => o.value)"), "| badge:", await pg.evaluate("document.getElementById('pk-n').textContent"))
        await pg.select_option("#pk-album", "Kosovo (2025)"); await pg.evaluate("window.pkLoad()")
        await pg.wait_for_function("document.querySelector('#pk-place').style.display !== 'none'", timeout=30000)
        print("pk tiles:", await pg.evaluate("document.querySelectorAll('#pk-grid .pk-ph').length"), "| place opts:", await pg.evaluate("document.querySelectorAll('#pk-place option').length"))
        await pg.select_option("#pk-place", "Prizren Fortress"); await pg.wait_for_timeout(400)
        print("after manual place:", await pg.evaluate("({tiles: document.querySelectorAll('#pk-grid .pk-ph').length, plFollow: !!document.querySelector('#pl-follow') && document.querySelector('#pl-follow').checked})"))
        # follow with no article: enable follow then fire a selection change
        if await pg.evaluate("!!document.querySelector('#pl-follow')"):
            await pg.check("#pl-follow")
        await pg.evaluate("document.dispatchEvent(new Event('selectionchange'))"); await pg.wait_for_timeout(700)
        await pg.evaluate("window.pkFollowSection && window.pkFollowSection()"); await pg.wait_for_timeout(300)
        print("after follow pass (no article):", await pg.evaluate("({place: document.querySelector('#pk-place').value, tiles: document.querySelectorAll('#pk-grid .pk-ph').length, state: (document.querySelector('#pl-geo-state')||{textContent:''}).textContent})"), "| errors:", ctx.errors, "| unhandled:", unh)
        # click a pick tile with no article open: what happens?
        ctx.dialog_reply = None
        await pg.click("#pk-grid .pk-ph >> nth=0"); await pg.wait_for_timeout(1500)
        print("click a pick with no article:", await pg.evaluate("({crop: document.getElementById('crop-backdrop') && document.getElementById('crop-backdrop').classList.contains('open')})"), "| dialogs:", ctx.dialogs, "| errors:", ctx.errors, "| unhandled:", unh)
        await pg.screenshot(path=str(SHOTS / "pk-no-article.png"))
        await b.close()
asyncio.run(main())
