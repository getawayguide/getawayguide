#!/usr/bin/env python3
"""A city map drawn on a PAINTED base (an illustrated panorama) instead of OpenStreetMap streets.

The Meteora guide uses the official bird's-eye painting: it shows the rock pillars and the monasteries
the way a street map cannot (Kevin, 2026-10-04: "that was the most helpful part"). Pins, names, the
title, the legend and the hiking route are ours, laid on top from a config Kevin arranged by hand in
an artifact (drag + sliders, "Copy settings").

It emits the SAME markup as tools/city_map.py (figure.citymap-fig > .citymap.desk + .citymap.mob),
so the page's existing city-map CSS and script drive it: hover sync between pins and the legend, and
on a phone the pan / pinch / tap-for-name gesture map. Differences from a street map:
  * links are in-page anchors ("#great-meteoron"): a pin jumps to its section of the article
  * the legend is a band UNDER the map, never on it (the painting has no empty corner to give up)
  * names are drawn beside the pins, and the hiking route is a dashed loop

  python tools/painted_map.py tools/painted_maps/meteora.json            # images + preview
  python tools/painted_map.py tools/painted_maps/meteora.json --embed    # ... and write it into the article

Config (positions in the 760 x 470 desktop frame): slug, kicker, city, source (the archived original under
Images/, never served), crop [x0,y0,x1,y1] in source pixels, credit, article, style{text,halo,pin,trail,
title,color,dash,title_at}, mobile{aspect,init_x,hint[]}, trail{name,anchor,points[]},
places[{id,name,cat see|eat|stay,anchor,xy,label?,label_anchor?}], towns[{name,anchor,xy}].
"""
import argparse, html, io, json, os, re, sys
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMGDIR = os.path.join(ROOT, "Images", "web", "city-maps")
PREV = os.path.join(ROOT, ".tmp", "previews")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import city_map as cm                                    # its CSS + script are the ones every page already carries

W, H = cm.DESK_W, cm.DESK_H                              # 760 x 470, the same frame as every city map
INK, LAND, KICKER = "#1C2821", "#F4F2EC", "#245A43"
PIN = "M0 0 C-3.4 -8 -8 -11.4 -8 -17.7 A8 8 0 1 1 8 -17.7 C8 -11.4 3.4 -8 0 0 Z"
CAT = {"see": ("ATTRACTIONS", "#245A43", "#ffffff", "#245A43"),
       "eat": ("RESTAURANTS & BARS", "#C8DDD5", "#1C2821", "#245A43"),
       "stay": ("ACCOMMODATION", "#6B716C", "#ffffff", "#6B716C")}
ONE_X = 1350                                             # the 1x file for a 729 px column (1.85x: see CLAUDE.md)

# Only what differs from a street map. Scoped to .cm-painted so no other city map on the site changes.
CSS = """
.citymap-fig.cm-painted .cmmap{border-radius:0;box-shadow:none}
.citymap-fig.cm-painted .cmmap picture{display:block}
.citymap-fig.cm-painted .cmmap picture+svg{position:absolute;top:0;left:0}
.citymap-fig.cm-painted .citymap.desk{border-radius:7px;box-shadow:0 4px 20px rgba(0,0,0,.13);background:#F4F2EC;overflow:hidden}
.citymap-fig.cm-painted .cmkey{position:static;width:auto;background:none;backdrop-filter:none;-webkit-backdrop-filter:none;border-radius:0;box-shadow:none;
  padding:8px 14px 9px;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:0 12px;align-items:start}
.citymap-fig.cm-painted .citymap.desk .cmkey{grid-template-columns:minmax(0,1fr) minmax(0,1.06fr) minmax(0,1.14fr) minmax(0,1.08fr) minmax(0,1.12fr) minmax(0,1.1fr);gap:0 10px}
.citymap-fig.cm-painted .cmhead{white-space:nowrap}
.citymap-fig.cm-painted .cmkey .nm{font-size:10.5px;font-weight:500!important}
/* badge colours by class, not inline: the editor's paste clean-up strips a dark inline color on save */
.citymap-fig.cm-painted .num.c-see{background:#245A43;color:#fff}
.citymap-fig.cm-painted .num.c-eat{background:#C8DDD5;color:#1C2821}
.citymap-fig.cm-painted .num.c-stay{background:#6B716C;color:#fff}
.citymap-fig.cm-painted .cmhead.sp{visibility:hidden}
.citymap-fig.cm-painted .cmrow{align-items:flex-start}
.citymap-fig.cm-painted .cmrow .sw{flex:0 0 22px;height:15px;display:inline-flex;align-items:center}
.citymap-fig.cm-painted .cmlab,.citymap-fig.cm-painted .cmdl{pointer-events:none}
.citymap-fig.cm-painted a:hover .cmlab,.citymap-fig.cm-painted .cmpin.active .cmlab{fill:#0B3FB8}
.citymap-fig.cm-painted .cmtrail-hit{fill:none;stroke:transparent;pointer-events:stroke;cursor:pointer}
.citymap-fig.cm-painted .cmcredit{font:400 9.5px/1.3 Hanken Grotesk,Helvetica,Arial,sans-serif;color:#8B978F;text-align:right;padding:5px 2px 0}
/* phone: the painting is landscape, so the window is wider than a street map's; the title sits above the
   map and the legend under it, as ordinary blocks, and the gesture map is only the picture */
.citymap-fig.cm-painted .cm-mobhead,.citymap-fig.cm-painted .cm-mobkey{display:none;max-width:540px;margin:0 auto;background:#F4F2EC}
.citymap-fig.cm-painted .cm-mobhead{padding:12px 14px 9px;border-radius:7px 7px 0 0}
.citymap-fig.cm-painted .cm-mobhead .cm-ov-kick,.citymap-fig.cm-painted .cm-mobhead .cm-ov-title,.citymap-fig.cm-painted .cm-mobhead .cm-ov-hint{text-shadow:none}
.citymap-fig.cm-painted .cm-mobhead .cm-ov-title{font-size:32px}
.citymap-fig.cm-painted .cm-mobhead .cm-ov-hint{max-width:none}
.citymap-fig.cm-painted .cm-mobkey{border-radius:0 0 7px 7px}
.citymap-fig.cm-painted .cm-mobkey .cmkey{grid-template-columns:repeat(2,minmax(0,1fr));padding:9px 14px 11px}
.citymap-fig.cm-painted .cm-mobkey .cmrow{min-height:22px;padding-top:3px;padding-bottom:3px}
.citymap-fig.cm-painted .cm-mobkey .nm{font-size:12.5px}
.citymap-fig.cm-painted .cm-mobkey .num{flex-basis:17px;width:17px;height:17px;font-size:9.5px}
.citymap-fig.cm-painted .citymap.mob .cmmap{aspect-ratio:__ASPECT__;background:#CBD8B8}
.citymap-fig.cm-painted .cmworld{width:760px}
@media (max-width:700px){ .citymap-fig.cm-painted .cm-mobhead,.citymap-fig.cm-painted .cm-mobkey{display:block} }
"""


def build_images(cfg):
    """The painting, cropped to the frame's shape, at the widths the page draws it. The source is the web's
    best copy (1920 px), so there is no larger file to cut: 1x for the desktop column and the native crop for
    zooming on a phone. A light denoise + unsharp takes the compression speckle off the lettering."""
    src = Image.open(os.path.join(ROOT, cfg["source"]))
    icc = src.info.get("icc_profile")                    # grab BEFORE convert(): see CLAUDE.md
    im = src.convert("RGB").crop(tuple(cfg["crop"]))
    assert abs(im.width / im.height - W / H) < 0.01, "crop must have the frame's shape (%d x %d)" % (W, H)
    im = im.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.UnsharpMask(radius=1.6, percent=95, threshold=2))
    os.makedirs(IMGDIR, exist_ok=True)
    slug, kw = cfg["slug"], ({"icc_profile": icc} if icc else {})
    one = im.resize((ONE_X, round(ONE_X * H / W)), Image.LANCZOS) if im.width > ONE_X else im
    one.save(os.path.join(IMGDIR, slug + "-1x.webp"), quality=84, method=6, **kw)
    one.save(os.path.join(IMGDIR, slug + "-1x.jpg"), quality=84, optimize=True, **kw)
    im.save(os.path.join(IMGDIR, slug + ".webp"), quality=84, method=6, **kw)
    im.save(os.path.join(IMGDIR, slug + ".jpg"), quality=86, optimize=True, **kw)
    return one.width, im.width


def overlay(cfg, mobile):
    st, o = cfg["style"], []
    E = html.escape
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Hanken Grotesk,Helvetica,Arial,sans-serif" style="paint-order:stroke">')
    tr = cfg.get("trail")
    if tr and tr.get("points"):
        pts = " ".join("%g,%g" % (x, y) for x, y in tr["points"])
        dash = f' stroke-dasharray="{st["trail"] * 2.4:g} {st["trail"] * 2:g}"' if st.get("dash") else ""
        o.append(f'<a href="#{E(tr["anchor"])}" aria-label="{E(tr["name"])}"><g class="cmtrail">'
                 f'<polygon points="{pts}" fill="none" stroke="{INK}" stroke-opacity=".55" stroke-width="{st["trail"] + 1.3:g}" stroke-linejoin="round" stroke-linecap="round"/>'
                 f'<polygon points="{pts}" fill="none" stroke="{st["color"]}" stroke-width="{st["trail"]:g}" stroke-linejoin="round" stroke-linecap="round"{dash}/>'
                 f'<polygon class="cmtrail-hit" points="{pts}" stroke-width="9"/></g></a>')
    for t in cfg.get("towns", []):
        x, y = t["xy"]
        o.append(f'<text class="cmdl" x="{x:g}" y="{y:g}" font-size="{st["text"] * 1.08:.1f}" letter-spacing="{st["text"] * .24:.1f}" font-weight="600" '
                 f'fill="{INK}" text-anchor="middle" stroke="{LAND}" stroke-width="{st["halo"]:g}" stroke-linejoin="round">{E(t["name"])}</text>')
    ps = st.get("pin", 1)
    P = cfg["places"]
    for i in range(len(P), 0, -1):                       # high -> low, so the earlier (more important) pins sit on top
        p = P[i - 1]; x, y = p["xy"]; c = CAT[p["cat"]]
        lab = ""
        if p.get("label"):
            dx, dy = p["label"]
            lab = (f'<text class="cmlab" x="{x + dx * ps:.1f}" y="{y + dy * ps:.1f}" text-anchor="{p.get("label_anchor", "start")}" font-size="{st["text"]:g}" '
                   f'font-weight="600" fill="{INK}" stroke="{LAND}" stroke-width="{st["halo"]:g}" stroke-linejoin="round">{E(p["name"])}</text>')
        o.append(f'<a href="#{E(p["anchor"])}"><g class="cmpin" data-i="{i}" data-name="{E(p["name"])}">'
                 f'<g transform="translate({x:g} {y:g}) scale({ps:g})"><path d="{PIN}" transform="translate(0.7 1.5)" fill="#000" opacity="0.2"/>'
                 f'<path d="{PIN}" fill="{c[1]}" stroke="#fff" stroke-width="1.1"/><circle cx="0" cy="-17.7" r="6.4" fill="#fff"/>'
                 f'<text x="0" y="-17.7" text-anchor="middle" dominant-baseline="central" font-size="8.6" font-weight="700" fill="{c[3]}">{i}</text></g>{lab}</g></a>')
    if not mobile:                                       # the phone shows the title above the map instead
        tx, ty = st.get("title_at", [W - 18, 20]); ts = st.get("title", cm.TITLE_PX)
        o.append(f'<text x="{tx:g}" y="{ty:g}" text-anchor="end" font-size="{ts * .27:.1f}" font-weight="600" letter-spacing="{ts * .12:.1f}" fill="{KICKER}" stroke="{LAND}" stroke-width="{ts * .08:.1f}" stroke-linejoin="round">{E(cfg["kicker"])}</text>')
        o.append(f'<text x="{tx:g}" y="{ty + ts * 1.08:.1f}" text-anchor="end" font-family="Newsreader,Georgia,serif" font-size="{ts:g}" font-weight="600" fill="{INK}" stroke="{LAND}" stroke-width="{ts * .1:.1f}" stroke-linejoin="round">{E(cfg["city"])}</text>')
    o.append("</svg>")
    return "".join(o)


def legend(cfg, mobile):
    """A band under the map. Desktop: three rows to a column (a low strip). Phone: one block per group, two across."""
    st, E = cfg["style"], html.escape
    P = cfg["places"]
    row = lambda i, p: (f'<a class="cmrow" data-i="{i}" href="#{E(p["anchor"])}"><span class="num c-{p["cat"]}">{i}</span>'
                        f'<span class="nm">{E(p["name"])}</span></a>')
    head = lambda t, sp=False: f'<div class="cmhead{" sp" if sp else ""}">{E(t)}</div>'
    tr = cfg.get("trail"); trail = ""
    if tr and tr.get("points"):
        d = "M1 9 C6 2 10 13 21 5"
        dash = f' stroke-dasharray="{st["trail"] * 2.4:g} {st["trail"] * 2:g}"' if st.get("dash") else ""
        sw = (f'<svg width="22" height="14" viewBox="0 0 22 14" aria-hidden="true"><path d="{d}" fill="none" stroke="{INK}" stroke-opacity=".55" stroke-width="{st["trail"] + 1.3:g}" stroke-linecap="round"/>'
              f'<path d="{d}" fill="none" stroke="{st["color"]}" stroke-width="{st["trail"]:g}" stroke-linecap="round"{dash}/></svg>')
        trail = head("HIKING") + f'<a class="cmrow" href="#{E(tr["anchor"])}"><span class="sw">{sw}</span><span class="nm">{E(tr["name"])}</span></a>'
    out, last = ['<div class="cmkey">'], None
    for cat in ("see", "eat", "stay"):
        items = [(i, p) for i, p in enumerate(P, 1) if p["cat"] == cat]
        if not items: continue
        step = len(items) if mobile else 3
        for k in range(0, len(items), step):
            out.append('<div class="cmcol">' + head(CAT[cat][0] if k == 0 else ".", k > 0) + "".join(row(i, p) for i, p in items[k:k + step]))
            last = len(out) - 1; out.append("</div>")
    if trail and last is not None: out[last] += trail    # the route's row closes the last column (with Accommodation)
    out.append("</div>")
    return "".join(out)


def fragment(cfg, w1, wfull):
    E, slug, m = html.escape, cfg["slug"], cfg.get("mobile", {})
    depth = cfg["article"].count("/")
    base = "../" * depth + "Images/web/city-maps/" + slug
    aspect = m.get("aspect", 0.8)
    iw = round(aspect * H / W, 4)                        # the whole height of the painting fills the phone's window
    desk_img = (f'<picture><source type="image/webp" srcset="{base}-1x.webp {w1}w, {base}.webp {wfull}w" sizes="729px">'
                f'<img class="cmbase" src="{base}-1x.jpg" srcset="{base}-1x.jpg {w1}w, {base}.jpg {wfull}w" sizes="729px" width="{W}" height="{H}" alt="{E(cfg["city"])} map" loading="lazy"></picture>')
    mob_img = (f'<picture><source type="image/webp" srcset="{base}.webp"><img class="cmbase" src="{base}.jpg" width="{W}" height="{H}" alt="{E(cfg["city"])} map" loading="lazy"></picture>')
    hint = "<br>".join(E(l) for l in m.get("hint", ["Drag to explore, pinch to zoom", "Tap a pin for its name, tap again to jump to it"]))
    credit = f'<div class="cmcredit">{E(cfg["credit"])}</div>' if cfg.get("credit") else ""
    head = (f'<div class="cm-mobhead"><div class="cm-ov-kick">{E(cfg["kicker"])}</div><div class="cm-ov-title">{E(cfg["city"])}</div><div class="cm-ov-hint">{hint}</div></div>')
    fig = (f'<figure class="citymap-fig cm-painted" data-painted="{E(slug)}">'
           f'<div class="citymap desk"><div class="cmmap">{desk_img}{overlay(cfg, False)}</div>{legend(cfg, False)}</div>'
           f'{head}<div class="citymap mob" data-dar="{aspect}" data-iw="{iw}" data-ix="{m.get("init_x", 0.12)}" data-iy="0.5">'
           f'<div class="cmmap"><div class="cmworld">{mob_img}{overlay(cfg, True)}</div></div></div>'
           f'<div class="cm-mobkey">{legend(cfg, True)}</div>{credit}</figure>')
    css = CSS.replace("__ASPECT__", str(aspect))
    return css, fig


def embed(cfg, css, fig):
    """Swap the article's city-map figure for this one, and make sure the page's head carries the city-map
    CSS + this map's own, and the page ends with the CURRENT city-map script (in-page links need it)."""
    p = os.path.join(ROOT, cfg["article"])
    s = io.open(p, encoding="utf-8", newline="").read()
    mine = re.search(r'<figure class="citymap-fig cm-painted" data-painted="%s">.*?</figure>' % re.escape(cfg["slug"]), s, re.S)
    old = mine or re.search(r'<figure class="citymap-fig">.*?</figure>', s, re.S)
    if not old:
        raise SystemExit("embed aborted: no city-map figure in " + cfg["article"])
    s = s[:old.start()] + fig + s[old.end():]
    tag = '<style id="cm-painted">'
    block = tag + css + "</style>"
    if tag in s:
        s = re.sub(r'<style id="cm-painted">.*?</style>', lambda _: block, s, flags=re.S)
    else:
        s = s.replace("</head>", block + "\n</head>", 1)
    if ".citymap-fig{" not in s:                          # a page that never had a city map: the engine's own CSS
        s = s.replace(tag, "<style>\n" + cm.CSS + "</style>\n" + tag, 1)
    js = [m for m in re.finditer(r"<script>(.*?)</script>", s, re.S) if "cmworld" in m.group(1)]
    cur = re.search(r"<script>(.*?)</script>", cm.JS, re.S).group(1)
    if js:
        if js[0].group(1).strip() != cur.strip():
            s = s[:js[0].start(1)] + cur + s[js[0].end(1):]
    else:
        s = s.replace("</body>", cm.JS + "\n</body>", 1)
    io.open(p, "w", encoding="utf-8", newline="").write(s)
    print("embedded into", cfg["article"], "(replaced %s)" % ("its earlier build" if mine else "the street-map figure"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config"); ap.add_argument("--embed", action="store_true")
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8"))
    w1, wfull = build_images(cfg)
    css, fig = fragment(cfg, w1, wfull)
    os.makedirs(PREV, exist_ok=True)
    prev = os.path.join(PREV, cfg["slug"] + ".html")
    rel = fig.replace("../" * cfg["article"].count("/") + "Images/", "../../Images/")
    open(prev, "w", encoding="utf-8").write(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>body{{margin:0;background:#e9e9e3;padding:22px 16px}}{cm.CSS}{css}</style><body>{rel}{cm.JS}</body>')
    for f in sorted(os.listdir(IMGDIR)):
        if f.startswith(cfg["slug"]): print("  %-26s %4d KB" % (f, os.path.getsize(os.path.join(IMGDIR, f)) // 1024))
    print("preview: .tmp/previews/%s.html" % cfg["slug"])
    if a.embed:
        embed(cfg, css, fig)


if __name__ == "__main__":
    main()
