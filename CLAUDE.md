# Agent Instructions

You're working inside the **WAT framework** (Workflows, Agents, Tools). This architecture separates concerns so that probabilistic AI handles reasoning while deterministic code handles execution. That separation is what makes this system reliable.

## The WAT Architecture

**Layer 1: Workflows (The Instructions)**
- Markdown SOPs stored in `workflows/`
- Each workflow defines the objective, required inputs, which tools to use, expected outputs, and how to handle edge cases
- Written in plain language, the same way you'd brief someone on your team

**Layer 2: Agents (The Decision-Maker)**
- This is your role. You're responsible for intelligent coordination.
- Read the relevant workflow, run tools in the correct sequence, handle failures gracefully, and ask clarifying questions when needed
- You connect intent to execution without trying to do everything yourself
- Example: If you need to pull data from a website, don't attempt it directly. Read `workflows/scrape_website.md`, figure out the required inputs, then execute `tools/scrape_single_site.py`

**Layer 3: Tools (The Execution)**
- Python scripts in `tools/` that do the actual work
- API calls, data transformations, file operations, database queries
- Credentials and API keys are stored in `.env`
- These scripts are consistent, testable, and fast

**Why this matters:** When AI tries to handle every step directly, accuracy drops fast. If each step is 90% accurate, you're down to 59% success after just five steps. By offloading execution to deterministic scripts, you stay focused on orchestration and decision-making where you excel.

## How to Operate

**1. Look for existing tools first**
Before building anything new, check `tools/` based on what your workflow requires. Only create new scripts when nothing exists for that task.

**2. Learn and adapt when things fail**
When you hit an error:
- Read the full error message and trace
- Fix the script and retest (if it uses paid API calls or credits, check with me before running again)
- Document what you learned in the workflow (rate limits, timing quirks, unexpected behavior)
- Example: You get rate-limited on an API, so you dig into the docs, discover a batch endpoint, refactor the tool to use it, verify it works, then update the workflow so this never happens again

**3. Keep workflows current**
Workflows should evolve as you learn. When you find better methods, discover constraints, or encounter recurring issues, update the workflow. That said, don't create or overwrite workflows without asking unless I explicitly tell you to. These are your instructions and need to be preserved and refined, not tossed after one use.

## The Self-Improvement Loop

Every failure is a chance to make the system stronger:
1. Identify what broke
2. Fix the tool
3. Verify the fix works
4. Update the workflow with the new approach
5. Move on with a more robust system

This loop is how the framework improves over time.

## File Structure

**What goes where:**
- **Deliverables**: Final outputs go to cloud services (Google Sheets, Slides, etc.) where I can access them directly
- **Intermediates**: Temporary processing files that can be regenerated

**Directory layout:**
```
.tmp/           # Temporary files (scraped data, intermediate exports). Regenerated as needed.
tools/          # Python scripts for deterministic execution
workflows/      # Markdown SOPs defining what to do and how
.env            # API keys and environment variables (NEVER store secrets anywhere else)
credentials.json, token.json  # Google OAuth (gitignored)
```

**Core principle:** Local files are just for processing. Anything I need to see or use lives in cloud services. Everything in `.tmp/` is disposable.

## Image System — Responsive, WebP on Both Tiers

Every article body image is served from `Images/web/` at two tiers, each with a 1x/2x/3x
`srcset` and a WebP alongside the JPEG. **The full-resolution original under `Images/` is
never served to a visitor** — it is the archive the variants are built from.

| Tier | Files | Serves |
|------|-------|--------|
| Desktop | `Images/web/<Country>/…-1x/-2x/-3x.{webp,jpg}` | ≥769px |
| Mobile | `Images/web/<Country>/…-mob-1x/-2x/-3x.{webp,jpg}` | <769px |

```html
<picture>
  <source type="image/webp" media="(min-width:769px)" srcset="…-1x.webp 576w, …-3x.webp 1728w" sizes="576px">
  <source                   media="(min-width:769px)" srcset="…-1x.jpg  576w, …-3x.jpg  1728w" sizes="576px">
  <source type="image/webp" srcset="…-mob-1x.webp 393w, …-mob-3x.webp 1179w" sizes="393px">
  <img src="…-mob-2x.jpg" srcset="…-mob-1x.jpg 393w, …-mob-3x.jpg 1179w" sizes="393px">
</picture>
```

The `<img src>` is only the fallback for clients that ignore `srcset`; point it at
`-mob-2x.jpg`, **never at the original** (some are 13MB — that used to be the fallback).

**Whenever new photos are added to an article page, you must:**
1. `python tools/recompress_desktop.py` — desktop variants into `Images/web/`
2. `python tools/add_picture_mobile.py` — wrap bare `<img>` tags in `<picture>`
3. `python tools/gen_mobile_webp.py` — mobile WebP + fixes the `src` fallback
4. `python tools/fix_img_perf.py` — `loading` + intrinsic `width`/`height` (layout shift)
5. `python tools/fix_case.py` — case-sensitivity for GitHub Pages (Linux)

Run them in that order any time photos are added or changed.

**ALWAYS carry the ICC profile through.** These photos are **Display P3**. Pillow's
`.convert("RGB")` silently drops the profile, and a browser then reads P3 pixel values as
sRGB, which renders them **visibly desaturated — the photos look grey**. Every tool that
writes an image must pass it on:

```python
src = ImageOps.exif_transpose(Image.open(path))
icc = src.info.get("icc_profile")          # grab BEFORE convert()
src.convert("RGB").save(dst, quality=84, icc_profile=icc)
```

This is the same class of bug as the System.Drawing one — see [[feedback_image_files]].

**No third-party image hosts.** Nav flags are local (`Images/web/flags/`, via
`tools/localize_flags.py`); they used to come from flagcdn.com, which put an external
origin in the critical path of the nav on all 45 pages.

## Visual QA — Screenshot Rule

**After every change to HTML or CSS, you must:**
1. Run `python tools/screenshot.py <affected-page>.html` to capture desktop, tablet, and mobile screenshots
2. Read all three images and inspect them for layout issues, including:
   - Layout structure (spacing, alignment, responsive breakpoints)
   - Image quality — check that photos look sharp, not blurry or grainy. Hero/cover photos must use originals; article body images use `Images/web/` compressed versions
3. Fix any problems found, then re-screenshot to confirm
4. Only report the task as done once all three platforms look correct

Use `--all` only when changes affect site-wide styles (e.g. styles.css). For page-specific changes, screenshot just the affected page(s).

## Writing Rule — American English Only

I'm American, so everything published under my name has to read in my voice. **Always use
American spellings, never British ones.** This applies to all prose: drafts, live articles,
page copy, meta descriptions.

| Use | Not |
|---|---|
| color, harbor, favorite, neighbor, flavor | colour, harbour, favourite, neighbour, flavour |
| center, theater, meter, liter | centre, theatre, metre, litre |
| traveling, traveled, traveler, canceled | travelling, travelled, traveller, cancelled |
| organized, realize, customization, prioritizing | organised, realise, customisation, prioritising |
| gray, story (a building floor), catalog, specialty | grey, storey, catalogue, speciality |
| while, among, skeptical, program | whilst, amongst, sceptical, programme |

Drafts assembled from mixed sources pick these up constantly, so sweep before publishing:
`python tools/americanize.py --dry-run` (then without the flag to apply).

**The one exception is proper names** — never "correct" the spelling of a real place or
business: *Viaduct Harbour*, *Lady Janes Ice Cream Parlour*, *Centre Pompidou*, *Sydney Harbour
Bridge*. The tool protects these (a capitalized British word preceded by another capitalized
word is treated as part of a name) and skips anything inside a tag, so `href`/`alt` text and
embedded map code are never rewritten.

Related: see the no-em-dashes rule in my writing style — both exist so the writing sounds like me.

## Bottom Line

You sit between what I want (workflows) and what actually gets done (tools). Your job is to read instructions, make smart decisions, call the right tools, recover from errors, and keep improving the system as you go.

Stay pragmatic. Stay reliable. Keep learning.
