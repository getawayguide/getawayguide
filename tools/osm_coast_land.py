#!/usr/bin/env python3
"""Land polygons for a small area from OpenStreetMap coastline ways, for a route map drawn at a scale where
Natural Earth is too coarse (a 20 km box: the Ölüdeniz lagoon is a dozen vertices there, the Fethiye bay a
notch). The Lycian Way map (2026-10-19) was the first.

Input: an Overpass answer for `way["natural"="coastline"](S,W,N,E); out geom;` (fetch it with PowerShell, see
tools/fetch_osm.ps1: the Bash tool has no network). Output: a JSON list of rings, each [[lon, lat], ...], that
tools/itinerary_map.py takes as "outline_file".

How: OSM draws every coastline with LAND ON THE LEFT. The ways are chained end to end, clipped to the box, and
each clipped piece is closed along the box's edge walked counter-clockwise (the box interior is on the left of a
counter-clockwise walk, the same side as the land), piece to piece, until the walk returns to where it began.
Closed rings that lie wholly inside the box are islands and pass through as they are. No shapely needed.

    python tools/osm_coast_land.py .tmp/fethiye_coast.json .tmp/fethiye_land.json 29.0 36.46 29.22 36.66
"""
import json
import sys


def chain(ways):
    """join ways whose ends share a node id into polylines of (lon, lat); returns (closed, open)"""
    segs = {w["id"]: ([(g["lon"], g["lat"]) for g in w["geometry"]], w["nodes"][0], w["nodes"][-1]) for w in ways}
    by_start, by_end = {}, {}
    for wid, (_, a, b) in segs.items():
        by_start.setdefault(a, []).append(wid)
        by_end.setdefault(b, []).append(wid)
    used, closed, open_ = set(), [], []
    for wid in list(segs):
        if wid in used:
            continue
        pts, a, b = segs[wid]
        used.add(wid)
        line, start, end = list(pts), a, b
        while end != start:                                  # forward
            nxt = next((n for n in by_start.get(end, []) if n not in used), None)
            if nxt is None:
                break
            used.add(nxt)
            p2, _, end = segs[nxt]
            line += p2[1:]
        if end == start and len(line) > 3:
            closed.append(line)
            continue
        while True:                                          # backward
            prv = next((n for n in by_end.get(start, []) if n not in used), None)
            if prv is None:
                break
            used.add(prv)
            p0, start, _ = segs[prv]
            line = p0[:-1] + line
        open_.append(line)
    return closed, open_


def clip_line(line, W, S, E, N):
    """pieces of a polyline inside the box, each starting and ending where the line crossed the box edge"""
    def cut(a, b):                                           # Liang-Barsky: the [t0, t1] of a->b inside the box
        t0, t1 = 0.0, 1.0
        dx, dy = b[0] - a[0], b[1] - a[1]
        for p, q in ((-dx, a[0] - W), (dx, E - a[0]), (-dy, a[1] - S), (dy, N - a[1])):
            if p == 0:
                if q < 0:
                    return None
                continue
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return None
        return t0, t1

    pieces, cur = [], None
    for a, b in zip(line, line[1:]):
        r = cut(a, b)
        if r is None:
            if cur:
                pieces.append(cur)
            cur = None
            continue
        t0, t1 = r
        pa = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
        pb = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
        if cur is None:
            cur = [pa]
        elif t0 > 0:
            pieces.append(cur)
            cur = [pa]
        cur.append(pb)
        if t1 < 1:
            pieces.append(cur)
            cur = None
    if cur:
        pieces.append(cur)
    return [p for p in pieces if len(p) > 1]


def land_rings(ways, W, S, E, N):
    closed, open_ = chain(ways)
    per = 2 * (E - W) + 2 * (N - S)
    eps = 1e-9

    def inside(p):
        return W - eps <= p[0] <= E + eps and S - eps <= p[1] <= N + eps

    def t_of(p):
        """perimeter parameter, counter-clockwise from the (W, S) corner; None off the edge"""
        x, y = p
        if abs(y - S) < eps:
            return x - W
        if abs(x - E) < eps:
            return (E - W) + (y - S)
        if abs(y - N) < eps:
            return (E - W) + (N - S) + (E - x)
        if abs(x - W) < eps:
            return 2 * (E - W) + (N - S) + (N - y)
        return None

    corners = [(E, S), (E, N), (W, N), (W, S)]
    corner_t = [t_of(c) for c in corners]
    islands, coast = [], []                                  # coast: (points, t_in, t_out)
    for line in closed + open_:
        ring = line[0] == line[-1]
        if ring and all(inside(p) for p in line):
            islands.append(line)
            continue
        if ring:                                             # a ring crossing the box: open it at an outside point
            k = next(i for i, p in enumerate(line) if not inside(p))
            line = line[k:] + line[1:k + 1]
        for pc in clip_line(line, W, S, E, N):
            ti, to = t_of(pc[0]), t_of(pc[-1])
            if ti is None or to is None:
                print("  skipped a piece that does not reach the box edge (%d pts)" % len(pc), file=sys.stderr)
                continue
            coast.append((pc, ti, to))

    rings, seen = list(islands), set()
    for i in range(len(coast)):
        if i in seen:
            continue
        ring, j = [], i
        while True:
            seen.add(j)
            pc, ti, to = coast[j]
            ring += pc
            # the next entry ahead of this exit, walking the edge counter-clockwise; the start piece may close it
            best, bk = None, None
            for k, (_, ti2, _) in enumerate(coast):
                if k in seen and k != i:
                    continue
                gap = (ti2 - to) % per
                if best is None or gap < best:
                    best, bk = gap, k
            if bk is None:
                break
            ring += [c for _, c in sorted(((ct - to) % per, c) for c, ct in zip(corners, corner_t) if (ct - to) % per < best)]
            if bk == i:
                break
            j = bk
        ring.append(ring[0])
        rings.append(ring)
    return rings


def main():
    src, dst = sys.argv[1], sys.argv[2]
    W, S, E, N = map(float, sys.argv[3:7])
    d = json.load(open(src, encoding="utf-8"))
    ways = [e for e in d["elements"] if e["type"] == "way" and e.get("geometry")]
    rings = land_rings(ways, W, S, E, N)
    json.dump(rings, open(dst, "w"))

    def area(r):
        return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(r, r[1:])) / 2)
    print("%d ring(s) from %d coastline way(s):" % (len(rings), len(ways)),
          ", ".join("%d pts / area %.5f" % (len(r), area(r)) for r in rings))


if __name__ == "__main__":
    main()
