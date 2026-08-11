const q = document.getElementById('q');
const statusEl = document.getElementById('status');
let platform = 'all';
let timer;
let currentReportPayload = null;
let currentGtmContext = { account_id: '', container_id: '', google_login: '' };
let chartInstance = null;

// Animated SVG Spinner HTML
const svgSpinner = `
  <svg class="search-spinner" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="12" cy="12" r="10" stroke="#ccd6e5" stroke-width="3"></circle>
    <path d="M12 2C6.47715 2 2 6.47715 2 12" stroke="#1a2e58" stroke-width="3" stroke-linecap="round"></path>
  </svg>
`;

// Toast Notification Engine
function showToast(message, type='success') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${esc(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('toast-fade-out');
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

// Auto-Refresh on Load (No Pre-Population)
window.addEventListener('DOMContentLoaded', async () => {
  if (statusEl) {
    statusEl.innerHTML = `${svgSpinner} <span>Auto-refreshing Google accounts in background...</span>`;
    try {
      const r = await fetch('/api/refresh', { method: 'POST' });
      const data = await r.json();
      statusEl.textContent = `Google accounts refreshed (${data.count || 0} assets indexed). Start typing above to search...`;
    } catch (err) {
      statusEl.textContent = 'Start typing above to search all connected accounts.';
    }
  }

  // Load history on History page
  if (document.getElementById('saved-reports-list')) {
    fetchSavedReports();
    fetchGtmLogs();
  }
});

function esc(v='') {
  return String(v).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function getBadgeClass(platformName) {
  if (platformName.includes('Tag')) return 'gtm';
  if (platformName.includes('Business')) return 'gmb';
  if (platformName.includes('Console')) return 'gsc';
  return 'ga';
}

function renderCard(x) {
  return `
    <article class="result">
      <div class="badge ${getBadgeClass(x.platform)}">${esc(x.platform)}</div>
      <div class="main">
        <h3>${esc(x.name || '(unnamed)')}</h3>
        <div class="login"><b>Google Login:</b> ${esc(x.google_login)}</div>
        <div class="meta"><b>Account:</b> ${esc(x.account_name)} <span>${esc(x.account_id)}</span></div>
        <div class="meta"><b>${esc(x.type)}:</b> ${esc(x.resource_id)}</div>
      </div>
      <div style="display:flex; flex-direction:column; gap:6px;">
        ${x.open_url ? `<a class="open" target="_blank" rel="noopener" href="${esc(x.open_url)}">Open</a>` : ''}
        ${x.platform === 'Google Analytics' ? `<button class="btn btn-auto-all" data-id="${esc(x.resource_id)}" data-login="${esc(x.google_login)}" style="font-size:12px; padding:6px 10px; background:#1a2e58!important;">⚡ Auto-Populate GA Tools</button>` : ''}
        ${x.platform === 'Google Tag Manager' ? `<button class="btn btn-inspect-gtm" data-account="${esc(x.account_id)}" data-container="${esc(x.internal_container_id || x.resource_id)}" data-login="${esc(x.google_login)}" style="font-size:12px; padding:6px 10px; background:#24519c!important;">Inspect GTM Tags</button>` : ''}
      </div>
    </article>
  `;
}

function draw(items) {
  const boxAnalytics = document.getElementById('box-analytics');
  const boxGmb = document.getElementById('box-gmb');
  const boxGsc = document.getElementById('box-gsc');

  const resAnalytics = document.getElementById('results-analytics');
  const resGmb = document.getElementById('results-gmb');
  const resGsc = document.getElementById('results-gsc');

  if (!items.length) {
    if (boxAnalytics) boxAnalytics.style.display = 'none';
    if (boxGmb) boxGmb.style.display = 'none';
    if (boxGsc) boxGsc.style.display = 'none';
    if (statusEl) statusEl.textContent = 'No matches found.';
    return;
  }

  const analyticsItems = items.filter(x => x.platform === 'Google Analytics' || x.platform === 'Google Tag Manager');
  const gmbItems = items.filter(x => x.platform === 'Google Business Profile');
  const gscItems = items.filter(x => x.platform === 'Search Console');

  const cntAll = document.getElementById('cnt-all');
  if (cntAll) cntAll.textContent = `(${items.length})`;
  const cntGa = document.getElementById('cnt-ga');
  if (cntGa) cntGa.textContent = `(${analyticsItems.length})`;
  const cntGsc = document.getElementById('cnt-gsc');
  if (cntGsc) cntGsc.textContent = `(${gscItems.length})`;
  const cntGmb = document.getElementById('cnt-gmb');
  if (cntGmb) cntGmb.textContent = `(${gmbItems.length})`;

  if (analyticsItems.length && (platform === 'all' || platform === 'analytics')) {
    if (resAnalytics) resAnalytics.innerHTML = analyticsItems.map(renderCard).join('');
    if (boxAnalytics) boxAnalytics.style.display = 'block';
  } else if (boxAnalytics) {
    boxAnalytics.style.display = 'none';
  }

  if (gmbItems.length && (platform === 'all' || platform === 'gmb')) {
    if (resGmb) resGmb.innerHTML = gmbItems.map(renderCard).join('');
    if (boxGmb) boxGmb.style.display = 'block';
  } else if (boxGmb) {
    boxGmb.style.display = 'none';
  }

  if (gscItems.length && (platform === 'all' || platform === 'gsc')) {
    if (resGsc) resGsc.innerHTML = gscItems.map(renderCard).join('');
    if (boxGsc) boxGsc.style.display = 'block';
  } else if (boxGsc) {
    boxGsc.style.display = 'none';
  }

  document.querySelectorAll('.btn-auto-all').forEach(btn => {
    btn.addEventListener('click', () => {
      window.location.href = `/ga-tools?id=${encodeURIComponent(btn.dataset.id)}&login=${encodeURIComponent(btn.dataset.login)}`;
    });
  });

  document.querySelectorAll('.btn-inspect-gtm').forEach(btn => {
    btn.addEventListener('click', async () => {
      const account_id = btn.dataset.account;
      const container_id = btn.dataset.container;
      const google_login = btn.dataset.login;

      currentGtmContext = { account_id, container_id, google_login };

      const modal = document.getElementById('gtm-modal');
      const body = document.getElementById('gtm-modal-body');
      modal.style.display = 'flex';
      body.innerHTML = renderSkeletonCard();

      const resp = await fetch('/api/gtm/inspect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ account_id, container_id, google_login })
      });

      const data = await resp.json();
      if (!resp.ok) {
        body.innerHTML = `<div style="color:#c5221f; font-size:13px;">Error: ${esc(data.error || 'Failed to fetch GTM workspace.')}</div>`;
        return;
      }

      body.innerHTML = `
        <div style="font-size:13px;">
          <h4 style="margin:0 0 8px; color:#1a2e58;">🏷️ Tags (${data.tags.length}):</h4>
          <ul style="margin:0 0 14px; padding-left:18px;">
            ${data.tags.map(t => `<li><b>${esc(t.name)}</b> <span style="color:#69758a;">(${esc(t.type)})${t.paused ? ' [PAUSED]' : ''}</span></li>`).join('') || '<li>No tags defined.</li>'}
          </ul>

          <h4 style="margin:0 0 8px; color:#1a2e58;">⚡ Triggers (${data.triggers.length}):</h4>
          <ul style="margin:0 0 14px; padding-left:18px;">
            ${data.triggers.map(tr => `<li><b>${esc(tr.name)}</b> <span style="color:#69758a;">(${esc(tr.type)})</span></li>`).join('') || '<li>No triggers defined.</li>'}
          </ul>

          <h4 style="margin:0 0 8px; color:#1a2e58;">🔧 Variables (${data.variables.length}):</h4>
          <ul style="margin:0; padding-left:18px;">
            ${data.variables.map(v => `<li><b>${esc(v.name)}</b> <span style="color:#69758a;">(${esc(v.type)})</span></li>`).join('') || '<li>No user variables defined.</li>'}
          </ul>
        </div>
      `;
    });
  });
}

function renderSkeletonCard() {
  return `
    <div class="skeleton-card">
      <div class="skeleton-line" style="width: 40%;"></div>
      <div class="skeleton-line" style="width: 80%;"></div>
      <div class="skeleton-line" style="width: 60%;"></div>
    </div>
  `;
}

// Search Logic with Animated SVG Indicator
async function search() {
  if (!q) return;
  const value = q.value.trim();
  if (!value) {
    if (document.getElementById('box-analytics')) document.getElementById('box-analytics').style.display = 'none';
    if (document.getElementById('box-gmb')) document.getElementById('box-gmb').style.display = 'none';
    if (document.getElementById('box-gsc')) document.getElementById('box-gsc').style.display = 'none';
    statusEl.textContent = 'Start typing above to search all connected accounts.';
    return;
  }

  statusEl.innerHTML = `${svgSpinner} <span>Searching all connected Google accounts…</span>`;

  const r = await fetch(`/api/search?q=${encodeURIComponent(value)}&platform=${encodeURIComponent(platform)}`);
  const data = await r.json();
  if (!r.ok) {
    statusEl.textContent = data.error || 'Search failed.';
    return;
  }
  const items = data.results || [];
  const problemCount = (data.errors || []).length;
  statusEl.textContent = `${items.length} match${items.length === 1 ? '' : 'es'}${problemCount ? ` · ${problemCount} account refresh error${problemCount === 1 ? '' : 's'}` : ''}`;
  draw(items);
}

if (q) q.addEventListener('input', () => {
  clearTimeout(timer);
  timer = setTimeout(search, 250);
});

// STANDALONE PAGE SEARCH LOOKUPS
// 1. GTM Tools Page Lookup
const gtmLookupBtn = document.getElementById('gtm-lookup-btn');
const gtmLookupInput = document.getElementById('gtm-lookup-q');
const gtmLookupResults = document.getElementById('gtm-lookup-results');

if (gtmLookupBtn && gtmLookupInput) {
  gtmLookupBtn.addEventListener('click', async () => {
    const query = gtmLookupInput.value.trim();
    if (!query) return;

    gtmLookupResults.innerHTML = renderSkeletonCard();
    const r = await fetch(`/api/search?q=${encodeURIComponent(query)}&platform=gtm`);
    const data = await r.json();

    if (data.results && data.results.length) {
      gtmLookupResults.innerHTML = data.results.map(item => `
        <div style="padding:8px; background:#fff; border:1px solid #ccd6e5; border-radius:6px; margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;">
          <div>
            <strong>${esc(item.name)}</strong> (${esc(item.resource_id)})
            <div style="font-size:11px; color:#69758a;">User: ${esc(item.google_login)}</div>
          </div>
          <button class="btn btn-use-gtm" data-login="${esc(item.google_login)}" data-acct="${esc(item.account_id)}" data-container="${esc(item.internal_container_id || item.resource_id)}" style="font-size:11px; padding:4px 8px; background:#24519c!important;">Auto-Populate Pixel Form</button>
        </div>
      `).join('');

      document.querySelectorAll('.btn-use-gtm').forEach(btn => {
        btn.addEventListener('click', () => {
          document.getElementById('pixel-login').value = btn.dataset.login;
          document.getElementById('pixel-account-id').value = btn.dataset.acct;
          document.getElementById('pixel-container-id').value = btn.dataset.container;
          currentGtmContext = { account_id: btn.dataset.acct, container_id: btn.dataset.container, google_login: btn.dataset.login };
          showToast("Loaded GTM container parameters into pixel form!", "success");
        });
      });
    } else {
      gtmLookupResults.innerHTML = '<span style="font-size:12px; color:#c5221f;">No GTM containers found.</span>';
    }
  });
}

// 2. Webmaster Tools Page Lookup
const gscLookupBtn = document.getElementById('gsc-lookup-btn');
const gscLookupInput = document.getElementById('gsc-lookup-q');
const gscLookupResults = document.getElementById('gsc-lookup-results');

if (gscLookupBtn && gscLookupInput) {
  gscLookupBtn.addEventListener('click', async () => {
    const query = gscLookupInput.value.trim();
    if (!query) return;

    gscLookupResults.innerHTML = renderSkeletonCard();
    const r = await fetch(`/api/search?q=${encodeURIComponent(query)}&platform=gsc`);
    const data = await r.json();

    if (data.results && data.results.length) {
      gscLookupResults.innerHTML = data.results.map(item => `
        <div style="padding:8px; background:#fff; border:1px solid #ccd6e5; border-radius:6px; margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;">
          <div>
            <strong>${esc(item.name)}</strong>
            <div style="font-size:11px; color:#69758a;">User: ${esc(item.google_login)}</div>
          </div>
          <button class="btn btn-use-gsc" data-login="${esc(item.google_login)}" data-url="${esc(item.name)}" style="font-size:11px; padding:4px 8px; background:#c5221f!important;">Auto-Populate Bulk Form</button>
        </div>
      `).join('');

      document.querySelectorAll('.btn-use-gsc').forEach(btn => {
        btn.addEventListener('click', () => {
          document.getElementById('gsc-bulk-account').value = btn.dataset.login;
          const textArea = document.getElementById('gsc-bulk-text');
          const cleanUrl = btn.dataset.url;
          textArea.value = `${cleanUrl} | ${cleanUrl.replace(/\/$/, '')}/sitemap.xml`;
          showToast("Loaded property & sitemap into bulk deployer!", "success");
        });
      });
    } else {
      gscLookupResults.innerHTML = '<span style="font-size:12px; color:#c5221f;">No Search Console properties found.</span>';
    }
  });
}

const gtmModalClose = document.getElementById('gtm-modal-close');
if (gtmModalClose) {
  gtmModalClose.addEventListener('click', () => {
    document.getElementById('gtm-modal').style.display = 'none';
  });
}

// Diagnostics Modal Handler
const btnOpenDiagnostics = document.getElementById('btn-open-diagnostics');
const diagnosticsModal = document.getElementById('diagnostics-modal');
const diagnosticsClose = document.getElementById('diagnostics-modal-close');
const diagnosticsBody = document.getElementById('diagnostics-modal-body');

if (btnOpenDiagnostics) {
  btnOpenDiagnostics.addEventListener('click', async (e) => {
    e.preventDefault();
    diagnosticsModal.style.display = 'flex';
    diagnosticsBody.innerHTML = renderSkeletonCard();

    try {
      const [healthRes, debugRes] = await Promise.all([
        fetch('/health').then(async r => {
          const text = await r.text();
          try { return JSON.parse(text); } catch(e) { return { ok: false, error: text }; }
        }),
        fetch('/debug/accounts').then(async r => {
          const text = await r.text();
          try { return JSON.parse(text); } catch(e) { return { error: text, diagnostics: [] }; }
        })
      ]);

      let html = `
        <div style="font-size:13px;">
          <h4 style="margin:0 0 8px; color:#1a2e58;">System Environment Status:</h4>
          <div style="background:#f8fafc; padding:12px; border:1px solid #d8e0eb; border-radius:8px; margin-bottom:16px; line-height:1.6;">
            <div>Overall System Health: <b>${healthRes.ok ? '🟢 Operational' : '🔴 Issue Detected'}</b></div>
            <div>Google Client ID: ${healthRes.google_client_id_configured ? '🟢 Configured' : '🔴 Missing'}</div>
            <div>Google Client Secret: ${healthRes.google_client_secret_configured ? '🟢 Configured' : '🔴 Missing'}</div>
            <div>Token Encryption Key: ${healthRes.token_encryption_key_configured ? '🟢 Configured' : '🔴 Missing'}</div>
            <div>Database Path: <code>${esc(healthRes.token_db_path)}</code></div>
            <div>Connected Accounts Count: <b>${healthRes.connected_account_count}</b></div>
          </div>

          <h4 style="margin:0 0 8px; color:#1a2e58;">Connected Account Diagnostics:</h4>
      `;

      if (!debugRes.diagnostics || !debugRes.diagnostics.length) {
        html += '<div style="color:#69758a; font-style:italic;">No Google accounts currently connected to test.</div>';
      } else {
        html += debugRes.diagnostics.map(acc => `
          <div style="background:#fff; border:1px solid #d8e0eb; border-radius:8px; padding:12px; margin-bottom:10px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
              <strong style="color:#1a2e58; font-size:14px;">${esc(acc.email)}</strong>
              <span style="font-size:11px; background:${acc.token_refresh_status === 'SUCCESS' ? '#e6f4ea' : '#fce8e6'}; color:${acc.token_refresh_status === 'SUCCESS' ? '#137333' : '#c5221f'}; padding:2px 8px; border-radius:4px; font-weight:bold;">
                OAuth Token: ${esc(acc.token_refresh_status)}
              </span>
            </div>
            <div style="font-size:12px; color:#58677e; display:grid; grid-template-columns:1fr 1fr 1fr; gap:6px; background:#f0f4f9; padding:8px; border-radius:6px;">
              <div>GA4 Admin: <b>${acc.ga4_api_status === 'SUCCESS' ? '🟢 OK' : '🔴 Failed'}</b></div>
              <div>GTM API: <b>${acc.gtm_api_status === 'SUCCESS' ? '🟢 OK' : '🔴 Failed'}</b></div>
              <div>Search Console: <b>${acc.gsc_api_status === 'SUCCESS' ? '🟢 OK' : '🔴 Failed'}</b></div>
            </div>
          </div>
        `).join('');
      }

      html += '</div>';
      diagnosticsBody.innerHTML = html;
    } catch (err) {
      diagnosticsBody.innerHTML = `<div style="color:#c5221f; font-size:13px;">Failed to fetch system diagnostics: ${esc(err.message)}</div>`;
    }
  });
}

if (diagnosticsClose) {
  diagnosticsClose.addEventListener('click', () => {
    diagnosticsModal.style.display = 'none';
  });
}

// Bulk Search Console Deployer
const btnRunGscBulk = document.getElementById('btn-run-gsc-bulk');
const gscBulkResults = document.getElementById('gsc-bulk-results');

if (btnRunGscBulk) {
  btnRunGscBulk.addEventListener('click', async () => {
    const google_login = document.getElementById('gsc-bulk-account').value;
    const rawText = document.getElementById('gsc-bulk-text').value.trim();

    if (!google_login || !rawText) {
      showToast("Please choose an account and enter domain lines.", "error");
      return;
    }

    const lines = rawText.split('\n');
    const entries = lines.map(l => {
      const parts = l.split('|');
      return {
        site_url: parts[0] ? parts[0].trim() : '',
        sitemap_url: parts[1] ? parts[1].trim() : ''
      };
    }).filter(e => e.site_url);

    gscBulkResults.innerHTML = '<span style="font-size:13px; color:#1a2e58;">Executing bulk deployment across Search Console...</span>';

    const resp = await fetch('/api/gsc/bulk-add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ google_login, entries })
    });

    const data = await resp.json();
    if (!resp.ok) {
      gscBulkResults.innerHTML = `<span style="color:#c5221f; font-size:13px;">Error: ${esc(data.error)}</span>`;
      return;
    }

    gscBulkResults.innerHTML = `
      <div style="background:#fff; border:1px solid #d8e0eb; border-radius:8px; padding:12px; font-size:13px;">
        ${data.results.map(r => `
          <div style="margin-bottom:4px; color:${r.status === 'SUCCESS' ? '#137333' : '#c5221f'};">
            <b>${esc(r.site_url)}</b>: ${esc(r.details)}
          </div>
        `).join('')}
      </div>
    `;
    showToast("Bulk Search Console deployment completed!", "success");
  });
}

// Custom Pixel Deployer Listener
const btnDeployPixel = document.getElementById('btn-deploy-pixel');
const pixelResults = document.getElementById('pixel-deploy-results');

if (btnDeployPixel) {
  btnDeployPixel.addEventListener('click', async () => {
    const google_login = document.getElementById('pixel-login').value.trim();
    const account_id = document.getElementById('pixel-account-id').value.trim();
    const container_id = document.getElementById('pixel-container-id').value.trim();
    const tag_name = document.getElementById('pixel-tag-name').value.trim();
    const pixel_code = document.getElementById('pixel-code-input').value.trim();

    if (!google_login || !account_id || !container_id || !tag_name || !pixel_code) {
      showToast("Please fill in all pixel form fields including the script code.", "error");
      return;
    }

    pixelResults.innerHTML = renderSkeletonCard();

    const resp = await fetch('/api/gtm/deploy-pixel', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        google_login, account_id, container_id, tag_name, pixel_code
      })
    });

    const data = await resp.json();
    if (resp.ok) {
      pixelResults.innerHTML = `<div style="color:#137333; font-size:13px; font-weight:bold;">✓ ${esc(data.message)}</div>`;
      showToast(`Custom pixel '${tag_name}' deployed successfully!`, "success");
    } else {
      pixelResults.innerHTML = `<div style="color:#c5221f; font-size:13px;">Error: ${esc(data.error)}</div>`;
      showToast("Failed to deploy pixel.", "error");
    }
  });
}

// AI GTM Event Generator Logic
const gtmGenBtn = document.getElementById('gtm-gen-btn');
const gtmGenUrlInput = document.getElementById('gtm-gen-url');
const gtmGenResults = document.getElementById('gtm-gen-results');

if (gtmGenBtn && gtmGenUrlInput) {
  gtmGenBtn.addEventListener('click', async () => {
    const url = gtmGenUrlInput.value.trim();
    if (!url) {
      showToast("Please enter a valid website URL.", "error");
      return;
    }

    gtmGenResults.innerHTML = renderSkeletonCard();

    const resp = await fetch('/api/gtm/generate-events', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ url })
    });

    const data = await resp.json();
    if (!resp.ok) {
      gtmGenResults.innerHTML = `<span style="font-size:13px; color:#c5221f;">Error: ${esc(data.error || 'Failed to analyze page.')}</span>`;
      return;
    }

    const events = data.suggested_events || [];
    if (!events.length) {
      gtmGenResults.innerHTML = '<span style="font-size:13px; color:#69758a;">No lead forms or phone/email links detected on this page.</span>';
      return;
    }

    gtmGenResults.innerHTML = `
      <div style="background:#fff; border:1px solid #d8e0eb; border-radius:8px; padding:12px; font-size:13px;">
        <h4 style="margin:0 0 8px; color:#1a2e58;">Found ${events.length} Recommended Events for "${esc(data.url)}":</h4>
        ${events.map((ev, idx) => `
          <div style="padding:8px 0; border-bottom:1px solid #f0f4f9; display:flex; justify-content:space-between; align-items:center;">
            <div>
              <strong>${esc(ev.tag_name)}</strong> <code>[${esc(ev.event_name)}]</code>
              <div style="color:#69758a; font-size:12px;">${esc(ev.description)}</div>
            </div>
            ${currentGtmContext.container_id ? `<button class="btn btn-deploy-gtm-tag" data-idx="${idx}" style="font-size:11px; padding:4px 8px;">Deploy to GTM</button>` : '<span style="font-size:11px; color:#8a95a7;">(Inspect GTM container above first to auto-deploy)</span>'}
          </div>
        `).join('')}
      </div>
    `;

    document.querySelectorAll('.btn-deploy-gtm-tag').forEach(btn => {
      btn.addEventListener('click', async () => {
        const ev = events[parseInt(btn.dataset.idx)];
        btn.disabled = true;
        btn.textContent = 'Deploying…';

        const deployResp = await fetch('/api/gtm/deploy-event', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            account_id: currentGtmContext.account_id,
            container_id: currentGtmContext.container_id,
            google_login: currentGtmContext.google_login,
            event: ev
          })
        });

        if (deployResp.ok) {
          btn.textContent = '✓ Deployed!';
          btn.style.background = '#137333';
          showToast(`Deployed ${ev.tag_name} to GTM!`, 'success');
        } else {
          btn.textContent = 'Failed';
          btn.style.background = '#c5221f';
          showToast("Failed to deploy tag to GTM.", "error");
        }
      });
    });
  });
}

// "Ask Analytics" Natural Language Query Handler
const askBtn = document.getElementById('ask-btn');
const askInput = document.getElementById('ask-q');
const askResults = document.getElementById('ask-results');

if (askBtn && askInput) {
  askBtn.addEventListener('click', async () => {
    const question = askInput.value.trim();
    const property_id = document.getElementById('comp-property-id').value.trim();
    const google_login = document.getElementById('comp-login').value.trim();

    if (!property_id || !google_login) {
      showToast("Please enter a GA4 Property ID and Login email first.", "error");
      return;
    }
    if (!question) {
      showToast("Please enter a question.", "error");
      return;
    }

    askResults.innerHTML = renderSkeletonCard();

    const resp = await fetch('/api/ga4/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ property_id, google_login, question })
    });

    const data = await resp.json();
    if (!resp.ok) {
      askResults.innerHTML = `<span style="font-size:13px; color:#c5221f;">Error: ${esc(data.error || 'Failed to query GA4.')}</span>`;
      return;
    }

    askResults.innerHTML = `
      <div style="padding:12px; background:#fff; border:1px solid #d8e0eb; border-radius:8px; font-size:13px; color:#1a2e58; line-height:1.5;">
        ${data.answer.replace(/\n/g, '<br>').replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')}
      </div>
    `;
  });
}

function applyPresetDates(presetKey) {
  const now = new Date();
  const formatDate = d => d.toISOString().split('T')[0];

  let p1Start, p1End;

  if (presetKey === 'last_month') {
    const firstThisMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    p1End = new Date(firstThisMonth.getTime() - 86400000);
    p1Start = new Date(p1End.getFullYear(), p1End.getMonth(), 1);
  } else if (presetKey === 'mtd') {
    p1Start = new Date(now.getFullYear(), now.getMonth(), 1);
    p1End = new Date(now.getTime() - 86400000);
  } else if (presetKey === 'last_week') {
    const dayOfWeek = now.getDay();
    p1End = new Date(now.getTime() - (dayOfWeek + 1) * 86400000);
    p1Start = new Date(p1End.getTime() - 6 * 86400000);
  } else if (presetKey === 'last_quarter') {
    const currentQuarter = Math.floor(now.getMonth() / 3);
    const lastQuarterMonth = (currentQuarter === 0 ? 3 : currentQuarter - 1) * 3;
    const year = currentQuarter === 0 ? now.getFullYear() - 1 : now.getFullYear();
    p1Start = new Date(year, lastQuarterMonth, 1);
    p1End = new Date(year, lastQuarterMonth + 3, 0);
  } else if (presetKey === 'ytd') {
    p1Start = new Date(now.getFullYear(), 0, 1);
    p1End = new Date(now.getTime() - 86400000);
  }

  if (p1Start && p1End && document.getElementById('p1-start')) {
    document.getElementById('p1-start').value = formatDate(p1Start);
    document.getElementById('p1-end').value = formatDate(p1End);
  }
}

const presetSelect = document.getElementById('comp-date-preset');
if (presetSelect) {
  presetSelect.addEventListener('change', (e) => {
    applyPresetDates(e.target.value);
  });
  applyPresetDates('last_month');
}

const filterBtns = document.querySelectorAll('.filter');
if (filterBtns.length) {
  filterBtns.forEach(btn => btn.addEventListener('click', () => {
    filterBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    platform = btn.dataset.platform;
    search();
  }));
}

const refresh = document.getElementById('refresh');
if (refresh) refresh.addEventListener('click', async () => {
  refresh.disabled = true;
  refresh.textContent = 'Refreshing…';
  const r = await fetch('/api/refresh', {method:'POST'});
  const data = await r.json();
  refresh.textContent = `Refreshed ${data.count || 0} records`;
  showToast(`Refreshed ${data.count || 0} Google records!`, 'success');
  setTimeout(() => { refresh.textContent = 'Refresh Google data'; refresh.disabled = false; }, 1800);
  search();
});

document.querySelectorAll('.disconnect').forEach(btn => btn.addEventListener('click', async () => {
  const email = btn.dataset.email;
  const r = await fetch(`/disconnect/${encodeURIComponent(email)}`, {method:'POST'});
  if (r.ok) window.location.reload();
}));

const scopeSelect = document.getElementById('comp-scope-type');
const pageWrapper = document.getElementById('page-path-wrapper');

if (scopeSelect) {
  scopeSelect.addEventListener('change', () => {
    if (scopeSelect.value === 'page') {
      pageWrapper.style.display = 'block';
    } else {
      pageWrapper.style.display = 'none';
    }
  });
}

const runCompBtn = document.getElementById('run-ga4-comp');
if (runCompBtn) {
  runCompBtn.addEventListener('click', async () => {
    const property_id = document.getElementById('comp-property-id').value.trim();
    const google_login = document.getElementById('comp-login').value.trim();
    const period_type = document.getElementById('comp-period-type').value;
    let scope_type = document.getElementById('comp-scope-type').value;
    let page_path = document.getElementById('comp-page-path').value.trim();
    const source_medium = document.getElementById('comp-source-medium').value;
    const tone = document.getElementById('comp-tone').value;
    const preset = document.getElementById('comp-preset').value;
    const p1_start = document.getElementById('p1-start').value;
    const p1_end = document.getElementById('p1-end').value;

    let limit = 15;
    if (scope_type === 'top_10') { scope_type = 'multiple'; limit = 10; }
    else if (scope_type === 'top_25') { scope_type = 'multiple'; limit = 25; }
    else if (scope_type === 'top_50') { scope_type = 'multiple'; limit = 50; }

    const resBox = document.getElementById('ai-comp-results');
    resBox.innerHTML = renderSkeletonCard();

    const resp = await fetch('/api/ga4/compare', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        property_id, google_login, period_type, scope_type,
        page_path, source_medium, tone, preset, p1_start, p1_end, limit
      })
    });

    const data = await resp.json();
    if (!resp.ok) {
      resBox.innerHTML = `<div style="color:#c5221f; font-size:13px;">Error: ${esc(data.error || 'Failed to generate comparison.')}</div>`;
      return;
    }

    currentReportPayload = data;
    const ai = data.ai_analysis;
    const m1 = data.metrics_p1;
    const m2 = data.metrics_p2;
    const breakdown = data.breakdown || [];

    const formatSecs = (s) => {
      const m = Math.floor(s / 60);
      const sec = Math.floor(s % 60);
      return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
    };

    const avgTimeP1 = m1.sessions > 0 ? formatSecs(m1.userEngagementDuration / m1.sessions) : '0s';
    const avgTimeP2 = m2.sessions > 0 ? formatSecs(m2.userEngagementDuration / m2.sessions) : '0s';

    const plainTextAnalysis = `
AI Traffic Summary: ${ai.status}
- ${ai.insights.join('\n- ').replace(/[*`]/g, '')}

${data.p1_label}
Sessions: ${m1.sessions.toLocaleString()}
Engaged Sessions: ${m1.engagedSessions.toLocaleString()}
Avg Engagement Time: ${avgTimeP1}
Total Events: ${m1.eventCount.toLocaleString()}
Key Events: ${m1.keyEvents.toLocaleString()}

${data.p2_label}
Sessions: ${m2.sessions.toLocaleString()}
Engaged Sessions: ${m2.engagedSessions.toLocaleString()}
Avg Engagement Time: ${avgTimeP2}
Total Events: ${m2.eventCount.toLocaleString()}
Key Events: ${m2.keyEvents.toLocaleString()}
    `.trim();

    resBox.innerHTML = `
      <div class="ai-box">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
          <h4 style="margin:0; color:#1a2e58; font-size:15px;">🤖 AI Summary (${tone === 'positive' ? '🟢 Client Wins' : '🔴 Optimization Focus'}): ${esc(ai.status)}</h4>
          <button id="btn-copy-analysis" class="btn" style="font-size:12px; padding:5px 10px; background:#137333!important;">📋 Copy Analysis</button>
        </div>
        <ul style="margin:0 0 12px; padding-left:18px; font-size:13px; color:#202124;">
          ${ai.insights.map(i => `<li>${i.replace(/\*\*(.*?)\*\*/g, '<b>$1</b>').replace(/`(.*?)`/g, '<code>$1</code>')}</li>`).join('')}
        </ul>

        <div style="background:#fff; padding:12px; border-radius:8px; border:1px solid #d8e0eb; margin-bottom:12px;">
          <canvas id="ga4CompareChart" height="120"></canvas>
        </div>

        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px; background:#fff; padding:12px; border-radius:8px; border:1px solid #d8e0eb; font-size:13px;">
          <div>
            <strong style="color:#1a2e58;">${esc(data.p1_label)}</strong>
            <div>Sessions: <b>${m1.sessions.toLocaleString()}</b></div>
            <div>Engaged Sessions: <b>${m1.engagedSessions.toLocaleString()}</b></div>
            <div>Avg Engagement Time: <b>${avgTimeP1}</b></div>
            <div>Total Events: <b>${m1.eventCount.toLocaleString()}</b></div>
            <div>Key Events: <b>${m1.keyEvents.toLocaleString()}</b></div>
          </div>
          <div>
            <strong style="color:#1a2e58;">${esc(data.p2_label)}</strong>
            <div>Sessions: <b>${m2.sessions.toLocaleString()}</b></div>
            <div>Engaged Sessions: <b>${m2.engagedSessions.toLocaleString()}</b></div>
            <div>Avg Engagement Time: <b>${avgTimeP2}</b></div>
            <div>Total Events: <b>${m2.eventCount.toLocaleString()}</b></div>
            <div>Key Events: <b>${m2.keyEvents.toLocaleString()}</b></div>
          </div>
        </div>

        ${breakdown.length ? `
          <div style="margin-top:14px; background:#fff; padding:12px; border-radius:8px; border:1px solid #d8e0eb; overflow-x:auto;">
            <h5 style="margin:0 0 8px; font-size:13px; color:#1a2e58;">Detailed Performance Breakdown (${breakdown.length} items)</h5>
            <table style="width:100%; border-collapse:collapse; font-size:12px; text-align:left; min-width:600px;">
              <thead>
                <tr style="border-bottom:2px solid #e1e7ef; color:#58677e;">
                  <th style="padding:6px;">Item / Channel</th>
                  <th style="padding:6px; text-align:right;">Sessions (P1 / P2)</th>
                  <th style="padding:6px; text-align:right;">Engaged Sessions</th>
                  <th style="padding:6px; text-align:right;">Avg Time on Site</th>
                  <th style="padding:6px; text-align:right;">Total Events</th>
                  <th style="padding:6px; text-align:right;">Key Events</th>
                </tr>
              </thead>
              <tbody>
                ${breakdown.map(b => {
                  const diff = b.session_diff;
                  const diffColor = diff > 0 ? '#137333' : (diff < 0 ? '#c5221f' : '#58677e');
                  const diffSign = diff > 0 ? '+' : '';
                  return `
                    <tr style="border-bottom:1px solid #f0f4f9;">
                      <td style="padding:6px;"><code>${esc(b.name)}</code></td>
                      <td style="padding:6px; text-align:right;"><b>${b.p1_sessions.toLocaleString()}</b> <span style="color:#8a95a7;">/ ${b.p2_sessions.toLocaleString()}</span> <span style="font-weight:bold; color:${diffColor};">(${diffSign}${diff.toLocaleString()})</span></td>
                      <td style="padding:6px; text-align:right;"><b>${b.p1_engaged.toLocaleString()}</b> <span style="color:#8a95a7;">/ ${b.p2_engaged.toLocaleString()}</span></td>
                      <td style="padding:6px; text-align:right;"><b>${b.p1_avg_time_str}</b> <span style="color:#8a95a7;">/ ${b.p2_avg_time_str}</span></td>
                      <td style="padding:6px; text-align:right;"><b>${b.p1_events.toLocaleString()}</b> <span style="color:#8a95a7;">/ ${b.p2_events.toLocaleString()}</span></td>
                      <td style="padding:6px; text-align:right;"><b>${b.p1_convs.toLocaleString()}</b> <span style="color:#8a95a7;">/ ${b.p2_convs.toLocaleString()}</span></td>
                    </tr>
                  `;
                }).join('')}
              </tbody>
            </table>
          </div>
        ` : ''}
      </div>
    `;

    renderChart(data);

    document.getElementById('btn-copy-analysis').addEventListener('click', () => {
      navigator.clipboard.writeText(plainTextAnalysis);
      const copyBtn = document.getElementById('btn-copy-analysis');
      copyBtn.textContent = '✓ Copied!';
      showToast("Copied analysis text to clipboard!", "success");
      setTimeout(() => { copyBtn.textContent = '📋 Copy Analysis'; }, 2000);
    });
  });
}

function renderChart(data) {
  const ctx = document.getElementById('ga4CompareChart');
  if (!ctx) return;

  if (chartInstance) {
    chartInstance.destroy();
  }

  const m1 = data.metrics_p1;
  const m2 = data.metrics_p2;

  chartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['Sessions', 'Engaged Sessions', 'Key Events (x100)'],
      datasets: [
        {
          label: 'Selected Period (P1)',
          data: [m1.sessions, m1.engagedSessions, m1.keyEvents * 100],
          backgroundColor: '#1a2e58'
        },
        {
          label: 'Prior Period (P2)',
          data: [m2.sessions, m2.engagedSessions, m2.keyEvents * 100],
          backgroundColor: '#8a95a7'
        }
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'top' },
        title: { display: true, text: 'Period Performance Metric Comparison' }
      }
    }
  });
}

// Saved Reports & Audit Log Search
async function fetchSavedReports(query='') {
  const listEl = document.getElementById('saved-reports-list');
  if (!listEl) return;

  const resp = await fetch(`/api/reports/search?q=${encodeURIComponent(query)}`);
  const data = await resp.json();
  
  if (!data.reports || !data.reports.length) {
    listEl.innerHTML = '<div style="padding:16px; background:#fff; border:1px solid #d8e0eb; border-radius:8px; font-size:13px; color:#69758a;">No historical reports found.</div>';
    return;
  }

  listEl.innerHTML = data.reports.map(r => `
    <div style="padding:12px 14px; background:#fff; border:1px solid #d8e0eb; border-radius:8px; margin-bottom:10px; font-size:13px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <strong style="font-size:15px; color:#1a2e58;">${esc(r.customer_name)}</strong>
        <span style="color:#8a95a7; font-size:12px;">Saved on ${new Date(r.created_at * 1000).toLocaleDateString()}</span>
      </div>
      <div style="margin-top:4px; color:#58677e;">
        <b>Summary Title:</b> ${esc(r.summary_title)} | <b>Property ID:</b> <code>${esc(r.property_id)}</code> | <b>User:</b> ${esc(r.google_login)}
      </div>
    </div>
  `).join('');
}

const btnSearchSaved = document.getElementById('btn-search-saved');
if (btnSearchSaved) {
  btnSearchSaved.addEventListener('click', () => {
    fetchSavedReports(document.getElementById('search-saved-q').value);
  });
}

async function fetchGtmLogs(query='') {
  const listEl = document.getElementById('gtm-logs-list');
  if (!listEl) return;

  const resp = await fetch(`/api/gtm/logs/search?q=${encodeURIComponent(query)}`);
  const data = await resp.json();
  
  if (!data.logs || !data.logs.length) {
    listEl.innerHTML = '<div style="padding:16px; background:#fff; border:1px solid #d8e0eb; border-radius:8px; font-size:13px; color:#69758a;">No recorded GTM change logs found.</div>';
    return;
  }

  listEl.innerHTML = data.logs.map(log => {
    const dateStr = new Date(log.created_at * 1000).toLocaleString();
    return `
      <div style="padding:12px 14px; background:#fff; border:1px solid #d8e0eb; border-radius:8px; margin-bottom:10px; font-size:13px;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <span style="background:#eaf2ff; color:#24519c; font-weight:bold; padding:3px 8px; border-radius:4px; font-size:12px;">${esc(log.action_type)}</span>
            <strong style="margin-left:8px; color:#1a2e58; font-size:14px;">${esc(log.tag_name)}</strong>
          </div>
          <span style="color:#8a95a7; font-size:12px;">${esc(dateStr)}</span>
        </div>
        <div style="margin-top:6px; color:#58677e;">
          <b>Container ID:</b> <code>${esc(log.container_id)}</code> | 
          <b>Account ID:</b> <code>${esc(log.account_id)}</code> | 
          <b>Executed By:</b> ${esc(log.google_login)}
        </div>
      </div>
    `;
  }).join('');
}

const btnSearchGtmLogs = document.getElementById('btn-search-gtm-logs');
if (btnSearchGtmLogs) {
  btnSearchGtmLogs.addEventListener('click', () => {
    fetchGtmLogs(document.getElementById('search-gtm-logs-q').value);
  });
}

const manualBtn = document.getElementById('manual-btn');
const manualInput = document.getElementById('manual-q');
const manualResults = document.getElementById('manual-results');

if (manualBtn && manualInput) {
  manualBtn.addEventListener('click', () => {
    const query = manualInput.value.trim();
    if (!query) {
      manualResults.innerHTML = '<span style="font-size:13px; color:#c5221f;">Please enter a business name or URL.</span>';
      return;
    }

    const encoded = encodeURIComponent(query);
    const mapsSearchUrl = `https://www.google.com/maps/search/${encoded}`;
    const gmbClaimUrl = `https://business.google.com/add`;
    const googleSearchUrl = `https://www.google.com/search?q=${encoded}`;

    manualResults.innerHTML = `
      <div class="manual-results-group">
        <strong>Quick Links for "${esc(query)}":</strong>
        <a href="${mapsSearchUrl}" target="_blank" rel="noopener" class="open" style="padding: 6px 10px; font-size: 12px;">Search Maps</a>
        <a href="${googleSearchUrl}" target="_blank" rel="noopener" class="open" style="padding: 6px 10px; font-size: 12px; background: #1a2e58!important;">Google Search</a>
        <a href="${gmbClaimUrl}" target="_blank" rel="noopener" class="open" style="padding: 6px 10px; font-size: 12px; background: #137333!important;">Claim/Add on GMB</a>
      </div>
    `;
  });
}
