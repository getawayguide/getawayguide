#!/usr/bin/env python3
"""Retire a country's field notes when its full guide launches (Kevin, 2026-09-30).

"field notes will go away now with the release of all of these articles at once ... In the future,
the field notes will stay up, link to other pages and will be taken down once I uncheck and launch
the whole country." The Launch tab's Field notes sub-tab holds that choice (keep or retire); this
is what the launch runs when it says retire. The country becomes an In-Depth Guide the way El
Salvador is, and nothing on the site points at the field notes any more:

  1. destinations.html   the country's card opens <slug>/index.html and wears the In-Depth Guide
                         badge, says Read Guide; the map's entry for it opens the same page
  1b. index.html         the home page's Explore tile opens the country page, counts its stories and says
                         Read Guide (Kevin, 2026-10-24). A country with no tile gets one (2026-11-11; North
                         Macedonia had none): in a free cell of the grid's last row, or in the slot of the tile
                         --replace-tile=<slug> names; with the grid full and no --replace-tile, the log says ACTION
                         NEEDED and lists the field-notes tiles that could make way. A new tile gets its photo
                         bands from tools/fit_backgrounds.py index.html (needs the photo server, :5003).
  2. publish_country.py  FULL_GUIDES gains the country, so every nav built from now on lists it
                         as a guide (green dot) and not as field notes
  3. the nav             regenerated on every live page from destinations.html + FULL_GUIDES
  4. every other link    to <slug>/field-notes.html in a live page (other countries' field notes,
                         the home page, posts) now opens <slug>/index.html; a link to a section
                         keeps nothing of the old anchor, the country page has none of them
  5. the page itself     goes to Drafts/.Archive/<slug>/ and its address becomes a 301 to the country page in
                         _redirects (tools/site_archive.py; Kevin, 2026-10-24: "remove any files that are not online
                         anymore like armenia field notes, those belong in the archive folder"), so old links and
                         bookmarks still land and the country folder holds live pages only. Until 2026-10-24 the page
                         was overwritten with a redirect stub that stayed in the folder.
  5a. share images      every live page of the country gets its own og image if it has none: the country page
                         from its destinations card's photo, an article from its card (gen_og_images.ensure; North
                         Macedonia's country page needed a hand edit of PAGE_SRC, 2026-11-10)
  5b. guide cards       a kept field-notes page gets a card under each city heading whose city now has its own
                         guide (tools/guide_cards.py; Kevin, 2026-10-23)
  6b. Explore More       the three related-article cards at the end of every live page are picked again
                         (tools/explore_more.py; Kevin, 2026-10-23)
  6c. dates              the "Last updated ... · Contains affiliate links" footnote before Explore More and
                         the date under the hero description (not on field notes) on every live article
                         (tools/updated_line.py)
  6. posts + count      tools/sync_guides.py: its articles join the posts page's Guides section, its
                         field-notes card leaves, and the home hero's "N and counting." is recounted
  7. sitemap + search    tools/gen_sitemap.py and tools/gen_search_index.py run again
  7b. search photos      tools/build_search_thumbs.py: the rebuilt index has no thumbnails until it runs
  7c. draft navs         tools/update_draft_nav.py rebuilds the menu on every draft, nav block only, byte-safe
  9. currency          the converter on every live itinerary gets today's rates (tools/currency_widget.py)

It refuses to run until <slug>/index.html is live: retiring first would point the whole site at a
page that does not exist yet. archive/ is never touched (see the publish protocol memory).

--keep-field-notes (Kevin, 2026-10-07, Greece: "the only greek articles that are live are field notes, meteora
and thessaloniki"): the country becomes an In-Depth Guide (steps 1, 2, 3, 5b, 6, 7) but its field notes stay a live
page that the site keeps linking to (steps 4 and 5 are skipped). That is the "Keep the field notes live" launch.

    python tools/retire_field_notes.py armenia --dry-run     # what it would change (the tab shows this)
    python tools/retire_field_notes.py armenia               # at launch, after the guide is live
    python tools/retire_field_notes.py greece --keep-field-notes   # a guide launch that keeps the notes live
    python tools/retire_field_notes.py north-macedonia --keep-field-notes --replace-tile=kosovo   # its home tile takes Kosovo's
"""
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
DOMAIN = "https://getawayguide.io/"
NOT_SITE = {"editor.html"}                     # the editor is a tool that lives in the repo, not a page


def live_pages():
    out = subprocess.run(["git", "ls-files", "*.html"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout.split("\n")
    pages = [p for p in out if p and not p.startswith(("archive/", "Drafts/", ".tmp/", "tools/")) and p not in NOT_SITE
             and (ROOT / p).is_file()]               # a page archived since the last commit is still in the index
    # a page published in this same launch may not be in the index yet
    for p in ROOT.glob("*/*.html"):
        r = p.relative_to(ROOT).as_posix()
        if r not in pages and not r.startswith(("archive/", "Drafts/", ".tmp/", "tools/")) and r not in NOT_SITE:
            pages.append(r)
    return pages


def _read(p):
    return io.open(ROOT / p, encoding="utf-8", newline="").read()


def link_re(slug):
    """any reference to the field notes: ../slug/field-notes.html, slug/field-notes.html,
    https://getawayguide.io/slug/field-notes.html, with or without an #anchor"""
    return re.compile(r"(?P<pre>(?:\.\./)*|%s)%s/field-notes\.html(?P<hash>#[\w-]*)?" % (re.escape(DOMAIN), re.escape(slug)))


def facts(slug):
    """name, iso2 and continent of the country, from destinations.html"""
    d = _read("destinations.html")
    m = re.search(r'"([A-Z]{2})": \{ name: "([^"]+)",\s*url: "%s/(?:field-notes|index)\.html"' % re.escape(slug), d)
    c = re.search(r'data-continent="([a-z]+)" onclick="location\.href=\'%s/(?:field-notes|index)\.html\'"' % re.escape(slug), d)
    return (m.group(2) if m else slug.replace("-", " ").title(), m.group(1).lower() if m else "", c.group(1) if c else "")


def plan(slug):
    page = "%s/field-notes.html" % slug
    fn = ROOT / page
    rx = link_re(slug)
    inbound, nav_pages = [], 0
    for p in live_pages():
        if p == page:
            continue
        h = _read(p)
        nav = re.search(r'<div class="nav-dropdown[ "].*?</div>\s*</li>', h, re.S)
        n_nav = len(rx.findall(nav.group(0))) if nav else 0
        n_all = len(rx.findall(h))
        if p.startswith(slug + "/"):                  # a sibling links to it as plain field-notes.html
            n_all += len(re.findall(r'href="field-notes\.html(?:#[\w-]*)?"', h))
        if n_nav:
            nav_pages += 1
        if n_all - n_nav:
            inbound.append({"page": p, "links": n_all - n_nav})
    name, iso2, cont = facts(slug)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("pc", TOOLS / "publish_country.py")
        pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)
        in_full = any(g[3] == "%s/index.html" % slug for g in pc.FULL_GUIDES)
    except Exception:
        in_full = False
    sm = (ROOT / "sitemap.xml").read_text(encoding="utf-8") if (ROOT / "sitemap.xml").is_file() else ""
    import site_archive
    stub = (fn.is_file() and 'http-equiv="refresh"' in _read(page)) or (not fn.is_file() and site_archive.redirected(page))
    return {"country": slug, "name": name, "iso2": iso2, "continent": cont, "page": page,
            "live": fn.is_file() and not stub, "retired": stub,
            "country_page": "%s/index.html" % slug, "country_page_live": (ROOT / slug / "index.html").is_file(),
            "nav_pages": nav_pages, "inbound": inbound, "in_full_guides": in_full,
            "in_sitemap": ("/%s/field-notes.html" % slug) in sm,
            "steps": [
                "The %s card on the destinations page and its map entry open the country page, with the In-Depth Guide badge" % name,
                ("%s joins the nav as a guide (green dot) on all %d pages that carry the nav" % (name, nav_pages)) if nav_pages
                else "%s joins the nav as a guide (green dot) once pages carry the nav (none do yet)" % name,
                "%d other link(s) on %d page(s) move to the country page" % (sum(i["links"] for i in inbound), len(inbound)),
                "%s moves to Drafts/.Archive and its address 301s to %s/index.html, so old links still land" % (page, slug),
                "The sitemap and site search drop it",
                "Home: " + _home_tile(slug, dry=True)[0][-1].replace("index.html: ", "")]}


STUB = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{name}</title>
<meta name="robots" content="noindex">
<link rel="canonical" href="{domain}{slug}/index.html">
<meta http-equiv="refresh" content="0; url=index.html">
<script>location.replace('index.html');</script>
</head>
<body><p>The {name} field notes became a full guide: <a href="index.html">{name}</a>.</p></body>
</html>
"""


def apply(slug, dry=False, force=False, keep=False, replace_tile=None):
    p = plan(slug)
    if p["retired"] and not keep:
        return ["%s is already retired" % p["page"]]
    if not p["country_page_live"] and not force and not dry:
        raise SystemExit("%s is not live yet: publish the country page first, then retire the field notes" % p["country_page"])
    log = []
    name, iso2, cont = p["name"], p["iso2"], p["continent"]
    if not (iso2 and cont):
        raise SystemExit("could not read %s's flag code and continent from destinations.html" % slug)
    # 1. destinations: the card and the map entry
    d = _read("destinations.html")
    d2 = d.replace("onclick=\"location.href='%s/field-notes.html'\"" % slug, "onclick=\"location.href='%s/index.html'\"" % slug)
    d2 = re.sub(r'(url: ")%s/field-notes\.html(")' % re.escape(slug), r"\g<1>%s/index.html\2" % slug, d2)
    card = re.search(r"<div class=\"home-dest-card\"[^>]*location\.href='%s/index\.html'[^>]*>.*?<div class=\"art-card-info\">" % re.escape(slug), d2, re.S)
    if card and "art-card-badge" not in card.group(0):
        new = card.group(0).replace('<div class="art-card-info">',
                                    '<span class="art-card-badge"><span class="art-card-badge-dot"></span>In-Depth Guide</span>\n        <div class="art-card-info">')
        d2 = d2.replace(card.group(0), new)
    # within the country's own card: a card that already says Read Guide let .*? run on into the next card and turn
    # Australia's Read Notes into Read Guide (2026-11-01, a rerun for Armenia's kept field notes)
    btn = re.search(r"(location\.href='%s/index\.html'(?:(?!home-dest-card).)*?<span class=\"art-card-btn\">)(?:Read Notes|Explore)(</span>)" % re.escape(slug), d2, re.S)
    if btn:                                           # a guide's card says Read Guide (Kevin, 2026-10-24)
        d2 = d2[:btn.start()] + btn.group(1) + "Read Guide" + btn.group(2) + d2[btn.end():]
    if d2 != d:
        log.append("destinations.html: card + map entry -> %s/index.html, In-Depth Guide badge" % slug)
        if not dry:
            io.open(ROOT / "destinations.html", "w", encoding="utf-8", newline="").write(d2)
    # 1b. the home page's tile for the country opens the country page and reads as a guide (Kevin, 2026-10-24:
    #     "Turkiye card on home should link to the index card")
    #     A guide country with no tile gets one (North Macedonia had none, 2026-11-10): in a free cell, or in the slot of
    #     the tile --replace-tile names; with the grid full and no --replace-tile, the log says ACTION NEEDED.
    tile_log, tile_new = _home_tile(slug, dry, replace_tile)
    log += tile_log
    if tile_new:
        log += fit_home_tiles(dry)
    # 2. FULL_GUIDES
    pcf = TOOLS / "publish_country.py"
    src = io.open(pcf, encoding="utf-8", newline="").read()
    m = re.search(r"^FULL_GUIDES = \[(.*?)\]", src, re.M)      # no $: the file may end lines in CRLF
    if m and ('"%s/index.html"' % slug) not in m.group(1):
        guides = re.findall(r'\("([^"]+)", "([a-z]{2})", "([a-z]+)", "([^"]+)"\)', m.group(1))
        guides.append((name, iso2, cont, "%s/index.html" % slug))
        guides.sort(key=lambda g: g[0].lower())
        line = "FULL_GUIDES = [%s]" % ", ".join('("%s", "%s", "%s", "%s")' % g for g in guides)
        log.append("publish_country.py: FULL_GUIDES += %s" % name)
        if not dry:
            io.open(pcf, "w", encoding="utf-8", newline="").write(src[:m.start()] + line + src[m.end():])
    # 3. the nav on every live page
    if not dry:
        import importlib.util
        spec = importlib.util.spec_from_file_location("pc", pcf)
        pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)
        by_cont = pc.parse_countries(_read("destinations.html"))
        n = 0
        for r in live_pages():
            cur = _read(r)
            if '<div class="nav-dropdown' not in cur:
                continue
            nl = "\r\n" if "\r\n" in cur else "\n"           # a CRLF page keeps its line endings
            block = (pc.build_nav("../" if "/" in r else "", by_cont) + "\n    </li>").replace("\n", nl)
            new = pc.NAV_RE.sub(lambda _m: block, cur, count=1)
            if new != cur:
                io.open(ROOT / r, "w", encoding="utf-8", newline="").write(new)
                n += 1
        log.append("nav regenerated on %d pages" % n)
    else:
        log.append("nav regenerated on the %d pages that carry it" % p["nav_pages"])
    if keep:                                          # the field notes stay a live page: links to them stay
        log.append("field notes kept live: other links and %s left as they are" % p["page"])
        return log + _finish(dry, p["page"])
    # 4. every other link
    rx = link_re(slug)
    moved = 0
    for r in live_pages():
        if r == p["page"]:
            continue
        cur = _read(r)
        new = rx.sub(lambda mm: mm.group("pre") + "%s/index.html" % slug, cur)
        if r.startswith(slug + "/"):
            new = re.sub(r'href="field-notes\.html(?:#[\w-]*)?"', 'href="index.html"', new)
        if new != cur:
            moved += 1
            if not dry:
                io.open(ROOT / r, "w", encoding="utf-8", newline="").write(new)
    log.append("links moved to %s/index.html on %d page(s)" % (slug, moved))
    # 5. the page goes to the archive; its address becomes a 301 in _redirects (2026-10-24, not a stub any more)
    import site_archive
    log += site_archive.retire(p["page"], "%s/index.html" % slug, dry)
    return log + _finish(dry, p["page"])


def _tile_re(slug):
    return re.compile(r'<a class="tile" href="%s/(?:field-notes|index)\.html">.*?</a>' % re.escape(slug), re.S)


def _stories(slug):
    return len([p for p in (ROOT / slug).glob("*.html") if p.name not in ("index.html",) and 'http-equiv="refresh"' not in p.read_text(encoding="utf-8")[:4000]])


def tiles_grid(h):
    """the Explore by Destination grid on the home page: (start, end, columns, [tile matches]) or None. The grid is
    <div class="tiles five">: five columns on a desktop (artifact.css .tiles; "four" makes it four), two full rows today
    (nine countries and the map tile)."""
    g = re.search(r'<div class="tiles(?: ([\w ]*))?">', h)
    if not g:
        return None
    depth = 0
    for t in re.finditer(r"<(/?)div\b[^>]*>", h[g.start():]):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            end = g.start() + t.end()
            break
    else:
        return None
    cols = 4 if "four" in (g.group(1) or "").split() else 5
    tiles = list(re.finditer(r'<a class="tile(?: [^"]*)?" href="[^"]*">.*?</a>', h[g.start():end], re.S))
    return g.start(), end, cols, tiles


def new_tile(slug, name, n, bgfit=None):
    """a guide country's tile, built the way the others are: the destinations card's photo and framing (its dest-bg
    style, byte for byte, as every tile on the page has it), "N stories" and Read Guide"""
    d = _read("destinations.html")
    c = re.search(r"location\.href='%s/(?:field-notes|index)\.html'[^>]*>\s*<div class=\"dest-bg\" style=\"([^\"]*)\"" % re.escape(slug), d)
    style = c.group(1) if c else ("background-image:url('Images/web/dest-cards/%s.jpg');background-size:cover;"
                                  "background-position:50%% 50%%" % slug)
    return ('<a class="tile" href="%s/index.html"><div class="tile-bg" style="%s"%s></div><div class="veil"></div>'
            '<div class="lab"><div class="serif nm">%s</div><div class="sub">%d stories</div><span class="rn">Read Guide</span>'
            '</div></a>' % (slug, style, (' data-bgfit="%s"' % bgfit) if bgfit else "", name, n))


def home_tile(slug, dry=False, replace=None):
    """index.html's Explore tile for `slug`: href to the country page, "N stories" for the sub line, Read Guide"""
    return _home_tile(slug, dry, replace)[0]


def _home_tile(slug, dry=False, replace=None):
    """(log, wrote a new tile). A tile that exists is rewired (href, story count, Read Guide). A guide country with no
    tile gets one (North Macedonia, 2026-11-10, had none and the launch said only "no tile"):
      * replace=<slug>: it takes that country's tile and its slot (the data-bgfit number, so the page's per-band photo
        rules stay keyed right until fit_backgrounds.py recuts them). Kevin picks it, as he did for North Macedonia:
        "replace Kosovo under Explore by Destination with North Macedonia"
      * else, if the grid's last row has room, it goes in before the map tile
      * else the grid is full (adding one would start a row of one): nothing is written and the log says it is needed
        and which tiles could make way, so the launch is rerun with --replace-tile=<slug>
    """
    h = _read("index.html")
    m = _tile_re(slug).search(h)
    n = _stories(slug)
    name = facts(slug)[0]
    if m:
        t = m.group(0).replace('href="%s/field-notes.html"' % slug, 'href="%s/index.html"' % slug)
        t = re.sub(r'<div class="sub">[^<]*</div>', '<div class="sub">%d stories</div>' % n, t, count=1)
        t = t.replace('<span class="rn">Read Notes</span>', '<span class="rn">Read Guide</span>')
        if replace and replace != slug:
            log = ["index.html: %s already has a tile, so --replace-tile=%s was not used" % (slug, replace)]
        else:
            log = []
        if t == m.group(0):
            return log + ["index.html: the %s tile already opens the country page" % slug], False
        if not dry:
            io.open(ROOT / "index.html", "w", encoding="utf-8", newline="").write(h[:m.start()] + t + h[m.end():])
        return log + ["index.html: %s tile -> %s/index.html, %d stories, Read Guide" % (slug, slug, n)], False
    grid = tiles_grid(h)
    if not grid:
        return ["ACTION NEEDED index.html: no tile for %s and no Explore by Destination grid found to add one to" % slug], False
    g0, g1, cols, tiles = grid
    if replace:
        old = _tile_re(replace).search(h, g0, g1)
        if not old:
            return ["ACTION NEEDED index.html: no tile for %s, and --replace-tile=%s names no tile on the home page" % (slug, replace)], False
        bf = re.search(r'data-bgfit="(\d+)"', old.group(0))
        t = new_tile(slug, name, n, bf.group(1) if bf else None)
        if not dry:
            io.open(ROOT / "index.html", "w", encoding="utf-8", newline="").write(h[:old.start()] + t + h[old.end():])
        return ["index.html: %s tile replaced by a %s tile -> %s/index.html, %d stories, Read Guide%s"
                % (replace, slug, slug, n, "" if dry else " (photo bands recut below)")], True
    if len(tiles) % cols:                              # the last row has a gap: fill it, before the map tile
        mp = next((t for t in tiles if "map-live" in t.group(0) or 'href="destinations.html"' in t.group(0)), None)
        at = g0 + (mp.start() if mp else tiles[-1].end())
        t = new_tile(slug, name, n)
        if not dry:
            io.open(ROOT / "index.html", "w", encoding="utf-8", newline="").write(h[:at] + t + h[at:])
        return ["index.html: added a %s tile under Explore by Destination -> %s/index.html, %d stories, Read Guide%s"
                % (slug, slug, n, "" if dry else " (photo bands recut below)")], True
    notes = [re.search(r'href="([a-z-]+)/field-notes\.html"', t.group(0)) for t in tiles]
    cands = [x.group(1) for x in notes if x]
    return ["ACTION NEEDED index.html: %s has no tile under Explore by Destination and the grid is full (%d tiles, %d a row). "
            "Pick one to make way and rerun with --replace-tile=<slug>%s"
            % (slug, len(tiles), cols, ("; field-notes tiles: " + ", ".join(cands)) if cands else "")], False


def fit_home_tiles(dry=False):
    """a new tile needs its own photo files per window band, like every other tile: tools/fit_backgrounds.py index.html
    (CLAUDE.md: run it after adding a card by hand). It loads the page through the photo server (:5003)."""
    if dry:
        return ["would run fit_backgrounds.py index.html (the new tile's photo bands)"]
    try:
        r = subprocess.run([sys.executable, str(TOOLS / "fit_backgrounds.py"), "index.html"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=1800)
        tail = (r.stdout or "").strip().splitlines()
        if r.returncode == 0:
            return ["fit_backgrounds.py index.html: %s" % (tail[-1] if tail else "done")]
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return ["WARN: fit_backgrounds.py index.html failed (%s) - run it with the photo server up" % (err[-1] if err else r.returncode)]
    except Exception as e:
        return ["WARN: fit_backgrounds.py index.html not run (%s) - run it with the photo server up" % e]


def country_pages(slug):
    """the country's live pages (country page, articles, kept field notes), redirect stubs left out"""
    d = ROOT / slug
    return [p.relative_to(ROOT).as_posix() for p in sorted(d.glob("*.html"))
            if 'http-equiv="refresh"' not in p.read_text(encoding="utf-8", errors="replace")[:4000]] if d.is_dir() else []


def search_thumbs(dry=False):
    """tools/build_search_thumbs.py after the search index is rebuilt (it reuses every thumbnail that exists)"""
    try:
        r = subprocess.run([sys.executable, str(TOOLS / "build_search_thumbs.py")] + (["--dry-run"] if dry else []), cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    except Exception as e:
        return ["WARN: build_search_thumbs.py not run (%s) - run it by hand" % e]
    out = (r.stdout or "").strip().splitlines()
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return ["WARN: build_search_thumbs.py failed (%s) - run it by hand" % (err[-1] if err else r.returncode)]
    cut = sum(1 for ln in out if ln.strip().startswith(("cut ", "would cut")))
    missing = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
    log = ["search thumbnails: %s (%d new)" % (out[-1].strip() if out else "-", cut)]
    if missing:
        log.append("search thumbnails: %d without a photo, e.g. %s" % (len(missing), missing[0]))
    return log


def draft_navs(dry=False):
    """every draft's nav rebuilt from the launched menu, nav block only (tools/update_draft_nav.py)"""
    try:
        r = subprocess.run([sys.executable, str(TOOLS / "update_draft_nav.py")] + (["--dry-run"] if dry else []), cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    except Exception as e:
        return ["WARN: draft navs not refreshed (%s) - run tools/update_draft_nav.py" % e]
    out = (r.stdout or "").strip().splitlines()
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return ["WARN: update_draft_nav.py failed (%s) - run it by hand" % (err[-1] if err else r.returncode)]
    return ["draft navs: " + (out[-1].strip() if out else "-")]


def _finish(dry, page=None):
    log = []
    # 5c. the country folder holds live pages only: a stub or stray file goes to Drafts/.Archive (tools/site_archive.py;
    #     Kevin, 2026-10-24: "remove any files that are not online anymore ... Add this to the launch process")
    if page and "/" in page:
        import site_archive
        log += ["folder: " + ln for ln in site_archive.sweep([page.split("/")[0]], dry)]
    # 5a. share images (North Macedonia, 2026-11-10: its country page had none until gen_og_images.PAGE_SRC was edited by
    #     hand): every live page of the country gets its own og image if it has none, the country page from its
    #     destinations card's photo, an article from its country-page card; a page that already has one is left alone
    if page and "/" in page:
        try:
            import gen_og_images
            log += ["og: " + ln for ln in gen_og_images.ensure(country_pages(page.split("/")[0]), dry)]
        except Exception as e:
            log.append("WARN: share images not checked (%s) - run tools/gen_og_images.py" % e)
    # 5b. guide cards (Kevin, 2026-10-23: "add field notes badges as part of launch steps"): a field-notes section
    #     whose city now has its own published guide gets the card under its heading (tools/guide_cards.py). A
    #     retired page is a redirect by now, so only a kept page gets them.
    if page and (ROOT / page).is_file():
        import guide_cards
        if guide_cards.published(ROOT / page):
            rep = guide_cards.process(ROOT / page, dry)
            for sec, guide, what in rep:
                log.append("guide card on %s#%s -> %s: %s" % (page, sec, guide, what))
            if not rep:
                log.append("guide cards: no section of %s links to a published guide" % page)
    # 5d. cross-links (Kevin, 2026-11-10: "I want the articles to cross link ... make the crosslink a tool that can be
    #     replicated"): the first bare mention of a sibling article's place in each section links to it
    #     (tools/crosslink.py). Existing links are never touched: no relink pass, so a Google Maps pin or a booking
    #     link stays as it is (Kevin, 2026-11-10: "map links should be linked to map pins like google maps or booking
    #     links"). Repoint suggestions go to .tmp/crosslink/<slug>-review.md for Kevin, never guessed.
    if page and "/" in page:
        try:
            import crosslink
            cx = crosslink.run(page.split("/")[0], apply=not dry, mentions=True)      # mentions only: relinks removed
            log += ["crosslink: " + ln for ln in cx["log"]]
        except Exception as e:
            log.append("WARN: cross-links not run (%s) — run tools/crosslink.py %s --apply" % (e, page.split("/")[0]))
    # 6. the posts page lists the country's articles under Guides (a retired country's field-notes card
    #    leaves), and the home hero's "N and counting." is recounted (tools/sync_guides.py; Kevin, 2026-10-02)
    import sync_guides
    log += sync_guides.run(dry)
    # 6b. Explore More (Kevin, 2026-10-23: "when I publish a new article the website should re-evaluate these
    #     thumbnails"): the three cards at the end of every live page are picked again, pins kept
    import explore_more as xm
    cat, pins, n = xm.catalog(), xm.load_pins(), 0
    for r in xm.live_pages():
        n += xm.apply_page(r, cat, pins, dry)[1] == "ok"
    log.append("Explore More cards picked again on %d pages" % n)
    # 6c. every live article carries "Last updated: <Month YYYY> · Based on firsthand experience · Contains affiliate
    #     links" right before Explore More, and the date under its hero description, both from its
    #     dateModified (tools/updated_line.py; Kevin, 2026-11-08). The guides launched with the country get them here.
    r = subprocess.run([sys.executable, str(TOOLS / "updated_line.py"), "--root", str(ROOT)] + ([] if dry else ["--apply"]),
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    head = (r.stdout or "").strip().splitlines()
    log.append(("updated_line.py: %s" % (head[0] if head else "-")) if r.returncode == 0
               else "WARN: updated_line.py failed - run it manually")
    # 7. sitemap + search
    for tool in ("gen_sitemap.py", "gen_search_index.py"):
        if not (TOOLS / tool).is_file():
            continue
        log.append("ran %s" % tool)
        if not dry:
            subprocess.run([sys.executable, str(TOOLS / tool)], cwd=ROOT, capture_output=True, text=True)
    # 7b. the search results' photos: gen_search_index writes the index without them, so every rebuild drops all of them
    #     until tools/build_search_thumbs.py runs (North Macedonia, 2026-11-10: run by hand). It reuses a thumbnail that
    #     exists and cuts only the missing ones.
    log += search_thumbs(dry)
    # 7c. the drafts' nav: the launch changed the menu (a new guide, its green dot), so every draft's copy is stale. Only
    #     the nav block is rewritten, byte-safe, so a draft with uncommitted work is safe to touch (tools/update_draft_nav.py)
    log += draft_navs(dry)
    # 8. Cloudflare Web Analytics beacon in the head of every live page, the new guide pages included
    #    (idempotent; a no-op while CF_ANALYTICS_TOKEN is missing from .env)
    log.append("ran analytics_snippet.py --apply")
    if not dry:
        subprocess.run([sys.executable, str(TOOLS / "analytics_snippet.py"), "--apply"],
                       cwd=ROOT, capture_output=True, text=True)
    # 9. the currency converter on the live itineraries, the one launched with the country included
    #    (tools/currency_widget.py; Kevin, 2026-11-08). No network keeps the saved rates; it never fails the launch.
    r = subprocess.run([sys.executable, str(TOOLS / "currency_widget.py"), "--live-only"]
                       + ([] if dry else ["--refresh-rates", "--apply"]),
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = (r.stdout or "").strip().splitlines()
    log.append(("currency converter: %s" % (lines[-1] if lines else "-")) if r.returncode == 0
               else "WARN: currency_widget.py failed - run it manually")
    return log


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # this box's console is cp1252: ’ came out as �
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    slug, dry = args[0], "--dry-run" in sys.argv
    if "--json" in sys.argv:
        sys.stdout.buffer.write(json.dumps(plan(slug), ensure_ascii=False).encode("utf-8"))
        return 0
    rt = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--replace-tile=")), None)
    for line in apply(slug, dry=dry, force="--force" in sys.argv, keep="--keep-field-notes" in sys.argv, replace_tile=rt):
        print(("[dry] " if dry else "") + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
