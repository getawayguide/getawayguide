"""Fix Windows backslashes in url() CSS paths that were written incorrectly."""
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

files = [BASE / 'styles.css'] + list((BASE / 'el-salvador').glob('*.html'))

URL_PAT = re.compile(r"url\('([^']+)'\)")

for f in files:
    text = f.read_text(encoding='utf-8')
    original = text

    def fix_url(m):
        url = m.group(1)
        if 'Images/web/' in url and '\\' in url:
            fixed = url.replace('\\', '/')
            return f"url('{fixed}')"
        return m.group(0)

    updated = URL_PAT.sub(fix_url, text)
    if updated != original:
        f.write_text(updated, encoding='utf-8')
        print(f'Fixed: {f.relative_to(BASE)}')
    else:
        print(f'OK:    {f.relative_to(BASE)}')
