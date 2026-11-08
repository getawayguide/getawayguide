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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
