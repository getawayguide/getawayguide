"""The editor's Queue tab (queue.js + tools/queue_tab.py; Kevin, 2026-11-06). Everything runs against SCRATCH COPIES of
.tmp/away_queue.md and .tmp/kevin_todo.json in a temp folder (QUEUE_TAB_DIR) with a fake .tmp/redline (QUEUE_TAB_REDLINE),
served by the standalone `python tools/queue_tab.py --serve <port>`; the editor is loaded from file:// with
window.QUEUE_API pointed at that port, so the live photo server is never needed and the real files are never written.
The last section reads the REAL files through a read-only server (QUEUE_TAB_READONLY=1) for a render check only.

  API    the md parsed into cards: every "N. [ ]" AND "- [ ]" line is an item, wholly bold lines are group headings
         (QA 2026-11-10: the mixed file hid 13 open items); add lands after the open items of a group (never below
         the done archive); move shifts only the named item's lines (no heading dragged along, nothing renumbered);
         hold changes one character; every other byte is kept (CRLF + BOM too); a stale version is refused with 409
         and nothing is written; the old whole-list "reorder" is refused; the same checks on a copy of the REAL md; the inbox's
         add / update / toggle / reorder (inside a category) / reorder_cats / batch / 409; live rounds + Shadow threads
         folded in and finished by themselves; the seed's old "shadow" category is migrated into rounds on load
  UI     the tab in the bar with Ctrl+Alt+6 in its tooltip and the shortcut working; badge = open items of the shown
         categories; categories folded by default, filter chips remembered across a reload; add / check off / status;
         Claude's list under its group headings with the true open count; drag an item across a heading, onto a
         heading, the arrows across a heading, add into a picked group; a category, and an item inside a category;
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
# a fixed md in the shape the real one has had since 2026-11-10 (numbered and "- [ ]" items mixed, bold group headings,
# a "**Now:**" note line, a done archive at the bottom), so the checks test behavior, not whatever the real file holds today
FIXTURE = """# Away queue: test

The work Claude does while Kevin is traveling.

Markers: `[ ]` pending, `[x]` done, `[~]` running, `[-]` on hold.

## Queue (in order)
<!-- Queue lines stay one or two plain sentences. -->
**No input from you: Claude works through these in order (reordered 2026-11-10, least input first).**
- [x] **Done bullet A.** Done Nov 10: found things.
10. [x] **Done numbered B.** Done Nov 10: fixed it.
- [~] **Running bullet C.** Found 3 bugs so far.
- [ ] **Pending bullet D.** Looks for drift. (added by Kevin 2026-11-10)
- [x] **Done bullet E.** Done Nov 9: cleared.

**Claude does the work, and you review it later.**
- [ ] **Push package.** One page with everything ready to push.
20. [ ] **Media kit page.** For tourism boards and brands.
   - detail one
   - detail two
9. [ ] **Upgrade the remaining countries.** Rewrite each country's drafts.
18. [-] **Email content for Kit.** The welcome series.

**Needs you.**
- [ ] **Confirm Google Remote Desktop is working.** Check it before the 13th. (added by Kevin 2026-11-10)
25. [ ] **Add Google Analytics.** You'd create the account.

**Top:**
- [x] **Old done one.** Done Nov 9: shipped.

**Now:** the Social tab polish, then the queue in order.

**Before the trip:**
- [x] **Pause Windows Update.** Done Nov 7.


1. [x] **Editor stress test.** Done Nov 6: 25 fixes.
2. [x] **Bus maps.** Done Nov 6.

## Waiting on Kevin
- **Shadow reviewer on every article** now runs alongside the upgrades.

## Log
- 2026-11-06: Kevin, "let's start".
"""
MD.write_bytes(FIXTURE.encode("utf-8"))
md_bytes0 = MD.read_bytes()
CRLF = b"\r\n" in md_bytes0
sys.path.insert(0, str(ROOT / "tools"))
import re  # noqa: E402
import queue_tab as qt  # noqa: E402
ITEM_LINE = re.compile(r"^(?:\d+\.|[-*])\s+\[[ xX~>-]\]")
OPEN_LINE = re.compile(r"^(?:\d+\.|[-*])\s+\[[ ~>-]\]")


def queue_section(text):
    return text.split("## Queue", 1)[1].split("\n## ", 1)[0].splitlines()[1:]

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
    # ----------------------------------------------------------------------------- API: Claude's queue (the fixture)
    T = lambda q, t: next((i for i in q["queue"] if i["title"] == t), None)
    S = lambda: MD.read_bytes().decode("utf-8").split("\n")
    st, q = api.call("/api/queue")
    sec0 = queue_section(FIXTURE)
    ok("Q1 every checkbox line in the Queue section is an item, numbered or '- [ ]' (QA 2026-11-10: bullets between numbered items were never drawn)",
       st == 200 and q["ok"] and len(q["queue"]) == sum(1 for l in sec0 if ITEM_LINE.match(l)) == 15 and len(q["version"]) == 16, (st, len(q.get("queue", []))))
    ok("Q1a the count is the real number of open items: 6 pending + 1 running + 1 held = 8 open",
       q["counts"] == {"pending": 6, "running": 1, "hold": 1, "done": 7, "open": 8} and q["counts"]["open"] == sum(1 for l in sec0 if OPEN_LINE.match(l)), q["counts"])
    rd, mk, c = T(q, "Confirm Google Remote Desktop is working"), T(q, "Media kit page"), T(q, "Running bullet C")
    ok("Q1b items keep their text and marker as in the file: a bullet has num null, a numbered one its number and sub-bullets, [~] reads running",
       rd and rd["num"] is None and rd["state"] == "pending" and rd["added_by"] == "2026-11-10" and rd["text"] == "**Confirm Google Remote Desktop is working.** Check it before the 13th. (added by Kevin 2026-11-10)"
       and mk and mk["num"] == 20 and mk["details"] == ["detail one", "detail two"] and c and c["mark"] == "~" and c["state"] == "running" and [i["id"] for i in q["queue"]] == list(range(15)), (rd, mk, c))
    ok("Q1c titles are the bold run, never the ** marks or an '(added by' tail", all(i["title"] and "**" not in i["title"] and "(added by" not in i["title"] for i in q["queue"]), [i["title"][:60] for i in q["queue"]])
    names = [g["name"] for g in q["groups"]]
    ok("Q1d the wholly bold lines are the groups (a '**Now:** text' note is not), with open/total counts; every item carries its group; the other sections come through",
       names == ["No input from you: Claude works through these in order", "Claude does the work, and you review it later", "Needs you", "Top", "Before the trip"]
       and [(g["open"], g["total"]) for g in q["groups"]] == [(2, 5), (4, 4), (2, 2), (0, 1), (0, 3)] and rd["group"] == 2 and mk["group"] == 1 and T(q, "Editor stress test")["group"] == 4
       and any(s["name"].startswith("Waiting on Kevin") for s in q["sections"]) and any(s["name"] == "Log" for s in q["sections"]) and q["queue_pre"]["items"] == [],
       (names, [(g["open"], g["total"]) for g in q["groups"]], [s["name"] for s in q["sections"]]))
    ok("Q1e a 'Done Nov 10' line gets a done_on date for the Done fold's order", T(q, "Done bullet A")["done_on"] and T(q, "Done bullet A")["done_on"].endswith("-11-10"), T(q, "Done bullet A"))

    st, r = api.call("/api/queue", {"version": q["version"], "op": "add", "title": "Zz test item", "details": "first detail\nsecond detail"})
    b1 = MD.read_bytes()
    L0, L1 = lines(md_bytes0), lines(b1)
    new = [l for l in L1 if l not in L0]
    k = L1.index(new[0]) if new else -1
    ok("Q2 add (no group named) writes a '- [ ]' item with (added by Kevin <date>) + sub-bullets as ONE 3-line insertion, every other byte kept",
       st == 200 and len(r["queue"]) == 16 and T(r, "Zz test item") and new[0].startswith(b"- [ ] **Zz test item** (added by Kevin 20") and new[1:] == [b"   - first detail", b"   - second detail"]
       and L1[:k] == L0[:k] and L1[k + 3:] == L0[k:], (st, new[:3]))
    ok("Q2b ...in the first group with open work, right after its last open item (above that group's done items; never under the archive)",
       L1[k - 1].startswith(b"- [ ] **Pending bullet D.**") and L1[k + 3].startswith(b"- [x] **Done bullet E.**") and T(r, "Zz test item")["group"] == 0 and T(r, "Zz test item")["state"] == "pending", L1[k - 1:k + 4])
    st, r = api.call("/api/queue", {"version": r["version"], "op": "add", "title": "Zz needs", "group": 2, "position": "top"})
    s1 = S(); i_h = s1.index("**Needs you.**")
    ok("Q2c add with a group and position top lands right under that group's heading", st == 200 and s1[i_h + 1].startswith("- [ ] **Zz needs**") and T(r, "Zz needs")["group"] == 2, s1[i_h:i_h + 3])
    st, r = api.call("/api/queue", {"version": r["version"], "op": "add", "title": "Zz into archive", "group": 3})
    s2 = S(); i_t = s2.index("**Top:**")
    ok("Q2d add into a group with nothing open goes above its done items, right under the heading", st == 200 and s2[i_t + 1].startswith("- [ ] **Zz into archive**") and s2[i_t + 2].startswith("- [x] **Old done one.**"), s2[i_t:i_t + 3])
    st, e = api.call("/api/queue", {"version": r["version"], "op": "add", "title": "x", "group": 99})
    ok("Q2e add into a group that doesn't exist is refused (400)", st == 400 and "group" in e.get("error", ""), (st, e))
    # back to the fixture for the move checks
    MD.write_bytes(md_bytes0)
    st, q = api.call("/api/queue")

    ga, c, d = T(q, "Add Google Analytics"), T(q, "Running bullet C"), T(q, "Pending bullet D")
    st, r2 = api.call("/api/queue", {"version": q["version"], "op": "move", "id": d["id"], "after": c["id"]})
    ok("Q3 a move to where the item already is writes nothing (bytes and version unchanged); so does a drop on itself",
       st == 200 and MD.read_bytes() == md_bytes0 and r2["version"] == q["version"] and api.call("/api/queue", {"version": q["version"], "op": "move", "id": d["id"], "before": d["id"]})[0] == 200 and MD.read_bytes() == md_bytes0, (st, r2.get("version")))

    # the QA repro: drag an item of "Needs you" to the very top of the list
    st, r3 = api.call("/api/queue", {"version": q["version"], "op": "move", "id": ga["id"], "before": c["id"]})
    s3 = S(); s0 = FIXTURE.split("\n"); ga_line = "25. [ ] **Add Google Analytics.** You'd create the account."
    ok("Q4 move: only the item's own line moves (to just above the target, in the target's group); its number is untouched; every other line keeps its order",
       st == 200 and s3.index(ga_line) == s3.index("- [~] **Running bullet C.** Found 3 bugs so far.") - 1 and [l for l in s3 if l != ga_line] == [l for l in s0 if l != ga_line]
       and T(r3, "Add Google Analytics")["group"] == 0 and T(r3, "Add Google Analytics")["num"] == 25, s3[8:14])
    ok("Q4b ...the headings did not travel with it: 'Needs you' still heads Confirm Google Remote Desktop, and no item was renumbered",
       s3[s3.index("**Needs you.**") + 1].startswith("- [ ] **Confirm Google Remote Desktop") and sorted(i["num"] for i in r3["queue"] if i["num"] is not None) == sorted(i["num"] for i in q["queue"] if i["num"] is not None)
       and T(r3, "Confirm Google Remote Desktop is working")["group"] == 2, s3[s3.index("**Needs you.**"):s3.index("**Needs you.**") + 3])
    mk = T(r3, "Media kit page"); rd = T(r3, "Confirm Google Remote Desktop is working")
    st, r4 = api.call("/api/queue", {"version": r3["version"], "op": "move", "id": mk["id"], "after": rd["id"]})
    s4 = S(); i_mk = s4.index("20. [ ] **Media kit page.** For tourism boards and brands.")
    ok("Q4c an item moves with its indented sub-bullets, across groups, and joins the target's group",
       st == 200 and s4[i_mk - 1].startswith("- [ ] **Confirm Google Remote Desktop") and s4[i_mk + 1:i_mk + 3] == ["   - detail one", "   - detail two"] and T(r4, "Media kit page")["group"] == 2
       and T(r4, "Media kit page")["details"] == ["detail one", "detail two"] and "   - detail one" not in s4[:i_mk], s4[i_mk - 1:i_mk + 4])
    pk = T(r4, "Push package")
    st, r5 = api.call("/api/queue", {"version": r4["version"], "op": "move", "id": pk["id"], "group": 3, "position": "top"})
    s5 = S(); i_t = s5.index("**Top:**")
    ok("Q4d move to a group (a drop on its heading) puts it at the top of that group", st == 200 and s5[i_t + 1].startswith("- [ ] **Push package.**") and T(r5, "Push package")["group"] == 3, s5[i_t:i_t + 3])
    st, r5b = api.call("/api/queue", {"version": r5["version"], "op": "move", "id": T(r5, "Push package")["id"], "group": 1, "position": "end"})
    s5b = S(); i_e = s5b.index("18. [-] **Email content for Kit.** The welcome series.")
    ok("Q4e move to the end of a group lands after its last open item", st == 200 and s5b[i_e + 1].startswith("- [ ] **Push package.**"), s5b[i_e:i_e + 3])
    q_start = s5b.index("## Queue (in order)"); w5 = s5b.index("## Waiting on Kevin")
    ok("Q4f everything outside the Queue section and every non-item line inside it is untouched after four moves",
       s5b[:q_start + 3] == s0[:q_start + 3] and s5b[w5:] == s0[s0.index("## Waiting on Kevin"):] and [l for l in s5b if not ITEM_LINE.match(l) and not l.startswith("   - ")] == [l for l in s0 if not ITEM_LINE.match(l) and not l.startswith("   - ")], "")
    b5 = MD.read_bytes()

    pend = T(r5b, "Pending bullet D")
    st, r6 = api.call("/api/queue", {"version": r5b["version"], "op": "hold", "id": pend["id"], "hold": True})
    held = T(r6, "Pending bullet D")
    ok("Q5 hold on a '- [ ]' item writes '- [-]' (one character) and the card reads 'hold'", st == 200 and held["state"] == "hold" and held["mark"] == "-" and b"- [-] **Pending bullet D.**" in MD.read_bytes()
       and len(MD.read_bytes()) == len(b5), (st, held))
    st, r7 = api.call("/api/queue", {"version": r6["version"], "op": "hold", "id": pend["id"], "hold": False})
    ok("Q5b resume gives the exact bytes back", st == 200 and MD.read_bytes() == b5, st)
    st, r7b = api.call("/api/queue", {"version": r7["version"], "op": "hold", "num": 9, "hold": True})
    ok("Q5c an old client's {num} still works for a numbered item", st == 200 and b"9. [-] **Upgrade the remaining countries.**" in MD.read_bytes(), (st, r7b.get("error")))
    st, r7 = api.call("/api/queue", {"version": r7b["version"], "op": "hold", "num": 9, "hold": False})
    done = next(i for i in r7["queue"] if i["state"] == "done")
    st, e = api.call("/api/queue", {"version": r7["version"], "op": "hold", "id": done["id"], "hold": True})
    ok("Q6 a done item cannot go on hold (400)", st == 400 and "done" in e.get("error", "") and MD.read_bytes() == b5, (st, e))

    # a concurrent edit: Claude appends a Log line while the page holds the old version
    stale_version = r7["version"]
    ext = MD.read_bytes() + ("- 2026-11-07: external edit by Claude" + ("\r\n" if CRLF else "\n")).encode()
    MD.write_bytes(ext)
    st, e = api.call("/api/queue", {"version": stale_version, "op": "add", "title": "Should not land"})
    ok("Q7 a write with a stale version is refused (409, stale, nothing written, fresh list returned)", st == 409 and e.get("stale") and "changed" in e.get("error", "") and MD.read_bytes() == ext and e.get("queue") and e["version"] != stale_version, (st, e.get("error")))
    st, r8 = api.call("/api/queue", {"version": e["version"], "op": "add", "title": "Lands now"})
    ok("Q7b ...and the same write with the fresh version lands", st == 200 and T(r8, "Lands now") is not None, st)
    b8 = MD.read_bytes()
    st, e = api.call("/api/queue", {"version": r8["version"], "op": "move", "id": 999, "before": 0})
    st2, e2 = api.call("/api/queue", {"version": r8["version"], "op": "reorder", "order": [1, 2]})
    ok("Q8 a move naming no real item, and the old whole-list 'reorder', are refused (400); nothing changes", st == 400 and st2 == 400 and "out of date" in e2.get("error", "") and MD.read_bytes() == b8, (st, e, st2, e2))

    # in-process: CRLF + BOM survive, and the same operations on a copy of the REAL md
    crlf = b"\xef\xbb\xbf" + FIXTURE.replace("\n", "\r\n").encode("utf-8")
    dc = qt.Doc(crlf); pc = qt.parse(dc)
    err = qt.apply_queue_op(dc, {"op": "move", "id": T(pc, "Add Google Analytics")["id"], "before": T(pc, "Running bullet C")["id"]})
    out = dc.to_bytes()
    ok("Q9 a CRLF file with a BOM keeps both; a move changes only the moved line's place", err is None and out.startswith(b"\xef\xbb\xbf") and out.count(b"\r\n") == crlf.count(b"\r\n") and b"\n" not in out.replace(b"\r\n", b"")
       and sorted(out.split(b"\r\n")) == sorted(crlf.split(b"\r\n")) and out != crlf, err)
    real = REAL_MD.read_bytes(); dr = qt.Doc(real); pr = qt.parse(dr); sec_r = queue_section(real.decode("utf-8-sig").replace("\r\n", "\n"))
    ok("Q10 the REAL md (read only, in memory): every checkbox line is an item and the open count matches the file",
       len(pr["queue"]) == sum(1 for l in sec_r if ITEM_LINE.match(l)) and pr["counts"]["open"] == sum(1 for l in sec_r if OPEN_LINE.match(l)) and pr["counts"]["open"] >= 1, (len(pr["queue"]), pr["counts"]))
    w_r = [i for i in pr["queue"] if i["state"] != "done"]
    if len(w_r) >= 2 and len({i["group"] for i in w_r}) >= 2:
        last, first = w_r[-1], w_r[0]
        dr2 = qt.Doc(real); err = qt.apply_queue_op(dr2, {"op": "move", "id": last["id"], "before": first["id"]})
        a, b = real.decode("utf-8-sig").splitlines(), dr2.to_bytes().decode("utf-8-sig").splitlines()
        mline = next(l for l in a if (lambda m: m and m.group(3) == last["text"])(qt.ITEM_RE.match(l)))
        rest = lambda ls: [l for l in ls if l != mline and not (l[:1] in (" ", "\t") and l.strip())]     # its own sub-bullets travel with it
        ok("Q10b ...the QA repro on the real md: the last open item dragged to the top moves only its own line(s); every heading and item keeps its place",
           err is None and rest(b) == rest(a) and b.index(mline) < a.index(mline) and qt.parse(qt.Doc(dr2.to_bytes()))["counts"] == pr["counts"], err)
        dr3 = qt.Doc(real); err = qt.apply_queue_op(dr3, {"op": "add", "title": "Zz real add"}); p3 = qt.parse(dr3)
        z = next(i for i in p3["queue"] if i["title"] == "Zz real add"); after_z = p3["queue"][z["id"] + 1:]
        ok("Q10c ...an add on the real md lands in the first group with open work, above every done item after it in that group, not under the archive",
           err is None and z["group"] == w_r[0]["group"] and all(i["state"] == "done" or i["group"] != z["group"] for i in after_z) and z["id"] < len(p3["queue"]) - 1, (z, err))

    # ----------------------------------------------------------------------------- API: the inbox
    st, t = api.call("/api/todo?sync=1")
    byid = {i["id"]: i for i in t["items"]}
    ok("T1 GET /api/todo: 6 categories (the seed's 'shadow' and 'upgrades' merged into rounds), counts, and the live round + Shadow items folded into rounds", st == 200 and [c["id"] for c in t["categories"]] == ["rounds", "maps", "feedback", "social", "setup", "mine"]
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
    MD.write_bytes(md_bytes0)                                              # and the fixture queue again
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
        ok("U1 the Queue tab sits last in the bar, after Launch and Social (2026-11-08), tooltip says Ctrl+Alt+7", tab.count() and "Ctrl+Alt+7" in (tab.get_attribute("title") or "") and tab.inner_text().startswith("Queue")
           and pg.evaluate("[...document.querySelectorAll('.app-tabs a')].map(a => a.dataset.view)")[-3:] == ["launch", "social", "queue"], tab.get_attribute("title"))
        pg.keyboard.press("Control+Alt+5"); pg.wait_for_timeout(200)
        v5 = pg.evaluate("[document.getElementById('app-launch').hidden, document.getElementById('app-queue').hidden]")
        pg.keyboard.press("Control+Alt+7"); pg.wait_for_timeout(200)
        v6 = pg.evaluate("[document.getElementById('app-launch').hidden, document.getElementById('app-queue').hidden, location.hash]")
        ok("U2 Ctrl+Alt+5 shows Launch, Ctrl+Alt+7 brings Queue back (#queue)", v5 == [False, True] and v6 == [True, False, "#queue"], (v5, v6))
        st, t = api.call("/api/todo?sync=0")
        nopen = sum(1 for i in t["items"] if i["status"] != "done")
        bdg = pg.locator("#app-queue .app-tabs a[data-view='queue'] .bdg").first
        ok("U3 the badge counts the open items", bdg.is_visible() and bdg.inner_text() == str(nopen), (bdg.inner_text(), nopen))
        qn = pg.locator(".qt-item").count()
        qq = api.call("/api/queue")[1]["queue"]; n_work = sum(1 for i in qq if i["state"] != "done")
        ok("U4 Claude's queue renders one row per item ('- [ ]' ones too): the working ones in the list, the done ones in one collapsed Done fold, Log folded at the bottom",
           qn == len(qq) == 15 and pg.locator("[data-list='queue'] .qt-item").count() == n_work == 8 and pg.locator("details[data-fold='done:queue'] .qt-item").count() == len(qq) - n_work
           and not pg.locator("details[data-fold='done:queue']").first.get_attribute("open")
           and pg.locator("details[data-fold='Log']").count() == 1 and not pg.locator("details[data-fold='Log']").first.get_attribute("open"), (qn, n_work))
        heads = pg.evaluate("[...document.querySelectorAll(\"[data-list='queue'] > .qt-qgh .nm\")].map(h => h.textContent)")
        seq = pg.evaluate("[...document.querySelectorAll(\"[data-list='queue'] > .qt-qgh, [data-list='queue'] > .qt-item\")].map(r => r.classList.contains('qt-qgh') ? 'H:' + r.querySelector('.nm').textContent : r.querySelector('.qt-t').textContent)")
        ok("U4a the md's group headings are drawn in the list (only groups with open work), each above its own items; Confirm Google Remote Desktop shows under 'Needs you'",
           heads == ["No input from you: Claude works through these in order", "Claude does the work, and you review it later", "Needs you"]
           and seq == ["H:No input from you: Claude works through these in order", "Running bullet C", "Pending bullet D", "H:Claude does the work, and you review it later", "Push package", "Media kit page",
                       "Upgrade the remaining countries", "Email content for Kit", "H:Needs you", "Confirm Google Remote Desktop is working", "Add Google Analytics"], seq)
        hdr = pg.locator(".qt-col[data-col='claude'] > h2 span").inner_text()
        ok("U4c the column count is the real open count (8, running and held included)", hdr.startswith("8 open") and "1 running" in hdr and "1 held" in hdr, hdr)
        pg.locator(".qt-item .qt-trow[role=button]").first.click()
        ok("U4b a card expands to its details", pg.locator(".qt-item .qt-d").count() == 1, pg.locator(".qt-item .qt-d").count())
        ok("U5 categories are folded by default, each header shows its counts", pg.locator(".qt-cat").count() == 6 and pg.locator(".qt-cat.open").count() == 0
           and pg.locator(".qt-cat[data-cat='maps'] .cnt .n").first.inner_text() == "3", pg.locator(".qt-cat[data-cat='maps'] .cnt").inner_text())
        pg.locator(".qt-cat[data-cat='maps'] .qt-catb").click()
        ok("U5b a category opens to its rows", pg.locator(".qt-cat[data-cat='maps'].open .qt-todo").count() == 3, pg.locator(".qt-cat[data-cat='maps'].open .qt-todo").count())
        pg.locator(".qt-fchip[data-cat='setup']").click(); pg.wait_for_timeout(100)
        pg.locator(".qt-fchip[data-cat='feedback']").click(); pg.wait_for_timeout(100)
        n_after = pg.locator(".qt-cat").count(); bdg2 = bdg.inner_text()
        pg.reload(); pg.wait_for_selector("#app-queue:not([hidden]) .qt-cat", timeout=20000); pg.wait_for_timeout(300)
        ok("U6 filter chips hide categories (badge drops the hidden ones) and the choice survives a reload",
           n_after == 4 and int(bdg2) == nopen - 1 and pg.locator(".qt-cat").count() == 4 and pg.locator(".qt-cat[data-cat='feedback']").count() == 0 and pg.locator(".qt-cat[data-cat='maps'].open").count() == 1, (n_after, bdg2, pg.locator(".qt-cat").count()))
        pg.locator(".qt-more[data-act='cat-filter-all']").click(); pg.wait_for_timeout(150)
        ok("U6b 'show all' brings them back", pg.locator(".qt-cat").count() == 6, pg.locator(".qt-cat").count())
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
        ok("U7b the checkbox marks it done: it leaves the category and lands first in the column's one Done section (newest first, still undoable)", next(i for i in t["items"] if i["id"] == ui_item["id"])["status"] == "done"
           and pg.locator(f".qt-cat[data-cat='mine'] .qt-todo[data-id='{ui_item['id']}']").count() == 0 and pg.locator(f"details[data-fold='done:todo'] .qt-todo[data-id='{ui_item['id']}']").count() == 1
           and pg.locator("details[data-fold='done:todo'] .qt-todo").first.get_attribute("data-id") == ui_item["id"] and pg.locator("details[data-fold='done:todo'] .qt-todo input[type=checkbox]").first.is_checked(), st)
        pg.evaluate("document.querySelector(\"details[data-fold='done:todo']\").open = true"); pg.wait_for_timeout(100)
        pg.locator(f"details[data-fold='done:todo'] .qt-todo[data-id='{ui_item['id']}'] input[type=checkbox]").uncheck(); pg.wait_for_timeout(300)
        ok("U7b2 unticking it in Done brings it back to its category", next(i for i in api.call("/api/todo?sync=0")[1]["items"] if i["id"] == ui_item["id"])["status"] == "seen" and pg.locator(f".qt-cat[data-cat='mine'] .qt-todo[data-id='{ui_item['id']}']").count() == 1, "")
        pg.locator(f".qt-todo[data-id='{ui_item['id']}'] input[type=checkbox]").check(); pg.wait_for_timeout(300)
        pg.locator(".qt-todo[data-id='map-c'] .qt-stsel").select_option("needs-changes"); pg.wait_for_timeout(300)
        st, t = api.call("/api/todo?sync=0")
        ok("U7c the status select writes needs-changes", next(i for i in t["items"] if i["id"] == "map-c")["status"] == "needs-changes" and pg.locator(".qt-todo[data-id='map-c'] .qt-stsel.needs-changes").count() == 1, st)

        # drag by the grip (pointer events): Claude's queue, a category, an item inside a category
        def drag(src_sel, dst_sel, where="before"):
            # both rows must be on screen: a mouse at off-screen coordinates presses nothing. Center the span between the
            # two rows (2026-11-11: the status strip above the columns pushed the list down, so "dst into view, then fix
            # src" scrolled dst off the top)
            pg.evaluate("""([a, b]) => { const r = document.getElementById('queue-root'), x = document.querySelector(a).getBoundingClientRect(), y = document.querySelector(b).getBoundingClientRect();
                r.scrollTop += (Math.min(x.top, y.top) + Math.max(x.bottom, y.bottom)) / 2 - 450; }""", [src_sel, dst_sel]); pg.wait_for_timeout(100)
            s = pg.locator(src_sel).first.bounding_box(); d = pg.locator(dst_sel).first.bounding_box()
            if s["y"] < 70 or s["y"] + s["height"] > 880:
                pg.evaluate("dy => { document.getElementById('queue-root').scrollTop += dy; }", s["y"] - 450); pg.wait_for_timeout(100)
                s = pg.locator(src_sel).first.bounding_box(); d = pg.locator(dst_sel).first.bounding_box()
            y = d["y"] + (3 if where == "before" else d["height"] - 3)
            pg.mouse.move(s["x"] + s["width"] / 2, s["y"] + s["height"] / 2); pg.mouse.down()
            pg.mouse.move(s["x"] + s["width"] / 2, s["y"] + 12, steps=3); pg.mouse.move(d["x"] + 30, y, steps=8); pg.mouse.move(d["x"] + 30, y + (1 if where == "before" else -1), steps=2)
            pg.mouse.up(); pg.wait_for_timeout(500)
        q0 = api.call("/api/queue")[1]["queue"]; nums_before = sorted(i["num"] for i in q0 if i["num"] is not None)
        work_titles = lambda: pg.evaluate("[...document.querySelectorAll(\"[data-list='queue'] > .qt-item .qt-t\")].map(t => t.textContent)")
        md_lines = lambda: MD.read_bytes().decode("utf-8").split("\n")
        fx = FIXTURE.split("\n")
        ga_line = "25. [ ] **Add Google Analytics.** You'd create the account."
        drag(".qt-item[data-idx='7'] .qt-grip", ".qt-item[data-idx='0']", "before")      # the QA repro: the last item of "Needs you" to the very top
        s8 = md_lines(); q8 = api.call("/api/queue")[1]["queue"]
        ok("U8 dragging the last card (in 'Needs you') above the first moves ONLY its line, to just above the first card; the 'Needs you' heading and Confirm Google Remote Desktop stay; nothing renumbered",
           s8.index(ga_line) == s8.index("- [~] **Running bullet C.** Found 3 bugs so far.") - 1 and [l for l in s8 if l != ga_line] == [l for l in fx if l != ga_line]
           and s8[s8.index("**Needs you.**") + 1].startswith("- [ ] **Confirm Google Remote Desktop") and sorted(i["num"] for i in q8 if i["num"] is not None) == nums_before
           and work_titles()[0] == "Add Google Analytics" and pg.locator("[data-list='queue'] > .qt-qgh").first.locator(".nm").inner_text().startswith("No input from you"), (s8[8:14], work_titles()[:3]))
        rd_line = "- [ ] **Confirm Google Remote Desktop is working.** Check it before the 13th. (added by Kevin 2026-11-10)"
        pg.locator(".qt-item[data-idx='7'] .qt-ib[data-act='move'][data-dir='-1']").click(); pg.wait_for_timeout(500)
        s8b = md_lines()
        ok("U8b the up arrow on the first card of a group crosses the heading: it lands at the end of the group above (after its last open item), only its line moved",
           s8b.index(rd_line) == s8b.index("18. [-] **Email content for Kit.** The welcome series.") + 1 and [l for l in s8b if l != rd_line] == [l for l in s8 if l != rd_line]
           and next(i for i in api.call("/api/queue")[1]["queue"] if i["title"] == "Confirm Google Remote Desktop is working")["group"] == 1, s8b[14:26])
        d_line = "- [ ] **Pending bullet D.** Looks for drift. (added by Kevin 2026-11-10)"
        drag(".qt-item[data-idx='2'] .qt-grip", ".qt-qgh[data-group='1']", "after")     # onto a heading: the top of that group
        s8c = md_lines()
        ok("U8c dropping a card on a group heading puts it first in that group (right under the heading); nothing else moves",
           s8c[s8c.index("**Claude does the work, and you review it later.**") + 1] == d_line and [l for l in s8c if l != d_line] == [l for l in s8b if l != d_line], s8c[12:20])
        pg.fill("#qt-qa-title", "Zz UI queue item"); pg.select_option("#qt-qa-group", "1")
        pg.locator("form[data-form='queue-add'] button[type=submit]").click(); pg.wait_for_timeout(500)
        s8d = md_lines(); zi = next((k for k, l in enumerate(s8d) if l.startswith("- [ ] **Zz UI queue item** (added by Kevin 20")), -1)
        ok("U8d add from the tab with a group picked lands after that group's last open item, as a '- [ ]' line; nothing else moves",
           zi > 0 and s8d[zi - 1] == rd_line and s8d[:zi] + s8d[zi + 1:] == s8c and "Zz UI queue item" in work_titles(), s8d[zi - 2:zi + 2] if zi > 0 else zi)
        ok("U8e after the drags, the arrow and the add, every heading, note and blank line in the Queue section is where it was",
           [l for l in s8d if not ITEM_LINE.match(l) and not l.startswith("   - ")] == [l for l in fx if not ITEM_LINE.match(l) and not l.startswith("   - ")], "")
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
        ext = MD.read_bytes() + ("- 2026-11-07: another external edit" + ("\r\n" if CRLF else "\n")).encode()
        MD.write_bytes(ext)
        pg.locator("[data-list='queue'] .qt-item:not(.st-hold) .qt-hold").first.click()
        pg.wait_for_function("(() => { const t = document.querySelector('.qt-toast'); return t && !t.hidden && t.textContent.includes('changed'); })()", timeout=5000)   # not the previous toast
        toast = pg.inner_text(".qt-toast")
        log_txt = pg.evaluate("document.querySelector(\"details[data-fold='Log']\").textContent")
        ok("U14 a Hold on a stale page is refused with the message and the list reloads; nothing was written", "changed" in toast and MD.read_bytes() == ext and log_txt.count("another external edit") == 1, (toast, MD.read_bytes() == ext, log_txt[-80:]))

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
    ok("R1 the real away_queue.md parses (items, the ticks above them, Waiting on Kevin, Log)", st == 200 and len(q["queue"]) >= 10 and q["counts"]["pending"] >= 1 and any(s["name"].startswith("Waiting") for s in q["sections"]), (st, q.get("counts")))
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
            n_real_open = sum(1 for l in queue_section(real_md_before.decode("utf-8-sig").replace("\r\n", "\n")) if OPEN_LINE.match(l))
            hdr = pg.locator(".qt-col[data-col='claude'] > h2 span").inner_text()
            ok(f"R4b real render at {w} px: one working card per open line in the real md ({n_real_open}), the header says so, every item is a card",
               pg.locator("[data-list='queue'] > .qt-item").count() == n_real_open and hdr.startswith(f"{n_real_open} open")
               and pg.locator(".qt-item").count() == sum(1 for l in queue_section(real_md_before.decode("utf-8-sig").replace("\r\n", "\n")) if ITEM_LINE.match(l)), (pg.locator("[data-list='queue'] > .qt-item").count(), n_real_open, hdr))
            ctx.close()
        b.close()
    ok("R5 the read-only run left the real files byte-identical", REAL_MD.read_bytes() == real_md_before and (ROOT / ".tmp" / "kevin_todo.json").read_bytes() == real_todo_before, "")
finally:
    srv2.kill()
    shutil.rmtree(SCRATCH, ignore_errors=True)

n = sum(1 for _, c, _ in checks if c)
print(f"\n{n}/{len(checks)} passed; screenshots in {SHOTS}\\queue-tab-*.png")
sys.exit(0 if n == len(checks) else 1)
