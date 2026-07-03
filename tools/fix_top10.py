"""Apply all Top 10 article changes without touching image paths."""
from pathlib import Path
import re

path = Path('el-salvador/top-10-el-salvador.html')
html = path.read_text(encoding='utf-8')

def make_pair_span(src, obj_pos, ratio):
    return (f'<span style="display:block;position:relative;overflow:hidden;'
            f'width:100%;aspect-ratio:{ratio};border-radius:2px;">'
            f'<img src="{src}" alt="" style="position:absolute;top:0;left:0;'
            f'width:100%;height:100%;object-fit:cover;object-position:{obj_pos};" class=""></span>')

def extract(frag):
    """Find img src, object-position and aspect-ratio for an image whose src contains frag."""
    m = re.search(
        r'aspect-ratio:\s*([\d /]+?)\s*;[^>]*?><img src="([^"]*' + re.escape(frag) + r'[^"]*)"[^>]*?object-position:\s*([^;"]+?)\s*[;"]',
        html, re.DOTALL)
    if not m:
        raise ValueError(f'Cannot find image: {frag}')
    ratio, src, op = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    return src, op, ratio

def find_span_block(src):
    """Return the full <p><span>...</span></p> block for a given src."""
    # Find the <p> that contains this src
    idx = html.find(src)
    if idx == -1:
        raise ValueError(f'src not found in html: {src}')
    # Walk backwards to find opening <p>
    p_start = html.rfind('<p>', 0, idx)
    # Walk forwards to find closing </p>
    p_end = html.find('</p>', idx) + len('</p>')
    return html[p_start:p_end]

def convert_pair(frag1, frag2):
    global html
    src1, op1, r1 = extract(frag1)
    src2, op2, r2 = extract(frag2)
    block1 = find_span_block(src1)
    block2 = find_span_block(src2)
    old = block1 + block2
    new = ('<div class="img-pair">'
           + make_pair_span(src1, op1, r1)
           + make_pair_span(src2, op2, r2)
           + '</div>')
    if old not in html:
        raise ValueError(f'Combined block not found for: {frag1} + {frag2}\nblock1={repr(block1[:80])}\nblock2={repr(block2[:80])}')
    html = html.replace(old, new, 1)
    print(f'  Paired: {frag1} | {frag2}')

# ── Pair sections 2, 3, 4, 6, 7, 10 ─────────────────────────────────────────
convert_pair('El Tunco Sunset 2', 'El Tunco Mural')
convert_pair('Volcano-1', 'Lago de Coatepeque')
convert_pair('hectors-tour-1', 'hectors-tour-2')
convert_pair('Taquillo View', 'Tacos - Shalpa')
convert_pair('Pupusa Making People', 'Pupusa Making Class-2')
convert_pair('Cafe Albania Slide', 'Cafe Albania Drop Swing')

# ── Section 8: Motorcycle 4-image 2×2 ───────────────────────────────────────
src_m, op_m, r_m = extract('Motorcycle Solo 2')
src_p, op_p, r_p = extract('Pachamama Store Far')
src_a, op_a, r_a = extract('Ataco Viewpoint Portrait')
src_ap, op_ap, r_ap = extract('Apaneca Path 2')

b_m  = find_span_block(src_m)
b_p  = find_span_block(src_p)
b_a  = find_span_block(src_a)
b_ap = find_span_block(src_ap)

# The four blocks appear with empty <p></p> paragraphs between them — find the whole region
idx_m  = html.find(b_m)
idx_ap = html.find(b_ap)
if idx_m == -1 or idx_ap == -1:
    raise ValueError('Motorcycle blocks not found')
old_region = html[idx_m : idx_ap + len(b_ap)]
# Also strip any leading empty paragraph before the first block
pre_p = '<p></p>'
start = idx_m
while html[start - len(pre_p):start] == pre_p:
    start -= len(pre_p)
old_region = html[start : idx_ap + len(b_ap)]

new_4 = ('<div class="img-pair">'
         + make_pair_span(src_m, op_m, r_m)
         + make_pair_span(src_p, op_p, r_p)
         + make_pair_span(src_a, op_a, r_a)
         + make_pair_span(src_ap, op_ap, r_ap)
         + '</div>')
html = html[:start] + new_4 + html[idx_ap + len(b_ap):]
print('  Paired 2x2: Motorcycle section')

# ── Link "my article on El Salvador's Coast" ─────────────────────────────────
html = html.replace(
    "my article on El Salvador's Coast",
    "<a href=\"el-tunco.html\">my article on El Salvador's Coast</a>"
)
print("  Linked: El Salvador's Coast")

# ── Remove empty tip ─────────────────────────────────────────────────────────
before = len(html)
html = html.replace('<div class="article-tip"><br></div>', '')
print(f'  Removed empty tip(s)')

# ── Change last tip block to paragraph ───────────────────────────────────────
html = html.replace(
    '<div class="article-tip">There are plenty more activities to explore in El Salvador. '
    'Check out my individual articles for the ultimate El Salvador Travel Guide with Perfect '
    '10 Day Itinerary or my various articles on each part of El Salvador here.</div>',
    '<p>There are plenty more activities to explore in El Salvador. Check out my individual '
    'articles for the ultimate <a href="el-salvador-itinerary.html">El Salvador Travel Guide '
    'with Perfect 10 Day Itinerary</a> or my various articles on each part of El Salvador here.</p>'
)
print('  Converted last tip to paragraph')

# ── Update volcano tip text ───────────────────────────────────────────────────
html = html.replace(
    '<div class="article-tip"><strong>Tip:</strong> Start early \u2014 guides typically depart '
    'around 8am. Bring layers; the summit can be surprisingly windy.</div>',
    '<div class="article-tip">Start early! Guides typically depart around 8am. '
    'Bring layers; the summit can be surprisingly windy.</div>'
)
print('  Updated volcano tip')

path.write_text(html, encoding='utf-8')
print('Done.')
