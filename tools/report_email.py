#!/usr/bin/env python3
"""Render an audit report as an email that reads like a page of getawayguide.io.

The scheduled routines (publish hygiene, prose sweep, link check) all send their findings by
email. Left to themselves they each invent a layout, so this is the one place the look lives.

It is built as the site's own article page, in the site's own order, because a report that
looks like the thing it reports on is easier to trust and easier to read:

    nav      white, hairline rule, the wordmark in Newsreader italic green   (theme.css)
    hero     the void ground under the article gradient, kicker + title + lead, left
             aligned, exactly as .article-hero.gg-banner sets it              (styles.css)
    body     Hanken Grotesk at 15/1.7; each check an .article-h2 with its hairline rule;
             each file an .article-h3, behind a 2px terra rule                (styles.css)
    footer   the void footer: wordmark, "Dispatches from the road", links     (styles.css)

    python tools/report_email.py findings.json > email.html
    cat findings.json | python tools/report_email.py > email.html
    python tools/report_email.py findings.json --preview > .tmp/previews/x.html
        ^ --preview also links the site's local fonts.css so the file renders with the real
          faces offline. Routines send WITHOUT it.
    python tools/report_email.py findings.json --dark > x.html
        ^ renders the DARK palette directly, for checking it without a dark-mode client.

Input JSON (tools/audit.py writes exactly this):
    {
      "title":    "Publish hygiene",              # the kicker
      "subtitle": "50 published pages checked",   # the hero's lead line
      "status":   "findings" | "clean",
      "summary":  "22 findings, 2 worth acting on today",   # the hero's headline
      "lead":     ["optional paragraph", "..."],
      "groups": [
        {"name": "kosovo/field-notes.html",
         "section": "Image tiers",                # optional: groups the files under a heading
         "note": "optional one-line context",
         "findings": [
            {"severity": "high"|"medium"|"low",
             "rule":   "webp-missing",
             "detail": "all 22 photos ship without a WebP source",
             "value":  "<optional exact offending value, shown monospaced>"}
         ]}
      ],
      "footer": "Report only - nothing was changed."
    }

EMAIL HTML IS NOT WEB HTML. Tables for layout, every style inlined, no flexbox or grid, a
600px cap, and web fonts that WILL fail to load in Gmail - so every stack ends in Georgia or
a system sans, and the hero's gradient sits on a solid colour that Outlook keeps when it
drops the gradient.

DARK MODE is the one place a <style> block earns its keep. Left alone, a phone in dark mode
inverts the card to something muddy and picks its own text colours, which is why the mail did
not match the preview. So: the inline styles stay the LIGHT baseline (a client that strips
<style> still gets a correct light email), `color-scheme` tells the client the mail handles
both, and the block below repaints a DESIGNED dark palette through classes. Same green, same
faces, the site's void as the page ground. Every dark rule needs !important to beat inline.
"""
import html
import json
import sys
from datetime import date

# The site's palette, from theme.css :root.
INK = "#1C2821"      # --ink / --void
GREEN = "#2D6B50"    # --terra
MIST = "#F5F5F2"     # --mist
WHITE = "#FFFFFF"    # --cream
MUTED = "#6B7A72"    # ink, lightened: captions, the footer
LINE = "rgba(28,40,33,.12)"
RULE = "rgba(28,40,33,.09)"
VOID_TEXT = "#EDE8DC"

# The dark palette. Not an inversion: the site's own void becomes the page, the card sits a
# step above it, and the green lifts to stay legible on dark (2D6B50 on near-black fails).
D_PAGE = "#101412"
D_CARD = "#191F1B"
D_TEXT = "#E7E3D8"
D_MUTED = "#96A399"
D_GREEN = "#7FC9A1"
D_LINE = "rgba(231,227,216,.13)"
D_BOX = "#0E1210"

DISPLAY = "'Newsreader',Georgia,'Iowan Old Style','Times New Roman',serif"
BODY = "'Hanken Grotesk',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
MONO = "'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"

FONT_LINK = ("https://fonts.googleapis.com/css2?"
             "family=Hanken+Grotesk:ital,wght@0,300;0,400;0,500;0,600;1,400"
             "&family=Newsreader:ital,wght@0,300;0,400;0,500;1,300&display=swap")
LOCAL_FONTS = "../../fonts.css"
SITE = "https://getawayguide.io"

# .article-hero-bg: that gradient at 30% over --void. Flattened here because an email cannot
# stack a positioned overlay, and carried as a background-image so Outlook falls back to INK.
# It reads correctly in both schemes, so dark mode leaves it alone.
HERO_GRADIENT = "linear-gradient(160deg,#3C6B55 0%,#2F5943 30%,#5A4536 65%,#241A12 100%)"

SEV = {
    "high":   ("#B3261E", "rgba(179,38,30,.10)", "Worth fixing", "#FF9A92", "rgba(255,154,146,.13)"),
    "medium": ("#8A5A00", "rgba(138,90,0,.10)",  "Minor",        "#E8B964", "rgba(232,185,100,.13)"),
    "low":    (MUTED,     "rgba(28,40,33,.06)",  "Cosmetic",     D_MUTED,   "rgba(150,163,153,.13)"),
}

DARK_CSS = """
@import url('%(font)s');
:root{color-scheme:light dark;supported-color-scheme:light dark}
@media (prefers-color-scheme:dark){
  .pg{background:%(page)s!important}
  .card{background:%(card)s!important;border-color:%(line)s!important}
  .nav{background:%(card)s!important;border-bottom-color:%(line)s!important}
  .wm a,.wm{color:%(green)s!important}
  .navlink{color:%(text)s!important}
  .tx{color:%(text)s!important}
  .muted{color:%(muted)s!important}
  .h2{color:%(text)s!important;border-bottom-color:%(line)s!important}
  .h3{color:%(green)s!important;border-left-color:%(green)s!important}
  .box{background:%(box)s!important;color:%(text)s!important;border-left-color:%(line)s!important}
  .rule{color:%(muted)s!important}
  .foot{background:%(box)s!important}
  .sev-high{color:%(sh)s!important;background:%(bh)s!important}
  .sev-medium{color:%(sm)s!important;background:%(bm)s!important}
  .sev-low{color:%(sl)s!important;background:%(bl)s!important}
}
/* Outlook.com rewrites the same rules behind these attributes */
[data-ogsc] .pg{background:%(page)s!important}
[data-ogsc] .card,[data-ogsc] .nav{background:%(card)s!important;border-color:%(line)s!important}
[data-ogsc] .tx{color:%(text)s!important}
[data-ogsc] .muted,[data-ogsc] .rule{color:%(muted)s!important}
[data-ogsc] .h2{color:%(text)s!important}
[data-ogsc] .h3,[data-ogsc] .wm,[data-ogsc] .wm a{color:%(green)s!important}
[data-ogsc] .box{background:%(box)s!important;color:%(text)s!important}
""" % {"page": D_PAGE, "card": D_CARD, "text": D_TEXT, "muted": D_MUTED, "green": D_GREEN,
       "line": D_LINE, "box": D_BOX, "font": FONT_LINK,
       "sh": SEV["high"][3], "bh": SEV["high"][4], "sm": SEV["medium"][3], "bm": SEV["medium"][4],
       "sl": SEV["low"][3], "bl": SEV["low"][4]}


def esc(s):
    return html.escape(str(s), quote=True)


class Theme:
    """The two palettes behind one set of names, so render() is written once. `dark` forces
    the dark one for --dark; a real client picks via the media query in DARK_CSS."""

    def __init__(self, dark=False):
        self.dark = dark
        self.page = D_PAGE if dark else MIST
        self.card = D_CARD if dark else WHITE
        self.nav = D_CARD if dark else WHITE
        self.text = D_TEXT if dark else INK
        self.muted = D_MUTED if dark else MUTED
        self.green = D_GREEN if dark else GREEN
        self.line = D_LINE if dark else LINE
        self.rule = D_LINE if dark else RULE
        self.box = D_BOX if dark else MIST
        self.foot = D_BOX if dark else INK
        self.foot_text = VOID_TEXT

    def sev(self, s):
        c, b, label, dc, db = SEV.get(s, SEV["low"])
        return (dc, db, label) if self.dark else (c, b, label)


def pill(t, severity):
    colour, bg, label = t.sev(severity)
    return (f'<span class="sev-{esc(severity if severity in SEV else "low")}" '
            f'style="display:inline-block;padding:2px 8px;border-radius:999px;background:{bg};'
            f'color:{colour};font-family:{BODY};font-size:11px;font-weight:600;letter-spacing:.04em;'
            f'text-transform:uppercase;white-space:nowrap">{esc(label)}</span>')


def nav(t):
    links = "".join(
        f'<a class="navlink" href="{SITE}/{href}" style="font-family:{BODY};font-size:10px;'
        f'letter-spacing:.14em;text-transform:uppercase;color:{t.text};text-decoration:none;'
        f'opacity:.6;padding-left:16px">{esc(x)}</a>'
        for x, href in (("Home", "index.html"), ("Destinations", "destinations.html"), ("About", "about.html")))
    return (f'<tr><td class="nav" bgcolor="{t.nav}" style="padding:14px 30px;border-bottom:1px solid {t.line};background-color:{t.nav}">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td class="wm" align="left" style="font-family:{DISPLAY};font-style:italic;font-weight:300;'
            f'font-size:20px;color:{t.green};letter-spacing:-.01em">'
            f'<a href="{SITE}" style="color:{t.green};text-decoration:none">getawayguide</a></td>'
            f'<td align="right" style="white-space:nowrap">{links}</td></tr></table></td></tr>')


def hero(t, d):
    day = date.today()
    stamp = "%d %s %d" % (day.day, day.strftime("%B"), day.year)
    kicker = "%s &middot; %s" % (esc(d.get("title", "Report")).upper(), stamp.upper())
    lead = d.get("subtitle")
    return (f'<tr><td bgcolor="{INK}" style="padding:0;background-color:{INK};background-image:{HERO_GRADIENT}">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{INK}"><tr>'
            f'<td bgcolor="{INK}" style="padding:46px 30px 40px 30px;background-color:{INK};background-image:{HERO_GRADIENT}">'
            f'<div style="font-family:{BODY};font-size:10.5px;font-weight:600;letter-spacing:.18em;'
            f'text-transform:uppercase;color:rgba(255,255,255,.72)">{kicker}</div>'
            f'<div style="font-family:{DISPLAY};font-weight:300;font-size:31px;line-height:1.1;'
            f'letter-spacing:-.018em;color:#FFFFFF;margin-top:14px">{esc(d.get("summary", ""))}</div>'
            + (f'<div style="font-family:{BODY};font-size:14px;line-height:1.6;'
               f'color:rgba(255,255,255,.82);margin-top:14px">{esc(lead)}</div>' if lead else "")
            + '</td></tr></table></td></tr>')


def h2(t, text):
    return (f'<tr><td style="padding:34px 30px 0 30px">'
            f'<div class="h2" style="font-family:{DISPLAY};font-weight:400;font-size:21px;color:{t.text};'
            f'letter-spacing:-.01em;padding-bottom:11px;border-bottom:1px solid {t.rule}">{esc(text)}</div></td></tr>')


def h3(t, text):
    return (f'<tr><td style="padding:22px 30px 0 30px">'
            f'<div class="h3" style="font-family:{MONO};font-size:12.5px;font-weight:600;color:{t.green};'
            f'padding-left:11px;border-left:2px solid {t.green};line-height:1.5;word-break:break-all">'
            f'{esc(text)}</div></td></tr>')


def footer(t, text):
    links = "".join(
        f'<a href="{SITE}/{href}" style="font-family:{BODY};font-size:10px;letter-spacing:.15em;'
        f'text-transform:uppercase;color:{t.foot_text};text-decoration:none;opacity:.6;padding-left:18px">{esc(x)}</a>'
        for x, href in (("Contact", "contact.html"), ("Privacy", "privacy.html")))
    return (f'<tr><td class="foot" bgcolor="{t.foot}" style="padding:30px;background-color:{t.foot}">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td align="left" valign="bottom">'
            f'<div style="font-family:{DISPLAY};font-style:italic;font-size:22px;color:{t.foot_text}">getawayguide</div>'
            f'<div style="font-family:{BODY};font-size:9.5px;letter-spacing:.2em;text-transform:uppercase;'
            f'color:#8E9A8F;margin-top:6px">Dispatches from the road</div></td>'
            f'<td align="right" valign="bottom">{links}</td></tr></table>'
            + (f'<div style="font-family:{BODY};font-size:11px;line-height:1.6;color:#8E9A8F;'
               f'margin-top:20px;padding-top:14px;border-top:1px solid rgba(237,232,220,.14)">{esc(text)}</div>'
               if text else "")
            + '</td></tr>')


def render(d, preview=False, dark=False):
    t = Theme(dark)
    clean = d.get("status") == "clean"
    out = []
    a = out.append

    a('<!DOCTYPE html><html><head><meta charset="utf-8">'
      '<meta name="viewport" content="width=device-width,initial-scale=1">'
      '<meta name="color-scheme" content="light dark">'
      '<meta name="supported-color-scheme" content="light dark">')
    a(f'<link rel="stylesheet" href="{FONT_LINK}">')
    if preview:
        a(f'<link rel="stylesheet" href="{LOCAL_FONTS}">')
    a(f'<title>{esc(d.get("title", "Report"))}</title>')
    if not dark:
        a("<style>%s</style>" % DARK_CSS)
    a("</head>")

    a(f'<body class="pg" bgcolor="{t.page}" style="margin:0;padding:0;background-color:{t.page}">')
    a(f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{esc(d.get("summary", ""))}</div>')
    a(f'<table role="presentation" class="pg" width="100%" cellpadding="0" cellspacing="0" border="0" '
      f'bgcolor="{t.page}" style="background-color:{t.page};padding:22px 10px"><tr><td align="center">')
    a(f'<table role="presentation" class="card" width="600" cellpadding="0" cellspacing="0" border="0" '
      f'bgcolor="{t.card}" style="width:100%;max-width:600px;background-color:{t.card};border:1px solid {t.line};'
      f'border-radius:10px;overflow:hidden">')

    a(nav(t))
    a(hero(t, d))

    for para in d.get("lead", []) or []:
        a(f'<tr><td style="padding:24px 30px 0 30px">'
          f'<div class="tx" style="font-family:{BODY};font-size:15px;line-height:1.7;color:{t.text}">'
          f'{esc(para)}</div></td></tr>')

    if clean and not (d.get("groups") or []):
        a(f'<tr><td style="padding:30px 30px 6px 30px">'
          f'<div class="muted" style="font-family:{BODY};font-size:15px;line-height:1.7;color:{t.muted}">'
          f'Nothing to report. You are only getting this because you asked to see the clean runs too.'
          f'</div></td></tr>')

    section = None
    for g in d.get("groups", []) or []:
        if g.get("section") and g["section"] != section:
            section = g["section"]
            a(h2(t, section))
        a(h3(t, g.get("name", "")))
        if g.get("note"):
            a(f'<tr><td style="padding:9px 30px 0 30px">'
              f'<div class="muted" style="font-family:{BODY};font-size:13px;line-height:1.6;color:{t.muted}">'
              f'{esc(g["note"])}</div></td></tr>')
        for f in g.get("findings", []) or []:
            a(f'<tr><td style="padding:13px 30px 0 30px">'
              f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
              # A fixed width, or a short pill ("Minor") and a long one ("Worth fixing")
              # indent their rows differently and the value boxes stop lining up.
              f'<td valign="top" width="104" style="width:104px;padding-right:10px;white-space:nowrap">'
              f'{pill(t, f.get("severity", "low"))}</td>'
              f'<td class="tx" valign="top" style="font-family:{BODY};font-size:14px;line-height:1.6;color:{t.text}">'
              f'{esc(f.get("detail", ""))}')
            if f.get("rule"):
                a(f' <span class="rule" style="font-family:{MONO};font-size:11px;color:{t.muted}">'
                  f'[{esc(f["rule"])}]</span>')
            if f.get("value"):
                a(f'<div class="box" style="font-family:{MONO};font-size:12px;line-height:1.55;color:{t.text};'
                  f'background:{t.box};border-left:2px solid {t.line};padding:8px 11px;margin-top:8px;'
                  # pre-wrap, or HTML collapses the run of spaces and the double-space
                  # finding shows an excerpt with nothing visibly wrong with it
                  f'white-space:pre-wrap;word-break:break-word">{esc(f["value"])}</div>')
            a('</td></tr></table></td></tr>')

    a('<tr><td style="height:34px;line-height:34px;font-size:0">&nbsp;</td></tr>')
    a(footer(t, d.get("footer", "")))
    a('</table></td></tr></table></body></html>')
    return "\n".join(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        raw = open(args[0], encoding="utf-8").read()
    else:
        raw = sys.stdin.buffer.read().decode("utf-8")
    html = render(json.loads(raw), preview="--preview" in sys.argv, dark="--dark" in sys.argv)
    # Write the bytes ourselves. A redirected stdout on Windows encodes with the locale
    # codepage, so an em dash in an excerpt left cp1252's 0x97 in a file whose own <meta>
    # promises utf-8, and every dash in the mail rendered as the replacement character.
    sys.stdout.buffer.write(html.encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
