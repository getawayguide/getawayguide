/* The Queue tab (Kevin, 2026-11-06: "add the queue to the editor apps so I can see what I have queued for
   claude and add to-do list there (queue for me), add as new tab next to launch"; then "add review hub to editor
   instead of being a separate artifact. add it as part of my to-dos ... better keep them organized so I don't get
   overwhelmed or I can choose which categories to pay attention to"; "let me order the to do hub by dragging").

   Two columns. Claude's queue is .tmp/away_queue.md (tools/queue_tab.py parses it; every write is a compare-and-swap
   on the file's version, so an edit Claude made underneath is refused, never lost). Mine is Kevin's inbox in
   .tmp/kevin_todo.json: categories he can fold, hide and drag into his order, review items with their in-article
   screenshots (the Review Hub's viewer lives here), the questions Claude needs answered, and his own to-dos.
   Review: rounds is drawn as one collapsible group per COUNTRY, flag first (Kevin, 2026-11-07); the Shadow reviewer's
   items live there too (the server derives each item's `group`).

   2026-11-08 clean-up (Kevin: "lots of small font and excess wording that can be trimmed. reference other tabs that
   have minimalist design"): one type scale (14 px titles, 13 px body, 11 px labels), chrome copy cut to a word or an
   icon with a tooltip, summaries clamped to one line and expanded on click, less boxing (one surface per column, rows
   on hairlines), actions shown on hover (always on touch), and everything finished moved out of the working lists
   into ONE collapsed Done section per column, newest first, still viewable and undoable.

   Self-contained on purpose: it talks to /api/queue and /api/todo with plain fetch and needs nothing from
   editor.html, so the cloud editor can mount it too: set window.QUEUE_API before this script loads and call
   window.__queueMount(element). window.__queueOpenRel(rel), when defined, opens an article there. */
(function () {
  'use strict';
  const API = window.QUEUE_API || 'http://127.0.0.1:5003';
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = x => String(x == null ? '' : x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const pad = n => String(n).padStart(2, '0');
  const ymd = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  const today = () => ymd(new Date());
  const tomorrow = () => { const d = new Date(); d.setDate(d.getDate() + 1); return ymd(d); };
  const store = {
    get(k, dflt) { try { const v = localStorage.getItem('qt:' + k); return v == null ? dflt : JSON.parse(v); } catch (e) { return dflt; } },
    set(k, v) { try { localStorage.setItem('qt:' + k, JSON.stringify(v)); } catch (e) {} }
  };
  const Q = { root: null, queue: null, todo: null, open: {}, xo: new Set(), editing: null, addDetails: false, state: 'loading', error: '',
              loadedAt: 0, busy: false, drag: null, viewer: null, retry: null,
              catOpen: new Set(store.get('cats:open', [])), catHidden: new Set(store.get('cats:hidden', [])), doneOpen: {},
              cgOpen: new Set(store.get('cg:open', [])),      // "rounds/greece": the country groups he has open
              dev: store.get('viewer:dev', 'desktop') };

  // ------------------------------------------------------------------ look (the Launch tab's language, one type scale)
  const css = document.createElement('style');
  css.textContent = `
  #app-queue{--qt-bg:#F7F7F3;--qt-card:#fff;--qt-ink:#1C2821;--qt-mute:#6b7670;--qt-faint:#98a29c;--qt-line:#E7E6DF;--qt-soft:#FBFBF8;
    --qt-green:#2D6B50;--qt-green-bg:#E8F1EC;--qt-amber:#9A6F12;--qt-amber-bg:#F7EFD9;--qt-red:#B0442F;--qt-red-bg:#F8E7E2;--qt-blue:#2B5D8A;--qt-blue-bg:#E6EEF6;
    position:fixed;inset:0;z-index:80;display:grid;grid-template-rows:56px 1fr;grid-template-columns:minmax(0,1fr);background:var(--qt-bg);
    font-family:'Hanken Grotesk',sans-serif;color:var(--qt-ink);font-size:13px;line-height:1.45}
  #app-queue[hidden]{display:none!important}
  #app-queue>header{height:56px;padding:0 20px;background:#1C2821;color:#fff;display:flex;align-items:center;gap:18px;box-sizing:border-box;overflow:hidden}
  #app-queue>header .suite-mark{margin:0 6px 0 0}
  #queue-root{overflow-y:auto;overflow-x:hidden;min-height:0;-webkit-overflow-scrolling:touch;position:relative}
  .qt-wrap{max-width:1320px;margin:0 auto;padding:22px 28px 60px;box-sizing:border-box}
  .qt-top{display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:22px}
  .qt-eyebrow{display:block;font-size:.6rem;font-weight:600;letter-spacing:.16em;text-transform:uppercase;color:var(--qt-mute);margin-bottom:4px}
  .qt-top h1{font-family:'Newsreader',Georgia,serif;font-weight:400;font-size:1.9rem;margin:0;line-height:1.15}
  .qt-spacer{flex:1}
  .qt-when{font-size:12px;color:var(--qt-mute);font-variant-numeric:tabular-nums}
  .qt-btn{font:600 .62rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;border-radius:999px;
    padding:.62rem 1rem;border:1px solid var(--qt-line);background:var(--qt-card);color:var(--qt-ink);white-space:nowrap;min-height:34px}
  .qt-btn:hover{border-color:var(--qt-green);color:var(--qt-green)}
  .qt-btn:focus-visible{outline:2px solid var(--qt-green);outline-offset:2px}
  .qt-btn.primary{background:var(--qt-green);border-color:var(--qt-green);color:#fff}
  .qt-btn.primary:hover{background:#24573F;color:#fff}
  .qt-btn[disabled]{opacity:.55;cursor:default}
  .qt-btn.sm{padding:.45rem .7rem;font-size:.56rem;min-height:28px}
  .qt-cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:32px;align-items:start}
  @media (max-width:980px){.qt-cols{grid-template-columns:minmax(0,1fr)}}
  .qt-col>h2{display:flex;align-items:baseline;gap:10px;font:600 11px/1 'Hanken Grotesk',sans-serif;letter-spacing:.14em;text-transform:uppercase;margin:0 0 10px;color:var(--qt-ink);flex-wrap:wrap}
  .qt-col>h2 span{font-weight:400;letter-spacing:0;text-transform:none;font-size:13px;color:var(--qt-mute)}
  .qt-group{font:600 11px/1 'Hanken Grotesk',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--qt-faint);margin:22px 0 6px;display:flex;align-items:center;gap:8px}
  .n{font:600 11px/1 'Hanken Grotesk',sans-serif;background:#EFEFE9;border-radius:999px;padding:3px 7px;letter-spacing:0;color:var(--qt-mute);font-variant-numeric:tabular-nums}
  .n.new{background:#6EC99A;color:#1C2821} .n.needs{background:var(--qt-amber-bg);color:var(--qt-amber)} .n.zero{background:transparent;color:var(--qt-faint)}
  .qt-card{background:var(--qt-card);border:1px solid var(--qt-line);border-radius:12px;overflow:hidden}
  .qt-card+.qt-card{margin-top:12px}
  .qt-row{display:grid;gap:10px;padding:9px 10px 9px 4px;border-top:1px solid var(--qt-line);align-items:start;position:relative}
  .qt-row:first-child{border-top:0}
  .qt-item{grid-template-columns:18px 24px minmax(0,1fr) auto}
  .qt-todo{grid-template-columns:18px 18px minmax(0,1fr) auto}
  .drop-before::before,.drop-after::after{content:'';position:absolute;left:8px;right:8px;height:2px;background:var(--qt-green);border-radius:2px;z-index:2}
  .drop-before::before{top:-1px} .drop-after::after{bottom:-1px}
  .dragging{opacity:.45}
  body.qt-dragging,body.qt-dragging *{user-select:none!important;cursor:grabbing!important}
  .qt-grip{width:18px;height:22px;display:grid;place-items:center;color:#D5D9D6;cursor:grab;line-height:1;user-select:none;margin-top:1px;touch-action:none;flex:none}
  .qt-row:hover .qt-grip,.qt-cath:hover .qt-grip,.qt-cgh:hover .qt-grip{color:#AEB5B0} .qt-grip:hover{color:var(--qt-mute)}
  .qt-grip svg{width:12px;height:14px;fill:currentColor;pointer-events:none}
  .qt-num{width:24px;height:22px;border-radius:6px;display:grid;place-items:center;font:600 11px/1 'Hanken Grotesk',sans-serif;background:#EFEFE9;color:var(--qt-mute);font-variant-numeric:tabular-nums}
  .qt-num.nb::before{content:'';width:5px;height:5px;border-radius:50%;background:currentColor;opacity:.7}
  /* the md's own group headings ("Needs you"), drawn inside the list so an item drags across them */
  .qt-qgh{display:flex;align-items:baseline;gap:8px;padding:14px 12px 6px 32px;border-top:1px solid var(--qt-line);background:var(--qt-soft);font:600 12px/1.35 'Hanken Grotesk',sans-serif;color:var(--qt-mute);position:relative}
  .qt-qgh:first-child{border-top:0} .qt-qgh .nm{min-width:0;overflow-wrap:anywhere}
  .st-done .qt-num{background:var(--qt-green-bg);color:var(--qt-green)} .st-running .qt-num{background:var(--qt-blue-bg);color:var(--qt-blue)} .st-hold .qt-num{background:var(--qt-amber-bg);color:var(--qt-amber)}
  .qt-main{min-width:0}
  .qt-trow{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;cursor:pointer}
  .qt-trow.nohide{cursor:default}
  .qt-t,.qt-tt{font-weight:600;font-size:14px;line-height:1.35;overflow-wrap:anywhere}
  .st-done .qt-t,.qt-todo.done .qt-tt{color:var(--qt-mute);font-weight:500;text-decoration:line-through;text-decoration-color:rgba(28,40,33,.3)}
  .st-hold .qt-t{color:var(--qt-mute)}
  .qt-st{font:600 10px/1 'Hanken Grotesk',sans-serif;letter-spacing:.1em;text-transform:uppercase;padding:.3rem .5rem;border-radius:999px;white-space:nowrap;position:relative;top:-1px}
  .qt-st.done{background:var(--qt-green-bg);color:var(--qt-green)} .qt-st.running{background:var(--qt-blue-bg);color:var(--qt-blue)}
  .qt-st.hold{background:var(--qt-amber-bg);color:var(--qt-amber)} .qt-st.pending{display:none}
  .qt-st.running::before{content:'';display:inline-block;width:7px;height:7px;border-radius:50%;border:1.5px solid var(--qt-blue);border-top-color:transparent;margin-right:5px;vertical-align:-1px;animation:qt-spin .9s linear infinite}
  @keyframes qt-spin{to{transform:rotate(360deg)}} @media (prefers-reduced-motion:reduce){.qt-st.running::before{animation:none}}
  .qt-s{font-size:13px;color:var(--qt-mute);line-height:1.45;margin-top:1px;overflow-wrap:anywhere}
  .qt-s.clamp{display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden;cursor:pointer}
  .qt-s.clamp:hover{color:var(--qt-ink)}
  .qt-s a,.qt-d a,.qt-b a{color:var(--qt-green);text-decoration:underline;text-decoration-color:rgba(45,107,80,.35);overflow-wrap:anywhere}
  .qt-d{margin:6px 0 0;padding:8px 0 0;border-top:1px dashed var(--qt-line);font-size:13px;line-height:1.5;color:var(--qt-ink);cursor:default}
  .qt-d ul{margin:0;padding-left:1.1rem} .qt-d li{margin:.2rem 0}
  .qt-d .meta{font-size:12px;color:var(--qt-faint);margin-top:6px}
  .qt-acts{display:flex;gap:2px;align-items:center;flex-wrap:wrap;justify-content:flex-end;opacity:0;transition:opacity .12s}
  .qt-row:hover .qt-acts,.qt-row:focus-within .qt-acts,.qt-cath:hover .qt-acts,.qt-cath:focus-within .qt-acts,.qt-acts.show{opacity:1}
  @media (hover:none){.qt-acts{opacity:1}}
  .qt-ib{width:28px;height:28px;border-radius:6px;border:1px solid transparent;background:none;color:var(--qt-faint);cursor:pointer;display:grid;place-items:center;padding:0;font-size:12px;line-height:1}
  .qt-ib:hover{color:var(--qt-ink);background:#F3F3EE;border-color:var(--qt-line)} .qt-ib[disabled]{opacity:.3;cursor:default}
  .qt-ib:focus-visible{outline:2px solid var(--qt-green);outline-offset:1px}
  .qt-ib svg{width:13px;height:13px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
  .qt-ib.danger:hover{color:var(--qt-red);background:var(--qt-red-bg);border-color:transparent}
  .qt-ib.qt-hold:hover{color:var(--qt-amber)} .st-hold .qt-ib.qt-hold{color:var(--qt-amber)} .st-hold .qt-ib.qt-hold:hover{color:var(--qt-green)}
  .qt-chk{display:grid;grid-template-columns:22px minmax(0,1fr);gap:10px;padding:8px 12px;border-top:1px solid var(--qt-line);font-size:13px;line-height:1.45;cursor:pointer}
  .qt-chk:first-child{border-top:0}
  .qt-chk .box{width:15px;height:15px;border-radius:4px;border:1.5px solid #C4C9C5;margin-top:2px;display:grid;place-items:center;font-size:10px;color:#fff}
  .qt-chk.done .box{background:var(--qt-green);border-color:var(--qt-green)} .qt-chk.done .box::before{content:'\\2713'}
  .qt-chk.done{color:var(--qt-mute);text-decoration:line-through;text-decoration-color:rgba(28,40,33,.3)}
  .qt-b{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:start;padding:8px 10px 8px 12px;border-top:1px solid var(--qt-line);font-size:13px;line-height:1.5}
  .qt-b:first-child{border-top:0}
  .qt-b .qt-acts{opacity:1}
  .qt-add{display:flex;gap:6px;padding:8px 10px;border-top:1px solid var(--qt-line);background:var(--qt-soft);flex-wrap:wrap;align-items:center}
  .qt-add input[type=text],.qt-add input[type=date],.qt-add textarea,.qt-add select,.qt-edit input,.qt-reply textarea{font:400 13px/1.3 'Hanken Grotesk',sans-serif;color:var(--qt-ink);border:1px solid rgba(28,40,33,.14);border-radius:6px;padding:7px 9px;background:#fff;min-height:32px;box-sizing:border-box}
  .qt-add input:focus,.qt-add textarea:focus,.qt-add select:focus,.qt-edit input:focus,.qt-reply textarea:focus{outline:2px solid rgba(45,107,80,.35);outline-offset:0;border-color:var(--qt-green)}
  .qt-add .grow{flex:1 1 120px;min-width:0}
  .qt-add textarea{width:100%;min-height:56px;resize:vertical}
  .qt-add .full{flex:1 1 100%}
  .qt-add input[type=date]{flex:0 0 auto;width:118px;color:var(--qt-mute)} .qt-add input[type=date].has{color:var(--qt-ink)}
  .qt-add .lnk{flex:1 1 70px;min-width:0} .qt-add select{flex:0 1 112px;min-width:0}
  .qt-add .qt-btn{min-height:32px;padding:.5rem .9rem}
  .qt-add #qt-qa-group{width:190px;max-width:100%}            /* a long group name must not widen the column (393 px) */
  .qt-more{font:600 .6rem/1 'Hanken Grotesk',sans-serif;color:var(--qt-mute);background:none;border:0;cursor:pointer;padding:.5rem .3rem;letter-spacing:.06em;text-transform:uppercase}
  .qt-more:hover{color:var(--qt-green)}
  /* the inbox: category chips to hide/show, then one row per category on one surface */
  .qt-filter{display:flex;gap:4px;flex-wrap:wrap;margin:0 0 10px;align-items:center}
  .qt-fchip{font:500 11.5px/1 'Hanken Grotesk',sans-serif;border-radius:999px;padding:.4rem .6rem;border:1px solid transparent;background:#EFEFE9;color:var(--qt-ink);cursor:pointer;display:inline-flex;gap:5px;align-items:center;min-height:26px}
  .qt-fchip .n{background:transparent;padding:0;color:var(--qt-mute)} .qt-fchip.on{background:var(--qt-green-bg);color:var(--qt-green)} .qt-fchip.on .n{color:var(--qt-green)}
  .qt-fchip.off{background:transparent;color:var(--qt-faint);text-decoration:line-through;text-decoration-color:rgba(28,40,33,.3)} .qt-fchip:hover{border-color:var(--qt-green)}
  .qt-fchip:focus-visible{outline:2px solid var(--qt-green);outline-offset:1px}
  .qt-due{margin:0 0 12px}
  .qt-due a{color:var(--qt-ink);text-decoration:none;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:baseline;padding:7px 12px;border-top:1px solid var(--qt-line);cursor:pointer;font-size:13px}
  .qt-due a:first-of-type{border-top:0} .qt-due a:hover{background:var(--qt-soft)}
  .qt-due .c{font-size:12px;color:var(--qt-mute);white-space:nowrap} .qt-due .c.over{color:var(--qt-red)} .qt-due .c.today{color:var(--qt-amber)}
  .qt-cat{position:relative}
  .qt-cath{display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:6px;align-items:center;padding:4px 8px 4px 4px;min-height:42px;border-top:1px solid var(--qt-line)}
  .qt-cat:first-child>.qt-cath{border-top:0}
  .qt-cat.open>.qt-cath{background:var(--qt-soft)}
  .qt-catb{display:flex;align-items:center;gap:9px;background:none;border:0;padding:4px 2px;cursor:pointer;font:600 14px/1.2 'Hanken Grotesk',sans-serif;color:var(--qt-ink);text-align:left;min-width:0;min-height:28px}
  .qt-catb .chev,.qt-cgb .chev{width:6px;height:6px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg);transition:transform .15s;flex:none;color:var(--qt-faint)}
  .qt-cat.open>.qt-cath .chev,.qt-cg.open>.qt-cgh .chev{transform:rotate(45deg)}
  .qt-catb:focus-visible,.qt-cgb:focus-visible{outline:2px solid var(--qt-green);outline-offset:2px;border-radius:6px}
  .qt-catb .nm,.qt-cgb .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .cnt{display:flex;gap:4px;align-items:center;flex-wrap:wrap}
  .qt-cat>[data-list],.qt-cat>.qt-cgs{border-top:1px solid var(--qt-line)}
  .qt-cat .qt-row{padding-left:12px}
  /* a category grouped by country (Review: rounds): a flag row per country, its open items under it */
  .qt-cg{position:relative}
  .qt-cgh{display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:6px;align-items:center;padding:3px 8px 3px 12px;min-height:38px;border-top:1px solid var(--qt-line)}
  .qt-cg:first-child>.qt-cgh{border-top:0}
  .qt-cg.open>.qt-cgh{background:#F6F6F1}
  .qt-cgb{display:flex;align-items:center;gap:9px;background:none;border:0;padding:4px 2px;cursor:pointer;font:600 13px/1.2 'Hanken Grotesk',sans-serif;color:var(--qt-ink);text-align:left;min-width:0;min-height:28px}
  .qt-cgb .fl{width:20px;height:15px;border-radius:2px;box-shadow:0 0 0 1px rgba(28,40,33,.12);flex:none;object-fit:cover;background:#EFEFE9}
  .qt-cgb .fl.none{display:inline-block;background:repeating-linear-gradient(45deg,#E7E6DF 0 3px,#F3F3EE 3px 6px)}
  .qt-cg>[data-list="todo"]{border-top:1px solid var(--qt-line)}
  .qt-cg .qt-row{padding-left:22px}
  .qt-todo input[type=checkbox]{width:16px;height:16px;margin:3px 0 0;accent-color:var(--qt-green);cursor:pointer}
  .qt-tt{cursor:pointer}
  .qt-todo.st-new .qt-tt::before{content:'';display:inline-block;width:6px;height:6px;border-radius:50%;background:#2D6B50;margin:0 6px 1px 0;vertical-align:middle}
  .qt-chips{display:flex;gap:4px;flex-wrap:wrap;margin-top:4px;align-items:center}
  .qt-chips:empty{display:none}
  .qt-chip{font:500 11.5px/1 'Hanken Grotesk',sans-serif;border-radius:999px;padding:.3rem .5rem;background:#EFEFE9;color:var(--qt-mute);text-decoration:none;white-space:nowrap;max-width:240px;overflow:hidden;text-overflow:ellipsis;display:inline-flex;align-items:center;gap:5px;border:0;cursor:pointer;min-height:22px}
  .qt-chip svg{width:10px;height:10px;fill:none;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round;flex:none}
  .qt-chip img{width:16px;height:12px;border-radius:2px;object-fit:cover}
  .qt-chip.link{color:var(--qt-green);background:var(--qt-green-bg)} .qt-chip.link:hover{background:#DCEBE2}
  .qt-chip.view{color:#fff;background:var(--qt-ink)} .qt-chip.view:hover{background:#2D6B50}
  .qt-chip.due.over{color:var(--qt-red);background:var(--qt-red-bg)} .qt-chip.due.today{color:var(--qt-amber);background:var(--qt-amber-bg)}
  .qt-chip.plain{cursor:default} .qt-chip.st{color:var(--qt-amber);background:var(--qt-amber-bg);cursor:default} .qt-chip.doneon{cursor:default;color:var(--qt-green);background:var(--qt-green-bg)}
  .qt-note{font-size:13px;color:var(--qt-ink);border-left:2px solid var(--qt-line);padding:2px 8px;margin-top:5px;line-height:1.45;overflow-wrap:anywhere}
  .qt-note.clamp{display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}
  .qt-note b{font-weight:600;color:var(--qt-mute);font-size:11px;letter-spacing:.1em;text-transform:uppercase;margin-right:5px}
  .qt-stsel{font:600 10px/1 'Hanken Grotesk',sans-serif;letter-spacing:.08em;text-transform:uppercase;border:1px solid var(--qt-line);border-radius:999px;padding:.35rem 1.3rem .35rem .6rem;background:#fff;color:var(--qt-mute);cursor:pointer;appearance:none;-webkit-appearance:none;min-height:26px;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%236b7670' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 7px center}
  .qt-stsel.needs-changes{color:var(--qt-amber);border-color:#EBDDB4;background-color:var(--qt-amber-bg)} .qt-stsel.done{color:var(--qt-green);background-color:var(--qt-green-bg);border-color:transparent}
  .qt-stsel.new{color:var(--qt-green);border-color:rgba(45,107,80,.3)}
  .qt-cgsec{font:600 10.5px/1 'Hanken Grotesk',sans-serif;letter-spacing:.08em;text-transform:uppercase;color:var(--qt-faint);padding:12px 12px 6px;display:flex;gap:6px;align-items:center}
  .qt-cgsec .n{font-weight:500;letter-spacing:0}
  .qt-cgempty{font-weight:400;letter-spacing:0;text-transform:none;margin-left:6px;color:var(--qt-faint);opacity:.8}
  .qt-cgsec.drop-before,.qt-cgsec.drop-after{box-shadow:inset 0 -2px 0 var(--qt-green)}
  .qt-reply{display:flex;gap:6px;margin-top:6px;align-items:flex-start;flex-wrap:wrap}
  .qt-reply textarea{flex:1 1 200px;min-height:34px;resize:vertical;padding:7px 9px}
  .qt-edit{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
  .qt-edit input{padding:7px 9px;min-height:32px}
  .qt-edit .t{flex:1 1 100%} .qt-edit .l{flex:1 1 150px;min-width:0} .qt-edit input[type=date]{width:132px} .qt-edit .n{flex:1 1 100%;font:inherit;background:#fff;color:var(--qt-ink);border-radius:6px;padding:7px 9px}
  .qt-empty{padding:16px 14px;color:var(--qt-faint);font-size:13px}
  .qt-msg{padding:60px 20px;text-align:center;color:var(--qt-mute);font-size:.9rem;line-height:1.6;max-width:560px;margin:0 auto}
  .qt-msg b{color:var(--qt-ink)} .qt-msg code{font:.78rem/1.5 ui-monospace,Menlo,Consolas,monospace;background:#EFEFE9;border-radius:4px;padding:.1rem .35rem}
  .qt-fold summary{list-style:none;cursor:pointer;padding:10px 12px;font:600 11px/1 'Hanken Grotesk',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--qt-faint);display:flex;align-items:center;gap:8px}
  .qt-fold summary::-webkit-details-marker{display:none}
  .qt-fold summary::before{content:'';width:5px;height:5px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg);transition:transform .15s}
  .qt-fold[open] summary::before{transform:rotate(45deg)}
  .qt-fold summary:hover{color:var(--qt-ink)}
  .qt-fold[open]>summary{border-bottom:1px solid var(--qt-line)}
  .qt-fold summary .qt-more{margin-left:auto;padding:.2rem .3rem}
  .qt-done .qt-row{padding-left:12px}
  .qt-toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:#1C2821;color:#fff;font-family:'Hanken Grotesk',sans-serif;font-size:.8rem;padding:.7rem 1rem;border-radius:10px;
    z-index:200;max-width:min(560px,90vw);box-shadow:0 8px 30px rgba(0,0,0,.18);white-space:pre-line;line-height:1.45}
  .qt-toast.bad{background:#B0442F}            /* literal colors: the toast sits on <body>, outside #app-queue's variables */
  .app-tabs a .bdg{font:700 9.5px/1 'Hanken Grotesk',sans-serif;background:#6EC99A;color:#1C2821;border-radius:999px;padding:3px 5px;margin-left:-2px;min-width:8px;text-align:center;font-variant-numeric:tabular-nums}
  .app-tabs a .bdg[hidden]{display:none}
  /* the viewer: the item's in-article screenshot big, Desktop/Phone, previous/next through the category (the Review Hub's) */
  #qt-viewer{position:fixed;inset:0;z-index:120;background:rgba(20,26,22,.96);color:#fff;display:grid;grid-template-rows:auto 1fr auto;grid-template-columns:minmax(0,1fr);font-family:'Hanken Grotesk',sans-serif}
  #qt-viewer[hidden]{display:none}
  .qv-bar{display:flex;align-items:center;gap:12px;padding:10px 16px;min-height:52px;box-sizing:border-box;flex-wrap:wrap}
  .qv-bar .ttl{font-weight:600;font-size:.9rem;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 200px}
  .qv-bar .pos{font-size:.72rem;color:rgba(255,255,255,.6);white-space:nowrap;font-variant-numeric:tabular-nums}
  .qv-seg{display:inline-flex;border:1px solid rgba(255,255,255,.25);border-radius:999px;overflow:hidden}
  .qv-seg button{font:600 .6rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.1em;text-transform:uppercase;padding:.55rem .8rem;background:none;border:0;color:rgba(255,255,255,.7);cursor:pointer;min-height:30px}
  .qv-seg button.on{background:#fff;color:#1C2821}
  .qv-x,.qv-nav{width:40px;height:40px;border-radius:50%;border:1px solid rgba(255,255,255,.25);background:rgba(255,255,255,.06);color:#fff;cursor:pointer;display:grid;place-items:center;padding:0;flex:none}
  .qv-x:hover,.qv-nav:hover{background:rgba(255,255,255,.16)} .qv-nav[disabled]{opacity:.3;cursor:default}
  .qv-x svg,.qv-nav svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
  .qv-stage{position:relative;min-height:0;overflow:auto;display:flex;align-items:flex-start;justify-content:center;padding:0 64px;touch-action:pan-y;-webkit-overflow-scrolling:touch}
  .qv-stage img{display:block;max-width:100%;height:auto;background:#fff;margin:6px auto;border-radius:4px;box-shadow:0 10px 40px rgba(0,0,0,.4);user-select:none;-webkit-user-drag:none}
  .qv-stage.phone img{max-width:min(100%,430px)}
  .qv-stage .none{color:rgba(255,255,255,.6);padding:60px 20px;text-align:center;font-size:.9rem;line-height:1.6;max-width:460px}
  .qv-nav.l,.qv-nav.r{position:absolute;top:50%;transform:translateY(-50%)} .qv-nav.l{left:12px} .qv-nav.r{right:12px}
  .qv-foot{display:flex;gap:10px;align-items:center;padding:10px 16px 12px;flex-wrap:wrap;border-top:1px solid rgba(255,255,255,.1)}
  .qv-foot .sum{flex:1 1 320px;font-size:13px;line-height:1.45;color:rgba(255,255,255,.78);min-width:0}
  .qv-foot .qt-stsel{background-color:rgba(255,255,255,.08);color:#fff;border-color:rgba(255,255,255,.3)}
  .qv-foot .qt-stsel.done{background-color:#2D6B50;border-color:transparent} .qv-foot .qt-stsel.needs-changes{background-color:#9A6F12;color:#fff;border-color:transparent}
  .qv-foot input{font:400 13px/1.3 'Hanken Grotesk',sans-serif;color:#fff;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.25);border-radius:6px;padding:7px 10px;flex:1 1 200px;min-width:0;min-height:34px;box-sizing:border-box}
  .qv-foot input::placeholder{color:rgba(255,255,255,.45)}
  .qv-foot .qt-btn{background:transparent;color:#fff;border-color:rgba(255,255,255,.3)} .qv-foot .qt-btn:hover{border-color:#6EC99A;color:#6EC99A}
  @media (max-width:700px){.qt-wrap{padding:14px 16px 40px}.qt-top h1{font-size:1.5rem}.qt-item{grid-template-columns:18px 24px minmax(0,1fr);padding-right:8px}
    .qt-item .qt-acts{grid-column:2 / 4;justify-content:flex-start}.qt-todo{grid-template-columns:18px 18px minmax(0,1fr)}.qt-todo .qt-acts{grid-column:2 / 4;justify-content:flex-start}
    .qt-b{grid-template-columns:1fr}.qt-add input[type=date]{width:auto;flex:1 1 40%}.qt-add .lnk{flex:1 1 40%}.qt-add select{flex:1 1 100%}.qt-due a{grid-template-columns:1fr;gap:2px}.qt-due .c{white-space:normal}
    .qv-stage{padding:0 8px} .qv-nav.l,.qv-nav.r{display:none} .qv-bar .pos{order:3} .qv-foot .sum{flex-basis:100%}}
  /* the Push package (2026-11-10): its own section above the two columns, in the same language: one strip that folds,
     the calls as cards with their options as buttons, the changes by section on hairline rows, before/after shots */
  .pp{margin:0 0 26px}
  .pp[hidden]{display:none}
  .pp-head{display:flex;align-items:center;gap:8px 14px;flex-wrap:wrap;background:var(--qt-card);border:1px solid var(--qt-line);border-radius:12px;padding:6px 14px 6px 8px;min-height:48px;box-sizing:border-box}
  .pp-fold{display:flex;align-items:center;gap:9px;background:none;border:0;cursor:pointer;font:600 14px/1.2 'Hanken Grotesk',sans-serif;color:var(--qt-ink);padding:4px 2px;min-height:32px;text-align:left}
  .pp-fold .chev{width:6px;height:6px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg);transition:transform .15s;flex:none;color:var(--qt-faint);margin:0 2px 0 6px}
  .pp.open .pp-fold .chev{transform:rotate(45deg)}
  .pp-fold:focus-visible{outline:2px solid var(--qt-green);outline-offset:2px;border-radius:6px}
  .pp-fold .sub{font-weight:400;font-size:12.5px;color:var(--qt-mute)}
  .pp-stats{display:flex;gap:4px;flex-wrap:wrap;align-items:center}
  .pp-stat{font:500 11.5px/1 'Hanken Grotesk',sans-serif;border-radius:999px;padding:.42rem .62rem;background:#EFEFE9;color:var(--qt-mute);white-space:nowrap;font-variant-numeric:tabular-nums}
  .pp-stat b{font-weight:600;color:inherit}
  .pp-stat.todo{background:var(--qt-amber-bg);color:var(--qt-amber)} .pp-stat.done{background:var(--qt-green-bg);color:var(--qt-green)} .pp-stat.held{background:var(--qt-amber-bg);color:var(--qt-amber)}
  .pp-how{margin-left:auto;font-size:12.5px;color:var(--qt-mute)} .pp-how b{color:var(--qt-ink);font-weight:600}
  .pp-body{margin-top:14px}
  .pp-lead{display:flex;gap:10px 16px;align-items:flex-start;flex-wrap:wrap;margin:0 0 4px}
  .pp-lead p{flex:1 1 420px;margin:0;font-size:13px;line-height:1.5;color:var(--qt-mute);max-width:86ch}
  .pp-lead p b{color:var(--qt-ink);font-weight:600}
  .pp-dev{display:flex;gap:4px;align-items:center}
  .pp a{color:var(--qt-green);text-decoration:underline;text-decoration-color:rgba(45,107,80,.35);overflow-wrap:anywhere}
  .pp a.qt-chip{text-decoration:none}
  .pp-calls{display:grid;gap:12px} .pp-calls>.qt-card+.qt-card{margin-top:0}
  .pp-call{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,440px);gap:12px 22px;padding:14px 16px 16px 12px;border-left:3px solid #D9B861}
  .pp-call.answered{border-left-color:#6EC99A}
  .pp-call.noshots{grid-template-columns:minmax(0,1fr)}
  .pp-ctop{display:flex;gap:10px;align-items:flex-start}
  .pp-ctop .qt-num{flex:none;margin-top:1px}
  .pp-call.answered .qt-num{background:var(--qt-green-bg);color:var(--qt-green)}
  .pp-ct{font:600 14px/1.35 'Hanken Grotesk',sans-serif;margin:2px 0 0;flex:1;min-width:0;overflow-wrap:anywhere}
  .pp-cbody{padding-left:34px}
  .pp-diff{display:grid;gap:5px;margin:8px 0 0;font-size:13px;line-height:1.45}
  .pp-diff>div{display:grid;grid-template-columns:38px minmax(0,1fr);gap:8px;align-items:baseline}
  .pp-diff .lb{font:600 10px/1 'Hanken Grotesk',sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--qt-faint)}
  .pp-diff .lbl{font-weight:600;color:var(--qt-ink);margin:4px 0 -2px;grid-template-columns:1fr}
  .pp-diff del{color:#8A6A5A;text-decoration-thickness:1px;text-decoration-color:rgba(138,106,90,.6)} .pp-diff ins{color:var(--qt-green);text-decoration:none;font-weight:500}
  .pp-note{font-size:13px;color:var(--qt-mute);margin:8px 0 0;line-height:1.5;overflow-wrap:anywhere}
  .pp-list{margin:6px 0 0;padding-left:1.1rem;font-size:13px;line-height:1.5;color:var(--qt-ink)} .pp-list li{margin:.2rem 0;overflow-wrap:anywhere}
  .pp-opts{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
  .pp-opt{font:500 13px/1.25 'Hanken Grotesk',sans-serif;border-radius:999px;padding:.45rem .85rem;border:1px solid rgba(28,40,33,.16);background:#fff;color:var(--qt-ink);cursor:pointer;min-height:34px;display:inline-flex;align-items:center;gap:7px;text-align:left;max-width:100%;box-sizing:border-box}
  .pp-opt:hover{border-color:var(--qt-green);color:var(--qt-green)}
  .pp-opt:focus-visible{outline:2px solid var(--qt-green);outline-offset:2px}
  .pp-opt .rec{font:600 9.5px/1 'Hanken Grotesk',sans-serif;letter-spacing:.08em;text-transform:uppercase;color:var(--qt-green);background:var(--qt-green-bg);border-radius:999px;padding:.28rem .42rem;flex:none}
  .pp-opt.on{background:var(--qt-green);border-color:var(--qt-green);color:#fff}
  .pp-opt.on .rec{background:rgba(255,255,255,.22);color:#fff}
  .pp-opt.on::before{content:'';width:9px;height:5px;border-left:2px solid currentColor;border-bottom:2px solid currentColor;transform:rotate(-45deg);margin:-3px 0 0 -1px;flex:none}
  .pp-hint{font-size:12px;color:var(--qt-faint);margin-top:7px}
  .pp-ans{font-size:12px;color:var(--qt-mute);margin-top:7px}
  .pp .qt-reply{margin-top:10px} .pp .qt-reply textarea{min-height:34px}
  .pp .qt-note{margin-top:10px}
  .pp-shots{min-width:0}
  .pp-ba{display:grid;grid-template-columns:minmax(0,1fr);gap:8px}
  .pp-ba.phone{grid-template-columns:repeat(2,minmax(0,1fr))}
  .pp-shot{margin:0;min-width:0}
  .pp-shot figcaption{font:600 10px/1 'Hanken Grotesk',sans-serif;letter-spacing:.1em;text-transform:uppercase;margin:0 0 5px;color:#8A6A5A}
  .pp-shot.a figcaption{color:var(--qt-green)}
  .pp-img{display:block;width:100%;padding:0;border:1px solid var(--qt-line);border-radius:6px;background:#fff;cursor:zoom-in;overflow:hidden;box-sizing:border-box}
  .pp-img:hover{border-color:var(--qt-green)} .pp-img:focus-visible{outline:2px solid var(--qt-green);outline-offset:1px}
  .pp-img img{display:block;width:100%;height:auto;max-height:300px;object-fit:cover;object-position:top}
  .pp-ba.phone .pp-img img{max-height:340px}
  .pp-empty{font-size:12px;color:var(--qt-faint);border:1px dashed #D5D9D6;border-radius:6px;padding:16px 8px;text-align:center}
  .pp-tier{font-size:11.5px;color:var(--qt-faint);margin-top:5px;display:flex;gap:6px;flex-wrap:wrap}
  .pp-foot{font-size:12.5px;color:var(--qt-mute);margin:10px 2px 0;line-height:1.5}
  .pp-sec{margin-top:12px}
  .pp-sec summary .cnt{display:flex;gap:4px;margin-left:4px;letter-spacing:0;text-transform:none}
  .pp-sintro{margin:0;padding:10px 12px 10px;font-size:13px;line-height:1.5;color:var(--qt-mute);border-bottom:1px solid var(--qt-line)}
  .pp-sub{font:600 11px/1 'Hanken Grotesk',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--qt-faint);padding:16px 12px 8px;border-top:1px solid var(--qt-line);background:var(--qt-soft)}
  .pp-chg{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,400px);gap:12px 20px;padding:12px;border-top:1px solid var(--qt-line);align-items:start}
  .pp-sintro+.pp-chg,.pp-sec>summary+.pp-chg{border-top:0}
  .pp-chg.noshots{grid-template-columns:minmax(0,1fr)}
  .pp-chg.held{background:#FDF8EC;box-shadow:inset 3px 0 0 #D9B861}
  .pp-chg.ok .pp-ttl::after{content:'';display:inline-block;width:9px;height:5px;border-left:2px solid var(--qt-green);border-bottom:2px solid var(--qt-green);transform:rotate(-45deg);margin:0 0 3px 8px}
  .pp-ttl{font-weight:600;font-size:14px;line-height:1.35;overflow-wrap:anywhere}
  .pp-wasnow{font-size:12.5px;color:var(--qt-mute);display:grid;grid-template-columns:38px minmax(0,1fr);gap:2px 8px;margin-top:6px;overflow-wrap:anywhere;align-items:baseline}
  .pp-wasnow .lb{font:600 10px/1 'Hanken Grotesk',sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--qt-faint)}
  .pp-acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;align-items:center}
  .pp-tg{font:600 .58rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.1em;text-transform:uppercase;border-radius:999px;padding:.5rem .75rem;border:1px solid var(--qt-line);background:#fff;color:var(--qt-mute);cursor:pointer;min-height:30px;display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
  .pp-tg svg{width:11px;height:11px;fill:none;stroke:currentColor;stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round;flex:none}
  .pp-tg:hover{border-color:var(--qt-green);color:var(--qt-green)} .pp-tg:focus-visible{outline:2px solid var(--qt-green);outline-offset:1px}
  .pp-tg.ok.on{background:var(--qt-green-bg);border-color:transparent;color:var(--qt-green)}
  .pp-tg.hold.on{background:var(--qt-amber-bg);border-color:transparent;color:var(--qt-amber)}
  .pp-noteform{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap;align-items:flex-start}
  .pp-noteform input{flex:1 1 220px;min-width:0;font:400 13px/1.3 'Hanken Grotesk',sans-serif;color:var(--qt-ink);border:1px solid rgba(28,40,33,.14);border-radius:6px;padding:7px 9px;background:#fff;min-height:32px;box-sizing:border-box}
  .pp-noteform input:focus{outline:2px solid rgba(45,107,80,.35);outline-offset:0;border-color:var(--qt-green)}
  .pp-sum{display:grid;grid-template-columns:150px minmax(0,1fr) auto;gap:4px 14px;padding:9px 12px;border-top:1px solid var(--qt-line);font-size:13px;line-height:1.45;align-items:baseline}
  .pp-sum:first-child{border-top:0} .pp-sum .g{font-weight:600} .pp-sum .f{color:var(--qt-mute);font-size:12px;text-align:right;font-variant-numeric:tabular-nums}
  .pp-check{display:grid;grid-template-columns:48px minmax(0,1fr);gap:10px;padding:9px 12px;border-top:1px solid var(--qt-line);font-size:13px;line-height:1.5;align-items:baseline}
  .pp-check:first-child{border-top:0}
  .pp-pill{font:600 9.5px/1 'Hanken Grotesk',sans-serif;letter-spacing:.08em;text-transform:uppercase;text-align:center;padding:.32rem 0;border-radius:4px;background:var(--qt-green-bg);color:var(--qt-green)}
  .pp-pill.fix{background:var(--qt-amber-bg);color:var(--qt-amber)}
  .pp-run>span:last-child{display:flex;gap:10px;align-items:center;flex-wrap:wrap;color:var(--qt-mute)}
  .pp-clear{padding:12px 14px;font-size:13px;color:var(--qt-green);background:var(--qt-green-bg)}
  .pp-state{margin-top:8px;font-size:12.5px;line-height:1.45;padding:6px 9px;border-radius:6px}
  .pp-state.work{background:var(--qt-amber-bg);color:var(--qt-amber)} .pp-state.done{background:var(--qt-green-bg);color:var(--qt-green)}
  .pp-state b{font-weight:600;margin-right:4px}
  .pp-more{margin-top:6px;font-size:12.5px} .pp-more summary{cursor:pointer;color:var(--qt-faint);font-size:12px;width:max-content}
  .pp-more .pp-note{margin-top:4px}
  .pp-srow{border-top:1px solid var(--qt-line)} .pp-sub+.pp-srow{border-top:0}
  .pp-sbtn{all:unset;box-sizing:border-box;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:4px 16px;width:100%;padding:8px 12px;cursor:pointer;font-size:13px;line-height:1.4}
  .pp-sbtn:hover{background:rgba(28,40,33,.03)} .pp-sbtn:focus-visible{outline:2px solid rgba(45,107,80,.35);outline-offset:-2px}
  .pp-st{color:var(--qt-ink);display:flex;gap:8px;align-items:baseline;min-width:0} .pp-st .qt-num{flex:none}
  .pp-sd{color:var(--qt-mute);min-width:0;overflow-wrap:anywhere} .pp-sa{color:var(--qt-faint);margin-right:6px}
  .pp-sa::after{content:' →'}
  .pp-srow.on .pp-sbtn{background:rgba(28,40,33,.03)} .pp-sfull{padding:0 0 4px} .pp-sfull .pp-call{margin:6px 10px 10px;border:1px solid var(--qt-line)}
  @media (max-width:700px){.pp-sbtn{grid-template-columns:minmax(0,1fr)}}
  .pp-steps{margin:0;padding:10px 14px 12px 2.1rem;font-size:13px;line-height:1.5} .pp-steps li{margin:.3rem 0} .pp-steps li::marker{font-weight:600;color:var(--qt-green)}
  .pp-small{font-size:12px;color:var(--qt-faint);margin:12px 2px 0;line-height:1.5}
  .pp-msg{font-size:13px;color:var(--qt-mute)}
  @media (max-width:1100px){.pp-call{grid-template-columns:minmax(0,1fr)}.pp-call .pp-shots{padding-left:34px}}
  @media (max-width:900px){.pp-chg{grid-template-columns:minmax(0,1fr)}}
  @media (max-width:700px){.pp-how{margin-left:0;flex-basis:100%;padding:0 0 4px 8px}.pp-call{padding:12px 12px 14px 10px}.pp-cbody,.pp-call .pp-shots{padding-left:0}
    .pp-sum{grid-template-columns:minmax(0,1fr)}.pp-sum .f{text-align:left}.pp-head{padding-right:10px}}
  /* the viewer, for a push item: before and after together, the call's options or the change's flags in the footer */
  .qv-pair{display:grid;gap:16px;width:100%;max-width:1180px;margin:6px auto 12px;align-content:start}
  .qv-pair.phone{grid-template-columns:repeat(2,minmax(0,430px));justify-content:center}
  .qv-pair figure{margin:0;min-width:0;justify-self:center;max-width:100%}
  .qv-foot .sum a{color:#8FD3AE;text-decoration:underline;text-decoration-color:rgba(143,211,174,.4)}
  .qv-pair figcaption{font:600 .62rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.12em;text-transform:uppercase;color:#E6C3B4;margin:0 0 7px}
  .qv-pair .a figcaption{color:#8FD3AE}
  .qv-pair img{display:block;max-width:100%;height:auto;background:#fff;border-radius:4px;box-shadow:0 10px 40px rgba(0,0,0,.4);margin:0;user-select:none;-webkit-user-drag:none}
  .qv-pair .none{color:rgba(255,255,255,.55);border:1px dashed rgba(255,255,255,.25);border-radius:6px;padding:28px 12px;text-align:center;font-size:.85rem}
  #qt-viewer .pp-opt{background:transparent;color:#fff;border-color:rgba(255,255,255,.3);min-height:32px;font-size:12.5px}
  #qt-viewer .pp-opt:hover{border-color:#6EC99A;color:#6EC99A} #qt-viewer .pp-opt.on{background:#2D6B50;border-color:#2D6B50;color:#fff}
  #qt-viewer .pp-opt .rec{background:rgba(255,255,255,.14);color:#BFE6CF}
  #qt-viewer .pp-tg{background:transparent;color:rgba(255,255,255,.8);border-color:rgba(255,255,255,.3)}
  #qt-viewer .pp-tg.ok.on{background:#2D6B50;border-color:#2D6B50;color:#fff} #qt-viewer .pp-tg.hold.on{background:#9A6F12;border-color:#9A6F12;color:#fff}
  .qv-foot .pp-opts{margin:0}
  @media (max-width:700px){.qv-pair.phone{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
  /* the status strip (2026-11-11): Waiting on you + Running now side by side, the system line under them, and the
     held-back drawer after the Push package. Served by tools/queue_status.py */
  .qs{margin:0 0 22px}
  .qs-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:12px;align-items:stretch}
  .qs .qt-card+.qt-card{margin-top:0}
  .qs-wait,.qs-run{padding:12px 14px 12px;min-width:0}
  .qs-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font:600 11px/1.2 'Hanken Grotesk',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--qt-mute)}
  .qs-h .qs-tick{margin-left:auto;font:400 12px/1.3 'Hanken Grotesk',sans-serif;letter-spacing:0;text-transform:none;color:var(--qt-mute);font-variant-numeric:tabular-nums}
  .qs-wtop{display:flex;align-items:baseline;gap:10px;margin-top:8px}
  .qs-big{font:400 2.5rem/1 'Newsreader',Georgia,serif;font-variant-numeric:tabular-nums;color:var(--qt-ink)}
  .qs-wl{font-size:13px;color:var(--qt-mute)}
  .qs-parts{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
  .qs-part{font:500 12.5px/1.2 'Hanken Grotesk',sans-serif;border-radius:999px;padding:.45rem .75rem;border:1px solid var(--qt-line);background:var(--qt-soft);color:var(--qt-ink);cursor:pointer;min-height:32px;display:inline-flex;gap:6px;align-items:center;text-align:left;max-width:100%;box-sizing:border-box}
  .qs-part b{font-weight:700;font-variant-numeric:tabular-nums}
  .qs-part .x{color:var(--qt-faint);font-weight:400}
  .qs-part:hover{border-color:var(--qt-green);color:var(--qt-green)} .qs-part:focus-visible,.qs-job:focus-visible,.qs-tg:focus-visible{outline:2px solid var(--qt-green);outline-offset:2px}
  .qs-part.zero{background:transparent;color:var(--qt-faint);cursor:default} .qs-part.zero:hover{border-color:var(--qt-line);color:var(--qt-faint)}
  .qs-part[aria-expanded="true"]{background:var(--qt-green-bg);border-color:transparent;color:var(--qt-green)}
  .qs-wlist{margin-top:10px;border-top:1px solid var(--qt-line)}
  .qs-wlist a{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:baseline;padding:7px 2px;border-bottom:1px solid var(--qt-line);color:var(--qt-ink);text-decoration:none;font-size:13px}
  .qs-wlist a:hover{color:var(--qt-green)} .qs-wlist a span:last-child{font-size:12px;color:var(--qt-mute);white-space:nowrap}
  .qs-jobs{margin-top:6px}
  .qs-job{all:unset;box-sizing:border-box;display:grid;grid-template-columns:12px minmax(0,1fr) auto;gap:9px;align-items:baseline;width:100%;padding:7px 0;border-top:1px solid var(--qt-line);font-size:13px;line-height:1.35;cursor:pointer}
  .qs-job:first-child{border-top:0}
  .qs-job:hover .t{color:var(--qt-green)}
  .qs-job .t{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
  .qs-job .w{font-size:12px;color:var(--qt-mute);white-space:nowrap;font-variant-numeric:tabular-nums}
  .qs-job .w.none{color:var(--qt-faint)}
  .qs-job .sp{width:8px;height:8px;border-radius:50%;border:1.5px solid var(--qt-blue);border-top-color:transparent;animation:qt-spin .9s linear infinite;position:relative;top:1px}
  @media (prefers-reduced-motion:reduce){.qs-job .sp{animation:none}}
  .qs-empty{font-size:13px;color:var(--qt-faint);padding:10px 0 2px}
  .qs-sys{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;align-items:center}
  .qs-tg,.qs-info{font:500 12.5px/1.2 'Hanken Grotesk',sans-serif;border-radius:999px;padding:.4rem .75rem;border:1px solid var(--qt-line);background:var(--qt-card);color:var(--qt-ink);min-height:32px;display:inline-flex;gap:7px;align-items:center;box-sizing:border-box;max-width:100%}
  .qs-tg{cursor:pointer;padding-left:.45rem} .qs-tg:hover{border-color:var(--qt-green)} .qs-tg[disabled]{opacity:.55;cursor:default}
  .qs-tg .sw{width:26px;height:15px;border-radius:999px;background:#D5D9D6;position:relative;flex:none;transition:background .15s}
  .qs-tg .sw::after{content:'';position:absolute;top:2px;left:2px;width:11px;height:11px;border-radius:50%;background:#fff;transition:transform .15s;box-shadow:0 1px 2px rgba(0,0,0,.2)}
  .qs-tg.on .sw{background:var(--qt-green)} .qs-tg.on .sw::after{transform:translateX(11px)}
  .qs-tg b,.qs-info b{font-weight:600} .qs-tg .sub,.qs-info .sub{color:var(--qt-mute)}
  .qs-info{background:#EFEFE9;border-color:transparent;color:var(--qt-mute)} .qs-info b{color:var(--qt-ink)}
  .qs-msg{font-size:12.5px;color:var(--qt-mute)}
  .qs-held{margin:0 0 26px}
  .qs-held>summary .qs-hsub{margin-left:4px;font-weight:400;letter-spacing:0;text-transform:none;font-size:12px;color:var(--qt-faint)}
  .qs-hrow{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 14px;padding:10px 12px;border-top:1px solid var(--qt-line);align-items:start}
  .qs-held>summary+.qs-hrow,.qs-hintro+.qs-hrow{border-top:0}
  .qs-hintro{margin:0;padding:10px 12px;font-size:13px;line-height:1.5;color:var(--qt-mute);border-bottom:1px solid var(--qt-line)}
  .qs-ht{font-weight:600;font-size:14px;line-height:1.35;overflow-wrap:anywhere}
  .qs-why{font-size:12.5px;color:var(--qt-mute);margin-top:4px;line-height:1.45;overflow-wrap:anywhere}
  .qs-hact{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
  .qs-confirm{grid-column:1/-1;background:var(--qt-soft);border:1px solid var(--qt-line);border-radius:8px;padding:10px 12px;font-size:13px;line-height:1.5;min-width:0}
  .qs-confirm.warn{background:var(--qt-amber-bg);border-color:#EBDDB4}
  .qs-confirm p{margin:0 0 6px} .qs-confirm p:last-of-type{margin-bottom:0}
  .qs-confirm code{display:block;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;background:#EFEFE9;border-radius:6px;padding:7px 9px;margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;user-select:all}
  .qs-confirm .acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
  @media (max-width:820px){.qs-row{grid-template-columns:minmax(0,1fr)}}
  @media (max-width:700px){.qs-hrow{grid-template-columns:minmax(0,1fr)}.qs-hact{justify-content:flex-start}.qs-h .qs-tick{margin-left:0;flex-basis:100%}
    .qs-job{grid-template-columns:12px minmax(0,1fr);gap:1px 9px}.qs-job .w{grid-column:2}}
  `;
  document.head.appendChild(css);

  // ------------------------------------------------------------------ api
  // Stress test 2026-11-11: on this machine's loopback a big answer now and then arrives cut short, or never ends. A 200
  // whose JSON could not be read used to come back as null, so the lists went blank (Q.todo = null) or a saved status
  // never showed. Now a read (GET) is tried once more and gives up after 20 s, and an answer that still cannot be read is
  // a failure like a dropped one (status 0), which every caller already handles.
  async function api(path, body) {
    for (let attempt = 0; ; attempt++) {
      const again = !body && attempt === 0;
      let r, j = null, cut = false, timer = null;
      try {
        const opt = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { cache: 'no-store' };
        if (!body && window.AbortController) { const ac = new AbortController(); opt.signal = ac.signal; timer = setTimeout(() => ac.abort(), 20000); }
        r = await fetch(API + path, opt);
        try { j = await r.json(); } catch (e) { cut = true; }
      } catch (e) { clearTimeout(timer); if (again) continue; const err = new Error('down'); err.status = 0; throw err; }
      clearTimeout(timer);
      if (!r.ok) { const err = new Error((j && j.error) || ('HTTP ' + r.status)); err.status = r.status; err.body = j; throw err; }
      if (cut || j == null || typeof j !== 'object') { if (again) continue; const err = new Error('down'); err.status = 0; err.cut = true; throw err; }
      return j;
    }
  }
  function toast(msg, bad) {
    let t = $('.qt-toast'); if (!t) { t = document.createElement('div'); t.className = 'qt-toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
    t.textContent = msg; t.hidden = false; t.classList.toggle('bad', !!bad); clearTimeout(t._h); t._h = setTimeout(() => { t.hidden = true; }, bad ? 7000 : 3500);
  }
  const items = () => (Q.todo && Q.todo.items) || [];
  const cats = () => (Q.todo && Q.todo.categories) || [];
  const isOpen = it => it.status !== 'done';
  const visibleCats = () => cats().filter(c => !Q.catHidden.has(c.id));
  function badge() {
    const t = items().filter(i => isOpen(i) && !Q.catHidden.has(i.cat)).length;   // the categories he hid do not nag from the tab
    const pc = typeof pushOpenCalls === 'function' ? pushOpenCalls() : 0;         // the push package's unanswered calls count too
    const n = t + pc;
    document.querySelectorAll('.app-tabs a[data-view="queue"]').forEach(a => {
      let b = a.querySelector('.bdg'); if (!b) { b = document.createElement('span'); b.className = 'bdg'; a.appendChild(b); }
      b.textContent = n; b.hidden = !n; b.title = t + (t === 1 ? ' open to-do' : ' open to-dos') + (pc ? ' + ' + pc + ' push call' + (pc === 1 ? '' : 's') + ' to answer' : '');
    });
    window.__queueOpenCount = n;
  }
  window.__queueBadge = badge;

  async function load(quiet) {
    if (!quiet) { Q.state = Q.queue ? Q.state : 'loading'; render(); }
    loadPush(quiet);                                      // its own request: a server without /api/push still shows the queue
    // the status strip and the held-back drawer: their own request (a failure there never blocks the queue), awaited with
    // the queue so the strip is drawn in the same pass and nothing below it jumps when it arrives
    // (at most 2.5 s: a status read that hangs, as this box's loopback sometimes makes one, never holds up the queue; it
    // then draws itself when it lands)
    let late = false;
    Q.triedAt = Date.now();
    const seq0 = Q.wseq || 0;                             // a write that lands while this load waits wins over the older read
    const st = Promise.race([loadStatus(true).then(() => { if (late) drawStatus(); }), new Promise(res => setTimeout(() => { late = true; res(); }, 2500))]);
    try {
      const [q, t] = await Promise.all([api('/api/queue'), api('/api/todo'), st]);
      // Nov 11: the status read can hold this load up to 2.5 s; a status pick made meanwhile was being overwritten by the
      // list read before it (test_queue_rounds_by_country U9)
      if ((Q.wseq || 0) === seq0) { Q.queue = q; Q.todo = t; }
      Q.state = 'ok'; Q.error = ''; Q.loadedAt = Date.now();
    } catch (e) {
      if (e.status === 404) Q.state = 'old';
      else if (e.status === 0) Q.state = 'down';
      else { Q.state = Q.queue ? 'ok' : 'error'; Q.error = e.message; }
    }
    badge(); render();
  }
  // Claude edits the md as it works: a write with the version this page read is refused if the file moved on,
  // and the answer carries the fresh list, so the page shows that and says so.
  async function queueOp(body) {
    if (Q.busy || !Q.queue) return false;
    Q.busy = true; render();
    try {
      Q.queue = await api('/api/queue', Object.assign({ version: Q.queue.version }, body));
      Q.wseq = (Q.wseq || 0) + 1;
      return true;
    } catch (e) {
      if (e.status === 409 && e.body) { Q.queue = Object.assign({}, e.body, { ok: true }); toast(e.body.error || 'The queue changed underneath; reloaded.', true); }
      else if (e.status === 404) Q.state = 'old';
      else toast(e.status === 0 ? 'The photo server is not answering.' : e.message, true);
      if (e.status === 0) setTimeout(() => load(true), 800);   // the write may have landed with its answer lost: show what is saved
      return false;
    } finally { Q.busy = false; render(); }
  }
  async function todoOp(body, retry) {
    if (!Q.todo) return false;
    try {
      Q.todo = await api('/api/todo', Object.assign({ version: Q.todo.version }, body));
      Q.wseq = (Q.wseq || 0) + 1;
      badge(); return true;
    } catch (e) {
      if (e.status === 409 && e.body && !retry) { Q.todo = Object.assign({}, e.body, { ok: true }); return todoOp(body, true); }   // another tab wrote first: redo on its list
      if (e.status === 409 && e.body) { Q.todo = Object.assign({}, e.body, { ok: true }); toast('The to-do list changed in another tab; reloaded.', true); }
      else if (e.status === 404) Q.state = 'old';
      else toast(e.status === 0 ? 'The photo server is not answering.' : e.message, true);
      // a dropped answer (this box's loopback drops one now and then) can hide a write the server DID make: read the list
      // again shortly, so the page shows what's really saved (Nov 11, test_queue_rounds_by_country U9)
      if (e.status === 0) setTimeout(() => load(true), 800);
      badge(); render(); return false;
    }
  }

  // ------------------------------------------------------------------ bits
  const GRIP = '<svg viewBox="0 0 12 14" aria-hidden="true"><circle cx="3" cy="2" r="1.4"/><circle cx="9" cy="2" r="1.4"/><circle cx="3" cy="7" r="1.4"/><circle cx="9" cy="7" r="1.4"/><circle cx="3" cy="12" r="1.4"/><circle cx="9" cy="12" r="1.4"/></svg>';
  const I = {
    up: '<svg viewBox="0 0 24 24"><path d="m6 15 6-6 6 6"/></svg>', down: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>', pen: '<svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
    ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4 10 14M18 14v6H4V6h6"/></svg>', doc: '<svg viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>',
    cal: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>',
    eye: '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    left: '<svg viewBox="0 0 24 24"><path d="m15 6-6 6 6 6"/></svg>', right: '<svg viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>',
    pause: '<svg viewBox="0 0 24 24"><path d="M9 5v14M15 5v14"/></svg>', play: '<svg viewBox="0 0 24 24"><path d="M7 4v16l13-8z"/></svg>',
    plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>', check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5L20 7"/></svg>'
  };
  const STATUS_LABEL = { new: 'New', seen: 'Seen', 'needs-changes': 'Needs changes', done: 'Done' };
  // the md's own inline marks: **bold**, `code`, bare URLs; everything else escaped
  function md(s) {
    let out = esc(s);
    out = out.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
    out = out.replace(/(https?:\/\/[^\s<)]+)/g, (m) => `<a href="${m}" target="_blank" rel="noopener">${m.replace(/^https?:\/\//, '').slice(0, 48)}${m.length > 56 ? '…' : ''}</a>`);
    return out;
  }
  const when = () => { if (!Q.loadedAt) return ''; const s = Math.round((Date.now() - Q.loadedAt) / 1000); return s < 5 ? 'just now' : s < 60 ? s + ' s ago' : Math.round(s / 60) + ' min ago'; };
  const siteUrl = rel => API + '/site/' + rel.replace(/\\/g, '/').replace(/^\.?\//, '').split('/').map(encodeURIComponent).join('/');
  const shortDate = d => { if (!d) return ''; const dt = new Date(String(d).slice(0, 10) + 'T00:00:00'); return isNaN(dt) ? String(d).slice(0, 10) : dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); };
  function dueChip(d) {
    if (!d) return '';
    const t = today(); const cls = d < t ? 'over' : d === t ? 'today' : '';
    const label = cls === 'today' ? 'today' : d === tomorrow() ? 'tomorrow' : shortDate(d);
    return `<span class="qt-chip due ${cls}" title="due ${esc(d)}">${I.cal}${esc(label)}</span>`;
  }
  function linkChips(it) {
    const L = it.links || {}; let h = '';
    if (L.editor) h += `<a class="qt-chip link" href="${esc(siteUrl(L.editor))}" data-act="open-rel" data-rel="${esc(L.editor)}" title="Open ${esc(L.editor)} in the editor">${I.doc}${esc(L.editor.split('/').pop().replace(/\.html?$/i, ''))}</a>`;
    if (L.preview) h += `<a class="qt-chip link" href="${esc(siteUrl(L.preview))}" target="_blank" rel="noopener" title="Preview ${esc(L.preview)}">${I.ext}preview</a>`;
    if (L.url) { let label = L.url; try { const u = new URL(L.url); label = u.host.replace(/^www\./, '') + (u.pathname.length > 1 ? u.pathname.slice(0, 18) + (u.pathname.length > 18 ? '…' : '') : ''); } catch (e) {}
      h += `<a class="qt-chip link" href="${esc(L.url)}" target="_blank" rel="noopener" title="${esc(L.url)}">${I.ext}${esc(label)}</a>`; }
    for (const k of Object.keys(L)) if (!['editor', 'preview', 'url', 'cloud'].includes(k)) h += `<a class="qt-chip link" href="${esc(/^https?:/.test(L[k]) ? L[k] : siteUrl(L[k]))}" target="_blank" rel="noopener">${I.ext}${esc(k)}</a>`;
    return h;
  }
  const catName = id => { const c = cats().find(c => c.id === id); return c ? c.name : id; };
  // the categories the server says to draw by country ("rounds"); each item carries its group {key, name, flag} or null.
  // A server from before this build sends neither, and then the category stays the flat list it was.
  const isGrouped = catId => ((Q.todo && Q.todo.grouped) || []).includes(catId);
  const gkey = it => (it.group && it.group.key) || '_other';
  const cgId = (catId, key) => catId + '/' + key;
  function groupsOf(all) {                       // [{key, name, flag, items}] in the order the countries first appear in his order; Other last
    const by = new Map();
    for (const it of all) {
      const k = gkey(it);
      if (!by.has(k)) by.set(k, { key: k, name: it.group ? it.group.name : 'Other', flag: it.group ? it.group.flag : null, items: [] });
      by.get(k).items.push(it);
    }
    const out = [...by.values()].filter(g => g.key !== '_other');
    if (by.has('_other')) out.push(by.get('_other'));
    return out;
  }
  const stSel = (it, extra) => `<select class="qt-stsel ${esc(it.status)}" data-act="todo-status" aria-label="Status" title="Status" ${extra || ''}>${Object.keys(STATUS_LABEL).map(s => `<option value="${s}" ${s === it.status ? 'selected' : ''}>${STATUS_LABEL[s]}</option>`).join('')}</select>`;
  // finished things, newest first: Kevin's done to-dos by when they were done, Claude's by the DONE date then the number
  const doneStamp = it => it.done_at || it.decided_at || it.updated || it.created || '';
  const doneTodos = list => list.filter(x => !isOpen(x)).sort((a, b) => doneStamp(b).localeCompare(doneStamp(a)));
  const working = () => ((Q.queue && Q.queue.queue) || []).filter(i => i.state !== 'done');
  const doneQueue = () => ((Q.queue && Q.queue.queue) || []).filter(i => i.state === 'done').sort((a, b) => (b.done_on || '').localeCompare(a.done_on || '') || b.id - a.id);
  // the md's bold group headings ("**Needs you.**"): {id, name, open, total}; an item's `group` is one of these ids, or null
  const qGroups = () => (Q.queue && Q.queue.groups) || [];
  const qGroupName = g => { const x = qGroups().find(x => x.id === g); return x ? x.name : ''; };
  const openKey = it => 'q:' + (it.text || '').slice(0, 80);     // an expanded card stays expanded when ids shift after a move

  // ------------------------------------------------------------------ render
  const root = () => Q.root || $('#queue-root');
  function render() {
    const r = root(); if (!r) return;
    const focus = document.activeElement, fid = focus && r.contains(focus) ? focus.id : null, fsel = focus && fid && 'selectionStart' in focus ? [focus.selectionStart, focus.selectionEnd] : null;
    if (Q.state === 'loading' && !Q.queue) { r.innerHTML = '<div class="qt-msg">Loading…</div>'; return; }
    if (Q.state === 'old') {
      r.innerHTML = `<div class="qt-msg"><b>The photo server needs a restart</b> for this tab: <code>/api/queue</code> is not there yet.<br>
        Opening the editor from the taskbar icon restarts it as soon as no photo job is running.<br>
        <button class="qt-btn" style="margin-top:16px" data-act="reload">Try again</button></div>`;
      clearTimeout(Q.retry); Q.retry = setTimeout(() => { const a = $('#app-queue'); if (!a || !a.hidden) load(true); }, 20000);   // a restart under way lands here by itself
      return;
    }
    if (Q.state === 'down' && !Q.queue) {
      r.innerHTML = `<div class="qt-msg"><b>The photo server isn’t answering.</b><br>It restarts by itself; this tab tries again in a moment.<br>
        <button class="qt-btn" style="margin-top:16px" data-act="reload">Try again</button></div>`;
      clearTimeout(Q.retry); Q.retry = setTimeout(() => { const a = $('#app-queue'); if ((!a || !a.hidden) && !Q.queue) load(true); }, 6000);
      return;
    }
    if (Q.state === 'error' && !Q.queue) { r.innerHTML = `<div class="qt-msg"><b>Could not read the queue.</b><br>${esc(Q.error)}<br><button class="qt-btn" style="margin-top:16px" data-act="reload">Try again</button></div>`; return; }
    const q = Q.queue, c = q.counts || {};
    const open = items().filter(isOpen), shown = open.filter(i => !Q.catHidden.has(i.cat)), doneAll = doneTodos(items().filter(i => !Q.catHidden.has(i.cat)));
    const work = working(), doneQ = doneQueue(), preDone = ((q.queue_pre || {}).items || []).filter(i => i.done);
    const nOpen = c.open != null ? c.open : (c.pending || 0) + (c.running || 0) + (c.hold || 0);      // every item not done, running and held included
    const sub = [c.running ? c.running + ' running' : '', c.hold ? c.hold + ' held' : ''].filter(Boolean).join(' · ');
    const cc = nOpen ? nOpen + ' open' + (sub ? ' (' + sub + ')' : '') : 'nothing open';
    r.innerHTML = `<div class="qt-wrap">
      <div class="qt-top">
        <div><span class="qt-eyebrow">Editor suite</span><h1>Queue</h1></div>
        <span class="qt-spacer"></span>
        <span class="qt-when" data-when>${Q.state === 'down' ? 'Server not answering · last read' : 'Read ' + when()}</span>
        <button class="qt-btn" data-act="reload" ${Q.busy ? 'disabled' : ''}>Reload</button>
      </div>
      ${renderStatus()}
      <div id="qt-push-slot"></div>
      ${renderHeld()}
      <div class="qt-cols">
        <section class="qt-col" data-col="claude">
          <h2>Claude <span>${esc(cc)}</span></h2>
          <div class="qt-card" data-list="queue">
            ${work.length ? renderWork(work) : '<div class="qt-empty">Nothing queued.</div>'}
            ${renderQueueAdd()}
          </div>
          ${renderPre(q, false)}
          ${renderSections(q)}
          ${doneQ.length || preDone.length ? `<details class="qt-card qt-fold qt-done" style="margin-top:12px" ${Q.doneOpen.queue ? 'open' : ''} data-fold="done:queue"><summary>Done <span class="n">${doneQ.length + preDone.length}</span></summary>${doneQ.map(it => renderItem(it, -1, 0, true)).join('')}${renderPre(q, true)}</details>` : ''}
        </section>
        <section class="qt-col" data-col="mine">
          <h2>Mine <span>${shown.length} open${shown.length !== open.length ? ' of ' + open.length : ''}</span></h2>
          ${renderFilter()}
          ${renderDue(shown)}
          <div class="qt-card">${renderTodoAdd()}</div>
          <div class="qt-card" data-list="cats">${visibleCats().map((cat, i) => renderCat(cat, i, visibleCats().length)).join('') || '<div class="qt-empty">All categories hidden.</div>'}</div>
          ${doneAll.length ? `<details class="qt-card qt-fold qt-done" ${Q.doneOpen.todo ? 'open' : ''} data-fold="done:todo"><summary>Done <span class="n">${doneAll.length}</span><button type="button" class="qt-more" data-act="todo-clear-done" title="Delete every done item">Clear</button></summary>
            <div data-list="done">${doneAll.map((it, k) => renderTodo(it, k, doneAll.length, true)).join('')}</div></details>` : ''}
        </section>
      </div></div>`;
    mountPush(r);                                          // the Push package's own element, kept across renders (typing, images)
    if (fid) { const el = document.getElementById(fid); if (el) { el.focus(); try { if (fsel && fsel[0] != null) el.setSelectionRange(fsel[0], fsel[1]); } catch (e) {} } }
    renderViewer();
  }
  // the lines above the first numbered item (a setup checklist): its open ticks as a group, its done ticks in the Done fold
  function renderPre(q, done) {
    const p = q.queue_pre; if (!p || !p.items) return '';
    const list = p.items.filter(i => !!i.done === !!done); if (!list.length) return '';
    const rows = list.map((i, k) => { const x = !!Q.open['pre:' + i.text.slice(0, 40)]; return `<div class="qt-chk ${i.done ? 'done' : ''}" data-act="pre-toggle" data-k="${esc(i.text.slice(0, 40))}"><span class="box"></span><span class="qt-s ${x ? '' : 'clamp'}" style="color:inherit;margin:0" ${x ? '' : 'title="Show more"'}>${md(i.text)}</span></div>`; }).join('');
    if (done) return rows;
    const label = (p.label || 'First').split(/[(:]/)[0].trim().slice(0, 48) || 'First';
    return `<div class="qt-group">${esc(label)} <span class="n">${list.length}</span></div><div class="qt-card">${rows}</div>`;
  }
  // the working items in file order, under the md's own group headings (a heading row is drawn where the group changes;
  // groups with nothing open are not drawn). The headings sit in the same list as the items, so an item drags across them.
  function renderWork(work) {
    let h = '', g = undefined;
    work.forEach((it, i) => {
      if (it.group !== g) {
        g = it.group;
        if (g != null) { const x = qGroups().find(x => x.id === g); h += `<div class="qt-qgh" data-group="${g}"><span class="nm">${esc(x ? x.name : '')}</span><span class="n">${x ? x.open : ''}</span></div>`; }
      }
      h += renderItem(it, i, work.length);
    });
    return h;
  }
  function renderItem(it, i, n, isDone) {
    const open = !!Q.open[openKey(it)], has = !!(it.summary || (it.details && it.details.length));
    const stLabel = { done: isDone ? '' : 'done', running: 'running', hold: 'on hold', pending: '' }[it.state] || '';
    return `<div class="qt-row qt-item st-${it.state}" data-id="${it.id}" data-group="${it.group == null ? '' : it.group}" ${isDone ? '' : `data-idx="${i}"`}>
      ${isDone ? '<span></span>' : `<span class="qt-grip" title="Drag to reorder" aria-label="Drag to reorder">${GRIP}</span>`}
      <span class="qt-num ${it.num == null ? 'nb' : ''}" ${it.num == null ? 'aria-hidden="true"' : ''}>${it.num == null ? '' : it.num}</span>
      <div class="qt-main">
        <div class="qt-trow ${has ? '' : 'nohide'}" data-act="${has ? 'toggle' : ''}" ${has ? `role="button" tabindex="0" aria-expanded="${open}"` : ''}>
          <span class="qt-t">${md(it.title)}</span>${stLabel ? `<span class="qt-st ${it.state}">${esc(stLabel)}</span>` : ''}${isDone && it.done_on ? `<span class="qt-chip doneon" title="done ${esc(it.done_on)}">${esc(shortDate(it.done_on))}</span>` : ''}
        </div>
        ${it.summary && !open ? `<div class="qt-s clamp" data-act="toggle" title="Show more">${md(it.summary)}</div>` : ''}
        ${open ? `<div class="qt-d">${it.summary ? `<div>${md(it.summary)}</div>` : ''}${it.details.length ? `<ul>${it.details.map(d => `<li>${md(d)}</li>`).join('')}</ul>` : ''}
          <div class="meta">${[it.num != null ? '#' + it.num : '', it.group != null ? esc(qGroupName(it.group)) : '', it.added_by ? 'added by Kevin ' + esc(it.added_by) : ''].filter(Boolean).join(' · ')}</div></div>` : ''}
      </div>
      <div class="qt-acts">
        ${isDone ? '' : `<button class="qt-ib qt-hold" data-act="hold" data-hold="${it.state === 'hold' ? 0 : 1}" ${Q.busy ? 'disabled' : ''} title="${it.state === 'hold' ? 'Resume: Claude may start it again' : 'Hold: Claude skips it until you resume it'}" aria-label="${it.state === 'hold' ? 'Resume' : 'Hold'}">${it.state === 'hold' ? I.play : I.pause}</button>
        <button class="qt-ib" data-act="move" data-dir="-1" title="Move up" aria-label="Move up" ${i === 0 || Q.busy ? 'disabled' : ''}>${I.up}</button>
        <button class="qt-ib" data-act="move" data-dir="1" title="Move down" aria-label="Move down" ${i === n - 1 || Q.busy ? 'disabled' : ''}>${I.down}</button>`}
      </div></div>`;
  }
  function renderQueueAdd() {
    const gs = qGroups().filter(g => g.open > 0);           // a new item goes after the open items of the group picked here (the first by default)
    const sel = gs.length > 1 ? `<select id="qt-qa-group" aria-label="Group" title="Which group it joins">${gs.map(g => `<option value="${g.id}" ${g.id === Q.addGroup ? 'selected' : ''}>${esc(g.name)}</option>`).join('')}</select>` : '';
    return `<form class="qt-add" data-form="queue-add" autocomplete="off">
      <input type="text" id="qt-qa-title" class="grow" placeholder="Add for Claude…" maxlength="300" required ${Q.busy ? 'disabled' : ''} title="Goes after the open items of its group">
      ${sel}
      ${Q.addDetails ? '' : '<button type="button" class="qt-more" data-act="qa-details" title="Add sub-bullets">Details</button>'}
      <button type="submit" class="qt-btn primary" ${Q.busy ? 'disabled' : ''}>Add</button>
      ${Q.addDetails ? '<textarea id="qt-qa-details" class="full" placeholder="One detail per line"></textarea>' : ''}
    </form>`;
  }
  function renderSections(q) {
    const secs = (q.sections || []).filter(s => s.kind !== 'queue');
    const waiting = secs.filter(s => /waiting/i.test(s.name)), rest = secs.filter(s => !/waiting/i.test(s.name) && !/^log/i.test(s.name)), log = secs.filter(s => /^log/i.test(s.name));
    let h = '';
    for (const s of waiting) {
      h += `<div class="qt-group">${esc(s.name)} <span class="n">${s.items.length}</span></div><div class="qt-card">${s.items.length ? s.items.map((it, i) => `<div class="qt-b"><span>${md(it.text)}</span>
        <span class="qt-acts"><button class="qt-ib" data-act="to-todo" data-sec="${esc(s.name)}" data-i="${i}" title="Copy to my to-do" aria-label="Copy to my to-do">${I.plus}</button></span></div>`).join('') : '<div class="qt-empty">Nothing.</div>'}</div>`;
    }
    for (const s of rest.concat(log)) {
      h += `<details class="qt-card qt-fold" style="margin-top:12px" data-fold="${esc(s.name)}" ${Q.open['sec:' + s.name] ? 'open' : ''}><summary>${esc(s.name)} <span class="n">${s.items.length}</span></summary>${s.items.map(it => `<div class="qt-b"><span>${md(it.text)}</span></div>`).join('')}</details>`;
    }
    return h;
  }
  // ---- the inbox
  function renderFilter() {
    const cnt = (Q.todo && Q.todo.counts) || {};
    return `<div class="qt-filter" role="group" aria-label="Categories shown">${cats().map(c => {
      const on = !Q.catHidden.has(c.id), n = (cnt[c.id] || {}).open || 0;
      return `<button type="button" class="qt-fchip ${on ? 'on' : 'off'}" data-act="cat-filter" data-cat="${esc(c.id)}" aria-pressed="${on}" title="${on ? 'Hide' : 'Show'} ${esc(c.name)}">${esc(c.name)}<span class="n">${n}</span></button>`;
    }).join('')}${Q.catHidden.size ? '<button type="button" class="qt-more" data-act="cat-filter-all">show all</button>' : ''}</div>`;
  }
  function renderDue(shown) {
    const soon = shown.filter(i => i.due && i.due <= tomorrow()).sort((a, b) => a.due < b.due ? -1 : 1).slice(0, 6);
    if (!soon.length) return '';
    return `<div class="qt-group" style="margin-top:0">Due <span class="n">${soon.length}</span></div><div class="qt-card qt-due">${soon.map(i => { const w = i.due < today() ? 'over' : i.due === today() ? 'today' : ''; return `<a data-act="jump" data-id="${esc(i.id)}" data-cat="${esc(i.cat)}" href="#"><span>${esc(i.title)}</span><span class="c ${w}">${esc(catName(i.cat))} · ${w === 'over' ? 'overdue' : w === 'today' ? 'today' : 'tomorrow'}</span></a>`; }).join('')}</div>`;
  }
  function renderCat(cat, i, n) {
    const all = items().filter(x => x.cat === cat.id), open = all.filter(isOpen);
    const isOpenCat = Q.catOpen.has(cat.id), nNew = open.filter(x => x.status === 'new').length, nNeeds = open.filter(x => x.status === 'needs-changes').length;
    return `<section class="qt-cat ${isOpenCat ? 'open' : ''}" data-cat="${esc(cat.id)}" data-idx="${i}">
      <header class="qt-cath">
        <span class="qt-grip" title="Drag to reorder the categories" aria-label="Drag to reorder the categories">${GRIP}</span>
        <button type="button" class="qt-catb" data-act="cat-toggle" aria-expanded="${isOpenCat}"><span class="chev"></span><span class="nm">${esc(cat.name)}</span>
          <span class="cnt"><span class="n ${open.length ? '' : 'zero'}" title="open">${open.length}</span>${nNew ? `<span class="n new" title="new">${nNew} new</span>` : ''}${nNeeds ? `<span class="n needs" title="needs changes">${nNeeds}</span>` : ''}</span></button>
        <span class="qt-acts"><button class="qt-ib" data-act="cat-move" data-dir="-1" title="Move up" aria-label="Move category up" ${i === 0 ? 'disabled' : ''}>${I.up}</button>
          <button class="qt-ib" data-act="cat-move" data-dir="1" title="Move down" aria-label="Move category down" ${i === n - 1 ? 'disabled' : ''}>${I.down}</button></span>
      </header>
      ${isOpenCat ? (isGrouped(cat.id) ? renderGroups(cat, open) : `<div data-list="todo" data-cat="${esc(cat.id)}">
        ${open.length ? open.map((it, k) => renderTodo(it, k, open.length)).join('') : '<div class="qt-empty">Nothing open.</div>'}
      </div>`) : ''}
    </section>`;
  }
  // Review: rounds by country: a flag row per country (folded unless he opened it; remembered), its open items under it in
  // his order; the country rows drag like items, and an item drags inside its country. Done items are in the column's Done.
  function renderGroups(cat, open) {
    const groups = groupsOf(open);
    if (!groups.length) return `<div data-list="todo" data-cat="${esc(cat.id)}"><div class="qt-empty">Nothing open.</div></div>`;
    const up = new Set((Q.todo && Q.todo.upcoming) || []);
    // both parts always show, so a country can be dragged into an empty one (Kevin, 2026-11-11: "let me drag between
    // upcoming and backlog"); "Other" stays in Backlog
    const parts = [['up', 'Upcoming', groups.filter(g => up.has(g.key))], ['back', 'Backlog', groups.filter(g => !up.has(g.key))]];
    return parts.map(([part, label, gs]) => `<div class="qt-cgsec" data-part="${part}">${label} <span class="n">${gs.length}</span>${gs.length ? '' : '<span class="qt-cgempty">drag a country here</span>'}</div>${renderCgs(cat, gs, part)}`).join('');
  }
  function renderCgs(cat, groups, part) {
    return `<div class="qt-cgs" data-list="cgroups" data-cat="${esc(cat.id)}" data-part="${part || ''}">${groups.map((g, i) => {
      const on = Q.cgOpen.has(cgId(cat.id, g.key));
      const nNew = g.items.filter(x => x.status === 'new').length, nNeeds = g.items.filter(x => x.status === 'needs-changes').length;
      const flag = g.flag ? `<img class="fl" src="${esc(siteUrl(g.flag))}" alt="" width="20" height="15">` : '<span class="fl none" aria-hidden="true"></span>';
      return `<section class="qt-cg ${on ? 'open' : ''}" data-cg="${esc(g.key)}" data-idx="${i}">
        <header class="qt-cgh">
          <span class="qt-grip" title="Drag to reorder the countries" aria-label="Drag to reorder the countries">${GRIP}</span>
          <button type="button" class="qt-cgb" data-act="cg-toggle" aria-expanded="${on}"><span class="chev"></span>${flag}<span class="nm">${esc(g.name)}</span>
            <span class="cnt"><span class="n" title="open">${g.items.length}</span>${nNew ? `<span class="n new" title="new">${nNew} new</span>` : ''}${nNeeds ? `<span class="n needs" title="needs changes">${nNeeds}</span>` : ''}</span></button>
          <span class="qt-acts"></span>
        </header>
        ${on ? `<div data-list="todo" data-cat="${esc(cat.id)}" data-cg="${esc(g.key)}">${g.items.map((it, k) => renderTodo(it, k, g.items.length, false, true)).join('')}</div>` : ''}
      </section>`;
    }).join('')}</div>`;
  }
  function renderTodoAdd() {
    return `<form class="qt-add" data-form="todo-add" autocomplete="off" style="border-top:0">
      <input type="text" id="qt-ta-text" class="grow" placeholder="Add a to-do…" maxlength="500" required>
      <select id="qt-ta-cat" aria-label="Category" title="Category">${cats().map(c => `<option value="${esc(c.id)}" ${c.id === 'mine' ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select>
      <input type="date" id="qt-ta-due" title="Due" aria-label="Due date">
      <input type="text" id="qt-ta-link" class="lnk" placeholder="Link" title="An article path or a URL" maxlength="500">
      <button type="submit" class="qt-btn primary">Add</button></form>`;
  }
  function renderTodo(it, i, n, isDone, inGroup) {
    const L = it.links || {};
    if (Q.editing === it.id) {
      return `<div class="qt-row qt-todo ${it.status === 'done' ? 'done' : ''}" data-id="${esc(it.id)}"><span></span><input type="checkbox" ${it.status === 'done' ? 'checked' : ''} data-act="todo-toggle" aria-label="Done">
        <form class="qt-edit" data-form="todo-edit" autocomplete="off">
          <input type="text" class="t" id="qt-te-text" value="${esc(it.title)}" maxlength="500" required aria-label="Title">
          <input type="date" id="qt-te-due" value="${esc(it.due || '')}" aria-label="Due date" title="Due">
          <input type="text" class="l" id="qt-te-link" value="${esc(L.url || L.editor || '')}" placeholder="Link" maxlength="500" aria-label="Link">
          <input type="text" class="n" id="qt-te-note" value="${esc(it.note || '')}" placeholder="Note" maxlength="2000" aria-label="Note">
          <button type="submit" class="qt-btn sm primary">Save</button><button type="button" class="qt-btn sm" data-act="todo-cancel">Cancel</button>
        </form><div class="qt-acts"></div></div>`;
    }
    const shots = it.shots && (it.shots.desktop || it.shots.phone), x = Q.xo.has(it.id);
    // a round applied since the item was made counts nothing (QA 2026-11-10: "20 changes" on an applied cappadocia-r3);
    // queue_tab.sync_live marks it, and the chip says "round applied" instead
    const roundChip = it.round && it.round.applied ? `<span class="qt-chip plain" title="${esc(it.round.slug)} is applied: nothing left to decide in it">round applied</span>`
      : it.round && it.round.changes != null && !it.round.gone ? `<span class="qt-chip plain" title="changes in the round">${esc(it.round.left != null && it.round.left !== it.round.changes ? it.round.left + ' of ' + it.round.changes : it.round.changes)} change${it.round.changes === 1 ? '' : 's'}</span>` : '';
    const shadowChip = it.shadow_open ? `<span class="qt-chip plain">${it.shadow_open} sr- thread${it.shadow_open === 1 ? '' : 's'}</span>` : '';
    const stChip = it.status === 'needs-changes' ? '<span class="qt-chip st">needs changes</span>' : '';
    const whereChip = isDone ? `<span class="qt-chip plain" title="${esc(catName(it.cat))}">${it.group && it.group.flag ? `<img src="${esc(siteUrl(it.group.flag))}" alt="">` : ''}${esc(catName(it.cat).replace(/^Review: /, ''))}</span>${doneStamp(it) ? `<span class="qt-chip doneon" title="done ${esc(doneStamp(it))}">${esc(shortDate(doneStamp(it)))}</span>` : ''}` : '';
    // every item takes a reply, not only questions (Kevin, 2026-11-11: "I should be able to respond to all of My to-dos on
    // the queue page"); a question's answer closes it, a reply on anything else leaves it open for Claude to act on
    const q = it.kind === 'question';
    const reply = it.reply
      ? `<div class="qt-note"><b>${q ? 'Answer' : 'Reply'}</b>${esc(it.reply)} ${isDone ? '' : '<button class="qt-more" data-act="reply-edit" style="padding:0 4px">edit</button>'}</div>`
      : isDone ? '' : `<form class="qt-reply" data-form="todo-reply"><textarea id="qt-rp-${esc(it.id)}" placeholder="${q ? 'Answer' : 'Reply'} for Claude…" maxlength="2000" rows="1" aria-label="${q ? 'Answer' : 'Reply'} for Claude"></textarea><button type="submit" class="qt-btn sm primary">Send</button></form>`;
    return `<div class="qt-row qt-todo st-${esc(it.status)} ${it.status === 'done' ? 'done' : ''}" data-id="${esc(it.id)}" data-idx="${i}">
      ${isDone ? '<span></span>' : `<span class="qt-grip" title="Drag to reorder" aria-label="Drag to reorder">${GRIP}</span>`}
      <input type="checkbox" ${it.status === 'done' ? 'checked' : ''} data-act="todo-toggle" aria-label="Done" title="${it.status === 'done' ? 'Reopen' : 'Done'}">
      <div class="qt-main"><div class="qt-tt" data-act="${shots ? 'view' : 'xo'}" title="${shots ? 'Open the viewer' : ''}">${esc(it.title)}</div>
        ${it.summary ? `<div class="qt-s ${x ? '' : 'clamp'}" data-act="xo" ${x ? '' : 'title="Show more"'}>${md(it.summary)}</div>` : ''}
        <div class="qt-chips">${shots ? `<button type="button" class="qt-chip view" data-act="view">${I.eye}View</button>` : ''}${stChip}${dueChip(it.due)}${roundChip}${shadowChip}${linkChips(it)}${it.country && !inGroup && !isDone ? `<span class="qt-chip plain">${esc(it.country)}</span>` : ''}${whereChip}</div>
        ${it.note ? `<div class="qt-note ${x ? '' : 'clamp'}" data-act="xo"><b>Note</b>${esc(it.note)}</div>` : ''}${reply}
      </div>
      <div class="qt-acts">
        ${stSel(it)}
        <button class="qt-ib" data-act="todo-edit" title="Edit" aria-label="Edit">${I.pen}</button>
        ${isDone ? '' : `<button class="qt-ib" data-act="todo-move" data-dir="-1" title="Move up" aria-label="Move up" ${i === 0 ? 'disabled' : ''}>${I.up}</button>
        <button class="qt-ib" data-act="todo-move" data-dir="1" title="Move down" aria-label="Move down" ${i === n - 1 ? 'disabled' : ''}>${I.down}</button>`}
        <button class="qt-ib danger" data-act="todo-delete" title="Delete" aria-label="Delete">${I.x}</button>
      </div></div>`;
  }

  // ------------------------------------------------------------------ the viewer
  function viewerEl() {
    let v = $('#qt-viewer');
    if (!v) {
      v = document.createElement('div'); v.id = 'qt-viewer'; v.hidden = true; v.setAttribute('role', 'dialog'); v.setAttribute('aria-modal', 'true'); v.setAttribute('aria-label', 'Review viewer');
      document.body.appendChild(v);
      v.addEventListener('click', onViewerClick); v.addEventListener('change', onViewerChange);
      document.addEventListener('keydown', e => { if (Q.viewer) onViewerKey(e); });   // Esc and the arrows work wherever focus sits
      v.addEventListener('pointerdown', swipeStart); v.addEventListener('pointerup', swipeEnd); v.addEventListener('pointercancel', () => { Q.swipe = null; });
    }
    return v;
  }
  function openViewer(id, catId) {
    const me = items().find(x => x.id === id);
    const pool = me && !isOpen(me) ? doneTodos(items()) : items().filter(x => x.cat === catId && isOpen(x));   // the Done section walks the done ones
    const list = pool.filter(x => x.shots && (x.shots.desktop || x.shots.phone)).map(x => x.id);
    const i = Math.max(0, list.indexOf(id));
    Q.viewer = { ids: list.length ? list : [id], i, cat: catId };
    renderViewer(); seenNow();
    setTimeout(() => { const x = $('#qt-viewer .qv-x'); if (x) x.focus(); }, 0);
  }
  function closeViewer() { Q.viewer = null; renderViewer(); render(); }
  function seenNow() {
    if (Q.viewer && Q.viewer.push) { pushSeen(Q.viewer.ids[Q.viewer.i]); return; }
    const it = viewerItem(); if (it && it.status === 'new') todoOp({ op: 'update', id: it.id, status: 'seen' }).then(() => renderViewer());
  }
  const viewerItem = () => Q.viewer ? (Q.viewer.push ? pushEntry(Q.viewer.ids[Q.viewer.i]) : items().find(x => x.id === Q.viewer.ids[Q.viewer.i])) : null;
  function step(d) { if (!Q.viewer) return; const j = Q.viewer.i + d; if (j < 0 || j >= Q.viewer.ids.length) return; Q.viewer.i = j; renderViewer(); seenNow(); }
  function renderViewer() {
    const v = viewerEl();
    if (!Q.viewer) { v.hidden = true; document.body.style.overflow = ''; return; }
    if (Q.viewer.push) return renderPushViewer(v);
    const it = viewerItem(); if (!it) { Q.viewer = null; v.hidden = true; return; }
    const dev = Q.dev, src = it.shots && it.shots[dev] ? siteUrl(it.shots[dev]) : null, other = dev === 'desktop' ? 'phone' : 'desktop';
    const L = it.links || {};
    v.hidden = false; document.body.style.overflow = 'hidden';
    v.innerHTML = `<div class="qv-bar">
        <button class="qv-x" data-act="close" title="Close (Esc)" aria-label="Close">${I.x}</button>
        <span class="ttl" title="${esc(it.title)}">${esc(it.title)}</span>
        <span class="pos">${Q.viewer.i + 1} / ${Q.viewer.ids.length} · ${esc(catName(it.cat))}</span>
        <span class="qv-seg" role="group" aria-label="Device"><button data-act="dev" data-dev="desktop" class="${dev === 'desktop' ? 'on' : ''}">Desktop</button><button data-act="dev" data-dev="phone" class="${dev === 'phone' ? 'on' : ''}">Phone</button></span>
      </div>
      <div class="qv-stage ${dev}">
        ${src ? `<img src="${esc(src)}" alt="${esc(it.title)}, ${dev}" draggable="false">` : `<div class="none">No ${dev} screenshot for this item${it.shots && it.shots[other] ? `; there is a ${other} one.` : '.'}</div>`}
        <button class="qv-nav l" data-act="prev" title="Previous (←)" aria-label="Previous" ${Q.viewer.i === 0 ? 'disabled' : ''}>${I.left}</button>
        <button class="qv-nav r" data-act="next" title="Next (→)" aria-label="Next" ${Q.viewer.i >= Q.viewer.ids.length - 1 ? 'disabled' : ''}>${I.right}</button>
      </div>
      <div class="qv-foot">
        <span class="sum">${md(it.summary || '')}${it.round && it.round.applied ? ' <span style="opacity:.7">· round applied</span>' : it.round && it.round.changes != null && !it.round.gone ? ` <span style="opacity:.7">· ${esc(it.round.changes)} changes</span>` : ''}</span>
        ${stSel(it, 'data-id="' + esc(it.id) + '"')}
        <input type="text" id="qv-note" value="${esc(it.note || '')}" placeholder="Note for Claude" title="Saved when you leave the box" maxlength="2000" aria-label="Note">
        ${L.editor ? `<a class="qt-btn" href="${esc(siteUrl(L.editor))}" data-act="open-rel" data-rel="${esc(L.editor)}">Open in editor</a>` : ''}
      </div>`;
  }
  function onViewerClick(e) {
    const el = e.target.closest('[data-act]'); if (!el) return;
    const act = el.dataset.act;
    if (act === 'close') closeViewer();
    else if (act === 'prev') step(-1);
    else if (act === 'next') step(1);
    else if (act === 'dev') { Q.dev = el.dataset.dev; store.set('viewer:dev', Q.dev); renderViewer(); }
    else if (act.startsWith('pp-')) pushAct(el, e);
    else if (act === 'open-rel') { if (window.__queueOpenRel || (window.__apOpen && window.showView)) { e.preventDefault(); const rel = el.dataset.rel; closeViewer(); openRel(rel); } else el.target = '_blank'; }
  }
  function onViewerChange(e) {
    const it = viewerItem(); if (!it) return;
    if (e.target.matches('.qt-stsel')) todoOp({ op: 'update', id: it.id, status: e.target.value }).then(() => { renderViewer(); });
  }
  function onViewerKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); closeViewer(); }
    else if (e.key === 'ArrowLeft' && !e.target.matches('input,textarea')) step(-1);
    else if (e.key === 'ArrowRight' && !e.target.matches('input,textarea')) step(1);
    else if (e.key === 'Enter' && e.target.id === 'qv-pnote') { e.preventDefault(); e.target.blur(); }
    else if ((e.key === 'd' || e.key === 'p') && !e.target.matches('input,textarea')) { Q.dev = e.key === 'd' ? 'desktop' : 'phone'; store.set('viewer:dev', Q.dev); renderViewer(); }
  }
  function swipeStart(e) { if (!e.target.closest('.qv-stage') || e.target.closest('button')) return; Q.swipe = { x: e.clientX, y: e.clientY, t: Date.now() }; }
  function swipeEnd(e) {
    const s = Q.swipe; Q.swipe = null; if (!s) return;
    const dx = e.clientX - s.x, dy = e.clientY - s.y;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5 && Date.now() - s.t < 800) step(dx < 0 ? 1 : -1);
  }
  document.addEventListener('focusout', e => {      // the viewer's note saves itself when the box is left
    if (e.target && e.target.id === 'qv-pnote' && Q.viewer && Q.viewer.push) { pushNoteFrom(Q.viewer.ids[Q.viewer.i], e.target.value); return; }
    if (e.target && e.target.id === 'qv-note' && Q.viewer && !Q.viewer.push) { const it = viewerItem(); if (it && (it.note || '') !== e.target.value.trim()) todoOp({ op: 'update', id: it.id, note: e.target.value.trim() }); }
  });
  function openRel(rel) {
    if (window.__queueOpenRel) return window.__queueOpenRel(rel);
    if (window.__apOpen && window.showView) { showView('editor'); window.__apOpen(rel); }
    else window.open(siteUrl(rel), '_blank');
  }

  // ------------------------------------------------------------------ actions
  // Claude's queue: one item moves at a time ({op:'move'}), and only its own lines move in the md: the group headings,
  // the done items and every other item stay where they are, and nothing is renumbered (QA 2026-11-10: a whole-list
  // reorder dragged headings along and renumbered everything). An arrow past a group heading crosses into that group:
  // up lands at the end of the group above, down at the top of the group below.
  const qItem = row => row ? ((Q.queue && Q.queue.queue) || []).find(x => x.id === +row.dataset.id) : null;
  function arrowMove(i, dir) {
    const w = working(), j = i + dir; if (!w[i] || j < 0 || j >= w.length) return null;
    const me = w[i], t = w[j], same = me.group === t.group;
    return dir < 0 ? (same ? { op: 'move', id: me.id, before: t.id } : { op: 'move', id: me.id, after: t.id })
                   : (same ? { op: 'move', id: me.id, after: t.id } : { op: 'move', id: me.id, before: t.id });
  }
  function moveIn(arr, from, to) { const a = arr.slice(); const [x] = a.splice(from, 1); a.splice(to, 0, x); return a; }
  const catIds = (cat, onlyOpen) => items().filter(x => x.cat === cat && (!onlyOpen || isOpen(x))).map(x => x.id);
  function catReorder(cat, openOrder) { return todoOp({ op: 'reorder', cat, order: openOrder.concat(items().filter(x => x.cat === cat && !isOpen(x)).map(x => x.id)) }).then(() => render()); }
  function place(arr, from, to, after) { if (after) to++; if (from < to) to--; return from === to ? null : moveIn(arr, from, to); }
  // a grouped category's open order is what the tab shows: country by country, his order inside each; a move inside one
  // country changes only that country's run, and dragging a country row moves its whole run
  const rowsOf = el => [...el.querySelectorAll('.qt-todo')].filter(r => !r.closest('.qt-fold')).map(r => r.dataset.id);
  const shownOpen = catEl => isGrouped(catEl.dataset.cat) ? groupsOf(items().filter(x => x.cat === catEl.dataset.cat && isOpen(x))).flatMap(g => g.items.map(x => x.id)) : catIds(catEl.dataset.cat, true);
  function reorderList(listEl, newSub) {          // listEl: a [data-list="todo"] (a category's, or one country's inside it)
    const catEl = listEl.closest('.qt-cat'), cat = catEl.dataset.cat, set = new Set(newSub); let k = 0;
    return catReorder(cat, shownOpen(catEl).map(id => set.has(id) ? newSub[k++] : id));
  }
  function reorderGroups(catEl, keyOrder) {
    const gs = groupsOf(items().filter(x => x.cat === catEl.dataset.cat && isOpen(x))), by = new Map(gs.map(g => [g.key, g]));
    return catReorder(catEl.dataset.cat, keyOrder.flatMap(k => (by.get(k) ? by.get(k).items.map(x => x.id) : [])));
  }
  function setCgOpen(id, on) { if (on) Q.cgOpen.add(id); else Q.cgOpen.delete(id); store.set('cg:open', [...Q.cgOpen]); }
  function catsReorder(visOrder) {               // hidden categories keep their places; the visible ones take the new order
    const all = cats().map(c => c.id); const vis = new Set(visOrder); let k = 0;
    return todoOp({ op: 'reorder_cats', order: all.map(id => vis.has(id) ? visOrder[k++] : id) }).then(() => render());
  }
  function setCatOpen(id, on) { if (on) Q.catOpen.add(id); else Q.catOpen.delete(id); store.set('cats:open', [...Q.catOpen]); }
  async function addTodoFrom(text, extra) {
    const ok = await todoOp(Object.assign({ op: 'add', text, cat: 'mine' }, extra || {}));
    if (ok) { toast('Added.'); setCatOpen('mine', true); render(); }
    return ok;
  }
  function onClick(e) {
    const r = root(); if (!r || !r.contains(e.target)) return;
    const el = e.target.closest('[data-act]'); if (!el) return;
    const act = el.dataset.act;
    if (e.target.closest('a[href]') && act !== 'jump' && act !== 'open-rel') return;   // a link inside a summary is a link
    if (act === 'reload') { e.preventDefault(); load(); return; }
    if (act.startsWith('qs-')) { e.preventDefault(); statusAct(el, e); return; }
    if (act === 'toggle') { const it = qItem(el.closest('.qt-item')); if (!it) return; const k = openKey(it); Q.open[k] = !Q.open[k]; render(); return; }
    if (act === 'pre-toggle') { const k = 'pre:' + el.dataset.k; Q.open[k] = !Q.open[k]; render(); return; }
    if (act === 'xo') { const id = el.closest('.qt-todo').dataset.id; if (Q.xo.has(id)) Q.xo.delete(id); else Q.xo.add(id); render(); return; }
    if (act === 'qa-details') { Q.addDetails = true; render(); setTimeout(() => { const d = $('#qt-qa-details'); if (d) d.focus(); }, 0); return; }
    if (act === 'hold') { const id = +el.closest('.qt-item').dataset.id; queueOp({ op: 'hold', id, hold: el.dataset.hold === '1' }).then(ok => { if (ok) toast(el.dataset.hold === '1' ? 'On hold.' : 'Resumed.'); }); return; }
    if (act === 'move') { const op = arrowMove(+el.closest('.qt-item').dataset.idx, +el.dataset.dir); if (op) queueOp(op); return; }
    if (act === 'to-todo') { const sec = (Q.queue.sections || []).find(s => s.name === el.dataset.sec); const it = sec && sec.items[+el.dataset.i]; if (it) addTodoFrom(it.text.replace(/\*\*/g, '').slice(0, 500)); return; }
    // the inbox
    if (act === 'cat-filter') { const id = el.dataset.cat; if (Q.catHidden.has(id)) Q.catHidden.delete(id); else Q.catHidden.add(id); store.set('cats:hidden', [...Q.catHidden]); badge(); render(); return; }
    if (act === 'cat-filter-all') { Q.catHidden.clear(); store.set('cats:hidden', []); badge(); render(); return; }
    if (act === 'cat-toggle') { const id = el.closest('.qt-cat').dataset.cat; setCatOpen(id, !Q.catOpen.has(id)); render(); return; }
    if (act === 'cg-toggle') { const id = cgId(el.closest('.qt-cat').dataset.cat, el.closest('.qt-cg').dataset.cg); setCgOpen(id, !Q.cgOpen.has(id)); render(); return; }
    if (act === 'cat-move') { const vis = visibleCats().map(c => c.id), i = +el.closest('.qt-cat').dataset.idx, j = i + (+el.dataset.dir); if (j < 0 || j >= vis.length) return; catsReorder(moveIn(vis, i, j)); return; }
    if (act === 'jump') { e.preventDefault(); setCatOpen(el.dataset.cat, true); const ji = items().find(x => x.id === el.dataset.id); if (ji && isGrouped(ji.cat)) setCgOpen(cgId(ji.cat, gkey(ji)), true); render(); const row = r.querySelector(`.qt-todo[data-id="${el.dataset.id}"]`); if (row) { row.scrollIntoView({ block: 'center' }); row.style.outline = '2px solid #2D6B50'; setTimeout(() => { row.style.outline = ''; }, 1600); } return; }
    if (act === 'view') { const row = el.closest('.qt-todo'), it = items().find(x => x.id === row.dataset.id); openViewer(row.dataset.id, it ? it.cat : (row.closest('[data-cat]') || {}).dataset.cat); return; }
    if (act === 'todo-toggle') { const id = el.closest('.qt-todo').dataset.id; todoOp({ op: 'toggle', id, done: el.checked }).then(() => render()); return; }
    if (act === 'todo-edit') { Q.editing = el.closest('.qt-todo').dataset.id; render(); setTimeout(() => { const t = $('#qt-te-text'); if (t) { t.focus(); t.setSelectionRange(t.value.length, t.value.length); } }, 0); return; }
    if (act === 'todo-cancel') { Q.editing = null; render(); return; }
    if (act === 'reply-edit') { const id = el.closest('.qt-todo').dataset.id, it = items().find(x => x.id === id) || {};
      todoOp({ op: 'update', id, reply: '', status: it.kind === 'question' || it.status === 'new' ? 'seen' : it.status }).then(() => render()); return; }
    if (act === 'todo-delete') { const row = el.closest('.qt-todo'), id = row.dataset.id, it = items().find(x => x.id === id); if (!it) return; if (!confirm('Delete “' + it.title.slice(0, 80) + '”?')) return; todoOp({ op: 'delete', id }).then(() => render()); return; }
    if (act === 'todo-move') { const row = el.closest('.qt-todo'), id = row.dataset.id, list = row.closest('[data-list="todo"]'); const sub = rowsOf(list), i = sub.indexOf(id), j = i + (+el.dataset.dir); if (i < 0 || j < 0 || j >= sub.length) return; reorderList(list, moveIn(sub, i, j)); return; }
    if (act === 'todo-clear-done') { e.preventDefault(); const cat = el.dataset.cat, cg = el.dataset.cg, its = items().filter(x => !isOpen(x) && (!cat || x.cat === cat) && (!cg || gkey(x) === cg) && !Q.catHidden.has(x.cat)), ids = its.map(x => x.id);
      if (!ids.length || !confirm('Delete the ' + ids.length + ' done item' + (ids.length === 1 ? '' : 's') + '?')) return;
      todoOp({ op: 'batch', ops: ids.map(id => ({ op: 'delete', id })) }).then(() => render()); return; }
    if (act === 'open-rel') {                 // inside the suite the editor opens it; elsewhere the link is the page on the photo server
      if (window.__queueOpenRel || (window.__apOpen && window.showView)) { e.preventDefault(); openRel(el.dataset.rel); } else el.target = '_blank';
    }
  }
  function onChange(e) {
    const r = root(); if (!r || !r.contains(e.target)) return;
    if (e.target.matches('.qt-stsel')) { const id = e.target.closest('.qt-todo').dataset.id; todoOp({ op: 'update', id, status: e.target.value }).then(() => render()); }
    if (e.target.matches('input[type=date]')) e.target.classList.toggle('has', !!e.target.value);
  }
  async function onSubmit(e) {
    const r = root(); if (!r || !r.contains(e.target)) return;
    const f = e.target.closest('form[data-form]'); if (!f) return;
    e.preventDefault();
    if (f.dataset.form === 'queue-add') {
      const title = $('#qt-qa-title').value.trim(); if (!title) return;
      const details = $('#qt-qa-details') ? $('#qt-qa-details').value : '';
      const gsel = $('#qt-qa-group'), group = gsel ? +gsel.value : null;
      if (gsel) Q.addGroup = group;
      const ok = await queueOp(Object.assign({ op: 'add', title, details }, group != null ? { group } : {}));
      if (ok) { Q.addDetails = false; toast(group != null ? 'Added to “' + qGroupName(group) + '”.' : 'Added.'); render(); setTimeout(() => { const t = $('#qt-qa-title'); if (t) t.focus(); }, 0); }
    } else if (f.dataset.form === 'todo-add') {
      const title = $('#qt-ta-text').value.trim(); if (!title) return;
      const cat = $('#qt-ta-cat').value, due = $('#qt-ta-due').value || null, link = $('#qt-ta-link').value.trim() || null;
      if (await todoOp({ op: 'add', title, cat, due, links: link ? (/^https?:/.test(link) ? { url: link } : { editor: link }) : {} })) { setCatOpen(cat, true); render(); setTimeout(() => { const t = $('#qt-ta-text'); if (t) t.focus(); }, 0); }
    } else if (f.dataset.form === 'todo-edit') {
      const id = f.closest('.qt-todo').dataset.id, it = items().find(x => x.id === id);
      const title = $('#qt-te-text').value.trim(); if (!title) return;
      const link = $('#qt-te-link').value.trim(); const links = Object.assign({}, it.links || {}); delete links.url; delete links.editor;
      if (link) links[/^https?:/.test(link) ? 'url' : 'editor'] = link;
      const ok = await todoOp({ op: 'update', id, title, due: $('#qt-te-due').value || '', links, note: $('#qt-te-note').value.trim() });
      if (ok) { Q.editing = null; render(); }
    } else if (f.dataset.form === 'todo-reply') {
      const id = f.closest('.qt-todo').dataset.id, ta = f.querySelector('textarea'); const reply = ta.value.trim(); if (!reply) return;
      const it = items().find(x => x.id === id) || {}, q = it.kind === 'question';
      const st = q ? 'done' : (it.status === 'new' ? 'seen' : it.status);
      if (await todoOp({ op: 'update', id, reply, status: st })) { toast(q ? 'Answer saved.' : 'Reply saved: Claude picks it up.'); render(); }
    }
  }
  function onKey(e) {
    const r = root(); if (!r || !r.contains(e.target)) return;
    if (e.key === 'Escape' && Q.editing) { Q.editing = null; render(); return; }
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.qt-trow[role=button]')) { e.preventDefault(); e.target.click(); }
    if (e.target.matches('#qt-qa-details, .qt-reply textarea') && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.target.form.requestSubmit(); }
  }
  function onToggleFold(e) {
    const d = e.target; if (!d.matches || !d.matches('details[data-fold]')) return;
    const k = d.dataset.fold;
    if (k === 'held') { if (!!Q.open['sec:held'] !== d.open) { Q.open['sec:held'] = d.open; drawStatus(); } return; }   // its rows are drawn only while open
    if (k.startsWith('done:')) Q.doneOpen[k.slice(5)] = d.open; else Q.open['sec:' + k] = d.open;
  }

  // ------------------------------------------------------------------ drag to reorder: pointer events, so mouse and touch
  // behave the same (HTML5 drag-and-drop has no touch on phones). The grip starts it; the arrows are the keyboard way.
  const ROW = { queue: '.qt-item, .qt-qgh', todo: '.qt-todo', cats: '.qt-cat', cgroups: '.qt-cg, .qt-cgsec' };
  function onPointerDown(e) {
    const grip = e.target.closest && e.target.closest('.qt-grip'); if (!grip || e.button) return;
    const list = grip.closest('[data-list]'); if (!list || !ROW[list.dataset.list]) return;
    const kind = list.dataset.list, row = grip.closest(ROW[kind]); if (!row) return;
    if (kind === 'cats' && grip.closest('[data-list="todo"]')) return;           // a to-do's grip inside an open category
    if (kind === 'cgroups' && grip.closest('[data-list="todo"]')) return;        // a to-do's grip inside an open country
    Q.drag = { kind, list, row, cat: list.dataset.cat, x: e.clientX, y: e.clientY, live: false, id: e.pointerId };
    try { grip.setPointerCapture(e.pointerId); } catch (err) {}
    e.preventDefault();
  }
  function onPointerMove(e) {
    const d = Q.drag; if (!d || e.pointerId !== d.id) return;
    if (!d.live) { if (Math.abs(e.clientY - d.y) < 5 && Math.abs(e.clientX - d.x) < 5) return; d.live = true; d.row.classList.add('dragging'); document.body.classList.add('qt-dragging'); }
    clearDrop();
    const under = document.elementFromPoint(e.clientX, e.clientY); const row = under && under.closest && under.closest(ROW[d.kind]);
    // a country row may land in the other part (Upcoming / Backlog) of the same category, or on its heading
    const sameCat = d.kind === 'cgroups' && row && row.closest('.qt-cat') === d.list.closest('.qt-cat') && !row.closest('[data-list="todo"]')
      && !(row.classList.contains('qt-cg') && row.dataset.cg === '_other') && !(d.row.dataset.cg === '_other' && row.closest('[data-part]') && row.closest('[data-part]').dataset.part === 'up');
    if (!row || row === d.row || !(d.list.contains(row) || sameCat) || (d.kind === 'todo' && row.closest('.qt-fold')) || (d.kind === 'todo' && row.closest('[data-list="todo"]') !== d.list)) { d.over = null; return; }
    const rc = row.getBoundingClientRect(); d.over = row; d.after = e.clientY > rc.top + rc.height / 2;
    row.classList.add(d.after ? 'drop-after' : 'drop-before');
    const sc = root(); if (sc) { const b = sc.getBoundingClientRect(); if (e.clientY < b.top + 40) sc.scrollTop -= 12; else if (e.clientY > b.bottom - 40) sc.scrollTop += 12; }
  }
  function onPointerUp(e) {
    const d = Q.drag; if (!d || e.pointerId !== d.id) return;
    Q.drag = null; clearDrop(); d.row.classList.remove('dragging'); document.body.classList.remove('qt-dragging');
    if (!d.live || !d.over) return;
    if (d.kind === 'queue') {
      const id = +d.row.dataset.id, over = d.over;
      if (over.classList.contains('qt-qgh')) {       // a group heading: its lower half = the top of that group, its upper half = the end of the group above
        const prev = over.previousElementSibling;
        if (!d.after && prev && prev.classList.contains('qt-item')) { if (+prev.dataset.id !== id) queueOp({ op: 'move', id, after: +prev.dataset.id }); }
        else queueOp({ op: 'move', id, group: +over.dataset.group, position: 'top' });
      } else if (+over.dataset.id !== id) queueOp(Object.assign({ op: 'move', id }, d.after ? { after: +over.dataset.id } : { before: +over.dataset.id }));
    }
    else if (d.kind === 'cats') { const vis = visibleCats().map(c => c.id); const n = place(vis, vis.indexOf(d.row.dataset.cat), vis.indexOf(d.over.dataset.cat), d.after); if (n) catsReorder(n); }
    else if (d.kind === 'cgroups') {
      const catEl = d.list.closest('.qt-cat'), key = d.row.dataset.cg;
      const to = d.over.classList.contains('qt-cgsec') ? d.over.dataset.part : (d.over.closest('[data-part]') || {}).dataset.part, from = d.list.dataset.part;
      const reorder = () => { if (d.over.classList.contains('qt-cgsec')) return render();
        const ks = [...catEl.querySelectorAll('.qt-cg')].map(g => g.dataset.cg); const n = place(ks, ks.indexOf(key), ks.indexOf(d.over.dataset.cg), d.after); if (n) return reorderGroups(catEl, n); render(); };
      if (to && from && to !== from) todoOp({ op: 'set_upcoming', key, upcoming: to === 'up' }).then(ok => { if (ok) { toast((to === 'up' ? 'Moved to Upcoming' : 'Moved to Backlog') + ' (the draft folder stays where it is).'); reorder(); } });
      else reorder();
    }
    else { const sub = rowsOf(d.list); const n = place(sub, sub.indexOf(d.row.dataset.id), sub.indexOf(d.over.dataset.id), d.after); if (n) reorderList(d.list, n); }
  }
  function clearDrop() { document.querySelectorAll('.drop-before,.drop-after').forEach(x => x.classList.remove('drop-before', 'drop-after')); }

  // ------------------------------------------------------------------ the Push package (Kevin, 2026-11-10: "make the push package a
  // separate section on the queue tab ... shows the individual issues like the artifact? I should be able to respond to each one
  // of them and click on the options offered"). tools/push_package.py serves .tmp/push_package/package.json (what is in the
  // push: built by that script, never written from here) and answers.json (his answers). An answer names only the fields it
  // sets and the revision it saw of each; the server refuses a field another tab wrote since (409, nothing written) and sends
  // the newest, so a stale tab never wipes an answer. Its element lives across the tab's re-renders (typing, loaded images).
  const P = { data: null, ver: null, answers: {}, rev: 0, state: 'idle', error: '', el: null, ops: {}, own: new Set(), chain: Promise.resolve(),
              open: store.get('push:open', true), secOpen: new Set(store.get('push:secs', [])), dev: store.get('push:dev', null) || (window.innerWidth >= 900 ? 'desktop' : 'phone'),   // unpicked: what fits the screen
              drafts: {}, noteOpen: new Set(), replyEdit: new Set(),
              status: {}, checks: null, ready: null, settledOpen: new Set(), stOpen: store.get('push:settled', false), poll: null };
  // Nov 11 (Kevin: "The changes should address themselves as I work through ... create a 'addressed' section"): the server
  // says what state each item is in (open: needs him; working: he answered, Claude is carrying it out; settled: done, or
  // nothing to do). Open items stay up top; settled ones fold into Settled. The checks are live (checks.json + the editor's own).
  const stOf = id => (P.status[id] || {}).state || 'open';
  const statusSig = () => Object.keys(P.status).map(k => k + ':' + P.status[k].state + ':' + (P.status[k].text || '')).join('|');
  const checksSig = () => P.checks ? [P.checks.ran, P.checks.running, (P.checks.items || []).map(c => c.id + (c.ok ? 1 : 0) + (c.stale ? 's' : '')).join(',')].join('|') : '';
  const pushFile = src => API + '/api/push/file/' + String(src).split('/').map(encodeURIComponent).join('/');
  const plain = s => String(s || '').replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '$1').replace(/\*\*|`/g, '');
  function pmd(s) {                                       // the package's small markdown: **bold**, `code`, [text](https://...)
    let o = esc(s || '');
    o = o.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (m, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
    return o.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  }
  const pkgCalls = () => (P.data && P.data.calls) || [];
  const pagesSec = () => { const u = (P.data && P.data.unreviewed_pages) || []; return u.length ? { id: '_pages', title: 'Changed pages not described yet',
    intro: 'These pages differ from the live site and nothing above describes them yet. Hold one back if it should not go.',
    changes: u.map(x => ({ id: x.id, title: x.page, page: x.page, note: x.status ? 'Page ' + x.status + '.' : '' })) } : null; };
  const pkgSecs = () => ((P.data && P.data.sections) || []).concat(pagesSec() ? [pagesSec()] : []);
  const allChanges = () => pkgSecs().flatMap(s => s.changes || []);
  function ans(id) {                                      // the server's answer with this tab's writes in flight laid over it
    const a = P.answers[id] || {}, ops = P.ops[id];
    return ops && ops.length ? Object.assign({}, a, ...ops) : a;
  }
  const isAnswered = c => { const a = ans(c.id); return !!(a.choice || a.reply); };
  function pushOpenCalls() { return P.data ? pkgCalls().concat(allChanges()).filter(c => stOf(c.id) === 'open').length : 0; }   // waiting on him
  function findEntry(id) {
    for (const c of pkgCalls()) if (c.id === id) return { kind: 'call', e: c };
    for (const s of pkgSecs()) for (const c of s.changes || []) if (c.id === id) return { kind: 'change', e: c, sec: s };
    return null;
  }
  async function loadPush() {
    const w0 = P.wseq || 0;
    try {
      const j = await api('/api/push');
      // an answer of his saved while this read was on its way (or still being saved): the read may be the older one, and
      // showing it put his last pick back and made his next click read as "changed in another tab" (stress test 2026-11-11).
      // Keep what the write returned, take only the checks, and read again once the writes are in.
      const crossed = P.data && j.package && j.package.id === P.data.id && ((P.wseq || 0) !== w0 || Object.keys(P.ops).length > 0);
      if (crossed) { P.checks = j.checks || P.checks; P.state = 'ok'; if (!P.reread) P.reread = setTimeout(() => { P.reread = null; loadPush(); }, 700); }
      else { P.data = j.package || null; P.ver = j.package_version || null; P.answers = j.answers || {}; P.rev = j.rev || 0; P.state = 'ok';
        P.status = j.status || {}; P.checks = j.checks || null; P.ready = j.ready || null; }
      if (P.checks && P.checks.running && !P.poll) P.poll = setTimeout(() => { P.poll = null; loadPush(); }, 5000);   // checks running: follow them
      if (pushKey() !== P.drawn) renderPush();            // a quiet refresh that found nothing new leaves his typing alone
    } catch (e) {
      P.state = e.status === 404 ? 'old' : e.status === 0 ? 'down' : 'error'; P.error = e.message;
      if (P.state === 'old' || !P.data) renderPush();
      // a dropped request (this box's loopback drops one now and then) must not hide the section until the next minute's refresh
      if (P.state !== 'old' && !P.retry) P.retry = setTimeout(() => { P.retry = null; loadPush(); }, P.state === 'down' ? 6000 : 4000);
    }
    badge();
  }
  function pushEl() {
    if (!P.el) {
      const el = document.createElement('section'); el.className = 'pp'; el.id = 'qt-push'; el.hidden = true; el.setAttribute('aria-label', 'Push package');
      el.addEventListener('click', onPushClick); el.addEventListener('submit', onPushSubmit); el.addEventListener('input', onPushInput);
      el.addEventListener('keydown', onPushKey); el.addEventListener('toggle', onPushToggle, true);
      P.el = el;
    }
    return P.el;
  }
  function mountPush(r) { const slot = r.querySelector('#qt-push-slot'); if (!slot) return; const el = pushEl(); slot.replaceWith(el); if (!el.dataset.drawn) renderPush(); }
  function keepFocus(scope, fn) {                         // re-draw part of the section without losing the caret or the focused button
    const f = document.activeElement, inside = f && scope && scope.contains(f);
    const k = inside ? { id: f.id, act: f.dataset && f.dataset.act, pid: f.dataset && f.dataset.id, opt: f.dataset && f.dataset.opt,
      sel: 'selectionStart' in f ? [f.selectionStart, f.selectionEnd] : null } : null;
    fn();
    if (!k) return;
    let el = k.id ? document.getElementById(k.id) : null;
    if (!el && k.act) el = P.el.querySelector(`[data-act="${k.act}"]${k.pid ? `[data-id="${CSS.escape(k.pid)}"]` : ''}${k.opt ? `[data-opt="${CSS.escape(k.opt)}"]` : ''}`);
    if (el) { el.focus(); try { if (k.sel && k.sel[0] != null) el.setSelectionRange(k.sel[0], k.sel[1]); } catch (e) {} }
  }
  const pushKey = () => [P.state, P.data ? P.data.id : '', P.ver, P.rev, statusSig(), checksSig()].join('|');     // what the section shows; drawn again only when it moves
  function renderPush() {
    const el = pushEl(); el.dataset.drawn = '1'; P.drawn = pushKey();
    if (P.state === 'old') {
      el.hidden = false; el.classList.remove('open');
      el.innerHTML = `<div class="pp-head"><span class="pp-fold" style="cursor:default;padding-left:10px">Push package</span><span class="pp-msg">The photo server needs a restart to show it: re-click the Editor Suite shortcut.</span></div>`;
      return;
    }
    if (!P.data) { el.hidden = true; el.innerHTML = ''; return; }
    el.hidden = false; el.classList.toggle('open', !!P.open);
    keepFocus(el, () => { el.innerHTML = renderPushHead() + (P.open ? `<div class="pp-body">${renderPushBody()}</div>` : ''); });
  }
  function renderPushHead() {
    const items = pkgCalls().concat(allChanges()), n = st => items.filter(c => stOf(c.id) === st).length;
    const open = n('open'), work = n('working'), done = n('settled');
    const ck = (P.checks && P.checks.items) || [], green = ck.filter(c => c.ok && !c.stale).length;
    const ready = !open && !work && ck.length && green === ck.length;
    return `<div class="pp-head" id="pp-head">
      <button type="button" class="pp-fold" data-act="pp-fold" aria-expanded="${!!P.open}"><span class="chev"></span>Push package${P.data.built ? ` <span class="sub">${esc(shortDate(P.data.built))}</span>` : ''}</button>
      <span class="pp-stats">
        <span class="pp-stat ${open ? 'todo' : 'done'}" data-stat="open"><b>${open}</b> need${open === 1 ? 's' : ''} you</span>
        ${work ? `<span class="pp-stat held" data-stat="working"><b>${work}</b> Claude is on</span>` : ''}
        <span class="pp-stat" data-stat="settled"><b>${done}</b> settled</span>
        <span class="pp-stat ${ck.length && green === ck.length ? 'done' : 'todo'}" data-stat="checks"><b>${green} of ${ck.length}</b> checks green</span>
      </span>
      <span class="pp-how">${ready ? 'Ready: say <b>push</b> in the session.' : 'Not ready to push yet.'}</span>
    </div>`;
  }
  const listHtml = list => list && list.length ? `<ul class="pp-list">${list.map(x => `<li>${pmd(x)}</li>`).join('')}</ul>` : '';
  const moreHtml = (e, withList) => e.more || (withList && (e.list || []).length) ? `<details class="pp-more"><summary>Details</summary>${e.more ? `<p class="pp-note">${pmd(e.more)}</p>` : ''}${withList ? listHtml(e.list) : ''}</details>` : '';
  function stateLine(id) {
    const s = P.status[id]; if (!s) return '';
    if (s.state === 'open' && s.reopened) return `<div class="pp-state work"><b>Reopened.</b>${esc(s.text || '')}</div>`;   // the held-back drawer put it back
    if (s.state === 'open') return '';
    return s.state === 'working' ? `<div class="pp-state work"><b>Claude is on it.</b>${s.answer ? ` You: ${esc(s.answer)}` : ''}</div>`
      : `<div class="pp-state done"><b>Settled.</b> ${pmd(s.text || '')}</div>`;
  }
  function pageChips(e) {
    const parts = String(e.page || '').split(/,\s*/).filter(Boolean), isPath = p => /^[\w.-]+(\/[\w.-]+)?\.html$/.test(p);
    let h = '';
    if (parts.length && parts.every(isPath)) for (const p of parts) h += `<a class="qt-chip link" href="${esc(siteUrl(p))}" data-act="open-rel" data-rel="${esc(p)}" title="Open ${esc(p)} in the editor">${I.doc}${esc(p.replace(/\.html$/, ''))}</a>`;
    else if (e.page) h += `<span class="qt-chip plain" title="${esc(e.page)}">${esc(e.page)}</span>`;
    if (e.url) h += `<a class="qt-chip link" href="${esc(e.url)}" target="_blank" rel="noopener" title="The live page now: ${esc(e.url)}">${I.ext}live</a>`;
    return h ? `<div class="qt-chips" style="margin-top:8px">${h}</div>` : '';
  }
  function tierFor(e, dev) {                              // the phone tier for Phone, else the widest (desktop, tablet, icons)
    const ts = (e.shots || []).filter(t => t.after); if (!ts.length) return null;
    const ph = ts.find(t => t.kind === 'phone'), dk = ts.find(t => t.kind !== 'phone');
    return dev === 'phone' ? (ph || dk) : (dk || ph);
  }
  function shotsHtml(e) {
    const t = tierFor(e, P.dev); if (!t) return '';
    const dv = t.kind === 'phone' ? 'phone' : 'desktop';
    const fig = (s, cls, cap) => `<figure class="pp-shot ${cls}"><figcaption>${cap}</figcaption>${s
      ? `<button type="button" class="pp-img" data-act="pp-view" data-id="${esc(e.id)}" data-dev="${dv}" title="Open in the viewer" aria-label="${cap}: open in the viewer"><img src="${esc(pushFile(s.src))}" ${s.w ? `width="${s.w}" height="${s.h}"` : ''} loading="lazy" alt="${esc(plain(e.title))}, ${cap.toLowerCase()}"></button>`
      : `<div class="pp-empty">${esc(t.before_note || 'Nothing before')}</div>`}</figure>`;
    const other = (e.shots || []).filter(x => x !== t && x.after).map(x => x.label);
    return `<div class="pp-ba ${t.kind === 'phone' ? 'phone' : ''}">${fig(t.before, 'b', 'Before')}${fig(t.after, 'a', 'After')}</div>
      <div class="pp-tier"><span>${esc(t.label || '')}</span>${other.length ? `<span>· also ${esc(other.join(', '))}</span>` : ''}</div>`;
  }
  function optButtons(c, a) {
    return `<div class="pp-opts" role="radiogroup" aria-label="Your call">${(c.options || []).map(o => { const on = a.choice === o.id;
      return `<button type="button" class="pp-opt ${on ? 'on' : ''}" role="radio" aria-checked="${on}" data-act="pp-choose" data-id="${esc(c.id)}" data-opt="${esc(o.id)}" title="${on ? 'Your pick (click again to clear it)' : 'Pick this'}">${esc(o.label)}${o.recommended ? '<span class="rec">Recommended</span>' : ''}</button>`; }).join('')}</div>`;
  }
  function replyHtml(c, a) {
    const fid = 'pp-rp-' + c.id, editing = P.replyEdit.has(c.id);
    if (a.reply && !editing) return `<div class="qt-note"><b>Reply</b>${esc(a.reply)}${a.via ? ` <span style="color:var(--qt-faint)">· ${esc(a.via)}</span>` : ''} <button type="button" class="qt-more" data-act="pp-reply-edit" data-id="${esc(c.id)}" style="padding:0 4px">edit</button></div>`;
    const v = P.drafts[fid] != null ? P.drafts[fid] : (a.reply || '');
    return `<form class="qt-reply" data-pform="reply" data-id="${esc(c.id)}" autocomplete="off"><textarea id="${esc(fid)}" placeholder="Anything else for Claude…" maxlength="2000" rows="1" aria-label="Reply to Claude">${esc(v)}</textarea><button type="submit" class="qt-btn sm primary">Send</button>${editing ? `<button type="button" class="qt-btn sm" data-act="pp-reply-cancel" data-id="${esc(c.id)}">Cancel</button>` : ''}</form>`;
  }
  function renderCall(c) {
    const a = ans(c.id), shots = shotsHtml(c), answered = !!(a.choice || a.reply), rec = (c.options || []).find(o => o.recommended);
    const diff = (c.diff || []).length ? `<div class="pp-diff">${c.diff.map(x => `${x.label ? `<div class="lbl">${pmd(x.label)}</div>` : ''}<div><span class="lb">Was</span><del>${pmd(x.before)}</del></div><div><span class="lb">Now</span><ins>${pmd(x.after)}</ins></div>`).join('')}</div>` : '';
    const hint = a.choice ? (a.via && a.times && a.times.choice ? `<div class="pp-ans">Answered in ${esc(a.via)}, ${esc(shortDate(a.times.choice))}.</div>` : '')
      : `<div class="pp-hint">No pick yet: ${rec ? '“' + esc(rec.label) + '” goes' : 'it goes as described'}.</div>`;
    return `<article class="qt-card pp-call ${answered ? 'answered' : ''} ${shots ? '' : 'noshots'}" id="pp-e-${esc(c.id)}" data-pid="${esc(c.id)}">
      <div class="pp-cmain"><div class="pp-ctop"><span class="qt-num">${esc(c.num)}</span><h3 class="pp-ct">${pmd(c.title)}</h3></div>
        <div class="pp-cbody">${diff}${c.note ? `<p class="pp-note">${pmd(c.note)}</p>` : ''}${listHtml(c.list)}${moreHtml(c, false)}${pageChips(c)}${optButtons(c, a)}${hint}${stateLine(c.id)}${replyHtml(c, a)}</div></div>
      ${shots ? `<div class="pp-shots">${shots}</div>` : ''}</article>`;
  }
  function flagButtons(c, a) {
    return `<button type="button" class="pp-tg ok ${a.ok ? 'on' : ''}" data-act="pp-ok" data-id="${esc(c.id)}" aria-pressed="${!!a.ok}" title="${a.ok ? 'Marked as looking good (click to undo)' : 'Mark it as looking good'}">${I.check}Looks good</button>
      <button type="button" class="pp-tg hold ${a.hold ? 'on' : ''}" data-act="pp-hold" data-id="${esc(c.id)}" aria-pressed="${!!a.hold}" title="${a.hold ? 'Held back: Claude leaves it out of the push (click to put it back in)' : 'Hold this back from the push'}">${I.pause}${a.hold ? 'Held back' : 'Hold back'}</button>`;
  }
  function renderChange(c) {
    const a = ans(c.id), shots = shotsHtml(c), fid = 'pp-nt-' + c.id, noteOpen = P.noteOpen.has(c.id);
    const note = noteOpen ? `<form class="pp-noteform" data-pform="note" data-id="${esc(c.id)}" autocomplete="off"><input type="text" id="${esc(fid)}" value="${esc(P.drafts[fid] != null ? P.drafts[fid] : (a.note || ''))}" placeholder="Note for Claude…" maxlength="2000" aria-label="Note for Claude"><button type="submit" class="qt-btn sm primary">Save</button><button type="button" class="qt-btn sm" data-act="pp-note-cancel" data-id="${esc(c.id)}">Cancel</button></form>`
      : a.note ? `<div class="qt-note"><b>Note</b>${esc(a.note)} <button type="button" class="qt-more" data-act="pp-note" data-id="${esc(c.id)}" style="padding:0 4px">edit</button></div>` : '';
    const wasnow = c.was || c.now ? `<div class="pp-wasnow"><span class="lb">Was</span><span>${pmd(c.was)}</span><span class="lb">Now</span><span>${pmd(c.now)}</span></div>` : '';
    return `<div class="pp-chg ${shots ? '' : 'noshots'} ${a.hold ? 'held' : ''} ${a.ok ? 'ok' : ''}" id="pp-e-${esc(c.id)}" data-pid="${esc(c.id)}">
      <div class="pp-cmain"><div class="pp-ttl">${pmd(c.title)}</div>${pageChips(c)}${c.note ? `<p class="pp-note">${pmd(c.note)}</p>` : ''}${moreHtml(c, true)}${wasnow}${stateLine(c.id)}
        <div class="pp-acts">${flagButtons(c, a)}${noteOpen || a.note ? '' : `<button type="button" class="pp-tg" data-act="pp-note" data-id="${esc(c.id)}">${I.pen}Note</button>`}${a.seen && !a.ok && !a.hold ? '<span class="qt-chip plain" title="Opened in the viewer or reviewed in My to-do">seen</span>' : ''}</div>${note}</div>
      ${shots ? `<div class="pp-shots">${shots}</div>` : ''}</div>`;
  }
  function secCnt(s) {
    const ch = (s.changes || []).filter(c => stOf(c.id) !== 'settled'), held = ch.filter(c => ans(c.id).hold).length, ok = ch.filter(c => ans(c.id).ok).length;
    return `<span class="cnt"><span class="n">${ch.length}</span>${held ? `<span class="n needs">${held} held</span>` : ''}${ok ? `<span class="n" style="background:var(--qt-green-bg);color:var(--qt-green)">${ok} good</span>` : ''}</span>`;
  }
  function renderSec(s) {
    const open = P.secOpen.has(s.id);
    let rows = '', g = null;
    if (open) for (const c of (s.changes || []).filter(c => stOf(c.id) !== 'settled')) { if ((c.group || null) !== g) { g = c.group || null; if (g) rows += `<div class="pp-sub">${pmd(g)}</div>`; } rows += renderChange(c); }
    const det = open && s.details ? `<div class="pp-sub">${pmd(s.details.title)}</div><ul class="pp-list" style="padding:8px 14px 12px 2rem;margin:0">${s.details.list.map(x => `<li>${pmd(x)}</li>`).join('')}</ul>` : '';
    return `<details class="qt-card qt-fold pp-sec" data-psec="${esc(s.id)}" id="pp-sec-${esc(s.id)}" ${open ? 'open' : ''}><summary>${esc(plain(s.title))} ${secCnt(s)}</summary>${open ? `${s.intro ? `<p class="pp-sintro">${pmd(s.intro)}</p>` : ''}${rows}${det}` : ''}</details>`;
  }
  function settledRow(kind, e) {
    const st = P.status[e.id] || {}, on = P.settledOpen.has(e.id);
    return `<div class="pp-srow ${on ? 'on' : ''}" id="pp-s-${esc(e.id)}"><button type="button" class="pp-sbtn" data-act="pp-sopen" data-id="${esc(e.id)}" aria-expanded="${on}">
        <span class="pp-st">${kind === 'call' ? `<span class="qt-num">${esc(e.num)}</span>` : ''}${esc(plain(e.title))}</span>
        <span class="pp-sd">${st.answer && !String(st.text || '').startsWith(st.answer) ? `<span class="pp-sa">${esc(st.answer.length > 70 ? st.answer.slice(0, 68) + '…' : st.answer)}</span>` : ''}${pmd(st.text || '')}</span></button>
      ${on ? `<div class="pp-sfull">${kind === 'call' ? renderCall(e) : renderChange(e)}</div>` : ''}</div>`;
  }
  function renderChecks() {
    const ck = (P.checks && P.checks.items) || [], running = P.checks && P.checks.running;
    const rows = ck.map(c => `<div class="pp-check"><span class="pp-pill ${c.ok && !c.stale ? '' : 'fix'}">${c.ok && !c.stale ? 'Green' : c.stale ? 'Re-run' : 'Fix'}</span><span>${pmd(c.text)}${c.stale && c.ok ? ' <span style="color:var(--qt-faint)">(files changed since it ran)</span>' : ''}</span></div>`).join('');
    const ran = P.checks && P.checks.ran ? `Last run ${esc(shortDate(P.checks.ran))}.` : 'Not run yet.';
    return `<div class="qt-group">Checks</div><div class="qt-card">${rows}<div class="pp-check pp-run"><span></span><span>${running ? '<b>Running the checks…</b> this updates by itself.' : ran}
      <button type="button" class="qt-btn sm" data-act="pp-run-checks" ${running ? 'disabled' : ''}>${running ? 'Running…' : 'Run the checks'}</button></span></div></div>`;
  }
  function renderPushBody() {
    const d = P.data, calls = pkgCalls();
    let h = `<div class="pp-lead"><p>${pmd(d.verdict || '')}</p><span class="pp-dev" role="group" aria-label="Screenshots">${['desktop', 'phone'].map(v =>
      `<button type="button" class="qt-fchip ${P.dev === v ? 'on' : ''}" data-act="pp-dev" data-dev="${v}" aria-pressed="${P.dev === v}">${v === 'desktop' ? 'Desktop shots' : 'Phone shots'}</button>`).join('')}</span></div>`;
    const liveCalls = calls.filter(c => stOf(c.id) !== 'settled'), liveSecs = pkgSecs().filter(s => (s.changes || []).some(c => stOf(c.id) !== 'settled'));
    const nLive = liveCalls.length + liveSecs.reduce((n, s) => n + s.changes.filter(c => stOf(c.id) !== 'settled').length, 0);
    h += `<div class="qt-group">Needs you <span class="n">${nLive}</span></div>`;
    if (!nLive) h += `<div class="qt-card pp-clear">Nothing needs you. Everything you answered is carried out and in <b>Settled</b>.</div>`;
    if (liveCalls.length) {
      if (d.calls_intro) h += `<p class="pp-foot" style="margin:0 2px 10px">${pmd(d.calls_intro)}</p>`;
      h += `<div class="pp-calls">${liveCalls.map(renderCall).join('')}</div>`;
    }
    h += liveSecs.map(renderSec).join('');
    h += renderChecks();
    if (d.summary && d.summary.length) h += `<div class="qt-group">What's in the push</div><div class="qt-card">${d.summary.map(r => `<div class="pp-sum"><span class="g">${pmd(r.group)}</span><span>${pmd(r.notices)}</span><span class="f">${pmd(r.files)}</span></div>`).join('')}</div>`;
    const setCalls = calls.filter(c => stOf(c.id) === 'settled'), setSecs = pkgSecs().map(s => ({ s, ch: (s.changes || []).filter(c => stOf(c.id) === 'settled') })).filter(x => x.ch.length);
    const nSet = setCalls.length + setSecs.reduce((n, x) => n + x.ch.length, 0);
    if (nSet) {
      let rows = setCalls.length ? `<div class="pp-sub">Your calls</div>${setCalls.map(c => settledRow('call', c)).join('')}` : '';
      for (const x of setSecs) rows += `<div class="pp-sub">${esc(plain(x.s.title))}</div>${x.ch.map(c => settledRow('change', c)).join('')}`;
      h += `<details class="qt-card qt-fold pp-sec pp-settled" data-psettled="1" ${P.stOpen ? 'open' : ''}><summary>Settled <span class="cnt"><span class="n" style="background:var(--qt-green-bg);color:var(--qt-green)">${nSet}</span></span></summary>
        ${P.stOpen ? `<p class="pp-sintro">You answered these and they're carried out, or there was nothing to do. Click one to see it or change your answer; a new answer moves it back up.</p>${rows}` : ''}</details>`;
    }
    if (calls.length && d.calls_footnote) h += `<p class="pp-foot">${pmd(d.calls_footnote)}</p>`;
    if ((d.push_steps || []).length) h += `<div class="qt-group">How to push</div><div class="qt-card">${d.how_intro ? `<p class="pp-sintro" style="border:0;padding-bottom:0">${pmd(d.how_intro)}</p>` : ''}<ol class="pp-steps">${d.push_steps.map(x => `<li>${pmd(x)}</li>`).join('')}</ol></div>`;
    h += `<p class="pp-small">${pmd(d.footer || '')}</p>`;
    return h;
  }
  function renderPushPart(id) {
    if (!P.el || !P.data || !P.el.dataset.drawn) return;
    const f = findEntry(id), node = document.getElementById('pp-e-' + id);
    keepFocus(P.el, () => {
      const srow = document.getElementById('pp-s-' + id);
      if (f && srow) srow.outerHTML = settledRow(f.kind, f.e);
      else if (f && node) node.outerHTML = f.kind === 'call' ? renderCall(f.e) : renderChange(f.e);
      const head = document.getElementById('pp-head'); if (head) head.outerHTML = renderPushHead();
      if (f && f.sec) { const sm = P.el.querySelector(`#pp-sec-${CSS.escape(f.sec.id)} > summary .cnt`); if (sm) sm.outerHTML = secCnt(f.sec); }
    });
  }
  // one write at a time, in click order; the fields shown change at once and settle when the server answers
  function pushSet(id, set) {
    if (!P.data) return Promise.resolve(false);
    const a = P.answers[id] || {}, seen = {};
    for (const f of Object.keys(set)) seen[f] = a.rev && a.rev[f] != null ? a.rev[f] : null;
    (P.ops[id] = P.ops[id] || []).push(set);
    renderPushPart(id);
    let full = false;                                     // another tab's answers came back with ours: draw everything, not one card
    const run = async () => {
      const cur = P.answers[id] || {}, revBefore = P.rev;
      for (const f of Object.keys(seen)) { const r = cur.rev && cur.rev[f]; if (r != null && P.own.has(r)) seen[f] = r; }   // this tab's own earlier write
      try {
        const j = await api('/api/push', { op: 'answer', id, package: P.data.id, set, seen });
        P.answers = j.answers || {}; P.rev = j.rev || P.rev;
        if (P.rev !== revBefore + 1) full = true;
        if (j.status) { const was = stOf(id); P.status = j.status; if (stOf(id) !== was) full = true; }
        P.wseq = (P.wseq || 0) + 1;
        const mine = P.answers[id] && P.answers[id].rev; if (mine) for (const f of Object.keys(set)) if (mine[f] != null) P.own.add(mine[f]);
        if (j.package_version && j.package_version !== P.ver) setTimeout(loadPush, 0);
        return true;
      } catch (e) {
        if (e.status === 409 && e.body) {
          if (e.body.package) { P.data = e.body.package; P.ver = e.body.package_version; }
          if (e.body.answers) { P.answers = e.body.answers; P.rev = e.body.rev || P.rev; }
          full = true;
          toast(e.body.error || 'Changed in another tab; here is the newest.', true);
        } else if (e.status === 404) { P.state = 'old'; renderPush(); }
        else toast(e.status === 0 ? 'The photo server is not answering.' : e.message, true);
        if (e.status === 0) setTimeout(loadPush, 800);        // the answer may be saved with its reply lost: show what the server has
        return false;
      } finally {
        const q = P.ops[id] || []; const k = q.indexOf(set); if (k >= 0) q.splice(k, 1); if (!q.length) delete P.ops[id];
        if (full || !findEntry(id)) renderPush(); else { renderPushPart(id); P.drawn = pushKey(); }
        badge(); if (Q.viewer && Q.viewer.push) renderViewer();
      }
    };
    const p = P.chain.then(run); P.chain = p.catch(() => {}); return p;
  }
  function pushNoteFrom(id, v) {
    v = String(v || '').trim(); delete P.drafts['pp-nt-' + id]; P.noteOpen.delete(id);
    if ((ans(id).note || '') === v) { renderPushPart(id); return; }
    pushSet(id, { note: v || null });
  }
  function pushSeen(id) { if (findEntry(id) && !ans(id).seen) pushSet(id, { seen: true }); }
  const focusLater = id => setTimeout(() => { const el = document.getElementById(id); if (el) { el.focus(); try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) {} } }, 0);
  function pushAct(el) {
    const act = el.dataset.act, id = el.dataset.id;
    if (act === 'pp-fold') { P.open = !P.open; store.set('push:open', P.open); renderPush(); return; }
    if (act === 'pp-dev') { P.dev = el.dataset.dev; store.set('push:dev', P.dev); renderPush(); return; }
    if (act === 'pp-choose') { pushSet(id, { choice: ans(id).choice === el.dataset.opt ? null : el.dataset.opt }); return; }
    if (act === 'pp-ok') { pushSet(id, ans(id).ok ? { ok: false } : { ok: true, hold: false }); return; }
    if (act === 'pp-hold') { pushSet(id, ans(id).hold ? { hold: false } : { hold: true, ok: false }); return; }
    if (act === 'pp-note') { P.noteOpen.add(id); renderPushPart(id); focusLater('pp-nt-' + id); return; }
    if (act === 'pp-note-cancel') { P.noteOpen.delete(id); delete P.drafts['pp-nt-' + id]; renderPushPart(id); return; }
    if (act === 'pp-reply-edit') { P.replyEdit.add(id); renderPushPart(id); focusLater('pp-rp-' + id); return; }
    if (act === 'pp-reply-cancel') { P.replyEdit.delete(id); delete P.drafts['pp-rp-' + id]; renderPushPart(id); return; }
    if (act === 'pp-view') { openPushViewer(id, el.dataset.dev); return; }
    if (act === 'pp-sopen') {
      if (P.settledOpen.has(id)) P.settledOpen.delete(id); else P.settledOpen.add(id);
      const f = findEntry(id), r = document.getElementById('pp-s-' + id); if (f && r) keepFocus(P.el, () => { r.outerHTML = settledRow(f.kind, f.e); });
      return;
    }
    if (act === 'pp-run-checks') {
      el.disabled = true; el.textContent = 'Running…';
      api('/api/push', { op: 'checks' }).then(j => { P.checks = j.checks || P.checks; P.status = j.status || P.status; renderPush(); if (!P.poll) P.poll = setTimeout(() => { P.poll = null; loadPush(); }, 4000); })
        .catch(e => { toast(e.status === 404 || e.status === 400 ? 'The editor needs a restart for this: re-click the Editor Suite shortcut.' : e.message, true); renderPush(); });
      return;
    }
  }
  function onPushClick(e) {
    const el = e.target.closest('[data-act]'); if (!el || !P.el.contains(el) || !el.dataset.act.startsWith('pp-')) return;
    e.preventDefault(); pushAct(el);
  }
  function onPushSubmit(e) {
    const f = e.target.closest('form[data-pform]'); if (!f) return;
    e.preventDefault();
    const id = f.dataset.id;
    if (f.dataset.pform === 'reply') {
      const ta = f.querySelector('textarea'), v = ta.value.trim(), editing = P.replyEdit.has(id);
      if (!v && !editing) return;
      delete P.drafts[ta.id]; P.replyEdit.delete(id);
      pushSet(id, { reply: v || null }).then(ok => { if (ok) toast(v ? 'Reply saved.' : 'Reply cleared.'); });
    } else if (f.dataset.pform === 'note') pushNoteFrom(id, f.querySelector('input').value);
  }
  function onPushInput(e) { const t = e.target; if (t.id && /^pp-(rp|nt)-/.test(t.id)) P.drafts[t.id] = t.value; }
  function onPushKey(e) {
    const t = e.target;
    if (t.matches('form[data-pform="reply"] textarea') && e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); e.stopPropagation(); t.form.requestSubmit(); return; }
    if (t.matches('form[data-pform="note"] input') && e.key === 'Escape') { e.stopPropagation(); const id = t.form.dataset.id; P.noteOpen.delete(id); delete P.drafts[t.id]; renderPushPart(id); }
    if (t.matches('form[data-pform="reply"] textarea') && e.key === 'Escape' && P.replyEdit.has(t.form.dataset.id)) { e.stopPropagation(); const id = t.form.dataset.id; P.replyEdit.delete(id); delete P.drafts[t.id]; renderPushPart(id); }
  }
  function onPushToggle(e) {
    const d = e.target;
    if (d.matches && d.matches('details[data-psettled]')) { if (d.open !== P.stOpen) { P.stOpen = d.open; store.set('push:settled', P.stOpen); renderPush(); } return; } if (!d.matches || !d.matches('details[data-psec]')) return;
    const id = d.dataset.psec; if (d.open === P.secOpen.has(id)) return;
    if (d.open) P.secOpen.add(id); else P.secOpen.delete(id);
    store.set('push:secs', [...P.secOpen]);
    const s = pkgSecs().find(x => x.id === id); if (s) d.outerHTML = renderSec(s);
  }
  // the viewer: every push item with screenshots, in the order the section shows them; before and after side by side
  const pushShotIds = () => pkgCalls().concat(allChanges()).filter(e => (e.shots || []).some(t => t.after)).map(e => e.id);
  function pushEntry(id) { const f = findEntry(id); return f ? Object.assign({ kind: f.kind }, f.e) : null; }
  function openPushViewer(id, dev) {
    const ids = pushShotIds();
    if (dev) { Q.dev = dev; store.set('viewer:dev', Q.dev); }
    Q.viewer = { push: true, ids: ids.length ? ids : [id], i: Math.max(0, ids.indexOf(id)) };
    renderViewer(); seenNow();
    setTimeout(() => { const x = $('#qt-viewer .qv-x'); if (x) x.focus(); }, 0);
  }
  function renderPushViewer(v) {
    const id = Q.viewer.ids[Q.viewer.i], f = findEntry(id);
    if (!f) { Q.viewer = null; v.hidden = true; document.body.style.overflow = ''; return; }
    const e = f.e, a = ans(id), dev = Q.dev, t = tierFor(e, dev), exact = t && ((t.kind === 'phone') === (dev === 'phone'));
    const fig = (s, cls, cap) => `<figure class="${cls}"><figcaption>${cap}</figcaption>${s ? `<img src="${esc(pushFile(s.src))}" alt="${esc(plain(e.title))}, ${cap.toLowerCase()}" draggable="false">` : `<div class="none">${esc((t && t.before_note) || 'Nothing before')}</div>`}</figure>`;
    const where = f.kind === 'call' ? 'Call ' + e.num : plain(f.sec.title);
    const foot = f.kind === 'call' ? optButtons(e, a)
      : `<span class="pp-acts" style="margin:0">${flagButtons(e, a)}</span><input type="text" id="qv-pnote" value="${esc(a.note || '')}" placeholder="Note for Claude" title="Saved when you leave the box" maxlength="2000" aria-label="Note for Claude">`;
    v.hidden = false; document.body.style.overflow = 'hidden';
    v.innerHTML = `<div class="qv-bar">
        <button class="qv-x" data-act="close" title="Close (Esc)" aria-label="Close">${I.x}</button>
        <span class="ttl" title="${esc(plain(e.title))}">${esc(plain(e.title))}</span>
        <span class="pos">${Q.viewer.i + 1} / ${Q.viewer.ids.length} · ${esc(where)}</span>
        <span class="qv-seg" role="group" aria-label="Device"><button data-act="dev" data-dev="desktop" class="${dev === 'desktop' ? 'on' : ''}">Desktop</button><button data-act="dev" data-dev="phone" class="${dev === 'phone' ? 'on' : ''}">Phone</button></span>
      </div>
      <div class="qv-stage">
        ${t ? `<div class="qv-pair ${t.kind === 'phone' ? 'phone' : ''}">${exact ? '' : `<div class="none" style="grid-column:1/-1">No ${esc(dev)} screenshot for this one; this is ${esc(t.label)}.</div>`}${fig(t.before, 'b', 'Before (live now)')}${fig(t.after, 'a', 'After (this push)')}</div>` : '<div class="none">No screenshot.</div>'}
        <button class="qv-nav l" data-act="prev" title="Previous (←)" aria-label="Previous" ${Q.viewer.i === 0 ? 'disabled' : ''}>${I.left}</button>
        <button class="qv-nav r" data-act="next" title="Next (→)" aria-label="Next" ${Q.viewer.i >= Q.viewer.ids.length - 1 ? 'disabled' : ''}>${I.right}</button>
      </div>
      <div class="qv-foot"><span class="sum">${pmd(e.note || '')}</span>${foot}</div>`;
  }

  // ------------------------------------------------------------------ the status strip and the held-back drawer (Kevin,
  // 2026-11-11, "let's queue 1-3,5"): what is running now and when the last hourly check ran, the system line (watchdog,
  // screen-off timer, emails, credits reset), one "Waiting on you" count whose parts each jump to their items, and the
  // drawer of everything parked from the push with a put-it-back button. tools/queue_status.py reads it all from files
  // that already exist; the toggles only create or remove flag files (.tmp/watchdog_off, .tmp/emails_off).
  const S = { data: null, state: 'idle', error: '', wOpen: false, runAll: false, busy: '', hb: { key: null, mode: null, preview: null, ok: false, busy: false } };
  const parseLocal = s => s ? new Date(String(s).replace(' ', 'T') + (String(s).length === 10 ? 'T00:00' : '')) : null;   // a bare date is local midnight, not UTC
  const clock = d => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  function span(ms) {                                     // 4 min / 1 h 12 min / 2 d 4 h
    const m = Math.max(0, Math.round(ms / 60000));
    if (m < 60) return m + ' min';
    const h = Math.floor(m / 60); if (h < 24) return h + ' h' + (m % 60 ? ' ' + (m % 60) + ' min' : '');
    const d = Math.floor(h / 24); return d + ' d' + (h % 24 ? ' ' + (h % 24) + ' h' : '');
  }
  function stamp(s) {                                     // "9:47 AM" today, "Nov 10, 9:47 AM" before
    const d = parseLocal(s); if (!d || isNaN(d)) return '';
    const day = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    if (String(s).length <= 10) return ymd(d) === today() ? 'today' : day;
    return (ymd(d) === today() ? '' : day + ', ') + clock(d);
  }
  const dur = secs => secs == null ? '?' : secs === 0 ? 'never' : secs % 3600 === 0 ? secs / 3600 + ' h' : secs % 60 === 0 ? secs / 60 + ' min' : secs + ' s';
  async function loadStatus(noDraw) {                     // never rejects; noDraw: the caller renders the whole tab next
    const w0 = S.wseq || 0;                               // a switch flipped (or a map put back) while this read was on its way wins
    try {
      const d = await api('/api/queue/status');
      if ((S.wseq || 0) === w0 && !S.busy && !S.hb.busy) { S.data = d; S.state = 'ok'; S.error = ''; }
    } catch (e) {
      if ((S.wseq || 0) === w0) { S.state = e.status === 404 ? 'old' : e.status === 0 ? (S.data ? S.state : 'down') : 'error'; S.error = e.message; }
    }
    if (!noDraw) drawStatus();
  }
  function drawStatus() {                                 // only the strip and the drawer: his typing elsewhere is left alone
    const r = root(); if (!r || !r.querySelector('.qt-wrap')) return;
    const a = r.querySelector('#qs'), b = r.querySelector('#qs-held'), f = document.activeElement;
    const fk = f && (a && a.contains(f) || b && b.contains(f)) ? [f.dataset.act, f.dataset.part || f.dataset.op || f.dataset.key || ''] : null;
    const tmp = document.createElement('div');
    tmp.innerHTML = renderStatus(); const ns = tmp.firstElementChild;
    if (a) { if (ns) a.replaceWith(ns); else a.remove(); }
    else if (ns) { const slot = r.querySelector('#qt-push') || r.querySelector('#qt-push-slot'); if (slot) slot.before(ns); }
    tmp.innerHTML = renderHeld(); const nh = tmp.firstElementChild;
    if (b) { if (nh) b.replaceWith(nh); else b.remove(); }
    else if (nh) { const cols = r.querySelector('.qt-cols'); if (cols) cols.before(nh); }
    if (fk) { const el = r.querySelector(`[data-act="${fk[0]}"]${fk[1] ? `[data-part="${CSS.escape(fk[1])}"],[data-act="${fk[0]}"][data-op="${CSS.escape(fk[1])}"],[data-act="${fk[0]}"][data-key="${CSS.escape(fk[1])}"]` : ''}`); if (el) el.focus(); }
  }
  function renderStatus() {
    if (S.state === 'old') return `<section class="qs" id="qs" aria-label="Status"><div class="qt-card qs-wait"><span class="qs-msg">The status strip needs the new photo server code: re-click the Editor Suite shortcut.</span></div></section>`;
    if (!S.data) return '';
    const d = S.data, now = parseLocal(d.now) || new Date();
    return `<section class="qs" id="qs" aria-label="Status"><div class="qs-row">${renderWaiting(d.waiting || { total: 0, parts: [] })}${renderRunning(d, now)}</div>${renderSystem(d.system || {}, now)}</section>`;
  }
  const PART_TIP = {
    rounds: 'Review rounds with changes left (Review, by country)', push: 'Push package items that need your answer',
    writer: 'Questions the writer left in the margin of your drafts, plus open questions in My to-do', shadow: 'Articles with Shadow reviewer comments you have not answered (one per article)'
  };
  function renderWaiting(w) {
    const parts = (w.parts || []).map(p => {
      const extra = p.id === 'rounds' && p.changes ? ` <span class="x">${p.changes} changes</span>` : p.id === 'shadow' && p.threads ? ` <span class="x">${p.threads} comments</span>` : '';
      return `<button type="button" class="qs-part ${p.n ? '' : 'zero'}" data-act="qs-jump" data-part="${esc(p.id)}" ${p.id === 'writer' ? `aria-expanded="${S.wOpen}"` : ''} ${p.n ? '' : 'aria-disabled="true"'} title="${esc(PART_TIP[p.id] || '')}"><b>${p.n}</b> ${esc(p.label)}${extra}</button>`;
    }).join('');
    const wp = (w.parts || []).find(p => p.id === 'writer');
    const list = S.wOpen && wp && wp.n ? `<div class="qs-wlist">${(wp.articles || []).map(a => `<a href="${esc(a.rel ? siteUrl(a.rel) : '#')}" data-act="open-rel" data-rel="${esc(a.rel || '')}" title="Open ${esc(a.rel || a.stem)} in the editor"><span>${esc(a.title)}</span><span>${a.open} question${a.open === 1 ? '' : 's'}</span></a>`).join('')}${(wp.ids || []).map(id => { const it = items().find(x => x.id === id); return it ? `<a href="#" data-act="jump" data-id="${esc(id)}" data-cat="${esc(it.cat)}"><span>${esc(it.title)}</span><span>My to-do</span></a>` : ''; }).join('')}</div>` : '';
    return `<div class="qt-card qs-wait" id="qs-wait"><div class="qs-h">Waiting on you</div>
      <div class="qs-wtop"><span class="qs-big" data-total>${w.total}</span><span class="qs-wl">${w.total === 1 ? 'thing needs' : 'things need'} you${w.error ? ' (part of it could not be read)' : ''}</span></div>
      <div class="qs-parts">${parts}</div>${list}</div>`;
  }
  function renderRunning(d, now) {
    const jobs = d.running || [], lt = d.last_tick, at = lt && parseLocal(lt.at);
    const tick = at && !isNaN(at) ? `Last hourly check ${esc(stamp(lt.at))}, ${esc(span(now - at))} ago${lt.owner === 'headless' ? (lt.alive ? ' (a headless run is working)' : ' (headless run)') : ''}` : 'No hourly check recorded';
    const shown = S.runAll ? jobs : jobs.slice(0, 5);
    const rows = shown.map(j => { const st = parseLocal(j.started);
      return `<button type="button" class="qs-job" data-act="qs-job" data-id="${j.id}" title="${esc(j.group || '')}"><span class="sp" aria-hidden="true"></span><span class="t">${esc(j.title)}</span>${st && !isNaN(st)
        ? `<span class="w" title="started ${esc(j.started)}">started ${esc(stamp(j.started))}${String(j.started).length > 10 ? ' · ' + esc(span(now - st)) : ''}</span>` : '<span class="w none">start not recorded</span>'}</button>`; }).join('');
    const more = jobs.length > 5 ? `<button type="button" class="qt-more" data-act="qs-run-all">${S.runAll ? 'Show fewer' : 'Show all ' + jobs.length}</button>` : '';
    return `<div class="qt-card qs-run" id="qs-run"><div class="qs-h">Running now <span class="n">${jobs.length}</span><span class="qs-tick" data-tick>${tick}</span></div>
      ${jobs.length ? `<div class="qs-jobs">${rows}</div>${more}` : '<div class="qs-empty">Nothing marked running in the queue.</div>'}</div>`;
  }
  function renderSystem(s, now) {
    const w = s.watchdog || {}, e = s.emails || {}, sc = s.screen, c = s.credits || {};
    const wsub = w.on ? (w.running === true ? 'running' : w.running === false ? 'starts at the next hourly check' : '')
      : (w.running === true ? (w.stop_pending ? 'stopping' : 'still running') : '');
    const wtip = (w.on ? 'On: the hourly check keeps one watchdog running, so the PC rests between jobs. Click to turn it off (a running one stops within a minute).'
      : 'Off: no watchdog is started. Click to turn it on; the next hourly check starts one.') + (w.note && !w.on ? '\n' + w.note : '') + (w.last_log ? '\nLast log: ' + w.last_log : '');
    const nr = parseLocal(c.next), until = nr && !isNaN(nr) ? span(nr - now) : '';
    const crLabel = nr && !isNaN(nr) ? nr.toLocaleDateString(undefined, { weekday: 'short' }) + ' ' + clock(nr).replace(':00', '') : 'Fri 2 PM';
    return `<div class="qs-sys" role="group" aria-label="System">
      <button type="button" class="qs-tg ${w.on ? 'on' : ''}" data-act="qs-toggle" data-op="watchdog" aria-pressed="${!!w.on}" title="${esc(wtip)}" ${S.busy ? 'disabled' : ''}><span class="sw" aria-hidden="true"></span>Watchdog <b>${w.on ? 'On' : 'Off'}</b>${wsub ? `<span class="sub">· ${esc(wsub)}</span>` : ''}</button>
      <span class="qs-info" title="${esc(sc ? 'The active power plan, read with powercfg: ' + dur(sc.ac) + ' plugged in, ' + dur(sc.dc) + ' on battery. A running watchdog sets 2 min and puts yours back when it stops. Change it in Windows Settings.' : 'Not read on this server')}">Screen off after <b>${sc ? esc(dur(sc.ac)) : 'not read'}</b></span>
      <button type="button" class="qs-tg ${e.on ? 'on' : ''}" data-act="qs-toggle" data-op="emails" aria-pressed="${!!e.on}" title="${esc(e.on ? 'On: Claude emails you short updates as queue items finish. Click to stop them.' : 'Off: Claude sends no update emails. Click to turn them back on.')}" ${S.busy ? 'disabled' : ''}><span class="sw" aria-hidden="true"></span>Emails <b>${e.on ? 'On' : 'Off'}</b></button>
      <span class="qs-info" title="Your weekly credits reset Fridays at 2 PM">Credits reset <b>${esc(crLabel)}</b>${until ? `<span class="sub">· in ${esc(until)}</span>` : ''}</span>
    </div>`;
  }
  const H_STATE = { held: ['st', 'held back'], back: ['doneon', 'back on the page'], changed: ['st', 'page changed'], missing: ['plain', 'page missing'] };
  function renderHeld() {
    const list = (S.data && S.data.held) || []; if (!list.length) return '';
    const n = list.filter(h => h.state === 'held').length, open = !!Q.open['sec:held'];
    const rows = list.map(h => {
      const st = H_STATE[h.state] || ['plain', h.state], isPage = /^[\w.\/ -]+\.html$/.test(h.page);
      const chips = `<div class="qt-chips">${isPage && h.state !== 'missing' ? `<a class="qt-chip link" href="${esc(siteUrl(h.page))}" data-act="open-rel" data-rel="${esc(h.page)}" title="Open ${esc(h.page)} in the editor">${I.doc}${esc(h.page.replace(/\.html$/, ''))}</a>` : `<span class="qt-chip plain">${esc(h.page)}</span>`}<span class="qt-chip ${st[0]}">${esc(st[1])}</span>${h.restored ? `<span class="qt-chip plain" title="${esc(h.restored.at)}">put back ${esc(stamp(h.restored.at.replace('T', ' ').slice(0, 16)))}</span>` : ''}</div>`;
      const btn = h.action === 'restore' ? `<button type="button" class="qt-btn sm" data-act="qs-hb-ask" data-key="${esc(h.key)}" ${S.hb.busy ? 'disabled' : ''}>Put it back</button>`
        : h.action === 'copy' || h.state === 'changed' ? `<button type="button" class="qt-btn sm" data-act="qs-hb-steps" data-key="${esc(h.key)}">Put it back</button>` : '';
      return `<div class="qs-hrow" data-key="${esc(h.key)}"><div><div class="qs-ht">${esc(h.title)}</div>${chips}${h.reason ? `<div class="qs-why">${esc(h.reason)}</div>` : ''}</div>
        <div class="qs-hact">${btn}</div>${S.hb.key === h.key ? renderHbPanel(h) : ''}</div>`;
    }).join('');
    return `<details class="qt-card qt-fold qs-held" id="qs-held" data-fold="held" ${open ? 'open' : ''}><summary>Held back <span class="n">${n}</span><span class="qs-hsub">parked from the push</span></summary>
      ${open ? `<p class="qs-hintro">Cut from the working copy so they stay out of the push. Putting a map back runs its saved restore, which drops the block back in exactly where it was cut. Its push item then reopens, because your answer was to keep it out.</p>${rows}` : ''}</details>`;
  }
  function renderHbPanel(h) {
    const hb = S.hb;
    if (hb.mode === 'steps') {
      const why = h.why_copy || 'The page changed around the spot since the map was cut, so the restore script would stop and print where the block goes. Run it yourself to see that, or ask Claude.';
      return `<div class="qs-confirm"><p>${esc(why)} These are the steps to run or hand to Claude:</p><code id="qs-cmd-${esc(h.key)}">${esc(h.command)}</code>
        <div class="acts"><button type="button" class="qt-btn sm primary" data-act="qs-copy" data-key="${esc(h.key)}">Copy</button><button type="button" class="qt-btn sm" data-act="qs-hb-cancel">Close</button></div></div>`;
    }
    const answer = h.push_id ? ' Your push answer was to keep it out, so its push item reopens and waits for a new answer.' : '';
    const pv = hb.preview == null ? 'Checking where it goes…' : hb.preview;
    return `<div class="qs-confirm warn" role="group" aria-label="Confirm"><p><b>Put the ${esc(h.title)} map back on ${esc(h.page)}?</b>${esc(answer)} Nothing is pushed.</p>
      <p style="color:var(--qt-mute)">${esc(pv)}</p>
      <div class="acts"><button type="button" class="qt-btn sm primary" data-act="qs-hb-ok" data-key="${esc(h.key)}" ${hb.ok && !hb.busy ? '' : 'disabled'}>${hb.busy ? 'Putting it back…' : 'Yes, put it back'}</button>
        <button type="button" class="qt-btn sm" data-act="qs-hb-cancel" ${hb.busy ? 'disabled' : ''}>Cancel</button></div></div>`;
  }
  function jumpTodos(ids) {                               // open every category and country holding them, then scroll to the first
    const its = ids.map(id => items().find(x => x.id === id)).filter(Boolean); if (!its.length) return false;
    for (const it of its) { if (Q.catHidden.has(it.cat)) { Q.catHidden.delete(it.cat); store.set('cats:hidden', [...Q.catHidden]); } setCatOpen(it.cat, true); if (isGrouped(it.cat)) setCgOpen(cgId(it.cat, gkey(it)), true); }
    render();
    const r = root(), rows = its.map(it => r.querySelector(`.qt-todo[data-id="${CSS.escape(it.id)}"]`)).filter(Boolean);
    if (rows[0]) rows[0].scrollIntoView({ block: 'center' });
    rows.forEach(row => { row.style.outline = '2px solid #2D6B50'; row.style.outlineOffset = '-2px'; setTimeout(() => { row.style.outline = ''; row.style.outlineOffset = ''; }, 2200); });
    return true;
  }
  function jumpPush(ids) {
    if (!P.data) return;
    P.open = true; store.set('push:open', true);
    for (const id of ids) { const f = findEntry(id); if (f && f.sec) P.secOpen.add(f.sec.id); }
    store.set('push:secs', [...P.secOpen]); renderPush();
    const el = ids.map(id => document.getElementById('pp-e-' + id)).find(Boolean) || P.el;
    if (el) { el.scrollIntoView({ block: el === P.el ? 'start' : 'center' }); if (el !== P.el) { el.style.outline = '2px solid #2D6B50'; setTimeout(() => { el.style.outline = ''; }, 2200); } }
  }
  async function copyText(t) {
    try { await navigator.clipboard.writeText(t); return true; } catch (e) {}
    try { const ta = document.createElement('textarea'); ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select(); const ok = document.execCommand('copy'); ta.remove(); return ok; } catch (e) { return false; }
  }
  async function statusAct(el, e) {
    const act = el.dataset.act;
    if (act === 'qs-jump') {
      const p = ((S.data && S.data.waiting && S.data.waiting.parts) || []).find(x => x.id === el.dataset.part); if (!p || !p.n) return;
      if (p.id === 'writer') { S.wOpen = !S.wOpen; drawStatus(); return; }
      if (p.id === 'push') { jumpPush(p.ids || []); return; }
      if (!jumpTodos(p.ids || [])) toast('Those items are not in the list yet; press Reload.', true);
      return;
    }
    if (act === 'qs-job') {
      const row = root().querySelector(`.qt-item[data-id="${CSS.escape(el.dataset.id)}"]`);
      if (row) { row.scrollIntoView({ block: 'center' }); row.style.outline = '2px solid #2B5D8A'; setTimeout(() => { row.style.outline = ''; }, 2200); }
      return;
    }
    if (act === 'qs-run-all') { S.runAll = !S.runAll; drawStatus(); return; }
    if (act === 'qs-toggle') {
      const op = el.dataset.op, cur = ((S.data && S.data.system) || {})[op] || {}, on = !cur.on;
      S.busy = op; drawStatus();
      try {
        S.data = await api('/api/queue/status', { op, on }); S.state = 'ok'; S.wseq = (S.wseq || 0) + 1;
        toast(op === 'watchdog' ? (on ? 'Watchdog on: the next hourly check starts it.' : 'Watchdog off: a running one stops within a minute, and no new one starts.')
          : (on ? 'Emails on: Claude emails you short updates as items finish.' : 'Emails off: no update emails until you turn them back on.'));
      } catch (err) { toast(err.status === 404 ? 'The photo server needs a restart for this: re-click the Editor Suite shortcut.' : err.status === 0 ? 'The photo server is not answering.' : err.message, true); }
      S.busy = ''; drawStatus(); return;
    }
    if (act === 'qs-hb-steps') { S.hb = { key: el.dataset.key, mode: 'steps', preview: null, ok: false, busy: false }; Q.open['sec:held'] = true; drawStatus(); return; }
    if (act === 'qs-hb-ask') {
      const key = el.dataset.key;
      S.hb = { key, mode: 'confirm', preview: null, ok: false, busy: false }; drawStatus();
      try { const j = await api('/api/queue/held', { op: 'preview', key }); if (S.hb.key === key) { S.hb.preview = j.text ? j.text.replace(/^would restore/, 'It would restore') + '.' : 'Ready.'; S.hb.ok = !!j.ok; } }
      catch (err) { if (S.hb.key === key) { S.hb.preview = (err.body && err.body.error) || err.message; S.hb.ok = false; } }
      drawStatus(); return;
    }
    if (act === 'qs-hb-cancel') { S.hb = { key: null, mode: null, preview: null, ok: false, busy: false }; drawStatus(); return; }
    if (act === 'qs-hb-ok') {
      const key = el.dataset.key, h = ((S.data && S.data.held) || []).find(x => x.key === key);
      S.hb.busy = true; drawStatus();
      try {
        S.data = await api('/api/queue/held', { op: 'restore', key, confirm: true }); S.wseq = (S.wseq || 0) + 1;
        S.hb = { key: null, mode: null, preview: null, ok: false, busy: false };
        toast('Back on ' + (h ? h.page : 'its page') + '. Its push item reopened for a new answer.');
        loadPush();
      } catch (err) {
        S.hb.busy = false; S.hb.ok = false; S.hb.preview = (err.body && err.body.error) || err.message;
        toast(err.status === 404 ? 'The photo server needs a restart for this: re-click the Editor Suite shortcut.' : 'Nothing was changed: ' + ((err.body && err.body.error) || err.message), true);
      }
      drawStatus(); return;
    }
    if (act === 'qs-copy') {
      const h = ((S.data && S.data.held) || []).find(x => x.key === el.dataset.key); if (!h) return;
      toast(await copyText(h.command) ? 'Copied.' : 'Select the text and copy it.', false); return;
    }
  }

  // ------------------------------------------------------------------ mount
  function mount(el) {
    if (el) Q.root = el;
    const r = root(); if (!r || r.dataset.qtMounted) { if (r) load(); return; }
    r.dataset.qtMounted = '1';
    r.addEventListener('click', onClick); r.addEventListener('submit', onSubmit); r.addEventListener('keydown', onKey); r.addEventListener('change', onChange);
    r.addEventListener('toggle', onToggleFold, true);
    r.addEventListener('pointerdown', onPointerDown); r.addEventListener('pointermove', onPointerMove); r.addEventListener('pointerup', onPointerUp); r.addEventListener('pointercancel', onPointerUp);
    load();
  }
  // shown: load (or refresh when the last read is older than 20 s); Claude edits the md while you are on another tab
  function show() {
    const r = root(); if (!r) return;
    if (!r.dataset.qtMounted) return mount();
    if (Date.now() - Q.loadedAt > 20000) load(true); else render();
  }
  function tick() {                                        // a quiet refresh every minute while the tab shows
    const a = $('#app-queue'); const shown = Q.root ? true : a && !a.hidden;
    if (shown && !document.hidden && Q.state === 'ok' && !Q.editing && !Q.busy && !Q.drag && !Q.viewer && Date.now() - Q.loadedAt > 55000) load(true);
    // one lost read left "Server not answering" up until Reload was pressed (stress test 2026-11-11): try again by itself
    else if (shown && !document.hidden && Q.state === 'down' && Q.queue && !Q.editing && !Q.busy && !Q.drag && Date.now() - (Q.triedAt || 0) > 9000) load(true);
    else { const w = $('[data-when]'); if (w && Q.loadedAt && Q.state === 'ok') w.textContent = 'Read ' + when(); }
  }
  setInterval(tick, 10000);
  window.__queueShow = show;
  window.__queueMount = mount;
  window.__queue = Q;                                      // test seam
  window.__queueReload = () => load(true);
  window.__queueOpenViewer = openViewer;
  P.load = () => loadPush();
  window.__push = P;                                       // test seam (the Push package)
  // the badge wants the count even before the tab is opened once
  if (!Q.root) api('/api/todo').then(t => { Q.todo = t; badge(); }).catch(() => {});
  if (!Q.root) api('/api/push').then(j => { if (P.state === 'idle') { P.data = j.package || null; P.ver = j.package_version || null; P.answers = j.answers || {}; P.rev = j.rev || 0; P.state = 'ok';
    P.status = j.status || {}; P.checks = j.checks || null; P.ready = j.ready || null; badge();
    if (P.el && P.el.dataset.drawn && pushKey() !== P.drawn) renderPush(); } }).catch(() => {});
  // the editor can open straight onto this tab while this deferred script is still loading
  { const a = $('#app-queue'); if (a && !a.hidden) mount(); }
})();
