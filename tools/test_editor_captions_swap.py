"""The photo toolbar's caption buttons and the drag of a paired photo between frames (editor.html, 2026-11-06;
Kevin: "add ability to fill out captions on your own ... switch vertical photos between two different placeholders
... using the drag interface ... add an add caption button so I don't have to open the editor interface").

  1. Add caption on a selected photo puts the site's exact caption div right after the block (after the WHOLE
     pair, never between its halves), with the cursor in it; typing lands there; the save keeps the markup.
     On a block that already has a caption it moves the cursor there instead. An empty caption is dropped by the save.
  2. Write caption (/api/caption, mocked here) sends both halves of a pair, the heading, the paragraph before,
     the alt text and the page's captions as examples, and puts the answer in the caption, cursor at the end.
     A missing route (old server) and a Gemini error each give a clear message and change nothing.
  3. The ⠿ Move drag of a paired photo (pressing the photo itself is the crop pan, so the Move handle is the drag):
     over another pair's cell or an empty slot that frame is the target; dropped on a photo the two <picture>s swap,
     each keeping its own object-position crop and tiers; dropped on an empty slot it moves there and leaves a
     [PHOTO] slot behind; anywhere else it is the old block move. Click-to-place mode does the same. Ctrl+Z undoes a
     swap. The save writes the moved pictures byte-for-byte as they were, just in their new frames.
  4. The sidebar's drag-to-insert (application/x-photo) still shows its drop line and opens the crop dialog.

Headless, nothing on disk is written: the fixture is the Ohrid draft read into memory (canonical/og:url masked so no
tool can mistake it for the real page), the stub file handle keeps the save in window.__disk, and every photo-server
route that could touch a file (/api/pipeline/queue, /api/caption) is intercepted. Photos are shown from :5003.

    python tools/test_editor_captions_swap.py          (needs the photo server on :5003 for the thumbnails)
"""
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
EDITOR = (ROOT / "editor.html").as_uri() + "#editor"
SHOTS = ROOT / ".tmp" / "previews"
SHOTS.mkdir(parents=True, exist_ok=True)

CAP_STYLE = ("width:79%;margin:-1rem auto 1.75rem;text-align:center;font-family:Hanken Grotesk,sans-serif;"
             "font-size:.85rem;font-style:italic;color:rgba(28,40,33,.55)")
CAP_RE = re.compile(r'<div class="img-caption" style="' + re.escape(CAP_STYLE) + r'">([^<]*)</div>')
SLOT_CSS = "display:block;position:relative;overflow:hidden;width:100%;aspect-ratio:2/3;border-radius:2px"
PH_STYLE = ("width:100%;height:100%;background:#F2F0E9;border:1px dashed #C9C3B4;border-radius:3px;display:flex;"
            "align-items:center;justify-content:center;color:#8C8676;font-family:'Hanken Grotesk',Helvetica,Arial,sans-serif;"
            "font-size:11px;font-weight:600;letter-spacing:.16em")


def fixture():
    """The Ohrid draft with: no page identity, no caption on pair 1 and landscape 1, and pair 2's right half an
    empty [PHOTO] marker (the form adoptPlaceholders turns into a slot)."""
    src = ROOT / "Drafts" / "Upcoming" / "north-macedonia" / "ohrid.html"
    html = src.read_text(encoding="utf-8")
    html = html.replace("https://getawayguide.io/north-macedonia/ohrid.html", "https://example.invalid/zz-test/ohrid-test.html")
    pairs = [m for m in re.finditer(r'<div class="img-pair">[\s\S]*?</div>', html)]
    assert len(pairs) >= 4, len(pairs)
    # pair 2: the second <span ...2/3...>...</span> becomes a marker cell
    p2 = pairs[1]
    cells = list(re.finditer(r'<span style="[^"]*aspect-ratio:2/3[^"]*">[\s\S]*?</span>', p2.group(0)))
    assert len(cells) == 2
    c2 = cells[1]
    marker = '<span style="%s"><span class="photo-ph" style="%s">[PHOTO]</span></span>' % (SLOT_CSS, PH_STYLE)
    new_p2 = p2.group(0)[:c2.start()] + marker + p2.group(0)[c2.end():]
    html = html[:p2.start()] + new_p2 + html[p2.end():]
    # the captions after pair 1 and the first landscape go
    m1 = re.search(r'<div class="img-pair">[\s\S]*?</div>\s*', html)
    html = html[:m1.end()] + CAP_RE.sub("", html[m1.end():], count=1)
    ml = re.search(r'<div class="img-landscape">[\s\S]*?</div>\s*', html)
    html = html[:ml.end()] + CAP_RE.sub("", html[ml.end():], count=1)
    return html


FIX = fixture()

OPEN = """async ([text, name]) => { folderHandle = null; window.__disk = text; window.__writes = 0;
  await window.loadFile({ kind: 'file', name,
    getFile: async () => new File([window.__disk], name, { type: 'text/html' }),
    createWritable: async () => ({ write: async h => { window.__disk = h; window.__writes++; }, close: async () => {} }) }); }"""

checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))


def body_of(html):
    m = re.search(r'<div class="article-body"[^>]*>', html)
    return html[m.end():] if m else html


def pairs_of(html):
    return [m.group(0) for m in re.finditer(r'<div class="img-pair"[^>]*>[\s\S]*?</div>', body_of(html))]


def pictures(block):
    return re.findall(r'<picture>[\s\S]*?</picture>', block)


def stem(ref):
    m = re.search(r'/([^/]+?)(?:-(?:mob-)?[123]x)?\.(?:jpg|webp)', ref or "")
    return m.group(1) if m else ref


IMG_INFO = """([pi, ci]) => { const pair = document.querySelectorAll('#editor .img-pair')[pi];
  const cells = [...pair.children].filter(c => c.tagName === 'SPAN');
  const cell = cells[ci]; if (!cell) return null; const img = cell.querySelector('img');
  return { slot: cell.classList.contains('img-slot-empty') || !!cell.querySelector('.img-slot-empty'), ph: cell.dataset.ph || (cell.querySelector('.img-slot-empty') || {}).dataset?.ph || '',
    path: img ? img.getAttribute('data-path') : null, pos: img ? img.style.objectPosition : null,
    pic: img ? !!img.closest('picture') : false, sources: img && img.closest('picture') ? (img.closest('picture').dataset.sources || '').length : 0,
    cells: cells.length }; }"""

SEL_IN = """sel => { const el = document.querySelector(sel); const s = getSelection();
  return !!(el && s.rangeCount && el.contains(s.anchorNode)); }"""

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    pg = ctx.new_page()
    errs, dialogs, caption_calls = [], [], []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    # nothing of this run may reach the real pipeline; the caption route is played by the test
    pg.route("**/api/pipeline/queue", lambda r: r.fulfill(status=200, content_type="application/json", body='{"ok":true,"queued":false}',
                                                          headers={"Access-Control-Allow-Origin": "*"}))
    MOCK = {"mode": "ok"}

    def caption_route(route):
        caption_calls.append(route.request.post_data_json)
        h = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type"}
        if route.request.method == "OPTIONS":
            return route.fulfill(status=204, headers=h)
        if MOCK["mode"] == "missing":
            return route.fulfill(status=404, content_type="text/html", body="not found", headers=h)
        if MOCK["mode"] == "error":
            return route.fulfill(status=502, content_type="application/json", body='{"ok":false,"error":"Gemini could not answer: quota used up"}', headers=h)
        return route.fulfill(status=200, content_type="application/json", body='{"ok":true,"caption":"Mocked houses on the lake, and a boat by the quay"}', headers=h)

    pg.route("**/api/caption", caption_route)

    pg.goto(EDITOR)
    pg.wait_for_timeout(1200)
    ok("the editor loads without a script error", not errs, errs[:2])
    ok("the toolbar has Add caption and Write caption", pg.evaluate("!!document.getElementById('img-cap-add') && !!document.getElementById('img-cap-write')"))

    pg.evaluate(OPEN, [FIX, "ohrid-test.html"])
    pg.wait_for_timeout(6000)          # the post-open walk resets selections for a few seconds on a page this size
    n_pairs = pg.evaluate("document.querySelectorAll('#editor .img-pair').length")
    ok("fixture: the pairs are there and pair 2's right half is an empty slot", n_pairs >= 4 and pg.evaluate(IMG_INFO, [1, 1])["slot"], pg.evaluate(IMG_INFO, [1, 1]))
    ok("fixture: pair 1 has no caption", pg.evaluate("!document.querySelectorAll('#editor .img-pair')[0].nextElementSibling.classList.contains('img-caption')"))
    cap0 = pg.evaluate("document.querySelectorAll('#editor .img-caption').length")
    cap0_re = len(CAP_RE.findall(body_of(FIX)))          # the plain-text captions the file-side regex sees

    # ---------------- 1. Add caption ----------------
    pair1_img = pg.locator("#editor .img-pair").nth(0).locator("img").nth(0)
    pair1_img.scroll_into_view_if_needed()
    pair1_img.click()
    pg.wait_for_timeout(300)
    tb = pg.evaluate("getComputedStyle(document.getElementById('img-floating-toolbar')).display")
    ok("clicking a paired photo shows the toolbar with the two caption buttons", tb == "flex" and pg.evaluate(
        "['img-cap-add','img-cap-write'].every(id => document.getElementById(id).offsetWidth > 0)"), tb)
    pg.screenshot(path=str(SHOTS / "editor-caption-buttons-1440.png"))
    pg.click("#img-cap-add")
    pg.wait_for_timeout(200)
    shape = pg.evaluate("""() => { const pair = document.querySelectorAll('#editor .img-pair')[0]; const cap = pair.nextElementSibling;
      const s = getSelection(); return { isCap: cap && cap.classList.contains('img-caption'), style: cap && cap.getAttribute('style'),
        inner: cap && cap.innerHTML, caretIn: !!(cap && s.rangeCount && cap.contains(s.anchorNode)),
        betweenHalves: !!pair.querySelector(':scope > .img-caption'), tb: getComputedStyle(document.getElementById('img-floating-toolbar')).display }; }""")
    ok("Add caption puts the site's caption div right after the whole pair, never between the halves",
       shape["isCap"] and shape["style"] == CAP_STYLE and not shape["betweenHalves"], shape)
    ok("the cursor is in the new caption and the photo toolbar is gone", shape["caretIn"] and shape["tb"] == "none", shape)
    pg.keyboard.type("Typed by hand under the first pair")
    pg.wait_for_timeout(300)
    typed = pg.evaluate("document.querySelectorAll('#editor .img-pair')[0].nextElementSibling.textContent")
    ok("typing lands in the caption", typed == "Typed by hand under the first pair", typed)
    ok("adding the caption marks the article unsaved", pg.evaluate("isDirty"))

    # a block that already has a caption: the cursor goes to it, no second div
    pg.locator("#editor .img-pair").nth(0).locator("img").nth(1).click()
    pg.wait_for_timeout(200)
    pg.click("#img-cap-add")
    pg.wait_for_timeout(200)
    again = pg.evaluate("""() => { const pair = document.querySelectorAll('#editor .img-pair')[0]; const cap = pair.nextElementSibling;
      const s = getSelection(); return { n: document.querySelectorAll('#editor .img-caption').length, caretIn: !!(s.rangeCount && cap.contains(s.anchorNode)),
        atEnd: s.isCollapsed && (() => { const r = document.createRange(); r.setStart(cap, 0); r.setEnd(s.anchorNode, s.anchorOffset); return r.toString() === cap.textContent; })(),
        second: cap.nextElementSibling && cap.nextElementSibling.classList.contains('img-caption') }; }""")
    ok("Add caption on a captioned block moves the cursor to the end of that caption, adds nothing", again["caretIn"] and again["atEnd"] and not again["second"] and again["n"] == cap0 + 1, again)

    # an empty caption left behind: the first landscape gets one and nothing is typed
    pg.locator("#editor .img-landscape").nth(0).locator("img").click()
    pg.wait_for_timeout(200)
    pg.click("#img-cap-add")
    pg.wait_for_timeout(200)
    ok("an empty caption stands in the editor while he could still type", pg.evaluate(
        "(() => { const c = document.querySelectorAll('#editor .img-landscape')[0].nextElementSibling; return c.classList.contains('img-caption') && c.innerHTML === '<br>'; })()"))

    # ---------------- 2. Write caption (mocked) ----------------
    # pair 4 (index 3) has a caption already; the mock's answer replaces it, and the request carries the context
    pg.locator("#editor .img-pair").nth(3).locator("img").nth(0).scroll_into_view_if_needed()
    pg.locator("#editor .img-pair").nth(3).locator("img").nth(0).click()
    pg.wait_for_timeout(200)
    before4 = pg.evaluate("document.querySelectorAll('#editor .img-pair')[3].nextElementSibling.textContent")
    pg.click("#img-cap-write")
    pg.wait_for_timeout(1500)
    req = [c for c in caption_calls if c][-1] if [c for c in caption_calls if c] else {}
    w = pg.evaluate("""() => { const pair = document.querySelectorAll('#editor .img-pair')[3]; const cap = pair.nextElementSibling; const s = getSelection();
      return { text: cap.textContent, caretIn: !!(s.rangeCount && cap.contains(s.anchorNode)),
        atEnd: s.isCollapsed && (() => { const r = document.createRange(); r.setStart(cap, 0); r.setEnd(s.anchorNode, s.anchorOffset); return r.toString() === cap.textContent; })(),
        btn: document.getElementById('img-cap-write').textContent, n: document.querySelectorAll('#editor .img-caption').length }; }""")
    ok("Write caption asks the route with both halves, pair flag, heading, paragraph, alt text, examples and the current caption",
       len(req.get("images", [])) == 2 and all("Images/" in r for r in req["images"]) and req.get("pair") is True and req.get("heading")
       and req.get("paragraph") and len(req.get("alts", [])) == 2 and len(req.get("examples", [])) >= 3 and req.get("current") == before4
       and before4 not in req.get("examples", []) and req.get("country") == "Zz Test", {k: (v if k != "paragraph" else v[:40]) for k, v in req.items()})
    ok("the answer replaces the caption, cursor at its end, button back to normal",
       w["text"] == "Mocked houses on the lake, and a boat by the quay" and w["caretIn"] and w["atEnd"] and w["btn"].strip() == "✦ Write caption" and w["n"] == cap0 + 2, w)
    ok("nothing but the photos and the page context left the page", set(req.keys()) <= {"images", "pair", "count", "heading", "paragraph", "alts", "examples", "current", "country", "city"}, sorted(req.keys()))
    # the old server (no route yet) and a Gemini failure: a message, no change
    for mode, needle in (("missing", "restarted"), ("error", "quota used up")):
        MOCK["mode"] = mode
        pg.locator("#editor .img-pair").nth(3).locator("img").nth(1).click()
        pg.wait_for_timeout(200)
        pg.click("#img-cap-write")
        pg.wait_for_timeout(1200)
        msg = pg.evaluate("[...document.querbody ? [] : document.querySelectorAll('body > div')].map(d => d.textContent).join(' | ')") if False else \
            pg.evaluate("[...document.querySelectorAll('body > *')].filter(d => d.id !== 'editor-wrap' && /caption/i.test(d.textContent) && d.getBoundingClientRect().height < 200).map(d => d.textContent.trim()).join(' | ')")
        txt = pg.evaluate("document.querySelectorAll('#editor .img-pair')[3].nextElementSibling.textContent")
        ok("Write caption with %s: a clear message, the caption untouched" % ("no route on the server" if mode == "missing" else "a Gemini error"),
           needle in msg and txt == "Mocked houses on the lake, and a boat by the quay", (msg[-160:], txt))
    MOCK["mode"] = "ok"

    # ---------------- save 1: captions ----------------
    pg.evaluate("saveFile()")
    pg.wait_for_timeout(1800)
    out1 = pg.evaluate("window.__disk")
    ok("the save was written", pg.evaluate("window.__writes") == 1 and out1 and out1 != FIX)
    body1 = body_of(out1)
    caps1 = CAP_RE.findall(body1)
    ok("the typed caption is saved in the site's exact markup, right after the pair's closing tag",
       re.search(r'</span>\s*</div>\s*<div class="img-caption" style="' + re.escape(CAP_STYLE) + r'">Typed by hand under the first pair</div>', body1)
       and "Typed by hand under the first pair" in caps1)
    ok("the written caption is saved too", "Mocked houses on the lake, and a boat by the quay" in caps1)
    ok("the empty caption is not saved, and no caption keeps a <br>", not re.search(r'<div class="img-caption"[^>]*>\s*(?:<br>)?\s*</div>', body1)
       and not re.search(r'<div class="img-caption"[^>]*>(?:[^<]*<br>)', body1) and len(caps1) == cap0_re + 1, (len(caps1), cap0_re))
    ok("the empty slot is saved as a [PHOTO] marker in its cell", re.search(r'<span style="' + re.escape(SLOT_CSS) + r'"><span class="photo-ph"[^>]*>\[PHOTO\]</span></span>', pairs_of(out1)[1]))

    # ---------------- 3. drag a half between pairs ----------------
    # distinct crops first, so the test sees them travel with their photos
    pg.evaluate("""() => { const P = document.querySelectorAll('#editor .img-pair');
      P[0].querySelectorAll('img')[0].style.objectPosition = '10% 20%'; P[2].querySelectorAll('img')[1].style.objectPosition = '80% 90%'; setDirty(true); }""")
    pg.evaluate("saveFile()")
    pg.wait_for_timeout(1800)
    base = pg.evaluate("window.__disk")
    a = pg.evaluate(IMG_INFO, [0, 0]); c = pg.evaluate(IMG_INFO, [2, 1])
    ok("before the drag: pair 1 left and pair 3 right are two different pictures with their own crops",
       a["path"] and c["path"] and a["path"] != c["path"] and a["pos"] == "10% 20%" and c["pos"] == "80% 90%" and a["pic"] and c["pic"] and a["sources"] > 0, (a, c))
    cap_before = pg.evaluate("[...document.querySelectorAll('#editor .img-pair')].map(p => (p.nextElementSibling.classList.contains('img-caption') ? p.nextElementSibling.textContent : ''))")

    TARGET_IDX = """(() => { const t = document.querySelector('#editor .half-target'); if (!t) return -1;
      const pair = t.closest('.img-pair, .pair'); return [...document.querySelectorAll('#editor .img-pair')].indexOf(pair); })()"""

    def handle_drag(src, dst, shot=None):
        """select `src`, press the ⠿ Move handle, drag over `dst`, release; returns (marks while dragging, target pair index)"""
        src.scroll_into_view_if_needed()
        src.click()
        pg.wait_for_timeout(250)
        hb = pg.locator("#img-move-handle").bounding_box()
        pg.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2)
        pg.mouse.down()
        pg.mouse.move(hb["x"] + hb["width"] / 2 + 40, hb["y"] + hb["height"] + 40, steps=4)
        pg.wait_for_timeout(120)
        marks = pg.evaluate("[document.querySelectorAll('#editor .half-dragging').length, document.querySelectorAll('#editor .half-can').length]")
        dst.scroll_into_view_if_needed()
        db = dst.bounding_box()
        pg.mouse.move(db["x"] + db["width"] / 2, db["y"] + db["height"] / 2, steps=10)
        pg.wait_for_timeout(250)
        tgt = pg.evaluate(TARGET_IDX)
        if shot:
            pg.evaluate("(() => { const t = document.getElementById('ed-toast'); if (t) { t.classList.remove('on'); t.style.visibility = 'hidden'; } })()")   # a message from an earlier step would mislead
            pg.screenshot(path=str(shot))
            pg.evaluate("(() => { const t = document.getElementById('ed-toast'); if (t) t.style.visibility = ''; })()")
        pg.mouse.up()
        pg.wait_for_timeout(700)
        return marks, tgt

    src = pg.locator("#editor .img-pair").nth(0).locator("img").nth(0)
    dst = pg.locator("#editor .img-pair").nth(2).locator("img").nth(1)
    marks, tgt = handle_drag(src, dst, SHOTS / "editor-half-drag-in-progress-1440.png")
    ok("while a half is on the Move drag, its frame dims, every other pair cell and the empty slot is marked as a landing place, and the hovered one is lit",
       marks[0] == 1 and marks[1] >= 2 * (n_pairs - 1) and tgt == 2, (marks, tgt))
    a2 = pg.evaluate(IMG_INFO, [0, 0]); c2 = pg.evaluate(IMG_INFO, [2, 1])
    ok("dropping on another pair's half swaps the two pictures, each with its own crop and tiers",
       a2["path"] == c["path"] and a2["pos"] == "80% 90%" and c2["path"] == a["path"] and c2["pos"] == "10% 20%" and a2["sources"] == c["sources"] and c2["sources"] == a["sources"], (a2, c2))
    ok("no drag chrome is left behind", pg.evaluate("document.querySelectorAll('#editor .half-can, #editor .half-target, #editor .half-dragging').length") == 0
       and pg.evaluate("document.querySelectorAll('#editor .img-pair')[0].style.opacity") == "")
    ok("captions stay with their blocks", pg.evaluate("[...document.querySelectorAll('#editor .img-pair')].map(p => (p.nextElementSibling.classList.contains('img-caption') ? p.nextElementSibling.textContent : ''))") == cap_before)

    # Ctrl+Z puts them back
    pg.focus("#editor")
    pg.keyboard.press("Control+z")
    pg.wait_for_timeout(400)
    a3 = pg.evaluate(IMG_INFO, [0, 0]); c3 = pg.evaluate(IMG_INFO, [2, 1])
    ok("Ctrl+Z after the swap puts both pictures back", a3["path"] == a["path"] and a3["pos"] == "10% 20%" and c3["path"] == c["path"] and c3["pos"] == "80% 90%", (a3, c3))
    # and again, for the save
    handle_drag(pg.locator("#editor .img-pair").nth(0).locator("img").nth(0), pg.locator("#editor .img-pair").nth(2).locator("img").nth(1))
    a4 = pg.evaluate(IMG_INFO, [0, 0])
    ok("the same drag again swaps again", a4["path"] == c["path"], a4)

    # a half dragged to a gap between blocks is still the old block move of the whole pair
    pg.locator("#editor .img-pair").nth(3).locator("img").nth(0).scroll_into_view_if_needed()
    order0 = pg.evaluate("[...document.getElementById('editor').children].indexOf(document.querySelectorAll('#editor .img-pair')[3])")
    nxt = pg.evaluate("(() => { const p = document.querySelectorAll('#editor .img-pair')[3]; let n = p.nextElementSibling; if (n && n.classList.contains('img-caption')) n = n.nextElementSibling; return n ? n.tagName : null; })()")
    pg.locator("#editor .img-pair").nth(3).locator("img").nth(0).click()
    pg.wait_for_timeout(250)
    hb = pg.locator("#img-move-handle").bounding_box()
    pg.mouse.move(hb["x"] + 5, hb["y"] + 5); pg.mouse.down(); pg.mouse.move(hb["x"] + 60, hb["y"] + 60, steps=4)
    # the next block mid-window: a pointer within 80 px of the scroller's edge is the Move tool's auto-scroll
    nb = pg.evaluate("(() => { const p = document.querySelectorAll('#editor .img-pair')[3]; let n = p.nextElementSibling; if (n && n.classList.contains('img-caption')) n = n.nextElementSibling; n.scrollIntoView({ block: 'center' }); const r = n.getBoundingClientRect(); return [r.left + 40, r.bottom - 4]; })()")
    pg.wait_for_timeout(150)
    pg.mouse.move(nb[0], nb[1], steps=10); pg.wait_for_timeout(250)
    line_shown = pg.evaluate("!!document.querySelector('#editor .pl-drop-line') && !document.querySelector('#editor .half-target')")
    pg.mouse.up(); pg.wait_for_timeout(600)
    order1 = pg.evaluate("[...document.getElementById('editor').children].indexOf(document.querySelectorAll('#editor .img-pair')[3])")
    moved_ok = pg.evaluate("(() => { const p = document.querySelectorAll('#editor .img-pair')[3]; const c = p.nextElementSibling; return c && c.classList.contains('img-caption') && c.textContent.startsWith('Mocked houses'); })()")   # pair 4's caption is the mocked one from step 2
    ok("over a gap between blocks the drop line shows and the WHOLE pair moves, caption with it, as before", nxt and line_shown and order1 > order0 and moved_ok, (nxt, line_shown, order0, order1, moved_ok))

    # into the empty slot, by click-to-place: pair 2's left photo to its own empty right half
    s2 = pg.evaluate(IMG_INFO, [1, 0])
    src = pg.locator("#editor .img-pair").nth(1).locator("img").nth(0)
    src.scroll_into_view_if_needed()
    src.click()
    pg.wait_for_timeout(250)
    pg.click("#img-move-handle")
    pg.wait_for_timeout(200)
    mode = pg.evaluate("[document.getElementById('img-move-handle').textContent, document.querySelectorAll('#editor .half-can').length, (document.getElementById('pl-flash') || {}).textContent || '']")
    ok("a click on ⠿ Move with a half selected enters place mode and says a photo or an empty frame can be clicked", mode[0].startswith("⠿ Click") and mode[1] >= 1 and "empty frame" in mode[2], mode)
    pg.locator("#editor .img-pair").nth(1).locator(".img-slot-empty").click()
    pg.wait_for_timeout(700)
    l2 = pg.evaluate(IMG_INFO, [1, 0]); r2 = pg.evaluate(IMG_INFO, [1, 1])
    ok("clicking the empty slot moves the picture there and leaves an empty [PHOTO] frame behind",
       r2["path"] == s2["path"] and r2["pic"] and r2["sources"] == s2["sources"] and l2["slot"] and l2["ph"] == "1" and r2["cells"] == 2, (l2, r2))
    ok("the moved picture sits in a 2:3 frame span", pg.evaluate("(() => { const P = document.querySelectorAll('#editor .img-pair')[1]; const cell = [...P.children][1]; return cell.tagName === 'SPAN' && /aspect-ratio:\\s*2\\s*\\/\\s*3/.test(cell.getAttribute('style') || ''); })()"))

    # ---------------- save 2: the swap round-trips ----------------
    pg.evaluate("saveFile()")
    pg.wait_for_timeout(1800)
    out2 = pg.evaluate("window.__disk")
    DUMP = ROOT / ".tmp" / "editor_tests"; DUMP.mkdir(parents=True, exist_ok=True)      # the two saves, for a look when a check fails
    (DUMP / "captions_swap_base.html").write_text(base, encoding="utf-8"); (DUMP / "captions_swap_after.html").write_text(out2, encoding="utf-8")
    P1, P2 = pairs_of(base), pairs_of(out2)
    pics1 = sorted(pic for blk in P1 for pic in pictures(blk))
    pics2 = sorted(pic for blk in P2 for pic in pictures(blk))
    ok("every <picture> of the pairs is saved byte-for-byte as before the drags (sources, crops, alt), only in new frames", pics1 == pics2 and len(pics1) > 6,
       (len(pics1), len(pics2), next((x for x in pics2 if x not in pics1), "")[:300]))
    ok("pair 1's left frame now holds pair 3's old right picture, with its crop", stem(c["path"]) in pictures(P2[0])[0] and re.search(r'object-position:\s*80% 90%', pictures(P2[0])[0]))
    ok("pair 3's right frame holds pair 1's old left picture, with its crop", stem(a["path"]) in pictures(P2[2])[1] and re.search(r'object-position:\s*10% 20%', pictures(P2[2])[1]))
    ok("pair 2 is saved as a [PHOTO] marker cell and then the moved picture",
       re.search(r'<span style="' + re.escape(SLOT_CSS) + r'"><span class="photo-ph"[^>]*>\[PHOTO\]</span></span>\s*<span style="' + re.escape(SLOT_CSS) + r'">\s*<picture>', P2[1])
       and stem(s2["path"]) in P2[1], P2[1][:400])
    ok("no editor residue in the file", not re.search(r'data-(?:sources|path|slot|swap-ref|ed-src)|img-slot-empty|slot-x|slot-up|half-(?:can|target|dragging)|127\.0\.0\.1', body_of(out2)))
    ok("the captions are all still there after the drags", CAP_RE.findall(body_of(out2)) == caps1)
    strip_pairs = lambda h: re.sub(r'<div class="img-pair"[^>]*>[\s\S]*?</div>\s*(?:<div class="img-caption"[^>]*>[^<]*</div>\s*)?', "", body_of(h))
    ok("the rest of the page is untouched by the drags (the pairs and their captions aside: one pair was moved on purpose)",
       strip_pairs(out2) == strip_pairs(base))
    ok("the moved pair's caption moved with it", re.search(r'<div class="img-pair"[^>]*>[\s\S]*?</div>\s*<div class="img-caption"[^>]*>[^<]*</div>', out2) and
       len(re.findall(r'<div class="img-pair"[^>]*>[\s\S]*?</div>\s*<div class="img-caption"', body_of(out2))) == len(re.findall(r'<div class="img-pair"[^>]*>[\s\S]*?</div>\s*<div class="img-caption"', body_of(base))))

    # ---------------- 4. the sidebar's drag-to-insert still works ----------------
    side = pg.evaluate("""() => { const ed = document.getElementById('editor');
      const p = [...ed.querySelectorAll(':scope > p')].find(p => p.textContent.length > 80); p.scrollIntoView();
      const dt = new DataTransfer(); dt.setData('application/x-photo', JSON.stringify({ root: 3, path: 'zz-none/none.jpg', name: 'none.jpg' }));
      const r = p.getBoundingClientRect();
      const ev = t => new DragEvent(t, { bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.left + 30, clientY: r.top + 3 });
      const over = p.dispatchEvent(ev('dragover'));
      const line = !!ed.querySelector('.pl-drop-line'); const lineBefore = ed.querySelector('.pl-drop-line') && ed.querySelector('.pl-drop-line').nextSibling === p;
      const halfLive = !!ed.querySelector('.half-dragging');
      const drop = p.dispatchEvent(ev('drop'));
      const opening = !!document.getElementById('crop-opening') || getComputedStyle(document.getElementById('crop-backdrop')).display !== 'none';
      return { overCancelled: !over, line, lineBefore, halfLive, dropCancelled: !drop, lineGone: !ed.querySelector('.pl-drop-line'), opening }; }""")
    pg.wait_for_timeout(800)
    opening = side["opening"]
    ok("a sidebar photo dragged over a paragraph still shows the drop line above it, and the drop opens the crop dialog",
       side["overCancelled"] and side["line"] and side["lineBefore"] and not side["halfLive"] and side["dropCancelled"] and side["lineGone"] and opening, (side, opening))
    pg.evaluate("try { closeCropModal(); } catch (e) {} try { cropOpening(false); } catch (e) {}")

    ok("no page errors", not errs, errs[:3])
    b.close()

for name, good, info in checks:
    print(("PASS " if good else "FAIL ") + name + ("" if good or not info else f"  {info}"))
print(f"{sum(1 for c in checks if not c[1])} failed")
sys.exit(1 if any(not c[1] for c in checks) else 0)
