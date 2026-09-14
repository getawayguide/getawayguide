"""Where was each photo taken, in the site's own place names.

Two halves, both cached:

  1. GPS from EXIF.  ~90% of the shared-album photos carry a lat/lon (iCloud
     shared albums keep EXIF), read without decoding pixels. Results live in
     .tmp/photo_editor/geo.sqlite keyed by path + mtime, so an album costs one
     pass (~10s per 1,000 files) and is instant after that. Uncached folders
     are filled in on a small thread pool while the UI shows a "locating…"
     count, unless the batch is small enough to just do inline.

  2. A place index derived from the site itself, so no per-country config:
       - tools/city_maps/*.json        POIs (curated names) + the city center
       - tools/itinerary_maps/*.json   stops = cities with coordinates
       - every Google Maps place link in the live pages and drafts, whose URL
         carries the pin's !3d<lat>!4d<lon> and whose link text is the name
     A photo is assigned to the nearest named place within that place's
     radius, else "Elsewhere in <nearest city>", else "Unplaced". Radii come
     from what the name says it is: a market or cafe is a couple of hundred
     meters, a monastery or temple a kilometre and a bit, a lake several.

Used by tools/photo_editor.py (/api/geo) for the editor sidebar, the Blog
Picks panel and the Photo Library view.
"""
import glob
import json
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ExifTags

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:                                   # HEIC then just reads as "no GPS"
    pass

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / ".tmp" / "photo_editor" / "geo.sqlite"
IMG_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}

# ------------------------------------------------------------------ EXIF ----
_GPS = ExifTags.IFD.GPSInfo
_EXIF = ExifTags.IFD.Exif
_DTO = 36867                                        # DateTimeOriginal


def _dms(v, ref):
    d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
    return -d if ref in ("S", "W") else d


def exif_geo(path):
    """(lat, lon, DateTimeOriginal) from a photo; (None, None, dto) without GPS."""
    with Image.open(path) as im:
        e = im.getexif()
        dto = e.get_ifd(_EXIF).get(_DTO)
        g = e.get_ifd(_GPS)
        if g and 2 in g and 4 in g:
            try:
                return _dms(g[2], g.get(1, "N")), _dms(g[4], g.get(3, "E")), dto
            except Exception:
                pass
        return None, None, dto


_db_lock = threading.Lock()


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")           # readers never wait on the writer
    con.execute("CREATE TABLE IF NOT EXISTS geo (path TEXT PRIMARY KEY, mtime INTEGER, "
                "lat REAL, lon REAL, dto TEXT, ok INTEGER)")
    return con


def _read_cached(paths):
    """{path: (lat, lon, dto)} for every path whose cached mtime still matches."""
    out = {}
    with _db_lock, _db() as con:
        for i in range(0, len(paths), 500):
            chunk = paths[i:i + 500]
            q = ",".join("?" * len(chunk))
            for p, mt, lat, lon, dto in con.execute(
                    f"SELECT path, mtime, lat, lon, dto FROM geo WHERE path IN ({q})",
                    [str(x) for x in chunk]):
                out[p] = (mt, lat, lon, dto)
    res = {}
    for p in paths:
        row = out.get(str(p))
        if row is None:
            continue
        try:
            if row[0] == p.stat().st_mtime_ns:
                res[p] = row[1:]
        except OSError:
            pass
    return res


_writeq = []                                          # rows waiting for one batched commit
_writeq_lock = threading.Lock()


def _write(p, lat, lon, dto):
    """Queue a row; _flush commits the queue in one transaction. One connection
    and one commit per photo (the first version) held the lock ~40 ms each,
    which starved the reads a folder switch needs while a big album was
    still being located."""
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return
    with _writeq_lock:
        _writeq.append((str(p), mt, lat, lon, str(dto) if dto else None))


def _flush():
    with _writeq_lock:
        rows, _writeq[:] = list(_writeq), []
    if not rows:
        return
    with _db_lock, _db() as con:
        con.executemany("INSERT OR REPLACE INTO geo VALUES (?,?,?,?,?,1)", rows)


_pool = ThreadPoolExecutor(max_workers=3)
_inflight = set()
_inflight_lock = threading.Lock()


def _locate(p):
    try:
        lat, lon, dto = exif_geo(p)
    except Exception:
        lat, lon, dto = None, None, None
    _write(p, lat, lon, dto)
    with _inflight_lock:
        _inflight.discard(str(p))
        drain = not _inflight or len(_writeq) >= 40
    if drain:
        _flush()


_RECALL, _OFFLINE = 0x400000, 0x1000        # Windows cloud-placeholder attributes


def is_cloud_placeholder(p):
    """An iCloud / OneDrive on-demand file that has not been downloaded. Opening
    it, even just for EXIF, pulls the whole file down - a 9k-photo iCloud
    library is >100GB, so these are never read here. They count as unlocated."""
    try:
        a = getattr(os.stat(p), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(a & _RECALL or a & _OFFLINE)


def locate(paths, inline_limit=120):
    """Geo for a batch of photos. Cached rows come back now; the rest are
    read inline when few, else queued for the pool and reported as pending.
    Cloud-only placeholders are skipped entirely (see is_cloud_placeholder)."""
    paths = [Path(p) for p in paths]
    _flush()                                          # anything the pool finished since last call
    have = _read_cached(paths)
    todo = [p for p in paths if p not in have and not is_cloud_placeholder(p)]
    if todo and len(todo) <= inline_limit:
        for p in todo:
            _locate(p)
        have = _read_cached(paths)
        todo = [p for p in paths if p not in have]
    pending = 0
    for p in todo:
        with _inflight_lock:
            if str(p) in _inflight:
                pending += 1
                continue
            _inflight.add(str(p))
        pending += 1
        _pool.submit(_locate, p)
    return {p.name: {"lat": v[0], "lon": v[1], "dto": v[2]} for p, v in have.items()}, pending


# ---------------------------------------------------------- place index ----
def _hav(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


# what a name says it is decides how far from the pin a photo can be
_BIG = re.compile(r"\b(lake|lago|laguna|national park|parque nacional|reserve|reserva|island|isla|"
                  r"bay|beach|playa|volcano|volcán|canyon|glacier|peninsula|mount|mt\.?|cerro|hike|"
                  r"trail|crossing|track|falls|waterfall|cascadas|springs|gorge|valley|desert|"
                  r"ruins|archaeological|observatory|telescope|fortress|castle|citadel|"
                  r"monastery|monasterio|temple|cathedral|basilica|abbey|sanctuary|park|jardín|garden|"
                  r"wall|mine|quarry|plane|airplane|aircraft|dam|reservoir|viewpoint|mirador|summit|"
                  r"peak|pass|cemetery|cementerio|festival|stadium|university|airport)\b", re.I)
_HUGE = re.compile(r"\b(lake|lago|national park|parque nacional|island|isla|bay|volcano|volcán|"
                   r"glacier|peninsula|desert|valley|crossing|track)\b", re.I)


def _radius(name):
    if _HUGE.search(name):
        return 4000
    if _BIG.search(name):
        return 1200
    return 260


def _norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.lower())).strip()


def _clean_name(t):
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"&amp;", "&", t)
    t = re.sub(r"&[a-z]+;", "", t)
    t = re.sub(r"\s+", " ", t).strip(" ,.;:—–-")
    return t


_index = {"sig": None, "places": [], "cities": [], "built": 0}
_index_lock = threading.Lock()


def _sources():
    files = (glob.glob(str(ROOT / "*" / "*.html"))
             + glob.glob(str(ROOT / "Drafts" / "*" / "*.html"))
             + glob.glob(str(ROOT / "Drafts" / ".Full Articles" / "*" / "*.html"))
             + glob.glob(str(ROOT / "tools" / "city_maps" / "*.json"))
             + glob.glob(str(ROOT / "tools" / "itinerary_maps" / "*.json")))
    return sorted(set(files))


def _build():
    places, cities = [], []
    # 1. city maps: curated POI names, and the city itself
    for f in glob.glob(str(ROOT / "tools" / "city_maps" / "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        city = d.get("city") or Path(f).stem.title()
        c = d.get("center") or {}
        if "lat" in c and "lon" in c:
            cities.append({"name": city, "lat": c["lat"], "lon": c["lon"], "r": 7000})
        for p in d.get("pois", []) + d.get("day_trips", []):
            if "lat" in p and "lon" in p and p.get("name"):
                places.append({"name": _clean_name(p["name"]), "lat": p["lat"], "lon": p["lon"], "src": 0})
    # 2. itinerary stops: cities/towns with coordinates
    for f in glob.glob(str(ROOT / "tools" / "itinerary_maps" / "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for s in d.get("stops", []):
            if "lat" in s and "lon" in s and s.get("name"):
                cities.append({"name": _clean_name(s["name"]), "lat": s["lat"], "lon": s["lon"], "r": 9000})
    # 3. every Google Maps place link on the site: the pin is the LAST !3d!4d in
    #    the URL (an earlier pair is the map's previous context)
    # greedy [^"]* so the LAST !3d!4d pair in the URL is captured: a Maps URL
    # often carries an earlier pair for the previous place the map was showing
    link = re.compile(r'<a href="https://www\.google\.com/maps/place/[^"]*?!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)(?![^"]*!3d)[^"]*"[^>]*>(.*?)</a>', re.S)
    for f in (glob.glob(str(ROOT / "*" / "*.html")) + glob.glob(str(ROOT / "Drafts" / "*" / "*.html"))
              + glob.glob(str(ROOT / "Drafts" / ".Full Articles" / "*" / "*.html"))):
        try:
            s = Path(f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in link.finditer(s):
            name = _clean_name(m.group(3))
            if 2 <= len(name) <= 60 and not name.lower().startswith(("http", "more in", "link")):
                places.append({"name": name, "lat": float(m.group(1)), "lon": float(m.group(2)), "src": 1})
    # dedupe: same spot within 80 m is one place; curated names win, then the
    # first (usually the field-notes bold lead) — and drop a leading "the"
    places.sort(key=lambda p: (p["src"], len(p["name"])))
    # a link whose text IS a city name ("Antigua", "Yerevan") is the city's pin,
    # not a place to photograph: it becomes a city entry, never a POI, so it
    # cannot be folded into whatever POI happens to sit nearest that pin
    city_names = {_norm(c["name"]) for c in cities}
    merged = []
    for p in places:
        nm = re.sub(r"^(the|el|la|los|las)\s+", "", p["name"], flags=re.I) or p["name"]
        nn = _norm(nm)
        if nn in city_names:
            if not any(_hav(c["lat"], c["lon"], p["lat"], p["lon"]) < 3000 for c in cities):
                cities.append({"name": nm, "lat": p["lat"], "lon": p["lon"], "r": 7000})
            continue
        for q in merged:
            if abs(q["lat"] - p["lat"]) > 0.01 or abs(q["lon"] - p["lon"]) > 0.013:
                continue
            d = _hav(q["lat"], q["lon"], p["lat"], p["lon"])
            qn = _norm(q["name"])
            # the same pin linked twice, or "Cascade" next to "Cascade Complex":
            # one place, however many names the articles used for it
            # 25 m is the same pin linked twice; anything wider merged neighbours
            # (a cafe 60 m from a bar became an alias of it, so photos of one
            # filtered under the other's name)
            if d < 25 or (d < 700 and nn and qn and (nn in qn or qn in nn)):
                q["aliases"].add(nn)
                break
        else:
            merged.append({"name": nm, "lat": p["lat"], "lon": p["lon"], "r": _radius(nm),
                           "aliases": {_norm(nm)}})
    cmerged = []
    for c in sorted(cities, key=lambda c: -c["r"]):
        if not any(_hav(q["lat"], q["lon"], c["lat"], c["lon"]) < 3000 for q in cmerged):
            cmerged.append(c)
    # each place remembers its group: the nearest city within 60 km, else itself
    for p in merged:
        best = min(((_hav(p["lat"], p["lon"], c["lat"], c["lon"]), c["name"]) for c in cmerged),
                   default=(None, None))
        p["city"] = best[1] if best[0] is not None and best[0] < 60000 else "Other"
        p["aliases"] = sorted(p["aliases"])
    return merged, cmerged


def places_index():
    """The place list, rebuilt when any source file changes (checked at most
    every 30 s so a burst of /api/geo calls does not re-scan 200 files)."""
    with _index_lock:
        now = time.time()
        if _index["places"] and now - _index["built"] < 30:
            return _index["places"], _index["cities"]
        files = _sources()
        sig = hash(tuple((f, os.path.getmtime(f)) for f in files if os.path.exists(f)))
        if sig != _index["sig"]:
            _index["places"], _index["cities"] = _build()
            _index["sig"] = sig
        _index["built"] = now
        return _index["places"], _index["cities"]


def assign(lat, lon):
    """(place, city) for a coordinate: nearest named place inside its radius,
    else 'Elsewhere in <city>' for the nearest city inside its radius."""
    places, cities = places_index()
    best = None
    for p in places:
        if abs(p["lat"] - lat) > 0.1 or abs(p["lon"] - lon) > 0.13:     # cheap prefilter (~10 km)
            continue
        d = _hav(lat, lon, p["lat"], p["lon"])
        if d <= p["r"] and (best is None or d < best[0]):
            best = (d, p)
    if best:
        return best[1]["name"], best[1]["city"]
    cb = None
    for c in cities:
        if abs(c["lat"] - lat) > 0.6 or abs(c["lon"] - lon) > 0.8:
            continue
        d = _hav(lat, lon, c["lat"], c["lon"])
        if cb is None or d < cb[0]:
            cb = (d, c)
    if cb and cb[0] <= cb[1]["r"]:
        return f"Elsewhere in {cb[1]['name']}", cb[1]["name"]
    if cb and cb[0] <= 60000:                        # a road stop, a village, a lake shore
        return f"Near {cb[1]['name']}", cb[1]["name"]
    return "Unplaced", "Other"


def folder_geo(paths):
    """Everything the UI needs for one folder of photos."""
    geo, pending = locate(paths)
    photos, groups = {}, {}
    no_gps = 0
    for name, g in geo.items():
        if g["lat"] is None:
            no_gps += 1
            photos[name] = {"place": None, "city": None, "dto": g["dto"]}
            continue
        place, city = assign(g["lat"], g["lon"])
        photos[name] = {"place": place, "city": city, "lat": round(g["lat"], 5),
                        "lon": round(g["lon"], 5), "dto": g["dto"]}
        groups.setdefault(city, {})
        groups[city][place] = groups[city].get(place, 0) + 1
    out_groups = []
    for city, pl in groups.items():
        items = sorted(pl.items(), key=lambda kv: (kv[0].startswith("Elsewhere"), -kv[1]))
        out_groups.append({"city": city, "n": sum(pl.values()),
                           "places": [{"name": k, "n": v} for k, v in items]})
    out_groups.sort(key=lambda g: -g["n"])
    places, _ = places_index()
    names = sorted({p["name"] for p in places if any(
        p["name"] == x["name"] for g in out_groups for x in g["places"])})
    aliases = {p["name"]: p["aliases"] for p in places if p["name"] in names}
    return {"photos": photos, "groups": out_groups, "noGps": no_gps,
            "pending": pending, "located": len(geo), "total": len(paths), "aliases": aliases}


if __name__ == "__main__":                          # quick check: python tools/photo_geo.py "Armenia (2026)"
    import sys
    alb = Path.home() / "Backup" / (sys.argv[1] if len(sys.argv) > 1 else "Armenia (2026)")
    files = [p for p in alb.iterdir() if p.suffix.lower() in IMG_EXTS and not p.name.startswith("_")]
    t = time.time()
    r = folder_geo(files)
    while r["pending"]:
        time.sleep(1)
        r = folder_geo(files)
    print(f"{alb.name}: {r['located']}/{r['total']} located in {time.time()-t:.1f}s, no GPS {r['noGps']}")
    for g in r["groups"]:
        print(f"  {g['city']} ({g['n']})")
        for p in g["places"]:
            print(f"      {p['n']:4}  {p['name']}")
