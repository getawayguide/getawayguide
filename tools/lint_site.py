#!/usr/bin/env python3
"""
lint_site.py — pre-push quality checker for the getawayguide site.

Scans live HTML pages (excludes Drafts/, .tmp/, .git/) and reports:
  1. duplicate id attributes on a page (invalid HTML; breaks anchor nav)
  2. broken in-page anchors  (href="#x" with no id="x")
  3. broken internal file links (href="foo.html" whose target file is missing)
 3b. assets referenced by a live page that are not in the repo (held-back draft files,
     which 404 on the deployed site), or referenced with the wrong case (Linux host)
  4. Google-Docs paste artifacts (font-size:1rem, opaque color:rgb(28,40,33))
  5. in-sentence em dashes (Kevin's no-em-dash rule; bullet separators are fine)
  6. Field-Notes nav-dropdown drift (a page missing/extra country vs the norm)
  7. <img> without alt=""
  8. missing <title> or meta description
  9. nested bold (<b> ... <b>) — usually a paste bug

Usage:
  python tools/lint_site.py            # lint live pages, summary + details
  python tools/lint_site.py --drafts   # also include Drafts/
  python tools/lint_site.py --quiet    # summary only
Exit code is non-zero if any issues are found (so it can gate a push).
"""
import re, sys, os, glob, subprocess, unicodedata, urllib.parse
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCLUDE_DRAFTS = "--drafts" in sys.argv
QUIET = "--quiet" in sys.argv
EXCLUDE = {"editor.html"}   # private/tool pages (also robots-disallowed)

VERB = re.compile(r"\b(is|are|was|were|has|have|it's|means?|feels?|lives?|sits?|runs?|packs?|"
                  r"needs?|covers?|follows?|focus(es)?|lies?|stretch(es)?|hosts?|offers?|makes?|comes?|remains?)\b")

def rel(p): return os.path.relpath(p, ROOT).replace("\\", "/")

def pages():
    for p in glob.glob(os.path.join(ROOT, "**", "*.html"), recursive=True):
        r = rel(p)
        if r.startswith("Drafts/") and not INCLUDE_DRAFTS: continue
        if r.startswith(".tmp/") or r.startswith(".git/"): continue
        if os.path.basename(r) in EXCLUDE or r in EXCLUDE: continue
        yield p, r

def lineno(text, idx): return text.count("\n", 0, idx) + 1
def strip_tags(s): return re.sub(r"<[^>]+>", "", s)


def nfc(s): return unicodedata.normalize("NFC", s)


def committed_files():
    """Every path that will exist on the server: the git index plus HEAD.

    Deliberately NOT the filesystem. Draft-only city maps and configs live on disk but are
    held back from every push, so `os.path.exists` says yes about a file the deployed site
    has never seen. A live page pointing at one 404s in production and looks perfect locally.

    -z because git octal-escapes non-ASCII paths otherwise, and NFC because the accented
    photo folders can be recorded either way. Getting both wrong reported eight healthy
    El Salvador images as missing.
    """
    out = set()
    for args in (["ls-files", "-z"], ["ls-tree", "-r", "-z", "--name-only", "HEAD"]):
        r = subprocess.run(["git"] + args, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            out |= {nfc(p) for p in r.stdout.split("\0") if p}
    return out


COMMITTED = committed_files()
LOWER = {p.lower() for p in COMMITTED}
ASSET = re.compile(r"""(?:src|href)=["']([^"'>]+)["']|url\(['"]?([^)'"]+)['"]?\)""")
REMOTE = ("http://", "https://", "//", "mailto:", "tel:", "data:", "javascript:", "#")

findings = defaultdict(list)   # check name -> list of (file, line, msg)
def add(check, f, line, msg): findings[check].append((f, line, msg))

def body_of(html):
    m = re.search(r'<div class="article-body">(.*?)</div>\s*</div>\s*<footer', html, re.S)
    return m.group(1) if m else html

nav_lists = {}   # file -> tuple of field-notes slugs

def blank_blocks(s):
    # replace <script>/<style> block contents with blank lines (keeps line numbers)
    return re.sub(r'<(script|style)\b[^>]*>.*?</\1>',
                  lambda m: "<%s></%s>" % (m.group(1), m.group(1)) + "\n" * m.group(0).count("\n"),
                  s, flags=re.S | re.I)

for path, r in pages():
    html = open(path, encoding="utf-8").read()
    clean = blank_blocks(html)   # for prose checks (no JS comments / CSS)

    # 1. duplicate ids
    ids = re.findall(r'\bid="([^"]+)"', html)
    for i, c in Counter(ids).items():
        if c > 1:
            add("duplicate-id", r, lineno(html, html.index('id="%s"' % i)), 'id="%s" appears %d times' % (i, c))

    # 2. broken in-page anchors
    idset = set(ids)
    for m in re.finditer(r'href="#([a-zA-Z][\w-]*)"', html):
        if m.group(1) not in idset:
            add("broken-anchor", r, lineno(html, m.start()), 'href="#%s" has no matching id' % m.group(1))

    # 3. broken internal file links
    for m in re.finditer(r'href="([^"#?:]+\.html)(?:#[^"]*)?"', html):
        target = m.group(1)
        if target.startswith("http"): continue
        dest = os.path.normpath(os.path.join(os.path.dirname(path), target))
        if not os.path.exists(dest):
            add("broken-link", r, lineno(html, m.start()), 'links to missing file: %s' % target)

    # 3b. assets that exist on disk but are not in the repo (held-back draft files), and
    #     references whose case does not match — Linux serves this site, Windows does not care
    if COMMITTED and not r.startswith("Drafts/"):
        base = os.path.dirname(r)
        for m in ASSET.finditer(html):
            raw = (m.group(1) or m.group(2) or "").split("#")[0].split("?")[0].strip()
            if not raw or raw.startswith(REMOTE):
                continue
            u = urllib.parse.unquote(raw)
            t = u.lstrip("/") if u.startswith("/") else "%s/%s" % (base, u) if base else u
            t = nfc(os.path.normpath(t).replace("\\", "/"))
            if t.endswith("/") or "." not in os.path.basename(t):
                continue
            if t in COMMITTED:
                continue
            if t.lower() in LOWER:
                add("asset-case", r, lineno(html, m.start()),
                    "case does not match the committed file: %s" % raw)
            elif os.path.exists(os.path.join(ROOT, t)):
                add("unpublished-asset", r, lineno(html, m.start()),
                    "on disk but not in the repo, so it 404s live: %s" % raw)
            else:
                add("missing-asset", r, lineno(html, m.start()), "no such file: %s" % raw)

    # 4. paste artifacts (skip <style>/<script>)
    for m in re.finditer(r'font-size:\s*1rem', clean):
        add("paste-artifact", r, lineno(clean, m.start()), "font-size:1rem span")
    for m in re.finditer(r'color:\s*rgb\(28,\s*40,\s*33\)', clean):
        add("paste-artifact", r, lineno(clean, m.start()), "opaque color:rgb(28,40,33)")

    # 5. in-sentence em dashes (body prose only; skip headings/toc/title/meta/script/style)
    for i, ln in enumerate(re.split(r"\n", clean), 1):
        if "—" not in ln: continue
        if re.search(r"<title>|<meta|fn-sub-hd|toc-", ln): continue
        # A dash inside a heading is a separator, not prose. Skip by SPAN, not by line: these
        # files put a whole card on one line, so `<h3>Ruta de las Flores — ...</h3>` sits in
        # the same line as its <p> excerpt, and skipping the line would blind the excerpt too.
        heads = [(m.start(), m.end()) for m in
                 re.finditer(r"<h[1-4]\b.*?</h[1-4]>", ln, re.S | re.I)]
        for m in re.finditer("—", ln):
            if any(a <= m.start() < b for a, b in heads):
                continue
            # Distinguish the ALLOWED `Name — description` lead-in from a genuine in-sentence
            # dash by how much running prose precedes it inside its own <li>/<p>/sentence.
            # (The old test — "does the char before the dash close a tag?" — flagged every
            # lead-in whose name ended in a parenthetical, e.g. "Philae Temple (near Aswan) —",
            # producing ~97% false positives.)
            q = ln.rfind('"', 0, m.start()); eq = ln.rfind("=", 0, max(0, q))
            if q > 0 and eq > 0 and q - eq <= 2 and ln.find('"', m.start()) > m.start():
                continue                                    # inside an attribute, not prose
            block = max(ln.rfind("<li", 0, m.start()), ln.rfind("<p", 0, m.start()), 0)
            # The documented lead-in is `<b>Name</b> — description` opening its own bullet, so
            # match that shape instead of inferring it from length. Two real lead-ins were
            # being flagged only because the bolded name was long: a Fitz Roy hike with the
            # distance in parentheses, and "Alcaldía Municipal de Santa Ana and Monumento a la
            # Libertad". Word count cannot tell those from prose; the markup can. Tags may
            # also sit between the bold close and the dash (Belgium wraps the dash itself
            # in a <span>), so the tail allows them.
            if re.match(r"^\s*(?:<(?!b\b|strong\b)[^>]+>\s*)*<(b|strong)\b[^>]*>.*?</\1>\s*(?:<[^>]+>\s*)*$",
                        ln[block:m.start()], re.S):
                continue
            head = max(block, ln.rfind(". ", 0, m.start()), 0)
            pre = re.sub(r"^\W+", "", strip_tags(ln[head:m.start()]).strip())
            # first person belongs with the other pronouns already here: a bullet like
            # "I walked past the cathedral <b>twice</b> — it was closed" is narration, not a
            # lead-in, but it is short and carries none of the listed verbs, so it slipped through
            lead_in = (len(pre.split()) <= 9 and
                       not re.search(r"\b(is|are|was|were|has|have|will|can|you|we|it|they|i|my|me|our)\b",
                                     pre, re.I))
            if pre and not lead_in:
                add("em-dash", r, i, strip_tags(ln[max(0, m.start()-35):m.start()+30]).strip())
                break

    # 6. collect nav field-notes list
    slugs = tuple(re.findall(r'href="(?:\.\./)*([a-z-]+)/field-notes\.html" class="nav-dropdown-item"', html))
    if slugs: nav_lists[r] = slugs

    # 7. img without alt
    for m in re.finditer(r"<img\b[^>]*>", html):
        if "alt=" not in m.group(0):
            add("img-no-alt", r, lineno(html, m.start()), m.group(0)[:70])

    # 8. missing title / meta description
    if "<title>" not in html:
        add("meta", r, 1, "missing <title>")
    if 'name="description"' not in html:
        add("meta", r, 1, "missing meta description")

    # 9. nested bold
    for m in re.finditer(r"<b\b[^>]*>(?:(?!</b>).)*?<b\b", html, re.S):
        add("nested-bold", r, lineno(html, m.start()), "nested <b> (whole-line bold bug?)")

# 6 (cont). nav drift: compare each page's set to the most common set
if nav_lists:
    norm = Counter(frozenset(v) for v in nav_lists.values()).most_common(1)[0][0]
    for f, v in nav_lists.items():
        s = frozenset(v)
        missing = norm - s
        extra = s - norm
        if missing or extra:
            parts = []
            if missing: parts.append("missing " + ", ".join(sorted(missing)))
            if extra: parts.append("extra " + ", ".join(sorted(extra)))
            add("nav-drift", f, 0, "; ".join(parts))

# ---- report ----
ORDER = ["duplicate-id", "broken-link", "broken-anchor", "unpublished-asset", "missing-asset",
         "asset-case", "nav-drift", "nested-bold", "paste-artifact", "em-dash", "img-no-alt",
         "meta"]
LABEL = {
    "duplicate-id": "Duplicate id attributes", "broken-link": "Broken internal file links",
    "broken-anchor": "Broken in-page anchors", "nav-drift": "Nav Field-Notes list drift",
    "nested-bold": "Nested <b> (whole-line bold)", "paste-artifact": "Google-Docs paste artifacts",
    "em-dash": "In-sentence em dashes", "img-no-alt": "Images missing alt", "meta": "Missing title/meta",
    "unpublished-asset": "Assets not in the repo (404 live)",
    "missing-asset": "Assets that do not exist", "asset-case": "Asset case mismatch (Linux host)",
}
# breaking issues that should fail CI (vs. style/quality warnings like em dashes)
ERROR_CHECKS = {"duplicate-id", "broken-link", "broken-anchor", "nav-drift",
                "unpublished-asset", "missing-asset", "asset-case"}
CI = "--ci" in sys.argv

total = sum(len(v) for v in findings.values())
errors = sum(len(findings.get(k, [])) for k in ERROR_CHECKS)
print("=" * 60)
print("  lint_site.py — %d issue(s) across %d live pages (%d error, %d warning)"
      % (total, sum(1 for _ in pages()), errors, total - errors))
print("=" * 60)
# anything not in ORDER still gets printed: a new check used to be counted in the header and
# then silently dropped from the detail list, which reads as "3 errors" with nothing under it
for k in ORDER + [k for k in sorted(findings) if k not in ORDER]:
    v = findings.get(k, [])
    if not v: continue
    sev = "ERROR" if k in ERROR_CHECKS else "warn"
    print("\n[%s] %s — %d  (%s)" % (k, LABEL.get(k, k), len(v), sev))
    if QUIET: continue
    for f, line, msg in v[:40]:
        loc = "%s:%d" % (f, line) if line else f
        print("   %-45s %s" % (loc, msg))
    if len(v) > 40:
        print("   ... and %d more" % (len(v) - 40))
if total == 0:
    print("\n  ✓ clean")
elif CI:
    print("\n  CI mode: %s (fails only on error-class checks)" % ("FAIL" if errors else "PASS"))
# --ci fails only on breaking issues; default fails on anything
sys.exit(1 if (errors if CI else total) else 0)
