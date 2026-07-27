#!/usr/bin/env python3
"""Embed (or re-embed) an itinerary map SVG into an article page.

Automates the repetitive embed steps so map iteration stays token-cheap:
  - reads the built SVG from .tmp/previews/<slug>-map.svg
  - makes the <svg> responsive (width:100%)
  - computes the FONT-CONSISTENT embed width = round(K * viewBox_width),
    K = 720/946 (the El Salvador standard) so text renders the same px on
    every map regardless of country shape  (see feedback_itinerary_map_protocol)
  - ensures the page loads Montserrat (the SVG's font)
  - REPLACES the existing <figure class="itinerary-map"> in place if present
    (the common re-embed case during label tuning), else INSERTS a titled
    figure after --after "anchor text"

Typical use:
  # build + visually QA first:
  python tools/itinerary_map.py tools/itinerary_maps/argentina.json --shot
  # first embed (needs an anchor to insert after):
  python tools/embed_itinerary_map.py argentina argentina/field-notes.html \\
      --after "to fully explore the Patagonia region.</p>"
  # any later tweak (rebuild, then just re-run; it replaces in place):
  python tools/embed_itinerary_map.py argentina argentina/field-notes.html
"""
import argparse, pathlib, re, sys

K = 720 / 946           # Font-size standard: px per SVG unit (El Salvador 720/946 =
                        # Egypt 451/592). Base embed width = K*vw.
MAX_VH = 531            # Egypt's viewBox height = the max rendered height (~404px).
                        # A taller map is embedded NARROWER so its height caps at
                        # Egypt's. The builder (itinerary_map.render) pre-scales that
                        # map's annotations by vh/MAX_VH, so this width cap leaves the
                        # TEXT at Egypt's px size — only the country outline shrinks.
                        # Net: every map = same font size AND height <= Egypt.
TITLE = "Suggested Route Overview"
FIG_STYLE = "margin:0 auto 2.25rem;max-width:{w}px;border-radius:4px;overflow:hidden"
TITLE_STYLE = "text-align:center;max-width:{w}px;margin:1.75rem auto .5rem"
MONT = "&amp;family=Montserrat:wght@400;500;600;700"


def load_svg(slug):
    p = pathlib.Path(".tmp/previews") / f"{slug}-map.svg"
    if not p.exists():
        sys.exit(f"! {p} not found — build it first: "
                 f"python tools/itinerary_map.py tools/itinerary_maps/{slug}.json --shot")
    svg = p.read_text(encoding="utf-8").strip()
    if 'style="width:100%' not in svg:
        svg = re.sub(r"<svg ", '<svg style="width:100%;height:auto;display:block" ', svg, count=1)
    m = re.search(r'viewBox="[\d.\-]+ [\d.\-]+ ([\d.]+) ([\d.]+)"', svg)
    if not m:
        sys.exit("! could not parse viewBox from SVG")
    return svg, float(m.group(1)), float(m.group(2))


def ensure_montserrat(html):
    if "family=Montserrat" in html:
        return html, False
    if "&amp;display=swap" in html:
        return html.replace("&amp;display=swap", MONT + "&amp;display=swap", 1), True
    sys.exit("! could not add Montserrat (no &display=swap in the font link) — add it by hand")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("target", help="article html path")
    ap.add_argument("--after", help="insert the figure right after this substring (first embed only)")
    ap.add_argument("--title", default=TITLE)
    ap.add_argument("--no-title", action="store_true")
    ap.add_argument("--width", type=int, help="override the computed embed width (px)")
    args = ap.parse_args()

    svg, vw, vh = load_svg(args.slug)
    # height-capped width; the builder already scaled a tall map's annotations so
    # the text still lands at Egypt's px size after this cap
    w = args.width or round(K * vw * min(1.0, MAX_VH / vh))
    p = pathlib.Path(args.target)
    html = p.read_text(encoding="utf-8")
    html, mont_added = ensure_montserrat(html)

    existing = re.search(r'<figure class="itinerary-map"[^>]*>', html)
    if existing:
        start = existing.start()
        end = html.index("</figure>", existing.end()) + len("</figure>")
        # widen the region back to a preceding fn-sub-hd title, if adjacent
        head = html[:start]
        tdiv = list(re.finditer(r'<div class="fn-sub-hd"[^>]*>[^<]*</div>\s*$', head))
        region_start = tdiv[-1].start() if tdiv else start
        region = html[region_start:end]
        new_fig = re.sub(r'(<figure class="itinerary-map"[^>]*>).*?(</figure>)',
                         lambda mo: mo.group(1) + svg + mo.group(2), region, count=1, flags=re.S)
        new_fig = re.sub(r"max-width:\d+px", f"max-width:{w}px", new_fig)
        html = html[:region_start] + new_fig + html[end:]
        action = "replaced"
    elif args.after:
        if html.count(args.after) != 1:
            sys.exit(f"! --after anchor must occur exactly once (found {html.count(args.after)})")
        title = "" if args.no_title else (
            f'\n    <div class="fn-sub-hd" style="{TITLE_STYLE.format(w=w)}">{args.title}</div>')
        fig = (title +
               f'\n    <figure class="itinerary-map" style="{FIG_STYLE.format(w=w)}">{svg}</figure>')
        html = html.replace(args.after, args.after + fig, 1)
        action = "inserted"
    else:
        # auto: insert in the General section, before its first <ul> or fn-sub-hd
        g = re.search(r'<div class="fn-section" id="general">', html)
        if not g:
            sys.exit("! no General section — pass --after to insert manually")
        gstart = g.end()
        gm = re.search(r'<div class="fn-section"', html[gstart:])
        gend = gstart + gm.start() if gm else len(html)
        seg = html[gstart:gend]
        cands = [m.start() for m in (re.search(r'<ul>', seg),
                                     re.search(r'<div class="fn-sub-hd"', seg)) if m]
        if not cands:
            sys.exit("! could not find an insertion point in General — pass --after")
        ins = gstart + min(cands)
        ls = html.rfind("\n", 0, ins) + 1
        indent = html[ls:ins]
        title = "" if args.no_title else (
            f'{indent}<div class="fn-sub-hd" style="{TITLE_STYLE.format(w=w)}">{args.title}</div>\n')
        block = title + f'{indent}<figure class="itinerary-map" style="{FIG_STYLE.format(w=w)}">{svg}</figure>\n'
        html = html[:ls] + block + html[ls:]
        action = "auto-inserted"

    p.write_text(html, encoding="utf-8")
    print(f"{action}: {args.target}  vw={vw:.0f} vh={vh:.0f}  max-width={w}px  "
          f"height~{round(w*vh/vw)}px  montserrat={'added' if mont_added else 'ok'}")


if __name__ == "__main__":
    main()
