"""Shadow's lessons for the writer: the kinds of thing Shadow keeps flagging and Kevin keeps saying yes to, as candidate
writer rules, and Shadow's findings per draft as a score for that draft's writer.

    python tools/shadow_lessons.py                  writes .tmp/kevin_reviewer/shadow_lessons.md and prints its first lines
    python tools/shadow_lessons.py --json           the same numbers as JSON on stdout
    python tools/shadow_lessons.py --out FILE       write the report elsewhere

Counts only what Kevin did (the shadow_signals convention): a typed reply, or a round change that names the thread and that he
accepted or rejected. Never silence, a tick with no reply, or an auto-close, and never a change from origin shadow-loop,
shadow-consent or draft_check. Photo kinds are left out. The six exam articles are left out entirely.

A candidate is only a candidate. Before it joins writer_rules.md it has to pass tools/rule_test.py (see after_article.md).
Read-only apart from the report.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from article_blocks import blocks_of, prose_words                         # noqa: E402
import finished_articles as fa                                            # noqa: E402
import shadow_signals as ss                                               # noqa: E402

OUT = os.path.join(ROOT, ".tmp", "kevin_reviewer", "shadow_lessons.md")
THREAD_ID = re.compile(r"\bsr2?-[a-z0-9]{6,}")
MIN_YES, MIN_ARTICLES, MIN_RATE = 3, 2, 0.7
ALREADY_COVERED = {
    "links": "already a writer rule and draft_check.py --apply handles these",
    "prices": "already a writer rule (prices in local currency and USD)",
}


def round_signals(redline_dir=None):
    """{thread id: "accepted" | "rejected"}, from changes that name a Shadow thread and Kevin decided himself"""
    root = redline_dir or ss.REDLINE
    out = {}
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        prop, dec = os.path.join(root, name, "proposal.json"), os.path.join(root, name, "decisions.json")
        if not os.path.isfile(prop) or not os.path.isfile(dec):
            continue
        try:
            with open(prop, encoding="utf-8-sig") as f:
                p = json.load(f)
            with open(dec, encoding="utf-8-sig") as f:
                decisions = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(p, dict) or not isinstance(decisions, dict):
            continue
        for c in p.get("changes", []):
            if not isinstance(c, dict) or c.get("loop_source") or (c.get("origin") or p.get("origin")) in ss.EXCLUDED_ORIGINS:
                continue
            ids = set(THREAD_ID.findall(c.get("note") or "")) | {t for t in (c.get("threads") or []) if isinstance(t, str) and THREAD_ID.fullmatch(t)}
            if not ids:
                continue
            v = decisions.get(c.get("id"))
            accepted = v.get("accepted") if isinstance(v, dict) else v
            if accepted is None:
                continue
            for tid in ids:
                if accepted:
                    out[tid] = "accepted"
                else:
                    out.setdefault(tid, "rejected")
    return out


def signal(thread, deleted, rounds):
    """the thread's verdict once typed replies and his round decisions are counted, silence never"""
    a = ss.answer(thread, deleted)
    if a["verdict"] in ("yes", "nuance", "no", "unclear"):
        return a
    r = rounds.get(thread.get("id"))
    if r == "accepted":
        return {"verdict": "yes", "reply": "", "when": "", "via_round": True}
    if r == "rejected":
        return {"verdict": "no", "reply": "", "when": "", "via_round": True}
    return a


def gather(stores=None, redline_dir=None):
    stores = stores if stores is not None else ss.load_stores()
    rounds = round_signals(redline_dir)
    exam = ss.exam_keys()
    rows = []
    for key, t in ss.deduped_threads(stores):
        if t.get("author") != ss.SHADOW or key in exam:
            continue
        category = (t.get("shadow") or {}).get("category") if isinstance(t.get("shadow"), dict) else None
        fam = ss.family(category)
        if fam == "photos":
            continue
        a = signal(t, ss.deleted_ids(stores[key]), rounds)
        rows.append({"key": key, "thread": t.get("id"), "category": (category or "").strip().lower() or "(none)", "family": fam,
                     "text": re.sub(r"^\[Shadow reviewer\]\s*|\s*\(confidence [\d.]+\)\s*$", "", t.get("text", "")),
                     "quote": (t.get("anchor") or {}).get("quote", ""), "created": t.get("created", ""), **a})
    return rows


def candidates(rows):
    by_kind = defaultdict(list)
    for r in rows:
        by_kind[r["category"]].append(r)
    out = []
    for kind, group in by_kind.items():
        t = ss.tally([r["verdict"] for r in group])
        yes = [r for r in group if r["verdict"] in ("yes", "nuance")]
        articles = {r["key"] for r in yes}
        if len(yes) >= MIN_YES and len(articles) >= MIN_ARTICLES and t["rate"] >= MIN_RATE:
            out.append({"kind": kind, "family": group[0]["family"], "yes": len(yes), "no": t["no"], "articles": len(articles), "rate": t["rate"],
                        "asks": [r["text"] for r in yes[:3]], "his_words": [r["reply"] for r in yes if r["verdict"] == "nuance"][:3],
                        "covered": ALREADY_COVERED.get(group[0]["family"], "")})
    return sorted(out, key=lambda c: (-c["yes"], c["kind"]))


def words_in(key, newest, cache):
    if key not in cache:
        path = fa.article_file(key, newest)
        try:
            with open(path, encoding="utf-8") as f:
                cache[key] = len(prose_words(blocks_of(f.read())))
        except (OSError, TypeError):
            cache[key] = 0
    return cache[key]


def per_draft(rows, redline_dir=None):
    """one row per article and Shadow pass (threads posted the same day), newest pass last"""
    _, newest = fa.round_paths(redline_dir)
    cache = {}
    groups = defaultdict(list)
    for r in rows:
        groups[(r["key"], r["created"][:10])].append(r)
    out = []
    for (key, day), g in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        t = ss.tally([r["verdict"] for r in g])
        confirmed = [r for r in g if r["verdict"] in ("yes", "nuance")]
        words = words_in(key, newest, cache)
        fams = Counter(r["family"] for r in confirmed)
        out.append({"article": key, "pass": day, "threads": len(g), "confirmed": len(confirmed), "said_no": t["no"], "waiting": t["unanswered"],
                    "words": words, "per_1000": round(1000 * len(confirmed) / words, 1) if words else None,
                    "by_family": ", ".join("%s %d" % kv for kv in fams.most_common())})
    return out


def family_table(rows):
    table = []
    for f in ss.FAMILY_ORDER:
        g = [r for r in rows if r["family"] == f]
        if g:
            t = ss.tally([r["verdict"] for r in g])
            table.append({"family": f, **t})
    return table


def report(rows, redline_dir=None):
    return {"candidates": candidates(rows), "families": family_table(rows), "drafts": per_draft(rows, redline_dir)}


def render(rep):
    lines = ["# Shadow's lessons for the writer", "",
             "Generated %s by `python tools/shadow_lessons.py`. Counts his typed replies and the round changes he decided that name a Shadow thread. "
             "Silence, ticks with no reply, auto-closes and changes made by a tool (shadow-loop, shadow-consent, draft_check) don't count. Photos and the six exam articles are left out." % datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
    lines += ["## Candidate writer rules", "",
              "Kinds Shadow keeps flagging and he keeps agreeing with: %d or more yes on %d or more articles, at least %d%% yes. A candidate is only a candidate: it has to pass `tools/rule_test.py` before it joins `writer_rules.md`, and it should merge into a rule that's already there if one fits." % (MIN_YES, MIN_ARTICLES, MIN_RATE * 100), ""]
    if not rep["candidates"]:
        lines.append("None yet.")
    else:
        lines += ["| Kind | Family | Yes | No | Articles | Yes rate | What Shadow asked | His own words | Note |", "|---|---|---:|---:|---:|---:|---|---|---|"]
        for c in rep["candidates"]:
            lines.append("| %s | %s | %d | %d | %d | %s | %s | %s | %s |" % (c["kind"], c["family"], c["yes"], c["no"], c["articles"], ss.pct(c["rate"]),
                                                                          "; ".join(c["asks"])[:220], "; ".join(c["his_words"])[:220], c["covered"]))
    lines += ["", "## By family (typed answers only; waiting threads aren't counted)", "",
              "| Family | Yes | His instruction | No | Unclear | Ticked, no reply | Waiting | Yes rate |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for f in rep["families"]:
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %s |" % (f["family"], f["yes"], f["nuance"], f["no"], f["unclear"], f["tick"], f["unanswered"], ss.pct(f["rate"])))
    lines += ["", "## Shadow's findings per draft (the score for that draft's writer)", "",
              "\"Confirmed\" is how many Shadow threads he agreed with (typed yes, an instruction, or a round change he accepted): each is something the writer could have got right. Fewer per 1,000 words is a cleaner draft. A pass with nothing answered yet has no score. Words are today's text, so a score against an old pass is approximate.", "",
              "| Draft | Shadow pass | Threads | Confirmed | Said no | Waiting | Words | Confirmed per 1,000 | Confirmed by family |", "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for d in rep["drafts"]:
        lines.append("| %s | %s | %d | %d | %d | %d | %d | %s | %s |" % (d["article"], d["pass"], d["threads"], d["confirmed"], d["said_no"], d["waiting"], d["words"],
                                                                     "" if d["per_1000"] is None or (d["confirmed"] == 0 and d["said_no"] == 0) else d["per_1000"], d["by_family"]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args(argv)
    rep = report(gather())
    if a.json:
        ss.utf8_out(json.dumps(rep, ensure_ascii=False, indent=1) + "\n")
        return 0
    text = render(rep)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text)
    ss.utf8_out("%d candidate kinds, %d drafts -> %s\n" % (len(rep["candidates"]), len(rep["drafts"]), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
