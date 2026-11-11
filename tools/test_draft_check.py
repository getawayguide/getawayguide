#!/usr/bin/env python3
"""Pin down what tools/draft_check.py may report and write, on scratch copies only.

Every draft below is built in a temp folder, and draft_check.REDLINE is pointed at a scratch comment/round store
(memory feedback_test_cleanup_never_in_real_folders), so no real article, thread or round is read or written.

    python tools/test_draft_check.py
"""
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import draft_check as dc

passed = failed = 0
TMP = Path(tempfile.mkdtemp(prefix="draft_check_test_"))
dc.REDLINE = TMP / "redline"
dc.map_pins = lambda rel: {}                     # the real city-map folder stays out of it
_n = [0]

MAPS = "https://www.google.com/maps/place/Skadarlija/@44.8,20.4,17z/data=!1s0x47?hl=en&entry=ttu"
SIBLING_LINKS = [
    ("belgrade.html#food", "Burek"), ("../albania/field-notes.html", "Albanian"), (MAPS, "Greek border"),
    ("https://www.google.com/maps/place/Albanian+Riviera/@40.07,19.76,15z", "Albanian Riviera"), ("../kosovo/field-notes.html", "Kosovo"), ("https://www.uber.com/", "Uber"),
    ("https://bitaksi.com/", "BiTaksi"), ("https://bolt.eu/", "Bolt"), ("../germany/field-notes.html", "Germany"),
    ("../bosnia/field-notes.html", "Bosnia"), ("../bosnia/field-notes.html#sarajevo", "Sarajevo"),
    ("../greece/index.html", "Greece"), (MAPS, "Reuss"), (MAPS, "Skadarlija"), ("../hungary/field-notes.html", "Hungary"),
    ("../macedonia/field-notes.html", "Macedonia"), ("https://www.getyourguide.com/x", "The tour"),
]
SIBLING = '<html><body><div class="article-body"><p>%s</p></div></body></html>' % " ".join(
    '<a href="%s">%s</a>' % (h.replace("&", "&amp;"), t) for h, t in SIBLING_LINKS)


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = passed + bool(ok), failed + (not ok)
    if not ok:
        sys.stdout.buffer.write(("FAIL %s %s\n" % (name, detail)).encode("utf-8"))


def page(body):
    return '<html><body><nav>x</nav><div class="article-body">\n%s\n</div></body></html>' % body


def draft(body, country="serbia", raw=None):
    """a throwaway site: <TMP>/<n>/<country>/{belgrade.html, sibling.html}; returns the draft's path"""
    _n[0] += 1
    d = TMP / str(_n[0]) / country
    d.mkdir(parents=True)
    (d / "sibling.html").write_text(SIBLING, encoding="utf-8")
    p = d / "belgrade.html"
    p.write_bytes(raw if raw is not None else page(body).encode("utf-8"))
    return p


def found(body):
    res = dc.check(str(draft(body)))
    return [x["text"] for x in res["issues"] if x["kind"] == "bare-name"], {f["text"]: f["reason"] for f in res["filtered"]}


def reset_store():
    shutil.rmtree(dc.REDLINE, ignore_errors=True)
    (dc.REDLINE / "_comments").mkdir(parents=True)


def add_round(slug, changes, decisions=None, applied=False):
    d = dc.REDLINE / slug
    d.mkdir(parents=True)
    (d / "proposal.json").write_text(json.dumps({"slug": slug, "article_dir": "Drafts/Upcoming/serbia", "article": "belgrade.html",
                                                 "changes": changes}), encoding="utf-8")
    if decisions is not None:
        (d / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")
    if applied:
        (d / "applied.json").write_text("{}", encoding="utf-8")


def add_threads(threads):
    (dc.REDLINE / "_comments" / "serbia__belgrade.json").write_text(json.dumps({"threads": threads, "deleted": []}), encoding="utf-8")


# ---- filters ----------------------------------------------------------------------------------------------------------
reset_store()
names, why = found('<ul><li>Burek is a flaky pastry.</li></ul>')
check("own-section: Burek pointing at this page's #food is out", "Burek" not in names and why.get("Burek") == "own-section", (names, why))

names, why = found('<p>The Albanian food here is great, and the Greek border is close.</p>')
check("demonym: Albanian", "Albanian" not in names and why.get("Albanian") == "demonym", (names, why))
check("demonym: Greek border", "Greek border" not in names and why.get("Greek border") == "demonym", (names, why))
names, _ = found('<p>The Albanian Riviera is lovely.</p>')
check("demonym: a real place starting with one stays", names == ["Albanian Riviera"], names)
check("demonym: countries that look like plurals stay", not dc._is_demonym("Philippines") and not dc._is_demonym("Balkans") and dc._is_demonym("Greeks"))

names, why = found('<p>It sits far south, along the border with Kosovo.</p>')
check("border: 'border with Kosovo' is out", not names and why.get("Kosovo") == "border-only", (names, why))
names, why = found('<p>It stops short of the Kosovo border.</p>')
check("border: 'the Kosovo border' is out", not names and why.get("Kosovo") == "border-only", (names, why))
names, why = found('<p>The waterfalls are on the border of Greece and Kosovo.</p>')
check("border: list after 'border of' is out", not names and why.get("Kosovo") == "border-only" and why.get("Greece") == "border-only", (names, why))
names, _ = found('<p>Near the border with Kosovo the road bends.</p><p>I took a day trip to Kosovo after that.</p>')
check("border: the next real mention is still reported", names == ["Kosovo"], names)
names, _ = found('<p>The village is just across the border in Greece.</p>')
check("border: 'across the border in X' is a place he went, it stays", names == ["Greece"], names)

names, why = found("<p>In Cappadocia don't count on Uber or BiTaksi.</p>")
check("not-use: don't count on Uber or BiTaksi", not names and why.get("Uber") == "told-not-to-use" and why.get("BiTaksi") == "told-not-to-use", (names, why))
names, why = found("<p>Uber doesn't operate in Serbia, so plan ahead.</p>")
check("not-use: Uber doesn't operate", not names and why.get("Uber") == "told-not-to-use", (names, why))
names, why = found("<p>There's no Uber or Bolt in the country.</p>")
check("not-use: there's no Uber or Bolt", not names and why.get("Bolt") == "told-not-to-use", (names, why))
names, why = found("<p>Take the bus back instead of an Uber.</p>")
check("not-use: instead of an Uber", not names and why.get("Uber") == "told-not-to-use", (names, why))
names, _ = found("<p>Bolt is cheap here, so use it.</p>")
check("not-use: an app he recommends stays", names == ["Bolt"], names)
names, why = found("<p>The name was chosen to avoid upsetting Greece.</p>")
check("not-use: 'avoid upsetting Greece' is not an instruction about Greece", why.get("Greece") != "told-not-to-use", (names, why))
names, _ = found("<p>Don't stop there. In Sarajevo, order the burek.</p>")
check("not-use: negation stops at the sentence end", names == ["Sarajevo"], names)

for sent, n in (("Occupied by Nazi Germany a year later.", "Germany"), ("A bridge across the Reuss River is nearby.", "Reuss"),
                ("It was part of Austria-Hungary then.", "Hungary"), ("The capital of North Macedonia is Skopje.", "Macedonia"),
                ("She studied at the University of Sarajevo for years.", "Sarajevo"), ("We reached Bosnia and Herzegovina by bus.", "Bosnia")):
    names, why = found("<p>%s</p>" % sent)
    check("part-of-name: " + n, n not in names and why.get(n) == "part-of-name", (sent, names, why))
for sent, n in (("From Germany, take the train.", "Germany"), ("Visit Sarajevo soon.", "Sarajevo"),
                ("On my last night in Germany I ate well.", "Germany"), ("Bosnia's hills are green.", "Bosnia")):
    names, _ = found("<p>%s</p>" % sent)
    check("part-of-name keeps: " + n, n in names, (sent, names))
names, _ = found("<p>The tour left at noon.</p>")
check("'The tour' is not a name", "The tour" not in names, names)

# ---- raw positions ----------------------------------------------------------------------------------------------------
for t, s in (("  a\n  b ", None), ("\n x  y\t", None)):
    txt, m = dc._squeeze(t, [(i, i + 1) for i in range(len(t))])
    check("squeeze equals the regex", txt == re.sub(r"\s+", " ", t).strip() and len(m) == len(txt), repr(txt))
body = '<p>Tom &amp; Jerry&#39;s caf&eacute; beat anything in Sarajevo, truly.</p>'
p = draft(body)
_, raw, res = dc._analyze(str(p))
x = [i for i in res["issues"] if i["kind"] == "bare-name"][0]
check("position survives entities", raw[x["start"]:x["end"]] == "Sarajevo", raw[x["start"]:x["end"]])

# ---- --apply ----------------------------------------------------------------------------------------------------------
reset_store()
body = ('<p>Tom &amp; Jerry\r\nin Sarajevo, then Skadarlija at night.</p>\r\n'
        '<p>[ ] placeholders stay, and Hidden Cafe has only a map pin.</p>\r\n'
        '<ul><li>Burek is out.</li></ul>')
raw_in = b"\xef\xbb\xbf" + page(body).replace("\n", "\r\n").replace("\r\r\n", "\r\n").encode("utf-8")
p = draft(None, raw=raw_in)
dc.map_pins = lambda rel: {"Hidden Cafe": None}
code, lines = dc.apply_links(str(p))
dc.map_pins = lambda rel: {}
out = p.read_bytes()
check("apply: exit 0", code == 0, lines)
rel_link = b'<a href="../bosnia/field-notes.html#sarajevo">Sarajevo</a>'
ext_link = ('<a href="%s" target="_blank" rel="noopener noreferrer">Skadarlija</a>' % MAPS.replace("&", "&amp;")).encode()
check("apply: relative link has no target", rel_link in out, out[:400])
check("apply: external link has target/rel and an escaped href", ext_link in out, out[:600])
check("apply: byte-identical outside the links", out.replace(rel_link, b"Sarajevo").replace(ext_link, b"Skadarlija") == raw_in)
check("apply: BOM and CRLF kept", out.startswith(b"\xef\xbb\xbf") and b"\r\n" in out)
check("apply: map pin skipped, not invented", b"Hidden Cafe has" in out and any("Hidden Cafe" in l and "no href" in l for l in lines), lines)
check("apply: placeholder untouched", b"[ ] placeholders" in out)
check("apply: filtered name untouched", b"Burek is out" in out)
code, lines = dc.apply_links(str(p))
check("apply: second run adds nothing", p.read_bytes() == out and "0 link(s)" in lines[0], lines)

p = draft('<p>Sarajevo &gt; Bosnia, <b>Skadarlija</b> too.</p>')
dc.apply_links(str(p))
out = p.read_text(encoding="utf-8")
check("apply: link goes inside the bold", "<b><a href=" in out and "</a></b>" in out, out)

# ---- refusal ----------------------------------------------------------------------------------------------------------
BODY = "<p>I flew to Sarajevo from here.</p>"


def refused(setup):
    reset_store()
    setup()
    p = draft(BODY)
    before = p.read_bytes()
    code, lines = dc.apply_links(str(p))
    return code, lines, p.read_bytes() == before


code, lines, same = refused(lambda: add_threads([{"author": "Kevin", "text": "link", "replies": []}]))
check("refuse: a Kevin thread", code == 2 and same and "--round-changes" in lines[1], lines)
code, lines, same = refused(lambda: add_threads([{"author": "Shadow reviewer", "text": "x", "replies": [{"author": "Kevin", "text": "yes"}]}]))
check("refuse: a Kevin reply under a Shadow thread", code == 2 and same, lines)
code, lines, same = refused(lambda: add_round("belgrade-r1", [], decisions={"g01": True}))
check("refuse: a decided round", code == 2 and same and "belgrade-r1 is decided" in lines[0], lines)
code, lines, same = refused(lambda: add_round("belgrade-r1", [], applied=True))
check("refuse: an applied round", code == 2 and same, lines)
code, lines, same = refused(lambda: add_round("belgrade-r1", []))
check("refuse: an open round", code == 2 and same and "belgrade-r1 is open" in lines[0], lines)
code, lines, same = refused(lambda: add_threads([{"author": "Shadow reviewer", "text": "x", "replies": [{"author": "Claude", "text": "ok"}]}]))
check("no refusal on Shadow-only threads", code == 0 and not same, lines)
code, lines, same = refused(lambda: None)
check("no refusal on an untouched draft", code == 0 and not same, lines)

reset_store()
add_threads([{"author": "Kevin", "text": "link", "replies": []}])
p = draft(BODY)
real_stdout, sys.stdout = sys.stdout, type("S", (), {"buffer": io.BytesIO(), "write": lambda s, x: None, "flush": lambda s: None})()
try:
    rc = dc.main([str(p), "--apply"])
    said = sys.stdout.buffer.getvalue().decode("utf-8")
finally:
    sys.stdout = real_stdout
check("refuse: the CLI exits 2 and says what to use", rc == 2 and "--round-changes" in said, said)

# ---- --round-changes ------------------------------------------------------------------------------------------------
reset_store()
body = ('<h2 id="a">Getting There</h2>\n'
        '<p>I flew to Sarajevo that day. The same phrase comes later.</p>\n'
        '<p>[ ] blank and Skadarlija at night and Greece too.</p>\n'
        '<h2 id="b">Later</h2>\n'
        '<p>I flew to Sarajevo that day and on to Bosnia the next.</p>\n'
        '<p>Then it ended.</p>')
p = draft(body)
before = p.read_bytes()
rounds_before = sorted(x.name for x in dc.REDLINE.iterdir())
changes, lines = dc.round_changes(str(p))
raw = p.read_bytes().decode("utf-8")
check("round: nothing written to the draft", p.read_bytes() == before)
check("round: no round created", sorted(x.name for x in dc.REDLINE.iterdir()) == rounds_before)
check("round: every find matches once", changes and all(raw.count(c["find"]) == 1 for c in changes), changes)
check("round: finds are text only", all(not re.search(r"[<>\n]", c["find"]) for c in changes), changes)
check("round: finds start and end on a word", all(c["find"] == c["find"].strip() for c in changes), changes)
check("round: shape", all(set(c) == {"id", "kind", "section", "find", "replace", "count", "note", "origin"} and c["kind"] == "grammar"
                          and c["count"] == 1 and c["origin"] == "draft_check" for c in changes), changes)
check("round: ids are ones the margin can name", all(re.fullmatch(r"[a-z]\d{1,3}", c["id"]) for c in changes), [c["id"] for c in changes])
check("round: section is the h2", {c["section"] for c in changes} >= {"Getting There", "Later"}, [c["section"] for c in changes])
check("round: only the link is added", all(re.sub(r'<a href="[^"]*"(?: target="_blank" rel="noopener noreferrer")?>|</a>', "", c["replace"]) == c["find"]
                                           for c in changes), changes)
check("round: never a placeholder", not any("[ ]" in c["find"] for c in changes))
t = raw
okay = True
for c in changes:
    okay = okay and t.count(c["find"]) == 1
    t = t.replace(c["find"], c["replace"], 1)
check("round: each still matches once after the others go in", okay, changes)
ids = [(c["find"], c["replace"]) for c in changes]
check("round: the repeated phrase got enough neighbors to be unique",
      any("Sarajevo" in f and f.endswith("day.") for f, r in ids) and any("Sarajevo" in f and f.endswith("and") for f, r in ids), ids)

reset_store()
twin = '<p>See <a href="https://x.test/">one</a> or on to Kosovo <a href="https://y.test/">two</a> too.</p>'
p = draft('<h2 id="a">A</h2>%s<h2 id="b">B</h2>%s' % (twin, twin))
changes, lines = dc.round_changes(str(p))
check("round: a text run too short to be unique is skipped, never a tag-crossing find", not changes and sum("no text-only find" in l for l in lines) == 2, (changes, lines))

reset_store()
p = draft('<p>Sarajevo was fine.</p><p>The Skadarlija quarter was better, far better.</p>')
add_round("belgrade-r2", [{"id": "g01", "kind": "grammar", "find": "Sarajevo was fine.", "replace": "x"},
                          {"id": "g02", "kind": "grammar", "find": "far better", "replace": "y"}])
changes, lines = dc.round_changes(str(p))
check("round: a name inside an open round's find is left to that round", not any("Sarajevo" in c["find"] for c in changes)
      and any("Sarajevo" in l and "belgrade-r2 g01" in l for l in lines), lines)
check("round: a find stops short of an open round's find", changes and all("far" not in c["find"].split() or "better" not in c["find"] for c in changes), changes)

reset_store()
dc.map_pins = lambda rel: {"Hidden Cafe": None}
changes, lines = dc.round_changes(str(draft("<p>Hidden Cafe was great.</p>")))
dc.map_pins = lambda rel: {}
check("round: a name with no href is skipped", not changes and any("no href" in l for l in lines), lines)

# ---- link-target trust (2026-11-10: the 591 reverted links) -------------------------------------------------------------

def found_with(links, body, country="philippines", own=""):
    """a site whose sibling holds exactly `links` [(href, text)]; returns (reported names+hrefs, filtered {name: reason})"""
    _n[0] += 1
    d = TMP / str(_n[0]) / country
    d.mkdir(parents=True)
    (d / "sibling.html").write_text('<html><body><div class="article-body"><p>%s</p></div></body></html>' % " ".join(
        '<a href="%s">%s</a>' % (h.replace("&", "&amp;"), t) for h, t in links), encoding="utf-8")
    p = d / "article.html"
    p.write_text(page(body + own), encoding="utf-8")
    res = dc.check(str(p))
    return [(x["text"], x["href"]) for x in res["issues"] if x["kind"] == "bare-name"], {f["text"]: f["reason"] for f in res["filtered"]}


def reason(links, body, name):
    got, why = found_with(links, body)
    return why.get(name), got


# deep links: a listing or booking page, never carried on a brand name
HW = "https://www.hostelworld.com/hostels/p/313245/roamer-brisbane/"
for href, name, sent in ((HW, "Hostelworld", "<p>A dorm on Hostelworld starts around 23 euros.</p>"),
                         ("https://www.greyhound.com.au/buses/sunshine-coast-to-brisbane", "Greyhound", "<p>I booked my Greyhound buses for the coast.</p>"),
                         ("https://www.alltrails.com/trail/brazil/rio-de-janeiro--2/trilha-morro-dois-irmaos", "AllTrails", "<p>It is 6 km on AllTrails.</p>"),
                         ("https://www.uber.com/global/en/cities/cairo/", "Uber", "<p>I took an Uber to the airport.</p>"),
                         ("https://www.doc.govt.nz/parks-and-recreation/places-to-go/fiordland/places/fiordland-national-park/", "Department of Conservation",
                          "<p>The Department of Conservation lists the walk.</p>")):
    r, got = reason([(href, name)], sent, name)
    check("deep link stays out: " + name, r == "deep-link" and not got, (r, got))
got, _ = found_with([("https://www.hostelworld.com/hostels/p/280127/casa-samay-mountain-view/", "Casa Samay Mountain View")], "<p>I stayed at Casa Samay Mountain View for a night.</p>")
check("deep link keeps a name that IS the listing", got == [("Casa Samay Mountain View", "https://www.hostelworld.com/hostels/p/280127/casa-samay-mountain-view/")], got)
got, _ = found_with([("https://www.uber.com/ar/en/", "Uber")], "<p>I took an Uber to the airport.</p>")
check("a locale home page is fine for a brand", [x[0] for x in got] == ["Uber"], got)
r, _ = reason([("https://www.rainbowmountaintravels.com/", "Tours")], "<p>Tours like Rainbow Mountain Travels cost $49.</p>", "Tours")
check("a word with nothing distinctive is out", r == "generic-name", r)
r, _ = reason([("https://www.instagram.com/wildlifephotographer_tambopata/", "Bryan")], "<p>Our guide, Bryan, was great.</p>", "Bryan")
check("a first name to someone's profile is out", r == "deep-link", r)

# in-page sections
r, got = reason([("manila.html#where-to-eat", "Manila")], "<p>Manila didn't make the ten.</p>", "Manila")
check("section-mismatch: Manila -> #where-to-eat", r == "section-mismatch" and not got, (r, got))
r, got = reason([("puerto-maldonado-amazon.html#night-bus", "Amazon")], "<p>After the Amazon I needed laundry.</p>", "Amazon")
check("section-mismatch: Amazon -> #night-bus", r == "section-mismatch" and not got, (r, got))
got, _ = found_with([("svaneti.html#kutaisi", "Kutaisi"), ("../brazil/field-notes.html#rio-de-janeiro", "Rio")], "<p>A bus to Kutaisi, then a flight to Rio.</p>")
check("a section that names the place stays", sorted(x[0] for x in got) == ["Kutaisi", "Rio"], got)
got, _ = found_with([("medellin.html#comuna-13", "Medellín's Comuna 13")], "<p>A lot of street art, as in Medellín's Comuna 13, covers the walls.</p>")
check("a section that is the name's own subject stays", [x[0] for x in got] == ["Medellín's Comuna 13"], got)

# a name larger or different from the target
r, got = reason([("coron.html", "Palawan")], "<p>Palawan was a lot greener than I expected.</p>", "Palawan")
check("region to one town's guide: Palawan -> Coron", r == "name-not-target" and not got, (r, got))
r, got = reason([("torres-del-paine-w-trek.html", "Patagonia")], "<p>Days in Patagonia pass fast.</p>", "Patagonia")
check("region to one trek's guide: Patagonia", r == "name-not-target", (r, got))
r, got = reason([("../../../croatia/field-notes.html", "Zagreb")], "<p>I flew to Zagreb.</p>", "Zagreb")
check("a city to its country's page, no section: out", r == "name-not-target", (r, got))
r, got = reason([("https://www.google.com/maps/place/Munich+International+Airport/@48.35,11.79,15z", "Munich")], "<p>My flight from Munich landed.</p>", "Munich")
check("different-place: city name on the airport pin", r == "different-place", (r, got))
r, got = reason([("https://www.google.com/maps/place/Woolworths+Byron+Bay/@-28.6,153.6,17z", "Woolworths")], "<p>Live off Woolworths and Coles.</p>", "Woolworths")
check("different-place: chain name on one store", r == "different-place", (r, got))
r, got = reason([("https://www.google.com/maps/place/Twelve+Apostles/@-38.2,143.8,9z", "Great Ocean Road")], "<p>A koala on the Great Ocean Road.</p>", "Great Ocean Road")
check("name-not-target: pin that names something else", r == "name-not-target", (r, got))
got, _ = found_with([("https://www.google.com/maps/place/Daintree+Rainforest/@-16.1,145.4,17z", "Daintree")], "<p>A day tour into the Daintree was great.</p>")
check("a pin with only a feature word extra stays", [x[0] for x in got] == ["Daintree"], got)
got, _ = found_with([("https://www.google.com/maps/place/Puerto+Princesa+International+Airport/@9.7,118.7,17z", "Puerto Princesa airport")],
                    "<p>The best plate was at the Puerto Princesa airport, oddly.</p>")
check("the same kind of thing stays", [x[0] for x in got] == ["Puerto Princesa airport"], got)
got, _ = found_with([("../armenia/field-notes.html", "Armenia field notes")], "<p>It is in my Armenia field notes for the bus.</p>")
check("a guide/field-notes phrase matches its page", [x[0] for x in got] == ["Armenia field notes"], got)

# which sibling link wins
got, _ = found_with([("manila.html#where-to-eat", "Manila"), ("manila.html", "Manila")], "<p>Manila is huge.</p>")
check("a bad sibling link loses to a good one", got == [("Manila", "manila.html")], got)
got, why = found_with([("coron.html", "El Nido"), ("el-nido.html", "El Nido")], "<p>On to El Nido for 3 days.</p>")
check("the good one is used, even if listed second", got == [("El Nido", "el-nido.html")], (got, why))
got, why = found_with([("el-nido.html", "El Nido"), ("el-nido-guide.html", "El Nido")], "<p>On to El Nido for 3 days.</p>")
check("two different pages, both plausible: conflicting, report only", not got and why.get("El Nido") == "conflicting-targets", (got, why))
got, _ = found_with([("https://www.google.com/maps/place/El+Nido,+Palawan/@11.1,119.3,13z", "El Nido"), ("el-nido.html", "El Nido")], "<p>On to El Nido for 3 days.</p>")
check("the site's own article beats a Maps pin", got == [("El Nido", "el-nido.html")], got)

# passing mentions of a country
for sent, n in (("<p>Allied with Germany at the time, the empire fell.</p>", "Germany"),
                ("<p>Stones came from around the world: black from Belgium, green from China.</p>", "Belgium"),
                ("<p>People were surprised, and one man guessed Indonesia.</p>", "Indonesia"),
                ("<p>The rolls were stuffed with cream cheese, like in Brazil.</p>", "Brazil"),
                ("<p>The town was a lot like Greece or Croatia.</p>", "Croatia"),
                ("<p>A Bulgarian living in Belgium joined us.</p>", "Belgium")):
    r, got = reason([("../%s/field-notes.html" % n.lower(), n)], sent, n)
    check("passing country stays out: " + sent, r in ("passing-country", "aside") and not got, (r, got))
for sent, n in (("<p>From Puerto Natales I crossed into Argentina by bus.</p>", "Argentina"),
                ("<p>On the second day I flew to Brazil.</p>", "Brazil"),
                ("<p>I took the night bus to Montenegro.</p>", "Montenegro"),
                ("<p>From Uruguay — the ferries are quick.</p>", "Uruguay"),
                ("<p>After a week in Argentina I went north.</p>", "Argentina"),
                ("<p>The village is just across the border in Greece.</p>", "Greece"),
                ("<p>A day trip to Kosovo is easy from here.</p>", "Kosovo")):
    got, why = found_with([("../%s/field-notes.html" % n.lower(), n)], sent)
    check("a country he went to keeps its link: " + sent, [x[0] for x in got] == [n], (got, why))
got, why = found_with([("../greece/field-notes.html", "Greece")], "<p>A pass of mine: near the Greece border the road bends.</p>")
check("the writer's border rule still holds", not got and why.get("Greece") == "border-only", (got, why))
got, why = found_with([("../serbia/field-notes.html", "Serbia")], "<p>It is close to Serbia, which I never reached.</p>")
check("a direction is no reason for a link", not got and why.get("Serbia") == "passing-country", (got, why))

# a short form next to the full one
got, why = found_with([("https://x.test/gwk", "GWK")], '<p>The statue at <a href="https://www.gwkbali.com/">Garuda Wisnu Kencana Cultural Park</a> (GWK) is huge.</p>')
check("abbreviation: (GWK) beside the full name", not got and why.get("GWK") in ("abbreviation", "deep-link"), (got, why))
got, why = found_with([("https://www.google.com/maps/place/GWK/@1,1,15z", "GWK")], '<p>The statue at Garuda Wisnu Kencana Cultural Park (GWK) is huge.</p>')
check("abbreviation: a code in brackets, even with its own pin", not got and why.get("GWK") == "abbreviation", (got, why))
got, why = found_with([("malba.html", "MALBA")], '<p><a href="https://x.test/">Museo de Arte Latinoamericano de Buenos Aires</a>, MALBA for short, is great.</p>')
check("short-form: acronym right after its linked full name", not got and why.get("MALBA") == "short-form", (got, why))
got, why = found_with([("../brazil/rio-de-janeiro.html", "Rio de Janeiro"), ("rio.html", "Rio")], '<p>From Rio de Janeiro the buses to Rio leave every hour.</p>', country="brazil")
check("short-form: never both forms in one run", got == [("Rio de Janeiro", "../brazil/rio-de-janeiro.html")] and why.get("Rio") == "short-form", (got, why))

# a name that only modifies the next word
for sent, n, href in (("<p>A night in the Manila airport hurt.</p>", "Manila", "manila.html"),
                      ("<p>From Technical University station the metro is easy.</p>", "Technical University", "https://www.google.com/maps/place/Technical+University/@41.7,44.7,14z"),
                      ("<p>A friend from my Oaxaca hostel told me.</p>", "Oaxaca", "oaxaca.html"),
                      ("<p>The crisp Brussels kind is better.</p>", "Brussels", "brussels.html"),
                      ("<p>The mountain on the Patagonia clothing logo.</p>", "Patagonia", "patagonia.html"),
                      ("<p>A town on the Rio de Janeiro state coast.</p>", "Rio de Janeiro", "rio-de-janeiro.html")):
    r, got = reason([(href, n)], sent, n)
    check("attributive: " + sent, r == "attributive" and not got, (r, got))

# comparisons and where someone is from
for sent, n, href in (("<p>The Gold Coast is Australia's answer to Miami, really.</p>", "Miami", "https://www.google.com/maps/place/Miami/@25.7,-80.2,12z"),
                      ("<p>A friendly guide who used to live in Miami drove us.</p>", "Miami", "https://www.google.com/maps/place/Miami/@25.7,-80.2,12z"),
                      ("<p>My roommate, a woman from São Paulo, told me about it.</p>", "São Paulo", "sao-paulo.html")):
    r, got = reason([(href, n)], sent, n)
    check("aside: " + sent, r == "aside" and not got, (r, got))

# and nothing above stops the ordinary good case, or writes outside a scratch copy
reset_store()
_n[0] += 1
d = TMP / str(_n[0]) / "philippines"
d.mkdir(parents=True)
(d / "sibling.html").write_text('<html><body><div class="article-body"><p><a href="coron.html">Coron</a> <a href="%s">Hostelworld</a></p></div></body></html>' % HW, encoding="utf-8")
p = d / "article.html"
p.write_bytes(page("<p>The bus to Coron took 3 hours, and Hostelworld had dorms.</p>").encode("utf-8"))
before = p.read_bytes()
code, lines = dc.apply_links(str(p))
out = p.read_bytes()
check("apply: the good link written, the deep brand link not", b'<a href="coron.html">Coron</a>' in out and b"hostelworld.com" not in out, out[:300])
check("apply: nothing else changes", out.replace(b'<a href="coron.html">Coron</a>', b"Coron") == before)

real_stderr, sys.stderr = sys.stderr, io.StringIO()         # argparse prints its usage line
try:
    dc.main(["a.html", "--apply", "--round-changes", "o.json"])
    check("the two edit modes are exclusive", False)
except SystemExit as e:
    check("the two edit modes are exclusive", e.code == 2)
finally:
    sys.stderr = real_stderr

# ---- --since-round: the sweep of his own new text ----------------------------------------------------------------------

def sweep_setup(old_body, new_body, slug="belgrade-r1", changes=(), decisions=None):
    """a scratch draft whose round baseline is old_body and whose file is new_body"""
    reset_store()
    add_round(slug, list(changes), decisions=decisions)
    (dc.REDLINE / slug / "baseline").mkdir()
    (dc.REDLINE / slug / "baseline" / "belgrade.html").write_text(page(old_body), encoding="utf-8")
    return draft(new_body)


OLD = '<h2 id="a">Getting There</h2>\n<p>I flew to Sarajevo that day and it was fine.</p>\n<p>[ ] an old blank here.</p>'
p = sweep_setup(OLD, OLD + '\n<p>Then I took a bus to Sarajevo from the border. Bosnia was lovely after that.</p>\n<p>[ ] new blank and [PHOTO] slot.</p>')
before = p.read_bytes()
changes, lines, blanks = dc.sweep_new_text(str(p), "last")
names = [c["note"].split(" to ")[0].replace("Link: ", "") for c in changes]
check("sweep: the name in the new paragraph is linked", "Sarajevo" in names and "Bosnia" in names, lines)
check("sweep: the old paragraph's bare Sarajevo is left alone", all("flew" not in c["find"] for c in changes), changes)
check("sweep: a second mention in new text is linked even though it's in the section already", any("Sarajevo" in c["find"] and c["section"] == "Getting There" and "flew" not in c["find"] for c in changes), changes)
check("sweep: nothing written to the draft", p.read_bytes() == before)
check("sweep: only the new blank is listed", [b["text"] for b in blanks] == ["[ ]"] and "new blank" in blanks[0]["context"], blanks)
check("sweep: [PHOTO] is a photo slot, not a blank", all(b["text"] != "[PHOTO]" for b in blanks), blanks)
check("sweep: a full run still reports the old text", dc.check(str(p))["issues"], lines)

p = sweep_setup('<p>The town was quiet and lovely.</p>', '<p>The town was quiet and lovely, and Sarajevo was close.</p>')
changes, lines, _ = dc.sweep_new_text(str(p), "last")
check("sweep: a new clause inside an old paragraph counts", len(changes) == 1 and "Sarajevo" in changes[0]["find"], lines)

p = sweep_setup('<p>The town was quiet and lovely today, truly.</p>', '<p>The town was quiet and lovely today, truly, near <a href="../bosnia/field-notes.html#sarajevo">Sarajevo</a> and Sarajevo.</p>')
changes, lines, _ = dc.sweep_new_text(str(p), "last")
check("sweep: a name he linked in the same paragraph isn't linked again", not changes, lines)

OLD2 = '<p>The town was quiet.</p>\n<p>I walked to the fortress and back.</p>'
p = sweep_setup(OLD2, '<p>The town was quiet.</p>\n<p>I walked to the fortress and back, and <a href="x.html">the park</a> too.</p>\n<p>On to Sarajevo next.</p>')
changes, lines, _ = dc.sweep_new_text(str(p), "last")
check("sweep: a link he added doesn't make the rest of the paragraph new", all("walked" not in c["find"] for c in changes), changes)

# the round's own accepted change counts as old text, a rejected one doesn't
BASE = '<p>The town was quiet in the morning.</p>'
ACC = [{"id": "g01", "kind": "grammar", "section": "", "find": "quiet in the morning", "replace": "quiet in the morning, and Sarajevo was close", "count": 1, "note": "x"}]
TYPED = '<p>The town was quiet in the morning, and Sarajevo was close.</p>\n<p>Bosnia was a treat.</p>'
p = sweep_setup(BASE, TYPED, changes=ACC, decisions={"g01": True})
changes, lines, _ = dc.sweep_new_text(str(p), "last")
check("sweep: text the round added and he accepted isn't his new text", len(changes) == 1 and "Bosnia" in changes[0]["find"], lines)
p = sweep_setup(BASE, TYPED, changes=ACC, decisions={"g01": False})
changes, lines, _ = dc.sweep_new_text(str(p), "last")
check("sweep: a rejected change doesn't hide what he typed", len(changes) == 2 and any("Sarajevo" in c["find"] for c in changes), lines)

# picking the round, and refusing a wrong one
p = sweep_setup(BASE, TYPED, slug="belgrade-r2")
add_round("belgrade-r10", [])
(dc.REDLINE / "belgrade-r10" / "baseline").mkdir()
(dc.REDLINE / "belgrade-r10" / "baseline" / "belgrade.html").write_text(page(TYPED), encoding="utf-8")
changes, _, _ = dc.sweep_new_text(str(p), "last")
check("sweep: 'last' is the highest round number (r10 over r2)", not changes, changes)
changes, _, _ = dc.sweep_new_text(str(p), "belgrade-r2")
check("sweep: a named round is the one used", changes, changes)
for slug, words in (("nope-r1", "not a round of this article"),):
    try:
        dc.sweep_new_text(str(p), slug)
        check("sweep: an unknown round is refused", False)
    except ValueError as e:
        check("sweep: an unknown round is refused", words in str(e), e)
reset_store()
p = draft('<p>Sarajevo.</p>')
try:
    dc.sweep_new_text(str(p), "last")
    check("sweep: no round at all is refused", False)
except ValueError as e:
    check("sweep: no round at all is refused", "no round" in str(e), e)

# an open round's finds stay protected, the file on disk is the point of comparison, and no real store was touched
p = sweep_setup(OLD, OLD + '\n<p>Then I took a bus to Sarajevo from the border.</p>',
                changes=[{"id": "g01", "kind": "grammar", "section": "", "find": "bus to Sarajevo", "replace": "x", "count": 1, "note": "n"}])
changes, lines, _ = dc.sweep_new_text(str(p), "last")
check("sweep: a name inside an open round's find is left to that round", not changes and any("already inside open round" in l for l in lines), lines)

p = sweep_setup(OLD, OLD + '\n<p>Then I took a bus to Sarajevo from the border.</p>\n<p>[ ] blank.</p>')
outfile = p.parent / "out.json"
real_stdout, sys.stdout = sys.stdout, type("S", (), {"buffer": io.BytesIO(), "write": lambda s, x: None, "flush": lambda s: None})()
try:
    rc = dc.main([str(p), "--round-changes", str(outfile), "--since-round", "last"])
    said = sys.stdout.buffer.getvalue().decode("utf-8")
    bad = dc.main([str(p), "--round-changes", str(outfile), "--since-round", "nope-r1"])
finally:
    sys.stdout = real_stdout
blanks_file = outfile.with_suffix(".blanks.json")
got = json.loads(outfile.read_text(encoding="utf-8"))
check("sweep CLI: writes the changes in the proposal shape", rc == 0 and len(got) == 1 and got[0]["origin"] == "draft_check", (rc, said))
check("sweep CLI: writes the blanks file", json.loads(blanks_file.read_text(encoding="utf-8"))[0]["text"] == "[ ]", said)
check("sweep CLI: says which round it used", "new since belgrade-r1" in said, said)
check("sweep CLI: a bad round exits 2", bad == 2)
real_stderr, sys.stderr = sys.stderr, io.StringIO()
try:
    dc.main(["a.html", "--since-round", "last"])
    check("--since-round needs --round-changes", False)
except SystemExit as e:
    check("--since-round needs --round-changes", e.code == 2)
finally:
    sys.stderr = real_stderr

shutil.rmtree(TMP, ignore_errors=True)
sys.stdout.buffer.write(("%d passed, %d failed\n" % (passed, failed)).encode("utf-8"))
sys.exit(1 if failed else 0)
