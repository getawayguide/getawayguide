"""
Compress article images and update HTML references.

- Portrait images (taller than wide) → 600×900 max, saved to Images/web/
- Landscape images (wider than tall) → 1200×800 max, saved to Images/web/
- Square images → 900×900 max, saved to Images/web/
- Preserves ICC profiles
- Does NOT modify original files

Pages processed: 6 El Salvador article pages
Output folder:  Images/web/  (mirroring original structure)
"""

from pathlib import Path
import re
import shutil

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow not installed. Run: pip install Pillow")
    raise

BASE_DIR  = Path(__file__).resolve().parent.parent
PAGES_DIR = BASE_DIR / 'el-salvador'
IMAGES_DIR = BASE_DIR / 'Images'
WEB_DIR    = BASE_DIR / 'Images' / 'web'

PAGES = [
    'santa-ana.html',
    'el-tunco.html',
    'ruta-de-las-flores.html',
    '7-waterfalls-hike.html',
    'el-salvador-itinerary.html',
    'el-salvador-itinerary.html',
    'top-10-el-salvador.html',
]
# deduplicate while preserving order
seen = set()
PAGES = [p for p in PAGES if not (p in seen or seen.add(p))]

SRC_RE = re.compile(r'src="(\.\./Images/[^"]+)"', re.IGNORECASE)


def collect_srcs():
    """Return sorted list of unique src paths (as written in HTML)."""
    srcs = set()
    for page_name in PAGES:
        html = (PAGES_DIR / page_name).read_text(encoding='utf-8')
        for m in SRC_RE.finditer(html):
            srcs.add(m.group(1))
    return sorted(srcs)


def src_to_disk(src):
    """Convert ../Images/... src to absolute disk path."""
    # src is relative to el-salvador/, strip leading ../
    rel = src[3:]  # strip '../'
    return BASE_DIR / rel


def src_to_web(src):
    """
    Convert ../Images/El Salvador/foo/bar.JPEG  →  ../Images/web/El Salvador/foo/bar.jpg
    """
    # Insert 'web/' after 'Images/'
    assert src.startswith('../Images/')
    rest = src[len('../Images/'):]          # e.g.  El Salvador/foo/bar.JPEG
    stem = Path(rest).with_suffix('.jpg')  # change extension to .jpg
    return f'../Images/web/{stem}'


def compress(src_path: Path, dst_path: Path):
    """Open, resize, and save image preserving ICC profile."""
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src_path) as img:
        w, h = img.size
        icc = img.info.get('icc_profile')

        # Determine target box by orientation
        if h > w:
            box = (600, 900)    # portrait
        elif w > h:
            box = (1200, 800)   # landscape
        else:
            box = (900, 900)    # square

        img = img.convert('RGB')
        img.thumbnail(box, Image.LANCZOS)

        save_kwargs = {'format': 'JPEG', 'quality': 85, 'optimize': True}
        if icc:
            save_kwargs['icc_profile'] = icc

        img.save(dst_path, **save_kwargs)

    orig_kb = src_path.stat().st_size // 1024
    new_kb  = dst_path.stat().st_size // 1024
    return orig_kb, new_kb


def update_html(html, src, web_src):
    """Replace all occurrences of src with web_src in HTML."""
    return html.replace(f'src="{src}"', f'src="{web_src}"')


# ── Main ────────────────────────────────────────────────────────────────────

srcs = collect_srcs()
print(f"Found {len(srcs)} unique image references\n")

errors = []
total_orig = 0
total_new  = 0

for src in srcs:
    disk_path = src_to_disk(src)
    web_src   = src_to_web(src)
    dst_path  = BASE_DIR / web_src[3:]  # strip '../' for absolute path

    if not disk_path.exists():
        print(f"  MISSING  {disk_path.name}")
        errors.append(str(disk_path))
        continue

    orig_kb, new_kb = compress(disk_path, dst_path)
    total_orig += orig_kb
    total_new  += new_kb
    savings = round((1 - new_kb / max(orig_kb, 1)) * 100)
    print(f"  {disk_path.name:<50}  {orig_kb:>5} KB -> {new_kb:>5} KB  ({savings}% saved)")

print(f"\nTotal: {total_orig} KB -> {total_new} KB  "
      f"({round((1 - total_new/max(total_orig,1))*100)}% saved)")

if errors:
    print(f"\nWARNING: {len(errors)} missing source files:")
    for e in errors:
        print(f"  {e}")

# ── Update HTML references ───────────────────────────────────────────────────

print("\nUpdating HTML references...")
for page_name in PAGES:
    path = PAGES_DIR / page_name
    html = path.read_text(encoding='utf-8')
    original = html
    for src in srcs:
        web_src = src_to_web(src)
        html = update_html(html, src, web_src)
    if html != original:
        path.write_text(html, encoding='utf-8')
        n = html.count('Images/web/')
        print(f"  {page_name}: updated (now {n} web/ references)")
    else:
        print(f"  {page_name}: no changes needed")

print("\nDone.")
