#!/usr/bin/env python3
"""
check_links.py -- find dead external links on the live pages (link-rot checker).

The old version reported every 4xx/5xx/timeout as "dead", and about 103 of its
106 "dead" links turned out to be bot walls (GetYourGuide, AllTrails, Viator,
Expedia, TripAdvisor...). So every result now lands in one of three buckets:

  dead      404/410, a domain that no longer resolves, or a page that is gone
            while still answering 200: a soft 404 (redirected to its own parent
            page, per fix_links.classify), a "Page not found" title, or a parked
            domain for sale.
  unclear   we could not tell: 401/403/406/429/999, 5xx, Cloudflare and other
            challenge pages, timeouts, resets, refused connections, TLS failures.
            Most of these are fine in a browser.
  fine      everything that loaded.

ONLY DEAD IS A FAILURE. Exit code 1 means at least one dead link, 0 means none
(unclear never fails the run), 2 means the checker had no network and has no
opinion at all. Whatever else changes, keep the summary line "  N dead / M checked":
.github/workflows/link-rot.yml greps it to title the issue.

Polite on purpose: GET only (plenty of servers answer HEAD with a false 404),
one request at a time per host with a delay between them, and a host that
refuses five in a row is left alone for the rest of the run. Stdlib only, so the
GitHub workflow has nothing to install; --browser needs Playwright, and is meant
for Kevin's PC, not the runner.

Network note: the Bash tool here has no internet. Run this from PowerShell.

Usage:
  python tools/check_links.py                 # all rot-prone external links
  python tools/check_links.py --maps          # also google.com/maps links
  python tools/check_links.py --max 50        # cap the URL count (quick sample)
  python tools/check_links.py --drafts        # include Drafts/ pages
  python tools/check_links.py --dry-run       # count what would be checked; no requests
  python tools/check_links.py --browser       # retry unclear links in headless Chromium
  python tools/check_links.py --report-only   # never exit non-zero for dead links
  python tools/check_links.py --list-unclear  # print every unclear link, not just per host
  python tools/check_links.py --json F        # full results (with anchor text) to file F
"""
import argparse
import glob
import http.client
import json
import os
import random
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse as up
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fix_links  # the soft-404 rules live there, with their own test table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEAD, UNCLEAR, FINE = "dead", "unclear", "fine"

# Our own site is the lint's job, and the rest are not link rot.
SKIP_HOSTS = ("flagcdn.com", "fonts.googleapis.com", "fonts.gstatic.com",
              "schemas.sitemaps.org", "www.w3.org", "getawayguide.io")
# Top-level folders that are not published articles. Everything else is the
# repo root or a country folder.
NOT_LIVE = {"archive", "tools", "Drafts", "Images", "assets", "fonts", "_content",
            "workflows", "node_modules"}
NOT_LIVE_FILES = {"editor.html"}          # the editor's own page, not an article

# A plain desktop browser. Never put a name or an email address in here.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
CTX = ssl.create_default_context()
CTX.check_hostname = False          # a lapsed certificate is not a dead link
CTX.verify_mode = ssl.CERT_NONE

BODY_LIMIT = 96 * 1024              # enough for <head>; never download the page
TIMEOUT = 20
ATTEMPTS = 3
BACKOFF = (3, 8)                    # seconds before attempt 2 and 3
MAX_RETRY_AFTER = 30
HOST_DELAY = 1.2                    # seconds between requests to one host
HOST_BREAKER = 5                    # consecutive refusals before leaving a host alone
BROWSER_BREAKER = 3
# Seconds between browser pages, for hosts that only let a browser through slowly.
# GetYourGuide 403s after two or three quick pages; at 20s with a fresh session per
# page, all 38 links loaded (2026-11-10).
BROWSER_PACE = {"www.getyourguide.com": 20}
DEADLINE_MIN = 16                   # inside the workflow's 20-minute timeout

GONE_STATUS = (404, 410)
REFUSED_STATUS = (401, 403, 406, 429, 999)

# gaierror errno: the name definitely does not exist (Windows, then Linux/glibc).
# "Try again" (11002, -3) means the resolver hiccuped, which proves nothing.
DNS_GONE = (11001, 11004, -2, -5)

ERROR_TEXT = {
    "dns-temporary": "DNS lookup failed temporarily",
    "timeout": "timed out",
    "reset": "connection reset or dropped",
    "refused": "connection refused",
    "tls": "TLS handshake failed",
    "bad-url": "malformed URL",
    "other": "request failed",
}

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# Anchored to the START of the title, so a real article called "Airport Security
# Check Tips" stays fine while "Security check" the wall does not.
CHALLENGE_TITLE = re.compile(
    r"^\W*(just a moment|attention required|checking your browser|one more step|"
    r"please wait|verify(ing)? (that )?you are (a )?human|human verification|"
    r"are you (a )?(robot|human)|bot or not|pardon our interruption|access denied|"
    r"security check|request blocked|you have been blocked|ddos protection|"
    r"captcha)", re.I)

# Only strings that appear on the wall itself. NOT "challenge-platform" or a bare
# "datadome": Cloudflare and DataDome put those scripts on perfectly normal pages.
CHALLENGE_BODY = ("cf_chl_opt", "cf-browser-verification", "enable javascript and cookies to continue")
# These do show up on real pages, so they only count on a tiny wall-sized one.
CHALLENGE_BODY_SMALL = ("geo.captcha-delivery.com", "_incapsula_resource", "px-captcha",
                        "perimeterx")
SMALL_PAGE = 20000

NOT_FOUND_TITLE = re.compile(
    r"(^\W*404\b|\b404\W+(error|not found|page)|\b(error|page)\W+404\b|"
    r"page (was |is )?not found|page (cannot|can.t|could not|couldn.t) be found|"
    r"page (does not|doesn.t) exist|(is )?no longer available|\bnot found\W*$)", re.I)

PARKED_HOSTS = ("hugedomains.com", "sedo.com", "dan.com", "afternic.com", "bodis.com")
PARKED_TEXT = re.compile(r"(this|the) domain (name )?(is|may be) (for sale|parked)|"
                         r"buy this domain", re.I)


# ----------------------------------------------------------------- classifying

def page_title(body):
    m = TITLE_RE.search(body or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def challenge_of(body, headers):
    """Name the bot wall this response is, or None. Applies to any status."""
    if (headers or {}).get("cf-mitigated", "").lower() == "challenge":
        return "Cloudflare challenge"
    body = body or ""
    title = page_title(body)
    if title and CHALLENGE_TITLE.search(title):
        return title[:60]
    low = body.lower()
    if any(s in low for s in CHALLENGE_BODY):
        return "challenge page"
    if len(body) < SMALL_PAGE and any(s in low for s in CHALLENGE_BODY_SMALL):
        return "challenge page"
    return None


def classify(status=None, error=None, body="", headers=None, url="", final=""):
    """Return (verdict, reason). Pure, so every rule can be pinned in a test.

    status  the HTTP status, or None if there was no response
    error   None, or a kind from error_kind()
    """
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    if error == "dns":
        return DEAD, "the domain no longer resolves (DNS)"
    if error:
        return UNCLEAR, ERROR_TEXT.get(error, error)
    if status is None:
        return UNCLEAR, "no response"

    wall = challenge_of(body, headers)
    if wall:
        return UNCLEAR, "bot challenge: %s" % wall
    if status in GONE_STATUS:
        return DEAD, "HTTP %d" % status
    if status >= 400:
        if status in REFUSED_STATUS:
            return UNCLEAR, "HTTP %d (refuses bots or rate limited)" % status
        if status >= 500:
            return UNCLEAR, "HTTP %d (server error)" % status
        return UNCLEAR, "HTTP %d" % status
    if status >= 300:
        return UNCLEAR, "HTTP %d (redirect not followed)" % status

    # 2xx from here on: it loaded, but a page can be gone and still say 200.
    title = page_title(body)
    host = up.urlsplit(final or url).netloc.lower()
    if any(host == h or host.endswith("." + h) for h in PARKED_HOSTS):
        return DEAD, "gone: the domain is parked at %s" % host
    if PARKED_TEXT.search(title) or (len(body or "") < SMALL_PAGE and PARKED_TEXT.search(body or "")):
        return DEAD, "gone: the domain is parked or for sale"
    if title and NOT_FOUND_TITLE.search(title):
        return DEAD, 'gone: the page title says "%s"' % title[:70]
    if url and final:
        kind, detail = fix_links.classify(url, status, final, "")
        if kind == "soft-404":
            return DEAD, "gone (soft 404): %s" % detail
    return FINE, ""


def is_refusal(status, reason):
    """True when the answer means "go away", which is what trips the host breaker."""
    return status in REFUSED_STATUS or reason.startswith("bot challenge")


def error_kind(exc):
    """Reduce a network exception to one of the kinds classify() understands."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        return "dns" if reason.errno in DNS_GONE else "dns-temporary"
    if isinstance(reason, TimeoutError):
        return "timeout"
    if isinstance(reason, ssl.SSLError):
        return "timeout" if "timed out" in str(reason).lower() else "tls"
    if isinstance(reason, ConnectionRefusedError):
        return "refused"
    if isinstance(reason, (ConnectionError, http.client.HTTPException)):
        if isinstance(reason, http.client.InvalidURL):
            return "bad-url"
        return "reset"
    if isinstance(reason, (UnicodeError, ValueError)):
        return "bad-url"
    return "other"


def retry_wanted(result, verdict, attempt):
    """Should we ask again? Walls never change their mind, so 403s are not retried."""
    if result["error"] in ("timeout", "reset", "refused", "tls", "other", "dns-temporary"):
        return attempt < ATTEMPTS - 1
    status = result["status"]
    if status is not None and (status >= 500 or status == 429):
        return attempt < ATTEMPTS - 1
    # Confirm a dead verdict once: flaky CDNs do hand out the odd 404.
    if verdict == DEAD and (status in GONE_STATUS or result["error"] == "dns"):
        return attempt < 1
    return False


# -------------------------------------------------------------------- fetching

def to_ascii(url):
    """Make an href from a page safe for urllib: spaces, accents, IDN hosts."""
    s = up.urlsplit(url.strip())
    host = s.hostname.encode("idna").decode("ascii") if s.hostname else ""
    if s.port:
        host += ":%d" % s.port
    return up.urlunsplit((s.scheme, host,
                          up.quote(s.path, safe="/%:@!$&'()*+,;=~-._"),
                          up.quote(s.query, safe="=&%/:+,;@!$'()*?~-._"), ""))


def fetch(url, timeout=TIMEOUT):
    """One GET. Returns {status, final, error, body, headers, retry_after}."""
    out = {"status": None, "final": url, "error": None, "body": "", "headers": {},
           "retry_after": None}
    def read(resp):
        out["status"] = resp.status if hasattr(resp, "status") else resp.getcode()
        out["final"] = resp.geturl() or url
        out["headers"] = {k.lower(): v for k, v in resp.headers.items()}
        ra = out["headers"].get("retry-after", "")
        out["retry_after"] = int(ra) if ra.isdigit() else None
        try:
            out["body"] = resp.read(BODY_LIMIT).decode("utf-8", "ignore")
        except Exception:
            pass                 # the status is what matters; a short body is fine
    try:
        req = urllib.request.Request(to_ascii(url), headers=HEADERS)
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=CTX),
            urllib.request.HTTPCookieProcessor())   # cookie-less loops are a false alarm
        with opener.open(req, timeout=timeout) as resp:
            read(resp)
    except urllib.error.HTTPError as e:
        try:
            read(e)
        finally:
            e.close()
    except Exception as e:
        out["error"] = error_kind(e)
    return out


def check_url(url, fetcher=fetch, sleep=time.sleep, timeout=TIMEOUT):
    """Fetch with retries and return the settled result."""
    for attempt in range(ATTEMPTS):
        res = fetcher(url, timeout)
        verdict, reason = classify(res["status"], res["error"], res["body"], res["headers"],
                                   url=url, final=res["final"])
        if not retry_wanted(res, verdict, attempt):
            break
        wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
        if res.get("retry_after") is not None:
            wait = min(max(wait, res["retry_after"]), MAX_RETRY_AFTER)
        sleep(wait)
    return {"url": url, "verdict": verdict, "reason": reason, "status": res["status"],
            "error": res["error"], "final": res["final"], "attempts": attempt + 1,
            "requested": True, "browser": ""}


def host_of(url):
    return (up.urlsplit(url).hostname or "").lower()


def check_host(urls, fetcher=fetch, sleep=time.sleep, delay=HOST_DELAY, deadline=None):
    """Check one host's URLs one at a time, backing off if it refuses."""
    results, refused, gave_up = [], 0, False
    for i, url in enumerate(urls):
        if deadline is not None and time.monotonic() > deadline:
            results.append(_not_requested(url, "not checked: ran out of time"))
            continue
        if gave_up:
            results.append(_not_requested(url, "not requested: this host refused %d in a row"
                                          % HOST_BREAKER))
            continue
        if i:
            sleep(delay + (random.uniform(0, delay / 2) if delay else 0))
        r = check_url(url, fetcher=fetcher, sleep=sleep)
        results.append(r)
        refused = refused + 1 if is_refusal(r["status"], r["reason"]) else 0
        gave_up = refused >= HOST_BREAKER
    return results


def _not_requested(url, reason):
    return {"url": url, "verdict": UNCLEAR, "reason": reason, "status": None, "error": None,
            "final": url, "attempts": 0, "requested": False, "browser": ""}


def check_all(urls, workers=10, fetcher=fetch, delay=HOST_DELAY, deadline_min=DEADLINE_MIN,
              progress=None):
    """Hosts run in parallel; each host is strictly one request at a time."""
    by_host = defaultdict(list)
    for u in urls:
        by_host[host_of(u)].append(u)
    deadline = time.monotonic() + deadline_min * 60 if deadline_min else None
    done, lock, results = [0], threading.Lock(), []

    def one(host_urls):
        rs = check_host(host_urls, fetcher=fetcher, delay=delay, deadline=deadline)
        with lock:
            results.extend(rs)
            done[0] += len(rs)
            if progress:
                progress(done[0], len(urls))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, by_host.values()))
    return results


def network_up():
    """If the big names do not resolve, every link would look dead."""
    for host in ("example.com", "www.google.com", "www.wikipedia.org"):
        try:
            socket.getaddrinfo(host, 443)
            return True
        except OSError:
            continue
    return False


# -------------------------------------------------------- the headless retry

def browser_candidates(results):
    """Unclear links, plus HTTP-dead ones. A DNS failure needs no second opinion."""
    return [r for r in results
            if (r["verdict"] == UNCLEAR and r["error"] != "bad-url")
            or (r["verdict"] == DEAD and r["status"] in GONE_STATUS)]


def browser_pass(results, delay=1.5, log=print):
    """Retry the candidates in headless Chromium. Updates results in place.

    GetYourGuide (and some others) pass a real browser, GetYourGuide only when
    paced (BROWSER_PACE); AllTrails, TripAdvisor, Viator and Expedia do not. Three
    refusals in a row and we stop trying that host, so the walled ones cost three
    page loads and not forty.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("Playwright is not installed; skipping the browser retry.")
        return False

    by_host = defaultdict(list)
    for r in browser_candidates(results):
        by_host[host_of(r["url"])].append(r)

    def new_context(browser):
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1366, "height": 850},
                                  ignore_https_errors=True)
        ctx.route("**/*", lambda route: route.abort()
                  if route.request.resource_type in ("image", "media", "font")
                  else route.continue_())
        return ctx

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for host in sorted(by_host):
            gap = BROWSER_PACE.get(host)
            ctx = new_context(browser)
            refused = 0
            for i, r in enumerate(by_host[host]):
                if refused >= BROWSER_BREAKER:
                    r["browser"] = "not tried: browser refused %d in a row here" % BROWSER_BREAKER
                    continue
                if i:
                    time.sleep(gap or delay)
                    if gap:                  # a paced host also wants a clean session
                        ctx.close()
                        ctx = new_context(browser)
                outcome = _browser_load(ctx, r["url"])
                _apply_browser(r, outcome)
                refused = refused + 1 if is_refusal(outcome["status"], r["browser_reason"]) else 0
                log("  browser %-7s %s" % (r["verdict"], r["url"][:90]))
            ctx.close()
        browser.close()
    return True


def browser_error_kind(msg):
    """Reduce a Chromium net::ERR_ message to the kinds classify() understands."""
    if "ERR_NAME_NOT_RESOLVED" in msg:
        return "dns"
    if "Timeout" in msg or "TIMED_OUT" in msg:
        return "timeout"
    if "ERR_CONNECTION_REFUSED" in msg:
        return "refused"
    if "ERR_SSL" in msg or "ERR_CERT" in msg:
        return "tls"
    if any(s in msg for s in ("ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED",
                              "ERR_EMPTY_RESPONSE", "ERR_HTTP2_PROTOCOL_ERROR")):
        return "reset"
    return "other"


def _browser_load(ctx, url):
    out = {"status": None, "error": None, "body": "", "final": url, "headers": {}}
    page = ctx.new_page()
    last = {}
    def on_response(resp):
        try:
            if resp.request.is_navigation_request() and resp.frame == page.main_frame:
                last["status"] = resp.status
                last["headers"] = resp.headers
        except Exception:
            pass
    page.on("response", on_response)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        # Challenge pages clear themselves in a few seconds if they are going to.
        if challenge_of(page.content(), last.get("headers")):
            page.wait_for_timeout(7000)
        out["status"], out["headers"] = last.get("status"), last.get("headers", {})
        out["body"], out["final"] = page.content()[:BODY_LIMIT], page.url
    except Exception as e:
        out["error"] = browser_error_kind(str(e))
    finally:
        page.close()
    return out


def _apply_browser(r, outcome):
    """Overwrite the verdict only when the browser actually got an answer."""
    verdict, reason = classify(outcome["status"], outcome["error"], outcome["body"],
                               outcome["headers"], url=r["url"], final=outcome["final"])
    r["browser_reason"] = reason
    r["browser"] = (verdict if verdict != UNCLEAR else "also refused: %s" % reason)
    if outcome["error"] and verdict == UNCLEAR:
        return                       # a browser timeout tells us nothing new
    r["verdict"], r["reason"] = verdict, reason
    r["status"], r["error"], r["final"] = outcome["status"], outcome["error"], outcome["final"]


# ------------------------------------------------------------------ collecting

class AnchorParser(HTMLParser):
    """Every <a href> with its text. Scripts and comments never reach handlers."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self._open = [], None

    def _close(self):
        if self._open:
            text = re.sub(r"\s+", " ", "".join(self._open["text"])).strip()
            self.links.append((self._open["href"], text, self._open["line"]))
        self._open = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._close()
            href = dict(attrs).get("href")
            self._open = {"href": href, "text": [], "line": self.getpos()[0]} if href else None
        elif tag == "img" and self._open:
            self._open["text"].append(dict(attrs).get("alt") or "[image]")

    def handle_data(self, data):
        if self._open:
            self._open["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self._close()

    def close(self):
        super().close()
        self._close()


def is_live_page(rel, drafts=False):
    parts = rel.split("/")
    if rel in NOT_LIVE_FILES or parts[0].startswith("."):
        return False
    if parts[0] == "Drafts":
        return drafts
    return len(parts) == 1 or parts[0] not in NOT_LIVE


def is_maps(url):
    s = up.urlsplit(url)
    host = (s.hostname or "").lower()
    if host.startswith("maps.google."):
        return True
    return bool(re.match(r"(www\.)?google\.[a-z.]+$", host)) and s.path.startswith("/maps")


def is_skipped(url):
    host = (up.urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in SKIP_HOSTS)


def strip_fragment(url):
    return up.urldefrag(url)[0]


def collect_links(root=ROOT, drafts=False, maps=False):
    """{url (no fragment): {page: [(anchor text, line), ...]}}"""
    found = defaultdict(lambda: defaultdict(list))
    for path in sorted(glob.glob(os.path.join(root, "**", "*.html"), recursive=True)):
        rel = os.path.relpath(path, root).replace("\\", "/")
        if not is_live_page(rel, drafts):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            parser = AnchorParser()
            parser.feed(f.read())
            parser.close()
        for href, text, line in parser.links:
            href = href.strip()
            if href.startswith("//"):
                href = "https:" + href
            if not re.match(r"https?://", href, re.I):
                continue
            if is_skipped(href) or (is_maps(href) and not maps):
                continue
            found[strip_fragment(href)][rel].append((text or "[no text]", line))
    return found


# ------------------------------------------------------------------- reporting

def print_report(results, found, list_unclear=False):
    buckets = defaultdict(list)
    for r in results:
        buckets[r["verdict"]].append(r)
    dead, unclear = buckets[DEAD], buckets[UNCLEAR]

    print("=" * 60)
    print("  %d dead / %d checked" % (len(dead), len(results)))
    print("  %d unclear (not failures), %d fine" % (len(unclear), len(buckets[FINE])))
    print("=" * 60)

    if dead:
        print("\nDEAD -- fix or remove these:")
    for r in sorted(dead, key=lambda x: x["url"]):
        print("\n  [%s] %s" % (r["reason"], r["url"]))
        for page in sorted(found[r["url"]])[:5]:
            text, line = found[r["url"]][page][0]
            print('        on %s (line %d): "%s"' % (page, line, text[:70]))
        extra = len(found[r["url"]]) - 5
        if extra > 0:
            print("        and %d more page(s)" % extra)
    if not dead:
        print("\n  No dead links.")

    if unclear:
        print("\nUNCLEAR -- could not verify, not counted as failures (per host):")
        for host, n in Counter(host_of(r["url"]) for r in unclear).most_common():
            reasons = Counter(r["reason"] for r in unclear if host_of(r["url"]) == host)
            print("  %4d  %-34s %s" % (n, host, reasons.most_common(1)[0][0]))
    if list_unclear:
        for r in sorted(unclear, key=lambda x: x["url"]):
            print("  [%s] %s" % (r["reason"], r["url"]))


def write_json(path, results, found):
    rows = []
    for r in sorted(results, key=lambda x: x["url"]):
        row = dict(r)
        row["pages"] = {p: [{"text": t, "line": n} for t, n in anchors]
                        for p, anchors in sorted(found.get(r["url"], {}).items())}
        rows.append(row)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1, ensure_ascii=False)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")   # cp1252 here otherwise

    ap = argparse.ArgumentParser(description="Report dead external links on the live pages.")
    ap.add_argument("--maps", action="store_true", help="also check google.com/maps links")
    ap.add_argument("--max", type=int, help="cap the number of URLs")
    ap.add_argument("--drafts", action="store_true", help="include Drafts/ pages")
    ap.add_argument("--dry-run", action="store_true", help="count what would be checked; no requests")
    ap.add_argument("--browser", action="store_true", help="retry unclear links in headless Chromium")
    ap.add_argument("--report-only", action="store_true", help="exit 0 even if links are dead")
    ap.add_argument("--list-unclear", action="store_true", help="print every unclear link")
    ap.add_argument("--json", metavar="FILE", help="write full results, with anchor text")
    ap.add_argument("--workers", type=int, default=10, help="hosts checked at once")
    ap.add_argument("--delay", type=float, default=HOST_DELAY, help="seconds between requests to one host")
    ap.add_argument("--deadline", type=float, default=DEADLINE_MIN,
                    help="stop requesting after this many minutes (0 for no limit)")
    args = ap.parse_args(argv)

    found = collect_links(drafts=args.drafts, maps=args.maps)
    urls = sorted(found)
    if args.max:
        urls = urls[:args.max]
    pages = {p for u in urls for p in found[u]}

    if args.dry_run:
        print("Would check %d unique external URLs (from %d pages)." % (len(urls), len(pages)))
        for host, n in Counter(host_of(u) for u in urls).most_common(25):
            print("  %4d  %s" % (n, host))
        return 0

    print("Checking %d unique external URLs (from %d pages)...\n" % (len(urls), len(pages)), flush=True)
    if not network_up():
        print("No network: even the canary hosts do not resolve. No verdicts given.")
        return 2

    shown = [0]

    def progress(done, total):
        if done - shown[0] >= 50 or done == total:
            shown[0] = done
            print("  ...%d/%d" % (done, total), flush=True)

    results = check_all(urls, workers=args.workers, delay=args.delay,
                        deadline_min=args.deadline, progress=progress)

    if args.browser and browser_candidates(results):
        print("\nRetrying %d unclear/dead link(s) in headless Chromium..."
              % len(browser_candidates(results)), flush=True)
        browser_pass(results)

    # A DNS outage mid-run would otherwise bury the real results under false "dead".
    if any(r["error"] == "dns" and r["verdict"] == DEAD for r in results) and not network_up():
        for r in results:
            if r["error"] == "dns" and r["verdict"] == DEAD:
                r["verdict"], r["reason"] = UNCLEAR, "DNS failed while the network was down"

    print_report(results, found, list_unclear=args.list_unclear)
    if args.json:
        write_json(args.json, results, found)
    dead = sum(1 for r in results if r["verdict"] == DEAD)
    return 1 if dead and not args.report_only else 0


if __name__ == "__main__":
    sys.exit(main())
