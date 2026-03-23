(function(){
  var themes = ['','theme-midnight','theme-editorial','theme-jungle'];
  var labels = ['Parchment','Midnight','Editorial','Jungle'];

  var saved = localStorage.getItem('gag-theme') || '';
  var idx = themes.indexOf(saved);
  if(idx < 0) idx = 0;

  if(themes[idx]) document.documentElement.classList.add(themes[idx]);

  document.addEventListener('DOMContentLoaded', function(){
    var btn = document.createElement('button');
    btn.id = 'theme-switcher-btn';
    btn.innerHTML = '&#9681; ' + labels[idx];
    btn.style.cssText = [
      'position:fixed','bottom:1.5rem','right:1.5rem','z-index:9999',
      'padding:.45rem 1rem','font-family:"DM Mono",monospace','font-size:.58rem',
      'letter-spacing:.15em','text-transform:uppercase','border-radius:20px',
      'border:1px solid currentColor','cursor:pointer','transition:opacity .2s',
      'opacity:.55','background:var(--cream)','color:var(--ink)','line-height:1.4'
    ].join(';');

    btn.onmouseover = function(){ btn.style.opacity = '1'; };
    btn.onmouseout  = function(){ btn.style.opacity = '.55'; };

    btn.onclick = function(){
      if(themes[idx]) document.documentElement.classList.remove(themes[idx]);
      idx = (idx + 1) % themes.length;
      if(themes[idx]) document.documentElement.classList.add(themes[idx]);
      localStorage.setItem('gag-theme', themes[idx]);
      btn.innerHTML = '&#9681; ' + labels[idx];
    };

    document.body.appendChild(btn);
  });
})();
