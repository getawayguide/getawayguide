"""Normalize the capitalization of field-note subheads.

These headings (`.fn-sub-hd`, and the place headings beside them) used to be
rendered ALL CAPS by CSS, so however they were typed came out looking the same.
Nothing pushed back on `wHERE TO STAY` or `where to stay`, and the source drifted
into a dozen spellings of the same six headings. The re-theme sets them in
Newsreader at their typed case, so the drift is now visible on the page.

    python tools/fix_subhead_case.py --dry-run   # show what would change
    python tools/fix_subhead_case.py             # apply

The site's convention is title case with short prepositions and conjunctions left
lowercase: "What to Do", "Where to Stay", "Day Trips from Cusco", "How to Get
There". CANON below is keyed on the lowercased heading, so every spelling of a
known heading collapses onto the one form. Anything not in CANON is reported, not
rewritten -- a heading like "Foods (similar to most other Balkan countries)" is
deliberately not title case and must not be mangled.
"""
import argparse
import collections
import pathlib
import re

SITE = pathlib.Path(__file__).resolve().parent.parent
SKIP = {"preview", ".tmp", "Drafts", "tools", "node_modules", "Images"}

# lowercased heading -> the one spelling the site uses
CANON = {
    "what to do": "What to Do",
    "where to stay": "Where to Stay",
    "where to eat": "Where to Eat",
    "where to drink": "Where to Drink",
    "where to eat &amp; drink": "Where to Eat &amp; Drink",
    "where to eat and drink": "Where to Eat &amp; Drink",
    "what to eat": "What to Eat",
    "what to expect": "What to Expect",
    "what to do in cusco city": "What to Do in Cusco City",
    "getting there": "Getting There",
    "getting around": "Getting Around",
    "how to get there": "How to Get There",
    "day trips": "Day Trips",
    "day trips &amp; festivals": "Day Trips &amp; Festivals",
    "nightlife": "Nightlife",
    "neighborhoods": "Neighborhoods",
    "neighborhoods / where to stay": "Neighborhoods / Where to Stay",
    "practical info": "Practical Info",
    "general": "General",
    "general logistics": "General Logistics",
    "the basics": "The Basics",
    "the highlights": "The Highlights",
    "the verdict": "The Verdict",
    "transport": "Transport",
    "tickets": "Tickets",
    "tips": "Tips",
    "itinerary": "Itinerary",
    "historic towns": "Historic Towns",
    "worth knowing": "Worth Knowing",
    "suggested route overview": "Suggested Route Overview",
}

# the elements that carry a subhead
PAT = re.compile(r'(class="fn-sub-hd"[^>]*>)([^<]+)(<)')


def pages():
    for f in sorted(SITE.rglob("*.html")):
        if not any(p in SKIP for p in f.relative_to(SITE).parts):
            yield f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    changed, edits = 0, 0
    unknown = collections.Counter()
    for f in pages():
        src = f.read_text(encoding="utf-8")
        hits = []

        def sub(m):
            nonlocal hits
            raw = m.group(2)
            want = CANON.get(raw.strip().lower())
            if want is None:
                unknown[raw.strip()] += 1
                return m.group(0)
            if raw.strip() == want:
                return m.group(0)
            hits.append((raw.strip(), want))
            return m.group(1) + want + m.group(3)

        out = PAT.sub(sub, src)
        if not hits:
            continue
        changed += 1
        edits += len(hits)
        rel = f.relative_to(SITE).as_posix()
        for was, now in hits:
            print(f"  {rel}: {was!r} -> {now!r}")
        if not a.dry_run:
            f.write_text(out, encoding="utf-8")

    verb = "would fix" if a.dry_run else "fixed"
    print(f"\n{verb} {edits} subheads across {changed} pages")
    if unknown:
        print(f"\n{len(unknown)} headings are not in CANON and were left alone:")
        for t, n in sorted(unknown.items()):
            # only worth a look if it reads like a casing slip
            odd = t and (t[0].islower() or t.isupper())
            print(f"  {'!! ' if odd else '   '}{n:>3}  {t!r}")
        print("  (!! = starts lowercase or is all caps; add it to CANON if it "
              "is one of the site's standard headings)")


if __name__ == "__main__":
    main()
