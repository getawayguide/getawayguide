"""How far a draft is from the version Kevin approved: the draft-distance metric of .tmp/kevin_reviewer/program.md.

    python tools/draft_distance.py <draft.html> <final.html>          one line per metric
    python tools/draft_distance.py <draft.html> <final.html> --json   the numbers as JSON

Reported, on body prose only (no photos, captions, alt text, maps, nav or contents boxes; see tools/article_blocks.py):
  edit_ratio        word-level edit distance from draft to final, over the longer of the two (0 is the same text, 1 is nothing in
                    common). A replaced word costs 1; a paragraph rewritten past recognition counts as replacement, not as a cut
                    plus an addition; text that only moved costs nothing.
  sections_added    headings in the final that the draft lacks (h2/h3, renumbering ignored)
  sections_removed  headings in the draft the final dropped
  facts_changed     numbers, prices and durations that differ inside a matched paragraph
  facts_added / facts_removed   the same tokens in whole paragraphs only one side has
  links_missing     links in the final's prose the draft doesn't have (Google Maps compared by place name, own pages by path)
"""
import argparse
import difflib
import json
import os
import re
import sys
from collections import Counter
from urllib.parse import unquote, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from article_blocks import blocks_of, compare_key, HEADING_TAGS   # noqa: E402
from kevin_edits import diff_blocks                               # noqa: E402

UNIT = (r"(?:hours?|hrs?|minutes?|mins?|days?|nights?|weeks?|months?|years?|km|kilometers?|miles?|meters?|feet|ft|m\b|%|"
        r"stars?|percent|people|floors?|stories)")
FACT = re.compile(r"(?:[$€£]\s?\d[\d,.]*)|(?:\d[\d,.]*\s?(?:AMD|USD|EUR|TRY|MKD|ALL|lira|denar\w*|dram\w*|euros?|dollars?))|"
                  r"(?:\d[\d,.]*(?:[–-]\d[\d,.]*)?\s?" + UNIT + r")|(?:\b\d{1,2}(?::\d{2})?\s?(?:AM|PM|am|pm)\b)|(?:\b\d{4}\b)", re.I)


def facts(text):
    out = Counter()
    for m in FACT.finditer(text):
        out[re.sub(r"\s+", " ", m.group(0).lower().replace(",", "")).rstrip(".")] += 1
    return out


def norm_link(href):
    href = (href or "").strip()
    if not href or href.startswith(("mailto:", "javascript:")):
        return None
    if href.startswith("#"):
        return href
    if "google." in href and "/maps" in href:
        m = re.search(r"/maps/place/([^/@?]+)", href)
        if m:
            return "maps:" + unquote(m.group(1)).replace("+", " ").lower().strip()
        m = re.search(r"[?&](?:query|q)=([^&]+)", href)
        return "maps:" + unquote(m.group(1)).replace("+", " ").lower().strip() if m else "maps:?"
    if re.match(r"https?://", href):
        u = urlparse(href)
        return (u.netloc.lower().replace("www.", "") + u.path.rstrip("/")).lower()
    return re.sub(r"^(?:\.\./|\./)+", "", href).lower()


def _heading_set(blocks):
    out = {}
    for b in blocks:
        if b["tag"] in ("h2", "h3"):
            k = re.sub(r"^\s*\d+(?:[–-]\d+)?[.)]?\s+", "", compare_key(b["text"]).lower())
            out[k] = b["text"]
    return out


def _word_changes(a, b):
    wa, wb = compare_key(a).split(), compare_key(b).split()
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def distance(html_draft, html_final):
    ba, bb = blocks_of(html_draft), blocks_of(html_final)
    words_a = sum(len(b["text"].split()) for b in ba)
    words_b = sum(len(b["text"].split()) for b in bb)
    changed = cut_words = added_words = fact_changed = fact_added = fact_removed = 0
    fact_examples = []
    for a, b in diff_blocks(ba, bb):
        if a and b:
            changed += _word_changes(a["text"], b["text"])
            fa, fb = facts(a["text"]), facts(b["text"])
            diff = max(sum((fa - fb).values()), sum((fb - fa).values()))
            fact_changed += diff
            if diff and len(fact_examples) < 8:
                fact_examples.append([sorted((fa - fb).elements()), sorted((fb - fa).elements())])
        elif a:
            cut_words += len(a["text"].split())
            fact_removed += sum(facts(a["text"]).values())
        else:
            added_words += len(b["text"].split())
            fact_added += sum(facts(b["text"]).values())
    # a paragraph with no partner on the other side is mostly a rewrite: count it as substitution, like a word edit distance
    changed += max(cut_words, added_words)
    ha, hb = _heading_set(ba), _heading_set(bb)
    la = {norm_link(h) for x in ba for h in x["links"]} - {None}
    lb = {norm_link(h) for x in bb for h in x["links"]} - {None}
    missing = sorted(lb - la)
    return {
        "words_draft": words_a, "words_final": words_b,
        "words_changed": changed, "edit_ratio": round(changed / max(words_a, words_b), 4) if max(words_a, words_b) else None,
        "sections_added": sorted(hb[k] for k in hb.keys() - ha.keys()),
        "sections_removed": sorted(ha[k] for k in ha.keys() - hb.keys()),
        "facts_changed": fact_changed, "facts_added": fact_added, "facts_removed": fact_removed,
        "fact_examples": fact_examples,
        "links_final": len(lb), "links_missing": len(missing), "links_missing_list": missing[:40],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft")
    ap.add_argument("final")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    with open(a.draft, encoding="utf-8") as f:
        d = f.read()
    with open(a.final, encoding="utf-8") as f:
        fin = f.read()
    r = distance(d, fin)
    if a.json:
        sys.stdout.buffer.write(json.dumps(r, ensure_ascii=False, indent=1).encode("utf-8") + b"\n")
        return
    lines = [
        f"edit ratio        {r['edit_ratio']} ({r['words_changed']} words edited; draft {r['words_draft']}, final {r['words_final']})",
        f"sections added    {len(r['sections_added'])}: {r['sections_added'][:12]}",
        f"sections removed  {len(r['sections_removed'])}: {r['sections_removed'][:12]}",
        f"facts             {r['facts_changed']} changed, {r['facts_added']} added, {r['facts_removed']} removed; e.g. {r['fact_examples'][:4]}",
        f"links missing     {r['links_missing']} of {r['links_final']}: {r['links_missing_list'][:12]}",
    ]
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
