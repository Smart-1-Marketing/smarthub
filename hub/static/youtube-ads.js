(() => {
  'use strict';
  const root = '/tools/youtube-ads', $ = id => document.getElementById('yta-' + id);
  let generation = 0, loading = false;
  const message = text => { $('message').textContent = text; };
  const cid = () => {
    const value = $('customer').value.replaceAll('-', '').trim() || $('account').value;
    if (!/^\d{10}$/.test(value)) throw new Error('Choose an account or enter its 10-digit customer ID.');
    return value;
  };
  async function api(path, body) {
    const response = await fetch(root + path, {method: body ? 'POST' : 'GET', headers: {'Accept': 'application/json', ...(body ? {'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('.yta').dataset.csrf} : {})}, ...(body ? {body: JSON.stringify(body)} : {})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Request failed.');
    return data;
  }
  const el = (tag, text) => {const node = document.createElement(tag); if (text != null) node.textContent = text; return node;};
  async function run(button, fn) {
    button.disabled = true;
    message('Working…');
    try { await fn(); } catch (error) { message(error.message); } finally {button.disabled = false;}
  }
  document.querySelectorAll('[data-tab]').forEach(button => button.onclick = () => {
    document.querySelectorAll('[data-tab]').forEach(item => {item.setAttribute('aria-pressed', String(item === button)); $('' + item.dataset.tab).hidden = item !== button;});
  });
  $('accounts').onclick = event => run(event.target, async () => {
    const data = await api('/api/accounts');
    $('account').replaceChildren(el('option', 'Choose an account'));
    $('account').firstChild.value = '';
    data.accounts.filter(a => !a.is_manager && !a.error).forEach(a => {
      const option = el('option', `${a.name} · ${a.formatted_id} · ${a.currency}`); option.value = a.id; $('account').append(option);
    });
    message('Accounts loaded. Choose the client account.');
  });
  function clearAccount() {
    generation++;
    for (const name of ['drafts', 'metrics', 'advice', 'report-copy']) $(name).replaceChildren();
    $('checked').textContent = 'No results loaded for this account.';
    $('auto').checked = false;
  }
  $('account').onchange = () => {$('customer').value = ''; clearAccount();};
  $('customer').oninput = clearAccount;
  $('range').onchange = clearAccount;
  async function drafts() {
    const account = cid(), version = generation;
    const data = await api('/api/drafts?customer_id=' + account);
    if (version !== generation) return;
    $('drafts').replaceChildren();
    if (!data.drafts.length) $('drafts').append(el('p', 'No drafts for this account yet.'));
    data.drafts.forEach(d => {
      const card = el('article'); card.className = 'yta-card';
      card.append(el('h3', d.data.name), el('p', `${d.state} · Daily budget ${d.data.daily_budget} · ${d.created_at}`), el('p', `${d.data.headline} — ${d.data.description}`));
      const preview = el('a', 'Review source video ↗'); preview.href = 'https://www.youtube.com/watch?v=' + encodeURIComponent(d.data.video_id); preview.target = '_blank'; preview.rel = 'noopener'; card.append(preview);
      const details = el('details'); details.append(el('summary', 'Review campaign settings'));
      const settings = el('dl'); Object.entries(d.data).forEach(([key, value]) => {settings.append(el('dt', key.replaceAll('_', ' ')), el('dd', Array.isArray(value) ? value.join(', ') : String(value)));}); details.append(settings); card.append(details);
      const copy = el('button', 'Edit as new draft'); copy.onclick = () => {
        Object.entries(d.data).forEach(([key, value]) => {const field = $('form').elements.namedItem(key); if (field && key !== 'placements') field.value = value;});
        $('form').querySelectorAll('[name=placements]').forEach(field => {field.checked = d.data.placements.includes(field.value);});
        message('Draft copied into the form. Saving creates a new version requiring validation.'); $('form').scrollIntoView({behavior: 'smooth'});
      }; card.append(copy);
      if (['DRAFT', 'VALIDATED'].includes(d.state)) {
        const validate = el('button', 'Validate with Google'); validate.onclick = () => run(validate, async () => {
          await api(`/api/drafts/${d.id}/validate`, {customer_id: account}); await drafts(); message('Google validated this draft. Review settings before creating.');
        }); card.append(validate);
      }
      if (d.state === 'VALIDATED') {
        const create = el('button', 'Create paused campaign'); create.onclick = () => {
          if (!confirm(`Create ${d.data.name} in account ${account} with daily budget ${d.data.daily_budget} in account currency? Campaign and ad will remain paused.`)) return;
          run(create, async () => {try {await api(`/api/drafts/${d.id}/create`, {customer_id: account, confirmation: 'CREATE_PAUSED'}); message('Campaign created paused. Review targeting, conversions and policy status in Google Ads before launch.');} finally {await drafts();}});
        }; card.append(create);
      }
      if (['SUBMITTING', 'CHECK_GOOGLE_ADS'].includes(d.state)) card.append(el('p', 'Submission outcome needs reconciliation. Check this campaign name in Google Ads before creating a replacement.'));
      const link = el('a', 'Open Google Ads ↗'); link.href = 'https://ads.google.com/aw/overview'; link.target = '_blank'; link.rel = 'noopener';
      const linkRow = el('p'); linkRow.append(link); card.append(linkRow);
      $('drafts').append(card);
    });
  }
  $('drafts-refresh').onclick = event => run(event.target, async () => {await drafts(); message('Saved drafts loaded.');});
  $('form').onsubmit = event => {
    event.preventDefault(); run($('form').querySelector('[type=submit]'), async () => {
      const form = new FormData($('form')), body = Object.fromEntries(form);
      body.customer_id = cid(); body.placements = form.getAll('placements');
      await api('/api/drafts', body); await drafts(); message('Draft saved. Review it and validate with Google when ready.');
    });
  };
  function table(data) {
    const wrap = el('div'); wrap.className = 'yta-scroll'; const t = el('table');
    t.append(el('caption', `${data.account.currencyCode || ''} · ${data.account.timeZone || ''} · ${data.range} · Updated ${data.checked_at}`));
    const head = el('tr'); ['Campaign', 'Type', 'Status', 'Impressions', 'Clicks', 'Spend', 'Conversions', 'CTR %', 'CPA', 'ROAS', 'TrueView views', 'View rate %'].forEach(v => {const h = el('th', v); h.scope = 'col'; head.append(h);});
    const thead = el('thead'); thead.append(head); t.append(thead);
    const tbody = el('tbody'); data.campaigns.forEach(c => {const tr = el('tr'); ['name', 'type', 'status', 'impressions', 'clicks', 'cost', 'conversions', 'ctr', 'cpa', 'roas', 'trueview_views', 'view_rate'].forEach(key => tr.append(el('td', c[key] == null ? '—' : typeof c[key] === 'number' ? c[key].toLocaleString(undefined, {maximumFractionDigits: 2}) : c[key]))); tbody.append(tr);}); t.append(tbody); wrap.append(t); return wrap;
  }
  async function refresh() {
    if (loading) return;
    const account = cid(), version = generation;
    loading = true;
    try {
      const data = await api(`/api/report?customer_id=${account}&range=${$('range').value}`);
      if (version !== generation) return;
      $('metrics').replaceChildren(table(data)); $('report-copy').replaceChildren(el('p', data.scope), table(data)); $('advice').replaceChildren();
      data.campaigns.forEach(c => {const card = el('article'); card.className = 'yta-card'; card.append(el('h3', c.name)); c.recommendations.forEach(text => card.append(el('p', text))); $('advice').append(card);});
      $('checked').textContent = 'Last successful refresh: ' + data.checked_at;
      message(data.campaigns.length ? 'Results refreshed. ' + data.scope : 'No Video or Demand Gen campaigns returned for this window.');
    } finally {loading = false;}
  }
  document.querySelectorAll('.yta-refresh').forEach(button => button.onclick = () => run(button, refresh));
  setInterval(() => {if ($('auto').checked && !document.hidden) refresh().catch(e => {message('Refresh failed; displayed results may be stale. ' + e.message);});}, 300000);
  $('export').onclick = event => run(event.target, async () => {
    const response = await fetch(`${root}/report.csv?customer_id=${cid()}&range=${$('range').value}`);
    if (!response.ok) {const data = await response.json(); throw new Error(data.error || 'Report export failed.');}
    const url = URL.createObjectURL(await response.blob()), link = el('a'); link.href = url; link.download = 'youtube-ads-report.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); message('Fresh CSV report downloaded.');
  });
  $('print').onclick = () => {if (!$('report-copy').children.length) return message('Refresh results in Monitor before printing.'); window.print();};
})();
