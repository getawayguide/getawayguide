#!/usr/bin/env python3
"""Build a page that shows how EVERY live page looks when its link is shared.

There is no way to eyeball link previews before publishing - the platform debuggers only
read a URL that is already live, and they cache aggressively. This reads each page's
Open Graph tags straight from the repo and renders them as the card Facebook, LinkedIn,
X, Slack, iMessage and WhatsApp all draw (image on top, domain, title, description), so
a broken or ugly preview is visible BEFORE it ships.

Also validates each one and shows a badge: missing tags, an image that isn't 1200x630,
a title/description that will be truncated, or an og:image that isn't a raster file
(no platform renders an SVG preview).

  py tools/gen_link_previews.py      ->  .tmp/link-previews.html
"""
import glob, html, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".tmp", "link-previews.html")
DOMAIN = "https://getawayguide.io/"
# platform truncation points (approx, from where each clips in a feed card)
TITLE_MAX, DESC_MAX = 60, 110


def meta(s, prop):
    m = (re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)["\']' % prop, s, re.I)
         or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % prop, s, re.I))
    return html.unescape(m.group(1)).strip() if m else ""


def collect():
    pages = sorted(p.replace("\\", "/") for p in
                   glob.glob(os.path.join(ROOT, "*.html")) + glob.glob(os.path.join(ROOT, "*", "*.html")))
    out = []
    for p in pages:
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.startswith((".tmp/", "Drafts/")):
            continue
        s = open(p, encoding="utf-8", errors="replace").read()
        img = meta(s, "og:image")
        if not img and not meta(s, "og:title"):
            continue                      # 404 / editor: deliberately not shareable
        local = img.replace(DOMAIN, "") if img.startswith(DOMAIN) else img
        abspath = os.path.join(ROOT, local.replace("/", os.sep))
        exists = bool(local) and os.path.exists(abspath)
        dims = ""
        if exists:
            try:
                from PIL import Image
                with Image.open(abspath) as im:
                    dims = "%dx%d" % im.size
            except Exception:
                dims = "?"
        problems = []
        if not img:
            problems.append("no og:image")
        elif not exists:
            problems.append("image file missing")
        elif re.search(r"\.svg($|\?)", local, re.I):
            problems.append("SVG - no platform renders this")
        elif dims not in ("1200x630", ""):
            problems.append("image is %s, not 1200x630" % dims)
        if not meta(s, "og:title"):
            problems.append("no og:title")
        if not meta(s, "og:description"):
            problems.append("no og:description")
        if meta(s, "twitter:card") != "summary_large_image":
            problems.append("twitter:card is not summary_large_image")
        out.append({
            "page": rel,
            "title": meta(s, "og:title"), "desc": meta(s, "og:description"),
            "url": meta(s, "og:url") or DOMAIN + rel,
            "img": ("../" + local) if exists else "",
            "dims": dims, "problems": problems,
        })
    return out


def card(d):
    t, dd = d["title"], d["desc"]
    tt = html.escape(t if len(t) <= TITLE_MAX else t[:TITLE_MAX - 1] + "…")
    dt = html.escape(dd if len(dd) <= DESC_MAX else dd[:DESC_MAX - 1] + "…")
    dom = re.sub(r"^https?://", "", d["url"]).split("/")[0].upper()
    bad = d["problems"]
    badge = ('<span class="ok">looks good</span>' if not bad else
             "".join('<span class="bad">%s</span>' % html.escape(b) for b in bad))
    img = ('<img class="ph" src="%s" alt="" loading="lazy">' % html.escape(d["img"])
           if d["img"] else '<div class="ph missing">no image</div>')
    over = []
    if len(t) > TITLE_MAX:
        over.append("title clipped (%d chars)" % len(t))
    if len(dd) > DESC_MAX:
        over.append("description clipped (%d chars)" % len(dd))
    note = ('<div class="note">%s</div>' % html.escape(" · ".join(over))) if over else ""
    return """<figure class="item" data-q="%s">
  <figcaption class="fp">%s <span class="dim">%s</span></figcaption>
  <div class="card">%s<div class="meta"><div class="dom">%s</div>
    <div class="t">%s</div><div class="d">%s</div></div></div>
  <div class="badges">%s</div>%s
</figure>""" % (html.escape((d["page"] + " " + t).lower()), html.escape(d["page"]),
                d["dims"], img, html.escape(dom), tt, dt, badge, note)


def main():
    items = collect()
    broken = sum(1 for d in items if d["problems"])
    groups = [("Site pages", lambda r: "/" not in r["page"]),
              ("El Salvador guide", lambda r: r["page"].startswith("el-salvador/")),
              ("Country field notes", lambda r: "/" in r["page"] and not r["page"].startswith("el-salvador/"))]
    body = []
    for name, pred in groups:
        rows = [d for d in items if pred(d)]
        if not rows:
            continue
        body.append('<h2>%s <span class="c">%d</span></h2><div class="grid">%s</div>'
                    % (name, len(rows), "".join(card(d) for d in rows)))
    doc = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link share previews &middot; getawayguide</title>
<style>
 :root{color-scheme:light dark}
 body{margin:0;padding:28px;background:#eceae4;color:#1C2821;
   font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
 h1{font:600 22px/1.3 Georgia,serif;margin:0 0 4px}
 .sub{opacity:.65;margin:0 0 18px;font-size:13px}
 .tools{position:sticky;top:0;background:#eceae4;padding:10px 0 14px;z-index:5;
   display:flex;gap:10px;align-items:center;flex-wrap:wrap}
 input{padding:8px 12px;border:1px solid #c9c8c1;border-radius:7px;font-size:14px;min-width:240px;background:#fff}
 button{padding:8px 12px;border:1px solid #c9c8c1;border-radius:7px;background:#fff;cursor:pointer;font-size:13px}
 button.on{background:#1C2821;color:#fff;border-color:#1C2821}
 h2{font:600 15px/1.3 Georgia,serif;margin:26px 0 12px;display:flex;gap:8px;align-items:center}
 h2 .c{font:500 11px/1 sans-serif;opacity:.55}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:22px}
 .item{margin:0}
 .fp{font:500 11px/1.4 ui-monospace,Menlo,Consolas,monospace;opacity:.7;margin-bottom:6px;
   display:flex;justify-content:space-between;gap:8px}
 .fp .dim{opacity:.6}
 /* the card shape Facebook / LinkedIn / X(summary_large_image) / Slack all draw */
 .card{border:1px solid #d4d2cb;border-radius:10px;overflow:hidden;background:#fff}
 .ph{display:block;width:100%;aspect-ratio:1200/630;object-fit:cover;background:#ddd}
 .ph.missing{display:flex;align-items:center;justify-content:center;color:#a33;
   font-size:12px;letter-spacing:.08em;text-transform:uppercase}
 .meta{padding:10px 12px 12px;border-top:1px solid #ecebe6}
 .dom{font-size:10.5px;letter-spacing:.06em;color:#65635e;text-transform:uppercase}
 .t{font-weight:600;margin:3px 0 2px;font-size:15px;line-height:1.3}
 .d{font-size:12.5px;color:#5d5b56;line-height:1.4}
 .badges{margin-top:7px;display:flex;flex-wrap:wrap;gap:5px}
 .ok,.bad{font-size:10.5px;padding:2px 7px;border-radius:99px;letter-spacing:.03em}
 .ok{background:#dcece1;color:#1c5136}.bad{background:#f6ddd9;color:#8d2b1c}
 .note{margin-top:5px;font-size:11px;opacity:.7}
 @media (prefers-color-scheme:dark){
   body{background:#191b19;color:#e8e6e0}.tools{background:#191b19}
   .card{background:#232624;border-color:#39403b}.meta{border-top-color:#333733}
   .d{color:#a9a7a1}.dom{color:#9a9893}input,button{background:#232624;color:#e8e6e0;border-color:#39403b}
   button.on{background:#e8e6e0;color:#191b19}
   .ok{background:#1f3b2c;color:#a9dcbf}.bad{background:#43231e;color:#f0b3a6}}
</style>
<h1>Link share previews</h1>
<p class="sub">How each page renders when its link is pasted into Facebook, LinkedIn, X, Slack,
iMessage or WhatsApp. Generated from the Open Graph tags in the repo, so it reflects what will
ship &mdash; not what those platforms have cached. <b>@@BROKEN@@ of @@TOTAL@@ pages have a problem.</b></p>
<div class="tools">
  <input id="q" placeholder="Filter by page or title&hellip;" oninput="f()">
  <button id="b-all" class="on" onclick="only(0)">All</button>
  <button id="b-bad" onclick="only(1)">Problems only</button>
</div>
@@BODY@@
<script>
var badOnly = 0;
function only(v){ badOnly = v;
  document.getElementById('b-all').className = v ? '' : 'on';
  document.getElementById('b-bad').className = v ? 'on' : ''; f(); }
function f(){
  var q = document.getElementById('q').value.toLowerCase();
  document.querySelectorAll('.item').forEach(function(el){
    var hit = el.dataset.q.indexOf(q) > -1;
    if (badOnly && !el.querySelector('.bad')) hit = false;
    el.style.display = hit ? '' : 'none';
  });
  document.querySelectorAll('h2').forEach(function(h){
    var g = h.nextElementSibling, any = false;
    g.querySelectorAll('.item').forEach(function(i){ if (i.style.display !== 'none') any = true; });
    h.style.display = g.style.display = any ? '' : 'none';
  });
}
</script>"""
    # plain substitution, not %-formatting: the CSS is full of literal % (width:100%)
    doc = (doc.replace("@@BROKEN@@", str(broken)).replace("@@TOTAL@@", str(len(items)))
              .replace("@@BODY@@", "".join(body)))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(doc)
    print("wrote %s" % OUT)
    print("  %d page(s), %d with a problem" % (len(items), broken))
    for d in items:
        if d["problems"]:
            print("   %-38s %s" % (d["page"], "; ".join(d["problems"])))


if __name__ == "__main__":
    main()
