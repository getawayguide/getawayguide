"""Kevin's margin comments as data: category, photo or not, and where in the article each one sits.

Read-only. Shared by tools/shadow_retrieve.py and tools/shadow_backtest.py. Author "Kevin" only; Shadow's sr- threads are
never comment records (kevin_reviewer_corpus.py keeps only author == "Kevin").
"""
import bisect
import json
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from article_blocks import blocks_of, compare_key, section_kind, article_type   # noqa: E402

OUT_DIR = os.path.join(ROOT, ".tmp", "kevin_reviewer")
CORPUS = os.path.join(OUT_DIR, "corpus.jsonl")
SECTIONS_CACHE = os.path.join(OUT_DIR, "comment_sections.json")

# First match wins. The categories are model.md's §1 taxonomy; the order is the one that classified its 1,042 threads best.
CATEGORY_RULES = [
    ("photos", r"\bphotos?\b|\bpictures?\b|\bpics?\b|\bimages?\b|\bcaptions?\b|\bthumbnail|subtitle format|\bcrop\b|\bsatellite\b"),
    ("maps", r"\bmaps?\b(?! link)|route map|\bpins?\b|\bzoom\b|\blegend\b"),
    ("link", r"\blinks?\b|\bhyperlink|\bwiki\b|\bwikipedia\b|^url\b|anchor|field notes\b.*\b(?:section|link)"),
    ("formatting", r"\bfont\b|\bspacing\b|extra (?:line|row|space)|\bnumbering\b|\baccent\b|\bbold\b|\bitalic|\bindent|\bcolor\b|"
                   r"line break|blank line|\bTOC\b|contents box"),
    ("fill", r"\bfill\b|fill out|fill in|look up|convert to|\busd\b|how (?:long|much|many|far|tall|old)|add (?:the )?(?:price|cost|time|hours)|"
             r"^\[ ?\]$|what (?:year|time|day)|magnitude"),
    ("fact-check", r"fact.?check|is this (?:true|right|correct|accurate|the official)|are these (?:true|right|correct)|\baccurate\b|seems (?:really |very )?(?:high|low|off|wrong)|"
                   r"are you sure|double.?check|\bverify\b|isn'?t (?:this|it|that)\b|\bAD or BC\b"),
    ("condense", r"\bcondense|\bshorten|\btrim\b|\bcut (?:this|down|it)|too (?:long|wordy|detailed)|\bsimplify\b|tighten|much longer than|\bbrief(?:er)?\b"),
    ("structure", r"\bmove\b|\bfold\b|\bmerge\b|\bcombine\b|\bsplit\b|break (?:into|up|this)|\bbullets?\b|separate section|\breorder\b|"
                  r"\bsection\b|\bsubsection\b|\bparagraphs? (?:break|up)|\bbefore the\b|\bafter the\b|put this|\bplace this\b|\bswap\b"),
    ("expand", r"\bexpand\b|beef up|add (?:more|some|a bit|details?|history|another|an? (?:line|sentence|paragraph|section|bullet)|sentence|paragraph|a little)|more detail|write (?:\d|a|an|another|one|two|up)\b|"
               r"make (?:this|it) longer|feels? (?:a bit |very |too )?(?:light|thin)|flesh out|elaborate|\bsell\b|comment on|tell more|say more"),
    ("repetition", r"repetit|repeated|\bredundan|use .* too much|overus|said this|already (?:said|mentioned|covered)|too many times"),
    ("tone", r"\btone\b|too (?:negative|nerdy|positive|salesy|formal|history)|not encouraging|flattering|personal experience|sounds like|\bvoice\b"),
    ("wording", r"\breword|\brewrite\b|\brephrase|\bsynonym|better (?:word|wording|phrase|lead)|\bwording\b|\bphrasing\b|another word|awkward|odd\b|"
                r"\bword\b|\bgrammar\b|\btypo\b|\bspelling\b|\bplural\b|\bcapitali[sz]e\b|\bremove\b|\bdelete\b|\bdon'?t need\b"),
]
_RULES = [(n, re.compile(p, re.I)) for n, p in CATEGORY_RULES]
CATEGORIES = [n for n, _ in CATEGORY_RULES] + ["content"]


def category(text):
    """model.md's category for a comment; whatever matches no rule is his dictated content."""
    t = re.sub(r"^\[Shadow reviewer\]\s*", "", text or "")
    for name, rx in _RULES:
        if rx.search(t):
            return name
    return "content"


def is_photo(text, quote=""):
    return category(text) == "photos" or bool(re.fullmatch(r"\W*\[?\s*(?:\w+ )?photos?\W*\]?\W*", quote or "", re.I))


def read_corpus(kinds=("comment", "edit")):
    out = []
    with open(CORPUS, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["kind"] in kinds:
                out.append(r)
    return out


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else 0.0


# ---------------------------------------------------------------- finding a comment in a version of its article
class Located:
    """A draft's blocks joined into one searchable string, with a way back from offset to block."""

    def __init__(self, html):
        self.blocks = blocks_of(html)
        keys = [compare_key(b["text"]) for b in self.blocks]
        self.starts, pos, parts = [], 0, []
        for k in keys:
            self.starts.append(pos)
            parts.append(k)
            pos += len(k) + 1
        self.text = "\n".join(parts)
        self.raw_plain = compare_key(re.sub(r"<[^>]+>", " ", html))

    def block_at(self, pos):
        return bisect.bisect_right(self.starts, pos) - 1

    def find_near(self, before, after):
        """index of the block around an anchor's surrounding text, or None"""
        for piece, side in ((compare_key(before)[-45:], "b"), (compare_key(after)[:45], "a")):
            if len(piece) >= 18:
                i = self.text.find(piece)
                if i >= 0:
                    return self.block_at(i + len(piece) - 1 if side == "b" else i)
        return None

    def find(self, quote, before="", after=""):
        """(start, end, block index) of a quote, the anchor context picking between repeats; None if it is not in the prose."""
        q = compare_key(quote)
        if len(q) < 2:
            return None
        hits, i = [], self.text.find(q)
        while i >= 0:
            hits.append(i)
            i = self.text.find(q, i + 1)
        if not hits and len(q) > 60:                   # a selection crossing a block boundary
            q = compare_key(quote)[:60]
            i = self.text.find(q)
            hits = [i] if i >= 0 else []
            if hits:
                return hits[0], hits[0] + len(compare_key(quote)), self.block_at(hits[0])
        if not hits:
            return None
        if len(hits) > 1 and (before or after):
            bk, ak = compare_key(before)[-40:], compare_key(after)[:40]

            def fit(p):
                score = 0
                for n in (40, 25, 12):
                    if bk and self.text[max(0, p - n):p].replace("\n", " ").strip().endswith(bk[-n:].strip()):
                        score += 2
                    if ak and self.text[p + len(q):p + len(q) + n].replace("\n", " ").strip().startswith(ak[:n].strip()):
                        score += 2
                return score
            hits.sort(key=lambda p: -fit(p))
        return hits[0], hits[0] + len(q), self.block_at(hits[0])


def _version_list(key, ctx):
    """[(time, loader)] of every copy of this article we have, git and round baselines."""
    import kevin_edits
    out = []
    for v in ctx["timeline"].get(key, []):
        out.append((v["t"], lambda v=v: ctx["blobs"].read(v["sha"], v["path"])))
    for r in ctx["rounds"].get(key, []):
        if r["baseline"]:
            def load(p=r["baseline"]):
                with open(p, encoding="utf-8") as f:
                    return f.read()
            out.append((r["t"], load))
    out.sort(key=lambda x: x[0])
    return out, kevin_edits


def load_anchors():
    """{(key, thread id): (before, after)} from the comment store; corpus.jsonl keeps only the quote."""
    import glob as _glob
    out = {}
    for f in _glob.glob(os.path.join(ROOT, ".tmp", "redline", "_comments", "*.json")):
        name = os.path.basename(f)
        if ".before" in name or name.startswith("test__"):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        for t in data.get("threads", []):
            a = t.get("anchor") or {}
            out[(name[:-5], t.get("id"))] = (a.get("before") or "", a.get("after") or "")
    return out


def locate_all(comments):
    """{comment id: {"section", "sub", "section_kind", "block", "tag", "status"}}, cached in comment_sections.json.

    Each quote is looked for in the copy of the article nearest before the comment was written, then outward."""
    anchors = load_anchors()
    for c in comments:
        c.setdefault("anchor_before", anchors.get((c["key"], c.get("thread")), ("", ""))[0])
        c.setdefault("anchor_after", anchors.get((c["key"], c.get("thread")), ("", ""))[1])
    try:
        with open(SECTIONS_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    todo = [c for c in comments if c["id"] not in cache]
    if not todo:
        return cache
    import kevin_edits
    ctx = {"timeline": kevin_edits.timeline(), "rounds": kevin_edits.load_rounds(), "blobs": kevin_edits.Blobs()}
    by_key = {}
    for c in todo:
        by_key.setdefault(c["key"], []).append(c)
    for key, items in by_key.items():
        versions, _ = _version_list(key, ctx)
        for other in {o for c in items for o in c.get("also_in", [])}:     # the Greece store quirk: one file holds two articles
            versions += _version_list(other, ctx)[0]
        versions.sort(key=lambda v: v[0])
        located = {}

        def version(i):
            if i not in located:
                try:
                    html = versions[i][1]()
                except OSError:
                    html = None
                located[i] = Located(html) if html else None
            return located[i]

        kind = article_type(key)
        for c in items:
            t = parse_time(c.get("created"))
            before = [i for i, v in enumerate(versions) if v[0] <= t][::-1]
            after = [i for i, v in enumerate(versions) if v[0] > t]
            res = {"section": "", "sub": "", "section_kind": "other", "block": None, "tag": "", "status": "not-found"}
            anchor_b, anchor_a = c.get("anchor_before", ""), c.get("anchor_after", "")
            for i in (before + after)[:14]:
                loc = version(i)
                if loc is None:
                    continue
                pos = loc.find(c.get("quote") or "", anchor_b, anchor_a)
                if pos:
                    b = loc.blocks[pos[2]]
                    res = {"section": b["section"], "sub": b["sub"], "section_kind": section_kind(b["sub"] or b["section"], kind, b["section"]),
                           "block": b["text"][:200], "tag": b["tag"], "status": "prose"}
                    break
                q = compare_key(c.get("quote") or "")
                if len(q) >= 2 and q in loc.raw_plain:
                    res["status"] = "not-prose"     # a caption, map label, hero or contents box
            if res["status"] == "not-found":
                # the words he selected were saved only in between; the text around the selection often survives
                for i in (before + after)[:14]:
                    loc = version(i)
                    pos = loc and loc.find_near(anchor_b, anchor_a)
                    if pos is not None and pos is not False:
                        b = loc.blocks[pos]
                        res = {"section": b["section"], "sub": b["sub"], "section_kind": section_kind(b["sub"] or b["section"], kind, b["section"]),
                               "block": b["text"][:200], "tag": b["tag"], "status": "prose-near"}
                        break
            cache[c["id"]] = res
    ctx["blobs"].close()
    tmp = SECTIONS_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, SECTIONS_CACHE)
    return cache
