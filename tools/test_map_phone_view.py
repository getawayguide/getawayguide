"""The map editor's Phone view for every map, and its toolbar at a phone's width (stress test 2026-11-06 leftovers
t5e/t6, finished 2026-11-07).

Runs its OWN map_editor.py on MAP_EDITOR_PORT (default 5022) beside the suite's :5002, against scratch copies of a
route map (egypt, which has no "mobile" block: slug zz-scratch-phone) and a city map (riga, with its published
rasters copied under slug zz-scratch-city and "labels": true so pin names draw) whose pages are scratch files under
.tmp/map-phone-test/. Everything it makes is deleted at the end; no real page, config, raster or running server is
touched.

  P1  a route map without a phone build shows the Phone button; the Phone view draws the map at the width the page's
      phone column gives the figure (min(max-width, 345)), with the caption, and the switch is not an edit
  P2  a label dragged in the Phone view lands in cfg.mobile.stops["<i>"] (the block is made on that first move), the
      desktop entry is untouched, the redrawn label sits where it was dropped; back on Desktop the desktop svg shows
  P3  Save writes the phone block, builds <slug>-mob-map.svg and embeds BOTH svgs in the page: at 393 px the page shows
      the phone svg at 345 px, at 1440 px the desktop one, unchanged from before the phone move
  P4  pruneMobile drops a phone block with nothing in it (a reset that changed nothing never makes a second build)
  P5  2x magnifies the phone canvas and 1x puts it back
  P6  a city map's Phone view shows its portrait window at the phone column's width with the page script's opening
      transform (data-iw/ix/iy) and the title overlay
  P7  a district label dragged there goes to district_labels[].mobile (desktop lat/lon untouched); a place name clicked
      there cycles pois[].lab_mobile and the desktop side stays
  P8  city Save writes those, re-embeds the scratch page (phone variant drawn at 393 px) and leaves the rasters as they were
  P9  at 393 px (and the article editor's 369 px modal) Done, Save and Phone are on screen, the canvas fits, nothing
      scrolls sideways; standalone, the map list lies over the stage
  P10 no page errors
  1x screenshots: .tmp/previews/map-phone-test-*.png

    python tools/test_map_phone_view.py
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "tools"))

PORT = int(os.environ.get("MAP_EDITOR_PORT", "5022"))
API = f"http://127.0.0.1:{PORT}"
RSLUG, CSLUG = "zz-scratch-phone", "zz-scratch-city"
RSRC = ROOT / "tools" / "itinerary_maps" / "egypt.json"
CSRC = ROOT / "tools" / "city_maps" / "riga.json"
RCFG = ROOT / "tools" / "itinerary_maps" / f"{RSLUG}.json"
CCFG = ROOT / "tools" / "city_maps" / f"{CSLUG}.json"
IMG = ROOT / "Images" / "web" / "city-maps"
SCRATCH = ROOT / ".tmp" / "map-phone-test"
RPAGE = f".tmp/map-phone-test/{RSLUG}.html"
CPAGE = f".tmp/map-phone-test/{CSLUG}.html"
PREV = ROOT / ".tmp" / "previews"
PREV_PNG = PREV / "city-png"
SHOTS = {k: PREV / f"map-phone-test-{k}.png" for k in ("route-editor", "route-page-393", "city-editor", "city-page-393", "bar-393", "bar-369", "bar-393-standalone")}
NOWIN = 0x08000000 if sys.platform == "win32" else 0
PHONE_COL = 345

checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:500]), flush=True)


def setup():
    for s in (RSLUG, CSLUG):
        assert "zz-" in s                                      # scratch slugs, never a real map's
    SCRATCH.mkdir(parents=True, exist_ok=True)
    rcfg = json.loads(RSRC.read_text(encoding="utf-8"))
    rcfg["slug"] = RSLUG; rcfg["page"] = RPAGE; rcfg.pop("pages", None); rcfg.pop("mobile", None)
    RCFG.write_text(json.dumps(rcfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # the page: an article column padded 24 px a side like .article-body under 769 px, so the figure is 345 px at 393
    (ROOT / RPAGE).write_text(
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>scratch</title>\n'
        '<link href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600;700&amp;display=swap" rel="stylesheet">\n'
        '<style>body{margin:0}main{padding:0 24px;max-width:825px;margin:0 auto}</style>\n'
        '</head>\n<body><main><div class="article-body">\n'
        '<div class="fn-section" id="general">\n  <p>Intro paragraph.</p>\n  <ul><li>Getting around</li></ul>\n</div>\n'
        '</div></main></body></html>\n', encoding="utf-8")
    ccfg = json.loads(CSRC.read_text(encoding="utf-8"))
    ccfg["slug"] = CSLUG; ccfg["article"] = CPAGE; ccfg["osm"] = f".tmp/{CSLUG}_osm.json"; ccfg["labels"] = True
    ccfg["embed"] = {"pre": f"<!-- city map: {CSLUG} -->\n", "post": f"<!-- /city map: {CSLUG} -->"}
    CCFG.write_text(json.dumps(ccfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for ext in (".png", "-mobile.png", "-1x.webp"):                # the published rasters, under the scratch name
        shutil.copyfile(IMG / ("riga" + ext), IMG / (CSLUG + ext))
    (ROOT / CPAGE).write_text(
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>scratch city</title>\n'
        '<style>body{margin:0}main{padding:0 24px;max-width:825px;margin:0 auto}</style>\n'
        '</head>\n<body><main><div class="article-body">\n<div class="fn-section" id="riga">\n  <p>Riga prose.</p>\n'
        f'{ccfg["embed"]["pre"]}{ccfg["embed"]["post"]}\n</div>\n</div></main></body></html>\n', encoding="utf-8")
    return rcfg, ccfg


def cleanup(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    files = [RCFG, CCFG] + list(PREV.glob(f"{RSLUG}*")) + list(PREV.glob(f"{CSLUG}*")) + list(PREV.glob(f"_edit-{CSLUG}*"))
    files += list(IMG.glob(f"{CSLUG}*")) + (list(PREV_PNG.glob(f"{CSLUG}*")) if PREV_PNG.is_dir() else [])
    for p in files:
        try:
            p.unlink()
        except OSError:
            pass
    shutil.rmtree(SCRATCH, ignore_errors=True)


def start_server(log):
    env = dict(os.environ, MAP_EDITOR_PORT=str(PORT), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "tools" / "map_editor.py")], cwd=str(ROOT), env=env,
                            stdout=log, stderr=subprocess.STDOUT, creationflags=NOWIN)
    for _ in range(120):
        if proc.poll() is not None:
            raise SystemExit("the test map editor exited at startup; see " + log.name)
        try:
            urllib.request.urlopen(API + "/api/maps", timeout=2).read()
            return proc
        except Exception:
            time.sleep(0.5)
    raise SystemExit("the test map editor did not answer on :%d" % PORT)


def main():
    from playwright.sync_api import sync_playwright
    import itinerary_map as im
    rcfg0, ccfg0 = setup()
    svg0, vw0, vh0 = im.render(rcfg0)                    # the desktop build before any move
    base_bytes = {ext: (IMG / (CSLUG + ext)).read_bytes() for ext in (".png", "-mobile.png")}
    PREV.mkdir(parents=True, exist_ok=True)
    log = open(SCRATCH / "server.log", "w", encoding="utf-8")
    proc = None
    try:
        proc = start_server(log)
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
            pg.on("dialog", lambda d: d.accept())

            def status():
                return pg.evaluate("document.getElementById('status').textContent")

            def settled(want=None, t=60000):
                pg.wait_for_function("() => !/rendering…|saving…|reverting…/.test(document.getElementById('status').textContent)", timeout=t)
                if want is not None:
                    pg.wait_for_function("w => document.getElementById('status').textContent.startsWith(w)", arg=want, timeout=t)
                pg.wait_for_timeout(200)

            def box(sel):
                return pg.locator(sel).first.bounding_box()

            def center(sel):
                bb = box(sel)
                return (bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)

            def drag(sel, dx, dy, want="unsaved"):
                x, y = center(sel)
                pg.mouse.move(x, y); pg.mouse.down()
                for k in range(1, 5):
                    pg.mouse.move(x + dx * k / 4, y + dy * k / 4)
                pg.mouse.up()
                settled(want)
                return (x, y)

            def near(a, b, tol=4):
                return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

            # ------------------------------------------------------------------ route map (no mobile block)
            pg.goto(f"{API}/?slug={RSLUG}")
            pg.wait_for_selector('#canvas svg .eh[data-kind="stop"]', timeout=60000)
            pg.wait_for_timeout(300)
            ok("P1 a route map without a phone build shows the Phone button", pg.evaluate("!document.getElementById('phone').hidden"))
            ok("P1 the magnifier waits for the Phone view", pg.evaluate("document.getElementById('mag').hidden"))
            embed_w = pg.evaluate("embedW")
            ok("P1 the server tells the figure's page width (embed_itinerary_map's: egypt 451)", embed_w == 451, embed_w)
            desk_w = box("#canvas svg")["width"]
            pg.click("#phone"); settled()
            cw = box("#canvas")["width"]; sw = box("#canvas svg")["width"]
            ok("P1 the Phone view draws the map at the phone column's width (min(451, 345) = 345)",
               pg.evaluate("document.getElementById('canvas').classList.contains('phone')") and abs(cw - PHONE_COL) < 1 and abs(sw - PHONE_COL) < 1 and desk_w > 600, (cw, sw, desk_w))
            cap = pg.evaluate("(document.getElementById('pcap')||{}).textContent||''")
            ok("P1 the caption names the phone and that moves change the phone only", "393" in cap and "phone only" in cap, cap)
            ok("P1 switching views is not an edit", status() == "" and pg.evaluate("saveBtn.disabled") and pg.evaluate("cfg.mobile === undefined"), status())
            hint = pg.evaluate("document.getElementById('hint').textContent")
            ok("P1 the hint says so too", hint.startswith("phone build"), hint)

            # --- P2 a phone move
            S1 = '#canvas svg .eh[data-kind="stop"][data-idx="1"]'
            c0 = center(S1); cc = center("#canvas")
            dx, dy = (-18 if c0[0] > cc[0] else 18), (-12 if c0[1] > cc[1] else 12)      # toward the middle, so the crop does not grow
            x0 = drag(S1, dx, dy)
            m1 = pg.evaluate("cfg.mobile && cfg.mobile.stops && cfg.mobile.stops['1']")
            ok("P2 a drag in the Phone view makes the phone block and patches cfg.mobile.stops['1']", m1 and "dx" in m1 and "dy" in m1, pg.evaluate("JSON.stringify(cfg.mobile)"))
            ok("P2 the desktop entry is untouched", json.loads(pg.evaluate("JSON.stringify(cfg.stops[1])")) == rcfg0["stops"][1], pg.evaluate("JSON.stringify(cfg.stops[1])"))
            ok("P2 the redrawn label sits where it was dropped", near(center(S1), (x0[0] + dx, x0[1] + dy), 5), (center(S1), x0, dx, dy))
            ok("P2 the canvas stays at the phone width through the redraw", abs(box("#canvas")["width"] - PHONE_COL) < 1, box("#canvas")["width"])
            pg.click("#phone"); settled()
            ok("P2 back on Desktop the desktop svg shows (wide canvas, label at its desktop offsets)",
               not pg.evaluate("document.getElementById('canvas').classList.contains('phone')") and box("#canvas svg")["width"] > 600
               and pg.evaluate("+document.querySelector('%s').dataset.dx" % S1) == rcfg0["stops"][1].get("dx", 0), (box("#canvas svg")["width"], pg.evaluate("document.querySelector('%s').dataset.dx" % S1)))
            ok("P2 and the status still says unsaved", status() == "unsaved changes", status())
            pg.click("#phone"); settled()
            ok("P2 the Phone view again draws the phone build (the handle carries the phone offset)", pg.evaluate("+document.querySelector('%s').dataset.dx" % S1) == m1["dx"], (pg.evaluate("document.querySelector('%s').dataset.dx" % S1), m1))

            # --- P5 magnifier (before the save, while the phone view is up)
            pg.click("#mag"); pg.wait_for_timeout(200)
            ok("P5 2x doubles the phone canvas", abs(box("#canvas")["width"] - 2 * PHONE_COL) < 1 and pg.evaluate("document.getElementById('mag').textContent") == "1×", box("#canvas")["width"])
            pg.click("#mag"); pg.wait_for_timeout(200)
            ok("P5 1x puts it back", abs(box("#canvas")["width"] - PHONE_COL) < 1, box("#canvas")["width"])
            pg.locator("#wrap").screenshot(path=str(SHOTS["route-editor"]))

            # --- P3 save
            pg.click("#save"); settled("saved", 180000)
            ok("P3 Save reports the scratch page", status().startswith("saved → " + RPAGE), status())
            disk = json.loads(RCFG.read_text(encoding="utf-8"))
            ok("P3 the JSON on disk carries the phone block and nothing else of the phone", disk.get("mobile") == {"stops": {"1": m1}} and disk["stops"][1] == rcfg0["stops"][1] and disk.get("page") == RPAGE, disk.get("mobile"))
            msvg_p = PREV / f"{RSLUG}-mob-map.svg"
            ok("P3 the phone svg is built beside the desktop one", msvg_p.is_file() and (PREV / f"{RSLUG}-map.svg").is_file())
            ok("P3 the desktop svg is the one from before the phone move", (PREV / f"{RSLUG}-map.svg").read_text(encoding="utf-8") == svg0)
            ok("P3 the phone svg differs from it (the moved label)", msvg_p.is_file() and msvg_p.read_text(encoding="utf-8") != svg0)
            html = (ROOT / RPAGE).read_text(encoding="utf-8")
            ok("P3 the page carries both builds in the one figure with the 700 px swap", 'class="imap-desk"' in html and 'class="imap-mob"' in html and "@media (max-width:700px)" in html and f'data-map="{RSLUG}"' in html)
            pg2 = ctx.new_page(); pg2.goto((ROOT / RPAGE).resolve().as_uri()); pg2.wait_for_timeout(500)
            d_vis = pg2.evaluate("[getComputedStyle(document.querySelector('.imap-desk')).display, getComputedStyle(document.querySelector('.imap-mob')).display, document.querySelector('.imap-desk').getBoundingClientRect().width]")
            pg2.close()
            ctxm = b.new_context(viewport={"width": 393, "height": 800}, device_scale_factor=1)
            pm = ctxm.new_page(); pm.goto((ROOT / RPAGE).resolve().as_uri()); pm.wait_for_timeout(500)
            m_vis = pm.evaluate("[getComputedStyle(document.querySelector('.imap-desk')).display, getComputedStyle(document.querySelector('.imap-mob')).display, document.querySelector('.imap-mob').getBoundingClientRect().width]")
            pm.locator("figure.itinerary-map").screenshot(path=str(SHOTS["route-page-393"]))
            ok("P3 at 1440 the page shows the desktop svg at its max-width", d_vis[0] == "block" and d_vis[1] == "none" and abs(d_vis[2] - 451) < 1, d_vis)
            ok("P3 at 393 the page shows the phone svg at the width the editor showed", m_vis[0] == "none" and m_vis[1] == "block" and abs(m_vis[2] - PHONE_COL) < 1, m_vis)
            pm.close(); ctxm.close()

            # --- P4 prune
            pr = pg.evaluate("""() => { const keep = cfg.mobile; const out = [];
              cfg.mobile = {stops: {'0': {}}, legend: {}}; pruneMobile(); out.push(cfg.mobile === undefined);
              cfg.mobile = {stops: {'0': {}, '2': {dx: 3}}, focus_span: 400}; pruneMobile(); out.push(JSON.stringify(cfg.mobile));
              cfg.mobile = keep; return out; }""")
            ok("P4 pruneMobile drops an empty phone block", pr[0] is True, pr)
            ok("P4 and keeps real patches and scalar keys", pr[1] == '{"stops":{"2":{"dx":3}},"focus_span":400}', pr)

            # ------------------------------------------------------------------ city map
            pg.goto(f"{API}/?city={CSLUG}")
            pg.wait_for_selector("#canvas.city svg", timeout=180000)
            pg.wait_for_timeout(400)
            ok("P6 a city map shows the Phone button", pg.evaluate("!document.getElementById('phone').hidden"))
            ok("P6 the desktop view is the landscape base with its pin overlay (no phone window)",
               pg.locator("#canvas img.cmbase").count() == 1 and pg.locator("#canvas .citymap.mob").count() == 0 and pg.evaluate("document.querySelector('#canvas svg').viewBox.baseVal.width") == 760)
            pg.click("#phone"); pg.wait_for_selector("#canvas.city.phone .citymap.mob", timeout=10000); pg.wait_for_timeout(400)
            mm = box("#canvas .citymap.mob .cmmap")
            ok("P6 the Phone view is the portrait window at the phone column's width (345 x 479)", abs(mm["width"] - PHONE_COL) < 1 and abs(mm["height"] - PHONE_COL / 0.72) < 2, mm)
            tf = pg.evaluate("document.querySelector('#canvas .cmworld').style.transform")
            W, H, vw, vh = 660, 820, mm["width"], mm["height"]
            iw, ix, iy = ccfg0["mobile"].get("init_w", 0.8), ccfg0["mobile"].get("init_x", 0.5), ccfg0["mobile"].get("init_y", 0.5)
            minS = min(vw / W, vh / H); s = max(minS, min(minS * 7, vw / (iw * W)))
            tx, ty = -max(0, W - vw / s) * ix * s, -max(0, H - vh / s) * iy * s
            got = [float(v) for v in re.findall(r"-?[\d.]+", tf)]
            ok("P6 the world opens where the page script opens it (data-iw/ix/iy)", len(got) == 3 and abs(got[0] - tx) < 0.6 and abs(got[1] - ty) < 0.6 and abs(got[2] - s) < 0.001, (tf, tx, ty, s))
            ok("P6 the title overlay is there", pg.evaluate("document.querySelector('#canvas .cm-ov-title').textContent") == "Riga")
            ok("P6 switching views is not an edit", status() == "" and pg.evaluate("saveBtn.disabled"), status())

            # --- P7 phone-only edits
            DL = "#canvas .citymap.mob text.cmdl"
            ok("P7 the district label is on the phone base", pg.locator(DL).count() == 1)
            x0 = drag(DL, 30, 22)
            dl = pg.evaluate("cfg.district_labels[0]")
            ok("P7 a district label dragged in the Phone view gets its phone spot (district_labels[].mobile)", isinstance(dl.get("mobile"), dict) and "lat" in dl["mobile"] and "lon" in dl["mobile"], dl)
            ok("P7 the desktop lat/lon are untouched", dl["lat"] == ccfg0["district_label"]["lat"] and dl["lon"] == ccfg0["district_label"]["lon"], dl)
            ok("P7 the redrawn label moved the way it was dragged", near(center(DL), (x0[0] + 30, x0[1] + 22), 14), (center(DL), x0))
            LB = "#canvas .citymap.mob text.cmlab"
            name = pg.evaluate("document.querySelector('%s').closest('.cmpin').dataset.name" % LB)
            side0 = pg.evaluate("document.querySelector('%s').dataset.side" % LB)
            pg.locator(LB).first.click(); settled("unsaved")
            poi = pg.evaluate("cfg.pois.find(p => p.name === %s)" % json.dumps(name))
            side1 = pg.evaluate("[...document.querySelectorAll('%s')].find(t => t.closest('.cmpin').dataset.name === %s).dataset.side" % (LB, json.dumps(name)))
            ok("P7 a place name clicked in the Phone view cycles pois[].lab_mobile", poi.get("lab_mobile") in ("n", "e", "s", "w") and poi.get("lab_mobile") != side0 and "lab" not in poi, poi)
            ok("P7 and the phone base draws the name on that side", side1 == poi.get("lab_mobile"), (side0, side1, poi))
            pg.click("#phone"); pg.wait_for_selector("#canvas:not(.phone) img.cmbase", timeout=10000); pg.wait_for_timeout(300)
            dside = pg.evaluate("([...document.querySelectorAll('#canvas:not(.phone) text.cmlab')].find(t => t.closest('.cmpin').dataset.name === %s)||{dataset:{}}).dataset.side" % json.dumps(name))
            dpos = pg.evaluate("[+document.querySelector('#canvas:not(.phone) text.cmdl').getAttribute('x'), +document.querySelector('#canvas:not(.phone) text.cmdl').getAttribute('y')]")
            ok("P7 on Desktop the name keeps its desktop side", dside == side0, (dside, side0))
            pg.click("#phone"); pg.wait_for_selector("#canvas.city.phone", timeout=10000); pg.wait_for_timeout(300)
            pg.locator("#wrap").screenshot(path=str(SHOTS["city-editor"]))

            # --- P8 city save
            pg.click("#save"); settled("saved", 300000)
            ok("P8 Save reports the scratch page", status().startswith("saved → " + CPAGE), status())
            cdisk = json.loads(CCFG.read_text(encoding="utf-8"))
            ok("P8 the JSON on disk carries the phone spot and the phone side", cdisk["district_labels"][0].get("mobile") == dl["mobile"] and next(p for p in cdisk["pois"] if p["name"] == name).get("lab_mobile") == poi["lab_mobile"], cdisk["district_labels"])
            chtml = (ROOT / CPAGE).read_text(encoding="utf-8")
            ok("P8 the page got the map between its markers", f"city-maps/{CSLUG}.png" in chtml and '<figure class="citymap-fig">' in chtml and "Riga prose." in chtml)
            ok("P8 the rasters are as they were (keep_base)", all((IMG / (CSLUG + ext)).read_bytes() == v for ext, v in base_bytes.items()))
            ok("P8 the saved phone overlay draws the name on its phone side",
               re.search(r'data-side="%s"[^>]*>%s<' % (poi["lab_mobile"], re.escape(name)), chtml.split('<div class="citymap mob"', 1)[1]) is not None)
            ctxm = b.new_context(viewport={"width": 393, "height": 800}, device_scale_factor=1)
            pm = ctxm.new_page(); pm.goto((ROOT / CPAGE).resolve().as_uri()); pm.wait_for_timeout(900)
            pm.locator(".citymap.mob").screenshot(path=str(SHOTS["city-page-393"]))
            vis = pm.evaluate("[getComputedStyle(document.querySelector('.citymap.mob')).display, document.querySelector('.citymap.mob .cmmap').getBoundingClientRect().width, document.querySelector('.cmworld').style.transform]")
            ok("P8 at 393 the page shows the phone variant at the editor's width and opening view", vis[0] == "block" and abs(vis[1] - PHONE_COL) < 1 and vis[2] and near([float(v) for v in re.findall(r"-?[\d.]+", vis[2])][:2], got[:2], 0.6), (vis, got))
            pm.close(); ctxm.close()

            # ------------------------------------------------------------------ P9 the bar at a phone's width
            def bar_check(width, embed, shot):
                c = b.new_context(viewport={"width": width, "height": 800}, device_scale_factor=1)
                q = c.new_page(); e2 = []; q.on("pageerror", lambda e: e2.append(str(e)[:300]))
                q.goto(f"{API}/?{'embed=1&' if embed else ''}slug={RSLUG}")
                q.wait_for_selector('#canvas svg .eh[data-kind="stop"]', timeout=60000); q.wait_for_timeout(400)
                r = q.evaluate("""() => { const vis = id => { const el = document.getElementById(id); const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
                    return {id, x: r.x, right: r.right, bottom: r.bottom, shown: cs.display !== 'none' && r.width > 0}; };
                  const cv = document.getElementById('canvas').getBoundingClientRect();
                  return {btns: ['save','done','phone','reset'].map(vis), canvas: [cv.x, cv.right], scrollW: document.documentElement.scrollWidth, mainW: document.getElementById('main').scrollWidth,
                    side: getComputedStyle(document.getElementById('side')).position, hint: getComputedStyle(document.getElementById('hint')).display}; }""")
                q.screenshot(path=str(shot)); c.close()
                return r, e2
            r, e2 = bar_check(393, True, SHOTS["bar-393"])
            on = {x["id"]: x for x in r["btns"]}
            ok("P9 at 393 px (embed) Done, Save and Phone are shown and inside the window", all(on[k]["shown"] and on[k]["x"] >= 0 and on[k]["right"] <= 393 for k in ("done", "save", "phone")), r)
            ok("P9 the canvas fits the window and nothing scrolls sideways", r["canvas"][0] >= 0 and r["canvas"][1] <= 393 and r["scrollW"] <= 393 and r["mainW"] <= 393, r)
            ok("P9 the long hint is gone at that width", r["hint"] == "none", r["hint"])
            r2, e3 = bar_check(369, True, SHOTS["bar-369"])
            on2 = {x["id"]: x for x in r2["btns"]}
            ok("P9 at 369 px (the article editor's modal at 393) Done and Save are still inside", all(on2[k]["shown"] and on2[k]["right"] <= 369 for k in ("done", "save")) and r2["scrollW"] <= 369, r2)
            r3, e4 = bar_check(393, False, SHOTS["bar-393-standalone"])
            on3 = {x["id"]: x for x in r3["btns"]}
            ok("P9 standalone at 393 px the map list lies over the stage and Save is on screen", r3["side"] == "absolute" and on3["save"]["shown"] and on3["save"]["right"] <= 393 and r3["scrollW"] <= 393, r3)
            errs += e2 + e3 + e4

            ok("P10 no page errors", not errs, errs[:3])
            ok("1x screenshots written", all(s.is_file() for s in SHOTS.values()), [str(s) for s in SHOTS.values() if not s.is_file()])
            b.close()
    finally:
        log.close()                                    # before the rmtree: Windows keeps an open file's folder
        cleanup(proc)
    bad = sum(1 for _, c, _ in checks if not c)
    print("%d checks, %d failed" % (len(checks), bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
