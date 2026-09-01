"""Consistency check: one site, one look.

Loads every page and records the computed value of each design vector we care
about. A vector is a (selector, property) pair, for example ".article-h3" and
"font-size". The site is consistent when a given vector resolves to the SAME
value on every page that has it. Anything else is reported, with the pages that
disagree and the value each of them uses.

    python tools/consistency_check.py                  # the preview build
    python tools/consistency_check.py --live           # the live site
    python tools/consistency_check.py --width 390      # phone
    python tools/consistency_check.py --only nav,type  # one group
    python tools/consistency_check.py --only states    # hover states only
    python tools/consistency_check.py --only faces     # retired typefaces only

It checks three things:

  VECTORS  a (selector, property) pair that must resolve to one value sitewide
  HOVERS   every interactive element, hovered, measured on the element that
           actually PAINTS the change. A rule set on an <a> whose colour is
           painted by a <span> inside it changes nothing visible, and this
           build has shipped that bug repeatedly.
  FACES    any element still rendering in a typeface the redesign retired

This does not look at content. It has no opinion about which value is right; it
only says that two pages disagree, which is the class of bug that keeps coming
back when a rule is changed in one place and not another.
"""
import argparse
import asyncio
import collections
import json
import pathlib
import re
import sys

from playwright.async_api import async_playwright

SITE = pathlib.Path(__file__).resolve().parent.parent

# selector -> properties that must match across pages.
# Grouped so a run can be narrowed while iterating on one part of the design.
VECTORS = {
    "nav": {
        "nav": ["height", "paddingLeft", "paddingRight", "position"],
        "nav .nav-logo": ["fontFamily", "fontSize", "fontWeight", "letterSpacing"],
        "nav > ul > li > a": ["fontFamily", "fontSize", "letterSpacing",
                              "textTransform", "color"],
        "nav > ul": ["gap"],
        ".nav-dropdown-fn": ["width", "padding", "backgroundColor", "boxShadow"],
        ".nav-dropdown-fn .mm-col a": ["fontSize", "color", "padding"],
        ".mm-head-t": ["fontFamily", "fontSize", "fontWeight", "textTransform"],
        ".nav-fulldot": ["width", "height", "borderRadius", "backgroundColor"],
    },
    "footer": {
        "footer": ["backgroundColor", "paddingLeft", "paddingRight"],
        "footer .footer-logo": ["fontFamily", "fontSize", "color"],
        "footer .footer-tagline": ["fontSize", "color", "opacity"],
        "footer .footer-links a": ["fontSize", "color", "letterSpacing", "opacity"],
        "footer .footer-copy": ["fontSize", "color", "opacity"],
    },
    "type": {
        "h1": ["fontFamily", "fontWeight"],
        # every section heading, whatever page type it is on. marginTop is not
        # compared: a field note opens its section with the heading, a guide
        # reaches one mid-prose, so the two legitimately differ.
        ".article-h2, .article-body h2, .fn-section h2, .artbody h2, .fn-city-hd h2":
            ["fontFamily", "fontSize", "fontWeight", "color",
             "paddingBottom", "borderBottomWidth"],
        # the sub level. One class across both page types: the guides reach it
        # as an h3, the field notes as .fn-sub-hd, and they must not drift.
        ".article-h3, .article-body h3, .fn-section h3, .fn-sub-hd":
            ["fontFamily", "fontSize", "fontWeight", "fontStyle", "color",
             "textAlign"],
        ".artbody p, .article-body p, .fn-section p, .measure p":
            ["fontFamily", "fontSize", "fontWeight", "lineHeight", "color"],
        ".page-title, h1.page-title": ["fontSize", "fontWeight"],
        # EVERY section-rank heading, including the ones the article union above
        # cannot reach: the transplanted guide and the about page open a section
        # with .h-a, privacy with .privacy-heading, resources with .rbh-title.
        # One rank on the page means one size on the page.
        ".article-h2, .article-body h2, .fn-section h2, .artbody h2,"
        " .fn-city-hd h2, .h-a, .privacy-heading, .rbh-title, .blk-h,"
        " .rs-blk-h, .comp-h":
            ["fontSize", "fontWeight", "paddingBottom", "borderBottomWidth",
             "textTransform"],
        # .rm-h titles the itinerary's route map, .fn-sub-hd titles the field
        # notes'. Same job, so the same treatment; kept out of the h3 vector
        # above so a miss reads as "the route-map title drifted", not "h3 did".
        # .rm-h ALONE. It titles a route map -- a caption tied to the figure
        # below it -- so it keeps the display face, centred over the map. It is
        # deliberately NOT the section subhead above, which is the sans caps
        # marker with the accent bar; the two used to share .fn-sub-hd and every
        # rule change had to fight itself.
        ".rm-h": ["fontFamily", "fontSize", "fontWeight", "color", "textAlign"],
        # every page title, whatever class its page happens to reach for
        ".page-title, h1.page-title, h1.sec-h, .gg-about-h, .posts-page-title,"
        " .privacy-title, .contact-title, .nf-title": ["fontSize", "fontWeight"],
        # the one line of deck under a page title
        ".gg-posts-lede, .dest-subhead, .page-lede, .privacy-lede,"
        " .contact-lede, .posts-page-sub":
            ["fontSize", "color", "opacity", "lineHeight"],
        # a description inside a card or a row is body copy, so it is the sans
        ".rrow-desc, .card-p, .post-excerpt, .essentials-body, .f-b":
            ["fontFamily"],
        # ... and the name at the head of one is the sans too, at one weight
        ".rrow-name, .cards > a .card-h, .art-card-name, .rs-n":
            ["fontFamily", "fontWeight"],
        # a field someone types into takes the reading face, not the display one
        ".contact-input, .nav-search-q, .form-row input": ["fontFamily"],
    },
    "links": {
        ".artbody p a, .article-body p a, .fn-section p a, .measure p a":
            ["color", "textDecorationLine"],
        ".artbody p b, .article-body p b, .fn-section p b": ["color", "fontWeight"],
        ".artbody u, .article-body u, .fn-section u": ["color", "fontStyle"],
    },
    "furniture": {
        ".article-tip, .article-callout": ["backgroundColor", "borderLeftWidth",
                                           "borderLeftColor", "borderRadius"],
        "#toc-b, .article-toc": ["backgroundColor", "borderRadius", "borderTopWidth"],
        ".gg-updated": ["fontSize", "marginTop", "marginBottom", "color"],
        ".cmmap": ["borderRadius", "boxShadow"],
        ".cmkey": ["borderRadius", "boxShadow"],
    },
    "hero": {
        ".article-hero.gg-banner, .ah-banner": ["height", "paddingLeft",
                                                "justifyContent", "alignItems"],
        # the headline over a photograph: one size, one measure, on all six
        ".article-hero.gg-banner h1, .ah-banner h1":
            ["fontSize", "fontWeight", "maxWidth", "marginTop"],
        # and the standfirst under it
        ".article-hero .article-lead, .ah-banner .artsub, .article-hero-sub":
            ["fontSize", "fontWeight", "maxWidth", "color"],
        ".country-hero, .fn-hero": ["backgroundColor", "minHeight", "paddingLeft",
                                    "paddingTop", "paddingBottom", "height"],
        ".country-hero-map, .fn-hero-map": ["opacity"],
        # the silhouette is a watermark; at two very different sizes it stops
        # reading as the same device
        ".country-hero-map svg, .fn-hero-map svg": ["height"],
        # the warm radial left over from the retired palette. It is on 34 field
        # notes in two different shades and on the 404 in a third context.
        ".fn-hero-glow, .country-hero-glow, .nf-glow": ["backgroundImage"],
        ".country-hero-title, .fn-hero-title": ["fontFamily", "fontSize", "fontWeight"],
        ".country-place-btn": ["borderTopWidth", "color", "fontSize", "padding"],
        ".country-breadcrumb a, .article-breadcrumb a": ["color", "fontSize"],
    },
    "controls": {
        # one primary button across the site: the contact form, the 404, the
        # newsletter. They had drifted to two paddings and two type sizes.
        ".contact-submit, .nf-btn-primary, .newsletter-submit, .form-submit,"
        " .btn-primary": ["backgroundColor", "color", "padding", "fontSize",
                          "letterSpacing", "borderWidth"],
        ".nf-btn-ghost, .country-place-btn, .chip:not(.on)":
            ["borderWidth", "padding", "fontSize"],
        ".filter-btn.active, .chip.on": ["backgroundColor", "borderColor", "color"],
    },
    "cards": {
        ".cards > a .card-h": ["fontFamily", "fontSize", "color"],
        ".cards > a .kick": ["fontSize", "letterSpacing", "textTransform", "color"],
        ".cards .card-img": ["aspectRatio"],
        ".tile": ["aspectRatio"],
        ".tile .nm": ["fontFamily", "fontSize", "color"],
    },
    "layout": {
        ".page-header": ["paddingTop", "paddingLeft"],
        ".sec.pad, .pad": ["paddingLeft", "paddingRight"],
        ".article-body, .artbody": ["maxWidth"],
        # the air an article opens on, below the hero. A field note and a guide
        # are different templates that must still start at the same distance.
        ".article-body:has(> #toc-b:first-child),"
        " .article-body:has(> .article-toc:first-child),"
        " .article-body:has(> .fn-toc:first-child)": ["paddingTop"],
    },
}

# Interactive elements, and the element that actually PAINTS the change.
# (name, thing you hover, child that carries the colour or None, properties)
#
# This build has shipped hover rules that did nothing several times, always the
# same way: the rule is on the <a> but the colour is painted by a <span> or <b>
# inside it, so nothing visible moves. Measuring the painted element is the
# whole point. A component that reports NO CHANGE has no hover state at all,
# which is a fault in its own right, not a difference between pages.
HOVERS = [
    # :not(.active) — the current page's own link already sits at full strength,
    # so hovering it correctly changes nothing
    ("nav top-level link",
     "nav > ul > li > a:not(.nav-dropdown-trigger):not(.active)", None,
     ["color", "borderBottomColor"], True),
    ("nav wordmark", "nav .nav-logo", None, ["color"], False),
    ("dropdown country link", ".nav-dropdown-fn .mm-col a", None, ["color"], True),
    # the country name lives in a span inside the link; it has to move too
    ("dropdown country name", ".nav-dropdown-fn .mm-col a",
     "span:not(.nav-fulldot)", ["color"], True),
    ("dropdown continent", ".mm-side-live a.nav-dropdown-continent", None,
     ["color"], True),
    ("dropdown map card", ".mm-side-live .nav-map-card", None,
     ["borderColor", "backgroundColor", "opacity"], True),
    ("dispatch card title", ".cards > a", ".card-h", ["color"], True),
    ("dispatch card photo", ".cards > a", ".card-img", ["transform"], True),
    ("country tile", ".tiles .tile, .home-dest-card", None, ["transform"], True),
    ("country guide card", ".country-article-card, .country-articles-grid > a",
     ".country-article-card-title", ["color"], True),
    ("filter pill", ".filter-btn:not(.active)", None,
     ["backgroundColor", "color", "borderColor"], True),
    ("jump chip", ".country-place-btn", None,
     ["backgroundColor", "borderColor", "color"], True),
    # the prose link's rest state already carries a full-strength underline, so
    # the hover rule that deepens it has nothing left to deepen
    ("prose link", ".artbody p a, .article-body p a, .fn-section p a, .measure p a",
     None, ["color", "textDecorationColor"], True),
    ("contents link", "#toc-b a.toc-link, .article-toc a, .toc-g a", None,
     ["color"], True),
    ("section 'view all' link", ".more, .section-link", None,
     ["color", "borderBottomColor"], True),
    ("resource row", ".rrow", ".rrow-name", ["color"], True),
    ("footer link", "footer .footer-links a", None, ["color"], True),
    ("breadcrumb", ".country-breadcrumb a, .article-breadcrumb a,"
     " .fn-hero-crumbs a", None, ["color"], True),
]

# The field notes are one template built 34 times, so the states pass hovers
# three of them rather than all of them. Every other page is its own template
# and is hovered in full.
STATE_SAMPLE_NOTES = {"albania/field-notes", "georgia/field-notes",
                      "philippines/field-notes"}

# Faces the redesign retired. Any element that still renders in one of these is
# reported, wherever it is; SVG map labels are the usual survivor because the
# family is set on the container, out of reach of the theme's sweep.
RETIRED_FACES = ["Montserrat", "Fraunces", "Jost", "DM Mono", "Playfair",
                 "Source Serif", "monospace"]

# Variations that are correct by design; these are reported as expected rather
# than counted as faults. Each key is a (selector, property) from VECTORS.
EXPECTED = {
    (".country-hero, .fn-hero", "height"):
        "the field-note hero holds a row of jump chips, one per place the note "
        "covers, and a country with nine places wraps to more rows than one "
        "with four. The padding and the type are what must match, not the "
        "box's resulting height.",
    (".country-hero-map svg, .fn-hero-map svg", "height"):
        "the hero silhouette is the country's own outline, so its height is "
        "the shape's aspect ratio: Chile is tall and thin, the Netherlands "
        "squat. The opacity and the box it sits in are what must match.",
    ("nav > ul > li > a", "color"):
        "the home nav sits over the hero photograph, so its links are white",
    (".artbody u, .article-body u, .fn-section u", "color"):
        "a <u> that wraps a link takes the link colour, which is the point",
    (".article-body, .artbody", "maxWidth"):
        "the transplanted guide uses .artbody, whose measure is set on its rows",
    (".cards .card-img", "aspectRatio"):
        "the archive's field-note cards and the home dispatch cards differ by design",
    (".article-hero.gg-banner, .ah-banner", "paddingLeft"):
        "both render the photo full bleed with the copy on a 96px gutter; the "
        "five live-markup guides hang that gutter on the section, the "
        "transplanted itinerary hangs it on the absolutely placed copy block",
    ("nav", "backgroundColor"):
        "the home nav sits over the hero photograph until it pins, so it is "
        "transparent at rest there and white on every other page",
}

# Interactive elements that legitimately have no hover state.
HOVER_OK = {
    "nav wordmark": "the wordmark is the masthead, not one of the nav items",
    "dispatch card title":
        "an article card answers the cursor with the LIFT only. Tinting the "
        "title on hover made the card read as a link in prose, which is the "
        "one job the accent still has. The card moves; the words do not.",
    "country guide card":
        "same as the dispatch card: the lift is the response. These titles sit "
        "white on a photograph and stay white in both states.",
    "nav top-level link":
        "the home nav floats over the hero photograph, so it rests at 70% "
        "white and steps to full white; every other page rests at 55% ink and "
        "steps to full ink. Same gesture, opposite ground.",
}

PROBE = """([sel, props]) => {
  // A vector's selector is often a UNION of the several classes that serve one
  // role ('.rrow-desc, .card-p, .post-excerpt'). Taking document.querySelector
  // on the whole union measures whichever member happens to come first on THIS
  // page, so resources reports its .rrow-desc and index reports its .card-p and
  // the two get filed as the same page disagreeing. That is not a difference a
  // visitor can see; it is the probe comparing two different components.
  //
  // Each member of the union is therefore resolved on its own and the first one
  // that this page actually has is measured, but the RESULT is tagged with the
  // member it came from. The caller keys on that tag, so a class is only ever
  // compared against itself.
  const parts = sel.split(',').map(s => s.trim()).filter(Boolean);
  for (const part of parts) {
    let el = null;
    try { el = document.querySelector(part); } catch (e) { continue; }
    if (!el) continue;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width < 1 && r.height < 1 && cs.display === 'none') continue;
    const out = {__part: part};
    for (const p of props) out[p] = cs[p];
    if (props.includes('height')) out.height = Math.round(r.height) + 'px';
    if (props.includes('width')) out.width = Math.round(r.width) + 'px';
    return out;
  }
  return null;
}"""


READ = ("(el, props) => { const cs = getComputedStyle(el); const o = {};"
        " for (const q of props) o[q] = cs[q]; return o; }")

# Every <link> stylesheet has parsed, artifact.css's own last rule is reachable,
# and the imported fonts.css resolved. Without this the run measures pages
# mid-parse and invents differences that no visitor would ever see.
SHEETS_READY = """() => {
  const links = [...document.querySelectorAll('link[rel=stylesheet]')];
  if (!links.length) return true;
  for (const l of links) {
    let s; try { s = l.sheet; } catch (e) { return false; }
    if (!s || !s.cssRules || s.cssRules.length === 0) return false;
  }
  // artifact.css ends on the footer/tail block; its presence proves the whole
  // 170KB was parsed, not just the first screenful of rules
  const art = links.find(l => (l.href || '').includes('artifact.css'));
  if (art && art.sheet) {
    const txt = art.sheet.cssRules[art.sheet.cssRules.length - 1].cssText || '';
    if (!txt) return false;
  }
  if (document.fonts && document.fonts.status !== 'loaded') return false;
  // ... and the cascade has stopped moving. Chromium applies a long sheet in
  // chunks, so "the rules exist" is not "the rules are painted": take a
  // fingerprint of the values that move last and require two equal samples.
  const fp = () => {
    const f = document.querySelector('footer');
    const n = document.querySelector('nav');
    const b = document.body;
    const g = e => e ? getComputedStyle(e) : null;
    const cf = g(f), cn = g(n), cb = g(b);
    return [cf && cf.paddingLeft, cf && cf.marginTop, cn && cn.paddingLeft,
            cn && cn.height, cb && cb.fontFamily,
            getComputedStyle(document.documentElement)
              .getPropertyValue('--dls')].join('|');
  };
  const now = fp();
  const ok = window.__ggfp === now;
  window.__ggfp = now;
  return ok;
}"""

FACES = """(faces) => {
  const hits = {};
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const ff = cs.fontFamily || '';
    for (const n of faces) {
      if (ff.includes(n)) {
        (hits[n] = hits[n] || {n: 0, eg: ''});
        hits[n].n++;
        if (!hits[n].eg) hits[n].eg = (el.tagName.toLowerCase()
          + (el.className && el.className.baseVal === undefined && el.className
             ? '.' + String(el.className).split(' ')[0] : ''));
        break;
      }
    }
  }
  return hits;
}"""


def norm(v):
    """Flatten differences a visitor cannot see.

    Two things kept being reported as components disagreeing when they do not:
    `transform: none` and `matrix(1, 0, 0, 1, 0, 0)` are the same identity, and
    two cards of slightly different width scale to 1.03972 and 1.03955 for the
    same rule. Identity collapses to "none" and matrix numbers round to 2dp.
    """
    if not isinstance(v, str):
        return v
    s = v.strip()
    m = re.match(r"matrix\(([^)]*)\)$", s)
    if m:
        try:
            nums = [round(float(x), 2) for x in m.group(1).split(",")]
        except ValueError:
            return s
        if nums == [1, 0, 0, 1, 0, 0]:
            return "none"
        return "matrix(" + ", ".join(f"{n:g}" for n in nums) + ")"
    return s


async def hover_probe(p, page):
    """Hover every interactive component and record what visibly moved."""
    out = {}
    for name, sel, child, props, want in HOVERS:
        try:
            el = p.locator(sel).first
            if await el.count() == 0:
                continue
            # a panel that only exists on hover has to be opened first
            if ".nav-dropdown-fn" in sel or ".mm-side-live" in sel:
                await p.locator(".nav-dropdown-wrap").first.hover(timeout=1500)
                await p.wait_for_timeout(260)
            paint = el.locator(child).first if child else el
            if child and await paint.count() == 0:
                continue
            # let any entrance animation finish first: a card still sliding up
            # reads as a transform change and looks like a hover response
            await el.scroll_into_view_if_needed(timeout=1500)
            await p.wait_for_timeout(420)
            before = await paint.evaluate(READ, props)
            await el.hover(timeout=1500, force=True)
            await p.wait_for_timeout(500)
            after = await paint.evaluate(READ, props)
            moved = {q: [norm(before[q]), norm(after[q])]
                     for q in props if norm(before[q]) != norm(after[q])}
            out[name] = moved or None
            await p.mouse.move(3, 3)
            await p.wait_for_timeout(180)
        except Exception:
            continue
    return out


async def collect(pages, width, groups, root):
    seen = collections.defaultdict(lambda: collections.defaultdict(list))
    hovers = collections.defaultdict(lambda: collections.defaultdict(list))
    faces = collections.defaultdict(list)
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        p = await b.new_page(viewport={"width": width, "height": 1000})
        for f in pages:
            # the 34 field notes all stem to "field-notes", so name them by path
            name = f.relative_to(root).as_posix().replace(".html", "")
            await p.goto(f.resolve().as_uri(), wait_until="load")
            # "load" can fire before a 170KB stylesheet is fully parsed, and
            # Chromium applies it incrementally, so a page sampled mid-parse
            # reports theme.css's values while its neighbours report
            # artifact.css's. That invented a "12 pages have a dimmer footer"
            # and a "Egypt's breadcrumb is a different white". Wait until every
            # linked sheet has its rules, then one frame.
            try:
                await p.wait_for_function(SHEETS_READY, timeout=8000)
            except Exception:
                print(f"  ! {name}: a stylesheet never finished", file=sys.stderr)
            await p.wait_for_timeout(360)
            for group, sels in VECTORS.items():
                if groups and group not in groups:
                    continue
                for sel, props in sels.items():
                    got = await p.evaluate(PROBE, [sel, props])
                    if not got:
                        continue
                    # key on the union member that was actually measured, so a
                    # class is only ever compared against itself across pages
                    part = got.pop("__part", sel)
                    label = sel if part == sel else f"{sel}  [{part}]"
                    for prop, val in got.items():
                        seen[(group, label, prop)][val].append(name)
            if not groups or "faces" in groups:
                for face, hit in (await p.evaluate(FACES, RETIRED_FACES)).items():
                    faces[face].append(f"{name} ({hit['n']}x, e.g. {hit['eg']})")
            # Hovering is slow (a settle and a transition per element), and the
            # 34 field notes are one template, so the states pass runs on a
            # sample: every page that is its own template, plus three notes.
            sampled = ("/" not in name or name.startswith("el-salvador/")
                       or name in STATE_SAMPLE_NOTES)
            if (not groups or "states" in groups) and sampled:
                for hname, moved in (await hover_probe(p, name)).items():
                    hovers[hname][json.dumps(moved, sort_keys=True)].append(name)
        await b.close()
    return seen, hovers, faces


def report(seen, width):
    bad, expected = 0, 0
    by_group = collections.defaultdict(list)
    for (group, sel, prop), values in seen.items():
        if len(values) > 1:
            by_group[group].append((sel, prop, values))
    for group in sorted(by_group):
        # a selector may now carry a "  [member]" tag naming the union member
        # that was measured; EXPECTED is keyed on the base selector
        rows = [r for r in sorted(by_group[group])
                if (r[0].split("  [")[0], r[1]) not in EXPECTED]
        expected += len(by_group[group]) - len(rows)
        if not rows:
            continue
        print(f"\n=== {group} ===")
        for sel, prop, values in rows:
            bad += 1
            print(f"  {sel}  ->  {prop}")
            for val, pages in sorted(values.items(), key=lambda x: -len(x[1])):
                shown = ", ".join(sorted(pages)[:6])
                more = f" (+{len(pages)-6} more)" if len(pages) > 6 else ""
                print(f"      {len(pages):>3} pages  {val}")
                print(f"           {shown}{more}")
    print(f"\n{bad} inconsistent vectors at {width}px"
          + (f"  ({expected} expected by design)" if expected else ""))
    return bad


def report_states(hovers):
    """A hover rule that changes nothing is a fault; so is one that changes
    something on one page and nothing on another."""
    bad = 0
    dead, split = [], []
    for name, results in sorted(hovers.items()):
        if name in HOVER_OK:
            continue
        if len(results) == 1 and next(iter(results)) == "null":
            dead.append((name, next(iter(results.values()))))
        elif len(results) > 1:
            split.append((name, results))
    if dead:
        print("\n=== states: no hover response at all ===")
        for name, pages in dead:
            bad += 1
            print(f"  {name}  ->  nothing moves on {len(pages)} pages")
            print(f"       {', '.join(sorted(pages)[:6])}"
                  + (f" (+{len(pages)-6} more)" if len(pages) > 6 else ""))
    if split:
        print("\n=== states: the same component responds differently ===")
        for name, results in split:
            bad += 1
            print(f"  {name}")
            for moved, pages in sorted(results.items(), key=lambda x: -len(x[1])):
                shown = "nothing moves" if moved == "null" else moved
                print(f"      {len(pages):>3} pages  {shown[:150]}")
                print(f"           {', '.join(sorted(pages)[:6])}"
                      + (f" (+{len(pages)-6} more)" if len(pages) > 6 else ""))
    return bad


def report_faces(faces):
    if not faces:
        return 0
    print("\n=== faces: a retired typeface still reaches the screen ===")
    for face, pages in sorted(faces.items()):
        print(f"  {face}  ->  {len(pages)} pages")
        print(f"       {'; '.join(sorted(pages)[:4])}"
              + (f" (+{len(pages)-4} more)" if len(pages) > 4 else ""))
    return len(faces)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="check the live site")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--only", default="", help="comma-separated group names")
    ap.add_argument("--json", default="", help="write the raw findings here")
    a = ap.parse_args()

    root = SITE if a.live else SITE / "preview"
    skip = {".tmp", "Drafts", "preview", "tools", "Images", "node_modules"}
    pages = sorted(f for f in root.rglob("*.html")
                   if not any(part in skip for part in f.relative_to(root).parts))
    groups = {g.strip() for g in a.only.split(",") if g.strip()}
    print(f"{len(pages)} pages, {a.width}px"
          + (f", groups: {', '.join(sorted(groups))}" if groups else ""))

    seen, hovers, faces = await collect(pages, a.width, groups, root)
    bad = report(seen, a.width)
    bad += report_states(hovers)
    bad += report_faces(faces)
    print(f"\n{bad} findings in total at {a.width}px")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(
            {f"{g}|{s}|{p}": {k: v for k, v in vals.items()}
             for (g, s, p), vals in seen.items()}, indent=1), encoding="utf-8")
    sys.exit(1 if bad else 0)


asyncio.run(main())
