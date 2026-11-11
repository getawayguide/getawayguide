#!/usr/bin/env python3
"""Pin down what tools/country_check.py reports and the one thing --fix may rewrite, on scratch copies only.

Every page below is built in a temp tree that stands in for the site root (memory feedback_test_cleanup_never_in_real_folders),
and the review guard is replaced where a test needs it, so no real article, comment or round is read or written.

    python tools/test_country_check.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import country_check as cc

passed = failed = 0
TMP = Path(tempfile.mkdtemp(prefix="country_check_test_"))
_n = [0]


def ok(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        sys.stdout.buffer.write(("FAIL %s  %s\n" % (name, detail)).encode("utf-8"))


def site():
    _n[0] += 1
    r = TMP / ("s%d" % _n[0])
    r.mkdir()
    return r


def article(path, h1, title=None, og=None, desc=None, body=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = title if title is not None else h1
    meta = ('<meta property="og:title" content="%s">' % og if og else "") + \
           ('<meta property="og:description" content="%s">' % desc if desc else "")
    path.write_bytes(("<!DOCTYPE html>\n<html><head><title>%s</title>%s</head>\n<body><h1>%s</h1>\n%s\n</body></html>\n"
                      % (t, meta, h1, body)).encode("utf-8"))


def card(href, title, photo="a.jpg"):
    return ("""<div class="country-article-card" onclick="location.href='%s'" style="background:url('%s') center/cover" data-bgfit="1">\n"""
            """  <div class="country-article-card-body"><h3 class="country-article-card-title">%s</h3></div>\n</div>\n""" % (href, photo, title))


def xm(href, title, desc="", img="x.jpg"):
    return ('<a class="xm-c" href="%s" data-xm="auto"><picture><img src="%s"></picture><span class="xm-t">%s</span>'
            '<span class="xm-d">%s</span></a>\n' % (href, img, title, desc))


def gb(href, title, img="g.jpg"):
    return ('<a class="gb-card" href="%s"><picture><img src="%s"></picture><span class="t"><span class="h">%s</span>'
            '<span class="d">Want the detailed version?</span></span></a>\n' % (href, img, title))


def btn(href, label):
    return """<button class="country-place-btn" onclick="location.href='%s'">%s</button>\n""" % (href, label)


def run(r, folder):
    return cc.check([r / folder], r)


def kinds(fs):
    return sorted(f["kind"] for f in fs)


# ---- numbers -----------------------------------------------------------------------------------------------------------
r = site()
article(r / "mk/itin.html", "The Perfect 8 Day Itinerary")
article(r / "mk/index.html", "North Macedonia", body=card("itin.html", "The Perfect 7 Day Itinerary"))
article(r / "mk/skopje.html", "Skopje", body=xm("itin.html", "The Perfect 7 Day Itinerary"))
fs = run(r, "mk")
ok("day count on a card is caught", any(f["kind"] == "number" and f["element"] == "card" for f in fs), fs)
ok("day count on an Explore More block is caught", any(f["kind"] == "number" and f["element"] == "xm" for f in fs), fs)
ok("day count finding is high and fixable", all(f["severity"] == "high" and f["fixable"] for f in fs if f["kind"] == "number"), fs)
ok("number finding names both counts", "7" in fs[0]["message"] and "8" in fs[0]["message"], fs[0])

r = site()
article(r / "gr/top.html", "Top 10 Things to Do in Greece")
article(r / "gr/index.html", "Greece", body=btn("top.html", "Top 12") + card("top.html", "Top 10 Things to Do in Greece"))
fs = run(r, "gr")
ok("button number disagreeing with Top N", kinds(fs) == ["number"] and fs[0]["element"] == "button", fs)
ok("a button is never fixable", not fs[0].get("fixable"), fs)

r = site()
article(r / "gr/top.html", "15 Best Things to Do in Greece")
article(r / "gr/a.html", "A", body=xm("top.html", "Top 12 Things to Do in Greece"))
ok("N things vs Top N share a unit", "number" in kinds(run(r, "gr")), run(r, "gr"))

r = site()
article(r / "gr/x.html", "Three Days in Athens")
article(r / "gr/a.html", "A", body=xm("x.html", "3 Days in Athens"))
ok("number words count as the number", "number" not in kinds(run(r, "gr")), run(r, "gr"))

n = cc.numbers("1 to 2 days, Top 10, 48 hours, 7-day trek")
ok("numbers(): ranges, top and hyphens", n.get("day") == {1, 2, 7} and n.get("count") == {10} and n.get("hour") == {48}, n)

# ---- title wording -------------------------------------------------------------------------------------------------------
r = site()
article(r / "gr/m.html", "Meteora — Ancient Monasteries &amp; Greece's Best Sunsets", title="Meteora - getawayguide")
article(r / "gr/a.html", "A", body=xm("m.html", "Meteora: Ancient Monasteries and Greece’s Best Sunsets"))
ok("quotes, & and separators don't count as a difference", run(r, "gr") == [], run(r, "gr"))

r = site()
article(r / "gr/m.html", "One H1 Title", title="Different Title - getawayguide", og="Og Title")
article(r / "gr/a.html", "A", body=card("m.html", "Different Title") + card("m.html", "Og Title", "b.jpg") + card("m.html", "One H1 Title", "c.jpg"))
ok("matching any of h1, <title> or og:title is enough", run(r, "gr") == [], run(r, "gr"))

r = site()
article(r / "gr/m.html", "The Real Title")
article(r / "gr/a.html", "A", body=card("m.html", "Some Other Words"))
fs = run(r, "gr")
ok("a different title is reported medium", kinds(fs) == ["title"] and fs[0]["severity"] == "medium", fs)
ok("the finding shows found and expected", fs[0]["found"] == "Some Other Words" and fs[0]["expected"] == "The Real Title", fs)

r = site()
article(r / "gr/field-notes.html", "Greece Travel Guide")
article(r / "gr/k.html", "Krushevo Guide")
article(r / "gr/index.html", "Greece", body=card("field-notes.html", "Greece Field Notes: What I Learned on the Ground")
        + gb("k.html#prilep", "A Day Trip to Prilep"))
ok("field-notes cards and #section cards are exempt", run(r, "gr") == [], run(r, "gr"))

# ---- description ----------------------------------------------------------------------------------------------------------
r = site()
article(r / "gr/m.html", "M", desc="Field notes from Greece: The long way around")
article(r / "gr/a.html", "A", body=xm("m.html", "M", "The long way around"))
ok("the Field-notes-from prefix is ignored, as explore_more strips it", run(r, "gr") == [], run(r, "gr"))

r = site()
article(r / "gr/m.html", "M", desc="Something fresh")
article(r / "gr/a.html", "A", body=xm("m.html", "M", "Something stale"))
fs = run(r, "gr")
ok("a stale description is reported low and never fixable", kinds(fs) == ["description"] and fs[0]["severity"] == "low" and not fs[0]["fixable"], fs)

r = site()
article(r / "gr/i.html", "Itin", desc="An 8 day route")
article(r / "gr/a.html", "A", body=xm("i.html", "Itin", "A 7 day route"))
fs = run(r, "gr")
ok("a description's day count clash is high", kinds(fs) == ["number"] and fs[0]["severity"] == "high", fs)

# ---- dup photo -----------------------------------------------------------------------------------------------------------
ok("photo_key: format and size suffixes collapse",
   len({cc.photo_key(u) for u in ("../Images/web/x/card-skopje.jpg", "card-skopje.webp", "card-skopje-1499w.jpg", "y/card-skopje-mob-2x.webp")}) == 1)
r = site()
article(r / "gr/a.html", "A")
article(r / "gr/b.html", "B")
article(r / "gr/index.html", "G", body=card("a.html", "A", "p/one.jpg") + card("b.html", "B", "q/one.webp"))
fs = run(r, "gr")
ok("two cards on one photo", kinds(fs) == ["dup-photo"] and fs[0]["severity"] == "high", fs)
r = site()
article(r / "gr/a.html", "A")
article(r / "gr/b.html", "B")
article(r / "gr/index.html", "G", body=card("a.html", "A", "one.jpg") + card("b.html", "B", "two.jpg"))
ok("different photos are fine", run(r, "gr") == [], run(r, "gr"))

r = site()
article(r / "gr/a.html", "A")
article(r / "gr/b.html", "B")
html_a = ('<a href="a.html" class="country-article-card img-one" style="display:block"><h3 class="country-article-card-title">A</h3></a>\n'
          '<a href="b.html" class="country-article-card img-one" style="display:block"><h3 class="country-article-card-title">B</h3></a>\n')
article(r / "gr/index.html", "G", body=html_a)
fs = run(r, "gr")
ok("anchor-style cards are read, and a shared img- class is a shared photo", kinds(fs) == ["dup-photo"], fs)

# ---- live link -----------------------------------------------------------------------------------------------------------
r = site()
article(r / "mk/skopje.html", "Skopje")
article(r / "Drafts/Upcoming/mk/skopje.html", "Skopje")
article(r / "Drafts/Upcoming/mk/index.html", "MK", body=card("../../../mk/skopje.html", "Skopje"))
fs = run(r, "Drafts/Upcoming/mk")
ok("a draft card pointing at the live page, sibling draft exists", kinds(fs) == ["live-link"] and fs[0]["severity"] == "high", fs)

r = site()
article(r / "mk/skopje.html", "Skopje")
article(r / "Drafts/Upcoming/mk/index.html", "MK", body=card("../../../mk/skopje.html", "Skopje"))
ok("no sibling draft: pointing at live is right", run(r, "Drafts/Upcoming/mk") == [], run(r, "Drafts/Upcoming/mk"))

r = site()
article(r / "mk/field-notes.html", "MK Field Notes")
article(r / "Drafts/Field Notes/mk/field-notes.html", "MK Field Notes")
article(r / "Drafts/Full Articles/mk/index.html", "MK", body=card("../../../mk/field-notes.html", "MK Field Notes"))
fs = run(r, "Drafts/Full Articles/mk")
ok("a sibling draft in another draft folder counts", kinds(fs) == ["live-link"], fs)

r = site()
article(r / "mk/skopje.html", "Skopje")
article(r / "mk/index.html", "MK", body=card("skopje.html", "Skopje"))
ok("a live folder is never flagged live-link", run(r, "mk") == [], run(r, "mk"))

# ---- missing and order ----------------------------------------------------------------------------------------------------
r = site()
article(r / "gr/index.html", "G", body=card("gone.html", "Gone"))
fs = run(r, "gr")
ok("a card pointing nowhere", kinds(fs) == ["missing"] and fs[0]["severity"] == "high", fs)
r = site()
article(r / "gr/index.html", "G", body=btn("#a", "A") + card("https://x.example/", "Ext"))
ok("anchors and external links are not checked", run(r, "gr") == [], run(r, "gr"))

r = site()
article(r / "gr/a.html", "A")
article(r / "gr/b.html", "B")
article(r / "gr/index.html", "G", body=btn("a.html", "A") + btn("b.html", "B") + card("b.html", "B", "1.jpg") + card("a.html", "A", "2.jpg"))
fs = run(r, "gr")
ok("buttons vs cards in a different order", kinds(fs) == ["order"] and fs[0]["severity"] == "medium", fs)
r = site()
article(r / "gr/a.html", "A")
article(r / "gr/b.html", "B")
article(r / "gr/c.html", "C")
article(r / "gr/index.html", "G", body=btn("a.html", "A") + btn("b.html", "B") + card("c.html", "C", "0.jpg") + card("a.html", "A", "1.jpg") + card("b.html", "B", "2.jpg"))
ok("a card with no button doesn't count; same relative order is fine", run(r, "gr") == [], run(r, "gr"))
r = site()
article(r / "gr/a.html", "A")
article(r / "gr/b.html", "B")
article(r / "gr/other.html", "O", body=btn("a.html", "A") + btn("b.html", "B") + card("b.html", "B", "1.jpg") + card("a.html", "A", "2.jpg"))
ok("order is only checked on an index", "order" not in kinds(run(r, "gr")), run(r, "gr"))

# ---- reporting writes nothing --------------------------------------------------------------------------------------------
r = site()
article(r / "gr/m.html", "The Real Title")
article(r / "gr/a.html", "A", body=card("m.html", "Wrong"))
before = {p: p.read_bytes() for p in (r / "gr").glob("*.html")}
cc.check([r / "gr"], r)
ok("a report writes nothing", all(p.read_bytes() == b for p, b in before.items()))

# ---- --fix ---------------------------------------------------------------------------------------------------------------
NOGUARD = lambda p: []
r = site()
article(r / "gr/i.html", "The Perfect 8 Day Itinerary", body="<p>x</p>")
page = (b"\xef\xbb\xbf<!DOCTYPE html>\r\n<html><head><title>Index</title></head>\n<body><h1>Index</h1>\r\n"
        + card("i.html", "The Perfect 7 Day Itinerary").encode() + b"\t<p>  odd   spacing\xc2\xa0kept </p>\r\n"
        + xm("i.html", "Perfect 7 Day", "Stale description here").encode() + b"\n</body></html>\r\n")
(r / "gr/index.html").write_bytes(page)
lines = cc.fix(run(r, "gr"), r, guard=NOGUARD)
after = (r / "gr/index.html").read_bytes()
expected = page.replace(b"The Perfect 7 Day Itinerary", b"The Perfect 8 Day Itinerary").replace(b"Perfect 7 Day<", b"The Perfect 8 Day Itinerary<")
ok("--fix mirrors the title and byte-preserves everything else (BOM, CRLF, spacing)", after == expected, after[:200])
ok("--fix leaves a stale description alone", b"Stale description here" in after)
ok("--fix says what it changed", sum(l.startswith("  ") for l in lines) == 2, lines)
ok("--fix fixed it: no number or title finding is left", not [f for f in run(r, "gr") if f["kind"] in ("number", "title")], run(r, "gr"))
again = cc.fix(run(r, "gr"), r, guard=NOGUARD)
ok("--fix a second time changes nothing", again == [] and (r / "gr/index.html").read_bytes() == expected, again)

r = site()
article(r / "gr/m.html", "H1 Words", title="Other Words")
article(r / "gr/a.html", "A", body=card("m.html", "Third Words"))
fs = run(r, "gr")
ok("h1 and <title> disagree: reported, not fixable", kinds(fs) == ["title"] and not fs[0]["fixable"], fs)
before = (r / "gr/a.html").read_bytes()
cc.fix(fs, r, guard=NOGUARD)
ok("--fix leaves the ambiguous one alone", (r / "gr/a.html").read_bytes() == before)

r = site()
article(r / "gr/field-notes.html", "Greece 3 Day Notes")
article(r / "gr/a.html", "A", body=card("field-notes.html", "Greece 4 Day Notes"))
fs = run(r, "gr")
ok("a field-notes target's number is reported but not fixable", kinds(fs) == ["number"] and not fs[0]["fixable"], fs)
before = (r / "gr/a.html").read_bytes()
cc.fix(fs, r, guard=NOGUARD)
ok("--fix never touches a field-notes card", (r / "gr/a.html").read_bytes() == before)

r = site()
article(r / "gr/a.html", "A Title")
article(r / "gr/index.html", "G", body=btn("a.html", "Top 3") + gb("a.html", "Wrong Words"))
fs = run(r, "gr")
cc.fix(fs, r, guard=NOGUARD)
s = (r / "gr/index.html").read_text(encoding="utf-8")
ok("--fix mirrors a guide-card title, never a button", '<span class="h">A Title</span>' in s and ">Top 3</button>" in s, s)

r = site()
article(r / "gr/m.html", "The Real Title")
article(r / "gr/a.html", "A", body=card("m.html", "Wrong"))
fs = run(r, "gr")
before = (r / "gr/a.html").read_bytes()
lines = cc.fix(fs, r, guard=lambda p: ["3 comment(s) by Kevin"])
ok("--fix refuses a file Kevin has in review", (r / "gr/a.html").read_bytes() == before and lines[0].startswith("SKIPPED"), lines)

r = site()
article(r / "gr/m.html", "Tom &amp; Jerry <em>Live</em>")
article(r / "gr/a.html", "A", body=card("m.html", "Wrong"))
# a tag inside the h1 makes <title> differ only by markup: strip it for the mirror
(r / "gr/m.html").write_bytes(b"<html><head><title>Tom &amp; Jerry Live</title></head><body><h1>Tom &amp; Jerry <em>Live</em></h1></body></html>")
cc.fix(run(r, "gr"), r, guard=NOGUARD)
ok("the mirror drops h1 markup and keeps its entities", "Tom &amp; Jerry Live</h3>" in (r / "gr/a.html").read_text(encoding="utf-8"), (r / "gr/a.html").read_text(encoding="utf-8"))

# ---- reused lines ----------------------------------------------------------------------------------------------------------
STOCK = "you&rsquo;ll have most of the sights to yourself"


def prose(*paras):
    return '<div class="artbody">' + "".join("<p>%s</p>" % p for p in paras) + "</div>"


def reused(r, folder):
    return [f for f in run(r, folder) if f["kind"] == "reused-line"]


r = site()
for n in ("a", "b", "c", "d", "e"):
    article(r / ("mk/%s.html" % n), n.upper(), body=prose("Outside of Ohrid in summer %s, though buses are slow." % STOCK, "Page %s is about something else entirely here." % n))
fs = reused(r, "mk")
ok("the stock phrase on 5 pages is one finding", len(fs) == 1 and len(fs[0]["pages"]) == 5, fs)
ok("reused-line is low severity and never fixable", fs and fs[0]["severity"] == "low" and fs[0]["fixable"] is False, fs)
ok("the finding names its words and the sentence on each page",
   fs and fs[0]["words"] >= 8 and all("sights to yourself" in p["sentence"] and p["sentence"].startswith("Outside") for p in fs[0]["pages"]), fs)
ok("the finding gives each page's line", fs and all(p["line"] > 1 for p in fs[0]["pages"]), fs)

r = site()
article(r / "mk/a.html", "A", body=prose("Out in the hills, YOU'LL have most of the sights; to yourself!"))
article(r / "mk/b.html", "B", body=prose("you’ll have most of the sights to yourself, at dawn"))
ok("case, punctuation and curly quotes don't count", len(reused(r, "mk")) == 1, reused(r, "mk"))

r = site()
article(r / "mk/a.html", "A", body=prose("You will have most of the sights to yourself here."))
article(r / "mk/b.html", "B", body=prose("You might have most of those sights all to yourself here."))
ok("seven shared words is not a run", reused(r, "mk") == [], reused(r, "mk"))

r = site()
article(r / "mk/a.html", "A", body=prose("I loved it so much that I went back the next morning to see it."))
article(r / "mk/b.html", "B", body=prose("Nothing here repeats, I promise you, so there is nothing to find."))
ok("a run on only one page isn't reported, even if the page repeats it", reused(r, "mk") == [], reused(r, "mk"))
r = site()
article(r / "mk/a.html", "A", body=prose("I loved it so much that I went back the next morning.", "I loved it so much that I went back the next morning."))
ok("a line repeated within one page is not a cross-page reuse", reused(r, "mk") == [], reused(r, "mk"))

r = site()
article(r / "mk/a.html", "A", body=prose("The bus was late and I loved it anyway because the driver sang the whole way to the coast."))
article(r / "mk/b.html", "B", body=prose("The bus was late and I loved it anyway because the driver sang the whole way to the coast."))
article(r / "mk/c.html", "C", body=prose("One morning, and I loved it anyway because the driver sang."))
fs = reused(r, "mk")
ok("a long run and its shorter copy on a third page are reported once", len(fs) == 1 and len(fs[0]["pages"]) == 3, fs)
ok("the run reported is the longest one", fs and fs[0]["words"] == 19, fs)

r = site()
long_run = "We walked across the empty square at dawn and watched the old men feed the pigeons while the bells rang out"
for n in ("a", "b"):
    article(r / ("x/%s.html" % n), n, body=prose(long_run + "."))
for n in ("a", "b", "c", "d", "e"):
    article(r / ("y/%s.html" % n), n, body=prose("Outside of Ohrid in summer %s." % STOCK))
fs = cc.reused_only(cc.check([r / "x", r / "y"], r))
ok("more pages outranks a longer run on two pages", fs[0]["country"] == "y" and fs[1]["country"] == "x", [f["message"] for f in fs])

r = site()
article(r / "x/a.html", "A", body=prose("We walked across the empty square at dawn and watched the pigeons."))
article(r / "y/b.html", "B", body=prose("We walked across the empty square at dawn and watched the pigeons."))
ok("two folders are never compared", cc.check([r / "x", r / "y"], r) == [], cc.check([r / "x", r / "y"], r))

# what is not body prose
r = site()
LINE = "Prices were correct when I visited but always check before you book"
shell = ('<nav><p>%s</p></nav><div class="toc-b"><p>%s</p></div><footer><p>%s</p></footer>' % (LINE, LINE, LINE))
fig = '<figure><figcaption>%s</figcaption></figure><div class="route-map-wrap"><p>%s</p></div><div class="citymap-fig"><p>%s</p></div>' % (LINE, LINE, LINE)
foot = '<p class="article-foot">%s</p><div class="gg-footnote"><p>%s</p></div><div class="aff-note"><p>%s</p></div>' % (LINE, LINE, LINE)
xmb = '<div class="xm"><p>%s</p></div>' % LINE
for n in ("a", "b"):
    article(r / ("mk/%s.html" % n), n, body=shell + '<div class="artbody">' + fig + foot + xmb + "<h2>%s</h2><h3>%s</h3></div>" % (LINE, LINE))
ok("nav, footer, contents box, captions, maps, template lines, Explore More and headings are out", reused(r, "mk") == [], reused(r, "mk"))

r = site()
for n in ("a", "b"):
    article(r / ("mk/%s.html" % n), n, body=prose("Plain words here. Prices were correct when I visited but always check before you book. More words."))
ok("the same line in real prose is found", len(reused(r, "mk")) == 1, reused(r, "mk"))

# exclusions
r = site()
for n in ("a", "b", "c"):
    article(r / ("pe/%s.html" % n), n, body=prose("Cusco is where I started. The whole route is in my Peru itinerary, and the short version lives in my Peru field notes."))
ok("a sentence that only points at a sibling page is left out", reused(r, "pe") == [], reused(r, "pe"))
r = site()
for n in ("a", "b"):
    article(r / ("pe/%s.html" % n), n, body=prose("Everything in town is on the map below if you need it.", "My guide picked me up at 8am, and we drove off."))
fs = reused(r, "pe")
ok("'on the map below' is out, 'my guide' (a person) is not", len(fs) == 1 and "guide picked" in fs[0]["run"], [f["run"] for f in fs])
r = site()
for n in ("a", "b"):
    article(r / ("pe/%s.html" % n), n, body=prose("Go easy on the street carts for your first few days (my Delhi guide explains why)."))
ok("real prose with a pointer at its end is still reported", len(reused(r, "pe")) == 1, reused(r, "pe"))
r = site()
for n in ("a", "b"):
    article(r / ("pe/%s.html" % n), n, body=prose("The full lists are in the Where to Eat sections of my Medellín and Cartagena guides."))
ok("a pointer to several guides is left out", reused(r, "pe") == [], reused(r, "pe"))

r = site()
title = "The Perfect Eight Day Route Through North Macedonia"
article(r / "pe/t.html", title, body=prose("Read about it in The Perfect Eight Day Route Through North Macedonia."))
article(r / "pe/u.html", "U", body=prose("My friend wrote The Perfect Eight Day Route Through North Macedonia."))
ok("a run that only repeats a page title is left out", reused(r, "pe") == [], reused(r, "pe"))

r = site()
for n in ("a", "b"):
    article(r / ("pe/%s.html" % n), n, body=prose("Buses run about every hour from 6 AM to 8:30 PM for about $5.", "Quiet walks along Calle de la Cochera del Hobo and Calle de Don Sancho."))
ok("figures and runs of proper names are left out", reused(r, "pe") == [], reused(r, "pe"))
ok("numbers count as one word each", "830" in cc.prose_blocks(r / "pe/a.html")[0]["words"], cc.prose_blocks(r / "pe/a.html")[0]["words"])

r = site()
for n in ("a", "b"):
    article(r / ("pe/%s.html" % n), n, body=prose("Meet me at 21 Costa Rica Street near the old market downtown."))
ok("an address is left out", reused(r, "pe") == [], reused(r, "pe"))
r = site()
for n in ("a", "b"):
    article(r / ("pe/%s.html" % n), n, body=prose("I stayed on Costa Rica Street for a little over 2 weeks last year."))
ok("a street name in a sentence is not an address", len(reused(r, "pe")) == 1, reused(r, "pe"))

r = site()
NAME = '"The Best Little Coffee Stand on the whole Dalmatian coast"'
article(r / "pe/a.html", "A", body=prose("Locals call it %s without irony." % NAME))
article(r / "pe/b.html", "B", body=prose("A sign out front read %s in gold." % NAME))
ok("a quoted name is left out", reused(r, "pe") == [], reused(r, "pe"))

# field notes
r = site()
article(r / "am/field-notes.html", "FN", body=prose("Trains run most days. Flatbread baked in an underground oven and served with every meal."))
article(r / "am/yerevan.html", "Y", body=prose("Flatbread baked in an underground oven and served with every meal, everywhere."))
ok("field notes repeating a guide's fact is left out", reused(r, "am") == [], reused(r, "am"))
r = site()
article(r / "am/field-notes.html", "FN", body=prose("It's safe and cheap, and outside of Ohrid %s." % STOCK))
article(r / "am/top-10.html", "Y", body=prose("It's compact, and outside of Lake Ohrid in the summer %s." % STOCK))
ok("field notes sharing a line in his voice is reported", len(reused(r, "am")) == 1, reused(r, "am"))
r = site()
sent = "A faded old mining town near the border where the abandoned factories still loom over the river and the road just ends"
article(r / "am/field-notes.html", "FN", body=prose(sent + "."))
article(r / "am/yerevan.html", "Y", body=prose(sent + "."))
ok("a whole copied sentence from field notes is reported even with no first person", len(reused(r, "am")) == 1, reused(r, "am"))
r = site()
FACT = "flatbread baked in an underground oven and served with every meal"
article(r / "am/field-notes.html", "FN", body=prose("Mostly %s." % FACT))
article(r / "am/yerevan.html", "Y", body=prose("Mostly %s." % FACT))
article(r / "am/gyumri.html", "G", body=prose("Mostly %s." % FACT))
ok("the same words on two guides as well are reported", len(reused(r, "am")) == 1, reused(r, "am"))

r = site()
sent = "It was a wonderful place to spend a long and lazy afternoon"
article(r / "mk/a.html", "A", body=prose(sent + "."))
article(r / "mk/b.html", "B", body=prose(sent + "."))
article(r / "mk/m.html", "The Real Title")
article(r / "mk/c.html", "C", body=card("m.html", "Wrong"))
fs = run(r, "mk")
ok("reused and consistency findings sit side by side", sorted(kinds(fs)) == ["reused-line", "title"], kinds(fs))
before = {p: p.read_bytes() for p in (r / "mk").glob("*.html")}
lines = cc.fix(fs, r, guard=NOGUARD)
after = {p: p.read_bytes() for p in (r / "mk").glob("*.html")}
ok("--fix leaves reused lines alone", after[r / "mk/a.html"] == before[r / "mk/a.html"] and after[r / "mk/b.html"] == before[r / "mk/b.html"], lines)
ok("--fix only made the title change", [p for p in after if after[p] != before[p]] == [r / "mk/c.html"] and len(lines) == 1, lines)
fs = run(r, "mk")
text = cc.plain(fs, 1)
ok("plain report has a reused-lines section with the sentences", "Reused lines: 1" in text and "lazy afternoon" in text and "a.html:" in text, text)
md = cc.markdown(fs, [1])
ok("markdown report has a reused-lines section", "## Reused lines (1" in md and "lazy afternoon" in md, md)
blob = json.dumps(fs, ensure_ascii=False)
ok("--json carries the run and its pages", '"reused-line"' in blob and '"sentence"' in blob and '"run"' in blob, blob[:300])
nobody = cc.plain([f for f in fs if f["kind"] != "reused-line"], 1)
ok("no reused lines: no reused section", "Reused lines" not in nobody, nobody)

s = "Long intro goes here. It was a wonderful place to spend a day. And then it was over."
i = s.index("wonderful")
ok("sentence_around returns the sentence holding the run", cc.sentence_around(s, i, i + 9) == "It was a wonderful place to spend a day.", cc.sentence_around(s, i, i + 9))

# ---- folders and output ----------------------------------------------------------------------------------------------------
r = site()
for d in ("gr", "mk", "tools", "Images", "archive"):
    article(r / d / "index.html", d)
article(r / "Drafts/Upcoming/xk/a.html", "A")
article(r / "Drafts/Full Articles/br/a.html", "A")
article(r / "Drafts/Field Notes/cu/field-notes.html", "C")
article(r / "Drafts/.Archive/old/a.html", "A")
names = sorted(p.name for p in cc.all_folders(r))
ok("every live and draft folder, not tools/Images/archive/.Archive", names == ["br", "cu", "gr", "mk", "xk"], names)
ok("a country name finds its folders", sorted(cc.rel(p, r) for p in cc.resolve_folders(["xk"], r)) == ["Drafts/Upcoming/xk"], cc.resolve_folders(["xk"], r))
ok("draft-folder test", cc.is_draft_folder(r / "Drafts/Upcoming/xk", r) and not cc.is_draft_folder(r / "gr", r))

r = site()
article(r / "aa/m.html", "T")
article(r / "aa/a.html", "A", body=card("m.html", "Wrong"))
article(r / "zz/m.html", "T")
article(r / "zz/a.html", "A", body=card("m.html", "Wrong", "1.jpg") + card("gone.html", "G", "2.jpg"))
article(r / "aa/index.html", "AA")
article(r / "zz/index.html", "ZZ")
fs = cc.check(None, r)
order = [c for c, _ in cc.grouped(fs)]
ok("most findings first", order == ["zz", "aa"], order)
p = cc.plain(fs, 2)
ok("plain report is per country with counts", "zz: 2 finding(s)" in p and "aa: 1 finding(s)" in p, p)
m = cc.markdown(fs, [1, 2])
ok("markdown report groups by country", m.index("## zz") < m.index("## aa"), m)
json.dumps(fs, ensure_ascii=False)                # --json must be serializable
ok("findings serialize to JSON", True)

shutil.rmtree(TMP, ignore_errors=True)
sys.stdout.buffer.write(("%d passed, %d failed\n" % (passed, failed)).encode("utf-8"))
sys.exit(1 if failed else 0)
