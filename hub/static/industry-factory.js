(() => {
  const $ = id => document.getElementById(id), packs = JSON.parse($('packs').textContent);
  let current = null, pages = [], busy = false;
  const setup = $('setup');
  function option(value, label) { return new Option(label, value); }
  function fill() {
    const p = packs.find(p => p.id === $('industry').value);
    $('service').replaceChildren(...Object.entries(p.services).map(([k,v]) => option(k,v)));
    $('goal').replaceChildren(...Object.entries(p.conversion_goals).map(([k,v]) => option(k,v)));
    $('goal').value = p.default_goal;
    $('triggers').replaceChildren(...p.trigger_refs.map(r => {
      const label = document.createElement('label'), input = document.createElement('input');
      input.type = 'checkbox'; input.value = r.id; input.checked = true; input.name = 'trigger_ids';
      label.append(input, ' ' + r.id.replaceAll('_', ' ')); return label;
    }));
    $('weather-note').textContent = p.weather_note;
  }
  $('industry').replaceChildren(...packs.map(p => option(p.id,p.name))); fill(); $('industry').onchange = fill;
  async function api(path, body) {
    const response = await fetch('/api/industry-factory/pages' + path, body === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Request failed'); return data;
  }
  async function run(fn) {
    if (busy) return; busy = true; controls();
    try { await fn(); } catch(e) { $('status').textContent = e.message; }
    finally { busy = false; controls(); }
  }
  function controls() {
    document.querySelectorAll('[data-action]').forEach(b=>b.disabled=busy || !current || current.status==='published');
    $('publish').disabled=busy || !current || current.status==='published';
    $('clone-service').disabled=$('clone-location').disabled=busy || !current;
    setup.querySelector('button').disabled=busy;
  }
  function selection() {
    const body = Object.fromEntries(new FormData(setup));
    body.trigger_ids = [...setup.querySelectorAll('[name=trigger_ids]:checked')].map(x => x.value); return body;
  }
  function show(p) {
    const switched = !current || current.id !== p.id;
    current = p;
    $('status').textContent = `Version ${p.version} · ${p.status} · ${p.config.market}`;
    $('states').textContent = `Page: ${p.states.page} · Report: ${p.states.report} · Creative: ${p.states.creative}`;
    $('details').textContent = JSON.stringify({config:p.config,pack_version:p.pack.version,artifacts:p.artifacts},null,2);
    document.querySelectorAll('[data-action]').forEach(b => b.disabled = p.status === 'published');
    $('publish').disabled = p.status === 'published';
    $('clone-service').disabled = $('clone-location').disabled = false;
    $('qa').querySelectorAll('input').forEach(x => { if(switched || p.status === 'published') x.checked = p.qa.includes(x.value); x.disabled = p.status === 'published'; });
    if (p.states.page === 'ready') {
      const url = '/sales/industry-factory/preview/' + p.id;
      if ($('preview').getAttribute('src') !== url) $('preview').src = url;
      $('preview-link').href = url;
    } else {
      $('preview').removeAttribute('src');
      $('preview-link').removeAttribute('href');
    }
    $('published').replaceChildren(); $('embed').value = '';
    if(p.status === 'published') {
      const a = document.createElement('a'); a.href='/industry/p/'+p.id; a.textContent='Open published page'; a.target='_blank'; a.rel='noopener'; $('published').append(a);
      $('embed').value=`<script src="${location.origin}/industry/widget/${p.id}/embed.js" defer></script>`;
    }
    controls();
  }
  async function refresh() {
    const data = await api(''); pages = data.pages;
    $('saved').replaceChildren(option('','Choose a page'),...pages.map(p => option(p.id,`${p.config.market} · ${p.pack.services[p.config.service]} · v${p.version} · ${p.status}`)));
    if(current) { $('saved').value=current.id; const p=pages.find(x=>x.id===current.id); if(p) show(p); }
  }
  async function create(parent) {
    if(!setup.reportValidity()) return;
    const body=selection(); if(parent) body.parent_id=parent;
    show(await api('',body)); await refresh();
  }
  setup.onsubmit=e=>{e.preventDefault();run(()=>create());};
  $('saved').onchange=()=>{const p=pages.find(p=>p.id===$('saved').value);if(p){$('industry').value=p.config.industry_id;fill();for(const key of ['service','market','radius','conversion_goal']) setup.elements[key].value=p.config[key];setup.querySelectorAll('[name=trigger_ids]').forEach(x=>x.checked=p.config.trigger_ids.includes(x.value));show(p);}};
  document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>run(async()=>{show(await api('/'+current.id+'/'+b.dataset.action,{}));await refresh();}));
  $('publish').onclick=()=>run(async()=>{show(await api('/'+current.id+'/publish',{qa:[...$('qa').querySelectorAll('input:checked')].map(x=>x.value)}));await refresh();});
  ['clone-service','clone-location'].forEach(id=>$(id).onclick=()=>run(()=>create(current.id)));
  $('desktop').onclick=()=>{$('preview').style.maxWidth='100%';};
  $('mobile').onclick=()=>{$('preview').style.maxWidth='375px';};
  run(refresh);
  setInterval(()=>{if(current && ['queued','running'].includes(current.states.creative)) run(refresh);},5000);
})();
