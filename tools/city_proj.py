#!/usr/bin/env python3
"""Recover the projection a published city map was drawn with, and measure pin drift.

A map built before the desktop svg carried data-proj (2026-10) can still take new pins over its
published base: the pins already on the page give the projection back (x = W/2 + (mx - cx) * s,
y = H/2 - (my - cy) * s in Mercator units), and tools/city_map.py's "proj_lock" pins it, so a
--keep-base rebuild draws every old pin on the same street however the new pins move the framing.

    python tools/city_proj.py <page.html> <slug>                 # print {"desktop": [cx, cy, s], "mobile": [...]}
    python tools/city_proj.py <page.html> <slug> --cfg <cfg.json> # also write it into the config as proj_lock
    python tools/city_proj.py --drift <old.html> <new.html> <slug> [<new slug>]   # pin shift per name, both variants
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from city_map import merc, DESK_W, DESK_H, MOB_W, MOB_H

PIN = re.compile(r'<g class="cmpin" data-i="(\d+)" data-name="([^"]*)"><g transform="translate\(([-\d.]+) ([-\d.]+)\)')


def figure(html, slug):
    """the <figure class="citymap-fig"> holding city-maps/<slug>.png"""
    k = html.index(f"city-maps/{slug}.png")
    a = html.rindex('<figure class="citymap-fig">', 0, k)
    return html[a:html.index("</figure>", a)]


def pins(fig):
    """{"desktop": {name: (x, y)}, "mobile": {...}}"""
    m = fig.index('<div class="citymap mob"')
    out = {}
    for var, part in (("desktop", fig[:m]), ("mobile", fig[m:])):
        out[var] = {}
        for _, name, x, y in PIN.findall(part):
            out[var][name.replace("&amp;", "&")] = (float(x), float(y))
    return out


def solve(pts, lonlat, W, H):
    """least squares for (cx, cy, s): x - W/2 = s*mx - s*cx, H/2 - y = s*my - s*cy"""
    rows = [(merc(*lonlat[n]), xy) for n, xy in pts.items() if n in lonlat]
    if len(rows) < 2:
        raise SystemExit("need two pins with known lat/lon")
    mxs = [m[0] for m, _ in rows]; mys = [m[1] for m, _ in rows]
    us = [xy[0] - W / 2 for _, xy in rows]; vs = [H / 2 - xy[1] for _, xy in rows]
    n = len(rows)
    # one shared scale: minimize sum (s*mx + a - u)^2 + (s*my + b - v)^2, a = -s*cx, b = -s*cy
    mmx, mmy, mu, mv = sum(mxs) / n, sum(mys) / n, sum(us) / n, sum(vs) / n
    num = sum((x - mmx) * (u - mu) for x, u in zip(mxs, us)) + sum((y - mmy) * (v - mv) for y, v in zip(mys, vs))
    den = sum((x - mmx) ** 2 for x in mxs) + sum((y - mmy) ** 2 for y in mys)
    s = num / den
    cx = mmx - mu / s; cy = mmy - mv / s
    res = max(((W / 2 + (merc(*lonlat[nm])[0] - cx) * s - xy[0]) ** 2 +
               (H / 2 - (merc(*lonlat[nm])[1] - cy) * s - xy[1]) ** 2) ** .5 for nm, xy in pts.items() if nm in lonlat)
    return [cx, cy, s], res


def main():
    a = sys.argv[1:]
    if a and a[0] == "--drift":
        old = pins(figure(open(a[1], encoding="utf-8").read(), a[3]))
        new = pins(figure(open(a[2], encoding="utf-8").read(), a[4] if len(a) > 4 else a[3]))
        worst = 0.0
        for var in ("desktop", "mobile"):
            for n, (x, y) in old[var].items():
                if n not in new[var]:
                    print(f"  {var}: {n} GONE"); worst = 99; continue
                d = ((new[var][n][0] - x) ** 2 + (new[var][n][1] - y) ** 2) ** .5
                worst = max(worst, d)
                if d > 0.15:
                    print(f"  {var}: {n} moved {d:.1f} px")
            added = [n for n in new[var] if n not in old[var]]
            print(f"  {var}: {len(old[var])} old pins, max drift so far {worst:.2f} px; new: {added}")
        return
    page, slug = a[0], a[1]
    cfgp = a[a.index("--cfg") + 1] if "--cfg" in a else os.path.join(ROOT, "tools", "city_maps", slug + ".json")
    cfg = json.load(open(cfgp, encoding="utf-8"))
    lonlat = {p["name"]: (p["lat"], p["lon"]) for p in cfg["pois"]}
    pp = pins(figure(open(page, encoding="utf-8").read(), slug))
    lock = {}
    for var, (W, H) in (("desktop", (DESK_W, DESK_H)), ("mobile", (MOB_W, MOB_H))):
        lock[var], res = solve(pp[var], lonlat, W, H)
        print(f"{var}: {len(pp[var])} pins, worst residual {res:.2f} px")
    print(json.dumps(lock))
    if "--cfg" in a:
        cfg["proj_lock"] = lock
        json.dump(cfg, open(cfgp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print("wrote proj_lock into", cfgp)


if __name__ == "__main__":
    main()
