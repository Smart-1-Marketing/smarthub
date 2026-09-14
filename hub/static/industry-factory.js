(() => {
  'use strict';
  const $ = id => document.getElementById(id), setup = $('setup'), packs = JSON.parse($('packs').textContent);
  let current = null, pages = [], audiences = [], review = null, busy = false, dirty = false, step = 'setup';
  const copyKeys = ['headline','subhead','pre_event','active_event','post_event'];
  const option = (value, label) => new Option(label, value);
  function node(tag, text, cls) { const n = document.createElement(tag); n.textContent = text; if(cls) n.className = cls; return n; }
  async function requestJSON(url, body) {
    const response = await fetch(url, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    let data; try { data = await response.json(); } catch (_) { throw new Error('The server did not return a usable response. Reload to check saved progress.'); }
    if(!response.ok) throw new Error(data.error || 'Request failed'); return data;
  }
  const api = (path, body) => requestJSON('/api/industry-factory/pages' + path, body);
  async function run(fn) {
    if(busy) return; busy = true; controls();
    try { await fn(); } catch(e) { $('status').textContent = e.message; }
    finally { busy = false; controls(); }
  }
  function changeStep(value) {
    step = value;
    document.querySelectorAll('[data-panel]').forEach(n => n.hidden = n.dataset.panel !== value);
    document.querySelectorAll('[data-step]').forEach(n => { if(n.dataset.step === value) n.setAttribute('aria-current','step'); else n.removeAttribute('aria-current'); });
    controls();
  }
  function copyFields(pack) {
    $('messaging').replaceChildren(...copyKeys.map(key => {
      const label = node('label', key.replaceAll('_',' ')), input = document.createElement('textarea');
      input.name = 'copy_' + key; input.maxLength = 1000; input.required = true; input.value = pack.messaging[key]; input.rows = key === 'headline' ? 2 : 3; label.append(input); return label;
    }));
  }
  function fill() {
    const p = packs.find(p => p.id === $('industry').value);
    $('service').replaceChildren(...Object.entries(p.services).map(([k,v])=>option(k,v)));
    $('goal').replaceChildren(...Object.entries(p.conversion_goals).map(([k,v])=>option(k,v))); $('goal').value = p.default_goal;
    $('triggers').replaceChildren(...p.trigger_refs.map(r=>{const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.name='trigger_ids';input.value=r.id;input.checked=true;label.append(input,' '+r.id.replaceAll('_',' ').replaceAll('-',' '));return label;}));
    $('weather-note').textContent = p.weather_note; copyFields(p);
  }
  $('industry').replaceChildren(...packs.map(p=>option(p.id,p.name))); fill();
  $('industry').onchange=()=>{fill();dirty=true;controls();};
  function selection() {
    const data=Object.fromEntries(new FormData(setup)); data.trigger_ids=[...setup.querySelectorAll('[name=trigger_ids]:checked')].map(x=>x.value);
    data.messaging={}; for(const key of copyKeys){data.messaging[key]=data['copy_'+key];delete data['copy_'+key];} return data;
  }
  function loadForm(p) {
    $('industry').value=p.config.industry_id;fill();
    for(const key of ['service','market','radius','conversion_goal']) setup.elements[key].value=p.config[key];
    $('audience').value=p.config.audience_id || '';
    setup.querySelectorAll('[name=trigger_ids]').forEach(x=>x.checked=p.config.trigger_ids.includes(x.value)); copyFields(p.pack); dirty=false;
  }
  function approvals() {return [...$('qa').querySelectorAll('input:checked')].map(x=>x.value);}
  function canPublish() {return current && current.status==='draft' && !dirty && review?.qa.ready && approvals().length===$('qa').querySelectorAll('input').length;}
  function nextAction() {
    if(!current) return ['Create draft','Choose your audience, service and market.','create'];
    if(dirty) return [current.status==='draft'?'Save draft changes':'Clone with these changes','Changed setup must be saved before generation or publication.','save'];
    if(current.status==='published') return ['View publication','This version is live. Create a revision to make changes.','publish'];
    if(current.status==='archived') return ['View version history','This version is archived. Restore it or create a revision.','publish'];
    if(current.states.page!=='ready') return ['Generate page','Save a complete page before review.','generate-page'];
    if(current.states.report!=='ready') return ['Generate planning report','The planning report is required; creative briefs are optional.','generate-report'];
    if(!review?.qa.ready) return ['Review technical checks','Resolve failed automatic checks. Warnings explain external setup still needed.','review'];
    if(!canPublish()) return ['Review and approve','Review messaging, branding and configuration warnings before approval.','review'];
    return ['Review publication','All required checks and approvals are complete.','publish'];
  }
  function controls() {
    const draft=current?.status==='draft', ready=!!current;
    document.querySelectorAll('#history button, #metrics button').forEach(b=>b.disabled=busy);
    document.querySelectorAll('[data-action]').forEach(b=>b.disabled=busy || !draft || dirty);
    $('publish').disabled=busy || !canPublish();
    for(const id of ['clone-service','clone-location','revise','check','refresh-metrics']) $(id).disabled=busy || !ready;
    $('unpublish').disabled=busy || !review?.active_id;
    $('save').disabled=busy || (ready && !draft);$('save').textContent=ready?'Save draft changes':'Create draft';
    $('saved').disabled=$('new').disabled=busy;
    const next=nextAction();$('next').textContent=next[0];$('next-help').textContent=next[1];$('next').disabled=busy;
    document.querySelectorAll('[data-step]').forEach(b=>b.disabled=busy || (!current && b.dataset.step!=='setup'));
    $('qa').querySelectorAll('input').forEach(x=>x.disabled=busy || !draft || dirty);
  }
  function show(p, form=false) {
    const switched=current?.id!==p.id; current=p;
    if(form || switched) loadForm(p);
    $('status').textContent=`Version ${p.version} · ${p.status} · ${p.config.market}${dirty?' · Unsaved changes':''}`;
    $('states').replaceChildren(...Object.entries(p.states).map(([key,value])=>node('div',`${{page:'Landing page',report:'Planning report',creative:'Creative briefs'}[key]}: ${value==='not_started'?'Not generated':value==='done'?'Ready':value.replaceAll('_',' ')}`,'output-card')));
    $('details').textContent=JSON.stringify({config:p.config,pack_version:p.pack.version,artifacts:p.artifacts},null,2);
    $('generated-content').replaceChildren();
    if(p.artifacts.report) $('generated-content').append(node('h3','Planning report ready'),node('p',`${p.config.market}: ${p.artifacts.report.triggers.length} weather conditions to monitor. Review the page below; the printable report is available after publication.`));
    for(const concept of p.artifacts.creative?.concepts || []) $('generated-content').append(node('pre',typeof concept==='string'?concept:JSON.stringify(concept,null,2)));
    if(p.artifacts.creative_error) $('generated-content').append(node('p',p.artifacts.creative_error));
    $('qa').querySelectorAll('input').forEach(x=>{if(switched || p.status!=='draft')x.checked=p.qa.includes(x.value);});
    $('preview-context').textContent=`${p.pack.services[p.config.service]} · ${p.config.market} · ${p.config.radius} miles · ${p.pack.conversion_goals[p.config.conversion_goal]}`;
    $('requirements').textContent=`Required: ${p.pack.widget.required.join(', ')}. Goal: ${p.pack.conversion_goals[p.config.conversion_goal]}. Preview submissions are disabled.`;
    for(const [id,suffix] of [['preview',''],['widget-preview','?widget=1']]) {
      const url='/sales/industry-factory/preview/'+p.id+suffix;
      if(p.states.page==='ready'){if($(id).getAttribute('src')!==url)$(id).src=url;}else $(id).removeAttribute('src');
    }
    if(p.states.page==='ready')$('preview-link').href='/sales/industry-factory/preview/'+p.id;else $('preview-link').removeAttribute('href');
    controls();
  }
  function renderReview() {
    $('technical-qa').replaceChildren(...review.qa.checks.map(c=>node('li',`${c.state.toUpperCase()} — ${c.label}. ${c.detail}`,c.state)));
    $('comparison').replaceChildren(...(review.changes.length?review.changes.map(c=>node('p',`${c.section} / ${c.field}: ${JSON.stringify(c.before)} → ${JSON.stringify(c.after)}`)):[node('p','No differences from the comparison version, or this is the first version.')]));
    $('history').replaceChildren(...review.versions.map(p=>{
      const row=node('div',`Version ${p.version} · ${p.status} · ${p.config.market} `,'history-row');
      const select=node('button','Review');select.onclick=()=>run(async()=>{if(dirty && !await confirmChange('Discard unsaved changes?','Saved versions will not be affected.'))return;show(p,true);await inspect();changeStep('review');});row.append(select);
      if(p.status==='archived'){const restore=node('button','Restore this version');restore.onclick=()=>run(async()=>{if(await confirmChange('Restore version '+p.version+'?', 'The stable campaign address will serve this version. The current live version will be archived.')){show(await api('/'+p.id+'/restore',{}),true);await refresh();changeStep('publish');}});row.append(restore);} return row;
    }));
    $('published').replaceChildren();$('embed').value='';
    if(review.active_id){const a=node('a','Open live campaign');a.href='/industry/p/'+current.publication_id;a.target='_blank';a.rel='noopener';$('published').append(a);$('embed').value=`<script src="${location.origin}/industry/widget/${current.publication_id}/embed.js" defer></script>`;}
    $('publish-help').textContent=review.qa.ready?'Technical QA passed. Review any configuration warnings and complete human approval.':'Publication is blocked until technical QA passes.';controls();
  }
  async function inspect(){review=null;controls();review=await api('/'+current.id+'/review');renderReview();}
  async function refresh(){const data=await api('');pages=data.pages;$('saved').replaceChildren(option('','Choose a page'),...pages.map(p=>option(p.id,`${p.config.market} · ${p.pack.services[p.config.service]} · v${p.version} · ${p.status}`)));if(current){const p=pages.find(p=>p.id===current.id);if(p)show(p);$('saved').value=current.id;await inspect();}}
  async function save(parent) {
    if(!setup.reportValidity()){changeStep('setup');return;}
    const body=selection();
    if(!current || parent){if(parent)body.parent_id=parent;const p=await api('',body);current=p;show(await api('/'+p.id+'/save',body),true);}else show(await api('/'+current.id+'/save',body),true);
    $('qa').querySelectorAll('input').forEach(x=>x.checked=false);await refresh();changeStep('generate');
  }
  function confirmChange(title,message){$('confirm-title').textContent=title;$('confirm-message').textContent=message;return new Promise(resolve=>{const dialog=$('confirm');const done=value=>{dialog.close();resolve(value);};$('confirm-yes').onclick=()=>done(true);$('confirm-no').onclick=()=>done(false);dialog.oncancel=e=>{e.preventDefault();done(false);};dialog.showModal();});}
  async function metrics(){const data=await api('/'+current.id+'/metrics');$('metrics').replaceChildren(node('p',`${data.views} views · ${data.leads} leads · ${data.qualified} staff-qualified opportunities`),node('p',data.note));
    const table=document.createElement('table');const heading=document.createElement('tr');for(const text of ['Version','Source / medium','Campaign','Views','Leads','Qualified'])heading.append(node('th',text));table.append(heading);
    for(const g of data.groups){const tr=document.createElement('tr'),p=pages.find(p=>p.id===g.page_id);for(const value of [p?'v'+p.version:g.page_id,g.source+' / '+g.medium,g.campaign,g.views,g.leads,g.qualified])tr.append(node('td',String(value)));table.append(tr);}const wrap=node('div','','table-scroll');wrap.append(table);$('metrics').append(wrap);
    for(const lead of data.recent_leads){const row=node('div',`${lead.company} · ${lead.created} `,'history-row'),button=node('button',lead.qualified?'Remove qualification':'Mark qualified');button.onclick=()=>run(async()=>{await api('/'+current.id+'/qualify',{lead_id:lead.id,qualified:!lead.qualified});await metrics();});row.append(button);$('metrics').append(row);}
  }
  setup.oninput=()=>{dirty=!!current;controls();};setup.onsubmit=e=>{e.preventDefault();run(()=>save());};
  $('saved').onchange=()=>run(async()=>{const p=pages.find(p=>p.id===$('saved').value);if(!p)return;if(dirty && !await confirmChange('Discard unsaved changes?','Your saved campaign will stay unchanged.')){$('saved').value=current.id;return;}show(p,true);await inspect();changeStep(p.status==='draft'?'setup':'publish');if(p.status!=='draft')await metrics();});
  $('new').onclick=()=>run(async()=>{if(dirty && !await confirmChange('Discard unsaved changes?','Start a separate campaign with fresh review and output states.'))return;current=null;review=null;dirty=false;setup.reset();fill();$('saved').value='';$('status').textContent='Create a draft to begin.';changeStep('setup');});
  document.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>run(async()=>{changeStep(b.dataset.step);if(step==='review')await inspect();if(step==='publish')await metrics();}));
  document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>run(async()=>{show(await api('/'+current.id+'/'+b.dataset.action,{}));await refresh();}));
  $('next').onclick=()=>run(async()=>{const action=nextAction()[2];if(action==='create')await save();else if(action==='save')await save(current.status==='draft'?null:current.id);else if(action.startsWith('generate-')){show(await api('/'+current.id+'/'+action,{}));await refresh();changeStep('generate');}else{changeStep(action);if(action==='review')await inspect();if(action==='publish')await metrics();}});
  $('check').onclick=()=>run(inspect);$('refresh-metrics').onclick=()=>run(metrics);$('qa').onchange=controls;
  $('publish').onclick=()=>run(async()=>{if(!canPublish())return;show(await api('/'+current.id+'/publish',{qa:approvals()}));await refresh();await metrics();});
  $('revise').onclick=()=>run(async()=>{if(dirty && !await confirmChange('Use the saved version?','Unsaved setup changes will not be copied into this revision.'))return;show(await api('/'+current.id+'/revise',{}),true);await refresh();changeStep('setup');});
  $('unpublish').onclick=()=>run(async()=>{if(await confirmChange('Unpublish this campaign?','Its page, report and embedded widget will become unavailable. You can restore an approved version later.')){show(await api('/'+current.id+'/unpublish',{}));await refresh();}});
  ['clone-service','clone-location'].forEach(id=>$(id).onclick=()=>run(()=>save(current.id)));
  for(const id of ['desktop','mobile'])$(id).onclick=()=>{for(const frame of ['preview','widget-preview'])$(frame).style.maxWidth=id==='mobile'?'375px':'100%';$('desktop').setAttribute('aria-pressed',String(id==='desktop'));$('mobile').setAttribute('aria-pressed',String(id==='mobile'));};
  $('audience').onchange=()=>{const a=audiences.find(a=>a.id===$('audience').value);if(a && !a.supported){$('audience').value='';$('audience-note').textContent='This audience needs an industry pack that is not available yet.';return;}if(a){$('industry').value=a.industry;fill();setup.elements.market.value=a.market;$('audience-note').textContent='Audience linked. Headquarters geography is a starting point; confirm the service area and radius.';}dirty=!!current;controls();};
  run(async()=>{await refresh();try{const data=await requestJSON('/api/industry-factory/audiences');audiences=data.audiences;$('audience').replaceChildren(option('','No linked audience'),...audiences.map(a=>{const o=option(a.id,a.name+(a.supported?'':' — industry pack unavailable'));o.disabled=!a.supported;return o;}));$('audience-note').textContent='Choose a saved audience to prefill industry and geography. No contacts are purchased or contacted.';const id=new URLSearchParams(location.search).get('audience');if(id){$('audience').value=id;$('audience').dispatchEvent(new Event('change'));}}catch(e){$('audience-note').textContent='Saved audiences unavailable: '+e.message;}});
  setInterval(()=>{if(current && !dirty && ['queued','running'].includes(current.states.creative))run(refresh);},5000);
})();
