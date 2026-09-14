"""Open/Save round-trip on .tmp copies through the REAL loadFile()/saveFile().
Run: PYTHONIOENCODING=utf-8 python .tmp/editor-qa/qa_roundtrip.py"""
import asyncio, difflib, re, sys, pathlib, html as htmlmod
from playwright.async_api import async_playwright
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from qa_common import *

FILES = ["kosovo-field-notes.html", "yerevan.html"]

def split_outside(page):
    """Return (before, body, after) around the .article-body / .artbody element (depth-tracked)."""
    m = re.search(r'<div[^>]*class="(?:article-body|artbody)[^"]*"[^>]*>', page)
    if not m: return None
    start = m.end(); depth = 1; i = start
    for t in re.finditer(r'<(/?)div\b[^>]*>', page[start:]):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            end = start + t.start()
            return page[:start], page[start:end], page[end:]
    return None

def norm_body(s):
    s = re.sub(r'>\s+<', '><', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()

def tokens(s):
    # split into tags and text so diffs are readable
    return [t for t in re.split(r'(<[^>]+>)', norm_body(s)) if t.strip()]

async def main():
    ctx = Ctx()
    async with async_playwright() as p:
        b, pg = await boot(p, ctx)
        for f in FILES:
            src = (QA / f).read_text(encoding="utf-8-sig")   # File.text() strips a UTF-8 BOM, so do we
            n = await load_real(pg, f, src)
            e0 = len(ctx.errors)
            print(f"\n=== {f}: loaded {n} chars of body; dirty={await pg.evaluate('isDirty')}")
            # 1. no-op save
            await pg.evaluate("saveFile()"); await pg.wait_for_timeout(500)
            saved = await pg.evaluate("window.__saved")
            (QA / f.replace(".html", ".saved1.html")).write_text(saved, encoding="utf-8")
            o = split_outside(src); s = split_outside(saved)
            if not o or not s: print("   could not split", bool(o), bool(s)); continue
            print(f"   outside-body byte-identical: head/before={o[0]==s[0]} after={o[2]==s[2]}")
            if o[0] != s[0]:
                d = list(difflib.unified_diff(o[0].splitlines(), s[0].splitlines(), lineterm="", n=0))
                print("   BEFORE-body diff (first 30 lines):"); [print("     " + l[:400]) for l in d[:30]]
            if o[2] != s[2]:
                d = list(difflib.unified_diff(o[2].splitlines(), s[2].splitlines(), lineterm="", n=0))
                print("   AFTER-body diff (first 30 lines):"); [print("     " + l[:400]) for l in d[:30]]
            ot, st = tokens(o[1]), tokens(s[1])
            sm = difflib.SequenceMatcher(None, ot, st, autojunk=False)
            ops = [op for op in sm.get_opcodes() if op[0] != "equal"]
            print(f"   body tokens: {len(ot)} -> {len(st)}; differing runs: {len(ops)}")
            for tag, i1, i2, j1, j2 in [o for o in ops if "<path" not in " ".join(ot[o[1]:o[2]])][:25]:
                print(f"     {tag:7s} - {' '.join(ot[i1:i2])[:400]!r}")
                print(f"             + {' '.join(st[j1:j2])[:400]!r}")
            # 2. save again: idempotent?
            src2 = saved
            await pg.evaluate("saveFile()"); await pg.wait_for_timeout(400)
            saved2 = await pg.evaluate("window.__saved")
            print(f"   second save identical to first: {saved2 == saved}")
            if saved2 != saved:
                d = list(difflib.unified_diff(saved.splitlines(), saved2.splitlines(), lineterm="", n=0))
                [print("     " + l[:200]) for l in d[:20]]
            # 3. reload the saved file and save once more (what the user sees after the tools reload)
            await load_real(pg, f, saved)
            await pg.evaluate("saveFile()"); await pg.wait_for_timeout(400)
            saved3 = await pg.evaluate("window.__saved")
            print(f"   reload(saved)+save identical: {saved3 == saved}")
            if saved3 != saved:
                d = list(difflib.unified_diff(saved.splitlines(), saved3.splitlines(), lineterm="", n=0))
                [print("     " + l[:200]) for l in d[:20]]
            # invariants inside body
            for label, rx in [("caption font-family", r'img-caption[^>]*font-family'), ("object-position", r'object-position'), ("filter:", r'filter:'), ("img-pair", r'class="img-pair'), ("img-landscape", r'class="img-landscape'), ("<picture", r'<picture'), ("<source", r'<source'), ("srcset", r'srcset='), ("127.0.0.1", r'127\.0\.0\.1'), ("data-path", r'data-path'), ("blob:", r'blob:'), ("<svg", r'<svg'), ("fn-sub-hd", r'fn-sub-hd'), ("id=", r' id=')]:
                a, c = len(re.findall(rx, o[1])), len(re.findall(rx, s[1]))
                flag = "" if a == c else "   <-- CHANGED"
                print(f"   {label:20s} {a:4d} -> {c:4d}{flag}")
            print(f"   errors during this file: {ctx.errors[e0:]}")
        report_errors(ctx, "roundtrip")
        await b.close()

asyncio.run(main())
