/* The Social tab (Kevin, 2026-11-08: "add integrating the new social tool to the editor as a 'social' tab between
   launch and queue"; then "make it blend more seamlessly with the editor", "2 types of outputs: personal post, Travel
   Blog post", "is there a version for IG stories too?").

   One country at a time, two OUTPUT TYPES chosen at the top:
     Personal post    his own Instagram: any 10 photos, shortlisted, cropped and captioned (the maker's free mode),
                      rendered by tools/ig_post.py; its stories by tools/ig_story.py --kind free
     Travel Blog post the branded itinerary carousel (cover, route map, a slide per stop, call to action, the caption
                      builder), rendered by tools/ig_carousel.py; its stories by tools/ig_story.py --kind carousel; pins too
   Each type has its own state on the photo server (tools/social_tab.py: .tmp/ig/<slug>/state), renders and caption.

   The maker itself (tools/ig_post_page.html, the same page the claude.ai artifacts run) sits in an iframe served with
   ?embed=1: it drops its own header and takes the editor's look, and this tab's toolbar drives it over postMessage
   (output type = the page's mode, the views, the article filter, Ready); the page reports its counts back after every
   render, so the tab's sub-tabs carry live numbers. The renders are child processes on the server; the Renders / Slides /
   Stories / Pins views list the files, with a lightbox.

   Self-contained like queue.js: plain fetch against window.SOCIAL_API (default the photo server), mounted by
   window.__socialMount(element) / shown by window.__socialShow(). */
(function () {
  'use strict';
  const API = window.SOCIAL_API || 'http://127.0.0.1:5003';
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = x => String(x == null ? '' : x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const store = {
    get(k, d) { try { const v = localStorage.getItem('so:' + k); return v == null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('so:' + k, v); } catch (e) {} }
  };
  // the two output types: the page mode each one is, and the views its sub-tab bar offers (maker views open the iframe)
  const TYPES = {
    // Kevin, 2026-11-11: "remove the Shortlist: the slots are the shortlist", so the slots view is called Shortlist
    personal: { label: 'Personal post', mode: 'free', hint: 'Your own Instagram: up to 20 photos, cropped and captioned',
                views: [['all', 'Photos'], ['final', 'Shortlist'], ['archived', 'Archived'], ['caption', 'Caption'], ['renders', 'Post'], ['stories', 'Stories']],
                maker: ['all', 'final', 'archived', 'caption'], home: 'all' },
    blog: { label: 'Travel Blog post', mode: 'template', hint: 'The branded itinerary carousel: cover, route map, a slide per stop, call to action',
            views: [['layout', 'Layout'], ['slides', 'Slides'], ['stories', 'Stories'], ['pins', 'Pins']], maker: ['layout'], home: 'layout' }
  };
  const typeOf = t => TYPES[t] ? t : 'personal';
  const S = { root: null, state: 'loading', error: '', countries: [], slug: store.get('country', ''), type: typeOf(store.get('type', 'personal')),
              views: { personal: store.get('view:personal', 'all'), blog: store.get('view:blog', 'layout') }, article: { personal: '', blog: '' },
              renders: null, rendersFor: null, job: null, light: null, loadedAt: 0, busy: false, pg: null, pgFor: null, synced: false };
  if (S.views.personal === 'shortlist') S.views.personal = 'final';           // the old Shortlist view (2026-11-11)
  for (const t of Object.keys(TYPES)) if (!TYPES[t].views.some(v => v[0] === S.views[t])) S.views[t] = TYPES[t].home;
  // the feed ratios Instagram takes: Meta's publishing docs allow 4:5 to 1.91:1, and since May 2025 the app takes 3:4 too
  const RATIOS = [['4:5', 'Portrait, 1080 × 1350'], ['3:4', 'Tall portrait, 1080 × 1440: the profile grid’s own shape'], ['1:1', 'Square, 1080 × 1080'], ['1.91:1', 'Landscape, 1080 × 565']];
  const arCss = a => ({ '4:5': '4/5', '3:4': '3/4', '1:1': '1/1', '1.91:1': '1.91/1' }[a] || '4/5');

  // ------------------------------------------------------------------ look (the Launch and Queue tabs' language, one type scale)
  const css = document.createElement('style');
  css.textContent = `
  #app-social{--so-bg:#F7F7F3;--so-card:#fff;--so-ink:#1C2821;--so-mute:#6b7670;--so-faint:#98a29c;--so-line:#E7E6DF;--so-soft:#FBFBF8;
    --so-green:#2D6B50;--so-green-bg:#E8F1EC;--so-amber:#9A6F12;--so-amber-bg:#F7EFD9;--so-red:#B0442F;--so-red-bg:#F8E7E2;
    position:fixed;inset:0;z-index:80;display:grid;grid-template-rows:56px 1fr;grid-template-columns:minmax(0,1fr);background:var(--so-bg);
    font-family:'Hanken Grotesk',sans-serif;color:var(--so-ink);font-size:13px;line-height:1.45}
  #app-social[hidden]{display:none!important}
  #app-social>header{height:56px;padding:0 20px;background:#1C2821;color:#fff;display:flex;align-items:center;gap:18px;box-sizing:border-box;overflow:hidden}
  #app-social>header .suite-mark{margin:0 6px 0 0}
  #social-root{min-height:0;display:grid;grid-template-rows:minmax(0,1fr)}
  .so-wrap{max-width:1320px;width:100%;margin:0 auto;padding:22px 28px 0;box-sizing:border-box;display:flex;flex-direction:column;min-height:0}
  .so-top{display:flex;align-items:flex-end;gap:18px;flex-wrap:wrap}
  .so-eyebrow{display:block;font-size:.6rem;font-weight:600;letter-spacing:.16em;text-transform:uppercase;color:var(--so-mute);margin-bottom:4px}
  .so-pick{display:flex;align-items:center;gap:10px}
  .so-pick img{width:26px;height:19px;border-radius:2px;object-fit:cover;box-shadow:0 0 0 1px rgba(28,40,33,.12)}
  .so-country{font-family:'Newsreader',Georgia,serif;font-size:1.9rem;font-weight:400;color:var(--so-ink);background:transparent;border:0;padding:0 26px 0 0;margin:0;cursor:pointer;
    appearance:none;-webkit-appearance:none;line-height:1.15;max-width:min(70vw,520px);text-overflow:ellipsis;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%231C2821' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 2px center}
  .so-country:focus-visible{outline:2px solid var(--so-green);outline-offset:4px;border-radius:4px}
  .so-meta{font-size:12px;color:var(--so-mute);margin:0 0 7px;font-variant-numeric:tabular-nums}
  .so-spacer{flex:1}
  /* the output type: a two-way switch, the Launch tab's soft-green "on" */
  .so-seg{display:inline-flex;gap:2px;background:#fff;border:1px solid var(--so-line);border-radius:999px;padding:3px;margin-bottom:1px}
  .so-seg button{font:500 12.5px/1 'Hanken Grotesk',sans-serif;color:#4f5c54;border:0;background:none;border-radius:999px;padding:8px 14px;cursor:pointer;white-space:nowrap;transition:background .15s,color .15s}
  .so-seg button:hover{color:var(--so-ink);background:#F4F6F4} .so-seg button.on{background:var(--so-green-bg);color:var(--so-green);font-weight:600}
  .so-seg button:focus-visible{outline:2px solid var(--so-green);outline-offset:2px}
  .so-btn{font:600 .62rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;border-radius:999px;padding:.62rem 1rem;border:1px solid var(--so-line);
    background:var(--so-card);color:var(--so-ink);white-space:nowrap;min-height:34px;display:inline-flex;align-items:center;gap:6px;text-decoration:none;box-sizing:border-box}
  .so-btn:hover{border-color:var(--so-green);color:var(--so-green)} .so-btn:focus-visible{outline:2px solid var(--so-green);outline-offset:2px}
  .so-btn.primary{background:var(--so-green);border-color:var(--so-green);color:#fff} .so-btn.primary:hover{background:#24573F;color:#fff}
  .so-btn[disabled]{opacity:.55;cursor:default;pointer-events:none} .so-btn.sm{padding:.45rem .7rem;font-size:.56rem;min-height:28px}
  .so-btn svg{width:12px;height:12px;fill:none;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
  .so-ib{width:34px;height:34px;border-radius:50%;border:1px solid var(--so-line);background:var(--so-card);color:var(--so-mute);cursor:pointer;display:grid;place-items:center;padding:0;text-decoration:none;box-sizing:border-box}
  .so-ib:hover{color:var(--so-green);border-color:var(--so-green)} .so-ib:focus-visible{outline:2px solid var(--so-green);outline-offset:2px}
  .so-ib svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
  .so-ib.sm{width:28px;height:28px;border-radius:6px;border-color:transparent;color:var(--so-faint);font:600 12px/1 'Hanken Grotesk',sans-serif}
  .so-ib.sm:hover{color:var(--so-ink);background:#F3F3EE;border-color:var(--so-line)} .so-ib.sm svg{width:13px;height:13px}
  .so-pill{font:600 .56rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.12em;text-transform:uppercase;padding:.36rem .6rem;border-radius:999px;background:#EFEFE9;color:var(--so-mute);white-space:nowrap}
  .so-pill.live{background:var(--so-green-bg);color:var(--so-green)}
  .so-sel{height:28px;padding:0 26px 0 9px;border:1px solid rgba(28,40,33,.16);border-radius:6px;background:#fff;color:var(--so-ink);font:400 12.5px/1 'Hanken Grotesk',sans-serif;max-width:220px;text-overflow:ellipsis;
    appearance:none;-webkit-appearance:none;cursor:pointer;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%234f5c54' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 8px center}
  .so-sel:hover{border-color:rgba(28,40,33,.32)} .so-sel:focus-visible{outline:2px solid var(--so-green);outline-offset:1px}
  .so-sel.has{color:var(--so-green);border-color:var(--so-green)}
  /* the sub-tabs: the Launch tab's bar */
  .so-tabs{display:flex;align-items:center;gap:2px;margin:16px 0 0;padding:6px 8px 0;background:#fff;border:1px solid var(--so-line);border-bottom:0;border-radius:12px 12px 0 0;flex-wrap:wrap}
  .so-tab{display:inline-flex;align-items:center;gap:6px;flex:none;font:500 12.5px/1 'Hanken Grotesk',sans-serif;color:#4f5c54;cursor:pointer;background:none;border:0;border-bottom:2px solid transparent;
    border-radius:8px 8px 0 0;padding:9px 12px 10px;white-space:nowrap;transition:background .15s,color .15s}
  .so-tab:hover{background:#F4F6F4;color:var(--so-ink)} .so-tab.on{background:#EEF3EF;color:var(--so-green);border-bottom-color:var(--so-green)}
  .so-tab:focus-visible{outline:2px solid var(--so-green);outline-offset:-2px}
  .so-tab .n{font:600 9.5px/1 'Hanken Grotesk',sans-serif;border-radius:999px;padding:2px 5px;background:#EFEFE9;color:var(--so-mute);font-variant-numeric:tabular-nums}
  .so-tab.on .n{background:var(--so-green-bg);color:var(--so-green)} .so-tab .n.full{background:var(--so-green-bg);color:var(--so-green)}
  .so-tab .sep{width:1px;height:18px;background:var(--so-line);margin:0 4px 6px;flex:none;align-self:center}
  .so-tabs .acts{margin-left:auto;display:flex;gap:6px;align-items:center;padding:0 2px 6px;flex-wrap:wrap}
  .so-job{font-size:12px;color:var(--so-mute);display:inline-flex;align-items:center;gap:7px;max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .so-job.run::before{content:'';width:10px;height:10px;border-radius:50%;border:2px solid var(--so-line);border-top-color:var(--so-green);animation:so-spin .8s linear infinite;flex:none}
  .so-job.bad{color:var(--so-red)} .so-job.ok{color:var(--so-green)}
  @keyframes so-spin{to{transform:rotate(360deg)}} @media (prefers-reduced-motion:reduce){.so-job.run::before{animation:none}}
  .so-body{flex:1;min-height:0;background:#fff;border:1px solid var(--so-line);border-top:0;border-radius:0 0 12px 12px;overflow:hidden;position:relative;margin-bottom:22px;display:flex;flex-direction:column}
  .so-body.scroll{overflow:auto;display:block}
  .so-panel{padding:18px 18px 24px} .so-panel[hidden]{display:none}
  .so-frame{flex:1;width:100%;height:100%;border:0;display:block;background:#fff} .so-frame[hidden]{display:none}
  /* the maker fills the window while its preview or crop editor is open (the page asks with {ig:1, event:'full'}) */
  .so-frame.full{position:fixed;inset:0;width:100vw;height:100vh;z-index:150;background:#000}
  .so-seg.sm{padding:2px;margin:0}.so-seg.sm button{padding:6px 9px;font-size:12px;font-variant-numeric:tabular-nums}
  .so-group{font:600 11px/1 'Hanken Grotesk',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--so-faint);margin:0 0 10px;display:flex;align-items:center;gap:8px}
  .so-group .n{background:#EFEFE9;border-radius:999px;padding:2px 6px;letter-spacing:0;color:var(--so-mute)}
  .so-group .hint{display:none}   /* Kevin, 2026-11-11: "hide the descriptions from social" (the sizes live in the tooltips) */
  .so-group+.so-group,.so-cap+.so-group,.so-grid+.so-group,.so-strip+.so-group{margin-top:26px}
  .so-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:0 0 14px}
  .so-grid.pins{grid-template-columns:repeat(auto-fill,minmax(170px,1fr))}
  .so-thumb{position:relative;border-radius:8px;overflow:hidden;background:#EFEFE9;aspect-ratio:4/5;cursor:zoom-in;border:1px solid var(--so-line);padding:0;display:block;width:100%}
  .so-grid.pins .so-thumb{aspect-ratio:2/3} .so-grid.f .so-thumb{aspect-ratio:var(--so-ar,4/5)}
  .so-thumb img{width:100%;height:100%;object-fit:cover;display:block}
  .so-thumb .k{position:absolute;left:6px;bottom:6px;font:600 10px/1 'Hanken Grotesk',sans-serif;background:rgba(255,255,255,.92);color:var(--so-ink);border-radius:999px;padding:4px 7px;letter-spacing:.04em;max-width:calc(100% - 12px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;box-sizing:border-box}
  .so-thumb:focus-visible{outline:2px solid var(--so-green);outline-offset:2px}
  /* the Stories strip: 9:16 frames in one row that scrolls sideways */
  .so-strip{display:flex;gap:12px;overflow-x:auto;padding:2px 2px 12px;margin:0 0 6px;scrollbar-width:thin;scrollbar-color:var(--so-line) transparent;scroll-snap-type:x proximity}
  .so-strip .so-thumb{flex:0 0 auto;width:180px;aspect-ratio:9/16;scroll-snap-align:start;border-radius:12px}
  .so-strip .so-thumb .k{left:8px;bottom:8px}
  .so-cap{position:relative;margin-bottom:26px;background:var(--so-soft);border:1px solid var(--so-line);border-radius:10px;padding:12px 44px 12px 14px;font-size:13px;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--so-ink);max-width:720px}
  .so-cap .so-ib{position:absolute;right:8px;top:8px}
  .so-empty{padding:26px 16px;text-align:center;color:var(--so-mute);font-size:13px;line-height:1.55;display:flex;flex-direction:column;align-items:center;gap:12px}
  .so-msg{padding:60px 20px;text-align:center;color:var(--so-mute);font-size:.9rem;line-height:1.6;max-width:560px;margin:0 auto}
  .so-msg b{color:var(--so-ink)} .so-msg code{font:.78rem/1.5 ui-monospace,Menlo,Consolas,monospace;background:#EFEFE9;border-radius:4px;padding:.1rem .35rem}
  .so-log{font:11.5px/1.5 ui-monospace,Menlo,Consolas,monospace;color:var(--so-mute);background:var(--so-soft);border:1px solid var(--so-line);border-radius:8px;padding:10px 12px;max-height:200px;overflow:auto;white-space:pre-wrap;margin:0 0 14px}
  .so-toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:#1C2821;color:#fff;font:400 .8rem/1.45 'Hanken Grotesk',sans-serif;padding:.7rem 1rem;border-radius:10px;z-index:200;max-width:min(560px,90vw);box-shadow:0 8px 30px rgba(0,0,0,.18)}
  .so-toast.bad{background:#B0442F}
  .so-toast[hidden]{display:none}   /* the toast and the lightbox hang off <body>, outside #app-social, so no --so- variables reach them */
  /* the lightbox */
  #so-light{position:fixed;inset:0;z-index:120;background:rgba(20,26,22,.96);display:grid;grid-template-rows:auto 1fr;font-family:'Hanken Grotesk',sans-serif;color:#fff}
  #so-light[hidden]{display:none}
  #so-light .bar{display:flex;align-items:center;gap:12px;padding:10px 16px;min-height:52px;box-sizing:border-box}
  #so-light .ttl{font-weight:600;font-size:.9rem;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #so-light .pos{font-size:.72rem;color:rgba(255,255,255,.6);font-variant-numeric:tabular-nums}
  #so-light .x{width:40px;height:40px;border-radius:50%;border:1px solid rgba(255,255,255,.25);background:rgba(255,255,255,.06);color:#fff;cursor:pointer;display:grid;place-items:center;padding:0;flex:none}
  #so-light .x:hover{background:rgba(255,255,255,.16)}
  #so-light svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
  #so-light .stage{position:relative;min-height:0;display:flex;align-items:center;justify-content:center;padding:0 84px 20px}
  #so-light .stage img{max-width:100%;max-height:100%;display:block;box-shadow:0 10px 40px rgba(0,0,0,.4);background:#fff}
  #so-light .stage img.story{border-radius:24px}
  /* previous / next: the photo library's big side arrows (editor.html #light .nav), against the screen edges */
  #so-light .nav{position:fixed;top:50%;transform:translateY(-50%);width:52px;height:82px;display:flex;align-items:center;justify-content:center;cursor:pointer;
    background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.22);color:#fff;font:400 1.5rem/1 'Hanken Grotesk',sans-serif;border-radius:6px;padding:0;user-select:none}
  #so-light .nav:hover{background:rgba(255,255,255,.22)} #so-light .nav[disabled]{opacity:.2;cursor:default} #so-light .nav.l{left:16px} #so-light .nav.r{right:16px}
  /* the photo library picker (Add photos): hangs off <body> like the lightbox, so no --so- variables */
  #so-lib{position:fixed;inset:0;z-index:120;background:rgba(20,26,22,.55);display:flex;align-items:center;justify-content:center;padding:24px;font-family:'Hanken Grotesk',sans-serif;color:#1C2821;font-size:13px}
  #so-lib[hidden]{display:none}
  #so-lib .box{background:#fff;border-radius:12px;width:min(1180px,100%);height:min(860px,100%);display:grid;grid-template-rows:auto 1fr auto;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.25)}
  #so-lib .hd{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid #E7E6DF;flex-wrap:wrap}
  #so-lib h2{font:400 1.35rem/1.1 'Newsreader',Georgia,serif;margin:0 6px 0 0}
  #so-lib select{height:30px;padding:0 26px 0 9px;border:1px solid rgba(28,40,33,.16);border-radius:6px;background:#fff;color:#1C2821;font:400 12.5px/1 'Hanken Grotesk',sans-serif;max-width:min(320px,60vw)}
  #so-lib .chk{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;color:#4f5c54;cursor:pointer}
  #so-lib .sp{flex:1}
  #so-lib .grid{overflow:auto;padding:14px 16px;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;align-content:start}
  #so-lib .t{position:relative;border:0;padding:0;border-radius:8px;overflow:hidden;background:#EFEFE9;aspect-ratio:1/1;cursor:pointer;outline:2px solid transparent;outline-offset:-2px}
  #so-lib .t img{width:100%;height:100%;object-fit:cover;display:block}
  #so-lib .t.on{outline-color:#2D6B50;box-shadow:0 0 0 3px #E8F1EC}
  #so-lib .t.on::after{content:'';position:absolute;top:6px;right:6px;width:20px;height:20px;border-radius:50%;background:#2D6B50 url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m5 12 5 5 9-10'/%3E%3C/svg%3E") center/13px no-repeat}
  #so-lib .t.in{opacity:.35;cursor:default}
  #so-lib .t.pk::before{content:'';position:absolute;left:7px;top:7px;width:8px;height:8px;border-radius:50%;background:#E0A93B;box-shadow:0 0 0 2px #fff}
  #so-lib .ft{display:flex;align-items:center;gap:10px;padding:12px 16px;border-top:1px solid #E7E6DF}
  #so-lib .msg{color:#6b7670;font-size:12.5px;padding:30px 0;grid-column:1/-1;text-align:center}
  #so-lib .btn{font:600 .62rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;border-radius:999px;padding:.62rem 1rem;border:1px solid #E7E6DF;background:#fff;color:#1C2821;min-height:34px}
  #so-lib .btn.primary{background:#2D6B50;border-color:#2D6B50;color:#fff} #so-lib .btn[disabled]{opacity:.55;cursor:default}
  @media (max-width:700px){#so-lib{padding:0}#so-lib .box{border-radius:0;height:100%}#so-lib .grid{grid-template-columns:repeat(3,minmax(0,1fr));padding:10px}}
  @media (max-width:700px){.so-wrap{padding:12px 12px 0}.so-country{font-size:1.5rem}.so-meta{display:none}.so-top{gap:10px 12px}.so-seg{order:5;width:100%;justify-content:stretch}.so-seg button{flex:1;text-align:center}
    .so-seg.sm{order:0;width:auto}.so-seg.sm button{flex:none;padding:6px 8px}
    .so-btn.art span,.so-tabs .acts .so-btn span{display:none}.so-btn.art{width:34px;height:34px;padding:0;justify-content:center;border-radius:50%}.so-tabs .acts{margin-left:0;width:100%;justify-content:flex-start}.so-body{margin-bottom:12px}
    .so-grid{grid-template-columns:repeat(2,1fr)}.so-grid.pins{grid-template-columns:repeat(2,1fr)}.so-strip .so-thumb{width:140px}#so-light .stage{padding:0 40px 8px}
    #so-light .nav{width:34px;height:60px}#so-light .nav.l{left:3px}#so-light .nav.r{right:3px}}
  `;
  document.head.appendChild(css);

  const I = {
    x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>', ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4 10 14M18 14v6H4V6h6"/></svg>',
    left: '<svg viewBox="0 0 24 24"><path d="m15 6-6 6 6 6"/></svg>', right: '<svg viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
    down: '<svg viewBox="0 0 24 24"><path d="M12 3v13M6 10l6 6 6-6M4 21h16"/></svg>', refresh: '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/></svg>',
    plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
    eye: '<svg viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>'
  };
  const SHORTCUTS = 'Keyboard shortcuts\nArrows  move around the grid\nShift+arrows  move the photo\n1–0  into slide 1–10\nS  into the Shortlist (again: out)\nDelete  out of its slot\nA  archive\nEnter  edit (crop and tilt)\nP or Space  preview\nEsc  close';

  // ------------------------------------------------------------------ api
  async function api(path, body) {
    let r;
    try { r = await fetch(API + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { cache: 'no-store' }); }
    catch (e) { const err = new Error('down'); err.status = 0; throw err; }
    let j = null; try { j = await r.json(); } catch (e) {}
    if (!r.ok) { const err = new Error((j && j.error) || ('HTTP ' + r.status)); err.status = r.status; err.body = j; throw err; }
    return j;
  }
  function toast(msg, bad) {
    let t = $('.so-toast'); if (!t) { t = document.createElement('div'); t.className = 'so-toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
    t.textContent = msg; t.hidden = false; t.classList.toggle('bad', !!bad); clearTimeout(t._h); t._h = setTimeout(() => { t.hidden = true; }, bad ? 7000 : 3500);
  }
  const cur = () => S.countries.find(c => c.slug === S.slug) || null;
  const siteUrl = rel => API + '/site/' + rel.replace(/\\/g, '/').replace(/^\.?\//, '').split('/').map(encodeURIComponent).join('/');
  const view = () => S.views[S.type];
  const isMaker = () => TYPES[S.type].maker.includes(view());

  async function load(quiet) {
    if (!quiet) { S.state = S.countries.length ? S.state : 'loading'; render(); }
    try {
      const j = await api('/api/social');
      S.countries = j.countries || []; S.state = 'ok'; S.error = ''; S.loadedAt = Date.now();
      if (!cur()) S.slug = S.countries.length ? S.countries[0].slug : '';
    } catch (e) {
      if (e.status === 404) S.state = 'old'; else if (e.status === 0) S.state = 'down'; else { S.state = S.countries.length ? 'ok' : 'error'; S.error = e.message; }
    }
    render();
    if (S.state === 'ok' && S.slug) loadRenders();
  }
  async function loadRenders() {
    const slug = S.slug; if (!slug) return;
    try { const r = await api('/api/social/' + encodeURIComponent(slug) + '/renders'); if (slug === S.slug) { S.renders = r; S.rendersFor = slug; const c = cur(); if (c) c.running = !!r.running; render(); } }   // a render an agent started shows up, and its end frees the buttons
    catch (e) { if (slug === S.slug) { S.renders = null; S.rendersFor = slug; } }
  }
  async function startRender(kind) {
    if (S.busy || !S.slug) return;
    S.busy = true; render();
    try {
      const j = await api('/api/social/' + encodeURIComponent(S.slug) + '/render', { kind });
      S.job = j.job; pollJob();
    } catch (e) { toast(e.status === 0 ? 'The photo server is not answering.' : e.message, true); }
    finally { S.busy = false; render(); }
  }
  function pollJob() {
    clearTimeout(S.jobT);
    if (!S.job || !['queued', 'running'].includes(S.job.status)) return;
    S.jobT = setTimeout(async () => {
      try { const j = await api('/api/social/job/' + S.job.id); S.job = j.job; } catch (e) { S.job = null; render(); return; }
      if (['queued', 'running'].includes(S.job.status)) { render(); pollJob(); }
      else { render(); toast(S.job.status === 'done' ? 'Rendered.' : 'The render failed; the log is below.', S.job.status !== 'done'); loadRenders(); load(true); }
    }, 1500);
  }
  async function seed() {
    if (S.busy) return; S.busy = true; render();
    try { await api('/api/social/' + encodeURIComponent(S.slug) + '/seed', {}); toast('Plan seeded from tools/ig_carousel/' + S.slug + '.json.'); remakeFrame(); }
    catch (e) { toast(e.message, true); }
    finally { S.busy = false; load(true); }
  }

  // ------------------------------------------------------------------ the maker iframe and its protocol
  const frameEl = () => { const r = root(); return r ? r.querySelector('#so-frame') : null; };
  function remakeFrame() { const f = frameEl(); if (f) f.remove(); S.pg = null; S.synced = false; render(); }
  // what the tab wants the page to show: its mode (the output type), the view (Personal post only) and the article filter
  function sync() {
    const f = frameEl(); if (!f || !f.contentWindow) return;
    const msg = { so: 1, mode: TYPES[S.type].mode, article: S.article[S.type] || '' };
    if (S.type === 'personal' && isMaker()) msg.view = view();
    try { f.contentWindow.postMessage(msg, '*'); } catch (e) {}
  }
  function onMessage(e) {
    const f = frameEl(); if (!f || e.source !== f.contentWindow || !e.data || e.data.ig !== 1) return;
    const m = e.data; const c = cur(); if (!c) return;
    if (m.event === 'full') { f.classList.toggle('full', !!m.on); if (m.on) { try { f.focus(); } catch (x) {} } return; }   // the preview or the crop editor takes the window
    S.pg = m; S.pgFor = c.slug;
    if (m.mode !== TYPES[S.type].mode) { sync(); return; }           // the page woke up in the other mode (or the handshake was lost): tell it again
    // the page moved itself (Ready jumps to Caption): the tab's sub-tab follows, but only once the tab has had its say
    if (S.synced && S.type === 'personal' && isMaker() && m.view !== view() && TYPES.personal.maker.includes(m.view)) { S.views.personal = m.view; store.set('view:personal', m.view); }
    render();
  }
  window.addEventListener('message', onMessage);
  function frameLoaded(f) { S.synced = true; sync(); }
  function send(msg) { const f = frameEl(); if (f && f.contentWindow) f.contentWindow.postMessage(Object.assign({ so: 1 }, msg), '*'); }
  // Render (Kevin, 2026-11-11: "Ready to render becomes Render"): the post is marked ready and rendered in one go, and
  // the tab moves to what it renders into (Post for the Personal post, Slides for the Travel Blog post)
  function renderNow() {
    if (S.busy) return;
    send({ cmd: 'markready' });
    const blog = S.type === 'blog';
    setTimeout(() => { startRender(blog ? 'carousel' : 'free'); setView(blog ? 'slides' : 'renders'); }, 400);   // the ready write lands first
  }
  function openPreview() { const f = frameEl(); send({ cmd: 'preview' }); if (f) try { f.focus(); } catch (e) {} }

  // ------------------------------------------------------------------ render
  const root = () => S.root || $('#social-root');
  const jobOf = slug => S.job && S.job.slug === slug ? S.job : null;
  const n = (v, cls) => v ? `<span class="n ${cls || ''}">${v}</span>` : '';
  function render() {
    const r = root(); if (!r) return;
    if (S.state === 'loading' && !S.countries.length) { r.innerHTML = '<div class="so-msg">Loading…</div>'; return; }
    if (S.state === 'old') {
      r.innerHTML = `<div class="so-msg"><b>The photo server needs a restart</b> for this tab: <code>/api/social</code> is not there yet.<br>
        Opening the editor from the taskbar icon restarts it as soon as no photo job is running.<br><button class="so-btn" style="margin-top:16px" data-act="reload">Try again</button></div>`;
      clearTimeout(S.retry); S.retry = setTimeout(() => { const a = $('#app-social'); if (!a || !a.hidden) load(true); }, 20000); return;
    }
    if (S.state === 'down' && !S.countries.length) {
      r.innerHTML = `<div class="so-msg"><b>The photo server isn’t answering.</b><br>It restarts by itself; this tab tries again in a moment.<br><button class="so-btn" style="margin-top:16px" data-act="reload">Try again</button></div>`;
      clearTimeout(S.retry); S.retry = setTimeout(() => { const a = $('#app-social'); if ((!a || !a.hidden) && !S.countries.length) load(true); }, 6000); return;
    }
    if (S.state === 'error' && !S.countries.length) { r.innerHTML = `<div class="so-msg"><b>Could not read the Social store.</b><br>${esc(S.error)}<br><button class="so-btn" style="margin-top:16px" data-act="reload">Try again</button></div>`; return; }
    const c = cur();
    if (!c) {
      r.innerHTML = `<div class="so-msg"><b>No country has photos collected yet.</b><br>Claude runs <code>python tools/ig_post.py "&lt;Country&gt;"</code> (add <code>--drafts</code> for drafts) and the country appears here.</div>`; return;
    }
    const rd = S.rendersFor === c.slug ? S.renders : null, job = jobOf(c.slug), T = TYPES[S.type], v = view();
    const pg = S.pgFor === c.slug && S.pg && S.pg.mode === T.mode ? S.pg : null, k = (pg && pg.counts) || {};
    const cnt = { carousel: rd ? rd.carousel.length : c.renders.carousel, free: rd ? rd.free.length : c.renders.free, pins: rd ? rd.pins.length : c.renders.pins,
                  sc: rd ? rd.stories_carousel.length : (c.renders.stories_carousel || 0), sf: rd ? rd.stories_free.length : (c.renders.stories_free || 0) };
    const nfin = k.nfinal || 10;
    const badge = id => id === 'all' ? n(k.all) : id === 'final' ? (pg ? `<span class="n ${k.final === nfin ? 'full' : ''}">${k.final}/${nfin}${k.shortlist ? ' +' + k.shortlist : ''}</span>` : '')
      : id === 'archived' ? n(k.archived) : id === 'layout' ? (pg && pg.hasPlan ? `<span class="n ${k.placed === k.boxes ? 'full' : ''}">${k.slides}</span>` : '') : id === 'renders' ? n(cnt.free) : id === 'slides' ? n(cnt.carousel)
      : id === 'stories' ? n(S.type === 'blog' ? cnt.sc : cnt.sf) : id === 'pins' ? n(cnt.pins) : '';
    const tabs = T.views.map(([id, label], i) => (i && T.maker.includes(T.views[i - 1][0]) && !T.maker.includes(id) ? '<span class="sep" aria-hidden="true"></span>' : '')
      + `<button type="button" class="so-tab ${v === id ? 'on' : ''}" data-act="view" data-view="${id}" role="tab" aria-selected="${v === id}">${label}${badge(id)}</button>`).join('');
    const jobLine = job ? `<span class="so-job ${job.status === 'done' ? 'ok' : job.status === 'failed' ? 'bad' : 'run'}" title="${esc((job.log || '').split('\n').filter(Boolean).slice(-1)[0] || '')}">${esc(job.kind.replace('-', ' '))} · ${esc(job.status)}${job.status === 'running' ? ' · ' + esc(((job.log || '').trim().split('\n').pop() || '').slice(0, 60)) : ''}</span>` : '';
    const running = !!(job && ['queued', 'running'].includes(job.status)) || c.running, off = running || S.busy;
    const hasPlan = pg ? pg.hasPlan : c.has_plan;
    const rbtn = (kind, label, ok, why, primary) => `<button class="so-btn sm ${primary ? 'primary' : ''}" data-act="render" data-kind="${kind}" ${off || !ok ? 'disabled' : ''} title="${esc(ok ? why : why)}">${label}</button>`;
    let acts = '';
    if (isMaker()) {
      const filter = ['all', 'archived', 'layout'].includes(v) && pg && pg.articles && pg.articles.length > 1
        ? `<select class="so-sel ${S.article[S.type] ? 'has' : ''}" data-act="article" aria-label="Filter by article"><option value="">Every article</option>${pg.articles.map(a => `<option value="${esc(a.k)}" ${a.k === S.article[S.type] ? 'selected' : ''}>${esc(a.v)}</option>`).join('')}</select>` : '';
      const personal = S.type === 'personal';
      const help = personal ? `<button class="so-ib sm" type="button" data-act="help" title="${esc(SHORTCUTS)}" aria-label="Keyboard shortcuts">?</button>` : '';
      // the post's ratio, one for every slide (Instagram shows a carousel at one ratio); the crop editor has it too
      const ratio = personal && pg && v !== 'caption' ? `<div class="so-seg sm" role="group" aria-label="Aspect ratio">${RATIOS.map(([r, tip]) => `<button type="button" class="${pg.aspect === r ? 'on' : ''}" data-act="aspect" data-aspect="${r}" aria-pressed="${pg.aspect === r}" title="${esc(tip)}">${r}</button>`).join('')}</div>` : '';
      const add = personal && v === 'all' ? `<button class="so-btn sm" data-act="library" title="Add photos to this country from the photo library">${I.plus}<span>Add photos</span></button>` : '';
      const prev = personal && pg && v !== 'caption' ? `<button class="so-btn sm" data-act="preview" title="${v === 'final' ? 'The slides, big, on black (P)' : 'These photos, big, on black (P)'}">${I.eye}<span>Preview</span></button>` : '';
      const seedBtn = S.type === 'blog' && c.has_seed && !hasPlan ? `<button class="so-btn sm" data-act="seed" ${S.busy ? 'disabled' : ''} title="Build the itinerary plan from tools/ig_carousel/${esc(c.slug)}.json">Seed plan</button>` : '';
      const canRender = pg && (personal ? k.final > 0 : hasPlan);
      const renderBtn = pg && (personal || hasPlan) ? `<button class="so-btn sm primary" data-act="rendernow" ${off || !canRender ? 'disabled' : ''} title="${personal ? (k.final ? 'Render the Final row at 1080 px from the originals' : 'Put a photo in the Final row first') : 'Render the carousel at 1080 × 1350'}">Render</button>` : '';
      acts = `${jobLine}${filter}${ratio}${add}${prev}${help}${seedBtn}${renderBtn}`;
    } else if (v === 'renders') acts = `${jobLine}${rbtn('free', 'Render the post', true, 'Render the Final row from the originals at 1080 px (tools/ig_post.py)', true)}`;
    else if (v === 'slides') acts = `${jobLine}${rbtn('carousel', 'Render the carousel', hasPlan, hasPlan ? 'Render the slides at 1080 × 1350 from the originals (tools/ig_carousel.py)' : 'No plan yet: seed one or lay it out in Layout', true)}`;
    else if (v === 'stories') acts = S.type === 'blog'
      ? `${jobLine}${rbtn('stories-carousel', 'Render stories', hasPlan, hasPlan ? 'Story frames at 1080 × 1920: cover, route map, one per stop, call to action (tools/ig_story.py)' : 'No plan yet: seed one or lay it out in Layout', true)}`
      : `${jobLine}${rbtn('stories-free', 'Render stories', true, 'One 1080 × 1920 frame per slide of the Final row, its note as the line (tools/ig_story.py)', true)}`;
    else if (v === 'pins') acts = `${jobLine}${rbtn('pins', 'Render pins', c.has_seed, c.has_seed ? 'Render the Pinterest pins listed in tools/ig_carousel/' + esc(c.slug) + '.json' : 'No seed file with a pins list', true)}`;
    // the chrome re-draws; the Maker's iframe stays put (re-made only for another country), or its page would reload
    let wrap = r.querySelector('.so-wrap');
    if (!wrap) { r.innerHTML = '<div class="so-wrap"><div class="so-top"></div><div class="so-tabs" role="tablist"></div><div class="so-body"></div></div>'; wrap = r.querySelector('.so-wrap'); }
    wrap.querySelector('.so-top').innerHTML = `<div><span class="so-eyebrow">Editor suite · Social</span>
          <div class="so-pick">${c.flag ? `<img src="${esc(siteUrl(c.flag))}" alt="" width="26" height="19">` : ''}<select class="so-country" aria-label="Country" data-act="country">${S.countries.map(x => `<option value="${esc(x.slug)}" ${x.slug === c.slug ? 'selected' : ''}>${esc(x.country)}</option>`).join('')}</select></div></div>
        <div class="so-meta">${c.photos} photos · ${c.pages} article${c.pages === 1 ? '' : 's'}${c.drafts ? ' (drafts)' : ''}</div>
        <div class="so-seg" role="group" aria-label="Output type">${Object.keys(TYPES).map(t => `<button type="button" class="${S.type === t ? 'on' : ''}" data-act="type" data-type="${t}" aria-pressed="${S.type === t}" title="${esc(TYPES[t].hint)}">${TYPES[t].label}</button>`).join('')}</div>
        <span class="so-spacer"></span>
        ${c.artifact ? `<a class="so-btn art" href="${esc(c.artifact)}" target="_blank" rel="noopener" title="The claude.ai artifact for ${esc(c.country)} (its own store)">${I.ext}<span>claude.ai</span></a>` : ''}`;
    // (Kevin, 2026-11-11: "remove these buttons": open-in-a-window, import and reload are gone from the top. The maker
    // still opens alone at /social/<slug>/page, an ArtifactData export still imports with POST /api/social/<slug>/import
    // (Claude's job), and the tab reloads its list by itself when it is shown after 30 s.)
    wrap.querySelector('.so-tabs').innerHTML = `${tabs}<span class="acts">${acts}</span>`;
    const body = wrap.querySelector('.so-body');
    body.classList.toggle('scroll', !isMaker());
    let frame = body.querySelector('#so-frame'), panel = body.querySelector('.so-panel');
    if (!frame || frame.dataset.slug !== c.slug) {
      if (frame) frame.remove();
      S.pg = null; S.synced = false;
      body.insertAdjacentHTML('afterbegin', renderMaker(c)); frame = body.querySelector('#so-frame');
      frame.addEventListener('load', () => frameLoaded(frame));
    }
    if (!panel) { panel = document.createElement('div'); panel.className = 'so-panel'; body.appendChild(panel); }
    frame.hidden = !isMaker(); panel.hidden = isMaker();
    panel.innerHTML = isMaker() ? '' : v === 'renders' ? renderFree(c, rd, job) : v === 'slides' ? renderSlides(c, rd, job) : v === 'stories' ? renderStories(c, rd, job) : renderPins(c, rd, job);
    renderLight();
  }
  function renderMaker(c) {
    // the iframe is kept across renders (its state is the page's), re-made only for another country
    const src = API + '/social/' + encodeURIComponent(c.slug) + '/page?embed=1';
    return `<iframe class="so-frame" id="so-frame" title="IG post maker: ${esc(c.country)}" src="${esc(src)}" data-slug="${esc(c.slug)}"></iframe>`;
  }
  function thumbs(list, cls, labelOf, strip) {
    return `<div class="${strip ? 'so-strip' : 'so-grid'} ${cls || ''}">${list.map((f, i) => `<button type="button" class="so-thumb" data-act="light" data-set="${cls || 'c'}" data-i="${i}" title="${esc(f.name)}"><img src="${esc(API + f.url)}?t=${Math.round(f.mtime)}" alt="${esc(f.name)}" loading="lazy"><span class="k">${esc(labelOf ? labelOf(f, i) : i + 1)}</span></button>`).join('')}</div>`;
  }
  const capBox = (text, id) => text ? `<div class="so-cap" id="${id}">${esc(text.trim())}<button class="so-ib sm" data-act="copy" data-for="${id}" title="Copy" aria-label="Copy">${I.copy}</button></div>` : '';
  const failLog = job => job && job.status === 'failed' ? `<pre class="so-log">${esc(job.log || job.error || '')}</pre>` : '';
  const slideKey = f => f.name.replace(/\.png$/, '').replace(/^\d+-/, '');
  function renderFree(c, rd, job) {
    if (!rd) return '<div class="so-empty">Loading…</div>';
    let h = failLog(job) + `<div class="so-group" title="1080 px wide · also in Pictures\\IG\\${esc(c.slug)}">The post <span class="n">${rd.free.length}</span></div>`;
    const ar = S.pg && S.pgFor === c.slug && S.pg.aspect ? S.pg.aspect : '4:5';
    h += rd.free.length ? thumbs(rd.free, 'f', (f, i) => i + 1).replace('<div class="so-grid f', `<div style="--so-ar:${arCss(ar)}" class="so-grid f`) + capBox((rd.free_caption || '').trim() + (rd.free_notes ? '\n\n' + rd.free_notes.trim() : ''), 'so-cap-f')
      : '<div class="so-empty">No render yet. Fill the Final row in Shortlist, then press Render.</div>';
    return h;
  }
  function renderSlides(c, rd, job) {
    if (!rd) return '<div class="so-empty">Loading…</div>';
    let h = failLog(job) + `<div class="so-group">The carousel <span class="n">${rd.carousel.length}</span><span class="hint">1080 × 1350</span></div>`;
    h += rd.carousel.length ? thumbs(rd.carousel, 'c', slideKey) + capBox(rd.caption, 'so-cap-c')
      : `<div class="so-empty">No carousel slides yet.${c.has_plan ? ' Render the carousel above.' : ' Lay the plan out in Layout, or seed it.'}</div>`;
    return h;
  }
  function renderStories(c, rd, job) {
    if (!rd) return '<div class="so-empty">Loading…</div>';
    const blog = S.type === 'blog', list = blog ? rd.stories_carousel : rd.stories_free;
    let h = failLog(job) + `<div class="so-group">Stories <span class="n">${list.length}</span><span class="hint">1080 × 1920 · the top 250 px and bottom 340 px are left to Instagram's UI</span></div>`;
    h += list.length ? thumbs(list, blog ? 'sc' : 'sf', slideKey, true)
      : `<div class="so-empty">${blog ? 'No story frames yet. Render stories above: the cover, the route map, one frame per stop and the call to action, from the same plan as the carousel.'
                                       : 'No story frames yet. Fill the Final row in Shortlist (a slide’s note becomes its line), then Render stories above.'}</div>`;
    return h;
  }
  function renderPins(c, rd, job) {
    if (!rd) return '<div class="so-empty">Loading…</div>';
    return failLog(job) + `<div class="so-group">Pinterest <span class="n">${rd.pins.length}</span><span class="hint">1000 × 1500, one per article</span></div>` + (rd.pins.length
      ? thumbs(rd.pins, 'pins', f => f.name.replace(/\.png$/, '').replace(new RegExp('^' + c.slug.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '-'), ''))
      : `<div class="so-empty">No pins yet.${c.has_seed ? '' : ' A pins list lives in tools/ig_carousel/' + esc(c.slug) + '.json.'}</div>`);
  }

  // ------------------------------------------------------------------ the lightbox
  function lightEl() {
    let v = $('#so-light');
    if (!v) {
      v = document.createElement('div'); v.id = 'so-light'; v.hidden = true; v.setAttribute('role', 'dialog'); v.setAttribute('aria-modal', 'true'); v.setAttribute('aria-label', 'Slide viewer');
      document.body.appendChild(v);
      v.addEventListener('click', e => { const el = e.target.closest('[data-act]'); if (!el) return; if (el.dataset.act === 'close') closeLight(); else if (el.dataset.act === 'prev') stepLight(-1); else if (el.dataset.act === 'next') stepLight(1); });
      document.addEventListener('keydown', e => { if (!S.light) return; if (e.key === 'Escape') { e.preventDefault(); closeLight(); } else if (e.key === 'ArrowLeft') stepLight(-1); else if (e.key === 'ArrowRight') stepLight(1); });
    }
    return v;
  }
  const lightList = () => { const rd = S.renders || {}; return S.light ? ({ c: rd.carousel, f: rd.free, pins: rd.pins, sc: rd.stories_carousel, sf: rd.stories_free }[S.light.set] || []) : []; };
  function openLight(set, i) { S.light = { set, i }; renderLight(); setTimeout(() => { const x = $('#so-light .x'); if (x) x.focus(); }, 0); }
  function closeLight() { S.light = null; renderLight(); }
  function stepLight(d) { if (!S.light) return; const n = lightList().length, j = S.light.i + d; if (j < 0 || j >= n) return; S.light.i = j; renderLight(); }
  function renderLight() {
    const v = lightEl();
    if (!S.light) { v.hidden = true; return; }
    const list = lightList(), f = list[S.light.i]; if (!f) { S.light = null; v.hidden = true; return; }
    v.hidden = false;
    const story = S.light.set === 'sc' || S.light.set === 'sf';
    v.innerHTML = `<div class="bar"><button class="x" data-act="close" title="Close (Esc)" aria-label="Close">${I.x}</button><span class="ttl">${esc(f.name)}${story ? ' · story 1080 × 1920' : ''}</span><span class="pos">${S.light.i + 1} / ${list.length}</span>
        <a class="x" href="${esc(API + f.url)}" download="${esc(f.name)}" title="Save the file" aria-label="Save">${I.down}</a></div>
      <div class="stage"><img class="${story ? 'story' : ''}" src="${esc(API + f.url)}?t=${Math.round(f.mtime)}" alt="${esc(f.name)}">
        <button class="nav l" data-act="prev" title="Previous (←)" aria-label="Previous" ${S.light.i === 0 ? 'disabled' : ''}>&#8249;</button>
        <button class="nav r" data-act="next" title="Next (→)" aria-label="Next" ${S.light.i >= list.length - 1 ? 'disabled' : ''}>&#8250;</button></div>`;
  }

  // ------------------------------------------------------------------ add photos from the photo library
  // Kevin, 2026-11-11: "let me add more photos to the Photos tab from the photo library". The albums come from the
  // Library app's own routes on the photo server (/api/albums, /api/backup_browse, /bthumb); the picked photos go
  // through POST /api/social/<slug>/add, which makes their web copies and manifest entries (tools/social_tab.py), and
  // the maker picks them up on its next poll. Its "article" filter lists them as "Photo library: <album>".
  const LIB = { albums: null, album: '', photos: [], inSet: new Set(), sel: new Set(), picks: false, busy: false };
  function libEl() {
    let v = $('#so-lib');
    if (!v) {
      v = document.createElement('div'); v.id = 'so-lib'; v.hidden = true; v.setAttribute('role', 'dialog'); v.setAttribute('aria-modal', 'true'); v.setAttribute('aria-label', 'Add photos from the photo library');
      document.body.appendChild(v);
      v.addEventListener('click', e => {
        if (e.target === v) { closeLib(); return; }
        const t = e.target.closest('[data-l]'); if (!t) return; const a = t.dataset.l;
        if (a === 'close') closeLib(); else if (a === 'add') libAdd();
        else if (a === 't') { const nm = t.dataset.n; if (LIB.inSet.has(nm)) return; if (LIB.sel.has(nm)) LIB.sel.delete(nm); else LIB.sel.add(nm); t.classList.toggle('on', LIB.sel.has(nm)); libFoot(); }
      });
      v.addEventListener('change', e => { if (e.target.matches('select')) libAlbum(e.target.value); else if (e.target.matches('input[type=checkbox]')) { LIB.picks = e.target.checked; libGrid(); } });
      document.addEventListener('keydown', e => { if (!v.hidden && e.key === 'Escape') { e.preventDefault(); closeLib(); } });
    }
    return v;
  }
  async function openLib() {
    const v = libEl(); v.hidden = false; LIB.sel = new Set();
    v.innerHTML = `<div class="box"><div class="hd"><h2>Add photos</h2><select aria-label="Album"></select><label class="chk"><input type="checkbox" ${LIB.picks ? 'checked' : ''}> Blog picks only</label><span class="sp"></span><button class="btn" data-l="close">Close</button></div>
      <div class="grid"><div class="msg">Loading the albums…</div></div><div class="ft"><span class="cnt"></span><span class="sp"></span><button class="btn primary" data-l="add" disabled>Add</button></div></div>`;
    try { if (!LIB.albums) { const j = await api('/api/albums'); LIB.albums = (j.albums || []).map(a => a.name).filter(Boolean); } }
    catch (e) { const g = $('#so-lib .grid'); if (g) g.innerHTML = `<div class="msg">The photo library is not answering (${esc(e.message)}).</div>`; return; }
    const c = cur(), want = store.get('lib:' + S.slug, ''), first = c ? c.country.toLowerCase().split(/[\s+]+/)[0] : '';
    const guess = want && LIB.albums.includes(want) ? want : (LIB.albums.find(a => first && a.toLowerCase().startsWith(first)) || LIB.albums[0] || '');
    const sel = $('#so-lib select'); if (!sel) return;
    sel.innerHTML = LIB.albums.map(a => `<option ${a === guess ? 'selected' : ''}>${esc(a)}</option>`).join('');
    if (guess) libAlbum(guess); else $('#so-lib .grid').innerHTML = '<div class="msg">The photo library has no albums.</div>';
  }
  async function libAlbum(name) {
    LIB.album = name; LIB.sel = new Set(); store.set('lib:' + S.slug, name);
    const g = $('#so-lib .grid'); if (!g) return; g.innerHTML = '<div class="msg">Loading…</div>'; libFoot();
    try {
      const [b, have] = await Promise.all([api('/api/backup_browse?album=' + encodeURIComponent(name)), api('/api/social/' + encodeURIComponent(S.slug) + '/library?album=' + encodeURIComponent(name))]);
      if (LIB.album !== name) return;
      LIB.photos = b.photos || []; LIB.inSet = new Set(have.names || []);
    } catch (e) { g.innerHTML = `<div class="msg">Could not read this album (${esc(e.message)}).</div>`; return; }
    libGrid();
  }
  function libGrid() {
    const g = $('#so-lib .grid'); if (!g) return;
    const list = LIB.picks ? LIB.photos.filter(p => p.picked) : LIB.photos;
    g.innerHTML = list.length ? list.map(p => `<button type="button" class="t ${LIB.sel.has(p.n) ? 'on' : ''} ${LIB.inSet.has(p.n) ? 'in' : ''} ${p.picked ? 'pk' : ''}" data-l="t" data-n="${esc(p.n)}" title="${esc(p.n)}${LIB.inSet.has(p.n) ? ': already in' : p.picked ? ': a blog pick' : ''}"><img loading="lazy" src="${esc(API + '/bthumb?album=' + encodeURIComponent(LIB.album) + '&name=' + encodeURIComponent(p.n) + '&s=400')}" alt=""></button>`).join('')
      : `<div class="msg">${LIB.picks ? 'No blog picks in this album.' : 'No photos in this album.'}</div>`;
    libFoot();
  }
  function libFoot() {
    const n = LIB.sel.size, b = $('#so-lib [data-l="add"]'), c = $('#so-lib .cnt');
    if (b) { b.disabled = !n || LIB.busy; b.textContent = LIB.busy ? 'Adding…' : n ? 'Add ' + n + ' photo' + (n > 1 ? 's' : '') : 'Add'; }
    if (c) c.textContent = n ? n + ' selected' : '';
  }
  async function libAdd() {
    if (!LIB.sel.size || LIB.busy) return; LIB.busy = true; libFoot();
    try {
      const j = await api('/api/social/' + encodeURIComponent(S.slug) + '/add', { album: LIB.album, names: [...LIB.sel] });
      toast(j.added.length + ' photo' + (j.added.length === 1 ? '' : 's') + ' added' + (j.skipped.length ? ', ' + j.skipped.length + ' skipped' : '') + '.');
      LIB.busy = false; closeLib(); send({ cmd: 'poll' }); load(true);
    } catch (e) { toast(e.status === 0 ? 'The photo server is not answering.' : e.message, true); }
    finally { LIB.busy = false; libFoot(); }
  }
  function closeLib() { const v = $('#so-lib'); if (v) { v.hidden = true; v.innerHTML = ''; } }

  // ------------------------------------------------------------------ events
  function setView(id) { S.views[S.type] = id; store.set('view:' + S.type, id); render(); if (isMaker()) sync(); else loadRenders(); }
  function onClick(e) {
    const r = root(); if (!r || !r.contains(e.target)) return;
    const el = e.target.closest('[data-act]'); if (!el) return;
    const act = el.dataset.act;
    if (act === 'reload') { e.preventDefault(); load(); return; }
    if (act === 'type') { const t = typeOf(el.dataset.type); if (t === S.type) return; S.type = t; store.set('type', t); S.job = null; render(); if (isMaker()) sync(); else loadRenders(); return; }
    if (act === 'view') { setView(el.dataset.view); return; }
    if (act === 'render') { startRender(el.dataset.kind); return; }
    if (act === 'rendernow') { renderNow(); return; }
    if (act === 'aspect') { send({ cmd: 'aspect', aspect: el.dataset.aspect }); return; }
    if (act === 'preview') { openPreview(); return; }
    if (act === 'library') { openLib(); return; }
    if (act === 'seed') { seed(); return; }
    if (act === 'help') { toast(SHORTCUTS.replace(/\n/g, ' · ')); return; }
    if (act === 'light') { openLight(el.dataset.set, +el.dataset.i); return; }
    if (act === 'copy') { const box = document.getElementById(el.dataset.for); const text = box ? box.textContent : ''; (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(() => toast('Copied.'), () => toast('Could not copy.', true)); }
  }
  function onChange(e) {
    const r = root(); if (!r || !r.contains(e.target)) return;
    if (e.target.matches('[data-act="country"]')) { S.slug = e.target.value; store.set('country', S.slug); S.renders = null; S.rendersFor = null; S.job = null; S.article = { personal: '', blog: '' }; render(); loadRenders(); }
    else if (e.target.matches('[data-act="article"]')) { S.article[S.type] = e.target.value; render(); sync(); }
  }

  // ------------------------------------------------------------------ mount
  function mount(el) {
    if (el) S.root = el;
    const r = root(); if (!r || r.dataset.soMounted) { if (r) load(); return; }
    r.dataset.soMounted = '1';
    r.addEventListener('click', onClick); r.addEventListener('change', onChange);
    load();
  }
  function show() {
    const r = root(); if (!r) return;
    if (!r.dataset.soMounted) return mount();
    if (Date.now() - S.loadedAt > 30000) load(true); else { render(); if (!isMaker()) loadRenders(); }
  }
  window.__socialShow = show;
  window.__socialMount = mount;
  window.__social = S;                                       // test seam
  window.__socialReload = () => load(true);
  { const a = $('#app-social'); if (a && !a.hidden) mount(); }
})();
