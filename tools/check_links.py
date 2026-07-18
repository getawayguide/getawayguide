#!/usr/bin/env python3
"""
check_links.py — find dead external links across live pages (link-rot checker).

Collects unique external URLs (hostel/booking/operator/etc.), requests each
concurrently, and reports the ones that are dead (4xx/5xx) or unreachable.
By default it skips google.com/maps query links (huge volume, effectively never
rot) and flag/font CDNs.

Usage:
  python tools/check_links.py                # check all rot-prone external links
  python tools/check_links.py --maps         # also check google.com/maps links
  python tools/check_links.py --max 50       # cap number of URLs (quick sample)
  python tools/check_links.py --drafts       # include Drafts/ pages
Exit code is non-zero if any dead links are found.
"""
import os, re, glob, sys, urllib.request, urllib.error, ssl
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAFTS = "--drafts" in sys.argv
MAPS = "--maps" in sys.argv
MAX = next((int(a.split("=")[1]) if "=" in a else int(sys.argv[sys.argv.index(a)+1])
            for a in sys.argv if a.startswith("--max")), None)
SKIP_HOSTS = ("flagcdn.com", "fonts.googleapis.com", "fonts.gstatic.com", "schemas.sitemaps.org")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = "Mozilla/5.0 (compatible; getawayguide-linkcheck/1.0)"

def rel(p): return os.path.relpath(p, ROOT).replace("\\", "/")

url_pages = defaultdict(set)   # url -> set of pages
for p in glob.glob(os.path.join(ROOT, "**", "*.html"), recursive=True):
    r = rel(p)
    if r.startswith((".tmp/", ".git/")): continue
    if r.startswith("Drafts/") and not DRAFTS: continue
    html = open(p, encoding="utf-8").read()
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        u = m.group(1).replace("&amp;", "&")
        host = re.sub(r"^https?://([^/]+).*", r"\1", u)
        if any(h in host for h in SKIP_HOSTS): continue
        if not MAPS and "google.com/maps" in u: continue
        url_pages[u].add(r)

urls = sorted(url_pages)
if MAX: urls = urls[:MAX]
print("Checking %d unique external URLs (from %d pages)...\n" % (len(urls), len({p for ps in url_pages.values() for p in ps})))

def check(u):
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(u, method=method, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=12, context=CTX) as resp:
                return u, resp.status
        except urllib.error.HTTPError as e:
            if e.code in (403, 405, 429) and method == "HEAD":
                continue   # some servers reject HEAD; retry GET
            return u, e.code
        except Exception as e:
            if method == "HEAD": continue
            return u, "ERR: %s" % type(e).__name__
    return u, "ERR"

dead = []
with ThreadPoolExecutor(max_workers=16) as ex:
    for u, status in ex.map(check, urls):
        ok = isinstance(status, int) and status < 400
        if not ok:
            dead.append((u, status))

print("=" * 60)
print("  %d dead / %d checked" % (len(dead), len(urls)))
print("=" * 60)
for u, status in sorted(dead, key=lambda x: str(x[1])):
    print("\n  [%s] %s" % (status, u))
    for pg in sorted(url_pages[u])[:5]:
        print("        on %s" % pg)
if not dead:
    print("\n  ✓ all links resolve")
sys.exit(1 if dead else 0)
