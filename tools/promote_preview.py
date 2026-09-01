"""Ship the re-themed preview: archive the current site, then promote preview/.

Two moves, both reversible:

1. ARCHIVE -- every live page is copied to archive/ with its asset URLs rewritten
   for the extra directory level, so the OLD site stays browsable at
   archive/index.html and can be compared page for page against the new one. The
   archive links only styles.css, never theme.css/artifact.css, so it keeps the
   old design even though it now sits in the same repo.

2. PROMOTE -- preview/ becomes the site. Asset URLs are re-resolved rather than
   string-replaced: a URL is only rewritten when it actually points OUTSIDE
   preview/ (../Images, ../styles.css). Anything inside preview/ -- theme.css,
   artifact.css, assets/ -- moves with the page and keeps the URL it already has.
   Links between pages never change, because both trees have the same shape.

    python tools/promote_preview.py --dry-run
    python tools/promote_preview.py
"""
import argparse
import pathlib
import re
import shutil

SITE = pathlib.Path(__file__).resolve().parent.parent
PREVIEW = SITE / "preview"
ARCHIVE = SITE / "archive"

SKIP_DIRS = {"preview", "archive", ".git", ".tmp", "Drafts", "tools",
             "node_modules", "Images", "__pycache__"}
SKIP_FILES = {"editor.html"}

ASSET_ATTR = re.compile(
    r'\b(src|href|srcset|content|data-src|poster)\s*=\s*"([^"]*)"', re.I)
ASSET_EXT = re.compile(
    r'\.(?:css|js|png|jpe?g|webp|gif|svg|avif|ico|mp4|webm|woff2?|json|xml|txt)'
    r'(?:\?|#|$)', re.I)
CSS_URL = re.compile(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)")
EXTERNAL = ("http://", "https://", "//", "#", "data:", "mailto:", "tel:", "/")


def is_asset(attr, val):
    if not val or val.startswith(EXTERNAL):
        return False
    if attr.lower() in ("src", "srcset", "data-src", "poster"):
        return True
    return bool(ASSET_EXT.search(val))


def rewrite(html, remap):
    """Apply `remap(url) -> url` to every relative asset URL in the document."""
    def one(m):
        attr, val = m.group(1), m.group(2)
        if attr.lower() == "srcset":
            out = []
            for chunk in val.split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                bits = chunk.split()
                if not bits[0].startswith(EXTERNAL):
                    bits[0] = remap(bits[0])
                out.append(" ".join(bits))
            return f'{attr}="{", ".join(out)}"'
        if attr.lower() == "content" and not ASSET_EXT.search(val):
            return m.group(0)
        if not is_asset(attr, val):
            return m.group(0)
        return f'{attr}="{remap(val)}"'

    html = ASSET_ATTR.sub(one, html)

    def css_one(m):
        q, u = m.group(1), m.group(2)
        if u.startswith(EXTERNAL):
            return m.group(0)
        return f"url({q}{remap(u)}{q})"
    return CSS_URL.sub(css_one, html)


def split_frag(url):
    m = re.match(r"([^?#]*)([?#].*)?$", url)
    return m.group(1), (m.group(2) or "")


def relocate(url, from_dir, to_dir, root):
    """Re-resolve a relative URL when its page moves from one dir to another."""
    path, frag = split_frag(url)
    if not path:
        return url
    target = (from_dir / path).resolve()
    try:
        rel = pathlib.Path(target).relative_to(root.resolve())
    except ValueError:
        return url                     # escapes the tree entirely; leave it
    new = pathlib.Path(*([".."] * len(to_dir.relative_to(root).parts))) / rel \
        if to_dir != root else rel
    import os
    new = os.path.relpath(target, to_dir).replace("\\", "/")
    return new + frag


def live_pages():
    for p in sorted(SITE.rglob("*.html")):
        rel = p.relative_to(SITE)
        if rel.parts[0] in SKIP_DIRS or p.name in SKIP_FILES:
            continue
        yield p, rel


def do_archive(dry):
    n = 0
    for src, rel in live_pages():
        html = src.read_text(encoding="utf-8")
        from_dir = src.parent
        to_dir = ARCHIVE / rel.parent
        html = rewrite(html, lambda u: relocate(u, from_dir, to_dir, SITE))
        if not dry:
            dst = ARCHIVE / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(html, encoding="utf-8")
        n += 1
    return n


def do_promote(dry):
    n = 0
    for src in sorted(PREVIEW.rglob("*.html")):
        rel = src.relative_to(PREVIEW)
        html = src.read_text(encoding="utf-8")
        from_dir = src.parent
        to_dir = SITE / rel.parent

        def remap(u, _f=from_dir, _t=to_dir):
            path, frag = split_frag(u)
            if not path:
                return u
            target = (_f / path).resolve()
            try:                               # inside preview/: moves with us
                target.relative_to(PREVIEW.resolve())
                inside = True
            except ValueError:
                inside = False
            if inside:
                newtarget = SITE / pathlib.Path(target).relative_to(
                    PREVIEW.resolve())
            else:
                newtarget = target
            import os
            return os.path.relpath(newtarget, _t).replace("\\", "/") + frag

        html = rewrite(html, remap)
        if not dry:
            dst = SITE / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(html, encoding="utf-8")
        n += 1
    # the theme's own files come across too
    for extra in ("theme.css", "artifact.css", "fonts.css"):
        s = PREVIEW / extra
        if s.exists() and not dry:
            shutil.copyfile(s, SITE / extra)
    a = PREVIEW / "assets"
    if a.is_dir() and not dry:
        shutil.copytree(a, SITE / "assets", dirs_exist_ok=True)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-archive", action="store_true")
    a = ap.parse_args()

    if not a.skip_archive:
        n = do_archive(a.dry_run)
        print(f"{'would archive' if a.dry_run else 'archived'} {n} pages "
              f"-> archive/")
    n = do_promote(a.dry_run)
    print(f"{'would promote' if a.dry_run else 'promoted'} {n} pages "
          f"from preview/ -> site root")


if __name__ == "__main__":
    main()
