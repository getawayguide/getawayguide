"""
Wrap article body images in <picture> elements for ALL countries so that:
  - Desktop (min-width 769px): loads compressed Images/web/<Country>/ version
  - Mobile (default fallback): loads original full-quality Images/<Country>/ file

Handles both starting states inside .article-body:
  1. <img src="../Images/web/<Country>/X.jpg">  (legacy: web src) ->
     swaps src to the original and adds a desktop <source> for the web file.
  2. <img src="../Images/<Country>/X.JPEG">     (editor inserts originals) ->
     keeps the original as mobile src and adds a desktop <source> for the
     matching Images/web/ file (run tools/recompress_desktop.py first to
     create it).

Heroes are safe: only images inside .article-body are touched.
Safe to re-run: images already inside <picture> are skipped.
"""

import os
import re
from pathlib import Path

BASE    = Path(__file__).resolve().parent.parent
IMAGES  = BASE / 'Images'
WEB     = IMAGES / 'web'
EXCLUDE = {'web', 'Index', 'Bio', 'dest-cards'}

_dir_cache = {}
def real_name(directory, name):
    key = str(directory)
    if key not in _dir_cache:
        try: _dir_cache[key] = {e.lower(): e for e in os.listdir(directory)}
        except OSError: _dir_cache[key] = {}
    return _dir_cache[key].get(name.lower())

def find_original(orig_dir, stem):
    for ext in ('.JPEG', '.JPG', '.jpeg', '.jpg', '.png', '.PNG', '.webp', '.WEBP'):
        real = real_name(orig_dir, stem + ext)
        if real:
            return real
    return None

# Any body <img> referencing ../Images/ (web or original)
IMG_PAT = re.compile(r'<img\s[^>]*src="(\.\./Images/([^"]+))"[^>]*>', re.DOTALL)

def inside_picture(html, pos):
    before = html[:pos]
    return before.rfind('<picture') > before.rfind('</picture')

def wrap(tag, web_src, orig_src):
    new_img = tag.replace(f'src="{web_src}"', f'src="{orig_src}"', 1) if web_src in tag else tag
    return (f'<picture>'
            f'<source media="(min-width:769px)" srcset="{web_src}">'
            f'{new_img}'
            f'</picture>')

total_pages = 0
for page in sorted(BASE.glob('*/*.html')):
    html = page.read_text(encoding='utf-8')
    body_start = html.find('class="article-body"')
    if body_start < 0:
        continue

    original = html
    wrapped = skipped = 0
    result, prev = [], 0

    for m in IMG_PAT.finditer(html):
        if m.start() < body_start or inside_picture(html, m.start()):
            continue
        full_tag, src, rel = m.group(0), m.group(1), m.group(2)
        rel_fs = rel.replace('%20', ' ')
        parts = Path(rel_fs)

        if parts.parts[0] == 'web':
            # Case 1: src points at the web version -> find original for mobile
            country = parts.parts[1] if len(parts.parts) > 1 else ''
            sub = Path(*parts.parts[2:])
            orig_dir = IMAGES / country / sub.parent
            orig = find_original(orig_dir, sub.stem)
            if country in EXCLUDE or not orig:
                if country not in EXCLUDE:
                    print(f'  {page.parent.name}/{page.name}: MISSING original for {rel}')
                skipped += 1
                continue
            orig_src = f'../Images/{country}/{sub.parent}/{orig}'.replace('\\', '/').replace('/./', '/')
            replacement = wrap(full_tag, src, orig_src)
        else:
            # Case 2: src is the original -> desktop source is the web jpg
            country = parts.parts[0]
            if country in EXCLUDE:
                continue
            sub = Path(*parts.parts[1:])
            web_file = WEB / country / sub.parent / (sub.stem + '.jpg')
            if not web_file.exists():
                print(f'  {page.parent.name}/{page.name}: no web version yet for {rel}'
                      ' - run recompress_desktop.py first')
                skipped += 1
                continue
            web_src = f'../Images/web/{country}/{sub.parent}/{sub.stem}.jpg'.replace('\\', '/').replace('/./', '/')
            replacement = f'<picture><source media="(min-width:769px)" srcset="{web_src}">{full_tag}</picture>'

        result.append(html[prev:m.start()])
        result.append(replacement)
        prev = m.end()
        wrapped += 1

    result.append(html[prev:])
    html = ''.join(result)

    if html != original:
        page.write_text(html, encoding='utf-8')
        print(f'  {page.parent.name}/{page.name}: {wrapped} wrapped, {skipped} skipped')
        total_pages += 1

print(f'\n{total_pages} page(s) updated. Done.')
