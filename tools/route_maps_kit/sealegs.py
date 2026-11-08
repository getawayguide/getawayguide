"""Check every leg with "sea": true stays on water: samples the exact curve the builder draws (Catmull-Rom through
via points is affine-invariant, so it is computed straight in lon/lat) against the full NE 10m rings.
usage: sealegs.py <config.json> [ISO,ISO]"""
import json, sys, os
ROOT = r"C:\Users\kevin\Projects\Travel Blog"
sys.path.insert(0, os.path.join(ROOT, "tools"))
import itinerary_map as im, country_outline as co
cfg = json.load(open(os.path.join(ROOT, sys.argv[1]), encoding="utf-8"))
isos = (sys.argv[2] if len(sys.argv) > 2 else "GRC,TUR").split(",")
gj = im.load_geojson()
RINGS = [r for iso in isos for r in co.extract_rings(co.find_feature(gj, iso))]
BB = [(min(p[0] for p in r), min(p[1] for p in r), max(p[0] for p in r), max(p[1] for p in r)) for r in RINGS]
def land(lon, lat):
    ins = False
    for r, b in zip(RINGS, BB):
        if not (b[0] <= lon <= b[2] and b[1] <= lat <= b[3]): continue
        j = len(r) - 1
        for i in range(len(r)):
            xi, yi = r[i][:2]; xj, yj = r[j][:2]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                ins = not ins
            j = i
    return ins
def pt(key):
    if isinstance(key, str) and key.startswith("dot:"):
        d = next(d for d in cfg["dots"] if d["name"] == key[4:]); return (d["lon"], d["lat"])
    if key == "air":
        a = im.airport_list(cfg)[0]; return (a["lon"], a["lat"])
    s = cfg["stops"][key]; return (s["lon"], s["lat"])
def curve(leg, n=60):
    a, b = pt(leg["a"]), pt(leg["b"])
    P = [a] + [(lo, la) for la, lo in leg.get("via", [])] + [b]
    out = []
    if len(P) == 2:
        if not leg.get("bow"):
            return [(a[0] + (b[0]-a[0])*t/n, a[1] + (b[1]-a[1])*t/n) for t in range(n+1)]
        # bow is applied in projected (x right, y down) space: y=-lat
        x1, y1, x2, y2 = a[0], -a[1], b[0], -b[1]; k = leg["bow"]
        mx, my = (x1+x2)/2 + (y2-y1)*k, (y1+y2)/2 - (x2-x1)*k
        cx, cy = 2*mx - (x1+x2)/2, 2*my - (y1+y2)/2
        for t in [i/n for i in range(n+1)]:
            x = (1-t)**2*x1 + 2*(1-t)*t*cx + t*t*x2; y = (1-t)**2*y1 + 2*(1-t)*t*cy + t*t*y2
            out.append((x, -y))
        return out
    for i in range(len(P) - 1):
        p0 = P[i-1] if i else P[i]; p1, p2 = P[i], P[i+1]; p3 = P[i+2] if i+2 < len(P) else p2
        c1 = (p1[0] + (p2[0]-p0[0])/6, p1[1] + (p2[1]-p0[1])/6); c2 = (p2[0] - (p3[0]-p1[0])/6, p2[1] - (p3[1]-p1[1])/6)
        for t in [j/n for j in range(n)]:
            u = 1-t
            out.append(tuple(u**3*p1[k] + 3*u*u*t*c1[k] + 3*u*t*t*c2[k] + t**3*p2[k] for k in (0, 1)))
    out.append(P[-1]); return out
bad = 0
for li, leg in enumerate(cfg["legs"]):
    if not leg.get("sea"): continue
    c = curve(leg)
    import math
    a, b = c[0], c[-1]
    hits = [(round(p[1], 3), round(p[0], 3)) for p in c
            if math.hypot(p[0]-a[0], p[1]-a[1]) > 0.06 and math.hypot(p[0]-b[0], p[1]-b[1]) > 0.06 and land(*p)]
    name = lambda k: k if isinstance(k, str) else cfg["stops"][k]["name"]
    print("leg %d %s -> %s: %s" % (li, name(leg["a"]), name(leg["b"]), "OK" if not hits else "LAND x%d %s" % (len(hits), hits[:6])))
    bad += bool(hits)
print("bad legs:", bad)
