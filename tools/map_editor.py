#!/usr/bin/env python3
"""
Itinerary-map label editor — drag the labels/pills of any map and save.

Run:
    python tools/map_editor.py
Then open http://localhost:5002

Pick a map from the sidebar, drag any place label, day count, transport pill,
dot label or airport label to reposition it, then hit Save. Save writes the new
offsets back to tools/itinerary_maps/<slug>.json, rebuilds the SVG and re-embeds
it into the article page (same as running embed_itinerary_map.py).

The canvas shows each map exactly as it will appear on the page (height-capped,
annotations at the standard font size), so what you arrange is what you get.
"""
import json
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
  relation["natural"="water"]($bbox);
);
out geom;
"@
$delay = 5
for ($i = 0; $i -lt 4; $i++) {
  try {
    $r = Invoke-WebRequest -Uri "https://overpass-api.de/api/interpreter" -Method Post -Body @{data=$q} `
           -UserAgent "getawayguide-citymap/1.0 (kevindphan@gmail.com)" -TimeoutSec 300 -UseBasicParsing
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


def ensure_osm(cfg_path):
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
                       capture_output=True, text=True, timeout=1500)
    if r.returncode != 0 or not osm.is_file():
        return "Overpass fetch failed for " + cfg["slug"] + ": " + (r.stdout + r.stderr).strip()[-400:]
    return None


import threading as _th
_CITY_LOCK = _th.Lock()   # one build at a time: they share the preview files and the rasters


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
    rasters = [ROOT / "Images" / "web" / "city-maps" / (slug0 + x) for x in (".png", "-mobile.png")]
    keep = {r: r.read_bytes() for r in rasters if r.is_file()} if not embed else {}
    try:
        cm.build(str(cfg_path), embed=embed, article=article)
    finally:
        # a PREVIEW must not rewrite the tracked base rasters: build() always regenerates
        # them, and a fresher OSM extract makes every pixel differ. Only a Save keeps them.
        for r, data in keep.items():
            r.write_bytes(data)
    slug = __import__("json").loads(Path(cfg_path).read_text(encoding="utf-8"))["slug"]
    frag = (PREV / f"{slug}-city-embed.html").read_text(encoding="utf-8")   # external hrefs, as embedded
    desk = _re.search(r'<div class="citymap desk"><div class="cmmap">(.*?)</div>', frag, _re.S).group(1)
    desk = _re.sub(r'src="[^"]*Images/web/city-maps/([^"]+)"', r'src="/city-png/\1"', desk, count=1)
    desk_svg = _re.search(r'<div class="citymap desk"><div class="cmmap">.*?(<svg.*?</svg>)', frag, _re.S).group(1)
    mob_svg = _re.search(r'<div class="cmworld">.*?(<svg.*?</svg>)', frag, _re.S).group(1)
    return desk, desk_svg, mob_svg


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


def page_for(slug):
    if slug in PAGES:
        return PAGES[slug]
    # prefer the live page; fall back to the Drafts/ copy if not yet published
    if (ROOT / slug / "field-notes.html").exists():
        return f"{slug}/field-notes.html"
    if (ROOT / "Drafts" / slug / "field-notes.html").exists():
        return f"Drafts/{slug}/field-notes.html"
    return f"{slug}/field-notes.html"


def load_cfg(slug):
    return json.loads((CFG_DIR / f"{slug}.json").read_text(encoding="utf-8"))


def final_afs(cfg):
    """The annotation-scale a tall map ends up with (see itinerary_map.render)."""
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


def edit_svg(cfg):
    afs = final_afs(cfg)
    svg, _, _ = im.build({**cfg, "_edit": True, "_afs": afs})
    return svg, afs


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
            parts = [x for x in (c.get("article") or "").split("/") if x and x not in ("Drafts", ".Full Articles")]
            country = parts[0] if len(parts) > 1 else "other"
            groups.setdefault(country, []).append({"kind": "city", "slug": p.stem, "label": c.get("city") or p.stem})
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
    cfg = load_cfg(slug)
    svg, afs = edit_svg(cfg)
    return jsonify(config=cfg, svg=svg, afs=afs, page=page_for(slug))


@app.route("/api/preview/<slug>", methods=["POST"])
def api_preview(slug):
    cfg = request.get_json()["config"]
    svg, afs = edit_svg(cfg)
    return jsonify(svg=svg, afs=afs)


@app.route("/api/map/<slug>", methods=["POST"])
def api_save(slug):
    cfg = request.get_json()["config"]
    (CFG_DIR / f"{slug}.json").write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # rebuild the standalone SVG (final render, with the height/afs treatment)
    svg, cw, ch = im.render(cfg)
    (ROOT / ".tmp" / "previews").mkdir(parents=True, exist_ok=True)
    (ROOT / ".tmp" / "previews" / f"{slug}-map.svg").write_text(svg, encoding="utf-8")
    # re-embed into the article page
    page = page_for(slug)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "embed_itinerary_map.py"),
                        slug, page], cwd=str(ROOT), capture_output=True, text=True)
    ok = r.returncode == 0
    esvg, afs = edit_svg(cfg)
    return jsonify(ok=ok, log=(r.stdout + r.stderr).strip(), svg=esvg, afs=afs, page=page)


@app.route("/api/slug-for")
def api_slug_for():
    """maps that live on this repo-relative page, for the article editor's Edit button:
    itinerary maps by the page their config embeds into, city maps by the marker their
    embedded fragment carries (city-maps/<slug>.png), in the order they appear on the page"""
    page = (request.args.get("page") or "").replace("\\", "/").lstrip("./")
    itin = [p.stem for p in sorted(CFG_DIR.glob("*.json")) if page_for(p.stem) == page]
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
    f = (ROOT / "Images" / "web" / "city-maps" / name).resolve()
    if (ROOT / "Images" / "web" / "city-maps").resolve() not in f.parents or not f.is_file():
        return "", 404
    from flask import send_file
    r = send_file(f, mimetype="image/png"); r.headers["Cache-Control"] = "no-store"; return r


@app.route("/api/city/<slug>")
def api_city(slug):
    cfg_path = CITY_DIR / f"{slug}.json"
    if not cfg_path.is_file():
        return jsonify(error="no such city map"), 404
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    why = ensure_osm(cfg_path)
    if why:
        return jsonify(error=why), 502
    try:
        desk, _, _ = city_render(cfg_path)
    except Exception as e:
        return jsonify(error=f"could not build {slug}: {e}"), 500
    return jsonify(config=cfg, desk=desk, page=city_page_for(slug, request.args.get("page")))


@app.route("/api/city/<slug>/preview", methods=["POST"])
def api_city_preview(slug):
    cfg = request.get_json()["config"]
    tmp = PREV / f"_edit-{slug}.json"
    PREV.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    try:
        desk, _, _ = city_render(tmp)
    except Exception as e:
        return jsonify(error=str(e)), 500
    return jsonify(desk=desk)


@app.route("/api/city/<slug>", methods=["POST"])
def api_city_save(slug):
    d = request.get_json()
    cfg, page = d["config"], d.get("page")
    cfg_path = CITY_DIR / f"{slug}.json"
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    target = city_page_for(slug, page)
    try:
        _, desk_svg, mob_svg = city_render(cfg_path, embed=True, article=target)
        return jsonify(ok=True, desk_svg=desk_svg, mob_svg=mob_svg, page=target)
    except SystemExit as e:                       # build() aborts an embed whose anchors are gone
        return jsonify(ok=False, log=str(e), page=target)
    except Exception as e:
        return jsonify(ok=False, log=str(e), page=target)


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


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
  *{scrollbar-width:thin;scrollbar-color:rgba(28,40,33,.28) transparent}
  ::-webkit-scrollbar{width:6px;height:6px}
  ::-webkit-scrollbar-thumb{background:rgba(28,40,33,.28);border-radius:3px}
  ::-webkit-scrollbar-thumb:hover{background:rgba(28,40,33,.45)}
  ::-webkit-scrollbar-track{background:transparent}
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
  #bar .title{font-weight:600;text-transform:capitalize;font-size:15px}
  #bar button{font:inherit;font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;border:1px solid var(--green);background:var(--green);color:#fff;border-radius:999px;padding:8px 16px;cursor:pointer}
  #bar button.ghost{background:#fff;color:var(--ink);border-color:rgba(28,40,33,.16)}
  #bar button.ghost:hover:not(:disabled){background:#F3F2EC}
  #bar button:disabled{opacity:.4;cursor:default}
  #status{font-size:12px;color:#7a8a7f;margin-left:auto}
  #hint{font-size:12px;color:#6b7a70}
  #stage{flex:1;overflow:auto;display:flex;align-items:center;justify-content:center;padding:24px}
  /* the map fills the window it has (2026-09-27): as wide as fits, never taller than the stage */
  #canvas{background:#F7F7F3;box-shadow:0 6px 30px rgba(0,0,0,.12);border-radius:6px;width:min(100%,1200px)}
  #canvas svg{width:100%;height:auto;max-height:calc(100vh - 110px);display:block}
  #canvas.city{position:relative;width:min(100%,1200px)}
  #canvas.city img.cmbase{width:100%;height:auto;display:block}
  #canvas.city svg{position:absolute;inset:0;width:100%;height:100%}
  #canvas.city text.cmdl:hover,#canvas.city text.cmlab:hover{fill:#2D6B50}
  .eh{cursor:grab}
  .eh:hover>.hit{stroke:var(--green);stroke-dasharray:3 3;stroke-width:1}
  .eh.drag{cursor:grabbing}
  .eh.drag>.hit{stroke:var(--green);stroke-dasharray:none;stroke-width:1.4}
  .hit{fill:transparent;stroke:transparent;vector-effect:non-scaling-stroke}
  #empty{margin:auto;color:#4f5c54;font-size:14px;text-align:center;line-height:1.7}
  #empty b{display:block;font-family:Newsreader,Georgia,serif;font-weight:400;font-size:22px;color:var(--ink)}
  body.embed #side{display:none}
  body.embed #done{display:inline-block}
  #done{display:none}
</style></head>
<body>
<div id="side"><button class="side-handle" id="side-handle" type="button" title="Hide the map list" aria-expanded="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg></button><h1>Maps</h1><input id="q" type="search" placeholder="Find a country or city" autocomplete="off"><div id="list"></div></div>
<div id="main">
  <div id="bar">
    <span class="title" id="ttl">Select a map</span>
    <span id="hint">drag any label or pill · double-click a place label to flip its side</span>
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
  let on = false; try { on = localStorage.getItem('side:maps') === '1'; } catch (e) {}
  apply(on);
  btn.onclick = () => { on = !on; apply(on); try { localStorage.setItem('side:maps', on ? '1' : '0'); } catch (e) {} };
  document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === '\\') { e.preventDefault(); btn.click(); } });
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
if(EMBED) document.body.classList.add('embed');
const tell=(msg)=>{ if(window.parent!==window) window.parent.postMessage(Object.assign({source:'map-editor'},msg),'*'); };
document.getElementById('done').onclick=()=>{ if(dirty && !confirm('Discard the unsaved label moves?')) return; dirty=false; tell({type:'map-done',slug}); };

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
      b.onclick=()=>{ if(dirty && !confirm('Discard the unsaved label moves?')) return; if(m.kind==='city'){ mark(b); selectCity(m.slug); } else selectMap(m.slug,b); };
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
function selectCity(m){
  cityMode=true;
  ttl.textContent='loading '+m.replace(/-/g,' ')+'… (a first open fetches the map data from OpenStreetMap, up to a minute)';
  fetch('/api/city/'+m+'?page='+encodeURIComponent(PAGE)).then(r=>r.json()).then(d=>{
    if(d.error){ ttl.textContent='cannot open this map'; stage.innerHTML='<div id="empty">'+d.error.replace(/</g,'&lt;')+'</div>'; return; }
    slug=m; cfg=d.config; dirty=false;
    ttl.textContent=m.replace(/-/g,' ')+' (city map)'; statusEl.textContent='';
    document.getElementById('hint').textContent='drag a district label · click a place name to move it round its pin';
    renderCity(d.desk); saveBtn.disabled=true; resetBtn.disabled=false;
  });
}
function renderCity(desk){
  stage.innerHTML='<div id="canvas" class="city">'+desk+'</div>';
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
        list[i].lat=+ll.lat.toFixed(6); list[i].lon=+ll.lon.toFixed(6);
        markDirty(); previewCity(); };
      window.addEventListener('pointermove',move); window.addEventListener('pointerup',up,{once:true});
    });
  });
  root.querySelectorAll('text.cmlab').forEach(t=>{
    t.style.cursor='pointer';
    t.addEventListener('click',e=>{
      e.preventDefault(); e.stopPropagation();
      const name=t.closest('.cmpin').dataset.name, poi=cfg.pois.find(p=>p.name===name); if(!poi) return;
      const order=['n','e','s','w']; poi.lab=order[(order.indexOf(poi.lab||'s')+1)%4];
      markDirty(); previewCity();
    });
  });
  root.querySelectorAll('a').forEach(a=>a.addEventListener('click',e=>e.preventDefault()));
}
function previewCity(){
  statusEl.textContent='rendering…';
  fetch('/api/city/'+slug+'/preview',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({config:cfg})}).then(r=>r.json()).then(d=>{ renderCity(d.desk); statusEl.textContent='unsaved changes'; });
}

function selectMap(m,btn){
  cityMode=false; document.getElementById('hint').textContent='drag any label or pill · double-click a place label to flip its side';
  mark(btn);
  fetch('/api/map/'+m).then(r=>r.json()).then(d=>{
    slug=m; cfg=d.config; afs=d.afs; dirty=false;
    ttl.textContent=m.replace(/-/g,' '); statusEl.textContent='';
    render(d.svg); saveBtn.disabled=true; resetBtn.disabled=false;
  });
}

function render(svg){
  stage.innerHTML='<div id="canvas">'+svg+'</div>';
  const root=stage.querySelector('svg');
  root.querySelectorAll('.eh').forEach(g=>{
    const bb=g.getBBox();
    const hit=document.createElementNS(SVGNS,'rect');
    hit.setAttribute('class','hit');
    hit.setAttribute('x',bb.x-3); hit.setAttribute('y',bb.y-3);
    hit.setAttribute('width',bb.width+6); hit.setAttribute('height',bb.height+6);
    g.insertBefore(hit,g.firstChild);
    g.addEventListener('pointerdown',startDrag);
    g.addEventListener('dblclick',flipAnchor);
  });
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
    window.removeEventListener('pointermove',move);
    g.classList.remove('drag');
    if(Math.abs(d.dx)>0.5||Math.abs(d.dy)>0.5){ commit(g,d); preview(); }
  }
  window.addEventListener('pointermove',move);
  window.addEventListener('pointerup',up,{once:true});
}

function round1(v){return Math.round(v*10)/10;}

function commit(g,d){
  const k=g.dataset.kind, i=+g.dataset.idx;
  if(k==='stop'){const o=cfg.stops[i]; o.dx=round1(+g.dataset.dx+d.dx); o.dy=round1(+g.dataset.dy+d.dy);}
  else if(k==='dot'){const o=cfg.dots[i]; o.dx=round1(+g.dataset.dx+d.dx); o.dy=round1(+g.dataset.dy+d.dy);}
  else if(k==='airport'){const o=cfg.airport; o.dx=round1(+g.dataset.dx+d.dx); o.dy=round1(+g.dataset.dy+d.dy);}
  else if(k==='leg'){const o=cfg.legs[i]; o.chip_off=0; o.chip_side=0; o.chip_dx=round1(+g.dataset.ox+d.dx); o.chip_dy=round1(+g.dataset.oy+d.dy);}
  markDirty();
}

function flipAnchor(e){
  const g=e.currentTarget, k=g.dataset.kind, i=+g.dataset.idx;
  let o = k==='stop'?cfg.stops[i] : k==='dot'?cfg.dots[i] : k==='airport'?cfg.airport : null;
  if(!o) return;                       // pills are centered; nothing to flip
  const cur=o.anchor||'start';
  o.anchor = cur==='start'?'end':(cur==='end'?'middle':'start');
  // nudge dx to the sensible side so the label doesn't sit on the pin
  if(o.anchor==='start') o.dx=Math.abs(o.dx||12);
  else if(o.anchor==='end') o.dx=-Math.abs(o.dx||12);
  else o.dx=0;
  markDirty(); preview();
}

function markDirty(){ dirty=true; saveBtn.disabled=false; statusEl.textContent='unsaved changes'; }

function preview(){
  fetch('/api/preview/'+slug,{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({config:cfg})}).then(r=>r.json()).then(d=>{afs=d.afs; render(d.svg);});
}

saveBtn.onclick=()=>{
  statusEl.textContent='saving…'; saveBtn.disabled=true;
  if(cityMode){
    fetch('/api/city/'+slug,{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({config:cfg,page:PAGE})}).then(r=>r.json()).then(d=>{
        dirty=false;
        statusEl.textContent = d.ok ? ('saved → '+d.page) : ('embed error: '+d.log);
        if(d.ok) tell({type:'map-saved',city:true,slug,page:d.page,desk_svg:d.desk_svg,mob_svg:d.mob_svg});
        if(d.ok) fetch('/api/city/'+slug+'?page='+encodeURIComponent(PAGE)).then(r=>r.json()).then(x=>{ if(x.desk) renderCity(x.desk); });
      });
    return;
  }
  fetch('/api/map/'+slug,{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({config:cfg})}).then(r=>r.json()).then(d=>{
      dirty=false; afs=d.afs; render(d.svg);
      statusEl.textContent = d.ok ? ('saved → '+d.page) : ('embed error: '+d.log);
      if(d.ok) tell({type:'map-saved',slug,page:d.page,svg:d.svg});
    });
};
resetBtn.onclick=()=>{ if(cityMode&&slug){ selectCity(slug); return; } if(slug) fetch('/api/map/'+slug).then(r=>r.json()).then(d=>{cfg=d.config;afs=d.afs;dirty=false;render(d.svg);saveBtn.disabled=true;statusEl.textContent='reverted';}); };
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
    print("Map label editor → http://localhost:5002")
    app.run(port=5002, debug=False)
