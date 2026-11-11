"""A tick on a comment applies the round changes its Claude reply names (review.js resolve + namedChanges).
Kevin, 2026-11-08 (North Macedonia itinerary r5): "some of my comments aren't going through even when I check the box".
He ticked the comment card beside each change and the thread closed with nothing applied.

Everything is built here in memory: a short article, a round of changes, the threads. editor.html is opened from
disk and every request to a local server is answered by the stub below, so no server is needed and nothing on disk
is read or written (see memory feedback_test_cleanup_never_in_real_folders).

  T1  ticks accept the changes the reply names: "In round <slug> (g01)", "In round 5 (g02, g03)", "In round 5 (g19 to g21)"
  T2  a change nobody ticked stays undecided, and so does the one on a thread that was left open
  T3  a Shadow reviewer thread's tick closes the thread and applies nothing (there a tick means "no, skip it")
  T4  a change with no marks in the text is not recorded as accepted
  T5  a reply that names another round applies nothing
  T6  the saved article has exactly the accepted edits
  T7  QA 2026-11-10, reply shapes from real stores: a hyphen range "(g09-g12)" accepts all four; "(g13, carried from r4
      g14)" accepts g13 and not this round's g14; "around 5 (g15)" in prose names nothing
  T8  a double-click on a comment's tick closes that comment only (the next card slid under the pointer and closed too)
  T9  a tick whose change can't be applied says so and leaves the comment open; a second tick closes it
  T10 one Ctrl+Z takes back a whole tick: both changes its reply names come back AND the comment (and the second
      comment the same change closed by itself) is open again with its card; Ctrl+Y puts it all back; a tick that
      only closed a comment is undone too; the next Ctrl+Z reaches the step before the tick

    python tools/test_comment_tick.py
    REVIEW_JS=<path> python tools/test_comment_tick.py      runs against that copy of review.js instead (to prove the checks can fail)
"""
import json
import os
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
REVIEW_JS = os.environ.get("REVIEW_JS")
SLUG = "zz-tick-r5"
KEY = "zz__test_tick"
REL = ".tmp/qa/test_tick/page.html"             # a path no real article has
checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:500]), flush=True)


# ----------------------------------------------------------------------------- the article, the round, the threads
# a change's text carries its own id, so one find can never match inside another
DRAWN = ["g01", "g02", "g03", "g04", "g05", "g07", "g08", "g09", "g10", "g11", "g12", "g13", "g14", "g15", "g16", "g17", "g19", "g20", "g21",
         "g22", "g23", "g24", "g25"]
UNMARKED = "g06"                                 # its find is not in the article, so it is never drawn


def old(g):
    return f"old wording {g}"


def new(g):
    return f"new wording {g}"


def note(g):
    return f"This sentence carries the comment for {g} and nothing else."


PAGE = ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>Tick test</title></head>\n'
        '<body><main><div class="article-body">\n<h2 class="article-h2" id="s1">One section</h2>\n'
        + "\n".join(f"<p>Here the {old(g)} sits in the middle of a sentence about the harbor. {note(g)}</p>" for g in DRAWN + [UNMARKED]).replace(old(UNMARKED), "plain text")
        + '\n</div></main>\n</body></html>\n')
CHANGES = [{"id": g, "kind": "grammar", "section": "One section", "find": old(g), "replace": new(g), "count": 1, "note": "synthetic"} for g in DRAWN + [UNMARKED]]


def thread(tid, g, reply, author="Kevin"):
    return {"id": tid, "author": author, "created": "2026-11-08T10:00:00.000Z", "text": "comment " + tid, "resolved": False,
            "anchor": {"before": "", "quote": note(g), "after": ""},
            "replies": [{"id": "r-" + tid, "author": "Claude", "text": reply, "created": "2026-11-08T11:00:00.000Z"}]}


THREADS = [
    thread("t-slug", "g01", f"In round {SLUG} (g01): reworded it."),
    thread("t-five", "g02", "In round 5 (g02, g03): both fixed."),
    thread("t-range", "g19", "In round 5 (g19 to g21): the whole run."),
    thread("t-left", "g04", "In round 5 (g04): fixed, but this comment is never ticked."),
    thread("t-shadow", "g05", "In round 5 (g05): would apply if this were Kevin's thread.", author="Shadow reviewer"),
    thread("t-nomark", UNMARKED, f"In round 5 ({UNMARKED}): named, but not drawn in the text."),
    thread("t-other", "g07", "In round 4 (g07): an earlier round, not this one."),
    thread("t-hyph", "g09", f"Round {SLUG} (g09-g12): Two paragraphs now, about 170 words."),
    thread("t-carry", "g13", f"Round {SLUG} (g13, carried from r4 g14): Revised with your answer."),
    thread("t-around", "g15", "I looked around 5 (g15) places for this one."),
    thread("t-dbl1", "g16", f"Round {SLUG} (g16): fixed."),
    thread("t-dbl2", "g17", f"Round {SLUG} (g17): fixed."),
    # T10 (QA 2026-11-10 #7): one Ctrl+Z takes back a whole tick
    thread("t-undo", "g22", f"Round {SLUG} (g22, g23): both reworded."),
    thread("t-undo-twin", "g23", f"Round {SLUG} (g23): the same change, answered on a second comment."),
    thread("t-plain", "g24", "Good point, left as it is."),
    thread("t-before", "g25", f"Round {SLUG} (g25): fixed."),
]
TICKED = ["t-slug", "t-five", "t-range", "t-shadow", "t-nomark", "t-other", "t-hyph", "t-carry", "t-around"]
ACCEPTED = ["g01", "g02", "g03", "g09", "g10", "g11", "g12", "g13", "g16", "g19", "g20", "g21"]
UNDECIDED = ["g04", "g05", "g07", "g08", "g14", "g15", "g17"]          # g08 is on no thread at all
RES = "id => { const t = window.review.state.comments.threads.find(x => x.id === id); return t ? !!t.resolved : null; }"
TOAST = "() => { const t = document.getElementById('rv-toast'); return t && getComputedStyle(t).display !== 'none' && getComputedStyle(t).visibility !== 'hidden' ? t.textContent : ''; }"
FX = {"slug": SLUG, "comments_key": KEY, "comments": {"threads": THREADS}, "changes": CHANGES, "decisions": {}, "decided": []}
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"}

MARKS = "g => document.querySelectorAll(`#editor [data-id=\"${g}\"]`).length"
STATE = "g => { const v = window.review.state.st[g]; return v === undefined ? 'undecided' : v; }"
TICK = """id => { const b = document.querySelector(`.rv-card[data-cm="${id}"] button[data-a="resolve"]`); if (!b) return false; b.click(); return true; }"""


def main():
    decisions, stores, served, real_calls = [], [], [], []

    def stub(route):
        req, u = route.request, route.request.url
        if req.method == "OPTIONS":
            return route.fulfill(status=204, headers=CORS)
        if "/review/for" in u:
            return route.fulfill(status=200, json=FX, headers=CORS)
        if u.split("?")[0].endswith(f"/review/{SLUG}/decisions"):
            decisions.append(json.loads(req.post_data or "{}"))
            return route.fulfill(status=200, json={"ok": True, "kept": []}, headers=CORS)
        if f"/comments/{KEY}" in u and req.method == "POST":
            stores.append(json.loads(req.post_data or "{}"))
            return route.fulfill(status=200, json={"ok": True, "threads": []}, headers=CORS)
        if f"/comments/{KEY}" in u:
            return route.fulfill(status=200, json=FX["comments"], headers=CORS)
        if req.method == "POST":                     # any other write the page tries: answered here, never reaches a server
            real_calls.append(u)
            return route.fulfill(status=200, json={"ok": True, "threads": [], "kept": []}, headers=CORS)
        return route.fulfill(status=200, json={}, headers=CORS)

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1500, "height": 950}, device_scale_factor=1)
        ctx.add_init_script("try { localStorage.setItem('editorAutosave', 'off'); } catch (e) {}")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
        pg.on("dialog", lambda d: d.accept())
        pg.route(re.compile(r"^http://(127\.0\.0\.1|localhost):\d+/.*"), stub)
        if REVIEW_JS:
            pg.route("**/review.js*", lambda r: (served.append(r.request.url), r.fulfill(path=REVIEW_JS, content_type="application/javascript")))
        pg.goto((ROOT / "editor.html").as_uri())
        pg.wait_for_timeout(800)
        if REVIEW_JS and not served:
            print("the override of review.js was never served, so this run proves nothing")
            return 2
        pg.evaluate("""h => { window.__doc = new DOMParser().parseFromString(h, 'text/html'); window.__ob = window.__doc.querySelector('.article-body');
                              const e = document.getElementById('editor'); e.innerHTML = window.__ob.innerHTML; window.adoptPlaceholders(e); }""", PAGE)
        live = pg.evaluate("document.getElementById('editor').innerHTML")
        pg.evaluate("([r, h]) => window.review.loadFor(r, h)", [REL, live])
        pg.wait_for_timeout(800)

        # the fixture itself: without these the checks below could pass on nothing
        missing = [g for g in DRAWN if not pg.evaluate(MARKS, g)]
        ok("T0 every change but one is drawn in the text", not missing, missing)
        ok(f"T0 {UNMARKED} has no marks in the text", pg.evaluate(MARKS, UNMARKED) == 0 and UNMARKED in pg.evaluate("window.review.state.hidden"), pg.evaluate("window.review.state.hidden"))
        ok("T0 nothing is decided before the ticks", all(pg.evaluate(STATE, g) == "undecided" for g in DRAWN + [UNMARKED]))

        toasts = {}
        for tid in TICKED:
            pg.evaluate("() => { const t = document.getElementById('rv-toast'); if (t) t.textContent = ''; }")
            ok(f"T0 {tid} has a tick and it was clicked", pg.evaluate(TICK, tid))
            pg.wait_for_timeout(300)
            toasts[tid] = pg.evaluate("() => (document.getElementById('rv-toast') || {}).textContent || ''")
        pg.wait_for_timeout(400)

        posted = {}
        for d in decisions:
            posted.update(d)
        last = {t["id"]: t for t in stores[-1]["threads"]} if stores else {}
        for g in [x for x in ACCEPTED if x != "g16"]:          # g16 is ticked by T8's double-click, below
            ok(f"T1 {g}: accepted, marks gone, decision sent", pg.evaluate(STATE, g) is True and pg.evaluate(MARKS, g) == 0 and posted.get(g) is True,
               (pg.evaluate(STATE, g), pg.evaluate(MARKS, g), posted.get(g)))
        for tid in ("t-slug", "t-five", "t-range"):
            ok(f"T1 {tid}: the thread is closed by the tick", last.get(tid, {}).get("resolved") is True and last[tid].get("resolved_by") == "user", last.get(tid))
        for g in ("g04", "g08"):
            ok(f"T2 {g}: not ticked, still undecided with its marks, no decision sent", pg.evaluate(STATE, g) == "undecided" and pg.evaluate(MARKS, g) > 0 and g not in posted,
               (pg.evaluate(STATE, g), pg.evaluate(MARKS, g), posted.get(g)))
        ok("T2 the thread nobody ticked stays open", last.get("t-left", {}).get("resolved") is False, last.get("t-left"))
        ok("T3 g05: a Shadow reviewer tick applies nothing", pg.evaluate(STATE, "g05") == "undecided" and pg.evaluate(MARKS, "g05") > 0 and "g05" not in posted,
           (pg.evaluate(STATE, "g05"), pg.evaluate(MARKS, "g05"), posted.get("g05")))
        ok("T3 the Shadow reviewer thread itself is closed", last.get("t-shadow", {}).get("resolved") is True, last.get("t-shadow"))
        ok(f"T4 {UNMARKED}: no marks in the text, so not recorded as accepted", pg.evaluate(STATE, UNMARKED) == "undecided" and UNMARKED not in posted, (pg.evaluate(STATE, UNMARKED), posted.get(UNMARKED)))
        ok("T5 g07: a reply naming another round applies nothing", pg.evaluate(STATE, "g07") == "undecided" and pg.evaluate(MARKS, "g07") > 0 and "g07" not in posted,
           (pg.evaluate(STATE, "g07"), pg.evaluate(MARKS, "g07"), posted.get("g07")))

        # T7 the reply shapes found in real stores (QA 2026-11-10)
        st = {g: pg.evaluate(STATE, g) for g in ("g09", "g10", "g11", "g12")}
        ok("T7 '(g09-g12)', a hyphen range: all four accepted, not just the ends", all(v is True for v in st.values()), st)
        ok("T7 '(g13, carried from r4 g14)': g13 accepted, this round's g14 left alone", pg.evaluate(STATE, "g13") is True and pg.evaluate(STATE, "g14") == "undecided" and "g14" not in posted,
           (pg.evaluate(STATE, "g13"), pg.evaluate(STATE, "g14")))
        ok("T7 'around 5 (g15)' in a sentence is not 'round 5': nothing accepted", pg.evaluate(STATE, "g15") == "undecided" and "g15" not in posted, pg.evaluate(STATE, "g15"))

        # T9 a tick whose change can't be applied: it says so, and the comment stays open until a second tick
        ok(f"T9 the tick on t-nomark ({UNMARKED} is not in the text) says so and leaves the comment open",
           pg.evaluate(RES, "t-nomark") is False and UNMARKED in toasts.get("t-nomark", "") and "stays open" in toasts.get("t-nomark", ""), (pg.evaluate(RES, "t-nomark"), toasts.get("t-nomark")))
        ok("T9 the ticks that did apply say nothing of the kind", not any("not applied" in toasts.get(t, "") for t in ("t-slug", "t-five", "t-range", "t-hyph", "t-carry")), {t: toasts.get(t) for t in ("t-slug", "t-hyph")})
        pg.evaluate(TICK, "t-nomark"); pg.wait_for_timeout(300)
        ok("T9 a second tick closes it anyway, still without recording the change as accepted", pg.evaluate(RES, "t-nomark") is True and pg.evaluate(STATE, UNMARKED) == "undecided", (pg.evaluate(RES, "t-nomark"), pg.evaluate(STATE, UNMARKED)))

        # T8 a real double-click (two clicks at one spot) on a comment's tick, in List view, where the next card slides up
        pg.click("#rv-v-list"); pg.wait_for_timeout(300)
        box = pg.evaluate("""() => { const b = document.querySelector('.rv-card[data-cm="t-dbl1"] button[data-a="resolve"]'); if (!b) return null; b.scrollIntoView({block: 'center'});
                                     const r = b.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }""")
        pg.wait_for_timeout(200)
        if box:
            pg.mouse.dblclick(box[0], box[1]); pg.wait_for_timeout(500)
        ok("T8 a double-click on one comment's tick closes that comment and applies its change", box and pg.evaluate(RES, "t-dbl1") is True and pg.evaluate(STATE, "g16") is True, (box, pg.evaluate(RES, "t-dbl1"), pg.evaluate(STATE, "g16")))
        ok("T8 ...and not the next one (its change stays undecided)", pg.evaluate(RES, "t-dbl2") is False and pg.evaluate(STATE, "g17") == "undecided", (pg.evaluate(RES, "t-dbl2"), pg.evaluate(STATE, "g17")))
        guarded = pg.evaluate("""() => { const b = document.querySelector('.rv-card[data-cm="t-dbl2"] button[data-a="resolve"]'); if (!b) return null;
                                         b.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 2 })); return true; }""")
        pg.wait_for_timeout(300)
        ok("T8 the second click of a double-click (detail 2) is ignored on its own", guarded and pg.evaluate(RES, "t-dbl2") is False, (guarded, pg.evaluate(RES, "t-dbl2")))
        pg.click("#rv-v-ctx"); pg.wait_for_timeout(300)
        posted = {}
        for d in decisions:
            posted.update(d)

        saved = pg.evaluate("() => assembleDocument(document.getElementById('editor'), window.__doc, window.__ob).newHTML")
        for g in ACCEPTED:
            ok(f"T6 saved article has the new text for {g}", new(g) in saved and old(g) not in saved)
        for g in UNDECIDED:
            ok(f"T6 saved article keeps the original for {g}", old(g) in saved and new(g) not in saved)

        # T10 one Ctrl+Z takes back a whole tick (QA 2026-11-10 #7)
        def key(combo):
            pg.evaluate("() => { const a = document.activeElement; if (a && a !== document.body) a.blur(); }")
            pg.keyboard.press(combo); pg.wait_for_timeout(350)

        def last_store(tid):
            for s in reversed(stores):
                for t in s.get("threads", []):
                    if t["id"] == tid:
                        return t
            return {}
        CARD = "id => !!document.querySelector(`.rv-card[data-cm=\"${id}\"]`)"
        pg.evaluate(TICK, "t-before"); pg.wait_for_timeout(400)
        ok("T10 setup: the earlier tick accepted g25", pg.evaluate(STATE, "g25") is True and pg.evaluate(RES, "t-before") is True)
        depth0 = pg.evaluate("() => window.__History.depth()")
        pg.evaluate(TICK, "t-undo"); pg.wait_for_timeout(500)
        depth1 = pg.evaluate("() => window.__History.depth()")
        ok("T10 the tick accepted g22 and g23 and closed both comments that name them",
           all(pg.evaluate(STATE, g) is True for g in ("g22", "g23")) and pg.evaluate(RES, "t-undo") is True and pg.evaluate(RES, "t-undo-twin") is True,
           [pg.evaluate(STATE, g) for g in ("g22", "g23")] + [pg.evaluate(RES, "t-undo"), pg.evaluate(RES, "t-undo-twin")])
        ok("T10 the tick is ONE step on the undo stack", depth1["idx"] == depth0["idx"] + 1, (depth0, depth1))
        key("Control+z")
        ok("T10 one Ctrl+Z: g22 and g23 are both pending again with their marks",
           all(pg.evaluate(STATE, g) == "undecided" and pg.evaluate(MARKS, g) > 0 for g in ("g22", "g23")),
           [(pg.evaluate(STATE, g), pg.evaluate(MARKS, g)) for g in ("g22", "g23")])
        ok("T10 ...the ticked comment is open again, with its card beside the changes", pg.evaluate(RES, "t-undo") is False and pg.evaluate(CARD, "t-undo"),
           (pg.evaluate(RES, "t-undo"), pg.evaluate(CARD, "t-undo")))
        ok("T10 ...and so is the second comment the same change closed by itself", pg.evaluate(RES, "t-undo-twin") is False and pg.evaluate(CARD, "t-undo-twin"),
           (pg.evaluate(RES, "t-undo-twin"), pg.evaluate(CARD, "t-undo-twin")))
        ok("T10 ...the store was told both are open", last_store("t-undo").get("resolved") is False and last_store("t-undo-twin").get("resolved") is False
           and "resolved_by" not in last_store("t-undo"), (last_store("t-undo"), last_store("t-undo-twin")))
        ok("T10 ...and the earlier tick is untouched", pg.evaluate(STATE, "g25") is True and pg.evaluate(RES, "t-before") is True)
        key("Control+y")
        ok("T10 Ctrl+Y: g22 and g23 accepted again and both comments closed",
           all(pg.evaluate(STATE, g) is True and pg.evaluate(MARKS, g) == 0 for g in ("g22", "g23")) and pg.evaluate(RES, "t-undo") is True and pg.evaluate(RES, "t-undo-twin") is True,
           [pg.evaluate(STATE, g) for g in ("g22", "g23")] + [pg.evaluate(RES, "t-undo"), pg.evaluate(RES, "t-undo-twin")])
        ok("T10 ...closed the way they were (by the tick, and by the round)", last_store("t-undo").get("resolved_by") == "user" and last_store("t-undo-twin").get("resolved_by") == "round",
           (last_store("t-undo").get("resolved_by"), last_store("t-undo-twin").get("resolved_by")))
        key("Control+z"); key("Control+z")
        ok("T10 two Ctrl+Z: the step before the tick (the earlier tick) is undone next, whole",
           pg.evaluate(STATE, "g25") == "undecided" and pg.evaluate(MARKS, "g25") > 0 and pg.evaluate(RES, "t-before") is False
           and pg.evaluate(RES, "t-undo") is False and pg.evaluate(STATE, "g22") == "undecided",
           (pg.evaluate(STATE, "g25"), pg.evaluate(RES, "t-before"), pg.evaluate(RES, "t-undo")))
        pg.evaluate(TICK, "t-plain"); pg.wait_for_timeout(400)
        ok("T10 a tick on a comment that names no change closes it", pg.evaluate(RES, "t-plain") is True)
        key("Control+z")
        ok("T10 ...and Ctrl+Z opens it again, with its card", pg.evaluate(RES, "t-plain") is False and pg.evaluate(CARD, "t-plain") and last_store("t-plain").get("resolved") is False,
           (pg.evaluate(RES, "t-plain"), last_store("t-plain")))
        ok("T10 ...without touching the article or any other decision", pg.evaluate(STATE, "g25") == "undecided" and pg.evaluate(STATE, "g01") is True)
        ok("T6 nothing else was posted anywhere", not real_calls, real_calls[:4])
        ok("T6 no script errors", not errs, errs[:2])
        b.close()
    n_ok = sum(1 for c in checks if c[1])
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
