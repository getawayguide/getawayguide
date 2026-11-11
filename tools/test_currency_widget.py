#!/usr/bin/env python3
"""Pin down tools/currency_widget.py (the itineraries' currency converter) on a scratch site (never a real folder:
memory feedback_test_cleanup_never_in_real_folders).

  insertion    as the Currency section's last part (pretty and one-line markup; one in the old spot moves), under Key Facts when a
               page has no Currency section, the currency.js tag with the page's depth and the content stamp
  idempotent   a second --apply changes nothing; --check is 0 when current, 1 when a page is missing it
  refresh      new rates / as-of rewrite the block in place; a fetch that fails keeps the old rates file
  special      a euro country drops EUR from the menu and defaults to USD; a US-dollar country (El Salvador) drops
               USD and opens on 1 EUR = x USD; an unmapped country is skipped
  JS off       the fallback line is inside the div; the rendered widget (Playwright, file://) replaces it
  field notes  any other page with a block (live or Drafts/Field Notes) is found by its markup and refreshed in place:
               byte-identical apart from the block and the ?v= stamp (defer="" and CRLF kept), stale fails --check,
               --live-only skips the drafts' copy, a page without a block never gets one, archive/ Images/ and
               editor.html are never touched, a block in a currency with no rate is left as is and reported

    python tools/test_currency_widget.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import currency_widget as C

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    sys.stdout.buffer.write(("  %s  %s%s\n" % ("ok  " if ok else "FAIL", name,
                             ("\n        " + detail) if (detail and not ok) else "")).encode("utf-8"))
    passed, failed = passed + bool(ok), failed + (not ok)


RATES = {"asof": "2026-11-08", "source": C.FEED, "fetched": "2026-11-08T00:00:00Z",
         "rates": {"USD": 1, "EUR": 0.9, "GBP": 0.75, "AUD": 1.45, "CAD": 1.4, "CHF": 0.82, "MKD": 55.0,
                   "AMD": 360.5, "IDR": 18000.0}}
HEAD = '<html><head></head><body class="frame">\n  <div class="artbody">\n'
KEYFACTS = ('  <div class="measure" id="key-facts">\n    <div class="v v-facts" data-v="a"><div class="f-t">Currency</div>'
            '</div>\n  </div>\n')
SECTION = ('  <div class="measure" id="currency">\n    <div class="v v-head" data-v="a">\n'
           '      <div class="serif h-a">Currency</div>\n    </div>\n  </div>\n'
           '  <div class="measure">\n    <p class="copy">First paragraph.</p>\n    <p class="copy">Second.</p>\n  </div>\n'
           '  <div class="measure">\n    <p class="copy">Last.</p>\n  </div>\n'
           '  <div class="measure" id="language">\n    <div class="v v-head" data-v="a">Language</div>\n  </div>\n'
           '  <div class="measure">\n    <p class="copy">Hello.</p>\n  </div>\n')
TAIL = '  </div>\n<script defer src="../search.js"></script>\n</body></html>\n'
PRETTY = HEAD + KEYFACTS + SECTION + TAIL
ONELINE = (HEAD + '<div class="measure" id="currency"><div class="v v-head" data-v="a"><div class="serif h-a">Currency'
           '</div></div></div>\n<div class="measure"><p class="copy">El Salvador uses the dollar.</p></div>\n' + TAIL)
NOSECTION = HEAD + KEYFACTS + '  <div class="measure">\n    <p class="copy">Text.</p>\n  </div>\n' + TAIL

# a field-notes page that carries the block between two lists, as north-macedonia/field-notes.html does (2026-11-11),
# saved by the editor (defer="") with an old stamp and old rates: refreshed IN PLACE, nothing else touched
FN_OLD = ('<div class="cconv" data-local="MKD" data-local-name="Denar" data-flag="mk" data-asof="2026-11-01" '
          'data-rates="USD:1 EUR:0.8 GBP:0.7 AUD:1.4 CAD:1.3 CHF:0.8 MKD:50"><div class="cconv-fb">1 USD ≈ 50.00 MKD '
          '(mid-market, Nov 1, 2026).</div></div>')
FIELDNOTES = ('<html><head></head><body class="fn">\n<h2 id="money">Money, Safety &amp; Language</h2>\n<ul>\n'
              '  <li><b>Cash</b> — ATMs everywhere.</li>\n</ul>\n    ' + FN_OLD + '\n<ul>\n  <li><b>Safety</b> — fine.'
              '</li>\n</ul>\n<script defer="" src="../currency.js?v=00000000"></script>\n</body></html>\n')
# the same in a draft, CRLF, with the attributes in another order (an editor round can re-serialize them)
FN_OLD_DRAFT = FN_OLD.replace('data-local="MKD" data-local-name="Denar" data-flag="mk"',
                              'data-flag="mk" data-local-name="Denar" data-local="MKD"')
FIELDNOTES_DRAFT = (FIELDNOTES.replace(FN_OLD, FN_OLD_DRAFT).replace("../currency.js", "../../../currency.js")
                    .replace("\n", "\r\n"))
NOBLOCK = '<html><head></head><body>\n<h2 id="money">Money</h2>\n<ul>\n  <li><b>Cash</b> — dram.</li>\n</ul>\n</body></html>\n'


def build(tmp):
    site = Path(tmp) / "site"
    pages = {"armenia/armenia-itinerary.html": PRETTY,
             "el-salvador/el-salvador-itinerary.html": ONELINE,
             "Drafts/Upcoming/north-macedonia/north-macedonia-itinerary.html": PRETTY,
             "Drafts/Full Articles/greece/greece-itinerary.html": NOSECTION,
             "Drafts/Full Articles/indonesia/indonesia-itinerary.html": ONELINE.replace("El Salvador uses the dollar", "Rupiah"),
             "Drafts/Full Articles/atlantis/atlantis-itinerary.html": PRETTY,
             "archive/old-itinerary.html": PRETTY,
             "north-macedonia/field-notes.html": FIELDNOTES,
             "Drafts/Field Notes/north-macedonia/field-notes.html": FIELDNOTES_DRAFT,
             "armenia/field-notes.html": NOBLOCK,
             "archive/field-notes.html": FIELDNOTES,
             "Images/web/stray.html": FIELDNOTES,
             "editor.html": FIELDNOTES}
    for rel, body in pages.items():
        p = site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(body)
    (site / "tools").mkdir()
    (site / "tools" / C.RATES_NAME).write_text(json.dumps(RATES), encoding="utf-8")
    shutil.copy(ROOT / "currency.js", site / "currency.js")
    shutil.copy(ROOT / "artifact.css", site / "artifact.css")
    return site


def rd(p):
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def run(site, *args):
    r = subprocess.run([sys.executable, str(HERE / "currency_widget.py"), "--root", str(site)] + list(args),
                       capture_output=True)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def main():
    tmp = tempfile.mkdtemp(prefix="cconv_test_")
    try:
        site = build(tmp)
        arm = site / "armenia/armenia-itinerary.html"
        es = site / "el-salvador/el-salvador-itinerary.html"
        nm = site / "Drafts/Upcoming/north-macedonia/north-macedonia-itinerary.html"
        gr = site / "Drafts/Full Articles/greece/greece-itinerary.html"
        idr = site / "Drafts/Full Articles/indonesia/indonesia-itinerary.html"
        atl = site / "Drafts/Full Articles/atlantis/atlantis-itinerary.html"
        fn = site / "north-macedonia/field-notes.html"
        fnd = site / "Drafts/Field Notes/north-macedonia/field-notes.html"
        stamp = C.js_stamp(site)

        code, out = run(site)
        check("dry run writes nothing", rd(arm) == PRETTY and rd(nm) == PRETTY and rd(fn) == FIELDNOTES, out)
        code, out = run(site, "--check")
        check("--check is 1 while pages lack it", code == 1, out)

        code, out = run(site, "--apply")
        a = rd(arm)
        check("Armenia: widget is the last part of the Currency section (after its last paragraph), own indented line",
              '<p class="copy">Last.</p>\n    <div class="cconv" data-local="AMD"' in a, a)
        check("Armenia: the next section's heading follows it",
              '(mid-market, Nov 8, 2026).</div></div>\n  </div>\n  <div class="measure" id="language">' in a, a)
        check("Armenia: the paragraphs before it untouched",
              '<p class="copy">First paragraph.</p>\n    <p class="copy">Second.</p>\n  </div>' in a, a)
        check("Armenia: fallback line inside the div",
              '<div class="cconv-fb">1 USD ≈ 361 AMD (mid-market, Nov 8, 2026).</div></div>' in a, a)
        check("Armenia: menu rates + local in data-rates",
              'data-rates="USD:1 EUR:0.9 GBP:0.75 AUD:1.45 CAD:1.4 CHF:0.82 AMD:360.5"' in a, a)
        check("live page: script tag with ../ and the content stamp, once",
              a.count('<script defer src="../currency.js?v=%s"></script>' % stamp) == 1, a)
        check("one widget only", a.count('class="cconv"') == 1)
        n = rd(nm)
        check("draft: script tag with ../../../", '<script defer src="../../../currency.js?v=%s">' % stamp in n, n)
        check("draft NM: MKD 55.00 fallback", "1 USD ≈ 55.00 MKD" in n, n)
        g = rd(gr)
        check("no Currency section: own .measure right after Key Facts",
              '  </div>\n  <div class="measure"><div class="cconv" data-local="EUR"' in g, g)
        check("euro country: no EUR in the menu, default USD",
              'data-rates="USD:1 GBP:0.75 AUD:1.45 CAD:1.4 CHF:0.82 EUR:0.9"' in g and "1 USD ≈ 0.90 EUR" in g, g)
        i = rd(idr)
        check("one-line markup: widget inline after the paragraph",
              '<p class="copy">Rupiah.</p><div class="cconv" data-local="IDR"' in i, i)
        check("zero-decimal currency: whole number", "1 USD ≈ 18,000 IDR" in i, i)
        e = rd(es)
        check("US-dollar country (El Salvador): gets the converter, menu without USD, opens on 1 EUR",
              'data-local="USD" data-local-name="US dollar" data-flag="us"' in e
              and 'data-rates="EUR:0.9 GBP:0.75 AUD:1.45 CAD:1.4 CHF:0.82 USD:1"' in e
              and "1 EUR \u2248 1.11 USD (mid-market, Nov 8, 2026)." in e, e)
        check("El Salvador: inline, as the Currency section's last part, + the script tag",
              '<p class="copy">El Salvador uses the dollar.</p><div class="cconv"' in e
              and '<script defer src="../currency.js?v=%s"></script>' % stamp in e, e)
        check("unmapped country skipped and reported", rd(atl) == PRETTY and "atlantis" in out and "not mapped" in out, out)
        check("archive/ is never touched", rd(site / "archive/old-itinerary.html") == PRETTY)
        check("Key Facts placement only when there is no Currency section", 'data-local="AMD"' in a and
              a.index('class="cconv"') > a.index('id="currency"'))

        # field notes (any page that is not an itinerary): found by the block's markup, refreshed in place
        fn_new = C.widget("north-macedonia", RATES)
        f = rd(fn)
        check("field notes: the block is refreshed in place (new rates, as-of, fallback), the page byte-identical "
              "otherwise, the editor's defer=\"\" kept and only the ?v= stamp updated",
              f == FIELDNOTES.replace(FN_OLD, fn_new).replace("v=00000000", "v=" + stamp), f)
        check("field notes: reported as refreshed", "north-macedonia/field-notes.html" in out and "refreshed" in out, out)
        fd = rd(fnd)
        check("draft field notes (Drafts/Field Notes, CRLF, attributes re-ordered): rebuilt in place, CRLF kept",
              fd == FIELDNOTES_DRAFT.replace(FN_OLD_DRAFT, fn_new).replace("v=00000000", "v=" + stamp), fd)
        check("a non-itinerary page without a block is never given one", rd(site / "armenia/field-notes.html") == NOBLOCK)
        check("archive/, Images/ and editor.html are never touched, block or not",
              all(rd(site / r) == FIELDNOTES for r in ("archive/field-notes.html", "Images/web/stray.html", "editor.html")))
        check("summary counts the other pages", "and 2 other pages with a converter" in out, out)

        before = {p: rd(p) for p in (arm, nm, gr, idr, es, fn, fnd)}
        code, out = run(site, "--apply")
        check("second --apply changes nothing (idempotent)", all(rd(p) == s for p, s in before.items()), out)
        code, out = run(site, "--check")
        check("--check flags only the unmapped page", code == 1 and "atlantis" in out and "0 stale" in out, out)
        atl.unlink()
        code, out = run(site, "--check")
        check("--check is 0 when every page is current", code == 0, out)

        # a stale field-notes block fails --check; a block whose currency has no rate is left as is and reported
        cur_fn = rd(fn)
        with open(fn, "w", encoding="utf-8", newline="") as fh:
            fh.write(cur_fn.replace(fn_new, FN_OLD))
        code, out = run(site, "--check")
        check("--check is 1 when a field-notes block is stale", code == 1 and "north-macedonia/field-notes.html" in out
              and "1 stale" in out, out)
        run(site, "--apply")
        check("...and --apply puts it back byte for byte", rd(fn) == cur_fn)
        odd = site / "kosovo/guide.html"
        odd.parent.mkdir(parents=True, exist_ok=True)
        odd_html = cur_fn.replace('data-local="MKD"', 'data-local="XYZ"')
        with open(odd, "w", encoding="utf-8", newline="") as fh:
            fh.write(odd_html)
        code, out = run(site, "--apply")
        check("a block in a currency with no rate: left as is and reported", rd(odd) == odd_html
              and "kosovo/guide.html" in out and "no rate" in out, out)
        odd.unlink()

        # a widget in the old spot (under the first paragraph, before 2026-11-08) moves to the end of the section
        cur = rd(arm)
        blk = C.BLOCK_RE.search(cur).group(0)
        old = C.strip_block(cur).replace('<p class="copy">First paragraph.</p>',
                                         '<p class="copy">First paragraph.</p>\n    ' + blk)
        with open(arm, "w", encoding="utf-8", newline="") as f:
            f.write(old)
        code, out = run(site, "--apply")
        check("a widget in the old spot moves to the end of the section", rd(arm) == cur and "moved:currency" in out, out)

        # an editor round re-serialized the attribute order: still found, refreshed in place, never doubled
        moved = rd(arm).replace('<div class="cconv" data-local="AMD"', '<div data-x="1" class="cconv" data-local="AMD"')
        with open(arm, "w", encoding="utf-8", newline="") as f:
            f.write(moved)
        run(site, "--apply")
        check("a re-serialized block is replaced, not doubled", rd(arm).count('class="cconv"') == 1 and 'data-x' not in rd(arm))

        # refresh: new rates + date rewrite the block
        r2 = dict(RATES, asof="2026-12-01", rates=dict(RATES["rates"], AMD=400.0))
        (site / "tools" / C.RATES_NAME).write_text(json.dumps(r2), encoding="utf-8")
        code, out = run(site, "--apply")
        a = rd(arm)
        check("refresh: new rate + as-of in the block", 'data-asof="2026-12-01"' in a and "AMD:400" in a
              and "1 USD ≈ 400 AMD (mid-market, Dec 1, 2026)" in a, a)
        check("refresh: block still once", a.count('class="cconv"') == 1)
        f = rd(fn)
        check("refresh: the field-notes block takes the new date in place",
              f == cur_fn.replace(fn_new, C.widget("north-macedonia", r2)) and "(mid-market, Dec 1, 2026)" in f, f)
        # --live-only: a draft's stale block stays stale
        fd_before = rd(fnd).replace(C.widget("north-macedonia", r2), FN_OLD)
        with open(fnd, "w", encoding="utf-8", newline="") as fh:
            fh.write(fd_before)
        code, out = run(site, "--apply", "--live-only")
        check("--live-only leaves drafts alone", "Drafts/" not in out and rd(fnd) == fd_before, out)
        run(site, "--apply")

        # refresh_rates: a fetch that fails keeps the file; a good payload replaces it
        def boom():
            raise OSError("no network")
        ok, msg = C.refresh_rates(site, [("x", boom)])
        check("no network: rates kept, message says so", not ok and "kept the rates as of 2026-12-01" in msg
              and C.load_rates(site)["asof"] == "2026-12-01", msg)
        feed = {"result": "success", "base_code": "USD", "time_last_update_utc": "Mon, 09 Nov 2026 00:02:31 +0000",
                "rates": {c: 2.0 for c in set(C.MENU) | {v[0] for v in C.COUNTRIES.values()}}}
        feed["rates"]["USD"] = 1
        ok, msg = C.refresh_rates(site, [("x", lambda: json.dumps(feed))])
        check("good feed: saved with the feed's date", ok and C.load_rates(site)["asof"] == "2026-11-09", msg)
        bad = dict(feed, rates={"USD": 1, "EUR": 0.9})
        ok, msg = C.refresh_rates(site, [("x", lambda: json.dumps(bad))])
        check("feed missing a needed currency: refused, file kept", not ok and C.load_rates(site)["asof"] == "2026-11-09", msg)
        run(site, "--apply")                       # the pages take the saved feed (every rate 2.0) for the render checks


        # the rendered widget + JS off (file://, so the photo server is not needed)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            check("playwright available for the render checks", False)
            return
        page = rd(nm).replace("</head>", '<link rel="stylesheet" href="../../../artifact.css"></head>')
        with open(nm, "w", encoding="utf-8", newline="") as f:
            f.write(page)
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(java_script_enabled=False)
            pg = ctx.new_page()
            pg.goto(nm.as_uri())
            check("JS off: the fallback line shows", pg.locator(".cconv").inner_text().startswith("1 USD ≈"),
                  pg.locator(".cconv").inner_text())
            ctx.close()
            ctx = b.new_context(viewport={"width": 1000, "height": 800})
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(nm.as_uri())
            pg.wait_for_selector(".cconv-grid")
            check("JS on: the fallback is replaced by the widget", pg.locator(".cconv-fb").count() == 0
                  and pg.locator(".cconv-side").count() == 2)
            check("default 1 USD = 2.00 MKD", pg.locator(".cconv-a").input_value() == "1"
                  and pg.locator(".cconv-b").input_value() == "2.00")
            pg.locator(".cconv-a").fill("10000")
            check("typing 10000 shows 10,000", pg.locator(".cconv-a").input_value() == "10,000")
            pg.locator(".cconv-b").fill("4")
            check("typing on the right converts the left", pg.locator(".cconv-a").input_value() == "2.00")
            pg.select_option(".cconv-sel", "GBP")
            check("menu switch resets the left to 1", pg.locator(".cconv-a").input_value() == "1")
            check("flag paths resolve from the script's folder", pg.eval_on_selector(
                ".cconv-lbl img", "e => e.src").endswith("/Images/web/flags/mk.png"))
            check("no page errors", not errs, "; ".join(errs))
            b.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 70)
    print("  %d checks, %d failed" % (passed + failed, failed))
    print("=" * 70)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
