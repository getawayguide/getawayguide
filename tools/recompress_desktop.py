"""
Generate/refresh desktop-compressed versions of article body images for ALL countries.

  Portrait  -> 600x900     Landscape -> 1200x800     Square -> 900x900

Two passes:
  A. Regenerate every existing Images/web/<Country>/**.jpg from its original
     in Images/<Country>/ (same behavior the old El Salvador-only script had).
  B. Scan every article page (*/*.html with an .article-body) for body images
     that reference an original (src="../Images/<Country>/...") and create the
     missing Images/web/<Country>/... .jpg so add_picture_mobile.py can wrap it.

Heroes are safe: they live outside .article-body and are never scanned.
Originals in Images/<Country>/ are NEVER modified (read-only source).
Site-asset folders (Index, Bio, dest-cards, web) are excluded.

Usage:
    py tools/recompress_desktop.py             # both passes
    py tools/recompress_desktop.py --new-only  # only create missing web files (fast)
    py tools/recompress_desktop.py --dry-run   # report what would be done
"""

import os
import re
import sys
from pathlib import Path
from PIL import Image

BASE    = Path(__file__).resolve().parent.parent
IMAGES  = BASE / 'Images'
WEB     = IMAGES / 'web'
EXCLUDE = {'web', 'Index', 'Bio', 'dest-cards'}   # site assets, not article images

DRY = '--dry-run' in sys.argv
NEW_ONLY = '--new-only' in sys.argv

_dir_cache = {}
def real_name(directory, name):
    key = str(directory)
    if key not in _dir_cache:
        try: _dir_cache[key] = {e.lower(): e for e in os.listdir(directory)}
        except OSError: _dir_cache[key] = {}
    return _dir_cache[key].get(name.lower())

def find_original(orig_dir, stem):
    """Case-insensitive original lookup by stem, any common extension."""
    for ext in ('.JPEG', '.JPG', '.jpeg', '.jpg', '.png', '.PNG', '.webp', '.WEBP'):
        real = real_name(orig_dir, stem + ext)
        if real:
            return orig_dir / real
    return None

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


countries = [d.name for d in IMAGES.iterdir() if d.is_dir() and d.name not in EXCLUDE]
print('Countries:', ', '.join(sorted(countries)) or '(none)')

total_orig = total_new = processed = created = 0

# ---- Pass A: regenerate existing web files ----
print('\n== Pass A: regenerate existing Images/web/<Country>/ files ==' +
      (' (skipped: --new-only)' if NEW_ONLY else ''))
for country in ([] if NEW_ONLY else sorted(countries)):
    web_dir = WEB / country
    if not web_dir.is_dir():
        continue
    for web_file in sorted(web_dir.rglob('*.jpg')):
        # density variants (-1x/-2x/-3x, -mob-*) belong to the HQ srcset pipeline
        if re.search(r'-(?:mob-)?[123]x$', web_file.stem):
            continue
        rel = web_file.relative_to(web_dir)
        orig = find_original(IMAGES / country / rel.parent, web_file.stem)
        if not orig:
            print(f'  MISSING original: {country}/{rel}')
            continue
        if DRY:
            print(f'  would regen: {country}/{rel}')
            continue
        orig_kb, new_kb = compress(orig, web_file)
        total_orig += orig_kb; total_new += new_kb; processed += 1
        print(f'  {country}/{str(rel):<60} {orig_kb:>6}KB -> {new_kb:>5}KB')

# ---- Pass B: create web versions for new body images ----
print('\n== Pass B: create missing web versions referenced by article bodies ==')
IMG_REF = re.compile(r'<img[^>]+src="\.\./Images/(?!web/)([^"/]+)/([^"]+)"')
for page in sorted(BASE.glob('*/*.html')):
    html = page.read_text(encoding='utf-8')
    i = html.find('class="article-body"')
    if i < 0:
        continue
    body = html[i:]
    for m in IMG_REF.finditer(body):
        # skip images already wrapped in <picture> (existing two-version setup)
        before = body[:m.start()]
        if before.rfind('<picture') > before.rfind('</picture'):
            continue
        country, relfile = m.group(1), m.group(2)
        if country in EXCLUDE:
            continue
        rel_path = Path(relfile.replace('%20', ' '))
        web_file = WEB / country / rel_path.parent / (rel_path.stem + '.jpg')
        if web_file.exists():
            continue   # pass A already refreshed it
        orig = find_original(IMAGES / country / rel_path.parent, rel_path.stem)
        if not orig:
            print(f'  MISSING original: {country}/{rel_path} (referenced by {page.name})')
            continue
        if DRY:
            print(f'  would create: web/{country}/{rel_path.parent}/{rel_path.stem}.jpg')
            continue
        orig_kb, new_kb = compress(orig, web_file)
        total_orig += orig_kb; total_new += new_kb; created += 1
        print(f'  NEW {country}/{str(rel_path):<56} {orig_kb:>6}KB -> {new_kb:>5}KB')

if not DRY:
    print(f'\n{processed} regenerated, {created} created')
    if total_orig:
        print(f'Total: {total_orig}KB -> {total_new}KB ({round((1-total_new/max(total_orig,1))*100)}% saved)')
print('Done.')
