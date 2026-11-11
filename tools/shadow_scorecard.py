"""Shadow's report card, graded by Kevin's yes rate, and how much he has to do per draft.

    python tools/shadow_scorecard.py                writes .tmp/kevin_reviewer/scorecard.md, prints its top lines
    python tools/shadow_scorecard.py --json         the numbers as JSON on stdout
    python tools/shadow_scorecard.py --out FILE     write it elsewhere

Yes rate = his typed yes and typed instructions, out of typed yes, instructions and no (shadow_signals.answer). A thread still waiting
for him doesn't count either way. Replies count; silence doesn't. A tick with no reply is shown in its own column (his convention reads
it as a no), but it isn't in the rate. Photo comments are left out everywhere (program.md).

A draft here is an article, over its whole review. Sittings are stretches of his comments and replies on it, split by a gap of more
than 4 hours (same as tools/shadow_backtest.py). Target (program.md): one sitting, under 15 comments.

Reads the comment store only. Nothing here changes how Shadow ranks its comments; that's a separate, later queue item.
"""
import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from article_blocks import article_type                                   # noqa: E402
import kevin_comments as kc                                               # noqa: E402
import shadow_signals as ss                                               # noqa: E402

OUT = os.path.join(ROOT, ".tmp", "kevin_reviewer", "scorecard.md")
BLIND_EVERY = 10


def shadow_rows(stores):
    rows = []
    for key, t in ss.deduped_threads(stores):
        if t.get("author") != ss.SHADOW:
            continue
        category = (t.get("shadow") or {}).get("category") if isinstance(t.get("shadow"), dict) else None
        fam = ss.family(category)
        if fam == "photos":
            continue
        rows.append({"key": key, "family": fam, "created": t.get("created", ""), **ss.answer(t, ss.deleted_ids(stores[key]))})
    return rows


def activity(stores):
    """{key: {"comments": [times of his non-photo threads], "touches": [those plus his replies]}}"""
    out = defaultdict(lambda: {"comments": [], "touches": []})
    for key, t in ss.deduped_threads(stores):
        quote = (t.get("anchor") or {}).get("quote", "")
        if t.get("author") == ss.KEVIN and not kc.is_photo(t.get("text", ""), quote):
            out[key]["comments"].append(t.get("created", ""))
            out[key]["touches"].append(t.get("created", ""))
        for r in ss.kevin_replies(t):
            if not kc.is_photo(r.get("text", ""), quote):
                out[key]["touches"].append(r.get("created", ""))
    return out


def sittings(times):
    """how many stretches of work, split by a gap of more than SITTING_GAP_H hours"""
    ts = sorted(kc.parse_time(t) for t in times if t)
    if not ts:
        return 0
    return 1 + sum(1 for a, b in zip(ts, ts[1:]) if b - a > ss.SITTING_GAP_H * 3600)


def per_draft(stores, shadow):
    exam = ss.exam_keys()
    with_shadow = {r["key"] for r in shadow}
    out = []
    for key, a in activity(stores).items():
        if not a["comments"] and not a["touches"]:
            continue
        out.append({"key": key, "type": article_type(key), "first": min(a["touches"]), "own_comments": len(a["comments"]),
                    "sittings": sittings(a["touches"]), "shadow": key in with_shadow, "exam": key in exam})
    return sorted(out, key=lambda r: r["first"])


def by_month(drafts):
    groups = defaultdict(list)
    for d in drafts:
        groups[d["first"][:7]].append(d)
    out = []
    for month in sorted(groups):
        g = groups[month]
        out.append({"month": month, "drafts": len(g), "median_comments": statistics.median(d["own_comments"] for d in g),
                    "median_sittings": statistics.median(d["sittings"] for d in g),
                    "at_target": sum(1 for d in g if d["sittings"] <= 1 and d["own_comments"] < 15)})
    return out


def by_week(rows):
    groups = defaultdict(list)
    for r in rows:
        if r["verdict"] in ("yes", "nuance", "no") and r["when"]:
            d = datetime.fromtimestamp(kc.parse_time(r["when"]), tz=timezone.utc)
            groups["%d-W%02d" % d.isocalendar()[:2]].append(r["verdict"])
    return [{"week": w, **ss.tally(groups[w])} for w in sorted(groups)]


def blind_counter(stores):
    """how many articles he has reviewed (a comment or reply) since the last blind test, against the every-10 cadence"""
    try:
        with open(ss.SPLIT, encoding="utf-8") as f:
            made = json.load(f).get("made", "")
    except (OSError, ValueError):
        made = ""
    cutoff = datetime.fromisoformat(made).timestamp() if made else 0
    exam = ss.exam_keys()
    reviewed = {key for key, a in activity(stores).items() if key not in exam and any(kc.parse_time(t) > cutoff for t in a["touches"] if t)}
    return {"last_blind_test": made[:10], "reviewed_since": sorted(reviewed), "every": BLIND_EVERY}


def build(stores=None):
    stores = stores if stores is not None else ss.load_stores()
    rows = shadow_rows(stores)
    overall = ss.tally([r["verdict"] for r in rows])
    fams = []
    for f in ss.FAMILY_ORDER:
        g = [r for r in rows if r["family"] == f]
        if g:
            fams.append({"family": f, **ss.tally([r["verdict"] for r in g])})
    drafts = per_draft(stores, rows)
    arts = []
    for key in sorted({r["key"] for r in rows}):
        g = [r for r in rows if r["key"] == key]
        arts.append({"key": key, **ss.tally([r["verdict"] for r in g])})
    deleted = sum(1 for s in stores.values() for x in ss.deleted_ids(s) if x.startswith("sr"))
    return {"overall": overall, "families": fams, "articles": arts, "drafts": drafts, "months": by_month(drafts), "weeks": by_week(rows),
            "deleted_shadow_threads": deleted, "blind": blind_counter(stores)}


def fmt_rate(t):
    return ss.pct(t["rate"])


def render(c):
    o = c["overall"]
    ticks_no = (o["yes"] + o["nuance"]) / (o["answered"] + o["tick"]) if (o["answered"] + o["tick"]) else None
    lines = ["# Shadow scorecard", "", "Generated %s by `python tools/shadow_scorecard.py`. Only what Kevin typed is graded; photo comments are out." % datetime.now().strftime("%Y-%m-%d %H:%M"), "",
             "## Top lines", "",
             "- **He said yes to %s of Shadow's comments he answered** (%d yes, %d with an instruction, %d no, %d answered). %d are still waiting for him and don't count." % (
                 fmt_rate(o), o["yes"], o["nuance"], o["no"], o["answered"], o["unanswered"]),
             "- %d he ticked with no reply (his convention reads that as a no; if they all counted, the rate would be %s). %d deleted, %d replies couldn't be read as a yes or a no." % (
                 o["tick"], ss.pct(ticks_no), c["deleted_shadow_threads"], o["unclear"])]
    recent = [d for d in c["drafts"] if not d["exam"]][-5:]
    if recent:
        lines.append("- Newest drafts he reviewed: " + "; ".join("%s %d comments, %d %s" % (d["key"], d["own_comments"], d["sittings"], "sitting" if d["sittings"] == 1 else "sittings") for d in recent) + ". Target: one sitting, under 15 comments.")
    b = c["blind"]
    lines += ["- Blind test: articles he's reviewed since the last one (%s): %d of the %d that trigger a rerun." % (b["last_blind_test"], len(b["reviewed_since"]), b["every"]), ""]
    lines += ["## Yes rate by kind", "", "| Family | Yes | His instruction | No | Unclear | Ticked, no reply | Waiting | Yes rate |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for f in c["families"]:
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %s |" % (f["family"], f["yes"], f["nuance"], f["no"], f["unclear"], f["tick"], f["unanswered"], fmt_rate(f)))
    lines += ["", "## Yes rate by article", "", "| Article | Yes | His instruction | No | Unclear | Ticked, no reply | Waiting | Yes rate |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for a in c["articles"]:
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %s |" % (a["key"], a["yes"], a["nuance"], a["no"], a["unclear"], a["tick"], a["unanswered"], fmt_rate(a)))
    lines += ["", "## His own comments per draft, over time", "",
              "A draft is one article over its whole review, in the order he started on it. Photo comments are out. An article with `*` is one of the six exam articles.", "",
              "| Started | Article | Type | Shadow? | His comments | Sittings | At target |", "|---|---|---|---|---:|---:|---|"]
    for d in c["drafts"]:
        lines.append("| %s | %s%s | %s | %s | %d | %d | %s |" % (d["first"][:10], d["key"], "*" if d["exam"] else "", d["type"], "yes" if d["shadow"] else "no", d["own_comments"], d["sittings"],
                                                                 "yes" if d["sittings"] <= 1 and d["own_comments"] < 15 else ""))
    lines += ["", "| Month started | Drafts | Median comments | Median sittings | At target |", "|---|---:|---:|---:|---:|"]
    for m in c["months"]:
        lines.append("| %s | %d | %g | %g | %d |" % (m["month"], m["drafts"], m["median_comments"], m["median_sittings"], m["at_target"]))
    lines += ["", "## Yes rate over time (by the week he answered)", "", "| Week | Yes rate | Answers |", "|---|---:|---:|"]
    for w in c["weeks"]:
        lines.append("| %s | %s | %d |" % (w["week"], fmt_rate(w), w["answered"]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args(argv)
    card = build()
    if a.json:
        ss.utf8_out(json.dumps(card, ensure_ascii=False, indent=1) + "\n")
        return 0
    text = render(card)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text)
    top = text.split("## Yes rate by kind")[0]
    ss.utf8_out(top + "written to %s\n" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
