"""Photo Library Blog Picks panel (writes ONLY QA-created picks, then removes them).
Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_picks.py"""
import asyncio, sys, pathlib, time, json
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

META = pathlib.Path.home() / "Backup" / "_meta"
def rj(name): return json.loads((META / name).read_text(encoding="utf-8-sig"))
ALBUM = "Vietnam (2024)"

async def main():
    ctx = Ctx()
    pk0 = rj("picks.json")
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        unh = []
        pg.on("console", lambda m: unh.append(m.text[:200]) if m.text.startswith("UNHANDLED") else None)
        await pg.evaluate("window.addEventListener('unhandledrejection', e => console.log('UNHANDLED ' + (e.reason && (e.reason.stack || e.reason))))")
        await lib_open(pg, ctx, ALBUM)
        api = await pg.evaluate("async () => await (await fetch('/api/picks')).json()")
        photos = await pg.evaluate("async () => (await (await fetch('/api/backup_browse?album=Vietnam%20(2024)')).json()).photos.map(p => ({n: p.n, picked: p.picked, note: p.note}))")
        free = [i for i, ph in enumerate(photos) if not ph["picked"] and not ph["note"]]
        badge = await pg.evaluate("document.getElementById('pick-count').textContent")
        print(f"== picks: api total {api['total']} across {len(api['albums'])} albums; badge {badge!r}; {ALBUM} has {sum(1 for ph in photos if ph['picked'])} picks ==")

        print("\n== add / remove via the tile + ==")
        i = free[0]; name = photos[i]["n"]; key = f"{ALBUM}/{name}"
        await pg.evaluate(f"document.querySelectorAll('#grid .ph')[{i}].querySelector('.pick').click()"); await pg.wait_for_timeout(600)
        print(f"  + on {name}: tile picked={await pg.evaluate(f'document.querySelectorAll(\"#grid .ph\")[{i}].classList.contains(\"picked\")')} json={key in rj('picks.json')} badge={await pg.evaluate('document.getElementById(\"pick-count\").textContent')!r}")
        await pg.evaluate("openPicks()"); await pg.wait_for_timeout(800)
        rows = await pg.evaluate("[...document.querySelectorAll('#pick-body .pk .fn')].map(f => f.textContent)")
        groups = await pg.evaluate("[...document.querySelectorAll('#pick-body .pk-group')].map(f => f.textContent)")
        print(f"  panel (This album): groups={groups} rows={rows}")
        await pg.click("#sc-all"); await pg.wait_for_timeout(1000)
        rows_all = await pg.evaluate("document.querySelectorAll('#pick-body .pk').length"); groups_all = await pg.evaluate("[...document.querySelectorAll('#pick-body .pk-group')].map(f => f.textContent)")
        print(f"  panel (All): {rows_all} rows (api total now {api['total'] + 1}); groups={groups_all}")
        print("  thumbs in panel loading:", await pg.evaluate("[...document.querySelectorAll('#pick-body .pk img')].filter(i => i.src).length"), "/", rows_all)
        # note edit from the panel
        row = pg.locator("#pick-body .pk").filter(has_text=name)
        await row.locator("textarea").click(); await pg.keyboard.type("from the picks panel", delay=5); await pg.wait_for_timeout(900)
        print(f"  panel note saved: {rj('picks.json').get(key, {}).get('note')!r} | saved chip on: {await row.locator('.saved').evaluate('e => e.classList.contains(\"on\")')} | grid tile hasnote: {await pg.evaluate(f'!!document.querySelectorAll(\"#grid .ph\")[{i}].querySelector(\".hasnote\")')}")
        # remove from the panel
        await row.locator("button.drop").click(); await pg.wait_for_timeout(800)
        print(f"  remove: json has key={key in rj('picks.json')} | tile picked={await pg.evaluate(f'document.querySelectorAll(\"#grid .ph\")[{i}].classList.contains(\"picked\")')} | badge={await pg.evaluate('document.getElementById(\"pick-count\").textContent')!r} | rows now {await pg.evaluate('document.querySelectorAll(\"#pick-body .pk\").length')}")
        await pg.keyboard.press("Escape"); await pg.wait_for_timeout(100)
        print("  Escape closes panel:", not await pg.evaluate("document.getElementById('picks').classList.contains('open')"))

        print("\n== jump from a pick in another album ==")
        await pg.evaluate("openPicks()"); await pg.click("#sc-all"); await pg.wait_for_timeout(1000)
        other = await pg.evaluate("() => { const r = [...document.querySelectorAll('#pick-body .pk')].find(r => !r.closest('#pick-body').querySelector('.pk-group') || true); const rows = [...document.querySelectorAll('#pick-body .pk')]; const grp = [...document.querySelectorAll('#pick-body .pk-group')]; const kos = grp.find(g => g.textContent === 'Kosovo (2025)'); const row = kos && kos.nextElementSibling; return row ? row.querySelector('.fn').textContent : null; }")
        await pg.evaluate("() => { const grp = [...document.querySelectorAll('#pick-body .pk-group')].find(g => g.textContent === 'Kosovo (2025)'); grp.nextElementSibling.querySelector('img').click(); }")
        await pg.wait_for_timeout(3000)
        st = await pg.evaluate("({picksOpen: document.getElementById('picks').classList.contains('open'), light: document.getElementById('light').classList.contains('open'), count: document.getElementById('light-count').textContent, meta: document.getElementById('light-meta').textContent.slice(0,30), on: document.querySelector('#albums .alb.on').dataset.album, title: document.getElementById('tb-title').textContent})")
        print(f"  jumped to {other}: {st}")
        await pg.keyboard.press("Escape")

        print("\n== pick pointing at a file that no longer exists ==")
        ghost = f"{ALBUM}/ghost-does-not-exist.HEIC"
        await pg.evaluate("async () => { await fetch('/api/pick', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({album: 'Vietnam (2024)', name: 'ghost-does-not-exist.HEIC', picked: true, note: 'ghost note'})}); }")
        api2 = await pg.evaluate("async () => await (await fetch('/api/picks?album=Vietnam%20(2024)')).json()")
        print(f"  ghost in picks.json: {ghost in rj('picks.json')} | api lists it: {any(p['n'].startswith('ghost') for p in api2['picks'])} | api total {api2['total']} (before {api['total']})")
        await pg.evaluate("openPicks()"); await pg.click("#sc-album"); await pg.wait_for_timeout(800)
        print("  panel shows ghost:", await pg.evaluate("[...document.querySelectorAll('#pick-body .pk .fn')].some(f => f.textContent.startsWith('ghost'))"), "| any way to delete it from the UI: no (filtered out)")
        await pg.keyboard.press("Escape")
        await pg.evaluate("async () => { await fetch('/api/pick', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({album: 'Vietnam (2024)', name: 'ghost-does-not-exist.HEIC', picked: false})}); }")

        print("\n== switching album while the panel is open (via jumpTo path) and badge sync ==")
        await pg.evaluate("openPicks()"); await pg.wait_for_timeout(500)
        await pg.evaluate("document.querySelector('#albums .alb[data-album=\"Kosovo (2025)\"]').click()"); await pg.wait_for_timeout(2500)
        print("  after clicking Kosovo card behind the panel: rows:", await pg.evaluate("document.querySelectorAll('#pick-body .pk').length"), "| panel header scope:", await pg.evaluate("document.querySelector('.scope.on').id"), "| CUR title:", await pg.evaluate("document.getElementById('tb-title').textContent"), "| empty text:", (await pg.evaluate("(document.getElementById('pick-empty')||{textContent:''}).textContent"))[:60])
        await pg.evaluate("closePicks()")
        print("  badge vs api after everything:", await pg.evaluate("document.getElementById('pick-count').textContent"), "vs", (await pg.evaluate("async () => (await (await fetch('/api/picks')).json()).total")))
        pk = rj("picks.json")
        print("  picks.json == snapshot:", pk == pk0, "| unhandled:", unh)
        if pk != pk0: print("   diff:", [k for k in pk if k not in pk0], [k for k in pk0 if k not in pk])
        report_errors(ctx, "picks")
        await b.close()

asyncio.run(main())
