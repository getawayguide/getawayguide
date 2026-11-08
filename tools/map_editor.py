#!/usr/bin/env python3
"""
Itinerary-map label editor — drag the labels/pills of any map and save.

Run:
    python tools/map_editor.py
Then open http://localhost:5002

Pick a map from the sidebar, drag any place label, day count, transport pill,
dot label, airport label, country name, water name or the legend box to reposition
it, then hit Save. Save writes the new offsets back to tools/itinerary_maps/<slug>.json,
rebuilds the SVG and re-embeds it into the article page (same as running
embed_itinerary_map.py). Double-click a country/water name or the legend to drop
its override (back to where the config's lat/lon or corner puts it).

Phone (every map, 2026-11-07): the canvas shows the map as a 393 px phone draws it. A
route map is drawn at the phone column's width (345 px, or the figure's own max-width if
narrower); a map with a "mobile" block shows that build. A city map shows its portrait
gesture window at the opening view the page's script gives it (drag to pan, wheel to
zoom). Moves made in the Phone view change the phone build only: a route map's "mobile"
block (made at the first phone move of a map that had none, so the page gets a second,
phone-only SVG), a city map's district_labels[].mobile spot and pois[].lab_mobile side.
Desktop moves go into the top-level entries as before. "2×" magnifies the phone view.

MAP_EDITOR_PORT=5022 (or a port as the first argument) runs a second instance for
tests beside the suite's :5002.

The canvas shows each map exactly as it will appear on the page (height-capped,
annotations at the standard font size), so what you arrange is what you get.
"""
import json
import os
import re
import sys
import subprocess
from pathlib import Path

from flask import Flask, jsonify, request, Response, send_file

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import itinerary_map as im  # noqa: E402

CFG_DIR = ROOT / "tools" / "itinerary_maps"
CITY_DIR = ROOT / "tools" / "city_maps"
PREV = ROOT / ".tmp" / "previews"
import city_map as cm  # noqa: E402
import embed_itinerary_map as em  # noqa: E402   K and MAX_VH: the figure width a route map gets on the page

# The phone the editor's Phone view stands for (2026-11-07): a 393 px window, whose article column is 345 px
# (.article-body pads 24 px a side under 769 px; measured on egypt, latvia, thessaloniki and north-macedonia).
# A route map's figure is drawn at min(its max-width, the column); a city map's phone window is the column wide.
PHONE_W, PHONE_COL = 393, 345


OVERPASS_PS = r"""
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$bbox = '__BBOX__'
$q = @"
[out:json][timeout:240];
(
  way["highway"]($bbox);
  way["natural"="water"]($bbox);
  way["waterway"]($bbox);
  way["natural"="coastline"]($bbox);
  way["leisure"~"park|garden"]($bbox);
  way["landuse"="recreation_ground"]($bbox);
  way["natural"="wood"]($bbox);
  relation["natural"="wood"]($bbox);
  way["landuse"="forest"]($bbox);
  relation["landuse"="forest"]($bbox);
  relation["natural"="water"]($bbox);
);
out geom;
"@
$delay = 5
for ($i = 0; $i -lt 4; $i++) {
  try {
    $r = Invoke-WebRequest -Uri "https://overpass-api.de/api/interpreter" -Method Post -Body @{data=$q} `
           -UserAgent "getawayguide-citymap/1.0 (+https://getawayguide.io)" -TimeoutSec 300 -UseBasicParsing
    [IO.File]::WriteAllText('__OUT__', $r.Content, (New-Object Text.UTF8Encoding $false))
    Write-Output ("ok " + $r.Content.Length)
    exit 0
  } catch {
    Write-Output ("attempt " + ($i + 1) + " failed: " + $_.Exception.Message)
    Start-Sleep -Seconds $delay; $delay *= 2
  }
}
exit 1
"""


def _ensure_osm_now(cfg_path):
    """The OSM extract a city map is drawn from lives in .tmp, which is disposable, so it is
    often gone. Fetch it again from Overpass (bbox computed by city_map itself) before a
    build; return None when present or fetched, else the reason."""
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    osm = ROOT / cfg["osm"]
    if osm.is_file() and osm.stat().st_size > 1000:
        return None
    try:
        bbox = "%.4f,%.4f,%.4f,%.4f" % cm.fetch_bbox(str(cfg_path))
    except Exception as e:
        return f"could not compute the fetch box: {e}"
    osm.parent.mkdir(parents=True, exist_ok=True)
    ps = (ROOT / ".tmp" / f"_fetch_osm_{cfg['slug']}.ps1")
    ps.write_text(OVERPASS_PS.replace("__BBOX__", bbox).replace("__OUT__", str(osm).replace("'", "''")), encoding="utf-8")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)],
                       capture_output=True, text=True, timeout=1500, creationflags=_NOWIN)
    if r.returncode != 0 or not osm.is_file():
        return "Overpass fetch failed for " + cfg["slug"] + ": " + (r.stdout + r.stderr).strip()[-400:]
    return None


import threading as _th

# every child gets a hidden console of its own: under the pythonw launcher a console program
# (git, PowerShell, python.exe) otherwise opens a visible window (QA 2026-10-08)
_NOWIN = 0x08000000 if __import__("sys").platform == "win32" else 0
_CITY_LOCK = _th.Lock()   # one build at a time: they share the preview files and the rasters
_OSM_LOCKS, _OSM_GUARD = {}, _th.Lock()
IMG = ROOT / "Images" / "web" / "city-maps"
PREV_PNG = ROOT / ".tmp" / "previews" / "city-png"   # base rasters a preview had to draw (never published)


def ensure_osm(cfg_path):
    """One Overpass fetch per city at a time: two opens of a city with no extract started two
    PowerShell fetches of up to 25 minutes each (stress test 2026-10-09). The second waits for the
    first and then finds the file."""
    key = Path(cfg_path).stem
    with _OSM_GUARD:
        lk = _OSM_LOCKS.setdefault(key, _th.Lock())
    with lk:
        return _ensure_osm_now(cfg_path)


def has_base(slug):
    """the published street rasters a preview reuses (then no OSM data is needed at all)"""
    return all((IMG / (slug + x)).is_file() for x in (".png", "-mobile.png"))


def _write_cfg(path, cfg):
    """a map config, written whole (temp file, then replace)"""
    tmp = path.with_name("%s.%d.tmp" % (path.name, _th.get_ident()))
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _wrong_kind(slug, cfg, kind):
    """A Save names a map and carries a config, and both must be this kind. Save pressed while another
    map was loading posted armenia's ROUTE config to /api/city/armenia, and yerevan's CITY config to
    /api/map/yerevan; both used to be written into the catalog before anything was checked (stress test
    2026-10-09). Every route config has "stops" and every city config "pois", never both."""
    if not isinstance(cfg, dict):
        return "no map config was sent"
    if kind == "route":
        if not (CFG_DIR / f"{slug}.json").is_file():
            return f"there is no route map called {slug}"
        if not isinstance(cfg.get("stops"), list) or "pois" in cfg:
            return "that is not a route map's config"
    else:
        if not (CITY_DIR / f"{slug}.json").is_file():
            return f"there is no city map called {slug}"
        if not isinstance(cfg.get("pois"), list) or "stops" in cfg:
            return "that is not a city map's config"
        if cfg.get("slug", slug) != slug:
            return f"that config is for {cfg.get('slug')}, not {slug}"
    return None


def city_render(cfg_path, embed=False, article=None):
    with _CITY_LOCK:
        return _city_render(cfg_path, embed, article)


def _city_render(cfg_path, embed=False, article=None):
    """Build a city map and return its DESKTOP render (base image + pin overlay) and, when
    embedding, the mobile overlay too. city_map.build writes the previews as a side effect;
    the non-external preview page carries the base as a data URI, which is what a page on
    another origin needs."""
    import re as _re, shutil as _sh
    slug0 = json.loads(Path(cfg_path).read_text(encoding="utf-8"))["slug"]
    # every file build() can write for this map. A preview used to leave new ones behind: the phone
    # WebP that 129 of the 133 maps do not have yet, and all four for a map never published
    # (stress test 2026-10-09)
    files = [IMG / (slug0 + x) for x in (".png", "-mobile.png", "-mobile.webp", "-1x.webp")]
    keep = {r: r.read_bytes() for r in files if r.is_file()} if not embed else {}
    # a preview only moves pins and labels: the published street raster is reused (QA 2026-10-02:
    # every open/drag re-rendered it in headless Chromium, 2-5 minutes each). A SAVE from the editor moves
    # pins and labels too, so it keeps the raster as well: redrawing the streets needs the OSM extract and a
    # fresher extract moved the water (Himarë's coast flooded on a label move, stress test 2026-11-06)
    keep_base = has_base(slug0)
    try:
        cm.build(str(cfg_path), embed=embed, article=article, keep_base=keep_base)
    finally:
        # a PREVIEW must not rewrite the tracked base rasters: build() always regenerates
        # them, and a fresher OSM extract makes every pixel differ. Only a Save keeps them.
        for r in (files if not embed else []):
            data = keep.get(r)
            if data is None:
                # new: it goes to .tmp, where /city-png still finds it for this preview
                if r.is_file():
                    try:
                        PREV_PNG.mkdir(parents=True, exist_ok=True)
                        os.replace(r, PREV_PNG / r.name)
                    except OSError:
                        pass
                continue
            # only when it changed, and by replace: the same bytes rewritten on every open or drag was a
            # non-atomic write to a file the site serves (QA 2026-10-08)
            try:
                if r.is_file() and r.read_bytes() == data:
                    continue
            except OSError:
                pass
            tmp = r.with_name(r.name + ".restore.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, r)
    slug = __import__("json").loads(Path(cfg_path).read_text(encoding="utf-8"))["slug"]
    frag = (PREV / f"{slug}-city-embed.html").read_text(encoding="utf-8")   # external hrefs, as embedded
    desk = _re.search(r'<div class="citymap desk"><div class="cmmap">(.*?)</div>', frag, _re.S).group(1)
    desk = _re.sub(r'src="[^"]*Images/web/city-maps/([^"]+)"', r'src="/city-png/\1"', desk, count=1)
    # the base image's srcset/sizes still named the site's files, which 404 here, and srcset wins over src:
    # the city editor showed a blank card with the alt text (stress test 2026-10-16)
    desk = _re.sub(r'(<img class="cmbase"[^>]*?)\s(?:srcset|sizes)="[^"]*"', r'\1', desk, count=2)
    desk_svg = _re.search(r'<div class="citymap desk"><div class="cmmap">.*?(<svg.*?</svg>)', frag, _re.S).group(1)
    mob_svg = _re.search(r'<div class="cmworld">.*?(<svg.*?</svg>)', frag, _re.S).group(1)
    # the phone variant whole (window, world, title overlay) for the editor's Phone view, its base pointed at the
    # PNG master (the page is served the WebP, which may not exist for a map not rebuilt since 2026-10-02)
    mob = _re.search(r'<div class="citymap mob".*?</figure>', frag, _re.S).group(0)[:-len("</figure>")]
    mob = _re.sub(r'src="[^"]*Images/web/city-maps/([^"]+?)\.(?:webp|png)"', r'src="/city-png/\1.png"', mob, count=1)
    mob = _re.sub(r'(<img class="cmbase"[^>]*?)\s(?:srcset|sizes)="[^"]*"', r'\1', mob, count=2)
    return desk, desk_svg, mob_svg, mob


def city_page_for(slug, page=None):
    """Where this city map is embedded: the page the editor names if the map is already in
    it, else the article the config names."""
    marker = f"city-maps/{slug}.png"
    if page and (ROOT / page).is_file() and marker in (ROOT / page).read_text(encoding="utf-8"):
        return page
    cfg = json.loads((CITY_DIR / f"{slug}.json").read_text(encoding="utf-8"))
    return cfg.get("article")
PAGES = {
    "el-salvador": "el-salvador/el-salvador-itinerary.html",
    "australia": "Drafts/australia/field-notes.html",
    "armenia": "Drafts/.Full Articles/armenia/armenia-itinerary.html",
    "armenia-yerevan": "Drafts/.Full Articles/armenia/yerevan.html",
    "orgov-loop": "Drafts/.Full Articles/armenia/orgov-observatory.html",
}

app = Flask(__name__)


@app.after_request
def _cors(resp):
    """The article editor calls this server from another origin (file:// or :5003) to ask
    which map a page carries; without this header the browser drops the answer and the
    Edit button shows the offline panel over a server that is running."""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# /api/build: this server's code fingerprint, so the editor shortcut restarts it when its code changed; a Save or
# preview in flight (an OSM fetch runs inside one) counts as busy through the POST counter (tools/build_id.py)
import build_id as _build_id
_build_id.install(app, __file__)


def pages_for(slug):
    """every page this config is embedded in: "pages" (a list) when the same map sits on a live field-notes page
    AND a draft itinerary (Kevin, 2026-10-23), else the one page page_for finds"""
    try:
        own = load_cfg(slug).get("pages")
    except (OSError, ValueError):
        own = None
    if isinstance(own, list) and own:
        return [p for p in own if isinstance(p, str)]
    return [page_for(slug)]


def page_for(slug):
    # a config can name its own page ("page": repo path): a route map that is not a country's
    # field notes (Meteora's bus map) was looked for at <slug>/field-notes.html, so its Edit
    # button found no map and a save re-embedded nowhere (Kevin, 2026-10-05)
    try:
        own = load_cfg(slug).get("page")
    except (OSError, ValueError):
        own = None
    if own:
        return own
    if slug in PAGES:
        return PAGES[slug]
    # prefer the live page; fall back to the Drafts/ copy if not yet published
    if (ROOT / slug / "field-notes.html").exists():
        return f"{slug}/field-notes.html"
    import draft_paths as _dp                       # Drafts/Field Notes or Drafts/Upcoming (2026-10-24)
    hit = _dp.locate(slug, "field-notes.html")
    if hit:
        return hit
    return f"{slug}/field-notes.html"


def load_cfg(slug):
    return json.loads((CFG_DIR / f"{slug}.json").read_text(encoding="utf-8"))


def final_afs(cfg):
    """The annotation-scale a tall map ends up with (see itinerary_map.render)."""
    if cfg.get("aspect"):          # a fixed-shape map is never height-capped, so render() leaves it at 1 (the editor
        return 1.0                 # drew ohrid-boat's labels 11% larger than the page until 2026-11-07)
    _, _, vh = im.build(cfg)
    if vh <= im.MAX_VH:
        return 1.0
    afs = vh / im.MAX_VH
    for _ in range(5):
        _, _, vh = im.build({**cfg, "_afs": afs})
        nxt = vh / im.MAX_VH
        if abs(nxt - afs) < 0.005:
            break
        afs = nxt
    return round(afs, 4)


def view_cfg(cfg, view):
    """the config a build is for: the phone build (its "mobile" block laid over, itinerary_map.mobile_cfg) when the
    editor asks for view=mob and the map has one, else the desktop config"""
    return im.mobile_cfg(cfg) if view == "mob" and cfg.get("mobile") else cfg


def edit_svg(cfg, view=None):
    cfg = view_cfg(cfg, view)
    afs = final_afs(cfg)
    svg, vw, vh = im.build({**cfg, "_edit": True, "_afs": afs})
    return svg, afs, vw, vh


def figure_width(cfg, view, vw, vh):
    """The max-width embed_itinerary_map.py gives this map's figure on the page, from its DESKTOP build's viewBox
    (or "embed_width"). On a phone the figure is drawn at min(this, PHONE_COL), which is what the Phone view shows.
    None when the build at hand is the phone one (its viewBox says nothing about the figure); the editor keeps the
    width it had."""
    if view == "mob" and cfg.get("mobile"):
        return None
    return int(cfg.get("embed_width") or round(em.K * vw * min(1.0, em.MAX_VH / vh)))


@app.route("/api/maps")
def api_maps():
    slugs = sorted(p.stem for p in CFG_DIR.glob("*.json"))
    return jsonify(slugs)


ISO2 = {"albania": "al", "argentina": "ar", "armenia": "am", "australia": "au", "belgium": "be", "bosnia": "ba",
        "brazil": "br", "chile": "cl", "colombia": "co", "croatia": "hr", "cuba": "cu", "denmark": "dk", "egypt": "eg",
        "el-salvador": "sv", "estonia": "ee", "finland": "fi", "france": "fr", "georgia": "ge", "germany": "de",
        "greece": "gr", "guatemala": "gt", "hungary": "hu", "india": "in", "indonesia": "id", "italy": "it",
        "japan": "jp", "kosovo": "xk", "latvia": "lv", "mexico": "mx", "montenegro": "me", "morocco": "ma",
        "netherlands": "nl", "new-zealand": "nz", "nicaragua": "ni", "north-macedonia": "mk", "peru": "pe",
        "philippines": "ph", "portugal": "pt", "serbia": "rs", "slovenia": "si", "spain": "es", "sweden": "se",
        "switzerland": "ch", "tanzania": "tz", "thailand": "th", "turkiye": "tr", "vietnam": "vn", "uk": "gb"}   # the same table the article editor uses for its flags


def _country_name(slug):
    return {"uk": "UK", "el-salvador": "El Salvador", "north-macedonia": "North Macedonia"}.get(
        slug, slug.replace("-", " ").title())


@app.route("/api/catalog")
def api_catalog():
    """Every map grouped by country (2026-09-27): route maps by the country their iso3 belongs
    to, city maps by the folder of the article they sit in."""
    route = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in CFG_DIR.glob("*.json")}
    plain = ("el-salvador", "north-macedonia")
    by_iso = {c.get("iso3"): k for k, c in route.items() if "-" not in k or k in plain}
    groups = {}
    for k, c in route.items():
        country = by_iso.get(c.get("iso3"), k)
        if k == country:
            label = "Route map"
        elif k.startswith(country + "-"):
            label = k[len(country) + 1:].replace("-", " ").capitalize()
        else:
            label = k.replace("-", " ").capitalize()
        groups.setdefault(country, []).append({"kind": "route", "slug": k, "label": label})
    if CITY_DIR.is_dir():
        for p in CITY_DIR.glob("*.json"):
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            parts = [x for x in (c.get("article") or "").split("/") if x and x not in ("Drafts", ".Full Articles", "Upcoming", "Full Articles", "Field Notes", ".Archive")]
            country = parts[0] if len(parts) > 1 else "other"
            page = (c.get("article") or "").rsplit("/", 1)[-1].replace(".html", "")
            where = "field notes" if page == "field-notes" else "guide"
            groups.setdefault(country, []).append({"kind": "city", "slug": p.stem, "label": c.get("city") or p.stem, "where": where})
    # two city maps of the same city (field notes and a guide): say which article each is in
    for maps in groups.values():
        names = [m["label"] for m in maps if m["kind"] == "city"]
        for m in maps:
            if m["kind"] == "city" and names.count(m["label"]) > 1 and m.get("where"):
                m["label"] = f'{m["label"]} ({m["where"]})'
    out = []
    for k in sorted(groups):
        maps = sorted(groups[k], key=lambda m: (m["kind"] != "route", m["label"] != "Route map", m["label"].lower()))
        out.append({"country": _country_name(k), "flag": ISO2.get(k, ""), "maps": maps})
    return jsonify(out)


@app.get("/fonts.css")
def fonts_css():
    # the suite's self-hosted faces; the map's own labels are Hanken Grotesk too, so without
    # this the editor measured and drew them in a fallback face
    return send_file(ROOT / "fonts.css", mimetype="text/css")


@app.get("/fonts/<path:name>")
def font_file(name):
    f = (ROOT / "fonts" / name).resolve()
    if (ROOT / "fonts").resolve() not in f.parents or not f.is_file():
        return ("", 404)
    return send_file(f, mimetype="font/woff2")


@app.route("/api/map/<slug>")
def api_map(slug):
    try:
        cfg = load_cfg(slug)
    except FileNotFoundError:
        return jsonify(error="No map called " + slug), 404
    view = request.args.get("view")
    try:
        svg, afs, vw, vh = edit_svg(cfg, view)
    except (ValueError, KeyError, ZeroDivisionError) as e:
        return jsonify(error="could not build %s: %s" % (slug, e)), 400
    return jsonify(config=cfg, svg=svg, afs=afs, page=page_for(slug), embed_w=figure_width(cfg, view, vw, vh),
                   phone={"w": PHONE_W, "col": PHONE_COL})


@app.route("/api/preview/<slug>", methods=["POST"])
def api_preview(slug):
    cfg = (request.get_json(silent=True) or {}).get("config")
    if not isinstance(cfg, dict) or not cfg.get("stops"):
        return jsonify(error="a config with stops is needed"), 400
    view = request.args.get("view")
    try:
        svg, afs, vw, vh = edit_svg(cfg, view)
    except (ValueError, KeyError, ZeroDivisionError) as e:
        return jsonify(error="could not build %s: %s" % (slug, e)), 400
    return jsonify(svg=svg, afs=afs, embed_w=figure_width(cfg, view, vw, vh))


@app.route("/api/map/<slug>", methods=["POST"])
def api_save(slug):
    cfg = (request.get_json(silent=True) or {}).get("config")
    why = _wrong_kind(slug, cfg, "route")
    if why:
        return jsonify(ok=False, error=why), 400
    try:                                       # never lose the page the config names
        old = load_cfg(slug)
        if old.get("page") and not cfg.get("page"):
            cfg["page"] = old["page"]
    except (OSError, ValueError):
        pass
    _write_cfg(CFG_DIR / f"{slug}.json", cfg)
    # rebuild the standalone SVG (final render, with the height/afs treatment)
    svg, cw, ch = im.render(cfg)
    (ROOT / ".tmp" / "previews").mkdir(parents=True, exist_ok=True)
    (ROOT / ".tmp" / "previews" / f"{slug}-map.svg").write_text(svg, encoding="utf-8")
    if cfg.get("mobile"):                      # the phone build the embed puts beside it (ohrid-boat, 2026-11-02)
        msvg, _, _ = im.render(im.mobile_cfg(cfg))
        (ROOT / ".tmp" / "previews" / f"{slug}-mob-map.svg").write_text(msvg, encoding="utf-8")
    # re-embed into every page that carries this map (the page the editor opened from is first, so the figure
    # handed back is that page's)
    asked = (request.args.get("page") or "").replace("\\", "/").lstrip("./")
    pages = pages_for(slug)
    if asked in pages:
        pages = [asked] + [p for p in pages if p != asked]
    page = pages[0]
    ok, out = True, []
    for pg in pages:
        if not (ROOT / pg).is_file():
            out.append("%s: no such page" % pg); ok = False; continue
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "embed_itinerary_map.py"),
                            slug, pg], cwd=str(ROOT), capture_output=True, text=True, creationflags=_NOWIN)
        out.append((r.stdout + r.stderr).strip())
        ok = ok and r.returncode == 0
    class _R:                                       # the shape the rest of this handler reads
        stdout, stderr = "\n".join(out), ""
    r = _R()
    esvg, afs, vw, vh = edit_svg(cfg)
    # the figure exactly as embed_itinerary_map.py wrote it into the page. The editor used to put the
    # EDITING svg into its copy (labels as drag handles, place links dropped), and the article's next
    # Save wrote that over the embed (QA 2026-10-08: meteora's 3 map links became 0).
    figure = None
    if ok:
        try:
            ph = (ROOT / page).read_text(encoding="utf-8")
            m = re.search(r'<figure class="itinerary-map" data-map="%s"[^>]*>.*?</figure>' % re.escape(slug), ph, re.S)
            if not m:
                # an untagged figure is only THIS map's when it is the page's only route map: on a two-map page the
                # first figure was handed back for the second map and both became the boat map (stress test 2026-11-06)
                figs = re.findall(r'<figure class="itinerary-map"[^>]*>.*?</figure>', ph, re.S)
                figure = figs[0] if len(figs) == 1 else None
            else:
                figure = m.group(0)
        except (OSError, TypeError):
            figure = None
    return jsonify(ok=ok, log=(r.stdout + r.stderr).strip(), svg=esvg, afs=afs, page=page, figure=figure,
                   embed_w=figure_width(cfg, None, vw, vh))


@app.route("/api/slug-for")
def api_slug_for():
    """maps that live on this repo-relative page, for the article editor's Edit button:
    itinerary maps by the page their config embeds into, city maps by the marker their
    embedded fragment carries (city-maps/<slug>.png), in the order they appear on the page"""
    page = (request.args.get("page") or "").replace("\\", "/").lstrip("./")
    itin = [p.stem for p in sorted(CFG_DIR.glob("*.json")) if page in pages_for(p.stem)]
    city = []
    f = ROOT / page
    if f.is_file():
        txt = f.read_text(encoding="utf-8")
        found = [(txt.find(f"city-maps/{p.stem}.png"), p.stem) for p in CITY_DIR.glob("*.json")]
        city = [slug for pos, slug in sorted(x for x in found if x[0] >= 0)]
    return jsonify(itinerary=itin, city=city)


@app.route("/city-png/<path:name>")
def city_png(name):
    """the base raster for the editor's canvas (the page keeps it inline as a data URI)"""
    f = (IMG / name).resolve()
    if IMG.resolve() not in f.parents or not f.is_file():
        f = (PREV_PNG / name).resolve()             # a map not published yet: the preview's own raster
        if PREV_PNG.resolve() not in f.parents or not f.is_file():
            return "", 404
    from flask import send_file
    r = send_file(f, mimetype="image/png"); r.headers["Cache-Control"] = "no-store"; return r


@app.route("/api/city/<slug>")
def api_city(slug):
    cfg_path = CITY_DIR / f"{slug}.json"
    if not cfg_path.is_file():
        return jsonify(error="no such city map"), 404
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    # with the published rasters the preview needs no OSM data: selecting any of the 113 maps whose
    # extract is not in .tmp started a PowerShell Overpass fetch of up to 25 minutes (stress test 2026-10-09)
    why = None if has_base(slug) else ensure_osm(cfg_path)
    if why:
        return jsonify(error=why), 502
    try:
        desk, _, _, mob = city_render(cfg_path)
    except Exception as e:
        return jsonify(error=f"could not build {slug}: {e}"), 500
    return jsonify(config=cfg, desk=desk, mob=mob, page=city_page_for(slug, request.args.get("page")),
                   phone={"w": PHONE_W, "col": PHONE_COL})


@app.route("/api/city/<slug>/preview", methods=["POST"])
def api_city_preview(slug):
    cfg = (request.get_json(silent=True) or {}).get("config")
    if not isinstance(cfg, dict) or not cfg.get("pois"):
        return jsonify(error="a config with pois is needed"), 400
    cfg["slug"] = slug                            # the URL names the map; the JSON cannot point at another
    # one temp config per request: two previews shared "_edit-<slug>.json", written outside the build
    # lock, so one build could read the other's labels (stress test 2026-10-09)
    tmp = PREV / f"_edit-{slug}-{_th.get_ident()}.json"
    PREV.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    try:
        desk, _, _, mob = city_render(tmp)
    except Exception as e:
        return jsonify(error=str(e)), 500
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return jsonify(desk=desk, mob=mob)


@app.route("/api/city/<slug>", methods=["POST"])
def api_city_save(slug):
    d = request.get_json(silent=True) or {}
    cfg, page = d.get("config"), d.get("page")
    why = _wrong_kind(slug, cfg, "city")
    if why:
        return jsonify(ok=False, error=why), 400
    cfg_path = CITY_DIR / f"{slug}.json"
    _write_cfg(cfg_path, cfg)
    target = city_page_for(slug, page)
    # a map with a published raster is redrawn from it (pins and labels only); one without needs the extract
    why = None if has_base(slug) else ensure_osm(cfg_path)
    if why:
        return jsonify(ok=False, log="The label moves are saved, but the map could not be redrawn: " + why, page=target)
    try:
        _, desk_svg, mob_svg, _ = city_render(cfg_path, embed=True, article=target)
        return jsonify(ok=True, desk_svg=desk_svg, mob_svg=mob_svg, page=target)
    except SystemExit as e:                       # build() aborts an embed whose anchors are gone
        return jsonify(ok=False, log=str(e), page=target)
    except Exception as e:
        return jsonify(ok=False, log=str(e), page=target)


@app.route("/")
def index():
    # the city maps' own CSS goes in, so the Phone view draws the portrait window, world and title overlay exactly
    # as the page does (city_map.CSS; its display rules are overridden below it: here one variant is in the DOM)
    return Response(PAGE.replace("/*CITYCSS*/", cm.CSS), mimetype="text/html")


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Map editor</title>
<link rel="stylesheet" href="/fonts.css">
<style>
  :root{--green:#2D6B50;--ink:#1C2821;--line:#e3e0d8}
  *{box-sizing:border-box}
  body{margin:0;font-family:'Hanken Grotesk',system-ui,sans-serif;color:var(--ink);background:#F7F7F3;display:flex;height:100vh;overflow:hidden}
  #side{width:240px;flex:none;background:#fff;border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0}

  /* the hide-the-sidebar arrow, the same one the Article Editor and Photo Library use (2026-09-27) */
  .side-handle{width:24px;height:24px;border-radius:50%;border:1px solid rgba(28,40,33,.16);background:#fff;
    color:#4f5c54;box-shadow:0 1px 4px rgba(28,40,33,.12);display:flex;align-items:center;justify-content:center;
    cursor:pointer;padding:0;z-index:30;transition:margin .2s,right .2s,left .2s}
  .side-handle:hover{color:#2D6B50;border-color:#2D6B50}
  .side-handle:focus-visible{outline:2px solid #2D6B50;outline-offset:2px}
  .side-handle svg{width:12px;height:12px;transition:transform .2s}
  #side{position:relative;transition:width .2s}
  #side > .side-handle{position:absolute;top:10px;right:-12px}
  body.side-collapsed #side{width:0;border-right:0}
  body.side-collapsed #side > :not(.side-handle){display:none}
  body.side-collapsed #side > .side-handle{right:-30px}
  body.side-collapsed #side > .side-handle svg{transform:rotate(180deg)}
  body.side-collapsed #bar{padding-left:44px}      /* clear of the arrow */

  /* thin scrollbars everywhere (2026-09-27) */
  ::-webkit-scrollbar{width:4px;height:4px}
  ::-webkit-scrollbar-track{background:transparent}
  ::-webkit-scrollbar-thumb{background:transparent;border-radius:2px}
  *:hover::-webkit-scrollbar-thumb{background:rgba(28,40,33,.3)}
  ::-webkit-scrollbar-thumb:hover{background:rgba(28,40,33,.5)}
    .cfl{width:18px;height:13px;border-radius:2px;object-fit:cover;margin-right:9px;flex:none;display:inline-block}
  .cfl.none{background:#E8E7E1}
  /* one type standard for controls (2026-09-27) */
  #q{font-family:'Hanken Grotesk',sans-serif !important;font-size:13px !important;font-weight:400 !important;letter-spacing:0 !important;text-transform:none !important}
  #bar button{font-family:'Hanken Grotesk',sans-serif !important;font-size:11px !important;font-weight:600 !important;letter-spacing:.08em !important;text-transform:uppercase !important}
  #side h1{display:none}
  #q{margin:12px 12px 8px;font:inherit;font-size:13px;padding:8px 10px;border:1px solid rgba(28,40,33,.16);border-radius:6px;outline:none}
  #q:focus{border-color:var(--green);box-shadow:0 0 0 3px rgba(45,107,80,.14)}
  #list{overflow-y:auto;flex:1;padding:0 0 16px}
  .cty{display:flex;align-items:center;width:100%;border:0;background:none;cursor:pointer;padding:9px 14px;font:inherit;font-size:13px;color:var(--ink);text-align:left}
  .cty:hover{background:#F3F2EC}
  .cty span{font-size:11px;color:#6b7a70;margin-left:auto}
  .cty::after{content:'';width:6px;height:6px;border-right:1.5px solid #8a978f;border-bottom:1.5px solid #8a978f;transform:rotate(-45deg);margin-left:10px}
  .grp.open .cty::after{transform:rotate(45deg)}
  .grp.has .cty{font-weight:600}
  .grp .maps{display:none;padding:0 0 6px}
  .grp.open .maps{display:block}
  .mapbtn{display:flex;justify-content:space-between;align-items:baseline;gap:8px;width:100%;text-align:left;border:0;border-left:3px solid transparent;background:none;padding:6px 14px 6px 26px;font:inherit;font-size:13px;color:var(--ink);cursor:pointer}
  .mapbtn i{font-style:normal;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#6b7a70}
  .mapbtn:hover{background:#F3F2EC}
  .mapbtn.active{background:rgba(45,107,80,.08);color:var(--green);font-weight:600;border-left-color:var(--green)}
  #main{flex:1;display:flex;flex-direction:column;min-width:0}
  #bar{flex:none;display:flex;align-items:center;gap:14px;padding:10px 18px;background:#fff;border-bottom:1px solid var(--line)}
  #bar .title{font-weight:600;font-size:15px}
  #bar button{font:inherit;font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;border:1px solid var(--green);background:var(--green);color:#fff;border-radius:999px;padding:8px 16px;cursor:pointer}
  #bar button.ghost{background:#fff;color:var(--ink);border-color:rgba(28,40,33,.16)}
  #bar button.ghost:hover:not(:disabled){background:#F3F2EC}
  #bar button:disabled{opacity:.4;cursor:default}
  #status{font-size:12px;color:#7a8a7f;margin-left:auto}
  #hint{font-size:12px;color:#6b7a70}
  /* a column with auto margins on the content, not justify-content:center: a 2x Phone view taller than the stage
     then scrolls from its top instead of losing it above the fold */
  #stage{flex:1;overflow:auto;display:flex;flex-direction:column;align-items:center;padding:24px}
  #wrap{margin:auto 0;display:flex;flex-direction:column;align-items:center;width:100%}
  /* the map fills the window it has (2026-09-27): as wide as fits, never taller than the stage */
  #canvas{background:#F7F7F3;box-shadow:0 6px 30px rgba(0,0,0,.12);border-radius:6px;width:min(100%,1200px)}
  /* Phone view (2026-11-07): the canvas is as wide as the page's phone column draws the map (set inline, PHONE_COL
     or the figure's own max-width if narrower, x the 1x/2x magnifier); never height-capped, or the svg would letterbox */
  #canvas.phone{flex:none;max-width:100%}
  #canvas.phone svg{max-height:none}
  #pcap{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#7a8a7f;margin-top:10px;text-align:center}
  /* touch-action on the svg ROOT: Chromium ignores it on inner svg elements, so a finger drag on a label was
     taken for a pan halfway through (pointercancel) and the label stopped short (test_map_label_drag D2) */
  #canvas svg{width:100%;height:auto;max-height:calc(100vh - 110px);display:block;touch-action:none}
  #canvas.city{position:relative;width:min(100%,1200px)}
  #canvas.city img.cmbase{width:100%;height:auto;display:block}
  #canvas.city svg{position:absolute;inset:0;width:100%;height:100%}
  #canvas.city text.cmdl:hover,#canvas.city text.cmlab:hover{fill:#2D6B50}
  .eh{cursor:grab}
  .eh[data-kind="legend"]:hover>.hit,.eh[data-kind="country"]:hover>.hit,.eh[data-kind="water"]:hover>.hit{stroke:var(--green);stroke-dasharray:3 3;stroke-width:1;fill:rgba(45,107,80,.05)}
  .legpick{cursor:copy}
  .legpick:hover{stroke:rgba(45,107,80,.18)}
  .vh circle{cursor:grab}
  .vh:hover circle{stroke:#2D6B50;stroke-width:2.2}
  .vh>.hit{display:none}
  .eh:hover>.hit{stroke:var(--green);stroke-dasharray:3 3;stroke-width:1}
  .eh.drag{cursor:grabbing}
  .eh.drag>.hit{stroke:var(--green);stroke-dasharray:none;stroke-width:1.4}
  .hit{fill:transparent;stroke:transparent;vector-effect:non-scaling-stroke}
  #empty{margin:auto;color:#4f5c54;font-size:14px;text-align:center;line-height:1.7}
  #empty b{display:block;font-family:Newsreader,Georgia,serif;font-weight:400;font-size:22px;color:var(--ink)}
  body.embed #side{display:none}
  body.embed #done{display:inline-block}
  #done{display:none}

  /* the city maps' own stylesheet (city_map.CSS), for the Phone view's portrait window, world and title overlay */
  /*CITYCSS*/
  /* one variant is in the editor's DOM at a time, so neither hides, whatever the window width; the canvas sets the width */
  #canvas.city .citymap.desk,#canvas.city .citymap.mob{display:block;max-width:none}
  #canvas.city .citymap.mob .cmmap{cursor:grab;border-radius:6px}
  /* the page's world svg is pointer-events:none (gestures go to the window); here its labels must take the pointer */
  #canvas.city .cmworld svg{pointer-events:auto}

  /* a phone-width window (the article editor's modal at 393 px is 369 px wide): the bar wraps, the hint goes, Save
     and Done stay on screen, and the map list lies over the stage instead of squeezing it (stress test 2026-11-06, t5e) */
  @media (max-width:700px){
    #bar{flex-wrap:wrap;gap:8px 10px;padding:8px 10px}
    #bar .title{flex:1 1 100%;font-size:14px;min-width:0;overflow-wrap:anywhere}
    #hint{display:none}
    #bar button{padding:7px 11px;letter-spacing:.06em !important}
    #status{flex:1 1 100%;margin-left:0}
    #stage{padding:10px}
    body:not(.embed) #side{position:absolute;left:0;top:0;bottom:0;z-index:25;box-shadow:4px 0 18px rgba(28,40,33,.12)}
    body:not(.embed):not(.side-collapsed) #bar{padding-left:10px}
  }
</style></head>
<body>
<div id="side"><button class="side-handle" id="side-handle" type="button" title="Hide the map list" aria-expanded="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg></button><h1>Maps</h1><input id="q" type="search" placeholder="Find a country or city" autocomplete="off"><div id="list"></div></div>
<div id="main">
  <div id="bar">
    <span class="title" id="ttl">Select a map</span>
    <span id="hint">drag any label or pill · double-click a place label to flip its side · click a route to add a waypoint, drag it, double-click to remove</span>
    <button class="ghost" id="phone" hidden title="Show this map as a 393 px phone draws it; moves made there change the phone build only">Phone</button>
    <button class="ghost" id="mag" hidden title="Magnify the phone view (it stays laid out as the phone draws it)">2×</button>
    <button class="ghost" id="reset" disabled>Revert</button>
    <button id="save" disabled>Save &amp; embed</button>
    <button class="ghost" id="done" title="Back to the article">Done</button>
    <span id="status"></span>
  </div>
  <div id="stage"><div id="empty"><b>Pick a map</b>Open a country on the left, then a route or city map.<br>Drag any label to move it, then Save &amp; embed.</div></div>
</div>
<script>
(function sideHandle() {
  const btn = document.getElementById('side-handle');
  const apply = on => { document.body.classList.toggle('side-collapsed', on); btn.title = (on ? 'Show ' : 'Hide ') + 'the map list (Ctrl+\\)';
    btn.setAttribute('aria-label', (on ? 'Show ' : 'Hide ') + 'the map list'); btn.setAttribute('aria-expanded', String(!on)); };
  // no stored preference: open, except in a phone-width window, where the list would lie over the whole map (2026-11-07)
  let on = false; try { const v = localStorage.getItem('side:maps'); on = v === null ? matchMedia('(max-width:700px)').matches : v === '1'; } catch (e) {}
  apply(on);
  btn.onclick = () => { on = !on; apply(on); try { localStorage.setItem('side:maps', on ? '1' : '0'); } catch (e) {} };
  document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === '\\') { e.preventDefault(); btn.click(); } });
  // the suite's shortcuts from inside this frame, and Ctrl+\ from outside it (QA 2026-09-27)
  document.addEventListener('keydown', e => { if (e.ctrlKey && e.altKey && /^[1-6]$/.test(e.key) && window.parent !== window) { e.preventDefault(); window.parent.postMessage({ type: 'suite-view', n: +e.key }, '*'); } });
  window.addEventListener('message', e => { if (e.data && e.data.type === 'suite-side') btn.click(); });
})();

const SVGNS="http://www.w3.org/2000/svg";
let slug=null, cfg=null, afs=1, dirty=false;
const listEl=document.getElementById('list'), stage=document.getElementById('stage');
const saveBtn=document.getElementById('save'), resetBtn=document.getElementById('reset');
const ttl=document.getElementById('ttl'), statusEl=document.getElementById('status');

// ?embed=1&slug=<map>: opened from the article editor's Edit button on a map figure.
// The sidebar goes, the map comes up already selected, and Done hands control back.
const Q=new URLSearchParams(location.search), EMBED=Q.has('embed'), WANT=Q.get('slug'), CITY=Q.get('city'), PAGE=Q.get('page')||'';
let cityMode=false;
// kind is set with slug and cfg when a map's answer lands, so Save always posts a config to its own
// kind's endpoint. cityMode flipped at the click, and Save pressed while the next map loaded posted
// armenia's route config to /api/city/armenia (stress test 2026-10-09).
let kind=null, loading=false, PSEQ=0, EDITS=0;
// view: which build the canvas shows (2026-11-07: every map has a Phone view). 'desk' edits a route map's top-level
// entries and a city map's district labels / pin-label sides. 'mob' shows the map as a PHONE.w px phone draws it: a
// route map's "mobile" block (itinerary_map.mobile_cfg lays it over the desktop config; made at the first phone move
// of a map that had none) drawn at the phone column's width, or a city map's portrait gesture window at its opening
// view; moves there go to the phone build only (cfg.mobile, district_labels[].mobile, pois[].lab_mobile).
let view='desk', embedW=null, PHONE={w:393,col:345}, mag=1, lastSvg=null, lastCity=null;
const phoneBtn=document.getElementById('phone'), magBtn=document.getElementById('mag');
const HINT_ROUTE='drag any label, pill, country or water name, or the legend · double-click a place label to flip its side, a country/water name or the legend to put it back · click a route to add a waypoint, drag it, double-click to remove';
const HINT_CITY='drag a district label · click a place name to move it round its pin';
function setHint(){ const h=document.getElementById('hint');
  if(kind==='city') h.textContent=view==='mob'?'phone build · drag a district label (the phone keeps its own spot) · click a place name to move it round its pin · drag the map to pan, wheel to zoom':HINT_CITY;
  else h.textContent=(view==='mob'?'phone build · moves here change the phone only · ':'')+HINT_ROUTE; }
function pcap(){ return '<div id="pcap">Phone build · '+PHONE.w+' px window · '+(mag===1?'actual size':mag+'× magnified')+' · moves here change the phone only</div>'; }
function redraw(){ if(kind==='city'){ if(lastCity) renderCity(lastCity.desk,lastCity.mob); return; }
  // a route map with a phone block has two builds (the server lays the block over), so a view switch rebuilds; one
  // without draws the same svg, at the phone column's width or the stage's
  if(cfg.mobile) preview(true); else if(lastSvg) render(lastSvg); }
phoneBtn.onclick=()=>{ if(loading||!cfg||!kind) return; view=view==='mob'?'desk':'mob'; phoneBtn.textContent=view==='mob'?'Desktop':'Phone'; magBtn.hidden=view!=='mob'; setHint(); redraw(); };
magBtn.onclick=()=>{ mag=mag===1?2:1; magBtn.textContent=mag===1?'2×':'1×'; redraw(); };
function resetView(){ view='desk'; mag=1; phoneBtn.textContent='Phone'; magBtn.textContent='2×'; magBtn.hidden=true; lastSvg=null; lastCity=null; }
if(EMBED) document.body.classList.add('embed');
const tell=(msg)=>{ if(window.parent!==window) window.parent.postMessage(Object.assign({source:'map-editor'},msg),'*'); };
document.getElementById('done').onclick=()=>{ if(dirty && !confirm('Discard the unsaved map edits?')) return; dirty=false; tell({type:'map-done',slug}); };
if(EMBED){
  // Escape with the focus in here never reached the article editor's handler (QA 2026-10-08)
  document.addEventListener('keydown',e=>{ if(e.key==='Escape'){ e.preventDefault(); document.getElementById('done').click(); } });
  // the article editor asks before closing (a backdrop click closed it with label moves unsaved):
  // say we heard, then run Done's own check
  window.addEventListener('message',e=>{ if(e.data&&e.data.type==='map-close-request'){ tell({type:'map-close-ack'}); setTimeout(()=>document.getElementById('done').click(),0); } });
}

fetch('/api/catalog').then(r=>r.json()).then(cat=>{
  cat.forEach(g=>{
    const grp=document.createElement('div'); grp.className='grp';
    grp.dataset.q=(g.country+' '+g.maps.map(m=>m.label+' '+m.slug).join(' ')).toLowerCase();
    const h=document.createElement('button'); h.className='cty';
    h.innerHTML=(g.flag?'<img class="cfl" src="http://127.0.0.1:5003/site/Images/web/flags/'+g.flag+'.png" alt="" width="18" height="13">':'<i class="cfl none"></i>')+g.country.replace(/</g,'&lt;')+' <span>'+g.maps.length+'</span>';
    h.onclick=()=>grp.classList.toggle('open');
    const box=document.createElement('div'); box.className='maps';
    g.maps.forEach(m=>{
      const b=document.createElement('button'); b.className='mapbtn'; b.dataset.slug=m.kind+':'+m.slug;
      b.innerHTML=m.label.replace(/</g,'&lt;')+'<i>'+(m.kind==='city'?'city':'route')+'</i>';
      b.onclick=()=>{ if(dirty && !confirm('Discard the unsaved map edits?')) return; if(m.kind==='city'){ mark(b); selectCity(m.slug); } else selectMap(m.slug,b); };
      box.appendChild(b);
    });
    grp.append(h,box); listEl.appendChild(grp);
  });
  if(WANT){ const b=listEl.querySelector('[data-slug="route:'+WANT+'"]'); if(b) selectMap(WANT,b); else ttl.textContent='No map called '+WANT; }
  if(CITY){ mark(listEl.querySelector('[data-slug="city:'+CITY+'"]')); selectCity(CITY); }
});
document.getElementById('q').addEventListener('input',e=>{
  const q=e.target.value.trim().toLowerCase();
  listEl.querySelectorAll('.grp').forEach(g=>{ const hit=!q||g.dataset.q.includes(q); g.style.display=hit?'':'none'; if(q) g.classList.toggle('open',hit); });
});
// the open map is highlighted, and its country stays open and bold
function mark(btn){
  listEl.querySelectorAll('.mapbtn.active').forEach(x=>x.classList.remove('active'));
  listEl.querySelectorAll('.grp.has').forEach(x=>x.classList.remove('has'));
  if(btn){ btn.classList.add('active'); btn.closest('.grp').classList.add('has','open'); }
}

// ---- city maps: the base raster with the pin overlay; district labels drag, pin labels
// (orientation maps) click through n -> e -> s -> w; Save rebuilds and re-embeds.
var GEN=0;   // QA 2026-10-02: every select/preview answer is checked against the map chosen since
// A map is being chosen: nothing can be saved or reverted until its answer lands, and the old map's
// unsaved moves are gone (Discard was confirmed). Opening fails visibly, with a way to try again.
function beginSelect(){
  const my=++GEN; PSEQ++; loading=true; dirty=false; kind=null;
  saveBtn.disabled=true; resetBtn.disabled=true; statusEl.textContent='';
  return my;
}
function failSelect(my,m,why,again){
  if(my!==GEN) return;
  loading=false; slug=null; cfg=null; kind=null; saveBtn.disabled=true; resetBtn.disabled=true;
  ttl.textContent='cannot open this map';
  stage.innerHTML='<div id="empty">'+String(why).replace(/</g,'&lt;')+'<br><button type="button" id="retry">Try again</button></div>';
  document.getElementById('retry').onclick=()=>again(m);
}
function selectCity(m){
  cityMode=true; phoneBtn.hidden=true; resetView(); const my=beginSelect();
  ttl.textContent='loading '+m.replace(/-/g,' ')+'… (a first open fetches the map data from OpenStreetMap, up to a minute)';
  stage.innerHTML='<div id="empty">loading…</div>';
  // a short pause first: 100 quick clicks down the list made 100 server builds (stress test 2026-10-09)
  setTimeout(()=>{ if(my!==GEN) return;
  fetch('/api/city/'+m+'?page='+encodeURIComponent(PAGE)).then(r=>r.json()).then(d=>{
    if(my!==GEN) return;
    if(d.error){ failSelect(my,m,d.error,selectCity); return; }
    slug=m; cfg=d.config; kind='city'; loading=false; dirty=false; if(d.phone) PHONE=d.phone;
    ttl.textContent=m.replace(/-/g,' ')+' (city map)'; statusEl.textContent='';
    setHint(); phoneBtn.hidden=false;
    renderCity(d.desk,d.mob); saveBtn.disabled=true; resetBtn.disabled=false;
  }).catch(()=>failSelect(my,m,'The map editor did not answer.',selectCity));
  },120);
}
// The phone window of a city map, as the page's script opens it (city_map.py JS init(): data-iw/ix/iy frame the
// cluster), with its pan (drag) and zoom (wheel) so the whole base can be checked. Labels and pins are left to
// their own handlers.
function phoneWorld(canvas){
  const cm=canvas.querySelector('.citymap.mob'), vp=cm.querySelector('.cmmap'), world=cm.querySelector('.cmworld'), svg=world.querySelector('svg');
  const vb=svg.viewBox.baseVal, W=vb.width, H=vb.height, vw=vp.clientWidth, vh=vp.clientHeight;
  const minS=Math.min(vw/W,vh/H), maxS=minS*7;
  const iw=parseFloat(cm.dataset.iw)||0.8, ix=parseFloat(cm.dataset.ix)||0.5, iy=parseFloat(cm.dataset.iy)||0.5;
  let s=Math.max(minS,Math.min(maxS,vw/(iw*W))), tx=-Math.max(0,W-vw/s)*ix*s, ty=-Math.max(0,H-vh/s)*iy*s;
  const clamp=()=>{ const cw=W*s, ch=H*s; tx=cw<=vw?(vw-cw)/2:Math.min(0,Math.max(vw-cw,tx)); ty=ch<=vh?(vh-ch)/2:Math.min(0,Math.max(vh-ch,ty)); };
  const apply=()=>{ world.style.transform='translate('+tx.toFixed(2)+'px,'+ty.toFixed(2)+'px) scale('+s.toFixed(5)+')'; };
  clamp(); apply();
  vp.addEventListener('pointerdown',e=>{ if(e.target.closest('text.cmdl,text.cmlab')) return; e.preventDefault(); let lx=e.clientX, ly=e.clientY;
    const mv=ev=>{ tx+=ev.clientX-lx; ty+=ev.clientY-ly; lx=ev.clientX; ly=ev.clientY; clamp(); apply(); };
    window.addEventListener('pointermove',mv); window.addEventListener('pointerup',()=>window.removeEventListener('pointermove',mv),{once:true}); });
  vp.addEventListener('wheel',e=>{ e.preventDefault(); const r=vp.getBoundingClientRect(), px=e.clientX-r.left, py=e.clientY-r.top;
    const ns=Math.max(minS,Math.min(maxS,s*Math.pow(1.0016,-e.deltaY))), ax=(px-tx)/s, ay=(py-ty)/s; s=ns; tx=px-ax*s; ty=py-ay*s; clamp(); apply(); },{passive:false});
}
function renderCity(desk,mob){
  lastCity={desk:desk,mob:mob};
  const phone=view==='mob'&&!!mob;
  stage.innerHTML='<div id="wrap"><div id="canvas" class="city'+(phone?' phone':'')+'">'+(phone?mob:desk)+'</div>'+(phone?pcap():'')+'</div>';
  const canvas=document.getElementById('canvas');
  if(phone){ canvas.style.width=(PHONE.col*mag)+'px'; phoneWorld(canvas); }
  const root=stage.querySelector('svg');
  const [cx,cy,sc,W,H]=(root.getAttribute('data-proj')||'0 0 1 1 1').split(' ').map(Number);
  const toLL=(x,y)=>{ const mx=(x-W/2)/sc+cx, my=cy-(y-H/2)/sc;
    return {lon:mx*180/Math.PI, lat:(2*Math.atan(Math.exp(my))-Math.PI/2)*180/Math.PI}; };
  // pins are drawn after the district labels, so a label under a marker could not be grabbed:
  // in the editor the labels go on top (the saved map keeps the page's own order)
  root.querySelectorAll('text.cmdl').forEach(t=>root.appendChild(t));
  root.querySelectorAll('text.cmdl').forEach(t=>{
    t.style.cursor='grab';
    t.addEventListener('pointerdown',e=>{
      e.preventDefault();
      const inv=root.getScreenCTM().inverse();
      const pt=(X,Y)=>{const q=root.createSVGPoint();q.x=X;q.y=Y;return q.matrixTransform(inv);};
      const s0=pt(e.clientX,e.clientY), x0=+t.getAttribute('x'), y0=+t.getAttribute('y');
      let last=null;
      const move=ev=>{ const c=pt(ev.clientX,ev.clientY); last=[x0+c.x-s0.x, y0+c.y-s0.y]; t.setAttribute('x',last[0]); t.setAttribute('y',last[1]); };
      const up=()=>{ window.removeEventListener('pointermove',move);
        if(!last) return;
        const i=+t.dataset.dl, ll=toLL(last[0],last[1]);
        const list=cfg.district_labels||(cfg.district_label?[cfg.district_label]:[]);
        if(!cfg.district_labels){ cfg.district_labels=list; delete cfg.district_label; }
        // in the Phone view the label's phone spot (district_labels[].mobile, city_map.py) takes the move; desktop keeps its own
        const o=phone?(list[i].mobile=(list[i].mobile&&typeof list[i].mobile==='object')?list[i].mobile:{}):list[i];
        o.lat=+ll.lat.toFixed(6); o.lon=+ll.lon.toFixed(6);
        markDirty(); previewCity(); };
      window.addEventListener('pointermove',move); window.addEventListener('pointerup',up,{once:true});
    });
  });
  root.querySelectorAll('text.cmlab').forEach(t=>{
    t.style.cursor='pointer';
    t.addEventListener('click',e=>{
      e.preventDefault(); e.stopPropagation();
      const name=t.closest('.cmpin').dataset.name, poi=cfg.pois.find(p=>p.name===name); if(!poi) return;
      // the phone has its own side (pois[].lab_mobile): its pins are 1.6x, so a side that clears on desktop can collide there
      const order=['n','e','s','w'], cur=phone?(poi.lab_mobile||poi.lab||'s'):(poi.lab||'s'), nxt=order[(order.indexOf(cur)+1)%4];
      if(phone) poi.lab_mobile=nxt; else poi.lab=nxt;
      markDirty(); previewCity();
    });
  });
  root.querySelectorAll('a').forEach(a=>a.addEventListener('click',e=>e.preventDefault()));
}
// PSEQ: only the newest preview is drawn. Two drags answered out of order drew the first one's
// labels over the second's, and the next drag committed from that stale drawing (stress test 2026-10-09)
function previewCity(){
  statusEl.textContent='rendering…'; const my=GEN, s=slug, p=++PSEQ;
  fetch('/api/city/'+slug+'/preview',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({config:cfg})}).then(r=>r.json()).then(d=>{ if(my!==GEN||s!==slug||p!==PSEQ) return; if(d.error){ statusEl.textContent='could not render: '+d.error; return; } renderCity(d.desk,d.mob); statusEl.textContent='unsaved changes'; })
    .catch(()=>{ if(my===GEN&&p===PSEQ) statusEl.textContent='could not render (server?)'; });
}

function selectMap(m,btn){
  cityMode=false; document.getElementById('hint').textContent=HINT_ROUTE; resetView(); phoneBtn.hidden=true;
  if(btn) mark(btn); const my=beginSelect();
  ttl.textContent='loading '+m.replace(/-/g,' ')+'…'; stage.innerHTML='<div id="empty">loading…</div>';
  setTimeout(()=>{ if(my!==GEN) return;
  fetch('/api/map/'+m).then(r=>r.json()).then(d=>{
    if(my!==GEN) return;
    if(d.error){ failSelect(my,m,d.error,x=>selectMap(x)); return; }
    slug=m; cfg=d.config; afs=d.afs; kind='route'; loading=false; dirty=false; embedW=d.embed_w||null; if(d.phone) PHONE=d.phone;
    ttl.textContent=m.replace(/-/g,' '); statusEl.textContent='';
    phoneBtn.hidden=false; setHint();
    render(d.svg); saveBtn.disabled=true; resetBtn.disabled=false;
  }).catch(()=>failSelect(my,m,'The map editor did not answer.',x=>selectMap(x)));
  },120);
}

function render(svg){
  lastSvg=svg;
  const phone=view==='mob';
  stage.innerHTML='<div id="wrap"><div id="canvas"'+(phone?' class="phone"':'')+'>'+svg+'</div>'+(phone?pcap():'')+'</div>';
  // the page draws the figure at min(its max-width, the phone column); so does the canvas (times the magnifier)
  if(phone) document.getElementById('canvas').style.width=(Math.min(embedW||PHONE.col,PHONE.col)*mag)+'px';
  const root=stage.querySelector('svg');
  root.querySelectorAll('.eh').forEach(g=>{
    const bb=g.getBBox();
    const hit=document.createElementNS(SVGNS,'rect');
    hit.setAttribute('class','hit');
    hit.setAttribute('x',bb.x-3); hit.setAttribute('y',bb.y-3);
    hit.setAttribute('width',bb.width+6); hit.setAttribute('height',bb.height+6);
    g.insertBefore(hit,g.firstChild);
    g.addEventListener('pointerdown',startDrag);
    const k=g.dataset.kind;
    g.addEventListener('dblclick',k==='via'?removeVia:(k==='country'||k==='water'||k==='legend')?resetPos:flipAnchor);
  });
  // the route pen: click a leg to add a waypoint where you clicked
  root.querySelectorAll('.legpick').forEach(pth=>{ pth.addEventListener('click',addVia); });
}

// the editing SVG's projection (itinerary_map.py T()): lon*K -> x, -lat -> y
function unproject(svg,x,y){
  const [minx,miny,scale,ox,oy,K]=(svg.getAttribute('data-proj')||'').split(' ').map(Number);
  if(!(scale>0)) return null;
  return [-( (y-oy)/scale + miny ), ((x-ox)/scale + minx)/K];      // [lat, lon]
}
function svgPoint(svg,clientX,clientY){ const pt=svg.createSVGPoint(); pt.x=clientX; pt.y=clientY; return pt.matrixTransform(svg.getScreenCTM().inverse()); }
// a waypoint stays inside the map's frame: one dragged off the window was kept at its far-away position and the
// whole map zoomed out 4x to fit it (stress test 2026-10-15)
function clampToView(svg,x,y){
  const vb=svg.viewBox&&svg.viewBox.baseVal; if(!vb||!vb.width) return [x,y];
  const m=4;
  return [Math.max(vb.x+m,Math.min(vb.x+vb.width-m,x)), Math.max(vb.y+m,Math.min(vb.y+vb.height-m,y))];
}
function addVia(e){
  if(!cfg||kind!=='route') return;
  if(e.detail>1) return;                 // the second click of a double-click: one waypoint, not two
  const pth=e.currentTarget, svg=pth.ownerSVGElement, li=+pth.dataset.idx, leg=cfg.legs[li];
  if(!leg) return;
  const p0=svgPoint(svg,e.clientX,e.clientY), [px,py]=clampToView(svg,p0.x,p0.y), p={x:px,y:py}, ll=unproject(svg,p.x,p.y);
  if(!ll) return;
  // insert after the nearest point of the leg's polyline that precedes the click
  const T=(la,lo)=>{ const [minx,miny,scale,ox,oy,K]=svg.getAttribute('data-proj').split(' ').map(Number); return [(lo*K-minx)*scale+ox, (-la-miny)*scale+oy]; };
  const stops=cfg.stops, legPt=key=>{ if(key==='air'){ const a=(cfg.airports&&cfg.airports[0])||cfg.airport; return [a.lat,a.lon]; } if(typeof key==='string'&&key.startsWith('dot:')){ const d=cfg.dots.find(d=>d.name===key.slice(4)); return [d.lat,d.lon]; } return [stops[key].lat,stops[key].lon]; };
  const via=Array.isArray(leg.via)?leg.via.slice():[];
  const pts=[legPt(leg.a)].concat(via).concat([legPt(leg.b)]).map(q=>T(q[0],q[1]));
  let best=0, bd=Infinity;
  for(let i=0;i<pts.length-1;i++){
    const [ax,ay]=pts[i],[bx,by]=pts[i+1]; const dx=bx-ax, dy=by-ay, L2=dx*dx+dy*dy||1;
    const t=Math.max(0,Math.min(1,((p.x-ax)*dx+(p.y-ay)*dy)/L2)); const qx=ax+t*dx, qy=ay+t*dy;
    const dd=(p.x-qx)**2+(p.y-qy)**2; if(dd<bd){bd=dd;best=i;}
  }
  via.splice(best,0,[Math.round(ll[0]*1e5)/1e5, Math.round(ll[1]*1e5)/1e5]);
  leg.via=via; delete leg.bow;           // a bowed leg becomes an explicit route once you draw on it
  markDirty(); preview();
}
function removeVia(e){
  const g=e.currentTarget, li=+g.dataset.idx, k=+g.dataset.k, leg=cfg&&cfg.legs[li];
  if(!leg||!Array.isArray(leg.via)) return;
  leg.via.splice(k,1); if(!leg.via.length) delete leg.via;
  markDirty(); preview();
}

function startDrag(e){
  e.preventDefault();
  const g=e.currentTarget; g.classList.add('drag');
  const inv=g.getScreenCTM().inverse();
  const p=(x,y)=>{const pt=g.ownerSVGElement.createSVGPoint();pt.x=x;pt.y=y;return pt.matrixTransform(inv);};
  const s=p(e.clientX,e.clientY);
  let d={dx:0,dy:0};
  function move(ev){
    const c=p(ev.clientX,ev.clientY);
    d={dx:c.x-s.x, dy:c.y-s.y};
    g.setAttribute('transform',`translate(${d.dx} ${d.dy})`);
  }
  function up(){
    window.removeEventListener('pointermove',move); window.removeEventListener('pointerup',up); window.removeEventListener('pointercancel',up);
    g.classList.remove('drag');
    // a label dragged past the frame stays inside it: one dragged to the window corner rescaled the whole map,
    // a district label landed off-card where nothing could reach it (stress test 2026-10-16)
    // the label's box in ROOT units (getBBox is local to the label's own scaled group, and clamping that against
    // the root viewBox threw a label ~180 units off its pin; stress test 2026-10-23): shift it back inside
    try{ const svg=g.ownerSVGElement, vb=svg.viewBox&&svg.viewBox.baseVal, r=g.getBoundingClientRect(), inv2=svg.getScreenCTM().inverse();
      const to=(x,y)=>{const pt=svg.createSVGPoint();pt.x=x;pt.y=y;return pt.matrixTransform(inv2);};
      const a=to(r.left,r.top), b=to(r.right,r.bottom);
      if(vb&&vb.width&&r.width){ let sx=0, sy=0;
        if(a.x<vb.x) sx=vb.x-a.x; else if(b.x>vb.x+vb.width) sx=vb.x+vb.width-b.x;
        if(a.y<vb.y) sy=vb.y-a.y; else if(b.y>vb.y+vb.height) sy=vb.y+vb.height-b.y;
        d.dx+=sx; d.dy+=sy; } }catch(_){}
    if(Math.abs(d.dx)>0.5||Math.abs(d.dy)>0.5){ commit(g,d); preview(); }
  }
  window.addEventListener('pointermove',move);
  window.addEventListener('pointerup',up); window.addEventListener('pointercancel',up);   // a touch the browser takes over still ends the drag
}

function round1(v){return Math.round(v*10)/10;}

// Where a label's offsets live. Desktop view: the entry itself. Phone view: the entry's patch in cfg.mobile
// (a {"<index>": {...}} map itinerary_map.mobile_cfg lays over the list; a mobile block that carries the
// whole list instead is edited in place). The legend and a lone airport are dicts: the phone gets its own copy.
const LIST={stop:'stops',dot:'dots',country:'country_labels',water:'water_labels',leg:'legs',airport:'airports'};
function baseOf(k,i){
  if(k==='legend') return cfg.legend;
  if(k==='airport'&&!cfg.airports) return cfg.airport;
  return (cfg[LIST[k]]||[])[i];
}
function tgt(k,i){
  if(view!=='mob') return baseOf(k,i);
  if(!cfg.mobile||typeof cfg.mobile!=='object') cfg.mobile={};   // the phone build, made at the first phone move (pruned again if nothing lands in it)
  const mob=cfg.mobile;
  if(k==='legend'){ if(!mob.legend) mob.legend=Object.assign({},cfg.legend||{}); return mob.legend; }
  if(k==='airport'&&!cfg.airports){ if(!mob.airport) mob.airport=Object.assign({},cfg.airport||{}); return mob.airport; }
  const key=LIST[k]; let m=mob[key];
  if(Array.isArray(m)) return m[i];
  if(!m||typeof m!=='object') m=mob[key]={};
  return m[i]=m[i]||{};
}
// the entry as the build sees it (desktop values with the phone patch over them), for reading anchor/dx
function eff(k,i){ const b=baseOf(k,i)||{}; return view==='mob'?Object.assign({},b,tgt(k,i)):b; }
// A phone block that says nothing is dropped before a save or a preview, so a map gets a second (phone) build only when
// a phone move was made: tgt() makes an empty patch on the way to a reset or a flip that then changed nothing.
function pruneMobile(){ const mob=cfg&&cfg.mobile; if(!mob||typeof mob!=='object'||Array.isArray(mob)) return;
  Object.keys(mob).forEach(k=>{ const v=mob[k]; if(v&&typeof v==='object'&&!Array.isArray(v)){
    Object.keys(v).forEach(j=>{ const e=v[j]; if(e&&typeof e==='object'&&!Array.isArray(e)&&!Object.keys(e).length) delete v[j]; });
    if(!Object.keys(v).length) delete mob[k]; } });
  if(!Object.keys(mob).length) delete cfg.mobile; }

function commit(g,d){
  const k=g.dataset.kind, i=+g.dataset.idx;
  // data-dx/dy are the offsets the build drew (the phone patch included), and the drag is in the label's own
  // stamp units, so the new offset is their sum; it is written where tgt() says (desktop entry or phone patch)
  if(k==='stop'||k==='dot'||k==='airport'||k==='country'||k==='water'){const o=tgt(k,i); if(!o) return; o.dx=round1(+g.dataset.dx+d.dx); o.dy=round1(+g.dataset.dy+d.dy);}
  else if(k==='legend'){
    // the legend box is placed in WINDOW units from its corner (itinerary_map.py), while its drag handle sits
    // inside the annotation stamp (scale AFS): back to window units before adding
    const o=tgt(k,0); if(!o) return; o.dx=round1(+g.dataset.dx+d.dx*afs); o.dy=round1(+g.dataset.dy+d.dy*afs); }
  else if(k==='leg'){const o=tgt(k,i); if(!o) return; o.chip_off=0; o.chip_side=0; o.chip_dx=round1(+g.dataset.ox+d.dx); o.chip_dy=round1(+g.dataset.oy+d.dy);}
  else if(k==='via'){
    const leg=cfg.legs[i], kk=+g.dataset.k, [vx,vy]=clampToView(g.ownerSVGElement, +g.dataset.x+d.dx, +g.dataset.y+d.dy), ll=unproject(g.ownerSVGElement, vx, vy);
    if(leg&&Array.isArray(leg.via)&&leg.via[kk]&&ll) leg.via[kk]=[Math.round(ll[0]*1e5)/1e5, Math.round(ll[1]*1e5)/1e5];
  }
  markDirty();
}

function flipAnchor(e){
  const g=e.currentTarget, k=g.dataset.kind, i=+g.dataset.idx;
  if(k!=='stop'&&k!=='dot'&&k!=='airport') return;      // pills are centered; nothing to flip
  const o=tgt(k,i), cur=eff(k,i); if(!o) return;
  const a=cur.anchor||'start';
  o.anchor = a==='start'?'end':(a==='end'?'middle':'start');
  // nudge dx to the sensible side so the label doesn't sit on the pin
  if(o.anchor==='start') o.dx=Math.abs(cur.dx||12);
  else if(o.anchor==='end') o.dx=-Math.abs(cur.dx||12);
  else o.dx=0;
  markDirty(); preview();
}
// double-click a country name, a water name or the legend: its dx/dy override goes, and the build puts it back
// where the config's lat/lon (or the legend's corner) says. In the phone view only the phone's own override goes.
function resetPos(e){
  const g=e.currentTarget, k=g.dataset.kind, i=+g.dataset.idx, o=tgt(k,i);
  if(!o||(o.dx===undefined&&o.dy===undefined)) return;
  delete o.dx; delete o.dy;
  markDirty(); preview();
}

function markDirty(){ dirty=true; EDITS++; if(!loading) saveBtn.disabled=false; statusEl.textContent='unsaved changes'; }

// quiet: a redraw (the Phone/Desktop switch, a save in the phone view) that changes nothing, so the status stays
function preview(quiet){
  const my=GEN, s=slug, p=++PSEQ, was=statusEl.textContent; statusEl.textContent='rendering…'; pruneMobile();
  fetch('/api/preview/'+slug+'?view='+view,{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({config:cfg})}).then(r=>r.json()).then(d=>{ if(my!==GEN||s!==slug||p!==PSEQ) return; if(d.error){ statusEl.textContent='could not render: '+d.error; return; } afs=d.afs; if(d.embed_w) embedW=d.embed_w; render(d.svg); statusEl.textContent=quiet?was:'unsaved changes'; })
    .catch(()=>{ if(my===GEN&&p===PSEQ) statusEl.textContent='could not render (server?)'; });
}

// Save posts to the endpoint of the map's own kind, never while a map loads, and an answer that comes
// back after another map was chosen only tells the article editor (the save did happen). A move made
// while the save ran stays unsaved. A failed save says so and can be pressed again.
saveBtn.onclick=()=>{
  if(loading||!slug||!cfg||!kind) return;
  const s=slug, k=kind, my=GEN, e0=EDITS, mine=()=>my===GEN&&s===slug;
  statusEl.textContent='saving…'; saveBtn.disabled=true; PSEQ++;
  const failed=(why)=>{ if(!mine()) return; statusEl.textContent=why; saveBtn.disabled=false; };
  const settled=()=>{ if(EDITS===e0) dirty=false; else saveBtn.disabled=false; };
  if(k==='city'){
    fetch('/api/city/'+s,{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({config:cfg,page:PAGE})}).then(r=>r.json()).then(d=>{
        if(d.error){ failed('not saved: '+d.error); return; }
        if(d.ok) tell({type:'map-saved',city:true,slug:s,page:d.page,desk_svg:d.desk_svg,mob_svg:d.mob_svg});
        if(!mine()) return;
        settled();
        statusEl.textContent = d.ok ? ('saved → '+d.page) : ('embed error: '+d.log);
        if(d.ok&&EDITS===e0){ const p=++PSEQ; fetch('/api/city/'+s+'?page='+encodeURIComponent(PAGE)).then(r=>r.json()).then(x=>{ if(mine()&&p===PSEQ&&x.desk) renderCity(x.desk,x.mob); }).catch(()=>{}); }
      }).catch(()=>failed('could not save (server?): press Save again'));
    return;
  }
  pruneMobile();
  fetch('/api/map/'+s,{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({config:cfg})}).then(r=>r.json()).then(d=>{
      if(d.error){ failed('not saved: '+d.error); return; }
      if(d.ok) tell({type:'map-saved',slug:s,page:d.page,svg:d.svg,figure:d.figure||null});
      if(!mine()) return;
      settled();
      statusEl.textContent = d.ok ? ('saved → '+d.page) : ('embed error: '+d.log);
      if(d.embed_w) embedW=d.embed_w;
      if(EDITS===e0){ if(view==='mob') preview(true); else { afs=d.afs; render(d.svg); } }
    }).catch(()=>failed('could not save (server?): press Save again'));
};
// the answer is checked against the map chosen since, like selectMap/selectCity: a late Revert put
// meteora's config under armenia, and Save would have written it there (QA 2026-10-08). A preview
// still on its way is dropped (PSEQ), and a failed Revert says so.
resetBtn.onclick=()=>{ if(loading) return; if(kind==='city'&&slug){ selectCity(slug); return; } if(!slug) return; const my=GEN, s=slug, p=++PSEQ;
  resetBtn.disabled=true; statusEl.textContent='reverting…';
  fetch('/api/map/'+s).then(r=>r.json()).then(d=>{ if(my!==GEN||s!==slug||p!==PSEQ) return; resetBtn.disabled=false; if(d.error){ statusEl.textContent='could not revert: '+d.error; return; } cfg=d.config;afs=d.afs;if(d.embed_w) embedW=d.embed_w;dirty=false;saveBtn.disabled=true;statusEl.textContent='reverted';if(view==='mob'&&cfg.mobile) preview(true); else render(d.svg);})
    .catch(()=>{ if(my===GEN&&s===slug){ resetBtn.disabled=false; statusEl.textContent='could not revert (server?)'; } }); };
window.addEventListener('beforeunload',e=>{ if(dirty){e.preventDefault();e.returnValue='';} });
</script>
</body></html>"""


if __name__ == "__main__":
    # The Windows console defaults to cp1252, which cannot encode the arrow below. Without
    # this the banner raises UnicodeEncodeError and the server never starts at all, which
    # looks like the editor is broken when it is only the greeting that failed.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # MAP_EDITOR_PORT (or a port as the first argument) runs a test instance beside the suite's :5002
    port = int(os.environ.get("MAP_EDITOR_PORT") or (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].isdigit() else 5002))
    print("Map label editor → http://127.0.0.1:%d" % port)
    app.run(port=port, debug=False)
