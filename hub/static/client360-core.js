/* Client 360 -- the shared helpers, the request guard, notices, the health strip, the warnings column, the next-action line, the rail and its sections.
   Cut out of hub/templates/client360.html: the record's own inline script
   grew past 5,900 lines with only render() needing Jinja, so everything
   else lives here as plain files node can lint and the tests can read.
   Load order is hub/client360_assets.MODULES; the template's inline script
   (run, pick, render and the Jinja constants) loads after every module and
   calls into them, never the other way round at load time. Top-level
   let/const here are shared with the other classic scripts on the page. */
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const money=v=>{const n=Number(String(v??'').replace(/[$,]/g,''));return isFinite(n)&&n?'$'+n.toLocaleString():esc(v??'—')};

/* A record starts many requests at once. Do not let responses from a prior
   selected client paint into the replacement DOM. A generation guard avoids
   triggering old loaders' recovery handlers, as an aborted request would. */
let c360Generation=0;
const nativeC360Fetch=window.fetch.bind(window);
window.fetch=function(...args){
  const generation=c360Generation;
  const stale=()=>new Promise(()=>{});
  return nativeC360Fetch(...args).then(
    response=>{
      if(generation!==c360Generation) return stale();
      if(!response.ok) showC360RequestFailure(response.status);
      return response;
    },
    error=>generation===c360Generation?Promise.reject(error):stale());
};
/* A source that answered with an error gets said once, with the fix beside
   it: the Refresh button re-runs the record (run()), the same thing a page
   reload does without losing the section that was open. */
/* ---- c360 notices (lifted and driven in node by test_client360_layout.py) ---- */
/* One place every "that did not save" and "sent" goes. `kind` is err (the
   default: most of these are a save that failed), ok, or info. An error
   stays until closed or 12 seconds; the rest go on their own after 6.
   Returns the markup so the test can drive it without a browser. */
function c360NoticeHtml(text, kind){
  const k=(kind==='ok'||kind==='info')?kind:'err';
  return '<div class="c360-toast '+k+'" role="'+(k==='err'?'alert':'status')+'">'
    +'<span class="tx">'+esc(text)+'</span>'
    +'<button type="button" class="tclose" aria-label="Dismiss" onclick="this.parentNode.remove()">&times;</button></div>';
}
function c360Notice(text, kind){
  if(typeof document==='undefined') return;
  let host=document.getElementById('c360Toasts');
  if(!host){ host=document.createElement('div'); host.id='c360Toasts'; document.body.appendChild(host); }
  const wrap=document.createElement('div');
  wrap.innerHTML=c360NoticeHtml(text, kind);
  const el=wrap.firstChild;
  host.appendChild(el);
  while(host.children.length>4) host.firstChild.remove();
  setTimeout(()=>{ if(el.parentNode) el.remove(); }, (kind==='ok'||kind==='info')?6000:12000);
}
/* ---- end c360 notices ---- */
function c360RefreshButton(){
  return '<button type="button" class="c360-refresh" onclick="c360Refresh()">&#8635; Refresh</button>';
}
function c360Refresh(){
  const q=$('q'); if(q&&q.value.trim()){ run(); return; }
  location.reload();
}
function showC360RequestFailure(status){
  const host=document.getElementById('c360-request-status');
  if(host) host.innerHTML='Some Client 360 data could not be loaded (HTTP '+esc(String(status))+'). '
    +'Some cards may be empty or out of date.'+c360RefreshButton();
}
function fetchJson(...args){
  return fetch(...args).then(async response=>{
    if(response.ok) return response.json();
    const body=await response.json().catch(()=>({}));
    throw new Error(body.error||`Request failed (${response.status}).`);
  });
}
/* The strip. Fetched apart from the record so a health source that is slow
   or refusing costs the strip and never the page; a later render() makes an
   earlier answer inert, the rule every other card on this page follows. */
function loadHealth(name){
  const box=$('c360Health'); if(!box) return;
  const gen=c360Generation;
  box.hidden=true;
  window.__c360queue=null;
  fetchJson('/api/client/health?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(h=>{ if(gen!==c360Generation) return; box.innerHTML=renderC360Health(h); box.hidden=false;
      window.__c360queue=(h&&h.queue)||[]; renderC360Warnings(); })
    .catch(err=>{ if(gen!==c360Generation) return;
      box.innerHTML=renderC360Health({error:'The health summary could not be read ('+esc(err.message||'')+').'});
      box.hidden=false; window.__c360queue={error:err.message||'unread'}; renderC360Warnings(); });
}
/* ---- c360 warnings (lifted and driven in node by test_client360_layout.py) ---- */
/* The Client Warnings column of the Smart 1 Internal row. Reads what
   loadHealth() already fetched -- record_health.client360()'s `queue`, the
   list the next-action line is picked from -- plus the record's own local
   findings (a client with no contact on file). Nothing here is a fourth idea
   of what "bad" means; it is the same list, drawn where somebody reading
   who is on the client will see it. */
function renderC360WarningsHtml(queue, local){
  const items=[];
  (local||[]).forEach(function(w){ items.push(w); });
  if(queue&&!Array.isArray(queue)&&queue.error){
    return '<ul class="c360-warn-list"><li class="warn"><span class="wdot"></span><span>Warnings could not be read'
      +' <span class="wd">'+esc(queue.error)+'</span></span></li></ul>'+c360RefreshButton();
  }
  (Array.isArray(queue)?queue:[]).forEach(function(q){ items.push(q); });
  if(queue===null||queue===undefined){
    if(!items.length) return '<div class="c360-warn-none">Checking…</div>';
  }
  if(!items.length) return '<div class="c360-warn-none">Nothing flagged on this record.</div>';
  const order={bad:0,warn:1,info:2};
  items.sort(function(a,b){ return (order[a.level]??3)-(order[b.level]??3); });
  return '<ul class="c360-warn-list">'+items.map(function(q){
    const lvl=(q.level==='bad'||q.level==='info')?q.level:'warn';
    const body='<b>'+esc(q.title||'')+'</b>'+(q.detail?'<span class="wd">'+esc(q.detail)+'</span>':'');
    const inner=q.href
      ? '<a href="'+esc(q.href)+'">'+body+'</a>'
      : '<button type="button" data-go="'+esc(q.section||q.go||'overview')+'">'+body+'</button>';
    return '<li class="'+lvl+'"><span class="wdot"></span><span>'+inner+'</span></li>';
  }).join('')+'</ul>';
}
/* How many findings point at each rail section, and the worst of them --
   what the rail's badges draw. A finding with an href goes to another
   record (the SEO pills) and counts against no section here. */
function c360WarningCounts(queue, local){
  const out={};
  const rank={bad:2,warn:1,info:0};
  const all=(local||[]).concat(Array.isArray(queue)?queue:[]);
  all.forEach(function(q){
    if(!q||q.href) return;
    const sec=q.section||q.go; if(!sec) return;
    const lvl=(q.level==='bad'||q.level==='info')?q.level:'warn';
    const cur=out[sec]||{n:0,level:'info'};
    cur.n+=1;
    if((rank[lvl]||0)>(rank[cur.level]||0)) cur.level=lvl;
    out[sec]=cur;
  });
  return out;
}
/* ---- end c360 warnings ---- */
function paintRailBadges(){
  const rail=document.getElementById('c360Rail'); if(!rail) return;
  const counts=c360WarningCounts(window.__c360queue, window.__c360localWarnings||[]);
  rail.querySelectorAll('.rl').forEach(function(b){
    const old=b.querySelector('.rlb'); if(old) old.remove();
    const c=counts[b.dataset.sec]; if(!c) return;
    const badge=document.createElement('span');
    badge.className='rlb '+c.level; badge.textContent=String(c.n);
    badge.title=c.n+' finding'+(c.n===1?'':'s')+' in this section';
    b.appendChild(badge);
  });
}
function renderC360Warnings(){
  paintRailBadges();
  const el=document.getElementById('c360Warnings'); if(!el) return;
  el.innerHTML=renderC360WarningsHtml(window.__c360queue, window.__c360localWarnings||[]);
}
document.addEventListener('click',function(e){
  const go=e.target.closest&&e.target.closest('.c360-hp[data-go]');
  if(go) showC360Section(go.dataset.go);
  const nba=e.target.closest&&e.target.closest('.c360-nba[data-go]');
  if(nba) showC360Section(nba.dataset.go);
  const warn=e.target.closest&&e.target.closest('.c360-warn-list button[data-go]');
  if(warn) showC360Section(warn.dataset.go);
});
/* Fetched apart from the record for the same reason loadHealth() is: a slow
   or refusing source costs this line and never the page. */
function loadNextAction(name){
  const box=$('c360NextAction'); if(!box) return;
  const gen=c360Generation;
  box.hidden=true;
  fetchJson('/api/client/next-action?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(n=>{ if(gen!==c360Generation) return; box.innerHTML=renderNextAction(n); box.hidden=!n||!n.measured||!n.text; })
    .catch(()=>{ /* silent: the health strip already surfaces a source outage */ });
}
function safeExternalUrl(value){
  try{
    const url=new URL(String(value||''));
    return ['https:','http:'].includes(url.protocol)&&url.hostname?url.href:'';
  }catch(e){ return ''; }
}
let c360SearchGeneration=0;

/* A grouped record shows more than one company's rows side by side. The group
   is a billing relationship, not a rename, so every row that came from another
   member of the group says which one — silently absorbing Fast Fingerprints'
   insertion orders into National Background Check's list is the mistake this
   whole feature is one step away from making. */
const memberTag=m=>m?` <span class="pill neutral" style="font-size:10.5px;padding:1px 7px;vertical-align:middle" title="Grouped client record">${esc(m)}</span>`:'';

const KIND_ICON={gdrive:'&#128193; Drive',pdf:'&#128462; PDF',dropbox:'&#128230; Dropbox',image:'&#128444; Image',file:'&#128206; File'};

// Google Finder functions attached to each Google result.
function googleTools(platform,name,rid){
  const q=encodeURIComponent(rid||name||'');
  const P=String(platform||'');
  if(P==='Google Analytics') return [
    ['GA4 Tools','/google/ga-tools?q='+q],['Compare / AI analysis','/google/ga-tools?q='+q+'#compare'],['History & Logs','/google/history?q='+q]];
  const uParam=window.__c360domain?'&url='+encodeURIComponent(window.__c360domain):'';
  if(P==='Google Tag Manager') return [
    ['GTM Tools','/google/gtm-tools?q='+q+uParam],['Inspect container','/google/gtm-tools?q='+q+uParam+'#inspect'],['History & Logs','/google/history?q='+q]];
  if(P==='Search Console') return [['Webmaster Tools','/google/webmaster-tools?q='+q]];
  if(P==='Google Business Profile') return [['GMB Tools','/google/gmb-tools?q='+q]];
  return [['Open Google Finder','/google/?q='+q]];
}
const gbtns=list=>'<div class="gbtns">'+list.map(([t,h])=>`<a class="gbtn" href="${h}">${t} →</a>`).join('')+'</div>';

/* ---------- sections: the rail, one group of cards at a time ----------
   render() still builds every card exactly as it always has, into a hidden
   staging grid — sectionize() then moves each card into its section by the
   card's own title, the reading hub-accordion.js used for its ordering. A
   card whose title matches nothing lands on Overview rather than vanishing,
   so a card added later is findable without being registered here. Moving a
   node keeps its identity, so every follow-up fetch writing into
   $('c-google') and friends is untouched — and the moves are synchronous,
   before any of those fetches can resolve. hub-accordion.js sees the
   data-s1-workspace marker and stays off this page: its reorder() appends
   every .card into the first card's parent, which here would pile all
   twenty-two into the first section. */
/* ---- c360 sections (lifted and driven in node by test_client360_layout.py) ---- */
const C360_SECTIONS=[
  // Overview is the one-screen summary: what is sold, what is ending, what
  // is proposed, the leads, the notes. The presence cards (Google listing,
  // YouTube channel, email campaigns), the landing pages and the audience
  // used to sit here too, thirteen cards deep, and live with their kin now.
  {key:'overview', label:'Overview',          match:['products','orders we have sent','coming up','ad performance','camhub live cam','smart 1 suite','pipeline & leads','proposals','client notes']},
  // Directly under Overview by request: the work on a client is the second
  // thing anybody opens the record for.
  {key:'work',     label:'Work & requests',   match:['work for this','web tickets','execution plan']},
  {key:'billing',  label:'Billing',           match:['invoices']},
  {key:'website',  label:'Website & audits',  match:['website record','site health','what they are already spending','what we know about this business','landing pages']},
  {key:'google',   label:'Google & traffic',  match:['ga4','gtm container','traffic summary','google listing']},
  {key:'creative', label:'Creative & brand',  match:['creative infor','client images','brand','target audience']},
  {key:'social',   label:'Social & links',    match:['social media','social content','social suggestions','youtube channel','youtube','email campaigns','tracked links','form submissions','approvals & proof links']},
  // Skill-gated cards (modules/skills360). Drawn only for a client whose
  // skill is on; an empty section is the CSS fallback's nothing here.
  {key:'skills',   label:'Skills',            match:['ecommerce','email creator']},
];
function c360SectionFor(title){
  const t=String(title||'').toLowerCase();
  // A match at the START of the title wins, the accordion's own rule —
  // otherwise "brand" claims any card whose title merely contains the word.
  for(const s of C360_SECTIONS){ if(s.match.some(m=>t.indexOf(m)===0)) return s.key; }
  for(const s of C360_SECTIONS){ if(s.match.some(m=>t.indexOf(m)>-1)) return s.key; }
  return 'overview';
}
/* ---- end c360 sections ---- */
/* ---- c360 health (lifted and driven in node by test_client360_health.py) ---- */
/* Draws hub/record_health.client360()'s payload. Pure: takes the payload and
   returns markup, so the test can run it without a browser. Every pill is a
   fact the stores hold, and the three empties are kept apart -- `ok` is
   measured and clear, `idle` is measured and nothing there, `unread` is a
   source that would not answer -- because a strip that draws the third as
   the first is a report answering zero when it could not look. */
const C360_HEALTH_STATES={ok:'ok',warn:'warn',bad:'bad',idle:'idle',unread:'unread'};
function renderC360Health(h){
  if(!h||h.error){
    return '<span class="c360-hnote">'+esc((h&&h.error)||'The health summary could not be built.')+'</span>';
  }
  const pills=(h.pills||[]).map(function(p){
    const st=C360_HEALTH_STATES[p.state]||'idle';
    const title=esc(p.detail||'');
    const body='<span class="hdot"></span><span><span class="lab">'+esc(p.label)+'</span>'
      +'<span class="val">'+esc(p.value)+'</span></span>';
    if(p.href){
      return '<a class="c360-hp '+st+'" href="'+esc(p.href)+'" title="'+title+'">'+body+'</a>';
    }
    return '<button type="button" class="c360-hp '+st+'" data-go="'+esc(p.go||'overview')
      +'" title="'+title+'">'+body+'</button>';
  });
  let note='';
  if(h.unread&&h.unread.length){
    note='<span class="c360-hnote">Not measured: '+h.unread.map(esc).join('; ')
      +'. Anything those would have raised is missing from this strip, not absent from it.</span>';
  }
  return pills.join('')+note;
}
/* ---- end c360 health ---- */
/* ---- c360 next-action (lifted and driven in node by test_next_action.py) ---- */
/* Draws hub/next_action.pick()'s payload: one sentence, worst-first, from
   the same three sources the health strip and the Coming up / Pipeline
   cards already fetch. See hub/next_action.py for the ordering. */
function renderNextAction(n){
  if(!n||!n.measured||!n.text) return '';
  const st=(n.state==='bad'||n.state==='warn')?n.state:'ok';
  const body='<span class="ndot"></span><span>'+esc(n.text)+'</span>';
  if(n.href){
    return '<a class="c360-nba '+st+'" href="'+esc(n.href)+'">'+body+'</a>';
  }
  return '<button type="button" class="c360-nba '+st+'" data-go="'+esc(n.go||'overview')+'">'+body+'</button>';
}
/* ---- end c360 next-action ---- */
function showC360Section(key){
  window.__c360sec=key;
  const work=document.querySelector('.c360-work'); if(!work) return;
  document.querySelectorAll('.c360-view').forEach(v=>
    v.classList.toggle('on', key==='all'||v.id==='v360-'+key));
  document.querySelectorAll('#c360Rail .rl').forEach(r=>
    r.classList.toggle('on', r.dataset.sec===key));
  /* replaceState with a bare fragment keeps ?q= and adds no history entry. */
  try{ history.replaceState(null,'','#'+key); }catch(e){}
  window.scrollTo(0,0);
}
function sectionize(){
  const stage=$('c360Stage'), rail=$('c360Rail'), surf=$('c360Surface');
  if(!stage||!rail||!surf) return;
  surf.innerHTML=C360_SECTIONS.map(s=>
    `<section class="c360-view" id="v360-${s.key}"><div class="grid-2"></div></section>`).join('');
  stage.querySelectorAll('.grid-2 > .card').forEach(card=>{
    const h=card.querySelector('.card-h h3');
    surf.querySelector('#v360-'+c360SectionFor(h?h.textContent:'')+' > .grid-2').appendChild(card);
  });
  stage.remove();
  rail.innerHTML=C360_SECTIONS.map(s=>
    `<button type="button" class="rl" data-sec="${s.key}">${esc(s.label)}</button>`).join('')
    +'<button type="button" class="rl" data-sec="all" title="Every card on one page — the old layout">Everything</button>';
  rail.querySelectorAll('.rl').forEach(b=>{ b.onclick=()=>showC360Section(b.dataset.sec); });
  paintRailBadges();
  // A refresh, a bookmarked #section, or a re-render after picking another
  // group member keeps its place.
  const want=(location.hash||'').replace(/^#/,'')||window.__c360sec||'overview';
  showC360Section(want==='all'||C360_SECTIONS.some(s=>s.key===want)?want:'overview');
}

/* Keep the upload form self-contained.  This page used to call todayISO()
   without defining it, so clicking "Upload proposal" threw before the form
   could be displayed.  Build the date in local time so the value does not
   jump to tomorrow or yesterday around UTC midnight. */
function todayISO(){
  const now=new Date();
  const offset=now.getTimezoneOffset()*60000;
  return new Date(now.getTime()-offset).toISOString().slice(0,10);
}
