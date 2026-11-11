"""Mine Kevin's own typing out of the draft history: what we handed him versus what he saved, round changes taken out.

Read-only on Drafts git and .tmp/redline. Called by tools/kevin_reviewer_corpus.py, which writes the records into
corpus.jsonl as kind "edit". Run this file directly for a sample and the counts:

    python tools/kevin_edits.py --sample 40 --out <file>

Where the before/after pairs come from
  git     a Drafts commit whose message says it is Kevin's save (KEVIN_MSG). The parent is what he had; the commit is
          what he saved. Every round change whose new text shows up in the save is applied to the parent first, so a
          round he accepted does not count as his typing and a sentence he retyped after accepting does.
  chain   round N's baseline with round N's changes applied, versus round N+1's baseline. The Antalya, Yerevan and
          Blue Cruise saves were never committed one by one, so this is the only record of them. The last
          round is compared with the newest committed version. An interval whose two ends barely overlap is a rewrite of ours
          and is skipped.

Whose typing it is (`provenance`)
  kevin   a commit labeled as his alone, or a chain interval; sentences any of OUR commits introduced have been dropped
  mixed   a commit that says it holds his saves and also our markup in one diff ("Skopje r8 markup: Kevin's editor saves
          since ..."). The markup is mostly maps and photos, but a pair from here is less certain.
Retrieval uses both and prefers `kevin`.

Left out: photos, captions and alt text (not in article_blocks), link or bold changes (the text is unchanged), editor
reformatting (compare_key), renumbered headings, whole blocks that only moved, [PHOTO] slots.
"""
import argparse
import difflib
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from article_blocks import blocks_of, clean_text, compare_key, article_type, section_kind  # noqa: E402

DRAFTS = os.path.join(ROOT, "Drafts")
REDLINE = os.path.join(ROOT, ".tmp", "redline")
CACHE = os.path.join(ROOT, ".tmp", "kevin_reviewer", "edit_cache.json")
EXTRACT_VERSION = 8      # bump when the extraction changes; old cache entries are then ignored

ROOT_PARTS = ("Drafts", ".Full Articles", "Upcoming", "Full Articles", "Field Notes", ".Archive", "Archived Articles")

# his alone. Checked before MIXED_MSG only when nothing marks the commit as ours.
KEVIN_MSG = re.compile(
    r"Kevin's (?:own |partial pass|editor (?:save|edit)|edits|round \d+ review save|finished draft|latest pass|"
    r"Top 10 (?:save|pass)|[\w/-]+ edits|version|new intro|[\w-]+ decisions)"
    r"|plus Kevin's own|his own '|reworded the closing himself", re.I)
# his saves and our changes in one diff; "committed as the baseline before round" is a pure save and doesn't count
MIXED_MSG = re.compile(r"\bmarkup\b|(?<!baseline )before round|carries round|; Top 10 carries|final drafts before going live", re.I)
# messages that mention his work without being his save
NOT_KEVIN = re.compile(r"an editor save rewrote|as the editor saves it|editor save had left|from the editor save\)|"
                       r"nav map alt|map labels moved|Kevin's final-pass photos|Kevin's (?:outline|arrangement|Armenia structure)|"
                       r"carrying over Kevin's|rewritten in Kevin's|Kevin's accepted launch proofread|Kevin's to-do answers", re.I)

ABBREV = re.compile(r"(?:\b(?:St|Mt|Dr|Mr|Mrs|Ms|vs|Sv|No|approx|etc|ca|Blvd|Ave|Rd)|\b[A-Z]|e\.g|i\.e)\.$")
SENT_END = re.compile(r"(?<=[.!?])[\"”')\]]*\s+(?=[A-Z\"“(\[\d])")
PHOTO_ONLY = re.compile(r"^\W*\[?\s*photos?\b.*$", re.I)
LEAD_NUM = re.compile(r"^\s*(?:\d+(?:[–-]\d+)?[.)]?\s+)")
MIN_OVERLAP = 0.55       # a chain interval below this word overlap was rewritten, not edited


def sentences(text):
    parts, out, pos = [], [], 0
    for m in SENT_END.finditer(text):
        parts.append(text[pos:m.start()] + text[m.start():m.end()].rstrip())
        pos = m.end()
    parts.append(text[pos:])
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and ABBREV.search(out[-1]):
            out[-1] += " " + p
        else:
            out.append(p)
    return out


def article_key(path):
    parts = [x for x in path.replace("\\", "/").split("/") if x and x not in ROOT_PARTS]
    return "__".join(re.sub(r"[^A-Za-z0-9-]+", "-", x.lstrip(".").rsplit(".", 1)[0]) for x in parts[-2:])


def plain(s):
    from html import unescape
    return compare_key(unescape(re.sub(r"<[^>]+>", "", s or "")))


def iso_utc(unix):
    return datetime.fromtimestamp(int(unix), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------------------------- the round changes, for every article
def _round_num(slug):
    m = re.search(r"-r(\d+)$", slug)
    return int(m.group(1)) if m else 1


def load_rounds():
    """{key: [round dicts in round order]}, each with its changes as (find, replace) and the baseline file."""
    by_key = {}
    extra = {}                       # sentences a cut moved into another article
    for f in sorted(glob.glob(os.path.join(REDLINE, "*", "proposal.json"))):
        d = os.path.dirname(f)
        try:
            with open(f, encoding="utf-8") as fh:
                p = json.load(fh)
        except Exception:
            continue
        if not isinstance(p, dict) or not p.get("article"):
            continue
        key = article_key((p.get("article_dir") or "") + "/" + p["article"])
        pairs = []
        for c in p.get("changes", []):
            if not isinstance(c, dict):
                continue
            if c.get("kind") == "cut":
                pairs.append((c.get("sentence") or "", c.get("bridge_before") or ""))
                dest = c.get("dest_file")
                if dest:
                    dk = article_key((p.get("article_dir") or "") + "/" + dest)
                    if c.get("dest_replaces"):
                        extra.setdefault(dk, []).append((c["dest_replaces"], c.get("sentence") or ""))
                    elif c.get("dest_after"):
                        extra.setdefault(dk, []).append((c["dest_after"], c["dest_after"] + " " + (c.get("sentence") or "")))
            else:
                pairs.append((c.get("find") or c.get("old") or "", c.get("replace") or c.get("new") or ""))
        pairs = [(a, b) for a, b in pairs if a]
        base = os.path.join(d, "baseline", p["article"])
        try:
            with open(os.path.join(d, "decisions.json"), encoding="utf-8") as fh:
                dec = json.load(fh)
        except Exception:
            dec = {}
        by_key.setdefault(key, []).append({
            "slug": os.path.basename(d), "num": _round_num(os.path.basename(d)), "pairs": pairs,
            "baseline": base if os.path.exists(base) else None, "decisions": dec if isinstance(dec, dict) else {},
            "t": os.path.getmtime(base) if os.path.exists(base) else os.path.getmtime(f),
        })
    for k in by_key:
        by_key[k].sort(key=lambda r: r["num"])
    for k, pairs in extra.items():
        if k in by_key:
            by_key[k][0]["pairs"].extend(pairs)
    return by_key


def apply_round_changes(html_before, html_after, pairs):
    """`html_before` with every round change that actually landed in `html_after`. Self-verifying: a change counts only if
    its new text is in the save (or, for a deletion, its old text is gone), so rejected or stale changes do nothing."""
    after_plain = plain(html_after)
    out = html_before
    for find, rep in pairs:
        if find not in out:
            continue
        pr = plain(rep)
        if pr:
            if pr not in after_plain:
                continue
        elif plain(find) and plain(find) in after_plain:
            continue
        out = out.replace(find, rep, 1)
    return out


# ---------------------------------------------------------------- diffing
def _align(unA, unB, threshold):
    """Order-preserving best pairing of two lists of blocks by word similarity."""
    n, m = len(unA), len(unB)
    if not n or not m:
        return []
    sim = [[0.0] * m for _ in range(n)]
    for i, a in enumerate(unA):
        wa = a["key"].split()
        for j, b in enumerate(unB):
            wb = b["key"].split()
            if not (set(wa) & set(wb)):
                continue
            sim[i][j] = difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio()
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            take = (sim[i][j] + best[i + 1][j + 1]) if sim[i][j] >= threshold else -1
            best[i][j] = max(take, best[i + 1][j], best[i][j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        if sim[i][j] >= threshold and abs(best[i][j] - (sim[i][j] + best[i + 1][j + 1])) < 1e-9:
            out.append((i, j, sim[i][j]))
            i += 1
            j += 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def diff_blocks(blocks_a, blocks_b):
    """[(a_block|None, b_block|None)] for every block that changed; whole-block moves are dropped."""
    A = [dict(b, key=compare_key(b["text"])) for b in blocks_a]
    B = [dict(b, key=compare_key(b["text"])) for b in blocks_b]
    sm = difflib.SequenceMatcher(None, [x["key"] for x in A], [x["key"] for x in B], autojunk=False)
    pairs, un_a, un_b = [], [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        ga, gb = A[i1:i2], B[j1:j2]
        taken_a, taken_b = set(), set()
        for i, j, _ in _align(ga, gb, 0.45):
            pairs.append((ga[i], gb[j]))
            taken_a.add(i)
            taken_b.add(j)
        un_a += [x for i, x in enumerate(ga) if i not in taken_a]
        un_b += [x for j, x in enumerate(gb) if j not in taken_b]
    # moved whole, or moved and lightly edited
    keys_b = {}
    for x in un_b:
        keys_b.setdefault(x["key"], []).append(x)
    rest_a = []
    for x in un_a:
        if keys_b.get(x["key"]):
            un_b.remove(keys_b[x["key"]].pop())
        else:
            rest_a.append(x)
    un_a = rest_a
    used = set()
    for x in list(un_a):
        best, bs = None, 0.8
        for k, y in enumerate(un_b):
            if k in used or len(x["key"]) < 40:
                continue
            r = difflib.SequenceMatcher(None, x["key"].split(), y["key"].split(), autojunk=False).ratio()
            if r > bs:
                best, bs = k, r
        if best is not None:
            used.add(best)
            pairs.append((x, un_b[best]))
            un_a.remove(x)
    un_b = [y for k, y in enumerate(un_b) if k not in used]
    pairs += [(x, None) for x in un_a] + [(None, y) for y in un_b]
    return pairs


def _word_changes(a, b):
    wa, wb = a.split(), b.split()
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def hunks_of(a_block, b_block, full_a, full_b):
    """Sentence-level hunks [(before, after, block)] of one changed block."""
    if a_block is None or b_block is None:
        block = b_block or a_block
        kept = []
        other = full_a if a_block is None else full_b
        for s in sentences(block["text"]):
            if compare_key(s) not in other:          # a sentence that merely moved to another block is no edit
                kept.append(s)
        if not kept:
            return []
        text = " ".join(kept)
        return [("", text, block)] if a_block is None else [(text, "", block)]
    sa, sb = sentences(a_block["text"]), sentences(b_block["text"])
    ka, kb = [compare_key(s) for s in sa], [compare_key(s) for s in sb]
    out = []
    sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        ga = sa[i1:i2]
        gb = sb[j1:j2]
        # a sentence that moved inside the paragraph (or to another one) is not typing
        ga = [s for s in ga if compare_key(s) not in full_b]
        gb = [s for s in gb if compare_key(s) not in full_a]
        if not ga and not gb:
            continue
        # one-for-one when the counts agree and each pair resembles its partner; otherwise one hunk for the span
        if len(ga) == len(gb) and len(ga) > 1 and all(
                difflib.SequenceMatcher(None, compare_key(x).split(), compare_key(y).split(), autojunk=False).ratio() > 0.5
                for x, y in zip(ga, gb)):
            out += [(x, y, b_block) for x, y in zip(ga, gb)]
        else:
            out.append((" ".join(ga), " ".join(gb), b_block))
    return out


def keep_hunk(before, after, block):
    ka, kb = compare_key(before), compare_key(after)
    if ka == kb:
        return False
    if not re.search(r"[A-Za-z0-9]", ka + kb):
        return False
    if PHOTO_ONLY.match(ka or kb) and len(ka or kb) < 60:
        return False
    if LEAD_NUM.sub("", ka) == LEAD_NUM.sub("", kb):      # a renumbered heading
        return False
    if block["tag"] in ("h2", "h3", "h4") and re.sub(r"\W+", "", ka.lower()) == re.sub(r"\W+", "", kb.lower()):
        return False
    return True


def diff_versions(html_a, html_b):
    """Sentence-level hunks of the prose between two HTML versions."""
    ba, bb = blocks_of(html_a), blocks_of(html_b)
    full_a = {compare_key(s) for b in ba for s in sentences(b["text"])} | {compare_key(b["text"]) for b in ba}
    full_b = {compare_key(s) for b in bb for s in sentences(b["text"])} | {compare_key(b["text"]) for b in bb}
    out = []
    for a, b in diff_blocks(ba, bb):
        for before, after, block in hunks_of(a, b, full_a, full_b):
            if keep_hunk(before, after, block):
                out.append({"before": before, "after": after, "section": block.get("section") or "", "sub": block.get("sub") or "",
                            "tag": block["tag"]})
    return out, ba, bb


def overlap(blocks_a, blocks_b):
    wa = [w for b in blocks_a for w in compare_key(b["text"]).split()]
    wb = [w for b in blocks_b for w in compare_key(b["text"]).split()]
    if not wa or not wb:
        return 0.0
    # a multiset overlap: order-blind so a moved section is not called a rewrite, and cheap on 7,000-word pages
    from collections import Counter
    ca, cb = Counter(wa), Counter(wb)
    return 2.0 * sum((ca & cb).values()) / (len(wa) + len(wb))


# ---------------------------------------------------------------- git
class Blobs:
    """A long-lived `git cat-file --batch`: a few hundred 200 KB versions without a process each."""

    def __init__(self):
        self.p = subprocess.Popen(["git", "-C", DRAFTS, "cat-file", "--batch"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE)

    def read(self, sha, path):
        self.p.stdin.write(f"{sha}:{path}\n".encode("utf-8"))
        self.p.stdin.flush()
        head = self.p.stdout.readline().decode("utf-8", "replace").split()
        if len(head) < 3 or head[-1] == "missing":
            return None
        size = int(head[2])
        data = self.p.stdout.read(size)
        self.p.stdout.read(1)
        return data.decode("utf-8", "replace")

    def close(self):
        self.p.stdin.close()
        self.p.wait()


def git_history():
    """[{sha, parent_sha, t, msg, files: [path]}] oldest first, html files touched (no renames: a move is D + A)."""
    out = subprocess.run(["git", "-C", DRAFTS, "log", "--reverse", "--no-renames", "--name-status", "-z",
                          "--format=%x01%H%x02%P%x02%ct%x02%B%x03"], stdout=subprocess.PIPE, check=True).stdout
    commits = []
    for chunk in out.decode("utf-8", "replace").split("\x01")[1:]:
        head, _, rest = chunk.partition("\x03")
        sha, parents, t, msg = head.split("\x02", 3)
        fields = [x for x in rest.strip("\n\0").split("\0") if x != ""]
        files, i = [], 0
        while i + 1 < len(fields):
            st, path = fields[i].strip(), fields[i + 1]
            i += 2
            if st[:1] in "AM" and path.endswith(".html"):
                files.append(path)
        commits.append({"sha": sha, "parent": (parents.split() or [None])[0], "t": int(t), "msg": msg.strip(), "files": files})
    return commits


def classify(msg):
    subject = msg.split("\n", 1)[0]
    if NOT_KEVIN.search(subject) and not KEVIN_MSG.search(NOT_KEVIN.sub("", subject)):
        return "ours"
    if KEVIN_MSG.search(subject):
        return "mixed" if MIXED_MSG.search(subject) else "kevin"
    return "ours"


def skip_path(path):
    return bool(re.search(r"/_content/|\.Archive/[^/]*-20\d\d-\d\d/|^Archived Articles/", path))


def timeline():
    """{key: [version dicts oldest first]}; a version is one commit's copy of the article."""
    by_key = {}
    for c in git_history():
        seen = set()
        for path in c["files"]:
            if skip_path(path):
                continue
            key = article_key(path)
            if key in seen:
                continue
            seen.add(key)
            by_key.setdefault(key, []).append({"sha": c["sha"], "path": path, "t": c["t"], "msg": c["msg"], "class": classify(c["msg"])})
    return by_key


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("version") == EXTRACT_VERSION:
            return c
    except Exception:
        pass
    return {"version": EXTRACT_VERSION, "git": {}, "ours": {}, "chain": {}}


def _save_cache(c):
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
    os.replace(tmp, CACHE)


def _rounds_fp(rounds):
    h = hashlib.sha1()
    for r in rounds:
        h.update(r["slug"].encode())
        h.update(str(len(r["pairs"])).encode())
    return h.hexdigest()[:10]


# ---------------------------------------------------------------- our own scripts
GRAM = re.compile(r"[^a-z0-9$€%]+")


def _grams(text):
    w = [x for x in GRAM.split(compare_key(text).lower()) if x]
    return [" ".join(w[i:i + 5]) for i in range(len(w) - 4)]


def script_index():
    """5-word shingle -> earliest time one of our scripts under .tmp held it. Round-chain intervals have no commit message
    to say who typed what, but a rewrite of ours left its text in a build script (blue-cruise-r1 -> r4: 38 of 40 hunks)."""
    index = {}
    for root, dirs, names in os.walk(os.path.join(ROOT, ".tmp")):
        dirs[:] = [d for d in dirs if d not in ("_comments", "__pycache__", "_disk", "baseline", "before-apply", "kevin_reviewer")]
        for n in names:
            if not n.endswith(".py"):
                continue
            path = os.path.join(root, n)
            try:
                if os.path.getsize(path) > 3_000_000:
                    continue
                m = os.path.getmtime(path)
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            for g in _grams(text):
                if m < index.get(g, 1e18):
                    index[g] = m
    return index


def script_share(text, t, index):
    gs = _grams(text)
    if len(gs) < 4:
        return 0.0
    return sum(1 for g in gs if index.get(g, 1e18) < t) / len(gs)


def mine(progress=None):
    """Every edit record. Results are cached per commit, so a rebuild only reads new versions."""
    rounds = load_rounds()
    tl = timeline()
    cache = _load_cache()
    blobs = Blobs()
    sidx = None                # built only when a new chain interval needs it (about 20 s)
    raw = []                   # hunks before the ours-sentence filter and the de-duplication
    ours_sentences = {}        # key -> {compare_key(sentence)} introduced by a commit of ours
    ours_removed = {}          # key -> sentences a commit of ours took out
    stats = {"versions": 0, "skipped_rewrite_intervals": []}

    def version_text(v):
        return blobs.read(v["sha"], v["path"])

    for key, versions in tl.items():
        rd = rounds.get(key, [])
        fp = _rounds_fp(rd)
        pairs = [p for r in rd for p in r["pairs"]]
        last_by_path = {}
        prev = None
        ours = ours_sentences.setdefault(key, set())
        ours_gone = ours_removed.setdefault(key, set())
        for v in versions:
            stats["versions"] += 1
            p = last_by_path.get(v["path"]) or prev
            last_by_path[v["path"]] = v
            prev = v
            if p is None:
                continue
            ck = f"{key}|{v['sha']}|{fp}"
            if v["class"] == "ours":
                if ck not in cache["ours"]:
                    a_txt, b_txt = version_text(p), version_text(v)
                    new = gone = []
                    if a_txt and b_txt:
                        sa = {compare_key(s) for b in blocks_of(a_txt) for s in sentences(b["text"])}
                        sb = {compare_key(s) for b in blocks_of(b_txt) for s in sentences(b["text"])}
                        new, gone = sorted(sb - sa), sorted(sa - sb)
                    cache["ours"][ck] = {"new": new, "gone": gone}
                ours.update(cache["ours"][ck]["new"])
                ours_gone.update(cache["ours"][ck]["gone"])
                continue
            if ck not in cache["git"]:
                a_txt, b_txt = version_text(p), version_text(v)
                found = []
                if a_txt and b_txt:
                    handed = apply_round_changes(a_txt, b_txt, pairs)
                    found, _, _ = diff_versions(handed, b_txt)
                cache["git"][ck] = found
            for h in cache["git"][ck]:
                raw.append(dict(h, key=key, t=v["t"], sha=v["sha"][:7], source="git", provenance=v["class"], msg=v["msg"].split("\n")[0][:100]))

        # the round chain
        chain = [r for r in rd if r["baseline"]]
        for i, r in enumerate(chain):
            nxt = chain[i + 1] if i + 1 < len(chain) else None
            ck = f"{key}|{r['slug']}|{nxt['slug'] if nxt else 'END:' + (versions[-1]['sha'] if versions else '')}"
            if ck not in cache["chain"]:
                try:
                    with open(r["baseline"], encoding="utf-8") as f:
                        a_txt = f.read()
                    if nxt:
                        with open(nxt["baseline"], encoding="utf-8") as f:
                            b_txt = f.read()
                    else:
                        b_txt = version_text(versions[-1]) if versions else None
                except Exception:
                    b_txt = None
                entry = {"hunks": [], "overlap": None, "t": None}
                end = None
                if b_txt:
                    handed = apply_round_changes(a_txt, b_txt, r["pairs"])
                    found, ba, bb = diff_versions(handed, b_txt)
                    ov = overlap(ba, bb)
                    end = int(os.path.getmtime(nxt["baseline"])) if nxt else (versions[-1]["t"] if versions else None)
                    entry = {"hunks": found if ov >= MIN_OVERLAP else [], "overlap": round(ov, 3), "t": end}
                if entry["hunks"] and sidx is None:
                    sidx = script_index()
                for h in entry["hunks"]:         # the share of this new text that was already in a script of ours
                    h["script"] = round(script_share(h["after"], end or 0, sidx), 2) if h["after"] else 0.0
                cache["chain"][ck] = entry
            entry = cache["chain"][ck]
            if entry["overlap"] is not None and entry["overlap"] < MIN_OVERLAP:
                stats["skipped_rewrite_intervals"].append(f"{r['slug']}->{nxt['slug'] if nxt else 'END'} ({entry['overlap']})")
            for h in entry["hunks"]:
                # a gap in the round numbers (r1 -> r4) means rounds and rewrites we have no record of happened in between
                gap = bool(nxt) and nxt["num"] - r["num"] > 1
                raw.append(dict(h, key=key, t=entry["t"] or 0, sha="", source="chain", provenance="mixed" if gap else "kevin",
                                last_round=(nxt or r)["num"],
                                msg=f"{r['slug']} -> {nxt['slug'] if nxt else 'latest'}"))
    blobs.close()
    _save_cache(cache)

    # what a round proposed is never his, however it arrived; only rounds that existed by then can have supplied it
    def from_round(h, kb, ka):
        for r in rounds.get(h["key"], []):
            if h["source"] == "chain":
                if r["num"] > h["last_round"]:
                    continue
            elif r["t"] > h["t"] + 3600:
                continue
            for find, rep in r["pairs"]:
                if kb and kb in compare_key(plain(rep)):
                    return True
                if not kb and ka and ka in compare_key(plain(find)):
                    return True
        return False

    recs, seen = [], {}
    # git first (it knows the date and commit); oldest first so a change keeps the date he made it
    raw.sort(key=lambda h: (h["source"] != "git", h["t"]))
    dropped = {"ours": 0, "script": 0, "round": 0, "dup": 0, "heading": 0}
    for h in raw:
        ka, kb = compare_key(h["before"]), compare_key(h["after"])
        ours = ours_sentences.get(h["key"], set())
        s_after = [compare_key(s) for s in sentences(h["after"])]
        if s_after and sum(1 for s in s_after if s in ours) >= 0.6 * len(s_after):
            dropped["ours"] += 1
            continue
        s_before = [compare_key(s) for s in sentences(h["before"])]
        if not s_after and s_before and all(s in ours_removed.get(h["key"], ()) for s in s_before):
            dropped["ours"] += 1
            continue
        if h.get("script", 0) >= 0.5:         # the text is in a script of ours written before this interval closed
            dropped["script"] += 1
            continue
        if from_round(h, kb, ka):
            dropped["round"] += 1
            continue
        if h["tag"] in ("h2", "h3", "h4") and (not h["before"] or not h["after"]):
            dropped["heading"] += 1            # a section appearing or going is structure, not his sentences
            continue
        # a chain hunk usually repeats a git hunk, or spans it with a wider sentence window
        sig = (h["key"], kb or ("cut:" + ka))
        if sig in seen:
            dropped["dup"] += 1
            continue
        seen[sig] = True
        hid = hashlib.sha1(f"{h['key']}\n{ka}\n{kb}".encode("utf-8")).hexdigest()[:12]
        section = h["section"]
        rec = {
            "kind": "edit", "id": f"edit:{h['key']}/{hid}", "key": h["key"], "article": h["key"].split("__")[-1],
            "article_type": article_type(h["key"]),
            "section": section or "Intro", "subsection": h["sub"],
            "section_kind": section_kind(h["sub"] or section, article_type(h["key"]), section),
            "block": h["tag"],
            "before": h["before"], "after": h["after"],
            "change": "added" if not h["before"] else ("cut" if not h["after"] else "reworded"),
            "words_changed": _word_changes(h["before"], h["after"]),
            "created": iso_utc(h["t"]) if h["t"] else "",
            "source": h["source"], "provenance": h["provenance"], "commit": h["sha"], "origin": h["msg"],
        }
        recs.append(rec)
    stats["dropped"] = dropped
    stats["raw"] = len(raw)
    return recs, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--out", help="UTF-8 file for the sample")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    import collections
    import random
    recs, stats = mine()
    lines = [f"{len(recs)} edit records; {json.dumps(stats, ensure_ascii=False)[:1500]}"]
    for field in ("provenance", "source", "change", "article_type"):
        lines.append(f"{field}: {collections.Counter(r[field] for r in recs).most_common()}")
    lines.append("by article: " + str(collections.Counter(r["key"] for r in recs).most_common(40)))
    if a.sample:
        random.seed(a.seed)
        for r in random.sample(recs, min(a.sample, len(recs))):
            lines.append(f"\n[{r['provenance']}/{r['source']}/{r['change']}] {r['key']} > {r['section'][:30]} > {r['subsection'][:30]} ({r['created'][:10]}, {r['commit']}) {r['origin'][:70]}")
            lines.append("  - " + r["before"][:420])
            lines.append("  + " + r["after"][:420])
    text = "\n".join(lines) + "\n"
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main()
