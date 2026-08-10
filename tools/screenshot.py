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

    # Threading, not plain HTTPServer: destinations.html pulls ~36 dest-card photos at once
    # and a single-threaded server answers them one at a time while Chromium holds six
    # connections open waiting. Pages captured mid-load, and the stalls looked like broken
    # CSS rather than a slow server. (Same fix the map screenshots needed.)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), SilentHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.chdir(original_dir)
    return server


async def screenshot_pages(pages, selector=None):
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

        for page_file in pages:
            # Use full path slug to avoid collisions (e.g. el-salvador/index → el-salvador-index)
            name = page_file.replace("/", "-").replace("\\", "-").removesuffix(".html")
            url = f"http://127.0.0.1:{PORT}/{page_file}"
            print(f"\n{page_file}")

            for vp_name, ctx_opts in viewports:
                ctx = await chromium.new_context(**ctx_opts)
                page = await ctx.new_page()

                try:
                    await page.goto(url, wait_until="networkidle", timeout=15000)
                except Exception:
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    except Exception:
                        # external resources (CDNs/fonts) can stall intermittently;
                        # capture whatever has rendered rather than failing outright
                        pass

                await page.wait_for_timeout(2000)  # let fonts and animations load

                # overflow-x:hidden on body causes Playwright to miscalculate scroll height
                # Also force-reveal any IntersectionObserver-hidden elements (opacity:0)
                await page.evaluate("""() => {
                    document.documentElement.style.overflowX = 'visible';
                    document.body.style.overflowX = 'visible';
                    // Reveal elements hidden by IntersectionObserver scroll animations
                    document.querySelectorAll('*').forEach(function(el) {
                        var s = el.style;
                        if (s.opacity === '0' && s.transform && s.transform.includes('translateY')) {
                            s.opacity = '1';
                            s.transform = 'translateY(0)';
                        }
                    });
                }""")

                # The reveal above is what STARTS the dest-card background fetches: Chromium
                # will not load a background-image for an element at opacity:0, so those
                # requests only go out once we flip them visible. Screenshotting immediately
                # caught the cards mid-fetch and every destinations/index capture came back
                # with blank cards (just the overlay gradient and the label). Wait for them.
                # onload/onerror rather than decode(), which rejects on a request still in
                # flight and would abort the whole run.
                try:
                    await page.evaluate("""async () => {
                        const urls = new Set();
                        document.querySelectorAll('*').forEach(function(el) {
                            const b = getComputedStyle(el).backgroundImage;
                            if (!b || b === 'none') return;
                            for (const m of b.matchAll(/url\\("?([^")]+)"?\\)/g)) urls.add(m[1]);
                        });
                        const all = Promise.all([...urls].map(u => new Promise(function(res) {
                            const i = new Image();
                            i.onload = i.onerror = res;
                            i.src = u;
                        })));
                        // never let a stalled asset hold the capture hostage
                        await Promise.race([all, new Promise(r => setTimeout(r, 8000))]);
                    }""")
                    await page.wait_for_timeout(400)
                except Exception:
                    pass

                out_path = SCREENSHOTS_DIR / f"{name}-{vp_name}.png"
                if selector:
                    el = page.locator(selector).first
                    await el.screenshot(path=str(out_path), timeout=60000)
                else:
                    await page.screenshot(path=str(out_path), full_page=True, timeout=60000)
                print(f"  [ok] {vp_name:<8} -> {out_path.name}")
                await ctx.close()

        await chromium.close()


def main():
    parser = argparse.ArgumentParser(description="Screenshot tool for getawayguide")
    parser.add_argument("pages", nargs="*", help="HTML files to screenshot")
    parser.add_argument("--all", action="store_true", help="Screenshot all HTML files")
    parser.add_argument("--selector", default=None, help="CSS selector to crop screenshot to (e.g. '.cc-wrap')")
    args = parser.parse_args()

    if args.all:
        # recursive: include subdirectory pages (el-salvador/, new-zealand/, */field-notes.html)
        pages = sorted(
            str(p.relative_to(PROJECT_DIR)).replace("\\", "/")
            for p in PROJECT_DIR.rglob("*.html")
            if not any(part.startswith(".") for part in p.relative_to(PROJECT_DIR).parts)
        )
    elif args.pages:
        pages = args.pages
    else:
        pages = ["index.html"]

    print(f"Starting local server on port {PORT}...")
    server = start_server()
    time.sleep(0.4)

    try:
        asyncio.run(screenshot_pages(pages, selector=args.selector))
    finally:
        server.shutdown()

    print(f"\nDone. Screenshots saved to .tmp/screenshots/")


if __name__ == "__main__":
    main()
