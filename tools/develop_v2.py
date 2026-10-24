"""Develop engine v2: the photo editor's slider math, ported from LightCraft (github.com/storytold/lightcraft,
MIT OR Apache-2.0, v0.8.0), which is built after Lightroom. Kevin, 2026-10-14: "the logic and code there is
probably better than anything we've iterated, so err on the side of using their code, but cater to work with
our app." What is theirs: linear-light exposure, Bradford white balance on a Kelvin track, a luminance-only tone
map with hue-preserving RGB scaling, highlights/shadows on an edge-aware (guided-filter) base, clarity on a
second guided band, texture/sharpening/noise on a fine Gaussian band, dark-channel dehaze with an airlight
estimate, OkLab vibrance (skin-protected), saturation, the 8-band color mixer, 3-way color grading, the
parametric tone curve, the superellipse vignette, and the mask shapes (linear, radial, luminance range, color
range) whose terms are alpha-summed into the global sliders in one per-pixel pass. What is ours: every control
is a -100..100 slider (their Kelvin/EV/mired units are mapped onto that), the working space is Display P3 (the
photos' own), the sky mask is PhotoCraft's classical heuristic, and the whole thing is one pass over the pixels
so the WebGL preview in editor.html can run the identical formulas.

CONTRACT: DEV_KEYS order is shared with editor.html (the shader indexes u_p[] by position) and every formula
here has a twin in its GLSL. tools/test_develop_parity.py renders both and compares them pixel for pixel.

Spatial "planes" (the guided-filter coefficients of the tone base and the clarity band, the normalized dark
channel, the sky mask) are computed ONCE per photo at <= PLANE_LONG px on the long edge, from the source's log
luminance, and shared with the preview through /api/planes as raw bytes; the per-pixel pass then only reads them.
"""
import math
import struct

import numpy as np

GREY = 0.18
EV_RANGE = 5.0              # exposure slider +-100 -> +-5 EV, Lightroom's range
PLANE_LONG = 1024           # the planes' long edge (they are smooth: guided-filter windows are >= 1.2 % of the edge)
SKY_LONG = 400              # the sky heuristic works at this size (PhotoCraft: ~160 k px)

# ---------------------------------------------------------------- parameters
# ORDER IS A CONTRACT with editor.html. Each value is -100..100 (hue wheels: -180..180 degrees).
MIX_BANDS = "roygabpm"      # red, orange, yellow, green, aqua, blue, purple, magenta
GRADE_WHEELS = ("sh", "mi", "hi", "gl")
DEV_KEYS = (("exposure", "contrast", "highlights", "shadows", "whites", "blacks",
             "temp", "tint", "vibrance", "saturation", "sharpen", "vignette",
             "dehaze", "clarity", "noise", "vigmid", "vigfeather", "texture",
             "curve_hi", "curve_li", "curve_da", "curve_sh")
            + tuple(f"mix_{b}_{s}" for b in MIX_BANDS for s in "hsl")
            + tuple(f"grade_{w}_{s}" for w in GRADE_WHEELS for s in "hsl")
            + ("grade_blend", "grade_bal"))
IDX = {k: i for i, k in enumerate(DEV_KEYS)}
# per-mask adjustments, alpha-weighted into the globals (LightCraft's local terms, the ones that are per pixel)
MASK_TERMS = ("exposure", "contrast", "highlights", "shadows", "temp", "tint", "saturation", "clarity", "dehaze")
MASK_TYPES = {"linear": 0, "radial": 1, "lum": 2, "color": 3, "sky": 4, "brush": 5}

# OkLCh hue (radians) of the mixer bands' centres: pure sRGB colors of HSV hue 0, 30, 60, 120, 180, 225, 270,
# 315 (LightCraft MIXER_HUES through oklch_hue_of_srgb_hue), and of skin (HSV 25).
TAU = 2 * math.pi

# ---------------------------------------------------------------- color spaces (Display P3, D65)
P3_TO_XYZ = np.array([[0.4865709486482161, 0.26566769316909306, 0.19821728523436247],
                      [0.22897456406974875, 0.6917385218365063, 0.07928691409374498],
                      [0.0, 0.04511338185890263, 1.0439443689009757]])
XYZ_TO_P3 = np.array([[2.4934969119414254, -0.931383617919124, -0.4027107844507169],
                      [-0.8294889695615747, 1.7626640603183465, 0.023624685841943563],
                      [0.03584583024378446, -0.07617238926804183, 0.9568845240076874]])
LUMA = (0.22897456406974875, 0.6917385218365063, 0.07928691409374498)
# OkLab: linear P3 -> LMS (XYZ->LMS of Björn Ottosson composed with P3->XYZ), and the LMS'->Lab matrix
P3_TO_LMS = np.array([[0.4813272911960762, 0.4620679116724794, 0.05649560287357197],
                      [0.22883810133173532, 0.6532343999261817, 0.11795441319019855],
                      [0.08398601779387468, 0.2242727892997344, 0.6922208388786097]], dtype=np.float32)
LMS_TO_P3 = np.array([[3.128110529032311, -2.2570750183548705, 0.1293047884378313],
                      [-1.0911281609083134, 2.4132667617599557, -0.3221681708589349],
                      [-0.026013649706485314, -0.5080276489512767, 1.5333166822296194]], dtype=np.float32)
M2 = np.array([[0.2104542553, 0.793617785, -0.0040720468],
               [1.9779984951, -2.428592205, 0.4505937099],
               [0.0259040371, 0.7827717662, -0.808675766]], dtype=np.float32)
M2I = np.array([[1.0, 0.39633779217376774, 0.2158037580607588],
                [1.0, -0.10556134232365633, -0.0638541747717059],
                [1.0, -0.08948418209496574, -1.2914855378640917]], dtype=np.float32)
SRGB_TO_XYZ = np.array([[0.4123907993, 0.3575843394, 0.1804807884],
                        [0.2126390059, 0.7151686788, 0.0721923154],
                        [0.0193308187, 0.1191947798, 0.9505321522]])
XYZ_TO_LMS = np.array([[0.8189330101, 0.3618667424, -0.1288597137],
                       [0.0329845436, 0.9293118715, 0.0361456387],
                       [0.0482003018, 0.2643662691, 0.6338517070]])
BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
D65 = (0.3127, 0.3290)
AS_SHOT_K = 6500.0          # a rendered file's white: slider 0 = as shot (LightCraft: 6500 K / 0)
TINT_SCALE = 3000.0         # tint units per unit of Duv
MIN_K, MAX_K = 1667.0, 25000.0


def ss(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def luma(c):
    return c[..., 0] * LUMA[0] + c[..., 1] * LUMA[1] + c[..., 2] * LUMA[2]


def oklab(c):
    """Linear P3 (..., 3) -> OkLab (..., 3)."""
    lms = c @ P3_TO_LMS.T
    lms = np.cbrt(lms)
    return lms @ M2.T


def oklab_inv(lab):
    lms = lab @ M2I.T
    return (lms * lms * lms) @ LMS_TO_P3.T


def oklch_hue_of_srgb_hue(deg):
    """OkLCh hue (radians) of the pure sRGB color with HSV hue `deg` (the color-grading wheels' hue)."""
    h = (deg % 360.0) / 60.0
    i = int(h) % 6
    f = h - int(h)
    rgb = [(1, f, 0), (1 - f, 1, 0), (0, 1, f), (0, 1 - f, 1), (f, 0, 1), (1, 0, 1 - f)][i]
    lin = [(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4) for v in rgb]
    lms = XYZ_TO_LMS @ (SRGB_TO_XYZ @ np.array(lin))
    lab = M2.astype(float) @ np.cbrt(lms)
    return float(math.atan2(lab[2], lab[1]))


BAND_HUES = tuple(oklch_hue_of_srgb_hue(d) for d in (0, 30, 60, 120, 180, 225, 270, 315))
SKIN_HUE = oklch_hue_of_srgb_hue(25)


# ---------------------------------------------------------------- white balance (LightCraft cct.rs)
def kim_xy(t):
    t = min(max(t, MIN_K), MAX_K)
    t2, t3 = t * t, t * t * t
    if t <= 4000.0:
        x = -0.2661239e9 / t3 - 0.2343589e6 / t2 + 0.8776956e3 / t + 0.179910
    else:
        x = -3.0258469e9 / t3 + 2.1070379e6 / t2 + 0.2226347e3 / t + 0.240390
    x2, x3 = x * x, x * x * x
    if t <= 2222.0:
        y = -1.1063814 * x3 - 1.34811020 * x2 + 2.18555832 * x - 0.20219683
    elif t <= 4000.0:
        y = -0.9549476 * x3 - 1.37418593 * x2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x3 - 5.87338670 * x2 + 3.75112997 * x - 0.37001483
    return (x, y)


def xy_to_uv(p):
    d = -2.0 * p[0] + 12.0 * p[1] + 3.0
    return (4.0 * p[0] / d, 6.0 * p[1] / d)


def uv_to_xy(u, v):
    d = 2.0 * u - 8.0 * v + 4.0
    return (3.0 * u / d, 2.0 * v / d)


def locus_uv(t):
    t = max(t, 1000.0)
    if MIN_K <= t <= MAX_K:
        return xy_to_uv(kim_xy(t))
    a, b = (MIN_K, MIN_K + 50.0) if t < MIN_K else (MAX_K - 500.0, MAX_K)
    ua, va = xy_to_uv(kim_xy(a))
    ub, vb = xy_to_uv(kim_xy(b))
    ma, mb, m = 1e6 / a, 1e6 / b, 1e6 / t
    s = (m - ma) / (mb - ma)
    return (ua + (ub - ua) * s, va + (vb - va) * s)


def locus_normal(t):
    m = 1e6 / t
    u0, v0 = locus_uv(1e6 / (m + 1.0))
    u1, v1 = locus_uv(1e6 / max(m - 1.0, 1.0))
    du, dv = u1 - u0, v1 - v0
    ln = max(math.hypot(du, dv), 1e-12)
    nx, ny = -dv / ln, du / ln
    return (-nx, -ny) if ny < 0.0 else (nx, ny)


def temp_tint_to_xy(t, tint):
    u, v = locus_uv(t)
    nu, nv = locus_normal(t)
    duv = tint / TINT_SCALE
    return uv_to_xy(u + nu * duv, v + nv * duv)


def xy_to_temp_tint(p):
    u, v = xy_to_uv(p)

    def dist(m):
        lu, lv = locus_uv(1e6 / m)
        return (u - lu) ** 2 + (v - lv) ** 2
    best, bd, m = 20.0, float("inf"), 20.0
    while m <= 1000.0:
        d = dist(m)
        if d < bd:
            bd, best = d, m
        m += 5.0
    a, b = max(best - 5.0, 20.0), min(best + 5.0, 1000.0)
    g = 0.61803398875
    for _ in range(60):
        c = b - g * (b - a)
        d = a + g * (b - a)
        if dist(c) < dist(d):
            b = d
        else:
            a = c
    m = (a + b) / 2.0
    t = 1e6 / m
    lu, lv = locus_uv(t)
    nu, nv = locus_normal(t)
    duv = (u - lu) * nu + (v - lv) * nv
    return (t, duv * TINT_SCALE)


def xy_to_xyz(p):
    return np.array([p[0] / p[1], 1.0, (1.0 - p[0] - p[1]) / p[1]])


def bradford(src, dst):
    s = BRADFORD @ xy_to_xyz(src)
    d = BRADFORD @ xy_to_xyz(dst)
    return np.linalg.inv(BRADFORD) @ np.diag(d / s) @ BRADFORD


def wb_matrix(src_xy):
    """Linear P3 -> linear P3: a scene lit by `src_xy` rendered as if lit by D65."""
    return XYZ_TO_P3 @ bradford(src_xy, D65) @ P3_TO_XYZ


# The temperature slider is LightCraft's Kelvin track (Lightroom's: 2000 K to the centre evenly, then
# logarithmic to 50000 K), re-centred on the rendered file's as-shot 6500 K so that 0 means no change.
KELVIN_MIN, KELVIN_MAX, KELVIN_KNEE = 2000.0, 50000.0, 1000.0
_KELVIN_RIGHT = math.log(1.0 + (KELVIN_MAX - AS_SHOT_K) / KELVIN_KNEE)


def slider_to_kelvin(t):
    p = min(max((t + 100.0) / 200.0, 0.0), 1.0)
    if p >= 1.0:
        return KELVIN_MAX
    if p > 0.5:
        return AS_SHOT_K + KELVIN_KNEE * (math.exp((2.0 * p - 1.0) * _KELVIN_RIGHT) - 1.0)
    return KELVIN_MIN + 2.0 * p * (AS_SHOT_K - KELVIN_MIN)


def kelvin_to_slider(k):
    k = min(max(k, KELVIN_MIN), KELVIN_MAX)
    if k > AS_SHOT_K:
        p = 0.5 + 0.5 * math.log(1.0 + (k - AS_SHOT_K) / KELVIN_KNEE) / _KELVIN_RIGHT
    else:
        p = 0.5 * (k - KELVIN_MIN) / (AS_SHOT_K - KELVIN_MIN)
    return p * 200.0 - 100.0


def wb_for_sliders(temp, tint):
    """The white-balance matrix for slider values (-100..100 each; tint spans LightCraft's +-150), or None at
    0/0. Luminance-normalized, as LightCraft's wb_matrix_for: white balance never changes exposure."""
    if abs(temp) < 1e-6 and abs(tint) < 1e-6:
        return None
    k = slider_to_kelvin(temp)
    m = wb_matrix(temp_tint_to_xy(k, tint * 1.5)) @ np.linalg.inv(wb_matrix(temp_tint_to_xy(AS_SHOT_K, 0.0)))
    g = m @ np.array([1.0, 1.0, 1.0])
    y = g[0] * LUMA[0] + g[1] * LUMA[1] + g[2] * LUMA[2]
    return (m / y).astype(np.float32)


def neutral_sliders(avg_rgb_lin):
    """Temp/tint sliders that render the linear P3 color `avg_rgb_lin` neutral (LightCraft auto_wb's last
    step: the white seen through a Bradford adaptation from the as-shot white). Used by the WB eyedropper and
    Auto WB, which average a patch or the weighted scene first."""
    shot = temp_tint_to_xy(AS_SHOT_K, 0.0)
    xyz = bradford(D65, shot) @ (P3_TO_XYZ @ np.asarray(avg_rgb_lin, dtype=float))
    s = xyz.sum()
    seen = D65 if s <= 0 else (xyz[0] / s, xyz[1] / s)
    t, tint = xy_to_temp_tint(seen)
    return (kelvin_to_slider(t), min(max(tint / 1.5, -100.0), 100.0))


# ---------------------------------------------------------------- spatial planes
def gaussian(p, sigma):
    import cv2
    return cv2.GaussianBlur(np.ascontiguousarray(p, dtype=np.float32), (0, 0), sigma, borderType=cv2.BORDER_REFLECT)


def guided_coeffs(p, sigma, eps):
    """Self-guided filter coefficients (He et al.) with Gaussian windows: q = mean(a) * p + mean(b)."""
    mean = gaussian(p, sigma)
    corr = gaussian(p * p, sigma)
    var = np.maximum(corr - mean * mean, 0)
    a = var / (var + eps)
    b = mean - a * mean
    return gaussian(a, sigma), gaussian(b, sigma)


def guided_cross(guide, p, sigma, eps):
    """p filtered with `guide`'s edges (LightCraft guided_cross: mask refinement)."""
    mi = gaussian(guide, sigma)
    mp = gaussian(p, sigma)
    cov = gaussian(guide * p, sigma) - mi * mp
    var = gaussian(guide * guide, sigma) - mi * mi
    a = cov / (var + eps)
    b = mp - a * mi
    return gaussian(a, sigma) * guide + gaussian(b, sigma)


def log_luma(a):
    """Encoded P3 (H, W, 3) 0..1 -> log2 luminance over middle grey (LightCraft log_lum)."""
    lin = np.power(np.clip(a, 0, 1), 2.2, dtype=np.float32)
    return np.log2(np.maximum(luma(lin), 1e-7) / GREY).astype(np.float32)


def tex_kernel(long_edge):
    """The texture / sharpening band: a Gaussian of sigma 0.18 % of the long edge, sampled on 17 taps (centre,
    two rings at r1 and 2 r1 on the axes and diagonals) so the single-pass shader can take the same blur.
    Returns (sigma, [(dx, dy, weight), ...]) with the weights normalized."""
    sigma = max(0.6, 0.0018 * long_edge)
    r1 = max(1, int(round(sigma)))
    taps = [(0, 0)]
    for r in (r1, 2 * r1):
        taps += [(r, 0), (-r, 0), (0, r), (0, -r), (r, r), (r, -r), (-r, r), (-r, -r)]
    w = [math.exp(-(dx * dx + dy * dy) / (2 * sigma * sigma)) for dx, dy in taps]
    s = sum(w)
    return sigma, [(dx, dy, wi / s) for (dx, dy), wi in zip(taps, w)]


def tex_plane(L, long_edge):
    _, taps = tex_kernel(long_edge)
    r = max(abs(dx) for dx, _, _ in taps)
    H, W = L.shape
    Lp = np.pad(L, r, mode="edge")
    out = np.zeros_like(L)
    for dx, dy, w in taps:
        out += w * Lp[r + dy:r + dy + H, r + dx:r + dx + W]
    return out


def _resize(p, w, h, down):
    import cv2
    return cv2.resize(np.ascontiguousarray(p, dtype=np.float32), (w, h),
                      interpolation=cv2.INTER_AREA if down else cv2.INTER_LINEAR)


def planes(a):
    """The per-photo planes of an encoded P3 float32 image (H, W, 3): guided-filter coefficients of the tone
    base (sigma 1.5 % of the long edge, eps 0.35 EV^2) and of the clarity band (1.2 %, 0.8), the normalized dark
    channel (min RGB in linear light, Gaussian 2 %) with its airlight (99.5th percentile), and the sky mask;
    all at <= PLANE_LONG px. The pass reads them as base = ma_b * L + mb_b, etc."""
    H, W = a.shape[:2]
    long_edge = max(H, W)
    k = min(1.0, PLANE_LONG / long_edge)
    pw, ph = max(1, int(round(W * k))), max(1, int(round(H * k)))
    L = log_luma(a)
    small = _resize(a, pw, ph, True) if k < 1 else a
    Ls = log_luma(small)
    ppl = max(pw, ph)
    ma_b, mb_b = guided_coeffs(Ls, max(1.0, 0.015 * ppl), 0.35)
    ma_c, mb_c = guided_coeffs(Ls, max(1.0, 0.012 * ppl), 0.8)
    lin = np.power(np.clip(small, 0, 1), 2.2, dtype=np.float32)
    dark = gaussian(lin.min(axis=-1), max(1.0, 0.02 * ppl))
    samp = np.sort(dark.ravel()[::7])
    air = float(max(samp[min(len(samp) - 1, int(len(samp) * 0.995))], 0.05))
    dark_n = np.clip(dark / air, 0, 1).astype(np.float32)
    sky = sky_mask(small, Ls)
    return {"w": pw, "h": ph, "ma_b": ma_b, "mb_b": mb_b, "ma_c": ma_c, "mb_c": mb_c,
            "dark": dark_n, "air": air, "sky": sky, "L": L}


MB_LO, MB_SPAN = -12.0, 16.0        # mean(b) of the guided filters is packed 16-bit over -12..4 EV


def pack_planes(P):
    """Bytes for the preview: a 16-byte header (w, h, airlight, flags) then two RGBA8 planes:
    A = (ma_b, mb_b hi, mb_b lo, dark) and B = (ma_c, mb_c hi, mb_c lo, sky). The shader decodes the 16-bit
    pair itself and interpolates after decoding (a hi/lo split cannot be filtered by the GPU)."""
    def b8(x):
        return (np.clip(x, 0, 1) * 255 + 0.5).astype(np.uint8)

    def b16(x):
        v = (np.clip((x - MB_LO) / MB_SPAN, 0, 1) * 65535 + 0.5).astype(np.uint16)
        return (v >> 8).astype(np.uint8), (v & 255).astype(np.uint8)
    hb, lb = b16(P["mb_b"])
    hc, lc = b16(P["mb_c"])
    A = np.dstack([b8(P["ma_b"]), hb, lb, b8(P["dark"])])
    B = np.dstack([b8(P["ma_c"]), hc, lc, b8(P["sky"])])
    return struct.pack("<IIfI", P["w"], P["h"], P["air"], 2) + A.tobytes() + B.tobytes()


def unpack_mb(hi, lo):
    return (hi.astype(np.float32) * 256 + lo.astype(np.float32)) / 65535 * MB_SPAN + MB_LO


# ---------------------------------------------------------------- sky (PhotoCraft segment/sky.rs)
def sky_color_prior(p):
    r, g, b = p[..., 0], p[..., 1], p[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    mx = p.max(-1)
    mn = p.min(-1)
    sat = np.where(mx > 1e-4, (mx - mn) / np.maximum(mx, 1e-4), 0)
    blue = ss(0.02, 0.12, b - r) * ss(-0.12, 0.0, b - g) * ss(0.18, 0.4, y)
    white = ss(0.5, 0.75, y) * (1 - ss(0.12, 0.3, sat))
    warm = 0.6 * ss(0.55, 0.8, y) * ss(0.0, 0.1, r - b) * (1 - ss(0.05, 0.25, g - b - 0.2))
    return np.maximum(np.maximum(blue, white), warm)


def sky_mask(small, Ls, threshold=50.0):
    """Sky likelihood (color prior x smoothness) grown from the top rows through gradually changing colors,
    small holes filled, refined against the log luminance so edges follow foliage. Zeros when nothing at the
    top looks like sky. `small` is the encoded image at the planes' size; the work runs at <= SKY_LONG px."""
    import cv2
    ph, pw = small.shape[:2]
    k = min(1.0, SKY_LONG / max(ph, pw))
    w, h = max(2, int(round(pw * k))), max(2, int(round(ph * k)))
    img = _resize(small, w, h, True) if k < 1 else small.astype(np.float32)
    y = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
    gx = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3, borderType=cv2.BORDER_REPLICATE)
    gy = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3, borderType=cv2.BORDER_REPLICATE)
    grad = np.sqrt(gx * gx + gy * gy) / 8.0
    tex = cv2.blur(grad, (5, 5), borderType=cv2.BORDER_REPLICATE)
    like = sky_color_prior(img) * (1 - ss(0.015, 0.07, tex))
    t = 0.12 + 0.6 * (min(max(threshold, 0), 100) / 100.0)
    top = max(1, h // 40)
    likely = like > t
    mask = np.zeros((h, w), bool)
    mask[:top] = likely[:top]
    if mask[:top].sum() * 20 < top * w:
        return np.zeros((ph, pw), np.float32)
    # region growing with a gradual-change constraint: OpenCV's floating-range flood fill from the seeds (a
    # pixel joins when it is within the tolerance of the neighbour it is reached from); unlikely pixels are
    # painted a far-off color first so the fill cannot cross them
    img8 = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    img8[~likely] = (255, 0, 255)
    img8[likely & (img8 == (255, 0, 255)).all(-1)] = (254, 0, 255)
    fill = np.zeros((h + 2, w + 2), np.uint8)
    flags = 4 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY
    for yy, xx in zip(*np.nonzero(mask)):
        if fill[yy + 1, xx + 1]:
            continue
        cv2.floodFill(img8, fill, (int(xx), int(yy)), 0, (28, 28, 28), (28, 28, 28), flags)
    mask = fill[1:-1, 1:-1] > 0
    m8 = mask.astype(np.uint8)
    r = max(1, int(round(0.01 * max(w, h))))
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    m8 = cv2.morphologyEx(m8, cv2.MORPH_CLOSE, ker)
    m8 = cv2.morphologyEx(m8, cv2.MORPH_OPEN, ker)
    if not m8.any():
        return np.zeros((ph, pw), np.float32)
    # a neutral region covering the bottom rows too is a backdrop, not a sky
    bottom = m8[h - top:]
    if bottom.mean() > 0.6:
        sel = m8 > 0
        mx, mn = img[sel].max(-1), img[sel].min(-1)
        if np.mean(np.where(mx > 1e-4, (mx - mn) / np.maximum(mx, 1e-4), 0)) < 0.1:
            return np.zeros((ph, pw), np.float32)
    up = _resize(m8.astype(np.float32), pw, ph, False)
    ref = guided_cross(Ls, up, max(1.5, 0.008 * max(pw, ph)), 0.02)
    return np.clip(ref, 0, 1).astype(np.float32)


# ---------------------------------------------------------------- the per-pixel pass
def params(p):
    """Slider dict -> floats in the shader's units (value / 100)."""
    return {k: float(p.get(k, 0) or 0) / 100.0 for k in DEV_KEYS}


def wheel_terms(v):
    """Color grading wheels as OkLab offsets (a, b, L), LightCraft colorops::wheel: hue in degrees on the
    HSV circle -> OkLCh hue; sat * 0.09 chroma; lum * 0.12 lightness."""
    out = []
    for w in GRADE_WHEELS:
        h = oklch_hue_of_srgb_hue(v[f"grade_{w}_h"] * 100.0)
        s = v[f"grade_{w}_s"] * 0.09
        out.append((s * math.cos(h), s * math.sin(h), v[f"grade_{w}_l"] * 0.12))
    return out


def tex_weights(long_edge):
    return tex_kernel(long_edge)[1]


def band_weights(h):
    """Partition-of-unity raised-cosine weights of OkLCh hue `h` (array) over the 8 mixer bands."""
    w = [np.zeros_like(h) for _ in range(8)]
    done = np.zeros(h.shape, bool)
    for i in range(8):
        a, b = BAND_HUES[i], BAND_HUES[(i + 1) % 8]
        span = (b - a) % TAU
        d = (h - a) % TAU
        sel = (d <= span) & ~done
        t = d / span
        s = 0.5 - 0.5 * np.cos(t * math.pi)
        w[i] = w[i] + np.where(sel, 1 - s, 0)
        w[(i + 1) % 8] = w[(i + 1) % 8] + np.where(sel, s, 0)
        done |= sel
    return w


def wrap_angle(a):
    return (a + math.pi) % TAU - math.pi


def tone_lum(y, c, w, b):
    """LightCraft ToneMap::display: identity at neutral; contrast / whites / blacks as S-curve adjustments in
    the gamma-2.2 domain, with a short shoulder above 0.95."""
    m = GREY ** (1 / 2.2)
    p = np.power(np.maximum(y, 0), 1 / 2.2, dtype=np.float32)
    hi = p > 1.0
    q = p + c * 0.35 * (p - m) * (1 - (2 * p - 1) ** 2)
    up = ss(0.45, 1.0, q)
    q = q + w * 0.12 * up * (1 - q * 0.5)
    lo = 1 - ss(0.0, 0.45, q)
    q = q + b * 0.07 * lo * np.minimum(q * 2, 1)
    p = np.where(hi, p + w * 0.12 * 0.5, q)
    o = np.power(np.maximum(p, 0), 2.2, dtype=np.float32)
    d = np.minimum(o - 0.95, 0.1)
    o = np.where(o > 0.95, 0.95 + d - d * d / 0.2, o)
    return np.clip(o, 0, 1)


def mask_alpha(m, H, W, lin, L, ev, sky_full, y0=0, y1=None):
    """A mask's alpha (rows y0..y1) from its shape. `lin` is the linear source (rows), `L` its log luminance,
    `sky_full` the sky plane at image size."""
    y1 = H if y1 is None else y1
    kind = m.get("type", "linear")
    if kind in ("linear", "radial"):
        yy, xx = np.mgrid[y0:y1, 0:W].astype(np.float32)
        u, v = xx / W, yy / H
    if kind == "radial":
        cx, cy = float(m["x0"]), float(m["y0"])
        rx, ry = max(1e-4, abs(float(m["x1"]) - cx)), max(1e-4, abs(float(m["y1"]) - cy))
        f = min(max(float(m.get("feather", 50)) / 100.0, 0.0), 1.0)
        r = np.sqrt(((u - cx) / rx) ** 2 + ((v - cy) / ry) ** 2)
        a = 1 - ss(1 - max(f, 0.01), 1.0, r)
    elif kind == "linear":
        x0, yy0 = float(m["x0"]), float(m["y0"])
        dx, dy = float(m["x1"]) - x0, float(m["y1"]) - yy0
        ln = max(1e-6, dx * dx + dy * dy)
        a = 1 - ss(0.0, 1.0, ((u - x0) * dx + (v - yy0) * dy) / ln)
    elif kind == "lum":
        lo, hi = float(m.get("lo", 0)) / 100.0, float(m.get("hi", 100)) / 100.0
        lf = max(float(m.get("lf", 10)) / 100.0, 1e-3)
        hf = max(float(m.get("hf", 10)) / 100.0, 1e-3)
        y = np.clip((L + ev + 8.0) / 12.0, 0, 1)
        a = ss(lo - lf, lo, y) * (1 - ss(hi, hi + hf, y))
    elif kind == "color":
        tol = 0.015 + 0.06 * min(max(float(m.get("refine", 50)) / 100.0, 0.0), 1.0)
        c = lin * (2.0 ** ev)
        lab = oklab(c / (1 + c))
        s = m.get("lab") or [0.5, 0, 0]
        d = np.sqrt((lab[..., 1] - s[1]) ** 2 + (lab[..., 2] - s[2]) ** 2 + 0.25 * (lab[..., 0] - s[0]) ** 2)
        a = 1 - ss(tol * 0.5, tol, d)
    elif kind == "brush":
        a = m["_alpha"][y0:y1]                 # painted in the editor, decoded by photo_editor.brush_alphas
    else:
        a = sky_full[y0:y1]
    a = a.astype(np.float32)
    if m.get("invert"):
        a = 1 - a
    return a


LIN8 = np.power(np.arange(256, dtype=np.float32) / 255.0, 2.2, dtype=np.float32)


def finish(a8, L, tex, pl, air, v, wheels, wbm, lt, y0, H, W, taps_long):
    """One pass over the rows a8[y0:y0+n] (encoded P3 uint8), LightCraft finish.rs in numpy. `pl` holds
    the row slices of the upsampled planes, `lt` the alpha-summed local terms (arrays) or None."""
    n = a8.shape[0]
    c = LIN8[a8]
    ev = v["exposure"] * EV_RANGE
    if wbm is not None:
        c = np.maximum(c @ wbm.T, 0)
    c = c * np.float32(2.0 ** ev)
    l_pre = L
    l0 = l_pre + ev
    T = (lambda k: lt[k]) if lt else (lambda k: 0.0)
    # --- dehaze (scene linear)
    dz = v["dehaze"] + T("dehaze")
    if np.any(dz != 0):
        d = pl["dark"]
        airv = air * (2.0 ** ev)
        dz3 = np.asarray(dz, dtype=np.float32)
        t = np.maximum(1 - 0.95 * np.minimum(dz3, 1) * d, 0.12)
        pos = np.maximum((c - airv * (1 - t)[..., None]) / t[..., None], 0)
        kneg = np.minimum(-dz3, 1) * 0.7 * (0.35 + 0.65 * d)
        neg = c + (airv * 0.9 - c) * kneg[..., None]
        c = np.where((dz3 > 0)[..., None], pos, np.where((dz3 < 0)[..., None], neg, c))
    # --- local exposure / temp / tint
    le = T("exposure")
    if np.any(le != 0):
        c = c * np.exp2(le, dtype=np.float32)[..., None]
    ltemp, ltint = T("temp"), T("tint")
    if np.any(ltemp != 0) or np.any(ltint != 0):
        yb = luma(c)
        c = np.stack([c[..., 0] * (1 + 0.3 * ltemp), c[..., 1] * (1 - 0.22 * ltint),
                      c[..., 2] * np.maximum(1 - 0.3 * ltemp, 0)], -1)
        c = c * (yb / np.maximum(luma(c), 1e-9))[..., None]
    # --- tone in log luminance
    l1 = np.log2(np.maximum(luma(c), 1e-7) / GREY)
    base = pl["ma_b"] * l_pre + pl["mb_b"] + ev + (l1 - l0)
    delta = np.zeros_like(l1)
    hh = v["highlights"] + T("highlights")
    sh = v["shadows"] + T("shadows")
    if np.any(hh != 0) or np.any(sh != 0):
        ws = 1 - ss(-4.8, 0.3, base)
        wh = ss(-1.0, 2.8, base)
        delta += sh * 1.7 * ws * np.sqrt(ws) + hh * 1.7 * wh
    lc = T("contrast")
    if np.any(lc != 0):
        delta += lc * 0.14 * np.clip(l1, -6, 4)
    cl = v["clarity"] + T("clarity")
    if np.any(cl != 0):
        det = np.clip(l_pre - (pl["ma_c"] * l_pre + pl["mb_c"]), -2.5, 2.5)
        q = base / 3.2
        delta += cl * 0.85 * det * (0.35 + 0.65 * np.exp(-(q * q)))
    tx, sp, nz = v["texture"], v["sharpen"], v["noise"]
    if tx != 0 or sp != 0 or nz != 0:
        det = l_pre - tex
        tame = 1 - 0.6 * ss(0.4, 1.6, np.abs(det))
        delta += tx * 1.1 * np.clip(det, -1, 1) * tame
        delta += sp * 1.3 * np.clip(det, -0.8, 0.8)
        delta -= nz * 0.9 * det * (1 - ss(0.1, 0.5, np.abs(det)))
    c = c * np.exp2(delta, dtype=np.float32)[..., None]
    # --- tone map on luminance, hue preserved, gamut desaturated towards luminance
    yl = luma(c)
    o = tone_lum(yl, v["contrast"], v["whites"], v["blacks"])
    d = np.where((yl > 1e-9)[..., None], c * (o / np.maximum(yl, 1e-9))[..., None], 0)
    mx = d.max(-1)
    t = np.where(mx > 1, np.clip((mx - 1) / np.maximum(mx - o, 1e-6), 0, 1), 0)
    d = d + (o[..., None] - d) * t[..., None]
    # --- color (OkLab)
    lab = oklab(d)
    ll, C, hue = lab[..., 0], np.hypot(lab[..., 1], lab[..., 2]), np.arctan2(lab[..., 2], lab[..., 1])
    if any(v[f"mix_{b}_{s}"] != 0 for b in MIX_BANDS for s in "hsl"):
        w = band_weights(hue)
        dh = sum(w[i] * v[f"mix_{b}_h"] * 0.5 for i, b in enumerate(MIX_BANDS))
        ds = sum(w[i] * v[f"mix_{b}_s"] for i, b in enumerate(MIX_BANDS))
        dl = sum(w[i] * v[f"mix_{b}_l"] * 0.18 for i, b in enumerate(MIX_BANDS))
        cw = np.minimum(C / 0.12, 1)
        hue = hue + dh * cw
        C = C * np.maximum(1 + ds, 0)
        ll = ll + dl * cw * np.sqrt(np.maximum(ll, 0.05))
    vib = v["vibrance"]
    if vib != 0:
        low = 1 - np.clip(C / 0.22, 0, 1)
        if vib > 0:
            q = wrap_angle(hue - SKIN_HUE) / 0.35
            skin = 1 - 0.6 * np.exp(-(q * q))
        else:
            skin = 1.0
        C = C * np.maximum(1 + vib * low * low * skin * 1.2, 0)
    sat = v["saturation"] + T("saturation")
    if np.any(sat != 0):
        C = C * np.maximum(1 + sat, 0)
    la, lb = C * np.cos(hue), C * np.sin(hue)
    if any(abs(x) > 1e-9 for wh3 in wheels for x in wh3):
        m = 0.5 - v["grade_bal"] * 0.25
        width = 0.15 + v["grade_blend"] * 0.5
        ws = 1 - ss(m - width, m + width * 0.25, ll)
        wh = ss(m - width * 0.25, m + width, ll)
        wm = np.maximum(1 - ws - wh, 0)
        for (A, B, LU), wt in zip(wheels, (ws, wm, wh, 1.0)):
            la = la + A * wt
            lb = lb + B * wt
            ll = ll + LU * wt
    d = np.maximum(oklab_inv(np.stack([ll, la, lb], -1)), 0)
    # --- vignette (display linear, LightCraft superellipse; roundness 0, style Color)
    if v["vignette"] != 0:
        yy, xx = np.mgrid[y0:y0 + n, 0:W].astype(np.float32)
        u = (xx + 0.5) / W * 2 - 1
        vv = (yy + 0.5) / H * 2 - 1
        aspect = W / H
        sx = 1 + (aspect - 1) * 0.5
        sy = 1 + (1 / aspect - 1) * 0.5
        ax = np.abs(u * max(sx, 1) / max(sx, sy))
        ay = np.abs(vv * max(sy, 1) / max(sx, sy))
        dist = np.sqrt(ax * ax + ay * ay)
        start = 0.15 + (v["vigmid"] + 1) / 2 * 0.95
        width = 0.05 + (v["vigfeather"] + 1) / 2 * 1.1
        t = ss(start, start + width, dist)
        amt = v["vignette"]
        if amt < 0:
            d = d * (1 + amt * t)[..., None]
        else:
            d = d + (1 - d) * (amt * t * 0.85)[..., None]
    # --- encode, parametric curve
    e = np.power(np.clip(d, 0, 1), 1 / 2.2, dtype=np.float32)
    if any(v[k] != 0 for k in ("curve_hi", "curve_li", "curve_da", "curve_sh")):
        e = np.clip(curve_apply(e, v), 0, 1)
    return np.clip(e, 0, 1)


def curve_apply(e, v):
    """LightCraft's parametric region curve (finish.rs curve_luts), splits at 25 / 50 / 75."""
    regions = ((0.0, 0.25, v["curve_sh"]), (0.25, 0.5, v["curve_da"]), (0.5, 0.75, v["curve_li"]), (0.75, 1.0, v["curve_hi"]))
    dd = np.zeros_like(e)
    for a, b, amt in regions:
        if amt == 0:
            continue
        ctr, half = (a + b) / 2, (b - a) * 0.75 + 0.05
        t = np.clip((e - ctr) / half, -1, 1)
        dd += amt * 0.22 * (0.5 + 0.5 * np.cos(t * math.pi))
    return e + dd * 4 * e * (1 - e)


def is_neutral(p, masks):
    v = params(p or {})
    if any(abs(x) > 1e-6 for x in v.values()):
        return False
    return not [m for m in (masks or []) if any(abs(float(m.get(k, 0) or 0)) > 1e-4 for k in MASK_TERMS)]


def develop(im, p, masks=None, strip=256, threads=4):
    """Bake the sliders (and masks) into a PIL RGB image. The planes and the texture band are computed once
    for the whole image, then the per-pixel pass runs over row strips, a few at a time (numpy lets go of the
    GIL inside its loops, so the strips really run side by side: a 24 MP bake in ~10 s instead of ~28)."""
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image
    masks = [m for m in (masks or []) if any(abs(float(m.get(k, 0) or 0)) > 1e-4 for k in MASK_TERMS)]
    if is_neutral(p, masks):
        return im
    v = params(p or {})
    a8 = np.asarray(im.convert("RGB"), dtype=np.uint8)
    a = a8.astype(np.float32) / 255.0
    H, W = a.shape[:2]
    P = planes(a)
    L = P["L"]
    long_edge = max(H, W)
    full = {k: _resize(P[k], W, H, False) for k in ("ma_b", "mb_b", "ma_c", "mb_c", "dark")}
    sky_full = _resize(P["sky"], W, H, False) if any(m.get("type") == "sky" for m in masks) else None
    tex = tex_plane(L, long_edge)
    wheels = wheel_terms(v)
    wbm = wb_for_sliders(v["temp"] * 100, v["tint"] * 100)
    ev = v["exposure"] * EV_RANGE
    del a
    out = np.empty((H, W, 3), np.uint8)

    def run(y0):
        y1 = min(H, y0 + strip)
        rows = a8[y0:y1]
        lt = None
        if masks:
            lin = LIN8[rows]
            lt = {k: np.zeros((y1 - y0, W), np.float32) for k in MASK_TERMS}
            for m in masks:
                al = mask_alpha(m, H, W, lin, L[y0:y1], ev, sky_full, y0, y1)
                for k in MASK_TERMS:
                    t = float(m.get(k, 0) or 0) / 100.0
                    if abs(t) > 1e-6:
                        lt[k] += al * t
        pl = {k: full[k][y0:y1] for k in full}
        e = finish(rows, L[y0:y1], tex[y0:y1], pl, P["air"], v, wheels, wbm, lt, y0, H, W, long_edge)
        out[y0:y1] = (e * 255 + 0.5).astype(np.uint8)
    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(run, range(0, H, strip)))
    return Image.fromarray(out, "RGB")


# ---------------------------------------------------------------- auto straighten (LightCraft upright.rs level_angle)
def level_angle(gray):
    """The straighten angle (degrees, the sign our straighten() takes) that levels the dominant horizon or
    plumb lines of an 8-bit grayscale image, or None when the lines do not agree (scene texture, not a horizon).
    Line segments from OpenCV's LSD; horizontal ones (within 20 degrees) decide unless vertical ones carry
    twice their length; the length-weighted median deviation, kept only when half the length agrees within 2."""
    import cv2
    h, w = gray.shape[:2]
    k = min(1.0, 1200 / max(h, w))
    if k < 1:
        gray = cv2.resize(gray, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    lines = lsd.detect(gray)[0]
    if lines is None or not len(lines):
        return None
    seg = lines.reshape(-1, 4).astype(float)
    dx, dy = seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]
    ln = np.hypot(dx, dy)
    keep = ln >= 0.02 * max(gray.shape)
    seg, dx, dy, ln = seg[keep], dx[keep], dy[keep], ln[keep]
    if not len(ln):
        return None

    def dev(vertical):
        a = np.degrees(np.arctan2(dx, dy) if vertical else np.arctan2(dy, dx))
        a = np.where(a > 90, a - 180, np.where(a < -90, a + 180, a))
        return -a if vertical else a
    dh, dv = dev(False), dev(True)
    hsel, vsel = np.abs(dh) <= 20, np.abs(dv) <= 20
    lh, lv = ln[hsel].sum(), ln[vsel].sum()
    if lh >= lv * 0.5 and lh > 0:
        d, wt = dh[hsel], ln[hsel]
    else:
        d, wt = dv[vsel], ln[vsel]
    if not len(d):
        return None
    order = np.argsort(d)
    d, wt = d[order], wt[order]
    total = wt.sum()
    med = d[np.searchsorted(np.cumsum(wt), total / 2)]
    agree = wt[np.abs(d - med) <= 2.0].sum()
    if agree < 0.5 * total:
        return None
    # a positive deviation is a line sloping down to the right (y grows downward); our straighten() rotates
    # counter-clockwise for a positive angle, which lifts the right end: the same sign
    return float(round(med, 2))
