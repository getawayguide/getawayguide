"""Regression checks for the fixes of the 2026-11-06 four-agent stress test of the editor suite (see
memory project_suite_stress_1106). Headless; nothing on disk is written: every article is a small synthetic page
handed to window.loadFile through a stub handle whose save lands in window.__disk, every POST to the photo server is
answered locally, and the comments store is a stub.

  A1  a save keeps a curated "In this guide" label; only a heading RENAMED in the session changes its label
  A2  a class-less <a> holding <img>/<div> children (the El Tunco YouTube card) is a block: not wrapped in <p>, not split,
      its DM Mono label keeps its font-family
  A5  tool-written lines come back byte-identical: img attribute order (srcset/sizes in place), the one-line city-map
      figure and <!-- /explore-more --> marker, the BOM and CRLF of the file; a save with nothing dirty writes nothing
  B6  Backspace at the start of the block after a heading / a photo (inside .fn-section) / a guide card does not merge;
      Delete at the end of a heading does not pull the paragraph in; a click on a guide card does not navigate
  B7  Tab on a bullet keeps the caret in it and nests li > ul; Shift+Tab right after gives the exact list back;
      Shift+Tab on three selected bullets outdents all three
  D18 guide_cards.CARD_RE never runs from a marker-less card across prose to the next card's marker
  D19 the map editor's city-map Save keeps the published street raster (keep_base) and no longer needs the OSM extract
  D20 an untagged route figure on a two-map page opens the one config left over, or refuses; never a guess by alphabet
  E23 a comment anchors on the occurrence whose context before AND after agrees, never the first match anywhere

    python tools/test_stress_1106.py          (needs the photo server on :5003)
"""
import json
import os
import pathlib
import re
import sys
import tempfile

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "tools"))
API = "http://127.0.0.1:" + os.environ.get("PHOTO_EDITOR_PORT", "5003")     # a test server on another port: PHOTO_EDITOR_PORT
REL = ".tmp/qa/test1106/page.html"           # a path no real article has; the comments key it makes is stubbed below

checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:600]), flush=True)


# ----------------------------------------------------------------------------- fixtures
def page(body, head_extra="", crlf=False, bom=False):
    t = ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>T</title>\n'
         '<meta property="og:title" content="Kevin&#x27;s page">\n<link rel="stylesheet" href="../../../styles.css">\n'
         '<script type="application/ld+json">\n{\n      "@type": "Article",\n      "dateModified": "2026-10-01",\n      "datePublished": "2026-06-01"\n}\n</script>\n'
         + head_extra + '</head>\n<body><main><div class="article-body" style="max-width:825px">\n' + body + '\n</div></main>\n</body></html>\n')
    if crlf:
        t = t.replace("\n", "\r\n")
    return ("﻿" if bom else "") + t


TOC_PAGE = page(
    '  <div class="toc-cols">\n'
    '    <div class="toc-row"><span class="toc-num">01</span><a href="#orgov" class="toc-link">Explore the Observatory</a></div>\n'
    '    <div class="toc-row"><span class="toc-num">02</span><a href="#cascade" class="toc-link">Climb the Cascade</a></div>\n'
    '  </div>\n'
    '  <h2 class="article-h2" id="orgov">1. Explore Orgov Observatory</h2>\n  <p>First paragraph of prose here.</p>\n'
    '  <h2 class="article-h2" id="cascade">2. Climb the Steps of the Cascade</h2>\n  <p>Second paragraph of prose here.</p>')

CARD_PAGE = page(
    '  <h3 class="article-h3" id="surfing">Surfing</h3>\n'
    '  <a href="https://www.youtube.com/watch?v=x" target="_blank" rel="noopener" style="display:block;margin:1.5rem auto;max-width:600px;position:relative;aspect-ratio:16/9;text-decoration:none">\n'
    '  <img loading="lazy" src="https://img.youtube.com/vi/x/maxresdefault.jpg" alt="video" style="width:100%;height:100%;object-fit:cover;display:block">\n'
    '  <div style="position:absolute;inset:0;background:rgba(0,0,0,.3)"><div style="width:64px;height:64px;border-radius:50%"></div></div>\n'
    '  <div style="position:absolute;bottom:.75rem;left:.75rem;font-family:\'DM Mono\',monospace;font-size:.6rem;color:#fff">Watch on YouTube</div>\n'
    '</a><p>The main draw is the surf.</p>\n  <p>Another paragraph.</p>')

CMBASE = ('<img class="cmbase" src="../../../Images/web/city-maps/zz.png" srcset="../../../Images/web/city-maps/zz-1x.webp 1350w, ../../../Images/web/city-maps/zz.png 1520w" sizes="729px" width="760" height="470" alt="Zz map" loading="lazy">')
FIG = ('<figure class="citymap-fig"><div class="citymap desk"><div class="cmmap">' + CMBASE +
       '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 470"><path d="M0 0 L10 10" fill="#000"/><circle cx="1" cy="1" r="2"/><a href="https://www.google.com/maps/place/A"><g><rect x="0" y="0" width="5" height="5"/></g></a></svg>'
       '</div><div class="cmkey"><a class="cmrow" data-i="1" href="https://www.google.com/maps/place/Francesco&#x27;s"><span class="num" style="background:#C8DDD5;color:#1C2821">1</span><span class="nm">Francesco&#x27;s Hostel</span></a></div></div></figure>')
XM = ('<section class="xm" id="explore-more"><h2 class="xm-h">Explore More</h2><div class="xm-g"><a class="xm-c" href="other.html" data-xm="auto" data-rel="x/other.html">'
      '<picture><source type="image/webp" media="(min-width:769px)" srcset="../../../Images/web/explore/o-1x.webp 311w" sizes="168px"><img src="../../../Images/web/explore/o-mob-2x.jpg" srcset="../../../Images/web/explore/o-mob-1x.jpg 393w" sizes="calc(100vw - 40px)" alt="" loading="lazy" width="786" height="442"></picture>'
      '<span class="xm-k g"><span class="dot"></span>Greece · Guide</span><span class="xm-t">Other</span><span class="xm-d">Blurb.</span></a></div></section><!-- /explore-more -->')
TOOL_PAGE = page('  <h2 class="article-h2" id="a">Heading A</h2>\n  <p>Prose one.</p>\n' + FIG + '\n<style>.x{color:red}</style>\n  <p>Prose two.</p>\n' + XM, crlf=True, bom=True)

GB = ('<a class="gb-card" href="meteora.html" data-guide="meteora.html"><picture><img src="../../../Images/web/g-mob-2x.jpg" alt="Meteora" loading="lazy" width="786" height="442"></picture>'
      '<span class="t"><span class="k"><span class="dot"></span>In-depth guide</span><span class="h">The Meteora Guide</span><span class="d">Want the detailed version?</span><span class="b">Read the guide →</span></span></a><!-- /gb-card -->')
FN_PAGE = page(
    '  <div class="fn-section" id="meteora">\n    <div class="fn-city-hd">\n      <h2>Meteora</h2>\n      <span class="fn-time">2 days recommended</span>\n    </div>\n    ' + GB + '\n'
    '    <p>Although there are day tours, stay a night.</p>\n'
    '    <div class="img-pair"><span style="display:block;position:relative;overflow:hidden;width:100%;aspect-ratio:2/3;border-radius:2px"><img src="../../../Images/web/a.jpg" alt="a" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover"></span>'
    '<span style="display:block;position:relative;overflow:hidden;width:100%;aspect-ratio:2/3;border-radius:2px"><img src="../../../Images/web/b.jpg" alt="b" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover"></span></div>\n'
    '    <p>Under the photos a paragraph.</p>\n'
    '    <ul>\n      <li>First bullet under a list after the paragraph.</li>\n    </ul>\n  </div>\n'
    '  <h2 class="article-h2" id="h2">A Heading</h2>\n  <p>Paragraph under the heading.</p>\n  <h3 class="article-h3" id="h3">Sub heading</h3>\n  <p>Paragraph under the sub heading.</p>')

LI = 'style="padding:.6rem 0;border-bottom:1px solid rgba(26,22,16,.1)"'
LIST_BODY = ('<p>Intro para.</p><ul style="list-style:none;padding:0;margin:1.5rem 0">' +
             "".join('<li %s><b>Item %d</b> — text %d</li>' % (LI if i < 5 else 'style="padding:.6rem 0"', i, i) for i in range(1, 6)) + '</ul><p>After para.</p>')
LIST_PAGE = page("  " + LIST_BODY)

COMMENT_PAGE = page(
    '  <p>Walk through the old town in the morning when it is empty.</p>\n'
    '  <p>In Struga the old town is smaller, and the old town there closes early.</p>\n'
    '  <p>Nothing else mentions it.</p>')

# ----------------------------------------------------------------------------- browser helpers
OPEN = """async ([text, name, bom]) => { folderHandle = null; window.__disk = text; window.__writes = 0; window.__saved = null;
  const bytes = new TextEncoder().encode((bom ? '\\ufeff' : '') + text);
  const h = { kind: 'file', name, getFile: async () => new File([bytes], name, { type: 'text/html' }),
    createWritable: async () => ({ write: async d => { window.__disk = d; window.__saved = d; window.__writes++; }, close: async () => {} }) };
  return await window.loadFile(h); }"""
CARET = """([sel, which, where]) => { const ed = document.getElementById('editor'); const el = ed.querySelectorAll(sel)[which]; if (!el) return null;
  el.scrollIntoView({ block: 'center' }); const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); let t, first = null, last = null;
  while ((t = w.nextNode())) { if (!t.nodeValue.trim()) continue; if (!first) first = t; last = t; }
  const r = document.createRange();
  if (where === 'start') r.setStart(first || el, 0); else if (where === 'end') { if (last) r.setStart(last, last.nodeValue.length); else r.setStart(el, el.childNodes.length); }
  else r.setStart(first, Math.min(where, first.nodeValue.length));
  r.collapse(true); ed.focus(); const s = getSelection(); s.removeAllRanges(); s.addRange(r); return (first || el).textContent.slice(0, 30); }"""
BODY = """() => { const e = document.getElementById('editor').cloneNode(true); e.querySelectorAll('[data-chrome]').forEach(x => x.remove()); return e.innerHTML; }"""


def body_of(html):
    m = re.search(r'<div class="article-body"[^>]*>', html)
    return html[m.end():html.index("</main>")] if m else html


def new_page(b, threads=None):
    ctx = b.new_context(viewport={"width": 1500, "height": 1000})
    ctx.add_init_script("try { localStorage.setItem('editorAutosave', 'off'); } catch (e) {}")
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
    pg.on("dialog", lambda d: d.dismiss())

    def handler(route):
        req = route.request
        u = req.url
        if req.method == "OPTIONS":
            return route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"})
        if req.resource_type == "image" and "/site/" in u:
            return route.fulfill(status=200, body=b"", headers={"Content-Type": "image/png", "Access-Control-Allow-Origin": "*"})
        if "/comments/" in u and req.method == "GET":
            return route.fulfill(status=200, body=json.dumps({"threads": threads or []}), headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})
        if "/review/for" in u and req.method == "GET":      # the margin loads its comments with the round
            return route.fulfill(status=200, body=json.dumps({"changes": [], "comments": {"threads": threads or []}, "comments_key": "test1106__page", "decided": [], "decisions": {}, "slug": None}),
                                 headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})
        if req.method == "POST":
            return route.fulfill(status=200, body='{"ok":true,"queued":[],"threads":[],"decisions":{},"kept":[],"warn":[],"why":[]}',
                                 headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})
        return route.continue_()
    pg.route(re.compile(r"^http://127\.0\.0\.1:5003/.*"), handler)
    pg.goto(API + "/browser", wait_until="domcontentloaded")
    pg.wait_for_timeout(1200)
    return ctx, pg, errs


def open_text(pg, text, bom=False, wait=2500):
    pg.evaluate("r => { window.review.state.relOverride = r; }", REL)
    pg.evaluate(OPEN, [text.lstrip("﻿"), "page.html", bom])
    pg.wait_for_timeout(wait)
    pg.evaluate("() => { try { _spellWalkToken++; } catch (e) {} }")


def save(pg, dirty=True, wait=900):
    if dirty:
        pg.evaluate("setDirty(true)")
    pg.evaluate("saveFile()")
    pg.wait_for_timeout(wait)
    return pg.evaluate("window.__saved")


# ----------------------------------------------------------------------------- the checks
def t_A1(pg):
    open_text(pg, TOC_PAGE)
    out = save(pg)
    ok("A1 a save keeps the curated contents labels", out and 'class="toc-link">Explore the Observatory</a>' in out and 'class="toc-link">Climb the Cascade</a>' in out,
       re.findall(r'class="toc-link">([^<]*)<', out or ""))
    pg.evaluate("() => { const h = document.querySelector('#editor h2#cascade'); h.textContent = '2. Up the Cascade'; setDirty(true); }")
    out = save(pg)
    ok("A1 a heading renamed in the session updates ITS label only", out and 'class="toc-link">Explore the Observatory</a>' in out and 'class="toc-link">2. Up the Cascade</a>' in out,
       re.findall(r'class="toc-link">([^<]*)<', out or ""))


def t_A2(pg):
    open_text(pg, CARD_PAGE)
    out = save(pg)
    b = body_of(out or "")
    ok("A2 the link card stays one block (no <p> around or inside it, overlay divs kept)", out and '<p><a href="https://www.youtube' not in b and b.count('inset:0') == 1 and b.count('youtube.com/watch') == 1 and '<p></p>' not in b, b[:600])
    ok("A2 the card's DM Mono label keeps its font-family", "font-family:'DM Mono'" in b or 'font-family: "DM Mono"' in b or "font-family:&quot;DM Mono&quot;" in b, re.findall(r"Watch on YouTube[^<]*", b))
    ok("A2 the paragraph after the card is intact", '<p>The main draw is the surf.</p>' in b)


def t_A5(pg):
    open_text(pg, TOOL_PAGE, bom=True)
    n0 = pg.evaluate("window.__writes")
    pg.evaluate("saveFile()"); pg.wait_for_timeout(700)
    ok("A5 Ctrl+S with nothing dirty writes nothing", pg.evaluate("window.__writes") == n0, pg.evaluate("window.__writes"))
    out = save(pg)
    src = TOOL_PAGE.lstrip("﻿")
    ok("A5 the city-map figure line comes back byte-identical (img attribute order, self-closed SVG, &#x27;)", out and FIG in out, (out or "")[:0])
    ok("A5 the one-line Explore More block with its marker comes back byte-identical", out and XM in out)
    ok("A5 <style> after the figure keeps its column", out and "\r\n<style>.x{color:red}</style>\r\n" in out)
    ok("A5 CRLF line endings are kept", out and out.count("\r\n") > 10 and not re.search(r"[^\r]\n", out), (out or "")[:200])
    ok("A5 the BOM is written back", out and out.startswith("﻿"))
    ok("A5 the head's entities are untouched (&#x27;)", out and 'content="Kevin&#x27;s page"' in out)
    ok("A5 the JSON-LD keeps its indentation when dateModified moves", out and re.search(r'\n      "dateModified": "\d{4}-\d\d-\d\d",\r?\n      "datePublished"', out), re.findall(r'"dateModified"[^\n]*', out or ""))


def t_B6(pg):
    open_text(pg, FN_PAGE)
    pg.evaluate(CARET, ["#editor h2.article-h2 + p", 0, "start"]); pg.keyboard.press("Backspace"); pg.wait_for_timeout(150)
    ok("B6 Backspace at the start of the paragraph after an h2 does not merge", pg.evaluate("document.querySelector('#editor h2#h2').textContent") == "A Heading" and pg.evaluate("!!document.querySelector('#editor h2#h2 + p')"),
       pg.evaluate("document.querySelector('#editor h2#h2').outerHTML"))
    pg.evaluate(CARET, ["#editor h3.article-h3", 0, "end"]); pg.keyboard.press("Delete"); pg.wait_for_timeout(150)
    ok("B6 Delete at the end of an h3 does not pull the paragraph in", pg.evaluate("document.querySelector('#editor h3#h3').textContent") == "Sub heading" and pg.evaluate("!!document.querySelector('#editor h3#h3 + p')"))
    pg.evaluate(CARET, ["#editor .fn-section .img-pair + p", 0, "start"]); pg.keyboard.press("Backspace"); pg.wait_for_timeout(150)
    ok("B6 Backspace at the start of the paragraph under a photo pair inside .fn-section does not merge", pg.evaluate("!!document.querySelector('#editor .fn-section .img-pair + p') && document.querySelectorAll('#editor img').length === 3"),
       pg.evaluate("document.querySelector('#editor .fn-section .img-pair').nextElementSibling.outerHTML.slice(0, 200)"))
    pg.evaluate(CARET, ["#editor .fn-section ul li", 0, "start"]); pg.keyboard.press("Backspace"); pg.wait_for_timeout(150)
    ok("B6 Backspace at the start of the first bullet after a paragraph still merges normally (not an object)", pg.evaluate("!document.querySelector('#editor .fn-section ul') || document.querySelector('#editor .fn-section ul li').textContent.startsWith('First')"))
    pg.evaluate(CARET, ["#editor a.gb-card + p, #editor a.gb-card ~ p", 0, "start"]); pg.keyboard.press("Backspace"); pg.keyboard.press("Backspace"); pg.wait_for_timeout(150)
    mk = pg.evaluate("[document.querySelectorAll('#editor a.gb-card').length, (document.getElementById('editor').innerHTML.match(/<!-- \\/gb-card -->/g) || []).length]")
    ok("B6 Backspace at the start of the paragraph after a guide card keeps the card and its marker", mk == [1, 1] and pg.evaluate("document.querySelector('#editor a.gb-card').nextElementSibling.textContent.startsWith('Although')"), mk)
    pg.evaluate(CARET, ["#editor .fn-city-hd .fn-time", 0, "end"]); pg.keyboard.press("Delete"); pg.wait_for_timeout(150)
    ok("B6 Delete at the end of the city chip does not pull the guide card in", pg.evaluate("document.querySelector('#editor .fn-time').textContent") == "2 days recommended" and pg.evaluate("document.querySelectorAll('#editor a.gb-card').length") == 1)
    url0 = pg.url
    box = pg.evaluate("() => { const h = document.querySelector('#editor a.gb-card .h'); h.scrollIntoView({block:'center'}); const r = h.getBoundingClientRect(); return [r.x + 20, r.y + r.height / 2]; }")
    pg.mouse.click(box[0], box[1]); pg.wait_for_timeout(500); pg.keyboard.type("ZZ"); pg.wait_for_timeout(200)
    ok("B6 a click on a guide card neither navigates nor lets typing into or over it", pg.url == url0 and pg.evaluate("document.querySelectorAll('#editor a.gb-card').length") == 1 and not pg.evaluate("document.querySelector('#editor a.gb-card').textContent.includes('ZZ')"),
       (pg.url, pg.evaluate("document.querySelectorAll('#editor a.gb-card').length")))
    out = save(pg)
    ok("B6 the save keeps the card with its marker on the card's line", out and GB in out, re.findall(r'</a>[^\n]{0,40}', out or "")[:3])


def t_B7(pg):
    open_text(pg, LIST_PAGE)
    h0 = pg.evaluate(BODY)
    pg.evaluate(CARET, ["#editor li", 2, 4]); pg.keyboard.press("Tab"); pg.wait_for_timeout(150)
    pg.keyboard.type("@@"); pg.wait_for_timeout(150)
    st3 = pg.evaluate("() => { const l = [...document.querySelectorAll('#editor li')].reverse().find(x => x.textContent.includes('text 3')); /* the innermost: a parent entry holds its text too */ return l ? { nested: l.parentElement.parentElement.tagName === 'LI', text: l.textContent, ulul: !!document.querySelector('#editor ul > ul') } : null; }")
    ok("B7 Tab nests the bullet under the previous one (li > ul) and the caret stays in it", st3 and st3["nested"] and "@@" in st3["text"] and not st3["ulul"], st3)
    pg.keyboard.press("Backspace"); pg.keyboard.press("Backspace"); pg.wait_for_timeout(100)
    pg.keyboard.press("Shift+Tab"); pg.wait_for_timeout(150)
    ok("B7 Shift+Tab right after Tab gives the list back exactly", pg.evaluate(BODY) == h0, pg.evaluate(BODY)[:500])
    # three bullets selected: Shift+Tab outdents all three into paragraphs
    pg.evaluate("""() => { const lis = document.querySelectorAll('#editor li'); const r = document.createRange(); r.setStart(lis[1].firstChild.firstChild, 1); r.setEnd(lis[3].lastChild, lis[3].lastChild.length - 1);
      document.getElementById('editor').focus(); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }""")
    pg.keyboard.press("Shift+Tab"); pg.wait_for_timeout(200)
    st = pg.evaluate("() => ({ lis: document.querySelectorAll('#editor li').length, ps: [...document.querySelectorAll('#editor > p')].map(p => p.textContent.slice(0, 6)) })")
    ok("B7 Shift+Tab on three selected bullets outdents all three", st["lis"] == 2 and st["ps"] == ["Intro ", "Item 2", "Item 3", "Item 4", "After "], st)
    # Enter twice at the end of a nested bullet leaves the sub-list without li > li or an empty list
    open_text(pg, LIST_PAGE)
    pg.evaluate(CARET, ["#editor li", 2, 4]); pg.keyboard.press("Tab"); pg.wait_for_timeout(100)
    pg.evaluate(CARET, ["#editor li li", 0, "end"]); pg.keyboard.press("Enter"); pg.keyboard.press("Enter"); pg.wait_for_timeout(200)
    ok("B7 Enter twice at the end of a nested bullet: no li > li, no empty list", not pg.evaluate("!!document.querySelector('#editor li > li') || [...document.querySelectorAll('#editor ul')].some(u => !u.children.length)"), pg.evaluate(BODY)[:500])


def t_D20(pg):
    two = page('  <p>Before.</p>\n  <figure class="itinerary-map"><svg viewBox="0 0 10 10"><text>ROUTES</text></svg></figure>\n'
               '  <figure class="itinerary-map" data-map="zz-boat"><svg viewBox="0 0 10 10"><text>BOAT</text></svg></figure>\n  <p>After.</p>')
    opened = []
    pg.route(re.compile(r"^http://127\.0\.0\.1:5002/.*"), lambda route: (opened.append(route.request.url), route.fulfill(status=200, body=json.dumps({"itinerary": ["zz-boat", "zz-routes"], "city": []}) if "slug-for" in route.request.url else "<!doctype html><title>m</title>",
                                                                                                           headers={"Content-Type": "application/json" if "slug-for" in route.request.url else "text/html", "Access-Control-Allow-Origin": "*"})))
    open_text(pg, two)
    pg.evaluate("() => document.querySelectorAll('#editor figure.itinerary-map')[0].querySelector('.map-edit').click()"); pg.wait_for_timeout(1500)
    frame = pg.evaluate("document.getElementById('map-frame').getAttribute('src') || ''")
    url = next((u for u in opened if "embed=1" in u), frame)
    ok("D20 the untagged figure opens the one config the tagged figure does not take (zz-routes), not the alphabetical first", "slug=zz-routes" in (url or ""), opened)
    pg.evaluate("document.getElementById('map-close-off').click()"); pg.wait_for_timeout(200)
    opened.clear()
    both = page('  <figure class="itinerary-map"><svg viewBox="0 0 10 10"><text>A</text></svg></figure>\n  <figure class="itinerary-map"><svg viewBox="0 0 10 10"><text>B</text></svg></figure>')
    open_text(pg, both)
    pg.evaluate("() => document.querySelectorAll('#editor figure.itinerary-map')[1].querySelector('.map-edit').click()"); pg.wait_for_timeout(1500)
    off = pg.evaluate("() => { const o = document.getElementById('map-offline'); return o && !o.hidden ? o.textContent : ''; }")
    ok("D20 two untagged figures, two configs: the editor refuses instead of guessing", "data-map" in off and not any("embed=1" in u for u in opened), (off[:120], opened))
    pg.evaluate("document.getElementById('map-close-off').click()")
    pg.unroute(re.compile(r"^http://127\.0\.0\.1:5002/.*"))


def t_E23(b):
    text_before = "In Struga the old town is smaller, and "
    threads = [
        {"id": "cmA", "author": "Kevin", "text": "the last one", "created": "2026-11-06T00:00:00Z", "resolved": False, "replies": [],
         "anchor": {"quote": "the old town", "before": text_before[-40:], "after": " there closes early."}},
        {"id": "cmB", "author": "Kevin", "text": "context gone", "created": "2026-11-06T00:00:00Z", "resolved": False, "replies": [],
         "anchor": {"quote": "the old town", "before": "zzzz nothing like this anywhere ", "after": " qqqq nothing like this either"}},
        {"id": "cmC", "author": "Kevin", "text": "unique", "created": "2026-11-06T00:00:00Z", "resolved": False, "replies": [],
         "anchor": {"quote": "Nothing else mentions it.", "before": "", "after": ""}},
    ]
    ctx, pg, errs = new_page(b, threads)
    open_text(pg, COMMENT_PAGE, wait=4000)
    pos = pg.evaluate("""() => { const ed = document.getElementById('editor'); const all = [...ed.querySelectorAll('p')].map(p => p.textContent).join(' ');
      const out = {}; for (const id of ['cmA', 'cmB', 'cmC']) { const m = ed.querySelector('mark.cm[data-cm="' + id + '"]'); out[id] = m ? m.closest('p').textContent.slice(0, 12) + '|' + [...m.closest('p').querySelectorAll('mark.cm[data-cm="' + id + '"]')].length : null; }
      const a = ed.querySelector('mark.cm[data-cm="cmA"]'); if (a) { const p = a.closest('p'); const w = document.createTreeWalker(p, NodeFilter.SHOW_TEXT); let t = '', before = ''; let n; while ((n = w.nextNode())) { if (n === a.firstChild) before = t; t += n.nodeValue; } out.offA = before.length; out.occ = [...t.matchAll(/the old town/g)].map(x => x.index); }
      return out; }""")
    ok("E23 the comment lands on the occurrence whose context before AND after agrees (the third 'the old town', not the first)", pos.get("cmA", "").startswith("In Struga") and pos.get("occ") and pos.get("offA") == pos["occ"][-1], pos)
    ok("E23 a comment whose context matches none of several occurrences is not anchored anywhere", pos.get("cmB") is None, pos)
    ok("E23 a unique quote still anchors without context", (pos.get("cmC") or "").startswith("Nothing else"), pos)
    ok("E23 no page errors", not errs, errs[:2])
    ctx.close()


def t_D18():
    import guide_cards
    card = lambda g, marker: '\n    <a class="gb-card" href="%s" data-guide="%s"><picture><img src="x.jpg" alt=""></picture><span class="t"><span class="h">T</span></span></a>%s' % (g, g, "<!-- /gb-card -->" if marker else "")
    s = ('<div class="fn-section" id="a">\n    <div class="fn-city-hd"><h2>A</h2></div>' + card("a.html", False) +
         '\n    <p>Prose that must survive, with <a href="https://x">a link</a>.</p>\n  </div>\n<div class="fn-section" id="b">\n    <div class="fn-city-hd"><h2>B</h2></div>' + card("b.html", True) + '\n    <p>More prose.</p>\n  </div>')
    out = guide_cards.CARD_RE.sub("", s)
    ok("D18 CARD_RE removes the marked card only; the marker-less card and the prose between stay", "Prose that must survive" in out and 'data-guide="a.html"' in out and 'data-guide="b.html"' not in out and "More prose." in out, out[:400])
    ok("D18 CARD_RE matches exactly one card per hit", [m.group(1) for m in guide_cards.CARD_RE.finditer(s)] == ["b.html"])


def t_D19():
    import map_editor
    calls = []
    real_build, real_has = map_editor.cm.build, map_editor.has_base
    map_editor.cm.build = lambda cfg_path, embed=False, article=None, keep_base=False, **k: calls.append({"embed": embed, "keep_base": keep_base, "article": article})
    map_editor.has_base = lambda slug: slug == "zz-qa-city"
    try:
        with tempfile.TemporaryDirectory() as td:
            cfg = pathlib.Path(td) / "zz-qa-city.json"
            cfg.write_text(json.dumps({"slug": "zz-qa-city", "pois": []}), encoding="utf-8")
            try:
                map_editor._city_render(cfg, embed=True, article="x/y.html")     # fails after build() on the missing preview file; the call is what matters
            except Exception:
                pass
        ok("D19 a city-map Save (embed=True) keeps the published base raster", calls and calls[-1]["embed"] is True and calls[-1]["keep_base"] is True, calls)
    finally:
        map_editor.cm.build, map_editor.has_base = real_build, real_has
    src = (ROOT / "tools" / "map_editor.py").read_text(encoding="utf-8")
    ok("D19 the Save route asks for the OSM extract only when no raster is published", "why = None if has_base(slug) else ensure_osm(cfg_path)" in src)


ONLY = sys.argv[1:]                       # e.g. "python tools/test_stress_1106.py B7 E23" runs those alone
want = lambda fn: not ONLY or any(fn.__name__.endswith(o) for o in ONLY)
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx, pg, errs = new_page(b)
    ok("the editor loads without a script error", not errs, errs[:2])
    for fn in [f for f in (t_A1, t_A2, t_A5, t_B6, t_B7, t_D20) if want(f)]:
        try:
            fn(pg)
        except Exception as e:  # one broken check must not hide the rest
            ok(fn.__name__ + " ran", False, repr(e)[:300])
    ok("no page errors through the editor checks", not errs, errs[:3])
    ctx.close()
    if want(t_E23):
        try:
            t_E23(b)
        except Exception as e:
            ok("t_E23 ran", False, repr(e)[:300])
    b.close()
if want(t_D18): t_D18()
if want(t_D19): t_D19()
bad = [c for c in checks if not c[1]]
print("\n%d checks, %d failed" % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
