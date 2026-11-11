"""The itineraries' currency converter in the editor (Kevin, 2026-11-08: "show conversion tool in editor as a normal
element"). The page holds <div class="cconv" data-local data-rates ...> with a one-line fallback inside; currency.js
renders it on the site. The editor draws it the same way, as ONE read-only block, and saves the file's own bytes back.

Headless, nothing on disk is written: the live Armenia itinerary's TEXT is handed to window.loadFile through a stub
handle whose save lands in window.__disk; the photo server (:5003) is only read from (/browser, /site/*.css, /Images).

  R1  it renders as on the page: grid, menu (USD first, the local currency left out), 1 USD = 361 AMD, flags, the
      as-of line; the fallback line is hidden; the block is contenteditable=false + data-ed-ro; the article opens clean
  R2  it cannot be typed into: a click selects it whole (toolbar shown), typing / Enter change nothing
  R3  Delete at the end of the paragraph above and Backspace at the start of the block below leave it alone
  R4  Delete with the block selected asks first; Cancel keeps it, OK removes it, Ctrl+Z puts it back
  R5  ⠿ Move: click-to-place puts it before the paragraph above; the saved file has the block's line byte-identical
      (no style="", no chrome) at the new spot and nothing else changed
  R6  a save with nothing edited gives the block back byte-identical (indent, the file's own line ending, data-rates),
      no editor residue
  R7  a review round with a change in the paragraph beside it anchors, is accepted and saved; a comment next to it anchors
  R8  screenshots at 1440 (.tmp/qa/cconv/)

    python tools/test_editor_currency_block.py          (needs the photo server on :5003)
"""
import difflib
import os
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)
API = "http://127.0.0.1:" + os.environ.get("PHOTO_EDITOR_PORT", "5003")
REL = ".tmp/qa/cconv/page.html"            # a path no real article has: comments and rounds are stubbed below
OUT = ROOT / ".tmp" / "qa" / "cconv"
OUT.mkdir(parents=True, exist_ok=True)
PAGE = ROOT / "armenia" / "armenia-itinerary.html"
TEXT = PAGE.read_text(encoding="utf-8", newline="")          # read only; every save lands in window.__disk

checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:700]), flush=True)


# the file's own line ending: the page has been saved both ways (CRLF, and LF since 2026-11-10), and git stores LF
# anyway, so the checks below hold the save to whichever the file uses rather than to CRLF
NL = "\r\n" if "\r\n" in TEXT else "\n"
# the block's own line in the file, and the line as the file has it (indent + line ending)
LINE = next(l for l in TEXT.split(NL) if '<div class="cconv"' in l)
SRC = LINE.strip()
# the as-of line follows the block's data-asof, which the monthly rates refresh moves (Nov 8 -> Nov 11, 2026)
_y, _m, _d = re.search(r'data-asof="(\d{4})-(\d\d)-(\d\d)"', SRC).groups()
ASOF = "Mid-market rates, %s %d, %s." % (["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(_m) - 1], int(_d), _y)
assert LINE.startswith("    <div class=\"cconv\"") and SRC.endswith("</div></div>"), LINE[:80]

OPEN = """async ([text, name]) => { folderHandle = null; window.__disk = text; window.__writes = 0; window.__saved = null;
  const bytes = new TextEncoder().encode(text);
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
# the block as the file would get it: chrome and the editor's own attributes off
BARE = """() => { const c = document.querySelector('#editor .cconv'); if (!c) return null; const k = c.cloneNode(true);
  k.querySelectorAll('[data-chrome]').forEach(x => x.remove()); ['contenteditable', 'data-ed-ro', 'data-ed-src-id'].forEach(a => k.removeAttribute(a)); return k.outerHTML; }"""
STATE = """() => { const c = document.querySelector('#editor .cconv'); if (!c) return { n: 0 }; const ed = c.querySelector(':scope > .cconv-ed');
  const cs = x => getComputedStyle(x); const fb = c.querySelector('.cconv-fb');
  return { n: document.querySelectorAll('#editor .cconv').length, ce: c.getAttribute('contenteditable'), ro: c.getAttribute('data-ed-ro'), srcId: c.getAttribute('data-ed-src-id'),
    drawn: !!ed, chrome: ed && ed.getAttribute('data-chrome'), cols: ed ? cs(ed.querySelector('.cconv-grid')).gridTemplateColumns.split(' ').length : 0,
    opts: ed ? [...ed.querySelectorAll('option')].map(o => o.value) : [], sel: ed ? ed.querySelector('select').value : null,
    a: ed ? ed.querySelector('.cconv-a').value : null, b: ed ? ed.querySelector('.cconv-b').value : null,
    lbl: ed ? ed.querySelector('.cconv-lbl').textContent.trim() : null, asof: ed ? (ed.querySelector('.cconv-asof') || {}).textContent : null,
    flags: ed ? [...ed.querySelectorAll('.cconv-fl')].map(f => (cs(f).backgroundImage.match(/flags\\/([a-z]+)\\.png/) || [])[1]) : [],
    flagW: ed ? Math.round(ed.querySelector('.cconv-fl').getBoundingClientRect().width) : 0,
    fbShown: fb ? cs(fb).display !== 'none' : null, border: ed ? cs(ed.querySelector('.cconv-side')).borderTopWidth : null,
    inert: ed ? cs(ed).pointerEvents : null, dirty: typeof isDirty !== 'undefined' ? isDirty : null, imgs: c.querySelectorAll('img').length }; }"""


def body_of(html):
    m = re.search(r'<div class="artbody"[^>]*>', html)
    return html[m.end():] if m else html


def new_page(b, threads=None, review=None, images=False):
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script("try { localStorage.setItem('editorAutosave', 'off'); } catch (e) {}")
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
    dialogs = {"accept": False, "seen": []}

    def on_dialog(d):
        dialogs["seen"].append(d.message)
        d.accept() if dialogs["accept"] else d.dismiss()
    pg.on("dialog", on_dialog)

    def handler(route):
        req = route.request
        u = req.url
        if req.method == "OPTIONS":
            return route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"})
        if req.resource_type == "image" and "/site/" in u and not images:
            return route.fulfill(status=200, body=b"", headers={"Content-Type": "image/png", "Access-Control-Allow-Origin": "*"})
        if "/comments/" in u and req.method == "GET":
            return route.fulfill(status=200, body='{"threads": %s}' % _json(threads or []), headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})
        if "/review/for" in u and req.method == "GET":
            r = {"changes": [], "comments": {"threads": threads or []}, "comments_key": "cconv__page", "decided": [], "decisions": {}, "slug": None}
            if review:
                r.update(review)
            return route.fulfill(status=200, body=_json(r), headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})
        if req.method == "POST":
            return route.fulfill(status=200, body='{"ok":true,"queued":[],"threads":[],"decisions":{},"kept":[],"warn":[],"why":[]}',
                                 headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})
        return route.continue_()
    pg.route(re.compile(r"^http://127\.0\.0\.1:5003/.*"), handler)
    pg.goto(API + "/browser", wait_until="domcontentloaded")
    pg.wait_for_timeout(1200)
    return ctx, pg, errs, dialogs


def _json(x):
    import json
    return json.dumps(x)


def open_text(pg, wait=4000):
    pg.evaluate("r => { window.review.state.relOverride = r; }", REL)
    pg.evaluate(OPEN, [TEXT, "armenia-itinerary.html"])
    pg.wait_for_timeout(wait)
    pg.evaluate("() => { try { _spellWalkToken++; } catch (e) {} }")


def save(pg, dirty=True, force=False, wait=1500):
    if dirty:
        pg.evaluate("setDirty(true)")
    pg.evaluate("window.saveFile(%s)" % ("{ force: true }" if force else ""))
    pg.wait_for_timeout(wait)
    return pg.evaluate("window.__saved")


DATE_LINE = re.compile(r'dateModified|modified_time|Last updated|datePublished|"lastmod"|class="hero-date', re.I)
# first-save normalizations of this hand-written page: three bullets carry a pasted <span style="color: rgba(28, 40, 33, 0.86)">
# that the save unwraps, and one of them a trailing space before </li>. The editor did both before the converter was
# drawn too (checked against the pre-change editor, 2026-11-08), so a line whose only difference is that is not this
# block's doing.
PASTED_SPAN = re.compile(r'<span style="color: rgba\(28, 40, 33, 0\.86\)">|</span>')
norm = lambda l: re.sub(r'\s+</li>', '</li>', PASTED_SPAN.sub("", l))     # the space sits before the </span>, so the span goes first


def other_diffs(out, base=TEXT):
    """lines that differ between the saved file and the original, minus the date lines a dirty save touches and the
    pasted-span unwrap above; empty when the save changed nothing else (the leftovers go to .tmp/qa/cconv/diff-rest.txt)"""
    d = [l for l in difflib.unified_diff(base.split(NL), (out or "").split(NL), lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
    d = [l for l in d if not DATE_LINE.search(l)]
    minus = {norm(l[1:]) for l in d if l[0] == "-" and "rgba(28, 40, 33, 0.86)" in l}
    rest = [l for l in d if not ((l[0] == "-" and "rgba(28, 40, 33, 0.86)" in l) or (l[0] == "+" and norm(l[1:]) in minus))]
    if rest:
        (OUT / "diff-rest.txt").write_text("\n".join(rest), encoding="utf-8")
    return rest


def click_block(pg):
    box = pg.evaluate("() => { const c = document.querySelector('#editor .cconv'); c.scrollIntoView({ block: 'center' }); const r = c.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }")
    pg.mouse.click(box[0], box[1])
    pg.wait_for_timeout(300)
    return pg.evaluate("() => ({ sel: !!window.__roSelected(), tb: document.getElementById('ro-toolbar').style.display !== 'none', whole: (() => { const s = getSelection(); if (!s.rangeCount || s.isCollapsed) return false; const r = s.getRangeAt(0); return r.intersectsNode(document.querySelector('#editor .cconv')); })() })")


# ----------------------------------------------------------------------------- the checks
def t_R1(pg):
    open_text(pg)
    st = pg.evaluate(STATE)
    ok("R1 the block is drawn as on the page (grid of three, outlined sides, the as-of line)", st["n"] == 1 and st["drawn"] and st["chrome"] == "cconv" and st["cols"] == 3 and st["border"] == "1px" and st["asof"] == ASOF, (ASOF, st))
    ok("R1 the menu lists USD EUR GBP AUD CAD CHF with USD chosen; 1 USD = 361 AMD · Dram", st["opts"] == ["USD", "EUR", "GBP", "AUD", "CAD", "CHF"] and st["sel"] == "USD" and st["a"] == "1" and st["b"] == "361" and st["lbl"] == "AMD · Dram", st)
    ok("R1 the US and Armenian flags are drawn (as backgrounds, no <img> for the photo code to meet)", st["flags"] == ["us", "am"] and st["flagW"] == 20 and st["imgs"] == 0, st)
    ok("R1 the file's fallback line is hidden behind the drawing", st["fbShown"] is False, st)
    ok("R1 the block is read-only (contenteditable=false, data-ed-ro), paired with its source (data-ed-src-id), inert to the pointer", st["ce"] == "false" and st["ro"] == "1" and st["srcId"] is not None and st["inert"] == "none", st)
    ok("R1 the article opens clean (drawing it is not an edit)", st["dirty"] is False, st)
    got = pg.evaluate("() => performance.getEntriesByType('resource').filter(e => /\\/Images\\/web\\/flags\\/(us|am)\\.png$/.test(e.name)).map(e => [e.name.replace(/.*flags\\//, ''), e.responseStatus || 200])")
    ok("R1 the two flags are fetched from the repo's Images/web/flags/ and load (200)", sorted(set(map(tuple, got))) == [("am.png", 200), ("us.png", 200)], got)   # (the photo sidebar's album flag is a second am.png)


def t_R2(pg):
    bare0 = pg.evaluate(BARE)
    ok("R2 the block as the file would get it is exactly the file's line", bare0 == SRC, bare0[:200] if bare0 else bare0)
    r = click_block(pg)
    ok("R2 a click selects the block whole and shows its toolbar", r["sel"] and r["tb"] and r["whole"], r)
    pg.keyboard.type("ZZ"); pg.wait_for_timeout(120)
    pg.keyboard.press("Enter"); pg.wait_for_timeout(120)
    pg.keyboard.type("q"); pg.wait_for_timeout(200)
    st = pg.evaluate(STATE); bare = pg.evaluate(BARE)
    ok("R2 typing and Enter with the block selected change nothing (the block stays, no text lands in or over it)", st["n"] == 1 and bare == SRC and not pg.evaluate("document.getElementById('editor').textContent.includes('ZZ')"), (st["n"], bare and bare[:120]))
    pg.evaluate("() => { const e = document.getElementById('editor'); const r = document.createRange(); r.setStart(e.querySelector('.cconv .cconv-fb').firstChild, 2); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }")
    pg.keyboard.type("YY"); pg.wait_for_timeout(200)
    ok("R2 a caret forced inside the block cannot type into it", pg.evaluate(BARE) == SRC and not pg.evaluate("document.getElementById('editor').textContent.includes('YY')"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(100)


def t_R3(pg):
    pg.evaluate(CARET, ["#editor .cconv", 0, "start"])      # a caret just before: the p above is the previous sibling
    pg.evaluate("() => { const c = document.querySelector('#editor .cconv'); const p = c.previousElementSibling; const w = document.createTreeWalker(p, NodeFilter.SHOW_TEXT); let t, last = null; while ((t = w.nextNode())) if (t.nodeValue.trim()) last = t; const r = document.createRange(); r.setStart(last, last.nodeValue.length); r.collapse(true); document.getElementById('editor').focus(); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }")
    p_before = pg.evaluate("document.querySelector('#editor .cconv').previousElementSibling.textContent")
    pg.keyboard.press("Delete"); pg.keyboard.press("Delete"); pg.wait_for_timeout(200)
    ok("R3 Delete at the end of the paragraph above stops at the block", pg.evaluate(STATE)["n"] == 1 and pg.evaluate("document.querySelector('#editor .cconv').previousElementSibling.textContent") == p_before and pg.evaluate(BARE) == SRC,
       pg.evaluate("document.querySelector('#editor .cconv').parentElement.innerHTML.slice(-300)"))
    pg.evaluate(CARET, ["#editor #language .h-a", 0, "start"])
    pg.keyboard.press("Backspace"); pg.wait_for_timeout(150); pg.keyboard.press("Backspace"); pg.wait_for_timeout(200)
    ok("R3 Backspace at the start of the block below (twice) leaves the block and the heading alone", pg.evaluate(STATE)["n"] == 1 and pg.evaluate("document.querySelector('#editor #language .h-a').textContent") == "Currency".replace("Currency", "Language") and pg.evaluate(BARE) == SRC,
       (pg.evaluate("document.querySelector('#editor #language') && document.querySelector('#editor #language').outerHTML.slice(0, 160)"), pg.evaluate(STATE)["n"]))


def t_R4(pg, dialogs):
    click_block(pg)
    dialogs["seen"].clear(); dialogs["accept"] = False
    pg.keyboard.press("Delete"); pg.wait_for_timeout(300)
    ok("R4 Delete with the block selected asks first; Cancel keeps it", len(dialogs["seen"]) == 1 and "currency converter" in dialogs["seen"][0] and pg.evaluate(STATE)["n"] == 1, dialogs["seen"])
    click_block(pg)
    dialogs["seen"].clear(); dialogs["accept"] = True
    pg.keyboard.press("Delete"); pg.wait_for_timeout(300)
    gone = pg.evaluate(STATE)["n"] == 0
    ok("R4 OK removes it and the article is unsaved", len(dialogs["seen"]) == 1 and gone and pg.evaluate("isDirty"), (dialogs["seen"], pg.evaluate(STATE)["n"]))
    pg.keyboard.press("Control+z"); pg.wait_for_timeout(400)
    st = pg.evaluate(STATE)
    ok("R4 Ctrl+Z puts it back, drawn", st["n"] == 1 and st["drawn"] and pg.evaluate(BARE) == SRC, st)
    dialogs["accept"] = False
    # the toolbar's Remove button, cancelled
    click_block(pg); dialogs["seen"].clear()
    pg.click("#ro-remove"); pg.wait_for_timeout(300)
    ok("R4 the toolbar's Remove asks too (cancelled here)", len(dialogs["seen"]) == 1 and pg.evaluate(STATE)["n"] == 1, dialogs["seen"])


def t_R5(pg):
    open_text(pg)
    r = click_block(pg)
    assert r["sel"], r
    mv = pg.evaluate("() => { const b = document.getElementById('ro-move'); const r = b.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }")
    pg.mouse.click(mv[0], mv[1]); pg.wait_for_timeout(250)          # a click, not a drag: sticky click-to-place mode
    tgt = pg.evaluate("() => { const p = document.querySelector('#editor .cconv').previousElementSibling; const r = p.getBoundingClientRect(); return [r.x + r.width / 2, r.y + Math.min(8, r.height / 4)]; }")
    pg.mouse.click(tgt[0], tgt[1]); pg.wait_for_timeout(400)
    pos = pg.evaluate("() => { const c = document.querySelector('#editor .cconv'); return { n: document.querySelectorAll('#editor .cconv').length, nextIsP: !!(c.nextElementSibling && c.nextElementSibling.matches('p.copy')), style: c.getAttribute('style'), drawn: !!c.querySelector(':scope > .cconv-ed'), dirty: isDirty }; }")
    ok("R5 click-to-place moves the block above the paragraph, drawn, with no style attribute left on it", pos["n"] == 1 and pos["nextIsP"] and pos["style"] is None and pos["drawn"] and pos["dirty"], pos)
    ok("R5 the moved block is still exactly the file's line", pg.evaluate(BARE) == SRC)
    out = save(pg, dirty=False)
    ok("R5 the saved file holds the block's line byte-identical, once, now before the paragraph",
       out and out.count(SRC) == 1 and out.index(SRC) < out.index("Make sure to keep small bills on hand") and (NL + "    " + SRC + NL) in out, (out or "")[:0])
    rest = other_diffs(out.replace(LINE + NL, "", 1) if out else out, TEXT.replace(LINE + NL, "", 1))
    ok("R5 nothing else in the file changed (the two date lines aside)", out and not rest, rest[:6])
    ok("R5 no editor residue in the file", out and not re.search(r'cconv-ed|data-chrome|contenteditable|data-ed-|127\.0\.0\.1', out), re.findall(r'.{40}(?:cconv-ed|data-chrome|contenteditable|data-ed-).{40}', out or "")[:3])


def t_R6(pg):
    open_text(pg)
    n0 = pg.evaluate("window.__writes")
    pg.evaluate("window.saveFile()"); pg.wait_for_timeout(700)
    ok("R6 Ctrl+S with nothing dirty writes nothing", pg.evaluate("window.__writes") == n0)
    out = save(pg)
    ok("R6 a dirty save with no edit gives the block back byte-identical, on its own line, indent and line ending kept", out and (LINE + NL) in out and out.count(SRC) == 1, (out or "")[:0])
    rest = other_diffs(out)
    ok("R6 the rest of the file is as it was (the two date lines aside)", out and not rest, rest[:6])
    same_eol = (not re.search(r"[^\r]\n", out)) if NL == "\r\n" else ("\r" not in out)
    ok("R6 the file's line ending (%s) kept, no residue" % ("CRLF" if NL == "\r\n" else "LF"), out and same_eol and not re.search(r'cconv-ed|data-chrome|contenteditable|data-ed-', out))


def t_R7(b):
    find, repl = "neither of us had change.", "neither of us had any change."
    assert TEXT.count(find) == 1
    review = {"slug": "armenia-itinerary", "changes": [{"id": "q1", "kind": "grammar", "find": find, "replace": repl, "why": "test"}]}
    threads = [{"id": "cmX", "author": "Kevin", "text": "beside the converter", "created": "2026-11-08T00:00:00Z", "resolved": False, "replies": [],
                "anchor": {"quote": "overpaying simply because", "before": "a few times I ended up ", "after": " neither of us had change."}}]
    ctx, pg, errs, dialogs = new_page(b, threads=threads, review=review)
    open_text(pg, wait=6000)
    st = pg.evaluate("() => { const s = window.review.state; return { ids: s.changes.map(c => c.id), hidden: s.hidden, mark: !!document.querySelector('#editor del.rl[data-id=\"q1\"], #editor ins.rl[data-id=\"q1\"], #editor [data-id=\"q1\"]'), cm: !!document.querySelector('#editor mark.cm[data-cm=\"cmX\"]'), block: document.querySelectorAll('#editor .cconv').length, drawn: !!document.querySelector('#editor .cconv > .cconv-ed') }; }")
    ok("R7 the round's change in the paragraph beside the block is marked, and the comment next to it anchors", st["ids"] == ["q1"] and not st["hidden"] and st["mark"] and st["cm"] and st["block"] == 1 and st["drawn"], st)
    pg.evaluate("window.review.decide('q1', true)"); pg.wait_for_timeout(300)
    out = save(pg, force=True, wait=2500)
    ok("R7 accepted and saved: the new text is in, the block's line is byte-identical beside it, no marks or residue",
       out and repl in out and find not in out and (LINE + NL) in out and not re.search(r'class="rl|cconv-ed|data-chrome|contenteditable|data-ed-|data-cm=', out),
       ((out or "").count(repl), re.findall(r'.{30}(?:class="rl|cconv-ed|data-chrome|contenteditable|data-ed-|data-cm=).{30}', out or "")[:3]))
    rest = [l for l in other_diffs(out) if find not in l and repl not in l]
    ok("R7 nothing else changed", out and not rest, rest[:6])
    ok("R7 no page errors", not errs, errs[:3])
    ctx.close()


def t_R8(b):
    ctx, pg, errs, dialogs = new_page(b, images=True)
    open_text(pg, wait=5000)
    pg.evaluate("() => { document.querySelector('#editor .cconv').scrollIntoView({ block: 'center' }); }")
    pg.wait_for_timeout(600)
    pg.screenshot(path=str(OUT / "editor-cconv-1440.png"))
    click_block(pg)
    pg.wait_for_timeout(300)
    pg.screenshot(path=str(OUT / "editor-cconv-selected-1440.png"))
    ok("R8 screenshots written", (OUT / "editor-cconv-1440.png").exists() and (OUT / "editor-cconv-selected-1440.png").exists())
    ctx.close()


ONLY = sys.argv[1:]
want = lambda name: not ONLY or any(name.endswith(o) for o in ONLY)
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx, pg, errs, dialogs = new_page(b)
    ok("the editor loads without a script error", not errs, errs[:2])
    for fn in (t_R1, t_R2, t_R3, t_R4, t_R5, t_R6):
        if not want(fn.__name__):
            continue
        try:
            fn(pg, dialogs) if fn is t_R4 else fn(pg)
        except Exception as e:
            ok(fn.__name__ + " ran", False, repr(e)[:400])
    ok("no page errors through the editor checks", not errs, errs[:3])
    ctx.close()
    for fn in (t_R7, t_R8):
        if want(fn.__name__):
            try:
                fn(b)
            except Exception as e:
                ok(fn.__name__ + " ran", False, repr(e)[:400])
    b.close()
bad = [c for c in checks if not c[1]]
print("\n%d checks, %d failed" % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
