/* The cookie choice for Google Analytics (tools/analytics_snippet.py puts it on every page, after the consent
   defaults). It asks only visitors whose clock is set to a European time zone and who haven't chosen yet; everyone
   else is never asked. Google's own default already keeps analytics off for visitors it places in the EEA, the UK and
   Switzerland until they accept. The choice is kept in this browser (localStorage "gg-consent"). The privacy page's
   "Cookie settings" link calls ggConsent.open() to change it. */
(function () {
  var KEY = 'gg-consent';
  function get() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function set(v) { try { localStorage.setItem(KEY, v); } catch (e) {} }
  function europe() {
    try {
      var tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
      return tz.indexOf('Europe/') === 0 || /^Atlantic\/(Canary|Madeira|Azores|Reykjavik|Faroe)$/.test(tz);
    } catch (e) { return false; }
  }
  function choose(v) {
    set(v);
    if (typeof window.gtag === 'function') window.gtag('consent', 'update', { analytics_storage: v === 'granted' ? 'granted' : 'denied' });
    close();
  }
  var el = null;
  function close() { if (el && el.parentNode) el.parentNode.removeChild(el); el = null; }
  function open() {
    if (el) return;
    var me = document.querySelector('script[src$="consent.js"]');
    var base = me ? me.getAttribute('src').replace(/consent\.js$/, '') : '';
    el = document.createElement('div');
    el.className = 'gg-consent';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'Cookie choice');
    el.innerHTML = '<style>' +
      '.gg-consent{position:fixed;left:16px;right:16px;bottom:calc(16px + env(safe-area-inset-bottom,0px));z-index:9999;max-width:560px;margin:0 auto;' +
      'background:#fff;color:#1C2821;border:1px solid rgba(28,40,33,.14);border-radius:10px;box-shadow:0 8px 28px rgba(28,40,33,.16);' +
      'padding:14px 16px;font:400 14px/1.5 "Hanken Grotesk",system-ui,-apple-system,sans-serif;display:flex;gap:10px 14px;flex-wrap:wrap;align-items:center}' +
      '.gg-consent p{margin:0;flex:1 1 260px}.gg-consent a{color:#2D6B50}' +
      '.gg-consent .gg-b{display:flex;gap:8px;margin-left:auto}' +
      '.gg-consent button{font:600 13px/1 "Hanken Grotesk",system-ui,sans-serif;border-radius:999px;padding:10px 16px;cursor:pointer;border:1px solid #2D6B50}' +
      '.gg-consent .gg-y{background:#2D6B50;color:#fff}.gg-consent .gg-n{background:#fff;color:#2D6B50}' +
      '.gg-consent button:focus-visible{outline:2px solid #2D6B50;outline-offset:2px}' +
      '</style><p>I use Google Analytics cookies to see which guides people read. OK with that? ' +
      '<a href="' + base + 'privacy.html">Privacy</a></p>' +
      '<div class="gg-b"><button type="button" class="gg-n">Decline</button><button type="button" class="gg-y">Accept</button></div>';
    el.querySelector('.gg-y').addEventListener('click', function () { choose('granted'); });
    el.querySelector('.gg-n').addEventListener('click', function () { choose('denied'); });
    document.body.appendChild(el);
  }
  window.ggConsent = { open: open, get: get };
  function start() { if (!get() && europe()) open(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
