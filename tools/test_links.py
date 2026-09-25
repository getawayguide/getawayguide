#!/usr/bin/env python3
"""Pin down what fix_links.py is allowed to rewrite.

This classifier is the only thing standing between a redirect and an automatic
edit to Kevin's published articles, so the cases below are not invented: every
one is a real URL from the 2026-09-25 scan of the live site, with the status and
final address the site actually returned. If a change to the rules moves one of
these, it moves something that shipped.

The two directions matter differently. A missed fix costs a redirect hop. A
WRONG fix silently retargets a recommendation, and nobody finds out, because the
new link returns 200. So the cases that must stay un-rewritten carry most of the
weight here.

  python tools/test_links.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fix_links as fl

FIX = ("canonical", "moved")            # the two kinds that get written

# (why, url, status, final, expected kind)
CASES = [
    # ---- may be rewritten: the destination proves it is the same page --------
    ("slug survives a reorganisation",
     "https://www.budget-georgia.com/chalaadi-glacier-tour.html", 200,
     "https://www.budget-georgia.com/tour/chalaadi-glacier-tour/", "moved"),
    ("numeric id survives a rename",
     "https://www.hostelworld.com/hostels/p/275702/hindustan-by-backpackers-heaven/", 200,
     "https://www.hostelworld.com/hostels/p/275702/backpackers-heaven-free-airport-pick-up/",
     "moved"),
    ("tour id survives a spelling correction (sakkara -> saqqara)",
     "https://www.getyourguide.com/en-gb/cairo-l92/giza-pyramids-sphinx-and-sakkara-private-full-day-tour-t11731/",
     200,
     "https://www.getyourguide.com/en-gb/cairo-l92/giza-pyramids-sphinx-and-saqqara-private-full-day-tour-t11731/",
     "moved"),
    ("place id survives a rename (prison-island -> changuu-island)",
     "https://www.getyourguide.com/en-gb/prison-island-l97419/?date_from=2026-07-25", 200,
     "https://www.getyourguide.com/en-gb/changuu-island-l97419/", "moved"),
    ("a locale infix is not part of the page's identity",
     "https://www.booking.com/hotel/tr/terra-cave.en.html?aid=311088&gclid=x", 200,
     "https://www.booking.com/hotel/tr/terra-cave.html", "moved"),
    ("the destination drops its own tracking, path intact",
     "https://www.booking.com/hotel/ge/latour-mestia.html?label=junk&aid=311088", 200,
     "https://www.booking.com/hotel/ge/latour-mestia.html", "canonical"),

    # ---- must NEVER be rewritten -------------------------------------------
    ("soft 404: the tour is gone, we land on the city page. Shares id l32202 "
     "with its destination, so an id test running first would call this a move",
     "https://www.getyourguide.com/en-gb/kotor-l32202/kotor-blue-cave-and-boka-bay-tour-pickup-t477207/",
     200, "https://www.getyourguide.com/en-gb/kotor-l32202/", "soft-404"),
    ("soft 404: the hostel is gone, we land on a list of Lima hostels",
     "https://www.hostelworld.com/hostels/p/313437/rainbow-hostel/", 200,
     "https://www.hostelworld.com/hostels/south-america/peru/lima/", "soft-404"),
    ("soft 404 by marker: google answers a deep link with /unsupported and a 200",
     "https://www.google.com/travel/flights?gl=US&hl=en-US", 200,
     "https://www.google.com/travel/flights/unsupported?gl=US", "soft-404"),
    ("the query lost the one parameter naming the hotel, and both paths are "
     "/searchresults.html, so the paths match while the meaning does not",
     "https://www.booking.com/searchresults.html?label=arinna&highlighted_hotels=6033046",
     200, "https://www.booking.com/searchresults.html?dest_id=-750642;dest_type=city",
     "unclear"),
    ("a redirect to a different domain is a judgement call",
     "https://cosituc.gob.pe/#", 200, "http://cosituccusco.pe/tarifario/", "unclear"),
    ("a real 404", "https://www.guruwalk.com/puebla", 404,
     "https://www.guruwalk.com/puebla", "gone"),
    ("a timeout is NOT a 404: goindigo is up in a browser",
     "https://www.goindigo.in/", 0, "https://www.goindigo.in/", "unreachable"),
    ("403 is a bot defence, not link rot",
     "https://www.alltrails.com/trail/turkey/mugla/lycian-way-fethiye-to-kayakoy", 403,
     "https://www.alltrails.com/trail/turkey/mugla/lycian-way-fethiye-to-kayakoy", "blocked"),
    ("the article pins a booking date from an earlier trip",
     "https://www.hostelworld.com/pwa/hosteldetails.php/Kantar/Yerevan/88646?from=2026-06-21&to=2026-06-22",
     200,
     "https://www.hostelworld.com/pwa/hosteldetails.php/Kantar/Yerevan/88646?from=2026-09-26&to=2026-09-27",
     "stale-date"),

    # ---- benign, must stay silent ------------------------------------------
    ("a bare domain redirecting to its own locale is how the web works",
     "https://www.prioritypass.com/", 200, "https://www.prioritypass.com/en-GB/", "ok"),
    ("adding www to a bare domain is not a finding",
     "https://booking.com", 200, "https://www.booking.com/", "ok"),
    ("unchanged", "https://sallysees.com/santa-ana-volcano-hike/", 200,
     "https://sallysees.com/santa-ana-volcano-hike/", "ok"),
]


def main():
    passed = failed = 0
    for why, url, status, final, want in CASES:
        got, detail = fl.classify(url, status, final, "")
        ok = got == want
        # the assertion that actually protects the articles
        if want not in FIX and got in FIX:
            ok = False
            why += "  <-- WOULD HAVE BEEN REWRITTEN"
        print("  %s %-9s %s" % ("PASS" if ok else "FAIL", got, why[:96]))
        if not ok:
            print("         wanted %r, got %r for %s" % (want, got, url[:90]))
        passed, failed = passed + ok, failed + (not ok)

    # nothing outside the two writable kinds may ever reach the rewriter
    writable = {k for _, _, _, _, k in CASES if k in FIX}
    print("\n  writable kinds seen: %s" % ", ".join(sorted(writable)))
    print("=" * 70)
    print("  %d checks, %d failed" % (passed + failed, failed))
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
