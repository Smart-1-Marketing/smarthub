/* Client 360 -- the social, coming-up, gallery, traffic, images, brand, work, links, UTM, forms, request and IO cards.
   Cut out of hub/templates/client360.html: the record's own inline script
   grew past 5,900 lines with only render() needing Jinja, so everything
   else lives here as plain files node can lint and the tests can read.
   Load order is hub/client360_assets.MODULES; the template's inline script
   (run, pick, render and the Jinja constants) loads after every module and
   calls into them, never the other way round at load time. Top-level
   let/const here are shared with the other classic scripts on the page. */

function scStat(label,value,href,warn){
  // A figure that opens the rows behind it. A count somebody cannot act on is
  // the thing hub/housekeeping.py exists to stop putting on screens.
  const v=`<div style="font-size:22px;font-weight:700;line-height:1.15;color:${warn&&value?'var(--bad,#dc2626)':'var(--navy,#1a2e58)'}">${value}</div>`;
  const l=`<div class="muted" style="font-size:11.5px">${esc(label)}</div>`;
  return href?`<a href="${esc(href)}" style="text-decoration:none;display:block">${v}${l}</a>`
             :`<div>${v}${l}</div>`;
}

/* ---- social suggestions (lifted and driven in node by test_client360_social_ideas.py) ---- */
function renderSocialIdeas(d){
  d=d||{};
  if(d.measured===false) return '<div class="empty">'+esc(d.error||'The ideas could not be read.')+'</div>';
  const c=d.counts||{}, rows=d.ideas||[], link=d.link||{};
  const RESP={pending:['Unanswered','neutral'],liked:['Liked','ok'],passed:['Passed','warn']};
  let h='<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px">'
    +'<span class="pill neutral">'+(c.pending||0)+' unanswered</span>'
    +'<span class="pill ok">'+(c.liked||0)+' liked</span>'
    +'<span class="pill warn">'+(c.passed||0)+' passed</span>'
    +(c.promoted?'<span class="pill info">'+c.promoted+' in a plan</span>':'')
    +'</div>';
  // What they like: every tag with a swipe, strongest first. Nothing answered
  // is a state of its own -- it is the one that means send them the link.
  const answered=(d.weights||[]).filter(w=>w.answered);
  if(answered.length){
    h+='<div class="muted" style="font-size:12px;margin-bottom:4px">What this client responds to</div>'
      +'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">'
      +answered.map(w=>'<span class="pill '+(w.weight>=1?'ok':'neutral')+'" title="'+w.liked+' liked, '+w.passed+' passed" style="font-size:11.5px">'+esc(w.label)+' · '+w.liked+'/'+w.answered+'</span>').join('')
      +'</div>';
  }
  if(!rows.length){
    h+='<div class="muted" style="font-size:12.5px">No ideas yet for this client. <b>Suggest more</b> asks the AI for a batch built on what the Hub knows about them, or type one below.</div>';
  }else{
    if(!answered.length) h+='<div class="muted" style="font-size:12.5px;margin-bottom:8px">Nobody at this client has swiped yet -- the ideas link below is what gets that started.</div>';
    const order={pending:0,liked:1,passed:2};
    const shown=rows.slice().sort((a,b)=>(order[a.response]??3)-(order[b.response]??3)).slice(0,10);
    h+='<table style="width:100%;border-collapse:collapse;font-size:13px">'+shown.map(r=>{
      const rp=RESP[r.response]||RESP.pending;
      return '<tr><td style="padding:5px 8px 5px 0">'+esc(r.title)+(r.promoted?' <span class="muted" style="font-size:11px">· in a plan</span>':'')+'</td>'
        +'<td style="padding:5px 8px" class="muted">'+esc(r.tag_label||'')+'</td>'
        +'<td style="padding:5px 8px" class="muted" style="white-space:nowrap">'+esc(r.source==='model'?'AI':(r.origin==='staff'?'Staff':'House'))+'</td>'
        +'<td style="padding:5px 0;text-align:right;white-space:nowrap"><span class="pill '+rp[1]+'" style="font-size:11px">'+rp[0]+'</span></td></tr>';
    }).join('')+'</table>';
    if(rows.length>shown.length) h+='<div class="muted" style="font-size:12px;margin-top:4px">'+(rows.length-shown.length)+' more on the planner.</div>';
  }
  h+='<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px">'
    +'<button type="button" class="btn-ghost" id="si-more" title="AI -- a batch of ideas from what the Hub knows about this client; nothing is sent to them until they open their link">Suggest more (AI)</button>'
    +'<input type="text" id="si-title" placeholder="Or type an idea…" style="padding:7px 9px;border:1px solid #d6dde5;border-radius:6px;font-size:13px;flex:1;min-width:200px">'
    +'<select id="si-tag" style="padding:7px 9px;border:1px solid #d6dde5;border-radius:6px;font-size:13px"><option value="">Type of post…</option>'+(d.tags||[]).map(t=>'<option value="'+esc(t.key)+'">'+esc(t.label)+'</option>').join('')+'</select>'
    +'<button type="button" class="btn-ghost" id="si-add">Add</button></div>';
  if(link.measured!==false){
    h+='<div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="muted" style="font-size:12px">Their ideas link:</span>'
      +(link.revoked?'<b style="color:var(--bad,#dc2626);font-size:12.5px">turned off</b>'
        :'<code style="font-size:11.5px;background:var(--bg,#f8fafc);border:1px solid var(--line);border-radius:6px;padding:4px 7px;overflow-wrap:anywhere">'+esc(link.url||'')+'</code>'
         +(link.url?'<button type="button" class="gbtn" data-si-copy="'+esc(link.url)+'">Copy</button>':''))
      +'</div>';
  }
  h+='<div id="si-msg" class="muted" style="font-size:12.5px;margin-top:8px"></div>';
  return h;
}
/* ---- end social suggestions ---- */
function loadSocialIdeas(name,note){
  const el=$('c-social-ideas'); if(!el) return;
  const gen=c360Generation;
  fetch('/api/client/social-ideas?name='+encodeURIComponent(name)+'&url='+encodeURIComponent(window.__c360domain||''),{credentials:'same-origin'})
    .then(r=>r.json()).then(d=>{
      if(gen!==c360Generation) return;
      el.innerHTML=renderSocialIdeas(d);
      wireSocialIdeas(name,d);
      if(note){ const m=$('si-msg'); if(m) m.textContent=note; }
    }).catch(()=>{ if(gen!==c360Generation) return; el.innerHTML=renderSocialIdeas({measured:false,error:'The ideas could not be read.'}); });
}
function wireSocialIdeas(name,d){
  const url=(d&&d.url)||'';
  const say=(t,bad)=>{ const m=$('si-msg'); if(m){ m.textContent=t; m.style.color=bad?'#9c2b25':''; } };
  const post=(path,body)=>fetch('/tools/social'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(Object.assign({client:name,url:url},body||{}))}).then(r=>r.json());
  const more=$('si-more'); if(more) more.onclick=async()=>{
    more.disabled=true; say('Asking for a batch of ideas…');
    const r=await post('/api/ideas/generate',{}).catch(()=>({ok:false,error:'no answer'}));
    more.disabled=false;
    if(!r.ok){ say(r.error||'Could not suggest ideas.',true); return; }
    loadSocialIdeas(name,(r.ideas||[]).length+' ideas added'+(r.source==='house'?' from the house list (the AI could not be asked'+(r.note?': '+r.note:'')+')':' by AI')+'. Nothing reaches the client until they open their link.');
  };
  const add=$('si-add'); if(add) add.onclick=async()=>{
    const title=($('si-title').value||'').trim(); if(!title){ say('Type the idea first.',true); return; }
    add.disabled=true;
    const r=await post('/api/ideas',{title:title,idea_tag:$('si-tag').value}).catch(()=>({ok:false,error:'no answer'}));
    add.disabled=false;
    if(!r.ok){ say(r.error||'Could not add it.',true); return; }
    loadSocialIdeas(name,'Added. The client sees it the next time they open their ideas link.');
  };
  document.querySelectorAll('#c-social-ideas [data-si-copy]').forEach(b=>{ b.onclick=()=>copyToClipboard(b.getAttribute('data-si-copy'), b); });
}

/* ---- coming up (lifted and driven in node by test_client360_upcoming.py) ---- */
function renderUpcoming(d){
  d=d||{};
  if(d.measured===false) return '<div class="empty">'+esc(d.error||'Could not read.')+'</div>';
  const items=d.items||[], act=d.activity||{};
  let h='';
  if(act.measured===false){
    h+='<div class="muted" style="font-size:12.5px;margin-bottom:8px">'+esc(act.error||'Last activity could not be read.')+'</div>';
  }else if(act.state==='idle'){
    h+='<div class="muted" style="font-size:12.5px;margin-bottom:8px">No Hub tool has logged work for this client yet.</div>';
  }else{
    const col=act.state==='ok'?'#0a6b3c':act.state==='warn'?'#92400e':'#9c2b25';
    h+='<div style="font-size:12.5px;margin-bottom:8px">Last activity <b style="color:'+col+'">'+(act.days===0?'today':act.days+' days ago')+'</b>'
      +' <span class="muted">· '+esc(act.kind||'work')+(act.source?' via '+esc(act.source):'')+(act.actor?' by '+esc(act.actor):'')+'</span></div>';
  }
  if(!items.length){
    h+='<div class="muted" style="font-size:12.5px">Nothing ends or renews in the next '+(d.horizon_days||120)+' days'+((d.unread||[]).length?', from what could be read':'')+'.</div>';
  }else{
    const cls={bad:'err',warn:'warn',ok:'ok',unread:'neutral'};
    h+='<table style="width:100%;border-collapse:collapse;font-size:13px">'+items.map(i=>{
      const when=i.days===null||i.days===undefined?'date not on file':(i.days<0?Math.abs(i.days)+' days ago':i.days===0?'today':'in '+i.days+' days');
      return '<tr><td style="padding:5px 8px 5px 0;white-space:nowrap"><span class="pill '+(cls[i.state]||'neutral')+'" style="font-size:11px">'+esc(when)+'</span></td>'
        +'<td style="padding:5px 8px"><b>'+esc(i.label)+'</b> · '+esc(i.what)+(i.detail?'<div class="muted" style="font-size:12px">'+esc(i.detail)+'</div>':'')+'</td>'
        +'<td style="padding:5px 0;text-align:right;white-space:nowrap" class="muted">'+esc(i.when||'')+'</td></tr>';
    }).join('')+'</table>';
  }
  if((d.unread||[]).length) h+='<div class="muted" style="font-size:11.5px;margin-top:8px">Not measured: '+d.unread.map(esc).join(' ')+'</div>';
  return h;
}
/* ---- end coming up ---- */
function loadUpcoming(name){
  const el=$('c-upcoming'); if(!el) return;
  const gen=c360Generation;
  fetch('/api/client/upcoming?name='+encodeURIComponent(name)+'&url='+encodeURIComponent(window.__c360domain||''),{credentials:'same-origin'})
    .then(r=>r.json()).then(d=>{ if(gen!==c360Generation) return; el.innerHTML=renderUpcoming(d); })
    .catch(()=>{ if(gen!==c360Generation) return; el.innerHTML=renderUpcoming({measured:false,error:'The dates could not be read.'}); });
}
function socialContentHtml(d,name){
  if(!d||d.measured===false)
    return '<div class="empty">'+esc((d&&d.error)||'Not measured.')+'</div>';
  const r=d.requests||{}, ideas=d.ideas||{}, posts=d.posts||{}, link=d.link||{};
  let h='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:14px;margin-bottom:14px">';
  if(r.measured===false){
    h+='<div class="muted" style="grid-column:1 / -1">'+esc(r.error||'')+'</div>';
  }else{
    h+=scStat('Waiting on us',r.open||0,r.url);
    h+=scStat('Past their date',r.overdue||0,r.url,true);
    h+=scStat('Possible duplicates',r.duplicates||0,r.url);
  }
  if(posts.measured!==false){
    h+=scStat('With the client',posts.waiting_on_client||0,posts.url);
    h+=scStat('Changes asked for',posts.changes_requested||0,posts.url,true);
  }
  if(ideas.measured!==false) h+=scStat('Ideas unanswered',ideas.pending||0,null);
  h+='</div>';

  // The open requests themselves, because a count with no rows under it sends
  // somebody to another screen to find out what it is about.
  if((d.recent||[]).length){
    h+='<table style="width:100%;border-collapse:collapse;font-size:13px">';
    d.recent.forEach(x=>{
      h+=`<tr><td style="padding:6px 8px 6px 0">${esc(x.location)}</td>`
        +`<td style="padding:6px 8px" class="muted">${esc(x.type)}</td>`
        +`<td style="padding:6px 8px" class="muted">${esc(x.when)}</td>`
        +`<td style="padding:6px 0;text-align:right">${x.overdue
            ?'<b style="color:var(--bad,#dc2626)">Overdue</b>':esc(x.status)}</td></tr>`;
    });
    h+='</table>';
  }else if(r.measured!==false && !(r.total)){
    // "Nobody has asked" and "we could not look" read identically as an empty
    // card, and only the first one has an action under it: send them the link.
    h+='<div class="muted" style="font-size:12.5px">Nobody at this client has '
      +'sent a request yet. If they have not been given their link, that is '
      +'usually why.</div>';
  }

  if(ideas.measured!==false){
    h+='<div class="muted" style="font-size:12px;margin-top:10px">'
      +(ideas.answered
        ? esc(ideas.answered+' idea(s) answered'
              +(ideas.liked&&ideas.liked.length?'. Most liked: '+ideas.liked.join(', '):'.'))
        : 'Nobody here has swiped on an idea yet.')
      +'</div>';
  }

  // Their link, and whether it is on. Offered here because this is the screen
  // somebody is on when they realize the client has sent us nothing.
  if(link.pages&&link.pages.length){
    const req=link.pages.filter(p=>p.page==='request')[0];
    h+='<div style="margin-top:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
      +'<span class="muted" style="font-size:12px">Their link:</span>'
      +(link.revoked
        ? '<b style="color:var(--bad,#dc2626);font-size:12.5px">turned off — none of their four pages will open</b>'
        : `<code style="font-size:11.5px;background:var(--bg,#f8fafc);border:1px solid var(--line);border-radius:6px;padding:4px 7px;overflow-wrap:anywhere">${esc(req?req.url:'')}</code>`)
      +'</div>';
  }
  return h;
}

// ---------- Creative gallery ----------
function galleryThumb(item){
  if(item.kind==='image') return `<img src="${esc(item.thumb||item.url)}" loading="lazy" style="width:100%;height:120px;object-fit:cover;border-radius:8px 8px 0 0;background:var(--bg)" onerror="this.outerHTML=galleryIconTile('${esc(item.kind)}')">`;
  if(item.kind==='gdrive'){
    const m=String(item.url).match(/\/d\/([\w-]+)/);
    if(m) return `<img src="https://drive.google.com/thumbnail?id=${m[1]}&sz=w400" loading="lazy" referrerpolicy="no-referrer" style="width:100%;height:120px;object-fit:cover;border-radius:8px 8px 0 0;background:var(--bg)" onerror="this.outerHTML=galleryIconTile('gdrive')">`;
  }
  return galleryIconTile(item.kind);
}
function galleryIconTile(kind){
  const icon={gdrive:'&#128193;',pdf:'&#128462;',dropbox:'&#128230;',image:'&#128444;',file:'&#128206;'}[kind]||'&#128206;';
  return `<div style="width:100%;height:120px;display:flex;align-items:center;justify-content:center;font-size:40px;background:var(--bg);border-radius:8px 8px 0 0">${icon}</div>`;
}
function openGallery(startIdx){
  const items=window._galleryItems||[];
  if(!items.length)return;
  $('galTitle').textContent='Creative files ('+items.length+')';
  $('galGrid').innerHTML=items.map((c,i)=>`
    <a href="${esc(c.url)}" target="_blank" rel="noopener" style="text-decoration:none;color:var(--ink);border:1px solid ${i===startIdx?'var(--brand)':'var(--line)'};border-radius:10px;overflow:hidden;background:#fff;display:block">
      ${galleryThumb(c)}
      <div style="padding:8px 10px">
        <div style="font-weight:600;font-size:12.5px;line-height:1.3">${esc(c.product||'Creative')}</div>
        <div class="muted" style="font-size:11px;margin-top:2px">${esc(c.year||'')}${c.campaign?' · '+esc(c.campaign):''}${c.io?' · IO '+esc(c.io):''}</div>
        <div class="muted" style="font-size:10.5px;margin-top:3px">${KIND_ICON[c.kind]||'&#128206; File'}</div>
      </div>
    </a>`).join('');
  $('galModal').style.display='block';
  document.body.style.overflow='hidden';
  if(startIdx>0){setTimeout(()=>{const el=$('galGrid').children[startIdx];if(el)el.scrollIntoView({block:'center'});},50);}
}
function closeGallery(){$('galModal').style.display='none';document.body.style.overflow='';}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeGallery();});
function openAllFiles(){
  const items=window._galleryItems||[];
  let i=0;
  const next=()=>{
    if(i>=items.length)return;
    window.open(items[i].url,'_blank','noopener');
    i++;
    if(i<items.length)setTimeout(next,450);
  };
  next();
}

// ---------- Traffic Summary: last month vs previous, attach-aware ----------
function fmtEng(sec){sec=Math.round(sec||0);return Math.floor(sec/60)+'m '+(sec%60)+'s';}
function deltaPill(v){
  if(v===null||v===undefined)return '<span class="muted" style="font-size:11px">new</span>';
  const up=v>=0;
  return `<span style="font-weight:700;color:${up?'#16a34a':'#dc2626'};white-space:nowrap">${up?'▲':'▼'} ${Math.abs(v).toFixed(1)}%</span>`;
}
function maybeTraffic(clientName,googleResults){
  if(window.__trafficStarted===clientName)return;   // once per search
  window.__trafficStarted=clientName;
  fetch('/api/client/links?name='+encodeURIComponent(clientName))
    .then(r=>r.json()).then(d=>{
      let att=(d.attached||{}).analytics;
      if(Array.isArray(att)) att=att[0];
      if(att&&att.resource_id){
        $('c-traffic-meta').textContent=(att.name||att.resource_id)+' · '+(att.google_login||'')+' · attached';
        return c360Traffic(att.resource_id,att.google_login);
      }
      const ga=(googleResults||[]).find(it=>it.platform==='Google Analytics');
      if(ga){
        $('c-traffic-meta').textContent=(ga.name||ga.resource_id)+' · '+ga.google_login;
        return c360Traffic(ga.resource_id,ga.google_login);
      }
      showTrafficAttach(clientName);
    }).catch(()=>showTrafficAttach(clientName));
}
function showTrafficAttach(clientName){
  $('c-traffic').innerHTML=`<div class="empty">No Analytics property found for this client — search your connected Google accounts and attach one. It will be used everywhere this client appears going forward.<br><br>
    <div style="display:flex;gap:8px;max-width:520px;margin:0 auto">
      <input id="ta-q" placeholder="Search by client name or domain…" style="flex:1;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:13px inherit" value="${esc(clientName)}">
      <button type="button" class="gbtn" id="ta-go">Search</button>
    </div><div id="ta-res" style="max-width:520px;margin:10px auto 0;text-align:left"></div></div>`;
  const go=()=>{
    const q=$('ta-q').value.trim(); if(!q)return;
    $('ta-res').innerHTML='<div class="empty">Searching… <span class="spin"></span></div>';
    fetch('/google/api/search?q='+encodeURIComponent(q)+'&platform=analytics').then(r=>r.json()).then(d=>{
      const items=(d.results||[]).filter(x=>x.platform==='Google Analytics');
      if(!items.length){$('ta-res').innerHTML='<div class="empty">No matches — try a domain, or connect the owning Google login in the <a href="/google/">Google module</a>.</div>';return}
      $('ta-res').innerHTML=items.slice(0,6).map((x,i)=>`
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;margin-bottom:6px;background:#fff">
          <div><b>${esc(x.name||x.resource_id)}</b> <span class="muted" style="font-size:11.5px">${esc(x.resource_id)}</span>
          <div class="muted" style="font-size:11px">${esc(x.google_login)} · ${esc(x.account_name||'')}</div></div>
          <button type="button" class="gbtn ta-attach" data-i="${i}">Attach →</button>
        </div>`).join('');
      document.querySelectorAll('.ta-attach').forEach(btn=>{
        btn.onclick=()=>{
          const x=items[parseInt(btn.dataset.i,10)];
          // Through the shared attach path, not a bare link write: attaching a
          // property here has to reach the account index (so it stops reading
          // as an orphan) and the Knack website record (which is what every
          // report that never touches Google reads). Each system reports
          // separately — "attached" and "attached in one of three places" are
          // different outcomes.
          fetch('/api/google/attach',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({client:clientName,resource_id:x.resource_id})})
            .then(r=>r.json()).then(res=>{
              if(!res.ok){$('ta-res').innerHTML='<div class="empty">'+esc(res.error||'That did not go through.')+'</div>';return}
              const skipped=(res.skipped||[]).map(w=>w.label+': '+w.why).join(' · ');
              $('c-traffic-meta').textContent=(x.name||x.resource_id)+' · '+x.google_login+' · attached';
              if(skipped)$('ta-res').innerHTML='<div class="empty">'+esc(res.note||'')+'<br>'+esc(skipped)+'</div>';
              c360Traffic(x.resource_id,x.google_login);   // attach auto-runs the report
            });
        };
      });
    });
  };
  $('ta-go').onclick=go;
  $('ta-q').addEventListener('keydown',e=>{if(e.key==='Enter')go();});
}
/* Ask AI — the full Ask Analytics widget, not a cut-down version of it.
   Same endpoint and same renderer as the Google tools page: any metric, any
   dimension, any date range, and it asks a question back when the question as
   put has no defensible answer. */
function c360AskReady(){
  const link=$('c-traffic-ask'); if(!link||!window.__c360GA) return;
  link.style.display='';
  if(link.dataset.wired) return;
  link.dataset.wired='1';
  link.onclick=()=>{
    const box=$('c-traffic-askbox');
    if(box.dataset.open==='1'){ box.style.display='none'; box.dataset.open='0';
      link.textContent='✨ Ask AI about this data'; return; }
    box.style.display=''; box.dataset.open='1';
    link.textContent='Hide Ask AI';
    if(!box.dataset.mounted && window.Smart1Ask){
      box.dataset.mounted='1';
      window.Smart1Ask.mount(box,{
        propertyId:()=>(window.__c360GA||{}).property||'',
        login:()=>(window.__c360GA||{}).login||'',
        label:(document.getElementById('c-traffic-meta')||{}).textContent||'',
        placeholder:'Ask anything about this client’s analytics…'
      });
    }
    box.scrollIntoView({behavior:'smooth',block:'nearest'});
  };
}

/* The period this card reads.

   It used to have none: the card reported last full month, always, and there
   was no way to ask it anything else. It now carries the same Smart1Range
   picker as the SEO client page — the same list of periods, the same
   comparison options, the same custom boxes underneath — and opens on month
   to date, which is what that page opens on too. Flipping between the two
   pages for one client should not change the numbers. */
var c360Range=null;
function c360RangeReady(){
  if(!window.Smart1Range) return;
  var host=$('c-traffic-range'); if(!host) return;
  /* Searching a second client rebuilds the card, which leaves the old picker
     attached to an element no longer on the page — still holding the period
     and still firing onChange into nothing. Re-mount when the host it drew
     into is not the host that is on screen now. */
  if(c360Range && c360Range.el && c360Range.el.parentNode===host) return;
  if(host.firstChild) host.innerHTML='';
  c360Range=window.Smart1Range.mount(host,{
    preset:'mtd', compare:'previous',
    onChange:function(){
      var ga=window.__c360GA||{};
      if(ga.property) c360Traffic(ga.property,ga.login);
    }
  });
}

function c360Traffic(propertyId,googleLogin){
  /* Ask AI needs the same property this card just resolved. Holding it here
     means the button works whether the property came from an attachment, a
     name match, or a manual attach a moment ago. */
  window.__c360GA={property:propertyId,login:googleLogin};
  c360AskReady();
  c360RangeReady();
  var body={property_id:propertyId,google_login:googleLogin};
  var params=(c360Range&&c360Range.params&&c360Range.params())||{};
  Object.keys(params).forEach(function(k){body[k]=params[k];});
  var periodName=(c360Range&&c360Range.value()&&c360Range.value().label)||'month to date';
  $('c-traffic').innerHTML='<div class="empty">Pulling '+esc(periodName)+' vs the comparison period… <span class="spin" data-s1-think="scan"></span></div>';
  fetch('/google/api/ga4/monthly-summary',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)})
    .then(r=>r.json()).then(d=>{
      if(d.needs_reauth){
        // A revoked Google token is fixable in one click — say so, rather than
        // leaving the reader with an error they can't act on.
        $('c-traffic').innerHTML='<div class="empty">'+esc(d.error)+
          ' <a class="btn-primary" style="margin-left:8px;padding:6px 14px;font-size:13px;text-decoration:none" href="'+esc(d.reconnect_url||'/google/login')+
          '" target="_blank" rel="noopener">Reconnect Google</a></div>';return}
      if(d.error){$('c-traffic').innerHTML='<div class="empty">'+esc(d.error)+'</div>';return}
      const cur=d.current||{},prev=d.previous||{},del=d.deltas||{};
      const rows=[
        ['Sessions',cur.sessions,prev.sessions,del.sessions,v=>Number(v||0).toLocaleString()],
        ['Engaged sessions',cur.engaged_sessions,prev.engaged_sessions,del.engaged_sessions,v=>Number(v||0).toLocaleString()],
        ['Avg engagement time',cur.avg_engagement_seconds,prev.avg_engagement_seconds,del.avg_engagement_seconds,fmtEng],
        ['Total events',cur.events,prev.events,del.events,v=>Number(v||0).toLocaleString()],
        ['Total key events',cur.key_events,prev.key_events,del.key_events,v=>Number(v||0).toLocaleString()],
      ];
      let h=`<div style="background:#eef6ff;border:1px solid #cfe3ff;border-radius:10px;padding:12px 16px;margin-bottom:14px;font-size:13.5px;line-height:1.55">
        <b>Executive summary — ${esc((d.period||{}).current||periodName)}:</b> ${esc(d.summary||'')}</div>`;
      h+='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:14px">';
      rows.forEach(([label,c,p,dl,fmt])=>{
        h+=`<div style="background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:11px 14px">
          <div class="muted" style="font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.5px">${label}</div>
          <div style="font-size:20px;font-weight:800;color:var(--navy);margin-top:3px">${fmt(c)}</div>
          <div style="font-size:11.5px;margin-top:2px">${deltaPill(dl)} <span class="muted">vs ${fmt(p)}</span></div>
        </div>`;
      });
      h+='</div>';
      const br=d.breakdown||[];
      if(br.length){
        h+=`<div class="muted" style="font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">Top ${br.length} source / medium — ${esc((d.period||{}).current||'')}</div>`;
        h+='<table><thead><tr><th>Source / Medium</th><th>Sessions</th><th>Engaged</th><th>Avg engagement</th><th>Events</th><th>Key events</th></tr></thead><tbody>';
        br.forEach(b=>{
          const c=b.current||{},p=b.previous||{};
          h+=`<tr><td><b>${esc(b.source_medium||'—')}</b></td>
            <td>${Number(c.sessions||0).toLocaleString()} ${deltaPill(b.delta_sessions)}</td>
            <td>${Number(c.engaged_sessions||0).toLocaleString()}</td>
            <td>${fmtEng(c.avg_engagement_seconds)}</td>
            <td>${Number(c.events||0).toLocaleString()}</td>
            <td>${Number(c.key_events||0).toLocaleString()}</td></tr>`;
        });
        h+='</tbody></table>';
      }
      $('c-traffic').innerHTML=h;
    }).catch(e=>{$('c-traffic').innerHTML='<div class="empty">'+esc(String(e))+'</div>'});
}


// ------------------------------------------------- add more images (ours)
/* Files our team adds from internal resources — a Drive folder of logo
   versions, a Dropbox of store photos — into a folder of the person's
   choosing. The same Cloudinary widget the picker's own pages open
   (modules/image_picker/static/picker-upload.js), so the source tabs, the
   formats and the ceiling are decided server-side once. The folder chooser
   offers what already exists first: a second batch of logos lands in Logos,
   not in a new folder spelled "logo". */
function addMoreImages(name, web0){
  var host = document.getElementById('c-images');
  if(!host) return;
  var box = document.getElementById('c-addimg-box');
  if(box){ box.remove(); return; }
  box = document.createElement('div');
  box.id = 'c-addimg-box';
  box.style.cssText = 'margin:0 0 12px;padding:11px 13px;border:1px solid #c7d2fe;'
    + 'background:#eef2ff;border-radius:9px;font-size:12.5px';
  host.insertBefore(box, host.firstChild);
  box.innerHTML = '<span class="muted">Finding this client’s gallery…</span>';
  var url = (web0 && web0.domain) ? String(web0.domain) : '';
  var BASE = '/tools/image-picker';

  function fail(msg){
    box.innerHTML = '<div style="color:#b45309">'+esc(msg||'The gallery could not be opened.')+'</div>'
      + '<div style="margin-top:7px"><a class="gbtn" href="'+BASE+'/">Open Client Assets →</a> '
      + '<button type="button" class="open-link" id="c-addimg-close" style="margin-left:8px">Close</button></div>';
    document.getElementById('c-addimg-close').onclick = function(){ box.remove(); };
  }

  function script(){
    if(window.S1PickerUpload) return Promise.resolve();
    return new Promise(function(resolve, reject){
      var tag = document.createElement('script');
      tag.src = BASE + '/static/picker-upload.js';
      tag.onload = resolve; tag.onerror = reject;
      document.head.appendChild(tag);
    });
  }

  /* Creating the gallery is what this press means, when there is none:
     somebody chose to add files to this client, which is not a page load. */
  Promise.all([
    script(),
    fetch(BASE+'/api/clients/for-hub-client',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:name, url:url, create:true})}).then(function(r){ return r.json(); })
  ]).then(function(both){
    var d = both[1];
    if(!d || !d.ok || !d.client){ fail(d && d.error); return; }
    var U = window.S1PickerUpload;
    var clientId = d.client.id;
    box.innerHTML =
      '<div style="font-weight:600;color:var(--navy);margin-bottom:6px">Add files to '+esc(name)+'’s gallery</div>'
      + '<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">'
      + '<select id="c-addimg-folder" aria-label="Folder" style="padding:7px 9px;border:1px solid var(--line);border-radius:7px;font:12.5px inherit;min-width:220px"></select>'
      + '<input id="c-addimg-folder-new" hidden placeholder="New folder name, e.g. Logos" maxlength="200" '
      + 'style="padding:7px 9px;border:1px solid var(--line);border-radius:7px;font:12.5px inherit;min-width:220px">'
      + '<button class="gbtn" id="c-addimg-open" type="button">Choose files…</button>'
      + '<button type="button" class="open-link" id="c-addimg-close">Close</button>'
      + '</div>'
      + '<div class="muted" id="c-addimg-state" style="margin-top:6px">From your computer, Google Drive, Dropbox or a link. '
      + 'Originals are kept as uploaded; a web-ready SEO copy of each image is made in the background.</div>'
      + '<div id="c-addimg-strip" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px"></div>';
    var sel = document.getElementById('c-addimg-folder');
    var fresh = document.getElementById('c-addimg-folder-new');
    var state = document.getElementById('c-addimg-state');
    var strip = document.getElementById('c-addimg-strip');
    document.getElementById('c-addimg-close').onclick = function(){ box.remove(); };
    var opts = {
      base: BASE, token: '', clientId: clientId, internal: true,
      folder: function(){ return U.chosenFolder(sel, fresh); },
      onState: function(m){ if(m) state.textContent = m; },
      onAdded: function(info){
        var fig = document.createElement('span');
        fig.className = 'pill neutral';
        fig.textContent = String(info.original_filename || 'file').slice(0, 24);
        strip.appendChild(fig);
        state.textContent = 'Added '+strip.children.length+' file'+(strip.children.length===1?'':'s')
          + (U.chosenFolder(sel, fresh) ? ' to '+U.chosenFolder(sel, fresh) : '') + '.';
      },
      onDuplicate: function(info){
        var fig = document.createElement('span');
        fig.className = 'pill warn';
        fig.textContent = String(info.original_filename || 'file').slice(0, 24) + ' — already in the gallery';
        strip.appendChild(fig);
      },
      onQueueEnd: function(){
        if(window.loadClientImages) window.loadClientImages();
        U.folders(opts).then(function(list){ var keep = U.chosenFolder(sel, fresh); U.fillChooser(sel, fresh, list); if(keep) sel.value = keep; });
      }
    };
    U.folders(opts).then(function(list){ U.fillChooser(sel, fresh, list); });
    document.getElementById('c-addimg-open').onclick = function(){ U.open(opts); };
  }).catch(function(){
    fail('Client Image Uploads could not be reached, so nothing was added.');
  });
}

// ------------------------------------------------- client upload link
/* Hand a client the link they upload their own photographs through.
   Two presses on purpose: the first asks whether a gallery already exists and
   the second creates one, because creating a gallery is a thing somebody
   chose to do, not something a page load does. */
function clientUploadLink(name, web0){
  var host = document.getElementById('c-images');
  if(!host) return;
  var box = document.getElementById('c-uplink-box');
  if(!box){
    box = document.createElement('div');
    box.id = 'c-uplink-box';
    box.style.cssText = 'margin:0 0 12px;padding:11px 13px;border:1px solid #bfdbfe;'
      + 'background:#eff6ff;border-radius:9px;font-size:12.5px';
    host.insertBefore(box, host.firstChild);
  }
  box.innerHTML = '<span class="muted">Looking for this client\u2019s upload gallery\u2026</span>';
  var url = (web0 && web0.domain) ? String(web0.domain) : '';
  ask(false);

  function ask(create){
    fetch('/tools/image-picker/api/clients/for-hub-client',{method:'POST',
      credentials:'same-origin', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:name, url:url, create:!!create})})
      .then(function(r){ return r.json(); }).then(draw)
      .catch(function(){
        box.innerHTML = '<span class="muted">Client Image Uploads could not be '
          + 'reached, so we cannot say whether this client has a link \u2014 '
          + '<b>not measured</b> rather than none.</span>';
      });
  }

  function draw(d){
    if(!d || !d.ok){
      /* Two galleries that could both be this client refuses rather than
         guessing: the wrong pick sends a client's photographs into another
         client's gallery, which is the one mistake here that cannot be undone
         by editing a row. */
      box.innerHTML = '<div style="color:#b45309">'+esc((d&&d.error)||'The upload link could not be made.')+'</div>'
        + '<div style="margin-top:7px"><a class="gbtn" href="/tools/image-picker/">Open Client Assets →</a></div>';
      return;
    }
    if(!d.share_url){
      box.innerHTML = '<div><b>No upload gallery for this client yet.</b> '
        + 'Creating one gives you a link they can send photos, logos and PDFs through; '
        + 'everything they upload lands in this client\u2019s gallery.</div>'
        + '<div style="margin-top:8px"><a class="gbtn" id="c-uplink-make" href="#">Create the upload link</a> '
        + '<button type="button" class="open-link" id="c-uplink-close" style="margin-left:8px">Not now</button></div>';
      document.getElementById('c-uplink-make').onclick = function(e){
        e.preventDefault();
        box.innerHTML = '<span class="muted">Creating\u2026</span>';
        ask(true);
      };
      document.getElementById('c-uplink-close').onclick = function(){ box.remove(); };
      return;
    }
    box.innerHTML =
      '<div style="font-weight:600;color:var(--navy);margin-bottom:5px">'
      + (d.created ? 'Upload link created' : 'Client upload link')
      + (d.matched_on ? ' <span class="muted" style="font-weight:400">\u00b7 matched on '+esc(d.matched_on)+'</span>' : '')
      + '</div>'
      + '<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">'
      + '<input readonly id="c-uplink-url" value="'+esc(d.share_url)+'" '
      + 'style="flex:1;min-width:260px;padding:7px 9px;border:1px solid var(--line);'
      + 'border-radius:7px;font:12px ui-monospace,Consolas,monospace">'
      + '<button class="gbtn" id="c-uplink-copy" type="button">Copy</button>'
      + '<a class="gbtn" href="'+esc(d.share_url)+'" target="_blank" rel="noopener">Open</a>'
      + '</div>'
      + (d.share_enabled === false
          ? '<div style="margin-top:6px;color:#b45309">'+esc(d.note||'')+'</div>'
          : '<div class="muted" style="margin-top:6px">Anything they upload here lands in '
            + esc(name)+'\u2019s gallery. Send it to them \u2014 they need no login.</div>');
    document.getElementById('c-uplink-copy').onclick = function(){
      copyToClipboard(d.share_url, this);
    };
  }
}

/* One delegated handler for every "Copy" button that carries its own url, so a
   card can render one without also wiring a listener for it. */
document.addEventListener('click', function(ev){
  var b = ev.target && ev.target.closest ? ev.target.closest('button[data-copy-url]') : null;
  if(!b) return;
  ev.preventDefault();
  copyToClipboard(b.getAttribute('data-copy-url'), b);
});

/* navigator.clipboard is not available on http and is allowed to refuse, and
   a button that reports a copy it never made is worse than one that asks. */
function copyToClipboard(text, btn, fieldId){
  function field(){ return document.getElementById(fieldId || 'c-uplink-url'); }
  function done(){ var was = btn.textContent; btn.textContent = 'Copied \u2713';
                   setTimeout(function(){ btn.textContent = was; }, 1400); }
  function manual(){
    var f = field();
    if(f){ f.focus(); f.select(); }
    btn.textContent = 'Press Ctrl-C';
  }
  try{
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(text).then(done, fallback);
      return;
    }
  }catch(e){}
  fallback();
  function fallback(){
    try{
      var f = field();
      if(f){ f.focus(); f.select(); if(document.execCommand('copy')){ done(); return; } }
    }catch(e){}
    manual();
  }
}

// ---------------------------------------------------------------- brand card
/* Any logo the Hub holds for this client belongs in their gallery's Logos
   folder, without a press. hub/client_logos.file_logos reads the brand
   record and the last site scan -- never Brandfetch, which is billed -- and
   with `auto` it fetches nothing for a source already filed, so a page load
   that finds nothing new costs one query. Said on the card when something
   was filed, because a file that appears in a gallery with nothing
   announcing it is one nobody knows to look for. */
var LOGOS_AUTO_FILED = false;
function autoFileLogos(d){
  if(LOGOS_AUTO_FILED) return;
  if(!d || !(d.logo_tiles||[]).length) return;
  LOGOS_AUTO_FILED = true;
  fetch('/api/client/logos',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:window.CURRENT_CLIENT||'', domain:window.__c360domain||'', auto:true})})
    .then(function(r){return r.json();}).then(function(res){
      if(!res || !(res.filed||[]).length) return;
      var acts=document.getElementById('c-brand-acts');
      if(acts) acts.insertAdjacentHTML('afterbegin',
        '<span class="pill ok" style="font-size:10.5px" title="'+esc(res.summary||'')+'">Logo filed to gallery</span> ');
      if(window.loadClientImages) window.loadClientImages();
    }).catch(function(){});
}

var COLOR_ROLES_UI=['primary','secondary','accent'];
function renderBrand(d){
  /* The logo is a client image. It is filed into the gallery's Logos folder
     automatically (autoFileLogos), where the images card lists it under
     its own heading beside everything else the client has -- never spliced
     into the card as a tile that exists on no record. */
  autoFileLogos(d);

  const el=document.getElementById('c-brand'); if(!el) return;
  const acts=document.getElementById('c-brand-acts');
  const lookBtn = (d&&d.can_lookup)
    ? '<a class="gbtn" href="#" onclick="lookupBrand();return false" '
      + 'title="Asks the brand service about '+esc(d.lookup_domain||'')
      + '. This call is billed, which is why it is a button.">'
      + (d&&d.found?'Refresh brand':'Look up the brand')+'</a>'
    : '';
  const obs = (d&&d.observed)||{};
  /* "We could not look" is never drawn as "there is nothing" — the rule
     hub/scan_facts.py carries at length. It says which half is missing, so a
     card with colors and no logo does not read as a card with everything. */
  const unread = obs.error
    ? '<div class="muted" style="font-size:11.5px;margin-top:10px">Their website '
      + 'could not be read, so whether it carries a logo is <b>not measured</b> '
      + 'rather than nothing found.</div>'
    : '';

  // Typing a color in never depends on anything having been detected --
  // that is the whole point, since the automated colors are sometimes
  // simply wrong (an announcement bar, a header background) and there has
  // to be a way to say so rather than live with them. hub/brand_template.py
  // accepts any well-formed hex for this, unlike a logo, which still has to
  // be one this Hub already has on file.
  function manualColorRow(role, hx){
    return '<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">'
      +'<label style="width:66px;flex:none;font-size:11.5px;color:#64748b;text-transform:capitalize">'+esc(role)+'</label>'
      +'<input class="bt-hex-input" data-role="'+esc(role)+'" value="'+esc(hx||'')+'" placeholder="#0077B4"'
      +' style="width:110px;padding:4px 7px;border:1px solid var(--line);border-radius:7px;font:12px monospace">'
      +'<a href="#" class="bt-hex-save" data-role="'+esc(role)+'" style="font-size:11.5px">save</a>'
      +(hx?' <a href="#" data-clear-color="'+esc(role)+'" style="font-size:11.5px">clear</a>':'')
      +'</div>';
  }
  function manualColorsBlock(tmpl){
    const colors=(tmpl&&tmpl.colors)||{};
    return '<div style="margin-top:8px"><div class="lbl">Not the right colors? Type theirs in</div>'
      + COLOR_ROLES_UI.map(r=>manualColorRow(r,colors[r])).join('')
      + '</div>';
  }
  const manualColors = manualColorsBlock(d.template);
  function wireManualColors(){
    Array.prototype.forEach.call(el.querySelectorAll('.bt-hex-save'), function(a){
      a.addEventListener('click', function(ev){
        ev.preventDefault();
        const role=a.getAttribute('data-role');
        const input=el.querySelector('.bt-hex-input[data-role="'+role+'"]');
        setBrandPick(role, input?input.value.trim():'');
      });
    });
  }

  if(!d || !d.has_brand){
    el.innerHTML='<div class="empty">'+esc((d&&d.note)||'No brand data yet.')+'</div>'+unread+manualColors;
    if(acts) acts.innerHTML = lookBtn;
    wireManualColors();
    return;
  }

  /* Every swatch and every tile says where it came from. They are one set
     because a rep wants the client's colors, not a lesson in which of our
     services answered — and they stay labeled because a logo the client gave
     us and a logo seen on their home page are different claims, and only the
     first belongs on a document a client reads. */
  const originText = o => o==='site' ? 'Seen on their website'
    : o==='manual' ? 'Typed in' : 'On file';
  /* A swatch or a tile can be *confirmed* now -- a rep's pick of which of
     several is actually the brand, rather than every reader guessing at
     position zero (hub/brand_template.py). The pick/clear links are read
     back off data attributes after the innerHTML assignment below, never
     inline onclick with an embedded URL: a logo URL can carry characters
     that would break out of a quoted JS string, where a data attribute only
     has to survive esc()'s ordinary HTML escaping. */
  const swatches = (d.palette||[]).map(c=>
    `<span title="${esc(c.hex)}${c.type?' — '+esc(c.type):''} · ${esc(originText(c.origin))}"
      style="display:inline-flex;flex-direction:column;align-items:center;gap:3px;margin:0 8px 8px 0">
      <span style="width:38px;height:38px;border-radius:8px;background:${esc(c.hex)};
        border:1px solid ${c.confirmed?'var(--brand)':'rgba(0,0,0,.12)'};display:block${c.origin==='site'?';box-shadow:inset 0 0 0 2px #fff':''}"></span>
      <code style="font-size:10.5px;color:#64748b">${esc(c.hex)}</code>
      ${c.confirmed
        ? `<span style="font-size:9.5px;color:#166534">${esc(c.role)} <a href="#" data-clear-color="${esc(c.role)}">clear</a></span>`
        : `<span style="font-size:9.5px">
             <a href="#" data-set-color="primary" data-hex="${esc(c.hex)}">primary</a> ·
             <a href="#" data-set-color="secondary" data-hex="${esc(c.hex)}">secondary</a> ·
             <a href="#" data-set-color="accent" data-hex="${esc(c.hex)}">accent</a></span>`}
      </span>`).join('');

  const tiles = (d.logo_tiles||[]).slice(0,6).map(l=>
    `<div style="display:inline-block;margin:0 8px 8px 0;vertical-align:top">
      <a href="${esc(l.url)}" target="_blank" rel="noopener"
         style="display:block;padding:8px;border:1px solid ${l.confirmed?'var(--brand)':'var(--line)'};border-radius:8px;background:#fff">
        <img src="${esc(l.url)}" alt="${esc(l.origin==='site'?'Logo on their website':'Logo on file')}"
             loading="lazy" style="max-width:120px;max-height:52px;display:block"></a>
      <div class="muted" style="font-size:10.5px;margin-top:3px;text-align:center">
        ${esc(l.label||originText(l.origin))}${l.format?' · '+esc(l.format.toUpperCase()):''}</div>
      <div style="text-align:center;margin-top:2px;font-size:9.5px">${l.confirmed
        ? `<span style="color:#166534">Brand logo</span> · <a href="#" data-pick-logo="">clear</a>`
        : `<a href="#" data-pick-logo="${esc(l.url)}">set as brand logo</a>`}</div></div>`).join('');

  const fonts = (d.fonts||[]).map(f=>
    `<a href="${esc(f.google)}" target="_blank" rel="noopener"
       style="font-size:13px;color:#1769AA;text-decoration:none;margin-right:12px">${esc(f.name)}</a>`).join('');

  /* A card with something on it still says when nothing has been looked up:
     the colors may be their website's and the brand's own answer may simply
     never have been asked for, which is a button press rather than a fact. */
  const gap = (!d.found && d.note)
    ? `<div class="muted" style="font-size:11.5px;margin-top:10px">${esc(d.note)}</div>` : '';

  el.innerHTML =
    (tiles?`<div style="margin-bottom:10px"><div class="lbl">Logos</div>${tiles}</div>`:'')+
    (swatches?`<div style="margin-bottom:6px"><div class="lbl">Brand colors</div>${swatches}</div>`:'')+
    (fonts?`<div><div class="lbl">Fonts</div>${fonts}</div>`:'')+
    (d.description?`<p style="margin:10px 0 0;font-size:12.5px;color:#64748b">${esc(d.description)}</p>`:'')+
    gap + unread + manualColors;
  wireManualColors();

  // Read off data attributes rather than an inline onclick carrying the
  // value itself -- a logo URL or a hex is data, not a JS literal to embed.
  Array.prototype.forEach.call(el.querySelectorAll('[data-pick-logo]'), function(a){
    a.addEventListener('click', function(ev){
      ev.preventDefault();
      setBrandPick('logo', a.getAttribute('data-pick-logo') || '');
    });
  });
  Array.prototype.forEach.call(el.querySelectorAll('[data-set-color]'), function(a){
    a.addEventListener('click', function(ev){
      ev.preventDefault();
      setBrandPick(a.getAttribute('data-set-color'), a.getAttribute('data-hex') || '');
    });
  });
  Array.prototype.forEach.call(el.querySelectorAll('[data-clear-color]'), function(a){
    a.addEventListener('click', function(ev){
      ev.preventDefault();
      setBrandPick(a.getAttribute('data-clear-color'), '');
    });
  });

  /* Once the guide is in Suite the button is a trap: pressing it again just
     overwrites what's there. Show the state instead. The push carries the
     brand on file and never a sighting, so it is offered only where there is
     one — see brand_guide_payload(). */
  /* Filing costs nothing at any provider — it reads the brand record already
     stored and the last site scan — so it is offered wherever there is a logo
     on the card, including the majority of local businesses who publish no
     brand record and whose only logo came off their own home page. Pressing
     it twice files nothing twice; see hub/client_logos.py. */
  const saveBtn = (d.logo_tiles||[]).length
    ? '<a class="gbtn" href="#" onclick="saveLogosToGallery();return false"'
      + ' title="Store these in this client&rsquo;s image gallery under Logo">'
      + 'Save logo to gallery</a>' : '';

  if(acts) acts.innerHTML = (d.found
    ? (d.suite_brand_guide
        ? '<span class="pill ok" title="Pushed '+esc(d.suite_brand_guide)+'">Brand Guide setup in Suite</span>'
        : '<a class="gbtn" href="#" onclick="pushBrand();return false">Send to Suite Brand Guide</a>')
    : '')
    + ' ' + saveBtn + ' ' + lookBtn;
}

/* The logo the Hub already has, into the gallery a rep actually opens.
   Announced rather than done silently: a file that lands in a gallery with
   nothing saying so is one nobody knows to look for. The answer distinguishes
   filed, already there, and could-not-be-fetched, because a dead logo URL and
   a client with no logo are different things to do next. */
function saveLogosToGallery(){
  var acts = document.getElementById('c-brand-acts');
  if(acts) acts.innerHTML = '<span class="muted" style="font-size:12px">Saving…</span>';
  fetch('/api/client/logos',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:window.CURRENT_CLIENT||'', domain:window.__c360domain||''})})
    .then(function(r){return r.json();}).then(function(d){
      if(d && d.error){ c360Notice(d.error); }
      else if(d && d.summary){ c360Notice(d.summary,'ok'); }
      loadBrand(window.CURRENT_CLIENT||'');
      if(window.loadClientImages) window.loadClientImages();
    }).catch(function(){
      c360Notice('The logo could not be saved to the gallery.');
      loadBrand(window.CURRENT_CLIENT||'');
    });
}

/* Confirm (or clear) which tile or swatch is actually the brand --
   hub/brand_template.py. A pick costs nothing at any provider: it names a
   tile or a hex the card is already showing, never a fetch. */
function setBrandPick(field, value){
  fetch('/api/client/brand-template',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:window.CURRENT_CLIENT||'', domain:window.__c360domain||'',
                         field:field, value:value})})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){ c360Notice(d.error||'That could not be confirmed.'); return; }
      loadBrand(window.CURRENT_CLIENT||'');
    }).catch(function(){ c360Notice('That could not be confirmed.'); });
}

function pushBrand(){
  var acts = document.querySelector('#card-brand .right');

  fetch('/api/client/brand/push-to-suite',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({name:CURRENT_CLIENT})})
   .then(r=>r.json()).then(d=>{
     if(d.error){c360Notice(d.error);return;}
     if(d.delivered){
       // Replace the button rather than leaving it pressable — pressing it
       // again would silently overwrite the guide already in Suite. The
       // server records the push now, so this survives a reload; before it
       // did, the guard held for one page view and the button came back.
       // The stamp is the server's own, so the pill says the same thing
       // after F5 as it does now.
       if(acts) acts.innerHTML='<span class="pill ok"'
         + (d.pushed_at?' title="Pushed '+esc(d.pushed_at)+'"':'')
         + '>Brand Guide setup in Suite</span>';
       c360Notice('Brand guide sent to Smart 1 Suite.','ok');
     } else if(d.reason==='not_configured'){
       c360Notice('No GHL_BRAND_WEBHOOK_URL set, so nothing was sent. '+
             'The payload is ready in the response.');
     } else {
       // Suite refused it, or could not be reached. Reporting either as a
       // missing variable sends somebody to set one that is already set.
       c360Notice((d.note||'The brand guide could not be delivered.')+
             ' Nothing was recorded, so you can try again.');
     }
   }).catch(()=>c360Notice('Could not send the brand guide.'));
}

// ------------------------------------------------------------- work log card
/* mm-dd-yy, from the shared helper. This was a local copy that built the
   string off `new Date(v)` — which parses a date-only ISO string as midnight
   UTC and renders the day before anywhere west of Greenwich, so a renewal
   ending on the 31st read as the 30th. */
function fmtDate(v){ return v ? S1Date.fmt(v, '') : ''; }

/* `Nothing recorded yet` is a claim about the whole history, and work_log()
   can only make it when it reached the end of the activity log -- which it
   reports as `complete`. Short of that the honest sentence names how far back
   it looked, because a long-standing client whose last deliverable predates
   the window would otherwise read as one nobody has ever made anything for. */
function workHorizon(d){ return d && d.horizon ? String(d.horizon).slice(0,10) : ''; }

function renderWork(d){
  const el=document.getElementById('c-work'); if(!el) return;
  const sum=document.getElementById('c-work-sum');
  if(sum) sum.innerHTML='';
  if(d && d.error){
    el.innerHTML='<div class="empty">The activity log could not be read, so this is not measured.</div>';
    return;
  }
  if(!d || !d.count){
    if(d && d.complete===false){
      const since=workHorizon(d);
      el.innerHTML='<div class="empty">Nothing logged for this client'+
        (since?' back to '+esc(since):'')+'. The activity log goes further back '+
        'than this read reached, so this is not "nothing has ever been made".</div>';
    } else {
      el.innerHTML='<div class="empty">Nothing recorded yet. Work shows here as tools log it.</div>';
    }
    return;
  }
  if(sum){
    let line=d.count+' item'+(d.count===1?'':'s');
    if(d.more) line='showing '+d.shown+' of '+d.count;
    if(d.complete===false){
      const since=workHorizon(d);
      if(since) line+=' · back to '+esc(since);
    }
    sum.innerHTML='<span class="muted" style="font-size:12px">'+line+'</span>';
  }
  el.innerHTML='<table style="width:100%;border-collapse:collapse;font-size:13px">'+
    d.items.map(i=>`<tr>
      <td style="padding:6px 8px;border-bottom:1px solid #f1f5f9;white-space:nowrap;color:#94a3b8">
        ${esc(fmtDate(i.when))}</td>
      <td style="padding:6px 8px;border-bottom:1px solid #f1f5f9"><b>${esc(i.kind)}</b>${memberTag(i.member)}</td>
      <td style="padding:6px 8px;border-bottom:1px solid #f1f5f9;color:#64748b">${esc(i.source)}</td>
      <td style="padding:6px 8px;border-bottom:1px solid #f1f5f9;color:#64748b">${esc(i.detail||'')}</td>
      <td style="padding:6px 8px;border-bottom:1px solid #f1f5f9;color:#94a3b8;white-space:nowrap">
        ${esc(i.actor||'')}</td></tr>`).join('')+'</table>';
}

function loadClientLinks(name){
  var box=document.getElementById('c-client-links'); if(!box||!name)return;
  fetch('/api/client/client-links?name='+encodeURIComponent(name),{credentials:'same-origin'}).then(r=>r.json()).then(function(d){
    if(!d||!d.ok){box.innerHTML='<div class="empty">Client links could not be loaded.</div>';return;}
    var open=document.getElementById('c-client-links-open'); if(open)open.href=d.url;
    var items=(d.links||[]).map(x=>'<li><b>'+esc(x.kind||'Proof')+'</b> · <a href="'+esc(x.url)+'" target="_blank" rel="noopener">'+esc(x.label)+' →</a></li>').join('')||'<li class="muted">No client links yet. Add a proof below or create an upload gallery.</li>';
    box.innerHTML='<p class="muted" style="font-size:12px;margin-top:0">Navigation-free Smart 1 Marketing page to send the client.</p><div style="display:flex;gap:7px;flex-wrap:wrap"><input readonly value="'+esc(d.url)+'" id="c-client-links-url" style="flex:1;min-width:230px;padding:7px"><button class="gbtn" id="c-client-links-copy">Copy page link</button></div><ul>'+items+'</ul><details><summary>Add a client-safe proof link</summary><div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:9px"><input id="c-proof-label" placeholder="Proof label"><input id="c-proof-url" placeholder="https://…" style="flex:1"><button class="gbtn" id="c-proof-add">Add</button></div></details>';
    document.getElementById('c-client-links-copy').onclick=function(){copyToClipboard(d.url,this,'c-client-links-url');};
    document.getElementById('c-proof-add').onclick=function(){fetch('/api/client/client-links',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,label:document.getElementById('c-proof-label').value,url:document.getElementById('c-proof-url').value})}).then(r=>r.json()).then(x=>x.ok?loadClientLinks(name):c360Notice(x.error||'Could not add that link.'));};
  }).catch(function(){box.innerHTML='<div class="empty">Client links could not be loaded.</div>';});
}

function renderAnalyticsIds(d){
  var host=document.getElementById('c-analytics-ids'); if(!host||!d) return;
  /* not_comparable is deliberately neutral rather than red or amber: it is
     not a finding and there is nothing to act on. Knack holds the G-
     measurement id and Google reports the numeric property id, which are two
     names for one property — this used to render as a red "mismatch" on every
     client whose GA is recorded and reachable, which is the false positive
     analytics_ids' own docstring says trains people to ignore the warning. */
  var pill={match:'#e7f6ef|#147d52', mismatch:'#feeceb|#b42318',
            recorded_only:'#fff4d7|#a06a00', live_only:'#e0f2fe|#075985',
            not_comparable:'#eef1f4|#475569',
            missing:'#eef1f4|#64748b'};
  host.innerHTML = d.rows.map(function(r){
    var col=(pill[r.state]||pill.missing).split('|');
    return '<div style="padding:8px 0;border-bottom:1px solid #f1f5f9">'+
      '<b style="font-size:12.5px">'+esc(r.kind)+'</b> '+
      '<span style="background:'+col[0]+';color:'+col[1]+';border-radius:20px;'+
      'padding:2px 8px;font-size:10.5px;font-weight:700">'+esc(r.state.replace("_"," "))+'</span><br>'+
      '<span style="font-size:12.5px">'+
        (r.recorded?'<b>Knack:</b> '+esc(r.recorded)+'  ':'')+
        (r.live?'<b>Google:</b> '+esc(r.live)+(r.live_name?' ('+esc(r.live_name)+')':''):
                '<span class="muted">no Google access</span>')+
      '</span>'+
      (r.state!=='match'?'<br><span class="muted" style="font-size:11.5px">'+esc(r.advice)+'</span>':'')+
      '</div>';
  }).join('') +
  (d.google_accounts_connected===0
    ? '<p class="muted" style="font-size:11.5px;margin:8px 0 0">No Google accounts are '+
      'connected, so only the Knack record is shown. <a href="/tools/google-access/">Request access</a>.</p>'
    : '');
}


/* Tracked links comes from two places and says which. `links` are built in
   the UTM Builder; `knack.links` are click-thru URLs carrying UTM parameters
   typed onto an insertion order (object_135 — the click-thru, display
   click-thru, IO click-thru and social fields). Reading only the first made a
   client whose tagged links all arrived on their IOs read as a client with
   none. They are drawn apart rather than merged, because "this one is wrong"
   is fixed on a different screen for each. */
function utmRow(label, sub, url){
  return '<tr><td style="padding:5px 6px;border-bottom:1px solid #f4f7fa">'+
    '<b>'+esc(label||'—')+'</b>'+
    (sub?'<div class="muted" style="font-size:11.5px">'+esc(sub)+'</div>':'')+
    '</td>'+
    '<td style="padding:5px 6px;border-bottom:1px solid #f4f7fa;text-align:right;white-space:nowrap">'+
    '<button class="gbtn" data-utm-url="'+esc(url||'')+'">Copy</button></td></tr>';
}

function utmTag(l){
  /* What the link is tagged as, in the order somebody reads it. Built from
     whatever utm_* parameters are actually on the URL rather than from a
     fixed source/medium/campaign triple: half of these carry utm_id and
     utm_content as well, and the ones that do not must not draw empty slots. */
  var u=l.utm||{}, bits=[];
  ['utm_campaign','utm_source','utm_medium','utm_content','utm_id'].forEach(function(k){
    if(u[k]) bits.push(u[k]);
  });
  Object.keys(u).forEach(function(k){
    if(['utm_campaign','utm_source','utm_medium','utm_content','utm_id'].indexOf(k)<0 && u[k])
      bits.push(u[k]);
  });
  return bits.join(' / ');
}

function renderUtm(d){
  var el=document.getElementById('c-utm'); if(!el) return;
  d=d||{};
  var kn=d.knack||{}, kl=(kn.links||[]);
  var built=(d.links||[]);
  if(!built.length && !kl.length){
    /* Four kinds of nothing, and only two of them mean there is nothing to
       do. A Knack that would not answer must not read as a client with no
       tracked links. */
    var why = d.error ? 'The UTM Builder\'s links could not be read ('+esc(d.error)+').'
            : kn.error ? 'Knack could not be read ('+esc(kn.error)+'), so only '+
                         'links built here are shown — and there are none yet.'
            : 'No tracked links yet, here or on this client\'s insertion orders.';
    el.innerHTML='<div class="empty">'+why+' '+
      '<a href="/tools/utm/" target="_blank">Build some &rarr;</a></div>'; return;
  }

  var html='';
  if(built.length){
    html+='<table style="width:100%;border-collapse:collapse;font-size:12.5px">'+
      built.slice(0,12).map(function(l){
        return utmRow(l.campaign||l.label, (l.source||'')+(l.medium?' / '+l.medium:''), l.url);
      }).join('')+'</table>'+
      (d.count>12?'<div class="muted" style="font-size:11.5px;margin-top:6px">'+
        (d.count-12)+' more in the UTM Builder.</div>':'');
  }

  if(kl.length){
    html+='<div style="margin-top:'+(built.length?'12px':'0')+'">'+
      '<div class="muted" style="font-size:11.5px;margin-bottom:4px">'+
      'Tagged on their insertion orders'+
      (kn.source?' — '+esc(kn.source):'')+'</div>'+
      '<table style="width:100%;border-collapse:collapse;font-size:12.5px">'+
      kl.slice(0,12).map(function(l){
        /* The same link is typed onto every product line of an IO, so it is
           deduped on the URL and the count says how many lines carry it —
           eleven rows of one link is not eleven tracked links. */
        var sub=[utmTag(l), (l.fields||[]).join(', ')].filter(Boolean).join(' · ');
        if(l.count>1) sub+=' · on '+l.count+' product lines';
        return utmRow((l.campaigns||[])[0]||l.url, sub, l.url);
      }).join('')+'</table>'+
      (kn.count>12?'<div class="muted" style="font-size:11.5px;margin-top:6px">'+
        (kn.count-12)+' more on this client\'s insertion orders.</div>':'');
    html+='</div>';
  } else if(kn.error){
    html+='<div class="muted" style="font-size:11.5px;margin-top:10px">'+
      'Links tagged on their insertion orders could not be read ('+esc(kn.error)+'), '+
      'so any of those are missing from this card rather than absent.</div>';
  }

  el.innerHTML=html;
  el.querySelectorAll('button[data-utm-url]').forEach(function(b){
    b.onclick=function(){ copyToClipboard(b.getAttribute('data-utm-url'), b); };
  });
}


function arrow(dir){
  if(dir==='up') return '<span style="color:#147d52">&#9650;</span>';
  if(dir==='down') return '<span style="color:#b42318">&#9660;</span>';
  return '<span class="muted">&#9644;</span>';
}
function renderForms(d){
  var el=document.getElementById('c-forms'); if(!el) return;
  if(d.error){ el.innerHTML='<div class="empty">'+esc(d.error)+'</div>'; return; }
  if(!d.forms || !d.forms.length){
    /* "Nobody filled one in" and "we could not read them" are different
       answers and only the first is good news, so the count of unreadable
       forms is said out loud rather than being absorbed into a tidy nought. */
    el.innerHTML='<div class="empty">No form submissions in '+esc(d.label||'this period')+'.'+
      (d.no_submissions?' '+d.no_submissions+' form(s) exist but had none.':'')+
      (d.unreadable?' '+d.unreadable+' form(s) could not be read, so this may not be the whole picture.':'')+
      '</div>';
    return;
  }
  el.innerHTML =
    '<div style="display:flex;align-items:baseline;gap:10px;margin-bottom:10px">'+
      '<b style="font-size:22px">'+d.total+'</b>'+
      '<span style="font-size:13px">'+arrow(d.total_direction)+' '+esc(d.total_text||'')+'</span>'+
      '<span class="muted" style="font-size:11.5px;margin-left:auto">'+esc(d.label||'')+
      ' · vs '+esc(d.compared_to||'')+'</span></div>'+
    '<table style="width:100%;border-collapse:collapse;font-size:13px">'+
    '<thead><tr><th style="text-align:left;padding:5px 6px;font-size:11px;color:#94a3b8;'+
    'text-transform:uppercase">Form</th><th style="text-align:right;padding:5px 6px;font-size:11px;'+
    'color:#94a3b8;text-transform:uppercase">Submissions</th>'+
    '<th style="text-align:right;padding:5px 6px;font-size:11px;color:#94a3b8;'+
    'text-transform:uppercase">vs previous</th></tr></thead><tbody>'+
    d.forms.map(function(f){
      return '<tr><td style="padding:6px;border-bottom:1px solid #f4f7fa">'+esc(f.name)+'</td>'+
        '<td style="padding:6px;border-bottom:1px solid #f4f7fa;text-align:right"><b>'+f.submissions+'</b></td>'+
        '<td style="padding:6px;border-bottom:1px solid #f4f7fa;text-align:right;white-space:nowrap">'+
        arrow(f.direction)+' <span class="muted">'+esc(f.text)+'</span></td></tr>';
    }).join('')+'</tbody></table>'+
    (d.note?'<div class="muted" style="font-size:11.5px;margin-top:7px">'+esc(d.note)+'</div>':'');
}
function loadForms(name){
  var sel=document.getElementById('formsPeriod');
  var period = sel ? sel.value : 'this_month';
  /* __c360domain is already the client's own domain, set by render() from
     their first website record. Reusing it rather than introducing a second
     global for the same fact -- two descriptions of one thing is how they
     come to disagree. */
  var url = window.__c360domain || '';
  fetch('/api/client/forms?name='+encodeURIComponent(name)+'&period='+period+
        (url?'&url='+encodeURIComponent(url):''),
        {credentials:'same-origin'})
    .then(function(r){return r.json();}).then(renderForms).catch(function(){});
}



function clientRequest(kind){
  var name = CURRENT_CLIENT || '';
  if(!name){ c360Notice('Open a client first.'); return; }
  if(kind === 'ticket'){
    window.open('/tools/tickets/?client=' + encodeURIComponent(name) + '&new=1', '_blank');
    return;
  }
  var label = kind === 'change' ? 'Campaign / Budget Change' : 'Campaign Support / Issue';
  var subject = prompt(label + ' for ' + name + '\n\nWhat is the request?');
  if(!subject) return;
  post('/api/client/campaign-request', {kind: kind, client: name, subject: subject})
    .then(function(d){
      c360Notice(d.error ? d.error : (label + ' submitted for ' + name + '.'), d.error?'err':'ok');
      if(!d.error && window.loadWorkLog) loadWorkLog(name);
    })
    .catch(function(){ c360Notice('That did not go through.'); });
}

function ioStart(mode){
  /* Hand the IO builder what we already know rather than opening a blank
     form. Nothing is submitted here — the builder still walks the rep
     through it, with our values pre-entered and marked with where they came
     from. */
  var name = CURRENT_CLIENT || '';
  if(!name){ c360Notice('Open a client first.'); return; }
  if(mode === 'proposal'){
    window.open('/tools/io/?client=' + encodeURIComponent(name) + '&from=proposal', '_blank');
    return;
  }
  if(mode === 'renewal'){
    fetch('/api/io/prefill?mode=renewal&client=' + encodeURIComponent(name),
          {credentials:'same-origin'})
      .then(function(r){ return r.json(); })
      .then(function(d){
        if(d.error){ c360Notice(d.error); return; }
        if(!confirm('Renew IO ' + d.renewing_io + '?\n\n' +
                    d.products.length + ' product(s), $' + d.previous_monthly +
                    '/month last term (' + d.previous_start + ' to ' + d.previous_end + ').\n\n' +
                    'Dates roll forward a year. You confirm every figure in the builder.')) return;
        window.open('/tools/io/?client=' + encodeURIComponent(name) +
                    '&mode=renewal&io=' + encodeURIComponent(d.renewing_io), '_blank');
      })
      .catch(function(){ c360Notice('Could not read the previous IO.'); });
    return;
  }
  window.open('/tools/io/?client=' + encodeURIComponent(name) + '&mode=new', '_blank');
}


function suiteMatch(name){
  var host = document.getElementById('c-suite-empty');
  if(!host || !name) return;
  var p = new URLSearchParams({prefill:'1', name:name});
  fetch('/api/client/suite-match?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.matches && d.matches.length){
        host.innerHTML =
          '<div style="text-align:left">'+
          '<b style="font-size:13px">Found in Smart 1 Suite</b>'+
          '<p class="muted" style="font-size:12px;margin:4px 0 10px">'+esc(d.note)+'</p>'+
          d.matches.map(function(m){
            return '<div style="display:flex;align-items:center;gap:8px;padding:7px 0;'+
              'border-bottom:1px solid #f1f5f9">'+
              '<div style="flex:1"><b>'+esc(m.name)+'</b>'+
              '<div class="muted" style="font-size:11.5px">'+esc(m.why)+'</div></div>'+
              '<a class="gbtn" target="_blank" rel="noopener" '+
              'href="/suite/?attach='+encodeURIComponent(m.id)+'&name='+encodeURIComponent(name)+
              '">Attach</a></div>';
          }).join('')+
          '<div style="margin-top:10px"><a class="gbtn" href="/suite/?'+p.toString()+
          '">None of these — create a new account</a></div></div>';
      } else {
        host.innerHTML =
          '<span class="muted">'+esc(d.note||'No Smart 1 Suite account yet.')+'</span><br><br>'+
          '<a class="gbtn" href="/suite/" target="_blank">Search the full Suite list</a> '+
          '<a class="btn-primary" style="padding:9px 18px;font-size:13.5px;text-decoration:none" '+
          'href="/suite/?'+p.toString()+'">+ Create account</a>';
      }
    })
    .catch(function(){
      host.innerHTML = '<span class="muted">Couldn\'t reach Smart 1 Suite.</span> '+
        '<a class="gbtn" href="/suite/?'+p.toString()+'">Open Suite</a>';
    });
}
