"""Print Kevin's real past comments and hand edits for one kind of section, for a reviewer or writer to read before working.

    python tools/shadow_retrieve.py --type city-guide --section "Getting to Skopje"
    python tools/shadow_retrieve.py --type itinerary --section why-visit --limit 30
    python tools/shadow_retrieve.py --type top-10 --section intro --exclude-articles top-10-armenia,armenia-itinerary

--type      city-guide | itinerary | top-10 | field-notes
--section   a heading ("Where to Eat") or a section kind: {kinds}
--exclude-articles   comma list of articles or corpus keys held out (a backtest never lets a test article teach it)
--json      records instead of the short lines

Sources: tools/kevin_reviewer_corpus.py's corpus.jsonl, author "Kevin" only, kinds `comment` and `edit`. Photo comments are
left out, and so are comments on non-prose (captions, maps, the hero). Same section kind first, then newest; other article
types count for less, since his itinerary habits are not his city-guide habits. Capped so the output stays small.
"""
import argparse
import json
import math
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from article_blocks import SECTION_KINDS, section_kind, compare_key   # noqa: E402
import kevin_comments as kc                                          # noqa: E402

HALF_LIFE_DAYS = 30.0     # a comment a month old counts half as much as today's; his taste moves fast (model.md §2)
TYPE_WEIGHT = {True: 1.0, False: 0.45}
GENERIC = re.compile(r"^(?:link|google maps link|wiki link|reword|rewrite|synonym|fill out|expand|condense)\W*$", re.I)


def wanted_kind(section, article_type):
    text = section.strip()
    if text.lower().replace(" ", "-") in SECTION_KINDS:
        return text.lower().replace(" ", "-"), True
    return section_kind(text, article_type, text), False


def load(exclude):
    """comments with their section attached, and edit pairs; excluded articles and photo comments already out"""
    comments = kc.read_corpus(("comment",))
    comments = [c for c in comments if c.get("thread_author") == "Kevin"]
    located = kc.locate_all(comments)
    items = []
    for c in comments:
        if c["key"] in exclude or c["key"].split("__")[-1] in exclude:
            continue
        if set(c.get("also_in", [])) & exclude:
            continue
        if kc.is_photo(c.get("text", ""), c.get("quote", "")):
            continue
        loc = located.get(c["id"]) or {}
        if loc.get("status") == "not-prose":
            continue
        items.append(dict(c, section=loc.get("section") or "", subsection=loc.get("sub") or "",
                          section_kind=loc.get("section_kind") or "other", article_type=_type(c["key"]),
                          ask=kc.category(c.get("text", ""))))
    for e in kc.read_corpus(("edit",)):
        if e["key"] in exclude or e["key"].split("__")[-1] in exclude:
            continue
        items.append(e)
    return items


def _type(key):
    from article_blocks import article_type
    return article_type(key)


def score(r, kind, heading, article_type, now, exact_kind):
    same_type = r.get("article_type") == article_type
    s = 3.0 if r.get("section_kind") == kind else (0.5 if exact_kind else 0.3)
    head = compare_key(heading).lower()
    if head and head in (compare_key(r.get("subsection") or "").lower(), compare_key(r.get("section") or "").lower()):
        s += 1.5
    s *= TYPE_WEIGHT[same_type]
    age_days = max(0.0, (now - kc.parse_time(r.get("created"))) / 86400.0)
    s *= 0.5 ** (age_days / HALF_LIFE_DAYS)
    if r["kind"] == "edit":
        if r.get("provenance") == "mixed":
            s *= 0.7
        if r.get("change") == "reworded":
            s *= 1.15                       # the most informative: the same sentence, his version
    elif GENERIC.match((r.get("text") or "").strip()):
        s *= 0.5                            # a bare "link" teaches little beyond the first couple
    return s


def pick(items, kind, heading, article_type, limit):
    now = time.time()
    exact = heading.strip().lower() == kind
    ranked = sorted(items, key=lambda r: -score(r, kind, heading, article_type, now, exact))
    # only items of this section kind or of the same article type earn a place; a thin section is left thin, not padded
    ranked = [r for r in ranked if r.get("section_kind") == kind or (r.get("article_type") == article_type and r.get("section_kind") in ("other", ""))]
    per_article, seen_generic, out = {}, 0, []
    max_per_article = max(5, limit // 3)
    for r in ranked:
        art = r["key"]
        if per_article.get(art, 0) >= max_per_article:
            continue
        if r["kind"] == "comment" and GENERIC.match((r.get("text") or "").strip()):
            seen_generic += 1
            if seen_generic > 3:
                continue
        per_article[art] = per_article.get(art, 0) + 1
        out.append(r)
        if len(out) >= limit:
            break
    return out


def short(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def line(r):
    art = r["key"].split("__")[-1]
    where = short(r.get("subsection") or r.get("section") or "", 34)
    date = (r.get("created") or "")[:10]
    if r["kind"] == "comment":
        return f"[comment] {date} {art} | {where} | on \"{short(r.get('quote'), 90)}\": {short(r.get('text'), 220)}"
    tag = "" if r.get("provenance") == "kevin" else " (less certain)"
    return f"[edit/{r.get('change')}{tag}] {date} {art} | {where}\n  was: {short(r.get('before'), 240) or '(nothing)'}\n  his: {short(r.get('after'), 240) or '(cut)'}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.format(kinds=", ".join(SECTION_KINDS)),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--type", required=True, choices=["city-guide", "itinerary", "top-10", "field-notes"])
    ap.add_argument("--section", required=True, help="a heading, or a section kind")
    ap.add_argument("--exclude-articles", default="", help="comma-separated articles held out")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    exclude = {x.strip() for x in a.exclude_articles.split(",") if x.strip()}
    kind, is_kind = wanted_kind(a.section, a.type)
    items = load(exclude)
    chosen = pick(items, kind, "" if is_kind else a.section, a.type, a.limit)
    out = sys.stdout.buffer
    if a.json:
        out.write(json.dumps({"section_kind": kind, "items": chosen}, ensure_ascii=False, indent=1).encode("utf-8") + b"\n")
        return
    head = (f"# Kevin's own history for: {a.type}, section kind '{kind}'"
            f"{' (from: ' + a.section + ')' if not is_kind else ''}: {len(chosen)} items, newest and closest first")
    body = [head] + [line(r) for r in chosen]
    out.write(("\n".join(body) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
