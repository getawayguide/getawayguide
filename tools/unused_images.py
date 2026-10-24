#!/usr/bin/env python3
"""Which files under Images/ nothing references (2026-10-19, Kevin: "clean up any unused files? double check to make
sure they are unused before deleting").

A file is USED when any text file in the repo names its path (every .html including Drafts/ and archive/, .css,
.js, .json, .md, .py, .txt, .xml, .svg; the .tmp picks files too), decoded and case-insensitively. An archive
original under Images/<Country>/... is also used when a tier cut from it is used (pages serve the tiers in
Images/web/, not the original), or when a web copy of the same stem is used. A tier is used only by its own path
(srcsets list every file). Spaces and parentheses are legal in these names ("Location Cards/", "IMG_0233(2)-2.jpg"),
so only quotes, tags and line ends stop a path, and the lazy match ends at the first extension.

    python tools/unused_images.py                 # report
    python tools/unused_images.py --delete        # remove them (prints each)
    python tools/unused_images.py --skip dest-cards --skip Index   # leave these folders alone
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "Images"
TEXT = {".html", ".css", ".js", ".json", ".md", ".py", ".txt", ".xml", ".svg", ".ps1", ".cmd", ".yml", ".yaml"}
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
PATH_RE = re.compile(r"Images/[^\"'<>\n;]+?\.(?:jpe?g|png|webp|avif|gif|svg|heic|ico)\b", re.I)
TIER_RE = re.compile(r"-(?:mob-)?[123]x$|-mob$|-\d{3,4}w$|-2x$", re.I)


def norm(p):
    return unquote(str(p)).replace("\\", "/").lower()


def refs():
    out = set()
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        if Path(dp).relative_to(ROOT).as_posix().startswith("Images"):
            continue
        for fn in fns:
            p = Path(dp) / fn
            if p.suffix.lower() not in TEXT or p.stat().st_size > 60_000_000:
                continue
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in PATH_RE.finditer(t):
                out.add(norm(m.group(0)))
    return out


def main():
    skip = [a.lower() for i, a in enumerate(sys.argv) if i and sys.argv[i - 1] == "--skip"]
    used = refs()
    used_stems = set()
    for u in used:
        if u.startswith("images/web/"):
            used_stems.add("images/" + re.sub(TIER_RE, "", u[len("images/web/"):].rsplit(".", 1)[0]))
        else:
            used_stems.add(u.rsplit(".", 1)[0])
    used_web_stems = {u.rsplit(".", 1)[0] for u in used}
    unused, kept, total = [], 0, 0
    for dp, dns, fns in os.walk(IMAGES):
        for fn in fns:
            p = Path(dp) / fn
            rel = norm(p.relative_to(ROOT).as_posix())
            total += 1
            stem = rel.rsplit(".", 1)[0]
            ok = rel in used
            if not ok and not rel.startswith("images/web/"):
                ok = stem in used_stems or ("images/web/" + stem[len("images/"):]) in used_web_stems
            if not ok and any(("images/" + s + "/") in rel or ("images/web/" + s + "/") in rel for s in skip):
                ok = True
            if ok:
                kept += 1
            else:
                unused.append((p, p.stat().st_size))
    unused.sort(key=lambda x: str(x[0]).lower())
    by_dir = {}
    for p, sz in unused:
        by_dir.setdefault(p.parent.relative_to(ROOT).as_posix(), []).append((p.name, sz))
    print("%d files under Images/, %d used, %d unused (%.1f MB)\n" % (total, kept, len(unused), sum(s for _, s in unused) / 1e6))
    for d, items in sorted(by_dir.items()):
        print("%s  (%d files, %.1f MB)" % (d, len(items), sum(s for _, s in items) / 1e6))
        for n, s in items:
            print("    %-60s %8.1f KB" % (n, s / 1024))
    if "--delete" in sys.argv:
        for p, _ in unused:
            p.unlink()
        print("\ndeleted %d files" % len(unused))
        for d in sorted({p.parent for p, _ in unused}, key=lambda x: -len(str(x))):
            try:
                d.rmdir()
                print("removed empty folder", d.relative_to(ROOT).as_posix())
            except OSError:
                pass


if __name__ == "__main__":
    main()
