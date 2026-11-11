#!/usr/bin/env python3
"""Write the context-land and lake ring files a route map draws as "layers" (tools/itinerary_map.py).

The Ohrid "Bus Routes" map (2026-11-02) set the look: the hub country is the config's iso3, the
neighbors are whole Natural Earth 10m countries drawn pale under it, a route-destination country a
shade darker, and lakes on top as water. Those ring files were cut by hand; this cuts them the same
way for any map, so every route map shares one recipe.

    python tools/itinerary_geo.py <prefix> --context HUN,ROU,BGR --route BIH,HRV --lakes 13,40,23,49

writes tools/itinerary_maps/geo/<prefix>-neighbors.json (the --context countries),
<prefix>-route.json (the --route countries) and <prefix>-lakes.json (every NE 10m lake whose
bbox lies inside W,S,E,N and that is big enough to see at country scale). Rings are [[lon, lat], ...],
simplified per country to about --points vertices (Douglas-Peucker), small islands dropped.
"""
import argparse, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import country_outline as co
import itinerary_map as im

GEO = os.path.join(ROOT, "tools", "itinerary_maps", "geo")
LAKES = os.path.join(ROOT, ".tmp", "ne_10m_lakes.geojson")


def country_rings(gj, iso3, points, keep):
    rings = co.extract_rings(co.find_feature(gj, iso3))
    areas = [co.ring_area(r) for r in rings]
    big = max(areas)
    rings = [[tuple(p) for p in r] for r, a in zip(rings, areas) if a >= big * keep]
    if points:                     # 0 = keep every vertex (the hub's "hub_detail": "full" borders then match)
        rings = co.simplify_rings(rings, points)
    return [[[round(x, 5), round(y, 5)] for x, y in r] for r in rings]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix")
    ap.add_argument("--context", default="", help="ISO3 list drawn pale (neighbors)")
    ap.add_argument("--route", default="", help="ISO3 list drawn a shade darker (route destinations)")
    ap.add_argument("--lakes", help="W,S,E,N box; lakes inside it go to <prefix>-lakes.json")
    ap.add_argument("--min-lake", type=float, default=0.004, help="smallest lake kept, deg^2")
    ap.add_argument("--points", type=int, default=260, help="vertices per country; 0 = full detail (use with hub_detail)")
    ap.add_argument("--keep", type=float, default=0.004, help="drop islands under this share of the mainland")
    a = ap.parse_args()
    gj = im.load_geojson()
    os.makedirs(GEO, exist_ok=True)
    for name, codes in (("neighbors", a.context), ("route", a.route)):
        codes = [c.strip() for c in codes.split(",") if c.strip()]
        if not codes:
            continue
        rings = [r for c in codes for r in country_rings(gj, c, a.points, a.keep)]
        out = os.path.join(GEO, f"{a.prefix}-{name}.json")
        json.dump(rings, open(out, "w", encoding="utf-8"))
        print(f"{out}: {len(codes)} countries, {len(rings)} rings, {sum(map(len, rings))} points")
    if a.lakes:
        W, S, E, N = map(float, a.lakes.split(","))
        rings = []
        for f in json.load(open(LAKES, encoding="utf-8"))["features"]:
            g = f["geometry"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            for po in polys:
                r = po[0]
                xs = [p[0] for p in r]; ys = [p[1] for p in r]
                if min(xs) >= W and max(xs) <= E and min(ys) >= S and max(ys) <= N and co.ring_area(r) >= a.min_lake:
                    rings.append([[round(x, 5), round(y, 5)] for x, y in r])
        out = os.path.join(GEO, f"{a.prefix}-lakes.json")
        json.dump(rings, open(out, "w", encoding="utf-8"))
        print(f"{out}: {len(rings)} lakes")


if __name__ == "__main__":
    main()
