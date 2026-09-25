#!/usr/bin/env python3
"""The repeatable quality checks the weekly cloud routines email Kevin about.

Every check here is deterministic and runs offline, so a routine's prompt can say "run this"
instead of describing a scan in English and hoping the agent reproduces it. Each one exists
because something real got through:

  prose      typos, spacing, British spellings, em dashes in body prose, leftover [ ] notes
             (tools/prose_check.py, which imports the rules from lint_prose / americanize /
             strip_paste_artifacts so there is one definition of each)
  tiers      a <picture> whose 1x/2x/3x JPEG+WebP tiers are not all published. Kosovo shipped
             with ONE un-tiered JPEG and no WebP for months; lint_site checks that a
             referenced file exists, not that the tier set is complete.
  heroes     a hero whose fallback is a full-resolution original (one was 13MB) or whose
             tiers are missing. The hero is the largest thing a visitor downloads.
  maps       a Google-Maps link still pointing at a SEARCH instead of a place, which drops
             the reader on a results page. Every draft round leaves these behind.
  drafts     what stands between each draft and publishing: leftover [ ] notes, unresolved
             map links, photos still at full resolution.
  selfcheck  the tools a routine depends on are in the git index and run from a fresh clone.
             review.js and tools/redline.py were untracked until 2026-09-23, so a cloud
             clone had an editor with no review pane and nobody found out.

    python tools/audit.py                          # every check, as a table
    python tools/audit.py --checks tiers,heroes    # some of them
    python tools/audit.py --json findings.json     # the schema tools/report_email.py reads
    python tools/audit.py --drafts-only            # just the draft readiness board
    python tools/audit.py --new-only --state .tmp/audit_state.json    # only what changed

--new-only is what makes a DAILY routine worth reading. Without it a finding nobody has
fixed yet arrives again every morning, identical, and the mail becomes something to archive
unread; the whole reason the routines are silent when clean is to avoid exactly that. With
it, each finding is fingerprinted (check + page + rule + value), the run compares against
the last one, and the mail carries only what is new, saying how many known ones it withheld.
The state file is written only on a real run, so a dry look never moves the baseline.

A DAILY CLOUD ROUTINE needs its baseline committed, because it starts from a fresh clone
and .tmp/ is gitignored, so it would otherwise call every finding new every morning. Give
it its own tracked path:

    python tools/audit.py --new-only --state audit-state.json     # the routine
    python tools/audit.py --new-only                              # you, locally (.tmp/)

Keep those two separate. One shared baseline conflates "a machine has seen this" with
"Kevin has seen this": run the audit here at 10am and tomorrow's mail goes quiet about
findings that never reached an inbox.

Exit code is 1 when anything HIGH was found, so a routine can gate on it.
"""
import argparse
import hashlib
import html as htmlmod
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

EXCLUDE_PAGES = {"editor.html"}
SEARCH_LINK = re.compile(r'https://www\.google\.com/maps/search/\?api=1&(?:amp;)?query=([^"\']+)')
PICTURE = re.compile(r"<picture\b.*?</picture>", re.S | re.I)
SOURCE = re.compile(r"<source\b[^>]*>", re.I)
IMG = re.compile(r"<img\b[^>]*>", re.I)
ATTR = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')
BRACKET = re.compile(r"\[(?!\d+\])[^\[\]\n]{0,120}\]")


def nfc(s):
    return unicodedata.normalize("NFC", s)


def committed_files():
    """Every path the deployed site will have: the git index plus HEAD, never the filesystem.

    Same rule and the same reasoning as lint_site.committed_files (drafts and draft-only
    assets exist on disk and 404 in production). It is repeated rather than imported because
    importing lint_site runs its entire lint as a side effect, which takes a minute and would
    make every one of these checks pay for it."""
    out = set()
    for args in (["ls-files", "-z"], ["ls-tree", "-r", "-z", "--name-only", "HEAD"]):
        r = subprocess.run(["git"] + args, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            out |= {nfc(p) for p in r.stdout.split("\0") if p}
    return out


COMMITTED = committed_files()


def live_pages():
    """Published pages only: the repo root and the country folders, never Drafts/ or archive/."""
    out = []
    for p in sorted(ROOT.glob("*.html")) + sorted(ROOT.glob("*/*.html")):
        r = p.relative_to(ROOT).as_posix()
        if r in EXCLUDE_PAGES or r.startswith(("Drafts/", "archive/", ".tmp/", "tools/")):
            continue
        out.append((p, r))
    return out


def draft_pages():
    pats = ["Drafts/.Full Articles/*/*.html", "Drafts/*/field-notes.html"]
    out = []
    for pat in pats:
        for p in sorted(ROOT.glob(pat)):
            r = p.relative_to(ROOT).as_posix()
            if "/.Archive/" in r:
                continue
            out.append((p, r))
    return out


def resolve(ref, page_rel):
    """A page-relative reference to a repo-relative path, or None if it is not a local file."""
    ref = ref.split("#")[0].split("?")[0].strip()
    if not ref or ref.startswith(("http://", "https://", "//", "data:", "mailto:", "tel:")):
        return None
    from urllib.parse import unquote
    ref = unquote(ref)
    base = os.path.dirname(page_rel)
    return nfc(os.path.normpath(os.path.join("/" if ref.startswith("/") else base, ref))
               .replace("\\", "/").lstrip("/"))


def srcset_files(value, page_rel):
    for cand in value.split(","):
        cand = cand.strip()
        if cand:
            r = resolve(cand.split()[0], page_rel)
            if r:
                yield r


def attrs(tag):
    return {k.lower(): v for k, v in ATTR.findall(tag)}


def finding(sev, rule, detail, value=None):
    f = {"severity": sev, "rule": rule, "detail": detail}
    if value:
        f["value"] = value
    return f


# ---------------------------------------------------------------- 1. prose
def check_prose(limit=None):
    import prose_check
    groups = []
    for p, r in live_pages():
        fs = prose_check.check(p.read_text(encoding="utf-8"))
        fs = [f for f in fs if f["kind"] != "maps"]          # the maps check has its own section
        if not fs:
            continue
        out = []
        for f in fs[:limit or 12]:
            a = f.get("anchor") or {}
            sev = "medium" if f.get("severity") == "error" else "low"
            quote = (a.get("before", "")[-34:] + a.get("quote", "") + a.get("after", "")[:26]).strip()
            out.append(finding(sev, f["kind"], f["message"], quote or None))
        if len(fs) > len(out):
            out.append(finding("low", "more", "%d more on this page." % (len(fs) - len(out))))
        groups.append({"name": r, "findings": out})
    return groups


# ---------------------------------------------------------------- 2. tiers
def check_tiers():
    """Every <picture> must serve the documented set: desktop WebP + JPEG at 1x/2x/3x, the
    same again for mobile, and a -mob-2x.jpg fallback. Missing FILES are what this catches;
    lint_site catches a reference to a file that is not there."""
    groups = []
    for p, r in live_pages():
        html = p.read_text(encoding="utf-8")
        probs = []
        for blk in PICTURE.findall(html):
            srcs = [attrs(s) for s in SOURCE.findall(blk)]
            img = attrs(IMG.search(blk).group(0)) if IMG.search(blk) else {}
            fallback = img.get("src", "")
            if not fallback:
                continue
            name = fallback.rsplit("/", 1)[-1]
            desk_webp = [s for s in srcs if s.get("type") == "image/webp" and "min-width" in s.get("media", "")]
            desk_jpg = [s for s in srcs if not s.get("type") and "min-width" in s.get("media", "")]
            mob_webp = [s for s in srcs if s.get("type") == "image/webp" and "min-width" not in s.get("media", "")]
            if not desk_webp:
                probs.append(finding("medium", "webp-missing", "No desktop WebP source, so every visitor downloads the JPEG.", name))
            if not mob_webp:
                probs.append(finding("medium", "webp-missing", "No mobile WebP source.", name))
            for label, group in (("desktop", desk_webp + desk_jpg), ("mobile", mob_webp)):
                for s in group:
                    cands = list(srcset_files(s.get("srcset", ""), r))
                    if len(cands) < 2:
                        probs.append(finding("low", "one-tier",
                                             "The %s source offers a single size, so a phone and a 3x screen get the same file." % label,
                                             (s.get("srcset") or "")[:90]))
                    for f in cands:
                        if f not in COMMITTED:
                            probs.append(finding("high", "tier-missing", "A %s tier is not published, so it 404s." % label, f))
            if "/Images/" in fallback and "/Images/web/" not in fallback:
                probs.append(finding("high", "original-as-fallback",
                                     "The fallback is the full-resolution original, not a -mob-2x.jpg.", fallback))
        if probs:
            groups.append({"name": r, "findings": probs[:10]})
    return groups


# ---------------------------------------------------------------- 3. heroes
def check_heroes():
    """The hero is the biggest thing a visitor downloads, and it is the first. Its tiers must
    all be published and its fallback must never be an archival original."""
    groups = []
    for p, r in live_pages():
        html = p.read_text(encoding="utf-8")
        m = re.search(r'<section[^>]*class="[^"]*\b(?:article|country)-hero\b[^"]*".*?</section>', html, re.S | re.I)
        region = m.group(0) if m else ""
        probs = []
        refs = set()
        for blk in PICTURE.findall(region):
            for s in SOURCE.findall(blk):
                refs |= set(srcset_files(attrs(s).get("srcset", ""), r))
            im = IMG.search(blk)
            if im:
                src = attrs(im.group(0)).get("src", "")
                f = resolve(src, r)
                if f:
                    refs.add(f)
                if "/Images/" in src and "/Images/web/" not in src:
                    probs.append(finding("high", "hero-original", "The hero fallback is an archival original.", src))
        # the other shape: a background image-set in the page's own <style>
        for mm in re.finditer(r"url\(['\"]?([^)'\"]+)['\"]?\)", region + (re.search(r'<style id="hero-tiers">.*?</style>', html, re.S).group(0) if re.search(r'<style id="hero-tiers">', html) else "")):
            f = resolve(mm.group(1), r)
            if f and "/Images/" in mm.group(1):
                refs.add(f)
        for f in sorted(refs):
            if f not in COMMITTED:
                probs.append(finding("high", "hero-tier-missing", "A hero tier is not published, so it 404s.", f))
        if region and not refs:
            probs.append(finding("low", "hero-unchecked", "Could not find the hero's image references to check."))
        if probs:
            groups.append({"name": r, "findings": probs[:8]})
    return groups


# ---------------------------------------------------------------- 4. maps
def check_maps():
    groups = []
    for p, r in live_pages():
        qs = SEARCH_LINK.findall(p.read_text(encoding="utf-8"))
        if not qs:
            continue
        from urllib.parse import unquote_plus
        pretty = [unquote_plus(htmlmod.unescape(q)).strip() for q in qs]
        groups.append({"name": r,
                       "note": "These drop the reader on a Google results page instead of the pin.",
                       "findings": [finding("medium", "maps-search", "Still a search link.", t) for t in pretty[:8]]
                       + ([finding("low", "more", "%d more on this page." % (len(pretty) - 8))] if len(pretty) > 8 else [])})
    return groups


# ---------------------------------------------------------------- 5. drafts
def check_drafts():
    """What stands between each draft and publishing. Not errors: a readiness board."""
    import prose_check
    groups = []
    note = ("Nothing here is live, so nothing here is urgent. This is what stands between "
            "each draft and publishing.")
    for p, r in draft_pages():
        html = p.read_text(encoding="utf-8")
        body = html[html.find('class="article-body"'):] if 'class="article-body"' in html else html
        # the bracket scan comes from prose_check, which masks scripts and styles and only
        # reads prose. Scanning the raw HTML here reported a page's own JavaScript, so a
        # draft's readiness line said "[p] ['class']" was a leftover note.
        found = prose_check.check(html)
        notes = [(f.get("anchor") or {}).get("quote", "") for f in found if f["kind"] == "placeholder"]
        maps = SEARCH_LINK.findall(html)
        photos = [m for m in IMG.finditer(body)
                  if "/Images/" in attrs(m.group(0)).get("src", "")
                  and "/Images/web/" not in attrs(m.group(0)).get("src", "")]
        prose = [f for f in found if f["kind"] not in ("maps", "placeholder")]
        if not (notes or maps or photos or prose):
            continue
        out = []
        if notes:
            out.append(finding("medium", "placeholder", "%d bracketed note(s) still in the text." % len(notes),
                               "  ".join(n[:40] for n in notes[:6])))
        if maps:
            out.append(finding("medium", "maps-search", "%d map link(s) still point at a search." % len(maps),
                               "Resolve maps in the editor, or tools/resolve_draft_maps.py"))
        if photos:
            out.append(finding("medium", "uncompressed", "%d photo(s) still at full resolution." % len(photos),
                               "python tools/draft_images.py \"%s\"" % r))
        if prose:
            out.append(finding("low", "prose", "%d prose finding(s): %s." % (
                len(prose), ", ".join(sorted({f["kind"] for f in prose}))), None))
        groups.append({"name": r, "note": note, "findings": out})
        note = None
    return groups


# ---------------------------------------------------------------- 7. seo
def check_seo():
    """CLAUDE.md, "Publishing a Country". Belgium and Germany both went live without this,
    which is why it is a step of the publish and not a follow-up."""
    groups = []
    for p, r in live_pages():
        html = p.read_text(encoding="utf-8")
        if 'name="robots"' in html and "noindex" in html:
            continue                                     # 404.html and friends
        probs = []

        def meta(prop, attr="property"):
            # unescaped, because a length is what a reader and a search engine SEE. Estonia's
            # description measured 161 only because an apostrophe is written &#x27;, five
            # characters for one, and the cap is about the sentence, not the markup.
            m = re.search(r'<meta[^>]*%s="%s"[^>]*content="([^"]*)"' % (attr, re.escape(prop)), html, re.I)
            if not m:
                m = re.search(r'<meta[^>]*content="([^"]*)"[^>]*%s="%s"' % (attr, re.escape(prop)), html, re.I)
            return htmlmod.unescape(m.group(1)) if m else None

        t = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
        title = htmlmod.unescape(t.group(1).strip()) if t else None
        if not title:
            probs.append(finding("high", "no-title", "No <title>."))
        elif len(title) > 70:
            probs.append(finding("medium", "title-long", "Title is %d characters; Google truncates around 70." % len(title), title))
        og_t = meta("og:title")
        if not og_t:
            probs.append(finding("medium", "no-og-title", "No og:title, so a share shows whatever the crawler guesses."))
        desc = meta("description", "name")
        if not desc:
            probs.append(finding("high", "no-description", "No meta description."))
        elif len(desc) >= 160:
            probs.append(finding("medium", "description-long", "Description is %d characters; the cap is 160." % len(desc), desc))
        od = meta("og:description")
        if od and len(od) >= 160:
            probs.append(finding("low", "og-description-long", "og:description is %d characters." % len(od), od))
        if 'rel="canonical"' not in html:
            probs.append(finding("high", "no-canonical", "No rel=\"canonical\"."))
        ogtype = meta("og:type")
        wants_article = "/" in r and not r.endswith("/index.html")
        if ogtype is None:
            probs.append(finding("medium", "no-og-type", "No og:type."))
        elif wants_article and ogtype != "article":
            probs.append(finding("medium", "og-type", "og:type is %r; an article page should be \"article\"." % ogtype))
        ld = re.search(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I)
        if not ld:
            probs.append(finding("medium", "no-jsonld", "No JSON-LD block."))
        else:
            blob = ld.group(1)
            # datePublished/dateModified and headline are Article properties. CLAUDE.md asks
            # for them when publishing a COUNTRY; about.html, contact.html and privacy.html
            # are "@type": "WebPage", where schema.org defines neither, so demanding them
            # there was this check inventing a rule the site never had.
            is_article = re.search(r'"@type"\s*:\s*"[^"]*Article[^"]*"', blob) is not None
            if is_article:
                for key in ("datePublished", "dateModified"):
                    if key not in blob:
                        probs.append(finding("medium", "jsonld-date", "JSON-LD has no %s." % key))
                hm = re.search(r'"headline"\s*:\s*"([^"]*)"', blob)
                # the <title> carries HTML entities and the JSON does not, so "... Hike &amp;
                # Tirana" and "... Hike & Tirana" are the same headline; comparing them raw
                # reported drift on every country page at once, which is the shape of a bug
                # rather than the shape of a mistake someone made twenty times
                plain = title or ""
                if hm and plain and hm.group(1).strip() and hm.group(1).strip() not in plain:
                    probs.append(finding("low", "headline-drift", "The JSON-LD headline does not match the <title>.", hm.group(1)[:90]))
        # The "<Country> Travel Guide" h1 is the GUIDE page's rule (field-notes.html). A
        # country LANDING page is a different thing: a .country-hero with a breadcrumb above
        # the name, where the bare country reads correctly. Kevin's call, 2026-09-24, on
        # el-salvador/index.html, the only page of that shape.
        if r.endswith("/field-notes.html"):
            h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
            txt = re.sub(r"<[^>]+>", "", h1.group(1)).strip() if h1 else ""
            if txt and not txt.lower().endswith("travel guide"):
                probs.append(finding("low", "h1", "A country guide <h1> should read \"<Country> Travel Guide\".", txt[:70]))
        if probs:
            groups.append({"name": r, "findings": probs})
    return groups


# ---------------------------------------------------------------- 6. selfcheck
ROUTINE_TOOLS = ["tools/lint_site.py", "tools/lint_prose.py", "tools/americanize.py",
                 "tools/prose_check.py", "tools/prose_rules.py", "tools/report_email.py",
                 "tools/audit.py", "tools/strip_paste_artifacts.py", "tools/check_links.py"]
ROUTINE_FILES = ["review.js", "tools/redline.py", "CLAUDE.md", "tools/prose_allowlist.txt"]


def check_selfcheck():
    """A routine clones the repo and runs tools. Anything it needs that is not COMMITTED does
    not exist over there, however well it works here."""
    probs = []
    for f in ROUTINE_TOOLS + ROUTINE_FILES:
        if nfc(f) not in COMMITTED:
            probs.append(finding("high", "untracked",
                                 "A routine depends on this and it is not in the repo, so a fresh clone has no copy.", f))
    for t in ROUTINE_TOOLS:
        if nfc(t) not in COMMITTED:
            continue
        r = subprocess.run([sys.executable, "-c",
                            "import importlib.util,sys;s=importlib.util.spec_from_file_location('m',r'%s');"
                            "m=importlib.util.module_from_spec(s);sys.argv=['m'];s.loader.exec_module(m)" % (ROOT / t)],
                           cwd=ROOT, capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace")
        # a tool that prints usage and exits is fine; a tool that cannot be loaded is not
        err = (r.stderr or "")
        if "Traceback" in err and "SystemExit" not in err:
            probs.append(finding("high", "import-error", "This tool fails to load.", err.strip().splitlines()[-1][:160]))
    return [{"name": "Routine dependencies",
             "note": "Checked against the git index, which is what a cloud clone gets.",
             "findings": probs}] if probs else []


def check_dates():
    """A page states its date twice and the two disagreed on five published pages.

    The byline under the title is what a reader believes; dateModified is what a search engine
    reads. Only the second was ever maintained, so every El Salvador article told a reader
    March and a crawler September. Worse, the Armenia drafts were built from
    ruta-de-las-flores.html and inherited ITS byline, so the Yerevan guide was dated the day
    the Santa Ana page was written. tools/article_dates.py --fix settles them."""
    import article_dates as ad
    groups = []
    for path, rel, _ in ad.pages(False):
        s = path.read_text(encoding="utf-8", newline="")
        b, j = ad.BYLINE.search(s), ad.JSONLD.search(s)
        if not (b and j):
            continue
        shown = ad.iso(b.group(2))
        if shown != j.group(2):
            groups.append({"name": rel, "findings": [finding(
                "medium", "date-mismatch",
                "The byline says %s and dateModified says %s. Run tools/article_dates.py --fix."
                % (b.group(2), j.group(2)), shown + " vs " + j.group(2))]})
    return groups


CHECKS = {"prose": check_prose, "tiers": check_tiers, "heroes": check_heroes, "maps": check_maps,
          "dates": check_dates, "drafts": check_drafts, "seo": check_seo,
          "selfcheck": check_selfcheck}
TITLES = {"prose": "Prose", "tiers": "Image tiers", "heroes": "Heroes", "maps": "Map links",
          "dates": "Article dates", "drafts": "Draft readiness", "seo": "SEO metadata",
          "selfcheck": "Routine dependencies"}


def run(names):
    sections = []
    for n in names:
        groups = CHECKS[n]()
        if groups:
            sections.append((n, groups))
    return sections


LEAD_FOR = {
    "tiers": "a <picture> that is not serving the sizes the site promises, which is bandwidth on every visit",
    "heroes": "a hero that is heavier than it should be, which is the first thing a visitor waits for",
    "maps": "a map link that drops the reader on a Google results page instead of the pin",
    "prose": "writing that slipped past the style rules",
    "drafts": "what is between each draft and publishing",
    "seo": "a page whose metadata is not what CLAUDE.md requires to publish",
    "selfcheck": "a tool a routine depends on that a fresh clone would not have",
}


def fingerprint(section, group, f):
    """What makes a finding the SAME finding across runs. Deliberately not the wording: a
    message reworded here should not re-alert; a different page, rule or offending value
    should.

    Hashed, because the state file this lands in is meant to be committed so a daily cloud
    run can tell today's findings from yesterday's, and a cloud run starts from a fresh
    clone with no .tmp/. Held in the clear it read:

        Draft readiness|Drafts/.Full Articles/armenia/yerevan.html|placeholder|[Russian Ballet]

    getawayguide is a public repo and Drafts/ is a local-only one precisely so unfinished
    work stays private, so committing that would publish the draft paths and a 160-character
    excerpt of the prose. The digest compares identically and says nothing.
    """
    raw = "%s|%s|%s|%s" % (section, group, f.get("rule", ""), (f.get("value") or "")[:160])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def filter_new(sections, state_path, write=True):
    """-> (sections holding only unseen findings, how many known ones were withheld)"""
    state_path = Path(state_path)
    try:
        known = set(json.loads(state_path.read_text(encoding="utf-8")).get("seen", []))
    except Exception:
        known = set()
    out, seen_now, withheld = [], set(), 0
    for n, gs in sections:
        keep = []
        for g in gs:
            fresh = []
            for f in g["findings"]:
                fp = fingerprint(TITLES[n], g["name"], f)
                seen_now.add(fp)
                if fp in known:
                    withheld += 1
                else:
                    fresh.append(f)
            if fresh:
                keep.append({**g, "findings": fresh})
        if keep:
            out.append((n, keep))
    if write:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"seen": sorted(seen_now),
                                          "at": __import__("datetime").datetime.now().isoformat(timespec="seconds")},
                                         indent=1), encoding="utf-8")
    return out, withheld


def to_findings(sections, title, subtitle, cap=6, withheld=0):
    """The report, capped so it is readable on a phone: every section is listed, the worst
    pages in each are shown, and the tail is counted rather than printed."""
    groups, counts = [], {"high": 0, "medium": 0, "low": 0}
    rank = {"high": 0, "medium": 1, "low": 2}
    for n, gs in sections:
        for g in gs:
            for f in g["findings"]:
                counts[f.get("severity", "low")] = counts.get(f.get("severity", "low"), 0) + 1
        # worst first, so a capped section still shows the pages that matter
        ordered = sorted(gs, key=lambda g: min(rank.get(f.get("severity"), 2) for f in g["findings"]))
        shown = ordered if n == "selfcheck" else ordered[:cap]
        for g in shown:
            groups.append({**g, "section": TITLES[n]})
        if len(ordered) > len(shown):
            groups.append({"name": "and %d more page(s)" % (len(ordered) - len(shown)),
                           "section": TITLES[n],
                           "findings": [finding("low", "more",
                                                "Run python tools/audit.py --checks %s to see them all." % n)]})
    total = sum(counts.values())
    lead = []
    if withheld:
        lead.append("%d finding%s you have already seen %s left out of this one. Run "
                    "python tools/audit.py without --new-only to see everything."
                    % (withheld, "" if withheld == 1 else "s", "is" if withheld == 1 else "are"))
    if total:
        worst = sorted(sections, key=lambda s: -sum(len(g["findings"]) for g in s[1]))[:3]
        lead.append("The short version: " + "; ".join(
            "%s, %s" % (TITLES[n].lower(), LEAD_FOR.get(n, "")) for n, _ in worst) + ".")
    if total:
        bits = [f"{counts[k]} {lbl}" for k, lbl in (("high", "worth fixing"), ("medium", "minor"), ("low", "cosmetic")) if counts[k]]
        summary = "%d finding%s: %s" % (total, "" if total == 1 else "s", ", ".join(bits))
    else:
        summary = "Everything checked came back clean"
    return {"title": title, "subtitle": subtitle, "status": "findings" if total else "clean",
            "summary": summary, "groups": groups, "lead": lead,
            "footer": "Report only. Nothing was changed. Generated by tools/audit.py."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks", default=",".join(CHECKS))
    ap.add_argument("--json", help="write findings.json here (the schema report_email.py reads)")
    ap.add_argument("--title", default="Site audit")
    ap.add_argument("--subtitle", default=None)
    ap.add_argument("--drafts-only", action="store_true")
    ap.add_argument("--new-only", action="store_true",
                    help="report only findings not seen on the last run (for a daily routine)")
    ap.add_argument("--state", default=".tmp/audit_state.json")
    ap.add_argument("--dry-state", action="store_true", help="with --new-only, do not move the baseline")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    names = ["drafts"] if a.drafts_only else [c.strip() for c in a.checks.split(",") if c.strip() in CHECKS]
    sections = run(names)
    withheld = 0
    if a.new_only:
        sections, withheld = filter_new(sections, ROOT / a.state, write=not a.dry_state)
    sub = a.subtitle or "%d published pages, %d drafts, checks: %s" % (
        len(live_pages()), len(draft_pages()), ", ".join(names))
    d = to_findings(sections, a.title, sub, withheld=withheld)
    if a.json:
        Path(a.json).write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
        print("wrote %s" % a.json)
    print("\n" + "=" * 68 + "\n  %s\n  %s\n" % (d["summary"], sub) + "=" * 68)
    for n, gs in sections:
        print("\n[%s]" % TITLES[n])
        for g in gs:
            print("  %s" % g["name"])
            for f in g["findings"]:
                print("    %-8s %-22s %s" % (f["severity"], f["rule"], f["detail"][:72]))
                if f.get("value"):
                    print("             %s" % f["value"][:96])
    high = sum(1 for _, gs in sections for g in gs for f in g["findings"] if f["severity"] == "high")
    return 1 if high else 0


if __name__ == "__main__":
    sys.exit(main())
