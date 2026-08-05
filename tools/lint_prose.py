#!/usr/bin/env python3
"""Prose-level checks the HTML linter doesn't do: typos, spacing, repeated words.

lint_site.py checks STRUCTURE (dead links, duplicate ids, nav drift, paste artifacts).
Nothing checked the writing itself, and the one spell-check that existed lived in .tmp/
and was lost in a cleanup.

Only real prose is inspected - the text inside <p>/<li>/<h*> with tags stripped - so
indentation, embedded JSON-LD, script and style never register. That distinction matters:
naively scanning the raw HTML reports thousands of "double spaces" that are just newline
indentation between tags.

Checks:
  double-space   two spaces between words
  repeat-word    "the the", "to to"
  space-punct    " ," / " ." / "( " / spacing around punctuation
  no-space-punct "word,word" with no space after the comma
  spell          words not in the dictionary or the project allow-list

  py tools/lint_prose.py                # live pages
  py tools/lint_prose.py --drafts       # drafts too
  py tools/lint_prose.py --fix          # apply the safe mechanical fixes
  py tools/lint_prose.py --add-words a b c    # extend the allow-list
"""
import argparse, glob, json, os, re, sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW_FILE = os.path.join(ROOT, "tools", "prose_allowlist.txt")
BLOCK = re.compile(r"<(script|style|svg|noscript)\b.*?</\1>", re.S | re.I)
PROSE = re.compile(r"<(p|li|h1|h2|h3|h4)\b[^>]*>(.*?)</\1>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")

# (name, pattern, replacement or None if it needs a human)
MECHANICAL = [
    ("double-space",   re.compile(r"(?<=\S) {2,}(?=\S)"), " "),
    # lowercase only: "Phi Phi", "Den Den", "Kome Kome", "Shanti Shanti" are place names
    ("repeat-word",    re.compile(r"\b([a-z]{2,}) \1\b"), None),
    ("space-punct",    re.compile(r" ([,;:!?](?:\s|$))"), r"\1"),
    # NOT auto-fixed: "a nice break from ." is a missing WORD, and closing the gap
    # would hide an incomplete sentence rather than repair it
    ("space-period",   re.compile(r"(?<=[a-z]) \.(?=\s|$)"), None),
    ("no-space-comma", re.compile(r"(?<=[a-z]),(?=[A-Za-z])"), ", "),
]


def load_allow():
    words = set()
    if os.path.exists(ALLOW_FILE):
        for ln in open(ALLOW_FILE, encoding="utf-8"):
            ln = ln.split("#")[0].strip().lower()
            if ln:
                words.add(ln)
    return words


def pages(drafts=False):
    pats = [os.path.join(ROOT, "*.html"), os.path.join(ROOT, "*", "*.html")]
    if drafts:
        pats.append(os.path.join(ROOT, "Drafts", "*", "field-notes.html"))
    for p in sorted({q for pat in pats for q in glob.glob(pat)}):
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.startswith((".tmp/", "Drafts/.")) or rel == "editor.html":
            continue
        if rel.startswith("Drafts/") and not drafts:
            continue
        yield p, rel


def prose_spans(html):
    """[(start, end, text)] for each prose element, tags already stripped."""
    clean = BLOCK.sub(lambda m: " " * len(m.group(0)), html)
    for m in PROSE.finditer(clean):
        inner = m.group(2)
        if "\n" in inner:
            # keep single-line elements only; multi-line ones are indented markup
            inner_txt = TAG.sub("", inner)
            if re.search(r"\n\s{2,}", inner_txt):
                continue
        yield m.start(2), m.end(2), TAG.sub("", inner)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drafts", action="store_true")
    ap.add_argument("--fix", action="store_true", help="apply the safe mechanical fixes")
    ap.add_argument("--spell", action="store_true", help="include the spell check")
    ap.add_argument("--add-words", nargs="*", help="append words to the allow-list")
    a = ap.parse_args()

    if a.add_words:
        cur = load_allow()
        with open(ALLOW_FILE, "a", encoding="utf-8") as fh:
            for w in a.add_words:
                if w.lower() not in cur:
                    fh.write(w.lower() + "\n")
        print("allow-list: +%d word(s)" % len(a.add_words))
        return

    found = {k: [] for k, _, _ in MECHANICAL}
    fixed = 0
    for p, rel in pages(a.drafts):
        html = open(p, encoding="utf-8").read()
        new = html
        for name, rx, repl in MECHANICAL:
            for s0, s1, txt in prose_spans(new):
                for m in rx.finditer(txt):
                    found[name].append((rel, m.group(0).strip()[:48],
                                        re.sub(r"\s+", " ", txt[max(0, m.start()-34):m.start()+34]).strip()))
            if a.fix and repl is not None:
                # rebuild only the prose spans so markup is never touched
                out, last = [], 0
                for s0, s1, _ in prose_spans(new):
                    seg = new[s0:s1]
                    # protect tags, fix the text between them
                    parts = re.split(r"(<[^>]+>)", seg)
                    for i in range(0, len(parts), 2):
                        f = rx.sub(repl, parts[i])
                        if f != parts[i]:
                            fixed += 1
                        parts[i] = f
                    if name == "double-space":
                        # a gap can straddle a tag: `<span> &mdash; </span> text` renders as
                        # two spaces while each text node holds only one, so the per-part
                        # pass above can't see it. Collapse across the boundary.
                        ends_space = False
                        for i in range(0, len(parts), 2):
                            if ends_space and parts[i][:1] == " ":
                                parts[i] = parts[i].lstrip(" ")
                                fixed += 1
                            if parts[i].strip():
                                ends_space = parts[i].endswith(" ")
                    out.append(new[last:s0]); out.append("".join(parts)); last = s1
                out.append(new[last:])
                new = "".join(out)
        if a.fix and new != html:
            open(p, "w", encoding="utf-8", newline="").write(new)

    # ---- spelling ---------------------------------------------------------------------
    # Travel writing is full of place names, so this is noisy by nature: only words that
    # appear ONCE across the whole site are reported. A misspelling is usually a one-off;
    # a place name Kevin uses repeatedly is not.
    if a.spell:
        try:
            from spellchecker import SpellChecker
        except ImportError:
            print("!! pip install pyspellchecker to enable --spell")
        else:
            sp = SpellChecker()
            sp.word_frequency.load_words(load_allow())
            counts, where, ever_capped = {}, {}, set()
            for p, rel in pages(a.drafts):
                html = open(p, encoding="utf-8").read()
                for _, _, txt in prose_spans(html):
                    # include the curly apostrophe and the hyphen, or "isn’t" and "go-tos"
                    # split into fragments ("isn", "tos") that look like typos but aren't
                    for m in re.finditer(r"\b([A-Za-z][a-z'’\-]{2,})\b", txt):
                        w = m.group(1)
                        lw = w.lower().strip("'’-")
                        counts[lw] = counts.get(lw, 0) + 1
                        where.setdefault(lw, (rel, txt[:70]))
                        # capitalised mid-sentence => a name, not a typo
                        if w[0].isupper() and m.start() > 0 and txt[m.start()-1] not in ".!?\"“(":
                            ever_capped.add(lw)

            # Raw "not in dictionary" is ~1600 words and almost all of it is place names,
            # which is useless. A TYPO looks different: it's always lowercase, appears once,
            # and the dictionary has a near neighbour that is itself a common word.
            cands = [w for w, c in counts.items() if c == 1 and w not in ever_capped]
            bad = []
            for w in sorted(sp.unknown(cands)):
                fix = sp.correction(w)
                if fix and fix != w and sp.word_frequency[fix] > 1e7:
                    bad.append((w, fix))
            print("\n[spell] %d likely typo(s)  (from %d unknown words, place names filtered out)"
                  % (len(bad), len(cands)))
            for w, fix in bad[:25]:
                rel, ctx = where[w]
                print("   %-16s -> %-16s %-28s …%s…"
                      % (w, fix, rel, re.sub(r"\s+", " ", ctx)))
            if len(bad) > 25:
                print("   … and %d more" % (len(bad) - 25))
            print("   (a real word wrongly flagged? py tools/lint_prose.py --add-words foo)")

    total = sum(len(v) for v in found.values())
    print("%sprose issues: %d" % ("[fixed %d] " % fixed if a.fix else "", total))
    for name, _, repl in MECHANICAL:
        hits = found[name]
        if not hits:
            continue
        tag = "" if repl is not None else "   (needs a human)"
        print("\n[%s] %d%s" % (name, len(hits), tag))
        for rel, what, ctx in hits[:8]:
            print("   %-34s %-16s …%s…" % (rel, repr(what), ctx))
        if len(hits) > 8:
            print("   … and %d more" % (len(hits) - 8))


if __name__ == "__main__":
    main()
