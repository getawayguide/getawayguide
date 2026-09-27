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
  repetition                  two paragraphs in a row opening with the same words, three
                              sentences starting the same way, a notable word twice in a
                              breath, the same link twice in one sentence: what his review
                              comments catch by hand ("The next stop is" twice, "up here" twice)
  voice_check                 words he never writes, "genuinely" past the first; check_full()
                              returns the register score beside the findings

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
import prose_rules           # noqa: E402  the em-dash judgement, shared with lint_site
import voice_check           # noqa: E402  the register score and the words he doesn't use

TAG = re.compile(r"<[^>]+>")
BLOCK = re.compile(r"<(script|style|svg|noscript|figure)\b.*?</\1>", re.S | re.I)
PROSE = re.compile(r"<(p|li|h1|h2|h3|h4|figcaption|blockquote)\b[^>]*>(.*?)</\1>", re.S | re.I)
MAPS_SEARCH = re.compile(r'href="https://www\.google\.com/maps/search/\?api=1&(?:amp;)?query=([^"]+)"')
BRACKET = re.compile(r"\[(?!\d+\])[^\[\]\n]{0,120}\]")     # [ ], [PHOTO], [fill this in]; not [1] footnotes


SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
WORDS = re.compile(r"[A-Za-z][A-Za-z'’-]*")
# long words that carry no meaning of their own, so a repeat of one is not a repetition
COMMON = set("""through because another between without around before against however
whether although several something anything everything nothing during towards toward
throughout despite instead perhaps already usually especially actually probably
different similar various certain whatever whenever wherever themselves yourself
himself herself ourselves including together sometimes somewhere everyone anywhere
morning evening minutes""".split())


def _repetition(blocks, add):
    """The repeats a reader notices and a spell-checker never does."""
    prev_open = None
    for kind, text in blocks:
        ws = WORDS.findall(text)
        # 1. two paragraphs in a row that open with the same three words
        opener = " ".join(w.lower() for w in ws[:3])
        if kind == "p" and opener and opener == prev_open and len(ws) >= 6:
            head = " ".join(ws[:3])
            add("repetition", "Opens with the same words as the paragraph before (\u201c%s\u201d)." % head,
                text, 0, len(head), None, "low")
        prev_open = opener if kind == "p" else None
        # 2. three sentences in a row that start the same way
        sents = SENT_SPLIT.split(text)
        run = 1
        for i in range(1, len(sents)):
            a = " ".join(w.lower() for w in WORDS.findall(sents[i - 1])[:2])
            b = " ".join(w.lower() for w in WORDS.findall(sents[i])[:2])
            if a and a == b:
                run += 1
                if run == 3:
                    at = text.find(sents[i])
                    add("repetition", "Three sentences in a row start with \u201c%s\u201d." % b, text, at, at + min(len(sents[i]), 24), None, "low")
            else:
                run = 1
        # 3. a notable word used twice within a breath (15 words); proper nouns excluded
        last = {}
        for m in WORDS.finditer(text):
            w = m.group(0); lw = w.lower()
            if len(lw) < 7 or lw in COMMON:
                continue
            if w[0].isupper() and not (m.start() == 0 or text[max(0, m.start() - 2):m.start()].strip() in (".", "!", "?", ":")):
                continue
            n = len(WORDS.findall(text[:m.start()]))
            if lw in last and n - last[lw] <= 7:      # 15 flagged the deliberate ones ("favorite monastery ... favorite monasteries")
                add("repetition", "\u201c%s\u201d twice within a few words." % w, text, m.start(), m.end(), None, "low")
            last[lw] = n


def _links_twice(inner, text, add):
    """the same href twice inside one sentence of a block (the second Yerevan in 'the drive
    home since the evening traffic into Yerevan'); the second can be plain text"""
    seen = set()
    for m in re.finditer(r'<a href="([^"]+)"[^>]*>(.*?)</a>', inner, re.S):
        href, label = m.group(1), _visible(m.group(2)).strip()
        if href.startswith("#") or not label:
            continue
        vis_before = _visible(inner[:m.start()])
        key = (href, len(SENT_SPLIT.split(vis_before)))
        if key in seen:
            at = len(vis_before)
            add("repetition", "Linked twice in one sentence; the second can be plain text.", text, at, at + len(label), None, "low")
        seen.add(key)


def _anchor(text, start, end):
    """The quote plus a little context either side, in the visible text, which is what the
    editor's comment anchors use. Context makes a repeated word land on the right one."""
    return {"quote": text[start:end], "before": text[max(0, start - 40):start], "after": text[end:end + 40]}


def _visible(inner):
    return htmlmod.unescape(TAG.sub("", inner))


def check(html, drafting=False):
    """-> [ {id, kind, message, anchor:{quote,before,after}, fix?: replacement} ]

    drafting=True adds the writing aids (repetition, the words he doesn't use): they are for
    the editor margin while a piece is being written. The routine that mails Kevin every two
    days runs the plain check: over the 50 live pages those aids raised 197 findings, and a
    report that long is one nobody reads (2026-09-27)."""
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
    blocks = []

    # ---- prose spans: the tools' own regexes, on the visible text of each prose element
    for m in PROSE.finditer(masked):
        inner = m.group(2)
        if re.search(r"\n\s{2,}", TAG.sub("", inner)):
            continue                                     # multi-line = indented markup
        text = _visible(inner)
        if not text.strip():
            continue
        blocks.append((m.group(1).lower(), text))
        if drafting:
            _links_twice(inner, text, add)
        for name, rx, repl in lint_prose.MECHANICAL:
            for h in rx.finditer(text):
                fix = rx.sub(repl, h.group(0)) if repl is not None else None
                msg = {"double-space": "Two spaces between words.",
                       "repeat-word": "Repeated word.",
                       "space-punct": "Space before punctuation.",
                       "space-period": "Space before a period. Usually a missing word, so not auto-fixed.",
                       "no-space-comma": "No space after the comma."}.get(name, name)
                add(name, msg, text, h.start(), h.end(), fix)
        # Em dashes in body prose, judged by prose_rules, the same call lint_site's pre-push
        # gate makes, so the editor margin and the gate never disagree about a bullet.
        # Headings are reported separately: CLAUDE.md lists them as covered, lint_site has
        # always skipped them, and that is Kevin's call rather than this tool's.
        heading = m.group(1).lower() in ("h1", "h2", "h3", "h4")
        # Bounded on purpose. Slicing `inner[:dash]` for every dash is quadratic, and a
        # paragraph holding thousands of them (a pathological paste) took the request down.
        # The rule only ever looks back to the block opening or the previous sentence, so a
        # window is the same answer; and past a dozen the reader needs the page, not the list.
        # The window has to reach the block's opening tag, because that is what tells a
        # `<b>Term</b> — description` bullet apart from a dash in a sentence. A fixed 600
        # characters did not: one Google Maps URL runs 400-500 characters, so a bullet whose
        # bold lead-in holds two links puts the <li> 875 characters back, the window opened
        # mid-URL, and the site's own bullet convention was reported as a voice error. Seek
        # the real opening and keep a generous cap so the scan stays linear.
        WINDOW, MAX = 4000, 12
        OPEN = re.compile(r"<(?:li|p|h[1-4])[^>]*>", re.I)
        seen = 0
        for h in re.finditer("—", inner):
            if seen >= MAX:
                add("em-dash", "More em dashes below this one on the same line.", text,
                    min(len(text) - 1, 0), min(len(text), 1), None, "low")
                break
            lo = max(0, h.start() - WINDOW)
            opens = list(OPEN.finditer(inner, lo, h.start()))
            back = inner[opens[-1].start():h.start()] if opens else inner[lo:h.start()]
            if prose_rules.em_dash_is_separator(back):
                continue
            seen += 1
            at = len(_visible(inner[:h.start()])) if len(inner) < 20000 else 0
            if heading:
                add("em-dash-heading", "Em dash in a heading. The gate allows these; CLAUDE.md lists headings as covered.",
                    text, at, at + 1, None, "low")
            else:
                add("em-dash", "Em dash in body prose. Use a comma, a period or an en dash for a range.",
                    text, at, at + 1, None)
        for h in BRACKET.finditer(text):
            what = h.group(0)
            if what.strip("[] ") == "":
                add("placeholder", "Empty [ ] left to fill.", text, h.start(), h.end(), None, "error")
            elif what.upper() == "[PHOTO]":
                continue                                 # the editor's own photo slot marker
            else:
                add("placeholder", "Bracketed note still in the text.", text, h.start(), h.end(), None, "error")

    if drafting:
        _repetition(blocks, add)
        # ---- the words he doesn't write, from the register check
        for f in voice_check.score(html)["findings"]:
            n += 1; f["id"] = "ln%03d" % n; out.append(f)

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


def check_full(html):
    """the findings plus the register score, for the margin's Voice line"""
    v = voice_check.score(html)
    v.pop("findings", None)
    v["summary"] = voice_check.summary(v)
    return {"findings": check(html, drafting=True), "voice": v}


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
