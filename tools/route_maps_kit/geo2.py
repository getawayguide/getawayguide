"""Variant of .tmp/skopje_r9/geo_layers.py: unsimplified NE 10m rings clipped to a box, so shared borders are one line.
usage: geo2.py <prefix> W S E N MAIN=GRC route=ALB,MKD neighbors=BGR,TUR keep=0.00005"""
import json, os, sys
ROOT = r"C:\Users\kevin\Projects\Travel Blog"
sys.path.insert(0, os.path.join(ROOT, "tools")); sys.path.insert(0, os.path.join(ROOT, ".tmp", "skopje_r9"))
import itinerary_map as im, country_outline as co
prefix = sys.argv[1]; W, S, E, N = map(float, sys.argv[2:6])
opts = dict(a.split("=", 1) for a in sys.argv[6:])
keep = float(opts.get("keep", "0.00005"))
src = open(os.path.join(ROOT, ".tmp", "skopje_r9", "geo_layers.py"), encoding="utf-8").read()
ns = {}
exec(src[src.index("def clip"):src.index("gj = im.load_geojson()")], ns)
clip = ns["clip"]
gj = im.load_geojson()
def rings(iso):
    rs = co.extract_rings(co.find_feature(gj, iso))
    areas = [co.ring_area(r) for r in rs]; big = max(areas); out = []
    for r, a in zip(rs, areas):
        if a < big * keep: continue
        c = clip(r, W - .3, S - .3, E + .3, N + .3)
        if len(c) >= 3:
            out.append([[round(x, 5), round(y, 5)] for x, y in c] + [[round(c[0][0], 5), round(c[0][1], 5)]])
    return out
GEO = os.path.join(ROOT, "tools", "itinerary_maps", "geo")
for name, codes in (("main", [opts["MAIN"]]), ("route", [c for c in opts.get("route", "").split(",") if c]),
                    ("neighbors", [c for c in opts.get("neighbors", "").split(",") if c])):
    if not codes: continue
    rs = [r for c in codes for r in rings(c)]
    p = os.path.join(GEO, "%s-%s.json" % (prefix, name))
    json.dump(rs, open(p, "w", encoding="utf-8"))
    print(p, len(rs), "rings", sum(map(len, rs)), "points")
