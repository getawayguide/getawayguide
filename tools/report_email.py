#!/usr/bin/env python3
"""Render an audit report as a getawayguide-branded HTML email.

The scheduled routines (link check, publish hygiene, prose sweep) all send their
findings by email. Left to themselves they each invent their own layout, so this
is the one place the look lives: same palette, same structure, version-controlled
alongside the site it reports on.

    python tools/report_email.py findings.json > email.html
    cat findings.json | python tools/report_email.py > email.html
    python tools/report_email.py findings.json --preview > .tmp/previews/x.html
        ^ --preview also links the site's local fonts.css, so the file renders
          with the real faces offline. Routines send WITHOUT it.

Input JSON:
    {
      "title":    "Publish hygiene",           # the report's name
      "subtitle": "50 published pages checked", # what was covered
      "status":   "findings" | "clean",        # picks the accent + headline tone
      "summary":  "22 findings, 2 worth acting on today",
      "lead":     ["optional paragraph", "..."],   # the 'read this first' bit
      "groups": [
        {"name": "kosovo/field-notes.html",
         "note": "optional one-line context for the whole group",
         "findings": [
            {"severity": "high"|"medium"|"low",
             "rule":   "webp-missing",
             "detail": "all 22 photos ship without a WebP source",
             "value":  "<optional exact offending value, shown monospaced>"}
         ]}
      ],
      "footer": "Report only - nothing was changed."
    }

EMAIL HTML IS NOT WEB HTML. Tables for layout, every style inlined, no flexbox or
grid, no <style> block worth relying on, a 600px cap, and web fonts that WILL fail
to load - so each stack ends in Georgia or a system sans. Keep it that way.
"""
import html
import json
import sys

# The site's own palette, from theme.css :root.
INK = "#1C2821"     # --ink / --void
GREEN = "#2D6B50"   # --terra / --gold
MIST = "#F5F5F2"    # --mist
WHITE = "#FFFFFF"   # --cream
MUTED = "#6B7A72"   # ink, lightened - for captions and the footer
LINE = "rgba(28,40,33,.12)"

DISPLAY = "'Newsreader',Georgia,'Times New Roman',serif"
BODY = "'Hanken Grotesk',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"

# The site's two faces, hosted. Naming a family is not loading it: without this
# every client fell back to Georgia/Arial and the mail looked nothing like the
# site. Mail clients split on webfonts - Apple Mail and iOS honour this link,
# Gmail strips it - so the stacks above still have to degrade cleanly, which is
# why each one ends in Georgia or a system sans.
FONT_LINK = ("https://fonts.googleapis.com/css2?"
             "family=Hanken+Grotesk:ital,wght@0,300;0,400;0,500;0,600;1,400"
             "&family=Newsreader:ital,wght@0,400;0,500;1,300&display=swap")
# Rendering a PREVIEW locally has no internet, so it also links the site's own
# fonts.css (the same @font-face files theme.css imports) to show the real faces.
LOCAL_FONTS = "../../fonts.css"

SEV = {
    "high":   ("#B3261E", "rgba(179,38,30,.10)", "Worth fixing"),
    "medium": ("#8A5A00", "rgba(138,90,0,.10)",  "Minor"),
    "low":    (MUTED,     "rgba(28,40,33,.06)",  "Cosmetic"),
}


def esc(s):
    return html.escape(str(s), quote=True)


def pill(severity):
    colour, bg, label = SEV.get(severity, SEV["low"])
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
        f'background:{bg};color:{colour};font-family:{BODY};font-size:11px;'
        f'font-weight:600;letter-spacing:.04em;text-transform:uppercase;'
        f'white-space:nowrap">{esc(label)}</span>'
    )


def render(d, preview=False):
    clean = d.get("status") == "clean"
    accent = GREEN if clean else "#B3261E"
    out = []
    a = out.append

    a(f'<link rel="stylesheet" href="{FONT_LINK}">')
    if preview:
        a(f'<link rel="stylesheet" href="{LOCAL_FONTS}">')

    # Preheader: the grey line a mail client shows next to the subject. Hidden in
    # the body itself, which is the standard trick.
    a(f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">'
      f'{esc(d.get("summary", ""))}</div>')

    a(f'<body style="margin:0;padding:0;background:{MIST}">')
    a(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
      f'border="0" style="background:{MIST};padding:24px 12px"><tr><td align="center">')
    a(f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
      f'style="width:100%;max-width:600px;background:{WHITE};border:1px solid {LINE};'
      f'border-radius:10px;overflow:hidden">')

    # --- masthead ---
    # The site's own wordmark treatment, from theme.css .nav-logo:
    # display face, italic, weight 300, in terra green.
    a(f'<tr><td style="padding:26px 30px 0 30px">'
      f'<div style="font-family:{DISPLAY};font-style:italic;font-weight:300;'
      f'font-size:22px;color:{GREEN};letter-spacing:-.01em">getawayguide</div>'
      f'<div style="height:1px;background:{LINE};margin:16px 0 0 0"></div>'
      f'</td></tr>')

    # --- headline ---
    a(f'<tr><td style="padding:22px 30px 0 30px">'
      f'<div style="font-family:{BODY};font-size:11px;font-weight:600;'
      f'letter-spacing:.09em;text-transform:uppercase;color:{MUTED}">'
      f'{esc(d.get("title", "Report"))}</div>'
      f'<div style="font-family:{DISPLAY};font-size:27px;line-height:1.25;'
      f'color:{accent};margin-top:7px">{esc(d.get("summary", ""))}</div>')
    if d.get("subtitle"):
        a(f'<div style="font-family:{BODY};font-size:13px;color:{MUTED};margin-top:7px">'
          f'{esc(d["subtitle"])}</div>')
    a('</td></tr>')

    # --- lead paragraphs ---
    for para in d.get("lead", []) or []:
        a(f'<tr><td style="padding:16px 30px 0 30px">'
          f'<div style="font-family:{BODY};font-size:14px;line-height:1.55;color:{INK}">'
          f'{esc(para)}</div></td></tr>')

    # --- groups ---
    for g in d.get("groups", []) or []:
        a(f'<tr><td style="padding:26px 30px 0 30px">'
          f'<div style="font-family:{MONO};font-size:13px;font-weight:600;color:{INK};'
          f'padding-bottom:6px;border-bottom:1px solid {LINE}">{esc(g.get("name",""))}</div>')
        if g.get("note"):
            a(f'<div style="font-family:{BODY};font-size:12.5px;color:{MUTED};'
              f'margin-top:8px">{esc(g["note"])}</div>')
        a('</td></tr>')
        for f in g.get("findings", []) or []:
            a(f'<tr><td style="padding:12px 30px 0 30px">'
              f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
              # Fixed width, or a short pill ("Minor") and a long one ("Worth fixing")
              # indent their rows differently and the value boxes stop lining up.
              f'<td valign="top" width="104" style="width:104px;padding-right:10px;'
              f'white-space:nowrap">{pill(f.get("severity","low"))}</td>'
              f'<td valign="top" style="font-family:{BODY};font-size:13.5px;line-height:1.5;color:{INK}">'
              f'{esc(f.get("detail",""))}')
            if f.get("rule"):
                a(f' <span style="font-family:{MONO};font-size:11.5px;color:{MUTED}">'
                  f'[{esc(f["rule"])}]</span>')
            if f.get("value"):
                a(f'<div style="font-family:{MONO};font-size:12px;color:{INK};'
                  f'background:{MIST};border-left:2px solid {LINE};padding:7px 10px;'
                  f'margin-top:7px;word-break:break-word">{esc(f["value"])}</div>')
            a('</td></tr></table></td></tr>')

    # --- footer ---
    a(f'<tr><td style="padding:28px 30px 26px 30px">'
      f'<div style="height:1px;background:{LINE};margin-bottom:14px"></div>'
      f'<div style="font-family:{BODY};font-size:11.5px;line-height:1.5;color:{MUTED}">'
      f'{esc(d.get("footer", ""))}</div></td></tr>')

    a('</table></td></tr></table></body>')
    return "\n".join(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    preview = "--preview" in sys.argv
    raw = open(args[0], encoding="utf-8").read() if args else sys.stdin.read()
    sys.stdout.write(render(json.loads(raw), preview=preview))


if __name__ == "__main__":
    main()
