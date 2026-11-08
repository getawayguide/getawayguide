#!/usr/bin/env python3
"""Pin down tools/updated_line.py on a scratch site (never a real folder: memory
feedback_test_cleanup_never_in_real_folders). One page per hero template, plus resources.html.

    python tools/test_updated_line.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import updated_line as U

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    print("  %s  %s%s" % ("ok  " if ok else "FAIL", name, ("\n        " + detail) if (detail and not ok) else ""))
    passed, failed = passed + bool(ok), failed + (not ok)


LD = '<script type="application/ld+json">{"datePublished": "2026-04-17", "dateModified": "%s"}</script>\n'
XM = '<section class="xm" id="explore-more"><h2 class="xm-h">Explore More</h2></section>\n<!-- /explore-more -->\n'
GUIDE = ("<html><head>" + LD % "2026-09-01" + "</head><body>\n<section class=\"article-hero\">\n"
         "    <p class=\"article-lead\" style=\"x\">Volcanoes and lakes</p>\n"
         "    <div style=\"position:relative;font-family:'DM Mono',monospace\">Apr 17, 2026</div>\n  </section>\n"
         "  <div class=\"article-body\" style=\"max-width:825px\">\n    <p>Text.</p>\n"
         "    <div class=\"article-tip affiliate-note\">tip</div>\n    <p>Last.</p>\n" + XM + "</div>\n<footer></footer></body></html>\n")
ITIN = ("<html><head>" + LD % "2026-10-02" + "</head><body>\n  <div class=\"ah ah-banner\">\n    <div class=\"band\">\n"
        "      <div class=\"cpy\">\n        <h1 class=\"serif\">Itinerary</h1>\n"
        "        <div class=\"artsub\">Everything <a href=\"x\">Gyumri</a></div>\n      </div></div>\n  </div>\n"
        "  <div class=\"ah ah-none\"><div class=\"artsub\">hidden copy</div></div>\n"
        "  <div class=\"artbody\">\n  <div class=\"measure\">\n    <p class=\"copy\">Text.</p>\n  </div>\n"
        "  <div class=\"measure gg-updated\">Last updated: September 2026 · Based on firsthand experience</div>\n"
        + XM + "</div>\n<footer></footer></body></html>\n")
FN = ("<html><head>" + (LD % "2026-11-01").replace("\n", "\r\n") + "</head><body>\r\n<div class=\"fn-hero\">\r\n"
      "  <h1 class=\"fn-hero-title\">El Salvador Field Notes</h1>\r\n  <p class=\"fn-hero-lead\">The quick version.</p>\r\n"
      "  <div class=\"country-places-row\"></div>\r\n</div>\r\n<div class=\"article-body\">\r\n  <p>Notes.</p></div>\r\n"
      "<footer></footer></body></html>\r\n")
RES = ("<html><body>\n<div class=\"page\" id=\"page-resources\">\n  <div class=\"blk-wrap\">rows</div>\n"
       "  <footer></footer>\n</div></body></html>\n")
HOME = "<html><body><h1>Home</h1><footer></footer></body></html>\n"


def build(tmp):
    site = Path(tmp) / "site"
    for rel, body in (("el-salvador/santa-ana.html", GUIDE), ("armenia/armenia-itinerary.html", ITIN),
                      ("el-salvador/field-notes.html", FN), ("resources.html", RES), ("index.html", HOME),
                      ("archive/el-salvador/santa-ana.html", GUIDE)):
        p = site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(body)
    return site


def run(site, *args):
    return subprocess.run([sys.executable, str(HERE / "updated_line.py"), "--root", str(site)] + list(args),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def rd(p):
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def main():
    print("updated_line.py (scratch site)")
    check("Month YYYY / Mon D, YYYY", U.month_year(U.date(2026, 9, 1)) == "September 2026"
          and U.short(U.date(2026, 9, 1)) == "Sep 1, 2026")
    tmp = tempfile.mkdtemp(prefix="updlinetest_")
    try:
        site = build(tmp)
        g, i, f, r, h = (site / "el-salvador/santa-ana.html", site / "armenia/armenia-itinerary.html",
                         site / "el-salvador/field-notes.html", site / "resources.html", site / "index.html")
        res = run(site)
        check("dry run writes nothing", rd(g) == GUIDE and rd(i) == ITIN and rd(f) == FN, res.stderr)
        check("--check fails while lines are missing", run(site, "--check").returncode == 1)
        res = run(site, "--apply")
        a, b, c, d = rd(g), rd(i), rd(f), rd(r)
        line = lambda m: U.LINE % (U.TEXT % m)
        check("guide: the line sits right before Explore More, from dateModified",
              (line("September 2026") + "\n" + XM.split("\n")[0]) in a, a)
        check("guide: the hero date follows dateModified (Apr 17 -> Sep 1)", ">Sep 1, 2026</div>" in a and "Apr 17" not in a)
        check("itinerary: the bottom line refreshed in place, never doubled",
              b.count("gg-updated") == 1 and ("  " + line("October 2026") + "\n<section") in b, b)
        check("itinerary: hero date under the banner's description only",
              b.count("hero-date") == 1 and b.index("hero-date") < b.index("ah-none") and ">Oct 2, 2026</div>" in b)
        check("field notes: no hero date, the line as the body's last line, CRLF kept",
              "hero-date" not in c and (line("November 2026") + "\r\n</div>") in c
              and "\n" not in c.replace("\r\n", ""), repr(c))
        check("resources: a line before its footer, no hero date",
              "gg-updated" in d and d.index("gg-updated") < d.index("<footer") and "hero-date" not in d)
        check("not an article -> untouched", rd(h) == HOME)
        check("archive/ is never touched", rd(site / "archive/el-salvador/santa-ana.html") == GUIDE)
        snap = (a, b, c, d)
        res = run(site, "--apply")
        check("second run is a no-op", (rd(g), rd(i), rd(f), rd(r)) == snap and "0 changed" in res.stdout, res.stdout)
        check("--check passes", run(site, "--check").returncode == 0)
        check("lint hook is quiet on a current page", U.problems("el-salvador/santa-ana.html", a) == [])
        # a new dateModified moves both lines
        with open(g, "w", encoding="utf-8", newline="") as fh:
            fh.write(a.replace('"dateModified": "2026-09-01"', '"dateModified": "2026-11-05"'))
        check("lint hook flags a stale page", any("stale" in m for m in U.problems("el-salvador/santa-ana.html", rd(g))))
        run(site, "--apply")
        a2 = rd(g)
        check("dateModified change refreshes both lines", line("November 2026") in a2 and ">Nov 5, 2026</div>" in a2
              and a2.count("gg-updated") == 1)
        # resources keeps its month on a later run (no JSON-LD: a commit must not move it)
        kept = d.replace(U.TEXT % U.month_year(U.page_date("resources.html", d)[0]), U.TEXT % "May 2026")
        with open(r, "w", encoding="utf-8", newline="") as fh:
            fh.write(kept)
        run(site, "--apply")
        check("a page with no JSON-LD keeps the month its line shows", rd(r) == kept)
        # field notes carry no hero date: one an earlier run added comes off (Kevin, 2026-11-08)
        fn_dated = c.replace('<p class="fn-hero-lead">The quick version.</p>\r\n',
                             '<p class="fn-hero-lead">The quick version.</p>\r\n'
                             '  <div class="hero-date" style="x">Nov 1, 2026</div>\r\n')
        check("lint hook flags a hero date on field notes",
              any("no hero date" in m for m in U.problems("el-salvador/field-notes.html", fn_dated)))
        with open(f, "w", encoding="utf-8", newline="") as fh:
            fh.write(fn_dated)
        run(site, "--apply")
        check("field notes: an old hero date is removed", rd(f) == c, repr(rd(f)))
        # the tip box is affiliate_links.py's to remove, never this tool's
        check("the old tip box is left for affiliate_links.py", "affiliate-note" in a2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 70)
    print("  %d checks, %d failed" % (passed + failed, failed))
    print("=" * 70)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
