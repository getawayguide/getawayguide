"""Clean every booking link on the site, and tag it with Kevin's affiliate IDs once they exist.

Roadmap steps R1 + R2 (Kevin, 2026-11-07; plan: .tmp/previews/roadmap/traffic-money-roadmap.html).

    python tools/affiliate_links.py                     # dry run (default): what --apply would change, per page
    python tools/affiliate_links.py --apply             # write it (clean only while no IDs are in .env)
    python tools/affiliate_links.py --apply --clean-only   # clean, never tag, whatever .env says
    python tools/affiliate_links.py --check             # exit 1 if a page needs a change (the lint)
    python tools/affiliate_links.py --preview-ids       # dry run as if every ID were set (shows the tagging)
    python tools/affiliate_links.py --report FILE.md    # also write the dry-run report as Markdown
    python tools/affiliate_links.py --pages a.html b/c.html   # only these pages
    python tools/affiliate_links.py --drafts            # include Drafts/ (read the report before --apply there)

WHAT IT DOES TO EACH BOOKING LINK
  1. Clean. Strips what a copied address drags along: Google/Microsoft ad-click ids (gclid, gbraid,
     wbraid, gad_source, gad_campaignid, gclsrc, msclkid), utm_*, Google Ads ValueTrack fields
     (campaign_id, adgroup_id, creative_id, network, device ...), session and ranking ids, stale
     check-in dates and guest counts, and any other partner's ID (Booking aid=311088 + label,
     GetYourGuide partner_id=CD951: those were Google Ads' own IDs, picked up with the ad click).
     What is left is the address that opens the same listing: the Hostelworld property id + slug,
     the GetYourGuide tour slug (-t123), the Booking hotel path, the Expedia .h123. path. Rules per
     network are in clean_url(); anything not listed is kept.
  2. Tag, ONLY for a network whose ID is in .env (never a placeholder, never half a link):
       Hostelworld   HOSTELWORLD_AFFILIATE_ID = the Partnerize camref
       GetYourGuide  GYG_PARTNER_ID
       Viator        VIATOR_PID  (VIATOR_MCID optional, default 42383)
       Stay22        STAY22_AID  (wraps Booking.com, Expedia and Hotels.com hotel links)
  3. rel="sponsored noopener" on every tagged link (existing rel tokens kept). Google: "Mark links that
     are advertisements or paid placements (commonly called paid links) with the sponsored value."
  4. ONE disclosure line per page, right before the block that holds the first tagged link, in the
     site's existing tip style (div.article-tip, styled in artifact.css as an accent rule):
         DISCLOSURE (below)
     The FTC wants it clear and close to the links, not only on a separate page.
     EXCEPT on a page that carries the "Last updated: ... · Contains affiliate links" line
     (div.gg-updated, written by tools/updated_line.py): that line is the page's disclosure, so no tip is
     added there and --apply removes an old one (Kevin, 2026-11-08). Pages without the line keep the tip.

  With NO IDs in .env nothing is tagged, no rel="sponsored" and no disclosure are added: the tool only
  cleans. It is a pure function of (page, IDs), so a second run changes nothing, and removing an ID
  later un-tags that network (rel="sponsored" and the disclosure come off with the last tagged link).

LINK FORMATS (researched 2026-11-07; confirm one generated link in each dashboard after sign-up)
  Hostelworld runs its program on Partnerize (partners.hostelworld.com/faqs: "create your Affiliate
    account on Partnerize", last-click attribution). Partnerize deep link, parameters in this order:
    https://prf.hn/click/camref:<CAMREF>/pubref:<sub-id>/destination:<url>
    (Partnerize click-link format, docs.fmtc.co/kb/partnerize; order camref, pubref, adref, destination).
    The destination is percent-encoded so its own ?/& cannot end the path segment.
  GetYourGuide: partner_id=<ID> on any getyourguide.com address is how the Partner Portal's Link Builder
    and widgets attribute (GetYourGuide WordPress plugin README; affilimate.com/blog/getyourguide-
    affiliate-search-page). The Link Builder also writes utm_medium=online_publisher, and cmp=<campaign>
    when a campaign is typed in, so this tool writes the same three. et=<tour id> highlights a tour on a
    search page (same Affilimate guide) and is kept.
  Viator: ?pid=<P00...>&mcid=42383&medium=link&campaign=<label> (partnerresources.viator.com: "PID: your
    nine digit account ID", "MCID is an internal tracking ID", "Media can be link, widget, banner, and API",
    example https://www.viator.com?pid=P00049694&mcid=42383&medium=link&campaign=example; "The PID and MCID
    tie bookings to your account and if they are modified or removed, we will not be able to pay you out").
  Stay22 Allez deep links: https://www.stay22.com/allez/<booking|expedia|hotelscom>?aid=<AID>&campaign=<c>
    &link=<encodeURIComponent(url)> (dev.stay22.com/docs/allez/parameters: link = "URL-encoded direct OTA
    link ... Must be URL-encoded via encodeURIComponent()"; campaign: "please use _ instead of -";
    dev.stay22.com/docs/allez/providers lists the /allez/booking, /allez/expedia, /allez/hotelscom endpoints).
    Booking.com moved small affiliates to networks in 2025, so Stay22 is the one account for hotels.
  Not tagged (no program on the roadmap, or none at all): Airbnb (program closed), Busbud, Bookaway,
    Ferryhopper, GoTrip, GuruWalk. They are still cleaned.

DISCLOSURE TEXT, kept in Kevin's voice (American English, no em dash):
    see DISCLOSURE below. The privacy page's "Affiliate links" section says the longer version.

Live pages = the repo root and the country folders; never archive/ (the frozen pre-redesign copy, robots
disallowed), .tmp/, tools/, _content/, workflows/, editor.html or a redirect stub. Drafts only with --drafts.
Only href values and, on a tagged link, its rel attribute and the one disclosure line are written: the
rest of the file (encoding, BOM, line endings) is left as found.

Wiring (OFF until IDs exist): tools/publish_country.py runs `--apply --pages <new page>` only when .env has
AFFILIATE_LINKS=on; the Launch tab (tools/prelaunch.py, check "affiliate") reports dirty or untagged links
and offers the same fix. Nothing turns AFFILIATE_LINKS on by itself.
"""
import argparse
import html as H
import os
import re
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
TODAY = date.today().isoformat()

DISCLOSURE = ("Some booking links on this page are affiliate links: if you book through one, "
              "I earn a small commission at no extra cost to you.")
NOTE_CLASS = "article-tip affiliate-note"
NOTE_RE = re.compile(r'[ \t]*<div class="article-tip affiliate-note">.*?</div>[ \t]*\r?\n?', re.S)
# the article's own "Last updated: ... · Contains affiliate links" line (tools/updated_line.py). A page that
# carries it is already disclosed, so the tip box is neither added nor kept (Kevin, 2026-11-08).
LINE_RE = re.compile(r'<div class="[^"]*\bgg-updated\b[^"]*"[^>]*>[^<]*affiliate link', re.I)


def has_line(text):
    return bool(LINE_RE.search(text))

ENV_KEYS = {"hostelworld": "HOSTELWORLD_AFFILIATE_ID", "getyourguide": "GYG_PARTNER_ID",
            "viator": "VIATOR_PID", "stay22": "STAY22_AID"}
# which program tags which network (Stay22 wraps the hotel sites)
PROGRAM = {"hostelworld": "hostelworld", "getyourguide": "getyourguide", "viator": "viator",
           "booking": "stay22", "expedia": "stay22", "hotelscom": "stay22"}
STAY22_ENDPOINT = {"booking": "booking", "expedia": "expedia", "hotelscom": "hotelscom"}

HOSTS = [  # (network, host suffix)
    ("hostelworld", "hostelworld.com"), ("getyourguide", "getyourguide.com"), ("viator", "viator.com"),
    ("booking", "booking.com"), ("expedia", "expedia.com"), ("hotelscom", "hotels.com"),
    ("airbnb", "airbnb.com"), ("busbud", "busbud.com"), ("bookaway", "bookaway.com"),
    ("ferryhopper", "ferryhopper.com"), ("gotrip", "gotrip.ge"), ("guruwalk", "guruwalk.com"),
    ("12go", "12go.asia"), ("omio", "omio.com"), ("agoda", "agoda.com"), ("klook", "klook.com"),
    ("tripcom", "trip.com"), ("rentalcars", "rentalcars.com"), ("airalo", "airalo.com"),
    ("safetywing", "safetywing.com"),
]
WRAPPERS = ("prf.hn", "stay22.com")

# ---- what counts as junk -------------------------------------------------------------------------
# ad-click ids and analytics tags: dropped from ANY external link (except Google's own, whose Maps
# share parameters are not ours to judge)
AD_IDS = {"gclid", "gbraid", "wbraid", "gclsrc", "dclid", "msclkid", "fbclid", "yclid", "twclid", "ttclid",
          "srsltid", "_gl", "mc_cid", "mc_eid", "igshid", "gad_source", "gad_campaignid"}
# Google Ads ValueTrack / ad-campaign fields: dropped when the address came from an ad click
AD_FIELDS = {"campaign_id", "adgroup_id", "ad_id", "creative_id", "criteria_id", "target_id", "feed_item_id",
             "keyword", "match_type", "matchtype", "network", "device", "placement", "position", "adposition",
             "ad_position", "location_physical_id", "location_interest_id", "loc_physical_ms", "assetgroup_id",
             "synthetic_keyword", "campaigntype", "term_match_type", "geo", "p", "sub_keyword", "sub_ad",
             "sub_publisher", "uniqueclickid", "google_ads_click_source", "source", "surface", "funnel"}
DATE_KEYS = {"chkin", "chkout", "checkin", "checkout", "check_in", "check_out", "checkin_date", "checkout_date",
             "from", "to", "datefrom", "dateto", "date_from", "date_to", "beginsat", "endsat", "arrival",
             "departure", "startdate", "enddate", "date"}
GUEST_KEYS = {"guests", "adults", "children", "rooms", "rm1", "group_adults", "group_children", "no_rooms",
              "number_of_guests"}
DATE_VAL = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def network_of(host):
    host = (host or "").lower()
    for net, suf in HOSTS:
        if host == suf or host.endswith("." + suf):
            return net
    if re.match(r"^(www\.)?expedia\.[a-z.]+$", host):
        return "expedia"
    return None


def _ad_click(params):
    keys = {k.lower() for k, _ in params}
    return bool(keys & {"gclid", "gbraid", "wbraid", "gad_source", "msclkid", "gclsrc"}) or any(
        k.lower() == "utm_medium" and v.lower() in ("cpc", "ppc", "paid", "paidsearch") for k, v in params) or any(
        k.lower() == "source" and v.lower().startswith("ppc_") for k, v in params) or any(
        k.lower().startswith("g_") for k, _ in params)


def _stale(v):
    m = DATE_VAL.match(v or "")
    return bool(m) and m.group(1) < TODAY


def _generic(params):
    """drop ad ids, utm_*, and (from an ad click) the campaign fields; drop past dates + their guest counts"""
    ad = _ad_click(params)
    out = []
    for k, v in params:
        kl = k.lower()
        if kl in AD_IDS or kl.startswith("utm_") or kl.startswith("g_"):
            continue
        if ad and kl in AD_FIELDS:
            continue
        out.append((k, v))
    if any(k.lower() in DATE_KEYS and _stale(v) for k, v in out):
        out = [(k, v) for k, v in out if k.lower() not in DATE_KEYS and k.lower() not in GUEST_KEYS]
    return out


def _keep(params, names):
    names = {n.lower() for n in names}
    return [(k, v) for k, v in params if k.lower() in names]


def unwrap(url):
    """a link this tool tagged earlier -> the plain address inside it (so a rerun is a no-op)"""
    sp = urlsplit(url)
    host = (sp.hostname or "").lower()
    if host == "prf.hn" or host.endswith(".prf.hn"):
        m = re.search(r"/destination:(.+)$", sp.path + ("?" + sp.query if sp.query else ""))
        if m:
            return unquote(m.group(1))
    if host.endswith("stay22.com") and sp.path.startswith("/allez"):
        link = dict(parse_qsl(sp.query)).get("link")
        if link:
            return link
    return url


def clean_url(url):
    """the canonical address that opens the same listing, minus everything an ad click or a session added"""
    url = unwrap(url)
    sp = urlsplit(url)
    net = network_of(sp.hostname)
    if not net:
        host = (sp.hostname or "").lower()
        if not sp.query or re.search(r"(^|\.)google\.[a-z.]+$", host) or "youtube" in host or "youtu.be" in host:
            return url
        return urlunsplit(sp._replace(query=urlencode(_generic(parse_qsl(sp.query, keep_blank_values=True)), safe=":/")))
    params = parse_qsl(sp.query, keep_blank_values=True)
    path, frag = sp.path, sp.fragment
    if net == "hostelworld":
        # /es/albergues/p/44341/slug/ (a Spanish ad click) -> the English page; it opens the same property
        m = re.match(r"^/[a-z]{2}(?:-[a-z]{2})?/[^/]+/p/(\d+)/([^/]+)/?$", path)
        if m:
            path = "/hostels/p/%s/%s/" % m.groups()
        params = []                                     # property id + slug are all it needs
    elif net == "getyourguide":
        if re.search(r"-[tl]\d+/?$", path):
            path = re.sub(r"^/en-[a-z]{2}(?=/)", "", path)   # en-gb prices in pounds; the plain path is US English
            params = []
        elif re.match(r"^/s/?$", path):
            params = _keep(params, ["q", "et", "lc"])
            path = "/s/"
        else:
            params = _keep(_generic(params), ["q", "date_from", "date_to"])
    elif net == "viator":
        params = _keep(params, ["text"]) if "search" in path.lower() else []
    elif net == "booking":
        if path.startswith("/hotel/"):
            params = []
        else:
            params = _keep(params, ["ss", "dest_id", "dest_type", "city", "region", "highlighted_hotels", "nflt"])
    elif net in ("expedia", "hotelscom"):
        if re.search(r"\.h\d+\.Hotel-Information$|^/ho\d+/", path):
            params = []
        else:
            params = _keep(params, ["destination", "regionId"])
    elif net == "airbnb":
        params = [] if path.startswith("/rooms/") else _generic(params)
    else:
        params = _generic(params)
        if net == "ferryhopper" and path in ("", "/"):
            params = []
    if path == sp.path and params == parse_qsl(sp.query, keep_blank_values=True):
        return url                                      # nothing dropped: keep the address byte for byte
    query = urlencode(params, safe=":/", quote_via=quote)
    if net == "getyourguide":
        query = query.replace("%20", "+")
    return urlunsplit((sp.scheme or "https", sp.netloc, path, query, frag))


# ---- tagging -------------------------------------------------------------------------------------
PLACEHOLDER = re.compile(r"^(|x+|0+|your.*|placeholder.*|changeme|todo|tbd|none|<.*>)$", re.I)


def load_ids(env_path=None):
    """{program: id} for every program with a real ID; a placeholder counts as missing"""
    vals = {}
    p = Path(env_path) if env_path else ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    for k in list(ENV_KEYS.values()) + ["VIATOR_MCID", "AFFILIATE_LINKS"]:
        if os.environ.get(k):
            vals[k] = os.environ[k].strip()
    ids = {prog: vals[key] for prog, key in ENV_KEYS.items() if not PLACEHOLDER.match(vals.get(key, ""))}
    if "viator" in ids:
        ids["viator_mcid"] = vals.get("VIATOR_MCID") or "42383"
    return ids


def enabled(env_path=None):
    """the publish-tool switch: AFFILIATE_LINKS=on in .env (or the environment)"""
    v = os.environ.get("AFFILIATE_LINKS", "")
    p = Path(env_path) if env_path else ROOT / ".env"
    if not v and p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.split("=", 1)[0].strip() == "AFFILIATE_LINKS" and "=" in line:
                v = line.split("=", 1)[1].split(" #")[0].strip().strip('"').strip("'")
    return v.lower() in ("on", "1", "true", "yes")


def campaign_of(page_rel):
    """armenia/yerevan.html -> armenia_yerevan (Stay22: underscores, never hyphens)"""
    stem = page_rel.replace("\\", "/")
    for pre in ("Drafts/Upcoming/", "Drafts/Full Articles/", "Drafts/Field Notes/"):
        if stem.startswith(pre):
            stem = stem[len(pre):]
    stem = re.sub(r"\.html$", "", stem)
    return re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_") or "site"


def tag_url(clean, ids, page_rel):
    """(url, tagged?) for an already-clean address"""
    sp = urlsplit(clean)
    net = network_of(sp.hostname)
    prog = PROGRAM.get(net)
    if not prog or prog not in ids:
        return clean, False
    camp = campaign_of(page_rel)
    if not sp.path:                                     # https://getyourguide.com -> .../?partner_id=, not .com?partner_id=
        sp = sp._replace(path="/")
    if prog == "hostelworld":
        return "https://prf.hn/click/camref:%s/pubref:%s/destination:%s" % (
            ids["hostelworld"], camp, quote(clean, safe="")), True
    if prog == "getyourguide":
        q = parse_qsl(sp.query, keep_blank_values=True) + [
            ("partner_id", ids["getyourguide"]), ("utm_medium", "online_publisher"), ("cmp", camp)]
        return urlunsplit(sp._replace(query=urlencode(q, quote_via=quote).replace("%20", "+"))), True
    if prog == "viator":
        q = parse_qsl(sp.query, keep_blank_values=True) + [
            ("pid", ids["viator"]), ("mcid", ids["viator_mcid"]), ("medium", "link"), ("campaign", camp)]
        return urlunsplit(sp._replace(query=urlencode(q, quote_via=quote))), True
    if prog == "stay22":
        return "https://www.stay22.com/allez/%s?aid=%s&campaign=%s&link=%s" % (
            STAY22_ENDPOINT[net], quote(ids["stay22"], safe=""), camp, quote(clean, safe="!~*'()")), True
    return clean, False


def is_tagged(url):
    sp = urlsplit(url)
    host = (sp.hostname or "").lower()
    if host == "prf.hn" or host.endswith(".prf.hn") or (host.endswith("stay22.com") and sp.path.startswith("/allez")):
        return True
    keys = {k.lower() for k, _ in parse_qsl(sp.query)}
    net = network_of(host)
    return (net == "getyourguide" and "partner_id" in keys) or (net == "viator" and "pid" in keys)


def is_booking(url):
    sp = urlsplit(H.unescape(url))
    host = (sp.hostname or "").lower()
    return bool(network_of(host)) or host == "prf.hn" or host.endswith(".prf.hn") or (
        host.endswith("stay22.com") and sp.path.startswith("/allez"))


def tracked(url):
    """any other outbound link that still carries an ad-click id or utm_* (a tour operator found through an
    ad: Civitatis, Roma Pass, meteora.com). Those are cleaned too, never tagged. Google's own links are not."""
    sp = urlsplit(H.unescape(url))
    host = (sp.hostname or "").lower()
    if not sp.query or re.search(r"(^|\.)google\.[a-z.]+$", host):
        return False
    keys = {k.lower() for k, _ in parse_qsl(sp.query, keep_blank_values=True)}
    return bool(keys & AD_IDS) or any(k.startswith("utm_") for k in keys)


def junk_in(url):
    """the parameters clean_url() would drop from a booking link, or from any outbound link carrying ad-click
    or utm tags ([] = already clean); lint_site uses this"""
    if not (is_booking(url) or tracked(url)):
        return []
    u = H.unescape(url)
    if is_tagged(u):
        return []
    before = parse_qsl(urlsplit(u).query, keep_blank_values=True)
    after = {k for k, _ in parse_qsl(urlsplit(clean_url(u)).query, keep_blank_values=True)}
    gone = [k for k, _ in before if k not in after]
    if not gone and clean_url(u) != u:
        gone = ["(path)"]
    return gone


# ---- pages ---------------------------------------------------------------------------------------
SKIP_PREFIX = ("Drafts/", "archive/", ".tmp/", "tools/", "_content/", "workflows/", ".git/", "preview/")


def live_pages():
    out = []
    for p in sorted(ROOT.glob("*.html")) + sorted(ROOT.glob("*/*.html")):
        r = p.relative_to(ROOT).as_posix()
        if r == "editor.html" or r.startswith(SKIP_PREFIX):
            continue
        out.append(r)
    return out


def draft_pages():
    sys.path.insert(0, str(ROOT / "tools"))
    import draft_paths as dp
    return sorted(p.relative_to(ROOT).as_posix() for p in dp.draft_pages())


def read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


A_TAG = re.compile(r"<a\b[^>]*>", re.I)
HREF = re.compile(r'(\bhref\s*=\s*)(["\'])(.*?)\2', re.I | re.S)
REL = re.compile(r'(\brel\s*=\s*)(["\'])(.*?)\2', re.I | re.S)


def _esc(u):
    return u.replace("&", "&amp;").replace('"', "&quot;")


def _set_rel(tag, add=(), remove=()):
    m = REL.search(tag)
    toks = m.group(3).split() if m else []
    new = [t for t in toks if t.lower() not in {r.lower() for r in remove}]
    for a in add:
        if a.lower() not in {t.lower() for t in new}:
            new.append(a)
    if new == toks:
        return tag
    if m:
        if not new:
            return tag[:m.start()].rstrip() + tag[m.end():]
        return tag[:m.start()] + '%s"%s"' % (m.group(1), " ".join(new)) + tag[m.end():]
    end = -2 if tag.endswith("/>") else -1
    return tag[:end] + ' rel="%s"' % " ".join(new) + tag[end:]


# ---- where the disclosure goes -------------------------------------------------------------------
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCKS_CLOSE_P = {"p", "div", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "figure", "blockquote",
                  "section", "article", "dl", "pre", "header", "footer", "nav", "aside", "details"}
CONTAINERS = {"ul", "ol", "table", "dl", "figure", "blockquote", "details"}
SKIP_ZONES = {"nav", "header", "footer", "script", "style", "template", "noscript", "button", "select"}


class _Tree(HTMLParser):
    """records, for each <a>, its start offset and the open elements around it (with start offsets)"""

    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.lines = [0]
        for m in re.finditer(r"\n", text):
            self.lines.append(m.end())
        self.stack, self.anchors, self.ends = [], [], {}

    def _off(self):
        ln, col = self.getpos()
        return self.lines[ln - 1] + col

    def handle_starttag(self, tag, attrs):
        off = self._off()
        if tag == "p" or tag in BLOCKS_CLOSE_P:
            while self.stack and self.stack[-1][0] == "p":
                self._pop()
        if tag == "li":
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] in ("ul", "ol"):
                    break
                if self.stack[i][0] == "li":
                    while len(self.stack) > i:
                        self._pop()
                    break
        if tag == "a":
            self.anchors.append((off, [(t, o, c) for t, o, c in self.stack]))
        if tag not in VOID:
            cls = dict(attrs).get("class") or ""
            self.stack.append((tag, off, cls))

    def handle_startendtag(self, tag, attrs):
        if tag == "a":
            self.anchors.append((self._off(), list(self.stack)))

    def _pop(self):
        t, o, _ = self.stack.pop()
        self.ends[o] = self._off()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                end = self._off()
                end = self.text.find(">", end) + 1 or end
                while len(self.stack) > i:
                    t, o, _ = self.stack.pop()
                    self.ends[o] = end
                return


def note_spot(text, anchor_offsets):
    """the offset to insert the disclosure at: before the top block holding the first tagged link that
    sits in the article (not the nav, header or footer). None if there is no such link."""
    tree = _Tree(text)
    try:
        tree.feed(text)
        tree.close()
    except Exception:
        return None
    want = set(anchor_offsets)
    for off, stack in tree.anchors:
        if off not in want or any(t in SKIP_ZONES for t, _, _ in stack):
            continue
        # outermost list/table/figure: the disclosure cannot sit inside a <ul>, and it should come before the
        # reader reaches the link
        for t, o, _ in stack:
            if t in CONTAINERS:
                return o
        for t, o, cls in reversed(stack):
            if t in ("p", "div"):                         # a paragraph, or the box of cards the link sits in
                return o
            if re.match(r"h[1-6]$", t):
                return tree.ends.get(o)
        return off
    return None


# ---- one page ------------------------------------------------------------------------------------
def process(text, page_rel, ids):
    """(new_text, changes) — changes: list of dicts {kind, before, after, network}"""
    changes = []
    tagged_offsets = []
    text0 = NOTE_RE.sub("", text) if NOTE_RE.search(text) else text
    out, last, delta = [], 0, 0
    for m in A_TAG.finditer(text0):
        tag = m.group(0)
        hm = HREF.search(tag)
        if not hm:
            continue
        raw = H.unescape(hm.group(3).strip())
        if not raw.lower().startswith("http") or not (is_booking(raw) or tracked(raw)):
            continue
        clean = clean_url(raw)
        new_url, tagged = tag_url(clean, ids, page_rel)
        net = network_of(urlsplit(clean).hostname) or "other"
        new_tag = tag
        if new_url != raw:
            new_tag = new_tag[:hm.start()] + '%s"%s"' % (hm.group(1), _esc(new_url)) + new_tag[hm.end():]
            changes.append({"kind": "tag" if tagged else "clean", "network": net, "before": raw, "after": new_url})
        if tagged:
            new_tag = _set_rel(new_tag, add=("sponsored", "noopener"))
        else:
            new_tag = _set_rel(new_tag, remove=("sponsored",))
        out.append(text0[last:m.start()])
        if tagged:
            tagged_offsets.append(m.start() + delta)
        out.append(new_tag)
        delta += len(new_tag) - len(tag)
        last = m.end()
    out.append(text0[last:])
    new = "".join(out)
    note = None
    if tagged_offsets and not has_line(new):
        spot = note_spot(new, tagged_offsets)
        if spot is not None:
            nl = "\r\n" if "\r\n" in new else "\n"
            div = '<div class="%s">%s</div>' % (NOTE_CLASS, H.escape(DISCLOSURE, quote=False))
            ls = new.rfind("\n", 0, spot) + 1
            ind = re.match(r"[ \t]*", new[ls:]).group(0)
            if new[ls:spot].strip():                    # mid-line (after a heading): on a line of its own
                line = nl + ind + div
            else:                                       # at the start of a block: before it, same indent
                line = div + nl + ind
            new = new[:spot] + line + new[spot:]
            note = {"line": new.count("\n", 0, spot) + 1, "context": _context(new, spot + len(line))}
    had_note = bool(NOTE_RE.search(text))
    if note and not had_note:
        changes.append({"kind": "disclosure", "network": "-", "before": "", "after": "line %d, before: %s" % (note["line"], note["context"])})
    if had_note and not note:
        changes.append({"kind": "disclosure-removed", "network": "-", "before": "", "after": ""})
    return new, changes


def _context(text, off):
    s = re.sub(r"<[^>]+>", " ", text[off:off + 600])
    s = re.sub(r"\s+", " ", H.unescape(s)).strip()
    return (s[:90] + "...") if len(s) > 90 else s


# ---- CLI -----------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    ap.add_argument("--check", action="store_true", help="exit 1 if any page would change")
    ap.add_argument("--preview-ids", action="store_true", help="dry run as if every program's ID were set")
    ap.add_argument("--clean-only", action="store_true", help="ignore the IDs: clean, never tag (also removes tags)")
    ap.add_argument("--pages", nargs="+", help="only these pages (paths relative to the repo)")
    ap.add_argument("--drafts", action="store_true", help="include Drafts/")
    ap.add_argument("--report", help="write the dry-run report (Markdown) here")
    ap.add_argument("--env", help="read IDs from this .env instead of the repo's (tests)")
    ap.add_argument("--root", help="treat this folder as the site root (tests)")
    a = ap.parse_args(argv)
    global ROOT
    if a.root:
        ROOT = Path(a.root).resolve()
    if a.apply and a.preview_ids:
        sys.exit("--preview-ids is for the report only; it never writes")
    ids = {} if a.clean_only else load_ids(a.env or (ROOT / ".env"))
    if a.preview_ids:
        ids = {"hostelworld": "CAMREF", "getyourguide": "GYG_ID", "viator": "P00000000",
               "viator_mcid": "42383", "stay22": "STAY22_AID"}
    pages = a.pages or (live_pages() + (draft_pages() if a.drafts else []))
    pages = [p.replace("\\", "/") for p in pages]

    real = [k for k in ENV_KEYS if k in ids]
    mode = "apply" if a.apply else "dry run"
    head = ("affiliate_links.py (%s): IDs %s" % (mode, ", ".join(real) if real else
            "none in .env, so links are cleaned only (no tags, no rel=sponsored, no disclosure)"))
    if a.preview_ids:
        head = "affiliate_links.py (dry run, PREVIEW with placeholder IDs for every program; nothing written)"
    per_page, totals = {}, {}
    for r in pages:
        p = ROOT / r
        if not p.is_file():
            print("skip (missing): %s" % r)
            continue
        text = read(p)
        if 'http-equiv="refresh"' in text[:3000].lower():
            continue
        new, ch = process(text, r, ids)
        if ch:
            per_page[r] = ch
            for c in ch:
                totals[c["kind"]] = totals.get(c["kind"], 0) + 1
            if a.apply and new != text:
                write(p, new)
    out = sys.stdout
    try:
        out.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    summary = "%d page(s) change: %s" % (len(per_page), ", ".join("%d %s" % (n, k) for k, n in sorted(totals.items())) or "nothing")
    print(head)
    print(summary + ("" if a.apply else " (dry run: nothing written)"))
    for r, ch in per_page.items():
        print("\n%s" % r)
        for c in ch:
            if c["kind"] in ("clean", "tag"):
                print("  %-5s %-12s %s\n        -> %s" % (c["kind"], c["network"], c["before"][:160], c["after"][:200]))
            else:
                print("  %s %s" % (c["kind"], c["after"]))
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(report_md(head, summary, per_page, pages, ids, a), encoding="utf-8")
        print("\nreport: %s" % a.report)
    if a.check and per_page:
        sys.exit(1)
    return per_page


def report_md(head, summary, per_page, pages, ids, a):
    from collections import Counter
    L = ["# Affiliate links: dry run %s" % TODAY, "", head + ".", "", summary + ".", ""]
    nets = Counter()
    for ch in per_page.values():
        for c in ch:
            if c["kind"] in ("clean", "tag"):
                nets[c["network"]] += 1
    if nets:
        L += ["| network | links that change |", "|---|---|"] + ["| %s | %d |" % kv for kv in nets.most_common()] + [""]
    L += ["Disclosure line (one per page, before the first tagged link, class `%s`):" % NOTE_CLASS, "",
          "> " + DISCLOSURE, ""]
    for r, ch in per_page.items():
        L += ["## %s" % r, ""]
        for c in ch:
            if c["kind"] in ("clean", "tag"):
                L += ["- **%s** (%s)  " % (c["kind"], c["network"]), "  before: `%s`  " % c["before"], "  after: `%s`" % c["after"]]
            else:
                L += ["- **%s** %s" % (c["kind"], c["after"])]
        L.append("")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
