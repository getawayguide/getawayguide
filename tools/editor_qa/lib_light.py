"""Photo Library lightbox + star ratings + notes (writes ONLY QA-created entries, then removes them).
Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/lib_light.py"""
import asyncio, sys, pathlib, time, json
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

META = pathlib.Path.home() / "Backup" / "_meta"
def rj(name): return json.loads((META / name).read_text(encoding="utf-8-sig"))
ALBUM = "Kosovo (2025)"

LIGHT = """() => ({ open: document.getElementById('light').classList.contains('open'), count: document.getElementById('light-count').textContent, meta: document.getElementById('light-meta').textContent.slice(0, 40), prev: document.getElementById('nav-prev').disabled, next: document.getElementById('nav-next').disabled, img: (i => ({ complete: i.complete, w: i.naturalWidth, src: decodeURIComponent(i.src).split('name=')[1] }))(document.getElementById('light-img')), pick: document.getElementById('light-pick-text').textContent, note: document.getElementById('light-note').value, noteFor: document.getElementById('light-note').dataset.for })"""

async def main():
    ctx = Ctx()
    rat0, pk0 = rj("ratings.json"), rj("picks.json")
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        unh = []
        pg.on("console", lambda m: unh.append(m.text[:200]) if m.text.startswith("UNHANDLED") else None)
        await pg.evaluate("window.addEventListener('unhandledrejection', e => console.log('UNHANDLED ' + (e.reason && (e.reason.stack || e.reason))))")
        big = []; pg.on("request", lambda r: big.append((time.time(), r.url)) if "s=2000" in r.url else None)
        bigdone = []; pg.on("requestfinished", lambda r: bigdone.append((time.time(), r.url)) if "s=2000" in r.url else None)
        await lib_open(pg, ctx, ALBUM)
        n = await pg.evaluate("document.querySelectorAll('#grid .ph').length")
        photos = await pg.evaluate("async () => (await (await fetch('/api/backup_browse?album=Kosovo%20(2025)')).json()).photos.map(p => ({n: p.n, picked: p.picked, rating: p.rating, note: p.note}))")
        free = [i for i, ph in enumerate(photos) if not ph["picked"] and not ph["rating"] and not ph["note"]]
        print(f"== {ALBUM}: {n} tiles; {len(free)} photos with no existing rating/pick/note (QA will use these) ==")

        print("\n== open / arrows / ends / Escape ==")
        t0 = time.time()
        await pg.click("#grid .ph >> nth=0"); await pg.wait_for_timeout(100)
        s = await pg.evaluate(LIGHT); print("  open tile 0:", {k: s[k] for k in ("open", "count", "prev", "next")})
        await pg.wait_for_function("document.getElementById('light-img').complete && document.getElementById('light-img').naturalWidth > 0", timeout=30000)
        print(f"  s=2000 image loaded in {time.time()-t0:.2f}s: {(await pg.evaluate(LIGHT))['img']}")
        await pg.keyboard.press("ArrowLeft"); await pg.wait_for_timeout(100)
        print("  ArrowLeft at first:", (await pg.evaluate(LIGHT))["count"])
        t0 = time.time(); big.clear(); bigdone.clear()
        for i in range(n + 3): await pg.keyboard.press("ArrowRight", delay=0)
        dt = time.time() - t0
        s = await pg.evaluate(LIGHT)
        print(f"  {n+3} rapid ArrowRight in {dt:.2f}s -> {s['count']} next-disabled={s['next']} img={s['img']['src']}")
        await pg.wait_for_function("document.getElementById('light-img').complete && document.getElementById('light-img').naturalWidth > 0", timeout=60000)
        print(f"  final image complete after {time.time()-t0:.2f}s; s=2000 requests issued {len(big)}, finished {len(bigdone)}")
        # is /api/picks starved behind those big thumbs?
        t1 = time.time(); await pg.evaluate("fetch('/api/picks').then(r => r.json())"); print(f"  /api/picks round trip right after: {time.time()-t1:.2f}s")
        await pg.keyboard.press("ArrowRight"); await pg.wait_for_timeout(100)
        print("  ArrowRight at last:", (await pg.evaluate(LIGHT))["count"])
        await pg.keyboard.press("Escape"); await pg.wait_for_timeout(100)
        print("  Escape -> open:", (await pg.evaluate(LIGHT))["open"], "| unhandled:", unh)

        print("\n== open while thumbs still loading (bottom of grid) ==")
        await pg.evaluate("document.querySelector('#app-library main').scrollTop = 1e9")
        await pg.click(f"#grid .ph >> nth={n-1}", timeout=5000); await pg.wait_for_timeout(200)
        print("  opened last tile:", {k: v for k, v in (await pg.evaluate(LIGHT)).items() if k in ("open", "count", "next")})
        await pg.keyboard.press("Escape")

        print("\n== P key / star pick on a QA photo; persistence in picks.json ==")
        i = free[0]; name = photos[i]["n"]
        await pg.evaluate("document.querySelector('#app-library main').scrollTop = 0")
        await pg.evaluate(f"document.querySelectorAll('#grid .ph')[{i}].scrollIntoView()")
        await pg.click(f"#grid .ph >> nth={i}"); await pg.wait_for_timeout(200)
        await pg.keyboard.press("p"); await pg.wait_for_timeout(600)
        key = f"{ALBUM}/{name}"
        print(f"  P on {name}: light says {(await pg.evaluate(LIGHT))['pick']!r}; picks.json has key: {key in rj('picks.json')}; badge: {await pg.evaluate('document.getElementById(\"pick-count\").textContent')}")
        await pg.keyboard.press("p"); await pg.wait_for_timeout(600)
        print(f"  P again: {(await pg.evaluate(LIGHT))['pick']!r}; key gone: {key not in rj('picks.json')}")

        print("\n== note with quotes/emoji/newline/HTML ==")
        note = 'He said "hi" \U0001F30B <b>bold</b> & \'x\'\nline two'
        await pg.click("#light-note"); await pg.keyboard.type(note.replace("\n", ""), delay=5)
        await pg.keyboard.press("Shift+Enter"); await pg.keyboard.type("line two", delay=5)
        await pg.wait_for_timeout(900)
        saved = rj("picks.json").get(key, {})
        tile = await pg.evaluate(f"(() => {{ const t = document.querySelectorAll('#grid .ph')[{i}]; const h = t.querySelector('.hasnote'); return {{ picked: t.classList.contains('picked'), hasnote: !!h, title: h && h.title, hasBold: !!t.querySelector('.hasnote b') }}; }})()")
        print("  saved note:", repr(saved.get("note")), "| equals typed:", saved.get("note") == note.replace("\n", "\n"), "| picked by note:", key in rj("picks.json"), "| tile:", tile)
        print("  light pick text:", (await pg.evaluate(LIGHT))["pick"])
        # 'p' while typing in the note must not toggle the pick; Escape blurs, not closes
        await pg.click("#light-note"); await pg.keyboard.press("End"); await pg.keyboard.press("p"); await pg.wait_for_timeout(700)
        print("  after typing 'p' in note: still picked:", key in rj("picks.json"), "| note ends with p:", rj("picks.json").get(key, {}).get("note", "").endswith("p"))
        await pg.keyboard.press("Escape"); await pg.wait_for_timeout(100)
        print("  Escape in note -> light open:", (await pg.evaluate(LIGHT))["open"], "| focus:", await pg.evaluate("document.activeElement.id"))
        # arrow away mid-type (within the 500 ms debounce) then back: note kept?
        await pg.click("#light-note"); await pg.keyboard.press("End"); await pg.keyboard.type("Z"); await pg.keyboard.press("Escape")
        await pg.keyboard.press("ArrowRight"); await pg.wait_for_timeout(200); await pg.keyboard.press("ArrowLeft"); await pg.wait_for_timeout(700)
        print("  arrow away within debounce -> note on disk ends with Z:", rj("picks.json").get(key, {}).get("note", "").endswith("Z"), "| textarea:", repr((await pg.evaluate(LIGHT))["note"][-8:]))

        print("\n== very long note (3000 chars) ==")
        await pg.click("#light-note"); await pg.keyboard.press("Control+a"); await pg.keyboard.press("Delete")
        await pg.evaluate("() => { const t = document.getElementById('light-note'); t.value = 'L'.repeat(3000); t.dispatchEvent(new Event('input')); }")
        await pg.wait_for_timeout(900)
        print("  on disk length:", len(rj("picks.json").get(key, {}).get("note", "")), "| textarea length:", len((await pg.evaluate(LIGHT))["note"]))
        await pg.keyboard.press("Escape"); await pg.keyboard.press("Escape"); await pg.wait_for_timeout(100)
        await pg.evaluate("__libRefresh()"); await pg.wait_for_timeout(2500)
        await pg.click(f"#grid .ph >> nth={i}"); await pg.wait_for_timeout(200)
        print("  after refresh + reopen, textarea length:", len((await pg.evaluate(LIGHT))["note"]))

        print("\n== 6 s refresh while the lightbox is open with a half-typed note ==")
        await pg.click("#light-note"); await pg.keyboard.press("Control+a"); await pg.keyboard.type("half typed", delay=20)
        first = await pg.evaluate("document.querySelector('#grid .ph')")
        g0 = await pg.evaluate("window.__g0 = document.querySelector('#grid').firstElementChild; 1")
        await pg.wait_for_timeout(7000)
        s = await pg.evaluate(LIGHT)
        print("  after 7 s: note:", repr(s["note"]), "| count:", s["count"], "| grid rebuilt:", await pg.evaluate("document.querySelector('#grid').firstElementChild !== window.__g0"), "| focus:", await pg.evaluate("document.activeElement.id"))
        await pg.keyboard.press("Escape"); await pg.keyboard.press("Escape"); await pg.wait_for_timeout(200)
        # cleanup: unpick (drops the note)
        await pg.evaluate("async (k) => { const [a, n] = [k.split('/')[0], k.split('/').slice(1).join('/')]; await fetch('/api/pick', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({album: a, name: n, picked: false})}); }", key)
        print("  cleanup: key removed:", key not in rj("picks.json"))

        print("\n== star rating: set/clear, persistence, re-render cost ==")
        j = free[1]; name2 = photos[j]["n"]; key2 = f"{ALBUM}/{name2}"
        await pg.evaluate("__libRefresh()"); await pg.wait_for_timeout(2000)
        await pg.evaluate("document.querySelector('#app-library main').scrollTop = 600"); await pg.wait_for_timeout(300)
        st0 = await pg.evaluate("document.querySelector('#app-library main').scrollTop")
        thumbs = []; pg.on("request", lambda r: thumbs.append(r.url) if "/bthumb" in r.url and "s=400" in r.url else None)
        g0 = await pg.evaluate("window.__g0 = document.querySelector('#grid').firstElementChild; 1")
        await pg.evaluate(f"document.querySelectorAll('#grid .ph')[{j}].querySelectorAll('.stars span')[3].click()"); await pg.wait_for_timeout(1500)
        print(f"  4 stars on {name2}: json={rj('ratings.json').get(key2)} | tile stars on={await pg.evaluate(f'document.querySelectorAll(\"#grid .ph\")[{j}].querySelectorAll(\".stars .on\").length')} | grid rebuilt={await pg.evaluate('document.querySelector(\"#grid\").firstElementChild !== window.__g0')} | scrollTop {st0} -> {await pg.evaluate('document.querySelector(\"#app-library main\").scrollTop')} | thumb requests after rating: {len(thumbs)}")
        await pg.evaluate(f"document.querySelectorAll('#grid .ph')[{j}].querySelectorAll('.stars span')[3].click()"); await pg.wait_for_timeout(800)
        print(f"  click same star again (clear): json has key={key2 in rj('ratings.json')} | tile stars on={await pg.evaluate(f'document.querySelectorAll(\"#grid .ph\")[{j}].querySelectorAll(\".stars .on\").length')}")
        # Rated 3+ filter live after rating
        await pg.evaluate(f"document.querySelectorAll('#grid .ph')[{j}].querySelectorAll('.stars span')[2].click()"); await pg.wait_for_timeout(600)
        await pg.click("#f-rated"); await pg.wait_for_timeout(300)
        print("  Rated 3+ after rating one QA photo 3:", await pg.evaluate("[...document.querySelectorAll('#grid .ph .res')].length"), "tiles")
        await pg.click("#f-all")
        await pg.evaluate("async (k) => { const [a, n] = [k.split('/')[0], k.split('/').slice(1).join('/')]; await fetch('/api/rate', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({album: a, name: n, rating: 0})}); }", key2)

        print("\n== two tabs ==")
        pg2 = await b.new_page(viewport={"width": 1400, "height": 900})
        pg2.on("pageerror", lambda e: ctx.errors.append(f"pageerror(tab2): {e}"))
        await pg2.goto(f"{API}/site/editor.html", wait_until="load"); await pg2.wait_for_timeout(500)
        await lib_open(pg2, ctx, ALBUM)
        k, l = free[2], free[3]
        await pg.evaluate(f"document.querySelectorAll('#grid .ph')[{k}].querySelectorAll('.stars span')[1].click()")
        await pg2.evaluate(f"document.querySelectorAll('#grid .ph')[{l}].querySelectorAll('.stars span')[2].click()")
        await pg.wait_for_timeout(1200)
        r = rj("ratings.json")
        print(f"  tab1 rated {photos[k]['n']}=2, tab2 rated {photos[l]['n']}=3 simultaneously -> json: {r.get(f'{ALBUM}/{photos[k]['n']}')}, {r.get(f'{ALBUM}/{photos[l]['n']}')}")
        await pg2.evaluate("__libRefresh()"); await pg2.wait_for_timeout(2500)
        print(f"  tab2 after refresh sees tab1's rating: {await pg2.evaluate(f'document.querySelectorAll(\"#grid .ph\")[{k}].querySelectorAll(\".stars .on\").length')} stars")
        for nm in (photos[k]["n"], photos[l]["n"]):
            await pg.evaluate("async ([a, n]) => { await fetch('/api/rate', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({album: a, name: n, rating: 0})}); }", [ALBUM, nm])
        await pg2.close()

        print("\n== 20 concurrent /api/rate and /api/pick writes (QA-only keys) ==")
        res = await pg.evaluate("""async () => {
          const post = (u, b) => fetch(u, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)});
          await Promise.all([...Array(20)].map((_, i) => post('/api/rate', {album: 'QA Race', name: 'qa-' + i + '.jpg', rating: (i % 5) + 1})));
          await Promise.all([...Array(20)].map((_, i) => post('/api/pick', {album: 'QA Race', name: 'qa-' + i + '.jpg', picked: true, note: 'n' + i})));
          const picksApi = await (await fetch('/api/picks')).json();
          return { picksTotal: picksApi.total, qaInApi: picksApi.picks.filter(p => p.album === 'QA Race').length };
        }""")
        r, pk = rj("ratings.json"), rj("picks.json")
        print(f"  ratings persisted: {sum(1 for k in r if k.startswith('QA Race/'))}/20 | picks persisted: {sum(1 for k in pk if k.startswith('QA Race/'))}/20 | /api/picks total {res['picksTotal']} (pre-existing {len([k for k in pk0])}); QA (missing files) listed: {res['qaInApi']}")
        await pg.evaluate("""async () => {
          const post = (u, b) => fetch(u, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)});
          await Promise.all([...Array(20)].map((_, i) => post('/api/rate', {album: 'QA Race', name: 'qa-' + i + '.jpg', rating: 0})));
          await Promise.all([...Array(20)].map((_, i) => post('/api/pick', {album: 'QA Race', name: 'qa-' + i + '.jpg', picked: false})));
        }""")
        await pg.wait_for_timeout(500)
        r, pk = rj("ratings.json"), rj("picks.json")
        left = [k for k in list(r) + list(pk) if k.startswith("QA Race/")]
        if left:   # serial cleanup of anything the race dropped on the floor
            await pg.evaluate("""async (keys) => { for (const k of keys) { const [a, n] = [k.split('/')[0], k.split('/').slice(1).join('/')];
              await fetch('/api/rate', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({album: a, name: n, rating: 0})});
              await fetch('/api/pick', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({album: a, name: n, picked: false})}); } }""", left)
        r, pk = rj("ratings.json"), rj("picks.json")
        print(f"  after concurrent delete: QA keys left before serial cleanup: {len(left)} | now ratings == snapshot: {r == rat0} | picks == snapshot: {pk == pk0}")
        if r != rat0: print("   ratings diff:", {k: v for k, v in r.items() if rat0.get(k) != v}, {k: v for k, v in rat0.items() if r.get(k) != v})
        if pk != pk0: print("   picks diff:", [k for k in pk if k not in pk0], [k for k in pk0 if k not in pk])
        print("  unhandled:", unh)
        report_errors(ctx, "light")
        await b.close()

asyncio.run(main())
