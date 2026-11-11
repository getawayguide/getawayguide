"""Deterministic pre-review check for an article draft: the mechanical asks Kevin keeps writing in the margin.

Kevin's review history (.tmp/kevin_reviewer/model.md) shows the commonest margin comments are mechanical: "link" on a
place name that is linked somewhere else but bare here, and "fill out" on a [ ] he left as a placeholder. Those are
predictable enough that a script finds them more cheaply and reliably than another AI pass (Kevin, 2026-11-08, the
review-loop pilot on Greece and Türkiye). Reports by default; can also turn bare-name findings into edits.

    python tools/draft_check.py "Drafts/Full Articles/greece/athens.html"
    python tools/draft_check.py <article> [<article> ...] --json      # machine-readable, for the loop's round builder
    python tools/draft_check.py <unreviewed draft> --apply            # writes the missing links into the file
    python tools/draft_check.py <reviewed draft> --round-changes out.json   # writes round changes, nothing else
    python tools/draft_check.py <reviewed draft> --round-changes out.json --since-round last   # his own new text only

Checks (article body only: no nav, hero, captions, contents boxes or hidden layout variants):
  placeholder  a "[ ]", "[PHOTO]", "[?]", "TK" or "XX" left in a paragraph or bullet
  bare-name    a place, sight or country name that has a link somewhere (this article, its sibling articles in the same
               folder, the city-map pins for this article, or a country page in the site nav) but not where it stands.
               Tuned to how Kevin asks (NM itinerary r4/r5, 2026-11-08):
                 - a name with its OWN ARTICLE on the site (a city guide, a country page) is reported at its first bare
                   mention in each section (h2) that never links it: he asked for "Bulgaria" and "Montenegro" again
                   in a later section even though an earlier one linked them;
                 - any other name (a sight's Maps link, a map pin) only at its first bare mention when this article
                   never links it at all: he doesn't want "Acropolis" linked in every paragraph.
               The article's own subject ("Athens" on the Athens guide) and its own country ("Greece" in a Greece
               article) are never reported.

Filters on bare-name, in report and edit modes (tuned on all 209 drafts, 2026-11-09). A mention that fails one is
skipped and the next mention of the name is tried:
  own-section      the href is this very page ("Burek" -> this page's #food): a sibling's link back, nothing to add here.
  demonym          "Albanian", "Greek border", "Brazilian side": an adjective, not a place. "Albanian Riviera" stays.
  border-only      "along the border with Kosovo", "the Queensland border": his rule, "no - just a reference to the
                   border not anything I did in the country" (NM itinerary + top 10). "across the border in Serbia" stays.
  told-not-to-use  "don't count on Uber or BiTaksi", "Uber doesn't operate in Serbia", "no Uber": linking an app the
                   sentence warns off is something he could say no to (Cappadocia, 2026-11-08).
  part-of-name     "Germany" in "Nazi Germany", "Reuss" in "Reuss River", "Hungary" in "Austria-Hungary": a link on half
                   a proper name is what he then asks to "extend".
Link text of the form "The tour" (The + lowercase) is not treated as a name either: it reads as a sentence, not a place.

Filters on the link's TARGET (added 2026-11-10, after 3 of 40 spot-checked links from the first big --apply were wrong and
all 591 were reverted). A link is only carried when the name is plainly what the target is about; a missed link is
cheap and a wrong one is not. Unsure means reported in "filtered", never written:
  generic-name     nothing distinctive left once the type words are gone ("Tours", "Tour A").
  deep-link        a brand or operator whose link is one specific page: "Hostelworld" -> one hostel, "Greyhound" -> one
                   route, "Department of Conservation" -> one park. Fine only if the name itself is in the path
                   ("Casa Samay Mountain View" -> that listing). A home page or locale root is fine.
  name-not-target  the name is not what the target is: "Palawan" -> the Coron guide, "Patagonia" -> a trek guide, "Zagreb" ->
                   the Croatia notes, "Tours" -> an operator. Compares the name with the page slug or the Maps place.
  section-mismatch the href ends in a #section that is not this name: "Manila" -> #where-to-eat, "Amazon" -> #night-bus.
                   "Kutaisi" -> #kutaisi and "Rio" -> #rio-de-janeiro stay.
  different-place  a Maps pin that is a narrower or different thing than the name: "Munich" -> Munich Airport, "Woolworths"
                   -> the Byron Bay store, "Miami" -> Miami QLD 4220.
  conflicting-targets  siblings link the name to different places and nothing says which is meant.
And on the MENTION:
  attributive      the name only modifies the next word: "Manila airport", "my Oaxaca hostel", "Brussels kind", "Patagonia
                   clothing logo", "Rio de Janeiro state", "Coron tour".
  aside            a comparison or where someone is from: "a lot like Greece or Croatia", "Australia's answer to Miami",
                   "a woman from Sao Paulo", "used to live in Miami".
  abbreviation     "(GWK)", "(DPS)": a code in brackets beside the full name.
  short-form       a short form right next to a linked full form: "Garuda Wisnu Kencana Park, GWK", "Rio" beside "Rio de Janeiro".
  passing-country  a country name gets a link only where the sentence is about going there: flew/bus/train/ferry to or from
                   it, crossing into it, days in it, "From Uruguay —", or opening the sentence. Not "Germany" in a WWI
                   aside or "Belgium" as a stone's source.

Edits (bare-name only, never placeholders; a name with no href, i.e. a map pin, is skipped, never made up):
  --apply          for drafts Kevin hasn't reviewed. Refused if the file has a thread or reply by Kevin or any round
                   (decided or still open, since a direct edit would break an open round's finds).
  --round-changes  for drafts he has reviewed. Writes a JSON list of proposal.json changes; opens nothing. Each find is
                   text only (no tags), matches once in the raw file, and stays clear of an open round's own finds.
  --since-round    with --round-changes: only text new since that round's baseline ("last" = this article's highest-numbered
                   round), the sweep for when he says "address comments" (Kevin, 2026-11-10). The round's accepted changes count
                   as old, so what's left is what he typed. Inside new text every town gets a link unless it's already linked in
                   that same paragraph, second mentions included ("sweep every passage new since the last round"). Blanks in the
                   new text are listed in <out>.blanks.json for the round builder to fill (logistics: look them up, never ask).
"""
import argparse, difflib, html as htmlmod, json, re, sys, unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
REDLINE = ROOT / ".tmp" / "redline"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from redline import comments_key


def _subject_names(rel, html):
    """the names an article is about, from tools/crosslink.py (imported lazily: crosslink imports this module for its
    name rules, so a top-level import either way would be circular). None when the launch tools are absent."""
    try:
        from crosslink import article_names
    except Exception:                                # the check still runs without the launch tools
        return []
    return article_names(rel, html)

SKIP_CLASSES = ("img-caption", "toc", "comp-h", "toc-sub", "toc-e", "v-head", "whm2", "cconv", "kf", "f-b", "f-h",
                "gb-card", "xm", "gg-updated", "artsub", "kick", "day-head", "mm-")
VOID = {"br", "img", "hr", "source", "input", "meta", "link", "wbr"}
PLACEHOLDER_RE = re.compile(r"\[\s*\]|\[PHOTO\]|\[\?\]|\bTK\b|\bXX\b")
GENERIC = {"here", "this", "the", "map", "guide", "website", "link", "book", "tickets", "booking", "official site",
           "google maps", "instagram", "read more", "this guide", "my guide", "the city", "old town", "the old town",
           "beach", "the beach", "airport", "bus station", "the port"}

DEMONYMS = set("""Albanian Argentine Argentinian Armenian Australian Austrian Balkan Belgian Bolivian Bosnian Brazilian
    British Bulgarian Byzantine Canadian Chilean Chinese Colombian Croat Croatian Cuban Czech Danish Dutch Ecuadorian
    Egyptian English Estonian Filipina Filipino Finnish French Georgian German Greek Guatemalan Hungarian Icelandic
    Indian Indonesian Irish Israeli Italian Japanese Jordanian Kenyan Korean Kosovan Kosovar Latvian Lebanese Lithuanian
    Macedonian Malaysian Mexican Moldovan Montenegrin Moroccan Nepalese Nepali Nicaraguan Norwegian Ottoman Panamanian
    Paraguayan Peruvian Philippine Polish Portuguese Roman Romanian Russian Salvadoran Salvadorean Scandinavian Scottish
    Serb Serbian Slovak Slovakian Slovene Slovenian Soviet Spanish Swedish Swiss Tanzanian Thai Tunisian Turk Turkish
    Ukrainian Uruguayan Venezuelan Vietnamese Welsh Yugoslav""".split())
# a capitalized neighbor that is only sentence-start or title-case grammar, so it does not make a longer proper name
NOT_PART_OF_NAME = set("""a about above across after again against all along also although an and are around as at back
    because been before behind below beside besides best between beyond both but by central day days discover do does
    drive driving during each early eastern either even every explore first five flight flights fly flying for four from
    get getting go going good great had has have he head heading her here his how however i i'd i'll i'm i've if in
    inside into is it it's its just last late later leave leaving less like many me monument more morning most my near
    neither next night nights no northern not now of on once one only onto or our outside over past reach return
    returning second see she since so some southern still stay staying take than that the their them then there these
    they third this those three through to today tomorrow toward towards try two under unlike until us via visit walk
    walking was we we'd we'll we've were western what when where which while who why with within without worst yet you
    you'd you'll you've your""".split())
NOT_OF_NAME = NOT_PART_OF_NAME | set("north south east west end edge start top bottom side middle part rest heart center".split())
OF_WORDS = {"of", "de", "del", "di", "da", "du", "dos", "das", "do", "la", "le", "el", "van", "von"}
_LIST = r"(?:[\w'\-:]+(?:,\s*|\s+and\s+|\s+or\s+))*"                # "Uber or " / "Uber, Bolt or "
_ARTICLE = r"(?:(?:an?|the|any|your)\s+)?"
BORDER_BEFORE = re.compile(r"\b(?:borders?\s+(?:with|of|between)|border\s+(?:dispute|conflict|war|crossing)s?\s+(?:with|between)"
                           r"|bordering(?:\s+on)?|borders(?:\s+on)?)\s+(?:the\s+)?" + _LIST + r"$", re.I)
BORDER_AFTER = re.compile(r"(?:'s)?(?:\s*[-–/]\s*[\w'\-]+)?\s+(?:border|borders|frontier)\b"
                          r"|\s+(?:and|or)\s+[\w'\-]+\s+(?:border|borders|frontier)\b", re.I)
NOT_USE_BEFORE = re.compile(r"\b(?:(?:don't|do not|never|can't|cannot|won't|shouldn't|wouldn't)\s+"
                            r"(?:count on|rely on|use|bother with|try|book|download|trust|take|expect)"
                            r"|avoid(?:\s+(?:using|taking|booking))?)\s+" + _ARTICLE + _LIST + r"$", re.I)
NOT_USE_AFTER = re.compile(r"(?:(?:,\s*|\s+and\s+|\s+or\s+)[\w'\-:]+)*\s+(?:doesn't|don't|does not|do not|isn't|aren't|won't|can't)\s+"
                           r"(?:operate|work|exist|run|available|an option|reliable|practical)\b", re.I)
NOT_SERVICE_BEFORE = re.compile(r"\b(?:no|instead of|rather than)(?:\s+(?:using|taking))?\s+" + _ARTICLE + _LIST + r"$", re.I)
PAIRED_NAME_AFTER = re.compile(r"\s+and\s+(?:Herzegovina|Barbuda|Tobago)\b")

COUNTRIES = set("""Afghanistan Albania Algeria Andorra Angola Argentina Armenia Australia Austria Azerbaijan Bahamas Bahrain
    Bangladesh Barbados Belarus Belgium Belize Benin Bhutan Bolivia Bosnia Botswana Brazil Brunei Bulgaria Cambodia
    Cameroon Canada Chad Chile China Colombia Congo Croatia Cuba Cyprus Czechia Denmark Djibouti Dominica Ecuador Egypt
    England Eritrea Estonia Eswatini Ethiopia Fiji Finland France Gabon Gambia Georgia Germany Ghana Greece Greenland
    Grenada Guatemala Guinea Guyana Haiti Honduras Hungary Iceland India Indonesia Iran Iraq Ireland Israel Italy Jamaica
    Japan Jordan Kazakhstan Kenya Kiribati Kosovo Kuwait Kyrgyzstan Laos Latvia Lebanon Lesotho Liberia Libya
    Liechtenstein Lithuania Luxembourg Macedonia Madagascar Malawi Malaysia Maldives Mali Malta Mauritania Mauritius
    Mexico Micronesia Moldova Monaco Mongolia Montenegro Morocco Mozambique Myanmar Namibia Nauru Nepal Netherlands
    Nicaragua Niger Nigeria Norway Oman Pakistan Palau Palestine Panama Paraguay Peru Philippines Poland Portugal Qatar
    Romania Russia Rwanda Samoa Scotland Senegal Serbia Seychelles Singapore Slovakia Slovenia Somalia Spain Sudan
    Suriname Sweden Switzerland Syria Taiwan Tajikistan Tanzania Thailand Togo Tonga Tunisia Turkey Türkiye
    Turkmenistan Tuvalu Uganda Ukraine Uruguay Uzbekistan Vanuatu Venezuela Vietnam Wales Yemen Zambia Zimbabwe""".split()) | {
    "Burkina Faso", "Cape Verde", "Costa Rica", "Czech Republic", "Dominican Republic", "East Timor", "El Salvador",
    "Equatorial Guinea", "Ivory Coast", "New Zealand", "North Korea", "North Macedonia", "Papua New Guinea", "Saudi Arabia",
    "Sierra Leone", "South Africa", "South Korea", "South Sudan", "Sri Lanka", "United Arab Emirates", "United Kingdom",
    "United States", "Bosnia and Herzegovina", "Trinidad and Tobago", "Antigua and Barbuda"}
_FOLD_MAP = str.maketrans({"ø": "o", "ł": "l", "đ": "d", "æ": "ae", "œ": "oe", "ı": "i", "ð": "d", "þ": "th"})
NAME_STOP = set("the of a an de del la el los las da do dos das di du le les van von and y e in on at to st saint".split())
# words naming the kind of thing, which a target needn't repeat ("Hvar Town" -> Hvar, "Jajce tour" -> the Jajce day trip)
NAME_TYPE = set("""trip trips tour tours guide guides notes field city town island islands beach beaches falls river rivers lake
    lakes mountain mountains mount valley station ferry train trains bus buses airport park national road bridge church
    temple monastery museum street square market waterfall waterfalls""".split())
# words a Maps place may carry beyond the name and still be the same thing ("Daintree" -> Daintree Rainforest). Deliberately
# missing: airport, station, hotel, restaurant, a street number, a state code: those make it a narrower thing than the name.
PLACE_EXTRA_OK = set("""rainforest forest national park nature reserve falls river lake beach mountain mountains island islands
    temple church cathedral monastery mosque statue monument fortress castle palace city town village old district province
    region state metro area international main grand""".split())
BOOKING_HOSTS = ("hostelworld.", "booking.com", "getyourguide.", "viator.", "klook.", "airbnb.", "tripadvisor.", "agoda.",
                 "expedia.", "hotels.com", "busbud.", "12go.", "bookaway.", "omio.", "instagram.", "facebook.", "tiktok.", "youtube.")
_LOCALE_SEG = re.compile(r"^(?:[a-z]{2}(?:[-_][a-z]{2})?|global|intl|home|index\.html?)$", re.I)
ASIDE_BEFORE = re.compile(r"\b(?:like(?:\s+the)?|reminded\s+me(?:\s+a\s+lot)?\s+of|reminds\s+me\s+of|similar\s+to|compared\s+(?:to|with)"
                          r"|answer\s+to|used\s+to\s+live\s+in|liv(?:e|ed|es|ing)\s+in"
                          r"|(?:guy|girl|man|woman|couple|traveler|friend|kid|family|sister|brother|people|person)s?\s+from)\s+"
                          r"(?:the\s+)?" + _LIST + r"$", re.I)
ATTRIBUTIVE_AFTER = re.compile(r"(?:'s)?\s+(?:airports?|stations?|terminals?|hostels?|hotels?|kind|style|logo|clothing|brand|state|province|county|department"
                               r"|region|group|chats?|apps?|compan(?:y|ies)|airlines?|tours?|trips?|restaurants?|dishes|food|cuisine"
                               r"|wines?|beers?|coffees?|locals?|embassy|consulate)\b", re.I)
_MOVE = (r"fl(?:y|ew|own|ying|ights?)|bus(?:es)?|minibus(?:es)?|vans?|trains?|ferr(?:y|ies)|boats?|cross(?:ed|es|ing)?|head(?:ed|ing|s)?"
         r"|continu(?:e|ed|es|ing)|mov(?:e|ed|es|ing)\s+on|on|back|(?:day|road|side)[\s-]?trips?|trips?|overland|drove|driv(?:e|ing)"
         r"|walk(?:ed|ing)?|went|go|going|came|coming|arriv(?:e|ed|es|ing)|left|leaving|return(?:ed|ing)?|transfer(?:red|s)?|sail(?:ed|ing|s)?"
         r"|enter(?:ed|ing|s)?|connect(?:ed|ing|s)?|before")
COUNTRY_TRAVEL_BEFORE = re.compile(r"(?:\b(?:" + _MOVE + r")(?:\s+(?:in|out|up|down|back|on|over|straight|north|south|east|west))*"
                                   r"\s+(?:to|into|from|through|via|for)\s+(?:the\s+)?"
                                   r"|\b(?:days?|nights?|weeks?|months?|stops?|stay|time|trip|visit|years?)\s+(?:in|around|through|across)\s+(?:the\s+)?"
                                   r"|\bacross\s+the\s+border\s+(?:in|into|to)\s+(?:the\s+)?"
                                   r"|\bbefore\s+(?:the\s+)?)" + _LIST + r"$", re.I)
_PERSON_OR_MODE = re.compile(r"\b(?:i|i'd|i'm|i've|i'll|we|we'd|we've|we'll|my|our|you|you'll|you're|you'd|you've|your"
                             r"|fl(?:y|ew|own|ying|ights?)|bus(?:es)?|minibus(?:es)?|vans?|trains?|ferr(?:y|ies)|boats?)\b", re.I)
COUNTRY_STRONG_BEFORE = re.compile(r"\b(?:on\s+to|(?:day|side)[\s-]?trips?\s+(?:to|into|in)|across\s+the\s+border\s+(?:in|into|to))\s+(?:the\s+)?" + _LIST + r"$", re.I)
COUNTRY_OPENER = re.compile(r"(?:^|[.!?—]\s+)(?:from|to|into|getting\s+to|by\s+\w+\s+from|flying\s+(?:in\s+|into\s+|from\s+|to\s+)?)\s*$", re.I)
_CHARREF = re.compile(r"&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)")
_DOM_SAFE_REF = re.compile(r"&(?:amp|lt|gt|nbsp|mdash|ndash|rsquo|lsquo|hellip);")     # review.js can still find these


def _raw_spans(src, at, d):
    """Where each character of parsed text `d` sits in the raw file: [(start, end), ...]. (-1, -1)s if it can't be told."""
    out, i = [], at
    while len(out) < len(d) and i < len(src):
        m = _CHARREF.match(src, i) if src[i] == "&" else None
        if m:
            out.extend([(i, m.end())] * len(htmlmod.unescape(m.group(0))))
            i = m.end()
        else:
            out.append((i, i + 1))
            i += 1
    return out if len(out) == len(d) and htmlmod.unescape(src[at:i]) == d else [(-1, -1)] * len(d)


def _squeeze(text, spans):
    """re.sub(r"\\s+", " ", text).strip(), keeping the raw-position list in step with the text"""
    t, s = [], []
    for ch, sp in zip(text, spans):
        if ch.isspace():
            if t and t[-1] == " ":
                continue
            ch = " "
        t.append(ch)
        s.append(sp)
    while t and t[-1] == " ":
        t.pop(), s.pop()
    if t and t[0] == " ":
        t.pop(0), s.pop(0)
    return "".join(t), s


class _Blocks(HTMLParser):
    """Text of every outermost <p> / <li> in the article body, with the link texts inside it."""

    def __init__(self, source=""):
        super().__init__(convert_charrefs=True)
        self.stack, self.in_body, self.skip, self.block, self.a, self.section = [], 0, 0, None, 0, 0
        self.blocks = []
        self.source, self.line_at = source, [0] + [m.end() for m in re.finditer("\n", source)]
        self.titles, self.h2 = {0: "Intro"}, None

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        skip = bool(a.get("data-v") and a.get("data-v") != "a") or any(c in cls for c in SKIP_CLASSES) \
            or tag in ("figure", "figcaption", "table", "nav", "header", "footer", "script", "style", "h1", "h2", "h3",
                       "h4", "summary", "details")
        body = "artbody" in cls.split() or "article-body" in cls.split()
        if self.in_body and tag == "h2" and not self.skip:
            self.section += 1
            self.h2 = ""
        self.stack.append((tag, skip, body))
        self.skip += skip
        self.in_body += body
        if self.in_body and not self.skip and self.block is None and tag in ("p", "li"):
            self.block = {"tag": tag, "depth": len(self.stack), "text": "", "map": [], "links": [], "section": self.section}
        elif self.block is not None and tag == "a":
            self.a += 1
            self.block["links"].append({"href": a.get("href") or "", "text": ""})

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        while self.stack:
            t, skip, body = self.stack.pop()
            self.skip -= skip
            self.in_body -= body
            if t == "h2" and self.h2 is not None:
                self.titles[self.section] = re.sub(r"\s+", " ", self.h2).strip()
                self.h2 = None
            if self.block is not None and t == "a" and self.a:
                self.a -= 1
            if self.block is not None and len(self.stack) < self.block["depth"]:
                self.block["text"], self.block["map"] = _squeeze(self.block["text"], self.block["map"])
                if self.block["text"]:
                    self.blocks.append(self.block)
                self.block = None
            if t == tag:
                break

    def handle_data(self, d):
        if self.h2 is not None:
            self.h2 += d
        if self.block is None:
            return
        line, col = self.getpos()
        spans = _raw_spans(self.source, self.line_at[line - 1] + col, d) if self.source else [(-1, -1)] * len(d)
        if self.a:                                          # linked text is fenced off
            d, spans = "\x01" + d + "\x02", [(-1, -1)] + spans + [(-1, -1)]
        self.block["text"] += d
        self.block["map"].extend(spans)
        if self.a and self.block["links"]:
            self.block["links"][-1]["text"] += d.strip("\x01\x02")


def _parse(html):
    p = _Blocks(html)
    p.feed(html)
    return p


def blocks_of(html):
    return _parse(html).blocks


def _name_ok(t):
    t = t.strip()
    return 3 <= len(t) <= 48 and t[0].isupper() and t.lower() not in GENERIC and not re.search(r"\d{3,}|[!?:]$", t) \
        and not re.fullmatch(r"The [^A-Z]+", t)


def link_names(html):
    """{name: href} from the links inside an article body (first href per name)"""
    out = {}
    for b in blocks_of(html):
        for l in b["links"]:
            t = re.sub(r"\s+", " ", l["text"]).strip().strip(".,;")
            if _name_ok(t) and l["href"] and not l["href"].startswith("#"):
                out.setdefault(t, l["href"])
    return out


def link_candidates(html):
    """{name: [every distinct href it carries, in file order]}"""
    out = {}
    for b in blocks_of(html):
        for l in b["links"]:
            t = re.sub(r"\s+", " ", l["text"]).strip().strip(".,;")
            if _name_ok(t) and l["href"] and not l["href"].startswith("#") and l["href"] not in out.setdefault(t, []):
                out[t].append(l["href"])
    return out


def nav_countries(html, rel):
    """{country: href} from the Destinations menu every page carries"""
    out = {}
    for m in re.finditer(r'<a href="([^"]+/(?:field-notes|index)\.html)"><img class="fl"[^>]*><span>([^<]+)</span>', html):
        out.setdefault(htmlmod.unescape(m.group(2)), m.group(1))
    return out


def map_pins(rel):
    """{pin name: None} from the city maps drawn for this article"""
    out = {}
    stem = Path(rel).stem
    for f in (ROOT / "tools" / "city_maps").glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        art = str(d.get("article") or "")
        if f.stem != stem and Path(art).stem != stem:
            continue
        for p in d.get("pois") or []:
            if isinstance(p, dict) and _name_ok(str(p.get("name") or "")):
                out.setdefault(p["name"], None)
    return out


# ---- the false-positive filters (why each exists: module docstring) -------------------------------------------------

def _points_here(href, path):
    """the href is this very page, with or without a #section"""
    if not href or href.startswith(("http:", "https:", "mailto:")):
        return False
    target = href.split("#")[0].split("?")[0]
    return not target or str((path.parent / target).resolve()).lower() == str(path.resolve()).lower()


def _is_demonym(name):
    first, *rest = name.split()
    plural = first.endswith("s") and first[:-1] in DEMONYMS and first not in ("Balkans", "Philippines")   # those two are places
    return (first in DEMONYMS or plural) and all(w[0].islower() for w in rest)


def _service(href):
    """an operator's or app's own website, not a place"""
    return bool(href) and href.startswith("http") and "google.com/maps" not in href


def _proper_word(w, stop=NOT_PART_OF_NAME):
    if w in ("St.", "Mt.", "Ft."):
        return True
    return bool(w) and w[0].isupper() and w.replace("-", "").isalpha() and w.lower() not in stop


def _part_of_name(before, after):
    if re.search(r"\w-$", before) or re.match(r"-\w", after) or PAIRED_NAME_AFTER.match(after):
        return True
    words = before.split()
    if words and before[-1:].isspace():
        if _proper_word(words[-1]):
            return True
        if len(words) > 1 and words[-1] in OF_WORDS and _proper_word(words[-2], NOT_OF_NAME):
            return True
    m = re.match(r"\s+([^\s]+)", after)
    return bool(m) and _proper_word(m.group(1).rstrip(".,;:!?)"))


def _context_filter(ctx, s, e, name, href):
    """why this mention should not be linked, or None. ctx is the block's text, same length as the positions."""
    before, after = ctx[max(0, s - 90):s], ctx[e:e + 90]
    clause = re.split(r"[.!?;:—]\s", before)[-1]                 # negation only reaches inside its own clause
    if BORDER_BEFORE.search(before) or BORDER_AFTER.match(after):
        return "border-only"
    if NOT_USE_BEFORE.search(clause) or NOT_USE_AFTER.match(after) or (_service(href) and NOT_SERVICE_BEFORE.search(clause)):
        return "told-not-to-use"
    if _part_of_name(before, after):
        return "part-of-name"
    return None


# ---- does this name belong on this target? (name- and target-level; module docstring has the reasons) --------------

def _fold(s):
    s = unicodedata.normalize("NFKD", (s or "").casefold().translate(_FOLD_MAP))
    return "".join(c for c in s if not unicodedata.combining(c))


def _tokens(s):
    return re.findall(r"[a-z0-9]+", _fold(re.sub(r"['’]s\b", "", s or "")))


def _distinct(name):
    """the words of a name that a target has to echo: no articles, no type words"""
    return [t for t in _tokens(name) if t not in NAME_STOP and t not in NAME_TYPE]


def _maps_place(href):
    """the place text of a Google Maps URL, or None if it isn't one we can read"""
    if "google." not in href or "/maps" not in href:
        return None
    m = re.search(r"/maps/place/([^/@?]+)", href)
    if not m:
        m = re.search(r"[?&]query=([^&]+)", href)
    return unquote(m.group(1)).replace("+", " ") if m else None


def _target_kind(href):
    return "map" if _maps_place(href) is not None else "external" if href.startswith(("http:", "https:")) else "page"


def _target_key(href):
    """two hrefs with the same key lead to the same place"""
    place = _maps_place(href)
    if place is not None:
        return "map:" + " ".join(_tokens(place.split(",")[0]))
    if href.startswith(("http:", "https:")):
        return "ext:" + re.sub(r"^www\.", "", urlsplit(href).hostname or "")
    return "page:" + href.split("#")[0].split("?")[0].lower()


def _target_problem(name, href):
    """why this name may not carry this href, or None. No href (a map pin) is left to _editable."""
    if not href:
        return None
    toks = _distinct(name)
    if not toks:
        return "generic-name"
    all_toks = set(_tokens(name))
    place = _maps_place(href)
    if place is not None:                                 # a Maps pin: the first comma part names the place
        where = _tokens(place.split(",")[0])
        if not all(t in where for t in toks):
            return "name-not-target"
        extra = [t for t in where if t not in all_toks and t not in NAME_STOP and t not in PLACE_EXTRA_OK]
        return "different-place" if extra else None
    if "google." in href and "/maps" in href or "goo.gl" in href:
        return "name-not-target"                          # a short or unreadable map link: can't tell what it is
    if href.startswith(("http:", "https:")):
        parts = urlsplit(href)
        host = _fold(re.sub(r"^www\.", "", parts.hostname or ""))
        segs = [p for p in parts.path.split("/") if p and not _LOCALE_SEG.match(p)]
        rest = [t for t in toks if t not in host]
        if not segs:                                      # a home or locale root: fine if the name is that site
            return "name-not-target" if rest else None
        path_seq = _tokens(" ".join(unquote(p) for p in segs))
        if any(b in host for b in BOOKING_HOSTS):         # a listing: the name has to be the listing's own name, in order
            want = [t for t in _tokens(name) if t not in NAME_STOP]
            n = len(want)
            ok = bool(want) and any(path_seq[i:i + n] == want for i in range(len(path_seq) - n + 1))
            return None if ok else "deep-link"
        path_toks = set(path_seq)
        return None if rest and all(t in path_toks for t in rest) else "deep-link"
    page, _, anchor = href.partition("#")
    page = Path(page.split("?")[0])
    if anchor:
        a_all = _tokens(anchor)
        a_toks = [t for t in a_all if t not in NAME_STOP and t not in NAME_TYPE]
        return None if a_toks and (all(t in a_all for t in toks) or all(t in all_toks for t in a_toks)) else "section-mismatch"
    slug = _tokens(page.stem)
    if page.stem in ("index", "field-notes"):
        slug += _tokens(page.parent.name)
    return None if all(t in slug for t in toks) else "name-not-target"


def choose_href(name, own, sibling, nav):
    """(the href to carry for this name, a reason it is ambiguous or None). `own` is this article's own link for the name,
    `sibling` every href the sibling articles give it, `nav` the site menu's. The article's own link is always the one used."""
    if own:
        return own, None
    cands = list(sibling) + ([nav] if nav else [])
    if not cands:
        return None, None
    good = [h for h in cands if not _target_problem(name, h)]
    if not good:
        return cands[0], None                              # reported with its problem by _target_problem
    for kind in ("page", "map", "external"):               # the site's own article first, a map pin next
        of_kind = [h for h in good if _target_kind(h) == kind]
        if of_kind:
            keys = {_target_key(h) for h in of_kind}
            return of_kind[0], "conflicting-targets" if len(keys) > 1 else None
    return good[0], None


# ---- does this mention deserve a link? -------------------------------------------------------------------------------

def _initials(words):
    return "".join(w[0].upper() for w in words if w)


def _short_form_near(name, s, e, links, window=40):
    """the mention is a short form of a linked (or just-linked) name next to it"""
    want = set(_tokens(name)) - NAME_STOP
    if not want:
        return False
    is_code = name.isupper() and len(name) <= 8
    for a, b, text in links:
        gap = s - b if b <= s else a - e if a >= e else -1
        if not 0 <= gap <= window:
            continue
        got = [t for t in _tokens(text) if t not in NAME_STOP]
        if want <= set(got) or (is_code and _initials(got).startswith(name)):
            return True
    return False


def _mention_problem(name, ctx, s, e, links):
    """why this particular mention should not take a link, or None. `links` are the [(start, end, text)] already linked in
    the block, including ones this run has just accepted. Runs last, after the name is known to fit its target."""
    before, after = ctx[max(0, s - 110):s], ctx[e:e + 60]
    if re.search(r"\(\s*$", before) and re.match(r"\s*\)", after) and name.isupper() and len(name) <= 8:
        return "abbreviation"
    if _short_form_near(name, s, e, links):
        return "short-form"
    if ATTRIBUTIVE_AFTER.match(after):
        return "attributive"
    clause = re.split(r"[.!?;:—]\s", before)[-1]
    if ASIDE_BEFORE.search(clause):
        return "aside"
    if name in COUNTRIES:
        sentence = re.split(r"[.!?]\s", before)[-1] + " " + re.split(r"[.!?]\s", after)[0]
        opens = not clause.strip()                         # opens the sentence: it is the sentence's subject
        strong = COUNTRY_STRONG_BEFORE.search(clause) or COUNTRY_OPENER.search(before[-30:])
        travel = COUNTRY_TRAVEL_BEFORE.search(clause) and _PERSON_OR_MODE.search(sentence)      # "members left for Germany" is not his trip
        if not opens and not strong and not travel:
            return "passing-country"
    return None


# ---- which text is new since a round ---------------------------------------------------------------------------------

_WORD = re.compile(r"\[\s*\??\s*\]|[\w'’-]+")


def _tokens_of(text):
    return [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text)]


def _new_mask(text, old_blocks, old_exact):
    """[True for each character of `text` that isn't in the older copy]: word by word against the closest older paragraph"""
    toks = _tokens_of(text)
    words = [t[0] for t in toks]
    mask = [False] * len(text)
    if tuple(words) in old_exact:
        return mask
    best, best_ratio = None, 0.0
    for old in old_blocks:
        sm = difflib.SequenceMatcher(None, words, old, autojunk=False)
        if sm.quick_ratio() <= best_ratio:
            continue
        r = sm.ratio()
        if r > best_ratio:
            best, best_ratio = sm, r
    if best is None or best_ratio < 0.6:                     # nothing close: the whole paragraph is new
        spans = [(0, len(words))]
    else:                                                    # "delete" = words in the new text the older copy doesn't have
        spans = [(i1, i2) for op, i1, i2, _, _ in best.get_opcodes() if op in ("delete", "replace")]
    for i1, i2 in spans:
        for _, s, e in toks[i1:i2]:
            for k in range(s, e):
                mask[k] = True
    return mask


def _new_masks(blocks, old_html):
    old_toks = [tuple(t[0] for t in _tokens_of(b["text"])) for b in blocks_of(old_html)]
    old_exact, old_lists = set(old_toks), [list(t) for t in old_toks]
    return [_new_mask(b["text"], old_lists, old_exact) for b in blocks]


def _round_num(slug):
    m = re.search(r"-r(\d+)$", slug)
    return int(m.group(1)) if m else 1


def rounds_of(path):
    """[(round number, slug, folder, proposal)] of every round on this article, oldest first"""
    key = comments_key(str(path))
    out = []
    for f in REDLINE.glob("*/proposal.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(p, dict) and comments_key("%s/%s" % (p.get("article_dir") or "", p.get("article") or "")) == key:
            out.append((_round_num(f.parent.name), f.parent.name, f.parent, p))
    return sorted(out, key=lambda r: r[0])


def _accepted_pairs(folder, proposal):
    """[(find, replace)] for the round changes Kevin accepted: what we wrote, not what he typed"""
    try:
        decisions = json.loads((folder / "decisions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    pairs = []
    for c in proposal.get("changes") or []:
        v = decisions.get(c.get("id")) if isinstance(decisions, dict) else None
        if not (v.get("accepted") if isinstance(v, dict) else v):
            continue
        if c.get("kind") == "cut":
            pairs.append((c.get("sentence") or "", c.get("bridge_before") or ""))
        elif c.get("kind") == "insert":
            pairs.append((c.get("after") or "", (c.get("after") or "") + (c.get("html") or "")))
        else:
            pairs.append((c.get("find") or c.get("old") or "", c.get("replace") or c.get("new") or ""))
    return [(a, b) for a, b in pairs if a]


def round_reference(path, slug):
    """(the round's baseline with its accepted changes applied, the round's slug). Raises ValueError with the reason."""
    found = rounds_of(path)
    if not found:
        raise ValueError("no round on this article")
    if slug == "last":
        _, slug, folder, p = found[-1]
    else:
        match = [r for r in found if r[1] == slug]
        if not match:
            raise ValueError("%s is not a round of this article (its rounds: %s)" % (slug, ", ".join(r[1] for r in found)))
        _, slug, folder, p = match[0]
    baseline = folder / "baseline" / p.get("article", "")
    if not baseline.is_file():
        raise ValueError("round %s has no saved baseline" % slug)
    old = baseline.read_bytes().decode("utf-8")
    for find, rep in _accepted_pairs(folder, p):
        if find in old:
            old = old.replace(find, rep, 1)
    return old, slug


# ---- the check ------------------------------------------------------------------------------------------------------

def _path_of(rel):
    return (ROOT / rel) if not Path(rel).is_absolute() else Path(rel)


def _analyze(rel, since=None):
    """`since`: an older copy of this page's HTML; findings then only come from text that isn't in it (--since-round)"""
    path = _path_of(rel)
    html = path.read_bytes().decode("utf-8")                # bytes: an edit must keep every line ending and any BOM
    rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else rel
    own = link_names(html)
    sibling = {}
    for sib in sorted(path.parent.glob("*.html")):
        if sib != path:
            for k, v in link_candidates(sib.read_text(encoding="utf-8")).items():
                kept = [h for h in v if not re.search(r"wikipedia\.org|unesco\.org", h)]   # a concept a sibling explains, not a place
                if kept:
                    sibling.setdefault(k, [])
                    sibling[k] += [h for h in kept if h not in sibling[k]]
    nav = nav_countries(html, rel)
    names, ambiguous = {}, {}
    for k in set(own) | set(sibling) | set(nav):
        h, amb = choose_href(k, own.get(k), sibling.get(k, []), nav.get(k))
        if h:
            names[k] = h
            if amb:
                ambiguous[k] = amb
    for k in map_pins(rel):
        names.setdefault(k, None)
    subject = set()
    if _subject_names:
        try:
            subject = {s.lower() for s in _subject_names(rel, html)}
        except Exception:
            subject = set()
    folder = path.parent.name.lower()
    for k, v in nav_countries(html, rel).items():
        if v and ("/%s/" % folder) in v:
            subject.add(k.lower())
    # longest names first, so "Lake Ohrid" is matched before "Ohrid"
    order = sorted((n for n in names if n.lower() not in subject), key=len, reverse=True)
    pats = {n: re.compile(r"(?<![\w\x01])" + re.escape(n) + r"(?![\w])") for n in order}
    named_out = {n: "own-section" if _points_here(names[n], path) else "demonym" if _is_demonym(n) else None for n in order}
    target_out = {n: ambiguous.get(n) or _target_problem(n, names[n]) for n in order}
    issues, filtered = [], []
    linked_in_article = set(own)
    linked_in_section = {}
    parsed = _parse(html)
    blocks = parsed.blocks
    for b in blocks:
        for l in b["links"]:
            linked_in_section.setdefault(b["section"], set()).add(re.sub(r"\s+", " ", l["text"]).strip())
    reported_global, reported_section = set(), set()
    masks = _new_masks(blocks, since) if since is not None else None

    def is_new(i, s, e):
        return masks is None or any(masks[i][s:e])

    def own_page(href):
        return bool(href) and not href.startswith(("http", "#", "mailto:"))

    for i, b in enumerate(blocks):
        bare = re.sub("\x01[^\x02]*\x02", lambda m: " " * len(m.group(0)), b["text"])   # linked text blanked out
        plain = b["text"].replace("\x01", "").replace("\x02", "")
        ctx = re.sub("[\x01\x02]", " ", b["text"]).replace("’", "'")             # same length as `bare`
        for m in PLACEHOLDER_RE.finditer(bare):
            if not is_new(i, m.start(), m.end()):
                continue
            issues.append({"kind": "placeholder", "block": i, "text": m.group(0), "section_title": parsed.titles.get(b["section"], ""),
                           "context": plain[max(0, m.start() - 60): m.end() + 60]})
        sec_links = linked_in_section.get(b["section"], set())
        taken, accepted = [], []
        linked_spans = [(q.start(), q.end(), q.group(1)) for q in re.finditer("\x01([^\x02]*)\x02", b["text"])]
        block_links = [re.sub(r"\s+", " ", l["text"]).strip() for l in b["links"]]
        for n in order:
            if masks is not None:
                if any(n in lt for lt in block_links):      # sweep of new text: only this paragraph's own links settle it
                    continue
            elif own_page(names[n]):
                if any(n in lt for lt in sec_links) or (b["section"], n) in reported_section:
                    continue
            else:
                if any(n in lt for lt in linked_in_article) or n in reported_global:
                    continue
            m = None
            for c in pats[n].finditer(bare):
                if any(s0 <= c.start() < e0 for s0, e0 in taken) or not is_new(i, c.start(), c.end()):
                    continue
                why = named_out[n] or _context_filter(ctx, c.start(), c.end(), n, names[n]) or target_out[n] \
                    or _mention_problem(n, ctx, c.start(), c.end(), linked_spans + accepted)
                if not why:
                    m = c
                    break
                filtered.append({"text": n, "reason": why, "block": i, "href": names[n],
                                 "start": b["map"][c.start()][0],
                                 "context": bare[max(0, c.start() - 60): c.end() + 60].strip()})
            if not m:
                continue
            taken.append((m.start(), m.end()))
            accepted.append((m.start(), m.end(), n))
            (reported_section.add((b["section"], n)) if own_page(names[n]) else reported_global.add(n))
            start, end = b["map"][m.start()][0], b["map"][m.end() - 1][1]
            issues.append({"kind": "bare-name", "block": i, "section": b["section"], "section_title": parsed.titles.get(b["section"], ""),
                           "text": n, "href": names[n], "start": start if start >= 0 and end >= 0 else None, "end": end,
                           "context": bare[max(0, m.start() - 60): m.end() + 60].strip()})
    return path, html, {"article": rel, "blocks": len(blocks), "issues": issues, "filtered": filtered}


def check(rel):
    return _analyze(rel)[2]


# ---- turning findings into edits -------------------------------------------------------------------------------------

def review_state(path):
    """(kevin_reviewed, open_rounds): why this file may not be written directly, and its undecided rounds' changes"""
    key = comments_key(str(path))
    why, open_rounds = [], []
    store = REDLINE / "_comments" / (key + ".json")
    if store.is_file():
        try:
            threads = json.loads(store.read_text(encoding="utf-8")).get("threads") or []
        except Exception:
            threads, _ = [], why.append("the comment store %s could not be read" % store.name)
        n = sum((t.get("author") == "Kevin") + sum(r.get("author") == "Kevin" for r in t.get("replies") or []) for t in threads)
        if n:
            why.append("%d comment(s) by Kevin in %s" % (n, store.name))
    for f in sorted(REDLINE.glob("*/proposal.json")):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if comments_key("%s/%s" % (p.get("article_dir") or "", p.get("article") or "")) != key:
            continue
        d = f.parent
        decisions = {}
        try:
            decisions = json.loads((d / "decisions.json").read_text(encoding="utf-8")) if (d / "decisions.json").is_file() else {}
        except Exception:
            decisions = {"unreadable": True}
        if decisions or (d / "applied.json").exists() or (d / "progress.json").exists():
            why.append("round %s is decided" % d.name)
        else:
            why.append("round %s is open" % d.name)
            open_rounds.append(p)
    return why, open_rounds


def _editable(res, raw):
    """(findings that can become an edit, in file order; [(finding, why not)])"""
    ok, skipped = [], []
    for x in res["issues"]:
        if x["kind"] != "bare-name":
            continue
        if not x.get("href"):
            skipped.append((x, "no href (a map pin): needs a real link"))
        elif x.get("start") is None or x["end"] <= x["start"]:
            skipped.append((x, "could not place it in the raw file"))
        elif "<" in raw[x["start"]:x["end"]] or ">" in raw[x["start"]:x["end"]]:
            skipped.append((x, "the name crosses other markup"))
        else:
            ok.append(x)
    return sorted(ok, key=lambda x: x["start"]), skipped


def link_html(href, text):
    """text wrapped the way the site writes a link. href is already unescaped (it came through the HTML parser)."""
    ext = ' target="_blank" rel="noopener noreferrer"' if href.startswith(("http:", "https:")) else ""
    return '<a href="%s"%s>%s</a>' % (href.replace("&", "&amp;").replace('"', "&quot;"), ext, text)


def apply_links(rel):
    """write the missing links into an unreviewed draft. Returns (exit code, lines to print)."""
    path, raw, res = _analyze(rel)
    why, _ = review_state(path)
    if why:
        return 2, ["REFUSED %s: Kevin has this draft in review (%s)." % (res["article"], "; ".join(why)),
                   '  Use instead: python tools/draft_check.py "%s" --round-changes <out.json>, then put those changes in the next round.' % rel]
    ok, skipped = _editable(res, raw)
    out, at = [], 0
    for x in ok:
        out += [raw[at:x["start"]], link_html(x["href"], raw[x["start"]:x["end"]])]
        at = x["end"]
    if ok:
        path.write_bytes(("".join(out) + raw[at:]).encode("utf-8"))
    lines = ["%s: %d link(s) written, %d skipped" % (res["article"], len(ok), len(skipped))]
    lines += ["  + %s -> %s" % (x["text"], x["href"]) for x in ok]
    lines += ["  - %s: %s" % (x["text"], w) for x, w in skipped]
    return 0, lines


def _spans_of(raw, needle):
    out, i = [], raw.find(needle) if needle else -1
    while i >= 0:
        out.append((i, i + len(needle)))
        i = raw.find(needle, i + 1)
    return out


def build_find(raw, s, e, lo, hi, want_neighbors=2):
    """the shortest whole-word text around raw[s:e] that matches once in the file, or None. It never crosses a tag or a
    line break and stays inside [lo, hi), so it can't touch another change."""
    left = max(lo, raw.rfind(">", 0, s) + 1, raw.rfind("<", 0, s) + 1, raw.rfind("\n", 0, s) + 1)
    rights = [i for i in (raw.find("<", e), raw.find(">", e), raw.find("\n", e), hi) if i >= 0]
    right = min(rights) if rights else len(raw)
    for m in _CHARREF.finditer(raw, left, right):                # an entity the editor's DOM would rewrite is a wall
        if _DOM_SAFE_REF.fullmatch(m.group(0)):
            continue
        if m.end() <= s:
            left = m.end()
        elif m.start() >= e:
            right = m.start()
            break
    words = [(m.start() + left, m.end() + left) for m in re.finditer(r"\S+", raw[left:right])]
    inside = [k for k, (a, b) in enumerate(words) if b > s and a < e]
    if not inside:
        return None
    i, j, core = inside[0], inside[-1], len(inside)
    while True:
        find = raw[words[i][0]:words[j][1]]
        if raw.count(find) == 1 and (j - i + 1 - core) >= min(want_neighbors, len(words) - core):
            return words[i][0], words[j][1]
        if i > 0 and ((j - i) % 2 == 0 or j == len(words) - 1):
            i -= 1
        elif j < len(words) - 1:
            j += 1
        else:
            return None


def _short_target(href):
    if "google.com/maps" in href:
        return "its Google Maps pin"
    if href.startswith("http"):
        return re.sub(r"^https?://(www\.)?", "", href).split("/")[0]
    return re.sub(r"^(\.\./)+", "", href)


def round_changes(rel):
    """(changes in the proposal.json shape, lines to print). Writes nothing itself and never creates a round."""
    changes, lines, _ = _round_changes(rel)
    return changes, lines


def sweep_new_text(rel, slug):
    """(changes, lines, blanks): the same round changes limited to text new since round `slug` ("last" = his newest round),
    plus the blanks in that text (never [PHOTO], which is a slot for a photo, not a blank). Raises ValueError if there's no such round."""
    old, slug = round_reference(_path_of(rel), slug)
    changes, lines, res = _round_changes(rel, since=old)
    blanks = [x for x in res["issues"] if x["kind"] == "placeholder" and x["text"] != "[PHOTO]"]
    lines[0] = "%s (only text new since %s)" % (lines[0], slug)
    lines += ["  ? blank (%s): %s" % (x.get("section_title") or "?", x["context"]) for x in blanks]
    return changes, lines, blanks


def _round_changes(rel, since=None):
    path, raw, res = _analyze(rel, since=since)
    _, open_rounds = review_state(path)
    blocked = []
    for p in open_rounds:
        for c in p.get("changes") or []:
            text = c.get("find") or c.get("sentence") or ""
            blocked += [(a, b, "%s %s" % (p.get("slug"), c.get("id"))) for a, b in _spans_of(raw, text)]
    ok, skipped = _editable(res, raw)
    live = []
    for x in ok:
        hit = next((w for a, b, w in blocked if a < x["end"] and x["start"] < b), None)
        if hit:
            skipped.append((x, "already inside open round change " + hit))
        else:
            live.append(x)
    changes, lo = [], 0
    for k, x in enumerate(live):
        hi = live[k + 1]["start"] if k + 1 < len(live) else len(raw)
        low = max([lo] + [b for a, b, _ in blocked if b <= x["start"]])
        high = min([hi] + [a for a, b, _ in blocked if a >= x["end"]])
        got = build_find(raw, x["start"], x["end"], low, high)
        if not got:
            skipped.append((x, "no text-only find is unique (the text repeats elsewhere in the file, usually a hidden layout variant)"))
            continue
        a, b = got
        lo = b
        link = link_html(x["href"], raw[x["start"]:x["end"]])
        changes.append({"id": "l%02d" % (len(changes) + 1), "kind": "grammar", "section": x.get("section_title") or "",
                        "find": raw[a:b], "replace": raw[a:x["start"]] + link + raw[x["end"]:b], "count": 1,
                        "note": "Link: %s to %s (draft_check)." % (x["text"], _short_target(x["href"])), "origin": "draft_check"})
    lines = ["%s: %d round change(s), %d skipped" % (res["article"], len(changes), len(skipped))]
    lines += ["  + %s: %s" % (c["id"], c["find"]) for c in changes]
    lines += ["  - %s: %s" % (x["text"], w) for x, w in skipped]
    return changes, lines, res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("articles", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true", help="write the bare-name links into a draft Kevin hasn't reviewed")
    ap.add_argument("--round-changes", metavar="OUT.json", help="write bare-name links as round changes, for a draft he has reviewed")
    ap.add_argument("--since-round", metavar="SLUG", help="with --round-changes: only text new since this round's baseline ('last' = his newest round); blanks go to OUT.blanks.json")
    a = ap.parse_args(argv)
    out = sys.stdout.buffer
    if a.apply and a.round_changes:
        ap.error("--apply and --round-changes are alternatives: one for unreviewed drafts, one for reviewed ones")
    if a.since_round and not a.round_changes:
        ap.error("--since-round narrows --round-changes; use the two together")
    if a.round_changes:
        if len(a.articles) != 1:
            ap.error("--round-changes takes one article: a round belongs to one file")
        if a.since_round:
            try:
                changes, lines, blanks = sweep_new_text(a.articles[0], a.since_round)
            except ValueError as e:
                out.write(("draft_check: %s\n" % e).encode("utf-8"))
                return 2
            blanks_path = Path(a.round_changes).with_suffix(".blanks.json")
            blanks_path.write_text(json.dumps(blanks, ensure_ascii=False, indent=1), encoding="utf-8")
            lines.append("blanks in new text: %d -> %s" % (len(blanks), blanks_path))
        else:
            changes, lines = round_changes(a.articles[0])
        Path(a.round_changes).write_text(json.dumps(changes, ensure_ascii=False, indent=1), encoding="utf-8")
        out.write(("\n".join(lines) + "\n").encode("utf-8"))
        return 0
    if a.apply:
        worst = 0
        for r in a.articles:
            code, lines = apply_links(r)
            worst = max(worst, code)
            out.write(("\n".join(lines) + "\n").encode("utf-8"))
        return worst
    res = [check(r) for r in a.articles]
    if a.json:
        out.write(json.dumps(res, ensure_ascii=False, indent=1).encode("utf-8"))
        return 0
    for r in res:
        out.write(("%s: %d issue(s) in %d paragraphs/bullets\n" % (r["article"], len(r["issues"]), r["blocks"])).encode("utf-8"))
        for x in r["issues"]:
            tgt = (" -> " + x["href"]) if x.get("href") else (" -> (map pin, no link yet)" if x["kind"] == "bare-name" else "")
            out.write(("  [%s] %s%s\n      ...%s...\n" % (x["kind"], x["text"], tgt, x["context"])).encode("utf-8"))
        if r["filtered"]:
            out.write(("  filtered: " + ", ".join("%s (%s)" % (f["text"], f["reason"]) for f in r["filtered"]) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
