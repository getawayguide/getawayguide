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
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / ".tmp" / "redline"
SERVER = "http://127.0.0.1:5003"
MAPS = re.compile(r'href="(https://www\.google\.com/maps/place/[^"]+)"')


# ============================================================== store
def load(slug):
    d = STORE / slug
    p = json.loads((d / "proposal.json").read_text(encoding="utf-8"))
    p["_dir"] = d
    dec = d / "decisions.json"
    p["decisions"] = json.loads(dec.read_text(encoding="utf-8")) if dec.exists() else {}
    app = d / "applied.json"
    p["applied"] = json.loads(app.read_text(encoding="utf-8")) if app.exists() else None
    # a round saved in more than one sitting keeps its running tally in progress.json;
    # commit() used to start from p["applied"] alone, so every save after the first
    # threw the earlier one's ids away and a round could only complete in one go
    prog = d / "progress.json"
    p["progress"] = json.loads(prog.read_text(encoding="utf-8")) if prog.exists() else None
    return p


def save_decisions(slug, decisions):
    d = STORE / slug
    (d / "decisions.json").write_text(json.dumps(decisions, indent=1), encoding="utf-8")


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
    rel = rel.replace("\\", "/").lstrip("./")
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
    parts = [x for x in rel.replace("\\", "/").split("/") if x and x != "Drafts" and not x.startswith(".")]
    return "__".join(re.sub(r"[^A-Za-z0-9-]+", "-", x.rsplit(".", 1)[0]) for x in parts[-2:])


def comments_load(key):
    f = COMMENTS / f"{key}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"threads": []}


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


def comments_save(key, data):
    COMMENTS.mkdir(parents=True, exist_ok=True)
    (COMMENTS / f"{key}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def merge_decisions(slug, partial):
    """decisions arrive one at a time from the editor; null means undecided again (an undo)"""
    d = STORE / slug
    cur = json.loads((d / "decisions.json").read_text(encoding="utf-8")) if (d / "decisions.json").exists() else {}
    for k, v in (partial or {}).items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = bool(v)
    (d / "decisions.json").write_text(json.dumps(cur, indent=1), encoding="utf-8")
    return cur


def commit(slug, accepted_ids):
    """After the editor saved the article with these changes accepted in its text: apply the
    destination side of any accepted MOVE (insert or replace in another file, with backups and
    the Maps-link guard), then record them as applied. Grammar fixes and the source side of every
    cut are already in the saved file, so nothing is written to the article itself."""
    p = load(slug)
    arts = ROOT / p["article_dir"]
    by_id = {c["id"]: c for c in p["changes"]}
    acc = [by_id[i] for i in accepted_ids if i in by_id]
    applied = p["applied"] or p["progress"] or {"ids": [], "moves": [], "log": []}
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
                problems.append(f'{c["id"]}: replace target appears {t.count(c["dest_replaces"])}x in {c["dest_file"]}'); continue
            plan.append((c["dest_file"], c["dest_replaces"], incoming, c["id"]))
        else:
            a = c.get("dest_after", "")
            if t.count(a) != 1:
                problems.append(f'{c["id"]}: anchor appears {t.count(a)}x in {c["dest_file"]}'); continue
            plan.append((c["dest_file"], a, a + " " + incoming, c["id"]))
    if problems:
        return {"ok": False, "problems": problems}
    for f, a, b, _ in plan:
        files[f] = files[f].replace(a, b, 1)
    # the article was just saved by the editor with its cut sentences gone: every Maps link the
    # baseline had must still exist somewhere across the saved article and the updated files
    base = (p["_dir"] / "baseline" / p["article"]).read_text(encoding="utf-8")
    have = set(MAPS.findall((arts / p["article"]).read_text(encoding="utf-8", newline="")))
    for f in p["files"]:
        if f != p["article"]:
            have |= set(MAPS.findall(files.get(f) or (arts / f).read_text(encoding="utf-8", newline="")))
    lost = sorted(set(MAPS.findall(base)) - have)
    if lost:
        return {"ok": False, "problems": ["these Maps links would disappear from the site: " + ", ".join(u[:80] for u in lost)]}
    bdir = p["_dir"] / "before-apply"; bdir.mkdir(exist_ok=True)
    for f, s in files.items():
        if s != (arts / f).read_text(encoding="utf-8", newline=""):
            shutil.copy2(arts / f, bdir / f)
            (arts / f).write_text(s, encoding="utf-8", newline="")
    applied["ids"] = sorted(set(applied["ids"]) | {c["id"] for c in acc})
    applied["moves"] = sorted(set(applied["moves"]) | {i for _, _, _, i in plan})
    applied["log"].append({"at": datetime.now().isoformat(timespec="seconds"), "accepted": [c["id"] for c in acc],
                           "destination_writes": [i for _, _, _, i in plan]})
    decisions = merge_decisions(slug, {})
    done = all((cid in applied["ids"]) or (decisions.get(cid) is False) for cid in by_id)
    applied["complete"] = done
    (p["_dir"] / ("applied.json" if done else "progress.json")).write_text(json.dumps(applied, indent=1), encoding="utf-8")
    if done and (p["_dir"] / "progress.json").exists():
        (p["_dir"] / "progress.json").unlink()
    return {"ok": True, "applied": len(acc), "destination_writes": len(plan), "complete": done}


def review_state(rel):
    """everything the editor needs when an article opens"""
    slug = slug_for(rel)
    out = {"slug": slug, "comments_key": comments_key(rel), "changes": [], "decisions": {},
           "comments": comments_load(comments_key(rel))}
    if slug:
        p = load(slug)
        prog = p["_dir"] / "progress.json"
        applied_ids = set(json.loads(prog.read_text(encoding="utf-8"))["ids"]) if prog.exists() else set()
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
