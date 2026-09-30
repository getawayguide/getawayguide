"""Merge .tmp/<slug>_osm_tile_*.json into .tmp/<slug>_osm.json (elements deduped by type+id)."""
import glob, json, sys
slug = sys.argv[1]
seen, els = set(), []
tiles = sorted(glob.glob(f".tmp/{slug}_osm_tile_*.json"))
for f in tiles:
    for e in json.load(open(f, encoding="utf-8-sig"))["elements"]:
        k = (e["type"], e["id"])
        if k not in seen:
            seen.add(k); els.append(e)
json.dump({"elements": els}, open(f".tmp/{slug}_osm.json", "w", encoding="utf-8"))
print(slug, len(tiles), "tiles,", len(els), "elements")
