#!/usr/bin/env python3
"""Re-embed city maps into their articles — restores maps an HTML editor stripped
(editors drop the mobile pin overlay + the pan/zoom <script> when they re-save a page).
Run this after editing an article and the maps are whole again.

    python tools/reembed_all.py                 # every city map
    python tools/reembed_all.py finland sweden  # only maps whose article path contains one of these
    python tools/reembed_all.py --check         # report damaged maps, embed nothing

A map is "damaged" if its mobile variant has fewer pins than its desktop variant.
"""
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
args = sys.argv[1:]
CHECK = "--check" in args
filters = [a for a in args if not a.startswith("-")]


def damaged(article, slug):
    """True if the embedded map for `slug` is missing its mobile pins (editor-stripped)."""
    try:
        s = open(os.path.join(ROOT, article), encoding="utf-8").read()
    except FileNotFoundError:
        return None
    for fig in re.findall(r'<figure class="citymap-fig">.*?</figure>', s, re.DOTALL):
        if "city-maps/%s.png" % slug not in fig:
            continue
        desk = re.search(r'<div class="citymap desk">.*?<div class="citymap mob"', fig, re.DOTALL)
        mob = re.search(r'<div class="citymap mob".*', fig, re.DOTALL)
        dc = len(re.findall(r'class="cmpin"', desk.group(0))) if desk else 0
        mc = len(re.findall(r'class="cmpin"', mob.group(0))) if mob else 0
        return dc != mc or mc == 0
    return None  # not embedded


def main():
    n_ok = n_dmg = 0
    for cfg_path in sorted(glob.glob(os.path.join(ROOT, "tools", "city_maps", "*.json"))):
        cfg = json.load(open(cfg_path, encoding="utf-8"))
        slug, art = cfg["slug"], cfg.get("article", "")
        if filters and not any(f in art for f in filters):
            continue
        dmg = damaged(art, slug)
        if dmg is None:
            continue
        if dmg:
            n_dmg += 1
        if CHECK:
            print("%-4s %-16s %s" % ("DMG" if dmg else "ok", slug, art))
            continue
        if not dmg:
            print("ok   %-16s (already whole)" % slug)
            continue
        r = subprocess.run([PY, os.path.join(ROOT, "tools", "city_map.py"), cfg_path, "--embed"],
                           capture_output=True, text=True)
        ok = "embedded into" in r.stdout
        n_ok += ok
        print("%-4s %-16s -> %s" % ("FIX" if ok else "ERR", slug, art))
    print("\n%d damaged%s" % (n_dmg, "" if CHECK else "; %d re-embedded" % n_ok))


if __name__ == "__main__":
    main()
