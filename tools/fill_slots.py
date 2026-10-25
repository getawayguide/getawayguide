#!/usr/bin/env python3
"""Fill a draft's empty [PHOTO] slots with Kevin's own photos (Kevin, 2026-10-24: "Add photos in empty slots").

The choosing is judgment (which photo shows what this paragraph is about); everything else is mechanical and lives
here, so a choice is placed exactly the way the editor places one:

    python tools/fill_slots.py slots "<draft>"              # the empty slots, in page order, with their context (JSON)
    python tools/fill_slots.py photos "<Album (Year)>"      # every photo: time, GPS, the site's place name, Kevin's star
    python tools/fill_slots.py sheet "<Album>" [--place X] [--picks] [--files a,b,..] [--out f.jpg]
                                                            # a labeled contact sheet (EXIF-rotated thumbnails, 4 across)
    python tools/fill_slots.py used <country>               # photos already on the country's pages (never use one twice)
    python tools/fill_slots.py apply "<draft>" plan.json [--no-pipeline]

plan.json: {"slots": <the count `slots` reported>, "photos": [{"slot": 3, "photo": "Greece (2023)/IMG_1234.HEIC",
            "alt": "...", "city": "Athens", "pos": "50% 40%"}, ...]}

apply: each photo is archived through the photo server's /api/import (HEIC -> JPEG, ICC kept, Images/<Country>/<City>/;
Kevin's edited copy is reused when it is already there), the slot's [PHOTO] placeholder is replaced by the <img> the
editor's slotAdopt() writes for that frame, the page is written once (refused if it changed meanwhile), then the
draft pipeline runs (tools/draft_images.py, tools/fit_image_tiers.py). Photo markup never goes through a review round.
Needs the photo server on :5003.
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
API = "http://127.0.0.1:5003"
BACKUP = Path.home() / "Backup"
PICKS = BACKUP / "_meta" / "picks.json"
CACHE = ROOT / ".tmp" / "fill_slots"
IMG_EXT = (".jpg", ".jpeg", ".heic", ".png")
SLOT = re.compile(r'<(div|span) class="photo-ph"[^>]*>\[PHOTO\]</\1>')     # span: the editor's single Portrait frame
# the 2024 drafts (New Zealand): the frame itself is the placeholder, `<div style="aspect-ratio:2/3;...">[photo]</div>` in
# an .img-pair or `<div class="img-landscape" style="...">[photo]</div>`; filled, it becomes today's markup
OLD_SLOT = re.compile(r'<div\b([^>]*)>\s*\[(?:photo|picture)\]\s*</div>')      # [picture]: the NZ itinerary's day boxes
PAIR_CELL = "display:block;position:relative;overflow:hidden;width:100%;aspect-ratio:2/3;border-radius:2px;"


def _slot_matches(s):
    """every empty slot in the article BODY, in page order: (match, old). A "[picture]" left in the New Zealand
    itinerary's hero is the cover's placeholder, not a photo slot (covers are Kevin's, in Covers)."""
    a = s.find('class="article-body"')
    a = a if a >= 0 else s.find('class="artbody"')
    ms = [(m, False) for m in SLOT.finditer(s)] + [(m, True) for m in OLD_SLOT.finditer(s)]
    return sorted([x for x in ms if x[0].start() > a], key=lambda x: x[0].start())
OPEN_TAG = re.compile(r"<(span|div)\b([^>]*)>\s*$", re.S)
COUNTRY_DIR = {"turkiye": "Turkiye", "new-zealand": "New Zealand", "north-macedonia": "North Macedonia",
               "el-salvador": "El Salvador"}


def _read(p):
    return io.open(p, encoding="utf-8", newline="").read()


def _text(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()


def _get(path):
    with urllib.request.urlopen(API + path, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(path, body):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def country_dir(country):
    return COUNTRY_DIR.get(country, country.replace("-", " ").title())


# ------------------------------------------------------------------------------------------------ slots
def slots(page):
    """[{slot, shape, ratio, section, before, after}] for every empty slot, in page order"""
    s = _read(ROOT / page)
    out = []
    heads = [(m.start(), m.group(1), _text(m.group(2))) for m in re.finditer(r"<(h2|h3)\b[^>]*>(.*?)</\1>", s, re.S)]
    for i, (m, old) in enumerate(_slot_matches(s)):
        if old:
            portrait = "2/3" in m.group(1)
            shape, ratio = ("pair", 2 / 3) if portrait else ("landscape", 1.5)
        else:
            pre = s[max(0, m.start() - 600):m.start()]
            po = OPEN_TAG.search(pre)
            tag, attrs = (po.group(1), po.group(2)) if po else ("?", "")
            cls = (re.search(r'class="([^"]*)"', attrs) or [None, ""])[1]
            ar = re.search(r"aspect-ratio:\s*([\d.]+)\s*/\s*([\d.]+)", attrs + m.group(0))
            ratio = float(ar.group(1)) / float(ar.group(2)) if ar else (1.5 if "img-landscape" in cls or cls in ("ls",) else 2 / 3)
            shape = ("pair" if tag == "span" else "landscape" if "img-landscape" in cls else cls or ("frame" if ar else "?"))
        sec = [h for at, lv, h in heads if at < m.start()]
        h2 = [h for at, lv, h in heads if at < m.start() and lv == "h2"]
        before = _text(s[max(0, m.start() - 2500):m.start()])[-320:]
        after = _text(s[m.end():m.end() + 2500])[:320]
        out.append({"slot": i, "shape": shape, "orient": "portrait" if ratio < 1 else "landscape", "ratio": round(ratio, 3),
                    "section": sec[-1] if sec else "", "h2": h2[-1] if h2 else "",
                    "before": before, "after": after})
    return out


# ------------------------------------------------------------------------------------------------ photos
def _picks():
    try:
        return json.load(open(PICKS, encoding="utf-8"))
    except Exception:
        return {}


def photos(album):
    """every photo in a Backup album with time, GPS, place (the site's names), size, star and note; cached per album"""
    import photo_geo
    d = BACKUP / album
    files = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXT and not p.name.startswith((".", "_"))
                   and "thumb_" not in p.name)
    CACHE.mkdir(parents=True, exist_ok=True)
    cf = CACHE / (re.sub(r"[^\w.-]+", "_", album) + ".json")
    cache = json.load(open(cf, encoding="utf-8")) if cf.is_file() else {}
    picks = _picks()
    out = []
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    from PIL import Image, ImageOps
    changed = False
    heics = {q.stem.lower() for q in files if q.suffix.lower() == ".heic"}
    for p in files:
        key = "%s|%d" % (p.name, int(p.stat().st_mtime))
        row = cache.get(key)
        if not row:
            try:
                lat, lon, dto = photo_geo.exif_geo(p)
            except Exception:
                lat = lon = dto = None
            w = h = 0
            try:
                with Image.open(p) as im:
                    w, h = im.size
                    o = im.getexif().get(0x0112, 1)
                    if o in (5, 6, 7, 8):
                        w, h = h, w
            except Exception:
                pass
            place, city = photo_geo.assign(lat, lon) if lat is not None else ("", "")
            row = {"name": p.name, "time": dto or "", "lat": lat, "lon": lon, "place": place or "", "city": city or "", "w": w, "h": h}
            cache[key] = row
            changed = True
        r = dict(row)
        r["path"] = "%s/%s" % (album, p.name)
        pk = picks.get(r["path"])
        r["picked"] = bool(pk)
        r["note"] = (pk or {}).get("note", "") if isinstance(pk, dict) else ""
        r["orient"] = "portrait" if r["h"] > r["w"] * 1.05 else "landscape" if r["w"] > r["h"] * 1.05 else "square"
        # his edits: "IMG_1(1).jpg", or a .jpg/.JPEG twin of a .HEIC original (Guatemala has ten: IMG_8255.JPEG is the arch)
        r["edited"] = bool(re.search(r"\(\d\)\.(jpe?g)$", p.name, re.I)) or (p.suffix.lower() in (".jpg", ".jpeg") and p.stem.lower() in heics)
        out.append(r)
    if changed:
        json.dump(cache, open(cf, "w", encoding="utf-8"), ensure_ascii=False)
    return out


def sheet(album, files, out, cols=4, size=300):
    """a contact sheet of the given photos (album-relative names), numbered, with name / place / star under each"""
    from PIL import Image, ImageDraw, ImageFont
    root = _get("/api/pick_root")["root"]
    rows = (len(files) + cols - 1) // cols
    W, H = cols * (size + 10) + 10, rows * (size + 52) + 10
    sh = Image.new("RGB", (W, H), (245, 243, 238))
    dr = ImageDraw.Draw(sh)
    try:
        font = ImageFont.truetype("arial.ttf", 13)
    except Exception:
        font = ImageFont.load_default()
    meta = {r["name"]: r for r in photos(album)}
    for i, name in enumerate(files):
        x, y = 10 + (i % cols) * (size + 10), 10 + (i // cols) * (size + 52)
        try:
            q = urllib.parse.urlencode({"root": root, "path": "%s/%s" % (album, name), "s": 400})
            with urllib.request.urlopen(API + "/thumb?" + q, timeout=120) as r:
                im = Image.open(io.BytesIO(r.read())).convert("RGB")
            im.thumbnail((size, size))
            sh.paste(im, (x + (size - im.width) // 2, y + (size - im.height) // 2))
        except Exception as e:
            dr.text((x + 8, y + 8), "no thumb: %s" % e, fill=(160, 40, 40), font=font)
        m = meta.get(name, {})
        dr.text((x, y + size + 4), "%d. %s%s" % (i + 1, name, "  ★" if m.get("picked") else ""), fill=(28, 40, 33), font=font)
        dr.text((x, y + size + 22), (m.get("place") or m.get("city") or "no GPS")[:44] + "  " + (m.get("time") or "")[5:16],
                fill=(90, 100, 94), font=font)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sh.save(out, quality=86)
    return str(out)


def _key(stem):
    """one photo's key on BOTH sides of the repeat check. His edited copy "IMG_8406(1)" and an archive's collision
    suffix "IMG_8406-1" are the same photo as IMG_8406; the capture time then tells true repeats from trips that reuse
    IMG numbers. (2026-10-25: apply stripped "(1)" but used_files kept it, so an edited copy on a guide could be
    picked again for the itinerary.)"""
    return re.sub(r"(?:\(\d+\)|-\d+)$", "", stem).lower()


def used_files(country):
    """{key: {(page, archive original or None)}} for every photo on the country's pages (live and drafts)"""
    import draft_paths as dp
    pages = sorted((ROOT / country).glob("*.html")) + dp.draft_pages(country)
    pages = [p for p in pages if not p.name.startswith(("_", "."))]   # the pipeline's own _draft-*.html working copy
    out = {}
    for p in pages:
        h, pg = _read(p), p.relative_to(ROOT).as_posix()
        for src in re.findall(r'Images/(?!web/)[^"\'\s>]+\.(?:jpe?g|png|heic)', h, re.I):
            rel = urllib.parse.unquote(src)
            out.setdefault(_key(Path(rel).stem), set()).add((pg, rel if (ROOT / rel).is_file() else None))
        for src in re.findall(r'Images/web/[^"\'\s>]+?-(?:mob-)?\dx\.(?:jpg|webp)', h, re.I):
            rel = urllib.parse.unquote(src)
            base = re.sub(r"-(?:mob-)?\dx$", "", Path(rel).stem)
            orig = next((o for o in (Path("Images") / Path(rel).parent.relative_to("Images/web") / (base + e)
                                     for e in (".jpg", ".jpeg", ".JPG", ".png")) if (ROOT / o).is_file()), None)
            out.setdefault(_key(base), set()).add((pg, orig.as_posix() if orig else None))
    return out


def used(country):
    """photo stems already on any of the country's pages (live and drafts): a photo goes on one page only"""
    return {k: sorted({pg for pg, _ in v}) for k, v in used_files(country).items()}


_DTO = {}
def _dto(path):
    """the photo's capture time (EXIF DateTimeOriginal), or None"""
    path = str(path)
    if path not in _DTO:
        try:
            import photo_geo
            _DTO[path] = photo_geo.exif_geo(Path(path))[2] or None
        except Exception:
            _DTO[path] = None
    return _DTO[path]


def _same_photo(archive_rel, src_dto):
    """is this archive copy the photo shot at src_dto? (unknown on either side: assume it is, the safe answer)"""
    if not archive_rel or not src_dto:
        return True
    d = _dto(ROOT / archive_rel)
    return d is None or d == src_dto


# ------------------------------------------------------------------------------------------------ apply
def _existing_copy(country, stem, src_dto=None):
    """Kevin's edited copy (or an earlier import) of THIS photo already under Images/<Country>/, if any. Same name is not
    enough: IMG numbers repeat across trips, so a copy counts only when its capture time matches the source's (a copy
    with no capture time is used only when no time is known for the source either)."""
    d = ROOT / "Images" / country_dir(country)
    if not d.is_dir():
        return None
    hits = [p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
            and re.fullmatch(re.escape(stem) + r"(-\d+)?", p.stem, re.I) and "web" not in p.relative_to(ROOT / "Images").parts]
    hits = [p for p in hits if (_dto(p) == src_dto if src_dto else _dto(p) is None)]
    return sorted(hits, key=lambda p: (len(p.stem), p.stem))[0] if hits else None


def _img(src_rel, depth, alt, pos, positioned):
    url = "../" * depth + src_rel.replace(" ", "%20")
    fill = "position:absolute;top:0;left:0;" if positioned else "display:block;"
    alt = alt.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    return '<img src="%s" alt="%s" style="%swidth:100%%;height:100%%;object-fit:cover;object-position:%s;">' % (url, alt, fill, pos or "50% 50%")


def apply(page, plan, pipeline=True):
    import draft_paths as dp
    p = ROOT / page
    country = dp.country_of(page)
    s0 = _read(p)
    stamp = p.stat().st_mtime_ns
    found = _slot_matches(s0)
    if plan.get("slots") is not None and len(found) != plan["slots"]:
        raise SystemExit("the page has %d empty slots now, the plan was made for %d: list them again" % (len(found), plan["slots"]))
    taken = used_files(country)
    chosen = {}
    root = _get("/api/pick_root")["root"]
    depth = len(page.split("/")) - 1
    log = []
    for it in plan["photos"]:
        i = int(it["slot"])
        if i in chosen or not 0 <= i < len(found):
            raise SystemExit("slot %s is out of range or chosen twice" % i)
        name = it["photo"].split("/", 1)[1]
        stem = re.sub(r"\(\d+\)$", "", Path(name).stem)
        key = _key(Path(name).stem)
        src_dto = _dto(BACKUP / it["photo"])
        clash = sorted({pg for pg, ar in taken.get(key, ()) if _same_photo(ar, src_dto)})
        if clash and not it.get("reuse"):
            raise SystemExit("%s is already on %s: pick another (or say reuse: true)" % (stem, ", ".join(clash)))
        taken.setdefault(key, set()).add((page, None if not src_dto else "__pending__%s" % src_dto))
        hit = _existing_copy(country, stem, src_dto)
        if hit:
            src = hit.relative_to(ROOT).as_posix()
            log.append("slot %d: %s (the copy already in %s)" % (i, hit.name, hit.parent.relative_to(ROOT).as_posix()))
        else:
            r = _post("/api/import", {"root": root, "path": it["photo"], "country": country_dir(country), "city": it.get("city", ""),
                                      "rot": 0, "flip": None, "angle": 0, "dev": {}, "masks": [], "recipe": {"erases": []}})
            if not r.get("ok"):
                raise SystemExit("import failed for %s: %s" % (it["photo"], r))
            src = r["src"]
            log.append("slot %d: %s -> %s" % (i, it["photo"], src))
        chosen[i] = (src, it.get("alt", ""), it.get("pos", "50% 50%"))
    s = _read(p)
    if p.stat().st_mtime_ns != stamp or s != s0:
        raise SystemExit("the page changed while the photos were archived (an editor save?): nothing written; run apply again")
    for i in sorted(chosen, reverse=True):
        m, old = found[i]
        src, alt, pos = chosen[i]
        if old:                                                          # the old frame is replaced by today's frame
            img = _img(src, depth, alt, pos, True)
            rep = ('<span style="%s">%s</span>' % (PAIR_CELL, img)) if "2/3" in m.group(1) else '<div class="img-landscape">%s</div>' % img
        else:
            po = OPEN_TAG.search(s[max(0, m.start() - 600):m.start()])
            tag, attrs = (po.group(1), po.group(2)) if po else ("div", "")
            positioned = tag == "span" or "img-landscape" in attrs       # editor.html slotAdopt()
            rep = _img(src, depth, alt, pos, positioned)
        s = s[:m.start()] + rep + s[m.end():]
    io.open(p, "w", encoding="utf-8", newline="").write(s)
    log.append("%s: %d slot(s) filled, %d left empty" % (page, len(chosen), len(found) - len(chosen)))
    if pipeline:
        for cmd in ([sys.executable, str(TOOLS / "draft_images.py"), page], [sys.executable, str(TOOLS / "fit_image_tiers.py"), page]):
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7200)
            tail = (r.stdout or r.stderr).strip().splitlines()[-2:]
            log.append("%s: %s" % (Path(cmd[1]).name, "ok" if r.returncode == 0 else "FAILED (%d)" % r.returncode) + (" | " + " / ".join(tail) if tail else ""))
    return log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["slots", "photos", "sheet", "used", "apply"])
    ap.add_argument("arg")
    ap.add_argument("plan", nargs="?")
    ap.add_argument("--place", action="append", default=[], help="sheet: only photos whose place or city contains this")
    ap.add_argument("--picks", action="store_true", help="sheet: only Kevin's starred photos")
    ap.add_argument("--files", help="sheet: these album files, comma-separated, in this order")
    ap.add_argument("--orient", choices=["portrait", "landscape"])
    ap.add_argument("--max", type=int, default=24)
    ap.add_argument("--out")
    ap.add_argument("--no-pipeline", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.cmd == "slots":
        print(json.dumps({"page": a.arg, "slots": slots(a.arg)}, ensure_ascii=False, indent=1))
    elif a.cmd == "photos":
        print(json.dumps(photos(a.arg), ensure_ascii=False, indent=0))
    elif a.cmd == "used":
        print(json.dumps(used(a.arg), ensure_ascii=False, indent=1))
    elif a.cmd == "sheet":
        rows = photos(a.arg)
        if a.files:
            names = [f.strip() for f in a.files.split(",") if f.strip()]
        else:
            rows = [r for r in rows if (not a.picks or r["picked"]) and (not a.orient or r["orient"] == a.orient)
                    and (not a.place or any(x.lower() in (r["place"] + " " + r["city"]).lower() for x in a.place))]
            names = [r["name"] for r in rows][:a.max]
        out = a.out or str(CACHE / "sheets" / ("%s-%s.jpg" % (re.sub(r"[^\w-]+", "_", a.arg), re.sub(r"[^\w-]+", "_", "-".join(a.place) or "all"))))
        print(sheet(a.arg, names, out))
        print("%d photos: %s" % (len(names), ", ".join(names)))
    elif a.cmd == "apply":
        plan = json.load(open(a.plan, encoding="utf-8"))
        print("\n".join(apply(a.arg, plan, not a.no_pipeline)))


if __name__ == "__main__":
    main()
