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
  @media (max-width:980px){.qt-cols{grid-template-columns:1fr}}
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
  .qt-toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:var(--qt-ink);color:#fff;font-size:.8rem;padding:.7rem 1rem;border-radius:10px;
    z-index:200;max-width:min(560px,90vw);box-shadow:0 8px 30px rgba(0,0,0,.18);white-space:pre-line;line-height:1.45}
  .qt-toast.bad{background:var(--qt-red)}
  .app-tabs a .bdg{font:700 9.5px/1 'Hanken Grotesk',sans-serif;background:#6EC99A;color:#1C2821;border-radius:999px;padding:3px 5px;margin-left:-2px;min-width:8px;text-align:center;font-variant-numeric:tabular-nums}
  .app-tabs a .bdg[hidden]{display:none}
  /* the viewer: the item's in-article screenshot big, Desktop/Phone, previous/next through the category (the Review Hub's) */
  #qt-viewer{position:fixed;inset:0;z-index:120;background:rgba(20,26,22,.96);color:#fff;display:grid;grid-template-rows:auto 1fr auto;font-family:'Hanken Grotesk',sans-serif}
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
  `;
  document.head.appendChild(css);

  // ------------------------------------------------------------------ api
  async function api(path, body) {
    let r;
    try {
      r = await fetch(API + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { cache: 'no-store' });
    } catch (e) { const err = new Error('down'); err.status = 0; throw err; }
    let j = null;
    try { j = await r.json(); } catch (e) {}
    if (!r.ok) { const err = new Error((j && j.error) || ('HTTP ' + r.status)); err.status = r.status; err.body = j; throw err; }
    return j;
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
    const n = items().filter(i => isOpen(i) && !Q.catHidden.has(i.cat)).length;   // the categories he hid do not nag from the tab
    document.querySelectorAll('.app-tabs a[data-view="queue"]').forEach(a => {
      let b = a.querySelector('.bdg'); if (!b) { b = document.createElement('span'); b.className = 'bdg'; a.appendChild(b); }
      b.textContent = n; b.hidden = !n; b.title = n + (n === 1 ? ' open to-do' : ' open to-dos');
    });
    window.__queueOpenCount = n;
  }
  window.__queueBadge = badge;

  async function load(quiet) {
    if (!quiet) { Q.state = Q.queue ? Q.state : 'loading'; render(); }
    try {
      const [q, t] = await Promise.all([api('/api/queue'), api('/api/todo')]);
      Q.queue = q; Q.todo = t; Q.state = 'ok'; Q.error = ''; Q.loadedAt = Date.now();
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
      return true;
    } catch (e) {
      if (e.status === 409 && e.body) { Q.queue = Object.assign({}, e.body, { ok: true }); toast(e.body.error || 'The queue changed underneath; reloaded.', true); }
      else if (e.status === 404) Q.state = 'old';
      else toast(e.status === 0 ? 'The photo server is not answering.' : e.message, true);
      return false;
    } finally { Q.busy = false; render(); }
  }
  async function todoOp(body, retry) {
    if (!Q.todo) return false;
    try {
      Q.todo = await api('/api/todo', Object.assign({ version: Q.todo.version }, body));
      badge(); return true;
    } catch (e) {
      if (e.status === 409 && e.body && !retry) { Q.todo = Object.assign({}, e.body, { ok: true }); return todoOp(body, true); }   // another tab wrote first: redo on its list
      if (e.status === 409 && e.body) { Q.todo = Object.assign({}, e.body, { ok: true }); toast('The to-do list changed in another tab; reloaded.', true); }
      else if (e.status === 404) Q.state = 'old';
      else toast(e.status === 0 ? 'The photo server is not answering.' : e.message, true);
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
  const doneQueue = () => ((Q.queue && Q.queue.queue) || []).filter(i => i.state === 'done').sort((a, b) => (b.done_on || '').localeCompare(a.done_on || '') || b.num - a.num);

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
    const cc = [c.pending ? c.pending + ' open' : '', c.running ? c.running + ' running' : '', c.hold ? c.hold + ' held' : ''].filter(Boolean).join(' · ') || 'nothing open';
    r.innerHTML = `<div class="qt-wrap">
      <div class="qt-top">
        <div><span class="qt-eyebrow">Editor suite</span><h1>Queue</h1></div>
        <span class="qt-spacer"></span>
        <span class="qt-when" data-when>${Q.state === 'down' ? 'Server not answering · last read' : 'Read ' + when()}</span>
        <button class="qt-btn" data-act="reload" ${Q.busy ? 'disabled' : ''}>Reload</button>
      </div>
      <div class="qt-cols">
        <section class="qt-col" data-col="claude">
          <h2>Claude <span>${esc(cc)}</span></h2>
          <div class="qt-card" data-list="queue">
            ${work.length ? work.map((it, i) => renderItem(it, i, work.length)).join('') : '<div class="qt-empty">Nothing queued.</div>'}
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
  function renderItem(it, i, n, isDone) {
    const open = !!Q.open[it.num], has = !!(it.summary || (it.details && it.details.length));
    const stLabel = { done: isDone ? '' : 'done', running: 'running', hold: 'on hold', pending: '' }[it.state] || '';
    return `<div class="qt-row qt-item st-${it.state}" data-num="${it.num}" ${isDone ? '' : `data-idx="${i}"`}>
      ${isDone ? '<span></span>' : `<span class="qt-grip" title="Drag to reorder" aria-label="Drag to reorder">${GRIP}</span>`}
      <span class="qt-num">${it.num}</span>
      <div class="qt-main">
        <div class="qt-trow ${has ? '' : 'nohide'}" data-act="${has ? 'toggle' : ''}" ${has ? `role="button" tabindex="0" aria-expanded="${open}"` : ''}>
          <span class="qt-t">${md(it.title)}</span>${stLabel ? `<span class="qt-st ${it.state}">${esc(stLabel)}</span>` : ''}${isDone && it.done_on ? `<span class="qt-chip doneon" title="done ${esc(it.done_on)}">${esc(shortDate(it.done_on))}</span>` : ''}
        </div>
        ${it.summary && !open ? `<div class="qt-s clamp" data-act="toggle" title="Show more">${md(it.summary)}</div>` : ''}
        ${open ? `<div class="qt-d">${it.summary ? `<div>${md(it.summary)}</div>` : ''}${it.details.length ? `<ul>${it.details.map(d => `<li>${md(d)}</li>`).join('')}</ul>` : ''}
          <div class="meta">#${it.num}${it.added_by ? ' · added by Kevin ' + esc(it.added_by) : ''}</div></div>` : ''}
      </div>
      <div class="qt-acts">
        ${isDone ? '' : `<button class="qt-ib qt-hold" data-act="hold" data-hold="${it.state === 'hold' ? 0 : 1}" ${Q.busy ? 'disabled' : ''} title="${it.state === 'hold' ? 'Resume: Claude may start it again' : 'Hold: Claude skips it until you resume it'}" aria-label="${it.state === 'hold' ? 'Resume' : 'Hold'}">${it.state === 'hold' ? I.play : I.pause}</button>
        <button class="qt-ib" data-act="move" data-dir="-1" title="Move up" aria-label="Move up" ${i === 0 || Q.busy ? 'disabled' : ''}>${I.up}</button>
        <button class="qt-ib" data-act="move" data-dir="1" title="Move down" aria-label="Move down" ${i === n - 1 || Q.busy ? 'disabled' : ''}>${I.down}</button>`}
      </div></div>`;
  }
  function renderQueueAdd() {
    return `<form class="qt-add" data-form="queue-add" autocomplete="off">
      <input type="text" id="qt-qa-title" class="grow" placeholder="Add for Claude…" maxlength="300" required ${Q.busy ? 'disabled' : ''} title="Goes to the end of the queue, numbered">
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
    return `<div class="qt-cgs" data-list="cgroups" data-cat="${esc(cat.id)}">${groups.map((g, i) => {
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
    const roundChip = it.round && it.round.changes != null ? `<span class="qt-chip plain" title="changes in the round">${esc(it.round.left != null && it.round.left !== it.round.changes ? it.round.left + ' of ' + it.round.changes : it.round.changes)} change${it.round.changes === 1 ? '' : 's'}</span>` : '';
    const shadowChip = it.shadow_open ? `<span class="qt-chip plain">${it.shadow_open} sr- thread${it.shadow_open === 1 ? '' : 's'}</span>` : '';
    const stChip = it.status === 'needs-changes' ? '<span class="qt-chip st">needs changes</span>' : '';
    const whereChip = isDone ? `<span class="qt-chip plain" title="${esc(catName(it.cat))}">${it.group && it.group.flag ? `<img src="${esc(siteUrl(it.group.flag))}" alt="">` : ''}${esc(catName(it.cat).replace(/^Review: /, ''))}</span>${doneStamp(it) ? `<span class="qt-chip doneon" title="done ${esc(doneStamp(it))}">${esc(shortDate(doneStamp(it)))}</span>` : ''}` : '';
    const reply = it.kind === 'question' ? (it.reply
      ? `<div class="qt-note"><b>Answer</b>${esc(it.reply)} <button class="qt-more" data-act="reply-edit" style="padding:0 4px">edit</button></div>`
      : `<form class="qt-reply" data-form="todo-reply"><textarea id="qt-rp-${esc(it.id)}" placeholder="Answer for Claude…" maxlength="2000" rows="1"></textarea><button type="submit" class="qt-btn sm primary">Send</button></form>`) : '';
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
    const it = viewerItem(); if (it && it.status === 'new') todoOp({ op: 'update', id: it.id, status: 'seen' }).then(() => renderViewer());
  }
  const viewerItem = () => Q.viewer ? items().find(x => x.id === Q.viewer.ids[Q.viewer.i]) : null;
  function step(d) { if (!Q.viewer) return; const j = Q.viewer.i + d; if (j < 0 || j >= Q.viewer.ids.length) return; Q.viewer.i = j; renderViewer(); seenNow(); }
  function renderViewer() {
    const v = viewerEl();
    if (!Q.viewer) { v.hidden = true; document.body.style.overflow = ''; return; }
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
        <span class="sum">${md(it.summary || '')}${it.round && it.round.changes != null ? ` <span style="opacity:.7">· ${esc(it.round.changes)} changes</span>` : ''}</span>
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
    else if ((e.key === 'd' || e.key === 'p') && !e.target.matches('input,textarea')) { Q.dev = e.key === 'd' ? 'desktop' : 'phone'; store.set('viewer:dev', Q.dev); renderViewer(); }
  }
  function swipeStart(e) { if (!e.target.closest('.qv-stage') || e.target.closest('button')) return; Q.swipe = { x: e.clientX, y: e.clientY, t: Date.now() }; }
  function swipeEnd(e) {
    const s = Q.swipe; Q.swipe = null; if (!s) return;
    const dx = e.clientX - s.x, dy = e.clientY - s.y;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5 && Date.now() - s.t < 800) step(dx < 0 ? 1 : -1);
  }
  document.addEventListener('focusout', e => {      // the viewer's note saves itself when the box is left
    if (e.target && e.target.id === 'qv-note' && Q.viewer) { const it = viewerItem(); if (it && (it.note || '') !== e.target.value.trim()) todoOp({ op: 'update', id: it.id, note: e.target.value.trim() }); }
  });
  function openRel(rel) {
    if (window.__queueOpenRel) return window.__queueOpenRel(rel);
    if (window.__apOpen && window.showView) { showView('editor'); window.__apOpen(rel); }
    else window.open(siteUrl(rel), '_blank');
  }

  // ------------------------------------------------------------------ actions
  // Claude's queue: the working list (not done) is what moves; done items keep their lines and numbers in the md,
  // so a new working order is laid back over the working slots of the full list
  function queueOrderFrom(workNums) { const set = new Set(workNums); let k = 0; return Q.queue.queue.map(i => set.has(i.num) ? workNums[k++] : i.num); }
  const workNums = () => working().map(i => i.num);
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
    if (act === 'toggle') { const num = +el.closest('.qt-item').dataset.num; Q.open[num] = !Q.open[num]; render(); return; }
    if (act === 'pre-toggle') { const k = 'pre:' + el.dataset.k; Q.open[k] = !Q.open[k]; render(); return; }
    if (act === 'xo') { const id = el.closest('.qt-todo').dataset.id; if (Q.xo.has(id)) Q.xo.delete(id); else Q.xo.add(id); render(); return; }
    if (act === 'qa-details') { Q.addDetails = true; render(); setTimeout(() => { const d = $('#qt-qa-details'); if (d) d.focus(); }, 0); return; }
    if (act === 'hold') { const num = +el.closest('.qt-item').dataset.num; queueOp({ op: 'hold', num, hold: el.dataset.hold === '1' }).then(ok => { if (ok) toast(el.dataset.hold === '1' ? 'On hold.' : 'Resumed.'); }); return; }
    if (act === 'move') { const row = el.closest('.qt-item'), i = +row.dataset.idx, j = i + (+el.dataset.dir); const o = workNums(); if (j < 0 || j >= o.length) return; queueOp({ op: 'reorder', order: queueOrderFrom(moveIn(o, i, j)) }); return; }
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
    if (act === 'reply-edit') { const id = el.closest('.qt-todo').dataset.id; todoOp({ op: 'update', id, reply: '', status: 'seen' }).then(() => render()); return; }
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
      const ok = await queueOp({ op: 'add', title, details });
      if (ok) { Q.addDetails = false; toast('Added as #' + Q.queue.queue[Q.queue.queue.length - 1].num + '.'); render(); setTimeout(() => { const t = $('#qt-qa-title'); if (t) t.focus(); }, 0); }
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
      if (await todoOp({ op: 'update', id, reply, status: 'done' })) { toast('Answer saved.'); render(); }
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
    if (k.startsWith('done:')) Q.doneOpen[k.slice(5)] = d.open; else Q.open['sec:' + k] = d.open;
  }

  // ------------------------------------------------------------------ drag to reorder: pointer events, so mouse and touch
  // behave the same (HTML5 drag-and-drop has no touch on phones). The grip starts it; the arrows are the keyboard way.
  const ROW = { queue: '.qt-item', todo: '.qt-todo', cats: '.qt-cat', cgroups: '.qt-cg' };
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
    if (!row || row === d.row || !d.list.contains(row) || (d.kind === 'todo' && row.closest('.qt-fold')) || (d.kind === 'todo' && row.closest('[data-list="todo"]') !== d.list)) { d.over = null; return; }
    const rc = row.getBoundingClientRect(); d.over = row; d.after = e.clientY > rc.top + rc.height / 2;
    row.classList.add(d.after ? 'drop-after' : 'drop-before');
    const sc = root(); if (sc) { const b = sc.getBoundingClientRect(); if (e.clientY < b.top + 40) sc.scrollTop -= 12; else if (e.clientY > b.bottom - 40) sc.scrollTop += 12; }
  }
  function onPointerUp(e) {
    const d = Q.drag; if (!d || e.pointerId !== d.id) return;
    Q.drag = null; clearDrop(); d.row.classList.remove('dragging'); document.body.classList.remove('qt-dragging');
    if (!d.live || !d.over) return;
    if (d.kind === 'queue') { const o = workNums(); const n = place(o, o.indexOf(+d.row.dataset.num), o.indexOf(+d.over.dataset.num), d.after); if (n) queueOp({ op: 'reorder', order: queueOrderFrom(n) }); }
    else if (d.kind === 'cats') { const vis = visibleCats().map(c => c.id); const n = place(vis, vis.indexOf(d.row.dataset.cat), vis.indexOf(d.over.dataset.cat), d.after); if (n) catsReorder(n); }
    else if (d.kind === 'cgroups') { const ks = [...d.list.querySelectorAll('.qt-cg')].map(g => g.dataset.cg); const n = place(ks, ks.indexOf(d.row.dataset.cg), ks.indexOf(d.over.dataset.cg), d.after); if (n) reorderGroups(d.list.closest('.qt-cat'), n); }
    else { const sub = rowsOf(d.list); const n = place(sub, sub.indexOf(d.row.dataset.id), sub.indexOf(d.over.dataset.id), d.after); if (n) reorderList(d.list, n); }
  }
  function clearDrop() { document.querySelectorAll('.drop-before,.drop-after').forEach(x => x.classList.remove('drop-before', 'drop-after')); }

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
    else { const w = $('[data-when]'); if (w && Q.loadedAt && Q.state === 'ok') w.textContent = 'Read ' + when(); }
  }
  setInterval(tick, 10000);
  window.__queueShow = show;
  window.__queueMount = mount;
  window.__queue = Q;                                      // test seam
  window.__queueReload = () => load(true);
  window.__queueOpenViewer = openViewer;
  // the badge wants the count even before the tab is opened once
  if (!Q.root) api('/api/todo').then(t => { Q.todo = t; badge(); }).catch(() => {});
  // the editor can open straight onto this tab while this deferred script is still loading
  { const a = $('#app-queue'); if (a && !a.hidden) mount(); }
})();
