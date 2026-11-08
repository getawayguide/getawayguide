"""The editor's Queue tab (Kevin, 2026-11-06: "add the queue to the editor apps so I can see what I have
queued for claude and add to-do list there (queue for me), add as new tab next to launch").

Two stores, both under .tmp/ (override the folder with QUEUE_TAB_DIR; QUEUE_TAB_READONLY=1 refuses writes):

  away_queue.md     Claude's queue. Claude edits this file by hand in every session, so this module never
                    rewrites it: it parses the file into lines, changes only the lines an operation names,
                    and writes every other byte back as it was (BOM, CRLF, indents, the sections it does
                    not understand). Every write is a compare-and-swap on the file's version: the client
                    sends the version it read, and a file that changed underneath is refused with 409 and
                    the fresh parse, never merged.
  kevin_todo.json   Kevin's own to-do list (add, check off, edit, reorder, delete, due date, link).

Markers in the Queue section:  [ ] pending   [x] done   [~] or [>] running   [-] on hold
(a pending item whose first line says "running" also reads as running). Lines in the Queue section before
its first numbered item (the "FIRST" setup checklist) come back as `queue_pre`.

Routes (CORS open like the rest of the photo server; the cloud editor mirrors them):

  GET  /api/queue                       -> {ok, version, title, intro, sections, queue, counts, path}
  POST /api/queue   {version, op: "add",     title, details?, position?: "end"|"top"}
                    {version, op: "reorder", order: [num, ...]}            renumbers 1..n
                    {version, op: "hold",    num, hold: true|false}
                                        -> the GET payload; 409 {stale:true, error, ...GET payload} if the
                                           version differs; 400 on a bad op; 403 when read-only
  GET  /api/todo                        -> {ok, version, categories: [{id, name}], items: [...], counts: {cat: {open, new, done, needs}}}
                                           (folds the live rounds under .tmp/redline and the Shadow reviewer's unanswered
                                           threads into the file first, at most every 30 s; ?sync=0 skips that)
       item = {id, cat, kind: todo|review|question, title, summary, status: new|seen|done|needs-changes, note, reply,
               due, links: {editor: <repo path>, preview: <repo path>, url: <https…>}, shots: {desktop, phone} (repo
               paths under .tmp/review_hub/shots, served at /site/<path>), round: {slug, changes, left}, created, …}
  POST /api/todo    {version?, op: "add",     cat, title, summary?, kind?, links?, shots?, due?, note?, id?, position?: "top",
                                              cat_name? (creates the category when cat is new; else it lands in "mine")}
                    {version?, op: "update",  id, title?, summary?, status?, note?, reply?, due?, links?, shots?, cat?}
                    {version?, op: "toggle",  id, done?}                     done <-> seen
                    {version?, op: "delete",  id}
                    {version?, op: "reorder", cat, order: [id, ...]}          Kevin's order inside one category
                    {version?, op: "reorder_cats", order: [cat id, ...]}      his order of the categories
                    {version?, op: "rename_cat", id, name}
                    {version?, op: "batch",   ops: [ ...any of the above ]}
                                        -> the GET payload; 409 when a version is sent and differs; 400 on a bad op
  An agent adds a review item with: op add, id "<type>-<slug>", cat maps|rounds|upgrades|social, kind "review", title,
  summary, links {editor: <repo path>}, shots {desktop, phone} from tools/review_hub_shot.py, status "new".
  Kevin's status, note, reply and order are his: read them, never overwrite them.
  Every item comes back with `group`: {key, name, flag} = its country (from `country`, the article path, or the round
  slug; see item_group), or null. The tab draws the categories in `grouped` ("rounds") as collapsible per-country groups.
  There is no "shadow" category since 2026-11-07: Shadow reviewer items ("<Article>: answer the Shadow reviewer") sit in
  "rounds" right after the article's rounds, and an old store is migrated on load (migrate()).

Standalone (tests, the cloud editor):  python tools/queue_tab.py --serve 5033
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
bp = Blueprint("queue_tab", __name__)
_lock = threading.RLock()

QUEUE_RE = re.compile(r"^(\d+)\.\s+\[([ xX~>-])\]\s?(.*)$")         # "3. [ ] text"
CHECK_RE = re.compile(r"^\s*[-*]\s+\[([ xX~>-])\]\s?(.*)$")          # "- [ ] text"
BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")                           # "- text"
QUEUE_NAMES = ("queue",)


def store_dir():
    return Path(os.environ.get("QUEUE_TAB_DIR") or (ROOT / ".tmp"))


def queue_path():
    return store_dir() / "away_queue.md"


def todo_path():
    return store_dir() / "kevin_todo.json"


def readonly():
    return os.environ.get("QUEUE_TAB_READONLY") == "1"


def version_of(data: bytes):
    return hashlib.sha1(data).hexdigest()[:16]


# ------------------------------------------------------------------------------------- file lock
class FileLock:
    """A lock file beside the store, for writers in other processes that honor it; the in-process RLock
    covers the threaded server. A lock older than 30 s is a crashed writer's and is taken over."""

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
                    raise RuntimeError("the queue is locked by another writer; try again in a moment")
                time.sleep(0.05)

    def __exit__(self, *a):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        _lock.release()


def write_bytes_atomic(p: Path, data: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    for _ in range(40):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, p)


# ------------------------------------------------------------------------------------- markdown
class Doc:
    """The md as lines, with the newline style and BOM it came with."""

    def __init__(self, data: bytes):
        self.bom = data.startswith(b"\xef\xbb\xbf")
        text = data[3:].decode("utf-8") if self.bom else data.decode("utf-8")
        self.nl = "\r\n" if "\r\n" in text else "\n"
        self.lines = text.split(self.nl)
        self.version = version_of(data)

    def to_bytes(self):
        out = self.nl.join(self.lines).encode("utf-8")
        return (b"\xef\xbb\xbf" + out) if self.bom else out

    def sections(self):
        """[(name, heading_line_index, first_content_index, end_index)] for every '## ' heading"""
        heads = [i for i, l in enumerate(self.lines) if l.startswith("## ")]
        out = []
        for k, i in enumerate(heads):
            end = heads[k + 1] if k + 1 < len(heads) else len(self.lines)
            out.append((self.lines[i][3:].strip(), i, i + 1, end))
        return out

    def queue_span(self):
        for name, _h, s, e in self.sections():
            if name.strip().lower().split(" (")[0].split(":")[0] in QUEUE_NAMES:
                return s, e
        return None


def _is_cont(line):
    return bool(line.strip()) and line[:1] in (" ", "\t")


def queue_blocks(lines):
    """The Queue section's lines as (pre, [block, ...], tail). A block is an item's first line plus its
    indented continuation lines, plus any loose lines up to the next item (they move with it)."""
    pre, blocks, i = [], [], 0
    while i < len(lines) and not QUEUE_RE.match(lines[i]):
        pre.append(lines[i])
        i += 1
    while i < len(lines):
        m = QUEUE_RE.match(lines[i])
        block = {"num": int(m.group(1)), "mark": m.group(2), "text": m.group(3), "first": lines[i], "cont": [], "loose": []}
        i += 1
        while i < len(lines) and _is_cont(lines[i]):
            block["cont"].append(lines[i])
            i += 1
        blocks.append(block)
        j = i
        while j < len(lines) and not QUEUE_RE.match(lines[j]):
            j += 1
        if j >= len(lines):                      # nothing but the section's tail follows
            break
        block["loose"] = lines[i:j]              # loose lines between two items ride with the one above
        i = j
    tail = lines[i:]
    return pre, blocks, tail


def _strip_md(s):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", s).strip()


KEVIN_PAREN = re.compile(r"\s*\((?:Kevin|added by Kevin)\b[^()]*(?:\([^()]*\)[^()]*)*\)")


def title_of(text):
    """the bold run, else the first clause of the line (a 'DONE <date> (...): ' prefix dropped)"""
    m = re.match(r"\s*\*\*(.+?)\*\*", text)
    if m:
        t = m.group(1)
        rest = text[m.end():]
    else:
        t = re.sub(r"^\s*(DONE|RUNNING|ON HOLD|HOLD)\b[^:]*:\s*", "", text)
        parts = re.split(r"(?<=[a-z0-9\)\]])\.\s+|:\s+", t, maxsplit=1)
        t = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
    t = KEVIN_PAREN.sub("", t)
    t = _strip_md(t).rstrip(" .:;")
    return t, rest


def item_of(block):
    text = block["text"]
    title, rest = title_of(text)
    mark = block["mark"].lower()
    state = {"x": "done", "-": "hold", "~": "running", ">": "running"}.get(mark, "pending")
    if state == "pending" and re.search(r"\b(is )?running\b", text, re.I) and not re.search(r"\bnot (be )?running\b", text, re.I):
        state = "running"
    added = re.search(r"\(added by Kevin (\d{4}-\d{2}-\d{2})\)", text)
    done = re.search(r"\bDONE (\d{4}-\d{2}-\d{2})", text)
    summary = re.sub(r"^[\s.:;,]+", "", rest).strip()
    details = [re.sub(r"^\s*[-*]\s*", "", l) for l in block["cont"]]
    return {"num": block["num"], "mark": block["mark"], "state": state, "title": title, "summary": summary,
            "text": text, "details": details, "added_by": added.group(1) if added else None,
            "done_on": done.group(1) if done else None}


def parse(doc: Doc):
    out = {"ok": True, "version": doc.version, "title": "", "intro": [], "sections": [], "queue": [], "queue_pre": {"label": "", "items": []},
           "counts": {"pending": 0, "done": 0, "hold": 0, "running": 0}}
    secs = doc.sections()
    first = secs[0][1] if secs else len(doc.lines)
    for l in doc.lines[:first]:
        if l.startswith("# ") and not out["title"]:
            out["title"] = l[2:].strip()
        elif l.strip():
            out["intro"].append(l.strip())
    for name, _h, s, e in secs:
        body = doc.lines[s:e]
        if (s, e) == (doc.queue_span() or (None, None)):
            pre, blocks, _tail = queue_blocks(body)
            out["queue"] = [item_of(b) for b in blocks]
            out["queue_pre"] = {"label": "", "items": []}
            for l in pre:
                m = CHECK_RE.match(l)
                if m:
                    out["queue_pre"]["items"].append({"text": m.group(2).strip(), "done": m.group(1).lower() == "x"})
                elif l.strip() and not out["queue_pre"]["items"]:
                    out["queue_pre"]["label"] = _strip_md(KEVIN_PAREN.sub("", l)).rstrip(" :")
            for it in out["queue"]:
                out["counts"][it["state"]] += 1
            out["sections"].append({"name": name, "kind": "queue"})
            continue
        items, kind = [], "text"
        if any(CHECK_RE.match(l) for l in body):
            kind = "checklist"
            for l in body:
                m = CHECK_RE.match(l)
                if m:
                    items.append({"text": m.group(2).strip(), "done": m.group(1).lower() == "x"})
                elif l.strip() and items and _is_cont(l):
                    items[-1]["text"] += " " + l.strip()
        elif any(BULLET_RE.match(l) for l in body):
            kind = "bullets"
            for l in body:
                m = BULLET_RE.match(l)
                if m and not _is_cont(l):
                    items.append({"text": m.group(1).strip()})
                elif l.strip() and items:
                    items[-1]["text"] += " " + l.strip()
        else:
            items = [{"text": l.strip()} for l in body if l.strip()]
        out["sections"].append({"name": name, "kind": kind, "items": items})
    return out


def load_doc():
    p = queue_path()
    if not p.is_file():
        return None
    return Doc(p.read_bytes())


def _indent_of(blocks):
    for b in blocks:
        for l in b["cont"]:
            return re.match(r"^\s*", l).group(0)
    return "   "


def apply_queue_op(doc: Doc, body: dict):
    """edit the Queue section's lines in place; returns an error string or None"""
    span = doc.queue_span()
    if not span:
        return "the file has no '## Queue' section"
    s, e = span
    pre, blocks, tail = queue_blocks(doc.lines[s:e])
    op = body.get("op")
    if op == "add":
        title = (body.get("title") or "").strip()
        if not title:
            return "the item needs a title"
        title = title.replace("\r", " ").replace("\n", " ")
        details = [d.strip() for d in str(body.get("details") or "").replace("\r", "").split("\n") if d.strip()]
        ind = _indent_of(blocks)
        stamp = body.get("date") or date.today().isoformat()
        new = {"num": max([b["num"] for b in blocks] + [0]) + 1, "mark": " ",
               "text": f"**{title}** (added by Kevin {stamp})", "cont": [f"{ind}- {d}" for d in details], "loose": []}
        new["first"] = f"{new['num']}. [ ] {new['text']}"
        if body.get("position") == "top":
            blocks.insert(0, new)
            _renumber(blocks)
        else:
            if blocks and blocks[-1]["loose"]:          # the last item keeps the gap after the new one
                new["loose"], blocks[-1]["loose"] = blocks[-1]["loose"], []
            blocks.append(new)
    elif op == "reorder":
        order = body.get("order")
        nums = [b["num"] for b in blocks]
        if not isinstance(order, list) or sorted(order) != sorted(nums) or len(set(order)) != len(nums):
            return "the order must list every item's number exactly once"
        if order == nums:
            return None                           # nothing to do: not a byte changes
        by = {b["num"]: b for b in blocks}
        blocks = [by[n] for n in order]           # a blank line under an item travels with it
        _renumber(blocks)
    elif op == "hold":
        num = body.get("num")
        b = next((b for b in blocks if b["num"] == num), None)
        if not b:
            return f"no item {num}"
        if b["mark"].lower() == "x":
            return f"item {num} is done; it cannot go on hold"
        want = "-" if body.get("hold") else " "
        b["mark"] = want
        b["first"] = f"{b['num']}. [{want}] {b['text']}"
    else:
        return f"unknown op {op!r}"
    new_lines = list(pre)
    for b in blocks:
        new_lines.append(b["first"])
        new_lines.extend(b["cont"])
        new_lines.extend(b["loose"])
    new_lines.extend(tail)
    doc.lines[s:e] = new_lines
    return None


def _renumber(blocks):
    for i, b in enumerate(blocks, 1):
        b["num"] = i
        b["first"] = f"{i}. [{b['mark']}] {b['text']}"


# ------------------------------------------------------------------------------------- the inbox (My to-do)
# Kevin, 2026-11-06: "add review hub to editor instead of being a separate artifact. add it as part of my to-dos ...
# better keep them organized so I don't get overwhelmed or I can choose which categories to pay attention to";
# "let me order the to do hub by dragging". So one file holds every category: the order of `categories` and the
# order of `items` inside a category are HIS order (new items land at the end of their category), and the
# review-hub items (maps, rounds, upgrades), the open questions for him and his own to-dos are all the same shape.
# Kevin, 2026-11-07: "in queue organize review rounds by country and add flag and collapsability since I work on
# things by country. Shadow reviewer just becomes part of regular article review from now on so merge that as part of
# the review: rounds". So there is no "shadow" category any more: its items live in "rounds" (migrate() moves the old
# ones, sync_live() seeds new ones there), every item comes back with its country (`group`, derived below) and the tab
# draws the rounds category as one collapsible group per country, flag first.
CATEGORIES = [("rounds", "Review: rounds"), ("maps", "Review: maps"), ("upgrades", "Review: articles"),
              ("feedback", "Feedback for Claude"), ("social", "Social"),
              ("setup", "Setup before Friday"), ("mine", "My own to-dos")]
MERGED_CATS = {"shadow": "rounds"}            # old category id -> where its items live now
STATUSES = ("new", "seen", "done", "needs-changes")
SHOTS_DIR = ".tmp/review_hub/shots"           # served by the photo server at /site/<path>
FLAGS_DIR = "Images/web/flags"                # the nav's local flags (tools/localize_flags.py), <iso>.png

# country folder (Drafts/<tier>/<folder>/, or <folder>/ live) -> (name as the nav shows it, ISO-3166 alpha-2 of its flag)
COUNTRIES = {
    "albania": ("Albania", "al"), "argentina": ("Argentina", "ar"), "armenia": ("Armenia", "am"), "australia": ("Australia", "au"),
    "belgium": ("Belgium", "be"), "bosnia": ("Bosnia", "ba"), "brazil": ("Brazil", "br"), "chile": ("Chile", "cl"),
    "colombia": ("Colombia", "co"), "croatia": ("Croatia", "hr"), "cuba": ("Cuba", "cu"), "denmark": ("Denmark", "dk"),
    "egypt": ("Egypt", "eg"), "el-salvador": ("El Salvador", "sv"), "estonia": ("Estonia", "ee"), "finland": ("Finland", "fi"),
    "france": ("France", "fr"), "georgia": ("Georgia", "ge"), "germany": ("Germany", "de"), "greece": ("Greece", "gr"),
    "guatemala": ("Guatemala", "gt"), "hungary": ("Hungary", "hu"), "india": ("India", "in"), "indonesia": ("Indonesia", "id"),
    "italy": ("Italy", "it"), "japan": ("Japan", "jp"), "kosovo": ("Kosovo", "xk"), "latvia": ("Latvia", "lv"),
    "mexico": ("Mexico", "mx"), "montenegro": ("Montenegro", "me"), "morocco": ("Morocco", "ma"), "netherlands": ("Netherlands", "nl"),
    "new-zealand": ("New Zealand", "nz"), "nicaragua": ("Nicaragua", "ni"), "north-macedonia": ("North Macedonia", "mk"),
    "peru": ("Peru", "pe"), "philippines": ("Philippines", "ph"), "portugal": ("Portugal", "pt"), "serbia": ("Serbia", "rs"),
    "slovenia": ("Slovenia", "si"), "spain": ("Spain", "es"), "sweden": ("Sweden", "se"), "switzerland": ("Switzerland", "ch"),
    "tanzania": ("Tanzania", "tz"), "thailand": ("Thailand", "th"), "tunisia": ("Tunisia", "tn"), "turkiye": ("Türkiye", "tr"),
    "uk": ("UK", "gb"), "vietnam": ("Vietnam", "vn"),
}
_BY_NAME = {n.lower(): k for k, (n, _i) in COUNTRIES.items()}
_BY_NAME.update({"turkey": "turkiye", "bosnia and herzegovina": "bosnia", "macedonia": "north-macedonia",
                 "united kingdom": "uk", "england": "uk", "scotland": "uk"})


def country_key(text):
    """'Türkiye' / 'Turkiye' / 'north macedonia' / 'north-macedonia' -> the country folder, else None"""
    import unicodedata
    if not text:
        return None
    raw = str(text).strip().lower()
    t = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    t = re.sub(r"[\s_]+", "-", t)
    if t in COUNTRIES:
        return t
    return _BY_NAME.get(raw) or _BY_NAME.get(t.replace("-", " "))


def _key_from_path(rel):
    """Drafts/<Upcoming|Full Articles|Field Notes>/<country>/x.html or <country>/x.html -> the country folder"""
    if not rel:
        return None
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p]
    if len(parts) >= 2 and parts[0] == "Drafts":
        parts = parts[2:]
    if len(parts) >= 2 and parts[0].lower() in COUNTRIES:
        return parts[0].lower()
    return None


def _key_from_slug(slug):
    """'top10-turkiye-r3' / 'north-macedonia-itinerary' -> the country folder named in the slug, else None"""
    if not slug:
        return None
    s = "-" + re.sub(r"-r\d+$", "", str(slug).lower()) + "-"
    for k in sorted(COUNTRIES, key=len, reverse=True):         # north-macedonia before any shorter name
        if f"-{k}-" in s:
            return k
    return None


def item_country(it):
    """the item's country folder: its own fields first, then the article it points at, then the round's slug"""
    art = it.get("article")
    iid = str(it.get("id", ""))
    for k in (country_key(it.get("country")),
              _key_from_path(art.get("path") if isinstance(art, dict) else art),
              _key_from_path((it.get("links") or {}).get("editor")),
              _key_from_path((it.get("links") or {}).get("preview")),
              _key_from_slug((it.get("round") or {}).get("slug")),
              _key_from_slug(iid[6:] if iid.startswith("round-") else None),
              _key_from_slug(iid[7:] if iid.startswith("shadow-") else None)):
        if k:
            return k
    return None


def item_group(it):
    """{key, name, flag} for the country an item belongs to (the tab's group), or None (the tab's 'Other')"""
    k = item_country(it)
    if not k:
        return None
    name, iso = COUNTRIES[k]
    return {"key": k, "name": name, "flag": f"{FLAGS_DIR}/{iso}.png" if (ROOT / FLAGS_DIR / f"{iso}.png").is_file() else None}


def _article_key(it):
    """what 'the same article' means for keeping a round and its Shadow pass together: the article path, else the slug
    without its round number"""
    art = it.get("article")
    p = (art.get("path") if isinstance(art, dict) else art) or (it.get("links") or {}).get("editor")
    if p:
        return str(p).replace("\\", "/").lower()
    iid = str(it.get("id", ""))
    slug = (it.get("round") or {}).get("slug") or (iid[6:] if iid.startswith("round-") else iid[7:] if iid.startswith("shadow-") else "")
    return re.sub(r"-r\d+$", "", slug.lower()) or None


def insert_with_article(items, it, cat):
    """a new round or Shadow item lands right after the last item of ITS article in the category (Kevin reviews a round
    and the Shadow pass in one sitting, so they sit together), else at the end of the category; nothing else moves"""
    key = _article_key(it)
    last = None
    if key:
        for i, x in enumerate(items):
            if x.get("cat") == cat and _article_key(x) == key:
                last = i
    if last is None:
        items.append(it)
    else:
        items.insert(last + 1, it)


def redline_dir():
    return Path(os.environ.get("QUEUE_TAB_REDLINE") or (ROOT / ".tmp" / "redline"))


def empty_store():
    return {"categories": [{"id": i, "name": n} for i, n in CATEGORIES], "items": [], "updated": None}


def load_todo():
    p = todo_path()
    if not p.is_file() or p.stat().st_size == 0:
        return empty_store(), version_of(b"")
    data = p.read_bytes()
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except Exception as e:
        raise RuntimeError(f"{p.name} is unreadable, refusing to overwrite it: {e}")
    if not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
        raise RuntimeError(f"{p.name} is not a to-do store")
    if migrate(obj):
        obj["_migrated"] = True                 # the next save (a GET's sync, or any write) persists the new shape
    return obj, version_of(data)


def migrate(obj):
    """the first store (text/done/link) and hub docs (type/status/notes) become inbox items in place; a category that was
    merged away (shadow -> rounds, 2026-11-07) disappears and its items move, each placed after the last item of its
    article in the new category, status/note/reply untouched. Returns True when the store changed shape."""
    changed = False
    if not isinstance(obj.get("categories"), list) or not obj["categories"]:
        obj["categories"] = [{"id": i, "name": n} for i, n in CATEGORIES]
    for old_id, new_id in MERGED_CATS.items():
        if any(c["id"] == old_id for c in obj["categories"]):
            obj["categories"] = [c for c in obj["categories"] if c["id"] != old_id]
            if not any(c["id"] == new_id for c in obj["categories"]):
                obj["categories"].insert(0, {"id": new_id, "name": dict(CATEGORIES).get(new_id, new_id)})
            changed = True
        moving = [it for it in obj["items"] if it.get("cat") == old_id]
        if moving:
            obj["items"] = [it for it in obj["items"] if it.get("cat") != old_id]
            for it in moving:
                it["cat"] = new_id
                it["moved_from"] = old_id
                insert_with_article(obj["items"], it, new_id)
            changed = True
    known = {c["id"] for c in obj["categories"]}
    for it in obj["items"]:
        if "title" not in it:
            it["title"] = it.pop("text", "") or ""
        if "status" not in it:
            it["status"] = "done" if it.pop("done", False) else "new"
        it.pop("done", None)
        if it.get("cat") not in known:
            it["cat"] = "mine"
        if "links" not in it or not isinstance(it["links"], dict):
            it["links"] = {}
        if it.get("link") and "editor" not in it["links"] and "url" not in it["links"]:
            it["links"]["url" if re.match(r"^https?://", it["link"]) else "editor"] = it.pop("link")
        it.pop("link", None)
        for k in ("summary", "note", "reply", "due", "created", "done_at", "seen_at"):
            it.setdefault(k, None)
        it.setdefault("kind", "review" if it.get("shots") else "todo")
        it.setdefault("id", uuid.uuid4().hex[:10])
    return changed


def todo_payload(obj, version):
    counts = {}
    items = []
    for it in obj["items"]:
        items.append(dict(it, group=item_group(it)))        # the country group is derived on every read, never stored
        c = counts.setdefault(it["cat"], {"open": 0, "new": 0, "done": 0, "needs": 0})
        if it["status"] == "done":
            c["done"] += 1
        else:
            c["open"] += 1
            if it["status"] == "new":
                c["new"] += 1
            if it["status"] == "needs-changes":
                c["needs"] += 1
    return {"ok": True, "version": version, "categories": obj["categories"], "items": items, "counts": counts,
            "statuses": list(STATUSES), "shots_dir": SHOTS_DIR, "flags_dir": FLAGS_DIR, "grouped": ["rounds"]}


def _clean_str(v, n=2000):
    return str(v).replace("\r", " ").replace("\n", " ").strip()[:n] if v is not None else ""


def _clean_links(v):
    if isinstance(v, str):
        v = {"url" if re.match(r"^https?://", v) else "editor": v}
    if not isinstance(v, dict):
        return {}
    return {_clean_str(k, 40): _clean_str(x, 600) for k, x in v.items() if _clean_str(x, 600) and _clean_str(k, 40)}


def _clean_shots(v):
    if not isinstance(v, dict):
        return None
    out = {k: _clean_str(x, 300).replace("\\", "/") for k, x in v.items() if k in ("desktop", "phone") and _clean_str(x, 300)}
    return out or None


def new_item(body, now):
    cat = _clean_str(body.get("cat"), 40) or "mine"
    it = {"id": _clean_str(body.get("id"), 80) or uuid.uuid4().hex[:10], "cat": cat,
          "kind": body.get("kind") if body.get("kind") in ("todo", "review", "question") else ("review" if body.get("shots") else "todo"),
          "title": _clean_str(body.get("title") or body.get("text")), "summary": _clean_str(body.get("summary")) or None,
          "status": body.get("status") if body.get("status") in STATUSES else "new",
          "note": _clean_str(body.get("note")) or None, "reply": None, "due": None,
          "links": _clean_links(body.get("links") or body.get("link")), "shots": _clean_shots(body.get("shots")),
          "created": now, "done_at": None, "seen_at": None}
    for k in ("country", "article", "round", "source"):
        if body.get(k) is not None:
            it[k] = body[k]
    due = _clean_str(body.get("due"), 10)
    if due:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", due):
            return None, "due must be YYYY-MM-DD"
        it["due"] = due
    if not it["title"]:
        return None, "the item needs a title"
    return it, None


def apply_todo_op(obj, body):
    op = body.get("op")
    items = obj["items"]
    cats = obj["categories"]
    now = datetime.now().isoformat(timespec="seconds")
    if op == "add":
        it, err = new_item(body, now)
        if err:
            return err
        if it["cat"] not in {c["id"] for c in cats}:
            if body.get("cat_name"):
                cats.append({"id": it["cat"], "name": _clean_str(body["cat_name"], 60)})
            else:
                it["cat"] = "mine"
        if any(x["id"] == it["id"] for x in items):
            return f"an item {it['id']!r} exists; use update"
        if body.get("position") == "top":
            first = next((i for i, x in enumerate(items) if x["cat"] == it["cat"]), len(items))
            items.insert(first, it)
        else:
            items.append(it)                      # the end of its category: Kevin's order is never reshuffled
        return None
    if op == "reorder_cats":
        order = body.get("order")
        ids = [c["id"] for c in cats]
        if not isinstance(order, list) or sorted(order) != sorted(ids):
            return "the order must list every category exactly once"
        by = {c["id"]: c for c in cats}
        obj["categories"] = [by[i] for i in order]
        return None
    if op == "rename_cat":
        c = next((c for c in cats if c["id"] == body.get("id")), None)
        if not c:
            return f"no category {body.get('id')!r}"
        c["name"] = _clean_str(body.get("name"), 60) or c["name"]
        return None
    if op == "reorder":
        order = body.get("order")
        cat = body.get("cat")
        if cat:                                   # the order inside one category; other categories keep theirs
            mine = [x["id"] for x in items if x["cat"] == cat]
            if not isinstance(order, list) or sorted(order) != sorted(mine) or len(set(order)) != len(mine):
                return "the order must list every item of the category exactly once"
            by = {x["id"]: x for x in items}
            seq = iter(order)
            obj["items"] = [by[next(seq)] if x["cat"] == cat else x for x in items]
            return None
        ids = [x["id"] for x in items]
        if not isinstance(order, list) or sorted(order) != sorted(ids) or len(set(order)) != len(ids):
            return "the order must list every to-do's id exactly once"
        by = {x["id"]: x for x in items}
        obj["items"] = [by[i] for i in order]
        return None
    it = next((x for x in items if x.get("id") == body.get("id")), None)
    if not it:
        return f"no to-do {body.get('id')!r}"
    if op == "update":
        for k in ("title", "text"):
            if k in body:
                t = _clean_str(body[k])
                if not t:
                    return "the item needs a title"
                it["title"] = t
        if "due" in body:
            due = _clean_str(body["due"], 10)
            if due and not re.match(r"^\d{4}-\d{2}-\d{2}$", due):
                return "due must be YYYY-MM-DD"
            it["due"] = due or None
        for k in ("summary", "note", "reply"):
            if k in body:
                it[k] = _clean_str(body[k]) or None
                if k == "reply":
                    it["replied_at"] = now if it[k] else None
        if "links" in body or "link" in body:
            it["links"] = _clean_links(body.get("links") if "links" in body else body.get("link"))
        if "shots" in body:
            it["shots"] = _clean_shots(body["shots"])
        if "cat" in body and body["cat"] in {c["id"] for c in cats} and body["cat"] != it["cat"]:
            it["cat"] = body["cat"]
            items.remove(it)
            items.append(it)
        if "status" in body:
            st = body["status"]
            if st not in STATUSES:
                return f"status must be one of {', '.join(STATUSES)}"
            _set_status(it, st, now)
    elif op == "toggle":
        done = bool(body.get("done", it["status"] != "done"))
        _set_status(it, "done" if done else "seen", now)
    elif op == "delete":
        items.remove(it)
    else:
        return f"unknown op {op!r}"
    it["updated"] = now
    return None


def _set_status(it, st, now):
    it["status"] = st
    it["done_at"] = now if st == "done" else None
    if st in ("seen", "done", "needs-changes") and not it.get("seen_at"):
        it["seen_at"] = now
    if st in ("done", "needs-changes"):
        it["decided_at"] = now


# ---- what is waiting on him, read live: pending rounds and the Shadow reviewer's unanswered threads
def live_rounds():
    """{slug: {rel, changes, left, title}} for every round with a proposal and no applied.json"""
    out = {}
    d = redline_dir()
    if not d.is_dir():
        return out
    for r in sorted(d.iterdir()):
        pj = r / "proposal.json"
        if not r.is_dir() or not pj.is_file() or (r / "applied.json").is_file() or not re.search(r"-r\d+$", r.name):
            continue
        try:
            p = json.loads(pj.read_text(encoding="utf-8-sig"))
            dec = json.loads((r / "decisions.json").read_text(encoding="utf-8-sig")) if (r / "decisions.json").is_file() else {}
        except Exception:
            continue
        rel = f"{p.get('article_dir', '').rstrip('/')}/{p.get('article', '')}".strip("/")
        n = len(p.get("changes") or [])
        out[r.name] = {"rel": rel, "changes": n, "left": max(0, n - len(dec)), "title": p.get("title") or r.name,
                       "created": p.get("created")}
    return out


def _rel_for_comments(stem):
    """turkiye__istanbul -> the article's repo path (a draft first, then live)"""
    parts = stem.split("__")
    if len(parts) != 2:
        return None
    country, slug = parts
    for base in ("Drafts/Upcoming", "Drafts/Full Articles", "Drafts/Field Notes", ""):
        p = ROOT / base / country / f"{slug}.html" if base else ROOT / country / f"{slug}.html"
        if p.is_file():
            return str(p.relative_to(ROOT)).replace("\\", "/")
    return None


def live_shadow():
    """{comments stem: {rel, open}} = articles with Shadow reviewer threads Kevin has not answered"""
    out = {}
    d = redline_dir() / "_comments"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            th = json.loads(f.read_text(encoding="utf-8-sig")).get("threads") or []
        except Exception:
            continue
        n = sum(1 for t in th if not t.get("resolved") and (t.get("author") == "Shadow reviewer" or t.get("shadow") or str(t.get("id", "")).startswith("sr-"))
                and not any((r.get("author") or "").lower().startswith("kevin") for r in t.get("replies") or []))
        if n:
            out[f.stem] = {"rel": _rel_for_comments(f.stem), "open": n}
    return out


def _slug_name(slug):
    return slug.replace("-", " ").title().replace("Naxos Ios", "Naxos and Ios")


def _shots_for(iid):
    base = ROOT / SHOTS_DIR
    d, p = base / f"{iid}-desktop.jpg", base / f"{iid}-phone.jpg"
    if d.is_file() or p.is_file():
        return {k: f"{SHOTS_DIR}/{iid}-{k}.jpg" for k, f in (("desktop", d), ("phone", p)) if f.is_file()}
    return None


def sync_live(obj):
    """upsert the live round and Shadow items; returns True when anything changed"""
    now = datetime.now().isoformat(timespec="seconds")
    changed = False
    by_id = {x["id"]: x for x in obj["items"]}
    rounds = live_rounds()
    for slug, r in rounds.items():
        iid = "round-" + slug
        art, rn = slug.rsplit("-r", 1)
        summary = (f"{r['left']} of {r['changes']} changes waiting" if r["left"] != r["changes"] else f"{r['changes']} changes waiting") + " in the editor's review pane."
        it = by_id.get(iid)
        if not it:
            it = {"id": iid, "cat": "rounds", "kind": "review", "title": f"{_slug_name(art)}, round {rn}", "summary": summary, "status": "new",
                  "note": None, "reply": None, "due": None, "links": {"editor": r["rel"]}, "shots": _shots_for(iid), "created": now,
                  "done_at": None, "seen_at": None, "round": {"slug": slug, "changes": r["changes"], "left": r["left"]}, "source": "live: .tmp/redline"}
            ck = _key_from_path(r["rel"])
            if ck:
                it["country"] = COUNTRIES[ck][0]
            insert_with_article(obj["items"], it, "rounds")
            by_id[iid] = it
            changed = True
        else:
            rd = it.get("round") or {}
            if (rd.get("changes"), rd.get("left")) != (r["changes"], r["left"]) or (it["status"] == "done" and r["left"]):
                it["round"] = {"slug": slug, "changes": r["changes"], "left": r["left"]}
                it["summary"] = summary
                if it["status"] == "done" and r["left"]:
                    _set_status(it, "seen", now)
                changed = True
            if not it.get("shots") and _shots_for(iid):
                it["shots"] = _shots_for(iid)
                changed = True
    for it in obj["items"]:            # a round applied or deleted since is finished
        if it["cat"] == "rounds" and it.get("round") and it["id"].startswith("round-") and it["round"]["slug"] not in rounds and it["status"] != "done":
            _set_status(it, "done", now)
            it["summary"] = (it.get("summary") or "").split(" waiting")[0].strip(" .") + ". Applied."
            changed = True
    shadow = live_shadow()
    for stem, s in shadow.items():
        iid = "shadow-" + stem.split("__")[-1]
        it = by_id.get(iid)
        summary = f"{s['open']} Shadow reviewer thread{'s' if s['open'] != 1 else ''} (sr-) without your yes/no. Answer them in the editor's review pane."
        if not it:
            # the Shadow pass is part of the article's review (Kevin, 2026-11-07): it lives in "rounds", beside the article's rounds
            it = {"id": iid, "cat": "rounds", "kind": "todo", "title": f"{_slug_name(stem.split('__')[-1])}: answer the Shadow reviewer", "summary": summary,
                  "status": "new", "note": None, "reply": None, "due": None, "links": {"editor": s["rel"]} if s["rel"] else {}, "shots": None,
                  "created": now, "done_at": None, "seen_at": None, "shadow_open": s["open"], "source": "live: .tmp/redline/_comments"}
            ck = country_key(stem.split("__")[0]) or _key_from_path(s["rel"])
            if ck:
                it["country"] = COUNTRIES[ck][0]
            insert_with_article(obj["items"], it, "rounds")
            by_id[iid] = it
            changed = True
        elif it.get("shadow_open") != s["open"] or it["status"] == "done":
            it["shadow_open"] = s["open"]
            it["summary"] = summary
            if it["status"] == "done":
                _set_status(it, "seen", now)
            changed = True
    for it in obj["items"]:
        if it["id"].startswith("shadow-") and it.get("shadow_open") is not None and it["status"] != "done" and not any(k.split("__")[-1] == it["id"][7:] for k in shadow):
            _set_status(it, "done", now)
            it["summary"] = "All answered."
            changed = True
    return changed


_sync_at = {"t": 0.0}


def save_todo(obj):
    obj.pop("_migrated", None)
    obj["updated"] = datetime.now().isoformat(timespec="seconds")
    write_bytes_atomic(todo_path(), json.dumps(obj, indent=1, ensure_ascii=False).encode("utf-8"))


def import_review_hub(obj, docs_dir=None):
    """the Review Hub artifact's items (its builder left .tmp/review_hub/docs/*.json) become inbox items;
    screenshots are the local files in .tmp/review_hub/shots. Existing ids are left alone."""
    from urllib.parse import unquote
    docs_dir = Path(docs_dir or (ROOT / ".tmp" / "review_hub" / "docs"))
    have = {x["id"] for x in obj["items"]}
    cat_of = {"map": "maps", "round": "rounds", "upgrade": "upgrades", "social": "social", "fix": "mine", "other": "mine"}
    n = 0
    for f in sorted(docs_dir.glob("*.json"), key=lambda f: json.loads(f.read_text(encoding="utf-8-sig")).get("order", 0)):
        d = json.loads(f.read_text(encoding="utf-8-sig"))
        if d["id"] in have:
            continue
        links = {}
        path = (d.get("article") or {}).get("path") or ""
        if path:
            links["editor"] = path
        pv = (d.get("links") or {}).get("preview") or ""
        m = re.search(r"/site/(.+)$", pv)
        if m and unquote(m.group(1)) != path:
            links["preview"] = unquote(m.group(1))
        status = d.get("status") if d.get("status") in STATUSES else ("done" if d.get("status") == "approved" else "new")
        obj["items"].append({"id": d["id"], "cat": cat_of.get(d.get("type"), "mine"), "kind": "review", "title": d["title"], "summary": d.get("summary"),
                             "status": status, "note": d.get("notes") or None, "reply": None, "due": None, "links": links, "shots": _shots_for(d["id"]),
                             "country": d.get("country"), "article": d.get("article"), "round": d.get("round"), "source": d.get("source"),
                             "created": d.get("created"), "done_at": None, "seen_at": None})
        have.add(d["id"])
        n += 1
    return n


# ------------------------------------------------------------------------------------- routes
def _json():
    return request.get_json(force=True, silent=True) or {}


@bp.route("/api/queue", methods=["GET", "POST", "OPTIONS"])
def api_queue():
    if request.method == "OPTIONS":
        return "", 204
    if request.method == "GET":
        doc = load_doc()
        if doc is None:
            return jsonify({"ok": False, "error": f"{queue_path()} does not exist"}), 404
        return jsonify(dict(parse(doc), path=str(queue_path())))
    if readonly():
        return jsonify({"ok": False, "error": "this server is read-only for the queue"}), 403
    body = _json()
    try:
        with FileLock(queue_path()):
            doc = load_doc()
            if doc is None:
                return jsonify({"ok": False, "error": f"{queue_path()} does not exist"}), 404
            if body.get("version") != doc.version:
                return jsonify(dict(parse(doc), ok=False, stale=True, path=str(queue_path()),
                                    error="The queue changed since this page read it (Claude or another tab edited "
                                          "the file). Nothing was written; here is the current list, try again on it.")), 409
            before = doc.to_bytes()
            err = apply_queue_op(doc, body)
            if err:
                return jsonify({"ok": False, "error": err}), 400
            after = doc.to_bytes()
            if after != before:
                write_bytes_atomic(queue_path(), after)
            doc = load_doc()
            return jsonify(dict(parse(doc), path=str(queue_path())))
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 423


@bp.route("/api/todo", methods=["GET", "POST", "OPTIONS"])
def api_todo():
    if request.method == "OPTIONS":
        return "", 204
    try:
        if request.method == "GET":
            # the live view of rounds and Shadow threads is folded into the file (so Kevin's status and order
            # stick to them), at most every 30 s and only when something moved
            if (time.time() - _sync_at["t"] > 30 or request.args.get("sync") == "1") and not readonly() and request.args.get("sync") != "0":
                with FileLock(todo_path()):
                    obj, _v = load_todo()
                    if sync_live(obj) or obj.get("_migrated"):
                        save_todo(obj)
                _sync_at["t"] = time.time()
            obj, v = load_todo()
            return jsonify(todo_payload(obj, v))
        if readonly():
            return jsonify({"ok": False, "error": "this server is read-only for the to-do list"}), 403
        body = _json()
        with FileLock(todo_path()):
            obj, v = load_todo()
            if body.get("version") is not None and body.get("version") != v:
                return jsonify(dict(todo_payload(obj, v), ok=False, stale=True,
                                    error="The to-do list changed since this page read it. Nothing was written.")), 409
            if body.get("op") == "batch":
                for i, o in enumerate(body.get("ops") or []):
                    err = apply_todo_op(obj, o)
                    if err:
                        return jsonify({"ok": False, "error": f"op {i}: {err}"}), 400
            else:
                err = apply_todo_op(obj, body)
                if err:
                    return jsonify({"ok": False, "error": err}), 400
            save_todo(obj)
            obj, v = load_todo()
            return jsonify(todo_payload(obj, v))
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 423


def register(app):
    """photo_editor.py calls this: the Queue tab's API lives on the photo server"""
    app.register_blueprint(bp)


# ------------------------------------------------------------------------------------- standalone
def make_app():
    from flask import Flask
    app = Flask("queue_tab")
    register(app)

    @app.after_request
    def cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    @app.route("/site/<path:rel>")                 # the screenshots and articles the items link to, as the photo server serves them
    def site(rel):
        from flask import send_from_directory
        return send_from_directory(ROOT, rel)
    return app


if __name__ == "__main__":
    if "--serve" in sys.argv:
        port = int(sys.argv[sys.argv.index("--serve") + 1])
        print(f"queue tab api: http://127.0.0.1:{port}  store: {store_dir()}" + ("  READ-ONLY" if readonly() else ""), flush=True)
        make_app().run(port=port, debug=False, threaded=True)
    elif "--import-hub" in sys.argv:
        with FileLock(todo_path()):
            obj, _v = load_todo()
            n = import_review_hub(obj)
            sync_live(obj)
            save_todo(obj)
        print(f"{n} hub items imported into {todo_path()} ({len(obj['items'])} items now)")
    else:
        d = load_doc()
        sys.stdout.buffer.write(json.dumps(parse(d) if d else {"ok": False}, indent=1, ensure_ascii=False).encode("utf-8"))
