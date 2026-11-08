"""Review: rounds by country + the Shadow reviewer merged into it (queue.js + tools/queue_tab.py; Kevin, 2026-11-07:
"in queue organize review rounds by country and add flag and collapsability since I work on things by country. Shadow
reviewer just becomes part of regular article review from now on so merge that as part of the review: rounds").

Everything runs against a SCRATCH COPY of the real .tmp/kevin_todo.json (so the migration is tested on his real items)
plus a fake redline folder, served by the standalone `python tools/queue_tab.py --serve <port>`; the editor is loaded from
file:// with window.QUEUE_API pointed at that port. The live photo server (:5003) is never touched and the real store
is never written (checked byte for byte at the end).

  MIGRATION  the 'shadow' category is gone, its items are in 'rounds' with status/note/reply kept, each right after the
             last round of its article; the shape is persisted on the first GET; every item resolves to a country
  DERIVATION country from `country` (incl. Türkiye spellings), the article path, the round slug; unknown -> null
  LIVE       a new Shadow thread seeds a 'rounds' item beside the article's round; a new round lands beside its article
  UI         rounds opens to one flag row per country (flag = the nav's local png), open/total counts, collapsed by default,
             a click opens it and the state survives a reload; items without a country sit under Other at the end; drag
             inside a country and the arrows reorder only that country; a country row drags; the viewer still walks the
             category; other categories unchanged; 393 px without sideways scroll; screenshots to .tmp/previews/

    python tools/test_queue_rounds_by_country.py
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
sys.path.insert(0, str(ROOT / "tools"))
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
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status, json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.loads(e.read().decode("utf-8"))
                except Exception:
                    return e.code, {}
            except (ConnectionResetError, ConnectionAbortedError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(0.3)


def start_server(port, env):
    e = dict(os.environ, **env, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, str(ROOT / "tools" / "queue_tab.py"), "--serve", str(port)], env=e,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == "win32" else 0)
    if not wait_port(port):
        p.kill(); raise RuntimeError("queue_tab server did not start")
    return p


# ----------------------------------------------------------------------------- fixtures: a copy of the REAL store
REAL_TODO = ROOT / ".tmp" / "kevin_todo.json"
REAL_MD = ROOT / ".tmp" / "away_queue.md"
real_todo_before = REAL_TODO.read_bytes()
real_md_before = REAL_MD.read_bytes()
SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="queue_rounds_test_"))
TODO = SCRATCH / "kevin_todo.json"
RL = SCRATCH / "redline"
shutil.copyfile(REAL_TODO, TODO)
shutil.copyfile(REAL_MD, SCRATCH / "away_queue.md")
RL.mkdir(); (RL / "_comments").mkdir()
raw = json.loads(real_todo_before.decode("utf-8-sig"))
had_shadow_cat = any(c["id"] == "shadow" for c in raw["categories"])
shadow_items = [i for i in raw["items"] if i["cat"] == "shadow"]
rounds_items = [i for i in raw["items"] if i["cat"] == "rounds"]
before_state = {i["id"]: (i["status"], i.get("note"), i.get("reply")) for i in raw["items"]}
print(f"real store: {len(raw['items'])} items, shadow category present: {had_shadow_cat}, {len(shadow_items)} shadow items, {len(rounds_items)} rounds items")

# ----------------------------------------------------------------------------- pure functions
import queue_tab as qt   # noqa: E402

ok("D1 country_key: folder names, the nav's spellings, Türkiye with and without the umlaut, spaces", [qt.country_key(x) for x in ("greece", "Greece", "Türkiye", "Turkiye", "turkey", "North Macedonia", "north-macedonia", "El Salvador", "", None, "Narnia")]
   == ["greece", "greece", "turkiye", "turkiye", "turkiye", "north-macedonia", "north-macedonia", "el-salvador", None, None, None],
   [qt.country_key(x) for x in ("greece", "Greece", "Türkiye", "Turkiye", "turkey", "North Macedonia", "north-macedonia", "El Salvador", "", None, "Narnia")])
ok("D2 item_country: article path (Drafts tiers and live), links.editor, round slug (north-macedonia before any shorter name), id; unknown -> None",
   [qt.item_country(x) for x in ({"id": "a", "article": {"path": "Drafts/Full Articles/turkiye/istanbul.html"}}, {"id": "b", "links": {"editor": "Drafts/Upcoming/kosovo/prizren.html"}},
                                 {"id": "c", "links": {"editor": "greece/athens.html"}}, {"id": "round-north-macedonia-itinerary-r2", "round": {"slug": "north-macedonia-itinerary-r2"}},
                                 {"id": "round-top10-turkiye-r3"}, {"id": "shadow-top-10-north-macedonia"}, {"id": "x", "links": {"editor": ".tmp/previews/foo.html"}}, {"id": "y", "title": "nothing"})]
   == ["turkiye", "kosovo", "greece", "north-macedonia", "turkiye", "north-macedonia", None, None], "")
g = qt.item_group({"id": "round-athens-r3", "links": {"editor": "Drafts/Full Articles/greece/athens.html"}})
ok("D3 item_group gives the nav's name and the local flag png that exists", g == {"key": "greece", "name": "Greece", "flag": "Images/web/flags/gr.png"} and (ROOT / g["flag"]).is_file(), g)
ok("D3b every country in the table has its flag file", all((ROOT / qt.FLAGS_DIR / f"{iso}.png").is_file() for _n, iso in qt.COUNTRIES.values()), [k for k, (_n, iso) in qt.COUNTRIES.items() if not (ROOT / qt.FLAGS_DIR / f"{iso}.png").is_file()])

# ----------------------------------------------------------------------------- the server over the copy
PORT = free_port()
srv = start_server(PORT, {"QUEUE_TAB_DIR": str(SCRATCH), "QUEUE_TAB_REDLINE": str(RL)})
api = Api(PORT)
try:
    st, t = api.call("/api/todo?sync=1")
    byid = {i["id"]: i for i in t["items"]}
    cat_ids = [c["id"] for c in t["categories"]]
    moved = [i for i in t["items"] if i.get("moved_from") == "shadow"]
    ok("M1 GET: no 'shadow' category, rounds first, 7 categories", st == 200 and "shadow" not in cat_ids and cat_ids[0] == "rounds" and len(cat_ids) == 7, cat_ids)
    ok(f"M2 every one of the {len(shadow_items)} shadow items is now in rounds (moved_from=shadow); none lost", len(moved) == len(shadow_items) and all(byid[i["id"]]["cat"] == "rounds" for i in shadow_items)
       and len(t["items"]) == len(raw["items"]) + sum(1 for i in t["items"] if i.get("source", "").startswith("live") and i["id"] not in before_state), (len(moved), len(shadow_items), len(t["items"]), len(raw["items"])))
    live = lambda i: i.startswith(("round-", "shadow-"))          # live items legitimately go done here (the scratch redline folder is empty)
    kept = all(byid[i]["status"] == s and byid[i].get("note") == n and byid[i].get("reply") == r for i, (s, n, r) in before_state.items() if i in byid and not live(i))
    kept_live = all(byid[i].get("note") == n and byid[i].get("reply") == r for i, (s, n, r) in before_state.items() if i in byid and live(i))
    ok("M3 Kevin's status / note / reply on every item is untouched (live ones: note and reply; their status follows the redline folder)", kept and kept_live,
       [i for i, (s, n, r) in before_state.items() if i in byid and (byid[i].get("note"), byid[i].get("reply")) != (n, r)][:8])
    rounds_now = [i for i in t["items"] if i["cat"] == "rounds"]
    old_order = [i["id"] for i in rounds_items]
    ok("M4 the rounds he already had keep their relative order", [i["id"] for i in rounds_now if i["id"] in set(old_order)] == old_order, "")

    def art(i):
        return (i.get("links") or {}).get("editor")
    together = []
    for i in moved:
        pos = next(k for k, x in enumerate(rounds_now) if x["id"] == i["id"])
        same = [k for k, x in enumerate(rounds_now) if art(x) == art(i) and x["id"] != i["id"] and not x.get("moved_from")]
        together.append((i["id"], pos, max(same) if same else None))
    ok("M5 each moved Shadow item sits right after the last round of its article (or at the end when the article has none)",
       all(last is None or pos > last and all(rounds_now[k].get("moved_from") or art(rounds_now[k]) == art(byid[iid]) for k in range(last + 1, pos)) for iid, pos, last in together), together)
    ok("M6 every rounds item resolves to a country group with a flag", all(i.get("group") and i["group"]["flag"] for i in rounds_now), [i["id"] for i in rounds_now if not i.get("group")])
    on_disk = json.loads(TODO.read_text(encoding="utf-8-sig"))
    ok("M7 the migrated shape is persisted on that first GET (categories without shadow, items moved) and no '_migrated' marker leaks", not any(c["id"] == "shadow" for c in on_disk["categories"])
       and not any(i["cat"] == "shadow" for i in on_disk["items"]) and "_migrated" not in on_disk and "group" not in on_disk["items"][0], (list(on_disk), [c["id"] for c in on_disk["categories"]]))
    ok("M8 counts carry no 'shadow' bucket; rounds counts the merged items", "shadow" not in t["counts"] and t["counts"]["rounds"]["open"] + t["counts"]["rounds"]["done"] == len(rounds_now), t["counts"].get("rounds"))
    ok("M8b the payload says which categories are grouped and where the flags live", t.get("grouped") == ["rounds"] and t.get("flags_dir") == "Images/web/flags", (t.get("grouped"), t.get("flags_dir")))

    # live seeding: a new Shadow thread on Prizren and a new round for Istanbul land in rounds, beside their article
    (RL / "_comments" / "kosovo__prizren.json").write_text(json.dumps({"threads": [{"id": "sr-9", "author": "Shadow reviewer", "resolved": False, "replies": []}]}), encoding="utf-8")
    (RL / "istanbul-r9").mkdir()
    (RL / "istanbul-r9" / "proposal.json").write_text(json.dumps({"slug": "istanbul-r9", "title": "Istanbul, round 9", "article_dir": "Drafts/Full Articles/turkiye", "article": "istanbul.html", "changes": [{"id": "g1"}]}), encoding="utf-8")
    (RL / "_comments" / "zz__nowhere.json").write_text(json.dumps({"threads": [{"id": "sr-1", "author": "Shadow reviewer", "resolved": False, "replies": []}]}), encoding="utf-8")
    api.call("/api/todo", {"op": "delete", "id": "shadow-prizren"}) if "shadow-prizren" in byid else None     # so the seed is a NEW item
    st, t = api.call("/api/todo?sync=1")
    rn = [i for i in t["items"] if i["cat"] == "rounds"]; ids = [i["id"] for i in rn]
    sp = next((i for i in rn if i["id"] == "shadow-prizren"), None)
    prev = rn[ids.index("shadow-prizren") - 1] if sp else None
    ok("L1 a new Shadow thread seeds '<Article>: answer the Shadow reviewer' in ROUNDS, country set, right after the article's last round",
       sp and sp["cat"] == "rounds" and sp["title"] == "Prizren: answer the Shadow reviewer" and sp["country"] == "Kosovo" and sp["group"]["key"] == "kosovo" and prev and art(prev) == "Drafts/Upcoming/kosovo/prizren.html"
       and all(art(x) != "Drafts/Upcoming/kosovo/prizren.html" for x in rn[ids.index("shadow-prizren") + 1:]), (sp and sp["cat"], prev and prev["id"]))
    i9 = next((i for i in rn if i["id"] == "round-istanbul-r9"), None)
    prev9 = rn[ids.index("round-istanbul-r9") - 1] if i9 else None
    ok("L2 a new round lands beside its article (after the last Istanbul item), country Türkiye, flag tr", i9 and i9["country"] == "Türkiye" and i9["group"] == {"key": "turkiye", "name": "Türkiye", "flag": "Images/web/flags/tr.png"}
       and prev9 and art(prev9) == "Drafts/Full Articles/turkiye/istanbul.html" and all(art(x) != "Drafts/Full Articles/turkiye/istanbul.html" for x in rn[ids.index("round-istanbul-r9") + 1:]), (i9 and i9.get("group"), prev9 and prev9["id"]))
    nowhere = next((i for i in rn if i["id"] == "shadow-nowhere"), None)
    ok("L3 a Shadow item whose article is nowhere has no group (-> Other) and sits at the end", nowhere and nowhere["group"] is None and ids[-1] == "shadow-nowhere", (nowhere and nowhere.get("group"), ids[-1]))
    # answered: it finishes by itself even though it lives in rounds now
    (RL / "_comments" / "kosovo__prizren.json").write_text(json.dumps({"threads": [{"id": "sr-9", "author": "Shadow reviewer", "resolved": False, "replies": [{"author": "Kevin", "text": "yes"}]}]}), encoding="utf-8")
    st, t = api.call("/api/todo?sync=1")
    sp = next(i for i in t["items"] if i["id"] == "shadow-prizren")
    ok("L4 answering the threads marks the merged Shadow item done ('All answered.')", sp["status"] == "done" and sp["summary"] == "All answered.", (sp["status"], sp["summary"]))
    ok("L5 the old round items still finish by themselves (no redline folder here -> Applied)", all(i["status"] == "done" for i in t["items"] if i["id"].startswith("round-") and i["id"] != "round-istanbul-r9" and i.get("round")), "")
    # an agent adding a Shadow item by hand with the old category still lands in rounds
    st, t = api.call("/api/todo", {"op": "add", "cat": "shadow", "id": "shadow-zz-hand", "title": "Zz: answer the Shadow reviewer", "links": {"editor": "Drafts/Full Articles/greece/athens.html"}})
    hand = next(i for i in t["items"] if i["id"] == "shadow-zz-hand")
    ok("L6 an add with cat 'shadow' (an old agent brief) lands in 'mine' per the unknown-category rule, not in a resurrected shadow category", st == 200 and hand["cat"] == "mine" and not any(c["id"] == "shadow" for c in t["categories"]), (st, hand["cat"]))
    api.call("/api/todo", {"op": "delete", "id": "shadow-zz-hand"})

    # ----------------------------------------------------------------------------- UI
    # a controlled rounds category for the UI: live proposals keep these rounds open (a forced status would be undone by the
    # 30 s sync), a Shadow thread on Athens, and the no-country Shadow item from L3 for the Other row
    for slug, d, a in (("athens-r3", "Drafts/Full Articles/greece", "athens.html"), ("top10-greece-r2", "Drafts/Full Articles/greece", "top-10-greece.html"),
                       ("athens-r2", "Drafts/Full Articles/greece", "athens.html"), ("istanbul-r5", "Drafts/Full Articles/turkiye", "istanbul.html"), ("prizren-r2", "Drafts/Upcoming/kosovo", "prizren.html")):
        (RL / slug).mkdir(exist_ok=True)
        (RL / slug / "proposal.json").write_text(json.dumps({"slug": slug, "title": slug, "article_dir": d, "article": a, "changes": [{"id": "g1"}, {"id": "g2"}]}), encoding="utf-8")
    (RL / "_comments" / "greece__athens.json").write_text(json.dumps({"threads": [{"id": "sr-1", "author": "Shadow reviewer", "resolved": False, "replies": []}]}), encoding="utf-8")
    st, t = api.call("/api/todo?sync=1")
    rn = [i for i in t["items"] if i["cat"] == "rounds"]
    groups = []
    for i in rn:
        k = (i.get("group") or {}).get("key", "_other")
        if k not in groups:
            groups.append(k)
    n_open = lambda k: sum(1 for i in rn if (i.get("group") or {}).get("key", "_other") == k and i["status"] != "done")
    n_all = lambda k: sum(1 for i in rn if (i.get("group") or {}).get("key", "_other") == k)
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(f"window.QUEUE_API = 'http://127.0.0.1:{PORT}'; try {{ if (!sessionStorage.getItem('qrt')) {{ localStorage.clear(); sessionStorage.setItem('qrt', '1'); }} }} catch (e) {{}}")
        pg = ctx.new_page(); errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(EDITOR + "#queue"); pg.wait_for_selector("#app-queue:not([hidden]) .qt-cat", timeout=20000); pg.wait_for_timeout(300)
        ok("U0 7 categories, rounds first, no page errors", pg.locator(".qt-cat").count() == 7 and pg.locator(".qt-cat").first.get_attribute("data-cat") == "rounds" and not errors, (pg.locator(".qt-cat").count(), errors[:2]))
        pg.screenshot(path=str(SHOTS / "queue-rounds-before-open.png"))
        pg.locator(".qt-cat[data-cat='rounds'] .qt-catb").click(); pg.wait_for_timeout(300)
        cg = pg.locator(".qt-cat[data-cat='rounds'] .qt-cg")
        keys = pg.evaluate("[...document.querySelectorAll(\".qt-cat[data-cat='rounds'] .qt-cg\")].map(g => g.dataset.cg)")
        ok("U1 rounds opens to one row per country, in the order his items first name them, Other last, no bare rows", keys == groups and keys[-1] == "_other" and pg.locator(".qt-cat[data-cat='rounds'] > .qt-card > .qt-todo").count() == 0, (keys, groups))
        ok("U1b every country row is collapsed by default", pg.locator(".qt-cat[data-cat='rounds'] .qt-cg.open").count() == 0 and pg.locator(".qt-cat[data-cat='rounds'] .qt-todo").count() == 0, "")
        first = cg.first; k0 = keys[0]
        flag = first.locator("img.fl")
        pg.wait_for_function("document.querySelector('.qt-cg img.fl') && document.querySelector('.qt-cg img.fl').naturalWidth > 0", timeout=10000)
        ok("U2 the country row shows the nav's local flag (natural size loaded), the country name and open / total",
           flag.count() == 1 and f"/site/Images/web/flags/" in flag.get_attribute("src") and pg.evaluate("document.querySelector('.qt-cg img.fl').naturalWidth") > 0
           and first.locator(".nm").inner_text() == next(i["group"]["name"] for i in rn if i["group"] and i["group"]["key"] == k0) and first.locator(".cnt .n").first.inner_text() == f"{n_open(k0)} / {n_all(k0)}",
           (flag.get_attribute("src"), first.locator(".nm").inner_text(), first.locator(".cnt").inner_text()))
        other = cg.last
        ok("U2b Other has a neutral placeholder instead of a flag", other.locator(".fl.none").count() == 1 and other.locator(".nm").inner_text() == "Other", other.inner_text())
        first.locator(".qt-cgb").click(); pg.wait_for_timeout(250)
        rows = pg.locator(f".qt-cat[data-cat='rounds'] .qt-cg[data-cg='{k0}'] [data-list='todo'] > .qt-todo")
        ok("U3 clicking the row opens that country: its open items, in his order, and only them", pg.locator(".qt-cg.open").count() == 1 and rows.count() == n_open(k0)
           and [r for r in pg.evaluate(f"[...document.querySelectorAll(\".qt-cg[data-cg='{k0}'] [data-list='todo'] > .qt-todo\")].map(r => r.dataset.id)")] == [i["id"] for i in rn if (i.get("group") or {}).get("key") == k0 and i["status"] != "done"], rows.count())
        cname = first.locator(".nm").inner_text()
        ok("U3b inside a country the item does not repeat the country chip; its round chip and link chip stay", not any(x.strip() == cname for x in pg.evaluate(f"[...document.querySelectorAll(\".qt-cg[data-cg='{k0}'] .qt-todo .qt-chip.plain\")].map(c => c.textContent)"))
           and pg.locator(f".qt-cg[data-cg='{k0}'] .qt-todo .qt-chip.link").count() >= 1 and pg.locator(f".qt-cg[data-cg='{k0}'] .qt-todo .qt-chip.plain[title]").count() >= 1, "")
        done_fold = pg.locator(f".qt-cg[data-cg='{k0}'] details[data-fold]")
        ok("U3c the country's done items are in its own Done fold", (done_fold.count() == 1) == (n_all(k0) > n_open(k0)) and (not done_fold.count() or done_fold.locator("summary .n").inner_text() == str(n_all(k0) - n_open(k0))), done_fold.count())
        pg.screenshot(path=str(SHOTS / "queue-rounds-desktop.png"))
        pg.reload(); pg.wait_for_selector("#app-queue:not([hidden]) .qt-cat", timeout=20000); pg.wait_for_timeout(400)
        ok("U4 the open country survives a reload (localStorage), the others stay folded", pg.locator(f".qt-cg[data-cg='{k0}'].open").count() == 1 and pg.locator(".qt-cg.open").count() == 1, pg.locator(".qt-cg.open").count())
        pg.locator(f".qt-cg[data-cg='{k0}'] .qt-cgb").click(); pg.wait_for_timeout(200)
        ok("U4b clicking again folds it", pg.locator(".qt-cg.open").count() == 0, "")

        # reorder inside a country: the arrows and a drag change only that country's run; the full order stays grouped
        kk = next(k for k in keys if n_open(k) >= 2)
        pg.locator(f".qt-cg[data-cg='{kk}'] .qt-cgb").click(); pg.wait_for_timeout(250)
        sub0 = pg.evaluate(f"[...document.querySelectorAll(\".qt-cg[data-cg='{kk}'] [data-list='todo'] > .qt-todo\")].map(r => r.dataset.id)")
        all0 = [i["id"] for i in api.call("/api/todo?sync=0")[1]["items"] if i["cat"] == "rounds" and i["status"] != "done"]   # a reorder has always re-appended the done items after the open ones
        pg.locator(f".qt-cg[data-cg='{kk}'] .qt-todo[data-idx='1'] .qt-ib[data-act='todo-move'][data-dir='-1']").click(); pg.wait_for_timeout(400)
        sub1 = pg.evaluate(f"[...document.querySelectorAll(\".qt-cg[data-cg='{kk}'] [data-list='todo'] > .qt-todo\")].map(r => r.dataset.id)")
        all1 = [i["id"] for i in api.call("/api/todo?sync=0")[1]["items"] if i["cat"] == "rounds" and i["status"] != "done"]
        others = lambda lst: [x for x in lst if x not in set(sub0)]
        ok("U5 the up arrow swaps the first two of that country; every other item keeps its place; persisted", sub1 == [sub0[1], sub0[0]] + sub0[2:] and others(all1) == others(all0) and [x for x in all1 if x in set(sub0)] == sub1, (sub0[:3], sub1[:3]))

        def drag(src_sel, dst_sel, where="before"):
            pg.locator(dst_sel).first.scroll_into_view_if_needed(); pg.wait_for_timeout(100)
            s = pg.locator(src_sel).first.bounding_box(); d = pg.locator(dst_sel).first.bounding_box()
            if s["y"] < 70 or s["y"] + s["height"] > 880:
                pg.evaluate("dy => { document.getElementById('queue-root').scrollTop += dy; }", s["y"] - 450); pg.wait_for_timeout(100)
                s = pg.locator(src_sel).first.bounding_box(); d = pg.locator(dst_sel).first.bounding_box()
            y = d["y"] + (3 if where == "before" else d["height"] - 3)
            pg.mouse.move(s["x"] + s["width"] / 2, s["y"] + s["height"] / 2); pg.mouse.down()
            pg.mouse.move(s["x"] + s["width"] / 2, s["y"] + 12, steps=3); pg.mouse.move(d["x"] + 30, y, steps=8); pg.mouse.move(d["x"] + 30, y + (1 if where == "before" else -1), steps=2)
            pg.mouse.up(); pg.wait_for_timeout(500)
        drag(f".qt-cg[data-cg='{kk}'] .qt-todo[data-idx='1'] .qt-grip", f".qt-cg[data-cg='{kk}'] .qt-todo[data-idx='0']", "before")
        sub2 = pg.evaluate(f"[...document.querySelectorAll(\".qt-cg[data-cg='{kk}'] [data-list='todo'] > .qt-todo\")].map(r => r.dataset.id)")
        all2 = [i["id"] for i in api.call("/api/todo?sync=0")[1]["items"] if i["cat"] == "rounds" and i["status"] != "done"]
        ok("U6 dragging an item above the first inside its country reorders just that country (back to the original run); persisted", sub2 == sub0 and others(all2) == others(all1) and [x for x in all2 if x in set(sub0)] == sub0, (sub1[:3], sub2[:3]))
        # a drag onto another country's row is refused (no drop line, nothing written)
        kk2 = next(k for k in keys if k != kk and n_open(k) >= 1)
        pg.locator(f".qt-cg[data-cg='{kk2}'] .qt-cgb").click(); pg.wait_for_timeout(250)
        drag(f".qt-cg[data-cg='{kk}'] .qt-todo[data-idx='0'] .qt-grip", f".qt-cg[data-cg='{kk2}'] .qt-todo[data-idx='0']", "before")
        all3 = [i["id"] for i in api.call("/api/todo?sync=0")[1]["items"] if i["cat"] == "rounds" and i["status"] != "done"]
        ok("U6b an item dropped on another country's row stays where it was", all3 == all2, "")
        # drag a country row above the first: the whole run moves, items inside keep their order
        gi = keys.index(kk)
        if gi > 0:
            drag(f".qt-cg[data-cg='{kk}'] .qt-cgh .qt-grip", f".qt-cg[data-idx='0'] .qt-cgh", "before")
            keys2 = pg.evaluate("[...document.querySelectorAll(\".qt-cat[data-cat='rounds'] .qt-cg\")].map(g => g.dataset.cg)")
            all4 = [i["id"] for i in api.call("/api/todo?sync=0")[1]["items"] if i["cat"] == "rounds" and i["status"] != "done"]
            ok("U7 dragging a country row to the top moves its whole run first (his order inside it kept, Other still last); persisted", keys2[0] == kk and keys2[-1] == "_other" and all4[:len(sub0)] == sub0 and keys2 == [kk] + [k for k in keys if k != kk], (keys2, keys))
        else:
            drag(f".qt-cg[data-idx='1'] .qt-cgh .qt-grip", f".qt-cg[data-idx='0'] .qt-cgh", "before")
            keys2 = pg.evaluate("[...document.querySelectorAll(\".qt-cat[data-cat='rounds'] .qt-cg\")].map(g => g.dataset.cg)")
            ok("U7 dragging the second country row above the first swaps them; persisted", keys2[:2] == [keys[1], keys[0]] and keys2[-1] == "_other", (keys2, keys))
        # the viewer still walks the whole category, the status select and Due-soon jump still work
        shot_item = next((i for i in rn if i["shots"] and i["status"] != "done"), None)
        if shot_item:
            key_s = (shot_item.get("group") or {}).get("key", "_other")
            if not pg.locator(f".qt-cg[data-cg='{key_s}'].open").count():
                pg.locator(f".qt-cg[data-cg='{key_s}'] .qt-cgb").click(); pg.wait_for_timeout(250)
            pg.locator(f".qt-todo[data-id='{shot_item['id']}'] .qt-chip.view").click(); pg.wait_for_selector("#qt-viewer:not([hidden])", timeout=10000); pg.wait_for_timeout(300)
            pos = pg.inner_text("#qt-viewer .pos")
            ok("U8 the viewer opens from inside a country and still counts through the whole category", "Review: rounds" in pos and int(pos.split("/")[1].split("·")[0]) == sum(1 for i in rn if i["shots"] and i["status"] != "done"), pos)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
        row = pg.locator(f".qt-cg[data-cg='{kk}'] .qt-todo").first; rid = row.get_attribute("data-id")
        row.locator(".qt-stsel").select_option("needs-changes"); pg.wait_for_timeout(700)
        hdr = pg.locator(f".qt-cg[data-cg='{kk}'] .qt-cgh .n.needs")
        st9 = next(i for i in api.call("/api/todo?sync=0")[1]["items"] if i["id"] == rid)["status"]
        ok("U9 the status select works inside a country and the country row shows the needs-changes count", st9 == "needs-changes" and hdr.count() == 1,
           (rid, st9, hdr.count(), pg.locator(f".qt-cg[data-cg='{kk}'] .qt-cgh").inner_text(), pg.locator(".qt-toast").inner_text() if pg.locator(".qt-toast").count() else ""))
        api.call("/api/todo", {"op": "update", "id": rid, "due": time.strftime("%Y-%m-%d")})
        pg.evaluate("window.__queueReload()"); pg.wait_for_timeout(600)
        for k in (kk, kk2, k0):
            if pg.locator(f".qt-cg[data-cg='{k}'].open").count():
                pg.locator(f".qt-cg[data-cg='{k}'] .qt-cgb").click(); pg.wait_for_timeout(150)
        pg.locator(f".qt-due a[data-id='{rid}']").click(); pg.wait_for_timeout(400)
        ok("U10 Due soon's jump opens the item's country and lands on the row", pg.locator(f".qt-cg[data-cg='{kk}'].open").count() == 1 and pg.locator(f".qt-todo[data-id='{rid}']").count() == 1, "")
        # other categories are untouched: maps is a flat list with its own drag indices
        pg.locator(".qt-cat[data-cat='maps'] .qt-catb").click(); pg.wait_for_timeout(250)
        ok("U11 Review: maps is still a flat list (no country rows)", pg.locator(".qt-cat[data-cat='maps'] .qt-cg").count() == 0 and pg.locator(".qt-cat[data-cat='maps'] [data-list='todo'] .qt-todo").count() >= 1, "")
        pg.set_viewport_size({"width": 393, "height": 852}); pg.wait_for_timeout(400)
        over = pg.evaluate("[document.scrollingElement.scrollWidth, document.getElementById('queue-root').scrollWidth, document.getElementById('queue-root').clientWidth]")
        ok("U12 393 px: no sideways scroll with countries open", over[0] <= 393 and over[1] <= over[2], over)
        pg.evaluate(f"document.querySelector(\".qt-cat[data-cat='rounds']\").scrollIntoView()"); pg.wait_for_timeout(200)
        pg.screenshot(path=str(SHOTS / "queue-rounds-phone.png"))
        ok("U13 no page errors through the run", not errors, errors[:3])
        b.close()
finally:
    srv.kill()
    shutil.rmtree(SCRATCH, ignore_errors=True)

ok("Z1 the REAL store and queue are byte-identical to before the run", REAL_TODO.read_bytes() == real_todo_before and REAL_MD.read_bytes() == real_md_before, "")
n = sum(1 for _, c, _ in checks if c)
print(f"\n{n}/{len(checks)} passed; screenshots in {SHOTS}\\queue-rounds-*.png")
sys.exit(0 if n == len(checks) else 1)
