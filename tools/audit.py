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

Exit code is 1 when anything HIGH was found, so a routine can gate on it.
"""
import argparse
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
        notes = [b for b in BRACKET.findall(body) if b.upper() != "[PHOTO]"]
        maps = SEARCH_LINK.findall(html)
        photos = [m for m in IMG.finditer(body)
                  if "/Images/" in attrs(m.group(0)).get("src", "")
                  and "/Images/web/" not in attrs(m.group(0)).get("src", "")]
        prose = [f for f in prose_check.check(html) if f["kind"] not in ("maps", "placeholder")]
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


CHECKS = {"prose": check_prose, "tiers": check_tiers, "heroes": check_heroes,
          "maps": check_maps, "drafts": check_drafts, "selfcheck": check_selfcheck}
TITLES = {"prose": "Prose", "tiers": "Image tiers", "heroes": "Heroes", "maps": "Map links",
          "drafts": "Draft readiness", "selfcheck": "Routine dependencies"}


def run(names):
    sections = []
    for n in names:
        groups = CHECKS[n]()
        if groups:
            sections.append((n, groups))
    return sections


def to_findings(sections, title, subtitle):
    groups, counts = [], {"high": 0, "medium": 0, "low": 0}
    for n, gs in sections:
        for g in gs:
            for f in g["findings"]:
                counts[f.get("severity", "low")] = counts.get(f.get("severity", "low"), 0) + 1
            groups.append({**g, "name": g["name"], "section": TITLES[n]})
    total = sum(counts.values())
    if total:
        bits = [f"{counts[k]} {lbl}" for k, lbl in (("high", "worth fixing"), ("medium", "minor"), ("low", "cosmetic")) if counts[k]]
        summary = "%d finding%s: %s" % (total, "" if total == 1 else "s", ", ".join(bits))
    else:
        summary = "Everything checked came back clean"
    return {"title": title, "subtitle": subtitle, "status": "findings" if total else "clean",
            "summary": summary, "groups": groups,
            "footer": "Report only. Nothing was changed. Generated by tools/audit.py."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks", default=",".join(CHECKS))
    ap.add_argument("--json", help="write findings.json here (the schema report_email.py reads)")
    ap.add_argument("--title", default="Site audit")
    ap.add_argument("--subtitle", default=None)
    ap.add_argument("--drafts-only", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    names = ["drafts"] if a.drafts_only else [c.strip() for c in a.checks.split(",") if c.strip() in CHECKS]
    sections = run(names)
    sub = a.subtitle or "%d published pages, %d drafts, checks: %s" % (
        len(live_pages()), len(draft_pages()), ", ".join(names))
    d = to_findings(sections, a.title, sub)
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
