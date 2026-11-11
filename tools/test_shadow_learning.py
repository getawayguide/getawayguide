#!/usr/bin/env python3
"""Tests for the Shadow learning tools (shadow_signals, finished_articles, rule_test, shadow_lessons, shadow_scorecard).

Everything is built in a scratch folder; the real comment store, rounds and articles are never read or written
(memory feedback_test_cleanup_never_in_real_folders). The exam list is read from the real split.json, read-only.

    python tools/test_shadow_learning.py
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import finished_articles as fa
import rule_test as rt
import shadow_lessons as sl
import shadow_scorecard as sc
import shadow_signals as ss

passed = failed = 0
TMP = tempfile.mkdtemp(prefix="shadow_learning_test_")
COMMENTS = os.path.join(TMP, "comments")
REDLINE = os.path.join(TMP, "redline")
SITE = os.path.join(TMP, "site")
_n = [0]


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = passed + bool(ok), failed + (not ok)
    if not ok:
        sys.stdout.buffer.write(("FAIL %s %s\n" % (name, detail)).encode("utf-8"))


def reset():
    for d in (COMMENTS, REDLINE, SITE):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)


def store(key, threads, deleted=()):
    with open(os.path.join(COMMENTS, key + ".json"), "w", encoding="utf-8") as f:
        json.dump({"threads": threads, "deleted": list(deleted)}, f)


def kevin_thread(tid="c1", resolved=True, text="reword this", created="2026-11-01T10:00:00.000Z", quote="town", replies=()):
    return {"id": tid, "author": "Kevin", "text": text, "created": created, "resolved": resolved, "resolved_by": "user" if resolved else None,
            "anchor": {"quote": quote}, "replies": list(replies)}


def shadow_thread(tid="sr-aaaaaaaa", category="wording", replies=(), resolved=False, resolved_by=None, text="reword", created="2026-11-02T10:00:00.000Z"):
    return {"id": tid, "author": ss.SHADOW, "text": "[Shadow reviewer] %s (confidence 0.50)" % text, "created": created, "resolved": resolved,
            "resolved_by": resolved_by, "anchor": {"quote": "x"}, "shadow": {"category": category}, "replies": list(replies)}


def kreply(text, created="2026-11-02T12:00:00.000Z"):
    return {"author": "Kevin", "text": text, "created": created}


PAGE = '<html><body><div class="article-body"><p>INTRO. Skopje is great, and I loved it.</p><h2 id="a">Getting There</h2><p>By bus from Sofia is easy.</p></div></body></html>'


def article(key, body=PAGE):
    country, page = key.split("__")
    d = os.path.join(SITE, country)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, page + ".html"), "w", encoding="utf-8") as f:
        f.write(body)
    return os.path.join(d, page + ".html")


def add_round(slug, article_path, changes=(), decisions=None, applied=True):
    d = os.path.join(REDLINE, slug)
    os.makedirs(d, exist_ok=True)
    p = {"slug": slug, "article_dir": os.path.dirname(article_path), "article": os.path.basename(article_path), "changes": list(changes)}
    with open(os.path.join(d, "proposal.json"), "w", encoding="utf-8") as f:
        json.dump(p, f)
    if decisions is not None:
        with open(os.path.join(d, "decisions.json"), "w", encoding="utf-8") as f:
            json.dump(decisions, f)
    if applied:
        with open(os.path.join(d, "applied.json"), "w", encoding="utf-8") as f:
            f.write("{}")


def scan(state=None):
    return {r["key"]: r for r in fa.scan(state or {}, COMMENTS, REDLINE, SITE)}


STATE_DONE = {"last_run": "2026-11-01T00:00:00+00:00", "articles": {}}

# ---- shadow_signals --------------------------------------------------------------------------------------------------
for text, want in (("yes", "yes"), ("Yes, but not condense", "yes"), ("agree", "yes"), ("good catch", "yes"), ("good comment, but let's remove talk of the airbnb", "yes"),
                   ("not a bad comment, but the friend stays", "yes"), ("no", "no"), ("no, but right idea on changing the photo", "no"), ("this wording is fine", "no"),
                   ("that is fine", "no"), ("fine to repeat here.", "no"), ("doesn't conflict, I can have multiple favorites", "no"), ("ok to skip this one", "no"),
                   ("no it is far away enough", "no"), ("I think you have a it switched - bullet is right", "no"), ("2 hours", "nuance"),
                   ("should be hostel world - update ohrid guide", "nuance"), ("seems fine, but provide different options", "nuance"), ("maybe \"came out to\"", "nuance"),
                   ("don't include this - personal experience", "nuance"), ("can't see what this refers to", "unclear"), ("is this supposed to be antalya?", "unclear"), ("", "unclear")):
    check("verdict: " + text, ss.reply_verdict(text) == want, ss.reply_verdict(text))

for cat, fam in (("link", "links"), ("link: wikipedia person", "links"), ("link / lookup: unnamed place", "add-detail"), ("fill: unnamed place lookup", "add-detail"),
                 ("currency", "prices"), ("fact check: price", "prices"), ("fill: price", "prices"), ("fact check", "facts"), ("source-conflict", "facts"), ("fact: journal conflict", "facts"),
                 ("fact: journal + photo conflict", "facts"), ("fact / coverage: famous sight left out", "add-detail"), ("repetition: same verdict", "repetition"), ("wording: cross-article repetition", "repetition"),
                 ("wording: weak word", "wording"), ("title", "wording"), ("expand: sight history", "add-detail"), ("content", "add-detail"), ("overview-trim", "structure"), ("structure: move", "structure"),
                 ("media: route map", "maps"), ("map: day trip", "maps"), ("media: missing photo", "photos"), ("photo", "photos"), ("media: day-trip map/photo", "photos"), ("journal / photo lookup + structure", "add-detail"),
                 ("something new", "other"), (None, "other")):
    check("family: %s" % cat, ss.family(cat) == fam, ss.family(cat))

t = shadow_thread(replies=[kreply("no thanks")], resolved=True, resolved_by="user")
check("answer: a reply beats the tick", ss.answer(t)["verdict"] == "no")
check("answer: a tick with no reply is apart", ss.answer(shadow_thread(resolved=True, resolved_by="user"))["verdict"] == "tick")
check("answer: an old resolve with no stamp is his click", ss.answer(shadow_thread(resolved=True))["verdict"] == "tick")
check("answer: an auto-close isn't his", ss.answer(shadow_thread(resolved=True, resolved_by="round"))["verdict"] == "auto")
check("answer: retired by us isn't his", ss.answer(shadow_thread(resolved=True, resolved_by="shadow-tightened"))["verdict"] == "auto")
check("answer: open and silent", ss.answer(shadow_thread())["verdict"] == "unanswered")
check("answer: deleted", ss.answer(shadow_thread(tid="sr-1"), {"sr-1"})["verdict"] == "deleted")
t2 = shadow_thread(replies=[kreply("can't see this"), kreply("yes")])
check("answer: a later yes beats an unclear first reply", ss.answer(t2)["verdict"] == "yes")
t3 = shadow_thread(replies=[{"author": "Claude", "text": "yes"}])
check("answer: only Kevin's replies count", ss.answer(t3)["verdict"] == "unanswered")
tl = ss.tally(["yes", "nuance", "no", "unanswered", "tick", "auto"])
check("tally: rate over typed answers only", tl["rate"] == 2 / 3 and tl["answered"] == 3 and tl["unanswered"] == 1, tl)
check("tally: nothing answered is no rate, not 0", ss.tally(["unanswered"])["rate"] is None and ss.pct(None) == "n/a")

# ---- finished_articles --------------------------------------------------------------------------------------------------
reset()
art = article("greece__meteora")
store("greece__meteora", [kevin_thread()])
r = scan(STATE_DONE)["greece__meteora"]
check("finished: reviewed, closed, no round", r["finished"] and r["status"] == "new", r)
check("finished: no state file means it's the backlog", scan({})["greece__meteora"]["status"] == "backlog")
store("greece__meteora", [kevin_thread(), kevin_thread("c2", resolved=False)])
r = scan(STATE_DONE)["greece__meteora"]
check("finished: an open thread of his blocks it", not r["finished"] and r["status"] == "open-threads", r)
store("greece__meteora", [kevin_thread(), shadow_thread()])
check("finished: an open Shadow thread blocks it too", scan(STATE_DONE)["greece__meteora"]["status"] == "open-threads")
store("greece__meteora", [kevin_thread(), shadow_thread()], deleted=["sr-aaaaaaaa"])
check("finished: a deleted thread doesn't count as open", scan(STATE_DONE)["greece__meteora"]["finished"])
store("greece__meteora", [shadow_thread(resolved=True, resolved_by="user")])
r = scan(STATE_DONE)["greece__meteora"]
check("finished: a tick alone isn't a review by him", r["status"] == "not-reviewed", r)
store("greece__meteora", [shadow_thread(resolved=True, replies=[kreply("yes")])])
check("finished: a reply alone is a review", scan(STATE_DONE)["greece__meteora"]["finished"])

store("greece__meteora", [kevin_thread()])
add_round("meteora-r1", art, applied=False)
r = scan(STATE_DONE)["greece__meteora"]
check("finished: a pending round blocks it", r["status"] == "pending-round" and r["pending_rounds"] == ["meteora-r1"], r)
add_round("meteora-r1", art, applied=True)
check("finished: an applied round doesn't", scan(STATE_DONE)["greece__meteora"]["status"] == "new")
art2 = article("greece__santorini")
store("greece__santorini", [kevin_thread()])
add_round("x-r1", art, applied=False, changes=[{"id": "c1", "kind": "cut", "sentence": "s", "dest_file": "santorini.html"}])
check("finished: a pending round moving text into it blocks it", scan(STATE_DONE)["greece__santorini"]["status"] == "pending-round", scan(STATE_DONE)["greece__santorini"])
shutil.rmtree(os.path.join(REDLINE, "x-r1"))

reset()
article("turkiye__antalya")
store("turkiye__antalya", [kevin_thread()])
r = scan(STATE_DONE)["turkiye__antalya"]
check("exam: never newly finished", r["status"] == "exam")
check("exam: never in the finished pool", fa.finished_articles(COMMENTS, REDLINE, SITE) == [])

reset()
article("greece__meteora")
store("greece__meteora", [kevin_thread()])
state = fa.record(fa.scan({}, COMMENTS, REDLINE, SITE), {"articles": {}})
check("record: the first run is only a baseline, nothing queued", not state["articles"]["greece__meteora"]["queued"] and state.get("last_run"))
check("record: unchanged after that is seen, not new", scan(state)["greece__meteora"]["status"] == "seen")
article("greece__meteora", PAGE.replace("Skopje", "Thessaloniki"))
r = scan(state)["greece__meteora"]
check("record: a change makes it new", r["status"] == "new" and r["changed"], r)
state = fa.record(fa.scan(state, COMMENTS, REDLINE, SITE), state)
check("record: a real tick queues it", state["articles"]["greece__meteora"]["queued"])
r = scan(state)["greece__meteora"]
check("record: still new while queued, though unchanged", r["status"] == "new" and r["queued"] and not r["changed"], r)
state = fa.mark_practiced(["greece__meteora"], fa.scan(state, COMMENTS, REDLINE, SITE), state)
check("practiced: off the queue", not state["articles"]["greece__meteora"]["queued"] and state["articles"]["greece__meteora"]["practiced"])
check("practiced: no longer new", scan(state)["greece__meteora"]["status"] == "practiced")
article("greece__meteora", PAGE.replace("Skopje", "Athens"))
check("practiced: a later change is listed apart, not re-queued", scan(state)["greece__meteora"]["status"] == "practiced-changed")
try:
    fa.mark_practiced(["nope__nope"], fa.scan(state, COMMENTS, REDLINE, SITE), state)
    check("practiced: an unknown key is refused", False)
except ValueError as e:
    check("practiced: an unknown key is refused", "nope__nope" in str(e))
before = sorted(os.listdir(SITE))
fa.scan(state, COMMENTS, REDLINE, SITE)
check("scan: a dry run writes nothing", sorted(os.listdir(SITE)) == before)
check("render: says what's new", "Newly finished: 0" in fa.render(list(scan(state).values())))

# ---- rule_test ----------------------------------------------------------------------------------------------------------
reset()
PAGE_B = '<html><body><div class="article-body"><p>Another INTRO here.</p><h2 id="a">Where to Eat</h2><p>Great burek in the old bazaar.</p></div></body></html>'
for k, b in (("armenia__yerevan", PAGE), ("armenia__gyumri", PAGE_B), ("kosovo__prizren", PAGE), ("kosovo__top-10-kosovo", PAGE_B)):
    article(k, b)
    store(k, [kevin_thread()])
pool = fa.finished_articles(COMMENTS, REDLINE, SITE)


def args(**kw):
    base = dict(pattern=[], absent=[], type=[], scope="all", min=1, source=[], case=False, json=False, sample=False, per_article=2, seed=7, out=None)
    base.update(kw)
    return argparse.Namespace(**base)


res = rt.run_test(args(pattern=[r"\bIntro\b"]), pool)
check("rule: conform counted over all finished", res["conforming"] == 4 and res["tested"] == 4 and res["passes"], res)
res = rt.run_test(args(pattern=[r"burek"]), pool)
check("rule: two others conform passes", res["conforming"] == 2 and res["passes"], res)
res = rt.run_test(args(pattern=[r"burek"], source=["kosovo__top-10-kosovo"]), pool)
check("rule: the source article can't vouch for its own rule", res["conforming"] == 1 and not res["passes"], res)
res = rt.run_test(args(pattern=[r"burek"], type=["city-guide"]), pool)
check("rule: by type", res["by_type"]["city-guide"]["conform"] == 1 and "top-10" not in res["by_type"], res["by_type"])
res = rt.run_test(args(pattern=[r"Sofia"], scope="section:getting-there"), pool)
check("rule: a scope missing from an article is n/a, not a failure", res["by_type"]["top-10"]["no_such_scope"] == 1 and res["conforming"] == 2, res["by_type"])
res = rt.run_test(args(pattern=[r"\bgreat\b"], absent=[r"bazaar"]), pool)
check("rule: an --absent hit fails that article", res["conforming"] == 2 and not any(r["verdict"] == "yes" and r["key"].endswith("gyumri") for r in res["rows"]), res)
res = rt.run_test(args(pattern=[r"INTRO"], scope="intro"), pool)
check("rule: the intro scope stops at the first h2", res["conforming"] == 4, res)
res = rt.run_test(args(pattern=[r"Getting"], scope="intro"), pool)
check("rule: the intro scope leaves the headings out", res["conforming"] == 0, res)
res = rt.run_test(args(pattern=[r"Skopje", r"bus"], min=1), pool)
check("rule: every --pattern has to match", res["conforming"] == 2, res)
res = rt.run_test(args(pattern=[r"great"], min=2), pool)
check("rule: --min", res["conforming"] == 0, res)
sample = rt.run_sample(args(scope="paragraphs", per_article=1), pool)
check("sample: one per article, real passages", len(sample) == 4 and all(s["text"] for s in sample), sample)
check("sample: same seed, same passages", sample == rt.run_sample(args(scope="paragraphs", per_article=1), pool))
check("sample: a pattern narrows it", {s["key"] for s in rt.run_sample(args(pattern=["burek"], per_article=2), pool)} == {"armenia__gyumri", "kosovo__top-10-kosovo"})
check("sample: a heading isn't a passage unless asked", all(s["tag"] != "h2" for s in rt.run_sample(args(per_article=5), pool)))
check("render: says PASS or FAIL with the bar", "PASS: 4 of 4" in rt.render_test(rt.run_test(args(pattern=[r"Intro"]), pool)))
article("turkiye__antalya", PAGE.replace("INTRO", "burek"))
store("turkiye__antalya", [kevin_thread()])
pool2 = fa.finished_articles(COMMENTS, REDLINE, SITE)
check("rule: an exam article never reaches the pool", all(a["key"] != "turkiye__antalya" for a in pool2))

# ---- shadow_lessons ------------------------------------------------------------------------------------------------------
reset()
store("kosovo__prizren", [shadow_thread("sr-aaa11111", "expand: sight history", [kreply("yes")]),
                          shadow_thread("sr-aaa22222", "expand: sight history", [kreply("no")]),
                          shadow_thread("sr-aaa33333", "expand: sight history", resolved=True, resolved_by="round"),
                          shadow_thread("sr-aaa44444", "expand: sight history"),
                          shadow_thread("sr-aaa55555", "wording", resolved=True, resolved_by="user"),
                          shadow_thread("sr-aaa66666", "photo", [kreply("yes")]),
                          shadow_thread("sr-aaa77777", "link", resolved=True, resolved_by="round"),
                          kevin_thread()])
store("kosovo__prishtina", [shadow_thread("sr-bbb11111", "expand: sight history", [kreply("yes, but keep it short")]),
                            shadow_thread("sr-bbb22222", "expand: sight history", [kreply("add the year it was built")]),
                            shadow_thread("sr-bbb33333", "expand: sight history", [kreply("yes")])])
store("turkiye__antalya", [shadow_thread("sr-ccc11111", "expand: sight history", [kreply("yes")])])
stores = ss.load_stores(COMMENTS)
rows = sl.gather(stores, REDLINE)
check("lessons: exam article left out", all(r["key"] != "turkiye__antalya" for r in rows))
check("lessons: photo kinds left out", all(r["category"] != "photo" for r in rows))
cands = sl.candidates(rows)
hist = [c for c in cands if c["kind"] == "expand: sight history"]
check("lessons: a kind he keeps accepting is a candidate", len(hist) == 1 and hist[0]["yes"] == 4 and hist[0]["no"] == 1 and hist[0]["articles"] == 2, cands)
check("lessons: his instruction is carried as his words", hist[0]["his_words"] == ["add the year it was built"], hist)
check("lessons: a round-closed thread isn't a yes", all(c["kind"] != "link" for c in cands))
drafts = {(d["article"], d["pass"]): d for d in sl.per_draft(rows, REDLINE)}
d = drafts[("kosovo__prizren", "2026-11-02")]
check("lessons: findings per draft count only what he confirmed", d["threads"] == 6 and d["confirmed"] == 1 and d["said_no"] == 1 and d["waiting"] == 1, d)

reset()
art = article("kosovo__prizren")
store("kosovo__prizren", [shadow_thread("sr-rrr11111", "link", resolved=True, resolved_by="round"),
                          shadow_thread("sr-rrr22222", "link", resolved=True, resolved_by="round"),
                          shadow_thread("sr-rrr33333", "link", resolved=True, resolved_by="round"),
                          shadow_thread("sr-rrr44444", "link", resolved=True, resolved_by="round"),
                          shadow_thread("sr-rrr55555", "wording", resolved=True, resolved_by="round")])
changes = [{"id": "g01", "kind": "grammar", "note": "Kevin's reply on the Shadow thread sr-rrr11111 ('yes')", "find": "a", "replace": "b"},
           {"id": "g02", "kind": "grammar", "note": "Review loop (sr-rrr22222)", "find": "a", "replace": "b", "origin": "shadow-loop"},
           {"id": "g03", "kind": "grammar", "note": "consent sr-rrr33333", "find": "a", "replace": "b", "origin": "shadow-consent"},
           {"id": "g04", "kind": "grammar", "note": "x sr-rrr44444", "find": "a", "replace": "b", "origin": "draft_check"},
           {"id": "g05", "kind": "grammar", "note": "x", "threads": ["sr-rrr55555"], "find": "a", "replace": "b"}]
add_round("prizren-r1", art, changes, decisions={"g01": True, "g02": True, "g03": True, "g04": True, "g05": False})
sig = sl.round_signals(REDLINE)
check("round signal: an accepted change naming the thread", sig.get("sr-rrr11111") == "accepted", sig)
check("round signal: shadow-loop, shadow-consent and draft_check never count", not any(k in sig for k in ("sr-rrr22222", "sr-rrr33333", "sr-rrr44444")), sig)
check("round signal: a rejected change is a no", sig.get("sr-rrr55555") == "rejected", sig)
rows = sl.gather(ss.load_stores(COMMENTS), REDLINE)
by = {r["thread"]: r["verdict"] for r in rows}
check("lessons: an accepted round change counts as a yes", by["sr-rrr11111"] == "yes", by)
check("lessons: a tool's change stays what it was", by["sr-rrr22222"] == "auto" and by["sr-rrr44444"] == "auto", by)
check("lessons: a rejected round change counts as a no", by["sr-rrr55555"] == "no", by)
add_round("prizren-r2", art, [{"id": "g01", "kind": "grammar", "note": "sr-rrr44444", "find": "a", "replace": "b", "loop_source": "shadow"}], decisions={"g01": True})
check("round signal: a change with loop_source is a tool's", "sr-rrr44444" not in sl.round_signals(REDLINE))

# ---- shadow_scorecard ------------------------------------------------------------------------------------------------------
reset()
store("kosovo__prizren", [
    shadow_thread("sr-1", "link", [kreply("yes", "2026-11-03T10:00:00.000Z")]),
    shadow_thread("sr-2", "link", [kreply("no", "2026-11-03T11:00:00.000Z")]),
    shadow_thread("sr-3", "wording", [kreply("fix this")]),
    shadow_thread("sr-4", "wording", resolved=True, resolved_by="user"),
    shadow_thread("sr-5", "wording"),
    shadow_thread("sr-6", "media: missing photo", [kreply("yes")]),
    kevin_thread("c1", created="2026-11-01T08:00:00.000Z"),
    kevin_thread("c2", created="2026-11-01T09:00:00.000Z"),
    kevin_thread("c3", created="2026-11-03T09:00:00.000Z"),
    kevin_thread("c4", created="2026-11-03T09:30:00.000Z", text="add a photo here"),
], deleted=["sr-9"])
card = sc.build(ss.load_stores(COMMENTS))
o = card["overall"]
check("score: replies count, silence doesn't", o["yes"] == 1 and o["nuance"] == 1 and o["no"] == 1 and o["answered"] == 3 and o["unanswered"] == 1, o)
check("score: rate is yes+instructions out of typed answers", abs(o["rate"] - 2 / 3) < 1e-9, o["rate"])
check("score: a tick is apart, not in the rate", o["tick"] == 1, o)
check("score: photo kinds out of every number", sum(f["yes"] for f in card["families"]) == o["yes"] and not any(f["family"] == "photos" for f in card["families"]), card["families"])
fams = {f["family"]: f for f in card["families"]}
check("score: by family", fams["links"]["yes"] == 1 and fams["links"]["no"] == 1 and fams["wording"]["unanswered"] == 1, fams)
d = {x["key"]: x for x in card["drafts"]}["kosovo__prizren"]
check("score: his own comments, photo ones out", d["own_comments"] == 3, d)
check("score: sittings split by 4 hours, replies included", d["sittings"] == 3, d)
check("score: deleted Shadow threads shown", card["deleted_shadow_threads"] == 1)
check("score: weekly rate uses the reply date", card["weeks"] and card["weeks"][-1]["answered"] == 3, card["weeks"])
check("score: month rollup", card["months"] and card["months"][0]["median_comments"] == 3, card["months"])
txt = sc.render(card)
check("render: top lines say the rate plainly", "He said yes to 67%" in txt and "still waiting" in txt, txt[:400])
check("render: no em dashes in what Kevin reads", "—" not in txt)
check("render: lessons report has no em dash either", "—" not in sl.render(sl.report(sl.gather(ss.load_stores(COMMENTS), REDLINE), REDLINE)))
check("sittings: empty", sc.sittings([]) == 0)
check("sittings: one", sc.sittings(["2026-11-01T08:00:00.000Z", "2026-11-01T10:00:00.000Z"]) == 1)
check("sittings: a gap of over 4 hours", sc.sittings(["2026-11-01T08:00:00.000Z", "2026-11-01T13:00:00.000Z"]) == 2)

shutil.rmtree(TMP, ignore_errors=True)
sys.stdout.buffer.write(("%d passed, %d failed\n" % (passed, failed)).encode("utf-8"))
sys.exit(1 if failed else 0)
