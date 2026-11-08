#!/usr/bin/env python3
"""Keep each live article's two date lines in step with its JSON-LD dateModified.

1. The footnote line, which is also the site's affiliate disclosure (Kevin, 2026-11-08):

       Last updated: <Month YYYY> · Based on firsthand experience · Contains affiliate links

   as <div class="measure gg-updated">, at the bottom of the article: the last line of the article body,
   right before the Explore More section (#explore-more), exactly where armenia/armenia-itinerary.html has
   always carried it. A page with no Explore More gets it as the body's last line; resources.html (not an
   article) gets it right before its footer. Styled by the existing .gg-updated rules in artifact.css.
   Kevin chose the bottom over the top ("add it to the bottom area where it is on the armenia page").
   Because the line says "affiliate links", tools/affiliate_links.py no longer adds its tip box to these
   pages and removes an old one.

2. The date under the hero description, "Mon D, YYYY" (Kevin, 2026-11-08: "check all pages to make sure
   that is there"). The city guides / top 10s always had it (an inline DM Mono div after .article-lead);
   the itineraries' banner hero did not, so it gets one in its own look (class "hero-date"). The guides show
   the page's dateModified (El Salvador: published 2026-04-17, modified 2026-09-01, shown "Sep 1, 2026"), so
   the itineraries do too. FIELD NOTES CARRY NO HERO DATE (Kevin, 2026-11-08, after seeing them): one on a
   field-notes page is removed; they keep only the footnote line.

The date: the page's JSON-LD dateModified; else datePublished; else (no JSON-LD, i.e. resources.html) the month its
line already shows; else the file's last git commit; else today.
dateModified itself is never changed here (tools/seo_apply.py owns it).

    python tools/updated_line.py                 # dry run: list what would change
    python tools/updated_line.py --apply         # write it
    python tools/updated_line.py --check         # exit 1 if any live article is missing/stale (the lint)
    python tools/updated_line.py --pages greece/meteora.html --apply

Run by tools/publish_country.py and tools/retire_field_notes.py after the SEO dates are set, so new pages
get both lines. tools/lint_site.py flags a live article without them (problems()).
"""
import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_PREFIX = ("Drafts/", "archive/", ".tmp/", "tools/", "_content/", "workflows/", ".git/", "preview/")
EXTRA = ("resources.html",)          # not an article, but it has booking links: same footnote, no hero date
ARTICLE_RE = re.compile(r'<div class="(?:article-body|artbody)[" ]')

TEXT = "Last updated: %s · Based on firsthand experience · Contains affiliate links"
LINE = '<div class="measure gg-updated">%s</div>'
ANY_LINE_RE = re.compile(r'<div class="[^"]*\bgg-updated\b[^"]*"[^>]*>[^<]*</div>')
XM_RE = re.compile(r'<section class="xm[" ]')

# hero dates
GUIDE_DATE_STYLE = ("position:relative;z-index:1;margin-top:.6rem;font-family:'DM Mono',monospace;font-size:.58rem;"
                    "letter-spacing:.15em;text-transform:uppercase;color:var(--cream)")
# the itinerary banner: the kicker's size and spacing, the kicker's color via .on-ph (which also follows the
# "under" scrim, where the copy sits on white)
ITIN_DATE = ('<div class="hero-date on-ph" style="margin-top:14px;font-family:\'DM Mono\',monospace;font-size:11px;'
             'letter-spacing:.16em;text-transform:uppercase">%s</div>')
DATE_TXT = r"[A-Z][a-z]{2} \d{1,2}, \d{4}"
GUIDE_HERO_RE = re.compile(r'(<p class="article-lead"[^>]*>.*?</p>)(\s*<div style="[^"]*DM Mono[^"]*">)(%s)(</div>)' % DATE_TXT, re.S)
GUIDE_LEAD_RE = re.compile(r'<p class="article-lead"[^>]*>.*?</p>', re.S)
CLASSED_RE = re.compile(r'(<div class="hero-date[^"]*"[^>]*>)([^<]*)(</div>)')
BANNER_SUB_RE = re.compile(r'<div class="ah ah-banner">.*?(<div class="artsub">.*?</div>)', re.S)
FN_HERO_RE = re.compile(r'<h1 class="fn-hero-title"')     # a field-notes page: no hero date
CLASSED_LINE_RE = re.compile(r'[ \t]*<div class="hero-date[^"]*"[^>]*>[^<]*</div>[ \t]*\r?\n?')


def no_hero_date(rel, text):
    """resources.html and the field notes carry no hero date (Kevin, 2026-11-08)"""
    return rel in EXTRA or bool(FN_HERO_RE.search(text) and not GUIDE_LEAD_RE.search(text)
                                and not BANNER_SUB_RE.search(text))


# ---- pages ---------------------------------------------------------------------------------------
def live_pages():
    out = []
    for p in sorted(ROOT.glob("*.html")) + sorted(ROOT.glob("*/*.html")):
        r = p.relative_to(ROOT).as_posix()
        if r == "editor.html" or r.startswith(SKIP_PREFIX):
            continue
        out.append(r)
    return out


def is_article(text):
    return bool(ARTICLE_RE.search(text)) and 'http-equiv="refresh"' not in text[:3000].lower()


def wants(rel, text):
    return rel in EXTRA or is_article(text)


def read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


# ---- the date ------------------------------------------------------------------------------------
def git_date(rel):
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cs", "--", rel], cwd=ROOT, capture_output=True,
                             text=True, timeout=30).stdout.strip()
        return date.fromisoformat(out) if out else None
    except Exception:
        return None


def page_date(rel, text):
    """(date, source)"""
    for key in ("dateModified", "datePublished"):
        m = re.search(r'"%s"\s*:\s*"(\d{4}-\d{2}-\d{2})' % key, text)
        if m:
            return date.fromisoformat(m.group(1)), key
    # no JSON-LD (resources.html): a line already on the page keeps its month, so that a later commit of any kind
    # (a stylesheet stamp, a nav refresh) does not move "Last updated"; git dates only a page that has none yet
    m = re.search(r'gg-updated\b[^>]*>Last updated: ([A-Z][a-z]+) (\d{4})', text)
    if m:
        try:
            from datetime import datetime
            return datetime.strptime("%s 1 %s" % (m.group(1), m.group(2)), "%B %d %Y").date(), "kept"
        except ValueError:
            pass
    d = git_date(rel)
    if d:
        return d, "git"
    return date.today(), "today"


def month_year(d):
    return "%s %d" % (d.strftime("%B"), d.year)


def short(d):
    return "%s %d, %d" % (d.strftime("%b"), d.day, d.year)


# ---- the footnote line ---------------------------------------------------------------------------
def _close_of(text, start):
    """offset of the </div> that closes the div opening at start"""
    depth = 0
    for t in re.finditer(r"<(/?)div\b[^>]*>", text[start:]):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return start + t.start()
    return None


def line_spot(rel, text):
    """where the footnote goes: before Explore More; else the article body's last line; else before the footer"""
    m = XM_RE.search(text)
    if m:
        return m.start()
    m = ARTICLE_RE.search(text)
    if m:
        c = _close_of(text, m.start())
        if c is not None:
            return c
    i = text.find("<footer")
    return i if i >= 0 else None


def set_line(rel, text, d):
    nl = "\r\n" if "\r\n" in text else "\n"
    want = LINE % (TEXT % month_year(d))
    # take every copy out (with its own line), so no page ever shows it twice, then put one back
    s = re.sub(r"[ \t]*" + ANY_LINE_RE.pattern + r"[ \t]*\r?\n?", "", text)
    spot = line_spot(rel, s)
    if spot is None:
        return text, "no place for the line"
    ls = s.rfind("\n", 0, spot) + 1
    if s[ls:spot].strip():                     # mid-line (e.g. "...</p></div>"): break onto a line of its own
        ind = re.match(r"[ \t]*", s[ls:]).group(0) or "  "
        ins = nl + ind + want + nl
    else:                                      # its own line, indented like the line above it
        prev = s.rfind("\n", 0, max(ls - 1, 0)) + 1
        ind = re.match(r"[ \t]*", s[prev:ls]).group(0) if ls else ""
        ins = (ind or "  ") + want + nl
        spot = ls
    return s[:spot] + ins + s[spot:], None


# ---- the hero date -------------------------------------------------------------------------------
def set_hero(rel, text, d):
    """(new_text, None) or (text, reason it could not be placed)"""
    if no_hero_date(rel, text):                 # field notes / resources: take off one an earlier run added
        return CLASSED_LINE_RE.sub("", text), None
    want = short(d)
    nl = "\r\n" if "\r\n" in text else "\n"
    m = GUIDE_HERO_RE.search(text)
    if m:                                       # city guide / top 10: refresh the inline date
        return text[:m.start(3)] + want + text[m.end(3):], None
    m = CLASSED_RE.search(text)
    if m:                                       # one this tool wrote: refresh
        return text[:m.start(2)] + want + text[m.end(2):], None
    m = GUIDE_LEAD_RE.search(text)
    if m:
        ls = text.rfind("\n", 0, m.start()) + 1
        ind = re.match(r"[ \t]*", text[ls:]).group(0)
        div = '<div style="%s">%s</div>' % (GUIDE_DATE_STYLE, want)
        return text[:m.end()] + nl + ind + div + text[m.end():], None
    m = BANNER_SUB_RE.search(text)
    if m:
        ls = text.rfind("\n", 0, m.start(1)) + 1
        ind = re.match(r"[ \t]*", text[ls:]).group(0)
        return text[:m.end(1)] + nl + ind + ITIN_DATE % want + text[m.end(1):], None
    return text, "no hero description found"


def process(rel, text):
    """(new_text, date, source, notes)"""
    d, src = page_date(rel, text)
    notes = []
    new, why = set_line(rel, text, d)
    if why:
        notes.append(why)
    new, why = set_hero(rel, new, d)
    if why:
        notes.append(why)
    return new, d, src, notes


def problems(rel, text):
    """what the lint reports for one live page: [] when both lines are present and current"""
    if not wants(rel, text):
        return []
    new, d, src, notes = process(rel, text)
    out = list(notes)
    if new != text:
        lines = ANY_LINE_RE.findall(text)
        if not lines:
            out.append("no 'Last updated' line")
        elif len(lines) > 1:
            out.append("'Last updated' line appears %d times" % len(lines))
        elif (LINE % (TEXT % month_year(d))) not in text:
            out.append("'Last updated' line is stale or worded differently (want %s)" % month_year(d))
        if no_hero_date(rel, text):
            if CLASSED_RE.search(text):
                out.append("a field-notes page carries no hero date")
        elif not (GUIDE_HERO_RE.search(text) or CLASSED_RE.search(text)):
            out.append("no date under the hero description")
        elif short(d) not in (GUIDE_HERO_RE.search(text) or CLASSED_RE.search(text)).group(0):
            out.append("hero date is not %s" % short(d))
        if not out:
            out.append("'Last updated' line is not right before Explore More")
    return out


# ---- CLI -----------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="list what would change (the default)")
    g.add_argument("--apply", action="store_true", help="write the changes")
    g.add_argument("--check", action="store_true", help="exit 1 if any page would change")
    ap.add_argument("--pages", nargs="+", help="only these pages (paths relative to the repo)")
    ap.add_argument("--root", help="treat this folder as the site root (tests)")
    a = ap.parse_args(argv)
    global ROOT
    if a.root:
        ROOT = Path(a.root).resolve()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    pages = [p.replace("\\", "/") for p in (a.pages or live_pages())]
    changed, seen, issues = [], 0, []
    for r in pages:
        p = ROOT / r
        if not p.is_file():
            print("skip (missing): %s" % r)
            continue
        text = read(p)
        if not wants(r, text):
            continue
        seen += 1
        new, d, src, notes = process(r, text)
        for n in notes:
            issues.append("%s: %s" % (r, n))
        if new != text:
            changed.append((r, d, src))
            if a.apply:
                write(p, new)
    mode = "apply" if a.apply else ("check" if a.check else "dry run")
    print("updated_line.py (%s): %d page(s) checked, %d %s" % (mode, seen, len(changed),
          "changed" if a.apply else "would change"))
    for r, d, src in changed:
        print("  %-42s %-15s %-13s (%s)" % (r, month_year(d), short(d), src))
    for i in issues:
        print("  ! " + i)
    if a.check and (changed or issues):
        sys.exit(1)
    return changed


if __name__ == "__main__":
    main()
