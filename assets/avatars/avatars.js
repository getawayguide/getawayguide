/* Comment-author avatars for the editor's comment margin and Queue/inbox (2026-11-07).
   window.avatarFor(author) -> <span class="cm-avatar"> holding an inline SVG badge.
   "Claude", "Shadow reviewer", "Kevin" (case-insensitive) get their drawings; anyone else
   gets a neutral initial badge. Size it with CSS: .cm-avatar { width; height } (default 28px).
   No disc or ring since 2026-11-07 (Kevin: "no background or circular outline"): Claude is coral on any
   ground, the ninja's hood carries a thin currentColor rim, and Kevin's hiker is filled with currentColor,
   so the slot must take the card's text color (review.js .av.pic sets the ink, #1C2821).
   Source drawings: assets/avatars/{claude,shadow,kevin}.svg (keep this file in sync). */
(function () {
  'use strict';
  var SVG = {
  "claude": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 32 32\" aria-hidden=\"true\" focusable=\"false\"><g transform=\"translate(16 16) scale(1.3) translate(-16 -16)\" shape-rendering=\"crispEdges\"><path fill=\"#F25A45\" d=\"M8 8h16v4h4v4h-4v8h-2v-4h-2v4h-2v-4h-4v4h-2v-4h-2v4h-2v-8h-4v-4h4z\"/><path fill=\"#111\" d=\"M10 10h2v2h-2zM20 10h2v2h-2z\"/></g></svg>",
  "shadow": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 32 32\" aria-hidden=\"true\" focusable=\"false\"><g transform=\"translate(.3 2.3) scale(1.25) translate(-5.2 -6.4)\"><circle cx=\"16\" cy=\"17.5\" r=\"10.6\" fill=\"#2B3230\" stroke=\"currentColor\" stroke-width=\".9\"/><path d=\"M24.5 11.6 Q28.6 10 29.2 6.4 Q26.6 9 24 9.4 Z M24.6 12.8 Q29.2 13.6 30.3 10.4 Q27.8 11.6 24.4 11 Z\" fill=\"#3F8A66\"/><rect x=\"6.8\" y=\"9.8\" width=\"18.4\" height=\"2.8\" rx=\"1.4\" fill=\"#3F8A66\"/><rect x=\"8\" y=\"14.2\" width=\"16\" height=\"7\" rx=\"3.5\" fill=\"#F2D2B6\"/><g fill=\"#20262A\"><ellipse cx=\"12.6\" cy=\"17.7\" rx=\"1.7\" ry=\"2\"/><ellipse cx=\"19.4\" cy=\"17.7\" rx=\"1.7\" ry=\"2\"/></g><g fill=\"#fff\"><circle cx=\"13.2\" cy=\"17\" r=\".6\"/><circle cx=\"20\" cy=\"17\" r=\".6\"/></g></g></svg>",
  "kevin": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 32 32\" aria-hidden=\"true\" focusable=\"false\"><g transform=\"translate(16 16) scale(1.16) translate(-16 -16)\"><path fill=\"currentColor\" fill-rule=\"evenodd\" d=\"M20.7 2.5l-1 0l-.7 .3l-.6 .4l-.5 .6l-.3 .6l-.1 .9l.3 .9l.3 .6l-.4 1.2l-.9 .3l-.3 0l-.7-.8l-1-.9l-.9-.5l-1-.1l-1.4 .1l-1.6 .8l-.4 .4l-.1 .3l0 .8l.7 1.4l1.8-.3l.5 .1l-2.1 .6l-.5 .9l-.4 1.2l-.2 1.5l0 1.4l.3 2.4l.6 1.3l.7 .7l.7 .3l.6 0l.1 .1l.1 .7l-.1 1.3l.6 .1l.2-.8l-.1-1.5l1.7-.9l.6 0l.7-.2l.3 .1l.1 1.7l-.1 1l-.6 2l-.9 1.7l-.7 .8l-1.2 1.1l-.3 .5l0 .6l.2 .5l.2 .4l.7 .4l.8 0l.4-.3l1.8-1.7l1.2-1.9l1.1-2.5l.5 1.7l.3 1.5l.1 1.9l.3 .7l.8 .6l.8 0l.8-.6l.2-.5l0-1l-.2-2.1l-.7-2.9l-1.1-2.9l-1.5-2.8l0-.2l1.1-1.3l.9-1.5l-.8 .1l-1 1.4l-.2 .2l0-1.3l-2 .4l-.8 0l-.7-.4l-.3-.6l.2-.7l1.2-3.3l.4-.6l.5-.2l.3 .2l-.7 .2l-.2 .4l-1.4 3.6l0 .5l.4 .5l.6 .2l5.1-1l.4-.4l.1-.4l-.1-.5l-.2-.3l-.4-.2l-.7 0l-2.7 .6l-.1 0l.7-1.9l0-.3l-.2-.6l.4 .1l.1 .6l-.6 1.7l1.2-.2l.3-1.7l.3 .1l.5 .5l.3 .4l.1 .4l.7 .1l-.1-.6l-.6-.9l-.4-.4l-1-.6l.5-1l.5-.1l.8-.3l.7-.7l.4-.8l.1-1.1l-.2-.8l-.5-.8l-.6-.4zM15.5 17.8l.2 .2l-.3 .2l-.1-.1z\"/></g></svg>"
};
  var NAMES = { claude: 'Claude', shadow: 'Shadow reviewer', kevin: 'Kevin' };
  var ALIAS = { 'claude': 'claude', 'shadow reviewer': 'shadow', 'shadow': 'shadow', 'kevin': 'kevin' };

  function keyFor(author) {
    var k = String(author == null ? '' : author).trim().toLowerCase().replace(/[\s_-]+/g, ' ');
    return ALIAS[k] || null;
  }

  function initialSvg(author) {
    var s = String(author == null ? '' : author).trim();
    var ch = s ? Array.from(s)[0].toUpperCase() : '?';
    ch = ch.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" aria-hidden="true" focusable="false">' +
      '<circle cx="16" cy="16" r="15.25" fill="#ECEBE6" stroke="#C9C6BC" stroke-width="1.5"/>' +
      '<text x="16" y="16" dy=".35em" text-anchor="middle" font-family="Hanken Grotesk,-apple-system,Segoe UI,Arial,sans-serif" ' +
      'font-size="15" font-weight="600" fill="#4A514C">' + ch + '</text></svg>';
  }

  function injectStyle() {
    if (document.getElementById('cm-avatar-style')) return;
    var st = document.createElement('style');
    st.id = 'cm-avatar-style';
    /* :where() keeps specificity at zero, so any page rule on .cm-avatar wins */
    st.textContent = ':where(.cm-avatar){display:inline-block;width:28px;height:28px;flex:none;' +
      'vertical-align:middle;line-height:0}' +
      ':where(.cm-avatar) > svg{width:100%;height:100%;display:block}';
    (document.head || document.documentElement).appendChild(st);
  }

  window.avatarFor = function (author) {
    injectStyle();
    var key = keyFor(author);
    var label = key ? NAMES[key] : (String(author == null ? '' : author).trim() || 'Unknown');
    var el = document.createElement('span');
    el.className = 'cm-avatar' + (key ? ' cm-avatar-' + key : ' cm-avatar-initial');
    el.setAttribute('role', 'img');
    el.setAttribute('aria-label', label);
    el.title = label;
    el.innerHTML = key ? SVG[key] : initialSvg(author);
    return el;
  };
  window.avatarFor.keyFor = keyFor;
})();
