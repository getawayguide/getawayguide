# -*- coding: utf-8 -*-
"""Extract per-city POIs (name, coords, category) from a field-notes article for city_map configs.
Usage: python tools/mapgen.py <country-slug> report            -> print POIs per section
       python tools/mapgen.py <country-slug> config <sec:City> ... -> write tools/city_maps/<slug>.json per city
"""
import json, os, re, sys, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = {"what to do": "see", "where to eat": "eat", "where to drink": "eat",
       "where to eat & drink": "eat", "where to eat and drink": "eat",
       "where to stay": "stay"}

def sections(html):
    """{id: (inner_html, start, end)} for each fn-section."""
    out = {}
    ms = list(re.finditer(r'<div class="fn-section" id="([^"]+)">', html))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else html.find('</div>\n</div>', m.end())
        if end < 0: end = len(html)
        out[m.group(1)] = (html[m.start():end], m.start(), end)
    return out

def clean(t):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', t)).replace('&amp;', '&').strip()

def coords(url):
    m = re.search(r'!3d(-?[0-9.]+)!4d(-?[0-9.]+)', url) or re.search(r'/@(-?[0-9.]+),(-?[0-9.]+)', url)
    return (float(m.group(1)), float(m.group(2))) if m else None

def pois_for(inner):
    """List of (name, cat, coord|None). Category tracked by the preceding fn-sub-hd; one POI per top-level <li>."""
    out, seen = [], set()
    cat = "see"
    # walk the section in order, updating cat on each fn-sub-hd, grabbing the first gmaps link per <li>
    for tok in re.finditer(r'<div class="fn-sub-hd"[^>]*>([^<]*)</div>|<li>(.*?)</li>', inner, re.DOTALL):
        if tok.group(1) is not None:
            lab = clean(tok.group(1)).lower()
            cat = CAT.get(lab, cat if lab in ("how to get there",) else "see")
            if lab == "how to get there": cat = "_skip"
            continue
        li = tok.group(2)
        if cat == "_skip": continue
        a = re.search(r'<a href="(https://www\.google\.com/maps[^"]+)"[^>]*>(.*?)</a>', li, re.DOTALL)
        if not a: continue
        name = clean(a.group(2))
        if not name or name.lower() in seen: continue
        seen.add(name.lower())
        out.append((name, cat, coords(a.group(1).replace('&amp;', '&'))))
    return out

def cityname(inner):
    m = re.search(r'<div class="fn-city-hd">\s*<h2>(.*?)</h2>', inner)
    return clean(m.group(1)) if m else ""

def embed_anchors(html, sid, order):
    """Insert point = right after the section's last </ul>, before its closing </div>.
    pre = unique tail up to that point; post = the closing </div> + any comment + the next-section div."""
    si = html.index('id="%s"' % sid)
    idx = order.index(sid)
    if idx + 1 < len(order):                             # normal: insert before the next fn-section
        nxt_div = '<div class="fn-section" id="%s"' % order[idx + 1]   # prefix (tolerates extra attrs)
        nsi = html.index(nxt_div, si)
        close = html.rfind('\n  </div>', 0, nsi) + 1
        post = html[close:nsi + len(nxt_div)]
    else:                                                # last section: insert before the article-body close
        m = re.search(r'\n  </div>\s*\n</div>\s*\n\s*<footer', html[si:])   # include <footer> so it's unique
        close = si + m.start() + 1
        post = html[close:si + m.end()]                  # '  </div>\n</div>\n\n<footer'
    k = 90
    while html.count(html[close - k:close]) != 1 and k < 500:
        k += 40
    return html[close - k:close], post

SEA_BG = {"santorini", "ios", "mykonos", "naxos", "zanzibar", "puerto-escondido", "coron",
          "el-nido", "siargao", "port-barton", "oludeniz-fethiye", "kas", "canggu", "kotor",
          "perast", "ohrid", "istanbul", "antalya", "manila", "lima"}

CAP = {"see": 13, "eat": 5, "stay": 3}  # keep the legend under ~24 rows

def article_path(slug):
    """Live article if it exists, else the Drafts/ copy (drafts sit one folder deeper)."""
    for rel in (f"{slug}/field-notes.html", f"Drafts/{slug}/field-notes.html"):
        if os.path.exists(f"{ROOT}/{rel}"):
            return rel
    raise SystemExit(f"no article found for {slug} (looked in ./ and Drafts/)")


def build_config(slug, sid, inner, html, order, kicker):
    ps = pois_for(inner)
    kept, n = [], {"see": 0, "eat": 0, "stay": 0}
    for name, cat, co in ps:                 # article order; cap per category
        if n.get(cat, 0) >= CAP.get(cat, 99): continue
        n[cat] = n.get(cat, 0) + 1
        kept.append((name, cat, co))
    ps = kept
    pois = []
    for name, cat, co in ps:
        p = {"name": name, "cat": cat, "match": name.lower()[:22]}
        if co:
            p["lat"], p["lon"] = round(co[0], 6), round(co[1], 6)
        else:
            p["geo"] = "%s %s" % (name, cityname(inner))  # geocode later
        pois.append(p)
    pre, post = embed_anchors(html, sid, order)
    cfg = {"slug": sid, "kicker": kicker, "city": cityname(inner),
           "osm": ".tmp/%s_osm.json" % sid, "article": article_path(slug),
           "embed": {"pre": pre, "post": post},
           "hint": ["Touch to explore", "Double-tap to open in Google Maps"],
           "desktop": {"pad": 1.4}, "mobile": {"pad": 1.2, "init_w": 0.82, "init_x": 0.5, "init_y": 0.5},
           "pois": pois}
    if sid in SEA_BG:
        cfg["sea_bg"] = True
    return cfg

def main():
    slug = sys.argv[1]; mode = sys.argv[2]
    html = open(f"{ROOT}/{article_path(slug)}", encoding="utf-8").read()
    secs = sections(html)
    order = list(secs.keys())
    kicker = slug.replace('-', ' ').title() if slug != "north-macedonia" else "North Macedonia"
    if slug == "turkiye": kicker = "Türkiye"
    if slug == "usa": kicker = "USA"
    if mode == "report":
        for sid, (inner, _, _) in secs.items():
            if sid in ("general", "other", "other-stops", "other-places"): continue
            ps = pois_for(inner)
            haveco = sum(1 for _, _, c in ps if c)
            print(f"{sid:22} pois={len(ps):2}  coords={haveco:2}  needgeo={len(ps)-haveco:2}  "
                  f"cats={{{','.join(sorted(set(c for _,c,_ in ps)))}}}")
    elif mode == "config":
        for sid in sys.argv[3:]:
            cfg = build_config(slug, sid, secs[sid][0], html, order, kicker)
            path = f"{ROOT}/tools/city_maps/{sid}.json"
            json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            ng = sum(1 for p in cfg["pois"] if "geo" in p)
            print(f"wrote {sid}.json  ({len(cfg['pois'])} pois, {ng} need geocode)  sea_bg={cfg.get('sea_bg',False)}")
    elif mode == "geoqueries":  # print all pending geocode queries across given configs
        for sid in sys.argv[3:]:
            cfg = json.load(open(f"{ROOT}/tools/city_maps/{sid}.json", encoding="utf-8"))
            for p in cfg["pois"]:
                if "geo" in p:
                    print("%s\t%s" % (sid, p["geo"]))
    elif mode == "finalize":  # drop outliers, then grid-search center+pad so pins clear the top-right legend
        import statistics, math
        def merc(la, lo):
            return (math.radians(lo), math.log(math.tan(math.pi / 4 + math.radians(la) / 2)))
        for sid in sys.argv[3:]:
            path = f"{ROOT}/tools/city_maps/{sid}.json"
            cfg = json.load(open(path, encoding="utf-8"))
            pts = [p for p in cfg["pois"] if "lat" in p]
            if len(pts) < 1:
                continue
            def km2(p, la, lo):
                return math.hypot((p["lat"] - la) * 111.0, (p["lon"] - lo) * 111.0 * math.cos(math.radians(la)))
            # iteratively drop the farthest-from-median POI until the cluster fits within ~6km
            kp = list(pts)
            floor = max(2, math.ceil(len(pts) * 0.45))
            while len(kp) > floor:
                mla = statistics.median(p["lat"] for p in kp); mlo = statistics.median(p["lon"] for p in kp)
                far = max(kp, key=lambda p: km2(p, mla, mlo))
                if km2(far, mla, mlo) <= 6.0:
                    break
                kp.remove(far)
            keep = [p for p in cfg["pois"] if "lat" not in p or p in kp]
            dropped = [p["name"] for p in cfg["pois"] if "lat" in p and p not in kp]
            cfg["pois"] = keep
            if len(kp) < 3:                            # tiny cluster: just center, keep default pad
                cfg["center"] = {"lat": round(sum(p["lat"] for p in kp) / len(kp), 6),
                                 "lon": round(sum(p["lon"] for p in kp) / len(kp), 6)}
                json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                print(f"{sid}: {len(keep)} pois (small), centered"); continue
            # legend geometry (desktop 760x470): height grows with rows (headers + pins)
            cats = [p["cat"] for p in kp]
            nrows = len(kp) + len(set(cats))
            W, H = 760, 470
            LEG_L, LEG_R, LEG_T, LEG_B = 500, 752, 6, 6 + min(H - 20, 24 + nrows * 16.5)
            mx = [merc(p["lat"], p["lon"]) for p in kp]
            cx0 = sum(x for x, y in mx) / len(mx); cy0 = sum(y for x, y in mx) / len(mx)
            hxs = max(abs(x - cx0) for x, y in mx) or 1e-6
            hys = max(abs(y - cy0) for x, y in mx) or 1e-6
            best = None
            for pad in (1.25, 1.4, 1.55, 1.75, 1.95):
                for bx in (0, .2, .4, -.15):          # +east bias -> pins shift west (off the right-side legend)
                    for by in (0, .2, .4, -.15):      # +north bias -> pins shift down (below the legend)
                        cx = cx0 + bx * hxs; cy = cy0 + by * hys
                        hx = (max(abs(x - cx) for x, y in mx) * pad) or 1e-6
                        hy = (max(abs(y - cy) for x, y in mx) * pad) or 1e-6
                        sc = min(W / (2 * hx), H / (2 * hy))
                        under = 0
                        for x, y in mx:
                            px = W / 2 + (x - cx) * sc; py = H / 2 - (y - cy) * sc - 22
                            if LEG_L <= px <= LEG_R and LEG_T <= py <= LEG_B:
                                under += 1
                        score = (under, pad, abs(bx) + abs(by))
                        if best is None or score < best[0]:
                            best = (score, cx, cy, pad)
            _, cx, cy, pad = best
            # convert center merc back to lat/lon
            clon = math.degrees(cx)
            clat = math.degrees(2 * math.atan(math.exp(cy)) - math.pi / 2)
            cfg["center"] = {"lat": round(clat, 6), "lon": round(clon, 6)}
            cfg.setdefault("desktop", {})["pad"] = pad
            json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"{sid}: {len(keep)} pois, pad {pad}, under-legend {best[0][0]}, dropped {dropped if dropped else '[]'}")
    elif mode == "fill":  # fill coords from .tmp/geo.json {"query": [lat,lon]}
        geo = json.load(open(f"{ROOT}/.tmp/geo.json", encoding="utf-8-sig"))
        for sid in sys.argv[3:]:
            path = f"{ROOT}/tools/city_maps/{sid}.json"
            cfg = json.load(open(path, encoding="utf-8"))
            miss = []
            for p in cfg["pois"]:
                if "geo" in p:
                    g = geo.get(p["geo"])
                    if g:
                        p["lat"], p["lon"] = round(g[0], 6), round(g[1], 6); del p["geo"]
                    else:
                        miss.append(p["name"])
            cfg["pois"] = [p for p in cfg["pois"] if "geo" not in p]  # drop un-geocodable
            json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"{sid}: filled; dropped {len(miss)} un-geocoded {miss if miss else ''}")

if __name__ == "__main__":
    main()
