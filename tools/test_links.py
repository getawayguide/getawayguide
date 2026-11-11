#!/usr/bin/env python3
"""Pin down what fix_links.py may rewrite, and what check_links.py may call dead.

This classifier is the only thing standing between a redirect and an automatic
edit to Kevin's published articles, so the cases below are not invented: every
one is a real URL from the 2026-09-25 scan of the live site, with the status and
final address the site actually returned. If a change to the rules moves one of
these, it moves something that shipped.

The two directions matter differently. A missed fix costs a redirect hop. A
WRONG fix silently retargets a recommendation, and nobody finds out, because the
new link returns 200. So the cases that must stay un-rewritten carry most of the
weight here.

The second half covers check_links.py: only a truly dead link may count as a
failure, and a bot wall must never look like one (2026-10-15: 103 of 106 "dead"
links were bot walls). The end-to-end checks talk to a throwaway server on
127.0.0.1, so they need no internet.

  python tools/test_links.py
"""
import contextlib
import http.server
import io
import os
import re
import socket
import sys
import tempfile
import threading
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_links as cl
import fix_links as fl

FIX = ("canonical", "moved")            # the two kinds that get written

# (why, url, status, final, expected kind)
CASES = [
    # ---- may be rewritten: the destination proves it is the same page --------
    ("slug survives a reorganisation",
     "https://www.budget-georgia.com/chalaadi-glacier-tour.html", 200,
     "https://www.budget-georgia.com/tour/chalaadi-glacier-tour/", "moved"),
    ("numeric id survives a rename",
     "https://www.hostelworld.com/hostels/p/275702/hindustan-by-backpackers-heaven/", 200,
     "https://www.hostelworld.com/hostels/p/275702/backpackers-heaven-free-airport-pick-up/",
     "moved"),
    ("tour id survives a spelling correction (sakkara -> saqqara)",
     "https://www.getyourguide.com/en-gb/cairo-l92/giza-pyramids-sphinx-and-sakkara-private-full-day-tour-t11731/",
     200,
     "https://www.getyourguide.com/en-gb/cairo-l92/giza-pyramids-sphinx-and-saqqara-private-full-day-tour-t11731/",
     "moved"),
    ("place id survives a rename (prison-island -> changuu-island)",
     "https://www.getyourguide.com/en-gb/prison-island-l97419/?date_from=2026-07-25", 200,
     "https://www.getyourguide.com/en-gb/changuu-island-l97419/", "moved"),
    ("a locale infix is not part of the page's identity",
     "https://www.booking.com/hotel/tr/terra-cave.en.html?aid=311088&gclid=x", 200,
     "https://www.booking.com/hotel/tr/terra-cave.html", "moved"),
    ("the destination drops its own tracking, path intact",
     "https://www.booking.com/hotel/ge/latour-mestia.html?label=junk&aid=311088", 200,
     "https://www.booking.com/hotel/ge/latour-mestia.html", "canonical"),

    # ---- must NEVER be rewritten -------------------------------------------
    ("soft 404: the tour is gone, we land on the city page. Shares id l32202 "
     "with its destination, so an id test running first would call this a move",
     "https://www.getyourguide.com/en-gb/kotor-l32202/kotor-blue-cave-and-boka-bay-tour-pickup-t477207/",
     200, "https://www.getyourguide.com/en-gb/kotor-l32202/", "soft-404"),
    ("soft 404: the hostel is gone, we land on a list of Lima hostels",
     "https://www.hostelworld.com/hostels/p/313437/rainbow-hostel/", 200,
     "https://www.hostelworld.com/hostels/south-america/peru/lima/", "soft-404"),
    ("soft 404 by marker: google answers a deep link with /unsupported and a 200",
     "https://www.google.com/travel/flights?gl=US&hl=en-US", 200,
     "https://www.google.com/travel/flights/unsupported?gl=US", "soft-404"),
    ("the query lost the one parameter naming the hotel, and both paths are "
     "/searchresults.html, so the paths match while the meaning does not",
     "https://www.booking.com/searchresults.html?label=arinna&highlighted_hotels=6033046",
     200, "https://www.booking.com/searchresults.html?dest_id=-750642;dest_type=city",
     "unclear"),
    ("a redirect to a different domain is a judgement call",
     "https://cosituc.gob.pe/#", 200, "http://cosituccusco.pe/tarifario/", "unclear"),
    ("a real 404", "https://www.guruwalk.com/puebla", 404,
     "https://www.guruwalk.com/puebla", "gone"),
    ("a timeout is NOT a 404: goindigo is up in a browser",
     "https://www.goindigo.in/", 0, "https://www.goindigo.in/", "unreachable"),
    ("403 is a bot defence, not link rot",
     "https://www.alltrails.com/trail/turkey/mugla/lycian-way-fethiye-to-kayakoy", 403,
     "https://www.alltrails.com/trail/turkey/mugla/lycian-way-fethiye-to-kayakoy", "blocked"),
    ("the article pins a booking date from an earlier trip",
     "https://www.hostelworld.com/pwa/hosteldetails.php/Kantar/Yerevan/88646?from=2026-06-21&to=2026-06-22",
     200,
     "https://www.hostelworld.com/pwa/hosteldetails.php/Kantar/Yerevan/88646?from=2026-09-26&to=2026-09-27",
     "stale-date"),

    # ---- benign, must stay silent ------------------------------------------
    ("a bare domain redirecting to its own locale is how the web works",
     "https://www.prioritypass.com/", 200, "https://www.prioritypass.com/en-GB/", "ok"),
    ("adding www to a bare domain is not a finding",
     "https://booking.com", 200, "https://www.booking.com/", "ok"),
    ("unchanged", "https://sallysees.com/santa-ana-volcano-hike/", 200,
     "https://sallysees.com/santa-ana-volcano-hike/", "ok"),
]


# ------------------------------------------------------------ check_links.py

CF_WALL = "<html><head><title>Just a moment...</title></head><body>cf_chl_opt</body></html>"
# A normal page on a Cloudflare/DataDome site carries these strings too.
NORMAL_ON_CF = ("<html><head><title>Lycian Way Guide</title>"
                "<script src='/cdn-cgi/challenge-platform/scripts/x.js'></script>"
                "<script src='https://js.datadome.co/tags.js'></script></head>"
                "<body>" + "lots of real content " * 2000 + "</body></html>")
TITLE = "<html><head><title>%s</title></head><body>x</body></html>"

# (why, kwargs for classify, expected verdict)
VERDICTS = [
    # ---- dead ----------------------------------------------------------------
    ("404", dict(status=404), cl.DEAD),
    ("410", dict(status=410), cl.DEAD),
    ("the domain does not resolve", dict(error="dns"), cl.DEAD),
    ("200, but the title says the page is gone", dict(status=200, body=TITLE % "Page Not Found | Acme"), cl.DEAD),
    ("200, title 404", dict(status=200, body=TITLE % "404 Error"), cl.DEAD),
    ("parked domain",
     dict(status=200, url="https://oldhostel.com/", final="https://www.hugedomains.com/domain_profile.cfm?d=oldhostel.com"),
     cl.DEAD),
    ("parked text", dict(status=200, body=TITLE % "This domain is for sale"), cl.DEAD),
    ("soft 404: the tour is gone, we land on the city page",
     dict(status=200,
          url="https://www.getyourguide.com/kotor-l32202/kotor-blue-cave-tour-t477207/",
          final="https://www.getyourguide.com/kotor-l32202/"), cl.DEAD),

    # ---- unclear: the whole point ---------------------------------------------
    ("403 is a bot wall", dict(status=403), cl.UNCLEAR),
    ("429 is a rate limit", dict(status=429), cl.UNCLEAR),
    ("999 is LinkedIn saying no", dict(status=999), cl.UNCLEAR),
    ("401", dict(status=401), cl.UNCLEAR),
    ("406", dict(status=406), cl.UNCLEAR),
    ("503", dict(status=503), cl.UNCLEAR),
    ("Cloudflare 520", dict(status=520), cl.UNCLEAR),
    ("400 proves nothing", dict(status=400), cl.UNCLEAR),
    ("a timeout is not a 404: goindigo is up in a browser", dict(error="timeout"), cl.UNCLEAR),
    ("a reset", dict(error="reset"), cl.UNCLEAR),
    ("a refused connection", dict(error="refused"), cl.UNCLEAR),
    ("TLS failure", dict(error="tls"), cl.UNCLEAR),
    ("a resolver hiccup is not a dead domain", dict(error="dns-temporary"), cl.UNCLEAR),
    ("Cloudflare challenge answered with 200", dict(status=200, body=CF_WALL), cl.UNCLEAR),
    ("Cloudflare challenge answered with 403", dict(status=403, body=CF_WALL), cl.UNCLEAR),
    ("a wall answering 404 is still a wall", dict(status=404, body=CF_WALL), cl.UNCLEAR),
    ("cf-mitigated header", dict(status=200, headers={"CF-Mitigated": "challenge"}), cl.UNCLEAR),
    ("Expedia's wall", dict(status=200, body=TITLE % "Bot or Not?"), cl.UNCLEAR),
    ("DataDome captcha page",
     dict(status=200, body="<html><script src='https://geo.captcha-delivery.com/x'></script></html>"), cl.UNCLEAR),

    # ---- fine ----------------------------------------------------------------
    ("200", dict(status=200), cl.FINE),
    ("a normal page with Cloudflare and DataDome scripts", dict(status=200, body=NORMAL_ON_CF), cl.FINE),
    ("a real article about security checks",
     dict(status=200, body=TITLE % "Airport Security Check Tips"), cl.FINE),
    ("a real article whose title merely contains a number",
     dict(status=200, body=TITLE % "Route 404 Through the Andes"), cl.FINE),
    ("an ordinary redirect that kept the page",
     dict(status=200, url="https://www.budget-georgia.com/chalaadi-glacier-tour.html",
          final="https://www.budget-georgia.com/tour/chalaadi-glacier-tour/"), cl.FINE),
    ("a redirect to another domain is not dead",
     dict(status=200, url="https://cosituc.gob.pe/#", final="http://cosituccusco.pe/tarifario/"), cl.FINE),
]

ERRORS = [
    ("Windows: host not found", socket.gaierror(11001, "x"), "dns"),
    ("Linux: name not known", socket.gaierror(-2, "x"), "dns"),
    ("Windows: try again", socket.gaierror(11002, "x"), "dns-temporary"),
    ("Linux: try again", socket.gaierror(-3, "x"), "dns-temporary"),
    ("wrapped in URLError", urllib.error.URLError(socket.gaierror(11001, "x")), "dns"),
    ("a timeout", TimeoutError("x"), "timeout"),
    ("refused", urllib.error.URLError(ConnectionRefusedError()), "refused"),
    ("reset", ConnectionResetError(), "reset"),
]

SCOPE = [
    ("index.html", False, True), ("greece/meteora.html", False, True),
    ("archive/index.html", False, False), ("tools/page.html", False, False),
    ("Drafts/Upcoming/x.html", False, False), ("Drafts/Upcoming/x.html", True, True),
    ("editor.html", False, False), (".tmp/x.html", False, False),
]

MAPS = [
    ("https://www.google.com/maps/search/?api=1&query=x", True),
    ("https://maps.google.com/?q=x", True),
    ("https://www.google.co.uk/maps/place/x", True),
    ("https://www.google.com/travel/flights", False),
    ("https://maps.app.goo.gl/abc", False),      # short links can really die
]


class Handler(http.server.BaseHTTPRequestHandler):
    hits = {}

    def log_message(self, *a):
        pass

    def send(self, code, body="", headers=None):
        raw = body.encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def do_HEAD(self):
        self.send(404)                           # GET-only must not be fooled by this

    def do_GET(self):
        Handler.hits[self.path] = Handler.hits.get(self.path, 0) + 1
        n = Handler.hits[self.path]
        if self.path.startswith("/ok"):
            self.send(200, TITLE % "A fine page")
        elif self.path == "/gone":
            self.send(404)
        elif self.path == "/wall":
            self.send(403, CF_WALL)
        elif self.path == "/flaky":
            self.send(503 if n == 1 else 200, TITLE % "Back")
        elif self.path == "/flaky404":
            self.send(404 if n == 1 else 200, TITLE % "Back")
        elif self.path == "/relocated":
            self.send(301, headers={"Location": "/ok/relocated"})
        elif self.path == "/moved-gone":
            self.send(302, headers={"Location": "/gone"})
        elif self.path == "/limited":
            self.send(429, headers={"Retry-After": "0"})
        else:
            self.send(500)


@contextlib.contextmanager
def local_server():
    Handler.hits = {}
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_port
    finally:
        srv.shutdown()
        srv.server_close()


def nosleep(_):
    pass


class Tally:
    def __init__(self):
        self.passed = self.failed = 0

    def check(self, why, ok, detail=""):
        print("  %s %s" % ("PASS" if ok else "FAIL", why[:96]))
        if not ok:
            print("         %s" % detail)
        self.passed, self.failed = self.passed + bool(ok), self.failed + (not ok)


def run_main(base, paths, *flags):
    """Run check_links.main against fake pages pointing at the local server."""
    saved = cl.collect_links, cl.network_up, cl.BACKOFF
    cl.collect_links = lambda **k: {base + p: {"x.html": [("text", 1)]} for p in paths}
    cl.network_up = lambda: True
    cl.BACKOFF = (0, 0)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = cl.main(["--delay", "0"] + list(flags))
    finally:
        cl.collect_links, cl.network_up, cl.BACKOFF = saved
    return code, out.getvalue()


def test_check_links(t):
    print("\n  check_links.py: verdicts")
    for why, kw, want in VERDICTS:
        got, reason = cl.classify(**kw)
        t.check(why, got == want, "wanted %r, got %r (%s)" % (want, got, reason))

    print("\n  check_links.py: network errors")
    for why, exc, want in ERRORS:
        got = cl.error_kind(exc)
        t.check(why, got == want, "wanted %r, got %r" % (want, got))

    print("\n  check_links.py: scope and parsing")
    for rel, drafts, want in SCOPE:
        t.check("%s drafts=%s is live=%s" % (rel, drafts, want), cl.is_live_page(rel, drafts) == want)
    for url, want in MAPS:
        t.check("maps=%s %s" % (want, url), cl.is_maps(url) == want)
    p = cl.AnchorParser()
    p.feed('<a href="https://x.com/?a=1&amp;b=2">The  <b>Hostel</b></a><!-- <a href="https://c.com">no</a> -->'
           '<script>var s=\'<a href="https://d.com">no</a>\';</script><a href="https://y.com"><img alt="Photo"></a>')
    p.close()
    t.check("anchor text, &amp; decoded, comments and scripts ignored",
            [(h, x) for h, x, _ in p.links] == [("https://x.com/?a=1&b=2", "The Hostel"), ("https://y.com", "Photo")],
            repr(p.links))
    with tempfile.TemporaryDirectory() as tmp:           # a scratch tree, never the real one
        os.makedirs(os.path.join(tmp, "greece"))
        os.makedirs(os.path.join(tmp, "archive"))
        page = ('<a href="https://x.com/a#one">first</a><a href="https://x.com/a#two">second</a>'
                '<a href="https://www.google.com/maps/search/?api=1&amp;query=z">map</a>'
                '<a href="https://getawayguide.io/about">own</a><a href="/local">local</a>')
        for rel in ("greece/a.html", "archive/old.html"):
            with open(os.path.join(tmp, rel.replace("/", os.sep)), "w", encoding="utf-8") as f:
                f.write(page)
        found = cl.collect_links(root=tmp)
        t.check("collects live pages, one entry per URL without its fragment, no maps or own site",
                set(found) == {"https://x.com/a"} and set(found["https://x.com/a"]) == {"greece/a.html"}
                and len(found["https://x.com/a"]["greece/a.html"]) == 2, repr(dict(found)))
        found = cl.collect_links(root=tmp, maps=True)
        t.check("--maps adds the maps link", len(found) == 2, repr(dict(found)))
    t.check("to_ascii handles spaces, accents and IDN hosts",
            cl.to_ascii("https://café.example/a b?q=é#x") ==
            "https://xn--caf-dma.example/a%20b?q=%C3%A9", cl.to_ascii("https://café.example/a b?q=é#x"))

    print("\n  check_links.py: retries and politeness")
    calls = []
    def wall(url, timeout):
        calls.append(url)
        return {"status": 403, "final": url, "error": None, "body": "", "headers": {}, "retry_after": None}
    urls = ["https://walled.example/%d" % i for i in range(12)]
    rs = cl.check_host(urls, fetcher=wall, sleep=nosleep, delay=0)
    t.check("a host that refuses five in a row is left alone (no more requests)",
            len(calls) == cl.HOST_BREAKER and len(rs) == 12 and all(r["verdict"] == cl.UNCLEAR for r in rs),
            "%d calls" % len(calls))
    slept = []
    calls.clear()
    def retry_after(url, timeout):
        calls.append(url)
        return {"status": 429, "final": url, "error": None, "body": "", "headers": {}, "retry_after": 25}
    r = cl.check_url("https://x.example/", fetcher=retry_after, sleep=slept.append)
    t.check("429 honors Retry-After and gives up as unclear",
            r["verdict"] == cl.UNCLEAR and slept and all(s == 25 for s in slept) and len(calls) == cl.ATTEMPTS,
            repr(slept))
    slept.clear()
    def huge_retry(url, timeout):
        return {"status": 429, "final": url, "error": None, "body": "", "headers": {}, "retry_after": 3600}
    cl.check_url("https://x.example/", fetcher=huge_retry, sleep=slept.append)
    t.check("a one-hour Retry-After is capped", slept and max(slept) == cl.MAX_RETRY_AFTER, repr(slept))
    seen = []
    cl.check_all(["https://a.example/1", "https://a.example/2", "https://b.example/1"],
                 fetcher=lambda u, t: (seen.append(u), {"status": 200, "final": u, "error": None, "body": "",
                                                        "headers": {}, "retry_after": None})[1],
                 delay=0, deadline_min=0)
    t.check("every URL requested exactly once", sorted(seen) ==
            ["https://a.example/1", "https://a.example/2", "https://b.example/1"], repr(seen))
    calls.clear()
    rs = cl.check_host(["https://x.example/1"], fetcher=wall, sleep=nosleep, delay=0, deadline=0)
    t.check("past the deadline: no request, unclear", not calls and rs[0]["verdict"] == cl.UNCLEAR)

    print("\n  check_links.py: browser retry")
    def br(status=None, body="", final="https://x.example/a", error=None):
        return {"status": status, "error": error, "body": body, "final": final, "headers": {}}
    r = cl._not_requested("https://x.example/a", "HTTP 403")
    cl._apply_browser(r, br(200, TITLE % "Tour"))
    t.check("browser loads a 403'd page: fine", r["verdict"] == cl.FINE, repr(r))
    r = dict(cl._not_requested("https://x.example/a", "HTTP 404"), verdict=cl.DEAD, status=404)
    cl._apply_browser(r, br(200, TITLE % "Tour"))
    t.check("browser loads a 404'd page: fine (the 404 was for bots)", r["verdict"] == cl.FINE, repr(r))
    r = cl._not_requested("https://x.example/a", "HTTP 403")
    cl._apply_browser(r, br(404))
    t.check("browser gets 404: dead", r["verdict"] == cl.DEAD, repr(r))
    r = cl._not_requested("https://x.example/a", "HTTP 403")
    cl._apply_browser(r, br(200, CF_WALL))
    t.check("browser hits the wall too: unclear, and it counts as a refusal",
            r["verdict"] == cl.UNCLEAR and cl.is_refusal(200, r["browser_reason"]), repr(r))
    r = dict(cl._not_requested("https://x.example/a", "HTTP 404"), verdict=cl.DEAD, status=404)
    cl._apply_browser(r, br(error="timeout"))
    t.check("a browser timeout does not overturn the first answer", r["verdict"] == cl.DEAD, repr(r))

    for msg, want in [("net::ERR_NAME_NOT_RESOLVED at https://x", "dns"),
                      ("net::ERR_CONNECTION_TIMED_OUT at https://x", "timeout"),
                      ("Timeout 30000ms exceeded.", "timeout"),
                      ("net::ERR_CONNECTION_RESET at https://x", "reset"),
                      ("net::ERR_SSL_PROTOCOL_ERROR at https://x", "tls"),
                      ("net::ERR_CONNECTION_REFUSED at https://x", "refused"),
                      ("net::ERR_ABORTED at https://x", "other")]:
        t.check("browser error %s" % msg[:40], cl.browser_error_kind(msg) == want)

    print("\n  check_links.py: end to end against a local server")
    cl.BACKOFF, saved = (0, 0), cl.BACKOFF
    with local_server() as base:
        expect = [("/ok", cl.FINE), ("/gone", cl.DEAD), ("/wall", cl.UNCLEAR), ("/relocated", cl.FINE),
                  ("/moved-gone", cl.DEAD), ("/flaky", cl.FINE), ("/flaky404", cl.FINE), ("/limited", cl.UNCLEAR)]
        for path, want in expect:
            r = cl.check_url(base + path, sleep=nosleep, timeout=5)
            t.check("%s is %s" % (path, want), r["verdict"] == want, repr(r))
        t.check("a dead link is confirmed before it is called dead (asked twice, each way in)",
                Handler.hits.get("/gone", 0) == 4 and Handler.hits.get("/moved-gone", 0) == 2,
                repr(Handler.hits))
        t.check("the wall was not retried", Handler.hits.get("/wall") == 1, repr(Handler.hits))
    cl.BACKOFF = saved

    with local_server() as base:
        code, out = run_main(base, ["/ok", "/wall"])
        t.check("unclear links alone exit 0", code == 0, out)
        t.check('the summary line the workflow greps: "  N dead / M checked"',
                re.search(r"(?m)^ +0 dead / 2 checked", out), out)
        code, out = run_main(base, ["/ok", "/gone"])
        t.check("a dead link exits 1", code == 1 and re.search(r"(?m)^ +1 dead", out), out)
        code, out = run_main(base, ["/gone"], "--report-only")
        t.check("--report-only exits 0 despite a dead link", code == 0 and "1 dead" in out, out)
    saved_up = cl.network_up
    cl.network_up = lambda: False
    saved_collect = cl.collect_links
    cl.collect_links = lambda **k: {"https://x.example/": {"x.html": [("t", 1)]}}
    with contextlib.redirect_stdout(io.StringIO()):
        code = cl.main([])
    cl.network_up, cl.collect_links = saved_up, saved_collect
    t.check("no network: exit 2, no verdicts", code == 2)


def main():
    passed = failed = 0
    for why, url, status, final, want in CASES:
        got, detail = fl.classify(url, status, final, "")
        ok = got == want
        # the assertion that actually protects the articles
        if want not in FIX and got in FIX:
            ok = False
            why += "  <-- WOULD HAVE BEEN REWRITTEN"
        print("  %s %-9s %s" % ("PASS" if ok else "FAIL", got, why[:96]))
        if not ok:
            print("         wanted %r, got %r for %s" % (want, got, url[:90]))
        passed, failed = passed + ok, failed + (not ok)

    # nothing outside the two writable kinds may ever reach the rewriter
    writable = {k for _, _, _, _, k in CASES if k in FIX}
    print("\n  writable kinds seen: %s" % ", ".join(sorted(writable)))

    t = Tally()
    test_check_links(t)
    passed, failed = passed + t.passed, failed + t.failed

    print("=" * 70)
    print("  %d checks, %d failed" % (passed + failed, failed))
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
