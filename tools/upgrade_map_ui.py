#!/usr/bin/env python3
"""Re-sync the city-map INTERACTION LAYER (the <style> + <script> that wrap every embedded
map) with the current CSS/JS constants in tools/city_map.py — without re-rendering anything.

The map fragment written by city_map.build() is always:
    <style>\\n{CSS}</style>\\n<figure class="citymap-fig">...</figure>\\n{JS}\\n
Only the style/script blocks are swapped, so the base PNGs, pin geometry, framing and legend
markup stay byte-identical. Use this to roll a gesture/CSS fix out to every existing map.

  python tools/upgrade_map_ui.py --dry-run     # report what would change
  python tools/upgrade_map_ui.py               # apply to every article with a map
"""
import argparse, glob, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import city_map

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIG = '<figure class="citymap-fig">'


def articles():
    """Every live + draft field-notes page (and any other html) holding a city map."""
    seen = []
    for pat in ("*/field-notes.html", "Drafts/*/field-notes.html", "*.html"):
        for p in glob.glob(os.path.join(ROOT, pat)):
            if p not in seen:
                seen.append(p)
    return [p for p in seen if FIG in open(p, encoding="utf-8").read()]


def upgrade(html):
    """Swap each fragment's style+script for the current constants. Returns (html, n_maps)."""
    new_style = "<style>\n" + city_map.CSS + "</style>"
    new_js = city_map.JS
    starts = []
    i = html.find(FIG)
    while i >= 0:
        starts.append(i)
        i = html.find(FIG, i + 1)
    n = 0
    for fs in reversed(starts):                      # back-to-front keeps earlier offsets valid
        fe = html.find("</figure>", fs)
        if fe < 0:
            continue
        fe += len("</figure>")
        ss = html.find("<script>", fe)
        se = html.find("</script>", ss) if ss >= 0 else -1
        # the style/script must bracket this figure with nothing but whitespace between
        # (fragments picked up an indent when embedded into some articles)
        if ss < 0 or se < 0 or html[fe:ss].strip():
            continue
        se += len("</script>")
        ye = html.rfind("</style>", 0, fs)
        ys = html.rfind("<style>", 0, ye) if ye >= 0 else -1
        if ys < 0 or html[ye + len("</style>"):fs].strip():
            continue
        html = html[:ss] + new_js + html[se:]        # script first (later in the string)
        html = html[:ys] + new_style + html[ye + len("</style>"):]
        n += 1
    return html, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    tot_f = tot_m = 0
    for p in articles():
        cur = open(p, encoding="utf-8").read()
        new, n = upgrade(cur)
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if n == 0:
            print("  !! %-44s no upgradable fragment found" % rel)
            continue
        if new != cur:
            if not a.dry_run:
                open(p, "w", encoding="utf-8", newline="").write(new)
            print("  %-44s %d map(s)%s" % (rel, n, "  [dry-run]" if a.dry_run else ""))
            tot_f += 1
        else:
            print("  %-44s %d map(s)  already current" % (rel, n))
        tot_m += n
    print("\n%s %d file(s), %d map(s) total" % ("would update" if a.dry_run else "updated", tot_f, tot_m))


if __name__ == "__main__":
    main()
