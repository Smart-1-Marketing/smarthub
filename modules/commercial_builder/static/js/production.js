/* Server-derived state; no paid generation is triggered by opening this panel. */
(() => {
  const panel = document.querySelector('.cb-production');
  if (!panel) return;
  const base = `/tools/commercial-builder/api/projects/${panel.dataset.project}`;
  const find = selector => panel.querySelector(selector);
  async function read(path, options) {
    const response = await fetch(base + path, {...options, signal: AbortSignal.timeout(15000)});
    if (response.status === 401) throw new Error('Sign in again to view production progress.');
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || 'Could not load production details.');
    return data;
  }
  async function status() {
    try {
      const data = await read('/production');
      find('[data-production-label]').textContent = data.label;
      find('[data-production-recovery]').textContent = data.recovery_note;
      find('[data-production-counts]').textContent = `${data.generating} creating · ${data.saving} saving · ${data.rendering} rendering`;
      const action = find('[data-production-action]');
      action.textContent = data.action; action.href = data.href; action.hidden = false;
      const issues = find('[data-production-issues]');
      issues.replaceChildren(...data.issues.map(text => {
        const li = document.createElement('li'); li.textContent = text; return li;
      }));
      issues.hidden = !data.issues.length;
    } catch (error) {
      find('[data-production-label]').textContent = 'Status could not be refreshed';
      find('[data-production-counts]').textContent = error.message;
      find('[data-production-action]').hidden = true;
      find('[data-production-recovery]').textContent = '';
      find('[data-production-issues]').hidden = true;
    }
  }
  // Chain requests instead of overlapping slow responses, and pause in hidden tabs.
  async function tick() {
    if (!document.hidden) await status();
    setTimeout(tick, 15000);
  }
  tick();
  let next = null, loading = false, loaded = false;
  const names = {script: 'Script', voice: 'Scene narration', presenter: 'HeyGen presenter', track: 'Full narration'};
  function preview(container, value, kind) {
    if (!value) { container.textContent = 'No saved take is currently attached.'; return; }
    if (kind === 'script') {
      const text = document.createElement('pre');
      text.textContent = `${value.narration || '(No narration)'}\n\n${value.visual_description || ''}`;
      container.append(text); return;
    }
    const url = value.audio_url || value.spokesperson_url || value.voice_track_url;
    if (url && /^https?:\/\//i.test(url)) {
      const player = document.createElement(kind === 'presenter' ? 'video' : 'audio');
      player.controls = true; player.preload = 'none'; player.src = url; container.append(player);
    }
    const info = document.createElement('p');
    info.textContent = [value.avatar_id, value.voice_id || value.heygen_job?.voice_id].filter(Boolean).join(' · ');
    container.append(info);
  }
  function card(take) {
    const item = document.createElement('details');
    item.className = 'cb-take';
    const heading = document.createElement('summary');
    heading.textContent = `${names[take.kind]}${take.scene_number ? ` · Scene ${take.scene_number}` : take.kind !== 'track' ? ' · Deleted scene' : ''} · ${new Date(take.created_at).toLocaleString()}${take.is_current ? ' · Current' : ''}`;
    item.append(heading);
    const comparison = document.createElement('div'); comparison.className = 'cb-take-comparison';
    for (const [label, value] of [['Saved version', take.snapshot], ['Current version', take.current]]) {
      const side = document.createElement('div'), title = document.createElement('strong');
      title.textContent = label; side.append(title); preview(side, value, take.kind); comparison.append(side);
    }
    item.append(comparison);
    if (take.restorable && !take.is_current) {
      const restore = document.createElement('button');
      restore.type = 'button'; restore.className = 'cb-btn cb-btn-sm'; restore.textContent = 'Restore this take';
      restore.addEventListener('click', async () => {
        if (!window.confirm('Restore this saved take? Save any open edits first. This page will reload; the currently saved version stays in history.')) return;
        restore.disabled = true;
        try {
          await read(`/takes/${take.id}/restore`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({current_digest: take.current_digest})});
          location.reload();
        } catch (error) {
          find('[data-history-message]').textContent = error.message; restore.disabled = false;
        }
      });
      item.append(restore);
    }
    return item;
  }
  async function history(append = false) {
    if (loading) return;
    loading = true;
    const message = find('[data-history-message]'); message.textContent = 'Loading history…';
    try {
      const data = await read('/takes' + (append && next ? `?before=${next}` : ''));
      const list = find('[data-history-list]');
      if (!append) list.replaceChildren();
      list.append(...data.takes.map(card)); next = data.next_before; loaded = true;
      find('[data-history-more]').hidden = !next;
      message.textContent = list.children.length ? '' : 'No earlier takes yet. History begins with your next saved edit or generated take.';
    } catch (error) { message.textContent = error.message; }
    finally { loading = false; }
  }
  find('[data-take-history]').addEventListener('toggle', event => {
    if (event.target.open && !loaded) history();
  });
  find('[data-history-more]').addEventListener('click', () => history(true));
  find('[data-history-refresh]').addEventListener('click', () => history());
})();
