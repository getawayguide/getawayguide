"""Eraser v2: patch-based completion at FULL resolution, after PhotoCraft's Remove Tool (crates/algo/src/
nonlocal.rs, a port of A. Newson et al., "Non-Local Patch-Based Image Inpainting", IPOL 2017, MIT OR
Apache-2.0) and LightCraft's Remove/Heal spots (crates/pipeline/src/spots.rs). Kevin, 2026-10-14: "the AI
erase tool seems pretty poor and smudgy, study how lightcraft and photocraft do it and improve."

What was wrong: LaMa ran on a context crop capped at 1200 px and the result was scaled back up, so on a
24 MP photo a fill was painted at a quarter of the resolution around it, then feathered in: a smudge.

What this does (PhotoCraft's recipe, with LaMa in the one place it is better than their onion peel):
  * a pyramid of the context crop, full resolution at the bottom, ~1200 px at the top;
  * the COARSEST level is initialized by LaMa (global structure: where edges and regions continue), then
    refined by patch search + vote;
  * each finer level takes the upsampled match field, rebuilds the hole from its own full-resolution
    sources (search / vote EM, PatchMatch with jump-flood propagation vectorized in numpy), so the fill
    keeps the grain of its surroundings instead of an enlarged blur;
  * texture features (local mean |dx|, |dy|) join the patch distance (Liu & Caselles), so grass is not
    filled from a blurred background of the same mean color;
  * the final pixel comes from the best patch covering it (no averaging), and the color seam along the
    hole's edge is hidden in the gradient domain (Poisson: OpenCV's seamlessClone).
Spot mode is LightCraft's Remove: the best-matching source offset on rings around the spot (annulus SSD
score) healed in with the same Poisson clone, so a small spot takes real texture, not a Telea smear.
"""
import math

import numpy as np

PATCH_R = 3                 # patches are 7 x 7 (the paper's)
LAMBDA = 50.0               # weight of the texture features in the patch distance
COARSE_LONG = 1200          # the coarsest level's long edge (LaMa's working size)
CHUNK = 48000               # targets per vectorized batch (memory: CHUNK x 49 x 5 floats)


def _cv2():
    import cv2
    return cv2


def dilate(mask, r):
    cv2 = _cv2()
    if r <= 0:
        return mask.copy()
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * r + 1, 2 * r + 1))
    return cv2.dilate(mask.astype(np.uint8), k).astype(bool)


def hole_radius(hole):
    """Thickness: the largest distance from a hole cell to the nearest known cell."""
    cv2 = _cv2()
    if not hole.any():
        return 0
    d = cv2.distanceTransform(hole.astype(np.uint8), cv2.DIST_C, 3)
    return int(d.max())


def texture_features(img, hole, side):
    """Mean |dx| and |dy| of the channel mean over a `side` window, counting only derivatives between two
    known cells. Hole cells take the local average of the known ones around them (the completion then
    re-votes them along with the color)."""
    cv2 = _cv2()
    g = img[..., :3].mean(-1)
    known = ~hole
    out = []
    for axis in (1, 0):
        d = np.abs(np.diff(g, axis=axis))
        ok = (known[:, 1:] & known[:, :-1]) if axis == 1 else (known[1:] & known[:-1])
        dv = np.zeros_like(g)
        cnt = np.zeros_like(g)
        if axis == 1:
            dv[:, :-1] = d * ok
            cnt[:, :-1] = ok
        else:
            dv[:-1] = d * ok
            cnt[:-1] = ok
        s = cv2.blur(dv, (side, side), borderType=cv2.BORDER_REPLICATE)
        c = cv2.blur(cnt, (side, side), borderType=cv2.BORDER_REPLICATE)
        f = np.where(c > 0, s / np.maximum(c, 1e-6), 0).astype(np.float32)
        # the hole: a wide average of the known values around it
        kf = cv2.GaussianBlur(f * known, (0, 0), max(3.0, side))
        kn = cv2.GaussianBlur(known.astype(np.float32), (0, 0), max(3.0, side))
        f = np.where(known, f, kf / np.maximum(kn, 1e-6)).astype(np.float32)
        out.append(f)
    return out[0], out[1]


def downsample(img, hole):
    """Half size: the known cells' box average (a hole cell weighs nothing), the hole where any of the
    2 x 2 was hole."""
    cv2 = _cv2()
    h, w = hole.shape
    nw, nh = (w + 1) // 2, (h + 1) // 2
    known = (~hole).astype(np.float32)
    den = np.maximum(cv2.resize(known, (nw, nh), interpolation=cv2.INTER_AREA), 1e-6)
    # (cv2.resize takes at most 4 channels: one plane at a time)
    out = np.dstack([cv2.resize(img[..., c] * known, (nw, nh), interpolation=cv2.INTER_AREA) / den
                     for c in range(img.shape[-1])])
    hs = cv2.resize(hole.astype(np.uint8), (nw, nh), interpolation=cv2.INTER_AREA) > 0
    hs |= cv2.resize(hole.astype(np.uint8) * 255, (nw, nh), interpolation=cv2.INTER_NEAREST) > 0
    return out.astype(np.float32), hs


class Level:
    """One pyramid level: the image (color + texture channels), the hole, the valid sources, the targets
    (patch centres whose patch overlaps the hole) and their match field."""

    def __init__(self, img, hole, rng):
        self.img = img
        self.hole = hole
        self.h, self.w = hole.shape
        self.rng = rng
        r = PATCH_R
        self.valid = ~dilate(hole, r)
        self.valid[:r] = False
        self.valid[-r:] = False
        self.valid[:, :r] = False
        self.valid[:, -r:] = False
        vy, vx = np.nonzero(self.valid)
        self.valid_list = (vx.astype(np.int32), vy.astype(np.int32))
        self.targets = dilate(hole, r)
        ty, tx = np.nonzero(self.targets)
        self.tx, self.ty = tx.astype(np.int32), ty.astype(np.int32)
        self.n = len(tx)
        self.tindex = np.full((self.h, self.w), -1, np.int32)
        self.tindex[self.ty, self.tx] = np.arange(self.n, dtype=np.int32)
        self.sx = np.zeros(self.n, np.int32)
        self.sy = np.zeros(self.n, np.int32)
        self.cost = np.full(self.n, np.inf, np.float32)
        hy, hx = np.nonzero(hole)
        self.hx, self.hy = hx.astype(np.int32), hy.astype(np.int32)
        offs = [(dx, dy) for dy in range(-r, r + 1) for dx in range(-r, r + 1)]
        self.odx = np.array([o[0] for o in offs], np.int32)
        self.ody = np.array([o[1] for o in offs], np.int32)

    def random_sources(self, n):
        k = self.rng.integers(0, len(self.valid_list[0]), n)
        return self.valid_list[0][k], self.valid_list[1][k]

    def is_valid(self, x, y):
        ok = (x >= 0) & (y >= 0) & (x < self.w) & (y < self.h)
        xi = np.clip(x, 0, self.w - 1)
        yi = np.clip(y, 0, self.h - 1)
        return ok & self.valid[yi, xi]

    def patches(self, x, y):
        """(n, 49, cha) stack of the patches centred at (x, y), coordinates clipped to the grid."""
        px = np.clip(x[:, None] + self.odx[None, :], 0, self.w - 1)
        py = np.clip(y[:, None] + self.ody[None, :], 0, self.h - 1)
        return self.img[py, px]

    def dist(self, tp, x, y):
        """Mean squared difference between the target patch stack `tp` and the patches at (x, y)."""
        sp = self.patches(x, y)
        return np.mean((tp - sp) ** 2, axis=(1, 2))

    def search(self, radius, jumps=(1, 4)):
        """One PatchMatch iteration over the targets, vectorized: propagation from neighbours at the jump
        distances (both ways on both axes), then a random search in shrinking windows."""
        for c0 in range(0, self.n, CHUNK):
            sl = slice(c0, min(self.n, c0 + CHUNK))
            tx, ty = self.tx[sl], self.ty[sl]
            tp = self.patches(tx, ty)
            bx, by, bc = self.sx[sl].copy(), self.sy[sl].copy(), self.cost[sl].copy()

            def consider(cx, cy):
                nonlocal bx, by, bc
                ok = self.is_valid(cx, cy) & ((cx != bx) | (cy != by))
                if not ok.any():
                    return
                d = np.full(len(cx), np.inf, np.float32)
                d[ok] = self.dist(tp[ok], cx[ok], cy[ok])
                better = d < bc
                bx = np.where(better, cx, bx)
                by = np.where(better, cy, by)
                bc = np.where(better, d, bc)
            for j in jumps:
                for ddx, ddy in ((j, 0), (-j, 0), (0, j), (0, -j)):
                    nx = np.clip(tx - ddx, 0, self.w - 1)
                    ny = np.clip(ty - ddy, 0, self.h - 1)
                    ti = self.tindex[ny, nx]
                    has = ti >= 0
                    cx = np.where(has, self.sx[np.maximum(ti, 0)] + ddx, -1)
                    cy = np.where(has, self.sy[np.maximum(ti, 0)] + ddy, -1)
                    consider(cx.astype(np.int32), cy.astype(np.int32))
            rad = int(radius)
            while rad >= 1:
                cx = bx + self.rng.integers(-rad, rad + 1, len(bx))
                cy = by + self.rng.integers(-rad, rad + 1, len(by))
                consider(cx.astype(np.int32), cy.astype(np.int32))
                rad //= 4
            self.sx[sl], self.sy[sl], self.cost[sl] = bx, by, bc

    def refresh_costs(self):
        for c0 in range(0, self.n, CHUNK):
            sl = slice(c0, min(self.n, c0 + CHUNK))
            tp = self.patches(self.tx[sl], self.ty[sl])
            self.cost[sl] = self.dist(tp, self.sx[sl], self.sy[sl])

    def _covering(self):
        """For every hole cell and every patch offset: the covering target's index (-1 none) and the source
        cell that patch predicts for the hole cell."""
        px = np.clip(self.hx[:, None] + self.odx[None, :], 0, self.w - 1)
        py = np.clip(self.hy[:, None] + self.ody[None, :], 0, self.h - 1)
        ti = self.tindex[py, px]
        has = ti >= 0
        t = np.maximum(ti, 0)
        sx = self.sx[t] - self.odx[None, :]
        sy = self.sy[t] - self.ody[None, :]
        has &= (sx >= 0) & (sy >= 0) & (sx < self.w) & (sy < self.h)
        return t, has, np.clip(sx, 0, self.w - 1), np.clip(sy, 0, self.h - 1)

    def vote(self, uniform=False):
        """Re-estimate the hole as the similarity-weighted vote of the overlapping matches (sigma^2 from
        the 75th percentile of the costs, Wexler et al.). Returns the mean absolute color change."""
        t, has, sx, sy = self._covering()
        c = self.cost[t]
        finite = np.isfinite(self.cost)
        if uniform or not finite.any():
            w = has.astype(np.float32)
        else:
            s2 = max(float(np.percentile(self.cost[finite], 75)), 1e-6)
            w = np.where(has & np.isfinite(c), np.exp(-c / (2 * s2)), 0).astype(np.float32)
        ws = w.sum(1)
        src = self.img[sy, sx]                      # (H, 49, cha)
        new = (src * w[..., None]).sum(1) / np.maximum(ws, 1e-8)[:, None]
        ok = ws > 0
        old = self.img[self.hy[ok], self.hx[ok], :3]
        change = float(np.abs(new[ok, :3] - old).mean()) if ok.any() else 0.0
        self.img[self.hy[ok], self.hx[ok]] = new[ok]
        return change

    def copy_best(self):
        """The final reconstruction (the paper's eq. 5): each hole cell copies the source cell of the
        lowest-cost patch covering it (the grain of one source, not an average)."""
        t, has, sx, sy = self._covering()
        c = np.where(has, self.cost[t], np.inf)
        k = np.argmin(c, axis=1)
        rows = np.arange(len(k))
        ok = np.isfinite(c[rows, k])
        self.img[self.hy[ok], self.hx[ok]] = self.img[sy[rows, k][ok], sx[rows, k][ok]]
        return ok


def complete(img8, hole, init=None, seed=1, iters=(5, 2, 1), progress=None):
    """Fill `hole` (bool H x W) in the RGB uint8 image `img8` from its surroundings. `init`: an RGB uint8
    image of the same size whose hole pixels seed the coarsest level (LaMa's suggestion); without it the
    coarsest level starts from a diffusion of the edge. Returns the RGB uint8 result."""
    cv2 = _cv2()
    rng = np.random.default_rng(seed)
    H, W = hole.shape
    k = math.sqrt(LAMBDA)
    img = img8.astype(np.float32) / 255.0
    # levels: down to ~COARSE_LONG, at most what the hole's thickness asks for (Newson)
    long_edge = max(H, W)
    want_res = max(1, int(math.ceil(math.log2(max(1.0, long_edge / COARSE_LONG)))) + 1)
    n_patch = 2 * PATCH_R + 1
    want_thick = max(1, int(math.ceil(math.log2(max(1.0, 2.0 * hole_radius(hole) / n_patch)))))
    levels = max(want_res, want_thick)
    tx, ty = texture_features(img, hole, 1 << min(levels, 8))
    base = np.dstack([img, tx * k, ty * k]).astype(np.float32)
    pyramid = [(base, hole)]
    while len(pyramid) < levels:
        im, ho = downsample(*pyramid[-1])
        if min(ho.shape) < 3 * n_patch or not (~dilate(ho, PATCH_R)).any():
            break
        pyramid.append((im, ho))
    prev = None
    final = None
    n_levels = len(pyramid)
    for li in range(len(pyramid) - 1, -1, -1):
        # progress(fraction, note) -> False means "cancelled": stop here, the caller drops the result
        if progress is not None and progress((n_levels - 1 - li) / n_levels, "rebuilding level %d of %d" % (n_levels - li, n_levels)) is False:
            raise RuntimeError("cancelled")
        im, ho = pyramid[li]
        L = Level(im.copy(), ho, rng)
        if not len(L.valid_list[0]):
            return membrane_fill(img8, hole)
        coarsest = li == len(pyramid) - 1
        if prev is None:
            if init is not None:
                small = cv2.resize(init, (L.w, L.h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
                L.img[ho, :3] = small[ho]
            else:
                L.img[..., :3] = membrane_fill((L.img[..., :3] * 255).astype(np.uint8), ho).astype(np.float32) / 255.0
            rx, ry = L.random_sources(L.n)
            L.sx, L.sy = rx.copy(), ry.copy()
        else:
            P = prev
            cx = (P.sx[np.maximum(P.tindex[np.minimum(L.ty // 2, P.h - 1), np.minimum(L.tx // 2, P.w - 1)], 0)] * 2 + (L.tx % 2)).astype(np.int32)
            cy = (P.sy[np.maximum(P.tindex[np.minimum(L.ty // 2, P.h - 1), np.minimum(L.tx // 2, P.w - 1)], 0)] * 2 + (L.ty % 2)).astype(np.int32)
            ok = L.is_valid(cx, cy)
            rx, ry = L.random_sources(L.n)
            L.sx = np.where(ok, cx, rx).astype(np.int32)
            L.sy = np.where(ok, cy, ry).astype(np.int32)
            L.cost[:] = 0
            L.vote(uniform=True)
        full = max(L.w, L.h)
        n_it = iters[0] if coarsest else (iters[1] if li > 0 else iters[2])
        for it in range(n_it):
            L.refresh_costs()
            radius = full if (coarsest or it == 0) else min(full, 8 * n_patch)
            L.search(radius)
            # the coarsest level with LaMa's suggestion in the hole: the match field learns to explain
            # LaMa's structure, the pixels are not re-voted (a vote would average its edges away
            # before the finer levels copy real patches along them)
            if coarsest and init is not None:
                continue
            change = L.vote()
            if change < 0.1 / 255:
                break
        if li == 0:
            L.refresh_costs()
            L.copy_best()
            final = (np.clip(L.img[..., :3], 0, 1) * 255 + 0.5).astype(np.uint8)
        prev = L
    out = img8.copy()
    out[hole] = final[hole]
    return seam_blend(out, img8, hole)


def membrane_fill(img8, hole):
    """Harmonic diffusion of the edge into the hole (the fallback when nothing can be copied)."""
    cv2 = _cv2()
    return cv2.inpaint(img8, hole.astype(np.uint8) * 255, 3, cv2.INPAINT_NS)


def seam_blend(filled, orig, hole):
    """Hide the color seam along the hole's edge in the gradient domain (Pérez et al., Poisson Image
    Editing): the fill's gradients are kept, the edge mismatch is spread over the hole as a membrane."""
    cv2 = _cv2()
    m = dilate(hole, 1)
    ys, xs = np.nonzero(m)
    h, w = m.shape
    if not len(ys) or ys.min() < 1 or xs.min() < 1 or ys.max() > h - 2 or xs.max() > w - 2:
        return filled
    try:
        bx, by, bw, bh = cv2.boundingRect(m.astype(np.uint8))
        center = (bx + bw // 2, by + bh // 2)
        out = cv2.seamlessClone(filled[..., ::-1].copy(), orig[..., ::-1].copy(), m.astype(np.uint8) * 255, center, cv2.NORMAL_CLONE)
        return out[..., ::-1].copy()
    except Exception:
        return filled


# ---------------------------------------------------------------- Spot: LightCraft's Remove (auto-sourced heal)
def heal(img8, hole, seed=1):
    """Replace the spot with the best-matching patch of its surroundings: candidate source offsets on rings
    of 2.4, 3.2 and 4.2 radii around it, scored by how well the annulus around the source matches the
    annulus around the spot (sqrt-light SSD) plus a penalty for texture inside the source; the winner is
    cloned in with the Poisson blend, so its grain stays and its tone takes the spot's surroundings."""
    cv2 = _cv2()
    ys, xs = np.nonzero(hole)
    if not len(ys):
        return img8
    h, w = hole.shape
    r = max(3.0, 0.5 * max(ys.max() - ys.min() + 1, xs.max() - xs.min() + 1))
    ring = dilate(hole, int(round(0.6 * r))) & ~dilate(hole, max(1, int(round(0.05 * r))))
    ry, rx = np.nonzero(ring)
    lin = np.sqrt(img8.astype(np.float32) / 255.0)
    g = lin.mean(-1)
    best, best_d = None, np.inf
    for ringk in (2.4, 3.2, 4.2):
        for i in range(16):
            a = i / 16 * 2 * math.pi
            dx, dy = int(round(math.cos(a) * r * ringk)), int(round(math.sin(a) * r * ringk))
            sx, sy = rx + dx, ry + dy
            ok = (sx >= 0) & (sy >= 0) & (sx < w) & (sy < h)
            if ok.sum() < 0.9 * len(rx):
                continue
            sx, sy = sx[ok], sy[ok]
            if hole[sy, sx].any():
                continue
            d = float(np.mean((lin[ry[ok], rx[ok]] - lin[sy, sx]) ** 2))
            # texture continuity: the source's inside should be as flat as the target's ring
            hx2, hy2 = xs + dx, ys + dy
            ok2 = (hx2 >= 0) & (hy2 >= 0) & (hx2 < w) & (hy2 < h)
            if ok2.any():
                d += 0.5 * float(np.mean((g[hy2[ok2], hx2[ok2]] - g[ry[ok], rx[ok]].mean()) ** 2))
            if d < best_d:
                best, best_d = (dx, dy), d
    if best is None:
        return membrane_fill(img8, hole)
    dx, dy = best
    src = np.zeros_like(img8)
    # the image shifted by -offset: src[p] = img[p + d]
    ys2, xs2 = np.nonzero(dilate(hole, 2))
    sy2, sx2 = np.clip(ys2 + dy, 0, h - 1), np.clip(xs2 + dx, 0, w - 1)
    src[ys2, xs2] = img8[sy2, sx2]
    out = img8.copy()
    out[hole] = src[hole]
    return seam_blend(out, img8, hole)
