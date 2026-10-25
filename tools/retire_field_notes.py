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
                         Read Guide (Kevin, 2026-10-24)
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
  5b. guide cards       a kept field-notes page gets a card under each city heading whose city now has its own
                         guide (tools/guide_cards.py; Kevin, 2026-10-23)
  6b. Explore More       the three related-article cards at the end of every live page are picked again
                         (tools/explore_more.py; Kevin, 2026-10-23)
  6. posts + count      tools/sync_guides.py: its articles join the posts page's Guides section, its
                         field-notes card leaves, and the home hero's "N and counting." is recounted
  7. sitemap + search    tools/gen_sitemap.py and tools/gen_search_index.py run again

It refuses to run until <slug>/index.html is live: retiring first would point the whole site at a
page that does not exist yet. archive/ is never touched (see the publish protocol memory).

--keep-field-notes (Kevin, 2026-10-07, Greece: "the only greek articles that are live are field notes, meteora
and thessaloniki"): the country becomes an In-Depth Guide (steps 1, 2, 3, 5b, 6, 7) but its field notes stay a live
page that the site keeps linking to (steps 4 and 5 are skipped). That is the "Keep the field notes live" launch.

    python tools/retire_field_notes.py armenia --dry-run     # what it would change (the tab shows this)
    python tools/retire_field_notes.py armenia               # at launch, after the guide is live
    python tools/retire_field_notes.py greece --keep-field-notes   # a guide launch that keeps the notes live
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
                "The sitemap and site search drop it"]}


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


def apply(slug, dry=False, force=False, keep=False):
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
    btn = re.search(r"(location\.href='%s/index\.html'.*?<span class=\"art-card-btn\">)(?:Read Notes|Explore)(</span>)" % re.escape(slug), d2, re.S)
    if btn:                                           # a guide's card says Read Guide (Kevin, 2026-10-24)
        d2 = d2[:btn.start()] + btn.group(1) + "Read Guide" + btn.group(2) + d2[btn.end():]
    if d2 != d:
        log.append("destinations.html: card + map entry -> %s/index.html, In-Depth Guide badge" % slug)
        if not dry:
            io.open(ROOT / "destinations.html", "w", encoding="utf-8", newline="").write(d2)
    # 1b. the home page's tile for the country opens the country page and reads as a guide (Kevin, 2026-10-24:
    #     "Turkiye card on home should link to the index card")
    log += home_tile(slug, dry)
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


def home_tile(slug, dry=False):
    """index.html's Explore tile for `slug`: href to the country page, "N stories" for the sub line, Read Guide"""
    h = _read("index.html")
    m = re.search(r'<a class="tile" href="%s/(?:field-notes|index)\.html">.*?</a>' % re.escape(slug), h, re.S)
    if not m:
        return ["index.html: no tile for %s" % slug]
    n = len([p for p in (ROOT / slug).glob("*.html") if p.name not in ("index.html",) and 'http-equiv="refresh"' not in p.read_text(encoding="utf-8")[:4000]])
    t = m.group(0).replace('href="%s/field-notes.html"' % slug, 'href="%s/index.html"' % slug)
    t = re.sub(r'<div class="sub">[^<]*</div>', '<div class="sub">%d stories</div>' % n, t, count=1)
    t = t.replace('<span class="rn">Read Notes</span>', '<span class="rn">Read Guide</span>')
    if t == m.group(0):
        return ["index.html: the %s tile already opens the country page" % slug]
    if not dry:
        io.open(ROOT / "index.html", "w", encoding="utf-8", newline="").write(h[:m.start()] + t + h[m.end():])
    return ["index.html: %s tile -> %s/index.html, %d stories, Read Guide" % (slug, slug, n)]


def _finish(dry, page=None):
    log = []
    # 5c. the country folder holds live pages only: a stub or stray file goes to Drafts/.Archive (tools/site_archive.py;
    #     Kevin, 2026-10-24: "remove any files that are not online anymore ... Add this to the launch process")
    if page and "/" in page:
        import site_archive
        log += ["folder: " + ln for ln in site_archive.sweep([page.split("/")[0]], dry)]
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
    # 7. sitemap + search
    for tool in ("gen_sitemap.py", "gen_search_index.py"):
        if not (TOOLS / tool).is_file():
            continue
        log.append("ran %s" % tool)
        if not dry:
            subprocess.run([sys.executable, str(TOOLS / tool)], cwd=ROOT, capture_output=True, text=True)
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
    for line in apply(slug, dry=dry, force="--force" in sys.argv, keep="--keep-field-notes" in sys.argv):
        print(("[dry] " if dry else "") + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
