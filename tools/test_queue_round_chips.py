"""An inbox item that names a round shows that round's live count, and nothing to decide once it is applied
(queue_tab.sync_live + queue.js roundChip). QA 2026-11-10: the Review Hub's "upgrade-*" items kept the count from the
day they were imported, so "Cappadocia: Balloons..." said "20 changes" for cappadocia-r3, which was applied.

Scratch only: a temp QUEUE_TAB_DIR (away_queue.md + kevin_todo.json) and a fake redline folder (QUEUE_TAB_REDLINE),
served by the standalone `python tools/queue_tab.py --serve <port>`; the editor is opened from disk with
window.QUEUE_API pointed at it. The real .tmp/away_queue.md, .tmp/kevin_todo.json and /api/todo are never touched.

  R1  an item on an OPEN round follows the live count (left of total), not the count it was imported with
  R2  an item on an APPLIED round is marked applied: its chip says "round applied" and counts no changes
  R3  an item whose round folder is gone shows no count at all
  R4  the round-* items made from live rounds are unchanged (count, then "Applied." once applied)
  R5  a second sync with nothing moved writes nothing (the file is byte-identical)
  R6  in the tab: the chips read as above, and the viewer's footer agrees

    python tools/test_queue_round_chips.py
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
import urllib.request

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:600]), flush=True)


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


SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="queue_round_chips_"))
RL = SCRATCH / "redline"
TODO = SCRATCH / "kevin_todo.json"
assert not str(SCRATCH.resolve()).startswith(str(ROOT.resolve())), "not a scratch folder"


def round_dir(slug, n, decided=0, applied=False):
    d = RL / slug
    d.mkdir(parents=True)
    (d / "proposal.json").write_text(json.dumps({"slug": slug, "title": slug, "article_dir": "Drafts/Upcoming/zz", "article": "test.html",
                                                 "changes": [{"id": "g%02d" % i} for i in range(1, n + 1)]}), encoding="utf-8")
    if decided:
        (d / "decisions.json").write_text(json.dumps({"g%02d" % i: True for i in range(1, decided + 1)}), encoding="utf-8")
    if applied:
        (d / "applied.json").write_text(json.dumps({"applied": True}), encoding="utf-8")


def item(iid, title, rd):
    return {"id": iid, "cat": "rounds", "kind": "review", "title": title, "summary": "A rewritten article to review.", "status": "new",
            "links": {"editor": "Drafts/Upcoming/zz/test.html"}, "round": rd}


def main():
    round_dir("zz-open-r2", 5, decided=2)
    round_dir("zz-done-r1", 20, applied=True)
    (RL / "_comments").mkdir()
    (SCRATCH / "away_queue.md").write_text("# Away queue: test\n\n## Queue (in order)\n- [ ] **One.** Something.\n", encoding="utf-8")
    TODO.write_text(json.dumps({"categories": [{"id": "rounds", "name": "Review"}, {"id": "mine", "name": "My own to-dos"}], "items": [
        item("upgrade-open", "Zz Open: rewritten", {"slug": "zz-open-r2", "changes": 99, "left": 99}),
        item("upgrade-done", "Zz Done: rewritten", {"slug": "zz-done-r1", "changes": 20, "left": 20}),
        item("upgrade-gone", "Zz Gone: rewritten", {"slug": "zz-gone-r1", "changes": 7, "left": 7}),
    ]}, indent=1), encoding="utf-8")
    port = free_port()
    env = dict(os.environ, QUEUE_TAB_DIR=str(SCRATCH), QUEUE_TAB_REDLINE=str(RL), PYTHONIOENCODING="utf-8")
    srv = subprocess.Popen([sys.executable, str(ROOT / "tools" / "queue_tab.py"), "--serve", str(port)], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == "win32" else 0)
    try:
        if not wait_port(port):
            ok("the scratch queue server started", False)
            return 1
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/todo?sync=1", timeout=20) as r:
            body = json.loads(r.read().decode("utf-8"))
        items = {x["id"]: x for x in json.loads(TODO.read_text(encoding="utf-8"))["items"]}
        ok("R1 an open round's item follows the live count (3 of 5 left), not the imported 99",
           items["upgrade-open"]["round"] == {"slug": "zz-open-r2", "changes": 5, "left": 3}, items["upgrade-open"]["round"])
        ok("R2 an applied round's item is marked applied and has nothing left",
           items["upgrade-done"]["round"].get("applied") is True and items["upgrade-done"]["round"]["left"] == 0, items["upgrade-done"]["round"])
        ok("R2 its status is left alone (the item is about the article)", items["upgrade-done"]["status"] == "new", items["upgrade-done"]["status"])
        ok("R3 a round that no longer exists gives no count", items["upgrade-gone"]["round"].get("gone") is True and items["upgrade-gone"]["round"]["changes"] is None, items["upgrade-gone"]["round"])
        ok("R4 the live round item is made as before", items.get("round-zz-open-r2", {}).get("round") == {"slug": "zz-open-r2", "changes": 5, "left": 3}, items.get("round-zz-open-r2"))
        ok("R4 an applied round gets no round-* item", "round-zz-done-r1" not in items, list(items))
        before = TODO.read_bytes()
        time.sleep(1.1)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/todo?sync=1", timeout=20) as r:
            r.read()
        ok("R5 a second sync with nothing moved writes nothing", TODO.read_bytes() == before)

        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            ctx.add_init_script(f"window.QUEUE_API = 'http://127.0.0.1:{port}'; try {{ localStorage.clear(); localStorage.setItem('editorAutosave', 'off'); }} catch (e) {{}}")
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]) if "loadAlbums" not in str(getattr(e, "stack", "")) else None)
            pg.route("http://127.0.0.1:5003/**", lambda r: r.fulfill(status=200, json={}, headers={"Access-Control-Allow-Origin": "*"}))
            pg.goto((ROOT / "editor.html").as_uri())
            pg.wait_for_timeout(600)
            pg.evaluate("showView('queue')")
            pg.wait_for_timeout(1200)
            for _ in range(3):                     # unfold every category and country group
                pg.evaluate("() => document.querySelectorAll('.qt-catb[aria-expanded=\"false\"], .qt-cgb[aria-expanded=\"false\"]').forEach(b => b.click())")
                pg.wait_for_timeout(300)
            chips = pg.evaluate("""() => Object.fromEntries(['upgrade-open', 'upgrade-done', 'upgrade-gone', 'round-zz-open-r2'].map(id => {
                const el = document.querySelector(`.qt-todo[data-id="${id}"]`);
                return [id, el ? [...el.querySelectorAll('.qt-chip.plain')].map(c => c.textContent.trim()) : null]; }))""")
            ok("R6 the open round's chip: '3 of 5 changes'", chips["upgrade-open"] is not None and "3 of 5 changes" in chips["upgrade-open"], chips)
            ok("R6 the applied round's chip: 'round applied', no count", chips["upgrade-done"] is not None and "round applied" in chips["upgrade-done"]
               and not any("change" in c for c in chips["upgrade-done"]), chips)
            ok("R6 the gone round's item has no round chip", chips["upgrade-gone"] is not None and not any("change" in c or "applied" in c for c in chips["upgrade-gone"]), chips)
            pg.screenshot(path=str(SCRATCH / "chips.png"))
            ok("no script errors", not errs, errs[:2])
            b.close()
    finally:
        srv.kill()
        srv.wait(timeout=10)
        shutil.rmtree(SCRATCH, ignore_errors=True)
    n_ok = sum(1 for c in checks if c[1])
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
