/* Shared customer voice choice; the host tool persists the actual casting decision. */
window.CustomerVoicePicker = {
  mount(container, {client = '', onSelect} = {}) {
    if (!container) return;
    if (container.dataset.customerVoiceClient === client) return;
    container.dataset.customerVoiceClient = client;
    container.replaceChildren();
    const label = document.createElement('label'); label.textContent = 'Customer voice ';
    const select = document.createElement('select'); select.setAttribute('aria-label', 'Customer voice');
    const use = document.createElement('button'); use.type = 'button'; use.className = 'btn sec cb-btn'; use.textContent = 'Use customer voice'; use.disabled = true;
    const refresh = document.createElement('button'); refresh.type = 'button'; refresh.className = 'btn sec cb-btn'; refresh.textContent = 'Refresh voices';
    const link = document.createElement('a'); link.textContent = 'Add / manage customer voices'; link.href = '/tools/customer-voices/?client=' + encodeURIComponent(client); link.target = '_blank'; link.rel = 'noopener';
    const note = document.createElement('p'); note.setAttribute('role','status');
    const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'none'; audio.hidden = true;
    label.append(select); container.append(label, document.createTextNode(' '), use, document.createTextNode(' '), refresh, document.createTextNode(' '), link, audio, note);
    let voices = [];
    async function load() {
      refresh.disabled = true; use.disabled = true; select.disabled = true; note.textContent = 'Loading customer voices…';
      audio.pause(); audio.hidden = true;
      try {
        const response = await fetch('/api/customer-voices?ready=1'); const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || 'Could not load customer voices.');
        voices = data.voices || [];
        voices.sort((a,b) => Number(b.client?.toLowerCase() === client.toLowerCase()) - Number(a.client?.toLowerCase() === client.toLowerCase()) || a.name.localeCompare(b.name));
        select.replaceChildren(new Option('Choose a saved customer voice', ''));
        voices.forEach(v => select.add(new Option(`${v.client} — ${v.name}`, v.voice_id)));
        select.disabled = !voices.length;
        note.textContent = voices.length ? 'Choose a voice, then use it in this project.' : 'No ready customer voices yet. Add one, or complete its verification in Customer Voices.';
      } catch(e) { select.replaceChildren(new Option('Voices unavailable', '')); note.textContent = e.message || 'Could not load voices. Refresh to retry.'; }
      finally { refresh.disabled = false; }
    }
    select.onchange = () => { const v = voices.find(v => v.voice_id === select.value); use.disabled = !v; audio.pause(); audio.hidden = !v?.preview_url || !/^https:\/\//i.test(v.preview_url); if (!audio.hidden) audio.src = v.preview_url; };
    use.onclick = async () => { const v = voices.find(v => v.voice_id === select.value); if (!v) return; use.disabled = true; try { const message = await onSelect?.({...v, custom:true, score:0, match_reasons:['Saved customer voice']}); note.textContent = message || `Selected ${v.name}.`; } catch(e) { note.textContent = e.message; } finally { use.disabled = false; } };
    refresh.onclick = load;
    load();
  }
};
