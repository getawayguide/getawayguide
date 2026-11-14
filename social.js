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
              renders: null, rendersFor: null, job: null, light: null, loadedAt: 0, busy: false, pg: null, pgFor: null, synced: false,
              post: null, postFor: null, ig: { i: 0, more: false, grid: store.get('iggrid', '') === '1' } };
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
  /* the Post view (Kevin, 2026-11-11: "can the post tab show how it would look on IG?"): the Instagram preview on the
     left, the email card and the rendered files on the right. Inside the phone card Instagram's own type and colors. */
  .so-post{display:grid;grid-template-columns:minmax(0,400px) minmax(0,1fr);gap:32px;align-items:start}
  .so-ig-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 0 10px;max-width:400px}
  .so-ig-head .so-group{margin:0}
  .so-ig-phone{width:100%;max-width:400px;box-sizing:border-box;background:#fff;border:1px solid var(--so-line);border-radius:14px;overflow:hidden;color:#000;
    font:400 14px/18px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;box-shadow:0 1px 2px rgba(28,40,33,.04),0 10px 30px rgba(28,40,33,.07)}
  .so-ig-top{display:flex;align-items:center;gap:10px;padding:10px 6px 10px 12px}
  .so-ig-av{width:32px;height:32px;border-radius:50%;border:1px solid #dbdbdb;object-fit:cover;flex:none;box-sizing:border-box;background:#fafafa}
  .so-ig-top .who{flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.25}
  .so-ig-top b{font-weight:600;font-size:14px} .so-ig-top span{font-size:12px;color:#262626}
  .so-ig-ib{background:none;border:0;padding:7px;color:#000;display:grid;place-items:center;cursor:default}
  .so-ig-ib svg{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
  .so-ig-ib.dots svg{fill:currentColor;stroke:none}
  .so-ig-media{position:relative;overflow:hidden;background:#efefef;touch-action:pan-y;user-select:none;-webkit-user-select:none;outline:none;cursor:grab}
  .so-ig-media.drag{cursor:grabbing} .so-ig-media:focus-visible{box-shadow:inset 0 0 0 2px #0095f6}
  .so-ig-track{display:flex;height:100%;transition:transform .32s ease;will-change:transform}
  .so-ig-track>*{flex:0 0 100%;width:100%;height:100%;object-fit:cover;display:block;pointer-events:none}
  .so-ig-count{position:absolute;top:12px;right:12px;background:rgba(18,18,18,.72);color:#fff;font-size:12px;font-weight:600;line-height:1;padding:6px 8px;border-radius:13px;font-variant-numeric:tabular-nums}
  .so-ig-nav{position:absolute;top:50%;transform:translateY(-50%);width:28px;height:28px;border-radius:50%;background:rgba(255,255,255,.88);border:0;display:grid;place-items:center;cursor:pointer;
    opacity:0;transition:opacity .15s;box-shadow:0 0 5px rgba(0,0,0,.18);padding:0;color:#262626}
  .so-ig-media:hover .so-ig-nav,.so-ig-nav:focus-visible{opacity:1} .so-ig-nav.l{left:8px} .so-ig-nav.r{right:8px} .so-ig-nav[hidden]{display:none}
  .so-ig-nav svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:2.6;stroke-linecap:round;stroke-linejoin:round}
  .so-ig-acts{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:4px 4px 2px}
  .so-ig-acts .l{display:flex} .so-ig-acts .r{justify-self:end}
  .so-ig-dots{display:flex;gap:4px;justify-content:center;flex-wrap:wrap;max-width:150px}
  .so-ig-dots i{width:6px;height:6px;border-radius:50%;background:#a8a8a8;transition:background .2s}
  .so-ig-dots i.on{background:#0095f6}
  .so-ig-cap{padding:2px 12px 0;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
  .so-ig-cap b{font-weight:600} .so-ig-cap .tag{color:#00376b} .so-ig-cap i{color:#737373}
  .so-ig-more{background:none;border:0;padding:0;color:#737373;font:inherit;cursor:pointer}
  .so-ig-more:hover{color:#262626} .so-ig-more:focus-visible{outline:2px solid #0095f6;outline-offset:1px;border-radius:2px}
  .so-ig-time{padding:8px 12px 14px;font-size:12px;color:#737373}
  .so-ig-prof{display:flex;align-items:center;gap:18px;padding:18px 16px 14px}
  .so-ig-prof .so-ig-av{width:76px;height:76px}
  .so-ig-prof b{font-size:17px;font-weight:600;display:block;margin-bottom:4px} .so-ig-prof span{font-size:13px;color:#737373}
  .so-ig-gtab{display:flex;justify-content:center;border-top:1px solid #dbdbdb}
  .so-ig-gtab span{border-top:1px solid #000;margin-top:-1px;padding:10px 26px;display:grid;place-items:center}
  .so-ig-gtab svg{width:20px;height:20px;fill:none;stroke:#000;stroke-width:1.8}
  .so-ig-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:2px}
  .so-ig-grid .tile{aspect-ratio:3/4;background:#efefef;position:relative;overflow:hidden}
  .so-ig-grid .tile.old{background:#f3f3f3}
  .so-ig-grid .tile>img,.so-ig-grid .tile>canvas{width:100%;height:100%;object-fit:cover;object-position:center;display:block}
  .so-ig-grid .multi{position:absolute;top:7px;right:7px;width:18px;height:18px;fill:#fff;filter:drop-shadow(0 0 2px rgba(0,0,0,.45))}
  .so-ig-note{font-size:12px;color:var(--so-mute);margin:9px 2px 0;max-width:400px;line-height:1.5}
  .so-ig-empty{padding:40px 22px;text-align:center;color:#737373;font-size:13px;line-height:1.5}
  .so-mail{background:var(--so-soft);border:1px solid var(--so-line);border-radius:10px;padding:14px 16px;margin:0 0 26px;max-width:720px}
  .so-mail .so-group{margin-bottom:8px}
  .so-mail p{margin:0;line-height:1.55;color:var(--so-ink)} .so-mail p+p,.so-mail p+.so-btn,.so-mail p+.so-mail-st{margin-top:10px}
  .so-mail .warn{color:var(--so-amber)} .so-mail .faint{color:var(--so-mute);font-size:12px}
  .so-mail-st{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--so-mute)}
  .so-mail-st.run::before{content:'';width:10px;height:10px;border-radius:50%;border:2px solid var(--so-line);border-top-color:var(--so-green);animation:so-spin .8s linear infinite;flex:none}
  .so-mail-st.ok{color:var(--so-green);font-weight:600} .so-mail-st.bad{color:var(--so-red)}
  @media (max-width:900px){.so-post{grid-template-columns:minmax(0,1fr);gap:24px}}
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
    eye: '<svg viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    mail: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 7 8.5 6 8.5-6"/></svg>'
  };
  // Instagram's own glyphs for the preview (outline, 24 px)
  const IG = {
    heart: '<svg viewBox="0 0 24 24"><path d="M16.8 3.9c-2 0-3.6 1.1-4.8 2.8-1.2-1.7-2.8-2.8-4.8-2.8C4.3 3.9 2 6.3 2 9.3c0 5.4 7.6 10.3 10 11.7 2.4-1.4 10-6.3 10-11.7 0-3-2.3-5.4-5.2-5.4z"/></svg>',
    comment: '<svg viewBox="0 0 24 24"><path d="M20.66 16.92A9.5 9.5 0 1 0 17.1 20.3L22 21.5z"/></svg>',
    share: '<svg viewBox="0 0 24 24"><path d="M22 3 9.2 10.1M22 3 15 21.5l-5.8-11.4L2 7.6z"/></svg>',
    save: '<svg viewBox="0 0 24 24"><path d="M19.5 21 12 14.6 4.5 21V3h15z"/></svg>',
    dots: '<svg viewBox="0 0 24 24"><circle cx="5.5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="18.5" cy="12" r="1.6"/></svg>',
    grid: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18"/></svg>',
    multi: '<svg viewBox="0 0 48 48"><path d="M34.8 29.7V11c0-2.9-2.3-5.2-5.2-5.2H11c-2.9 0-5.2 2.3-5.2 5.2v18.7c0 2.9 2.3 5.2 5.2 5.2h18.7c2.8-.1 5.1-2.4 5.1-5.2zM39.2 15v16.1c0 4.5-3.7 8.2-8.2 8.2H14.9c-.6 0-.9.7-.5 1.1 1 1.1 2.4 1.8 4.1 1.8h13.4c5.7 0 10.3-4.6 10.3-10.3V18.5c0-1.6-.7-3.1-1.8-4.1-.5-.4-1.2 0-1.2.6z"/></svg>'
  };
  const SHORTCUTS = 'Keyboard shortcuts\nArrows  move around the grid\nShift+arrows  move the photo\n1–0  into slide 1–10\nS  into the Shortlist (again: out)\nDelete  out of its slot\nA  archive\nEnter  edit (crop and tilt)\nClick, P or Space  preview\nEsc  close';

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
    if (S.state === 'ok' && S.slug) { loadRenders(); if (S.type === 'personal' && view() === 'renders') loadPost(); }
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
      else if (S.job.kind === 'email') { toast(S.job.status === 'done' ? 'Sent. Check your phone.' : 'The email failed: ' + (S.job.error || 'see the log below'), S.job.status !== 'done'); loadPost(); loadRenders(); }
      else { render(); toast(S.job.status === 'done' ? 'Rendered.' : 'The render failed; the log is below.', S.job.status !== 'done'); loadRenders(); loadPost(); load(true); }
    }, 1500);
  }
  // the Post view's data: the Final row with its crops, caption, render (and whether it is stale) and the last email
  async function loadPost() {
    const slug = S.slug; if (!slug) return;
    const was = S.postFor === slug && S.post && S.post.email ? S.post.email.status : null;
    try {
      const p = await api('/api/social/' + encodeURIComponent(slug) + '/post');
      if (slug !== S.slug) return;
      S.post = p; S.postFor = slug;
      if (was === 'requested' && p.email && p.email.status === 'sent') toast('Sent. Check your phone.');
      else if (was === 'requested' && p.email && p.email.status === 'failed') toast('The email failed: ' + (p.email.error || ''), true);
      render();
    } catch (e) { if (slug === S.slug && e.status === 404 && !e.body) { S.post = { missing: true }; S.postFor = slug; render(); } }   // a photo server from before the route
    pollMail();
  }
  // a request Claude sends (no Gmail keys in .env): look again every 5 s while the Post view is open
  function pollMail() {
    clearTimeout(S.mailT);
    const e = S.post && S.post.email;
    if (!e || e.status !== 'requested') return;
    S.mailT = setTimeout(() => { const a = $('#app-social'); if ((!a || !a.hidden) && S.type === 'personal' && view() === 'renders') loadPost(); else pollMail(); }, 5000);
  }
  async function sendEmail() {
    if (S.busy || !S.slug) return;
    const slug = S.slug; S.busy = true; render();
    try {
      const j = await api('/api/social/' + encodeURIComponent(slug) + '/email', {});
      if (S.postFor === slug && S.post) S.post.email = j.email;
      if (j.job) { S.job = j.job; pollJob(); toast('Sending to your phone…'); }
      else { toast(j.already ? 'Already requested: Claude sends it in a moment.' : 'Requested: Claude sends it in a moment.'); pollMail(); }
    } catch (e) { toast(e.status === 0 ? 'The photo server is not answering.' : e.status === 404 && !e.body ? 'The photo server needs a restart for email (re-click the Editor Suite shortcut).' : e.message, true); loadPost(); }
    finally { S.busy = false; render(); }
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
    } else if (v === 'renders') {
      // Email to my phone: only a render that still matches the Final row goes out (the card under it says why not)
      const P = S.postFor === c.slug && S.post && !S.post.missing ? S.post : null, rs = P ? P.render.state : '';
      const sending = !!(job && job.kind === 'email' && ['queued', 'running'].includes(job.status)) || !!(P && P.email && P.email.status === 'sending');
      const why = !P ? 'Loading' : sending ? 'Sending it now' : rs === 'ok' ? 'Email the rendered slides at full quality, numbered in order, with the caption on top, to your phone'
        : rs === 'stale' ? 'The render is out of date: render again first' : rs === 'rendering' ? 'A render is running' : 'Render the post first';
      acts = `${jobLine}<button class="so-btn sm" data-act="email" ${P && rs === 'ok' && !off && !sending ? '' : 'disabled'} title="${esc(why)}">${I.mail}Email to my phone</button>`
        + rbtn('free', 'Render the post', true, 'Render the Final row from the originals at 1080 px (tools/ig_post.py)', true);
    }
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
    if (igDrag && v === 'renders' && !isMaker()) return;          // never pull the carousel from under a drag (a job poll re-draws)
    panel.innerHTML = isMaker() ? '' : v === 'renders' ? renderFree(c, rd, job) : v === 'slides' ? renderSlides(c, rd, job) : v === 'stories' ? renderStories(c, rd, job) : renderPins(c, rd, job);
    if (!isMaker() && v === 'renders') paintIg();
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
    const P = S.postFor === c.slug ? S.post : null;
    let side = `<div class="so-group" title="1080 px wide · also in Pictures\\IG\\${esc(c.slug)}">The post <span class="n">${rd.free.length}</span></div>`;
    const ar = P && !P.missing ? ((P.slides[0] && P.slides[0].aspect) || P.aspect) : S.pg && S.pgFor === c.slug && S.pg.aspect ? S.pg.aspect : '4:5';
    side += rd.free.length ? thumbs(rd.free, 'f', (f, i) => i + 1).replace('<div class="so-grid f', `<div style="--so-ar:${arCss(ar)}" class="so-grid f`) + capBox((rd.free_caption || '').trim() + (rd.free_notes ? '\n\n' + rd.free_notes.trim() : ''), 'so-cap-f')
      : '<div class="so-empty">No render yet. Fill the Final row in Shortlist, then press Render.</div>';
    const ig = !P ? '<div class="so-empty">Loading the preview…</div>'
      : P.missing ? '<div class="so-empty">The photo server needs a restart for the Instagram preview: re-click the Editor Suite shortcut.</div>' : igBlock(c, P);
    return failLog(job) + `<div class="so-post"><div class="so-post-ig">${ig}</div><div class="so-post-side">${P && !P.missing ? mailCard(c, P, job) : ''}${side}</div></div>`;
  }

  // ------------------------------------------------------------------ the Instagram preview (Post view)
  // How the Personal post looks in the feed: the account, the carousel at the post's ratio (the rendered files when they
  // are current, else the Final row's photos cut by their crops, the same math as the page and ig_post.py), the dots,
  // the action row and the caption cut like Instagram's (about 125 characters, then "more"). "Profile grid" shows the
  // cover as the grid tile: 3:4, cut around its center (Instagram's grid since January 2025).
  const ASPECT = { '4:5': 5 / 4, '3:4': 4 / 3, '1:1': 1, '1.91:1': 1 / 1.91 };
  function clampCrop(c, W, H, ratio) {                      // tools/ig_post_page.html clampCrop (= ig_post.py clamp_crop)
    const rot = Math.max(-45, Math.min(45, +c.rot || 0));
    const th = rot * Math.PI / 180, cs = Math.abs(Math.cos(th)), sn = Math.abs(Math.sin(th));
    const fwMax = Math.min(W / (cs + ratio * sn), H / (sn + ratio * cs));
    let fw = Math.min((+c.w || 1) * W, fwMax); fw = Math.max(fw, Math.min(fwMax, .05 * W));
    const fh = fw * ratio, hx = fw / 2 * cs + fh / 2 * sn, hy = fw / 2 * sn + fh / 2 * cs;
    const cx = Math.min(Math.max((c.cx ?? .5) * W, hx), W - hx), cy = Math.min(Math.max((c.cy ?? .5) * H, hy), H - hy);
    return { cx: cx / W, cy: cy / H, w: fw / W, rot };
  }
  const imgCache = new Map();
  function loadImg(url) {
    if (imgCache.has(url)) return imgCache.get(url);
    const p = new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = url; });
    imgCache.set(url, p); p.catch(() => imgCache.delete(url)); return p;
  }
  function paintIg() {
    const P = S.post; if (!P || P.missing) return;
    const r = root(); if (!r) return;
    r.querySelectorAll('canvas.so-igc').forEach(cv => {
      const s = P.slides[+cv.dataset.k]; if (!s) return;
      loadImg(API + s.blob).then(img => {
        if (!cv.isConnected) return;
        const W = s.w || img.naturalWidth, H = s.h || img.naturalHeight;
        const c = clampCrop(Object.assign({ cx: .5, cy: .5, w: 1, rot: 0 }, s.crop || {}), W, H, ASPECT[s.aspect] || 1.25);
        const ctx = cv.getContext('2d'), cw = cv.width, ch = cv.height, iw = img.naturalWidth, ih = img.naturalHeight, k = cw / (c.w * iw);
        ctx.save(); ctx.fillStyle = '#000'; ctx.fillRect(0, 0, cw, ch);
        ctx.translate(cw / 2, ch / 2); ctx.rotate(c.rot * Math.PI / 180); ctx.scale(k, k); ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(img, -c.cx * iw, -c.cy * ih); ctx.restore(); cv.dataset.painted = '1';
      }).catch(() => {});
    });
  }
  // the slides the preview shows: the rendered files when they match the Final row, else the crops
  const igFromRender = P => P.render && P.render.state === 'ok' && P.render.files.length === P.slides.length;
  function igSlide(P, k, cls) {
    if (igFromRender(P)) { const f = P.render.files[k]; return `<img class="${cls || ''}" src="${esc(API + f.url)}?t=${Math.round(f.mtime)}" alt="Slide ${k + 1}" draggable="false">`; }
    const s = P.slides[k], w = 720;
    return `<canvas class="so-igc ${cls || ''}" data-k="${k}" width="${w}" height="${Math.round(w * (ASPECT[s.aspect] || 1.25))}" aria-label="Slide ${k + 1}"></canvas>`;
  }
  function igCaption(P, handle) {
    const full = (P.caption || '') + (P.hashtags ? (P.caption ? '\n\n' : '') + P.hashtags : '');
    if (!full.trim()) return `<div class="so-ig-cap"><b>${esc(handle)}</b> <i>No caption yet: write it in the Caption tab.</i></div>`;
    const tags = t => esc(t).replace(/(^|[\s(])(#[\p{L}\p{N}_]+)/gu, '$1<span class="tag">$2</span>');
    // Instagram's feed shows about 125 characters (and never past the first line break), then "… more"
    let cut = null;
    const nl = full.indexOf('\n');
    if (nl >= 0 && nl <= 125) cut = full.slice(0, nl);
    else if (full.length > 125) { cut = full.slice(0, 125); const sp = cut.lastIndexOf(' '); if (sp > 90) cut = cut.slice(0, sp); }
    if (cut === null || S.ig.more) return `<div class="so-ig-cap" data-full="1"><b>${esc(handle)}</b> ${tags(full)}</div>`;
    return `<div class="so-ig-cap"><b>${esc(handle)}</b> ${tags(cut.trimEnd())}<button type="button" class="so-ig-more" data-act="igmore" aria-label="Show the whole caption">… more</button></div>`;
  }
  function igBlock(c, P) {
    const n = P.slides.length, a = (P.slides[0] && P.slides[0].aspect) || P.aspect, handle = P.handle || 'getawayguide';
    S.ig.i = Math.max(0, Math.min(S.ig.i, n - 1));
    const i = S.ig.i, av = `<img class="so-ig-av" src="${esc(siteUrl(P.avatar || 'Images/web/icons/icon-192.png'))}" alt="">`;
    const head = `<div class="so-ig-head"><span class="so-group">On Instagram</span><div class="so-seg sm" role="group" aria-label="Preview">
        <button type="button" class="${S.ig.grid ? '' : 'on'}" data-act="iggrid" data-grid="0" aria-pressed="${!S.ig.grid}">Post</button>
        <button type="button" class="${S.ig.grid ? 'on' : ''}" data-act="iggrid" data-grid="1" aria-pressed="${S.ig.grid}" title="The cover as your profile grid shows it: 3:4, cut around its center">Profile grid</button></div></div>`;
    if (!n) return head + `<div class="so-ig-phone"><div class="so-ig-empty">Put photos in the Final row (Shortlist) and the post shows here as Instagram will show it.</div></div>`;
    const src = igFromRender(P) ? `The rendered files: ${n} slide${n > 1 ? 's' : ''} at ${a}.`
      : P.render.state === 'stale' ? `From your crops: the render is out of date (${esc(P.render.reason)}).` : 'From your crops: not rendered yet.';
    if (S.ig.grid) {
      const tiles = [`<div class="tile">${igSlide(P, 0)}${n > 1 ? IG.multi.replace('<svg ', '<svg class="multi" aria-label="Carousel" ') : ''}</div>`]
        .concat(Array.from({ length: 5 }, () => '<div class="tile old" aria-hidden="true"></div>')).join('');
      return head + `<div class="so-ig-phone so-ig-gridview"><div class="so-ig-prof">${av}<div><b>${esc(handle)}</b><span>Your newest post leads the grid</span></div></div>
          <div class="so-ig-gtab"><span>${IG.grid}</span></div><div class="so-ig-grid">${tiles}</div></div>
        <div class="so-ig-note">The grid shows the cover cut to 3:4 around its center${a === '3:4' ? ' (your 3:4 post fits it exactly)' : ''}. ${src}</div>`;
    }
    const slides = P.slides.map((s, k) => igSlide(P, k)).join('');
    const dots = n > 1 ? `<span class="so-ig-dots" aria-hidden="true">${P.slides.map((s, k) => `<i class="${k === i ? 'on' : ''}"></i>`).join('')}</span>` : '<span></span>';
    return head + `<div class="so-ig-phone">
        <div class="so-ig-top">${av}<div class="who"><b>${esc(handle)}</b><span>${esc(P.country || c.country)}</span></div><span class="so-ig-ib dots" aria-hidden="true">${IG.dots}</span></div>
        <div class="so-ig-media" tabindex="0" style="aspect-ratio:${arCss(a)}" role="region" aria-roledescription="carousel" aria-label="Slide ${i + 1} of ${n}">
          <div class="so-ig-track" style="transform:translateX(${-i * 100}%)">${slides}</div>
          ${n > 1 ? `<span class="so-ig-count">${i + 1}/${n}</span>
          <button type="button" class="so-ig-nav l" data-act="igstep" data-d="-1" aria-label="Previous slide" ${i === 0 ? 'hidden' : ''}>${I.left}</button>
          <button type="button" class="so-ig-nav r" data-act="igstep" data-d="1" aria-label="Next slide" ${i >= n - 1 ? 'hidden' : ''}>${I.right}</button>` : ''}
        </div>
        <div class="so-ig-acts"><span class="l"><span class="so-ig-ib">${IG.heart}</span><span class="so-ig-ib">${IG.comment}</span><span class="so-ig-ib">${IG.share}</span></span>${dots}<span class="r"><span class="so-ig-ib">${IG.save}</span></span></div>
        ${igCaption(P, handle)}
        <div class="so-ig-time">Just now</div></div>
      <div class="so-ig-note">${src}</div>`;
  }
  // move the carousel without re-drawing the tab (a drag or an arrow keeps its place)
  function igGo(k) {
    const P = S.post; if (!P || P.missing) return;
    const n = P.slides.length; S.ig.i = Math.max(0, Math.min(n - 1, k));
    const r = root(), m = r && r.querySelector('.so-ig-media'); if (!m) return;
    const tr = m.querySelector('.so-ig-track'); tr.style.transition = ''; tr.style.transform = `translateX(${-S.ig.i * 100}%)`;
    const cnt = m.querySelector('.so-ig-count'); if (cnt) cnt.textContent = (S.ig.i + 1) + '/' + n;
    m.setAttribute('aria-label', `Slide ${S.ig.i + 1} of ${n}`);
    const l = m.querySelector('.so-ig-nav.l'), rr = m.querySelector('.so-ig-nav.r'); if (l) l.hidden = S.ig.i === 0; if (rr) rr.hidden = S.ig.i >= n - 1;
    r.querySelectorAll('.so-ig-dots i').forEach((d, k2) => d.classList.toggle('on', k2 === S.ig.i));
  }
  let igDrag = null;
  function igDown(e) {
    const m = e.target.closest && e.target.closest('.so-ig-media');
    if (!m || e.button !== 0 || e.target.closest('.so-ig-nav') || !S.post || S.post.slides.length < 2) return;
    igDrag = { x: e.clientX, dx: 0, m, w: m.clientWidth, tr: m.querySelector('.so-ig-track') };
    igDrag.tr.style.transition = 'none'; m.classList.add('drag');
    try { m.setPointerCapture(e.pointerId); } catch (x) {}
  }
  function igMove(e) {
    if (!igDrag) return;
    igDrag.dx = e.clientX - igDrag.x;
    const n = S.post.slides.length, edge = (S.ig.i === 0 && igDrag.dx > 0) || (S.ig.i === n - 1 && igDrag.dx < 0);
    igDrag.tr.style.transform = `translateX(calc(${-S.ig.i * 100}% + ${edge ? igDrag.dx / 3 : igDrag.dx}px))`;
  }
  function igUp() {
    if (!igDrag) return;
    const d = igDrag.dx, w = igDrag.w; igDrag.m.classList.remove('drag'); igDrag = null;
    igGo(S.ig.i + (d < -Math.min(60, w / 5) ? 1 : d > Math.min(60, w / 5) ? -1 : 0));
  }
  function mailCard(c, P, job) {
    const R = P.render, E = P.email, n = R.files.length, mb = (R.total_bytes / 1e6).toFixed(1), pad = x => String(x).padStart(2, '0');
    let h = '<div class="so-mail"><div class="so-group">Email to my phone</div>';
    if (R.state === 'none') h += '<p>Render the post first, then email it to your phone: the slides at full quality, numbered 01.jpg, 02.jpg … in order, with the caption at the top.</p>';
    else if (R.state === 'rendering') h += '<p>Rendering. You can email it when the render is done.</p>';
    else if (R.state === 'stale') h += `<p class="warn">The render is out of date: ${esc(R.reason)}. Render again before you email it, so the phone gets what you see.</p><button class="so-btn sm" data-act="render" data-kind="free">Render again</button>`;
    else h += `<p>${n} slide${n === 1 ? '' : 's'}, ${mb} MB: the rendered files themselves at full quality, named 01.jpg to ${pad(n)}.jpg, with the caption and hashtags at the top of the email, ready to copy.${R.emails > 1 ? ` It comes in ${R.emails} emails (Gmail takes 25 MB each).` : ''}</p>`;
    const sendJob = job && job.kind === 'email' ? job : null;
    const t = iso => { if (!iso) return ''; const d = new Date(iso); if (isNaN(d)) return iso; const same = d.toDateString() === new Date().toDateString();
      return (same ? '' : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ', ') + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }); };
    if (sendJob && ['queued', 'running'].includes(sendJob.status)) h += `<p class="so-mail-st run">Sending… ${esc((sendJob.log || '').trim().split('\n').pop() || '')}</p>`;
    else if (E && E.status === 'sending') h += '<p class="so-mail-st run">Sending…</p>';
    else if (E && E.status === 'requested') h += '<p class="so-mail-st run">Requested. Claude sends it in a moment.</p>';
    else if (E && E.status === 'sent') h += `<p class="so-mail-st ok">Sent ${esc(t(E.sent_at))}${E.emails > 1 ? ` in ${E.emails} emails` : ''} · ${E.slides} slide${E.slides === 1 ? '' : 's'}</p>`;
    else if (E && E.status === 'failed') h += `<p class="so-mail-st bad">Failed: ${esc(E.error || 'unknown')}</p>`;
    if (!P.direct) h += '<p class="faint">No Gmail keys in .env on this server, so Claude sends it for you from your Gmail.</p>';
    return h + '</div>';
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
  function setView(id) { S.views[S.type] = id; store.set('view:' + S.type, id); render(); if (isMaker()) sync(); else { loadRenders(); if (id === 'renders') loadPost(); } }
  function onClick(e) {
    const r = root(); if (!r || !r.contains(e.target)) return;
    const el = e.target.closest('[data-act]'); if (!el) return;
    const act = el.dataset.act;
    if (act === 'reload') { e.preventDefault(); load(); return; }
    if (act === 'type') { const t = typeOf(el.dataset.type); if (t === S.type) return; S.type = t; store.set('type', t); S.job = null; render(); if (isMaker()) sync(); else { loadRenders(); if (t === 'personal' && view() === 'renders') loadPost(); } return; }
    if (act === 'view') { setView(el.dataset.view); return; }
    if (act === 'render') { startRender(el.dataset.kind); return; }
    if (act === 'rendernow') { renderNow(); return; }
    if (act === 'aspect') { send({ cmd: 'aspect', aspect: el.dataset.aspect }); return; }
    if (act === 'preview') { openPreview(); return; }
    if (act === 'library') { openLib(); return; }
    if (act === 'seed') { seed(); return; }
    if (act === 'help') { toast(SHORTCUTS.replace(/\n/g, ' · ')); return; }
    if (act === 'light') { openLight(el.dataset.set, +el.dataset.i); return; }
    if (act === 'email') { sendEmail(); return; }
    if (act === 'igstep') { igGo(S.ig.i + (+el.dataset.d || 0)); return; }
    if (act === 'igmore') { S.ig.more = true; render(); return; }
    if (act === 'iggrid') { S.ig.grid = el.dataset.grid === '1'; store.set('iggrid', S.ig.grid ? '1' : ''); render(); return; }
    if (act === 'copy') { const box = document.getElementById(el.dataset.for); const text = box ? box.textContent : ''; (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(() => toast('Copied.'), () => toast('Could not copy.', true)); }
  }
  function onChange(e) {
    const r = root(); if (!r || !r.contains(e.target)) return;
    if (e.target.matches('[data-act="country"]')) { S.slug = e.target.value; store.set('country', S.slug); S.renders = null; S.rendersFor = null; S.job = null; S.article = { personal: '', blog: '' };
      S.post = null; S.postFor = null; S.ig.i = 0; S.ig.more = false; render(); loadRenders(); if (S.type === 'personal' && view() === 'renders') loadPost(); }
    else if (e.target.matches('[data-act="article"]')) { S.article[S.type] = e.target.value; render(); sync(); }
  }

  // ------------------------------------------------------------------ mount
  function mount(el) {
    if (el) S.root = el;
    const r = root(); if (!r || r.dataset.soMounted) { if (r) load(); return; }
    r.dataset.soMounted = '1';
    r.addEventListener('click', onClick); r.addEventListener('change', onChange);
    r.addEventListener('pointerdown', igDown); r.addEventListener('pointermove', igMove);
    r.addEventListener('pointerup', igUp); r.addEventListener('pointercancel', igUp);
    r.addEventListener('keydown', e => { if (!e.target.matches || !e.target.matches('.so-ig-media')) return;
      if (e.key === 'ArrowLeft') { e.preventDefault(); igGo(S.ig.i - 1); } else if (e.key === 'ArrowRight') { e.preventDefault(); igGo(S.ig.i + 1); } });
    load();
  }
  function show() {
    const r = root(); if (!r) return;
    if (!r.dataset.soMounted) return mount();
    if (Date.now() - S.loadedAt > 30000) load(true); else { render(); if (!isMaker()) loadRenders(); if (S.type === 'personal' && view() === 'renders') loadPost(); }
  }
  window.__socialShow = show;
  window.__socialMount = mount;
  window.__social = S;                                       // test seam
  window.__socialReload = () => load(true);
  { const a = $('#app-social'); if (a && !a.hidden) mount(); }
})();
