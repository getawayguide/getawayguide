#!/usr/bin/env python3
"""Add `loading` and intrinsic width/height to <img> tags that lack them.

Missing width/height is the main cause of layout shift: the browser can't reserve space,
so text jumps as each photo arrives. Missing `loading` means offscreen images compete with
the ones actually on screen.

The FIRST image on a page is deliberately left eager (and marked fetchpriority=high) -
lazy-loading the thing above the fold delays the largest contentful paint rather than
helping it.

  py tools/fix_img_perf.py --dry-run
  py tools/fix_img_perf.py
"""
import glob, os, re, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRY = "--dry-run" in sys.argv
IMG = re.compile(r"<img\b[^>]*>")


def dims(page, tag):
    m = re.search(r'src="([^"]+)"', tag)
    if not m:
        return None
    u = m.group(1).split("#")[0].split("?")[0]
    if u.startswith(("http", "data:")):
        return None
    p = os.path.normpath(os.path.join(os.path.dirname(page),
                                      urllib.parse.unquote(u))).replace("\\", "/")
    if not os.path.exists(p):
        return None
    try:
        from PIL import Image
        with Image.open(p) as im:
            return im.size
    except Exception:
        return None


def main():
    pages = sorted(glob.glob(os.path.join(ROOT, "**", "*.html"), recursive=True))
    n_load = n_dim = n_eager = 0
    touched = 0
    for p in pages:
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.startswith((".tmp/", ".git/", "Drafts/.")) or rel == "editor.html":
            continue
        s = open(p, encoding="utf-8").read()
        orig = s
        idx = [0]

        def fix(m):
            tag = m.group(0)
            first = idx[0] == 0
            idx[0] += 1
            add = ""
            if "loading=" not in tag:
                if first:
                    # above the fold: eager, and tell the browser it's the LCP candidate
                    if "fetchpriority=" not in tag:
                        add += ' fetchpriority="high"'
                    n_e[0] += 1
                else:
                    add += ' loading="lazy"'
                    n_l[0] += 1
            if "width=" not in tag and "height=" not in tag:
                wh = dims(p, tag)
                if wh:
                    add += ' width="%d" height="%d"' % wh
                    n_d[0] += 1
            if not add:
                return tag
            return tag[:-1].rstrip() + add + ">"

        n_l, n_d, n_e = [0], [0], [0]
        s = IMG.sub(fix, s)
        n_load += n_l[0]; n_dim += n_d[0]; n_eager += n_e[0]
        if s != orig:
            touched += 1
            if not DRY:
                open(p, "w", encoding="utf-8", newline="").write(s)
    print('%sadded loading="lazy" x%d, width/height x%d, fetchpriority="high" x%d '
          "across %d page(s)" % ("[dry-run] " if DRY else "", n_load, n_dim, n_eager, touched))


if __name__ == "__main__":
    main()
