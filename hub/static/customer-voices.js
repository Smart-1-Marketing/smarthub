/* Staff voice library. Never retain the customer's sample recordings in browser storage. */
(() => {
  const form = document.getElementById('cv-form');
  const get = id => document.getElementById(id);
  const states = {ready:'Ready to use', verification_required:'Verification required', creating:'Awaiting confirmation', needs_review:'Confirmation needed', failed:'Not saved — retry after correction'};
  let requestId = crypto.randomUUID(), recoveryConfirmation = null;
  async function api(url, options) {
    const res = await fetch(url, options); let data;
    try { data = await res.json(); } catch (_) { throw new Error('The server did not confirm this request. Refresh the library before trying again.'); }
    if (!res.ok || !data.ok) throw new Error(data.error || 'Could not complete this request.');
    return data;
  }
  async function load() {
    const list = get('cv-list'); list.textContent = 'Loading voices…';
    try {
      const {voices} = await api('/api/customer-voices'); list.replaceChildren();
      if (!voices.length) list.textContent = 'No customer voices yet. Add your first voice here.';
      voices.forEach(v => {
        const row = document.createElement('article'); row.className = 'cv-voice';
        const title = document.createElement('h3'); title.textContent = `${v.name} · ${v.client}`;
        const status = document.createElement('p'); status.className = 'cv-status'; status.textContent = states[v.status] || v.status;
        row.append(title, status);
        if (v.error) { const p = document.createElement('p'); p.textContent = v.error; row.append(p); }
        if (v.preview_url && /^https:\/\//i.test(v.preview_url)) { const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'none'; audio.src = v.preview_url; row.append(audio); }
        if (v.voice_id) {
          const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Refresh voice status';
          button.onclick = async () => { button.disabled = true; try { await api(`/api/customer-voices/${encodeURIComponent(v.id)}/refresh`, {method:'POST'}); await load(); } catch(e) { status.textContent = e.message; button.disabled = false; } };
          row.append(button);
        }
        if (v.status === 'needs_review' && !v.voice_id) {
          const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Retry upload after checking ElevenLabs';
          retry.onclick = () => {
            if (!window.confirm('Have you checked the connected ElevenLabs account and confirmed this voice was NOT created? Continuing allows a new cloning attempt.')) return;
            requestId = v.id; recoveryConfirmation = crypto.randomUUID(); form.reset(); get('cv-mode').value = 'upload'; get('cv-mode').onchange();
            get('cv-name').value = v.name || ''; get('cv-client').value = v.client || ''; get('cv-description').value = v.description || '';
            get('cv-submit').disabled = false; get('cv-message').textContent = 'Select the same recordings, confirm permission, then create the voice again.'; get('cv-files').focus();
          };
          row.append(retry);
        }
        if (v.status === 'needs_review' || v.status === 'failed') {
          const recover = document.createElement('button'); recover.type = 'button'; recover.textContent = 'Use existing ElevenLabs voice ID';
          recover.onclick = () => {
            requestId = crypto.randomUUID(); recoveryConfirmation = null; form.reset(); get('cv-mode').value = 'existing'; get('cv-mode').onchange();
            get('cv-name').value = v.name || ''; get('cv-client').value = v.client || ''; get('cv-id').value = v.voice_id || '';
            get('cv-submit').disabled = false; get('cv-message').textContent = 'Paste the voice ID from ElevenLabs to save the existing voice without creating another clone.';
            get('cv-id').focus();
          };
          row.append(recover);
        }
        list.append(row);
      });
    } catch(e) { list.textContent = e.message; }
  }
  get('cv-client').value = new URLSearchParams(location.search).get('client') || '';
  let captureRows = [];
  get('cv-mode').onchange = async () => {
    const mode = get('cv-mode').value, existing = mode === 'existing', capture = mode === 'capture';
    get('cv-upload').hidden = existing || capture; get('cv-existing').hidden = !existing; get('cv-capture').hidden = !capture;
    get('cv-files').disabled = existing || capture; get('cv-files').required = !existing && !capture;
    get('cv-id').disabled = !existing; get('cv-id').required = existing;
    get('cv-capture-id').disabled = !capture; get('cv-capture-id').required = capture;
    get('cv-submit').textContent = existing ? 'Save customer voice' : 'Create customer voice';
    if (capture) {
      try {
        const requestedCapture = new URLSearchParams(location.search).get('capture_id');
        const data = await api('/api/customer-voices/captures' + (requestedCapture ? '?capture_id=' + encodeURIComponent(requestedCapture) : '')); captureRows = data.captures || [];
        const select = get('cv-capture-id'); select.replaceChildren(new Option('Choose a submitted recording', ''));
        captureRows.forEach(r => select.add(new Option(`${r.client} — ${r.name || r.filename}`, r.id)));
        get('cv-capture-note').textContent = captureRows.length ? 'Listen to the submitted recording before creating its reusable voice.' : 'No submitted recordings yet. Create a client recording link in Commercial Builder, or upload recordings here.';
        const requested = new URLSearchParams(location.search).get('capture_id');
        if (requested) { select.value = requested; select.onchange(); }
      } catch(e) { get('cv-capture-note').textContent = e.message; }
    }
  };
  get('cv-capture-id').onchange = () => {
    const row = captureRows.find(r => String(r.id) === get('cv-capture-id').value);
    const audio = get('cv-capture-audio'); audio.pause(); audio.hidden = !row?.audio_url;
    if (row) { get('cv-client').value = row.client || ''; get('cv-name').value = row.name || ''; if (row.audio_url) audio.src = row.audio_url; }
  };
  if (new URLSearchParams(location.search).has('capture_id')) { get('cv-mode').value = 'capture'; get('cv-mode').onchange(); }
  form.onsubmit = async e => {
    e.preventDefault(); const button = get('cv-submit'), message = get('cv-message');
    const files = get('cv-files').disabled ? [] : [...get('cv-files').files];
    if (files.length > 5 || files.some(f => !f.size || f.size > 25*1024*1024) || files.reduce((n,f) => n+f.size,0) > 50*1024*1024) { message.textContent = 'Use 1–5 nonempty recordings, at most 25 MB each and 50 MB total.'; return; }
    button.disabled = true; message.textContent = 'Saving voice… This can take a few minutes.';
    const body = new FormData(form); body.set('authorized', 'true'); body.set('request_id', requestId);
    if (recoveryConfirmation) body.set('recovery_confirmation', recoveryConfirmation);
    recoveryConfirmation = null;
    try {
      const {voice} = await api('/api/customer-voices', {method:'POST', body});
      message.textContent = voice.status === 'ready' ? 'Saved. This voice is now available in all speech creators.' : 'Saved. Complete verification in ElevenLabs, then refresh its status.';
      get('cv-files').value = ''; get('cv-new').hidden = false;
    } catch(e) { message.textContent = e.message; button.disabled = false; get('cv-new').hidden = false; }
    await load();
  };
  get('cv-new').onclick = () => { requestId = crypto.randomUUID(); recoveryConfirmation = null; form.reset(); get('cv-mode').onchange(); get('cv-submit').disabled = false; get('cv-new').hidden = true; get('cv-message').textContent = ''; };
  get('cv-reload').onclick = load;
  load();
})();
