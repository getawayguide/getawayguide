"""Kevin-reviewer corpus: every Kevin-authored review comment, reply and round decision.

Read-only on the comment store (redline.COMMENTS) and on .tmp/redline/*/decisions.json.
Writes:
  .tmp/kevin_reviewer/corpus.jsonl       one record per line (kind = comment | reply | decision)
  .tmp/kevin_reviewer/last_refresh.json  (only with --mark) newest timestamp + ids already folded into model.md

Usage:
  python tools/kevin_reviewer_corpus.py            rebuild corpus.jsonl, print counts
  python tools/kevin_reviewer_corpus.py --new      rebuild, print only records not in the stamp
  python tools/kevin_reviewer_corpus.py --mark     rebuild, record every current id as folded in
  python tools/kevin_reviewer_corpus.py --new --json   same, new records as JSON lines

Record ids: comment  = "<key>#<thread id>"
            reply    = "<key>#<thread id>/<reply id>"
            decision = "round:<slug>/<change id>"
Threads duplicated across files (greece__meteora / greece__thessaloniki share one set) are kept once,
under the first key alphabetically, with `also_in` listing the others.
"""
import argparse
import glob
import json
import os
import re
import sys
from html import unescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
OUT_DIR = os.path.join(ROOT, ".tmp", "kevin_reviewer")
CORPUS = os.path.join(OUT_DIR, "corpus.jsonl")
STAMP = os.path.join(OUT_DIR, "last_refresh.json")
REDLINE = os.path.join(ROOT, ".tmp", "redline")


def comments_dir():
    try:
        import redline  # noqa: WPS433 (path lookup only; nothing is written)
        return str(redline.COMMENTS)
    except Exception:
        return os.path.join(REDLINE, "_comments")


def plain(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", unescape(s)).strip()


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def comment_records():
    files = sorted(glob.glob(os.path.join(comments_dir(), "*.json")))
    files = [f for f in files
             if ".before" not in os.path.basename(f) and not os.path.basename(f).startswith("test__")]
    recs, seen = [], {}
    for f in files:
        key = os.path.basename(f)[:-5]
        try:
            data = load_json(f)
        except Exception as e:  # a half-written file must not kill the run
            print(f"skip {key}: {e}", file=sys.stderr)
            continue
        for t in data.get("threads", []):
            anchor = t.get("anchor") or {}
            base = {
                "key": key,
                "thread": t.get("id"),
                "quote": anchor.get("quote", ""),
                "where": anchor.get("where"),
                "resolved": bool(t.get("resolved")),
                "thread_author": t.get("author"),
            }
            if t.get("author") == "Kevin":
                sig = ("c", t.get("id"), t.get("text"))
                if sig in seen:
                    seen[sig].setdefault("also_in", []).append(key)
                else:
                    r = dict(base, kind="comment", id=f"{key}#{t.get('id')}",
                             created=t.get("created", ""), text=t.get("text", ""))
                    seen[sig] = r
                    recs.append(r)
            for rp in t.get("replies", []) or []:
                if rp.get("author") != "Kevin":
                    continue
                sig = ("r", t.get("id"), rp.get("id"), rp.get("text"))
                if sig in seen:
                    seen[sig].setdefault("also_in", []).append(key)
                    continue
                r = dict(base, kind="reply", id=f"{key}#{t.get('id')}/{rp.get('id')}",
                         created=rp.get("created", ""), text=rp.get("text", ""),
                         thread_text=t.get("text", ""))
                seen[sig] = r
                recs.append(r)
    return recs


def decision_records():
    recs = []
    for f in sorted(glob.glob(os.path.join(REDLINE, "*", "decisions.json"))):
        slug = os.path.basename(os.path.dirname(f))
        try:
            dec = load_json(f)
        except Exception as e:
            print(f"skip round {slug}: {e}", file=sys.stderr)
            continue
        if not isinstance(dec, dict):
            continue
        changes = {}
        prop = os.path.join(REDLINE, slug, "proposal.json")
        if os.path.exists(prop):
            try:
                p = load_json(prop)
                lst = p.get("changes", []) if isinstance(p, dict) else p
                changes = {c.get("id"): c for c in lst if isinstance(c, dict)}
            except Exception:
                pass
        mtime = os.path.getmtime(f)
        for cid, v in dec.items():
            accepted = v if isinstance(v, bool) else (v.get("accepted") if isinstance(v, dict) else None)
            c = changes.get(cid, {})
            recs.append({
                "kind": "decision",
                "id": f"round:{slug}/{cid}",
                "round": slug,
                "change": cid,
                "accepted": accepted,
                "change_kind": c.get("kind"),
                "find": plain(c.get("find") or c.get("old"))[:400],
                "replace": plain(c.get("replace") or c.get("new"))[:400],
                "why": plain(c.get("why") or c.get("note") or c.get("reason"))[:300],
                "decided_mtime": mtime,
            })
    return recs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new", action="store_true", help="print only records not yet folded into model.md")
    ap.add_argument("--mark", action="store_true", help="record every current id as folded into model.md")
    ap.add_argument("--json", action="store_true", help="with --new: print records as JSON lines")
    a = ap.parse_args()

    recs = comment_records() + decision_records()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(CORPUS, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    out = sys.stdout.buffer  # this box's redirected stdout is cp1252; write utf-8 ourselves
    def say(s=""):
        out.write((s + "\n").encode("utf-8"))

    kinds = {}
    for r in recs:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    rej = sum(1 for r in recs if r["kind"] == "decision" and r["accepted"] is False)
    say(f"corpus: {len(recs)} records {kinds}, {rej} rejected changes -> {CORPUS}")

    stamp = load_json(STAMP) if os.path.exists(STAMP) else {"folded_ids": [], "newest_created": ""}
    folded = set(stamp.get("folded_ids", []))

    if a.new:
        new = [r for r in recs if r["id"] not in folded]
        say(f"new since last refresh ({stamp.get('refreshed_at', 'never')}): {len(new)}")
        for r in sorted(new, key=lambda r: (r.get("created") or "", r["id"])):
            if a.json:
                say(json.dumps(r, ensure_ascii=False))
            elif r["kind"] == "decision":
                state = {True: "ACCEPTED", False: "REJECTED", None: "?"}[r["accepted"]]
                say(f"[{state}] {r['id']}: {r['find'][:90]!r} -> {r['replace'][:90]!r}")
            else:
                say(f"[{r['kind']}] {r['id']} {r.get('created', '')[:10]} "
                    f"[{r['quote'][:60]}] {r['text']}")

    if a.mark:
        import datetime
        newest = max((r.get("created") or "" for r in recs if r["kind"] != "decision"), default="")
        stamp = {
            "refreshed_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "newest_created": newest,
            "counts": kinds,
            "folded_ids": sorted(r["id"] for r in recs),
        }
        with open(STAMP, "w", encoding="utf-8") as f:
            json.dump(stamp, f, ensure_ascii=False, indent=1)
        say(f"marked {len(recs)} ids as folded; newest comment {newest} -> {STAMP}")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:  # e.g. piped into head
        pass
