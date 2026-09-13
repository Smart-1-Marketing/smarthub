(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let active = '', page = 1, total = 0, plan = null, busy = false;
  const selections = new Set();
  const message = (text, error = false) => {
    $('ip-message').textContent = text;
    $('ip-message').classList.toggle('error', error);
  };
  async function json(url, options = {}) {
    const response = await fetch(url, options);
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error('The server did not return a usable response. Reload to inspect saved progress; do not repeat an uncertain purchase.'); }
    if (!response.ok || !data.ok) throw new Error(data.error || 'The request failed.');
    return data;
  }
  const action = async (name, body = {}) => (await json('/api/industry-prospects/action', {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Prospect-Request': '1'},
    body: JSON.stringify({...body, action: name})
  })).result;
  async function run(fn) {
    if (busy) return;
    busy = true;
    $('prospect-builder').setAttribute('aria-busy', 'true');
    const controls = [...document.querySelectorAll('#prospect-builder button, #ip-form input, #ip-form select, #ip-results input, #ip-all, #ip-campaign')];
    const states = controls.map(el => el.disabled);
    controls.forEach(el => { el.disabled = true; });
    try { await fn(); }
    catch (error) { message(error.message, true); }
    finally {
      controls.forEach((el, i) => { el.disabled = states[i]; });
      busy = false;
      $('prospect-builder').removeAttribute('aria-busy');
      paging();
    }
  }
  function paging() {
    $('ip-prev').disabled = !active || page <= 1;
    $('ip-next').disabled = !active || page * 25 >= Math.min(total, 12500);
    $('ip-review').disabled = selections.size === 0;
    $('ip-review').textContent = selections.size ? `Review ${selections.size} selected contacts` : 'Review selected contacts';
  }
  async function refresh() {
    const data = await json('/api/industry-prospects/status');
    const sync = data.sync;
    $('ip-fresh').textContent = data.fresh ? 'Suppression is current' : 'Sync needed before purchase';
    $('ip-ghl').textContent = sync.read ?? '—';
    $('ip-saved').textContent = sync.saved ?? '—';
    $('ip-sync-time').textContent = sync.completed ? new Date(sync.completed * 1000).toLocaleString() : 'Not yet';
    $('ip-sync-detail').textContent = sync.phase ? `Stage: ${sync.phase}. ${sync.unmatchable || 0} contacts lack enough identity information for Apollo matching. Automatic sync: ${data.auto_sync ? 'on' : 'off'}.` : 'No sync has run yet.';
    $('ip-config').textContent = [
      data.missing.length ? 'Apollo and GHL connection setup is incomplete. Ask an administrator to configure the integrations.' : 'Apollo and GHL configuration is present.',
      data.paid_enabled ? 'Paid operations enabled; each purchase still requires review.' : 'Purchasing is off until an administrator enables it.',
      data.lock ? `An operation is running: ${data.lock.action}.` : ''
    ].filter(Boolean).join(' ');
    $('ip-resume').hidden = !sync.phase || sync.phase === 'complete';
    const choice = $('ip-campaign').value;
    $('ip-campaign').replaceChildren(new Option('Choose an audience', ''));
    data.campaigns.forEach(c => $('ip-campaign').add(new Option(c.name, c.id)));
    $('ip-campaign').value = active || choice;
    $('ip-factory').href = '/sales/industry-factory' + ($('ip-campaign').value ? '?audience=' + encodeURIComponent($('ip-campaign').value) : '');
    return data;
  }
  async function sync(resume) {
    message(resume ? 'Resuming suppression sync…' : 'Starting suppression sync…');
    let result = resume ? (await refresh()).sync : await action('sync_start');
    while (result.phase !== 'complete') {
      result = await action('sync_step');
      $('ip-ghl').textContent = result.read;
      $('ip-saved').textContent = result.saved;
      message(`Syncing ${result.phase === 'apollo' ? 'Apollo saved contacts' : 'GHL contacts'}: ${result.read} GHL contacts, ${result.saved} saved Apollo contacts checked. You can reopen this page and resume if interrupted.`);
    }
    await refresh();
    message('Suppression sync complete. You can now search your audience.');
  }
  function cell(row, text) {
    const td = document.createElement('td');
    td.textContent = text;
    row.append(td);
    return td;
  }
  function invalidatePlan() {
    plan = null;
    $('ip-review-box').hidden = true;
    $('ip-confirm').checked = false;
  }
  async function search(nextPage) {
    message('Searching Apollo and checking existing contacts…');
    const data = await action('search', {campaign: active, page: nextPage});
    page = data.page; total = data.total;
    selections.clear(); invalidatePlan(); $('ip-all').checked = false;
    $('ip-count').textContent = `${data.eligible_on_page} eligible on this page`;
    $('ip-audience').replaceChildren(document.createTextNode(`${data.campaign.name} · Apollo reports ${total.toLocaleString()} matches. Landing page: `));
    const link = document.createElement('a');
    link.href = data.campaign.landing_page; link.textContent = data.campaign.landing_page;
    link.target = '_blank'; link.rel = 'noopener noreferrer'; $('ip-audience').append(link);
    $('ip-page').textContent = `Page ${page}`;
    $('ip-results').replaceChildren();
    for (const person of data.people) {
      const tr = document.createElement('tr');
      const td = cell(tr, '');
      const check = document.createElement('input'); check.type = 'checkbox'; check.value = person.id;
      check.disabled = Boolean(person.reason); check.setAttribute('aria-label', `Select ${person.name} at ${person.company}`);
      check.addEventListener('change', () => {
        if (check.checked) selections.add(person.id); else selections.delete(person.id);
        invalidatePlan(); paging();
      });
      td.append(check);
      cell(tr, person.name + (person.last_name_obfuscated ? ` ${person.last_name_obfuscated} (hidden)` : ''));
      const company = cell(tr, person.company || 'Company unavailable');
      if (person.domain) { const small = document.createElement('small'); small.textContent = person.domain; company.append(small); }
      cell(tr, person.title); cell(tr, person.reason || 'No known duplicate');
      $('ip-results').append(tr);
    }
    if (!data.people.length) { const tr = document.createElement('tr'); cell(tr, 'No matches. Try different keywords or a wider geography.').colSpan = 5; $('ip-results').append(tr); }
    await history(); paging(); message('Search complete. Select the contacts you want to review.');
  }
  async function history() {
    if (!active) return;
    const data = await json('/api/industry-prospects/history/' + encodeURIComponent(active));
    $('ip-history').replaceChildren();
    for (const row of data.rows) {
      const tr = document.createElement('tr');
      cell(tr, row.email || 'Email unavailable'); cell(tr, row.status.replaceAll('_', ' '));
      cell(tr, row.reason || (row.contact_id ? `GHL contact: ${row.contact_id}` : ''));
      const td = cell(tr, '');
      if (row.status === 'ready') {
        const button = document.createElement('button'); button.textContent = 'Import to GHL';
        button.addEventListener('click', () => run(async () => {
          const result = await action('import', {person: row.id});
          await history(); message(result.status === 'imported' ? 'Contact confirmed in GHL.' : result.reason || result.status, result.status !== 'imported');
        })); td.append(button);
      }
      $('ip-history').append(tr);
    }
    if (!data.rows.length) { const tr = document.createElement('tr'); cell(tr, 'No purchases for this audience yet.').colSpan = 4; $('ip-history').append(tr); }
  }
  $('ip-sync').addEventListener('click', () => run(() => sync(false)));
  $('ip-resume').onclick = () => run(() => sync(true));
  $('ip-form').onsubmit = event => {
    event.preventDefault();
    // Capture before run() disables the form controls.
    const body = Object.fromEntries(new FormData(event.currentTarget));
    run(async () => {
      const data = await action('create', body); active = data.id;
      await refresh(); await search(1);
    });
  };
  $('ip-form').elements.industry.onchange = event => {
    $('ip-form').elements.keywords.value = event.target.selectedOptions[0].textContent;
  };
  $('ip-open').onclick = () => run(async () => {
    active = $('ip-campaign').value;
    if (!active) throw new Error('Choose a saved audience.');
    await history(); await search(1);
  });
  $('ip-campaign').onchange = () => {
    $('ip-factory').href = '/sales/industry-factory' + ($('ip-campaign').value ? '?audience=' + encodeURIComponent($('ip-campaign').value) : '');
  };
  $('ip-prev').onclick = () => run(() => search(page - 1));
  $('ip-next').onclick = () => run(() => search(page + 1));
  $('ip-all').onchange = event => {
    document.querySelectorAll('#ip-results input:not(:disabled)').forEach(check => {
      check.checked = event.target.checked;
      if (check.checked) selections.add(check.value); else selections.delete(check.value);
    }); invalidatePlan(); paging();
  };
  $('ip-review').onclick = () => run(async () => {
    plan = await action('quote', {campaign: active, ids: [...selections]});
    $('ip-quote').textContent = `${plan.ids.length} contacts selected. Maximum ${plan.max_email_credits} Apollo credits. ${plan.note}`;
    $('ip-confirm').checked = false; $('ip-review-box').hidden = false;
    $('ip-review-box').scrollIntoView({block: 'center', behavior: 'smooth'});
    message('Review the purchase details and charges before approving.');
  });
  $('ip-cancel').onclick = invalidatePlan;
  $('ip-buy').onclick = () => run(async () => {
    if (!plan || !$('ip-confirm').checked) throw new Error('Confirm the displayed purchase and verification charges first.');
    const approved = await action('approve', {plan: plan.id, confirmed: true});
    $('ip-review-box').hidden = true;
    let completed = 0;
    for (const id of approved.ids) {
      message(`Revealing and verifying contact ${completed + 1} of ${approved.ids.length}…`);
      const result = await action('buy', {plan: approved.id, person: id});
      completed += 1; await history();
      if (result.status === 'review_required') throw new Error(result.reason + ' Remaining contacts were not purchased.');
    }
    selections.clear(); plan = null;
    await history(); message(`Processed ${completed} contacts. Review verified contacts below before importing to GHL.`);
  });
  run(refresh);
})();
