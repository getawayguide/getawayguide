"""Shift+Tab on a bullet makes a paragraph, and a save keeps the page's decorative markup
(editor.html keydown handler + normalizeForSave + __applySeo + siteCss.scope). Kevin's Armenia
field-notes saves of 2026-10-31/11-01 showed four breakages, each a check here:

  1. outdenting a top-level bullet left BARE TEXT + <br> in the section beside an empty <ul></ul>
     (no paragraph style, "the font breaks"); outdenting a nested bullet made li > li
  2. the legend's <span class="fn-dot"></span> and a guide card's <span class="dot"></span> were
     deleted as "empty inlines", and the legend's bare <div> boxes were rewritten to <p>
  3. the JSON-LD datePublished moved to today on every save (Armenia, Krushevo and El Salvador all
     took the save day), and the <script> lost its newlines
  4. the <!-- /gb-card --> marker after a guide card was dropped by the pretty printer

plus siteCss.scope(): a rule whose :is()/:where() lists the wrapper class is expanded per branch
(Bitola's guide cards drew unstyled in the editor, 2026-11-01).

Headless, nothing on disk is written: the article text lives in the page (window.__disk) and the
save lands there. The field-notes fixture is the El Salvador draft (as committed in Drafts/, else
the working file) with the markup under test put into a known state first, because a copy on disk
may already carry the damage; the guide fixture is the live Yerevan page.

    python tools/test_editor_outdent_save.py          (needs the photo server on :5003)
"""
import datetime
import pathlib
import re
import subprocess
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
EDITOR = (ROOT / "editor.html").as_uri() + "#editor"
TODAY = datetime.date.today().isoformat()

FN_REL = "Field Notes/el-salvador/field-notes.html"
try:
    FN_HTML = subprocess.run(["git", "-C", str(ROOT / "Drafts"), "show", "HEAD:" + FN_REL],
                             capture_output=True, check=True).stdout.decode("utf-8")
except Exception:
    FN_HTML = (ROOT / "Drafts" / FN_REL).read_text(encoding="utf-8")
GUIDE_HTML = (ROOT / "armenia" / "yerevan.html").read_text(encoding="utf-8")

LEGEND = ('<div class="fn-legend">\n'
          '    <div><b><span class="fn-dot" aria-hidden="true"></span>Bullets and Map pins</b>Tap a place name to open it in Google Maps or the booking site.</div>\n'
          '    <div><b><span class="gl-key" aria-hidden="true">↗</span>Full guide</b>Tap the arrow to read about a place in my full guides.</div>\n'
          '  </div>')


def known_state(html):
    """The fixture's legend, cards and JSON-LD as the launch tools write them."""
    html = re.sub(r'<div class="fn-legend">\s*(?:<(?:div|p)>.*?</(?:div|p)>\s*){2}</div>', LEGEND, html, count=1, flags=re.S)
    html = re.sub(r'(<a class="gb-card".*?</a>)(?:\s*<!-- /gb-card -->)?', r'\1<!-- /gb-card -->', html, flags=re.S)
    html = html.replace('<span class="k">In-depth guide', '<span class="k"><span class="dot"></span>In-depth guide')
    html = re.sub(r'<script type="application/ld\+json">\s*\{', '<script type="application/ld+json">\n{', html)
    html = re.sub(r'\}\s*</script>', '}\n</script>', html)
    html = re.sub(r'"datePublished":\s*"[^"]+"', '"datePublished": "2026-06-20"', html)
    html = re.sub(r'"dateModified":\s*"[^"]+"', '"dateModified": "2026-10-31"', html)
    return html


FN_HTML = known_state(FN_HTML)

# a stub file handle: getFile() returns the text, createWritable() captures the save in window.__disk
OPEN = """async ([text, name]) => { folderHandle = null; window.__disk = text; window.__writes = 0;
  await window.loadFile({ kind: 'file', name,
    getFile: async () => new File([window.__disk], name, { type: 'text/html' }),
    createWritable: async () => ({ write: async h => { window.__disk = h; window.__writes++; }, close: async () => {} }) }); }"""

# put the caret `off` characters into the first text node of the element `sel` matches, and focus
CARET = """([sel, off]) => { const el = document.querySelector(sel); if (!el) return null;
  const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); let t = w.nextNode();
  while (t && !t.nodeValue.trim()) t = w.nextNode(); if (!t) return null;
  document.getElementById('editor').focus();
  const r = document.createRange(); r.setStart(t, Math.min(off, t.nodeValue.length)); r.collapse(true);
  const s = getSelection(); s.removeAllRanges(); s.addRange(r); return t.nodeValue.slice(0, 40); }"""

checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))


def caret(sel, off):
    """A click first: the editor walks the caret through every block for a few seconds after an open
    (spellcheck pass), and the first click or key ends that walk, as it does for a person."""
    pg.click(sel)
    pg.wait_for_timeout(150)
    return pg.evaluate(CARET, [sel, off])


def body_of(html):
    m = re.search(r'<div class="article-body"[^>]*>', html)
    return html[m.end():] if m else html


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(EDITOR)
    pg.wait_for_timeout(1200)
    ok("the editor loads without a script error", not errs, errs[:2])

    # 0. siteCss.scope(): a rule whose :is()/:where() lists the wrapper class is expanded per branch, so the
    #    wrapper folds into #editor (`:is(.fn-section, .article-body) a.gb-card` used to become
    #    `#editor :is(.fn-section, .article-body) a.gb-card`, which never matches: Bitola's cards, 2026-11-01)
    sc = lambda sel, wrap: pg.evaluate("([s, w]) => window.__siteCss.scopeSelector(s, w)", [sel, wrap])
    ok("scope: :is() listing the wrapper expands per branch", sc(":is(.fn-section, .article-body) a.gb-card", "article-body") == "#editor .fn-section a.gb-card, #editor a.gb-card",
       sc(":is(.fn-section, .article-body) a.gb-card", "article-body"))
    ok("scope: :where() too, with the wrapper's own descendants", sc(":where(.artbody, .measure) p > b", "artbody") == "#editor p > b, #editor .measure p > b",
       sc(":where(.artbody, .measure) p > b", "artbody"))
    ok("scope: a selector list with an :is() is split at top-level commas only",
       sc("h2, :is(.article-body, .fn-section) li", "article-body") == "#editor h2, #editor li, #editor .fn-section li", sc("h2, :is(.article-body, .fn-section) li", "article-body"))
    ok("scope: an :is() without the wrapper is left whole", sc(".article-body :is(h2, h3)", "article-body") == "#editor :is(h2, h3)", sc(".article-body :is(h2, h3)", "article-body"))
    ok("scope: the other template's wrapper is not folded", sc(":is(.artbody, .article-body) p", "article-body") == "#editor .artbody p, #editor p",
       sc(":is(.artbody, .article-body) p", "article-body"))
    ok("scope: plain selectors as before", sc(".article-body h3, body.frame[data-list=\"a\"] .v-list", "article-body") == "#editor h3, #editor[data-list=\"a\"] .v-list",
       sc(".article-body h3, body.frame[data-list=\"a\"] .v-list", "article-body"))

    # ---------------- field notes ----------------
    pg.evaluate(OPEN, [FN_HTML, "field-notes.html"])
    pg.wait_for_timeout(3500)
    dots0 = len(re.findall(r'<span class="fn-dot" aria-hidden="true"></span>', FN_HTML))
    card_dots0 = len(re.findall(r'<span class="k"><span class="dot"></span>', FN_HTML))
    markers0 = FN_HTML.count("<!-- /gb-card -->")
    legend0 = len(re.findall(r'<div class="fn-legend">\s*<div>', FN_HTML))
    pub0 = re.search(r'"datePublished":\s*"([^"]+)"', FN_HTML).group(1)
    ok("fixture has the markup under test", dots0 and card_dots0 and markers0 and legend0 and pub0 != TODAY,
       (dots0, card_dots0, markers0, legend0, pub0))

    # 1. Shift+Tab on a top-level bullet of a section -> a <p> where it stood, the list split around it
    SEC_P = """t => { const sec = document.querySelector('#editor .fn-section');
      return [...sec.querySelectorAll(':scope > p')].find(p => p.textContent.startsWith(t)) || null; }"""
    first = caret("#editor .fn-section > ul > li:nth-child(2)", 5)
    n_li0 = pg.evaluate("document.querySelectorAll('#editor .fn-section > ul > li').length")
    pg.keyboard.press("Shift+Tab")
    pg.wait_for_timeout(200)
    shape = pg.evaluate("""t => { const sec = document.querySelector('#editor .fn-section');
      const p = [...sec.querySelectorAll(':scope > p')].find(p => p.textContent.startsWith(t)); if (!p) return { p: null };
      const bare = [...sec.childNodes].filter(n => n.nodeType === 3 && n.nodeValue.trim()).length;
      const s = getSelection(); const anchorInP = p.contains(s.anchorNode);
      return { p: p.textContent.slice(0, 40), prev: p.previousElementSibling && p.previousElementSibling.tagName,
        prevN: p.previousElementSibling ? p.previousElementSibling.children.length : -1,
        next: p.nextElementSibling && p.nextElementSibling.tagName, bare,
        emptyUl: sec.querySelectorAll('ul:empty').length, anchorInP, off: s.anchorOffset,
        li: document.querySelectorAll('#editor .fn-section > ul > li').length }; }""", first)
    ok("Shift+Tab turns the bullet into a <p> in the section", shape["p"] and shape["p"].startswith(first), shape)
    ok("the list splits around it: a <ul> before with one entry, a <ul> after with the rest",
       shape.get("prev") == "UL" and shape.get("prevN") == 1 and shape.get("next") == "UL" and shape.get("li") == n_li0 - 1, shape)
    ok("no bare text and no empty <ul> left in the section", shape.get("bare") == 0 and shape.get("emptyUl") == 0, shape)
    ok("the caret stays where it was in the text", shape.get("anchorInP") and shape.get("off") == 5, shape)
    ok("Shift+Tab marks the article unsaved", pg.evaluate("isDirty"))
    pg.keyboard.press("Control+z")
    pg.wait_for_timeout(300)
    ok("Ctrl+Z puts the bullet back", pg.evaluate("document.querySelectorAll('#editor .fn-section > ul > li').length") == n_li0
       and not pg.evaluate(SEC_P, first))
    # redo it for the save below
    caret("#editor .fn-section > ul > li:nth-child(2)", 5)
    pg.keyboard.press("Shift+Tab")
    pg.wait_for_timeout(200)

    # 1b. the damage an older save left (bare text + <br> + empty <ul>) is repaired by the next save
    pg.evaluate("""() => { const sec = document.querySelectorAll('#editor .fn-section')[1];
      const hd = sec.querySelector('.fn-sub-hd') || sec.querySelector('ul');
      hd.after(document.createTextNode('\\n    BARE-RUN-TEXT with a '), Object.assign(document.createElement('b'), { textContent: 'bold bit' }),
        document.createTextNode(' after'), document.createElement('br'), document.createTextNode('\\n    '), document.createElement('ul'));
      setDirty(true); }""")

    pg.evaluate("saveFile()")
    pg.wait_for_timeout(1500)
    out = pg.evaluate("window.__disk")
    ok("the save was written", pg.evaluate("window.__writes") == 1 and out and out != FN_HTML)
    body = body_of(out)
    ok("the outdented entry is saved as a <p> after its <ul>, before the rest of the list",
       re.search(r'</ul>\s*<p>(?:<b>)?' + re.escape(first[:20]), body) and re.search(r'<p>(?:<b>)?' + re.escape(first[:20]) + r'[^\n]*</p>\s*<ul>', body), first)
    ok("no empty <ul></ul> is saved", not re.search(r'<ul>\s*</ul>', body) and not re.search(r'<ol>\s*</ol>', body))
    ok("bare prose in a section is saved inside a <p>, the trailing <br> dropped",
       re.search(r'<p>BARE-RUN-TEXT with a <b>bold bit</b> after</p>', body) and "BARE-RUN-TEXT with a <b>bold bit</b> after<br>" not in body)
    ok("no text stands bare in a section after the save",
       not re.search(r'\n\s{4}[^<\s][^\n]*\n', body.split('<script')[0]))

    # 2. decorative spans and the legend's boxes survive the save
    ok("the legend keeps its <span class=\"fn-dot\"></span>", body.count('<span class="fn-dot" aria-hidden="true"></span>') == dots0)
    ok("the legend's boxes stay <div>, not <p>", len(re.findall(r'<div class="fn-legend">\s*<div>', body)) == legend0 and
       not re.search(r'<div class="fn-legend">\s*<p>', body))
    ok("guide cards keep their <span class=\"dot\"></span>", body.count('<span class="k"><span class="dot"></span>') == card_dots0)
    ok("guide cards keep their <!-- /gb-card --> marker", body.count("<!-- /gb-card -->") == markers0, (body.count("<!-- /gb-card -->"), markers0))
    ok("the <span class=\"gl-key\"> arrow survives", '<span class="gl-key" aria-hidden="true">↗</span>' in body)

    # 3. the JSON-LD: datePublished untouched, dateModified today, the script keeps its newlines
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', out, re.S).group(1)
    ok("datePublished does not change on a save", re.search(r'"datePublished":\s*"' + re.escape(pub0) + '"', ld), ld[:200])
    ok("dateModified is today", re.search(r'"dateModified":\s*"' + TODAY + '"', ld))
    ok("the JSON-LD script keeps its newline framing", ld.startswith("\n") and ld.endswith("\n"), repr(ld[:12]) + " … " + repr(ld[-8:]))

    # 4. a second save of the same page (nothing changed) writes the same bytes
    pg.evaluate("setDirty(true); saveFile()")
    pg.wait_for_timeout(1500)
    out2 = pg.evaluate("window.__disk")
    ok("a second save is byte-identical (the cleanup is stable)", out2 == out,
       str(len(out2)) + " vs " + str(len(out)))

    # ---------------- a guide page (.article-body) ----------------
    pg.evaluate(OPEN, [GUIDE_HTML, "yerevan.html"])
    pg.wait_for_timeout(3500)
    first = caret("#editor > ul > li:nth-child(2)", 3)
    n_top = pg.evaluate("document.querySelectorAll('#editor > ul').length")
    pg.keyboard.press("Shift+Tab")
    pg.wait_for_timeout(200)
    shape = pg.evaluate("""t => { const ed = document.getElementById('editor');
      const p = [...ed.querySelectorAll(':scope > p')].find(p => p.textContent.startsWith(t));
      const bare = [...ed.childNodes].filter(n => n.nodeType === 3 && n.nodeValue.trim()).length;
      return { p: p && p.textContent.slice(0, 40), prev: p && p.previousElementSibling && p.previousElementSibling.tagName,
        next: p && p.nextElementSibling && p.nextElementSibling.tagName, bare,
        emptyUl: ed.querySelectorAll('ul:empty').length, uls: ed.querySelectorAll(':scope > ul').length }; }""", first)
    ok("article page: Shift+Tab on a top-level bullet makes a <p> between two lists",
       shape["p"] and shape["prev"] == "UL" and shape["next"] == "UL" and shape["uls"] == n_top + 1 and shape["bare"] == 0 and shape["emptyUl"] == 0, shape)
    # a nested bullet goes up one level, into the outer list after its parent entry. (The Yerevan page itself
    # writes some sub-lists as ul > ul, so that count is compared with before, not with zero.)
    ulul0 = pg.evaluate("document.querySelectorAll('#editor ul > ul').length")
    nested = caret("#editor > ul > li > ul > li", 2)
    parent_txt = pg.evaluate("t => { const li = [...document.querySelectorAll('#editor li')].find(l => l.textContent.startsWith(t)); return li.parentElement.parentElement.firstChild.textContent.slice(0, 30); }", nested)
    pg.keyboard.press("Shift+Tab")
    pg.wait_for_timeout(200)
    lvl = pg.evaluate("""([t, pt]) => { const li = [...document.querySelectorAll('#editor li')].find(l => l.textContent.startsWith(t)); if (!li) return null;
      const prev = li.previousElementSibling;
      return { top: li.parentElement.parentElement === document.getElementById('editor'), nest: !!li.parentElement.closest('li'),
        afterParent: !!prev && prev.firstChild.textContent.startsWith(pt), lili: document.querySelectorAll('#editor li > li').length,
        ulul: document.querySelectorAll('#editor ul > ul').length }; }""", [nested, parent_txt])
    ok("a nested bullet outdents into the outer list after its parent entry (never li > li, no new ul > ul)",
       lvl and lvl["top"] and not lvl["nest"] and lvl["afterParent"] and lvl["lili"] == 0 and lvl["ulul"] <= ulul0, lvl)
    pg.evaluate("saveFile()")
    pg.wait_for_timeout(1500)
    body = body_of(pg.evaluate("window.__disk"))
    ok("article page: the entry is saved as a <p> with no empty list", re.search(r'</ul>\s*<p>(?:<b>)?' + re.escape(first[:20]), body) and not re.search(r'<ul>\s*</ul>', body))

    ok("no page errors", not errs, errs[:3])
    b.close()

for name, good, info in checks:
    print(("PASS " if good else "FAIL ") + name + ("" if good or not info else f"  {info}"))
print(f"{sum(1 for c in checks if not c[1])} failed")
sys.exit(1 if any(not c[1] for c in checks) else 0)
