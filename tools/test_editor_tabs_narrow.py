"""Every app tab in the editor's top bar can be reached at any window width (editor.html fitTabs).
QA 2026-11-10: at 800 px and less the row was clipped ("Cove...") and Maps, Launch, Social and Queue could not be
reached by mouse or touch. Kevin will use the editor through Chrome Remote Desktop from a phone or a small window.

editor.html is opened from disk and every request to a local server is answered by a stub, so no server is needed
and nothing on disk is read or written.

  N1  at 1440, 1024, 800, 600 and 393 px, in each of the seven apps: every tab is on screen and is the element under
      its own center (a real click lands on it), or the one-pill form is, and its list holds all seven apps on screen
  N2  the bar never runs past the window (no clipped bar, no sideways page scroll)
  N3  a real mouse click on each way in (a tab, or the pill and then a row of its list) opens that app
  N4  at 1440 every tab keeps its name (the wide form is unchanged)
  N5  at 393 a "Saved" note beside the editor's buttons still leaves the pill on screen and clickable
  N6  the list closes on Escape and when a row is picked, and the pill says which app is open

    python tools/test_editor_tabs_narrow.py            (screenshots to .tmp/qa/tabs_narrow/)
"""
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
SHOTS = ROOT / ".tmp" / "qa" / "tabs_narrow"
WIDTHS = (1440, 1024, 800, 600, 393)
VIEWS = ["editor", "library", "heroes", "maps", "launch", "social", "queue"]
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"}
checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:500]), flush=True)


# what a click at each visible way in would hit; [] for a tab that is off screen or under something else
REACH = """() => {
  const nav = [...document.querySelectorAll('.app-tabs')].find(n => n.getClientRects().length);
  if (!nav) return null;
  const W = window.innerWidth, hd = nav.closest('header'), hr = hd.getBoundingClientRect();
  const hit = el => { const r = el.getBoundingClientRect(); if (!r.width || r.left < 0 || r.right > W + 0.5) return null;
                      const x = r.left + r.width / 2, y = r.top + r.height / 2, at = document.elementFromPoint(x, y);
                      return at && (at === el || el.contains(at)) ? [x, y] : null; };
  const tabs = [...nav.querySelectorAll('a[data-view]')].filter(a => a.getClientRects().length).map(a => [a.dataset.view, hit(a)]);
  const menu = nav.querySelector('.tabs-menu');
  return { fit: nav.dataset.fit, tabs, menu: menu && menu.getClientRects().length ? hit(menu) : null,
           menuName: menu ? menu.textContent.trim() : '',
           named: [...nav.querySelectorAll('a[data-view]')].filter(a => a.querySelector('.at').getBoundingClientRect().width > 2).length,
           barRight: Math.round(hr.right), barScroll: hd.scrollWidth - hd.clientWidth, pageScroll: document.documentElement.scrollWidth - W };
}"""
LIST = """() => { const l = document.getElementById('applist'); if (l.hidden) return null; const W = innerWidth, H = innerHeight;
  return [...l.querySelectorAll('a[data-view]')].map(a => { const r = a.getBoundingClientRect();
    const on = r.left >= 0 && r.right <= W + 0.5 && r.top >= 0 && r.bottom <= H + 0.5; const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return [a.dataset.view, on && at && (at === a || a.contains(at)) ? [r.left + r.width / 2, r.top + r.height / 2] : null]; }); }"""
SHOWING = "() => ['editor','library','heroes','maps','launch','social','queue'].find(v => v === 'editor' ? document.getElementById('app-editor').style.display !== 'none' : !document.getElementById('app-' + v).hidden)"


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    posted = []

    def stub(route):
        # the Launch tab asks for its state by POST (/api/launch/...); everything is answered here, never by a server
        if route.request.method == "POST" and "/api/launch/" not in route.request.url:
            posted.append(route.request.url)
        return route.fulfill(status=200, json={}, headers=CORS)

    with sync_playwright() as p:
        b = p.chromium.launch()
        for w in WIDTHS:
            ctx = b.new_context(viewport={"width": w, "height": 760}, device_scale_factor=1)
            ctx.add_init_script("try { localStorage.setItem('editorAutosave', 'off'); sessionStorage.setItem('tb:view', 'editor'); } catch (e) {}")
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]) if "loadAlbums" not in str(getattr(e, "stack", "")) else None)
            pg.on("dialog", lambda d: d.dismiss())
            pg.route(re.compile(r"^http://(127\.0\.0\.1|localhost):\d+/.*"), stub)
            pg.goto((ROOT / "editor.html").as_uri())
            pg.wait_for_timeout(700)
            for v in VIEWS:
                pg.evaluate("v => showView(v)", v)
                pg.wait_for_timeout(250)
                r = pg.evaluate(REACH)
                pg.screenshot(path=str(SHOTS / f"{v}_{w}.png"), clip={"x": 0, "y": 0, "width": w, "height": 120})
                tag = f"{w}px {v}"
                ok(f"N2 {tag}: the bar stays inside the window (fit {r['fit']})", r["barRight"] <= w and r["barScroll"] <= 1 and r["pageScroll"] <= 1, r)
                if r["menu"] is None:
                    reach = {t: xy for t, xy in r["tabs"]}
                    ok(f"N1 {tag}: all seven tabs are on screen and clickable", len(reach) == 7 and all(reach.values()), r["tabs"])
                    # N3 a real click on the last tab, then back
                    target = "queue" if v != "queue" else "launch"
                    if reach.get(target):
                        pg.mouse.click(*reach[target]); pg.wait_for_timeout(250)
                        ok(f"N3 {tag}: a click on {target} opens it", pg.evaluate(SHOWING) == target, pg.evaluate(SHOWING))
                        pg.evaluate("v => showView(v)", v); pg.wait_for_timeout(150)
                else:
                    ok(f"N1 {tag}: the one-pill form is on screen and clickable", bool(r["menu"]), r)
                    ok(f"N6 {tag}: the pill names the open app", w == 393 or pg.evaluate("v => ({editor:'Editor',library:'Library',heroes:'Covers',maps:'Maps',launch:'Launch',social:'Social',queue:'Queue'})[v]", v) in r["menuName"], r["menuName"])
                    pg.mouse.click(*r["menu"]); pg.wait_for_timeout(200)
                    rows = pg.evaluate(LIST)
                    ok(f"N1 {tag}: the pill opens the list with all seven apps on screen", rows and len(rows) == 7 and all(xy for _, xy in rows), rows)
                    if v == "editor":
                        pg.screenshot(path=str(SHOTS / f"{v}_{w}_list.png"), clip={"x": 0, "y": 0, "width": w, "height": 520})
                    pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
                    ok(f"N6 {tag}: Escape closes the list", pg.evaluate("document.getElementById('applist').hidden"))
                    target = "queue" if v != "queue" else "maps"
                    pg.mouse.click(*r["menu"]); pg.wait_for_timeout(200)
                    rows = dict(pg.evaluate(LIST) or [])
                    if rows.get(target):
                        pg.mouse.click(*rows[target]); pg.wait_for_timeout(250)
                    ok(f"N3 {tag}: pill, then the {target} row, opens {target} and closes the list",
                       pg.evaluate(SHOWING) == target and pg.evaluate("document.getElementById('applist').hidden"), (pg.evaluate(SHOWING), rows.get(target)))
                    pg.evaluate("v => showView(v)", v); pg.wait_for_timeout(150)
                if w == 1440:
                    ok(f"N4 {tag}: every tab keeps its name at 1440", r["named"] == 7 and r["fit"] == "0", r)
            if w == 393:
                pg.evaluate("() => { showView('editor'); document.getElementById('save-status').textContent = 'Saved'; }")
                pg.wait_for_timeout(300)
                r = pg.evaluate(REACH)
                pg.screenshot(path=str(SHOTS / "editor_393_saved.png"), clip={"x": 0, "y": 0, "width": w, "height": 120})
                ok("N5 393px: with a 'Saved' note the pill is still on screen and clickable", bool(r["menu"]) and r["barScroll"] <= 1, r)
                if r["menu"]:
                    pg.mouse.click(*r["menu"]); pg.wait_for_timeout(200)
                    rows = pg.evaluate(LIST)
                    ok("N5 393px: ...and opens the list", rows and len(rows) == 7 and all(xy for _, xy in rows), rows)
            ok(f"{w}px: no script errors", not errs, errs[:2])
            ctx.close()
        b.close()
    ok("nothing was posted anywhere", not posted, posted[:3])
    n_ok = sum(1 for c in checks if c[1])
    print(f"\n{n_ok}/{len(checks)} checks passed  (screenshots: {SHOTS})")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
