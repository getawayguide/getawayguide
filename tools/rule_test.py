"""Test a candidate writer or Shadow rule against Kevin's finished articles before it joins a rulebook.

    python tools/rule_test.py --pattern "regex" [--pattern "regex" ...] [--absent "regex"] [--type city-guide]
                              [--scope all|intro|headings|paragraphs|section:KIND] [--min 1] [--from KEY ...] [--json]
    python tools/rule_test.py --sample [--pattern "regex"] [--type ...] [--scope ...] [--per-article 3] [--seed 7] [--out FILE]

An article conforms when, inside the scope, every --pattern matches at least --min times and no --absent pattern matches.
The bar (program note, 2026-11-09): his finals on 2 or more OTHER finished articles conform. So --from names the article(s) a rule
was learned from and they don't count. Passing is the least a rule has to do; read the articles that don't conform before keeping it.
Patterns are case-insensitive unless --case. Text is his final as plain prose: no photos, captions, contents box or nav.

A judgment rule can't be a regex ("give the intro one idea"). --sample pulls real passages, without a verdict, for someone to read
and count by hand; record what you read and how many fit, as practice/merge_notes.md does.

Finished articles only (tools/finished_articles.py), never the six exam articles. Read-only. His finals contain text we wrote and
he kept, so "conforms" means he accepted it, not always that he wrote it.
"""
import argparse
import json
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from article_blocks import article_type, blocks_of, section_kind          # noqa: E402
import finished_articles as fa                                            # noqa: E402
import shadow_signals as ss                                               # noqa: E402

MIN_CONFORMING = 2


def in_scope(block, scope, kind_of_article):
    tag = block["tag"]
    if scope == "all":
        return True
    if scope == "intro":
        return tag not in ("h2", "h3", "h4") and not block["section"]
    if scope == "headings":
        return tag in ("h2", "h3", "h4")
    if scope == "paragraphs":
        return tag in ("p", "li")
    if scope.startswith("section:"):
        return section_kind(block["sub"] or block["section"], kind_of_article, block["section"]) == scope[8:]
    raise ValueError("unknown scope: %s" % scope)


def scoped_blocks(path, scope, kind_of_article):
    with open(path, encoding="utf-8") as f:
        return [b for b in blocks_of(f.read()) if in_scope(b, scope, kind_of_article)]


def judge(blocks, patterns, absent, minimum):
    """(verdict, counts per --pattern, hits of --absent): verdict is yes, no, or n/a when the scope holds nothing"""
    if not blocks:
        return "n/a", [0] * len(patterns), 0
    text = "\n".join(b["text"] for b in blocks)
    counts = [len(rx.findall(text)) for rx in patterns]
    bad = sum(len(rx.findall(text)) for rx in absent)
    return ("yes" if all(c >= minimum for c in counts) and not bad else "no"), counts, bad


def pool(types, sources, finished=None):
    finished = finished if finished is not None else fa.finished_articles()
    return [a for a in finished if (not types or a["type"] in types) and a["key"] not in sources]


def path_of(a):
    return os.path.join(ROOT, a["file"])


def run_test(a, finished=None):
    flags = 0 if a.case else re.I
    patterns = [re.compile(p, flags) for p in a.pattern]
    absent = [re.compile(p, flags) for p in a.absent]
    rows = []
    for art in pool(a.type, set(a.source), finished):
        blocks = scoped_blocks(path_of(art), a.scope, art["type"])
        verdict, counts, bad = judge(blocks, patterns, absent, a.min)
        rows.append({"key": art["key"], "type": art["type"], "verdict": verdict, "matches": counts, "absent_hits": bad})
    conform = [r for r in rows if r["verdict"] == "yes"]
    by_type = {}
    for r in rows:
        t = by_type.setdefault(r["type"], {"conform": 0, "tested": 0, "no_such_scope": 0})
        if r["verdict"] == "n/a":
            t["no_such_scope"] += 1
        else:
            t["tested"] += 1
            t["conform"] += r["verdict"] == "yes"
    return {"passes": len(conform) >= MIN_CONFORMING, "conforming": len(conform), "tested": sum(1 for r in rows if r["verdict"] != "n/a"),
            "bar": MIN_CONFORMING, "by_type": by_type, "rows": rows}


def render_test(res):
    lines = ["%s: %d of %d finished articles conform (bar: %d other articles)" % ("PASS" if res["passes"] else "FAIL", res["conforming"], res["tested"], res["bar"]), ""]
    for t, v in sorted(res["by_type"].items()):
        lines.append("  %-12s %d of %d conform%s" % (t, v["conform"], v["tested"], ", %d have no such section" % v["no_such_scope"] if v["no_such_scope"] else ""))
    for label, want in (("Conform", "yes"), ("Do not conform (read these before keeping the rule)", "no")):
        g = [r for r in res["rows"] if r["verdict"] == want]
        lines += ["", "%s: %d" % (label, len(g))]
        lines += ["  %-48s %s  matches %s%s" % (r["key"], r["type"], r["matches"], ", absent-pattern hits %d" % r["absent_hits"] if r["absent_hits"] else "") for r in g]
    return "\n".join(lines) + "\n"


def run_sample(a, finished=None):
    rng = random.Random(a.seed)
    flags = 0 if a.case else re.I
    patterns = [re.compile(p, flags) for p in a.pattern]
    out = []
    for art in sorted(pool(a.type, set(a.source), finished), key=lambda x: x["key"]):
        blocks = [b for b in scoped_blocks(path_of(art), a.scope, art["type"]) if b["tag"] not in ("h2", "h3", "h4") or a.scope == "headings"]
        if patterns:
            blocks = [b for b in blocks if any(rx.search(b["text"]) for rx in patterns)]
        if not blocks:
            continue
        picked = sorted(rng.sample(blocks, min(a.per_article, len(blocks))), key=lambda b: b["i"])
        for b in picked:
            out.append({"key": art["key"], "type": art["type"], "section": b["sub"] or b["section"] or "Intro", "tag": b["tag"], "text": b["text"]})
    return out


def render_sample(rows):
    lines = ["%d passages, by article (read them, don't grep them; count how many fit and write the count down):" % len(rows)]
    last = None
    for r in rows:
        if r["key"] != last:
            lines += ["", "== %s (%s)" % (r["key"], r["type"])]
            last = r["key"]
        lines.append("[%s] %s" % (r["section"], r["text"]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pattern", action="append", default=[], help="must match (repeatable)")
    ap.add_argument("--absent", action="append", default=[], help="must not match (repeatable)")
    ap.add_argument("--type", action="append", default=[], help="city-guide, itinerary, top-10, field-notes (repeatable; default all)")
    ap.add_argument("--scope", default="all", help="all, intro, headings, paragraphs, section:<section kind from article_blocks>")
    ap.add_argument("--min", type=int, default=1, help="matches each --pattern needs in an article")
    ap.add_argument("--from", dest="source", action="append", default=[], metavar="KEY", help="the article the rule was learned from; it can't vouch for it")
    ap.add_argument("--case", action="store_true", help="case-sensitive patterns")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sample", action="store_true", help="pull passages for a person to read instead of testing a regex")
    ap.add_argument("--per-article", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7, help="same seed, same passages")
    ap.add_argument("--out", help="write the sample here instead of stdout")
    a = ap.parse_args(argv)
    if not a.sample and not a.pattern and not a.absent:
        ap.error("give at least one --pattern or --absent, or use --sample")
    if a.sample:
        rows = run_sample(a)
        text = json.dumps(rows, ensure_ascii=False, indent=1) + "\n" if a.json else render_sample(rows)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(text)
            ss.utf8_out("%d passages -> %s\n" % (len(rows), a.out))
        else:
            ss.utf8_out(text)
        return 0
    res = run_test(a)
    ss.utf8_out(json.dumps(res, ensure_ascii=False, indent=1) + "\n" if a.json else render_test(res))
    return 0 if res["passes"] else 1


if __name__ == "__main__":
    sys.exit(main())
