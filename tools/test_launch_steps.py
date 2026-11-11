#!/usr/bin/env python3
"""The launch steps the North Macedonia launch (2026-11-10) had to do by hand, pinned down on a scratch site tree only
(memory feedback_test_cleanup_never_in_real_folders: every module's ROOT is pointed at a temp folder, and the real tree is
never written).

  1. home tile     retire_field_notes._home_tile: rewires a tile that exists; adds one to a free cell before the map
                   tile; with the grid full writes nothing and says ACTION NEEDED; --replace-tile takes that tile's slot
                   and data-bgfit number; idempotent; CRLF kept; a dry run writes nothing
  2. share images  gen_og_images.ensure: a page with its own image is left alone byte for byte; a country page gets
                   <slug>-index.jpg from its destinations card's photo, an article <slug>-<page>.jpg from its card, a
                   page with no og tag gets one; 1200x630, ICC profile carried; idempotent; dry run writes nothing
  3. search thumbs build_search_thumbs restores the thumbs a gen_search_index rebuild drops, reusing existing files
  4. Explore More  a block whose closing marker an editor save dropped is replaced, never doubled; two stacked copies
                   become one and the text between them survives (the old pattern would have cut it); either attribute
                   order; orphan markers go; idempotent; CRLF kept
  5. draft navs    update_draft_nav.nav_swap/refresh: only the menu changes, byte for byte outside it, the whitespace
                   before </li> and the menu's own line endings kept, an up-to-date page untouched, no temp file left
  6. wiring        both launch tools run the steps, in an order that works

    python tools/test_launch_steps.py
"""
import io
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REAL_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = passed + bool(ok), failed + (not ok)
    if not ok:
        sys.stdout.buffer.write(("FAIL %s %s\n" % (name, detail)).encode("utf-8"))


def wr(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return p


def rd(root, rel):
    with io.open(Path(root) / rel, encoding="utf-8", newline="") as f:
        return f.read()


def icc_bytes():
    from PIL import ImageCms
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def photo(path, size, color=(30, 120, 200)):
    from PIL import Image
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, quality=90, icc_profile=icc_bytes())


# ---------------------------------------------------------------- the scratch site
def tile(slug, page, name, sub, rn, n):
    return ('<a class="tile" href="%s/%s"><div class="tile-bg" style="background-image:url(\'Images/web/dest-cards/%s.jpg\');'
            'background-size:cover;background-position:50%% 50%%" data-bgfit="%d"></div><div class="veil"></div><div class="lab">'
            '<div class="serif nm">%s</div><div class="sub">%s</div><span class="rn">%s</span></div></a>' % (slug, page, slug, n, name, sub, rn))


MAP_TILE = ('<a class="tile map-live" href="destinations.html"><div class="tile-bg" style="background-image:url(\'Images/web/map.png\');'
            'background-size:cover" data-bgfit="%d"></div><div class="veil"></div><div class="lab"><div class="serif nm"><em>Explore</em> '
            'the Map</div><div class="sub">All destinations</div><span class="rn">Open Map</span></div></a>')


def home(tiles, nl="\n"):
    return nl.join(['<!doctype html>', '<html><head><title>Home</title></head><body>', '  <div class="sec pad">',
                    '    <div class="sec-head"><div><div class="serif sec-h">Explore by <em>Destination</em></div></div></div>',
                    '    <div class="tiles five">%s</div>' % "".join(tiles), '  </div>',
                    '  <div class="sec pad"><div class="cards"><a href="x.html">x</a></div></div>', '</body></html>', ''])


def destinations():
    cards = []
    for slug, name, pos in (("albania", "Albania", "50% 50%"), ("kosovo", "Kosovo", "50% 60%"),
                            ("north-macedonia", "North Macedonia", "50% 45%"), ("peru", "Peru", "50% 42%")):
        cards.append('      <div class="home-dest-card" data-continent="europe" onclick="location.href=\'%s/field-notes.html\'" style="cursor:pointer">\n'
                     '        <div class="dest-bg" style="background-image:url(\'Images/web/dest-cards/%s.jpg\');background-size:cover;background-position:%s"></div>\n'
                     '        <div class="art-card-info"><p class="art-card-name">%s</p><span class="art-card-btn">Read Notes</span></div>\n      </div>' % (slug, slug, pos, name))
    js = ('  "AL": { name: "Albania",        url: "albania/field-notes.html" },\n'
          '  "XK": { name: "Kosovo",         url: "kosovo/field-notes.html" },\n'
          '  "MK": { name: "North Macedonia", url: "north-macedonia/field-notes.html" },\n'
          '  "PE": { name: "Peru",           url: "peru/field-notes.html" }\n')
    return "<html><body>\n" + "\n".join(cards) + "\n<script>var m = {\n" + js + "};</script></body></html>\n"


def build_site(root):
    wr(root, "destinations.html", destinations())
    wr(root, "styles.css", "body{}\n")
    for f in ("index.html", "field-notes.html", "skopje.html", "ohrid.html"):
        wr(root, "north-macedonia/" + f, "<html><head></head><body>%s</body></html>\n" % f)


# ---------------------------------------------------------------- 1. home tile
def test_home_tile(root):
    import retire_field_notes as rf
    rf.ROOT = Path(root)
    build_site(root)
    four = [tile("albania", "field-notes.html", "Albania", "Field Notes", "Read Notes", 1),
            tile("kosovo", "field-notes.html", "Kosovo", "Field Notes", "Read Notes", 2),
            tile("peru", "field-notes.html", "Peru", "Field Notes", "Read Notes", 3)]

    # a) the tile exists (field notes): rewired, then a no-op
    wr(root, "index.html", home(four + [tile("north-macedonia", "field-notes.html", "North Macedonia", "Field Notes", "Read Notes", 4),
                                        MAP_TILE % 5]))
    log, new = rf._home_tile("north-macedonia")
    h = rd(root, "index.html")
    check("rewire: href", 'href="north-macedonia/index.html"' in h and "north-macedonia/field-notes.html" not in h, log)
    check("rewire: 3 stories, Read Guide", "North Macedonia</div><div class=\"sub\">3 stories</div><span class=\"rn\">Read Guide" in h, h[-900:])
    check("rewire: not a new tile", new is False)
    log2, _ = rf._home_tile("north-macedonia")
    check("rewire: idempotent", rd(root, "index.html") == h and "already opens" in log2[-1], log2)

    # b) no tile, the last row has room: added before the map tile, built from the destinations card's style
    base = home(four + [MAP_TILE % 4])
    wr(root, "index.html", base)
    log, new = rf._home_tile("north-macedonia", dry=True)
    check("add dry: nothing written", rd(root, "index.html") == base and new is True and "added" in log[0], log)
    log, new = rf._home_tile("north-macedonia")
    h = rd(root, "index.html")
    t = re.search(r'<a class="tile" href="north-macedonia/index\.html">.*?</a>', h, re.S)
    check("add: tile written", t is not None and new is True, log)
    check("add: before the map tile", t and h.index(t.group(0)) < h.index("tile map-live"))
    check("add: destinations card's framing", t and "background-position:50% 45%" in t.group(0)
          and "Images/web/dest-cards/north-macedonia.jpg" in t.group(0), t and t.group(0))
    check("add: Read Guide, 3 stories", t and "3 stories" in t.group(0) and "Read Guide" in t.group(0))
    check("add: only the tile was inserted", t and h.replace(t.group(0), "", 1) == base)
    check("add: one tile", h.count('href="north-macedonia/index.html"') == 1)
    log2, new2 = rf._home_tile("north-macedonia")
    check("add: rerun is a no-op", rd(root, "index.html") == h and new2 is False, log2)

    # c) no tile, grid full (10): nothing written, ACTION NEEDED with the field-notes tiles
    full = [tile("c%d" % i, "index.html", "C%d" % i, "3 stories", "Read Guide", i) for i in range(6)]
    base = home(full + four + [MAP_TILE % 10])
    wr(root, "index.html", base)
    log, new = rf._home_tile("north-macedonia")
    check("full: nothing written", rd(root, "index.html") == base and new is False)
    check("full: ACTION NEEDED + candidates", log[0].startswith("ACTION NEEDED") and "--replace-tile" in log[0]
          and "albania, kosovo, peru" in log[0], log)

    # d) --replace-tile=kosovo: the slot and its data-bgfit number, nothing else moves
    log, new = rf._home_tile("north-macedonia", replace="kosovo")
    h = rd(root, "index.html")
    t = re.search(r'<a class="tile" href="north-macedonia/index\.html">.*?</a>', h, re.S)
    old = re.search(r'<a class="tile" href="kosovo/field-notes\.html">.*?</a>', base, re.S).group(0)
    check("replace: kosovo gone, NM in", "kosovo/" not in h and t is not None and new is True, log)
    bf = re.search(r'data-bgfit="\d+"', old).group(0)
    check("replace: same slot, same bgfit", t and bf in t.group(0) and h == base.replace(old, t.group(0)), (bf, t and t.group(0)))
    log2, _ = rf._home_tile("north-macedonia", replace="kosovo")
    check("replace: rerun no-op", rd(root, "index.html") == h and "already opens" in log2[-1], log2)

    # e) --replace-tile naming no tile: nothing written
    wr(root, "index.html", base)
    log, new = rf._home_tile("north-macedonia", replace="atlantis")
    check("replace missing: refused", rd(root, "index.html") == base and log[0].startswith("ACTION NEEDED"), log)

    # f) CRLF home page keeps its endings
    base = home(four + [MAP_TILE % 4], nl="\r\n")
    wr(root, "index.html", base)
    rf._home_tile("north-macedonia")
    h = rd(root, "index.html")
    check("crlf: no bare LF", h.count("\n") == h.count("\r\n") and h.count("\r\n") == base.count("\r\n"))

    # g) tiles_grid finds the right grid on a "four" grid too
    g = rf.tiles_grid(home(four).replace("tiles five", "tiles four"))
    check("grid: four columns", g and g[2] == 4 and len(g[3]) == 3)


# ---------------------------------------------------------------- 2. share images
OG_HEAD = ('<html><head>\n<meta property="og:title" content="T">\n<meta property="og:description" content="D">\n'
           '%s</head><body><p>x</p></body></html>\n')


def og_tags(url):
    return ('<meta property="og:image" content="%s">\n<meta property="og:image:width" content="1200">\n'
            '<meta property="og:image:height" content="630">\n<meta name="twitter:image" content="%s">\n' % (url, url))


def test_share_images(root):
    import gen_og_images as og, dest_card_source as dcs, thumb_lock as tl
    from PIL import Image
    og.ROOT = dcs.ROOT = tl.ROOT = str(root)
    tl.MANIFEST = os.path.join(str(root), "tools", ".thumb-manifest.json")
    os.makedirs(os.path.dirname(tl.MANIFEST), exist_ok=True)
    dcs._DEST = dcs._CSS = None
    build_site(root)
    D = og.DOMAIN + "/Images/web/og/"
    # the destinations card photo: thumb + its original, so dest_card_source matches them
    photo(Path(root) / "Images/dest-cards/north-macedonia_1.jpg", (1800, 1200), (200, 90, 40))
    photo(Path(root) / "Images/web/dest-cards/north-macedonia.jpg", (600, 900), (200, 90, 40))
    # an article card photo on the country page, and its -3x derivative
    photo(Path(root) / "Images/web/North Macedonia/card-ohrid.jpg", (700, 467), (20, 160, 90))
    photo(Path(root) / "Images/web/North Macedonia/card-ohrid-3x.jpg", (1600, 1067), (20, 160, 90))
    wr(root, "north-macedonia/index.html", (OG_HEAD % og_tags(D + "north-macedonia-index.jpg")).replace("\n", "\r\n").replace(
        "<p>x</p>", '<div class="country-article-card" onclick="location.href=\'ohrid.html\'" style="background-image:url(\'../Images/web/North%20Macedonia/card-ohrid.jpg\');background-position:50% 40%"><h3 class="country-article-card-title">Ohrid</h3></div>'))
    photo(Path(root) / "Images/web/og/north-macedonia.jpg", (1200, 630))
    photo(Path(root) / "Images/web/og/north-macedonia-skopje.jpg", (1200, 630))
    wr(root, "north-macedonia/field-notes.html", OG_HEAD % og_tags(D + "north-macedonia.jpg"))
    wr(root, "north-macedonia/skopje.html", OG_HEAD % og_tags(D + "north-macedonia-skopje.jpg"))
    wr(root, "north-macedonia/ohrid.html", OG_HEAD % og_tags(D + "north-macedonia.jpg"))      # the country's: not its own
    wr(root, "north-macedonia/bitola.html", OG_HEAD % "")                                     # no tag, no card
    wr(root, "north-macedonia/krushevo.html", OG_HEAD % "")                                   # no tag, no card
    pages = ["north-macedonia/%s.html" % s for s in ("index", "field-notes", "skopje", "ohrid", "bitola")]
    before = {p: rd(root, p) for p in pages}
    files0 = sorted(os.listdir(Path(root) / "Images/web/og"))

    log = og.ensure(pages, dry=True)
    check("og dry: nothing written", all(rd(root, p) == before[p] for p in pages)
          and sorted(os.listdir(Path(root) / "Images/web/og")) == files0, log)
    check("og dry: says what it would do", any("would get" in ln for ln in log), log)

    log = og.ensure(pages)
    txt = "\n".join(log)
    check("og: field notes + skopje untouched", rd(root, pages[1]) == before[pages[1]] and rd(root, pages[2]) == before[pages[2]], txt)
    idx = Path(root) / "Images/web/og/north-macedonia-index.jpg"
    check("og: country page image cut", idx.is_file(), txt)
    if idx.is_file():
        im = Image.open(idx)
        check("og: 1200x630", im.size == (1200, 630), im.size)
        check("og: ICC carried", bool(im.info.get("icc_profile")))
    h = rd(root, pages[0])
    check("og: country page tags kept, CRLF kept", h == before[pages[0]], h[:300])
    oh = Path(root) / "Images/web/og/north-macedonia-ohrid.jpg"
    check("og: article cut from its card", oh.is_file() and "card-ohrid-3x.jpg" in txt, txt)
    h = rd(root, pages[3])
    check("og: article repointed (og + twitter)", h.count(D + "north-macedonia-ohrid.jpg") == 2 and D + "north-macedonia.jpg" not in h, h)
    check("og: article with no card -> ACTION NEEDED", any(ln.startswith("ACTION NEEDED north-macedonia/bitola.html") for ln in log)
          and rd(root, pages[4]) == before[pages[4]], txt)
    # a country page with no og tag at all gets one after og:description
    wr(root, "north-macedonia/index.html", OG_HEAD % "")
    og.ensure(["north-macedonia/index.html"])
    h = rd(root, "north-macedonia/index.html")
    check("og: tag inserted after og:description", re.search(r'og:description" content="D">\n<meta property="og:image" content="%s"'
                                                             % re.escape(D + "north-macedonia-index.jpg"), h) is not None
          and 'og:image:width' in h, h)
    # idempotent
    snap = {p: rd(root, p) for p in pages}
    m0 = {f: os.stat(Path(root) / "Images/web/og" / f).st_mtime_ns for f in os.listdir(Path(root) / "Images/web/og")}
    log = og.ensure(pages)
    m1 = {f: os.stat(Path(root) / "Images/web/og" / f).st_mtime_ns for f in os.listdir(Path(root) / "Images/web/og")}
    check("og: rerun no-op", snap == {p: rd(root, p) for p in pages} and m0 == m1, log)
    check("og: manifest stayed in the scratch tree", os.path.isfile(tl.MANIFEST))


# ---------------------------------------------------------------- 3. search thumbs
def test_search_thumbs(root):
    import json
    import build_search_thumbs as bst
    from PIL import Image
    bst.SITE, bst.OUT, bst.INDEX = Path(root), Path(root) / "Images/web/search", Path(root) / "search-index.js"
    build_site(root)
    photo(Path(root) / "Images/North Macedonia/Ohrid/lake.jpg", (2000, 1333))
    photo(Path(root) / "Images/North Macedonia/Skopje/bridge.jpg", (2000, 1333))
    wr(root, "index.html", '<div class="cards"><a href="north-macedonia/ohrid.html"><div class="card-img bg" style="background-image:url(\'Images/North Macedonia/Ohrid/lake.jpg\');background-position:50% 30%"></div></a>'
                           '<a href="north-macedonia/skopje.html"><div class="card-img bg" style="background-image:url(\'Images/North Macedonia/Skopje/bridge.jpg\')"></div></a></div>')
    wr(root, "posts.html", "<html></html>")
    # what gen_search_index writes: no thumbs
    idx = {"countries": [{"name": "North Macedonia", "url": "north-macedonia/index.html", "flag": "mk", "kind": "guide"}],
           "articles": [{"title": "Ohrid", "url": "north-macedonia/ohrid.html"}, {"title": "Skopje", "url": "north-macedonia/skopje.html"}],
           "pages": []}
    wr(root, "search-index.js", "window.__SEARCH_INDEX__=" + json.dumps(idx, separators=(",", ":")) + ";")
    photo(Path(root) / "Images/web/search/north-macedonia__skopje.jpg", (240, 180))        # already cut
    m0 = os.stat(Path(root) / "Images/web/search/north-macedonia__skopje.jpg").st_mtime_ns
    argv = sys.argv
    try:
        sys.argv = ["build_search_thumbs.py", "--dry-run"]
        bst.main()
        check("thumbs dry: index unchanged", "thumb" not in rd(root, "search-index.js"))
        sys.argv = ["build_search_thumbs.py"]
        bst.main()
    finally:
        sys.argv = argv
    d = json.loads(rd(root, "search-index.js").split("=", 1)[1].rstrip().rstrip(";"))
    check("thumbs: country thumb", d["countries"][0].get("thumb") == "Images/web/dest-cards/north-macedonia.jpg", d["countries"])
    check("thumbs: articles", [a.get("thumb") for a in d["articles"]] == ["Images/web/search/north-macedonia__ohrid.jpg",
                                                                          "Images/web/search/north-macedonia__skopje.jpg"], d["articles"])
    cut = Path(root) / "Images/web/search/north-macedonia__ohrid.jpg"
    check("thumbs: cut 240x180 + ICC", cut.is_file() and Image.open(cut).size == (240, 180) and bool(Image.open(cut).info.get("icc_profile")))
    check("thumbs: existing one reused", os.stat(Path(root) / "Images/web/search/north-macedonia__skopje.jpg").st_mtime_ns == m0)


# ---------------------------------------------------------------- 4. Explore More
XM = ('<section class="xm" id="explore-more"><h2 class="xm-h">Explore More</h2><div class="xm-g"><a class="xm-c" href="old.html">'
      'old</a></div></section>')


def art(body, nl="\n"):
    return nl.join(['<!doctype html>', '<html><head><title>A</title></head><body>', '<div class="article-body">',
                    '  <p>First.</p>', body, '</div>', '<footer>f</footer></body></html>', ''])


def test_explore_more(root):
    import explore_more as xm
    xm.ROOT = Path(root)
    e = {"rel": "greece/meteora.html", "country": "greece", "name": "Greece", "kind": "guide", "title": "Meteora",
         "desc": "Rocks.", "img": "", "mins": 5, "draft": False}
    cat = {e["rel"]: e}

    def run(name, body, nl="\n"):
        wr(root, "greece/athens.html", art(body, nl))
        chosen, what = xm.apply_page("greece/athens.html", cat, {})
        return rd(root, "greece/athens.html"), what

    for name, body in (("marker", "  " + XM + "<!-- /explore-more -->"),
                       ("marker own line", "  " + XM + "\n  <!-- /explore-more -->"),
                       ("no marker", "  " + XM),
                       ("id first", "  " + XM.replace('class="xm" id="explore-more"', 'id="explore-more" class="xm"')),
                       ("itinerary class", "  " + XM.replace('class="xm"', 'class="xm measure"') + "<!-- /explore-more -->")):
        h, what = run(name, body)
        check("xm %s: one block" % name, what == "ok" and h.count('id="explore-more"') == 1 and h.count("<!-- /explore-more -->") == 1
              and 'href="old.html"' not in h and 'href="meteora.html"' in h, h)
        h2 = (xm.apply_page("greece/athens.html", cat, {}), rd(root, "greece/athens.html"))[1]
        check("xm %s: idempotent" % name, h2 == h)

    # two stacked copies, the first without its marker, prose between them: one block, the prose kept
    body = "  " + XM + "\n  <p>Kept between.</p>\n  " + XM + "<!-- /explore-more -->"
    old_cut = xm.BLOCK_RE.sub("", art(body))
    check("xm stacked: the old pattern would have cut the prose", "Kept between." not in old_cut)
    h, what = run("stacked", body)
    check("xm stacked: one block, prose kept", h.count('id="explore-more"') == 1 and "<p>Kept between.</p>" in h
          and h.count("<!-- /explore-more -->") == 1, h)

    # an orphan marker the section lost (the drafts' "</p>\n    <!-- /explore-more -->\n\n    <section ...")
    body = "  <p>Last.</p>\n    <!-- /explore-more -->\n\n  " + XM + "<!-- /explore-more -->"
    h, what = run("orphan", body)
    check("xm orphan marker removed", h.count("<!-- /explore-more -->") == 1 and h.count('id="explore-more"') == 1 and "<p>Last.</p>" in h, h)

    # CRLF page stays CRLF
    h, what = run("crlf", "  " + XM, nl="\r\n")
    check("xm crlf kept", h.count("\n") == h.count("\r\n") and h.count('id="explore-more"') == 1, repr(h[-400:]))

    # strip_blocks matches BLOCK_RE wherever BLOCK_RE worked
    s = art("  " + XM + "\n  <!-- /explore-more -->")
    check("xm strip == old pattern on a well-formed block", xm.strip_blocks(s)[0] == xm.BLOCK_RE.sub("", s))


# ---------------------------------------------------------------- 5. draft navs
def test_draft_navs(root):
    import update_draft_nav as u
    from publish_country import parse_countries, build_nav
    build_site(root)
    by = parse_countries(rd(root, "destinations.html"))
    pfx = "../../../"
    fresh = build_nav(pfx, by)
    stale = fresh.replace("North Macedonia", "N. Macedonia")

    def page(nav, nl="\n", tail="\n    </li>", extra="<p>Kevin's unsaved paragraph.</p>"):
        return nl.join(['<!doctype html>', '<html><head></head><body><nav><ul>', '    <li class="dd">',
                        nav.replace("\n", nl) + tail.replace("\n", nl) + '</ul></nav>',
                        '<div class="article-body">' + extra + '</div>', '</body></html>', ''])

    rels = {"lf": page(stale), "crlf": page(stale, "\r\n"), "lfnav_in_crlf": page(stale, "\r\n").replace(stale.replace("\n", "\r\n"), stale),
            "fresh": page(fresh), "odd_ws": page(stale, tail="\n\t\t  </li>"), "nomenu": "<html><body><p>none</p></body></html>\n"}
    paths = {}
    for k, v in rels.items():
        paths[k] = str(wr(root, "Drafts/Upcoming/testland/%s.html" % k, v))
    mt = {k: os.stat(p).st_mtime_ns for k, p in paths.items()}

    changed, skipped = u.refresh(list(paths.values()), by, dry=True, root=str(root), say=lambda *_: None)
    check("nav dry: counts", (changed, skipped) == (4, 2), (changed, skipped))
    check("nav dry: nothing written", all(rd(root, os.path.relpath(p, root)) == rels[k] for k, p in paths.items()))

    changed, skipped = u.refresh(list(paths.values()), by, root=str(root), say=lambda *_: None)
    check("nav: counts", (changed, skipped) == (4, 2), (changed, skipped))
    got = {k: rd(root, os.path.relpath(p, root)) for k, p in paths.items()}
    check("nav lf: exactly the fresh page", got["lf"] == page(fresh), got["lf"][:400])
    check("nav crlf: exactly the fresh page, CRLF", got["crlf"] == page(fresh, "\r\n") and got["crlf"].count("\n") == got["crlf"].count("\r\n"))
    check("nav lf-menu-in-crlf: menu stays LF, rest CRLF", got["lfnav_in_crlf"] == page(fresh, "\r\n").replace(fresh.replace("\n", "\r\n"), fresh))
    check("nav odd whitespace before </li> kept", got["odd_ws"] == page(fresh, tail="\n\t\t  </li>"))
    check("nav: up-to-date page untouched", got["fresh"] == rels["fresh"] and os.stat(paths["fresh"]).st_mtime_ns == mt["fresh"])
    check("nav: page without a menu untouched", got["nomenu"] == rels["nomenu"])
    check("nav: unsaved work kept", all("Kevin's unsaved paragraph." in got[k] for k in ("lf", "crlf", "odd_ws")))
    check("nav: no temp files", not list(Path(root, "Drafts").rglob("*.navtmp")))
    changed, _ = u.refresh(list(paths.values()), by, root=str(root), say=lambda *_: None)
    check("nav: rerun no-op", changed == 0)


# ---------------------------------------------------------------- 6. wiring
def test_wiring():
    rf = (HERE / "retire_field_notes.py").read_text(encoding="utf-8")
    fin = rf[rf.index("def _finish("):]
    order = ["gen_og_images.ensure", "import explore_more", '"gen_search_index.py"', "search_thumbs(dry)", "draft_navs(dry)"]
    pos = [fin.find(x) for x in order]
    check("retire: steps present", all(p >= 0 for p in pos), dict(zip(order, pos)))
    check("retire: og before Explore More, thumbs after the index", pos == sorted(pos), dict(zip(order, pos)))
    check("retire: home tile + fit_backgrounds in apply", "_home_tile(slug, dry, replace_tile)" in rf and "fit_home_tiles(dry)" in rf)
    check("retire: --replace-tile flag", '"--replace-tile="' in rf)
    pc = (HERE / "publish_country.py").read_text(encoding="utf-8")
    check("publish: thumbs after the search index", 0 < pc.find("gen_search_index.main()") < pc.find("build_search_thumbs.py"))
    check("publish: every draft's nav (no slug list)", '"update_draft_nav.py")],' in pc and "field_notes()]" not in pc)
    check("publish: og images", '"gen_og_images.py"' in pc)


def main():
    real = {p: p.stat().st_mtime_ns for p in [REAL_ROOT / "index.html", REAL_ROOT / "search-index.js", REAL_ROOT / "tools" / ".thumb-manifest.json"]
            if p.exists()}
    tmp = tempfile.mkdtemp(prefix="launch_steps_")
    try:
        for i, fn in enumerate((test_home_tile, test_share_images, test_search_thumbs, test_explore_more, test_draft_navs)):
            root = Path(tmp) / ("site%d" % i)
            root.mkdir()
            try:
                fn(root)
            except Exception as e:                          # a crash is a failure, and the rest still run
                import traceback
                check(fn.__name__ + " crashed", False, traceback.format_exc())
        test_wiring()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check("real tree untouched", all(p.stat().st_mtime_ns == m for p, m in real.items()))
    print("%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
