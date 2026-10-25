#!/usr/bin/env python3
"""Bring a draft's contents box in line with its headings after a rewrite round (2026-10-24: every accepted rewrite
removed or added sections, and the boxes are hand-written lists the rounds may not touch).

Two shapes of box:
  - a city guide's box, built by a <script> from a template literal of `<div class="toc-row">` rows (toc-num,
    toc-link): rows whose #anchor no longer exists are dropped, h2s with an id that the box lacks are added in page
    order, and the numbers run 01, 02, ... again. Existing labels are kept (Kevin may have worded them).
  - the itinerary template's `.toc-box` (one per layout variant, rows `<a class="r" href="#id"><span class="n">NN</span>
    <span class="t">Title</span></a>`): the same, in every variant; a new section's row goes where its heading sits.

    python tools/toc_sync.py "<draft>" ... [--dry-run]
"""
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _text(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h)).replace("&amp;", "&").strip()


def heads(s):
    """[(id, title)] of the page's sections in order: article h2s with an id, or the itinerary template's sections
    (`<div class="measure" id="...">` holding a `.v-head` whose `.h-a` is the title)"""
    a = s.find('class="article-body"')
    a = a if a >= 0 else s.find('class="artbody"')
    e = s.find('<section class="xm')
    body = s[a:e if e > a else len(s)]
    out = [(i, _text(t)) for i, t in re.findall(r'<h2\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</h2>', body, re.S)]
    if not out:
        out = [(i, _text(t)) for i, t in re.findall(
            r'<div class="measure" id="([^"]+)">\s*<div class="v v-head"[^>]*>\s*<div class="serif h-a">(.*?)</div>', body, re.S)]
    return out


def sync_script(s, ids, hs, log):
    """a city guide's box keeps its own structure (two columns, collapsible sub-lists): rows and sub-links whose target
    is gone are dropped and the numbers run again; a section the box lacks is reported, not invented"""
    m = re.search(r"toc\.innerHTML\s*=\s*`(.*?)`;", s, re.S)
    if not m:
        return sync_concat(s, ids, hs, log)
    lit = m.group(1)
    dead = []

    def drop_row(r):
        a = re.search(r'href="#([\w-]+)"', r.group(0))
        if a and a.group(1) not in ids and 'toc-section-head' not in r.group(0):
            dead.append(a.group(1))
            return ""
        return r.group(0)
    lit2 = re.sub(r'\n?[ \t]*<div class="toc-row">\s*<span class="toc-num">\d+</span>\s*<a href="#[\w-]+" class="toc-link">.*?</a>\s*</div>', drop_row, lit, flags=re.S)

    def drop_sub(a):
        if a.group(1) not in ids:
            dead.append(a.group(1))
            return ""
        return a.group(0)
    lit2 = re.sub(r'\n?[ \t]*<a href="#([\w-]+)">[^<]*</a>', drop_sub, lit2)
    n = [0]

    def num(x):
        n[0] += 1
        return '<span class="toc-num">%02d</span>' % n[0]
    lit2 = re.sub(r'<span class="toc-num">\d+</span>', num, lit2)
    listed = set(re.findall(r'href="#([\w-]+)"', lit2))
    missing = [(i, t) for i, t in hs if i not in listed]
    if dead:
        log.append("  - " + ", ".join(dead))
    if missing:
        log.append("  ! not in the box (add by hand if wanted): " + ", ".join("%s (%s)" % x for x in missing))
    return s[:m.start(1)] + lit2 + s[m.end(1):]


def sync_concat(s, ids, hs, log):
    """the older box: a string built line by line, `"<div class=\\"toc-row\\">...<a href=\\"#id\\" ...>Label</a></div>" +`"""
    line = re.compile(r'\n[ \t]*"<div class=\\"toc-row\\"><span class=\\"toc-num\\">\d+</span><a href=\\"#([\w-]+)\\"[^\n]*?</div>" \+')
    if not line.search(s):
        return s
    dead = []

    def drop(m):
        if m.group(1) not in ids:
            dead.append(m.group(1))
            return ""
        return m.group(0)
    t = line.sub(drop, s)
    n = [0]

    def num(x):
        n[0] += 1
        return '<span class=\\"toc-num\\">%02d</span>' % n[0]
    t = re.sub(r'<span class=\\"toc-num\\">\d+</span>', num, t)
    listed = set(re.findall(r'href=\\"#([\w-]+)\\"', t))
    missing = [(i, x) for i, x in hs if i not in listed]
    if dead:
        log.append("  - " + ", ".join(dead))
    if missing:
        log.append("  ! not in the box (add by hand if wanted): " + ", ".join("%s (%s)" % x for x in missing))
    return t


ROW = re.compile(r'<a class="r" href="#([\w-]+)"><span class="(?:serif )?n">\d+</span><span class="t">(.*?)</span></a>')   # variant d writes "serif n"


def sync_boxes(s, ids, hs, log):
    """the box keeps its own order; a section it lacks goes in right after the section it follows on the page.
    A box drawn in columns is one list: runs whose numbers continue (the next column starts at last + 1) are fixed
    together and dealt back into the same columns (2026-10-25: each column of the new itineraries got every heading)."""
    first = [True]
    seq = [i for i, _ in hs]

    def fixed(rows):
        have = {i for i, _ in rows}
        kept = [(i, t) for i, t in rows if i in ids]
        add = [(i, t) for i, t in hs if i not in have]
        if first[0]:
            if add: log.append("  + " + ", ".join("%s (%s)" % a for a in add))
            gone = [i for i, _ in rows if i not in ids]
            if gone: log.append("  - " + ", ".join(gone))
            first[0] = False
        for i, t in add:
            k = seq.index(i)
            before = [x for x in seq[:k] if any(r[0] == x for r in kept)]
            after = [x for x in seq[k + 1:] if any(r[0] == x for r in kept)]
            if before:
                at = next(n for n, r in enumerate(kept) if r[0] == before[-1]) + 1
            elif after:
                at = next(n for n, r in enumerate(kept) if r[0] == after[0])
            else:
                at = len(kept)
            kept.insert(at, (i, t))
        return kept

    # every run of rows, whatever wraps it: the template shows the box in several layout variants, and only one of
    # them sits in a .toc-box (the Food row reached one of five, 2026-10-24)
    run = re.compile(r'(?:<a class="r" href="#[\w-]+"><span class="(?:serif )?n">\d+</span><span class="t">.*?</span></a>\s*)+', re.S)
    num = re.compile(r'<span class="(?:serif )?n">(\d+)</span>')
    runs = list(run.finditer(s))
    groups = []
    for m in runs:
        ns = [int(x) for x in num.findall(m.group(0))]
        if groups and ns and ns[0] != 1 and ns[0] == groups[-1][-1][1] + 1:
            groups[-1].append((m, ns[-1]))
        else:
            groups.append([(m, ns[-1] if ns else 0)])
    out, pos = [], 0
    for g in groups:
        parts = [m.group(0) for m, _ in g]
        rows = [r for part in parts for r in ROW.findall(part)]
        if not rows:
            continue
        new = fixed(rows)
        if new == rows:
            continue                                 # already right: leave it exactly as written
        sizes = [len(ROW.findall(part)) for part in parts]
        sizes[-1] += len(new) - len(rows)
        if sizes[-1] < 0:                            # more rows died than the last column held: refill front to back
            sizes = [len(new) // len(parts) + (1 if n < len(new) % len(parts) else 0) for n in range(len(parts))]
        k = 0
        for (m, _), part, size in zip(g, parts, sizes):
            tail = part[len(part.rstrip()):]
            ncls = "serif n" if '<span class="serif n">' in part else "n"      # keep the variant's own number style
            body = "".join('<a class="r" href="#%s"><span class="%s">%02d</span><span class="t">%s</span></a>' % (i, ncls, k + n + 1, t)
                           for n, (i, t) in enumerate(new[k:k + size]))
            k += size
            out.append(s[pos:m.start()]); out.append(body + tail); pos = m.end()
    out.append(s[pos:])
    return "".join(out)


def add_simple(s, ids, hs, log):
    """--add, for the simple two-column box (one line per row, no sub-lists): every section the box lacks goes in, in page
    order, the rows are split evenly over the columns again and renumbered; existing labels stay"""
    m = re.search(r"toc\.innerHTML\s*=\s*`(.*?)`;", s, re.S)
    if not m or "toc-section-head" in m.group(1) or "toc-sub" in m.group(1):
        return s
    lit = m.group(1)
    cols = re.search(r'(<div class="toc-cols">)(.*)(</div>\s*)$', lit, re.S)
    rows = re.findall(r'<div class="toc-row"><span class="toc-num">\d+</span><a href="#([\w-]+)" class="toc-link">(.*?)</a></div>', lit)
    if not cols or not rows:
        return s
    have = {i for i, _ in rows}
    allr = [(i, t) for i, t in rows if i in ids] + [(i, t) for i, t in hs if i not in have]
    seen, uniq = set(), []
    for i, t in allr:
        if i not in seen:
            seen.add(i); uniq.append((i, t))
    uniq.sort(key=lambda r: s.find('id="%s"' % r[0]))
    added = [i for i, _ in uniq if i not in have]
    if not added:
        return s
    log.append("  + " + ", ".join(added))
    half = (len(uniq) + 1) // 2
    ind = (re.search(r"\n([ \t]*)<div class=\"toc-row\">", lit) or [None, "        "])[1]
    cind = (re.search(r"\n([ \t]*)<div class=\"toc-col\">", lit) or [None, "      "])[1]
    def col(part, start):
        return (cind + '<div class="toc-col">\n' + "".join(ind + '<div class="toc-row"><span class="toc-num">%02d</span><a href="#%s" class="toc-link">%s</a></div>\n'
                % (start + n, i, t) for n, (i, t) in enumerate(part)) + cind + "</div>\n")
    inner = "\n" + col(uniq[:half], 1) + col(uniq[half:], half + 1) + cind[:-2] if len(cind) >= 2 else ""
    new_lit = lit[:cols.start(2)] + inner + lit[cols.end(2):]
    return s[:m.start(1)] + new_lit + s[m.end(1):]


def sync(rel, dry=False, add=False):
    p = ROOT / rel
    s = io.open(p, encoding="utf-8", newline="").read()
    ids = set(re.findall(r'\bid="([^"]+)"', s))
    hs = heads(s)
    log = []
    t = sync_script(s, ids, hs, log)
    if add:
        t = add_simple(t, ids, hs, log)
    if 'class="r" href="#' in t:
        t = sync_boxes(t, ids, hs, log)
    if t != s and not dry:
        io.open(p, "w", encoding="utf-8", newline="").write(t)
    return ["%s: %s" % (rel, "contents box updated" if t != s else "contents box already matches")] + log


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    dry = "--dry-run" in sys.argv
    add = "--add" in sys.argv
    for rel in [a for a in sys.argv[1:] if not a.startswith("--")]:
        print("\n".join(sync(rel.replace("\\", "/"), dry, add)))
