/* Client 360 -- the category pill, the scan screenshots and their lightbox, and the loader that starts every card.
   Cut out of hub/templates/client360.html: the record's own inline script
   grew past 5,900 lines with only render() needing Jinja, so everything
   else lives here as plain files node can lint and the tests can read.
   Load order is hub/client360_assets.MODULES; the template's inline script
   (run, pick, render and the Jinja constants) loads after every module and
   calls into them, never the other way round at load time. Top-level
   let/const here are shared with the other classic scripts on the page. */
/* ==================== end 360 Skills cards ==================== */

function loadIndustry(name){
  var el=document.getElementById('c360IndustryPill');
  if(!el||!name) return;
  fetch('/api/client/industry?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();}).then(function(d){renderIndustryPill(name, d);})
    .catch(function(){ el.innerHTML=''; });
}
function c360EditIndustry(){
  var el=document.getElementById('c360IndustryPill'); if(!el) return;
  var pill=el.querySelector('.pill'), ed=el.querySelector('.c360-industry-edit');
  if(!pill||!ed) return;
  pill.style.display='none'; ed.classList.add('on');
  var inp=ed.querySelector('input'); if(inp&&inp.style.display!=='none') inp.focus();
  el.scrollIntoView({block:'nearest'});
}
function renderIndustryPill(name, d){
  var el=document.getElementById('c360IndustryPill');
  if(!el) return;
  var ind=(d&&d.industry)||{};
  var options=(d&&d.options)||[];
  window.__c360industryOptions=options;
  var label=ind.label||'General Business';
  var custom=String(ind.custom_label||'');
  var srcWords={manual:'set by hand', scan_primary:'their website', scan_sectors:'their website',
                gbp:'their Google listing', name:'their name', general:''};
  var src=srcWords[ind.source||'']||'';
  var when=(ind.resolved_at||'').slice(0,10);
  var title=(src?('Source: '+src+'. '):'')+(when?('Resolved '+when+'. '):'')
    +(ind.evidence?('Evidence: '+ind.evidence):'');
  /* A dropdown of the taxonomy, plus "add your own": a business the table
     has no row for (a winery) keeps its own wording, and the wording is
     resolved onto the nearest key underneath so the Image Picker and every
     other module keyed on the taxonomy still follow the change. */
  var optHtml=options.map(function(o){
    return '<option value="'+esc(o.key)+'"'+(o.key===ind.key&&!custom?' selected':'')+'>'+esc(o.label)+'</option>';
  }).join('')+'<option value="__custom"'+(custom?' selected':'')+'>Other — type your own…</option>';
  el.innerHTML='<span class="pill neutral" title="'+esc(title)+' Click to change." '
    +'style="font-size:12.5px;padding:5px 12px;cursor:pointer" onclick="c360EditIndustry()">'
    +esc(label)+(src?(' <span class="muted" style="font-size:11px">('+esc(src)+')</span>'):'')+'</span>'
    +'<span class="c360-industry-edit">'
    +'<select onchange="var i=this.parentNode.querySelector(\'input\');i.style.display=this.value===\'__custom\'?\'\':\'none\';if(this.value===\'__custom\')i.focus();">'+optHtml+'</select>'
    +'<input type="text" maxlength="80" placeholder="Your own category, e.g. Winery" value="'+esc(custom)+'"'
    +(custom?'':' style="display:none"')+' onkeydown="if(event.key===\'Enter\')this.parentNode.querySelector(\'button\').click()">'
    +'<button type="button" onclick="saveIndustry(\''+esc(name).replace(/'/g,"\\'")+'\',this)">Save</button>'
    +'<button type="button" onclick="var e=this.parentNode;e.classList.remove(\'on\');e.previousElementSibling.style.display=\'\'">Cancel</button>'
    +'</span>';
}
function saveIndustry(name, btn){
  var ed=btn.parentNode;
  var sel=ed.querySelector('select'), inp=ed.querySelector('input');
  var key=sel&&sel.value;
  var body={client:name};
  if(key==='__custom'){
    var text=(inp&&inp.value||'').trim();
    if(!text){ if(inp) inp.focus(); return; }
    body.custom=text;
  }else{
    if(!key) return;
    body.key=key;
  }
  btn.textContent='Saving…';
  fetch('/api/client/industry',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)})
    .then(function(r){return r.json();})
    .then(function(j){
      if(j&&j.error){ c360Notice(j.error); btn.textContent='Save'; return; }
      loadIndustry(name);
      if(window.__c360reloadProfile) window.__c360reloadProfile();
    })
    .catch(function(){ btn.textContent='Save'; c360Notice('That category could not be saved.'); });
}

/* The scan's desktop and mobile captures beside the client's name. Each
   thumbnail is added only once its image has loaded: a capture the scan
   host no longer serves is nothing on the page, never a broken frame. */
function loadScreenshots(name){
  const box=document.getElementById('c360Shots'); if(!box) return;
  const domain=window.__c360domain||'';
  box.hidden=true; box.innerHTML='';
  if(!domain) return;
  const gen=c360Generation;
  fetch('/api/client/screenshots?domain='+encodeURIComponent(domain),{credentials:'same-origin'})
    .then(r=>r.json()).then(d=>{
      if(gen!==c360Generation||!d) return;
      /* The scan's score, one glance for the website: drawn whenever the
         scan has one, screenshots or not, linking to the audit itself. */
      const score=Number(d.score);
      if(d.score!==null&&d.score!==undefined&&isFinite(score)){
        const a=document.createElement('a');
        a.className='c360-score '+(score>=80?'good':score>=60?'mid':'low');
        a.href=d.scan_url||'#'; a.title='Site scan score'+(d.tier?' · tier '+d.tier:'')+(d.scanned_at?' · '+d.scanned_at:'')+'. Opens the audit.';
        a.innerHTML='<span class="sn">'+esc(String(Math.round(score)))+'</span><span class="sl">Site score</span>';
        box.appendChild(a); box.hidden=false;
      }
      if(!d.found) return;
      [['desktop','Desktop'],['mobile','Mobile']].forEach(([k,lab])=>{
        const url=safeExternalUrl(d[k]); if(!url) return;
        const b=document.createElement('button');
        b.type='button'; b.className='c360-shot '+k;
        b.title=lab+' screenshot from the site scan'+(d.scanned_at?' ('+d.scanned_at+')':'')+' — click to enlarge';
        const img=new Image();
        img.alt=lab+' screenshot of '+domain;
        img.loading='lazy';
        img.onload=()=>{ if(gen!==c360Generation) return; box.appendChild(b); box.hidden=false; };
        img.onerror=()=>{ b.remove(); if(!box.children.length) box.hidden=true; };
        b.appendChild(img);
        const cap=document.createElement('span'); cap.className='sl'; cap.textContent=lab; b.appendChild(cap);
        b.onclick=()=>openShot(url, lab+' · '+domain+(d.scanned_at?' · scanned '+d.scanned_at:''), d.scan_url);
        img.src=url;
      });
    }).catch(()=>{});
}
function openShot(url, caption, scanUrl){
  const lb=$('shotLightbox'); if(!lb) return;
  $('shotLightboxImg').src=url;
  $('shotLightboxCap').innerHTML=esc(caption||'')+(scanUrl?' · <a href="'+esc(scanUrl)+'" style="color:#fff">Open the scan →</a>':'');
  lb.classList.add('on'); document.body.style.overflow='hidden';
}
function closeShot(){
  const lb=$('shotLightbox'); if(!lb) return;
  lb.classList.remove('on'); $('shotLightboxImg').src=''; document.body.style.overflow='';
}
document.addEventListener('keydown',function(e){ if(e.key==='Escape') closeShot(); });
function loadBrandAndWork(name){
  loadForms(name);
  loadGroup(name);
  loadOwner(name);
  loadIndustry(name);
  loadScreenshots(name);
  loadOrders(name);
  loadAdPerformance(name);
  wireAskChips();
  loadPipeline(name);
  loadPlaces(name);
  loadYouTube(name);
  loadSuiteEmail(name);
  loadPlan(name);
  loadLanding(name);
  fetch('/api/client/utm?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();}).then(renderUtm).catch(function(){});
  loadSocialIdeas(name);

  loadUpcoming(name);
  fetch('/api/client/analytics-ids?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();}).then(renderAnalyticsIds).catch(function(){});
  if(!name) return;
  /* The domain goes with the name. Brand data is stored two ways — under the
     client name and in a cache keyed by domain — and every lookup anybody has
     ever run landed in the second one, so asking by name alone is why this
     card read "no brand data yet" for clients whose logo was plainly on file. */
  loadBrand(name);
  loadAudience(name);
  fetch('/api/client/work?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(r=>r.json()).then(renderWork).catch(()=>{});
  loadClientLinks(name);
}
