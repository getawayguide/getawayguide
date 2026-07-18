#!/usr/bin/env python3
"""
add_structured_data.py — inject JSON-LD structured data into live pages for richer
Google results. Derives everything from each page's existing <title>/OG/meta tags,
so it's safe to re-run. Skips pages that already have a <script type="application/ld+json">.

  - index.html           -> WebSite + Organization
  - */field-notes.html   -> Article + BreadcrumbList (Home > Destinations > Country)
  - article sub-pages    -> Article + BreadcrumbList
  - other content pages  -> WebPage

Usage: python tools/add_structured_data.py [--dry-run]
"""
import os, re, glob, json, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRY = "--dry-run" in sys.argv
FORCE = "--force" in sys.argv   # strip & regenerate existing JSON-LD
DOMAIN = "https://getawayguide.io"
EXCLUDE = {"editor.html"}
PUBLISHER = {"@type": "Organization", "name": "Getawayguide", "url": DOMAIN + "/",
             "logo": {"@type": "ImageObject", "url": DOMAIN + "/og-image.svg"}}
AUTHOR = {"@type": "Person", "name": "Kevin Phan"}

def rel(p): return os.path.relpath(p, ROOT).replace("\\", "/")
def meta(html, prop, attr="property"):
    m = re.search(r'<meta %s="%s" content="([^"]*)"' % (attr, re.escape(prop)), html)
    return m.group(1) if m else None
def unescape(s): return (s or "").replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')

def blocks_for(r, html):
    url = meta(html, "og:url") or (DOMAIN + "/" + (r if r != "index.html" else ""))
    title = unescape(meta(html, "og:title")) or unescape(re.search(r"<title>(.*?)</title>", html, re.S).group(1))
    desc = unescape(meta(html, "og:description")) or unescape(meta(html, "description", "name"))
    image = meta(html, "og:image") or (DOMAIN + "/og-image.svg")
    out = []

    if r == "index.html":
        out.append({"@context": "https://schema.org", "@type": "WebSite",
                    "name": "Getawayguide", "url": DOMAIN + "/",
                    "description": desc, "publisher": PUBLISHER})
        out.append({"@context": "https://schema.org", **PUBLISHER,
                    "sameAs": ["https://www.instagram.com/kphanthaman"]})
        return out

    is_content = r.endswith("field-notes.html") or (("/" in r) and r not in ("",))
    is_article = r.endswith("field-notes.html") or re.search(r"/(?!index\.html)[a-z0-9-]+\.html$", r)
    if is_article:
        # country name from title: "Country — Field Notes — getawayguide" / "Title — getawayguide"
        country = re.split(r"\s+[—-]\s+", title)[0].strip()
        crumbs = [("Home", DOMAIN + "/"), ("Destinations", DOMAIN + "/destinations.html")]
        # continent from the PAGE breadcrumb (not the nav dropdown), if any
        bc = re.search(r'class="country-breadcrumb".*?</p>', html, re.S)
        cm = re.search(r'destinations\.html#([a-z]+)"[^>]*>([^<]+)</a>', bc.group(0)) if bc else None
        if cm: crumbs.append((cm.group(2).strip(), DOMAIN + "/destinations.html#" + cm.group(1)))
        crumbs.append((country, url))
        out.append({"@context": "https://schema.org", "@type": "Article",
                    "headline": title, "description": desc, "image": image, "url": url,
                    "author": AUTHOR, "publisher": PUBLISHER,
                    "mainEntityOfPage": {"@type": "WebPage", "@id": url}})
        out.append({"@context": "https://schema.org", "@type": "BreadcrumbList",
                    "itemListElement": [{"@type": "ListItem", "position": i + 1,
                                         "name": n, "item": u} for i, (n, u) in enumerate(crumbs)]})
    else:
        out.append({"@context": "https://schema.org", "@type": "WebPage",
                    "name": title, "url": url, "description": desc, "publisher": PUBLISHER})
    return out

changed = 0
for p in glob.glob(os.path.join(ROOT, "**", "*.html"), recursive=True):
    r = rel(p)
    if r.startswith(("Drafts/", ".tmp/", ".git/")) or os.path.basename(r) in EXCLUDE: continue
    html = open(p, encoding="utf-8").read()
    if "application/ld+json" in html:
        if not FORCE:
            continue
        html = re.sub(r'\n?<script type="application/ld\+json">.*?</script>', "", html, flags=re.S)
    blocks = blocks_for(r, html)
    if not blocks: continue
    snippet = "\n".join('<script type="application/ld+json">\n%s\n</script>'
                        % json.dumps(b, ensure_ascii=False, indent=2) for b in blocks)
    new = html.replace("</head>", snippet + "\n</head>", 1)
    if new != html:
        if not DRY: open(p, "w", encoding="utf-8", newline="").write(new)
        changed += 1
        print("%s%s (%d block%s)" % ("[dry] " if DRY else "", r, len(blocks), "s" if len(blocks) > 1 else ""))
print("\n%s%d pages updated with JSON-LD" % ("[dry-run] " if DRY else "", changed))
