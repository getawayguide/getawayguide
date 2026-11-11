r"""
Instagram STORIES: 9:16 frames at 1080x1920 for both output types of the editor's Social tab (Kevin, 2026-11-08: "is there
a version for IG stories too?"). A sibling of tools/ig_carousel.py and it borrows its pieces: every photo is cut from its
ORIGINAL to the exact pixel size of its box (Display P3 -> sRGB, the crop center Kevin set in the maker), the frames are
drawn in headless Chromium at 1x and saved as PNG with the sRGB profile embedded.

  python tools/ig_story.py <Country> render --kind carousel [--state DIR] [--out DIR]
      the TRAVEL BLOG post (carousel/main) as story frames: cover (photo full bleed, kicker + big title), route map (the
      day list under it), one frame per stop (its first photo full bleed over a green band with DAY / name / one line),
      the call to action ("Reply GUIDE": a story is answered, not commented)
      -> NN-<key>.png into --out (default .tmp/ig/<slug>/stories/carousel)
  python tools/ig_story.py <Country> render --kind free [--state DIR] [--out DIR] [--no-copy]
      the PERSONAL post (photos/<id> in final slots 1-10): one frame per slot, the photo full bleed from its crop center,
      the country as the kicker and the slot's note as the line (none: just the kicker)
      -> NN-<id>.png into --out (default .tmp/ig/<slug>/stories/free), copied to Pictures\IG\<slug>\stories unless --no-copy

SAFE AREAS: Instagram lays its own UI over the top ~250 px (profile, progress bar) and the bottom ~340 px (reply box,
share), so every frame keeps its type between y = 250 and y = 1580; photos may bleed under both.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ig_carousel  # noqa: E402  (cut, manifest, load_plan, chromium, save_png, FONTS_CSS)
import ig_post  # noqa: E402  (load_state, slugify, PICTURES)

ROOT = ig_carousel.ROOT
TMP = ig_carousel.TMP
W, H = 1080, 1920
SAFE_TOP, SAFE_BOTTOM = 250, 340
BOX = {"full": (W, H), "stop": (W, 1340), "cta": (W, 1000)}
out = ig_carousel.out

ST_CSS = """
.st { position: relative; width: 1080px; height: 1920px; overflow: hidden; background: #F7F7F3; color: #1C2821;
  font-family: 'Hanken Grotesk', -apple-system, 'Segoe UI', sans-serif; font-weight: 400; -webkit-font-smoothing: antialiased; text-rendering: geometricPrecision }
.st * { box-sizing: border-box }
.st .ph { position: absolute; overflow: hidden; background: #DCE7E0 }
.st .ph > img { display: block; width: 100%; height: 100% }
.st .k { font-weight: 600; font-size: 28px; letter-spacing: .24em; text-transform: uppercase; line-height: 1.2 }
.st .t { font-family: 'Newsreader', Georgia, serif; font-weight: 400; letter-spacing: -.02em; line-height: .98 }
.st .t em { font-style: italic; font-weight: 300 }
.st .mark { position: absolute; top: 270px; left: 72px; font-family: 'Newsreader', Georgia, serif; font-style: italic; font-weight: 300; font-size: 36px; line-height: 1 }
.st .shade { text-shadow: 0 1px 18px rgba(10,16,13,.4) }
/* cover + personal frames: the photo full bleed, the words at the foot of the safe area */
.st-cover .ph, .st-photo .ph { inset: 0 }
.st-cover .scrim, .st-photo .scrim { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(14,22,18,.45) 0%, rgba(14,22,18,0) 16%, rgba(14,22,18,0) 46%, rgba(14,22,18,.62) 66%, rgba(14,22,18,.9) 100%) }
.st-cover .mark, .st-photo .mark { color: #fff }
.st-cover .txt, .st-photo .txt { position: absolute; left: 72px; right: 72px; bottom: 400px; color: #fff }
.st-cover .k, .st-photo .k { color: #CFE6D8 }
.st-cover .t { font-size: 180px; margin-top: 26px }
.st-cover .rule { width: 96px; height: 4px; background: #8FC2A5; margin: 48px 0 0 }
.st-photo .t { font-size: 84px; margin-top: 26px; line-height: 1.04 }
.st-photo .k.big { font-size: 34px }
/* the route map: header, the map filling the middle, the day list on one column */
.st-map .body { position: absolute; left: 36px; right: 36px; top: 290px; bottom: 400px; display: flex; flex-direction: column }
.st-map .hd { flex: none; padding: 0 36px }
.st-map .hd .k { color: #2D6B50 }
.st-map .hd .t { font-size: 92px; font-weight: 300; margin-top: 16px; white-space: nowrap }
.st-map .mark { left: auto; right: 72px; top: 290px; color: #2D6B50; font-size: 32px }
.st-map .mapbox { flex: 1; min-height: 0; margin: 30px 0; display: flex; align-items: center; justify-content: center }
.st-map .mapbox svg { width: 100%; height: 100%; display: block }
.st-map .days { flex: none; margin: 0 36px; padding: 28px 0 0; list-style: none; border-top: 2px solid rgba(28,40,33,.14); display: grid; grid-template-columns: 1fr; gap: 14px }
.st-map .days li { display: flex; align-items: baseline; gap: 24px; min-width: 0 }
.st-map .days b { font-weight: 600; font-size: 20px; letter-spacing: .2em; text-transform: uppercase; color: #2D6B50; flex: 0 0 200px }
.st-map .days span { font-family: 'Newsreader', Georgia, serif; font-size: 36px; line-height: 1.1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis }
/* one stop: its first photo full width, the label in a green band that runs under Instagram's reply box */
.st-stop .ph { left: 0; top: 0; width: 1080px; height: 1340px }
.st-stop .bar { position: absolute; left: 0; right: 0; top: 1340px; bottom: 0; background: #2D6B50; color: #fff; padding: 60px 72px 0 }
.st-stop.nophoto .ph { display: none }
.st-stop.nophoto .bar { top: 0; padding-top: 1400px }
.st-stop .bar .k { color: #CFE6D8 }
.st-stop .bar .t { font-size: 96px; margin-top: 14px; white-space: nowrap }
.st-stop .bar .sub { font-size: 30px; line-height: 1.4; color: rgba(255,255,255,.86); margin-top: 18px; max-width: 900px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden }
.st-stop .mark { color: #fff }
.st-stop.nophoto .mark { color: rgba(255,255,255,.7) }
/* the call to action: photo on top, the pitch under it, the keyword as a pill */
.st-cta .ph { left: 0; top: 0; width: 1080px; height: 1000px }
.st-cta .txt { position: absolute; left: 72px; right: 72px; top: 1080px }
.st-cta .k { color: #2D6B50 }
.st-cta .t { font-size: 104px; font-weight: 300; margin-top: 18px }
.st-cta p { font-size: 34px; line-height: 1.45; margin: 26px 0 0; color: rgba(28,40,33,.86); max-width: 900px }
.st-cta .pill { display: inline-block; margin-top: 36px; background: #2D6B50; color: #fff; font-weight: 600; font-size: 27px; letter-spacing: .2em; text-transform: uppercase; padding: 22px 36px 21px; border-radius: 999px }
.st-cta .mark { color: #fff }
"""

ST_JS = """
window.ST = (() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const rich = (s) => esc(s).replace(/\\*([^*]+)\\*/g, '<em>$1</em>').replace(/\\s*\\|\\s*/g, '<br>');
  const mark = (txt, cls) => `<div class="mark shade ${cls || ''}">${esc(txt || 'getawayguide')}</div>`;
  const img = (key) => IMGS[key] ? `<img src="${IMGS[key]}" alt="">` : '';
  function cover(plan) {
    return `<div class="st st-cover" data-frame="cover"><div class="ph">${img('cover')}</div><div class="scrim"></div>${mark(plan.mark)}
      <div class="txt shade"><div class="k">${esc(plan.kicker)}</div><div class="t" data-fit="180" data-min="96" data-lines="3">${rich(plan.title)}</div><div class="rule"></div></div></div>`;
  }
  function map(plan, svg) {
    const m = plan.map || {};
    const days = (plan.stops || []).map(s => `<li><b>${esc(s.days)}</b><span>${esc(s.list || s.name)}</span></li>`).join('');
    return `<div class="st st-map" data-frame="map">${mark(plan.mark)}<div class="body"><div class="hd"><div class="k">${esc(m.kicker || 'The route')}</div><div class="t" data-fit="92" data-min="56" data-lines="1">${rich(m.title || 'The Route')}</div></div>
      <div class="mapbox">${svg || ''}</div><ul class="days">${days}</ul></div></div>`;
  }
  function stop(plan, s, i) {
    const has = !!IMGS['s' + i];
    return `<div class="st st-stop${has ? '' : ' nophoto'}" data-frame="s${i}"><div class="ph">${img('s' + i)}</div><div class="bar"><div class="k">${esc(s.days)}</div>
      <div class="t" data-fit="96" data-min="56" data-lines="1">${rich(s.name)}</div>${s.sub ? `<div class="sub">${esc(s.sub)}</div>` : ''}</div>${mark(plan.mark)}</div>`;
  }
  function cta(plan) {
    const c = plan.cta || {};
    return `<div class="st st-cta" data-frame="cta"><div class="ph">${img('cta')}</div>${mark(c.site || 'getawayguide.io')}<div class="txt"><div class="k">${esc(c.kicker || 'Save this for your trip')}</div>
      <div class="t" data-fit="104" data-min="64" data-lines="2">${rich(c.headline || 'Want the full guide?')}</div><p>${esc((c.body || '').replace(/^Comment\\b/, 'Reply'))}</p>
      <div class="pill">Reply &ldquo;${esc(c.keyword || 'GUIDE')}&rdquo;</div></div></div>`;
  }
  function photo(f) {
    return `<div class="st st-photo" data-frame="${esc(f.key)}"><div class="ph">${img(f.key)}</div><div class="scrim"></div>${mark(f.mark)}
      <div class="txt shade"><div class="k${f.text ? '' : ' big'}">${esc(f.kicker)}</div>${f.text ? `<div class="t" data-fit="84" data-min="44" data-lines="4">${esc(f.text)}</div>` : ''}</div></div>`;
  }
  function carousel(plan, svg) {
    const list = [{ key: 'cover', html: cover(plan) }];
    if (svg) list.push({ key: 'map', html: map(plan, svg) });
    (plan.stops || []).forEach((s, i) => { if (s.slide !== false) list.push({ key: 's' + i, html: stop(plan, s, i) }); });
    if (plan.cta && plan.cta.on !== false) list.push({ key: 'cta', html: cta(plan) });
    return list;
  }
  function free(frames) { return frames.map(f => ({ key: f.key, html: photo(f) })); }
  function fit(root) {
    for (const el of root.querySelectorAll('[data-fit]')) {
      let size = +el.dataset.fit; const min = +el.dataset.min || 40; const lines = +el.dataset.lines || 1;
      el.style.fontSize = size + 'px';
      const lh = () => parseFloat(getComputedStyle(el).lineHeight) || size;
      while (size > min && (el.scrollWidth > el.clientWidth + 1 || el.offsetHeight > lh() * lines + 2)) { size -= 2; el.style.fontSize = size + 'px'; }
    }
  }
  return { carousel, free, fit };
})();
"""


def first_photo(stop):
    for r in (stop.get("photos") or []):
        if r and r.get("id"):
            return r
    return None


def frames_carousel(slug, state):
    """[(key, ref, (w, h))] photo cuts + the plan and map svg for the Travel Blog post's story frames"""
    plan, svg = ig_carousel.load_plan(slug, state)
    cuts = []
    if (plan.get("cover") or {}).get("id"):
        cuts.append(("cover", plan["cover"], BOX["full"]))
    for i, s in enumerate(plan.get("stops") or []):
        if s.get("slide") is False:
            continue
        r = first_photo(s)
        if r:
            cuts.append(("s%d" % i, r, BOX["stop"]))
    c = plan.get("cta") or {}
    if c.get("on") is not False and (c.get("photo") or {}).get("id"):
        cuts.append(("cta", c["photo"], BOX["cta"]))
    return plan, svg, cuts


def frames_free(country, state):
    """[(key, ref, size)] + the frame texts for the Personal post: final slots 1-10 in order"""
    st = ig_post.load_state(state) if state else {"photos": {}, "post": {}, "captions": []}
    slots = []
    n_final = ig_post.nfinal_of(st.get("post"))                 # the Final row: 10 slides unless he added more (up to 20)
    for pid, doc in (st.get("photos") or {}).items():
        if doc.get("state") == "final" and doc.get("slot") and 1 <= int(doc["slot"]) <= n_final:
            slots.append((int(doc["slot"]), pid, doc))
    slots.sort()
    if not slots:
        raise SystemExit("no photos in the final slots 1-10: fill them in the Personal post first")
    cuts, texts = [], []
    for n, pid, doc in slots:
        key = "%02d-%s" % (n, pid)
        cuts.append((key, {"id": pid, "crop": doc.get("crop")}, BOX["full"]))
        texts.append({"key": key, "kicker": country, "text": (doc.get("note") or "").strip(), "mark": "getawayguide"})
    return cuts, texts


def cmd_render(args):
    slug = ig_post.slugify(args.country)
    man = ig_carousel.manifest(slug)
    if args.kind == "carousel":
        plan, svg, cuts = frames_carousel(slug, args.state)
        texts = None
        dest = Path(args.out) if args.out else TMP / slug / "stories" / "carousel"
    else:
        plan, svg = None, ""
        cuts, texts = frames_free(args.country, args.state)
        dest = Path(args.out) if args.out else TMP / slug / "stories" / "free"
    dest.mkdir(parents=True, exist_ok=True)
    work = dest / ".work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    imgs = {}
    for key, ref, (w, h) in cuts:
        p = man.get(ref["id"])
        if not p:
            out("  ! %s: %s is not in the manifest, left empty" % (key, ref["id"]))
            continue
        f = work / (key + ".png")
        ig_carousel.cut(ROOT / p["original"], ref.get("crop"), w, h).save(f, "PNG")
        imgs[key] = f.as_uri()
        out("  cut %-40s %4dx%-4d %s" % (key, w, h, p["original"]))
    build = ("ST.carousel(PLAN, SVG)" if args.kind == "carousel" else "ST.free(FRAMES)")
    html = ("<!doctype html><html><head><meta charset='utf-8'><link rel='stylesheet' href='%s'><style>%s</style>"
            "<style>html,body{margin:0;background:#888;overflow:hidden}.st{display:none}.st.on{display:block}</style></head><body><div id='root'></div>"
            "<script>const IMGS=%s, PLAN=%s, SVG=%s, FRAMES=%s;</script><script>%s</script><script>"
            "const list=%s; document.getElementById('root').innerHTML=list.map(s=>s.html).join(''); window.__keys=list.map(s=>s.key);"
            "Promise.all([document.fonts.ready, ...[...document.images].map(i=>i.decode().catch(()=>0))]).then(()=>{document.querySelectorAll('.st').forEach(e=>{e.classList.add('on');ST.fit(e);e.classList.remove('on');});"
            "window.__fonts=['400 30px Newsreader','italic 300 30px Newsreader','600 30px \"Hanken Grotesk\"'].map(f=>document.fonts.check(f,'Day'));window.__ready=1;});"
            "</script></body></html>") % (ig_carousel.FONTS_CSS, ST_CSS, json.dumps(imgs), json.dumps(plan, ensure_ascii=False), json.dumps(svg),
                                          json.dumps(texts, ensure_ascii=False), ST_JS, build)
    page_file = work / "render.html"
    page_file.write_text(html, encoding="utf-8")
    for old in dest.glob("*.png"):
        old.unlink()
    pw, b = ig_carousel.chromium()
    try:
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(page_file.as_uri())
        pg.wait_for_function("window.__ready === 1", timeout=30000)
        fonts = pg.evaluate("window.__fonts")
        if not all(fonts):
            out("  ! fonts missing in the render: %s" % fonts)
        keys = pg.evaluate("window.__keys")
        for n, key in enumerate(keys, 1):
            pg.evaluate("k => { document.querySelectorAll('.st').forEach(e => e.classList.toggle('on', e.dataset.frame === k)); "
                        "return new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); }", key)
            raw = pg.screenshot(type="png", clip={"x": 0, "y": 0, "width": W, "height": H}, animations="disabled")
            name = ("%02d-%s.png" % (n, key)) if args.kind == "carousel" else (key + ".png")
            size = ig_carousel.save_png(raw, dest / name)
            out("  story %02d %-40s %dx%d" % (n, key, size[0], size[1]))
        if errs:
            out("  ! page errors: %s" % errs)
    finally:
        b.close()
        pw.stop()
    shutil.rmtree(work, ignore_errors=True)
    if args.kind == "free" and not args.no_copy:
        pics = Path(args.copy_to) if args.copy_to else ig_post.PICTURES / slug / "stories"
        pics.mkdir(parents=True, exist_ok=True)
        for old in pics.glob("*.png"):
            old.unlink()
        for f in dest.glob("*.png"):
            shutil.copy2(f, pics / f.name)
        out("copied to %s" % pics)
    out("rendered %d story frames (1080x1920) into %s" % (len(keys), dest))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip(), formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("country")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="render the story frames")
    r.add_argument("--kind", choices=("carousel", "free"), required=True, help="carousel = the Travel Blog post's plan, free = the Personal post's slots")
    r.add_argument("--state", help="the store: an ArtifactData out_dir or the Social tab's .tmp/ig/<slug>/state (default: the seed output for carousel)")
    r.add_argument("--out", help="output folder")
    r.add_argument("--copy-to", help="(free) folder for the copies, default Pictures\\IG\\<slug>\\stories")
    r.add_argument("--no-copy", action="store_true", help="(free) no copy to Pictures")
    args = ap.parse_args()
    cmd_render(args)


if __name__ == "__main__":
    main()
