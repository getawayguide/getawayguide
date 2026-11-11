"""The editor's Social tab (social.js + tools/social_tab.py; Kevin, 2026-11-08). Everything runs against a SCRATCH copy of
.tmp/ig/armenia (manifest, the seed's plan + map, three web copies) in a temp folder (SOCIAL_IG_DIR) with a scratch output
folder (SOCIAL_OUT_DIR), served by the standalone `python tools/social_tab.py --serve <port>`; the editor is loaded from
file:// with window.SOCIAL_API pointed at that port, so the live photo server is never needed and the real store
(.tmp/ig/<slug>/state), the real renders (.tmp/previews/social-pilot) and Pictures\\IG are never written.

  API    the country list (flag, artifact, has_plan, story counts); the store: a catalog built from the manifest with the
         photo id as its asset, the seed's carousel/main + map copied in, set / delete / batch writing one plain JSON per
         document (what ig_post.py / ig_carousel.py / ig_story.py --state read), a bad path refused, 304 on an unchanged
         version; the blob route; an ArtifactData export (wrapped {data, version} bodies) imported; a second render
         refused while one runs
  RENDER a real carousel render through the job route: 7 slides at 1080x1350 with the sRGB profile + caption.txt, listed
         by /renders and served by the file route; then the Travel Blog STORIES (7 frames at 1080x1920) and the Personal
         post's stories from one final slot (--no-copy: Pictures untouched)
  PAGE   tools/ig_post_page.html served with the local runtime shim: 125 cards from the local catalog, a shortlist key
         writes photos/<id>.json, the template mode opens on the plan; the shortcut hint line is gone (a "?" button holds
         the keys); ?embed=1 drops the page's header (html.embed)
  UI     the Social tab in the bar between Launch and Queue (Ctrl+Alt+6): the two OUTPUT TYPES at the top (Personal post /
         Travel Blog post) driving the maker iframe over postMessage, the sub-tabs with the page's live counts, the
         article filter, Ready, Post / Slides / Stories / Pins with the rendered thumbnails, the Stories strip and the
         lightbox, 393 px without sideways scroll, the restart message on a 404
  REDESIGN (Kevin, 2026-11-11) the old shortlist migrated into the slots (with a state.bak copy, duplicates moved, nothing
         lost); the Shortlist sub-tab is the slots; five to a row with no sideways scroll; empty slots hold only a +;
         a slotted photo has just edit and remove; Photos cards have no star and no caption overlay; + / − on the Final
         row (Instagram's 20 cap); the grid's keys; the four ratios (4:5, 3:4, 1:1, 1.91:1) from the tab's switch; the
         preview (black, big, side arrows, the iframe fills the window); the crop editor's side arrows; Render marks the
         post ready and renders it (a real one-photo render, 1080x1350 then 1080x565 at 1.91:1); photos added from the
         photo library (mocked album routes, a scratch backup folder)
  REAL   the real store, renders and Pictures\\IG untouched

    python tools/test_social_tab.py
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

from PIL import Image
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)
EDITOR = (ROOT / "editor.html").as_uri()
SHOTS = ROOT / ".tmp" / "previews"
SHOTS.mkdir(parents=True, exist_ok=True)
REAL = ROOT / ".tmp" / "ig" / "armenia"
REAL_OUT = ROOT / ".tmp" / "previews" / "social-pilot"
PICS = pathlib.Path(os.environ.get("USERPROFILE", str(pathlib.Path.home()))) / "Pictures" / "IG" / "armenia"
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

    def call(self, path, body=None, raw=False):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"} if body is not None else {}, method="POST" if body is not None else "GET")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
                    return r.status, (data if raw else (json.loads(data.decode("utf-8")) if data else {}))
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.loads(e.read().decode("utf-8"))
                except Exception:
                    return e.code, {}
            except (ConnectionResetError, ConnectionAbortedError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(0.3)

    def wait_job(self, jid, t=300):
        end = time.time() + t
        while time.time() < end:
            st, jj = self.call("/api/social/job/" + jid)
            if jj.get("job", {}).get("status") in ("done", "failed"):
                return jj.get("job", {})
            time.sleep(2)
        return jj.get("job", {})


def snapshot(folder):
    return sorted((str(p.relative_to(folder)), p.stat().st_mtime_ns, p.stat().st_size) for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else None


# ----------------------------------------------------------------------------- scratch fixtures
if not (REAL / "manifest.json").is_file():
    print("no .tmp/ig/armenia/manifest.json: run  python tools/ig_post.py Armenia  first"); sys.exit(1)
SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="social_tab_test_"))
IG = SCRATCH / "ig"; OUT = SCRATCH / "out"
(IG / "armenia" / "web").mkdir(parents=True); (IG / "armenia" / "carousel").mkdir(); OUT.mkdir()
shutil.copyfile(REAL / "manifest.json", IG / "armenia" / "manifest.json")
for n in ("main.json", "map.json"):
    shutil.copyfile(REAL / "carousel" / n, IG / "armenia" / "carousel" / n)
man = json.loads((REAL / "manifest.json").read_text(encoding="utf-8"))
web_ids = []
for p in man["photos"][:3]:
    src = REAL / "web" / (p["id"] + ".jpg")
    if src.is_file():
        shutil.copyfile(src, IG / "armenia" / "web" / src.name); web_ids.append(p["id"])
real_state0, real_out0 = snapshot(REAL / "state"), snapshot(REAL_OUT)
pics0 = snapshot(PICS)
real_free0 = snapshot(REAL / "out")
# a scratch photo library (the Library app's backed-up albums) with one album and two photos
BACKUP = SCRATCH / "backup"; ALBUM = "Armenia (2025)"
(BACKUP / ALBUM).mkdir(parents=True)
lib_names = []
for i, wid in enumerate(web_ids[:2]):
    shutil.copyfile(REAL / "web" / (wid + ".jpg"), BACKUP / ALBUM / ("IMG_90%02d.JPG" % i)); lib_names.append("IMG_90%02d.JPG" % i)

PORT = free_port()
env = dict(os.environ, SOCIAL_IG_DIR=str(IG), SOCIAL_OUT_DIR=str(OUT), SOCIAL_BACKUP_DIR=str(BACKUP), PYTHONIOENCODING="utf-8")
srv = subprocess.Popen([sys.executable, str(ROOT / "tools" / "social_tab.py"), "--serve", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == "win32" else 0)
if not wait_port(PORT):
    srv.kill(); raise RuntimeError("social_tab server did not start")
api = Api(PORT)

try:
    # ----------------------------------------------------------------------------- API
    st, j = api.call("/api/social")
    c = next((x for x in j.get("countries", []) if x["slug"] == "armenia"), None)
    ok("A1 /api/social lists the scratch country with its flag, artifact, counts (stories too) and plan", st == 200 and c and c["country"] == "Armenia" and c["photos"] == len(man["photos"])
       and c["flag"] == "Images/web/flags/am.png" and "claude.ai/artifact/" in (c["artifact"] or "") and c["has_plan"] and c["has_seed"]
       and c["renders"] == {"carousel": 0, "free": 0, "pins": 0, "stories_carousel": 0, "stories_free": 0}, (st, c))
    st, db = api.call("/api/social/armenia/db")
    cat = db.get("docs", {}).get("catalog/main") or {}
    ok("A2 the store's first read builds catalog/main from the manifest (asset = photo id) and copies the seed's plan + map in",
       st == 200 and len(cat.get("items") or []) == len(man["photos"]) and cat["items"][0]["asset"] == cat["items"][0]["id"] and cat.get("country") == "Armenia"
       and "carousel/main" in db["docs"] and "carousel/map" in db["docs"] and db["docs"]["carousel/main"].get("stops") and len(db["version"]) == 16
       and (IG / "armenia" / "state" / "catalog" / "main.json").is_file() and (IG / "armenia" / "state" / "carousel" / "main.json").is_file(), (st, sorted(db.get("docs", {}))))
    v0 = db["version"]
    st, _ = api.call("/api/social/armenia/db?version=" + v0, raw=True)
    ok("A2b the same version answers 304", st == 304, st)
    pid = man["photos"][0]["id"]
    st, w = api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + pid, "data": {"state": "shortlist", "slot": None, "order": 1, "note": "", "crop": None}})
    f = IG / "armenia" / "state" / "photos" / (pid + ".json")
    ok("A3 set writes one plain JSON per document (what ig_post.py --state reads) and the version moves", st == 200 and w["version"] != v0 and f.is_file() and json.loads(f.read_text(encoding="utf-8"))["state"] == "shortlist", (st, w))
    st, w2 = api.call("/api/social/armenia/db", {"op": "batch", "ops": [{"op": "set", "path": "post/main", "data": {"status": "draft", "aspect": "4:5"}}, {"op": "set", "path": "captions/c1", "data": {"text": "hello", "stars": 3}}]})
    st2, w3 = api.call("/api/social/armenia/db", {"op": "delete", "path": "captions/c1"})
    ok("A3b batch writes several, delete removes the file", st == 200 and (IG / "armenia" / "state" / "post" / "main.json").is_file() and st2 == 200 and not (IG / "armenia" / "state" / "captions" / "c1.json").is_file(), (st, st2))
    st, e = api.call("/api/social/armenia/db", {"op": "set", "path": "../../evil", "data": {}})
    st2, e2 = api.call("/api/social/armenia/db", {"op": "set", "path": "secrets/x", "data": {}})
    ok("A3c a path outside the five collections, or with dots, is refused (400)", st == 400 and st2 == 400 and not (SCRATCH / "evil.json").is_file(), (st, st2))
    st, e = api.call("/api/social/nowhere/db")
    ok("A3d an unknown country is a 404", st == 404, st)

    # the old shortlist becomes the slots (Kevin, 2026-11-11): two 'shortlist' photos in their order, and two photos
    # claiming one slot, all land in free slots after the Final row; the state is copied to state.bak-* first
    p2, p3, p4 = (man["photos"][k]["id"] for k in (1, 2, 3))
    for path, data in (("photos/" + p2, {"state": "shortlist", "slot": None, "order": 0, "note": "keep me", "crop": None}),
                       ("photos/" + p3, {"state": "final", "slot": 3, "order": 0, "note": "", "crop": None}),
                       ("photos/" + p4, {"state": "final", "slot": 3, "order": 0, "note": "", "crop": None})):
        api.call("/api/social/armenia/db", {"op": "set", "path": path, "data": data})
    st, db = api.call("/api/social/armenia/db")
    d = db.get("docs", {})
    first3, other3 = sorted([p3, p4])
    baks = list((IG / "armenia").glob("state.bak-*"))
    ok("M1 GET migrates: the old shortlist goes into slots 11, 12 in its order (the note kept), a duplicate slot 3 moves to 13; a state.bak copy holds the old docs",
       d.get("photos/" + p2, {}).get("slot") == 11 and d["photos/" + p2]["state"] == "final" and d["photos/" + p2]["note"] == "keep me"
       and d.get("photos/" + pid, {}).get("slot") == 12 and d["photos/" + pid]["state"] == "final"
       and d.get("photos/" + first3, {}).get("slot") == 3 and d.get("photos/" + other3, {}).get("slot") == 13
       and len(baks) == 1 and json.loads((baks[0] / "photos" / (p2 + ".json")).read_text(encoding="utf-8"))["state"] == "shortlist",
       ({k: (v.get("state"), v.get("slot")) for k, v in d.items() if k.startswith("photos/")}, baks))
    st, db2 = api.call("/api/social/armenia/db")
    ok("M1b a second read migrates nothing more (no new backup, same slots)", len(list((IG / "armenia").glob("state.bak-*"))) == 1
       and db2["docs"]["photos/" + p2]["slot"] == 11, len(list((IG / "armenia").glob("state.bak-*"))))
    for x in (p2, p3, p4):
        api.call("/api/social/armenia/db", {"op": "delete", "path": "photos/" + x})
    api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + pid, "data": {"state": "final", "slot": 11, "order": 0, "note": "", "crop": None}})
    if web_ids:
        st, raw = api.call("/api/social/armenia/blob/" + web_ids[0], raw=True)
        st2, _ = api.call("/api/social/armenia/blob/not-a-photo", raw=True)
        ok("A4 the blob route serves the web copy (JPEG) and 404s a missing id", st == 200 and raw[:2] == b"\xff\xd8" and st2 == 404, (st, st2))
    exp = ROOT / ".tmp" / ("social_tab_test_" + SCRATCH.name[-8:]) / "export"; (exp / "photos").mkdir(parents=True); (exp / "post").mkdir()   # an export must sit under .tmp (the server's rule)
    (exp / "photos" / "zz-imported.json").write_text(json.dumps({"id": "zz-imported", "version": "v1", "data": {"state": "final", "slot": 1, "order": 0, "note": "", "crop": None}}), encoding="utf-8")
    (exp / "post" / "main.json").write_text(json.dumps({"status": "ready", "aspect": "1:1"}), encoding="utf-8")
    st, imp = api.call("/api/social/armenia/import", {"dir": str(exp)})
    st_ok = st == 200 and imp.get("copied") == 2
    st2, imp2 = api.call("/api/social/armenia/import", {"dir": str(ROOT / ".tmp")})       # a folder under .tmp but inside the repo is allowed; one elsewhere is refused
    st3, e3 = api.call("/api/social/armenia/import", {"dir": str(SCRATCH)})
    ok("A5 import copies an ArtifactData export (wrapped bodies unwrapped) into the store; a folder outside .tmp is refused", st_ok
       and json.loads((IG / "armenia" / "state" / "photos" / "zz-imported.json").read_text(encoding="utf-8")) == {"state": "final", "slot": 1, "order": 0, "note": "", "crop": None}
       and json.loads((IG / "armenia" / "state" / "post" / "main.json").read_text(encoding="utf-8"))["status"] == "ready" and st3 == 400, (st, imp, st3, e3))
    api.call("/api/social/armenia/db", {"op": "delete", "path": "photos/zz-imported"})
    api.call("/api/social/armenia/db", {"op": "set", "path": "post/main", "data": {"status": "draft", "aspect": "4:5"}})
    shutil.rmtree(exp.parent, ignore_errors=True)

    # ----------------------------------------------------------------------------- RENDER (a real one, in the background while the UI is checked)
    st, r = api.call("/api/social/armenia/render", {"kind": "carousel"})
    job = r.get("job") or {}
    st2, r2 = api.call("/api/social/armenia/render", {"kind": "carousel"})
    ok("R1 a carousel render starts as a job; a second one for the same country is refused (409) while it runs", st == 200 and job.get("id") and job.get("status") in ("queued", "running") and st2 == 409, (st, r, st2, r2))
    st, lst = api.call("/api/social")
    ok("R1b /api/social says the country is rendering", st == 200 and next(x for x in lst["countries"] if x["slug"] == "armenia")["running"] is True, st)
    st, e = api.call("/api/social/armenia/render", {"kind": "nope"})
    ok("R1c an unknown kind is refused", st in (400, 409), (st, e))

    # ----------------------------------------------------------------------------- PAGE + UI
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        # the stored choices are wiped once per origin (sessionStorage flag), so a reload can still remember them
        ctx.add_init_script(f"window.SOCIAL_API = 'http://127.0.0.1:{PORT}'; try {{ if (!sessionStorage.getItem('so-test')) {{ localStorage.clear(); sessionStorage.setItem('so-test', '1'); }} }} catch (e) {{}}")
        errors = []
        pg = ctx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
        pend = {}
        pg.on("request", lambda r: pend.__setitem__(r.url, 1)); pg.on("requestfinished", lambda r: pend.pop(r.url, None)); pg.on("requestfailed", lambda r: pend.pop(r.url, None))
        try:
            pg.goto(f"http://127.0.0.1:{PORT}/social/armenia/page", wait_until="domcontentloaded")
            pg.wait_for_load_state("load", timeout=30000)
        except Exception as e:
            print("  (the page's load event is late; still waiting on: %s)" % list(pend)[:6], flush=True)
        pg.wait_for_selector(".card[data-id]", timeout=20000); pg.wait_for_timeout(500)
        n_cards = pg.locator(".card[data-id]").count()
        ok("P1 the page served with the shim loads every photo of the local catalog, titled for the country, with Claude's own runtime absent", n_cards == len(man["photos"]) and pg.inner_text("#title").startswith("Armenia")
           and pg.evaluate("window.claude && window.claude.local === true") and pg.evaluate("window.__socialDb.loaded()"), (n_cards, errors[:2]))
        if web_ids:
            src = pg.get_attribute(f".card[data-id='{web_ids[0]}'] img", "src")
            ok("P1b a card's image is the blob route of this server", src and f"/api/social/armenia/blob/{web_ids[0]}" in src, src)
        ok("P1c standalone (the artifacts' look): the page keeps its own header, the shortcut hint line is gone and a ? button holds the keys", pg.locator("header.top").is_visible()
           and pg.locator("#hint").count() == 0 and pg.locator("#helpBtn").count() == 1 and "Shift+arrows" in (pg.get_attribute("#helpBtn", "title") or "")
           and not pg.evaluate("document.documentElement.classList.contains('embed')"), pg.get_attribute("#helpBtn", "title"))
        pf = IG / "armenia" / "state" / "photos" / (pid + ".json")
        pg.locator(f".card[data-id='{pid}']").click(); pg.keyboard.press("s"); pg.wait_for_timeout(700)
        ok("P2 the S key on a photo in the Shortlist takes it out through the shim: the page deletes photos/<id>.json", not pf.is_file(), pf.is_file())
        pg.keyboard.press("s"); pg.wait_for_timeout(700)
        ok("P2b ...and again puts it in the first free Shortlist slot (11): state final, slot 11", pf.is_file() and json.loads(pf.read_text(encoding="utf-8")).get("slot") == 11
           and json.loads(pf.read_text(encoding="utf-8")).get("state") == "final", pf.read_text(encoding="utf-8") if pf.is_file() else None)
        pg.locator("#modeSeg button[data-mode='template']").click(); pg.wait_for_timeout(800)
        ok("P3 the Itinerary template mode opens on the plan from the store (its stops drawn)", pg.locator("#modeSeg button[data-mode='template'][aria-pressed='true']").count() == 1
           and pg.evaluate("Object.keys(window.__socialDb.docs()).includes('carousel/main')") and pg.locator("main").inner_text().strip() != "", pg.locator("main").inner_text()[:120])
        ok("P4 no page errors in the maker", not errors, errors[:3])
        pg.close()

        # the editor
        pg2 = ctx.new_page(); errs2 = []
        pg2.on("pageerror", lambda e: errs2.append(str(e)))
        pg2.goto(EDITOR + "#social")
        pg2.wait_for_selector("#app-social:not([hidden]) .so-tabs", timeout=20000); pg2.wait_for_timeout(500)
        tab = pg2.locator(".app-tabs a[data-view='social']").first
        views = pg2.evaluate("[...document.querySelectorAll('.app-tabs a')].map(a => a.dataset.view)")
        ok("U1 the Social tab sits between Launch and Queue, tooltip says Ctrl+Alt+6", tab.count() and "Ctrl+Alt+6" in (tab.get_attribute("title") or "") and views[-3:] == ["launch", "social", "queue"], (views, tab.get_attribute("title")))
        pg2.keyboard.press("Control+Alt+7"); pg2.wait_for_timeout(200)
        v7 = pg2.evaluate("[document.getElementById('app-social').hidden, document.getElementById('app-queue').hidden]")
        pg2.keyboard.press("Control+Alt+6"); pg2.wait_for_timeout(300)
        v6 = pg2.evaluate("[document.getElementById('app-social').hidden, document.getElementById('app-queue').hidden, location.hash]")
        ok("U2 Ctrl+Alt+7 shows Queue, Ctrl+Alt+6 brings Social back (#social)", v7 == [True, False] and v6 == [False, True, "#social"], (v7, v6))
        ok("U3 the country picker shows Armenia with its flag, the claude.ai link and the maker iframe on this server in embed mode", pg2.locator(".so-country").input_value() == "armenia"
           and "flags/am.png" in (pg2.get_attribute(".so-pick img", "src") or "") and "claude.ai/artifact/" in (pg2.get_attribute(".so-btn.art", "href") or "")
           and f"127.0.0.1:{PORT}/social/armenia/page?embed=1" in (pg2.get_attribute("#so-frame", "src") or ""), pg2.get_attribute("#so-frame", "src"))
        seg = pg2.locator(".so-seg:not(.sm) button")
        ok("U3a the two output types sit at the top, Personal post on by default", seg.count() == 2 and seg.nth(0).inner_text() == "Personal post" and seg.nth(1).inner_text() == "Travel Blog post"
           and pg2.locator(".so-seg button.on[data-type='personal']").count() == 1, [seg.nth(i).inner_text() for i in range(seg.count())])
        fr = pg2.frame_locator("#so-frame")
        fr.locator(".card[data-id]").first.wait_for(timeout=20000)
        pg2.wait_for_function("document.querySelector('.so-tab[data-view=\"all\"] .n') !== null", timeout=15000)
        ok("U3b the maker inside the tab shows the photos with NO inner header (html.embed, the editor's fonts), and the Photos sub-tab carries the page's count",
           fr.locator(".card[data-id]").count() == len(man["photos"]) and fr.locator("header.top").is_hidden() and fr.locator("html.embed").count() == 1
           and pg2.inner_text(".so-tab[data-view='all'] .n") == str(len(man["photos"]))
           and "Hanken" in fr.locator("body").evaluate("e => getComputedStyle(e).fontFamily"), (pg2.inner_text(".so-tabs"), fr.locator("body").evaluate("e => getComputedStyle(e).fontFamily")))
        labels = pg2.evaluate("[...document.querySelectorAll('.so-tab')].map(b => b.dataset.view)")
        ok("U3c Personal post's sub-tabs: Photos, Shortlist (the slots), Archived, Caption | Post, Stories; the filter, the ratio switch, Add photos, Preview, ? and Render in the bar",
           labels == ["all", "final", "archived", "caption", "renders", "stories"] and pg2.inner_text(".so-tab[data-view='final']").lower().startswith("shortlist")
           and pg2.locator(".so-sel[data-act='article']").count() == 1 and pg2.locator(".so-seg.sm button[data-act='aspect']").count() == 4
           and pg2.locator(".so-btn[data-act='library']").count() == 1 and pg2.locator(".so-btn[data-act='preview']").count() == 1
           and "Shift+arrows" in (pg2.get_attribute(".so-ib[data-act='help']", "title") or "") and pg2.inner_text(".so-btn[data-act='rendernow']").strip().lower() == "render"
           and pg2.locator(".so-pill").count() == 0 and "ready to render" not in pg2.inner_text("#app-social").lower() and fr.locator("#hint").count() == 0, (labels, pg2.inner_text(".so-tabs .acts")))
        ok("U3g the three round buttons at the top (open in a window, import, reload) are gone; the claude.ai link stays",
           pg2.locator(".so-top .so-ib").count() == 0 and pg2.locator(".so-top [data-act='import'], .so-top [data-act='reload']").count() == 0 and pg2.locator(".so-top .so-btn.art").count() == 1, pg2.inner_html(".so-top")[-300:])
        cards = fr.locator(".card[data-id]")
        tops = fr.locator(".grid").evaluate("g => [...g.querySelectorAll('.card')].slice(0, 6).map(c => Math.round(c.getBoundingClientRect().top))")
        fo = fr.locator("html").evaluate("e => [e.scrollWidth, e.clientWidth]")
        ok("N1 Photos: five to a row, no sideways scroll, no caption overlay, and each card's only button is archive (no shortlist star)",
           len(set(tops[:5])) == 1 and tops[5] > tops[0] and fo[0] <= fo[1] and fr.locator(".card .over").count() == 0 and fr.locator(".card [data-act='star']").count() == 0
           and cards.first.locator("button").count() == 1 and cards.first.locator("button[data-act='arch']").count() == 1, (tops, fo))
        frame_el = pg2.evaluate_handle("document.getElementById('so-frame')")
        pg2.locator(".so-tab[data-view='final']").click(); pg2.wait_for_timeout(700)
        fin, sho = fr.locator(".rowwrap.final .frame"), fr.locator(".rowwrap.backlog .frame")
        ok("U3d the Shortlist sub-tab drives the page's view (the slots: a Final row of 10 and the Shortlist, the photo in its first slot; the iframe stays)",
           fin.count() == 10 and sho.count() >= 20 and fr.locator(f".rowwrap.backlog .frame[data-id='{pid}'][data-slot='11']").count() == 1
           and pg2.locator(".so-tab.on[data-view='final']").count() == 1 and pg2.evaluate("el => el === document.getElementById('so-frame')", frame_el), (fin.count(), sho.count()))
        tops = fr.locator(".rowwrap.final .slots").evaluate("g => [...g.querySelectorAll('.frame')].map(c => Math.round(c.getBoundingClientRect().top))")
        fo = fr.locator("html").evaluate("e => [e.scrollWidth, e.clientWidth]")
        slots_over = fr.locator(".slots").first.evaluate("e => [e.scrollWidth, e.clientWidth]")
        empty_txt = fr.locator(".rowwrap.final .frame[data-slot='1']").inner_text().strip()
        filled = fr.locator(f".frame[data-id='{pid}']")
        ok("N2 slots: five to a row, no sideways scroll; an empty slot holds only a +; a filled one has exactly two buttons (edit, remove), no caption line, and a Notes field",
           len(set(tops[:5])) == 1 and tops[5] > tops[0] and fo[0] <= fo[1] and slots_over[0] <= slots_over[1] and empty_txt == "+"
           and filled.locator("button").count() == 2 and filled.locator("button[data-act='crop']").count() == 1 and filled.locator("button[data-act='unslot']").count() == 1
           and fr.locator(".slot .cap").count() == 0 and fr.locator(f"textarea[data-note='{pid}']").get_attribute("placeholder") == "Notes", (tops, fo, slots_over, empty_txt))
        post_f = IG / "armenia" / "state" / "post" / "main.json"
        rd_post = lambda: json.loads(post_f.read_text(encoding="utf-8")) if post_f.is_file() else {}
        fr.locator(".rowhead .rbtn[data-slots='1']").click(); pg2.wait_for_timeout(800)
        ok("N3 + on the Final row adds slide 11: post/main.nfinal = 11 and the Shortlist moved one slot along (the photo in 11 is now in 12, not in the post)",
           rd_post().get("nfinal") == 11 and json.loads(pf.read_text(encoding="utf-8"))["slot"] == 12 and fr.locator(".rowwrap.final .frame").count() == 11
           and "/11" in pg2.inner_text(".so-tab[data-view='final'] .n"), (rd_post(), pf.read_text(encoding="utf-8")))
        fr.locator(".rowhead .rbtn[data-slots='-1']").click(); pg2.wait_for_timeout(800)
        ok("N3b − takes it back to 10 (the button only shows past 10; Instagram's cap of 20 hides the +)", rd_post().get("nfinal") == 10 and fr.locator(".rowwrap.final .frame").count() == 10
           and fr.locator(".rowhead .rbtn[data-slots='-1']").count() == 0, rd_post())
        # the grid's keys: arrows walk the slots (the empty ones too), 1 puts the photo on slide 1, Delete takes it out
        fr.locator(".rowwrap.final .frame[data-slot='1']").click(); pg2.wait_for_timeout(150)
        pg2.keyboard.press("ArrowRight"); pg2.keyboard.press("ArrowDown"); pg2.wait_for_timeout(150)
        at = fr.locator("html").evaluate("() => document.activeElement && document.activeElement.dataset.slot")
        fr.locator(f".frame[data-id='{pid}']").focus(); pg2.keyboard.press("1"); pg2.wait_for_timeout(700)
        on1 = json.loads(pf.read_text(encoding="utf-8")).get("slot")
        pg2.keyboard.press("Shift+ArrowRight"); pg2.wait_for_timeout(700)
        on2 = json.loads(pf.read_text(encoding="utf-8")).get("slot")
        ok("N4 keys: → then ↓ from slot 1 lands on slot 7; 1 puts the focused photo on slide 1; Shift+→ carries it to slide 2", at == "7" and on1 == 1 and on2 == 2, (at, on1, on2))
        # the ratio switch in the tab: the post and the slots follow
        pg2.locator(".so-seg.sm button[data-aspect='1.91:1']").click(); pg2.wait_for_timeout(800)
        r = fr.locator(f".frame[data-id='{pid}']").evaluate("e => { const b = e.getBoundingClientRect(); return b.width / b.height; }")
        ok("N5 the ratio switch (4:5, 3:4, 1:1, 1.91:1) sets post/main.aspect and the slides draw at it (1.91:1 here)", rd_post().get("aspect") == "1.91:1" and abs(r - 1.91) < .08
           and pg2.locator(".so-seg.sm button.on[data-aspect='1.91:1']").count() == 1, (rd_post().get("aspect"), r))
        pg2.locator(".so-seg.sm button[data-aspect='3:4']").click(); pg2.wait_for_timeout(700)
        r = fr.locator(f".frame[data-id='{pid}']").evaluate("e => { const b = e.getBoundingClientRect(); return b.width / b.height; }")
        ok("N5b ...and 3:4 (Instagram's tall portrait since May 2025)", rd_post().get("aspect") == "3:4" and abs(r - .75) < .03, (rd_post().get("aspect"), r))
        pg2.locator(".so-seg.sm button[data-aspect='4:5']").click(); pg2.wait_for_timeout(500)
        # the preview: only the photos, big, on black; the iframe takes the window while it is open
        pg2.locator(".so-btn[data-act='preview']").click(); pg2.wait_for_timeout(900)
        pv = fr.locator("#pv")
        full_on = pg2.evaluate("document.getElementById('so-frame').classList.contains('full')")
        box = pg2.evaluate("(() => { const b = document.getElementById('so-frame').getBoundingClientRect(); return [b.width, b.height]; })()")
        bg = pv.evaluate("e => getComputedStyle(e).backgroundColor") if pv.is_visible() else None
        ok("N6 Preview: the iframe fills the window, the preview is black with the slide drawn big and the library's side arrows, nothing else",
           full_on and box == [1440, 900] and pv.is_visible() and bg == "rgb(0, 0, 0)" and fr.locator("#pvStage canvas").count() == 1 and fr.locator("#pv .sidenav").count() == 2
           and fr.locator("#pvStage canvas").evaluate("c => c.getBoundingClientRect().height") > 600, (full_on, box, bg))
        pg2.screenshot(path=str(SHOTS / "social-redesign-test-preview-1440.png"))
        pg2.keyboard.press("Escape"); pg2.wait_for_timeout(400)
        ok("N6b Esc closes it and the iframe goes back into the tab", not pv.is_visible() and not pg2.evaluate("document.getElementById('so-frame').classList.contains('full')"), "")
        # the crop editor: big side arrows, four ratios
        fr.locator(f".frame[data-id='{pid}']").hover(); fr.locator(f".frame[data-id='{pid}'] button[data-act='crop']").click(); pg2.wait_for_timeout(600)
        sl = fr.locator("#modal .sidenav.l").evaluate("e => { const b = e.getBoundingClientRect(); return [b.left, b.width, b.height]; }")
        ok("N7 the crop editor: Prev / Next are the big side arrows at the window's edges, the ratio buttons are 4:5 3:4 1:1 1.91:1, the iframe fills the window",
           fr.locator("#modal").is_visible() and sl[0] < 30 and sl[1] >= 50 and sl[2] >= 80 and fr.locator("#modal .nav #prevBtn").count() == 0
           and [b.inner_text() for b in fr.locator("#aspectSeg button").all()] == ["4:5", "3:4", "1:1", "1.91:1"]
           and pg2.evaluate("document.getElementById('so-frame').classList.contains('full')"), sl)
        pg2.screenshot(path=str(SHOTS / "social-redesign-test-crop-1440.png"))
        fr.locator("#doneBtn").click(); pg2.wait_for_timeout(400)
        fr.locator(f".frame[data-id='{pid}']").focus(); pg2.keyboard.press("Delete"); pg2.wait_for_timeout(700)
        ok("N8 Delete takes the photo out of its slot (back with the photos: no doc left)", not pf.is_file() and not pg2.evaluate("document.getElementById('so-frame').classList.contains('full')"), pf.is_file())
        api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + pid, "data": {"state": "final", "slot": 11, "order": 0, "note": "", "crop": None}})
        pg2.locator(".so-tab[data-view='all']").click(); pg2.wait_for_timeout(600)
        pg2.locator(".so-sel[data-act='article']").select_option(index=1); pg2.wait_for_timeout(600)
        picked = pg2.evaluate("document.querySelector('.so-sel[data-act=\"article\"]').value")
        n_f = fr.locator(".card[data-id]").count()
        ok("U3e the article filter in the tab's bar filters the page", picked and 0 < n_f < len(man["photos"]) and n_f == sum(1 for x in man["photos"] if x["article"] == picked), (picked, n_f))
        pg2.locator(".so-sel[data-act='article']").select_option(value=""); pg2.wait_for_timeout(500)
        pg2.screenshot(path=str(SHOTS / "social-tab-personal-1440.png"))

        # photos from the photo library: the album routes are the photo server's own (mocked here), the add is real, from
        # the scratch backup folder (SOCIAL_BACKUP_DIR)
        jpg = (BACKUP / ALBUM / lib_names[0]).read_bytes()
        pg2.route(f"http://127.0.0.1:{PORT}/api/albums", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps({"albums": [{"name": "Albania (2025)"}, {"name": ALBUM}]})))
        pg2.route(f"http://127.0.0.1:{PORT}/api/backup_browse*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps({"album": ALBUM, "photos": [{"n": x, "picked": i == 0} for i, x in enumerate(lib_names)]})))
        pg2.route(f"http://127.0.0.1:{PORT}/bthumb*", lambda r: r.fulfill(status=200, content_type="image/jpeg", body=jpg))
        n_before = fr.locator(".card[data-id]").count()
        pg2.locator(".so-btn[data-act='library']").click()
        pg2.wait_for_selector("#so-lib:not([hidden]) .t", timeout=10000)
        ok("N9 Add photos opens the library picker on the country's album (Armenia (2025)), its photos as tiles, the blog pick marked",
           pg2.locator("#so-lib select").input_value() == ALBUM and pg2.locator("#so-lib .t").count() == 2 and pg2.locator("#so-lib .t.pk").count() == 1, pg2.locator("#so-lib select").input_value())
        pg2.locator("#so-lib .t").nth(1).click()
        add_txt = pg2.inner_text("#so-lib [data-l='add']")
        pg2.screenshot(path=str(SHOTS / "social-redesign-test-library-1440.png"))
        pg2.locator("#so-lib [data-l='add']").click()
        pg2.wait_for_function("document.getElementById('so-lib').hidden", timeout=60000)
        for _ in range(40):
            if fr.locator(".card[data-id]").count() == n_before + 1:
                break
            pg2.wait_for_timeout(500)
        man2 = json.loads((IG / "armenia" / "manifest.json").read_text(encoding="utf-8"))
        new = [x for x in man2["photos"] if x.get("source") == "library"]
        webf = IG / "armenia" / "web" / ((new[0]["id"] if new else "x") + ".jpg")
        im = Image.open(webf) if webf.is_file() else None
        ok("N9b Add 1 photo: a 1280 px sRGB web copy and a manifest entry (the album file as its original, 'Photo library: <album>'), and the page shows one more photo",
           add_txt.lower() == "add 1 photo" and len(new) == 1 and new[0]["name"] == lib_names[1] and new[0]["article_title"] == "Photo library: " + ALBUM
           and new[0]["original"].endswith(lib_names[1]) and im is not None and max(im.size) <= 1280 and bool(im.info.get("icc_profile"))
           and fr.locator(".card[data-id]").count() == n_before + 1 and fr.locator(f".card[data-id='{new[0]['id'] if new else 'x'}']").count() == 1, (add_txt, new, n_before, fr.locator(".card[data-id]").count()))
        from urllib.parse import quote
        st, libj = api.call("/api/social/armenia/library?album=" + quote(ALBUM))
        st2, again = api.call("/api/social/armenia/add", {"album": ALBUM, "names": [lib_names[1]]})
        st3, bad = api.call("/api/social/armenia/add", {"album": "../..", "names": ["x.jpg"]})
        ok("N9c /library names it as already in, adding it twice skips it, and an album outside the library is refused", st == 200 and libj.get("names") == [lib_names[1]]
           and st2 == 200 and again.get("added") == [] and again.get("skipped") == [lib_names[1]] and st3 == 400, (libj, again, st3))
        pg2.locator(".so-btn[data-act='library']").click(); pg2.wait_for_selector("#so-lib:not([hidden]) .t", timeout=10000)
        ok("N9d the picker shows the added photo as already in", pg2.locator(f"#so-lib .t.in[data-n='{lib_names[1]}']").count() == 1, "")
        pg2.keyboard.press("Escape"); pg2.wait_for_timeout(200)

        # Travel Blog post
        pg2.locator(".so-seg button[data-type='blog']").click(); pg2.wait_for_timeout(900)
        labels = pg2.evaluate("[...document.querySelectorAll('.so-tab')].map(b => b.dataset.view)")
        ok("U4 Travel Blog post: the page switches to the template mode on the plan (same iframe), sub-tabs Layout | Slides, Stories, Pins, the Layout count = the slides",
           labels == ["layout", "slides", "stories", "pins"] and fr.locator("#modeSeg button[data-mode='template'][aria-pressed='true']").count() == 1
           and fr.locator(".tslide").count() == 7 and pg2.inner_text(".so-tab[data-view='layout'] .n") == "7"
           and pg2.evaluate("el => el === document.getElementById('so-frame')", frame_el), (labels, fr.locator(".tslide").count()))
        pg2.screenshot(path=str(SHOTS / "social-tab-blog-1440.png"))
        pg2.locator(".so-tab[data-view='slides']").click(); pg2.wait_for_timeout(400)
        pg2.wait_for_timeout(600)
        _, rr = api.call("/api/social/armenia/renders")
        ok("U4b Slides shows its group with the Render carousel button, disabled exactly while the render runs (the earlier tests may outlast it)", pg2.locator(".so-group").count() == 1
           and pg2.locator(".so-btn[data-act='render'][data-kind='carousel']").count() == 1
           and (pg2.locator(".so-btn[data-act='render'][data-kind='carousel'][disabled]").count() == 1) == bool(rr.get("running")), (pg2.locator(".so-tabs .acts").inner_text(), rr.get("running")))
        pg2.locator(".so-tab[data-view='layout']").click(); pg2.wait_for_timeout(300)
        ok("U4c coming back to Layout keeps the same iframe (the page is not reloaded)", pg2.evaluate("el => el === document.getElementById('so-frame')", frame_el) and fr.locator(".tslide").count() == 7, "")
        pg2.reload(); pg2.wait_for_selector("#app-social:not([hidden]) .so-tabs", timeout=20000)
        fr = pg2.frame_locator("#so-frame"); fr.locator(".tslide").first.wait_for(timeout=20000)
        ok("U4d a reload remembers the output type (Travel Blog post) and opens the page in the template mode", pg2.locator(".so-seg button.on[data-type='blog']").count() == 1
           and fr.locator("#modeSeg button[data-mode='template'][aria-pressed='true']").count() == 1, pg2.inner_text(".so-seg"))
        frame_el = pg2.evaluate_handle("document.getElementById('so-frame')")

        # wait for the carousel render
        jd = api.wait_job(job["id"], 240)
        files = sorted(f.name for f in (OUT / "armenia").glob("*.png")) if (OUT / "armenia").is_dir() else []
        ok("R2 the render finishes (done) with the 7 slides + caption.txt in the scratch out folder", jd.get("status") == "done" and len(files) == 7 and files[0] == "01-cover.png" and (OUT / "armenia" / "caption.txt").is_file(), (jd.get("status"), jd.get("error"), files, (jd.get("log") or "")[-400:]))
        if files:
            im = Image.open(OUT / "armenia" / files[0])
            ok("R2b a slide is 1080x1350 PNG with the sRGB profile embedded", im.size == (1080, 1350) and im.format == "PNG" and bool(im.info.get("icc_profile")), (im.size, im.format, bool(im.info.get("icc_profile"))))
        st, rd = api.call("/api/social/armenia/renders")
        ok("R3 /renders lists them with the file route and the caption; nothing is running", st == 200 and len(rd["carousel"]) == 7 and rd["carousel"][0]["url"].startswith("/api/social/armenia/file/carousel/") and rd["caption"].strip() and rd["running"] is False, (st, rd.get("carousel", [])[:1]))
        st, raw = api.call(rd["carousel"][1]["url"], raw=True)
        st2, _ = api.call("/api/social/armenia/file/carousel/..%2F..%2Fmanifest.json", raw=True)
        ok("R3b the file route serves a slide (PNG) and refuses a path trick", st == 200 and raw[:4] == b"\x89PNG" and st2 in (400, 404), (st, st2))

        pg2.locator(".so-tab[data-view='slides']").click()
        pg2.wait_for_function("document.querySelectorAll('.so-grid.c .so-thumb').length === 7", timeout=15000)
        thumbs = pg2.locator(".so-grid.c .so-thumb")
        ok("U5 Slides now shows the 7 rendered thumbnails and the caption with a Copy button", thumbs.count() == 7 and pg2.locator("#so-cap-c").count() == 1 and "Comment GUIDE" in pg2.inner_text("#so-cap-c"), thumbs.count())
        pg2.wait_for_function("[...document.querySelectorAll('.so-grid.c img')].every(i => i.complete && i.naturalWidth > 0)", timeout=20000)
        ok("U5b every thumbnail image loaded from the file route", pg2.evaluate("[...document.querySelectorAll('.so-grid.c img')].every(i => i.naturalWidth === 1080)"), "")
        pg2.screenshot(path=str(SHOTS / "social-tab-test-slides.png"))
        thumbs.nth(1).click(); pg2.wait_for_selector("#so-light:not([hidden]) img", timeout=10000)
        pg2.wait_for_function("document.querySelector('#so-light img').naturalWidth > 0", timeout=10000)
        pos = pg2.inner_text("#so-light .pos")
        pg2.keyboard.press("ArrowRight"); pg2.wait_for_timeout(200)
        pos2 = pg2.inner_text("#so-light .pos")
        pg2.keyboard.press("Escape"); pg2.wait_for_timeout(200)
        ok("U6 the lightbox opens on the slide, → steps, Esc closes", pos.startswith("2 / 7") and pos2.startswith("3 / 7") and pg2.locator("#so-light").get_attribute("hidden") is not None, (pos, pos2))
        pg2.locator(".so-tab[data-view='pins']").click(); pg2.wait_for_timeout(300)
        ok("U7 Pins shows its empty state with the Render pins button enabled (the seed lists pins)", pg2.locator(".so-btn[data-act='render'][data-kind='pins']:not([disabled])").count() == 1 and "No pins yet" in pg2.locator(".so-body").inner_text(), pg2.locator(".so-body").inner_text()[:80])

        # ----------------------------------------------------------------------------- STORIES (Travel Blog post, from the tab's button)
        pg2.locator(".so-tab[data-view='stories']").click(); pg2.wait_for_timeout(300)
        ok("S1 Stories (Travel Blog post) shows its empty state with the Render stories button", "No story frames yet" in pg2.locator(".so-body").inner_text()
           and pg2.locator(".so-btn[data-act='render'][data-kind='stories-carousel']:not([disabled])").count() == 1, pg2.locator(".so-tabs .acts").inner_text())
        pg2.locator(".so-btn[data-act='render'][data-kind='stories-carousel']").click()
        pg2.wait_for_selector(".so-job.run", timeout=10000)
        sjob = pg2.evaluate("window.__social.job")
        ok("S1b the button starts a stories-carousel job (the spinner shows in the bar)", sjob and sjob.get("kind") == "stories-carousel", sjob)
        pg2.wait_for_function("document.querySelectorAll('.so-strip .so-thumb').length === 7", timeout=240000)
        sfiles = sorted(f.name for f in (OUT / "armenia" / "stories").glob("*.png"))
        ok("S2 7 story frames land in <out>/armenia/stories (cover, map, 4 stops, cta) and the Stories strip shows them", sfiles == ["01-cover.png", "02-map.png", "03-s0.png", "04-s1.png", "05-s2.png", "06-s3.png", "07-cta.png"], sfiles)
        if sfiles:
            im = Image.open(OUT / "armenia" / "stories" / sfiles[0])
            ok("S2b a story frame is 1080x1920 PNG with the sRGB profile", im.size == (1080, 1920) and bool(im.info.get("icc_profile")), (im.size, bool(im.info.get("icc_profile"))))
            px = Image.open(OUT / "armenia" / "stories" / "03-s0.png").convert("RGB")
            ok("S2c a stop frame: the photo on top, the green band under y = 1340 (the label sits above Instagram's bottom 340 px)", px.getpixel((40, 1900)) == (45, 107, 80) and px.getpixel((40, 1340 - 20)) != (45, 107, 80), (px.getpixel((40, 1900)), px.getpixel((40, 1320))))
        pg2.wait_for_function("[...document.querySelectorAll('.so-strip img')].every(i => i.complete && i.naturalWidth === 1080)", timeout=20000)
        pg2.screenshot(path=str(SHOTS / "social-tab-stories-1440.png"))
        pg2.locator(".so-strip .so-thumb").nth(0).click(); pg2.wait_for_selector("#so-light:not([hidden]) img.story", timeout=10000)
        ttl = pg2.inner_text("#so-light .ttl")
        pg2.wait_for_function("document.querySelector('#so-light img').naturalHeight === 1920", timeout=10000)
        pg2.screenshot(path=str(SHOTS / "social-tab-story-light-1440.png"))
        pg2.keyboard.press("Escape"); pg2.wait_for_timeout(200)
        ok("S3 the lightbox opens a story frame (titled as a 1080 × 1920 story)", "1080 × 1920" in ttl and pg2.locator("#so-light").get_attribute("hidden") is not None, ttl)
        st, rd = api.call("/api/social/armenia/renders")
        st2, lst = api.call("/api/social")
        ok("S4 /renders and /api/social count the story frames", st == 200 and len(rd["stories_carousel"]) == 7 and rd["stories_free"] == []
           and next(x for x in lst["countries"] if x["slug"] == "armenia")["renders"]["stories_carousel"] == 7, (st, len(rd.get("stories_carousel", []))))

        # the Personal post's stories: one final slot with a note, --no-copy on this scratch server
        api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + pid, "data": {"state": "final", "slot": 1, "order": 0, "note": "The lahmajun at Elie's, 600 dram", "crop": None}})
        st, r = api.call("/api/social/armenia/render", {"kind": "stories-free"})
        fj = api.wait_job((r.get("job") or {}).get("id", ""), 180) if st == 200 else {}
        ffiles = sorted(f.name for f in (IG / "armenia" / "stories").glob("*.png")) if (IG / "armenia" / "stories").is_dir() else []
        ok("S5 stories-free renders the one final slot into <ig>/armenia/stories as 01-<id>.png at 1080x1920 (the note is its line)", st == 200 and fj.get("status") == "done" and ffiles == ["01-%s.png" % pid]
           and Image.open(IG / "armenia" / "stories" / ffiles[0]).size == (1080, 1920) if ffiles else False, (st, fj.get("status"), ffiles, (fj.get("log") or "")[-300:]))
        pg2.locator(".so-seg button[data-type='personal']").click(); pg2.wait_for_timeout(500)
        pg2.locator(".so-tab[data-view='stories']").click()
        pg2.wait_for_function("document.querySelectorAll('.so-strip .so-thumb').length === 1", timeout=15000)
        ok("S5b Personal post > Stories shows that frame (its own list, not the Travel Blog's)", pg2.locator(".so-strip .so-thumb").count() == 1 and pid in (pg2.get_attribute(".so-strip .so-thumb", "title") or ""), pg2.get_attribute(".so-strip .so-thumb", "title"))

        # Render (was "Ready to render"): marks the post ready and renders the Final row, then shows it in Post
        pg2.locator(".so-tab[data-view='all']").click()
        pg2.wait_for_function("(document.querySelector('.so-tab[data-view=\"final\"] .n') || {}).textContent === '1/10'", timeout=20000)
        btn = pg2.locator(".so-btn[data-act='rendernow']")
        btn_ok = btn.is_enabled() and btn.inner_text().strip().lower() == "render"
        btn.click()
        pg2.wait_for_function("window.__social.job && window.__social.job.kind === 'free'", timeout=15000)
        fj = api.wait_job(pg2.evaluate("window.__social.job.id"), 240)
        pg2.wait_for_function("document.querySelectorAll('.so-grid.f .so-thumb').length === 1", timeout=30000)
        outf = IG / "armenia" / "out" / ("01-%s.jpg" % pid)
        ok("N10 Render marks the post ready, renders the Final row into the scratch out folder (1080x1350, the tab's own --out) and the tab moves to Post with it",
           btn_ok and fj.get("status") == "done" and outf.is_file() and Image.open(outf).size == (1080, 1350) and rd_post().get("status") == "ready"
           and pg2.locator(".so-tab.on[data-view='renders']").count() == 1, (btn_ok, fj.get("status"), (fj.get("log") or "")[-300:], rd_post().get("status")))
        pg2.locator(".so-tab[data-view='all']").click(); pg2.wait_for_timeout(500)
        pg2.locator(".so-seg.sm button[data-aspect='1.91:1']").click(); pg2.wait_for_timeout(700)
        pg2.locator(".so-btn[data-act='rendernow']").click()
        pg2.wait_for_function("window.__social.job && window.__social.job.kind === 'free' && window.__social.job.id !== '%s'" % fj.get("id"), timeout=15000)
        fj2 = api.wait_job(pg2.evaluate("window.__social.job.id"), 240)
        ok("N10b at 1.91:1 the same slide renders landscape, 1080x565", fj2.get("status") == "done" and outf.is_file() and Image.open(outf).size == (1080, 565), (fj2.get("status"), Image.open(outf).size if outf.is_file() else None))
        pg2.locator(".so-tab[data-view='all']").click(); pg2.wait_for_timeout(400)
        pg2.locator(".so-seg.sm button[data-aspect='4:5']").click(); pg2.wait_for_timeout(500)
        api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + pid, "data": {"state": "final", "slot": 11, "order": 0, "note": "", "crop": None}})

        # phone
        pg2.set_viewport_size({"width": 393, "height": 852}); pg2.wait_for_timeout(400)
        pg2.locator(".so-seg button[data-type='blog']").click(); pg2.wait_for_timeout(300)
        pg2.locator(".so-tab[data-view='slides']").click(); pg2.wait_for_timeout(400)
        over = pg2.evaluate("[document.scrollingElement.scrollWidth, document.querySelector('.so-body').scrollWidth, document.querySelector('.so-body').clientWidth]")
        ok("U8 393 px: no sideways scroll", over[0] <= 393 and over[1] <= over[2], over)
        pg2.screenshot(path=str(SHOTS / "social-tab-blog-393.png"))
        pg2.locator(".so-tab[data-view='layout']").click(); pg2.wait_for_timeout(300)
        ok("U8b the maker iframe survived the Slides, Stories and Pins views (same element, still on the plan)", pg2.evaluate("el => el === document.getElementById('so-frame')", frame_el) and fr.locator(".tslide").count() == 7, "")
        pg2.locator(".so-seg button[data-type='personal']").click(); pg2.wait_for_timeout(700)
        over = pg2.evaluate("[document.scrollingElement.scrollWidth, document.querySelector('.so-body').scrollWidth, document.querySelector('.so-body').clientWidth]")
        ok("U8c 393 px, Personal post on the Photos view: no sideways scroll either", over[0] <= 393 and over[1] <= over[2], over)
        pg2.screenshot(path=str(SHOTS / "social-tab-personal-393.png"))
        pg2.locator(".so-tab[data-view='final']").click(); pg2.wait_for_timeout(800)
        over = pg2.evaluate("[document.scrollingElement.scrollWidth, document.querySelector('.so-body').scrollWidth, document.querySelector('.so-body').clientWidth]")
        fo = fr.locator("html").evaluate("e => [e.scrollWidth, e.clientWidth]")
        tops = fr.locator(".rowwrap.final .slots").evaluate("g => [...g.querySelectorAll('.frame')].slice(0, 4).map(c => Math.round(c.getBoundingClientRect().top))")
        ok("U8d 393 px, the Shortlist: three slots to a row on a phone, no sideways scroll in the tab or the maker", over[0] <= 393 and over[1] <= over[2] and fo[0] <= fo[1]
           and len(set(tops[:3])) == 1 and tops[3] > tops[0], (over, fo, tops))
        pg2.screenshot(path=str(SHOTS / "social-redesign-test-shortlist-393.png"))
        pg2.set_viewport_size({"width": 1440, "height": 900})
        ok("U9 no page errors in the editor", not errs2, errs2[:3])

        pg3 = ctx.new_page()
        pg3.route(f"http://127.0.0.1:{PORT}/api/social", lambda r: r.fulfill(status=404, body="not found"))
        pg3.goto(EDITOR + "#social"); pg3.wait_for_selector("#social-root .so-msg", timeout=20000)
        msg = pg3.inner_text("#social-root .so-msg")
        ok("U10 an old photo server (404) shows the restart message", "restart" in msg.lower() and "/api/social" in msg, msg[:120])
        pg3.close()
        b.close()
finally:
    srv.kill()

ok("Z1 the real store, the real renders (the Personal post's .tmp/ig/armenia/out too) and Pictures\\IG were not touched", snapshot(REAL / "state") == real_state0
   and snapshot(REAL_OUT) == real_out0 and snapshot(PICS) == pics0 and snapshot(REAL / "out") == real_free0, "")
shutil.rmtree(SCRATCH, ignore_errors=True)
n = sum(1 for _, c, _ in checks if c)
print(f"\n{n}/{len(checks)} passed; screenshots in {SHOTS}\\social-tab-*.png")
sys.exit(0 if n == len(checks) else 1)
