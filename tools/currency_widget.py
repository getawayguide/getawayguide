#!/usr/bin/env python3
"""The currency converter on every itinerary (Kevin, 2026-11-08: "let's do grey outline for the box and add to
the pages"). Approved design: .tmp/previews/currency-converter-options.html (artifact DU6UAAp4nhiTNmmLNctP3X, v7).

Each itinerary's Currency section gets, as its last part (after the section's last paragraph, right before the next
section's heading; Kevin, 2026-11-08: "make the currency converter the last part of the currency section"), one placeholder

    <div class="cconv" data-local="MKD" data-local-name="Denar" data-flag="mk" data-asof="2026-11-08"
         data-rates="USD:1 EUR:0.895793 ... MKD:54.928103"><div class="cconv-fb">1 USD ≈ 54.93 MKD (mid-market,
         Nov 8, 2026).</div></div>

that currency.js (site root, loaded with defer, ?v= content stamp) renders into the two outlined boxes: a menu of
USD / EUR / GBP / AUD / CAD / CHF on the left (the local currency is left out of it; the first one left is the
default, so a euro country opens on 1 USD with EUR left out of the menu, and a US-dollar country such as El Salvador
opens on 1 EUR = x USD with USD left out; Kevin, 2026-11-08: "for usd countries - lets add currency converter default
to Euro to USD"), the local currency on the right, and the "Mid-market rates, <Mon D, YYYY>." note. With JS off the
fallback line shows.
Styles: the "currency converter" block in artifact.css. A page without a Currency section gets it right after
its Key Facts box in its own .measure, with no heading.

Rates are per US dollar from the free feed https://open.er-api.com/v6/latest/USD, kept in
tools/currency_rates.json so --apply works offline. Bash on this machine has no internet, so --refresh-rates
tries Python first and then PowerShell's Invoke-WebRequest (generic browser User-Agent; never an email address).
With no network it keeps the rates it has and says so: a launch never fails on it.

    python tools/currency_widget.py                       # dry run: list what would change
    python tools/currency_widget.py --apply               # insert / refresh the widget + the script tag
    python tools/currency_widget.py --check               # exit 1 if any itinerary is missing it or stale
    python tools/currency_widget.py --refresh-rates --apply --live-only    # what the launch tools run

--live-only skips the drafts (a launch refreshes the live pages; drafts are committed in the Drafts repo by hand).
--root <dir> treats another folder as the site root (tools/test_currency_widget.py).
Run by tools/publish_country.py and tools/retire_field_notes.py.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RATES_NAME = "currency_rates.json"
FEED = "https://open.er-api.com/v6/latest/USD"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# the menu, in Kevin's order; the local currency drops out of it and the first one left is the default
MENU = ["USD", "EUR", "GBP", "AUD", "CAD", "CHF"]
# country slug -> (currency code, the name shown after the code, flag ISO in Images/web/flags/). The flag is the
# CURRENCY's flag, as in the menu: a euro country shows the EU flag, El Salvador (US dollar) the US flag.
COUNTRIES = {
    "albania":         ("ALL", "Lek", "al"),
    "argentina":       ("ARS", "Peso", "ar"),
    "armenia":         ("AMD", "Dram", "am"),
    "australia":       ("AUD", "Australian dollar", "au"),
    "belgium":         ("EUR", "Euro", "eu"),
    "bosnia":          ("BAM", "Convertible mark", "ba"),
    "brazil":          ("BRL", "Real", "br"),
    "chile":           ("CLP", "Peso", "cl"),
    "colombia":        ("COP", "Peso", "co"),
    "croatia":         ("EUR", "Euro", "eu"),       # euro since January 2023
    "egypt":           ("EGP", "Egyptian pound", "eg"),
    "el-salvador":     ("USD", "US dollar", "us"),  # the US dollar since 2001: opens on 1 EUR = x USD
    "georgia":         ("GEL", "Lari", "ge"),
    "greece":          ("EUR", "Euro", "eu"),
    "guatemala":       ("GTQ", "Quetzal", "gt"),
    "india":           ("INR", "Rupee", "in"),
    "indonesia":       ("IDR", "Rupiah", "id"),
    "kosovo":          ("EUR", "Euro", "eu"),
    "mexico":          ("MXN", "Peso", "mx"),
    "montenegro":      ("EUR", "Euro", "eu"),       # the euro, though not in the EU
    "new-zealand":     ("NZD", "NZ dollar", "nz"),
    "north-macedonia": ("MKD", "Denar", "mk"),
    "peru":            ("PEN", "Sol", "pe"),
    "philippines":     ("PHP", "Peso", "ph"),
    "serbia":          ("RSD", "Dinar", "rs"),
    "slovenia":        ("EUR", "Euro", "eu"),       # euro since 2007
    "sweden":          ("SEK", "Krona", "se"),
    "switzerland":     ("CHF", "Swiss franc", "ch"),
    "tanzania":        ("TZS", "Shilling", "tz"),
    "turkiye":         ("TRY", "Lira", "tr"),
}
NODEC = {"JPY", "KRW", "HUF", "IDR", "CLP", "COP", "TZS", "VND", "ISK"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SKIP_TOP = {"Drafts", "archive", ".tmp", ".git", "tools", "node_modules"}
DRAFT_FOLDERS = ("Upcoming", "Full Articles")

BLOCK_RE = re.compile(r'<div\b[^>]*\bclass="cconv"[^>]*>\s*(?:<div class="cconv-fb">[^<]*</div>)?\s*</div>')
SCRIPT_RE = re.compile(r'[ \t]*<script\b[^>]*\bsrc="(?:\.\./)*currency\.js(?:\?v=[0-9a-f]*)?"[^>]*>\s*</script>\n?')
DIV_TOKEN = re.compile(r"<div\b|</div\s*>", re.I)


def out(s=""):
    try:
        sys.stdout.buffer.write((s + "\n").encode("utf-8"))     # redirected stdout here is cp1252: write the bytes
        sys.stdout.buffer.flush()
    except OSError:                                            # a reader that hung up early (| head)
        pass


# ---------------------------------------------------------------- rates
def rates_path(root):
    return Path(root) / "tools" / RATES_NAME


def load_rates(root):
    p = rates_path(root)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _fetch_python():
    req = urllib.request.Request(FEED, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8")


def _fetch_powershell():
    cmd = ("$ProgressPreference='SilentlyContinue'; "
           "$r = Invoke-WebRequest -Uri '%s' -UserAgent '%s' -UseBasicParsing -TimeoutSec 20; "
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $r.Content") % (FEED, UA)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace").strip()[:200] or "powershell failed")
    return r.stdout.decode("utf-8", "replace")


def parse_feed(text):
    d = json.loads(text)
    if d.get("result") != "success" or d.get("base_code") != "USD":
        raise ValueError("feed did not return USD rates")
    rates = {k: float(v) for k, v in d["rates"].items()}
    need = set(MENU) | {c for c, _, _ in COUNTRIES.values()}
    missing = sorted(c for c in need if not rates.get(c))
    if missing:
        raise ValueError("feed is missing " + ", ".join(missing))
    when = d.get("time_last_update_utc")
    asof = (parsedate_to_datetime(when) if when else datetime.now(timezone.utc)).date().isoformat()
    return {"asof": asof, "source": FEED, "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rates": rates}


def refresh_rates(root, fetchers=None):
    """fetch the feed and save it; on any failure keep the file as it is. Returns (ok, message)."""
    errors = []
    for name, fn in (fetchers or [("python", _fetch_python), ("powershell", _fetch_powershell)]):
        try:
            data = parse_feed(fn())
        except Exception as e:                      # no network, TLS, a bad payload: try the next way
            errors.append("%s: %s" % (name, str(e).splitlines()[0][:120] if str(e) else type(e).__name__))
            continue
        p = rates_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        return True, "rates refreshed via %s, as of %s" % (name, data["asof"])
    old = load_rates(root)
    return False, "could not fetch rates (%s); kept %s" % (
        "; ".join(errors), ("the rates as of " + old["asof"]) if old else "nothing (no rates file yet)")


# ---------------------------------------------------------------- the widget
def fmt_amount(v, code):
    # half-up, as the page's Math.round does (Python's round() would make 360.5 -> 360)
    if v >= 100 or code in NODEC:
        return "{:,}".format(int(Decimal(repr(v)).quantize(Decimal("1"), ROUND_HALF_UP)))
    return "{:,}".format(Decimal(repr(v)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def pretty_date(iso):
    y, m, d = iso.split("-")
    return "%s %d, %s" % (MONTHS[int(m) - 1], int(d), y)


def menu_for(local):
    return [c for c in MENU if c != local]


def widget(slug, data):
    """the placeholder block for a country, or None if its currency or a rate is unknown"""
    if slug not in COUNTRIES or not data:
        return None
    local, name, flag = COUNTRIES[slug]
    rates = data["rates"]
    codes = menu_for(local) + [local]
    if any(not rates.get(c) for c in codes):
        return None
    default = menu_for(local)[0]
    one = rates[local] / rates[default]
    attr = " ".join("%s:%s" % (c, "%.8g" % rates[c]) for c in codes)
    fb = "1 %s ≈ %s %s (mid-market, %s)." % (default, fmt_amount(one, local), local, pretty_date(data["asof"]))
    return ('<div class="cconv" data-local="%s" data-local-name="%s" data-flag="%s" data-asof="%s" data-rates="%s">'
            '<div class="cconv-fb">%s</div></div>') % (local, name, flag, data["asof"], attr, fb)


def div_end(html, start):
    """index just past the </div> that closes the <div at `start`"""
    depth = 0
    for m in DIV_TOKEN.finditer(html, start):
        if m.group(0).lower().startswith("<div"):
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return m.end()
    return -1


def open_tag_start(html, idx):
    return html.rfind("<div", 0, idx)


def line_indent(html, idx):
    ls = html.rfind("\n", 0, idx) + 1
    m = re.match(r"[ \t]*", html[ls:idx])
    return m.group(0) if m and m.end() == idx - ls else None


def place(html, block):
    """insert `block` into a page that has none. Returns (html, where) with where 'currency' / 'key-facts' / None.

    'currency': the LAST thing in the Currency section (Kevin, 2026-11-08: "make the currency converter the last part
    of the currency section"), i.e. at the end of the last plain <div class="measure"> that follows the section's
    heading measure, right before the next section's heading."""
    m = re.search(r'<div\b[^>]*\bid="currency"', html)
    if m:
        end = div_end(html, m.start())
        last = None
        nxt_re = re.compile(r'\s*<div class="measure">')
        while end > 0:
            nxt = nxt_re.match(html, end)
            if not nxt:
                break
            start = nxt.end() - len('<div class="measure">')
            close = div_end(html, start)
            if close < 0:
                break
            last, end = (start, close), close
        if last:
            start, close = last
            at = close - len("</div>")
            ws = at
            while ws > start and html[ws - 1] in " \t":
                ws -= 1
            if ws > start and html[ws - 1] == "\n":           # pretty markup: its own line, at the children's indent
                first = re.compile(r'<div class="measure">\r?\n([ \t]*)').match(html, start)
                ind = first.group(1) if first else html[ws:at] + "  "
                cut = ws - 1 - (html[ws - 2] == "\r" if ws >= 2 else 0)
                return html[:cut] + "\n" + ind + block + html[cut:], "currency"
            return html[:at] + block + html[at:], "currency"
    m = re.search(r'<div\b[^>]*\bid="key-facts"', html)
    if m:
        end = div_end(html, m.start())
        if end > 0:
            ind = line_indent(html, m.start())
            if ind is not None:
                ins = "\n" + ind + '<div class="measure">' + block + "</div>"
            else:
                ins = '<div class="measure">' + block + "</div>"
            return html[:end] + ins + html[end:], "key-facts"
    return html, None


def js_stamp(root):
    p = Path(root) / "currency.js"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8] if p.is_file() else None


def depth_prefix(rel):
    return "../" * (len(rel.split("/")) - 1)


def set_script(html, rel, stamp):
    tag = '<script defer src="%scurrency.js?v=%s"></script>' % (depth_prefix(rel), stamp)
    found = SCRIPT_RE.search(html)
    if found:
        if found.group(0).strip() == tag:
            return html
        lead = re.match(r"[ \t]*", found.group(0)).group(0)
        nl = "\n" if found.group(0).endswith("\n") else ""
        return html[:found.start()] + lead + tag + nl + html[found.end():]
    i = html.rfind("</body>")
    if i < 0:
        return html
    return html[:i] + tag + "\n" + html[i:]


def strip_block(html):
    """take the widget out (with its own .measure when it sat under Key Facts, and the line it sat on)"""
    html = re.sub(r'\n?[ \t]*<div class="measure">' + BLOCK_RE.pattern + r'</div>', "", html)
    m = BLOCK_RE.search(html)
    while m:
        a = m.start()
        ls = html.rfind("\n", 0, a)
        if ls >= 0 and not html[ls + 1:a].strip():
            a = ls                                   # the line it sat on, so no blank line is left
        html = html[:a] + html[m.end():]
        m = BLOCK_RE.search(html)
    return html


def apply_page(html, rel, slug, data, stamp):
    """(new html, status) where status is 'ok' (no change), 'inserted:<where>', 'moved:<where>', 'refreshed', 'script',
    'unknown-country', 'no-place'"""
    block = widget(slug, data)
    if block is None:
        return html, "unknown-country"
    status = "ok"
    m = BLOCK_RE.search(html)
    # a widget already there is taken out and placed again, so one in an old spot (under the first paragraph,
    # before 2026-11-08's "make the currency converter the last part of the currency section") moves, stale rates
    # are refreshed, and one in the right spot comes back byte for byte
    placed, where = place(strip_block(html) if m else html, block)
    if not where:
        return html, "no-place"
    if not m:
        status = "inserted:" + where
    elif placed != html:
        old_at = strip_block(html[:m.start()]).count("\n")
        status = "refreshed" if m.group(0) != block and placed.count("\n", 0, placed.find(block)) == old_at \
            else "moved:" + where
    html = placed
    if stamp:
        new = set_script(html, rel, stamp)
        if new != html:
            html = new
            if status == "ok":
                status = "script"
    return html, status


# ---------------------------------------------------------------- pages
def itinerary_pages(root, live_only=False):
    root = Path(root)
    out_ = [p for p in sorted(root.glob("*/*-itinerary.html")) if p.parts[-2] not in SKIP_TOP]
    if live_only:
        return out_
    if root.resolve() == ROOT.resolve():
        sys.path.insert(0, str(ROOT / "tools"))
        import draft_paths as dp                                     # the one place draft paths live
        out_ += dp.draft_pages(name="*-itinerary.html", folders=DRAFT_FOLDERS)
    else:
        for f in DRAFT_FOLDERS:
            out_ += sorted(p for p in (root / "Drafts" / f).glob("*/*-itinerary.html") if p.is_file())
    return out_


def run(root=ROOT, apply=False, live_only=False, pages=None):
    root = Path(root)
    data = load_rates(root)
    if not data:
        return [("-", "no-rates")], {}
    stamp = js_stamp(root)
    results, changed = [], {}
    targets = [root / p for p in pages] if pages else itinerary_pages(root, live_only)
    for p in targets:
        rel = p.relative_to(root).as_posix()
        slug = p.parent.name
        with open(p, encoding="utf-8", newline="") as fh:
            raw = fh.read()
        new, status = apply_page(raw, rel, slug, data, stamp)
        results.append((rel, status))
        if new != raw:
            changed[rel] = new
            if apply:
                with open(p, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new)
    return results, changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--refresh-rates", action="store_true")
    ap.add_argument("--live-only", action="store_true")
    ap.add_argument("--pages", nargs="*", help="repo-relative pages instead of every itinerary")
    ap.add_argument("--root", help="treat this folder as the site root (tests)")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if a.root else ROOT
    if a.refresh_rates:
        ok, msg = refresh_rates(root)
        out(msg)
    data = load_rates(root)
    if not data:
        out("no tools/%s yet: run with --refresh-rates first" % RATES_NAME)
        return 1
    results, changed = run(root, apply=a.apply and not a.check, live_only=a.live_only, pages=a.pages)
    tag = "" if (a.apply and not a.check) else "[dry] "
    bad = 0
    for rel, status in results:
        cur = COUNTRIES.get(Path(rel).parent.name, ("?",))[0]
        if status in ("unknown-country", "no-place"):
            bad += 1
            out("  ! %s  %s" % (rel, "currency not mapped in COUNTRIES (skipped)" if status == "unknown-country"
                                 else "no Currency section and no Key Facts box (skipped)"))
        elif rel in changed:
            out("  %s%-64s %s  %s" % (tag, rel, cur, status))
    n = len(changed)
    out("%d itinerar%s, %d %s; rates as of %s" % (len(results), "y" if len(results) == 1 else "ies", n,
        "stale or missing" if a.check else ("changed" if tag == "" else "would change"), data["asof"]))
    if a.check:
        return 1 if (n or bad) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
