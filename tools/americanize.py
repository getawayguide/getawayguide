#!/usr/bin/env python3
"""Convert British spellings to American in field-notes PROSE.

Kevin is American, so the writing has to read in his voice. Drafts assembled from mixed
sources pick up British forms (harbour, centre, travelling, customisation).

Two protections, because a blind find/replace would corrupt real names:
  1. Only PROSE is touched. Everything inside a tag (href, alt, title) and inside
     <style>/<script> is masked out first, so URLs and embedded map code can't match.
  2. A capitalised British word preceded by another capitalised word is treated as part of a
     PROPER NAME and left alone ("Viaduct Harbour", "Lady Janes Ice Cream Parlour"), as are
     any phrases in KEEP.

  python tools/americanize.py --dry-run        # report only
  python tools/americanize.py                  # apply to all drafts
  python tools/americanize.py japan            # one draft
"""
import argparse, glob, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# British -> American. -ise verbs are expanded to their inflections automatically below.
BASE = [
    ("colour", "color"), ("harbour", "harbor"), ("favourite", "favorite"), ("favour", "favor"),
    ("neighbour", "neighbor"), ("flavour", "flavor"), ("behaviour", "behavior"),
    ("labour", "labor"), ("rumour", "rumor"), ("honour", "honor"), ("humour", "humor"),
    ("odour", "odor"), ("vapour", "vapor"), ("savour", "savor"), ("splendour", "splendor"),
    ("glamour", "glamor"), ("parlour", "parlor"), ("armour", "armor"), ("harbours", "harbors"),
    ("centre", "center"), ("theatre", "theater"), ("metre", "meter"), ("litre", "liter"),
    ("fibre", "fiber"), ("sombre", "somber"), ("calibre", "caliber"), ("spectre", "specter"),
    ("centres", "centers"), ("theatres", "theaters"), ("metres", "meters"), ("litres", "liters"),
    ("analyse", "analyze"), ("paralyse", "paralyze"),
    ("travelled", "traveled"), ("travelling", "traveling"), ("traveller", "traveler"),
    ("travellers", "travelers"), ("cancelled", "canceled"), ("cancelling", "canceling"),
    ("labelled", "labeled"), ("marvellous", "marvelous"), ("jewellery", "jewelry"),
    ("modelled", "modeled"), ("fuelled", "fueled"), ("signalled", "signaled"),
    ("defence", "defense"), ("offence", "offense"), ("licence", "license"),
    ("grey", "gray"), ("aluminium", "aluminum"), ("programme", "program"),
    ("storey", "story"), ("storeys", "stories"), ("kerb", "curb"), ("tyre", "tire"),
    ("draught", "draft"), ("cosy", "cozy"), ("whilst", "while"), ("amongst", "among"),
    ("speciality", "specialty"), ("specialities", "specialties"),
    ("pyjamas", "pajamas"), ("moustache", "mustache"), ("plough", "plow"),
    ("sceptical", "skeptical"), ("manoeuvre", "maneuver"), ("catalogue", "catalog"),
    ("mould", "mold"), ("smoulder", "smolder"), ("enrol", "enroll"), ("fulfil", "fulfill"),
    ("instalment", "installment"), ("skilful", "skillful"), ("wilful", "willful"),
    ("aeroplane", "airplane"), ("doughnut", "donut"), ("practise", "practice"),
]
# verbs where British -ise is genuinely wrong in US English (NOT advertise/surprise/etc.)
ISE = ["real", "organ", "recogn", "special", "apolog", "memor", "priorit", "custom",
       "maxim", "minim", "emphas", "summar", "social", "util", "civil", "modern",
       "acclimat", "energ", "normal", "final", "critic", "author", "capital"]
for stem in ISE:
    for suf_b, suf_a in [("ise", "ize"), ("ised", "ized"), ("ising", "izing"),
                         ("isation", "ization"), ("isations", "izations"), ("iser", "izer")]:
        BASE.append((stem + suf_b, stem + suf_a))

KEEP = ["Viaduct Harbour", "Lady Janes Ice Cream Parlour", "Lady Jane's Ice Cream Parlour"]

BLOCK = re.compile(r"<(style|script)\b.*?</\1>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")


def mask(html):
    """Positions that are prose (True) vs markup/code (False)."""
    ok = [True] * len(html)
    for rx in (BLOCK, TAG):
        for m in rx.finditer(html):
            for i in range(m.start(), m.end()):
                ok[i] = False
    for phrase in KEEP:
        for m in re.finditer(re.escape(phrase), html):
            for i in range(m.start(), m.end()):
                ok[i] = False
    return ok


def match_case(src, repl):
    if src.isupper(): return repl.upper()
    if src[0].isupper(): return repl[0].upper() + repl[1:]
    return repl


def convert(html):
    ok = mask(html)
    edits = []          # (start, end, new, old)
    for brit, amer in BASE:
        for m in re.finditer(r"\b%s\b" % re.escape(brit), html, re.I):
            s, e = m.span()
            if not all(ok[s:e]):
                continue
            word = m.group(0)
            # Capitalised and preceded by another capitalised word -> part of a proper name
            if word[0].isupper():
                before = html[max(0, s - 40):s]
                prev = re.search(r"([A-Za-z’'\-]+)\s+$", before)
                if prev and prev.group(1)[0].isupper():
                    continue
            edits.append((s, e, match_case(word, amer), word))
    if not edits:
        return html, []
    edits.sort(key=lambda t: -t[0])          # apply back-to-front
    seen, out, applied = set(), html, []
    for s, e, new, old in edits:
        if s in seen: continue
        seen.add(s)
        out = out[:s] + new + out[e:]
        applied.append((old, new))
    return out, applied


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    pat = "Drafts/%s/field-notes.html" % a.slug if a.slug else "Drafts/*/field-notes.html"
    total = 0
    for f in sorted(glob.glob(os.path.join(ROOT, pat))):
        html = open(f, encoding="utf-8").read()
        new, applied = convert(html)
        if not applied:
            continue
        rel = os.path.relpath(f, ROOT).replace("\\", "/")
        if not a.dry_run:
            open(f, "w", encoding="utf-8", newline="").write(new)
        counts = {}
        for old, nw in applied:
            counts["%s->%s" % (old, nw)] = counts.get("%s->%s" % (old, nw), 0) + 1
        print("  %-38s %2d  %s" % (rel, len(applied),
                                   ", ".join("%s(%d)" % (k, v) if v > 1 else k
                                             for k, v in sorted(counts.items()))))
        total += len(applied)
    print("\n%s %d spelling(s)" % ("would change" if a.dry_run else "changed", total))


if __name__ == "__main__":
    main()
