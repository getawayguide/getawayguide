#!/usr/bin/env python3
"""Does everything in a country's field notes make it into its articles?

The field notes are written first and are the complete list of what Kevin saw; the guides
and the itinerary are written from them afterwards, one place at a time, and things fall
through. Armenia's field notes had Sevanavank, the Black Wall and the abandoned Tu-134A, and
no article mentioned any of them (2026-09-26). This lists every linked place in
<country>/field-notes.html that none of the country's draft articles name, so the check
runs BEFORE a country is published rather than after a reader asks.

    python tools/coverage_check.py armenia
    python tools/coverage_check.py armenia --all      # every linked name, covered or not

A place counts as covered when its link text (or the same words without a trailing
"Monastery", "Market", "Museum" and the like) appears in any article's prose. The check is
by name, so a place written up under another spelling shows as missing; that is worth a
glance, not a fix.
"""
import argparse
import html as H
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERIC = {"here", "link", "website", "map", "maps", "this", "guide", "book", "booking", "site",
           "tour", "tours", "more", "read more", "online", "bus", "train", "ferry", "airport"}
TAILS = re.compile(r"\s+(monastery|market|museum|church|cathedral|temple|fortress|park|square|"
                   r"street|avenue|hostel|hotel|restaurant|cafe|coffee|bar|beach|lake|island|"
                   r"national park|observatory|arch|bridge|tunnel|gorge|valley|station)$", re.I)


def prose(path):
    t = path.read_text(encoding="utf-8", errors="replace")
    t = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", t, flags=re.S)
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", t))).lower()


def linked_names(path):
    t = path.read_text(encoding="utf-8", errors="replace")
    t = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", t, flags=re.S)
    out = []
    for m in re.finditer(r'<a href="(https?://[^"]+)"[^>]*>([^<]{3,80})</a>', t):
        name = re.sub(r"\s+", " ", H.unescape(m.group(2))).strip(" .,:;!")
        if name.lower() in GENERIC or len(name) < 3 or name.lower().startswith(("http", "www")):
            continue
        out.append((name, m.group(1)))
    seen, uniq = set(), []
    for n, u in out:
        if n.lower() not in seen:
            seen.add(n.lower()); uniq.append((n, u))
    return uniq


COMMON = {"abandoned", "central", "national", "ancient", "historic", "little", "old", "new", "free",
          "walking", "main", "local", "public", "private", "grand", "royal", "great", "upper", "lower"}


def covered(name, texts):
    """The full name, then the name without its generic tail, then its first proper word:
    the field notes say 'Orgov Space Telescope' where the guides say 'Orgov Observatory',
    and that is the same place. A common first word ('Abandoned Soviet Plane') proves
    nothing on its own and is not tried."""
    n = re.sub(r"\s*\(.*?\)", "", name.lower()).strip()
    short = TAILS.sub("", n)
    first = n.split()[0] if n.split() else ""
    for t in texts:
        if n in t or (len(short) >= 5 and short in t):
            return True
        if len(first) >= 5 and first not in COMMON and re.search(r"\b" + re.escape(first) + r"\b", t):
            return True
    return False


def place_keys(url):
    """what identifies a Google Maps place in a link: its place id, and its pin to ~100 m"""
    keys = set(re.findall(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", url))
    m = re.findall(r"!3d(-?[\d.]+)!4d(-?[\d.]+)", url)
    if m:
        keys.add("%.3f,%.3f" % tuple(map(float, m[-1])))
    return keys


def article_places(paths):
    """every map place the articles link to, by place_keys"""
    out = set()
    for p in paths:
        t = p.read_text(encoding="utf-8", errors="replace")
        for u in re.findall(r'href="(https?://[^"]*google\.[^"]*/maps/[^"]+)"', t):
            out |= place_keys(H.unescape(u))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("country", help="folder name, e.g. armenia")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    fn = ROOT / a.country / "field-notes.html"
    if not fn.exists():
        fn = ROOT / "Drafts" / a.country / "field-notes.html"
    if not fn.exists():
        raise SystemExit("no field notes for %s" % a.country)
    arts = sorted((ROOT / "Drafts" / ".Full Articles" / a.country).glob("*.html"))
    if not arts:
        arts = sorted(p for p in (ROOT / a.country).glob("*.html") if p.name != "field-notes.html")
    texts = [prose(p) for p in arts]
    names = linked_names(fn)
    linked = article_places(arts)       # the same place linked under another name counts
    missing = [(n, u) for n, u in names if not covered(n, texts) and not (place_keys(H.unescape(u)) & linked)]
    out = sys.stdout
    out.write("%s: %d linked places in the field notes, %d article(s) checked\n\n" % (a.country, len(names), len(arts)))
    if a.all:
        for n, u in names:
            out.write("  %s %s\n" % ("  " if covered(n, texts) else "!!", n))
        out.write("\n")
    if missing:
        out.write("NOT IN ANY ARTICLE (%d):\n" % len(missing))
        for n, u in missing:
            out.write("  - %-40s %s\n" % (n, u[:70]))
    else:
        out.write("everything in the field notes is covered.\n")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
