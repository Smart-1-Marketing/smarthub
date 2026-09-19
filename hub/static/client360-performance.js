/* Client 360 -- the execution plan, ad performance, landing pages, Google listing, YouTube, email campaigns, pipeline and orders cards.
   Cut out of hub/templates/client360.html: the record's own inline script
   grew past 5,900 lines with only render() needing Jinja, so everything
   else lives here as plain files node can lint and the tests can read.
   Load order is hub/client360_assets.MODULES; the template's inline script
   (run, pick, render and the Jinja constants) loads after every module and
   calls into them, never the other way round at load time. Top-level
   let/const here are shared with the other classic scripts on the page. */

/* The insertion orders this Hub sent for a client.

   Four empties, not one: nothing sent, the store could not be read, an order
   Smart 1 Suite never took, and an order with no campaign in Knack against
   its number. Only the first means there is nothing to look at, and the last
   two are somebody's to act on.

   Whether Knack has the campaign is read off `g.products`, which is already
   on the page — a second reconciliation here would be a second answer to a
   question `hub/io_reconcile.py` already answers, and the two would come to
   disagree on one screen. */
/* The execution plan a proposal produced: counts beside a link, never the
   items. Three answers are kept apart -- the table would not answer, no plan
   has been built, and a plan with nothing left to do here -- because the
   first two render identically as an empty card and only one of them means
   there is nothing to do. */
function loadPlan(name){
  var box=document.getElementById('c-plan');
  if(!box) return;
  if(!name){box.innerHTML='<div class="empty">No client selected.</div>';return;}
  fetch('/api/client/execution-plan?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      box=document.getElementById('c-plan');
      if(!box) return;
      if(d && d.measured===false){
        box.innerHTML='<div class="empty">The execution plans could not be read'
          + (d.error?' &mdash; '+esc(d.error):'') + '. That is not the same as '
          + 'no plan existing.</div>';
        return;
      }
      var rows=(d&&d.runs)||[];
      if(!rows.length){
        box.innerHTML='<div class="empty">No execution plan has been built for this '
          + 'client. <a href="/proposal-execution?client='+encodeURIComponent(name)+'">'
          + 'Analyze a proposal</a> to get the creative, launch and monthly lists.</div>';
        return;
      }
      var h='<table><thead><tr><th>Proposal</th><th>Launch</th><th>Kept</th>'
        + '<th>Still to do on the plan</th><th></th></tr></thead><tbody>';
      rows.forEach(function(p){
        var todo=[];
        if(p.to_review) todo.push(p.to_review+' item'+(p.to_review===1?'':'s')+' to review');
        if(p.open_questions) todo.push(p.open_questions+' question'+(p.open_questions===1?'':'s')+' open');
        /* What the client answered on their own page and nobody has taken
           onto the plan yet -- a reply read by nothing. */
        if(p.client_answers_pending) todo.push(p.client_answers_pending+' answer'
          + (p.client_answers_pending===1?'':'s')+' from the client to confirm');
        if(p.creative_unassigned) todo.push(p.creative_unassigned+' creative item'
          + (p.creative_unassigned===1?'':'s')+' with no supplier named');
        /* Launch tasks and creative past the date the launch date put on
           them, with nothing marked done and nothing landed. */
        var pg=p.progress||{};
        if(pg.overdue) todo.push(pg.overdue+' launch or creative item'+(pg.overdue===1?'':'s')+' past due');
        /* The monthly promises, month by month, are on the plan; what the
           record says is whether any is missed or still due this month. A
           schedule with no launch date to measure from says nothing here --
           the plan's own line names that gap. */
        var pr=p.promises||{};
        if(pr.measured!==false){
          if(pr.missed) todo.push(pr.missed+' monthly promise'+(pr.missed===1?'':'s')+' missed');
          if(pr.due) todo.push(pr.due+' due this month');
        }
        var kept=p.kept||{};
        h+='<tr><td><b>'+esc(p.title||'Proposal')+'</b> <span class="pill neutral">'
          + esc(String(p.state||'').replace(/_/g,' '))+'</span>'
          + (p.member&&p.member!==name?memberTag(p.member):'')+'</td>'
          + '<td class="muted" style="font-size:12.5px">'+esc(p.launch_date_label||'not answered')+'</td>'
          + '<td class="muted" style="font-size:12.5px">'+esc(kept.creative||0)+' creative &middot; '
          + esc(kept.launch||0)+' launch &middot; '+esc(kept.monthly||0)+' monthly</td>'
          + '<td>'+(todo.length?todo.map(function(t){return '<span class="pill warn">'+esc(t)+'</span>';}).join(' ')
                   :'<span class="pill neutral">reviewed and answered</span>')+'</td>'
          + '<td><a href="'+esc(p.url||'/proposal-execution')+'">Open the plan &rarr;</a></td></tr>';
      });
      box.innerHTML=h+'</tbody></table>';
    })
    .catch(function(){
      var b=document.getElementById('c-plan');
      if(b) b.innerHTML='<div class="empty">The execution plans could not be read. '
        + 'That is not the same as no plan existing.</div>';
    });
}
/* ---- c360 ad performance (lifted and driven in node by test_reports_pages.py) ---- */
/* Draws /api/client/ad-performance's payload. Pure: takes the payload and the
   client's name and returns markup, so the test can run it without a
   browser. Four kinds of nothing are drawn apart -- the store would not
   answer, nothing is filed, everything filed is waiting for a person, and
   the confirmed campaigns spent nothing this period -- because a card that
   draws the first three as the fourth is a report answering zero when it
   could not look. Nothing here is a figure the card computed: every number
   is the server's, and the server's is client_view's, so the card and the
   Reports page cannot disagree about a client's month. */
function adMoney(v){
  var n=Number(v);
  if(!isFinite(n)) return '—';
  return '$'+n.toLocaleString(undefined,{maximumFractionDigits:0});
}
function adInt(v){ var n=Number(v); return isFinite(n)?n.toLocaleString():'—'; }
function renderAdPerformance(d,name){
  if(!d||d.measured===false){
    return '<div class="empty">The ad-performance store could not be read'
      + (d&&d.error?' &mdash; '+esc(d.error):'')
      + '. That is not the same as no campaign running for this client.</div>';
  }
  var c=d.campaigns||{};
  var unmapped=esc(d.unmapped_url||'/reports/unmapped');
  var staff=esc(d.staff_url||'/reports/');
  var open='<div class="muted" style="margin-top:8px;font-size:12.5px"><a href="'+staff+'">Open this client in Reports &rarr;</a></div>';
  if(d.state==='no_campaigns'){
    var lines=Number(d.budget_lines||0);
    return '<div class="empty">No advertising campaign is filed under this client in Reports'
      + (lines?', and '+lines+' budget line'+(lines===1?' is':'s are')+' set up waiting on one to pace against':'')
      + '. A campaign is filed on <a href="'+unmapped+'">the unmapped queue</a>, or files itself '
      + 'when it is named <code>S1M | &lt;ClientKey&gt; | &lt;Product&gt; | &hellip;</code> in the platform '
      + 'and a person confirms it.</div>'+open;
  }
  if(d.state==='all_pending'){
    var pn=Number(c.pending||0);
    return '<div class="empty">'+pn+' campaign'+(pn===1?' is':'s are')+' filed under this client from '
      + 'the campaign name and waiting for a person to confirm the filing. Nothing reaches this card '
      + 'or the client\'s page until then. <a href="'+unmapped+'">Confirm on the unmapped queue</a>.</div>'+open;
  }
  var per=d.period||{};
  var h='<div class="muted" style="font-size:12.5px;margin-bottom:8px">'+esc(per.label||'This month')
    + ' &middot; '+Number(c.confirmed||0)+' confirmed campaign'+(Number(c.confirmed)===1?'':'s')
    + (c.platforms&&c.platforms.length?' on '+esc(c.platforms.join(', ')):'')
    + (c.pending?' &middot; <a href="'+unmapped+'">'+c.pending+' more waiting for confirmation</a>':'')
    + '</div>';
  if(d.state==='nothing_this_period'){
    h+='<div class="empty">No spend is recorded for '+esc(per.label||'this period')
      + ' on the confirmed campaigns yet.</div>';
  }else{
    h+='<table><thead><tr><th>Platform</th><th>Spend</th><th>Impressions</th><th>Clicks</th>'
      + '<th>CTR</th><th>Billed</th></tr></thead><tbody>';
    (d.totals||[]).forEach(function(t){
      h+='<tr><td>'+esc(t.label||t.platform)+'</td><td>'+adMoney(t.raw_spend)+'</td>'
        + '<td>'+adInt(t.impressions)+'</td><td>'+adInt(t.clicks)+'</td>'
        + '<td>'+(t.ctr==null?'—':Number(t.ctr).toFixed(2)+'%')+'</td>'
        + '<td>'+(t.client_price==null
            ?'<span class="muted" title="No markup or CPM is set for this platform, so there is no billed figure to show">not priced</span>'
            :adMoney(t.client_price))+'</td></tr>';
    });
    h+='<tr><td><b>Total</b></td><td><b>'+adMoney(d.spend_total)+'</b></td><td></td><td></td><td></td>'
      + '<td><b>'+(d.billed_total==null?'—':adMoney(d.billed_total))+'</b></td></tr></tbody></table>';
  }
  /* The sold lines against the month, from the pacing board's own rows. */
  if(d.pacing&&d.pacing.length){
    h+='<div style="margin-top:10px;font-size:12.5px"><b>Pacing</b> ';
    d.pacing.forEach(function(p){
      var cls=p.band==='on'?'ok':(p.band==='under'||p.band==='stalled')?'bad':'warn';
      var txt=(p.product||p.platform_label||'line')+': '+(p.band_label||p.band)
        + (p.pace!=null?' '+Number(p.pace).toFixed(2)+'×':'')
        + (p.alert?' for '+p.trend_days+' days':'')
        + (p.band==='unmapped'&&p.pending_campaigns?' ('+p.pending_campaigns+' waiting for confirmation)':'');
      var title=adMoney(p.spent)+' spent of '+adMoney(p.expected)+' expected by now; '
        + adMoney(p.monthly_budget)+' a month; '+p.days_remaining+' days left';
      h+='<span class="pill '+cls+'" title="'+esc(title)+'">'+esc(txt)+'</span> ';
    });
    h+='<a href="'+esc(d.pacing_url||'/reports/pacing')+'">board &rarr;</a></div>';
  }
  /* A day held in quarantine is a day missing from the figures above. */
  if(d.held===null||d.held===undefined){
    h+='<div class="muted" style="margin-top:6px;font-size:12px">Quarantine could not be read, so '
      + 'whether a day is held out of these figures is not measured.</div>';
  }else if(d.held){
    h+='<div class="muted" style="margin-top:6px;font-size:12px"><span class="pill warn">'+d.held
      + ' day'+(d.held===1?'':'s')+' held in quarantine</span> and missing from the figures above '
      + '&mdash; <a href="/reports/quarantine">decide them</a>.</div>';
  }
  /* A feed that is not current explains a low figure better than the figure does. */
  if(d.feeds===null||d.feeds===undefined){
    h+='<div class="muted" style="margin-top:6px;font-size:12px">Feed health could not be read.</div>';
  }else if(d.feeds.length){
    h+='<div style="margin-top:6px;font-size:12px">'+d.feeds.map(function(f){
      return '<span class="pill warn" title="'+esc(f.detail||'')+'">'+esc(f.label)+' feed '
        + esc(f.state_label||f.state)+'</span>';
    }).join(' ')+' <span class="muted">&mdash; the figures stop where the feed did.</span></div>';
  }
  if(d.link){
    var v=Number(d.link.views||0);
    h+='<div class="muted" style="margin-top:8px;font-size:12.5px">Client link: '
      + (v?'opened '+v+' time'+(v===1?'':'s')+(d.link.last_viewed_at?', last on '+esc(String(d.link.last_viewed_at).slice(0,10)):''):'never opened')
      + (d.link.show_spend?'':' &middot; delivery only, no spend shown')
      + ' &middot; <a href="'+esc(d.link.url)+'" target="_blank" rel="noopener">open</a></div>';
  }else{
    h+='<div class="muted" style="margin-top:8px;font-size:12.5px">No live client link yet &mdash; '
      + '<a href="'+staff+'">create one in Reports</a>.</div>';
  }
  return h+open;
}
/* ---- end c360 ad performance ---- */
/* ---- c360 landing pages (lifted and driven in node by test_landing_maker.py) ---- */
/* Draws /api/client/landing-pages. Pure: takes the payload and the client's
   name and returns markup, so the test can run it without a browser.

   Three reads stand behind one card -- the pages store, the visit table and
   the lead store -- and each says separately when it could not answer. The
   card never turns any of those into a nought: "nobody has opened it" and
   "we could not read the opens" send a rep to opposite conclusions about a
   campaign somebody is paying for, and only one of them is a reason to
   pause it. Nothing here is a figure this card computed; the readiness and
   the conversion line are the server's own, so the card and the Landing
   Page Maker cannot disagree about a page. */
function landingReadyPill(r){
  if(!r||r.measured===false) return '<span class="pill neutral">readiness not measured</span>';
  if(r.ready) return '<span class="pill ok">ready to send</span>';
  var n=Number(r.count||0);
  return '<span class="pill warn">'+n+' to fix before sending</span>';
}
function landingRatePill(c){
  if(!c||c.measured===false) return '<span class="pill neutral">not measured</span>';
  if(c.state==='measured') return '<span class="pill ok">'+esc(String(c.rate))+'%</span>';
  if(c.state==='over') return '<span class="pill warn">opens undercounted</span>';
  if(c.state==='too_early') return '<span class="pill neutral">too early</span>';
  return '<span class="pill neutral">no opens yet</span>';
}
function renderLandingPages(d,name){
  if(d && d.measured===false){
    return '<div class="empty">The landing pages could not be read'
      + (d.error?' &mdash; '+esc(d.error):'') + '. That is not the same as '
      + 'this client having none.</div>';
  }
  var rows=(d&&d.pages)||[];
  if(!rows.length){
    return '<div class="empty">No landing page has been built for this client. '
      + '<a href="/sales/landing?client='+encodeURIComponent(name||'')+'">'
      + 'Build one</a> and the link, its readiness and its conversion rate '
      + 'appear here.</div>';
  }
  var h='<table><thead><tr><th>Page</th><th>Link</th><th>Ready</th>'
    + '<th>Opens</th><th>Conversion</th></tr></thead><tbody>';
  rows.forEach(function(p){
    var title=p.headline||p.campaign||p.slug||'Landing page';
    var v=p.views, c=p.conversion||{};
    /* The cell stays a number -- a full sentence in a table column is not a
       table. What it gained is the DEFINITION, as a title: this screen used
       to print "N (M recent)" and say nowhere what an open counts, while the
       landing maker said it in its own words and hub/landing_views.line_for()
       -- the function written to be the one reading -- had no caller. The
       line is the server's, so the two screens cannot word it apart again. */
    var openTitle = [(v && v.line) || '', d && d.views_counting_note || '']
      .filter(Boolean).join(' ');
    var opens = (v && typeof v.views!=='undefined')
      ? '<span'+(openTitle?' title="'+esc(openTitle)+'"':'')+'>'
        + esc(String(v.views))
        + (v.recent?' <span class="muted" style="font-size:11.5px">('+esc(String(v.recent))+' recent)</span>':'')
        + '</span>'
      : '<span class="muted">not measured</span>';
    h+='<tr><td><b>'+esc(title)+'</b>'
      + (p.campaign&&p.campaign!==title?'<div class="muted" style="font-size:12px">'+esc(p.campaign)+'</div>':'')
      + (p.member&&p.member!==name?memberTag(p.member):'')+'</td>'
      /* No url means the Hub cannot name its own public address -- the row
         says so rather than drawing a link that would hand over a path. */
      + '<td>'+(p.url?'<a href="'+esc(p.url)+'" target="_blank" rel="noopener">'+esc(p.slug||'open')+'</a>'
               :'<span class="muted" title="PUBLIC_BASE_URL is not set">no public address</span>')+'</td>'
      + '<td>'+landingReadyPill(p.readiness)+'</td>'
      + '<td>'+opens+'</td>'
      + '<td>'+landingRatePill(c)
      + (c&&c.line?'<div class="muted" style="font-size:11.5px">'+esc(c.line)+'</div>':'')
      + '</td></tr>';
  });
  h+='</tbody></table>';
  /* Said once under the table rather than per row: a caveat on every line is
     a caveat nobody reads, and these are one table and one store either way. */
  var notes=[];
  if(d && d.views_measured===false){
    notes.push('The visit table could not be read'
      + (d.views_error?' &mdash; '+esc(d.views_error):'')
      + ', so the opens and the rates are missing from this rather than nought.');
  } else if(d && d.conversion_measured===false){
    notes.push('The lead store could not be read, so there is no rate.');
  }
  if(notes.length){
    h+='<div class="muted" style="font-size:11.5px;margin-top:6px">'+notes.join(' ')+'</div>';
  }
  return h;
}
/* ---- end c360 landing pages ---- */
/* ---- c360 google listing (lifted and driven in node by test_places.py) ---- */
/* Draws /api/client/places' payload. Pure: takes the payload, the client's
   name and any candidates a lookup returned, and returns markup. Five kinds
   of nothing are drawn apart -- the key is not set, no listing is confirmed,
   the listing is confirmed and not yet read, the last read failed, and the
   store would not answer -- and a rating is never printed without its review
   count. Every figure is the server's reading; nothing here computes one. */
function plStars(v){
  var n=Number(v);
  if(!isFinite(n)) return '—';
  return n.toFixed(1)+' \u2605';
}
function plInt(v){
  var n=Number(v);
  return isFinite(n)?n.toLocaleString():'—';
}
function plDelta(v,unit){
  if(v===null||v===undefined||!isFinite(Number(v))) return '';
  var n=Number(v);
  if(n===0) return 'no change';
  return (n>0?'+':'')+(unit==='star'?n.toFixed(1)+' \u2605':n.toLocaleString())+(unit==='star'?'':' reviews');
}
function renderPlaces(d,name,cands){
  d=d||{}; cands=cands||null;
  var st=d.state||'unread';
  var rec=d.record||null;
  var h='';
  if(st==='unconfigured'){
    return '<div class="empty">Google Places is not set up on this deployment, so no listing can be read. '
      + '<a href="/status">System status</a> names the setting.</div>';
  }
  if(st==='unread'&&!rec){
    return '<div class="empty">'+esc(d.error||'The listing store could not be read.')
      + ' That is not the same as this client having no listing.</div>';
  }
  if(cands){
    if(cands.error){
      h+='<div class="empty">'+esc(cands.error)+'</div>';
    }else if(!cands.candidates||!cands.candidates.length){
      h+='<div class="empty">Google returned no listing for &ldquo;'+esc(cands.query||name)+'&rdquo;. '
        + 'Add the city or the street and search again.</div>';
    }else{
      h+='<div class="muted" style="font-size:12px;margin-bottom:6px">'+esc(cands.candidates.length)+' listing'+(cands.candidates.length===1?'':'s')
        + ' for &ldquo;'+esc(cands.query||name)+'&rdquo;'
        + (cands.proposed?' &mdash; one proposed: '+esc(cands.why||''):' &mdash; '+esc(cands.why||''))+'</div>';
      h+='<ul class="plain" style="margin:0;padding:0;list-style:none">';
      cands.candidates.forEach(function(c){
        var prop=cands.proposed&&c.place_id===cands.proposed;
        h+='<li style="padding:6px 0;border-top:1px solid #eef1f5'+(prop?';background:#f3faf6':'')+'">'
          + '<b>'+esc(c.name||c.place_id)+'</b>'+(prop?' <span class="pill ok">proposed</span>':'')
          + (c.domain_match?' <span class="pill ok">their website</span>':'')
          + '<div class="muted" style="font-size:12px">'+esc(c.address||'')
          + (c.rating!==null&&c.rating!==undefined?' &middot; '+plStars(c.rating)+' from '+plInt(c.review_count)+' reviews':'')
          + (c.status_label?' &middot; '+esc(c.status_label):'')
          + (c.website?' &middot; '+esc(c.domain||c.website):'')+'</div>'
          + '<button class="btn small" data-act="confirm" data-id="'+esc(c.place_id)+'">This is their listing</button>'
          + '</li>';
      });
      h+='</ul>';
    }
    h+='<div class="muted" style="font-size:12px;margin-top:8px"><a href="#" data-act="cancel">Cancel</a></div>';
    return h;
  }
  if(st==='no_place'){
    return '<div class="empty">No Google listing has been confirmed for this client.</div>'
      + '<div class="row" style="margin-top:8px;gap:6px;align-items:center">'
      + '<input type="text" id="pl-q" placeholder="Add a city or street to narrow it" style="flex:1;min-width:120px">'
      + '<button class="btn small" data-act="lookup">Find their listing</button></div>'
      + '<div class="muted" style="font-size:12px;margin-top:6px">One billed lookup. It proposes a listing only when exactly one matches; you confirm.</div>';
  }
  var recLine='';
  if(rec){
    recLine='<div class="muted" style="font-size:12px">'+esc(rec.name||rec.place_id||'')+(rec.address?' &middot; '+esc(rec.address):'')
      + (rec.maps_url?' &middot; <a href="'+esc(rec.maps_url)+'" target="_blank" rel="noopener">Open on Google</a>':'')
      + (rec.confirmed_by?' &middot; confirmed by '+esc(rec.confirmed_by)+(rec.confirmed_at?' on '+esc(String(rec.confirmed_at).slice(0,10)):''):'')
      + '</div>';
  }
  if(st==='no_snapshot'){
    return recLine+'<div class="empty">Confirmed and not read yet. The nightly read will take it, or press Refresh reading.</div>';
  }
  if(st==='unread'){
    return recLine+'<div class="empty">The last read failed: '+esc(d.error||'')+' The listing is still confirmed.</div>';
  }
  h+='<div style="font-size:26px;font-weight:600;line-height:1.1">'+plStars(d.rating)
    + ' <span style="font-size:14px;font-weight:400;color:var(--muted)">from '+plInt(d.review_count)+' Google reviews'
    + (d.status_label?' &middot; '+esc(d.status_label):'')+'</span></div>';
  var ch=d.change||{};
  if(ch.measured){
    h+='<div class="muted" style="font-size:12px;margin-top:4px">Over the last '+esc(ch.days)+' days: '
      + esc(plDelta(ch.rating_delta,'star')||'rating not measured')+', '
      + esc(plDelta(ch.reviews_delta,'reviews')||'reviews not measured')
      + ' (since '+esc(ch.since||'')+').</div>';
  }else if(ch.note){
    h+='<div class="muted" style="font-size:12px;margin-top:4px">'+esc(ch.note)+'</div>';
  }
  h+='<div class="muted" style="font-size:12px;margin-top:4px">Read '+esc(d.as_of||'')+' from Google.'+(d.note?' '+esc(d.note):'')+'</div>';
  if(d.staff_note) h+='<div class="muted" style="font-size:12px;color:#a15c00">'+esc(d.staff_note)+'</div>';
  h+=recLine;
  h+='<div class="row" style="margin-top:8px;gap:6px">'
    + '<button class="btn small" data-act="refresh">Refresh reading</button>'
    + '<button class="btn small ghost" data-act="clear">Not this listing</button></div>';
  return h;
}
/* ---- end c360 google listing ---- */
/* ---- c360 youtube channel (lifted and driven in node by test_youtube.py) ---- */
/* Draws /api/client/youtube's payload. Pure: takes the payload, the client's
   name and any candidates a lookup returned, and returns markup. The same
   five kinds of nothing as the Google listing card, drawn apart, and a
   subscriber count the channel hides reads as hidden rather than as a
   nought. Every figure is the server's reading; nothing here computes one. */
function ytInt(v){
  var n=Number(v);
  return (v===null||v===undefined||!isFinite(n))?'—':n.toLocaleString();
}
function ytDelta(v,unit){
  if(v===null||v===undefined||!isFinite(Number(v))) return '';
  var n=Number(v);
  if(n===0) return 'no change in '+unit;
  return (n>0?'+':'')+n.toLocaleString()+' '+unit;
}
function renderYouTube(d,name,cands){
  d=d||{}; cands=cands||null;
  var st=d.state||'unread';
  var rec=d.record||null;
  var h='';
  if(st==='unconfigured'){
    return '<div class="empty">YouTube is not set up on this deployment, so no channel can be read. '
      + '<a href="/status">System status</a> names the setting.</div>';
  }
  if(st==='unread'&&!rec){
    return '<div class="empty">'+esc(d.error||'The channel store could not be read.')
      + ' That is not the same as this client having no channel.</div>';
  }
  if(cands){
    if(cands.error){
      h+='<div class="empty">'+esc(cands.error)+'</div>';
    }else if(!cands.candidates||!cands.candidates.length){
      h+='<div class="empty">'+(cands.why?esc(cands.why):'YouTube returned no channel for &ldquo;'+esc(cands.query||name)+'&rdquo;.')
        + ' Paste the channel&rsquo;s link, or add a word and search again.</div>';
    }else{
      h+='<div class="muted" style="font-size:12px;margin-bottom:6px">'+esc(cands.candidates.length)+' channel'+(cands.candidates.length===1?'':'s')
        + (cands.how==='link'?' at the link':' for &ldquo;'+esc(cands.query||name)+'&rdquo;')
        + (cands.proposed?' &mdash; one proposed: '+esc(cands.why||''):' &mdash; '+esc(cands.why||''))+'</div>';
      h+='<ul class="plain" style="margin:0;padding:0;list-style:none">';
      cands.candidates.forEach(function(c){
        var prop=cands.proposed&&c.channel_id===cands.proposed;
        h+='<li style="padding:6px 0;border-top:1px solid #eef1f5'+(prop?';background:#f3faf6':'')+'">'
          + '<b>'+esc(c.title||c.channel_id)+'</b>'+(prop?' <span class="pill ok">proposed</span>':'')
          + (c.name_match?' <span class="pill ok">their exact name</span>':'')
          + '<div class="muted" style="font-size:12px">'+(c.handle?esc(c.handle)+' &middot; ':'')
          + (c.subscribers_hidden?'subscribers hidden':ytInt(c.subscribers)+' subscribers')
          + ' &middot; '+ytInt(c.views)+' views &middot; '+ytInt(c.videos)+' videos'
          + (c.url?' &middot; <a href="'+esc(c.url)+'" target="_blank" rel="noopener">Open on YouTube</a>':'')+'</div>'
          + '<button class="btn small" data-act="confirm" data-id="'+esc(c.channel_id)+'">This is their channel</button>'
          + '</li>';
      });
      h+='</ul>';
    }
    h+='<div class="muted" style="font-size:12px;margin-top:8px"><a href="#" data-act="cancel">Cancel</a></div>';
    return h;
  }
  if(st==='no_channel'){
    return '<div class="empty">No YouTube channel has been confirmed for this client.</div>'
      + (d.hint?'<div class="muted" style="font-size:12px;margin-top:6px">Their record links '+esc(d.hint)+' &mdash; Find their channel reads that first.</div>':'')
      + '<div class="row" style="margin-top:8px;gap:6px;align-items:center">'
      + '<input type="text" id="yt-q" placeholder="Paste the channel link or @handle, or add a word to narrow a search" style="flex:1;min-width:120px">'
      + '<button class="btn small" data-act="lookup">Find their channel</button></div>'
      + '<div class="muted" style="font-size:12px;margin-top:6px">A link resolves for one quota unit; a search by name spends a hundred. It proposes a channel only when exactly one matches; you confirm.</div>';
  }
  var recLine='';
  if(rec){
    recLine='<div class="muted" style="font-size:12px">'+esc(rec.title||rec.channel_id||'')+(rec.handle?' &middot; '+esc(rec.handle):'')
      + (rec.url?' &middot; <a href="'+esc(rec.url)+'" target="_blank" rel="noopener">Open on YouTube</a>':'')
      + (rec.confirmed_by?' &middot; confirmed by '+esc(rec.confirmed_by)+(rec.confirmed_at?' on '+esc(String(rec.confirmed_at).slice(0,10)):''):'')
      + '</div>';
  }
  if(st==='no_snapshot'){
    return recLine+'<div class="empty">Confirmed and not read yet. The nightly read will take it, or press Refresh reading.</div>';
  }
  if(st==='unread'){
    return recLine+'<div class="empty">The last read failed: '+esc(d.error||'')+' The channel is still confirmed.</div>';
  }
  h+='<div class="row" style="gap:18px;flex-wrap:wrap">'
    + '<div><div style="font-size:24px;font-weight:600;line-height:1.1">'+(d.subscribers_hidden?'hidden':ytInt(d.subscribers))+'</div><div class="muted" style="font-size:12px">subscribers'+(d.subscribers_hidden?' (the channel hides the count)':'')+'</div></div>'
    + '<div><div style="font-size:24px;font-weight:600;line-height:1.1">'+ytInt(d.views)+'</div><div class="muted" style="font-size:12px">views, all time</div></div>'
    + '<div><div style="font-size:24px;font-weight:600;line-height:1.1">'+ytInt(d.videos)+'</div><div class="muted" style="font-size:12px">videos</div></div>'
    + '</div>';
  var ch=d.change||{};
  if(ch.measured){
    var parts=[];
    if(!d.subscribers_hidden&&ch.subscribers_delta!==null&&ch.subscribers_delta!==undefined) parts.push(ytDelta(ch.subscribers_delta,'subscribers'));
    if(ch.views_delta!==null&&ch.views_delta!==undefined) parts.push(ytDelta(ch.views_delta,'views'));
    if(ch.videos_delta!==null&&ch.videos_delta!==undefined) parts.push(ytDelta(ch.videos_delta,'videos'));
    h+='<div class="muted" style="font-size:12px;margin-top:4px">Over the last '+esc(ch.days)+' days: '+esc(parts.length?parts.join(', '):'not measured')
      + ' (since '+esc(ch.since||'')+').</div>';
  }else if(ch.note){
    h+='<div class="muted" style="font-size:12px;margin-top:4px">'+esc(ch.note)+'</div>';
  }
  h+='<div class="muted" style="font-size:12px;margin-top:4px">Read '+esc(d.as_of||'')+' from YouTube.'+(d.note?' '+esc(d.note):'')+'</div>';
  if(d.staff_note) h+='<div class="muted" style="font-size:12px;color:#a15c00">'+esc(d.staff_note)+'</div>';
  h+=renderYtAnalytics(d.analytics);
  h+=recLine;
  h+='<div class="row" style="margin-top:8px;gap:6px">'
    + '<button class="btn small" data-act="refresh">Refresh reading</button>'
    + '<button class="btn small ghost" data-act="clear">Not this channel</button></div>';
  return h;
}
/* Draws the Analytics half (d.analytics from /api/client/youtube): watch
   time, subscribers gained and traffic sources, read through a
   youtube_studio OAuth connection when one exists for this channel.
   not_connected is the ordinary answer for a channel we do not manage --
   drawn as a quiet staff-only line, never a warning -- and nothing at all
   is drawn once the reading is stripped for the client's own page. */
function renderYtAnalytics(a){
  a=a||{}; var st=a.state||'';
  if(st==='not_connected'){
    return '<div class="muted" style="font-size:12px;margin-top:8px;border-top:1px solid #eef1f5;padding-top:8px">'
      + 'Watch time, subscribers gained and traffic sources need a <a href="/tools/youtube/">YouTube Studio</a> '
      + 'connection for this channel; none exists yet.</div>';
  }
  if(st==='no_reading'){
    return '<div class="muted" style="font-size:12px;margin-top:8px;border-top:1px solid #eef1f5;padding-top:8px">'
      + 'The YouTube Studio connection is live and has not been read yet.</div>';
  }
  if(st==='unread'){
    return '<div class="muted" style="font-size:12px;margin-top:8px;border-top:1px solid #eef1f5;padding-top:8px;color:#a15c00">'
      + 'The last watch-time read failed: '+esc(a.error||a.staff_note||'')+'</div>';
  }
  if(st!=='ok') return '';
  var srcLine=(a.sources||[]).slice(0,3).map(function(s){return esc(s.label||s.source)+' ('+ytInt(s.views)+')';}).join(', ');
  return '<div style="margin-top:8px;border-top:1px solid #eef1f5;padding-top:8px">'
    + '<div class="row" style="gap:18px;flex-wrap:wrap">'
    + '<div><div style="font-size:20px;font-weight:600;line-height:1.1">'+ytInt(a.watch_minutes!==null&&a.watch_minutes!==undefined?Math.round(a.watch_minutes):null)+'</div><div class="muted" style="font-size:12px">minutes watched</div></div>'
    + '<div><div style="font-size:20px;font-weight:600;line-height:1.1">'+(a.subscribers_net===null||a.subscribers_net===undefined?'—':((a.subscribers_net>0?'+':'')+ytInt(a.subscribers_net)))+'</div><div class="muted" style="font-size:12px">subscribers, net</div></div>'
    + '<div><div style="font-size:20px;font-weight:600;line-height:1.1">'+ytInt(a.views)+'</div><div class="muted" style="font-size:12px">views in the period</div></div>'
    + '</div>'
    + (srcLine?'<div class="muted" style="font-size:12px;margin-top:4px">Top traffic sources: '+srcLine+'</div>':'')
    + '<div class="muted" style="font-size:12px;margin-top:4px">'+esc(a.start||'')+' to '+esc(a.end||'')+', read '+esc(a.as_of||'')+' via YouTube Studio.</div>'
    + '</div>';
}
/* ---- end c360 youtube channel ---- */
/* ---- c360 email campaigns (lifted and driven in node by test_suite_email_stats.py) ---- */
/* Draws /api/client/suite-email's payload. Pure: takes the payload and the
   client's name and returns markup. Not linked, not consented, could not
   read, no campaigns and read are drawn apart; every figure is the
   server's reading and nothing here computes one. */
function seInt(v){
  var n=Number(v);
  return (v===null||v===undefined||!isFinite(n))?'&mdash;':n.toLocaleString();
}
function seRate(v){
  return (v===null||v===undefined||!isFinite(Number(v)))?'&mdash;':Number(v).toFixed(1)+'%';
}
function renderSuiteEmail(d,name){
  d=d||{}; var st=d.state||'unread'; var h='';
  var sc=d.scopes||{};
  if(st==='not_linked'){
    return '<div class="empty">No Smart 1 Suite sub-account is linked to this client, so their email campaigns cannot be read. Attach one on the Smart 1 Suite card above.</div>';
  }
  if(st==='no_scope'&&!d.measured){
    return '<div class="empty">'+esc(sc.detail||d.staff_note||'The Hub app has not been consented with the email scopes.')+'</div>';
  }
  if(st==='unread'){
    return '<div class="empty">'+esc(d.error||d.staff_note||'The campaign store could not be read.')+' That is not the same as this client having sent nothing.</div>'
      + '<div class="row" style="margin-top:8px;gap:6px"><button class="btn small" data-act="refresh">Refresh reading</button></div>';
  }
  if(st==='no_snapshot'){
    return '<div class="empty">Linked and not read yet. The nightly read will take it, or press Refresh reading.</div>'
      + '<div class="row" style="margin-top:8px;gap:6px"><button class="btn small" data-act="refresh">Refresh reading</button></div>';
  }
  if(st==='empty'){
    h+='<div class="empty">The sub-account has no sent email campaign as of '+esc(d.as_of||'')+'.</div>';
  }else{
    var t=(d.totals||{})['30']||{};
    h+='<div class="row" style="gap:18px;flex-wrap:wrap">'
      + '<div><div style="font-size:24px;font-weight:600;line-height:1.1">'+seInt(t.campaigns)+'</div><div class="muted" style="font-size:12px">campaigns, last 30 days</div></div>'
      + '<div><div style="font-size:24px;font-weight:600;line-height:1.1">'+seInt(t.delivered||t.sent)+'</div><div class="muted" style="font-size:12px">delivered</div></div>'
      + '<div><div style="font-size:24px;font-weight:600;line-height:1.1">'+seRate(t.open_rate)+'</div><div class="muted" style="font-size:12px">opened</div></div>'
      + '<div><div style="font-size:24px;font-weight:600;line-height:1.1">'+seRate(t.click_rate)+'</div><div class="muted" style="font-size:12px">clicked</div></div>'
      + '</div>';
    if(t.campaigns&&t.measured<t.campaigns){
      h+='<div class="muted" style="font-size:12px;margin-top:4px">'+esc(t.campaigns-t.measured)+' of those campaign'+((t.campaigns-t.measured)===1?'':'s')+' came back without counts; the totals are over the rest.</div>';
    }
    var rows=(d.campaigns||[]).slice(0,10);
    h+='<table style="margin-top:8px;font-size:12.5px"><thead><tr><th>Campaign</th><th>Sent</th><th style="text-align:right">Delivered</th><th style="text-align:right">Opened</th><th style="text-align:right">Clicked</th><th style="text-align:right">Bounced</th><th style="text-align:right">Unsub.</th></tr></thead><tbody>';
    rows.forEach(function(c){
      h+='<tr><td><b>'+esc(c.name||'')+'</b>'+(c.subject?'<div class="muted" style="font-size:11.5px">'+esc(c.subject)+'</div>':'')+'</td>'
        + '<td>'+esc(String(c.sent_at||'').slice(0,10))+'</td>'
        + (c.has_stats
          ? '<td style="text-align:right">'+seInt(c.delivered!==null&&c.delivered!==undefined?c.delivered:c.sent)+'</td><td style="text-align:right">'+seInt(c.opened)+(c.open_rate!==null&&c.open_rate!==undefined?' <span class="muted">('+seRate(c.open_rate)+')</span>':'')+'</td><td style="text-align:right">'+seInt(c.clicked)+(c.click_rate!==null&&c.click_rate!==undefined?' <span class="muted">('+seRate(c.click_rate)+')</span>':'')+'</td><td style="text-align:right">'+seInt(c.bounced)+'</td><td style="text-align:right">'+seInt(c.unsubscribed)+'</td>'
          : '<td class="muted" colspan="5">no counts came back for this campaign</td>')
        + '</tr>';
    });
    h+='</tbody></table>';
    if((d.campaigns||[]).length>rows.length) h+='<div class="muted" style="font-size:12px;margin-top:4px">'+esc((d.campaigns||[]).length-rows.length)+' older campaign(s) not shown.</div>';
  }
  h+='<div class="muted" style="font-size:12px;margin-top:4px">Read '+esc(d.as_of||'')+' from the client&rsquo;s Smart 1 Suite sub-account.'+(d.note?' '+esc(d.note):'')+'</div>';
  (d.notes||[]).forEach(function(n){h+='<div class="muted" style="font-size:12px">'+esc(n)+'</div>';});
  if(st==='no_scope') h+='<div class="muted" style="font-size:12px;color:#a15c00">'+esc(sc.detail||'')+'</div>';
  else if(d.staff_note&&st!=='empty') h+='<div class="muted" style="font-size:12px;color:#a15c00">'+esc(d.staff_note)+'</div>';
  h+='<div class="row" style="margin-top:8px;gap:6px"><button class="btn small" data-act="refresh">Refresh reading</button>'
    + ((st==='ok')?'<button class="btn small" data-act="raw">Show raw statistics</button>':'')
    + '</div><div id="c-suite-email-raw"></div>';
  return h;
}
/* Draws /api/client/suite-email?raw=1. The Suite's own statistics object
   beside what the module made of it, so a mapping is fixed from what was
   sent rather than by re-pulling (docs/claude/60). Staff only: the route is
   behind _require_api() and a client's page is built by public_view(). */
function renderSuiteEmailRaw(d){
  d=d||{};
  if(!d.measured) return '<div class="muted" style="font-size:12px;margin-top:8px">'+esc(d.staff_note||'There is no reading to show raw statistics from.')+'</div>';
  var camps=d.campaigns||[];
  if(!camps.length) return '<div class="muted" style="font-size:12px;margin-top:8px">The reading carried no campaign to show statistics for.</div>';
  var h='<div style="margin-top:10px;border-top:1px solid #eef1f5;padding-top:8px">'
    + '<div class="muted" style="font-size:12px">What the sub-account sent on '+esc(d.as_of||'')+', beside what the Hub read from it. A count under <b>unclaimed</b> is a key no spelling in _COUNT_KEYS matches yet.</div>';
  camps.forEach(function(c){
    h+='<div style="margin-top:8px"><b style="font-size:12.5px">'+esc(c.name||'')+'</b>'
      + '<span class="muted" style="font-size:11.5px"> &middot; '+esc(String(c.sent_at||'').slice(0,10))+(c.stats_source?' &middot; from the '+esc(c.stats_source):'')+'</span>';
    var res=c.resolved||{}, names=Object.keys(res);
    if(names.length){
      h+='<div class="muted" style="font-size:11.5px">read: '+names.map(function(k){
        return esc(k)+(res[k]?'&nbsp;&larr;&nbsp;'+esc(res[k]):'&nbsp;(off the row)');
      }).join(', ')+'</div>';
    }
    if((c.missing||[]).length) h+='<div class="muted" style="font-size:11.5px;color:#a15c00">no value: '+esc((c.missing||[]).join(', '))+'</div>';
    if((c.unmapped||[]).length) h+='<div style="font-size:11.5px;color:#b3261e">unclaimed: '+esc((c.unmapped||[]).join(', '))+'</div>';
    h+='<pre style="font-size:11px;background:#f7f8fa;border-radius:4px;padding:6px;overflow:auto;margin:4px 0 0">'+esc(JSON.stringify(c.raw_stats||{},null,2))+'</pre></div>';
  });
  return h+'</div>';
}
/* ---- end c360 email campaigns ---- */
function loadSuiteEmail(name){
  var box=document.getElementById('c-suite-email');
  if(!box) return;
  if(!name){box.innerHTML='<div class="empty">No client selected.</div>';return;}
  fetch('/api/client/suite-email?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      box=document.getElementById('c-suite-email');
      if(!box) return;
      box.innerHTML=renderSuiteEmail(d,name);
      box.onclick=function(e){seAct(e,name);};
    })
    .catch(function(){
      var b=document.getElementById('c-suite-email');
      if(b) b.innerHTML='<div class="empty">The campaign store could not be read. That is not the same as this client having sent nothing.</div>';
    });
}
function seAct(e,name){
  var t=e.target.closest('[data-act]'); if(!t) return;
  e.preventDefault();
  var box=document.getElementById('c-suite-email'); if(!box) return;
  var wait=(window.S1Think&&S1Think.attach)?S1Think.attach(t,{kind:'scan'}):{done:function(){}};
  if(t.getAttribute('data-act')==='refresh'){
    plPost('/api/client/suite-email/refresh',{name:name}).then(function(r){
      wait.done();
      if(r&&r.ok===false&&r.error){ loadSuiteEmail(name); var b=document.getElementById('c-suite-email'); if(b) b.insertAdjacentHTML('afterbegin','<div class="muted" style="font-size:12px;color:#a15c00">'+esc(r.error)+'</div>'); return; }
      loadSuiteEmail(name);
    }).catch(function(){wait.done();loadSuiteEmail(name);});
  }
  if(t.getAttribute('data-act')==='raw'){
    fetch('/api/client/suite-email?raw=1&name='+encodeURIComponent(name),{credentials:'same-origin'})
      .then(function(r){return r.json();})
      .then(function(d){
        wait.done();
        var b=document.getElementById('c-suite-email-raw');
        if(b) b.innerHTML=renderSuiteEmailRaw(d&&d.raw);
      })
      .catch(function(){
        wait.done();
        var b=document.getElementById('c-suite-email-raw');
        if(b) b.innerHTML='<div class="muted" style="font-size:12px;margin-top:8px">The raw statistics could not be read.</div>';
      });
  }
}
function loadYouTube(name){
  var box=document.getElementById('c-youtube');
  if(!box) return;
  if(!name){box.innerHTML='<div class="empty">No client selected.</div>';return;}
  fetch('/api/client/youtube?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      box=document.getElementById('c-youtube');
      if(!box) return;
      box.innerHTML=renderYouTube(d,name,null);
      box.onclick=function(e){ytAct(e,name);};
    })
    .catch(function(){
      var b=document.getElementById('c-youtube');
      if(b) b.innerHTML='<div class="empty">The channel store could not be read. That is not the same as this client having no channel.</div>';
    });
}
function ytAct(e,name){
  var t=e.target.closest('[data-act]'); if(!t) return;
  e.preventDefault();
  var act=t.getAttribute('data-act');
  var box=document.getElementById('c-youtube'); if(!box) return;
  var wait=(window.S1Think&&S1Think.attach)?S1Think.attach(t,{kind:'scan'}):{done:function(){}};
  if(act==='cancel'){wait.done();loadYouTube(name);return;}
  if(act==='lookup'){
    var q=(document.getElementById('yt-q')||{}).value||'';
    plPost('/api/client/youtube/lookup',{name:name,query:q}).then(function(c){
      wait.done(); box.innerHTML=renderYouTube({state:'no_channel'},name,c);
    }).catch(function(){wait.done();box.innerHTML='<div class="empty">The lookup could not be made.</div>';});
    return;
  }
  if(act==='confirm'){
    plPost('/api/client/youtube/confirm',{name:name,channel_id:t.getAttribute('data-id')}).then(function(){wait.done();loadYouTube(name);})
      .catch(function(){wait.done();loadYouTube(name);});
    return;
  }
  if(act==='refresh'){
    plPost('/api/client/youtube/refresh',{name:name}).then(function(){wait.done();loadYouTube(name);})
      .catch(function(){wait.done();loadYouTube(name);});
    return;
  }
  if(act==='clear'){
    if(!confirm('Clear the confirmed YouTube channel for '+name+'? The readings are kept; nothing draws them until a channel is confirmed again.')){wait.done();return;}
    plPost('/api/client/youtube/clear',{name:name}).then(function(){wait.done();loadYouTube(name);})
      .catch(function(){wait.done();loadYouTube(name);});
  }
}
var _plCands=null;
function loadPlaces(name){
  var box=document.getElementById('c-places');
  if(!box) return;
  if(!name){box.innerHTML='<div class="empty">No client selected.</div>';return;}
  _plCands=null;
  fetch('/api/client/places?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      box=document.getElementById('c-places');
      if(!box) return;
      box.innerHTML=renderPlaces(d,name,null);
      box.onclick=function(e){plAct(e,name);};
    })
    .catch(function(){
      var b=document.getElementById('c-places');
      if(b) b.innerHTML='<div class="empty">The listing store could not be read. That is not the same as this client having no listing.</div>';
    });
}
function plPost(path,body){
  return fetch(path,{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(function(r){return r.json();});
}
function plAct(e,name){
  var t=e.target.closest('[data-act]'); if(!t) return;
  e.preventDefault();
  var act=t.getAttribute('data-act');
  var box=document.getElementById('c-places'); if(!box) return;
  var wait=(window.S1Think&&S1Think.attach)?S1Think.attach(t,{kind:'scan'}):{done:function(){}};
  if(act==='cancel'){wait.done();loadPlaces(name);return;}
  if(act==='lookup'){
    var q=(document.getElementById('pl-q')||{}).value||'';
    plPost('/api/client/places/lookup',{name:name,query:q}).then(function(c){
      wait.done(); box.innerHTML=renderPlaces({state:'no_place'},name,c);
    }).catch(function(){wait.done();box.innerHTML='<div class="empty">The lookup could not be made.</div>';});
    return;
  }
  if(act==='confirm'){
    plPost('/api/client/places/confirm',{name:name,place_id:t.getAttribute('data-id')}).then(function(){wait.done();loadPlaces(name);})
      .catch(function(){wait.done();loadPlaces(name);});
    return;
  }
  if(act==='refresh'){
    plPost('/api/client/places/refresh',{name:name}).then(function(){wait.done();loadPlaces(name);})
      .catch(function(){wait.done();loadPlaces(name);});
    return;
  }
  if(act==='clear'){
    if(!confirm('Clear the confirmed Google listing for '+name+'? The readings are kept; nothing draws them until a listing is confirmed again.')){wait.done();return;}
    plPost('/api/client/places/clear',{name:name}).then(function(){wait.done();loadPlaces(name);})
      .catch(function(){wait.done();loadPlaces(name);});
  }
}
function loadAdPerformance(name){
  var box=document.getElementById('c-adperf');
  if(!box) return;
  if(!name){box.innerHTML='<div class="empty">No client selected.</div>';return;}
  fetch('/api/client/ad-performance?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      box=document.getElementById('c-adperf');
      if(box) box.innerHTML=renderAdPerformance(d,name);
    })
    .catch(function(){
      var b=document.getElementById('c-adperf');
      if(b) b.innerHTML='<div class="empty">The ad-performance store could not be read. '
        + 'That is not the same as no campaign running for this client.</div>';
    });
}
function loadLanding(name){
  var box=document.getElementById('c-landing');
  if(!box) return;
  if(!name){box.innerHTML='<div class="empty">No client selected.</div>';return;}
  fetch('/api/client/landing-pages?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      box=document.getElementById('c-landing');
      if(box) box.innerHTML=renderLandingPages(d,name);
    })
    .catch(function(){
      var b=document.getElementById('c-landing');
      if(b) b.innerHTML='<div class="empty">The landing pages could not be read. '
        + 'That is not the same as this client having none.</div>';
    });
}
/* ---- pipeline (lifted and driven in node by test_client360_pipeline.py) ---- */
function pipeMoney(n){
  n=Number(n)||0;
  return '$'+(n>=1000?Math.round(n).toLocaleString('en-US'):n.toFixed(n%1?2:0));
}
function pipeAgo(iso){
  if(!iso) return '';
  const d=(Date.now()-Date.parse(iso))/86400000;
  if(!(d>=0)) return '';
  return d<1?'today':d<2?'yesterday':Math.floor(d)+' days ago';
}
function renderPipeline(d){
  d=d||{};
  if(d.state==='not_connected')
    return '<div class="empty">'+esc(d.detail||'This client has no Smart 1 Suite sub-account attached.')+'<div class="muted" style="font-size:12px;margin-top:6px">Attach one in the Smart 1 Suite Account card and this fills in.</div></div>';
  if(d.state!=='connected'||d.measured===false)
    return '<div class="empty">'+esc(d.detail||'The pipeline could not be read.')+'</div>';
  const t=d.totals||{}, pipes=d.pipelines||[], recent=d.recent||[];
  if(d.no_pipelines&&!t.all)
    return '<div class="empty">Their sub-account has no pipeline set up yet.<div class="muted" style="font-size:12px;margin-top:6px">Leads land in a pipeline; without one there is nothing to count. Set one up in Smart 1 Suite.</div></div>';
  let h='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(105px,1fr));gap:14px;margin-bottom:12px">';
  h+=scStat('Open leads',t.open||0,d.suite_url);
  h+=scStat('Open value',pipeMoney(t.open_value),d.suite_url);
  h+=scStat('New this week',t.new_week||0,d.suite_url);
  h+=scStat('Won, 30 days',(t.won_month||0)+(t.won_value_month?' · '+pipeMoney(t.won_value_month):''),d.suite_url);
  h+=scStat('Going cold',t.stale||0,d.suite_url,true);
  h+='</div>';
  h+='<div class="muted" style="font-size:12px;margin-bottom:10px">'+(t.new_month||0)+' new in the last 30 days · '+(t.lost_month||0)+' lost'
    +(t.stale?' · <b>'+t.stale+'</b> open with no activity in '+(d.stale_days||30)+' days':'')+'</div>';
  const shown=pipes.filter(p=>p.open>0);
  if(shown.length){
    h+=shown.map(p=>{
      const max=Math.max.apply(null,p.stages.map(s=>s.count).concat([1]));
      return '<div style="margin-bottom:10px"><div style="font-size:12.5px;font-weight:600;margin-bottom:4px">'+esc(p.name)+' <span class="muted" style="font-weight:400">· '+p.open+' open · '+pipeMoney(p.value)+'</span></div>'
        +'<table style="width:100%;border-collapse:collapse;font-size:12.5px">'+p.stages.map(s=>
          '<tr><td style="padding:2px 8px 2px 0;white-space:nowrap;width:1%">'+esc(s.name)+'</td>'
          +'<td style="padding:2px 0"><div style="background:var(--line-soft,#e5e7eb);border-radius:4px;height:10px;overflow:hidden"><div style="width:'+Math.round(100*s.count/max)+'%;height:100%;background:var(--brand,#2563eb);min-width:'+(s.count?'3px':'0')+'"></div></div></td>'
          +'<td style="padding:2px 0 2px 8px;text-align:right;white-space:nowrap" class="muted">'+s.count+(s.value?' · '+pipeMoney(s.value):'')+'</td></tr>').join('')+'</table></div>';
    }).join('');
  }
  const idle=pipes.filter(p=>!(p.open>0));
  if(idle.length) h+='<div class="muted" style="font-size:12.5px;margin-bottom:10px">Nothing open in '+idle.map(p=>esc(p.name)).join(', ')+'.</div>';
  if(t.unstaged) h+='<div class="muted" style="font-size:11.5px;margin-bottom:8px">'+t.unstaged+' open lead'+(t.unstaged===1?' is':'s are')+' in a stage that no longer exists.</div>';
  if(recent.length){
    const cls={open:'info',won:'ok',lost:'err',abandoned:'neutral'};
    h+='<div style="font-size:12.5px;font-weight:600;margin:6px 0 4px">Newest leads</div><table style="width:100%;border-collapse:collapse;font-size:12.5px">'
      +recent.map(r=>'<tr><td style="padding:3px 8px 3px 0"><b>'+esc(r.name)+'</b>'+(r.contact&&r.contact!==r.name?' <span class="muted">· '+esc(r.contact)+'</span>':'')
        +(r.stage?'<div class="muted" style="font-size:11.5px">'+esc(r.stage)+(r.source?' · '+esc(r.source):'')+'</div>':'')+'</td>'
        +'<td style="padding:3px 8px;white-space:nowrap"><span class="pill '+(cls[r.status]||'neutral')+'" style="font-size:10.5px">'+esc(r.status)+'</span></td>'
        +'<td style="padding:3px 0;text-align:right;white-space:nowrap" class="muted">'+(r.value?pipeMoney(r.value)+' · ':'')+esc(pipeAgo(r.created))+'</td></tr>').join('')+'</table>';
  }
  if(d.truncated) h+='<div class="muted" style="font-size:11.5px;margin-top:8px">Only the newest 1,000 leads were counted.</div>';
  return h;
}
/* ---- end pipeline ---- */
function loadPipeline(name){
  const el=$('c-pipeline'); if(!el) return;
  const gen=c360Generation;
  fetch('/api/client/pipeline?name='+encodeURIComponent(name)+'&url='+encodeURIComponent(window.__c360domain||''),{credentials:'same-origin'})
    .then(r=>r.json()).then(d=>{
      if(gen!==c360Generation) return;
      el.innerHTML=renderPipeline(d);
      const o=$('c-pipeline-open'); if(o&&d&&d.suite_url){o.href=d.suite_url;o.style.display='';}
    })
    .catch(()=>{ if(gen!==c360Generation) return; el.innerHTML=renderPipeline({state:'not_measured',measured:false,detail:'The pipeline could not be read.'}); });
}
function loadOrders(name){
  var box=document.getElementById('c-orders');
  if(!box) return;
  if(!name){box.innerHTML='<div class="empty">No client selected.</div>';return;}
  fetch('/api/client/orders?name='+encodeURIComponent(name),{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      box=document.getElementById('c-orders');
      if(!box) return;
      if(d && d.measured===false){
        box.innerHTML='<div class="empty">The order records could not be read'
          + (d.error?' &mdash; '+esc(d.error):'') + '. That is not the same as '
          + 'no orders having been sent.</div>';
        return;
      }
      var rows=(d&&d.orders)||[];
      if(!rows.length){
        box.innerHTML='<div class="empty">No insertion order has been sent for '
          + 'this client from the IO Builder. Orders sent before the Hub began '
          + 'keeping its own record are not here.</div>';
        return;
      }
      /* Only claim "no campaign yet" when the products card actually
         answered. An empty Set is a real answer — a client with no products
         is exactly the case this card exists for — but a missing one means
         we never looked, and absent data must not read as a finding. */
      var known=(window.__c360ioNums instanceof Set)?window.__c360ioNums:null;
      var h='<table><thead><tr><th>Order</th><th>Flight</th><th>Monthly</th>'
        + '<th>Sent</th><th>Documents</th></tr></thead><tbody>';
      rows.forEach(function(o){
        var key=String(o.order||'').replace(/[^0-9a-z]+/gi,'').toLowerCase();
        var flags='';
        if(key && known && !known.has(key))
          flags+=' <span class="pill warn" title="Nothing in Knack carries this '
            + 'order number yet">no campaign yet</span>';
        if(o.suite && o.suite.ever_delivered===false)
          flags+=' <span class="pill bad" title="The order was built but Smart 1 '
            + 'Suite never took it">not in Suite</span>';
        if(o.resubmitted)
          flags+=' <span class="pill neutral" title="This order was submitted '
            + 'more than once">revised</span>';
        var docs=[];
        if(o.client_pdf) docs.push('<a href="'+esc(o.client_pdf)+'" target="_blank" rel="noopener">Client</a>');
        if(o.internal_pdf) docs.push('<a href="'+esc(o.internal_pdf)+'" target="_blank" rel="noopener">Internal</a>');
        h+='<tr><td><b>'+esc(o.order||'—')+'</b>'+flags
          + (o.member&&o.member!==name?memberTag(o.member):'')
          + (o.partner?'<div class="muted" style="font-size:12px">'+esc(o.partner)+'</div>':'')
          + '</td><td class="muted" style="font-size:12.5px">'
          + esc([o.start,o.end].filter(Boolean).join(' – ')||'—')+'</td><td>'
          + money(o.monthly)+'</td><td class="muted" style="font-size:12.5px">'
          + esc(String(o.submitted_at||'').slice(0,10)||'—')
          + (o.submitted_by?'<div>'+esc(o.submitted_by)+'</div>':'')
          + '</td><td>'+(docs.join(' &middot; ')||'<span class="muted">—</span>')
          + '</td></tr>';
      });
      h+='</tbody></table>';
      box.innerHTML=h;
    })
    .catch(function(){
      var b=document.getElementById('c-orders');
      if(b) b.innerHTML='<div class="empty">The order records could not be '
        + 'read. That is not the same as no orders having been sent.</div>';
    });
}
