"""What Kevin's margin says about the Shadow reviewer: shared by tools/finished_articles.py, shadow_lessons.py, rule_test.py
and shadow_scorecard.py. Read-only: nothing here writes to the comment store, a round or an article.

The convention these functions apply is the one in .tmp/kevin_reviewer/brief.md Step 0.3: he replies to Shadow's threads.
  yes       he typed yes (or "agree", "good", "not a bad comment, but...")
  nuance    he typed an instruction instead ("should be hostel world", "2 hours"): the flag was real, the reply is the how
  no        he typed no, or "this is fine" and the like
  unclear   a typed question or a remark about the editor ("can't see what this refers to")
  tick      resolved by his own click with no reply (brief.md: a no, kept apart here because it isn't a typed answer)
  auto      closed by the editor or by us (resolved_by round / answered / shadow-tightened): never his answer
  deleted   gone from the store, in its `deleted` list
  unanswered still open with no reply: silence, which never counts as anything
"""
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
COMMENTS = os.path.join(ROOT, ".tmp", "redline", "_comments")
REDLINE = os.path.join(ROOT, ".tmp", "redline")
SPLIT = os.path.join(ROOT, ".tmp", "kevin_reviewer", "backtest", "split.json")

SHADOW = "Shadow reviewer"
KEVIN = "Kevin"
# Changes from these came from a tool or from silence. They are never his ask and never a Shadow hit (brief.md, program.md).
EXCLUDED_ORIGINS = {"shadow-loop", "shadow-consent", "draft_check"}
SITTING_GAP_H = 4                # same gap as tools/shadow_backtest.py
EXAM_FALLBACK = {"turkiye__antalya", "north-macedonia__bitola", "north-macedonia__ohrid",
                 "armenia__armenia-itinerary", "armenia__top-10-armenia", "armenia__field-notes"}

_NO = re.compile(r"^(no\b|nope\b|not needed|don'?t need|this (wording )?is fine|that is fine|it is fine|fine to\b|seems fine$|"
                 r"leave it|leave as|doesn'?t conflict|ok to skip|skip\b|i think you\b)")
_YES = re.compile(r"^(yes\b|yep\b|yeah\b|yup\b|agree\b|good\b|great\b|correct\b|sure\b|exactly\b|true\b|ok\b|okay\b|"
                  r"not a bad comment|fair\b)")
_UNCLEAR = re.compile(r"^(can'?t see|cannot see|not sure|what\b|why\b|huh\b)|\?\s*$")

# Shadow's 150-odd free-text categories, kept in a few families. First match wins; order matters ("fact check: price" is a price).
FAMILY_RULES = [
    ("photos", r"\bphotos?\b(?!\s+(?:lookup|conflict))"),
    ("add-detail", r"unnamed|lookup"),                    # "which hostel?": something to find out, not a link to add
    ("links", r"^link"),
    ("maps", r"^media|^map|route map"),
    ("prices", r"currency|price"),
    ("facts", r"fact doubt|fact check|source-conflict"),
    ("add-detail", r"^expand|^fill|^content|coverage|famous|left out|^origins|missing-sight"),
    ("facts", r"^fact"),
    ("repetition", r"repet|repeated from|same (verdict|blurb|advice|fact|name|phrase|wording)"),
    ("wording", r"^wording|^title|heading options|dish-bullet"),
    ("structure", r"^structure|^condense|overview-trim|^length|^tone|transport bullet"),
]
_FAMILIES = [(n, re.compile(p)) for n, p in FAMILY_RULES]
FAMILY_ORDER = ["links", "prices", "facts", "repetition", "wording", "add-detail", "structure", "maps", "photos", "other"]


def family(category):
    c = (category or "").strip().lower()
    for name, rx in _FAMILIES:
        if rx.search(c):
            return name
    return "other"


def reply_verdict(text):
    """yes | no | nuance | unclear for one reply of his"""
    t = re.sub(r"^\W+", "", (text or "").strip().lower())
    if not t:
        return "unclear"
    if _NO.search(t):
        return "no"
    if _YES.search(t):
        return "yes"
    return "unclear" if _UNCLEAR.search(t) else "nuance"


def kevin_replies(thread):
    return [r for r in thread.get("replies") or [] if isinstance(r, dict) and (r.get("author") or "").strip() == KEVIN]


def answer(thread, deleted=()):
    """{verdict, reply, when} for one Shadow thread. The first reply that says yes or no decides; failing that a nuance."""
    replies = kevin_replies(thread)
    if replies:
        verdicts = [reply_verdict(r.get("text")) for r in replies]
        for wanted in (("yes", "no"), ("nuance",), ("unclear",)):
            for r, v in zip(replies, verdicts):
                if v in wanted:
                    return {"verdict": v, "reply": r.get("text", ""), "when": r.get("created", "")}
    if thread.get("id") in deleted:
        return {"verdict": "deleted", "reply": "", "when": ""}
    if thread.get("resolved"):
        by = thread.get("resolved_by")
        if by in (None, "user"):                         # no stamp = from before the stamp existed: his click
            return {"verdict": "tick", "reply": "", "when": thread.get("at", "")}
        return {"verdict": "auto", "reply": "", "when": ""}
    return {"verdict": "unanswered", "reply": "", "when": ""}


def exam_keys():
    """the six articles the blind test holds back; never rule evidence"""
    try:
        with open(SPLIT, encoding="utf-8") as f:
            keys = {t["key"] for t in json.load(f).get("test", [])}
        return keys or set(EXAM_FALLBACK)
    except (OSError, ValueError, KeyError):
        return set(EXAM_FALLBACK)


def load_stores(comments_dir=None):
    """{key: store}, dropping the before-* copies, test__ stores and anything half-written"""
    stores = {}
    for f in sorted(glob.glob(os.path.join(comments_dir or COMMENTS, "*.json"))):
        name = os.path.basename(f)
        if ".before" in name or ".corrupt" in name or name.startswith("test__"):
            continue
        try:
            with open(f, encoding="utf-8-sig") as fh:
                s = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(s, dict) and isinstance(s.get("threads"), list):
            stores[name[:-5]] = s
    return stores


def deduped_threads(stores):
    """[(key, thread)] with a thread shared by two store files kept once, under the first key alphabetically (same as the corpus)"""
    seen, out = set(), []
    for key in sorted(stores):
        for t in stores[key].get("threads", []):
            if not isinstance(t, dict):
                continue
            sig = (t.get("id"), t.get("text"))
            if sig in seen:
                continue
            seen.add(sig)
            out.append((key, t))
    return out


def deleted_ids(store):
    return {x for x in store.get("deleted") or [] if isinstance(x, str)}


def tally(verdicts):
    """counts of each verdict, with the yes rate over typed answers only: (yes + nuance) / (yes + nuance + no)"""
    t = {v: 0 for v in ("yes", "nuance", "no", "unclear", "tick", "deleted", "auto", "unanswered")}
    for v in verdicts:
        t[v] = t.get(v, 0) + 1
    said = t["yes"] + t["nuance"] + t["no"]
    t["answered"] = said
    t["rate"] = (t["yes"] + t["nuance"]) / said if said else None
    return t


def pct(rate):
    return "n/a" if rate is None else "%d%%" % round(rate * 100)


def utf8_out(text):
    sys.stdout.buffer.write(text.encode("utf-8"))
