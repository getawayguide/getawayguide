"""Audit background-image file references not yet using Images/web/."""
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
files_to_check = (
    list(BASE.glob('*.html')) +
    list((BASE / 'el-salvador').glob('*.html')) +
    [BASE / 'styles.css']
)
pat = re.compile(r"url\('([^']+)'\)")

refs = {}
for f in files_to_check:
    if not f.exists():
        continue
    text = f.read_text(encoding='utf-8', errors='ignore')
    for m in pat.finditer(text):
        url = m.group(1)
        if url.startswith('data:') or url.startswith('http') or '/web/' in url:
            continue
        # Resolve to path relative to project root
        if 'el-salvador' in str(f) and url.startswith('../'):
            rel = url[3:]   # strip ../
        else:
            rel = url
        key = rel.replace('\\', '/')
        if key not in refs:
            refs[key] = []
        refs[key].append(str(f.relative_to(BASE)))

print('Background-image file refs not yet using web/:')
for k, sources in sorted(refs.items()):
    abs_src = BASE / k
    try:
        rel_to_img = Path(k).relative_to('Images')
        web_jpg = BASE / 'Images' / 'web' / rel_to_img.with_suffix('.jpg')
        web_orig = BASE / 'Images' / 'web' / rel_to_img
        exists_web = web_jpg.exists() or web_orig.exists()
    except Exception:
        exists_web = False
    src_exists = abs_src.exists()
    if exists_web:
        status = 'WEB-OK'
    elif not src_exists:
        status = 'MISSING-SRC'
    else:
        status = 'NEEDS-COMPRESS'
    print(f'  [{status}] {k}')
    for fname in sources:
        print(f'    <- {fname}')
