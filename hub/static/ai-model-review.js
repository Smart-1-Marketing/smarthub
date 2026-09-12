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
  const comparison = document.getElementById('model-comparison-form');
  if (!comparison) return;
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
