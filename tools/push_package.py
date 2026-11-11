"""The push package: everything in the working copy that is ready to go live, the calls Kevin makes before it does,
and his answers (Kevin, 2026-11-10: "can you make the push package a separate section on the queue tab? With an
interface that matches our editor, but shows the individual issues like the artifact? I should be able to respond to
each one of them and click on the options offered").

Two files in .tmp/push_package/ (override the folder with PUSH_PACKAGE_DIR; under a test's QUEUE_TAB_DIR it is
<QUEUE_TAB_DIR>/push_package):

  package.json   WHAT is in the push. Built by this script only; the editor never writes it.
                   calls     [{id: "call-N", num, title, diff: [{label, before, after}], note, list, page,
                               options: [{id, label, recommended}], shots_id, shots: [tier]}]
                   sections  [{id, title, intro, changes: [{id, title, page, url, note, list, group, was, now,
                               shots_id, shots: [tier], shoot: {spec}}], details: {title, list}}]
                   summary   [{group, notices, files}]   checks [{state: ok|fix, label, text}]   push_steps [md]
                   tier      {kind: desk|tab|phone|icons, label, w, before: {src, w, h} | null, before_note, after}
                   unreviewed_pages [{id: "page:<rel>", page, status}]   changed pages no change entry describes yet
                   inventory, auto (computed from the working copy by `rebuild`)
                 Text fields are a small markdown: **bold**, `code`, [text](https://...).
  answers.json   HIS answers, written only by POST /api/push (and `import-todo`), keyed by call or change id:
                   {package, rev, updated, answers: {id: {choice, choice_label, recommended, reply, note, hold, ok,
                    seen, via, rev: {field: n}, times: {field: iso}}}}
                 Every field carries the store revision that last wrote it. A POST names the fields it sets and the
                 revision it saw for each; a field written since (another tab, a stale page) is refused with 409 and
                 nothing in that request is written, so a stale tab can never wipe a newer answer. Fields a request
                 does not name are never touched. `seen` is set-once and exempt.
                 When package.json is rebuilt on a new base (after a push), its id changes, the old answers are
                 ignored, and the first new answer archives them to answers-<old id>.json.
  add_list.txt   optional: the untracked files the push adds, one per line (falls back to files_to_add.txt); `rebuild`
                 counts the ones still untracked with the ones the pages use (a launch's search thumbnails and share
                 pictures no page names directly).

Routes (registered on the photo server by queue_tab.register):
  GET  /api/push                      -> {ok, package | null, package_version, answers, rev}
  POST /api/push {op: "answer", id, package, set: {field: value}, seen: {field: rev | null}}
                                      -> {ok, answers, rev, package_version}; 409 {stale, conflicts, answers, error}
  GET  /api/push/file/shots/<file>    -> a screenshot from the package's shots/ folder

When Kevin says "push":   python tools/push_package.py answers      (what he chose, replied, held back)

Building and rebuilding:
  python tools/push_package.py import-html [page.html]   the published push-package page -> package.json (no re-shoot)
  python tools/push_package.py merge fragment.json       add or replace calls / sections / changes (by id)
  python tools/push_package.py shoot [ids...] [--missing] before (git base) / after (working copy) crops for the
                                                         changes that carry a `shoot` spec, into shots/
                                                         (kind "stack" with `parts` [spec]: several spots, one
                                                         picture; `mark` [link text]: outline those links)
  python tools/push_package.py rebuild [--base origin/main]   re-reads the working copy: git diff against the base,
                                                         the fix reports, the shots folder; keeps every id
  python tools/push_package.py check                     ids unique, one recommended option per call, shots exist
  python tools/push_package.py import-todo todo.json     the old "push" inbox items' replies/status -> answers.json
  python tools/push_package.py archive                   after the push: answers.json -> answers-<id>.json
  python tools/push_package.py retext trim.json          shorter notes (the old one kept as `more`, under Details)

Settled (Nov 11): each item is open / working / settled (see item_status). done.json is Claude's log of what it did:
  python tools/push_package.py todo                      the answers waiting to be carried out
  python tools/push_package.py done <id> "what was done" settles it (a newer answer from him reopens it)
  python tools/push_package.py status                    every item and its state
  python tools/push_package.py watch [--every 15]        for a Monitor: a line each time an answer needs doing
  reopened.json: the Queue tab's held-back drawer (tools/queue_status.py) puts a held item back on its page -> that item
  is open again (waits on Kevin) until he answers anew or Claude marks it done; answers.json is never touched by it
Checks (Nov 11): all live and pre-push; checks.json, run in the background by the editor too:
  python tools/push_package.py checks [--only site,pull] stage the add list, lint, GitHub, the test suites
  python tools/push_package.py stage                     git add the add list (index only)
"""
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = "https://getawayguide.io/"
FIELDS = ("choice", "reply", "note", "hold", "ok", "seen")
_lock = threading.RLock()


def pkg_dir():
    if os.environ.get("PUSH_PACKAGE_DIR"):
        return Path(os.environ["PUSH_PACKAGE_DIR"])
    return Path(os.environ.get("QUEUE_TAB_DIR") or (ROOT / ".tmp")) / "push_package"


def package_path():
    return pkg_dir() / "package.json"


def answers_path():
    return pkg_dir() / "answers.json"


def readonly():
    return os.environ.get("QUEUE_TAB_READONLY") == "1"


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def out(s):
    """stdout is cp1252 on this box when redirected: write UTF-8 bytes"""
    sys.stdout.buffer.write((s + "\n").encode("utf-8"))
    sys.stdout.flush()


# ------------------------------------------------------------------------------------- files
class FileLock:
    """a lock file beside the target (honored by every writer of the answers), plus the in-process lock"""

    def __init__(self, target: Path):
        self.path = target.with_name(target.name + ".lock")

    def __enter__(self):
        _lock.acquire()
        deadline = time.time() + 3
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 30:
                        self.path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    _lock.release()
                    raise RuntimeError("the push answers are locked by another writer; try again in a moment")
                time.sleep(0.05)

    def __exit__(self, *a):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        _lock.release()


def write_json_atomic(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, indent=1, ensure_ascii=False).encode("utf-8")
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    for _ in range(40):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, p)


def load_package():
    """(package dict | None, version)"""
    p = package_path()
    if not p.is_file():
        return None, None
    data = p.read_bytes()
    return json.loads(data.decode("utf-8-sig")), hashlib.sha1(data).hexdigest()[:12]


# ------------------------------------------------------------------------------------- markup
def slug(s, n=48):
    import unicodedata
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:n].strip("-") or "x"


def html_to_md(s):
    """the page's inline HTML -> the package's small markdown (links, bold, code); every other tag dropped"""
    if s is None:
        return ""
    s = re.sub(r"""<a\b[^>]*?href=(["'])(.*?)\1[^>]*>(.*?)</a>""", lambda m: f"[{html_to_md(m.group(3))}]({html.unescape(m.group(2))})", s, flags=re.S)
    s = re.sub(r"<(b|strong)>(.*?)</\1>", lambda m: f"**{m.group(2)}**", s, flags=re.S)
    s = re.sub(r"<code>(.*?)</code>", lambda m: f"`{m.group(1)}`", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def md_text(s):
    """the small markdown as plain text (for the answers report)"""
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1", s or "")
    return s.replace("**", "").replace("`", "")


# ------------------------------------------------------------------------------------- import the published page
D, P = 1440, 393
# the crop specs the Nov 10 package was shot with (its scratch shoot.py), kept on each change so `shoot` can redo them
LEGACY_SHOOT = {s["id"]: s for s in [
    dict(id="arm-itin-intro", page="armenia/armenia-itinerary.html", kind="el", sel="p", text="Armenia is a small country located"),
    dict(id="arm-itin-visa", page="armenia/armenia-itinerary.html", kind="el", sel="p", text="generous visa policy"),
    dict(id="arm-fn-visa", page="armenia/field-notes.html", kind="el", sel="li", text="180 days visa-free"),
    dict(id="arm-fn-train", page="armenia/field-notes.html", kind="el", sel="li", text="From Tbilisi by night train"),
    dict(id="yerevan-train", page="armenia/yerevan.html", kind="el", sel="li", text="a sleeper train leaves Tbilisi"),
    dict(id="geo-marsh", page="georgia/field-notes.html", kind="el", sel="li", text="option to get to Mestia"),
    dict(id="geo-transfer", page="georgia/field-notes.html", kind="el", sel="li", text="the most comfortable, but expens"),
    dict(id="peru-wayna", page="peru/field-notes.html", kind="el", sel="li", text="up for a workout"),
    dict(id="peru-rainbow", page="peru/field-notes.html", kind="el", sel="li", text="popular, well-kept and social"),
    dict(id="chile-ferry", page="chile/field-notes.html", kind="el", sel="li", text="last ferry for the day"),
    dict(id="meteora-days", page="greece/meteora.html", kind="el", sel="ul", text="closed Friday"),
    dict(id="greece-acropolis", page="greece/field-notes.html", kind="el", sel="li", text="Athens City pass"),
    dict(id="greece-guard", page="greece/field-notes.html", kind="el", sel="li", text="changing of the guard ceremony"),
    dict(id="swiss-jungfrau", page="switzerland/field-notes.html", kind="el", sel="li", text="very pricey"),
    dict(id="swiss-lucerne", page="switzerland/field-notes.html", kind="el", sel="li", text="Lake Lucerne Boat Tour"),
    dict(id="tr-fn-dolmus", page="turkiye/field-notes.html", kind="el", sel="li", text="(called the \"Dolmus\")"),
    dict(id="fethiye-dolmus", page="turkiye/fethiye-oludeniz.html", kind="el", sel="li", text="quite possibl"),
    dict(id="dk-noma", page="denmark/field-notes.html", kind="el", sel="li", text="regular restaurant service"),
    dict(id="ee-sveta", page="estonia/field-notes.html", kind="el", sel="li", text="Local DJs will spin"),
    dict(id="tz-insurance", page="tanzania/field-notes.html", kind="el", sel="ul", text="Zanzibar has its own airport"),
    dict(id="au-budds", page="australia/field-notes.html", kind="el", sel="li", text="Budds In Surfers"),
    dict(id="be-shuttles", page="belgium/field-notes.html", kind="el", sel="li", text="official shuttles"),
    dict(id="nm-boat", page="north-macedonia/field-notes.html", kind="el", sel="li", text="The tour I went on was more expensive"),
    dict(id="me-kotor", page="montenegro/field-notes.html", kind="el", sel="li", text="the best way to explore the Bay of Kotor"),
    dict(id="map-yerevan", page="armenia/yerevan.html", kind="map", map='[data-map="yerevan-routes"]'),
    dict(id="map-el-tunco", page="el-salvador/el-tunco.html", kind="map", map='[data-map="el-tunco-routes"]'),
    dict(id="map-ruta", page="el-salvador/ruta-de-las-flores.html", kind="map", map='[data-map="ruta-routes"]'),
    dict(id="map-santa-ana", page="el-salvador/santa-ana.html", kind="map", map='[data-map="santa-ana-routes"]'),
    dict(id="map-thessaloniki", page="greece/thessaloniki.html", kind="map", map='[data-map="thessaloniki-routes"]'),
    dict(id="map-fethiye", page="turkiye/fethiye-oludeniz.html", kind="map", map='[data-map="fethiye-routes"]'),
    dict(id="map-meteora", page="greece/meteora.html", kind="map", map='figure.itinerary-map'),
    dict(id="top10-arm-hero", page="armenia/top-10-armenia.html", kind="sel", sel=".article-hero", widths=[P], below=40),
    dict(id="top10-arm-intro", page="armenia/top-10-armenia.html", kind="range", sel=".article-body", end="h2.article-h2"),
    dict(id="top10-es-hero", page="el-salvador/top-10-el-salvador.html", kind="sel", sel=".article-hero", widths=[P], below=40),
    dict(id="top10-es-intro", page="el-salvador/top-10-el-salvador.html", kind="range", sel=".article-body", end="h2.article-h2", widths=[D]),
    dict(id="cap-pair-phone", page="armenia/gyumri.html", kind="cap", sel=".img-pair + .img-caption", idx=0, widths=[P]),
    dict(id="cap-pair-tablet", page="armenia/gyumri.html", kind="cap", sel=".img-pair + .img-caption", idx=0, widths=[700]),
    dict(id="cap-meteora-map", page="greece/meteora.html", kind="cap", sel="figure.citymap-fig + .img-caption", idx=0, widths=[P]),
    dict(id="es-fn-arrows", page="el-salvador/field-notes.html", kind="el", sel="p", text="I went with Rick"),
    dict(id="es-xm-title", page="el-salvador/7-waterfalls-hike.html", kind="sel", sel="#explore-more"),
    dict(id="kosovo-page", page="kosovo/index.html", kind="top", h={str(D): 820, str(P): 900}, modes=["after"]),
]}
# the page's intro to the calls described the artifact ("the options below are labels, not buttons"); in the editor
# they ARE buttons, so the imported package says what the editor does instead
CALLS_INTRO = "Each of these is already in the push as described. Pick an option or write a reply; no answer means the recommended option goes."

FIG_RE = re.compile(r'<figure class="shot (before|after|none)"><figcaption>(.*?)</figcaption>(?:<img src="([^"]+)" width="(\d+)" height="(\d+)"[^>]*>|<div class="empty">(.*?)</div>)', re.S)
BA_RE = re.compile(r'<div class="ba (\w+)" data-kind="\w+"><div class="ba-label">(.*?)</div><div class="ba-row">(.*?)</div></div>', re.S)
SPOT_RE = re.compile(r'<article class="spot"(?: id="([^"]+)")?><header><h4>(.*?)</h4>(.*?)</header>(.*?)</article>', re.S)


def _figs(chunk):
    got = {}
    for mode, cap, src, w, h, empty in FIG_RE.findall(chunk):
        if mode == "none":
            got["before"] = None
            got["before_note"] = html_to_md(empty)
        else:
            got[mode] = {"src": src, "w": int(w), "h": int(h)}
    return got


def _tiers(body):
    tiers = []
    for kind, label, row in BA_RE.findall(body):
        m = re.search(r"(\d{3,4})", label)
        t = {"kind": kind, "label": html_to_md(label), "w": int(m.group(1)) if m else None, "before": None}
        t.update(_figs(row))
        tiers.append(t)
    if '<div class="icons">' in body:
        t = {"kind": "icons", "label": "Icons", "w": None, "before": None}
        t.update(_figs(body.split('<div class="icons">', 1)[1]))
        tiers.append(t)
    return tiers


def _note(body):
    m = re.search(r'<p class="note">(.*?)</p>', body, re.S)
    return html_to_md(m.group(1)) if m else ""


def _list(body):
    m = re.search(r'<ul class="plain">(.*?)</ul>', body, re.S)
    return [html_to_md(x) for x in re.findall(r"<li>(.*?)</li>", m.group(1), re.S)] if m else []


def _change(sid, title, head, body, page=None):
    href = re.search(r'href="([^"]+)"', head)
    pg = re.search(r'class="pg"[^>]*>(.*?)</', head, re.S)
    c = {"id": sid or slug(title), "title": html_to_md(title), "page": html_to_md(pg.group(1)) if pg else (page or ""),
         "url": href.group(1) if href else None, "note": _note(body), "list": _list(body), "shots_id": sid or None,
         "shots": _tiers(body)}
    if sid in LEGACY_SHOOT:
        c["shoot"] = LEGACY_SHOOT[sid]
    return c


def import_html(path):
    s = Path(path).read_text(encoding="utf-8")
    pkg = {"format": 1, "title": "Push package", "source": str(Path(path).resolve().relative_to(ROOT)).replace("\\", "/") if str(Path(path).resolve()).startswith(str(ROOT)) else str(path),
           "source_url": "https://claude.ai/artifact/4Lj3gsNmUehCZvvJ71qFqW", "built": now_iso(), "shots_dir": "shots"}
    pkg["eyebrow"] = html_to_md(re.search(r'<div class="eyebrow">(.*?)</div>', s, re.S).group(1))
    pkg["verdict"] = html_to_md(re.search(r'<p class="verdict">(.*?)</p>', s, re.S).group(1))
    pkg["calls_intro"] = CALLS_INTRO
    calls = []
    for m in re.finditer(r'<div class="dcard"><h3>(.*?)</h3>(.*?)\n  </div>\n', s, re.S):
        head, body = m.group(1), m.group(2)
        num = int(re.match(r"\s*(\d+)\.", head).group(1))
        diff = []
        dm = re.search(r'<div class="diff">(.*?)</div>', body, re.S)
        if dm:
            spans = re.findall(r"<span><b>(.*?)</b>:\s*<del>(.*?)</del>\s*<ins>(.*?)</ins></span>", dm.group(1), re.S)
            if spans:
                diff = [{"label": html_to_md(a), "before": html_to_md(b), "after": html_to_md(c)} for a, b, c in spans]
            else:
                diff = [{"label": "", "before": html_to_md(b), "after": html_to_md(c)} for b, c in re.findall(r"<del>(.*?)</del>\s*<ins>(.*?)</ins>", dm.group(1), re.S)]
        opts = []
        om = re.search(r'<div class="opts">(.*?)</div>', body, re.S)
        for cls, label in re.findall(r'<span( class="rec")?>(.*?)</span>', om.group(1) if om else "", re.S):
            lab = html_to_md(label)
            opts.append({"id": slug(lab, 40), "label": lab, "recommended": bool(cls)})
        sp = re.search(r'<div class="spot bare" id="([^"]+)">', body)
        sid = sp.group(1) if sp else None
        call = {"id": f"call-{num}", "num": num, "title": html_to_md(re.sub(r"^\s*\d+\.\s*", "", head)), "diff": diff,
                "note": _note(body), "list": _list(body), "options": opts, "shots_id": sid,
                "page": LEGACY_SHOOT[sid]["page"] if sid in LEGACY_SHOOT else "", "shots": _tiers(body) if sid else []}
        if sid in LEGACY_SHOOT:
            call["shoot"] = LEGACY_SHOOT[sid]
        calls.append(call)
    pkg["calls"] = calls
    fm = re.search(r'</div>\n<p class="note">(Not in this push.*?)</p>', s, re.S)
    pkg["calls_footnote"] = html_to_md(fm.group(1)) if fm else ""
    summ = re.search(r'<h2 id="summary">.*?<tbody>(.*?)</tbody>', s, re.S).group(1)
    pkg["summary"] = [{"group": html_to_md(a), "notices": html_to_md(b), "files": html_to_md(c)}
                      for a, b, c in re.findall(r'<tr><td>(.*?)</td><td>(.*?)</td><td class="n">(.*?)</td></tr>', summ, re.S)]
    heads = list(re.finditer(r'<h2 id="(\w+)">(.*?)</h2>', s))
    sections = []
    for k, h in enumerate(heads):
        sec_id, title = h.group(1), html_to_md(h.group(2))
        chunk = s[h.end(): heads[k + 1].start() if k + 1 < len(heads) else s.index("<footer>")]
        if sec_id in ("calls", "summary"):
            continue
        intro = re.match(r"\s*<p>(.*?)</p>", chunk, re.S)
        if sec_id == "checks":
            pkg["checks"] = [{"state": cls, "label": lab, "text": html_to_md(txt)} for cls, lab, txt in
                             re.findall(r'<div class="check"><span class="pill (\w+)">(\w+)</span><span>(.*?)</span></div>', chunk, re.S)]
            continue
        if sec_id == "how":
            pkg["how_intro"] = html_to_md(intro.group(1)) if intro else ""
            pkg["push_steps"] = [html_to_md(x) for x in re.findall(r"<li>(.*?)</li>", re.search(r'<ol class="steps">(.*?)</ol>', chunk, re.S).group(1), re.S)]
            continue
        sec = {"id": sec_id, "title": title, "intro": html_to_md(intro.group(1)) if intro else "", "changes": []}
        sub = re.search(r"<h3>(.*?)</h3>", chunk)
        main_part, tail = (chunk[:sub.start()], chunk[sub.start():]) if sub else (chunk, "")
        for sid, t, head, body in SPOT_RE.findall(main_part):
            sec["changes"].append(_change(sid, t, head, body))
        if sub and "<table" in tail:              # "Link-only fixes": one change per row, so each can be held back
            group = html_to_md(sub.group(1))
            for a, b, c, d in re.findall(r"<tr><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td></tr>", tail, re.S):
                sec["changes"].append({"id": "link-" + slug(a, 40), "title": html_to_md(a), "page": html_to_md(b), "url": None,
                                       "note": "", "list": [], "group": group, "was": html_to_md(c), "now": html_to_md(d),
                                       "kind": "link", "shots_id": None, "shots": []})
        if sec_id == "behind":                    # the bullets are the changes here; "Left out on purpose" stays a list
            ul = re.search(r'<ul class="plain">(.*?)</ul>', chunk.split("<details>")[0], re.S)
            for li in re.findall(r"<li>(.*?)</li>", ul.group(1), re.S):
                lm = re.match(r"\s*<b>(.*?)</b>:?\s*(.*)", li, re.S)
                label, rest = (lm.group(1), lm.group(2)) if lm else (li, "")
                sec["changes"].append({"id": "behind-" + slug(label, 40), "title": html_to_md(label), "page": "", "url": None,
                                       "note": html_to_md(rest), "list": [], "kind": "item", "shots_id": None, "shots": []})
            dm = re.search(r"<details><summary>(.*?)</summary>(.*?)</details>", chunk, re.S)
            if dm:
                sec["details"] = {"title": html_to_md(dm.group(1)), "list": _list(dm.group(2))}
        sections.append(sec)
    pkg["sections"] = sections
    pkg["footer"] = html_to_md(re.search(r"<footer>(.*?)</footer>", s, re.S).group(1))
    pkg["unreviewed_pages"] = []
    stamp_identity(pkg)
    return pkg


# ------------------------------------------------------------------------------------- git / working copy
def git(*args, ok_fail=False):
    r = subprocess.run(["git", "-c", "core.safecrlf=false", "-c", "core.quotepath=false", *args], cwd=ROOT, capture_output=True,
                       creationflags=0x08000000 if sys.platform == "win32" else 0)
    if r.returncode and not ok_fail:
        raise RuntimeError("git " + " ".join(args) + ": " + r.stderr.decode("utf-8", "replace")[:300])
    return r.stdout.decode("utf-8", "replace")


def stamp_identity(pkg, base=None):
    """the package id follows the base it is pushed onto, so the answers to an old package never apply to a new one"""
    base = base or pkg.get("base") or "origin/main"
    # a fixed 7: git lengthens --short as the repo grows (Nov 11: 2,318 staged photos made it 8), which changed the id
    # and orphaned every answer
    sha = git("rev-parse", "--short=7", base, ok_fail=True).strip() or "unknown"
    pkg["base"], pkg["base_sha"] = base, sha
    pkg["head_sha"] = git("rev-parse", "--short", "HEAD", ok_fail=True).strip()
    pkg["id"] = "push-" + sha


SKIP_PREFIX = ("Drafts/", ".tmp/", ".claude/", ".vscode/", "archive/")


def changed_files(base):
    """{rel: status} for every file that differs from the base (M/A/D/R) plus every untracked file"""
    out_ = {}
    for line in git("diff", "--name-status", base).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out_[parts[-1]] = parts[0][0]
    for rel in git("ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if rel:
            out_[rel] = "?"
    return {k: v for k, v in out_.items() if not k.startswith(SKIP_PREFIX)}


def is_page(rel):
    """a page of the site: <page>.html or <country>/<page>.html, not the editor's own pages"""
    return (rel.endswith(".html") and rel.count("/") <= 1 and rel != "editor.html" and not rel.endswith(".new.html")
            and not rel.startswith(("tools/", "fonts/", "Images/", "assets/", "archive/")))


def group_of(rel):
    if rel.startswith("Images/") or rel == "favicon.ico":
        return "images"
    if is_page(rel):
        return "pages"
    if rel.endswith((".css",)) or rel in ("search-index.js", "search-index.json", "sitemap.xml", "robots.txt"):
        return "site"
    if rel.startswith(".github/"):
        return "workflows"
    if rel.startswith(("tools/city_maps/", "tools/itinerary_maps/")) and rel.endswith(".json"):
        return "map data"
    return "behind the scenes"


REF_RE = re.compile(r"""(?:src|href|srcset|content|url\()\s*=?\s*["']?([^"'()>]*?(?:Images/[^"'\s,()>]+|favicon\.ico))""")


def referenced_untracked(changed):
    """untracked files (photos, the favicon, a font) that a page in the working copy uses: the push has to add them.
    Paths may hold spaces, written raw inside url('...') or %20-encoded in a srcset ("Images/web/North Macedonia/..."),
    so each reference is read up to a quote or bracket, unquoted, and tried whole and cut back at each space (a srcset
    candidate ends at the space before its "576w"). Names can hold brackets too ("IMG_2682(1)-1x.jpg"), so a closing
    bracket is a cut point, not a stop. The site's root scripts and style sheets count as well (the search thumbnails
    are only named in search-index.js)."""
    from urllib.parse import unquote
    untracked = {r for r, st in changed.items() if st in ("?", "A")}     # A: staged by `stage`, still new to the base
    if not untracked:
        return []
    need = set()
    pages = ([p for p in ROOT.glob("*.html")] + [p for p in ROOT.glob("*/*.html") if not p.parent.name.startswith((".", "Drafts", "tools", "archive"))]
             + [p for p in ROOT.glob("*.js")] + [p for p in ROOT.glob("*.css")])
    for p in pages:
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        base = p.parent.relative_to(ROOT).as_posix()
        for m in re.finditer(r"""(?:\.\./)*((?:Images|fonts)/[^"'<>?#,\n\\]+|favicon\.ico)""", t):
            ref = unquote(html.unescape(m.group(1))).rstrip()
            cands = [ref] + [ref[:k] for k in range(len(ref) - 1, 0, -1) if ref[k] in " )"]
            for rel in cands:
                if rel in untracked:
                    need.add(rel)
                    break
                if base not in ("", ".") and f"{base}/{rel}" in untracked:
                    need.add(f"{base}/{rel}")
                    break
    return sorted(need)


def scan_shots(d=None):
    """{shots id: [tier]} from files named <id>-<width>-<before|after>.jpg in the shots folder"""
    d = Path(d or pkg_dir() / "shots")
    found = {}
    if not d.is_dir():
        return found
    try:
        from PIL import Image
    except Exception:
        Image = None
    for f in sorted(d.glob("*.jpg")):
        m = re.match(r"^(.+)-(\d{3,4})-(before|after)\.jpg$", f.name)
        if not m:
            continue
        sid, w, mode = m.group(1), int(m.group(2)), m.group(3)
        size = Image.open(f).size if Image else (0, 0)
        tier = found.setdefault(sid, {}).setdefault(w, {"kind": "desk" if w >= 1000 else ("tab" if w > 600 else "phone"), "w": w,
                                                         "label": ("Desktop " if w >= 1000 else "Tablet " if w > 600 else "Phone ") + str(w),
                                                         "before": None})
        tier[mode] = {"src": f"shots/{f.name}", "w": size[0], "h": size[1]}
    return {sid: [t for _w, t in sorted(ws.items(), key=lambda kv: -kv[0])] for sid, ws in found.items()}


def all_entries(pkg):
    for c in pkg.get("calls") or []:
        yield "call", c
    for sec in pkg.get("sections") or []:
        for c in sec.get("changes") or []:
            yield "change", c


def entry_ids(pkg):
    ids = {}
    for kind, e in all_entries(pkg):
        ids[e["id"]] = kind
    for u in pkg.get("unreviewed_pages") or []:
        ids[u["id"]] = "page"
    return ids


def rebuild(base=None):
    pkg, _v = load_package()
    if pkg is None:
        raise SystemExit("no package.json yet: start with import-html or merge a fragment")
    base = base or pkg.get("base") or "origin/main"
    stamp_identity(pkg, base)
    changed = changed_files(base)
    # shots: whatever is in the folder now (after a `shoot`), keeping hand-made tiers (the icons) that follow no name rule
    shots = scan_shots()
    for _k, e in all_entries(pkg):
        sid = e.get("shots_id")
        if sid and sid in shots:
            keep_none = {t.get("w"): t.get("before_note") for t in e.get("shots") or [] if t.get("before_note")}
            e["shots"] = shots[sid]
            for t in e["shots"]:
                if t.get("before") is None and keep_none.get(t["w"]):
                    t["before_note"] = keep_none[t["w"]]
                elif t.get("before") is None:
                    t["before_note"] = "Not on the live site yet"
    # inventory: what the diff holds, by kind
    inv = {}
    for rel, st in changed.items():
        g = group_of(rel)
        if g == "images" and st in ("?", "A"):
            continue                                    # untracked photos count only when a page uses them (below)
        inv.setdefault(g, []).append(rel)
    need = referenced_untracked(changed)
    # the push's own add list (search thumbnails, share pictures the scan can't see). add_list.txt is the one the push
    # uses once it exists (Nov 11: files_to_add.txt still named Kosovo's held-back page and share picture)
    listed = next((p for p in (pkg_dir() / "add_list.txt", pkg_dir() / "files_to_add.txt") if p.is_file()), None)
    if listed:
        need = sorted(set(need) | {r.strip() for r in listed.read_text(encoding="utf-8-sig").splitlines() if changed.get(r.strip()) in ("?", "A")})
    pkg["inventory"] ={g: {"count": len(v), "files": sorted(v)[:400]} for g, v in sorted(inv.items())}
    pkg["inventory"]["images to add"] = {"count": len(need), "files": need[:400]}
    # pages that changed and that no call or change names yet (e.g. a launch before Claude has written it up)
    # (a change that covers many pages, like "analytics tag moved on 19 pages", lists them in `pages`)
    described = {p.strip() for _k, e in all_entries(pkg) for p in (e.get("page") or "").split(",")}
    described |= {p for _k, e in all_entries(pkg) for p in e.get("pages") or []}
    old = {u["id"]: u for u in pkg.get("unreviewed_pages") or []}
    pkg["unreviewed_pages"] = [{"id": "page:" + rel, "page": rel, "status": {"M": "changed", "A": "new", "?": "new", "D": "deleted", "R": "renamed"}.get(st, st),
                                "note": (old.get("page:" + rel) or {}).get("note", "")}
                               for rel, st in sorted(changed.items()) if group_of(rel) == "pages" and rel not in described]
    reports = sorted((ROOT / ".tmp").glob("live_fixes_*.md")) + sorted((ROOT / ".tmp").glob("*fix*report*.md"))
    base_time = git("log", "-1", "--format=%ct", base, ok_fail=True).strip()
    pkg["reports"] = [str(p.relative_to(ROOT)).replace("\\", "/") for p in reports if not base_time or p.stat().st_mtime >= int(base_time)]
    pkg["auto"] = {"rebuilt": now_iso(), "base": base, "base_sha": pkg["base_sha"], "head_sha": pkg["head_sha"],
                   "commits_ahead": int((git("rev-list", "--count", f"{base}..HEAD", ok_fail=True).strip() or "0")),
                   "artifact_css_changed": "artifact.css" in changed, "images_to_add": len(need),
                   "pages_changed": len(inv.get("pages", [])), "files_changed": len(changed) - sum(1 for r, s in changed.items() if s in ("?", "A") and group_of(r) == "images")}
    pkg["built"] = now_iso()
    write_json_atomic(package_path(), pkg)
    return pkg


def merge_fragment(path):
    """a fragment {calls?, sections?, summary?, checks?, push_steps?, verdict?, ...}: calls and changes replace by id or
    are appended; a section is matched by id (its title/intro replaced when given); other keys replace"""
    frag = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    pkg, _v = load_package()
    if pkg is None:
        pkg = {"format": 1, "title": "Push package", "calls": [], "sections": [], "summary": [], "checks": [], "push_steps": [],
               "unreviewed_pages": [], "shots_dir": "shots", "built": now_iso()}
        stamp_identity(pkg)
    for c in frag.get("calls") or []:
        c.setdefault("shots", []); c.setdefault("diff", []); c.setdefault("list", []); c.setdefault("note", "")
        c.setdefault("shots_id", c["id"] if c.get("shoot") else None)
        if not c.get("num"):
            c["num"] = max([x.get("num", 0) for x in pkg["calls"]] + [0]) + 1
        i = next((k for k, x in enumerate(pkg["calls"]) if x["id"] == c["id"]), None)
        if i is None:
            pkg["calls"].append(c)
        else:
            pkg["calls"][i] = c
    for s in frag.get("sections") or []:
        sec = next((x for x in pkg["sections"] if x["id"] == s["id"]), None)
        if sec is None:
            sec = {"id": s["id"], "title": s.get("title") or s["id"], "intro": s.get("intro", ""), "changes": []}
            pkg["sections"].insert(s["position"], sec) if isinstance(s.get("position"), int) else pkg["sections"].append(sec)
        for k in ("title", "intro", "details"):
            if k in s:
                sec[k] = s[k]
        for c in s.get("changes") or []:
            c.setdefault("shots", []); c.setdefault("list", []); c.setdefault("note", ""); c.setdefault("page", "")
            c.setdefault("shots_id", c["id"] if c.get("shoot") else None)
            i = next((k for k, x in enumerate(sec["changes"]) if x["id"] == c["id"]), None)
            if i is None:
                sec["changes"].append(c)
            else:
                sec["changes"][i] = c
    for k, v in frag.items():
        if k not in ("calls", "sections"):
            pkg[k] = v
    problems = validate(pkg)
    if problems:
        raise SystemExit("the merged package has problems, nothing written:\n  " + "\n  ".join(problems))
    pkg["built"] = now_iso()
    write_json_atomic(package_path(), pkg)
    return pkg


def validate(pkg):
    problems = []
    seen = set()
    for kind, e in all_entries(pkg):
        if e["id"] in seen:
            problems.append(f"duplicate id {e['id']!r}")
        seen.add(e["id"])
        if not e.get("title"):
            problems.append(f"{e['id']}: no title")
        if kind == "call":
            recs = [o for o in e.get("options") or [] if o.get("recommended")]
            if not e.get("options"):
                problems.append(f"{e['id']}: no options")
            elif len(recs) != 1:
                problems.append(f"{e['id']}: {len(recs)} recommended options (want exactly 1)")
            if len({o["id"] for o in e.get("options") or []}) != len(e.get("options") or []):
                problems.append(f"{e['id']}: duplicate option ids")
        for t in e.get("shots") or []:
            for mode in ("before", "after"):
                if t.get(mode) and not (pkg_dir() / t[mode]["src"]).is_file():
                    problems.append(f"{e['id']}: missing {t[mode]['src']}")
            if not t.get("after"):
                problems.append(f"{e['id']}: a {t.get('label')} tier with no after shot")
    return problems


# ------------------------------------------------------------------------------------- shoot (before = git base, after = working copy)
FIND = r"""
([spec, mode, remembered]) => {
  const absr = el => { const r = el.getBoundingClientRect(); return {x: r.left, y: r.top + scrollY, w: r.width, h: r.height}; };
  const smallest = (sel, text) => { let best = null;
    for (const el of document.querySelectorAll(sel)) { const t = el.textContent.replace(/\s+/g,' ');
      if (t.includes(text) && (!best || t.length < best.textContent.replace(/\s+/g,' ').length)) best = el; }
    return best; };
  const k = spec.kind;
  if (k === 'el') { const el = smallest(spec.sel, spec.text); if (!el) return null; const r = absr(el);
    return {x: r.x - 34, y: r.y - 12, w: r.w + 50, h: r.h + 24}; }
  if (k === 'sel') { const el = document.querySelector(spec.sel); if (!el) return null; const r = absr(el);
    return {x: r.x, y: Math.max(0, r.y - (spec.above||0)), w: r.w, h: r.h + (spec.below||0) + (r.y - Math.max(0, r.y - (spec.above||0)))}; }
  if (k === 'range') { const a = document.querySelector(spec.sel), b = document.querySelector(spec.end); if (!a || !b) return null;
    const ra = absr(a), rb = absr(b); return {x: ra.x, y: ra.y - 8, w: ra.w, h: rb.y - ra.y + 60}; }
  if (k === 'cap') { const el = document.querySelectorAll(spec.sel)[spec.idx]; if (!el) return null;
    const prev = el.previousElementSibling, rp = absr(prev), rc = absr(el); const top = Math.max(rp.y, rp.y + rp.h - 200);
    const nxt = el.nextElementSibling; const bottom = nxt ? Math.min(absr(nxt).y + 60, rc.y + rc.h + 90) : rc.y + rc.h + 40;
    return {x: Math.min(rp.x, rc.x) - 8, y: top, w: Math.max(rp.w, rc.w) + 16, h: bottom - top}; }
  if (k === 'top') { return {x: 0, y: 0, w: innerWidth, h: spec.h}; }
  if (k === 'map') {
    const col = document.querySelector('.article-body, .artbody, .fn-section'); const rc = absr(col);
    const key = el => { const t = el.tagName.toLowerCase(), c = el.classList[0]; const s = c ? t + '.' + CSS.escape(c) : t;
      return {s, i: [...document.querySelectorAll(s)].indexOf(el)}; };
    if (mode === 'after' || !remembered) {
      const fig = document.querySelector(spec.map); if (!fig) return null;
      const wrap = fig.closest('.route-map') || fig; const rw = absr(wrap);
      const prev = wrap.previousElementSibling; const rp = prev ? absr(prev) : rw;
      const top = prev ? Math.max(rp.y + rp.h - 90, rp.y) : rw.y - 60;
      return {x: rc.x, y: top, w: rc.w, h: rw.y + rw.h - top + 10, prev: prev ? key(prev) : null};
    }
    if (!remembered.prev) return null;
    const prev = document.querySelectorAll(remembered.prev.s)[remembered.prev.i]; if (!prev) return null;
    const rp = absr(prev); const top = Math.max(rp.y + rp.h - 90, rp.y);
    return {x: rc.x, y: top, w: rc.w, h: Math.min(remembered.h, 420)};
  }
  return null;
}
"""


def shoot(ids=None, missing=False, quality=68):
    """crop each change's spot before (the base, from git) and after (the working copy) at its widths"""
    import mimetypes
    import posixpath
    from urllib.parse import unquote, urlparse
    from playwright.sync_api import sync_playwright
    pkg, _v = load_package()
    if pkg is None:
        raise SystemExit("no package.json")
    base = pkg.get("base") or "origin/main"
    changed = changed_files(base)
    specs = []
    for _k, e in all_entries(pkg):
        sp = e.get("shoot")
        if not sp:
            continue
        sp = dict(sp, id=e.get("shots_id") or e["id"])
        if ids and e["id"] not in ids and sp["id"] not in ids:
            continue
        if missing and e.get("shots"):
            continue
        specs.append(sp)
    outd = pkg_dir() / "shots"
    outd.mkdir(parents=True, exist_ok=True)
    cache = {}

    def base_bytes(rel):
        if rel not in cache:
            r = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=ROOT, capture_output=True)
            cache[rel] = r.stdout if r.returncode == 0 else None
        return cache[rel]

    def handler(route):
        u = urlparse(route.request.url)
        mode = {"before.test": "before", "after.test": "after"}.get(u.hostname)
        if not mode:
            return route.abort()
        rel = posixpath.normpath(unquote(u.path).lstrip("/")) if u.path.strip("/") else "index.html"
        full = ROOT / rel
        if full.is_dir():
            rel = posixpath.join(rel, "index.html"); full = ROOT / rel
        if mode == "before" and changed.get(rel) == "?":
            return route.fulfill(status=404, body="")
        if mode == "before" and rel in changed:
            data = base_bytes(rel)
            if data is None:
                return route.fulfill(status=404, body="")
        elif full.is_file():
            data = full.read_bytes()
        else:
            return route.fulfill(status=404, body="")
        route.fulfill(status=200, body=data, headers={"Content-Type": mimetypes.guess_type(rel)[0] or "application/octet-stream"})

    WAIT = """() => Promise.all([...document.images].filter(i => { const r = i.getBoundingClientRect();
        return !i.complete && r.bottom > -50 && r.top < innerHeight + 50; }).map(i => new Promise(r => { i.onload = i.onerror = r; setTimeout(r, 3000); })))"""
    made = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pages, remember = {}, {}

        def page_for(mode, w):
            if (mode, w) not in pages:
                ctx = br.new_context(viewport={"width": w, "height": 1000}, device_scale_factor=1)
                ctx.route("**/*", handler)
                pages[(mode, w)] = ctx.new_page()
            return pages[(mode, w)]
        def grab(spec, mode, w, path):
            """crop one spot; -> (png bytes | None when written to `path`, width, height) or None when not found"""
            pg = page_for(mode, w)
            pg.set_viewport_size({"width": w, "height": 1000})
            pg.goto(f"http://{mode}.test/{spec['page']}", wait_until="domcontentloaded", timeout=60000)
            pg.evaluate("document.fonts.ready.then(() => 1)")
            pg.wait_for_timeout(400)
            if spec.get("mark"):
                pg.evaluate(MARK, spec["mark"])
            rem = remember.get((spec["id"], w))
            box = pg.evaluate(FIND, [spec, mode, rem])
            if not box:
                return None
            vh = max(1000, int(box["h"]) + 260)
            pg.set_viewport_size({"width": w, "height": vh})
            pg.wait_for_timeout(150)
            for _ in range(2):
                box = pg.evaluate(FIND, [spec, mode, rem]) or box
                pg.evaluate(f"window.scrollTo(0, {0 if box['y'] < 140 else box['y'] - 130})")
                pg.wait_for_timeout(350)
                pg.evaluate(WAIT)
                pg.wait_for_timeout(150)
            box = pg.evaluate(FIND, [spec, mode, rem]) or box
            if spec["kind"] == "map" and mode == "after":
                remember[(spec["id"], w)] = box
            sy = pg.evaluate("scrollY")
            x = max(0, box["x"]); y = max(0, box["y"] - sy)
            cw = min(w - x, box["w"] + (box["x"] - x)); ch = min(vh - y, box["h"])
            clip = {"x": x, "y": y, "width": cw, "height": ch}
            if path:
                pg.screenshot(path=str(path), type="jpeg", quality=quality, clip=clip)
                return None, cw, ch
            return pg.screenshot(type="png", clip=clip), cw, ch

        for spec0 in specs:
            for w in spec0.get("widths", [D, P]):
                spec = dict(spec0, h=spec0["h"].get(str(w)) or spec0["h"].get(w)) if isinstance(spec0.get("h"), dict) else spec0
                for mode in (["after", "before"] if spec["kind"] == "map" else ["before", "after"]):
                    if mode not in spec.get("modes", ["before", "after"]):
                        continue
                    fn = f"{spec['id']}-{w}-{mode}.jpg"
                    if spec["kind"] == "stack":
                        # several spots on one page (Nov 11: "shots of two spots per page" for the restored map pins),
                        # each cropped as its own spec and stacked into one picture with a thin band between them
                        parts = []
                        for k, part in enumerate(spec["parts"]):
                            part = dict(part, id=f"{spec['id']}#{k}", page=part.get("page") or spec["page"],
                                        mark=part.get("mark", spec.get("mark")))
                            got = grab(part, mode, w, None)
                            if got:
                                parts.append(got)
                            else:
                                out(f"NOT FOUND {spec['id']} part {k} {w} {mode}")
                        if not parts:
                            continue
                        stitch(parts, outd / fn, quality)
                        made.append(fn)
                        out(f"{fn} {len(parts)} spots")
                        continue
                    got = grab(spec, mode, w, outd / fn)
                    if not got:
                        out(f"NOT FOUND {spec['id']} {w} {mode}")
                        continue
                    made.append(fn)
                    out(f"{fn} {round(got[1])}x{round(got[2])}")
        br.close()
    return made


# a restored link looks exactly like the one it replaced, so a shoot spec may name link texts to outline in both shots
MARK = r"""
(texts) => { for (const a of document.querySelectorAll('a')) {
  if (texts.includes(a.textContent.replace(/\s+/g, ' ').trim())) {
    a.style.outline = '2px solid #e8a000'; a.style.outlineOffset = '2px'; a.style.borderRadius = '2px'; } } }
"""


def stitch(parts, path, quality=68, gap=14):
    """crops [(png bytes, w, h)] -> one JPEG, top to bottom, left-aligned on the first crop's background, a hairline
    between them"""
    import io
    from PIL import Image, ImageDraw
    ims = [Image.open(io.BytesIO(b)).convert("RGB") for b, _w, _h in parts]
    W = max(i.width for i in ims)
    H = sum(i.height for i in ims) + gap * (len(ims) - 1)
    bg = ims[0].getpixel((ims[0].width - 1, 0))
    canvas = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(canvas)
    y = 0
    for k, im in enumerate(ims):
        canvas.paste(im, (0, y))
        y += im.height
        if k < len(ims) - 1:
            d.line([(0, y + gap // 2), (W, y + gap // 2)], fill=(200, 196, 188), width=1)
            y += gap
    canvas.save(path, "JPEG", quality=quality)


# ------------------------------------------------------------------------------------- answers
def empty_answers(pkg_id):
    return {"package": pkg_id, "rev": 0, "updated": None, "answers": {}}


def load_answers(pkg_id):
    """the answers for this package; answers left from another package are not this one's (None = archive them first)"""
    p = answers_path()
    if not p.is_file() or p.stat().st_size == 0:
        return empty_answers(pkg_id), None
    try:
        obj = json.loads(p.read_bytes().decode("utf-8-sig"))
    except ValueError as e:                             # half-written or damaged: say so (423), never read it as empty
        raise RuntimeError(f"{p.name} is unreadable; refusing to overwrite it ({e})")
    if not isinstance(obj, dict) or not isinstance(obj.get("answers"), dict):
        raise RuntimeError(f"{p.name} is not an answers store; refusing to overwrite it")
    if obj.get("package") != pkg_id:
        return empty_answers(pkg_id), obj
    obj.setdefault("rev", 0)
    return obj, None


def _clean(v, n=2000):
    return str(v).replace("\r", " ").strip()[:n] if v is not None else ""


def apply_answer(store, pkg, body, via=None, when=None):
    """-> (error | None, conflicts []). Nothing is changed unless every named field passes."""
    ids = entry_ids(pkg)
    iid = body.get("id")
    kind = ids.get(iid) if isinstance(iid, str) else None
    if not kind:
        return f"no call or change {iid!r} in this package", []
    sets = body.get("set")
    if not isinstance(sets, dict) or not sets:
        return "nothing to set", []
    seen = body.get("seen") if isinstance(body.get("seen"), dict) else {}
    entry = store["answers"].get(iid) or {}
    revs = entry.get("rev") or {}
    clean = {}
    for f, v in sets.items():
        if f not in FIELDS:
            return f"unknown field {f!r}", []
        if f in ("choice", "reply") and kind != "call":
            return f"{f} is for calls", []
        if f in ("hold", "ok") and kind == "call":
            return f"{f} is for changes", []
        if f == "choice":
            opts = {o["id"]: o for _k, e in all_entries(pkg) if e["id"] == iid for o in e.get("options") or []}
            if v not in (None, "") and (not isinstance(v, str) or v not in opts):
                return f"{iid} has no option {v!r}", []
            clean[f] = v or None
        elif f in ("reply", "note"):
            clean[f] = _clean(v) or None
        else:
            clean[f] = bool(v)
    conflicts = [f for f in clean if f != "seen" and revs.get(f) is not None and seen.get(f) != revs.get(f)]
    if conflicts:
        return None, conflicts
    if "seen" in clean and (entry.get("seen") or not clean["seen"]):
        clean.pop("seen")                               # set once, never unset
    if not clean:
        return None, []
    store["rev"] = int(store.get("rev") or 0) + 1
    t = when or now_iso()
    entry = dict(entry)
    entry["kind"] = kind
    entry.setdefault("rev", {}); entry.setdefault("times", {})
    for f, v in clean.items():
        entry[f] = v
        entry["rev"][f] = store["rev"]
        entry["times"][f] = t
        if f == "choice":
            opt = next((o for _k, e in all_entries(pkg) if e["id"] == iid for o in e.get("options") or [] if o["id"] == v), None)
            entry["choice_label"] = opt["label"] if opt else None
            entry["recommended"] = bool(opt and opt.get("recommended"))
    if via:
        entry["via"] = via
    entry["at"] = t
    store["answers"][iid] = entry
    store["updated"] = now_iso()
    return None, []


def save_answers(store, old=None):
    if old is not None:                                 # answers to an older package: keep them beside, never lose them
        write_json_atomic(pkg_dir() / f"answers-{old.get('package') or 'old'}.json", old)
    write_json_atomic(answers_path(), store)


def answered(call, a):
    return bool(a and (a.get("choice") or a.get("reply")))


def answers_report(pkg, store):
    A = store["answers"]
    lines = [f"Push package {pkg['id']} (built {pkg.get('built', '?')}); answers updated {store.get('updated') or 'never'}"]
    calls = pkg.get("calls") or []
    lines.append(f"\nCalls: {sum(1 for c in calls if answered(c, A.get(c['id'])))} of {len(calls)} answered")
    for c in calls:
        a = A.get(c["id"]) or {}
        rec = next((o for o in c.get("options") or [] if o.get("recommended")), None)
        if a.get("choice"):
            pick = f"{a.get('choice_label')}" + (" (recommended)" if a.get("recommended") else "  << NOT the recommended option")
        else:
            pick = f"no pick -> goes as recommended: {rec['label'] if rec else '?'}"
        lines.append(f"  {c.get('num')}. {md_text(c['title'])}\n     {pick}")
        if a.get("reply"):
            lines.append(f"     reply: \"{a['reply']}\"" + (f" ({a['via']}, {a['times'].get('reply', '')})" if a.get("via") else f" ({a['times'].get('reply', '')})"))
        if a.get("note"):
            lines.append(f"     note: \"{a['note']}\"")
    held, notes, ok, total = [], [], 0, 0
    for sec in pkg.get("sections") or []:
        for e in sec.get("changes") or []:
            total += 1
            a = A.get(e["id"]) or {}
            ok += 1 if a.get("ok") else 0
            if a.get("hold"):
                held.append(f"  HOLD  [{sec['title']}] {md_text(e['title'])} ({e.get('page') or ''})" + (f"\n        note: \"{a['note']}\"" if a.get("note") else ""))
            elif a.get("note"):
                notes.append(f"  note  [{sec['title']}] {md_text(e['title'])}: \"{a['note']}\"")
    for u in pkg.get("unreviewed_pages") or []:
        a = A.get(u["id"]) or {}
        if a.get("hold"):
            held.append(f"  HOLD  [not yet described] {u['page']}" + (f"\n        note: \"{a['note']}\"" if a.get("note") else ""))
        elif a.get("note"):
            notes.append(f"  note  [not yet described] {u['page']}: \"{a['note']}\"")
    lines.append(f"\nHeld back ({len(held)}):" + ("" if held else " none"))
    lines += held
    lines.append(f"\nNotes on changes ({len(notes)}):" + ("" if notes else " none"))
    lines += notes
    lines.append(f"\nMarked looks good: {ok} of {total} changes")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------- settled (Kevin, 2026-11-11)
# "The changes should address themselves as I work through ... I want to address the changes and clear the list before
# I feel comfortable pushing. Maybe we can create a 'addressed' section." Every item is one of:
#   open      nothing from him yet (a call with no pick or reply; a change not marked good, held or noted)
#   working   he answered and the answer needs doing: a reply, a pick other than the recommended one, a note, a hold
#   settled   carried out (done.json says so, at or after his latest answer), or nothing to do (the recommended pick,
#             "looks good")
# done.json is written only by Claude (`push_package.py done <id> "what was done"`), never by the editor. A newer answer
# on the same item reopens it (its revision is past the one done.json recorded).
# reopened.json (2026-11-11) is the one file the editor writes here: when the Queue tab's held-back drawer puts a held item
# back on its page (tools/queue_status.py), it records {text, rev, at} for that item, and the item is open again (it waits
# on him) until he answers anew or Claude marks it done later. His answers.json is never touched by it.
WORK_FIELDS = {"call": ("choice", "reply"), "change": ("note", "hold"), "page": ("note", "hold")}


def done_path():
    return pkg_dir() / "done.json"


def checks_path():
    return pkg_dir() / "checks.json"


def load_done(pkg_id):
    p = done_path()
    if p.is_file() and p.stat().st_size:
        try:
            obj = json.loads(p.read_bytes().decode("utf-8-sig"))
        except ValueError:                              # a damaged log must not take the whole section down
            obj = None
        if isinstance(obj, dict) and obj.get("package") == pkg_id and isinstance(obj.get("items"), dict):
            return obj
    return {"package": pkg_id, "items": {}}


def _entries_with_kind(pkg):
    for kind, e in all_entries(pkg):
        yield kind, e
    for u in pkg.get("unreviewed_pages") or []:
        yield "page", {"id": u["id"], "title": u["page"], "page": u["page"]}


def work_rev(kind, a):
    rev = (a or {}).get("rev") or {}
    return max([int(rev.get(f) or 0) for f in WORK_FIELDS[kind]] + [0])


def item_status(kind, e, a, d):
    """-> {state, text, answer}: text = what was done (settled) or what is waiting (working); answer = his answer, short"""
    a, d = a or {}, d or None
    # Reopened (2026-11-11): the Queue tab's held-back drawer put a held item back on its page, so his answer ("hold it
    # back", "delete map") no longer matches the working copy. reopened.json says so (statuses() passes it in as d with `reopened`); the item waits on HIM
    # again until he answers anew (his next answer's revision passes the recorded one, and the normal rules take over).
    if d and d.get("reopened"):
        if int(d.get("rev") or 0) >= work_rev(kind, a):
            return {"state": "open", "text": d.get("text") or "Put back on the page; answer again.", "answer": "", "reopened": True, "at": d.get("at")}
        d = None
    if kind == "call":
        opts = e.get("options") or []
        opt = next((o for o in opts if o["id"] == a.get("choice")), None)
        answer = " · ".join(x for x in [opt["label"] if opt else "", f"“{a['reply']}”" if a.get("reply") else ""] if x)
        # no pick and no reply: open, unless Claude's log has it (stress test 2026-11-11). He answered in the session and
        # Claude marked it done -> settled; he picked, Claude acted, then he cleared the pick -> Claude undoes it (working),
        # the same rule a change has ("he took it back").
        if not (a.get("choice") or a.get("reply")) and not d:
            return {"state": "open", "text": "", "answer": ""}
        needs = bool(a.get("reply")) or bool(opt and opt.get("work", not opt.get("recommended")))
        auto = f"Goes as “{opt['label']}”." if opt else "Goes as described."
    else:
        answer = " · ".join(x for x in ["Held back" if a.get("hold") else "", "Looks good" if a.get("ok") else "",
                                        f"“{a['note']}”" if a.get("note") else ""] if x)
        if not (a.get("ok") or a.get("hold") or a.get("note")) and not d:
            return {"state": "open", "text": "", "answer": ""}
        needs = bool(a.get("note") or a.get("hold"))
        auto = "Looks good."
    wr = work_rev(kind, a)
    if d and int(d.get("rev") or 0) >= wr:
        return {"state": "settled", "text": d.get("text") or auto, "answer": answer, "at": d.get("at")}
    if needs or d:                                      # d older than his answer: he changed it after Claude acted
        return {"state": "working", "text": "Claude is on it.", "answer": answer}
    if kind != "call" and not a.get("ok"):
        return {"state": "open", "text": "", "answer": answer}
    return {"state": "settled", "text": auto, "answer": answer}


def reopened_path():
    return pkg_dir() / "reopened.json"


def load_reopened(pkg_id):
    p = reopened_path()
    if p.is_file() and p.stat().st_size:
        try:
            obj = json.loads(p.read_bytes().decode("utf-8-sig"))
        except Exception:
            obj = None
        if isinstance(obj, dict) and obj.get("package") == pkg_id and isinstance(obj.get("items"), dict):
            return obj
    return {"package": pkg_id, "items": {}}


def mark_reopened(iid, text, pkg=None):
    """the held-back drawer put this item back on its page: it waits on Kevin again (see item_status)"""
    pkg = pkg or load_package()[0]
    kinds = {e["id"]: k for k, e in _entries_with_kind(pkg)}
    if iid not in kinds:
        return None
    with FileLock(reopened_path()):
        store, _old = load_answers(pkg["id"])
        ro = load_reopened(pkg["id"])
        ro["items"][iid] = {"text": _clean(text, 400), "rev": work_rev(kinds[iid], store["answers"].get(iid)), "at": now_iso()}
        write_json_atomic(reopened_path(), ro)
    return ro["items"][iid]


def statuses(pkg, store, done):
    A, D = (store or {}).get("answers") or {}, (done or {}).get("items") or {}
    R = load_reopened(pkg.get("id"))["items"] if pkg else {}

    def log_for(iid):                                   # the newer of Claude's done entry and a drawer's reopen
        d, r = D.get(iid), R.get(iid)
        if r and (not d or str(r.get("at") or "") > str(d.get("at") or "")):
            return dict(r, reopened=True)
        return d
    return {e["id"]: item_status(kind, e, A.get(e["id"]), log_for(e["id"])) for kind, e in _entries_with_kind(pkg)}


def mark_done(iid, text, pkg=None):
    pkg = pkg or load_package()[0]
    kinds = {e["id"]: k for k, e in _entries_with_kind(pkg)}
    if iid not in kinds:
        raise SystemExit(f"no item {iid!r} in the package")
    with FileLock(done_path()):
        store, _old = load_answers(pkg["id"])
        done = load_done(pkg["id"])
        done["items"][iid] = {"text": _clean(text, 400), "rev": work_rev(kinds[iid], store["answers"].get(iid)), "at": now_iso()}
        write_json_atomic(done_path(), done)
    return done["items"][iid]


# ------------------------------------------------------------------------------------- checks (all pre-push, all live)
# Kevin, 2026-11-11: "all checks should be pre-push checks and turn green before we push". `checks` runs them and writes
# checks.json; the route adds the one only a running server can answer (is the editor on today's code?) and marks a
# result stale when the files it covered changed after it ran. The site check stages the add list first (git add, never
# a commit), so it checks the files the push will really carry.
SUITES = [
    ("tests-editor", "Editor tests", ["test_queue_tab.py", "test_queue_push.py", "test_queue_status.py", "test_queue_rounds_by_country.py","test_comment_tick.py",
                                      "test_comment_margin.py", "test_editor_auto_caption.py", "test_editor_currency_block.py"]),
    ("tests-site", "Site tool tests", ["test_links.py", "test_affiliate_links.py", "test_currency_widget.py", "test_launch_steps.py",
                                       "test_crosslink.py", "test_updated_line.py"]),
    ("tests-review", "Review tool tests", ["test_country_check.py", "test_draft_check.py", "test_shadow_consent.py", "test_shadow_learning.py"]),
]
STALE_GLOBS = {"site": ["*.html", "*/*.html", "*.css", "*.js", ".tmp/push_package/add_list.txt"],
               "tests-editor": ["tools/*.py", "*.js", "editor.html"], "tests-site": ["tools/*.py"], "tests-review": ["tools/*.py"]}


def add_list():
    p = next((q for q in (pkg_dir() / "add_list.txt", pkg_dir() / "files_to_add.txt") if q.is_file()), None)
    return [r.strip() for r in p.read_text(encoding="utf-8-sig").splitlines() if r.strip()] if p else []


def stage_add_list():
    """git add the add list (index only; the push commits it). -> (added, missing on disk, staged but not listed)"""
    want = add_list()
    indexed = set(git("ls-files", "-z").split("\0"))
    todo = [r for r in want if r not in indexed and (ROOT / r).is_file()]
    missing = [r for r in want if not (ROOT / r).is_file()]
    for i in range(0, len(todo), 200):
        for attempt in range(3):                        # "unable to index file" now and then (Nov 11: a file in use); a retry adds it
            try:
                git("add", "--", *todo[i:i + 200])
                break
            except RuntimeError:
                if attempt == 2:
                    raise
                time.sleep(2)
    new_in_index = {l.strip() for l in git("diff", "--cached", "--name-only", "--diff-filter=A", "HEAD").splitlines() if l.strip()}
    extra = sorted(r for r in new_in_index if r not in set(want) and not r.startswith(SKIP_PREFIX))
    return todo, missing, extra


def _run(cmd, timeout=900):
    t = time.time()
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=timeout, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                           creationflags=0x08000000 if sys.platform == "win32" else 0)
        text = (r.stdout + b"\n" + r.stderr).decode("utf-8", "replace")
        return r.returncode, text, time.time() - t
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s", time.time() - t


def _last_line(text):
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith(("warning:", "Traceback"))]
    return lines[-1][:160] if lines else ""


def run_checks(only=None):
    res = {"at": now_iso(), "t": time.time(), "items": []}
    old = {}
    if checks_path().is_file():
        try:
            old = {c["id"]: c for c in json.loads(checks_path().read_text(encoding="utf-8-sig")).get("items", [])}
        except Exception:
            old = {}
    want = lambda cid: not only or cid in only
    if want("site"):
        added, missing, extra = stage_add_list()
        code, text, _s = _run([sys.executable, "tools/lint_site.py", "--quiet"])
        m = re.search(r"(\d+) issue\(s\) across (\d+) live pages", text)
        if code == 0 and not missing:
            t = f"**Site check**: clean across {re.search(r'(\d+) live pages', text).group(1) if re.search(r'(\d+) live pages', text) else 'all'} pages, with the {len(add_list())} new files staged for the push."
        else:
            t = f"**Site check**: {m.group(1) + ' problems' if m else 'failed'}" + (f"; {len(missing)} listed files are missing on disk" if missing else "") + ". `python tools/lint_site.py` shows them."
        if extra:
            t += f" ({len(extra)} new files are staged but not in the add list.)"
        res["items"].append({"id": "site", "label": "Site check", "ok": code == 0 and not missing, "text": t, "t": time.time()})
    elif "site" in old:
        res["items"].append(old["site"])
    if want("pull"):
        f_code, f_text, _s = _run(["git", "fetch", "--quiet", "origin"], timeout=60)
        if f_code:                                      # Nov 11: one failed fetch, the next one fine
            time.sleep(3)
            f_code, f_text, _s = _run(["git", "fetch", "--quiet", "origin"], timeout=60)
        if f_code:
            res["items"].append({"id": "pull", "label": "GitHub", "ok": False, "t": time.time(),
                                 "text": "**GitHub**: couldn't reach it to see whether there is anything to pull first."})
        else:
            n = int(git("rev-list", "--count", "HEAD..origin/main", ok_fail=True).strip() or 0)
            res["items"].append({"id": "pull", "label": "GitHub", "ok": n == 0, "t": time.time(),
                                 "text": "**GitHub**: nothing to pull first." if n == 0 else f"**GitHub**: {n} commit{'s' if n != 1 else ''} to pull first (the push does `git pull --rebase`)."})
    elif "pull" in old:
        res["items"].append(old["pull"])
    for cid, label, files in SUITES:
        if not want(cid):
            if cid in old:
                res["items"].append(old[cid])
            continue
        bad, n = [], 0
        for f in files:
            if not (ROOT / "tools" / f).is_file():
                continue
            n += 1
            code, text, _s = _run([sys.executable, f"tools/{f}"])
            if code:
                bad.append(f"{f[5:-3].replace('_', ' ')} ({_last_line(text)})")
        res["items"].append({"id": cid, "label": label, "ok": not bad, "t": time.time(),
                             "text": f"**{label}**: {n - len(bad)} of {n} suites pass." + (" Failing: " + "; ".join(bad) if bad else "")})
    write_json_atomic(checks_path(), res)
    return res


def _newest(globs):
    m = 0.0
    for g in globs:
        for p in ROOT.glob(g):
            if p.parent.name.startswith(".") and not g.startswith(".tmp"):
                continue
            if any(part in ("Drafts", "archive", "node_modules") for part in p.relative_to(ROOT).parts[:1]):
                continue
            try:
                m = max(m, p.stat().st_mtime)
            except OSError:
                pass
    return m


_auto = {"last": 0.0, "proc": None}


def checks_running():
    p = _auto.get("proc")
    return bool(p and p.poll() is None)


def start_checks(only=None):
    """run the checks in a child process (the server never blocks on them)"""
    if checks_running() or os.environ.get("QUEUE_TAB_DIR") or os.environ.get("PUSH_PACKAGE_DIR"):
        return False                                    # a test's scratch package never stages or lints the real repo
    cmd =[sys.executable, str(ROOT / "tools" / "push_package.py"), "checks"] + (["--only", ",".join(only)] if only else [])
    _auto["proc"] = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=0x08000000 if sys.platform == "win32" else 0)
    _auto["last"] = time.time()
    return True


def editor_check(app_info):
    """is this server on the code that's on disk? (a re-click of the Editor Suite shortcut restarts it when it isn't)"""
    if not app_info:
        return None
    try:
        import build_id
        disk = build_id.fingerprint(ROOT / app_info["entry"])["build"]
    except Exception as e:
        return {"id": "editor", "label": "Editor", "ok": False, "text": f"**Editor**: couldn't compare its code ({e})."}
    if disk == app_info["build"]:
        return {"id": "editor", "label": "Editor", "ok": True, "text": "**Editor**: running today's code."}
    return {"id": "editor", "label": "Editor", "ok": False,
            "text": "**Editor**: running older code. Re-click the Editor Suite shortcut; this turns green by itself once it restarts."}


def live_checks(app_info=None, auto=True):
    """checks.json with stale results marked, plus the editor check. Stale site/GitHub results re-run on their own
    (at most every 5 minutes); stale test results wait for the Run button or Claude."""
    items, ran = [], None
    if checks_path().is_file():
        try:
            obj = json.loads(checks_path().read_text(encoding="utf-8-sig"))
            items, ran = obj.get("items", []), obj.get("at")
        except Exception:
            items = []
    out_ = []
    stale_quick = []
    for c in items:
        if not isinstance(c, dict) or not c.get("id"):  # a damaged entry is dropped; its check then reads "not run yet"
            continue
        c = dict(c)
        c.setdefault("text", f"**{c.get('label') or c['id']}**")
        globs = STALE_GLOBS.get(c["id"])
        if globs and _newest(globs) > float(c.get("t") or 0) + 1:
            c["stale"] = True
            if c["id"] in ("site", "pull"):
                stale_quick.append(c["id"])
        out_.append(c)
    have = {c["id"] for c in out_}
    for cid, label in [("site", "Site check"), ("pull", "GitHub")] + [(s[0], s[1]) for s in SUITES]:
        if cid not in have:
            out_.append({"id": cid, "label": label, "ok": False, "stale": True, "text": f"**{label}**: not run yet."})
            if cid in ("site", "pull"):
                stale_quick.append(cid)
    if auto and stale_quick and not checks_running() and time.time() - _auto["last"] > 300:
        start_checks(stale_quick)
    ed = editor_check(app_info)
    if ed:
        out_.insert(0, ed)
    return {"items": out_, "ran": ran, "running": checks_running()}


def import_todo(items, pkg=None, store=None):
    """the old "push" inbox items (to_queue.py, Nov 10) -> answers: push-call-N -> call-N, push-<id> -> <id>.
    done call = the option his reply names, else the recommended one; his reply kept as his words; done change = ok;
    seen = seen; needs-changes = hold. A field the editor already set is never overwritten. -> list of what was set"""
    if pkg is None:
        pkg, _v = load_package()
    calls = {c["id"]: c for c in pkg.get("calls") or []}
    ids = entry_ids(pkg)
    did = []
    for it in items:
        if it.get("cat") != "push" or not str(it.get("id", "")).startswith("push-") or it["id"] == "push-overview":
            continue
        iid = it["id"][5:]
        iid = iid if iid in ids else None
        if not iid:
            did.append(f"skip {it['id']}: not in the package")
            continue
        st, reply, note = it.get("status"), (it.get("reply") or "").strip(), (it.get("note") or "").strip()
        when = it.get("replied_at") or it.get("done_at") or it.get("seen_at") or it.get("updated") or now_iso()
        have = store["answers"].get(iid) or {}
        sets = {}
        if ids[iid] == "call":
            c = calls[iid]
            if st == "done" or reply:
                norm = lambda x: re.sub(r"[^a-z0-9]+", " ", x.lower().replace("(recommended)", "")).strip()
                named = next((o for o in c["options"] if norm(o["label"]) == norm(reply)), None) if reply else None
                rec = next(o for o in c["options"] if o.get("recommended"))
                if st == "done" or named:
                    sets["choice"] = (named or rec)["id"]
                if reply:
                    sets["reply"] = reply
        else:
            if st == "done":
                sets["ok"] = True
            if st == "needs-changes":
                sets["hold"] = True
        if st in ("seen", "done", "needs-changes"):
            sets["seen"] = True
        if note:
            sets["note"] = note
        sets = {f: v for f, v in sets.items() if have.get("rev", {}).get(f) is None and not (f == "seen" and have.get("seen"))}
        if not sets:
            continue
        err, conf = apply_answer(store, pkg, {"id": iid, "set": sets, "seen": {}}, via="My to-do", when=when)
        did.append(f"{it['id']} -> {iid}: " + (err or ", ".join(f"{k}={v!r}" for k, v in sets.items())))
    return did


# ------------------------------------------------------------------------------------- route
try:
    from flask import Blueprint, jsonify, request, send_from_directory
    bp = Blueprint("push_package", __name__)
except Exception:                                       # the CLI works without Flask
    bp = None


def _payload(pkg, ver, store, checks=True):
    done = load_done(pkg["id"])
    st = statuses(pkg, store, done)
    j = {"ok": True, "package": pkg, "package_version": ver, "answers": store["answers"] if store else {},
         "rev": store.get("rev", 0) if store else 0, "dir": str(pkg_dir()), "status": st}
    if checks:
        try:
            from flask import current_app
            info = current_app.config.get("BUILD_ID")
        except Exception:
            info = None
        lc = live_checks(info, auto=not readonly())
        j["checks"] = lc
        n_open = sum(1 for s in st.values() if s["state"] == "open")
        n_work = sum(1 for s in st.values() if s["state"] == "working")
        n_red = sum(1 for c in lc["items"] if not c.get("ok") or c.get("stale"))
        j["ready"] = {"open": n_open, "working": n_work, "red": n_red, "ok": not (n_open or n_work or n_red)}
    return j


if bp is not None:
    @bp.route("/api/push", methods=["GET", "POST", "OPTIONS"])
    def api_push():
        if request.method == "OPTIONS":
            return "", 204
        try:
            pkg, ver = load_package()
            if request.method == "GET":
                if pkg is None:
                    return jsonify({"ok": True, "package": None, "answers": {}, "rev": 0})
                store, _old = load_answers(pkg["id"])
                return jsonify(_payload(pkg, ver, store))
            if readonly():
                return jsonify({"ok": False, "error": "this server is read-only for the push answers"}), 403
            if pkg is None:
                return jsonify({"ok": False, "error": "there is no push package"}), 404
            body = request.get_json(force=True, silent=True)
            if not isinstance(body, dict):
                return jsonify({"ok": False, "error": "the request body must be a JSON object"}), 400
            if body.get("op") == "checks":                 # the Run checks button: every check, in a child process
                started = start_checks()
                store, _old = load_answers(pkg["id"])
                return jsonify(dict(_payload(pkg, ver, store), started=started))
            if body.get("op") != "answer":
                return jsonify({"ok": False, "error": f"unknown op {body.get('op')!r}"}), 400
            with FileLock(answers_path()):
                store, old = load_answers(pkg["id"])
                if body.get("package") and body["package"] != pkg["id"]:
                    return jsonify(dict(_payload(pkg, ver, store), ok=False, stale=True, conflicts=[],
                                        error="The push package was rebuilt since this page read it. Nothing was written; here it is again.")), 409
                err, conflicts = apply_answer(store, pkg, body)
                if err:
                    return jsonify({"ok": False, "error": err}), 400
                if conflicts:
                    return jsonify({"ok": False, "stale": True, "conflicts": conflicts, "answers": store["answers"], "rev": store["rev"],
                                    "package_version": ver, "error": "Changed in another tab since this page read it. Nothing was overwritten; here is the newest."}), 409
                save_answers(store, old)
                return jsonify({"ok": True, "answers": store["answers"], "rev": store["rev"], "package_version": ver,
                                "status": statuses(pkg, store, load_done(pkg["id"]))})
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 423

    @bp.route("/api/push/file/<path:rel>")
    def api_push_file(rel):
        rel = rel.replace("\\", "/")
        if not rel.startswith("shots/") or ".." in rel.split("/"):
            return jsonify({"ok": False, "error": "not a package file"}), 404
        resp = send_from_directory(pkg_dir(), rel)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def register(app):
    if bp is not None:
        app.register_blueprint(bp)


# ------------------------------------------------------------------------------------- CLI
def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        out(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "import-html":
        src = Path(rest[0]) if rest else pkg_dir() / "index.html"
        pkg = import_html(src)
        problems = validate(pkg)
        write_json_atomic(package_path(), pkg)
        n = sum(1 for _ in all_entries(pkg))
        out(f"{package_path()}: {len(pkg['calls'])} calls, {n - len(pkg['calls'])} changes in {len(pkg['sections'])} sections, "
            f"{sum(len(e.get('shots') or []) for _k, e in all_entries(pkg))} shot tiers; id {pkg['id']}")
        for p in problems:
            out("  ! " + p)
        return 1 if problems else 0
    if cmd == "rebuild":
        base = rest[rest.index("--base") + 1] if "--base" in rest else None
        pkg = rebuild(base)
        a = pkg["auto"]
        out(f"rebuilt {pkg['id']} on {a['base']} ({a['base_sha']}): {a['files_changed']} files changed, {a['pages_changed']} pages, "
            f"{a['images_to_add']} photo files to add, {len(pkg['unreviewed_pages'])} changed pages not described yet")
        for u in pkg["unreviewed_pages"]:
            out(f"  not described: {u['page']} ({u['status']})")
        for p in validate(pkg):
            out("  ! " + p)
        return 0
    if cmd == "merge":
        pkg = merge_fragment(rest[0])
        out(f"merged {rest[0]} into {package_path()} ({len(pkg['calls'])} calls)")
        return 0
    if cmd == "shoot":
        made = shoot([x for x in rest if not x.startswith("--")] or None, "--missing" in rest)
        out(f"{len(made)} screenshots; run `rebuild` to attach them")
        return 0
    if cmd == "check":
        pkg, _v = load_package()
        problems = validate(pkg) if pkg else ["no package.json"]
        out("\n".join(problems) or "package.json is consistent")
        return 1 if problems else 0
    if cmd == "answers":
        pkg, _v = load_package()
        if pkg is None:
            out("no push package")
            return 1
        store, old = load_answers(pkg["id"])
        if "--json" in rest:
            out(json.dumps(store, indent=1, ensure_ascii=False))
        else:
            out(answers_report(pkg, store))
            if old:
                out(f"\n(answers.json holds answers to another package, {old.get('package')}; they are not these)")
        return 0
    if cmd == "import-todo":
        items = json.loads(Path(rest[0]).read_text(encoding="utf-8-sig"))
        items = items.get("items", items) if isinstance(items, dict) else items
        pkg, _v = load_package()
        with FileLock(answers_path()):
            store, old = load_answers(pkg["id"])
            did = import_todo(items, pkg, store)
            if "--dry-run" not in rest:
                save_answers(store, old)
        out("\n".join(did) or "nothing to carry over")
        return 0
    if cmd == "retext":                                 # shorter text from a JSON file; the old note is kept as `more`
        t = json.loads(Path(rest[0]).read_text(encoding="utf-8-sig"))
        pkg, _v = load_package()
        for k, v in (t.get("top") or {}).items():
            pkg[k] = v
        if "push_steps" in t:
            pkg["push_steps"] = t["push_steps"]
        for r in pkg.get("summary") or []:
            if r.get("group") in (t.get("summary") or {}):
                r["notices"] = t["summary"][r["group"]]
        for s in pkg.get("sections") or []:
            if s["id"] in (t.get("intros") or {}):
                s["intro"] = t["intros"][s["id"]]
        notes = dict(t.get("calls") or {}, **(t.get("changes") or {}))
        n = 0
        for _k, e in all_entries(pkg):
            if e["id"] in notes and notes[e["id"]] != e.get("note"):
                if e.get("note") and not e.get("more"):
                    e["more"] = e["note"]
                e["note"] = notes[e["id"]]
                n += 1
        pkg["checks"] = []                              # the checks are live now (checks.json + the editor's own)
        problems = validate(pkg)
        if problems:
            raise SystemExit("\n".join(problems))
        write_json_atomic(package_path(), pkg)
        out(f"{n} notes shortened")
        return 0
    if cmd in ("todo", "status"):
        pkg, _v = load_package()
        store, _o = load_answers(pkg["id"])
        st = statuses(pkg, store, load_done(pkg["id"]))
        titles = {e["id"]: md_text(e.get("title") or "") for _k, e in _entries_with_kind(pkg)}
        want = ("working",) if cmd == "todo" else ("open", "working", "settled")
        for state in want:
            ids = [i for i, s in st.items() if s["state"] == state]
            out(f"{state}: {len(ids)}")
            for i in ids:
                out(f"  {i}  {titles[i][:70]}" + (f"\n      his answer: {st[i]['answer']}" if st[i]["answer"] else "")
                    + (f"\n      done: {st[i]['text']}" if state == "settled" and cmd == "status" else ""))
        return 0
    if cmd == "done":
        d = mark_done(rest[0], " ".join(rest[1:]))
        out(f"{rest[0]}: settled at answer revision {d['rev']}")
        return 0
    if cmd == "watch":                                  # for a Monitor: one line each time an item starts needing work
        every = float(rest[rest.index("--every") + 1]) if "--every" in rest else 15
        told = {}
        while True:
            try:
                pkg, _v = load_package()
                if pkg:
                    store, _o = load_answers(pkg["id"])
                    st = statuses(pkg, store, load_done(pkg["id"]))
                    for i, s in st.items():
                        key = (s["state"], s.get("answer"))
                        if s["state"] == "working" and told.get(i) != key:
                            out(f"PUSH ANSWER {i}: {s['answer'] or '(cleared)'}")
                        told[i] = key
            except Exception as e:                      # a half-written file: try again next round
                out(f"watch: {e}") if "--verbose" in rest else None
            time.sleep(every)
    if cmd == "checks":
        only = rest[rest.index("--only") + 1].split(",") if "--only" in rest else None
        res = run_checks(only)
        for c in res["items"]:
            out(("OK   " if c["ok"] else "FIX  ") + md_text(c["text"]))
        return 0 if all(c["ok"] for c in res["items"]) else 1
    if cmd == "stage":
        added, missing, extra = stage_add_list()
        out(f"staged {len(added)} new files; {len(missing)} listed files missing on disk; {len(extra)} staged but not listed")
        for r in missing[:20]:
            out("  missing: " + r)
        return 0
    if cmd == "archive":
        pkg, _v = load_package()
        p = answers_path()
        if p.is_file():
            dst = pkg_dir() / f"answers-{(pkg or {}).get('id', 'old')}-{datetime.now():%Y%m%d-%H%M%S}.json"
            os.replace(p, dst)
            out(f"answers archived to {dst}")
        return 0
    out(f"unknown command {cmd!r}\n\n" + __doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
