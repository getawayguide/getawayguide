#!/usr/bin/env python3
"""Give city-map pins an explicit `href` so rebuilds stop falling back to a Maps SEARCH url.

city_map.py resolves a pin's link as: explicit `href` > a matching Google-Maps link in the
article > a generic `/maps/search/?api=1&query=<name> <city>`. That last fallback lands the
reader on a search RESULTS page instead of the actual pin, and it fires whenever the article
links a place by something other than Maps (a hostel linked to Hostelworld, a tour linked to
GetYourGuide) or by wording that doesn't contain the pin's `match` string.

resolve_draft_maps.py fixes the links already sitting in the HTML. This fixes the SOURCE, so
the next `city_map.py --embed` doesn't reintroduce them. It resolves nothing itself - it reads
the .tmp/gmaps_resolved.json cache both tools share, so run the resolver first.

  python tools/backfill_pin_hrefs.py --dry-run     # what would change
  python tools/backfill_pin_hrefs.py               # write hrefs into the configs
  python tools/backfill_pin_hrefs.py --drafts      # include configs pointing at Drafts/
"""
import argparse, glob, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".tmp", "gmaps_resolved.json")


def load_cache():
    try:
        return json.load(open(CACHE, encoding="utf-8-sig"))
    except Exception:
        sys.exit("no cache at %s - run tools/resolve_draft_maps.py first" % CACHE)


def article_links(path):
    """{lowercased link text: url} for every Google-Maps link in the article."""
    try:
        html = open(path, encoding="utf-8").read()
    except OSError:
        return {}
    out = {}
    for u, t in re.findall(
            r'<a\b[^>]*href="(https://www\.google\.com/maps/place/[^"]+)"[^>]*>(.*?)</a>',
            html, re.S):
        txt = re.sub(r"<[^>]+>", "", t).strip().lower()
        if txt:
            out.setdefault(txt, u.replace("&amp;", "&"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--drafts", action="store_true", help="also touch configs whose article is a draft")
    a = ap.parse_args()

    cache = load_cache()
    hit = miss = files = 0
    unresolved = []

    for p in sorted(glob.glob(os.path.join(ROOT, "tools", "city_maps", "*.json"))):
        cfg = json.load(open(p, encoding="utf-8"))
        art = cfg.get("article", "")
        if art.startswith("Drafts/") and not a.drafts:
            continue
        links = article_links(os.path.join(ROOT, art)) if art else {}
        city = cfg.get("city", "")
        changed = []
        for poi in cfg["pois"]:
            if poi.get("href"):
                continue
            key = poi.get("match", poi["name"].lower())
            # the article already links it -> city_map would find this itself, leave it alone
            if any(key in t for t in links):
                continue
            q = "%s %s" % (poi["name"], city)
            rec = cache.get(q)
            if rec and rec.get("ok") and rec.get("url"):
                poi["href"] = rec["url"]
                changed.append(poi["name"]); hit += 1
            else:
                unresolved.append((os.path.basename(p)[:-5], q)); miss += 1
        if changed:
            files += 1
            print("  %-22s %d pin(s): %s" % (os.path.basename(p)[:-5], len(changed),
                                             ", ".join(changed[:4]) + ("…" if len(changed) > 4 else "")))
            if not a.dry_run:
                json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("\n%s %d pin href(s) across %d config(s)"
          % ("would write" if a.dry_run else "wrote", hit, files))
    if unresolved:
        print("%d pin(s) not in the cache (left as-is):" % miss)
        for s, q in unresolved[:15]:
            print("   %-20s %s" % (s, q))
        if len(unresolved) > 15:
            print("   … and %d more" % (len(unresolved) - 15))


if __name__ == "__main__":
    main()
