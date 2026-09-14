"""Lightbox note: typing after Escape-blur, and arrowing away inside the debounce. Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_note2.py"""
import asyncio, sys, pathlib, json
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *
META = pathlib.Path.home() / "Backup" / "_meta"
def rj(n): return json.loads((META / n).read_text(encoding="utf-8-sig"))
async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        await lib_open(pg, ctx, "Kosovo (2025)")
        photos = await pg.evaluate("async () => (await (await fetch('/api/backup_browse?album=Kosovo%20(2025)')).json()).photos.map(p => ({n: p.n, picked: p.picked, rating: p.rating, note: p.note}))")
        free = [i for i, ph in enumerate(photos) if not ph["picked"] and not ph["rating"] and not ph["note"]]
        i = free[5]; key = f"Kosovo (2025)/{photos[i]['n']}"
        await pg.evaluate(f"document.querySelectorAll('#grid .ph')[{i}].scrollIntoView()")
        await pg.click(f"#grid .ph >> nth={i}"); await pg.wait_for_timeout(300)
        NOTE = "() => ({ta: document.getElementById('light-note').value, focus: document.activeElement.id, for: document.getElementById('light-note').dataset.for, count: document.getElementById('light-count').textContent})"
        await pg.click("#light-note"); await pg.keyboard.type("one", delay=20); await pg.wait_for_timeout(800)
        print("1 typed 'one' ->", await pg.evaluate(NOTE), "| disk:", repr(rj("picks.json").get(key, {}).get("note")))
        await pg.keyboard.press("Escape"); await pg.wait_for_timeout(100)
        print("2 Escape ->", await pg.evaluate(NOTE), "| light open:", await pg.evaluate("document.getElementById('light').classList.contains('open')"))
        await pg.click("#light-note"); await pg.wait_for_timeout(100)
        print("3 click note ->", await pg.evaluate(NOTE))
        await pg.keyboard.press("End"); await pg.keyboard.type("two", delay=20); await pg.wait_for_timeout(800)
        print("4 typed 'two' ->", await pg.evaluate(NOTE), "| disk:", repr(rj("picks.json").get(key, {}).get("note")))
        await pg.keyboard.type("Z", delay=20); await pg.wait_for_timeout(50)
        await pg.keyboard.press("Escape"); await pg.keyboard.press("ArrowRight"); await pg.wait_for_timeout(300)
        print("5 Z + Escape + ArrowRight ->", await pg.evaluate(NOTE), "| disk:", repr(rj("picks.json").get(key, {}).get("note")))
        await pg.keyboard.press("ArrowLeft"); await pg.wait_for_timeout(600)
        print("6 ArrowLeft back ->", await pg.evaluate(NOTE), "| disk:", repr(rj("picks.json").get(key, {}).get("note")))
        # type then arrow WITHOUT Escape (focus stays in textarea: arrows are swallowed by the textarea)
        await pg.click("#light-note"); await pg.keyboard.press("End"); await pg.keyboard.type("Q", delay=20); await pg.keyboard.press("ArrowRight"); await pg.wait_for_timeout(300)
        print("7 Q + ArrowRight while focused ->", await pg.evaluate(NOTE))
        await pg.keyboard.press("Escape"); await pg.keyboard.press("Escape"); await pg.wait_for_timeout(300)
        print("8 after closing: disk:", repr(rj("picks.json").get(key, {}).get("note")))
        await pg.evaluate("async (k) => { const [a, n] = [k.split('/')[0], k.split('/').slice(1).join('/')]; await fetch('/api/pick', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({album: a, name: n, picked: false})}); }", key)
        print("cleanup: key removed:", key not in rj("picks.json"))
        report_errors(ctx, "note2")
        await b.close()
asyncio.run(main())
