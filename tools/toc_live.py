"""Make a page's contents box build itself from the page's headings (Kevin, 2026-10-25, on Krushevo: "table of contents
is missing all of the new subsections that were added. This should be updated automatically").

A guide's box used to be a fixed list written into its <script>, so every section added later (a review round, a
heading typed in the editor) was missing until someone re-ran toc_sync. This swaps that list for the script the Gyumri
and Meteora guides already use: on load it reads every h2.article-h2[id] and the h3[id]s under each, and draws them in
two balanced columns with the collapsible sub-lists. The page keeps its own body selector and box title. The editor
reads the same kind of script and draws the box live from the headings as you type (tocSpecFrom: sel + subs).

    python tools/toc_live.py <page.html> ... [--dry-run]

Itinerary boxes (the .toc-box rows) are a different design and are left alone: toc_sync.py keeps those.
"""
import io, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "armenia" / "gyumri.html"

SUB_CSS = """/* collapsible section header row */
#toc-b .toc-section-head{display:flex;align-items:baseline;gap:.45rem;width:100%}
#toc-b a.toc-section-link{font-family:Hanken Grotesk,Helvetica,Arial,sans-serif;font-size:.85rem;font-weight:400;color:rgba(28,40,33,.85);text-decoration:none;transition:color .15s;line-height:1.5;flex:1}
#toc-b a.toc-section-link:hover{color:var(--terra)}
#toc-b .toc-arrow{background:none;border:none;padding:0 0 0 .2rem;cursor:pointer;color:var(--terra);font-size:1.2rem;line-height:1.4;transition:transform .2s,opacity .15s;opacity:.6;flex-shrink:0}
#toc-b .toc-arrow:hover{opacity:1}
#toc-b .toc-arrow.open{transform:rotate(180deg)}
/* sub-items */
#toc-b .toc-sub{overflow:hidden;transition:max-height .25s ease}
#toc-b .toc-sub a{display:block;font-family:Hanken Grotesk,Helvetica,Arial,sans-serif;font-size:.78rem;font-weight:300;color:rgba(28,40,33,.7);text-decoration:none;padding:.28rem 0 .28rem 1.1rem;border-left:1.5px solid var(--b-medium);margin-left:.1rem;transition:color .15s}
#toc-b .toc-sub a:hover{color:var(--terra);border-left-color:var(--terra)}
"""


def _model_script():
    s = MODEL.read_text(encoding="utf-8")
    m = [x for x in re.finditer(r"<script>(.*?)</script>", s, re.S) if "toc-b" in x.group(1) and "querySelectorAll" in x.group(1)]
    assert m, "the Gyumri model script moved"
    return m[0].group(1)


def convert(s):
    """-> (new html, note). Leaves a page alone when its box already builds from the headings, or has no box."""
    hits = [x for x in re.finditer(r"<script>(.*?)</script>", s, re.S) if re.search(r"\.id\s*=\s*['\"]toc-b['\"]", x.group(1))]
    if not hits:
        return s, "no contents box"
    m = hits[0]
    old = m.group(1)
    if "querySelectorAll(\"h2.article-h2[id]\")" in old or "querySelectorAll('h2.article-h2[id]')" in old:
        return s, "already builds from the headings"
    if "toc-box" in old or 'class="r"' in old:
        return s, "itinerary box: left to toc_sync"
    body = re.search(r"document\.querySelector\((['\"])(.+?)\1\)", old)
    title = re.search(r'toc-title\\?">([^<\\]*)', old)
    if not body or not title:
        return s, "box not recognized: left alone"
    new = _model_script()
    new = re.sub(r"document\.querySelector\((['\"]).+?\1\)", lambda _: "document.querySelector('%s')" % body.group(2), new, count=1)
    new = re.sub(r'(toc-title\\">)[^<\\]*', lambda mm: mm.group(1) + title.group(1), new, count=1)
    s = s[:m.start(1)] + new + s[m.end(1):]
    # the sub-list styles, into the page's #toc-b style block when it has none
    st = [x for x in re.finditer(r"<style>(.*?)</style>", s, re.S) if "#toc-b" in x.group(1)]
    if st and "toc-sub" not in st[0].group(1):
        x = st[0]
        css = x.group(1)
        mq = css.find("@media")
        css = (css[:mq] + SUB_CSS + css[mq:]) if mq > 0 else (css + SUB_CSS)
        s = s[:x.start(1)] + css + s[x.end(1):]
    return s, "now builds from the headings (title: %s)" % title.group(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    dry = "--dry-run" in sys.argv
    for rel in [a for a in sys.argv[1:] if not a.startswith("--")]:
        p = ROOT / rel
        s = io.open(p, encoding="utf-8", newline="").read()
        t, note = convert(s)
        if t != s and not dry:
            io.open(p, "w", encoding="utf-8", newline="").write(t)
        print("%s: %s%s" % (rel, note, " (dry run)" if dry and t != s else ""))
