/* Client 360 -- the skill-gated cards: ecommerce and the email creator.
   Cut out of hub/templates/client360.html: the record's own inline script
   grew past 5,900 lines with only render() needing Jinja, so everything
   else lives here as plain files node can lint and the tests can read.
   Load order is hub/client360_assets.MODULES; the template's inline script
   (run, pick, render and the Jinja constants) loads after every module and
   calls into them, never the other way round at load time. Top-level
   let/const here are shared with the other classic scripts on the page. */

/* ==================== 360 Skills cards (modules/skills360) ====================
   Each loader honors c360Generation and keeps its empties apart: the skill
   is off (the card is not drawn at all), the source answered nothing, the
   source could not be read. renderUtm's rule. */
const SK_API='/api/client/skills';
function skPost(path,body){
  return fetch(SK_API+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(Object.assign({client:window.CURRENT_CLIENT||''},body||{}))}).then(r=>r.json());
}
const skMoney=n=>'$'+Number(n||0).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0});
const skMoney2=n=>'$'+Number(n||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
function skDelta(cur,prev,label){
  if(!prev) return '<div class="muted" style="font-size:12px">vs '+label+': —</div>';
  const p=Math.round(100*(cur-prev)/prev);
  return '<div style="font-size:12px;color:'+(p>=0?'#0a6b3c':'#9c2b25')+'">'+(p>=0?'&#9650;':'&#9660;')+' '+Math.abs(p)+'% vs '+label+'</div>';
}
/* ---- ecommerce (lifted and driven in node by test_skills360.py) ---- */
function renderEcwid(d){
  d=d||{};
  if(!d.ok){
    const why = d.state==='off' ? 'The Ecommerce skill is not switched on for this client.'
              : 'The store could not be read'+(d.error?' ('+esc(d.error)+')':'')+'.';
    return '<div class="empty">'+why+' <a href="/tools/360-skills/?client='+encodeURIComponent(window.CURRENT_CLIENT||'')+'">Open 360 Skills &rarr;</a></div>';
  }
  const P=d.periods||{}, w=P.week||{}, lw=P.last_week||{}, m=P.month||{}, lm=P.last_month||{}, y=P.year||{};
  const tile=(k,v,sub,extra)=>'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px 14px;min-width:150px;flex:1">'
    +'<div class="muted" style="font-size:11.5px;font-weight:600;text-transform:uppercase;letter-spacing:.03em">'+k+'</div>'
    +'<div style="font-size:24px;font-weight:700;color:var(--navy);margin:2px 0">'+v+'</div>'
    +'<div class="muted" style="font-size:12px">'+sub+'</div>'+(extra||'')+'</div>';
  let h='<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">';
  h+=tile('This week',skMoney(w.revenue),(w.orders||0)+' orders · avg '+skMoney2(w.avg_order),skDelta(w.revenue,lw.revenue,'last week'));
  h+=tile('This month',skMoney(m.revenue),(m.orders||0)+' orders · avg '+skMoney2(m.avg_order),skDelta(m.revenue,lm.revenue,'last month'));
  h+=tile('This year',skMoney(y.revenue),(y.orders||0)+' orders','');
  if(d.abandoned){const a=d.abandoned; h+=tile('Abandoned carts',String((a.month||{}).carts||0),skMoney((a.month||{}).value)+' left in carts this month','');}
  h+='</div>';
  const W=d.windows||{}; if(Object.keys(W).length){
    const pick=String(window.__ecDays||'30'); const w=W[pick]||W[Object.keys(W)[0]]||{};
    h+='<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:2px 0 8px"><span class="muted" style="font-size:12px">Rolling:</span>'
      +Object.keys(W).map(k=>'<button type="button" class="gbtn" data-ec-days="'+k+'"'+(k===pick?' style="background:var(--navy);color:#fff"':'')+'>Last '+k+' days</button>').join('')
      +'<span style="font-size:13px;margin-left:6px"><b>'+skMoney(w.revenue)+'</b> · '+(w.orders||0)+' orders · avg '+skMoney2(w.avg_order)+'</span>'
      +(w.change_pct===null||w.change_pct===undefined?'<span class="muted" style="font-size:12px">no prior period</span>':'<span style="font-size:12px;color:'+(w.change_pct>=0?'#0a6b3c':'#9c2b25')+'">'+(w.change_pct>=0?'&#9650;':'&#9660;')+' '+Math.abs(w.change_pct)+'% vs the '+w.days+' days before</span>')
      +'</div>';
  }
  const ms=d.monthly||[]; const max=Math.max(1,...ms.map(x=>x.revenue||0));
  h+='<div style="display:flex;align-items:flex-end;gap:5px;height:110px;margin:4px 0 14px">'+ms.map(x=>
    '<div title="'+esc(x.month)+': '+skMoney2(x.revenue)+' · '+x.orders+' orders" style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;min-width:0">'
    +'<span style="font-size:9.5px;color:#334155;white-space:nowrap">'+(x.revenue?skMoney(x.revenue):'')+'</span>'
    +'<i style="display:block;width:100%;background:var(--navy);border-radius:3px 3px 0 0;height:'+Math.max(2,Math.round(100*(x.revenue||0)/max))+'%"></i>'
    +'<b style="font-size:10px;color:#64748b;font-weight:500;white-space:nowrap;margin-top:3px">'+esc(x.month)+'</b></div>').join('')+'</div>';
  const tp=d.top_products||{}; const st=m.status||{}; const dm=m.discounts||{};
  const list=rows=>rows&&rows.length?'<table style="width:100%;border-collapse:collapse;font-size:12.5px">'+rows.slice(0,6).map(r=>
    '<tr><td style="padding:4px 6px;border-bottom:1px solid #eef1f4">'+esc(r.name)+'</td><td style="padding:4px 6px;text-align:right;border-bottom:1px solid #eef1f4">'+r.quantity+'</td><td style="padding:4px 6px;text-align:right;border-bottom:1px solid #eef1f4">'+skMoney2(r.revenue)+'</td></tr>').join('')+'</table>':'<div class="muted" style="font-size:12.5px">Nothing sold in this period yet.</div>';
  h+='<div class="grid-2" style="gap:14px"><div><b style="font-size:13px">Top products this month</b>'+list(tp.month)+'</div><div><b style="font-size:13px">Top products this year</b>'+list(tp.year)+'</div></div>';
  h+='<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">'
    +'<span class="pill neutral">Pending '+(st.pending||0)+'</span><span class="pill neutral">Processing '+(st.processing||0)+'</span><span class="pill neutral">Shipped '+(st.shipped||0)+'</span><span class="pill neutral">Delivered '+(st.delivered||0)+'</span>'
    +'<span class="pill info">'+(dm.orders_with||0)+' orders used a discount ('+(dm.rate||0)+'%) · '+skMoney2(dm.amount)+' given</span></div>';
  const rc=d.recent||[];
  if(rc.length) h+='<div style="margin-top:12px"><b style="font-size:13px">Latest orders</b><table style="width:100%;border-collapse:collapse;font-size:12.5px">'+rc.map(o=>
    '<tr><td style="padding:4px 6px;border-bottom:1px solid #eef1f4">#'+esc(o.number)+'</td><td style="padding:4px 6px;border-bottom:1px solid #eef1f4">'+esc(o.date)+'</td><td style="padding:4px 6px;text-align:right;border-bottom:1px solid #eef1f4">'+o.items+' items</td><td style="padding:4px 6px;text-align:right;border-bottom:1px solid #eef1f4">'+skMoney2(o.total)+'</td><td style="padding:4px 6px;border-bottom:1px solid #eef1f4">'+esc(o.status)+'</td></tr>').join('')+'</table></div>';
  const s=d.store||{};
  h+='<div class="muted" style="font-size:11.5px;margin-top:10px">'+esc(s.store_name||'')+(s.store_url?' · <a href="'+esc(s.store_url)+'" target="_blank" rel="noopener">'+esc(s.store_url)+'</a>':'')+' · '+(d.order_count||0)+' orders on record'+(d.cached?' · cached':'')+(d.generated_at?' · as of '+esc(new Date(d.generated_at).toLocaleString()):'')+'</div>';
  return h;
}
/* ---- end ecommerce ---- */
function wireEcwidDays(el){
  el.querySelectorAll('[data-ec-days]').forEach(b=>{ b.onclick=()=>{ window.__ecDays=b.dataset.ecDays; el.innerHTML=renderEcwid(window.__ecData); wireEcwidDays(el); }; });
}
function loadEcwid(name,fresh){
  const el=$('c-ecwid'); if(!el) return;
  const gen=c360Generation;
  if(fresh) el.innerHTML='<div class="empty">Re-reading the store… <span class="spin"></span></div>';
  fetch(SK_API+'/ecwid/dashboard?name='+encodeURIComponent(name)+(fresh?'&fresh=1':''),{credentials:'same-origin'})
    .then(r=>r.json()).then(d=>{
      if(gen!==c360Generation) return;
      window.__ecData=d;
      el.innerHTML=renderEcwid(d);
      wireEcwidDays(el);
      const acts=$('c-ecwid-acts');
      if(acts&&!acts.querySelector('[data-ecwid-refresh]')){
        const b=document.createElement('button'); b.type='button'; b.className='gbtn'; b.textContent='Refresh'; b.setAttribute('data-ecwid-refresh','1');
        b.onclick=()=>loadEcwid(name,true); acts.insertBefore(b,acts.firstChild);
      }
    }).catch(()=>{ if(gen!==c360Generation) return; el.innerHTML=renderEcwid({ok:false,state:'unreadable'}); });
}

/* ---- email creator ---- */
function skPill(state,label){
  const cls=state==='ok'?'ok':state==='bad'?'err':state==='warn'?'warn':'neutral';
  return '<span class="pill '+cls+'" style="font-size:11.5px">'+esc(label)+'</span>';
}
function renderEmailReadiness(r){
  r=r||{};
  if(!r.ok){
    const why = r.state==='off' ? 'The Email Creator skill is not switched on for this client.'
              : 'The Suite account could not be checked'+(r.error?' ('+esc(r.error)+')':'')+'.';
    return '<div class="empty">'+why+' <a href="/tools/360-skills/?client='+encodeURIComponent(window.CURRENT_CLIENT||'')+'">Open 360 Skills &rarr;</a></div>';
  }
  const a=r.account||{}, f=r.from||{}, b=r.builder||{}, d=r.domain||{}, sc=r.scopes||{};
  let h='<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px">';
  h+=skPill(a.state==='connected'?'ok':'bad','Suite: '+(a.name||a.matched_name||a.location_id||'not linked'));
  h+=skPill(f.state==='set'?'ok':'bad','From: '+(f.email||'none'));
  h+=skPill(b.state==='ok'?'ok':'bad','Email builder: '+(b.state==='ok'?'ok':'blocked'));
  h+=skPill(d.state==='verified'?'ok':d.state==='missing'?'warn':'neutral','Sending domain: '+(d.state==='verified'?'verified':d.state==='missing'?'not set up':d.state==='not_measured'?'not measured':'no address'));
  if(sc.missing&&sc.missing.length) h+=skPill('warn','App needs re-consent: '+sc.missing.join(', '));
  h+='</div><div class="muted" style="font-size:12.5px;line-height:1.5">'+esc(r.detail||'')
    +(d.state==='missing'?' <a href="'+esc(r.settings_url||'#')+'" target="_blank" rel="noopener">Email Services in Suite &rarr;</a> · <a href="'+esc(r.tool_url||'/tools/360-skills/')+'">DNS records in 360 Skills &rarr;</a>':'')
    +'</div>';
  const sends=r.sends||[];
  if(sends.length){
    const KIND={template:'Saved to Suite templates',test:'Test',batch:'Sent to contacts'};
    h+='<div style="margin-top:8px"><span class="muted" style="font-size:12px">Recent sends</span><table style="width:100%;border-collapse:collapse;font-size:12.5px">'
      +sends.slice(0,6).map(x=>'<tr><td style="padding:3px 8px 3px 0;white-space:nowrap" class="muted">'+esc((x.at||'').slice(0,10))+'</td><td style="padding:3px 8px">'+esc(KIND[x.kind]||x.kind)+(x.kind==='batch'?' ('+(x.count||0)+')':'')+(x.to?' → '+esc(x.to):'')+'</td><td style="padding:3px 0"><b>'+esc(x.subject||'')+'</b>'+(x.detail?' <span class="muted">'+esc(x.detail)+'</span>':'')+'</td><td style="padding:3px 0 3px 8px;text-align:right" class="muted">'+esc(x.by||'')+'</td></tr>').join('')
      +'</table></div>';
  }
  return h;
}
let skEmailReady=false;
function loadEmailCreator(name){
  const el=$('c-email'); if(!el) return;
  const gen=c360Generation;
  fetch(SK_API+'/email/readiness?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(r=>r.json()).then(r=>{
      if(gen!==c360Generation) return;
      skEmailReady=!!(r&&r.ok&&r.ready);
      el.innerHTML='<div id="c-email-ready">'+renderEmailReadiness(r)+'</div>'+(r&&r.ok?renderEmailComposer():'');
      if(r&&r.ok) wireEmailComposer(name);
    }).catch(()=>{ if(gen!==c360Generation) return; el.innerHTML=renderEmailReadiness({ok:false,state:'unreadable'}); });
}
function renderEmailComposer(){
  const inp=(id,label,ph,extra)=>'<div class="field"><label style="font-size:11.5px;font-weight:600;color:#5a6b7c;display:block;margin:8px 0 3px" for="'+id+'">'+label+'</label><input type="text" id="'+id+'" placeholder="'+esc(ph||'')+'" style="width:100%;box-sizing:border-box;padding:7px 9px;border:1px solid #d6dde5;border-radius:6px;font-size:13px"'+(extra||'')+'></div>';
  let h='<div style="margin-top:12px;border-top:1px solid #eef1f4;padding-top:10px">';
  h+='<div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap"><div style="flex:1;min-width:240px">'+inp('em-brief','Draft with AI (optional) — what is this email about?','e.g. Fall wine release this Saturday, 20% off cases for members')+'</div>'
    +'<button type="button" class="btn-ghost" id="em-draft" style="margin-bottom:1px">Draft with AI</button></div>';
  h+='<div class="grid-2" style="gap:0 14px">'+inp('em-subject','Subject','')+inp('em-preview','Preview text','What the inbox shows under the subject')+'</div>';
  h+=inp('em-headline','Headline','');
  h+='<div class="field"><label style="font-size:11.5px;font-weight:600;color:#5a6b7c;display:block;margin:8px 0 3px" for="em-body">Body</label><textarea id="em-body" rows="7" placeholder="Short paragraphs separated by a blank line. '+'{'+'{contact.first_name}'+'}'+' works." style="width:100%;box-sizing:border-box;padding:7px 9px;border:1px solid #d6dde5;border-radius:6px;font-size:13px;font-family:inherit"></textarea></div>';
  h+='<div class="grid-2" style="gap:0 14px">'+inp('em-cta','Button text','Reserve your spot')+inp('em-url','Button link','https://…')+'</div>';
  h+='<div style="display:flex;gap:8px;align-items:flex-end"><div style="flex:1">'+inp('em-hero','Hero image URL (optional)','https://…')+'</div><button type="button" class="btn-ghost" id="em-pick" style="margin-bottom:1px">Pick from client images</button></div>'
    +'<div id="em-pick-box" hidden style="display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:6px;margin-top:8px"></div>';
  h+='<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px">'
    +'<button type="button" class="btn-ghost" id="em-preview-btn">Preview</button>'
    +'<button type="button" class="btn-ghost" id="em-template-btn">Save to Suite templates</button>'
    +'<input type="text" id="em-test-to" placeholder="you@smart1marketing.com" style="padding:7px 9px;border:1px solid #d6dde5;border-radius:6px;font-size:13px;min-width:220px">'
    +'<button type="button" class="btn-ghost" id="em-test-btn">Send me a test</button>'
    +'<button type="button" class="btn-primary" id="em-send-open">Send to contacts…</button></div>';
  h+='<div id="em-msg" class="muted" style="font-size:12.5px;margin-top:8px;line-height:1.5"></div>';
  h+='<div id="em-preview-box" hidden style="margin-top:10px;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden"><iframe id="em-preview-frame" title="Email preview" style="width:100%;height:520px;border:0;background:#fff"></iframe></div>';
  h+='<div id="em-send-box" hidden style="margin-top:12px;border:1px solid #e2e8f0;border-radius:8px;padding:12px;background:#f8fafc">'
    +'<b style="font-size:13px">Send to Suite contacts</b><div class="muted" style="font-size:12px;margin:2px 0 8px">Only contacts on the client’s Smart 1 Suite sub-account. Search by name or email, or by tag, tick who gets it, then confirm. Sends go one at a time so a bad address costs one line.</div>'
    +'<div style="display:flex;gap:8px;flex-wrap:wrap"><input type="text" id="em-q" placeholder="Name or email" style="padding:7px 9px;border:1px solid #d6dde5;border-radius:6px;font-size:13px;flex:1;min-width:160px">'
    +'<input type="text" id="em-tag" placeholder="Tag (optional)" style="padding:7px 9px;border:1px solid #d6dde5;border-radius:6px;font-size:13px;min-width:140px">'
    +'<button type="button" class="btn-ghost" id="em-search">Find contacts</button></div>'
    +'<div id="em-contacts" style="margin-top:8px;max-height:220px;overflow:auto"></div>'
    +'<div style="display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap"><label style="font-size:12.5px"><input type="checkbox" id="em-confirm"> I have previewed this email and want it sent to the ticked contacts</label>'
    +'<button type="button" class="btn-primary" id="em-send-btn" disabled>Send</button></div></div>';
  h+='</div>';
  return h;
}
function emailFields(){
  return {subject:$('em-subject').value, preview:$('em-preview').value, headline:$('em-headline').value, body:$('em-body').value,
          cta_text:$('em-cta').value, cta_url:$('em-url').value, hero_url:$('em-hero').value};
}
function emSay(kind,text){ const el=$('em-msg'); if(!el) return; el.style.color=kind==='err'?'#9c2b25':kind==='ok'?'#0a6b3c':'#5a6b7c'; el.innerHTML=text; }
function wireEmailComposer(name){
  const busy=(b,on)=>{ if(b){ b.disabled=!!on; } };
  $('em-draft').onclick=async()=>{
    const brief=$('em-brief').value.trim(); if(!brief){ emSay('err','Say what the email is about first.'); return; }
    busy($('em-draft'),1); emSay('note','Drafting… (AI — review before sending)');
    const d=await skPost('/email/draft',{brief:brief}).catch(()=>({ok:false,error:'no answer'}));
    busy($('em-draft'),0);
    if(!d.ok){ emSay('err',esc(d.error||'Drafting failed.')); return; }
    const x=d.draft||{}; $('em-subject').value=x.subject||''; $('em-preview').value=x.preview||''; $('em-headline').value=x.headline||''; $('em-body').value=x.body||''; $('em-cta').value=x.cta_text||'';
    emSay('ok','Drafted by AI — edit anything, then preview.');
  };
  $('em-preview-btn').onclick=async()=>{
    const d=await skPost('/email/preview',emailFields()).catch(()=>({ok:false}));
    if(!d.ok){ emSay('err','Could not render the preview.'); return; }
    $('em-preview-box').hidden=false; $('em-preview-frame').srcdoc=d.html;
    emSay('note',d.brand&&d.brand.picked?'Preview uses the client’s confirmed brand colors'+(d.brand.logo?' and logo.':'.'):'No confirmed brand for this client yet — the preview uses a neutral navy. Confirm one on the Brand & logos card.');
  };
  $('em-template-btn').onclick=async()=>{
    if(!$('em-subject').value.trim()){ emSay('err','Give it a subject first — that becomes the template name.'); return; }
    busy($('em-template-btn'),1); emSay('note','Saving into the Suite email builder…');
    const d=await skPost('/email/template',Object.assign(emailFields(),{title:$('em-subject').value})).catch(()=>({ok:false,detail:'no answer'}));
    busy($('em-template-btn'),0);
    emSay(d.ok?'ok':'err',esc(d.detail||'')+(d.preview_url?' <a href="'+esc(d.preview_url)+'" target="_blank" rel="noopener">Preview in Suite &rarr;</a>':''));
  };
  $('em-test-btn').onclick=async()=>{
    const to=$('em-test-to').value.trim(); if(!to){ emSay('err','Type the address the test should go to.'); return; }
    if(!$('em-subject').value.trim()){ emSay('err','Give it a subject first.'); return; }
    busy($('em-test-btn'),1); emSay('note','Sending the test from the client’s Suite account…');
    const d=await skPost('/email/test',Object.assign(emailFields(),{to:to})).catch(()=>({ok:false,detail:'no answer'}));
    busy($('em-test-btn'),0);
    emSay(d.ok?'ok':'err',esc(d.detail||''));
  };
  $('em-send-open').onclick=()=>{ $('em-send-box').hidden=!$('em-send-box').hidden; };
  $('em-pick').onclick=async()=>{
    const box=$('em-pick-box'); if(!box.hidden){ box.hidden=true; return; }
    box.hidden=false; box.innerHTML='<span class="muted" style="font-size:12px">Loading the client\'s images…</span>';
    const d=await fetch('/tools/seo-images/api/gallery?company='+encodeURIComponent(name)+'&limit=24',{credentials:'same-origin'}).then(r=>r.ok?r.json():{gallery:[]}).catch(()=>({gallery:[]}));
    const rows=(d.gallery||[]).filter(r=>r.url);
    if(!rows.length){ box.innerHTML='<span class="muted" style="font-size:12px">No images saved for this client yet — the Client Images card is where they are added.</span>'; return; }
    box.innerHTML=rows.map(r=>'<img src="'+esc(r.thumb||r.url)+'" data-full="'+esc(r.url)+'" alt="'+esc(r.alt_text||'')+'" loading="lazy" title="'+esc(r.filename||'')+'" style="width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:6px;border:2px solid transparent;cursor:pointer">').join('');
    box.querySelectorAll('img[data-full]').forEach(im=>{ im.onclick=()=>{ $('em-hero').value=im.dataset.full; box.querySelectorAll('img').forEach(x=>x.style.borderColor='transparent'); im.style.borderColor='var(--navy)'; emSay('note','Hero image set. Preview to see it in place.'); }; });
  };
  $('em-search').onclick=async()=>{
    const q=$('em-q').value.trim(), tag=$('em-tag').value.trim();
    $('em-contacts').innerHTML='<div class="muted" style="font-size:12.5px">Searching…</div>';
    const d=await fetch(SK_API+'/email/contacts?name='+encodeURIComponent(name)+'&q='+encodeURIComponent(q)+'&tag='+encodeURIComponent(tag),{credentials:'same-origin'}).then(r=>r.json()).catch(()=>({ok:false,contacts:[]}));
    const rows=d.contacts||[];
    if(!d.ok){ $('em-contacts').innerHTML='<div class="muted" style="font-size:12.5px">'+esc(d.detail||'Could not search.')+'</div>'; return; }
    if(!rows.length){ $('em-contacts').innerHTML='<div class="muted" style="font-size:12.5px">No contacts with an email address matched.</div>'; return; }
    $('em-contacts').innerHTML='<label style="font-size:12px;display:block;margin-bottom:4px"><input type="checkbox" id="em-all"> Tick all '+rows.length+'</label>'
      +rows.map(c=>'<label style="display:block;font-size:12.5px;padding:2px 0"><input type="checkbox" class="em-c" value="'+esc(c.id)+'"> '+esc(c.name||'(no name)')+' <span class="muted">&lt;'+esc(c.email)+'&gt;</span>'+(c.tags&&c.tags.length?' <span class="muted" style="font-size:11px">'+esc(c.tags.slice(0,3).join(', '))+'</span>':'')+'</label>').join('');
    $('em-all').onchange=()=>{ document.querySelectorAll('.em-c').forEach(x=>x.checked=$('em-all').checked); gate(); };
    document.querySelectorAll('.em-c').forEach(x=>x.onchange=gate);
    gate();
  };
  function picked(){ return Array.from(document.querySelectorAll('.em-c:checked')).map(x=>x.value); }
  function gate(){ const n=picked().length; $('em-send-btn').disabled=!(n&&$('em-confirm').checked&&skEmailReady); $('em-send-btn').textContent=n?'Send to '+n+' contact'+(n>1?'s':''):'Send'; }
  $('em-confirm').onchange=gate;
  $('em-send-btn').onclick=async()=>{
    const ids=picked(); if(!ids.length) return;
    if(!confirm('Send “'+$('em-subject').value+'” to '+ids.length+' contact'+(ids.length>1?'s':'')+' from the client’s Suite account now?')) return;
    busy($('em-send-btn'),1); emSay('note','Sending to '+ids.length+'…');
    const d=await skPost('/email/send',Object.assign(emailFields(),{contact_ids:ids,confirmed:true})).catch(()=>({ok:false,detail:'no answer'}));
    busy($('em-send-btn'),0);
    emSay(d.ok?'ok':'err',esc(d.detail||''));
    if(d.results&&d.results.some(r=>!r.ok)) emSay('err',esc(d.detail||'')+'<br>'+d.results.filter(r=>!r.ok).slice(0,5).map(r=>esc(r.contact_id)+': '+esc(r.detail||'failed')).join('<br>'));
  };
}
