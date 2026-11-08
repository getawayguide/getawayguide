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

PORT = free_port()
env = dict(os.environ, SOCIAL_IG_DIR=str(IG), SOCIAL_OUT_DIR=str(OUT), PYTHONIOENCODING="utf-8")
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
        pg.goto(f"http://127.0.0.1:{PORT}/social/armenia/page")
        pg.wait_for_selector(".card[data-id]", timeout=20000); pg.wait_for_timeout(500)
        n_cards = pg.locator(".card[data-id]").count()
        ok("P1 the page served with the shim loads every photo of the local catalog, titled for the country, with Claude's own runtime absent", n_cards == len(man["photos"]) and pg.inner_text("#title").startswith("Armenia")
           and pg.evaluate("window.claude && window.claude.local === true") and pg.evaluate("window.__socialDb.loaded()"), (n_cards, errors[:2]))
        if web_ids:
            src = pg.get_attribute(f".card[data-id='{web_ids[0]}'] img", "src")
            ok("P1b a card's image is the blob route of this server", src and f"/api/social/armenia/blob/{web_ids[0]}" in src, src)
        ok("P1c standalone (the artifacts' look): the page keeps its own header, the shortcut hint line is gone and a ? button holds the keys", pg.locator("header.top").is_visible()
           and pg.locator("#hint").count() == 0 and pg.locator("#helpBtn").count() == 1 and "Shift+1" in (pg.get_attribute("#helpBtn", "title") or "")
           and not pg.evaluate("document.documentElement.classList.contains('embed')"), pg.get_attribute("#helpBtn", "title"))
        pf = IG / "armenia" / "state" / "photos" / (pid + ".json")
        pg.locator(f".card[data-id='{pid}']").click(); pg.keyboard.press("s"); pg.wait_for_timeout(700)
        ok("P2 the S key on the photo the API shortlisted takes it off the shortlist through the shim: the page deletes photos/<id>.json", not pf.is_file(), pf.is_file())
        pg.keyboard.press("s"); pg.wait_for_timeout(700)
        ok("P2b ...and again shortlists it: the file is back with state shortlist", pf.is_file() and json.loads(pf.read_text(encoding="utf-8")).get("state") == "shortlist", pf.is_file())
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
        seg = pg2.locator(".so-seg button")
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
        ok("U3c Personal post's sub-tabs: Photos, Shortlist, Slots, Archived, Caption | Post, Stories; the article filter, ? and Ready in the bar; no shortcut hint line anywhere",
           labels == ["all", "shortlist", "final", "archived", "caption", "renders", "stories"] and pg2.locator(".so-sel[data-act='article']").count() == 1
           and "Shift+1" in (pg2.get_attribute(".so-ib[data-act='help']", "title") or "") and pg2.locator(".so-btn[data-act='ready']").count() == 1
           and "backlog 11" not in pg2.inner_text("#app-social") and fr.locator("#hint").count() == 0, (labels, pg2.inner_text(".so-tabs .acts")))
        frame_el = pg2.evaluate_handle("document.getElementById('so-frame')")
        pg2.locator(".so-tab[data-view='shortlist']").click(); pg2.wait_for_timeout(600)
        ok("U3d the Shortlist sub-tab drives the page's view over postMessage (the shortlisted photo is there, the iframe stays)", fr.locator("#shortGrid .card[data-id]").count() == 1
           and pg2.locator(".so-tab.on[data-view='shortlist']").count() == 1 and pg2.evaluate("el => el === document.getElementById('so-frame')", frame_el), fr.locator("#shortGrid .card[data-id]").count())
        pg2.locator(".so-sel[data-act='article']").select_option(index=1); pg2.wait_for_timeout(500)
        picked = pg2.evaluate("document.querySelector('.so-sel[data-act=\"article\"]').value")
        pg2.locator(".so-tab[data-view='all']").click(); pg2.wait_for_timeout(600)
        n_f = fr.locator(".card[data-id]").count()
        ok("U3e the article filter in the tab's bar filters the page", picked and 0 < n_f < len(man["photos"]) and n_f == sum(1 for x in man["photos"] if x["article"] == picked), (picked, n_f))
        pg2.locator(".so-sel[data-act='article']").select_option(value=""); pg2.wait_for_timeout(400)
        pg2.locator(".so-btn[data-act='ready']").click(); pg2.wait_for_timeout(700)
        post_f = IG / "armenia" / "state" / "post" / "main.json"
        ok("U3f Ready in the tab's bar marks the Personal post ready in the store, the pill turns, the page jumps to Caption and the sub-tab follows",
           post_f.is_file() and json.loads(post_f.read_text(encoding="utf-8")).get("status") == "ready" and pg2.inner_text(".so-pill").strip().lower() == "ready"
           and pg2.locator(".so-tab.on[data-view='caption']").count() == 1 and "back to draft" in pg2.inner_text(".so-btn[data-act='ready']").lower(), (pg2.inner_text(".so-tabs .acts"), post_f.is_file()))
        pg2.locator(".so-btn[data-act='ready']").click(); pg2.wait_for_timeout(600)
        pg2.locator(".so-tab[data-view='all']").click(); pg2.wait_for_timeout(300)
        pg2.screenshot(path=str(SHOTS / "social-tab-personal-1440.png"))

        # Travel Blog post
        pg2.locator(".so-seg button[data-type='blog']").click(); pg2.wait_for_timeout(900)
        labels = pg2.evaluate("[...document.querySelectorAll('.so-tab')].map(b => b.dataset.view)")
        ok("U4 Travel Blog post: the page switches to the template mode on the plan (same iframe), sub-tabs Layout | Slides, Stories, Pins, the Layout count = the slides",
           labels == ["layout", "slides", "stories", "pins"] and fr.locator("#modeSeg button[data-mode='template'][aria-pressed='true']").count() == 1
           and fr.locator(".tslide").count() == 7 and pg2.inner_text(".so-tab[data-view='layout'] .n") == "7"
           and pg2.evaluate("el => el === document.getElementById('so-frame')", frame_el), (labels, fr.locator(".tslide").count()))
        pg2.screenshot(path=str(SHOTS / "social-tab-blog-1440.png"))
        pg2.locator(".so-tab[data-view='slides']").click(); pg2.wait_for_timeout(400)
        ok("U4b Slides shows its group with the Render carousel button, waiting for the running render", pg2.locator(".so-group").count() == 1
           and pg2.locator(".so-btn[data-act='render'][data-kind='carousel'][disabled]").count() == 1, pg2.locator(".so-tabs .acts").inner_text())
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
        api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + pid, "data": {"state": "shortlist", "slot": None, "order": 1, "note": "", "crop": None}})

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

ok("Z1 the real store, the real renders and Pictures\\IG were not touched", snapshot(REAL / "state") == real_state0 and snapshot(REAL_OUT) == real_out0 and snapshot(PICS) == pics0, "")
shutil.rmtree(SCRATCH, ignore_errors=True)
n = sum(1 for _, c, _ in checks if c)
print(f"\n{n}/{len(checks)} passed; screenshots in {SHOTS}\\social-tab-*.png")
sys.exit(0 if n == len(checks) else 1)
