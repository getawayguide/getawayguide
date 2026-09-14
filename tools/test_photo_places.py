"""Stress test for the photo place filter + follow-section. Needs the photo
server running (Editor Suite.cmd) and Playwright: python tools/test_photo_places.py

Stress the place filter + follow-section against REAL articles, loaded the
way the editor loads them (.article-body innerHTML into #editor), with the
caret placed in every single block of each article."""
import asyncio, collections, json, pathlib, re, sys, time
from playwright.async_api import async_playwright

OUT = pathlib.Path(".tmp/screenshots"); OUT.mkdir(parents=True, exist_ok=True)
API = "http://127.0.0.1:5003"
ARTICLES = [  # (page, backup album)
    ("armenia/field-notes.html", "Armenia (2026)"),
    ("Drafts/.Full Articles/armenia/yerevan.html", "Armenia (2026)"),
    ("Drafts/.Full Articles/armenia/armenia-itinerary.html", "Armenia (2026)"),
    ("Drafts/.Full Articles/armenia/gyumri.html", "Armenia (2026)"),
    ("Drafts/guatemala/field-notes.html", "Guatemala (2026)"),
    ("kosovo/field-notes.html", "Kosovo (2025)"),
]
errors = []

LOAD_JS = """async (rel) => {
  const html = await (await fetch('/site/' + rel)).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const body = doc.querySelector('.article-body, .artbody');
  const ed = document.getElementById('editor');
  ed.innerHTML = body.innerHTML; ed.style.display = 'block'; ed.contentEditable = 'true';
  fileHandle = { name: rel.split('/').pop() };        // what openArticle sets; the article-name fallback reads .name
  const blocks = Array.from(ed.querySelectorAll('li, p, .d-t, .d-t2')).filter(b =>
    b.textContent.trim().length > 15 && !b.querySelector('.photo-ph') && !b.closest('.v[data-v="b"], .v[data-v="c"], .v[data-v="d"], .v[data-v="e"], .v[data-v="f"], .v[data-v="g"], .v[data-v="h"], .v[data-v="i"]'));
  blocks.forEach((b, i) => b.setAttribute('data-stress', i));
  return blocks.length;
}"""

CARET_JS = """(i) => {
  const b = document.querySelector('[data-stress="' + i + '"]');
  const walker = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
  let t, last = null, afterLead = null, lead = b.querySelector('b, strong');
  while ((t = walker.nextNode())) { last = t; if (!afterLead && t.textContent.trim() && !(lead && lead.contains(t))) afterLead = t; }
  const node = afterLead || last; if (!node) return null;
  const r = document.createRange(); r.setStart(node, Math.min(2, node.textContent.length)); r.collapse(true);
  const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  const t0 = performance.now();
  const f = window.plFollowPlaces(window.PLgeoForTest);
  return { ms: performance.now() - t0, follow: f, text: b.textContent.replace(/\\s+/g, ' ').trim().slice(0, 70) };
}"""


async def open_album(pg, album):
    await pg.click("#pl-tabs :text('Backup')")
    await pg.wait_for_selector(f"#pl-folders :text('{album}')", timeout=15000)
    net = []
    t0 = time.time()
    h = lambda r: net.append(f"{time.time()-t0:5.1f}s {r.status} {r.url[-70:]}") if "/api/" in r.url else None
    pg.on("response", h)
    pg.on("requestfailed", lambda r: net.append(f"{time.time()-t0:5.1f}s FAILED {r.url[-70:]} {r.failure}") if "/api/" in r.url else None)
    await pg.click(f"#pl-folders :text('{album}')")
    try:
        await pg.wait_for_function("document.querySelector('#pl-placebar') && document.querySelector('#pl-placebar').style.display !== 'none' && !(document.querySelector('#pl-geo-state').textContent||'').startsWith('locating')", timeout=60000)
    except Exception:
        print(f"[open_album {album}] TIMEOUT. PL =", await pg.evaluate("window.plDebug()"))
        print("   crumbs:", await pg.evaluate("document.querySelector('#pl-crumbs').textContent"))
        print("   net:\n   " + "\n   ".join(net[-14:]))
        raise
    finally:
        pg.remove_listener("response", h)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        pg.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
        pg.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url[:110]}") if r.status >= 400 and "/api/" in r.url else None)
        notfound = []
        pg.on("response", lambda r: notfound.append(r.url[:100]) if r.status == 404 else None)
        await pg.goto(f"{API}/site/editor.html", wait_until="load")
        await pg.wait_for_timeout(600)
        await pg.evaluate("window.togglePhotoPanel()")
        await pg.wait_for_selector("#pl-tabs :text('Backup')", timeout=15000)

        # ---------------------------------------------------------- 1. matcher coverage
        summary = []
        for rel, album in ARTICLES:
            await open_album(pg, album)
            geo = await pg.evaluate("window.geoFetch('/api/geo?album='+encodeURIComponent(%s))" % json.dumps(album))
            await pg.evaluate("g => { window.PLgeoForTest = g; }", geo)
            n = await pg.evaluate(LOAD_JS, rel)
            levels = collections.Counter(); misses = []; times = []; examples = []; suspicious = []
            for i in range(n):
                r = await pg.evaluate(CARET_JS, i)
                if not r: continue
                times.append(r["ms"])
                f = r["follow"]; lv = f["level"] or "none"; levels[lv] += 1
                if lv == "none" and f["text"] and len(f["text"]) < 40:
                    misses.append((f["text"], r["text"][:50]))
                elif lv != "none":
                    if len(examples) < 5: examples.append((f["text"][:30], f["places"][:3], lv))
                    # a match whose first token is not in the source text is suspicious
                    p0 = f["places"][0].replace("city:", "")
                    if not any(w.lower() in f["text"].lower() for w in p0.split() if len(w) > 3):
                        suspicious.append((f["text"][:30], f["places"][:2], lv))
            print(f"\n=== {rel}  (album {album}, {geo['total']} photos)")
            print(f"    blocks {n}: lead {levels['lead']}, heading {levels['heading']}, h2 {levels['h2']}, no place {levels['none']}"
                  f" | follow pass {max(times):.1f} ms worst, {sum(times)/len(times):.2f} ms avg")
            for e in examples: print("    ok  ", e)
            for s_ in suspicious[:6]: print("    SUSPICIOUS", s_)
            for m in misses[:6]: print("    miss", repr(m[0]), "| block:", repr(m[1]))
            summary.append((rel, n, dict(levels), len(suspicious)))
            for i in range(n - 1, -1, -1):          # the live filter on the last matching block
                r = await pg.evaluate(CARET_JS, i)
                if r and r["follow"]["level"]:
                    await pg.evaluate("document.dispatchEvent(new Event('selectionchange'))")
                    await pg.wait_for_timeout(500)
                    v = await pg.evaluate("document.querySelector('#pl-place').value")
                    cnt = await pg.evaluate("document.querySelectorAll('#pl-grid .pl-ph').length")
                    st = await pg.evaluate("document.querySelector('#pl-geo-state').textContent")
                    print(f"    live: caret in {r['text'][:40]!r} -> filter {v!r}, {cnt} tiles, state {st!r}")
                    break

        # ---------------------------------------------------------- 2. typing storm in a PLACE bullet (Kosovo is loaded)
        await pg.evaluate("() => { fileHandle = null; }")     # no fake handle while input handlers run
        idx = await pg.evaluate("""() => { for (const b of document.querySelectorAll('[data-stress]')) { const l = b.querySelector('b');
            if (l && /Ura Hostel/i.test(l.textContent)) { const r = document.createRange(); r.setStart(b, b.childNodes.length); r.collapse(true);
              const s = window.getSelection(); s.removeAllRanges(); s.addRange(r); return b.getAttribute('data-stress'); } } return null; }""")
        await pg.evaluate("document.dispatchEvent(new Event('selectionchange'))")
        await pg.wait_for_timeout(600)
        v0 = await pg.evaluate("document.querySelector('#pl-place').value")
        e0 = len(errors); t0 = time.time()
        await pg.keyboard.type(" and this is a stress test of typing quickly in the editor while follow is on", delay=8)
        await pg.wait_for_timeout(700)
        v1 = await pg.evaluate("document.querySelector('#pl-place').value")
        print(f"\n[typing] in bullet {idx}: 80 keystrokes in {time.time()-t0:.1f}s; filter before {v0!r} after {v1!r}; new errors {len(errors)-e0}")

        # ---------------------------------------------------------- 3. folder switch while EXIF is still being read
        await pg.click("#pl-tabs :text('Backup')")
        await pg.wait_for_selector("#pl-folders :text('Vietnam (2024)')", timeout=15000)
        await pg.click("#pl-folders :text('Vietnam (2024)')")
        await pg.wait_for_timeout(400)
        st = await pg.evaluate("(document.querySelector('#pl-geo-state')||{}).textContent || ''")
        await pg.click("#pl-crumbs a")                                  # back to the Backup root
        await pg.wait_for_selector("#pl-folders :text('Armenia (2026)')", timeout=15000)
        t0 = time.time()
        net = []
        pg.on("response", lambda r: net.append(f"{time.time()-t0:5.1f}s {r.status} {r.url[-60:]}") if "/api/" in r.url else None)
        await pg.click("#pl-folders :text('Armenia (2026)')")
        stale = await pg.evaluate("Array.from(document.querySelectorAll('#pl-place optgroup')).map(o=>o.label)")
        try:
            await pg.wait_for_function("document.querySelector('#pl-placebar').style.display !== 'none'", timeout=30000)
        except Exception as ex:
            print("[switch] TIMEOUT waiting for the place bar; PL =", await pg.evaluate("window.plDebug()"))
            print("[switch] bar:", await pg.evaluate("(b => b && { display: b.style.display, opts: document.querySelectorAll('#pl-place option').length })(document.querySelector('#pl-placebar'))"))
            print("[switch] net:", "\n   ".join(net[-12:]))
            raise
        groups = await pg.evaluate("Array.from(document.querySelectorAll('#pl-place optgroup')).map(o=>o.label)")
        print(f"[switch] Vietnam state {st!r}; right after clicking Armenia the menu held {stale}; "
              f"Armenia menu ready in {time.time()-t0:.1f}s: {groups}")
        print(f"[404s] {len(notfound)} not-found responses, e.g. {sorted(set(u.split('/site/')[-1][:60] for u in notfound))[:4]}")

        # ---------------------------------------------------------- 4. picks panel follow (Kosovo has picks)
        await pg.evaluate(LOAD_JS, "kosovo/field-notes.html")
        await pg.evaluate("window.pkToggle()")
        await pg.wait_for_selector("#pk-album option", state="attached", timeout=15000)
        await pg.select_option("#pk-album", "Kosovo (2025)")
        await pg.evaluate("window.pkLoad()")
        await pg.wait_for_function("document.querySelector('#pk-place').style.display !== 'none'", timeout=30000)
        opts = await pg.evaluate("Array.from(document.querySelectorAll('#pk-place option')).map(o=>o.textContent)")
        lead = await pg.evaluate("""() => { for (const b of document.querySelectorAll('[data-stress]')) { const l = b.querySelector('b');
            if (l && /Prizren Fortress|Ura Hostel|League of Prizren/i.test(l.textContent)) { const r = document.createRange(); r.setStart(b, b.childNodes.length); r.collapse(true);
              const s = window.getSelection(); s.removeAllRanges(); s.addRange(r); return l.textContent; } } return null; }""")
        await pg.evaluate("document.dispatchEvent(new Event('selectionchange'))")
        await pg.wait_for_timeout(700)
        pv = await pg.evaluate("document.querySelector('#pk-place').value")
        pn = await pg.evaluate("document.querySelectorAll('#pk-grid .pk-ph').length")
        print(f"[picks] menu {opts[:6]} | caret in {lead!r} -> picks filter {pv!r}, {pn} tiles")
        await pg.locator("aside.sidebar").screenshot(path=str(OUT / "places-stress-picks.png"))
        await b.close()
        print("\nSUMMARY", json.dumps(summary))


try:
    asyncio.run(main())
finally:
    print("\nconsole/page errors:", len(errors))
    for e in errors[:12]: print("  ", e[:220])
