"""The photo editor fills the caption box by itself, the way it fills alt text (editor.html openCropForPhoto; Kevin,
2026-11-08: "it should fire in the photo editor like the alt text refill").

Headless, nothing on disk is written: the fixture is the Ohrid draft read into memory, /api/caption, /api/alt_text and
/api/import are played by the test, saves go to a stub. Needs the photo server on :5003 for the photo library.

    python tools/test_editor_auto_caption.py
"""
import json
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
SLOT = "display:block;position:relative;overflow:hidden;width:100%;aspect-ratio:2/3;border-radius:2px"
PH = "width:100%;height:100%;background:#F2F0E9"


def fixture():
    # the Ohrid guide, read into memory only: the draft until the Nov 10 launch moved it live
    src = next(p for p in (ROOT / "Drafts/Upcoming/north-macedonia/ohrid.html", ROOT / "north-macedonia/ohrid.html") if p.is_file())
    html = src.read_text(encoding="utf-8")
    html = html.replace("https://getawayguide.io/north-macedonia/ohrid.html", "https://example.invalid/zz-test/ohrid-test.html")
    pairs = list(re.finditer(r'<div class="img-pair">[\s\S]*?</div>', html))
    for pair_at in (2, 1):                         # pairs 2 and 3 get an empty right half
        p = pairs[pair_at]
        cells = list(re.finditer(r'<span style="[^"]*aspect-ratio:2/3[^"]*">[\s\S]*?</span>', p.group(0)))
        c = cells[1]
        marker = '<span style="%s"><span class="photo-ph" style="%s">[PHOTO]</span></span>' % (SLOT, PH)
        new = p.group(0)[:c.start()] + marker + p.group(0)[c.end():]
        html = html[:p.start()] + new + html[p.end():]
    return html


OPEN = """async ([text, name]) => { folderHandle = null; window.__disk = text;
  await window.loadFile({ kind: 'file', name, getFile: async () => new File([window.__disk], name, { type: 'text/html' }),
    createWritable: async () => ({ write: async h => { window.__disk = h; }, close: async () => {} }) }); }"""
checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))


WAIT_OPEN = "document.querySelector('#crop-backdrop.open') && document.getElementById('crop-country')"
SLOT_OF = """i => { const p = [...document.querySelectorAll('#editor .img-pair')].filter(x => x.querySelector('.img-slot-empty, .photo-ph'))[i];
  return p; }"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    errs, calls, delay = [], [], {"ms": 0}
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: d.dismiss())

    def caption_route(r):
        calls.append(json.loads(r.request.post_data or "{}"))
        n = len(calls)
        if delay["ms"]:
            pg.wait_for_timeout(0)
        r.fulfill(json={"ok": True, "caption": "Auto caption number %d" % n, "model": "test"})

    pg.route("**/api/caption", caption_route)
    pg.route("**/api/alt_text", lambda r: r.fulfill(json={"ok": True, "alt": "A test alt text"}))
    pg.route("**/api/import", lambda r: r.fulfill(json={"ok": True, "src": "Images/North Macedonia/Ohrid/__cap_test.jpg"}))
    pg.route("**/api/pipeline/queue", lambda r: r.fulfill(json={"ok": True, "queued": False}))
    pg.goto((ROOT / "editor.html").as_uri() + "#editor")
    pg.wait_for_timeout(1500)
    pg.evaluate(OPEN, [fixture(), "ohrid.html"])
    pg.wait_for_timeout(2000)
    ALBUM = "North Macedonia (2026)"
    names = [n for n in pg.request.get("http://127.0.0.1:5003/api/browse?root=5&path=North%20Macedonia%20(2026)").json()["photos"] if n.lower().endswith(".jpg")][:6]
    assert len(names) >= 4, names
    PH_OF = lambda i: {"root": 5, "path": ALBUM + "/" + names[i], "name": names[i]}

    def open_on(ph, js_drop):
        pg.evaluate("([ph, src]) => { window.__testDrop = (new Function('return ' + src))(); window.openCropForPhoto(ph, window.__testDrop); }", [ph, js_drop])
        pg.wait_for_selector("#crop-backdrop.open", timeout=60000)

    END = "({ where: 'end', el: null })"
    # 1. a new photo: the box fills from the photo, and the request names the photo itself
    open_on(PH_OF(0), END)
    pg.wait_for_function("document.getElementById('crop-caption').value.startsWith('Auto caption')", timeout=30000)
    first = calls[-1]
    photo = first.get("photo") or {}
    ok("the caption box fills while the dialog is open", True)
    ok("the request names the open photo", photo.get("root") is not None and photo.get("path"), first)
    ok("a lone photo sends no partners", first.get("images") == [], first)
    ok("the page's captions go along as style examples", len(first.get("examples") or []) > 0, first)
    pg.wait_for_timeout(1200)
    ok("the stage load keeps the answer", pg.evaluate("document.getElementById('crop-caption').value").startswith("Auto caption"))
    pg.evaluate("closeCropModal()")
    pg.wait_for_timeout(400)
    SLOT_JS = "(() => { const e = [...document.querySelectorAll('#editor .img-slot-empty, #editor .photo-ph')][%d]; return { where: 'slot', el: e.closest('span[style], .img-slot-empty') || e }; })"

    # 2. a pair's empty half: the other photo goes along, this one in the right place
    pg.evaluate("""() => { const e = document.querySelector('#editor .img-slot-empty, #editor .photo-ph'); const c = e.closest('.img-pair').nextElementSibling;
      if (c && c.classList.contains('img-caption')) c.remove(); }""")
    n0 = len(calls)
    open_on(PH_OF(1), SLOT_JS % 0 + "()")
    pg.wait_for_function("n => document.getElementById('crop-caption').value.startsWith('Auto caption')", arg=n0, timeout=30000)
    c = calls[-1]
    ok("a pair's other photo is sent with it", len(c.get("images") or []) == 1, c)
    ok("the new photo is placed on the right", (c.get("photo") or {}).get("index") == 1, c)
    pg.evaluate("closeCropModal()")
    pg.wait_for_timeout(400)

    # 3. words of his under the frame are left alone: no request at all
    pg.evaluate("""() => { const e = [...document.querySelectorAll('#editor .img-slot-empty, #editor .photo-ph')][0];
      const f = e.closest('.img-pair'); let c = f.nextElementSibling;
      if (!c || !c.classList.contains('img-caption')) { c = document.createElement('div'); c.className = 'img-caption'; f.after(c); }
      c.textContent = 'Words Kevin typed'; }""")
    n0 = len(calls)
    open_on(PH_OF(1), SLOT_JS % 0 + "()")
    pg.wait_for_timeout(1800)
    ok("a caption he wrote means no request", len(calls) == n0, calls[n0:])
    ok("and it shows in the box", pg.evaluate("document.getElementById('crop-caption').value") == "Words Kevin typed")
    pg.evaluate("closeCropModal()")
    pg.wait_for_timeout(400)

    # 4. Edit of a placed photo: no request
    n0 = len(calls)
    open_on(PH_OF(1), "(() => ({ where: 'image', edit: true, el: document.querySelector('#editor .img-pair img') }))()")
    pg.wait_for_timeout(1800)
    ok("Edit of a placed photo means no request", len(calls) == n0, calls[n0:])
    pg.evaluate("closeCropModal()")
    pg.wait_for_timeout(400)

    # 5. typing first wins over an answer that lands later
    pg.evaluate("""() => { const f = window.fetch; window.__origFetch = f; window.fetch = (u, o) => String(u).includes('/api/caption')
      ? new Promise(r => setTimeout(r, 2500)).then(() => f(u, o)) : f(u, o); }""")
    open_on(PH_OF(2), END)
    pg.wait_for_timeout(200)
    pg.fill("#crop-caption", "My own caption")
    pg.wait_for_timeout(4500)
    ok("typing first is never replaced", pg.evaluate("document.getElementById('crop-caption').value") == "My own caption")
    pg.evaluate("closeCropModal()")
    pg.wait_for_timeout(400)

    # 6. Insert before the answer: it lands under the photo afterwards, and the caret stays put
    pg.evaluate("""() => { const f = window.__origFetch; window.fetch = (u, o) => String(u).includes('/api/caption')
      ? new Promise(r => setTimeout(r, 6000)).then(() => f(u, o)) : f(u, o); }""")
    open_on(PH_OF(3), END)
    pg.wait_for_function("document.getElementById('crop-country').value || (document.getElementById('crop-country').value = 'North Macedonia')", timeout=30000)
    pg.wait_for_timeout(1500)
    before_cap = pg.evaluate("document.getElementById('crop-caption').value")
    pg.click("#crop-go")
    pg.wait_for_selector("#editor img[data-path*='__cap_test.jpg']", timeout=30000)
    SEL = "#editor img[data-path*='__cap_test.jpg']"
    CAP = """s => { const i = document.querySelector(s); let b = i; while (b && !(b.nextElementSibling && b.nextElementSibling.classList.contains('img-caption')) && b.parentElement.id !== 'editor') b = b.parentElement;
      const c = b && b.nextElementSibling; return c && c.classList.contains('img-caption') ? c.textContent : null; }"""
    ok("the box was still empty at Insert", before_cap == "", before_cap)
    pg.wait_for_function("s => { const i = document.querySelector(s); let b = i; for (let k = 0; b && k < 6; k++, b = b.parentElement) { const n = b.nextElementSibling; if (n && n.classList.contains('img-caption') && n.textContent.startsWith('Auto caption')) return true; } return false; }", arg=SEL, timeout=20000)
    ok("a late answer goes under the placed photo", True)

    # ---- QA 2026-11-10: the timings a real bake has (5-17 s) and inserting several photos in a row. Every answer and
    # every import is played in the page (numbered), and the other POSTs an insert makes are answered here too.
    pg.evaluate("""() => { const f = window.__origFetch; window.__cfg = { capMs: 0, altMs: 0, impMs: 0 }; let n = 0, a = 0, k = 0;
      window.fetch = (u, o) => { const s = String(u);
        if (s.includes('/api/caption')) { const id = ++n; return new Promise(r => setTimeout(r, window.__cfg.capMs)).then(() => new Response(JSON.stringify({ ok: true, caption: 'Timed caption ' + id }), { headers: { 'Content-Type': 'application/json' } })); }
        if (s.includes('/api/alt_text')) { const id = ++a; return new Promise(r => setTimeout(r, window.__cfg.altMs)).then(() => new Response(JSON.stringify({ ok: true, alt: 'Timed alt ' + id }))); }
        if (/[/]api[/]import([?]|$)/.test(s)) { const id = ++k; return new Promise(r => setTimeout(r, window.__cfg.impMs)).then(() => new Response(JSON.stringify({ ok: true, src: 'Images/North Macedonia/Ohrid/__cap_t' + id + '.jpg' }))); }
        if (o && o.method && o.method !== 'GET') return Promise.resolve(new Response(JSON.stringify({ ok: true })));
        return f(u, o); }; }""")
    cfg = lambda **k: pg.evaluate("k => Object.assign(window.__cfg, k)", k)
    UNDER = """n => { const i = document.querySelector(`#editor img[data-path*="__cap_t${n}.jpg"]`); if (!i) return 'NO PHOTO';
      let b = i; for (let k = 0; b && k < 6; k++, b = b.parentElement) { const x = b.nextElementSibling; if (x && x.classList.contains('img-caption')) return x.textContent; if (b.parentElement && b.parentElement.id === 'editor') break; } return null; }"""
    ALT = "n => { const i = document.querySelector(`#editor img[data-path*=\"__cap_t${n}.jpg\"]`); return i ? i.getAttribute('alt') : 'NO PHOTO'; }"

    def open2(ph, js_drop=END):
        open_on(ph, js_drop)
        pg.wait_for_timeout(700)
        pg.evaluate("() => { const c = document.getElementById('crop-country'); if (!c.value) c.value = 'North Macedonia'; }")

    # 7. the answer lands AFTER Insert but BEFORE the photo is placed (the bake is slower than the caption)
    cfg(capMs=2500, altMs=2500, impMs=6000)
    open2(PH_OF(4))
    at_insert = pg.evaluate("[document.getElementById('crop-caption').value, document.getElementById('crop-alt').value]")
    pg.click("#crop-go")
    pg.wait_for_timeout(8500)
    u1 = pg.evaluate(UNDER, 1)
    ok("7 a caption that lands between Insert and placement goes under the photo (QA C1)", at_insert[0] == "" and (u1 or "").startswith("Timed caption"), (at_insert, u1))
    ok("7b ...and so does the alt text that lands in that window (the alt path shares the pattern)", (pg.evaluate(ALT, 1) or "").startswith("Timed alt"), pg.evaluate(ALT, 1))
    pg.evaluate("() => { const e = document.getElementById('editor'); e.focus(); }")
    pg.keyboard.press("Control+z"); pg.wait_for_timeout(500)
    gone = pg.evaluate("""() => [!!document.querySelector('#editor img[data-path*="__cap_t1.jpg"]'), [...document.querySelectorAll('#editor .img-caption')].some(c => c.textContent.startsWith('Timed caption 1'))]""")
    ok("7c ...in the same undo step as the photo: one Ctrl+Z takes both away", gone == [False, False], gone)
    pg.wait_for_timeout(300)

    # 8. two photos in a row: Insert A before its answers, open B at once; A still gets its own caption and alt
    cfg(capMs=4000, altMs=4000, impMs=0)
    open2(PH_OF(0))
    pg.click("#crop-go")
    pg.wait_for_timeout(300)
    open2(PH_OF(1))
    pg.wait_for_timeout(6000)
    u2, b_box = pg.evaluate(UNDER, 2), pg.evaluate("document.getElementById('crop-caption').value")
    ok("8 photo A gets its caption when B's dialog was opened meanwhile (QA C2), and B's box gets B's own", (u2 or "").startswith("Timed caption") and b_box.startswith("Timed caption") and b_box != u2, (u2, b_box))
    ok("8b ...A's alt text too (it used to look for window.__altSeed, which B had replaced)", (pg.evaluate(ALT, 2) or "").startswith("Timed alt"), pg.evaluate(ALT, 2))
    pg.evaluate("closeCropModal()"); pg.wait_for_timeout(300)

    # 9. the same with a slow bake: A's answer lands while B is open and A is still baking
    cfg(capMs=2000, altMs=2000, impMs=5000)
    open2(PH_OF(2))
    pg.click("#crop-go")
    pg.wait_for_timeout(300)
    open2(PH_OF(3))
    pg.wait_for_timeout(7000)
    u3 = pg.evaluate(UNDER, 3)
    ok("9 A's answer that lands while B is open and A is still baking goes under A, not into B's box", (u3 or "").startswith("Timed caption") and pg.evaluate("document.getElementById('crop-caption').value") != u3, (u3, pg.evaluate("document.getElementById('crop-caption').value")))
    pg.evaluate("closeCropModal()"); pg.wait_for_timeout(300)

    # 10. the placeholder after two quick opens, then an Edit (no request)
    cfg(capMs=2500, altMs=0, impMs=0)
    open2(PH_OF(4)); pg.evaluate("closeCropModal()"); pg.wait_for_timeout(100)
    open2(PH_OF(5)); pg.wait_for_timeout(4000); pg.evaluate("closeCropModal()"); pg.wait_for_timeout(300)
    open_on(PH_OF(5), "(() => ({ where: 'image', edit: true, el: document.querySelector('#editor img') }))()")
    pg.wait_for_timeout(1200)
    ok("10 the box's placeholder is its own again after two quick opens and an Edit (QA #9)", pg.evaluate("document.getElementById('crop-caption').placeholder") == "Shown under the photo", pg.evaluate("document.getElementById('crop-caption').placeholder"))
    pg.evaluate("closeCropModal()"); pg.wait_for_timeout(300)

    # 11. a box he empties on purpose is never refilled, in the dialog or under the photo
    cfg(capMs=2500, altMs=0, impMs=0)
    open2(PH_OF(0))
    pg.fill("#crop-caption", "x"); pg.fill("#crop-caption", "")
    pg.wait_for_timeout(3500)
    ok("11 a box he cleared himself stays empty when the answer lands (QA #10)", pg.evaluate("document.getElementById('crop-caption').value") == "", pg.evaluate("document.getElementById('crop-caption').value"))
    pg.evaluate("closeCropModal()"); pg.wait_for_timeout(300)
    TIMED = "[...document.querySelectorAll('#editor img')].map(i => (i.getAttribute('data-path') || '').match(/__cap_t([0-9]+)[.]jpg/)).filter(Boolean).map(m => +m[1])"
    n_imp = len(pg.evaluate(TIMED))
    cfg(capMs=3000, impMs=0)
    open2(PH_OF(1))
    pg.fill("#crop-caption", "x"); pg.fill("#crop-caption", "")
    pg.click("#crop-go")
    pg.wait_for_timeout(5000)
    timed = pg.evaluate(TIMED); last = max(timed) if timed else None
    ok("11b ...and Insert with a box he cleared puts no late caption under the photo", len(timed) == n_imp + 1 and last is not None and pg.evaluate(UNDER, last) is None, (last, timed, pg.evaluate(UNDER, last) if last else None))
    ok("no script errors", not errs, errs[:3])
    b.close()

bad = 0
for n, c, i in checks:
    print("  %s %s %s" % ("PASS" if c else "FAIL", n, "" if c else str(i)[:300]))
    bad += not c
print("  %d checks, %d failed" % (len(checks), bad))
sys.exit(1 if bad else 0)
