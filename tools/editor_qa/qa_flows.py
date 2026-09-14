"""General editor flows. Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/qa_flows.py"""
import asyncio, re, sys, pathlib, time, json
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

FIX = """
<p id="p1">First paragraph with some words in it.</p>
<p id="p2">Second paragraph <b>bold bit</b> and <a href="https://example.com">a link</a> here.</p>
<ul id="ul1"><li id="li1">Bullet one</li><li id="li2">Bullet two</li></ul>
<h2 class="article-h2" id="h2a">Heading two</h2>
<p id="p3">Third paragraph.</p>
"""

PASTE_JS = """({html, text}) => {
  const dt = new DataTransfer();
  if (html) dt.setData('text/html', html);
  if (text) dt.setData('text/plain', text);
  const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
  document.getElementById('editor').dispatchEvent(ev);
  return ev.defaultPrevented;
}"""

GDOCS = ('<meta charset="utf-8"><b style="font-weight:normal;" id="docs-internal-guid-1234">'
         '<p dir="ltr" style="line-height:1.38;margin-top:0pt;margin-bottom:0pt;"><span style="font-size:11pt;font-family:Arial,sans-serif;color:#000000;background-color:transparent;font-weight:400;font-style:normal;font-variant:normal;text-decoration:none;vertical-align:baseline;white-space:pre;white-space:pre-wrap;">Pasted from Docs </span>'
         '<span style="font-size:11pt;font-family:Arial,sans-serif;color:#000000;background-color:transparent;font-weight:700;">bold part</span>'
         '<span style="font-size:11pt;color:#1155cc;"><a href="https://maps.app.goo.gl/x" style="text-decoration:none;">map</a></span></p>'
         '<p dir="ltr" style="line-height:1.38;"><span style="font-size:11pt;font-family:Arial;color:#000000;">Second line</span></p></b><br class="Apple-interchange-newline">')

async def status(pg):
    return await pg.evaluate("({dirty: isDirty, status: document.getElementById('save-status').textContent, depth: __History.depth()})")

async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        kos = (QA / "kosovo-field-notes.html").read_text(encoding="utf-8")

        print("== dirty flag / Ctrl+S / autosave ==")
        await load_real(pg, "kosovo-field-notes.html", kos)
        print("  after load:", await status(pg))
        await caret(pg, "#editor li", at_end=True)
        await pg.keyboard.type(" typed")
        await pg.wait_for_timeout(100)
        print("  after typing:", await status(pg))
        await pg.keyboard.press("Control+s"); await pg.wait_for_timeout(400)
        print("  after Ctrl+S:", await status(pg), "saved bytes:", len(await pg.evaluate("window.__saved || ''")))
        print("  autosave default:", await pg.evaluate("__autosave()"), "| btn:", await pg.evaluate("document.getElementById('btn-autosave').textContent"))
        await pg.click("#btn-autosave"); print("  after toggle:", await pg.evaluate("__autosave()"), await pg.evaluate("localStorage.getItem('editorAutosave')"))
        await pg.click("#btn-autosave"); print("  after toggle back:", await pg.evaluate("__autosave()"))
        await pg.keyboard.type("x"); await pg.wait_for_timeout(100)
        await pg.evaluate("window.__saved = null; autosaveTick()"); await pg.wait_for_timeout(400)
        print("  autosaveTick with dirty doc -> wrote:", bool(await pg.evaluate("window.__saved")), "|", await status(pg))
        await pg.evaluate("autosaveOn = false; window.__saved = null"); await pg.keyboard.type("y"); await pg.wait_for_timeout(100)
        await pg.evaluate("autosaveTick()"); await pg.wait_for_timeout(300)
        print("  autosaveTick with autosave OFF -> wrote:", bool(await pg.evaluate("window.__saved")))
        await pg.evaluate("autosaveOn = true")

        print("\n== History: typing bursts + Ctrl+Z / Ctrl+Y ==")
        await inject(pg, "flows.html", FIX)
        await caret(pg, "#p1", at_end=True)
        await pg.keyboard.type(" burst-one"); await pg.wait_for_timeout(600)
        await pg.keyboard.type(" burst-two"); await pg.wait_for_timeout(600)
        t = lambda: pg.evaluate("document.getElementById('p1').textContent")
        print("  typed:", await t(), await status(pg))
        await pg.keyboard.press("Control+z"); print("  undo1:", await t())
        await pg.keyboard.press("Control+z"); print("  undo2:", await t())
        await pg.keyboard.press("Control+z"); print("  undo3 (nothing left):", await t())
        await pg.keyboard.press("Control+y"); print("  redo1:", await t())
        await pg.keyboard.press("Control+Shift+z"); print("  redo2 (ctrl+shift+z):", await t())
        # undo immediately after a keystroke (inside the 450 ms coalesce window)
        await pg.keyboard.type(" quick"); await pg.keyboard.press("Control+z"); print("  type+immediate undo:", await t())
        # bold via Ctrl+B then undo
        await caret(pg, "#p1", select_all=True); await pg.keyboard.press("Control+b"); await pg.wait_for_timeout(500)
        print("  after Ctrl+B:", await pg.evaluate("document.getElementById('p1').innerHTML")[:80] if False else (await pg.evaluate("document.getElementById('p1').innerHTML"))[:80])
        await pg.keyboard.press("Control+z"); await pg.wait_for_timeout(100)
        print("  undo bold:", (await pg.evaluate("document.getElementById('p1').innerHTML"))[:80])
        print("  active btns after select-all bold text:", await active_blocks(pg))

        print("\n== Ctrl+K link ==")
        await inject(pg, "flows.html", FIX)
        await caret(pg, "#p1", select_all=True)
        ctx.dialog_reply = "https://example.org/test"
        await pg.keyboard.press("Control+k"); await pg.wait_for_timeout(200)
        print("  dialogs:", ctx.dialogs[-1:], "| p1:", await pg.evaluate("document.getElementById('p1').innerHTML"))
        await pg.keyboard.press("Control+z"); await pg.wait_for_timeout(100)
        print("  undo link:", await pg.evaluate("document.getElementById('p1').innerHTML"))
        ctx.dialog_reply = None   # cancel
        await caret(pg, "#p3", select_all=True); await pg.keyboard.press("Control+k"); await pg.wait_for_timeout(200)
        print("  cancelled prompt -> p3:", await pg.evaluate("document.getElementById('p3').innerHTML"))
        # collapsed caret + link
        ctx.dialog_reply = "https://x.y"; await caret(pg, "#p3", off=3); await pg.keyboard.press("Control+k"); await pg.wait_for_timeout(200)
        print("  collapsed caret link -> p3:", await pg.evaluate("document.getElementById('p3').innerHTML"))
        # javascript: url
        ctx.dialog_reply = "javascript:alert(1)"; await caret(pg, "#p1", select_all=True); await pg.keyboard.press("Control+k"); await pg.wait_for_timeout(200)
        print("  javascript: url -> p1:", await pg.evaluate("document.getElementById('p1').innerHTML"))
        ctx.dialog_reply = None
        # toolbar Link button with focus already in editor
        await caret(pg, "#p2", select_all=True); ctx.dialog_reply = "https://btn.example"; await pg.click(".toolbar .tool-btn:text-is('Link')"); await pg.wait_for_timeout(200)
        print("  toolbar Link -> p2:", await pg.evaluate("document.getElementById('p2').innerHTML"))
        ctx.dialog_reply = None

        print("\n== lists / indent / outdent / term list ==")
        await inject(pg, "flows.html", FIX)
        await caret(pg, "#p3", off=2); await pg.click(".toolbar .tool-btn:text-is('• List')"); await pg.wait_for_timeout(80)
        print("  p3 -> bullet:", await pg.evaluate("(document.getElementById('p3')||{}).outerHTML || document.querySelector('#editor ul:last-of-type').outerHTML"))
        await caret(pg, "#li2", off=2); await pg.keyboard.press("Tab"); await pg.wait_for_timeout(80)
        print("  Tab on li2:", await pg.evaluate("document.getElementById('ul1').outerHTML"))
        await pg.keyboard.press("Shift+Tab"); await pg.wait_for_timeout(80)
        print("  Shift+Tab:", await pg.evaluate("document.getElementById('ul1').outerHTML"))
        await pg.wait_for_timeout(500); await pg.keyboard.press("Control+z"); await pg.wait_for_timeout(80)
        print("  Ctrl+Z after outdent:", await pg.evaluate("document.getElementById('ul1').outerHTML"))
        # Enter on empty top-level li -> exits to <p>
        await caret(pg, "#li2", at_end=True); await pg.keyboard.press("Enter"); await pg.keyboard.press("Enter"); await pg.keyboard.type("after list")
        print("  Enter,Enter on last li:", await pg.evaluate("document.getElementById('ul1').outerHTML + ' || ' + document.getElementById('ul1').nextElementSibling.outerHTML"))
        # Tab in a paragraph (not li) -> browser default (focus moves?)
        await caret(pg, "#p1", off=2); await pg.keyboard.press("Tab"); await pg.wait_for_timeout(80)
        print("  Tab in <p> -> focus:", await pg.evaluate("document.activeElement.id || document.activeElement.tagName"), "| p1:", await pg.evaluate("document.getElementById('p1').innerHTML")[:60] if False else (await pg.evaluate("document.getElementById('p1').innerHTML"))[:70])
        # Term list mid-paragraph
        await inject(pg, "flows.html", FIX)
        await caret(pg, "#p1", off=6); await pg.click(".toolbar .tool-btn:text-is('≡ Term')"); await pg.wait_for_timeout(80)
        print("  Term mid-<p>:", await pg.evaluate("document.getElementById('p1').outerHTML")[:300] if False else (await pg.evaluate("document.getElementById('p1').outerHTML"))[:320])
        print("  ul inside p?:", await pg.evaluate("!!document.querySelector('#editor p ul')"))
        await pg.evaluate("saveFile()"); await pg.wait_for_timeout(300)
        sv = await pg.evaluate("window.__saved"); i = sv.find("Term"); print("  saved around Term:", sv[max(0, i-250):i+120].replace("\n", "\\n"))
        # term list on empty selection at end of editor + Enter behaviour in the term li
        await inject(pg, "flows.html", FIX)
        await caret(pg, "#p3", at_end=True); await pg.click(".toolbar .tool-btn:text-is('≡ Term')"); await pg.wait_for_timeout(80)
        await pg.keyboard.press("Enter"); await pg.keyboard.type("Second term")
        print("  Term @end + Enter:", (await pg.evaluate("document.querySelector('#editor ul[style]').outerHTML"))[:400])

        print("\n== Pair block ==")
        await inject(pg, "flows.html", FIX)
        await caret(pg, "#p1", off=6)
        e0 = len(ctx.errors)
        await pg.click(".toolbar .tool-btn:has-text('Pair')"); await pg.wait_for_timeout(200)
        print("  pair inserted:", await pg.evaluate("() => { const p = document.querySelector('#editor .img-pair'); return p ? { parent: p.parentElement.tagName + '#' + p.parentElement.id, prev: p.previousElementSibling && p.previousElementSibling.outerHTML.slice(0,60), next: p.nextElementSibling && p.nextElementSibling.outerHTML.slice(0,60), html: p.outerHTML.slice(0,200) } : null; }"))
        print("  p1 after pair:", await pg.evaluate("[...document.querySelectorAll('#editor p')].map(p=>p.textContent).slice(0,3)"))
        print("  photos panel open:", await pg.evaluate("document.body.classList.contains('photos-open')"), "| errors:", ctx.errors[e0:])
        await pg.wait_for_timeout(500); await pg.keyboard.press("Control+z"); await pg.wait_for_timeout(100)
        print("  Ctrl+Z after pair -> pairs:", await pg.evaluate("document.querySelectorAll('#editor .img-pair').length"), "| p1:", await pg.evaluate("document.getElementById('p1') && document.getElementById('p1').textContent"))
        await pg.keyboard.press("Control+y"); await pg.wait_for_timeout(100)
        await pg.evaluate("saveFile()"); await pg.wait_for_timeout(300)
        sv = await pg.evaluate("window.__saved"); print("  empty pair saved?:", "img-pair" in sv, "| slot placeholders saved?:", "img-slot" in sv or "photo-ph" in sv)
        await pg.screenshot(path=str(SHOTS / "flows-pair.png"))
        await pg.evaluate("if (document.body.classList.contains('photos-open')) togglePhotoPanel()")

        print("\n== highlight / color / font selects ==")
        await inject(pg, "flows.html", FIX)
        await caret(pg, "#p1", select_all=True)
        await pg.click(".toolbar .tool-btn:has-text('H')", timeout=3000) if False else None
        await pg.evaluate("saveSelection(); applyHilite('#FFF3B0')"); await pg.wait_for_timeout(80)
        print("  hilite:", await pg.evaluate("document.getElementById('p1').innerHTML"))
        await pg.evaluate("saveSelection(); applyHilite('transparent')"); await pg.wait_for_timeout(80)
        print("  remove hilite:", await pg.evaluate("document.getElementById('p1').innerHTML"))
        await pg.evaluate("saveFile()"); await pg.wait_for_timeout(300)
        sv = await pg.evaluate("window.__saved"); m = re.search(r'<p id="p1">.*?</p>', sv, re.S); print("  saved p1:", m and m.group(0)[:200])
        await caret(pg, "#p2", select_all=True); await pg.evaluate("saveSelection(); applyColor('#e63946')"); await pg.wait_for_timeout(80)
        print("  color:", (await pg.evaluate("document.getElementById('p2').innerHTML"))[:200])
        await caret(pg, "#p3", select_all=True)
        await pg.evaluate("saveSelection(); applyFontFamily(document.querySelector('.tool-select'), '__display__')"); await pg.wait_for_timeout(80)
        print("  font display:", await pg.evaluate("document.getElementById('p3').innerHTML"))
        await caret(pg, "#p3", off=2)
        await pg.evaluate("saveSelection(); applyFontFamily(document.querySelector('.tool-select'), '__body-bold__')"); await pg.wait_for_timeout(80)
        print("  font with collapsed caret (no-op?):", await pg.evaluate("document.getElementById('p3').innerHTML"))
        await caret(pg, "#p3", select_all=True)
        await pg.evaluate("saveSelection(); applyFontSize(document.querySelectorAll('.tool-select')[1], 't-lg')"); await pg.wait_for_timeout(80)
        print("  size t-lg:", await pg.evaluate("document.getElementById('p3').innerHTML"))
        await caret(pg, "#p3", off=3)
        await pg.evaluate("saveSelection(); applyFontSize(document.querySelectorAll('.tool-select')[1], '__reset__')"); await pg.wait_for_timeout(80)
        print("  size reset:", await pg.evaluate("document.getElementById('p3').innerHTML"))
        print("  undo chain (3x):")
        for i in range(3):
            await pg.keyboard.press("Control+z"); await pg.wait_for_timeout(60)
            print("    ", (await pg.evaluate("document.getElementById('p3').innerHTML"))[:120])

        print("\n== image modal open/close ==")
        await inject(pg, "flows.html", FIX)
        mod = await pg.evaluate("""() => { const bd = document.getElementById('img-modal-backdrop'); return { exists: !!bd, display: bd && getComputedStyle(bd).display, closeFns: ['closeImageModal','cancelInsertImage','closeImgModal'].filter(f => typeof window[f] === 'function') }; }""")
        print("  modal:", mod)
        await pg.evaluate("() => { const bd = document.getElementById('img-modal-backdrop'); bd.classList.add('open'); bd.style.display = 'flex'; }")
        await pg.keyboard.press("Escape"); await pg.wait_for_timeout(100)
        print("  after Escape:", await pg.evaluate("() => { const bd = document.getElementById('img-modal-backdrop'); return getComputedStyle(bd).display + ' ' + bd.className; }"))
        await pg.evaluate("() => { const bd = document.getElementById('img-modal-backdrop'); bd.classList.remove('open'); bd.style.display = ''; }")

        print("\n== paste ==")
        await inject(pg, "flows.html", FIX)
        await caret(pg, "#p3", at_end=True)
        await pg.evaluate(PASTE_JS, {"html": "", "text": "Line A\nLine B\n\nPara two <not a tag> & amp"}); await pg.wait_for_timeout(100)
        print("  plain text:", (await pg.evaluate("document.getElementById('p3').outerHTML + (document.getElementById('p3').nextElementSibling||{}).outerHTML"))[:300])
        await inject(pg, "flows.html", FIX)
        await caret(pg, "#p3", at_end=True)
        await pg.evaluate(PASTE_JS, {"html": GDOCS, "text": "Pasted from Docs bold partmap\nSecond line"}); await pg.wait_for_timeout(150)
        out = await pg.evaluate("document.getElementById('p3').outerHTML + ' || ' + [...document.querySelectorAll('#editor p')].slice(-3).map(p => p.outerHTML).join(' ')")
        print("  gdocs paste:", out[:700])
        print("  leftovers -> style attrs:", await pg.evaluate("document.querySelectorAll('#editor [style]').length"), "spans:", await pg.evaluate("document.querySelectorAll('#editor span').length"), "b tags:", await pg.evaluate("[...document.querySelectorAll('#editor b')].map(b => b.textContent.slice(0,40))"), "id attrs from docs:", await pg.evaluate("document.querySelectorAll('#editor [id^=docs-]').length"))
        await pg.wait_for_timeout(500); await pg.keyboard.press("Control+z"); await pg.wait_for_timeout(80)
        print("  undo paste -> p count:", await pg.evaluate("document.querySelectorAll('#editor p').length"), "p3:", await pg.evaluate("document.getElementById('p3').textContent"))
        # paste HTML while a word is selected (replaces?)
        await caret(pg, "#p1", select_all=True)
        await pg.evaluate(PASTE_JS, {"html": "<span style=\"color:red\">RED</span> <h1>Big</h1><script>window.__pwned=1</script><img src=x onerror=\"window.__pwned2=1\">", "text": "RED Big"}); await pg.wait_for_timeout(200)
        print("  html paste over selection:", (await pg.evaluate("document.getElementById('editor').innerHTML"))[:260].replace("\n", " "), "| pwned:", await pg.evaluate("[window.__pwned, window.__pwned2]"))

        print("\n== keyboard shortcuts ==")
        await inject(pg, "flows.html", FIX)
        await caret(pg, "#p1", select_all=True)
        for key, what in [("Control+b", "b,strong"), ("Control+i", "i,em"), ("Control+u", "u")]:
            await pg.keyboard.press(key); await pg.wait_for_timeout(60)
            print(f"  {key}: {await pg.evaluate('document.getElementById(\"p1\").innerHTML')}  active={await active_blocks(pg)}")
        await pg.keyboard.press("Control+f"); await pg.wait_for_timeout(100)
        print("  Ctrl+F find bar visible:", await pg.evaluate("() => { const f = document.getElementById('find-q'); return f && getComputedStyle(f.closest('div')).display !== 'none' && document.activeElement === f; }"))
        await pg.keyboard.type("paragraph"); await pg.wait_for_timeout(300)
        print("  find hits:", await pg.evaluate("document.querySelectorAll('#editor mark.find-hit').length"))
        await pg.keyboard.press("Escape"); await pg.wait_for_timeout(100)
        print("  after Esc marks:", await pg.evaluate("document.querySelectorAll('#editor mark.find-hit').length"), "| focus:", await pg.evaluate("document.activeElement.id||document.activeElement.tagName"))
        await pg.keyboard.press("Control+h"); await pg.wait_for_timeout(100); await pg.keyboard.press("Escape")
        # Ctrl+Z while focus is in the find box must not undo the article
        await caret(pg, "#p1", at_end=True); await pg.keyboard.type(" ZZZ"); await pg.wait_for_timeout(600)
        await pg.keyboard.press("Control+f"); await pg.keyboard.type("abc"); await pg.keyboard.press("Control+z"); await pg.wait_for_timeout(100)
        print("  Ctrl+Z inside find box -> p1 still has ZZZ:", "ZZZ" in await pg.evaluate("document.getElementById('p1').textContent"), "| find-q value:", await pg.evaluate("document.getElementById('find-q').value"))
        await pg.keyboard.press("Escape")

        print("\n== Enter/Backspace edge cases ==")
        await inject(pg, "flows.html", FIX)
        await caret(pg, "#h2a", at_end=True); await pg.keyboard.press("Enter"); await pg.keyboard.type("after h2")
        print("  Enter@end h2 ->", await pg.evaluate("document.getElementById('h2a').nextElementSibling.outerHTML"))
        await caret(pg, "#h2a", off=0); await pg.keyboard.press("Enter")
        print("  Enter@start h2 ->", await pg.evaluate("document.getElementById('h2a').previousElementSibling.outerHTML"), "| h2 still:", await pg.evaluate("document.getElementById('h2a').outerHTML"))
        await caret(pg, "#editor", off=0)
        await pg.keyboard.press("Control+a"); await pg.keyboard.press("Delete"); await pg.keyboard.type("fresh")
        print("  select-all delete type ->", await pg.evaluate("document.getElementById('editor').innerHTML")[:120] if False else (await pg.evaluate("document.getElementById('editor').innerHTML"))[:160])
        await pg.wait_for_timeout(500); await pg.keyboard.press("Control+z"); await pg.wait_for_timeout(100)
        print("  undo -> blocks:", await pg.evaluate("document.getElementById('editor').children.length"))

        print("\n== long article (guatemala, read-only fake handle) + typing storm ==")
        gua = await pg.evaluate("async () => await (await fetch('/site/Drafts/guatemala/field-notes.html')).text()")
        t0 = time.time()
        n = await pg.evaluate(LOAD_REAL_JS, {"name": "field-notes.html", "html": gua})
        t1 = time.time() - t0
        blocks = await pg.evaluate("document.querySelectorAll('#editor p, #editor li, #editor h2, #editor h3').length")
        print(f"  loaded {n} chars, {blocks} blocks in {t1:.2f}s; spell-walk running for ~{blocks*0.03:.1f}s")
        e0 = len(ctx.errors)
        # type during the spell walk (the walk moves the selection every 30 ms!)
        await caret(pg, "#editor li", at_end=True)
        await pg.keyboard.type("DURINGWALK", delay=15)
        await pg.wait_for_timeout(100)
        where = await pg.evaluate("() => { const t = [...document.querySelectorAll('#editor *')].filter(e => /DURINGWALK/.test(e.textContent) && !e.querySelector('*')).map(e => e.tagName + ':' + e.textContent.slice(0, 40)); const li = document.querySelector('#editor li'); return { where: t.slice(0,3), firstLi: li.textContent.slice(-30), intact: (document.getElementById('editor').textContent.match(/DURINGWALK/g)||[]).length, fragments: (document.getElementById('editor').textContent.match(/D?U?R?I?N?G?W?A?L?K?/g)||[]).filter(x=>x.length>1 && x!=='DURINGWALK').length }; }")
        print("  typed during spell-walk ->", where, "| errors:", ctx.errors[e0:])
        await pg.evaluate(STOP_SPELLWALK_JS); await pg.wait_for_timeout(200)
        await pg.evaluate("() => { if (window.__History) __History.reset(); }")
        await caret(pg, "#editor li", at_end=True)
        storm = "".join("abcdefghij"[i % 10] for i in range(260))
        e0 = len(ctx.errors); t0 = time.time()
        await pg.keyboard.type(storm, delay=4)
        dt = time.time() - t0
        got = await pg.evaluate("document.querySelector('#editor li').textContent")
        print(f"  260 keystrokes in {dt:.2f}s; text intact: {storm in got}; errors: {ctx.errors[e0:]}; history: {await pg.evaluate('__History.depth()')}")
        t0 = time.time(); await pg.keyboard.press("Control+z"); await pg.wait_for_timeout(100)
        got2 = await pg.evaluate("document.querySelector('#editor li').textContent")
        print(f"  Ctrl+Z after storm removed all typed text: {storm not in got2 and 'DURINGWALK' in got2 or storm not in got2}; undo took {(time.time()-t0)*1000:.0f}ms; li tail: {got2[-30:]!r}")
        # selectionchange storm: arrow through 300 positions
        e0 = len(ctx.errors); t0 = time.time()
        for _ in range(150): await pg.keyboard.press("ArrowRight")
        for _ in range(150): await pg.keyboard.press("ArrowDown")
        print(f"  300 caret moves in {time.time()-t0:.2f}s; errors: {ctx.errors[e0:]}")
        await pg.evaluate("setDirty(false)")

        report_errors(ctx, "flows")
        await b.close()

asyncio.run(main())
