# Offload to Gemini

**Objective:** save Claude tokens by sending three bulky, low-judgment jobs to Google's free Gemini API, so
only Gemini's short answer enters Claude's context. (Kevin, 2026-10-31: "lets just do the 3 free gemini jobs,
keep the other on claude".)

**Tool:** `tools/ask_gemini.py` (CLI, and `generate()` for other tools). Key: `GEMINI_API_KEY` in `.env`.
Never print, copy or log the key, and never put Kevin's email or other personal details in a request.

## The one rule that makes it save anything

Pass Gemini the **file path** and read only its answer. **Never read the file first.** If Claude reads the
page or log and then calls Gemini, the tokens are already spent and nothing is saved. Every full response is
also saved to `.tmp/gemini/<stamp>-<preset>.json`; open it only if the printed lines are cut off and you need
the rest.

## What goes to Gemini

| Job | Command | Notes |
|---|---|---|
| Error scan of a LIVE field-notes page | `python tools/ask_gemini.py --file <country>/field-notes.html --preset live-scan` | typos, grammar (it's/its), British spellings, contradicting facts, a Maps link that points at a clearly different place, real ad-tracking params |
| Alt text for a photo | automatic in the editor and `python tools/alt_writer.py <photo> --place ... --city ... --country ... --section ...` (or `--page <html>`) | alt_writer tries Gemini first, then the warm Claude session |
| One-off alt text | `python tools/ask_gemini.py --image <Images/web/...jpg> --preset alt --context "<place>, <city>, <country>"` | JPEG/WebP tiers only, never a HEIC original; no trailing period |
| Digest of a long log or a public transcript | `python tools/ask_gemini.py --file <log or transcript> --preset digest --max-lines 15` | e.g. a server log, a QA log, a YouTube transcript from `tools/youtube_transcript.py` |

## What never goes to Gemini

The free tier may use prompts to improve Google's models, so treat every request as public.

- `.env`, `credentials.json`, `token.json`, or anything else holding keys (the tool refuses these paths)
- Kevin's travel journal (`Travel Journals`, refused by the tool)
- Unpublished drafts (`Drafts/`), unless Kevin says so for a specific file
- Private details about people (names with personal situations, contact details, messages)
- Voice-sensitive writing and review: drafting or rewriting articles, review rounds, anything published in
  Kevin's voice. That stays on Claude (see the writing style guide).
- Fact lookups: they need Google Search grounding, which has **no free-tier quota** (see limits). They stay
  on Claude. `--search` is kept in the tool for a paid key, but on the free tier it just exits 75.

## Live-scan: verify before you edit

Gemini's lines are leads, not edits. **Before changing a live page, Claude must check every line** against the
page itself (a targeted `grep` for the quoted phrase, not a full read) and drop anything that is not a real
error. Things it has gotten wrong:

- It used to call every Google Maps link's `hl=`, `entry=`, `g_ep=`, `data=` "tracking parameters". Those are
  normal. Only `gclid`, `gbraid`, `wbraid`, `fbclid`, `utm_*`, `campaign_id`, `ranking_uuid` and `source=ppc`
  are tracking. The preset now says so (2026-10-31).
- It offered tense and style rewrites. The preset forbids those; ignore any that slip through.
- The scan varies a little run to run (on Latvia one run found both "it's" errors, the next only one). A clean
  result does not prove a page is clean.
- Proper names keep British spelling (*Viaduct Harbour*, *Centre Pompidou*); never "fix" those.
- Em dashes in body prose are checked by `tools/prose_check.py`, not by this scan.

## When Gemini can't answer: exit 75

If the quota is used up, the model name is wrong, the key is missing, a feature isn't in the free tier, or
there is no answer in time, the tool prints `GEMINI_UNAVAILABLE (HTTP <code>): <reason>` and exits with code
**75**. Then do the job with Claude the old way. Nothing is lost; that job just costs Claude tokens again.
`alt_writer.py` does this by itself: if Gemini fails or takes more than 15 s, the warm Claude session writes
the alt text (`ALT_GEMINI=0` skips Gemini entirely).

## Free-tier limits (measured 2026-10-31)

- **Model:** the default is `gemini-3.1-flash-lite`, which answers in about 2 to 9 s. `gemini-flash-latest` and
  the other Lite aliases queue for 30 to 77 s on the free tier, so don't use them. Override with `--model` or
  `GEMINI_MODEL` if that changes.
- **No Google Search:** grounding has zero free quota (exit 75).
- **Rate limits:** a per-minute 429 with a `retryDelay` is waited out and retried automatically (up to 3
  retries); a used-up daily quota exits 75 at once.
- **Timings seen:** live-scan of a field-notes page (~10k tokens) 2 to 4 s; digest of a 3.6 KB file 4.6 s and
  of a 238 KB log (162k tokens) 5.5 s; alt text about 5 s per photo end to end.

## Restart note

The photo server (:5003) imports `alt_writer` once, the first time a photo needs alt text. A running server
keeps the old Claude-only code until it restarts; never restart it while jobs may be running, ask Kevin.
