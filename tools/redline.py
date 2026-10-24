"""Redline review for AI edits: the article with every proposed change marked inline, Word style,
and a Changes pane beside it with Accept / Reject on each one.

How it fits the editor
----------------------
When Claude finishes an edit pass on an article it writes a PROPOSAL rather than touching the
file, then opens a review tab:

    .tmp/redline/<slug>/proposal.json     the changes (see schema below)
    .tmp/redline/<slug>/baseline/         the affected files exactly as they were
    python tools/redline.py open <slug>   opens http://127.0.0.1:5003/redline/<slug> in a new tab

The page is served by tools/photo_editor.py (same server as the editor) so it gets the site's
real stylesheets and photos through /site/. It is a separate tab and never touches the editor's
DOM, so working on another article in the editor is undisturbed. Accept / Reject decisions POST
back and are stored; Apply writes the accepted changes to the articles with every safeguard
below, backs the originals up, and records what was applied.

Proposal schema (one object per change, ids stable across rebuilds):
    grammar : {id, kind:"grammar", section, find, replace, count, note}
    cut     : {id, kind:"cut", section, words, sentence, bridge_before, dest_file, dest_after,
               dest_replaces, covered_by, dup_scope, note, context_before, context_after}
A cut with dest_replaces REPLACES that sentence in dest_file with the itinerary's own; with
dest_after it is inserted after that anchor; with neither it is simply removed.

Safeguards on apply (each one caught a real fault during the first pass on Armenia):
  * grammar fixes are applied first and folded into any cut sentence that contains them, so a
    sentence that moves arrives corrected instead of carrying the typo
  * every find / sentence / anchor must match exactly once, or nothing is written
  * a paragraph or list emptied by its cuts is removed, or it renders as a blank line
  * no Google Maps link may vanish from the site: every URL in the article before must exist in
    at least one affected file after, or nothing is written (this caught Vernissage Market)
"""
import html
import json
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / ".tmp" / "redline"
SERVER = "http://127.0.0.1:5003"
MAPS = re.compile(r'href="(https://www\.google\.com/maps/place/[^"]+)"')


# ============================================================== store
# Every write to a round's files (decisions, progress, applied) holds this lock and goes through a temp
# file named for its writer. Thirty decision writers plus commits and prechecks shared one
# "decisions.json.tmp" and tore the file ("Extra data line 40"); the round then answered 400 to
# everything until it was repaired by hand (stress test 2026-10-09).
_store_lock = threading.RLock()


class _flock:
    """A lock across processes: the photo server and a `redline.py` / `claude_answer.py` process both
    read-modify-write the same store, and each other's threads were dropped (418 of 1260, stress test
    2026-10-09; the in-process lock cannot see the other process). An exclusive-create lock file, waited
    for up to 15 s; one older than 60 s belongs to a process that died and is taken over."""

    def __init__(self, path):
        self.path = Path(str(path) + ".lock")

    def __enter__(self):
        t0 = time.time()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode()); os.close(fd)
                return self
            except (FileExistsError, PermissionError):
                # Windows answers EACCES, not EEXIST, for a lock file another process is deleting at that
                # moment: 2 of 250 concurrent saves raised straight through (stress test 2026-10-15)
                try:
                    if time.time() - self.path.stat().st_mtime > 60:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() - t0 > 15:
                    raise TimeoutError("the store %s is locked by another process" % self.path.name)
                time.sleep(0.02 + 0.03 * ((time.time() * 1000) % 3))

    def __exit__(self, *a):
        try:
            self.path.unlink()
        except OSError:
            pass


def _read_json(path, default, strict=False):
    """A store file, read with patience (a writer may be replacing it) and without letting a torn one
    lock the round: it is set aside as <name>.corrupt-<time> and treated as empty. strict: a file that
    stays locked is an error, never "empty" (a comment save that read the store as empty while another
    process replaced it wrote the empty store back; stress test 2026-10-09)."""
    for i in range(8):
        try:
            if not path.exists():
                return default
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if strict and i == 7:
                raise
            time.sleep(0.05 * (i + 1))
        except ValueError:
            # one copy per torn file, not one per read
            if not any(c.stat().st_mtime >= path.stat().st_mtime for c in path.parent.glob(path.name + ".corrupt-*")):
                try:
                    shutil.copy2(path, path.with_name(path.name + ".corrupt-%d" % int(time.time())))
                except OSError:
                    pass
            if strict:
                # a torn comment store read as "empty" let the next save write the empty store back, and every
                # thread survived only in the .corrupt copy (stress test 2026-10-15). An error keeps them.
                raise ValueError("%s is unreadable (torn JSON); a copy is beside it as %s.corrupt-*" % (path.name, path.name))
            print("redline: %s was unreadable and is treated as empty (a copy was kept)" % path, file=sys.stderr)
            return default
    return default


def _write_json(path, obj):
    tmp = path.with_name("%s.%d.%d.tmp" % (path.name, os.getpid(), threading.get_ident()))
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    _swap(tmp, path)


def load(slug):
    d = STORE / slug
    p = json.loads((d / "proposal.json").read_text(encoding="utf-8"))
    p["_dir"] = d
    p["decisions"] = _read_json(d / "decisions.json", {})
    p["applied"] = _read_json(d / "applied.json", None)
    # a round saved in more than one sitting keeps its running tally in progress.json;
    # commit() used to start from p["applied"] alone, so every save after the first
    # threw the earlier one's ids away and a round could only complete in one go
    p["progress"] = _read_json(d / "progress.json", None)
    return p


def save_decisions(slug, decisions):
    with _store_lock:
        _write_json(STORE / slug / "decisions.json", decisions)


def pending():
    out = []
    for d in sorted(STORE.glob("*/proposal.json")):
        p = json.loads(d.read_text(encoding="utf-8"))
        out.append({"slug": p["slug"], "title": p.get("title", p["slug"]), "changes": len(p["changes"]),
                    "applied": (d.parent / "applied.json").exists()})
    return out


# ============================================================== marking the article
def fixed(sentence, changes):
    for g in changes:
        if g["kind"] == "grammar":
            sentence = sentence.replace(g["find"], g["replace"])
    return sentence


# ============================================================== the page
# ============================================================== apply
def apply(slug, decisions=None, root=None, dry=False):
    """Write the accepted changes. Validates everything first and writes nothing on any fault.
    `root` lets a rehearsal target a scratch copy of the article folder."""
    p = load(slug)
    decisions = decisions if decisions is not None else p["decisions"]
    arts = Path(root) if root else ROOT / p["article_dir"]
    changes = p["changes"]
    ok = lambda cid: bool(decisions.get(cid, False))
    files, problems, plan = {}, [], []

    def get(name):
        if name not in files:
            files[name] = (arts / name).read_text(encoding="utf-8", newline="")
        return files[name]

    cuts = [c for c in changes if c["kind"] == "cut"]
    for g in changes:
        if g["kind"] != "grammar" or not ok(g["id"]):
            continue
        s = get(p["article"])
        if s.count(g["find"]) != 1:
            problems.append(f'{g["id"]}: text appears {s.count(g["find"])}x'); continue
        plan.append(("replace", p["article"], g["find"], g["replace"], g["id"]))
        for c in cuts:                       # the fix travels with a moved sentence
            if g["find"] in c["sentence"]:
                c["sentence"] = c["sentence"].replace(g["find"], g["replace"])
    for c in cuts:
        if not ok(c["id"]):
            continue
        s = get(p["article"])
        for op, f, a, b, _ in plan:
            if op == "replace" and f == p["article"]:
                s = s.replace(a, b)
        if s.count(c["sentence"]) != 1:
            problems.append(f'{c["id"]}: sentence appears {s.count(c["sentence"])}x'); continue
        plan.append(("cut", p["article"], c["sentence"], c.get("bridge_before", ""), c["id"]))
        d = c.get("dest_file")
        if not d:
            continue
        t = get(d)
        if c.get("dest_replaces"):
            if t.count(c["dest_replaces"]) != 1:
                problems.append(f'{c["id"]}: replace target appears {t.count(c["dest_replaces"])}x in {d}'); continue
            plan.append(("replace", d, c["dest_replaces"], c["sentence"], c["id"]))
        else:
            if t.count(c.get("dest_after", "")) != 1:
                problems.append(f'{c["id"]}: anchor appears {t.count(c.get("dest_after", ""))}x in {d}'); continue
            plan.append(("insert", d, c["dest_after"], c["sentence"], c["id"]))
    if problems:
        return {"ok": False, "problems": problems}

    for op, f, a, b, _ in plan:
        s = get(f)
        if op == "replace":
            s = s.replace(a, b)
        elif op == "cut":
            i = s.index(a); j = i + len(a)
            while j < len(s) and s[j] == " ":
                j += 1
            s = s[:i] + (b + " " if b else "") + s[j:]
        elif op == "insert":
            i = s.index(a) + len(a); s = s[:i] + " " + b + s[i:]
        files[f] = s
    for f in list(files):
        s = files[f]
        s = re.sub(r'\n?[ \t]*<p class="copy day-copy">\s*</p>', "", s)
        s = re.sub(r"\n?[ \t]*<ul(?:\s[^>]*)?>\s*</ul>", "", s)
        files[f] = s

    before = set(MAPS.findall((arts / p["article"]).read_text(encoding="utf-8", newline="")))
    after = set()
    for f in p["files"]:
        after |= set(MAPS.findall(files.get(f) or (arts / f).read_text(encoding="utf-8", newline="")))
    lost = sorted(before - after)
    if lost:
        return {"ok": False, "problems": ["these Maps links would disappear from the site: " + ", ".join(u[:80] for u in lost)]}

    n_g = sum(1 for c in changes if c["kind"] == "grammar" and ok(c["id"]))
    n_c = sum(1 for c in cuts if ok(c["id"]))
    words = sum(c.get("words", 0) for c in cuts if ok(c["id"]))
    result = {"ok": True, "grammar": n_g, "cuts": n_c, "words": words, "files": sorted(files), "dry": dry}
    if not dry:
        # a rehearsal (root given) keeps its backups beside the scratch copy and records nothing
        # in the store: the first one wrote applied.json for the REAL article and would have shown
        # the live review as already applied, with the button disabled, while nothing had changed
        bdir = (arts.parent / (arts.name + "-before-apply")) if root else (p["_dir"] / "before-apply")
        bdir.mkdir(parents=True, exist_ok=True)
        for f, s in files.items():
            shutil.copy2(arts / f, bdir / f)
            (arts / f).write_text(s, encoding="utf-8", newline="")
        result["applied_at"] = datetime.now().isoformat(timespec="seconds")
        result["backups"] = str(bdir)
        if not root:
            (p["_dir"] / "applied.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def open_tab(slug):
    """Open the review in a new browser tab. By default that is the EDITOR, at
    /browser#review=<article path>: the pane opens on the comments and changes at once and asks
    for one click on Open Article, after which everything anchors in place and Accept / Reject
    work on the live text."""
    from urllib.parse import quote
    p = load(slug)
    # a query parameter: the editor's view router owns the URL hash
    url = f"{SERVER}/browser?review=" + quote(p["article_dir"] + "/" + p["article"], safe="/")
    try:
        os.startfile(url)              # Windows: the default browser, in a new tab
    except Exception:
        webbrowser.open_new_tab(url)
    return url


# ============================================================== page chrome
# ============================================================== editor integration
# The review lives inside the article editor as a right-hand pane (review.js). The editor asks
# /review/for?rel=<repo path> when an article opens, marks the pending changes in its own DOM,
# posts each Accept / Reject as it happens, and on Save strips every mark: accepted changes are
# already in the text it writes, everything else is written as the original. commit() then does
# the half the editor cannot: the destination side of accepted MOVES, which live in other files.
COMMENTS = STORE / "_comments"


def slug_for(rel):
    """slug of the pending proposal for this repo-relative article path, else None"""
    rel = re.sub(r"^(?:\./|/)+", "", rel.replace("\\", "/"))   # not lstrip("./"): it ate the dot of ".tmp"
    # The NEWEST open round wins. This returned the first match in directory order, so a round
    # with one change left undecided (yerevan-r7's hero subtitle) kept counting as open and was
    # served in place of the round written after it: the new tab showed two stale changes and
    # none of the new ones. A round a newer one replaces should be closed, but the newest must
    # win even when it is not.
    best = None
    for d in STORE.glob("*/proposal.json"):
        p = json.loads(d.read_text(encoding="utf-8"))
        if p["article_dir"] + "/" + p["article"] == rel and not (d.parent / "applied.json").exists():
            m = d.stat().st_mtime
            if best is None or m > best[0]:
                best = (m, p["slug"])
    return best[1] if best else None


def comments_key(rel):
    """comments are per ARTICLE, proposal or not: 'armenia/yerevan.html' -> 'armenia__yerevan'"""
    parts = [x for x in rel.replace("\\", "/").split("/") if x and x not in ("Drafts", ".Full Articles")]
    return "__".join(re.sub(r"[^A-Za-z0-9-]+", "-", x.lstrip(".").rsplit(".", 1)[0]) for x in parts[-2:])


def comments_load(key):
    # retried: a read that met a save replacing the file was a PermissionError, a 500 to the editor
    # (4.7% of reads under 50 writers, stress test 2026-10-09)
    d = _read_json(COMMENTS / f"{key}.json", None, strict=True) or {"threads": []}
    if not isinstance(d, dict) or not isinstance(d.get("threads"), list):
        raise ValueError("%s.json is not a comment store (expected {\"threads\": [...]}); repair it by hand" % key)
    return d


def task_lines():
    """every thread Kevin sent to Claude (kind rewrite / task) that has no answer yet"""
    out = []
    for f in sorted(COMMENTS.glob("*.json")):
        for t in json.loads(f.read_text(encoding="utf-8")).get("threads", []):
            if t.get("kind") in ("rewrite", "task") and not t.get("resolved") and not t.get("edit"):
                q = (t.get("anchor") or {}).get("quote", "")[:160]
                out.append("  %-40s %s  %s" % (f.stem, t["id"], t.get("text", "")))
                out.append("      “%s”" % q)
    return out


# one lock per article: a single lock for every article queued 50 writers on 50 different articles
# behind each other (p50 1.7 s, stress test 2026-10-09)
_comments_locks, _comments_locks_guard = {}, threading.Lock()


def _comments_lock_for(key):
    with _comments_locks_guard:
        return _comments_locks.setdefault(key, threading.Lock())


def comments_save(key, data):
    """Merge a tab's threads into the store (QA 2026-10-02: two tabs on one article overwrote each
    other's comments). A thread only on disk stays (another tab's), unless this tab deleted it;
    a thread both have takes this tab's text and resolved flag, every reply from both, and never
    goes back from answered to sent (Claude's answer may have landed since the tab loaded)."""
    # one save at a time: this is read-modify-write, and two tabs saving together each merged into the
    # same old file, so one tab's new threads were dropped (QA 2026-10-08: 4 of 100 lost)
    COMMENTS.mkdir(parents=True, exist_ok=True)
    with _comments_lock_for(key), _flock(COMMENTS / f"{key}.json"):
        return _comments_save(key, data)


def _comments_save(key, data):
    COMMENTS.mkdir(parents=True, exist_ok=True)
    f = COMMENTS / f"{key}.json"
    cur = _read_json(f, None, strict=True) or {"threads": []}      # a locked store is an error, never empty
    if not isinstance(cur, dict) or not isinstance(cur.get("threads"), list):
        raise ValueError("%s is not a comment store; repair it by hand" % f.name)
    def _tidy(t):                             # one malformed thread wedged an article's store (stress test 2026-10-15/16)
        t["replies"] = [r for r in (t.get("replies") or []) if isinstance(r, dict)]
        for k in ("at", "created", "status", "text", "kind"):
            if k in t and t[k] is not None and not isinstance(t[k], str):
                t[k] = str(t[k])
        for r in t["replies"]:
            for k in ("id", "created", "edited", "text", "author"):
                if k in r and r[k] is not None and not isinstance(r[k], str):
                    r[k] = str(r[k])
    cur["threads"] = [t for t in cur["threads"] if isinstance(t, dict)]
    for t in cur["threads"]:
        _tidy(t)
    posted = {t["id"]: t for t in (data.get("threads") or []) if isinstance(t, dict) and t.get("id")}
    for t in posted.values():
        _tidy(t)
    dl_posted = [x for x in (data.get("deleted") if isinstance(data.get("deleted"), list) else []) if isinstance(x, str)]   # a string was taken letter by letter
    deleted = set(dl_posted) | set(x for x in (cur.get("deleted") or []) if isinstance(x, str))
    DONE = ("answered", "applied", "offered", "proposed", "rejected", "failed")
    merged, seen = [], set()
    for t in cur.get("threads", []):
        tid = t.get("id")
        if tid in deleted:
            continue
        p = posted.get(tid)
        if p is None:
            merged.append(t)
        else:
            # a thread another tab changed more recently (resolved, reopened, edited) keeps that tab's
            # version; it used to be whichever tab saved last (QA 2026-10-08)
            if (t.get("at") or "") > (p.get("at") or ""):
                # ...including what it did with Claude's answer: a stale tab's save put "offered" back over
                # another tab's "applied" (stress test 2026-10-09)
                p = dict(p, **{k: t[k] for k in ("text", "resolved", "at", "status", "edit", "edits", "kind", "anchor") if k in t})
            # replies: the union by id; one both have keeps the newer edit of it
            mine = {r.get("id"): r for r in p.get("replies", []) if isinstance(r, dict)}
            changed = False
            for r in t.get("replies", []):
                rid = r.get("id")
                if rid not in mine:
                    mine[rid] = r; changed = True
                elif (r.get("edited") or "") > (mine[rid].get("edited") or ""):
                    mine[rid] = r; changed = True
            if changed:
                p = dict(p, replies=sorted(mine.values(), key=lambda r: r.get("created", "")))
            if t.get("status") in DONE and p.get("status") in (None, "sent"):
                # ...or that has never heard it was sent at all: a stale tab's save stripped kind, status and
                # the edit, and the asking tab waited forever (stress test 2026-10-09)
                p = dict(p, **{k: t[k] for k in ("status", "edit", "edits", "kind", "sentAt") if k in t})
            merged.append(p)
        seen.add(tid)
    for tid, p in posted.items():
        if tid not in seen and tid not in deleted:
            merged.append(p)
    # deletions newest-last: the oldest fall off at 300, not whichever ids sort first
    dl = [x for x in (cur.get("deleted") or []) if isinstance(x, str)]
    for x in dl_posted:
        if x not in dl:
            dl.append(x)
    out = {"threads": merged, "deleted": dl[-300:]}
    # a temp name per writer: a shared one let one save's replace() take the file another was about to
    # move, which raised FileNotFoundError (a 500) in the other
    tmp = f.with_name("%s.%d-%d.tmp" % (f.name, os.getpid(), __import__("threading").get_ident()))
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    _swap(tmp, f)
    return out


def _swap(tmp, dest):
    """tmp -> dest, retried: OneDrive (or a reader) can hold dest for a moment, and the one
    PermissionError that caused turned a comment save into a 500 (2026-10-03)"""
    import time
    for i in range(6):
        try:
            tmp.replace(dest)
            return
        except PermissionError:
            if i == 5:
                raise
            time.sleep(0.15 * (i + 1))


def merge_decisions(slug, partial):
    """decisions arrive one at a time from the editor; null means undecided again (an undo). With nothing
    to merge it only READS: precheck and commit call it that way, and used to rewrite the file each time."""
    d = STORE / slug
    with _store_lock, _flock(d / "decisions.json"):
        cur = _read_json(d / "decisions.json", {})
        if not partial:
            return cur
        for k, v in partial.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = bool(v)
        _write_json(d / "decisions.json", cur)
        return cur


def _commit_plan(slug, accepted_ids, article_text=None):
    """What a commit of these accepted changes would write, without writing it: the destination side
    of each accepted MOVE, the moves that cannot be placed (their anchor is missing or doubled), and
    the Maps links the baseline had that would be on no page afterwards. `article_text` is the article
    as it will be saved; by default, as it is on disk."""
    p = load(slug)
    arts = ROOT / p["article_dir"]
    by_id = {c["id"]: c for c in p["changes"]}
    acc = [by_id[i] for i in accepted_ids if i in by_id]
    applied = p["applied"] or p["progress"] or {}
    # an applied.json written by apply() (the server-side round) has no "ids": every save after one
    # failed with KeyError: 'ids' (QA 2026-10-08)
    applied = {"ids": [], "moves": [], "log": [], **applied}
    todo = [c for c in acc if c["kind"] == "cut" and c.get("dest_file") and c["id"] not in applied["ids"]]
    files, problems, plan = {}, [], []
    get = lambda f: files.setdefault(f, (arts / f).read_text(encoding="utf-8", newline=""))
    # a moved sentence carries only the grammar fixes Kevin actually accepted, not every one
    # proposed: he may keep a sentence's wording and still send it to the supporting article
    decided = merge_decisions(slug, {})
    accepted_fixes = [g for g in p["changes"] if g["kind"] == "grammar" and decided.get(g["id"]) is True]
    for c in todo:
        t, incoming = get(c["dest_file"]), fixed(c["sentence"], accepted_fixes)
        if incoming in t:
            continue                                     # already there (a re-save)
        if c.get("dest_replaces"):
            if t.count(c["dest_replaces"]) != 1:
                problems.append({"id": c["id"], "why": f'replace target appears {t.count(c["dest_replaces"])}x in {c["dest_file"]}'}); continue
            plan.append((c["dest_file"], c["dest_replaces"], incoming, c["id"]))
        else:
            a = c.get("dest_after", "")
            if t.count(a) != 1:
                problems.append({"id": c["id"], "why": f'anchor appears {t.count(a)}x in {c["dest_file"]}'}); continue
            plan.append((c["dest_file"], a, a + " " + incoming, c["id"]))
    for f, a, b, _ in plan:
        files[f] = files[f].replace(a, b, 1)
    base = (p["_dir"] / "baseline" / p["article"]).read_text(encoding="utf-8")
    art = article_text if article_text is not None else (arts / p["article"]).read_text(encoding="utf-8", newline="")
    have = set(MAPS.findall(art))
    for f in p["files"]:
        if f != p["article"]:
            have |= set(MAPS.findall(files.get(f) or (arts / f).read_text(encoding="utf-8", newline="")))
    lost = sorted(set(MAPS.findall(base)) - have)
    return {"p": p, "arts": arts, "by_id": by_id, "acc": acc, "applied": applied, "files": files,
            "plan": plan, "problems": problems, "lost": lost}


def precheck(slug, accepted_ids, article_html):
    """Before the editor writes the article: would this save strand a moved sentence or take a Maps
    link off the site? The editor asks first and does not write when it would (QA 2026-10-08: the
    check ran AFTER the article was saved, refused the commit, and the moved sentence was then in
    neither article)."""
    k = _commit_plan(slug, accepted_ids, article_html)
    return {"ok": not k["problems"] and not k["lost"], "problems": k["problems"],
            "lost": [u[:120] for u in k["lost"]]}


def commit(slug, accepted_ids):
    """After the editor saved the article with these changes accepted in its text: apply the
    destination side of any accepted MOVE (insert or replace in another file, with backups), then
    record them as applied. Grammar fixes and the source side of every cut are already in the saved
    file, so nothing is written to the article itself.

    The article is already saved when this runs, so a move that can be placed is ALWAYS written: the
    old all-or-nothing refusal left a moved sentence in neither article. A move that cannot be placed
    stays unapplied (its sentence is in before-apply/ and the round), and is reported; so are Maps
    links that are now on no page. precheck() is what stops those before the save."""
    k = _commit_plan(slug, accepted_ids)
    p, arts, by_id, acc, applied, files, plan = k["p"], k["arts"], k["by_id"], k["acc"], k["applied"], k["files"], k["plan"]
    stuck = {x["id"] for x in k["problems"]}
    bdir = p["_dir"] / "before-apply"; bdir.mkdir(exist_ok=True)
    for f, s_ in files.items():
        if s_ != (arts / f).read_text(encoding="utf-8", newline=""):
            shutil.copy2(arts / f, bdir / f)
            (arts / f).write_text(s_, encoding="utf-8", newline="")
    done_ids = {c["id"] for c in acc} - stuck
    applied["ids"] = sorted(set(applied["ids"]) | done_ids)
    applied["moves"] = sorted(set(applied["moves"]) | {i for _, _, _, i in plan})
    applied["log"].append({"at": datetime.now().isoformat(timespec="seconds"), "accepted": sorted(done_ids),
                           "destination_writes": [i for _, _, _, i in plan], "stuck": sorted(stuck),
                           "lost_maps_links": k["lost"]})
    decisions = merge_decisions(slug, {})
    done = all((cid in applied["ids"]) or (decisions.get(cid) is False) for cid in by_id)
    applied["complete"] = done
    with _store_lock, _flock(p["_dir"] / "progress.json"):   # written whole: a kill mid-write left progress.json at 0 bytes
        _write_json(p["_dir"] / ("applied.json" if done else "progress.json"), applied)
    if done and (p["_dir"] / "progress.json").exists():
        (p["_dir"] / "progress.json").unlink()
    problems = [f'{x["id"]}: {x["why"]}' for x in k["problems"]]
    warnings = ["these Maps links are now on no page: " + ", ".join(u[:80] for u in k["lost"])] if k["lost"] else []
    return {"ok": not problems, "applied": len(done_ids), "destination_writes": len(plan), "complete": done,
            "problems": problems, "warnings": warnings}


def decided_changes(p, only=None):
    """a round's grammar changes that are decided (and, of an open round, saved): the margin lists
    them under Decided so a decision can be reversed after its card has gone. Kevin, 2026-09-30:
    the autosave committed #15 and closed the round, and nothing could bring the card back."""
    out = []
    for i, c in enumerate(p["changes"]):
        v = p["decisions"].get(c["id"])
        if c["kind"] != "grammar" or v is None or (only is not None and c["id"] not in only):
            continue
        out.append({"round": p["slug"], "id": c["id"], "n": i + 1, "section": c.get("section", ""),
                    "find": c["find"], "replace": c["replace"], "accepted": bool(v)})
    return out


def last_closed(rel):
    """slug of the newest round for this article that is closed (applied.json), else None"""
    rel = re.sub(r"^(?:\./|/)+", "", rel.replace("\\", "/"))   # not lstrip("./"): it ate the dot of ".tmp"
    best = None
    for d in STORE.glob("*/applied.json"):
        pj = d.parent / "proposal.json"
        if not pj.exists():
            continue
        p = json.loads(pj.read_text(encoding="utf-8"))
        if p["article_dir"] + "/" + p["article"] == rel and (best is None or d.stat().st_mtime > best[0]):
            best = (d.stat().st_mtime, p["slug"])
    return best[1] if best else None


def review_state(rel):
    """everything the editor needs when an article opens"""
    slug = slug_for(rel)
    out = {"slug": slug, "comments_key": comments_key(rel), "changes": [], "decisions": {},
           "comments": comments_load(comments_key(rel)), "decided": []}
    closed = last_closed(rel)
    if closed:
        out["decided"] += decided_changes(load(closed))
    if slug:
        p = load(slug)
        prog = p["_dir"] / "progress.json"
        applied_ids = set(json.loads(prog.read_text(encoding="utf-8"))["ids"]) if prog.exists() else set()
        out["decided"] += decided_changes(p, applied_ids)
        out["changes"] = [c for c in p["changes"] if c["id"] not in applied_ids]
        out["decisions"] = p["decisions"]
        out["title"] = p.get("title", slug)
        out["created"] = datetime.fromtimestamp((p["_dir"] / "proposal.json").stat().st_mtime).isoformat(timespec="seconds")
        out["words_before"], out["words_target"], out["target_label"] = p.get("words_before", 0), p.get("words_target", 0), p.get("target_label", "")
    return out


# ============================================================== CLI
if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__); raise SystemExit(0)
    cmd, rest = args[0], args[1:]
    if cmd == "open":                 # the editor, comments and changes ready to anchor
        print(open_tab(rest[0]))
    elif cmd == "list":
        for p in pending():
            print(f"  {p['slug']:<30} {p['changes']:>4} changes  {'APPLIED' if p['applied'] else 'pending'}   {p['title']}")
    elif cmd in ("tasks", "rewrites"):    # what Kevin sent to Claude from the margin, still unanswered
        for line in task_lines():
            print(line)
    elif cmd == "apply":
        dry = "--dry-run" in rest
        r = apply(rest[0], dry=dry)
        print(json.dumps(r, indent=1))
    else:
        print("commands: open <slug> | list | tasks | apply <slug> [--dry-run]")
