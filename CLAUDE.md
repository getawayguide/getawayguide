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

## Image System — Two Versions Required

Every article body image must always have two versions:

| Version | Path | Dimensions | Quality | Loads on |
|---------|------|------------|---------|----------|
| Desktop compressed | `Images/web/El Salvador/...` | Portrait 600×900 · Landscape 1200×800 | 85 | Desktop (≥769px) |
| Mobile original | `Images/El Salvador/...` | Full resolution original | Original | Mobile (<769px) |

This is implemented via `<picture>` elements in the article HTML:
```html
<picture>
  <source media="(min-width:769px)" srcset="../Images/web/El Salvador/X/Y.jpg">
  <img src="../Images/El Salvador/X/Y.ORIG_EXT" style="position:absolute;...">
</picture>
```

**Hero/cover photos** always use the full-resolution original on all devices — no compression.

**Whenever new photos are added to an article page, you must:**
1. Run `python tools/recompress_desktop.py` — generates/updates the `Images/web/` compressed version
2. Run `python tools/add_picture_mobile.py` — wraps any unwrapped `<img>` tags in `<picture>` elements
3. Run `python tools/fix_case.py` — fixes case-sensitivity for GitHub Pages (Linux)

Run all three scripts in that order any time photos are added or changed.

## Visual QA — Screenshot Rule

**After every change to HTML or CSS, you must:**
1. Run `python tools/screenshot.py <affected-page>.html` to capture desktop, tablet, and mobile screenshots
2. Read all three images and inspect them for layout issues, including:
   - Layout structure (spacing, alignment, responsive breakpoints)
   - Image quality — check that photos look sharp, not blurry or grainy. Hero/cover photos must use originals; article body images use `Images/web/` compressed versions
3. Fix any problems found, then re-screenshot to confirm
4. Only report the task as done once all three platforms look correct

Use `--all` only when changes affect site-wide styles (e.g. styles.css). For page-specific changes, screenshot just the affected page(s).

## Bottom Line

You sit between what I want (workflows) and what actually gets done (tools). Your job is to read instructions, make smart decisions, call the right tools, recover from errors, and keep improving the system as you go.

Stay pragmatic. Stay reliable. Keep learning.
