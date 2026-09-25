#!/usr/bin/env python3
"""What Kevin's review comments keep asking for, counted, so a first draft starts there.

Kevin, 2026-09-24: "make sure you learn from the comments I write in response to my writing"
and "make learn from text a regular process". So this is the process rather than a habit.

His review rounds should be about voice and substance. Anything he has already asked for on
three other articles is a draft that was handed over unfinished. This reads every comment he
has left in the editor margin, buckets them, and prints the ranking. The first run over the
Armenia itinerary and the Yerevan guide found that 46% of his 67 comments were asking for a
link of some kind, which is not something anyone would have guessed.

    python tools/learn_from_comments.py              # the ranking, plus what is new
    python tools/learn_from_comments.py --all        # every comment, not just unseen ones
    python tools/learn_from_comments.py --brief      # the drafting checklist, to read first
    python tools/learn_from_comments.py --json x.json

READ --brief BEFORE DRAFTING (workflows/publish_article.md, Stage 1).

The comments quote unpublished drafts, so everything here stays out of git: the store lives
in .tmp/redline/_comments/ and the seen-list in .tmp/, and the seen-list holds hashes rather
than text so it stays harmless even if it is moved somewhere tracked one day.
"""
import argparse
import glob
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / ".tmp" / "redline" / "_comments"
STATE = ROOT / ".tmp" / "learned_comments.json"

# Ordered: the first pattern that matches wins, so a comment counts once.
BUCKETS = [
    ("link: Google Maps",        r"google ?maps"),
    ("link: my other article",   r"link to (my |the )?(yerevan|gyumri|orgov|monastery|article)|inter.?article link|link to section|to section below"),
    ("link: my field notes",     r"field ?notes"),
    ("link: external website",   r"link to (the )?(website|site)|add separate links|prefilled|link these"),
    ("length: trim it",          r"trim|too long|feels longer|cut down|shorten|feels a bit short|feels light"),
    ("voice: too history-nerdy", r"history nerdy|history textbook|too many dates|clean up the dates"),
    # Widened from .{0,20}: "make sure the soviets rebuilding yerevan detail stays in"
    # puts forty characters between the two halves.
    ("move detail elsewhere",    r"but make sure|make sure .{0,60}(makes it|stays in|stays|is in)"),
    # Not writing feedback at all: a bug in the editor, reported where he happened to be
    # standing. These are worth pulling out because they otherwise die in a comment thread.
    ("TOOL BUG, not writing",    r"red-?line broke|font seems|should be able to|pop ?up dialog"),
    # A gap Claude left for him to fill, which is a draft handed over unfinished.
    ("finish the placeholder",   r"^fill out|^update this$|fill in|if there isn.t one"),
    ("no repeated info",         r"repeat"),
    ("more travel-blog color",   r"descriptive travel|add some background|write about what you can see"),
    ("accuracy / terminology",   r"more accurate|accent|greco-roman"),
    ("do not talk it down",      r"not encouraging|not true"),
]

BRIEF = """Before drafting, satisfy these without being asked (measured over %(n)d comments):

  LINK EVERYTHING (%(linkpct)d%% of all his comments)
    - every place, mountain, metro/bus station, border, neighbouring country -> Google Maps
    - every city that has its own guide -> that guide, every time it is named
    - every country he has been to -> ../../../<country>/field-notes.html, deep-linked
      to the city section when he names one
    - every app or booking site -> its website, with the route PREFILLED where it takes one
    - anything the intro names and the body covers -> an in-page anchor
    El Salvador is the benchmark he compares against, for link density and for length.

  VOICE
    - lead with what a visitor SEES, not a chronology. Cut dates that do not earn their place
    - never write a destination down. A plain city needs a better angle, not a shrug
    - guide opening prose runs 158-201 words; itinerary openings 254-266

  STRUCTURE
    - cutting means MOVING: a detail leaving the itinerary lands in the supporting article
    - do not repeat what another page in the set already says
"""


def load():
    out = []
    for f in sorted(glob.glob(str(STORE / "*.json"))):
        slug = Path(f).stem.split("__")[-1]
        try:
            threads = json.load(open(f, encoding="utf-8")).get("threads") or []
        except Exception as e:
            print("  ! could not read %s: %s" % (f, e), file=sys.stderr)
            continue
        for t in threads:
            for m in [t] + (t.get("replies") or []):
                if (m.get("author") or "").strip().lower() == "claude":
                    continue
                text = (m.get("text") or "").strip()
                if text:
                    out.append({"slug": slug, "text": text,
                                "quote": (t.get("anchor", {}) or {}).get("quote", "")[:70]})
    return out


def bucket(text):
    low = text.lower()
    for name, pat in BUCKETS:
        if re.search(pat, low):
            return name
    return "other"


def fp(c):
    """Slug + text. He writes "link to article" verbatim several times on one page, so 67
    comments collapse to 57 marks and a repeat of an identical ask does not re-report. That
    is the right trade here: this exists to find the PATTERN, and the pattern is already
    counted above."""
    return hashlib.sha256(("%s|%s" % (c["slug"], c["text"])).encode("utf-8")).hexdigest()[:20]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="list every comment, not only unseen")
    ap.add_argument("--brief", action="store_true", help="just the drafting checklist")
    ap.add_argument("--json", help="write the counts here")
    ap.add_argument("--keep-state", action="store_true", help="do not mark anything as seen")
    a = ap.parse_args()

    out = sys.stdout.buffer            # redirected stdout on this box is cp1252
    def w(s=""):
        out.write((s + "\n").encode("utf-8", "replace")); out.flush()

    cs = load()
    if not cs:
        w("No review comments found in %s" % STORE)
        return 0
    counts = Counter(bucket(c["text"]) for c in cs)
    linkpct = round(100 * sum(v for k, v in counts.items() if k.startswith("link:")) / len(cs))

    if a.brief:
        w(BRIEF % {"n": len(cs), "linkpct": linkpct})
        return 0

    try:
        seen = set(json.loads(STATE.read_text(encoding="utf-8")).get("seen", []))
    except Exception:
        seen = set()
    fresh = [c for c in cs if fp(c) not in seen]

    w("%d review comment(s) across %d article(s)\n" % (len(cs), len({c["slug"] for c in cs})))
    for k, v in counts.most_common():
        w("  %-26s %3d  %2d%%" % (k, v, round(100 * v / len(cs))))
    w("\n  %-26s %3d  %2d%%" % ("ALL LINK REQUESTS", sum(v for k, v in counts.items()
                                                        if k.startswith("link:")), linkpct))

    show = cs if a.all else fresh
    w("\n%s (%d):" % ("every comment" if a.all else "new since the last run", len(show)))
    for c in show:
        w("\n  [%s] %-26s on %r" % (c["slug"], bucket(c["text"]), c["quote"]))
        w("      %s" % c["text"][:300].replace("\n", " "))
    if not show:
        w("  (nothing new)")

    if a.json:
        Path(ROOT / a.json).write_text(json.dumps(
            {"total": len(cs), "counts": dict(counts), "link_pct": linkpct,
             "new": len(fresh)}, indent=1), encoding="utf-8")
        w("\nwrote %s" % a.json)

    if not a.keep_state:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"seen": sorted({fp(c) for c in cs})}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
