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
  py tools/localize_flags.py --rewrite   # point the HTML at the local copies
"""
import glob, os, re, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "Images", "web", "flags")
# the nav asks for 16x12; grab 32x24 so it stays crisp on retina and let width/height scale it
REMOTE = "https://flagcdn.com/32x24/%s.png"
REF = re.compile(r'https://flagcdn\.com/\d+x\d+/([a-z]{2})\.png')


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


if __name__ == "__main__":
    isos = referenced()
    if "--list" in sys.argv or len(sys.argv) == 1:
        print("%d distinct flags referenced: %s" % (len(isos), " ".join(isos)))
    if "--fetch" in sys.argv:
        fetch(isos)
    if "--rewrite" in sys.argv:
        rewrite()
