/* All text from clients and YouTube is escaped before rendering. */
(() => {
  'use strict';
  const root=document.getElementById('ytStudio');
  if(!root)return;
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let current=null, client='', selectedVideo=null, pending=0, searchTimer;
  let csrf=root.dataset.csrf;
  $('channelUrl').value=new URLSearchParams(location.search).get('channel')||'';
  let messageVersion=0;
  function message(text,error=false){messageVersion++;$('ytMessage').textContent=text;$('ytMessage').className=error?'error':'';}
  async function api(path,data,form=false){
    const options=data?{method:'POST',headers:{'X-YouTube-CSRF':csrf},body:data}:{};
    if(data&&!form){options.headers['Content-Type']='application/json';options.body=JSON.stringify({client,...data});}
    const response=await fetch('/tools/youtube/api/'+path,options);
    let result;try{result=await response.json();}catch(_){throw new Error('The server did not return a result. Refresh the page and check status before retrying.');}
    if(!response.ok||result.ok===false)throw new Error(result.error||'The request could not be completed.');
    return result;
  }
  async function run(button,fn){
    pending++;$('clientName').disabled=true;$('clientForm').querySelector('button').disabled=true;
    if(button)button.disabled=true;
    message('Working…');
    const startedMessage=messageVersion;
    const indicator=window.S1Think?.attach?.($('ytMessage'),{kind:'wait'});
    try{await fn();if(messageVersion===startedMessage)message('Saved.');}
    catch(error){message(error.message,true);}
    finally{indicator?.done?.();if(button)button.disabled=false;pending--;$('clientName').disabled=!!pending;$('clientForm').querySelector('button').disabled=!!pending;}
  }
  function needClient(){if(!client)throw new Error('Open a client first.');}
  function tab(id){root.querySelectorAll('[data-section]').forEach(e=>e.hidden=e.dataset.section!==id);root.querySelectorAll('[data-panel]').forEach(e=>e.setAttribute('aria-pressed',String(e.dataset.panel===id)));}
  root.querySelectorAll('[data-panel]').forEach(button=>button.onclick=()=>tab(button.dataset.panel));
  async function load(){
    const name=$('clientName').value.trim();if(!name)throw new Error('Enter a client name.');
    const data=await api('client?client='+encodeURIComponent(name));
    client=name;current=data.client;csrf=data.csrf;
    $('client360Link').href='/client360?q='+encodeURIComponent(client);
    const url=new URL(location.href);url.searchParams.set('client',client);history.replaceState(null,'',url);
    const config=data.configuration;
    $('ytSetup').hidden=config.connect_ready&&config.public_lookup_ready;
    $('ytSetup').textContent=[!config.connect_ready?'Connection setup needed: '+config.missing.join(', ')+'.':'',!config.public_lookup_ready?'Public channel search needs YOUTUBE_API_KEY. Owner connection can still be used when configured.':''].filter(Boolean).join(' ');
    $('connectNew').disabled=!config.connect_ready;
    render();
  }
  function render(){
    $('draftChannel').innerHTML='<option value="">Choose a channel</option>'+current.channels.map(c=>`<option value="${esc(c.id)}">${esc(c.title)}</option>`).join('');
    if(current.channels.length===1)$('draftChannel').value=current.channels[0].id;
    $('channelList').innerHTML=current.channels.length?current.channels.map(c=>`<article class="yt-card" data-channel="${esc(c.id)}"><div class="yt-channel-head"><div><h2>${esc(c.title)}<span class="yt-badge ${c.connected?'connected':''}">${c.connection_error?'Reconnect required':c.connected?'Connected for management':'Linked · owner access needed'}</span></h2><a href="${esc(c.url)}" target="_blank" rel="noopener">Open channel ↗</a></div></div><p class="yt-muted">${c.statistics?`${esc(c.statistics.hiddenSubscriberCount?'Subscribers hidden':(c.statistics.subscriberCount??'Unavailable')+' subscribers')} · ${esc(c.statistics.videoCount??'Unavailable')} videos · ${esc(c.statistics.viewCount??'Unavailable')} views`:'Public statistics have not been loaded.'}${c.refreshed_at?' · Refreshed '+esc(new Date(c.refreshed_at*1000).toLocaleString()):''}</p><div class="yt-actions"><button data-action="connect">Create access link</button><button data-action="refresh" class="secondary">Refresh & review</button>${c.connected?'<button data-action="analytics" class="secondary">28-day results</button><button data-action="playlist" class="secondary">Create playlist</button><button data-action="disconnect" class="secondary">Disconnect Hub access</button>':''}</div>${c.review?`<p class="yt-muted">${esc(c.review.note)}</p>${c.review.findings.length?c.review.findings.map(f=>`<div class="yt-review"><b>${esc(f.title)}</b><p>${esc(f.detail)}</p></div>`).join(''):'<p>No issues were found by the limited metadata checks.</p>'}`:''}<details><summary>Recent videos (${(c.videos||[]).length})</summary>${(c.videos||[]).map(v=>`<div class="yt-row"><a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.title)}</a><p class="yt-muted">${esc(v.statistics?.viewCount??'Unavailable')} views</p>${c.connected?`<button data-action="improve" data-video="${esc(v.id)}" class="secondary">Improve video details</button><button data-action="thumbnail" data-video="${esc(v.id)}" class="secondary">Add thumbnail</button><button data-action="captions" data-video="${esc(v.id)}" class="secondary">Add captions</button>`:''}</div>`).join('')}</details></article>`).join(''):'<article class="yt-card"><h2>No channel linked yet</h2><p>Add a public channel or create an owner access link to get started.</p></article>';
    renderDrafts();renderLaunch(current.launch);
  }
  function renderDrafts(){
    $('draftList').innerHTML=current.drafts.length?current.drafts.map(d=>`<article class="yt-card" data-draft="${esc(d.id)}"><h3>${esc(d.title)}<span class="yt-badge">${esc(d.status.replaceAll('_',' '))}</span></h3><p>${esc(d.description)}</p>${d.review_note?`<p class="yt-note">Review: ${esc(d.review_note)}</p>`:''}${d.video_url?`<p><a href="${esc(d.video_url)}" target="_blank" rel="noopener">Check video playback in YouTube ↗</a></p>`:''}${d.publish_at?`<p>Scheduled: ${esc(new Date(d.publish_at).toLocaleString())}</p>`:''}<div class="yt-actions">${['draft','changes_requested'].includes(d.status)?'<button data-draft-action="edit" class="secondary">Edit</button><button data-draft-action="review" class="secondary">Customer review link</button><button data-draft-action="approve">Approve details</button>':''}${d.status==='approved'?'<label>Finished video (up to 256 MB)<input type="file" accept="video/mp4,video/quicktime,video/webm" data-file></label><button data-draft-action="upload">Upload privately</button>':''}${['uploading','upload_uncertain'].includes(d.status)?'<button data-draft-action="status">Check upload status</button>':''}${['uploaded','publishing'].includes(d.status)?'<button data-draft-action="publish">Publish now</button><label>Or schedule (your local time)<input type="datetime-local" data-schedule></label><button data-draft-action="schedule" class="secondary">Schedule</button>':''}</div></article>`).join(''):'<p>No drafts saved for this client.</p>';
  }
  async function invite(channelId='',draftId=''){
    needClient();const data=await api('invite',{channel_id:channelId,draft_id:draftId,kind:draftId?'review':'connect'});
    $('inviteBox').hidden=false;$('inviteUrl').value=data.url;$('inviteNote').textContent='Copy this seven-day link. A new link replaces the previous link for this purpose.';
    tab('channels');$('inviteUrl').focus();$('inviteUrl').select();message('Link created. Copy it to your customer.');
  }
  $('clientForm').onsubmit=e=>{e.preventDefault();run(e.submitter,async()=>{await load();message('Client opened.');});};
  $('clientName').oninput=()=>{clearTimeout(searchTimer);const term=$('clientName').value.trim();if(term.length<2)return;searchTimer=setTimeout(async()=>{try{const r=await fetch('/api/clients/search?q='+encodeURIComponent(term)+'&limit=10');if(!r.ok)return;const d=await r.json();if($('clientName').value.trim()!==term)return;$('ytClients').innerHTML=(d.clients||[]).map(c=>`<option value="${esc(c.name)}">${esc(c.domain||'')}</option>`).join('');}catch(_){}},250);};
  $('addForm').onsubmit=e=>{e.preventDefault();run(e.submitter,async()=>{needClient();await api('channels',{url:$('channelUrl').value});await load();message('Channel added to this client.');});};
  $('searchForm').onsubmit=e=>{e.preventDefault();run(e.submitter,async()=>{needClient();const data=await api('search',{query:$('searchQuery').value});$('searchResults').innerHTML=data.channels.map(c=>`<div class="yt-row"><b>${esc(c.title)}</b><p>${esc(c.description)}</p><a href="${esc(c.url)}" target="_blank" rel="noopener">Check channel ↗</a> <button data-add="${esc(c.id)}">Add to client</button></div>`).join('')||'<p>No channels found.</p>';message('Check the channel identity before adding it.');});};
  $('searchResults').onclick=e=>{const b=e.target.closest('[data-add]');if(b)run(b,async()=>{await api('channels',{url:b.dataset.add});await load();});};
  $('connectNew').onclick=e=>run(e.currentTarget,()=>invite());
  $('channelList').onclick=e=>{const b=e.target.closest('[data-action]');if(!b)return;const cid=b.closest('[data-channel]').dataset.channel;run(b,async()=>{
    if(b.dataset.action==='connect')return invite(cid);
    if(b.dataset.action==='disconnect'){if(!confirm('Remove Smart Hub’s saved access to this channel? The channel and its videos remain on YouTube.'))return;await api('disconnect',{channel_id:cid});await load();return;}
    if(b.dataset.action==='refresh'){await api('refresh',{channel_id:cid});await load();message('Recent videos and metadata review refreshed.');return;}
    if(b.dataset.action==='analytics'){const d=await api('analytics',{channel_id:cid});$('analytics').hidden=false;const cols=d.report.columnHeaders||[];$('analytics').innerHTML=`<h2>Channel results</h2><p>${esc(d.start)} through ${esc(d.end)} · Recent data may be delayed. These are YouTube results, not website leads.</p><div class="yt-table-wrap"><table><thead><tr>${cols.map(c=>`<th>${esc(c.name)}</th>`).join('')}</tr></thead><tbody>${(d.report.rows||[]).map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${!d.report.rows?.length?'<p>No report rows were available for this date range.</p>':''}`;message('Results loaded.');return;}
    if(b.dataset.action==='playlist'){const title=prompt('Playlist name');if(!title)return;await api('playlist',{channel_id:cid,title});message('Private playlist created. Manage its visibility in YouTube Studio.');return;}
    if(b.dataset.action==='improve'){const channel=current.channels.find(c=>c.id===cid);selectedVideo={channel_id:cid,...channel.videos.find(v=>v.id===b.dataset.video)};showVideoEditor();return;}
    if(['thumbnail','captions'].includes(b.dataset.action)){
      const input=document.createElement('input');input.type='file';input.accept=b.dataset.action==='thumbnail'?'.jpg,.jpeg,.png':'.srt,.vtt';
      input.onchange=()=>run(b,async()=>{if(!input.files[0])return;const form=new FormData();form.append('client',client);form.append('channel_id',cid);form.append('video_id',b.dataset.video);form.append('file',input.files[0]);if(b.dataset.action==='captions'){const language=prompt('Caption language code, for example en or es','en');if(!language)return;form.append('language',language);}await api(b.dataset.action,form,true);message(b.dataset.action==='captions'?'Caption track added.':'Thumbnail added.');});input.click();
    }
  });};
  function showVideoEditor(){
    let editor=$('videoEditor');if(!editor){editor=document.createElement('article');editor.id='videoEditor';editor.className='yt-card';$('channelList').append(editor);}
    editor.innerHTML=`<h2>Improve an existing video</h2><p>Review the exact replacement text before applying. The video file remains the same.</p><label>Title<input id="videoTitle" maxlength="100" value="${esc(selectedVideo.title)}"></label><label>Description<textarea id="videoDescription" rows="7">${esc(selectedVideo.description)}</textarea></label><button id="videoSuggest" class="secondary">Suggest improvements</button><button id="videoApply">Apply these details to YouTube</button>`;
    $('videoSuggest').onclick=e=>run(e.currentTarget,async()=>{const d=await api('suggest',{title:$('videoTitle').value,description:$('videoDescription').value});$('videoTitle').value=d.suggestion.title;$('videoDescription').value=d.suggestion.description;message('Suggestions ready for review. Nothing changed on YouTube.');});
    $('videoApply').onclick=e=>run(e.currentTarget,async()=>{if(!confirm('Apply the displayed title and description to this YouTube video?'))return;await api('video-details',{channel_id:selectedVideo.channel_id,video_id:selectedVideo.id,title:$('videoTitle').value,description:$('videoDescription').value,original_title:selectedVideo.title,original_description:selectedVideo.description});await load();message('YouTube video details updated.');});
    editor.scrollIntoView({block:'center'});
    const playlistButton=document.createElement('button');playlistButton.type='button';playlistButton.className='secondary';playlistButton.textContent='Add this video to a playlist';
    playlistButton.onclick=e=>run(e.currentTarget,async()=>{const playlist=prompt('Paste a playlist URL or ID from this channel');if(!playlist)return;const d=await api('playlist-video',{channel_id:selectedVideo.channel_id,video_id:selectedVideo.id,playlist_id:playlist});message(d.message);});editor.append(playlistButton);
  }
  $('draftForm').onsubmit=e=>{e.preventDefault();run(e.submitter,async()=>{needClient();await api('drafts',{id:$('draftId').value,title:$('draftTitle').value,description:$('draftDescription').value,channel_id:$('draftChannel').value,made_for_kids:$('madeForKids').checked,synthetic:$('synthetic').checked});$('draftForm').reset();$('draftId').value='';await load();message('Draft saved. Edits require a new approval.');});};
  $('suggestCopy').onclick=e=>run(e.currentTarget,async()=>{needClient();const d=await api('suggest',{title:$('draftTitle').value,description:$('draftDescription').value});$('draftTitle').value=d.suggestion.title;$('draftDescription').value=d.suggestion.description;$('contentPackage').hidden=false;$('contentPackage').innerHTML='<h3>Content package suggestions</h3><div class="yt-copy">'+esc(d.suggestion.content_package||'')+'</div>';message('Suggested copy is ready to review and save.');});
  $('draftList').onclick=e=>{const b=e.target.closest('[data-draft-action]');if(!b)return;const card=b.closest('[data-draft]');const draft=current.drafts.find(d=>d.id===card.dataset.draft);run(b,async()=>{
    const action=b.dataset.draftAction;
    if(action==='edit'){$('draftId').value=draft.id;$('draftTitle').value=draft.title;$('draftDescription').value=draft.description;$('draftChannel').value=draft.channel_id;$('madeForKids').checked=draft.made_for_kids;$('synthetic').checked=draft.synthetic;$('draftTitle').focus();message('Edit the draft above.');return;}
    if(action==='review')return invite('',draft.id);
    if(action==='approve'){await api('approve',{draft_id:draft.id});await load();return;}
    if(action==='upload'){const file=card.querySelector('[data-file]').files[0];if(!file)throw new Error('Choose the finished video file.');const form=new FormData();form.append('client',client);form.append('draft_id',draft.id);form.append('video',file);message('Uploading privately. Keep this page open; large files can take several minutes.');await api('upload',form,true);await load();message('Video uploaded privately. Check playback before publishing.');return;}
    if(action==='status'){const d=await api('upload-status',{draft_id:draft.id});await load();message(d.message);return;}
    const when=action==='schedule'?card.querySelector('[data-schedule]').value:'';
    if(action==='schedule'&&!when)throw new Error('Choose a scheduled date and time.');
    if(!confirm(action==='schedule'?'Schedule this video to become public at the selected time?':'Make this video public now? Confirm that you checked its playback.'))return;
    const d=await api('publish',{draft_id:draft.id,publish_at:when?new Date(when).toISOString():null});await load();message(d.message);
  });};
  function renderLaunch(plan){if(!plan?.about)return;$('launchServices').value=plan.services||'';$('launchAudience').value=plan.audience||'';$('launchWebsite').value=plan.website||'';$('launchPlan').innerHTML=`<h3>About the channel</h3><div class="yt-copy">${esc(plan.about)}</div><h3>Suggested playlists</h3><ul>${plan.playlists.map(p=>`<li>${esc(p)}</li>`).join('')}</ul><h3>First month</h3>${plan.calendar.map(w=>`<div class="yt-row"><b>Week ${esc(w.week)}: ${esc(w.topic)}</b><p>${esc(w.brief)}</p></div>`).join('')}<h3>Launch checklist</h3><ol>${plan.checklist.map(i=>`<li>${esc(i)}</li>`).join('')}</ol><a href="https://www.youtube.com/create_channel" target="_blank" rel="noopener">Create the channel in YouTube ↗</a>`;}
  $('launchForm').onsubmit=e=>{e.preventDefault();run(e.submitter,async()=>{needClient();const d=await api('launch',{services:$('launchServices').value,audience:$('launchAudience').value,website:$('launchWebsite').value});renderLaunch(d.plan);message('Launch plan saved to this client.');});};
  $('loadOpportunities').onclick=e=>run(e.currentTarget,async()=>{const d=await api('opportunities');$('opportunityList').innerHTML=d.opportunities.map(o=>`<div class="yt-row"><a href="/tools/youtube/?client=${encodeURIComponent(o.client)}">${esc(o.client)}</a><p>${esc(o.action)} · ${esc(o.channel)}</p></div>`).join('')||'<p>No saved opportunities yet. Connect and review client channels to populate this list.</p>';message('Opportunity queue refreshed.');});
  if($('clientName').value)run(null,async()=>{await load();message('Client opened.');});
})();
