"""
Re-compress article images at quality=92 (vs 85 before) to reduce JPEG
artifacts, then restore ../Images/web/ src paths in all article HTML pages.

Portrait  → 800×1200  (was 600×900)
Landscape → 1400×933  (was 1200×800)
Square    → 1000×1000 (was 900×900)

Higher dimensions + higher quality = sharp images without browser-downscaling grain.
"""

import re, os
from pathlib import Path
from PIL import Image

BASE      = Path(__file__).resolve().parent.parent
PAGES_DIR = BASE / 'el-salvador'
WEB_DIR   = BASE / 'Images' / 'web'

PAGES = [
    'santa-ana.html',
    'el-tunco.html',
    'ruta-de-las-flores.html',
    '7-waterfalls-hike.html',
    'el-salvador-itinerary.html',
    'top-10-el-salvador.html',
]

SRC_PAT = re.compile(r'src="(\.\./Images/El Salvador/[^"]+)"')

# ── Collect unique src paths ───────────────────────────────────────────────

srcs = set()
for page in PAGES:
    html = (PAGES_DIR / page).read_text(encoding='utf-8')
    for m in SRC_PAT.finditer(html):
        srcs.add(m.group(1))

print(f'Found {len(srcs)} unique article image references\n')

# ── Re-compress each ───────────────────────────────────────────────────────

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
        if h > w:   box = (800, 1200)
        elif w > h: box = (1400, 933)
        else:       box = (1000, 1000)
        img = img.convert('RGB')
        img.thumbnail(box, Image.LANCZOS)
        kw = {'format': 'JPEG', 'quality': 92, 'optimize': True}
        if icc: kw['icc_profile'] = icc
        img.save(dst_path, **kw)
    return src_path.stat().st_size // 1024, dst_path.stat().st_size // 1024

total_orig = total_new = 0
for src_rel in sorted(srcs):
    # src_rel like ../Images/El Salvador/X/Y.JPEG
    orig_path = BASE / src_rel[3:]  # strip ../
    # Web destination: Images/web/El Salvador/X/Y.jpg
    rest = Path(src_rel[3:]).relative_to('Images')  # El Salvador/X/Y.JPEG
    dst_path = WEB_DIR / rest.with_suffix('.jpg')

    if not orig_path.exists():
        # Try case-insensitive lookup
        real = real_name(orig_path.parent, orig_path.name)
        if real:
            orig_path = orig_path.parent / real
        else:
            print(f'  MISSING: {src_rel}')
            continue

    orig_kb, new_kb = compress(orig_path, dst_path)
    total_orig += orig_kb
    total_new  += new_kb
    ratio = round((1 - new_kb / max(orig_kb,1)) * 100)
    print(f'  {orig_path.name:<55} {orig_kb:>6}KB -> {new_kb:>5}KB ({ratio}%)')

print(f'\nTotal: {total_orig}KB -> {total_new}KB ({round((1-total_new/max(total_orig,1))*100)}% saved)')

# ── Update HTML src to use web/ paths ──────────────────────────────────────

print('\nUpdating HTML src attributes to Images/web/ ...')

def src_to_web(src):
    # ../Images/El Salvador/X/Y.EXT -> ../Images/web/El Salvador/X/Y.jpg
    rest = src[len('../Images/'):]
    new_rest = str(Path(rest).with_suffix('.jpg')).replace('\\', '/')
    return f'../Images/web/{new_rest}'

for page in PAGES:
    path = PAGES_DIR / page
    html = path.read_text(encoding='utf-8')
    original = html

    def replace_src(m):
        return f'src="{src_to_web(m.group(1))}"'

    html = SRC_PAT.sub(replace_src, html)
    if html != original:
        path.write_text(html, encoding='utf-8')
        n = html.count('Images/web/')
        print(f'  {page}: {n} web/ refs')
    else:
        print(f'  {page}: no changes')

print('\nDone.')
