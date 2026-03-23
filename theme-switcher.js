(function() {
  const themes = [
    { id: '', name: 'Parchment', dot: '#C4673A', bg: '#F5F0E8' },
    { id: 'theme-coastal', name: 'Coastal', dot: '#B85C38', bg: '#FFFFFF' },
    { id: 'theme-sage', name: 'Sage', dot: '#2D6B50', bg: '#F7F7F3' },
    { id: 'theme-dark', name: 'Dark', dot: '#F2C124', bg: '#0D1117' },
  ];

  // Apply saved theme immediately (before DOM loads to avoid flash)
  const saved = localStorage.getItem('gag-theme') || '';
  if (saved) document.documentElement.classList.add(saved);
  let current = themes.findIndex(t => t.id === saved);
  if (current < 0) current = 0;

  document.addEventListener('DOMContentLoaded', function() {
    // Build switcher: a small floating panel with 4 dots
    const wrap = document.createElement('div');
    wrap.id = 'theme-sw';
    wrap.style.cssText = 'position:fixed;bottom:1.5rem;right:1.5rem;z-index:9999;display:flex;flex-direction:column;align-items:center;gap:.4rem;';

    // Label
    const label = document.createElement('div');
    label.style.cssText = 'font-family:"Space Mono",monospace;font-size:.48rem;letter-spacing:.15em;text-transform:uppercase;color:var(--ink);opacity:.35;';
    label.textContent = 'theme';
    wrap.appendChild(label);

    // Dot row
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:.4rem;background:var(--cream);border:1px solid var(--b-subtle);padding:.4rem .5rem;border-radius:20px;';

    const dots = [];
    themes.forEach((t, i) => {
      const btn = document.createElement('button');
      btn.title = t.name;
      btn.style.cssText = `width:14px;height:14px;border-radius:50%;background:${t.dot};border:2px solid ${i === current ? 'var(--ink)' : 'transparent'};cursor:pointer;transition:all .2s;padding:0;`;
      btn.onclick = function() {
        // Remove old
        if (themes[current].id) document.documentElement.classList.remove(themes[current].id);
        current = i;
        // Add new
        if (t.id) document.documentElement.classList.add(t.id);
        localStorage.setItem('gag-theme', t.id);
        // Update dot borders
        dots.forEach((d, j) => {
          d.style.border = j === current ? '2px solid var(--ink)' : '2px solid transparent';
        });
      };
      dots.push(btn);
      row.appendChild(btn);
    });

    wrap.appendChild(row);
    document.body.appendChild(wrap);
  });
})();
