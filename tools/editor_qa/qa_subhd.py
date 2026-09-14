"""Sub Hd / EYEBROW button matrix. Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/qa_subhd.py"""
import asyncio, json, re, sys, pathlib
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

GIF = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
FIX = f"""
<p id="p1">Intro paragraph text here, long enough to matter.</p>
<p id="pempty"></p>
<p id="pbr"><br></p>
<h2 class="article-h2" id="h2a">Heading two</h2>
<ul id="ul1"><li id="li1">Bullet one<ul><li id="li1n">Nested bullet</li></ul></li><li id="li2">Bullet two</li></ul>
<ul id="term" style="list-style:none;padding:0;margin:1rem 0 1.5rem"><li id="tli" style="padding:.7rem 0"><b>Term</b> — Description</li></ul>
<div class="fn-sub-hd" id="sub-keep">Existing sub hd</div>
<div class="eyebrow" id="eye-keep">Existing eyebrow</div>
<h3 class="article-h3" id="h3-keep">An h3</h3>
<blockquote id="bq"><p id="bqp">Quoted para</p></blockquote>
<div class="img-pair" id="pair"><span class="pair-slot" id="slot1"><img src="{GIF}"></span><span class="pair-slot"><img src="{GIF}"></span></div>
<p id="plast">Last paragraph.</p>
"""

SUB = ".toolbar .tool-btn:has-text('Sub Hd')"
EYE = ".toolbar .tool-btn:text-is('EYEBROW')"

INSPECT = """() => {
  const ed = document.getElementById('editor');
  const bad = ed.querySelectorAll('p .fn-sub-hd, li .fn-sub-hd, h2 .fn-sub-hd, h3 .fn-sub-hd, p .eyebrow, li .eyebrow, h2 .eyebrow, h3 .eyebrow, .fn-sub-hd .fn-sub-hd, .eyebrow .eyebrow, .fn-sub-hd .eyebrow, .eyebrow .fn-sub-hd');
  const outside = document.querySelectorAll('.fn-sub-hd, .eyebrow');
  const inEd = ed.querySelectorAll('.fn-sub-hd, .eyebrow');
  const s = window.getSelection(); const an = s.anchorNode && (s.anchorNode.nodeType===3? s.anchorNode.parentElement : s.anchorNode);
  const labels = [...inEd].map(d => d.tagName.toLowerCase()+'.'+d.className+'#'+(d.id||'')+'['+d.textContent.trim().slice(0,20)+'] parent='+d.parentElement.tagName.toLowerCase()+'#'+(d.parentElement.id||'')+' prev='+(d.previousElementSibling? d.previousElementSibling.tagName.toLowerCase()+'#'+(d.previousElementSibling.id||''):'-'));
  return { nested: bad.length, total: inEd.length, strayOutsideEditor: outside.length - inEd.length,
    labels, selIn: an ? (an.closest('.fn-sub-hd,.eyebrow') ? 'in-label' : an.tagName.toLowerCase()+'#'+(an.id||'')) : 'none',
    active: [...document.querySelectorAll('.toolbar .tool-btn.tool-active')].map(b=>b.textContent.trim()),
    focus: document.activeElement && (document.activeElement.id || document.activeElement.tagName) };
}"""

async def run_case(pg, ctx, name, setup, button, presses=1, check_undo=True):
    await inject(pg, "subhd.html", FIX)
    await setup()
    before = await ed_html(pg)
    e0 = len(ctx.errors)
    for _ in range(presses):
        await pg.click(button)
        await pg.wait_for_timeout(60)
    after = await ed_html(pg)
    info = await pg.evaluate(INSPECT)
    line = f"  {name:44s} total={info['total']} nested={info['nested']} stray={info['strayOutsideEditor']} sel={info['selIn']} active={info['active']} focus={info['focus']}"
    res = {"name": name, "info": info, "undo_ok": None, "redo_ok": None, "errors": ctx.errors[e0:]}
    if check_undo:
        await pg.wait_for_timeout(550)
        await pg.keyboard.press("Control+z")
        await pg.wait_for_timeout(80)
        undo = await ed_html(pg)
        await pg.keyboard.press("Control+y")
        await pg.wait_for_timeout(80)
        redo = await ed_html(pg)
        res["undo_ok"] = (undo == before); res["redo_ok"] = (redo == after)
        line += f" undo={'ok' if res['undo_ok'] else 'FAIL'} redo={'ok' if res['redo_ok'] else 'FAIL'}"
    print(line)
    for l in info["labels"]:
        print("      ->", l)
    if res["errors"]: print("      ERRORS:", res["errors"])
    return res, after

async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        results = []
        C = lambda sel, **k: (lambda: caret(pg, sel, **k))

        print("\n== Sub Hd / EYEBROW insertion matrix ==")
        results.append(await run_case(pg, ctx, "p mid-text -> SubHd", C("#p1", off=6), SUB))
        results.append(await run_case(pg, ctx, "empty <p></p> -> SubHd", C("#pempty"), SUB))
        results.append(await run_case(pg, ctx, "<p><br></p> -> SubHd", C("#pbr"), SUB))
        results.append(await run_case(pg, ctx, "h2 -> SubHd", C("#h2a", off=3), SUB))
        results.append(await run_case(pg, ctx, "nested li -> SubHd", C("#li1n", off=3), SUB))
        results.append(await run_case(pg, ctx, "top li2 -> SubHd", C("#li2", off=3), SUB))
        results.append(await run_case(pg, ctx, "term-list li -> SubHd", C("#tli", at_end=True), SUB))
        results.append(await run_case(pg, ctx, "term-list <b> -> Eyebrow", C("#tli b", off=2), EYE))
        results.append(await run_case(pg, ctx, "existing fn-sub-hd -> Eyebrow (keep id)", C("#sub-keep", off=3), EYE))
        results.append(await run_case(pg, ctx, "existing eyebrow -> SubHd (keep id)", C("#eye-keep", off=3), SUB))
        results.append(await run_case(pg, ctx, "h3 -> SubHd (keep id)", C("#h3-keep", off=2), SUB))
        results.append(await run_case(pg, ctx, "h3 -> Eyebrow (keep id)", C("#h3-keep", off=2), EYE))
        results.append(await run_case(pg, ctx, "blockquote>p -> SubHd", C("#bqp", off=2), SUB))
        results.append(await run_case(pg, ctx, "pair slot (img) -> SubHd", C("#slot1"), SUB))
        results.append(await run_case(pg, ctx, "editor start (editor,0) -> SubHd", C("#editor"), SUB))
        results.append(await run_case(pg, ctx, "editor end -> SubHd", C("#editor", at_end=True), SUB))
        results.append(await run_case(pg, ctx, "selection spanning p1..h2a -> SubHd",
            lambda: pg.evaluate("""() => { const r = document.createRange(); r.setStart(document.querySelector('#p1').firstChild, 6); r.setEnd(document.querySelector('#h2a').firstChild, 4); const s = getSelection(); s.removeAllRanges(); s.addRange(r); document.getElementById('editor').focus(); s.removeAllRanges(); s.addRange(r); }"""), SUB))
        results.append(await run_case(pg, ctx, "select-all of p1 -> SubHd", C("#p1", select_all=True), SUB))
        results.append(await run_case(pg, ctx, "SubHd x3 on p1 (repeat)", C("#p1", off=6), SUB, presses=3))
        r, _ = await run_case(pg, ctx, "SubHd on p1 then EYEBROW then SubHd", C("#p1", off=6), SUB, check_undo=False)
        await pg.click(EYE); await pg.wait_for_timeout(60); await pg.click(SUB); await pg.wait_for_timeout(60)
        print("      after toggle:", await pg.evaluate(INSPECT))
        await pg.wait_for_timeout(550)
        for i in range(4):
            await pg.keyboard.press("Control+z"); await pg.wait_for_timeout(80)
            print(f"      undo#{i+1}:", (await pg.evaluate(INSPECT))["labels"] or "(no labels)")
        for i in range(4):
            await pg.keyboard.press("Control+y"); await pg.wait_for_timeout(80)
        print("      after 4 redo:", (await pg.evaluate(INSPECT))["labels"])

        print("\n== focus outside editor ==")
        await inject(pg, "subhd.html", FIX)
        await caret(pg, "#p1", off=4)
        todo = await pg.evaluate("() => { const t = document.querySelector('.todo-text'); if (!t) return 'no-todo'; const r = document.createRange(); r.selectNodeContents(t); r.collapse(false); const s = getSelection(); s.removeAllRanges(); s.addRange(r); t.focus(); return t.textContent.slice(0,30); }")
        print("   todo focus:", todo)
        await pg.click(SUB); await pg.wait_for_timeout(100)
        print("   after SubHd with selection in to-do:", await pg.evaluate(INSPECT))
        print("   to-do text now:", await pg.evaluate("() => { const t = document.querySelector('.todo-text'); return t ? t.innerHTML.slice(0,120) : null; }"))
        await pg.screenshot(path=str(SHOTS / "subhd-todo-focus.png"))

        print("\n== no article open ==")
        await pg.evaluate("setDirty(false)"); await pg.reload(wait_until="load"); await pg.wait_for_timeout(400)
        await pg.click("#empty-state")
        await pg.click(SUB); await pg.wait_for_timeout(100)
        print("   labels anywhere in document:", await pg.evaluate("[...document.querySelectorAll('.fn-sub-hd,.eyebrow')].map(d=>d.parentElement.id||d.parentElement.className||d.parentElement.tagName)"))
        print("   empty-state html:", await pg.evaluate("document.getElementById('empty-state').innerHTML.replace(/\\s+/g,' ').slice(0,200)"))
        await pg.screenshot(path=str(SHOTS / "subhd-no-article.png"))

        print("\n== typing + Enter after insert ==")
        await inject(pg, "subhd.html", FIX)
        await caret(pg, "#p1", at_end=True)
        await pg.click(SUB); await pg.wait_for_timeout(60)
        await pg.keyboard.type("Where to eat")
        await pg.keyboard.press("Enter")
        await pg.keyboard.type("Next paragraph text")
        print("   after Enter:", await pg.evaluate("() => { const d = document.querySelector('#editor .fn-sub-hd'); return { sub: d && d.outerHTML, next: d && d.nextElementSibling && d.nextElementSibling.outerHTML.slice(0,140) }; }"))
        await caret(pg, "#eye-keep", off=8); await pg.keyboard.press("Enter")
        print("   Enter mid-eyebrow:", await pg.evaluate("() => [...document.querySelectorAll('#editor .eyebrow')].map(d=>d.outerHTML)"))
        await caret(pg, "#sub-keep", off=0); await pg.keyboard.press("Backspace")
        print("   Backspace@start of sub-keep:", await pg.evaluate("() => { const h = document.querySelector('#editor #h3-keep'); const p = h && h.previousElementSibling; return p && p.outerHTML.slice(0,160); }"))
        await inject(pg, "subhd.html", FIX)
        await caret(pg, "#sub-keep", at_end=True); await pg.keyboard.press("Enter"); await pg.keyboard.type("xyz")
        print("   Enter@end of sub-keep ->", await pg.evaluate("() => { const d = document.querySelector('#sub-keep'); return d.nextElementSibling && d.nextElementSibling.outerHTML; }"))
        await caret(pg, "#h3-keep", at_end=True); await pg.keyboard.press("Enter"); await pg.keyboard.type("abc")
        print("   Enter@end of h3 ->", await pg.evaluate("() => { const d = document.querySelector('#h3-keep'); return d.nextElementSibling && d.nextElementSibling.outerHTML; }"))

        print("\n== computed styles ==")
        await inject(pg, "subhd.html", FIX)
        cs = await pg.evaluate("""() => { const g = (sel) => { const e = document.querySelector('#editor ' + sel); const c = getComputedStyle(e); return { fontFamily: c.fontFamily, fontSize: c.fontSize, weight: c.fontWeight, borderLeft: c.borderLeftWidth + ' ' + c.borderLeftStyle + ' ' + c.borderLeftColor, tt: c.textTransform, ls: c.letterSpacing, color: c.color, margin: c.marginTop + '/' + c.marginBottom, pl: c.paddingLeft }; };
          return { subhd: g('.fn-sub-hd'), eyebrow: g('.eyebrow'), h3: g('h3'), h2: g('h2'), p: g('p') }; }""")
        for k, v in cs.items(): print(f"   {k}: {v}")
        fonts = await pg.evaluate("() => ({ newsreader: document.fonts.check('24px Newsreader'), hanken: document.fonts.check('16px \"Hanken Grotesk\"') })")
        print("   fonts loaded:", fonts)
        await pg.locator("#editor").screenshot(path=str(SHOTS / "subhd-styles.png"))

        print("\n== save serialization ==")
        await inject(pg, "subhd.html", FIX)
        await caret(pg, "#h3-keep", off=1); await pg.click(SUB); await pg.wait_for_timeout(60)
        await caret(pg, "#li1n", off=1); await pg.click(EYE); await pg.wait_for_timeout(60)
        await caret(pg, "#p1", off=1); await pg.click(SUB); await pg.wait_for_timeout(60)
        await pg.keyboard.type("Typed heading")
        await pg.evaluate("saveFile()"); await pg.wait_for_timeout(300)
        saved = await pg.evaluate("window.__saved")
        m = re.findall(r'<(?:div|p|li|h3)[^>]*class="(?:fn-sub-hd|eyebrow)"[^>]*>.*?</(?:div|p|li)>', saved)
        print("   saved label blocks:", m)
        print("   dirty after save:", await pg.evaluate("isDirty"), "| status:", await pg.evaluate("document.getElementById('save-status').textContent"))
        (QA / "subhd-saved.html").write_text(saved, encoding="utf-8")
        print("   saved page ->", QA / "subhd-saved.html")
        i = saved.find('class="eyebrow"'); print("   nested-li eyebrow context:", saved[max(0,i-220):i+80].replace("\n", "\\n"))

        report_errors(ctx, "subhd")
        await b.close()

asyncio.run(main())
