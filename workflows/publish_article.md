# Workflow: take a country from journal to published

**Objective.** Turn a trip journal into a published country (itinerary, top-10,
city guides, index) while spending Kevin's attention on writing rather than on
mechanics.

**This is not the field-notes workflow.** A field-notes page is one page of
lightly-dressed journal and can ship the same day. A country like Armenia is
seven pages, a photo pipeline, route and city maps, cross-page dedupe, and
several rounds of Kevin rewriting and commenting. It is a bigger job and it
should take longer. The goal is not to make it a two-day job.

## Why this file exists

The Armenia itinerary was drafted in about four hours on 2026-09-11, between
14:02 and 18:04: journal extract, field notes, outline, full draft, voice pass.
It shipped eleven days later.

Those eleven days were roughly twenty narrow revision passes, each discovered
one at a time, each leaving a hand-made backup (`.tmp/armenia-itinerary-before-*.html`):

    voice, unslop, intro, image paths, cascade, highlights, de-template, hero,
    day 6-7, table of contents, overlap, prose, gyumri link, slimming, images, map

Nearly all of them are mechanical and recur on **every** article. None of them
were hard. They were slow because they arrived one per sitting instead of as a
list, and because several of them changed structure *after* the prose had
already been polished, which meant polishing it again.

**Kevin's editing rounds are not the waste.** He will rewrite sections and
leave comments, and that is what makes these publishable rather than generated.
Expect several rounds and design for them.

The waste is everything that makes a round *cost a day*: a mechanical problem
that should never have reached him, a structural change that invalidates prose
he already fixed, and a turnaround that takes a sitting because nothing was
batched. This file fixes the order so his rounds are about the writing and
arrive back quickly.

## The rule that saves the most time

**Lock structure before touching prose.**

Armenia polished voice on day 1, then changed the hero, the highlights, the
template and the spacing on day 4, rewrote days 6-7 on day 7, and slimmed the
whole thing on day 11. Every one of those invalidated prose that had already
been worked. Prose is the *last* thing that gets attention, because everything
above it moves the words around.

## Stage 0 — source material

    python tools/youtube_transcript.py <url>     # if there is video
    .tmp/journal-<country>.txt                   # extract from Travel Journals.html

The journals live at `~/Documents/Travel Journals/Travel Journals.html`, one
`<div class="entry">` per day with `<div class="when">Mon 18 Dec 2017</div>`.
The round-the-world year is day-numbered AND dated (`Day 1 · Tue 23 May 2023`).

Output: an outline and a field-notes text file. Do not write prose yet.

## Stage 1 — draft

Template is `ruta-de-las-flores.html` (see the new-article memory). Draft every
page of the country in one sitting: itinerary, top-10, each city, index.

Drafting them together is what makes Stage 2 cheap. It also surfaces overlap
between pages while it is still easy to cut.

## Stage 2 — structure (lock it here)

Everything in this stage moves words around, so all of it happens before prose.

- [ ] Hero chosen (`python tools/hero_picker.py`)
- [ ] Section order and day grouping final
- [ ] Highlights block present and correct
- [ ] De-templated: no two pages share a skeleton sentence
- [ ] Table of contents correct (this broke once sitewide, commit 5115557)
- [ ] Overlap between the country's own pages cut
- [ ] Length settled. Slimming AFTER polishing is pure waste.

Only when this list is done does anything below it happen.

## Stage 3 — mechanics (no judgment calls, no round trip)

Photos, in this exact order (CLAUDE.md, "Image System"):

    python tools/recompress_desktop.py
    python tools/add_picture_mobile.py
    python tools/gen_mobile_webp.py
    python tools/fix_img_perf.py
    python tools/fix_case.py

Then:

- [ ] Maps: `tools/itinerary_map.py` + `tools/embed_itinerary_map.py`, city maps
      via `tools/city_map.py`
- [ ] Place links resolved: `python tools/resolve_draft_maps.py`
- [ ] Spacing rhythm (one standard for field notes, guides, itineraries,
      enforced in artifact.css, commit 0443179)
- [ ] `python tools/strip_paste_artifacts.py` if anything came from Google Docs

**These need no human.** Do not queue them behind a review round.

## Stage 4 — prose

    python tools/americanize.py --dry-run     # then without the flag
    python tools/lint_prose.py --drafts
    python tools/normalize_quotes.py

Then the voice pass and the de-slop pass, against the writing-style memory.
This is the first point at which the words are worth real attention, because
nothing above will move them again.

## Stage 5 — one review round

    python tools/redline.py open <slug>

Kevin reviews **voice and substance only**. Anything mechanical that reaches
this stage is a failure of Stage 3, not a review item.

Answer his comments as thread replies plus a new proposal round. Never edit
directly, never answer in chat (see the review-workflow memory).

## Stage 6 — publish

    python tools/publish_country.py <slug> --name "Name" --iso2 xx --continent europe
    python tools/lint_site.py
    python tools/screenshot.py <page>.html      # read all three widths
    python tools/gen_sitemap.py
    python tools/gen_search_index.py

`publish_country.py` does SEO metadata itself and asserts
`SEO metadata: complete`. If it says anything else the country is not finished.

Do not push until Kevin asks.

## Drafts are version-controlled now

`Drafts/` is its own **local-only** git repo (initialized 2026-09-22). Use
`git diff` instead of writing `.tmp/<slug>-before-<thing>.html` by hand, and
commit after each stage above.

It has **no remote and a pre-push hook that refuses**, deliberately: the parent
repo is public, and neither `robots.txt` nor `_config.yml` excludes `Drafts/`,
so anything committed there would be readable on github.com and served on
getawayguide.io. Keep unpublished writing out of it.

## What good looks like

Not a deadline. Two things to watch instead:

1. **Nothing mechanical reaches Kevin.** If a review comment is about an image
   path, spacing, a broken anchor or a typo, Stage 3 or 4 failed. Add the check
   there rather than just fixing the instance, or it recurs next country.
2. **A round trips back fast.** His comments should come back answered in the
   same sitting where possible, not the next day. Batch the mechanical work so
   it is never what he is waiting on.

Rounds of real editing are expected and are not a problem to solve. A country
taking a week because Kevin rewrote three sections is fine. A country taking a
week because image paths were discovered on day four is not.
