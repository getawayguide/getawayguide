"""
Revert all article image references from Images/web/ back to originals.

- Article <img src> tags: ../Images/web/El Salvador/X.jpg -> ../Images/El Salvador/X.ORIG_EXT
- CSS/HTML background-image: Images/web/El Salvador/X.jpg -> Images/El Salvador/X.ORIG_EXT
- Hero background-image in el-salvador/*.html: same
- styles.css: same
- index.html: keep dest-cards file refs (they were pre-existing), only revert El Salvador paths

Does NOT touch:
- Images/web/dest-cards/ (pre-existing, used by index.html cards)
- Images/web/map-card-*.png (pre-existing UI assets)
- Images/web/Index/ (hero image - was sharp, keeping)
"""

import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# Build a lookup: relative path with .jpg -> original path with original extension
# e.g. "El Salvador/Santa Ana/Santa Ana Cathedral.jpg" -> "El Salvador/Santa Ana/Santa Ana Cathedral.JPEG"

IMAGES_DIR = BASE / 'Images'
WEB_EL_DIR = BASE / 'Images' / 'web' / 'El Salvador'

# Map: lowercase stem -> original Path (under Images/El Salvador/)
# We walk Images/web/El Salvador/ to find what was compressed
orig_map = {}  # "El Salvador/sub/file.jpg" (lowercase) -> original relative path string

for web_file in WEB_EL_DIR.rglob('*'):
    if web_file.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
        continue
    # Corresponding original dir
    rel_from_web = web_file.relative_to(BASE / 'Images' / 'web')  # e.g. El Salvador/Santa Ana/X.jpg
    orig_dir = IMAGES_DIR / rel_from_web.parent
    stem = web_file.stem
    # Find original file (any extension)
    found = None
    for ext in ('.JPEG', '.JPG', '.jpeg', '.jpg', '.png', '.PNG'):
        candidate = orig_dir / (stem + ext)
        if candidate.exists():
            found = candidate
            break
    if found:
        # Key: the web-relative path with forward slashes, lowercased
        key = str(rel_from_web).replace('\\', '/').lower()
        val = str(found.relative_to(IMAGES_DIR)).replace('\\', '/')
        orig_map[key] = val


def revert_url(url: str) -> str:
    """Given a web/ URL, return the original Images/ URL."""
    # Normalise to get the El Salvador/... part
    if url.startswith('../Images/web/'):
        rel_from_web = url[len('../Images/web/'):]
        prefix = '../Images/'
    elif url.startswith('Images/web/'):
        rel_from_web = url[len('Images/web/'):]
        prefix = 'Images/'
    else:
        return url  # not a web/ url

    key = rel_from_web.replace('\\', '/').lower()
    if key in orig_map:
        return prefix + orig_map[key]
    # Fallback: try stripping El Salvador/ prefix
    return url


def revert_text(text: str) -> str:
    """Replace all Images/web/El Salvador/... refs with original paths."""
    # Match URLs - El Salvador may be followed by / or \ (Windows path bug)
    # Stop at quote or closing paren
    pat = re.compile(r'(\.\./Images/web/El Salvador[/\\][^"\')\n]+|Images/web/El Salvador[/\\][^"\')\n]+)')

    def replace(m):
        url = m.group(1).strip()
        # Normalise backslashes to forward slashes before looking up
        url_fwd = url.replace('\\', '/')
        result = revert_url(url_fwd)
        return result

    return pat.sub(replace, text)


# ── Process files ──────────────────────────────────────────────────────────

files_to_process = (
    list((BASE / 'el-salvador').glob('*.html')) +
    [BASE / 'styles.css']
)

print('Reverting Images/web/El Salvador/ references to originals...\n')
for f in files_to_process:
    original = f.read_text(encoding='utf-8')
    updated  = revert_text(original)
    if updated != original:
        f.write_text(updated, encoding='utf-8')
        # Count changes
        n_before = original.count('Images/web/El Salvador/')
        n_after  = updated.count('Images/web/El Salvador/')
        print(f'  {f.relative_to(BASE)}: reverted {n_before - n_after} refs')
    else:
        print(f'  {f.relative_to(BASE)}: no changes')

print('\nDone.')
