"""
Recompress article Images/web/El Salvador/ files at desktop dimensions:
  Portrait  → 600×900
  Landscape → 1200×800
  Square    → 900×900

Works directly from the web/ directory — no HTML changes.
Heroes and other originals in Images/El Salvador/ are left untouched.
"""

import os
from pathlib import Path
from PIL import Image

BASE    = Path(__file__).resolve().parent.parent
WEB_DIR = BASE / 'Images' / 'web' / 'El Salvador'
SRC_DIR = BASE / 'Images' / 'El Salvador'

_dir_cache = {}
def real_name(directory, name):
    key = str(directory)
    if key not in _dir_cache:
        try: _dir_cache[key] = {e.lower(): e for e in os.listdir(directory)}
        except: _dir_cache[key] = {}
    return _dir_cache[key].get(name.lower())

def compress(src_path, dst_path):
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as img:
        w, h = img.size
        icc = img.info.get('icc_profile')
        if h > w:   box = (600, 900)
        elif w > h: box = (1200, 800)
        else:       box = (900, 900)
        img = img.convert('RGB')
        img.thumbnail(box, Image.LANCZOS)
        kw = {'format': 'JPEG', 'quality': 90, 'optimize': True}
        if icc: kw['icc_profile'] = icc
        img.save(dst_path, **kw)
    return src_path.stat().st_size // 1024, dst_path.stat().st_size // 1024

total_orig = total_new = 0
processed = 0

for web_file in sorted(WEB_DIR.rglob('*.jpg')):
    rel = web_file.relative_to(WEB_DIR)          # e.g. Santa Ana/X.jpg
    orig_dir = SRC_DIR / rel.parent               # Images/El Salvador/Santa Ana/
    stem = web_file.stem

    # Find original (any extension)
    real = real_name(orig_dir, stem + '.jpg')
    for ext in ('.JPEG', '.JPG', '.jpeg', '.jpg', '.png', '.PNG'):
        candidate = orig_dir / (stem + ext)
        if candidate.exists():
            real = stem + ext
            break

    if not real:
        print(f'  MISSING original: {rel}')
        continue

    orig_path = orig_dir / real
    orig_kb, new_kb = compress(orig_path, web_file)
    total_orig += orig_kb
    total_new  += new_kb
    ratio = round((1 - new_kb / max(orig_kb,1)) * 100)
    print(f'  {stem:<55} {orig_kb:>6}KB -> {new_kb:>5}KB ({ratio}%)')
    processed += 1

print(f'\n{processed} images processed')
print(f'Total: {total_orig}KB -> {total_new}KB ({round((1-total_new/max(total_orig,1))*100)}% saved)')
print('Done.')
