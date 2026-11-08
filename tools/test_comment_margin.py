"""The review margin's inline layout (review.js layout()) on a heavy page: 60 comment threads, 15 of them Shadow
reviewer threads, a cluster of 14 in one section, 5 resolved, 2 whose text is gone, 1 general, plus a round of 3
changes. Everything is synthetic and headless: the page is handed to window.loadFile through a stub handle, the
comments store and the round come from stubbed /review/for and /comments responses, every POST is answered locally,
so nothing on disk is read or written (see memory feedback_test_cleanup_never_in_real_folders).

Kevin, 2026-11-07: "fix comment bar, the in-line dynamic. Comments get stuck when I scroll past the area they are
meant to refer to". The selected card was kept on screen and clamped into the pane, and the clamp shifted every other
card by the same distance; a scroll frame also re-found 80 anchors by selector (~90 ms). What this checks:

  M1  five scroll positions: every card on show has its text on the page, the first card of the stack is level with
      its text (within 8 px), cards never overlap, and they keep document order top to bottom
  M2  a card is never on show while its text is more than a window away; a second layout at the same scroll moves nothing
  M3  selecting a card in the middle of a cluster puts it level with its text and the others make room around it;
      scrolled a window away it leaves the pane (nothing pinned) and the cards in view are level; back, it is level again
  M4  the pane relays out on accept and on reject of a round's changes: the decided card is gone, no overlap, level
  M5  a change of the article's height (an image arriving) moves the cards with the text
  M6  resolved threads are not in the margin; the Shadow reviewer threads are
  M7  under 900 px the margin is a sheet under the article and the cards flow as a scrollable list, no sideways scroll;
      back at 1440 the inline layout returns
  M8  layout() costs under 8 ms median / 16 ms p90 per scroll frame with 60 threads
  M9  the avatar hook: window.avatarFor present -> a 28 px picture (no disc) before the author's name; absent -> the initial

    python tools/test_comment_margin.py          (needs the photo server on :5003, or PHOTO_EDITOR_PORT=<test port>)
Screenshots: .tmp/previews/comment-margin-*.png
"""
import json
import os
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)
API = "http://127.0.0.1:" + os.environ.get("PHOTO_EDITOR_PORT", "5003")
REL = ".tmp/qa/test_margin/page.html"           # a path no real article has; the comments key it makes is stubbed below
SHOTS = ROOT / ".tmp" / "previews"
SHOTS.mkdir(parents=True, exist_ok=True)
checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:700]), flush=True)


# ----------------------------------------------------------------------------- the page and its comments
WORDS = ("harbor statue bridge fortress bazaar canyon monastery kebap market tram river square tower mosque church "
         "museum cable bus ticket hostel balcony terrace coffee bakery vineyard lake ferry trail summit sunrise").split()


def sentence(i, j):
    w = [WORDS[(i * 7 + j * 3 + k) % len(WORDS)] for k in range(6)]
    # the tail names the paragraph too: the six words repeat in another paragraph, and a lost thread is placed by its last 36 characters
    return f"Paragraph {i} sentence {j} walks past the {w[0]} and the {w[1]} before the {w[2]} near the {w[3]} with a {w[4]} by the {w[5]} of paragraph {i}."


SECTIONS = 6
PARAS = 7
paras = {}
body = ['<div class="toc-cols"></div>']
for s in range(SECTIONS):
    body.append(f'<h2 class="article-h2" id="sec{s}">Section {s}: the {WORDS[s]}</h2>')
    for p in range(PARAS):
        i = s * PARAS + p
        paras[i] = [sentence(i, j) for j in range(4)]
        body.append("<p>" + " ".join(paras[i]) + "</p>")
        if p == 3:
            body.append(f'<div class="img-landscape"><img src="../../../Images/web/zz/p{i}.jpg" alt="" width="576" height="384" style="display:block;width:100%;height:auto"></div>')
PAGE = ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>Margin test</title>\n'
        '<link rel="stylesheet" href="../../../styles.css">\n</head>\n<body><main><div class="article-body" style="max-width:825px">\n'
        + "\n".join(body) + '\n</div></main>\n</body></html>\n')


def anchor(i, j):
    q = paras[i][j]
    return {"quote": q, "before": (paras[i][j - 1][-40:] if j else ""), "after": (paras[i][j + 1][:40] if j + 1 < 4 else "")}


threads = []
n = 0


def add(i, j, author="Kevin", resolved=False, prefix="", extra=None):
    global n
    n += 1
    sid = ("sr-m%02d" % n) if author == "Shadow reviewer" else ("cm-m%02d" % n)
    t = {"id": sid, "author": author, "created": "2026-11-07T10:%02d:00.000Z" % (n % 60), "text": prefix + f"comment {n} on paragraph {i}",
         "replies": [], "resolved": resolved, "anchor": anchor(i, j)}
    if extra:
        t.update(extra)
    threads.append(t)
    return sid


# a cluster of 14 in section 2 (paragraphs 14-20), several per paragraph
for i in range(14, 21):
    add(i, 0); add(i, 2, author="Shadow reviewer", prefix="[Shadow reviewer] ")
# the rest spread over the page
for i in (0, 2, 4, 6, 7, 9, 11, 13, 22, 24, 26, 27, 29, 31, 33, 35, 36, 38, 40, 41):
    add(i, 1)
for i in (1, 5, 10, 23, 30, 37, 39):
    add(i, 3, author="Shadow reviewer", prefix="[Shadow reviewer] ")
# five resolved
RESOLVED = [add(i, 2, resolved=True) for i in (3, 8, 12, 25, 34)]
# two whose text is gone (context still there -> beside their old paragraph)
LOST = []
for i in (16, 28):
    sid = add(i, 1); threads[-1]["anchor"]["quote"] = "words that were rewritten and are no longer in the article"; LOST.append(sid)
# one general comment on a section
n += 1
threads.append({"id": "cm-gen", "author": "Kevin", "created": "2026-11-07T11:00:00.000Z", "text": "general comment on section 4", "replies": [], "resolved": False,
                "anchor": {"where": "general", "section": "sec4", "sectionText": "Section 4: the " + WORDS[4]}})
while len([t for t in threads if not t["resolved"]]) < 60:
    add(len(threads) % 42, 0)
OPEN_N = len([t for t in threads if not t["resolved"]])

CHANGES = [{"id": "g%02d" % (k + 1), "kind": "grammar", "section": "Section 3", "find": paras[i][2], "replace": paras[i][2].replace("walks past", "strolls past"), "count": 1, "n": k + 1}
           for k, i in enumerate((21, 32, 19))]       # g03 shares its sentence with a Shadow thread, which then rides inside its card
ROUND = {"changes": CHANGES, "comments": {"threads": threads}, "comments_key": "zz__test_margin", "decided": [], "decisions": {}, "slug": "zz-margin-r1",
         "title": "Margin test", "created": "2026-11-07T09:00:00.000Z"}

# ----------------------------------------------------------------------------- browser helpers
OPEN = """async ([text, name]) => { folderHandle = null; window.__disk = text; window.__writes = 0;
  const bytes = new TextEncoder().encode(text);
  const h = { kind: 'file', name, getFile: async () => new File([bytes], name, { type: 'text/html' }),
    createWritable: async () => ({ write: async d => { window.__disk = d; window.__writes++; }, close: async () => {} }) };
  return await window.loadFile(h); }"""

MEASURE = """() => {
  const scEl = document.querySelector('.editor-scroll'), sc = scEl.getBoundingClientRect();
  const body = document.getElementById('rv-body').getBoundingClientRect(), canvas = document.getElementById('rv-canvas');
  const out = { scrollTop: scEl.scrollTop, scTop: sc.top, scBottom: sc.bottom, paneTop: body.top, paneBottom: body.bottom, active: window.review.state.active,
                flow: canvas.classList.contains('flow'), cards: [] };
  for (const c of canvas.querySelectorAll(':scope > .rv-card')) {
    const id = c.dataset.cm || c.dataset.id || c.dataset.lint;
    let ar = null; const els = c._anc || [];
    for (const el of els) { const r = el.getClientRects()[0]; if (r) { ar = { top: r.top, bottom: r.bottom }; break; } }
    const cr = c.getBoundingClientRect();
    out.cards.push({ id, shown: c.style.display !== 'none', top: cr.top, bottom: cr.bottom, h: cr.height, aTop: ar && ar.top, aBottom: ar && ar.bottom,
                     lost: !!c._ancLost, active: c.classList.contains('active'), draft: c.classList.contains('draft'), pos: getComputedStyle(c).position });
  }
  return out; }"""


def shown(m):
    return [c for c in m["cards"] if c["shown"]]


def on_page(m, c):
    return c["aTop"] is not None and c["aBottom"] >= m["scTop"] and c["aTop"] <= m["scBottom"]


def overlaps(cards):
    cs = sorted([c for c in cards if c["h"] > 0], key=lambda c: c["top"])
    bad = []
    for a, b in zip(cs, cs[1:]):
        if b["top"] < a["bottom"] - 1:
            bad.append((a["id"], b["id"], round(a["bottom"] - b["top"])))
    return bad


def level(m, c):
    """level with its text (card top = text top - 6), or held at the pane's edge because the text's line is above the pane
    or the card would run past the pane bottom (QA 2026-10-08: the card being looked at is never out of sight)"""
    want = c["aTop"] - 6
    if abs(c["top"] - want) <= 8:
        return True
    if want < m["paneTop"] and abs(c["top"] - m["paneTop"]) <= 8:
        return True
    return want + c["h"] > m["paneBottom"] and abs(c["bottom"] - m["paneBottom"]) <= 8


def pivot(m):
    """the card the stack is built from: the active one if on show, else the first in view (by text position)"""
    s = [c for c in shown(m) if not c["lost"]]
    if not s:
        return None
    a = [c for c in s if c["active"]]
    return a[0] if a else min(s, key=lambda c: c["aTop"])


def order_ok(m):
    s = sorted([c for c in shown(m) if not c["lost"] and c["aTop"] is not None], key=lambda c: (c["aTop"], c["id"]))
    tops = [c["top"] for c in s]
    return all(b >= a - 1 for a, b in zip(tops, tops[1:])), [(c["id"], round(c["aTop"]), round(c["top"])) for c in s]


def scroll_to(pg, y, wait=350):
    pg.evaluate("y => { document.querySelector('.editor-scroll').scrollTop = y; }", y)
    pg.wait_for_timeout(wait)


def new_page(b, width=1440, height=900):
    ctx = b.new_context(viewport={"width": width, "height": height}, device_scale_factor=1)
    ctx.add_init_script("try { localStorage.setItem('editorAutosave', 'off'); } catch (e) {}")
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
    pg.on("dialog", lambda d: d.accept())
    posts = []

    def handler(route):
        req = route.request
        u = req.url
        if req.method == "OPTIONS":
            return route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"})
        if "/assets/" in u and req.method == "GET":        # editor.html's assets/ as the file:// editor sees them
            f = ROOT / "assets" / u.split("/assets/", 1)[1].split("?")[0]
            if f.is_file():
                return route.fulfill(status=200, body=f.read_bytes(), headers={"Content-Type": "application/javascript" if f.suffix == ".js" else "image/svg+xml"})
            return route.fulfill(status=404, body=b"")
        if req.resource_type == "image" and "/site/" in u:
            return route.fulfill(status=200, body=b"", headers={"Content-Type": "image/png", "Access-Control-Allow-Origin": "*"})
        if "/comments/" in u and req.method == "GET":
            return route.fulfill(status=200, body=json.dumps({"threads": threads}), headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})
        if "/review/for" in u:
            return route.fulfill(status=200, body=json.dumps(ROUND), headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})
        if req.method == "POST":
            posts.append(u)
            return route.fulfill(status=200, body='{"ok":true,"queued":[],"threads":[],"decisions":{},"kept":[],"warn":[],"why":[]}',
                                 headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})
        return route.continue_()
    pg.route(re.compile(r"^http://127\.0\.0\.1:\d+/.*"), handler)
    pg.goto(API + "/browser", wait_until="domcontentloaded")
    pg.wait_for_timeout(1200)
    pg.evaluate("r => { window.review.state.relOverride = r; }", REL)
    pg.evaluate(OPEN, [PAGE, "page.html"])
    pg.wait_for_timeout(3000)
    pg.evaluate("() => { try { _spellWalkToken++; } catch (e) {} }")
    return ctx, pg, errs, posts


# ----------------------------------------------------------------------------- the checks
def t_M1_M2(pg):
    H = pg.evaluate("document.querySelector('.editor-scroll').scrollHeight")
    vh = pg.evaluate("document.querySelector('.editor-scroll').clientHeight")
    positions = [0, int(H * 0.2), int(H * 0.42), int(H * 0.65), H]
    for k, y in enumerate(positions):
        scroll_to(pg, y)
        m = pg.evaluate(MEASURE)
        s = shown(m)
        off = [c["id"] for c in s if not c["draft"] and not on_page(m, c)]
        ok(f"M1 pos{k} (scrollTop {m['scrollTop']:.0f}): every card on show has its text on the page ({len(s)} on show)", not off, off)
        far = [c["id"] for c in s if c["aTop"] is not None and (c["aBottom"] < m["scTop"] - vh or c["aTop"] > m["scBottom"] + vh)]
        ok(f"M2 pos{k}: no card on show with its text more than a window away", not far, far)
        pv = pivot(m)
        if pv:
            d = pv["top"] - (pv["aTop"] - 6)
            ok(f"M1 pos{k}: the first card is level with its text (off {d:+.0f} px)", level(m, pv), (pv["id"], d, pv["aTop"], m["paneTop"]))
        ov = overlaps(s)
        ok(f"M1 pos{k}: no two cards overlap", not ov, ov)
        o, info = order_ok(m)
        ok(f"M1 pos{k}: cards keep the order of their text", o, info)
        tops1 = [(c["id"], c["top"]) for c in s]
        pg.evaluate("window.review.layout()"); pg.wait_for_timeout(250)
        tops2 = [(c["id"], c["top"]) for c in shown(pg.evaluate(MEASURE))]
        ok(f"M2 pos{k}: a second layout at the same scroll moves nothing", tops1 == tops2, (tops1[:4], tops2[:4]))
        pg.screenshot(path=str(SHOTS / f"comment-margin-pos{k}.png"))


def t_M3(pg):
    # the cluster: section 2 begins around paragraph 14; scroll its heading near the top and pick a middle card
    pg.evaluate("() => document.querySelector('#editor #sec2').scrollIntoView({ block: 'start' })"); pg.wait_for_timeout(400)
    m = pg.evaluate(MEASURE)
    s = sorted([c for c in shown(m) if not c["lost"]], key=lambda c: c["aTop"])
    ok("M3 the cluster shows several cards", len(s) >= 4, len(s))
    mid = s[min(3, len(s) - 1)]
    pg.evaluate("id => { document.querySelector(`#rv-canvas > .rv-card[data-cm=\"${id}\"]`).click(); }", mid["id"]); pg.wait_for_timeout(450)
    m = pg.evaluate(MEASURE)
    me = [c for c in m["cards"] if c["id"] == mid["id"]][0]
    ok("M3 the selected card is active and level with its text", me["active"] and me["shown"] and level(m, me), (me["top"], me["aTop"], me["active"]))
    ok("M3 the others made room (no overlap)", not overlaps(shown(m)), overlaps(shown(m)))
    o, info = order_ok(m)
    ok("M3 order kept around the selected card", o, info)
    pg.screenshot(path=str(SHOTS / "comment-margin-selected.png"))
    # a window away: the selected card leaves, nothing is pinned, the cards in view are level
    vh = pg.evaluate("document.querySelector('.editor-scroll').clientHeight")
    y0 = m["scrollTop"]
    scroll_to(pg, y0 + vh * 2.2)
    m2 = pg.evaluate(MEASURE)
    me2 = [c for c in m2["cards"] if c["id"] == mid["id"]][0]
    ok("M3 scrolled two windows on: the selected card is not on show (nothing rides along)", not me2["shown"], (me2["shown"], me2["top"]))
    ok("M3 scrolled on: no card on show without its text on the page", not [c["id"] for c in shown(m2) if not on_page(m2, c)], [c["id"] for c in shown(m2) if not on_page(m2, c)])
    pv = pivot(m2)
    ok("M3 scrolled on: the first card in view is level with its text", pv is None or level(m2, pv), pv and (pv["id"], pv["top"], pv["aTop"]))
    ok("M3 scrolled on: active card still remembered", m2["active"] == mid["id"], m2["active"])
    pg.screenshot(path=str(SHOTS / "comment-margin-selected-scrolled.png"))
    scroll_to(pg, y0)
    m3 = pg.evaluate(MEASURE)
    me3 = [c for c in m3["cards"] if c["id"] == mid["id"]][0]
    ok("M3 back: the selected card is on show and level again", me3["shown"] and level(m3, me3), (me3["shown"], me3["top"], me3["aTop"]))
    pg.evaluate("() => { window.review.state.active = null; window.review.layout(); }")


def t_M4(pg):
    # the round's changes: g01 (paragraph 21), g02 (32), g03 (19)
    pg.evaluate("() => { window.review.jump('g01'); }"); pg.wait_for_timeout(500)
    m = pg.evaluate(MEASURE)
    g = [c for c in m["cards"] if c["id"] == "g01"]
    ok("M4 the change card g01 is on show and level with its mark", g and g[0]["shown"] and level(m, g[0]), g and (g[0]["shown"], g[0]["top"], g[0]["aTop"]))
    before = len(shown(m))
    pg.evaluate("() => { document.querySelector('#rv-canvas .rv-card[data-id=\"g01\"] .acts .yes').click(); }"); pg.wait_for_timeout(600)
    m = pg.evaluate(MEASURE)
    ok("M4 accept: g01 is gone from the margin and the text has no mark for it", "g01" not in [c["id"] for c in m["cards"]] and pg.evaluate("document.querySelectorAll('#editor [data-id=\"g01\"]').length") == 0, [c["id"] for c in m["cards"] if c["id"].startswith("g")])
    ok("M4 accept: the text reads the replacement", pg.evaluate("document.getElementById('editor').textContent.includes('Paragraph 21 sentence 2 strolls past')"))
    s = shown(m)
    ok("M4 accept: the remaining cards were re-laid (no overlap, every one with text on the page)", not overlaps(s) and not [c["id"] for c in s if not on_page(m, c)], (overlaps(s), [c["id"] for c in s if not on_page(m, c)]))
    pv = pivot(m)
    ok("M4 accept: the first card is level", pv is None or level(m, pv), pv and (pv["id"], pv["top"], pv["aTop"]))
    pg.evaluate("() => { window.review.jump('g02'); }"); pg.wait_for_timeout(500)
    pg.evaluate("() => { document.querySelector('#rv-canvas .rv-card[data-id=\"g02\"] .acts .no').click(); }"); pg.wait_for_timeout(600)
    m = pg.evaluate(MEASURE)
    ok("M4 reject: g02 is gone from the margin and the text keeps the original", "g02" not in [c["id"] for c in m["cards"]] and pg.evaluate("document.getElementById('editor').textContent.includes('Paragraph 32 sentence 2 walks past')"))
    s = shown(m)
    ok("M4 reject: re-laid, no overlap", not overlaps(s), overlaps(s))
    pg.evaluate("() => { window.review.state.active = null; window.review.layout(); }")


def t_M5(pg):
    # the second photo frame at the top of the window, comments on the paragraphs under it in view
    pg.evaluate("() => document.querySelectorAll('#editor .img-landscape')[1].scrollIntoView({ block: 'start' })"); pg.wait_for_timeout(400)
    m1 = pg.evaluate(MEASURE)
    s1 = {c["id"]: c for c in shown(m1) if not c["lost"]}
    ok("M5 cards on show before the height change", len(s1) >= 2, len(s1))
    # the photo arrives taller than its frame: the article grows by 300 px there and the text below moves down
    pg.evaluate("() => { const fr = document.querySelectorAll('#editor .img-landscape')[1]; fr.style.height = (fr.getBoundingClientRect().height + 300) + 'px'; fr.querySelector('img').dispatchEvent(new Event('load')); }")
    pg.wait_for_timeout(500)
    m2 = pg.evaluate(MEASURE)
    s2 = {c["id"]: c for c in shown(m2)}
    moved = [(i, round(s1[i]["aTop"]), round(s2[i]["aTop"]), round(s1[i]["top"]), round(s2[i]["top"])) for i in s1 if i in s2]
    # a card follows its text by the same distance, or is held at the pane's edge when that would take it out of sight
    followed = [x for x in moved if abs(x[2] - x[1]) > 50 and (abs((x[4] - x[3]) - (x[2] - x[1])) <= 8 or level(m2, s2[x[0]]))]
    ok("M5 the cards moved with their text after the article grew (image load / ResizeObserver)", moved and len(followed) >= 1 and not overlaps(shown(m2)), (moved[:5], overlaps(shown(m2))))
    pg.evaluate("() => { document.querySelectorAll('#editor .img-landscape')[1].style.height = ''; }")
    pg.wait_for_timeout(300)


def t_M6(pg):
    ids = pg.evaluate("[...document.querySelectorAll('#rv-canvas > .rv-card')].map(c => c.dataset.cm || c.dataset.id)")
    ok("M6 resolved threads are not in the margin", not [r for r in RESOLVED if r in ids], [r for r in RESOLVED if r in ids])
    nested = pg.evaluate("[...document.querySelectorAll('#rv-canvas .rv-card.nested')].map(c => c.dataset.cm)")
    ok("M6 every open thread has a card (Shadow reviewer ones included; one rides inside the change on its sentence)", len([i for i in ids + nested if i.startswith("sr-")]) == len([t for t in threads if t["id"].startswith("sr-") and not t["resolved"]]) and len([i for i in ids + nested if i.startswith(("cm-", "sr-"))]) == OPEN_N, (len(ids), nested, OPEN_N))
    ok("M6 the two lost threads have a card beside their old paragraph", pg.evaluate("ids => ids.every(([id, para]) => { const c = document.querySelector(`#rv-canvas > .rv-card[data-cm=\"${id}\"]`); return c && c._ancLost && c._anc[0].textContent.includes('Paragraph ' + para + ' '); })", [[LOST[0], 16], [LOST[1], 28]]))
    ok("M6 no 'Send to Claude' on a Shadow reviewer card", pg.evaluate("!document.querySelector('#rv-canvas > .rv-card[data-cm^=\"sr-\"] .acts .claude')"))


def t_M7(pg):
    pg.set_viewport_size({"width": 800, "height": 900}); pg.wait_for_timeout(600)
    m = pg.evaluate(MEASURE)
    geo = pg.evaluate("""() => { const sb = document.querySelector('.sidebar').getBoundingClientRect(), ew = document.querySelector('.editor-wrap').getBoundingClientRect();
      const body = document.getElementById('rv-body'); return { sbTop: sb.top, sbH: sb.height, ewTop: ew.top, ewBottom: ew.bottom, ewW: ew.width, bodyScroll: body.scrollHeight > body.clientHeight, ov: getComputedStyle(body).overflowY,
        sideways: document.documentElement.scrollWidth > document.documentElement.clientWidth || document.querySelector('.workspace').scrollWidth > document.querySelector('.workspace').clientWidth + 1,
        lines: document.querySelectorAll('#rv-lines path').length }; }""")
    ok("M7 800 px: the margin flows as a list (every card static, all on show)", m["flow"] and all(c["pos"] == "static" and c["shown"] for c in m["cards"]), (m["flow"], [(c["id"], c["pos"], c["shown"]) for c in m["cards"] if c["pos"] != "static" or not c["shown"]][:4]))
    ok("M7 800 px: the sheet sits under the article, full width, and scrolls", geo["sbTop"] >= geo["ewBottom"] - 2 and geo["ewW"] > 700 and geo["bodyScroll"] and geo["ov"] in ("auto", "scroll"), geo)
    ok("M7 800 px: no sideways scroll, no connector lines", not geo["sideways"] and geo["lines"] == 0, geo)
    pg.screenshot(path=str(SHOTS / "comment-margin-800.png"))
    pg.set_viewport_size({"width": 393, "height": 851}); pg.wait_for_timeout(600)
    # (the app's tab bar is wider than 393 px on its own; only the sheet and the article column are judged here)
    geo2 = pg.evaluate("""() => ({ sbRight: document.querySelector('.sidebar').getBoundingClientRect().right, wsW: document.querySelector('.workspace').scrollWidth, flow: document.getElementById('rv-canvas').classList.contains('flow'),
      cardW: document.querySelector('#rv-canvas > .rv-card').getBoundingClientRect().width })""")
    ok("M7 393 px: list flow, cards fit the width, the sheet and the article column inside the window", geo2["flow"] and geo2["sbRight"] <= 393 and geo2["wsW"] <= 394 and 300 < geo2["cardW"] <= 393, geo2)
    pg.screenshot(path=str(SHOTS / "comment-margin-393.png"))
    pg.set_viewport_size({"width": 1440, "height": 900}); pg.wait_for_timeout(600)
    m = pg.evaluate(MEASURE)
    ok("M7 back at 1440: inline layout again (absolute cards, only those with text on the page)", not m["flow"] and all(c["pos"] == "absolute" for c in m["cards"]) and 0 < len(shown(m)) < len(m["cards"]), (m["flow"], len(shown(m)), len(m["cards"])))


def t_M8(pg):
    ft = pg.evaluate("""() => { const sc = document.querySelector('.editor-scroll'); const ts = []; let y = 0;
      for (let i = 0; i < 60; i++) { y = (y + 211) % sc.scrollHeight; sc.scrollTop = y; const t0 = performance.now(); window.review.layout(); ts.push(performance.now() - t0); }
      ts.sort((a, b) => a - b); return { median: ts[30], p90: ts[54], max: ts[59], cards: document.querySelectorAll('#rv-canvas > .rv-card').length }; }""")
    ok(f"M8 layout() per scroll frame with {ft['cards']} cards: median {ft['median']:.1f} ms, p90 {ft['p90']:.1f} ms (limits 8 / 16)", ft["median"] < 8 and ft["p90"] < 16, ft)


def t_M9(pg):
    has_real = pg.evaluate("typeof window.avatarFor === 'function'")
    pg.evaluate("""() => { window.__avSaved = window.avatarFor; window.avatarFor = a => { if (!/kevin|claude|shadow/i.test(a || '')) return null;
      const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('viewBox', '0 0 24 24'); s.dataset.au = a; return s; }; window.review.layout(); }""")
    pg.evaluate("() => window.review.setView('contextual')"); pg.wait_for_timeout(400)
    pg.evaluate("() => document.querySelector('#editor #sec2').scrollIntoView({ block: 'start' })"); pg.wait_for_timeout(400)
    av = pg.evaluate("""() => { const k = document.querySelector('#rv-canvas > .rv-card[data-cm^="cm-"]:not([style*="display: none"]) > .who > .av'), s = document.querySelector('#rv-canvas > .rv-card[data-cm^="sr-"]:not([style*="display: none"]) > .who > .av');
      const r = k.getBoundingClientRect(); return { kPic: k.classList.contains('pic') && !!k.querySelector('svg'), sPic: s && s.classList.contains('pic') && !!s.querySelector('svg'), w: r.width, h: r.height,
        round: getComputedStyle(k).borderRadius, beforeName: k.nextElementSibling && k.nextElementSibling.tagName === 'B', text: k.textContent }; }""")
    ok("M9 with window.avatarFor: a 28 px picture with no disc sits before the author's name on Kevin's and the Shadow reviewer's cards", av["kPic"] and av["sPic"] and abs(av["w"] - 28) <= 1 and abs(av["h"] - 28) <= 1 and av["round"] in ("0px", "0", "") and av["beforeName"] and av["text"] == "", av)
    pg.evaluate("() => { window.avatarFor = undefined; window.review.setView('contextual'); }"); pg.wait_for_timeout(400)
    av2 = pg.evaluate("""() => { const k = document.querySelector('#rv-canvas > .rv-card[data-cm^="cm-"]:not([style*="display: none"]) > .who > .av'); return { pic: k.classList.contains('pic'), text: k.textContent, w: k.getBoundingClientRect().width }; }""")
    ok("M9 without it: the initial as before", not av2["pic"] and av2["text"] == "K" and abs(av2["w"] - 22) <= 1, av2)
    pg.evaluate("() => { window.avatarFor = window.__avSaved; window.review.setView('contextual'); }")
    print(f"      (assets/avatars/avatars.js {'is' if has_real else 'is not'} loaded in this build)")


# ----------------------------------------------------------------------------- run
def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx, pg, errs, posts = new_page(b)
        ok("M0 the page opened with no script error and the margin is open", not errs and pg.evaluate("document.body.classList.contains('review-open')"), errs)
        ok("M0 every open thread and change has a card", pg.evaluate("document.querySelectorAll('#rv-canvas .rv-card').length") == OPEN_N + len(CHANGES), pg.evaluate("document.querySelectorAll('#rv-canvas .rv-card').length"))
        ok("M0 assets/avatars/avatars.js is loaded and exposes window.avatarFor", pg.evaluate("typeof window.avatarFor === 'function'"))
        for t in (t_M1_M2, t_M3, t_M4, t_M5, t_M6, t_M7, t_M8, t_M9):
            try:
                t(pg)
            except Exception as e:                       # noqa: BLE001
                ok(t.__name__ + " ran", False, repr(e)[:400])
        ok("M0 no script errors during the run", not errs, errs)
        ok("M0 nothing was written: no save, no file POST", pg.evaluate("window.__writes") == 0 and not [u for u in posts if "/api/import" in u or "/api/pipeline" in u or "/api/upload" in u], [u for u in posts][:5])
        ctx.close(); b.close()
    n_ok = sum(1 for c in checks if c[1])
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
