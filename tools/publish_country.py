#!/usr/bin/env python3
"""
publish_country.py — move a field-notes country from Drafts/ to the live site
and wire it in everywhere. Formalizes the manual protocol (first used for Tanzania).

Does, in order:
  1. Move Drafts/<slug>/field-notes.html -> <slug>/field-notes.html (fix ../../ -> ../)
  2. Regenerate the grouped Field Notes nav dropdown on every page (grouped by continent)
  3. Insert the index.html flag-card (alphabetical)
  4. Insert the destinations.html grid card (data-continent) + fieldNotesMap entry
  5. Compress the thumbnail -> Images/web/dest-cards/<slug>.jpg (portrait 600x900, q85, keep ICC)
  6. Remove the empty Drafts/<slug>/ folder
  7. Rebuild search-index.js so the new country/text is searchable (also adds the
     search.js script tag to the moved page, which drafts lack)

Usage:
  python tools/publish_country.py <slug> --name "Name" --iso2 xx --continent europe \
        [--thumb "Images/dest-cards/Name_1.JPG"] [--pos "50% 50%"] [--dry-run]

Always run tools/lint_site.py and a screenshot afterward. Do NOT push until Kevin reviews.
"""
import os, re, glob, sys, shutil, argparse
import thumb_lock  # preserve hand-edited dest-card thumbnails across runs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def rel(p): return os.path.relpath(p, ROOT).replace("\\", "/")
def read(p): return open(p, encoding="utf-8").read()
def write(p, s):
    if not DRY: open(p, "w", encoding="utf-8", newline="").write(s)

def live_pages():
    for p in glob.glob(os.path.join(ROOT, "**", "*.html"), recursive=True):
        r = rel(p)
        if r.startswith(("Drafts/", ".tmp/", ".git/")): continue
        yield p, r

def insert_after_alpha(html, item_re, name_grp, new_name, make_line):
    """Insert a new <a>/block alphabetically among existing matches of item_re.
    make_line(indent, prefix) builds the new line. Returns (html, changed)."""
    matches = list(re.finditer(item_re, html, re.M))
    if not matches:
        return html, False
    # is the new item already present?
    for m in matches:
        if m.group(name_grp).strip().lower() == new_name.lower():
            return html, False
    target = None
    for m in matches:
        if m.group(name_grp).strip().lower() > new_name.lower():
            target = m; before = True; break
    if target is None:
        target = matches[-1]; before = False
    indent = re.match(r"[ \t]*", target.group(0).split("\n")[0]).group(0)
    # prefix (../ depth) taken from the target line's href
    pm = re.search(r'href="((?:\.\./)*)', target.group(0)) or re.search(r"location\.href='((?:\.\./)*)", target.group(0))
    prefix = pm.group(1) if pm else ""
    line = make_line(indent, prefix)
    if before:
        # insert at start of the target's line (before its indent)
        pos = target.start()
        # back up to line start
        ls = html.rfind("\n", 0, pos) + 1
        return html[:ls] + line + "\n" + html[ls:], True
    else:
        pos = target.end()
        return html[:pos] + "\n" + line + html[pos:], True

# ---- grouped "Field Notes" nav dropdown, regenerated from destinations.html ----
# The dropdown is identical on every page and changes on every publish, so we rebuild
# it wholesale from a single source of truth rather than inserting into a flat list.
# Countries that have FULL multi-page guides (not just field notes). They're folded into
# their continent group in the merged dropdown and flagged with a green dot. (name, iso2,
# continent, href-to-guide-index). New Zealand's guides are unfinished -> moved to Drafts, omitted.
FULL_GUIDES = [("El Salvador", "sv", "americas", "el-salvador/index.html")]
NAV_SUBCOLS = [["europe", "africa"], ["asia", "americas", "oceania"]]  # left/right; empty groups skipped
CONT_LABEL = {"europe": "Europe", "asia": "Asia", "americas": "Americas",
              "africa": "Africa", "oceania": "Oceania"}
_DARK = 'style="pointer-events:none;cursor:default;white-space:nowrap;color:#1C2821"'
NAV_RE = re.compile(r'<div class="nav-dropdown[ "].*?</div>\s*</li>', re.DOTALL)

def _nav_a(pfx, name, iso2, href, full=False):
    dot = '<span class="nav-fulldot" title="Full guide available"></span>' if full else ''
    return ('<a href="%s%s" class="nav-dropdown-item"><img loading="lazy" '
            'src="https://flagcdn.com/16x12/%s.png" width="16" height="12" alt="">'
            '<span class="country-name">%s</span>%s</a>') % (pfx, href, iso2, name, dot)

def parse_countries(dest_html):
    """{continent: [(name, iso2, slug) alpha]} joined from destinations.html grid + fieldNotesMap."""
    cont_by_slug = {m.group(2): m.group(1) for m in re.finditer(
        r'data-continent="([a-z]+)" onclick="location\.href=\'([a-z-]+)/field-notes\.html\'"', dest_html)}
    by_cont = {}
    for m in re.finditer(r'"([A-Z]{2})": \{ name: "([^"]+)",\s*url: "([a-z-]+)/field-notes\.html"', dest_html):
        c = cont_by_slug.get(m.group(3))
        if c:
            by_cont.setdefault(c, []).append((m.group(2), m.group(1).lower(), m.group(3)))
    for c in by_cont:
        by_cont[c].sort(key=lambda t: t[0].lower())
    return by_cont

def build_nav(pfx, by_cont):
    # Merge the FULL_GUIDES (multi-page guide countries) into their continent groups so the
    # dropdown is ONE unified, continent-grouped list under "Available Guides" (no separate
    # "Field Notes" section). Full-guide countries get a green dot and link to their guide index.
    merged = {c: [(n, iso, "%s/field-notes.html" % sl, False) for (n, iso, sl) in lst]
              for c, lst in by_cont.items()}
    for name, iso, cont, href in FULL_GUIDES:
        merged.setdefault(cont, []).append((name, iso, href, True))
    for c in merged:
        merged[c].sort(key=lambda t: t[0].lower())
    L = ['<div class="nav-dropdown nav-dropdown-fn">',
         '        <div class="nav-dropdown-col">',
         '          <div class="nav-guides-head"><span class="nav-dropdown-continent" %s>Available Guides</span>'
         '<span class="nav-guides-legend"><span class="nav-fulldot"></span>In-Depth Guide</span></div>' % _DARK,
         '          <div class="nav-fn-groups">']
    for col in NAV_SUBCOLS:
        cols = [c for c in col if merged.get(c)]
        if not cols:
            continue
        L.append('            <div class="nav-fn-sub">')
        for c in cols:
            L.append('            <div class="nav-fn-grp">')
            L.append('              <span class="nav-fn-region">%s</span>' % CONT_LABEL[c])
            L += ['              ' + _nav_a(pfx, n, iso, target, full) for n, iso, target, full in merged[c]]
            L.append('            </div>')
        L.append('            </div>')
    L += ['          </div>', '        </div>', '        <div class="nav-dropdown-col">',
          '          <span class="nav-dropdown-continent" %s>Explore Destinations</span>' % _DARK,
          '          <a href="%sdestinations.html" class="nav-dropdown-continent" style="white-space:nowrap">&#127758; Explore Map</a>' % pfx]
    L += ['          <a href="%sdestinations.html#%s" class="nav-dropdown-continent">%s</a>' % (pfx, c, CONT_LABEL[c])
          for c in ("africa", "americas", "asia", "europe", "oceania")]
    L += ['          <a href="%sdestinations.html" class="nav-dropdown-continent" style="color:#1C2821;white-space:nowrap">All Destinations</a>' % pfx,
          '        </div>', '      </div>']
    return "\n".join(L)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--name", required=True)
    ap.add_argument("--iso2", required=True)
    ap.add_argument("--continent", required=True, choices=["europe", "americas", "asia", "africa", "oceania"])
    ap.add_argument("--thumb", default=None, help="source image; default Images/dest-cards/<Name>_1.JPG")
    ap.add_argument("--pos", default="50% 50%", help="dest-card background-position")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="regenerate even a hand-edited thumbnail")
    ap.add_argument("--skip-generators", action="store_true",
                    help="don't run OG/JSON-LD/sitemap generators + lint afterwards")
    a = ap.parse_args()
    global DRY, FORCE; DRY = a.dry_run; FORCE = a.force
    slug, name, iso2, cont = a.slug, a.name, a.iso2.lower(), a.continent
    tag = "[dry-run] " if DRY else ""
    log = []

    src = os.path.join(ROOT, "Drafts", slug, "field-notes.html")
    dst = os.path.join(ROOT, slug, "field-notes.html")
    if not os.path.exists(src): sys.exit("ERROR: draft not found: %s" % rel(src))
    if os.path.exists(dst) and not DRY: sys.exit("ERROR: live page already exists: %s" % rel(dst))

    # 1. move + fix path depth (+ ensure the site-search script tag, which draft
    #    pages don't have — a subpage loads it from ../)
    html = read(src).replace("../../", "../")
    assert "../../" not in html
    if "search.js" not in html and "</body>" in html:
        html = html.replace("</body>", '<script defer src="../search.js"></script>\n</body>', 1)
    if not DRY:
        os.makedirs(os.path.join(ROOT, slug), exist_ok=True)
        write(dst, html)
    log.append("moved draft -> %s (../../ -> ../, +search.js)" % rel(dst))

    # 2. nav is regenerated for every page in step 4c (needs the updated destinations data first)

    # 3. index flag-card — only if that section still exists. The 2026-07 restructure REMOVED
    #    the "Field Notes" flag strip from index.html; this used to log "already present" for a
    #    section that no longer exists, which read like success. Skip explicitly instead.
    ip = os.path.join(ROOT, "index.html"); s = read(ip)
    if "fn-flag-card" in s:
        fc = lambda indent, pfx: ('%s<a href="%s/field-notes.html" class="fn-flag-card">'
            '<img src="https://flagcdn.com/32x24/%s.png" width="32" height="24" alt="%s">'
            '<span class="fn-flag-name">%s</span></a>') % (indent, slug, iso2, name, name)
        fc_re = r'[ \t]*<a href="[a-z-]+/field-notes\.html" class="fn-flag-card"><img[^>]*><span class="fn-flag-name">(?P<n>[^<]+)</span></a>'
        s2, ch = insert_after_alpha(s, fc_re, "n", name, fc); write(ip, s2)
        log.append("index flag-card: " + ("added" if ch else "already present"))
    else:
        log.append("index flag-card: n/a (section removed in the 2026-07 restructure)")

    # 4. destinations grid card + fieldNotesMap
    dp = os.path.join(ROOT, "destinations.html"); s = read(dp)
    def card(indent):
        return ("\n".join([
            '%s<!-- %s -->' % (indent, name),
            '%s<div class="home-dest-card" data-continent="%s" onclick="location.href=\'%s/field-notes.html\'" style="cursor:pointer">' % (indent, cont, slug),
            '%s  <div class="dest-bg" style="background-image:url(\'Images/web/dest-cards/%s.jpg\');background-size:cover;background-position:%s"></div>' % (indent, slug, a.pos),
            '%s  <div class="dest-overlay"></div>' % indent,
            '%s  <div class="art-card-info">' % indent,
            '%s    <p class="art-card-name">%s</p>' % (indent, name),
            '%s    <span class="art-card-btn">Read Notes</span>' % indent,
            '%s  </div>' % indent, '%s</div>' % indent]))
    # Alphabetical insert among the cards in #articles-grid. The 2026-07 restructure merged
    # the two grids and dropped the `<!-- Name -->` comment that used to precede each card,
    # so anchor on the card div itself and read its .art-card-name (NOT the old comment —
    # that regex silently matched nothing, so publishes reported success while inserting
    # nothing, which also dropped the country from parse_countries -> from the nav).
    def grid_cards(html):
        out = []
        for m in re.finditer(r'[ \t]*<div class="home-dest-card"[^>]*>', html):
            nm = re.search(r'class="art-card-name">([^<]+)<', html[m.end():m.end() + 800])
            if nm:
                out.append((m.start(), nm.group(1).strip()))
        return out
    cards = grid_cards(s)
    if not cards:
        sys.exit("ERROR: no .home-dest-card entries found in destinations.html — the grid markup "
                 "changed again; update grid_cards() before publishing.")
    if not re.search(r"location\.href='%s/field-notes\.html'" % slug, s):
        ins = card("      ") + "\n\n"
        nxt = next((pos for pos, nm in cards if nm.lower() > name.lower()), None)
        if nxt is not None:
            ls = s.rfind("\n", 0, nxt) + 1
            s = s[:ls] + ins + s[ls:]
        else:                                   # alphabetically last: append after the final card
            lastpos = cards[-1][0]
            end = s.index("</div>", s.index("art-card-info", lastpos))
            end = s.index("</div>", end + 1)    # the card's own closing tag
            s = s[:end + 6] + "\n\n" + card("      ") + s[end + 6:]
        log.append("destinations grid card added (among %d)" % len(cards))
    else:
        log.append("destinations grid card already present")
    # fieldNotesMap
    if '"%s":' % iso2.upper() not in s:
        entries = list(re.finditer(r'\n( *)"([A-Z]{2})": \{ name: "([^"]+)",\s*url:', s))
        newe = '  "%s": { name: "%s",%surl: "%s/field-notes.html" },' % (
            iso2.upper(), name, " " * max(1, 15 - len(name)), slug)
        target = None
        for m in entries:
            if m.group(3).lower() > name.lower(): target = m; break
        if target is not None:
            s = s[:target.start()] + "\n" + newe + s[target.start():]
        elif entries:
            m = entries[-1]; eol = s.index("\n", m.end())
            # add a comma to the (previously last) entry, then append ours
            s = s[:eol] + "," + "\n" + newe + s[eol:]
        log.append("fieldNotesMap entry added")
    else:
        log.append("fieldNotesMap already present")
    write(dp, s)

    # 4c. Regenerate the grouped Field Notes nav dropdown on every page from the
    #     just-updated destinations.html (the single source of truth for the country set +
    #     continents). This replaces the whole dropdown, so old drafts / prior layouts and
    #     nav-drift are all resolved in one pass.
    by_cont = parse_countries(s)
    nfiles = list(live_pages())
    override = {}
    if DRY:                                   # moved page isn't written in dry-run
        nfiles.append((dst, rel(dst))); override[dst] = html
    nchanged = 0
    for np, nr in nfiles:
        cur = override.get(np) or (read(np) if os.path.exists(np) else None)
        if not cur or '<div class="nav-dropdown' not in cur:
            continue
        pfx = "../" if "/" in nr else ""
        new = NAV_RE.sub(build_nav(pfx, by_cont) + "\n    </li>", cur, count=1)
        if new != cur:
            if np not in override:
                write(np, new)
            nchanged += 1
    log.append("regenerated grouped nav on %d pages" % nchanged)

    # 5. thumbnail
    thumb = a.thumb or "Images/dest-cards/%s_1.JPG" % name
    tsrc = os.path.join(ROOT, thumb)
    tdst = os.path.join(ROOT, "Images", "web", "dest-cards", "%s.jpg" % slug)
    if os.path.exists(tsrc):
        if not DRY and not FORCE and thumb_lock.is_manual(tdst):
            log.append("kept hand-edited thumbnail %s (skipped)" % rel(tdst))
        elif not DRY:
            from PIL import Image, ImageOps
            im = ImageOps.exif_transpose(Image.open(tsrc)); W, H = im.size; icc = im.info.get("icc_profile")
            box = (600, 900) if H > W else ((1200, 800) if W > H else (900, 900))
            im = im.convert("RGB"); im.thumbnail(box, Image.LANCZOS)
            kw = {"format": "JPEG", "quality": 85, "optimize": True}
            if icc: kw["icc_profile"] = icc
            im.save(tdst, **kw); thumb_lock.record(tdst)
            log.append("thumbnail %s -> %s" % (thumb, rel(tdst)))
        else:
            log.append("thumbnail %s -> %s" % (thumb, rel(tdst)))
    else:
        log.append("WARN: thumbnail source not found: %s (add manually)" % thumb)

    # 6. remove draft folder
    if not DRY:
        try: shutil.rmtree(os.path.join(ROOT, "Drafts", slug))
        except Exception:
            os.system('rm -rf "%s"' % os.path.join(ROOT, "Drafts", slug))
    log.append("removed Drafts/%s/" % slug)

    # 7. rebuild the search index so the new country is searchable
    if not DRY:
        try:
            import gen_search_index
            gen_search_index.main()
            log.append("rebuilt search-index.js")
        except Exception as e:
            log.append("WARN: search index not rebuilt (%s) — run tools/gen_search_index.py" % e)
    else:
        log.append("would rebuild search-index.js")

    # 7b. City-map drift (Kevin's 2026-07-31 rule): articles gain bullets after their city map
    #     was built, and the editor can strip the mobile pin overlay. Warn if the live article
    #     no longer matches its map configs so the maps get re-extracted before pushing.
    maps = []
    for cfgp in glob.glob(os.path.join(ROOT, "tools", "city_maps", "*.json")):
        try:
            import json as _json
            c = _json.load(open(cfgp, encoding="utf-8"))
        except Exception:
            continue
        if c.get("article") in ("%s/field-notes.html" % slug, "Drafts/%s/field-notes.html" % slug):
            maps.append((os.path.basename(cfgp)[:-5], c))
    if maps:
        art = read(dst) if os.path.exists(dst) else html   # dry-run: use the in-memory page
        stale = [m for m, c in maps if c.get("article") != "%s/field-notes.html" % slug]
        gone = [(m, p["name"]) for m, c in maps for p in c.get("pois", [])
                if p["name"] not in art]
        nomap = [m for m, c in maps if "city-maps/%s.png" % m not in art]
        log.append("city maps for %s: %d config(s)" % (slug, len(maps)))
        if stale:
            # Repoint them at the live page. This matters beyond tidiness: city_map.py derives
            # the image path depth from article.count("/"), so a config left on Drafts/ would
            # emit ../../Images/... and break every map image on the published page.
            if not DRY:
                import json as _json
                for m, c in maps:
                    if m not in stale: continue
                    p = os.path.join(ROOT, "tools", "city_maps", m + ".json")
                    c["article"] = "%s/field-notes.html" % slug
                    _json.dump(c, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                log.append("    repointed %d map config(s) to %s/: %s" % (len(stale), slug, ", ".join(stale)))
            else:
                log.append("    would repoint %d map config(s) Drafts/ -> %s/: %s" % (len(stale), slug, ", ".join(stale)))
        if nomap: log.append("    ! not embedded in the live page: %s (rebuild with --embed)" % ", ".join(nomap))
        if gone:  log.append("    ! %d pin(s) no longer named in the article, e.g. %s"
                             % (len(gone), "; ".join("%s:%s" % g for g in gone[:3])))
        if not (stale or nomap or gone):
            log.append("    maps look in sync (re-extract anyway if you edited the article recently)")

    # 7c. Post-publish generators + lint (protocol steps 7-8). Separate top-level scripts,
    #     so run them as subprocesses rather than importing.
    if not DRY and not a.skip_generators:
        import subprocess
        for script, why in [("gen_og_images.py", "OG images"), ("add_structured_data.py", "JSON-LD"),
                            ("gen_sitemap.py", "sitemap")]:
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", script)],
                               cwd=ROOT, capture_output=True, text=True)
            if r.returncode == 0:
                log.append("ran %s (%s)" % (script, why))
            else:
                tail = (r.stderr or r.stdout or "").strip().splitlines()
                log.append("WARN: %s failed - run manually. %s" % (script, tail[-1] if tail else ""))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "lint_site.py"), "--ci"],
                           cwd=ROOT, capture_output=True, text=True)
        log.append("lint_site --ci: PASS" if r.returncode == 0
                   else "LINT FAILED - run: py tools/lint_site.py")
    elif DRY:
        log.append("would run gen_og_images / add_structured_data / gen_sitemap / lint_site --ci")

    # 8. VERIFY. Every wiring step above has silently no-opped at least once when the site
    #    markup drifted (the grid-card comment anchor, the removed index flag strip). Assert
    #    the end state instead of trusting the steps, so a publish can never *look* fine
    #    while leaving the country off destinations / the nav / the search index.
    if not DRY:
        problems = []
        d = read(os.path.join(ROOT, "destinations.html"))
        if "location.href='%s/field-notes.html'" % slug not in d:
            problems.append("destinations.html has no grid card for %s" % slug)
        if '"%s":' % iso2.upper() not in d:
            problems.append("destinations.html fieldNotesMap missing %s" % iso2.upper())
        by = parse_countries(d)
        if not any(x[2] == slug for v in by.values() for x in v):
            problems.append("parse_countries() cannot see %s -> it will be MISSING from the nav" % slug)
        missing_nav = [nr for np, nr in live_pages()
                       if '<div class="nav-dropdown' in read(np)
                       and '%s/field-notes.html' % slug not in read(np)]
        if missing_nav:
            problems.append("nav dropdown missing %s on %d page(s), e.g. %s"
                            % (slug, len(missing_nav), ", ".join(missing_nav[:3])))
        if not os.path.exists(dst):
            problems.append("live page %s was not written" % rel(dst))
        if os.path.exists(os.path.join(ROOT, "Drafts", slug)):
            problems.append("Drafts/%s/ still exists" % slug)
        if problems:
            log.append("VERIFY FAILED:")
            log += ["    ! " + p for p in problems]
        else:
            log.append("verified: live page, destinations card + map entry, nav on all pages")

    print(tag + ("\n" + tag).join("- " + x for x in log))
    if not DRY and any("VERIFY FAILED" in x for x in log):
        sys.exit("\nPublish did NOT fully wire up %s — fix the items above before pushing." % slug)
    print("\nNext: python tools/lint_site.py  &&  python tools/gen_sitemap.py  &&  screenshot the 3 pages. Do not push until reviewed.")

if __name__ == "__main__":
    DRY = False
    main()
