"""Verify the site's webfonts actually LOAD, not merely that they are requested.

getComputedStyle reports the font-family a rule *asked for*, whether or not the
file behind it ever arrived. That is how the re-theme shipped with preview/fonts/
left behind: 49 pages asked for Newsreader and Hanken Grotesk, every consistency
check reported those names, and the live site rendered in system fallbacks.

This checks the two things that actually matter:

  1. every @font-face file in fonts.css exists on disk
  2. with Google Fonts BLOCKED, document.fonts.check() confirms each declared
     face is usable, and nothing on the page renders at a weight the family
     does not ship (which the browser fakes, and fake bold looks wrong)

    python tools/check_fonts.py
    python tools/check_fonts.py --pages index.html,about.html
"""
import argparse
import asyncio
import pathlib
import re
import sys

from playwright.async_api import async_playwright

SITE = pathlib.Path(__file__).resolve().parent.parent

PROBE = """async () => {
  await document.fonts.ready;
  const declared = %s;
  const checks = {};
  for (const [spec, label] of declared) checks[label] = document.fonts.check(spec);
  const fake = [];
  const seen = new Set();
  document.querySelectorAll('*').forEach(e => {
    const c = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const fam = c.fontFamily.split(',')[0].replace(/["']/g, '').trim();
    if (!%s.includes(fam)) return;
    const key = fam + '|' + c.fontWeight + '|' + c.fontStyle;
    if (seen.has(key)) return;
    seen.add(key);
    fake.push([fam, c.fontWeight, c.fontStyle,
               e.tagName + '.' + String(e.className).slice(0, 18)]);
  });
  return {checks, used: fake};
}"""


def declared_faces(css):
    out = {}
    for blk in re.findall(r"@font-face\s*\{[^}]*\}", css):
        fam = re.search(r"font-family:\s*['\"]?([^;'\"]+)", blk)
        w = re.search(r"font-weight:\s*([^;]+);", blk)
        st = re.search(r"font-style:\s*([^;]+);", blk)
        src = re.findall(r"url\(([^)]+)\)", blk)
        if not fam:
            continue
        key = (fam.group(1).strip(),
               (w.group(1).strip() if w else "400"),
               (st.group(1).strip() if st else "normal"))
        out.setdefault(key, []).extend(u.strip("'\" ") for u in src)
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default="")
    a = ap.parse_args()

    css_path = SITE / "fonts.css"
    if not css_path.exists():
        print("fonts.css not found"); sys.exit(1)
    faces = declared_faces(css_path.read_text(encoding="utf-8"))
    fams = sorted({f for f, _, _ in faces})
    print(f"fonts.css declares {len(faces)} faces across {len(fams)}: "
          f"{', '.join(fams)}")

    # 1. every referenced file is on disk
    missing = []
    for (fam, w, st), srcs in faces.items():
        for u in srcs:
            if u.startswith(("http", "data:")):
                continue
            if not (css_path.parent / u).exists():
                missing.append(f"{fam} {w} {st} -> {u}")
    if missing:
        print(f"\n{len(missing)} @font-face file(s) MISSING from disk:")
        for m in missing[:10]:
            print("   " + m)
    else:
        print("every @font-face file is present on disk")

    by_family = {}
    for fam, w, st in faces:
        by_family.setdefault(fam, set()).add((w, st))

    specs = [[f"{st if st != 'normal' else ''} {w} 16px '{fam}'".strip(),
              f"{fam} {w}{' i' if st == 'italic' else ''}"]
             for fam, w, st in sorted(faces)]
    import json
    # json, not repr: repr emits Python quoting, and a family name with a
    # space in it ('Hanken Grotesk') came out as a bare JS identifier
    probe = PROBE % (json.dumps(specs), json.dumps(fams))

    pages = ([SITE / p for p in a.pages.split(",")] if a.pages else
             [SITE / "index.html", SITE / "about.html", SITE / "posts.html",
              SITE / "resources.html",  # destinations keeps a map
              # socket open, so it never finishes loading
              SITE / "albania/field-notes.html",
              SITE / "el-salvador/santa-ana.html"])

    faked = 0
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900})
        # block the CDN so only the local files can satisfy a face
        await ctx.route("**://fonts.googleapis.com/**", lambda r: r.abort())
        await ctx.route("**://fonts.gstatic.com/**", lambda r: r.abort())
        p = await ctx.new_page()
        print("\nwith Google Fonts blocked:")
        for f in pages:
            if not f.exists():
                continue
            # destinations.html keeps a map socket open, so "load" never fires
            await p.goto(f.resolve().as_uri(), wait_until="domcontentloaded")
            await p.wait_for_timeout(2200)
            r = await p.evaluate(probe)
            bad = []
            for fam, w, st, where in r["used"]:
                have = by_family.get(fam, set())
                if not any(hw == w and hs == st for hw, hs in have):
                    bad.append(f"{fam} {w} {st} on {where}")
            faked += len(bad)
            rel = f.relative_to(SITE).as_posix()
            print(f"  {rel:32s} {'FAKED: ' + '; '.join(bad[:3]) if bad else 'ok'}")
        await b.close()

    if missing or faked:
        print(f"\n{len(missing)} missing file(s), {faked} synthesised weight(s)")
        sys.exit(1)
    print("\nevery face loads and no weight is being synthesised")


asyncio.run(main())
