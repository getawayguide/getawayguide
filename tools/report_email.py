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
             each file an .article-h3, italic terra behind a 2px terra rule   (styles.css)
    footer   the void footer: wordmark, "Dispatches from the road", links     (styles.css)

    python tools/report_email.py findings.json > email.html
    cat findings.json | python tools/report_email.py > email.html
    python tools/report_email.py findings.json --preview > .tmp/previews/x.html
        ^ --preview also links the site's local fonts.css so the file renders with the real
          faces offline. Routines send WITHOUT it.

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

EMAIL HTML IS NOT WEB HTML. Tables for layout, every style inlined, no flexbox or grid, no
<style> block worth relying on, a 600px cap, and web fonts that WILL fail to load in Gmail -
so every stack ends in Georgia or a system sans, and the hero's gradient sits on a solid
background colour that Outlook keeps when it drops the gradient. Keep it that way.
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
VOID_TEXT = "#EDE8DC"

DISPLAY = "'Newsreader',Georgia,'Times New Roman',serif"
BODY = "'Hanken Grotesk',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"

# The site's two faces, hosted. Naming a family is not loading it: without this every client
# fell back to Georgia/Arial and the mail looked nothing like the site. Clients split on
# webfonts (Apple Mail and iOS honour the link, Gmail strips it), which is why the stacks
# above still have to degrade cleanly.
FONT_LINK = ("https://fonts.googleapis.com/css2?"
             "family=Hanken+Grotesk:ital,wght@0,300;0,400;0,500;0,600;1,400"
             "&family=Newsreader:ital,wght@0,300;0,400;0,500;1,300&display=swap")
LOCAL_FONTS = "../../fonts.css"
SITE = "https://getawayguide.io"

# .article-hero-bg: this gradient at 30% over --void. Flattened here because an email cannot
# stack a positioned overlay, and carried as a background-image so Outlook falls back to INK.
HERO_GRADIENT = ("linear-gradient(160deg,#3C6B55 0%,#2F5943 30%,#5A4536 65%,#241A12 100%)")

SEV = {
    "high":   ("#B3261E", "rgba(179,38,30,.10)", "Worth fixing"),
    "medium": ("#8A5A00", "rgba(138,90,0,.10)",  "Minor"),
    "low":    (MUTED,     "rgba(28,40,33,.06)",  "Cosmetic"),
}


def esc(s):
    return html.escape(str(s), quote=True)


def pill(severity):
    colour, bg, label = SEV.get(severity, SEV["low"])
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
            f'background:{bg};color:{colour};font-family:{BODY};font-size:11px;font-weight:600;'
            f'letter-spacing:.04em;text-transform:uppercase;white-space:nowrap">{esc(label)}</span>')


def nav():
    """The site's nav: white, a hairline under it, the wordmark left and the sections right."""
    links = "".join(
        f'<a href="{SITE}/{href}" style="font-family:{BODY};font-size:10px;letter-spacing:.14em;'
        f'text-transform:uppercase;color:{INK};text-decoration:none;opacity:.55;padding-left:16px">{esc(t)}</a>'
        for t, href in (("Home", "index.html"), ("Destinations", "destinations.html"), ("About", "about.html")))
    return (f'<tr><td style="padding:14px 30px;border-bottom:1px solid {LINE};background:{WHITE}">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td align="left" style="font-family:{DISPLAY};font-style:italic;font-weight:300;'
            f'font-size:20px;color:{GREEN};letter-spacing:-.01em">'
            f'<a href="{SITE}" style="color:{GREEN};text-decoration:none">getawayguide</a></td>'
            f'<td align="right" style="white-space:nowrap">{links}</td>'
            f'</tr></table></td></tr>')


def hero(d, clean):
    kicker = " &middot; ".join(x for x in [esc(d.get("title", "Report")).upper(),
                                           date.today().strftime("%-d %B %Y").upper()
                                           if sys.platform != "win32" else
                                           date.today().strftime("%d %B %Y").lstrip("0").upper()] if x)
    lead = d.get("subtitle")
    return (f'<tr><td style="padding:0;background:{INK};background-image:{HERO_GRADIENT}">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="padding:46px 30px 40px 30px">'
            f'<div style="font-family:{BODY};font-size:10.5px;font-weight:600;letter-spacing:.18em;'
            f'text-transform:uppercase;color:rgba(255,255,255,.72)">{kicker}</div>'
            f'<div style="font-family:{DISPLAY};font-weight:300;font-size:31px;line-height:1.1;'
            f'letter-spacing:-.018em;color:{WHITE};margin-top:14px">{esc(d.get("summary", ""))}</div>'
            + (f'<div style="font-family:{BODY};font-size:14px;line-height:1.6;'
               f'color:rgba(255,255,255,.82);margin-top:14px">{esc(lead)}</div>' if lead else "")
            + f'</td></tr></table></td></tr>')


def h2(text):
    """.article-h2: Newsreader, a hairline under it, generous space above."""
    return (f'<tr><td style="padding:34px 30px 0 30px">'
            f'<div style="font-family:{DISPLAY};font-weight:400;font-size:21px;color:{INK};'
            f'letter-spacing:-.01em;padding-bottom:11px;border-bottom:1px solid rgba(28,40,33,.09)">'
            f'{esc(text)}</div></td></tr>')


def h3(text):
    """.article-h3: italic terra behind a 2px terra rule. The file a finding belongs to."""
    return (f'<tr><td style="padding:22px 30px 0 30px">'
            f'<div style="font-family:{MONO};font-size:12.5px;font-weight:600;color:{GREEN};'
            f'padding-left:11px;border-left:2px solid {GREEN};line-height:1.5;word-break:break-all">'
            f'{esc(text)}</div></td></tr>')


def footer(text):
    """The site's void footer."""
    links = "".join(
        f'<a href="{SITE}/{href}" style="font-family:{BODY};font-size:10px;letter-spacing:.15em;'
        f'text-transform:uppercase;color:{VOID_TEXT};text-decoration:none;opacity:.55;padding-left:18px">{esc(t)}</a>'
        for t, href in (("Contact", "contact.html"), ("Privacy", "privacy.html")))
    return (f'<tr><td style="padding:30px;background:{INK}">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td align="left" valign="bottom">'
            f'<div style="font-family:{DISPLAY};font-style:italic;font-size:22px;color:{VOID_TEXT};opacity:.8">getawayguide</div>'
            f'<div style="font-family:{BODY};font-size:9.5px;letter-spacing:.2em;text-transform:uppercase;'
            f'color:{VOID_TEXT};opacity:.4;margin-top:6px">Dispatches from the road</div></td>'
            f'<td align="right" valign="bottom">{links}</td></tr></table>'
            + (f'<div style="font-family:{BODY};font-size:11px;line-height:1.6;color:{VOID_TEXT};'
               f'opacity:.5;margin-top:20px;padding-top:14px;border-top:1px solid rgba(237,232,220,.14)">'
               f'{esc(text)}</div>' if text else "")
            + '</td></tr>')


def render(d, preview=False):
    clean = d.get("status") == "clean"
    out = []
    a = out.append

    a('<!DOCTYPE html><html><head><meta charset="utf-8">'
      '<meta name="viewport" content="width=device-width,initial-scale=1">')
    a(f'<link rel="stylesheet" href="{FONT_LINK}">')
    if preview:
        a(f'<link rel="stylesheet" href="{LOCAL_FONTS}">')
    a(f'<title>{esc(d.get("title", "Report"))}</title></head>')

    a(f'<body style="margin:0;padding:0;background:{MIST}">')
    # Preheader: the grey line a client shows beside the subject. Hidden in the body itself.
    a(f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{esc(d.get("summary", ""))}</div>')
    a(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
      f'style="background:{MIST};padding:22px 10px"><tr><td align="center">')
    a(f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
      f'style="width:100%;max-width:600px;background:{WHITE};border:1px solid {LINE};'
      f'border-radius:10px;overflow:hidden">')

    a(nav())
    a(hero(d, clean))

    for para in d.get("lead", []) or []:
        a(f'<tr><td style="padding:24px 30px 0 30px">'
          f'<div style="font-family:{BODY};font-size:15px;line-height:1.7;color:{INK}">{esc(para)}</div></td></tr>')

    if clean and not (d.get("groups") or []):
        a(f'<tr><td style="padding:30px 30px 6px 30px">'
          f'<div style="font-family:{BODY};font-size:15px;line-height:1.7;color:{MUTED}">'
          f'Nothing to report. You are only getting this because you asked to see the clean runs too.'
          f'</div></td></tr>')

    section = None
    for g in d.get("groups", []) or []:
        if g.get("section") and g["section"] != section:
            section = g["section"]
            a(h2(section))
        a(h3(g.get("name", "")))
        if g.get("note"):
            a(f'<tr><td style="padding:9px 30px 0 30px">'
              f'<div style="font-family:{BODY};font-size:13px;line-height:1.6;color:{MUTED}">{esc(g["note"])}</div></td></tr>')
        for f in g.get("findings", []) or []:
            a(f'<tr><td style="padding:13px 30px 0 30px">'
              f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
              # A fixed width, or a short pill ("Minor") and a long one ("Worth fixing")
              # indent their rows differently and the value boxes stop lining up.
              f'<td valign="top" width="104" style="width:104px;padding-right:10px;white-space:nowrap">'
              f'{pill(f.get("severity", "low"))}</td>'
              f'<td valign="top" style="font-family:{BODY};font-size:14px;line-height:1.6;color:{INK}">'
              f'{esc(f.get("detail", ""))}')
            if f.get("rule"):
                a(f' <span style="font-family:{MONO};font-size:11px;color:{MUTED}">[{esc(f["rule"])}]</span>')
            if f.get("value"):
                a(f'<div style="font-family:{MONO};font-size:12px;line-height:1.55;color:{INK};'
                  f'background:{MIST};border-left:2px solid {LINE};padding:8px 11px;margin-top:8px;'
                  f'word-break:break-word">{esc(f["value"])}</div>')
            a('</td></tr></table></td></tr>')

    a(f'<tr><td style="height:34px;line-height:34px;font-size:0">&nbsp;</td></tr>')
    a(footer(d.get("footer", "")))
    a('</table></td></tr></table></body></html>')
    return "\n".join(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    preview = "--preview" in sys.argv
    raw = open(args[0], encoding="utf-8").read() if args else sys.stdin.read()
    sys.stdout.write(render(json.loads(raw), preview=preview))


if __name__ == "__main__":
    main()
