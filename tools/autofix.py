#!/usr/bin/env python3
"""Apply the audit findings that have exactly one right answer. Leave the rest to Kevin.

tools/audit.py reports. This applies the subset of what it reports where the fix is not a
judgement call, so the routine mail stops listing the same mechanical things every week.

The split is the whole point of this tool:

  APPLIED                                       HELD BACK, and why
  ----------------------------------            --------------------------------------------
  spelling  British -> American                 em-dash    choosing a comma, a period or a
            (americanize's proper-name guard               rewrite is a voice decision, and
            protects Viaduct Harbour)                      voice goes through the review
  spacing   two spaces, a space before a                   margin, not a script
            comma, a missing space after one    notes      a leftover [ ] may be a real todo
  paste     Google-Docs inline styles that      seo        a title and a description are copy
            fight the page CSS                  heroes     which photo leads a page is Kevin's
  maps      a Maps link pointing at a search    repeat-word  "had had" is sometimes right, and
            instead of a place                             deleting a word is destructive
                                                tiers      correct but heavy: it rebuilds
                                                           binaries. Opt in with --checks.

Three rules keep "I can always undo them" true:

  1. It refuses to run on a dirty tree, so a commit holds the fixer's work and nothing else.
  2. Each class is its own commit, so `git revert <sha>` undoes the spellings without
     touching the spacing.
  3. --dry-run never runs a fixer that cannot preview. Two of these tools write as soon as
     they start, so for those it says so and skips rather than "previewing" by doing it.

Published pages only. Drafts are unfinished writing and go through the review margin.

    python tools/autofix.py --dry-run             # what would change
    python tools/autofix.py                       # apply, one commit per class
    python tools/autofix.py --checks spelling     # just one
    python tools/autofix.py --push                # and push (off by default)
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each class says how to APPLY and how to PREVIEW. preview=None means the tool has no
# read-only mode, so --dry-run reports that and does NOT run it.
CLASSES = {
    # Every apply/preview names the published pages explicitly. americanize walked ONLY
    # Drafts/ and strip_paste_artifacts walked drafts as well as live pages, so the first
    # version of this tool would have rewritten unfinished writing while promising not to,
    # and because Drafts/ is its own repo none of it would have reached the commit.
    "spelling": {
        "apply": ["tools/americanize.py", "--live"],
        "preview": ["tools/americanize.py", "--live", "--dry-run"],
        "subject": "Use the American spelling",
        "body": "americanize.py, which skips anything inside a tag and treats a capitalised\n"
                "British word next to another capital as part of a name, so Viaduct Harbour\n"
                "and Centre Pompidou are left alone.",
    },
    "spacing": {
        "apply": ["tools/lint_prose.py", "--fix"],
        "preview": ["tools/lint_prose.py"],          # without --fix it only reports
        "subject": "Close up the spacing the style rules ask for",
        "body": "lint_prose.py --fix, which only applies the rules that carry a replacement:\n"
                "double spaces, a space before a comma, a missing space after one. Repeated\n"
                "words and a space before a period are left alone, because both are usually a\n"
                "missing word rather than a stray space.",
    },
    "paste": {
        "apply": ["tools/strip_paste_artifacts.py", "--live"],
        "preview": None,
        "subject": "Strip the Google-Docs styles off the prose",
        "body": "strip_paste_artifacts.py. These arrive with every Google-Docs paste and fight\n"
                "the page CSS.",
    },
    "maps": {
        "apply": ["tools/resolve_draft_maps.py", "--live"],
        "preview": ["tools/resolve_draft_maps.py", "--live", "--list"],
        "subject": "Point the Maps links at the place, not a search",
        "body": "resolve_draft_maps.py, which refuses a name it cannot match rather than\n"
                "guessing. Those stay in the report for a person to look at.",
    },
    "tiers": {
        "apply": ["tools/recompress_desktop.py"],
        "preview": None,
        "subject": "Rebuild the image tiers the pages promise",
        "body": "recompress_desktop.py, rebuilding the variants under Images/web/ from the\n"
                "originals. The originals under Images/ are never touched.",
    },
    # Reads the cached scan in .tmp/link_scan.json and never touches the network itself,
    # because this runs as the editor opens and 400 HTTP requests is not a thing to put in
    # front of a launch. `python tools/fix_links.py --scan` refreshes the cache.
    "links": {
        "apply": ["tools/fix_links.py"],
        "preview": ["tools/fix_links.py", "--dry-run"],
        "subject": "Repoint the links whose destination moved",
        "body": "fix_links.py, which repoints a link ONLY when the destination proves it is\n"
                "the same page: the address differs by a scheme, a www or a trailing slash,\n"
                "or the original's slug or numeric id survives in the target. A tour that now\n"
                "lands the reader on the city page, a dead 404 and a redirect to another\n"
                "domain are all left alone and reported instead.",
    },
}
DEFAULT = ["spelling", "spacing", "paste", "maps", "links"]


def out(s=""):
    """Write our own bytes: a redirected stdout on Windows is cp1252 and a child's output
    can carry anything, which killed this tool the first time it printed a fixer's log."""
    sys.stdout.buffer.write((s + "\n").encode("utf-8", "replace"))
    sys.stdout.buffer.flush()


def git(*a, check=True):
    # quotepath=false, or a path with an accent in it comes back as escaped octal
    r = subprocess.run(["git", "-c", "core.quotepath=false"] + list(a), cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise SystemExit("git %s failed:\n%s" % (" ".join(a), r.stderr.strip()))
    return r.stdout.strip()


def tracked_changes():
    """Tracked files that differ from HEAD, staged or not. Whole paths, never a slice of
    --porcelain: its status column is two chars wide but not always two characters, and
    guessing an offset silently truncated the first letter of every path."""
    return [l for l in git("diff", "--name-only", "HEAD").splitlines() if l.strip()]


def untracked():
    """Files git is not tracking. A fixer that WRITES a new file (a missing WebP) lands
    here, so the difference before and after is the fixer's; whatever was already lying
    around untracked is not, and must not be swept into its commit."""
    return {l for l in git("ls-files", "--others", "--exclude-standard").splitlines() if l.strip()}


def run(argv):
    return subprocess.run([sys.executable] + argv, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks", default=",".join(DEFAULT),
                    help="comma-separated: %s" % ",".join(CLASSES))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true", help="push when everything committed cleanly")
    ap.add_argument("--json", help="write a summary here, for the routine email")
    a = ap.parse_args()

    want = [c.strip() for c in a.checks.split(",") if c.strip() in CLASSES]
    if not want:
        raise SystemExit("nothing to do: --checks must name some of %s" % ",".join(CLASSES))

    if not a.dry_run:
        d = tracked_changes()
        if d:
            out("Refusing to run: %d tracked file(s) already modified." % len(d))
            out("A commit here would carry your work as well as the fixer's. Commit or stash")
            out("first, then run again.")
            for f in d[:10]:
                out("    " + f)
            return 2

    summary = {"applied": [], "skipped": [], "dry_run": a.dry_run}
    for name in want:
        c = CLASSES[name]
        argv = c["preview"] if a.dry_run else c["apply"]
        if a.dry_run and argv is None:
            out("\n=== %s: no read-only mode, skipped" % name)
            out("    %s writes as soon as it runs, so a dry run cannot preview it." % c["apply"][0])
            summary["skipped"].append({"class": name, "why": "no read-only mode to preview with"})
            continue

        out("\n=== %s: %s" % (name, " ".join(argv)))
        before_new = set() if a.dry_run else untracked()
        r = run(list(argv))
        # The last handful of lines: every fixer ends with its own tally, which is the
        # part worth showing. Say so when there was more, though -- a silently clipped
        # list of pages reads as the whole list, and fix_links names one page per line.
        _lines = (r.stdout or "").strip().splitlines()
        if len(_lines) > 6:
            out("    (%d earlier line(s) not shown)" % (len(_lines) - 6))
        for l in _lines[-6:]:
            out("    " + l)
        if r.returncode:
            err = (r.stderr or "").strip().splitlines()
            out("    FAILED (exit %d)%s" % (r.returncode, (": " + err[-1]) if err else ""))
            summary["skipped"].append({"class": name, "why": "the fixer exited %d" % r.returncode,
                                       "detail": (err[-1] if err else "")[:200]})
            # A fixer that cannot run must not take the rest down with it: a cloud runner
            # with no browser can still fix every spelling on the site.
            continue

        if a.dry_run:
            summary["applied"].append({"class": name, "files": [], "note": "dry run"})
            continue

        changed = tracked_changes() + sorted(untracked() - before_new)
        if not changed:
            out("    nothing to change")
            summary["skipped"].append({"class": name, "why": "nothing to change"})
            continue
        git("add", "-A", "--", *changed)
        msg = ("%s\n\n%s\n\nApplied by tools/autofix.py, which only applies the findings that have one\n"
               "right answer. Revert this commit alone to undo just this class.\n"
               % (c["subject"], c["body"]))
        subprocess.run(["git", "commit", "-q", "-F", "-"], cwd=ROOT,
                       input=msg.encode("utf-8"), check=True)
        sha = git("rev-parse", "--short", "HEAD")
        out("    %s  %d file(s)" % (sha, len(changed)))
        summary["applied"].append({"class": name, "commit": sha, "files": changed})

    summary["pushed"] = False
    if a.push and not a.dry_run and summary["applied"]:
        out("\n=== pushing")
        git("push", "origin", "HEAD")
        out("    pushed")
        summary["pushed"] = True

    if a.json:
        (ROOT / a.json).write_text(json.dumps(summary, indent=1), encoding="utf-8")
        out("\nwrote " + a.json)

    n = sum(len(x.get("files", [])) for x in summary["applied"])
    out("\n%s %d class(es), %d file(s)" % ("would fix" if a.dry_run else "fixed",
                                           len(summary["applied"]), n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
