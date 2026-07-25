"""
Build a stylised itinerary map (numbered stops, route, transport + day
annotations) from a small JSON config.

    py tools/itinerary_map.py tools/itinerary_maps/el-salvador.json
    py tools/itinerary_map.py tools/itinerary_maps/el-salvador.json --shot

Pins are placed at real lat/lon using the SAME equirectangular projection as
tools/country_outline.py, so they land geographically accurately on an outline
generated from Natural Earth data (10m if available, else 50m). The coastline
is optionally smoothed with a Catmull-Rom spline for a clean, organic look.

Outputs (into .tmp/previews/):
    <slug>-map.svg     the standalone SVG (embed this in an article)
    <slug>-map.html    a preview wrapper (open in a browser)
    <slug>-map.png     rendered PNG (only with --shot; needs playwright)

CONFIG SCHEMA (see tools/itinerary_maps/el-salvador.json for a full example):
    iso3            ISO-3 country code, e.g. "SLV"
    slug            output filename stem
    country_width   px width of the country outline (default 780)
    margins         {l,r,t,b} px around the country for labels
    simplify        outline point target (default 150); smooth (bool)
    palette         optional colour overrides (bg/land/pin/ink/route/muted/chip)
    stops[]         {n,name,lat,lon,days,anchor,dx,dy}  (anchor label offsets)
    dots[]          {name,lat,lon,anchor,dx,dy}  small un-numbered markers
    airport         {name,lat,lon,anchor,dx,dy}  optional ✈ marker
    legs[]          {a,b,label,dash,chip_off,chip_side}  a/b index stops or "air"

Label dx/dy are hand-tuned per map (good placement depends on geography); use
--shot to iterate quickly.
"""
import json, math, os, sys, io, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import country_outline as co

DATA_10M = os.path.join(ROOT, ".tmp", "ne_10m_countries.geojson")
URL_10M = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
           "master/geojson/ne_10m_admin_0_countries.geojson")

DEFAULT_PALETTE = {
    "bg":    "#F7F7F3",   # --cream
    "land":  "#C9E0D2",   # light sage tint of the brand green
    "pin":   "#2D6B50",   # --terra (brand green)
    "ink":   "#1C2821",   # --ink
    "route": "#4F7A64",   # muted green
    "muted": "#7C9488",   # green-grey
    "chip":  "#D8E5DC",   # chip border
}


def load_geojson():
    """Prefer the higher-detail 10m dataset; download once, else fall back."""
    if os.path.exists(DATA_10M):
        return json.load(io.open(DATA_10M, encoding="utf-8"))
    try:
        print("Downloading Natural Earth 10m dataset (one-time, ~13 MB)...", file=sys.stderr)
        os.makedirs(os.path.dirname(DATA_10M), exist_ok=True)
        subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", "120",
                        URL_10M, "-o", DATA_10M], check=True)
        return json.load(io.open(DATA_10M, encoding="utf-8"))
    except Exception as e:
        print(f"  10m unavailable ({e}); using 50m fallback", file=sys.stderr)
        return co.load_dataset()


def build(cfg):
    P = dict(DEFAULT_PALETTE, **cfg.get("palette", {}))
    country_w = cfg.get("country_width", 780)
    m = cfg.get("margins", {})
    ML, MR, MT, MB = m.get("l", 250), m.get("r", 250), m.get("t", 130), m.get("b", 140)
    simplify = cfg.get("simplify", 150)
    smooth = cfg.get("smooth", True)

    gj = load_geojson()
    feat = co.find_feature(gj, cfg["iso3"])
    rings = co.extract_rings(feat)
    areas = [co.ring_area(r) for r in rings]
    big = max(areas)
    rings = [r for r, a in zip(rings, areas) if a >= big * co.AREA_KEEP_RATIO]

    lats = [pt[1] for r in rings for pt in r]
    K = math.cos(math.radians((max(lats) + min(lats)) / 2.0))
    pr = [[(pt[0] * K, -pt[1]) for pt in r] for r in rings]
    pr = co.simplify_rings(pr, simplify)

    xs = [p[0] for r in pr for p in r]; ys = [p[1] for r in pr for p in r]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    PAD = co.PAD
    scale = (country_w - 2 * PAD) / (maxx - minx)
    country_h = round((maxy - miny) * scale + 2 * PAD)
    CW, CH = ML + country_w + MR, MT + country_h + MB

    def T(lat, lon):
        return ((lon * K - minx) * scale + PAD + ML,
                (-lat - miny) * scale + PAD + MT)

    def to_canvas(r):
        return [((x - minx) * scale + PAD + ML, (y - miny) * scale + PAD + MT) for x, y in r]

    def densify(pts, max_len):
        """Insert collinear midpoints on long edges so Catmull-Rom keeps
        straight (political) borders straight instead of bowing them."""
        out = []
        n = len(pts)
        for i in range(n):
            a = pts[i]; b = pts[(i + 1) % n]
            out.append(a)
            dist = math.hypot(b[0] - a[0], b[1] - a[1])
            if dist > max_len:
                k = int(dist // max_len)
                for j in range(1, k + 1):
                    t = j / (k + 1)
                    out.append((a[0] + (b[0]-a[0]) * t, a[1] + (b[1]-a[1]) * t))
        return out

    def smooth_closed(pts):
        n = len(pts)
        d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f} "
        for i in range(n):
            p0, p1, p2, p3 = pts[(i-1) % n], pts[i], pts[(i+1) % n], pts[(i+2) % n]
            c1x = p1[0] + (p2[0]-p0[0])/6; c1y = p1[1] + (p2[1]-p0[1])/6
            c2x = p2[0] - (p3[0]-p1[0])/6; c2y = p2[1] - (p3[1]-p1[1])/6
            d += f"C {c1x:.1f} {c1y:.1f} {c2x:.1f} {c2y:.1f} {p2[0]:.1f} {p2[1]:.1f} "
        return d + "Z"

    seg = cfg.get("max_seg", 26)

    def path_d(rings):
        out = []
        for r in rings:
            pts = r[:-1] if r[0] == r[-1] else r
            cv = to_canvas(pts)
            if smooth:
                out.append(smooth_closed(densify(cv, seg)))
            else:
                out.append("M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in cv) + " Z")
        return " ".join(out)

    def pin(x, y, num):
        body = ('M0 0 C-3.6 -8.4 -8.4 -12 -8.4 -18.6 '
                'A8.4 8.4 0 1 1 8.4 -18.6 C8.4 -12 3.6 -8.4 0 0 Z')
        return (f'<g transform="translate({x:.1f} {y:.1f})">'
                f'<path d="{body}" fill="{P["pin"]}"/>'
                f'<circle cx="0" cy="-18.6" r="6.2" fill="{P["bg"]}"/>'
                f'<text x="0" y="-18.6" text-anchor="middle" dominant-baseline="central" '
                f'font-weight="600" font-size="9" fill="{P["pin"]}">{num}</text></g>')

    # ---- content bbox tracking, so we can auto-crop the viewBox tight ----
    BB = [1e9, 1e9, -1e9, -1e9]
    def ex(x, y):
        BB[0] = min(BB[0], x); BB[1] = min(BB[1], y)
        BB[2] = max(BB[2], x); BB[3] = max(BB[3], y)
    def ex_text(x, y, s, fs, anchor, ls=0.0):
        w = len(s) * fs * 0.62 + max(0, len(s) - 1) * ls
        if anchor == "middle":  x0, x1 = x - w / 2, x + w / 2
        elif anchor == "end":   x0, x1 = x - w, x
        else:                   x0, x1 = x, x + w
        ex(x0, y - fs * 0.85); ex(x1, y + fs * 0.25)

    B = [f'<path d="{path_d(pr)}" fill="{P["land"]}"/>']
    for r in pr:
        for x, y in to_canvas(r[:-1] if r[0] == r[-1] else r):
            ex(x, y)

    stops = cfg["stops"]
    airport = cfg.get("airport")

    def leg_pt(key):
        if key == "air":
            return T(airport["lat"], airport["lon"])
        return T(stops[key]["lat"], stops[key]["lon"])

    for leg in cfg.get("legs", []):
        x1, y1 = leg_pt(leg["a"]); x2, y2 = leg_pt(leg["b"])
        dash = ' stroke-dasharray="2 5"' if leg.get("dash") else ""
        B.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                 f'stroke="{P["route"]}" stroke-width="1.6"{dash} stroke-linecap="round"/>')
        lab = (leg.get("label") or "").upper()
        if not lab:
            continue  # connector line only, no transport pill
        mx, my = (x1+x2)/2, (y1+y2)/2
        dx, dy = x2-x1, y2-y1
        L = math.hypot(dx, dy) or 1
        off = leg.get("chip_off", 14 if L > 90 else 34)
        sgn = leg.get("chip_side", 1)
        px, py = -dy/L*off*sgn, dx/L*off*sgn
        wt = 700 if leg.get("bold") else 500
        w = len(lab) * (5.9 if leg.get("bold") else 5.6) + 14
        B.append(f'<g transform="translate({mx+px:.1f} {my+py:.1f})">'
                 f'<rect x="{-w/2:.1f}" y="-9" width="{w:.1f}" height="18" rx="9" '
                 f'fill="{P["bg"]}" stroke="{P["chip"]}" stroke-width="1"/>'
                 f'<text x="0" y="1" text-anchor="middle" dominant-baseline="central" '
                 f'font-size="8" font-weight="{wt}" letter-spacing="1.2" '
                 f'fill="{P["muted"]}">{lab}</text></g>')
        ex(mx+px-w/2, my+py-9); ex(mx+px+w/2, my+py+9)

    if airport:
        ax, ay = T(airport["lat"], airport["lon"])
        adx, ady, aanc = airport.get("dx", 14), airport.get("dy", 20), airport.get("anchor", "start")
        B.append(f'<g transform="translate({ax:.1f} {ay:.1f})">'
                 f'<circle r="3" fill="{P["muted"]}"/>'
                 f'<text x="0" y="-7" text-anchor="middle" font-size="13" fill="{P["muted"]}">✈</text>'
                 f'<text x="{adx}" y="{ady}" text-anchor="{aanc}" font-size="9" '
                 f'font-weight="500" letter-spacing="1" fill="{P["muted"]}">'
                 f'{airport["name"].upper()}</text></g>')
        ex(ax, ay - 20)
        ex_text(ax + adx, ay + ady, airport["name"].upper(), 9, aanc, 1)

    for dot in cfg.get("dots", []):
        x, y = T(dot["lat"], dot["lon"])
        ddx, ddy, danc = dot.get("dx", 12), dot.get("dy", 4), dot.get("anchor", "start")
        B.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{P["pin"]}" fill-opacity="0.55"/>')
        B.append(f'<text x="{x+ddx:.1f}" y="{y+ddy:.1f}" text-anchor="{danc}" '
                 f'font-size="10.5" font-style="italic" fill="{P["muted"]}">{dot["name"]}</text>')
        ex_text(x + ddx, y + ddy, dot["name"], 10.5, danc, 0)

    for s in stops:
        x, y = T(s["lat"], s["lon"])
        B.append(pin(x, y, s["n"]))
        ex(x - 9, y - 28); ex(x + 9, y + 2)
        tx, ty = x + s.get("dx", 16), y + s.get("dy", 4)
        anc = s.get("anchor", "start")
        name = f'{s["n"]}. {s["name"].upper()}'
        B.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anc}" font-size="14" '
                 f'font-weight="600" letter-spacing="1.5" fill="{P["ink"]}">{name}</text>')
        ex_text(tx, ty, name, 14, anc, 1.5)
        if s.get("days"):
            B.append(f'<text x="{tx:.1f}" y="{ty+15:.1f}" text-anchor="{anc}" font-size="10" '
                     f'font-weight="500" letter-spacing="1.5" fill="{P["pin"]}">'
                     f'{s["days"].upper()}</text>')
            ex_text(tx, ty + 15, s["days"].upper(), 10, anc, 1.5)

    pad = cfg.get("crop_pad", 26)
    vx, vy = BB[0] - pad, BB[1] - pad
    vw, vh = (BB[2] - BB[0]) + 2 * pad, (BB[3] - BB[1]) + 2 * pad
    head = (f'<svg viewBox="{vx:.0f} {vy:.0f} {vw:.0f} {vh:.0f}" '
            f'xmlns="http://www.w3.org/2000/svg" font-family="Montserrat,sans-serif">')
    bg = f'<rect x="{vx:.0f}" y="{vy:.0f}" width="{vw:.0f}" height="{vh:.0f}" fill="{P["bg"]}"/>'
    return "\n".join([head, bg] + B + ["</svg>"]), round(vw), round(vh)


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cfg_path = sys.argv[1]
    shot = "--shot" in sys.argv
    cfg = json.load(io.open(cfg_path, encoding="utf-8"))
    slug = cfg.get("slug", os.path.splitext(os.path.basename(cfg_path))[0])

    svg, CW, CH = build(cfg)
    outdir = os.path.join(ROOT, ".tmp", "previews")
    os.makedirs(outdir, exist_ok=True)
    svg_path = os.path.join(outdir, f"{slug}-map.svg")
    html_path = os.path.join(outdir, f"{slug}-map.html")
    io.open(svg_path, "w", encoding="utf-8").write(svg)
    html = (f'<!doctype html><meta charset="utf-8">'
            f'<link rel="preconnect" href="https://fonts.googleapis.com">'
            f'<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap" rel="stylesheet">'
            f'<style>body{{margin:0;background:#e9e4da;display:flex;justify-content:center;'
            f'padding:24px;font-family:Montserrat,sans-serif}}'
            f'.wrap{{width:100%;max-width:1180px;box-shadow:0 8px 40px rgba(0,0,0,.1);'
            f'border-radius:6px;overflow:hidden}}svg{{width:100%;height:auto;display:block}}</style>'
            f'<div class="wrap">{svg}</div>')
    io.open(html_path, "w", encoding="utf-8").write(html)
    print("wrote", svg_path)
    print("wrote", html_path, f"({CW}x{CH})")

    if shot:
        from playwright.sync_api import sync_playwright
        import pathlib
        png = os.path.join(outdir, f"{slug}-map.png")
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1220, "height": max(400, int(1180 * CH / CW) + 60)},
                            device_scale_factor=2)
            pg.goto(pathlib.Path(html_path).resolve().as_uri())
            pg.wait_for_timeout(600)
            pg.screenshot(path=png)
            b.close()
        print("wrote", png)


if __name__ == "__main__":
    main()
