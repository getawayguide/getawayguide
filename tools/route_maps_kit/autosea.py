"""Route every leg marked "sea": true over water: a least-cost path on a 0.01-degree water grid (cost rises near land,
so boats keep to mid-channel), Douglas-Peucker'd to a few "via" points, tightened until the builder's Catmull-Rom
curve through them never touches land (checked by sealegs.curve against the full NE 10m rings).
A leg with "via_lock": true is left alone. usage: autosea.py <config.json> ISO,ISO [W S E N]"""
import json, sys, os, math, heapq
import numpy as np
from scipy import ndimage
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = r"C:\Users\kevin\Projects\Travel Blog"
cfgp = sys.argv[1]; sys.argv = [sys.argv[0], cfgp, sys.argv[2]] + sys.argv[3:]
isos = sys.argv[2]
sys.argv_saved = list(sys.argv)
import importlib.util
spec = importlib.util.spec_from_file_location("sealegs_mod", os.path.join(HERE, "sealegs_lib.py"))
cfg = json.load(open(os.path.join(ROOT, cfgp), encoding="utf-8"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import itinerary_map as im, country_outline as co
gj = im.load_geojson()
RINGS = [r for iso in isos.split(",") for r in co.extract_rings(co.find_feature(gj, iso))]
cb = cfg.get("crop_bounds")
W, S, E, N = (map(float, sys.argv[3:7]) if len(sys.argv) >= 7 else (cb[0] - .5, cb[1] - .5, cb[2] + .5, cb[3] + .5))
DLAT = 0.01; DLON = DLAT / math.cos(math.radians((S + N) / 2))
ny, nx = int((N - S) / DLAT) + 1, int((E - W) / DLON) + 1
lats = S + np.arange(ny) * DLAT; lons = W + np.arange(nx) * DLON
landg = np.zeros((ny, nx), bool)
for j, la in enumerate(lats):
    xs = []
    for r in RINGS:
        for i in range(len(r)):
            x1, y1 = r[i][:2]; x2, y2 = r[i - 1][:2]
            if (y1 > la) != (y2 > la):
                xs.append(x1 + (la - y1) * (x2 - x1) / (y2 - y1))
    xs.sort()
    for a, b in zip(xs[::2], xs[1::2]):
        i0 = max(0, int(math.ceil((a - W) / DLON))); i1 = min(nx - 1, int(math.floor((b - W) / DLON)))
        if i1 >= i0: landg[j, i0:i1 + 1] = True
dist = ndimage.distance_transform_edt(~landg)            # cells to the nearest land
lab, nlab = ndimage.label(~landg)                        # only the open sea counts (no lagoons or enclosed ports)
sea_id = np.bincount(lab.ravel())[1:].argmax() + 1
landg = landg | (lab != sea_id)
cost = 1 + 6.0 / (dist + 0.5)

def cell(lat, lon): return int(round((lat - S) / DLAT)), int(round((lon - W) / DLON))
def nearest_water(c):
    j, i = c; best = None
    for r in range(0, 60):
        for dj in range(-r, r + 1):
            for di in (-r, r) if abs(dj) != r else range(-r, r + 1):
                jj, ii = j + dj, i + di
                if 0 <= jj < ny and 0 <= ii < nx and not landg[jj, ii] and dist[jj, ii] >= 1.5:
                    d = dj * dj + di * di
                    if best is None or d < best[0]: best = (d, (jj, ii))
        if best: return best[1]
    raise SystemExit("no water near %s" % (c,))
if os.environ.get("DBG"):
    from PIL import Image; Image.fromarray((~landg[::-1]).astype("uint8")*255).save(os.path.join(HERE,"landg.png")); print("grid",ny,nx,"S W",S,W)
    for la,lo in ((37.75,23.55),(37.94,23.64),(37.5,24.37),(37.3,25.0),(37.0,25.2)):
        c=cell(la,lo); print(la,lo,c,"land" if landg[c] else "sea", "comp",lab[c],"sea_id",sea_id, "nearest",nearest_water(c))
    sys.exit()
def path(a, b):
    a, b = nearest_water(a), nearest_water(b)
    D = {a: 0.0}; prev = {}; pq = [(0.0, 0.0, a)]
    nb = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    while pq:
        _, d, u = heapq.heappop(pq)
        if u == b: break
        if d > D[u]: continue
        for dj, di in nb:
            v = (u[0] + dj, u[1] + di)
            if not (0 <= v[0] < ny and 0 <= v[1] < nx) or landg[v]: continue
            nd = d + math.hypot(dj, di) * (cost[u] + cost[v]) / 2
            h = math.hypot(v[0] - b[0], v[1] - b[1])
            if nd < D.get(v, 1e18):
                D[v] = nd; prev[v] = u; heapq.heappush(pq, (nd + h, nd, v))
    p = [b]
    while p[-1] != a: p.append(prev[p[-1]])
    return [(lons[i], lats[j]) for j, i in reversed(p)]
def dp(pts, tol):
    if len(pts) < 3: return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    L = math.hypot(x2 - x1, y2 - y1) or 1e-9
    dmax, k = 0, 0
    for i in range(1, len(pts) - 1):
        d = abs((x2 - x1) * (y1 - pts[i][1]) - (x1 - pts[i][0]) * (y2 - y1)) / L
        if d > dmax: dmax, k = d, i
    if dmax > tol: return dp(pts[:k + 1], tol)[:-1] + dp(pts[k:], tol)
    return [pts[0], pts[-1]]
# the curve sampler + land test, shared with sealegs.py
src = open(os.path.join(HERE, "sealegs.py"), encoding="utf-8").read()
ns = {"cfg": cfg, "im": im, "json": json, "os": os, "sys": sys, "RINGS": RINGS}
exec(src[src.index("BB = ["):src.index("bad = 0")], ns)
def hits(leg):
    c = ns["curve"](leg); a, b = c[0], c[-1]
    return [p for p in c if math.hypot(p[0] - a[0], p[1] - a[1]) > 0.06 and math.hypot(p[0] - b[0], p[1] - b[1]) > 0.06 and ns["land"](*p)]
for li, leg in enumerate(cfg["legs"]):
    if not leg.get("sea") or leg.get("via_lock"): continue
    A, B = ns["pt"](leg["a"]), ns["pt"](leg["b"])
    raw = path(cell(A[1], A[0]), cell(B[1], B[0]))
    for tol in (0.08, 0.06, 0.045, 0.03, 0.02, 0.014, 0.01):
        simp = dp(raw, tol)
        leg["via"] = [[round(la, 3), round(lo, 3)] for lo, la in simp]   # keep the snapped water ends as vias too
        leg.pop("bow", None)
        if not hits(leg): break
    h = hits(leg)
    print("leg %d: %d via, tol %.3f, %s" % (li, len(leg["via"]), tol, "OK" if not h else "LAND x%d" % len(h)))
json.dump(cfg, open(os.path.join(ROOT, cfgp), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("wrote", cfgp)
