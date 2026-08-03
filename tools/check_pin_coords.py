#!/usr/bin/env python3
"""Cross-check every city-map pin's lat/lon against the place its own link points at.

A pin's coordinates and its Google-Maps link are set independently, so a pin can sit in the
wrong part of town while its link opens the right place (Oaxaca's "La Popular" was ~5km
southwest of the centro). Now that every link resolves to a real /maps/place/ URL, the URL
carries authoritative coordinates (!3d<lat>!4d<lon>), so the two can be compared.

Also flags pins that sit far from the rest of the map's cluster, which catches a bad pin
whose link is missing or equally wrong.

  python tools/check_pin_coords.py                 # all live configs
  python tools/check_pin_coords.py oaxaca --fix    # write the link's coords onto the pin
"""
import argparse, glob, json, math, os, re, sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # pin names carry ı, ð, Đ …
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_place import place_coords          # handles multi-!3d URLs correctly


def haversine(a, b, c, d):
    R = 6371.0
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


coords_in = place_coords


def _links(html):
    return [(u.replace("&amp;", "&"), re.sub(r"<[^>]+>", "", t).strip().lower())
            for u, t in re.findall(
                r'<a\b[^>]*href="(https://www\.google\.com/maps/[^"]+)"[^>]*>(.*?)</a>', html, re.S)]


def article_links(path, slug):
    """Same resolution order as city_map.href_for(): this city's fn-section first, then
    the whole article. Must stay in step with the engine or the check reports phantom bugs."""
    try:
        html = open(path, encoding="utf-8").read()
    except OSError:
        return []
    m = re.search(r'<div class="fn-section" id="%s"' % re.escape(slug), html)
    sec = ""
    if m:
        n = re.search(r'<div class="fn-section" id="', html[m.end():])
        sec = html[m.start():m.end() + n.start()] if n else html[m.start():]
    return _links(sec) + _links(html)


def median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", nargs="?", help="one config (default: all live)")
    ap.add_argument("--fix", action="store_true", help="write the link's coords onto mismatched pins")
    ap.add_argument("--near", type=float, default=0.6, help="km mismatch to report (default 0.6)")
    ap.add_argument("--outlier", type=float, default=12.0, help="km from map median to flag")
    a = ap.parse_args()

    paths = ([os.path.join(ROOT, "tools", "city_maps", a.slug + ".json")] if a.slug
             else sorted(glob.glob(os.path.join(ROOT, "tools", "city_maps", "*.json"))))
    mism, outl, nolink = [], [], 0
    for p in paths:
        cfg = json.load(open(p, encoding="utf-8"))
        art = cfg.get("article", "")
        if art.startswith("Drafts/") and not a.slug:
            continue
        slug = os.path.basename(p)[:-5]
        links = article_links(os.path.join(ROOT, art), slug) if art else []
        mlat, mlon = median([q["lat"] for q in cfg["pois"]]), median([q["lon"] for q in cfg["pois"]])
        changed = 0
        for q in cfg["pois"]:
            d0 = haversine(q["lat"], q["lon"], mlat, mlon)
            if d0 > a.outlier:
                outl.append((slug, q["name"], d0))
            url = q.get("href")
            if not url:
                key = q.get("match", q["name"].lower())
                url = next((u for u, t in links if key in t), None)
            if not url:
                nolink += 1
                continue
            ll = coords_in(url)
            if not ll:
                continue
            d = haversine(q["lat"], q["lon"], *ll)
            if d < a.near:
                continue
            # Which end is wrong? Compare each against the map's own cluster. href_for()
            # substring-matches across the WHOLE article, so a place name that also appears
            # in another city's section (Dubrovnik's "Mlinar" vs Split's) links to the wrong
            # one - that's a bad LINK, not a bad pin, and must never be "fixed" onto the pin.
            dlink = haversine(ll[0], ll[1], mlat, mlon)
            kind = "LINK" if dlink > a.outlier and d0 <= a.outlier else \
                   "PIN" if d0 > dlink else "BOTH?"
            mism.append((kind, slug, q["name"], d, (q["lat"], q["lon"]), ll))
            if a.fix and kind == "PIN":
                q["lat"], q["lon"] = round(ll[0], 6), round(ll[1], 6)
                changed += 1
        if changed:
            json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print("  fixed %d pin(s) in %s" % (changed, slug))

    for kind, title in (("LINK", "BAD LINK - the pin's link opens a same-named place elsewhere"),
                        ("PIN", "BAD PIN - the pin sits away from the place its link opens"),
                        ("BOTH?", "UNCLEAR - both ends look off")):
        rows = [r for r in mism if r[0] == kind]
        print("\n%s: %d" % (title, len(rows)))
        for _, slug, name, d, pin, ll in sorted(rows, key=lambda r: -r[3]):
            print("   %-16s %-30s %8.2f km   pin %.5f,%.5f -> link %.5f,%.5f"
                  % (slug, name[:30], d, pin[0], pin[1], ll[0], ll[1]))
    print("\nPINS FAR FROM THEIR MAP'S CLUSTER (> %.0f km): %d" % (a.outlier, len(outl)))
    for slug, name, d in sorted(outl, key=lambda r: -r[2])[:20]:
        print("   %-16s %-30s %7.1f km" % (slug, name[:30], d))
    print("\n(%d pin(s) had no resolvable link to check against)" % nolink)


if __name__ == "__main__":
    main()
