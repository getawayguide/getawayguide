/* Getawayguide site search — self-contained, offline-friendly.
   Desktop: an icon in the nav; clicking it reveals an inline underline input,
   and results drop down below. Mobile: no top icon — a "Search" item is added
   to the hamburger drawer, opening a full-screen search.
   Data: search-index.js (window.__SEARCH_INDEX__) built by tools/gen_search_index.py.
   Groups: Countries · Articles · Mentions (word found inside a page's body). */
(function () {
  "use strict";

  // path to site root, from THIS script's own src ("" or "../"), so it's right
  // over http AND from file:// (offline).
  var me = document.currentScript;
  if (!me) {
    var ss = document.getElementsByTagName("script");
    for (var i = ss.length - 1; i >= 0; i--) {
      if (/search\.js(\?|$)/.test(ss[i].getAttribute("src") || "")) { me = ss[i]; break; }
    }
  }
  var PREFIX = ((me && me.getAttribute("src")) || "search.js").replace(/search\.js.*$/, "");

  var data = null, loading = null;
  var overlay, results, statusEl, navWrap, navQ, sInput;

  function esc(s) {
    return s.replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function highlight(text, q) {
    var lo = text.toLowerCase(), ql = q.toLowerCase(), out = "", i = 0, j;
    while ((j = lo.indexOf(ql, i)) >= 0) {
      out += esc(text.slice(i, j)) + "<mark>" + esc(text.slice(j, j + q.length)) + "</mark>";
      i = j + q.length;
    }
    return out + esc(text.slice(i));
  }
  function countOcc(text, q) {
    var lo = text.toLowerCase(), ql = q.toLowerCase(), n = 0, i = 0, j;
    while ((j = lo.indexOf(ql, i)) >= 0) { n++; i = j + ql.length; }
    return n;
  }
  function snippet(text, q) {
    var i = text.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return "";
    var start = Math.max(0, i - 55), end = Math.min(text.length, i + q.length + 130);
    var s = text.slice(start, end);
    if (start > 0) s = "…" + s;
    if (end < text.length) s = s + "…";
    return s;
  }
  function flagImg(code) {
    return '<img class="s-flag" loading="lazy" width="20" height="15" alt="" ' +
      'src="https://flagcdn.com/20x15/' + code + '.png">';
  }

  function ensureData() {
    if (data) return Promise.resolve(data);
    if (loading) return loading;
    statusEl.textContent = "Loading…";
    loading = new Promise(function (resolve) {
      if (window.__SEARCH_INDEX__) {
        data = window.__SEARCH_INDEX__; statusEl.textContent = ""; return resolve(data);
      }
      var s = document.createElement("script");
      s.src = PREFIX + "search-index.js";
      s.onload = function () {
        data = window.__SEARCH_INDEX__ || null;
        statusEl.textContent = data ? "" : "Search is unavailable right now.";
        resolve(data);
      };
      s.onerror = function () { statusEl.textContent = "Search is unavailable right now."; resolve(null); };
      document.head.appendChild(s);
    });
    return loading;
  }

  function render(q) {
    if (!data) return;
    q = (q || "").trim();
    overlay.classList.toggle("filled", q.length >= 2);
    if (q.length < 2) { results.innerHTML = ""; statusEl.textContent = ""; return; }
    var ql = q.toLowerCase(), html = "";

    var countries = data.countries.filter(function (c) { return c.name.toLowerCase().indexOf(ql) >= 0; });
    var articles = data.articles.filter(function (a) { return a.title.toLowerCase().indexOf(ql) >= 0; });
    var shown = {};
    countries.forEach(function (c) { shown[c.url] = 1; });
    articles.forEach(function (a) { shown[a.url] = 1; });
    var mentions = [];
    data.pages.forEach(function (p) {
      if (shown[p.url]) return;
      var n = countOcc(p.text, q);
      if (n) mentions.push({ title: p.title, url: p.url, n: n, snip: snippet(p.text, q) });
    });
    mentions.sort(function (a, b) { return b.n - a.n; });

    function group(t, c) { return '<div class="s-group-h">' + esc(t) + (c ? ' <span class="s-count">' + c + "</span>" : "") + "</div>"; }
    function row(href, inner, cls) { return '<a class="s-row ' + (cls || "") + '" href="' + href + '">' + inner + "</a>"; }
    // The photo on a result row. build_search_thumbs.py writes `thumb` (and the
    // background-position the card uses) onto every country and article in the
    // index; a row without one simply gets no picture rather than a broken box.
    function thumbImg(e) {
      if (!e || !e.thumb) return "";
      return '<span class="s-thumb"><img loading="lazy" decoding="async" ' +
             'width="80" height="60" alt="" src="' + PREFIX + esc(e.thumb) + '"' +
             (e.pos ? ' style="object-position:' + esc(e.pos) + '"' : "") + "></span>";
    }

    if (countries.length) {
      html += group("Countries", countries.length);
      countries.forEach(function (c) {
        html += row(PREFIX + c.url, thumbImg(c) + flagImg(c.flag) +
          '<span class="s-main">' + highlight(c.name, q) + "</span>" +
          '<span class="s-tag">' + (c.kind === "guide" ? "Full guide" : "Field notes") + "</span>");
      });
    }
    if (articles.length) {
      html += group("Articles", articles.length);
      articles.forEach(function (a) {
        html += row(PREFIX + a.url, thumbImg(a) + '<span class="s-main">' + highlight(a.title, q) + "</span>" +
          (a.tag ? '<span class="s-tag">' + esc(a.tag) + "</span>" : ""));
      });
    }
    if (mentions.length) {
      html += group("Mentions", mentions.length);
      mentions.slice(0, 8).forEach(function (m) {
        html += row(PREFIX + m.url + "#:~:text=" + encodeURIComponent(q),
          '<span class="s-main">' + esc(m.title) + ' <span class="s-n">' + m.n + (m.n > 1 ? " mentions" : " mention") + "</span></span>" +
          '<span class="s-snip">' + highlight(m.snip, q) + "</span>", "s-mention");
      });
    }
    results.innerHTML = html;
    statusEl.textContent = html ? "" : 'No results for “' + q + '”';
  }

  function isMobile() { return window.matchMedia("(max-width:768px)").matches; }
  function activeInput() { return isMobile() ? sInput : navQ; }

  function open() {
    overlay.classList.add("open");
    document.body.classList.add("s-open");
    document.body.style.overflow = "hidden";
    if (!isMobile()) navWrap.classList.add("active");
    ensureData().then(function () { render(activeInput().value); });
    setTimeout(function () { activeInput().focus(); }, 30);
  }
  function close() {
    overlay.classList.remove("open");
    document.body.classList.remove("s-open");
    document.body.style.overflow = "";
    if (navWrap) navWrap.classList.remove("active");
  }
  function toggle() { overlay.classList.contains("open") ? close() : open(); }

  var CSS = [
    /* --- nav trigger (desktop) --- */
    "nav .nav-logo{margin-right:auto}",
    ".nav-search{margin-left:1.6rem;display:inline-flex;align-items:center;gap:.45rem}",
    ".nav-search-btn{background:none;border:0;padding:.15rem;cursor:pointer;color:var(--ink);opacity:.5;display:inline-flex;align-items:center;transition:opacity .2s}",
    ".nav-search-btn:hover,.nav-search.active .nav-search-btn{opacity:1}",
    ".nav-search-btn svg{display:block}",
    ".nav-search-q{order:-1;width:0;opacity:0;border:0;border-bottom:1px solid var(--ink);background:none;padding:0;margin:0;font-family:'Montserrat',sans-serif;font-size:.72rem;letter-spacing:.06em;color:var(--ink);outline:none;transition:width .28s ease,opacity .18s,padding .18s}",
    ".nav-search.active .nav-search-q{width:190px;opacity:1;padding:.28rem .1rem}",
    ".nav-search-q::placeholder{color:rgba(28,40,33,.4)}",
    "body.s-open nav{z-index:1001}",
    /* --- results surface --- */
    ".s-overlay{position:fixed;inset:0;z-index:1000;display:none}",
    ".s-overlay.open{display:block}",
    ".s-modal{display:none;position:fixed;top:4.35rem;right:2.5rem;width:460px;max-width:calc(100vw - 2rem);max-height:76vh;overflow-y:auto;background:var(--cream);border:1px solid var(--b-subtle);border-radius:10px;box-shadow:0 22px 55px rgba(28,40,33,.26)}",
    ".s-overlay.open.filled .s-modal{display:block}",
    ".s-inrow{display:none}",
    ".s-status{padding:1rem 1.35rem;font-size:.9rem;color:rgba(28,40,33,.5);font-style:italic}",
    ".s-status:empty{display:none}",
    ".s-group-h{font-family:'Montserrat',sans-serif;font-size:.6rem;letter-spacing:.14em;text-transform:uppercase;color:rgba(28,40,33,.4);padding:1rem 1.35rem .35rem}",
    ".s-count{color:rgba(28,40,33,.3)}",
    ".s-row{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;padding:.5rem 1.35rem;text-decoration:none;color:var(--ink);border-left:2px solid transparent}",
    ".s-row:hover,.s-row:focus{background:var(--mist);border-left-color:var(--terra);outline:none}",
    ".s-flag{border-radius:2px;flex:none;box-shadow:0 0 0 1px var(--b-subtle)}",
    ".s-main{font-family:'Montserrat',sans-serif;font-weight:500;font-size:.8rem;letter-spacing:.01em}",
    ".s-tag{font-family:'Montserrat',sans-serif;font-size:.53rem;letter-spacing:.09em;text-transform:uppercase;color:rgba(28,40,33,.45);border:1px solid var(--b-medium);border-radius:3px;padding:.15rem .38rem}",
    ".s-mention{flex-direction:column;align-items:flex-start;gap:.2rem}",
    ".s-n{font-family:'Montserrat',sans-serif;font-size:.53rem;letter-spacing:.06em;text-transform:uppercase;color:var(--gold)}",
    ".s-snip{font-family:'Montserrat',sans-serif;font-weight:400;font-size:.72rem;line-height:1.55;color:rgba(28,40,33,.6);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}",
    ".s-row mark{background:rgba(94,154,120,.28);color:inherit;border-radius:2px;padding:0 .05em}",
    /* --- mobile: no top icon; search lives in the drawer; full-screen surface --- */
    "@media(max-width:768px){",
    "  .nav-search{margin-left:0}",
    "  .nav-search-q{display:none}",
    "  .nav-hamburger{order:1;margin-left:1.15rem}",
    "  #mobile-nav-drawer .mnav-search a{display:inline-flex;align-items:center;gap:.6rem}",
    "  #mobile-nav-drawer .mnav-search svg{display:block;flex:none;opacity:.85}",
    "  body.s-open nav{z-index:100}",
    "  .s-overlay.open{background:rgba(28,40,33,.4);backdrop-filter:blur(2px)}",
    "  .s-overlay.open .s-modal{display:block;top:1rem;right:1rem;left:1rem;width:auto;max-height:calc(100vh - 2rem);box-shadow:0 22px 55px rgba(28,40,33,.3)}",
    "  .s-inrow{display:flex;align-items:center;border-bottom:1px solid var(--ink);margin:.4rem 1.1rem 0}",
    "  .s-input{display:block;flex:1;border:0;background:none;padding:.9rem .1rem;font-family:'Montserrat',sans-serif;font-size:1.05rem;color:var(--ink);outline:none}",
    "  .s-input::placeholder{color:rgba(28,40,33,.4)}",
    "  .s-close{display:block;background:none;border:0;font-size:1.4rem;line-height:1;color:rgba(28,40,33,.5);cursor:pointer;padding:.2rem .3rem}",
    "}"
  ].join("");

  function build() {
    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var icon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"></circle>' +
      '<line x1="21" y1="21" x2="16.5" y2="16.5"></line></svg>';

    // desktop trigger: inline input + icon, far right of nav
    var nav = document.querySelector("nav");
    if (nav) {
      navWrap = document.createElement("div");
      navWrap.className = "nav-search";
      navWrap.innerHTML = '<input class="nav-search-q" type="text" placeholder="Search" ' +
        'aria-label="Search" autocomplete="off" spellcheck="false">' +
        '<button class="nav-search-btn" type="button" aria-label="Search">' + icon + "</button>";
      nav.appendChild(navWrap);
      navQ = navWrap.querySelector(".nav-search-q");
      navWrap.querySelector(".nav-search-btn").addEventListener("click", toggle);
    }

    // mobile trigger: a "Search" item at the end of the hamburger drawer
    var mlist = document.querySelector("#mobile-nav-drawer .mobile-nav-links");
    if (mlist) {
      var li = document.createElement("li");
      li.className = "mnav-search";
      li.innerHTML = '<a href="#" role="button">' + icon + "<span>Search</span></a>";
      mlist.appendChild(li);
      li.querySelector("a").addEventListener("click", function (e) {
        e.preventDefault(); e.stopPropagation();
        var d = document.getElementById("mobile-nav-drawer"), b = document.getElementById("nav-hamburger");
        if (d) d.classList.remove("open");
        if (b) b.classList.remove("open");
        open();
      });
    }

    // shared results surface (mobile also holds the input)
    overlay = document.createElement("div");
    overlay.className = "s-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-label", "Site search");
    overlay.innerHTML =
      '<div class="s-modal">' +
        '<div class="s-inrow">' + icon.replace('width="16" height="16"', 'width="18" height="18"') +
          '<input class="s-input" type="text" placeholder="Search" aria-label="Search" autocomplete="off" spellcheck="false">' +
          '<button class="s-close" type="button" aria-label="Close">&times;</button>' +
        '</div>' +
        '<div class="s-status"></div>' +
        '<div class="s-results"></div>' +
      '</div>';
    document.body.appendChild(overlay);
    results = overlay.querySelector(".s-results");
    statusEl = overlay.querySelector(".s-status");
    sInput = overlay.querySelector(".s-input");

    overlay.querySelector(".s-close").addEventListener("click", close);
    overlay.addEventListener("mousedown", function (e) { if (e.target === overlay) close(); });

    var t;
    function onType(el) {
      el.addEventListener("input", function () {
        clearTimeout(t); t = setTimeout(function () { if (data) render(el.value); }, 110);
      });
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { var f = results.querySelector(".s-row"); if (f) f.click(); }
      });
    }
    if (navQ) onType(navQ);
    onType(sInput);

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && overlay.classList.contains("open")) close();
      var typing = /^(input|textarea|select)$/i.test((e.target.tagName || ""));
      if (!typing && (e.key === "/" || ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k"))) {
        e.preventDefault(); open();
      }
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
