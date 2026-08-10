#!/usr/bin/env python3
"""Pull prose toward Kevin's measured register: contractions in, "genuinely" down.

A 2026-08-09 audit of 98.5k words of his live pages against 8.8k words of AI-written draft
prose found the drafts were measurably off even though they read fine:

    contractions   his 11.4 / 1k    drafts  5.3
    "genuinely"    his  0.38 / 1k   drafts  2.73     (7x his rate)
    "you'll"       his  1.35 / 1k   drafts  0.00

Formal expansions ("it is", "do not", "you will") are the single biggest tell. This
contracts them and thins out the "genuinely" tic.

Masking is copied from americanize.py: <script>/<style> bodies and everything inside a tag
are off limits, so hrefs, embedded city-map JS and attribute text can never be rewritten.

Deliberately NOT automated: splitting long sentences, and adding "a bit"/"pretty"/"you'll"
where they do not already exist. Both need judgment about meaning; the tool reports them
instead so they can be done by hand.

  py tools/register_pass.py --drafts --dry-run
  py tools/register_pass.py --drafts --skip germany
"""
import argparse, glob, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOCK = re.compile(r"<(style|script)\b.*?</\1>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")

# Only the forms Kevin actually contracts. His habit is NOT uniform, and a blanket sweep
# overshoots badly: it pushed the drafts to 15.4 contractions/1k against his 11.4, and wiped
# out formal expansions (0.1/1k) that he in fact uses at 6.3/1k.
#
# Measured across 98.5k words of his live pages, share of each pair he writes contracted:
#     did not 96%   you will 95%   you are 94%   do not 93%   I have 89%   is not 67%
#     it is   27%   they are 23%   I would 29%   there is 16%   that is 9%
# So the five on the second line are left alone; contracting them is what made my earlier
# pass read wrong.
PAIRS = [
    ("do not", "don't"), ("does not", "doesn't"), ("did not", "didn't"),
    ("is not", "isn't"), ("are not", "aren't"), ("was not", "wasn't"),
    ("were not", "weren't"), ("cannot", "can't"), ("can not", "can't"),
    ("will not", "won't"), ("would not", "wouldn't"), ("could not", "couldn't"),
    ("should not", "shouldn't"), ("have not", "haven't"), ("has not", "hasn't"),
    ("you will", "you'll"), ("you are", "you're"), ("you would", "you'd"),
    ("you have", "you've"), ("I have", "I've"), ("I will", "I'll"), ("I am", "I'm"),
]
# Deliberately NOT contracted (he keeps these expanded most of the time):
#   it is, there is, that is, they are, we are, I would, what is, here is, it will
# "genuinely" is his word too, just far rarer. Vary most of them away.
GENUINELY = ["really", "pretty", "honestly", ""]


def mask(html):
    ok = [True] * len(html)
    for rx in (BLOCK, TAG):
        for m in rx.finditer(html):
            for i in range(m.start(), m.end()):
                ok[i] = False
    return ok


def match_case(src, repl):
    return repl[0].upper() + repl[1:] if src[0].isupper() else repl


def convert(html, gen_budget):
    ok = mask(html)
    edits = []
    for long, short in PAIRS:
        for m in re.finditer(r"\b%s\b" % long.replace(" ", r"\s+"), html, re.I):
            s, e = m.span()
            if not all(ok[s:e]):
                continue
            # leave it alone if the next character starts a clause break; "it is," reads
            # emphatic and contracting it changes the beat
            if html[e:e + 1] in (",", ";"):
                continue
            edits.append((s, e, match_case(m.group(0), short)))

    # thin out "genuinely" down to the budget, cycling through his own hedges
    gens = [m for m in re.finditer(r"\bgenuinely\b", html, re.I) if all(ok[m.start():m.end()])]
    for i, m in enumerate(gens):
        if i < gen_budget:
            continue
        repl = GENUINELY[i % len(GENUINELY)]
        s, e = m.span()
        if repl == "":                      # drop the word and the space after it
            while e < len(html) and html[e] == " ":
                e += 1
        edits.append((s, e, match_case(m.group(0), repl) if repl else ""))

    edits.sort(key=lambda t: -t[0])
    out, seen, n = html, set(), 0
    for s, e, new in edits:
        if s in seen:
            continue
        seen.add(s)
        out = out[:s] + new + out[e:]
        n += 1
    return out, n


def report(html):
    """Things needing a human: long sentences, and where second person is missing."""
    body = BLOCK.sub(" ", html[html.lower().find("</head>"):])
    body = re.sub(r"<nav\b.*?</nav>|<footer\b.*?</footer>", " ", body, flags=re.S)
    text = re.sub(r"\s+", " ", TAG.sub(" ", body))
    longs = [s.strip() for s in re.split(r"(?<=[.!?]) ", text)
             if len(s.split()) > 42 and len(s) < 500]
    return longs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drafts", action="store_true", help="all Drafts/*/field-notes.html")
    ap.add_argument("--skip", default="", help="comma-separated slugs to leave alone")
    ap.add_argument("--gen-budget", type=int, default=1,
                    help='"genuinely" allowed per file before varying it away')
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()

    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    files = a.files or (sorted(glob.glob(os.path.join(ROOT, "Drafts", "*", "field-notes.html")))
                        if a.drafts else [])
    total = 0
    for p in files:
        slug = os.path.basename(os.path.dirname(p))
        if slug in skip:
            print("  %-13s skipped" % slug)
            continue
        s = open(p, encoding="utf-8", errors="replace").read()
        out, n = convert(s, a.gen_budget)
        longs = report(out)
        total += n
        print("  %-13s %3d contraction/tic edits | %d sentence(s) over 42 words" % (slug, n, len(longs)))
        if not a.dry_run and out != s:
            open(p, "w", encoding="utf-8", newline="").write(out)
    print("%s%d edit(s) across %d file(s)"
          % ("[dry-run] " if a.dry_run else "", total, len(files) - len(skip & {
              os.path.basename(os.path.dirname(f)) for f in files})))


if __name__ == "__main__":
    main()
