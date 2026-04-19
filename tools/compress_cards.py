"""
Compress card/hero background images and update all references.

Tasks:
  1. Compress 3 images not yet in Images/web/:
       - Images/El Salvador/El Tunco and Taquillo/El Tunco Mural.JPEG
       - Images/El Salvador/Santa Ana/Volcano de Santa Ana Horizontal.jpg
       - Images/Index/fitzroy island - main photo.JPG

  2. Update all background-image URL references in:
       - styles.css  (Images/El Salvador/... -> Images/web/El Salvador/...)
       - el-salvador/*.html  (../Images/El Salvador/... -> ../Images/web/El Salvador/...)
       - el-salvador/index.html extra card refs

  3. Replace base64 home-dest-card images in index.html with file refs:
       El Salvador  -> Images/web/dest-cards/el-salvador.jpg
       Albania      -> Images/web/dest-cards/albania.jpg
       Peru         -> Images/web/dest-cards/peru.jpg
       New Zealand  -> Images/web/dest-cards/new-zealand.jpg
"""

import re
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow not installed. Run: pip install Pillow")
    raise

BASE = Path(__file__).resolve().parent.parent

# ── Step 1: Compress missing images ─────────────────────────────────────────

TO_COMPRESS = [
    'Images/El Salvador/El Tunco and Taquillo/El Tunco Mural.JPEG',
    'Images/El Salvador/Santa Ana/Volcano de Santa Ana Horizontal.jpg',
    'Images/Index/fitzroy island - main photo.JPG',
]

def compress(src_path: Path, dst_path: Path):
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as img:
        w, h = img.size
        icc = img.info.get('icc_profile')
        if h > w:
            box = (600, 900)
        elif w > h:
            box = (1200, 800)
        else:
            box = (900, 900)
        img = img.convert('RGB')
        img.thumbnail(box, Image.LANCZOS)
        kwargs = {'format': 'JPEG', 'quality': 85, 'optimize': True}
        if icc:
            kwargs['icc_profile'] = icc
        img.save(dst_path, **kwargs)
    orig_kb = src_path.stat().st_size // 1024
    new_kb = dst_path.stat().st_size // 1024
    return orig_kb, new_kb

print('Step 1: Compressing missing images')
for rel in TO_COMPRESS:
    src = BASE / rel
    # Destination: insert 'web/' after 'Images/'
    rest = Path(rel).relative_to('Images')
    dst = BASE / 'Images' / 'web' / rest.with_suffix('.jpg')
    if dst.exists():
        print(f'  SKIP (exists): {dst.name}')
        continue
    if not src.exists():
        print(f'  MISSING SRC: {rel}')
        continue
    orig_kb, new_kb = compress(src, dst)
    savings = round((1 - new_kb / max(orig_kb, 1)) * 100)
    print(f'  {src.name:<50} {orig_kb} KB -> {new_kb} KB ({savings}% saved)')

# ── Step 2: Update background-image URL refs in CSS and HTML ─────────────────

# Map of old URL fragments -> new URL fragments
# We handle both root-relative (Images/...) and parent-relative (../Images/...)
# and convert all extensions to .jpg

def url_to_web(url: str) -> str:
    """Convert an Images/... or ../Images/... URL to its Images/web/ equivalent."""
    if url.startswith('../Images/'):
        rest = url[len('../Images/'):]
        # Use PurePosixPath to keep forward slashes
        new_rest = str(Path(rest).with_suffix('.jpg')).replace('\\', '/')
        return f'../Images/web/{new_rest}'
    elif url.startswith('Images/'):
        rest = url[len('Images/'):]
        new_rest = str(Path(rest).with_suffix('.jpg')).replace('\\', '/')
        return f'Images/web/{new_rest}'
    return url

def update_bg_urls(text: str) -> str:
    """Replace all background-image URL refs (non-web, non-data, non-http) with web/ versions."""
    def replace_url(m):
        url = m.group(1)
        if (url.startswith('data:') or url.startswith('http') or
                '/web/' in url or 'flagcdn' in url or 'svg' in url.lower()):
            return m.group(0)
        new_url = url_to_web(url)
        if new_url != url:
            return m.group(0).replace(url, new_url)
        return m.group(0)

    # Match url('...') patterns
    return re.sub(r"url\('([^']+)'\)", replace_url, text)

files_to_update = (
    [BASE / 'styles.css'] +
    list((BASE / 'el-salvador').glob('*.html'))
)

print('\nStep 2: Updating background-image URLs')
for path in files_to_update:
    original = path.read_text(encoding='utf-8')
    updated = update_bg_urls(original)
    if updated != original:
        path.write_text(updated, encoding='utf-8')
        count = updated.count('Images/web/')
        print(f'  Updated: {path.relative_to(BASE)} ({count} web/ refs now)')
    else:
        print(f'  No change: {path.relative_to(BASE)}')

# ── Step 3: Replace base64 home-dest-cards in index.html ─────────────────────

print('\nStep 3: Replacing base64 dest-cards in index.html')

DEST_MAP = {
    'El Salvador': "Images/web/dest-cards/el-salvador.jpg",
    'Albania':     "Images/web/dest-cards/albania.jpg",
    'Peru':        "Images/web/dest-cards/peru.jpg",
    'New Zealand': "Images/web/dest-cards/new-zealand.jpg",
}

index_path = BASE / 'index.html'
html = index_path.read_text(encoding='utf-8')

# Pattern: home-dest-card div with base64 background-image
CARD_PAT = re.compile(
    r'(<div class="home-dest-card"[^>]*style="[^"]*background-image:url\()\'data:image/[^\']+\''
    r'(\)[^"]*")[^>]*>.*?'
    r'<div class="dest-info-name">([^<]+)</div>',
    re.DOTALL
)

def replace_b64_card(m):
    dest_name = m.group(3).strip()
    if dest_name not in DEST_MAP:
        return m.group(0)
    file_url = DEST_MAP[dest_name]
    # Replace data:... with file URL
    old = m.group(0)
    new = re.sub(
        r"url\('data:image/[^']+'\)",
        f"url('{file_url}')",
        old,
        count=1
    )
    return new

updated_html = CARD_PAT.sub(replace_b64_card, html)

if updated_html != html:
    index_path.write_text(updated_html, encoding='utf-8')
    saved = len(html) - len(updated_html)
    print(f'  index.html: replaced base64 images, saved ~{saved//1024} KB')
else:
    print('  index.html: no base64 cards found to replace')

print('\nDone.')
