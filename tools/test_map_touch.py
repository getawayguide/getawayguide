"""Mobile touch test for the destinations map using RAW touch events with
explicit touch IDs — including real-device scenarios like a thumb resting
elsewhere on the page during map gestures (the 'can't pan after zoom' bug).
"""
import http.server, socketserver, threading, os, sys, time

os.chdir(r"c:/Users/kevin/OneDrive/Documents/Travel Blog")
PORT = 8807

from playwright.sync_api import sync_playwright


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


httpd = socketserver.TCPServer(("127.0.0.1", PORT), QuietHandler)
httpd.allow_reuse_address = True
threading.Thread(target=httpd.serve_forever, daemon=True).start()

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + ("  | " + str(detail) if detail else ""))


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True,
                        has_touch=True, device_scale_factor=3)
    pg = ctx.new_page()
    cdp = ctx.new_cdp_session(pg)

    def te(type_, points):
        """points = FULL active touch set after this event: [(id, x, y), ...]"""
        cdp.send("Input.dispatchTouchEvent", {
            "type": type_,
            "touchPoints": [{"x": x, "y": y, "id": i} for (i, x, y) in points]
        })

    def touch_drag(x, y, dx, dy, steps=14, dt=0.018, tid=0, held=()):
        held = list(held)
        te("touchStart", held + [(tid, x, y)])
        for i in range(1, steps + 1):
            te("touchMove", held + [(tid, x + dx * i / steps, y + dy * i / steps)])
            time.sleep(dt)
        te("touchEnd", held)          # lift the dragging finger, keep held
        pg.wait_for_timeout(450)

    def touch_pinch(cx, cy, d0, d1, steps=14, dt=0.018, held=()):
        held = list(held)
        te("touchStart", held + [(0, cx - d0 / 2, cy), (1, cx + d0 / 2, cy)])
        for i in range(1, steps + 1):
            d = d0 + (d1 - d0) * i / steps
            te("touchMove", held + [(0, cx - d / 2, cy), (1, cx + d / 2, cy)])
            time.sleep(dt)
        te("touchEnd", held)
        pg.wait_for_timeout(550)

    def touch_tap(x, y, tid=0):
        te("touchStart", [(tid, x, y)])
        time.sleep(0.05)
        te("touchEnd", [])
        pg.wait_for_timeout(300)

    def load():
        pg.goto(f"http://127.0.0.1:{PORT}/destinations.html", wait_until="domcontentloaded")
        pg.wait_for_function("window.__destMap && window.__destMap.series.length >= 3", timeout=20000)
        pg.wait_for_timeout(1800)

    def zoom_level():
        return pg.evaluate("window.__destMap.zoomLevel")

    def pan_behavior():
        return pg.evaluate("window.__destMap.panBehavior")

    def map_center():
        return pg.evaluate("""()=>{const b=document.getElementById('world-map').getBoundingClientRect();
            return {x:b.x+b.width/2,y:b.y+b.height/2,bottom:b.bottom};}""")

    def container_pos():
        return pg.evaluate("({x:window.__destMap.seriesContainer.pixelX, y:window.__destMap.seriesContainer.pixelY})")

    def zoomed_class():
        return pg.evaluate("document.getElementById('explore-map').classList.contains('map-zoomed')")

    # ================= original core scenarios =================
    load()
    c = map_center()
    sc0 = container_pos()
    y0 = pg.evaluate("window.scrollY")
    touch_drag(c["x"], c["y"], 0, -260)
    y1 = pg.evaluate("window.scrollY")
    sc1 = container_pos()
    drift = abs(sc1["x"] - sc0["x"]) + abs(sc1["y"] - sc0["y"])
    check("page scrolls over map at rest", y1 > y0 + 80, f"scrollY {y0} -> {y1}")
    check("map doesn't drift during page scroll", drift < 8, f"drift={drift:.1f}px")

    pg.evaluate("window.scrollTo(0, 0)")
    pg.wait_for_timeout(400)
    c = map_center()
    touch_pinch(c["x"], c["y"], 60, 220)
    z1 = zoom_level()
    check("pinch-in zooms inline map", z1 > 1.3, f"zoomLevel={z1:.2f}")
    check("map-zoomed class set", zoomed_class())
    check("amCharts pan stays off on mobile (manual gestures own it)", pan_behavior() == "none", pan_behavior())

    sc0 = container_pos()
    ys0 = pg.evaluate("window.scrollY")
    touch_drag(c["x"], c["y"], 110, 70)
    sc1 = container_pos()
    ys1 = pg.evaluate("window.scrollY")
    moved = abs(sc1["x"] - sc0["x"]) + abs(sc1["y"] - sc0["y"]) > 20
    check("drag pans zoomed map (page stays)", moved and ys1 == ys0,
          f"moved {abs(sc1['x']-sc0['x']):.0f},{abs(sc1['y']-sc0['y']):.0f}px")

    touch_pinch(c["x"], c["y"], 240, 50)
    z2 = zoom_level()
    check("pinch-out returns toward zoom 1", z2 < 1.15, f"zoomLevel={z2:.2f}")
    check("map-zoomed class cleared", not zoomed_class())
    y0 = pg.evaluate("window.scrollY")
    touch_drag(c["x"], c["y"], 0, -260)
    y1 = pg.evaluate("window.scrollY")
    check("page scrolls again after zoom-out", y1 > y0 + 80, f"scrollY {y0} -> {y1}")

    pg.evaluate("window.scrollTo(0, 0)")
    pg.wait_for_timeout(400)
    c = map_center()
    touch_pinch(c["x"], c["y"], 60, 200)
    check("second pinch-in still works", zoom_level() > 1.3, f"zoomLevel={zoom_level():.2f}")
    pg.evaluate("window.__destMap.goHome(0)")
    pg.wait_for_timeout(400)

    # ================= NEW: resting-thumb scenarios (the reported bug) =================
    load()
    c = map_center()
    thumb_y = min(c["bottom"] + 160, 800)
    thumb = (9, 60, thumb_y)                     # thumb resting on the page BELOW the map

    # thumb down first, then pinch on the map (3 total touches)
    te("touchStart", [thumb])
    time.sleep(0.05)
    touch_pinch(c["x"], c["y"], 60, 220, held=[thumb])
    zt = zoom_level()
    check("THUMB: pinch works with thumb resting off-map", zt > 1.3, f"zoomLevel={zt:.2f}")

    # fingers are up, thumb still down — now lift thumb
    te("touchEnd", [])
    pg.wait_for_timeout(400)
    check("THUMB: state clean after staggered lifts", pan_behavior() == "none", pan_behavior())

    # the reported bug: drag after the thumb-pinch must pan
    sc0 = container_pos()
    touch_drag(c["x"], c["y"], 110, 70)
    sc1 = container_pos()
    check("THUMB: drag pans after thumb-pinch (bug repro)",
          abs(sc1["x"] - sc0["x"]) + abs(sc1["y"] - sc0["y"]) > 20,
          f"moved {abs(sc1['x']-sc0['x']):.0f},{abs(sc1['y']-sc0['y']):.0f}px")

    # thumb + ONE map finger while zoomed: must PAN, not zoom
    z_before = zoom_level()
    te("touchStart", [thumb])
    time.sleep(0.05)
    sc0 = container_pos()
    touch_drag(c["x"], c["y"], 90, 60, held=[thumb])
    te("touchEnd", [])
    pg.wait_for_timeout(400)
    sc1 = container_pos()
    z_after = zoom_level()
    check("THUMB: one map finger pans (doesn't zoom) despite thumb",
          abs(sc1["x"] - sc0["x"]) + abs(sc1["y"] - sc0["y"]) > 20 and abs(z_after - z_before) < 0.15,
          f"moved {abs(sc1['x']-sc0['x']):.0f},{abs(sc1['y']-sc0['y']):.0f}px, zoom {z_before:.2f}->{z_after:.2f}")
    pg.evaluate("window.__destMap.goHome(0)")
    pg.wait_for_timeout(400)

    # ================= country tap =================
    load()
    box = pg.evaluate("""()=>{
        const ch = window.__destMap; let poly=null;
        ch.series.each(s=>{ if(s.include && s.include.indexOf('BR')>-1 && s.getPolygonById){ const p=s.getPolygonById('BR'); if(p) poly=p; }});
        if(!poly) return null;
        const r = poly.dom.getBoundingClientRect();
        return {x:r.x+r.width/2, y:r.y+r.height/2};
    }""")
    if box:
        try:
            with pg.expect_navigation(timeout=6000):
                touch_tap(box["x"], box["y"])
            nav_ok = "brazil/field-notes" in pg.url
        except Exception:
            nav_ok = False
        check("single tap on Brazil navigates", nav_ok, pg.url)
    else:
        check("single tap on Brazil navigates", False, "BR polygon not found")

    # ================= fullscreen =================
    load()
    hd = pg.evaluate("""()=>{const b=document.querySelector('.map-mobile-hd').getBoundingClientRect();
        return {x:b.x+b.width/2,y:b.y+b.height/2};}""")
    touch_tap(hd["x"], hd["y"])
    pg.wait_for_timeout(700)
    check("tap header opens fullscreen",
          pg.evaluate("document.getElementById('explore-map').classList.contains('map-fs')"))

    c = map_center()
    touch_pinch(c["x"], c["y"], 60, 230)
    check("fullscreen pinch-in works", zoom_level() > 1.3, f"zoomLevel={zoom_level():.2f}")

    sc0 = container_pos()
    touch_drag(c["x"], c["y"], 130, 90)
    sc1 = container_pos()
    check("fullscreen drag pans", abs(sc1["x"] - sc0["x"]) + abs(sc1["y"] - sc0["y"]) > 20,
          f"moved {abs(sc1['x']-sc0['x']):.0f},{abs(sc1['y']-sc0['y']):.0f}px")

    # NEW: fullscreen thumb-on-legend pinch, then pan (bug repro in fullscreen)
    fs_thumb = (9, 60, 45)                       # on the legend strip, outside #world-map
    te("touchStart", [fs_thumb])
    time.sleep(0.05)
    touch_pinch(c["x"], c["y"], 200, 70, held=[fs_thumb])   # zoom out a bit
    touch_pinch(c["x"], c["y"], 60, 200, held=[fs_thumb])   # zoom back in
    te("touchEnd", [])
    pg.wait_for_timeout(400)
    sc0 = container_pos()
    touch_drag(c["x"], c["y"], -100, -60)
    sc1 = container_pos()
    check("FS THUMB: drag pans after legend-thumb pinches",
          abs(sc1["x"] - sc0["x"]) + abs(sc1["y"] - sc0["y"]) > 20,
          f"moved {abs(sc1['x']-sc0['x']):.0f},{abs(sc1['y']-sc0['y']):.0f}px, pan={pan_behavior()}")


    # ================= NEW: deep-ocean (Atlantic) scenario =================
    pg.evaluate("window.__destMap.goHome(0)")
    pg.wait_for_timeout(500)
    check("geoPointToSVG available", pg.evaluate("typeof window.__destMap.geoPointToSVG === 'function'"))
    pt = pg.evaluate("""()=>{const p=window.__destMap.geoPointToSVG({latitude:25, longitude:-35});
        const b=document.getElementById('world-map').getBoundingClientRect();
        return {x:b.x+p.x, y:b.y+p.y};}""")
    touch_pinch(pt["x"], pt["y"], 60, 240)
    touch_pinch(pt["x"], pt["y"], 60, 240)
    za = zoom_level()
    check("ATLANTIC: deep pinch over open ocean zooms", za > 5, f"zoomLevel={za:.2f}")
    sc0 = container_pos()
    touch_drag(pt["x"], pt["y"], 150, 100)
    sc1 = container_pos()
    check("ATLANTIC: drag pans over pure ocean (nothing under finger)",
          abs(sc1["x"] - sc0["x"]) + abs(sc1["y"] - sc0["y"]) > 20,
          f"moved {abs(sc1['x']-sc0['x']):.0f},{abs(sc1['y']-sc0['y']):.0f}px")
    touch_pinch(pt["x"], pt["y"], 240, 40)
    touch_pinch(pt["x"], pt["y"], 240, 40)
    zb = zoom_level()
    check("ATLANTIC: pinch-out recovers from deep ocean zoom", zb < za / 2, f"{za:.2f} -> {zb:.2f}")
    cov = pg.evaluate("""()=>{const ch=window.__destMap;
        const nw=ch.geoPointToSVG({latitude:84, longitude:-169.9});
        const se=ch.geoPointToSVG({latitude:-55.8, longitude:189.9});
        return {l:Math.round(nw.x), r:Math.round(se.x), W:ch.pixelWidth};}""")
    check("ATLANTIC: view clamped to map bounds (no blank void)",
          cov["l"] <= 2 and cov["r"] >= cov["W"] - 2, str(cov))


    # ================= NEW: grip / ratchet / palm scenarios =================
    # GRIP: thumb resting ON the fullscreen map near the bottom edge must not
    # join the pinch pair (this mispairing caused the "second zoom resets" bug)
    pg.evaluate("window.__destMap.goHome(0)")
    pg.wait_for_timeout(500)
    grip_y = pg.evaluate("document.getElementById('world-map').getBoundingClientRect().bottom - 8")
    grip = (9, 120, grip_y)
    te("touchStart", [grip])
    time.sleep(0.06)
    touch_pinch(c["x"], c["y"], 60, 240, held=[grip])
    zg = zoom_level()
    check("GRIP: pinch zooms IN with grip-thumb on map edge", zg > 1.5, f"zoomLevel={zg:.2f}")
    sc0 = container_pos()
    touch_drag(c["x"], c["y"], 100, 60, held=[grip])
    sc1 = container_pos()
    check("GRIP: single finger pans despite grip on map",
          abs(sc1["x"] - sc0["x"]) + abs(sc1["y"] - sc0["y"]) > 20,
          f"moved {abs(sc1['x']-sc0['x']):.0f},{abs(sc1['y']-sc0['y']):.0f}px")
    te("touchEnd", [])
    pg.wait_for_timeout(300)

    # RATCHET: pinch in, lift one finger, re-place it, spread again ->
    # zoom continues from current level, never resets
    pg.evaluate("window.__destMap.goHome(0)")
    pg.wait_for_timeout(500)
    touch_pinch(c["x"], c["y"], 60, 240)
    z_r1 = zoom_level()
    cx, cy = c["x"], c["y"]
    te("touchStart", [(0, cx - 110, cy), (1, cx + 110, cy)])
    time.sleep(0.05)
    te("touchEnd", [(0, cx - 110, cy)])          # lift finger 1, keep finger 0
    time.sleep(0.08)
    te("touchStart", [(0, cx - 110, cy), (2, cx - 50, cy)])   # re-place close to finger 0
    time.sleep(0.05)
    for i in range(1, 13):                        # spread finger 2 rightward
        te("touchMove", [(0, cx - 110, cy), (2, cx - 50 + 210 * i / 12, cy)])
        time.sleep(0.018)
    te("touchEnd", [])
    pg.wait_for_timeout(550)
    z_r2 = zoom_level()
    check("RATCHET: re-placed finger continues zoom (no reset)", z_r2 > z_r1 * 1.5,
          f"{z_r1:.2f} -> {z_r2:.2f}")

    # PALM BLIP: a brief extra touch mid-pinch must not reset the zoom
    pg.evaluate("window.__destMap.goHome(0)")
    pg.wait_for_timeout(500)
    te("touchStart", [(0, cx - 30, cy), (1, cx + 30, cy)])
    for i in range(1, 11):                        # spread 60 -> 200
        d = 60 + 140 * i / 10
        te("touchMove", [(0, cx - d / 2, cy), (1, cx + d / 2, cy)])
        time.sleep(0.018)
    z_p1 = zoom_level()
    palm = (5, cx - 80, cy - 60)
    te("touchStart", [(0, cx - 100, cy), (1, cx + 100, cy), palm])   # palm lands
    time.sleep(0.09)
    te("touchEnd", [(0, cx - 100, cy), (1, cx + 100, cy)])           # palm lifts
    time.sleep(0.05)
    for i in range(1, 11):                        # continue spreading 200 -> 300
        d = 200 + 100 * i / 10
        te("touchMove", [(0, cx - d / 2, cy), (1, cx + d / 2, cy)])
        time.sleep(0.018)
    te("touchEnd", [])
    pg.wait_for_timeout(550)
    z_p2 = zoom_level()
    check("PALM BLIP: brief extra touch doesn't reset zoom", z_p2 > z_p1 * 0.85,
          f"before blip {z_p1:.2f} -> after {z_p2:.2f}")

    ok_cycles, detail = True, ""
    for i in range(3):
        touch_pinch(c["x"], c["y"], 240, 60)
        touch_pinch(c["x"], c["y"], 60, 210)
        z = zoom_level()
        if not (z > 1.2):
            ok_cycles, detail = False, f"cycle {i}: zoomLevel={z:.2f}"
            break
    check("3 rapid pinch cycles never wedge (fullscreen)", ok_cycles,
          detail or f"final zoom={zoom_level():.2f}")

    pg.evaluate("document.querySelector('.map-close-btn').click()")
    pg.wait_for_timeout(600)
    check("close restores inline state", pg.evaluate(
        "!document.getElementById('explore-map').classList.contains('map-fs')"))

    # ================= desktop =================
    ctx2 = b.new_context(viewport={"width": 1440, "height": 900})
    pg2 = ctx2.new_page()
    pg2.goto(f"http://127.0.0.1:{PORT}/destinations.html", wait_until="domcontentloaded")
    pg2.wait_for_function("window.__destMap", timeout=20000)
    pg2.wait_for_timeout(1200)
    check("desktop: pan enabled", pg2.evaluate("window.__destMap.panBehavior === 'move'"))

    b.close()

httpd.shutdown()
fails = [r for r in results if not r[1]]
print(f"\n{len(results)-len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
