"""What a reader actually sees in a page built from the artifact template.

The itinerary template carries several layout variants of one block side by side
(<div class="v v-list" data-v="a">, "b", "c" ...) and <body data-list="a"> picks the one that
shows; the others stay in the file, hidden by CSS. Checks that read the raw HTML counted the
hidden copies as content: the Launch tab's coverage check passed because a HIDDEN variant
mentioned the train-station lot while the visible list did not (2026-09-30).

    blank_hidden(html)  the page with every unselected variant replaced by spaces of the same
                        length (newlines kept), so offsets into it are offsets into the file.
"""
import re

VSTART = re.compile(r'<div class="v v-([a-z]+)" data-v="([a-z0-9]+)"[^>]*>')
DIV = re.compile(r"<(/?)div\b[^>]*>", re.I)


def selected(html):
    """{kind: variant} from <body data-kind="v" ...>"""
    m = re.search(r"<body\b([^>]*)>", html)
    return dict(re.findall(r'\bdata-([a-z]+)="([a-z0-9]+)"', m.group(1))) if m else {}


def _end(html, start):
    """index just past the </div> that closes the <div> opening at start"""
    depth = 0
    for m in DIV.finditer(html, start):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return m.end()
    return len(html)


def blank_hidden(html):
    sel = selected(html)
    if not sel:
        return html
    out, pos = [], 0
    for m in VSTART.finditer(html):
        if m.start() < pos:
            continue                                  # inside a block already blanked
        kind, v = m.group(1), m.group(2)
        if kind not in sel or sel[kind] == v:
            continue
        end = _end(html, m.start())
        out.append(html[pos:m.start()])
        out.append(re.sub(r"[^\n]", " ", html[m.start():end]))
        pos = end
    out.append(html[pos:])
    return "".join(out)
