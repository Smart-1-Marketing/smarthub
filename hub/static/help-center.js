(function () {
  'use strict';
  let catalog;
  const el = id => document.getElementById(id);
  function article(item, target) {
    const details = document.createElement('details'), summary = document.createElement('summary'), body = document.createElement('p');
    summary.textContent = item.title; body.textContent = item.body;
    details.append(summary, body); target.appendChild(details);
    if (item.link && (/^\/(?!\/)/.test(item.link) || /^https:\/\//.test(item.link))) {
      const link = document.createElement('a'); link.href = item.link;
      link.textContent = item.linkText || 'Open this tool'; details.appendChild(link);
    }
  }
  function filter() {
    const words = el('help-search').value.toLowerCase().split(/\s+/).filter(Boolean);
    let total = 0;
    ['articles', 'videos', 'walkthroughs'].forEach(type => {
      const target = el('help-' + type); target.replaceChildren();
      const rows = catalog[type].filter(row => words.every(w => JSON.stringify(row).toLowerCase().includes(w)));
      total += rows.length;
      if (!rows.length) target.textContent = type === 'videos' && !catalog.videos.length ? 'No recorded video tutorials have been added yet. Explore the guided walkthroughs below.' : 'No matching results.';
      rows.forEach(row => {
        if (type === 'articles') return article(row, target);
        const a = document.createElement('a'); a.className = 'help-resource'; a.href = row.url || row.path;
        a.textContent = row.title + (row.minutes ? ' · ' + row.minutes + ' min walkthrough' : '');
        if (type === 'videos') { a.target = '_blank'; a.rel = 'noopener'; }
        target.appendChild(a);
      });
    });
    el('help-load-status').textContent = total + ' resources';
  }
  fetch('/api/help-center/catalog').then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(d => { catalog = d; filter(); }).catch(() => { el('help-load-status').textContent = 'Help could not load. Refresh to try again.'; });
  el('help-search').addEventListener('input', () => { if (catalog) filter(); });
  el('help-ask').onsubmit = async e => {
    e.preventDefault(); const button = e.target.querySelector('button'); button.disabled = true;
    el('help-answer').textContent = 'Looking through the help documentation…'; el('help-sources').replaceChildren();
    try {
      const r = await fetch('/api/help-center/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:el('help-question').value})});
      const d = await r.json(); if (!r.ok) throw new Error(d.error || 'Unable to answer right now.');
      el('help-answer').textContent = d.answer;
      (d.sources || []).forEach(s => article(s, el('help-sources')));
    } catch (error) { el('help-answer').textContent = error.message; }
    finally { button.disabled = false; }
  };
})();
