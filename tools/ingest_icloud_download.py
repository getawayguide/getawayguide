"""Bring an iCloud.com "Unmodified Originals" download into the photo backup.

iCloud for Windows stopped handing over originals (hydration timeouts from
2026-09-28 on), so shared-album backups were stuck at 2048px album copies and
some albums (Portugal, Turkey 2023) wouldn't share at all. Downloading an album
from icloud.com (Personal Library view, select all, Download > Unmodified
Originals, up to 1,000 per zip) skips both problems: the browser gets the real
files, edits included.

A download holds, per item:
    IMG_1234.HEIC    the untouched original
    IMG_E1234.HEIC   Kevin's edit, when there is one
    IMG_1234.MOV     a Live Photo's clip (same name as a photo)
    IMG_5678.MOV     a real video (no photo of that name)
Original + edit + Live clip are ONE item, so totals line up with the phone.

Two modes, chosen by whether the album already has a backup:
  * NEW album: build Backup/<Album>/ from the download. The edit (else the
    original) is the item's file; the unedited original goes to _unedited/,
    Live clips to _live/, videos to videos/.
  * EXISTING album: upgrade every backup item that is still an album copy
    (full=false, no noOriginal): photos matched by capture time + fingerprint,
    picking whichever version (edit or original) the album copy looks like;
    videos matched by comparing frames. Album copies go to _rescue/album-copies/.

    python tools/ingest_icloud_download.py --album "Turkey (2023)" ~/Downloads/iCloud*.zip         # dry run
    python tools/ingest_icloud_download.py --album "Turkey (2023)" ~/Downloads/iCloud*.zip --apply

Pause the backup watcher for --apply (Editor Suite.cmd pause): both write the manifests.
"""
import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_lib as pb        # noqa: E402  (BACKUP, manifests, unique_name, stem_key)
import photo_editor as pe      # noqa: E402  (registers the HEIF opener)
import content_match as cm     # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / ".tmp" / "icloud_import"
PHOTO = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".dng", ".tif", ".tiff", ".gif", ".webp"}
VIDEO = {".mov", ".mp4", ".m4v"}


def unpack(sources, album):
    """Zips and folders -> one staging folder of files."""
    out = STAGE / album
    out.mkdir(parents=True, exist_ok=True)
    for s in sources:
        s = Path(s).expanduser()
        if s.is_dir():
            files = [p for p in s.rglob("*") if p.is_file() and p.stat().st_size > 0]
            for p in files:
                dst = out / p.name
                if not dst.exists():
                    shutil.copy2(p, dst)
        elif s.suffix.lower() == ".zip":
            with zipfile.ZipFile(s) as z:
                # iCloud sometimes zips an empty X.HEIC beside the real photo as X-1.HEIC
                # (Greece 2.0's IMG_4332). Skip the empty one and give the real one its
                # name back, or its Live Photo clip X.MOV reads as a separate video.
                empty = {Path(i.filename).name.lower() for i in z.infolist()
                         if not i.is_dir() and i.file_size == 0}
                for info in z.infolist():
                    if info.is_dir() or info.file_size == 0:
                        continue
                    name = Path(info.filename).name
                    m = re.match(r"^(.*)-1(\.[^.]+)$", name)
                    if m and (m.group(1) + m.group(2)).lower() in empty:
                        name = m.group(1) + m.group(2)
                    dst = out / name
                    if dst.exists() and dst.stat().st_size == info.file_size:
                        continue
                    if dst.exists():                     # same name, different file
                        dst = out / f"{dst.stem}~{info.CRC:08x}{dst.suffix}"
                    with z.open(info) as fi, open(dst, "wb") as fo:
                        shutil.copyfileobj(fi, fo)
    return sorted(p for p in out.iterdir() if p.is_file())


def base_of(p):
    """IMG_E1234.HEIC -> IMG_1234 (the edit belongs to the original)."""
    st = p.stem
    m = re.match(r"^(IMG|DSC|PXL|DJI)_E(\d.*)$", st, re.I)
    return (f"{m.group(1)}_{m.group(2)}" if m else st).lower()


def is_edit(p):
    return bool(re.match(r"^(IMG|DSC|PXL|DJI)_E\d", p.stem, re.I))


def items_of(files):
    """Group the download into items: {base: {"orig","edit","live","video"}}."""
    items = {}
    for p in files:
        b = base_of(p)
        it = items.setdefault(b, {})
        ext = p.suffix.lower()
        if ext in PHOTO:
            slot = "edit" if is_edit(p) else "orig"
            # NOT merged when the name and second match: a JPG export beside its HEIC
            # (Turkey 2.0's IMG_1720/2306/2442) is its own item in the album, and the
            # phone count includes it. Only Apple's IMG_E edits and Live clips group.
            n = 2
            while slot in it:
                # two DIFFERENT photos with one name (Cuba: IMG_0141.HEIC from the
                # phone and IMG_0141.JPG from somewhere else). Keeping one silently
                # dropped the other and the album read one item short.
                b = f"{base_of(p)}#{n}"; it = items.setdefault(b, {}); n += 1
            it[slot] = p
        elif ext in VIDEO:
            it.setdefault("movs", []).append(p)
    more = {}
    for b, it in items.items():
        movs = it.pop("movs", [])
        if "orig" in it or "edit" in it:
            it["live"] = movs                           # Live Photo clip(s)
        elif movs:
            it["video"] = max(movs, key=lambda m: (not is_edit(m), m.stat().st_size))
            it["video_edit"] = next((m for m in movs if is_edit(m)), None)
            # every other non-edit clip is a video of its own: Armenia's IMG_0576.MOV and
            # IMG_0576.mp4 are two album items, and keeping only the bigger lost one
            extra = [m for m in movs if m is not it["video"] and not is_edit(m)]
            for n, m in enumerate(extra, 2):
                more[f"{b}#v{n}"] = {"video": m, "video_edit": None}
    items.update(more)
    return items


def size_of(p):
    try:
        with Image.open(p) as im:
            return ImageOps.exif_transpose(im).size
    except Exception:
        return (None, None)


# ---------------------------------------------------------------- NEW album

def build_new(album, items, apply, d=None):
    d = d or pb.BACKUP / album
    photos = {b: it for b, it in items.items() if "orig" in it or "edit" in it}
    videos = {b: it for b, it in items.items() if "video" in it}
    lives = sum(len(it.get("live", [])) for it in photos.values())
    edits = sum(1 for it in photos.values() if "edit" in it)
    print(f"  NEW backup for {album}: {len(photos)} photos ({edits} with edits, {lives} Live Photo clips) "
          f"+ {len(videos)} videos = {len(photos) + len(videos)} items")
    if not apply:
        return
    (d / "videos").mkdir(parents=True, exist_ok=True)
    files, entries, vman = {}, {}, {}
    for b, it in sorted(photos.items()):
        main = it.get("edit") or it["orig"]
        shutil.copy2(main, d / main.name)
        w, h = size_of(d / main.name)
        files[main.name] = {"from": b, "full": True, "orig": f"icloud.com download: {main.name}",
                            "size": (d / main.name).stat().st_size, "w": w, "h": h, "edited": "edit" in it}
        entries[b] = main.name
        if "edit" in it and "orig" in it:
            (d / "_unedited").mkdir(exist_ok=True)
            shutil.copy2(it["orig"], d / "_unedited" / it["orig"].name)
        for m in it.get("live", []):
            (d / "_live").mkdir(exist_ok=True)
            shutil.copy2(m, d / "_live" / m.name)
    for b, it in sorted(videos.items()):
        v = it.get("video_edit") or it["video"]
        shutil.copy2(v, d / "videos" / v.name)
        vman[b] = {"file": v.name, "full": True, "size": (d / "videos" / v.name).stat().st_size}
        if it.get("video_edit") and it["video"] != it["video_edit"]:
            (d / "_unedited").mkdir(exist_ok=True)
            shutil.copy2(it["video"], d / "_unedited" / it["video"].name)
    pb.write_json(d / "_manifest.json", {"files": files, "entries": entries})
    pb.write_json(d / "videos" / "_manifest.json", vman)
    print(f"  wrote {d.name}: {len(files)} photos, {len(vman)} videos")


# ---------------------------------------------------------------- EXISTING album

def upgrade_existing(album, items, apply):
    d = pb.BACKUP / album
    mpath = d / "_manifest.json"
    man = pb.load_manifest(mpath)
    files, entries = man["files"], man["entries"]
    # download photos by capture time
    by_time = {}
    for b, it in items.items():
        for k in ("edit", "orig"):
            if k in it:
                for key in pe.capture_key(it[k]):
                    by_time.setdefault(key, []).append((b, k, it[k]))
    used = set()
    sigcache = {}
    claimed = {}                               # (item, kind) -> its file in the backup
    # originals a previous run already brought in count as claimed, so a re-run
    # can fold a leftover duplicate into them
    by_name = {it[k].name: (b, k) for b, it in items.items() for k in ("edit", "orig") if k in it}
    for f_, r_ in files.items():
        src = (r_.get("orig") or "").removeprefix("icloud.com download: ")
        if r_.get("full") and src in by_name:
            claimed[by_name[src]] = f_; used.add(by_name[src])
            bp = items[by_name[src][0]][by_name[src][1]]
            if bp not in sigcache:
                try:
                    sigcache[bp] = cm.photo_sig(bp)["h"]
                except Exception:
                    sigcache[bp] = None
    unreadable = set()
    cropped = []
    matched = set()
    # a video's poster frame (.jpgthumb_00001.jpg) is not a photo: matching Greece 2.0's 49 posters
    # crop-aware against every photo in the download took hours and could never succeed
    todo = [f for f, r in files.items() if not r.get("full") and not r.get("noOriginal")
            and ".jpgthumb" not in f and (d / f).exists()]
    print(f"  photos still album copies: {len(todo)}")
    done = 0
    every = [(b, k, it[k]) for b, it in items.items() for k in ("edit", "orig") if k in it]
    albsig = {}

    def album_sig(x):
        if x not in albsig:
            albsig[x] = cm.photo_sig(d / x)["h"]
        return albsig[x]

    def mutual_best(f, p, dist):
        """Several versions of ONE shot (Finland: the original, an edited copy and an
        Instagram story with a sticker) are all near each other, so the margin rule
        leaves every one of them. Accept f -> p when p is closer to f than to any
        other album copy still waiting: each version then pairs with its own twin."""
        h = sigcache[p]
        return all(cm.ham(album_sig(o), h) > dist for o in todo if o != f and o not in matched)

    queue, deferred = list(todo), []
    while queue:
        for f in queue:
            keys = pe.capture_key(d / f)
            cands = [c for k in keys for c in by_time.get(k, []) if (c[0], c[1]) not in used]
            if not cands and not keys:
                # an album copy with NO capture data (Greece 2.0: all 53 lost their
                # EXIF) can't be matched by time: compare its content with every
                # photo in the download instead
                cands = [c for c in every if (c[0], c[1]) not in used]
            if not cands and keys:
                # every original at that second is taken: this is a second album copy
                # of a photo already upgraded (Cuba: two rounds' copies of IMG_0141).
                # Point its entries at that original instead of keeping both.
                taken = [claimed[(c[0], c[1])] for k_ in keys for c in by_time.get(k_, [])
                         if (c[0], c[1]) in claimed and sigcache.get(c[2]) is not None
                         and cm.ham(cm.photo_sig(d / f)["h"], sigcache[c[2]]) <= cm.PHOTO_MAX]
                if taken:
                    done += 1; matched.add(f)
                    print(f"    {f[:24]}: a duplicate of a photo already upgraded, folded in")
                    if apply and taken[0]:
                        for e, t in list(entries.items()):
                            if t == f:
                                entries[e] = taken[0]
                        files.pop(f)
                        resc = d / "_rescue" / "album-copies"; resc.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(d / f), str(resc / f))
                    continue
            if not cands:
                continue
            mine = cm.photo_sig(d / f)
            for _, _, p in cands:
                if p not in sigcache:
                    try:
                        sigcache[p] = cm.photo_sig(p)["h"]
                    except Exception:
                        sigcache[p] = None                 # a file PIL can't open: report, don't stop
                        unreadable.add(p.name)
            scored = sorted((cm.ham(mine["h"], sigcache[p]), b, k, p) for b, k, p in cands if sigcache[p] is not None)
            if not scored:
                continue
            dist, b, k, p = scored[0]
            if dist > cm.PHOTO_MAX:
                if not keys:
                    cropped.append(f)                      # try the crop-aware match below
                else:
                    print(f"    {f[:24]}: closest is {p.name} at distance {dist}, left")
                continue
            if (len(scored) > 1 and scored[1][0] - dist < cm.PHOTO_MARGIN and scored[1][1] != b
                    and not mutual_best(f, p, dist)):
                deferred.append(f)                     # retried once the others have claimed theirs
                continue
            used.add((b, k)); done += 1; matched.add(f); claimed[(b, k)] = None
            print(f"    {f[:24]} -> {p.name} (distance {dist})")
            if not apply:
                continue
            newname = pb.unique_name(p.name, set(files) | set(entries.values()))
            claimed[(b, k)] = newname
            shutil.copy2(p, d / newname)
            w, h = size_of(d / newname)
            rec = dict(files[f]); rec.update({"full": True, "orig": f"icloud.com download: {p.name}",
                                              "size": (d / newname).stat().st_size, "w": w, "h": h})
            rec.pop("pendingOrig", None)
            files.pop(f); files[newname] = rec
            for e, t in list(entries.items()):
                if t == f:
                    entries[e] = newname
            resc = d / "_rescue" / "album-copies"; resc.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d / f), str(resc / f))
            other = items[b].get("orig" if k == "edit" else "edit")
            if other:
                (d / "_unedited").mkdir(exist_ok=True)
                shutil.copy2(other, d / "_unedited" / other.name)
        if len(deferred) == len(queue):
            for f in deferred:
                print(f"    {f[:24]}: two different photos match almost equally, left")
            break
        queue, deferred = deferred, []
    # CROPPED / EDITED exports (Lightroom and phone apps strip the date and crop):
    # feature-match each one against every photo in the download, crop-aware, and
    # accept only a close reproduction from a bigger original (Kevin's call,
    # 2026-10-01: "those might be edited photos in light room or another mobile app")
    if cropped:
        import rebuild_from_original as rb
        from concurrent.futures import ProcessPoolExecutor
        pool = [(b, k, it[k]) for b, it in items.items() for k in ("edit", "orig") if k in it
                and (b, k) not in used and it[k].name not in unreadable]
        # 2 workers: each decodes a 48 MP HEIC (~150 MB) and 6 ran out of memory
        with ProcessPoolExecutor(2) as ex:
            list(ex.map(rb.original_features, [p for _, _, p in pool], chunksize=4))
        for f in cropped:
            copy, _ = rb.load_rgb(d / f)
            cf = rb.features(copy)
            hits = []
            for b, k, p in pool:
                if (b, k) in used:
                    continue
                pts, desc, shape = rb.original_features(p)
                if max(shape) <= max(copy.shape[:2]) * 1.0:
                    continue
                M, n = rb.align(cf, (pts, desc))
                if M is not None:
                    hits.append((n, b, k, p, M))
            hits.sort(key=lambda h: -h[0])
            best = None
            for n, b, k, p, M in hits[:3]:
                r = rb._render_one(d / f, copy, p, M, n)
                if r["scale"] >= 1.0 and (best is None or r["diff"] < best[0]):
                    best = (r["diff"], b, k, p, r["scale"])
            if not best or best[0] > 12:
                continue
                
            diff_, b, k, p, sc = best
            used.add((b, k)); done += 1; matched.add(f)
            print(f"    {f[:24]} -> {p.name} (crop-aware, diff {diff_:.1f}, x{sc:.2f})")
            if not apply:
                continue
            newname = pb.unique_name(p.name, set(files) | set(entries.values()))
            shutil.copy2(p, d / newname)
            w, h = size_of(d / newname)
            rec = dict(files[f]); rec.update({"full": True, "orig": f"icloud.com download: {p.name}",
                                              "size": (d / newname).stat().st_size, "w": w, "h": h,
                                              "matched": f"crop-aware, diff {diff_:.1f}"})
            files.pop(f); files[newname] = rec
            for e, t in list(entries.items()):
                if t == f:
                    entries[e] = newname
            resc = d / "_rescue" / "album-copies"; resc.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d / f), str(resc / f))

    # What's left with NO capture date isn't one of Kevin's camera photos: an
    # Instagram/WhatsApp import or a screenshot. The album copy is the best version
    # (Kevin, 2026-10-01: "assume those are IG/whatsapp imports going forward").
    # Dated leftovers are real photos missing from the download: listed, not marked.
    leftover = [f for f, r in files.items() if not r.get("full") and not r.get("noOriginal")
                and (d / f).exists() and ".jpgthumb" not in f and f not in matched]
    undated = [f for f in leftover if not pe.capture_key(d / f)]
    for f in undated:
        files[f]["noOriginal"] = "undated: an Instagram/WhatsApp import or screenshot (assumed)"
    # Dated leftovers: the icloud.com download IS the whole album, so a dated album
    # copy it has no match for is a duplicate or a stray, not a missing original
    # (Kevin, 2026-10-01: "it has all the originals right? ... just mark it complete").
    # Listed so a short download still shows, then marked done.
    for f in leftover:
        if f not in undated:
            print(f"    no match in the download, marked done: {f[:24]} {size_of(d / f)}")
            files[f]["noOriginal"] = "no match in the icloud.com download of the full album (2026-10-01)"
    if apply:
        pb.write_json(mpath, {"files": files, "entries": entries})
    print(f"  photos upgraded: {done}; undated imports marked done: {len(undated)}")
    if unreadable:
        print(f"  download files the image library can't read (kept, not matched): {sorted(unreadable)}")

    # ADD what the backup never got: a downloaded photo with no backup file taken
    # in the same second (Greece 2.0's 2nd Try: ~50 photos whose originals timed
    # out before the album-copy fallback existed).
    have = set()
    for f in files:
        if (d / f).exists():
            have.update(pe.capture_key(d / f)[:1])
    added = 0
    for b, it in sorted(items.items()):
        if ("orig" not in it and "edit" not in it) or any((b, k) in used for k in ("orig", "edit")):
            continue
        main = it.get("edit") or it["orig"]
        keys = pe.capture_key(main)
        if not keys or keys[0] in have:
            continue
        added += 1
        if not apply:
            continue
        newname = pb.unique_name(main.name, set(files) | set(entries.values()))
        shutil.copy2(main, d / newname)
        w, h = size_of(d / newname)
        files[newname] = {"from": b, "full": True, "orig": f"icloud.com download: {main.name}",
                          "size": (d / newname).stat().st_size, "w": w, "h": h, "edited": "edit" in it}
        entries[f"icloud:{b}"] = newname
        have.update(keys[:1])
        if "edit" in it and "orig" in it:
            (d / "_unedited").mkdir(exist_ok=True)
            shutil.copy2(it["orig"], d / "_unedited" / it["orig"].name)
        for m in it.get("live", []):
            (d / "_live").mkdir(exist_ok=True)
            shutil.copy2(m, d / "_live" / m.name)
    if apply:
        pb.write_json(mpath, {"files": files, "entries": entries})
    print(f"  photos the backup never had, added: {added}")

    vpath = d / "videos" / "_manifest.json"
    if not vpath.exists():
        return
    vman = json.loads(vpath.read_text(encoding="utf-8"))
    vdl = [(it.get("video_edit") or it["video"], it) for it in items.values() if "video" in it]
    vsig = {}
    pending = sorted({r["file"] for r in vman.values() if r.get("file") and not r.get("full")
                      and (d / "videos" / r["file"]).exists()})
    print(f"  videos still album copies: {len(pending)}")
    vdone = 0
    for f in pending:
        mine = cm.video_sig(d / "videos" / f)
        if not mine:
            continue
        best = None
        for v, it in vdl:
            if v in used:
                continue
            if v not in vsig:
                vsig[v] = cm.video_sig(v)
            s = vsig[v]
            if not s or abs(s["dur"] - mine["dur"]) > cm.VIDEO_DUR_TOL:
                continue
            dist = cm.vdist(mine, s)
            if best is None or dist < best[0]:
                best = (dist, v)
        if not best or best[0] > cm.VIDEO_MAX:
            continue
        dist, v = best
        used.add(v); vdone += 1
        print(f"    {f[:24]} -> {v.name} (frames distance {dist:.0f})")
        if not apply:
            continue
        newname = pb.unique_name(v.name, {r.get("file") for r in vman.values()})
        shutil.copy2(v, d / "videos" / newname)
        for e, r in vman.items():
            if r.get("file") == f:
                vman[e] = {"file": newname, "full": True, "size": (d / "videos" / newname).stat().st_size,
                           "matched": f"icloud.com download, frames distance {dist:.0f}"}
        resc = d / "_rescue" / "album-copies"; resc.mkdir(parents=True, exist_ok=True)
        shutil.move(str(d / "videos" / f), str(resc / f))
    if apply:
        pb.write_json(vpath, vman)
    print(f"  videos upgraded: {vdone}")

    # MISPAIRED videos: one backup file standing in for two or more album videos is
    # the old duration-only matcher's wrong-clip pairing (205 of them, 2026-09-28).
    # Each album video's own copy was saved to _rescue/videos/; match each copy to
    # the download by frames and give it its own original.
    by_file = {}
    for e, r in vman.items():
        if r.get("file"):
            by_file.setdefault(r["file"], {}).setdefault(pb.stem_key(e), []).append(e)
    fixed = 0
    for f, stems in by_file.items():
        if len(stems) < 2:
            continue
        for stem, ents in stems.items():
            copy = next((c for e in ents for c in [d / "_rescue" / "videos" / e] + [
                r / e for r in pb.SHARED.glob(album + "*") if r.is_dir()] if c.exists()), None)
            if not copy:
                continue
            mine = cm.video_sig(copy)
            if not mine:
                continue
            best = None
            for v, it in vdl:
                if v not in vsig:
                    vsig[v] = cm.video_sig(v)
                sv = vsig[v]
                if sv and abs(sv["dur"] - mine["dur"]) <= cm.VIDEO_DUR_TOL:
                    dist = cm.vdist(mine, sv)
                    if best is None or dist < best[0]:
                        best = (dist, v)
            if not best or best[0] > cm.VIDEO_MAX:
                continue
            dist, v = best
            if (d / "videos" / v.name).exists() and any(r.get("file") == v.name for e2, r in vman.items()
                                                        if pb.stem_key(e2) == stem):
                continue                               # this stem already has it
            fixed += 1; used.add(v)
            print(f"    mispaired: {ents[0][:24]} was {f}, its own clip is {v.name} (frames distance {dist:.0f})")
            if not apply:
                continue
            newname = v.name if not (d / "videos" / v.name).exists() else pb.unique_name(
                v.name, {r.get("file") for r in vman.values()})
            if not (d / "videos" / newname).exists():
                shutil.copy2(v, d / "videos" / newname)
            for e in ents:
                vman[e] = {"file": newname, "full": True, "size": (d / "videos" / newname).stat().st_size,
                           "matched": f"icloud.com download, frames distance {dist:.0f} (was mispaired with {f})"}
    if apply:
        pb.write_json(vpath, vman)
        # a wrong clip nothing points at any more (another trip's video) -> set aside
        referenced = {r.get("file") for r in vman.values()}
        for vf in (d / "videos").iterdir():
            if vf.is_file() and vf.suffix.lower() in VIDEO and vf.name not in referenced:
                (d / "_rescue" / "mispaired").mkdir(parents=True, exist_ok=True)
                shutil.move(str(vf), str(d / "_rescue" / "mispaired" / vf.name))
    print(f"  mispaired videos fixed: {fixed}")

    # ADD videos the backup never got: a downloaded clip that matches no backup
    # video (album copy or original) by frames
    bsig = {}
    for r in vman.values():
        fp = d / "videos" / (r.get("file") or "")
        if r.get("file") and fp.exists() and r["file"] not in bsig:
            bsig[r["file"]] = cm.video_sig(fp)
    vadded = 0
    for v, it in vdl:
        if v in used:
            continue
        s = vsig.get(v) or cm.video_sig(v)
        if not s:
            continue
        if any(bs and abs(bs["dur"] - s["dur"]) <= cm.VIDEO_DUR_TOL and cm.vdist(s, bs) <= cm.VIDEO_MAX
               for bs in bsig.values()):
            continue                                   # already backed up
        vadded += 1
        if not apply:
            continue
        newname = pb.unique_name(v.name, {r.get("file") for r in vman.values()})
        shutil.copy2(v, d / "videos" / newname)
        vman[f"icloud:{base_of(v)}"] = {"file": newname, "full": True, "size": (d / "videos" / newname).stat().st_size}
        bsig[newname] = s
    if apply:
        pb.write_json(vpath, vman)
    print(f"  videos the backup never had, added: {vadded}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sources", nargs="+", help="zip files and/or folders from icloud.com")
    ap.add_argument("--album", required=True, help='backup album name, e.g. "Turkey (2023)"')
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ignore-watcher", action="store_true",
                    help="skip the watcher check: for a watcher already killed but stuck exiting "
                         "on an iCloud hydration (one thread left, can no longer write)")
    ap.add_argument("--require-count", action="store_true",
                    help="with --replace: refuse unless the item count equals expected.json "
                         "(rebuilding an album from its own files, where a mismatch means a bug)")
    ap.add_argument("--replace", action="store_true",
                    help="build the album from the download alone and replace the old backup folder")
    a = ap.parse_args()
    resume = False
    if a.apply and not a.ignore_watcher:
        # the watcher writes the same manifests. Pause it here and confirm it is gone,
        # then restart it at the end: a separate `photo_suite pause` kept losing a race
        # with the watcher coming back up, and the import refused (2026-10-02)
        import time
        import photo_suite
        for _ in range(10):
            try:                      # the CIM process query can time out on a busy box
                if not photo_suite.watcher_pids():
                    break
                resume = True
                photo_suite.pause()
            except Exception as e:
                print(f"  (watcher check failed, retrying: {type(e).__name__})")
            time.sleep(2)
        else:
            sys.exit("The backup watcher won't stop; nothing was changed.")
    try:
        run(a)
    finally:
        if resume:
            import photo_suite
            photo_suite.resume()
            print("Backup watcher resumed.")


def run(a):
    files = unpack(a.sources, a.album)
    items = items_of(files)
    print(f"{a.album}: {len(files)} files in the download -> {len(items)} items")
    if a.replace:
        replace_album(a.album, items, a.apply, a.require_count)
    elif (pb.BACKUP / a.album / "_manifest.json").exists():
        upgrade_existing(a.album, items, a.apply)
    else:
        build_new(a.album, items, a.apply)
    if a.apply:
        warm_thumbs(a.album)


def warm_thumbs(album):
    """Pre-build the Library grid's 400px thumbs for the album's photos. The retired
    watcher did this while downloading; without it, every replaced album made the grid
    decode 48MP HEICs on demand and thumbnails crawled (Kevin, 2026-10-04)."""
    from concurrent.futures import ThreadPoolExecutor
    d = pb.BACKUP / album
    todo = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in pe.IMG_EXTS
            and not p.name.startswith("_manifest") and not pe.is_stray_thumb(p.name)
            and not pe.bthumb_path(p, 400).exists()]
    if not todo:
        return
    def one(p):
        try:
            pe._build_bthumb(p, 400)
        except Exception:
            pass
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, todo))
    print(f"  library thumbs built: {len(todo)}")


def folder_mb(d):
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e6 if d.exists() else 0


def replace_album(album, items, apply, require_count=False):
    """THE process since 2026-10-02 (Kevin: "just use the photos / videos from the manual
    download ... this should be the general process going forward"): the album is rebuilt from
    the download alone and the old folder, with its shared-album copies and rebuild rounds, is
    deleted. The download is the album: if its item count differs from expected.json, the
    count is updated to the download's."""
    d = pb.BACKUP / album
    ep = pb.BACKUP / "_meta" / "expected.json"
    want = (json.loads(ep.read_text(encoding="utf-8")) if ep.exists() else {}).get(album)
    have = sum(1 for it in items.values() if "orig" in it or "edit" in it or "video" in it)
    print(f"  download {have} items, phone count {want}")
    if want != have and require_count:
        print("  NOT replaced: --require-count and the counts differ. Nothing changed.")
        return
    if want != have:
        # the download IS the album (Kevin, 2026-10-02: "just use what was downloaded ...
        # It is everything in the folders exactly"), so it wins and the count follows it
        print(f"  counts differ: the download is the album; count set to {have}")
        if apply:
            exp = json.loads(ep.read_text(encoding="utf-8")) if ep.exists() else {}
            exp[album] = have
            pb.write_json(ep, exp)
    new = pb.BACKUP / (album + " .replacing")
    if new.exists():
        shutil.rmtree(new)
    build_new(album, items, apply, new)
    if not apply:
        return
    old_mb = folder_mb(d)
    if d.exists():
        shutil.rmtree(d)
    new.rename(d)
    print(f"  replaced {album}: old folder {old_mb:.0f} MB deleted, new folder {folder_mb(d):.0f} MB")


if __name__ == "__main__":
    main()
