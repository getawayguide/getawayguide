#!/usr/bin/env python3
"""Download the nav's country flags once and serve them locally.

The Destinations dropdown pulls 32 images from flagcdn.com on EVERY page (2,192
references site-wide). That puts a third party in the critical path of the site's most
used component: if flagcdn is slow, rate-limits or is blocked, the dropdown renders as a
column of broken images. It's also 32 extra connections to an origin that isn't ours.

Fetching needs network, so run this from PowerShell. It writes Images/web/flags/<iso>.png
and rewrites every flagcdn.com reference to the local copy.

  py tools/localize_flags.py --list      # which flags are referenced
  py tools/localize_flags.py --fetch     # download them (PowerShell)
  py tools/localize_flags.py --all       # download EVERY country's flag (see below)
  py tools/localize_flags.py --rewrite   # point the HTML at the local copies

WHY --all EXISTS. `referenced()` scans for LITERAL flagcdn URLs, which only finds the flags
hard-coded as <img> tags in the nav. destinations.html builds its tooltip flag at runtime:

    src="Images/web/flags/" + id.toLowerCase() + ".png"

There is no literal URL there to match, so the first localisation pass downloaded 33 flags
and left every other country's tooltip pointing at a file that does not exist - a broken
image for 26 of the 50 visited countries and for every country on the all-countries layer.
The map can show ANY country, so the local set has to cover any country: --all fetches the
full ISO-3166-1 alpha-2 list. Roughly 250 files at ~600 bytes each, so ~150KB total.
"""
import glob, json, os, re, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "Images", "web", "flags")
# the nav asks for 16x12; grab 32x24 so it stays crisp on retina and let width/height scale it
REMOTE = "https://flagcdn.com/32x24/%s.png"
CODES = "https://flagcdn.com/en/codes.json"
REF = re.compile(r'https://flagcdn\.com/\d+x\d+/([a-z]{2})\.png')
# flag URLs assembled in JS from a country id - invisible to REF, hence --all
RUNTIME = re.compile(r'flags/["\']?\s*\+\s*\w+')


CODES_CACHE = os.path.join(ROOT, ".tmp", "flagcdn_codes.json")


def all_codes():
    """Every ISO-3166-1 alpha-2 code flagcdn serves (its list also carries subdivisions
    like gb-eng and us-ca, which the map never asks for, so keep 2-letter codes only).

    Prefers the cached list: Python's certificate store rejects flagcdn in this
    environment, so the list is fetched with PowerShell into .tmp/flagcdn_codes.json.
    """
    data = None
    if os.path.exists(CODES_CACHE):
        data = json.load(open(CODES_CACHE, encoding="utf-8-sig"))
    else:
        req = urllib.request.Request(CODES, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    return sorted(c for c in data if re.fullmatch(r"[a-z]{2}", c))


def runtime_builders():
    """Pages that construct a flag path in JS, so a literal-URL scan cannot see their needs."""
    hits = []
    for p, rel in pages():
        if RUNTIME.search(open(p, encoding="utf-8", errors="replace").read()):
            hits.append(rel)
    return hits


def pages():
    for p in sorted(glob.glob(os.path.join(ROOT, "**", "*.html"), recursive=True)):
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.startswith((".tmp/", ".git/")):
            continue
        yield p, rel


def referenced():
    iso = set()
    for p, _ in pages():
        iso |= set(REF.findall(open(p, encoding="utf-8", errors="replace").read()))
    return sorted(iso)


def fetch(isos):
    os.makedirs(OUT, exist_ok=True)
    got = skip = fail = 0
    for c in isos:
        dst = os.path.join(OUT, "%s.png" % c)
        if os.path.exists(dst):
            skip += 1
            continue
        try:
            req = urllib.request.Request(REMOTE % c, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            open(dst, "wb").write(data)
            got += 1
        except Exception as e:
            print("  !! %s: %s" % (c, str(e)[:60]))
            fail += 1
    total = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT)) / 1024
    print("flags: %d downloaded, %d already present, %d failed  (%.0f KB total)"
          % (got, skip, fail, total))
    return fail == 0


def rewrite():
    missing = set()
    changed = 0
    for p, rel in pages():
        s = open(p, encoding="utf-8").read()
        depth = rel.count("/")
        pfx = "../" * depth

        def sub(m):
            iso = m.group(1)
            if not os.path.exists(os.path.join(OUT, "%s.png" % iso)):
                missing.add(iso)
                return m.group(0)
            return "%sImages/web/flags/%s.png" % (pfx, iso)

        new = REF.sub(sub, s)
        if new != s:
            open(p, "w", encoding="utf-8", newline="").write(new)
            changed += 1
    print("rewrote flag urls on %d page(s)" % changed)
    if missing:
        print("  !! no local file for: %s (run --fetch)" % ", ".join(sorted(missing)))
    return not missing


def audit():
    """Which local flags are missing for anything a page can ask for at runtime."""
    have = {f[:-4] for f in os.listdir(OUT)} if os.path.isdir(OUT) else set()
    builders = runtime_builders()
    print("local flags: %d" % len(have))
    if builders:
        print("pages that build flag URLs in JS (need the FULL set): %s" % ", ".join(builders))
        try:
            need = all_codes()
        except Exception as e:
            print("  (could not fetch the code list: %s)" % str(e)[:60])
            return False
        gap = [c for c in need if c not in have]
        print("  ISO codes served by flagcdn: %d | missing locally: %d" % (len(need), len(gap)))
        if gap:
            print("  e.g. %s ... (run --all)" % " ".join(gap[:14]))
        return not gap
    return True


if __name__ == "__main__":
    isos = referenced()
    if "--list" in sys.argv or len(sys.argv) == 1:
        print("%d distinct flags referenced literally: %s" % (len(isos), " ".join(isos)))
    if "--audit" in sys.argv:
        audit()
    if "--fetch" in sys.argv:
        fetch(isos)
    if "--all" in sys.argv:
        fetch(all_codes())
    if "--rewrite" in sys.argv:
        rewrite()
