(() => {
  const root = document.getElementById('client-voice-capture-root');
  if (!root) return;

  const $ = (id) => document.getElementById(id);
  const script = $('voice-capture-script');
  const createBtn = $('voice-capture-create-btn');
  const createStatus = $('voice-capture-create-status');
  const linkBox = $('voice-capture-new-link');
  const linkInput = $('voice-capture-link-input');
  const openBtn = $('voice-capture-open-btn');
  const list = $('voice-capture-list');

  function fmtDate(value) {
    if (!value) return '';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleString();
  }

  function statusLabel(row) {
    if (row.status === 'submitted') return 'Submitted';
    if (row.revoked || row.status === 'revoked') return 'Revoked';
    if (row.expires_at && new Date(row.expires_at) < new Date()) return 'Expired';
    return 'Waiting on client';
  }

  async function copyText(value, button) {
    try {
      await navigator.clipboard.writeText(value);
    } catch (_err) {
      const temp = document.createElement('textarea');
      temp.value = value;
      temp.style.position = 'fixed';
      temp.style.opacity = '0';
      document.body.appendChild(temp);
      temp.select();
      document.execCommand('copy');
      temp.remove();
    }
    if (button) {
      const old = button.textContent;
      button.textContent = 'Copied';
      setTimeout(() => { button.textContent = old; }, 1400);
    }
  }

  function makeButton(label, className = 'cb-btn cb-btn-sm') {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = className;
    btn.textContent = label;
    return btn;
  }

  function renderRows(rows) {
    list.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement('span');
      empty.className = 'cb-hint';
      empty.textContent = 'No client voice requests yet.';
      list.appendChild(empty);
      return;
    }

    rows.forEach((row) => {
      const item = document.createElement('div');
      item.style.cssText = 'border:1px solid var(--cb-border);border-radius:10px;padding:12px;margin-top:8px;';

      const top = document.createElement('div');
      top.className = 'cb-flex-between';
      top.style.cssText = 'gap:12px;align-items:flex-start;flex-wrap:wrap;';

      const info = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = statusLabel(row);
      const meta = document.createElement('div');
      meta.className = 'cb-hint';
      const parts = [`Created ${fmtDate(row.created_at)}`];
      if (row.opened_count) parts.push(`opened ${row.opened_count} time${row.opened_count === 1 ? '' : 's'}`);
      if (row.submitted_at) parts.push(`received ${fmtDate(row.submitted_at)}`);
      meta.textContent = parts.filter(Boolean).join(' · ');
      info.append(title, meta);

      const actions = document.createElement('div');
      actions.style.cssText = 'display:flex;gap:7px;flex-wrap:wrap;';

      if (row.url) {
        const copy = makeButton('Copy link');
        copy.addEventListener('click', () => copyText(row.url, copy));
        const open = document.createElement('a');
        open.className = 'cb-btn cb-btn-sm';
        open.textContent = 'Open';
        open.href = row.url;
        open.target = '_blank';
        open.rel = 'noopener';
        const revoke = makeButton('Revoke');
        revoke.addEventListener('click', async () => {
          if (!confirm('Revoke this client recording link?')) return;
          revoke.disabled = true;
          try {
            const res = await fetch(`/tools/commercial-builder/api/voice-capture-requests/${row.id}/revoke`, { method: 'POST' });
            const data = await res.json();
            if (!res.ok || !data.ok) throw new Error(data.error || 'Could not revoke link.');
            await load();
          } catch (err) {
            alert(err.message || 'Could not revoke link.');
            revoke.disabled = false;
          }
        });
        actions.append(copy, open, revoke);
      }

      top.append(info, actions);
      item.appendChild(top);

      if (row.status === 'submitted') {
        const detail = document.createElement('div');
        detail.className = 'cb-hint';
        detail.style.marginTop = '8px';
        const who = row.submitter_name ? `Submitted by ${row.submitter_name}` : 'Client submission';
        const method = row.source_type === 'recorded' ? 'recorded in browser' : 'uploaded file';
        const length = row.duration_seconds ? ` · ${Math.round(row.duration_seconds)} sec` : '';
        detail.textContent = `${who} · ${method}${length}${row.original_filename ? ` · ${row.original_filename}` : ''}`;
        item.appendChild(detail);

        if (row.audio_url) {
          const audio = document.createElement('audio');
          audio.controls = true;
          audio.preload = 'metadata';
          audio.src = row.audio_url;
          audio.style.cssText = 'width:100%;margin-top:9px;';
          item.appendChild(audio);
        }
      }

      list.appendChild(item);
    });
  }

  async function load() {
    try {
      const res = await fetch(root.dataset.listUrl);
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Could not load client voice requests.');
      if (!script.value.trim()) script.value = data.default_script || '';
      renderRows(data.requests || []);
    } catch (err) {
      list.textContent = err.message || 'Could not load client voice requests.';
      list.className = 'cb-hint';
    }
  }

  createBtn.addEventListener('click', async () => {
    const text = script.value.trim();
    if (text.length < 100) {
      createStatus.textContent = 'Add a longer recording script first.';
      return;
    }
    createBtn.disabled = true;
    createStatus.textContent = 'Creating secure link…';
    try {
      const res = await fetch(root.dataset.createUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ script: text, expires_days: 30 }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Could not create the recording link.');
      linkInput.value = data.request.url;
      openBtn.href = data.request.url;
      linkBox.style.display = 'block';
      createStatus.textContent = 'Link ready. It expires in 30 days and can be revoked at any time.';
      await load();
    } catch (err) {
      createStatus.textContent = err.message || 'Could not create the recording link.';
    } finally {
      createBtn.disabled = false;
    }
  });

  $('voice-capture-copy-btn').addEventListener('click', () => copyText(linkInput.value, $('voice-capture-copy-btn')));
  $('voice-capture-refresh').addEventListener('click', load);
  load();
})();
