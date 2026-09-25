#!/usr/bin/env python3
"""Keep the date a reader sees and the date Google reads from contradicting each other.

Every article carries its date twice: a visible byline under the title (`Mar 22, 2026`, in DM
Mono) and `dateModified` in the JSON-LD. Only the second one was ever maintained.
tools/seo_meta.py and .tmp/seo_content_dates.py derive dateModified from the last commit that
changed the page's prose, which is right; nothing has ever touched the byline. So it still
says whatever the template said the day the page was created.

Five published El Salvador pages tell a reader March and tell a search engine September.

The drafts are worse, because .tmp/seo_content_dates.py excludes Drafts/ by design (it reads
the main repo's history and drafts are not in it). Every Armenia article was built from
ruta-de-las-flores.html and inherited ITS byline: Yerevan says Mar 22 2026, which is the day
the Santa Ana page was written, about a place Kevin had not been to yet.

The byline is the one a reader believes, so it follows dateModified rather than the other way
round. For a draft, which has no dateModified worth trusting, the date comes from the last
commit in the Drafts repo that changed the page's prose, and from today if there is none.

    python tools/article_dates.py                 # report disagreements
    python tools/article_dates.py --fix           # make the byline match
    python tools/article_dates.py --drafts        # include Drafts/
    python tools/article_dates.py --drafts --fix
"""
import argparse
import datetime
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAFTS = ROOT / "Drafts"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
MON_N = {m: i + 1 for i, m in enumerate(MONTHS)}

# The byline: a short month, the day, the year, sitting alone inside its own element.
BYLINE = re.compile(r"(>)([A-Z][a-z]{2} \d{1,2}, 20\d{2})(<)")
JSONLD = re.compile(r'("dateModified"\s*:\s*")([0-9]{4}-[0-9]{2}-[0-9]{2})(")')
PUBLISHED = re.compile(r'("datePublished"\s*:\s*")([0-9]{4}-[0-9]{2}-[0-9]{2})(")')


def out(s=""):
    sys.stdout.buffer.write((s + "\n").encode("utf-8", "replace"))
    sys.stdout.buffer.flush()


def iso(byline):
    mo, day, yr = byline.replace(",", "").split()
    return "%s-%02d-%02d" % (yr, MON_N[mo], int(day))


def pretty(iso_date):
    y, m, d = (int(x) for x in iso_date.split("-"))
    return "%s %d, %d" % (MONTHS[m - 1], d, y)


def prose_date(path, repo):
    """The last commit in `repo` that changed this file, or None. Drafts only: the published
    pages already have a dateModified derived by the SEO tools, which is more careful than
    this because it diffs the visible prose rather than the file."""
    rel = os.path.relpath(path, repo).replace("\\", "/")
    r = subprocess.run(["git", "log", "-1", "--format=%ad", "--date=short", "--", rel],
                       cwd=repo, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    d = (r.stdout or "").strip()
    return d if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else None


def pages(include_drafts):
    for p in sorted(ROOT.glob("*.html")) + sorted(ROOT.glob("*/*.html")):
        r = p.relative_to(ROOT).as_posix()
        if r == "editor.html" or r.startswith(("archive/", ".tmp/", "tools/", "Drafts/")):
            continue
        yield p, r, False
    if include_drafts:
        for pat in ("Drafts/.Full Articles/*/*.html", "Drafts/*/field-notes.html"):
            for p in sorted(ROOT.glob(pat)):
                r = p.relative_to(ROOT).as_posix()
                if "/.Archive/" not in r:
                    yield p, r, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--drafts", action="store_true")
    ap.add_argument("--today", help="override 'today' (YYYY-MM-DD), for testing")
    ap.add_argument("--match", help="only pages whose path contains this (e.g. armenia)")
    a = ap.parse_args()
    today = a.today or datetime.date.today().isoformat()

    bad, fixed = [], 0
    for path, rel, is_draft in pages(a.drafts):
        if a.match and a.match.lower() not in rel.lower():
            continue
        s = path.read_text(encoding="utf-8", newline="")
        b = BYLINE.search(s)
        if not b:
            continue
        shown = iso(b.group(2))
        j = JSONLD.search(s)

        if is_draft:
            # No trustworthy dateModified: drafts are excluded from the SEO date pass, and
            # what is in them came from the template they were copied from.
            want = prose_date(path, DRAFTS) or today
        elif j:
            want = j.group(2)
        else:
            continue

        if shown == want and (not is_draft or not j or j.group(2) == want):
            continue
        bad.append((rel, shown, want, is_draft))
        if not a.fix:
            continue
        s2 = BYLINE.sub(lambda m: m.group(1) + pretty(want) + m.group(3), s, count=1)
        if is_draft and j:
            s2 = JSONLD.sub(lambda m: m.group(1) + want + m.group(3), s2, count=1)
            # A draft's datePublished should never be later than the day it is modified.
            pm = PUBLISHED.search(s2)
            if pm and pm.group(2) > want:
                s2 = PUBLISHED.sub(lambda m: m.group(1) + want + m.group(3), s2, count=1)
        if s2 != s:
            path.write_text(s2, encoding="utf-8", newline="")
            fixed += 1

    out("%d page(s) where the byline a reader sees is not the date the page claims\n" % len(bad))
    for rel, shown, want, is_draft in bad:
        out("  %-46s shown %s -> %s%s" % (rel, shown, want, "   [draft]" if is_draft else ""))
    if a.fix:
        out("\nrewrote %d page(s)" % fixed)
    elif bad:
        out("\nrun again with --fix to make the byline match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
