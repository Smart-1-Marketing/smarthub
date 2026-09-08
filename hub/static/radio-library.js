/* Saved work stays available beside every radio creator. */
(() => {
  document.querySelectorAll('[data-radio-library]').forEach(root => {
    const kind = root.dataset.radioLibrary;
    const base = '/tools/' + ({promo:'radio-promo', fan:'fan-radio', scripts:'radio-scripts'}[kind]);
    const endpoint = base + (kind === 'promo' ? '/api/library' : kind === 'fan' ? '/api/projects' : '/api/sets');
    root.innerHTML = `<details data-library-panel><summary><strong>Saved ${kind === 'scripts' ? 'scripts' : 'radio projects'}</strong><span data-library-toggle>Show library</span></summary><div class="radio-library-head"><h2>Saved ${kind === 'scripts' ? 'scripts' : 'radio projects'}</h2><button type="button" class="btn sec sm" data-reload>Refresh library</button></div>
      <label>Search saved work<input type="search" data-query placeholder="${kind === 'scripts' ? 'Client, market, concept or script text' : kind === 'promo' ? 'Client, business, project, promotion or team member' : 'Client, business or team member'}"></label>
      <p data-message role="status" aria-live="polite">Loading saved work…</p><div class="radio-library-results" data-results></div><button class="btn sec sm" type="button" data-more hidden>Show more</button></details>`;
    const query = root.querySelector('[data-query]'), message = root.querySelector('[data-message]');
    const results = root.querySelector('[data-results]'), more = root.querySelector('[data-more]');
    const panel = root.querySelector('[data-library-panel]');
    let currentProject = new URLSearchParams(location.search).get('project') || location.hash.replace(/^#(?:p=|set=)?/, '');
    panel.open = !currentProject;
    function panelLabel() { root.querySelector('[data-library-toggle]').textContent = panel.open ? 'Hide library' : 'Show library'; }
    panel.addEventListener('toggle', panelLabel); panelLabel();
    window.addEventListener('radio-project-opened', event => {
      const id = String(event.detail?.id || '');
      if (id && id !== currentProject) { currentProject = id; panel.open = false; panelLabel(); }
    });
    let offset = 0, sequence = 0, timer, controller;
    async function load(append = false) {
      const ticket = ++sequence;
      if (controller) controller.abort();
      controller = new AbortController();
      if (!append) { offset = 0; results.replaceChildren(); }
      more.disabled = true; message.textContent = 'Loading saved work…';
      const params = new URLSearchParams({q:query.value.trim(), limit:'12', offset:String(offset)});
      try {
        const response = await fetch(endpoint + '?' + params, {signal:controller.signal});
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || 'Could not load saved work. Refresh the library to retry.');
        if (ticket !== sequence) return;
        const rows = data.projects || data.sets || [];
        rows.forEach(row => {
          const article = document.createElement('article'); article.className = 'radio-library-item';
          const link = document.createElement('a');
          link.textContent = row.project_name ? `${row.company || row.client || 'Radio project'} — ${row.project_name}` : row.company || row.client_name || row.client || 'Untitled project';
          link.href = base + '/?project=' + encodeURIComponent(row.id) + (kind === 'promo' ? '#p=' : kind === 'fan' ? '#' : '#set=') + encodeURIComponent(row.id);
          link.onclick = event => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            panel.open = false; panelLabel();
            if (kind === 'scripts') { event.preventDefault(); window.rsOpenSet(row.id); }
            else if (String(row.id) === currentProject) {
              event.preventDefault();
              document.querySelector('#steps, .flow')?.scrollIntoView({block:'start', behavior:'smooth'});
            }
          };
          const detail = document.createElement('p');
          const date = row.updated_at || row.updated || row.created_at;
          const stamp = date && !Number.isNaN(Date.parse(date)) ? new Date(date).toLocaleDateString() : '';
          detail.textContent = [row.client || row.market, kind === 'scripts' ? row.package : `${row.spots || 0} spots`, row.status || (row.approved ? `${row.approved} approved` : ''), row.team_member || row.created_by || row.actor, stamp].filter(Boolean).join(' · ');
          article.append(link, detail); results.append(article);
        });
        offset += rows.length;
        more.hidden = data.has_more === undefined ? offset >= (data.count || 0) : !data.has_more;
        message.textContent = offset ? `Showing ${offset} of ${data.count ?? offset} saved ${kind === 'scripts' ? 'sets' : 'projects'}.` : query.value.trim() ? 'No saved work matches this search.' : 'No saved work yet. Your first project will appear here.';
      } catch (error) {
        if (ticket !== sequence || error.name === 'AbortError') return;
        message.textContent = error.message || 'Could not load saved work. Refresh the library to retry.';
        more.hidden = true;
      } finally { if (ticket === sequence) more.disabled = false; }
    }
    function refresh() { clearTimeout(timer); load(); }
    query.addEventListener('input', () => {
      clearTimeout(timer); ++sequence; if (controller) controller.abort();
      more.hidden = true; results.replaceChildren(); message.textContent = 'Searching saved work…';
      timer = setTimeout(refresh, 250);
    });
    root.querySelector('[data-reload]').onclick = refresh;
    more.onclick = () => load(true);
    window.addEventListener('radio-library-changed', () => { clearTimeout(timer); timer = setTimeout(refresh, 150); });
    refresh();
  });
})();
