#!/usr/bin/env python3
"""Build tiny continent-silhouette SVG icons for the Destinations nav.

Emoji read as clip-art next to this site's type, and at 12px the three globe emoji
(🌍🌎🌏) are indistinguishable anyway. These are real coastlines from the Natural Earth
data already in .tmp, simplified hard and normalised into a 16x16 box so each continent
fills its icon at the same visual weight.

Paths use fill="currentColor", so they inherit the link colour and its hover state, and
they're inlined into the markup - no extra requests, crisp at any DPI.

  py tools/gen_continent_icons.py           # -> tools/continent_icons.py
"""
import json, math, os, sys

sys.setrecursionlimit(50000)          # RDP recurses once per retained vertex
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, ".tmp", "ne_50m_countries.geojson")
INK = "#1C2821"
BOX = 16.0

GROUPS = {
    "africa":   ["Africa"],
    "americas": ["North America", "South America"],
    "asia":     ["Asia"],
    "europe":   ["Europe"],
    "oceania":  ["Oceania"],
}
# trim far-flung territories that would shrink the shape to nothing
CLIP = {
    "europe":   (-27, 34, 46, 72),      # drop French Guiana etc, keep Iceland
    "americas": (-172, -57, -30, 73),
    "oceania":  (110, -50, 182, 0),
    "asia":     (25, -12, 150, 78),
    "africa":   (-26, -36, 52, 38),
}
# country polygons never union into one outline, so they are drawn filled and
# tile into the continent shape - keep small ones or the silhouette gets holes
MIN_AREA = {"africa": 0.35, "americas": 0.5, "asia": 0.5, "europe": 0.12, "oceania": 0.15}
# Each shape is normalised to fill the 16x16 box, but they don't carry equal visual weight:
# Africa is one solid blob so it reads much heavier than the sparse, island-y ones at the
# same nominal size. Scale it down so the row looks even.
SCALE = {"africa": 0.82}


def rings(feat):
    g = feat["geometry"]
    if g["type"] == "Polygon":
        return [g["coordinates"][0]]
    if g["type"] == "MultiPolygon":
        return [p[0] for p in g["coordinates"]]
    return []


def area(r):
    return abs(sum(r[i][0] * r[i - 1][1] - r[i - 1][0] * r[i][1] for i in range(len(r)))) / 2


def simplify(pts, tol):
    """Ramer-Douglas-Peucker."""
    if len(pts) < 3:
        return pts
    ax, ay = pts[0]; bx, by = pts[-1]
    dx, dy = bx - ax, by - ay
    n = math.hypot(dx, dy) or 1e-9
    worst, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        d = abs(dy * px - dx * py + bx * ay - by * ax) / n
        if d > worst:
            worst, idx = d, i
    if worst <= tol:
        return [pts[0], pts[-1]]
    return simplify(pts[:idx + 1], tol)[:-1] + simplify(pts[idx:], tol)


def build(key, feats):
    lo = CLIP.get(key)
    polys = []
    for f in feats:
        for r in rings(f):
            if lo:
                cx = sum(p[0] for p in r) / len(r)
                cy = sum(p[1] for p in r) / len(r)
                if not (lo[0] <= cx <= lo[2] and lo[1] <= cy <= lo[3]):
                    continue
            if area(r) < MIN_AREA.get(key, 1.0):
                continue
            polys.append(r)
    if not polys:
        return None
    xs = [p[0] for r in polys for p in r]
    ys = [p[1] for r in polys for p in r]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    # equal-ish area projection so shapes don't smear at high latitude
    def proj(p):
        return p[0] * math.cos(math.radians((miny + maxy) / 2)), p[1]
    polys = [[proj(p) for p in r] for r in polys]
    xs = [p[0] for r in polys for p in r]; ys = [p[1] for r in polys for p in r]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    s = min(BOX / (maxx - minx or 1e-9), BOX / (maxy - miny or 1e-9)) * SCALE.get(key, 1.0)
    ox = (BOX - (maxx - minx) * s) / 2
    oy = (BOX - (maxy - miny) * s) / 2
    out = []
    for r in polys:
        q = [((p[0] - minx) * s + ox, BOX - ((p[1] - miny) * s + oy)) for p in r]
        if len(q) > 900:                       # bound RDP's recursion depth
            step = len(q) // 900 + 1
            q = q[::step] + [q[-1]]
        # RDP needs an OPEN polyline: on a closed ring first == last, so the baseline is
        # degenerate and every point measures as infinitely far from it. Cut the ring at
        # its two most distant vertices and simplify each half.
        if len(q) > 3 and q[0] == q[-1]:
            q = q[:-1]
        if len(q) > 4:
            far = max(range(len(q)), key=lambda i: (q[i][0] - q[0][0]) ** 2 + (q[i][1] - q[0][1]) ** 2)
            q = simplify(q[:far + 1], 0.30)[:-1] + simplify(q[far:] + [q[0]], 0.30)[:-1]
        if len(q) < 3:
            continue
        out.append("M" + "L".join("%.2f %.2f" % t for t in q) + "Z")
    return "".join(out)


def main():
    if not os.path.exists(SRC):
        sys.exit("missing %s" % SRC)
    data = json.load(open(SRC, encoding="utf-8"))
    by = {}
    for f in data["features"]:
        by.setdefault(f["properties"].get("CONTINENT"), []).append(f)

    icons = {}
    for key, conts in GROUPS.items():
        feats = [f for c in conts for f in by.get(c, [])]
        d = build(key, feats)
        if d:
            icons[key] = d
            print("  %-9s %d chars" % (key, len(d)))
    # "All destinations" = a globe glyph rather than a landmass
    icons["all"] = ("M8 0.6A7.4 7.4 0 108 15.4 7.4 7.4 0 008 0.6ZM8 1.9c1.3 0 2.6 2.4 2.6 6.1"
                    "S9.3 14.1 8 14.1 5.4 11.7 5.4 8 6.7 1.9 8 1.9ZM1.9 8h12.2M8 1.9v12.2")

    # Written as FILES, not inlined: the paths total ~9KB, and the nav appears on 45 pages,
    # so inlining would add ~400KB of markup site-wide. Six cached requests instead.
    outdir = os.path.join(ROOT, "Images", "web", "continents")
    os.makedirs(outdir, exist_ok=True)
    total = 0
    for k, d in icons.items():
        # the countries are separate polygons, so simplification leaves hairline cracks
        # between them - a thin stroke in the fill colour welds them into one silhouette
        style = ('fill="none" stroke="%s" stroke-width="1.1"' % INK) if k == "all" \
            else 'fill="%s" stroke="%s" stroke-width="0.45" stroke-linejoin="round"' % (INK, INK)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" '
               'height="16"><path d="%s" %s/></svg>' % (d, style))
        p = os.path.join(outdir, "%s.svg" % k)
        open(p, "w", encoding="utf-8").write(svg)
        total += len(svg)
    print("wrote %d icon(s) -> Images/web/continents/  (%.1f KB total)" % (len(icons), total / 1024))


if __name__ == "__main__":
    main()
