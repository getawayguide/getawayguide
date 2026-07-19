#!/usr/bin/env python3
"""
capture_site_preview.py — capture a homepage screenshot + favicon for an
external site, for use as a resource-card preview image on resources.html.

Single site:  python tools/capture_site_preview.py <slug> <url> [--out-dir DIR]
Batch:        python tools/capture_site_preview.py --batch pairs.json [--out-dir DIR]
              (pairs.json is {"slug": "url", ...})

Output: <out-dir>/shots/<slug>.jpg (1280x800 viewport screenshot)
        <out-dir>/favicons/<slug>.png (128x128 favicon via Google's favicon service)
"""
import asyncio
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

CONCURRENCY = 4
COOKIE_BUTTON_TEXT = ["Accept all", "Accept All", "Accept", "I agree", "I Agree", "Got it", "Allow all", "Allow All"]


async def dismiss_cookie_banner(page):
    for txt in COOKIE_BUTTON_TEXT:
        try:
            btn = page.get_by_text(txt, exact=False).first
            if await btn.is_visible(timeout=600):
                await btn.click(timeout=600)
                await page.wait_for_timeout(400)
                return
        except Exception:
            pass


async def capture_one(browser, slug, url, shots_dir, fav_dir, sem):
    async with sem:
        ctx = await browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
        page = await ctx.new_page()
        try:
            try:
                await page.goto(url, wait_until="networkidle", timeout=20000)
            except Exception:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1200)
            await dismiss_cookie_banner(page)
            await page.wait_for_timeout(400)
            out_path = shots_dir / f"{slug}.jpg"
            await page.screenshot(path=str(out_path), type="jpeg", quality=82)
            print(f"[ok]   {slug:<24} <- {url}")
        except Exception as e:
            print(f"[FAIL] {slug:<24} {url} ({e})")
        finally:
            await ctx.close()

        # fetch favicon via the browser's own request client (shares its trusted
        # cert store, avoiding local Python urllib SSL cert-store mismatches)
        domain = urlparse(url).netloc or url
        fav_url = f"https://www.google.com/s2/favicons?sz=128&domain={domain}"
        try:
            fctx = await browser.new_context()
            r = await fctx.request.get(fav_url, timeout=15000)
            (fav_dir / f"{slug}.png").write_bytes(await r.body())
            await fctx.close()
            print(f"[ok]   {slug:<24} favicon")
        except Exception as e:
            print(f"[FAIL] {slug:<24} favicon ({e})")


async def run(pairs, out_dir):
    from playwright.async_api import async_playwright

    shots_dir = out_dir / "shots"
    fav_dir = out_dir / "favicons"
    shots_dir.mkdir(parents=True, exist_ok=True)
    fav_dir.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(CONCURRENCY)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        await asyncio.gather(*[capture_one(browser, slug, url, shots_dir, fav_dir, sem) for slug, url in pairs.items()])
        await browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", nargs="?")
    ap.add_argument("url", nargs="?")
    ap.add_argument("--batch", help="JSON file of {slug: url}")
    ap.add_argument("--out-dir", default=".tmp/previews")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if args.batch:
        pairs = json.loads(Path(args.batch).read_text(encoding="utf-8"))
    elif args.slug and args.url:
        pairs = {args.slug: args.url}
    else:
        ap.error("provide slug + url, or --batch pairs.json")
        return

    asyncio.run(run(pairs, out_dir))


if __name__ == "__main__":
    main()
