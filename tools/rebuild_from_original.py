"""Rebuild a site photo from its full-resolution original, keeping the crop and
the look of the version on the site.

Many site sources under Images/ are 2048px shared-album copies (or smaller crops
and "Compressed 800" exports of them), made before the originals were reachable.
The originals are in the iCloud library at 12-48 MP. This finds each photo's
original, recovers the exact crop (and any straightening) by feature matching,
cuts that region out of the original at full resolution, and fits the colour of
the site version onto it so an edit (exposure, warmth, a filter) carries over.

A result is only accepted when, scaled back down, it matches the current site
photo closely. Anything else is left alone and listed for a look by eye.

    python tools/rebuild_from_original.py --report .tmp/site_lowres_report.json --country "El Salvador"
    python tools/rebuild_from_original.py ... --apply

Dry run by default: writes a side-by-side review sheet to .tmp/previews/ and
changes nothing. --apply replaces each accepted source (same name, same folder,
so every page reference still resolves), keeping the old file under
.tmp/replaced_sources/. Rebuild the web tiers afterwards:
    python tools/gen_image_tiers.py --country "El Salvador" --force

ICC: the originals are Display P3. The site copy is converted INTO the
original's profile before fitting, and the result is saved with that profile.
"""
import os
import argparse
import io
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_editor as pe  # noqa: E402  (registers the HEIF opener, originals index)

ROOT = Path(__file__).resolve().parent.parent
PREVIEWS = ROOT / ".tmp" / "previews"
KEEP = ROOT / ".tmp" / "replaced_sources"
SRGB = ImageCms.createProfile("sRGB")

MIN_INLIERS = 40        # feature matches that agree on one crop
MAX_DIFF = 9.0          # mean abs difference (0-255) against the site copy, after colour fit
MATCH_EDGE = 1400       # long edge the feature matching runs at


# ---------------------------------------------------------------- loading

def load_rgb(path, profile_out=None):
    """(RGB array, icc bytes). With profile_out, converted into that profile."""
    im = ImageOps.exif_transpose(Image.open(path))
    icc = im.info.get("icc_profile")
    im = im.convert("RGB")
    if profile_out is not None:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc)) if icc else SRGB
        dst = ImageCms.ImageCmsProfile(io.BytesIO(profile_out))
        im = ImageCms.profileToProfile(im, src, dst, outputMode="RGB")
    return np.asarray(im), icc


def small(arr, edge):
    h, w = arr.shape[:2]
    s = edge / max(h, w)
    if s >= 1:
        return arr, 1.0
    return cv2.resize(arr, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA), s


# ---------------------------------------------------------------- matching

FEATS = ROOT / ".tmp" / "rebuild_features"


def features(arr):
    """(points in the array's own pixel coords, descriptors)."""
    sm, s = small(arr, MATCH_EDGE)
    kp, d = cv2.SIFT_create(nfeatures=4000).detectAndCompute(cv2.cvtColor(sm, cv2.COLOR_RGB2GRAY), None)
    pts = np.float32([k.pt for k in kp]) / s if kp else np.zeros((0, 2), np.float32)
    return pts, d


def original_features(p):
    """Features of a library original, cached on disk: decoding a 48 MP HEIC
    is the slow part, and a photo with no capture time is matched against
    every original of the trip."""
    import hashlib
    st = p.stat()
    key = FEATS / (hashlib.md5(f"{p}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest() + ".npz")
    if key.exists():
        try:
            with np.load(key) as z:            # closed on exit: Windows won't delete an open file
                return z["pts"], (z["desc"] if z["desc"].size else None), tuple(z["shape"])
        except Exception:          # half-written by a worker that was killed (out of memory)
            try:
                key.unlink(missing_ok=True)
            except OSError:
                pass                           # another worker has it open; recompute anyway
    try:
        arr, _ = load_rgb(p)
    except Exception:
        return np.zeros((0, 2), np.float32), None, (0, 0)
    pts, d = features(arr)
    FEATS.mkdir(parents=True, exist_ok=True)
    # write then rename, so a kill never leaves a torn file; per-process name, and a
    # rename that loses to another worker (Windows locks a file being read) is fine
    tmp = key.with_name(f"{key.stem}.{os.getpid()}.part.npz")
    np.savez(tmp, pts=pts, desc=d if d is not None else np.zeros((0, 128), np.float32),
             shape=np.array(arr.shape[:2]))
    try:
        os.replace(tmp, key)
    except OSError:
        tmp.unlink(missing_ok=True)
    return pts, d, arr.shape[:2]


def align(site_f, orig_f):
    """2x3 similarity transform mapping SITE pixel coords -> ORIGINAL pixel
    coords, and the inlier count; (None, n) when they don't match."""
    (ps, ds), (po, do) = site_f, orig_f
    if ds is None or do is None or len(ps) < 10 or len(po) < 10:
        return None, 0
    m = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 5}, {"checks": 64}).knnMatch(ds, do, k=2)
    good = [a for a, b in (p for p in m if len(p) == 2) if a.distance < 0.72 * b.distance]
    if len(good) < MIN_INLIERS:
        return None, len(good)
    src = np.float32([ps[g.queryIdx] for g in good])
    dst = np.float32([po[g.trainIdx] for g in good])
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=6,
                                         maxIters=4000, confidence=0.999)
    n = int(inl.sum()) if inl is not None else 0
    return (M if n >= MIN_INLIERS else None), n


def render(orig, M, site_shape):
    """The site photo's region of the original, at the original's resolution."""
    scale = float(np.hypot(M[0, 0], M[1, 0]))          # original px per site px
    h, w = site_shape[:2]
    out_w, out_h = round(w * scale), round(h * scale)
    # output pixel (x, y) = site (x/scale, y/scale) -> original via M
    A = M.copy().astype(np.float64)
    A[:, :2] /= scale
    return cv2.warpAffine(orig, A, (out_w, out_h), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LANCZOS4,
                          borderMode=cv2.BORDER_REFLECT), scale


# ---------------------------------------------------------------- colour

def fit_curves(src, dst):
    """Per-channel tone curves taking src to dst (both HxWx3 uint8, same size),
    plus a 3x3 channel mix for saturation / white-balance edits."""
    s = src.reshape(-1, 3).astype(np.float64)
    d = dst.reshape(-1, 3).astype(np.float64)
    # channel mix first (least squares with bias), then per-channel curves on the rest
    X = np.hstack([s, np.ones((len(s), 1))])
    mix, *_ = np.linalg.lstsq(X, d, rcond=None)
    mixed = np.clip(X @ mix, 0, 255)
    luts = []
    for c in range(3):
        v = np.clip(mixed[:, c].round().astype(int), 0, 255)
        sums = np.bincount(v, weights=d[:, c], minlength=256)
        cnt = np.bincount(v, minlength=256)
        xs = np.nonzero(cnt > 20)[0]
        if len(xs) < 8:
            luts.append(np.arange(256, dtype=np.float64))
            continue
        lut = np.interp(np.arange(256), xs, sums[xs] / cnt[xs])
        lut = np.convolve(np.pad(lut, 4, mode="edge"), np.ones(9) / 9, mode="valid")
        luts.append(np.maximum.accumulate(lut))         # tone curves never invert
    return mix, np.stack(luts)


def apply_curves(arr, mix, luts):
    h, w = arr.shape[:2]
    out = np.empty_like(arr)
    for y in range(0, h, 512):                           # strips: a 48 MP float copy is 1 GB
        s = arr[y:y + 512].reshape(-1, 3).astype(np.float64)
        m = np.clip(np.hstack([s, np.ones((len(s), 1))]) @ mix, 0, 255)
        for c in range(3):
            m[:, c] = np.interp(m[:, c], np.arange(256), luts[c])
        out[y:y + 512] = np.clip(m, 0, 255).round().astype(np.uint8).reshape(-1, w, 3)
    return out


def diff(a, b):
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


# ---------------------------------------------------------------- candidates

def candidates(site_path, idx, lib, window):
    """Same-second originals first; without a capture time, every original in
    the trip window (matching decides)."""
    out = []
    for k in pe.capture_key(site_path):
        out += [lib / c["n"] for c in idx["byTime"].get(k) or []]
        if out:
            return out, "same second"
    lo, hi = window
    for k, cs in idx["byTime"].items():
        if lo <= k <= hi:
            out += [lib / c["n"] for c in cs]
    return out, "trip window"


def rebuild(site_path, cands):
    site_raw, site_icc = load_rgb(site_path)
    site_f = features(site_raw)
    matches = []
    for p in cands:
        if not p.exists() or p.suffix.lower() in (".mov", ".mp4"):
            continue
        pts, d, shape = original_features(p)
        if max(shape) <= max(site_raw.shape[:2]) * 1.2:
            continue                                     # not actually bigger
        M, n = align(site_f, (pts, d))
        if M is not None:
            matches.append((n, p, M))
    if not matches:
        return None
    # Several versions of one shot can match (the iPhone original, a Lightroom
    # export of it, an edited copy). Rebuild from the best few and keep the one
    # that reproduces the SITE photo most closely - its crop and its colour -
    # with resolution breaking a near-tie.
    tried = []
    for n, p, M in sorted(matches, key=lambda m: -m[0])[:6]:
        r = _render_one(site_path, site_raw, p, M, n)
        r["_small"] = small(r["image"], 400)[0]
        r["image"] = None if tried else r["image"]      # keep one full-size array at a time
        tried.append((r, p, M, n))
    # The closest match wins: a retouch (a tarp removed from Mt Ararat's rooftops)
    # shows up as a slightly higher difference on the un-retouched export, so a
    # coarse tie would hand the win to the wrong version. Only near-exact ties
    # (within 0.3) go to more pixels, then the bigger, less compressed file.
    # a candidate whose MATCHING REGION is smaller than the site photo would be a
    # downgrade (a 1080p video frame behind a crop that was already 1070px wide)
    bigger = [t for t in tried if t[0]["scale"] >= 1.0]
    if not bigger:
        return None
    tried = bigger
    lo = min(t[0]["diff"] for t in tried)
    near = [t for t in tried if t[0]["diff"] <= lo + 0.3]
    r, p, M, n = max(near, key=lambda t: (round(t[0]["scale"], 2), t[1].stat().st_size))
    if r["image"] is None:                              # re-render the winner at full size
        r = _render_one(site_path, site_raw, p, M, n)
    r["versions"] = len(matches)
    return r


def _render_one(site_path, site_raw, p, M, n):
    orig, icc = load_rgb(p)
    out, scale = render(orig, M, site_raw.shape)
    del orig
    # the site copy, in the original's colour space, as the colour target
    site, _ = load_rgb(site_path, profile_out=icc) if icc else (site_raw, None)
    down = cv2.resize(out, (site.shape[1], site.shape[0]), interpolation=cv2.INTER_AREA)
    before = diff(down, site)
    mix, luts = fit_curves(down, site)
    after = diff(apply_curves(down, mix, luts), site)
    graded = after < before - 1.0
    if graded:
        out = apply_curves(out, mix, luts)
        down = cv2.resize(out, (site.shape[1], site.shape[0]), interpolation=cv2.INTER_AREA)
    angle = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    return {"original": p, "inliers": n, "scale": scale, "angle": angle,
            "diff_before": before, "diff": diff(down, site), "graded": graded,
            "image": out, "icc": icc, "site": site_raw, "down": down,
            "ok": diff(down, site) <= MAX_DIFF}


def save(path, arr, icc, original):
    """JPEG at archive quality with the original's profile and capture EXIF
    (orientation reset: the pixels are already upright)."""
    im = Image.fromarray(arr)
    exif = Image.open(original).getexif()
    exif[0x0112] = 1
    if path.suffix.lower() == ".png":                    # keep the format the name promises
        im.save(path, "PNG", optimize=True, icc_profile=icc)
    else:
        im.save(path, "JPEG", quality=95, subsampling=0, icc_profile=icc, exif=exif.tobytes())


def sheet(results, out):
    tiles = []
    for r in results:
        a = Image.fromarray(small(r["site"], 360)[0])
        b = Image.fromarray(small(r["image"], 360)[0])
        t = Image.new("RGB", (740, 400), "white")
        t.paste(a, (0, 30)); t.paste(b, (380, 30))
        from PIL import ImageDraw
        d = ImageDraw.Draw(t)
        d.text((4, 4), f"{r['name'][:44]}  ->  {r['original'].name}", fill=(0, 0, 0))
        d.text((4, 16), f"{'OK' if r['ok'] else 'CHECK'}  diff {r['diff']:.1f}  x{r['scale']:.1f}  "
                        f"{r['angle']:+.1f}deg  {'graded' if r['graded'] else 'no grade'}",
               fill=(0, 110, 0) if r["ok"] else (180, 0, 0))
        tiles.append(t)
    cols = 2
    rows = (len(tiles) + cols - 1) // cols
    img = Image.new("RGB", (740 * cols, 400 * rows), "white")
    for i, t in enumerate(tiles):
        img.paste(t, ((i % cols) * 740, (i // cols) * 400))
    img.save(out, quality=85)


def main():
    global MAX_DIFF
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--report", required=True, help="JSON list with a 'file' per site source")
    ap.add_argument("--country")
    ap.add_argument("--only", action="append", help="limit to files containing this text")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--candidates-dir", action="append",
                    help="also try every image in this folder (e.g. originals exported from the phone)")
    ap.add_argument("--max-diff", type=float, default=MAX_DIFF,
                    help="accept up to this difference (after checking the review sheet by eye)")
    a = ap.parse_args()
    MAX_DIFF = a.max_diff
    rows = json.loads(Path(a.report).read_text(encoding="utf-8"))
    files = sorted({r["file"] for r in rows
                    if (not a.country or r["file"].startswith(f"Images/{a.country}/"))
                    and (not a.only or any(o in r["file"] for o in a.only))})
    idx = pe.originals_index()
    lib = Path(idx["library"])
    # the trip window: every dated site photo's capture time, padded a day
    times = [k[0] for f in files for k in [pe.capture_key(ROOT / f)] if k]
    window = ((min(times)[:10] + "T00:00:00", (datetime.fromisoformat(max(times)) + timedelta(days=1)).isoformat())
              if times else ("0", "0"))
    # warm the feature cache for every candidate in parallel (decode is the cost)
    from concurrent.futures import ProcessPoolExecutor
    extra = [p for d in (a.candidates_dir or []) for p in Path(d).rglob("*")
             if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".heic", ".png")]
    allc = sorted({c for f in files for c in candidates(ROOT / f, idx, lib, window)[0] + extra
                   if c.exists() and c.suffix.lower() not in (".mov", ".mp4")})
    print(f"features for {len(allc)} originals (cached after the first run)...")
    with ProcessPoolExecutor(6) as ex:
        list(ex.map(original_features, allc, chunksize=4))
    results = []
    for f in files:
        path = ROOT / f
        cands, how = candidates(path, idx, lib, window)
        cands = extra + cands
        r = rebuild(path, cands) if cands else None
        if not r:
            print(f"  --  {f}: no original found ({len(cands)} candidates, {how})")
            continue
        r["name"], r["file"] = path.name, f
        results.append(r)
        print(f"  {'OK' if r['ok'] else '??'}  {f}\n      <- {r['original'].name}  x{r['scale']:.2f}  "
              f"{r['angle']:+.2f}deg  {r['inliers']} matches  diff {r['diff_before']:.1f} -> {r['diff']:.1f}"
              f"{'  (colour fitted)' if r['graded'] else ''}  -> {r['image'].shape[1]}x{r['image'].shape[0]}"
              f"  (best of {r.get('versions', 1)} matching versions)")
        if a.apply and r["ok"]:
            keep = KEEP / f
            keep.parent.mkdir(parents=True, exist_ok=True)
            if not keep.exists():
                shutil.copy2(path, keep)
            save(path, r["image"], r["icc"], r["original"])
            print("      replaced")
        # full-resolution arrays are 50-150 MB each: keep thumbnails for the sheet only
        r["image"], r["site"] = small(r["image"], 360)[0], small(r["site"], 360)[0]
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    if results:
        out = PREVIEWS / f"rebuild_{(a.country or 'all').replace(' ', '_')}.jpg"
        sheet(results, out)
        print(f"\nreview sheet: {out}")
    ok = [r for r in results if r["ok"]]
    # which original each site file was rebuilt from (full path: two exports can share a name)
    out_json = ROOT / ".tmp" / f"rebuild_results_{(a.country or 'all').replace(' ', '_')}.json"
    out_json.write_text(json.dumps([{"file": r["file"], "original": str(r["original"]), "ok": r["ok"],
                                     "diff": round(r["diff"], 2), "scale": round(r["scale"], 3),
                                     "applied": bool(a.apply and r["ok"])} for r in results],
                                   indent=1), encoding="utf-8")
    print(f"{len(ok)} ready, {len(results) - len(ok)} to check by eye, "
          f"{len(files) - len(results)} with no original")


if __name__ == "__main__":
    main()
