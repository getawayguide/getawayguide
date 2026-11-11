"""The "I've reviewed Shadow's comments" control (review.js) and tools/shadow_consent.py.
Kevin, 2026-11-10: silence counts on Shadow's price comments only after he says he reviewed the article; opening and
closing it must never count. Link comments go straight through unless he replied, ticked or deleted them.

Scratch only (memory feedback_test_cleanup_never_in_real_folders). The comment store is a temp folder, and redline.COMMENTS
is pointed at it, so the page's POSTs run through the REAL merge (redline.comments_save) on a scratch file. A request for
any other article's comments is refused and fails the run. editor.html is opened from disk; no server is needed.

  C1  the control's states: none without Shadow threads, counts only open ones, sits above the cards
  C2  opening and closing the article records nothing
  C3  a click records when and the exact ids, and adds no thread
  C4  it survives a reload and reaches a tab that was already open
  C5  it survives thread POSTs merging: a stale tab, a save with no deleted list
  C6  a Shadow thread posted after the click is not covered; the control comes back counting only that one
  C7  Undo: one click, survives a reload, a stale tab can't bring it back
  C8  the margin is otherwise unchanged: the click hides nothing, a tick still means no, replies work
  C9  tools/kevin_reviewer_corpus.py does not count it as a comment
  C10 (QA 2026-11-10) any earlier click can be undone from the "All N" list, not only the newest; shadow_consent.py
      reads the same; the strip's text can't be selected by a double-click
  C11 (QA 2026-11-10 #12) the store's 300-entry cap on deleted ids never drops a click or an Undo
  S*  every condition in tools/shadow_consent.py, plain and --json, read-only

    python tools/test_shadow_consent.py
    REVIEW_JS=<path> python tools/test_shadow_consent.py       runs against that copy of review.js (to prove the checks can fail)
    SHADOW_CONSENT_SHOTS=<dir> python tools/test_shadow_consent.py       also saves the three states as desktop screenshots
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import kevin_reviewer_corpus as corpus     # noqa: E402
import redline                             # noqa: E402
import shadow_consent as sc                # noqa: E402

REVIEW_JS = os.environ.get("REVIEW_JS")
SHOTS = os.environ.get("SHADOW_CONSENT_SHOTS")
KEY = "zz__test_shadow_consent"
REL = ".tmp/qa/test_shadow_consent/page.html"     # a path no real article has
SHADOW = "Shadow reviewer"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="shadow_consent_test_"))
STORE = TMP / "_comments"
STORE.mkdir()
checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:500]), flush=True)


def note(tid):
    return f"This sentence carries the comment for {tid} and nothing else."


def th(tid, cat=None, author=SHADOW, resolved=False, replies=()):
    t = {"id": tid, "author": author, "created": "2026-11-09T10:00:00.000Z", "text": f"[Shadow reviewer] about {tid} (confidence 0.50)",
         "resolved": resolved, "kind": None, "anchor": {"before": "", "quote": note(tid), "after": ""}, "replies": list(replies)}
    if author == SHADOW and cat is not None:
        t["shadow"] = {"pred": "k01", "category": cat, "confidence": 0.5}
    return t


def rp(author, text="yes"):
    return {"id": "r-" + author + text, "author": author, "text": text, "created": "2026-11-09T11:00:00.000Z"}


LATER = "sr-new00001"
FIXTURE = [th("sr-link0001", "link: maps"), th("sr2-price001", "fill: price"), th("sr-word0001", "wording"),
           th("sr-done0001", "link", resolved=True), th("k-kevin0001", author="Kevin")]
OPEN_SHADOW = ["sr-link0001", "sr2-price001", "sr-word0001"]
ALL_IDS = [t["id"] for t in FIXTURE] + [LATER]
PAGE = ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>Shadow consent test</title></head>\n'
        '<body><main><div class="article-body">\n<h2 class="article-h2" id="s1">One section</h2>\n'
        + "\n".join(f"<p>{note(i)}</p>" for i in ALL_IDS) + '\n</div></main>\n</body></html>\n')
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"}
FILE = STORE / (KEY + ".json")


def write_store(threads, deleted=()):
    FILE.write_text(json.dumps({"threads": threads, "deleted": list(deleted)}, indent=1, ensure_ascii=False), encoding="utf-8")


def on_disk():
    return json.loads(FILE.read_text(encoding="utf-8"))


def records(store=None):
    return [json.loads(x[len(sc.SC_ON):]) for x in (store or on_disk())["deleted"] if x.startswith(sc.SC_ON)]


def ids(group):
    return sorted(t["id"] for t in group)


def consent():
    return sc.report(KEY, STORE)


# ------------------------------------------------------------------------------------------------- shadow_consent.py
def click(*tids, at="2026-11-10T09:00:00.000Z", cid="sc-1"):
    return sc.SC_ON + json.dumps({"id": cid, "at": at, "threads": list(tids)})


def undo(cid="sc-1"):
    return sc.SC_OFF + json.dumps({"id": cid, "at": "2026-11-10T10:00:00.000Z"})


def groups(threads, deleted=()):
    _, links, prices = sc.qualifying({"threads": threads, "deleted": list(deleted)})
    return ids(links), ids(prices)


def test_consent_rules():
    for cat in ("link", "link: maps", "link: own article", "link: wikipedia person (round-added)", "Link: Maps"):
        ok(f"S1 category {cat!r}: a link kind, goes through with no click", groups([th("a", cat)]) == (["a"], []), groups([th("a", cat)]))
    for cat in ("currency", "fill: currency", "fill: price"):
        ok(f"S2 category {cat!r}: a price kind, waits for the click", groups([th("a", cat)]) == ([], []), groups([th("a", cat)]))
        ok(f"S2 category {cat!r}: qualifies once the click covers it", groups([th("a", cat)], [click("a")]) == ([], ["a"]), groups([th("a", cat)], [click("a")]))
    for cat in ("wording", "wording: weak word", "repetition", "repetition: same verdict", "fact", "fact check", "fact check: price", "expand: thin entry", "expand: how to get there",
                "link / lookup: unnamed place", "link/expand: unnamed object", "fill: practical detail", "fill: unnamed place lookup", "fill: confirm place", "lookup: unnamed place he visited", "media: route map", "map", "structure", "title", "content", "", None):
        ok(f"S3 category {cat!r} never qualifies, even when confirmed", groups([th("a", cat)], [click("a")]) == ([], []), groups([th("a", cat)], [click("a")]))
    for label, cat, want in (("link", "link: maps", (["a"], [])), ("price", "fill: price", ([], ["a"]))):
        base = [click("a")]
        ok(f"S4 {label}: the plain case qualifies", groups([th("a", cat)], base) == want, groups([th("a", cat)], base))
        ok(f"S4 {label}: resolved is out", groups([th("a", cat, resolved=True)], base) == ([], []))
        ok(f"S4 {label}: deleted is out", groups([th("a", cat)], base + ["a"]) == ([], []))
        ok(f"S4 {label}: a reply from Kevin is out", groups([th("a", cat, replies=[rp("Kevin", "no")])], base) == ([], []))
        ok(f"S4 {label}: a 'yes' from Kevin is out too (a typed answer is not silence)", groups([th("a", cat, replies=[rp("Kevin", "yes")])], base) == ([], []))
        ok(f"S4 {label}: not a Shadow thread is out", groups([dict(th("a", cat), author="Kevin")], base) == ([], []))
        claude = groups([th("a", cat, replies=[rp("Claude", "in round 3")])], base)
        ok(f"S4 {label}: a reply from Claude alone does not disqualify", claude == want, claude)
    price = th("a", "fill: price")
    ok("S5 price: id not in the confirmation is out", groups([price], [click("b")]) == ([], []))
    ok("S5 price: an undone confirmation is out", groups([price], [click("a"), undo()]) == ([], []))
    ok("S5 price: Undo before its record in the log still cancels it", groups([price], [undo(), click("a")]) == ([], []))
    ok("S5 price: undoing one click leaves another", groups([price], [click("a"), click("a", cid="sc-2"), undo()]) == ([], ["a"]))
    ok("S5 price: a mangled record is no confirmation", groups([price], [sc.SC_ON + "{not json", sc.SC_ON + '{"id": "x"}', sc.SC_ON + "[]"]) == ([], []))
    ok("S5 price: no shadow field is out", groups([th("a")], [click("a")]) == ([], []))
    ok("S5 an undone click does not hold back a link", groups([th("a", "link")], [click("a"), undo()]) == (["a"], []))
    ok("S5 the sr2- ids of a second pass count like sr- ones", groups([th("sr2-abc", "fill: price")], [click("sr2-abc")]) == ([], ["sr2-abc"]))
    two = {"threads": [th("a", "fill: price"), th("b", "fill: price")], "deleted": [click("a"), click("b", cid="sc-2", at="2026-11-10T12:00:00.000Z")]}
    ok("S5b undoing the OLDER of two clicks keeps the newer one's thread", groups(two["threads"], two["deleted"] + [undo("sc-1")]) == ([], ["b"]))
    f = sc.in_force({"threads": two["threads"] + [th("c", "fill: price", resolved=True)], "deleted": two["deleted"] + [click("c", cid="sc-3"), undo("sc-1")]})
    ok("S5b in_force lists the clicks still standing, oldest first, with their open threads", [(x["id"], x["open"]) for x in f] == [("sc-2", ["b"]), ("sc-3", [])], f)

    # the command line, on a scratch store
    d = TMP / "cli"
    d.mkdir()
    store = {"threads": [th("sr-l", "link: maps"), th("sr-p", "fill: price"), th("sr-w", "wording")], "deleted": []}
    store["threads"][0]["text"] = "[Shadow reviewer] link the café — Naïve (confidence 0.60)"
    path = d / "zz__cli.json"
    path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")

    def cli(*a):
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "shadow_consent.py"), *a, "--comments-dir", str(d)], capture_output=True)
        return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")

    before = (path.read_bytes(), path.stat().st_mtime_ns, sorted(p.name for p in d.iterdir()))
    rc, out, _ = cli("zz__cli")
    ok("S6 no click: exit 0, says so", rc == 0 and "No confirmation recorded" in out and "price comments" in out, out)
    ok("S6 no click: the link is listed and the price is not", "sr-l" in out and "sr-p" not in out, out)
    ok("S6 plain output is UTF-8 through the buffer", "café — Naïve" in out, repr(out[:300]))
    ok("S6 the two groups are headed separately", "Links, no confirmation needed: 1" in out and "Prices, confirmed by his click: 0" in out, out)
    rc, out, _ = cli("zz__cli", "--json")
    j = json.loads(out)
    ok("S6 --json, no click: links only, confirmed false, a note", rc == 0 and j["confirmed"] is False and ids(j["links"]) == ["sr-l"] and j["prices"] == [] and j["note"], j)
    store["deleted"].append(click("sr-l", "sr-p"))
    path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    before = (path.read_bytes(), path.stat().st_mtime_ns, sorted(p.name for p in d.iterdir()))
    rc, out, _ = cli("zz__cli", "--json")
    j = json.loads(out)
    ok("S6 --json, clicked: both groups, the time and the covered ids", ids(j["links"]) == ["sr-l"] and ids(j["prices"]) == ["sr-p"] and j["confirmed"] is True
       and j["reviewed_at"] == "2026-11-10T09:00:00.000Z" and j["covered_ids"] == ["sr-l", "sr-p"], j)
    rc, out, _ = cli("zz__cli")
    ok("S6 plain, clicked: both ids and the date", "sr-l" in out and "sr-p" in out and "sr-w" not in out and "reviewed 2026-11-10" in out, out)
    ok("S7 read-only: the store is unchanged and nothing was added", (path.read_bytes(), path.stat().st_mtime_ns, sorted(p.name for p in d.iterdir())) == before)
    # QA 2026-11-10 #13: a click at 5:30 pm Pacific is the next day in UTC; the CLI prints the margin's (local) day
    late_at = "2026-11-11T01:30:00.000Z"
    want_day = datetime.fromisoformat(late_at.replace("Z", "+00:00")).astimezone().date().isoformat()
    store["deleted"] = [click("sr-l", "sr-p", at=late_at)]
    path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    rc, out, _ = cli("zz__cli")
    ok("S7b plain output prints the LOCAL day of the click, as the margin does (%s here, not the UTC 2026-11-11)" % want_day,
       rc == 0 and ("reviewed %s " % want_day) in out and (want_day == "2026-11-11" or "2026-11-11" not in out)
       and sc.local_day("2026-11-10T09:00:00.000Z") == datetime(2026, 11, 10, 9, tzinfo=timezone.utc).astimezone().date().isoformat()
       and sc.local_day("garbage") == "garbage"[:10], out)
    store["deleted"] = [click("sr-l"), click("sr-p", cid="sc-2", at="2026-11-10T12:00:00.000Z")]
    path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    rc, out, _ = cli("zz__cli")
    ok("S7c plain output lists each click in force when there are several", rc == 0 and "click sc-1 on " in out and "click sc-2 on " in out and "1 still open" in out, out)
    store["deleted"].append(undo("sc-1"))
    path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    rc, out, _ = cli("zz__cli", "--json")
    j = json.loads(out)
    ok("S7c --json after undoing the older click: only the newer is in force, its price still qualifies",
       [x["id"] for x in j["confirmations"]] == ["sc-2"] and ids(j["prices"]) == ["sr-p"] and j["covered_ids"] == ["sr-p"], j)
    rc, out, _ = cli("zz__missing")
    ok("S8 no store: exit 0, says so, lists nothing", rc == 0 and "No comment store" in out and "sr-" not in out, out)
    (d / "zz__torn.json").write_text("{not json", encoding="utf-8")
    rc, out, err = cli("zz__torn", "--json")
    ok("S8 a torn store is an error, not an empty list", rc == 2 and not out and "shadow_consent" in err, (rc, out, err))
    rc, out, err = cli("../secrets")
    ok("S8 a key that is a path is refused", rc == 2 and "not an article key" in err, (rc, out, err))


# ------------------------------------------------------------------------------------------------------- the margin
TICK = """id => { const b = document.querySelector(`.rv-card[data-cm="${id}"] button[data-a="resolve"]`); if (!b) return false; b.click(); return true; }"""
REPLY = """([id, text]) => { const c = document.querySelector(`.rv-card[data-cm="${id}"]`); if (!c) return false; c.querySelector('.reply textarea').value = text; c.querySelector('[data-a="reply"]').click(); return true; }"""
STRIP = """() => { const el = document.getElementById('rv-shadow'), b = document.getElementById('rv-shadow-ok'), u = document.getElementById('rv-shadow-undo');
                   return {text: el ? el.textContent.trim() : null, shown: !!el && getComputedStyle(el).display !== 'none', button: b ? b.textContent.trim() : null, undo: !!u}; }"""


def main():
    if redline.COMMENTS == STORE:
        raise SystemExit("run this once per process")
    real = redline.COMMENTS
    redline.COMMENTS = STORE                      # the page's POSTs below reach only this folder
    if pathlib.Path(redline.COMMENTS).resolve() == pathlib.Path(real).resolve() or str(STORE.resolve()).startswith(str(ROOT.resolve())):
        raise SystemExit("not a scratch store, refusing to run")
    posts, violations, served = [], [], []

    def stub(route):
        req, u = route.request, route.request.url
        if req.method == "OPTIONS":
            return route.fulfill(status=204, headers=CORS)
        if "/review/for" in u:
            return route.fulfill(status=200, json={"slug": None, "comments_key": KEY, "comments": redline.comments_load(KEY), "changes": [], "decisions": {}, "decided": []}, headers=CORS)
        m = re.search(r"/comments/([^/?]+)", u)
        if m and m.group(1) != KEY:                # nothing here may ever reach a real article's comments
            violations.append((req.method, u))
            return route.fulfill(status=403, json={"ok": False}, headers=CORS)
        if m and req.method == "POST":
            posts.append(json.loads(req.post_data or "{}"))
            saved = redline.comments_save(KEY, posts[-1])
            return route.fulfill(status=200, json={"ok": True, "threads": saved.get("threads", [])}, headers=CORS)
        if m:
            return route.fulfill(status=200, json=redline.comments_load(KEY), headers=CORS)
        if req.method == "POST":
            violations.append((req.method, u))
            return route.fulfill(status=200, json={"ok": True, "threads": [], "kept": []}, headers=CORS)
        return route.fulfill(status=200, json={}, headers=CORS)

    errs = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1500, "height": 950}, device_scale_factor=1)
        ctx.add_init_script("try { localStorage.setItem('editorAutosave', 'off'); } catch (e) {}")
        ctx.route(re.compile(r"^http://(127\.0\.0\.1|localhost):\d+/.*"), stub)
        if REVIEW_JS:
            ctx.route("**/review.js*", lambda r: (served.append(r.request.url), r.fulfill(path=REVIEW_JS, content_type="application/javascript")))

        def open_tab():
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
            pg.on("dialog", lambda d: d.accept())
            pg.goto((ROOT / "editor.html").as_uri())
            pg.wait_for_timeout(700)
            pg.evaluate("""h => { window.__doc = new DOMParser().parseFromString(h, 'text/html'); window.__ob = window.__doc.querySelector('.article-body');
                                  const e = document.getElementById('editor'); e.innerHTML = window.__ob.innerHTML; window.adoptPlaceholders(e); }""", PAGE)
            live = pg.evaluate("document.getElementById('editor').innerHTML")
            pg.evaluate("([r, h]) => window.review.loadFor(r, h)", [REL, live])
            pg.wait_for_timeout(700)
            return pg

        def reopen(pg):
            pg.close()
            return open_tab()

        def shot(pg, name):
            if SHOTS:
                os.makedirs(SHOTS, exist_ok=True)
                pg.screenshot(path=os.path.join(SHOTS, name + ".png"))

        # C1: an article with no Shadow threads has no control at all
        write_store([th("k-kevin0001", author="Kevin")])
        tab = open_tab()
        s = tab.evaluate(STRIP)
        ok("C1 no Shadow threads: nothing is shown", s["text"] == "" and not s["shown"] and not s["button"], s)
        tab.close()

        # C1: the real fixture
        write_store(FIXTURE)
        before = FILE.read_bytes()
        tab = open_tab()
        if REVIEW_JS and not served:
            print("the override of review.js was never served, so this run proves nothing")
            return 2
        stale = open_tab()                        # open since before the click; used as the stale tab below
        s = tab.evaluate(STRIP)
        ok("C1 counts only open Shadow threads (not resolved, not his own)", s["button"] == f"I've reviewed Shadow's comments ({len(OPEN_SHADOW)})", s)
        ok("C1 no reviewed state yet, no Undo", not s["undo"] and "reviewed " not in (s["text"] or "").replace("I've reviewed", ""), s)
        place = tab.evaluate("""() => { const b = document.getElementById('rv-shadow-ok'), body = document.getElementById('rv-body'); if (!b) return null;
                                         const r = b.getBoundingClientRect(), p = document.getElementById('review-pane').getBoundingClientRect();
                                         return {above: r.bottom <= body.getBoundingClientRect().top, inside: r.left >= p.left && r.right <= p.right, cls: b.className, h: r.height}; }""")
        ok("C1 sits above the cards, inside the pane, styled like the pane's other buttons", place and place["above"] and place["inside"] and place["cls"] == "rv-btn" and place["h"] > 10, place)
        shot(tab, "1-control")

        # C2: opening, closing and reopening record nothing
        tab.evaluate("window.review.toggle(false)"); tab.wait_for_timeout(200)
        tab.evaluate("window.review.toggle(true)"); tab.wait_for_timeout(200)
        tab2 = open_tab(); tab2.wait_for_timeout(300); tab2.close()
        ok("C2 opening and closing wrote nothing", not posts and FILE.read_bytes() == before, len(posts))
        ok("C2 no confirmation came of it", consent()["confirmed"] is False and consent()["prices"] == [], consent())
        ok("C2 the link already goes through, the price does not", ids(consent()["links"]) == ["sr-link0001"], consent())

        # C3: the click
        n_threads = len(on_disk()["threads"])
        snap = {t["id"]: (t["resolved"], t["text"]) for t in on_disk()["threads"]}
        clicked = tab.evaluate("() => { const b = document.getElementById('rv-shadow-ok'); if (!b) return false; b.click(); return true; }")
        tab.wait_for_timeout(300)
        ok("C3 the control was there to click", clicked)
        rec = (records() or [{}])[-1] if clicked and FILE.exists() else {}
        ok("C3 records exactly the open Shadow ids", sorted(rec.get("threads", [])) == sorted(OPEN_SHADOW), rec)
        at = None
        try:
            at = datetime.fromisoformat(rec["at"].replace("Z", "+00:00")).astimezone()
        except Exception:
            pass
        ok("C3 records when", at is not None and abs((datetime.now().astimezone() - at).total_seconds()) < 120, rec.get("at"))
        ok("C3 adds no thread and changes none", len(on_disk()["threads"]) == n_threads and {t["id"]: (t["resolved"], t["text"]) for t in on_disk()["threads"]} == snap)
        day = f"{at:%b} {at.day}" if at else "?"
        s = tab.evaluate(STRIP)
        ok(f"C3 quiet state: 'Shadow comments reviewed {day}' with Undo, no button", (s["text"] or "").startswith(f"Shadow comments reviewed {day}") and s["undo"] and not s["button"], s)
        shot(tab, "2-reviewed")
        c = consent()
        ok("C3 now the price qualifies, the wording never does", ids(c["prices"]) == ["sr2-price001"] and ids(c["links"]) == ["sr-link0001"] and c["confirmed"], c)

        # C4: a reload, and the tab that was already open
        tab = reopen(tab)
        s = tab.evaluate(STRIP)
        ok("C4 reload: still reviewed, Undo there, no button", (s["text"] or "").startswith("Shadow comments reviewed") and s["undo"] and not s["button"], s)
        tab.evaluate("window.review.pollTasks(true)"); stale_after = None
        stale.evaluate("window.review.pollTasks(true)"); stale.wait_for_timeout(300)
        stale_after = stale.evaluate(STRIP)
        ok("C4 a tab already open picks it up on its poll", (stale_after["text"] or "").startswith("Shadow comments reviewed") and not stale_after["button"], stale_after)

        # C5: normal POSTs merging (a tab that never heard of the click, a save that sends no deleted list)
        recs_before = records()
        redline.comments_save(KEY, {"threads": FIXTURE, "deleted": []})
        ok("C5 a stale tab's POST with an empty deleted list keeps it", records() == recs_before, on_disk()["deleted"])
        redline.comments_save(KEY, {"threads": FIXTURE})
        ok("C5 a POST with no deleted list keeps it", records() == recs_before, on_disk()["deleted"])
        tab.evaluate(REPLY, ["k-kevin0001", "a normal reply through the margin"]); tab.wait_for_timeout(300)
        ok("C5 a real margin save (a reply) keeps it", records() == recs_before and len(on_disk()["threads"]) == n_threads, on_disk()["deleted"])

        # C6: a Shadow thread posted later (post_margin.py's kind of POST: threads + the store's deleted)
        cur = on_disk()
        cur["threads"].append(th(LATER, "fill: currency"))
        redline.comments_save(KEY, {"threads": cur["threads"], "deleted": cur["deleted"]})
        tab = reopen(tab)
        s = tab.evaluate(STRIP)
        ok("C6 the control comes back counting only the new thread", s["button"] == "I've reviewed Shadow's comments (1)", s)
        ok("C6 the earlier review still shows, with its Undo", "Earlier ones reviewed" in (s["text"] or "") and s["undo"], s)
        ok("C6 the new thread is not covered, and does not qualify", LATER not in sum((r["threads"] for r in records()), []) and LATER not in ids(consent()["prices"]), consent())
        shot(tab, "3-new-thread")
        tab.evaluate("document.getElementById('rv-shadow-ok').click()"); tab.wait_for_timeout(300)
        r2 = records()
        ok("C6 the second click covers exactly the new thread", len(r2) == 2 and r2[-1]["threads"] == [LATER], r2)
        ok("C6 and now it qualifies", ids(consent()["prices"]) == sorted(["sr2-price001", LATER]), consent())

        # C7: Undo
        tab.evaluate("document.getElementById('rv-shadow-undo').click()"); tab.wait_for_timeout(300)
        s = tab.evaluate(STRIP)
        ok("C7 Undo takes back the newest: its thread is unconfirmed again", s["button"] == "I've reviewed Shadow's comments (1)" and LATER not in ids(consent()["prices"]) and ids(consent()["prices"]) == ["sr2-price001"], (s, consent()))
        old_log = on_disk()["deleted"]
        redline.comments_save(KEY, {"threads": on_disk()["threads"], "deleted": [x for x in old_log if not x.startswith(sc.SC_OFF)]})
        ok("C7 a stale tab that never saw the Undo can't bring it back", LATER not in ids(consent()["prices"]), consent())
        tab = reopen(tab)
        s = tab.evaluate(STRIP)
        ok("C7 survives a reload", s["button"] == "I've reviewed Shadow's comments (1)", s)
        tab.evaluate("document.getElementById('rv-shadow-undo').click()"); tab.wait_for_timeout(300)
        s = tab.evaluate(STRIP)
        c = consent()
        ok("C7 undoing the first too: all open Shadow threads count again, no reviewed state", s["button"] == f"I've reviewed Shadow's comments ({len(OPEN_SHADOW) + 1})" and not s["undo"], s)
        ok("C7 with none left, prices are out and the store says unconfirmed", c["confirmed"] is False and c["prices"] == [] and "No confirmation recorded" in c["note"], c)
        ok("C7 links are not affected by Undo", ids(c["links"]) == ["sr-link0001"], c)

        # C10 any earlier click can be undone, not only the newest (QA 2026-11-10): two passes, two clicks in force
        older, newer = "sc-c10-older", "sc-c10-newer"
        older_at, newer_at = "2026-11-09T17:00:00.000Z", "2026-11-10T18:30:00.000Z"
        cur = on_disk()
        redline.comments_save(KEY, {"threads": cur["threads"], "deleted": cur["deleted"] + [click(*OPEN_SHADOW, at=older_at, cid=older), click(LATER, at=newer_at, cid=newer)]})
        tab = reopen(tab)
        s = tab.evaluate(STRIP)
        nday = datetime.fromisoformat(newer_at.replace("Z", "+00:00")).astimezone()
        ok("C10 two clicks in force: the line shows the newest, its Undo, and an 'All 2' toggle, the list shut",
           (s["text"] or "").startswith(f"Shadow comments reviewed {nday:%b} {nday.day}") and s["undo"] and not s["button"]
           and tab.evaluate("() => { const a = document.getElementById('rv-shadow-all'); return a ? [a.textContent.trim(), a.getAttribute('aria-expanded')] : null; }") == ["All 2", "false"]
           and not tab.evaluate("!!document.getElementById('rv-shadow-list')"), s)
        box = tab.evaluate("() => { const r = document.getElementById('rv-shadow-all').getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }")
        tab.mouse.dblclick(box[0], box[1]); tab.wait_for_timeout(250)
        rows = tab.evaluate("() => [...document.querySelectorAll('#rv-shadow-list li')].map(li => [li.dataset.sc, li.textContent.trim(), !!li.querySelector('button[data-sc]')])")
        ok("C10 a double-click on 'All 2' opens the list once (not open-then-shut)", len(rows) == 2, rows)
        ok("C10 the list has both clicks, newest first, each with its count and its own Undo",
           [r[0] for r in rows] == [newer, older] and all(r[2] for r in rows) and "1 comment" in rows[0][1] and f"{len(OPEN_SHADOW)} comments" in rows[1][1], rows)
        sel = tab.evaluate("""() => { const r = document.querySelector('.rv-shadow-done').getBoundingClientRect(); return [getComputedStyle(document.getElementById('rv-shadow')).userSelect, r.x + 30, r.y + r.height / 2]; }""")
        tab.mouse.dblclick(sel[1], sel[2]); tab.wait_for_timeout(150)
        ok("C10 the strip's text can't be selected: a double-click on 'reviewed' highlights nothing",
           sel[0] == "none" and tab.evaluate("String(getSelection()).trim()") == "", (sel, tab.evaluate("String(getSelection())")))
        shot(tab, "4-all-list")
        box = tab.evaluate(f"""() => {{ const b = document.querySelector('#rv-shadow-list li[data-sc="{older}"] button'); const r = b.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }}""")
        tab.mouse.click(box[0], box[1]); tab.wait_for_timeout(300)
        offs = [json.loads(x[len(sc.SC_OFF):])["id"] for x in on_disk()["deleted"] if x.startswith(sc.SC_OFF)]
        c = consent()
        ok("C10 the older click's Undo cancels THAT click only", older in offs and newer not in offs, offs)
        s = tab.evaluate(STRIP)
        ok(f"C10 its comments count as not reviewed again: the button is back for {len(OPEN_SHADOW)}, the newer click still shows",
           s["button"] == f"I've reviewed Shadow's comments ({len(OPEN_SHADOW)})" and "Earlier ones reviewed" in (s["text"] or "") and s["undo"], s)
        ok("C10 with one click left the 'All' toggle and the list are gone", not tab.evaluate("!!document.getElementById('rv-shadow-all') || !!document.getElementById('rv-shadow-list')"))
        ok("C10 shadow_consent.py reads the same: the newer click's price stays, the older one's is out",
           ids(c["prices"]) == [LATER] and c["confirmed"] and [x["id"] for x in c["confirmations"]] == [newer] and c["covered_ids"] == [LATER], c)
        tab = reopen(tab)
        s = tab.evaluate(STRIP)
        ok("C10 survives a reload", s["button"] == f"I've reviewed Shadow's comments ({len(OPEN_SHADOW)})" and s["undo"], s)
        tab.evaluate("document.getElementById('rv-shadow-undo').click()"); tab.wait_for_timeout(300)
        ok("C10 the newest's Undo then leaves none in force", consent()["confirmed"] is False and not tab.evaluate(STRIP)["undo"], consent())

        # C8: the margin's own behavior after a click
        tab.evaluate("document.getElementById('rv-shadow-ok').click()"); tab.wait_for_timeout(300)
        cards = tab.evaluate("ids => ids.map(i => !!document.querySelector(`#rv-canvas .rv-card[data-cm=\"${i}\"], #rv-list .rv-card[data-cm=\"${i}\"]`))", OPEN_SHADOW)
        marks = tab.evaluate("ids => ids.map(i => !!document.querySelector(`mark.cm[data-cm=\"${i}\"]`))", OPEN_SHADOW)
        res = {t["id"]: t["resolved"] for t in on_disk()["threads"]}
        ok("C8 the click resolves nothing", not any(res[i] for i in OPEN_SHADOW + [LATER]), res)
        ok("C8 the click hides no card or highlight", all(cards) and all(marks), (cards, marks))
        ok("C8 a tick on a Shadow thread was there to click", tab.evaluate(TICK, "sr-link0001"))
        tab.wait_for_timeout(300)
        t = {x["id"]: x for x in on_disk()["threads"]}["sr-link0001"]
        ok("C8 a tick still means no: closed by him, no reply added", t["resolved"] is True and t.get("resolved_by") == "user" and not t["replies"], t)
        ok("C8 a ticked link no longer qualifies", "sr-link0001" not in ids(consent()["links"]), consent())
        ok("C8 replying works as before", tab.evaluate(REPLY, ["sr2-price001", "no, skip this one"]))
        tab.wait_for_timeout(300)
        t = {x["id"]: x for x in on_disk()["threads"]}["sr2-price001"]
        ok("C8 the reply is Kevin's and the thread stays open", t["resolved"] is False and t["replies"] and t["replies"][-1]["author"] == "Kevin", t)
        ok("C8 a price he answered no longer qualifies", "sr2-price001" not in ids(consent()["prices"]), consent())
        tab.evaluate("""id => { const b = document.querySelector(`.rv-card[data-cm="${id}"] [data-a="del"]`); if (b) b.click(); }""", LATER); tab.wait_for_timeout(300)
        ok("C8 a covered thread he deletes is gone and does not qualify", LATER in on_disk()["deleted"] and LATER not in [x["id"] for x in on_disk()["threads"]] and LATER not in ids(consent()["prices"]), on_disk()["deleted"])
        s = tab.evaluate(STRIP)
        ok("C8 no open Shadow thread unconfirmed: the button stays away", not s["button"], s)

        # C9: nothing downstream reads the record as a comment
        recs = corpus.comment_records()
        ok("C9 the corpus reads only the scratch store", all(r["key"] == KEY for r in recs) and recs, [r["key"] for r in recs][:4])
        ok("C9 the corpus counts Kevin's comment and replies, not the confirmation", not [r for r in recs if "shadow-consent" in json.dumps(r)] and sorted(r["kind"] for r in recs) == ["comment", "reply", "reply"], [(r["kind"], r["text"][:40]) for r in recs])
        ok("C9 no stray thread: the store holds exactly the threads the fixture and test put there", sorted(x["id"] for x in on_disk()["threads"]) == sorted(i for i in ALL_IDS if i != LATER), [x["id"] for x in on_disk()["threads"]])

        # C11 the store's 300-entry cap on deleted ids never drops a confirmation (QA 2026-11-10 #12); scratch store only
        cap_key = "zz__test_shadow_cap"
        cap_file = STORE / (cap_key + ".json")
        cap_threads = [th("sr-cap-price", "fill: price"), th("sr-cap-other", "fill: price")]
        cap_file.write_text(json.dumps({"threads": cap_threads, "deleted": ["old-del-0", click("sr-cap-price", cid="sc-cap"),
                                                                           click("sr-cap-other", cid="sc-cap2"), undo("sc-cap2")]}), encoding="utf-8")
        for k in range(0, 360, 40):                # 360 later deletions, in a few saves, as tabs would post them
            redline.comments_save(cap_key, {"threads": cap_threads, "deleted": ["del-%03d" % i for i in range(k, k + 40)]})
        cap = json.loads(cap_file.read_text(encoding="utf-8"))
        plain = [x for x in cap["deleted"] if not x.startswith("shadow-consent")]
        rep = sc.report(cap_key, STORE)
        ok("C11 after 360 more deletions the click is still in the store and still in force",
           any(x.startswith(sc.SC_ON) and "sc-cap" in x for x in cap["deleted"]) and rep["confirmed"] and ids(rep["prices"]) == ["sr-cap-price"], (cap["deleted"][:3], rep))
        ok("C11 ...and so is the Undo of the other click (it stays undone)", any(x.startswith(sc.SC_OFF) for x in cap["deleted"]) and "sr-cap-other" not in ids(rep["prices"]), rep)
        ok("C11 deleted thread ids are still capped at 300, the oldest dropped first",
           len(plain) == 300 and plain[0] == "del-060" and plain[-1] == "del-359" and "old-del-0" not in plain, (len(plain), plain[:2], plain[-1:]))
        redline.comments_save(cap_key, {"threads": cap_threads + [th("del-359", "fill: price")], "deleted": []})
        ok("C11 deletions merge as before: a deleted id posted again by a stale tab stays deleted",
           "del-359" not in [t["id"] for t in json.loads(cap_file.read_text(encoding="utf-8"))["threads"]])
        ok("C0 no request went to any other article's comments or write route", not violations, violations[:4])
        ok("C0 no script errors", not errs, errs[:2])
        b.close()
    n_ok = sum(1 for c in checks if c[1])
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    try:
        test_consent_rules()
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
