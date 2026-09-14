"""Photo Library: album cards, counts, iPhone-count input, 0-photo albums, weird names.
Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_cards.py"""
import asyncio, sys, pathlib, time, json, re
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

from qa_common import PATCH, UNPATCH

async def cards(pg):
    return await pg.evaluate("""() => [...document.querySelectorAll('#albums .alb')].map(el => ({
      name: el.dataset.album, on: el.classList.contains('on'), files: (el.querySelector('.nm span:last-child')||{}).textContent,
      sub: (el.querySelector('.sub')||{textContent:''}).textContent, chk: (el.querySelector('.chk')||{textContent:''}).textContent, bar: (el.querySelector('.bar i')||{style:{}}).style.width,
      exp: (el.querySelector('.exp')||{}).value, hasDel: !!el.querySelector('.del'), hasUndo: !!el.querySelector('.undo'), hasHold: !!el.querySelector('.hold') }))""")

async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        await pg.evaluate("window.addEventListener('unhandledrejection', e => console.error('UNHANDLED ' + (e.reason && (e.reason.stack || e.reason))))")
        await pg.evaluate("showView('library')")
        await pg.wait_for_selector("#albums .alb", timeout=20000); await pg.wait_for_timeout(1500)
        st = await pg.evaluate("async () => await (await fetch('/api/backup_status')).json()")
        cs = await cards(pg)
        print("== cards vs /api/backup_status ==")
        print("  header:", await pg.evaluate("document.getElementById('hdr-albums').textContent"), "|", await pg.evaluate("document.getElementById('hdr-state').textContent"))
        bad = []
        for a, c in zip(st["albums"], cs):
            files = (a.get("onDisk") or 0) + (a.get("videos") or 0)
            if a["name"] != c["name"] or str(files) != c["files"]: bad.append((a["name"], files, c["files"]))
        print(f"  {len(cs)} cards, {len(st['albums'])} albums; file-count mismatches: {bad}")
        tot = sum((a.get("onDisk") or 0) + (a.get("videos") or 0) for a in st["albums"])
        print("  computed total files:", tot, "| first (auto-opened):", [c["name"] for c in cs if c["on"]])
        for c in cs:
            if c["name"] in ("Cologne (2023)", "Egypt (2023)", "Kosovo (2025)", "Italy (2023)", "Armenia (2026)", "Patagonia (2024)"):
                print(f"  {c['name']:26s} files={c['files']:>5} bar={c['bar']:>5} exp={c['exp']!r} del={c['hasDel']} undo={c['hasUndo']} | {c['sub'][:60]} | {c['chk'][:90]}")

        print("\n== 0-photo album card (Egypt) ==")
        await pg.click("#albums .alb[data-album='Egypt (2023)']"); await pg.wait_for_timeout(2500)
        print("  title:", await pg.evaluate("document.getElementById('tb-title').textContent"), "| tiles:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"), "| empty:", (await pg.evaluate("document.getElementById('empty').textContent"))[:90], "| f-place:", await pg.evaluate("document.getElementById('f-place').style.display"))
        await pg.click("#albums .alb[data-album='Cologne (2023)']"); await pg.wait_for_timeout(2500)
        print("  Cologne tiles:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"), "| empty:", (await pg.evaluate("document.getElementById('empty').textContent"))[:90])

        print("\n== iPhone-count input: what the client sends (intercepted, server untouched) ==")
        await pg.evaluate(PATCH, {"pat": "/api/expected", "mode": "swallow"})
        inp = pg.locator("#albums .alb[data-album='Vietnam (2024)'] input.exp")
        orig = await inp.input_value()
        for val in ["", "abc", "-5", "1e3", "0", "99999999999999999999", " 42 "]:
            await inp.fill(""); await inp.type(val) if val else None
            await inp.press("Enter"); await pg.wait_for_timeout(250)
            await inp.press("Tab"); await pg.wait_for_timeout(250)
            hits = await pg.evaluate("() => { const h = window.__hits; window.__hits = []; return h.map(x => x.body); }")
            print(f"  typed {val!r:24s} -> POST bodies {hits}")
            # loadAlbums() after the swallowed POST re-renders the cards; re-locate
            inp = pg.locator("#albums .alb[data-album='Vietnam (2024)'] input.exp")
        await pg.evaluate(UNPATCH)
        await pg.evaluate("__libRefresh()"); await pg.wait_for_timeout(1500)
        print("  Vietnam expected after tests (must equal original):", await pg.locator("#albums .alb[data-album='Vietnam (2024)'] input.exp").input_value(), "orig:", orig)

        print("\n== server /api/expected with a QA-only album key ==")
        for val in ["abc", "-5", "1e3", "0", "007", "99999999999999999999", " 42 ", ""]:
            r = await pg.evaluate("async (v) => (await (await fetch('/api/expected', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({album:'QA Test Album', count: v})})).json()).expected['QA Test Album']", val)
            print(f"  count={val!r:24s} -> stored {r!r}")
        r = await pg.evaluate("async () => (await (await fetch('/api/expected', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({album:'QA Test Album', count: ''})})).json()).expected")
        print("  cleaned up; QA key present:", "QA Test Album" in r)

        print("\n== typing in the count box while the 6 s refresh fires ==")
        inp = pg.locator("#albums .alb[data-album='Vietnam (2024)'] input.exp")
        await inp.click(); await inp.fill(""); await inp.type("12")
        await pg.evaluate(PATCH, {"pat": "/api/expected", "mode": "swallow"})
        await pg.evaluate("__libRefresh()"); await pg.wait_for_timeout(1500)
        inp2 = pg.locator("#albums .alb[data-album='Vietnam (2024)'] input.exp")
        print("  after refresh: value:", repr(await inp2.input_value()), "| focused:", await pg.evaluate("document.activeElement && document.activeElement.className"), "| POSTs fired:", await pg.evaluate("window.__hits.length"))
        await pg.evaluate(UNPATCH)
        await pg.evaluate("__libRefresh()"); await pg.wait_for_timeout(1200)

        print("\n== weird album names / held / archived rendering (fake backup_status) ==")
        fake = {"running": False, "albums": [
            {"name": "O'Brien's <b>Trip</b> (2026) éè 日本", "onDisk": 3, "videos": 1, "mb": 12.3, "videosMb": 1, "inAlbum": 4, "expected": None, "state": "done"},
            {"name": "Held Album (2026)", "onDisk": 0, "videos": 0, "mb": 0, "inAlbum": 50, "held": True},
            {"name": "Ticked Album (2026)", "onDisk": 10, "videos": 0, "mb": 5, "inAlbum": 10, "expected": 10, "archived": "2026-09-01", "verify": {"ok": True}},
            {"name": "Short Album (2026)", "onDisk": 5, "videos": 0, "mb": 5, "inAlbum": 10, "expected": 12, "verify": {"ok": False, "covered": True, "shortOfPhone": 2, "albumItems": 10}, "pending": 1, "onServer": 11},
        ]}
        await pg.evaluate(PATCH, {"pat": "/api/backup_status", "mode": "body", "body": json.dumps(fake)})
        await pg.evaluate(PATCH.replace("window.fetch = async function", "const f2 = window.fetch; window.fetch = async function").replace("return window.__origFetch(url, opts);\n  };", "return f2(url, opts);\n  };"), {"pat": "/api/(hold|archived|backup_browse)", "mode": "swallow"})
        await pg.evaluate("__libRefresh()"); await pg.wait_for_timeout(1500)
        cs = await cards(pg)
        for c in cs: print("  ", {k: (v[:70] if isinstance(v, str) else v) for k, v in c.items()})
        print("  bold injected via name?:", await pg.evaluate("!!document.querySelector('#albums .nm b')"))
        await pg.click("#albums .alb:first-child"); await pg.wait_for_timeout(600)
        print("  clicked weird name -> browse URL:", [h["url"][-90:] for h in await pg.evaluate("window.__hits") if "backup_browse" in h["url"]][-1:], "| title:", await pg.evaluate("document.getElementById('tb-title').textContent"))
        await pg.click("#albums .alb:nth-child(2) .hold"); await pg.wait_for_timeout(400)
        await pg.click("#albums .alb:nth-child(3) .undo"); await pg.wait_for_timeout(400)
        print("  hold/undo POST bodies:", [h["body"] for h in await pg.evaluate("window.__hits") if h["body"]])
        await pg.locator("#albums").screenshot(path=str(SHOTS / "lib-cards-fake.png"))
        await pg.evaluate(UNPATCH)

        print("\n== album disappears mid-session (fake status without the open album) ==")
        await pg.evaluate("__libRefresh()"); await pg.wait_for_timeout(1500)
        await pg.click("#albums .alb[data-album='Kosovo (2025)']"); await pg.wait_for_timeout(2000)
        without = {"running": True, "albums": [a for a in st["albums"] if a["name"] != "Kosovo (2025)"]}
        await pg.evaluate(PATCH, {"pat": "/api/backup_status", "mode": "body", "body": json.dumps(without)})
        await pg.evaluate(PATCH.replace("window.fetch = async function", "const f2 = window.fetch; window.fetch = async function").replace("return window.__origFetch(url, opts);\n  };", "return f2(url, opts);\n  };"), {"pat": "/api/backup_browse", "mode": "body", "body": '{"photos":[]}'})
        await pg.evaluate("__libRefresh()"); await pg.wait_for_timeout(1500)
        print("  cards:", await pg.evaluate("document.querySelectorAll('#albums .alb').length"), "| on:", await pg.evaluate("[...document.querySelectorAll('#albums .alb.on')].map(e=>e.dataset.album)"), "| title:", await pg.evaluate("document.getElementById('tb-title').textContent"), "| tiles:", await pg.evaluate("document.querySelectorAll('#grid .ph').length"), "| empty:", (await pg.evaluate("document.getElementById('empty').textContent"))[:80])
        await pg.evaluate(UNPATCH)
        report_errors(ctx, "cards")
        await b.close()

if __name__ == '__main__':
    asyncio.run(main())
