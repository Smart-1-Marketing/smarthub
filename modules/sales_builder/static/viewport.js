/* Progressive disclosure keeps long records readable without widening columns.
   All controls are native buttons/details, including on touch and keyboard. */
function salesFold(panel, label, open) {
  if (!panel || panel.tagName === 'DETAILS') return panel;
  const fold = document.createElement('details');
  for (const attr of panel.attributes) fold.setAttribute(attr.name, attr.value);
  fold.className = panel.className + ' sales-fold';
  fold.style.cssText = panel.style.cssText;
  fold.open = open;
  const summary = document.createElement('summary');
  summary.textContent = label;
  fold.append(summary);
  while (panel.firstChild) fold.append(panel.firstChild);
  panel.replaceWith(fold);
  return fold;
}

function salesPager(container, items, size, key) {
  let pager = container.nextElementSibling;
  if (!pager?.classList.contains('sales-pager')) {
    pager = document.createElement('nav');
    pager.className = 'sales-pager';
    pager.setAttribute('aria-label', 'Result pages');
    container.after(pager);
  }
  const old = container._salesPage;
  const state = {page: old?.key === key ? old.page : 0, key};
  container._salesPage = state;
  const pages = Math.max(1, Math.ceil(items.length / size));
  state.page = Math.min(state.page, pages - 1);
  function draw() {
    items.forEach((item, i) => { item.hidden = i < state.page * size || i >= (state.page + 1) * size; });
    pager.replaceChildren();
    const prev = document.createElement('button');
    prev.type = 'button'; prev.textContent = '← Previous'; prev.disabled = state.page === 0;
    const next = document.createElement('button');
    next.type = 'button'; next.textContent = 'Next →'; next.disabled = state.page === pages - 1;
    const status = document.createElement('span');
    status.setAttribute('aria-live', 'polite');
    status.textContent = items.length ? `${state.page * size + 1}–${Math.min(items.length, (state.page + 1) * size)} of ${items.length}` : 'No results';
    prev.onclick = () => { state.page--; draw(); pager.querySelector('button').focus(); };
    next.onclick = () => { state.page++; draw(); pager.lastElementChild.focus(); };
    pager.append(prev, status, next);
  }
  draw();
}

function salesTable(tb, key) {
  const table = tb.closest('table');
  table.classList.add('sales-table');
  const rows = [...tb.rows].filter(row => row.cells.length > 1);
  rows.forEach(row => {
    [...row.cells].forEach((cell, i) => {
      cell.dataset.label = table.tHead?.rows[0]?.cells[i]?.textContent || '';
      if (i === row.cells.length - 1) {
        const actions = cell.querySelector('.rowact');
        if (actions) {
          const fold = document.createElement('details'); fold.className = 'sales-actions';
          const summary = document.createElement('summary'); summary.textContent = 'Actions';
          fold.append(summary); actions.replaceWith(fold); fold.append(actions);
          fold.onclick = event => event.stopPropagation();
        }
      } else if (cell.textContent.trim().length > 85) {
        const fold = document.createElement('details'); fold.className = 'sales-cell';
        const summary = document.createElement('summary');
        summary.textContent = cell.textContent.trim().replace(/\s+/g, ' ').slice(0, 55) + '…';
        fold.append(summary);
        const content = document.createElement('div');
        while (cell.firstChild) content.append(cell.firstChild);
        fold.append(content); cell.append(fold);
        fold.onclick = event => event.stopPropagation();
      }
    });
  });
  const dashboard = !!tb.closest('#view-dashboard');
  const size = innerWidth < 721 ? 2 : dashboard ? 3 : Math.max(3, Math.min(8, Math.floor((innerHeight - 290) / 70)));
  salesPager(table, rows, size, key);
}

function salesDashboardLayout(host) {
  const pipeline = host.querySelector(':scope > .panel');
  if (pipeline) salesFold(pipeline, 'Needs chasing — pipeline details', false).querySelector('.phead')?.remove();
  const main = host.querySelector('.cols > div');
  salesFold(main?.querySelectorAll(':scope > .panel')[1], 'Insertion orders', false);
  host.querySelectorAll('.rail > .panel').forEach((panel, i) => {
    const label = panel.querySelector('.ttl')?.textContent || 'Details';
    const fold = salesFold(panel, label, i === 0);
    fold.querySelector('.phead')?.remove();
    fold.addEventListener('toggle', () => {
      if (fold.open) host.querySelectorAll('.rail > details').forEach(other => { if (other !== fold) other.open = false; });
    });
  });
}

function salesClients(host, label = 'Find a client') {
  const cards = [...host.querySelectorAll('.clientcard')];
  const search = document.createElement('input');
  search.className = 'sales-client-search'; search.placeholder = label + '…';
  search.setAttribute('aria-label', label);
  host.querySelector('.headrow').append(search);
  const list = document.createElement('div'); list.className = 'sales-client-list'; host.append(list);
  cards.forEach(card => {
    list.append(card);
    const label = card.querySelector('h3').textContent;
    salesFold(card, label, false).querySelector('h3').remove();
  });
  const items = [...list.children];
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    items.forEach(item => { item.hidden = true; });
    salesPager(list, items.filter(item => item.textContent.toLowerCase().includes(q)), 5, q);
  };
  search.oninput = draw; draw();
}

const salesStepOpen = new Map();
function salesStep(stage, step, entering) {
  stage.querySelectorAll(':scope > .bpanel').forEach((panel, i) => {
    const title = panel.querySelector(':scope > .ttl');
    if (!title) return;
    const key = step + ':' + i + ':' + title.textContent;
    const fold = salesFold(panel, title.textContent, salesStepOpen.get(key) ?? i === 0);
    title.remove();
    fold.addEventListener('toggle', () => salesStepOpen.set(key, fold.open));
  });
  if (entering) stage.closest('.bmain').scrollTop = 0;
}
