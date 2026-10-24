"""Kevin's 2026-09-24 editor list, items 2-8. Server must be on 127.0.0.1:5003.
Run: PYTHONIOENCODING=utf-8 python tools/editor_qa/qa_sidebar_0924.py"""
import asyncio, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from playwright.async_api import async_playwright
from qa_common import *

STUB = json.dumps({"ok": True, "src": "Images/Kosovo/Prishtina/IMG_5404-7-qa.jpg",
                   "w": 3024, "h": 4032, "orient": "portrait"})
PHOTO = "{ root: 4, path: 'Kosovo/Prishtina/IMG_5404-7.jpg', name: 'IMG_5404-7.jpg' }"

# a field-notes shape: a bullet list inside a section, a paragraph, an image
BODY = ('<div class="fn-section" id="sec"><h2 class="article-h2" id="h">Prishtina</h2>'
        '<ul id="ul"><li id="li1">First bullet, long enough to hold a caret.</li>'
        '<li id="li2">Second bullet, also long enough for a caret here.</li>'
        '<li id="li3">Third bullet at the end of the list.</li></ul>'
        '<p id="p1">A paragraph after the list.</p></div>'
        '<p id="p2">Trailing paragraph outside the section.</p>')

PASS = FAIL = 0
def check(cond, label, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print("  PASS ", label)
    else:    FAIL += 1; print("  FAIL ", label, "|", detail)


async def main():
    global FAIL
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)

        # ---------------------------------------------------------- 2: placeholder at the caret
        print("\n== 2. placeholder lands at the cursor, not the bottom of the section ==")
        await inject(pg, "kosovo/field-notes.html", BODY)
        await caret(pg, "#li2", off=5)
        await pg.evaluate("() => { saveSelection(); insertPhotoSlot('portrait'); }")
        await pg.wait_for_timeout(150)
        r = await pg.evaluate("""() => {
          const pair = document.querySelector('#editor .img-pair');
          const prev = pair.previousElementSibling, next = pair.nextElementSibling;
          const txt = n => n ? (n.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 30) : null;
          return { prevTag: prev && prev.tagName, prevLast: prev && prev.lastElementChild && prev.lastElementChild.id,
                   nextTag: next && next.tagName, nextFirst: next && next.firstElementChild && next.firstElementChild.id,
                   inSection: !!pair.closest('#sec'), prevTxt: txt(prev) };
        }""")
        check(r["inSection"], "stays inside the section", r)
        check(r["prevTag"] == "UL" and r["prevLast"] == "li2",
              "list splits right after the caret's bullet", r)
        check(r["nextTag"] == "UL" and r["nextFirst"] == "li3",
              "the rest of the bullets continue straight after it, no blank gap", r)

        # ---------------------------------------------------------- 6: clear a photo
        print("\n== 6. Clear leaves the placeholder, keeps the frame ==")
        await inject(pg, "kosovo/field-notes.html",
                     '<div class="img-pair"><span style="display:block;position:relative;overflow:hidden;'
                     'width:100%;aspect-ratio:2/3"><img id="im" src="' + API + '/site/Images/web/flags/xk.png" '
                     'alt="x" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover"></span></div>')
        await pg.evaluate("() => { const i = document.getElementById('im'); i.click(); window._selImg = i; }")
        await pg.evaluate("() => imgClear()")
        await pg.wait_for_timeout(100)
        r = await pg.evaluate("""() => {
          const ed = document.getElementById('editor');
          const slot = ed.querySelector('.img-slot-empty');
          return { imgGone: !ed.querySelector('img'), slot: !!slot, ph: slot && slot.dataset.ph,
                   inFrame: !!(slot && /aspect-ratio/.test(slot.parentElement.getAttribute('style') || '')),
                   pairKept: !!ed.querySelector('.img-pair') };
        }""")
        check(r["imgGone"] and r["slot"], "photo removed, placeholder in its place", r)
        check(r["inFrame"] and r["pairKept"], "the frame and the pair survive", r)
        check(r["ph"] == "1", "it will persist as a [PHOTO] marker on save", r)
        saved = await pg.evaluate("() => { const d = document.createElement('div'); d.innerHTML = document.getElementById('editor').innerHTML; stripEmptySlots(d); return d.innerHTML; }")
        check("photo-ph" in saved, "saves back as a [PHOTO] marker", saved[:120])

        # ---------------------------------------------------------- 5: country/city prefill
        print("\n== 5. the modal prefills country + city from the article ==")
        yer = pathlib.Path("Drafts/.Full Articles/armenia/yerevan.html").read_text(encoding="utf-8")
        await load_real(pg, "yerevan.html", yer)
        ctxinfo = await pg.evaluate("async () => await articleContext()")
        check(ctxinfo["country"] == "Armenia", "country from the article path", ctxinfo)
        check(ctxinfo["kind"] == "city" and ctxinfo["city"] == "Yerevan",
              "a city page fills the city too", ctxinfo)
        itin = pathlib.Path("Drafts/.Full Articles/armenia/armenia-itinerary.html").read_text(encoding="utf-8")
        await load_real(pg, "armenia-itinerary.html", itin)
        ctx2 = await pg.evaluate("async () => await articleContext()")
        check(ctx2["country"] == "Armenia" and ctx2["kind"] == "itinerary" and not ctx2["city"],
              "an itinerary fills only the country (city comes from the photo)", ctx2)

        # ---------------------------------------------------------- 7: straighten reaches the server
        print("\n== 7. a straighten is carried into the import ==")
        await inject(pg, "kosovo/field-notes.html",
                     '<p id="a">Lead.</p><div class="img-pair" style="grid-template-columns:1fr">'
                     '<span class="img-slot-empty" data-slot="1" data-ph="1" data-slot-id="s1"></span></div>')
        await pg.evaluate("() => restoreSlotButtons()")
        await pg.evaluate(PATCH, {"pat": "/api/import", "mode": "body", "delay": 0, "status": 200, "body": STUB})
        await pg.evaluate("() => { const s = document.querySelector('.img-slot-empty'); fillSlot(s);"
                          " const t = takePendingSlot(); openCropForPhoto(%s, { el: t, where: 'slot' }); }" % PHOTO)
        await pg.wait_for_selector("#crop-backdrop.open", timeout=40000)
        await pg.wait_for_timeout(900)
        await pg.evaluate("() => { const s = document.getElementById('crop-angle'); s.value = 5;"
                          " s.dispatchEvent(new Event('input', { bubbles: true })); }")
        await pg.wait_for_timeout(200)
        lbl = await pg.evaluate("() => document.getElementById('crop-go').textContent")
        check("5" in lbl and "°" in lbl, "the Insert button names the tilt before you click", lbl)
        await pg.evaluate("() => { document.getElementById('crop-country').value = 'Kosovo'; }")
        await pg.click("#crop-go")
        await pg.wait_for_timeout(1500)
        hits = await pg.evaluate("() => (window.__hits || []).filter(h => /api.import/.test(h.url)).map(h => JSON.parse(h.body))")
        check(hits and abs(hits[0].get("angle", 0) - 5) < .01,
              "the import request carries angle=5", hits[:1])
        check(await pg.evaluate("() => !!document.querySelector('#editor .img-pair img')"),
              "the photo lands in the placeholder's frame")

        # ...and the same slider must NOT look usable where it cannot run.
        # Straighten is baked at import; editing a photo already in the article
        # only rewrites CSS, so the control used to move and do nothing.
        await inject(pg, "kosovo/field-notes.html",
                     '<div class="img-landscape"><img id="im2" src="' + API +
                     '/site/Images/web/Kosovo/Prishtina/IMG_5404-7.jpg" alt="x" '
                     'style="width:100%;height:100%;object-fit:cover"></div>')
        await pg.evaluate("() => { const i = document.getElementById('im2'); i.click(); window._selImg = i; }")
        await pg.evaluate("() => openCropModal()")
        await pg.wait_for_selector("#crop-backdrop.open", timeout=40000)
        await pg.wait_for_timeout(1000)
        r = await pg.evaluate("""() => ({ disabled: document.getElementById('crop-angle').disabled,
                                        why: document.getElementById('crop-angle').title,
                                        go: document.getElementById('crop-go').textContent })""")
        check(r["disabled"], "editing an existing photo disables straighten instead of ignoring it", r)
        check("Edit" in (r["why"] or ""), "and says where the working one is", r)
        await pg.evaluate("() => { const s = document.getElementById('crop-angle'); s.value = 7; s.dispatchEvent(new Event('input', {bubbles:true})); }")
        await pg.wait_for_timeout(200)
        go = await pg.evaluate("() => document.getElementById('crop-go').textContent")
        check("°" not in go, "the Apply button never promises a tilt it cannot bake", go)
        await pg.evaluate("() => closeCropModal()")

        # ---------------------------------------------------------- 8: click inserts at the caret
        print("\n== 8. a clicked photo lands at the cursor, not the end ==")
        await pg.evaluate("() => { window.__hits = []; }")
        await inject(pg, "kosovo/field-notes.html", BODY)
        await caret(pg, "#p1", off=4)
        await pg.evaluate("() => { saveSelection(); const s = takePendingSlot();"
                          " const t = !s ? dropAtCaret() : { el: s, where: 'slot' };"
                          " openCropForPhoto(%s, t); }" % PHOTO)
        await pg.wait_for_selector("#crop-backdrop.open", timeout=40000)
        await pg.wait_for_timeout(700)
        await pg.evaluate("() => { document.getElementById('crop-country').value = 'Kosovo'; }")
        await pg.click("#crop-go")
        await pg.wait_for_timeout(1500)
        r = await pg.evaluate("""() => {
          const img = document.querySelector('#editor img');
          const blk = img.closest('#sec > *') || img.closest('#editor > *');
          return { prevId: blk.previousElementSibling && blk.previousElementSibling.id,
                   inSection: !!img.closest('#sec'),
                   isLast: blk === document.getElementById('editor').lastElementChild };
        }""")
        check(r["inSection"], "landed inside the section the caret was in", r)
        check(r["prevId"] == "p1", "sits right after the caret's paragraph", r)
        check(not r["isLast"], "not dumped at the end of the article", r)

        # ---------------------------------------------------------- 3 + 4: countries
        print("\n== 3 + 4. sidebar browses trips and defaults to the article's country ==")
        await load_real(pg, "yerevan.html", yer)
        await pg.evaluate("() => { if (!document.body.classList.contains('photos-open')) togglePhotoPanel(); }")
        # Blog Picks takes over the sidebar when it is open (a deliberate
        # :has() rule hides the whole folder browser behind it), and filling a
        # slot earlier in this suite opened it. Close it, or we would be
        # asserting against a panel that is covered on purpose.
        await pg.evaluate("() => { if (window.pkHide) pkHide(); }")
        await pg.wait_for_timeout(300)
        await pg.evaluate("() => plBoot()")
        await pg.wait_for_function("() => document.querySelectorAll('#pl-country option').length > 0", timeout=20000)
        await pg.wait_for_timeout(1200)
        r = await pg.evaluate("""() => ({
          n: document.querySelectorAll('#pl-country option').length,
          chosen: document.getElementById('pl-country').value,
          meta: [...document.querySelectorAll('#pl-country option')].some(o => o.value.startsWith('_')),
          barShown: getComputedStyle(document.getElementById('pl-countrybar')).display !== 'none',
          tabsHidden: getComputedStyle(document.getElementById('pl-tabs')).display === 'none',
          path: plDebug().path, root: plDebug().root, backup: plDebug().backupRoot })""")
        check(r["n"] >= 30, "every trip album is listed, like the Photo Library", r["n"])
        check(not r["meta"], "the _meta folder is not offered as a trip")
        check(r["chosen"].startswith("Armenia"), "defaulted to the open article's country", r)
        check(r["path"] == r["chosen"] and r["root"] == r["backup"], "browsing that album", r)
        check(r["barShown"] and r["tabsHidden"], "trips replace the folder tabs", r)
        await pg.wait_for_function("() => document.querySelectorAll('#pl-grid .pl-tile, #pl-grid > div').length > 0", timeout=25000)
        n = await pg.evaluate("() => document.querySelectorAll('#pl-grid > div').length")
        check(n > 0, "the album's photos are browsable", n)
        await pg.evaluate("() => plToggleMode()")
        await pg.wait_for_timeout(400)
        r2 = await pg.evaluate("""() => ({ bar: getComputedStyle(document.getElementById('pl-countrybar')).display,
                                           tabs: getComputedStyle(document.getElementById('pl-tabs')).display,
                                           btn: document.getElementById('pl-modebtn').textContent })""")
        check(r2["bar"] == "none" and r2["tabs"] != "none", "Folders still reachable for one-off photos", r2)
        await pg.evaluate("() => plToggleMode()")

        report_errors(ctx, "sidebar 0924")
        print(f"\n{PASS} passed, {FAIL} failed")
        await b.close()
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
