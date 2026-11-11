"""Which Shadow reviewer threads may go into the next round without a typed yes.

Kevin, 2026-11-10: "things like link and prices will not require me to type yes. A no response means it can go in the
next round and I don't need to confirm. maybe add a way to confirm I reviewed all of the shadow comments as a fail safe
in case I just quickly open the article and close it without actually reviewing myself". Later the same day: links go
straight through, and only prices wait for that confirmation.

Two groups, and a thread is in one only if ALL of its conditions hold:

  links   a Shadow reviewer thread, not resolved, not deleted, no reply from Kevin, shadow.category starts with "link"
  prices  the same, shadow.category is in PRICE_KINDS, AND its id is in an "I've reviewed Shadow's comments" click
          (review.js) that was not undone. With no such click this group is empty, and the output says so.

Nothing else qualifies: wording, repetition, facts, detail. Read-only: this opens the comment store and writes nothing.

The confirmation is a log inside the store's `deleted` list, because tools/redline.py comments_save keeps only `threads`
and `deleted` and merges `deleted` as a union (see the long comment above SC_ON in review.js):

    "shadow-consent:{"id": "sc-...", "at": "<iso time>", "threads": ["sr-...", ...]}"
    "shadow-consent-undo:{"id": "sc-...", "at": "<iso time>"}"

An Undo names one click by its id, and any click in force can be undone from the margin (its "All N" list), not only the
newest; a cancelled click's threads are simply no longer covered. `confirmations` in the report lists the clicks in force.

    python tools/shadow_consent.py greece__athens
    python tools/shadow_consent.py greece__athens --json
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMENTS = ROOT / ".tmp" / "redline" / "_comments"
SHADOW_AUTHOR = "Shadow reviewer"
SC_ON, SC_OFF = "shadow-consent:", "shadow-consent-undo:"
LINK_PREFIX = "link"
PRICE_KINDS = ("currency", "fill: currency", "fill: price")
NO_CLICK = "No confirmation recorded for this article, so its price comments still need a typed yes."
NO_STORE = "No comment store for this article, so nothing may go in without a typed yes."


def confirmations(store):
    """the confirmations in force, oldest first: every record in the log that no later Undo cancelled"""
    on, off = [], set()
    for entry in store.get("deleted") or []:
        if not isinstance(entry, str):
            continue
        try:
            if entry.startswith(SC_ON):
                rec = json.loads(entry[len(SC_ON):])
                if isinstance(rec, dict) and rec.get("id") and rec.get("at") and isinstance(rec.get("threads"), list):
                    on.append(rec)
            elif entry.startswith(SC_OFF):
                off.add(json.loads(entry[len(SC_OFF):]).get("id"))
        except (ValueError, AttributeError):       # a mangled entry is no confirmation at all
            continue
    return [rec for rec in on if rec["id"] not in off]


def _norm(category):
    return (category or "").strip().lower() if isinstance(category, str) else ""


def is_link(category):
    # "unnamed place" threads ask something only Kevin knows ("which hostel? link"), so they still need his answer
    return _norm(category).startswith(LINK_PREFIX) and "unnamed" not in _norm(category)


def is_price(category):
    return _norm(category) in PRICE_KINDS


def _replied(thread, match):
    return any(match((r.get("author") or "").strip().lower()) for r in thread.get("replies") or [] if isinstance(r, dict))


def _kevin_replied(thread):
    return _replied(thread, lambda a: a.startswith("kevin"))


def qualifying(store):
    """(the confirmations in force, the link threads, the price threads) that may go in without a typed yes"""
    recs = confirmations(store)
    covered = {}
    for rec in recs:
        for tid in rec["threads"]:
            covered[tid] = rec["at"]
    gone = {x for x in store.get("deleted") or [] if isinstance(x, str)}
    links, prices = [], []
    for t in store.get("threads") or []:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        if t.get("author") != SHADOW_AUTHOR or tid in gone or t.get("resolved") or _kevin_replied(t):
            continue
        shadow = t.get("shadow") if isinstance(t.get("shadow"), dict) else {}
        category = shadow.get("category")
        row = {"id": tid, "category": category, "text": t.get("text", ""), "quote": (t.get("anchor") or {}).get("quote", ""),
               "confidence": shadow.get("confidence"), "reviewed_at": covered.get(tid),
               "claude_replied": _replied(t, lambda a: a == "claude")}      # a round may already answer it: a builder must not double up
        if is_link(category):
            links.append(row)
        elif is_price(category) and tid in covered:
            prices.append(row)
    return recs, links, prices


def report(key, comments_dir=None):
    """the result as a dict, for --json and for anything that imports this"""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", key):
        raise ValueError("not an article key: %r" % key)
    path = Path(comments_dir or COMMENTS) / (key + ".json")
    if not path.exists():
        return {"key": key, "confirmed": False, "reviewed_at": None, "covered_ids": [], "links": [], "prices": [], "note": NO_STORE}
    store = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(store, dict):
        raise ValueError("%s is not a comment store" % path.name)
    recs, links, prices = qualifying(store)
    return {"key": key, "confirmed": bool(recs), "reviewed_at": recs[-1]["at"] if recs else None,
            "covered_ids": sorted({tid for rec in recs for tid in rec["threads"]}), "links": links, "prices": prices,
            "confirmations": in_force(store, recs), "note": "" if recs else NO_CLICK}


def in_force(store, recs=None):
    """every click still in force, oldest first, as the margin lists them (QA 2026-11-10: any click can be undone there,
    not only the newest): its id, when, the threads it covers, and which of them are still open Shadow threads"""
    recs = confirmations(store) if recs is None else recs
    gone = {x for x in store.get("deleted") or [] if isinstance(x, str)}
    open_ids = {t.get("id") for t in store.get("threads") or [] if isinstance(t, dict)
                and t.get("author") == SHADOW_AUTHOR and not t.get("resolved") and t.get("id") not in gone}
    return [{"id": r["id"], "at": r["at"], "threads": list(r["threads"]), "open": [x for x in r["threads"] if x in open_ids]} for r in recs]


def _rows(group):
    lines = []
    for t in group:
        lines.append("  %s  [%s]%s" % (t["id"], t["category"], "  (Claude already replied)" if t["claude_replied"] else ""))
        lines.append("      %s" % t["text"])
        if t["quote"]:
            lines.append("      on: %s" % t["quote"])
    return lines


def local_day(iso):
    """'2026-11-11T01:30:00.000Z' -> '2026-11-10' on a Pacific clock: the day the margin shows ("reviewed Nov 10"), not
    the UTC day, which is already tomorrow for any click after 4 pm (QA 2026-11-10 #13). A time without a zone is local."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (dt.astimezone() if dt.tzinfo else dt).date().isoformat()
    except (ValueError, AttributeError, TypeError):
        return (iso or "")[:10]


def plain_text(rep):
    if rep["note"] == NO_STORE:
        return "%s: %s\n" % (rep["key"], rep["note"])
    lines = ["%s: %s" % (rep["key"], "reviewed %s (%d Shadow threads covered)." % (local_day(rep["reviewed_at"]), len(rep["covered_ids"])) if rep["confirmed"] else rep["note"])]
    if len(rep.get("confirmations") or []) > 1:     # several clicks in force: each one, as the margin's "All N" list shows them
        for c in rep["confirmations"]:
            lines.append("  click %s on %s: %d threads, %d still open" % (c["id"], local_day(c["at"]), len(c["threads"]), len(c["open"])))
    lines += ["", "Links, no confirmation needed: %d" % len(rep["links"])]
    lines += _rows(rep["links"])
    lines += ["", "Prices, confirmed by his click: %d" % len(rep["prices"])]
    lines += _rows(rep["prices"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Shadow threads that may go into the next round without a typed yes (read-only).")
    ap.add_argument("key", help="the article's comment key, e.g. greece__athens")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--comments-dir", default=None, help="read this store folder instead of .tmp/redline/_comments (tests)")
    a = ap.parse_args(argv)
    try:
        rep = report(a.key, a.comments_dir)
    except ValueError as e:                        # also a torn JSON file: never read that as "nothing qualifies"
        sys.stderr.buffer.write(("shadow_consent: %s\n" % e).encode("utf-8"))
        return 2
    text = json.dumps(rep, indent=1, ensure_ascii=False) + "\n" if a.json else plain_text(rep)
    sys.stdout.buffer.write(text.encode("utf-8"))  # redirected stdout on this box is cp1252
    return 0


if __name__ == "__main__":
    sys.exit(main())
