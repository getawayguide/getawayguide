"""
Detect blurry images in Images/web/ by comparing Laplacian variance
against their originals in Images/.

Laplacian variance measures edge sharpness:
  - High score = sharp / lots of detail
  - Low score  = blurry / flat

A web image is flagged as blurry when its score is below BLUR_THRESHOLD.
The original is shown for comparison to confirm whether the source itself
is the problem or if the compression degraded it.

Output: sorted list of blurry compressed images with their blur scores.
"""

import cv2
import numpy as np
from pathlib import Path

BASE        = Path(__file__).resolve().parent.parent
WEB_DIR     = BASE / 'Images' / 'web'
IMAGES_DIR  = BASE / 'Images'

# Tune this threshold — lower = fewer flags, higher = more flags
BLUR_THRESHOLD = 80   # typical sharp thumbnail is 100-500+

def laplacian_variance(path: Path) -> float:
    """Return Laplacian variance (sharpness score) for an image file."""
    # Use imdecode + raw bytes to handle non-ASCII filenames on Windows
    raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return -1.0
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def find_original(web_path: Path) -> Path | None:
    """Find the corresponding original in Images/ (any extension)."""
    try:
        rel = web_path.relative_to(WEB_DIR)
    except ValueError:
        return None
    # rel might be e.g. El Salvador/Santa Ana/Santa Ana Cathedral.jpg
    # Original lives at Images/El Salvador/Santa Ana/Santa Ana Cathedral.JPEG (or .jpg etc.)
    orig_dir = IMAGES_DIR / rel.parent
    stem     = rel.stem
    for ext in ('.JPEG', '.JPG', '.jpeg', '.jpg', '.png', '.PNG'):
        candidate = orig_dir / (stem + ext)
        if candidate.exists():
            return candidate
    return None


# ── Scan all web images ───────────────────────────────────────────────────────

results = []

for web_img in sorted(WEB_DIR.rglob('*')):
    if web_img.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
        continue
    # Skip map cards and dest-cards (UI assets, not photos)
    rel = web_img.relative_to(WEB_DIR)
    if rel.parts[0] in ('dest-cards',) or web_img.name in ('map-card-option-A.png',):
        continue

    score_web  = laplacian_variance(web_img)
    orig       = find_original(web_img)
    score_orig = laplacian_variance(orig) if orig else None

    results.append((score_web, web_img, orig, score_orig))

# Sort blurry first
results.sort(key=lambda x: x[0])

# ── Report ────────────────────────────────────────────────────────────────────

blurry  = [(s, p, o, so) for s, p, o, so in results if s < BLUR_THRESHOLD]
sharp   = [(s, p, o, so) for s, p, o, so in results if s >= BLUR_THRESHOLD]

print(f'Scanned {len(results)} web images  |  threshold: {BLUR_THRESHOLD}')
print(f'Blurry: {len(blurry)}   Sharp: {len(sharp)}')
print()

if blurry:
    print(f'{"BLURRY IMAGES":-<72}')
    print(f'  {"Score":>6}  {"Orig score":>10}  File')
    print(f'  {"------":>6}  {"----------":>10}  ----')
    for score, web_img, orig, score_orig in blurry:
        rel = web_img.relative_to(WEB_DIR)
        orig_str = f'{score_orig:8.1f}' if score_orig is not None else '       ?'
        orig_note = ''
        if score_orig is not None and score_orig < BLUR_THRESHOLD:
            orig_note = ' <-source also blurry'
        elif score_orig is not None and score_orig > score * 3:
            orig_note = ' <-orig much sharper'
        print(f'  {score:6.1f}  {orig_str:>10}  {rel}{orig_note}')
else:
    print('No blurry images detected at this threshold.')

print()
print(f'{"ALL SCORES (asc)":-<72}')
print(f'  {"Score":>6}  File')
for score, web_img, orig, score_orig in results:
    rel = web_img.relative_to(WEB_DIR)
    flag = ' ***' if score < BLUR_THRESHOLD else ''
    print(f'  {score:6.1f}  {rel}{flag}')
