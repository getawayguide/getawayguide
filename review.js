/* review.js - Track Changes and Comments inside the article editor, Word style.

   Loaded by editor.html. When an article opens, this asks the photo server whether a pending
   proposal exists for it and marks every change in the editor's own DOM:

     <del class="rl">  red strikethrough, a deletion       (green double rule when it moves)
     <ins class="rl">  blue underline, an insertion
     <sup class="rl-n"> the change number, matching its card in the margin

   The marks are contenteditable=false islands, so the surrounding text stays editable while a
   change is pending. Accept removes the mark and leaves the new text; Reject removes the mark
   and leaves the original; both are ordinary undoable edits, and both post the decision at once.
   As in Word, the review moves to the next change after each one.

   The review lives in the editor's ONE left sidebar, swapping in for the to-do list the way the
   photo library does. It is a margin in Word's sense: change cards and comment cards in one
   stream, in document order, each card level with the text it belongs to and joined to it by a
   connector line (the classic balloon line), following the document as it scrolls. A change
   card carries Word's Track Changes card: who, when, what was deleted or inserted, and a tick
   to accept or a cross to reject, either of which previews its result in the text while hovered.
   Comments follow Word's modern comments: select text, + Comment (Ctrl+Alt+M), draft, Post
   (Ctrl+Enter), one draft at a time; the anchored text is highlighted; click either side to
   emphasise the other; reply, resolve (hidden from the margin, still in List), reopen, edit your
   own, delete. Comments are stored per article on the server, never in the article.

   Nothing about a review ever reaches the saved article: on Save, prepareForSave() unwraps
   whatever is still pending as its original text and strips comment anchors. Accepted changes
   are already plain text by then. After the write, onSaved() asks the server to commit the half
   the editor cannot do itself: the destination side of accepted moves, which live in other files. */
(function () {
  'use strict';
  const API = 'http://127.0.0.1:5003';
  const AUTHOR = 'Kevin';
  const REVIEWER = 'Claude';
  const $ = id => document.getElementById(id);
  const ed = () => $('editor');

  const state = { rel: null, slug: null, key: null, changes: [], st: {}, last: {}, comments: { threads: [] },
                  active: null, filter: 'all', view: 'contextual', draft: null, hidden: [], hover: null };

  // ------------------------------------------------------------------ pane
  function buildPane() {
    if ($('review-pane')) return;
    const side = document.querySelector('.sidebar');
    const pane = document.createElement('div');
    pane.id = 'review-pane';
    pane.innerHTML = `
      <div class="rv-head">
        <div class="rv-chips"><button id="rv-f-all" aria-pressed="true" title="Changes and comments together">All</button>
          <button id="rv-f-changes" title="Only tracked changes">Changes <span id="rv-n-ch">0</span></button>
          <button id="rv-f-comments" title="Only comments">Comments <span id="rv-n-cm">0</span></button></div>
        <button id="rv-refresh" class="rv-ico" title="Pick up comments or changes added since the article was opened">&#8635;</button>
        <button id="rv-close" class="rv-ico" title="Close the review and go back to the to-do list">&times;</button>
      </div>
      <div id="rv-banner" style="display:none"></div>
      <div class="rv-tools">
        <button id="rv-new" class="rv-btn primary" title="Comment on the selected text (Ctrl+Alt+M)">+ Comment</button>
        <div class="rv-seg"><button id="rv-prev" title="Previous change">&#8593;</button><button id="rv-next" title="Next change">&#8595;</button></div>
        <div class="rv-seg"><button id="rv-markup" aria-pressed="true" title="All markup: every change shown on the page">Markup</button><button id="rv-final" title="No markup: the page as it reads with the decisions so far">Clean</button></div>
        <div class="rv-seg"><button id="rv-v-ctx" aria-pressed="true" title="Cards level with the text they belong to">Beside</button><button id="rv-v-list" title="Every card as a list, unplaced ones too">List</button></div>
        <button id="rv-resolved" class="rv-btn" aria-pressed="false" title="Show resolved comment threads in the list" style="display:none">Resolved <span id="rv-n-res">0</span></button>
        <span class="rv-more"><button id="rv-more" class="rv-ico" title="More">&#8943;</button>
          <div class="rv-dd"><button id="rv-all-yes">Accept all changes</button><button id="rv-all-no">Reject all changes</button></div></span>
      </div>
      <div class="rv-status" id="rv-status"></div>
      <div class="rv-body" id="rv-body"><div class="rv-list" id="rv-list"></div><div class="rv-canvas" id="rv-canvas"></div></div>
      <div id="rv-toast"></div>`;
    side.appendChild(pane);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.id = 'rv-lines'; document.body.appendChild(svg);
    $('rv-close').onclick = () => toggle(false);
    $('rv-refresh').onclick = () => refresh();
    hashOpen();                                   // /browser?review=<path>: opened from Claude's pass
    $('rv-f-all').onclick = () => setFilter('all');
    $('rv-f-changes').onclick = () => setFilter('changes');
    $('rv-f-comments').onclick = () => setFilter('comments');
    $('rv-markup').onclick = () => setMarkup(true);
    $('rv-final').onclick = () => setMarkup(false);
    $('rv-prev').onclick = () => step(-1);
    $('rv-next').onclick = () => step(1);
    $('rv-more').onclick = e => { e.stopPropagation(); $('rv-more').parentElement.classList.toggle('open'); };
    document.addEventListener('click', () => { const m = $('rv-more'); if (m) m.parentElement.classList.remove('open'); });
    $('rv-all-yes').onclick = () => decideAll(true);
    $('rv-all-no').onclick = () => decideAll(false);
    $('rv-new').onmousedown = e => { e.preventDefault(); captureSelection(); };
    $('rv-new').onclick = () => newComment();
    $('rv-v-ctx').onclick = () => setView('contextual');
    $('rv-v-list').onclick = () => setView('list');
    $('rv-resolved').onclick = () => { state.showResolved = !state.showResolved; $('rv-resolved').setAttribute('aria-pressed', state.showResolved); renderAll(); };
    // the margin follows the document: re-place the cards whenever anything moves
    const scroller = document.querySelector('.editor-scroll');
    scroller.addEventListener('scroll', () => queueLayout(), { passive: true });
    // beside the text the margin never scrolls on its own (a focus() or scrollIntoView inside an
    // overflow:hidden box still can), it follows the document; in List it is an ordinary list
    $('rv-canvas').addEventListener('transitionend', () => drawLines());
    $('rv-body').addEventListener('scroll', () => { const b = $('rv-body'); if (!b.classList.contains('list') && b.scrollTop) { b.scrollTop = 0; layout(); } else drawLines(); }, { passive: true });
    window.addEventListener('resize', () => queueLayout());
    ed().addEventListener('input', () => { clearTimeout(state._lt); state._lt = setTimeout(layout, 120); });
    ed().addEventListener('load', () => queueLayout(), true);          // images arriving shift the text
    if (window.ResizeObserver) new ResizeObserver(() => queueLayout()).observe(ed());
    ed().addEventListener('click', e => {
      const m = e.target.closest('[data-id]'); if (m && (m.classList.contains('rl') || m.classList.contains('rl-n'))) { jump(m.dataset.id, true); return; }
      const c = e.target.closest('mark.cm'); if (c) { focusThread(c.dataset.cm, true); }
    });
    document.addEventListener('keydown', e => {
      if (e.ctrlKey && e.altKey && (e.key === 'm' || e.key === 'M')) { e.preventDefault(); captureSelection(); newComment(); }
    });
  }
  function toggle(on) {
    const want = on === undefined ? !document.body.classList.contains('review-open') : on;
    if (want) document.body.classList.remove('photos-open');        // one sidebar: the review takes the photo library's place
    document.body.classList.toggle('review-open', want);
    if (want) { renderAll(); } else drawLines();
  }
  function setFilter(f) {
    state.filter = f;
    for (const k of ['all', 'changes', 'comments']) $('rv-f-' + k).setAttribute('aria-pressed', f === k);
    renderAll();
  }
  function setView(v) { state.view = v; $('rv-v-ctx').setAttribute('aria-pressed', v === 'contextual'); $('rv-v-list').setAttribute('aria-pressed', v === 'list'); renderAll(); }
  function setMarkup(all) { ed().classList.toggle('rl-final', !all); $('rv-markup').setAttribute('aria-pressed', all); $('rv-final').setAttribute('aria-pressed', !all); renderAll(); }
  function toast(msg, ms) { const t = $('rv-toast'); t.textContent = msg; t.style.display = 'block'; clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', ms || 3500); }
  function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
  function plain(h) { const d = document.createElement('div'); d.innerHTML = h || ''; return d.textContent; }
  function dirty() { ed().dispatchEvent(new Event('input', { bubbles: true })); if (window.setDirty) try { window.setDirty(true); } catch (e) {} }
  const H = () => window.__History;

  // ------------------------------------------------------------------ boot
  async function boot(rel) {
    state.rel = rel; state.slug = null; state.changes = []; state.st = {}; state.last = {}; state.comments = { threads: [] }; state.hidden = []; state.active = null;
    let r;
    try { r = await (await fetch(API + '/review/for?rel=' + encodeURIComponent(rel), { cache: 'no-store' })).json(); }
    catch (e) { console.warn('review: server not reachable', e); return; }
    state.slug = r.slug; state.key = r.comments_key; state.comments = r.comments || { threads: [] };
    state.meta = { title: r.title, before: r.words_before, target: r.words_target, tlabel: r.target_label };
    if (state.awaitingFile) { state.view = 'contextual'; $('rv-v-ctx').setAttribute('aria-pressed', true); $('rv-v-list').setAttribute('aria-pressed', false); }
    state.awaitingFile = false;
    snapshotDisk(rel);                                   // so a later change on disk can be noticed
    if (r.slug) {
      state.changes = r.changes;
      for (const id in r.decisions) state.last[id] = r.decisions[id];
      injectMarks();
    }
    anchorComments();
    if (H()) H().reset();
    renderAll();
    if (state.changes.length || state.comments.threads.length) toggle(true);
    if (state.changes.length) toast(`${state.changes.length} proposed changes to review`, 4000);
  }

  // ------------------------------------------------------------------ marks
  const ENT = s => s.replace(/&mdash;/g, '—').replace(/&ndash;/g, '–').replace(/&nbsp;/g, ' ')
                    .replace(/&rsquo;/g, '’').replace(/&lsquo;/g, '‘').replace(/&hellip;/g, '…');
  function locate(html, needle) {          // the proposal was matched against the file; the DOM re-serialises entities
    if (html.indexOf(needle) >= 0) return needle;
    const n2 = ENT(needle); if (html.indexOf(n2) >= 0) return n2;
    return null;
  }
  function injectMarks(only) {               // only: just these changes, numbered after the existing ones
    const e = ed(); let html = e.innerHTML;
    const spans = [];
    const list = only || state.changes;
    let n = only ? Math.max(0, ...state.changes.map(c => c.n || 0)) : 0;
    // Every uncommitted change is marked, whatever the server has recorded for it: a decision
    // only becomes real when it is applied in this text and saved. (The standalone review page
    // stored "accept all" as a UI default, which once left this loop with nothing to mark.)
    for (const c of list) {
      if (c.kind === 'grammar') trimTags(c);
      if (c.kind === 'insert') {                 // zero-width span right after the anchor
        const key = locate(html, c.after); if (!key) { state.hidden.push(c.id); continue; }
        const i = html.indexOf(key) + key.length; spans.push([i, i, c, '']); continue;
      }
      const key = locate(html, c.kind === 'grammar' ? c.find : c.sentence);
      if (!key) { state.hidden.push(c.id); continue; }
      const i = html.indexOf(key); spans.push([i, i + key.length, c, key]);
    }
    spans.sort((a, b) => a[0] - b[0] || (b[1] - b[0]) - (a[1] - a[0]));
    const outer = [];
    for (let i = 0; i < spans.length;) {
      const [s0, e0, c] = spans[i]; const inner = [];
      let j = i + 1; while (j < spans.length && spans[j][0] < e0) { inner.push(spans[j][2]); j++; }
      c.inner = inner.map(x => x.id); inner.forEach(x => x.inside = c.id);
      outer.push(spans[i]); i = j;
    }
    let out = '', pos = 0;
    for (const [s0, e0, c, key] of outer) {
      out += html.slice(pos, s0); n++; c.n = n;
      out += markHtml(c, html.slice(s0, e0), n);
      pos = e0;
    }
    out += html.slice(pos);
    let m = n; for (const [, , c] of outer) for (const id of c.inner) { m++; byId(id).n = m; }
    for (const id of state.hidden) if (!byId(id).n) { m++; byId(id).n = m; }
    e.innerHTML = out;
    if (window.restoreSlotButtons) window.restoreSlotButtons(e);
  }
  // A fix may be anchored with the tag that follows it ("…traveled</div>" so it matches the end
  // of a card and nothing else). The tag is not part of the change: wrapping it in <del> split
  // the fact card's closing tag and, once accepted, pushed the remaining cards out of the grid.
  function trimTags(c) {
    if (c._trimmed) return; c._trimmed = true;
    // trailing: a shared run of CLOSING tags, but ONLY when their openers sit outside the
    // change. A find that ends at "</b>" whose "<b>" opens INSIDE it (a sentence whose tail
    // clause is bold) is balanced as written; stripping that "</b>" left an open <b> inside
    // the <del> and the <ins>, the parser re-balanced around it, and accepting the change
    // bolded the whole sentence. Same guard as the leading run below.
    const END = /(<\/[a-z][^>]*>\s*)+$/i;
    let a = (c.find.match(END) || [''])[0], b = (c.replace.match(END) || [''])[0];
    if (a && a === b) {
      const shut = [...a.matchAll(/<\/([a-z][a-z0-9]*)/gi)].map(m => m[1].toLowerCase());
      const rest = c.find.slice(0, -a.length);
      if (!shut.some(n => new RegExp('<' + n + '[ >]', 'i').test(rest))) { c.find = rest; c.replace = c.replace.slice(0, -b.length); }
    }
    // leading: a shared run of OPENING tags, but only when none of them closes inside the
    // change (a shared "<b>…</b>" belongs to the text; stripping it would leave a stray </b>)
    const TAG = /^(\s*<[a-z][^>]*>)+/i;
    a = (c.find.match(TAG) || [''])[0]; b = (c.replace.match(TAG) || [''])[0];
    if (a && a === b) {
      const names = [...a.matchAll(/<([a-z][a-z0-9]*)/gi)].map(m => m[1].toLowerCase());
      const rest = c.find.slice(a.length);
      if (!names.some(n => new RegExp('</' + n + '\\s*>', 'i').test(rest))) { c.find = rest; c.replace = c.replace.slice(b.length); }
    }
  }
  function markHtml(c, oldHtml, n) {
    const ce = ' contenteditable="false"';
    if (c.kind === 'insert')
      return `<ins class="rl ed blk" data-id="${c.id}"${ce}>${c.html}</ins><sup class="rl-n" data-id="${c.id}"${ce}>${n}</sup>`;
    if (c.kind === 'grammar')
      return `<del class="rl ed" data-id="${c.id}"${ce}>${oldHtml}</del><ins class="rl ed" data-id="${c.id}"${ce}>${c.replace}</ins><sup class="rl-n" data-id="${c.id}"${ce}>${n}</sup>`;
    const cls = c.dest_file ? 'mv' : 'rm';
    return `<del class="rl ${cls}" data-id="${c.id}"${ce}>${oldHtml}</del>` +
           (c.bridge_before ? `<ins class="rl ${cls}" data-id="${c.id}"${ce}>${esc(c.bridge_before)}</ins>` : '') +
           `<sup class="rl-n" data-id="${c.id}"${ce}>${n}</sup>`;
  }
  const byId = id => state.changes.find(c => c.id === id);
  const inDom = id => !!ed().querySelector(`[data-id="${id}"]`);
  function unwrap(el) { const p = el.parentNode; while (el.firstChild) p.insertBefore(el.firstChild, el); p.removeChild(el); }
  function pending() { return state.changes.filter(c => state.st[c.id] === undefined && inDom(c.id)); }

  function decide(id, accept, silent) {
    const c = byId(id); if (!c) return;
    const marks = [...ed().querySelectorAll(`[data-id="${id}"]`)];
    if (!marks.length) { state.st[id] = accept; post(id, accept); if (!silent) renderAll(); return; }
    if (H()) H().checkpoint();
    const holder = marks[0].closest('p,li,div.copy,div');
    for (const m of marks) {
      if (m.tagName === 'SUP') { m.remove(); continue; }
      if (m.tagName === 'DEL') { accept ? m.remove() : unwrap(m); }
      else if (m.tagName === 'INS') { accept ? unwrap(m) : m.remove(); }
    }
    if (accept && holder && holder.isConnected && !holder.textContent.trim() && !holder.querySelector('img,.img-slot-empty,picture')) holder.remove();
    state.st[id] = accept; state.last[id] = accept;
    const posted = { [id]: accept };
    if (c.inner && c.inner.length) {
      if (accept) c.inner.forEach(i => { state.st[i] = true; state.last[i] = true; posted[i] = true; });
      else reinject(c.inner);
    }
    post(posted);
    if (H()) H().touch(); dirty();
    previewDecision(null);
    if (!silent) { renderAll(); advanceFrom(id); }
  }
  function reinject(ids) {                   // a rejected cut keeps its sentence, so its grammar fixes are offered again
    const e = ed(); let html = e.innerHTML;
    for (const id of ids) {
      const c = byId(id); const key = locate(html, c.find); if (!key) continue;
      html = html.replace(key, markHtml(c, key, c.n));
    }
    e.innerHTML = html; if (window.restoreSlotButtons) window.restoreSlotButtons(e);
  }
  function decideAll(accept) {
    const list = pending(); if (!list.length) return;
    if (!confirm(`${accept ? 'Accept' : 'Reject'} all ${list.length} remaining changes?`)) return;
    if (H()) H().checkpoint();
    list.forEach(c => decide(c.id, accept, true));
    renderAll(); toast(`${accept ? 'Accepted' : 'Rejected'} ${list.length} changes.`);
  }
  async function post(map, accept) {
    if (!state.slug) return;
    const body = typeof map === 'string' ? { [map]: accept } : map;
    try { await fetch(API + `/review/${state.slug}/decisions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); }
    catch (e) { toast('Could not save the decision (server?)'); }
  }
  function syncFromDom() {                   // after undo/redo the DOM is the truth
    const changed = {};
    for (const c of state.changes) {
      const present = inDom(c.id);
      if (present && state.st[c.id] !== undefined) { state.st[c.id] = undefined; changed[c.id] = null; }
      else if (!present && state.st[c.id] === undefined && state.last[c.id] !== undefined) { state.st[c.id] = state.last[c.id]; changed[c.id] = state.last[c.id]; }
    }
    if (Object.keys(changed).length) post(changed);
    anchorComments(); renderAll();
  }
  function jump(id, fromDoc) {
    state.active = id;
    ed().querySelectorAll('.rl-hit').forEach(x => x.classList.remove('rl-hit'));
    const marks = [...ed().querySelectorAll(`[data-id="${id}"]`)];
    marks.forEach(m => (m.closest('p,li,div.copy') || m).classList.add('rl-hit'));
    if (marks[0] && !fromDoc) marks[0].scrollIntoView({ block: 'center', behavior: 'auto' });
    renderAll();
    const card = document.querySelector(`.rv-card[data-id="${id}"]`); if (card && state.view === 'list') card.scrollIntoView({ block: 'nearest' });
  }
  function step(dir) {
    const order = pending().map(c => c.id); if (!order.length) return;
    const i = order.indexOf(state.active); jump(order[(i + dir + order.length) % order.length]);
  }
  function advanceFrom(id) {                 // Word moves to the next change after Accept / Reject
    const order = pending(); if (!order.length) { state.active = null; renderAll(); return; }
    const idx = state.changes.findIndex(c => c.id === id);
    const next = order.find(c => state.changes.indexOf(c) > idx) || order[0];
    jump(next.id);
  }

  // ------------------------------------------------------------------ save
  function prepareForSave(saveEl) {
    saveEl.querySelectorAll('sup.rl-n').forEach(x => x.remove());
    saveEl.querySelectorAll('ins.rl').forEach(x => x.remove());          // undecided insertions do not ship
    saveEl.querySelectorAll('del.rl').forEach(unwrap);                   // undecided deletions stay as original text
    saveEl.querySelectorAll('mark.cm').forEach(unwrap);                  // comment anchors never ship
  }
  async function onSaved() {
    if (state.rel) await snapshotDisk(state.rel);    // our own write is not a change to warn about
    if (!state.slug) return;
    const accepted = Object.keys(state.st).filter(id => state.st[id] === true);
    if (!accepted.length) return;
    try {
      const r = await (await fetch(API + `/review/${state.slug}/commit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ accepted }) })).json();
      if (r.ok) {
        toast(`Saved. ${r.applied} accepted change(s) committed` + (r.destination_writes ? `, ${r.destination_writes} sentence(s) written into supporting articles.` : '.'), 6000);
        state.changes = state.changes.filter(c => !accepted.includes(c.id)); accepted.forEach(id => delete state.st[id]);
        if (r.complete) { state.slug = null; state.changes = []; toast('Review complete: every change is decided and applied.', 6000); }
        renderAll();
      } else toast('Saved the article, but the supporting-article writes failed: ' + (r.problems || []).join(' | '), 12000);
    } catch (e) { toast('Saved the article, but could not reach the server to commit the moves.', 8000); }
  }

  // ------------------------------------------------------------------ comments
  let _range = null;
  function captureSelection() { const s = window.getSelection(); if (s && s.rangeCount && !s.isCollapsed && ed().contains(s.anchorNode)) _range = s.getRangeAt(0).cloneRange(); else _range = null; }
  function textAround(range) {
    const quote = range.toString();
    const pre = range.cloneRange(); pre.collapse(true); pre.setStart(ed(), 0);
    const post = range.cloneRange(); post.collapse(false); post.setEnd(ed(), ed().childNodes.length);
    return { quote, before: pre.toString().slice(-40), after: post.toString().slice(0, 40) };
  }
  function newComment() {
    if (state.draft) { toast('Another comment is in progress.'); return; }
    if (!_range || !_range.toString().trim()) { toast('Select some text to comment on first.'); return; }
    if (state.filter === 'changes') setFilter('all'); toggle(true);
    state.draft = { range: _range, anchor: textAround(_range), id: 'c' + Date.now().toString(36) };
    // Word highlights the anchor while the draft is open
    wrapRange(_range, state.draft.id, true);
    renderAll();
    setTimeout(() => { const ta = document.querySelector('.rv-cm.draft textarea'); if (ta) ta.focus(); }, 0);
  }
  function wrapRange(range, id, draft) {   // one <mark> per text node, so the article's own structure is never touched
    const walker = document.createTreeWalker(ed(), NodeFilter.SHOW_TEXT);
    const nodes = []; let n, first = null;
    while ((n = walker.nextNode())) if (range.intersectsNode(n) && n.nodeValue.trim() && !n.parentElement.closest('sup.rl-n')) nodes.push(n);
    for (let t of nodes) {
      const s0 = t === range.startContainer ? range.startOffset : 0, e0 = t === range.endContainer ? range.endOffset : t.nodeValue.length;
      if (e0 <= s0) continue;
      if (e0 < t.nodeValue.length) t.splitText(e0);
      if (s0 > 0) t = t.splitText(s0);
      const m = document.createElement('mark'); m.className = 'cm' + (draft ? ' draft' : ''); m.dataset.cm = id;
      t.parentNode.insertBefore(m, t); m.appendChild(t); first = first || m;
    }
    return first;
  }
  function unwrapMark(id) { ed().querySelectorAll(`mark.cm[data-cm="${id}"]`).forEach(unwrap); }
  function postDraft(text) {
    const d = state.draft; if (!d || !text.trim()) return;
    const m = ed().querySelector(`mark.cm[data-cm="${d.id}"]`); if (m) m.classList.remove('draft');
    state.comments.threads.push({ id: d.id, author: AUTHOR, text: text.trim(), created: new Date().toISOString(), anchor: d.anchor, resolved: false, replies: [] });
    state.draft = null; _range = null; state.active = d.id;
    saveComments(); renderAll();
  }
  function cancelDraft() { if (!state.draft) return; unwrapMark(state.draft.id); state.draft = null; renderAll(); }
  async function saveComments() {
    if (!state.key) return;
    try { await fetch(API + `/comments/${state.key}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.comments) }); }
    catch (e) { toast('Could not save comments (server?)'); }
  }
  function anchorComments() {               // find each unresolved thread's quote in the text and highlight it
    ed().querySelectorAll('mark.cm:not(.draft)').forEach(unwrap);
    for (const t of state.comments.threads) {
      t.unanchored = false; if (t.resolved) continue;
      const r = findRange(t.anchor); if (r) wrapRange(r, t.id, false); else t.unanchored = true;
    }
  }
  function findRange(anchor) {
    const walker = document.createTreeWalker(ed(), NodeFilter.SHOW_TEXT);
    const nodes = []; let text = ''; let n;
    while ((n = walker.nextNode())) { if (n.parentElement.closest('sup.rl-n,ins.rl')) continue; nodes.push([n, text.length]); text += n.nodeValue; }
    let i = -1;
    const withCtx = (anchor.before || '').slice(-12) + anchor.quote;
    const j = text.indexOf(withCtx); if (j >= 0) i = j + (anchor.before || '').slice(-12).length;
    if (i < 0) i = text.indexOf(anchor.quote);
    if (i < 0) return null;
    const e = i + anchor.quote.length;
    const at = off => { for (let k = nodes.length - 1; k >= 0; k--) if (nodes[k][1] <= off) return [nodes[k][0], off - nodes[k][1]]; return [nodes[0][0], 0]; };
    const [sn, so] = at(i), [en, eo] = at(e);
    const r = document.createRange(); r.setStart(sn, so); r.setEnd(en, eo); return r;
  }
  function focusThread(id, fromDoc) {
    state.active = id; if (state.filter === 'changes') setFilter('all'); toggle(true);
    ed().querySelectorAll('mark.cm.cm-hit').forEach(x => x.classList.remove('cm-hit'));
    const m = ed().querySelector(`mark.cm[data-cm="${id}"]`); if (m) { m.classList.add('cm-hit'); if (!fromDoc) m.scrollIntoView({ block: 'center', behavior: 'auto' }); }
    renderAll();
  }
  function thread(id) { return state.comments.threads.find(t => t.id === id); }
  function resolve(id, on) { const t = thread(id); if (!t) return; t.resolved = on; if (on) unwrapMark(id); else { const r = findRange(t.anchor); if (r) wrapRange(r, id, false); else t.unanchored = true; } saveComments(); renderAll(); }
  function delThread(id) { if (!confirm('Delete this comment thread?')) return; state.comments.threads = state.comments.threads.filter(t => t.id !== id); unwrapMark(id); saveComments(); renderAll(); }
  function reply(id, text) { const t = thread(id); if (!t || !text.trim()) return; t.replies.push({ id: 'r' + Date.now().toString(36), author: AUTHOR, text: text.trim(), created: new Date().toISOString() }); saveComments(); renderAll(); }
  function editText(id, rid, text) { const t = thread(id); if (!t) return; if (rid) { const r = t.replies.find(x => x.id === rid); if (r && r.author === AUTHOR) { r.text = text.trim(); r.edited = new Date().toISOString(); } } else if (t.author === AUTHOR) { t.text = text.trim(); t.edited = new Date().toISOString(); } saveComments(); renderAll(); }
  function editInPlace(tx, text, save) {    // swap the comment text for a box, in the card, no dialog
    if (!tx || tx.querySelector('textarea')) return;
    const ta = document.createElement('textarea'); ta.value = text; ta.className = 'edit';
    const act = document.createElement('div'); act.className = 'act';
    act.innerHTML = '<button class="primary" data-a="save" title="Save (Ctrl+Enter)">Save</button><button data-a="cancel" title="Esc">Cancel</button>';
    const old = tx.textContent; tx.textContent = ''; tx.appendChild(ta); tx.appendChild(act);
    const done = () => { tx.textContent = old; queueLayout(); };
    act.querySelector('[data-a="save"]').onclick = e => { e.stopPropagation(); if (ta.value.trim()) save(ta.value); else done(); };
    act.querySelector('[data-a="cancel"]').onclick = e => { e.stopPropagation(); done(); };
    ta.onkeydown = e => { if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); if (ta.value.trim()) save(ta.value); } if (e.key === 'Escape') done(); };
    ta.oninput = () => queueLayout();
    ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); queueLayout();
  }
  function when(iso) { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); }

  // ---------------------------------------------------------------- pairing
  // A comment written ABOUT a change is the same conversation as the change, so
  // it rides inside that change's card instead of sitting in a second card
  // further down the margin saying the same thing twice. "Related" means the
  // two actually overlap in the text, not merely share a paragraph, so the test
  // is a range intersection and never a guess.
  function spanOf(sel) {
    const els = [...ed().querySelectorAll(sel)];
    if (!els.length) return null;
    const r = document.createRange();
    try { r.setStartBefore(els[0]); r.setEndAfter(els[els.length - 1]); } catch (e) { return null; }
    return r;
  }
  function hits(a, b) {
    try { return a.compareBoundaryPoints(Range.END_TO_START, b) < 0
              && a.compareBoundaryPoints(Range.START_TO_END, b) > 0; } catch (e) { return false; }
  }
  function pairUp(on) {
    const map = new Map(), taken = new Set();
    const open = on ? state.comments.threads.filter(t => !t.resolved) : [];
    if (!open.length) return { map, taken };
    const spans = pending().map(c => ({ id: c.id, r: spanOf(`[data-id="${c.id}"]`) })).filter(x => x.r);
    if (!spans.length) return { map, taken };
    for (const t of open) {
      const tr = spanOf(`mark.cm[data-cm="${t.id}"]`);
      if (!tr) continue;
      const hit = spans.find(x => hits(x.r, tr));
      if (!hit) continue;
      if (!map.has(hit.id)) map.set(hit.id, []);
      map.get(hit.id).push(t);
      taken.add(t.id);
    }
    return { map, taken };
  }

  // ------------------------------------------------------------------ render
  // One margin, as in Word's contextual view: change cards and comment cards in document order,
  // each level with the text it belongs to. List is Word's Comments pane: everything, scrolling.
  function items() {
    const out = [];
    const showCh = state.filter !== 'comments' && !ed().classList.contains('rl-final');
    const showCm = state.filter !== 'changes';
    // only merge when the margin is showing both; filtering to one or the other
    // is a request to see that one on its own
    const { map: paired, taken } = pairUp(showCh && showCm);
    if (showCh) for (const c of pending()) out.push({ kind: 'change', id: c.id, c, threads: paired.get(c.id), el: ed().querySelector(`[data-id="${c.id}"]`) });
    if (showCm) {
      if (state.draft) out.push({ kind: 'draft', id: state.draft.id, el: ed().querySelector(`mark.cm[data-cm="${state.draft.id}"]`) });
      for (const t of state.comments.threads) if ((!t.resolved || (state.view === 'list' && state.showResolved)) && !taken.has(t.id)) out.push({ kind: 'comment', id: t.id, t, el: ed().querySelector(`mark.cm[data-cm="${t.id}"]`) });
    }
    if (showCh && state.view === 'list') for (const id of state.hidden) if (state.st[id] === undefined) out.push({ kind: 'change', id, c: byId(id), dim: true, el: null });
    out.sort((a, b) => { if (!a.el && !b.el) return 0; if (!a.el) return 1; if (!b.el) return -1;
      return (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1; });
    return out;
  }
  function renderAll() {
    if (!$('review-pane')) return;
    const pend = pending(), open = state.comments.threads.filter(t => !t.resolved).length + (state.draft ? 1 : 0);
    $('rv-n-ch').textContent = pend.length; $('rv-n-cm').textContent = open;
    const nres = state.comments.threads.filter(t => t.resolved).length; $('rv-n-res').textContent = nres; $('rv-resolved').style.display = state.view === 'list' && nres ? '' : 'none';
    const hb = $('btn-review'); if (hb) hb.textContent = pend.length + open ? `Review ${pend.length + open}` : 'Review';
    const cut = state.changes.filter(c => c.kind === 'cut' && state.st[c.id] === true).reduce((s, c) => s + (c.words || 0), 0);
    const yes = Object.values(state.st).filter(v => v === true).length, no = Object.values(state.st).filter(v => v === false).length;
    const words = state.meta && state.meta.before ? ` · ${state.meta.before - cut} words (${state.meta.tlabel} ${state.meta.target})` : '';
    $('rv-status').textContent = state.slug ? `${pend.length} to review · ${yes} accepted · ${no} rejected${words}`
                               : (state.rel ? `${open} open comment${open === 1 ? '' : 's'} · no proposed changes` : 'Open an article to review it.');
    const list = $('rv-list'), canvas = $('rv-canvas');
    list.innerHTML = ''; canvas.innerHTML = '';
    const listMode = state.view === 'list' || state.awaitingFile;
    list.style.display = listMode ? '' : 'none'; canvas.style.display = listMode ? 'none' : '';
    $('rv-body').classList.toggle('list', listMode);
    const host = listMode ? list : canvas;
    if (state.awaitingFile) {
      const file = (state.rel || '').split('/').pop(), n = (state.previewChanges || []).length;
      const p = document.createElement('div'); p.className = 'rv-card rv-cm other wait';
      p.innerHTML = `<div class="who"><span class="av">!</span><b>Waiting for the article</b></div><div class="tx">These comments${n ? ` and ${n} proposed changes` : ''} belong to <b>${esc(file)}</b>. Click <b>Open Article</b> and pick that file to see them beside the text and accept or reject in place.</div>`;
      host.appendChild(p);
    }
    const its = items(); let lost = 0;
    for (const it of its) {
      if (!listMode && !it.el) { lost++; continue; }                  // the margin holds only what has a place in the text
      host.appendChild(it.kind === 'change' ? changeCard(it.c, it.dim, it.threads) : it.kind === 'draft' ? card({ id: it.id, draft: true }) : card(it.t));
    }
    if (lost) { const n = document.createElement('div'); n.className = 'rv-note'; n.textContent = `${lost} more not found in the open text · see List`; host.appendChild(n); }
    if (!host.children.length) {
      const e = document.createElement('div'); e.className = 'rv-empty';
      e.textContent = state.slug && !pend.length && state.filter !== 'comments' ? 'Every change is decided. Save the article to commit them.'
                    : state.filter === 'changes' ? 'No changes to review.' : 'Select text and choose + Comment (Ctrl+Alt+M).';
      host.appendChild(e);
    }
    layout();
  }
  // Word's Track Changes card: who, when, what, and a tick or a cross that previews its result
  function changeCard(c, dim, threads) {
    const cls = c.kind === 'cut' ? (c.dest_file ? 'mv' : 'rm') : 'ed';
    const label = c.kind === 'grammar' ? 'Replaced' : c.kind === 'insert' ? 'Inserted' : (c.dest_file ? 'Moved' : 'Deleted');
    const d = document.createElement('div');
    d.className = 'rv-card rv-c ' + cls + (state.active === c.id ? ' active' : '') + (dim ? ' dim' : ''); d.dataset.id = c.id;
    const body = c.kind === 'grammar' ? `<del>${esc(plain(c.find))}</del> <ins>${esc(plain(c.replace))}</ins>`
               : c.kind === 'insert' ? `<ins>${esc(plain(c.html)).slice(0, 600)}${plain(c.html).length > 600 ? '…' : ''}</ins>` : `<del>${esc(plain(c.sentence))}</del>`;
    let meta = '';
    if (c.kind === 'cut') {
      if (c.dest_file) meta += `<div class="m">Moves to <b>${esc(c.dest_file)}</b>${c.dest_replaces ? ` and replaces there: <span class="lose">&ldquo;${esc(plain(c.dest_replaces))}&rdquo;</span>` : ' as a new sentence'}</div>`;
      else if (c.dup_scope === 'itinerary') meta += `<div class="m">Removed only. The article already says this elsewhere.</div>`;
      else if (c.covered_by) meta += `<div class="m">Removed only. A supporting article covers it: &ldquo;${esc(plain(c.covered_by))}&rdquo;</div>`;
      else meta += `<div class="m">Removed only.</div>`;
    }
    if (c.inner && c.inner.length) meta += `<div class="m">Carries grammar fixes ${c.inner.map(i => byId(i).n).join(', ')}.</div>`;
    if (c.note) meta += `<div class="m">${esc(c.note)}</div>`;
    const tm = state.meta && state.meta.created ? when(state.meta.created) : '';
    d.innerHTML = `<div class="who"><span class="av">${REVIEWER[0]}</span><b>${REVIEWER}</b><span class="tm">${tm}</span>
        <span class="acts"><button class="yes" data-v="1" title="Accept this change (hover to preview)">&#10003;</button><button class="no" data-v="0" title="Reject this change (hover to preview)">&#10005;</button></span></div>
      <div class="lbl"><span class="num">${c.n}</span>${label}${c.section ? ' · ' + esc(c.section) : ''}${c.words ? ' · ' + c.words + ' words' : ''}${dim ? ' · <em>not found in the open text</em>' : ''}</div>
      <div class="t">${body}</div>${meta}`;
    d.onclick = e => { if (!e.target.closest('button')) jump(c.id); };
    d.querySelectorAll('.acts button').forEach(b => {
      b.onclick = ev => { ev.stopPropagation(); decide(c.id, b.dataset.v === '1'); };
      b.onmouseenter = () => previewDecision(c.id, b.dataset.v === '1');
      b.onmouseleave = () => previewDecision(null);
    });
    d.onmouseenter = () => { state.hover = c.id; hoverDoc(c.id, true); drawLines(); };
    d.onmouseleave = () => { state.hover = null; hoverDoc(c.id, false); drawLines(); };
    if (threads && threads.length) {           // the conversation about this change, in the same card
      const sub = document.createElement('div');
      sub.className = 'rv-sub';
      sub.innerHTML = `<div class="lbl2">${threads.length === 1 ? 'Comment on this change' : threads.length + ' comments on this change'}</div>`;
      for (const t of threads) { const el = card(t); el.classList.add('nested'); sub.appendChild(el); }
      d.appendChild(sub);
    }
    return d;
  }
  function previewDecision(id, accept) {       // while the tick or cross is hovered the text shows the outcome
    ed().querySelectorAll('.rl-pv-yes,.rl-pv-no').forEach(m => m.classList.remove('rl-pv-yes', 'rl-pv-no'));
    if (id) ed().querySelectorAll(`[data-id="${id}"]`).forEach(m => m.classList.add(accept ? 'rl-pv-yes' : 'rl-pv-no'));
    // the preview reflows the text; the cards hold still so the pointer stays on the button
    state.previewing = !!id;
    if (id) drawLines(); else queueLayout();
  }
  function hoverDoc(id, on) { ed().querySelectorAll(`[data-id="${id}"]`).forEach(m => m.classList.toggle('rl-hover', on)); }
  function card(t) {                           // a comment thread, Word's modern comment card
    const d = document.createElement('div');
    d.className = 'rv-card rv-cm' + (t.draft ? ' draft' : '') + (t.resolved ? ' resolved' : '') + (state.active === t.id ? ' active' : '') + (t.author && t.author !== AUTHOR ? ' other' : '');
    d.dataset.cm = t.id;
    if (t.draft) {
      d.innerHTML = `<div class="who"><span class="av me">${AUTHOR[0]}</span><b>${esc(AUTHOR)}</b><span class="tm">draft</span></div><textarea placeholder="Start the conversation (Ctrl+Enter to post)"></textarea>
        <div class="act"><button class="primary" data-a="post" title="Post (Ctrl+Enter)">Post</button><button data-a="cancel" title="Discard (Esc)">Cancel</button></div>`;
      d.querySelector('[data-a="post"]').onclick = () => postDraft(d.querySelector('textarea').value);
      d.querySelector('[data-a="cancel"]').onclick = cancelDraft;
      d.querySelector('textarea').onkeydown = e => { if (e.ctrlKey && e.key === 'Enter') postDraft(e.target.value); if (e.key === 'Escape') cancelDraft(); };
      return d;
    }
    const av = a => `<span class="av${a === AUTHOR ? ' me' : ''}">${esc((a || '?')[0])}</span>`;
    const replies = (t.replies || []).map(r => `<div class="rp" data-rid="${r.id}"><div class="who">${av(r.author)}<b>${esc(r.author)}</b><span class="tm">${when(r.created)}${r.edited ? ' · edited' : ''}</span>${r.author === AUTHOR ? '<button class="ico" data-a="edit-r" title="Edit">&#9998;</button>' : ''}</div><div class="tx">${esc(r.text)}</div></div>`).join('');
    d.innerHTML = `<div class="who">${av(t.author)}<b>${esc(t.author)}</b><span class="tm">${when(t.created)}${t.edited ? ' · edited' : ''}</span>
        <span class="acts">${t.resolved ? '' : '<button class="yes" data-a="resolve" title="Resolve thread">&#10003;</button>'}
          <span class="menu"><button class="ico" data-a="more" title="More thread actions">&#8943;</button>
          <div class="dd">${t.resolved ? '<button data-a="reopen">Reopen</button>' : '<button data-a="resolve2">Resolve thread</button>'}${t.author === AUTHOR ? '<button data-a="edit">Edit</button>' : ''}<button data-a="del">Delete thread</button></div></span></span></div>
      ${t.unanchored && !state.awaitingFile ? '<div class="lbl"><em>anchored text no longer in the article</em></div>' : ''}
      <div class="tx">${esc(t.text)}</div>${replies}
      ${t.resolved ? '<div class="res">Resolved</div>' : `<div class="reply"><textarea placeholder="Reply (Ctrl+Enter)"></textarea><button data-a="reply" title="Post reply (Ctrl+Enter)">&#10148;</button></div>`}`;
    d.onclick = e => { if (!e.target.closest('button,textarea,.dd')) focusThread(t.id, false); };
    d.querySelector('[data-a="more"]').onclick = e => { e.stopPropagation(); d.querySelector('.dd').classList.toggle('open'); };
    const on = (a, f) => { const b = d.querySelector(`[data-a="${a}"]`); if (b) b.onclick = e => { e.stopPropagation(); f(); }; };
    on('resolve', () => resolve(t.id, true)); on('resolve2', () => resolve(t.id, true)); on('reopen', () => resolve(t.id, false)); on('del', () => delThread(t.id));
    on('edit', () => editInPlace(d.querySelector(':scope > .tx'), t.text, nt => editText(t.id, null, nt)));
    d.querySelectorAll('[data-a="edit-r"]').forEach(b => b.onclick = e => { e.stopPropagation(); const rp = b.closest('.rp'); const r = t.replies.find(x => x.id === rp.dataset.rid); editInPlace(rp.querySelector('.tx'), r.text, nt => editText(t.id, r.id, nt)); });
    const ta = d.querySelector('.reply textarea');
    if (ta) { on('reply', () => reply(t.id, ta.value)); ta.onkeydown = e => { if (e.ctrlKey && e.key === 'Enter') reply(t.id, ta.value); }; ta.oninput = () => queueLayout(); }
    d.onmouseenter = () => { state.hover = t.id; const m = ed().querySelector(`mark.cm[data-cm="${t.id}"]`); if (m) m.classList.add('cm-hover'); drawLines(); };
    d.onmouseleave = () => { state.hover = null; ed().querySelectorAll('mark.cm.cm-hover').forEach(m => m.classList.remove('cm-hover')); drawLines(); };
    return d;
  }
  // ------------------------------------------------------------------ margin geometry
  const anchorOf = card => card.dataset.id ? ed().querySelector(`[data-id="${card.dataset.id}"]`)
                         : card.dataset.cm ? ed().querySelector(`mark.cm[data-cm="${card.dataset.cm}"]`) : null;
  function anchorRect(card) {                // first on-page box of the anchor; a hover preview may hide one of its marks
    const sel = card.dataset.id ? `[data-id="${card.dataset.id}"]` : `mark.cm[data-cm="${card.dataset.cm}"]`;
    for (const el of ed().querySelectorAll(sel)) { const r = el.getClientRects()[0]; if (r) return r; }
    return null;
  }
  function queueLayout() { if (state._raf) return; state._raf = requestAnimationFrame(() => { state._raf = 0; layout(); }); }
  function layout() {                          // each card level with its text; the active one exactly, the rest make room
    const canvas = $('rv-canvas'); if (!canvas) return;
    if (!document.body.classList.contains('review-open') || canvas.style.display === 'none' || state.previewing) { drawLines(); return; }
    if ($('rv-body').scrollTop) $('rv-body').scrollTop = 0;
    const top0 = $('rv-body').getBoundingClientRect().top;
    const rows = [];
    for (const c of canvas.querySelectorAll(':scope > .rv-card')) {      // a thread riding inside a change card is not placed on its own
      const r = anchorRect(c); if (!r) { c.style.display = 'none'; continue; }
      c.style.display = '';
      rows.push({ c, y: r.top - top0 - 6, h: c.offsetHeight });
    }
    if (!rows.length) { drawLines(); return; }
    rows.sort((a, b) => a.y - b.y);
    let ai = rows.findIndex(r => (r.c.dataset.id || r.c.dataset.cm) === state.active); if (ai < 0) ai = 0;
    const GAP = 8, pos = new Array(rows.length);
    pos[ai] = rows[ai].y;
    let floor = pos[ai] + rows[ai].h + GAP;
    for (let i = ai + 1; i < rows.length; i++) { pos[i] = Math.max(rows[i].y, floor); floor = pos[i] + rows[i].h + GAP; }
    let ceil = pos[ai] - GAP;
    for (let i = ai - 1; i >= 0; i--) { pos[i] = Math.min(rows[i].y, ceil - rows[i].h); ceil = pos[i] - GAP; }
    rows.forEach((r, i) => { r.c.style.top = pos[i] + 'px'; });
    drawLines(); clearTimeout(state._ld); state._ld = setTimeout(drawLines, 200);   // again once the cards have slid
  }
  function drawLines() {                       // classic Word balloons: a line from each card to the text it is about
    const svg = $('rv-lines'); if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const canvas = $('rv-canvas');
    if (!document.body.classList.contains('review-open') || !canvas || canvas.style.display === 'none') return;
    const br = $('rv-body').getBoundingClientRect(), sc = document.querySelector('.editor-scroll').getBoundingClientRect();
    const er = ed().getBoundingClientRect();
    const top = Math.max(br.top, sc.top);
    svg.style.clipPath = `inset(${top}px 0 0 0)`;
    const NS = 'http://www.w3.org/2000/svg', xg = Math.max(sc.left + 14, er.left - 26);   // the gutter the lines run along
    for (const card of canvas.querySelectorAll(':scope > .rv-card')) {
      if (card.style.display === 'none') continue;
      const cr = card.getBoundingClientRect(); if (cr.bottom < br.top || cr.top > br.bottom) continue;
      const ar = anchorRect(card); if (!ar || ar.bottom < sc.top || ar.top > sc.bottom) continue;
      const id = card.dataset.id || card.dataset.cm, on = state.active === id || state.hover === id;
      const x0 = cr.right, y0 = cr.top + 15, x1 = ar.left - 3, y1 = ar.top + ar.height / 2;
      const kind = card.classList.contains('rv-cm') ? 'cm' : card.classList.contains('mv') ? 'mv' : card.classList.contains('ed') ? 'ed' : 'rm';
      const p = document.createElementNS(NS, 'path');
      p.setAttribute('d', `M${x0},${y0} L${xg},${y1} H${x1}`);
      p.setAttribute('class', `ln ${kind}${on ? ' on' : ''}`);
      svg.appendChild(p);
      const dot = document.createElementNS(NS, 'circle');
      dot.setAttribute('cx', x1); dot.setAttribute('cy', y1); dot.setAttribute('r', on ? 3 : 2); dot.setAttribute('class', `dt ${kind}${on ? ' on' : ''}`);
      svg.appendChild(dot);
    }
  }

  // ------------------------------------------------------------------ wiring
  document.addEventListener('DOMContentLoaded', buildPane);
  if (document.readyState !== 'loading') buildPane();
  // The article's repo path. The connected site folder knows it for sure, but that folder is
  // usually NOT connected (it exists for images), which is why the first version found nothing
  // to review. Without it, the article's own canonical link names country and file, and the
  // stylesheet depth says whether it is live (1 up), a Drafts page (2) or a full-article draft (3).
  async function resolveRel() {
    if (state.relOverride) return state.relOverride;
    if (window.articleRelPath) { try { const r = await window.articleRelPath(); if (r) return r; } catch (e) {} }
    const head = window.articleHead ? window.articleHead() : '';
    const m = /<link[^>]+rel="canonical"[^>]+href="https?:\/\/[^\/"]+\/([a-z0-9-]+)\/([a-z0-9-]+\.html)"/i.exec(head)
           || /property="og:url"[^>]+content="https?:\/\/[^\/"]+\/([a-z0-9-]+)\/([a-z0-9-]+\.html)"/i.exec(head);
    if (!m) return null;
    const depth = window.articleDepth ? await window.articleDepth() : 1;
    const prefix = depth >= 3 ? 'Drafts/.Full Articles/' : depth === 2 ? 'Drafts/' : '';
    return prefix + m[1] + '/' + m[2];
  }
  document.addEventListener('article-loaded', async () => {
    const rel = await resolveRel();
    if (rel) boot(rel); else { state.rel = null; renderAll(); toast('Could not tell which article this is, so there is nothing to review.', 5000); }
  });

  // ---- opened from a link: /browser#review=<repo path> ------------------------------------
  // Claude opens the editor here after a pass. The File System Access API cannot open a file
  // without a click, so the pane shows the comments and changes straight away as a list and
  // asks for one click on Open Article; once that article is opened everything anchors in place.
  async function preview(rel) {
    state.rel = rel; state.awaitingFile = true; state.changes = []; state.st = {};
    let r; try { r = await (await fetch(API + '/review/for?rel=' + encodeURIComponent(rel), { cache: 'no-store' })).json(); } catch (e) { return; }
    state.slug = r.slug; state.key = r.comments_key; state.comments = r.comments || { threads: [] };
    state.meta = { title: r.title, before: r.words_before, target: r.words_target, tlabel: r.target_label };
    state.previewChanges = r.changes || [];
    toggle(true); setFilter('all'); setView('list');
    toast(`${(r.comments.threads || []).filter(t => !t.resolved).length} comments and ${(r.changes || []).length} changes waiting. Open the article to review them in place.`, 7000);
  }
  // a query parameter, not the hash: the editor's own view router owns location.hash, and an
  // unknown hash value there left the workspace unbuilt, so the pane had nowhere to go
  function hashOpen() { const rel = new URLSearchParams(location.search).get('review'); if (rel) preview(rel); }

  // ---- changed on disk -------------------------------------------------------------------
  // Claude's commit writes supporting articles, and this article may be open in another window.
  // The editor keeps what it loaded, so a save from a stale copy would overwrite that write.
  async function fetchDisk(rel) {
    try { const r = await fetch(API + '/site/' + rel.split('/').map(encodeURIComponent).join('/'), { cache: 'no-store' }); return r.ok ? await r.text() : null; }
    catch (e) { return null; }
  }
  async function snapshotDisk(rel) { state.disk = await fetchDisk(rel); state.diskWarned = false; }
  async function checkDisk() {
    if (!state.rel || state.awaitingFile || state.disk == null) return;
    const now = await fetchDisk(state.rel);
    if (now == null || now === state.disk || state.diskWarned) return;
    state.diskWarned = true;
    const b = $('rv-banner'); b.innerHTML = 'This article changed on disk since you opened it, from a commit in another window or another editor. ' +
      'Reopen it before saving or your save will overwrite that. <button id="rv-banner-x">Dismiss</button>';
    b.style.display = 'block'; $('rv-banner-x').onclick = () => { b.style.display = 'none'; };
    toggle(true);
  }
  window.addEventListener('focus', checkDisk);
  setInterval(checkDisk, 30000);

  // ---- refresh: new comments or changes that arrived while the article was open -----------
  async function refresh() {
    if (!state.rel) return;
    let r; try { r = await (await fetch(API + '/review/for?rel=' + encodeURIComponent(state.rel), { cache: 'no-store' })).json(); } catch (e) { toast('Server not reachable'); return; }
    state.comments = r.comments || { threads: [] }; anchorComments();
    const known = new Set(state.changes.map(c => c.id));
    const fresh = (r.changes || []).filter(c => !known.has(c.id));
    // every kind gets marked, through the same path boot() uses: reinject() only knows
    // grammar finds, so a fresh cut, move or insert used to land in state with no mark
    // and no place in the margin, undecidable until the file was reopened
    if (fresh.length) { state.slug = r.slug; state.changes.push(...fresh); injectMarks(fresh); if (window.review && review.syncFromDom) review.syncFromDom(); }
    renderAll();
    toast(fresh.length ? `${fresh.length} new change(s) added.` : 'Up to date.');
  }
  window.review = {
    toggle, prepareForSave, onSaved, syncFromDom, decide, jump, newComment, state, refresh, preview, checkDisk, layout, previewDecision, setFilter, setView, trimTags,
    // test seam: open an article body under a given repo path without a folder handle
    loadFor(rel, html) { state.relOverride = rel; const e = ed(); e.style.display = ''; e.contentEditable = 'true'; e.innerHTML = html; return boot(rel); }
  };

  const css = document.createElement('style'); css.textContent = `
#review-pane{display:none;flex-direction:column;flex:1;min-height:0;background:#F7F7F4;font:14px/1.5 'Hanken Grotesk',Helvetica,Arial,sans-serif;color:#1C2821}
body.review-open #review-pane{display:flex}
.rv-head{display:flex;align-items:center;gap:.35rem;padding:.55rem .6rem;border-bottom:1px solid #e6e6e2;background:#fff}
.rv-chips{display:flex;gap:.2rem;flex:1}
.rv-chips button,#review-pane .rv-btn,#review-pane .rv-seg button,#review-pane .rv-dd button{font:600 .6rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.06em;text-transform:uppercase;padding:.42rem .55rem;border:1px solid #e0ded8;background:#fff;border-radius:3px;cursor:pointer;color:#1C2821;white-space:nowrap}
.rv-chips button{border-radius:999px;padding:.38rem .62rem}
.rv-chips button[aria-pressed="true"],#review-pane .rv-seg button[aria-pressed="true"],#review-pane #rv-resolved[aria-pressed="true"]{background:#1C2821;color:#fff;border-color:#1C2821}
.rv-chips button span{opacity:.6;margin-left:.2rem}
#review-pane .rv-ico{border:0;background:none;font-size:1.05rem;line-height:1;cursor:pointer;color:#6b7a70;padding:.1rem .3rem}
#review-pane .rv-ico:hover{color:#1C2821}
.rv-tools{display:flex;gap:.3rem;flex-wrap:wrap;align-items:center;padding:.45rem .6rem;border-bottom:1px solid #e6e6e2;background:#fff}
.rv-seg{display:inline-flex;border:1px solid #e0ded8;border-radius:3px;overflow:hidden}.rv-seg button{border:0!important;border-radius:0!important;border-right:1px solid #e0ded8!important}.rv-seg button:last-child{border-right:0!important}
#review-pane .primary{background:#2D6B50;color:#fff;border-color:#2D6B50}
.rv-more{margin-left:auto;position:relative}
.rv-dd{display:none;position:absolute;right:0;top:1.5rem;background:#fff;border:1px solid #e0ded8;border-radius:3px;box-shadow:0 4px 14px rgba(0,0,0,.08);z-index:6;min-width:170px}
.rv-more.open .rv-dd{display:block}.rv-dd button{display:block;width:100%;text-align:left;border:0!important;border-radius:0!important;text-transform:none!important;letter-spacing:0!important;font-size:.78rem!important;padding:.45rem .7rem!important}
.rv-dd button:hover{background:#F5F5F2}
.rv-status{font:600 .58rem/1.4 'DM Mono',monospace;letter-spacing:.06em;text-transform:uppercase;color:#6b7a70;padding:.4rem .6rem;border-bottom:1px solid #e6e6e2;background:#fff}
.rv-body{flex:1;overflow:hidden;position:relative}.rv-body.list{overflow:auto}.rv-list{padding:.5rem}.rv-canvas{position:relative;height:100%}
#rv-banner{background:#FBEFC2;color:#5c4a12;font-size:.78rem;padding:.5rem .6rem;border-bottom:1px solid #E8C86A}#rv-banner button{font:600 .6rem/1 'Hanken Grotesk',sans-serif;margin-left:.3rem;border:1px solid #E8C86A;background:#fff;border-radius:3px;padding:.25rem .4rem;cursor:pointer}
.rv-empty,.rv-note{font-size:.8rem;color:#8a9790;padding:.9rem .6rem}.rv-canvas .rv-note{position:absolute;left:0;right:0;bottom:0;background:#F7F7F4;border-top:1px solid #e6e6e2;font-size:.7rem;padding:.4rem .6rem}
/* cards: one shape for a change and a comment, Word's margin */
.rv-card{border:1px solid #e3e2dc;border-radius:6px;padding:.5rem .6rem .55rem;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.04);font-size:.82rem}
.rv-canvas .rv-card{position:absolute;left:.5rem;right:.6rem;transition:top .16s ease-out}
.rv-list .rv-card{margin:0 0 .45rem}
.rv-card:hover{border-color:#c5c9c0}.rv-card.active{border-color:#1C2821;box-shadow:0 0 0 2px rgba(28,40,33,.1)}
.rv-card.dim{opacity:.65}
.rv-card .who{display:flex;align-items:center;gap:.4rem;min-height:22px;font-size:.72rem;color:#6b7a70}.rv-card .who b{color:#1C2821;font-size:.78rem}.rv-card .who .tm{white-space:nowrap}
.rv-card .av{width:22px;height:22px;border-radius:50%;background:#9A7B2E;color:#fff;font:700 .68rem/22px 'Hanken Grotesk',sans-serif;text-align:center;flex-shrink:0}
.rv-card .av.me{background:#2D6B50}.rv-card.wait .av{background:#B4553C}
.rv-card .acts{margin-left:auto;display:flex;align-items:center;gap:.1rem}
.rv-card .acts .yes,.rv-card .acts .no{width:26px;height:26px;border:1px solid transparent;border-radius:50%;background:none;cursor:pointer;font-size:.9rem;line-height:1;color:#6b7a70;padding:0}
.rv-card .acts .yes:hover{background:#E3F0E8;color:#2D6B50;border-color:#b9d4c5}.rv-card .acts .no:hover{background:#F8E5E0;color:#B4553C;border-color:#e9c4ba}
.rv-c .lbl{display:flex;align-items:center;gap:.35rem;font:600 .56rem/1 'DM Mono',monospace;letter-spacing:.1em;text-transform:uppercase;color:#6b7a70;margin-top:.35rem}
.rv-c .lbl em{font-style:normal;color:#9A7B2E;text-transform:none;letter-spacing:0}
.rv-c .num{background:#8a9790;color:#fff;border-radius:8px;padding:.15rem .4rem;font-size:.56rem}
.rv-c.rm .num{background:#B4553C}.rv-c.ed .num{background:#2557A7}.rv-c.mv .num{background:#2D6B50}
.rv-c .t{margin:.3rem 0 0;line-height:1.45}.rv-c .t del{color:#B4553C;text-decoration:line-through}.rv-c.mv .t del{color:#2D6B50;text-decoration:line-through double;text-decoration-thickness:1px}.rv-c .t ins{color:#2557A7;text-decoration:underline}
.rv-c .m{font-size:.72rem;color:#6b7a70;margin-top:.3rem}.rv-c .m b{color:#2D6B50}.rv-c .m .lose{color:#B4553C}
.rv-cm .act{display:flex;gap:.3rem;margin-top:.45rem}
.rv-cm button{font:600 .6rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.06em;text-transform:uppercase;padding:.35rem .55rem;border:1px solid #e0ded8;background:#fff;border-radius:3px;cursor:pointer;color:#1C2821}
.rv-c .rv-sub{margin:.55rem 0 0;padding:.45rem 0 0;border-top:1px dashed #e0ded8}
.rv-c .rv-sub .lbl2{font:600 .56rem/1 'DM Mono',monospace;letter-spacing:.1em;text-transform:uppercase;color:#8a9790;margin-bottom:.3rem}
.rv-canvas .rv-card.nested,.rv-list .rv-card.nested{position:static;left:auto;right:auto;margin:.3rem 0 0;border:0;border-left:2px solid #d9d6cd;border-radius:0;box-shadow:none;padding:.1rem 0 .1rem .5rem;background:transparent;transition:none}
.rv-card.nested:hover{border-left-color:#9A7B2E}
.rv-card.nested.active{box-shadow:none;border-left-color:#1C2821}
.rv-cm.nested.other{border-left:2px solid #9A7B2E}
.rv-cm.draft{border-color:#2D6B50}.rv-cm.resolved{opacity:.6}.rv-cm.other{border-left:3px solid #9A7B2E}.rv-cm.wait{border-left-color:#B4553C}
.rv-cm .lbl{font-size:.7rem;color:#9A7B2E;margin-top:.2rem}
.rv-cm .menu{position:relative}.rv-cm .ico{border:0!important;background:none!important;font-size:1rem;padding:0 .2rem!important;color:#6b7a70}
.rv-cm .dd{display:none;position:absolute;right:0;top:1.4rem;background:#fff;border:1px solid #e0ded8;border-radius:3px;box-shadow:0 4px 14px rgba(0,0,0,.08);z-index:3;min-width:150px}
.rv-cm .dd.open{display:block}.rv-cm .dd button{display:block;width:100%;text-align:left;border:0!important;border-radius:0!important;text-transform:none!important;letter-spacing:0!important;font-size:.78rem!important;padding:.45rem .7rem!important}
.rv-cm .dd button:hover{background:#F5F5F2}
.rv-cm .tx{font-size:.84rem;margin:.3rem 0 0;white-space:pre-wrap}.rv-cm .rp{margin:.5rem 0 0 .4rem;padding-left:.55rem;border-left:2px solid #e6e6e2}
.rv-cm .rp .av{width:18px;height:18px;line-height:18px;font-size:.6rem}
.rv-cm textarea.edit{margin-top:0;min-height:64px}
.rv-cm textarea{width:100%;min-height:54px;font:13px/1.45 'Hanken Grotesk',sans-serif;padding:.4rem;border:1px solid #e0ded8;border-radius:3px;margin-top:.4rem;resize:vertical}
.rv-cm .reply{display:flex;gap:.3rem;align-items:flex-end;margin-top:.4rem}.rv-cm .reply textarea{min-height:32px;margin:0;flex:1}
.rv-cm .reply button{padding:.4rem .5rem;font-size:.8rem;color:#2D6B50}
.rv-cm .res{font:600 .58rem/1 'DM Mono',monospace;letter-spacing:.1em;text-transform:uppercase;color:#2D6B50;margin-top:.45rem}
#rv-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:10001;background:#1C2821;color:#fff;padding:.6rem 1rem;border-radius:4px;font:13px/1.4 'Hanken Grotesk',sans-serif;max-width:640px;display:none}
/* connector lines from card to text */
#rv-lines{position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:4;display:none}
body.review-open #rv-lines{display:block}
#rv-lines .ln{fill:none;stroke:#C9C3B4;stroke-width:1;stroke-dasharray:3 3}
#rv-lines .ln.cm{stroke:#D9BE63}#rv-lines .ln.ed{stroke:#8FA8D6}#rv-lines .ln.mv{stroke:#8CB9A2}#rv-lines .ln.rm{stroke:#D9A395}
#rv-lines .ln.on{stroke-dasharray:none;stroke-width:1.5;stroke:#1C2821}
#rv-lines .dt{fill:#C9C3B4}#rv-lines .dt.cm{fill:#D9BE63}#rv-lines .dt.ed{fill:#8FA8D6}#rv-lines .dt.mv{fill:#8CB9A2}#rv-lines .dt.rm{fill:#D9A395}#rv-lines .dt.on{fill:#1C2821}
/* marks inside the editor */
#editor del.rl,#editor ins.rl{border-radius:2px;cursor:pointer;text-decoration-thickness:1.5px!important}
#editor del.rm,#editor del.ed{color:#B4553C!important;text-decoration:line-through!important}
#editor ins.ed{color:#2557A7!important;text-decoration:underline!important;text-underline-offset:2px}
#editor ins.blk{display:block;text-decoration:none!important;box-shadow:inset 3px 0 0 #2557A7;padding-left:.6rem}#editor ins.blk *{color:#2557A7!important;text-decoration:underline;text-underline-offset:2px}
#editor.rl-final ins.blk{box-shadow:none;padding-left:0}#editor.rl-final ins.blk *{color:inherit!important;text-decoration:none}
#editor ins.blk.rl-pv-yes{box-shadow:none;padding-left:0}#editor ins.blk.rl-pv-yes *{color:inherit!important;text-decoration:none;background:#E3F0E8}
#editor del.mv{color:#2D6B50!important;text-decoration:line-through double!important;text-decoration-thickness:1px!important}
#editor ins.mv{color:#2D6B50!important;text-decoration:underline double!important;text-decoration-thickness:1px!important;text-underline-offset:2px}
#editor del.rl *,#editor ins.rl *{color:inherit!important}
#editor sup.rl-n{font:600 .56rem/1 'DM Mono',monospace!important;color:#fff!important;background:#8a9790;border-radius:8px;padding:.15rem .3rem;margin-left:.15rem;vertical-align:super;cursor:pointer;user-select:none;text-decoration:none!important}
#editor .rl-hit del.rl,#editor .rl-hit ins.rl,#editor del.rl-hover,#editor ins.rl-hover{background:#FFF3B0}#editor .rl-hit sup.rl-n,#editor sup.rl-hover{background:#1C2821}
#editor :is(p,li,div.copy):has(del.rl,ins.rl){box-shadow:-2px 0 0 0 #C9C3B4;padding-left:.5rem}
#editor.rl-final del.rl,#editor.rl-final sup.rl-n{display:none}#editor.rl-final ins.rl{color:inherit!important;text-decoration:none!important;background:none}
#editor.rl-final :is(p,li,div.copy):has(del.rl,ins.rl){box-shadow:none;padding-left:0}
/* hover preview of a tick or cross: the text as it would read after that decision */
#editor del.rl-pv-yes,#editor sup.rl-pv-yes,#editor sup.rl-pv-no,#editor ins.rl-pv-no{display:none}
#editor ins.rl-pv-yes,#editor del.rl-pv-no{color:inherit!important;text-decoration:none!important;background:#E3F0E8!important}
#editor del.rl-pv-no{background:#FBEFC2!important}
#editor mark.cm{background:#F1EFE8;color:inherit;border-bottom:2px solid #D9BE63;cursor:pointer}
#editor mark.cm.draft{background:#E3F0E8;border-bottom-color:#2D6B50}
#editor mark.cm.cm-hit,#editor mark.cm.cm-hover{background:#F5D96A}
`; document.head.appendChild(css);
})();
