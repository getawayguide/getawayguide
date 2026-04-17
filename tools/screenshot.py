#!/usr/bin/env python3
"""
Screenshot tool — captures desktop, tablet, and mobile views of any page.

Usage:
    python tools/screenshot.py                           # index.html only
    python tools/screenshot.py el-salvador.html          # single page
    python tools/screenshot.py index.html posts.html     # multiple pages
    python tools/screenshot.py --all                     # every .html file

Output: .tmp/screenshots/{page}-{desktop|tablet|mobile}.png

Requirements:
    pip install playwright
    playwright install chromium
"""

import asyncio
import argparse
import http.server
import threading
import os
import time
import sys
from pathlib import Path

PORT = 8765
PROJECT_DIR = Path(__file__).resolve().parent.parent
SCREENSHOTS_DIR = PROJECT_DIR / ".tmp" / "screenshots"


def start_server():
    """Serve the project directory over HTTP on localhost."""
    original_dir = os.getcwd()
    os.chdir(PROJECT_DIR)

    class SilentHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), SilentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.chdir(original_dir)
    return server


async def screenshot_pages(pages):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: Playwright not installed.")
        print("Run:  pip install playwright && playwright install chromium")
        sys.exit(1)

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        iphone = p.devices["iPhone 14 Pro"]

        ipad = p.devices["iPad (gen 11)"]

        viewports = [
            ("desktop", {"viewport": {"width": 1440, "height": 900}}),
            ("tablet",  ipad),
            ("mobile",  iphone),
        ]

        chromium = await p.chromium.launch()
        webkit = await p.webkit.launch()

        browsers = {
            "desktop": chromium,
            "tablet":  chromium,
            "mobile":  webkit,
        }

        for page_file in pages:
            # Use full path slug to avoid collisions (e.g. el-salvador/index → el-salvador-index)
            name = page_file.replace("/", "-").replace("\\", "-").removesuffix(".html")
            url = f"http://127.0.0.1:{PORT}/{page_file}"
            print(f"\n{page_file}")

            for vp_name, ctx_opts in viewports:
                browser = browsers[vp_name]
                ctx = await browser.new_context(**ctx_opts)
                page = await ctx.new_page()

                try:
                    await page.goto(url, wait_until="networkidle", timeout=15000)
                except Exception:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)

                await page.wait_for_timeout(2000)  # let fonts and animations load

                out_path = SCREENSHOTS_DIR / f"{name}-{vp_name}.png"
                try:
                    await page.screenshot(path=str(out_path), full_page=True, timeout=20000)
                except Exception:
                    # WebKit can timeout on full-page screenshots for long pages; fall back to viewport
                    await page.screenshot(path=str(out_path), full_page=False, timeout=20000)
                print(f"  [ok] {vp_name:<8} -> {out_path.name}")
                await ctx.close()

        await chromium.close()
        await webkit.close()


def main():
    parser = argparse.ArgumentParser(description="Screenshot tool for getawayguide")
    parser.add_argument("pages", nargs="*", help="HTML files to screenshot")
    parser.add_argument("--all", action="store_true", help="Screenshot all HTML files")
    args = parser.parse_args()

    if args.all:
        pages = sorted(p.name for p in PROJECT_DIR.glob("*.html"))
    elif args.pages:
        pages = args.pages
    else:
        pages = ["index.html"]

    print(f"Starting local server on port {PORT}...")
    server = start_server()
    time.sleep(0.4)

    try:
        asyncio.run(screenshot_pages(pages))
    finally:
        server.shutdown()

    print(f"\nDone. Screenshots saved to .tmp/screenshots/")


if __name__ == "__main__":
    main()
