#!/usr/bin/env python3
"""Does this article sound like Kevin? Measured against his live pages, not against taste.

The 2026-08-09 register audit found that prose can pass every qualitative check in the style
guide and still measure wrong: my drafts had half his contractions, half his numerals, never
said "you'll", and used "genuinely" at seven times his rate. So the score here is arithmetic.
Every live page under the site root (not archive/, not Drafts/) is the baseline; a draft is
scored on how far each signal sits from that baseline, per 1,000 words.

    python tools/voice_check.py "Drafts/.Full Articles/armenia/orgov-observatory.html"
    python tools/voice_check.py --rebuild          # recompute the baseline after new pages go live

The review margin shows the result as one line under the toolbar ("Voice 84 · ...") and the
anchored findings (a cliche he never writes, a third "genuinely") as cards beside the text.
"""
import html as htmlmod
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".tmp" / "voice_baseline.json"

TAG = re.compile(r"<[^>]+>")
BLOCK = re.compile(r"<(script|style|svg|noscript|nav|header|footer)\b.*?</\1>", re.S | re.I)
PROSE = re.compile(r"<(p|li|h2|h3|h4|figcaption|blockquote)\b[^>]*>(.*?)</\1>", re.S | re.I)
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*|\d[\d,.:]*")
SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")

# the contractions he actually writes (register audit: did not 96%, you will 95%, you are 94%,
# do not 93%, I have 89%); it's / that's / there's are left out because he mostly expands those
CONTRACTION = re.compile(r"\b(?:\w+n[’']t|you[’']ll|you[’']re|you[’']ve|I[’']ve|I[’']m|I[’']ll|we[’']re|we[’']ve|they[’']ve|I[’']d|you[’']d|we[’']ll|they[’']ll|they[’']re|he[’']s|she[’']s|who[’']s|what[’']s|here[’']s|let[’']s)\b", re.I)
SECOND_PERSON = re.compile(r"\byou(?:[’']ll|[’']re|[’']ve|[’']d|r)?\b", re.I)
NUMERAL = re.compile(r"(?<![\w-])[$€£]?\d[\d,.:]*(?:%|am|pm|km|m|ft|h|hr|min)?(?![\w-])")
HEDGE = re.compile(r"\b(?:a bit|pretty|really|by far|surprisingly|absolutely)\b", re.I)
HIGHLIGHT = re.compile(r"\bthe (?:highlight|main reason to come|catch is)\b", re.I)
GENUINELY = re.compile(r"\bgenuinely\b", re.I)

# words he does not write. The style guide's own list, plus the brochure register a draft
# picks up from tour sites and Wikipedia. Each carries what he reaches for instead.
CLICHE = [
    # Calibrated against the live pages (111k words, 2026-09-26): the style guide's list said
    # he avoids "stunning", "charming", "vibrant", "iconic", "world-class", "picturesque", and
    # the corpus disagrees (0.12-0.37 per 1k each), so those are NOT here. Everything below
    # he writes fewer than 0.06 times per 1k words, i.e. essentially never.
    (r"\bnestled\b", "sits / is tucked"), (r"\bboasts?\b|\bboasting\b", "has"),
    (r"\bbreathtaking\b", "say what it looks like"), (r"\bunique experience\b", "say what happens"),
    (r"\bbustling\b", "busy / packed"), (r"\bmust-see\b", "don't miss / worth the stop"),
    (r"\bquaint\b", "small / old"), (r"\bimmerse\b|\bimmersive\b", "spend time in"),
    (r"\bawe-inspiring\b", "say what it looks like"), (r"\bmesmerizing\b", "say what it does"),
    (r"\bplethora\b", "plenty / a lot"), (r"\bdelve\b", "get into"), (r"\btapestry\b", "mix"),
    (r"\bwhether you(?:'re| are) [^.]{0,40} or\b", "say who it is for"),
    (r"\bfoodie\b", "if you like eating"), (r"\bwanderlust\b", "drop it"),
    (r"\bhidden gems?\b(?![^.]{0,30}\bunderrated\b)", "he says 'underrated' and names the place"),
]

SIGNALS = [
    # key,            label,                        how to count (per 1k words unless noted)
    ("sent_median",  "median sentence, words",     None),
    ("contractions", "contractions /1k",           CONTRACTION),
    ("numerals",     "numbers /1k",                NUMERAL),
    ("second",       "you / you'll /1k",           SECOND_PERSON),
    ("hedges",       "a bit / pretty / really /1k", HEDGE),
    ("highlight",    "'the highlight' /1k",        HIGHLIGHT),
    ("genuinely",    "genuinely /1k",              GENUINELY),
]


def prose_text(html):
    """Every prose block's visible text, one per line. Nav, header, footer and code are gone."""
    masked = BLOCK.sub(" ", html)
    out = []
    for m in PROSE.finditer(masked):
        inner = m.group(2)
        if re.search(r"\n\s{2,}", TAG.sub("", inner)):
            continue                                 # multi-line = indented markup, not prose
        t = htmlmod.unescape(TAG.sub("", inner)).strip()
        if t and t != "[PHOTO]":
            out.append(re.sub(r"\s+", " ", t))
    return out


def measure(blocks):
    text = " ".join(blocks)
    words = len(WORD.findall(text))
    if not words:
        return None
    sents = [s for b in blocks for s in SENT_END.split(b) if len(WORD.findall(s)) >= 3]
    k = 1000.0 / words
    m = {"words": words,
         "sent_median": statistics.median(len(WORD.findall(s)) for s in sents) if sents else 0}
    for key, label, rx in SIGNALS:
        if rx is not None:
            m[key] = round(len(rx.findall(text)) * k, 2)
    m["cliches"] = sum(len(re.findall(rx, text, re.I)) for rx, _ in CLICHE)
    return m


def live_pages():
    out = []
    for p in sorted(ROOT.glob("*/*.html")):
        top = p.parts[len(ROOT.parts)]
        if top in ("archive", "Drafts", "Images", "tools", "workflows", "_content", "assets", "fonts", ".tmp", "node_modules"):
            continue
        if p.name == "index.html":               # a country page is a card grid, not prose
            continue
        out.append(p)
    return out


def page_kind(path_or_html):
    """Field notes are bullets (16-word 'sentences', few contractions); the long-form guides
    run 21-word sentences and twice the contractions. Comparing a guide to the field notes
    reported the format as a voice fault, so each kind has its own baseline."""
    s = str(path_or_html)
    if s.endswith("field-notes.html"):
        return "field-notes"
    return "article"


def baseline(kind="article", rebuild=False):
    pages = live_pages()
    stamp = "%d:%d" % (len(pages), max((p.stat().st_mtime_ns for p in pages), default=0))
    if not rebuild and BASELINE.exists():
        try:
            b = json.loads(BASELINE.read_text(encoding="utf-8"))
            if b.get("stamp") == stamp and kind in b:
                return b[kind]
        except Exception:
            pass
    out = {"stamp": stamp}
    for k in ("article", "field-notes"):
        mine = [p for p in pages if page_kind(p) == k]
        blocks = []
        for p in mine:
            blocks += prose_text(p.read_text(encoding="utf-8", errors="replace"))
        m = measure(blocks) or {}
        m["cliches"] = round(m.get("cliches", 0) * 1000.0 / max(1, m.get("words", 1)), 2)   # per 1k, like the rest
        out[k] = {"pages": len(mine), "metrics": m}
    BASELINE.parent.mkdir(exist_ok=True)
    BASELINE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out[kind]


# how far from his number a signal can drift before it costs points: a fraction of his value
TOLERANCE = {"sent_median": 0.15, "contractions": 0.5, "numerals": 0.5, "second": 0.6,
             "hedges": 0.7, "highlight": 1.0, "genuinely": 1.0}
WEIGHT = {"sent_median": 20, "contractions": 15, "numerals": 20, "second": 15, "hedges": 8,
          "highlight": 4, "genuinely": 8}


def score(html):
    """-> {score, words, lines:[{key,label,value,base,ok,note}], findings:[anchored]}"""
    blocks = prose_text(html)
    m = measure(blocks)
    if not m:
        return {"score": None, "words": 0, "lines": [], "findings": []}
    b = baseline()["metrics"]
    lines, total = [], 100.0
    # a rate per 1,000 words swings on a short piece (one extra "you'll" in 800 words is
    # +1.25/1k), so the tolerance widens as the sample shrinks below 1,500 words
    size = max(1.0, (1500.0 / m["words"]) ** 0.5)
    for key, label, _ in SIGNALS:
        v, base = m.get(key, 0), b.get(key, 0)
        tol = TOLERANCE[key] * max(base, 0.5) * size
        off = abs(v - base)
        # over the tolerance the penalty grows to the full weight at 3x tolerance
        pen = 0 if off <= tol else min(1.0, (off - tol) / (2 * tol)) * WEIGHT[key]
        if key == "genuinely" and v <= base:
            pen = 0                                   # fewer is never a fault
        total -= pen
        lines.append({"key": key, "label": label, "value": v, "base": base, "ok": pen == 0,
                      "note": "" if pen == 0 else ("high" if v > base else "low")})
    cl = m["cliches"]
    total -= min(20, cl * 5)
    lines.append({"key": "cliches", "label": "words he doesn't use", "value": cl, "base": 0, "ok": cl == 0, "note": "" if not cl else "cut"})
    findings = []
    text = "\n".join(blocks)
    for rx, instead in CLICHE:
        for h in re.finditer(rx, text, re.I):
            findings.append({"kind": "voice", "severity": "warn",
                             "message": "'%s' isn't a word he uses; instead: %s." % (h.group(0), instead),
                             "anchor": {"quote": h.group(0), "before": text[max(0, h.start() - 40):h.start()], "after": text[h.end():h.end() + 40]}})
    gen = list(GENUINELY.finditer(text))
    for h in gen[1:]:
        findings.append({"kind": "voice", "severity": "low",
                         "message": "'genuinely' again (%d in this article; he averages about one). Try really / pretty / by far, or nothing." % len(gen),
                         "anchor": {"quote": h.group(0), "before": text[max(0, h.start() - 40):h.start()], "after": text[h.end():h.end() + 40]}})
    return {"score": max(0, round(total)), "words": m["words"], "lines": lines, "findings": findings,
            "baseline_pages": baseline()["pages"]}


def summary(r):
    if r["score"] is None:
        return "Voice: no prose to measure."
    parts = ["Voice %d/100" % r["score"], "%d words" % r["words"]]
    for l in r["lines"]:
        if l["key"] == "cliches":
            if l["value"]:
                parts.append("%d word%s he doesn't use" % (l["value"], "" if l["value"] == 1 else "s"))
            continue
        if not l["ok"]:
            parts.append("%s %s (his %s)" % (l["label"], l["value"], l["base"]))
    return " · ".join(parts)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--rebuild" in sys.argv:
        b = baseline(rebuild=True)
        print("baseline from %d live pages, %d words:" % (b["pages"], b["metrics"]["words"]))
        for k, v in b["metrics"].items():
            print("  %-14s %s" % (k, v))
        sys.argv = [a for a in sys.argv if a != "--rebuild"]
    if len(sys.argv) > 1:
        r = score((ROOT / sys.argv[1]).read_text(encoding="utf-8"))
        print(summary(r))
        for l in r["lines"]:
            print("  %s %-28s %8s   his %s" % ("  " if l["ok"] else "!!", l["label"], l["value"], l["base"]))
        for f in r["findings"]:
            print("  - %s   %r" % (f["message"], f["anchor"]["quote"]))
