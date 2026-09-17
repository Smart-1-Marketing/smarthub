(() => {
  const message = document.getElementById('report-message');
  async function save(button, url, body) {
    button.disabled = true;
    message.textContent = 'Working…';
    try {
      const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'The request failed. Refresh to inspect sync history.');
      location.reload();
    } catch (error) {
      message.textContent = error.message;
      button.disabled = false;
    }
  }
  const sync = document.getElementById('report-sync');
  sync.addEventListener('click', () => save(sync, '/diagnostics/reporting/tradedesk/sync', {}));
  document.querySelectorAll('[data-account]').forEach(button => {
    button.addEventListener('click', () => {
      const id = button.dataset.account;
      save(button, `/diagnostics/reporting/accounts/${id}/match`, {client_id: document.getElementById(`client-${id}`).value || null});
    });
  });
})();
