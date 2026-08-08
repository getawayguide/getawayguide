#!/usr/bin/env python3
"""Look up real Google-Maps coordinates for a place by name.

City-map configs need lat/lon for every pin, but hotels and hostels are usually linked in an
article by a booking URL (Hostelworld, Expedia, Booking) that carries no coordinates - so
accommodation used to get dropped from maps, or hand-looked-up one at a time.

This resolves a name the same way tools/resolve_draft_maps.py does (headless Chromium, follow
Google's client-side redirect to /maps/place/, click the first result if it shows a list) and
shares the same .tmp/gmaps_resolved.json cache, so a place resolved once is instant forever.

  python tools/resolve_place.py "Hostel Angelina Dubrovnik"
  python tools/resolve_place.py --add dubrovnik "Hostel Angelina Dubrovnik" "Hostel Angelina"
        -> resolves AND appends it to tools/city_maps/dubrovnik.json as a `stay` pin

Import it from other tools:  from resolve_place import resolve_sync
"""
import argparse, asyncio, json, os, re, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".tmp", "gmaps_resolved.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _load():
    try: return json.load(open(CACHE, encoding="utf-8-sig"))
    except Exception: return {}


def _save(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(c, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def place_coords(url):
    """(lat, lon) of the place a /maps/place/ URL is ABOUT.

    A Maps URL can carry several !3d<lat>!4d<lon> pairs. Google nests them CONTEXT FIRST,
    SUBJECT LAST: an adjacent `!2s<label>` names the context (usually the city, sometimes a
    neighbouring business), and the subject's own coords follow the final `!1s<feature-id>`.
    Taking the first pair returns a different place entirely - that is how Oaxaca's
    "La Popular" ended up pinned on Monte Alban, 5km away.

    Always take the LAST pair. An earlier version picked the pair nearest the `/@` viewport
    on the theory that the viewport tracks the subject; it doesn't when Google frames the
    shot on the city instead, and that was wrong on 132 of the 467 multi-pair links on this
    site (Sombor resolving to Novi Sad, 81km; an abandoned Soviet plane to a restaurant,
    41km). The `/@` viewport is only a fallback for URLs carrying no pair at all.
    """
    pairs = re.findall(r"!3d(-?[\d.]+)!4d(-?[\d.]+)", url)
    if pairs:
        return float(pairs[-1][0]), float(pairs[-1][1])
    at = re.search(r"/@(-?[\d.]+),(-?[\d.]+)", url)
    return (float(at.group(1)), float(at.group(2))) if at else None


async def resolve(queries, use_cache=True):
    """{query: {ok, url, lat, lon, name}} - cached entries are returned without a browser."""
    cache = _load()
    todo = [q for q in queries if not (use_cache and cache.get(q, {}).get("ok"))]
    if todo:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            br = await pw.chromium.launch()
            ctx = await br.new_context(locale="en-US", user_agent=UA)
            await ctx.add_cookies([{"name": "CONSENT", "value": "YES+cb",
                                    "domain": ".google.com", "path": "/"}])
            pg = await ctx.new_page()
            for q in todo:
                rec = {"ok": False}
                try:
                    await pg.goto("https://www.google.com/maps/search/?api=1&query=" +
                                  urllib.parse.quote_plus(q) + "&hl=en",
                                  wait_until="domcontentloaded", timeout=45000)
                    for _ in range(40):
                        await pg.wait_for_timeout(250)
                        if "/maps/place/" in pg.url: break
                    if "/maps/place/" not in pg.url:      # a results list -> take the first hit
                        try:
                            first = pg.locator("a.hfpxzc").first
                            await first.wait_for(state="attached", timeout=6000)
                            await first.click(timeout=6000)
                            for _ in range(28):
                                await pg.wait_for_timeout(250)
                                if "/maps/place/" in pg.url: break
                        except Exception:
                            pass
                    if "/maps/place/" in pg.url:
                        u = pg.url
                        ll = place_coords(u)
                        rec = {"ok": bool(ll), "url": u,
                               "lat": ll[0] if ll else None,
                               "lon": ll[1] if ll else None,
                               "name": urllib.parse.unquote(
                                   u.split("/maps/place/")[1].split("/@")[0]).replace("+", " ")}
                except Exception as e:
                    rec = {"ok": False, "err": str(e)[:90]}
                cache[q] = rec
            await br.close()
        _save(cache)
    return {q: cache.get(q, {"ok": False}) for q in queries}


def resolve_sync(queries, use_cache=True):
    return asyncio.run(resolve(list(queries), use_cache))


def add_to_map(slug, query, label, cat="stay"):
    rec = resolve_sync([query])[query]
    if not rec.get("ok"):
        print("could not resolve: %s" % query); return False
    p = os.path.join(ROOT, "tools", "city_maps", "%s.json" % slug)
    cfg = json.load(open(p, encoding="utf-8"))
    if any(q["name"].lower() == label.lower() for q in cfg["pois"]):
        print("%s already on the %s map" % (label, slug)); return False
    # Store the resolved /maps/place/ URL as the pin's explicit href too. Accommodation is
    # usually linked in the article by a booking URL (Hostelworld, Expedia), so city_map's
    # href_for() finds no Google-Maps link to reuse and falls back to a /maps/search/ URL -
    # the same "searches for the name instead of the actual place" problem drafts have.
    cfg["pois"].append({"name": label, "cat": cat, "match": label.lower()[:22],
                        "lat": round(rec["lat"], 6), "lon": round(rec["lon"], 6),
                        "href": rec["url"]})
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("added %-28s -> %s  (%.6f, %.6f)  [%s]"
          % (label, slug, rec["lat"], rec["lon"], rec.get("name", "")[:40]))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", metavar="SLUG", help="also append to tools/city_maps/<SLUG>.json")
    ap.add_argument("--cat", default="stay", choices=["see", "eat", "stay"])
    ap.add_argument("query")
    ap.add_argument("label", nargs="?", help="pin label (defaults to the query)")
    a = ap.parse_args()
    if a.add:
        add_to_map(a.add, a.query, a.label or a.query, a.cat)
    else:
        r = resolve_sync([a.query])[a.query]
        print(json.dumps(r, ensure_ascii=False, indent=1) if r.get("ok") else "could not resolve")


if __name__ == "__main__":
    main()
