"""Develop engine parity: the editor's WebGL preview (editor.html GLSL_FRAG) against the server's bake
(tools/develop_v2.py) on the same image, sliders and masks. Both must agree pixel for pixel: what Kevin sees
while editing is what gets published.

Needs the photo server (http://127.0.0.1:5003). The image is an sRGB PNG (no ICC profile) so the browser's
decode and numpy read identical numbers; the planes come from /api/planes exactly as the editor fetches them.

    python tools/test_develop_parity.py          # pass = every case under the thresholds
"""
import asyncio
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import develop_v2 as D  # noqa: E402
import photo_editor as pe  # noqa: E402  (brush_alphas: the painted mask the way the import decodes it)

API = "http://127.0.0.1:5003"
REL = ".tmp/photo_editor/parity/test.png"
MEAN_MAX = 0.6      # mean |diff| in 8-bit units
P99_MAX = 6.0       # 99th percentile

CASES = [
    ("light", {"exposure": 25, "contrast": 40, "highlights": -60, "shadows": 50, "whites": 20, "blacks": -20}, []),
    ("colour", {"temp": 30, "tint": -20, "vibrance": 40, "saturation": 20}, []),
    ("presence", {"clarity": 50, "dehaze": 40, "texture": 40, "sharpen": 60, "noise": 40}, []),
    ("mixer+grade+curve", {"mix_b_s": -60, "mix_b_h": 40, "mix_o_l": 30, "mix_g_s": 50, "grade_sh_h": 220, "grade_sh_s": 50,
                           "grade_hi_h": 40, "grade_hi_s": 40, "grade_gl_l": 10, "grade_blend": 20, "grade_bal": -30,
                           "curve_hi": -30, "curve_sh": 25, "curve_da": -10, "curve_li": 15}, []),
    ("vignette+bw", {"vignette": -50, "vigmid": -30, "vigfeather": 20, "saturation": -100, "contrast": 20}, []),
    ("masks", {"exposure": 10},
     [{"type": "sky", "exposure": -60, "saturation": 40, "dehaze": 30},
      {"type": "lum", "lo": 50, "hi": 100, "lf": 10, "hf": 10, "highlights": -50, "clarity": 40},
      {"type": "radial", "x0": 0.5, "y0": 0.6, "x1": 0.75, "y1": 0.8, "feather": 60, "exposure": 50, "temp": 40, "tint": -30, "contrast": 40},
      {"type": "linear", "x0": 0.5, "y0": 0.0, "x1": 0.5, "y1": 0.5, "shadows": 60, "invert": True}]),
    ("colour mask", {},
     [{"type": "color", "lab": None, "refine": 60, "saturation": -80, "exposure": -40}]),
    ("brush mask", {"contrast": 10},
     [{"type": "brush", "png": None, "exposure": 60, "temp": 40, "clarity": 50}]),
    ("everything", {"exposure": -15, "contrast": 30, "highlights": -40, "shadows": 30, "temp": -20, "vibrance": 30,
                    "clarity": 30, "dehaze": 20, "texture": 20, "sharpen": 40, "vignette": -30, "mix_a_s": 40,
                    "grade_mi_h": 120, "grade_mi_s": 30, "curve_hi": -20},
     [{"type": "sky", "exposure": -40}, {"type": "radial", "x0": 0.4, "y0": 0.4, "x1": 0.7, "y1": 0.7, "exposure": 30}]),
]


def test_image():
    """A 900 px sRGB PNG cut from a repo photo, WITHOUT its Display P3 profile: PIL carries the JPEG's ICC
    into a PNG's iCCP chunk, and Chrome then colour-converts on decode, so the texture no longer holds the
    file's numbers (that looked like a red-channel engine bug, 2026-10-14)."""
    out = ROOT / REL
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(ROOT / "Images" / "Greece" / "Meteora" / "IMG_4002.jpg").convert("RGB")
    im.thumbnail((900, 900))
    im.info.pop("icc_profile", None)
    im.save(out, icc_profile=None)
    return out


async def main():
    from playwright.async_api import async_playwright
    img = Image.open(test_image()).convert("RGB")
    a = np.asarray(img, dtype=np.float32) / 255.0
    # the colour mask's sample: a sky pixel, through the same tonemap_for_select as the shader
    lin = np.power(a[40, 300], 2.2)
    sky_lab = D.oklab((lin / (1 + lin)).astype(np.float32)).tolist()
    # a painted mask: a soft blob, as the editor's brush canvas would send it (alpha in a PNG)
    import base64
    import io
    yy, xx = np.mgrid[0:img.height, 0:img.width]
    blob = np.clip(1.5 - np.hypot(xx - img.width * 0.55, yy - img.height * 0.6) / 140.0, 0, 1)
    pa = Image.fromarray(np.dstack([np.full(blob.shape, 255, np.uint8)] * 3 + [(blob * 255).astype(np.uint8)]), "RGBA")
    buf = io.BytesIO()
    pa.save(buf, "PNG")
    brush_png = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    for _, _, masks in CASES:
        for m in masks:
            if m.get("type") == "color":
                m["lab"] = sky_lab
            if m.get("type") == "brush":
                m["png"] = brush_png
    worst = 0
    fails = []
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        pg = await b.new_page(viewport={"width": 1400, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
        await pg.goto(API + "/site/editor.html", wait_until="domcontentloaded")
        await pg.wait_for_timeout(1500)
        src = API + "/site/" + REL
        planes = API + "/api/planes?site=" + REL + "&s=2000"
        for name, dev, masks in CASES:
            r = await pg.evaluate("([s, p, d, m]) => window.__devTest(s, p, d, m)", [src, planes, dev, masks])
            px = np.array(r["px"], dtype=np.uint8).reshape(r["h"], r["w"], 4)[::-1, :, :3]    # GL rows run bottom-up
            bake = np.asarray(D.develop(img, dev, pe.brush_alphas(masks, img.size)), dtype=np.int16)
            diff = np.abs(px.astype(np.int16) - bake)
            mean, p99, mx = float(diff.mean()), float(np.percentile(diff, 99)), int(diff.max())
            ok = mean <= MEAN_MAX and p99 <= P99_MAX
            worst = max(worst, mean)
            print("%-18s mean %.3f  p99 %.1f  max %3d  %s" % (name, mean, p99, mx, "ok" if ok else "FAIL"))
            if not ok:
                fails.append(name)
                out = ROOT / ".tmp" / "photo_editor" / "parity"
                Image.fromarray(px).save(out / f"{name}-preview.png")
                Image.fromarray(bake.astype(np.uint8)).save(out / f"{name}-bake.png")
                Image.fromarray(np.clip(diff * 8, 0, 255).astype(np.uint8)).save(out / f"{name}-diff.png")
        await b.close()
    print("worst mean", round(worst, 3), "| page errors:", errs[:3])
    print("PASS" if not fails else "FAIL: " + ", ".join(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
