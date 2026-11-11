"""Which articles has Kevin newly finished, so the writer can practice on them (.tmp/kevin_reviewer/after_article.md).

    python tools/finished_articles.py                  dry run: lists what a tick would pick up, writes nothing
    python tools/finished_articles.py --json           the same as JSON
    python tools/finished_articles.py --record         a real tick: remembers what it saw and queues what's newly finished
    python tools/finished_articles.py --practiced KEY [KEY ...]   the writer has practiced on these; drop them from the queue

Newly finished means ALL of:
  * he has reviewed it (a comment or reply by author Kevin) and every thread in its store is closed, Shadow's included
  * no round on it is pending (a proposal with no applied.json, on the article itself or moving text into it)
  * it has changed since the last --record (its comments or its file), or it's still queued from an earlier tick
    (the very first --record only takes a baseline: articles already finished then are the backlog, which the queue item
    "the remaining finished articles" works through, not this tick)
  * no practice has been recorded for it
The six exam articles (backtest/split.json) stay out: they are frozen and aren't rule evidence. An article that's been
practiced and then changed again is listed apart and isn't queued; re-practicing it is a decision, not a tick.

State: .tmp/kevin_reviewer/finished_state.json. Reads the comment store, the rounds and the article files, and writes only
that state file, only with --record or --practiced. Finished is a guess from the margin (he may reopen one), so a tick also
looks at last_activity; see after_article.md.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from article_blocks import article_type                                   # noqa: E402
from draft_paths import locate, WORK, ALL                                 # noqa: E402
from redline import comments_key                                          # noqa: E402
import shadow_signals as ss                                               # noqa: E402

STATE = os.path.join(ROOT, ".tmp", "kevin_reviewer", "finished_state.json")


def round_paths(redline_dir=None):
    """(pending {key: [slugs]}, newest round's article file {key: path})"""
    root = redline_dir or ss.REDLINE
    pending, newest = {}, {}
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        prop = os.path.join(root, name, "proposal.json")
        if not os.path.isfile(prop):
            continue
        try:
            with open(prop, encoding="utf-8-sig") as f:
                p = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(p, dict) or not p.get("article"):
            continue
        d = p.get("article_dir") or ""
        key = comments_key("%s/%s" % (d, p["article"]))
        mtime = os.path.getmtime(prop)
        path = os.path.join(ROOT, d, p["article"]) if not os.path.isabs(d) else os.path.join(d, p["article"])
        if key not in newest or mtime > newest[key][0]:
            newest[key] = (mtime, path)
        if not os.path.exists(os.path.join(root, name, "applied.json")):
            pending.setdefault(key, []).append(name)
            for c in p.get("changes", []):
                if isinstance(c, dict) and c.get("dest_file"):
                    pending.setdefault(comments_key("%s/%s" % (d, c["dest_file"])), []).append(name)
    return pending, {k: v[1] for k, v in newest.items()}


def article_file(key, newest=None, root=None):
    """the copy that holds his final: the file his newest round worked on, else the live page, else a draft, else the archive"""
    root = root or ROOT
    if newest and key in newest and os.path.isfile(newest[key]):
        return newest[key]
    country, _, page = key.partition("__")
    live = os.path.join(root, country, page + ".html")
    if os.path.isfile(live):
        return live
    rel = locate(country, page + ".html", WORK) or locate(country, page + ".html", ALL)
    return os.path.join(ROOT, rel) if rel else None


def fingerprint(store, path):
    h = hashlib.sha1()
    summary = sorted((str(t.get("id")), bool(t.get("resolved")), len(t.get("replies") or []), t.get("text", ""))
                     for t in store.get("threads", []) if isinstance(t, dict))
    h.update(json.dumps(summary, ensure_ascii=False).encode("utf-8"))
    if path and os.path.isfile(path):
        with open(path, "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:16]


def _facts(key, store, pending, newest, root):
    deleted = ss.deleted_ids(store)
    threads = [t for t in store.get("threads", []) if isinstance(t, dict) and t.get("id") not in deleted]
    open_threads = [t for t in threads if not t.get("resolved")]
    times = []
    own = 0
    for t in threads:
        if t.get("author") == ss.KEVIN:
            own += 1
            times.append(t.get("created", ""))
        times += [r.get("created", "") for r in ss.kevin_replies(t)]
    path = article_file(key, newest, root)
    return {"key": key, "type": article_type(key), "file": os.path.relpath(path, ROOT).replace("\\", "/") if path else None,
            "own_comments": own, "reviewed": bool(times), "open_threads": len(open_threads),
            "open_by_author": _by_author(open_threads), "pending_rounds": sorted(set(pending.get(key, []))),
            "last_activity": max([x for x in times if x], default=""), "fingerprint": fingerprint(store, path)}


def _by_author(threads):
    out = {}
    for t in threads:
        a = t.get("author") or "?"
        out[a] = out.get(a, 0) + 1
    return out


def scan(state=None, comments_dir=None, redline_dir=None, root=None):
    """every article's facts plus its label; `state` is the finished_state.json content"""
    state = state or {}
    known = state.get("articles", {})
    exam = ss.exam_keys()
    stores = ss.load_stores(comments_dir)
    pending, newest = round_paths(redline_dir)
    rows = []
    for key, store in stores.items():
        f = _facts(key, store, pending, newest, root)
        prev = known.get(key, {})
        f["changed"] = f["fingerprint"] != prev.get("fp")
        f["queued"] = bool(prev.get("queued"))
        f["practiced"] = prev.get("practiced")
        f["finished"] = f["reviewed"] and not f["open_threads"] and not f["pending_rounds"] and bool(f["file"]) and f["type"] != "country-page"
        if key in exam:
            f["status"] = "exam"
        elif f["type"] == "country-page":
            f["status"] = "country-page"
        elif not f["reviewed"]:
            f["status"] = "not-reviewed"
        elif not f["file"]:
            f["status"] = "no-file"
        elif f["open_threads"]:
            f["status"] = "open-threads"
        elif f["pending_rounds"]:
            f["status"] = "pending-round"
        elif f["practiced"]:
            f["status"] = "practiced-changed" if f["changed"] else "practiced"
        elif not state.get("last_run"):
            f["status"] = "backlog"
        elif f["changed"] or f["queued"]:
            f["status"] = "new"
        else:
            f["status"] = "seen"
        rows.append(f)
    return rows


def finished_articles(comments_dir=None, redline_dir=None, root=None):
    """[{key, type, file, ...}] every finished article outside the exam set, for tools/rule_test.py"""
    return [r for r in scan({}, comments_dir, redline_dir, root) if r["finished"] and r["status"] != "exam"]


def read_state(path=STATE):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"articles": {}}


def write_state(state, path=STATE):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def record(rows, state):
    """what a real tick remembers: every fingerprint, and a queue entry for each newly finished article"""
    arts = state.setdefault("articles", {})
    state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in rows:
        e = arts.setdefault(r["key"], {})
        e["fp"] = r["fingerprint"]
        e["queued"] = r["status"] == "new"
    return state


def mark_practiced(keys, rows, state):
    arts = state.setdefault("articles", {})
    known = {r["key"]: r for r in rows}
    missing = [k for k in keys if k not in known]
    if missing:
        raise ValueError("no comment store for: " + ", ".join(missing))
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for k in keys:
        e = arts.setdefault(k, {})
        e["fp"] = known[k]["fingerprint"]
        e["queued"] = False
        e["practiced"] = when
    return state


def render(rows):
    new = sorted((r for r in rows if r["status"] == "new"), key=lambda r: (r["last_activity"], r["key"]))
    lines = ["Newly finished: %d" % len(new)]
    for r in new:
        lines.append("  %s (%s), %d comments, last activity %s, %s%s" % (
            r["key"], r["type"], r["own_comments"], r["last_activity"][:10] or "?", r["file"], "  [queued]" if r["queued"] else ""))
    groups = [("backlog", "Finished before the first baseline (the backlog; not queued)"), ("practiced-changed", "Practiced, then changed again (not queued)"), ("practiced", "Practiced"), ("seen", "Finished, seen before, not queued"),
              ("exam", "Exam articles (frozen, never practiced here)"), ("pending-round", "Waiting on a round"), ("open-threads", "Threads still open"),
              ("not-reviewed", "No comment by Kevin yet"), ("no-file", "Reviewed, but no article file found")]
    for status, label in groups:
        g = [r for r in rows if r["status"] == status]
        if not g:
            continue
        lines.append("%s: %d" % (label, len(g)))
        for r in sorted(g, key=lambda r: r["key"]):
            extra = ""
            if status == "pending-round":
                extra = " (%s)" % ", ".join(r["pending_rounds"])
            elif status == "open-threads":
                extra = " (%s)" % ", ".join("%s %d" % kv for kv in sorted(r["open_by_author"].items()))
            lines.append("  %s%s" % (r["key"], extra))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--record", action="store_true", help="a real tick: remember what was seen and queue what's newly finished")
    ap.add_argument("--practiced", nargs="+", metavar="KEY", help="record that the writer has practiced on these articles")
    a = ap.parse_args(argv)
    state = read_state()
    rows = scan(state)
    if a.practiced:
        try:
            write_state(mark_practiced(a.practiced, rows, state))
        except ValueError as e:
            sys.stderr.write("finished_articles: %s\n" % e)
            return 2
        ss.utf8_out("marked practiced: %s\n" % ", ".join(a.practiced))
        return 0
    if a.record:
        write_state(record(rows, state))
    ss.utf8_out(json.dumps(rows, ensure_ascii=False, indent=1) + "\n" if a.json else render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
