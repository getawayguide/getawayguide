"""Follow-up 2: Photo Library #f-place, empty-h2 survival on save, Sub Hd inside list-in-p.
Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/qa_followup2.py"""
import asyncio, re, sys, pathlib, time
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

FIX = """
<p id="p1">First paragraph with some words in it.</p>
<h2 class="article-h2" id="h2a">Heading two</h2>
<p id="p3">Third paragraph.</p>
"""

async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)

        print("== Enter at start of h2, then save: does the empty duplicate-id h2 survive? ==")
        await inject(pg, "f.html", FIX)
        await caret(pg, "#h2a", off=0); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(50)
        await pg.evaluate("saveFile()"); await pg.wait_for_timeout(300)
        sv = await pg.evaluate("window.__saved"); m = re.search(r'<div class="article-body">(.*?)</div>\s*</body>', sv, re.S)
        print("  SAVED:", m.group(1).replace("\n", "\\n"))
        print("  id=h2a count in saved:", sv.count('id="h2a"'))

        print("\n== Sub Hd with caret in a list Chromium nested inside <p> ==")
        await inject(pg, "f.html", FIX)
        await caret(pg, "#p1", off=3); await pg.click(".toolbar .tool-btn:text-is('• List')"); await pg.wait_for_timeout(80)
        print("  dom:", (await pg.evaluate("document.getElementById('editor').children[0].outerHTML"))[:120])
        await pg.click(".toolbar .tool-btn:has-text('Sub Hd')"); await pg.wait_for_timeout(80)
        print("  after Sub Hd:", (await pg.evaluate("document.getElementById('editor').children[0].outerHTML"))[:200])
        print("  p .fn-sub-hd:", await pg.evaluate("document.querySelectorAll('#editor p .fn-sub-hd').length"))
        await pg.evaluate("saveFile()"); await pg.wait_for_timeout(300)
        sv = await pg.evaluate("window.__saved"); m = re.search(r'<div class="article-body">(.*?)</div>\s*</body>', sv, re.S)
        print("  SAVED:", m.group(1).replace("\n", "\\n")[:400])

        print("\n== Ctrl+S / Save button with no article open ==")
        await pg.evaluate("setDirty(false)"); await pg.reload(wait_until="load"); await pg.wait_for_timeout(400)
        await pg.keyboard.press("Control+s"); await pg.wait_for_timeout(200)
        print("  dialogs:", ctx.dialogs, "| save btn disabled:", await pg.evaluate("document.getElementById('btn-save').disabled"), "| errors:", ctx.errors)

        print("\n== Shared Albums root: folder count / scroll ==")
        await pg.evaluate("window.togglePhotoPanel()")
        await pg.wait_for_selector("#pl-tabs :text('Shared Albums')", timeout=15000)
        await pg.click("#pl-tabs :text('Shared Albums')"); await pg.wait_for_timeout(2500)
        print("  ", await pg.evaluate("() => { const f = document.querySelector('#pl-folders'); return { n: f.children.length, h: f.clientHeight, sh: f.scrollHeight, scrolls: f.scrollHeight > f.clientHeight, gridH: document.querySelector('#pl-grid').clientHeight, tiles: document.querySelectorAll('#pl-grid .pl-ph').length }; }"))
        await pg.locator("#photo-panel").screenshot(path=str(SHOTS / "places-folders-shared.png"))
        await pg.evaluate("window.togglePhotoPanel()")

        print("\n== Photo Library view: #f-place ==")
        await pg.evaluate("showView('library')"); await pg.wait_for_timeout(1500)
        await pg.click("#albums :text('Kosovo (2025)')", timeout=15000)
        await pg.wait_for_function("document.getElementById('f-place').style.display !== 'none'", timeout=30000)
        await pg.wait_for_timeout(500)
        fp = await pg.evaluate("() => { const s = document.getElementById('f-place'); return { display: s.style.display, opts: [...s.options].map(o => o.textContent), value: s.value, tiles: document.querySelectorAll('#grid .ph').length }; }")
        print("  f-place:", fp)
        await pg.select_option("#f-place", " nogps"); await pg.wait_for_timeout(400)
        print("  nogps -> tiles:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"), "titles:", await pg.evaluate("[...document.querySelectorAll('#grid .ph')].map(t => t.title).slice(0,3)"))
        await pg.select_option("#f-place", "city:Prizren"); await pg.wait_for_timeout(400)
        print("  city:Prizren -> tiles:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"), "empty msg:", repr(await pg.evaluate("document.getElementById('empty').textContent")))
        # switch album while a place is chosen -> filter must reset, no stale options
        names = await pg.evaluate("[...document.querySelectorAll('#albums *')].map(e => e.textContent.trim()).filter(t => /^[A-Z][^\\n]{2,40}\\(20\\d\\d\\)$/.test(t))")
        names = list(dict.fromkeys(names))
        print("  albums:", names[:6])
        others = [n for n in names if "Kosovo" not in n][:3]
        e0 = len(ctx.errors)
        for nme in others:
            await pg.click(f"#albums :text('{nme}')", timeout=5000); await pg.wait_for_timeout(100)
        early = await pg.evaluate("() => { const s = document.getElementById('f-place'); return { display: s.style.display, first: s.options[0] && s.options[0].textContent, n: s.options.length, value: s.value }; }")
        await pg.wait_for_timeout(5000)
        late = await pg.evaluate("() => { const s = document.getElementById('f-place'); return { display: s.style.display, first: s.options[0] && s.options[0].textContent, n: s.options.length, value: s.value, tiles: document.querySelectorAll('#grid .ph').length, hdr: document.getElementById('hdr-state').textContent }; }")
        print("  rapid switch ->", others, "| right after:", early, "| +5s:", late, "| errors:", ctx.errors[e0:])
        await pg.screenshot(path=str(SHOTS / "library-fplace.png"))
        await pg.evaluate("showView('editor')")

        report_errors(ctx, "followup2")
        await b.close()

asyncio.run(main())
