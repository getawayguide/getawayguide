"""
tools/update_destinations.py
Updates destinations.html and site-wide nav for the 5 destinations tasks:
 1. NZ: Explore → Coming Soon
 2. Move Albania, Greece, Peru, Philippines from Articles → Field Notes (+ update map)
 3. Remove Tanzania from Articles; add TZ to visitedISO
 4. Add continent filter bar to Field Notes section
 5. Update nav on all HTML pages (remove AL/GR/PE/TZ/PH from Available Guides;
    add AL/GR/PE/PH to Field Notes)
"""

import os, re

ROOT = r'c:\Users\kevin\OneDrive\Documents\Travel Blog'

# ──────────────────────────────────────────────────────────────────────────────
# DESTINATIONS.HTML
# ──────────────────────────────────────────────────────────────────────────────
dest_path = os.path.join(ROOT, 'destinations.html')
with open(dest_path, 'rb') as f:
    raw = f.read()
has_crlf = b'\r\n' in raw
d = raw.replace(b'\r\n', b'\n').decode('utf-8')

# 1. NZ: Explore → Coming Soon
old_nz = '          <p class="art-card-name">New Zealand</p>\n          <span class="art-card-btn">Explore</span>'
new_nz = '          <p class="art-card-name">New Zealand</p>\n          <span class="art-card-btn art-card-soon">Coming Soon</span>'
if old_nz in d:
    d = d.replace(old_nz, new_nz)
    print("  ✓ NZ: Explore → Coming Soon")
else:
    print("  ⚠ NZ Explore button not found")

# 2. Remove article cards from articles-grid
def remove_article_card(d, country):
    name_marker = f'<p class="art-card-name">{country}</p>'
    pos = d.find(name_marker)
    if pos == -1:
        print(f"  ⚠ Card for {country} not found")
        return d
    art_info_close = d.find('\n        </div>', pos)
    if art_info_close == -1:
        print(f"  ⚠ art-card-info close not found for {country}"); return d
    card_close = d.find('\n      </div>', art_info_close + 1)
    if card_close == -1:
        print(f"  ⚠ card close not found for {country}"); return d
    card_end = card_close + len('\n      </div>')
    comment_start = d.rfind('\n\n      <!-- ', 0, pos)
    if comment_start == -1:
        comment_start = d.rfind('\n      <div class="home-dest-card"', 0, pos)
    if comment_start == -1:
        print(f"  ⚠ card start not found for {country}"); return d
    d = d[:comment_start] + d[card_end:]
    print(f"  ✓ Removed article card: {country}")
    return d

for country in ['Albania', 'Greece', 'Peru', 'Tanzania', 'Philippines']:
    d = remove_article_card(d, country)

# Remove leftover Turkiye placeholder comment in articles grid
d = re.sub(r'\n\n      <!-- Turkiye[^\n]*-->\n\n\n', '\n\n', d)
d = re.sub(r'\n\n      <!-- Turkiye[^\n]*-->\n\n', '\n\n', d)
print("  ✓ Cleaned up Turkiye placeholder comment")

# 3. Add data-continent to existing field notes cards
fn_continent_map = {
    'north-macedonia/field-notes.html': 'europe',
    'armenia/field-notes.html':         'europe',
    'turkiye/field-notes.html':         'europe',
    'serbia/field-notes.html':          'europe',
    'kosovo/field-notes.html':          'europe',
}
for url, continent in fn_continent_map.items():
    old = f'<div class="home-dest-card" onclick="location.href=\'{url}\'" style="cursor:pointer">'
    new = f'<div class="home-dest-card" data-continent="{continent}" onclick="location.href=\'{url}\'" style="cursor:pointer">'
    if old in d:
        d = d.replace(old, new)
        print(f"  ✓ Added data-continent={continent} to {url}")
    else:
        print(f"  ⚠ Could not add data-continent to {url}")

# 4. Add Albania, Greece, Peru, Philippines to field-notes-grid (Coming Soon)
new_fn_cards = '''
      <!-- Albania -->
      <div class="home-dest-card" data-continent="europe">
        <div class="dest-bg" style="background-image:url('Images/web/dest-cards/albania.jpg');background-size:cover;background-position:center"></div>
        <div class="dest-overlay"></div>
        <div class="art-card-info">
          <p class="art-card-name">Albania</p>
          <span class="art-card-btn art-card-soon">Coming Soon</span>
        </div>
      </div>

      <!-- Greece -->
      <div class="home-dest-card" data-continent="europe">
        <div class="dest-bg bg-greece"></div>
        <div class="dest-overlay"></div>
        <div class="art-card-info">
          <p class="art-card-name">Greece</p>
          <span class="art-card-btn art-card-soon">Coming Soon</span>
        </div>
      </div>

      <!-- Peru -->
      <div class="home-dest-card" data-continent="americas">
        <div class="dest-bg" style="background-image:url('Images/web/dest-cards/peru.jpg');background-size:cover;background-position:center"></div>
        <div class="dest-overlay"></div>
        <div class="art-card-info">
          <p class="art-card-name">Peru</p>
          <span class="art-card-btn art-card-soon">Coming Soon</span>
        </div>
      </div>

      <!-- Philippines -->
      <div class="home-dest-card" data-continent="asia">
        <div class="dest-bg bg-philippines"></div>
        <div class="dest-overlay"></div>
        <div class="art-card-info">
          <p class="art-card-name">Philippines</p>
          <span class="art-card-btn art-card-soon">Coming Soon</span>
        </div>
      </div>'''

anchor = '\n\n    </div>\n  </section>\n\n  <footer>'
if anchor in d:
    d = d.replace(anchor, new_fn_cards + anchor)
    print("  ✓ Added Albania/Greece/Peru/Philippines to field-notes-grid")
else:
    print("  ⚠ field-notes-grid closing anchor not found")

# 5. Add filter bar to Field Notes section
fn_filter_bar = '''    <div class="filter-bar-inline">
      <span class="filter-label">Filter by</span>
      <button class="filter-btn active" onclick="filterFieldNotes(\'all\',this)">All</button>
      <button class="filter-btn" onclick="filterFieldNotes(\'europe\',this)">Europe</button>
      <button class="filter-btn" onclick="filterFieldNotes(\'asia\',this)">Asia</button>
      <button class="filter-btn" onclick="filterFieldNotes(\'americas\',this)">Americas</button>
    </div>
    <div class="home-dest-grid" id="field-notes-grid">'''

old_fn_grid_start = '    <div class="home-dest-grid" id="field-notes-grid">'
if old_fn_grid_start in d:
    d = d.replace(old_fn_grid_start, fn_filter_bar)
    print("  ✓ Added filter bar to Field Notes section")
else:
    print("  ⚠ field-notes-grid div not found")

# 6. Add filterFieldNotes JS function after filterArticles
old_filter_fn = '''function filterArticles(continent, btn) {
  document.querySelectorAll('#articles-section .filter-btn').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  document.querySelectorAll('#articles-grid .home-dest-card').forEach(function(card){
    card.style.display = (continent === 'all' || card.dataset.continent === continent) ? '' : 'none';
  });
}'''
new_filter_fn = old_filter_fn + '''
function filterFieldNotes(continent, btn) {
  document.querySelectorAll('#field-notes-section .filter-btn').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  document.querySelectorAll('#field-notes-grid .home-dest-card').forEach(function(card){
    card.style.display = (continent === 'all' || card.dataset.continent === continent) ? '' : 'none';
  });
}'''
if old_filter_fn in d:
    d = d.replace(old_filter_fn, new_filter_fn)
    print("  ✓ Added filterFieldNotes JS function")
else:
    print("  ⚠ filterArticles function not found for injection")

# 7. Map JS: update featuredMap (remove AL, GR, PE, TZ, PH)
old_featured = '''var featuredMap = {
  "SV": { name: "El Salvador",  url: "el-salvador/index.html" },
  "AL": { name: "Albania",      url: null },
  "GR": { name: "Greece",       url: null },
  "NZ": { name: "New Zealand",  url: "new-zealand/index.html" },
  "PE": { name: "Peru",         url: null },
  "TZ": { name: "Tanzania",     url: null },
  "PH": { name: "Philippines",  url: null }
};'''
new_featured = '''var featuredMap = {
  "SV": { name: "El Salvador",  url: "el-salvador/index.html" },
  "NZ": { name: "New Zealand",  url: "new-zealand/index.html" }
};'''
if old_featured in d:
    d = d.replace(old_featured, new_featured)
    print("  ✓ Updated featuredMap (removed AL/GR/PE/TZ/PH)")
else:
    print("  ⚠ featuredMap not found as expected")

# 8. Map JS: update fieldNotesMap (add AL, GR, PE, PH)
old_fnmap = '''var fieldNotesMap = {
  "AM": { name: "Armenia", url: "armenia/field-notes.html" },
  "XK": { name: "Kosovo", url: "kosovo/field-notes.html" },
  "MK": { name: "North Macedonia", url: "north-macedonia/field-notes.html" },
  "RS": { name: "Serbia", url: "serbia/field-notes.html" },
  "TR": { name: "Turkiye", url: "turkiye/field-notes.html" }
};'''
new_fnmap = '''var fieldNotesMap = {
  "AL": { name: "Albania",         url: null },
  "AM": { name: "Armenia",         url: "armenia/field-notes.html" },
  "GR": { name: "Greece",          url: null },
  "XK": { name: "Kosovo",          url: "kosovo/field-notes.html" },
  "MK": { name: "North Macedonia", url: "north-macedonia/field-notes.html" },
  "PE": { name: "Peru",            url: null },
  "PH": { name: "Philippines",     url: null },
  "RS": { name: "Serbia",          url: "serbia/field-notes.html" },
  "TR": { name: "Turkiye",         url: "turkiye/field-notes.html" }
};'''
if old_fnmap in d:
    d = d.replace(old_fnmap, new_fnmap)
    print("  ✓ Updated fieldNotesMap (added AL/GR/PE/PH)")
else:
    print("  ⚠ fieldNotesMap not found as expected")

# 9. Map JS: update fieldNotesSeries tooltip to show Coming Soon for null URLs
old_tip = "      '<div style=\"color:rgba(255,255,255,.55);font-size:9px;text-transform:uppercase;letter-spacing:.1em;margin-top:2px\">Field Notes</div>' +"
new_tip = "      '<div style=\"color:rgba(255,255,255,.55);font-size:9px;text-transform:uppercase;letter-spacing:.1em;margin-top:2px\">' + (c.url ? 'Field Notes' : 'Coming Soon') + '</div>' +"
if old_tip in d:
    d = d.replace(old_tip, new_tip)
    print("  ✓ Updated fieldNotes tooltip (Coming Soon for no-url)")
else:
    print("  ⚠ fieldNotes tooltip line not found")

# 10. Map JS: update click handler to skip null URL
old_click = "  if (!id || !fieldNotesMap[id]) return;"
new_click = "  if (!id || !fieldNotesMap[id] || !fieldNotesMap[id].url) return;"
if old_click in d:
    d = d.replace(old_click, new_click)
    print("  ✓ Updated fieldNotes click handler (skip null URL)")
else:
    print("  ⚠ fieldNotes click handler not found")

# 11. Map JS: add TZ to visitedISO
old_africa = '  // Africa\n  "EG","MA",'
new_africa = '  // Africa\n  "EG","MA","TZ",'
if old_africa in d:
    d = d.replace(old_africa, new_africa)
    print("  ✓ Added TZ to visitedISO")
else:
    print("  ⚠ visitedISO Africa block not found")

# Write destinations.html
out = d.encode('utf-8')
if has_crlf:
    out = out.replace(b'\n', b'\r\n')
with open(dest_path, 'wb') as f:
    f.write(out)
print("\n  Saved destinations.html")

# ──────────────────────────────────────────────────────────────────────────────
# ALL HTML FILES: Update nav dropdown
# ──────────────────────────────────────────────────────────────────────────────
print("\n─── Updating nav on all HTML pages ───")

# Regex: match Available Guides span → last Turkiye flag CDN link
# Works for both root and subdir pages (handles backslashes too)
NAV_PATTERN = re.compile(
    r'<span class="nav-dropdown-continent"[^>]*>Available Guides</span>'
    r'.*?'
    r'<img src="https://flagcdn\.com/16x12/tr\.png"[^>]*><span class="country-name">Turkiye</span></a>',
    re.DOTALL
)

def make_nav_block(prefix=''):
    """Build the Available Guides + Field Notes nav block for a given href prefix."""
    d_url = f'{prefix}destinations.html'
    return (
        '<span class="nav-dropdown-continent" style="pointer-events:none;cursor:default;white-space:nowrap;color:#1C2821">Available Guides</span>\n'
        f'          <a href="{prefix}el-salvador/index.html" class="nav-dropdown-item"><img src="https://flagcdn.com/16x12/sv.png" width="16" height="12" alt=""><span class="country-name">El Salvador</span></a>\n'
        f'          <a href="{prefix}new-zealand/index.html" class="nav-dropdown-item"><img src="https://flagcdn.com/16x12/nz.png" width="16" height="12" alt=""><span class="country-name">New Zealand</span></a>\n'
        '          <span class="nav-dropdown-continent" style="pointer-events:none;cursor:default;white-space:nowrap;color:#1C2821;margin-top:.4rem;padding-top:.4rem;border-top:1px solid rgba(28,40,33,.08)">Field Notes</span>\n'
        f'          <a href="{d_url}#field-notes-section" class="nav-dropdown-item"><img src="https://flagcdn.com/16x12/al.png" width="16" height="12" alt=""><span class="country-name">Albania</span></a>\n'
        f'          <a href="{prefix}armenia/field-notes.html" class="nav-dropdown-item"><img src="https://flagcdn.com/16x12/am.png" width="16" height="12" alt=""><span class="country-name">Armenia</span></a>\n'
        f'          <a href="{d_url}#field-notes-section" class="nav-dropdown-item"><img src="https://flagcdn.com/16x12/gr.png" width="16" height="12" alt=""><span class="country-name">Greece</span></a>\n'
        f'          <a href="{prefix}kosovo/field-notes.html" class="nav-dropdown-item"><img src="https://flagcdn.com/16x12/xk.png" width="16" height="12" alt=""><span class="country-name">Kosovo</span></a>\n'
        f'          <a href="{prefix}north-macedonia/field-notes.html" class="nav-dropdown-item"><img src="https://flagcdn.com/16x12/mk.png" width="16" height="12" alt=""><span class="country-name">North Macedonia</span></a>\n'
        f'          <a href="{d_url}#field-notes-section" class="nav-dropdown-item"><img src="https://flagcdn.com/16x12/pe.png" width="16" height="12" alt=""><span class="country-name">Peru</span></a>\n'
        f'          <a href="{d_url}#field-notes-section" class="nav-dropdown-item"><img src="https://flagcdn.com/16x12/ph.png" width="16" height="12" alt=""><span class="country-name">Philippines</span></a>\n'
        f'          <a href="{prefix}serbia/field-notes.html" class="nav-dropdown-item"><img src="https://flagcdn.com/16x12/rs.png" width="16" height="12" alt=""><span class="country-name">Serbia</span></a>\n'
        f'          <a href="{prefix}turkiye/field-notes.html" class="nav-dropdown-item"><img src="https://flagcdn.com/16x12/tr.png" width="16" height="12" alt=""><span class="country-name">Turkiye</span></a>'
    )

ROOT_NAV = make_nav_block('')
SUB_NAV  = make_nav_block('../')

updated = 0
skipped = 0
for dirpath, dirs, files in os.walk(ROOT):
    dirs[:] = [d2 for d2 in dirs if not d2.startswith('.')]
    for fname in files:
        if not fname.endswith('.html'):
            continue
        fpath = os.path.join(dirpath, fname)
        rel = os.path.relpath(fpath, ROOT)
        if rel.startswith('.tmp') or rel.startswith('tools'):
            continue

        with open(fpath, 'rb') as f:
            raw2 = f.read()
        has_crlf2 = b'\r\n' in raw2
        content = raw2.replace(b'\r\n', b'\n').decode('utf-8')

        if 'Available Guides' not in content:
            skipped += 1
            continue

        is_root = (os.path.dirname(rel) == '' or os.path.dirname(rel) == '.')
        replacement = ROOT_NAV if is_root else SUB_NAV

        new_content, n = NAV_PATTERN.subn(replacement, content)
        if n > 0:
            out2 = new_content.encode('utf-8')
            if has_crlf2:
                out2 = out2.replace(b'\n', b'\r\n')
            with open(fpath, 'wb') as f:
                f.write(out2)
            updated += 1
            print(f"  ✓ Nav updated: {rel}")
        else:
            print(f"  ⚠ Nav pattern not matched: {rel}")

print(f"\n  Nav: {updated} pages updated, {skipped} skipped (no nav).")
print("\nDone.")
