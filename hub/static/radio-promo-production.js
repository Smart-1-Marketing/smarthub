/* Radio Ad Creator production controls. Provider work is always an explicit action. */
const DELIVERY_TAGS=['excited','curious','whispers','laughs','sighs','sarcastic','mischievously','short pause','long pause'];
const recordingSlots=new Set();
let recordingAction=false, mixRevision=0;
let productionVoiceProject='', bedShared=false, musicTracks=[], volumeSave=Promise.resolve();
function voiceSettingsNow(){
  return {...(P.voice||{}),voice_id:$('recordVoice').value||P.voice?.voice_id,
    name:$('recordVoice').selectedOptions[0]?.textContent||P.voice?.name,
    model_id:$('voiceMode').value,prompt_strength:Number($('styleStrength').value)/100,
    stability:Number($('voiceStability').value)/100,speed:Number($('voiceSpeed').value)};
}
function voiceLabels(){
  const expressive=$('voiceMode').value==='eleven_v3', stability=$('voiceStability');
  stability.step=expressive?'50':'5'; if(expressive) stability.value=String(Math.round(Number(stability.value)/50)*50);
  $('styleValue').textContent=$('styleStrength').value+'%';
  $('stabilityValue').textContent=expressive?({'0':'Creative','50':'Natural','100':'Robust'}[stability.value]):stability.value+'%';
  $('speedValue').textContent=Number($('voiceSpeed').value).toFixed(2)+'×';
}
function drawVoiceSettings(){
  const voices=[...(P.voice?.voice_id?[P.voice]:[]),...(P.voice_matches||[])];
  const current=$('recordVoice').value||P.voice?.voice_id, unique=voices.filter((v,i)=>voices.findIndex(x=>x.voice_id===v.voice_id)===i);
  $('recordVoice').innerHTML='<option value="">Choose a voice in step 4</option>'+unique.map(v=>`<option value="${esc(v.voice_id)}">${esc(v.name||v.voice_id)}</option>`).join('');
  $('recordVoice').value=current||'';
  if(productionVoiceProject!==P.id){
    const v=P.voice||{}; $('voiceMode').value=v.model_id||'eleven_multilingual_v2';
    $('styleStrength').value=(v.prompt_strength??.55)*100; $('voiceStability').value=(v.stability??.5)*100; $('voiceSpeed').value=v.speed??1;
    productionVoiceProject=P.id;
  }
  voiceLabels();
}
async function selectVoice(v,navigate=false){
  try{
    const d=await api('/api/projects/'+P.id+'/voice',{method:'POST',body:JSON.stringify({...P.voice,voice_id:v.voice_id,name:v.name})});
    P=d.project; assigned={}; productionVoiceProject=''; drawVoiceSettings(); $('recordVoice').value=v.voice_id;
    if(navigate) renderBooth();
  }catch(e){say('castMsg',e.message,'err');throw e;}
}
function chooseRecordingVoice(){ assigned={}; }
async function persistVoiceSettings(){
  const d=await api('/api/projects/'+P.id+'/voice',{method:'POST',body:JSON.stringify(voiceSettingsNow())}); P.voice=d.project.voice;
}
async function saveVoiceSettings(){
  try{await persistVoiceSettings();assigned={};say('voiceSettingsMsg','Voice settings saved for all scripts.','ok');}
  catch(e){say('voiceSettingsMsg',e.message,'err');}
}
async function previewVoice(){
  const btn=$('previewVoice'); if(btn.disabled)return; btn.disabled=true;
  say('voiceSettingsMsg','Generating voice sample…','ai');
  try{
    const d=await api('/api/projects/'+P.id+'/voice/preview',{method:'POST',body:JSON.stringify({...voiceSettingsNow(),text:$('voiceSample').value})});
    $('voiceSamplePlayer').innerHTML=`<div class="clip"><audio controls src="data:audio/mpeg;base64,${d.audio_base64}"></audio><span>${d.seconds==null?'Duration unavailable':Number(d.seconds).toFixed(1)+'s'+(d.measured?'':' estimated')}</span></div>`;
    say('voiceSettingsMsg','Sample ready.','ok');
  }catch(e){say('voiceSettingsMsg',e.message,'err');}finally{btn.disabled=false;}
}
function insertDeliveryTag(k){
  const box=$('recordScript-'+k); box.setRangeText('['+$('deliveryTag-'+k).value+'] ',box.selectionStart,box.selectionEnd,'end');
  $('voiceMode').value='eleven_v3';voiceLabels();box.focus();
}
async function saveRecordingScript(k,quiet=false){
  try{
  const script=$('recordScript-'+k).value;
  if(script===(P.scripts?.[k]?.script||''))return;
  const d=await api('/api/projects/'+P.id+'/script/edit',{method:'POST',body:JSON.stringify({slot:k,script})});
  P=d.project;discardMixes();say('boothMsg','Script saved.','ok');
  }catch(e){say('boothMsg',e.message,'err');if(quiet)throw e;}
}
async function recordScript(k){
  if(recordingAction)return;recordingAction=true;
  try{await saveRecordingScript(k,true);await persistVoiceSettings();assigned={};await render(k);}
  catch(e){say('boothMsg',e.message,'err');}
  finally{recordingAction=false;}
}
function timingBadge(seconds,target,measured){
  if(seconds==null||!Number.isFinite(Number(seconds)))return '<span class="timing">Duration unavailable</span>';
  const value=Number(seconds),gap=Number(target)-value,color=gap<0||gap>4?'bad':gap>=2?'warn':'ok';
  return `<span class="timing ${measured?color:''}">${value.toFixed(1)}s / ${Number(target)}s${measured?'':' estimated'} · ${Math.abs(gap).toFixed(1)}s ${gap<0?'over':'short'}</span>`;
}
function selectedMixLevel(){return (MIXCFG?.levels||[]).find(l=>l.label===(P?.mix_level||MIXCFG?.level_reference))||MIXCFG?.levels?.[0]||{};}
function discardMixes(){mixRevision++;Object.keys(RENDERED).forEach(k=>{if(RENDERED[k]?.url)URL.revokeObjectURL(RENDERED[k].url);delete RENDERED[k];});}
function changeBedVolume(el){
  const level=MIXCFG.levels[Number(el.value)];P.mix_level=level.label;discardMixes();
  $('bedLevelLabel').textContent=level.label+' · '+level.ducked_db+' dB';renderMixPanel();
  volumeSave=volumeSave.catch(()=>{}).then(()=>api('/api/projects/'+P.id+'/mix-settings',{method:'POST',body:JSON.stringify({level:level.label})})).catch(e=>say('musicMsg',e.message,'err'));
}
function closeBedDialog(){ $('bedDialog').querySelectorAll('audio').forEach(a=>a.pause());$('bedDialog').close(); }
async function openBedDialog(slot,shared){
  bedShared=shared;
  $('bedDialogBody').innerHTML=`<h3>Music bed — ${shared?'all eligible commercials':slotName(slot)}</h3>
    <label class="f" for="bedSearch">Search saved music</label><input type="search" id="bedSearch" oninput="filterMusicTracks()">
    <select id="bedLibraryChoice" aria-label="Saved music bed" onchange="auditionBed()"></select><div id="bedAudition"></div><button class="btn sec" onclick="applyLibraryBed('${slot}')">Use selected bed${shared?' across eligible commercials':' for this spot'}</button>
    <h4>Compose or upload</h4><label class="f" for="bedSaveName">Bed name</label><input id="bedSaveName" type="text" maxlength="80" value="${esc(P.company||'Radio')} — ${slotName(slot)} bed">
    <label><input id="bedSaveLibrary" type="checkbox" checked> Save to music library</label>
    <div class="rowbar">${(MIXCFG?.moods||[]).map(m=>`<button class="btn sec sm" onclick="composeBed('${slot}','${esc(m.id)}')" ${MIXCFG.can_compose?'':'disabled'}>${esc(m.label)}</button>`).join('')}</div>
    <label class="f" for="bedPrompt-${slot}">Describe a new music bed</label><input id="bedPrompt-${slot}" type="text"><button class="btn" onclick="composeBed('${slot}')" ${MIXCFG?.can_compose?'':'disabled'}>Compose</button>
    <label class="btn sec">Upload a track<input type="file" accept="audio/*" onchange="uploadBed('${slot}',this)"></label>
    <button class="btn sec" onclick="clearBed('${slot}')">No bed — straight read</button><span class="msg" id="bedMsg-${slot}" role="status"></span>`;
  $('bedDialog').showModal();
  try{musicTracks=(await api('/api/music-library')).tracks;filterMusicTracks();}
  catch(e){say('bedMsg-'+slot,e.message,'err');}
}
function filterMusicTracks(){
  const q=$('bedSearch').value.trim().toLowerCase();
  $('bedLibraryChoice').innerHTML='<option value="">Choose a saved bed</option>'+musicTracks.filter(t=>t.name.toLowerCase().includes(q)).map(t=>`<option value="${esc(t.id)}">${esc(t.name)} — ${t.seconds==null?'?':Number(t.seconds).toFixed(1)}s${t.estimated?' estimated':''}</option>`).join('');$('bedAudition').replaceChildren();
}
function auditionBed(){
  const track=musicTracks.find(t=>t.id===$('bedLibraryChoice').value);$('bedAudition').replaceChildren();
  if(!track)return;const player=document.createElement('audio');player.controls=true;player.preload='none';player.src=track.audio_url;$('bedAudition').append(player);
}
async function applyLibraryBed(slot){
  const id=$('bedLibraryChoice').value;if(!id)return;
  try{const d=await api('/api/projects/'+P.id+'/music-library/'+encodeURIComponent(id)+'/apply',{method:'POST',body:JSON.stringify(bedShared?{}:{slot})});P=d.project;closeBedDialog();discardMixes();renderMusic();await loadQc();renderMixPanel();say('musicMsg','Music bed applied to '+d.applied.map(slotName).join(', ')+'.','ok');}
  catch(e){say('bedMsg-'+slot,e.message,'err');}
}
async function bedChanged(slot){
  const across=bedShared||confirm('Use this bed across all commercials it is long enough for?');
  if(across){const d=await api('/api/projects/'+P.id+'/bed/reuse',{method:'POST',body:JSON.stringify({source:slot})});P=d.project;}
  closeBedDialog();discardMixes();renderMusic();await loadQc();renderMixPanel();say('musicMsg','Music bed saved and applied.','ok');
}
async function clearCustomerComments(){
  try{const d=await api('/api/projects/'+P.id+'/comments/clear',{method:'POST',body:'{}'});P=d.project;$('customerComments').textContent='Previous comments cleared; approval decisions kept.';}
  catch(e){$('shareResult').textContent=e.message;}
}
function renderCustomerAudio(){
  $('customerAudioBox').innerHTML=slotsOf().map(slot=>{
    const voice=(P.spots||[]).find(s=>s.slot===slot),mix=mixOf(slot),bed=bedOf(slot);
    if(!voice?.audio_url)return '';
    const player=(label,url,seconds,measured)=>`<p><b>${label}</b></p><div class="clip"><audio controls preload="none" src="${esc(url)}"></audio>${timingBadge(seconds,slotSeconds(slot),measured)}</div>`;
    return `<div class="slot"><h3>${esc(slotName(slot))}</h3>`+
      (mix?.audio_url?player(bed?'Final advertising audio — With music':'Final advertising audio',mix.audio_url,mix.seconds,mix.measured):bed?'<p class="hint">Music selected. The combined track will be saved when you prepare the customer link.</p>':'')+
      player(bed?'Voice only — alternate':'Voice only',voice.audio_url,voice.measured_seconds,voice.measured)+'</div>';
  }).join('');
}
let preparingCustomerAudio=false;
async function createReviewLink(){
  if(preparingCustomerAudio)return;
  preparingCustomerAudio=true;$('createReviewLink').disabled=true;
  try{
    $('shareResult').textContent='Preparing customer audio…';
    await volumeSave;
    const latest=await api('/api/projects/'+P.id);P=latest.project;
    discardMixes();
    if(!MIXCFG)await loadMixConfig();
    for(const slot of slotsOf()){
      if(!bedOf(slot)||(P.spots||[]).every(s=>s.slot!==slot||!s.audio_url))continue;
      if(mixOf(slot)?.audio_url&&mixOf(slot).level===selectedMixLevel().label)continue;
      $('shareResult').textContent='Saving the '+slotName(slot)+' voice and music track…';
      if(!RENDERED[slot]&&!await makeMix(slot))throw new Error('The '+slotName(slot)+' mix could not be rendered. Open step 6 to check the audio and try again.');
      if(!await fileMix(slot,false))throw new Error('The '+slotName(slot)+' mix could not be saved. Open step 6 to resolve the mix findings before sending the customer link.');
    }
    const d=await api('/api/projects/'+P.id+'/share',{method:'POST',body:JSON.stringify({enabled:true,require_mixes:true,headline:$('shareHeadline').value,intro:$('shareIntro').value})});
    P.share=d.share;$('shareResult').replaceChildren();const link=document.createElement('a');link.href=d.share_url;link.target='_blank';link.rel='noopener';link.textContent='Open customer review';$('shareResult').append(link);
  }catch(e){$('shareResult').textContent=e.message;}
  finally{renderCustomerAudio();preparingCustomerAudio=false;$('createReviewLink').disabled=false;}
}
