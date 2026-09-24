"""Every prose check the repo has, run on ONE article's HTML and returned as anchored findings.

The article editor's review margin calls this through the photo server (POST /review/lint)
and shows each finding as a card level with the text it is about, the way comments are shown.
Nothing here writes a file; the mechanical fixes are offered back to the editor as a
replacement string and applied there, in the text the writer is looking at.

The checks are the existing tools' own, imported rather than copied, so a rule tightened in
lint_prose.py or a word added to americanize.py is picked up here on the next run:

  lint_prose.MECHANICAL       double spaces, repeated words, spacing around punctuation
  americanize.convert         British spellings (with its proper-name and tag guards)
  strip_paste_artifacts.ART   Google-Docs inline styles that fight the page CSS
  em dash in body prose       CLAUDE.md: body prose only; the <b>Term</b> — bullet separator is
                              site convention and is skipped, as are titles and meta
  leftovers                   [ ], [PHOTO] and other bracketed notes; Maps search placeholders

    python tools/prose_check.py "Drafts/.Full Articles/armenia/yerevan.html"
"""
import html as htmlmod
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import lint_prose            # noqa: E402
import americanize           # noqa: E402
import strip_paste_artifacts # noqa: E402

TAG = re.compile(r"<[^>]+>")
BLOCK = re.compile(r"<(script|style|svg|noscript|figure)\b.*?</\1>", re.S | re.I)
PROSE = re.compile(r"<(p|li|h1|h2|h3|h4|figcaption|blockquote)\b[^>]*>(.*?)</\1>", re.S | re.I)
MAPS_SEARCH = re.compile(r'href="https://www\.google\.com/maps/search/\?api=1&(?:amp;)?query=([^"]+)"')
BRACKET = re.compile(r"\[(?!\d+\])[^\[\]\n]{0,120}\]")     # [ ], [PHOTO], [fill this in]; not [1] footnotes


def _anchor(text, start, end):
    """The quote plus a little context either side, in the visible text, which is what the
    editor's comment anchors use. Context makes a repeated word land on the right one."""
    return {"quote": text[start:end], "before": text[max(0, start - 40):start], "after": text[end:end + 40]}


def _visible(inner):
    return htmlmod.unescape(TAG.sub("", inner))


def check(html):
    """-> [ {id, kind, message, anchor:{quote,before,after}, fix?: replacement} ]"""
    out = []
    n = 0

    def add(kind, message, text, s, e, fix=None, severity="warn"):
        nonlocal n
        n += 1
        f = {"id": "ln%03d" % n, "kind": kind, "message": message, "severity": severity,
             "anchor": _anchor(text, s, e)}
        if fix is not None:
            f["fix"] = fix
        out.append(f)

    masked = BLOCK.sub(lambda m: " " * len(m.group(0)), html)

    # ---- prose spans: the tools' own regexes, on the visible text of each prose element
    for m in PROSE.finditer(masked):
        inner = m.group(2)
        if re.search(r"\n\s{2,}", TAG.sub("", inner)):
            continue                                     # multi-line = indented markup
        text = _visible(inner)
        if not text.strip():
            continue
        for name, rx, repl in lint_prose.MECHANICAL:
            for h in rx.finditer(text):
                fix = rx.sub(repl, h.group(0)) if repl is not None else None
                msg = {"double-space": "Two spaces between words.",
                       "repeat-word": "Repeated word.",
                       "space-punct": "Space before punctuation.",
                       "space-period": "Space before a period. Usually a missing word, so not auto-fixed.",
                       "no-space-comma": "No space after the comma."}.get(name, name)
                add(name, msg, text, h.start(), h.end(), fix)
        # em dashes in body prose. The bullet separator "<b>Term</b> — text" is site convention:
        # on a bold-led list item the first dash is the separator and is skipped.
        # (the bold may wrap a link, and the same convention appears in a <p> that opens a
        # bulleted list of places, so any element that OPENS with <b>…</b> — qualifies)
        # the term may be bold or a link: the guides link the place name instead of bolding it
        sep = re.match(r"\s*(?:<[^>]+>\s*)*<(?:b|a)\b.*?</(?:b|a)>(?:\s*<[^>]+>)*\s*—", inner, re.S) is not None
        for h in re.finditer("—", text):
            if sep and h.start() == text.find("—"):
                continue
            add("em-dash", "Em dash in body prose. Use a comma, a period or an en dash for a range.",
                text, h.start(), h.end(), None)
        for h in BRACKET.finditer(text):
            what = h.group(0)
            if what.strip("[] ") == "":
                add("placeholder", "Empty [ ] left to fill.", text, h.start(), h.end(), None, "error")
            elif what.upper() == "[PHOTO]":
                continue                                 # the editor's own photo slot marker
            else:
                add("placeholder", "Bracketed note still in the text.", text, h.start(), h.end(), None, "error")

    # ---- British spellings: americanize's own pass, with its guards, on the whole document
    for s, e, new, old in sorted(americanize.find(html)):
        # locate the visible text around it for the anchor: the word itself is unique enough
        # with 40 chars of context taken from the html with tags stripped
        ctx0 = _visible(html[max(0, s - 200):s]); ctx1 = _visible(html[e:e + 200])
        f = {"id": "ln%03d" % (n + 1), "kind": "british", "severity": "warn",
             "message": "British spelling: %s." % old, "fix": new,
             "anchor": {"quote": old, "before": ctx0[-40:], "after": ctx1[:40]}}
        n += 1; out.append(f)

    # ---- paste artifacts: counted, one card, since they are attributes not words
    last = 0; hits = 0
    for m in strip_paste_artifacts.BLOCK.finditer(html):
        hits += len(strip_paste_artifacts.ART.findall(html[last:m.start()])); last = m.end()
    hits += len(strip_paste_artifacts.ART.findall(html[last:]))
    if hits:
        out.append({"id": "ln%03d" % (n + 1), "kind": "paste", "severity": "warn", "anchor": None,
                    "message": "%d Google-Docs inline style(s) (font-size:1rem / color:rgb(28,40,33)) that fight the page CSS. "
                               "The editor strips them on save; tools/strip_paste_artifacts.py does the same on disk." % hits})
        n += 1

    # ---- Maps search placeholders: counted; the Resolve button is the fix
    q = MAPS_SEARCH.findall(html)
    if q:
        out.append({"id": "ln%03d" % (n + 1), "kind": "maps", "severity": "info", "anchor": None, "count": len(q),
                    "message": "%d Google Maps link(s) still point at a search instead of the place. Resolve maps turns them into pins." % len(q)})
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    res = check((ROOT / sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(res, indent=1, ensure_ascii=False) if "--json" in sys.argv else
          "\n".join("%-12s %-8s %s   %r" % (f["kind"], f["severity"], f["message"][:60], (f.get("anchor") or {}).get("quote", "")[:50]) for f in res)
          + "\n%d finding(s)" % len(res))
