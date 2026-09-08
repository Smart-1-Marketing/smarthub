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
})();
