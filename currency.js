/* Currency converter for the itineraries' Currency section (Kevin, 2026-11-08).
   Renders every <div class="cconv" data-local="MKD" data-local-name="Denar" data-flag="mk"
   data-asof="2026-11-08" data-rates="USD:1 EUR:0.8958 ... MKD:54.93"> into two outlined boxes:
   a currency menu + amount on the left, the local currency on the right. Rates are per US dollar,
   written into the page by tools/currency_widget.py. With JS off the div's own fallback line stays.
   Styles: the "currency converter" block in artifact.css. */
(function () {
  var MENU = [['USD', 'US dollar'], ['EUR', 'Euro'], ['GBP', 'British pound'],
              ['AUD', 'Australian dollar'], ['CAD', 'Canadian dollar'], ['CHF', 'Swiss franc']];
  var FLAG = { USD: 'us', EUR: 'eu', GBP: 'gb', AUD: 'au', CAD: 'ca', CHF: 'ch' };
  var NODEC = ['JPY', 'KRW', 'HUF', 'IDR', 'CLP', 'COP', 'TZS', 'VND', 'ISK'];
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var me = document.currentScript;
  var flagBase = 'Images/web/flags/';
  try { flagBase = new URL('Images/web/flags/', me && me.src ? me.src : location.href).href; } catch (e) {}

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function digits(v) { return String(v).replace(/[^0-9.]/g, '').replace(/(\..*)\./g, '$1'); }
  function fmtInput(v) {
    var p = digits(v).split('.');
    var w = p[0] ? Number(p[0]).toLocaleString('en-US') : (p.length > 1 ? '0' : '');
    return p.length > 1 ? w + '.' + p[1].slice(0, 2) : w;
  }
  function parse(v) { var n = parseFloat(digits(v)); return isFinite(n) ? n : 0; }
  function plain(v, code) {
    if (v >= 100 || NODEC.indexOf(code) > -1) return Math.round(v).toLocaleString('en-US');
    return (Math.round(v * 100) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function flagImg(iso) { return '<img class="cconv-fl" src="' + esc(flagBase + iso + '.png') + '" alt="" width="20" height="14">'; }
  function asOf(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    return m ? MONTHS[+m[2] - 1] + ' ' + (+m[3]) + ', ' + m[1] : '';
  }
  function rates(s) {
    var out = {};
    String(s || '').split(/[\s,;]+/).forEach(function (pair) {
      var kv = pair.split(':'), n = parseFloat(kv[1]);
      if (kv[0] && isFinite(n) && n > 0) out[kv[0].toUpperCase()] = n;
    });
    return out;
  }
  // keep the caret on the same digit while commas come and go
  function bindFormat(inp, onChange) {
    inp.addEventListener('input', function () {
      var before = inp.value, caret = inp.selectionStart, keep = digits(before.slice(0, caret)).length;
      inp.value = fmtInput(before);
      var i = 0, seen = 0;
      while (i < inp.value.length && seen < keep) { if (/[0-9.]/.test(inp.value[i])) seen++; i++; }
      try { inp.setSelectionRange(i, i); } catch (e) {}
      onChange();
    });
  }

  function render(root) {
    var R = rates(root.getAttribute('data-rates'));
    var local = (root.getAttribute('data-local') || '').toUpperCase();
    var localName = root.getAttribute('data-local-name') || local;
    var localFlag = root.getAttribute('data-flag') || '';
    if (!local || !R[local]) return;
    var menu = MENU.filter(function (c) { return c[0] !== local && R[c[0]]; });
    if (!menu.length) return;
    var opts = menu.map(function (c, i) {
      return '<option value="' + c[0] + '"' + (i === 0 ? ' selected' : '') + '>' + c[0] + ' · ' + esc(c[1]) + '</option>';
    }).join('');
    var when = asOf(root.getAttribute('data-asof'));
    root.innerHTML = '<div class="cconv-grid">'
      + '<div class="cconv-side"><div class="cconv-selwrap"><span class="cconv-tf"></span><span class="cconv-selbox">'
      + '<select class="cconv-sel" aria-label="Currency">' + opts + '</select></span></div>'
      + '<input class="cconv-amt cconv-a" type="text" inputmode="decimal" autocomplete="off" spellcheck="false" value="1" aria-label="Amount in the chosen currency"></div>'
      + '<div class="cconv-eq" aria-hidden="true">=</div>'
      + '<div class="cconv-side"><div class="cconv-lbl">' + (localFlag ? flagImg(localFlag) : '') + esc(local) + ' · ' + esc(localName) + '</div>'
      + '<input class="cconv-amt cconv-b" type="text" inputmode="decimal" autocomplete="off" spellcheck="false" aria-label="Amount in ' + esc(local) + '"></div>'
      + '</div>'
      + (when ? '<div class="cconv-asof">Mid-market rates, ' + when + '.</div>' : '');
    var a = root.querySelector('.cconv-a'), b = root.querySelector('.cconv-b'),
        sel = root.querySelector('.cconv-sel'), tf = root.querySelector('.cconv-tf');
    function toLocal(n) { return n / R[sel.value] * R[local]; }
    function fromLocal(n) { return n / R[local] * R[sel.value]; }
    function meta() { tf.innerHTML = FLAG[sel.value] ? flagImg(FLAG[sel.value]) : ''; }
    function fromA() { b.value = plain(toLocal(parse(a.value)), local); meta(); }
    function fromB() { a.value = plain(fromLocal(parse(b.value)), sel.value); meta(); }
    bindFormat(a, fromA);
    bindFormat(b, fromB);
    sel.addEventListener('change', function () { a.value = '1'; fromA(); });
    fromA();
    root.classList.add('cconv-on');
  }

  function init() {
    var all = document.querySelectorAll('.cconv[data-local]');
    for (var i = 0; i < all.length; i++) { try { render(all[i]); } catch (e) {} }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
