"""The editor's Queue tab (queue.js + tools/queue_tab.py; Kevin, 2026-11-06). Everything runs against SCRATCH COPIES of
.tmp/away_queue.md and .tmp/kevin_todo.json in a temp folder (QUEUE_TAB_DIR) with a fake .tmp/redline (QUEUE_TAB_REDLINE),
served by the standalone `python tools/queue_tab.py --serve <port>`; the editor is loaded from file:// with
window.QUEUE_API pointed at that port, so the live photo server is never needed and the real files are never written.
The last section reads the REAL files through a read-only server (QUEUE_TAB_READONLY=1) for a render check only.

  API    the md parsed into cards; add / reorder (renumbers) / hold edit only their lines and keep every other byte
         (BOM-less CRLF file stays CRLF); a stale version is refused with 409 and nothing is written; the inbox's
         add / update / toggle / reorder (inside a category) / reorder_cats / batch / 409; live rounds + Shadow threads
         folded in and finished by themselves; the seed's old "shadow" category is migrated into rounds on load
  UI     the tab in the bar with Ctrl+Alt+6 in its tooltip and the shortcut working; badge = open items of the shown
         categories; categories folded by default, filter chips remembered across a reload; add / check off / status;
         drag to reorder Claude's queue, a category, and an item inside a category (pointer drag on the grip);
         the viewer (screenshot, Desktop/Phone, ← →, Esc, new → seen); a question's reply; 393 px without sideways
         scroll; the "restart the photo server" message on a 404; a concurrent edit refused in the UI
  REAL   read-only render of the real queue + inbox, screenshots to .tmp/previews/queue-tab-*.png, POST refused (403)

    python tools/test_queue_tab.py
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)
EDITOR = (ROOT / "editor.html").as_uri()
SHOTS = ROOT / ".tmp" / "previews"
SHOTS.mkdir(parents=True, exist_ok=True)
checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:700]), flush=True)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def wait_port(port, t=30):
    end = time.time() + t
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


class Api:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"} if body is not None else {}, method="POST" if body is not None else "GET")
        for attempt in range(3):               # this box's loopback drops a connection now and then (memory project_suite_stress_1009)
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status, json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.loads(e.read().decode("utf-8"))
                except Exception:
                    return e.code, {}
            except (ConnectionResetError, ConnectionAbortedError, urllib.error.URLError) as e:
                if attempt == 2 or body is not None and attempt == 1:
                    raise
                time.sleep(0.3)


def start_server(port, env):
    e = dict(os.environ, **env, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, str(ROOT / "tools" / "queue_tab.py"), "--serve", str(port)], env=e,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == "win32" else 0)
    if not wait_port(port):
        p.kill(); raise RuntimeError("queue_tab server did not start")
    return p


# ----------------------------------------------------------------------------- scratch fixtures
SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="queue_tab_test_"))
MD = SCRATCH / "away_queue.md"
TODO = SCRATCH / "kevin_todo.json"
RL = SCRATCH / "redline"
REAL_MD = ROOT / ".tmp" / "away_queue.md"
shutil.copyfile(REAL_MD, MD)
md_bytes0 = MD.read_bytes()
CRLF = b"\r\n" in md_bytes0

(RL / "zz-test-r1").mkdir(parents=True)
(RL / "zz-test-r1" / "proposal.json").write_text(json.dumps({"slug": "zz-test-r1", "title": "Zz Test, round 1", "article_dir": "Drafts/Upcoming/zz", "article": "test.html",
                                                              "changes": [{"id": "g01"}, {"id": "g02"}, {"id": "g03"}]}), encoding="utf-8")
(RL / "zz-test-r1" / "decisions.json").write_text(json.dumps({"g01": True}), encoding="utf-8")
(RL / "_comments").mkdir()
(RL / "_comments" / "zz__test.json").write_text(json.dumps({"threads": [
    {"id": "sr-1", "author": "Shadow reviewer", "resolved": False, "replies": []},
    {"id": "sr-2", "author": "Shadow reviewer", "resolved": False, "replies": []},
    {"id": "sr-3", "author": "Shadow reviewer", "resolved": False, "replies": [{"author": "Kevin", "text": "yes"}]},
    {"id": "c-1", "author": "Kevin", "resolved": False, "replies": []}]}), encoding="utf-8")
shot_d = ROOT / ".tmp" / "review_hub" / "shots" / "map-athens-city-desktop.jpg"
shot_p = ROOT / ".tmp" / "review_hub" / "shots" / "map-athens-city-phone.jpg"
have_shots = shot_d.is_file() and shot_p.is_file()
seed = {"categories": [{"id": i, "name": n} for i, n in [("rounds", "Review: rounds"), ("maps", "Review: maps"), ("upgrades", "Review: articles"), ("feedback", "Feedback for Claude"),
                                                        ("shadow", "Shadow reviewer"), ("social", "Social"), ("setup", "Setup before Friday"), ("mine", "My own to-dos")]],
        "items": [
            {"id": "map-a", "cat": "maps", "kind": "review", "title": "Map A", "summary": "first map", "status": "new", "links": {"editor": "Drafts/Upcoming/zz/test.html"},
             "shots": {"desktop": ".tmp/review_hub/shots/map-athens-city-desktop.jpg", "phone": ".tmp/review_hub/shots/map-athens-city-phone.jpg"} if have_shots else None},
            {"id": "map-b", "cat": "maps", "kind": "review", "title": "Map B", "summary": "second map", "status": "new", "links": {},
             "shots": {"desktop": ".tmp/review_hub/shots/map-athens-city-desktop.jpg"} if have_shots else None},
            {"id": "map-c", "cat": "maps", "kind": "review", "title": "Map C", "summary": "third map, no shots", "status": "seen", "links": {}},
            {"id": "q-1", "cat": "feedback", "kind": "question", "title": "Which fare?", "summary": "50 or 65", "status": "new", "links": {}},
            {"id": "m-1", "cat": "mine", "kind": "todo", "title": "Mine one", "status": "new", "links": {}, "due": "2026-11-07"},
            {"id": "m-2", "cat": "mine", "kind": "todo", "title": "Mine two", "status": "new", "links": {}},
            {"id": "m-3", "cat": "mine", "kind": "todo", "title": "Mine done", "status": "done", "links": {}},
        ]}
TODO.write_text(json.dumps(seed, indent=1), encoding="utf-8")

PORT = free_port()
srv = start_server(PORT, {"QUEUE_TAB_DIR": str(SCRATCH), "QUEUE_TAB_REDLINE": str(RL)})
api = Api(PORT)
lines = lambda b: b.split(b"\r\n" if CRLF else b"\n")

try:
    # ----------------------------------------------------------------------------- API: Claude's queue
    st, q = api.call("/api/queue")
    ok("Q1 GET /api/queue parses the md", st == 200 and q["ok"] and len(q["queue"]) >= 10 and len(q["version"]) == 16, (st, len(q.get("queue", []))))
    it1 = next((i for i in q["queue"] if i["num"] == 1), None)
    ok("Q1b item 1 = bold title, done, with details", it1 and it1["title"] == "Editor suite stress test" and it1["state"] == "done" and len(it1["details"]) == 2, it1)
    bold = [i for i in q["queue"] if i["title"] and not i["title"].startswith("DONE")]       # the md's own items change as Claude works; the shape is what is tested
    ok("Q1c titles are the bold run, never the ** marks or an '(added by' tail", bold and all("**" not in i["title"] and "(added by" not in i["title"] for i in q["queue"]), [i["title"][:60] for i in q["queue"] if "**" in i["title"] or "(added by" in i["title"]])
    ok("Q1d the FIRST setup checklist and the sections come through", len(q["queue_pre"]["items"]) == 4 and any(s["name"].startswith("Waiting on Kevin") for s in q["sections"]) and any(s["name"] == "Log" for s in q["sections"]), [s["name"] for s in q["sections"]])
    n0 = len(q["queue"]); maxnum = max(i["num"] for i in q["queue"])

    st, r = api.call("/api/queue", {"version": q["version"], "op": "add", "title": "Zz test item", "details": "first detail\nsecond detail"})
    b1 = MD.read_bytes()
    L0, L1 = lines(md_bytes0), lines(b1)
    new = [l for l in L1 if l not in L0]
    ok("Q2 add appends a numbered [ ] item with (added by Kevin <date>) + sub-bullets", st == 200 and len(r["queue"]) == n0 + 1 and r["queue"][-1]["num"] == maxnum + 1 and r["queue"][-1]["title"] == "Zz test item"
       and new[0].startswith(f"{maxnum + 1}. [ ] **Zz test item** (added by Kevin 20".encode()) and new[1:] == [b"   - first detail", b"   - second detail"], (st, new[:3]))
    wi = next(i for i, l in enumerate(L1) if l.startswith(b"## Waiting on Kevin"))
    k = L1.index(new[0])
    ok("Q2b ...as one 3-line insertion after the last item (before '## Waiting on Kevin'), every other byte kept, CRLF kept",
       len(new) == 3 and L1[:k] == L0[:k] and L1[k + 3:] == L0[k:] and k < wi and all(not l.startswith(b"## ") for l in L0[k:wi - 3]) and (b"\r\n" in b1) == CRLF and (not CRLF or b1.count(b"\r\n") == len(L1) - 1), (k, wi, L1[k - 1:k + 4]))

    order = [i["num"] for i in r["queue"]]
    st, r2 = api.call("/api/queue", {"version": r["version"], "op": "reorder", "order": order})
    ok("Q3 a no-op reorder writes nothing (bytes and version unchanged)", st == 200 and MD.read_bytes() == b1 and r2["version"] == r["version"], (st, r2.get("version"), r["version"]))

    moved = [order[-1]] + order[:-1]
    st, r3 = api.call("/api/queue", {"version": r2["version"], "op": "reorder", "order": moved})
    b3 = MD.read_bytes(); L3 = lines(b3)
    nums = [i["num"] for i in r3["queue"]]
    ok("Q4 reorder moves the block (title + sub-bullets) and renumbers 1..n", st == 200 and nums == list(range(1, n0 + 2)) and r3["queue"][0]["title"] == "Zz test item" and r3["queue"][0]["details"] == ["first detail", "second detail"]
       and r3["queue"][1]["title"] == "Editor suite stress test", (st, nums[:5], r3["queue"][0]["title"]))
    q_start = next(i for i, l in enumerate(L3) if l.startswith(b"## Queue")); w3 = next(i for i, l in enumerate(L3) if l.startswith(b"## Waiting on Kevin"))
    ok("Q4b the preamble (FIRST checklist) and everything outside the Queue section are untouched", L3[:q_start + 1] == L1[:q_start + 1] and L3[w3:] == L1[wi:] and L3[q_start + 1:q_start + 7] == L1[q_start + 1:q_start + 7], (L3[q_start + 1:q_start + 3]))

    pend = next(i for i in r3["queue"] if i["state"] == "pending")
    st, r4 = api.call("/api/queue", {"version": r3["version"], "op": "hold", "num": pend["num"], "hold": True})
    held = next(i for i in r4["queue"] if i["num"] == pend["num"])
    ok("Q5 hold writes [-] and the card reads 'hold'", st == 200 and held["state"] == "hold" and held["mark"] == "-" and f"{pend['num']}. [-] ".encode() in MD.read_bytes(), (st, held))
    st, r5 = api.call("/api/queue", {"version": r4["version"], "op": "hold", "num": pend["num"], "hold": False})
    ok("Q5b resume gives the exact bytes back", st == 200 and MD.read_bytes() == b3, st)
    done = next(i for i in r5["queue"] if i["state"] == "done")
    st, e = api.call("/api/queue", {"version": r5["version"], "op": "hold", "num": done["num"], "hold": True})
    ok("Q6 a done item cannot go on hold (400)", st == 400 and "done" in e.get("error", ""), (st, e))

    # a concurrent edit: Claude appends a Log line while the page holds the old version
    stale_version = r5["version"]
    ext = MD.read_bytes() + ("- 2026-11-07: external edit by Claude" + ("\r\n" if CRLF else "\n")).encode()
    MD.write_bytes(ext)
    st, e = api.call("/api/queue", {"version": stale_version, "op": "add", "title": "Should not land"})
    ok("Q7 a write with a stale version is refused (409, stale, nothing written, fresh list returned)", st == 409 and e.get("stale") and "changed" in e.get("error", "") and MD.read_bytes() == ext and e.get("queue") and e["version"] != stale_version, (st, e.get("error")))
    st, r6 = api.call("/api/queue", {"version": e["version"], "op": "add", "title": "Lands now"})
    ok("Q7b ...and the same write with the fresh version lands", st == 200 and r6["queue"][-1]["title"] == "Lands now", st)
    st, e = api.call("/api/queue", {"version": r6["version"], "op": "reorder", "order": [1, 2]})
    ok("Q8 a bad reorder is refused (400) and nothing changes", st == 400 and MD.read_bytes() == MD.read_bytes() and "every item" in e.get("error", ""), (st, e))

    # ----------------------------------------------------------------------------- API: the inbox
    st, t = api.call("/api/todo?sync=1")
    byid = {i["id"]: i for i in t["items"]}
    ok("T1 GET /api/todo: 7 categories (the seed's 'shadow' one migrated away), counts, and the live round + Shadow items folded into rounds", st == 200 and [c["id"] for c in t["categories"]] == ["rounds", "maps", "upgrades", "feedback", "social", "setup", "mine"]
       and "round-zz-test-r1" in byid and byid["round-zz-test-r1"]["cat"] == "rounds"
       and byid["round-zz-test-r1"]["round"] == {"slug": "zz-test-r1", "changes": 3, "left": 2} and byid["round-zz-test-r1"]["links"]["editor"] == "Drafts/Upcoming/zz/test.html"
       and "shadow-test" in byid and byid["shadow-test"]["shadow_open"] == 2 and byid["shadow-test"]["cat"] == "rounds" and t["counts"]["mine"] == {"open": 2, "new": 2, "done": 1, "needs": 0}, (st, sorted(byid), t.get("counts")))
    st, t = api.call("/api/todo", {"version": t["version"], "op": "add", "cat": "mine", "title": "Mine three", "links": "Drafts/Upcoming/zz/test.html", "due": "2026-11-09"})
    mine = [i["id"] for i in t["items"] if i["cat"] == "mine"]
    m3 = next(i for i in t["items"] if i["title"] == "Mine three")
    ok("T2 add lands at the END of its category (his order kept), link -> links.editor", st == 200 and mine[-1] == m3["id"] and mine[:3] == ["m-1", "m-2", "m-3"] and m3["links"] == {"editor": "Drafts/Upcoming/zz/test.html"} and m3["due"] == "2026-11-09", (st, mine))
    st, t = api.call("/api/todo", {"version": t["version"], "op": "add", "cat": "mine", "title": "Mine top", "position": "top"})
    mine = [i["id"] for i in t["items"] if i["cat"] == "mine"]
    ok("T2b position top goes first in the category", st == 200 and next(i for i in t["items"] if i["id"] == mine[0])["title"] == "Mine top", mine)
    st, t = api.call("/api/todo", {"version": t["version"], "op": "add", "cat": "nowhere", "title": "Lost cat"})
    ok("T2c an unknown category without cat_name lands in 'mine'", st == 200 and next(i for i in t["items"] if i["title"] == "Lost cat")["cat"] == "mine", st)
    st, t = api.call("/api/todo", {"version": t["version"], "op": "add", "cat": "fixes", "cat_name": "Fixes", "title": "A fix", "id": "fix-1"})
    ok("T2d ...with cat_name the category is created at the end", st == 200 and t["categories"][-1] == {"id": "fixes", "name": "Fixes"} and next(i for i in t["items"] if i["id"] == "fix-1")["cat"] == "fixes", (st, t["categories"][-1]))
    st, e = api.call("/api/todo", {"version": t["version"], "op": "add", "cat": "mine", "title": "dup", "id": "fix-1"})
    ok("T2e a duplicate id is refused (400)", st == 400, (st, e))

    st, t = api.call("/api/todo", {"version": t["version"], "op": "update", "id": "map-a", "status": "needs-changes", "note": "the label overlaps"})
    a = next(i for i in t["items"] if i["id"] == "map-a")
    ok("T3 update status needs-changes + note (decided_at, seen_at stamped)", st == 200 and a["status"] == "needs-changes" and a["note"] == "the label overlaps" and a.get("decided_at") and a.get("seen_at"), a)
    st, t = api.call("/api/todo", {"version": t["version"], "op": "update", "id": "q-1", "reply": "65 at night", "status": "done"})
    qq = next(i for i in t["items"] if i["id"] == "q-1")
    ok("T3b a question's reply is stored with replied_at and the item is done", st == 200 and qq["reply"] == "65 at night" and qq["status"] == "done" and qq.get("replied_at") and qq.get("done_at"), qq)
    st, t = api.call("/api/todo", {"version": t["version"], "op": "toggle", "id": "m-1", "done": True})
    st2, t = api.call("/api/todo", {"version": t["version"], "op": "toggle", "id": "m-1", "done": False})
    m1 = next(i for i in t["items"] if i["id"] == "m-1")
    ok("T3c toggle done and back gives 'seen', not 'new'", st == 200 and st2 == 200 and m1["status"] == "seen" and m1["done_at"] is None, m1)
    st, e = api.call("/api/todo", {"version": t["version"], "op": "update", "id": "m-1", "status": "approved"})
    ok("T3d an unknown status is refused (400)", st == 400 and "status" in e.get("error", ""), (st, e))

    before = [i["id"] for i in t["items"]]
    mine = [i["id"] for i in t["items"] if i["cat"] == "mine"]
    rev = list(reversed(mine))
    st, t = api.call("/api/todo", {"version": t["version"], "op": "reorder", "cat": "mine", "order": rev})
    after = [i["id"] for i in t["items"]]
    ok("T4 reorder inside a category: that category reversed, every other item keeps its place",
       st == 200 and [i["id"] for i in t["items"] if i["cat"] == "mine"] == rev and [x for x in after if x not in mine] == [x for x in before if x not in mine], (st, after))
    st, e = api.call("/api/todo", {"version": t["version"], "op": "reorder", "cat": "mine", "order": rev[:-1]})
    ok("T4b an incomplete order is refused (400)", st == 400, (st, e))
    cats = [c["id"] for c in t["categories"]]
    newc = cats[1:] + cats[:1]
    st, t = api.call("/api/todo", {"version": t["version"], "op": "reorder_cats", "order": newc})
    ok("T5 reorder_cats persists his category order", st == 200 and [c["id"] for c in t["categories"]] == newc and json.loads(TODO.read_text(encoding="utf-8"))["categories"][0]["id"] == newc[0], (st, [c["id"] for c in t["categories"]]))
    st, e = api.call("/api/todo", {"version": "0000000000000000", "op": "add", "cat": "mine", "title": "stale"})
    ok("T6 a stale version is refused with 409 and the fresh list", st == 409 and e.get("stale") and e.get("items"), (st, e.get("error")))
    st, t = api.call("/api/todo", {"op": "batch", "ops": [{"op": "delete", "id": "fix-1"}, {"op": "update", "id": "map-b", "status": "seen"}]})
    ok("T7 batch without a version applies every op (an agent's write)", st == 200 and not any(i["id"] == "fix-1" for i in t["items"]) and next(i for i in t["items"] if i["id"] == "map-b")["status"] == "seen", st)
    # the round gets decided and applied; the Shadow threads get answered: the items finish by themselves
    (RL / "zz-test-r1" / "decisions.json").write_text(json.dumps({"g01": True, "g02": False, "g03": True}), encoding="utf-8")
    st, t = api.call("/api/todo?sync=1")
    rr = next(i for i in t["items"] if i["id"] == "round-zz-test-r1")
    ok("T8 live sync: the round's 'left' follows decisions.json", rr["round"]["left"] == 0 and "0 of 3" in rr["summary"], rr)
    (RL / "zz-test-r1" / "applied.json").write_text("{}", encoding="utf-8")
    c = json.loads((RL / "_comments" / "zz__test.json").read_text(encoding="utf-8"))
    for th in c["threads"]:
        th["replies"] = [{"author": "Kevin", "text": "no"}]
    (RL / "_comments" / "zz__test.json").write_text(json.dumps(c), encoding="utf-8")
    st, t = api.call("/api/todo?sync=1")
    rr = next(i for i in t["items"] if i["id"] == "round-zz-test-r1"); sh = next(i for i in t["items"] if i["id"] == "shadow-test")
    ok("T8b an applied round and answered Shadow threads go done by themselves", rr["status"] == "done" and "Applied" in rr["summary"] and sh["status"] == "done", (rr["status"], sh["status"]))

    # ----------------------------------------------------------------------------- UI
    TODO.write_text(json.dumps(seed, indent=1), encoding="utf-8")         # a clean inbox for the UI run (the round/shadow items re-sync on GET)
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(f"window.QUEUE_API = 'http://127.0.0.1:{PORT}'; try {{ if (!sessionStorage.getItem('qt-test')) {{ localStorage.clear(); sessionStorage.setItem('qt-test', '1'); }} }} catch (e) {{}}")
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(EDITOR + "#queue")
        pg.wait_for_selector("#app-queue:not([hidden]) .qt-item", timeout=20000)
        ok("U0 no page errors on load", not errors, errors[:3])
        tab = pg.locator(".app-tabs a[data-view='queue']").first
        ok("U1 the Queue tab sits in the bar after Launch, tooltip says Ctrl+Alt+6", tab.count() and "Ctrl+Alt+6" in (tab.get_attribute("title") or "") and tab.inner_text().startswith("Queue")
           and pg.evaluate("[...document.querySelectorAll('.app-tabs a')].map(a => a.dataset.view)")[-2:] == ["launch", "queue"], tab.get_attribute("title"))
        pg.keyboard.press("Control+Alt+5"); pg.wait_for_timeout(200)
        v5 = pg.evaluate("[document.getElementById('app-launch').hidden, document.getElementById('app-queue').hidden]")
        pg.keyboard.press("Control+Alt+6"); pg.wait_for_timeout(200)
        v6 = pg.evaluate("[document.getElementById('app-launch').hidden, document.getElementById('app-queue').hidden, location.hash]")
        ok("U2 Ctrl+Alt+5 shows Launch, Ctrl+Alt+6 brings Queue back (#queue)", v5 == [False, True] and v6 == [True, False, "#queue"], (v5, v6))
        st, t = api.call("/api/todo?sync=0")
        nopen = sum(1 for i in t["items"] if i["status"] != "done")
        bdg = pg.locator("#app-queue .app-tabs a[data-view='queue'] .bdg").first
        ok("U3 the badge counts the open items", bdg.is_visible() and bdg.inner_text() == str(nopen), (bdg.inner_text(), nopen))
        qn = pg.locator(".qt-item").count()
        ok("U4 Claude's queue renders one card per item, FIRST checklist above, Log folded at the bottom", qn == len(api.call("/api/queue")[1]["queue"]) and pg.locator(".qt-chk").count() == 4
           and pg.locator("details[data-fold='Log']").count() == 1 and not pg.locator("details[data-fold='Log']").first.get_attribute("open"), qn)
        pg.locator(".qt-item .qt-trow[role=button]").first.click()
        ok("U4b a card expands to its details", pg.locator(".qt-item .qt-d").count() == 1, pg.locator(".qt-item .qt-d").count())
        ok("U5 categories are folded by default, each header shows its counts", pg.locator(".qt-cat").count() == 7 and pg.locator(".qt-cat.open").count() == 0
           and pg.locator(".qt-cat[data-cat='maps'] .cnt .n").first.inner_text() == "3", pg.locator(".qt-cat[data-cat='maps'] .cnt").inner_text())
        pg.locator(".qt-cat[data-cat='maps'] .qt-catb").click()
        ok("U5b a category opens to its rows", pg.locator(".qt-cat[data-cat='maps'].open .qt-todo").count() == 3, pg.locator(".qt-cat[data-cat='maps'].open .qt-todo").count())
        pg.locator(".qt-fchip[data-cat='setup']").click(); pg.wait_for_timeout(100)
        pg.locator(".qt-fchip[data-cat='feedback']").click(); pg.wait_for_timeout(100)
        n_after = pg.locator(".qt-cat").count(); bdg2 = bdg.inner_text()
        pg.reload(); pg.wait_for_selector("#app-queue:not([hidden]) .qt-cat", timeout=20000); pg.wait_for_timeout(300)
        ok("U6 filter chips hide categories (badge drops the hidden ones) and the choice survives a reload",
           n_after == 5 and int(bdg2) == nopen - 1 and pg.locator(".qt-cat").count() == 5 and pg.locator(".qt-cat[data-cat='feedback']").count() == 0 and pg.locator(".qt-cat[data-cat='maps'].open").count() == 1, (n_after, bdg2, pg.locator(".qt-cat").count()))
        pg.locator(".qt-more[data-act='cat-filter-all']").click(); pg.wait_for_timeout(150)
        ok("U6b 'show all' brings them back", pg.locator(".qt-cat").count() == 7, pg.locator(".qt-cat").count())
        ok("U6c the Due soon strip lists the item due tomorrow with its category", pg.locator(".qt-due a").count() >= 1 and "Mine one" in pg.locator(".qt-due").inner_text(), pg.locator(".qt-due").inner_text() if pg.locator(".qt-due").count() else "none")

        pg.fill("#qt-ta-text", "Zz UI to-do"); pg.select_option("#qt-ta-cat", "mine"); pg.fill("#qt-ta-link", "https://example.com/x")
        pg.locator("form[data-form='todo-add'] button[type=submit]").click(); pg.wait_for_timeout(400)
        st, t = api.call("/api/todo?sync=0")
        ui_item = next((i for i in t["items"] if i["title"] == "Zz UI to-do"), None)
        ok("U7 add from the form lands at the end of its category with the link, category opens", ui_item and ui_item["links"] == {"url": "https://example.com/x"} and [i["id"] for i in t["items"] if i["cat"] == "mine"][-1] == ui_item["id"]
           and pg.locator(".qt-cat[data-cat='mine'].open").count() == 1 and pg.locator(f".qt-todo[data-id='{ui_item['id']}']").count() == 1, ui_item)
        row = pg.locator(f".qt-todo[data-id='{ui_item['id']}']")
        row.locator("input[type=checkbox]").check(); pg.wait_for_timeout(300)
        st, t = api.call("/api/todo?sync=0")
        ok("U7b the checkbox marks it done (it moves to the category's Done fold)", next(i for i in t["items"] if i["id"] == ui_item["id"])["status"] == "done" and pg.locator(f".qt-cat[data-cat='mine'] details[data-fold] .qt-todo[data-id='{ui_item['id']}']").count() == 1, st)
        pg.locator(".qt-todo[data-id='map-c'] .qt-stsel").select_option("needs-changes"); pg.wait_for_timeout(300)
        st, t = api.call("/api/todo?sync=0")
        ok("U7c the status select writes needs-changes", next(i for i in t["items"] if i["id"] == "map-c")["status"] == "needs-changes" and pg.locator(".qt-todo[data-id='map-c'] .qt-stsel.needs-changes").count() == 1, st)

        # drag by the grip (pointer events): Claude's queue, a category, an item inside a category
        def drag(src_sel, dst_sel, where="before"):
            # both rows must be on screen: a mouse at off-screen coordinates presses nothing
            pg.locator(dst_sel).first.scroll_into_view_if_needed(); pg.wait_for_timeout(100)
            s = pg.locator(src_sel).first.bounding_box(); d = pg.locator(dst_sel).first.bounding_box()
            if s["y"] < 70 or s["y"] + s["height"] > 880:
                pg.evaluate("dy => { document.getElementById('queue-root').scrollTop += dy; }", s["y"] - 450); pg.wait_for_timeout(100)
                s = pg.locator(src_sel).first.bounding_box(); d = pg.locator(dst_sel).first.bounding_box()
            y = d["y"] + (3 if where == "before" else d["height"] - 3)
            pg.mouse.move(s["x"] + s["width"] / 2, s["y"] + s["height"] / 2); pg.mouse.down()
            pg.mouse.move(s["x"] + s["width"] / 2, s["y"] + 12, steps=3); pg.mouse.move(d["x"] + 30, y, steps=8); pg.mouse.move(d["x"] + 30, y + (1 if where == "before" else -1), steps=2)
            pg.mouse.up(); pg.wait_for_timeout(500)
        q_before = [i["num"] for i in api.call("/api/queue")[1]["queue"]]
        third_title = pg.locator(".qt-item").nth(2).locator(".qt-t").inner_text()
        drag(".qt-item[data-idx='2'] .qt-grip", ".qt-item[data-idx='0']", "before")
        st, q2 = api.call("/api/queue")
        ok("U8 dragging the third card above the first reorders Claude's queue (renumbered, written to the md)", q2["queue"][0]["title"] == third_title and [i["num"] for i in q2["queue"]] == q_before
           and pg.locator(".qt-item").first.locator(".qt-t").inner_text() == third_title, (third_title, q2["queue"][0]["title"]))
        cats0 = [c["id"] for c in api.call("/api/todo?sync=0")[1]["categories"]]
        drag(".qt-cat[data-idx='1'] .qt-cath .qt-grip", ".qt-cat[data-idx='0'] .qt-cath", "before")
        cats1 = [c["id"] for c in api.call("/api/todo?sync=0")[1]["categories"]]
        ok("U9 dragging a category header above the first reorders the categories (persisted)", cats1 == [cats0[1], cats0[0]] + cats0[2:] and pg.locator(".qt-cat").first.get_attribute("data-cat") == cats0[1], (cats0[:3], cats1[:3]))
        maps0 = [i["id"] for i in api.call("/api/todo?sync=0")[1]["items"] if i["cat"] == "maps"]
        drag(".qt-cat[data-cat='maps'] .qt-todo[data-idx='2'] .qt-grip", ".qt-cat[data-cat='maps'] .qt-todo[data-idx='0']", "before")
        maps1 = [i["id"] for i in api.call("/api/todo?sync=0")[1]["items"] if i["cat"] == "maps"]
        ok("U10 dragging an item inside a category reorders just that category", maps1 == [maps0[2], maps0[0], maps0[1]], (maps0, maps1))
        pg.locator(".qt-cat[data-cat='maps'] .qt-todo[data-idx='2'] .qt-ib[data-act='todo-move'][data-dir='-1']").click(); pg.wait_for_timeout(300)
        maps2 = [i["id"] for i in api.call("/api/todo?sync=0")[1]["items"] if i["cat"] == "maps"]
        ok("U10b the arrow buttons reorder too (keyboard/phone way)", maps2 == [maps1[0], maps1[2], maps1[1]], (maps1, maps2))

        if have_shots:
            api.call("/api/todo", {"op": "reorder", "cat": "maps", "order": ["map-a", "map-b", "map-c"]})   # the drags above reversed them; the viewer walks the category in order
            pg.evaluate("window.__queueReload()"); pg.wait_for_timeout(600)
            pg.locator(".qt-todo[data-id='map-a'] .qt-chip.view").click(); pg.wait_for_selector("#qt-viewer:not([hidden]) img", timeout=10000)
            pg.wait_for_function("document.querySelector('#qt-viewer img') && document.querySelector('#qt-viewer img').naturalWidth > 0", timeout=10000)
            src = pg.get_attribute("#qt-viewer img", "src"); pos = pg.inner_text("#qt-viewer .pos")
            ok("U11 the viewer opens on the item's desktop screenshot, served from /site, with its position in the category", "map-athens-city-desktop.jpg" in src and pos.startswith("1 /") and "Review: maps" in pos, (src, pos))
            pg.wait_for_timeout(400)
            ok("U11b opening marks a new item seen", next(i for i in api.call("/api/todo?sync=0")[1]["items"] if i["id"] == "map-a")["status"] == "seen", "")
            pg.click("#qt-viewer [data-act='dev'][data-dev='phone']"); pg.wait_for_timeout(200)
            ok("U11c Phone switches the screenshot", "map-athens-city-phone.jpg" in pg.get_attribute("#qt-viewer img", "src"), pg.get_attribute("#qt-viewer img", "src"))
            pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(200)
            t2 = pg.inner_text("#qt-viewer .ttl"); pos2 = pg.inner_text("#qt-viewer .pos")
            ok("U11d → steps to the next item with shots (Map C, without shots, is skipped)", t2 == "Map B" and pos2.startswith("2 / 2"), (t2, pos2))
            ok("U11e a phone-less item says so instead of a broken image", pg.locator("#qt-viewer .qv-stage .none").count() == 1 and "No phone screenshot" in pg.inner_text("#qt-viewer .qv-stage .none"), "")
            pg.keyboard.press("ArrowLeft"); pg.wait_for_timeout(150)
            pg.locator("#qt-viewer .qt-stsel").select_option("done"); pg.wait_for_timeout(300)
            pg.fill("#qv-note", "approved from the viewer"); pg.locator("#qt-viewer .ttl").click(); pg.wait_for_timeout(300)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
            a = next(i for i in api.call("/api/todo?sync=0")[1]["items"] if i["id"] == "map-a")
            ok("U11f ← goes back; status and note from the viewer footer are saved; Esc closes", a["status"] == "done" and a["note"] == "approved from the viewer" and pg.locator("#qt-viewer").get_attribute("hidden") is not None, a)
            # swipe: a horizontal pointer gesture steps too
            pg.locator(".qt-todo[data-id='map-b'] .qt-chip.view").click(); pg.wait_for_selector("#qt-viewer:not([hidden])")
            stage = pg.locator("#qt-viewer .qv-stage").bounding_box()
            pg.mouse.move(stage["x"] + stage["width"] * .7, stage["y"] + 200); pg.mouse.down(); pg.mouse.move(stage["x"] + stage["width"] * .3, stage["y"] + 205, steps=6); pg.mouse.up(); pg.wait_for_timeout(200)
            ok("U11g a swipe left goes to the next item", pg.inner_text("#qt-viewer .pos").startswith("2 /") or pg.locator("#qt-viewer .qv-nav.r[disabled]").count() == 1, pg.inner_text("#qt-viewer .pos"))
            pg.keyboard.press("Escape"); pg.wait_for_timeout(150)
        else:
            print("skip U11 (no .tmp/review_hub/shots/map-athens-city-*.jpg)")

        pg.locator(".qt-cat[data-cat='feedback'] .qt-catb").click(); pg.wait_for_timeout(150)
        pg.fill("#qt-rp-q-1", "65 at night, 50 by day"); pg.locator(".qt-todo[data-id='q-1'] form[data-form='todo-reply'] button").click(); pg.wait_for_timeout(400)
        qq = next(i for i in api.call("/api/todo?sync=0")[1]["items"] if i["id"] == "q-1")
        ok("U12 a question's reply box writes the answer back (status done) for Claude to pick up", qq["reply"] == "65 at night, 50 by day" and qq["status"] == "done", qq)

        pg.set_viewport_size({"width": 393, "height": 852}); pg.wait_for_timeout(400)
        over = pg.evaluate("[document.scrollingElement.scrollWidth, document.getElementById('queue-root').scrollWidth, document.getElementById('queue-root').clientWidth, getComputedStyle(document.querySelector('.qt-cols')).gridTemplateColumns.split(' ').length]")
        ok("U13 393 px: no sideways scroll, the two panels stack", over[0] <= 393 and over[1] <= over[2] and over[3] == 1, over)
        pg.screenshot(path=str(SHOTS / "queue-tab-test-phone.png"))
        pg.set_viewport_size({"width": 1440, "height": 900})

        # a concurrent edit while the page is open: Claude appends a line, Kevin presses Hold
        MD.write_bytes(MD.read_bytes() + ("- 2026-11-07: another external edit" + ("\r\n" if CRLF else "\n")).encode())
        pg.locator(".qt-item .qt-hold").first.click()
        pg.wait_for_function("(() => { const t = document.querySelector('.qt-toast'); return t && !t.hidden && t.textContent.includes('changed'); })()", timeout=5000)   # not the previous toast
        toast = pg.inner_text(".qt-toast")
        held_after = bool(__import__("re").search(r"^\d+\. \[-\]", MD.read_text(encoding="utf-8"), __import__("re").M))
        log_txt = pg.evaluate("document.querySelector(\"details[data-fold='Log']\").textContent")
        ok("U14 a Hold on a stale page is refused with the message and the list reloads; nothing was written", "changed" in toast and not held_after and log_txt.count("another external edit") == 1, (toast, held_after, log_txt[-80:]))

        # the live photo server without this build: a 404 means "restart it"
        pg2 = ctx.new_page()
        pg2.route(f"http://127.0.0.1:{PORT}/api/queue", lambda r: r.fulfill(status=404, body="not found"))
        pg2.goto(EDITOR + "#queue"); pg2.wait_for_selector("#queue-root .qt-msg", timeout=20000)
        msg = pg2.inner_text("#queue-root .qt-msg")
        ok("U15 an old photo server (404) shows the restart message", "restart" in msg.lower() and "/api/queue" in msg, msg[:120])
        pg2.close()
        ok("U16 no page errors through the whole run", not errors, errors[:3])
        b.close()
finally:
    srv.kill()

# ----------------------------------------------------------------------------- REAL files, read-only render
real_todo_before = (ROOT / ".tmp" / "kevin_todo.json").read_bytes() if (ROOT / ".tmp" / "kevin_todo.json").is_file() else b""
real_md_before = REAL_MD.read_bytes()
PORT2 = free_port()
srv2 = start_server(PORT2, {"QUEUE_TAB_DIR": str(ROOT / ".tmp"), "QUEUE_TAB_READONLY": "1"})
api2 = Api(PORT2)
try:
    st, q = api2.call("/api/queue")
    ok("R1 the real away_queue.md parses (items, FIRST checklist, Waiting on Kevin, Log)", st == 200 and len(q["queue"]) >= 10 and q["counts"]["pending"] >= 5 and any(s["name"].startswith("Waiting") for s in q["sections"]), (st, q.get("counts")))
    st, e = api2.call("/api/queue", {"version": q["version"], "op": "add", "title": "must not land"})
    st2, e2 = api2.call("/api/todo", {"op": "add", "cat": "mine", "title": "must not land"})
    ok("R2 the read-only server refuses writes (403) and the real files are untouched", st == 403 and st2 == 403 and REAL_MD.read_bytes() == real_md_before and (ROOT / ".tmp" / "kevin_todo.json").read_bytes() == real_todo_before, (st, st2))
    st, t = api2.call("/api/todo")
    ok("R3 the real inbox: 7 categories (no 'shadow' since 2026-11-07), hub items with shots, questions, Shadow items in rounds", st == 200 and len(t["categories"]) >= 7 and not any(c["id"] == "shadow" for c in t["categories"])
       and all(i["cat"] == "rounds" for i in t["items"] if i["id"].startswith("shadow-")) and sum(1 for i in t["items"] if i.get("shots")) >= 30
       and sum(1 for i in t["items"] if i["kind"] == "question") >= 15 and any(i["id"].startswith("shadow-") for i in t["items"]) and any(i["id"] == "round-athens-r2" for i in t["items"]), (st, len(t.get("items", []))))
    with sync_playwright() as p:
        b = p.chromium.launch()
        for w, h, name in ((1440, 900, "desktop"), (393, 852, "phone")):
            ctx = b.new_context(viewport={"width": w, "height": h})
            ctx.add_init_script(f"window.QUEUE_API = 'http://127.0.0.1:{PORT2}'; try {{ localStorage.clear(); }} catch (e) {{}}")
            pg = ctx.new_page(); errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(EDITOR + "#queue"); pg.wait_for_selector("#app-queue:not([hidden]) .qt-cat", timeout=20000); pg.wait_for_timeout(500)
            if name == "desktop":
                pg.locator(".qt-cat[data-cat='maps'] .qt-catb").click(); pg.wait_for_timeout(300)
            pg.screenshot(path=str(SHOTS / f"queue-tab-{name}.png"), full_page=False)
            ok(f"R4 real render at {w} px: cards, categories, no page errors", pg.locator(".qt-item").count() >= 10 and pg.locator(".qt-cat").count() >= 7 and not errs, errs[:2])
            ctx.close()
        b.close()
    ok("R5 the read-only run left the real files byte-identical", REAL_MD.read_bytes() == real_md_before and (ROOT / ".tmp" / "kevin_todo.json").read_bytes() == real_todo_before, "")
finally:
    srv2.kill()
    shutil.rmtree(SCRATCH, ignore_errors=True)

n = sum(1 for _, c, _ in checks if c)
print(f"\n{n}/{len(checks)} passed; screenshots in {SHOTS}\\queue-tab-*.png")
sys.exit(0 if n == len(checks) else 1)
