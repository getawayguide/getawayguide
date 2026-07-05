"""
Generate an accurate SVG outline of a country from real GeoJSON boundary data.

Usage:
    py tools/country_outline.py TUR            # ISO-3 code
    py tools/country_outline.py TUR --width 500

Uses the Natural Earth 50m admin-0 countries dataset (the canonical public
cartography source). Downloaded once to .tmp/ne_50m_countries.geojson and
cached; every subsequent country is extracted locally with no network call.

The polygon is projected (equirectangular with cos-latitude correction),
simplified (Douglas-Peucker), and printed as an <svg> snippet matching the
El Salvador hero-map convention (fill styled by .map-style-b in styles.css).

Multi-polygon handling: keeps every polygon whose area is at least 3% of the
largest (so Türkiye keeps Thrace, island nations keep main islands, but tiny
islets that would render as specks are dropped).

No API keys required. Source: raw.githubusercontent.com (public).

Windows/network quirk (learned 2026-07): Python urllib fails SSL verification
on this machine and schannel curl needs --ssl-no-revoke because certificate
revocation servers are unreachable from the sandbox.
"""

import json
import math
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(REPO_ROOT, ".tmp", "ne_50m_countries.geojson")
SOURCE_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
              "master/geojson/ne_50m_admin_0_countries.geojson")
PAD = 10          # px padding inside the viewBox, matches El Salvador path bounds
DEFAULT_WIDTH = 500
AREA_KEEP_RATIO = 0.03   # keep polygons >= 3% of the largest polygon's area
SIMPLIFY_TARGET = 90     # aim for roughly this many total points


def load_dataset():
    """Load the Natural Earth 50m dataset, downloading it once if missing."""
    if not os.path.exists(CACHE_PATH):
        print("Downloading Natural Earth 50m dataset (one-time, ~3 MB)...", file=sys.stderr)
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        # curl with --ssl-no-revoke: Python urllib fails SSL verification on
        # this machine, and schannel curl needs revocation checks disabled
        # (CRYPT_E_NO_REVOCATION_CHECK) because those servers are unreachable.
        subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", "60",
                        SOURCE_URL, "-o", CACHE_PATH], check=True)
    with open(CACHE_PATH, encoding="utf-8") as f:
        return json.load(f)


def find_feature(gj, iso3):
    iso3 = iso3.upper()
    for feat in gj["features"]:
        props = feat["properties"]
        codes = {props.get(k) for k in ("ADM0_A3", "ISO_A3", "SOV_A3", "GU_A3")}
        if iso3 in codes:
            return feat
    raise SystemExit(f"Country code {iso3} not found in dataset")


def extract_rings(feat):
    """Return list of outer rings (list of [lon, lat]) from a feature."""
    geom = feat["geometry"]
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [poly[0] for poly in geom["coordinates"]]
    raise ValueError("Unsupported geometry: " + geom["type"])


def ring_area(ring):
    """Shoelace area (abs) in degree^2 — only used for relative comparison."""
    a = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def project(rings):
    """Equirectangular projection with cos(mid-latitude) x-scaling.
    Returns rings in (x, y) with y flipped for SVG (north = up)."""
    lats = [pt[1] for r in rings for pt in r]
    mid_lat = (max(lats) + min(lats)) / 2.0
    k = math.cos(math.radians(mid_lat))
    return [[(pt[0] * k, -pt[1]) for pt in r] for r in rings]


def perp_dist(pt, a, b):
    """Perpendicular distance from pt to segment ab."""
    (px, py), (ax, ay), (bx, by) = pt, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def douglas_peucker(pts, tol):
    if len(pts) < 3:
        return pts
    dmax, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        d = perp_dist(pts[i], pts[0], pts[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax > tol:
        left = douglas_peucker(pts[: idx + 1], tol)
        right = douglas_peucker(pts[idx:], tol)
        return left[:-1] + right
    return [pts[0], pts[-1]]


def simplify_rings(rings, target_points):
    """Binary-search a tolerance that lands near the target point count."""
    lo, hi = 0.0, 1.0
    best = rings
    for _ in range(24):
        tol = (lo + hi) / 2.0
        simplified = [douglas_peucker(r, tol) for r in rings]
        # drop rings simplified to nothing
        simplified = [r for r in simplified if len(r) >= 4]
        n = sum(len(r) for r in simplified)
        if n > target_points:
            lo = tol
        else:
            hi = tol
            best = simplified
    return best


def fit_to_viewbox(rings, width):
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span_x, span_y = maxx - minx, maxy - miny
    scale = (width - 2 * PAD) / span_x
    height = round(span_y * scale + 2 * PAD)
    out = []
    for r in rings:
        out.append([((p[0] - minx) * scale + PAD, (p[1] - miny) * scale + PAD) for p in r])
    return out, width, height


def to_path(rings):
    parts = []
    for r in rings:
        pts = r[:-1] if r[0] == r[-1] else r
        d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts) + " Z"
        parts.append(d)
    return " ".join(parts)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    iso3 = sys.argv[1].upper()
    width = DEFAULT_WIDTH
    if "--width" in sys.argv:
        width = int(sys.argv[sys.argv.index("--width") + 1])

    gj = load_dataset()
    feat = find_feature(gj, iso3)
    props = feat["properties"]
    name = props.get("NAME") or props.get("name") or iso3
    rings = extract_rings(feat)

    # keep only significant polygons
    areas = [ring_area(r) for r in rings]
    biggest = max(areas)
    rings = [r for r, a in zip(rings, areas) if a >= biggest * AREA_KEEP_RATIO]
    print(f"{name}: {len(areas)} polygon(s), keeping {len(rings)}", file=sys.stderr)

    rings = project(rings)
    rings = simplify_rings(rings, SIMPLIFY_TARGET)
    rings, vb_w, vb_h = fit_to_viewbox(rings, width)
    d = to_path(rings)
    n_pts = sum(len(r) for r in rings)
    print(f"Simplified to {n_pts} points, viewBox 0 0 {vb_w} {vb_h}", file=sys.stderr)

    # Tall countries would overflow the ~40vh hero when sized by width
    # (.map-style-b svg is width:min(44vw,384px)), so constrain by height.
    style = ' style="height:min(30vh,300px);width:auto"' if vb_h > 400 else ""
    print(f'<svg viewBox="0 0 {vb_w} {vb_h}" xmlns="http://www.w3.org/2000/svg"{style}>')
    print(f'  <path d="{d}"/>')
    print("</svg>")


if __name__ == "__main__":
    main()
