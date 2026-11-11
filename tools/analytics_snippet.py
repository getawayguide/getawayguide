"""Add (or remove) the Cloudflare Web Analytics beacon in the <head> of every live page.

The token lives in .env as CF_ANALYTICS_TOKEN (the 32-character hex "token" Cloudflare shows
on the Web Analytics > Manage site > JS snippet screen). With no token set, --apply does
nothing, so the tool is safe to call from the publish tools before Kevin has an account.

    python tools/analytics_snippet.py               # dry run (default): what --apply would change
    python tools/analytics_snippet.py --apply       # insert or update the snippet on every live page
    python tools/analytics_snippet.py --remove      # take it out of every live page (no token needed)
    python tools/analytics_snippet.py --check       # exit 1 if a token is set and a live page lacks it

The snippet sits on ONE line between two markers, last thing before </head>:

    <!-- Cloudflare Web Analytics --><script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{"token": "..."}'></script><!-- End Cloudflare Web Analytics -->

That is Cloudflare's own snippet as Kevin pasted it. A module script is deferred by default,
so it never blocks rendering and the head is as good a spot as "before </body>". Cloudflare Web Analytics sets no cookies and stores no IP
addresses, which is why the privacy page only needs a wording change (see
.tmp/affiliate-analytics/privacy-diff.md) and no consent banner.

Touches only the snippet line: page bodies, encoding, BOM and line endings are left as found.
Live pages = the repo root and the country folders; never Drafts/, archive/, .tmp/, tools/,
editor.html (private) or a redirect stub.
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE = {"editor.html"}
START, END = "<!-- Cloudflare Web Analytics -->", "<!-- End Cloudflare Web Analytics -->"
BLOCK_RE = re.compile(r"[ \t]*" + re.escape(START) + r".*?" + re.escape(END) + r"[ \t]*(\r?\n)?", re.S)
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def token():
    """CF_ANALYTICS_TOKEN from the environment, else from .env. Empty string when unset."""
    v = os.environ.get("CF_ANALYTICS_TOKEN", "")
    env = ROOT / ".env"
    if not v and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            if k.strip() == "CF_ANALYTICS_TOKEN":
                v = val.strip().strip('"').strip("'")
    return v.strip()


def snippet(tok):
    # Cloudflare's own snippet, character for character (the form Kevin pasted, 2026-11-07)
    return (START + "<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' "
            "data-cf-beacon='{\"token\": \"%s\"}'></script>" % tok + END)


def live_pages():
    out = []
    for p in sorted(ROOT.glob("*.html")) + sorted(ROOT.glob("*/*.html")):
        r = p.relative_to(ROOT).as_posix()
        if r in EXCLUDE or r.startswith(("Drafts/", "archive/", ".tmp/", "tools/", "_content/", "workflows/")):
            continue
        out.append((p, r))
    return out


def read(p):
    # newline="" keeps CRLF/LF exactly; a leading BOM survives as ﻿ and is written back
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


def strip_block(html):
    return BLOCK_RE.sub("", html)


def with_block(html, tok):
    """The page with exactly one snippet, placed on its own line just before </head>."""
    base = strip_block(html)
    i = base.lower().find("</head>")
    if i < 0:
        return None
    nl = "\r\n" if "\r\n" in base else "\n"
    line_start = base.rfind("\n", 0, i) + 1
    if base[line_start:i].strip():          # </head> shares its line with other markup
        return base[:i] + snippet(tok) + nl + base[i:]
    return base[:line_start] + snippet(tok) + nl + base[line_start:]


def main(argv):
    apply_, remove, check = "--apply" in argv, "--remove" in argv, "--check" in argv
    if apply_ and remove:
        sys.exit("Pick one of --apply / --remove.")
    tok = token()
    if tok and not TOKEN_RE.match(tok):
        sys.exit("CF_ANALYTICS_TOKEN in .env does not look like a Cloudflare beacon token "
                 "(32 lowercase hex characters). Nothing changed.")

    pages = live_pages()
    changed, missing, skipped = [], [], []
    for p, r in pages:
        html = read(p)
        if 'http-equiv="refresh"' in html:           # redirect stub: no visitor ever sees it
            continue
        has = START in html
        if remove:
            if has:
                changed.append(r)
                write(p, strip_block(html))
            continue
        if not tok:
            if has:
                skipped.append(r)
            continue
        new = with_block(html, tok)
        if new is None:
            skipped.append(r + " (no </head>)")
            continue
        if new != html:
            (changed if has else missing).append(r)
            if apply_:
                # another agent may be editing page bodies: re-read right before writing and
                # rebuild from that copy, so only the head line ever differs from what is on disk
                new = with_block(read(p), tok)
                write(p, new)

    out = []
    if remove:
        out.append("Removed the analytics snippet from %d page(s)." % len(changed) if changed
                   else "No live page carries the analytics snippet. Nothing to remove.")
        out += ["  " + r for r in changed]
    elif not tok:
        out.append("CF_ANALYTICS_TOKEN is not set in .env, so analytics stays OFF. Nothing to do.")
        if skipped:
            out.append("Note: %d page(s) still carry an old snippet; run --remove to take it out." % len(skipped))
    else:
        verb = "Added" if apply_ else "Would add"
        out.append("%s the snippet to %d page(s), updated it on %d, %d already current (of %d live pages)."
                   % (verb, len(missing), len(changed), len(pages) - len(missing) - len(changed) - len(skipped), len(pages)))
        out += ["  + " + r for r in missing] + ["  ~ " + r for r in changed] + ["  ! " + r for r in skipped]
        if not apply_ and not check and (missing or changed):
            out.append("Dry run: nothing written. Re-run with --apply.")
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))

    if check and tok and (missing or changed):
        return 1
    return ga_main(argv)


# ------------------------------------------------------------------ Google Analytics 4 (Kevin, 2026-11-11)
# The queue item: "the audience number partners recognize, with a consent banner for European visitors". The ID is
# GA_MEASUREMENT_ID in .env. One block per page, between its own markers, right before </head> (after the Cloudflare one):
#   1. Consent Mode v2 defaults, set BEFORE gtag.js loads: everything denied for visitors Google places in the EEA, the UK
#      and Switzerland; analytics allowed elsewhere; ads storage denied everywhere (the site runs no ads). A choice the
#      visitor saved on this browser is applied straight after.
#   2. gtag.js (async) and the config line.
#   3. consent.js (defer), at the site root: the small banner. It shows only to visitors whose clock is set to a European
#      time zone and who haven't chosen yet; Accept/Decline update the consent and are remembered. The privacy page's
#      "Cookie settings" link opens it again (window.ggConsent.open()).
# consent.js is linked relative to the page (".." for country pages), like the style sheets, so the local preview at
# /site/<path> works the same as the live site.
GA_START, GA_END = "<!-- Google Analytics -->", "<!-- End Google Analytics -->"
GA_RE = re.compile(r"[ \t]*" + re.escape(GA_START) + r".*?" + re.escape(GA_END) + r"[ \t]*(\r?\n)?", re.S)
GA_ID_RE = re.compile(r"^G-[A-Z0-9]{6,12}$")
CONSENT_REGIONS = ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
                   "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "GB", "CH"]


def ga_id():
    v = os.environ.get("GA_MEASUREMENT_ID", "")
    env = ROOT / ".env"
    if not v and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            if k.strip() == "GA_MEASUREMENT_ID":
                v = val.strip().strip('"').strip("'")
    return v.strip()


def ga_snippet(mid, rel):
    up = "../" * rel.count("/")
    deny = "ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'"
    regions = ",".join("'%s'" % c for c in CONSENT_REGIONS)
    return (GA_START + "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
            "gtag('consent','default',{%s,analytics_storage:'denied',region:[%s]});"
            "gtag('consent','default',{%s,analytics_storage:'granted'});"
            "try{var c=localStorage.getItem('gg-consent');if(c)gtag('consent','update',{analytics_storage:c==='granted'?'granted':'denied'});}catch(e){}"
            "gtag('js',new Date());gtag('config','%s');</script>"
            "<script async src=\"https://www.googletagmanager.com/gtag/js?id=%s\"></script>"
            "<script src=\"%sconsent.js\" defer></script>" % (deny, regions, deny, mid, mid, up) + GA_END)


def with_ga(html, mid, rel):
    base = GA_RE.sub("", html)
    i = base.lower().find("</head>")
    if i < 0:
        return None
    nl = "\r\n" if "\r\n" in base else "\n"
    line_start = base.rfind("\n", 0, i) + 1
    if base[line_start:i].strip():
        return base[:i] + ga_snippet(mid, rel) + nl + base[i:]
    return base[:line_start] + ga_snippet(mid, rel) + nl + base[line_start:]


def ga_main(argv):
    """the same --apply / --check / dry run for the Google Analytics block; --remove-ga takes it out"""
    apply_, check, remove = "--apply" in argv, "--check" in argv, "--remove-ga" in argv
    mid = ga_id()
    if mid and not GA_ID_RE.match(mid):
        sys.exit("GA_MEASUREMENT_ID in .env does not look like a GA4 measurement ID (G-XXXXXXXXXX). Nothing changed.")
    changed = []
    for p, r in live_pages():
        html = read(p)
        if 'http-equiv="refresh"' in html:
            continue
        if remove:
            if GA_START in html:
                changed.append(r)
                write(p, GA_RE.sub("", html))
            continue
        if not mid:
            continue
        new = with_ga(html, mid, r)
        if new is not None and new != html:
            changed.append(r)
            if apply_:
                write(p, with_ga(read(p), mid, r))
    if remove:
        msg = "Removed Google Analytics from %d page(s)." % len(changed)
    elif not mid:
        msg = "GA_MEASUREMENT_ID is not set in .env, so Google Analytics stays off."
    else:
        msg = "%s Google Analytics on %d page(s)%s." % ("Added or updated" if apply_ else "Would add or update", len(changed),
                                                       "" if apply_ or check or not changed else " (dry run: re-run with --apply)")
    sys.stdout.buffer.write((msg + "\n").encode("utf-8"))
    if not (ROOT / "consent.js").is_file() and mid and not remove:
        sys.stdout.buffer.write(b"WARNING: consent.js is missing at the site root; the banner won't show.\n")
    return 1 if check and mid and changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
