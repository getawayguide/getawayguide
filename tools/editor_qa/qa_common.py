"""Shared Playwright harness for editor.html QA. Server must already be on 127.0.0.1:5003."""
import asyncio, json, pathlib, sys, time
from playwright.async_api import async_playwright

API = "http://127.0.0.1:5003"
ROOT = pathlib.Path(r"c:\Users\kevin\OneDrive\Documents\Travel Blog")
QA = ROOT / ".tmp" / "editor-qa"
SHOTS = ROOT / ".tmp" / "qa"
SHOTS.mkdir(parents=True, exist_ok=True)

class Ctx:
    def __init__(self):
        self.errors = []      # pageerror + console.error
        self.dialogs = []     # (type, message)
        self.dialog_reply = None   # None = dismiss; str = accept with value; True = accept

async def boot(p, ctx, viewport=(1500, 1000), headless=True):
    b = await p.chromium.launch(headless=headless, args=["--use-gl=swiftshader"])
    pg = await b.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    pg.on("pageerror", lambda e: ctx.errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: ctx.errors.append(f"console.{m.type}: {m.text}")
          if m.type in ("error",) and "Failed to load resource" not in m.text else None)
    async def on_dialog(d):
        ctx.dialogs.append((d.type, d.message))
        r = ctx.dialog_reply
        if r is None: await d.dismiss()
        elif r is True: await d.accept()
        else: await d.accept(str(r))
    pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
    await pg.goto(f"{API}/site/editor.html", wait_until="load")
    await pg.wait_for_timeout(500)
    return b, pg

# Load HTML through the REAL loadFile() with a fake File System Access handle.
# Saving writes the serialized page to window.__saved instead of disk.
LOAD_REAL_JS = """async ({name, html}) => {
  window.__saved = null;
  const fh = { name, kind: 'file',
    getFile: async () => ({ text: async () => html }),
    createWritable: async () => ({ write: async h => { window.__saved = h; }, close: async () => {} }) };
  await loadFile(fh);
  return document.getElementById('editor').innerHTML.length;
}"""

# Inject raw article-body HTML with the same fake handle (no loadFile image munging).
INJECT_JS = """({name, body, page}) => {
  window.__saved = null;
  const ed = document.getElementById('editor');
  ed.innerHTML = body; ed.style.display = 'block'; ed.contentEditable = 'true';
  document.getElementById('empty-state').style.display = 'none';
  originalHTML = page || ('<!DOCTYPE html><html><head><title>t</title></head><body><div class="article-body">' + body + '</div></body></html>');
  fileHandle = { name, kind: 'file',
    createWritable: async () => ({ write: async h => { window.__saved = h; }, close: async () => {} }) };
  document.getElementById('btn-save').disabled = false;
  try { document.execCommand('defaultParagraphSeparator', false, 'p'); document.execCommand('styleWithCSS', false, false); } catch (e) {}
  setDirty(false); if (window.__History) __History.reset();
  return ed.innerHTML.length;
}"""

STOP_SPELLWALK_JS = "() => { _spellWalkToken++; }"

async def load_real(pg, name, html):
    n = await pg.evaluate(LOAD_REAL_JS, {"name": name, "html": html})
    await pg.evaluate(STOP_SPELLWALK_JS)
    await pg.wait_for_timeout(100)
    await pg.evaluate("() => { if (window.__History) __History.reset(); }")
    return n

async def inject(pg, name, body, page=None):
    return await pg.evaluate(INJECT_JS, {"name": name, "body": body, "page": page})

# Put a collapsed caret inside the element matched by selector, at text offset `off`
# (or at the end if off is None), or select the whole contents when `select_all`.
CARET_JS = """({sel, off, selectAll, atEnd}) => {
  const el = document.querySelector(sel); if (!el) return 'NO_EL';
  const r = document.createRange();
  if (selectAll) { r.selectNodeContents(el); }
  else {
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); const t = w.nextNode();
    if (!t) { r.setStart(el, atEnd ? el.childNodes.length : 0); r.collapse(true); }
    else if (atEnd) { let last = t, n; while ((n = w.nextNode())) last = n; r.setStart(last, last.textContent.length); r.collapse(true); }
    else { r.setStart(t, Math.min(off || 0, t.textContent.length)); r.collapse(true); }
  }
  const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  document.getElementById('editor').focus();
  s.removeAllRanges(); s.addRange(r);
  return 'ok';
}"""

async def caret(pg, sel, off=0, select_all=False, at_end=False):
    r = await pg.evaluate(CARET_JS, {"sel": sel, "off": off, "selectAll": select_all, "atEnd": at_end})
    assert r == "ok", f"caret: {r} for {sel}"

async def ed_html(pg):
    return await pg.evaluate("document.getElementById('editor').innerHTML")

async def active_blocks(pg):
    return await pg.evaluate("Array.from(document.querySelectorAll('.toolbar .tool-btn.tool-active')).map(b=>b.textContent.trim())")

def report_errors(ctx, label=""):
    print(f"\n[{label}] console/page errors: {len(ctx.errors)}; dialogs: {len(ctx.dialogs)}")
    for e in ctx.errors[:15]: print("   ERR", e[:300])
    for d in ctx.dialogs[:15]: print("   DLG", d)

# intercept a route in the page: fetch(url) matching `pat` is answered locally
PATCH = """({pat, mode, body, delay, status}) => {
  if (!window.__origFetch) window.__origFetch = window.fetch;
  window.__hits = window.__hits || [];
  const re = new RegExp(pat);
  window.fetch = async function (url, opts) {
    if (re.test(String(url))) {
      window.__hits.push({ url: String(url), body: opts && opts.body });
      if (mode === 'record') return window.__origFetch(url, opts);
      if (mode === 'swallow') return new Response('{"ok":true}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (delay) await new Promise(r => setTimeout(r, delay));
      if (mode === 'fail') throw new TypeError('Failed to fetch');
      if (mode === 'status') return new Response('<html>Internal Server Error</html>', { status: status || 500 });
      if (mode === 'body') return new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (mode === 'delay') return window.__origFetch(url, opts);
    }
    return window.__origFetch(url, opts);
  };
}"""
UNPATCH = "() => { if (window.__origFetch) window.fetch = window.__origFetch; const h = window.__hits || []; window.__hits = []; return h; }"

# Serve /api/backup_status from a snapshot so tests do not hammer the 40 s route.
async def stub_status(pg):
    snap = (QA / "status_snapshot.json").read_text(encoding="utf-8-sig")
    await pg.evaluate(PATCH, {"pat": "/api/backup_status", "mode": "body", "body": snap})

async def lib_open(pg, ctx, album, stub=True):
    if stub: await stub_status(pg)
    await pg.evaluate("showView('library')")
    await pg.wait_for_selector("#albums .alb", timeout=60000); await pg.wait_for_timeout(500)
    await pg.click(f"#albums .alb[data-album='{album}']")
    await pg.wait_for_function("document.querySelectorAll('#grid .ph').length > 0", timeout=60000)
    await pg.wait_for_timeout(800)
