#!/usr/bin/env python3
"""Reusable city-map builder (WAT tool).

Builds a two-variant "everything in the city" POI map from a JSON config:
  - DESKTOP: landscape street map + floating category key + hover sync
  - MOBILE:  portrait gesture map (drag-pan / pinch-zoom / tap-for-name /
             double-tap-to-open), with a fixed title + instructions overlay
             that the map pans underneath, and no key.

The street layer is rasterized to a PNG (Playwright, 2x) and the pins are drawn
as a light vector overlay on top, so the map ALWAYS paints (this sidesteps a
Chromium paint-deferral bug with heavy all-vector inline SVGs).

Usage:
  python tools/city_map.py tools/city_maps/riga.json            # build PNGs + standalone preview
  python tools/city_map.py tools/city_maps/riga.json --embed    # also embed the map into cfg["article"]
  python tools/city_map.py tools/city_maps/riga.json --demo 7   # preview auto-opens pin #7's name bubble

OSM street data is fetched SEPARATELY. The Bash tool here has no network, so
fetch with PowerShell (Overpass) and save to the path in cfg["osm"]:
  see tools/city_maps/README.md for the exact Overpass query.

POI CATEGORY RULE: a pin's `cat` comes from the article SUB-SECTION it is written
under, not from what the place "is":
  - "What to Do" / attractions / sights (incl. squares, neighborhoods,
    bazaars, viewpoints) -> "see"  (ATTRACTIONS)
  - "Where to Eat" (bars, restaurants, cafés, bakeries)          -> "eat"
  - "Where to Stay" (hotels, hostels, guesthouses)               -> "stay"

WHICH PLACES TO PICK UP: include every map-linked place that is the subject of a
bullet, PLUS notable named places listed inside a top-level bullet's prose (e.g.
"Skanderbeg Square ... surrounded by the National Museum, Et'hem Bej Mosque and
the Clock Tower" -> all four are pins; "Tirana Castle ... visit the New Bazaar
inside" -> both are pins). Do NOT pick up incidental links buried inside a
SUB-bullet's description (e.g. under Livadi Beach: "stop by the roof of the
abandoned hotel for a view" -> not a pin). Skip duplicate references, geographic
outliers that would wreck the frame (note them to the user), and places with no
resolvable coordinates (e.g. Airbnb listings not in OSM).

DISTRICT LABEL: if the article names a neighborhood/district (e.g. an old town or
"downtown"), add `district_label {text, lat, lon}` — a faint place name baked onto
the map (like VECRĪGA on Riga). Place it in that district but clear of the floating
key (top-right on desktop); nudge `lat/lon` and, if needed, the desktop `pad` so it
doesn't collide with the key.

STANDARD SIZE: every map is the SAME size — desktop 760x470, mobile 660x820
portrait (see DESK_*/MOB_* constants). Configs only set the framing: desktop `pad`,
and mobile `pad` + `init_w/init_x/init_y` (opening view). Any w/h/dar/pin_scale in a
config is ignored.

GROUPING: by default the legend groups pins into ATTRACTIONS/EAT/STAY by each POI's
`cat` (labels: ATTRACTIONS / RESTAURANTS & BARS / ACCOMMODATION). A config may instead
define its own ordered `groups` (e.g. by NEIGHBORHOOD, the way the article is written)
— a list of {key, label, color}; each POI then uses `group` (a group's key) instead of
`cat`, pins are coloured by group, and the legend is grouped/ordered to match. For a
NEIGHBORHOOD map: FOLD eat/stay POIs into their neighborhood group (no separate eat/stay
groups — assign each by area) and add a faint neighborhood-name label per area via
`district_labels`. Buenos Aires is the template (PALERMO / RECOLETA & MONSERRAT /
SAN TELMO / LA BOCA). Pick group colours dark enough to read white pin numbers on.
For a big multi-neighborhood city the legend is tall and the far-side cluster can fall
under the key — nudge the frame with `center` (see Buenos Aires) so it clears.

DISTRICT LABELS: `district_labels` (list of {text,lat,lon}; single `district_label`
still works) are faint place names baked onto the map — clear of the title and key.
The DESKTOP base is a plain <img> (reliably painted; survives HTML editors); MOBILE
keeps the base in the SVG so gesture pan/zoom moves base+pins together.
"""
import json, math, os, html, re, asyncio, base64, argparse
from io import BytesIO
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).replace("\\", "/")
PREV = f"{ROOT}/.tmp/previews"
IMGDIR = f"{ROOT}/Images/web/city-maps"

# ---- shared site palette / conventions (do not vary per city) ----------------
LAND = "#F4F2EC"; WATER = "#B4D3D9"; PARK = "#CFE3C6"; TOP = "#FFFFFF"
CASING = "#E6E1D4"; INK = "#1C2821"; KICKER = "#2D6B50"
CAT = {"see":  ("ATTRACTIONS",        "#245A43", "#ffffff", "#245A43"),
       "eat":  ("RESTAURANTS & BARS", "#C8DDD5", "#1C2821", "#3B6B54"),
       "stay": ("ACCOMMODATION",      "#6B716C", "#ffffff", "#565B57")}
CAT_ORDER = ["see", "eat", "stay"]
PIN = "M0 0 C-3.4 -8 -8 -11.4 -8 -17.7 A8 8 0 1 1 8 -17.7 C8 -11.4 3.4 -8 0 0 Z"

# ---- STANDARD FORMAT: uniform across every city so all maps render at the same
# size. Only the FRAMING knobs (desktop `pad`; mobile `pad` + `init_w/x/y`) live in
# each config. w/h/dar/pin_scale/title_px are fixed here and ignored in configs. ----
DESK_W, DESK_H, DESK_PIN = 760, 470, 1.0        # landscape + floating key
MOB_W, MOB_H, MOB_PIN, MOB_DAR = 660, 820, 1.6, 0.72   # portrait gesture map
TITLE_PX = 34
ROADW = {"living_street": 1.0, "pedestrian": 1.1, "residential": 1.2, "unclassified": 1.2,
         "tertiary": 1.7, "tertiary_link": 1.2, "secondary": 2.3, "secondary_link": 1.5,
         "primary": 2.9, "primary_link": 1.8, "trunk": 3.1, "trunk_link": 1.8,
         "motorway": 3.3, "motorway_link": 1.8}
MAJOR = ("tertiary", "tertiary_link", "secondary", "secondary_link", "primary",
         "primary_link", "trunk", "trunk_link", "motorway", "motorway_link")


def merc(lat, lon):
    return math.radians(lon), math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def parse_osm(elements):
    """Split a raw Overpass export into water polygons/lines, parks, roads, coastline, and
    water-island rings (islands that are HOLES in a water multipolygon, e.g. Stockholm's
    Kungsholmen in Lake Mälaren — filled back as LAND so they don't render flooded)."""
    water, parks, roads, coast, water_islands = [], [], {k: [] for k in ROADW}, [], []
    for el in elements:
        t = el.get("tags", {})
        if el["type"] == "way" and "geometry" in el:
            g = el["geometry"]
            if t.get("highway") in ROADW: roads[t["highway"]].append(g)
            elif t.get("natural") == "coastline": coast.append(g)
            elif t.get("natural") == "water" or t.get("waterway") == "riverbank" or "water" in t: water.append(g)
            elif t.get("waterway") in ("river", "canal"): water.append(("line", g))
            elif t.get("leisure") in ("park", "garden") or t.get("landuse") == "recreation_ground": parks.append(g)
        elif el["type"] == "relation" and t.get("natural") == "water":
            # a water multipolygon (e.g. the Río de la Plata, Lake Mälaren) arrives as many
            # separate boundary ways — chain outer members into rings so the body FILLS.
            # inner members are ISLANDS (holes); assemble them so we can paint them back as
            # land. Huge rings get clipped to the frame by the SVG viewBox.
            mem = el.get("members", [])
            water.extend(assemble_rings([m["geometry"] for m in mem if m.get("role") == "outer" and m.get("geometry")]))
            water_islands.extend(assemble_rings([m["geometry"] for m in mem if m.get("role") == "inner" and m.get("geometry")]))
    return water, parks, roads, coast, water_islands


def assemble_rings(ways):
    """Chain boundary ways (lists of {lat,lon}) into rings by their shared end nodes."""
    ways = [list(w) for w in ways if len(w) >= 2]
    def k(p): return (round(p["lat"], 7), round(p["lon"], 7))
    used = [False] * len(ways); rings = []
    for i in range(len(ways)):
        if used[i]: continue
        used[i] = True; ch = list(ways[i]); ext = True
        while ext:
            ext = False
            for j in range(len(ways)):
                if used[j]: continue
                w = ways[j]
                if   k(ch[-1]) == k(w[0]):  ch += w[1:];         used[j] = True; ext = True
                elif k(ch[-1]) == k(w[-1]): ch += w[-2::-1];     used[j] = True; ext = True
                elif k(ch[0])  == k(w[-1]): ch = w[:-1] + ch;    used[j] = True; ext = True
                elif k(ch[0])  == k(w[0]):  ch = w[1:][::-1] + ch; used[j] = True; ext = True
        rings.append(ch)
    return rings


def landmass_polys(coast, px, W, H, pins, flip=False):
    """LAND polygons (as SVG point-strings) to paint over a WATER background for an
    archipelago/peninsula frame. Robust for ANY coastline extent (tight or wide fetch):
    assemble the coastline into rings, fill closed loops (islands) directly, and close
    each open chain (the mainland) ALONG THE FRAME BORDER — never with a direct line,
    which would cut diagonally across the view once the fetched coastline runs well past
    the frame. OSM keeps land on the left of the way direction, but rather than reason
    about winding we just pick the border-walk direction that puts the most (on-land)
    pins inside the closed land. SVG clips everything past the viewBox."""
    E = 0.5
    def lb(a, b):                                   # Liang-Barsky clip of segment a->b to the frame
        x0, y0 = a; x1, y1 = b; dx = x1 - x0; dy = y1 - y0; t0, t1 = 0.0, 1.0
        for p, q in [(-dx, x0), (dx, W - x0), (-dy, y0), (dy, H - y0)]:
            if abs(p) < 1e-12:
                if q < 0: return None
            else:
                t = q / p
                if p < 0:
                    if t > t1: return None
                    if t > t0: t0 = t
                else:
                    if t < t0: return None
                    if t < t1: t1 = t
        return ((x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy), t0 > 1e-9, t1 < 1 - 1e-9)
    def runs_of(ch):                                # split a polyline into its inside-the-frame runs
        runs = []; cur = None
        for i in range(len(ch) - 1):
            r = lb(ch[i], ch[i + 1])
            if r is None:
                if cur and len(cur) >= 2: runs.append(cur)
                cur = None; continue
            p0, p1, c0, c1 = r
            if cur is None or c0:
                if cur and len(cur) >= 2: runs.append(cur)
                cur = [p0, p1]
            else:
                cur.append(p1)
            if c1:
                if len(cur) >= 2: runs.append(cur)
                cur = None
        if cur and len(cur) >= 2: runs.append(cur)
        return runs
    def onb(p): return p[0] <= E or p[0] >= W - E or p[1] <= E or p[1] >= H - E
    def pt(p):                                      # point -> perimeter param [0,4): top,right,bottom,left
        x, y = p
        if y <= E: return max(0.0, min(1.0, x / W))
        if x >= W - E: return 1 + max(0.0, min(1.0, y / H))
        if y >= H - E: return 2 + max(0.0, min(1.0, (W - x) / W))
        return 3 + max(0.0, min(1.0, (H - y) / H))
    def pp(t):
        t %= 4
        if t < 1: return (t * W, 0.0)
        if t < 2: return (W, (t - 1) * H)
        if t < 3: return ((3 - t) * W, H)
        return (0.0, (4 - t) * H)
    def corners(t0, t1, fwd):                       # frame-corner points between two params, one way round
        span = ((t1 - t0) % 4) if fwd else ((t0 - t1) % 4); out = []
        k = (math.floor(t0) + 1) if fwd else (math.ceil(t0) - 1)
        for _ in range(5):
            d = ((k - t0) % 4) if fwd else ((t0 - k) % 4)
            if 1e-9 < d < span - 1e-9: out.append(pp(k % 4)); k += 1 if fwd else -1
            else: break
        return out
    def close(runs, fwd):                           # join border-runs into closed land rings
        ent = [pt(r[0]) for r in runs]; used = [False] * len(runs); rings = []
        for s in range(len(runs)):
            if used[s]: continue
            ring = []; cur = s; guard = 0
            while cur is not None and not used[cur] and guard < len(runs) + 3:
                used[cur] = True; guard += 1; ring += runs[cur]
                tout = pt(runs[cur][-1]); best = None; bd = 1e9
                for j in range(len(runs)):
                    if used[j]: continue
                    d = ((ent[j] - tout) % 4) if fwd else ((tout - ent[j]) % 4)
                    if 1e-9 < d < bd: bd = d; best = j
                dclose = ((ent[s] - tout) % 4) if fwd else ((tout - ent[s]) % 4)
                if best is None or dclose <= bd + 1e-9:
                    ring += corners(tout, ent[s], fwd); cur = None
                else:
                    ring += corners(tout, ent[best], fwd); cur = best
            if len(ring) >= 3: rings.append(ring)
        return rings
    def inside(q, poly):
        x, y = q; c = False; n = len(poly); j = n - 1
        for i in range(n):
            xi, yi = poly[i]; xj, yj = poly[j]
            if ((yi > y) != (yj > y)) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi: c = not c
            j = i
        return c

    islands = []; runs = []
    for r in assemble_rings(coast):
        p = [px(q["lat"], q["lon"]) for q in r]
        if len(p) < 2: continue
        if (r[0]["lat"], r[0]["lon"]) == (r[-1]["lat"], r[-1]["lon"]):
            islands.append(p)                       # closed loop = an island, fill as-is
        else:
            runs += [rr for rr in runs_of(p) if onb(rr[0]) and onb(rr[-1])]
    # pick the border-walk direction that puts the most on-land pins inside the closed land.
    # `flip` (cfg "sea_flip") forces the OTHER direction when a near-shore pin fools the heuristic.
    if runs:
        opts = [close(runs, f) for f in (True, False)]
        if flip:
            best = opts[1]           # deterministic forced direction (stable regardless of pin moves)
        else:
            sc = [sum(any(inside(q, poly) for poly in o) for q in pins) for o in opts]
            best = opts[0] if sc[0] >= sc[1] else opts[1]
    else:
        best = []
    return [" ".join(f"{a:.1f},{b:.1f}" for a, b in poly) for poly in islands + best if len(poly) >= 3]


async def _raster(svg, W, H):
    doc = (f'<!doctype html><meta charset="utf-8"><body style="margin:0">'
           f'<div id="t" style="width:{W}px;height:{H}px">{svg}</div></body>')
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        ctx = await br.new_context(viewport={"width": W, "height": H}, device_scale_factor=2)
        pg = await ctx.new_page(); await pg.set_content(doc, wait_until="load"); await pg.wait_for_timeout(400)
        png = await pg.locator("#t").screenshot(); await br.close(); return png


def build(cfg_path, embed=False, demo=None):
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    slug = cfg["slug"]; city = cfg["city"]; kicker = cfg["kicker"]
    os.makedirs(IMGDIR, exist_ok=True); os.makedirs(PREV, exist_ok=True)
    osm = json.load(open(f"{ROOT}/{cfg['osm']}", encoding="utf-8-sig"))["elements"]  # -sig: PowerShell Out-File writes a BOM
    water, parks, roads, coast, water_islands = parse_osm(osm)
    up = "../" * cfg["article"].count("/")   # relative depth to repo root (live=../, draft=../../)

    # Grouping: default is the see/eat/stay categories. A config may instead define
    # its own ordered `groups` (e.g. by neighborhood) — then each POI carries a
    # `group` key (matching a group's `key`) instead of `cat`, pins are coloured by
    # group, and the legend is grouped/ordered the same way. Each group needs
    # {key, label, color}. Text colours are derived from the group colour's lightness
    # (like the `eat` light-green category): dark colours get a white legend badge +
    # the colour as the on-map number; LIGHT colours get a dark badge + a darkened
    # number — so a green ramp (dark→light shades) stays legible on both pin and key.
    def _lum(hx):
        r, g, b = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255
    def _dark(hx, f=0.5):
        return "#" + "".join("%02x" % int(int(hx[i:i + 2], 16) * f) for i in (1, 3, 5))
    if cfg.get("groups"):
        def _grp(g):
            light = _lum(g["color"]) > 0.5
            return (g["label"], g["color"], "#1C2821" if light else "#ffffff",
                    _dark(g["color"]) if light else g["color"])
        GRP = {g["key"]: _grp(g) for g in cfg["groups"]}
        GRP_ORDER = [g["key"] for g in cfg["groups"]]
        gkey = lambda p: p["group"]
    else:
        GRP, GRP_ORDER, gkey = CAT, CAT_ORDER, lambda p: p["cat"]

    # POIs in the order they're written about (earlier = lower number = drawn on top)
    POIS = [(p["name"], p["lat"], p["lon"], gkey(p), p.get("match", p["name"].lower())) for p in cfg["pois"]]

    # resolve each pin's Google-Maps link: explicit href > a matching link in the
    # article > a generic maps search for "<name> <city>"
    art = open(f"{ROOT}/{cfg['article']}", encoding="utf-8").read() if cfg.get("article") else ""
    links = [(u.replace("&amp;", "&"), re.sub(r"<[^>]+>", "", t).strip().lower())
             for u, t in re.findall(r'<a\b[^>]*href="(https://www\.google\.com/maps/[^"]+)"[^>]*>(.*?)</a>', art, re.S)]
    explicit = {p["name"]: p["href"] for p in cfg["pois"] if p.get("href")}
    def href_for(name, key):
        if name in explicit: return explicit[name]
        for u, t in links:
            if key in t: return u
        return f"https://www.google.com/maps/search/?api=1&query={(name + ' ' + city).replace(' ', '+')}"
    HREF = {p[0]: href_for(p[0], p[4]) for p in POIS}

    # day_trips / multi_day_trips: legend-ONLY entries (NOT drawn on the map), each with an
    # "↗" off-map marker and a "(time)" note. Multi-day trips (e.g. a 2-3 night sailing trip)
    # render first under a MULTI-DAY TRIP header, then day trips under DAY TRIPS. Name them to
    # match how the article frames the excursion (a tour/activity name, not just a place).
    def _trips(key):
        return [{"name": d["name"], "time": d.get("time", ""),
                 "href": d.get("href") or href_for(d["name"], d.get("match", d["name"].lower()))}
                for d in cfg.get(key, [])]
    MULTITRIPS = _trips("multi_day_trips")
    DAYTRIPS = _trips("day_trips")
    ACTIVITIES = _trips("activities")   # legend-only "ACTIVITIES" section (e.g. a ceremony, a boat ride)

    # faint neighborhood/district labels baked onto the map (e.g. VECRĪGA). Accepts a
    # list `district_labels`, or a single `district_label` for back-compat.
    dls = cfg.get("district_labels") or ([cfg["district_label"]] if cfg.get("district_label") else [])

    def make_variant(W, H, pad, title_px, pin_scale, pngname, show_num=True, svg_title=True, mobile=False):
        mx = [merc(la, lo) for _, la, lo, *_ in POIS]
        # A poi with "frame": false is dropped from the DESKTOP framing extent (a far outlier that
        # would force a zoom-out) — it's still drawn (clipped off the static desktop frame), still in
        # the legend, and still inside the wider MOBILE base so you can pan to it.
        fmx = [m for m, p in zip(mx, cfg["pois"]) if mobile or p.get("frame", True)] or mx  # never empty
        if cfg.get("center"):     # optional: frame around a chosen point (e.g. to clear a dense cluster off the key)
            cx, cy = merc(cfg["center"]["lat"], cfg["center"]["lon"])
        else:
            cx = (min(p[0] for p in fmx) + max(p[0] for p in fmx)) / 2
            cy = (min(p[1] for p in fmx) + max(p[1] for p in fmx)) / 2
        # half-extents keep every framed pin in frame; floored so a single/collinear pin set can't /0
        hx = max(abs(p[0] - cx) for p in fmx) * pad or 1e-9
        hy = max(abs(p[1] - cy) for p in fmx) * pad or 1e-9
        scale = min(W / (2 * hx), H / (2 * hy))

        # LEGEND-CLEAR RULE (desktop only): the floating key sits fixed top-right, so a pin
        # projected into that corner ends up hidden underneath it. If the config framing puts
        # any pin under the key, search nearby framings (bias the center up/right so pins slide
        # down/left, zooming out only as needed) and take the closest one that clears the key
        # AND keeps every pin in frame. If the current framing is already clear, nothing changes
        # (hand-tuned `center`/`pad` are respected).
        if not mobile and not os.environ.get("CM_NO_LEGEND_CLEAR"):
            rows = heads = 0
            for _cat in GRP_ORDER:
                if any(p[3] == _cat for p in POIS): heads += 1; rows += sum(p[3] == _cat for p in POIS)
            for _tr in (MULTITRIPS, DAYTRIPS, ACTIVITIES):
                if _tr: heads += 1; rows += len(_tr) + sum(len(t["name"]) > 24 for t in _tr)  # long names wrap
            Lw = 226 + 34                 # key width + right margin + a little slack
            Lh = 15 + 24 + heads * 17 + rows * 16 + 6
            Lx0 = W - Lw

            def _project(cx0, cy0, s0):
                return [(W / 2 + (p[0] - cx0) * s0, H / 2 - (p[1] - cy0) * s0) for p in fmx]

            def _under(pts):              # pins whose marker (x±9, y-30..y+2) pokes into the key box
                return sum(1 for X, Y in pts if X + 9 >= Lx0 and Y - 30 <= Lh)

            def _inframe(pts):
                return all(6 <= X <= W - 6 and 30 <= Y <= H - 6 for X, Y in pts)

            if _under(_project(cx, cy, scale)) > 0:
                orig = scale
                spanx = (max(p[0] for p in fmx) - min(p[0] for p in fmx)) or 1e-6
                spany = (max(p[1] for p in fmx) - min(p[1] for p in fmx)) or 1e-6
                best = None
                # small pans first, then progressively larger zoom-outs. Cost prefers the
                # framing closest to the original (least zoom-out, least shift) that clears the key.
                for padf in (1.0, 1.1, 1.22, 1.4, 1.6, 1.9, 2.3):
                    for ex in (0, .08, .16, .28, .42, .6):     # +lon -> pins slide LEFT (off the key)
                        for ey in (0, .08, .16, .28, .42):     # +lat -> pins slide DOWN (below the key)
                            ccx = cx + ex * spanx; ccy = cy + ey * spany
                            hxx = max(abs(p[0] - ccx) for p in fmx) * pad * padf
                            hyy = max(abs(p[1] - ccy) for p in fmx) * pad * padf
                            s0 = min(W / (2 * hxx), H / (2 * hyy))
                            pts = _project(ccx, ccy, s0)
                            if not _inframe(pts): continue
                            cost = _under(pts) * 1000 + (orig / s0 - 1) * 60 + ex * 6 + ey * 6
                            if best is None or cost < best[0]: best = (cost, ccx, ccy, s0)
                if best is not None:
                    _, cx, cy, scale = best

        def px(lat, lon):
            x, y = merc(lat, lon); return (W / 2 + (x - cx) * scale, H / 2 - (y - cy) * scale)
        def proj_str(g):
            pp = [px(p['lat'], p['lon']) for p in g]
            if not pp: return None
            xs = [a for a, b in pp]; ys = [b for a, b in pp]
            if max(xs) < -25 or min(xs) > W + 25 or max(ys) < -25 or min(ys) > H + 25: return None
            out = [pp[0]]
            for a, b in pp[1:]:
                if (a - out[-1][0]) ** 2 + (b - out[-1][1]) ** 2 >= 6.0: out.append((a, b))
            return " ".join(f"{a:.1f},{b:.1f}" for a, b in out) if len(out) >= 2 else None
        water_s = []
        for w in water:
            if isinstance(w, tuple):
                s = proj_str(w[1]); water_s.append(("line", s)) if s else None
            elif len(w) > 2:
                s = proj_str(w); water_s.append(("poly", s)) if s else None
        parks_s = [s for p in parks if len(p) > 2 and (s := proj_str(p))]
        roads_s = {cls: [s for g in roads[cls] if (s := proj_str(g))] for cls in ROADW}

        def basemap():
            o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" style="paint-order:stroke">']
            # sea_bg: archipelago/peninsula city (Helsinki). The open sea isn't a water
            # polygon, so fill the frame WATER and paint LAND from the coastline via
            # landmass_polys() — islands fill directly, the mainland closes along the
            # frame border (robust even when the fetched coastline runs far past the view).
            seabg = bool(cfg.get("sea_bg") and coast)
            if seabg:
                o.append(f'<rect width="{W}" height="{H}" fill="{WATER}"/>')
                pins_px = [px(p[1], p[2]) for p in POIS]
                for s in landmass_polys(coast, px, W, H, pins_px, flip=cfg.get("sea_flip", False)):
                    o.append(f'<polygon points="{s}" fill="{LAND}"/>')
            else:
                o.append(f'<rect width="{W}" height="{H}" fill="{LAND}"/>')
            sea = cfg.get("sea")   # coastal city: flood the sea side (west/east/north/south) up to the coastline
            if sea and coast:
                side = sea.get("side", "west")
                vertical = side in ("west", "east")
                axis = (lambda q: q[1]) if vertical else (lambda q: q[0])   # order along the run of the shore
                ways = []
                for g in coast:                       # keep each way's own point order (no cross-way sorting)
                    pts = [px(p['lat'], p['lon']) for p in g]
                    if len(pts) < 2: continue
                    if axis(pts[0]) > axis(pts[-1]): pts = pts[::-1]
                    ways.append(pts)
                ways.sort(key=lambda w: axis(w[0]))   # stack the ways in order along the shore
                line = [p for w in ways for p in w]
                thin = [line[0]]                       # light thinning to smooth the fill edge
                for a, b in line[1:]:
                    if (a - thin[-1][0]) ** 2 + (b - thin[-1][1]) ** 2 >= 6.0: thin.append((a, b))
                line = thin
                if vertical:                           # sea is the whole strip on one side of the shore line
                    edge = -300 if side == "west" else W + 300
                    poly = [(line[0][0], -300)] + line + [(line[-1][0], H + 300), (edge, H + 300), (edge, -300)]
                else:
                    edge = -300 if side == "north" else H + 300
                    poly = [(-300, line[0][1])] + line + [(W + 300, line[-1][1]), (W + 300, edge), (-300, edge)]
                o.append(f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in poly)}" fill="{WATER}"/>')
            for kind, s in water_s:
                o.append(f'<polyline points="{s}" fill="none" stroke="{WATER}" stroke-width="5" stroke-linecap="round"/>'
                         if kind == "line" else f'<polygon points="{s}" fill="{WATER}"/>')
            # ALWAYS paint the inner rings of water multipolygons back as LAND (holes = islands
            # like Stockholm's Kungsholmen, or Malmö's canal-ringed old town sitting inside the
            # harbour relation). SVG doesn't subtract a polygon's holes, so without this the outer
            # water ring floods the hole — and whether it shows depends on the frame (Malmö was fine
            # on desktop but flooded on the mobile crop). Not gated on sea_bg: a hole in water is land.
            for isl in water_islands:
                s = proj_str(isl)
                if s: o.append(f'<polygon points="{s}" fill="{LAND}"/>')
            for s in parks_s: o.append(f'<polygon points="{s}" fill="{PARK}"/>')
            for cls in MAJOR:
                for s in roads_s.get(cls, []):
                    o.append(f'<polyline points="{s}" fill="none" stroke="{CASING}" stroke-width="{ROADW[cls]*1.3+1.8:.1f}" stroke-linecap="round" stroke-linejoin="round"/>')
            # `"roads":"major"` draws only arterials (skip residential/minor) — declutters
            # dense mega-cities (e.g. Cairo) where the full street mesh reads as noise.
            major_only = cfg.get("roads") == "major"
            for cls, wdt in ROADW.items():
                if major_only and cls not in MAJOR: continue
                for s in roads_s[cls]:
                    o.append(f'<polyline points="{s}" fill="none" stroke="{TOP}" stroke-width="{wdt*1.3:.1f}" stroke-linecap="round" stroke-linejoin="round"/>')
            o.append('</svg>'); return "\n".join(o)

        png = asyncio.run(_raster(basemap(), W, H))
        open(f"{IMGDIR}/{pngname}.png", "wb").write(png)
        b64 = base64.b64encode(png).decode()
        ext_href = f"{up}Images/web/city-maps/{pngname}.png"

        # A neighborhood/district label names LAND, so NO part of it may sit in water. Sample the
        # rendered base raster across the label's actual glyph band (works for polygon water AND
        # sea_bg seas). Trigger on the faintest touch and nudge to the nearest ~all-land spot,
        # searching rings outward and taking the least-wet candidate at each radius (no directional
        # bias — moves whichever way reaches land; falls back to the least-wet spot if none is dry).
        im_wc = Image.open(BytesIO(png)).convert("RGB"); sc = im_wc.width / W
        WR, WG, WB = 0xB4, 0xD3, 0xD9   # WATER #B4D3D9
        def _wfrac(cx0, cy0, tw, th):
            hit = tot = 0
            for cf in (-.5, -.4, -.3, -.2, -.1, 0, .1, .2, .3, .4, .5):   # across the width incl. both ends
                for rf in (0.0, -0.35, -0.7):                            # baseline -> cap height
                    X = int((cx0 + tw * cf) * sc); Y = int((cy0 + th * rf) * sc)
                    if 0 <= X < im_wc.width and 0 <= Y < im_wc.height:
                        r, g, b = im_wc.getpixel((X, Y)); tot += 1
                        if abs(r - WR) < 26 and abs(g - WG) < 26 and abs(b - WB) < 26: hit += 1
            return hit / tot if tot else 0.0
        dls_adj = []
        for d in dls:
            lx, ly = px(d["lat"], d["lon"])
            tw = len(d["text"]) * 10.0 * pin_scale; th = 11 * pin_scale
            if _wfrac(lx, ly, tw, th) > 0.03:
                best, best_w = (lx, ly), _wfrac(lx, ly, tw, th)
                for R in range(6, 181, 6):
                    ring = [(dx, dy) for dx in range(-R, R + 1, 6) for dy in range(-R, R + 1, 6)
                            if max(abs(dx), abs(dy)) == R
                            and tw / 2 <= lx + dx <= W - tw / 2 and 12 <= ly + dy <= H - 12]
                    ring.sort(key=lambda o: o[0] * o[0] + o[1] * o[1])
                    for dx, dy in ring:
                        w = _wfrac(lx + dx, ly + dy, tw, th)
                        if w < best_w: best_w, best = w, (lx + dx, ly + dy)
                        if w <= 0.02: break          # nearest all-land spot at this radius
                    if best_w <= 0.02: break
                lx, ly = best
            dls_adj.append({"text": d["text"], "x": lx, "y": ly})

        def render(active=None, external=False):
            ps = pin_scale
            src = ext_href if external else f"data:image/png;base64,{b64}"
            # The base map is ALWAYS a plain <img> (browsers paint it reliably and HTML
            # editors keep it); the pins ride on a transparent SVG overlay. Desktop is
            # static; mobile wraps both in a .cmworld that JS pans/zooms via CSS transform
            # (so base + pins move together) — see the JS. This avoids the SVG <image>
            # paint bug that left the map blank / half-rendered at the edges.
            img = f'<img class="cmbase" src="{src}" width="{W}" height="{H}" alt="{html.escape(city)} map">'
            o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Montserrat,Arial,sans-serif" style="paint-order:stroke">']
            for d in dls_adj:  # faint neighborhood/district labels baked onto the map (e.g. VECRĪGA), nudged off water
                lx, ly = d["x"], d["y"]
                o.append(f'<text x="{lx:.0f}" y="{ly:.0f}" font-size="{11*ps:.0f}" letter-spacing="{2.5*ps:.1f}" '
                         f'fill="#9DAAA0" text-anchor="middle" stroke="{LAND}" stroke-width="3">{html.escape(d["text"])}</text>')
            for i in range(len(POIS), 0, -1):     # draw high->low so lower (earlier, more important) numbers sit on top
                name, la, lo, cat, key = POIS[i - 1]
                x, y = px(la, lo); fill = GRP[cat][1]; nt = GRP[cat][3]
                cls = "cmpin active" if active == i else "cmpin"
                mk = (f'<circle cx="0" cy="-17.7" r="6.4" fill="#fff"/><text x="0" y="-17.7" text-anchor="middle" dominant-baseline="central" font-size="8.6" font-weight="700" fill="{nt}">{i}</text>'
                      if show_num else '<circle cx="0" cy="-17.7" r="3.1" fill="#fff"/>')
                o.append(f'<a href="{html.escape(HREF[name])}" target="_blank" rel="noopener"><g class="{cls}" data-i="{i}" data-name="{html.escape(name)}">'
                         f'<g transform="translate({x:.1f} {y:.1f}) scale({ps})">'
                         f'<path d="{PIN}" transform="translate(0.7 1.5)" fill="#000" opacity="0.2"/>'
                         f'<path d="{PIN}" fill="{fill}" stroke="#fff" stroke-width="1.1"/>{mk}'
                         f'</g></g></a>')
            if svg_title:   # desktop draws the title in the SVG; mobile uses a fixed HTML overlay instead
                o.append(f'<text x="{title_px*0.68:.0f}" y="{title_px*0.58:.0f}" font-family="Montserrat,Arial" font-size="{title_px*0.27:.0f}" font-weight="600" letter-spacing="{title_px*0.12:.1f}" fill="{KICKER}" stroke="{LAND}" stroke-width="{title_px*0.08:.1f}" paint-order="stroke">{html.escape(kicker)}</text>')
                o.append(f'<text x="{title_px*0.62:.0f}" y="{title_px*1.66:.0f}" font-family="Fraunces,Georgia,serif" font-size="{title_px}" font-weight="600" fill="{INK}" stroke="{LAND}" stroke-width="{title_px*0.13:.1f}" paint-order="stroke">{html.escape(city)}</text>')
            o.append(f'<text x="{W-6}" y="{H-6}" font-size="8" fill="#8B978F" text-anchor="end">© OpenStreetMap · getawayguide</text>')
            o.append('</svg>'); return img + "".join(o)
        print(f"  {pngname}: {W}x{H}, raster {len(png)//1024}KB")
        return render

    d = cfg["desktop"]; m = cfg["mobile"]
    render_desk = make_variant(DESK_W, DESK_H, d["pad"], TITLE_PX, DESK_PIN, slug)
    render_mob = make_variant(MOB_W, MOB_H, m["pad"], TITLE_PX, MOB_PIN,
                              f"{slug}-mobile", show_num=False, svg_title=False, mobile=True)

    def legend_html(active=None):
        h = ['<div class="cmkey">']
        for cat in GRP_ORDER:
            label, fill, badge_text, _ = GRP[cat]
            items = [(i, p) for i, p in enumerate(POIS, 1) if p[3] == cat]
            if not items: continue
            h.append(f'<div class="cmhead">{html.escape(label)}</div>')
            for i, (name, la, lo, c, key) in items:
                ac = " active" if active == i else ""
                h.append(f'<a class="cmrow{ac}" data-i="{i}" href="{html.escape(HREF[name])}" target="_blank" rel="noopener">'
                         f'<span class="num" style="background:{fill};color:{badge_text}">{i}</span><span class="nm">{html.escape(name)}</span></a>')
        for hdr, trips, mark in [("MULTI-DAY TRIP", MULTITRIPS, "↗"), ("DAY TRIPS", DAYTRIPS, "↗"),
                                 ("ACTIVITIES", ACTIVITIES, "✦")]:  # legend-only sections
            if not trips: continue
            h.append(f'<div class="cmhead">{hdr}</div>')
            for dt in trips:
                tm = f'<span class="dt-time"> ({html.escape(dt["time"])})</span>' if dt["time"] else ""
                h.append(f'<a class="cmrow cmrow-dt" href="{html.escape(dt["href"])}" target="_blank" rel="noopener">'
                         f'<span class="num num-dt">{mark}</span><span class="nm">{html.escape(dt["name"])}{tm}</span></a>')
        h.append('</div>'); return "\n".join(h)

    hint = "<br>".join(html.escape(l) for l in cfg.get("hint", ["Touch to explore", "Double-tap to open in Google Maps"]))
    da = (f' data-dar="{MOB_DAR}" data-iw="{m.get("init_w",0.8)}" '
          f'data-ix="{m.get("init_x",0.5)}" data-iy="{m.get("init_y",0.5)}"')

    def maps_html(demo=None, external=False):
        dd = f' data-demo="{demo}"' if demo else ""
        return (f'<div class="citymap desk"><div class="cmmap">{render_desk(external=external)}</div>{legend_html()}</div>'
                f'<div class="citymap mob"{da}{dd}><div class="cmmap"><div class="cmworld">{render_mob(external=external)}</div></div>'
                f'<div class="cm-ov"><div class="cm-ov-kick">{html.escape(kicker)}</div><div class="cm-ov-title">{html.escape(city)}</div>'
                f'<div class="cm-ov-hint">{hint}</div></div></div>')

    frag = f'<style>\n{CSS}</style>\n<figure class="citymap-fig">{maps_html(external=True)}</figure>\n{JS}\n'
    open(f"{PREV}/{slug}-city-embed.html", "w", encoding="utf-8").write(frag)

    def page(demo=None, note=""):
        return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Montserrat:wght@400;500;600;700&display=swap" rel="stylesheet">'
                f'<style>body{{margin:0;background:#e9e9e3;padding:22px 16px}}{CSS}'
                f'.note{{max-width:760px;margin:0 auto 8px;font:600 13px Montserrat;color:#444}}</style>'
                f'<body>{f"<div class=note>{note}</div>" if note else ""}<figure class="citymap-fig">{maps_html(demo)}</figure>{JS}</body>')
    open(f"{PREV}/{slug}-city.html", "w", encoding="utf-8").write(page(note=f"{city} · desktop=landscape+key · mobile=portrait gesture map"))
    if demo:
        open(f"{PREV}/{slug}-city-demo.html", "w", encoding="utf-8").write(page(demo=demo, note="mobile demo — tap a pin for its name, double-tap to open"))
    print(f"wrote {slug}-city-embed.html + {slug}-city.html" + (" + demo" if demo else ""))

    if embed:
        e = cfg["embed"]; p = f"{ROOT}/{cfg['article']}"
        s = open(p, encoding="utf-8").read()
        # Validate the anchors before writing — a missing/misordered anchor otherwise silently
        # mangles the article (post is searched AFTER pre so the map region is always pre..post).
        if e["pre"] not in s:
            raise SystemExit(f"embed aborted: pre-anchor not found in {cfg['article']}\n  {e['pre']!r}")
        a = s.index(e["pre"]) + len(e["pre"])
        if e["post"] not in s[a:]:
            raise SystemExit(f"embed aborted: post-anchor not found after the pre-anchor in {cfg['article']}\n  {e['post']!r}")
        b = s.index(e["post"], a)
        open(p, "w", encoding="utf-8", newline="").write(s[:a] + frag + s[b:])
        print(f"embedded into {cfg['article']}")
    return frag


# ---- static CSS + JS (shared by every city; behaviour is data-attribute driven) ----
CSS = """
*{box-sizing:border-box}
.citymap-fig{margin:2.5rem auto 1.5rem;max-width:760px}
.citymap{position:relative;margin:0 auto;font-family:Montserrat,Arial,sans-serif}
.citymap.desk{max-width:760px}.citymap.mob{max-width:540px;display:none}
.cmmap{position:relative;border-radius:7px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.13)}
.cmmap svg{display:block;width:100%;height:auto}
.cmbase{display:block;width:100%;height:auto}          /* base map = plain img (reliably painted, editor-safe) */
.cmbase+svg{position:absolute;top:0;left:0}            /* pins overlay sits on top of the img */
/* mobile gesture map: a portrait window; .cmworld (img + pins) is panned/zoomed by JS via CSS transform.
   touch-action:pan-y lets a straight vertical swipe scroll the ARTICLE (so the map never traps the page)
   while horizontal drags + all pinches are captured by the map. */
.citymap.mob .cmmap{aspect-ratio:0.72;overflow:hidden;background:#F4F2EC;touch-action:pan-y;
  -webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent}
.cmworld{position:absolute;top:0;left:0;width:660px;transform-origin:0 0;will-change:transform}
.cmworld img,.cmworld svg{pointer-events:none}      /* gestures are handled on the viewport */
.cmworld .cmpin{pointer-events:auto}
.cmzoom{position:absolute;right:9px;bottom:9px;display:flex;flex-direction:column;gap:5px;z-index:5}
.cmzoom button{width:33px;height:33px;padding:0;border:0;border-radius:8px;background:rgba(255,255,255,.93);
  box-shadow:0 2px 6px rgba(0,0,0,.22);color:#1C2821;font:600 19px/1 Montserrat,Arial;cursor:pointer;
  display:flex;align-items:center;justify-content:center;-webkit-tap-highlight-color:transparent}
.cmzoom button:active{background:#fff}
.cmzoom button[disabled]{opacity:.35}
.cmpin{transition:transform .16s ease;cursor:pointer}
.cmpin.active{transform:translateY(-6px)}
.cmkey{position:absolute;top:15px;right:15px;width:226px;background:rgba(255,255,255,.82);
  backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);border-radius:9px;box-shadow:0 3px 8px rgba(0,0,0,.16);padding:11px 12px}
.cmhead{font-size:9.5px;font-weight:700;letter-spacing:1.6px;color:#1C2821;height:16px;display:flex;align-items:flex-end;padding-bottom:1px}
.cmrow{display:flex;align-items:center;gap:8px;min-height:16px;padding:1px 5px;margin:0 -5px;border-radius:4px;text-decoration:none;transition:background .14s}
.cmrow.active{background:rgba(28,40,33,.06)}
.num{flex:0 0 15px;width:15px;height:15px;border-radius:50%;font-size:8.5px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;align-self:flex-start;margin-top:1px}
.nm{font-size:11px;font-weight:500;color:#1C2821;line-height:1.18;white-space:normal}
.num-dt{background:none;color:#8B978F;font-size:13px;font-weight:600;line-height:1}
.dt-time{color:#8B978F;font-weight:500}
.cmrow-dt .nm{color:#586159}
.cmbubble{position:absolute;display:none;z-index:6;background:#1C2821;color:#fff;font:600 13px/1.3 Montserrat,Arial;padding:8px 13px;border-radius:8px;max-width:170px;white-space:normal;text-align:center;box-shadow:0 4px 12px rgba(0,0,0,.28);pointer-events:auto}
.cmbubble:after{content:"";position:absolute;left:var(--arrow-left,50%);bottom:-6px;transform:translateX(-50%);border:6px solid transparent;border-top-color:#1C2821;border-bottom:0}
/* mobile fixed title/instructions overlay (map pans underneath) */
.cm-ov{position:absolute;top:28px;left:18px;pointer-events:none}
.cm-ov-kick{font:600 10px/1 Montserrat,Arial;letter-spacing:3px;color:#245A43;margin-bottom:3px;text-shadow:-1.5px 0 0 #F4F2EC,1.5px 0 0 #F4F2EC,0 -1.5px 0 #F4F2EC,0 1.5px 0 #F4F2EC,-1px -1px 0 #F4F2EC,1px 1px 0 #F4F2EC,-1px 1px 0 #F4F2EC,1px -1px 0 #F4F2EC,0 0 4px #F4F2EC}
.cm-ov-title{font-family:Fraunces,Georgia,serif;font-weight:600;font-size:40px;color:#1C2821;line-height:1;text-shadow:0 0 5px #F4F2EC,0 0 5px #F4F2EC}
.cm-ov-hint{margin-top:4px;font:italic 500 11px/1.55 Montserrat,Arial;color:#586159;max-width:300px;text-shadow:0 0 5px #F4F2EC,0 0 5px #F4F2EC}
@media (max-width:700px){ .citymap.desk{display:none} .citymap.mob{display:block} }
"""
JS = """<script>
(function(){
function setup(cm){
 var svg=cm.querySelector('.cmmap svg');
 function all(i){return cm.querySelectorAll('[data-i="'+i+'"]');}
 cm.querySelectorAll('.cmpin,.cmrow').forEach(function(el){var i=el.getAttribute('data-i');
   el.addEventListener('mouseenter',function(){all(i).forEach(function(e){e.classList.add('active')})});
   el.addEventListener('mouseleave',function(){all(i).forEach(function(e){e.classList.remove('active')})});});
 if(!cm.classList.contains('mob')) return;      // desktop variant is static (landscape + key)
 var world=cm.querySelector('.cmworld'), vp=cm.querySelector('.cmmap');
 var vb=svg.viewBox.baseVal, W=vb.width, H=vb.height;
 // ---- view state: scale (px per base unit) + translation (px). Google-Maps style:
 // minS shows the WHOLE base (zoom-out is never blocked), maxS is 7x that.
 var s=1, tx=0, ty=0, minS=1, maxS=1, vw=0, vh=0;
 function measure(){vw=vp.clientWidth; vh=vp.clientHeight;
   if(!vw||!vh) return false;
   minS=Math.min(vw/W, vh/H); maxS=minS*7; return true;}
 function clampT(){var cw=W*s, ch=H*s;          // keep content in view; center it on an axis it underfills
   tx = cw<=vw ? (vw-cw)/2 : Math.min(0, Math.max(vw-cw, tx));
   ty = ch<=vh ? (vh-ch)/2 : Math.min(0, Math.max(vh-ch, ty));}
 function apply(){world.style.transform='translate('+tx.toFixed(2)+'px,'+ty.toFixed(2)+'px) scale('+s.toFixed(5)+')';
   if(zin){zin.disabled = s>=maxS-1e-6; zout.disabled = s<=minS+1e-6;}}
 function zoomAt(ns,px,py){                     // zoom keeping the (px,py) viewport point pinned
   ns=Math.max(minS,Math.min(maxS,ns));
   var ax=(px-tx)/s, ay=(py-ty)/s;
   s=ns; tx=px-ax*s; ty=py-ay*s; clampT(); apply();}
 function rel(t){var r=vp.getBoundingClientRect(); return {x:t.clientX-r.left, y:t.clientY-r.top};}
 // ---- name bubble (tap a pin to name it, tap it again to open Google Maps) ----
 cm.querySelectorAll('.cmpin').forEach(function(g){var a=g.closest('a');if(a)a.addEventListener('click',function(e){e.preventDefault();});});
 var bub=document.createElement('div'); bub.className='cmbubble'; cm.appendChild(bub);
 var armed=null, armedHref=null;
 function clearAct(){cm.querySelectorAll('.cmpin.active').forEach(function(e){e.classList.remove('active')});}
 function hideBub(){bub.style.display='none';armed=null;armedHref=null;clearAct();}
 function showBub(g){var i=g.getAttribute('data-i'),a=g.closest('a');armedHref=a?a.getAttribute('href'):null;armed=i;
   bub.textContent=g.getAttribute('data-name')||'';bub.style.display='block';
   var cr=cm.getBoundingClientRect(),pr=g.getBoundingClientRect();
   var left=pr.left+pr.width/2-cr.left-bub.offsetWidth/2, top=pr.top-cr.top-bub.offsetHeight-9;
   bub.style.left=Math.max(6,Math.min(cr.width-bub.offsetWidth-6,left))+'px';
   bub.style.top=Math.max(4,top)+'px';
   var arx=(pr.left+pr.width/2-cr.left)-parseFloat(bub.style.left);   // arrow points at the pin
   bub.style.setProperty('--arrow-left',Math.max(12,Math.min(bub.offsetWidth-12,arx))+'px');
   clearAct(); g.classList.add('active');}
 // Tapping the bubble opens the link. Mouse only for 'click' — after a touch the browser
 // replays a compatibility click whose target can be the just-repositioned bubble, which
 // would open a link the user never asked for. Touch is handled explicitly instead.
 bub.addEventListener('click',function(){if(fromTouch())return;if(armedHref)window.open(armedHref,'_blank');});
 bub.addEventListener('touchend',function(e){e.stopPropagation();e.preventDefault();lastTouch=Date.now();
   if(armedHref)window.open(armedHref,'_blank');},{passive:false});
 function pinAt(cx,cy){var n=document.elementFromPoint(cx,cy); return n&&n.closest?n.closest('.cmpin'):null;}
 // ---- momentum ----
 var vX=0,vY=0,raf=null;
 function stopGlide(){if(raf){cancelAnimationFrame(raf);raf=null;}}
 function glide(){stopGlide();
   if(Math.abs(vX)<0.4&&Math.abs(vY)<0.4) return;
   var step=function(){vX*=0.94; vY*=0.94;
     if(Math.abs(vX)<0.25&&Math.abs(vY)<0.25){raf=null;return;}
     var px=tx,py=ty; tx+=vX; ty+=vY; clampT(); apply();
     if(tx===px) vX=0;                          // hit an edge: kill that axis
     if(ty===py) vY=0;
     raf=requestAnimationFrame(step);};
   raf=requestAnimationFrame(step);}
 // ---- gestures: 1 finger = pan (captured once it reads as a map drag), 2 fingers = pinch+pan ----
 var anchor=null,startD=0,startS=1,panP=null,cap=false,moved=0,lastT=0,tapT=0,tapX=0,tapY=0;
 vp.addEventListener('touchstart',function(e){
   stopGlide(); lastTouch=Date.now();
   if(e.touches.length>=2){
     var a=rel(e.touches[0]),b=rel(e.touches[1]);
     startD=Math.hypot(a.x-b.x,a.y-b.y)||1; startS=s;
     var c={x:(a.x+b.x)/2,y:(a.y+b.y)/2};
     anchor={x:(c.x-tx)/s, y:(c.y-ty)/s};       // content point under the pinch midpoint
     cap=true; e.preventDefault();
   }else if(e.touches.length===1){
     anchor=null; panP=rel(e.touches[0]); cap=false; moved=0; vX=vY=0; lastT=e.timeStamp||Date.now();
   }
 },{passive:false});
 vp.addEventListener('touchmove',function(e){
   if(e.touches.length>=2&&anchor){             // pinch: scale about the midpoint, and follow its drift
     var a=rel(e.touches[0]),b=rel(e.touches[1]);
     var d=Math.hypot(a.x-b.x,a.y-b.y)||1, c={x:(a.x+b.x)/2,y:(a.y+b.y)/2};
     s=Math.max(minS,Math.min(maxS,startS*d/startD));
     tx=c.x-anchor.x*s; ty=c.y-anchor.y*s; clampT(); apply(); hideBub();
     e.preventDefault(); return;
   }
   if(e.touches.length!==1||!panP) return;
   var p=rel(e.touches[0]), dx=p.x-panP.x, dy=p.y-panP.y;
   moved+=Math.hypot(dx,dy);
   if(!cap){                                    // decide once: horizontal-ish drag -> the map takes it,
     if(moved<6) return;                        // straight vertical swipe -> let the article scroll
     if(Math.abs(dx)<=Math.abs(dy)){panP=null;return;}
     cap=true;
   }
   var now=e.timeStamp||Date.now(), dt=Math.max(8,now-lastT); lastT=now;
   vX=dx/dt*16; vY=dy/dt*16;
   tx+=dx; ty+=dy; panP=p; clampT(); apply();
   if(moved>8) hideBub();
   e.preventDefault();
 },{passive:false});
 vp.addEventListener('touchend',function(e){
   lastTouch=Date.now();
   if(e.touches.length>0) return;
   if(cap&&moved>10){glide();}
   else if(moved<10){                           // a tap: pin -> name / open, empty map -> double-tap zooms
     var c=e.changedTouches[0], g=pinAt(c.clientX,c.clientY), now=Date.now();
     if(g){var i=g.getAttribute('data-i');
       if(armed===i){if(armedHref)window.open(armedHref,'_blank');} else showBub(g);}
     else{
       var p=rel(c);
       if(now-tapT<300&&Math.hypot(p.x-tapX,p.y-tapY)<32){zoomAt(s*1.9,p.x,p.y);tapT=0;}
       else{hideBub();tapT=now;tapX=p.x;tapY=p.y;}
     }
   }
   panP=null; anchor=null; cap=false;
 });
 // ---- mouse / trackpad (narrow desktop windows show this variant too) ----
 // A touch also fires COMPATIBILITY mouse events (mousedown/mouseup) right after touchend.
 // Without this guard the mouseup re-ran the pin logic on the pin touchend had just armed,
 // so a single tap opened Google Maps immediately. Ignore mouse input just after a touch.
 var lastTouch=0; function fromTouch(){return Date.now()-lastTouch<700;}
 var md=false,mp=null;
 vp.addEventListener('mousedown',function(e){if(fromTouch())return;stopGlide();md=true;mp={x:e.clientX,y:e.clientY};moved=0;e.preventDefault();});
 window.addEventListener('mousemove',function(e){if(!md||fromTouch())return;
   var dx=e.clientX-mp.x, dy=e.clientY-mp.y; moved+=Math.hypot(dx,dy);
   tx+=dx; ty+=dy; mp={x:e.clientX,y:e.clientY}; clampT(); apply(); if(moved>8)hideBub();});
 window.addEventListener('mouseup',function(e){
   if(fromTouch()){md=false;return;}
   if(md&&moved<6){var g=pinAt(e.clientX,e.clientY);
     if(g){var i=g.getAttribute('data-i'); if(armed===i){if(armedHref)window.open(armedHref,'_blank');} else showBub(g);}
     else hideBub();}
   md=false;});
 vp.addEventListener('wheel',function(e){e.preventDefault();stopGlide();
   var p=rel(e); zoomAt(s*Math.pow(1.0016,-e.deltaY), p.x, p.y);},{passive:false});
 // ---- zoom buttons (discoverable, one-handed) ----
 var zc=document.createElement('div'); zc.className='cmzoom';
 var zin=document.createElement('button'); zin.type='button'; zin.textContent='+'; zin.setAttribute('aria-label','Zoom in');
 var zout=document.createElement('button'); zout.type='button'; zout.textContent='\\u2212'; zout.setAttribute('aria-label','Zoom out');
 zc.appendChild(zin); zc.appendChild(zout); cm.appendChild(zc);
 function zbtn(f){return function(e){e.preventDefault();e.stopPropagation();stopGlide();zoomAt(s*f,vw/2,vh/2);};}
 zin.addEventListener('click',zbtn(1.7)); zout.addEventListener('click',zbtn(1/1.7));
 // ---- init: open framed on the cluster (data-iw/ix/iy), then free to pan/zoom anywhere ----
 function init(){
   if(!measure()) return false;
   var iw=parseFloat(cm.getAttribute('data-iw')||0.8);
   var ix=parseFloat(cm.getAttribute('data-ix')||0.5), iy=parseFloat(cm.getAttribute('data-iy')||0.5);
   s=Math.max(minS,Math.min(maxS,vw/(iw*W)));
   tx=-Math.max(0,W-vw/s)*ix*s; ty=-Math.max(0,H-vh/s)*iy*s;
   clampT(); apply(); return true;}
 if(!init()){var t=setInterval(function(){if(init())clearInterval(t);},120);}   // hidden until the mq matches
 var rt=null;
 window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(function(){
   var ax=(vw/2-tx)/s, ay=(vh/2-ty)/s;         // keep the centred content point put
   if(!measure())return;
   s=Math.max(minS,Math.min(maxS,s)); tx=vw/2-ax*s; ty=vh/2-ay*s; clampT(); apply();},120);});
 var dm=cm.getAttribute('data-demo');
 if(dm){setTimeout(function(){var g=cm.querySelector('.cmpin[data-i="'+dm+'"]');if(g)showBub(g);},220);}
}
document.querySelectorAll('.citymap').forEach(setup);
})();
</script>"""


def fetch_bbox(cfg_path, margin=1.7):
    """Generous Overpass fetch bbox (S,W,N,E) for a config. Covers the frame of BOTH
    variants (desktop landscape + mobile portrait) at their current pad, then expands
    by `margin` so there's street/water data well beyond the edges — the map stays
    filled when you zoom out or nudge pad/center later, no blank land off the edge."""
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    mxs = [merc(p["lat"], p["lon"]) for p in cfg["pois"]]
    if cfg.get("center"):
        cx, cy = merc(cfg["center"]["lat"], cfg["center"]["lon"])
    else:
        cx = (min(p[0] for p in mxs) + max(p[0] for p in mxs)) / 2
        cy = (min(p[1] for p in mxs) + max(p[1] for p in mxs)) / 2
    HW = HH = 0.0
    for W, H, pad in [(DESK_W, DESK_H, cfg["desktop"]["pad"]), (MOB_W, MOB_H, cfg["mobile"]["pad"])]:
        hx = max(abs(p[0] - cx) for p in mxs) * pad or 1e-9
        hy = max(abs(p[1] - cy) for p in mxs) * pad or 1e-9
        scale = min(W / (2 * hx), H / (2 * hy))
        HW = max(HW, W / (2 * scale)); HH = max(HH, H / (2 * scale))
    HW *= margin; HH *= margin
    lat_of = lambda y: math.degrees(2 * (math.atan(math.exp(y)) - math.pi / 4))
    return (lat_of(cy - HH), math.degrees(cx - HW), lat_of(cy + HH), math.degrees(cx + HW))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build a two-variant city POI map from a JSON config.")
    ap.add_argument("config", help="path to the city-map config json (e.g. tools/city_maps/riga.json)")
    ap.add_argument("--embed", action="store_true", help="embed the map into cfg['article'] between its markers")
    ap.add_argument("--demo", type=int, default=None, help="preview auto-opens this pin's name bubble")
    ap.add_argument("--bbox", action="store_true", help="print a generous Overpass fetch bbox (S,W,N,E) and exit")
    a = ap.parse_args()
    if a.bbox:
        print("%.4f,%.4f,%.4f,%.4f" % fetch_bbox(a.config))
    else:
        build(a.config, embed=a.embed, demo=a.demo)
