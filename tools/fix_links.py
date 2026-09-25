#!/usr/bin/env python3
"""Repair the outbound links that can be repaired mechanically, and report the rest.

The daily link routine tells Kevin what is broken. This is the other half: the
part a machine is allowed to fix on its own. The split is the whole point, so it
is worth being precise about where it falls.

A link is FIXED only when the destination proves it is the same resource:

  canonical  the final URL differs from ours only by scheme, a "www.", or a
             trailing slash. Nothing about the page changed.
  moved      the site reorganised and told us where. We require the ORIGINAL's
             distinctive part to survive in the destination -- its slug, or a
             numeric id of four digits or more. budget-georgia.com moved
             /chalaadi-glacier-tour.html to /tour/chalaadi-glacier-tour/ and
             hostelworld renamed hostel 275702 from "hindustan-by-backpackers-
             heaven" to "backpackers-heaven-free-airport-pick-up". Same page,
             new address, no judgement required.
             Booking.com's habit of stripping its own ?aid=&label= tracking on
             redirect lands here too, and the clean URL is the better link.

Everything else is REPORTED and left alone:

  soft-404   the request returns 200, so every link checker on earth calls it
             healthy, but the destination is an ANCESTOR of what we asked for.
             getyourguide dropped tour t477207 and now lands the reader on the
             Kotor city page; hostelworld dropped hostel 313437 and lands them
             on a list of Lima hostels. The recommendation is dead and the page
             still reads as though it were not. This is the single most useful
             thing in here and check_links.py cannot see it -- it checks status
             codes, and the status code is 200.
  gone       a real 404/410, a dead domain, a TLS handshake that fails.
  unclear    a redirect that changed domain, or landed somewhere whose
             relationship to the original we cannot establish.
  blocked    401/403/406/429. alltrails refuses bots on every trail link we
             have; travel.state.gov does the same. Not broken, never actionable,
             kept in its own quiet list so it stops looking like a finding.

Replacing a closed restaurant is Kevin's call, not this tool's. It will never
retarget a link at something it merely thinks is similar, and it will never
delete one.

NETWORK. Python cannot reach the internet on this machine (broken TLS, and the
agent's shell has no egress), so the actual requests go through
tools/resolve_urls.ps1 and come back as a TSV. The scan is cached in
.tmp/link_scan.json, because the editor's startup preview has to be instant and
must never block the launch on 400 HTTP requests.

Writing is OPT-IN. A bare run reports what it WOULD repoint and changes nothing, because
the cloud routine runs this tool too and "report only" must not depend on a prompt
remembering to leave a flag off.

  python tools/fix_links.py --scan           refresh the cache (hits the network)
  python tools/fix_links.py                  what would be rewritten; writes nothing
  python tools/fix_links.py --apply          rewrite the safe classes
  python tools/fix_links.py --report         the human half: what needs Kevin
  python tools/fix_links.py --list           every class, including the ones it would fix
  python tools/fix_links.py --json F         findings for tools/report_email.py
"""
import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse as up

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".tmp", "link_scan.json")
PS1 = os.path.join(ROOT, "tools", "resolve_urls.ps1")
CREATE_NO_WINDOW = 0x08000000

# Not link rot: a font/flag CDN, and the sitemap schema URL, which is an XML
# namespace and not meant to be fetched at all.
SKIP_HOSTS = ("flagcdn.com", "fonts.googleapis.com", "fonts.gstatic.com",
              "schemas.sitemaps.org", "www.w3.org")
SKIP_PAGES = (".tmp/", "Drafts/", "archive/", ".git/", "node_modules/")

# A destination whose path says "we could not serve what you asked for". Google
# answers a flights deep link with /travel/flights/unsupported and a 200.
DEAD_END = re.compile(r"/(unsupported|not-?found|404|410|error|expired|"
                      r"parking|suspended|page-not-available)(/|\.|$)", re.I)

# A path segment too generic to prove anything survived the move.
GENERIC = {"", "en", "en-gb", "en-us", "index", "index.html", "home", "search",
           "searchresults", "searchresults.html", "s", "p", "tour", "tours",
           "hotel", "hotels", "hostels", "d", "www", "default.aspx"}

STALE_DATE = re.compile(r"[?&](chkin|chkout|from|to|date_from|date_to|checkin|checkout)="
                        r"(\d{4}-\d{2}-\d{2})")


# ----------------------------------------------------------------- collecting

def pages():
    for p in sorted(glob.glob(os.path.join(ROOT, "**", "*.html"), recursive=True)):
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.startswith(SKIP_PAGES) or rel == "editor.html":
            continue
        yield p, rel


def collect(include_maps=False):
    """{url: {"pages": {rel: [literal href text, ...]}}}

    The LITERAL text matters. An href in the file carries &amp; where the URL
    carries &, so a rewrite has to put back exactly the string it found rather
    than a re-encoded guess.
    """
    out = {}
    for path, rel in pages():
        html = io.open(path, encoding="utf-8", newline="").read()
        for m in re.finditer(r'href="(https?://[^"]+)"', html):
            lit = m.group(1)
            url = lit.replace("&amp;", "&")
            host = up.urlsplit(url).netloc.lower()
            if any(h in host for h in SKIP_HOSTS):
                continue
            if not include_maps and "google.com/maps" in url:
                continue
            e = out.setdefault(url, {"pages": {}})
            e["pages"].setdefault(rel, [])
            if lit not in e["pages"][rel]:
                e["pages"][rel].append(lit)
    return out


# ------------------------------------------------------------------- scanning

def scan(urls, workers=10, timeout=20):
    """Resolve every URL. Returns {url: [status, final, note]}.

    Two backends on purpose. On Kevin's Windows box Python has no working TLS and
    the agent's shell has no egress, so the requests go out through PowerShell.
    In the cloud routine's Linux container there is no PowerShell and urllib works
    fine. The CLASSIFICATION is the same code either way, which is the whole point
    -- a rule described in English gets applied differently every run, and the
    routine and Kevin's machine have to agree about what a dead link is.
    """
    if os.name != "nt" or not os.path.exists(PS1):
        return _scan_python(urls, workers=workers, timeout=timeout)
    return _scan_powershell(urls, workers=workers, timeout=timeout)


def _scan_python(urls, workers=10, timeout=20):
    import urllib.request
    import urllib.error
    from concurrent.futures import ThreadPoolExecutor

    ua = "Mozilla/5.0 (compatible; getawayguide-linkcheck/1.0; +https://getawayguide.io)"

    def one(u):
        for method in ("HEAD", "GET"):
            try:
                req = urllib.request.Request(u, method=method, headers={"User-Agent": ua})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return u, [int(r.status), r.geturl(), ""]
            except urllib.error.HTTPError as e:
                # the same HEAD-is-refused dance the PowerShell side does
                if method == "HEAD" and e.code in (400, 403, 405, 429, 501):
                    continue
                return u, [int(e.code), getattr(e, "url", u) or u, ""]
            except Exception as e:
                if method == "HEAD":
                    continue
                return u, [0, u, type(e).__name__ + ": " + str(e)[:100]]
        return u, [0, u, "no response"]

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for u, row in ex.map(one, urls):
            out[u] = row
    return out


def _scan_powershell(urls, workers=10, timeout=20):
    tmp = os.path.join(ROOT, ".tmp")
    os.makedirs(tmp, exist_ok=True)
    infile, outfile = os.path.join(tmp, "_urls.txt"), os.path.join(tmp, "_scan.tsv")
    io.open(infile, "w", encoding="utf-8", newline="\n").write("\n".join(urls))
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", PS1, "-InFile", infile, "-OutFile", outfile,
                        "-Workers", str(workers), "-TimeoutSec", str(timeout)],
                       cwd=ROOT, capture_output=True, text=True,
                       timeout=max(600, len(urls) * 3), creationflags=CREATE_NO_WINDOW)
    if not os.path.exists(outfile):
        raise SystemExit("the resolver wrote nothing:\n" + (r.stderr or r.stdout))
    res = {}
    for line in io.open(outfile, encoding="utf-8-sig"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 3:
            continue
        u, st, fin = parts[0], parts[1], parts[2]
        note = parts[3] if len(parts) > 3 else ""
        res[u] = [int(st or 0), fin, note]
    return res


def load_cache():
    if not os.path.exists(CACHE):
        return None
    try:
        return json.load(io.open(CACHE, encoding="utf-8"))
    except Exception:
        return None


# --------------------------------------------------------------- classifying

def norm(u):
    """Split a URL into the pieces worth comparing, with the noise levelled."""
    s = up.urlsplit(u)
    host = s.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = up.unquote(s.path)
    if path.endswith("/"):
        path = path[:-1]
    return host, path, up.unquote(s.query)


def segs(path):
    return [x for x in path.split("/") if x]


def tokens(path):
    """The parts of a path distinctive enough to recognise on the other side."""
    out = []
    for s in segs(path):
        s = re.sub(r"\.(html?|php|aspx)$", "", s, flags=re.I)
        # a locale infix is not part of the page's identity: booking.com answers
        # /hotel/tr/terra-cave.en.html by redirecting to /hotel/tr/terra-cave.html,
        # which is the same hotel and must not read as an unexplained move
        s = re.sub(r"\.[a-z]{2}(-[a-z]{2})?$", "", s, flags=re.I)
        if s.lower() in GENERIC or len(s) < 4:
            continue
        out.append(s.lower())
    return out


def ids(path):
    return set(re.findall(r"\d{4,}", path))


def classify(url, status, final, note):
    """Returns (kind, detail). kind is one of:
    ok canonical moved soft-404 gone unclear blocked stale-date"""
    if status == 0:
        # A timeout or a failed handshake is NOT the same claim as a 404. Three
        # of these (goindigo, hotels.com twice) are large sites that are up in a
        # browser and simply refuse this client. Calling them dead in an email
        # is how a report earns its reputation for crying wolf.
        return "unreachable", (note or "no response")
    if status in (401, 403, 406, 429):
        return "blocked", "HTTP %d (refuses bots)" % status
    if status >= 400:
        return "gone", "HTTP %d" % status

    oh, opath, oq = norm(url)
    fh, fpath, fq = norm(final or url)

    if (oh, opath, oq) == (fh, fpath, fq):
        return "ok", ""

    if DEAD_END.search(fpath):
        return "soft-404", "lands on %s" % (final or "")

    # A bare domain redirecting to its own landing page or locale is how the web
    # works (prioritypass.com -> /en-GB/). Not a finding, not worth a rewrite.
    if not segs(opath) and oh == fh:
        return "ok", ""

    if (oh, opath) == (fh, fpath):
        # same page, the query changed under us
        m = STALE_DATE.search(url)
        if m and not STALE_DATE.search(final or ""):
            return "stale-date", "the article pins %s=%s" % (m.group(1), m.group(2))
        if STALE_DATE.search(url):
            return "stale-date", "the article pins a booking date the site has moved on from"
        # The path has to identify something before dropping the query counts as
        # tidying. booking.com answers a searchresults URL carrying
        # highlighted_hotels=6033046 with a plain city search: same path, and the
        # one parameter naming the hotel is exactly what went missing. Rewriting
        # that would quietly turn a hotel recommendation into "here is the city".
        if not tokens(opath):
            return "unclear", "the query lost what identified the page: %s" % (final or "")
        return "canonical", final

    if oh == fh and not oq and not fq and opath and fpath:
        pass  # fall through to the structural tests

    # SOFT 404 FIRST. The destination being an ancestor of what we asked for is
    # the signature of "that page is gone, have the category instead", and some
    # of those still share an id with the original, so an id test run first
    # would wave them through as a clean move.
    if oh == fh and segs(fpath) and segs(fpath) == segs(opath)[:len(segs(fpath))] \
       and len(segs(fpath)) < len(segs(opath)):
        return "soft-404", "lands on the parent page %s" % (final or "")

    if oh != fh:
        return "unclear", "redirects to a different site: %s" % (final or "")

    # Same host from here on. Did the distinctive part of the address survive?
    ot, ft = tokens(opath), tokens(fpath)
    oi, fi = ids(opath), ids(fpath)
    if oi and oi & fi:
        return "moved", final
    if ot and any(t in ft for t in ot):
        return "moved", final
    if ot and not ft:
        return "soft-404", "lands on a bare %s" % (final or "")

    # We asked for a numbered listing and were sent somewhere that carries no
    # number and none of our words. hostelworld answers hostel 313437 with a
    # list of hostels in Lima: the hostel is gone, and nothing on the page we
    # land on is the thing the article recommended.
    if oi and not fi and not (set(ot) & set(ft)):
        return "soft-404", "the listing is gone; lands on a category page, %s" % (final or "")

    # Only the query differs in substance, and the path is intact: this is a
    # destination dropping its own tracking parameters, which is a better link.
    # The path has to MEAN something first. booking.com answers a searchresults
    # URL carrying highlighted_hotels=6033046 with a bare city search, and the
    # paths match because both are /searchresults.html -- rewriting that would
    # quietly turn a hotel recommendation into "here is the city".
    if opath == fpath and ot:
        return "canonical", final

    return "unclear", "redirects to %s" % (final or "")


def canonical_only(url, final):
    """True when nothing but scheme/www/trailing slash separates the two."""
    return norm(url) == norm(final or url)


# ----------------------------------------------------------------- rewriting

def rewrite(found, decisions, dry=False):
    """Apply {url: new_url} across the pages that carry them."""
    per_page = {}
    for url, new in decisions.items():
        for rel, lits in found[url]["pages"].items():
            per_page.setdefault(rel, []).extend((lit, url, new) for lit in lits)

    changed = []
    for rel, items in sorted(per_page.items()):
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        html = io.open(path, encoding="utf-8", newline="").read()
        orig, n = html, 0
        for lit, url, new in items:
            # put the escaping back exactly as the file writes it
            new_lit = new.replace("&", "&amp;") if "&amp;" in lit else new
            before = html
            html = html.replace('href="%s"' % lit, 'href="%s"' % new_lit)
            if html != before:
                n += 1
        if n and html != orig:
            if not dry:
                io.open(path, "w", encoding="utf-8", newline="").write(html)
            changed.append((rel, n))
    return changed


# --------------------------------------------------------------- the report

# What each class is, how loudly to say it, and the one line that explains why it
# is in the email at all. Kept next to the severities so the two cannot drift.
REPORTABLE = [
    ("soft-404", "high", "Gone, but still answering 200",
     "The destination is an ancestor of the page we asked for: the tour or the listing "
     "was dropped and the site is showing a category page instead. Every link checker "
     "calls these healthy, because the status code is 200. The recommendation is dead."),
    ("gone", "high", "Dead",
     "The site returned a 404. Nothing at that address any more."),
    ("stale-date", "medium", "Pinned to a date that has passed",
     "The link carries a booking date from an earlier trip, and the site quietly moves "
     "the reader to today instead. Worth dropping the date parameters."),
    ("unclear", "medium", "Redirected somewhere we cannot vouch for",
     "A redirect that changed domain, or landed somewhere whose relationship to the "
     "original could not be established. Left alone deliberately."),
    ("unreachable", "low", "No answer",
     "A timeout or a failed handshake, which is NOT the same claim as a 404. These are "
     "usually large sites refusing an unfamiliar client; check one in a browser before "
     "touching the article."),
    ("blocked", "low", "Refuses bots",
     "401, 403, 406 or 429. alltrails and travel.state.gov do this on every link we have. "
     "Not broken, never actionable, listed only so the count is honest."),
]


def write_json(path, buckets, found, age_h, checked):
    groups, counts = [], {}
    for kind, sev, title, why in REPORTABLE:
        rows = buckets.get(kind, [])
        if not rows:
            continue
        counts[sev] = counts.get(sev, 0) + len(rows)
        by_page = {}
        for url, final, detail in sorted(rows):
            for rel in sorted(found[url]["pages"]):
                by_page.setdefault(rel, []).append(
                    {"severity": sev, "rule": kind,
                     "detail": detail or ("-> " + final if final and final != url else ""),
                     "value": url})
        for rel, fs in sorted(by_page.items()):
            groups.append({"name": rel, "section": title, "findings": fs})

    total = sum(counts.values())
    bits = [f"{counts[k]} {lbl}" for k, lbl in
            (("high", "dead"), ("medium", "worth a look"), ("low", "probably fine"))
            if counts.get(k)]
    lead = [w for _, _, t, w in REPORTABLE if any(g["section"] == t for g in groups)
            for w in ([t + ". " + w])]
    doc = {
        "title": "Outbound links",
        "subtitle": "%d external link(s) checked, scan %.0f hour(s) old" % (checked, age_h),
        "status": "findings" if total else "clean",
        "summary": ("%d link%s: %s" % (total, "" if total == 1 else "s", ", ".join(bits)))
                   if total else "Every outbound link resolves",
        "lead": lead,
        "groups": groups,
        "footer": "Report only. The links whose destination proved it was the same page were "
                  "already repointed on Kevin's machine by tools/autofix.py; these are the ones "
                  "that need a person. Generated by tools/fix_links.py.",
    }
    io.open(path, "w", encoding="utf-8").write(json.dumps(doc, indent=1, ensure_ascii=False))
    print("wrote %s (%d finding(s) across %d group(s))" % (path, total, len(groups)))


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true",
                    help="re-resolve every link over the network (slow, needs PowerShell)")
    ap.add_argument("--list", action="store_true", help="what the cache says; writes nothing")
    ap.add_argument("--report", action="store_true", help="only the findings that need Kevin")
    # Writing is OPT-IN. The cloud routine runs this tool too, from a clone it would have to
    # push for a change to survive, and "report only" must not depend on the prompt
    # remembering to leave a flag off. A bare run reports and writes nothing.
    ap.add_argument("--apply", action="store_true", help="actually rewrite the safe classes")
    ap.add_argument("--dry-run", action="store_true", help="the default; kept for symmetry")
    ap.add_argument("--json", metavar="PATH",
                    help="write findings.json in the schema tools/report_email.py reads, so "
                         "the link email looks like the other two and reuses that renderer")
    ap.add_argument("--max-age", type=float, default=36.0,
                    help="hours before the cache counts as stale (default 36)")
    ap.add_argument("--workers", type=int, default=10)
    a = ap.parse_args()

    found = collect()
    urls = sorted(found)

    if a.scan:
        print("resolving %d external link(s) through PowerShell..." % len(urls))
        res = scan(urls, workers=a.workers)
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump({"at": time.time(), "results": res},
                  io.open(CACHE, "w", encoding="utf-8"), indent=0)
        print("cached %d result(s) in .tmp/link_scan.json" % len(res))

    cache = load_cache()
    if not cache:
        print("no scan yet: run  python tools/fix_links.py --scan")
        return 0
    age_h = (time.time() - cache.get("at", 0)) / 3600.0
    res = cache.get("results", {})

    buckets = {}
    for url in urls:
        if url not in res:
            continue                     # added since the last scan; next scan sees it
        status, final, note = res[url]
        kind, detail = classify(url, status, final, note)
        if kind == "ok":
            continue
        buckets.setdefault(kind, []).append((url, final, detail))

    fixable = {u: f for u, f, _ in buckets.get("canonical", []) + buckets.get("moved", [])}

    if age_h > a.max_age and not a.scan:
        print("note: the scan is %.0f hours old; --scan refreshes it" % age_h)

    if a.json:
        write_json(a.json, buckets, found, age_h, len(res))
        return 0

    # -- the half a machine may do ------------------------------------------
    if a.list or a.report:
        pass
    else:
        if fixable:
            changed = rewrite(found, fixable, dry=not a.apply)
            for rel, n in changed:
                print("  %-52s %2d link(s)" % (rel, n))
            print("%s %d link(s) across %d page(s)"
                  % ("changed" if a.apply else "would change",
                     sum(n for _, n in changed), len(changed)))
        else:
            print("%s 0 links" % ("changed" if a.apply else "would change"))

    if not (a.list or a.report):
        # autofix.py reads the line above; the rest is noise at launch time
        # Its own wording, not lint_prose's "prose issues:", because the startup
        # preview groups by that prefix and these are the opposite of tidied --
        # they are the ones nothing is allowed to touch.
        need = sum(len(buckets.get(k, []))
                   for k in ("soft-404", "gone", "unreachable", "unclear", "stale-date"))
        if need:
            gone = len(buckets.get("soft-404", [])) + len(buckets.get("gone", []))
            print("links needing a person: %d (%d of them dead) "
                  "-- python tools/fix_links.py --report" % (need, gone))
        return 0

    # -- the half only Kevin can do -----------------------------------------
    order = [("soft-404", "Still returns 200, but the page is GONE"),
             ("gone", "Dead (the site said 404)"),
             ("unreachable", "No answer -- may just be refusing this client"),
             ("stale-date", "Links a booking date that has passed"),
             ("unclear", "Redirected somewhere we cannot vouch for"),
             ("canonical", "Would be repointed (canonical)"),
             ("moved", "Would be repointed (moved, same page)"),
             ("blocked", "Refuses bots -- almost certainly fine")]
    for kind, title in order:
        rows = buckets.get(kind, [])
        if not rows:
            continue
        if a.report and kind in ("canonical", "moved", "blocked"):
            continue
        print("\n%s  (%d)" % (title, len(rows)))
        print("-" * 72)
        for url, final, detail in sorted(rows):
            print("  %s" % url)
            if detail and detail != final:
                print("      %s" % detail)
            elif final and final != url:
                print("      -> %s" % final)
            for rel in sorted(found[url]["pages"])[:4]:
                print("      on %s" % rel)
    print("\nscan is %.1f hours old, %d link(s) checked" % (age_h, len(res)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
