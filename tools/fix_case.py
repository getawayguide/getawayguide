"""
Fix case-sensitivity bugs in image src paths across all article HTML pages.

On Windows, Path.exists() is case-insensitive, so the revert script may have
written paths like hectors-tour-2.JPG when the real file is hectors-tour-2.jpg.
GitHub Pages (Linux) is case-sensitive and will 404 on the wrong case.

This script:
1. Scans all src="../Images/..." refs in every country's article pages (*/*.html)
2. For each, does a case-sensitive lookup of the real filename on disk
3. Rewrites the path with the exact on-disk filename and extension
"""

import re
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# Cache: dir -> {lower_name: real_name}
_dir_cache = {}

def real_filename(directory: Path, name: str) -> str | None:
    """Return the real on-disk filename (preserving case) for a given name."""
    key = str(directory)
    if key not in _dir_cache:
        try:
            _dir_cache[key] = {e.lower(): e for e in os.listdir(directory)}
        except FileNotFoundError:
            _dir_cache[key] = {}
    return _dir_cache[key].get(name.lower())


def fix_src(src: str) -> str:
    """Given a src like ../Images/El Salvador/X/Y.JPG, return corrected case."""
    if not src.startswith('../Images/'):
        return src
    rel = src[3:]  # strip ../   → Images/El Salvador/X/Y.JPG
    path = BASE / rel
    directory = path.parent
    name = path.name
    real = real_filename(directory, name)
    if real is None:
        print(f'  MISSING: {src}')
        return src
    if real != name:
        corrected = f'../{rel[:-len(name)]}{real}'
        print(f'  FIXED: {name!r} -> {real!r}')
        return corrected
    return src


pages = list(BASE.glob('*/*.html'))   # all country/article pages
SRC_PAT = re.compile(r'src="(\.\./Images/[^"]+)"')

total_fixed = 0
for page in sorted(pages):
    html = page.read_text(encoding='utf-8')
    original = html

    def replace_src(m):
        fixed = fix_src(m.group(1))
        return f'src="{fixed}"'

    html = SRC_PAT.sub(replace_src, html)
    if html != original:
        page.write_text(html, encoding='utf-8')
        n = sum(1 for a, b in zip(
            SRC_PAT.findall(original), SRC_PAT.findall(html)) if a != b)
        print(f'  Saved: {page.name}')
        total_fixed += 1

print(f'\nDone. {total_fixed} pages updated.')
