"""Stress the routine quality checks and the editor routes they lean on.

Not a demo. Each case is something that would actually go wrong: hostile input, malformed
HTML, a 3 MB article, twenty requests at once, the same call twice, a tool run from a
directory it did not expect. Every case says PASS or FAIL and the script exits non-zero if
anything failed, so it can gate a commit.

    python tools/test_routines.py            # everything
    python tools/test_routines.py editor     # just the editor routes
    python tools/test_routines.py email      # just audit.py + report_email.py
"""
import concurrent.futures as cf
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRV = "http://127.0.0.1:5003"
MAPS = "http://127.0.0.1:5002"
PY = sys.executable
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  %-4s %-52s %s" % ("PASS" if ok else "FAIL", name, detail[:70]))
    return ok


def post(url, obj, timeout=900):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def get(url, timeout=900, raw=False):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
        return r.status, (body if raw else json.loads(body))


def run(args, timeout=1800, cwd=ROOT):
    return subprocess.run([PY] + args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def tracked_state():
    r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return {l[3:] for l in r.stdout.splitlines() if not l.startswith("??")}


# ------------------------------------------------------------------ editor routes
def stress_editor():
    print("\n== editor: /review/lint")
    HOSTILE = [
        ("empty", ""),
        ("no html at all", "just some words, the  the colour of it"),
        ("unclosed tags", "<p>the  the <b>colour<p>of it"),
        ("nested picture", "<p>a</p>" + "<picture><source srcset='x.webp'><img src='../Images/y.jpg'></picture>" * 50),
        ("script injection", "<p>the colour</p><script>alert('x')</script><p onclick='evil()'>the  the</p>"),
        ("entity soup", "<p>caf&eacute;&nbsp;&mdash; the&nbsp;&nbsp;colour&amp;more</p>"),
        ("only a comment", "<!-- the colour of it -->"),
        ("10k dashes", "<p>" + "a \u2014 b " * 5000 + "</p>"),
    ]
    for name, body in HOSTILE:
        try:
            st, d = post(SRV + "/review/lint", {"html": body}, timeout=300)
            ok = st == 200 and isinstance(d.get("findings"), list)
            check("lint: %s" % name, ok, "%d finding(s)" % len(d.get("findings", [])))
        except Exception as e:
            check("lint: %s" % name, False, repr(e)[:70])

    # a real article, twice, must agree
    art = (ROOT / "Drafts/.Full Articles/armenia/yerevan.html").read_text(encoding="utf-8")
    try:
        _, a = post(SRV + "/review/lint", {"html": art})
        _, b = post(SRV + "/review/lint", {"html": art})
        check("lint: idempotent on a real article", a == b, "%d finding(s)" % len(a["findings"]))
    except Exception as e:
        check("lint: idempotent on a real article", False, repr(e)[:70])

    # a big one
    big = art * 6
    try:
        t = time.time(); st, d = post(SRV + "/review/lint", {"html": big})
        check("lint: %.1f MB article" % (len(big) / 1e6), st == 200,
              "%d finding(s) in %.1fs" % (len(d["findings"]), time.time() - t))
    except Exception as e:
        check("lint: big article", False, repr(e)[:70])

    # twenty at once
    try:
        with cf.ThreadPoolExecutor(10) as ex:
            outs = list(ex.map(lambda i: post(SRV + "/review/lint", {"html": "<p>the  the colour %d</p>" % i}), range(20)))
        check("lint: 20 concurrent", all(s == 200 for s, _ in outs), "all 200")
    except Exception as e:
        check("lint: 20 concurrent", False, repr(e)[:70])

    print("\n== editor: /review/resolve-maps")
    for name, qs in [("empty list", []), ("blank strings", ["", "   "]),
                     ("cached place", ["Split Croatia"]),
                     ("nonsense", ["zzqqxx not a real place 91231"]),
                     ("non-string junk", [123, None, {"a": 1}])]:
        try:
            st, d = post(SRV + "/review/resolve-maps", {"queries": qs}, timeout=900)
            urls = d.get("urls", {})
            ok = st == 200 and isinstance(urls, dict)
            if name == "cached place":
                ok = ok and "/maps/place/" in (urls.get("Split Croatia") or "")
            check("resolve: %s" % name, ok, str(list(urls.values())[:1])[:60])
        except Exception as e:
            check("resolve: %s" % name, False, repr(e)[:70])

    print("\n== editor: /preview")
    try:
        rel = "Drafts/.Full Articles/armenia/yerevan.html"
        st, d = post(SRV + "/preview", {"rel": rel, "html": "<html><head><title>T</title></head><body><p>hi</p></body></html>"})
        st2, body = get(SRV + d["url"].replace(" ", "%20"), raw=True)
        check("preview: round trip", st == 200 and st2 == 200 and "<base href=" in body,
              "base=%s" % (re.search(r'<base href="([^"]+)"', body).group(1) if "<base" in body else "none"))
    except Exception as e:
        check("preview: round trip", False, repr(e)[:70])
    for name, payload in [("no rel", {"html": "<p>x</p>"}), ("no html", {"rel": "a/b.html"}), ("empty", {})]:
        try:
            st, _ = post(SRV + "/preview", payload)
            check("preview: rejects %s" % name, False, "accepted it (HTTP %d)" % st)
        except urllib.error.HTTPError as e:
            check("preview: rejects %s" % name, e.code == 400, "HTTP %d" % e.code)
        except Exception as e:
            check("preview: rejects %s" % name, False, repr(e)[:70])
    try:
        get(SRV + "/preview/nope/never-stored.html", raw=True)
        check("preview: 404 for an unknown path", False, "served something")
    except urllib.error.HTTPError as e:
        check("preview: 404 for an unknown path", e.code == 404, "HTTP 404")
    try:
        rel = "Drafts/.Full Articles/armenia/yerevan.html"
        big = "<html><head><title>T</title></head><body>" + "<p>x</p>" * 40000 + "</body></html>"
        # One retry. The 20-concurrent lint case runs just above this and the photo server is
        # Flask's single-threaded dev server, so a 0.3 MB POST arriving while it is still
        # draining that burst gets its connection reset. That is the dev server under a load
        # no writer will ever make, not the preview route: the same request succeeds on its
        # own every time. Without the retry the suite cries wolf and stops being read.
        for attempt in (1, 2):
            try:
                st, d = post(SRV + "/preview", {"rel": rel, "html": big})
                break
            except (urllib.error.URLError, ConnectionError):
                if attempt == 2:
                    raise
                time.sleep(2)
        st2, body = get(SRV + d["url"].replace(" ", "%20"), raw=True)
        check("preview: %.1f MB document" % (len(big) / 1e6), st2 == 200 and len(body) > len(big) - 100, "%d bytes back" % len(body))
    except Exception as e:
        check("preview: big document", False, repr(e)[:70])

    print("\n== editor: map editor")
    try:
        st, d = get(MAPS + "/api/slug-for?page=Drafts/.Full%20Articles/armenia/yerevan.html")
        check("maps: slug-for finds both kinds", d.get("itinerary") == ["armenia-yerevan"] and d.get("city") == ["yerevan"], str(d))
    except Exception as e:
        check("maps: slug-for", False, repr(e)[:70])
    try:
        st, d = get(MAPS + "/api/slug-for?page=../../../etc/passwd")
        check("maps: slug-for shrugs at a traversal", st == 200 and d == {"itinerary": [], "city": []}, str(d))
    except Exception as e:
        check("maps: slug-for traversal", False, repr(e)[:70])
    try:
        get(MAPS + "/api/city/definitely-not-a-city")
        check("maps: unknown city 404s", False, "served it")
    except urllib.error.HTTPError as e:
        check("maps: unknown city 404s", e.code == 404, "HTTP 404")
    try:
        get(MAPS + "/city-png/../../../CLAUDE.md", raw=True)
        check("maps: city-png refuses traversal", False, "served the file")
    except urllib.error.HTTPError as e:
        check("maps: city-png refuses traversal", e.code in (400, 403, 404), "HTTP %d" % e.code)
    except Exception as e:
        check("maps: city-png refuses traversal", True, "rejected: %s" % type(e).__name__)
    try:
        t = time.time()
        with cf.ThreadPoolExecutor(3) as ex:
            outs = list(ex.map(lambda _: get(MAPS + "/api/map/armenia-yerevan", timeout=900), range(3)))
        check("maps: 3 concurrent itinerary renders", all(s == 200 for s, _ in outs), "%.1fs" % (time.time() - t))
    except Exception as e:
        check("maps: concurrent itinerary", False, repr(e)[:70])


# ------------------------------------------------------------------ email checks
def stress_email():
    print("\n== audit.py")
    before = tracked_state()
    for chk in ["prose", "tiers", "heroes", "maps", "drafts", "selfcheck"]:
        r = run(["tools/audit.py", "--checks", chk])
        ok = r.returncode in (0, 1) and ("finding" in r.stdout or "clean" in r.stdout)
        check("audit: --checks %s" % chk, ok,
              r.stdout.strip().splitlines()[1].strip()[:56] if len(r.stdout.splitlines()) > 1 else r.stderr[-60:])
    r = run(["tools/audit.py", "--checks", "nonsense,prose"])
    check("audit: ignores an unknown check", r.returncode in (0, 1), "ran anyway")
    r = run(["tools/audit.py", "--drafts-only"])
    check("audit: --drafts-only", r.returncode in (0, 1) and "Draft readiness" in r.stdout, "")
    out = ROOT / ".tmp/stress-findings.json"
    r = run(["tools/audit.py", "--json", str(out), "--title", "Stress"])
    try:
        d = json.loads(out.read_text(encoding="utf-8"))
        need = {"title", "subtitle", "status", "summary", "groups", "footer"}
        check("audit: --json matches the email schema", need <= set(d), "keys ok, %d groups" % len(d["groups"]))
        bad = [g for g in d["groups"] if not isinstance(g.get("findings"), list) or "name" not in g]
        check("audit: every group is well formed", not bad, "%d malformed" % len(bad))
        sev = {f["severity"] for g in d["groups"] for f in g["findings"]}
        check("audit: severities are the three known ones", sev <= {"high", "medium", "low"}, str(sorted(sev)))
    except Exception as e:
        check("audit: --json", False, repr(e)[:70])
    # twice in a row must agree
    out2 = ROOT / ".tmp/stress-findings-2.json"
    run(["tools/audit.py", "--json", str(out2), "--title", "Stress"])
    try:
        check("audit: deterministic", out.read_text(encoding="utf-8") == out2.read_text(encoding="utf-8"), "identical")
    except Exception as e:
        check("audit: deterministic", False, repr(e)[:60])
    # from another working directory
    r = run([str(ROOT / "tools/audit.py"), "--checks", "selfcheck"], cwd=ROOT.parent)
    check("audit: runs from another directory", r.returncode in (0, 1), r.stderr.strip().splitlines()[-1][:60] if r.returncode not in (0, 1) else "")
    check("audit: changed no tracked file", tracked_state() == before, "")

    print("\n== report_email.py")
    CASES = [
        ("clean", {"title": "T", "status": "clean", "summary": "All clean", "groups": []}),
        ("bare minimum", {}),
        ("missing groups key", {"title": "T", "summary": "s"}),
        ("group with no findings", {"summary": "s", "groups": [{"name": "a.html", "findings": []}]}),
        ("unknown severity", {"summary": "s", "groups": [{"name": "a", "findings": [{"severity": "nuclear", "detail": "d"}]}]}),
        ("unicode", {"title": "Türkiye", "summary": "Café — naïve", "subtitle": "日本語",
                     "groups": [{"name": "türkiye/field-notes.html", "findings": [{"severity": "low", "detail": "Ærø", "value": "→ ←"}]}]}),
        ("html injection", {"title": "<script>alert(1)</script>", "summary": "<img src=x onerror=alert(1)>",
                            "groups": [{"name": "<b>x</b>", "note": "</td></tr></table><script>",
                                        "findings": [{"severity": "high", "rule": "<i>r</i>", "detail": "\"'><script>", "value": "</style><script>alert(1)</script>"}]}]}),
        ("very long value", {"summary": "s", "groups": [{"name": "a", "findings": [{"severity": "low", "detail": "d", "value": "x" * 5000}]}]}),
        ("many groups", {"summary": "s", "groups": [{"name": "p%d.html" % i, "section": "S%d" % (i % 3),
                                                     "findings": [{"severity": "low", "detail": "d"}]} for i in range(200)]}),
    ]
    for name, obj in CASES:
        f = ROOT / ".tmp/stress-email.json"
        f.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        r = run(["tools/report_email.py", str(f)])
        html = r.stdout
        ok = r.returncode == 0 and "<body" in html and "</html>" in html
        check("email: %s renders" % name, ok, r.stderr.strip().splitlines()[-1][:60] if not ok else "%d bytes" % len(html))
        if name == "html injection" and ok:
            # Parse it. A substring test cannot tell "onerror=" inside escaped TEXT from a live
            # attribute, and the escaped form is exactly what we want to see.
            from html.parser import HTMLParser

            class P(HTMLParser):
                def __init__(self):
                    super().__init__(); self.tags = []; self.ev = []; self.js = []

                def handle_starttag(self, tag, attrs):
                    self.tags.append(tag)
                    for k, v in attrs:
                        if k.lower().startswith("on"):
                            self.ev.append((tag, k))
                        if k.lower() in ("href", "src") and (v or "").strip().lower().startswith("javascript:"):
                            self.js.append((tag, v))

            par = P(); par.feed(html)
            check("email: no script/svg element survives", par.tags.count("script") == 0 and par.tags.count("svg") == 0,
                  "script=%d svg=%d" % (par.tags.count("script"), par.tags.count("svg")))
            check("email: no event handler attribute", not par.ev, str(par.ev[:2]))
            check("email: no javascript: url", not par.js, str(par.js[:2]))
            check("email: the payload is still readable", "&lt;script&gt;" in html, "escaped and shown")
    # stdin path
    f = ROOT / ".tmp/stress-email.json"
    f.write_text(json.dumps({"summary": "piped"}), encoding="utf-8")
    r = subprocess.run([PY, "tools/report_email.py"], cwd=ROOT, input=f.read_text(encoding="utf-8"),
                       capture_output=True, text=True, encoding="utf-8")
    check("email: reads stdin", r.returncode == 0 and "piped" in r.stdout, "")
    r = subprocess.run([PY, "tools/report_email.py"], cwd=ROOT, input="{not json",
                       capture_output=True, text=True, encoding="utf-8")
    check("email: fails loudly on bad JSON", r.returncode != 0, (r.stderr.strip().splitlines() or [""])[-1][:56])
    # The bytes, not the decoded string. Every check above asked subprocess to decode as
    # utf-8, so none of them could see that a redirected stdout on Windows had encoded the
    # page with the locale codepage: the mail promised utf-8 in its own <meta> and delivered
    # cp1252, and every em dash in an excerpt arrived as the replacement character.
    f.write_text(json.dumps({"summary": "encoding", "groups": [{"name": "t.html", "findings": [
        {"rule": "em-dash", "message": "Em dash — here", "value": "café — naïve – “quoted” …"}]}]}),
        encoding="utf-8")
    r = subprocess.run([PY, "tools/report_email.py", str(f)], cwd=ROOT, capture_output=True)
    try:
        page = r.stdout.decode("utf-8")
        bad = page.count("�")
        check("email: stdout is utf-8, as the meta promises", bad == 0 and "—" in page,
              "no replacement chars, em dash intact" if bad == 0 else "%d replacement chars" % bad)
    except UnicodeDecodeError as e:
        check("email: stdout is utf-8, as the meta promises", False, str(e)[:60])
    # The real report, end to end. --no-ignore because this asserts the SHAPE of the email,
    # and tools/audit_ignore.txt is a record of Kevin's decisions, not of what the renderer
    # can draw: once it muted enough sections the site rendered with no findings at all and
    # "excerpts keep their whitespace" failed for want of an excerpt to look at. A structural
    # test must not depend on the site being dirty.
    r = run(["tools/audit.py", "--no-ignore", "--json", str(ROOT / ".tmp/stress-findings.json"),
             "--title", "Publish hygiene"])
    r2 = run(["tools/report_email.py", str(ROOT / ".tmp/stress-findings.json")])
    html = r2.stdout
    checks = [("has the nav", "getawayguide</a>" in html),
              # The hero's gradient is a stack of solid bands, not a CSS gradient: Gmail's
              # phone apps drop background-image and the hero arrived flat. So assert the
              # ramp is really there -- its first green and its last near-black -- rather
              # than that some gradient keyword appears.
              ("has the hero", 'bgcolor="#3C6B55"' in html and 'bgcolor="#241A12"' in html
                               and "linear-gradient" not in html),
              ("has the footer", "Dispatches from the road" in html),
              # the one <style> block is deliberate: it is the only way to hand a phone a
              # designed dark palette instead of letting it invert the card itself
              ("exactly one style block, for dark mode", html.count("<style") == 1 and "prefers-color-scheme" in html),
              ("declares it handles both schemes", 'name="color-scheme"' in html),
              ("dark palette reaches every surface", all(c in html for c in ("#101412", "#191F1B", "#E7E3D8", "#7FC9A1"))),
              ("tables only, no flex or grid", "display:flex" not in html and "display:grid" not in html),
              ("600px cap", "max-width:600px" in html),
              # the excerpt is evidence: a double-space finding whose two spaces collapse
              # into one shows the reader nothing wrong
              ("excerpts keep their whitespace", "white-space:pre-wrap" in html),
              ("every font stack has a fallback", all(("Georgia" in s or "Arial" in s or "monospace" in s or "sans-serif" in s)
                                                      for s in re.findall(r"font-family:([^;\"]+)", html)))]
    for n, ok in checks:
        check("email: %s" % n, ok, "")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    t = time.time()
    if which in ("all", "editor"):
        stress_editor()
    if which in ("all", "email"):
        stress_email()
    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 70)
    print("  %d checks, %d failed, %.0fs" % (len(RESULTS), len(bad), time.time() - t))
    for n in bad:
        print("    FAILED: %s" % n)
    print("=" * 70)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
