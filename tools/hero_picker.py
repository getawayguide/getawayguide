"""Hero picker: browse a backup album, see any photo as the article hero it would
become (16:9 crop, scrim, headline), nudge the crop up and down, and save picks.

    python tools/hero_picker.py          then open http://127.0.0.1:5004

Read-only against ~/Backup and Images/. Thumbnails are cached in
.tmp/hero_picker_cache/, picks are written to .tmp/hero_picks.json. Meant to be
folded into the article editor once the theme ships.

Thumbnails are cut to the same 16:9 the hero uses, so what you see in the strip
is the crop you get. They are built by a small thread pool the moment an album
is opened, and JPEGs are decoded at reduced scale, which is most of the reason
the strip fills quickly now.
"""
import hashlib
import json
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from PIL import Image, ImageFilter, ImageOps, ImageStat

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

SITE = Path(__file__).resolve().parent.parent
BACKUP = Path.home() / "Backup"
CACHE = SITE / ".tmp" / "hero_picker_cache"
PICKS = SITE / ".tmp" / "hero_picks.json"
STARS = SITE / ".tmp" / "hero_stars.json"
CACHE.mkdir(parents=True, exist_ok=True)
EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}

# the shapes the site actually cuts, measured off the built preview at 1440
SHAPES = {"home": 1440 / 680,       # the home page's rotating hero
          "article": 1440 / 560,    # the article banner hero
          "wide": 16 / 9}           # plain 16:9, for comparison
HERO_RATIO = SHAPES["home"]
HERO_MIN = 2880          # native px across the crop for a sharp 1440 hero at 2x
HERO_FAIR = 1920         # below this it is visibly soft even at 1x
THUMB_W = 340            # strip thumbnail width
POOL = ThreadPoolExecutor(max_workers=6)

# album folder stem -> country page it feeds
ALBUM_COUNTRY = {
    "albania": "Albania", "argentina & paraguay": "Argentina", "armenia": "Armenia",
    "australia": "Australia", "bali": "Indonesia", "bosnia": "Bosnia", "brazil": "Brazil",
    "cologne": "Germany", "colombia": "Colombia", "el salvador": "El Salvador",
    "georgia": "Georgia", "greece 2.0": "Greece", "guatemala": "Guatemala",
    "india": "India", "italy": "Italy", "japan": "Japan", "kosovo": "Kosovo",
    "mexico": "Mexico", "new zealand": "New Zealand", "nicaragua": "Nicaragua",
    "north macedonia": "North Macedonia", "patagonia": "Chile", "peru": "Peru",
    "philippines": "Philippines", "serbia": "Serbia", "turkey 2.0": "Türkiye",
    "vietnam": "Vietnam",
}
TITLES = {
    "Albania": "Albania Travel Guide: Himarë, Valbona to Theth Hike",
    "El Salvador": "El Salvador Travel Guide with Perfect 10 Day Itinerary",
    "Georgia": "Georgia Travel Guide: Tbilisi & the Kakheti Wine Region",
    "Kosovo": "Kosovo Travel Guide: Pristina, Prizren & the Rugova Valley",
    "Peru": "Peru Travel Guide: Cusco, Machu Picchu & Sacred Valley",
    "Philippines": "Philippines Travel Guide: Coron, El Nido & Port Barton",
    "Türkiye": "Türkiye Travel Guide: Istanbul, Cappadocia & the Coast",
}

app = Flask(__name__)


def _posix(p):
    """Photo paths are compared as strings against /photos output, which is
    as_posix(). An older save wrote them with Windows separators, so a stored
    pick never matched its own photo: the strip highlighted nothing and the
    stage came up blank even though the country read "1 of 27 picked"."""
    return p.replace("\\", "/") if isinstance(p, str) else p


def read_picks():
    if not PICKS.exists():
        return {}
    picks = json.loads(PICKS.read_text(encoding="utf-8"))
    for v in picks.values():
        if isinstance(v, dict) and "path" in v:
            v["path"] = _posix(v["path"])
    return picks


def read_stars():
    """country -> [photo path], the shortlist you keep while you compare."""
    if not STARS.exists():
        return {}
    stars = json.loads(STARS.read_text(encoding="utf-8"))
    return {k: [_posix(p) for p in v] for k, v in stars.items()}


def albums():
    picked = read_picks()
    starred = read_stars()
    out = []
    for p in sorted(BACKUP.iterdir()):
        if not p.is_dir() or p.name.startswith("_"):
            continue
        stem = p.name.split("(")[0].strip().lower()
        country = ALBUM_COUNTRY.get(stem, p.name)
        pick = picked.get(country)
        out.append({"folder": p.name, "country": country,
                    "picked": bool(pick),
                    "pickName": pick["name"] if pick else "",
                    "pickPath": pick["path"] if pick else "",
                    "pickOy": pick.get("objectPositionY", 50) if pick else 50,
                    "pickScrim": pick.get("scrim", 100) if pick else 100,
                    "stars": starred.get(country, [])})
    return out


def cache_path(src, w, crop, ratio):
    key = (f"{src}|{src.stat().st_mtime_ns}|{w}|"
           f"{('c%.4f' % ratio) if crop else 'f'}")
    return CACHE / (hashlib.md5(key.encode()).hexdigest() + ".jpg")


def build(src, w, crop, ratio=None):
    """Render one cached variant. `crop` cuts the same shape the hero uses."""
    ratio = ratio or HERO_RATIO
    cp = cache_path(src, w, crop, ratio)
    if cp.exists():
        return cp
    im = Image.open(src)
    try:                                  # JPEG decodes at 1/2, 1/4, 1/8 for free
        im.draft("RGB", (w * 2, int(w * 2 / ratio)))
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    icc = im.info.get("icc_profile")      # Display P3, must survive the convert
    if crop:
        im = ImageOps.fit(im, (w, round(w / ratio)), Image.LANCZOS,
                          centering=(0.5, 0.5))
    elif im.width > w:
        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    # written aside first so a half-encoded file is never served: two threads
    # can ask for the same variant at once
    tmp = cp.with_name(cp.stem + f".{threading.get_ident()}.part.jpg")
    im.convert("RGB").save(tmp, "JPEG", quality=80, icc_profile=icc)
    try:
        tmp.replace(cp)
    except OSError:
        # the warm pool and the request thread can build the same variant at
        # once; whoever lands first wins and the loser just cleans up
        tmp.unlink(missing_ok=True)
        if not cp.exists():
            raise
    return cp


def focus_score(src):
    """How much fine detail the frame holds. Blurry and heavily compressed shots
    score low; it is only meaningful relative to the rest of the album.

    Measured on the strip thumbnail, which is already being built, so scoring
    the album costs nothing beyond the thumbnails themselves."""
    try:
        g = Image.open(build(src, THUMB_W, True)).convert("L")
        return round(ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).stddev[0], 2)
    except Exception:
        return None


_PHOTO_CACHE = {}
_QUALITY = {}
_QLOCK = threading.Lock()


def warm(folder, rows):
    """Cut every thumbnail and score every frame, off the request thread."""
    def one(r):
        src = BACKUP / r["path"]
        try:
            build(src, THUMB_W, True)
            s = focus_score(src)
        except Exception:
            s = None
        with _QLOCK:
            _QUALITY.setdefault(folder, {})[r["path"]] = s
    for r in rows:
        POOL.submit(one, r)


@app.get("/")
def index():
    return PAGE


@app.get("/albums")
def get_albums():
    return jsonify(albums())


def measure(f):
    """Dimensions only: opening an image reads the header, not the pixels."""
    try:
        with Image.open(f) as im:
            w, h = im.size
            o = im.getexif().get(274, 1)
        if o in (5, 6, 7, 8):               # EXIF rotation swaps the axes
            w, h = h, w
    except Exception:
        return None
    # the hero crops 16:9 out of the frame, so the width is what has to carry
    tier = "good" if w >= HERO_MIN else "fair" if w >= HERO_FAIR else "low"
    return {"path": f.relative_to(BACKUP).as_posix(), "name": f.name,
            "w": w, "h": h, "heroW": w, "tier": tier, "sharp": w >= HERO_MIN}


@app.get("/photos")
def photos():
    folder = BACKUP / request.args["album"]
    stamp = folder.stat().st_mtime_ns
    hit = _PHOTO_CACHE.get(folder.name)
    if hit and hit[0] == stamp:
        warm(folder.name, hit[1])
        return jsonify(hit[1])
    # the scan survives a restart: 400 headers off a synced folder is 15 seconds
    idx = CACHE / (hashlib.md5(folder.name.encode()).hexdigest() + ".index.json")
    if idx.exists():
        saved = json.loads(idx.read_text(encoding="utf-8"))
        if saved.get("stamp") == stamp:
            _PHOTO_CACHE[folder.name] = (stamp, saved["rows"])
            warm(folder.name, saved["rows"])
            return jsonify(saved["rows"])
    files = [f for f in sorted(folder.rglob("*"))
             if f.suffix.lower() in EXT and f.is_file()]
    rows = [r for r in POOL.map(measure, files) if r]
    rows.sort(key=lambda r: r["name"])
    _PHOTO_CACHE[folder.name] = (stamp, rows)
    idx.write_text(json.dumps({"stamp": stamp, "rows": rows}), encoding="utf-8")
    warm(folder.name, rows)
    return jsonify(rows)


@app.get("/quality")
def quality():
    """Focus scores for whatever the pool has finished, plus the album median so
    the page can say which frames are soft relative to their neighbours."""
    folder = request.args["album"]
    with _QLOCK:
        scores = dict(_QUALITY.get(folder, {}))
    vals = [v for v in scores.values() if v is not None]
    return jsonify({"scores": scores, "done": len(scores),
                    "median": round(statistics.median(vals), 2) if vals else None})


@app.get("/img")
def img():
    src = (BACKUP / request.args["p"]).resolve()
    assert BACKUP in src.parents, "outside the backup"
    w = min(int(request.args.get("w", 480)), 2400)
    crop = request.args.get("crop") == "1"
    ratio = SHAPES.get(request.args.get("shape", "home"), HERO_RATIO)
    r = send_file(build(src, w, crop, ratio), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "public, max-age=604800"
    return r


@app.post("/save")
def save():
    picks = read_picks()
    body = dict(request.json)
    body["path"] = _posix(body.get("path", ""))
    picks[body["country"]] = body
    PICKS.write_text(json.dumps(picks, indent=1, ensure_ascii=False), encoding="utf-8")
    return jsonify(ok=True, saved=str(PICKS))


@app.get("/picks")
def get_picks():
    return jsonify(read_picks())


@app.post("/star")
def star():
    """Toggle one photo on a country's shortlist."""
    body = request.json
    stars = read_stars()
    lst = stars.setdefault(body["country"], [])
    body["path"] = _posix(body["path"])
    if body["path"] in lst:
        lst.remove(body["path"])
        on = False
    else:
        lst.append(body["path"])
        on = True
    stars = {k: v for k, v in stars.items() if v}
    STARS.write_text(json.dumps(stars, indent=1, ensure_ascii=False), encoding="utf-8")
    return jsonify(ok=True, starred=on, count=len(stars.get(body["country"], [])))


@app.get("/stars")
def get_stars():
    return jsonify(read_stars())


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hero picker</title>
<style>
  :root { --ink:#1C2821; --terra:#2D6B50; --line:rgba(28,40,33,.14); --warn:#B4553C;
          --amber:#9A7B2E; }
  * { box-sizing:border-box; }
  body { margin:0; background:#fff; color:rgba(28,40,33,.86);
    font:400 14px/1.6 'Hanken Grotesk',-apple-system,'Segoe UI',sans-serif; }
  header { display:flex; gap:10px 12px; align-items:center; padding:12px 18px;
    border-bottom:1px solid var(--line); flex-wrap:wrap; }
  header .tally { white-space:nowrap; }
  header .logo { font-family:Newsreader,Georgia,serif; font-style:italic;
    color:var(--terra); font-size:18px; margin-right:8px; }
  select, input[type=text] { font:inherit; padding:7px 10px; border:1px solid var(--line);
    background:#fff; }
  input[type=text] { flex:1 1 220px; min-width:180px; }
  button { font:inherit; padding:8px 16px; border:1px solid var(--ink); background:var(--ink);
    color:#fff; cursor:pointer; letter-spacing:.08em; text-transform:uppercase;
    font-size:11px; }
  button.q { background:#fff; color:var(--ink); }
  .tally { font-size:11.5px; letter-spacing:.06em; text-transform:uppercase;
    color:rgba(28,40,33,.55); }
  .tally b { color:var(--terra); }
  .main { display:grid; grid-template-columns:minmax(0,1fr) 400px; height:calc(100vh - 59px); }
  .stage { padding:22px 26px; overflow:auto; }
  .hero { position:relative; aspect-ratio:var(--shape,2.118);
    overflow:hidden; background:#eee; }
  .hero img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover;
    object-position:50% var(--oy,50%); user-select:none; -webkit-user-drag:none;
    cursor:grab; }
  .hero img.dragging { cursor:grabbing; }
  /* the scrim strength is live: not every photo needs the same weight */
  .veil { position:absolute; inset:0; pointer-events:none; opacity:var(--scrim,1);
    background:linear-gradient(180deg,
    rgba(18,26,21,.46) 0%, rgba(18,26,21,.16) 38%, rgba(18,26,21,.86) 100%); }
  .cpy { position:absolute; left:3rem; right:3rem; bottom:3.4rem; pointer-events:none; }
  .cpy .eb { font-size:.64rem; letter-spacing:.18em; text-transform:uppercase;
    color:rgba(255,255,255,.78); margin-bottom:.8rem; }
  .cpy h1 { font-family:Newsreader,Georgia,serif; font-weight:300; margin:0;
    font-size:clamp(1.6rem,3vw,2.9rem); line-height:1.07; letter-spacing:-.018em;
    color:#fff; max-width:26ch; }
  .cpy .cta { display:inline-block; margin-top:1.2rem; font-size:.62rem;
    letter-spacing:.16em; text-transform:uppercase; color:#fff;
    border-bottom:1px solid rgba(255,255,255,.5); padding-bottom:.3rem; }
  .meta { display:flex; gap:22px; margin-top:12px; font-size:12px;
    color:rgba(28,40,33,.6); flex-wrap:wrap; align-items:center; }
  .meta b { color:var(--ink); font-weight:500; }
  .meta .bad { color:var(--warn); font-weight:600; }
  .meta .mid { color:var(--amber); font-weight:600; }
  .meta .good { color:var(--terra); font-weight:600; }
  .strip { border-left:1px solid var(--line); overflow:auto; padding:12px;
    display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:8px;
    align-content:start; }
  .strip .t { position:relative; cursor:pointer; border:2px solid transparent;
    background:#f2f2ef; }
  .strip .t.on { border-color:var(--terra); }
  .strip .t.star .fav { color:#F2C124; }
  .strip .fav { position:absolute; left:6px; top:4px; z-index:3; border:0;
    background:transparent; padding:2px 5px; font-size:15px; line-height:1;
    cursor:pointer; color:rgba(255,255,255,.55);
    text-shadow:0 1px 3px rgba(0,0,0,.75); }
  .strip .fav:hover { color:#F2C124; }
  .strip .t.saved::after { content:'SAVED HERO'; position:absolute; left:0; top:0;
    background:var(--terra); color:#fff; font-size:8.5px; letter-spacing:.1em;
    padding:2px 6px; }
  /* the strip cuts the same shape the chosen hero does, so the crop is no
     surprise when you pick */
  .strip img { width:100%; display:block; aspect-ratio:var(--shape,2.118);
    object-fit:cover; }
  .strip .n { position:absolute; left:6px; bottom:6px; font-size:9px; letter-spacing:.06em;
    color:#fff; text-shadow:0 1px 2px rgba(0,0,0,.7); }
  .flag { position:absolute; top:6px; right:6px; font-size:8.5px; color:#fff;
    padding:2px 6px; letter-spacing:.08em; }
  .flag.low { background:var(--warn); }
  .flag.fair { background:var(--amber); }
  .flag.blur { background:var(--warn); top:26px; }
  .hint { font-size:11.5px; color:rgba(28,40,33,.5); margin:8px 0 0; }
  .key { font-size:11px; color:rgba(28,40,33,.5); margin:10px 0 0;
    display:flex; gap:14px; flex-wrap:wrap; align-items:center; }
  .key i { font-style:normal; color:#fff; padding:1px 6px; font-size:8.5px;
    letter-spacing:.08em; }
  .scrim-ctl { display:flex; align-items:center; gap:8px; font-size:11px;
    letter-spacing:.08em; text-transform:uppercase; color:rgba(28,40,33,.55); }
  .scrim-ctl input[type=range] { width:120px; accent-color:var(--terra); }
  .scrim-ctl b { color:var(--ink); font-variant-numeric:tabular-nums;
    min-width:34px; text-align:right; }
  .toast { position:fixed; left:50%; bottom:22px; transform:translateX(-50%);
    background:var(--ink); color:#fff; padding:10px 18px; font-size:12px;
    letter-spacing:.06em; opacity:0; transition:opacity .25s; pointer-events:none; }
  .toast.on { opacity:1; }
</style></head>
<body>
<header>
  <span class="logo">hero picker</span>
  <select id="album"></select>
  <select id="filter">
    <option value="all">Every photo</option>
    <option value="ok" selected>Hide low res</option>
    <option value="best">Hero-ready only</option>
    <option value="star">Starred only</option>
  </select>
  <span class="tally" id="tally"></span>
  <span class="tally" id="count"></span>
  <input type="text" id="title" placeholder="Article title shown on the hero">
  <select id="shape" title="Which hero on the site this photo is destined for">
    <option value="home" selected>Home hero</option>
    <option value="article">Article banner</option>
    <option value="wide">Plain 16:9</option>
  </select>
  <span class="scrim-ctl" title="How heavy the overlay sits on this photo">
    Scrim <input type="range" id="scrim" min="0" max="160" value="100"><b id="scrimv">100%</b>
  </span>
  <button class="q" id="reset">Center crop</button>
  <button id="save">Save pick</button>
</header>
<div class="main">
  <div class="stage">
    <div class="hero" id="hero">
      <img id="pic" draggable="false" alt="">
      <div class="veil"></div>
      <div class="cpy">
        <div class="eb" id="eb">Country &middot; Field notes</div>
        <h1 id="h1">Pick a photo from the strip</h1>
        <span class="cta">Read the guide &rarr;</span>
      </div>
    </div>
    <div class="meta" id="meta"></div>
    <p class="hint">Drag the photo up or down to set the crop. The crop point is saved as
    the CSS object-position the site will use.</p>
    <p class="key">
      <span><i class="flag low" style="position:static">LOW RES</i> under 1920px, blurry at any width</span>
      <span><i class="flag fair" style="position:static">1080p-ish</i> fine at 1x, soft at 2x</span>
      <span><i class="flag blur" style="position:static">SOFT FOCUS</i> less fine detail than the rest of the album</span>
    </p>
  </div>
  <div class="strip" id="strip">Loading&hellip;</div>
</div>
<div class="toast" id="toast"></div>
<script>
const TITLES = %TITLES%;
const SHAPES = %SHAPES%;
let cur = null, oy = 50, country = '', album = '', qTimer = null;
let ALL = [], QUAL = { scores: {}, median: null, done: 0 };
let STARS = [], scrim = 100;

const $ = id => document.getElementById(id);

async function loadAlbums() {
  const rows = await (await fetch('/albums')).json();
  $('album').innerHTML = rows.map(r =>
    `<option value="${r.folder}" data-country="${r.country}"
             data-picked="${r.picked ? 1 : 0}" data-pick="${r.pickPath}"
             data-oy="${r.pickOy}" data-scrim="${r.pickScrim}"
             data-stars="${(r.stars || []).join('|')}">${r.picked ? '✓ ' : '· '}${r.country}</option>`
  ).join('');
  const done = rows.filter(r => r.picked).length;
  $('tally').innerHTML = `<b>${done}</b> of ${rows.length} picked`;
  $('album').onchange = loadStrip;
  await loadStrip();
}

async function loadStrip() {
  const sel = $('album').selectedOptions[0];
  album = sel.value;
  country = sel.dataset.country;
  $('eb').textContent = country + ' · Field notes';
  $('title').value = TITLES[country] || (country + ' Travel Guide');
  $('h1').textContent = $('title').value;
  $('strip').textContent = 'Loading…';
  STARS = (sel.dataset.stars || '').split('|').filter(Boolean);
  ALL = await (await fetch('/photos?album=' + encodeURIComponent(album))).json();
  render();
  pollQuality();
}

/* the strip is for choosing, so by default it hides what could never be a hero */
function render() {
  const sel = $('album').selectedOptions[0];
  const mode = $('filter').value;
  const shape = $('shape').value;
  const saved0 = sel.dataset.pick;
  // whatever is already saved stays visible, even when the filter would hide it
  const rows = ALL.filter(r => r.path === saved0 || (
      mode === 'all'  ? true
    : mode === 'star' ? STARS.includes(r.path)
    : mode === 'best' ? r.tier === 'good'
    :                   r.tier !== 'low'));
  const hidden = ALL.length - rows.length;
  const saved = sel.dataset.pick;
  $('count').textContent = hidden
    ? `${rows.length} shown, ${hidden} hidden` : `${rows.length} photos`;
  $('strip').innerHTML = rows.map(r => `
    <div class="t${r.path === saved ? ' saved' : ''}${STARS.includes(r.path) ? ' star' : ''}"
         data-p="${r.path}" data-w="${r.heroW}" data-name="${r.name}" data-tier="${r.tier}">
      <button class="fav" title="Shortlist this photo">${STARS.includes(r.path) ? '★' : '☆'}</button>
      <img loading="lazy" decoding="async"
           src="/img?crop=1&w=340&shape=${shape}&p=${encodeURIComponent(r.path)}">
      ${r.tier === 'low' ? '<span class="flag low">LOW RES</span>'
        : r.tier === 'fair' ? '<span class="flag fair">' + r.w + 'px</span>' : ''}
      <span class="n">${r.name}</span>
    </div>`).join('');
  document.querySelectorAll('.strip .t').forEach(t => t.onclick = () => pick(t));
  document.querySelectorAll('.strip .fav').forEach(f => f.onclick = ev => {
    ev.stopPropagation();          // starring is not choosing
    toggleStar(f.closest('.t'));
  });
  $('count').textContent += STARS.length ? ` · ${STARS.length} starred` : '';
  if (saved) {
    const t = document.querySelector(`.strip .t[data-p="${CSS.escape(saved)}"]`);
    if (t) { pick(t); oy = +sel.dataset.oy || 50; setOy();
             setScrim(+sel.dataset.scrim || 100);
             t.scrollIntoView({block:'center'}); }
  }
  applyFocusFlags();
}

$('filter').onchange = () => { render(); };

/* the site cuts two different hero shapes; the picker shows whichever one this
   photo is destined for, in the stage and in the strip alike */
$('shape').onchange = () => {
  document.documentElement.style.setProperty('--shape', SHAPES[$('shape').value]);
  render();
};

/* the focus scores arrive as the pool finishes; label the soft frames when they do */
function applyFocusFlags() {
  if (!QUAL.median) { return 0; }
  const cut = QUAL.median * 0.62;
  let pending = 0;
  document.querySelectorAll('.strip .t').forEach(t => {
    const s = QUAL.scores[t.dataset.p];
    if (s === undefined) { pending += 1; return; }
    const soft = s !== null && s < cut;
    const has = t.querySelector('.flag.blur');
    if (soft && !has) {
      const el = document.createElement('span');
      el.className = 'flag blur';
      el.textContent = 'SOFT FOCUS';
      t.appendChild(el);
    } else if (!soft && has) { has.remove(); }
    t.dataset.focus = s === null ? '' : s;
    t.dataset.cut = cut.toFixed(2);
  });
  return pending;
}

function pollQuality() {
  clearInterval(qTimer);
  const mine = album;
  qTimer = setInterval(async () => {
    if (album !== mine) { return clearInterval(qTimer); }
    QUAL = await (await fetch('/quality?album=' + encodeURIComponent(mine))).json();
    if (!applyFocusFlags() && QUAL.done >= ALL.length) { clearInterval(qTimer); }
  }, 1200);
}

function pick(t) {
  document.querySelectorAll('.strip .t.on').forEach(x => x.classList.remove('on'));
  t.classList.add('on');
  cur = { path: t.dataset.p, name: t.dataset.name, w: +t.dataset.w,
          tier: t.dataset.tier };
  oy = 50;
  $('pic').style.setProperty('--oy', '50%');
  $('pic').src = '/img?w=1600&p=' + encodeURIComponent(cur.path);
  const q = cur.tier === 'good'
    ? '<span class="good">sharp at 2× on a 1440 hero</span>'
    : cur.tier === 'fair'
      ? '<span class="mid">fine at 1×, soft at 2×</span>'
      : '<span class="bad">too low resolution for a hero</span>';
  const f = t.dataset.focus;
  const soft = f && t.dataset.cut && +f < +t.dataset.cut
    ? ' <span class="bad">soft focus</span>' : '';
  $('meta').innerHTML = `<span><b>${cur.name}</b></span>` +
    `<span>${cur.w}px wide</span>` + q + soft + `<span id="oyv">crop 50%</span>`;
}

async function toggleStar(t) {
  const r = await (await fetch('/star', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ country, path: t.dataset.p }) })).json();
  t.classList.toggle('star', r.starred);
  t.querySelector('.fav').textContent = r.starred ? '★' : '☆';
  STARS = r.starred ? STARS.concat([t.dataset.p])
                    : STARS.filter(x => x !== t.dataset.p);
  const sel = $('album').selectedOptions[0];
  sel.dataset.stars = STARS.join('|');
  if ($('filter').value === 'star' && !r.starred) { render(); }
}

/* the scrim is what makes a bright photo readable and a dark one muddy, so it
   is adjustable per pick and saved with it */
function setScrim(v) {
  scrim = Math.max(0, Math.min(160, Math.round(v)));
  $('scrim').value = scrim;
  $('scrimv').textContent = scrim + '%';
  $('hero').style.setProperty('--scrim', scrim / 100);
}
$('scrim').addEventListener('input', e => setScrim(+e.target.value));

$('title').addEventListener('input', () => { $('h1').textContent = $('title').value; });
$('reset').onclick = () => { oy = 50; setOy(); };

function setOy() {
  oy = Math.max(0, Math.min(100, oy));
  $('pic').style.setProperty('--oy', oy + '%');
  const v = document.getElementById('oyv');
  if (v) { v.textContent = 'crop ' + Math.round(oy) + '%'; }
}

let drag = null;
$('hero').addEventListener('pointerdown', e => {
  if (!cur) { return; }
  drag = { y: e.clientY, oy };
  $('pic').classList.add('dragging');
  $('hero').setPointerCapture(e.pointerId);
});
$('hero').addEventListener('pointermove', e => {
  if (!drag) { return; }
  const img = $('pic'), box = $('hero').getBoundingClientRect();
  const imgH = img.naturalHeight * (box.width / img.naturalWidth);
  const spare = Math.max(1, imgH - box.height);
  oy = drag.oy - (e.clientY - drag.y) / spare * 100;
  setOy();
});
['pointerup', 'pointercancel'].forEach(ev => $('hero').addEventListener(ev, () => {
  drag = null; $('pic').classList.remove('dragging');
}));

$('save').onclick = async () => {
  if (!cur) { return toast('Pick a photo first'); }
  const body = { country, path: cur.path, name: cur.name,
                 objectPositionY: Math.round(oy * 10) / 10,
                 scrim: scrim,
                 title: $('title').value };
  const r = await (await fetch('/save', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
  toast('Saved to ' + r.saved);
  const keep = $('album').value;
  await loadAlbums.reload(keep);
};

/* re-read the picks so the tick and the tally update without losing your place */
loadAlbums.reload = async keep => {
  const rows = await (await fetch('/albums')).json();
  const done = rows.filter(x => x.picked).length;
  $('tally').innerHTML = `<b>${done}</b> of ${rows.length} picked`;
  [...$('album').options].forEach(o => {
    const row = rows.find(x => x.folder === o.value);
    if (!row) { return; }
    o.dataset.picked = row.picked ? 1 : 0;
    o.dataset.pick = row.pickPath;
    o.dataset.oy = row.pickOy;
    o.dataset.scrim = row.pickScrim;
    o.dataset.stars = (row.stars || []).join('|');
    // The album folder is the TRIP name, the country is the page it feeds, and
    // the two often differ: "Patagonia (2024)" is filed under Chile, "Bali"
    // under Indonesia, "Turkey 2.0" under Türkiye. Showing only the country
    // made those albums look missing to anyone looking for the trip.
    var trip = (row.folder || '').replace(/\s*\(\d{4}\)\s*$/, '').trim();
    var same = trip.toLowerCase() === (row.country || '').toLowerCase();
    o.textContent = (row.picked ? '✓ ' : '· ') + row.country +
                    (same ? '' : '  (' + trip + ')');
  });
  $('album').value = keep;
  document.querySelectorAll('.strip .t.saved').forEach(t => t.classList.remove('saved'));
  const t = document.querySelector('.strip .t.on');
  if (t) { t.classList.add('saved'); }
};

function toast(t) {
  const el = $('toast');
  el.textContent = t; el.classList.add('on');
  setTimeout(() => el.classList.remove('on'), 2600);
}

document.documentElement.style.setProperty('--shape', SHAPES.home);
setScrim(100);
loadAlbums();
</script>
</body></html>
""".replace("%TITLES%", json.dumps(TITLES, ensure_ascii=False)
          ).replace("%SHAPES%", json.dumps(SHAPES))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5004, debug=False, threaded=True)
