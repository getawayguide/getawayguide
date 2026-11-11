#!/usr/bin/env python3
"""Pin down what tools/crosslink.py may link, on scratch copies only (memory feedback_test_cleanup_never_in_real_folders).

A throwaway site is built in a temp folder: a country with four guides, an itinerary and field notes, written the way the
North Macedonia pages are.
  - NO RELINKS (removed 2026-11-10, Kevin: "map links should be linked to map pins like google maps or booking links"):
    every Google Maps link and every booking link (Booking.com, Hostelworld, GetYourGuide) survives an --apply byte for
    byte, even where a guide covers the place ("Kale Fortress", "Krushevo", "Makedonium"); apply_items refuses any item
    that is not a bare-text mention, and any span inside an existing <a>
  - mentions: first "Skopje" per section, whole "Lake Ohrid", "Krushevo guide", none inside headings/captions/alt/links,
    none in "Skopje 2014" or "Skopje bus station", none on the page's own place
  - apply is byte-preserving apart from the new <a> tags, idempotent, and refuses an href that does not resolve

    python tools/test_crosslink.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import crosslink as cx

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = passed + bool(ok), failed + (not ok)
    if not ok:
        sys.stdout.buffer.write(("FAIL %s %s\n" % (name, detail)).encode("utf-8"))


def maps(name, pid, lat, lon):
    return ("https://www.google.com/maps/place/%s/@%s,%s,17z/data=!3m1!4b1!4m6!3m5!1s%s!8m2!3d%s!4d%s!16s%%2Fg%%2F1x"
            % (name.replace(" ", "+"), lat, lon, pid, lat, lon))


def a(url, text, cls=""):
    return '<a%s href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % ((' class="%s"' % cls) if cls else "", url.replace("&", "&amp;"), text)


KALE = maps("Kale Fortress", "0x1354:0xaaa1", "41.9983", "21.4298")
PROESKI = maps("Tose Proeski Memorial House", "0x1356:0xbbb1", "41.3745", "21.2462")
ODAJA = maps("Krushevska Odaja", "0x1356:0xccc1", "41.3745", "21.2462")        # same 100 m pin as the memorial
KRUSHEVO = maps("Krushevo, North Macedonia", "0x1356:0xddd1", "41.3708", "21.2490")
PRILEP = maps("Prilep, North Macedonia", "0x1356:0xeee1", "41.3461", "21.5535")
KULI = maps("Markovi Kuli", "0x1356:0xfff1", "41.3600", "21.5500")
HOSTEL = maps("Old Town Hostel Ohrid", "0x1350:0x1111", "41.1130", "20.8010")
PARK = maps("Bitola City Park", "0x1357:0x2222", "41.0250", "21.3300")
OHRID_TOWN = maps("Ohrid, North Macedonia", "0x1350:0x3333", "41.1231", "20.8016")
BOOKING = "https://www.booking.com/hotel/mk/villa-ohrid.html?aid=123&label=x"
HOSTELWORLD = "https://www.hostelworld.com/hostels/p/12345/skopje-hostel/"
GYG = "https://www.getyourguide.com/skopje-l2734/kale-fortress-tour-t1/"


def page(title, body, cls="article-body"):
    return ('<!doctype html>\n<html><head><title>%s</title></head><body class="x">\n<nav><a href="../index.html">Home</a></nav>\n'
            '<h1 class="serif">%s</h1>\n<div class="%s">\n%s\n</div>\n<footer><a href="../about.html">About</a></footer></body></html>\n'
            % (title, title, cls, body))


SKOPJE = page("The Ultimate Guide to Skopje, North Macedonia's Bizarre Capital", """
<h2 class="article-h2" id="getting-there">Getting to Skopje</h2>
<p>Buses run from Ohrid and Bitola every hour. The Skopje bus station is east of the center.</p>
<h2 class="article-h2" id="the-city">Skopje — The City Itself</h2>
<h3 class="article-h3" id="square">Skopje 2014 (The Funky Statues)</h3>
<p>The %s stands on Macedonia Square.</p>
<h3 class="article-h3" id="kale">Kale Fortress</h3>
<p>%s sits above the bazaar. Lake Ohrid is three hours away.</p>
<h2 class="article-h2" id="day-trips">Skopje as a Base: The Day Trips</h2>
<h3 class="article-h3" id="ohrid">Ohrid</h3>
<p>%s is the other place everyone goes. My <a href="ohrid.html">Lake Ohrid guide</a> has the details, and Ohrid is worth two nights.</p>
<div class="img-caption">Krushevo seen from the road</div>
""" % (a(maps("Warrior on a Horse", "0x1354:0x0001", "41.9972", "21.4330"), "Alexander the Great Statue"), a(KALE, "Kale Fortress"), a(OHRID_TOWN, "Ohrid")))

OHRID = page("Lake Ohrid — The Hidden Gem of the Balkans", """
<h2 class="article-h2" id="getting-there">Getting to Ohrid</h2>
<p><b>From Krushevo</b> — take the bus and change in %s. Krushevo to Prilep is 2 hours.</p>
<p>Stay at %s, the best hostel in the country.</p>
<h2 class="article-h2" id="the-city">Ohrid — The Town Itself</h2>
<h3 class="article-h3" id="kaneo">St. John Kaneo Church</h3>
<p>%s is on every postcard. Lake Ohrid is clear here, and Ohrid itself is small.</p>
<h2 class="article-h2" id="where-to-stay">Where to Stay</h2>
<p>Skopje is a long day trip, so sleep in Ohrid.</p>
""" % (a(PRILEP, "Prilep"), a(HOSTEL, "Old Town Hostel Ohrid"), a(maps("Church of St. John at Kaneo", "0x1350:0x4444", "41.1127", "20.7908"), "St. John Kaneo Church")))

BITOLA = page("Bitola — The City of Consuls, Roman Ruins, and the Gateway to Greece", """
<h2 class="article-h2" id="getting-there">Getting to Bitola</h2>
<p><b>From Krushevo</b> — a bus to %s with a connection there. Walk through %s to the ruins.</p>
<h2 class="article-h2" id="day-trips">Day Trips from Bitola</h2>
<h3 class="article-h3" id="krushevo">Krushevo</h3>
<p>%s is a small, picturesque town deep in the mountains. The %s sits above it.</p>
<h3 class="article-h3" id="prilep">Prilep</h3>
<p>%s is the fourth-largest city. The fortress of %s above town is worth an hour.</p>
<h2 class="article-h2" id="where-to-eat">Where to Eat</h2>
<p>Skopje has better kebap, but Bitola's old bazaar wins for coffee. Read my Skopje guide for the bazaar.</p>
""" % (a(PRILEP, "Prilep"), a(PARK, "Bitola City Park"), a(KRUSHEVO, "Krushevo"),
       a(maps("Ilinden Monument - Makedonium", "0x1356:0x5555", "41.3774", "21.2483"), "Makedonium"), a(PRILEP, "Prilep"), a(KULI, "Markovi Kuli")))

KRUSHEVO_PAGE = page("Krushevo — North Macedonia's Picturesque Alpine Village", """
<h2 class="article-h2" id="getting-there">Getting to Krushevo</h2>
<p>High in the mountains, %s is a quiet town. A taxi from %s to Krushevo is 20€. Skopje has three minibuses a day.</p>
<h2 class="article-h2" id="the-city">Krushevo — The Town Itself</h2>
<h3 class="article-h3" id="makedonium">The Makedonium</h3>
<p>%s looks like a spaceship.</p>
<h3 class="article-h3" id="proeski">Nikola Karev Monument and the Toše Proeski Memorial House</h3>
<p>The %s is a museum to the singer. Eat at %s afterwards.</p>
<h2 class="article-h2" id="prilep">A Day Trip to Prilep</h2>
<p>%s is a working city. <img src="x.jpg" alt="Skopje street"> The fortress %s has the views.</p>
<h3 class="article-h3" id="markovi-kuli">Markovi Kuli (Marko's Towers)</h3>
<p>%s is medieval.</p>
""" % (a(KRUSHEVO, "Krushevo"), a(PRILEP, "Prilep"), a(maps("Ilinden Monument - Makedonium", "0x1356:0x5555", "41.3774", "21.2483"), "Makedonium"),
       a(PROESKI, "Toše Proeski Memorial House"), a(ODAJA, "Krushevska Odaja"), a(PRILEP, "Prilep"), a(KULI, "Markovi Kuli"), a(KULI, "Markovi Kuli")))

ITINERARY = page("North Macedonia Travel Guide with Perfect 8 Day Itinerary", """
<div id="overview" class="measure"><h2 class="serif">Overview</h2>
<p>Start in Skopje, then Ohrid, Bitola and Krushevo. Skopje again at the end.</p></div>
<div id="day-1" class="measure"><h2 class="serif">Day 1</h2>
<p>Catch the sunset from %s. Eat sarma at %s. Then visit %s and %s.</p>
<p>Spend the night in Skopje. The <a href="skopje.html">Skopje</a> guide lists hostels, and Skopje's bazaar is open late.</p></div>
<div id="day-2" class="measure"><h2 class="serif">Day 2</h2>
<p>Book a room in %s, a bed in %s, and the %s tour. Then see %s.</p></div>
<div id="day-5" class="measure"><h2 class="serif">Day 5</h2>
<p>Ride to %s and change for Krushevo. Read my Krushevo guide and my field notes. See the top 10 too.</p></div>
""" % (a(KALE, "Kale Fortress"), a(ODAJA, "Krushevska Odaja"), a(KRUSHEVO, "Krushevo"), a(PRILEP, "Prilep"),
       a(BOOKING, "Ohrid"), a(HOSTELWORLD, "Skopje"), a(GYG, "Kale Fortress", cls="bk"), a(OHRID_TOWN, "<b>Lake Ohrid</b>"),
       a(PRILEP, "Prilep")), cls="artbody")

FIELD = page("North Macedonia Field Notes", """
<h2 class="article-h2" id="krushevo-2">Krushevo</h2>
<div class="fn-sub-hd">What to Do</div>
<ul>
<li><b>%s</b><a class="gl" href="krushevo.html#makedonium" title="In the Krushevo guide">↗</a> — the highlight above Krushevo</li>
<li><b>Wander</b> — Skopje feels far away here; Bitola is two hours</li>
</ul>
""" % a(maps("Ilinden Monument - Makedonium", "0x1356:0x5555", "41.3774", "21.2483"), "Makedonium"), cls="fn-body")

TOP10 = page("Top 10 Things to Do in North Macedonia", """
<h2 class="article-h2" id="one">1. Krushevo</h2>
<p>Krushevo is the place. Lake Ohrid comes second, and the Lake Ohrid guide covers the boat tour.</p>
""")

INDEX = '<html><body><div class="country-article-card" onclick="location.href=\'skopje.html\'" style="background:url(\'x.jpg\')"></div></body></html>'

TMP = Path(tempfile.mkdtemp(prefix="crosslink_test_"))
C = TMP / "north-macedonia"
C.mkdir(parents=True)
PAGES = {"skopje.html": SKOPJE, "ohrid.html": OHRID, "bitola.html": BITOLA, "krushevo.html": KRUSHEVO_PAGE,
         "north-macedonia-itinerary.html": ITINERARY, "field-notes.html": FIELD, "top-10-north-macedonia.html": TOP10, "index.html": INDEX}
for n, s in PAGES.items():
    (C / n).write_bytes(s.replace("\n", "\r\n").encode("utf-8") if n == "bitola.html" else s.encode("utf-8"))   # one CRLF page
(TMP / "index.html").write_text("<html><body>home</body></html>", encoding="utf-8")
(TMP / "about.html").write_text("<html><body>about</body></html>", encoding="utf-8")
cx.ROOT = TMP
cx.REVIEW_DIR = TMP / ".tmp" / "crosslink"
cx.ALIASES = TMP / "none.json"

res = cx.run("north-macedonia", apply=False)
by = {}
for key in ("applied", "review", "rejected"):
    for it in res[key]:
        by.setdefault((it["page"].split("/")[-1], it["text"]), []).append((key, it))


def verdicts(page, text):
    return [(k, it["reason"], it["href"]) for k, it in by.get((page, text), [])]


def has(page, text, key, reason=None, href=None):
    return any(k == key and (reason is None or r == reason) and (href is None or h == href) for k, r, h in verdicts(page, text))


# ---- no relinks (removed 2026-11-10) -------------------------------------------------------------------------------------
check("no relink item at all: every item is a mention", all(it["kind"] == "mention" for it in res["applied"] + res["rejected"]) and not any(it["kind"] == "relink" for it in res["review"]),
      [(it["kind"], it["text"]) for it in res["applied"] + res["rejected"] + res["review"] if it["kind"] != "mention"])
check("run() has no relinks switch", "relinks" not in cx.run.__code__.co_varnames[:cx.run.__code__.co_argcount])
check("crosslink has no relink pass to call", not hasattr(cx, "scan_relinks") and not hasattr(cx, "judge_relink"))
check("itinerary Day 2: nothing linked (every name there is inside a booking or Maps link)",
      not [it for it in res["applied"] if "Book a room" in it["context"]],
      [(it["text"], it["context"]) for it in res["applied"] if "Book a room" in it["context"]])
check("no item starts inside an existing <a>", not [it for it in res["applied"]
                                                    if cx.inside_anchor((C / it["page"].split("/")[-1]).read_bytes().decode("utf-8"), it["at"])])

# ---- mentions -----------------------------------------------------------------------------------------------------------
men = [(it["page"].split("/")[-1], it["text"], it["href"], it["context"]) for it in res["applied"] if it["kind"] == "mention"]


def mentions(page, text=None):
    return [m for m in men if m[0] == page and (text is None or m[1] == text)]


itin = mentions("north-macedonia-itinerary.html")
check("itinerary overview: Skopje, Ohrid, Bitola, Krushevo once each", sorted(m[1] for m in itin if "Start in" in m[3]) == ["Bitola", "Krushevo", "Ohrid", "Skopje"], itin)
check("itinerary overview: second Skopje in the same section not linked", sum(1 for m in itin if m[1] == "Skopje" and "Start in" in m[3]) == 1)
check("itinerary day 1: Skopje not linked (section already links skopje.html)", not [m for m in itin if m[1] == "Skopje" and "night" in m[3]], itin)
check("itinerary day 5: 'Krushevo guide' linked whole, the bare Krushevo before it left alone", any(m[1] == "Krushevo guide" for m in itin) and not [m for m in itin if m[1] == "Krushevo" and "Ride to" in m[3]], itin)
check("itinerary day 5: 'field notes' -> field-notes.html", any(m[1] == "field notes" and m[2] == "field-notes.html" for m in itin), itin)
check("itinerary day 5: 'top 10' -> top-10 page", any(m[1].lower() == "top 10" and m[2] == "top-10-north-macedonia.html" for m in itin), itin)
sk = mentions("skopje.html")
check("skopje: 'Skopje bus station' not linked, Ohrid/Bitola in Getting there linked", {m[1] for m in sk if "Buses run" in m[3]} == {"Ohrid", "Bitola"} and not any(m[1] == "Skopje" for m in sk), sk)
check("skopje: 'Lake Ohrid' linked whole in the Kale section", any(m[1] == "Lake Ohrid" for m in sk), sk)
check("skopje: caption 'Krushevo' not linked", not any(m[1] == "Krushevo" for m in sk), sk)
kr = mentions("krushevo.html")
check("krushevo: alt text 'Skopje street' not linked; prose Skopje linked once in Getting there", [m[1] for m in kr] == ["Skopje"], kr)
bt = mentions("bitola.html")
check("bitola: 'Skopje guide' linked whole, Skopje once per section", any(m[1] == "Skopje guide" for m in bt) and sum(1 for m in bt if m[1] == "Skopje") <= 1, bt)
check("bitola: 'Krushevo' in bold 'From Krushevo' linked", any(m[1] == "Krushevo" and "From" in m[3] for m in bt), bt)
fn = mentions("field-notes.html")
check("field notes: Skopje and Bitola linked in the What to Do bullet; Krushevo (section links krushevo.html) not", {m[1] for m in fn} == {"Skopje", "Bitola"}, fn)
t10 = mentions("top-10-north-macedonia.html")
check("top 10: Krushevo once (the heading above is not 'the word before'), 'Lake Ohrid guide' over the bare 'Lake Ohrid'", sorted(m[1] for m in t10) == ["Krushevo", "Lake Ohrid guide"], t10)
check("no mention item inside a heading", not any(it["kind"] == "mention" and "1. Krushevo" in it["context"][:12] for it in res["applied"]))

# ---- apply ---------------------------------------------------------------------------------------------------------------
before = {n: (C / n).read_bytes() for n in PAGES}
res2 = cx.run("north-macedonia", apply=True)
check("apply wrote something, missed nothing", res2["applied"] and not res2["missed"], res2["missed"])
after = {n: (C / n).read_bytes() for n in PAGES}
check("CRLF page keeps CRLF", b"\r\n" in after["bitola.html"] and b"\n" not in after["bitola.html"].replace(b"\r\n", b""))
import re
strip = lambda b: re.sub(rb"<a\b[^>]*>|</a>", b"", b)
check("only <a> tags changed", all(strip(before[n]) == strip(after[n]) for n in PAGES), [n for n in PAGES if strip(before[n]) != strip(after[n])])
anchors = lambda b: re.findall(rb"<a\b[^>]*>.*?</a>", b, re.S)
lost = {n: [x for x in set(anchors(before[n])) if anchors(after[n]).count(x) < anchors(before[n]).count(x)] for n in PAGES}
check("every <a> that was there before is still there, byte for byte", not any(lost.values()), {n: v[:2] for n, v in lost.items() if v})


def survives(name, url, text, cls=""):
    """the exact Maps/booking tag a() wrote is still in the page after --apply"""
    return a(url, text, cls).encode("utf-8") in after[name]


itin_ = "north-macedonia-itinerary.html"
check("Maps link 'Kale Fortress' (a section of skopje.html covers it) survives --apply untouched", survives(itin_, KALE, "Kale Fortress"))
check("Maps link 'Krushevo' (a guide of its own) survives --apply untouched", survives(itin_, KRUSHEVO, "Krushevo") and survives("bitola.html", KRUSHEVO, "Krushevo"))
check("Maps links 'Makedonium', 'Markovi Kuli', 'Krushevska Odaja' survive", survives("bitola.html", maps("Ilinden Monument - Makedonium", "0x1356:0x5555", "41.3774", "21.2483"), "Makedonium")
      and survives("bitola.html", KULI, "Markovi Kuli") and survives(itin_, ODAJA, "Krushevska Odaja"))
check("Booking.com link 'Ohrid' (the name of a guide) survives --apply untouched", survives(itin_, BOOKING, "Ohrid"))
check("Hostelworld 'Skopje' and GetYourGuide 'Kale Fortress' links survive", survives(itin_, HOSTELWORLD, "Skopje") and survives(itin_, GYG, "Kale Fortress", cls="bk"))
check("a Maps link wrapping <b>Lake Ohrid</b> is not linked inside", survives(itin_, OHRID_TOWN, "<b>Lake Ohrid</b>") and b'<b><a href="ohrid.html">' not in after[itin_])
check("no internal link stands where a Maps link was", b'<a href="skopje.html#kale">' not in after[itin_] and b'<a href="krushevo.html#makedonium">Makedonium' not in after["bitola.html"])
check("Lake Ohrid wrapped whole", b'<a href="ohrid.html">Lake Ohrid</a>' in after["skopje.html"])
check("no link resolves badly", not res2["problems"], res2["problems"])
res3 = cx.run("north-macedonia", apply=True)
check("idempotent: second apply changes nothing", not res3["applied"], [(i["text"], i["page"]) for i in res3["applied"]])

# hard guards in apply_items: an old-style relink item is refused, and so is a mention span inside an existing <a>
itxt = (C / itin_).read_text(encoding="utf-8")
k = itxt.index(">Ohrid</a>") + 1                                      # the text of the Booking.com link
relink = [{"kind": "relink", "page": "north-macedonia/" + itin_, "text": "Ohrid", "at": k, "end": k + 5, "href": "ohrid.html",
           "tag": "", "verdict": "apply"}]
done, missed = cx.apply_items(relink, root=TMP)
check("apply_items refuses a relink item", not done and missed and "never rewritten" in missed[0]["miss"], missed)
inside = [{"kind": "mention", "page": "north-macedonia/" + itin_, "text": "Ohrid", "at": k, "end": k + 5, "href": "ohrid.html", "verdict": "apply"}]
done, missed = cx.apply_items(inside, root=TMP)
check("apply_items refuses a mention inside an existing <a>", not done and missed and "inside an existing link" in missed[0]["miss"], missed)
check("the itinerary is unchanged by the refused items", (C / itin_).read_text(encoding="utf-8") == itxt)
check("inside_anchor", cx.inside_anchor('<p><a href="x">Ohrid</a> Ohrid</p>', 17) and not cx.inside_anchor('<p><a href="x">Ohrid</a> Ohrid</p>', 27)
      and cx.inside_anchor('<p><a href="x"><b>Ohrid</b></a></p>', 18) and cx.inside_anchor('<a href="Ohrid">', 10))

# an href that does not resolve is refused
bad = [{"kind": "mention", "page": "north-macedonia/skopje.html", "text": "Bitola", "at": 0, "end": 6, "href": "nowhere.html", "verdict": "apply"}]
done, missed = cx.apply_items(bad, root=TMP)
check("unresolvable href refused", not done and missed and missed[0]["miss"] == "href does not resolve")
badanchor = [{"kind": "mention", "page": "north-macedonia/skopje.html", "text": "Bitola", "at": 0, "end": 6, "href": "bitola.html#nope", "verdict": "apply"}]
done, missed = cx.apply_items(badanchor, root=TMP)
check("missing anchor refused", not done and missed)

# verify() catches a broken anchor and a draft link
(C / "broken.html").write_text(page("Broken", '<p><a href="skopje.html#nope">x</a> <a href="../Drafts/Upcoming/x.html">d</a> <a href="#nothere">y</a></p>'), encoding="utf-8")
probs = cx.verify(["north-macedonia/broken.html"], root=TMP)
check("verify: anchor + draft + in-page anchor flagged", sorted(p[2] for p in probs) == ["anchor missing in target", "anchor with no target", "points at a draft or a local server"], probs)

# name rules
check("name_matches: Lake Ohrid ~ Ohrid", cx.name_matches("Lake Ohrid", "Ohrid") and cx.name_matches("Ohrid Lake", "Ohrid") and not cx.name_matches("Ohrid Trout House", "Ohrid"))
check("name_matches: Pelister National Park ~ Pelister", cx.name_matches("Pelister National Park", "Pelister"))
check("name_matches: Bitola City Park !~ Bitola", not cx.name_matches("Bitola City Park", "Bitola"))
check("name_matches: Gyumri Train Station !~ Gyumri", not cx.name_matches("Gyumri Train Station", "Gyumri"))
check("name_matches: Lake Ohrid Boat Tour !~ Lake Ohrid", not cx.name_matches("Lake Ohrid Boat Tour", "Lake Ohrid"))

shutil.rmtree(TMP, ignore_errors=True)
sys.stdout.buffer.write(("%d passed, %d failed\n" % (passed, failed)).encode("utf-8"))
sys.exit(1 if failed else 0)
