(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let active = '', page = 1, total = 0, plan = null, busy = false;
  const selections = new Set();
  let readiness = null, excluded = 0;
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
    if (readiness) {
      $('ip-create').disabled = !readiness.fresh;
      $('ip-search').disabled = !readiness.fresh;
      $('ip-review').disabled = !readiness.fresh || selections.size === 0;
      $('ip-buy').disabled = !readiness.fresh || !readiness.paid_enabled;
      $('ip-sync').disabled = Boolean(readiness.missing.length || readiness.lock || readiness.sync_job?.status === 'queued');
      $('ip-resume').disabled = Boolean(readiness.lock || readiness.sync_job?.status === 'queued');
    }
  }
  async function refresh() {
    const data = await json('/api/industry-prospects/status');
    readiness = data;
    const connections = data.connections;
    if (connections) {
      $('ip-connections').textContent = `Apollo: ${connections.apollo_configured ? 'configured' : 'missing'}. GHL: ${connections.ghl_configured ? 'configured' : 'missing'}. Accounts: ${connections.locations.join(', ') || 'none'}. ${connections.configuration_error || ''} ${connections.checked ? 'Read access tested ' + new Date(connections.checked * 1000).toLocaleString() : 'Read access has not been tested recently.'}`;
      $('ip-checks').replaceChildren();
      for (const check of connections.checks) {
        const item = document.createElement('li');
        item.textContent = `${check.ok ? 'Passed' : 'Needs attention'} — ${check.name}: ${check.detail}`;
        $('ip-checks').append(item);
      }
    }
    $('ip-next-action').textContent = data.missing.length ? 'Next: configure the missing connections, then test read access.' : !data.fresh ? 'Next: complete suppression sync. Saved purchase history remains available.' : 'Next: search an audience, review your selection, then import verified contacts.';
    const sync = data.sync;
    $('ip-fresh').textContent = data.fresh ? 'Suppression is current' : 'Sync needed before purchase';
    $('ip-ghl').textContent = sync.read ?? '—';
    $('ip-saved').textContent = sync.saved ?? '—';
    $('ip-sync-time').textContent = sync.completed ? new Date(sync.completed * 1000).toLocaleString() : 'Not yet';
    $('ip-sync-detail').textContent = sync.phase ? `Stage: ${sync.phase}. ${sync.unmatchable || 0} contacts lack enough identity information for Apollo matching. Automatic sync: ${data.auto_sync ? 'on' : 'off'}.` : 'No sync has run yet.';
    if (data.sync_job?.status) $('ip-sync-detail').textContent += ` Background job: ${data.sync_job.status}. ${data.sync_job.error || ''} Last progress: ${new Date(data.sync_job.updated * 1000).toLocaleString()}.`;
    if (data.expires_at) $('ip-sync-detail').textContent += ` Coverage expires ${new Date(data.expires_at * 1000).toLocaleString()}.`;
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
    return data;
  }
  async function sync(resume) {
    await action(resume ? 'sync_resume' : 'sync_queue');
    await refresh();
    message('Sync queued. The scheduler continues after you close this page. Refresh progress to check completion.');
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
    excluded = data.people.filter(person => person.reason).length;
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
    $('ip-jobs').replaceChildren();
    for (const job of data.jobs || []) {
      const item = document.createElement('p');
      item.textContent = `Purchase batch: ${job.status}. ${job.processed}/${job.total} processed. ${job.reason || ''} Approval expires ${new Date(job.expires_at * 1000).toLocaleString()}.`;
      if (job.status === 'queued') {
        const stop = document.createElement('button'); stop.textContent = 'Stop remaining purchases';
        stop.addEventListener('click', () => run(async () => {
          await action('purchase_stop', {job: job.id}); await history();
          message('Remaining purchases stopped. Already processed contacts are unchanged.');
        })); item.append(stop);
      }
      $('ip-jobs').append(item);
    }
    $('ip-history').replaceChildren();
    for (const row of data.rows) {
      const tr = document.createElement('tr');
      cell(tr, row.email || 'Email unavailable'); cell(tr, row.status.replaceAll('_', ' '));
      cell(tr, [row.reason, row.recovery, row.contact_id ? `GHL contact: ${row.contact_id}` : ''].filter(Boolean).join(' '));
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
  $('ip-refresh').onclick = () => run(async () => { await refresh(); await history(); });
  $('ip-test').onclick = () => run(async () => {
    message('Testing read access. No contacts will be purchased or imported.');
    await action('connections'); await refresh();
    message('Read checks finished. Review the results above; write access and billing remain untested.');
  });
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
    selections.clear(); invalidatePlan(); total = 0;
    $('ip-results').replaceChildren();
    await history(); message('Saved history loaded. Use Search audience for fresh candidates.');
  });
  $('ip-search').onclick = () => run(async () => {
    active = $('ip-campaign').value;
    if (!active) throw new Error('Choose a saved audience.');
    await search(1);
  });
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
    $('ip-quote').textContent = `${plan.ids.length} contacts selected; ${excluded} known duplicates excluded on this page. ${plan.note}`;
    $('ip-estimate').textContent = `Estimated Apollo email credits: ${plan.max_email_credits}. Up to ${plan.max_verifications ?? plan.ids.length} separately billed GHL verifications. Dollar cost is unknown until your account rates are confirmed. Review expires in 15 minutes. ${readiness?.paid_enabled ? '' : 'Purchasing is disabled; this is a preview only.'}`;
    $('ip-confirm').checked = false; $('ip-review-box').hidden = false;
    $('ip-review-box').scrollIntoView({block: 'center', behavior: 'smooth'});
    message('Review the purchase details and charges before approving.');
  });
  $('ip-cancel').onclick = invalidatePlan;
  $('ip-buy').onclick = () => run(async () => {
    if (!plan || !$('ip-confirm').checked) throw new Error('Confirm the displayed purchase and verification charges first.');
    const approved = await action('approve', {plan: plan.id, confirmed: true});
    await action('purchase_queue', {plan: approved.id});
    $('ip-review-box').hidden = true;
    selections.clear(); plan = null;
    await history(); message('Purchase batch queued. You can close this page; refresh progress to review results before importing.');
  });
  run(refresh);
})();
