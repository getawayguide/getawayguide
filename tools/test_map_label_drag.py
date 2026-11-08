"""Dragging a route map's country names, water names and legend in the map editor (Kevin, 2026-11-07, on the
Skopje bus map: "fix the map editor so I can drag these myself").

Runs its OWN map_editor.py on MAP_EDITOR_PORT (default 5022) beside the suite's :5002, against a scratch copy of
ohrid-boat (slug zz-scratch-drag: it has country labels, a water label, a legend and a "mobile" build) whose page
is a scratch file under .tmp/map-drag-test/. Everything it makes is deleted at the end; no real page, config
or running server is touched.

  D1  a country name, a water name and the legend drag with the mouse; the preview redraws them where they were
      dropped and the config carries the move as dx/dy (the legend in window units)
  D2  a touch drag (one finger, CDP touch events) moves a label too
  D3  the Phone view: a move there lands in cfg.mobile (the phone build), the desktop entry is untouched
  D4  Save writes the overrides to the JSON, the rebuilt desktop + phone SVGs show the labels moved, and the figure
      is embedded in the scratch page; a 1x screenshot of the editor and of the page is left in .tmp/previews/
  D5  double-click puts a country name and the legend back (the override goes, the label returns to its spot)
  D6  a map without any of these (egypt) still opens
  D7  no page errors

    python tools/test_map_label_drag.py
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
SLUG = "zz-scratch-drag"
SRC = ROOT / "tools" / "itinerary_maps" / "ohrid-boat.json"
CFG = ROOT / "tools" / "itinerary_maps" / f"{SLUG}.json"
SCRATCH = ROOT / ".tmp" / "map-drag-test"
PAGE_REL = f".tmp/map-drag-test/{SLUG}.html"
PREV = ROOT / ".tmp" / "previews"
SHOT_EDITOR = PREV / "map-drag-test-editor.png"
SHOT_PAGE = PREV / "map-drag-test-page.png"
NOWIN = 0x08000000 if sys.platform == "win32" else 0

checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:500]), flush=True)


def setup():
    for s in (SLUG,):
        assert "zz-" in s                                     # the scratch slug, never a real map's
    cfg = json.loads(SRC.read_text(encoding="utf-8"))
    cfg["slug"] = SLUG
    cfg["page"] = PAGE_REL
    cfg.pop("pages", None)
    CFG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    (ROOT / PAGE_REL).write_text(
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>scratch</title>\n'
        '<link href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600;700&amp;display=swap" rel="stylesheet">\n'
        '</head>\n<body><main><div class="article-body">\n'
        '<div class="fn-section" id="general">\n  <p>Intro paragraph.</p>\n  <ul><li>Getting around</li></ul>\n</div>\n'
        '</div></main></body></html>\n', encoding="utf-8")
    return cfg


def cleanup(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    for p in [CFG] + list(PREV.glob(f"{SLUG}*")) + list(PREV.glob(f"_edit-{SLUG}*")):
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


def label_tf(svg, text):
    """(x, y) of the stamp a label with this text sits in"""
    m = re.search(r'<g transform="translate\(([-\d.]+) ([-\d.]+)\)[^"]*">(?:<g class="eh"[^>]*>)?<text[^>]*>' + re.escape(text), svg)
    return (float(m.group(1)), float(m.group(2))) if m else None


def legend_tf(svg):
    m = re.search(r'<g transform="translate\(([-\d.]+) ([-\d.]+)\) scale\([\d.]+\)">\s*(?:<g class="eh" data-kind="legend"[^>]*>)?\s*<rect x="0" y="0"', svg)
    return (float(m.group(1)), float(m.group(2))) if m else None


def main():
    from playwright.sync_api import sync_playwright
    import itinerary_map as im
    cfg0 = setup()
    svg0, _, _ = im.render(cfg0)                       # the desktop build before any move
    msvg0, _, _ = im.render(im.mobile_cfg(cfg0))
    PREV.mkdir(parents=True, exist_ok=True)
    log = open(SCRATCH / "server.log", "w", encoding="utf-8")
    proc = None
    try:
        proc = start_server(log)
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 1400, "height": 900}, device_scale_factor=1, has_touch=True)
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
            pg.on("dialog", lambda d: d.accept())
            pg.goto(f"{API}/?slug={SLUG}")
            pg.wait_for_selector('#canvas svg .eh[data-kind="country"]', timeout=60000)
            pg.wait_for_timeout(300)

            def status():
                return pg.evaluate("document.getElementById('status').textContent")

            def settled(want=None, t=30000):
                pg.wait_for_function("() => document.getElementById('status').textContent !== 'rendering…' && document.getElementById('status').textContent !== 'saving…'", timeout=t)
                if want is not None:
                    pg.wait_for_function("w => document.getElementById('status').textContent.startsWith(w)", arg=want, timeout=t)
                pg.wait_for_timeout(150)

            def center(sel):
                bb = pg.locator(sel).first.bounding_box()
                return (bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)

            def drag(sel, dx, dy):
                x, y = center(sel)
                pg.mouse.move(x, y); pg.mouse.down()
                for k in range(1, 5):
                    pg.mouse.move(x + dx * k / 4, y + dy * k / 4)
                pg.mouse.up()
                settled("unsaved")
                return (x, y)

            def near(a, b, tol=4):
                return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

            C0 = '#canvas svg .eh[data-kind="country"][data-idx="0"]'
            C1 = '#canvas svg .eh[data-kind="country"][data-idx="1"]'
            W0 = '#canvas svg .eh[data-kind="water"][data-idx="0"]'
            LG = '#canvas svg .eh[data-kind="legend"]'
            S2 = '#canvas svg .eh[data-kind="stop"][data-idx="2"]'
            home_c0 = center(C0)
            home_lg = center(LG)
            ok("D1 the editor draws handles on the country names, the water name and the legend",
               pg.locator(C0).count() == 1 and pg.locator(W0).count() == 1 and pg.locator(LG).count() == 1)
            hint = pg.evaluate("document.getElementById('hint').textContent")
            ok("D1 the hint says country/water names and the legend drag and double-click back", "country" in hint and "legend" in hint and "put it back" in hint, hint)

            # --- D1 mouse drags
            x0 = drag(C0, -60, 30)                 # left: the name sits by the right frame edge, and a label is kept inside the frame
            c = pg.evaluate("cfg.country_labels[0]")
            ok("D1 a country name drag writes dx/dy to its config entry", c.get("dx", 0) < 0 and c.get("dy", 0) > 0, c)
            ok("D1 the redrawn country name sits where it was dropped", near(center(C0), (x0[0] - 60, x0[1] + 30)), (center(C0), x0))
            x0 = drag(W0, -50, 20)
            w = pg.evaluate("cfg.water_labels[0]")
            ok("D1 a water name drag writes dx/dy", w.get("dx", 0) < 0 and w.get("dy", 0) > 0, w)
            ok("D1 the redrawn water name sits where it was dropped", near(center(W0), (x0[0] - 50, x0[1] + 20)), (center(W0), x0))
            x0 = drag(LG, -80, 60)
            lg = pg.evaluate("cfg.legend")
            ok("D1 a legend drag writes dx/dy (window units) to cfg.legend", lg.get("dx", 0) < 0 and lg.get("dy", 0) > 0 and "pos" in lg, lg)
            ok("D1 the redrawn legend sits where it was dropped", near(center(LG), (x0[0] - 80, x0[1] + 60)), (center(LG), x0))
            ok("D1 nothing else moved: a stop label keeps its offsets", pg.evaluate("cfg.stops[2].dx") == cfg0["stops"][2]["dx"])

            # --- D2 touch drag (one finger), as a phone or tablet would
            cdp = ctx.new_cdp_session(pg)
            x, y = center(C1)
            cfgC1 = pg.evaluate("JSON.stringify(cfg.country_labels[1])")
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y, "id": 1}]})
            for k in range(1, 5):
                cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + 40 * k / 4, "y": y - 24 * k / 4, "id": 1}]})
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            settled("unsaved")
            c1 = pg.evaluate("cfg.country_labels[1]")
            ok("D2 a one-finger touch drag moves a country name", c1.get("dx", 0) > 0 and c1.get("dy", 0) < 0 and json.dumps(c1) != cfgC1, c1)
            ok("D2 the touched name sits where the finger lifted", near(center(C1), (x + 40, y - 24), 6), (center(C1), (x, y)))

            # --- D3 phone view
            ok("D3 a map with a mobile build shows the Phone button", pg.evaluate("!document.getElementById('phone').hidden"))
            pg.click("#phone"); settled()
            ok("D3 switching views does not count as an edit (status unchanged)", status() == "unsaved changes", status())
            vb = pg.evaluate("document.querySelector('#canvas svg').getAttribute('viewBox')")
            vw, vh = [float(v) for v in vb.split()[2:]]
            ok("D3 the Phone view shows the portrait build", vw < vh, vb)
            desk_c1 = pg.evaluate("JSON.stringify(cfg.country_labels[1])")
            drag(C1, 30, -20)
            mc1 = pg.evaluate("cfg.mobile.country_labels[1]")
            ok("D3 a drag in the Phone view lands in cfg.mobile (the phone's own label list here)", mc1.get("dx", 0) > 0 and mc1.get("dy", 0) < 0, mc1)
            ok("D3 the desktop entry is untouched by a phone move", pg.evaluate("JSON.stringify(cfg.country_labels[1])") == desk_c1)
            drag(S2, 25, 10)
            ms2 = pg.evaluate("cfg.mobile.stops['2']")
            ok("D3 a stop label moved in the Phone view patches cfg.mobile.stops['<i>']", ms2 and ms2.get("dx", 0) > 14, ms2)
            ok("D3 the desktop stop keeps its offsets", pg.evaluate("cfg.stops[2].dx") == cfg0["stops"][2]["dx"])
            drag(LG, -30, -30)
            mlg = pg.evaluate("cfg.mobile.legend")
            ok("D3 the phone legend gets its own dx/dy in cfg.mobile.legend", mlg and "dx" in mlg and "dy" in mlg and mlg.get("pos") == "br", mlg)
            ok("D3 the desktop legend keeps its move", pg.evaluate("cfg.legend.dx") == lg["dx"])
            pg.click("#phone"); settled()
            ok("D3 back on Desktop the country name is where the desktop move put it", pg.evaluate("cfg.country_labels[0].dx") == c["dx"] and pg.evaluate("document.getElementById('phone').textContent") == "Phone")

            # --- D4 save
            pg.click("#save"); settled("saved", 120000)
            ok("D4 Save reports the scratch page", status().startswith("saved → " + PAGE_REL), status())
            disk = json.loads(CFG.read_text(encoding="utf-8"))
            ok("D4 the JSON on disk carries the country, water and legend overrides",
               disk["country_labels"][0].get("dx") == c["dx"] and disk["water_labels"][0].get("dx") == w["dx"] and disk["legend"].get("dx") == lg["dx"] and disk["legend"].get("dy") == lg["dy"], {k: disk.get(k) for k in ("legend",)})
            ok("D4 and the phone block's moves", disk["mobile"]["country_labels"][1].get("dx") == mc1["dx"] and disk["mobile"]["stops"]["2"].get("dx") == ms2.get("dx") and disk["mobile"]["legend"].get("dx") == mlg["dx"], disk["mobile"].get("legend"))
            ok("D4 the page key survives the save", disk.get("page") == PAGE_REL)
            svg1 = (PREV / f"{SLUG}-map.svg").read_text(encoding="utf-8")
            msvg1 = (PREV / f"{SLUG}-mob-map.svg").read_text(encoding="utf-8")
            a0, a1 = label_tf(svg0, "NORTH MACEDONIA"), label_tf(svg1, "NORTH MACEDONIA")
            ok("D4 the rebuilt desktop SVG draws the country name moved by its dx/dy", a0 and a1 and near((a1[0] - a0[0], a1[1] - a0[1]), (c["dx"], c["dy"]), 0.2), (a0, a1, c))
            w0, w1 = label_tf(svg0, "LAKE OHRID"), label_tf(svg1, "LAKE OHRID")
            ok("D4 and the water name", w0 and w1 and near((w1[0] - w0[0], w1[1] - w0[1]), (w["dx"], w["dy"]), 0.2), (w0, w1, w))
            l0, l1 = legend_tf(svg0), legend_tf(svg1)
            ok("D4 and the legend box", l0 and l1 and near((l1[0] - l0[0], l1[1] - l0[1]), (lg["dx"], lg["dy"]), 0.2), (l0, l1, lg))
            m0, m1 = label_tf(msvg0, "ALBANIA"), label_tf(msvg1, "ALBANIA")
            ok("D4 the rebuilt phone SVG draws its own country move", m0 and m1 and near((m1[0] - m0[0], m1[1] - m0[1]), (mc1["dx"], mc1["dy"]), 0.2), (m0, m1, mc1))
            ok("D4 no edit handles leak into the saved SVGs", 'class="eh"' not in svg1 and 'class="eh"' not in msvg1 and "data-proj" not in svg1)
            page_html = (ROOT / PAGE_REL).read_text(encoding="utf-8")
            ok("D4 the figure is embedded in the scratch page with both builds",
               f'<figure class="itinerary-map" data-map="{SLUG}"' in page_html and 'class="imap-desk"' in page_html and 'class="imap-mob"' in page_html)
            pg.locator("#canvas").screenshot(path=str(SHOT_EDITOR))
            pg2 = ctx.new_page()
            pg2.goto((ROOT / PAGE_REL).resolve().as_uri()); pg2.wait_for_timeout(800)
            pg2.locator("figure.itinerary-map").screenshot(path=str(SHOT_PAGE))
            pg2.close()
            ok("D4 1x screenshots written", SHOT_EDITOR.is_file() and SHOT_PAGE.is_file(), (SHOT_EDITOR, SHOT_PAGE))

            # --- D5 reset by double-click
            pg.locator(C0).dblclick(); settled("unsaved")
            ok("D5 double-click drops a country name's dx/dy", pg.evaluate("'dx' in cfg.country_labels[0] || 'dy' in cfg.country_labels[0]") is False)
            ok("D5 and the name returns to its lat/lon spot", near(center(C0), home_c0), (center(C0), home_c0))
            pg.locator(LG).dblclick(); settled("unsaved")
            ok("D5 double-click drops the legend's dx/dy", pg.evaluate("'dx' in cfg.legend || 'dy' in cfg.legend") is False)
            ok("D5 and the legend returns to its corner", near(center(LG), home_lg), (center(LG), home_lg))
            ok("D5 the water name's move is kept", pg.evaluate("cfg.water_labels[0].dx") == w["dx"])
            pg.click("#save"); settled("saved", 120000)
            disk = json.loads(CFG.read_text(encoding="utf-8"))
            ok("D5 the saved JSON has no override on the reset labels", "dx" not in disk["country_labels"][0] and "dx" not in disk["legend"] and disk["water_labels"][0].get("dx") == w["dx"])
            ok("D5 the saved desktop SVG draws the country name back where it started", label_tf((PREV / f"{SLUG}-map.svg").read_text(encoding="utf-8"), "NORTH MACEDONIA") == a0)

            # --- D6 a plain map
            r = json.loads(urllib.request.urlopen(API + "/api/map/egypt", timeout=120).read())
            ok("D6 a map with no country/water names or legend still opens", "svg" in r and 'data-kind="stop"' in r["svg"] and 'data-kind="legend"' not in r["svg"])
            ok("D7 no page errors", not errs, errs[:2])
            b.close()
    finally:
        log.close()                                    # before the rmtree: Windows keeps an open file's folder
        cleanup(proc)
    bad = sum(1 for _, c, _ in checks if not c)
    print("%d checks, %d failed" % (len(checks), bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
