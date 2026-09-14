(() => {
  const root = document.getElementById('finishing-root');
  if (!root) return;
  const id = root.dataset.projectId, base = `/api/projects/${id}`;
  const $ = name => document.getElementById(name);
  const text = (name, value) => { $(name).textContent = value; };
  const esc = CB.escapeHtml;
  const api = (path, body) => CB.api(base + path, body === undefined ? {} : {method: 'POST', body});
  let timeline, currentScene, presets = [], plans = [], generation = 0, planRevision = 0;
  const safeMedia = raw => { try { const u = new URL(raw); return ['https:', 'http:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } };
  async function action(button, status, fn) {
    button.disabled = true;
    text(status, 'Working…');
    try { await fn(); } catch (e) { text(status, e.message || 'Could not complete this action. Try again.'); }
    finally { button.disabled = false; }
  }
  async function loadTimeline() {
    const ticket = ++generation;
    try {
      const data = await api(`/timeline?format=${encodeURIComponent($('timeline-format').value || '16:9')}`);
      if (ticket !== generation) return;
      timeline = data; currentScene = null;
      $('timeline-scrub').max = data.duration;
      $('timeline-stage').style.aspectRatio = `${data.width}/${data.height}`;
      $('timeline-stage').style.setProperty('--frame-ratio', data.width / data.height);
      $('timeline-captions').checked = data.captions_enabled;
      $('timeline-captions').disabled = !data.can_edit_captions;
      $('timeline-captions').title = data.can_edit_captions ? '' : 'Create a draft variation to change an approved cut, or wait for its current render.';
      Object.entries(data.safe_insets).forEach(([side, pct]) => { $('timeline-safe').style[side] = `${pct}%`; });
      text('timeline-guide', data.safe_note);
      text('timeline-error', '');
      const tracks = $('timeline-tracks'); tracks.replaceChildren();
      const select = $('variation-scene'), prior = select.value; select.replaceChildren();
      data.scenes.forEach((scene, i) => {
        const row = document.createElement('button'); row.type = 'button'; row.className = 'cb-timeline-row';
        row.textContent = `${i + 1} · ${scene.start.toFixed(1)}–${scene.end.toFixed(1)}s · ${scene.is_cta ? 'End card' : 'Scene'}${scene.speech_seconds != null ? ` · speech ${Number(scene.speech_seconds).toFixed(1)}s` : ' · speech timing unmeasured'}`;
        if (Number(scene.speech_seconds) > scene.end - scene.start + .5) row.classList.add('cb-timeline-warning');
        row.addEventListener('click', () => { $('timeline-scrub').value = scene.start; draw(); }); tracks.append(row);
        const option = new Option(`Scene ${i + 1}${scene.is_cta ? ' · End card' : ''}${scene.locked ? ' · Locked' : ''}`, scene.id);
        option.disabled = scene.locked; select.add(option);
      });
      if ([...select.options].some(o => o.value === prior)) select.value = prior;
      draw();
    } catch (e) { if (ticket === generation) text('timeline-error', e.message || 'Timeline unavailable.'); }
  }
  function draw() {
    if (!timeline) return;
    const time = Number($('timeline-scrub').value);
    text('timeline-time', `${time.toFixed(1)}s / ${timeline.duration}s`);
    const scene = timeline.scenes.find(s => s.start <= time && time < s.end) || (time === timeline.duration ? timeline.scenes.at(-1) : null);
    const media = $('timeline-media'), overlay = $('timeline-overlay');
    if (!scene) { media.replaceChildren(); overlay.replaceChildren(); text('timeline-speech', 'No scene at this time.'); text('timeline-caption', ''); currentScene = null; return; }
    if (currentScene !== scene.id) {
      currentScene = scene.id; media.replaceChildren();
      const url = safeMedia(scene.asset_url);
      if (url) {
        const video = scene.media_type === 'video';
        const node = document.createElement(video ? 'video' : 'img');
        node.src = url;
        if (video) { node.muted = true; node.preload = 'metadata'; node.playsInline = true; }
        else node.alt = 'Selected scene asset';
        node.addEventListener('error', () => { if (currentScene === scene.id) { media.replaceChildren(); text('timeline-error', 'Scene asset could not be loaded. Check its source before rendering.'); } });
        media.append(node);
      } else media.textContent = scene.is_cta ? 'End-card layout' : 'No scene asset selected';
    }
    const video = media.querySelector('video');
    if (video && video.readyState >= 1) video.currentTime = Math.min(Math.max(0, time - scene.start), video.duration || 0);
    text('timeline-speech', `Speech: ${scene.narration || '(none)'}${scene.has_presenter ? ' · HeyGen presenter' : ''}`);
    overlay.replaceChildren();
    const bounds = $('timeline-stage').getBoundingClientRect();
    const vmin = Math.min(bounds.width, bounds.height) / 100;
    $('timeline-caption').style.fontSize = `${4.5 * vmin}px`;
    (scene.overlays || []).filter(e => e.type === 'text' && e.text).forEach(e => {
      const line = document.createElement('div'); line.textContent = e.text; line.className = 'cb-rehearsal-text';
      const size = /^(\d+(\.\d+)?)vmin$/.exec(e.font_size || '');
      if (size) line.style.fontSize = `${Number(size[1]) * vmin}px`;
      // Restrict style values to numeric percentages; provider JSON never becomes CSS code.
      for (const [key, fallback] of [['x', '50%'], ['y', '50%'], ['width', '70%']]) {
        const value = /^\d+(\.\d+)?%$/.test(e[key]) ? e[key] : fallback;
        line.style[key === 'x' ? 'left' : key === 'y' ? 'top' : key] = value;
      }
      overlay.append(line);
    });
    const images = [...(scene.overlays || []).filter(e => e.type === 'image'), ...(timeline.extras || []).filter(e => e.type === 'image' && e.time <= time && time < e.time + e.duration)];
    images.forEach(e => {
      const source = safeMedia(e.source); if (!source) return;
      const node = document.createElement('img'); node.src = source; node.alt = e.id === 'logo_bug' ? 'Persistent logo' : 'End-card graphic';
      node.style.position = 'absolute'; node.style.transform = 'translate(-50%,-50%)';
      for (const [key, fallback] of [['x', '50%'], ['y', '50%'], ['width', '12%']]) node.style[key === 'x' ? 'left' : key === 'y' ? 'top' : key] = /^\d+(\.\d+)?%$/.test(e[key]) ? e[key] : fallback;
      overlay.append(node);
    });
    if ((timeline.extras || []).some(e => e.type === 'video' && e.time <= time && time < e.time + e.duration)) {
      const note = document.createElement('p'); note.className = 'cb-presenter-area'; note.textContent = 'Presenter composites over this scene. Check text placement against the finished presenter.'; overlay.append(note);
    }
    const caption = timeline.captions.find(c => c.time <= time && time < c.time + c.duration);
    text('timeline-caption', caption ? caption.text : '');
  }
  $('timeline-scrub').addEventListener('input', draw);
  window.addEventListener('resize', draw);
  $('timeline-format').addEventListener('change', loadTimeline);
  $('timeline-captions').addEventListener('change', async e => {
    const box = e.target; box.disabled = true;
    try { await api('/timeline-settings', {captions_enabled: box.checked}); await loadTimeline(); }
    catch (err) { box.checked = !box.checked; text('timeline-error', err.message); }
    finally { box.disabled = timeline ? !timeline.can_edit_captions : false; }
  });
  async function loadPresets() {
    const data = await api('/brand-presets'); presets = data.presets;
    $('preset-select').replaceChildren(new Option('Choose a preset', ''));
    presets.forEach(p => $('preset-select').add(new Option(p.name, p.id)));
    $('preset-apply').disabled = true;
  }
  $('preset-select').addEventListener('change', () => {
    const p = presets.find(p => String(p.id) === $('preset-select').value);
    $('preset-apply').disabled = !p;
    text('preset-detail', p ? `Approved by ${p.approved_by}. Brand: ${p.snapshot.brand.name}. ${p.snapshot.casting.length} presenter choice(s). End card: ${p.snapshot.cta.headline || '(no headline)'}.` : '');
  });
  $('preset-save').addEventListener('click', e => action(e.target, 'preset-status', async () => {
    await api('/brand-presets', {name: $('preset-name').value, approve: $('preset-approve').checked});
    text('preset-status', 'Approved preset saved for this client.'); await loadPresets();
  }));
  $('preset-apply').addEventListener('click', e => action(e.target, 'preset-status', async () => {
    if (!$('preset-select').value) throw new Error('Choose a preset.');
    const result = await api(`/brand-presets/${$('preset-select').value}/apply`, {});
    text('preset-status', result.note); await loadTimeline();
  }));
  async function loadCost() {
    const d = await api('/production-cost');
    const box = $('production-cost'); box.replaceChildren();
    const headline = document.createElement('strong'); headline.textContent = 'Total cost unknown — some provider or historical charges are missing'; box.append(headline);
    const p = document.createElement('p');
    p.textContent = `${d.elapsed_hours} hours ${d.approved ? 'to first approval' : 'elapsed so far'} · ${d.render_attempts} render attempts (${d.extra_render_attempts} extra) · ${d.approved_cuts} approved cuts. Known cost subtotal: $${d.known_cost_usd.toFixed(4)}.`; box.append(p);
    for (const provider of d.providers) {
      const line = document.createElement('p'); line.textContent = `${provider.provider}: ${provider.calls} recorded calls, ${provider.failed} failed, ${provider.cached} reused${provider.unpriced ? ` · ${provider.unpriced} unpriced` : ''}`; box.append(line);
    }
    const note = document.createElement('p'); note.className = 'cb-hint'; note.textContent = d.note; box.append(note);
    const budget = d.budget || {};
    text('budget-status', budget.configured ? `Limit: $${budget.limit_usd.toFixed(2)} · Reserved: $${budget.reserved_usd.toFixed(2)} · ${budget.remaining_usd === null ? 'Remaining budget unknown — reconcile past charges' : 'Available under ceiling: $' + budget.remaining_usd.toFixed(2)}. ${budget.note}` : budget.note);
    text('render-budget-status', 'Total cost unknown. ' + $('budget-status').textContent);
    if (budget.configured) $('budget-limit').value = budget.limit_usd;
  }
  $('budget-save').addEventListener('click', e => action(e.target, 'budget-status', async () => {
    const prior = $('budget-prior').value;
    await api('/budget', {limit_usd: $('budget-limit').value, prior_usd: prior === '' ? null : prior, confirm_prior: $('budget-confirm').checked});
    await loadCost();
  }));
  $('cost-refresh').addEventListener('click', e => action(e.target, 'production-cost', loadCost));
  $('creative-review').addEventListener('click', e => action(e.target, 'creative-result', async () => {
    const d = await api('/creative-review', {}), box = $('creative-result');
    box.replaceChildren(); const p = document.createElement('p'); p.textContent = d.review.summary + (d.cached ? ' (Saved review reused.)' : ''); box.append(p);
    for (const s of d.review.suggestions) {
      if (!s || typeof s !== 'object') continue;
      const line = document.createElement('p'); line.textContent = `Scene ${s.scene || '—'}: ${s.issue || ''} ${s.suggestion || ''}`; box.append(line);
    }
    await loadCost();
  }));
  function invalidatePlans() { planRevision++; plans = []; $('variation-create').disabled = true; text('variation-preview-result', 'Preview the updated text before creating drafts.'); }
  $('variation-lines').addEventListener('input', invalidatePlans);
  $('variation-scene').addEventListener('change', invalidatePlans);
  $('variation-preview').addEventListener('click', e => action(e.target, 'variation-preview-result', async () => {
    plans = []; $('variation-create').disabled = true;
    const lines = $('variation-lines').value.split('\n').map(s => s.trim()).filter(Boolean);
    if (!lines.length || lines.length > 5) throw new Error('Enter between 1 and 5 alternate lines.');
    const planned = [], revision = planRevision;
    for (const narration of lines) {
      const changes = [{scene_id: Number($('variation-scene').value), narration}];
      const d = await api('/variation-plan', {changes}); planned.push({changes, plan: d.plan});
    }
    if (revision !== planRevision) return;
    plans = planned;
    $('variation-preview-result').innerHTML = planned.map((p, i) => `<p><strong>Version ${i + 1}</strong><br>Before: ${esc(p.plan.edits[0].before)}<br>After: ${esc(p.plan.edits[0].after)}<br>Regenerate: ${esc(p.plan.edits[0].regenerate)}${p.plan.full_voice_track ? ', full voice track' : ''} and final render. Reuse ${p.plan.reused_scenes} unchanged scenes.</p>`).join('');
    $('variation-create').disabled = false;
  }));
  $('variation-create').addEventListener('click', async e => {
    const button = e.target; button.disabled = true; $('variation-preview').disabled = true; $('variation-lines').disabled = true; $('variation-scene').disabled = true;
    const box = $('variation-preview-result'); box.replaceChildren();
    try {
      // Remove only successfully created entries: a partial failure is explicit,
      // and retry cannot recreate the drafts already linked below.
      while (plans.length) {
        const entry = plans[0];
        const d = await api('/controlled-variation', {changes: entry.changes, plan_key: entry.plan.key});
        plans.shift();
        const p = document.createElement('p'), link = document.createElement('a');
        link.href = `${CB.API_ROOT}/project/${d.project.id}/blueprint`; link.textContent = `Open draft ${d.project.id}`; p.append(link); box.append(p);
      }
    } catch (error) { const p = document.createElement('p'); p.textContent = `Stopped: ${error.message} Successful drafts are linked above. Preview remaining changes before trying again.`; box.append(p); plans = []; }
    finally { $('variation-preview').disabled = false; $('variation-lines').disabled = false; $('variation-scene').disabled = false; }
  });
  loadTimeline();
  loadPresets().catch(e => text('preset-status', e.message));
  loadCost().catch(e => text('production-cost', e.message));
})();
