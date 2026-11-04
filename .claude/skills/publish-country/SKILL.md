---
name: publish-country
description: "Rules for launching or publishing a country or guide page on getawayguide: SEO metadata (title/og:title 60 chars, h1, description under 160, canonical, og:type, JSON-LD dates), dateModified from prose commits, guide cards and Explore More cards. Use when running publish_country.py or retire_field_notes.py, launching a country, or editing page titles and meta."
---

# Publishing a Country — SEO Metadata Is Part of the Job

**A country is not published until its SEO metadata is in place.** This is a step of the
publish, not a follow-up task, and it is wired into `tools/publish_country.py` so it runs
automatically. Belgium and Germany both went live without it because it used to be manual.

Every published page must have:

| | |
|---|---|
| `<title>` and `og:title` | `<Country> Travel Guide: City, City & City`, **60 chars or fewer** |
| `<h1>` | `<Country> Travel Guide` (not the bare country name) |
| meta + `og:description` | **under 160 chars** |
| `rel="canonical"` | from the page's own `og:url` |
| `og:type` | `article` (About/Privacy/index stay `website`) |
| JSON-LD | `datePublished` + `dateModified`, and `headline` matching the `<title>` |

**Exception: upgraded (Armenia-template) field-notes pages** (Kevin, 2026-11-02). Their title is
`<Country> Field Notes - Quick Reference Guides While Traveling`, or `<Country> Field Notes - Quick
Guides While Traveling` when the first runs over 60 (El Salvador), and their `<h1>` is `<Country> Field
Notes`. The checkers read the page's own `<title>` to tell which kind it is (tools/audit.py
check_seo, tools/prelaunch.py), so never "fix" these to Travel Guide, and don't run seo_apply on them.

Otherwise, never target "Field Notes" in a title. It's brand, and nobody searches it. Lead with the
keyword, then spend the rest of the 60 characters on real place names from the page.
The 60 is the whole `<title>`, as the browser tab and Google show it (the editor's Search tab
counts to 60), so a ` - getawayguide` ending counts against it: drop the ending when it would
push a title over, never the place names. (The cap was ~70 from 2026-09-21 to 2026-10-02, when
every title went back under 60.)

**Related-article blocks are part of the launch too** (2026-10-23): `tools/guide_cards.py` puts the
guide card under a field-notes city heading whose guide is live, and `tools/explore_more.py` picks the
three "Explore More" cards at the end of every live page (Kevin's rules; his pins in
`tools/explore_pins.json` are kept). Both run from `retire_field_notes.py` and `publish_country.py`;
never add those cards by hand.

`dateModified` comes from the last commit that changed the page's **prose**, not `git log -1`
— sitewide asset passes touch every page at once, and publishing that everywhere is untrue
and reads as a bulk refresh. A page published today has no commits yet, so it dates as today.

The generators live in `.tmp/` (`seo_content_dates.py`, `seo_proposals.py`,
`seo_descriptions.py`) and feed `tools/seo_apply.py` + `tools/seo_meta.py`. They read
`git ls-files`, so the new page has to be staged before they can see it — the publish tool
does that for you. The run asserts canonical, og:type and dates are present and logs
`SEO metadata: complete`. If it says anything else, the country is not finished.
