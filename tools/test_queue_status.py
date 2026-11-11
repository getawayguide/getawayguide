"""The Queue tab's status strip and held-back drawer (queue.js + tools/queue_status.py; Kevin, 2026-11-11: "let's queue
1-3,5": a "What's running now" strip, a system status line, one "Waiting on you" count, a held-back drawer).

Scratch only: a temp tree <scratch>/ with <scratch>/.tmp as QUEUE_TAB_DIR (away_queue.md, kevin_todo.json, away/lock.json,
push_package/, held_back/ with a COPY of the real restore.py and a made-up held_back.json) and its own pages under
<scratch>/el-salvador/ etc., served by the standalone `python tools/queue_tab.py --serve <port>`. The real .tmp, the
real pages and the photo server are never touched; probes (powercfg, the process list) are off under QUEUE_TAB_DIR.

  UNIT  next_reset: Fridays 2 pm (before, at, after, weekend); parse_powercfg; the "(started ...)" stamp comes back as
        `started` and leaves the summary
  RUN   the strip lists exactly the [~]/[>] items (not a pending line that only says "running"), with the stamp or none;
        the last hourly check is lock.json's
  SYS   watchdog/emails read from the flag files; the toggles create/remove ONLY watchdog_off (+ the stop file for off)
        and emails_off; a bad body is 400; read-only is 403 and writes nothing
  WAIT  rounds = open round- items with changes left (not applied, not done); push = package items in state open;
        writer = unanswered Writer threads (not resolved, not replied by Kevin) + open questions; shadow = open Shadow
        items; total = their sum
  HELD  the list: a restorable map (held), one whose page changed (changed, copy only), Meteora and Kosovo (copy only);
        preview = restore.py --dry-run and the page is untouched; restore needs confirm; a changed/copy item is refused
        (409) with its command; a confirmed restore puts the block back byte for byte, logs restored.json, reopens the
        push item through reopened.json (answers.json and done.json byte-identical); a second restore is refused; his
        next answer takes the item out of "reopened"
  UI    the strip draws the API's numbers; each part jumps (rounds/shadow open their rows, push opens its card, writer
        lists the articles); a job row scrolls to its queue item; the emails toggle flips the file; the drawer: confirm
        panel with the dry-run, Cancel changes nothing, Yes puts it back and the push item shows reopened; Kosovo shows
        the steps to copy; 393 px with no sideways scroll; no script errors
  Screenshots: .tmp/previews/queue-status-test-1440.png and -393.png (scratch data).

    python tools/test_queue_status.py
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
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
PREVIEWS = ROOT / ".tmp" / "previews"
PREVIEWS.mkdir(parents=True, exist_ok=True)
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


def call(port, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {}, method="POST" if body is not None else "GET")
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:
                return e.code, {}
        except (ConnectionResetError, ConnectionAbortedError):          # this box's loopback drops one now and then
            if attempt or body is not None:
                raise
            time.sleep(0.5)


def start(port, env):
    p = subprocess.Popen([sys.executable, str(ROOT / "tools" / "queue_tab.py"), "--serve", str(port)], env=dict(os.environ, **env, PYTHONIOENCODING="utf-8"),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == "win32" else 0)
    if not wait_port(port):
        p.kill(); raise RuntimeError("the scratch server did not start")
    return p


SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="queue_status_test_"))
assert not str(SCRATCH.resolve()).startswith(str(ROOT.resolve())), "not a scratch folder"
T = SCRATCH / ".tmp"
RL = T / "redline"
HB = T / "held_back"
PKG = T / "push_package"
ENV = {"QUEUE_TAB_DIR": str(T), "QUEUE_TAB_REDLINE": str(RL)}

BEFORE = "<h2 class=\"article-h2\" id=\"getting-there\">Getting There</h2>\r\n"
AFTER = "    <p>El Tunco is about 1 hour from San Salvador.</p>\r\n"
BLOCK = "<figure class=\"route-map\" data-map=\"el-tunco-routes\"><svg viewBox=\"0 0 10 10\"><text>EL TUNCO</text></svg></figure>\r\n"
PAGE_HEAD = "<!doctype html>\r\n<html><body>\r\n<p>Intro.</p>\r\n"
PAGE_TAIL = "</body></html>\r\n"
ORIGINAL = PAGE_HEAD + BEFORE + BLOCK + AFTER + PAGE_TAIL       # the page before the cut
CUT = PAGE_HEAD + BEFORE + AFTER + PAGE_TAIL                    # the working copy now


def seed():
    T.mkdir(parents=True)
    (T / "away").mkdir()
    (T / "away" / "lock.json").write_text(json.dumps({"owner": "interactive", "at": "2026-11-11 09:41:42"}), encoding="utf-8")
    (T / "away_queue.md").write_bytes((
        "# Away queue: test\r\n\r\n## Queue (in order)\r\n**No input from you.**\r\n"
        "- [~] **Build the strip.** Shows jobs. (added by Kevin 2026-11-11) (started 2026-11-11 09:47)\r\n"
        "- [~] **Old running thing.** No stamp here.\r\n"
        "- [ ] **Refresh menus.** Waits until no country upgrade is running.\r\n"
        "3. [>] **Numbered runner.** Also running (started 2026-11-10)\r\n"
        "- [x] **Done thing.** Done Nov 10: yes.\r\n").encode("utf-8"))
    items = [
        {"id": "round-athens-r5", "cat": "rounds", "kind": "review", "title": "Athens, round 5", "status": "new", "round": {"slug": "athens-r5", "changes": 32, "left": 30}},
        {"id": "round-mykonos-r4", "cat": "rounds", "kind": "review", "title": "Mykonos, round 4", "status": "seen", "round": {"slug": "mykonos-r4", "changes": 28, "left": 28}},
        {"id": "round-old-r1", "cat": "rounds", "kind": "review", "title": "Old, round 1", "status": "done", "round": {"slug": "old-r1", "changes": 5, "left": 5}},
        {"id": "round-zero-r2", "cat": "rounds", "kind": "review", "title": "Zero, round 2", "status": "new", "round": {"slug": "zero-r2", "changes": 5, "left": 0}},
        {"id": "upgrade-athens", "cat": "rounds", "kind": "review", "title": "Athens upgrade", "status": "new", "round": {"slug": "athens-r2", "changes": 27, "left": 0, "applied": True}},
        {"id": "shadow-athens", "cat": "rounds", "kind": "todo", "title": "Athens: answer the Shadow reviewer", "status": "new", "shadow_open": 28, "country": "Greece"},
        {"id": "shadow-done", "cat": "rounds", "kind": "todo", "title": "Done: answer the Shadow reviewer", "status": "done", "shadow_open": 3},
        {"id": "q1", "cat": "feedback", "kind": "question", "title": "Which ferry?", "status": "new"},
        {"id": "q2", "cat": "feedback", "kind": "question", "title": "Answered", "status": "done"},
        {"id": "t1", "cat": "mine", "kind": "todo", "title": "Buy film", "status": "new"},
    ]
    (T / "kevin_todo.json").write_text(json.dumps({"categories": [{"id": "rounds", "name": "Review"}, {"id": "feedback", "name": "Feedback for Claude"},
                                                                  {"id": "mine", "name": "My own to-dos"}], "items": items}, indent=1), encoding="utf-8")
    (RL / "_comments").mkdir(parents=True)
    # the live rounds the tab's to-do sync reads (a round without a proposal would be marked applied on the first GET)
    for slug, n, decided in (("athens-r5", 32, 2), ("mykonos-r4", 28, 0), ("zero-r2", 5, 5)):
        (RL / slug).mkdir()
        (RL / slug / "proposal.json").write_text(json.dumps({"article_dir": "Drafts/Upcoming/greece", "article": slug.split("-")[0] + ".html",
                                                             "title": slug, "changes": [{"id": f"c{i}"} for i in range(n)]}), encoding="utf-8")
        if decided:
            (RL / slug / "decisions.json").write_text(json.dumps({f"c{i}": "accept" for i in range(decided)}), encoding="utf-8")
    (RL / "_comments" / "greece__athens.json").write_text(json.dumps({"threads": [{"id": f"sr-a{i}", "author": "Shadow reviewer", "text": "s", "resolved": False} for i in range(28)]}), encoding="utf-8")
    th = [{"id": "wr-1", "author": "Writer", "text": "q?", "resolved": False},
          {"id": "wr-2", "author": "Writer", "text": "q2?", "resolved": False},
          {"id": "wr-3", "author": "Writer", "text": "q3?", "resolved": True},
          {"id": "wr-4", "author": "Writer", "text": "q4?", "resolved": False, "replies": [{"author": "Kevin", "text": "yes"}]}]
    (RL / "_comments" / "albania__himare.json").write_text(json.dumps({"threads": th}), encoding="utf-8")
    (RL / "_comments" / "albania__tirana.json").write_text(json.dumps({"threads": [{"id": "wr-9", "author": "Writer", "text": "x", "resolved": False}]}), encoding="utf-8")
    # the push package: one unanswered call (open), the held map (held + done), the Kosovo page (held + done)
    PKG.mkdir()
    pkg = {"id": "pkgtest", "calls": [{"id": "call-1", "num": 1, "title": "A call", "options": [{"id": "a", "label": "Keep", "recommended": True}, {"id": "b", "label": "Change"}]}],
           "sections": [{"id": "maps", "title": "Maps", "changes": [
               {"id": "map-el-tunco", "title": "Bus Routes to El Tunco (held back)", "page": "el-salvador/el-tunco.html"},
               {"id": "map-santa-ana", "title": "Bus Routes to Santa Ana (held back)", "page": "el-salvador/santa-ana.html"},
               {"id": "kosovo-page", "title": "Kosovo country page (held back)", "page": "Drafts/Upcoming/kosovo/index.html"}]}]}
    (PKG / "package.json").write_text(json.dumps(pkg, indent=1), encoding="utf-8")
    os.environ["PUSH_PACKAGE_DIR"] = str(PKG)
    try:
        import push_package as pp
        store = pp.empty_answers("pkgtest")
        for iid in ("map-el-tunco", "map-santa-ana", "kosovo-page"):
            pp.apply_answer(store, pkg, {"id": iid, "set": {"hold": True}, "seen": {}})
        pp.save_answers(store)
        for iid in ("map-el-tunco", "map-santa-ana", "kosovo-page"):
            pp.mark_done(iid, "Held back: off the page.", pkg)
    finally:
        del os.environ["PUSH_PACKAGE_DIR"]
    # pages and the held-back folder (restore.py is the real one, copied)
    (SCRATCH / "el-salvador").mkdir()
    (SCRATCH / "el-salvador" / "el-tunco.html").write_bytes(CUT.encode("utf-8"))
    (SCRATCH / "el-salvador" / "santa-ana.html").write_bytes(b"<p>Santa Ana changed since the cut.</p>\r\n")
    (SCRATCH / "Drafts" / "Upcoming" / "kosovo").mkdir(parents=True)
    (SCRATCH / "Drafts" / "Upcoming" / "kosovo" / "index.html").write_text("<h1>Kosovo</h1>", encoding="utf-8")
    HB.mkdir()
    shutil.copy2(ROOT / ".tmp" / "held_back" / "restore.py", HB / "restore.py")
    meta = {"el-tunco": {"page": "el-salvador/el-tunco.html", "data_map": "el-tunco-routes", "reason": "Kevin held it back from the push (2026-11-10)",
                         "config": "tools/itinerary_maps/el-tunco-routes.json", "text_before_cut": BEFORE, "text_after_cut": AFTER, "removed_chars": len(BLOCK)},
            "santa-ana": {"page": "el-salvador/santa-ana.html", "data_map": "santa-ana-routes", "reason": "Kevin held it back from the push (2026-11-10)",
                          "config": "x", "text_before_cut": "<p>not there</p>", "text_after_cut": "<p>either</p>", "removed_chars": 10}}
    (HB / "held_back.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (HB / "el-tunco.html").write_bytes(BLOCK.encode("utf-8"))
    (HB / "santa-ana.html").write_bytes(b"<figure data-map=\"santa-ana-routes\"></figure>")
    (HB / "meteora-new-route-map.html").write_text("<figure></figure>", encoding="utf-8")
    (T / "emails_off").write_text("off", encoding="utf-8")


def tree(d):
    return sorted(str(p.relative_to(d)).replace("\\", "/") for p in d.rglob("*") if p.is_file())


# ----------------------------------------------------------------------------- unit
def unit_checks():
    import queue_status as qs
    import queue_tab as qt
    nr = qs.next_reset
    ok("UNIT credits reset: Wed -> that Friday 2 pm; Fri 1 pm -> same day; Fri 2 pm -> next Friday; Sat -> next Friday",
       nr(datetime(2026, 11, 11, 9, 0)) == datetime(2026, 11, 13, 14, 0) and nr(datetime(2026, 11, 13, 13, 0)) == datetime(2026, 11, 13, 14, 0)
       and nr(datetime(2026, 11, 13, 14, 0)) == datetime(2026, 11, 20, 14, 0) and nr(datetime(2026, 11, 14, 10, 0)) == datetime(2026, 11, 20, 14, 0))
    sample = "    Power Setting GUID: 3c0bc021 (Turn off display after)\r\n    Current AC Power Setting Index: 0x00001c20\r\n    Current DC Power Setting Index: 0x00000258\r\n"
    ok("UNIT powercfg output -> AC 7200 s, DC 600 s; garbage -> None", qs.parse_powercfg(sample) == {"ac": 7200, "dc": 600} and qs.parse_powercfg("nope") == {"ac": None, "dc": None})
    doc = qt.Doc((T / "away_queue.md").read_bytes())
    q = qt.parse(doc)["queue"]
    a = next(i for i in q if i["title"] == "Build the strip")
    b = next(i for i in q if i["title"] == "Numbered runner")
    ok("UNIT the started stamp comes back as `started` and is left out of the summary (a date-only stamp works too)",
       a["started"] == "2026-11-11 09:47" and "started" not in a["summary"] and b["started"] == "2026-11-10" and next(i for i in q if i["title"] == "Old running thing")["started"] is None, (a, b))


# ----------------------------------------------------------------------------- API
def api_checks(port, ro_port):
    st, j = call(port, "/api/queue/status")
    ok("RUN GET /api/queue/status answers", st == 200 and j.get("ok"), (st, j))
    run = j["running"]
    ok("RUN the strip lists exactly the [~] and [>] items, not a pending line that only says 'running'",
       [r["title"] for r in run] == ["Build the strip", "Old running thing", "Numbered runner"], run)
    ok("RUN each job carries its stamp or none, and its group", run[0]["started"] == "2026-11-11 09:47" and run[1]["started"] is None and run[0]["group"] == "No input from you", run)
    ok("RUN the last hourly check is lock.json's", j["last_tick"] == {"at": "2026-11-11 09:41:42", "owner": "interactive"}, j["last_tick"])
    s = j["system"]
    ok("SYS read from the flags: watchdog on (no watchdog_off), emails off (emails_off), credits next Friday 2 pm; no probes under a test",
       s["watchdog"]["on"] is True and s["emails"]["on"] is False and s["credits"]["next"].endswith("T14:00") and s["watchdog"]["running"] is None and s["screen"] is None, s)
    w = {p["id"]: p for p in j["waiting"]["parts"]}
    ok("WAIT rounds = open round- items with changes left (athens-r5, mykonos-r4); not done, not 0 left, not an applied upgrade",
       w["rounds"]["n"] == 2 and w["rounds"]["ids"] == ["round-athens-r5", "round-mykonos-r4"] and w["rounds"]["changes"] == 58, w["rounds"])
    ok("WAIT push = the unanswered call only (held items are settled)", w["push"]["n"] == 1 and w["push"]["ids"] == ["call-1"], w["push"])
    ok("WAIT writer = 3 unanswered Writer threads (2 Himare, 1 Tirana; resolved and replied ones left out) + 1 open question",
       w["writer"]["n"] == 4 and [(a["title"], a["open"]) for a in w["writer"]["articles"]] == [("Himare", 2), ("Tirana", 1)] and w["writer"]["ids"] == ["q1"], w["writer"])
    ok("WAIT shadow = the open Shadow item (28 comments ride along)", w["shadow"]["n"] == 1 and w["shadow"]["threads"] == 28, w["shadow"])
    ok("WAIT the total is the sum of the parts", j["waiting"]["total"] == 2 + 1 + 4 + 1, j["waiting"]["total"])
    h = {x["key"]: x for x in j["held"]}
    ok("HELD the list: el-tunco held (restore), santa-ana changed (no restore), Meteora and Kosovo as steps to copy",
       list(h) == ["el-tunco", "santa-ana", "meteora", "kosovo-page"] and h["el-tunco"]["state"] == "held" and h["el-tunco"]["action"] == "restore"
       and h["santa-ana"]["state"] == "changed" and h["santa-ana"]["action"] is None and h["meteora"]["action"] == "copy" and h["kosovo-page"]["action"] == "copy"
       and h["el-tunco"]["title"] == "Bus Routes to El Tunco" and h["el-tunco"]["push_id"] == "map-el-tunco", h)

    # toggles: only the flag files change
    before = tree(T)
    st, j = call(port, "/api/queue/status", {"op": "watchdog", "on": False})
    made = set(tree(T)) - set(before)
    ok("SYS watchdog off -> watchdog_off and sleep_watchdog.stop appear (plus the toggle log), nothing else",
       st == 200 and j["system"]["watchdog"]["on"] is False and made == {"watchdog_off", "sleep_watchdog.stop", "system_toggles.log"}, (st, made))
    st, j = call(port, "/api/queue/status", {"op": "watchdog", "on": True})
    ok("SYS watchdog on -> watchdog_off is gone (no process is started from here)", st == 200 and j["system"]["watchdog"]["on"] is True and not (T / "watchdog_off").exists(), st)
    st, j = call(port, "/api/queue/status", {"op": "emails", "on": True})
    ok("SYS emails on -> emails_off removed", st == 200 and j["system"]["emails"]["on"] is True and not (T / "emails_off").exists(), st)
    st, j = call(port, "/api/queue/status", {"op": "emails", "on": False})
    ok("SYS emails off -> emails_off back", st == 200 and j["system"]["emails"]["on"] is False and (T / "emails_off").is_file(), st)
    st1, _ = call(port, "/api/queue/status", {"op": "emails"})
    st2, _ = call(port, "/api/queue/status", {"op": "sleep", "on": True})
    ok("SYS a toggle without on:true/false, or an unknown one, is 400", st1 == 400 and st2 == 400, (st1, st2))
    before = tree(T)
    st1, _ = call(ro_port, "/api/queue/status", {"op": "emails", "on": True})
    st2, _ = call(ro_port, "/api/queue/held", {"op": "restore", "key": "el-tunco", "confirm": True})
    st3, g = call(ro_port, "/api/queue/status")
    ok("SYS read-only: toggles and restores are 403, nothing written; GET still works", st1 == 403 and st2 == 403 and st3 == 200 and tree(T) == before, (st1, st2, st3))

    # held back
    page = SCRATCH / "el-salvador" / "el-tunco.html"
    ans0, done0 = (PKG / "answers.json").read_bytes(), (PKG / "done.json").read_bytes()
    st, j = call(port, "/api/queue/held", {"op": "preview", "key": "el-tunco"})
    ok("HELD preview runs restore.py --dry-run and leaves the page alone", st == 200 and j["ok"] and j["text"].startswith(f"would restore {len(BLOCK)} chars") and page.read_bytes() == CUT.encode(), (st, j))
    st, j = call(port, "/api/queue/held", {"op": "restore", "key": "el-tunco"})
    ok("HELD a restore without confirm:true is 400 and changes nothing", st == 400 and page.read_bytes() == CUT.encode(), (st, j))
    st1, j1 = call(port, "/api/queue/held", {"op": "restore", "key": "santa-ana", "confirm": True})
    st2, j2 = call(port, "/api/queue/held", {"op": "restore", "key": "kosovo-page", "confirm": True})
    st3, _ = call(port, "/api/queue/held", {"op": "restore", "key": "nope", "confirm": True})
    ok("HELD a changed page or a copy-only item is refused (409) with its command; an unknown key is 404",
       st1 == 409 and "restore.py santa-ana" in j1.get("command", "") and st2 == 409 and "gen_sitemap" in j2.get("command", "") and st3 == 404, (st1, j1, st2, st3))
    st, j = call(port, "/api/queue/held", {"op": "restore", "key": "el-tunco", "confirm": True})
    ok("HELD a confirmed restore puts the block back byte for byte", st == 200 and page.read_bytes() == ORIGINAL.encode("utf-8"), (st, j.get("error"), page.read_bytes()[:200]))
    h = {x["key"]: x for x in j.get("held", [])}
    ok("HELD after it: the row says back on the page, restored.json logs it", h.get("el-tunco", {}).get("state") == "back" and h["el-tunco"]["action"] is None
       and json.loads((HB / "restored.json").read_text(encoding="utf-8"))["el-tunco"]["push_id"] == "map-el-tunco", h.get("el-tunco"))
    ok("HELD his answers.json and Claude's done.json are byte-identical; the reopen is in reopened.json",
       (PKG / "answers.json").read_bytes() == ans0 and (PKG / "done.json").read_bytes() == done0 and "map-el-tunco" in json.loads((PKG / "reopened.json").read_text(encoding="utf-8"))["items"])
    st, p = call(port, "/api/push")
    ps = p.get("status", {}).get("map-el-tunco", {})
    ok("HELD the push item is open again (reopened), so it waits on him", ps.get("state") == "open" and ps.get("reopened") is True and "held-back drawer" in ps.get("text", ""), ps)
    w = {x["id"]: x for x in j["waiting"]["parts"]}
    ok("WAIT the reopened push item joins the push part", w["push"]["n"] == 2 and "map-el-tunco" in w["push"]["ids"] and w["push"]["reopened"] == ["map-el-tunco"], w["push"])
    st, j = call(port, "/api/queue/held", {"op": "restore", "key": "el-tunco", "confirm": True})
    ok("HELD a second restore is refused (it is already back) and the page is unchanged", st == 409 and page.read_bytes() == ORIGINAL.encode("utf-8"), (st, j))
    # his next answer takes it out of "reopened": looks good -> settled
    a = (call(port, "/api/push")[1].get("answers") or {}).get("map-el-tunco", {})
    st, r = call(port, "/api/push", {"op": "answer", "id": "map-el-tunco", "package": "pkgtest", "set": {"ok": True, "hold": False},
                                     "seen": {"ok": (a.get("rev") or {}).get("ok"), "hold": (a.get("rev") or {}).get("hold")}})
    ps = (r.get("status") or {}).get("map-el-tunco", {})
    ok("HELD his new answer (looks good) settles it; no longer reopened", st == 200 and ps.get("state") == "settled" and not ps.get("reopened"), (st, ps))
    # put the page back to the cut for the UI run, and undo the answer + reopen
    page.write_bytes(CUT.encode("utf-8"))
    (PKG / "answers.json").write_bytes(ans0)
    (PKG / "reopened.json").unlink()
    (HB / "restored.json").unlink()


# ----------------------------------------------------------------------------- UI
def ui_checks(port):
    from playwright.sync_api import sync_playwright
    errs = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()

        def page(w, h=900):
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            ctx.add_init_script(f"window.QUEUE_API = 'http://127.0.0.1:{port}'; try {{ if (!sessionStorage.getItem('seeded')) {{ localStorage.clear(); sessionStorage.setItem('seeded', '1'); }} localStorage.setItem('editorAutosave', 'off'); }} catch (e) {{}}")
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errs.append(str(e)[:300]) if "loadAlbums" not in str(getattr(e, "stack", "")) else None)
            pg.route("http://127.0.0.1:5003/**", lambda r: r.fulfill(status=200, json={}, headers={"Access-Control-Allow-Origin": "*"}))
            pg.goto((ROOT / "editor.html").as_uri())
            pg.wait_for_timeout(600)
            pg.evaluate("showView('queue')")
            pg.wait_for_selector("#qs .qs-part", timeout=20000)
            pg.wait_for_selector(".qt-cols .qt-item", timeout=20000)
            pg.wait_for_timeout(400)
            return ctx, pg

        ctx, pg = page(1440)
        st, api = call(port, "/api/queue/status")
        d = pg.evaluate("""() => ({ total: +document.querySelector('#qs [data-total]').textContent,
            parts: [...document.querySelectorAll('#qs .qs-part')].map(b => [b.dataset.part, +b.querySelector('b').textContent]),
            jobs: [...document.querySelectorAll('#qs .qs-job')].map(j => j.textContent.replace(/\\s+/g, ' ').trim()),
            tick: document.querySelector('#qs [data-tick]').textContent, sys: document.querySelector('#qs .qs-sys').textContent.replace(/\\s+/g, ' '),
            order: [...document.querySelector('.qt-wrap').children].map(x => x.id || x.className) })""")
        ok("UI the strip draws the API's total and parts", d["total"] == api["waiting"]["total"] and d["parts"] == [[p["id"], p["n"]] for p in api["waiting"]["parts"]], (d, api["waiting"]))
        ok("UI Running now: 3 jobs, the stamped one says when it started, the other says not recorded; the last check is shown",
           len(d["jobs"]) == 3 and "started" in d["jobs"][0] and "start not recorded" in d["jobs"][1] and d["tick"].startswith("Last hourly check"), d)
        ok("UI the system line: Watchdog On, Screen off (not read here), Emails Off, Credits reset Fri",
           "Watchdog On" in d["sys"] and "Emails Off" in d["sys"] and "Credits reset Fri" in d["sys"] and "Screen off after" in d["sys"], d["sys"])
        ok("UI order: strip, push package, held-back drawer, then the columns", d["order"][:2] == ["qt-top", "qs"] and d["order"].index("qt-push") < d["order"].index("qs-held") < d["order"].index("qt-cols"), d["order"])
        pg.screenshot(path=str(PREVIEWS / "queue-status-test-1440.png"))

        # jumps
        pg.click('#qs .qs-part[data-part="rounds"]'); pg.wait_for_timeout(500)
        j = pg.evaluate("() => ['round-athens-r5', 'round-mykonos-r4'].map(id => { const r = document.querySelector(`.qt-todo[data-id='${id}']`); if (!r) return null; const b = r.getBoundingClientRect(); return b.top >= 0 && b.bottom <= innerHeight + 200; })")
        ok("UI Rounds opens the Review category and its countries and scrolls to the rounds", j == [True, True] or (j[0] is True and j[1] is not None), j)
        pg.click('#qs .qs-part[data-part="shadow"]'); pg.wait_for_timeout(500)
        ok("UI Shadow opens its row", pg.evaluate("() => !!document.querySelector(\".qt-todo[data-id='shadow-athens']\")"))
        pg.click('#qs .qs-part[data-part="push"]'); pg.wait_for_timeout(600)
        ok("UI Push opens the push package at the open call", pg.evaluate("() => document.querySelector('#qt-push').classList.contains('open') && !!document.querySelector('#pp-e-call-1')"))
        pg.click('#qs .qs-part[data-part="writer"]'); pg.wait_for_timeout(300)
        wl = pg.evaluate("() => [...document.querySelectorAll('#qs .qs-wlist a')].map(a => [a.dataset.act, a.dataset.rel || a.dataset.id, a.textContent.replace(/\\s+/g, ' ').trim()])")
        ok("UI Writer lists its articles (open in the editor) and the open question", len(wl) == 3 and wl[0][0] == "open-rel" and "2 questions" in wl[0][2] and wl[2][0] == "jump" and wl[2][1] == "q1", wl)
        pg.click("#qs .qs-job >> nth=0"); pg.wait_for_timeout(300)
        ok("UI a job row scrolls to its queue item", pg.evaluate("() => { const r = document.querySelector('.qt-item[data-id=\"0\"]'); const b = r.getBoundingClientRect(); return b.top >= 0 && b.bottom <= innerHeight; }"))

        # the emails toggle
        pg.click('#qs .qs-tg[data-op="emails"]'); pg.wait_for_timeout(700)
        on = pg.evaluate("() => document.querySelector('#qs .qs-tg[data-op=\"emails\"]').getAttribute('aria-pressed')")
        ok("UI the emails toggle turns them on: emails_off is gone", on == "true" and not (T / "emails_off").exists(), on)
        pg.click('#qs .qs-tg[data-op="emails"]'); pg.wait_for_timeout(700)
        ok("UI and off again: emails_off is back", (T / "emails_off").is_file())

        # the drawer
        page_file = SCRATCH / "el-salvador" / "el-tunco.html"
        pg.click("#qs-held > summary"); pg.wait_for_timeout(400)
        rows = pg.evaluate("() => [...document.querySelectorAll('#qs-held .qs-hrow')].map(r => [r.dataset.key, (r.querySelector('.qs-hact button') || {}).dataset ? r.querySelector('.qs-hact button').dataset.act : null])")
        ok("UI the drawer lists 4: el-tunco can go back, the others give steps", rows == [["el-tunco", "qs-hb-ask"], ["santa-ana", "qs-hb-steps"], ["meteora", "qs-hb-steps"], ["kosovo-page", "qs-hb-steps"]], rows)
        pg.click('#qs-held [data-act="qs-hb-ask"][data-key="el-tunco"]'); pg.wait_for_timeout(1500)
        cf = pg.evaluate("() => { const c = document.querySelector('#qs-held .qs-confirm'); return c ? { t: c.textContent.replace(/\\s+/g, ' '), yes: !c.querySelector('[data-act=\"qs-hb-ok\"]').disabled } : null; }")
        ok("UI Put it back asks first, in the page, with the dry run", cf and "would restore" in cf["t"] and "reopens" in cf["t"] and cf["yes"], cf)
        pg.click('#qs-held [data-act="qs-hb-cancel"]'); pg.wait_for_timeout(300)
        ok("UI Cancel closes it and the page is untouched", pg.evaluate("() => !document.querySelector('#qs-held .qs-confirm')") and page_file.read_bytes() == CUT.encode("utf-8"))
        pg.click('#qs-held [data-act="qs-hb-ask"][data-key="el-tunco"]'); pg.wait_for_timeout(1500)
        t0 = time.time()
        pg.click('#qs-held [data-act="qs-hb-ok"]')
        try:
            pg.wait_for_function("() => /back on the page/.test(document.querySelector('#qs-held .qs-hrow[data-key=\"el-tunco\"]').textContent)", timeout=20000)
        except Exception:
            pass
        print(f"  (put back from the page in {time.time() - t0:.1f} s)")
        st_row = pg.evaluate("() => document.querySelector('#qs-held .qs-hrow[data-key=\"el-tunco\"]').textContent.replace(/\\s+/g, ' ')")
        ok("UI Yes puts the map back: the page matches the original and the row says so", page_file.read_bytes() == ORIGINAL.encode("utf-8") and "back on the page" in st_row, st_row)
        pg.wait_for_timeout(800)
        ps = pg.evaluate("() => (window.__push.status['map-el-tunco'] || {})")
        ok("UI the push package shows the item open again (reopened)", ps.get("state") == "open" and ps.get("reopened") is True, ps)
        pg.click('#qs-held [data-act="qs-hb-steps"][data-key="kosovo-page"]'); pg.wait_for_timeout(300)
        code = pg.evaluate("() => (document.querySelector('#qs-cmd-kosovo-page') || {}).textContent || ''")
        ok("UI Kosovo shows the relaunch steps to copy instead of running them", "Move-Item" in code and "gen_search_index" in code, code)
        ctx.close()

        ctx, pg = page(393, 860)
        sw = pg.evaluate("() => ({ doc: document.documentElement.scrollWidth, root: document.querySelector('#queue-root').scrollWidth, cw: document.querySelector('#queue-root').clientWidth })")
        pg.screenshot(path=str(PREVIEWS / "queue-status-test-393.png"))
        pg.click("#qs-held > summary"); pg.wait_for_timeout(300)
        pg.click('#qs-held [data-act="qs-hb-steps"][data-key="santa-ana"]'); pg.wait_for_timeout(300)
        sw2 = pg.evaluate("() => ({ doc: document.documentElement.scrollWidth, root: document.querySelector('#queue-root').scrollWidth, cw: document.querySelector('#queue-root').clientWidth })")
        ok("UI 393 px: no sideways scroll, drawer open too", sw["doc"] <= 393 and sw["root"] <= sw["cw"] and sw2["doc"] <= 393 and sw2["root"] <= sw2["cw"], (sw, sw2))
        ctx.close()
        b.close()
    ok("UI no script errors", not errs, errs[:3])


def main():
    seed()
    unit_checks()
    port, ro = free_port(), free_port()
    srv = start(port, ENV)
    rsrv = start(ro, dict(ENV, QUEUE_TAB_READONLY="1"))
    try:
        api_checks(port, ro)
        ui_checks(port)
    finally:
        for p in (srv, rsrv):
            p.kill(); p.wait(timeout=10)
        shutil.rmtree(SCRATCH, ignore_errors=True)
    n_ok = sum(1 for c in checks if c[1])
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
