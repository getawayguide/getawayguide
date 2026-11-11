"""Backtest the Shadow reviewer on Kevin's real reviews, by article, and measure drafts against his finished versions.

    python tools/shadow_backtest.py prep                     write backtest/split.json, each test article's draft and answer key
    python tools/shadow_backtest.py score <predictions> --label name  [--judged file.json] [--all-sittings] [--no-links]
    python tools/shadow_backtest.py baseline                 first reviewed draft vs his final, table in backtest/results.md

Everything is under .tmp/kevin_reviewer/backtest/ and nothing touches Drafts, the comment store or a round. No LLM here.

Definitions (program.md, brief.md)
  first sitting   his comments from the first one up to the first gap of more than SITTING_GAP_H hours, and never past the
                  moment his next round opened (later sittings react to our edits, not the draft). Author "Kevin" only.
  draft           the copy of the article he was looking at: of every git version and round baseline saved before that
                  sitting, the one containing the most of the text he selected (ties to the newest).
  predictions     <predictions> is a folder of <key>.json, one list per article in the brief.md shape:
                  [{"id", "quote", "text", "category", "confidence"}]. Run a test article with no test article in the
                  evidence: shadow_retrieve --exclude-articles <the test set>, and no rule built from one.
  hit             same passage (selections overlap or within CLOSE_CHARS of each other) and the same ask (category)
  near            same passage, different ask; or same ask in the same or the next paragraph
  false alarm     neither; a prediction whose quote is not in the draft is a false alarm too
  miss            a comment of his no prediction reached
  precision       (hit + 0.5 near) / predictions    recall   (hit + 0.5 near) / his comments
Photo comments are out of the answer key and photo predictions out of the count. Scoring by script is approximate: pairs it can't
be sure of (a near pair, or a hit whose wording differs a lot) are written to unsure_pairs_<label>.json for an agent to judge. Give the
verdicts back as --judged: {"<pred id>|<answer id>": "hit" | "near" | "false"}, and the same run counts them instead.
A verdict of false can free a comment for another prediction, so rerun and judge the newly listed pairs until none are left.

--no-links       drop link predictions and link comments from both sides.
--all-sittings   adds a LOOSER second measure, "did he ever say it": a prediction also counts if ANY non-photo Kevin comment on that
                 article, from any sitting, matches it by the same passage and ask rules. A comment can vouch for several predictions
                 (the 1-to-1 column is the same thing with each comment used once). Later comments are only matchable if their quoted
                 text is still in the first-sitting draft; the output says how many are. Precision uses this measure; recall stays
                 against the first sitting. Pairs it finds go to the same unsure list for judging.
"""
import argparse
import difflib
import html as htmllib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from article_blocks import article_type, compare_key                     # noqa: E402
import kevin_comments as kc                                              # noqa: E402
import kevin_edits as ke                                                 # noqa: E402
from draft_distance import distance                                      # noqa: E402

BT = os.path.join(ROOT, ".tmp", "kevin_reviewer", "backtest")
SPLIT = os.path.join(BT, "split.json")
RESULTS = os.path.join(BT, "results.md")
SITTING_GAP_H = 4
CLOSE_CHARS = 60
BULK_TOUCH = (datetime(2026, 10, 24, 14, 30).timestamp(), datetime(2026, 10, 24, 15, 10).timestamp())  # proposal files rewritten by the Drafts reorganization
TEST = {
    "turkiye__antalya": "city-guide (Oct, Turkiye)",
    "north-macedonia__bitola": "city-guide (Oct 31, North Macedonia)",
    "north-macedonia__ohrid": "city-guide (Nov 1, North Macedonia)",
    "armenia__armenia-itinerary": "itinerary (before Shadow existed)",
    "armenia__top-10-armenia": "top 10 (before Shadow existed)",
    "armenia__field-notes": "field notes",
}


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def real_round_times(key, rounds):
    """when each round of this article opened (proposal file), leaving out the bulk touch of Oct 24"""
    out = []
    for r in rounds.get(key, []):
        p = os.path.join(ke.REDLINE, r["slug"], "proposal.json")
        m = os.path.getmtime(p)
        if not BULK_TOUCH[0] <= m <= BULK_TOUCH[1]:
            out.append((m, r["slug"]))
    return sorted(out)


def first_sitting(comments, key, rounds):
    """(the comments of his first sitting, end time, why it ended)"""
    cs = sorted([c for c in comments if c["key"] == key], key=lambda c: c["created"])
    if not cs:
        return [], 0, ""
    times = [kc.parse_time(c["created"]) for c in cs]
    n, why = len(cs), "all comments"
    for i in range(1, len(cs)):
        if times[i] - times[i - 1] > SITTING_GAP_H * 3600:
            n, why = i, f"gap of over {SITTING_GAP_H}h"
            break
    opened = [(m, s) for m, s in real_round_times(key, rounds) if m > times[0] + 90]
    if opened:
        cut = sum(1 for t in times[:n] if t < opened[0][0])
        if 0 < cut < n:
            n, why = cut, f"round {opened[0][1]} opened"
    return cs[:n], times[n - 1], why


def snapshots(key, tl, rounds, blobs):
    """[(time, label, html)] every copy of the article, oldest first"""
    out = []
    for v in tl.get(key, []):
        out.append((v["t"], f"git {v['sha'][:7]}: {v['msg'].splitlines()[0][:80]}", lambda v=v: blobs.read(v["sha"], v["path"])))
    for r in rounds.get(key, []):
        if r["baseline"]:
            def load(p=r["baseline"]):
                with open(p, encoding="utf-8") as f:
                    return f.read()
            out.append((r["t"], f"baseline of {r['slug']}", load))
    return sorted(out, key=lambda x: x[0])


def choose_draft(sit, t_start, t_end, snaps):
    """The copy he was reading: the most of his selections present, before the sitting (a copy saved during it only if clearly better)."""
    best = None
    for t, label, load in snaps:
        if t > t_end + 3600:
            continue
        try:
            html = load()
        except OSError:
            continue
        if not html:
            continue
        loc = kc.Located(html)
        found = sum(1 for c in sit if loc.find(c.get("quote") or "", c.get("anchor_before", ""), c.get("anchor_after", "")))
        cov = found / len(sit) if sit else 0.0
        before = t <= t_start + 300
        score = cov - (0 if before else 0.15)
        if best is None or score > best["score"] or (score == best["score"] and t >= best["t"]):
            best = {"t": t, "label": label, "html": html, "coverage": round(cov, 2), "score": score, "before_sitting": before}
    return best


def draft_text(html):
    lines = []
    for b in kc.blocks_of(html):
        lines.append(f"[{b['i']} {b['tag']}] {b['text']}")
    return "\n".join(lines)


def _loaded_comments():
    comments = [c for c in kc.read_corpus(("comment",)) if c.get("thread_author") == "Kevin"]
    anchors = kc.load_anchors()
    for c in comments:
        a = anchors.get((c["key"], c.get("thread")), ("", ""))
        c["anchor_before"], c["anchor_after"] = a[0], a[1]
    return comments


def prep(a):
    comments = _loaded_comments()
    rounds = ke.load_rounds()
    tl = ke.timeline()
    blobs = ke.Blobs()
    keys = sorted({c["key"] for c in comments})
    split = {"made": datetime.now().isoformat(timespec="seconds"),
             "note": "Train/test split BY ARTICLE. Test articles and their comments are never used to write rules. "
                     "Comments in greece__meteora include Thessaloniki's (one store file), so greece stays in train.",
             "test": [], "train": [k for k in keys if k not in TEST]}
    for key, why_chosen in TEST.items():
        sit, t_end, why_end = first_sitting(comments, key, rounds)
        t0 = kc.parse_time(sit[0]["created"])
        snaps = snapshots(key, tl, rounds, blobs)
        best = choose_draft(sit, t0, t_end, snaps)
        loc = kc.Located(best["html"])
        answers, left = [], []
        for c in sit:
            cat = kc.category(c["text"])
            rec = {"id": c["id"], "quote": c.get("quote", ""), "text": c["text"], "category": cat, "created": c["created"],
                   "anchor_before": c["anchor_before"], "anchor_after": c["anchor_after"]}
            if kc.is_photo(c["text"], c.get("quote", "")):
                left.append(dict(rec, reason="photo comment"))
                continue
            if not loc.find(c.get("quote") or "", c["anchor_before"], c["anchor_after"]):
                left.append(dict(rec, reason="quote not in this draft"))
                continue
            answers.append(rec)
        d = os.path.join(BT, key)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "draft.html"), "w", encoding="utf-8") as f:
            f.write(best["html"])
        with open(os.path.join(d, "draft.txt"), "w", encoding="utf-8") as f:
            f.write(draft_text(best["html"]) + "\n")
        save_json(os.path.join(d, "answer.json"), {"key": key, "type": article_type(key), "first_sitting_comments": len(sit),
                                                   "draft": best["label"], "coverage": best["coverage"], "answers": answers, "left_out": left})
        split["test"].append({"key": key, "article_type": article_type(key), "why": why_chosen,
                              "first_comment": sit[0]["created"], "sitting_ended": why_end, "comments_in_sitting": len(sit),
                              "answers": len(answers), "left_out": len(left), "draft": best["label"], "draft_saved": datetime.fromtimestamp(best["t"]).isoformat(timespec="minutes"),
                              "quote_coverage": best["coverage"], "draft_before_sitting": best["before_sitting"]})
    blobs.close()
    save_json(SPLIT, split)
    lines = [f"split: {len(split['test'])} test, {len(split['train'])} train -> {SPLIT}"]
    for t in split["test"]:
        lines.append(f"  {t['key']} ({t['article_type']}): {t['answers']} answers of {t['comments_in_sitting']} in the sitting, "
                     f"{t['left_out']} left out; ended: {t['sitting_ended']}; coverage {t['quote_coverage']}; draft: {t['draft']}")
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))


# ---------------------------------------------------------------- scoring
def locate_pred(loc, p):
    return loc.find(p.get("quote") or "")


def tier(p_pos, a_pos, p_cat, a_cat):
    ps, pe, pb = p_pos
    as_, ae, ab = a_pos
    overlap = max(0, min(pe, ae) - max(ps, as_))
    gap = 0 if overlap else (max(ps, as_) - min(pe, ae))
    close = overlap > 0 or (pb == ab and gap <= CLOSE_CHARS)
    same_block = pb == ab
    adjacent = abs(pb - ab) == 1
    if close and p_cat == a_cat:
        return "hit", overlap
    if close:
        return "near", overlap
    if p_cat == a_cat and (same_block or adjacent):
        return "near", 0
    return None, 0


def later_comments(key, comments, ans, loc, no_links):
    """Kevin's non-photo comments on this article from outside the first sitting: (all of them, those whose quote is in this draft)"""
    known = {a["id"] for a in ans["answers"]} | {a["id"] for a in ans["left_out"]}
    rest = [c for c in comments if c["key"] == key and c["id"] not in known and not kc.is_photo(c["text"], c.get("quote", ""))]
    if no_links:
        rest = [c for c in rest if kc.category(c["text"]) != "link"]
    found = []
    for c in rest:
        pos = loc.find(c.get("quote") or "", c.get("anchor_before", ""), c.get("anchor_after", ""))
        if pos:
            found.append(dict(c, pos=pos, category=kc.category(c["text"])))
    return rest, found


def score_article(key, preds, judged, comments=None, no_links=False):
    d = os.path.join(BT, key)
    ans = read_json(os.path.join(d, "answer.json"))
    with open(os.path.join(d, "draft.html"), encoding="utf-8") as f:
        loc = kc.Located(f.read())
    answers = []
    for a in ans["answers"]:
        pos = loc.find(a["quote"], a.get("anchor_before", ""), a.get("anchor_after", ""))
        if pos:
            answers.append(dict(a, pos=pos))
    preds = [dict(p, category_scored=kc.category(p.get("text", ""))) for p in preds]
    preds = [p for p in preds if p["category_scored"] != "photos"]
    if no_links:
        preds = [p for p in preds if p["category_scored"] != "link"]
        answers = [a for a in answers if a["category"] != "link"]
    candidates = []
    placed = {}
    for i, p in enumerate(preds):
        pos = locate_pred(loc, p)
        placed[i] = pos
        if not pos:
            continue
        for j, a in enumerate(answers):
            pair = f"{p.get('id', i)}|{a['id']}"
            t, ov = tier(pos, a["pos"], p["category_scored"], a["category"])
            if pair in judged:
                t = {"hit": "hit", "near": "near", "false": None}.get(judged[pair], t)
            if t:
                candidates.append(({"hit": 2, "near": 1}[t], ov, i, j, t))
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    used_p, used_a, matches = set(), set(), {}
    for rank, ov, i, j, t in candidates:
        if i in used_p or j in used_a:
            continue
        used_p.add(i)
        used_a.add(j)
        matches[i] = (j, t)
    verdicts, unsure = [], []
    for i, p in enumerate(preds):
        if i in matches:
            j, t = matches[i]
            a = answers[j]
            verdicts.append({"id": p.get("id"), "verdict": t, "quote": p.get("quote"), "text": p.get("text"), "category": p["category_scored"],
                             "confidence": p.get("confidence"), "answer": a["id"], "answer_text": a["text"], "answer_category": a["category"]})
            wording = difflib.SequenceMatcher(None, compare_key(p.get("text", "")).lower(), compare_key(a["text"]).lower()).ratio()
            pair = f"{p.get('id', i)}|{a['id']}"
            if pair not in judged and (t == "near" or (t == "hit" and wording < 0.45 and p["category_scored"] not in ("link", "formatting"))):
                unsure.append({"article": key, "pair": pair, "scored": t, "pred_quote": p.get("quote"), "pred_text": p.get("text"), "pred_category": p["category_scored"],
                               "answer_quote": a["quote"], "answer_text": a["text"], "answer_category": a["category"]})
        else:
            verdicts.append({"id": p.get("id"), "verdict": "false-alarm" if placed.get(i) else "false-alarm (quote not in draft)", "quote": p.get("quote"),
                             "text": p.get("text"), "category": p["category_scored"], "confidence": p.get("confidence")})
    missed = [dict(answers[j], pos=None) for j in range(len(answers)) if j not in used_a]
    result = {"key": key, "article_type": ans["type"], "answers": len(answers), "predictions": len(preds), "verdicts": verdicts, "missed": missed}
    if comments is not None:
        rest, found = later_comments(key, comments, ans, loc, no_links)
        result["later_comments"] = {"non_photo": len(rest), "matchable": len(found)}
        pool = answers + found
        cands = []
        for i, p in enumerate(preds):
            if not placed.get(i):
                continue
            for j, a in enumerate(pool):
                pair = f"{p.get('id', i)}|{a['id']}"
                t, ov = tier(placed[i], a["pos"], p["category_scored"], a["category"])
                if pair in judged:
                    t = {"hit": "hit", "near": "near", "false": None}.get(judged[pair], t)
                if t:
                    cands.append(({"hit": 2, "near": 1}[t], ov, i, j, t, pair))
        cands.sort(key=lambda x: (-x[0], -x[1]))
        seen_unsure = {u["pair"] for u in unsure}
        best_done, used_p, used_a = set(), set(), set()
        for rank, ov, i, j, t, pair in cands:
            p, a, v = preds[i], pool[j], verdicts[i]
            if i not in best_done:
                best_done.add(i)
                v.update(ever=t, ever_answer=a["id"], ever_text=a["text"], ever_later=j >= len(answers))
                wording = difflib.SequenceMatcher(None, compare_key(p.get("text", "")).lower(), compare_key(a["text"]).lower()).ratio()
                if pair not in judged and pair not in seen_unsure and (t == "near" or (t == "hit" and wording < 0.45 and p["category_scored"] not in ("link", "formatting"))):
                    seen_unsure.add(pair)
                    unsure.append({"article": key, "pair": pair, "scored": t, "pred_quote": p.get("quote"), "pred_text": p.get("text"), "pred_category": p["category_scored"],
                                   "answer_quote": a["quote"], "answer_text": a["text"], "answer_category": a["category"]})
            if i not in used_p and j not in used_a:
                used_p.add(i)
                used_a.add(j)
                v["ever_1to1"] = t
    return result, unsure


def _rate(hit, near, total):
    return round((hit + 0.5 * near) / total, 3) if total else None


def summarize(results):
    def tally(group):
        hit = sum(1 for r in group for v in r["verdicts"] if v["verdict"] == "hit")
        near = sum(1 for r in group for v in r["verdicts"] if v["verdict"] == "near")
        false = sum(1 for r in group for v in r["verdicts"] if v["verdict"].startswith("false"))
        preds = sum(r["predictions"] for r in group)
        ans = sum(r["answers"] for r in group)
        return {"predictions": preds, "answers": ans, "hit": hit, "near": near, "false_alarm": false, "precision": _rate(hit, near, preds), "recall": _rate(hit, near, ans)}
    out = {"overall": tally(results), "by_article": {r["key"]: tally([r]) for r in results}, "by_type": {}, "by_category": {}}
    types = defaultdict(list)
    for r in results:
        types[r["article_type"]].append(r)
    out["by_type"] = {t: tally(g) for t, g in types.items()}
    if results and "later_comments" in results[0]:
        def ever(group):
            preds = sum(r["predictions"] for r in group)
            vs = [v for r in group for v in r["verdicts"]]
            hit, near = sum(1 for v in vs if v.get("ever") == "hit"), sum(1 for v in vs if v.get("ever") == "near")
            hit1, near1 = sum(1 for v in vs if v.get("ever_1to1") == "hit"), sum(1 for v in vs if v.get("ever_1to1") == "near")
            return {"predictions": preds, "hit": hit, "near": near, "precision": _rate(hit, near, preds),
                    "hit_1to1": hit1, "near_1to1": near1, "precision_1to1": _rate(hit1, near1, preds),
                    "best_match_is_later_comment": sum(1 for v in vs if v.get("ever_later")),
                    "later_non_photo": sum(r["later_comments"]["non_photo"] for r in group), "later_matchable": sum(r["later_comments"]["matchable"] for r in group)}
        out["all_sittings"] = {"note": "LOOSER measure: precision counts a match with any of his comments from any sitting; recall stays first sitting only",
                               "overall": ever(results), "by_type": {t: ever(g) for t, g in types.items()}, "by_article": {r["key"]: ever([r]) for r in results}}
    cats = sorted({v["category"] for r in results for v in r["verdicts"]} | {m["category"] for r in results for m in r["missed"]})
    for c in cats:
        vs = [v for r in results for v in r["verdicts"] if v["category"] == c]
        hit = sum(1 for v in vs if v["verdict"] == "hit")
        near = sum(1 for v in vs if v["verdict"] == "near")
        # recall per category counts the answers of that category, so a near on the wrong category still counts for the answer's own
        ans = sum(1 for r in results for v in r["verdicts"] if v.get("answer_category") == c) + sum(1 for r in results for m in r["missed"] if m["category"] == c)
        hit_a = sum(1 for r in results for v in r["verdicts"] if v.get("answer_category") == c and v["verdict"] == "hit")
        near_a = sum(1 for r in results for v in r["verdicts"] if v.get("answer_category") == c and v["verdict"] == "near")
        out["by_category"][c] = {"predicted": len(vs), "his_comments": ans, "precision": _rate(hit, near, len(vs)), "recall": _rate(hit_a, near_a, ans)}
    conf = [v for r in results for v in r["verdicts"] if isinstance(v.get("confidence"), (int, float))]
    for name, f in (("conf_0.6_up", lambda x: x >= 0.6), ("conf_below_0.5", lambda x: x < 0.5)):
        g = [v for v in conf if f(v["confidence"])]
        if g:
            out[name] = {"n": len(g), "hit_rate": round(sum(1 for v in g if v["verdict"] == "hit") / len(g), 3)}
    return out


def score(a):
    judged = read_json(a.judged) if a.judged else {}
    comments = _loaded_comments() if a.all_sittings else None
    results, unsure = [], []
    split = read_json(SPLIT)
    for t in split["test"]:
        key = t["key"]
        path = os.path.join(a.predictions, key + ".json") if os.path.isdir(a.predictions) else a.predictions
        if not os.path.exists(path):
            continue
        preds = read_json(path)
        if isinstance(preds, dict):
            preds = preds.get(key, preds.get("predictions", []))
        r, u = score_article(key, preds, judged, comments, a.no_links)
        results.append(r)
        unsure += u
    if not results:
        sys.exit(f"no predictions found under {a.predictions} for the test set in {SPLIT}")
    summ = summarize(results)
    out = {"label": a.label, "summary": summ, "articles": results}
    save_json(os.path.join(BT, f"scores_{a.label}.json"), out)
    save_json(os.path.join(BT, f"unsure_pairs_{a.label}.json"), unsure)
    lines = [f"# Backtest {a.label} ({len(results)} articles), scored per brief.md; {len(unsure)} pairs for an agent in unsure_pairs_{a.label}.json", "",
             f"overall: {json.dumps(summ['overall'])}", "by article type:"]
    lines += [f"  {t}: {json.dumps(v)}" for t, v in summ["by_type"].items()]
    lines.append("by category:")
    lines += [f"  {c}: {json.dumps(v)}" for c, v in summ["by_category"].items()]
    lines.append("by article:")
    lines += [f"  {k}: {json.dumps(v)}" for k, v in summ["by_article"].items()]
    if "all_sittings" in summ:
        lines += ["", "ALL SITTINGS (a looser measure; recall above stays first sitting only):", f"  overall: {json.dumps(summ['all_sittings']['overall'])}"]
        lines += [f"  {t}: {json.dumps(v)}" for t, v in summ["all_sittings"]["by_type"].items()]
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))


# ---------------------------------------------------------------- baseline: first reviewed draft vs his final
def baseline(a):
    comments = _loaded_comments()
    rounds = ke.load_rounds()
    tl = ke.timeline()
    blobs = ke.Blobs()
    rows = []
    for key in sorted({c["key"] for c in comments}, key=lambda k: (article_type(k), k)):
        sit, t_end, why_end = first_sitting(comments, key, rounds)
        versions = tl.get(key, [])
        if len(sit) < 2 or len(versions) < 2:
            continue
        t0 = kc.parse_time(sit[0]["created"])
        best = choose_draft(sit, t0, t_end, snapshots(key, tl, rounds, blobs))
        if not best or best["coverage"] < 0.5:
            rows.append({"key": key, "skip": f"no draft holds his selections (best {best and best['coverage']})"})
            continue
        last = versions[-1]
        final_html = blobs.read(last["sha"], last["path"])
        if not final_html or best["html"] == final_html or last["t"] <= best["t"]:
            rows.append({"key": key, "skip": "no later version in git"})
            continue
        d = distance(best["html"], final_html)
        live = os.path.exists(os.path.join(ROOT, key.split("__")[0], key.split("__")[1] + ".html"))
        rows.append({"key": key, "type": article_type(key), "draft": best["label"], "draft_saved": datetime.fromtimestamp(best["t"]).strftime("%b %d"),
                     "coverage": best["coverage"], "final": f"git {last['sha'][:7]}", "final_saved": datetime.fromtimestamp(last["t"]).strftime("%b %d"),
                     "state": "live" if live else "draft", "distance": {k: v for k, v in d.items() if k not in ("fact_examples", "links_missing_list")},
                     "fact_examples": d["fact_examples"], "links_missing_list": d["links_missing_list"]})
    blobs.close()
    save_json(os.path.join(BT, "baseline.json"), {"made": datetime.now().isoformat(timespec="seconds"), "rows": rows})
    def fmt(r):
        d = r["distance"]
        return (f"| {r['key']} | {r['type']} | {r['draft_saved']} ({r['coverage']:.0%} of his selections) | {r['final_saved']}, {r['state']} | {d['edit_ratio']:.2f} | "
                f"{d['words_changed']} / {d['words_final']} | {len(d['sections_added'])}/{len(d['sections_removed'])} | {d['facts_changed']} (+{d['facts_added']}/-{d['facts_removed']}) | {d['links_missing']}/{d['links_final']} |")
    good = [r for r in rows if "distance" in r]
    lines = ["# Baseline: first reviewed draft vs his final (draft-distance, program.md)", "",
             f"Made {datetime.now().strftime('%Y-%m-%d %H:%M')} by `python tools/shadow_backtest.py baseline`. Prose only: no photos, captions, alt text or maps.",
             "\"First reviewed draft\" = the copy holding the most of the text his first sitting selected, saved before that sitting. \"Final\" = the newest version in Drafts git; "
             "\"live\" means the article has launched, \"draft\" means it may still change.", "",
             "| Article | Type | First reviewed draft | Final | Edit ratio | Words changed / final | Sections +/- | Facts changed (+added/-removed) | Links missing / total in final |",
             "|---|---|---|---|---:|---:|---:|---:|---:|"]
    lines += [fmt(r) for r in sorted(good, key=lambda r: r["type"])]
    by_type = defaultdict(list)
    for r in good:
        by_type[r["type"]].append(r["distance"]["edit_ratio"])
    lines += ["", "**Median edit ratio by type:** " + ", ".join(f"{t} {sorted(v)[len(v) // 2]:.2f} (n={len(v)})" for t, v in sorted(by_type.items())),
              f"**All {len(good)} articles:** median edit ratio {sorted(r['distance']['edit_ratio'] for r in good)[len(good) // 2]:.2f}."]
    skipped = [r for r in rows if "skip" in r]
    if skipped:
        lines += ["", "Skipped: " + "; ".join(f"{r['key']} ({r['skip']})" for r in skipped)]
    with open(RESULTS, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prep")
    p = sub.add_parser("score")
    p.add_argument("predictions")
    p.add_argument("--label", default="run")
    p.add_argument("--judged", help="agent verdicts on unsure pairs")
    p.add_argument("--all-sittings", action="store_true", help="also report the looser 'did he ever say it' precision")
    p.add_argument("--no-links", action="store_true", help="drop link predictions and link comments")
    sub.add_parser("baseline")
    a = ap.parse_args()
    {"prep": prep, "score": score, "baseline": baseline}[a.cmd](a)


if __name__ == "__main__":
    main()
