"""
Wrap article body <img src="../Images/web/El Salvador/..."> tags in <picture>
elements so that:
  - Desktop (min-width 769px): loads compressed Images/web/ version
  - Mobile (default fallback): loads original full-quality Images/El Salvador/ file

Hero images (src="../Images/El Salvador/...") are left untouched — they already
load originals on all devices.

Safe to re-run: skips imgs already inside <picture>.
"""

import re, os
from pathlib import Path

BASE      = Path(__file__).resolve().parent.parent
PAGES_DIR = BASE / 'el-salvador'
SRC_DIR   = BASE / 'Images' / 'El Salvador'

PAGES = [
    'santa-ana.html',
    'el-tunco.html',
    'ruta-de-las-flores.html',
    '7-waterfalls-hike.html',
    'el-salvador-itinerary.html',
    'top-10-el-salvador.html',
]

_dir_cache = {}
def real_orig(directory, stem):
    key = str(directory)
    if key not in _dir_cache:
        try: _dir_cache[key] = {Path(e).stem.lower(): e for e in os.listdir(directory)}
        except: _dir_cache[key] = {}
    return _dir_cache[key].get(stem.lower())

# Match <img src="../Images/web/El Salvador/..." ...>
# Capture the full tag so we can replace it
IMG_PAT = re.compile(
    r'(?<!<picture>)'           # not already wrapped
    r'(<img\s[^>]*src="(\.\./Images/web/El Salvador/([^"]+))"[^>]*>)',
    re.DOTALL
)

def wrap_img(m, html, pos):
    """Return replacement string, or None to skip."""
    full_tag  = m.group(1)
    web_src   = m.group(2)   # ../Images/web/El Salvador/X/Y.jpg
    rel_part  = m.group(3)   # X/Y.jpg

    # Check if already inside <picture>
    before = html[:pos]
    if before.rfind('<picture') > before.rfind('</picture'):
        return None  # already wrapped

    # Find original file
    rel_path  = Path(rel_part)              # X/Y.jpg
    orig_dir  = SRC_DIR / rel_path.parent  # Images/El Salvador/X/
    stem      = rel_path.stem
    orig_name = real_orig(orig_dir, stem)
    if not orig_name:
        print(f'  MISSING original: {rel_part}')
        return None

    orig_src = f'../Images/El Salvador/{rel_path.parent}/{orig_name}'.replace('\\', '/')

    # Change img src to original (mobile default), add source for desktop
    new_img = full_tag.replace(f'src="{web_src}"', f'src="{orig_src}"', 1)
    return (
        f'<picture>'
        f'<source media="(min-width:769px)" srcset="{web_src}">'
        f'{new_img}'
        f'</picture>'
    )

for page in PAGES:
    path = PAGES_DIR / page
    html = path.read_text(encoding='utf-8')
    original = html

    result = []
    prev = 0
    wrapped = 0
    skipped = 0

    for m in IMG_PAT.finditer(html):
        replacement = wrap_img(m, html, m.start())
        if replacement is None:
            skipped += 1
            result.append(html[prev:m.end()])
            prev = m.end()
        else:
            result.append(html[prev:m.start()])
            result.append(replacement)
            prev = m.end()
            wrapped += 1

    result.append(html[prev:])
    html = ''.join(result)

    if html != original:
        path.write_text(html, encoding='utf-8')
        print(f'  {page}: {wrapped} wrapped, {skipped} skipped')
    else:
        print(f'  {page}: no changes')

print('\nDone.')
