"""
Standardize image layouts across all El Salvador article pages.

Rules applied:
 - Portrait images: aspect-ratio 4/5 (or 4 / 5) → 2/3
 - Landscape images: 7/5, 16/8.5 etc. → 3/2
 - 1/1 square images: leave alone (ratio AND layout)
 - Flex pairs of portraits → <div class="img-pair">
 - 3-col portrait grids → <div class="img-pair"> (first 2) + <div class="img-landscape"> (3rd)
 - Standalone landscape <p><span ...> → <div class="img-landscape">
 - Flex divs with raw <img> (landscape) → <div class="img-landscape">
"""

from pathlib import Path
import re

BASE = Path('el-salvador')
PAGES = [
    'santa-ana.html',
    'el-tunco.html',
    'ruta-de-las-flores.html',
    '7-waterfalls-hike.html',
    'el-salvador-itinerary.html',
    'top-10-el-salvador.html',
]


def extract_img(img_tag):
    """Return (src, alt, object_position) from an <img ...> tag."""
    src = re.search(r'\bsrc="([^"]*)"', img_tag)
    alt = re.search(r'\balt="([^"]*)"', img_tag)
    op  = re.search(r'object-position:\s*([^;"]+)', img_tag)
    src = src.group(1) if src else ''
    alt = alt.group(1) if alt else ''
    op  = op.group(1).strip() if op else 'center'
    return src, alt, op


def make_abs_img(img_tag):
    """Build a normalized absolutely-positioned img tag preserving src/alt/object-position."""
    src, alt, op = extract_img(img_tag)
    op_str = f'object-position:{op};' if op else ''
    return (f'<img src="{src}" alt="{alt}" style="position:absolute;top:0;left:0;'
            f'width:100%;height:100%;object-fit:cover;{op_str}">')


def process(html):

    # ── Step 1: Normalise aspect ratios ──────────────────────────────────────────
    # Portrait: 4/5 → 2/3 (various whitespace forms)
    html = re.sub(r'aspect-ratio:\s*4\s*/\s*5', 'aspect-ratio:2/3', html)
    # Landscape: 7/5 → 3/2
    html = re.sub(r'aspect-ratio:\s*7\s*/\s*5', 'aspect-ratio:3/2', html)
    # Landscape wide: 16/8.5 → 3/2
    html = re.sub(r'aspect-ratio:\s*16\s*/\s*8\.5', 'aspect-ratio:3/2', html)

    # ── Step 2: Convert portrait flex-pair divs → .img-pair ──────────────────────
    FLEX_PAT = re.compile(
        r'<div\s+style="display:flex;[^"]*align-items:start">(.*?)</div>',
        re.DOTALL
    )
    def replace_flex(m):
        full    = m.group(0)
        content = m.group(1)
        # Only convert if portrait (2/3) found; skip 1:1 pairs and raw-img divs
        if 'aspect-ratio:2/3' not in full:
            return full
        # Fix span widths: calc(39% – 0.5rem) → 100%
        content = re.sub(r'width:calc\(39%\s*-\s*0\.5rem\)', 'width:100%', content)
        return f'<div class="img-pair">{content}</div>'

    html = FLEX_PAT.sub(replace_flex, html)

    # ── Step 3: Convert portrait 3-col grids → .img-pair + .img-landscape ────────
    GRID3_PAT = re.compile(
        r'<div\s+style="display:grid;grid-template-columns:repeat\(3,1fr\);[^"]*">(.*?)</div>',
        re.DOTALL
    )
    def replace_3col(m):
        full    = m.group(0)
        content = m.group(1)
        # Skip 1:1 square grids
        if 'aspect-ratio:2/3' not in full:
            return full
        spans = re.findall(r'<span\b[^>]*>.*?</span>', content, re.DOTALL)
        if len(spans) != 3:
            return full  # unexpected structure, leave alone
        # First 2 spans → img-pair (widths already 100%)
        img_pair = f'<div class="img-pair">{spans[0]}{spans[1]}</div>'
        # 3rd span → img-landscape (extract img info)
        img_m = re.search(r'<img\b[^>]*>', spans[2])
        if not img_m:
            return full
        img_landscape = f'<div class="img-landscape">{make_abs_img(img_m.group())}</div>'
        return img_pair + img_landscape

    html = GRID3_PAT.sub(replace_3col, html)

    # ── Step 4: Flex divs containing a single raw <img> (landscape) → .img-landscape
    FLEX_RAW_PAT = re.compile(
        r'<div\s+style="display:flex;[^"]*align-items:start">\s*(<img\b[^>]*>)\s*</div>',
        re.DOTALL
    )
    def replace_flex_raw_img(m):
        return f'<div class="img-landscape">{make_abs_img(m.group(1))}</div>'

    html = FLEX_RAW_PAT.sub(replace_flex_raw_img, html)

    # ── Step 5: Standalone landscape <p><span …3/2…><img></span></p> → .img-landscape
    SOLO_LANDSCAPE_PAT = re.compile(
        r'<p>\s*(<span\b[^>]*aspect-ratio:3/2[^>]*>)\s*(<img\b[^>]*>)\s*</span>\s*</p>',
        re.DOTALL
    )
    def replace_solo_landscape(m):
        img_tag = make_abs_img(m.group(2))
        return f'<div class="img-landscape">{img_tag}</div>'

    html = SOLO_LANDSCAPE_PAT.sub(replace_solo_landscape, html)

    return html


for page_name in PAGES:
    path = BASE / page_name
    html = path.read_text(encoding='utf-8')
    before = len(html)
    html = process(html)
    path.write_text(html, encoding='utf-8')
    print(f'  {page_name}: {before} -> {len(html)} bytes')

print('Done.')
