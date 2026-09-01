"""Normalize prose quotes and apostrophes to the straight forms.

The site is overwhelmingly straight already: 207 straight double quotes against
4 curly, and 1397 straight apostrophes against 54 curly. The curly ones arrive
by accident, mostly from text pasted out of Google Docs or Word, which
autocorrects as you type. The result is the same word set two ways on one page
-- el-salvador-itinerary quotes "murder capital of the world" once straight and
once curly, two paragraphs apart.

    python tools/normalize_quotes.py --dry-run
    python tools/normalize_quotes.py

Only PROSE is touched. Everything inside a tag is left exactly as it is, so
href, alt, style, onclick and embedded map code never change; so do <script>
and <style> blocks, where a swapped quote would be a syntax error. Named
entities are decoded to the straight character (&rsquo; -> ') rather than left
as a second spelling of the same glyph.
"""
import argparse
import collections
import pathlib
import re

SITE = pathlib.Path(__file__).resolve().parent.parent
# Drafts/ is deliberately isolated until a country is shipped, so it is not
# swept by default; pass --path Drafts/<country> to run it there.
SKIP = {"preview", ".tmp", "tools", "node_modules", "Images", ".git", "Drafts"}

# curly -> straight. The primes are included because phones produce them.
CHARS = {
    "‘": "'",   # left single
    "’": "'",   # right single / apostrophe
    "‚": "'",   # single low-9
    "‛": "'",   # single high-reversed-9
    "“": '"',   # left double
    "”": '"',   # right double
    "„": '"',   # double low-9
    "′": "'",   # prime
    "″": '"',   # double prime
}
ENTITIES = {
    "&rsquo;": "'", "&lsquo;": "'", "&sbquo;": "'",
    "&rdquo;": '"', "&ldquo;": '"', "&bdquo;": '"',
    "&apos;": "'", "&quot;": '"',
}

# a tag, or a whole script/style element -- these are skipped wholesale
PROTECTED = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>|<[^>]*>", re.S | re.I)


def convert(text, counter):
    for src, dst in ENTITIES.items():
        n = text.count(src)
        if n:
            counter[f"{src} -> {dst}"] += n
            text = text.replace(src, dst)
    for src, dst in CHARS.items():
        n = text.count(src)
        if n:
            counter[f"{src} -> {dst}"] += n
            text = text.replace(src, dst)
    return text


def process(html, counter):
    """Rebuild the document, converting only the text between tags."""
    out, last = [], 0
    for m in PROTECTED.finditer(html):
        out.append(convert(html[last:m.start()], counter))
        out.append(m.group(0))          # verbatim
        last = m.end()
    out.append(convert(html[last:], counter))
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--path", default="", help="limit to one file or folder")
    a = ap.parse_args()

    root = SITE / a.path if a.path else SITE
    files = [root] if root.is_file() else [
        f for f in sorted(root.rglob("*.html"))
        if not any(p in SKIP for p in f.relative_to(SITE).parts)]

    total = collections.Counter()
    changed = 0
    for f in files:
        src = f.read_text(encoding="utf-8")
        counter = collections.Counter()
        out = process(src, counter)
        if out == src:
            continue
        changed += 1
        total.update(counter)
        n = sum(counter.values())
        print(f"  {f.relative_to(SITE).as_posix()}: {n} "
              + ", ".join(f"{k} x{v}" for k, v in counter.most_common(4)))
        if not a.dry_run:
            f.write_text(out, encoding="utf-8")

    verb = "would change" if a.dry_run else "changed"
    print(f"\n{verb} {sum(total.values())} quotes across {changed} of "
          f"{len(files)} pages")
    for k, v in total.most_common():
        print(f"  {v:>5}  {k}")


if __name__ == "__main__":
    main()
