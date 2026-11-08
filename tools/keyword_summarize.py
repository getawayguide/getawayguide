"""Summarize tools/keyword_suggest.ps1 output for a keyword map (roadmap step R6, 2026-11-07).

    python tools/keyword_summarize.py .tmp/keywords/raw/kotor.jsonl [more.jsonl ...] [--top 40]

Per seed it prints:
  * the bare seed's own suggestions in Google's order (order = the best free proxy for relative demand),
  * every other suggestion, ranked by how many of the seed's expansions returned it and its best position,
  * the question-shaped ones (how/what/is/can/do/should/where/when/why/which/best) separately, the
    People-Also-Ask material.
No volumes: autocomplete has none. Treat the ranking as "people type this", not as a number.
"""
import json
import sys
from collections import defaultdict

QWORDS = ("how ", "what ", "is ", "are ", "can ", "do ", "does ", "should ", "where ", "when ", "why ",
          "which ", "best ", "worth")


def main(argv):
    top = 40
    files = []
    it = iter(argv)
    for a in it:
        if a == "--top":
            top = int(next(it))
        else:
            files.append(a)
    out = []
    for f in files:
        by_seed = defaultdict(list)
        with open(f, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    by_seed[rec["seed"]].append(rec)
        for seed, recs in by_seed.items():
            bare = next((r["suggestions"] for r in recs if r["query"] == seed), [])
            score = defaultdict(lambda: [0, 99])
            for r in recs:
                for i, s in enumerate(r["suggestions"] or []):
                    s = s.strip().lower()
                    score[s][0] += 1
                    score[s][1] = min(score[s][1], i)
            ranked = sorted(score.items(), key=lambda kv: (-kv[1][0], kv[1][1], kv[0]))
            qs = [k for k, _ in ranked if k.startswith(QWORDS) or "?" in k]
            out.append(f"### seed: {seed}")
            out.append("bare (Google order): " + (" | ".join(bare) if bare else "(no suggestions: low demand)"))
            out.append("ranked: " + " | ".join(f"{k} ({v[0]})" for k, v in ranked[:top]))
            out.append("questions: " + " | ".join(qs[:25]))
            out.append("")
    sys.stdout.buffer.write("\n".join(out).encode("utf-8"))


if __name__ == "__main__":
    main(sys.argv[1:])
