"""Fix case-sensitivity in background:url('../Images/...') paths too."""
import re, os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
_dir_cache = {}

def real_filename(directory, name):
    key = str(directory)
    if key not in _dir_cache:
        try:
            _dir_cache[key] = {e.lower(): e for e in os.listdir(directory)}
        except FileNotFoundError:
            _dir_cache[key] = {}
    return _dir_cache[key].get(name.lower())

def fix_url(url):
    if not url.startswith('../Images/'):
        return url
    rel = url[3:]
    path = BASE / rel
    real = real_filename(path.parent, path.name)
    if real is None:
        print(f'  MISSING: {url}')
        return url
    if real != path.name:
        corrected = '../' + str(path.parent.relative_to(BASE)).replace('\\','/') + '/' + real
        print(f'  FIXED: {path.name!r} -> {real!r}')
        return corrected
    return url

BG_PAT = re.compile(r"url\('(\.\./Images/[^']+)'\)")
pages = list((BASE / 'el-salvador').glob('*.html'))
for page in sorted(pages):
    html = page.read_text(encoding='utf-8')
    original = html
    def rep(m):
        return f"url('{fix_url(m.group(1))}')"
    html = BG_PAT.sub(rep, html)
    if html != original:
        page.write_text(html, encoding='utf-8')
        print(f'  Saved: {page.name}')

print('Done.')
