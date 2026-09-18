/* Client 360 -- the group banner, the Smart 1 Internal row (roles, owner, followers) and the group modal.
   Cut out of hub/templates/client360.html: the record's own inline script
   grew past 5,900 lines with only render() needing Jinja, so everything
   else lives here as plain files node can lint and the tests can read.
   Load order is hub/client360_assets.MODULES; the template's inline script
   (run, pick, render and the Jinja constants) loads after every module and
   calls into them, never the other way round at load time. Top-level
   let/const here are shared with the other classic scripts on the page. */

/* ------------------------------------------------------------------
   Client groups — one company, several client records.

   National Background Check and Fast Fingerprints are one business: every IO
   and every invoice sits on the parent, and opening the other record reads as
   a client with no products and no history. Grouping them makes one record out
   of the two.

   The whole of the merge happens server-side (hub/client_groups.py) so that
   every card reads the group without each one having to know about it. What is
   here is the banner that says the page is showing more than one company, and
   the modal that attaches or detaches a record.
   ------------------------------------------------------------------ */
function renderGroupBanner(info){
  const el=document.getElementById('c-group'); if(!el) return;
  info=info||{grouped:false};
  /* /api/c360 is the only caller that knows which members it found no records
     for — the roster route reports membership, not what merged. Carry that
     across so a later refresh of the banner does not quietly drop the one line
     that says a member came back empty. */
  const prev=window.__c360group||{};
  if(!info.merged && prev.merged) info.merged=prev.merged;
  window.__c360group=info;
  const btn=document.querySelector('.s1-acc-group');
  if(btn){
    btn.classList.toggle('on', !!(info&&info.grouped));
    btn.innerHTML = info&&info.grouped
      ? '&#128279; Group ('+((info.members||[]).length)+')'
      : '&#128279; Group';
  }
  if(!info||!info.grouped){ el.innerHTML=''; return; }
  const others=(info.others||[]);
  const parent=(info.parent||{}).name||'';
  el.innerHTML='<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;'
    +'background:#eaf7ee;border:1px solid #b7e0c2;border-radius:12px;padding:9px 14px;'
    +'margin:0 0 12px;font-size:13px">'
    +'<b style="color:#1c7a3e">&#128279; Grouped record</b>'
    +'<span>Billing sits on <b>'+esc(parent)+'</b>'
    +(info.is_parent?'':' — you are viewing a member of that group')+'.</span>'
    +'<span class="muted">Also showing: '+(others.length?others.map(m=>esc(m.name)).join(', ')
        :'nothing else yet')+'</span>'
    +'<button type="button" class="gbtn" style="margin-left:auto" onclick="openGroupModal()">Manage group</button>'
    /* Named, not dropped. A member with nothing on file has to be visible as
       that, or "grouped" quietly means "grouped with a record we found no
       products, sites or creative for" and the page looks complete. */
    +(((info.merged||{}).missing||[]).length
      ? '<div style="flex-basis:100%;font-size:12px;color:#7a5b00">No Smart 1 Team records found for: <b>'
        +info.merged.missing.map(esc).join(', ')+'</b> — check the name matches the client record exactly.</div>'
      : '')
    +'</div>';
}

/* Who is on this client -- Partner, Assigned, Client Success and Followers,
   in one simple text strip. The record says everything about the client and
   nothing about whose job they are, so the work on them was six reports
   somebody had to open one at a time. `/api/client/roles` and not the
   assignment tool's own path, because `hub/suite_embed.EMBEDDABLE` allowlists
   `/api/client/` and a card pointed anywhere else renders on every screen
   except the one this page is framed in -- the half-broken embed that file
   exists to prevent.

   Partner and Client Success are read-only here: Partner comes from Knack's
   own client record (object_20 field_872) and Client Success from field_3128
   -- this page shows what Knack says rather than a second place to set it.
   Assigned and Followers are Hub overlays and are editable from here. */
function renderRoles(d){
  var el=document.getElementById('c-owner'); if(!el) return;
  d=d||{};
  var a=d.assigned||{};
  var who=a.email
    ? (a.known
        ? '<b>'+esc(a.name)+'</b>'
        : '<b>'+esc(a.email)+'</b> <span class="pill warn">no Hub account</span>')
      +(a.source==='rule'
        ? ' <span class="muted">through the '+esc(a.rule_partner)+' rule</span>' : '')
    : (a.contested&&a.contested.length
        ? '<span class="pill warn">two partner rules disagree: '
          +a.contested.map(esc).join(' / ')+'</span>'
        : (a.pinned
            ? '<span class="muted">nobody — held off the partner rule</span>'
            : '<span class="muted">nobody yet</span>'));
  var opts='<option value="">— nobody —</option>'+(d.users||[]).map(function(u){
      return '<option value="'+esc(u.email)+'"'+(u.email===a.email?' selected':'')
        +'>'+esc(u.name)+'</option>'; }).join('');
  /* An assigned client shows who, and nothing else: the "since <date> by
     <user>" and "managed from Client Assignments" text that used to follow
     it was noise on every record and is gone. */
  var assignmentControls=a.email
    ? ''
    : '<select id="c-owner-pick" style="padding:4px 7px;'
      +'border:1px solid #cbd5e1;border-radius:7px;font-size:12.5px">'+opts+'</select>'
      +'<button type="button" class="gbtn" id="c-owner-save">Assign</button>'
      +(a.rule&&a.source!=='rule'
        ? '<button type="button" class="gbtn" id="c-owner-follow">Follow the '
          +'partner rule</button>' : '');
  var sales=String((typeof window!=='undefined'&&window.__c360sales)||d.salesperson||'').trim();

  var cs=d.client_success||{};
  var csLine=cs.raw
    ? (cs.known ? esc(cs.name) : esc(cs.raw)+' <span class="pill warn">no Hub account</span>')
    : '<span class="muted">not set</span>';

  var followers=d.followers||[];
  var followChips=followers.map(function(f){
    return '<span class="pill neutral" style="margin-right:4px">'+esc(f.name)
      +' <a data-unfollow="'+esc(f.email)+'" style="cursor:pointer;margin-left:4px" '
      +'title="Remove follower">&times;</a></span>';
  }).join('');
  var followOpts='<option value="">— add a follower —</option>'+(d.users||[]).map(function(u){
      return '<option value="'+esc(u.email)+'">'+esc(u.name)+'</option>'; }).join('');

  el.innerHTML='<div class="c360-internal">'
    +'<div>'
    +'<div class="c360-int-h">Smart 1 Internal</div>'
    +'<div class="c360-int-roles">'
    +'<div>'
    +'<span>&#127760; Partner — '+(d.partner?esc(d.partner):'<span class="muted">none on file</span>')+'</span>'
    +'</div>'
    +'<div>'
    +'<span>&#128188; Salesperson — '+(sales?esc(sales):'<span class="muted">none on file</span>')+'</span>'
    +'</div>'
    +'<div>'
    +'<span>&#128100; Assigned — '+who+'</span>'
    +assignmentControls
    +'</div>'
    +'<div>'
    +'<span>&#127942; Client Success — '+csLine+'</span>'
    +'</div>'
    +'<div>'
    +'<span>&#128065; Followers — </span>'
    +(followChips||'<span class="muted">none yet</span>')
    +'<select id="c-follow-pick" style="padding:4px 7px;border:1px solid #cbd5e1;'
    +'border-radius:7px;font-size:12.5px">'+followOpts+'</select>'
    +'<button type="button" class="gbtn" id="c-follow-add">Add follower</button>'
    +'</div>'
    +(d.partner_error?'<div style="font-size:12px;color:#7a5b00">Partner: '
        +esc(d.partner_error)+'</div>':'')
    +(d.user_error?'<div style="font-size:12px;color:#7a5b00">'
        +esc(d.user_error)+'</div>':'')
    +'</div></div>'
    +'<div class="c360-int-warn">'
    +'<div class="c360-int-h">Client Warnings</div>'
    +'<div id="c360Warnings"><div class="c360-warn-none">Checking\u2026</div></div>'
    +'</div>'
    +'</div>';
  if(typeof renderC360Warnings==='function') renderC360Warnings();

  var follow=document.getElementById('c-owner-follow');
  if(follow) follow.onclick=function(){
    follow.textContent='Saving…';
    fetch('/api/client/owner/set',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({client:window.CURRENT_CLIENT||'',follow_rule:true})})
      .then(function(r){return r.json();})
      .then(function(j){
        if(j.ok===false) throw new Error(j.error||'That did not save.');
        loadOwner(window.CURRENT_CLIENT||'');
      })
      .catch(function(err){
        follow.textContent='Follow the partner rule';
        c360Notice((err&&err.message)||'That did not save.');
      });
  };
  var save=document.getElementById('c-owner-save');
  if(save) save.onclick=function(){
    var pick=document.getElementById('c-owner-pick');
    save.textContent='Saving…';
    fetch('/api/client/owner/set',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({client:window.CURRENT_CLIENT||'',email:pick?pick.value:''})})
      .then(function(r){return r.json();})
      .then(function(j){
        if(j.ok===false) throw new Error(j.error||'That did not save.');
        loadOwner(window.CURRENT_CLIENT||'');
      })
      /* Named rather than silent. Inside the Suite frame the companion cookie
         is accepted for GET and HEAD only, so this write is refused there --
         a stated consequence of that design, and one a card must say out loud
         rather than appearing to save. */
      .catch(function(err){
        save.textContent='Save';
        c360Notice((err&&err.message)||'That did not save. If this page is inside '
          +'Smart 1 Suite, changes have to be made in the Hub itself.');
      });
  };
  var followAdd=document.getElementById('c-follow-add');
  if(followAdd) followAdd.onclick=function(){
    var pick=document.getElementById('c-follow-pick');
    var email=pick?pick.value:'';
    if(!email) return;
    followAdd.textContent='Saving…';
    fetch('/api/client/followers/add',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({client:window.CURRENT_CLIENT||'',email:email})})
      .then(function(r){return r.json();})
      .then(function(j){
        if(j.ok===false) throw new Error(j.error||'That did not save.');
        loadOwner(window.CURRENT_CLIENT||'');
      })
      .catch(function(err){
        followAdd.textContent='Add follower';
        c360Notice((err&&err.message)||'That did not save. If this page is inside '
          +'Smart 1 Suite, changes have to be made in the Hub itself.');
      });
  };
  el.querySelectorAll('[data-unfollow]').forEach(function(x){
    x.onclick=function(){
      var email=x.getAttribute('data-unfollow');
      fetch('/api/client/followers/remove',{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({client:window.CURRENT_CLIENT||'',email:email})})
        .then(function(r){return r.json();})
        .then(function(j){
          if(j.ok===false) throw new Error(j.error||'That did not save.');
          loadOwner(window.CURRENT_CLIENT||'');
        })
        .catch(function(err){
          c360Notice((err&&err.message)||'That did not save.');
        });
    };
  });
}

function loadOwner(name){
  var el=document.getElementById('c-owner');
  if(!el||!name) return;
  fetch('/api/client/roles?client='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();}).then(renderRoles)
    /* "Nobody owns this" and "we could not read who owns it" are different
       answers, and drawing the first over the second is how a client gets
       assigned twice. */
    .catch(function(){ el.innerHTML='<div class="pill warn" style="margin:0 0 12px;'
      +'display:inline-block">Could not read who is assigned to this client.</div>'; });
}

function loadGroup(name){
  const el=document.getElementById('c-group');
  /* /api/c360 already answered with this client's roster, so draw it now
     rather than leaving the banner blank for a round-trip — a page that says
     nothing about the group for half a second is a page somebody reads as
     ungrouped. The fetch below refreshes it. */
  if(window.__c360group) renderGroupBanner(window.__c360group);
  fetch('/api/client/group?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(r=>r.json()).then(d=>renderGroupBanner(d.roster||{grouped:false}))
    /* A group we could not read must say so. Rendering nothing would be
       indistinguishable from "this client is not grouped", and the page would
       then be quietly showing one company's records under another's name with
       no sign that anything was missing. */
    .catch(()=>{ if(el) el.innerHTML='<div class="pill warn" style="margin:0 0 12px;display:inline-block">'
      +'Could not read this client\'s group — any grouped records are missing from this page.</div>'; });
}

/* hub-accordion.js calls this when it builds the toolbar, which can happen
   after the roster has already come back. */
window.S1GroupLabel=function(btn){
  const info=window.__c360group;
  if(!info||!btn) return;
  btn.classList.toggle('on', !!info.grouped);
  if(info.grouped) btn.innerHTML='&#128279; Group ('+((info.members||[]).length)+')';
};

window.openGroupModal=function(){
  const name=window.CURRENT_CLIENT||'';
  if(!name){ c360Notice('Search for a client first.'); return; }
  let m=document.getElementById('grpModal');
  if(m) m.remove();
  m=document.createElement('div');
  m.id='grpModal';
  m.style.cssText='position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:100002;'
    +'display:flex;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto';
  m.innerHTML='<div style="background:#fff;border-radius:14px;width:660px;max-width:100%">'
    +'<div style="display:flex;justify-content:space-between;align-items:center;padding:13px 18px;border-bottom:1px solid var(--line)">'
      +'<b style="color:var(--navy)">Group — '+esc(name)+'</b>'
      +'<button id="grpClose" style="border:0;background:none;font-size:22px;cursor:pointer;color:#64748b">&times;</button></div>'
    +'<div style="padding:16px 18px" id="grpBody"><div class="empty">Loading… <span class="spin"></span></div></div></div>';
  document.body.appendChild(m);
  m.addEventListener('click',e=>{if(e.target===m)m.remove();});
  document.getElementById('grpClose').onclick=()=>m.remove();
  grpLoad();
};

function grpLoad(){
  const name=window.CURRENT_CLIENT||'';
  const body=document.getElementById('grpBody'); if(!body) return;
  fetch('/api/client/group?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(r=>r.json()).then(d=>grpRender(d.roster||{grouped:false}))
    .catch(()=>{body.innerHTML='<div class="empty">Could not read the group.</div>';});
}

function grpRender(info){
  const name=window.CURRENT_CLIENT||'';
  const body=document.getElementById('grpBody'); if(!body) return;
  renderGroupBanner(info);
  let h='';
  if(info.grouped){
    h+='<div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;'
      +'color:#64748b;margin-bottom:8px">In this group</div>';
    h+=(info.members||[]).map(mem=>
      '<div style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--line);'
      +'border-radius:8px;margin-bottom:6px">'
      +'<b>'+esc(mem.name)+'</b>'
      +(mem.role==='parent'
        ? '<span class="pill ok" title="Every IO and invoice for the group is filed here">Parent — holds the billing</span>'
        : '<span class="pill neutral">Member</span>')
      +(mem.url?'<span class="muted" style="font-size:11.5px">'+esc(mem.url)+'</span>':'')
      +'<button type="button" class="gbtn grp-del" data-n="'+esc(mem.name)+'" data-u="'+esc(mem.url||'')+'" data-role="'+esc(mem.role)+'"'
      +' style="color:#b91c1c;margin-left:auto">Remove</button></div>').join('');
    h+='<p class="muted" style="font-size:12px;margin:10px 0 18px">'
      +'Products &amp; IOs, creative, notes, work, proposals and invoices are read across '
      +'every record above, wherever you open the group from. Rows keep the name of the '
      +'record they belong to. Nothing is written to Knack — removing a record from the '
      +'group leaves it exactly as it is.</p>';
  } else {
    h+='<p class="muted" style="font-size:13px;margin:0 0 16px">'
      +'<b>'+esc(name)+'</b> is not grouped. Attach another company below and the two '
      +'read as one record: products &amp; IOs, creative, client notes, work, proposals '
      +'and invoices are shown across both, from either one.</p>';
  }
  h+='<div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;'
    +'color:#64748b;margin-bottom:8px">Attach another company</div>'
    +'<div style="display:flex;gap:8px"><input id="grpQ" placeholder="Company name or domain…" '
    +'style="flex:1;padding:8px 11px;border:1px solid var(--line);border-radius:8px;font:13px inherit">'
    +'<button type="button" class="gbtn" id="grpGo">Search</button></div>'
    +'<div id="grpRes" style="margin-top:10px"></div>'
    +'<div id="grpMsg" style="margin-top:10px"></div>';
  body.innerHTML=h;

  body.querySelectorAll('.grp-del').forEach(a=>{
    a.onclick=()=>{
      const isParent=a.dataset.role==='parent';
      /* Removing the parent dissolves the group rather than promoting one of
         the members: which sibling holds the bill is not a question this page
         can answer, and guessing it moves every invoice on the record. */
      if(!confirm(isParent
        ? 'Remove the parent “'+a.dataset.n+'”? That dissolves the whole group. '
          +'Every client record stays exactly as it is — nothing is written to Knack.'
        : 'Remove “'+a.dataset.n+'” from this group?')) return;
      fetch('/api/client/group/remove',{method:'POST',headers:{'Content-Type':'application/json'},
        credentials:'same-origin',body:JSON.stringify({client:a.dataset.n,url:a.dataset.u})})
        .then(r=>r.json()).then(d=>{
          if(d.error){document.getElementById('grpMsg').innerHTML='<div class="pill err">'+esc(d.error)+'</div>';return}
          const gm=document.getElementById('grpModal'); if(gm) gm.remove();
          c360Refresh();   // every card on the page is now reading a different set
        });
    };
  });

  const go=()=>{
    const q=(document.getElementById('grpQ').value||'').trim();
    if(!q) return;
    const res=document.getElementById('grpRes');
    res.innerHTML='<div class="empty">Searching… <span class="spin"></span></div>';
    fetch('/api/clients/search?q='+encodeURIComponent(q)+'&limit=10',{credentials:'same-origin'})
      .then(r=>r.json()).then(d=>{
        const rows=(d.clients||[]).filter(c=>String(c.name||'').toLowerCase()!==String(name).toLowerCase());
        if(!rows.length){res.innerHTML='<div class="empty">No client matched “'+esc(q)+'”.</div>';return}
        res.innerHTML=rows.map((c,i)=>
          '<div style="display:flex;align-items:center;gap:10px;padding:7px 10px;border:1px solid var(--line);'
          +'border-radius:8px;margin-bottom:6px;background:#fff">'
          +'<b>'+esc(c.name)+'</b>'
          +'<span class="muted" style="font-size:11.5px">'+esc(c.domain||c.url||'no URL on file')
          +(c.product_count?' · '+c.product_count+' IOs':'')+'</span>'
          +'<button type="button" class="gbtn grp-add" data-i="'+i+'" style="margin-left:auto">Attach →</button></div>').join('');
        res.querySelectorAll('.grp-add').forEach(btn=>{
          btn.onclick=()=>{
            const c=rows[parseInt(btn.dataset.i,10)];
            const info2=window.__c360group||{};
            /* The parent is the record the billing already sits on. When this
               client is already a member of a group, the new record joins that
               same parent rather than starting a second group around a member —
               two groups claiming one company is the one state the store
               refuses, and it would be easy to create from here. */
            const parent=(info2.grouped&&info2.parent&&info2.parent.name)?info2.parent.name:name;
            const purl=(info2.grouped&&info2.parent&&info2.parent.url)?info2.parent.url:(window.__c360domain||'');
            btn.textContent='Attaching…';
            fetch('/api/client/group/add',{method:'POST',headers:{'Content-Type':'application/json'},
              credentials:'same-origin',
              body:JSON.stringify({parent:parent,parent_url:purl,
                                   member:c.name,member_url:c.domain||c.url||''})})
              .then(r=>r.json()).then(d2=>{
                if(d2.error){
                  btn.textContent='Attach →';
                  document.getElementById('grpMsg').innerHTML=
                    '<div class="pill err" style="white-space:normal;display:block;padding:8px 12px">'+esc(d2.error)+'</div>';
                  return;
                }
                const gm=document.getElementById('grpModal'); if(gm) gm.remove();
                c360Refresh();   // the whole record now reads across both
              });
          };
        });
      }).catch(()=>{res.innerHTML='<div class="empty">Client search unavailable.</div>';});
  };
  document.getElementById('grpGo').onclick=go;
  document.getElementById('grpQ').addEventListener('keydown',e=>{if(e.key==='Enter')go();});
}
