#!/usr/bin/env python3
"""Country folders hold live pages only (Kevin, 2026-10-24: "remove any files that are not online anymore like armenia
field notes, those belong in the archive folder. Add this to the launch process when I publish articles").

What does not belong in a live country folder, and where it goes:
  - a retired page. Its address stays alive as a 301 in `_redirects` (Netlify's redirect file at the site root), not as
    a stub file in the folder; the page itself goes to Drafts/.Archive/<country>/ (a redirect stub left by an older
    retirement is deleted once its rule is in `_redirects`: the real page was archived when it was retired).
  - anything that is not a page (an editor scratch file such as _content/todo.json, a *.bak / *.before-* copy, a
    stray .json): moved to Drafts/.Archive/<country>/, keeping its path below the country folder.

    python tools/site_archive.py --dry-run          # what it would move (the launch checklist shows this)
    python tools/site_archive.py                    # every country folder
    python tools/site_archive.py armenia            # one country
    python tools/site_archive.py --retire armenia/field-notes.html --to armenia/index.html   # retire one page

Run by tools/retire_field_notes.py (step 5, the field notes of a country whose guide launches) and by the launch
checklist's "Country folders" check (tools/prelaunch.py c_folders). archive/ (the pre-redesign site) is never touched.
"""
import argparse
import io
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "Drafts" / ".Archive"
REDIRECTS = ROOT / "_redirects"
NOT_COUNTRY = {"Drafts", "Images", "_content", "archive", "assets", "fonts", "tools", "workflows", "preview",
               "node_modules"}
STUB = re.compile(r'http-equiv="refresh"[^>]*content="\d+;\s*url=([^"]+)"', re.I)
HEADER = ("# Netlify redirects (docs.netlify.com/routing/redirects). A page that is no longer on the site keeps its\n"
          "# address alive here as a 301 instead of a stub file in its country folder; the page itself lives in\n"
          "# Drafts/.Archive. Written by tools/site_archive.py (Kevin, 2026-10-24).\n")


def country_dirs():
    out = []
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name in NOT_COUNTRY:
            continue
        if (d / "field-notes.html").is_file() or (d / "index.html").is_file():
            out.append(d)
    return out


def _read(p):
    try:
        return io.open(p, encoding="utf-8", newline="").read()
    except (OSError, UnicodeDecodeError):
        return ""


def stub_target(p):
    """the page a redirect stub sends to (repo-relative), or None when the file is a real page"""
    h = _read(p)[:4000]
    m = STUB.search(h)
    if not m:
        return None
    t = m.group(1).strip().split("#")[0]
    return (p.parent / t).resolve().relative_to(ROOT).as_posix() if not t.startswith("http") else None


def rules():
    if not REDIRECTS.is_file():
        return []
    out = []
    for ln in _read(REDIRECTS).splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            f = ln.split()
            if len(f) >= 2:
                out.append((f[0], f[1]))
    return out


def _url(rel):
    """the address a repo path is served at: armenia/index.html -> /armenia/"""
    rel = rel.replace("\\", "/")
    return "/" + (rel[:-len("index.html")] if rel.endswith("index.html") else rel)


def add_redirect(old_rel, new_rel, dry=False):
    """301 the old page (and its pretty URL, which Netlify also serves) to the new one. Returns the lines added."""
    have = {a for a, _ in rules()}
    old = _url(old_rel)
    want = [(old, _url(new_rel))]
    if old.endswith(".html"):
        want.append((old[:-5], _url(new_rel)))             # Netlify's pretty URL for the same page
    add = ["%-40s %-28s 301" % (a, b) for a, b in want if a not in have]
    if add and not dry:
        text = _read(REDIRECTS) if REDIRECTS.is_file() else HEADER
        if not text.endswith("\n"):
            text += "\n"
        io.open(REDIRECTS, "w", encoding="utf-8", newline="\n").write(text + "\n".join(add) + "\n")
    return add


def redirected(rel):
    """is this repo page's address already a 301 in _redirects?"""
    return _url(rel) in {a for a, _ in rules()}


def _archive_file(p, dry=False):
    """move a file from a country folder to Drafts/.Archive/<country>/<same path>; a copy already there is kept and
    this one is saved beside it with a -2, -3 suffix. Returns the archive path (repo-relative)."""
    rel = p.relative_to(ROOT)
    base = dst = ARCHIVE / rel
    n = 2
    while dst.exists():
        if dst.is_file() and dst.read_bytes() == p.read_bytes():
            if not dry:
                p.unlink()
            return dst.relative_to(ROOT).as_posix() + " (an identical copy was there already)"
        dst = base.with_name("%s-%d%s" % (base.stem, n, base.suffix))
        n += 1
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dst))
    return dst.relative_to(ROOT).as_posix()


def retire(rel, to_rel, dry=False):
    """retire one live page: archive it, 301 its address to `to_rel`. Returns log lines."""
    p = ROOT / rel
    log = ["%s -> 301 to %s" % (rel, _url(to_rel))] + ["  _redirects + " + a for a in add_redirect(rel, to_rel, dry)]
    if p.is_file():
        if stub_target(p):                                  # an old stub: the real page was archived when it retired
            if not dry:
                p.unlink()
            log.append("  stub deleted")
        else:
            log.append("  archived as " + _archive_file(p, dry))
    _prune(p.parent, dry)
    return log


def _prune(d, dry):
    """remove folders left empty by a move, up to (not including) the country folder's parent"""
    while not dry and d != ROOT and d.is_dir() and not any(d.iterdir()) and d.parent != ROOT:
        try:
            d.rmdir()
        except OSError:          # Windows refuses while another program (VS Code's watcher) holds the folder; git ignores it
            return
        d = d.parent


def plan(countries=None):
    """what does not belong: [(kind, rel, detail)] -- kind 'stub' (a retired page's redirect file) or 'file'"""
    out = []
    for d in country_dirs():
        if countries and d.name not in countries:
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT).as_posix()
            if p.suffix.lower() == ".html" and p.parent == d:
                t = stub_target(p)
                if t:
                    out.append(("stub", rel, t))
                continue
            out.append(("file", rel, "not a page"))
    return out


def sweep(countries=None, dry=False):
    log = []
    for kind, rel, detail in plan(countries):
        if kind == "stub":
            log += retire(rel, detail, dry)
        else:
            p = ROOT / rel
            log.append("%s -> %s" % (rel, _archive_file(p, dry)))
            _prune(p.parent, dry)
    return log or ["every country folder holds live pages only"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("countries", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retire", help="a live page to retire (repo path)")
    ap.add_argument("--to", help="where its address goes (repo path), with --retire")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.retire:
        if not a.to:
            raise SystemExit("--retire needs --to")
        log = retire(a.retire, a.to, a.dry_run)
    else:
        log = sweep(a.countries or None, a.dry_run)
    print("\n".join(("[dry-run] " if a.dry_run else "") + ln for ln in log))


if __name__ == "__main__":
    main()
