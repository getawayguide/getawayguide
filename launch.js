/* The Launch tab: the prelaunch checklist (Kevin, 2026-09-30).

   "a prelaunch checklist tab ... should list out all the checks claude does before launch ... it
   will show all the claude checks and the checks that I should do ... There should also be buttons
   taking me to the right tool to clean these up." Then: "how about I choose country and there is
   an overall country sub tab and identical sub tab for each article".

   So: choose a country. Claude's checks run once over every page of it (they launch together,
   which is what the relink and not-launching checks need), and each article's sub-tab shows the
   findings on that page; the Overview adds the country-wide checks and a table of every page.
   Kevin's checks are ticked per article, each showing what the page says today.
   The checks live in tools/prelaunch.py on the photo server. */
(function () {
  'use strict';
  const API = 'http://127.0.0.1:5003';
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = x => String(x == null ? '' : x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const store = {
    get(k) { try { return localStorage.getItem('launch:' + k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem('launch:' + k, v); } catch (e) {} }
  };
  const L = { defs: null, countries: [], country: null, tab: 'overview', results: {}, finished: null, job: null,
              current: null, rerun: null, kevin: { hints: {}, ticks: {} }, open: {}, sel: {}, busy: {},
              plan: 'keep', retire: null,
              loadSeq: 0, loading: { runs: false, hints: false }, starting: false, ranRels: null, detail: null,
              started: 0, pendingRerun: null, tickChain: {}, touched: {} };

  // ------------------------------------------------------------------ look
  const css = document.createElement('style');
  css.textContent = `
  #app-launch{--ln-bg:#F7F7F3;--ln-card:#fff;--ln-ink:#1C2821;--ln-mute:#6b7670;--ln-faint:#98a29c;--ln-line:#E7E6DF;
    --ln-green:#2D6B50;--ln-green-bg:#E8F1EC;--ln-amber:#9A6F12;--ln-amber-bg:#F7EFD9;--ln-red:#B0442F;--ln-red-bg:#F8E7E2;
    position:fixed;inset:0;z-index:80;display:grid;grid-template-rows:56px 1fr;grid-template-columns:minmax(0,1fr);background:var(--ln-bg);
    font-family:'Hanken Grotesk',sans-serif;color:var(--ln-ink)}
  #app-launch[hidden]{display:none!important}
  #app-launch>header{height:56px;padding:0 20px;background:#1C2821;color:#fff;display:flex;align-items:center;gap:18px;box-sizing:border-box;overflow:hidden}
  #app-launch>header .suite-mark{margin:0 6px 0 0}
  #launch-root{overflow-y:auto;overflow-x:hidden;min-height:0}
  .ln-wrap{max-width:1320px;margin:0 auto;padding:22px 28px 60px}
  .ln-top{display:flex;align-items:flex-end;gap:20px;flex-wrap:wrap}
  .ln-eyebrow{display:block;font-size:.6rem;font-weight:600;letter-spacing:.16em;text-transform:uppercase;color:var(--ln-mute);margin-bottom:4px}
  .ln-country{font-family:'Newsreader',Georgia,serif;font-size:1.9rem;font-weight:400;color:var(--ln-ink);background:transparent;border:0;
    padding:0 26px 0 0;margin:0;cursor:pointer;appearance:none;-webkit-appearance:none;line-height:1.15;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%231C2821' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 2px center}
  .ln-country:focus-visible{outline:2px solid var(--ln-green);outline-offset:4px;border-radius:4px}
  .ln-spacer{flex:1}
  .ln-when{font-size:.72rem;color:var(--ln-mute);font-variant-numeric:tabular-nums}
  .ln-btn{font:600 .62rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;border-radius:999px;
    padding:.62rem 1rem;border:1px solid var(--ln-line);background:var(--ln-card);color:var(--ln-ink);white-space:nowrap}
  .ln-btn:hover{border-color:var(--ln-green);color:var(--ln-green)}
  .ln-btn:focus-visible{outline:2px solid var(--ln-green);outline-offset:2px}
  .ln-btn.primary{background:var(--ln-green);border-color:var(--ln-green);color:#fff}
  .ln-btn.primary:hover{background:#24573F;color:#fff}
  .ln-btn[disabled]{opacity:.55;cursor:default}
  .ln-btn.sm{padding:.45rem .7rem;font-size:.56rem}
  /* the editor sidebar's tabs (editor.html .side-tabs): icon + label on a white bar, the open one a soft green tab with a rule */
  .ln-tabs{position:relative;margin:18px 0 22px;padding:8px 8px 0;background:#fff;border:1px solid var(--ln-line);border-bottom-color:rgba(28,40,33,.14);
    border-radius:12px 12px 0 0}
  /* one row that scrolls sideways; the bar itself shows no scroll bar, the arrows move it */
  .ln-tabstrip{display:flex;gap:2px;overflow-x:auto;overflow-y:hidden;scrollbar-width:none;overscroll-behavior-x:contain}
  .ln-tabstrip::-webkit-scrollbar{display:none}
  .ln-tabarrow{position:absolute;top:8px;bottom:0;width:44px;display:flex;align-items:center;border:0;padding:0;cursor:pointer;color:#4f5c54;z-index:2}
  .ln-tabarrow[hidden]{display:none}
  .ln-tabarrow.l{left:0;justify-content:flex-start;padding-left:8px;border-radius:12px 0 0 0;background:linear-gradient(to right,#fff 55%,rgba(255,255,255,0))}
  .ln-tabarrow.r{right:0;justify-content:flex-end;padding-right:8px;border-radius:0 12px 0 0;background:linear-gradient(to left,#fff 55%,rgba(255,255,255,0))}
  .ln-tabarrow span{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;background:#fff;border:1px solid var(--ln-line);box-shadow:0 1px 4px rgba(28,40,33,.12)}
  .ln-tabarrow:hover span{color:var(--ln-green);border-color:var(--ln-green)}
  .ln-tabarrow:focus-visible span{outline:2px solid var(--ln-green);outline-offset:2px}
  .ln-tabarrow svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
  .ln-tab{display:inline-flex;align-items:center;gap:6px;flex:none;font:500 12.5px/1 'Hanken Grotesk',sans-serif;color:#4f5c54;cursor:pointer;
    background:none;border:0;border-bottom:2px solid transparent;border-radius:8px 8px 0 0;padding:9px 12px 10px;white-space:nowrap;
    transition:background .15s,color .15s}
  .ln-tab .ti{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round;flex:none}
  .ln-tab:hover{background:#F4F6F4;color:var(--ln-ink)}
  .ln-tab.on{background:#EEF3EF;color:var(--ln-green);border-bottom-color:var(--ln-green)}
  .ln-tab:focus-visible{outline:2px solid var(--ln-green);outline-offset:-2px}
  .ln-tab .n{font:600 9.5px/1 'Hanken Grotesk',sans-serif;border-radius:999px;padding:2px 5px;font-variant-numeric:tabular-nums}
  .ln-tab .n.fail{background:var(--ln-red-bg);color:var(--ln-red)} .ln-tab .n.warn{background:var(--ln-amber-bg);color:var(--ln-amber)}
  .ln-tab .n.pass{background:var(--ln-green-bg);color:var(--ln-green)}
  .ln-tab .sep{width:1px;height:18px;background:var(--ln-line);margin:8px 4px 0;flex:none}
  .ln-head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin:0 0 18px}
  .ln-head h1{font-family:'Newsreader',Georgia,serif;font-weight:400;font-size:1.35rem;margin:0;text-wrap:balance}
  .ln-pill{font:600 .56rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.12em;text-transform:uppercase;padding:.32rem .55rem;border-radius:999px;background:#EFEFE9;color:var(--ln-mute)}
  .ln-pill.live{background:var(--ln-green-bg);color:var(--ln-green)}
  .ln-pill.retire{background:var(--ln-amber-bg);color:var(--ln-amber)}
  .ln-plan .ln-k{border-top:0}
  .ln-plan-body{padding:0 14px 14px 48px;font-size:.78rem;line-height:1.55;color:var(--ln-ink)}
  .ln-plan-body ol{margin:.4rem 0 .6rem;padding-left:1.1rem}
  .ln-plan-body li{margin:.2rem 0}
  .ln-plan-body .ln-note{margin-top:.5rem}
  .ln-tab .n.retire{background:var(--ln-amber-bg);color:var(--ln-amber);font-weight:500}
  .ln-cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:28px;align-items:start}   /* Claude's half, yours */
  @media (max-width:980px){.ln-cols{grid-template-columns:1fr}}
  .ln-col>h2{display:flex;align-items:baseline;gap:10px;font:600 .66rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.16em;text-transform:uppercase;margin:0 0 10px;color:var(--ln-ink)}
  .ln-col>h2 span{font-weight:500;letter-spacing:.02em;text-transform:none;font-size:.74rem;color:var(--ln-mute)}
  .ln-group{font:600 .58rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--ln-faint);margin:18px 0 6px}
  .ln-card{background:var(--ln-card);border:1px solid var(--ln-line);border-radius:12px;overflow:hidden}
  .ln-row{display:grid;grid-template-columns:22px minmax(0,1fr) auto;gap:12px;padding:12px 14px;border-top:1px solid var(--ln-line)}
  .ln-row:first-child{border-top:0}
  .ln-ic{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;font:700 .66rem/1 'Hanken Grotesk',sans-serif;margin-top:1px}
  .st-pass .ln-ic{background:var(--ln-green-bg);color:var(--ln-green)} .st-pass .ln-ic::before{content:'\\2713'}
  .st-warn .ln-ic{background:var(--ln-amber-bg);color:var(--ln-amber)} .st-warn .ln-ic::before{content:'!'}
  .st-fail .ln-ic{background:var(--ln-red-bg);color:var(--ln-red)} .st-fail .ln-ic::before{content:'\\2715'}
  .st-error .ln-ic{background:var(--ln-red-bg);color:var(--ln-red)} .st-error .ln-ic::before{content:'?'}
  .st-skip .ln-ic,.st-none .ln-ic{background:#EFEFE9;color:var(--ln-faint)} .st-skip .ln-ic::before{content:'\\2013'}
  .st-run .ln-ic{border:2px solid var(--ln-line);border-top-color:var(--ln-green);animation:ln-spin .8s linear infinite}
  @keyframes ln-spin{to{transform:rotate(360deg)}}
  @media (prefers-reduced-motion:reduce){.st-run .ln-ic{animation:none}}
  .ln-t{font-weight:600;font-size:.84rem;line-height:1.35}
  .ln-s{font-size:.78rem;color:var(--ln-mute);line-height:1.45;margin-top:2px}
  .st-fail .ln-s{color:var(--ln-red)} .st-warn .ln-s{color:var(--ln-amber)}
  .ln-why{font-size:.72rem;color:var(--ln-faint);line-height:1.45;margin-top:4px}
  .ln-acts{display:flex;gap:6px;align-items:flex-start;flex-wrap:wrap;justify-content:flex-end;max-width:260px}
  .ln-more{font:600 .6rem/1 'Hanken Grotesk',sans-serif;color:var(--ln-mute);background:none;border:0;cursor:pointer;padding:.5rem .3rem}
  .ln-more:hover{color:var(--ln-green)}
  .ln-items{grid-column:2 / 4;margin-top:4px;border-top:1px dashed var(--ln-line);padding-top:8px;display:flex;flex-direction:column;gap:6px}
  .ln-item{display:flex;gap:8px;align-items:baseline;font-size:.76rem;line-height:1.45;flex-wrap:wrap}
  .ln-item .pg{font-weight:600;font-size:.7rem;color:var(--ln-ink);background:#EFEFE9;border-radius:4px;padding:.1rem .35rem;white-space:nowrap}
  .ln-item code{font:.7rem/1.4 ui-monospace,Menlo,Consolas,monospace;color:var(--ln-mute);background:#F4F4EF;border-radius:4px;padding:.1rem .3rem;word-break:break-word}
  .ln-item .later{font-size:.7rem;color:var(--ln-faint)}
  .ln-item input{accent-color:var(--ln-green);margin:0;transform:translateY(2px)}
  .ln-item .go{font:600 .58rem/1 'Hanken Grotesk',sans-serif;color:var(--ln-green);background:none;border:0;cursor:pointer;padding:0;margin-left:auto;letter-spacing:.06em;text-transform:uppercase}
  .ln-note{font-size:.72rem;color:var(--ln-mute);margin-top:4px}
  .ln-k{display:grid;grid-template-columns:22px minmax(0,1fr) auto;gap:12px;padding:12px 14px;border-top:1px solid var(--ln-line);align-items:start}
  .ln-k:first-child{border-top:0}
  .ln-k input{width:18px;height:18px;margin:1px 0 0;accent-color:var(--ln-green);cursor:pointer}
  .ln-k input[data-auto],.ln-grid input[data-auto]{cursor:help}
  .ln-grid input{width:16px;height:16px;accent-color:var(--ln-green);cursor:pointer}
  .ln-grid{width:100%;border-collapse:collapse;font-size:.76rem}
  .ln-grid th{font:600 .54rem/1.2 'Hanken Grotesk',sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--ln-faint);text-align:center;padding:10px 6px;border-bottom:1px solid var(--ln-line);vertical-align:bottom}
  .ln-grid th:first-child{text-align:left;padding-left:14px}
  .ln-grid td{padding:8px 6px;border-top:1px solid var(--ln-line);text-align:center}
  .ln-grid td:first-child{text-align:left;padding-left:14px;font-weight:600}
  .ln-grid tr:first-child td{border-top:0}
  .ln-grid .na{color:var(--ln-faint)}
  .ln-k.done .ln-t{color:var(--ln-mute);text-decoration:line-through;text-decoration-color:rgba(28,40,33,.3)}
  .ln-h{font-size:.76rem;line-height:1.45;margin-top:3px;color:var(--ln-ink);word-break:break-word}
  .ln-h.ok::before{content:'\\2713  ';color:var(--ln-green);font-weight:700}
  .ln-h.no{color:var(--ln-amber)}
  .ln-h img{display:block;width:120px;aspect-ratio:2/3;object-fit:cover;border-radius:4px;margin-top:6px;background:#EFEFE9}
  .ln-at{font-size:.66rem;color:var(--ln-faint);margin-top:3px}
  .ln-table{width:100%;border-collapse:collapse;font-size:.8rem}
  .ln-table th{font:600 .56rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--ln-faint);text-align:left;padding:10px 14px;border-bottom:1px solid var(--ln-line)}
  .ln-table td{padding:10px 14px;border-top:1px solid var(--ln-line);vertical-align:middle}
  .ln-table tr:first-child td{border-top:0}
  .ln-table tbody tr{cursor:pointer} .ln-table tbody tr:hover td{background:#FAFAF7}
  .ln-table .nm{font-weight:600} .ln-table .k{font-size:.72rem;color:var(--ln-mute)}
  .ln-counts{display:flex;gap:6px;font-variant-numeric:tabular-nums}
  .ln-counts span{font:600 .64rem/1 'Hanken Grotesk',sans-serif;border-radius:999px;padding:.25rem .45rem}
  .ln-counts .f{background:var(--ln-red-bg);color:var(--ln-red)} .ln-counts .w{background:var(--ln-amber-bg);color:var(--ln-amber)} .ln-counts .p{background:var(--ln-green-bg);color:var(--ln-green)}
  .ln-bar{height:6px;border-radius:999px;background:#EFEFE9;overflow:hidden;width:110px;display:inline-block;vertical-align:middle;margin-right:8px}
  .ln-bar i{display:block;height:100%;background:var(--ln-green)}
  .ln-ov{margin-bottom:28px;overflow-x:auto}
  .ln-counts{flex-wrap:wrap} .ln-counts span{white-space:nowrap}
  @media (max-width:700px){.ln-table .c-st{display:none}.ln-table th,.ln-table td{padding:10px}.ln-bar{width:56px}}
  .ln-col h2.ln-h2-gap{margin-top:22px}
  .ln-btn[disabled]{cursor:default}
  .ln-empty{padding:60px 20px;text-align:center;color:var(--ln-mute);font-size:.9rem;line-height:1.6}
  .ln-toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:var(--ln-ink);color:#fff;font-size:.8rem;padding:.7rem 1rem;border-radius:10px;
    z-index:200;max-width:min(560px,90vw);box-shadow:0 8px 30px rgba(0,0,0,.18);white-space:pre-line}
  @media (max-width:700px){.ln-wrap{padding:16px 16px 40px}.ln-row,.ln-k{grid-template-columns:22px minmax(0,1fr)}.ln-acts{grid-column:2;justify-content:flex-start;max-width:none}.ln-items{grid-column:1 / 3}}
  `;
  document.head.appendChild(css);

  // ------------------------------------------------------------------ data
  const root = () => $('#launch-root');
  const country = () => L.countries.find(c => c.country === L.country);
  const pages = () => (country() || { articles: [] }).articles;
  const rels = () => pages().map(a => a.rel);
  const art = rel => pages().find(a => a.rel === rel);
  function shortTitle(a) {
    if (!a) return '';
    if (a.kind === 'country') return 'Country page';
    if (a.kind === 'itinerary') return 'Itinerary';
    if (a.kind === 'top10') return 'Top 10';
    if (a.kind === 'field-notes') return 'Field notes';
    let t = a.title.split(/\s*[—–:]\s*|\s+-\s+/)[0].replace(/^(The Ultimate Guide to|The)\s+/i, '');
    if (!/\s(&|and)\s/.test(t) && t.split(',').length === 2) t = t.split(',')[0];
    return t;
  }
  const short = rel => art(rel) ? shortTitle(art(rel)) : rel.split('/').pop().replace(/\.html$/, '');
  async function api(path, body) {
    const r = await fetch(API + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { cache: 'no-store' });
    if (!r.ok && r.status !== 400) { const e = new Error('HTTP ' + r.status); e.status = r.status; throw e; }
    return r.json();
  }
  function toast(msg, ms) {
    let t = $('.ln-toast'); if (!t) { t = document.createElement('div'); t.className = 'ln-toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
    t.textContent = msg; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => { t.hidden = true; }, ms || 4000);
  }

  // a check as one page sees it: that page's findings only
  const retiring = () => { const f = pages().find(a => a.kind === 'field-notes'); return f && L.plan === 'retire' ? f.rel : null; };
  function viewOf(id, rel) {
    const r = L.results[id];
    if (!r) return null;
    if (rel && L.ranRels && !L.ranRels.includes(rel) && !L.job) return null;   // not in that run: not checked
    const gone = retiring();
    if (!rel && gone && (r.items || []).some(i => i.page === gone)) {
      const its = r.items.filter(i => i.page !== gone);
      if (!its.length) return { status: 'pass', summary: 'Nothing on the pages that stay.', items: [], note: r.note };
      return Object.assign({}, r, { items: its });
    }
    if (!rel || ['skip', 'error'].includes(r.status)) return r;
    const items = (r.items || []).filter(i => i.page === rel);
    if (!items.length) return { status: 'pass', items: [], note: r.note,
      summary: (id === 'mentions' && r.per_page && r.per_page[rel]) || r.pass_text || ((L.defs.pass_text || {})[id]) || 'Clear on this page.' };
    if (items.every(i => i.ok)) return { status: 'pass', summary: items.map(i => i.detail).join(' · '), items: [] };
    let status = r.status === 'pass' ? 'warn' : r.status;
    if (items.some(i => i.sev)) status = items.some(i => i.sev === 'high') ? 'fail' : 'warn';
    const n = items.length, uniq = [...new Set(items.map(i => i.detail))];
    const summary = id === 'relink' ? `${n} link${n === 1 ? '' : 's'} could open your own article instead of Google Maps.`
      : id === 'maps-search' ? `${n} map link${n === 1 ? '' : 's'} still open${n === 1 ? 's' : ''} a Google search.`
      : id === 'voice' ? items.map(i => i.detail).join(' · ')
      : `${n} here: ${uniq.slice(0, 3).join('; ')}${uniq.length > 3 ? '…' : ''}`;
    return Object.assign({}, r, { status, items, summary });
  }
  function tally(rel) {
    const t = { fail: 0, warn: 0, pass: 0, error: 0 };
    for (const d of (L.defs ? L.defs.claude : [])) {
      if (rel && d.scope === 'country') continue;
      const v = viewOf(d.id, rel);
      if (v && t[v.status] !== undefined) t[v.status]++;
    }
    return t;
  }
  const onlyFieldNotes = () => pages().length > 0 && pages().every(a => a.kind === 'field-notes');
  // what the header says: checking (with the step, the page and the time), loading, or the last run
  function whenText() {
    if (L.job || L.starting) {
      const sec = L.started ? Math.max(0, Math.round((Date.now() - L.started) / 1000)) : 0;
      const t = sec ? ` · ${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}` : '';
      return `Checking${L.current ? ': ' + defTitle(L.current) : '…'}${L.detail ? ' (' + L.detail + ')' : ''}${t}`;
    }
    if (L.loading.runs) return 'Loading…';
    return L.finished ? 'Last run ' + new Date(L.finished).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : 'Not run yet';
  }
  const updateWhen = () => { const w = $('.ln-when', root()); if (w) w.textContent = whenText(); };
  // the element that had focus, so a redraw can put it back (every redraw used to drop focus to the page)
  function focusKey(el) {
    if (!el || !el.getAttribute) return null;
    for (const a of ['data-tick', 'data-tab', 'data-open', 'data-fix', 'data-sel', 'data-go', 'id']) {
      if (el.hasAttribute(a)) return { a, v: el.getAttribute(a), k: el.getAttribute('data-key') || el.getAttribute('data-rel') || '', tag: el.tagName };
    }
    return null;
  }
  function refocus(r, f) {
    if (!f) return;
    const esc_ = x => window.CSS && CSS.escape ? CSS.escape(x) : x.replace(/"/g, '\\"');
    let sel = `${f.tag.toLowerCase()}[${f.a}="${esc_(f.v)}"]`;
    if (f.k && f.a === 'data-tick') sel += `[data-key="${esc_(f.k)}"]`;
    else if (f.k && (f.a === 'data-go' || f.a === 'data-fix')) sel += `[data-rel="${esc_(f.k)}"]`;
    const el = r.querySelector(sel);
    if (el) try { el.focus({ preventScroll: true }); } catch (e) {}
  }
  const kevinDefs = scope => (L.defs ? L.defs.kevin : []).filter(k => k.scope === scope);
  // which of your checks a page has: no thumbnail on a country page or field notes, no maps check without a map
  function kevinFor(rel) {
    const a = art(rel), h = L.kevin.hints[rel] || {};
    return kevinDefs('article').filter(k => !(k.id === 'k-thumbs' && a && ['country', 'field-notes'].includes(a.kind)));
  }
  // the review check ticks itself: done exactly when the review store says it is clear
  const isDone = (rel, k) => k.auto ? !!((L.kevin.hints[rel] || {})[k.id] || {}).ok : !!(L.kevin.ticks[rel] || {})[k.id];
  // every one of your checks ticked on these pages (a retiring field notes page owes nothing)
  const allMine = rels_ => rels_.every(r => { const a = art(r); if (a && a.kind === 'field-notes' && L.plan === 'retire') return true;
                                            const d = kevinDone(r); return d.done === d.of; });
  function kevinDone(rel) {
    const defs = kevinFor(rel);
    return { done: defs.filter(k => isDone(rel, k)).length, of: defs.length };
  }

  // ------------------------------------------------------------------ render
  function render() {
    const r = root();
    if (!r) return;
    const keep = r.scrollTop;
    const focus = r.contains(document.activeElement) ? focusKey(document.activeElement) : null;
    const c = country();
    if (!c) { r.innerHTML = '<div class="ln-empty">Nothing to launch: no drafts found.</div>'; return; }
    const running = !!L.job || L.starting;
    const when = esc(whenText()), np = pages().length;
    const tabs = [['overview', 'Overview', tally(null)]].concat(pages().map(a => [a.rel, shortTitle(a), tally(a.rel), a]));
    r.innerHTML = `<div class="ln-wrap">
      <div class="ln-top">
        <div><span class="ln-eyebrow">Prelaunch checklist · ${pageCount()}</span>
          <select class="ln-country" id="ln-country" aria-label="Country">${countryOptions()}</select></div>
        <span class="ln-spacer"></span>
        <span class="ln-when" aria-live="polite">${when}</span>
        <button class="ln-btn primary" id="ln-run"${running ? ' disabled' : ''} title="Claude’s checks always run over every page of the country: they launch together">${running ? 'Running…' : `Check ${np === 1 ? 'the page' : 'all ' + np + ' pages'}`}</button>
      </div>
      <div class="ln-tabs"><button class="ln-tabarrow l" type="button" aria-label="Earlier tabs" hidden><span><svg viewBox="0 0 24 24"><path d="m15 6-6 6 6 6"/></svg></span></button>
      <nav class="ln-tabstrip" role="tablist" aria-label="Pages">${tabs.map(([k, label, t, a], i) => {
        const attrs = `data-tab="${esc(k)}" role="tab" aria-selected="${L.tab === k}" aria-controls="ln-body" tabindex="${L.tab === k ? 0 : -1}"`;
        if (a && a.kind === 'field-notes' && L.plan === 'retire' && !onlyFieldNotes())
          return `<button class="ln-tab${L.tab === k ? ' on' : ''}" ${attrs}>${icon('field-notes')}${esc(label)}<span class="n retire">retires</span></button>`;
        const bad = t.fail + t.error;
        const n = bad ? `<span class="n fail">${bad}</span>` : t.warn ? `<span class="n warn">${t.warn}</span>`
          : (L.finished || Object.keys(L.results).length) && !L.loading.hints && allMine(a ? [a.rel] : pages().map(p => p.rel)) ? '<span class="n pass">✓</span>' : '';
        return `<button class="ln-tab${L.tab === k ? ' on' : ''}" ${attrs}>${icon(a ? a.kind : 'overview')}${esc(label)}${n}</button>` + (i === 0 ? '<span class="sep" aria-hidden="true"></span>' : '');
      }).join('')}</nav>
      <button class="ln-tabarrow r" type="button" aria-label="More tabs" hidden><span><svg viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg></span></button></div>
      <div id="ln-body" role="tabpanel">${L.tab === 'overview' ? overview() : articleView(L.tab)}</div>
    </div>`;
    r.scrollTop = keep;
    tabArrows(r);
    fitSelect($('#ln-country', r));
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { const s = $('#ln-country', r); if (s) fitSelect(s); });
    wire();
    refocus(r, focus);
  }
  // the country menu in three groups: what is ready to launch, field-notes drafts, what has launched
  function countryGroup(x) {
    const draft = x.articles.some(a => !a.live), fn = x.articles.every(a => a.kind === 'field-notes');
    return !draft ? 'Launched' : fn ? 'Field notes drafts' : 'Ready to launch';
  }
  function countryOptions() {
    return ['Ready to launch', 'Field notes drafts', 'Launched'].map(g => {
      const xs = L.countries.filter(x => countryGroup(x) === g);
      return xs.length ? `<optgroup label="${g}">${xs.map(x => `<option value="${esc(x.country)}"${x.country === L.country ? ' selected' : ''}>${esc(x.label)}</option>`).join('')}</optgroup>` : '';
    }).join('');
  }
  // the tab row: keep its place across redraws, keep the open tab in view, show an arrow only on a
  // side that has tabs past it
  function tabArrows(r) {
    const strip = $('.ln-tabstrip', r), left = $('.ln-tabarrow.l', r), right = $('.ln-tabarrow.r', r);
    if (!strip) return;
    strip.scrollLeft = L.tabScroll || 0;
    const on = $('.ln-tab.on', strip);
    if (on && L.tabShown !== L.tab) {                 // a newly opened tab is brought into view, once
      const a = on.offsetLeft - strip.offsetLeft, b = a + on.offsetWidth;
      if (a < strip.scrollLeft + 40) strip.scrollLeft = Math.max(0, a - 48);
      else if (b > strip.scrollLeft + strip.clientWidth - 40) strip.scrollLeft = b - strip.clientWidth + 48;
      L.tabShown = L.tab;
    }
    const update = () => {
      left.hidden = strip.scrollLeft <= 1;
      right.hidden = strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - 1;
    };
    strip.onscroll = () => { L.tabScroll = strip.scrollLeft; update(); };
    const smooth = !(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
    left.onclick = () => strip.scrollBy({ left: -Math.max(160, strip.clientWidth * .7), behavior: smooth ? 'smooth' : 'auto' });
    right.onclick = () => strip.scrollBy({ left: Math.max(160, strip.clientWidth * .7), behavior: smooth ? 'smooth' : 'auto' });
    L.tabScroll = strip.scrollLeft;
    update();
    tabArrows.update = update;
  }
  window.addEventListener('resize', () => { if (tabArrows.update) tabArrows.update(); });

  function pageCount() {
    const d = pages().filter(a => !a.live).length, l = pages().length - d;
    return [d && `${d} draft${d === 1 ? '' : 's'}`, l && `${l} live`].filter(Boolean).join(', ');
  }
  // the select is as wide as the chosen country, so its arrow sits beside the name
  function fitSelect(sel) {
    const c = fitSelect.c || (fitSelect.c = document.createElement('canvas').getContext('2d'));
    const cs = getComputedStyle(sel);
    c.font = cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
    sel.style.width = Math.ceil(c.measureText(sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : '').width) + 34 + 'px';
  }
  // one icon per kind of page, drawn like the editor sidebar's (15px, 1.9 stroke)
  const ICONS = {
    overview: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    'field-notes': '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h11"/><path d="M9 8h6"/>',
    country: '<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/>',
    itinerary: '<circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 18h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7"/>',
    top10: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
    guide: '<path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>'
  };
  const icon = k => `<svg class="ti" viewBox="0 0 24 24" aria-hidden="true">${ICONS[k] || ICONS.guide}</svg>`;
  const defTitle = id => ((L.defs.claude.find(d => d.id === id) || {}).title || id);

  function checkRows(rel, scopeFilter) {
    const groups = [];
    for (const d of L.defs.claude) {
      if (scopeFilter && !scopeFilter(d)) continue;
      let g = groups.find(x => x.name === d.group);
      if (!g) groups.push(g = { name: d.group, rows: [] });
      g.rows.push(checkRow(d, rel));
    }
    return groups.map(g => `<div class="ln-group">${esc(g.name)}</div><div class="ln-card">${g.rows.join('')}</div>`).join('');
  }
  function checkRow(d, rel) {
    const running = L.job && (!L.rerun || L.rerun.includes(d.id));
    const v = viewOf(d.id, rel);
    const st = running && (!v || L.current === d.id || (L.rerun && L.rerun.includes(d.id))) ? 'run' : v ? v.status : 'none';
    const key = (rel || 'all') + '|' + d.id, open = L.open[key];
    const items = v && v.items ? v.items : [];
    const acts = [];
    if (v && v.tool && rel && items.length) acts.push(`<button class="ln-btn sm" data-go="${esc(v.tool.kind)}" data-rel="${esc(v.tool.rel || rel)}">${esc(v.tool.label)}</button>`);
    if (v && v.fix && items.length && items.some(i => i.apply !== false)) {      // nothing to apply yet: no button
      const busy = L.busy[d.id], wait = !busy && (L.job || L.starting);
      acts.push(`<button class="ln-btn sm primary" data-fix="${esc(d.id)}" data-rel="${esc(rel || '')}"${busy || wait ? ' disabled' : ''}${wait ? ' title="Wait for the run to finish"' : ''}>${busy ? 'Working…' : esc(v.fix.label)}</button>`);
    }
    if (items.length) acts.push(`<button class="ln-more" data-open="${esc(key)}" aria-expanded="${open ? 'true' : 'false'}">${open ? 'Hide' : 'Show ' + items.length}</button>`);
    const summary = st === 'run' ? 'Checking…' : v ? v.summary : L.loading.runs ? 'Loading…' : 'Not run yet.';
    return `<div class="ln-row st-${st}" data-id="${esc(d.id)}">
      <span class="ln-ic" aria-hidden="true"></span>
      <div><div class="ln-t" title="${esc(d.why)}">${esc(d.title)}</div><div class="ln-s">${esc(summary)}</div>
        ${open || !v || (v.status !== 'pass' && st !== 'run') ? `<div class="ln-why">${esc(d.why)}</div>` : ''}
        ${v && v.note && (open || !items.length) ? `<div class="ln-note">${esc(v.note)}</div>` : ''}</div>
      <div class="ln-acts">${acts.join('')}</div>
      ${open && items.length ? `<div class="ln-items">${items.slice(0, 120).map(i => itemRow(d.id, i, rel, v)).join('')}${items.length > 120 ? `<div class="ln-note">${items.length - 120} more.</div>` : ''}</div>` : ''}
    </div>`;
  }
  function itemRow(id, i, rel, v) {
    const pg = !rel && i.page ? `<span class="pg">${esc(short(i.page))}</span>` : '';
    if (id === 'mentions') {
      const k = relinkKey(i), on = L.sel[k] !== false;
      return `<label class="ln-item"><input type="checkbox" data-sel="${esc(k)}"${on ? ' checked' : ''}>${pg}
        <span>“${esc(i.text)}” \u2192 <b>${esc(short(i.target))}</b></span><code>${esc(i.value || '')}</code></label>`;
    }
    if (id === 'relink') {
      const k = relinkKey(i), on = L.sel[k] !== false;
      return `<label class="ln-item">${i.apply ? `<input type="checkbox" data-sel="${esc(k)}"${on ? ' checked' : ''}>` : ''}${pg}
        <span>“${esc(i.text)}” → <b>${esc(short(i.target))}</b></span><code>${esc(i.href)}</code>${i.apply ? '' : `<span class="later">${esc(i.why)}</span>`}</label>`;
    }
    const go = !rel && v && v.tool && i.page && art(i.page) ? `<button class="go" data-go="${esc(v.tool.kind)}" data-rel="${esc(i.page)}">Open</button>` : '';
    return `<div class="ln-item">${pg}<span>${esc(i.detail || '')}</span>${i.value ? `<code>${esc(String(i.value).slice(0, 180))}</code>` : ''}${go}</div>`;
  }
  const relinkKey = i => i.page + '|' + i.at + '|' + i.target;

  function kevinRows(key, defs, hints) {
    const t = L.kevin.ticks[key] || {};
    return `<div class="ln-card">${defs.map(k => {
      const h = (hints || {})[k.id], done = k.auto ? (h && h.ok ? { auto: true } : null) : t[k.id];
      const hint = h ? `<div class="ln-h${h.ok === true ? ' ok' : h.ok === false ? ' no' : ''}">${esc(h.text)}${h.img ? `<img src="${API}/site/${h.img.split('/').map(encodeURIComponent).join('/')}" alt="" loading="lazy">` : ''}</div>` : '';
      return `<div class="ln-k${done ? ' done' : ''}">
        <input type="checkbox" data-tick="${esc(k.id)}" data-key="${esc(key)}"${done ? ' checked' : ''}${k.auto ? ' data-auto="1" aria-disabled="true" title="Ticks itself when the review is clear"' : ''} aria-label="${esc(k.title)}">
        <div><div class="ln-t">${esc(k.title)}</div>${hint}<div class="ln-why">${esc(k.why)}</div>${done ? `<div class="ln-at">${done.auto ? 'Ticked automatically' : 'Done ' + new Date(done.at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</div>` : ''}</div>
        <div class="ln-acts">${k.tool ? `<button class="ln-btn sm" data-go="${esc(k.tool.kind)}" data-rel="${esc(key.startsWith('country:') ? '' : key)}">${esc(k.tool.label)}</button>` : ''}</div>
      </div>`;
    }).join('')}</div>`;
  }

  function fieldNotesView(rel) {
    const a = art(rel), solo = onlyFieldNotes(), keep = solo || L.plan !== 'retire', t = tally(rel);
    const pv = L.retire || {}, loading = L.retire === undefined, none = L.retire === false || (L.retire && !L.retire.live);
    const steps = (pv.steps || []).map(x => `<li>${esc(x)}</li>`).join('');
    const body = solo
      ? `<p>These field notes are the only page ${esc(country().label)} has, so they launch as field notes: <code>tools/publish_country.py</code> moves them live and wires them into the site.</p>`
      : keep
      ? `<p>They stay up after the launch and keep pointing readers at the articles. Uncheck this when a launch should take them down: the whole country goes live as an In-Depth Guide and these come down with it.</p>`
      : loading ? `<p>This launch takes them down.</p><p class="ln-note">Working out what retiring changes across the site…</p>`
      : none || !steps ? `<p class="ln-note">Nothing to retire: these field notes are not live, so they simply stay in Drafts when the country launches.</p>`
      : `<p>This launch takes them down:</p><ol>${steps}</ol>
         <div class="ln-note">The launch runs <code>tools/retire_field_notes.py ${esc(pv.country || '')}</code> after it publishes the country page${pv.country_page_live ? '' : ` (<code>${esc(pv.country_page || '')}</code> is not live yet: it goes live in the same launch)`}. Nothing changes until then.</div>`;
    const kd = kevinDone(rel);
    const mine = keep ? `<h2 class="ln-h2-gap">Your checks <span>${L.loading.hints ? 'loading…' : kd.done + ' of ' + kd.of + ' done'}</span></h2>${kevinRows(rel, kevinFor(rel), L.kevin.hints[rel])}` : '';
    return `<div class="ln-head"><h1>${esc(a.title)}</h1><span class="ln-pill${a.live ? ' live' : ''}">${a.live ? 'Live' : 'Draft'}</span>${keep ? '' : '<span class="ln-pill retire">Retires at launch</span>'}
        <span class="ln-spacer"></span><button class="ln-btn sm" data-go="editor" data-rel="${esc(rel)}">Open in the editor</button></div>
      <div class="ln-cols">
        <section class="ln-col"><h2>Claude checks <span>${keep ? summaryLine(t) : 'they no longer block the launch'}</span></h2>${checkRows(rel, d => d.scope !== 'country')}</section>
        <section class="ln-col"><h2>At launch</h2>
          <div class="ln-card ln-plan">
            ${solo ? '' : `<label class="ln-k"><input type="checkbox" id="ln-keep-fn"${keep ? ' checked' : ''} aria-label="Keep the field notes live after the launch">
              <div><div class="ln-t">Keep the field notes live</div><div class="ln-why">Checked: they stay up beside the articles. Unchecked: they retire when this country launches.</div></div><div></div></label>`}
            <div class="ln-plan-body">${body}</div>
          </div>${mine}
        </section>
      </div>`;
  }

  function articleView(rel) {
    const a = art(rel);
    if (!a) { L.tab = 'overview'; return overview(); }
    if (a.kind === 'field-notes') return fieldNotesView(rel);
    const t = tally(rel), kd = kevinDone(rel);
    const defs = kevinFor(rel);
    return `<div class="ln-head"><h1>${esc(a.title)}</h1><span class="ln-pill${a.live ? ' live' : ''}">${a.live ? 'Live' : 'Draft'}</span>
        <span class="ln-spacer"></span><button class="ln-btn sm" data-go="editor" data-rel="${esc(rel)}">Open in the editor</button></div>
      <div class="ln-cols">
        <section class="ln-col"><h2>Claude checks <span>${summaryLine(t)}</span></h2>${checkRows(rel, d => d.scope !== 'country')}</section>
        <section class="ln-col"><h2>Your checks <span>${L.loading.hints ? 'loading…' : kd.done + ' of ' + kd.of + ' done'}</span></h2>${kevinRows(rel, defs, L.kevin.hints[rel])}</section>
      </div>`;
  }
  const summaryLine = t => L.loading.runs ? 'loading…' : (L.finished || Object.keys(L.results).length)
    ? `${t.pass} passed · ${t.warn} to look at · ${t.fail} to fix${t.error ? ` · ${t.error} couldn’t run` : ''}` : 'not run yet';

  function kevinGrid() {
    const defs = kevinDefs('article');
    const short = { 'k-review': 'Review', 'k-search': 'Search', 'k-thumbs': 'Thumbnails' };
    const rows = pages().filter(a => !(a.kind === 'field-notes' && L.plan === 'retire' && !onlyFieldNotes())).map(a => {
      const mine = kevinFor(a.rel).map(k => k.id);
      return `<tr><td>${esc(shortTitle(a))}</td>${defs.map(k => {
        if (!mine.includes(k.id)) return '<td class="na" title="Not on this page">–</td>';
        const on = isDone(a.rel, k);
        return `<td><input type="checkbox" data-tick="${esc(k.id)}" data-key="${esc(a.rel)}"${on ? ' checked' : ''}${k.auto ? ' data-auto="1" aria-disabled="true" title="Ticks itself when the review is clear"' : ''} aria-label="${esc(k.title + ': ' + shortTitle(a))}"></td>`;
      }).join('')}</tr>`;
    }).join('');
    return `<div class="ln-card"><table class="ln-grid"><thead><tr><th>Page</th>${defs.map(k => `<th>${esc(short[k.id] || k.title)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function overview() {
    const c = country(), t = tally(null), ckey = 'country:' + c.country;
    const rows = pages().map(a => {
      const s = tally(a.rel), k = kevinDone(a.rel), run = L.finished || Object.keys(L.results).length;
      const gone = a.kind === 'field-notes' && L.plan === 'retire' && !onlyFieldNotes();
      const notRun = L.ranRels && !L.ranRels.includes(a.rel) && !L.job;
      return `<tr data-tab="${esc(a.rel)}" tabindex="0"><td><div class="nm">${esc(shortTitle(a))}</div><div class="k">${esc(a.rel.split('/').pop())}</div></td>
        <td class="c-st"><span class="ln-pill${a.live ? ' live' : ''}">${a.live ? 'Live' : 'Draft'}</span>${a.kind === 'field-notes' && L.plan === 'retire' ? ' <span class="ln-pill retire">Retires</span>' : ''}</td>
        <td>${gone ? '<span class="k">retires at launch</span>' : L.loading.runs ? '<span class="k">loading…</span>' : run && !notRun ? `<span class="ln-counts">${s.fail ? `<span class="f">${s.fail} to fix</span>` : ''}${s.error ? `<span class="f">${s.error} couldn’t run</span>` : ''}${s.warn ? `<span class="w">${s.warn} to look at</span>` : ''}${!s.fail && !s.warn && !s.error ? '<span class="p">all clear</span>' : ''}</span>` : '<span class="k">not run</span>'}</td>
        <td>${gone ? '' : L.loading.hints ? '<span class="k">loading…</span>' : `<span class="ln-bar"><i style="width:${k.of ? Math.round(k.done / k.of * 100) : 0}%"></i></span><span class="k">${k.done} of ${k.of}</span>`}</td></tr>`;
    }).join('');
    return `<div class="ln-ov ln-card"><table class="ln-table"><thead><tr><th>Page</th><th class="c-st">Status</th><th>Claude</th><th>You</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="ln-cols">
        <section class="ln-col"><h2>Claude checks <span>${summaryLine(t)}, across ${pages().length} page${pages().length === 1 ? '' : 's'}</span></h2>${checkRows(null)}</section>
        <section class="ln-col"><h2>Your checks <span>every page</span></h2>${kevinGrid()}</section>
      </div>`;
  }

  // ------------------------------------------------------------------ actions
  function wire() {
    const r = root();
    $('#ln-country', r).onchange = e => { L.country = e.target.value; store.set('country', L.country); L.tab = store.get('tab:' + L.country) || 'overview'; loadCountry(); };
    $('#ln-run', r).onclick = () => run();
    r.querySelectorAll('[data-tab]').forEach(b => {
      const go = () => { L.tab = b.dataset.tab; store.set('tab:' + L.country, L.tab); render(); root().scrollTop = 0; };
      b.onclick = go;
      b.onkeydown = e => {
        if (e.key === 'Enter' && b.tagName === 'TR') return go();
        if (b.getAttribute('role') !== 'tab' || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
        e.preventDefault();
        const all = [...r.querySelectorAll('[role=tab]')], i = all.indexOf(b);
        const next = e.key === 'Home' ? all[0] : e.key === 'End' ? all[all.length - 1] : all[(i + (e.key === 'ArrowRight' ? 1 : all.length - 1)) % all.length];
        L.tab = next.dataset.tab; store.set('tab:' + L.country, L.tab); render();
        const t = [...root().querySelectorAll('[role=tab]')].find(x => x.dataset.tab === L.tab);   // focus follows the arrow
        if (t) t.focus();
      };
    });
    r.querySelectorAll('[data-open]').forEach(b => b.onclick = () => { L.open[b.dataset.open] = !L.open[b.dataset.open]; render(); });
    r.querySelectorAll('[data-go]').forEach(b => b.onclick = () => go(b.dataset.go, b.dataset.rel));
    r.querySelectorAll('[data-sel]').forEach(b => b.onchange = () => { L.sel[b.dataset.sel] = b.checked; });
    r.querySelectorAll('[data-tick]').forEach(b => {
      // the review check ticks itself: a click explains instead of changing it (a disabled box would draw grey)
      // (inside a click the box has already flipped, so the state before the click is !b.checked)
      if (b.dataset.auto) b.onclick = e => { e.preventDefault(); toast(!b.checked ? 'This one ticks itself: the review is clear.' : 'This one ticks itself once every comment is resolved and every change decided.'); };
      else b.onchange = () => tick(b.dataset.key, b.dataset.tick, b.checked);
    });
    r.querySelectorAll('[data-fix]').forEach(b => b.onclick = () => fix(b.dataset.fix, b.dataset.rel));
    const k = $('#ln-keep-fn', r);
    if (k) k.onchange = () => setPlan(k.checked ? 'keep' : 'retire');
  }

  function tick(key, id, done) {
    const row = L.kevin.ticks[key] || (L.kevin.ticks[key] = {}), k = key + '|' + id;
    if (done) row[id] = row[id] || { at: new Date().toISOString() }; else delete row[id];
    L.touched[key] = true;
    render();
    L.tickChain[k] = (L.tickChain[k] || Promise.resolve()).then(() => api('/api/launch/tick', { key, id, done }))
      .then(r => { if (done && r && r.tick && row[id]) row[id] = r.tick; })
      .catch(() => toast('The photo server did not answer, so the tick was not saved.'));
  }

  async function setPlan(v) {
    try {
      const d = await api('/api/launch/plan', { country: L.country, field_notes: v });
      L.plan = d.field_notes;
    } catch (e) { toast('The photo server did not answer, so the choice was not saved.'); }
    render();
    if (Object.keys(L.results).length) {                       // relinks out of retiring field notes drop out
      if (L.job || L.starting) L.pendingRerun = ['relink']; else run(['relink']);
    }
  }

  async function openRel(rel) {
    let cur = '';
    try { cur = window.articleRelPath ? await window.articleRelPath() : ''; } catch (e) {}
    if (cur === rel) return true;
    if (window.showView) showView('editor');
    if (!window.__apOpen) return false;
    await window.__apOpen(rel);
    return true;
  }
  // every "take me there" button: the right app, the right article, the right panel
  async function go(kind, rel) {
    if (kind === 'maps') return showView('maps');
    if (rel) await openRel(rel);
    if (kind === 'covers') return showView('heroes');
    showView('editor');
    if (kind === 'review') sideTab('review');
    else if (kind === 'seo') sideTab('seo');
    else if (kind === 'thumbs') { sideTab('photos'); const p = document.getElementById('th-panel'); if (p && p.style.display === 'none' && window.thToggle) thToggle(); }
    else if (kind === 'preview' && window.openPreview) openPreview();
  }

  async function fix(id, rel) {
    const r = L.results[id];
    if (!r || !r.fix) return;
    const v = viewOf(id, rel || null);
    let body = { kind: r.fix.kind };
    if (r.fix.kind === 'relink' || r.fix.kind === 'mentions') {
      const items = (v.items || []).filter(i => i.apply && L.sel[relinkKey(i)] !== false);
      if (!items.length) return toast('Nothing selected.');
      body.items = items;
    } else {
      const pagesToFix = (r.fix.pages || []).filter(p => !rel || p === rel);
      if (!pagesToFix.length) return toast('Nothing on this page to fix.');
      if (r.fix.kind === 'photos' && !confirm('Run the photo pipeline on ' + pagesToFix.map(short).join(', ') + '?\nIt compresses and tiers every photo in the page, and can take a few minutes.')) return;
      body.pages = pagesToFix;
    }
    L.busy[id] = true; render();
    let out;
    try { out = await api('/api/launch/fix', body); } catch (e) { out = { ok: false, log: 'The photo server did not answer.' }; }
    L.busy[id] = false;
    toast((out.log || (out.ok ? 'Done.' : 'That did not work.')) + (out.ok && out.pages && out.pages.length ? '\nIf one of these is open in the editor, reopen it to see the change.' : ''), 7000);
    // re-run what the fix touches
    const again = { relink: ['relink', 'internal', 'unlaunched'], mentions: ['mentions', 'internal'], photos: ['photos', 'tiers', 'alt'], dates: ['dates'], share: ['cards'] }[r.fix.kind] || [id];
    run(again);
  }

  // ------------------------------------------------------------------ run
  async function run(only) {
    if (L.job || L.starting) return;                          // a double click started two full runs
    const want = L.country, body = { rels: rels() };
    if (only) body.only = only;
    L.starting = true; L.started = Date.now(); render();
    let r;
    try { r = await api('/api/launch/run', body); }
    catch (e) { L.starting = false; render(); return toast('The photo server is not running, so nothing can be checked.'); }
    L.starting = false;
    if (!r.ok) { render(); return toast(r.error || 'Could not start the checks.'); }
    if (L.country !== want) return;                            // switched away: coming back picks the run up
    if (!only) { L.results = {}; L.ranRels = rels(); }
    follow(r.job, only || null, Date.now());
  }

  // follow a run until it ends. Redraw only when a check finishes or the step changes (a redraw every
  // 700 ms dropped focus, selections and clicks); the elapsed time ticks on its own
  async function follow(job, only, started) {
    L.job = job; L.rerun = only; L.current = null; L.detail = null; L.started = started || Date.now();
    render();
    const want = L.country;
    let misses = 0, sig = '';
    while (L.job === job && L.country === want) {
      await new Promise(res => setTimeout(res, 700));
      if (L.job !== job || L.country !== want) return;
      let j;
      try { j = await api('/api/launch/job/' + job); misses = 0; }
      catch (e) {
        // the photo server restarted and forgot the run (or stopped answering): say so instead of spinning
        if (e.status === 404 || ++misses > 20) {
          if (L.job === job) { L.job = null; L.rerun = null; L.current = null; L.detail = null; render(); }
          toast('The run stopped: the photo server restarted. Run the checks again.', 7000);
          return;
        }
        updateWhen();
        continue;
      }
      if (L.job !== job || L.country !== want) return;
      Object.assign(L.results, j.results || {});
      L.current = j.current; L.detail = j.detail || null;
      if (j.state === 'done') {
        L.job = null; L.rerun = null; L.current = null; L.detail = null;
        L.finished = only ? (j.last_finished || L.finished || j.finished) : j.finished;   // a partial re-run is not a new "Last run"
        if (!only) L.ranRels = j.rels || L.ranRels;
        render();
        if (L.pendingRerun) { const p = L.pendingRerun; L.pendingRerun = null; run(p); }
        return;
      }
      const now = Object.keys(j.results || {}).join(',') + '|' + j.current + '|' + (j.detail || '');
      if (now !== sig) { sig = now; render(); } else updateWhen();
    }
  }

  async function loadCountry() {
    const seq = ++L.loadSeq, want = L.country, stale = () => L.loadSeq !== seq || L.country !== want;
    L.job = null; L.rerun = null; L.current = null; L.detail = null; L.starting = false; L.pendingRerun = null;
    L.results = {}; L.finished = null; L.ranRels = null; L.kevin = { hints: {}, ticks: {} }; L.touched = {}; L.open = {};
    L.plan = 'keep'; L.retire = null; L.loading = { runs: true, hints: true };
    L.tabScroll = 0; L.tabShown = null;                  // a new country starts its tab row at the left
    if (L.tab !== 'overview' && !art(L.tab)) { L.tab = 'overview'; store.set('tab:' + want, 'overview'); }   // a tab that no longer exists
    render();
    const body = { rels: rels() };
    // the hints (review status, titles) take a few seconds: they arrive on their own, after the rest
    api('/api/launch/kevin', body).then(kev => {
      if (stale()) return;
      const t = kev.ticks || {};
      for (const k of Object.keys(L.touched)) t[k] = L.kevin.ticks[k] || {};     // a tick made while loading wins
      L.kevin = { hints: kev.hints || {}, ticks: t }; L.loading.hints = false; render();
    }).catch(() => { if (!stale()) { L.loading.hints = false; render(); } });
    try {
      const [last, plan, act] = await Promise.all([api('/api/launch/last', body), api('/api/launch/plan?country=' + encodeURIComponent(want)),
                                                   api('/api/launch/active', body).catch(() => ({}))]);
      if (stale()) return;                               // another country was chosen meanwhile: its answers win
      L.plan = plan.field_notes || 'keep';
      L.results = last.results || {}; L.finished = last.finished || null; L.ranRels = last.rels || null;
      L.loading.runs = false;
      if (pages().some(a => a.kind === 'field-notes') && !onlyFieldNotes()) {   // what retiring does: slow, so after the rest
        L.retire = undefined;
        api('/api/launch/plan?preview=1&country=' + encodeURIComponent(want))
          .then(d => { if (!stale()) { L.retire = d.retire || false; render(); } })
          .catch(() => { if (!stale()) { L.retire = false; render(); } });
      }
      render();
      if (act && act.job) follow(act.job, act.only && act.only.length ? act.only : null, act.started ? Date.parse(act.started) : Date.now());
    } catch (e) { if (!stale()) { L.loading.runs = false; render(); toast('The photo server did not answer.'); } }
  }

  async function show() {
    const r = root();
    if (!r) return;
    if (!L.defs) {
      r.innerHTML = '<div class="ln-empty">Loading…</div>';
      try {
        const [d, a] = await Promise.all([api('/api/launch/checks'), api('/api/launch/articles')]);
        L.defs = d; L.countries = a.countries || [];
      } catch (e) {
        L.defs = null;
        r.innerHTML = '<div class="ln-empty"><b>The photo server isn’t running.</b><br>Double-click <b>Editor Suite.cmd</b> in your Travel Blog folder, then open this tab again.</div>';
        return;
      }
      // open on the country of the article in the editor, else the last one chosen
      let pick = store.get('country');
      try { const cur = window.articleRelPath && await window.articleRelPath(); const c = cur && L.countries.find(x => x.articles.some(a => a.rel === cur)); if (c) pick = c.country; } catch (e) {}
      L.country = (L.countries.find(x => x.country === pick) || L.countries.find(x => countryGroup(x) === 'Ready to launch') || L.countries[0] || {}).country || null;
      L.tab = store.get('tab:' + L.country) || 'overview';
      await loadCountry();
      return;
    }
    // coming back: the hints (review status, titles) may have changed while you were away
    try { const kev = await api('/api/launch/kevin', { rels: rels() }); L.kevin = { hints: kev.hints || {}, ticks: kev.ticks || {} }; } catch (e) {}
    render();
  }
  window.__launchShow = show;
  window.__launch = L;                                   // test seam
  // The editor can open straight onto this tab (#launch, or the view it remembers) while this deferred
  // script is still loading: it found no __launchShow to call and the tab stayed blank. Draw it now.
  { const la = document.getElementById('app-launch'); if (la && !la.hidden) show(); }
})();
