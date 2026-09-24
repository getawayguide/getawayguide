"""The two prose judgements that are genuinely hard, in one place.

Both lint_site.py (pre-push gate) and prose_check.py (the editor margin and the routine
emails) have to answer them, and they were answering them differently: lint_site reported 4
em dashes across the whole site where prose_check reported dozens of the same bullets,
because lint_site's rule had been tuned over months and prose_check's was written fresh.
One definition, used by both, so tuning either tool tunes the site.

No imports beyond re, so a caller can use it without pulling in a whole linter.
"""
import re

# `<b>Name</b> — description` opening its own bullet is the site's list convention, not prose.
# Tags may sit before the bold (a link wrapping it) and between the bold close and the dash
# (Belgium wraps the dash itself in a <span>), so both ends allow them.
LEAD_IN = re.compile(r"^\s*(?:<(?!b\b|strong\b)[^>]+>\s*)*<(b|strong)\b[^>]*>.*?</\1>\s*(?:<[^>]+>\s*)*$", re.S)
# A lead-in that is not bolded at all: short, and carrying none of the verbs or pronouns that
# make a clause. "I walked past the cathedral twice — it was closed" is narration, not a label.
CLAUSE = re.compile(r"\b(is|are|was|were|has|have|will|can|you|we|it|they|i|my|me|our)\b", re.I)
TAGS = re.compile(r"<[^>]+>")


def em_dash_is_separator(html_before_dash):
    """True when the em dash at the end of this run of HTML is the site's term separator
    rather than a dash in a sentence. `html_before_dash` is the markup from the start of the
    enclosing <li>/<p> up to the dash."""
    if LEAD_IN.match(html_before_dash):
        return True
    head = max(html_before_dash.rfind(". "), 0)
    pre = re.sub(r"^\W+", "", TAGS.sub("", html_before_dash[head:]).strip())
    if not pre:
        return True                                   # the dash opens the block
    return len(pre.split()) <= 9 and not CLAUSE.search(pre)


def in_attribute(line, idx):
    """True when this position sits inside an HTML attribute value rather than in text."""
    q, eq = line.rfind('"', 0, idx), line.rfind("=", 0, idx)
    return q > 0 and eq > 0 and q - eq <= 2 and line.find('"', idx) > idx


def is_proper_name(text, start, end):
    """True when a capitalized British spelling is part of a name and must not be corrected.

    CLAUDE.md: never "correct" a real place or business. Viaduct Harbour, Lady Janes Ice Cream
    Parlour, Centre Pompidou, Sydney Harbour Bridge. The test is a capitalized neighbour on
    EITHER side: the old rule only looked backwards, which protected Viaduct Harbour but not
    Centre Pompidou (the documented example) and not Grey Glacier, a real glacier in Torres
    del Paine that the Chile page names three times.
    """
    word = text[start:end]
    if not word[:1].isupper():
        return False
    prev = re.search(r"([A-Za-z’'\-]+)[\s ]+$", text[max(0, start - 60):start])
    nxt = re.match(r"[\s ]+([A-Za-z’'\-]+)", text[end:end + 60])
    return bool((prev and prev.group(1)[:1].isupper()) or (nxt and nxt.group(1)[:1].isupper()))
