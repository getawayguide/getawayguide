"""Every embedded city map: does each key row's Google Maps link land near its pin?
Usage: python tools/check_map_links.py [map-name ...]   (default: all maps)"""
import json, glob, re, math, sys
sys.stdout.reconfigure(encoding="utf-8")
only = set(sys.argv[1:])
bad = n = 0
for cf in sorted(glob.glob("tools/city_maps/*.json")):
    name_ = re.split(r"[\\/]", cf)[-1][:-5]
    if only and name_ not in only:
        continue
    c = json.load(open(cf, encoding="utf-8"))
    try:
        h = open(c.get("article", ""), encoding="utf-8").read()
    except Exception:
        continue
    # a page can hold several city maps (Croatia: Split, Dubrovnik, Hvar): take THIS map's figure
    slug = c.get("slug", name_)
    f = next((m for m in re.finditer(r'<figure class="citymap-fig".*?</figure>', h, re.S)
              if "city-maps/%s.png" % slug in m.group(0)), None)
    if not f:
        continue
    pos = {p["name"]: (p["lat"], p["lon"]) for p in c["pois"]}
    for u, name in re.findall(r'<a class="cmrow"[^>]*href="([^"]+)"[^>]*>.*?<span class="nm">([^<]+)</span>', f.group(0)):
        name = name.replace("&amp;", "&")
        if name not in pos:
            continue
        if "/maps/place/" not in u:
            if only: print("  %-12s %-30s (search link) %s" % (name_, name, u[:90]))
            continue
        pts = re.findall(r"!3d(-?[\d.]+)!4d(-?[\d.]+)", u) or re.findall(r"@(-?[\d.]+),(-?[\d.]+)", u)
        if not pts:
            continue
        la, lo = map(float, pts[-1]); n += 1
        d = math.hypot((la - pos[name][0]) * 111, (lo - pos[name][1]) * 111 * math.cos(math.radians(la)))
        if d > 3 or only:
            bad += d > 3
            print("  %-12s %-30s %6.1f km   %s" % (name_, name, d, u.split("/data=")[0][-70:]))
print(n, "links checked,", bad, "more than 3 km off")
