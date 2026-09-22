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

# Page furniture that must never be mistaken for the LCP image: nav flags (.fl),
# the megamenu's continent icons and map thumb, logos.
CHROME = re.compile(r'class="[^"]*\b(fl|nav-[\w-]+|mm-[\w-]+|logo|icon)\b')


def is_hero_candidate(tag):
    """Could this <img> plausibly be the page's largest contentful paint?

    Anything declared narrower than 200px is a flag, icon or thumbnail, and
    anything wearing a nav/menu class is chrome. Both get lazy-loaded like the
    rest rather than being handed fetchpriority."""
    w = re.search(r'\bwidth="(\d+)"', tag)
    if w and int(w.group(1)) < 200:
        return False
    return not CHROME.search(tag)


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
        # "Drafts/." never matched anything - a stray dot meant every draft was
        # being rewritten despite the intent to skip them (396 tags across 11
        # pages), and drafts are kept isolated until they ship. archive/ is the
        # frozen previous design, noindex and robots-disallowed, so editing it
        # is churn in every future diff for no live benefit.
        if rel.startswith((".tmp/", ".git/", "Drafts/", "archive/")) or rel == "editor.html":
            continue
        s = open(p, encoding="utf-8").read()
        orig = s
        picked = [False]

        def fix(m):
            tag = m.group(0)
            add = ""
            if "loading=" not in tag:
                # THIS TOOL NO LONGER GUESSES THE LCP. "First <img> in the
                # document" is not the hero here - every page opens with a nav
                # whose dropdown holds 16x12 flags - and after teaching it to
                # skip chrome it still reached for a src-less lightbox
                # placeholder, mid-page city maps and an external hotlink. Every
                # real hero on this site already carries loading="eager" by
                # hand, set deliberately, so the honest move is to leave the
                # first plausible hero ALONE (untouched means browser-default
                # eager, which is right) and lazy-load everything after it.
                # fetchpriority stays a human decision.
                if not picked[0] and is_hero_candidate(tag):
                    picked[0] = True
                    n_e[0] += 1          # left eager on purpose, not modified
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
    print('%sadded loading="lazy" x%d, width/height x%d, left x%d hero(s) eager '
          "across %d page(s)" % ("[dry-run] " if DRY else "", n_load, n_dim, n_eager, touched))


if __name__ == "__main__":
    main()
