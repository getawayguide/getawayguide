#!/usr/bin/env python3
"""Pin down what tools/affiliate_links.py may do to a booking link, on scratch copies only.

Every URL below is a real one from the live pages (2026-11-07 scan, before R1 cleaned them), so a rule
change that moves one of these moves something that shipped. The page tests build a throwaway site in a
temp folder (never a real folder: memory feedback_test_cleanup_never_in_real_folders) and drive the CLI
with --root/--env, so the real .env and the real pages are never read or written.

    python tools/test_affiliate_links.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import affiliate_links as A

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    print("  %s  %s%s" % ("ok  " if ok else "FAIL", name, ("\n        " + detail) if (detail and not ok) else ""))
    passed, failed = passed + bool(ok), failed + (not ok)


# ---------------------------------------------------------------------------------------- clean_url
CLEAN = [
    ("Hostelworld Google-ad click -> property id + slug",
     "https://www.hostelworld.com/hostels/p/313345/the-bearded-dad-hostel/?source=ppc_gooads_nonbrand_dsk_dsa_pn_en_eucatchall&network=g&campaign_id=20981130437&adgroup_id=161856943121&criteria_id=dsa-443313842330&creative_id=689218685481&location_physical_id=9031939",
     "https://www.hostelworld.com/hostels/p/313345/the-bearded-dad-hostel/"),
    ("Hostelworld app address keeps its path, loses stale dates + gclid",
     "https://www.hostelworld.com/pwa/hosteldetails.php/Captain-Morgan-Hostel-Lake-Coatepeque/Santa-Ana/299622?guests=1&source=ppc_gooads_nonbrand_dsk_dsa_pn_en_us&network=g&gclid=CjwKCAjw&from=2026-03-25&to=2026-03-26",
     "https://www.hostelworld.com/pwa/hosteldetails.php/Captain-Morgan-Hostel-Lake-Coatepeque/Santa-Ana/299622"),
    ("Hostelworld Spanish ad page -> the English page of the same property",
     "https://www.hostelworld.com/es/albergues/p/44341/viajero-cartagena-centro-hostel/?source=ppc_gooads_nonbrand_dsk_search_pn_es_us&network=g",
     "https://www.hostelworld.com/hostels/p/44341/viajero-cartagena-centro-hostel/"),
    ("GetYourGuide tour: ranking_uuid + q go, en-gb goes, slug + t-id stay",
     "https://www.getyourguide.com/en-gb/shaki-armenia-l217976/group-tour-tatev-monastery-khndzoresk-shaki-waterfall-t1296775/?ranking_uuid=617b9f10-faed-4077-aa4a-fb38dbe95abb",
     "https://www.getyourguide.com/shaki-armenia-l217976/group-tour-tatev-monastery-khndzoresk-shaki-waterfall-t1296775/"),
    ("GetYourGuide ad search keeps q + et, drops CD951 and the ad fields",
     "https://www.getyourguide.com/s?campaign_id=6654625102&adgroup_id=109582297841&partner_id=CD951&gad_source=1&gbraid=0AAAA&gclid=CjwK&q=Perito+Moreno+Glacier&et=808127",
     "https://www.getyourguide.com/s/?q=Perito+Moreno+Glacier&et=808127"),
    ("GetYourGuide plain search is left byte for byte",
     "https://www.getyourguide.com/s/?q=K%C3%B6pr%C3%BCl%C3%BC%20Canyon%20rafting%20Antalya",
     "https://www.getyourguide.com/s/?q=K%C3%B6pr%C3%BCl%C3%BC%20Canyon%20rafting%20Antalya"),
    ("Booking hotel page loses aid=311088",
     "https://www.booking.com/hotel/am/yellow-tree.html?aid=311088",
     "https://www.booking.com/hotel/am/yellow-tree.html"),
    ("Booking ad search keeps the hotel + city, drops aid/label/sid/gclid",
     "https://www.booking.com/searchresults.html?aid=311088&label=arinna-cappadocia-tWGY&gclid=CjwK&highlighted_hotels=6033046&redirected=1&city=-750642&hlrd=no_dates&source=hotel&expand_sb=1&keep_landing=1&sid=403ce41f",
     "https://www.booking.com/searchresults.html?highlighted_hotels=6033046&city=-750642"),
    ("Expedia hotel: stale dates, pwa_ts, referrerUrl all go",
     "https://www.expedia.com/Split-Hotels-Cornaro-Hotel.h8824056.Hotel-Information?chkin=2026-08-16&chkout=2026-08-17&x_pwa=1&rfrr=HSR&pwa_ts=1785643108676&referrerUrl=aHR0&useRewards=true&rm1=a2&regionId=3255",
     "https://www.expedia.com/Split-Hotels-Cornaro-Hotel.h8824056.Hotel-Information"),
    ("Hotels.com hotel the same",
     "https://www.hotels.com/ho3068351648/el-rancho-de-don-moncho-jicalapa-el-salvador/?chkin=2026-04-16&chkout=2026-04-17&x_pwa=1",
     "https://www.hotels.com/ho3068351648/el-rancho-de-don-moncho-jicalapa-el-salvador/"),
    ("Airbnb room: reservation tokens go",
     "https://www.airbnb.com/rooms/5994831?c=.pi80.pkYm9v&euid=68196e19&source_impression_id=p3_1782966027",
     "https://www.airbnb.com/rooms/5994831"),
    ("Busbud email utm_* go",
     "https://www.busbud.com/en?utm_source=busbud&utm_medium=email&utm_campaign=transactional_ticket-confirmation_en",
     "https://www.busbud.com/en"),
    ("GoTrip transfer keeps its search, loses the ad click",
     "https://gotrip.ge/en/transfer/from-lopota_lake-to-tbilisi_railway_station?load=1&sort_price_asc=1&hottransfer=0&page=1&utm_source=google&utm_medium=cpc&network=g&placement=&position=&device=c.&geo=.9061268&p=googleeng&gad_source=1&gbraid=0AAA&gclid=Cj0K",
     "https://gotrip.ge/en/transfer/from-lopota_lake-to-tbilisi_railway_station?load=1&sort_price_asc=1&hottransfer=0&page=1"),
    ("Ferryhopper brand ad -> the home page",
     "https://www.ferryhopper.com/?g_acctid=128-527-7584&g_adgroupid=141687857261&g_network=g",
     "https://www.ferryhopper.com/"),
    ("GuruWalk past dates go, the hub stays",
     "https://www.guruwalk.com/a/search?beginsAt=2026-06-26&endsAt=2026-06-26&vertical=free-tour&hub=cusco",
     "https://www.guruwalk.com/a/search?vertical=free-tour&hub=cusco"),
    ("a bare domain is not given a slash", "https://booking.com", "https://booking.com"),
    ("a Google Maps link is never touched",
     "https://www.google.com/maps/place/x/@40.1,44.5,14z?entry=ttu&g_ep=EgoyMDI2",
     "https://www.google.com/maps/place/x/@40.1,44.5,14z?entry=ttu&g_ep=EgoyMDI2"),
]


def test_clean():
    print("\nclean_url")
    for why, before, want in CLEAN:
        got = A.clean_url(before)
        check(why, got == want, "got %s" % got)
        check(why + " (stable)", A.clean_url(got) == got)


# ------------------------------------------------------------------------------------------ tagging
IDS = {"hostelworld": "1011lABC", "getyourguide": "XYZ123", "viator": "P00012345", "viator_mcid": "42383",
       "stay22": "kevinaid"}


def test_tag():
    print("\ntag_url / unwrap")
    hw = "https://www.hostelworld.com/hostels/p/313345/the-bearded-dad-hostel/"
    t, ok = A.tag_url(hw, IDS, "albania/field-notes.html")
    check("Hostelworld -> Partnerize camref/pubref/destination",
          ok and t == "https://prf.hn/click/camref:1011lABC/pubref:albania_field_notes/destination:"
                      "https%3A%2F%2Fwww.hostelworld.com%2Fhostels%2Fp%2F313345%2Fthe-bearded-dad-hostel%2F", t)
    check("Partnerize link unwraps to the plain address", A.clean_url(t) == hw)
    g = "https://www.getyourguide.com/s/?q=Perito+Moreno+Glacier&et=808127"
    t, ok = A.tag_url(g, IDS, "argentina/field-notes.html")
    check("GetYourGuide -> partner_id + utm_medium + cmp, search kept",
          t == "https://www.getyourguide.com/s/?q=Perito+Moreno+Glacier&et=808127&partner_id=XYZ123"
               "&utm_medium=online_publisher&cmp=argentina_field_notes", t)
    check("tagged GetYourGuide cleans back to the plain address", A.clean_url(t) == g, A.clean_url(t))
    v = "https://www.viator.com/tours/Seminyak/Bali-Instagram-Tour/d34198-7905P9"
    t, ok = A.tag_url(v, IDS, "indonesia/field-notes.html")
    check("Viator -> pid, mcid, medium=link, campaign",
          t == v + "?pid=P00012345&mcid=42383&medium=link&campaign=indonesia_field_notes", t)
    check("tagged Viator cleans back", A.clean_url(t) == v)
    b = "https://www.booking.com/hotel/am/yellow-tree.html"
    t, ok = A.tag_url(b, IDS, "armenia/yerevan.html")
    check("Booking -> Stay22 /allez/booking with the encoded link",
          t == "https://www.stay22.com/allez/booking?aid=kevinaid&campaign=armenia_yerevan&link="
               "https%3A%2F%2Fwww.booking.com%2Fhotel%2Fam%2Fyellow-tree.html", t)
    check("Stay22 link unwraps", A.clean_url(t) == b)
    e = "https://www.expedia.com/Split-Hotels-Cornaro-Hotel.h8824056.Hotel-Information"
    check("Expedia -> /allez/expedia", A.tag_url(e, IDS, "croatia/field-notes.html")[0].startswith(
        "https://www.stay22.com/allez/expedia?aid=kevinaid&campaign=croatia_field_notes&link="))
    check("Airbnb is never tagged", A.tag_url("https://www.airbnb.com/rooms/5994831", IDS, "x.html")[1] is False)
    check("no ID for the program -> untouched",
          A.tag_url(hw, {"getyourguide": "XYZ123"}, "x.html") == (hw, False))
    check("campaign never carries a hyphen (Stay22 splits on it)",
          "-" not in A.campaign_of("el-salvador/top-10-el-salvador.html"))


# ------------------------------------------------------------------------------------------- pages
NAV = '<nav><a href="https://www.hostelworld.com/?gclid=abc">HW</a></nav>\n'
PAGE_FN = (
    "<!doctype html>\n<html><head><title>Albania</title></head><body>\n" + NAV +
    "<main>\n  <h2>Tirana</h2>\n  <p>Intro with no booking link.</p>\n  <ul>\n"
    "    <li><b>Where to stay</b> — <a href=\"https://www.hostelworld.com/hostels/p/313345/the-bearded-dad-hostel/"
    "?source=ppc_gooads_nonbrand_dsk_dsa_pn_en_eucatchall&amp;network=g&amp;campaign_id=1\" target=\"_blank\" "
    "rel=\"noopener noreferrer\">Bearded Dad</a></li>\n"
    "    <li>Tour: <a href=\"https://www.getyourguide.com/en-gb/tirana-l1/tour-t1/?ranking_uuid=1\">Tour</a></li>\n"
    "  </ul>\n  <p>Room: <a href=\"https://www.airbnb.com/rooms/5?euid=1\">Airbnb</a></p>\n</main>\n"
    "<footer><a href=\"https://www.instagram.com/x\">IG</a></footer>\n</body></html>\n")
PAGE_ART = (
    "﻿<!doctype html>\r\n<html><body>\r\n<div class=\"article-body\">\r\n"
    "  <p>Text.</p>\r\n  <h3><a href=\"https://www.booking.com/hotel/am/kantar-hostel.html?aid=311088\">Kantar</a></h3>\r\n"
    "  <p>More <a href=\"https://www.viator.com/tours/x/d1-2P3?mcid=1&amp;pid=P00999\">Viator</a>.</p>\r\n"
    "</div>\r\n</body></html>\r\n")


def run(root, env, *args):
    e = {k: v for k, v in os.environ.items() if k not in A.ENV_KEYS.values() and k not in ("VIATOR_MCID", "AFFILIATE_LINKS")}
    e["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, str(HERE / "affiliate_links.py"), "--root", str(root), "--env", str(env)] + list(args),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=e)
    return r


def build(tmp):
    site = Path(tmp) / "site"
    (site / "albania").mkdir(parents=True)
    (site / "armenia").mkdir()
    (site / "archive" / "albania").mkdir(parents=True)
    with open(site / "albania" / "field-notes.html", "w", encoding="utf-8", newline="") as f:
        f.write(PAGE_FN)
    with open(site / "armenia" / "yerevan.html", "w", encoding="utf-8", newline="") as f:
        f.write(PAGE_ART)
    with open(site / "archive" / "albania" / "field-notes.html", "w", encoding="utf-8", newline="") as f:
        f.write(PAGE_FN)
    return site


def rd(p):
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def test_pages():
    print("\npages (scratch site)")
    tmp = tempfile.mkdtemp(prefix="afftest_")
    try:
        site = build(tmp)
        none_env = Path(tmp) / "none.env"
        none_env.write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
        ph_env = Path(tmp) / "ph.env"
        ph_env.write_text("HOSTELWORLD_AFFILIATE_ID=XXXX\nGYG_PARTNER_ID=your_partner_id\nVIATOR_PID=\nSTAY22_AID=<aid>\n", encoding="utf-8")
        ids_env = Path(tmp) / "ids.env"
        ids_env.write_text("HOSTELWORLD_AFFILIATE_ID=1011lABC\nGYG_PARTNER_ID=XYZ123\nVIATOR_PID=P00012345\nSTAY22_AID=kevinaid\n", encoding="utf-8")
        fn, art = site / "albania" / "field-notes.html", site / "armenia" / "yerevan.html"

        r = run(site, none_env)
        check("dry run writes nothing", rd(fn) == PAGE_FN and rd(art) == PAGE_ART, r.stderr)
        check("dry run reports the cleaning", "clean" in r.stdout and "albania/field-notes.html" in r.stdout, r.stdout[-400:])
        check("placeholder IDs count as none", A.load_ids(ph_env) == {}, str(A.load_ids(ph_env)))

        r = run(site, ph_env, "--apply")
        a = rd(fn)
        check("no IDs: links cleaned", "ppc_gooads" not in a and "gclid" not in a and "ranking_uuid" not in a
              and "euid" not in a, a)
        check("no IDs: nothing tagged, no sponsored, no disclosure",
              "prf.hn" not in a and "sponsored" not in a and "affiliate-note" not in a)
        check("the other markup is untouched (rel noopener noreferrer kept)", 'rel="noopener noreferrer"' in a)
        check("archive/ is never touched", rd(site / "archive" / "albania" / "field-notes.html") == PAGE_FN)
        b = rd(art)
        check("BOM and CRLF survive", b.startswith("﻿") and b.count("\r\n") == PAGE_ART.count("\r\n")
              and "\n" not in b.replace("\r\n", ""))
        clean_fn, clean_art = rd(fn), rd(art)
        r = run(site, ph_env, "--check")
        check("--check passes on a clean site", r.returncode == 0, r.stdout[-300:])

        r = run(site, ids_env, "--check")
        check("--check fails when IDs exist and links are untagged", r.returncode == 1)
        r = run(site, ids_env, "--apply")
        a, b = rd(fn), rd(art)
        check("Hostelworld tagged through Partnerize", "https://prf.hn/click/camref:1011lABC/pubref:albania_field_notes/destination:" in a)
        check("GetYourGuide tagged", "partner_id=XYZ123&amp;utm_medium=online_publisher&amp;cmp=albania_field_notes" in a, a)
        check("Airbnb left plain and not sponsored", 'href="https://www.airbnb.com/rooms/5">' in a)
        check("rel=sponsored added, existing tokens kept", 'rel="noopener noreferrer sponsored"' in a, a)
        check("a link with no rel gets sponsored noopener", 'rel="sponsored noopener"' in a)
        check("exactly one disclosure per page", a.count("affiliate-note") == 1 and b.count("affiliate-note") == 1)
        i_note, i_ul, i_nav = a.find("affiliate-note"), a.find("<ul>"), a.find("</nav>")
        check("disclosure sits right before the list holding the first tagged link, not in the nav",
              i_nav < i_note < i_ul and a[a.find("</div>", i_note) + 6:i_ul] == "\n  ", repr(a[i_note - 60:i_ul + 6]))
        check("disclosure wording", A.DISCLOSURE in a and "—" not in A.DISCLOSURE)
        check("after a heading link the disclosure gets its own line",
              "</h3>\r\n  <div class=\"article-tip affiliate-note\">" in b, repr(b))
        check("Viator: the old pid/mcid replaced by ours",
              "pid=P00999" not in b and "pid=P00012345" in b and "mcid=42383" in b, b)
        check("Booking wrapped by Stay22", "stay22.com/allez/booking?aid=kevinaid" in b)
        check("CRLF still CRLF after tagging", "\n" not in b.replace("\r\n", ""))
        tagged_fn, tagged_art = a, b

        r = run(site, ids_env, "--apply")
        check("second run is a no-op (idempotent)", rd(fn) == tagged_fn and rd(art) == tagged_art and "0 page(s) change" in r.stdout,
              r.stdout[:300])
        r = run(site, ids_env, "--check")
        check("--check passes once tagged", r.returncode == 0)

        r = run(site, none_env, "--apply")
        # the one thing that stays is the rel="noopener" a tagged link was given: harmless, and the tool
        # cannot tell it from one Kevin wrote
        un = lambda s: s.replace(' rel="noopener"', "")
        check("IDs removed -> back to the clean-only pages (tags, sponsored and disclosure gone)",
              un(rd(fn)) == clean_fn and un(rd(art)) == clean_art, rd(fn))
        clean_fn = rd(fn)
        r = run(site, none_env, "--preview-ids", "--apply")
        check("--preview-ids refuses to write", r.returncode != 0 and rd(fn) == clean_fn)
        rep = Path(tmp) / "rep.md"
        r = run(site, none_env, "--preview-ids", "--report", str(rep))
        check("preview report shows tags + the disclosure without writing",
              rep.exists() and "**tag**" in rep.read_text(encoding="utf-8") and "disclosure" in rep.read_text(encoding="utf-8")
              and rd(fn) == clean_fn)

        # the article's own "Last updated ... Contains affiliate links" line is the disclosure: a page that
        # carries it gets no tip box, and an old one comes off (Kevin, 2026-11-08). Pages without it are unchanged.
        run(site, ids_env, "--apply")
        check("tagged again: both pages carry the tip", rd(fn).count("affiliate-note") == 1 and rd(art).count("affiliate-note") == 1)
        line = ('<div class="measure gg-updated">Last updated: November 2026 · Based on firsthand experience'
                ' · Contains affiliate links</div>\n')
        lined = rd(fn).replace("</main>\n", "</main>\n" + line)
        with open(fn, "w", encoding="utf-8", newline="") as f:
            f.write(lined)
        r = run(site, ids_env, "--check")
        check("--check flags the tip a lined page no longer needs", r.returncode == 1 and "disclosure-removed" in r.stdout, r.stdout[-300:])
        r = run(site, ids_env, "--apply")
        a, b = rd(fn), rd(art)
        check("a page with the line loses its tip box, keeps its tags and the line",
              "affiliate-note" not in a and "prf.hn/click/camref:1011lABC" in a and line in a, a)
        check("a page without the line keeps its tip box", b.count("affiliate-note") == 1)
        r = run(site, ids_env, "--check")
        check("--check passes once the tip is gone (idempotent)", r.returncode == 0, r.stdout[-300:])
        check("a Last-updated line without the affiliate wording is not a disclosure",
              not A.has_line('<div class="measure gg-updated">Last updated: May 2026 · Based on firsthand experience</div>')
              and A.has_line(line))
        check("either wording counts: 'Contains affiliate link' / 'Some links are affiliate links', any case",
              A.has_line('<div class="measure gg-updated">Last updated: May 2026 · Contains Affiliate Link</div>')
              and A.has_line('<div class="measure gg-updated">Last updated: May 2026 · Some links are affiliate links</div>'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_junk():
    print("\njunk_in (the lint)")
    check("an ad-click link has junk", "gclid" in A.junk_in("https://www.booking.com/hotel/am/x.html?aid=1&amp;gclid=2"))
    check("a clean link has none", A.junk_in("https://www.booking.com/hotel/am/x.html") == [])
    check("a non-booking link with no ad tags is never judged", A.junk_in("https://en.wikipedia.org/wiki/X?oldid=5") == [])
    check("any outbound link that came from an ad is judged (Roma Pass, 2026-11-07)",
          "gclid" in A.junk_in("https://roma-pass.com/athens-sightseeing-passes/?gad_source=1&amp;gclid=Cj0K"))
    check("an ad-tagged tour operator is cleaned to its page",
          A.clean_url("https://meteora.com/tour/2-days/?utm_source=google&utm_medium=cpc&utm_campaign={campaign}")
          == "https://meteora.com/tour/2-days/")
    check("Google's own links are never judged", A.junk_in("https://www.google.com/maps/place/x?utm_source=y") == [])
    check("a tagged link is not junk", A.junk_in("https://www.getyourguide.com/x-t1/?partner_id=XYZ&utm_medium=online_publisher") == [])


if __name__ == "__main__":
    print("=" * 70)
    test_clean()
    test_tag()
    test_junk()
    test_pages()
    print("=" * 70)
    print("  %d checks, %d failed" % (passed + failed, failed))
    print("=" * 70)
    sys.exit(1 if failed else 0)
