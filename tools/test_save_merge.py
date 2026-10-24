"""The editor's save never overwrites a change made outside the editor (editor.html saveFile +
/api/merge3 on the photo server). Headless; nothing on disk is written: the article is a stub
file whose text lives in the page (window.__disk), and saves land there.

    python tools/test_save_merge.py          (needs the photo server on :5003)
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
ART = "Drafts/.Full Articles/.qa-scratch/athens.html"
EDITOR = (ROOT / "editor.html").as_uri() + "#editor"

# a stub that behaves like a real file: getFile() returns what was last written to it
OPEN = """async rel => { folderHandle = null; window.__writes = 0;
  window.__disk = await (await fetch('http://127.0.0.1:5003/site/' + rel.split('/').map(encodeURIComponent).join('/'))).text();
  await window.loadFile({ kind: 'file', name: 'athens.html', __realDisk: true,
    getFile: async () => new File([window.__disk], 'athens.html', { type: 'text/html' }),
    createWritable: async () => ({ write: async h => { window.__disk = h; window.__writes++; }, close: async () => {} }) }); }"""

# edit the Nth long paragraph in the editor (the editor's side)
EDIT = """([n, word]) => { const ps = [...document.querySelectorAll('#editor p')].filter(p => p.textContent.trim().length > 80);
  const p = ps[n]; p.appendChild(document.createTextNode(' ' + word)); setDirty(true); return p.textContent.slice(0, 30); }"""
# change the disk copy of the Nth long paragraph's text (the outside writer's side)
OUTSIDE = """([n, word]) => { const d = new DOMParser().parseFromString(window.__disk, 'text/html');
  const ps = [...d.querySelectorAll('.article-body p, .artbody p')].filter(p => p.textContent.trim().length > 80);
  const t = ps[n].innerHTML; const i = window.__disk.indexOf(t); if (i < 0) return false;
  window.__disk = window.__disk.slice(0, i) + t + ' ' + word + window.__disk.slice(i + t.length); return true; }"""

checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(accept_downloads=True, viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    # MERGE_PORT: send the merge to a test server instead of the live one (to try a server change
    # before the live server is restarted)
    import os
    if os.environ.get("MERGE_PORT"):
        import urllib.request
        def _to_test(route):
            req = route.request
            if req.method == "OPTIONS":
                return route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "*",
                                     "Access-Control-Allow-Headers": "Content-Type, Content-Encoding", "Access-Control-Allow-Methods": "POST"})
            r = urllib.request.Request(req.url.replace(":5003", ":" + os.environ["MERGE_PORT"]), data=req.post_data_buffer,
                                       headers={k: v for k, v in req.headers.items() if k.lower() in ("content-type", "content-encoding")}, method="POST")
            with urllib.request.urlopen(r, timeout=60) as resp:
                route.fulfill(status=resp.status, body=resp.read(), headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})
        pg.route("http://127.0.0.1:5003/api/merge3", _to_test)
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(EDITOR)
    pg.wait_for_timeout(1200)
    op = lambda: (pg.evaluate("([o, r]) => eval(o)(r)", [OPEN, ART]), pg.wait_for_timeout(3500))
    op()

    # 1. nothing changed outside: a save writes the editor's copy, as before
    pg.evaluate(EDIT, [1, "EDITOR-ONE"])
    pg.evaluate("saveFile()")
    pg.wait_for_timeout(800)
    d = pg.evaluate("window.__disk")
    ok("an unchanged file saves as before", "EDITOR-ONE" in d and not pg.evaluate("!!document.getElementById('notice-disk')"))

    # 2. an outside change to a far-away paragraph is merged, not overwritten
    ok("(setup) the outside change landed", pg.evaluate(OUTSIDE, [8, "OUTSIDE-ONE"]))
    pg.evaluate(EDIT, [2, "EDITOR-TWO"])
    pg.evaluate("saveFile()")
    pg.wait_for_timeout(1500)
    d = pg.evaluate("window.__disk")
    ok("a save after an outside change keeps both edits", all(w in d for w in ("EDITOR-ONE", "EDITOR-TWO", "OUTSIDE-ONE")))
    ok("the merge is announced with a Reopen button", pg.evaluate("(document.getElementById('notice-disk') || {}).textContent || ''").startswith("Saved and merged"))

    # 3. the next save still carries the outside change (the editor never showed it)
    pg.evaluate(EDIT, [3, "EDITOR-THREE"])
    pg.evaluate("saveFile()")
    pg.wait_for_timeout(1500)
    d = pg.evaluate("window.__disk")
    ok("the save after a merge does not undo the outside change", all(w in d for w in ("OUTSIDE-ONE", "EDITOR-THREE")))

    # 4. Reopen shows the outside change in the editor
    pg.evaluate("document.querySelector('#notice-disk [data-a=open]') && document.querySelector('#notice-disk [data-a=open]').click()")
    pg.wait_for_timeout(3500)
    ok("Reopen loads the merged file into the editor", "OUTSIDE-ONE" in pg.evaluate("document.getElementById('editor').textContent"))

    # 5. both sides edit the same paragraph: an autosave writes nothing and pauses behind a banner
    pg.evaluate(OUTSIDE, [5, "OUTSIDE-CLASH"])
    pg.evaluate(EDIT, [5, "EDITOR-CLASH"])
    before = pg.evaluate("[window.__writes, window.__disk]")
    with pg.expect_download(timeout=5000):          # no folder connection: the two copies download
        pg.evaluate("saveFile({ auto: true })")
    pg.wait_for_timeout(1200)
    after = pg.evaluate("[window.__writes, window.__disk]")
    ok("a clashing autosave writes nothing", after == before)
    ok("a clashing autosave shows the paused banner, no dialog",
       "Autosave is paused" in pg.evaluate("(document.getElementById('notice-disk') || {}).textContent || ''")
       and not pg.evaluate("!!document.getElementById('disk-clash-modal')"))
    pg.evaluate("saveFile({ auto: true })")
    pg.wait_for_timeout(800)
    ok("autosave stays paused until it is resolved", pg.evaluate("window.__writes") == before[0])

    # 6. a manual save asks; "Save my version over it" writes the editor's copy
    pg.evaluate("saveFile()")
    pg.wait_for_timeout(1500)
    ok("a clashing manual save asks which version to keep", pg.evaluate("!!document.getElementById('disk-clash-modal')"))
    pg.click("#disk-clash-modal [data-a=mine]")
    pg.wait_for_timeout(1500)
    d = pg.evaluate("window.__disk")
    ok("'Save my version' writes the editor's copy and clears the banner",
       "EDITOR-CLASH" in d and "OUTSIDE-CLASH" not in d and not pg.evaluate("!!document.getElementById('notice-disk')"))

    # 7. "Open the version on disk" loads the outside version and drops the editor's unsaved edit
    pg.evaluate(OUTSIDE, [6, "OUTSIDE-WINS"])
    pg.evaluate(EDIT, [6, "EDITOR-LOSES"])
    with pg.expect_download(timeout=5000):
        pg.evaluate("saveFile()")
    pg.wait_for_timeout(1500)
    pg.click("#disk-clash-modal [data-a=disk]")
    pg.wait_for_timeout(3500)
    t = pg.evaluate("document.getElementById('editor').textContent")
    ok("'Open the version on disk' shows the outside version", "OUTSIDE-WINS" in t and "EDITOR-LOSES" not in t)

    # 8. no photo server to merge with: nothing is written, the dialog says why
    pg.evaluate("""() => { const f = window.__realFetch = window.fetch; window.fetch = (u, o) => String(u).includes('/api/merge3') ? Promise.reject(new Error('down')) : f(u, o); }""")
    pg.evaluate(OUTSIDE, [9, "OUTSIDE-FAR"])
    pg.evaluate(EDIT, [1, "EDITOR-NOSERVER"])
    w0 = pg.evaluate("window.__writes")
    with pg.expect_download(timeout=5000):
        pg.evaluate("saveFile()")
    pg.wait_for_timeout(1500)
    ok("with the server down, a changed file is not overwritten", pg.evaluate("window.__writes") == w0)
    ok("and the dialog says the server is not answering",
       "isn’t answering" in pg.evaluate("(document.getElementById('disk-clash-modal') || {}).textContent || ''"))

    # 9. an outside change before the editor's first save still merges. A live article is in the
    #    editor's own format, so its first save changes only a line or two (the old scratch copy
    #    predates that format and is rewritten wholesale, which would read as a clash)
    pg.evaluate("() => { window.fetch = window.__realFetch || window.fetch; }")
    pg.evaluate("([o, r]) => eval(o)(r)", [OPEN, "greece/meteora.html"])
    pg.wait_for_timeout(3500)
    pg.evaluate(OUTSIDE, [8, "OUTSIDE-FIRST"])
    pg.evaluate(EDIT, [2, "EDITOR-FIRST"])
    pg.evaluate("saveFile()")
    pg.wait_for_timeout(1500)
    d = pg.evaluate("window.__disk")
    clash = pg.evaluate("!!document.getElementById('disk-clash-modal')")
    ok("an outside change before the editor's first save still merges", "OUTSIDE-FIRST" in d and "EDITOR-FIRST" in d, f"modal={clash}")
    pg.evaluate("window.__clearDiskClash()")

    op()
    # 10. with a folder connection, both versions are kept in .tmp/editor_conflicts/
    pg.evaluate("""() => { const files = window.__kept = {};
      const dir = name => ({ getDirectoryHandle: async (n) => dir(name + '/' + n),
        getFileHandle: async (n) => ({ createWritable: async () => ({ write: async t => { files[name + '/' + n] = t; }, close: async () => {} }) }) });
      folderHandle = dir('root'); }""")
    pg.evaluate(OUTSIDE, [4, "OUTSIDE-KEPT"])
    pg.evaluate(EDIT, [4, "EDITOR-KEPT"])
    pg.evaluate("saveFile()")
    pg.wait_for_timeout(1500)
    kept = pg.evaluate("Object.entries(window.__kept).map(([k, v]) => [k, v.includes('OUTSIDE-KEPT'), v.includes('EDITOR-KEPT')])")
    ok("both versions are kept in .tmp/editor_conflicts/", len(kept) == 2 and all(k.startswith("root/.tmp/editor_conflicts/athens-") for k, *_ in kept)
       and any(k.endswith("-on-disk.html") and o and not e for k, o, e in kept) and any(k.endswith("-yours.html") and e and not o for k, o, e in kept), kept)
    ok("the dialog names where they are", ".tmp/editor_conflicts/athens-" in pg.evaluate("document.getElementById('disk-clash-modal').textContent"))

    ok("no page errors", not errs, errs[:3])
    b.close()

for name, good, info in checks:
    print(("PASS " if good else "FAIL ") + name + ("" if good or not info else f"  {info}"))
print(f"{sum(1 for c in checks if not c[1])} failed")
sys.exit(1 if any(not c[1] for c in checks) else 0)
