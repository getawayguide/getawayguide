"""The Queue tab's Push package section (queue.js + tools/push_package.py; Kevin, 2026-11-10: "make the push package a
separate section on the queue tab ... I should be able to respond to each one of them and click on the options offered").

Scratch only: a temp QUEUE_TAB_DIR holding away_queue.md, kevin_todo.json and push_package/ (a COPY of the real
package.json and its shots; answers.json starts empty), served by the standalone `python tools/queue_tab.py --serve
<port>`; the editor is opened from disk with window.QUEUE_API pointed at it. The real .tmp/push_package/answers.json,
.tmp/kevin_todo.json and the photo server are never touched.

  API   GET: the package and no answers; POST one field: saved with its revision, package.json byte-identical; a stale
        `seen` -> 409 and nothing written; fields not named are never touched; bad option / unknown id / field of the
        wrong kind -> 400; read-only -> 403; the shots route serves a shot and refuses anything else; a rebuilt package
        (new id) ignores the old answers and archives them on its first write
  MIGR  import_todo: the Nov 10 inbox items -> answers ("update"/"good"/done = the recommended option with his words
        kept, a reply naming an option = that option, done change = looks good, seen = seen); an answer already made in
        the section is never overwritten; an empty "push" category is retired from the to-do store, a non-empty one kept
  UI    renders every call (counts read from the package; options as buttons, one Recommended each, the diff, the shots) and the change sections;
        choose an option, change it, clear it; a reply (Enter), edit; hold a change back, looks good, a note; the summary
        strip follows (calls answered, held, looks good); everything survives a reload; a stale tab: its write to a field
        another tab changed is refused with a toast and the newer answer shown, its write to another field goes through;
        the viewer: before + after, Desktop/Phone, next, an option chosen in it, Esc; 393 px without sideways scroll;
        a server without /api/push still shows the queue with a "re-click the shortcut" line; no script errors
  SETTLED (Nov 11, Kevin: "The changes should address themselves as I work through ... create a 'addressed' section"):
        every item is open / working / settled; the recommended pick or "looks good" settles at once, a reply, another
        pick, a hold or a note waits for Claude (done.json via `push_package.py done`), a newer answer reopens it; the
        Settled fold lists them and a row opens the full card; the header counts need-you / Claude-is-on / settled /
        checks green; the checks are live, and a scratch package never starts them (no staging or lint of the real repo)
  Screenshots at 1440 and 393 go to .tmp/previews/queue-push-*.png.

    python tools/test_queue_push.py
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
sys.path.insert(0, str(ROOT / "tools"))
REAL_PKG = ROOT / ".tmp" / "push_package"
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


def call(port, path, body=None, _retry=True):
    if body is None and _retry:                         # this box's loopback drops a GET now and then (memory: loopback freeze)
        try:
            return call(port, path, None, False)
        except (ConnectionResetError, ConnectionAbortedError):
            time.sleep(0.5)
            return call(port, path, None, False)
    req =urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw.decode("utf-8"))
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def start(port, env):
    p = subprocess.Popen([sys.executable, str(ROOT / "tools" / "queue_tab.py"), "--serve", str(port)], env=dict(os.environ, **env, PYTHONIOENCODING="utf-8"),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == "win32" else 0)
    if not wait_port(port):
        p.kill(); raise RuntimeError("the scratch server did not start")
    return p


SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="queue_push_test_"))
assert not str(SCRATCH.resolve()).startswith(str(ROOT.resolve())), "not a scratch folder"
PKG = SCRATCH / "push_package"
ANS = PKG / "answers.json"
TODO = SCRATCH / "kevin_todo.json"


def answers():
    return json.loads(ANS.read_text(encoding="utf-8"))["answers"] if ANS.is_file() else {}


N = {}                                  # the package's own counts (calls, options, sections), read at seed time


def seed():
    PKG.mkdir(parents=True)
    # The fixture is a package WITH calls (call-1..call-6, the nmlaunch section). After a push the live package.json starts
    # empty, so fall back to the newest archived package-<id>.json that still has them (2026-11-13: the suite failed on
    # KeyError 'call-1' the first time it ran after the Nov 11 push reset the package).
    fixture = REAL_PKG / "package.json"
    if "call-1" not in {c.get("id") for c in json.loads(fixture.read_text(encoding="utf-8")).get("calls", [])}:
        archived = sorted(REAL_PKG.glob("package-push-*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        fixture = next(f for f in archived if "call-1" in {c.get("id") for c in json.loads(f.read_text(encoding="utf-8")).get("calls", [])})
    shutil.copy2(fixture, PKG / "package.json")
    pk = json.loads((PKG / "package.json").read_text(encoding="utf-8"))
    N.update(calls=len(pk["calls"]), opts=sum(len(c["options"]) for c in pk["calls"]),
             secs=len(pk["sections"]) + (1 if pk.get("unreviewed_pages") else 0), id=pk["id"],
             nm=next((len(x["changes"]) for x in pk["sections"] if x["id"] == "nmlaunch"), 0),
             items=len(pk["calls"]) + sum(len(x["changes"]) for x in pk["sections"]) + len(pk.get("unreviewed_pages") or []))
    shutil.copytree(REAL_PKG / "shots", PKG / "shots")
    (SCRATCH / "away_queue.md").write_text("# Away queue: test\n\n## Queue (in order)\n- [ ] **One.** Something.\n2. [ ] **Two.** Else.\n", encoding="utf-8")
    TODO.write_text(json.dumps({"categories": [{"id": "rounds", "name": "Review"}, {"id": "mine", "name": "My own to-dos"}, {"id": "push", "name": "Push package"}],
                                "items": [{"id": "t1", "cat": "mine", "kind": "todo", "title": "Buy film", "status": "new"}]}, indent=1), encoding="utf-8")
    (SCRATCH / "redline" / "_comments").mkdir(parents=True)


# ----------------------------------------------------------------------------- migration + store unit checks (no server)
def migration_checks():
    import push_package as pp
    import queue_tab as qt
    os.environ["PUSH_PACKAGE_DIR"] = str(PKG)
    pkg, _v = pp.load_package()
    store = pp.empty_answers(pkg["id"])
    pp.apply_answer(store, pkg, {"id": "call-2", "set": {"note": "made in the section first"}, "seen": {}})   # must survive the import
    items = [{"id": "push-overview", "cat": "push", "status": "new"},
             {"id": "push-call-1", "cat": "push", "status": "done", "reply": "update", "replied_at": "2026-11-10T22:54:15"},
             {"id": "push-call-2", "cat": "push", "status": "done", "reply": "update", "note": "from the inbox"},
             {"id": "push-call-3", "cat": "push", "status": "done", "reply": "Relink to Booking.com"},
             {"id": "push-call-5", "cat": "push", "status": "done", "reply": None},
             {"id": "push-call-6", "cat": "push", "status": "new", "reply": None},
             {"id": "push-arm-fn-visa", "cat": "push", "status": "done"},
             {"id": "push-kosovo-page", "cat": "push", "status": "seen"},
             {"id": "push-geo-transfer", "cat": "push", "status": "new"},
             {"id": "push-nope", "cat": "push", "status": "done"},
             {"id": "t9", "cat": "mine", "status": "done"}]
    did = pp.import_todo(items, pkg, store)
    A = store["answers"]
    ok("MIGR call 1 'update' + done -> the recommended option, his word kept as the reply, via My to-do",
       A["call-1"]["choice"] == "keep-the-new-line" and A["call-1"]["recommended"] and A["call-1"]["reply"] == "update" and A["call-1"]["via"] == "My to-do"
       and A["call-1"]["times"]["reply"] == "2026-11-10T22:54:15", A.get("call-1"))
    ok("MIGR call 3 'Relink to Booking.com' -> that option, not the recommended one",
       A["call-3"]["choice"] == "relink-to-booking-com" and not A["call-3"]["recommended"] and A["call-3"]["reply"] == "Relink to Booking.com", A.get("call-3"))
    ok("MIGR call 5 done with no reply -> the recommended option, no reply", A["call-5"]["choice"] == "keep-the-saint-naum-page" and not A["call-5"].get("reply"), A.get("call-5"))
    ok("MIGR call 6 still new -> nothing carried", "call-6" not in A, A.get("call-6"))
    ok("MIGR an answer made in the section first is never overwritten (call 2's note)", A["call-2"]["note"] == "made in the section first" and A["call-2"]["choice"] == "keep", A.get("call-2"))
    ok("MIGR done change -> looks good; seen change -> seen; new change -> nothing", A["arm-fn-visa"].get("ok") is True and A["kosovo-page"].get("seen") is True
       and not A["kosovo-page"].get("ok") and "geo-transfer" not in A, (A.get("arm-fn-visa"), A.get("kosovo-page")))
    ok("MIGR an inbox item the package doesn't have is reported, not invented", any("push-nope" in d and "skip" in d for d in did) and "nope" not in A, did)
    # the retired category
    obj = {"categories": [{"id": "mine", "name": "Mine"}, {"id": "push", "name": "Push package"}], "items": [{"id": "a", "cat": "mine", "title": "x", "status": "new"}]}
    qt.migrate(obj)
    ok("MIGR an empty 'push' category is dropped from the to-do store on load", [c["id"] for c in obj["categories"]] == ["mine"], obj["categories"])
    obj = {"categories": [{"id": "mine", "name": "Mine"}, {"id": "push", "name": "Push package"}], "items": [{"id": "a", "cat": "push", "title": "x", "status": "new"}]}
    qt.migrate(obj)
    ok("MIGR a 'push' category that still has items is kept", "push" in [c["id"] for c in obj["categories"]], obj["categories"])
    del os.environ["PUSH_PACKAGE_DIR"]


def settled_unit_checks():
    import push_package as pp
    pkg = json.loads((PKG / "package.json").read_text(encoding="utf-8"))
    call = next(c for c in pkg["calls"] if c["id"] == "call-2")
    rec = next(o["id"] for o in call["options"] if o.get("recommended"))
    other = next(o["id"] for o in call["options"] if not o.get("recommended"))
    chg = next(c for s in pkg["sections"] for c in s["changes"] if c["id"] == "chile-ferry")
    S = lambda kind, e, a, d=None: pp.item_status(kind, e, a, d)["state"]
    ok("SETTLED a call with no answer is open; the recommended pick settles at once", S("call", call, {}) == "open"
       and S("call", call, {"choice": rec, "rev": {"choice": 1}}) == "settled", rec)
    ok("SETTLED another pick or a reply waits for Claude; done.json at or after the answer settles it; a newer answer reopens it",
       S("call", call, {"choice": other, "rev": {"choice": 3}}) == "working"
       and S("call", call, {"choice": rec, "reply": "x", "rev": {"choice": 1, "reply": 2}}) == "working"
       and S("call", call, {"choice": other, "rev": {"choice": 3}}, {"rev": 3, "text": "Done."}) == "settled"
       and S("call", call, {"choice": other, "reply": "more", "rev": {"choice": 3, "reply": 5}}, {"rev": 3, "text": "Done."}) == "working")
    ok("SETTLED a change: looks good settles; a hold or a note waits; done settles; seen alone stays open",
       S("change", chg, {}) == "open" and S("change", chg, {"seen": True}) == "open" and S("change", chg, {"ok": True}) == "settled"
       and S("change", chg, {"hold": True, "rev": {"hold": 4}}) == "working" and S("change", chg, {"note": "n", "ok": True, "rev": {"note": 2}}) == "working"
       and S("change", chg, {"hold": True, "rev": {"hold": 4}}, {"rev": 4, "text": "Held."}) == "settled")
    ok("SETTLED Claude changed something, then he took it back: it reopens for Claude to undo",
       S("change", chg, {"hold": False, "rev": {"hold": 6}}, {"rev": 4, "text": "Held."}) == "working")


# ----------------------------------------------------------------------------- API
def api_checks(port):
    pkg_bytes = (PKG / "package.json").read_bytes()
    st, j = call(port, "/api/push")
    ok(f"API GET: the package ({N['calls']} calls) and no answers yet", st == 200 and len(j["package"]["calls"]) == N["calls"] and j["answers"] == {} and j["rev"] == 0, (st, str(j)[:200]))
    pid = j["package"]["id"]
    ok("API GET carries every item's state (all open with no answers) and the live checks, none green before they run",
       len(j.get("status", {})) == N["items"] and all(v["state"] == "open" for v in j["status"].values())
       and j.get("checks", {}).get("items") and not any(c.get("ok") for c in j["checks"]["items"]) and j.get("ready", {}).get("ok") is False,
       (len(j.get("status", {})), N["items"], str(j.get("checks"))[:200]))
    st, j = call(port, "/api/push", {"op": "checks"})
    ok("API Run the checks on a scratch package starts nothing (never stages or lints the real repo)", st == 200 and j.get("started") is False, (st, j.get("started")))
    st, j = call(port, "/api/push", {"op": "answer", "id": "call-4", "package": pid, "set": {"choice": "drop-the-bullet-and-the-pin"}, "seen": {"choice": None}})
    a = answers().get("call-4", {})
    ok("API one field saved, with its label and revision", st == 200 and a.get("choice") == "drop-the-bullet-and-the-pin" and a.get("choice_label") == "Drop the bullet and the pin"
       and a["rev"]["choice"] == 1 and a.get("recommended") is False, (st, a))
    ok("API package.json is never written", (PKG / "package.json").read_bytes() == pkg_bytes)
    before = ANS.read_bytes()
    st, j = call(port, "/api/push", {"op": "answer", "id": "call-4", "package": pid, "set": {"choice": "push-as-is-rebuild-the-map-next"}, "seen": {"choice": None}})
    ok("API a stale `seen` for a field written since -> 409, the newer answer returned, nothing written",
       st == 409 and j.get("stale") and j.get("conflicts") == ["choice"] and j["answers"]["call-4"]["choice"] == "drop-the-bullet-and-the-pin" and ANS.read_bytes() == before, (st, j))
    st, j = call(port, "/api/push", {"op": "answer", "id": "call-4", "package": pid, "set": {"reply": "and tell me when the map is done"}, "seen": {"reply": None}})
    a = answers()["call-4"]
    ok("API a field the request doesn't name is never touched (the stale tab's reply lands, the choice stays)",
       st == 200 and a["reply"] == "and tell me when the map is done" and a["choice"] == "drop-the-bullet-and-the-pin", a)
    st, j = call(port, "/api/push", {"op": "answer", "id": "call-4", "package": pid, "set": {"choice": "push-as-is-rebuild-the-map-next"}, "seen": {"choice": 1}})
    ok("API with the right `seen` the choice changes", st == 200 and answers()["call-4"]["choice"] == "push-as-is-rebuild-the-map-next" and answers()["call-4"]["recommended"] is True, (st, j))
    ok("API a write returns the states: call 4 has a reply, so it waits for Claude", j.get("status", {}).get("call-4", {}).get("state") == "working", j.get("status", {}).get("call-4"))
    import push_package as pp
    os.environ["PUSH_PACKAGE_DIR"] = str(PKG)
    try:
        pp.mark_done("call-4", "Pushed as is; map rebuild queued.")
    finally:
        del os.environ["PUSH_PACKAGE_DIR"]
    st, j = call(port, "/api/push")
    ok("API `done` settles it, with the text shown", j["status"]["call-4"]["state"] == "settled" and j["status"]["call-4"]["text"] == "Pushed as is; map rebuild queued.", j["status"]["call-4"])
    for body, why in [({"op": "answer", "id": "call-4", "set": {"choice": "nope"}}, "an option the call doesn't have"),
                      ({"op": "answer", "id": "zzz", "set": {"note": "x"}}, "an unknown id"),
                      ({"op": "answer", "id": "call-4", "set": {"hold": True}}, "hold on a call"),
                      ({"op": "answer", "id": "arm-fn-visa", "set": {"choice": "keep"}}, "a choice on a change"),
                      ({"op": "answer", "id": "call-4", "set": {"bogus": 1}}, "an unknown field"),
                      ({"op": "frob"}, "an unknown op")]:
        st, j = call(port, "/api/push", body)
        ok(f"API refused (400): {why}", st == 400 and j.get("ok") is False, (st, j))
    st, j = call(port, "/api/push", {"op": "answer", "id": "arm-fn-visa", "package": pid, "set": {"seen": True}, "seen": {}})
    st2, j2 = call(port, "/api/push", {"op": "answer", "id": "arm-fn-visa", "package": pid, "set": {"seen": True}, "seen": {}})
    ok("API `seen` is set once and needs no revision", st == 200 and st2 == 200 and answers()["arm-fn-visa"]["seen"] is True, (st, st2))
    st, raw = call(port, "/api/push/file/shots/arm-fn-visa-1440-after.jpg")
    ok("API the shots route serves a screenshot", st == 200 and isinstance(raw, bytes) and raw[:2] == b"\xff\xd8", st)
    st, _ = call(port, "/api/push/file/package.json")
    st2, _ = call(port, "/api/push/file/shots/..%2Fanswers.json")
    ok("API the shots route serves nothing outside shots/", st == 404 and st2 == 404, (st, st2))
    # a read-only server refuses writes
    rport = free_port()
    ro = start(rport, {"QUEUE_TAB_DIR": str(SCRATCH), "QUEUE_TAB_REDLINE": str(SCRATCH / "redline"), "QUEUE_TAB_READONLY": "1"})
    try:
        before = ANS.read_bytes()
        st, j = call(rport, "/api/push", {"op": "answer", "id": "call-1", "package": pid, "set": {"note": "x"}, "seen": {}})
        ok("API a read-only server refuses (403) and writes nothing", st == 403 and ANS.read_bytes() == before, (st, j))
    finally:
        ro.kill(); ro.wait(timeout=10)


def rebuilt_package_check(port):
    """a package rebuilt on a new base gets a new id: the old answers stop applying and are archived on the first write"""
    p = PKG / "package.json"
    orig = p.read_bytes()
    obj = json.loads(orig.decode("utf-8"))
    obj["id"] = "push-newbase"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    try:
        st, j = call(port, "/api/push")
        ok("API a rebuilt package (new id) shows no answers from the old one", st == 200 and j["answers"] == {}, str(j.get("answers"))[:200])
        st, j = call(port, "/api/push", {"op": "answer", "id": "call-1", "package": N["id"], "set": {"note": "x"}, "seen": {}})
        ok("API a write naming the old package id is refused (409) with the new package", st == 409 and j.get("package", {}).get("id") == "push-newbase", (st, str(j)[:200]))
        st, j = call(port, "/api/push", {"op": "answer", "id": "call-1", "package": "push-newbase", "set": {"note": "x"}, "seen": {}})
        arch = list(PKG.glob("answers-push-*.json"))
        ok("API the first write to the new package archives the old answers beside it", st == 200 and len(arch) == 1 and "call-4" in json.loads(arch[0].read_text(encoding="utf-8"))["answers"]
           and json.loads(ANS.read_text(encoding="utf-8"))["package"] == "push-newbase", (st, arch))
    finally:
        p.write_bytes(orig)
        ANS.unlink()
        if (PKG / "done.json").is_file():
            (PKG / "done.json").unlink()
        for f in PKG.glob("answers-push-*.json"):
            f.unlink()


# ----------------------------------------------------------------------------- UI
def ui_checks(port):
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
            try:
                pg.wait_for_selector("#qt-push .pp-call", timeout=20000)
            except Exception:
                print("  (section not drawn:", pg.evaluate("() => ({st: window.__push.state, err: window.__push.error, data: !!window.__push.data, q: window.__queue.state})"), ")")
                raise
            pg.wait_for_timeout(300)
            return ctx, pg

        def strip(pg):
            return pg.evaluate("() => Object.fromEntries([...document.querySelectorAll('#pp-head .pp-stat')].map(s => [s.dataset.stat, s.textContent.trim()]))")

        def settle(pg):
            pg.evaluate("() => window.__push.chain")
            pg.wait_for_timeout(250)

        ctx, pg = page(1440)
        n = pg.evaluate("""() => ({ calls: document.querySelectorAll('#qt-push .pp-call').length,
            recs: [...document.querySelectorAll('#qt-push .pp-call')].map(c => c.querySelectorAll('.pp-opt .rec').length),
            opts: document.querySelectorAll('#qt-push .pp-opt').length, diff: document.querySelectorAll('#pp-e-call-1 .pp-diff del').length,
            shots: document.querySelectorAll('#pp-e-call-1 .pp-shot img').length, secs: document.querySelectorAll('#qt-push details.pp-sec').length,
            cols: !!document.querySelector('.qt-cols'), order: [...document.querySelector('.qt-wrap').children].map(x => x.id || x.className) })""")
        ok(f"UI the section draws {N['calls']} call cards, each with exactly one Recommended option", n["calls"] == N["calls"] and n["recs"] == [1] * N["calls"] and n["opts"] == N["opts"], n)
        ok("UI call 1 shows the diff and its before/after shots", n["diff"] == 1 and n["shots"] == 2, n)
        ok(f"UI the changes come by section ({N['secs']}), and the queue and to-do columns still draw under it", n["secs"] == N["secs"] and n["cols"] and n["order"].index("qt-push") < n["order"].index("qt-cols"), n)
        ok(f"UI the strip starts at {N['items']} need you, 0 settled, and the checks not green", strip(pg).get("open", "").startswith(f"{N['items']} need") and strip(pg).get("settled", "").startswith("0")
           and "working" not in strip(pg) and " of " in strip(pg).get("checks", "") and "Not ready" in pg.text_content("#pp-head .pp-how"), strip(pg))
        pg.screenshot(path=str(PREVIEWS / "queue-push-1440-top.png"))
        if N["nm"]:                                     # the North Macedonia launch section (Nov 10 evening)
            pg.click('#pp-sec-nmlaunch > summary'); pg.wait_for_timeout(500)
            nm = pg.evaluate("""() => { const s = document.querySelector('#pp-sec-nmlaunch'); return { open: s.open, rows: s.querySelectorAll('.pp-chg').length,
                newpg: [...s.querySelectorAll('#pp-e-nm-skopje .pp-empty')].map(x => x.textContent), after: [...s.querySelectorAll('#pp-e-nm-skopje .pp-shot.a img')].map(i => i.complete && i.naturalWidth > 0),
                ba: s.querySelectorAll('#pp-e-nm-home-tile .pp-shot img').length, det: (s.querySelector('.pp-sub:last-of-type') || {}).textContent || '' }; }""")
            ok("UI the North Macedonia launch section draws its changes; a new page shows only an after shot, a home change before and after",
               nm["open"] and nm["rows"] == N["nm"] and nm["newpg"] == ["Not on the live site yet"] and nm["after"] == [True] and nm["ba"] == 2, nm)
            pg.evaluate("document.querySelector('#pp-sec-nmlaunch').scrollIntoView({block:'start'})"); pg.wait_for_timeout(400)
            pg.screenshot(path=str(PREVIEWS / "queue-push-1440-nm.png"))
            pg.click('#pp-sec-nmlaunch > summary'); pg.wait_for_timeout(300)

        # choose, change, clear
        pg.click('#pp-e-call-2 .pp-opt[data-opt="unlink-tatev-instead"]'); settle(pg)
        on = pg.evaluate("() => [...document.querySelectorAll('#pp-e-call-2 .pp-opt.on')].map(b => b.dataset.opt)")
        ok("UI a pick other than the recommended one is saved and the card says Claude is on it", on == ["unlink-tatev-instead"] and answers().get("call-2", {}).get("choice") == "unlink-tatev-instead"
           and "Claude is on it" in (pg.text_content("#pp-e-call-2 .pp-state.work") or "") and strip(pg).get("working", "").startswith("1"), (on, answers().get("call-2"), strip(pg)))
        pg.click('#pp-e-call-2 .pp-opt[data-opt="keep"]'); settle(pg)
        ok("UI the recommended pick settles it: the card leaves Needs you and is a row in the closed Settled fold",
           answers()["call-2"]["choice"] == "keep" and answers()["call-2"]["recommended"] is True and not pg.query_selector("#pp-e-call-2")
           and pg.query_selector("details.pp-settled:not([open])") and strip(pg)["settled"].startswith("1"), (answers().get("call-2"), strip(pg)))
        pg.click("details.pp-settled > summary"); pg.wait_for_timeout(300)
        row = pg.text_content("#pp-s-call-2") or ""
        ok("UI the Settled fold opens on a row with his answer and what happened", "Keep" in row and "Goes as" in row, row)
        pg.click('#pp-s-call-2 [data-act="pp-sopen"]'); pg.wait_for_timeout(250)
        pg.click('#pp-s-call-2 #pp-e-call-2 .pp-opt[data-opt="keep"]'); settle(pg)
        ok("UI clearing the pick from the opened row moves the call back up to Needs you", answers()["call-2"]["choice"] is None
           and pg.query_selector("#pp-e-call-2") and not pg.query_selector("#pp-s-call-2") and not pg.query_selector('#pp-e-call-2 .pp-opt.on'), answers().get("call-2"))
        pg.click('#pp-e-call-2 .pp-opt[data-opt="keep"]'); settle(pg)
        pg.click("details.pp-settled > summary"); pg.wait_for_timeout(250)
        # reply
        pg.fill("#pp-rp-call-1", "Use 'about $2 a ride' instead")
        pg.press("#pp-rp-call-1", "Enter"); settle(pg)
        ok("UI a reply (Enter) is saved and shown as his reply", answers().get("call-1", {}).get("reply") == "Use 'about $2 a ride' instead"
           and "about $2 a ride" in (pg.text_content("#pp-e-call-1 .qt-note") or "") and pg.query_selector("#pp-e-call-1 .pp-state.work"), answers().get("call-1"))
        pg.click('#pp-e-call-1 [data-act="pp-reply-edit"]'); pg.wait_for_timeout(150)
        pg.fill("#pp-rp-call-1", "Use 'about $2' instead"); pg.press("#pp-rp-call-1", "Enter"); settle(pg)
        ok("UI the reply can be edited", answers()["call-1"]["reply"] == "Use 'about $2' instead", answers().get("call-1"))

        # hold, looks good, note on changes
        pg.click('#pp-sec-content > summary'); pg.wait_for_timeout(400)
        pg.click('#pp-e-peru-wayna [data-act="pp-hold"]'); settle(pg)
        ok("UI 'Hold back' flags a change and waits for Claude; the section count shows it", answers().get("peru-wayna", {}).get("hold") is True and pg.query_selector("#pp-e-peru-wayna.held")
           and pg.query_selector("#pp-e-peru-wayna .pp-state.work") and "1 held" in pg.text_content("#pp-sec-content > summary"), (answers().get("peru-wayna"), strip(pg)))
        pg.click('#pp-e-peru-wayna [data-act="pp-ok"]'); settle(pg)
        a = answers()["peru-wayna"]
        ok("UI 'Looks good' on a held change un-holds it and settles it", a.get("ok") is True and a.get("hold") is False and not pg.query_selector("#pp-e-peru-wayna")
           and pg.evaluate("() => window.__push.status['peru-wayna'].state") == "settled", a)
        pg.click('#pp-e-chile-ferry [data-act="pp-hold"]'); settle(pg)
        pg.click('#pp-e-chile-ferry [data-act="pp-note"]'); pg.wait_for_timeout(150)
        pg.fill("#pp-nt-chile-ferry", "Check the 5:30pm boat first")
        pg.press("#pp-nt-chile-ferry", "Enter"); settle(pg)
        ok("UI a note on a change is saved and shown", answers().get("chile-ferry", {}).get("note") == "Check the 5:30pm boat first"
           and "5:30pm" in (pg.text_content("#pp-e-chile-ferry .qt-note") or ""), answers().get("chile-ferry"))
        # a link-only row and a behind-the-scenes item can be held too
        pg.click('#pp-e-link-tatev-4-links [data-act="pp-hold"]'); settle(pg)
        ok("UI a link-only fix can be held back", answers().get("link-tatev-4-links", {}).get("hold") is True, answers().get("link-tatev-4-links"))
        pg.screenshot(path=str(PREVIEWS / "queue-push-1440-changes.png"))
        pg.evaluate("document.querySelector('#pp-e-chile-ferry').scrollIntoView({block:'center'})"); pg.wait_for_timeout(300)
        pg.screenshot(path=str(PREVIEWS / "queue-push-1440-change-rows.png"))

        # persistence across a reload
        pg.reload(); pg.wait_for_timeout(600); pg.evaluate("showView('queue')")
        try:
            pg.wait_for_selector("#qt-push .pp-call", timeout=20000)
        except Exception:
            print("  (section not drawn after reload:", pg.evaluate("() => ({st: window.__push.state, err: window.__push.error, data: !!window.__push.data, q: window.__queue.state, open: window.__push.open, html: (window.__push.el || {}).outerHTML ? window.__push.el.outerHTML.slice(0, 300) : null})"), ")")
            raise
        pg.wait_for_timeout(500)
        if not pg.query_selector("details.pp-settled[open]"):
            pg.click("details.pp-settled > summary"); pg.wait_for_timeout(300)
        st = pg.evaluate("""() => ({ c2: (document.querySelector('#pp-s-call-2') || {}).textContent || '', r1: (document.querySelector('#pp-e-call-1 .qt-note') || {}).textContent || '',
            open: !!document.querySelector('#pp-sec-content[open]'), held: !!document.querySelector('#pp-e-chile-ferry.held'), strip: document.querySelector('#pp-head').textContent,
            settledOpen: !!document.querySelector('details.pp-settled[open]') })""")
        ok("UI after a reload: the settled call, the reply, the held change, and the open section are all still there",
           "Keep" in st["c2"] and "about $2" in st["r1"] and st["open"] and st["held"] and "3 Claude is on" in st["strip"], st)
        # Claude carries one out: done.json, and the open page follows on its next refresh
        import push_package as pp
        os.environ["PUSH_PACKAGE_DIR"] = str(PKG)
        try:
            pp.mark_done("chile-ferry", "Held back; checked the 5:30pm boat.")
        finally:
            del os.environ["PUSH_PACKAGE_DIR"]
        pg.evaluate("() => window.__push.load ? window.__push.load() : null"); pg.wait_for_timeout(800)
        row = pg.text_content("#pp-s-chile-ferry") or ""
        ok("UI once Claude marks it done, the item moves to Settled with what was done", not pg.query_selector("#pp-e-chile-ferry") and "checked the 5:30pm boat" in row, row)

        # a stale tab
        ctx2, pg2 = page(1440)
        call(port, "/api/push", {"op": "answer", "id": "call-8", "package": N["id"], "set": {"choice": "hold-kosovo"}, "seen": {"choice": None}})   # tab B
        pg2.click('#pp-e-call-8 .pp-opt[data-opt="hold-the-rates-job"]'); settle(pg2)
        on = pg2.evaluate("() => [...document.querySelectorAll('#pp-e-call-8 .pp-opt.on')].map(b => b.dataset.opt)")
        toast = pg2.evaluate("() => { const t = document.querySelector('.qt-toast'); return t && !t.hidden ? t.textContent : ''; }")
        ok("UI a stale tab's write to a field changed elsewhere is refused: the newer choice stays and shows, with a toast",
           answers()["call-8"]["choice"] == "hold-kosovo" and on == ["hold-kosovo"] and "another tab" in toast, (answers().get("call-8"), on, toast))
        pg2.fill("#pp-rp-call-8", "hold Kosovo until Prizren is live"); pg2.press("#pp-rp-call-8", "Enter"); settle(pg2)
        ok("UI the stale tab's write to another field goes through and leaves the newer choice alone",
           answers()["call-8"]["reply"] == "hold Kosovo until Prizren is live" and answers()["call-8"]["choice"] == "hold-kosovo", answers().get("call-8"))
        pg2.click('#pp-e-call-8 .pp-opt[data-opt="push-both"]'); settle(pg2)
        ok("UI once refreshed, the stale tab can change the choice", answers()["call-8"]["choice"] == "push-both", answers().get("call-8"))
        pg2.click('#pp-e-call-5 .pp-opt[data-opt="swap-in-that-tour-after-you-open-it"]'); pg2.click('#pp-e-call-5 .pp-opt[data-opt="keep-the-saint-naum-page"]'); settle(pg2)
        ok("UI two quick clicks in one tab both land, in order (no false conflict with itself)", answers()["call-5"]["choice"] == "keep-the-saint-naum-page", answers().get("call-5"))
        ctx2.close()

        # the viewer
        pg.click('#pp-e-call-1 .pp-shot.a .pp-img'); pg.wait_for_timeout(400)
        v = pg.evaluate("""() => { const v = document.querySelector('#qt-viewer'); return { shown: !v.hidden, imgs: v.querySelectorAll('.qv-pair img').length,
            caps: [...v.querySelectorAll('.qv-pair figcaption')].map(x => x.textContent), pos: (v.querySelector('.pos') || {}).textContent, opts: v.querySelectorAll('.pp-opt').length,
            loaded: [...v.querySelectorAll('.qv-pair img')].map(i => i.complete && i.naturalWidth > 0) }; }""")
        ok("UI a screenshot opens the viewer with before and after together, and the call's options", v["shown"] and v["imgs"] == 2 and v["caps"] == ["Before (live now)", "After (this push)"]
           and v["opts"] == 2 and all(v["loaded"]), v)
        pg.screenshot(path=str(PREVIEWS / "queue-push-1440-viewer.png"))
        pg.click('#qt-viewer [data-act="dev"][data-dev="phone"]'); pg.wait_for_timeout(300)
        ph = pg.evaluate("() => ({ phone: !!document.querySelector('#qt-viewer .qv-pair.phone'), imgs: document.querySelectorAll('#qt-viewer .qv-pair img').length })")
        ok("UI Phone shows the phone pair", ph["phone"] and ph["imgs"] == 2, ph)
        pg.click('#qt-viewer .pp-opt[data-opt="write-your-own-line"]'); settle(pg)
        ok("UI an option chosen in the viewer is saved and the card follows", answers()["call-1"]["choice"] == "write-your-own-line"
           and pg.evaluate("() => [...document.querySelectorAll('#pp-e-call-1 .pp-opt.on')].map(b => b.dataset.opt)") == ["write-your-own-line"], answers().get("call-1"))
        pg.click('#qt-viewer [data-act="next"]'); pg.wait_for_timeout(300)
        nx = pg.evaluate("() => document.querySelector('#qt-viewer .pos').textContent")
        ok("UI the arrow walks to the next item with shots", nx.startswith("2 /"), nx)
        pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
        ok("UI Esc closes the viewer", pg.evaluate("() => document.querySelector('#qt-viewer').hidden"))
        # a change's flags from the viewer
        pg.click('#pp-e-greece-guard .pp-shot.b .pp-img'); pg.wait_for_timeout(300)
        pg.click('#qt-viewer [data-act="pp-hold"]'); settle(pg)
        pg.fill('#qv-pnote', 'Sunday 11am, double check'); pg.press('#qv-pnote', 'Enter'); settle(pg)
        a = answers().get("greece-guard", {})
        ok("UI a change can be held and noted from the viewer; it counts as seen", a.get("hold") is True and a.get("note") == "Sunday 11am, double check" and a.get("seen") is True, a)
        pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
        ctx.close()

        # phone width
        ctx, pg = page(393, 852)
        pg.wait_for_timeout(400)
        over = pg.evaluate("""() => { const r = document.querySelector('#queue-root'); const bad = [...document.querySelectorAll('#qt-push *')].filter(e => { const b = e.getBoundingClientRect(); return b.width && b.right > innerWidth + 1; }).map(e => e.className || e.tagName).slice(0, 5);
            return { sw: r.scrollWidth, cw: r.clientWidth, doc: document.documentElement.scrollWidth, w: innerWidth, bad }; }""")
        ok("UI 393 px: no sideways scroll, nothing in the section wider than the screen", over["sw"] <= over["cw"] and over["doc"] <= over["w"] and not over["bad"], over)
        pg.screenshot(path=str(PREVIEWS / "queue-push-393-top.png"))
        pg.evaluate("document.querySelector('#pp-e-call-3').scrollIntoView({block:'start'})"); pg.wait_for_timeout(300)
        pg.screenshot(path=str(PREVIEWS / "queue-push-393-calls.png"))
        pg.click('#pp-e-call-3 .pp-opt[data-opt="drop-a-recommendation"]'); settle(pg)
        ok("UI 393 px: an option can be tapped", answers().get("call-3", {}).get("choice") == "drop-a-recommendation", answers().get("call-3"))
        pg.click('#pp-sec-content > summary'); pg.wait_for_timeout(500)
        pg.evaluate("document.querySelector('#pp-sec-content').scrollIntoView({block:'start'})"); pg.wait_for_timeout(500)
        over2 = pg.evaluate("() => ({ sw: document.querySelector('#queue-root').scrollWidth, cw: document.querySelector('#queue-root').clientWidth })")
        ok("UI 393 px: an open change section fits too", over2["sw"] <= over2["cw"] and pg.query_selector("#pp-sec-content[open] .pp-chg"), over2)
        pg.screenshot(path=str(PREVIEWS / "queue-push-393-changes.png"))
        if N["nm"]:
            pg.click('#pp-sec-nmlaunch > summary'); pg.wait_for_timeout(500)
            pg.evaluate("document.querySelector('#pp-sec-nmlaunch').scrollIntoView({block:'start'})"); pg.wait_for_timeout(500)
            o3 = pg.evaluate("() => ({ sw: document.querySelector('#queue-root').scrollWidth, cw: document.querySelector('#queue-root').clientWidth })")
            ok("UI 393 px: the North Macedonia section fits", o3["sw"] <= o3["cw"] and pg.query_selector("#pp-sec-nmlaunch[open] .pp-chg"), o3)
            pg.screenshot(path=str(PREVIEWS / "queue-push-393-nm.png"))
        pg.click('#pp-e-arm-fn-visa .pp-shot.a .pp-img'); pg.wait_for_timeout(400)
        pg.screenshot(path=str(PREVIEWS / "queue-push-393-viewer.png"))
        vw = pg.evaluate("() => { const v = document.querySelector('#qt-viewer'); return { w: v.scrollWidth, cw: v.clientWidth, imgs: v.querySelectorAll('.qv-pair img').length }; }")
        ok("UI 393 px: the viewer fits and shows the pair", vw["w"] <= vw["cw"] + 1 and vw["imgs"] == 2, vw)
        ctx.close()

        # a server without /api/push
        ctx = b.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script(f"window.QUEUE_API = 'http://127.0.0.1:{port}'; try {{ localStorage.setItem('editorAutosave', 'off'); }} catch (e) {{}}")
        pg = ctx.new_page()
        pg.route("http://127.0.0.1:5003/**", lambda r: r.fulfill(status=200, json={}, headers={"Access-Control-Allow-Origin": "*"}))
        pg.route(f"http://127.0.0.1:{port}/api/push*", lambda r: r.fulfill(status=404, body="Not Found", headers={"Access-Control-Allow-Origin": "*"}))
        pg.goto((ROOT / "editor.html").as_uri()); pg.wait_for_timeout(600); pg.evaluate("showView('queue')"); pg.wait_for_timeout(1500)
        old = pg.evaluate("() => ({ msg: (document.querySelector('#qt-push .pp-msg') || {}).textContent || '', todo: document.querySelectorAll('.qt-cat').length, queue: document.querySelectorAll('.qt-item').length })")
        ok("UI an old server (no /api/push): the queue and to-do still draw, and the section says to re-click the shortcut",
           "re-click the Editor Suite shortcut" in old["msg"] and old["todo"] >= 1 and old["queue"] == 2, old)
        ctx.close()
        b.close()
    ok("UI no script errors", not errs, errs[:3])


def main():
    seed()
    migration_checks()
    settled_unit_checks()
    port = free_port()
    srv = start(port, {"QUEUE_TAB_DIR": str(SCRATCH), "QUEUE_TAB_REDLINE": str(SCRATCH / "redline")})
    try:
        api_checks(port)
        rebuilt_package_check(port)
        ui_checks(port)
        st, j = call(port, "/api/todo?sync=1")
        ok("the scratch to-do store dropped its empty 'push' category", st == 200 and "push" not in [c["id"] for c in j["categories"]], j.get("categories"))
    finally:
        srv.kill(); srv.wait(timeout=10)
        shutil.rmtree(SCRATCH, ignore_errors=True)
    n_ok = sum(1 for c in checks if c[1])
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
