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

  const state = { rel: null, slug: null, key: null, changes: [], st: {}, last: {}, comments: { threads: [] }, lint: [], decided: [],
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
        <button id="rv-refresh" class="rv-ico" hidden title="Pick up comments or changes added since the article was opened">&#8635;</button>
        <button id="rv-close" class="rv-ico" hidden title="Close the review and go back to the to-do list">&times;</button>
      </div>
      <div id="rv-banner" style="display:none"></div>
      <div class="rv-tools">
        <button id="rv-new" class="rv-btn primary" title="Comment on the selected text (Ctrl+Alt+M)">+ Comment</button>
        <span class="rv-switch" role="group" aria-label="How the cards are shown"><button id="rv-v-ctx" aria-pressed="true" title="Cards beside the text they belong to">Inline</button><button id="rv-v-list" aria-pressed="false" title="Every card as a list, unplaced ones too">List</button><button id="rv-v-clean" aria-pressed="false" title="Read the article as if every pending change were accepted: no strikethroughs, numbers or highlights (the cards stay in a list, so you can still decide)">Clean</button></span>
        <button id="rv-resolved" class="rv-btn" aria-pressed="false" title="Show resolved comment threads in the list" style="display:none">Resolved <span id="rv-n-res">0</span></button>
        <button id="rv-lint" class="rv-btn" title="Every prose check on this article: typos, spacing, British spellings, em dashes, leftover notes, repeats, words you don't use">Prose</button>
        <button id="rv-maps" class="rv-btn" title="Turn Google Maps search links into real place pins" style="display:none">Resolve maps <span id="rv-n-maps">0</span></button>
        <span class="rv-more"><button id="rv-more" class="rv-ico" title="More">&#8943;</button>
          <div class="rv-dd"><button id="rv-all-yes">Accept all changes</button><button id="rv-all-no">Reject all changes</button><button id="rv-history">History… (Ctrl+Alt+H)</button></div></span>
      </div>
      <div class="rv-status" id="rv-status"></div>
      <div class="rv-check" id="rv-check"></div>
      <div class="rv-voice" id="rv-voice" style="display:none" title="How this article measures against your live pages. Click for the numbers."></div>
      <div class="rv-body" id="rv-body"><div class="rv-list" id="rv-list"></div><div class="rv-canvas" id="rv-canvas"></div></div>
      <div id="rv-toast"></div>`;
    side.appendChild(pane);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.id = 'rv-lines'; document.body.appendChild(svg);
    $('rv-close').onclick = () => toggle(false);
    $('rv-lint').onclick = runLint;
    $('rv-voice').onclick = () => { state.voiceOpen = !state.voiceOpen; renderVoice(); };
    $('rv-maps').onclick = resolveMaps;
    $('rv-refresh').onclick = () => refresh();
    hashOpen();                                   // /browser?review=<path>: opened from Claude's pass
    $('rv-f-all').onclick = () => setFilter('all');
    $('rv-f-changes').onclick = () => setFilter('changes');
    $('rv-f-comments').onclick = () => setFilter('comments');
    if ($('rv-prev')) $('rv-prev').onclick = () => step(-1);
    if ($('rv-next')) $('rv-next').onclick = () => step(1);
    $('rv-more').onclick = e => { e.stopPropagation(); $('rv-more').parentElement.classList.toggle('open'); };
    document.addEventListener('click', () => { const m = $('rv-more'); if (m) m.parentElement.classList.remove('open'); });
    $('rv-all-yes').onclick = () => decideAll(true);
    $('rv-history').onclick = () => { $('rv-more').parentElement.classList.remove('open'); if (window.openHistory) window.openHistory(); };
    $('rv-all-no').onclick = () => decideAll(false);
    $('rv-new').onmousedown = e => { e.preventDefault(); captureSelection(); };
    $('rv-new').onclick = () => newComment();
    $('rv-v-list').onclick = () => setView('list');
    $('rv-v-ctx').onclick = () => setView('contextual');
    $('rv-v-clean').onclick = () => setView('clean');
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
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => queueLayout());   // cards measured in the fallback font
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
    if (want) { renderAll(); startPolling(); pollTasks(); } else drawLines();
  }
  function setFilter(f) {
    state.filter = f;
    for (const k of ['all', 'changes', 'comments']) $('rv-f-' + k).setAttribute('aria-pressed', f === k);
    renderAll();
  }
  const press = (id, on) => { const b = $(id); if (b) b.setAttribute('aria-pressed', !!on); };
  // Clean (Kevin, 2026-09-29: "read the clean version of a markup, as if the changes were implemented"): the
  // article shows every pending change as accepted and drops the strikethroughs, numbers and comment
  // highlights; the cards stay, as a list, so a decision can still be made from there. Nothing is applied.
  function setView(v) {
    state.clean = v === 'clean'; state.view = state.clean ? 'list' : v;
    ed().classList.toggle('rl-final', state.clean);
    press('rv-v-ctx', v === 'contextual'); press('rv-v-list', v === 'list'); press('rv-v-clean', state.clean);
    renderAll();
  }
  function toast(msg, ms, onClick) {
    const t = $('rv-toast'); t.textContent = msg; t.style.display = 'block';
    t.style.cursor = onClick ? 'pointer' : ''; t.onclick = onClick ? () => { t.style.display = 'none'; onClick(); } : null;
    clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', ms || 3500);
  }
  // Kevin, 2026-09-29: the "comments waiting" notice should take him to the article. The click is the
  // user gesture the File System Access API needs, so the Articles panel's own open can run from it.
  function openWaiting() { if (state.rel && window.__apOpen) window.__apOpen(state.rel); }
  function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
  function plain(h) { const d = document.createElement('div'); d.innerHTML = h || ''; return d.textContent; }
  function dirty() { ed().dispatchEvent(new Event('input', { bubbles: true })); if (window.setDirty) try { window.setDirty(true); } catch (e) {} }
  const H = () => window.__History;

  // ------------------------------------------------------------------ boot
  async function boot(rel) {
    state.rel = rel; state.slug = null; state.changes = []; state.st = {}; state.last = {}; state.comments = { threads: [] }; state.hidden = []; state.active = null; state.lint = [];
    let r;
    try { r = await (await fetch(API + '/review/for?rel=' + encodeURIComponent(rel), { cache: 'no-store' })).json(); }
    catch (e) { console.warn('review: server not reachable', e); return; }
    state.slug = r.slug; state.key = r.comments_key; state.comments = r.comments || { threads: [] };
    state.meta = { title: r.title, before: r.words_before, target: r.words_target, tlabel: r.target_label };
    state.decided = r.decided || [];
    if (state.awaitingFile) { state.view = 'contextual'; press('rv-v-ctx', true); press('rv-v-list', false); }
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
    if (state.comments.threads.some(isTask)) { startPolling(); pollTasks(); }
    // something to review opens the margin; otherwise the photo sidebar is the default
    // (Kevin, 2026-09-26: "default to photos bar open unless there are any comments to review")
    const pendingReview = state.changes.some(c => state.last[c.id] === undefined) || state.comments.threads.some(t => !t.resolved);
    if (pendingReview) toggle(true);
    else if (!document.body.classList.contains('photos-open') && typeof window.togglePhotoPanel === 'function') { toggle(false); window.togglePhotoPanel(); }
    if (state.changes.length) toast(`${state.changes.length} proposed changes to review`, 4000);
  }

  // ------------------------------------------------------------------ marks
  const ENT = s => s.replace(/&mdash;/g, '—').replace(/&ndash;/g, '–').replace(/&nbsp;/g, ' ')
                    .replace(/&rsquo;/g, '’').replace(/&lsquo;/g, '‘').replace(/&hellip;/g, '…');
  function locate(html, needle) {          // the proposal was matched against the file; the DOM re-serialises entities
    if (!needle) return null;
    if (html.indexOf(needle) >= 0) return needle;
    const n2 = ENT(needle); if (html.indexOf(n2) >= 0) return n2;
    return loose(html, n2) || loose(html, needle);
  }
  // Same text, different spacing (Kevin, 2026-09-29: "sometimes when I send to claude, the output isn't
  // written as a red line"). A third of Claude's answers missed on whitespace alone: pasted text
  // carries line breaks and &nbsp; inside sentences, and the model's find either kept them or
  // turned them into plain spaces, so it no longer matched the live text character for character.
  // Any run of spaces, line breaks or non-breaking spaces matches any other; the match must be unique.
  function loose(html, needle) {
    const parts = needle.split(/(?:\s|&nbsp;| )+/).filter(Boolean);
    if (!parts.length) return null;
    const esc = x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    let re; try { re = new RegExp(parts.map(esc).join('(?:\\s|&nbsp;|\\u00a0)+'), 'g'); } catch (e) { return null; }
    const hits = html.match(re);
    return hits && hits.length === 1 ? hits[0] : null;
  }
  window.__rvLocate = locate;               // test seam
  function injectMarks(only) {               // only: just these changes, numbered after the existing ones
    const e = ed(); let html = e.innerHTML;
    const spans = [];
    const list = only || state.changes;
    let n = only ? Math.max(0, ...state.changes.map(c => c.n || 0)) : 0;
    // Every uncommitted change is marked, whatever the server has recorded for it: a decision
    // only becomes real when it is applied in this text and saved. (The standalone review page
    // stored "accept all" as a UI default, which once left this loop with nothing to mark.)
    for (const c of list) {
      if (c.kind === 'grammar') { trimTags(c); c.struct = !balanced(c.find) || !balanced(c.replace); }
      if (c.kind === 'insert') c.struct = false;
      if (c.kind === 'insert') {                 // zero-width span right after the anchor
        const key = locate(html, c.after); if (!key) { state.hidden.push(c.id); continue; }
        const i = html.indexOf(key) + key.length; spans.push([i, i, c, '']); continue;
      }
      const key = locate(html, c.kind === 'grammar' ? c.find : c.sentence);
      if (!key) { state.hidden.push(c.id); continue; }
      const i = html.indexOf(key);
      // a find that starts inside a tag (an href, an alt) cannot be wrapped in <del>/<ins>:
      // the marks would land inside the tag and mangle it. It is applied whole instead.
      if (html.lastIndexOf('<', i) > html.lastIndexOf('>', i)) c.struct = true;
      spans.push([i, i + key.length, c, key]);
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
    // leading: a shared run of CLOSING tags. "</b> — the manuscript library…" closes a <b>
    // that opened before the change, so it is unchanged text and belongs outside the marks.
    // Left in, the parser closed the <b> AND the <del> around it, the old text fell out of
    // the <del>, and accepting kept both versions (Yerevan r7: Matenadaran, Ararat, Moscow
    // Cinema all shipped with the old bullet and the new one run together).
    const SHUT = /^(\s*<\/[a-z][^>]*>)+/i;
    a = (c.find.match(SHUT) || [''])[0]; b = (c.replace.match(SHUT) || [''])[0];
    if (a && a === b) { c.find = c.find.slice(a.length); c.replace = c.replace.slice(b.length); }
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
  // Can this fragment sit inside an inline <del>/<ins> without the parser moving it? Only if
  // every tag it opens it also closes, in order. "…sour cream</li><li><div…>" cannot: the
  // </li> closes the <ins> with it and the new list item lands outside, unmarked, as live
  // DOM. That is how the r7 photo placeholders arrived as dead "[PHOTO]" text on the wrong
  // side of the line, and how a new heading arrived with its old paragraph still in front.
  const VOID = /^(area|base|br|col|embed|hr|img|input|link|meta|source|track|wbr)$/i;
  function balanced(h) {
    const stack = [];
    for (const m of (h || '').matchAll(/<(\/?)([a-z][a-z0-9]*)[^>]*?(\/?)>/gi)) {
      const [, close, name, self] = m; const n = name.toLowerCase();
      if (self || VOID.test(n)) continue;
      if (!close) { stack.push(n); continue; }
      if (stack.pop() !== n) return false;
    }
    return !stack.length;
  }
  function markHtml(c, oldHtml, n) {
    const ce = ' contenteditable="false"';
    // A change that crosses a block boundary is not wrapped at all. The original stays
    // exactly as it is, a chip marks the spot, the pane shows the before and after, and
    // Accept swaps the whole fragment in one piece (decide()).
    if (c.struct)
      return `<ins class="rl ed struct" data-id="${c.id}"${ce}></ins><sup class="rl-n" data-id="${c.id}"${ce}>${n}</sup>${oldHtml}`;
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
    const ref = marks[0].previousElementSibling || marks[0].parentElement;   // where the change was, once its marks are gone
    for (const m of marks) {
      if (m.tagName === 'SUP') { m.remove(); continue; }
      if (m.tagName === 'DEL') { accept ? m.remove() : unwrap(m); }
      else if (m.tagName === 'INS') { accept ? unwrap(m) : m.remove(); }
    }
    if (accept && c.struct) {
      // the fragment was never wrapped, so it is still intact in the serialised canvas
      // Deciding another change in the same paragraph can leave an empty class="" on the <p>
      // (classList.remove of the hit highlight), and a find that opens with the tag then stops
      // matching. An empty class means nothing, so it is dropped before matching.
      // comment and lint highlights inside the fragment (the thread that asked for it is one)
      // would stop the find matching; they are drawn from anchors, so drop them and redraw after
      ed().querySelectorAll('mark.cm:not(.draft), mark.lint').forEach(unwrap); ed().normalize();
      const e = ed(), h = e.innerHTML.replace(/ class=""/g, ''), key = locate(h, c.find);
      if (key) e.innerHTML = h.replace(key, () => c.replace);   // a function: "$" in the text is literal
      else console.warn('review: layout change', id, 'no longer matches; nothing applied');
      anchorComments();
    } else if (accept && holder && holder.isConnected && !holder.textContent.trim() && !holder.querySelector('img,.img-slot-empty,picture,.photo-ph')) holder.remove();
    if (accept) {
      // an accepted change can carry a [PHOTO] marker; make it the same live, clickable slot
      // a marker becomes when the article is opened, instead of dead text until the next open
      const e = ed();
      if (window.adoptPlaceholders) window.adoptPlaceholders(e);
      if (window.restoreSlotButtons) window.restoreSlotButtons(e);
      if (window.restoreMapButtons) window.restoreMapButtons(e);
    }
    state.st[id] = accept; state.last[id] = accept;
    if (c.task) {                              // an answer from Claude: the thread follows the decision
      const th = state.comments.threads.find(t => t.id === c.task);
      // the tick applies the edit AND clears the comment it answered (Kevin, 2026-09-27); a cross leaves it open, marked rejected
      const open = state.changes.some(x => x.task === c.task && x.id !== id && state.st[x.id] === undefined);
      if (th && !open) {
        const any = accept || state.changes.some(x => x.task === c.task && state.st[x.id] === true);
        th.status = any ? 'applied' : 'rejected';
        if (any) { th.resolved = true; th.anchor = { before: '', quote: plain(c.replace).replace(/\s+/g, ' ').trim().slice(0, 120), after: '' }; }
        anchorComments(); saveComments();
      }
      // a Claude edit you accepted is saved at once: no "did that land?", no stale copy on reload
      if (accept && window.saveFile) setTimeout(() => { try { if (typeof fileHandle !== 'undefined' && fileHandle) window.saveFile({ auto: true }); } catch (e) {} }, 400);
    }
    if (H() && H().label) H().label((accept ? 'Accepted' : 'Rejected') + (c.task ? ' Claude: ' : ': ') + plain(c.replace || c.sentence || '').replace(/\s+/g, ' ').slice(0, 48));
    const posted = { [id]: accept };
    if (c.inner && c.inner.length) {
      if (accept) c.inner.forEach(i => { state.st[i] = true; state.last[i] = true; posted[i] = true; });
      else reinject(c.inner);
    }
    post(posted);
    if (H()) H().touch(); dirty();
    previewDecision(null);
    if (!silent) { renderAll(); advanceFrom(id, holder && holder.isConnected ? holder : ref); }
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
    // scroll only when the change is out of view: centering it on every jump made the
    // article leap on each Accept, even when the next change was already on screen
    if (marks[0] && !fromDoc) reveal(marks[0]);
    renderAll();
    const card = document.querySelector(`.rv-card[data-id="${id}"]`); if (card && state.view === 'list') card.scrollIntoView({ block: 'nearest' });
  }
  function reveal(el) {                      // the smallest scroll that brings the change into view, with some context
    const sc = document.querySelector('.editor-scroll'); if (!sc) { el.scrollIntoView({ block: 'center' }); return; }
    const r = el.getBoundingClientRect(), s = sc.getBoundingClientRect(), M = 120;
    if (r.top >= s.top + 24 && r.bottom <= s.bottom - 24) return;                 // already on screen: leave the page alone
    if (r.top < s.top + 24) sc.scrollTop -= (s.top + M) - r.top;
    else sc.scrollTop += r.bottom - (s.bottom - M);
  }
  function step(dir) {
    const order = pending().map(c => c.id); if (!order.length) return;
    const i = order.indexOf(state.active); jump(order[(i + dir + order.length) % order.length]);
  }
  function advanceFrom(id, from) {           // Word moves to the next change after Accept / Reject
    const order = pending(); if (!order.length) { state.active = null; renderAll(); return; }
    let next = null;
    if (from && from.isConnected) {
      const placed = order.map(c => [c, ed().querySelector(`[data-id="${c.id}"]`)]).filter(x => x[1]);
      placed.sort((a, b) => (a[1].compareDocumentPosition(b[1]) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);
      const below = placed.filter(x => from.compareDocumentPosition(x[1]) & Node.DOCUMENT_POSITION_FOLLOWING);
      // the next change down the page, or, after the last one, the nearest above it
      next = below.length ? below[0][0] : (placed.length ? placed[placed.length - 1][0] : null);
    }
    if (!next) {
      const idx = state.changes.findIndex(c => c.id === id);
      next = order.find(c => state.changes.indexOf(c) > idx) || order[0];
    }
    // the next card lights up; the article itself stays where Kevin left it (a scroll on
    // every Accept was "disruptive", 2026-09-26). Clicking the card still reveals its text.
    jump(next.id, true);
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
        // a saved decision stays reversible under Decided (in List) instead of vanishing with its card:
        // Kevin, 2026-09-30, could not reject #15 once the autosave had committed it
        const slug = state.slug, keep = (c, v) => { if (c && c.kind === 'grammar' && !state.decided.some(x => x.round === slug && x.id === c.id))
          state.decided.push({ round: slug, id: c.id, n: c.n, section: c.section, find: c.find, replace: c.replace, accepted: v }); };
        accepted.forEach(id => keep(byId(id), true));
        state.changes = state.changes.filter(c => !accepted.includes(c.id)); accepted.forEach(id => delete state.st[id]);
        if (r.complete) {
          state.changes.filter(c => state.st[c.id] === false).forEach(c => keep(c, false));
          state.slug = null; state.changes = [];
          toast('Review complete: every change is decided and applied. To change your mind on one, open List and show the decided changes.', 7000);
        }
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
  function wrapRange(range, id, draft, kind) {   // one <mark> per text node, so the article's own structure is never touched
    kind = kind || 'cm';
    const walker = document.createTreeWalker(ed(), NodeFilter.SHOW_TEXT);
    const nodes = []; let n, first = null;
    while ((n = walker.nextNode())) if (range.intersectsNode(n) && n.nodeValue.trim() && !n.parentElement.closest('sup.rl-n')) nodes.push(n);
    for (let t of nodes) {
      const s0 = t === range.startContainer ? range.startOffset : 0, e0 = t === range.endContainer ? range.endOffset : t.nodeValue.length;
      if (e0 <= s0) continue;
      if (e0 < t.nodeValue.length) t.splitText(e0);
      if (s0 > 0) t = t.splitText(s0);
      const m = document.createElement('mark'); m.className = kind + (draft ? ' draft' : ''); m.dataset[kind] = id;
      t.parentNode.insertBefore(m, t); m.appendChild(t); first = first || m;
    }
    return first;
  }
  function unwrapMark(id) { ed().querySelectorAll(`mark.cm[data-cm="${id}"]`).forEach(unwrap); }
  function postDraft(text, toClaude) {
    const d = state.draft; if (!d || !text.trim()) return;
    const m = ed().querySelector(`mark.cm[data-cm="${d.id}"]`); if (m) m.classList.remove('draft');
    const t = { id: d.id, author: AUTHOR, text: text.trim(), created: new Date().toISOString(), anchor: d.anchor, resolved: false, replies: [] };
    if (toClaude) { t.kind = 'task'; t.status = 'sent'; t.sentAt = new Date().toISOString(); }
    state.comments.threads.push(t);
    state.draft = null; _range = null; state.active = d.id;
    (toClaude ? beforeSend(t) : Promise.resolve()).then(saveComments).then(() => { if (toClaude) { toast('Sent. The edit lands here when Claude answers.', 4000); pollTasks(); } }); renderAll();
  }
  // ---- tasks: comments sent to Claude come back with an edit that goes into this text ----
  const isTask = t => t.kind === 'task' || t.kind === 'rewrite';
  // What Claude needs to answer against the text as it is NOW: the blocks the comment's
  // highlight sits in (a whole list when it spans items), marks stripped. And the article is
  // saved first, so the answerer's copy of the rest of the page matches the screen too.
  function liveContext(id) {
    const marks = [...ed().querySelectorAll(`mark.cm[data-cm="${id}"]`)];
    if (!marks.length) return '';
    const blocks = [];
    for (const m of marks) {
      let b = m.closest('li') ? (m.closest('ul, ol') || m.closest('li')) : m.closest('p, h2, h3, h4, blockquote, figcaption, .img-caption, div.fn-sub-hd');
      if (b && ed().contains(b) && !blocks.includes(b)) blocks.push(b);
    }
    return blocks.map(b => b.outerHTML.replace(/<mark class="(?:cm|lint)\b[^>]*>([\s\S]*?)<\/mark>/g, '$1')
                                       .replace(/<(del|ins) class="rl[^"]*"[^>]*>[\s\S]*?<\/\1>/g, m => /^<ins/.test(m) ? '' : m.replace(/<\/?del[^>]*>/g, ''))
                                       .replace(/<sup class="rl-n[^>]*>[\s\S]*?<\/sup>/g, '')).join('\n');
  }
  async function beforeSend(t) {
    try { t.live = liveContext(t.id); } catch (e) { t.live = ''; }
    try {
      if (typeof fileHandle !== 'undefined' && fileHandle && window.saveFile && typeof isDirty !== 'undefined' && isDirty) await window.saveFile({ auto: true });
    } catch (e) {}
  }
  // "moved it to gyumri.html" in a reply: the file name opens that article, "#more-stops" jumps here
  const linkFiles = s => s.replace(/\b([a-z0-9-]+\.html)(#[a-z0-9-]+)?\b/g, (m, f, h) => `<button class="rp-open" data-file="${f}" data-hash="${h || ''}" title="Open ${f}">${m}</button>`)
                          .replace(/(^|[\s(])#([a-z][a-z0-9-]+)\b/g, (m, pre, id) => `${pre}<button class="rp-open" data-hash="#${id}" title="Go to #${id}">#${id}</button>`);
  document.addEventListener('click', e => {
    const b = e.target.closest && e.target.closest('.rp-open'); if (!b) return;
    e.preventDefault(); e.stopPropagation();
    const f = b.dataset.file, h = b.dataset.hash;
    if (f && state.rel && !state.rel.endsWith('/' + f)) {
      const rel = state.rel.replace(/[^/]+$/, '') + f;
      if (window.__apOpen) window.__apOpen(rel);
      return;
    }
    const el = h && ed().querySelector(h); if (el) el.scrollIntoView({ block: 'center' });
  }, true);
  // 5: right-click on a selection sends one of the common asks straight to Claude
  const QUICK = [['Rewrite', 'rewrite'], ['Shorter', 'make this shorter'], ['Less formal', 'less formal, more like me'],
                 ['Synonym', 'synonym'], ['Link this', 'link this (Maps pin for a place, my article if I have one)'],
                 ['Look this up', 'look this up and fill it in, with the source in the reply']];
  function quickTask(text, range) {
    const a = textAround(range), id = 'c' + Date.now().toString(36);
    wrapRange(range, id, false);
    const t = { id, author: AUTHOR, text, created: new Date().toISOString(), anchor: a, resolved: false, replies: [],
                kind: 'task', status: 'sent', sentAt: new Date().toISOString() };
    state.comments.threads.push(t);
    state.active = id; if (state.filter === 'changes') setFilter('all');
    beforeSend(t).then(saveComments).then(() => pollTasks()); renderAll();
    toast('Sent to Claude: ' + text, 3000);
  }
  function quickMenu(x, y, range) {
    let m = document.getElementById('rv-quick'); if (m) m.remove();
    m = document.createElement('div'); m.id = 'rv-quick';
    m.innerHTML = `<div class="qh">${CLAUDE_MARK} Send to Claude</div>` + QUICK.map(([l], i) => `<button data-i="${i}">${l}</button>`).join('') +
                  '<button data-i="c" class="qc">Comment…</button>';
    m.style.left = Math.min(x, innerWidth - 190) + 'px'; m.style.top = Math.min(y, innerHeight - 260) + 'px';
    document.body.appendChild(m);
    m.onclick = e => {
      const b = e.target.closest('button'); if (!b) return;
      m.remove();
      if (b.dataset.i === 'c') { _range = range; newComment(); return; }
      quickTask(QUICK[+b.dataset.i][1], range);
    };
    setTimeout(() => document.addEventListener('mousedown', function off(ev) { if (!m.contains(ev.target)) { m.remove(); document.removeEventListener('mousedown', off); } }), 0);
  }
  document.addEventListener('contextmenu', e => {
    if (!ed() || !ed().contains(e.target) || !state.rel) return;
    const s = getSelection(); if (!s.rangeCount || s.isCollapsed || !ed().contains(s.anchorNode)) return;
    e.preventDefault();
    quickMenu(e.clientX, e.clientY, s.getRangeAt(0).cloneRange());
  });
  // the Claude app icon itself (Images/web/ui/claude-icon.png), served by the photo server
  const CLAUDE_MARK = '<img class="claude-mark" src="' + API + '/site/Images/web/ui/claude-icon.png" alt="" width="16" height="16">';
  function sendToClaude(id) {
    const t = state.comments.threads.find(x => x.id === id); if (!t) return;
    t.kind = 'task'; t.status = 'sent'; t.sentAt = new Date().toISOString(); t.resolved = false; delete t.edit; delete t.edits;
    beforeSend(t).then(saveComments).then(pollTasks); renderAll(); toast('Sent to Claude.');
  }
  let _pollT = null;
  const ageSec = iso => iso ? Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000) : 0;
  const ageText = sec => sec < 60 ? Math.round(sec) + 's' : sec < 3600 ? Math.round(sec / 60) + ' min' : Math.round(sec / 360) / 10 + ' h';
  function tickAges() {
    let needNote = false;
    document.querySelectorAll('#review-pane .chip.sent .age').forEach(a => {
      const sec = ageSec(a.dataset.since); a.textContent = ' · ' + ageText(sec);
      if (sec > 90 && !a.closest('.rv-card').querySelector('.wait-note')) needNote = true;
    });
    if (needNote && !state.draft) renderAll();
  }
  function startPolling() {
    if (_pollT) return;
    // the server pushes 'changed' the moment the comment store is written; the poll stays as a
    // slow fallback for when the stream drops (sleep, a server restart)
    let pushed = false;
    try {
      const es = new EventSource(API + '/api/tasks/stream');
      es.onopen = () => { pushed = true; };
      es.onmessage = () => pollTasks(true);
      es.onerror = () => { pushed = false; };
    } catch (e) {}
    _pollT = setInterval(() => { if (!pushed) pollTasks(); }, 1000);
    setInterval(() => { if (pushed) pollTasks(); }, 5000);
    setInterval(tickAges, 1000);
  }
  async function pollTasks(force) {
    if (!state.key || state._polling) return;
    // force: the server pushed a change, so fetch even with nothing waiting here (a task from
    // another tab, or one Claude's session added); the idle poll still skips
    if (!force && !state.comments.threads.some(t => isTask(t) && !t.resolved && !['applied', 'proposed', 'rejected', 'offered'].includes(t.status))) return;
    state._polling = true;
    let r; try { r = await (await fetch(API + `/comments/${state.key}`, { cache: 'no-store' })).json(); } catch (e) { state._polling = false; return; }
    state._polling = false;
    let touched = false;
    for (const st of (r.threads || [])) {
      let t = state.comments.threads.find(x => x.id === st.id);
      // a task this tab has never seen (another tab, or added while it was closed) may arrive
      // already answered: take it in and let the code below apply its edit in the same pass
      if (!t) {
        if (!isTask(st)) continue;
        t = Object.assign({}, st, { status: (st.edit || st.edits) && !['applied', 'rejected', 'offered'].includes(st.status) ? 'sent' : st.status });
        state.comments.threads.push(t); touched = true;
      }
      if (!isTask(t)) continue;
      // Claude's replies and status arrive; Kevin's own edits to the thread stay
      const seen = new Set((t.replies || []).map(x => x.id));
      for (const rp of (st.replies || [])) if (!seen.has(rp.id)) { (t.replies = t.replies || []).push(rp); touched = true; }
      if (st.edits && st.edits.length && !['applied', 'offered', 'proposed', 'rejected'].includes(t.status)) { t.edits = st.edits; touched = applyTaskEdits(t) || touched; }
      else if (st.edit && t.status !== 'applied' && t.status !== 'offered' && t.status !== 'proposed' && t.status !== 'rejected') { t.edit = st.edit; touched = applyTaskEdit(t) || touched; }
      // an edit that was applied but never saved: the article was reopened and the old sentence is
      // back. If its find is in the text again, apply it again rather than showing a done chip over undone text.
      else if (st.edit && t.status === 'applied' && !t.resolved && locate(ed().innerHTML.replace(/<\/?mark[^>]*>/g, '').replace(/ class=""/g, ''), st.edit.find)) { t.edit = st.edit; t.status = 'sent'; touched = applyTaskEdit(t) || touched; }
      else if (st.status && st.status !== t.status && t.status !== 'applied' && t.status !== 'offered') { t.status = st.status; touched = true; }
      if (st.resolved && !t.resolved && t.status !== 'applied') { t.resolved = true; touched = true; }
    }
    if (touched) {
      anchorComments(); saveComments();
      // never rebuild the cards under a comment or reply being written: the box would be
      // re-created mid-click. The next render (post, decide, resolve) shows the new state.
      const writing = state.draft || (document.activeElement && document.activeElement.closest('#review-pane textarea'));
      if (writing) queueLayout(); else renderAll();
    }
  }
  // the edit goes into the editor's copy (never the file): undoable, and the article is unsaved
  let _typedAt = 0;
  document.addEventListener('input', e => { if (ed() && ed().contains(e.target)) _typedAt = Date.now(); }, true);
  // one instruction, several places ("link every place in this section"): one tracked change each
  function applyTaskEdits(t) {
    if (Date.now() - _typedAt < 2500) return false;
    const e = ed();
    e.querySelectorAll('mark.cm:not(.draft), mark.lint').forEach(unwrap);
    e.normalize();
    let h = e.innerHTML.replace(/ class=""/g, ''); e.innerHTML = h;
    const fresh = [];
    t.edits.forEach((x, i) => {
      const cid = 'tk' + t.id + '-' + i;
      if (state.changes.some(c => c.id === cid)) return;
      const key = locate(h, x.find); if (!key) return;
      fresh.push({ id: cid, kind: 'grammar', section: 'Claude', find: key, replace: x.replace, count: 1, task: t.id,
                   note: i ? '' : ((t.replies || []).filter(r => r.author === 'Claude').map(r => r.text).pop() || '') });
    });
    for (const f of state.lint) { if (!f.anchor) continue; const rg = findRange(f.anchor); if (rg) wrapRange(rg, f.id, false, 'lint'); }
    if (!fresh.length) { t.status = 'offered'; t.edit = t.edits[0]; toast("Claude's edit no longer matches the text here; it is on the card with a Use button.", 7000); return true; }
    if (H() && H().label) { H().label('Claude proposed: ' + t.text.slice(0, 48)); H().checkpoint(); }
    state.changes.push(...fresh); injectMarks(fresh);
    t.status = 'proposed'; t.proposedAt = new Date().toISOString(); state.active = fresh[0].id;
    toast('Claude proposed ' + fresh.length + ' change' + (fresh.length === 1 ? '' : 's') + ': ' + t.text.slice(0, 50), 5000);
    return true;
  }
  function applyTaskEdit(t) {
    // never while Kevin is typing: the swap resets the caret. The next poll tries again.
    if (Date.now() - _typedAt < 2500) return false;
    const e = ed();
    // comment and lint highlights are <mark>s inside the text; a find wider than one quote
    // would never match through them. They are derived from anchors, so drop them, match on
    // the bare text, and put them back after.
    e.querySelectorAll('mark.cm:not(.draft), mark.lint').forEach(unwrap);
    e.normalize();
    const h = e.innerHTML.replace(/ class=""/g, '');
    const key = locate(h, t.edit.find);
    const relint = () => { for (const f of state.lint) { if (!f.anchor) continue; const rg = findRange(f.anchor); if (rg) wrapRange(rg, f.id, false, 'lint'); } };
    if (key) {
      // the answer goes in as a TRACKED CHANGE: old text struck, new text underlined, a tick
      // and a cross on its card, exactly like a review round. Nothing is applied until
      // Kevin accepts it; the thread card keeps the reply and says "Claude proposed".
      const cid = 'tk' + t.id;
      if (!state.changes.some(c => c.id === cid)) {
        const c = { id: cid, kind: 'grammar', section: 'Claude', find: key, replace: t.edit.replace, count: 1, task: t.id,
                    note: (t.replies || []).filter(r => r.author === 'Claude').map(r => r.text).pop() || '' };
        if (H() && H().label) { H().label('Claude proposed: ' + t.text.slice(0, 48)); H().checkpoint(); }
        state.changes.push(c);
        e.innerHTML = h;
        injectMarks([c]);
      }
      relint();
      t.status = 'proposed'; t.proposedAt = new Date().toISOString();
      state.active = cid;
      toast('Claude proposed: ' + t.text.slice(0, 70) + ' (tick to accept)', 5000);
      return true;
    }
    relint();
    t.status = 'offered';                    // the text moved on; the card offers the replacement instead
    toast("Claude's edit no longer matches the text here; it is on the card with a Use button.", 7000);
    // its quote may be what moved: re-anchor on the text either side of it, so the card
    // with the Use button still has a place beside the passage instead of only in the List
    if (!findRange(t.anchor)) {
      const a = t.anchor || {};
      for (const q of [(a.after || '').trim().slice(0, 40), (a.before || '').trim().slice(-40)]) {
        if (q.length > 8 && findRange({ quote: q })) { t.anchor = { before: '', quote: q, after: '' }; break; }
      }
    }
    return true;
  }
  function useTaskEdit(t) {
    const marks = [...ed().querySelectorAll(`mark.cm[data-cm="${t.id}"]`)];
    if (H()) H().checkpoint();
    if (marks.length) {
      marks[0].innerHTML = t.edit.replace; marks.slice(1).forEach(m => { m.textContent = ''; }); marks.forEach(unwrap);
    } else {
      const key = locate(ed().innerHTML.replace(/ class=""/g, ''), t.edit.find);
      if (!key) { toast('The passage is no longer in the text; paste it by hand from the card.'); return; }
      ed().innerHTML = ed().innerHTML.replace(/ class=""/g, '').replace(key, () => t.edit.replace);
    }
    if (window.adoptPlaceholders) window.adoptPlaceholders(ed());
    if (window.restoreSlotButtons) window.restoreSlotButtons(ed());
    t.status = 'applied'; t.anchor = { before: '', quote: plain(t.edit.replace).replace(/\s+/g, ' ').trim().slice(0, 120), after: '' };
    dirty(); if (H()) H().touch(); anchorComments(); saveComments(); renderAll();
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
  function reply(id, text) { const t = thread(id); if (!t || !text.trim()) return; t.replies.push({ id: 'r' + Date.now().toString(36), author: AUTHOR, text: text.trim(), created: new Date().toISOString() }); if (state.replyDrafts) delete state.replyDrafts[id]; saveComments(); renderAll(); }
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
    if (state.filter !== 'changes') for (const f of state.lint) out.push({ kind: 'lint', id: f.id, f, el: f.anchor ? ed().querySelector(`mark.lint[data-lint="${f.id}"]`) : null });
    out.sort((a, b) => { if (!a.el && !b.el) return 0; if (!a.el) return 1; if (!b.el) return -1;
      return (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1; });
    return out;
  }
  // what is still to fill in this article, as chips; a click goes to the first one
  function blanks() {
    const out = [], w = document.createTreeWalker(ed(), NodeFilter.SHOW_TEXT);
    let n; while ((n = w.nextNode())) {
      if (n.parentElement.closest('.rv-card, sup.rl-n, ins.rl, del.rl')) continue;
      for (const m of n.nodeValue.matchAll(/\[\s*\]|\[(?!PHOTO\])[^\[\]\n]{1,120}\]/g)) out.push([n, m.index, m[0].length]);
    }
    return out;
  }
  function renderChecklist() {
    const el = $('rv-check'); if (!el) return;
    if (!state.rel || state.awaitingFile) { el.innerHTML = ''; return; }
    const b = blanks(), slots = [...ed().querySelectorAll('.img-slot-empty')], maps = typeof mapsPlaceholders === 'function' ? mapsPlaceholders() : [];
    const waiting = state.comments.threads.filter(t => isTask(t) && !t.resolved && (t.status === 'sent' || !t.status));
    const items = [];
    if (b.length) items.push(['blank', b.length + (b.length === 1 ? ' blank' : ' blanks')]);
    if (slots.length) items.push(['photo', slots.length + ' empty photo' + (slots.length === 1 ? '' : 's')]);
    if (maps.length) items.push(['maps', maps.length + ' map link' + (maps.length === 1 ? '' : 's') + ' to resolve']);
    if (waiting.length) items.push(['wait', waiting.length + ' with Claude']);
    el.innerHTML = items.length ? items.map(([k, l]) => `<button data-k="${k}">${l}</button>`).join('') : '<span class="ok">&#10003; nothing left to fill</span>';
    el.querySelectorAll('button').forEach(x => x.onclick = () => {
      const k = x.dataset.k;
      if (k === 'blank') { const [n, i, len] = blanks()[0]; const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + len);
                           const s = getSelection(); s.removeAllRanges(); s.addRange(r); n.parentElement.scrollIntoView({ block: 'center' }); }
      else if (k === 'photo') ed().querySelector('.img-slot-empty').scrollIntoView({ block: 'center' });
      else if (k === 'maps') mapsPlaceholders()[0].scrollIntoView({ block: 'center' });
      else if (k === 'wait') { const t = waiting[0]; const m = ed().querySelector(`mark.cm[data-cm="${t.id}"]`); if (m) m.scrollIntoView({ block: 'center' }); }
    });
  }
  function renderAll() {
    if (!$('review-pane')) return;
    try { renderChecklist(); } catch (e) {}
    mapsCount();
    const pend = pending(), open = state.comments.threads.filter(t => !t.resolved).length + (state.draft ? 1 : 0);
    $('rv-n-ch').textContent = pend.length; $('rv-n-cm').textContent = open;
    const nres = state.comments.threads.filter(t => t.resolved).length; $('rv-n-res').textContent = nres; $('rv-resolved').style.display = state.view === 'list' && nres ? '' : 'none';
    const hb = $('btn-review-n'); if (hb) hb.textContent = pend.length + open ? String(pend.length + open) : '';
    const cut = state.changes.filter(c => c.kind === 'cut' && state.st[c.id] === true).reduce((s, c) => s + (c.words || 0), 0);
    const yes = Object.values(state.st).filter(v => v === true).length, no = Object.values(state.st).filter(v => v === false).length;
    const words = state.meta && state.meta.before && state.meta.target ? ` ·${state.meta.before - cut} words (${state.meta.tlabel} ${state.meta.target})` : '';
    $('rv-status').textContent = state.slug ? `${pend.length} to review · ${yes} accepted · ${no} rejected${words}`
                               : (state.rel ? `${open} open comment${open === 1 ? '' : 's'} · no proposed changes` : '');   // no article: say nothing
    const list = $('rv-list'), canvas = $('rv-canvas');
    list.innerHTML = ''; canvas.innerHTML = '';
    const listMode = state.view === 'list' || state.awaitingFile;
    list.style.display = listMode ? '' : 'none'; canvas.style.display = listMode ? 'none' : '';
    $('rv-body').classList.toggle('list', listMode);
    const host = listMode ? list : canvas;
    if (state.awaitingFile) {
      const file = (state.rel || '').split('/').pop(), n = (state.previewChanges || []).length;
      const p = document.createElement('div'); p.className = 'rv-card rv-cm other wait';
      p.innerHTML = `<div class="who"><span class="av">!</span><b>Waiting for the article</b></div><div class="tx">These comments${n ? ` and ${n} proposed changes` : ''} belong to <b>${esc(file)}</b>.</div><button type="button" class="rv-btn primary rv-open">Open ${esc(file)}</button>`;
      p.style.cursor = 'pointer'; p.title = 'Open ' + file;
      p.onclick = openWaiting;
      host.appendChild(p);
    }
    const its = items(); let lost = 0;
    for (const it of its) {
      if (!listMode && !it.el) { lost++; continue; }                  // the margin holds only what has a place in the text
      host.appendChild(it.kind === 'change' ? changeCard(it.c, it.dim, it.threads) : it.kind === 'draft' ? card(Object.assign({}, state.draft, { draft: true })) : it.kind === 'lint' ? lintCard(it.f) : card(it.t));
    }
    // decided changes stay out of the way (Kevin, 2026-10-01: "can you hide the Decided comments?"):
    // one collapsed row at the end of List, opened only to change a decision
    const dec = state.filter !== 'comments' ? (state.decided || []) : [];
    if (listMode && dec.length) {
      const h = document.createElement('button'); h.type = 'button'; h.className = 'rv-dec-h' + (state.showDecided ? ' open' : '');
      h.textContent = `${state.showDecided ? 'Hide' : 'Show'} decided changes (${dec.length})`;
      h.onclick = () => { state.showDecided = !state.showDecided; renderAll(); };
      host.appendChild(h);
      if (state.showDecided) [...dec].sort((a, b) => a.round === b.round ? a.n - b.n : (a.round < b.round ? 1 : -1)).forEach(x => host.appendChild(decidedCard(x)));
    }
    if (lost) { const n = document.createElement('div'); n.className = 'rv-note'; n.textContent = `${lost} more not found in the open text · see List`; host.appendChild(n); }
    if (!host.querySelector(':scope > :not(.rv-note):not(.rv-dec-h)')) {
      const e = document.createElement('div'); e.className = 'rv-empty';
      e.textContent = state.slug && !pend.length && state.filter !== 'comments' ? 'Every change is decided. Save the article to commit them.'
                    : state.filter === 'changes' ? 'No changes to review.' : 'Select text and choose + Comment (Ctrl+Alt+M).';
      host.appendChild(e);
    }
    layout();
    if (window.ResizeObserver) {
      if (state._ro) state._ro.disconnect();
      state._ro = new ResizeObserver(() => queueLayout());
      host.querySelectorAll(':scope > .rv-card').forEach(c => state._ro.observe(c));
    }
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
  // A decided, saved change: what was decided, and the other choice. The text is swapped in the
  // editor (an undoable edit, unsaved until Save) and the decision posted to that change's round,
  // open or closed. Grammar changes only: a cut or a move has written other files.
  function decidedCard(x) {
    const d = document.createElement('div');
    d.className = 'rv-card rv-c ed rv-done'; d.dataset.dec = x.round + ':' + x.id;
    d.innerHTML = `<div class="who"><span class="av">${REVIEWER[0]}</span><b>${x.accepted ? 'Accepted' : 'Rejected'}</b><span class="tm">${esc(x.round)}</span></div>
      <div class="lbl"><span class="num">${x.n}</span>Replaced${x.section ? ' · ' + esc(x.section) : ''}</div>
      <div class="t"><del>${esc(plain(x.find))}</del> <ins>${esc(plain(x.replace))}</ins></div>
      <div class="act"><button type="button" data-a="flip">${x.accepted ? 'Reject instead' : 'Accept instead'}</button></div>`;
    d.querySelector('[data-a="flip"]').onclick = ev => { ev.stopPropagation(); redecide(x, !x.accepted); };
    d.title = 'Click to show the whole change'; d.onclick = () => { d.classList.toggle('open'); queueLayout(); };
    return d;
  }
  function redecide(x, accept) {
    const e = ed();
    e.querySelectorAll('mark.cm:not(.draft), mark.lint').forEach(unwrap); e.normalize();
    const h = e.innerHTML.replace(/ class=""/g, ''), key = locate(h, accept ? x.find : x.replace);
    if (!key) {
      anchorComments(); renderAll();
      toast(`Change ${x.n} has been edited since it was ${x.accepted ? 'accepted' : 'rejected'}, so its text is not in the article any more. Nothing changed: edit it by hand.`, 9000);
      return;
    }
    if (H()) H().checkpoint();
    e.innerHTML = h.replace(key, () => accept ? x.replace : x.find);    // a function: "$" in the text is literal
    if (window.adoptPlaceholders) window.adoptPlaceholders(e);
    if (window.restoreSlotButtons) window.restoreSlotButtons(e);
    if (window.restoreMapButtons) window.restoreMapButtons(e);
    anchorComments();
    x.accepted = accept;
    fetch(API + `/review/${x.round}/decisions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [x.id]: accept }) })
      .catch(() => toast('Could not save the decision (server?)'));
    if (H() && H().label) H().label((accept ? 'Accepted' : 'Rejected') + ' instead: ' + plain(x.replace).replace(/\s+/g, ' ').slice(0, 48));
    if (H()) H().touch(); dirty(); renderAll();
    toast(`Change ${x.n} ${accept ? 'accepted' : 'rejected'} instead. Save to keep it.`, 5000);
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
      d.innerHTML = `<div class="who"><span class="av me">${AUTHOR[0]}</span><b>${esc(AUTHOR)}</b><span class="tm">draft</span></div><textarea placeholder="Start the conversation (Ctrl+Enter to post)">${esc(t.text || '')}</textarea>
        <div class="act"><button class="primary" data-a="post" title="Post (Ctrl+Enter)">Post</button><button data-a="send" class="send" title="Post it as an instruction for Claude: the edit lands in this text while you keep writing (Ctrl+Shift+Enter)">${CLAUDE_MARK} Send to Claude</button><button data-a="cancel" title="Discard (Esc)">Cancel</button></div>`;
      d.querySelector('[data-a="post"]').onclick = () => postDraft(d.querySelector('textarea').value);
      d.querySelector('[data-a="send"]').onclick = () => postDraft(d.querySelector('textarea').value, true);
      d.querySelector('[data-a="cancel"]').onclick = cancelDraft;
      d.querySelector('textarea').onkeydown = e => { if (e.ctrlKey && e.key === 'Enter') postDraft(e.target.value, e.shiftKey); if (e.key === 'Escape') cancelDraft(); };
      // typed text lives in state too: a re-render (the task poll, a decision) rebuilds the card
      d.querySelector('textarea').oninput = e => { if (state.draft) state.draft.text = e.target.value; queueLayout(); };
      return d;
    }
    const av = a => `<span class="av${a === AUTHOR ? ' me' : ''}">${esc((a || '?')[0])}</span>`;
    const replies = (t.replies || []).map(r => `<div class="rp" data-rid="${r.id}"><div class="who">${av(r.author)}<b>${esc(r.author)}</b><span class="tm">${when(r.created)}${r.edited ? ' · edited' : ''}</span>${r.author === AUTHOR ? '<button class="ico" data-a="edit-r" title="Edit">&#9998;</button>' : ''}</div><div class="tx">${linkFiles(esc(r.text))}</div></div>`).join('');
    const chip = !isTask(t) ? '' : t.status === 'applied' ? '<span class="chip done">Claude edited</span>' : t.status === 'offered' ? '<span class="chip wait">edit ready</span>'
               : t.status === 'failed' ? '<span class="chip fail">couldn\'t do it</span>' : t.status === 'answered' ? '<span class="chip done">Claude replied</span>'
               : t.status === 'proposed' ? '<span class="chip wait">Claude proposed · tick to accept</span>' : t.status === 'rejected' ? '<span class="chip fail">rejected</span>'
               : `<span class="chip sent"><span class="spin"></span>waiting for Claude<span class="age" data-since="${esc(t.sentAt || t.created || '')}"></span></span>`;
    const waitNote = isTask(t) && !t.resolved && (t.status === 'sent' || !t.status) && ageSec(t.sentAt || t.created) > 120
      ? '<div class="lbl wait-note">Nothing has picked this up yet. Claude answers from the Claude Code session (say <b>answer my tasks</b>, or run <b>/loop answer my editor tasks</b> to have it checked every minute), or on its own with an API key in .env.</div>' : '';
    d.innerHTML = `<div class="who">${av(t.author)}<b>${esc(t.author)}</b><span class="tm">${when(t.created)}${t.edited ? ' · edited' : ''}</span>${chip}
        <span class="acts">${!t.resolved && (!isTask(t) || t.status === 'failed') ? '<button class="claude" data-a="send" title="Send to Claude: the edit lands in this text while you keep writing">' + CLAUDE_MARK + '</button>' : ''}${t.resolved ? '' : '<button class="yes" data-a="resolve" title="Resolve thread">&#10003;</button>'}
          <span class="menu"><button class="ico" data-a="more" title="More thread actions">&#8943;</button>
          <div class="dd">${t.resolved ? '<button data-a="reopen">Reopen</button>' : '<button data-a="resolve2">Resolve thread</button>'}${t.author === AUTHOR ? '<button data-a="edit">Edit</button>' : ''}<button data-a="del">Delete thread</button></div></span></span></div>
      ${t.unanchored && !state.awaitingFile ? '<div class="lbl"><em>anchored text no longer in the article</em></div>' : ''}${waitNote}
      <div class="tx">${esc(t.text)}</div>${replies}
      ${t.status === 'offered' && t.edit ? `<div class="alt"><button class="use" data-a="use" title="Put Claude's version in the text">Use</button><span>${esc(plain(t.edit.replace)).slice(0, 700)}</span></div>` : ''}
      ${t.resolved ? '<div class="res">Resolved</div>' : `<div class="reply"><textarea placeholder="Reply (Ctrl+Enter)">${esc((state.replyDrafts || {})[t.id] || '')}</textarea><button data-a="reply" title="Post reply (Ctrl+Enter)">&#10148;</button></div>`}`;
    d.onclick = e => { if (!e.target.closest('button,textarea,.dd')) focusThread(t.id, false); };
    d.querySelector('[data-a="more"]').onclick = e => { e.stopPropagation(); d.querySelector('.dd').classList.toggle('open'); };
    const on = (a, f) => { const b = d.querySelector(`[data-a="${a}"]`); if (b) b.onclick = e => { e.stopPropagation(); f(); }; };
    on('resolve', () => resolve(t.id, true)); on('resolve2', () => resolve(t.id, true)); on('reopen', () => resolve(t.id, false)); on('del', () => delThread(t.id));
    on('send', () => sendToClaude(t.id)); on('use', () => useTaskEdit(t));
    on('edit', () => editInPlace(d.querySelector(':scope > .tx'), t.text, nt => editText(t.id, null, nt)));
    d.querySelectorAll('[data-a="edit-r"]').forEach(b => b.onclick = e => { e.stopPropagation(); const rp = b.closest('.rp'); const r = t.replies.find(x => x.id === rp.dataset.rid); editInPlace(rp.querySelector('.tx'), r.text, nt => editText(t.id, r.id, nt)); });
    const ta = d.querySelector('.reply textarea');
    if (ta) { on('reply', () => reply(t.id, ta.value)); ta.onkeydown = e => { if (e.ctrlKey && e.key === 'Enter') reply(t.id, ta.value); }; ta.oninput = () => { (state.replyDrafts = state.replyDrafts || {})[t.id] = ta.value; queueLayout(); }; }
    d.onmouseenter = () => { state.hover = t.id; const m = ed().querySelector(`mark.cm[data-cm="${t.id}"]`); if (m) m.classList.add('cm-hover'); drawLines(); };
    d.onmouseleave = () => { state.hover = null; ed().querySelectorAll('mark.cm.cm-hover').forEach(m => m.classList.remove('cm-hover')); drawLines(); };
    return d;
  }
  // ------------------------------------------------------------------ margin geometry
  const anchorOf = card => card.dataset.id ? ed().querySelector(`[data-id="${card.dataset.id}"]`)
                         : card.dataset.cm ? ed().querySelector(`mark.cm[data-cm="${card.dataset.cm}"]`)
                         : card.dataset.lint ? ed().querySelector(`mark.lint[data-lint="${card.dataset.lint}"]`) : null;
  function anchorRect(card) {                // first on-page box of the anchor; a hover preview may hide one of its marks
    const sel = card.dataset.id ? `[data-id="${card.dataset.id}"]` : card.dataset.cm ? `mark.cm[data-cm="${card.dataset.cm}"]` : `mark.lint[data-lint="${card.dataset.lint}"]`;
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
    let ai = rows.findIndex(r => (r.c.dataset.id || r.c.dataset.cm || r.c.dataset.lint) === state.active); if (ai < 0) ai = 0;
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
      const id = card.dataset.id || card.dataset.cm || card.dataset.lint, on = state.active === id || state.hover === id;
      const x0 = cr.right, y0 = cr.top + 15, x1 = ar.left - 3, y1 = ar.top + ar.height / 2;
      const kind = card.classList.contains('rv-lint') ? 'lint' : card.classList.contains('rv-cm') ? 'cm' : card.classList.contains('mv') ? 'mv' : card.classList.contains('ed') ? 'ed' : 'rm';
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
    state.decided = r.decided || [];
    state.previewChanges = r.changes || [];
    toggle(true); setFilter('all'); setView('list');
    const file = rel.split('/').pop(), nc = (r.comments.threads || []).filter(t => !t.resolved).length, nch = (r.changes || []).length;
    toast(`${nc} comment${nc === 1 ? '' : 's'}${nch ? ` and ${nch} change${nch === 1 ? '' : 's'}` : ''} waiting in ${file}. Click to open it.`, 15000, openWaiting);
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
    if (!state.rel || state.awaitingFile || state.disk == null || window.__saving) return;
    const now = await fetchDisk(state.rel);
    if (now == null || now === state.disk || state.diskWarned) return;
    if (window.__saving || now === window.__lastWritten) { state.disk = now; return; }   // this editor's own save
    state.diskWarned = true;
    const b = $('rv-banner'); b.innerHTML = 'This article changed on disk since you opened it, from a commit in another window or another editor. ' +
      'Reopen it before saving or your save will overwrite that. <button id="rv-banner-x">Dismiss</button>';
    b.style.display = 'block'; $('rv-banner-x').onclick = () => { b.style.display = 'none'; };
    toggle(true);
  }
  window.addEventListener('focus', checkDisk);
  setInterval(checkDisk, 30000);

  // ---- prose checks: every check the repo has, on the text as it stands, as margin cards ---
  // The server runs tools/prose_check.py on the editor's own HTML, so unsaved edits count and
  // nothing on disk is touched. A mechanical finding carries a fix, applied here in place.
  async function runLint() {
    if (!ed()) return;
    const b = $('rv-lint'); b.disabled = true; b.textContent = 'Checking…';
    ed().querySelectorAll('mark.lint').forEach(unwrap); state.lint = [];
    let r; try { r = await (await fetch(API + '/review/lint', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ html: ed().innerHTML }) })).json(); }
    catch (e) { toast('Server not reachable'); b.disabled = false; b.textContent = 'Prose'; return; }
    state.lint = r.findings || [];
    for (const f of state.lint) { if (!f.anchor) continue; const rg = findRange(f.anchor); if (rg) wrapRange(rg, f.id, false, 'lint'); else f.anchor = null; }
    state.voice = null;                       // the Voice line is off (2026-09-27); the word cards stay
    b.disabled = false; b.textContent = 'Prose';
    if (state.filter === 'changes') setFilter('all');
    toggle(true); renderAll();
    toast(state.lint.length ? `${state.lint.length} prose finding(s).` : 'Prose: clean.');
  }
  // One line: "Voice 84 · 21-word sentences (his 16) · ...". Only the signals that are off are
  // listed; click to see all of them. The score is arithmetic against his live pages
  // (tools/voice_check.py), not an opinion.
  function renderVoice() {
    const el = $('rv-voice'); if (!el) return;
    const v = state.voice;
    if (!v || v.score == null) { el.style.display = 'none'; return; }
    el.style.display = '';
    const cls = v.score >= 85 ? 'good' : v.score >= 70 ? 'ok' : 'off';
    let h = `<span class="sc ${cls}">Voice ${v.score}</span>`;
    const off = v.lines.filter(l => !l.ok);
    if (!state.voiceOpen) {
      h += off.length ? off.map(l => `<span class="sg">${esc(l.label)} <b>${l.value}</b> <i>(his ${l.base})</i></span>`).join('')
                      : `<span class="sg">reads like you · ${v.words} words</span>`;
    } else {
      h += `<span class="sg">${v.words} words · vs ${v.baseline_pages || ''} live pages</span>` +
           v.lines.map(l => `<span class="sg ${l.ok ? '' : 'off'}">${esc(l.label)} <b>${l.value}</b> <i>(his ${l.base})</i></span>`).join('');
    }
    el.innerHTML = h;
  }
  function useRewrite(f, text) {
    const marks = [...ed().querySelectorAll(`mark.lint[data-lint="${f.id}"]`)];
    if (!marks.length) return;
    marks[0].textContent = text; marks.slice(1).forEach(m => { m.textContent = ''; });
    marks.forEach(unwrap);
    state.lint = state.lint.filter(x => x.id !== f.id);
    dirty(); if (H()) H().touch(); renderAll();
  }
  function lintFix(f) {
    const marks = [...ed().querySelectorAll(`mark.lint[data-lint="${f.id}"]`)];
    if (!marks.length || f.fix == null) return;
    marks[0].textContent = f.fix; marks.slice(1).forEach(m => { m.textContent = ''; });
    marks.forEach(unwrap);
    state.lint = state.lint.filter(x => x.id !== f.id);
    dirty(); if (H()) H().touch(); renderAll();
  }
  function lintDrop(f) {
    ed().querySelectorAll(`mark.lint[data-lint="${f.id}"]`).forEach(unwrap);
    state.lint = state.lint.filter(x => x.id !== f.id); renderAll();
  }
  function lintCard(f) {
    const d = document.createElement('div');
    d.className = 'rv-card rv-lint ' + (f.severity || 'warn') + (state.active === f.id ? ' active' : ''); d.dataset.lint = f.id;
    if (f.alternatives) {
      d.innerHTML = `<div class="who">${CLAUDE_MARK}<b>rewrite</b><span class="tm"></span>
          <span class="acts"><button class="no" data-a="drop" title="Keep the sentence as it is">&#10005;</button></span></div>
        <div class="t"><del>${esc(f.anchor.quote)}</del></div>
        ${f.alternatives.map((t, i) => `<div class="alt"><button class="use" data-i="${i}" title="Put this one in the text">Use</button><span>${esc(t)}</span></div>`).join('')}
        <div class="m">${esc(f.message)}</div>`;
      d.onclick = e => { if (!e.target.closest('button')) { state.active = f.id; const m = ed().querySelector(`mark.lint[data-lint="${f.id}"]`); if (m) reveal(m); renderAll(); } };
      d.querySelectorAll('.use').forEach(b => { b.onclick = e => { e.stopPropagation(); useRewrite(f, f.alternatives[+b.dataset.i]); }; });
      d.querySelector('[data-a="drop"]').onclick = e => { e.stopPropagation(); lintDrop(f); };
      return d;
    }
    const q = f.anchor ? `<div class="t">&ldquo;${esc(f.anchor.quote)}&rdquo;${f.fix != null ? ` &rarr; <ins>${esc(f.fix)}</ins>` : ''}</div>` : '';
    d.innerHTML = `<div class="who"><span class="av lint">!</span><b>${esc(f.kind)}</b><span class="tm">${esc(f.severity || '')}</span>
        <span class="acts">${f.fix != null ? '<button class="yes" data-a="fix" title="Apply this fix">&#10003;</button>' : ''}<button class="no" data-a="drop" title="Dismiss">&#10005;</button></span></div>
      ${q}<div class="m">${esc(f.message)}</div>`;
    d.onclick = e => { if (!e.target.closest('button')) { state.active = f.id; const m = ed().querySelector(`mark.lint[data-lint="${f.id}"]`); if (m) m.scrollIntoView({ block: 'center' }); renderAll(); } };
    const fx = d.querySelector('[data-a="fix"]'); if (fx) fx.onclick = e => { e.stopPropagation(); lintFix(f); };
    d.querySelector('[data-a="drop"]').onclick = e => { e.stopPropagation(); lintDrop(f); };
    d.onmouseenter = () => { state.hover = f.id; ed().querySelectorAll(`mark.lint[data-lint="${f.id}"]`).forEach(m => m.classList.add('cm-hover')); drawLines(); };
    d.onmouseleave = () => { state.hover = null; ed().querySelectorAll('mark.lint.cm-hover').forEach(m => m.classList.remove('cm-hover')); drawLines(); };
    return d;
  }

  // ---- Maps placeholders -> real pins, in the text the writer is looking at -----------------
  const MAPS_SEARCH = 'https://www.google.com/maps/search/?api=1&query=';
  function mapsPlaceholders() { return [...ed().querySelectorAll('a[href]')].filter(a => (a.getAttribute('href') || '').startsWith(MAPS_SEARCH)); }
  function mapsQuery(a) { const raw = a.getAttribute('href').slice(MAPS_SEARCH.length).split('&')[0]; try { return decodeURIComponent(raw.replace(/\+/g, ' ')).trim(); } catch (e) { return raw; } }
  function mapsCount() { const n = mapsPlaceholders().length; const b = $('rv-maps'); if (b) { b.style.display = n ? '' : 'none'; $('rv-n-maps').textContent = n; } return n; }
  async function resolveMaps() {
    const links = mapsPlaceholders(); if (!links.length) return;
    const qs = [...new Set(links.map(mapsQuery))];
    const b = $('rv-maps'); b.disabled = true; b.firstChild.textContent = 'Resolving… ';
    toast(`Looking up ${qs.length} place(s) on Google Maps. About ten seconds each the first time.`, 8000);
    let r; try { r = await (await fetch(API + '/review/resolve-maps', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ queries: qs }) })).json(); }
    catch (e) { toast('Server not reachable'); b.disabled = false; b.firstChild.textContent = 'Resolve maps '; return; }
    let done = 0, miss = [];
    for (const a of links) { const u = (r.urls || {})[mapsQuery(a)]; if (u) { a.setAttribute('href', u); done++; } else miss.push(mapsQuery(a)); }
    b.disabled = false; b.firstChild.textContent = 'Resolve maps ';
    if (done) { dirty(); if (H()) H().touch(); }
    mapsCount();
    toast(done ? `${done} link(s) now point at the place.${miss.length ? ' Could not resolve: ' + miss.join('; ') : ''}` : 'Nothing resolved: ' + (r.log || miss.join('; ')), 8000);
  }

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
    resnapshot: () => state.rel && snapshotDisk(state.rel),
    toggle, prepareForSave, onSaved, syncFromDom, decide, jump, newComment, state, refresh, preview, checkDisk, layout, previewDecision, setFilter, setView, trimTags, pollTasks, sendToClaude,
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
.rv-tools{display:flex;gap:.25rem;flex-wrap:wrap;align-items:center;padding:.45rem .5rem;border-bottom:1px solid #e6e6e2;background:#fff}
.rv-tools .rv-btn,.rv-tools .rv-seg button{padding:.38rem .42rem;letter-spacing:.04em}   /* one row at the pane's 371px (2026-09-27) */
.rv-seg{display:inline-flex;border:1px solid #e0ded8;border-radius:3px;overflow:hidden}.rv-seg button{border:0!important;border-radius:0!important;border-right:1px solid #e0ded8!important}.rv-seg button:last-child{border-right:0!important}
#review-pane .primary{background:#2D6B50;color:#fff;border-color:#2D6B50}
.rv-more{margin-left:auto;position:relative}
#review-pane [hidden]{display:none!important}
.rv-switch{display:inline-flex;background:#F0F0EB;border-radius:7px;padding:2px;gap:2px}
#review-pane .rv-switch button{font:600 .6rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.06em;text-transform:uppercase;border:0;background:transparent;color:#4f5c54;padding:.42rem .6rem;border-radius:5px;cursor:pointer}
#review-pane .rv-switch button[aria-pressed="true"]{background:#fff;color:#1C2821;box-shadow:0 1px 3px rgba(28,40,33,.14)}
.rv-dd{display:none;position:absolute;right:0;top:1.5rem;background:#fff;border:1px solid #e0ded8;border-radius:3px;box-shadow:0 4px 14px rgba(0,0,0,.08);z-index:6;min-width:170px}
.rv-more.open .rv-dd{display:block}.rv-dd button{display:block;width:100%;text-align:left;border:0!important;border-radius:0!important;text-transform:none!important;letter-spacing:0!important;font-size:.78rem!important;padding:.45rem .7rem!important}
.rv-dd button:hover{background:#F5F5F2}
.rv-voice{display:flex;flex-wrap:wrap;gap:.25rem .5rem;align-items:center;font:.7rem/1.4 'Hanken Grotesk',sans-serif;color:#4c5a52;padding:.35rem .6rem;border-bottom:1px solid #e6e6e2;background:#fff;cursor:pointer}
.rv-voice .sc{font:600 .74rem/1 'Hanken Grotesk',sans-serif;letter-spacing:0;text-transform:none;padding:.25rem .4rem;border-radius:3px;color:#fff;background:#6b7a70}
.rv-voice .sc.good{background:#2D6B50}.rv-voice .sc.ok{background:#9A7B2E}.rv-voice .sc.off{background:#B4553C}
.rv-voice .sg{white-space:nowrap}.rv-voice .sg.off{color:#B4553C}.rv-voice .sg i{color:#8a9790;font-style:normal}
.rv-card .acts .claude{width:26px;height:26px;border:1px solid transparent;border-radius:50%;background:none;cursor:pointer;color:#D97757;padding:0;display:inline-flex;align-items:center;justify-content:center}
.claude-mark{width:16px;height:16px;border-radius:4px;vertical-align:-3px;display:inline-block}
.rv-card .who .claude-mark{width:22px;height:22px;border-radius:50%;flex-shrink:0}
.rv-card .acts .claude:hover{background:#FBEDE6;border-color:#eec3b3}
.rv-card .act .send svg{vertical-align:-3px;margin-right:.15rem;color:#D97757}
.rv-card .chip{font:600 .55rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.06em;text-transform:uppercase;padding:.22rem .4rem;border-radius:999px;margin-left:.35rem;white-space:nowrap}
.rv-card .chip.sent{background:#FBEFC2;color:#5c4a12;display:inline-flex;align-items:center;gap:.3rem}
.rv-card .chip .spin{width:9px;height:9px;border:1.5px solid #c9a94a;border-top-color:transparent;border-radius:50%;animation:rvspin .8s linear infinite;display:inline-block}
@keyframes rvspin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion: reduce){.rv-card .chip .spin{animation:none;border-top-color:#c9a94a;opacity:.6}}
.rv-card .wait-note{color:#5c4a12;background:#FFF8E1;border:1px solid #F1E2A8;border-radius:4px;padding:.35rem .5rem;margin:.35rem 0 0;font-size:.72rem;line-height:1.4}.rv-card .chip.done{background:#E3F0E8;color:#2D6B50}.rv-card .chip.wait{background:#E8EEF7;color:#2f4b78}.rv-card .chip.fail{background:#F8E5E0;color:#B4553C}
.rv-card .act .send{border-color:#b9d4c5;color:#2D6B50}
.rv-check{display:flex;flex-wrap:wrap;gap:.25rem;padding:.3rem .6rem;border-bottom:1px solid #e6e6e2;background:#fff;min-height:0}
.rv-check:empty{display:none}
.rv-check button{font:600 .55rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.05em;text-transform:uppercase;padding:.3rem .45rem;border:1px solid #F1E2A8;background:#FFF8E1;color:#5c4a12;border-radius:999px;cursor:pointer}
.rv-check button:hover{background:#FBEFC2}
.rv-check .ok{font:600 .55rem/1.6 'Hanken Grotesk',sans-serif;letter-spacing:.05em;text-transform:uppercase;color:#2D6B50}
.rv-card .rp-open{font:inherit;color:#2D6B50;background:none;border:0;border-bottom:1px dotted #2D6B50;padding:0;cursor:pointer}
#rv-quick{position:fixed;z-index:9999;background:#fff;border:1px solid #e0ded8;border-radius:6px;box-shadow:0 8px 24px rgba(0,0,0,.14);padding:.3rem;min-width:170px;font:13px/1.3 'Hanken Grotesk',sans-serif}
#rv-quick .qh{display:flex;align-items:center;gap:.35rem;font:600 .58rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#8a9790;padding:.35rem .45rem .45rem}
#rv-quick button{display:block;width:100%;text-align:left;border:0;background:none;padding:.42rem .55rem;border-radius:4px;cursor:pointer;color:#1C2821;font:inherit}
#rv-quick button:hover{background:#F3F6F4}
#rv-quick .qc{border-top:1px solid #eee;border-radius:0 0 4px 4px;color:#6b7a70}
.rv-card .alt{display:flex;gap:.45rem;align-items:flex-start;margin:.3rem 0;font-size:.8rem;line-height:1.4}
.rv-card .alt .use{flex-shrink:0;font:600 .58rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.06em;text-transform:uppercase;padding:.3rem .45rem;border:1px solid #b9d4c5;background:#E3F0E8;color:#2D6B50;border-radius:3px;cursor:pointer}
.rv-card .alt .use:hover{background:#2D6B50;color:#fff}
.rv-status:empty{display:none}
.rv-status{font:400 .78rem/1.4 'Hanken Grotesk',sans-serif;letter-spacing:0;text-transform:none;color:#6b7a70;padding:.4rem .6rem;border-bottom:1px solid #e6e6e2;background:#fff}
.rv-body{flex:1;overflow:hidden;position:relative}.rv-body.list{overflow:auto}.rv-list{padding:.5rem}.rv-canvas{position:relative;height:100%}
#rv-banner{background:#FBEFC2;color:#5c4a12;font-size:.78rem;padding:.5rem .6rem;border-bottom:1px solid #E8C86A}#rv-banner button{font:600 .6rem/1 'Hanken Grotesk',sans-serif;margin-left:.3rem;border:1px solid #E8C86A;background:#fff;border-radius:3px;padding:.25rem .4rem;cursor:pointer}
.rv-dec-h{display:block;width:100%;text-align:left;background:none;border:0;border-top:1px solid #e6e6e2;margin-top:.6rem;font:600 .62rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#8a9790;padding:1rem .6rem .5rem;cursor:pointer}.rv-dec-h:hover{color:#2D6B50}.rv-dec-h::before{content:'▸';display:inline-block;margin-right:.4rem;transition:transform .15s}.rv-dec-h.open::before{transform:rotate(90deg)}.rv-dec-h:first-child{border-top:0;margin-top:0;padding-top:.5rem}.rv-dec-h:focus-visible{outline:2px solid #2D6B50;outline-offset:-2px}
.rv-card.rv-done{cursor:pointer}.rv-card.rv-done .who b{font-weight:600}.rv-card.rv-done .t{opacity:.8;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}.rv-card.rv-done.open .t{display:block}
.rv-done .act{display:flex;gap:.3rem;margin-top:.45rem}.rv-done .act button{font:600 .6rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.06em;text-transform:uppercase;padding:.35rem .55rem;border:1px solid #e0ded8;background:#fff;border-radius:3px;cursor:pointer;color:#1C2821}.rv-done .act button:hover{border-color:#b9d4c5;color:#2D6B50}.rv-done .act button:focus-visible{outline:2px solid #2D6B50;outline-offset:1px}
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
.rv-c .lbl{display:flex;align-items:center;gap:.35rem;font:400 .74rem/1.2 'Hanken Grotesk',sans-serif;letter-spacing:0;text-transform:none;color:#6b7a70;margin-top:.35rem}
.rv-c .lbl em{font-style:normal;color:#9A7B2E;text-transform:none;letter-spacing:0}
.rv-c .num{background:#8a9790;color:#fff;border-radius:8px;padding:.15rem .4rem;font-size:.56rem}
.rv-c.rm .num{background:#B4553C}.rv-c.ed .num{background:#2557A7}.rv-c.mv .num{background:#2D6B50}
.rv-c .t{margin:.3rem 0 0;line-height:1.45}.rv-c .t del{color:#B4553C;text-decoration:line-through}.rv-c.mv .t del{color:#2D6B50;text-decoration:line-through double;text-decoration-thickness:1px}.rv-c .t ins{color:#2557A7;text-decoration:underline}
.rv-c .m{font-size:.72rem;color:#6b7a70;margin-top:.3rem}.rv-c .m b{color:#2D6B50}.rv-c .m .lose{color:#B4553C}
.rv-cm .act{display:flex;gap:.3rem;margin-top:.45rem}
.rv-cm button{font:600 .6rem/1 'Hanken Grotesk',sans-serif;letter-spacing:.06em;text-transform:uppercase;padding:.35rem .55rem;border:1px solid #e0ded8;background:#fff;border-radius:3px;cursor:pointer;color:#1C2821}
.rv-c .rv-sub{margin:.55rem 0 0;padding:.45rem 0 0;border-top:1px dashed #e0ded8}
.rv-c .rv-sub .lbl2{font:400 .74rem/1.2 'Hanken Grotesk',sans-serif;letter-spacing:0;text-transform:none;color:#8a9790;margin-bottom:.3rem}
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
.rv-cm .res{font:400 .74rem/1.2 'Hanken Grotesk',sans-serif;letter-spacing:0;text-transform:none;color:#2D6B50;margin-top:.45rem}
#review-pane .rv-open{margin-top:.5rem}
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
#editor ins.rl.struct{text-decoration:none!important;background:none}#editor ins.rl.struct::before{content:'\\00b6  layout change';font:400 .72rem/1 'Hanken Grotesk',sans-serif;letter-spacing:0;color:#6b5a1e;background:#FFF3B0;border:1px solid #e6d27a;border-radius:3px;padding:.12rem .35rem;margin-right:.2rem;vertical-align:middle}
#editor sup.rl-n{font:600 .62rem/1 'Hanken Grotesk',sans-serif!important;color:#fff!important;background:#8a9790;border-radius:8px;padding:.15rem .3rem;margin-left:.15rem;vertical-align:super;cursor:pointer;user-select:none;text-decoration:none!important}
#editor .rl-hit del.rl,#editor .rl-hit ins.rl,#editor del.rl-hover,#editor ins.rl-hover{background:#FFF3B0}#editor .rl-hit sup.rl-n,#editor sup.rl-hover{background:#1C2821}
#editor :is(p,li,div.copy):has(del.rl,ins.rl){box-shadow:-2px 0 0 0 #C9C3B4;padding-left:.5rem}
#editor.rl-final del.rl,#editor.rl-final sup.rl-n{display:none}#editor.rl-final ins.rl{color:inherit!important;text-decoration:none!important;background:none}
#editor.rl-final :is(p,li,div.copy):has(del.rl,ins.rl){box-shadow:none;padding-left:0}
#editor.rl-final ins.blk{box-shadow:none;padding-left:0}#editor.rl-final ins.blk *{color:inherit!important;text-decoration:none}
#editor.rl-final ins.rl.struct::before{display:none}
#editor.rl-final mark.cm,#editor.rl-final mark.lint{background:none!important;border-bottom:0!important;cursor:text}
#editor.rl-final .rl-hit del.rl,#editor.rl-final .rl-hit ins.rl,#editor.rl-final ins.rl-hover{background:none}
/* hover preview of a tick or cross: the text as it would read after that decision */
#editor del.rl-pv-yes,#editor sup.rl-pv-yes,#editor sup.rl-pv-no,#editor ins.rl-pv-no{display:none}
#editor ins.rl-pv-yes,#editor del.rl-pv-no{color:inherit!important;text-decoration:none!important;background:#E3F0E8!important}
#editor del.rl-pv-no{background:#FBEFC2!important}
#editor mark.cm{background:#F1EFE8;color:inherit;border-bottom:2px solid #D9BE63;cursor:pointer}
#editor mark.cm.draft{background:#E3F0E8;border-bottom-color:#2D6B50}
#editor mark.cm.cm-hit,#editor mark.cm.cm-hover{background:#F5D96A}
#editor mark.lint{background:#FBEFC2;color:inherit;border-bottom:2px solid #E8C86A;cursor:pointer}
#editor mark.lint.cm-hover{background:#F5D96A}
.rv-lint{border-left:3px solid #E8C86A}.rv-lint.error{border-left-color:#B4553C}.rv-lint.info{border-left-color:#8a9790}
.rv-lint .av.lint{background:#9A7B2E}.rv-lint .t{margin:.3rem 0 0;line-height:1.45}.rv-lint .t ins{color:#2557A7;text-decoration:none;font-weight:600}
.rv-lint .m{font-size:.72rem;color:#6b7a70;margin-top:.3rem}
#rv-lines .ln.lint{stroke:#E8C86A}#rv-lines .dt.lint{fill:#E8C86A}
`; document.head.appendChild(css);
})();
