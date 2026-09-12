(() => {
  const message = document.getElementById('review-message');
  async function send(url, body, button) {
    button.disabled = true;
    message.textContent = 'Working…';
    try {
      const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Request failed.');
      location.reload();
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  }
  document.getElementById('refresh-models').addEventListener('click', event => send('/api/diagnostics/ai-models/refresh', {}, event.target));
  document.getElementById('model-review-form').addEventListener('submit', event => {
    event.preventDefault();
    send('/api/diagnostics/ai-models/reviews', Object.fromEntries(new FormData(event.target)), event.target.querySelector('button'));
  });
  const queueForm = document.getElementById('comparison-queue-form');
  if (queueForm) {
    // Keep the ID after a network failure, so a retry cannot buy a second pair.
    const requestId = crypto.randomUUID();
    queueForm.addEventListener('submit', async event => {
      event.preventDefault();
      const button = queueForm.querySelector('button');
      const status = document.getElementById('queue-message');
      const data = new FormData(queueForm);
      button.disabled = true;
      status.textContent = 'Reserving budget and queuing…';
      try {
        const response = await fetch('/api/diagnostics/ai-models/jobs', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({profile: data.get('profile'), candidate: data.get('candidate'),
            brief: data.get('brief'), budget_usd: data.get('budget_usd'), request_id: requestId,
            active_model: queueForm.elements.profile.selectedOptions[0].dataset.model,
            confirm_spend: queueForm.elements.confirm_spend.checked})
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not queue comparison.');
        location.reload();
      } catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    });
  }
  document.querySelectorAll('[data-cancel-comparison]').forEach(button => {
    button.addEventListener('click', () => send('/api/diagnostics/ai-models/jobs/cancel',
      {id: button.dataset.cancelComparison}, button));
  });
  const comparison = document.getElementById('model-comparison-form');
  if (!comparison) return;
  document.querySelectorAll('[data-review-comparison]').forEach(button => {
    button.addEventListener('click', () => {
      const job = JSON.parse(button.dataset.reviewComparison);
      comparison.reset();
      comparison.elements.profile.value = job.payload.profile;
      if (comparison.elements.profile.selectedOptions[0]?.dataset.model !== job.payload.active) {
        document.getElementById('comparison-message').textContent = 'The active model changed. Start a new comparison against the current model.';
        return;
      }
      comparison.elements.candidate.value = job.payload.candidate;
      comparison.elements.brief.value = job.payload.brief_id;
      comparison.elements.brief.dispatchEvent(new Event('change'));
      comparison.elements.settings.value = `Queue job ${job.id}; fixed prompt ${job.payload.prompt_version}; Responses API; standard tier; output limit ${job.payload.output_limit}; no tools; model-default reasoning settings.`;
      ['active', 'proposed'].forEach((side, index) => {
        const sample = job.results[index];
        comparison.elements[`${side}_script`].value = sample.script;
        comparison.elements[`${side}_reference`].value = `${job.id} / ${sample.response_id}`;
        comparison.elements[`${side}_latency_seconds`].value = sample.latency_seconds;
        comparison.elements[`${side}_reported_cost_usd`].value = sample.estimated_cost_usd ?? '';
      });
      document.getElementById('comparison-message').textContent = 'Scripts copied. Add your human scores, builder compatibility and voice-read evidence.';
      comparison.scrollIntoView({behavior: 'smooth', block: 'start'});
    });
  });
  comparison.elements.brief.addEventListener('change', () => {
    document.querySelectorAll('[data-comparison-brief]').forEach(item => {
      item.hidden = item.dataset.comparisonBrief !== comparison.elements.brief.value;
    });
  });
  function readWav(file) {
    if (!file || !file.size) return Promise.resolve(null);
    if (file.size > 4 * 1024 * 1024) return Promise.reject(new Error('Each WAV must be at most 4 MB.'));
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(new Error('Could not read the WAV file.'));
      reader.readAsDataURL(file);
    });
  }
  comparison.addEventListener('submit', async event => {
    event.preventDefault();
    const button = comparison.querySelector('button[type="submit"]');
    const status = document.getElementById('comparison-message');
    button.disabled = true;
    status.textContent = 'Checking and saving comparison…';
    try {
      const data = new FormData(comparison);
      const body = {profile: data.get('profile'), brief: data.get('brief'),
        candidate: data.get('candidate'), settings: data.get('settings'),
        active_model: comparison.elements.profile.selectedOptions[0].dataset.model};
      for (const side of ['active', 'proposed']) {
        body[side] = {};
        for (const name of ['script', 'reference', 'quality', 'compatibility', 'delivery', 'latency_seconds', 'reported_cost_usd', 'notes']) {
          body[side][name] = data.get(`${side}_${name}`);
        }
        body[side].wav_base64 = await readWav(data.get(`${side}_wav`));
      }
      const response = await fetch('/api/diagnostics/ai-models/comparisons', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not save comparison.');
      location.reload();
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
})();
