#!/usr/bin/env python3
"""Turn the placeholder Google-Maps SEARCH links in draft articles into real PLACE links.

Drafts are built with `https://www.google.com/maps/search/?api=1&query=<name>`, which lands
the reader on a search RESULTS page instead of the actual pin. Google resolves those to a
canonical `/maps/place/<Name>/@<lat>,<lon>,17z/data=…!1s<cid>…` URL client-side, so we let a
headless browser do the resolving and write the resolved URL back into the article - the same
link shape the finished articles already use.

  python tools/resolve_draft_maps.py --list                 # what would be resolved
  python tools/resolve_draft_maps.py croatia --dry-run      # resolve, don't write
  python tools/resolve_draft_maps.py croatia                # resolve + rewrite
  python tools/resolve_draft_maps.py --all --limit 150      # work through everything in batches

Every lookup is cached in .tmp/gmaps_resolved.json, so re-runs are cheap and interrupted runs
resume. Anything that fails to resolve is LEFT AS-IS (never guessed at) and reported, as is
anything whose coordinates land outside the article's country - those are the only links left
to eyeball, instead of all of them.
"""
import argparse, asyncio, glob, html as htmlmod, json, os, re, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".tmp", "gmaps_resolved.json")
BBOX = os.path.join(ROOT, ".tmp", "country_bbox.json")
# the ampersand may be HTML-escaped or raw depending on how the link was authored - match both,
# or hand-written `&query=` placeholders slip through the sweep untouched.
SEARCH_RE = re.compile(r'href="(https://www\.google\.com/maps/search/\?api=1&(?:amp;)?query=([^"]+))"')

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def decode_q(raw):
    """href query -> the text to search. The href is HTML-escaped, so `&amp;` must become `&`
    before it reaches Google (otherwise we search for the literal 'amp')."""
    return htmlmod.unescape(urllib.parse.unquote_plus(raw)).strip()


def load(p, default):
    try: return json.load(open(p, encoding="utf-8-sig"))
    except Exception: return default


def save(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def articles(slug=None, live=False):
    """Draft field-notes by default; `live=True` targets the PUBLISHED country pages instead.

    Published articles were assumed to be clean, but pages published before the draft
    resolver existed still carry the same /maps/search/ placeholders (615 of them across
    21 live pages), so the same fix has to be runnable against them."""
    base = "%s/field-notes.html" if live else "Drafts/%s/field-notes.html"
    pats = [base % slug] if slug else [base % "*"]
    out = []
    for pat in pats:
        out += [p.replace("\\", "/") for p in glob.glob(os.path.join(ROOT, pat))]
    return sorted(p for p in out if "/Drafts/" not in p or not live)


def queries_in(path):
    """[(raw_href, decoded_query)] for every placeholder search link in the file."""
    html = open(path, encoding="utf-8").read()
    out = []
    for m in SEARCH_RE.finditer(html):
        out.append((m.group(1), decode_q(m.group(2))))
    return out


def country_of(path):
    return os.path.basename(os.path.dirname(path))      # works for Drafts/<c>/ and live <c>/


async def resolve_many(queries, cache, headful=False, pause=400):
    """Drive a real browser so Google's client-side redirect gives us the canonical place URL."""
    from playwright.async_api import async_playwright
    done = 0
    async with async_playwright() as pw:
        br = await pw.chromium.launch(headless=not headful)
        ctx = await br.new_context(locale="en-US", user_agent=UA)
        await ctx.add_cookies([{"name": "CONSENT", "value": "YES+cb", "domain": ".google.com", "path": "/"}])
        pg = await ctx.new_page()
        # images/fonts add nothing here and cost a lot of time
        await pg.route(re.compile(r"\.(png|jpe?g|gif|webp|woff2?|ttf|svg)(\?|$)"), lambda r: asyncio.ensure_future(r.abort()))
        for q in queries:
            url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(q) + "&hl=en"
            rec = {"ok": False}
            try:
                await pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                for _ in range(40):
                    await pg.wait_for_timeout(250)
                    if "/maps/place/" in pg.url: break
                # Google only auto-redirects on a single confident match. Otherwise it shows a
                # results list - take the FIRST result, which is what a reader would click.
                if "/maps/place/" not in pg.url:
                    try:
                        first = pg.locator("a.hfpxzc").first
                        await first.wait_for(state="attached", timeout=6000)
                        await first.click(timeout=6000)
                        for _ in range(28):
                            await pg.wait_for_timeout(250)
                            if "/maps/place/" in pg.url: break
                    except Exception:
                        pass
                final = pg.url
                if "/maps/place/" in final:
                    ll = re.search(r"!3d(-?[\d.]+)!4d(-?[\d.]+)", final) or re.search(r"/@(-?[\d.]+),(-?[\d.]+)", final)
                    rec = {"ok": True, "url": final,
                           "lat": float(ll.group(1)) if ll else None,
                           "lon": float(ll.group(2)) if ll else None}
            except Exception as e:
                rec = {"ok": False, "err": str(e)[:90]}
            cache[q] = rec
            done += 1
            if done % 10 == 0:
                save(CACHE, cache)
                print("    resolved %d/%d" % (done, len(queries)), flush=True)
            await pg.wait_for_timeout(pause)
        await br.close()
    save(CACHE, cache)
    return cache


async def fetch_bboxes(countries, bboxes):
    """Country bounding boxes (Nominatim) so we can flag a pin that lands on the wrong continent."""
    from playwright.async_api import async_playwright
    todo = [c for c in countries if c not in bboxes]
    if not todo: return bboxes
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        pg = await (await br.new_context(user_agent=UA)).new_page()
        for c in todo:
            name = c.replace("-", " ")
            try:
                # featureType=country or an unqualified name matches a same-named STATE first:
                # "georgia" returned the US state (lon -85..-80), so every correctly-resolved
                # Tbilisi pin got flagged as out-of-country and skipped.
                await pg.goto("https://nominatim.openstreetmap.org/search?format=json&limit=1"
                              "&featureType=country&q=" + urllib.parse.quote_plus(name),
                              wait_until="domcontentloaded", timeout=30000)
                data = json.loads(await pg.inner_text("pre"))
                if data:
                    bb = [float(x) for x in data[0]["boundingbox"]]      # S, N, W, E
                    bboxes[c] = {"s": bb[0], "n": bb[1], "w": bb[2], "e": bb[3]}
                    print("    bbox %-14s %s" % (c, bboxes[c]))
            except Exception as e:
                print("    bbox %-14s FAILED %s" % (c, str(e)[:50]))
            await pg.wait_for_timeout(1200)
        await br.close()
    save(BBOX, bboxes)
    return bboxes


def in_country(rec, bb, pad=2.0):
    if not bb or rec.get("lat") is None: return True          # can't judge -> don't flag
    return (bb["s"] - pad <= rec["lat"] <= bb["n"] + pad and
            bb["w"] - pad <= rec["lon"] <= bb["e"] + pad)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", nargs="?", help="country slug (default: all)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--live", action="store_true", help="target PUBLISHED country pages, not Drafts/")
    ap.add_argument("--list", action="store_true", help="just show what needs resolving")
    ap.add_argument("--dry-run", action="store_true", help="resolve but don't rewrite the article")
    ap.add_argument("--limit", type=int, default=0, help="max NEW lookups this run (batching)")
    ap.add_argument("--headful", action="store_true")
    a = ap.parse_args()

    files = articles(a.slug if a.slug and not a.all else None, live=a.live)
    if not files: sys.exit("no article found")

    pending, seen = [], set()
    for f in files:
        for _, q in queries_in(f):
            if q not in seen: seen.add(q); pending.append(q)

    cache = load(CACHE, {})
    todo = [q for q in pending if q not in cache or not cache[q].get("ok")]
    print("%d file(s) | %d unique queries | %d cached | %d to resolve"
          % (len(files), len(pending), len(pending) - len(todo), len(todo)))
    if a.list:
        for q in todo[:40]: print("   " + q)
        if len(todo) > 40: print("   ... and %d more" % (len(todo) - 40))
        return

    if todo:
        batch = todo[:a.limit] if a.limit else todo
        print("resolving %d (browser)…" % len(batch))
        asyncio.run(resolve_many(batch, cache, headful=a.headful))

    bboxes = load(BBOX, {})
    asyncio.run(fetch_bboxes(sorted({country_of(f) for f in files}), bboxes))

    tot_rw = tot_unres = 0
    review = []
    for f in files:
        html = open(f, encoding="utf-8").read()
        c = country_of(f)
        bb = bboxes.get(c)
        n = 0
        def sub(m):
            nonlocal n
            q = decode_q(m.group(2))
            rec = cache.get(q)
            if not rec or not rec.get("ok"):
                return m.group(0)                                   # leave placeholder alone
            if not in_country(rec, bb):
                review.append((c, q, "outside %s" % c, rec.get("url", "")))
                return m.group(0)                                   # suspicious -> leave alone
            n += 1
            return 'href="%s"' % rec["url"].replace("&", "&amp;")
        new = SEARCH_RE.sub(sub, html)
        left = len(SEARCH_RE.findall(new))
        tot_rw += n; tot_unres += left
        if n and not a.dry_run:
            open(f, "w", encoding="utf-8", newline="").write(new)
        print("  %-34s resolved %3d   still placeholder %3d%s"
              % (f.split("/Drafts/")[-1], n, left, "  [dry-run]" if a.dry_run else ""))

    print("\n%s %d link(s); %d left as search placeholders"
          % ("would rewrite" if a.dry_run else "rewrote", tot_rw, tot_unres))
    if review:
        print("\nFLAGGED - resolved outside the expected country, left unchanged (check these):")
        for c, q, why, url in review[:25]:
            print("   %-12s %-42s %s" % (c, q[:42], why))
        if len(review) > 25: print("   ... and %d more" % (len(review) - 25))
    unresolved = [q for q in pending if not cache.get(q, {}).get("ok")]
    if unresolved:
        print("\nCOULD NOT RESOLVE (left as search links - usually generic/activity names):")
        for q in unresolved[:25]: print("   " + q)
        if len(unresolved) > 25: print("   ... and %d more" % (len(unresolved) - 25))


if __name__ == "__main__":
    main()
