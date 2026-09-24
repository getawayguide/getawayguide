"""Redline review for AI edits: the article with every proposed change marked inline, Word style,
and a Changes pane beside it with Accept / Reject on each one.

How it fits the editor
----------------------
When Claude finishes an edit pass on an article it writes a PROPOSAL rather than touching the
file, then opens a review tab:

    .tmp/redline/<slug>/proposal.json     the changes (see schema below)
    .tmp/redline/<slug>/baseline/         the affected files exactly as they were
    python tools/redline.py open <slug>   opens http://127.0.0.1:5003/redline/<slug> in a new tab

The page is served by tools/photo_editor.py (same server as the editor) so it gets the site's
real stylesheets and photos through /site/. It is a separate tab and never touches the editor's
DOM, so working on another article in the editor is undisturbed. Accept / Reject decisions POST
back and are stored; Apply writes the accepted changes to the articles with every safeguard
below, backs the originals up, and records what was applied.

Proposal schema (one object per change, ids stable across rebuilds):
    grammar : {id, kind:"grammar", section, find, replace, count, note}
    cut     : {id, kind:"cut", section, words, sentence, bridge_before, dest_file, dest_after,
               dest_replaces, covered_by, dup_scope, note, context_before, context_after}
A cut with dest_replaces REPLACES that sentence in dest_file with the itinerary's own; with
dest_after it is inserted after that anchor; with neither it is simply removed.

Safeguards on apply (each one caught a real fault during the first pass on Armenia):
  * grammar fixes are applied first and folded into any cut sentence that contains them, so a
    sentence that moves arrives corrected instead of carrying the typo
  * every find / sentence / anchor must match exactly once, or nothing is written
  * a paragraph or list emptied by its cuts is removed, or it renders as a blank line
  * no Google Maps link may vanish from the site: every URL in the article before must exist in
    at least one affected file after, or nothing is written (this caught Vernissage Market)
"""
import html
import json
import os
import re
import shutil
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / ".tmp" / "redline"
SERVER = "http://127.0.0.1:5003"
MAPS = re.compile(r'href="(https://www\.google\.com/maps/place/[^"]+)"')


# ============================================================== store
def load(slug):
    d = STORE / slug
    p = json.loads((d / "proposal.json").read_text(encoding="utf-8"))
    p["_dir"] = d
    dec = d / "decisions.json"
    p["decisions"] = json.loads(dec.read_text(encoding="utf-8")) if dec.exists() else {}
    app = d / "applied.json"
    p["applied"] = json.loads(app.read_text(encoding="utf-8")) if app.exists() else None
    return p


def save_decisions(slug, decisions):
    d = STORE / slug
    (d / "decisions.json").write_text(json.dumps(decisions, indent=1), encoding="utf-8")


def pending():
    out = []
    for d in sorted(STORE.glob("*/proposal.json")):
        p = json.loads(d.read_text(encoding="utf-8"))
        out.append({"slug": p["slug"], "title": p.get("title", p["slug"]), "changes": len(p["changes"]),
                    "applied": (d.parent / "applied.json").exists()})
    return out


# ============================================================== marking the article
def fixed(sentence, changes):
    for g in changes:
        if g["kind"] == "grammar":
            sentence = sentence.replace(g["find"], g["replace"])
    return sentence


def mark(body, changes):
    """Wrap every change in the body with del/ins marks, numbered in document order.
    Returns (marked_body, ordered_change_list). Grammar fixes that sit inside a cut sentence are
    not marked separately (the sentence is struck whole); they ride on the cut's balloon."""
    spans, hidden = [], []
    for c in changes:
        key = c["find"] if c["kind"] == "grammar" else c["sentence"]
        n = body.count(key)
        if n >= 1:
            i = body.index(key)
            spans.append((i, i + len(key), c))
        else:
            hidden.append(c)
    spans.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    outer, i = [], 0
    while i < len(spans):
        s0, e0, c = spans[i]
        inner, j = [], i + 1
        while j < len(spans) and spans[j][0] < e0:
            inner.append(spans[j][2]); j += 1
        c["inner"] = [x["id"] for x in inner]
        for x in inner:
            x["inside"] = c["id"]
        outer.append((s0, e0, c)); i = j

    out, pos, num, ordered = [], 0, 0, []
    for s0, e0, c in outer:
        out.append(body[pos:s0]); num += 1; c["n"] = num; ordered.append(c)
        if c["kind"] == "grammar":
            out.append(f'<del class="rl ed" data-id="{c["id"]}">{body[s0:e0]}</del>'
                       f'<ins class="rl ed" data-id="{c["id"]}">{c["replace"]}</ins>'
                       f'<sup class="rl-n" data-id="{c["id"]}">{num}</sup>')
        else:
            cls = "mv" if c.get("dest_file") else "rm"
            out.append(f'<del class="rl {cls}" data-id="{c["id"]}">{body[s0:e0]}</del>')
            if c.get("bridge_before"):
                out.append(f'<ins class="rl {cls}" data-id="{c["id"]}">{html.escape(c["bridge_before"])}</ins>')
            out.append(f'<sup class="rl-n" data-id="{c["id"]}">{num}</sup>')
        pos = e0
    out.append(body[pos:])
    for s0, e0, c in outer:
        for cid in c.get("inner", []):
            x = next(s[2] for s in spans if s[2]["id"] == cid); num += 1; x["n"] = num; ordered.append(x)
    for c in hidden:
        num += 1; c["n"] = num; c["hidden"] = True; ordered.append(c)
    return "".join(out), ordered


def paragraph_around(text, needle):
    i = text.find(needle)
    if i < 0:
        return ""
    starts = [m.start() for m in re.finditer(r"<(p|li)\b[^>]*>", text[:i])]
    if not starts:
        return needle
    m = re.search(r"</(p|li)>", text[i:])
    return text[starts[-1]:i + (m.end() if m else len(needle))]


def destination_blocks(p):
    """Each destination paragraph once, with every incoming sentence underlined and every
    replaced sentence struck, grouped by file."""
    base = p["_dir"] / "baseline"
    paras, order = {}, []
    for c in p["changes"]:
        f = c.get("dest_file")
        if c["kind"] != "cut" or not f:
            continue
        t = (base / f).read_text(encoding="utf-8")
        incoming = fixed(c["sentence"], p["changes"])
        key = c.get("dest_replaces") or c.get("dest_after")
        para = paragraph_around(t, key)
        if c.get("dest_replaces"):
            m = (f'<del class="rl rm" data-id="{c["id"]}">{c["dest_replaces"]}</del>'
                 f'<ins class="rl mv" data-id="{c["id"]}">{incoming}</ins><sup class="rl-n" data-id="{c["id"]}"></sup>')
        else:
            m = c["dest_after"] + f' <ins class="rl mv" data-id="{c["id"]}">{incoming}</ins><sup class="rl-n" data-id="{c["id"]}"></sup>'
        slot = (f, para)
        if slot in paras and key in paras[slot]:
            paras[slot] = paras[slot].replace(key, m, 1)
        else:
            slot = (f, para, c["id"]) if slot in paras else slot
            paras[slot] = para.replace(key, m, 1); order.append(slot)
    groups = {}
    for slot in order:
        groups.setdefault(slot[0], []).append(paras[slot])
    return groups


# ============================================================== the page
def render(slug):
    """The article itself, with the site's own stylesheets and photos, marked up for review."""
    p = load(slug)
    art_dir = p["article_dir"]
    src = (p["_dir"] / "baseline" / p["article"]).read_text(encoding="utf-8")
    head_end = src.find("</head>")
    body_open = re.search(r"<body[^>]*>", src)
    body_close = src.rfind("</body>")
    head, body_tag, body = src[:head_end], body_open.group(0), src[body_open.end():body_close]

    # site behaviour the review does not need (search, drawer scripts) and that would fight ours
    head = re.sub(r"<script\b.*?</script>", "", head, flags=re.S)
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.S)
    # Every relative path in the article resolves as if it sat at its real place under /site/.
    # The <base> has to come BEFORE the stylesheet links: a base only governs URLs that follow
    # it, and appended at the end of the head it left ../../../styles.css resolving against the
    # page URL, three 404s, and the article in Times New Roman.
    base_href = "/site/" + "/".join(html.escape(seg) for seg in art_dir.split("/")) + "/"
    m = re.search(r"<head[^>]*>", head)
    head = head[:m.end()] + f'<base href="{base_href}">' + head[m.end():]

    marked, ordered = mark(body, p["changes"])
    dests = destination_blocks(p)
    dest_html = "".join(f'<h3 class="rl-dest-file">{html.escape(f)}</h3>' +
                        "".join(f'<div class="rl-dpara">{b}</div>' for b in blocks)
                        for f, blocks in dests.items())
    payload = json.dumps([{k: v for k, v in c.items() if not k.startswith("_")} for c in ordered], ensure_ascii=False)
    words_cut = sum(c.get("words", 0) for c in p["changes"] if c["kind"] == "cut")
    return (head + OVERLAY_CSS + "</head>" + body_tag
            + '<div id="rl-bar">' + BAR + '</div>'
            + '<div id="rl-doc">' + marked
            + '<div id="rl-dest"><h2 class="rl-sec" id="rl-dest-head">In the supporting articles</h2>'
              '<p class="rl-hint">Each moved sentence, shown where it lands. Red is what that article gives up, green is the itinerary wording coming in.</p>'
            + dest_html + '</div></div>'
            + '<aside id="rl-pane"><div class="rl-head"><b>Changes</b><span id="rl-cnt"></span>'
              '<div class="rl-seg"><button id="rl-f-all" aria-pressed="true">All</button><button id="rl-f-cut">Cuts</button><button id="rl-f-ed">Grammar</button></div></div>'
              '<div class="rl-list" id="rl-list"></div></aside>'
            + "<script>const RL_SLUG=" + json.dumps(slug) + ";const RL=" + payload
            + ";const RL_DEC=" + json.dumps(p["decisions"]) + ";const RL_APPLIED=" + json.dumps(p["applied"])
            + f";const RL_BASE={p.get('words_before', 0)},RL_TARGET={p.get('words_target', 0)};"
            + f"const RL_TLABEL={json.dumps(p.get('target_label', 'target'))};</script>"
            + OVERLAY_JS + "</body></html>")


# ============================================================== apply
def apply(slug, decisions=None, root=None, dry=False):
    """Write the accepted changes. Validates everything first and writes nothing on any fault.
    `root` lets a rehearsal target a scratch copy of the article folder."""
    p = load(slug)
    decisions = decisions if decisions is not None else p["decisions"]
    arts = Path(root) if root else ROOT / p["article_dir"]
    changes = p["changes"]
    ok = lambda cid: bool(decisions.get(cid, False))
    files, problems, plan = {}, [], []

    def get(name):
        if name not in files:
            files[name] = (arts / name).read_text(encoding="utf-8", newline="")
        return files[name]

    cuts = [c for c in changes if c["kind"] == "cut"]
    for g in changes:
        if g["kind"] != "grammar" or not ok(g["id"]):
            continue
        s = get(p["article"])
        if s.count(g["find"]) == 0:
            problems.append(f'{g["id"]}: text not found'); continue
        plan.append(("replace", p["article"], g["find"], g["replace"], g["id"]))
        for c in cuts:                       # the fix travels with a moved sentence
            if g["find"] in c["sentence"]:
                c["sentence"] = c["sentence"].replace(g["find"], g["replace"])
    for c in cuts:
        if not ok(c["id"]):
            continue
        s = get(p["article"])
        for op, f, a, b, _ in plan:
            if op == "replace" and f == p["article"]:
                s = s.replace(a, b)
        if s.count(c["sentence"]) != 1:
            problems.append(f'{c["id"]}: sentence appears {s.count(c["sentence"])}x'); continue
        plan.append(("cut", p["article"], c["sentence"], c.get("bridge_before", ""), c["id"]))
        d = c.get("dest_file")
        if not d:
            continue
        t = get(d)
        if c.get("dest_replaces"):
            if t.count(c["dest_replaces"]) != 1:
                problems.append(f'{c["id"]}: replace target appears {t.count(c["dest_replaces"])}x in {d}'); continue
            plan.append(("replace", d, c["dest_replaces"], c["sentence"], c["id"]))
        else:
            if t.count(c.get("dest_after", "")) != 1:
                problems.append(f'{c["id"]}: anchor appears {t.count(c.get("dest_after", ""))}x in {d}'); continue
            plan.append(("insert", d, c["dest_after"], c["sentence"], c["id"]))
    if problems:
        return {"ok": False, "problems": problems}

    for op, f, a, b, _ in plan:
        s = get(f)
        if op == "replace":
            s = s.replace(a, b)
        elif op == "cut":
            i = s.index(a); j = i + len(a)
            while j < len(s) and s[j] == " ":
                j += 1
            s = s[:i] + (b + " " if b else "") + s[j:]
        elif op == "insert":
            i = s.index(a) + len(a); s = s[:i] + " " + b + s[i:]
        files[f] = s
    for f in list(files):
        s = files[f]
        s = re.sub(r'\n?[ \t]*<p class="copy day-copy">\s*</p>', "", s)
        s = re.sub(r"\n?[ \t]*<ul(?:\s[^>]*)?>\s*</ul>", "", s)
        files[f] = s

    before = set(MAPS.findall((arts / p["article"]).read_text(encoding="utf-8", newline="")))
    after = set()
    for f in p["files"]:
        after |= set(MAPS.findall(files.get(f) or (arts / f).read_text(encoding="utf-8", newline="")))
    lost = sorted(before - after)
    if lost:
        return {"ok": False, "problems": ["these Maps links would disappear from the site: " + ", ".join(u[:80] for u in lost)]}

    n_g = sum(1 for c in changes if c["kind"] == "grammar" and ok(c["id"]))
    n_c = sum(1 for c in cuts if ok(c["id"]))
    words = sum(c.get("words", 0) for c in cuts if ok(c["id"]))
    result = {"ok": True, "grammar": n_g, "cuts": n_c, "words": words, "files": sorted(files), "dry": dry}
    if not dry:
        # a rehearsal (root given) keeps its backups beside the scratch copy and records nothing
        # in the store: the first one wrote applied.json for the REAL article and would have shown
        # the live review as already applied, with the button disabled, while nothing had changed
        bdir = (arts.parent / (arts.name + "-before-apply")) if root else (p["_dir"] / "before-apply")
        bdir.mkdir(parents=True, exist_ok=True)
        for f, s in files.items():
            shutil.copy2(arts / f, bdir / f)
            (arts / f).write_text(s, encoding="utf-8", newline="")
        result["applied_at"] = datetime.now().isoformat(timespec="seconds")
        result["backups"] = str(bdir)
        if not root:
            (p["_dir"] / "applied.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def open_tab(slug, standalone=False):
    """Open the review in a new browser tab. By default that is the EDITOR, at
    /browser#review=<article path>: the pane opens on the comments and changes at once and asks
    for one click on Open Article, after which everything anchors in place and Accept / Reject
    work on the live text. `standalone` opens the read-only redline page instead."""
    if standalone:
        url = f"{SERVER}/redline/{slug}"
    else:
        from urllib.parse import quote
        p = load(slug)
        # a query parameter: the editor's view router owns the URL hash
        url = f"{SERVER}/browser?review=" + quote(p["article_dir"] + "/" + p["article"], safe="/")
    try:
        os.startfile(url)              # Windows: the default browser, in a new tab
    except Exception:
        webbrowser.open_new_tab(url)
    return url


# ============================================================== page chrome
OVERLAY_CSS = """
<style id="rl-css">
:root{--rl-ink:#1C2821;--rl-line:rgba(28,40,33,.14);--rl-mist:#F5F5F2;--rl-rm:#B4553C;--rl-ins:#2557A7;--rl-mv:#2D6B50;--rl-bar:#C9C3B4}
/* the site's own chrome is not under review */
nav,footer,.mobile-nav-drawer,#mobile-nav-drawer,.nav-hamburger,#nav-hamburger{display:none!important}
html{scroll-padding-top:70px!important}
body{margin:0!important;padding:60px 380px 0 0!important}
#rl-bar{position:fixed;top:0;left:0;right:0;height:56px;z-index:10000;background:#fff;border-bottom:1px solid var(--rl-line);
  display:flex;gap:.55rem;align-items:center;padding:0 1rem;font:15px/1.4 'Hanken Grotesk',Helvetica,Arial,sans-serif;color:var(--rl-ink)}
#rl-bar h1{font:400 1.05rem/1.2 Newsreader,Georgia,serif;margin:0 .5rem 0 0}
#rl-bar button,#rl-pane button{font:600 .66rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.06em;text-transform:uppercase;padding:.5rem .75rem;
  border:1px solid var(--rl-line);background:#fff;border-radius:3px;cursor:pointer;color:var(--rl-ink)}
#rl-bar button:hover{border-color:var(--rl-mv);color:var(--rl-mv)}
#rl-bar button.primary{background:var(--rl-ink);color:#fff;border-color:var(--rl-ink)}
#rl-bar button.apply{background:var(--rl-mv);color:#fff;border-color:var(--rl-mv)}
#rl-bar button:disabled{opacity:.45;cursor:default}
.rl-seg{display:inline-flex;border:1px solid var(--rl-line);border-radius:3px;overflow:hidden}
.rl-seg button{border:0!important;border-right:1px solid var(--rl-line)!important;border-radius:0!important}
.rl-seg button:last-child{border-right:0!important}
.rl-seg button[aria-pressed="true"]{background:var(--rl-ink)!important;color:#fff!important}
#rl-tally{margin-left:auto;text-align:right;font:600 .64rem/1.3 'DM Mono',monospace;letter-spacing:.06em;text-transform:uppercase;color:#6b7a70}
#rl-bud{height:5px;width:200px;background:var(--rl-mist);border-radius:3px;overflow:hidden;margin-top:.3rem}
#rl-bud i{display:block;height:100%;background:var(--rl-mv)}
#rl-status{font:600 .64rem/1 'DM Mono',monospace;letter-spacing:.06em;color:#6b7a70}
/* the marks: strong enough to beat the site's own type rules */
#rl-doc del.rl,#rl-doc ins.rl{border-radius:2px;cursor:pointer;text-decoration-thickness:1.5px!important}
#rl-doc del.rm,#rl-doc del.ed{color:var(--rl-rm)!important;text-decoration:line-through!important}
#rl-doc ins.ed{color:var(--rl-ins)!important;text-decoration:underline!important;text-underline-offset:2px}
#rl-doc del.mv{color:var(--rl-mv)!important;text-decoration:line-through double!important;text-decoration-thickness:1px!important}
#rl-doc ins.mv{color:var(--rl-mv)!important;text-decoration:underline double!important;text-decoration-thickness:1px!important;text-underline-offset:2px}
#rl-doc del.rl *,#rl-doc ins.rl *{color:inherit!important}
#rl-doc sup.rl-n{font:600 .58rem/1 'DM Mono',monospace!important;color:#fff!important;background:#8a9790;border-radius:8px;padding:.15rem .32rem;margin-left:.15rem;vertical-align:super;cursor:pointer;user-select:none;text-decoration:none!important}
#rl-doc .rl-hit del.rl,#rl-doc .rl-hit ins.rl{background:#FFF3B0}
#rl-doc .rl-hit sup.rl-n{background:var(--rl-ink)}
#rl-doc :is(p,li,div.copy,.rl-dpara):has(del.rl,ins.rl){box-shadow:-2px 0 0 0 var(--rl-bar);padding-left:.6rem}
/* rejected reads as the original */
#rl-doc del.rl.rej{color:inherit!important;text-decoration:none!important;opacity:.55}
#rl-doc ins.rl.rej,#rl-doc sup.rl-n.rej{display:none}
#rl-doc del.rl.rej::after{content:" (kept)";font:600 .55rem/1 'DM Mono',monospace;color:#8a9790;letter-spacing:.08em;text-transform:uppercase}
/* No Markup */
body.rl-final #rl-doc del.rl:not(.rej){display:none}
body.rl-final #rl-doc ins.rl{color:inherit!important;text-decoration:none!important}
body.rl-final #rl-doc sup.rl-n{display:none}
body.rl-final #rl-doc :is(p,li,div.copy):has(del.rl,ins.rl){box-shadow:none;padding-left:0}
body.rl-final #rl-doc del.rl.rej{display:inline;opacity:1}body.rl-final #rl-doc del.rl.rej::after{content:""}
body.rl-final #rl-doc :is(p,li):not(:has(> :not(del.rl, sup.rl-n))):has(del.rl){display:none}
/* destinations */
#rl-dest{max-width:760px;margin:3rem auto 5rem;padding:0 1.5rem;font:15px/1.65 'Hanken Grotesk',Helvetica,Arial,sans-serif;color:var(--rl-ink)}
.rl-sec{font:400 1.45rem/1.2 Newsreader,Georgia,serif;margin:0 0 .5rem;padding-bottom:.35rem;border-bottom:1px solid var(--rl-line)}
.rl-hint{font-size:.86rem;color:#6b7a70}
.rl-dest-file{font:600 .68rem/1 'DM Mono',monospace;letter-spacing:.12em;text-transform:uppercase;color:var(--rl-mv);margin:2.2rem 0 .8rem;padding-bottom:.4rem;border-bottom:1px solid var(--rl-line)}
.rl-dpara{margin:0 0 1rem;padding:.7rem .9rem;background:var(--rl-mist);border-radius:3px}
.rl-dpara p,.rl-dpara li{margin:0;list-style:none}
/* pane */
#rl-pane{position:fixed;top:56px;right:0;bottom:0;width:380px;background:#fff;border-left:1px solid var(--rl-line);display:flex;flex-direction:column;z-index:9999;
  font:15px/1.5 'Hanken Grotesk',Helvetica,Arial,sans-serif;color:var(--rl-ink)}
.rl-head{padding:.7rem 1rem;border-bottom:1px solid var(--rl-line);display:flex;gap:.5rem;align-items:center}
.rl-head b{font-size:.82rem}.rl-head span{font:600 .64rem/1 'DM Mono',monospace;color:#6b7a70}
.rl-head .rl-seg{margin-left:auto}
.rl-list{overflow:auto;padding:.6rem;flex:1}
.rl-b{border:1px solid var(--rl-line);border-radius:4px;padding:.65rem .75rem;margin:0 0 .5rem;cursor:pointer;background:#fff}
.rl-b:hover{border-color:#b9cfc2}.rl-b.active{border-color:var(--rl-ink);box-shadow:0 0 0 2px rgba(28,40,33,.08)}
.rl-b.denied{opacity:.5;background:var(--rl-mist)}
.rl-b .k{display:flex;align-items:center;gap:.45rem;font:600 .6rem/1 'DM Mono',monospace;letter-spacing:.1em;text-transform:uppercase;color:#6b7a70}
.rl-b .k .num{background:#8a9790;color:#fff;border-radius:8px;padding:.15rem .4rem}
.rl-b .k .w{margin-left:auto;font-weight:400}
.rl-b .k.rm .num{background:var(--rl-rm)}.rl-b .k.ed .num{background:var(--rl-ins)}.rl-b .k.mv .num{background:var(--rl-mv)}
.rl-b .t{font-size:.86rem;margin:.4rem 0 0}
.rl-b .t del{color:var(--rl-rm);text-decoration:line-through}.rl-b .t ins{color:var(--rl-ins);text-decoration:underline}
.rl-b .t del.mv{color:var(--rl-mv)}
.rl-b .m{font-size:.76rem;color:#6b7a70;margin-top:.4rem}.rl-b .m b{color:var(--rl-mv);font-weight:600}.rl-b .m .lose{color:var(--rl-rm)}
.rl-b .act{display:flex;gap:.3rem;margin-top:.55rem}
.rl-b .act button{padding:.35rem .6rem;font-size:.62rem}
.rl-b .act button[aria-pressed="true"]{background:var(--rl-ink);color:#fff;border-color:var(--rl-ink)}
.rl-notvis{font:600 .6rem/1.4 'DM Mono',monospace;letter-spacing:.1em;text-transform:uppercase;color:#9aa69f;margin:1rem 0 .5rem}
#rl-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:10001;background:var(--rl-ink);color:#fff;padding:.7rem 1.1rem;border-radius:4px;
  font:14px/1.4 'Hanken Grotesk',sans-serif;max-width:640px;display:none}
@media (max-width:1100px){body{padding-right:0!important}#rl-pane{position:static;width:auto;border-left:0;border-top:1px solid var(--rl-line);max-height:50vh}}
</style>"""

BAR = """<h1 id="rl-title">Redline</h1>
<div class="rl-seg"><button id="rl-v-markup" aria-pressed="true">All markup</button><button id="rl-v-final">No markup</button></div>
<div class="rl-seg"><button id="rl-prev">&#8593; Previous</button><button id="rl-next">Next &#8595;</button></div>
<button id="rl-all-yes">Accept all</button><button id="rl-all-no">Reject all</button>
<button class="primary" id="rl-save">Save</button>
<button class="apply" id="rl-apply">Apply accepted</button>
<span id="rl-status"></span>
<div id="rl-tally"><div id="rl-tally-t"></div><div id="rl-bud"><i id="rl-bud-i"></i></div></div>
<div id="rl-toast"></div>"""

OVERLAY_JS = r"""<script>
(function(){
const st = {}; RL.forEach(c => st[c.id] = (c.id in RL_DEC) ? !!RL_DEC[c.id] : true);
const byId = Object.fromEntries(RL.map(c => [c.id, c]));
let filter='all', active=null, dirty=false;
const $ = id => document.getElementById(id);
function esc(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}
function plain(h){const d=document.createElement('div');d.innerHTML=h||'';return d.textContent;}
const KIND = c => c.kind==='grammar' ? ['ed','Edit'] : (c.dest_file ? ['mv','Moved'] : ['rm','Deleted']);
$('rl-title').textContent = document.title.replace(/ [-–—] getawayguide.*$/,'') + ' — redline';
function paintDoc(){
  document.querySelectorAll('#rl-doc [data-id]').forEach(el => el.classList.toggle('rej', !st[el.dataset.id]));
  document.querySelectorAll('#rl-doc sup.rl-n[data-id]').forEach(s => { const c=byId[s.dataset.id]; if (c) s.textContent=c.n; });
}
function renderPane(){
  const list=$('rl-list'); list.innerHTML='';
  const rows = RL.filter(c => filter==='all' || (filter==='ed' ? c.kind==='grammar' : c.kind!=='grammar'));
  let seenHidden=false;
  rows.forEach(c => {
    const [cls,label]=KIND(c);
    if (c.hidden && !seenHidden){ seenHidden=true; const h=document.createElement('div'); h.className='rl-notvis'; h.textContent='Not visible on the page (hidden design alternates)'; list.appendChild(h); }
    const d=document.createElement('div'); d.className='rl-b'+(st[c.id]?'':' denied')+(active===c.id?' active':''); d.dataset.id=c.id;
    const body = c.kind==='grammar' ? `<del>${esc(plain(c.find))}</del> <ins>${esc(plain(c.replace))}</ins>` : `<del class="${cls}">${esc(plain(c.sentence))}</del>`;
    let meta='';
    if (c.kind==='cut'){
      if (c.dest_file) meta += `<div class="m">Moves to <b>${esc(c.dest_file)}</b>` + (c.dest_replaces ? ` and replaces there: <span class="lose">&ldquo;${esc(plain(c.dest_replaces))}&rdquo;</span>` : ' as a new sentence') + `</div>`;
      else if (c.dup_scope==='itinerary') meta += `<div class="m">Removed only. The article already says this elsewhere on the page.</div>`;
      else if (c.covered_by) meta += `<div class="m">Removed only. A supporting article already covers it and this sentence would not fit there: &ldquo;${esc(plain(c.covered_by))}&rdquo;</div>`;
      else meta += `<div class="m">Removed only.</div>`;
    }
    if (c.inside) meta += `<div class="m">Inside change ${byId[c.inside].n}. If that cut is accepted this fix travels with the sentence to <b>${esc(byId[c.inside].dest_file||'nowhere')}</b>.</div>`;
    if (c.inner && c.inner.length) meta += `<div class="m">Carries grammar fixes ${c.inner.map(i=>byId[i].n).join(', ')}.</div>`;
    if (c.note) meta += `<div class="m">${esc(c.note)}</div>`;
    if (c.count>1) meta += `<div class="m">Applies to ${c.count} copies of this block.</div>`;
    d.innerHTML = `<div class="k ${cls}"><span class="num">${c.n}</span>${label}${c.section?' &middot; '+esc(c.section):''}<span class="w">${c.words?c.words+' words':''}</span></div><div class="t">${body}</div>${meta}
      <div class="act"><button data-v="1" aria-pressed="${st[c.id]}">Accept</button><button data-v="0" aria-pressed="${!st[c.id]}">Reject</button></div>`;
    d.onclick = e => { if (e.target.tagName!=='BUTTON') jump(c.id, true); };
    d.querySelectorAll('button').forEach(b => b.onclick = ev => { ev.stopPropagation(); st[c.id]=b.dataset.v==='1'; dirty=true; paintAll(); });
    list.appendChild(d);
  });
  $('rl-cnt').textContent = rows.length+' shown';
}
function tally(){
  const cut = RL.filter(c=>st[c.id]&&c.kind==='cut').reduce((s,c)=>s+(c.words||0),0);
  const yes = Object.values(st).filter(Boolean).length, now = RL_BASE-cut;
  $('rl-tally-t').textContent = `${yes}/${RL.length} accepted · ${cut} words cut` + (RL_BASE ? ` · ${now} left (${RL_TLABEL} ${RL_TARGET})` : '');
  $('rl-bud-i').style.width = RL_BASE ? Math.max(0,Math.min(100,100*(RL_BASE-now)/(RL_BASE-RL_TARGET)))+'%' : '0';
  $('rl-status').textContent = RL_APPLIED ? 'applied '+RL_APPLIED.applied_at : (dirty ? 'unsaved decisions' : 'saved');
  $('rl-apply').disabled = !!RL_APPLIED;
}
function paintAll(){ paintDoc(); renderPane(); tally(); }
function jump(id, fromPane, clicked){
  active=id;
  document.querySelectorAll('#rl-doc .rl-hit').forEach(x=>x.classList.remove('rl-hit'));
  const marks=[...document.querySelectorAll(`#rl-doc [data-id="${id}"]`)];
  marks.forEach(m => (m.closest('p,li,div.copy,.rl-dpara')||m).classList.add('rl-hit'));
  const t = clicked || marks[0]; if (t) t.scrollIntoView({block:'center', behavior:'auto'});
  renderPane();
  const card=document.querySelector(`.rl-b[data-id="${id}"]`); if (card && !fromPane) card.scrollIntoView({block:'nearest'});
}
$('rl-doc').addEventListener('click', e => {
  const m=e.target.closest('[data-id]'); if (m){ e.preventDefault(); jump(m.dataset.id,false,m); return; }
  const a=e.target.closest('a[href]'); if (a && !/^https?:/.test(a.getAttribute('href'))) e.preventDefault();
});
function step(dir){ const order=RL.filter(c=>document.querySelector(`#rl-doc [data-id="${c.id}"]`)).map(c=>c.id); const i=order.indexOf(active); jump(order[(i+dir+order.length)%order.length],false); }
$('rl-prev').onclick=()=>step(-1); $('rl-next').onclick=()=>step(1);
$('rl-all-yes').onclick=()=>{RL.forEach(c=>st[c.id]=true);dirty=true;paintAll();};
$('rl-all-no').onclick=()=>{RL.forEach(c=>st[c.id]=false);dirty=true;paintAll();};
for (const [id,v] of [['rl-f-all','all'],['rl-f-cut','cut'],['rl-f-ed','ed']])
  $(id).onclick=()=>{filter=v;['rl-f-all','rl-f-cut','rl-f-ed'].forEach(x=>$(x).setAttribute('aria-pressed',x===id));renderPane();};
$('rl-v-markup').onclick=()=>{document.body.classList.remove('rl-final');$('rl-v-markup').setAttribute('aria-pressed','true');$('rl-v-final').setAttribute('aria-pressed','false');};
$('rl-v-final').onclick=()=>{document.body.classList.add('rl-final');$('rl-v-markup').setAttribute('aria-pressed','false');$('rl-v-final').setAttribute('aria-pressed','true');};
function toast(msg, ms){ const t=$('rl-toast'); t.textContent=msg; t.style.display='block'; clearTimeout(t._h); t._h=setTimeout(()=>t.style.display='none', ms||3500); }
async function save(){
  const r = await fetch(`/redline/${RL_SLUG}/decisions`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(st)});
  if (r.ok){ dirty=false; tally(); toast('Decisions saved.'); return true; }
  toast('Save failed: '+r.status); return false;
}
$('rl-save').onclick = save;
$('rl-apply').onclick = async () => {
  const yes = Object.values(st).filter(Boolean).length;
  if (!confirm(`Apply ${yes} accepted change(s) to the article files? Originals are backed up first. If this article is open in the editor, reopen it there before saving so you do not overwrite this.`)) return;
  if (!(await save())) return;
  $('rl-apply').disabled = true; $('rl-status').textContent = 'applying…';
  const r = await fetch(`/redline/${RL_SLUG}/apply`, {method:'POST'}); const j = await r.json();
  if (j.ok){ toast(`Applied ${j.grammar} grammar fixes and ${j.cuts} cuts (${j.words} words) across ${j.files.length} file(s). Backups in ${j.backups}`, 9000); $('rl-status').textContent='applied '+j.applied_at; }
  else { $('rl-apply').disabled=false; $('rl-status').textContent='not applied'; toast('Nothing written. '+(j.problems||[]).join(' | '), 12000); }
};
window.addEventListener('beforeunload', e => { if (dirty){ e.preventDefault(); e.returnValue=''; } });
document.addEventListener('keydown', e => { if (e.target.tagName==='INPUT') return; if (e.key==='j') step(1); if (e.key==='k') step(-1); });
paintAll();
})();
</script>"""


# ============================================================== editor integration
# The review lives inside the article editor as a right-hand pane (review.js). The editor asks
# /review/for?rel=<repo path> when an article opens, marks the pending changes in its own DOM,
# posts each Accept / Reject as it happens, and on Save strips every mark: accepted changes are
# already in the text it writes, everything else is written as the original. commit() then does
# the half the editor cannot: the destination side of accepted MOVES, which live in other files.
COMMENTS = STORE / "_comments"


def slug_for(rel):
    """slug of the pending proposal for this repo-relative article path, else None"""
    rel = rel.replace("\\", "/").lstrip("./")
    for d in STORE.glob("*/proposal.json"):
        p = json.loads(d.read_text(encoding="utf-8"))
        if p["article_dir"] + "/" + p["article"] == rel and not (d.parent / "applied.json").exists():
            return p["slug"]
    return None


def comments_key(rel):
    """comments are per ARTICLE, proposal or not: 'armenia/yerevan.html' -> 'armenia__yerevan'"""
    parts = [x for x in rel.replace("\\", "/").split("/") if x and x != "Drafts" and not x.startswith(".")]
    return "__".join(re.sub(r"[^A-Za-z0-9-]+", "-", x.rsplit(".", 1)[0]) for x in parts[-2:])


def comments_load(key):
    f = COMMENTS / f"{key}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"threads": []}


def comments_save(key, data):
    COMMENTS.mkdir(parents=True, exist_ok=True)
    (COMMENTS / f"{key}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def merge_decisions(slug, partial):
    """decisions arrive one at a time from the editor; null means undecided again (an undo)"""
    d = STORE / slug
    cur = json.loads((d / "decisions.json").read_text(encoding="utf-8")) if (d / "decisions.json").exists() else {}
    for k, v in (partial or {}).items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = bool(v)
    (d / "decisions.json").write_text(json.dumps(cur, indent=1), encoding="utf-8")
    return cur


def commit(slug, accepted_ids):
    """After the editor saved the article with these changes accepted in its text: apply the
    destination side of any accepted MOVE (insert or replace in another file, with backups and
    the Maps-link guard), then record them as applied. Grammar fixes and the source side of every
    cut are already in the saved file, so nothing is written to the article itself."""
    p = load(slug)
    arts = ROOT / p["article_dir"]
    by_id = {c["id"]: c for c in p["changes"]}
    acc = [by_id[i] for i in accepted_ids if i in by_id]
    applied = p["applied"] or {"ids": [], "moves": [], "log": []}
    todo = [c for c in acc if c["kind"] == "cut" and c.get("dest_file") and c["id"] not in applied["ids"]]
    files, problems, plan = {}, [], []
    get = lambda f: files.setdefault(f, (arts / f).read_text(encoding="utf-8", newline=""))
    # a moved sentence carries only the grammar fixes Kevin actually accepted, not every one
    # proposed: he may keep a sentence's wording and still send it to the supporting article
    decided = merge_decisions(slug, {})
    accepted_fixes = [g for g in p["changes"] if g["kind"] == "grammar" and decided.get(g["id"]) is True]
    for c in todo:
        t, incoming = get(c["dest_file"]), fixed(c["sentence"], accepted_fixes)
        if incoming in t:
            continue                                     # already there (a re-save)
        if c.get("dest_replaces"):
            if t.count(c["dest_replaces"]) != 1:
                problems.append(f'{c["id"]}: replace target appears {t.count(c["dest_replaces"])}x in {c["dest_file"]}'); continue
            plan.append((c["dest_file"], c["dest_replaces"], incoming, c["id"]))
        else:
            a = c.get("dest_after", "")
            if t.count(a) != 1:
                problems.append(f'{c["id"]}: anchor appears {t.count(a)}x in {c["dest_file"]}'); continue
            plan.append((c["dest_file"], a, a + " " + incoming, c["id"]))
    if problems:
        return {"ok": False, "problems": problems}
    for f, a, b, _ in plan:
        files[f] = files[f].replace(a, b, 1)
    # the article was just saved by the editor with its cut sentences gone: every Maps link the
    # baseline had must still exist somewhere across the saved article and the updated files
    base = (p["_dir"] / "baseline" / p["article"]).read_text(encoding="utf-8")
    have = set(MAPS.findall((arts / p["article"]).read_text(encoding="utf-8", newline="")))
    for f in p["files"]:
        if f != p["article"]:
            have |= set(MAPS.findall(files.get(f) or (arts / f).read_text(encoding="utf-8", newline="")))
    lost = sorted(set(MAPS.findall(base)) - have)
    if lost:
        return {"ok": False, "problems": ["these Maps links would disappear from the site: " + ", ".join(u[:80] for u in lost)]}
    bdir = p["_dir"] / "before-apply"; bdir.mkdir(exist_ok=True)
    for f, s in files.items():
        if s != (arts / f).read_text(encoding="utf-8", newline=""):
            shutil.copy2(arts / f, bdir / f)
            (arts / f).write_text(s, encoding="utf-8", newline="")
    applied["ids"] = sorted(set(applied["ids"]) | {c["id"] for c in acc})
    applied["moves"] = sorted(set(applied["moves"]) | {i for _, _, _, i in plan})
    applied["log"].append({"at": datetime.now().isoformat(timespec="seconds"), "accepted": [c["id"] for c in acc],
                           "destination_writes": [i for _, _, _, i in plan]})
    decisions = merge_decisions(slug, {})
    done = all((cid in applied["ids"]) or (decisions.get(cid) is False) for cid in by_id)
    applied["complete"] = done
    (p["_dir"] / ("applied.json" if done else "progress.json")).write_text(json.dumps(applied, indent=1), encoding="utf-8")
    if done and (p["_dir"] / "progress.json").exists():
        (p["_dir"] / "progress.json").unlink()
    return {"ok": True, "applied": len(acc), "destination_writes": len(plan), "complete": done}


def review_state(rel):
    """everything the editor needs when an article opens"""
    slug = slug_for(rel)
    out = {"slug": slug, "comments_key": comments_key(rel), "changes": [], "decisions": {},
           "comments": comments_load(comments_key(rel))}
    if slug:
        p = load(slug)
        prog = p["_dir"] / "progress.json"
        applied_ids = set(json.loads(prog.read_text(encoding="utf-8"))["ids"]) if prog.exists() else set()
        out["changes"] = [c for c in p["changes"] if c["id"] not in applied_ids]
        out["decisions"] = p["decisions"]
        out["title"] = p.get("title", slug)
        out["created"] = datetime.fromtimestamp((p["_dir"] / "proposal.json").stat().st_mtime).isoformat(timespec="seconds")
        out["words_before"], out["words_target"], out["target_label"] = p.get("words_before", 0), p.get("words_target", 0), p.get("target_label", "")
    return out


# ============================================================== CLI
if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__); raise SystemExit(0)
    cmd, rest = args[0], args[1:]
    if cmd == "open":                 # the editor, comments and changes ready to anchor
        print(open_tab(rest[0]))
    elif cmd == "open-tab":           # the read-only standalone redline page
        print(open_tab(rest[0], standalone=True))
    elif cmd == "list":
        for p in pending():
            print(f"  {p['slug']:<30} {p['changes']:>4} changes  {'APPLIED' if p['applied'] else 'pending'}   {p['title']}")
    elif cmd == "apply":
        dry = "--dry-run" in rest
        r = apply(rest[0], dry=dry)
        print(json.dumps(r, indent=1))
    elif cmd == "render":
        out = Path(rest[1]) if len(rest) > 1 else STORE / rest[0] / "review.html"
        out.write_text(render(rest[0]), encoding="utf-8"); print(out)
    else:
        print("commands: open <slug> | list | apply <slug> [--dry-run] | render <slug> [file]")
