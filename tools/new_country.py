"""new_country.py — build full-article pages from prose (Kevin, 2026-10-30: the writer writes prose, a script makes the page).

The writer agents used to type whole HTML pages (nav, footer, scripts, meta, photo frames) and read whole HTML models.
Most of the tokens, and most of the template mistakes, were in that boilerplate. Now:

  python tools/new_country.py extract <accepted page.html> [--out prose.md]
      an accepted page -> the prose format below (what a writer reads as a model, and the round-trip test input)
  python tools/new_country.py render <plan.json> <prose.md> [--out page.html]
      prose -> a finished page: head/meta, hero, nav, contents box, footer and scripts come from a SHELL (an accepted
      page of the same type named in the plan); photo frames, maps and lists from the markers in the prose
  python tools/new_country.py check <plan.json> <accepted page.html> ...
      round trip: extract -> render with the plan's shells -> compare with the accepted page (photos normalized);
      prints the diff lines; 0 = the renderer reproduces the page

PROSE FORMAT (.md): a front matter block, then blocks separated by blank lines.
  ---                                   front matter keys:
  file: sarajevo.html                     file (fixed by the plan), type: guide | top10 | index
  type: guide                             kicker: City Guide / Town Guide / Day Trip Guide / Experience Guide / ...
  kicker: City Guide                      title: the <title>/og:title/headline (60 chars or fewer)
  title: Sarajevo Travel Guide: ...       h1: the hero headline; lead: the hero's one-line lead
  h1: The Ultimate Guide to Sarajevo      description: meta + og:description (under 160 chars)
  lead: ...                               toc_title: "Sarajevo, Bosnia" (guides) / "Top 10 Things to Do in Bosnia"
  description: ...
  toc_title: Sarajevo, Bosnia
  ---
  ## Getting to Sarajevo {#getting-there}      h2 (id in braces; standard ids: getting-there, where-to-eat, where-to-stay)
  ### Baščaršija {#bascarsija}                 h3
  A paragraph. **bold**, *italic*, [text](https://...) external link, [text](sarajevo.html#x) internal, & written as &.
  - **Name** — what to order. [Maps](https://www.google.com/maps/...)     a bullet (the site's styled list)
  [PHOTO pair]   [PHOTO landscape]   [PHOTO portrait]        empty photo frames, filled later by fill_slots.py
  [MAP city:sarajevo]   [MAP route:bosnia]                   copied from the country's live field notes
  > Tip: text                                                the green tip box
  <<<                                                        raw HTML passthrough (a table, an embed)
  ...
  >>>
  Top 10: "## 1. Title {#id}" per entry; the contents box is generated. Index: see the plan's "cards".
"""
import argparse, html as htmlmod, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

A_EXT = ' target="_blank" rel="noopener noreferrer"'
UL = '<ul style="list-style:none;padding:0;margin:1.5rem 0">'
LI = '<li style="padding:.6rem 0;border-bottom:1px solid rgba(26,22,16,.1);font-family:Hanken Grotesk,Helvetica,Arial,sans-serif">'
PH = ('<div class="photo-ph" style="width:100%;{dim}background:#F2F0E9;border:1px dashed #C9C3B4;border-radius:3px;display:flex;'
      'align-items:center;justify-content:center;color:#8C8676;font-family:\'Hanken Grotesk\',Helvetica,Arial,sans-serif;'
      'font-size:11px;font-weight:600;letter-spacing:.16em">[PHOTO]</div>')
CELL = '<span style="display:block;position:relative;overflow:hidden;width:100%;aspect-ratio:2/3;border-radius:2px;">' + PH.format(dim="height:100%;") + '</span>'
LAND = '<div class="img-landscape">' + PH.format(dim="aspect-ratio:3/2;") + '</div>'
PAIR = '<div class="img-pair">\n      ' + CELL + '\n      ' + CELL + '\n    </div>'
PORTRAIT = '<div class="img-pair" style="grid-template-columns: 1fr; width: 40%; max-width: 100%; margin-left: auto; margin-right: auto">\n      ' + CELL + '\n    </div>'
TOP10_H2_STYLE = ' style="border-bottom:0.666667px solid rgba(28,40,33,.08);"'


# ----------------------------------------------------------------------------- inline text <-> markdown
def md_inline(h):
    """HTML inline -> markdown: links, bold, italic; everything else stays as HTML. Entities unescaped (& only)."""
    h = re.sub(r'<a href="([^"]+)" target="_blank" rel="noopener(?: noreferrer)?">(.*?)</a>', lambda m: "[%s](%s)" % (m.group(2), m.group(1)), h, flags=re.S)
    h = re.sub(r'<a href="([^"]+)">(.*?)</a>', lambda m: "[%s](%s)" % (m.group(2), m.group(1)), h, flags=re.S)
    h = re.sub(r"<b>(.*?)</b>", r"**\1**", h, flags=re.S)
    h = re.sub(r"<i>(.*?)</i>", r"*\1*", h, flags=re.S)
    return h.replace("&amp;", "&")


def html_inline(t):
    """markdown inline -> HTML. A link to http(s) opens in a new tab; a page or #anchor link does not."""
    t = re.sub(r"&(?![a-zA-Z#][a-zA-Z0-9]*;)", "&amp;", t)
    def link(m):
        text, href = m.group(1), m.group(2)
        return '<a href="%s"%s>%s</a>' % (href, A_EXT if href.startswith("http") else "", text)
    t = re.sub(r"\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)", link, t)     # a Maps URL may hold one level of (parens)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t, flags=re.S)
    return t


def slug(s):
    s = htmlmod.unescape(re.sub(r"<[^>]+>", "", s)).lower()
    s = re.sub(r"^\d+\.\s*", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "section"


# ----------------------------------------------------------------------------- extract: page -> prose
def body_of(s):
    i = s.find('<div class="article-body"')
    if i < 0:
        raise SystemExit("no article-body on this page (itineraries and country pages are not extracted)")
    i = s.index(">", i) + 1
    j = s.find('<section class="xm"', i)
    if j < 0:
        j = s.find("\n</div>\n</div>", i)
    return s[i:j]


def extract(path):
    s = open(path, encoding="utf-8").read()
    b = body_of(s)
    b = re.sub(r"<!-- TOC -->.*?<div id=\"toc-b\">.*?</div>\s*</div>\s*</div>\s*</div>", "", b, count=1, flags=re.S)   # top 10: generated
    out = []
    pos = 0
    pats = [
        ("h2", r'<h2 class="article-h2" id="([^"]+)"[^>]*>(.*?)</h2>'),
        ("h3", r'<h3 class="article-h3" id="([^"]+)"[^>]*>(.*?)</h3>'),
        ("map", r'<figure class="citymap-fig">.*?</figure>'),
        ("route", r'<div class="route-map">.*?</figure>\s*</div>'),
        ("pair", r'<div class="img-pair"(?: style="([^"]*)")?>.*?</div>'),
        ("land", r'<div class="img-landscape">.*?</div>(?:\s*</div>)?'),
        ("tip", r'<div class="article-tip">(.*?)</div>'),
        ("cap", r'<div class="img-caption"[^>]*>(.*?)</div>'),
        ("ul", r'<ul style="[^"]*">(.*?)</ul>'),
        ("p", r"<p>(.*?)</p>"),
        ("phspan", r"<p><span style=\"[^\"]*\"></span></p>|<span class=\"photo-ph\"[^>]*>.*?</span>"),
        ("br", r"<br\s*/?>"),
        ("comment", r"<!--.*?-->"),
    ]
    rx = re.compile("|".join("(?P<%s>%s)" % (k, p) for k, p in pats), re.S)
    for m in rx.finditer(b):
        gap = b[pos:m.start()].strip()
        if gap:
            out.append("<<<\n%s\n>>>" % gap)
        k = m.lastgroup; g = m.group(0)
        if k == "h2":
            mm = re.match(pats[0][1], g, re.S); out.append("## %s {#%s}" % (md_inline(mm.group(2).strip()), mm.group(1)))
        elif k == "h3":
            mm = re.match(pats[1][1], g, re.S); out.append("### %s {#%s}" % (md_inline(mm.group(2).strip()), mm.group(1)))
        elif k == "map":
            mm = re.search(r'city-maps/([a-z0-9-]+)(?:-mobile)?\.(?:png|webp)', g); out.append("[MAP city:%s]" % (mm.group(1) if mm else "?"))
        elif k == "route":
            out.append("[MAP route]")
        elif k == "pair":
            cells = len(re.findall(r"<span style=\"display:block", g))
            out.append("[PHOTO portrait]" if cells == 1 else "[PHOTO pair]")
        elif k == "land":
            out.append("[PHOTO landscape]")
        elif k == "tip":
            mm = re.match(pats[6][1], g, re.S); t = re.sub(r"^<strong>Tip:</strong>\s*", "", mm.group(1).strip()); out.append("> Tip: " + md_inline(t))
        elif k == "cap":
            mm = re.match(pats[7][1], g, re.S); out.append("[CAPTION %s]" % md_inline(mm.group(1).strip()))
        elif k == "ul":
            items = re.findall(r"<li[^>]*>(.*?)</li>", g, re.S)
            out.append("\n".join("- " + re.sub(r"\s+", " ", md_inline(x.strip())) for x in items))
        elif k == "p":
            mm = re.match(pats[9][1], g, re.S); t = re.sub(r"\s*\n\s*", " ", md_inline(mm.group(1).strip()))
            if t: out.append(t)
        pos = m.end()
    gap = b[pos:].strip()
    if gap:
        out.append("<<<\n%s\n>>>" % gap)
    # front matter from the page
    def g1(p, d=""):
        mm = re.search(p, s, re.S); return htmlmod.unescape(mm.group(1).strip()) if mm else d
    fm = {"file": os.path.basename(path), "type": "top10" if "toc-num" in s and "<!-- TOC -->" in s else "guide",
          "kicker": g1(r'color:#7ED4A8[^>]*>([^<]*)<'), "title": g1(r"<title>(.*?)</title>"),
          "h1": g1(r'<h1 class="article-title"[^>]*>(.*?)</h1>'), "lead": g1(r'<p class="article-lead"[^>]*>(.*?)</p>'),
          "description": g1(r'<meta name="description" content="([^"]*)"'),
          "toc_title": g1(r'toc-title(?:\\?">|">)([^<\\]*)')}
    head = "---\n" + "".join("%s: %s\n" % (k, v) for k, v in fm.items()) + "---\n\n"
    return head + "\n\n".join(out) + "\n"


# ----------------------------------------------------------------------------- render: prose -> page
def parse_prose(md):
    m = re.match(r"---\n(.*?)\n---\n", md, re.S)
    fm = {}
    if m:
        for line in m.group(1).split("\n"):
            if ":" in line:
                k, v = line.split(":", 1); fm[k.strip()] = v.strip()
        md = md[m.end():]
    blocks, cur, raw = [], [], None
    for line in md.split("\n"):
        if raw is not None:
            if line.strip() == ">>>":
                blocks.append(("raw", "\n".join(raw))); raw = None
            else:
                raw.append(line)
            continue
        if line.strip() == "<<<":
            if cur: blocks.append(("p", " ".join(cur))); cur = []
            raw = []; continue
        if not line.strip():
            if cur: blocks.append(("p", " ".join(cur))); cur = []
            continue
        st = line.strip()
        if st.startswith("- "):
            if cur and blocks and False: pass
            if cur and not (blocks and blocks[-1][0] == "ul" and cur == []):
                blocks.append(("p", " ".join(cur))); cur = []
            if blocks and blocks[-1][0] == "ul":
                blocks[-1][1].append(st[2:])
            else:
                blocks.append(("ul", [st[2:]]))
            continue
        if cur: blocks.append(("p", " ".join(cur))); cur = []
        if st.startswith("### "): blocks.append(("h3", st[4:]))
        elif st.startswith("## "): blocks.append(("h2", st[3:]))
        elif re.match(r"\[PHOTO (pair|landscape|portrait)\]$", st): blocks.append(("photo", st[7:-1]))
        elif re.match(r"\[MAP ", st): blocks.append(("map", st[5:-1]))
        elif st.startswith("> Tip:"): blocks.append(("tip", st[6:].strip()))
        elif st.startswith("[CAPTION "): blocks.append(("cap", st[9:-1]))
        else: cur.append(st)
    if cur: blocks.append(("p", " ".join(cur)))
    return fm, blocks


def heading(txt, default_id):
    m = re.search(r"\s*\{#([^}]+)\}\s*$", txt)
    hid = m.group(1) if m else default_id
    return html_inline(txt[:m.start()] if m else txt), hid


def field_notes_map(plan, kind, key, have=None):
    have = have or {"style": False, "script": False}
    fn = open(os.path.join(ROOT, plan["field_notes"]), encoding="utf-8").read()
    if kind == "city":
        for m in re.finditer(r'<figure class="citymap-fig">.*?</figure>', fn, re.S):
            if re.search(r'city-maps/%s(?:-mobile)?\.(?:png|webp)' % re.escape(key), m.group(0)):
                # the map's own <style> and <script> sit right before and after the figure in the field notes; a guide
                # needs both (the shell's head CSS is generic), unless the prose already carries them in raw blocks
                before = fn[max(0, m.start() - 20000):m.start()]; after = fn[m.end():m.end() + 40000]
                st = re.search(r"(<style>(?:(?!</style>).)*\.citymap-fig\{(?:(?!</style>).)*</style>)\s*$", before, re.S)
                sc = re.match(r"\s*(<script>(?:(?!</script>).)*forEach\(setup\)(?:(?!</script>).)*</script>)", after, re.S)
                parts = [st.group(1) if st and not have["style"] else "", m.group(0), sc.group(1) if sc and not have["script"] else ""]
                return "\n    ".join(p for p in parts if p).replace("../Images/", "../../../Images/")
        raise SystemExit("no city map '%s' in %s" % (key, plan["field_notes"]))
    m = re.search(r'<div class="route-map">.*?<figure class="itinerary-map".*?</figure>\s*</div>', fn, re.S)
    if not m:
        raise SystemExit("no route map in " + plan["field_notes"])
    return m.group(0).replace("../Images/", "../../../Images/")


def render_body(plan, fm, blocks):
    out, top10 = [], fm.get("type") == "top10"
    h2s = []
    raws = "\n".join(v for k, v in blocks if k == "raw")
    have = {"style": ".citymap-fig{" in raws, "script": "forEach(setup)" in raws}
    for kind, val in blocks:
        if kind == "h2":
            t, hid = heading(val, slug(val)); h2s.append((hid, t))
            out.append('<h2 class="article-h2" id="%s"%s>%s</h2>' % (hid, TOP10_H2_STYLE if top10 else "", t))
        elif kind == "h3":
            t, hid = heading(val, slug(val)); out.append('<h3 class="article-h3" id="%s">%s</h3>' % (hid, t))
        elif kind == "p":
            out.append("<p>%s</p>" % html_inline(val))
        elif kind == "ul":
            out.append(UL + "\n" + "\n".join("      " + LI + html_inline(x) + "</li>" for x in val) + "\n    </ul>")
        elif kind == "photo":
            out.append({"pair": PAIR, "landscape": LAND, "portrait": PORTRAIT}[val])
        elif kind == "map":
            k, _, key = val.partition(":")
            out.append(field_notes_map(plan, k, key, have))
        elif kind == "tip":
            out.append('<div class="article-tip"><strong>Tip:</strong> %s</div>' % html_inline(val))
        elif kind == "cap":
            out.append('<div class="img-caption" style="width:79%%;margin:-1rem auto 1.75rem;text-align:center;font-family:Hanken Grotesk,sans-serif;'
                       'font-size:.85rem;font-style:italic;color:rgba(28,40,33,.55)">%s</div>' % html_inline(val))
        elif kind == "raw":
            out.append(val)
    body = "\n    ".join(out)
    if top10:
        rows = ['<div class="toc-row"><span class="toc-num">%02d</span><a href="#%s" class="toc-link">%s</a></div>' % (i + 1, hid, re.sub(r"^\d+\.\s*", "", t))
                for i, (hid, t) in enumerate(h2s)]
        half = (len(rows) + 1) // 2
        toc = ('<!-- TOC -->\n    <div id="toc-b">\n      <div class="toc-label">In this guide</div>\n      <div class="toc-title">%s</div>\n      <div class="toc-cols">\n        <div>\n          %s\n        </div>\n        <div>\n          %s\n        </div>\n      </div>\n    </div>\n\n    '
               % (fm.get("toc_title", ""), "\n          ".join(rows[:half]), "\n          ".join(rows[half:])))
        body = toc + body
    return body


def esc_attr(s):
    return s.replace("&", "&amp;").replace('"', "&quot;")


def render(plan, md):
    fm, blocks = parse_prose(md)
    typ = fm.get("type", "guide")
    shell_path = os.path.join(ROOT, plan["shells"]["top10" if typ == "top10" else "guide"])
    S = open(shell_path, encoding="utf-8").read()
    fname = fm["file"]; stem = fname[:-5]
    old_id = re.search(r'<div class="page active fade-in" id="([^"]+)">', S).group(1)
    new_id = "page-article-%s-%s" % (plan["cc"], stem)
    country, label = plan["country"], plan["label"]
    url = "https://getawayguide.io/%s/%s" % (country, fname)
    old_url = re.search(r'<link rel="canonical" href="([^"]+)"', S).group(1)
    old_country = re.search(r"getawayguide\.io/([a-z-]+)/", old_url).group(1)
    title, desc = fm["title"], fm.get("description", "")
    head, body_open, tail = S[:S.index('<div class="article-body"')], S[S.index('<div class="article-body"'):], None
    body_open = body_open[:body_open.index(">") + 1]
    tail = S[S.index("<!-- /explore-more -->") + len("<!-- /explore-more -->"):] if "<!-- /explore-more -->" in S else S[S.index("\n</div>\n</div>", S.index('<div class="article-body"')):]
    # head: meta, title, json-ld, hero
    head = head.replace(old_url, url).replace("https://getawayguide.io/Images/web/og/%s.jpg" % old_country, "https://getawayguide.io/Images/web/og/%s.jpg" % country)
    head = re.sub(r'(<meta name="description" content=")[^"]*', lambda m: m.group(1) + esc_attr(desc), head)
    head = re.sub(r'(<meta property="og:description" content=")[^"]*', lambda m: m.group(1) + esc_attr(desc), head)
    head = re.sub(r'(<meta property="og:title" content=")[^"]*', lambda m: m.group(1) + esc_attr(title), head)
    head = re.sub(r"<title>.*?</title>", "<title>%s</title>" % htmlmod.escape(title, quote=False), head, flags=re.S)
    jt = json.dumps(title, ensure_ascii=False)[1:-1]; jd = json.dumps(desc, ensure_ascii=False)[1:-1]
    head = re.sub(r'("headline":\s*")[^"]*', lambda m: m.group(1) + jt, head)
    head = re.sub(r'("description":\s*")[^"]*', lambda m: m.group(1) + jd, head)
    head = re.sub(r'("name":\s*")[^"]*(",\s*"item":\s*"%s)' % re.escape(url), lambda m: m.group(1) + jt + m.group(2), head)
    head = re.sub(r'("date(?:Published|Modified)":\s*")[^"]*', lambda m: m.group(1) + plan["date"], head)
    head = re.sub(r'<style id="hero-tiers">.*?</style>\n?', "", head, flags=re.S)
    head = re.sub(r'(<style id="bg-fit">).*?(</style>)', r"\1\n/* tools/fit_backgrounds.py: each background photo at the width this window draws it. Do not edit by hand. */\n\2", head, flags=re.S)
    head = head.replace('id="%s"' % old_id, 'id="%s"' % new_id)
    # the hero starts on the country placeholder whatever cover the shell has since been given (the Bosnia shells got
    # their own covers on 2026-10-30, and a new page inherited Sarajevo's); the cover step replaces it
    head = re.sub(r"(class=\"article-hero[^\"]*\" style=\"background:url\(')[^']+('\))",
                  lambda m: m.group(1) + "../../../Images/web/dest-cards/" + country + ".jpg" + m.group(2), head, count=1)
    head = re.sub(r"(location\.href='index\.html'[^>]*>)[^<]*(</span>)", lambda m: m.group(1) + htmlmod.escape(label, quote=False) + m.group(2), head)
    # a one-city country has no index draft (Kevin, 2026-10-31: "for places I went to 1 city, then just do 1 article"),
    # so the country pill goes to the live field notes
    if plan.get("single_city"):
        head = head.replace("location.href='index.html'", "location.href='../../../%s/field-notes.html'" % country)
    head = re.sub(r"(color:#7ED4A8[^>]*>)[^<]*(</p>)", lambda m: m.group(1) + htmlmod.escape(fm.get("kicker", "Travel Guide"), quote=False) + m.group(2), head)
    head = re.sub(r'(<h1 class="article-title"[^>]*>).*?(</h1>)', lambda m: m.group(1) + html_inline(fm["h1"]) + m.group(2), head, flags=re.S)
    head = re.sub(r'(<p class="article-lead"[^>]*>).*?(</p>)', lambda m: m.group(1) + html_inline(fm.get("lead", "")) + m.group(2), head, flags=re.S)
    head = re.sub(r"(text-transform:uppercase;color:var\(--cream\)\">)[^<]*(</div>)", lambda m: m.group(1) + plan["date_line"] + m.group(2), head)
    # tail: page id, contents-box title; the box then builds itself from the headings (toc_live)
    tail = tail.replace(old_id, new_id)
    old_toc = re.search(r'toc-title\\?">([^<\\]*)', tail)
    if old_toc and fm.get("toc_title"):
        tail = tail.replace(old_toc.group(0), old_toc.group(0).replace(old_toc.group(1), fm["toc_title"]))
    page = head + body_open + "\n    " + render_body(plan, fm, blocks) + "\n  " + tail
    import toc_live
    page, _ = toc_live.convert(page)
    return page


def render_index(plan, md):
    """The country page: an accepted index as the shell; the hero outline from the live field notes; cards from the plan."""
    fm, blocks = parse_prose(md)
    S = open(os.path.join(ROOT, plan["shells"]["index"]), encoding="utf-8").read()
    fn = open(os.path.join(ROOT, plan["field_notes"]), encoding="utf-8").read()
    country, label = plan["country"], plan["label"]
    old_country = re.search(r'id="page-([a-z-]+)"', S).group(1)
    svg = re.search(r'class="country-hero-map[^"]*"[^>]*>\s*(<svg.*?</svg>)', fn, re.S)
    if svg:
        S = re.sub(r'(id="hero-map">\s*)<svg.*?</svg>', lambda m: m.group(1) + svg.group(1), S, count=1, flags=re.S)
    S = re.sub(r"<!-- Outline map of [^>]*-->", "<!-- Outline map of %s as SVG background -->" % label, S)
    S = re.sub(r'("@type":\s*"WebPage",\s*"name":\s*")[^"]*', lambda m: m.group(1) + json.dumps(fm["title"], ensure_ascii=False)[1:-1], S)
    S = S.replace('id="page-%s"' % old_country, 'id="page-%s"' % country)
    S = S.replace("https://getawayguide.io/%s/" % old_country, "https://getawayguide.io/%s/" % country)
    S = S.replace("og/%s.jpg" % old_country, "og/%s.jpg" % country).replace("dest-cards/%s.jpg" % old_country, "dest-cards/%s.jpg" % country)
    # no blanket swap of the shell country's field-notes link: the nav has one too, and the cards are rebuilt below
    S = re.sub(r"<title>.*?</title>", "<title>%s</title>" % htmlmod.escape(fm["title"], quote=False), S, flags=re.S)
    for key in ("og:title",):
        S = re.sub(r'(<meta property="%s" content=")[^"]*' % key, lambda m: m.group(1) + esc_attr(fm["title"]), S)
    for key in ("description", "og:description"):
        S = re.sub(r'(<meta (?:name|property)="%s" content=")[^"]*' % key, lambda m: m.group(1) + esc_attr(fm.get("description", "")), S)
    S = re.sub(r'("headline":\s*")[^"]*', lambda m: m.group(1) + json.dumps(fm["title"], ensure_ascii=False)[1:-1], S)
    S = re.sub(r'("description":\s*")[^"]*', lambda m: m.group(1) + json.dumps(fm.get("description", ""), ensure_ascii=False)[1:-1], S)
    S = re.sub(r'("date(?:Published|Modified)":\s*")[^"]*', lambda m: m.group(1) + plan["date"], S)
    S = re.sub(r'(<h1 class="country-hero-title">).*?(</h1>)', lambda m: m.group(1) + htmlmod.escape(label, quote=False) + m.group(2), S, flags=re.S)
    intro = " ".join(v for k, v in blocks if k == "p")
    S = re.sub(r'(<p class="country-hero-desc">).*?(</p>)', lambda m: m.group(1) + html_inline(intro) + m.group(2), S, flags=re.S)
    S = re.sub(r'(<a href="\.\./\.\./\.\./destinations\.html#)[a-z-]+("[^>]*onclick="">)[^<]*(</a>)\s*<span>›</span>\s*([^<\n]+)',
               lambda m: m.group(1) + plan["continent"][0] + m.group(2) + plan["continent"][1] + m.group(3) + "\n      <span>›</span>\n      " + plan["region"], S, count=1)
    btns = "\n      ".join('<button class="country-place-btn" onclick="location.href=\'%s\'">%s</button>' % (c["file"], htmlmod.escape(c["place"], quote=False))
                           for c in plan["cards"] if c.get("place"))
    S = re.sub(r'(<span class="country-places-label">Places to visit</span>\s*).*?(\s*</div>)', lambda m: m.group(1) + btns + m.group(2), S, count=1, flags=re.S)
    cards = []
    for i, c in enumerate(plan["cards"]):
        href = c["file"] if c["file"] != "field-notes.html" else "../../../%s/field-notes.html" % country
        cards.append('<div class="country-article-card" onclick="location.href=\'%s\'" style="background:url(\'../../../Images/web/dest-cards/%s.jpg\') center %d%%/cover no-repeat;cursor:pointer" data-bgfit="%d">\n'
                     '        <div class="country-article-card-overlay"></div>\n        <div class="country-article-card-body">\n'
                     '          <div class="country-article-card-tag">%s</div>\n          <h3 class="country-article-card-title">%s</h3>\n        </div>\n      </div>'
                     % (href, country, 40 + (i * 7) % 25, i + 1, htmlmod.escape(c["tag"], quote=False), htmlmod.escape(c["title"], quote=False)))
    S = re.sub(r'(<div class="country-articles-grid">\s*).*?(\s*</div>\s*</section>)', lambda m: m.group(1) + "\n      ".join(cards) + m.group(2), S, count=1, flags=re.S)
    return S


# ----------------------------------------------------------------------------- check: round trip
def normalize(s):
    s = re.sub(r"<nav>.*?</nav>", "<NAV/>", s, flags=re.S)
    s = re.sub(r'<div class="mobile-nav-drawer".*?(?=<div class="page )', "<DRAWER/>", s, flags=re.S)
    s = re.sub(r'<section class="xm".*?</section>\s*<!-- /explore-more -->', "", s, flags=re.S)
    s = re.sub(r"<picture>.*?</picture>", "[PHOTO]", s, flags=re.S)
    s = re.sub(r'<div class="photo-ph"[^>]*>\[PHOTO\]</div>', "[PHOTO]", s)
    s = re.sub(r"<p><span style=\"[^\"]*\"></span></p>", "", s)
    s = re.sub(r"<span style=\"display:block;position:relative;overflow:hidden;width:100%;aspect-ratio:2/3;border-radius:2px;?\">", "<CELL>", s)
    s = re.sub(r"page-article-[a-z0-9-]+", "page-article-X", s)              # the page id is the renderer's to choose
    s = s.replace("border-bottom:1px solid rgba(26,22,16,.1);", "")          # two list styles are in use; both are fine
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def extract_index(path):
    s = open(path, encoding="utf-8").read()
    def g1(p):
        mm = re.search(p, s, re.S); return htmlmod.unescape(mm.group(1).strip()) if mm else ""
    return ("---\nfile: index.html\ntype: index\ntitle: %s\ndescription: %s\n---\n\n%s\n"
            % (g1(r"<title>(.*?)</title>"), g1(r'<meta name="description" content="([^"]*)"'), md_inline(g1(r'<p class="country-hero-desc">(.*?)</p>'))))


def check(plan, pages):
    import difflib, toc_live
    total = 0
    for p in pages:
        if os.path.basename(p) == "index.html":
            got = render_index(plan, extract_index(p)); want = open(p, encoding="utf-8").read()
        else:
            md = extract(p)
            got = render(plan, md)
            want, _ = toc_live.convert(open(p, encoding="utf-8").read())
        a, b = normalize(want), normalize(got)
        if a == b:
            print("OK   ", os.path.basename(p)); continue
        wa = re.split(r"(?<=>)\s*(?=<)", a); wb = re.split(r"(?<=>)\s*(?=<)", b)
        d = [l for l in difflib.unified_diff(wa, wb, lineterm="", n=0) if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        total += len(d)
        print("DIFF ", os.path.basename(p), len(d), "lines")
        for l in d[:14]:
            print("   ", l[:220])
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract"); e.add_argument("page"); e.add_argument("--out")
    r = sub.add_parser("render"); r.add_argument("plan"); r.add_argument("prose"); r.add_argument("--out")
    c = sub.add_parser("check"); c.add_argument("plan"); c.add_argument("pages", nargs="+")
    a = ap.parse_args()
    if a.cmd == "extract":
        md = extract(a.page)
        if a.out: open(a.out, "w", encoding="utf-8").write(md); print("wrote", a.out, len(md.split()), "words")
        else: sys.stdout.write(md)
    elif a.cmd == "render":
        plan = json.load(open(a.plan, encoding="utf-8")); md = open(a.prose, encoding="utf-8").read()
        fm, _ = parse_prose(md)
        page = render_index(plan, md) if fm.get("type") == "index" else render(plan, md)
        out = a.out or os.path.join(ROOT, plan["folder"], fm["file"])
        if os.path.exists(out) and not a.out:
            raise SystemExit("refusing to overwrite %s (render once; after that the page is the source of truth)" % out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "w", encoding="utf-8", newline="").write(page); print("wrote", out, len(page), "bytes")
    elif a.cmd == "check":
        plan = json.load(open(a.plan, encoding="utf-8"))
        sys.exit(1 if check(plan, a.pages) else 0)


if __name__ == "__main__":
    main()
