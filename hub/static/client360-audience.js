/* Client 360 -- the target-audience card and the brand lookup.
   Cut out of hub/templates/client360.html: the record's own inline script
   grew past 5,900 lines with only render() needing Jinja, so everything
   else lives here as plain files node can lint and the tests can read.
   Load order is hub/client360_assets.MODULES; the template's inline script
   (run, pick, render and the Jinja constants) loads after every module and
   calls into them, never the other way round at load time. Top-level
   let/const here are shared with the other classic scripts on the page. */

/* ------------------------------------------------------ audience card
   One confirmed audience per client (hub/audience_spec.py), read by the
   Proposal Builder's audience step, the IO Builder's audiences question
   and the ad-copy prompt. The model proposes and a person ticks — nothing
   below writes anything until Keep is pressed, and Find is a billed call,
   which is why it is a button and never part of loading the card. */
let AUD360={rows:null,busy:false,note:'',target:'',source:'',error:'',sells:''};
function loadAudience(name){
  AUD360={rows:null,busy:false,note:'',target:'',source:'',error:'',sells:''};
  fetch('/api/client/audience?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){renderAudience(d);})
    .catch(function(){
      var el=document.getElementById('c-audience');
      /* "We could not look" is never drawn as "there is nothing" — the
         scan_facts rule, one card over. */
      if(el)el.innerHTML='<div class="empty">The audience record could not be '
        +'read — which is not the same as no audience being confirmed.</div>';
    });
}
function renderAudience(d){
  var el=document.getElementById('c-audience'); if(!el) return;
  var acts=document.getElementById('c-audience-acts');
  window.__aud360=d||{};
  if(d&&d.error){ el.innerHTML='<div class="empty">'+esc(d.error)+'</div>'; return; }
  var h='';
  if(d&&d.picked){
    h+='<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">'
      +(d.audiences||[]).map(function(a,i){return '<span class="pill neutral" '
        +'style="display:inline-flex;align-items:center;gap:6px">'+esc(a)
        +' <a href="#" title="Remove this segment from the confirmed audience" '
        +'onclick="dropAudience360('+i+');return false" '
        +'style="text-decoration:none">&times;</a></span>';}).join('')+'</div>';
    if(d.target)h+='<div class="muted" style="font-size:11.5px">Found against: '+esc(d.target)+'</div>';
    h+='<div class="muted" style="font-size:11.5px;margin-top:4px">Confirmed'
      +(d.updated_by?' by '+esc(d.updated_by):'')
      +(d.updated_at?' on '+esc(String(d.updated_at).slice(0,10)):'')
      +'. The Proposal Builder, the IO Builder and the ad-copy writer read '
      +'this — a value typed on a campaign still wins.</div>';
  }else{
    h+='<div class="empty">No audience confirmed for this client yet. Find asks '
      +'the agency\'s own audience catalog; nothing is saved until you keep '
      +'what fits.</div>';
  }
  h+='<div style="margin-top:9px;display:flex;gap:8px;flex-wrap:wrap">'
    +'<input id="audSells" value="'+esc(AUD360.sells||'')+'" placeholder="Who should this reach / what do they sell? (optional)" '
    +'style="flex:1;min-width:200px;padding:6px 8px;border:1px solid var(--line);border-radius:7px;font:12.5px inherit">'
    +'<a class="gbtn" href="#" onclick="findAudience360();return false" '
    +'title="Asks the Audience Finder — the agency\'s own audience catalog — about this client. This call is billed, which is why it is a button.">'
    +(AUD360.busy?'<span class="spin" data-s1-think="ai"></span>Searching…':'&#128269; Find an audience')+'</a></div>';
  if(AUD360.error)h+='<div class="muted" style="color:#b91c1c;font-size:12px;margin-top:8px">'+esc(AUD360.error)+'</div>';
  if(AUD360.rows){
    if(!AUD360.rows.length){
      h+='<div class="muted" style="font-size:12px;margin-top:8px">'
        +esc(AUD360.note||'Nothing came back for this client.')+'</div>';
    }else{
      h+='<div class="muted" style="font-size:12px;margin:8px 0 6px">'+esc(AUD360.note||'')+'</div>'
        +AUD360.rows.map(function(r,i){return '<label style="display:block;padding:4px 2px;'
          +'border-bottom:1px dashed var(--line);cursor:pointer">'
          +'<input type="checkbox" '+(r.accepted?'checked':'')
          +' onchange="AUD360.rows['+i+'].accepted=this.checked" style="margin-right:7px">'
          +esc(r.name)+'</label>';}).join('')
        +'<div style="margin-top:8px;display:flex;gap:8px">'
        +'<a class="gbtn" href="#" onclick="keepAudience360();return false">Keep ticked</a>'
        +'<a class="gbtn" href="#" onclick="AUD360.rows=null;renderAudience(window.__aud360);return false">Dismiss</a></div>';
    }
  }
  el.innerHTML=h;
  if(acts)acts.innerHTML=(d&&d.picked)
    ?'<a class="gbtn" href="#" onclick="clearAudience360();return false" '
     +'title="Takes the confirmed audience off this record.">Clear</a>':'';
}
function findAudience360(){
  if(AUD360.busy)return;
  var inp=document.getElementById('audSells');
  AUD360.sells=inp?inp.value.trim():'';
  AUD360.busy=true;AUD360.error='';AUD360.rows=null;
  renderAudience(window.__aud360);
  fetch('/api/client/audience/find',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:window.CURRENT_CLIENT||'',sells:AUD360.sells||''})})
   .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
   .then(function(x){
     AUD360.busy=false;
     if(!x.ok||!(x.d&&x.d.ok)){
       AUD360.error=((x.d&&x.d.error)||'The audience research did not run')
         +' — nothing has been changed on this record.';
       AUD360.rows=null;
     }else{
       AUD360.rows=x.d.audiences||[];AUD360.note=x.d.note||'';
       AUD360.target=x.d.target||'';AUD360.source=x.d.source||'';
     }
     renderAudience(window.__aud360);
   })
   .catch(function(){
     AUD360.busy=false;
     AUD360.error='The audience research did not run — nothing has been changed on this record.';
     renderAudience(window.__aud360);
   });
}
function keepAudience360(){
  var ticked=(AUD360.rows||[]).filter(function(r){return r.accepted;})
    .map(function(r){return r.name;});
  if(!ticked.length){c360Notice('Tick the segments that fit first — nothing was saved.');return;}
  var current=((window.__aud360||{}).audiences)||[];
  postAudience360({name:window.CURRENT_CLIENT||'',audiences:current.concat(ticked),
                   target:AUD360.target||'',source:AUD360.source||''});
}
function dropAudience360(i){
  var d=window.__aud360||{};
  var rest=(d.audiences||[]).filter(function(_,j){return j!==i;});
  if(!rest.length){clearAudience360();return;}
  postAudience360({name:window.CURRENT_CLIENT||'',audiences:rest,
                   target:d.target||'',source:d.source||''});
}
function clearAudience360(){
  if(!confirm('Take the confirmed audience off this record? The Proposal '
              +'Builder, the IO Builder and the ad-copy writer will stop '
              +'offering it.'))return;
  postAudience360({name:window.CURRENT_CLIENT||'',clear:true});
}
function postAudience360(body){
  fetch('/api/client/audience',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
   .then(function(r){return r.json();})
   .then(function(d){
     if(d&&d.ok){AUD360.rows=null;if(d.note)c360Notice(d.note,'info');renderAudience(d.audience);}
     else c360Notice((d&&d.error)||'The save did not happen.');
   })
   .catch(function(){c360Notice('The save did not happen — the Hub could not be reached.');});
}

function loadBrand(name){
  var dom = window.__c360domain || '';
  fetch('/api/client/brand?name='+encodeURIComponent(name)
        +(dom?'&domain='+encodeURIComponent(dom):''),{credentials:'same-origin'})
    .then(r=>r.json()).then(renderBrand).catch(()=>{});
}

function lookupBrand(){
  var el=document.getElementById('c-brand'); if(!el) return;
  var acts=document.getElementById('c-brand-acts');
  /* A billed lookup against somebody else's service, so it gets the dish
     rather than the arc: this one is a wait on Brandfetch, not on us. */
  if(acts) acts.innerHTML='<span class="muted" style="font-size:12px">'
    + '<span class="spin" data-s1-think="scan"></span> Looking up their brand…</span>';
  fetch('/api/client/brand/lookup',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:window.CURRENT_CLIENT||'', domain:window.__c360domain||''})})
    .then(r=>r.json()).then(function(d){
      if(d && d.error){ c360Notice(d.error); loadBrand(window.CURRENT_CLIENT||''); return; }
      renderBrand(d);
      /* A lookup that found nothing is an answer, not a failure — and the
         reason matters: a refused key and a business nobody has published
         are different things to do next. */
      if(d && !d.found && d.note) c360Notice(d.note,'info');
      /* A lookup we paid for files the logo into the gallery on the way past
         — hub/brand_lookup.lookup(). Said out loud for the same reason the
         button above says it. */
      else if(d && d.logos_filed) console.info('Logos: ' + d.logos_filed);
      /* The logo IS a client image, so the images card redraws with it. */
      if(window.loadClientImages) window.loadClientImages();
    }).catch(function(){
      c360Notice('The brand lookup could not be run.');
      loadBrand(window.CURRENT_CLIENT||'');
    });
}
