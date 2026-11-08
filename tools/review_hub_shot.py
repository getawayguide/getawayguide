"""Screenshots for the Review Hub artifact (see .tmp/review_hub.md).

Renders a change IN ITS ARTICLE, the section around it rather than the element alone, at
1440 px (desktop) and 393 px (phone), device_scale_factor 1, against the photo server
(http://127.0.0.1:5003/site/<repo path>). Writes JPEGs to .tmp/review_hub/shots/:
    <id>-desktop.jpg   <id>-phone.jpg

Modes
    figure   the element matching --selector (nth match --nth), plus ~260 px of lead-in above
             and ~140 px below, so its heading and the paragraph before it show
    top      the top of the article (hero, title, first screen)
    intro    from the hero's bottom edge down: the intro and the contents box
    page     the whole page, capped at --max px tall (standalone previews)

Usage
    python tools/review_hub_shot.py <id> "<repo path>" --mode figure --selector "figure[data-map=belgrade-routes]"
    python tools/review_hub_shot.py <id> "<repo path>" --mode top
    python tools/review_hub_shot.py --batch items.json      # [{"id","path","mode","selector","nth"}, ...]

Read-only: it only loads pages. Always 127.0.0.1 (localhost pays a ~2 s IPv6 timeout).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".tmp" / "review_hub" / "shots"
BASE = "http://127.0.0.1:5003/site/"
VIEWPORTS = {"desktop": (1440, 1000), "phone": (393, 852)}

PREP = """() => {
  document.documentElement.style.overflowX = 'visible';
  document.body.style.overflowX = 'visible';
  document.querySelectorAll('*').forEach(el => {
    const s = el.style;
    if (s.opacity === '0' && s.transform && s.transform.includes('translateY')) { s.opacity = '1'; s.transform = 'none'; }
  });
  document.querySelectorAll('img[loading=lazy]').forEach(i => i.loading = 'eager');
}"""

WAIT_IMAGES = """async () => {
  const imgs = [...document.images].filter(i => !i.complete);
  await Promise.race([Promise.all(imgs.map(i => new Promise(r => { i.onload = i.onerror = r; }))),
                      new Promise(r => setTimeout(r, 8000))]);
}"""


async def shoot_one(browser, item):
    rid, path, mode = item["id"], item["path"], item.get("mode", "top")
    sel, nth, cap = item.get("selector"), int(item.get("nth", 0)), int(item.get("max", 2600))
    url = BASE + quote(path, safe="/")
    OUT.mkdir(parents=True, exist_ok=True)
    done = {}
    for vp, (w, h) in VIEWPORTS.items():
        ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1200)
        await page.evaluate(PREP)
        full_h = await page.evaluate("document.documentElement.scrollHeight")
        if mode == "figure":
            loc = page.locator(sel).nth(nth)
            await loc.scroll_into_view_if_needed(timeout=10000)
            await page.wait_for_timeout(900)
            await page.evaluate(WAIT_IMAGES)
            box = await page.evaluate(
                "([s, n]) => { const r = document.querySelectorAll(s)[n].getBoundingClientRect();"
                " return {top: r.top + scrollY, bottom: r.bottom + scrollY}; }", [sel, nth])
            top = max(0, box["top"] - 260)
            bottom = min(full_h, box["bottom"] + 140)
        elif mode == "intro":
            hb = await page.evaluate(
                "() => { const h = document.querySelector('.article-hero, .gg-banner, header.hero');"
                " return h ? h.getBoundingClientRect().bottom + scrollY : 0; }")
            top = max(0, hb - 40)
            bottom = min(full_h, top + (1300 if vp == "desktop" else 1700))
        elif mode == "page":
            top, bottom = 0, min(full_h, cap)
        else:  # top
            top, bottom = 0, min(full_h, h)
        bottom = min(bottom, top + cap)
        # scroll through the clip so lazy images and backgrounds inside it load
        for y in range(int(top), int(bottom), max(200, h // 2)):
            await page.evaluate(f"window.scrollTo(0, {y})")
            await page.wait_for_timeout(120)
        await page.evaluate(WAIT_IMAGES)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)
        dst = OUT / f"{rid}-{vp}.jpg"
        await page.screenshot(path=str(dst), type="jpeg", quality=86, full_page=True,
                              clip={"x": 0, "y": top, "width": w, "height": max(1, bottom - top)})
        done[vp] = str(dst.relative_to(ROOT)).replace("\\", "/")
        await ctx.close()
    return done


async def run(items):
    from playwright.async_api import async_playwright
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for it in items:
            try:
                results[it["id"]] = await shoot_one(browser, it)
                print("ok  ", it["id"], flush=True)
            except Exception as e:
                results[it["id"]] = {"error": str(e)[:300]}
                print("FAIL", it["id"], str(e)[:200], flush=True)
        await browser.close()
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("id", nargs="?")
    ap.add_argument("path", nargs="?")
    ap.add_argument("--mode", default="top", choices=["figure", "top", "intro", "page"])
    ap.add_argument("--selector")
    ap.add_argument("--nth", type=int, default=0)
    ap.add_argument("--max", type=int, default=2600)
    ap.add_argument("--batch")
    a = ap.parse_args()
    if a.batch:
        items = json.loads(Path(a.batch).read_text(encoding="utf-8"))
    elif a.id and a.path:
        if a.mode == "figure" and not a.selector:
            ap.error("--mode figure needs --selector")
        items = [{"id": a.id, "path": a.path, "mode": a.mode, "selector": a.selector, "nth": a.nth, "max": a.max}]
    else:
        ap.error("give <id> <path> or --batch")
    res = asyncio.run(run(items))
    sys.stdout.buffer.write((json.dumps(res, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
