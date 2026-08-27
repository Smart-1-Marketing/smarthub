/* Smart 1 Image Creator — editor engine.
 * Extracted from templates/index.html (Batch D structural refactor,
 * 2026-08-17) so the template itself stays a template and this stays
 * a plain, cacheable static asset. Anything the server needs to pass
 * in (feature flags, presets, fonts) comes through window.IC_CONFIG,
 * set by a small inline script in index.html right before this file
 * loads — this file has no Jinja templating in it at all.
 */
/* =====================================================================
   Smart 1 Image Creator
   Fabric.js is the editing engine. Everything the panels do is add or
   modify Fabric objects — nothing is ever flattened until export, which
   is what makes a saved project reopenable and editable.
   ===================================================================== */
const $ = id => document.getElementById(id);
const esc = s => String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
// Everything the server needs to tell the client is passed through
// window.IC_CONFIG, set by a small inline script in index.html — this file
// itself is a static asset with no Jinja templating in it.
const CFG = window.IC_CONFIG || {};
const PRESETS  = CFG.presets || [];
const PROVIDERS= CFG.providers || {};
const AI_ON    = !!CFG.ai;
const CLOUD_ON = !!CFG.cloud;
const BF_ON    = !!CFG.brandfetch;
const BGREM_ON = !!CFG.bgRemove;
const FONTS    = CFG.fonts || [];
const PREFILL_CLIENT = CFG.prefillClient || '';
const OPEN_PROJECT   = CFG.openProject || '';
const PROXY = u => 'api/photos/proxy?url=' + encodeURIComponent(u);

let canvas, zoom = 1, projectId = '', chosenClient = null;
let history = [], histIndex = -1, histLock = false;
let copiedObject = null;
let cropState = null;                                  // non-null while the crop tool is active

/* ---------------- recent-item history (browser-local, per user) ---------------- */
function recentGet(key){
  try{ return JSON.parse(localStorage.getItem(key) || '[]'); }catch(e){ return []; }
}
function recentPushColor(key, hex, max){
  try{
    const list = recentGet(key).filter(c => c !== hex);
    list.unshift(hex);
    localStorage.setItem(key, JSON.stringify(list.slice(0, max||10)));
  }catch(e){ /* storage unavailable — recents just won't persist */ }
}
function recentPushItem(key, item, idKey, max){
  try{
    const list = recentGet(key).filter(x => x[idKey] !== item[idKey]);
    list.unshift(item);
    localStorage.setItem(key, JSON.stringify(list.slice(0, max||12)));
  }catch(e){ /* storage unavailable — recents just won't persist */ }
}

// Without Fabric there is no editor, so say so plainly rather than leaving a
// dead page and an error only a developer would find.
if(typeof fabric === 'undefined'){
  document.body.innerHTML =
    '<div style="max-width:640px;margin:80px auto;padding:26px;background:#fff;'
    + 'border:1px solid #f3c1c1;border-radius:12px;font:14px system-ui">'
    + '<h2 style="color:#a12626;margin:0 0 8px">The editor engine didn\'t load</h2>'
    + '<p style="color:#475569;line-height:1.6">Fabric.js is served from this app at '
    + '<code>static/fabric.min.js</code>. If that file is missing from the deploy the '
    + 'canvas can\'t start.</p>'
    + '<p style="color:#475569">Reload the page; if it persists, check that '
    + '<code>modules/image_creator/static/</code> was included in the build, then '
    + '<a href="/status">System Status</a>.</p></div>';
  throw new Error('Fabric.js failed to load');
}

/* ---------------- canvas bootstrap ---------------- */
function initCanvas(w, h){
  if(canvas){ canvas.dispose(); }
  canvas = new fabric.Canvas('c', {width:w, height:h, backgroundColor:null,
                                   preserveObjectStacking:true});
  canvas.on('selection:created', syncProps);
  canvas.on('selection:updated', syncProps);
  canvas.on('selection:cleared', syncProps);
  // Temporary UI helpers (the crop tool's overlay/handle rect) are flagged
  // _s1temp so they never pollute undo history or show up as fake layers.
  canvas.on('object:modified', e => { if(e.target && e.target._s1temp) return; pushHistory(); renderLayers(); });
  canvas.on('object:added',    e => { if(e.target && e.target._s1temp) return; pushHistory(); renderLayers(); });
  canvas.on('object:removed',  e => { if(e.target && e.target._s1temp) return; pushHistory(); renderLayers(); });
  // Smart guides (snap-to-edge/centre/equal-spacing) — see the block below
  // initCanvas for the implementation. Only active while actually dragging,
  // and always cleared on mouse-up so the guide lines never linger.
  canvas.on('object:moving', e => { if(e.target && !e.target._s1temp && !cropState) applySmartGuides(e.target); });
  canvas.on('mouse:up', clearGuides);
  canvas.on('selection:cleared', clearGuides);
  $('canvasLabel').textContent = w + ' × ' + h;
  fitZoom();
  history = []; histIndex = -1; pushHistory();
  renderLayers();
}

function fitZoom(){
  const wrap = $('canvWrap');
  const avail = Math.min((wrap.clientWidth-60)/canvas.getWidth(),
                         (wrap.clientHeight-60)/canvas.getHeight(), 1);
  setZoom(Math.max(0.08, avail));
}
function setZoom(z){
  zoom = Math.max(0.08, Math.min(z, 4));
  const el = canvas.getElement().parentNode;   // fabric's wrapper
  el.style.width  = (canvas.getWidth()*zoom)+'px';
  el.style.height = (canvas.getHeight()*zoom)+'px';
  el.style.transform = 'scale('+zoom+')';
  el.style.transformOrigin = 'top left';
  $('zLabel').textContent = Math.round(zoom*100)+'%';
}
$('zIn').onclick  = () => setZoom(zoom*1.15);
$('zOut').onclick = () => setZoom(zoom/1.15);
$('zFit').onclick = fitZoom;
window.addEventListener('resize', () => { if(canvas) fitZoom(); });

/* ---------------- history ---------------- */
function pushHistory(){
  if(!canvas || histLock) return;
  const json = JSON.stringify(canvas.toJSON(['name','s1kind','s1meta']));
  if(history[histIndex] === json) return;
  history = history.slice(0, histIndex+1);
  history.push(json);
  if(history.length > 60) history.shift();
  histIndex = history.length-1;
  updateHistButtons();
}
function restore(i){
  if(i < 0 || i >= history.length) return;
  histLock = true; histIndex = i;
  canvas.loadFromJSON(JSON.parse(history[i]), () => {
    canvas.renderAll(); histLock = false; updateHistButtons(); renderLayers(); syncProps();
  });
}
function updateHistButtons(){
  $('btnUndo').disabled = histIndex <= 0;
  $('btnRedo').disabled = histIndex >= history.length-1;
}
$('btnUndo').onclick = () => restore(histIndex-1);
$('btnRedo').onclick = () => restore(histIndex+1);

/* ---------------- object helpers ---------------- */
function addObject(obj, kind, name){
  obj.s1kind = kind || 'object';
  obj.name = name || kind || 'Object';
  canvas.add(obj);
  canvas.setActiveObject(obj);
  canvas.renderAll();
}
function centreScale(img, coverCanvas){
  const cw = canvas.getWidth(), ch = canvas.getHeight();
  const s = coverCanvas ? Math.max(cw/img.width, ch/img.height)
                        : Math.min(cw/img.width, ch/img.height, 1) * 0.8;
  img.scale(s);
  img.set({left:(cw-img.width*s)/2, top:(ch-img.height*s)/2});
}
function loadImage(url, opts){
  opts = opts || {};
  return new Promise((res, rej) => {
    fabric.Image.fromURL(url, img => {
      if(!img || !img.width) return rej(new Error('Image failed to load'));
      res(img);
    }, {crossOrigin:'anonymous'});
  });
}
async function placeImage(url, meta, asBackground){
  const src = url.startsWith('data:') ? url : PROXY(url);
  try{
    const img = await loadImage(src);
    img.s1meta = meta || null;
    if(asBackground){
      centreScale(img, true);
      img.set({selectable:true, name:'Background'});
      addObject(img, 'background', 'Background');
      canvas.sendToBack(img);
    } else {
      centreScale(img, false);
      addObject(img, 'image', (meta && meta.name) || 'Image');
    }
  }catch(e){ alert('That image could not be loaded: '+e.message); }
}

/* ---------------- panels ---------------- */
const PANELS = {};
let activePanel = 'upload';
$('rail').querySelectorAll('button').forEach(b => {
  b.onclick = () => openPanel(b.dataset.p);
});
function openPanel(key){
  if(cropState) exitCropMode(false);                  // switching panels abandons an in-progress crop
  activePanel = key;
  $('rail').querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.p===key));
  $('panelTitle').textContent = ({upload:'Upload',photos:'Photos',assets:'Smart 1 Assets',
    backgrounds:'Background',logos:'Logos',icons:'Icons',text:'Text',shapes:'Shapes',
    ai:'AI',layers:'Layers'})[key] || key;
  $('panelBody').innerHTML = '';
  (PANELS[key]||(()=>{}))($('panelBody'));
  if(isNarrowLayout()) $('leftPanel').classList.add('show');
}
/* ---------------- responsive slide-over panels (≤920px) ---------------- */
function isNarrowLayout(){ return window.matchMedia('(max-width:920px)').matches; }
$('leftPanelClose').onclick  = () => $('leftPanel').classList.remove('show');
$('rightPanelClose').onclick = () => $('rightPanel').classList.remove('show');
$('canvWrap').addEventListener('mousedown', () => {
  if(isNarrowLayout()) $('leftPanel').classList.remove('show');
});

/* ---------- Upload ---------- */
PANELS.upload = host => {
  host.innerHTML = `
    <div class="drop" id="upDrop"><b>Drop images here</b><br>
      <span class="hint">or click to browse · paste with Ctrl+V<br>PNG · JPG · WebP · SVG</span></div>
    <input type="file" id="upFile" accept="image/*" multiple hidden>
    <div class="hint">Large photos are scaled to fit the canvas; you can resize after.</div>`;
  const drop = $('upDrop'), file = $('upFile');
  drop.onclick = () => file.click();
  file.onchange = () => handleFiles([...file.files]);
  ['dragenter','dragover'].forEach(e => drop.addEventListener(e, ev => {
    ev.preventDefault(); drop.classList.add('over'); }));
  ['dragleave','drop'].forEach(e => drop.addEventListener(e, ev => {
    ev.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', ev => handleFiles([...ev.dataTransfer.files]));
};
function handleFiles(files){
  files.filter(f => f.type.startsWith('image/')).slice(0,10).forEach(f => {
    const r = new FileReader();
    r.onload = () => placeImage(r.result, {name:f.name, source:'upload'}, false);
    r.readAsDataURL(f);
  });
}
window.addEventListener('paste', ev => {
  const items = [...(ev.clipboardData?.items||[])].filter(i => i.type.startsWith('image/'));
  if(items.length) handleFiles(items.map(i => i.getAsFile()).filter(Boolean));
});

/* ---------- Photos (universal search) ---------- */
let photoState = {q:'', orientation:'any', color:'', sort:'relevant', page:1, results:[]};
const PHOTO_COLORS = ['','red','orange','yellow','green','blue','purple','pink','brown','black','gray','white'];
PANELS.photos = host => {
  const off = Object.entries(PROVIDERS).filter(([,v]) => !v).map(([k]) => k);
  host.innerHTML = `
    ${off.length ? '<div class="warn">Not searching '+off.join(', ')+
      ' — no API key set. The other providers still work.</div>' : ''}
    <div class="row" style="margin-bottom:8px">
      <input type="search" id="phQ" placeholder="Search stock photos…" value="${esc(photoState.q)}">
      <span data-help="image_creator.photos.search"></span>
      <button class="btn sm" id="phGo" style="flex:none">Search</button>
    </div>
    ${AI_ON ? `<div class="sect">
      <button class="btn sec sm" id="phAI" style="width:100%">✨ Describe it instead</button>
      <span data-help="image_creator.photos.describe"></span>
      <div class="hint">Say what you need in plain English and AI will work out the search terms.</div>
    </div>` : ''}
    <div class="chips" id="phOrient">
      ${['any','landscape','portrait','square'].map(o =>
        `<button class="chip ${photoState.orientation===o?'on':''}" data-o="${o}">${o[0].toUpperCase()+o.slice(1)}</button>`).join('')}
    </div>
    <div class="row" style="margin-bottom:9px">
      <div>
        <label class="f">Color</label>
        <select id="phColor">
          ${PHOTO_COLORS.map(c => `<option value="${c}" ${photoState.color===c?'selected':''}>${c?c[0].toUpperCase()+c.slice(1):'Any color'}</option>`).join('')}
        </select>
      </div>
      <div>
        <label class="f">Sort</label>
        <select id="phSort">
          ${['relevant','popular','new'].map(s => `<option value="${s}" ${photoState.sort===s?'selected':''}>${s[0].toUpperCase()+s.slice(1)}</option>`).join('')}
        </select>
      </div>
    </div>
    <div id="phResults"></div>`;
  $('phGo').onclick = () => { photoState.q = $('phQ').value.trim(); photoState.page=1; runPhotoSearch(); };
  $('phQ').addEventListener('keydown', e => { if(e.key==='Enter') $('phGo').click(); });
  host.querySelectorAll('#phOrient .chip').forEach(c => c.onclick = () => {
    photoState.orientation = c.dataset.o; photoState.page=1;
    host.querySelectorAll('#phOrient .chip').forEach(x => x.classList.toggle('on', x===c));
    if(photoState.q) runPhotoSearch();
  });
  $('phColor').onchange = () => { photoState.color = $('phColor').value; photoState.page=1; if(photoState.q) runPhotoSearch(); };
  $('phSort').onchange  = () => { photoState.sort  = $('phSort').value;  photoState.page=1; if(photoState.q) runPhotoSearch(); };
  if($('phAI')) $('phAI').onclick = aiPhotoQueries;
  if(photoState.results.length) renderPhotos(photoState.results);
};
function photoSearchUrl(){
  return `api/photos/search?q=${encodeURIComponent(photoState.q)}`
    + `&orientation=${photoState.orientation}&color=${encodeURIComponent(photoState.color)}`
    + `&sort=${photoState.sort}&page=${photoState.page}&perPage=30`;
}
async function runPhotoSearch(){
  const box = $('phResults'); if(!box) return;
  box.innerHTML = '<div class="loading">Searching all providers…</div>';
  try{
    const d = await fetch(photoSearchUrl()).then(r=>r.json());
    photoState.results = d.results||[];
    if(!photoState.results.length){
      box.innerHTML = '<div class="loading">'+(d.errors&&d.errors.length
        ? 'All providers failed. '+esc(d.errors[0]) : 'No photos matched that search.')+'</div>';
      return;
    }
    renderPhotos(photoState.results, d.errors);
  }catch(e){ box.innerHTML = '<div class="loading">Search failed.</div>'; }
}
function renderPhotos(items, errors){
  const box = $('phResults'); if(!box) return;
  box.innerHTML =
    ((errors&&errors.length) ? '<div class="warn">'+esc(errors[0])+'</div>' : '')
    + '<div class="masonry">' + items.map((p,i) =>
      `<a class="tile" data-i="${i}" title="${esc(p.author)} · ${esc(p.provider)}">
         <img src="${esc(p.thumbnail)}" loading="lazy" alt="">
         <span class="cred">${esc(p.author||p.provider)}<br>${esc(p.provider)}</span></a>`).join('')
    + '</div><div class="hint" style="margin-top:9px">Click to add. Full resolution is only fetched when you do.</div>'
    + '<button class="btn sec sm" id="phMore" style="width:100%;margin-top:8px">Load more</button>';
  box.querySelectorAll('.tile').forEach(t => t.onclick = () => {
    const p = items[+t.dataset.i];
    placeImage(p.full || p.preview, {name:p.author||p.provider, source:p.provider,
      provider:p.provider, author:p.author, sourceUrl:p.source_url,
      providerImageId:p.provider_image_id}, false);
    fetch('api/photos/use', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({photo:p})}).catch(()=>{});
  });
  if($('phMore')) $('phMore').onclick = async () => {
    photoState.page++;
    const d = await fetch(photoSearchUrl()).then(r=>r.json());
    photoState.results = photoState.results.concat(d.results||[]);
    renderPhotos(photoState.results);
  };
}
async function aiPhotoQueries(){
  const want = prompt('Describe the photo you need:\n\ne.g. someone frustrated because their AC stopped working');
  if(!want) return;
  const box = $('phResults'); box.innerHTML = '<div class="loading">Working out the search terms…</div>';
  try{
    const d = await fetch('api/ai/photo-queries', {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({prompt:want})}).then(r=>r.json());
    if(d.error){ box.innerHTML = '<div class="err">'+esc(d.error)+'</div>'; return; }
    box.innerHTML = '<div class="sect"><div class="h">Try one of these</div>'
      + d.queries.map(q => `<button class="btn sec sm" style="width:100%;margin-bottom:5px" data-q="${esc(q)}">${esc(q)}</button>`).join('')
      + '</div>';
    box.querySelectorAll('[data-q]').forEach(b => b.onclick = () => {
      photoState.q = b.dataset.q; $('phQ').value = b.dataset.q; photoState.page=1; runPhotoSearch();
    });
  }catch(e){ box.innerHTML = '<div class="err">That failed.</div>'; }
}

/* ---------- Smart 1 assets ---------- */
PANELS.assets = host => {
  host.innerHTML = `
    <div class="hint">Assets already in the Hub — optimized client images, and imagery
      captured by site audits.</div>
    <div class="row" style="margin:9px 0">
      <input type="search" id="asQ" placeholder="Filter…">
      <button class="btn sm" id="asGo" style="flex:none">Find</button>
    </div>
    <div class="chips">
      <button class="chip on" data-src="gallery">Client gallery</button>
      <button class="chip" data-src="scans">Site audits</button>
    </div>
    <div id="asBody"><div class="loading">Loading…</div></div>`;
  let src = 'gallery';
  host.querySelectorAll('.chip').forEach(c => c.onclick = () => {
    src = c.dataset.src;
    host.querySelectorAll('.chip').forEach(x => x.classList.toggle('on', x===c));
    loadAssets(src);
  });
  $('asGo').onclick = () => loadAssets(src);
  $('asQ').addEventListener('keydown', e => { if(e.key==='Enter') loadAssets(src); });
  loadAssets(src);
};
async function loadAssets(src){
  const box = $('asBody'); if(!box) return;
  box.innerHTML = '<div class="loading">Loading…</div>';
  const client = chosenClient ? chosenClient.name : (PREFILL_CLIENT||'');
  const q = $('asQ') ? $('asQ').value.trim() : '';
  try{
    const url = src==='gallery'
      ? `api/assets/gallery?client=${encodeURIComponent(client)}&q=${encodeURIComponent(q)}`
      : `api/assets/scans?client=${encodeURIComponent(client)}`;
    const d = await fetch(url).then(r=>r.json());
    const list = d.assets||[];
    if(!list.length){
      box.innerHTML = '<div class="loading">'+(src==='gallery'
        ? 'No gallery images'+(client?' for '+esc(client):'')+' yet.'
        : 'No audit imagery found.')+'</div>';
      return;
    }
    box.innerHTML = '<div class="grid">' + list.map((a,i) =>
      `<a class="tile" data-i="${i}" title="${esc(a.alt||a.name)}">
         <img src="${esc(a.thumbnail)}" loading="lazy" alt="">
         <span class="cred">${esc(a.name||'')}</span></a>`).join('') + '</div>';
    box.querySelectorAll('.tile').forEach(t => t.onclick = () => {
      const a = list[+t.dataset.i];
      placeImage(a.url, {name:a.name, source:a.source, alt:a.alt}, false);
    });
  }catch(e){ box.innerHTML = '<div class="loading">Could not load assets.</div>'; }
}

/* ---------- Backgrounds ---------- */
PANELS.backgrounds = host => {
  host.innerHTML = `
    <div class="sect">
      <div class="h">Solid color</div>
      <div class="row"><input type="color" id="bgColor" value="#1a2e58">
        <button class="btn sm" id="bgColorGo" style="flex:none">Apply</button></div>
      <div class="swatches" style="margin-top:7px" id="bgSwatches"></div>
      <div id="bgRecentWrap"></div>
    </div>
    <div class="sect">
      <div class="h">Gradient</div>
      <div class="row">
        <div><label class="f">Color 1</label><input type="color" id="g1" value="#2563eb"></div>
        <div><label class="f">Color 2</label><input type="color" id="g2" value="#1a2e58"></div>
      </div>
      <label style="font-size:12px;display:block;margin-top:7px">
        <input type="checkbox" id="g3On"> Add a 3rd color</label>
      <input type="color" id="g3" value="#7c3aed" style="display:none;margin-top:6px">
      <div class="row" style="margin-top:7px">
        <div><label class="f">Type</label><select id="gType">
          <option value="linear">Linear</option><option value="radial">Radial</option>
        </select></div>
        <div><label class="f">Angle <span id="gAngleLabel">180°</span></label>
          <input type="range" id="gAngle" min="0" max="360" value="180"></div>
      </div>
      <button class="btn sm" id="gGo" style="width:100%;margin-top:8px">Apply</button>
      <div class="grid g3" style="margin-top:8px" id="gPresets"></div>
    </div>
    <div class="sect">
      <div class="h">Pattern</div>
      <div class="grid g3" id="patGrid"></div>
      <div class="row" style="margin-top:7px">
        <input type="color" id="patFg" value="#2563eb"><input type="color" id="patBg" value="#ffffff">
      </div>
      <label class="f" style="margin-top:6px">Scale</label>
      <input type="range" id="patScale" min="8" max="60" value="22">
      <label class="f" style="margin-top:6px">Spacing</label>
      <input type="range" id="patSpacing" min="0" max="40" value="0">
      <label class="f" style="margin-top:6px">Rotation <span id="patRotLabel">0°</span></label>
      <input type="range" id="patRotation" min="0" max="360" value="0">
      <label class="f" style="margin-top:6px">Opacity <span id="patOpLabel">100%</span></label>
      <input type="range" id="patOpacity" min="10" max="100" value="100">
      <div class="hint">Click a pattern above to apply the current settings; adjusting a
        slider afterwards re-applies to whichever pattern is already on the canvas.</div>
    </div>
    <div class="sect">
      <div class="h">Photo background</div>
      <div class="row" style="margin-bottom:8px">
        <input type="search" id="bgPhotoQ" placeholder="Search photos…">
        <button class="btn sm" id="bgPhotoGo" style="flex:none">Search</button>
      </div>
      <div id="bgPhotoResults"></div>
      <div id="bgPhotoActions" style="display:none;margin-top:8px">
        <div class="row"><button class="btn sec sm" id="bgFill">Fill canvas</button>
          <button class="btn sec sm" id="bgFit">Fit canvas</button></div>
        <button class="btn sec sm" id="bgPhotoReplace" style="width:100%;margin-top:6px">Replace photo</button>
        <div class="hint">Drag the background on the canvas to reposition it.</div>
      </div>
    </div>`;
  ['#ffffff','#000000','#1a2e58','#2563eb','#16a34a','#dc2626','#f59e0b','#7c3aed','#f1f5f9','#334155']
    .forEach(c => {
      const s = document.createElement('div');
      s.className='sw'; s.style.background=c; s.title=c;
      s.onclick = () => setSolidBackground(c);
      $('bgSwatches').appendChild(s);
    });
  const recentColors = recentGet('ic_recent_bg_colors');
  if(recentColors.length){
    $('bgRecentWrap').innerHTML = '<div class="hint" style="margin:8px 0 5px">Recent</div>'
      + '<div class="swatches" id="bgRecentSwatches"></div>';
    recentColors.forEach(c => {
      const s = document.createElement('div');
      s.className='sw'; s.style.background=c; s.title=c;
      s.onclick = () => setSolidBackground(c);
      $('bgRecentSwatches').appendChild(s);
    });
  }
  $('bgColorGo').onclick = () => setSolidBackground($('bgColor').value);

  let lastPattern = '';
  const applyGradient = () => {
    const colors = [$('g1').value, $('g2').value];
    if($('g3On').checked) colors.push($('g3').value);
    setGradient(colors, +$('gAngle').value, $('gType').value);
  };
  $('g3On').onchange = () => { $('g3').style.display = $('g3On').checked ? 'block' : 'none'; };
  $('gAngle').oninput = () => { $('gAngleLabel').textContent = $('gAngle').value+'°'; };
  $('gType').onchange = () => { $('gAngle').disabled = $('gType').value==='radial'; };
  $('gGo').onclick = applyGradient;
  [['#2563eb','#7c3aed'],['#f59e0b','#dc2626'],['#16a34a','#0891b2'],
   ['#1a2e58','#2563eb'],['#111827','#374151'],['#f472b6','#8b5cf6']].forEach(([a,b]) => {
    const d = document.createElement('div');
    d.className='tile'; d.style.background=`linear-gradient(160deg,${a},${b})`;
    d.onclick = () => {
      $('g1').value=a; $('g2').value=b; $('g3On').checked=false; $('g3').style.display='none';
      applyGradient();
    };
    $('gPresets').appendChild(d);
  });

  const applyPattern = () => { if(lastPattern) setPattern(lastPattern); };
  ['patFg','patBg','patScale','patSpacing','patRotation','patOpacity'].forEach(id => {
    $(id).oninput = () => {
      if(id==='patRotation') $('patRotLabel').textContent = $('patRotation').value+'°';
      if(id==='patOpacity') $('patOpLabel').textContent = $('patOpacity').value+'%';
      applyPattern();
    };
    $(id).onchange = pushHistory;
  });
  ['dots','grid','diagonal','waves','circles','hexagons','triangles','checker','confetti']
    .forEach(p => {
      const d = document.createElement('div');
      d.className='tile'; d.style.cssText='aspect-ratio:1;display:flex;align-items:center;justify-content:center;font-size:10px;color:#64748b;background:#fff';
      d.textContent = p;
      d.onclick = () => { lastPattern = p; setPattern(p); };
      $('patGrid').appendChild(d);
    });

  $('bgPhotoGo').onclick = bgPhotoSearch;
  $('bgPhotoQ').addEventListener('keydown', e => { if(e.key==='Enter') bgPhotoSearch(); });
  $('bgFill').onclick = () => { const im = currentBackgroundImage(); if(im){ fitBackgroundImage(im,'fill'); canvas.renderAll(); pushHistory(); } };
  $('bgFit').onclick  = () => { const im = currentBackgroundImage(); if(im){ fitBackgroundImage(im,'fit');  canvas.renderAll(); pushHistory(); } };
  $('bgPhotoReplace').onclick = () => { $('bgPhotoActions').style.display='none'; $('bgPhotoQ').value=''; $('bgPhotoQ').focus(); };
  if(currentBackgroundImage()) $('bgPhotoActions').style.display = 'block';
};
let bgPhotoResults = [];
async function bgPhotoSearch(){
  const q = $('bgPhotoQ').value.trim(); if(!q) return;
  const box = $('bgPhotoResults'); box.innerHTML = '<div class="loading">Searching…</div>';
  try{
    const d = await fetch(`api/photos/search?q=${encodeURIComponent(q)}&perPage=18`).then(r=>r.json());
    bgPhotoResults = d.results||[];
    if(!bgPhotoResults.length){ box.innerHTML = '<div class="loading">No photos matched.</div>'; return; }
    box.innerHTML = '<div class="masonry">' + bgPhotoResults.map((p,i) =>
      `<a class="tile" data-i="${i}" title="${esc(p.author)} · ${esc(p.provider)}">
         <img src="${esc(p.thumbnail)}" loading="lazy" alt=""></a>`).join('') + '</div>';
    box.querySelectorAll('.tile').forEach(t => t.onclick = () => {
      const p = bgPhotoResults[+t.dataset.i];
      setPhotoBackground(p.full || p.preview, {name:p.author||p.provider, source:p.provider,
        provider:p.provider, author:p.author, sourceUrl:p.source_url,
        providerImageId:p.provider_image_id}, 'fill');
      fetch('api/photos/use', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({photo:p})}).catch(()=>{});
    });
  }catch(e){ box.innerHTML = '<div class="loading">Search failed.</div>'; }
}
function currentBackgroundImage(){
  return canvas.getObjects().find(o => o.s1kind==='background' && o.type==='image');
}
function fitBackgroundImage(img, mode){
  const cw = canvas.getWidth(), ch = canvas.getHeight();
  const s = mode==='fit' ? Math.min(cw/img.width, ch/img.height)
                         : Math.max(cw/img.width, ch/img.height);
  img.scale(s);
  img.set({left:(cw-img.width*s)/2, top:(ch-img.height*s)/2});
}
async function setPhotoBackground(url, meta, mode){
  const src = url.startsWith('data:') ? url : PROXY(url);
  try{
    const img = await loadImage(src);
    img.s1meta = meta || null;
    clearBackgroundObjects();
    canvas.setBackgroundColor(null, ()=>{});
    fitBackgroundImage(img, mode||'fill');
    img.set({selectable:true, s1kind:'background', name:'Background'});
    canvas.add(img); canvas.sendToBack(img); canvas.renderAll(); pushHistory(); renderLayers();
    const actions = $('bgPhotoActions'); if(actions) actions.style.display = 'block';
  }catch(e){ alert('That image could not be loaded: '+e.message); }
}
function clearBackgroundObjects(){
  canvas.getObjects().filter(o => o.s1kind==='background').forEach(o => canvas.remove(o));
}
function setSolidBackground(color){
  clearBackgroundObjects();
  canvas.setBackgroundColor(color, () => canvas.renderAll());
  canvas.backgroundImage = null;
  recentPushColor('ic_recent_bg_colors', color, 10);
  pushHistory();
}
function gradientLineCoords(w, h, angleDeg){
  // A line through the canvas center, long enough to span corner-to-corner at
  // any angle, so the gradient covers the full rect with no banding —
  // 180° matches the tool's old fixed "top to bottom" default.
  const rad = angleDeg * Math.PI / 180;
  const cx = w/2, cy = h/2, len = Math.sqrt(w*w + h*h) / 2;
  const dx = Math.sin(rad) * len, dy = -Math.cos(rad) * len;
  return {x1: cx - dx, y1: cy - dy, x2: cx + dx, y2: cy + dy};
}
function setGradient(colors, angle, type){
  clearBackgroundObjects();
  const w = canvas.getWidth(), h = canvas.getHeight();
  const stops = colors.map((c,i) => ({offset: colors.length>1 ? i/(colors.length-1) : 0, color:c}));
  const coords = type==='radial'
    ? {r1:0, r2:Math.max(w,h)/1.4, x1:w/2, y1:h/2, x2:w/2, y2:h/2}
    : gradientLineCoords(w, h, angle);
  const rect = new fabric.Rect({left:0, top:0, width:w, height:h, selectable:false, evented:false});
  rect.set('fill', new fabric.Gradient({type: type==='radial'?'radial':'linear', coords, colorStops:stops}));
  rect.s1kind='background'; rect.name='Gradient background';
  canvas.setBackgroundColor(null, ()=>{});
  canvas.add(rect); canvas.sendToBack(rect); canvas.renderAll(); pushHistory(); renderLayers();
}
function patternSVG(kind, fg, bg, size, spacing, rotation){
  spacing = spacing||0; rotation = rotation||0;
  const s = size, h = s/2;
  const body = {
    dots:`<circle cx="${h}" cy="${h}" r="${s*0.14}" fill="${fg}"/>`,
    grid:`<path d="M0 0H${s}M0 0V${s}" stroke="${fg}" stroke-width="1.2" fill="none"/>`,
    diagonal:`<path d="M0 ${s}L${s} 0" stroke="${fg}" stroke-width="1.6"/>`,
    waves:`<path d="M0 ${h}Q${s*0.25} 0 ${h} ${h}T${s} ${h}" stroke="${fg}" stroke-width="1.5" fill="none"/>`,
    circles:`<circle cx="${h}" cy="${h}" r="${s*0.34}" stroke="${fg}" stroke-width="1.2" fill="none"/>`,
    hexagons:`<path d="M${h} ${s*0.08}L${s*0.9} ${s*0.3}V${s*0.7}L${h} ${s*0.92}L${s*0.1} ${s*0.7}V${s*0.3}Z" stroke="${fg}" stroke-width="1.1" fill="none"/>`,
    triangles:`<path d="M${h} ${s*0.15}L${s*0.88} ${s*0.85}H${s*0.12}Z" fill="${fg}"/>`,
    checker:`<rect width="${h}" height="${h}" fill="${fg}"/><rect x="${h}" y="${h}" width="${h}" height="${h}" fill="${fg}"/>`,
    confetti:`<rect x="${s*0.15}" y="${s*0.2}" width="${s*0.16}" height="${s*0.06}" fill="${fg}" transform="rotate(28 ${h} ${h})"/><rect x="${s*0.62}" y="${s*0.66}" width="${s*0.16}" height="${s*0.06}" fill="${fg}" transform="rotate(-40 ${h} ${h})"/>`
  }[kind] || '';
  const tile = s + spacing, off = spacing/2;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${tile}" height="${tile}">`
    + `<rect width="${tile}" height="${tile}" fill="${bg}"/>`
    + `<g transform="translate(${off} ${off}) rotate(${rotation} ${h} ${h})">${body}</g></svg>`;
}
function setPattern(kind){
  const size = +$('patScale').value, fg = $('patFg').value, bg = $('patBg').value;
  const spacing = +$('patSpacing').value, rotation = +$('patRotation').value;
  const opacity = +$('patOpacity').value / 100;
  const svg = patternSVG(kind, fg, bg, size, spacing, rotation);
  const url = 'data:image/svg+xml;base64,' + btoa(svg);
  fabric.Image.fromURL(url, img => {
    clearBackgroundObjects();
    const rect = new fabric.Rect({left:0, top:0, width:canvas.getWidth(), height:canvas.getHeight(),
      selectable:false, evented:false, opacity});
    rect.set('fill', new fabric.Pattern({source:img.getElement(), repeat:'repeat'}));
    rect.s1kind='background'; rect.name = kind+' pattern';
    canvas.setBackgroundColor(null, ()=>{});
    canvas.add(rect); canvas.sendToBack(rect); canvas.renderAll(); pushHistory(); renderLayers();
  });
}

/* ---------- Logos ---------- */
PANELS.logos = host => {
  host.innerHTML = `
    ${BF_ON ? '' : '<div class="warn">Brandfetch has no API key, so lookups fall back to whatever the Hub already cached.</div>'}
    <div class="row" style="margin-bottom:8px">
      <input type="search" id="loQ" placeholder="nike.com or Nike">
      <button class="btn sm" id="loGo" style="flex:none">Find</button>
    </div>
    <div class="sect">
      <button class="btn sec sm" style="width:100%" id="loUpload">Upload a logo</button>
      <input type="file" id="loFile" accept="image/png,image/jpeg,image/svg+xml,image/webp" hidden>
      <div class="hint">SVG stays vector — it won't be rasterised.</div>
    </div>
    <div id="loPending"></div>
    <div id="loRecent"></div>
    <div id="loBody"></div>`;
  $('loGo').onclick = brandLookup;
  $('loQ').addEventListener('keydown', e => { if(e.key==='Enter') brandLookup(); });
  $('loUpload').onclick = () => $('loFile').click();
  $('loFile').onchange = () => {
    const f = $('loFile').files[0]; if(!f) return;
    const r = new FileReader();
    r.onload = () => {
      if(f.type === 'image/svg+xml'){ addSVGString(r.result, f.name); return; }
      // Raster logos get a chance to clean the background up before they land
      // on the canvas — §19 of the dev outline.
      showLogoUploadOptions(r.result, f.name);
    };
    if(f.type === 'image/svg+xml') r.readAsText(f); else r.readAsDataURL(f);
  };
  const recentLogos = recentGet('ic_recent_logos');
  if(recentLogos.length){
    $('loRecent').innerHTML = '<div class="sect"><div class="h">Recent logos</div><div class="grid" id="loRecentGrid"></div></div>';
    recentLogos.forEach(l => {
      const a = document.createElement('a');
      a.className = 'logotile' + (l.theme==='dark' ? ' dark' : '');
      a.style.cssText += 'border:1px solid var(--line);border-radius:7px;cursor:pointer';
      a.title = l.label || '';
      a.innerHTML = `<img src="${esc(l.url)}" alt="" loading="lazy">`;
      a.onclick = () => addLogo(l);
      $('loRecentGrid').appendChild(a);
    });
  }
  if(chosenClient && chosenClient.domain){ $('loQ').value = chosenClient.domain; brandLookup(); }
};
async function brandLookup(){
  const q = $('loQ').value.trim(); if(!q) return;
  const box = $('loBody'); box.innerHTML = '<div class="loading">Looking up the brand…</div>';
  try{
    const d = await fetch('api/brands/search?q='+encodeURIComponent(q)).then(r=>r.json());
    if(d.error){ box.innerHTML = '<div class="err">'+esc(d.error)+'</div>'; return; }
    box.innerHTML = `<div class="sect"><div class="h">${esc(d.name)} — logos</div>
        <div class="grid" id="loGrid"></div></div>
      ${d.colors && d.colors.length ? `<div class="sect"><div class="h">Brand colors</div>
        <div class="swatches" id="loColors"></div>
        <div class="hint">Click a color to use it on the selected object.</div></div>` : ''}`;
    d.logos.forEach(l => {
      const a = document.createElement('a');
      a.className = 'logotile' + (l.theme==='dark' ? ' dark' : '');
      a.style.cssText += 'border:1px solid var(--line);border-radius:7px;cursor:pointer';
      a.title = l.label + (l.format ? ' · '+l.format.toUpperCase() : '');
      a.innerHTML = `<img src="${esc(l.url)}" alt="" loading="lazy">`;
      a.onclick = () => addLogo(l);
      $('loGrid').appendChild(a);
    });
    (d.colors||[]).forEach(c => {
      const s = document.createElement('div');
      s.className='sw'; s.style.background=c.hex; s.title=c.hex+(c.type?' · '+c.type:'');
      s.onclick = () => applyColorToSelection(c.hex);
      $('loColors').appendChild(s);
    });
  }catch(e){ box.innerHTML = '<div class="err">Lookup failed.</div>'; }
}
async function addLogo(l){
  recentPushItem('ic_recent_logos', l, 'url', 12);
  if((l.format||'').toLowerCase() === 'svg'){
    try{
      const txt = await fetch(PROXY(l.url)).then(r => r.text());
      if(txt.trim().startsWith('<svg')) return addSVGString(txt, l.label || 'Logo');
    }catch(e){ /* fall through to raster */ }
  }
  placeImage(l.url, {name:l.label||'Logo', source:'brandfetch'}, false);
}
function addSVGString(svgText, name){
  fabric.loadSVGFromString(svgText, (objects, options) => {
    if(!objects || !objects.length) return alert('That SVG could not be parsed.');
    const obj = fabric.util.groupSVGElements(objects, options);
    const max = Math.min(canvas.getWidth(), canvas.getHeight()) * 0.35;
    if(obj.width) obj.scaleToWidth(max);
    obj.set({left:(canvas.getWidth()-obj.getScaledWidth())/2,
             top:(canvas.getHeight()-obj.getScaledHeight())/2});
    addObject(obj, 'svg', name || 'Vector');
  });
}

/* ---------- Logo upload: background cleanup before it hits the canvas ---------- */
function showLogoUploadOptions(dataUrl, name){
  const box = $('loPending'); if(!box) return;
  box.innerHTML = `
    <div class="sect" style="border:1px solid var(--line);border-radius:9px;padding:11px;background:var(--soft)">
      <div class="h">Just uploaded</div>
      <img src="${dataUrl}" alt="" style="max-width:100%;max-height:110px;display:block;margin:6px 0;
        background-image:linear-gradient(45deg,#eceff4 25%,transparent 25%),linear-gradient(-45deg,#eceff4 25%,transparent 25%),
        linear-gradient(45deg,transparent 75%,#eceff4 75%),linear-gradient(-45deg,transparent 75%,#eceff4 75%);
        background-size:14px 14px;background-position:0 0,0 7px,7px -7px,-7px 0">
      <button class="btn sm" id="loAddAsIs" style="width:100%;margin-bottom:6px">Add as-is</button>
      <button class="btn sec sm" id="loRemoveBg" style="width:100%;margin-bottom:6px"
        ${BGREM_ON?'':'disabled title="REMOVE_BG_API_KEY isn\'t set"'}>Remove background</button>
      <button class="btn sec sm" id="loRemoveWhite" style="width:100%">Remove white background</button>
      <div class="hint" id="loPendingMsg"></div>
    </div>`;
  $('loAddAsIs').onclick = () => { placeImage(dataUrl, {name, source:'upload'}, false); box.innerHTML=''; };
  $('loRemoveWhite').onclick = async () => {
    $('loPendingMsg').textContent = 'Removing white background…';
    try{
      const out = await removeWhiteBackground(dataUrl);
      placeImage(out, {name, source:'upload'}, false);
      box.innerHTML = '';
    }catch(e){ $('loPendingMsg').textContent = 'That failed — try "Add as-is" instead.'; }
  };
  if(BGREM_ON) $('loRemoveBg').onclick = async () => {
    $('loPendingMsg').textContent = 'Removing background… this takes a moment.';
    $('loRemoveBg').disabled = true;
    try{
      const d = await fetch('api/logos/remove-background', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({image:dataUrl})}).then(r=>r.json());
      $('loRemoveBg').disabled = false;
      if(d.error){ $('loPendingMsg').textContent = d.error; return; }
      placeImage(d.image, {name, source:'upload'}, false);
      box.innerHTML = '';
    }catch(e){ $('loRemoveBg').disabled=false; $('loPendingMsg').textContent = 'That failed — try "Add as-is" instead.'; }
  };
}
function removeWhiteBackground(dataUrl, threshold){
  // A free, local alternative to the paid remove.bg call — flattens any
  // near-white pixel to transparent. Good enough for a logo shot on a plain
  // white background; not a substitute for real subject cut-out.
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      try{
        const c = document.createElement('canvas');
        c.width = img.naturalWidth; c.height = img.naturalHeight;
        const ctx = c.getContext('2d');
        ctx.drawImage(img, 0, 0);
        const id = ctx.getImageData(0, 0, c.width, c.height);
        const d = id.data, t = threshold || 245;
        for(let i=0; i<d.length; i+=4){
          if(d[i]>=t && d[i+1]>=t && d[i+2]>=t) d[i+3] = 0;
        }
        ctx.putImageData(id, 0, 0);
        resolve(c.toDataURL('image/png'));
      }catch(e){ reject(e); }
    };
    img.onerror = reject;
    img.src = dataUrl;
  });
}

/* ---------- Icons ---------- */
PANELS.icons = host => {
  host.innerHTML = `
    <div class="row" style="margin-bottom:8px">
      <input type="search" id="icQ" placeholder="air conditioner, phone, star…">
      <button class="btn sm" id="icGo" style="flex:none">Search</button>
    </div>
    <div class="chips" id="icCats"></div>
    <div id="icBody"><div class="hint">Icons are added as vectors, so they stay sharp and recolorable.</div></div>`;
  [['Business','briefcase'],['Automotive','car'],['Restaurant','restaurant'],
   ['Home Services','tools'],['Medical','medical'],['Legal','legal'],['Weather','weather'],
   ['Shopping','shopping cart'],['Finance','finance'],['Communication','phone'],
   ['Social','social'],['Arrows','arrow'],['People','people'],['Travel','travel']].forEach(([label,term]) => {
    const b = document.createElement('button');
    b.className='chip'; b.textContent=label;
    b.onclick = () => { $('icQ').value = term; iconSearch(); };
    $('icCats').appendChild(b);
  });
  $('icGo').onclick = iconSearch;
  $('icQ').addEventListener('keydown', e => { if(e.key==='Enter') iconSearch(); });
};
async function iconSearch(){
  const q = $('icQ').value.trim(); if(!q) return;
  const box = $('icBody'); box.innerHTML = '<div class="loading">Searching icons…</div>';
  try{
    const d = await fetch('api/icons/search?q='+encodeURIComponent(q)).then(r=>r.json());
    if(!d.icons || !d.icons.length){ box.innerHTML='<div class="loading">No icons matched.</div>'; return; }
    box.innerHTML = '<div class="grid g3">' + d.icons.map((i,n) =>
      `<div class="tile icontile" data-n="${n}" title="${esc(i.name)}">
         <img src="${esc(i.preview)}" loading="lazy" alt=""></div>`).join('') + '</div>';
    box.querySelectorAll('.tile').forEach(t => t.onclick = async () => {
      const ic = d.icons[+t.dataset.n];
      try{
        const svg = await fetch(`api/icons/svg?prefix=${encodeURIComponent(ic.prefix)}&name=${encodeURIComponent(ic.icon)}&color=%231a2e58`).then(r=>r.text());
        addSVGString(svg, ic.icon);
      }catch(e){ alert('That icon could not be added.'); }
    });
  }catch(e){ box.innerHTML = '<div class="err">Icon search failed.</div>'; }
}

/* ---------- Text ---------- */
PANELS.text = host => {
  host.innerHTML = `
    <button class="btn" id="txH" style="margin-bottom:7px">+ Add heading</button>
    <button class="btn sec" id="txS" style="margin-bottom:7px">+ Add subheading</button>
    <button class="btn sec" id="txB" style="margin-bottom:12px">+ Add body text</button>
    <div class="sect">
      <div class="h">Font</div>
      <input type="search" id="fontQ" placeholder="Search Google Fonts…">
      <div id="fontList" style="max-height:230px;overflow-y:auto;margin-top:7px"></div>
    </div>`;
  $('txH').onclick = () => addText('Your headline here', {fontSize:Math.round(canvas.getWidth()/14), fontWeight:'700'});
  $('txS').onclick = () => addText('Supporting subheading', {fontSize:Math.round(canvas.getWidth()/24), fontWeight:'600'});
  $('txB').onclick = () => addText('Body copy goes here.', {fontSize:Math.round(canvas.getWidth()/34)});
  renderFontList(FONTS);
  $('fontQ').addEventListener('input', async () => {
    const q = $('fontQ').value.trim();
    const d = await fetch('api/fonts?q='+encodeURIComponent(q)).then(r=>r.json()).catch(()=>({fonts:FONTS}));
    renderFontList(d.fonts||[]);
  });
};
function renderFontList(fonts){
  const box = $('fontList'); if(!box) return;
  box.innerHTML = fonts.slice(0,120).map(f =>
    `<div class="layer" data-f="${esc(f)}" style="font-family:'${esc(f)}',sans-serif">${esc(f)}</div>`).join('');
  box.querySelectorAll('[data-f]').forEach(el => {
    loadFont(el.dataset.f);
    el.onclick = () => {
      const o = canvas.getActiveObject();
      loadFont(el.dataset.f, () => {
        if(o && o.type && o.type.includes('text')){ o.set('fontFamily', el.dataset.f); canvas.renderAll(); pushHistory(); syncProps(); }
        else addText('Your text', {fontFamily: el.dataset.f, fontSize:Math.round(canvas.getWidth()/16)});
      });
    };
  });
}
const loadedFonts = new Set();
function loadFont(family, cb){
  if(loadedFonts.has(family)){ if(cb) cb(); return; }
  loadedFonts.add(family);
  const link = document.createElement('link');
  link.rel='stylesheet';
  link.href = 'https://fonts.googleapis.com/css2?family=' + family.replace(/ /g,'+')
            + ':wght@300;400;600;700;900&display=swap';
  link.onload = () => {
    if(document.fonts && document.fonts.load){
      document.fonts.load(`16px "${family}"`).then(() => { canvas && canvas.renderAll(); if(cb) cb(); });
    } else if(cb) cb();
  };
  document.head.appendChild(link);
}
function addText(str, opts){
  const t = new fabric.Textbox(str, Object.assign({
    left: canvas.getWidth()*0.1, top: canvas.getHeight()*0.4,
    width: canvas.getWidth()*0.8, fill:'#1a2e58', fontFamily:'Inter',
    textAlign:'left', fontSize:42
  }, opts||{}));
  loadFont(t.fontFamily);
  addObject(t, 'text', str.slice(0,28));
}

/* ---------- Shapes ---------- */
PANELS.shapes = host => {
  const shapes = [['Rectangle','rect'],['Rounded','round'],['Circle','circle'],['Ellipse','ellipse'],
                  ['Triangle','triangle'],['Line','line'],['Arrow','arrow'],['Star','star'],['Polygon','poly']];
  host.innerHTML = '<div class="grid g3">' + shapes.map(([label,k]) =>
    `<div class="tile" data-s="${k}" style="display:flex;align-items:center;justify-content:center;background:#fff;font-size:10.5px;color:#475569;text-align:center;padding:5px">${label}</div>`).join('') + '</div>';
  host.querySelectorAll('[data-s]').forEach(t => t.onclick = () => addShape(t.dataset.s));
};
function addShape(kind){
  const size = Math.min(canvas.getWidth(), canvas.getHeight())*0.28;
  const base = {left:canvas.getWidth()/2-size/2, top:canvas.getHeight()/2-size/2,
                fill:'#2563eb', stroke:'', strokeWidth:0};
  let o;
  if(kind==='rect')      o = new fabric.Rect({...base, width:size*1.4, height:size});
  else if(kind==='round')o = new fabric.Rect({...base, width:size*1.4, height:size, rx:size*0.16, ry:size*0.16});
  else if(kind==='circle')o= new fabric.Circle({...base, radius:size/2});
  else if(kind==='ellipse')o=new fabric.Ellipse({...base, rx:size*0.7, ry:size*0.45});
  else if(kind==='triangle')o=new fabric.Triangle({...base, width:size, height:size});
  else if(kind==='line') o = new fabric.Line([0,0,size*1.5,0], {...base, stroke:'#2563eb', strokeWidth:6, fill:''});
  else if(kind==='arrow'){
    const path = `M 0 ${size*0.42} L ${size*1.05} ${size*0.42} L ${size*1.05} ${size*0.24} L ${size*1.5} ${size*0.5} L ${size*1.05} ${size*0.76} L ${size*1.05} ${size*0.58} L 0 ${size*0.58} Z`;
    o = new fabric.Path(path, base);
  }
  else if(kind==='star'){
    const pts=[], R=size/2, r=R*0.42;
    for(let i=0;i<10;i++){ const ang=(Math.PI/5)*i - Math.PI/2; const rad=i%2?r:R;
      pts.push({x:R+rad*Math.cos(ang), y:R+rad*Math.sin(ang)}); }
    o = new fabric.Polygon(pts, base);
  }
  else { const pts=[], R=size/2, n=6;
    for(let i=0;i<n;i++){ const ang=(2*Math.PI/n)*i - Math.PI/2;
      pts.push({x:R+R*Math.cos(ang), y:R+R*Math.sin(ang)}); }
    o = new fabric.Polygon(pts, base);
  }
  addObject(o, 'shape', kind);
}

/* ---------- AI ---------- */
PANELS.ai = host => {
  if(!AI_ON){ host.innerHTML = '<div class="warn">OPENAI_API_KEY isn\'t set, so AI generation is off.</div>'; return; }
  host.innerHTML = `
    <div class="sect">
      <div class="h">Generate</div>
      <textarea id="aiPrompt" rows="3" placeholder="HVAC technician working outside a suburban home"></textarea>
      <div class="chips" style="margin-top:7px" id="aiStyle">
        <button class="chip on" data-s="photo">Photo</button>
        <button class="chip" data-s="illustration">Illustration</button>
        <button class="chip" data-s="graphic">Graphic</button>
        <button class="chip" data-s="abstract">Abstract</button>
        <button class="chip" data-s="gradient">Gradient</button>
        <button class="chip" data-s="texture">Texture</button>
      </div>
      <label style="font-size:12px;display:block;margin:6px 0">
        <input type="checkbox" id="aiBg"> Use as full background</label>
      <label style="font-size:12px;display:block;margin-bottom:8px">
        <input type="checkbox" id="aiTrans"> Transparent background (cut-out subject)</label>
      <button class="btn" id="aiGo">Generate image</button>
      <div class="hint" id="aiMsg"></div>
    </div>
    <div class="sect">
      <div class="h">Copy assistance</div>
      <div class="hint">Select a text layer, then:</div>
      <div class="row" style="margin-top:6px"><button class="btn sec sm" data-c="rewrite">Rewrite</button>
        <button class="btn sec sm" data-c="shorten">Shorten</button></div>
      <div class="row" style="margin-top:6px"><button class="btn sec sm" data-c="headline">Headline</button>
        <button class="btn sec sm" data-c="cta">CTA</button></div>
      <div id="aiCopy"></div>
    </div>`;
  let style = 'photo';
  host.querySelectorAll('#aiStyle .chip').forEach(c => c.onclick = () => {
    style = c.dataset.s;
    host.querySelectorAll('#aiStyle .chip').forEach(x => x.classList.toggle('on', x===c));
  });
  $('aiGo').onclick = async () => {
    const prompt = $('aiPrompt').value.trim();
    if(!prompt){ $('aiMsg').textContent = 'Describe what you want first.'; return; }
    const asBg = $('aiBg').checked;
    $('aiGo').disabled = true; $('aiMsg').textContent = 'Generating… this takes a moment.';
    try{
      const w = canvas.getWidth(), h = canvas.getHeight();
      const size = w>h*1.2 ? '1536x1024' : (h>w*1.2 ? '1024x1536' : '1024x1024');
      const d = await fetch('api/ai/image', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({prompt, style, background:asBg,
                              transparent:$('aiTrans').checked, size})}).then(r=>r.json());
      $('aiGo').disabled = false;
      if(d.error){ $('aiMsg').textContent = d.error; return; }
      $('aiMsg').textContent = '';
      await placeImage(d.image, {name:'AI image', source:'openai'}, asBg);
    }catch(e){ $('aiGo').disabled=false; $('aiMsg').textContent = 'Generation failed: '+e; }
  };
  host.querySelectorAll('[data-c]').forEach(b => b.onclick = async () => {
    const o = canvas.getActiveObject();
    if(!o || !o.type || !o.type.includes('text')){ $('aiCopy').innerHTML='<div class="err">Select a text layer first.</div>'; return; }
    $('aiCopy').innerHTML = '<div class="loading">Writing…</div>';
    try{
      const d = await fetch('api/ai/copy', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({text:o.text, mode:b.dataset.c})}).then(r=>r.json());
      if(d.error){ $('aiCopy').innerHTML = '<div class="err">'+esc(d.error)+'</div>'; return; }
      $('aiCopy').innerHTML = '<div class="sect" style="margin-top:9px"><div class="h">Options</div>'
        + d.options.map(t => `<button class="btn sec sm" style="width:100%;margin-bottom:5px;text-align:left" data-t="${esc(t)}">${esc(t)}</button>`).join('') + '</div>';
      $('aiCopy').querySelectorAll('[data-t]').forEach(x => x.onclick = () => {
        o.set('text', x.dataset.t); canvas.renderAll(); pushHistory(); renderLayers();
      });
    }catch(e){ $('aiCopy').innerHTML='<div class="err">That failed.</div>'; }
  });
};

/* ---------- Layers ---------- */
PANELS.layers = host => { host.innerHTML = '<div id="layerList"></div>'; renderLayers(); };
function renderLayers(){
  const box = $('layerList'); if(!box || !canvas) return;
  const objs = canvas.getObjects().filter(o => !o._s1temp).slice().reverse();
  if(!objs.length){ box.innerHTML = '<div class="hint">Nothing on the canvas yet.</div>'; return; }
  const active = canvas.getActiveObject();
  box.innerHTML = objs.map((o,i) => `
    <div class="layer ${o===active?'on':''}" data-i="${i}" draggable="true">
      <span class="drag">☰</span>
      <span class="nm" data-i="${i}" title="Double-click to rename">${esc(o.name||o.s1kind||o.type)}</span>
      <span class="kind">${esc(o.s1kind||o.type)}</span>
      <button data-a="dup" data-i="${i}" title="Duplicate">⧉</button>
      <button data-a="vis" data-i="${i}" title="Show / hide">${o.visible===false?'○':'●'}</button>
      <button data-a="lock" data-i="${i}" title="Lock / unlock">${o.selectable===false?'🔒':'🔓'}</button>
      <button data-a="del" data-i="${i}" title="Delete">✕</button>
    </div>`).join('');
  // Plain selection only toggles the "on" class and syncs Properties — it must
  // NOT tear down and rebuild the row markup (renderLayers()), because doing
  // that between the two clicks of a double-click replaces the .nm node and
  // silently breaks the browser's native dblclick detection used for rename.
  function selectLayer(o){
    canvas.setActiveObject(o); canvas.renderAll(); syncProps();
    box.querySelectorAll('.layer').forEach(r => r.classList.toggle('on', objs[+r.dataset.i]===o));
  }
  box.querySelectorAll('.layer').forEach(el => {
    const o = objs[+el.dataset.i];
    el.onclick = ev => {
      if(ev.target.dataset.a || ev.target.classList.contains('nm')) return;
      selectLayer(o);
    };
    el.ondragstart = ev => { ev.dataTransfer.setData('text/plain', el.dataset.i); el.classList.add('dragging'); };
    el.ondragend = () => el.classList.remove('dragging');
    el.ondragover = ev => ev.preventDefault();
    el.ondrop = ev => {
      ev.preventDefault();
      const from = +ev.dataTransfer.getData('text/plain'), to = +el.dataset.i;
      if(from===to) return;
      // the list is reversed, so a visual move down is a z-index move up
      canvas.moveTo(objs[from], canvas.getObjects().length-1-to);
      canvas.renderAll(); pushHistory(); renderLayers();
    };
  });
  box.querySelectorAll('.nm').forEach(el => {
    const o = objs[+el.dataset.i];
    el.onclick = ev => { ev.stopPropagation(); selectLayer(o); };
    el.ondblclick = ev => {
      ev.stopPropagation();
      const input = document.createElement('input');
      input.type = 'text'; input.value = o.name || '';
      input.style.cssText = 'width:100%;font-size:12px;padding:2px 4px;border:1px solid var(--blue);border-radius:4px';
      el.replaceWith(input); input.focus(); input.select();
      const commit = () => { o.name = input.value.trim() || o.s1kind || o.type; pushHistory(); renderLayers(); };
      input.onblur = commit;
      input.onkeydown = ev2 => {
        if(ev2.key==='Enter'){ ev2.preventDefault(); commit(); }
        if(ev2.key==='Escape'){ ev2.preventDefault(); renderLayers(); }
      };
    };
  });
  box.querySelectorAll('[data-a]').forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const o = objs[+b.dataset.i];
    if(b.dataset.a==='vis'){ o.visible = o.visible===false; }
    if(b.dataset.a==='lock'){ const lock = o.selectable!==false;
      o.set({selectable:!lock, evented:!lock}); }
    if(b.dataset.a==='del'){ canvas.remove(o); }
    if(b.dataset.a==='dup'){
      o.clone(c => {
        c.set({left:o.left+22, top:o.top+22});
        c.s1kind = o.s1kind; c.name = (o.name||'Copy')+' copy';
        canvas.add(c); canvas.setActiveObject(c); canvas.renderAll();
      });
      return;                                         // clone() finishes async; its own
    }                                                  // object:added event covers history/render
    canvas.renderAll(); pushHistory(); renderLayers();
  });
}

/* ---------- Properties ---------- */
function syncProps(){
  if(cropState) return;                               // crop mode owns #propBody while active
  const o = canvas && canvas.getActiveObject();
  const box = $('propBody');
  if(isNarrowLayout()) $('rightPanel').classList.toggle('show', !!o);
  if(!o){ box.innerHTML = '<div class="pgroup"><div class="hint">Select something on the canvas to edit it.</div></div>'; renderLayers(); return; }
  const isText = o.type && o.type.includes('text');
  const isImage = o.type === 'image';
  const isRectShape = o.type === 'rect';
  box.innerHTML = `
    <div class="pgroup">
      <div class="t">Position &amp; size</div>
      <div class="row"><div><label class="f">X</label><input type="number" id="pX" value="${Math.round(o.left)}"></div>
        <div><label class="f">Y</label><input type="number" id="pY" value="${Math.round(o.top)}"></div></div>
      <div class="row" style="margin-top:6px">
        <div><label class="f">W</label><input type="number" id="pW" value="${Math.round(o.getScaledWidth())}"></div>
        <div><label class="f">H</label><input type="number" id="pH" value="${Math.round(o.getScaledHeight())}"></div></div>
      <label class="f" style="margin-top:6px">Rotation ${Math.round(o.angle)}°</label>
      <input type="range" id="pAngle" min="0" max="360" value="${Math.round(o.angle)}">
      <label class="f">Opacity ${Math.round((o.opacity??1)*100)}%</label>
      <input type="range" id="pOpacity" min="0" max="100" value="${Math.round((o.opacity??1)*100)}">
    </div>
    ${isText ? `<div class="pgroup">
      <div class="t">Text</div>
      <textarea id="pText" rows="2">${esc(o.text||'')}</textarea>
      <div class="row" style="margin-top:6px">
        <div><label class="f">Size</label><input type="number" id="pFS" value="${Math.round(o.fontSize)}"></div>
        <div><label class="f">Weight</label><select id="pFW">
          ${[300,400,600,700,900].map(w=>`<option value="${w}" ${String(o.fontWeight)==String(w)?'selected':''}>${w}</option>`).join('')}
        </select></div></div>
      <label class="f" style="margin-top:6px">Align</label>
      <select id="pAlign">${['left','center','right','justify'].map(a=>`<option ${o.textAlign===a?'selected':''}>${a}</option>`).join('')}</select>
      <div class="row" style="margin-top:6px">
        <div><label class="f">Letter spacing</label><input type="number" id="pLS" value="${Math.round(o.charSpacing||0)}"></div>
        <div><label class="f">Line height</label><input type="number" step="0.05" id="pLH" value="${o.lineHeight||1.16}"></div></div>
      <div class="row" style="margin-top:8px">
        <label style="font-size:12px"><input type="checkbox" id="pItalic" ${o.fontStyle==='italic'?'checked':''}> Italic</label>
        <label style="font-size:12px"><input type="checkbox" id="pUnderline" ${o.underline?'checked':''}> Underline</label>
      </div>
      <label style="font-size:12px;display:block;margin-top:8px">
        <input type="checkbox" id="pTextBgOn" ${o.textBackgroundColor?'checked':''}> Text background</label>
      <input type="color" id="pTextBg" value="${o.textBackgroundColor?toHex(o.textBackgroundColor):'#ffffff'}"
             style="margin-top:5px;display:${o.textBackgroundColor?'block':'none'}">
    </div>` : ''}
    <div class="pgroup">
      <div class="t">${isText?'Color':'Fill'}</div>
      <input type="color" id="pFill" value="${toHex(o.fill)}">
      <label class="f" style="margin-top:6px">Stroke</label>
      <div class="row"><input type="color" id="pStroke" value="${toHex(o.stroke||'#000000')}">
        <input type="number" id="pSW" value="${o.strokeWidth||0}" title="Stroke width"></div>
    </div>
    ${isRectShape ? `<div class="pgroup">
      <div class="t">Corner radius</div>
      <input type="range" id="pRadius" min="0" max="${Math.max(1,Math.round(Math.min(o.width||1,o.height||1)/2))}" value="${Math.round(o.rx||0)}">
    </div>` : ''}
    <div class="pgroup">
      <div class="t">Shadow</div>
      <label style="font-size:12.5px;display:block;margin-bottom:7px">
        <input type="checkbox" id="pShadowOn" ${o.shadow?'checked':''}> Enable shadow</label>
      <div class="row"><input type="color" id="pShadowColor" value="${o.shadow&&/^#/.test(o.shadow.color||'')?o.shadow.color:'#000000'}">
        <input type="number" id="pShadowBlur" value="${o.shadow?o.shadow.blur:8}" title="Blur"></div>
      <div class="row" style="margin-top:6px">
        <div><label class="f">Offset X</label><input type="number" id="pShadowX" value="${o.shadow?o.shadow.offsetX:4}"></div>
        <div><label class="f">Offset Y</label><input type="number" id="pShadowY" value="${o.shadow?o.shadow.offsetY:4}"></div></div>
    </div>
    ${isImage ? `<div class="pgroup">
      <div class="t">Adjust</div>
      <label class="f">Brightness</label><input type="range" id="fBright" min="-50" max="50" value="0">
      <label class="f">Contrast</label><input type="range" id="fContrast" min="-50" max="50" value="0">
      <label class="f">Saturation</label><input type="range" id="fSat" min="-100" max="100" value="0">
      <label class="f">Blur</label><input type="range" id="fBlur" min="0" max="40" value="0">
      <div class="row" style="margin-top:7px">
        <button class="btn sec sm" id="fGray">Grayscale</button>
        <button class="btn sec sm" id="fSepia">Sepia</button>
        <button class="btn sec sm" id="fNone">Reset</button></div>
      <div class="row" style="margin-top:8px">
        <button class="btn sec sm" id="pCrop">Crop</button>
        ${(o.cropX||o.cropY) ? '<button class="btn sec sm" id="pCropReset">Reset crop</button>' : ''}
      </div>
      <button class="btn sec sm" id="pAsBg" style="width:100%;margin-top:8px">Set as background</button>
    </div>` : ''}
    <div class="pgroup">
      <div class="t">Arrange</div>
      <div class="row"><button class="btn sec sm" data-al="left">⇤</button>
        <button class="btn sec sm" data-al="centerH">↔</button>
        <button class="btn sec sm" data-al="right">⇥</button></div>
      <div class="row" style="margin-top:6px"><button class="btn sec sm" data-al="top">⇡</button>
        <button class="btn sec sm" data-al="centerV">↕</button>
        <button class="btn sec sm" data-al="bottom">⇣</button></div>
      <div class="row" style="margin-top:6px"><button class="btn sec sm" id="pFront">Bring front</button>
        <button class="btn sec sm" id="pBack">Send back</button></div>
      <div class="row" style="margin-top:6px"><button class="btn sec sm" id="pFlipH">Flip ↔</button>
        <button class="btn sec sm" id="pFlipV">Flip ↕</button></div>
      <div class="row" style="margin-top:6px"><button class="btn sec sm" id="pDup">Duplicate</button>
        <button class="btn danger sm" id="pDel">Delete</button></div>
    </div>`;

  const upd = () => { canvas.renderAll(); };
  const bind = (id, fn, ev) => { const el=$(id); if(el) el[ev||'oninput'] = () => { fn(el.value); upd(); }; };
  bind('pX', v => o.set('left', +v)); bind('pY', v => o.set('top', +v));
  bind('pW', v => { if(o.width) o.scaleX = +v / o.width; });
  bind('pH', v => { if(o.height) o.scaleY = +v / o.height; });
  bind('pAngle', v => o.rotate(+v)); bind('pOpacity', v => o.set('opacity', +v/100));
  bind('pFill', v => o.set('fill', v)); bind('pStroke', v => o.set('stroke', v));
  bind('pSW', v => o.set('strokeWidth', +v));
  bind('pText', v => o.set('text', v)); bind('pFS', v => o.set('fontSize', +v));
  bind('pFW', v => o.set('fontWeight', v)); bind('pAlign', v => o.set('textAlign', v));
  bind('pLS', v => o.set('charSpacing', +v)); bind('pLH', v => o.set('lineHeight', +v));
  ['pX','pY','pW','pH','pAngle','pOpacity','pFill','pStroke','pSW','pFS'].forEach(id => {
    const el = $(id); if(el) el.onchange = () => pushHistory();
  });

  if(isRectShape){
    $('pRadius').oninput = () => { o.set({rx:+$('pRadius').value, ry:+$('pRadius').value}); canvas.renderAll(); };
    $('pRadius').onchange = () => pushHistory();
  }

  const applyShadow = () => {
    if(!$('pShadowOn').checked){ o.set('shadow', null); canvas.renderAll(); return; }
    o.set('shadow', new fabric.Shadow({
      color: $('pShadowColor').value, blur: +$('pShadowBlur').value,
      offsetX: +$('pShadowX').value, offsetY: +$('pShadowY').value}));
    canvas.renderAll();
  };
  ['pShadowOn','pShadowColor','pShadowBlur','pShadowX','pShadowY'].forEach(id => {
    $(id).oninput = applyShadow;
    $(id).onchange = () => { applyShadow(); pushHistory(); };
  });

  if(isText){
    $('pItalic').onchange = () => { o.set('fontStyle', $('pItalic').checked?'italic':'normal'); canvas.renderAll(); pushHistory(); };
    $('pUnderline').onchange = () => { o.set('underline', $('pUnderline').checked); canvas.renderAll(); pushHistory(); };
    const applyTextBg = () => { o.set('textBackgroundColor', $('pTextBgOn').checked ? $('pTextBg').value : ''); canvas.renderAll(); };
    $('pTextBg').oninput = () => { applyTextBg(); };
    $('pTextBg').onchange = () => { applyTextBg(); pushHistory(); };
    $('pTextBgOn').onchange = () => {
      $('pTextBg').style.display = $('pTextBgOn').checked ? 'block' : 'none';
      applyTextBg(); pushHistory();
    };
  }

  if(isImage){
    const applyFilters = () => {
      o.filters = [];
      const b=+$('fBright').value, c=+$('fContrast').value, s=+$('fSat').value, bl=+$('fBlur').value;
      if(b) o.filters.push(new fabric.Image.filters.Brightness({brightness:b/100}));
      if(c) o.filters.push(new fabric.Image.filters.Contrast({contrast:c/100}));
      if(s) o.filters.push(new fabric.Image.filters.Saturation({saturation:s/100}));
      if(bl) o.filters.push(new fabric.Image.filters.Blur({blur:bl/100}));
      o.applyFilters(); canvas.renderAll();
    };
    ['fBright','fContrast','fSat','fBlur'].forEach(id => {
      $(id).oninput = applyFilters; $(id).onchange = pushHistory; });
    $('fGray').onclick = () => { o.filters=[new fabric.Image.filters.Grayscale()]; o.applyFilters(); canvas.renderAll(); pushHistory(); };
    $('fSepia').onclick= () => { o.filters=[new fabric.Image.filters.Sepia()]; o.applyFilters(); canvas.renderAll(); pushHistory(); };
    $('fNone').onclick = () => { o.filters=[]; o.applyFilters(); ['fBright','fContrast','fSat','fBlur'].forEach(id=>$(id).value=0); canvas.renderAll(); pushHistory(); };
    $('pCrop').onclick = () => enterCropMode(o);
    if($('pCropReset')) $('pCropReset').onclick = () => resetCrop(o);
    $('pAsBg').onclick = () => {
      centreScale(o, true); o.s1kind='background'; o.name='Background';
      canvas.sendToBack(o); canvas.renderAll(); pushHistory(); renderLayers();
    };
  }

  box.querySelectorAll('[data-al]').forEach(b => b.onclick = () => {
    const w=canvas.getWidth(), h=canvas.getHeight();
    const a=b.dataset.al;
    if(a==='left') o.set('left',0);
    if(a==='right') o.set('left', w-o.getScaledWidth());
    if(a==='centerH') o.set('left',(w-o.getScaledWidth())/2);
    if(a==='top') o.set('top',0);
    if(a==='bottom') o.set('top', h-o.getScaledHeight());
    if(a==='centerV') o.set('top',(h-o.getScaledHeight())/2);
    canvas.renderAll(); pushHistory(); syncProps();
  });
  $('pFront').onclick = () => { canvas.bringToFront(o); canvas.renderAll(); pushHistory(); renderLayers(); };
  $('pBack').onclick  = () => { canvas.sendToBack(o); canvas.renderAll(); pushHistory(); renderLayers(); };
  $('pFlipH').onclick = () => { o.set('flipX', !o.flipX); canvas.renderAll(); pushHistory(); };
  $('pFlipV').onclick = () => { o.set('flipY', !o.flipY); canvas.renderAll(); pushHistory(); };
  $('pDup').onclick   = () => o.clone(c => { c.set({left:o.left+22, top:o.top+22});
    c.s1kind=o.s1kind; c.name=(o.name||'Copy')+' copy'; canvas.add(c); canvas.setActiveObject(c); canvas.renderAll(); });
  $('pDel').onclick   = () => { canvas.remove(o); canvas.discardActiveObject(); canvas.renderAll(); };
  renderLayers();
}
function toHex(v){
  if(typeof v === 'string' && /^#[0-9a-f]{6}$/i.test(v)) return v;
  if(typeof v === 'string' && /^#[0-9a-f]{3}$/i.test(v))
    return '#'+v.slice(1).split('').map(c=>c+c).join('');
  return '#2563eb';
}
function applyColorToSelection(hex){
  const o = canvas.getActiveObject();
  if(!o) return alert('Select an object first, then click the color.');
  o.set('fill', hex); canvas.renderAll(); pushHistory(); syncProps();
}

/* ---------------- crop tool ----------------
   Fabric has no built-in crop UI, so this builds one: a draggable/resizable
   guide rect over the image plus four dark "mask" rects that dim everything
   outside it. On commit the guide rect's on-screen bounds are converted into
   Fabric's native cropX/cropY/width/height, which is non-destructive — the
   full original pixels stay in the underlying image element, so "Reset crop"
   can always get back to them (the spec's "original stays recoverable"). */
function enterCropMode(o){
  if(cropState) exitCropMode(false);
  if(o.angle % 360 !== 0){
    alert('Straighten the image (set rotation to 0°) before cropping.');
    return;
  }
  canvas.discardActiveObject();
  canvas.selection = false;
  canvas.getObjects().forEach(x => {
    x._cropPrevSelectable = x.selectable;
    x.selectable = false; x.evented = false;
  });

  const L = o.left, T = o.top, W = o.getScaledWidth(), H = o.getScaledHeight();
  const rect = new fabric.Rect({
    left:L, top:T, width:W, height:H, fill:'rgba(0,0,0,0)',
    stroke:'#2563eb', strokeWidth:2, strokeDashArray:[7,5],
    cornerColor:'#2563eb', cornerStyle:'circle', transparentCorners:false,
    lockRotation:true, hasBorders:false, _s1temp:true, name:'Crop guide'
  });
  rect.setControlsVisibility({mtr:false});
  const mask = () => new fabric.Rect({fill:'rgba(15,23,42,.55)', selectable:false,
    evented:false, _s1temp:true, name:'Crop mask'});
  const overlays = {top:mask(), bottom:mask(), left:mask(), right:mask()};

  cropState = {target:o, rect, overlays, imgLeft:L, imgTop:T, imgW:W, imgH:H};

  const updateOverlays = () => {
    const rL = rect.left, rT = rect.top, rW = rect.getScaledWidth(), rH = rect.getScaledHeight();
    overlays.top.set({left:L, top:T, width:W, height:Math.max(0, rT-T)});
    overlays.bottom.set({left:L, top:rT+rH, width:W, height:Math.max(0, (T+H)-(rT+rH))});
    overlays.left.set({left:L, top:rT, width:Math.max(0, rL-L), height:rH});
    overlays.right.set({left:rL+rW, top:rT, width:Math.max(0, (L+W)-(rL+rW)), height:rH});
    canvas.renderAll();
  };
  cropState.updateOverlays = updateOverlays;

  rect.on('moving', () => {
    const rW = rect.getScaledWidth(), rH = rect.getScaledHeight();
    rect.left = Math.min(Math.max(rect.left, L), L+W-rW);
    rect.top  = Math.min(Math.max(rect.top,  T), T+H-rH);
    updateOverlays();
  });
  rect.on('scaling', () => {
    const rW = rect.getScaledWidth(), rH = rect.getScaledHeight();
    if(rect.left < L) rect.left = L;
    if(rect.top  < T) rect.top  = T;
    if(rect.left + rW > L+W) rect.scaleX = (L+W-rect.left)/rect.width;
    if(rect.top  + rH > T+H) rect.scaleY = (T+H-rect.top)/rect.height;
    updateOverlays();
  });

  Object.values(overlays).forEach(r => canvas.add(r));
  canvas.add(rect);
  canvas.setActiveObject(rect);
  updateOverlays();
  renderCropPanel();
}
function setCropAspect(rw, rh){
  if(!cropState) return;
  const {imgLeft:L, imgTop:T, imgW:W, imgH:H, rect} = cropState;
  if(rw && rh){
    const targetRatio = rw/rh, imgRatio = W/H;
    const w = targetRatio > imgRatio ? W : H*targetRatio;
    const h = targetRatio > imgRatio ? W/targetRatio : H;
    rect.set({left:L+(W-w)/2, top:T+(H-h)/2, width:w, height:h, scaleX:1, scaleY:1});
  }
  canvas.renderAll();
  cropState.updateOverlays();
}
function commitCrop(){
  if(!cropState) return;
  const {target:o, rect, imgLeft:L, imgTop:T} = cropState;
  const rW = rect.getScaledWidth(), rH = rect.getScaledHeight();
  const localCropX = (rect.left - L) / o.scaleX;
  const localCropY = (rect.top  - T) / o.scaleY;
  const localCropW = rW / o.scaleX;
  const localCropH = rH / o.scaleY;
  o.set({
    cropX: (o.cropX||0) + localCropX,
    cropY: (o.cropY||0) + localCropY,
    width: localCropW,
    height: localCropH,
    left: rect.left,
    top: rect.top
  });
  o.setCoords();
  exitCropMode(true);
}
function exitCropMode(applied){
  if(!cropState) return;
  const {target:o, rect, overlays} = cropState;
  canvas.remove(rect);
  Object.values(overlays).forEach(r => canvas.remove(r));
  canvas.getObjects().forEach(x => {
    x.selectable = x._cropPrevSelectable !== undefined ? x._cropPrevSelectable : true;
    x.evented = true;
    delete x._cropPrevSelectable;
  });
  canvas.selection = true;
  cropState = null;
  canvas.setActiveObject(o);
  canvas.renderAll();
  if(applied) pushHistory();
  syncProps();
}
function resetCrop(o){
  const el = o._originalElement || o._element;
  if(!el){ alert('Original image data is not available to reset to.'); return; }
  o.set({cropX:0, cropY:0, width:el.naturalWidth||el.width, height:el.naturalHeight||el.height});
  o.setCoords(); canvas.renderAll(); pushHistory(); syncProps();
}
function renderCropPanel(){
  const box = $('propBody');
  box.innerHTML = `
    <div class="pgroup">
      <div class="t">Crop</div>
      <div class="hint">Drag the handles to adjust the crop area, or pick a ratio.</div>
      <div class="chips" id="cropAspect">
        <button class="chip on" data-r="0x0">Free</button>
        <button class="chip" data-r="1x1">1:1</button>
        <button class="chip" data-r="4x5">4:5</button>
        <button class="chip" data-r="16x9">16:9</button>
        <button class="chip" data-r="9x16">9:16</button>
        <button class="chip" data-r="3x2">3:2</button>
      </div>
      <div class="row" style="margin-top:14px">
        <button class="btn sec sm" id="cropCancel">Cancel</button>
        <button class="btn sm" id="cropDone">Done</button>
      </div>
    </div>`;
  box.querySelectorAll('#cropAspect .chip').forEach(c => c.onclick = () => {
    box.querySelectorAll('#cropAspect .chip').forEach(x => x.classList.toggle('on', x===c));
    const [rw,rh] = c.dataset.r.split('x').map(Number);
    setCropAspect(rw, rh);
  });
  $('cropCancel').onclick = () => exitCropMode(false);
  $('cropDone').onclick = commitCrop;
}

/* ---------------- smart guides (snap-to-edge / center / equal spacing) ----
   Fabric has no built-in alignment guides. This hooks object:moving,
   compares the dragged object's edges/centre against the canvas and every
   other object, snaps within a small on-screen threshold, and draws
   temporary guide lines — plus a basic equal-spacing check against the
   nearest flanking object on each axis. Guide lines are flagged _s1temp,
   the same convention the crop tool uses, so they never pollute undo
   history or the Layers panel. Optional per the original spec (§31); built
   as part of Batch D. ---------------- */
let guideLines = [];
const SNAP_PX = 8;                                    // on-screen pixels

function clearGuides(){
  if(!guideLines.length) return;
  guideLines.forEach(l => canvas.remove(l));
  guideLines = [];
}
function drawGuide(x1, y1, x2, y2){
  const line = new fabric.Line([x1, y1, x2, y2], {
    stroke: '#f43f5e', strokeWidth: 1/zoom, strokeDashArray: [4/zoom, 3/zoom],
    selectable: false, evented: false, _s1temp: true, name: 'Guide'
  });
  canvas.add(line);
  canvas.bringToFront(line);
  guideLines.push(line);
}
function objEdges(o){
  const b = o.getBoundingRect(true, true);
  return {left:b.left, right:b.left+b.width, centerX:b.left+b.width/2,
          top:b.top, bottom:b.top+b.height, centerY:b.top+b.height/2};
}
function nearestFlank(e, targets, axis){
  // axis 'x': nearest left/right neighbor whose vertical span overlaps this
  // object's — i.e. roughly "the same row". axis 'y' is the column version.
  let left=null, right=null, top=null, bottom=null;
  targets.forEach(t => {
    const te = objEdges(t);
    if(axis==='x'){
      if(Math.min(e.bottom,te.bottom) - Math.max(e.top,te.top) <= 0) return;
      if(te.right <= e.left && (!left || te.right > left.right)) left = te;
      if(te.left >= e.right && (!right || te.left < right.left)) right = te;
    } else {
      if(Math.min(e.right,te.right) - Math.max(e.left,te.left) <= 0) return;
      if(te.bottom <= e.top && (!top || te.bottom > top.bottom)) top = te;
      if(te.top >= e.bottom && (!bottom || te.top < bottom.top)) bottom = te;
    }
  });
  return axis==='x' ? {left, right} : {top, bottom};
}
function checkEqualSpacing(e, targets, threshold){
  const {left, right} = nearestFlank(e, targets, 'x');
  if(left && right){
    const gapL = e.left - left.right, gapR = right.left - e.right;
    if(gapL >= 0 && gapR >= 0 && Math.abs(gapL-gapR) <= threshold){
      const midY = e.centerY;
      drawGuide(left.right, midY, e.left, midY);
      drawGuide(e.right, midY, right.left, midY);
    }
  }
  const {top, bottom} = nearestFlank(e, targets, 'y');
  if(top && bottom){
    const gapT = e.top - top.bottom, gapB = bottom.top - e.bottom;
    if(gapT >= 0 && gapB >= 0 && Math.abs(gapT-gapB) <= threshold){
      const midX = e.centerX;
      drawGuide(midX, top.bottom, midX, e.top);
      drawGuide(midX, e.bottom, midX, bottom.top);
    }
  }
}
function applySmartGuides(o){
  clearGuides();
  if(!canvas || !o || o._s1temp) return;
  const threshold = SNAP_PX / zoom;
  const cw = canvas.getWidth(), ch = canvas.getHeight();
  const selected = canvas.getActiveObjects ? canvas.getActiveObjects() : [o];
  // Exclude the moving object itself explicitly, not just "whatever's
  // selected" — object:moving should always fire with o as the active
  // object, but never snap something against its own edges if that ever
  // isn't true (it would always win with distance 0 and block real snaps).
  const targets = canvas.getObjects().filter(x =>
    x !== o && !selected.includes(x) && !x._s1temp && x.s1kind !== 'background');
  const e = objEdges(o);

  const vCandidates = [{v:cw/2}, {v:0}, {v:cw}];
  const hCandidates = [{v:ch/2}, {v:0}, {v:ch}];
  targets.forEach(t => {
    const te = objEdges(t);
    vCandidates.push({v:te.left}, {v:te.centerX}, {v:te.right});
    hCandidates.push({v:te.top}, {v:te.centerY}, {v:te.bottom});
  });

  let bestDX = null, bestDY = null;
  ['left','centerX','right'].forEach(k => {
    vCandidates.forEach(c => {
      const d = c.v - e[k];
      if(Math.abs(d) <= threshold && (bestDX===null || Math.abs(d) < Math.abs(bestDX.d)))
        bestDX = {d, v:c.v};
    });
  });
  ['top','centerY','bottom'].forEach(k => {
    hCandidates.forEach(c => {
      const d = c.v - e[k];
      if(Math.abs(d) <= threshold && (bestDY===null || Math.abs(d) < Math.abs(bestDY.d)))
        bestDY = {d, v:c.v};
    });
  });
  if(bestDX) o.set('left', o.left + bestDX.d);
  if(bestDY) o.set('top', o.top + bestDY.d);
  if(bestDX || bestDY) o.setCoords();

  if(bestDX) drawGuide(bestDX.v, -40, bestDX.v, ch+40);
  if(bestDY) drawGuide(-40, bestDY.v, cw+40, bestDY.v);
  checkEqualSpacing(objEdges(o), targets, threshold);
}

/* ---------------- keyboard ---------------- */
document.addEventListener('keydown', e => {
  const typing = /input|textarea|select/i.test(document.activeElement.tagName);
  if(typing) return;
  const o = canvas && canvas.getActiveObject();
  const mod = e.ctrlKey || e.metaKey;
  if(mod && e.key.toLowerCase()==='z'){ e.preventDefault(); restore(histIndex-1); return; }
  if(mod && e.key.toLowerCase()==='y'){ e.preventDefault(); restore(histIndex+1); return; }
  if(mod && e.key.toLowerCase()==='d' && o){ e.preventDefault();
    o.clone(c => { c.set({left:o.left+22, top:o.top+22}); c.s1kind=o.s1kind;
      c.name=(o.name||'Copy')+' copy'; canvas.add(c); canvas.setActiveObject(c); canvas.renderAll(); }); return; }
  // Object copy/paste, distinct from Ctrl+D duplicate and from the window
  // 'paste' listener below (which handles pasting an image file from the
  // OS clipboard) — this copies the selected Fabric object itself.
  if(mod && e.key.toLowerCase()==='c' && o){ e.preventDefault();
    o.clone(c => { copiedObject = c; }); return; }
  if(mod && e.key.toLowerCase()==='v' && copiedObject){ e.preventDefault();
    copiedObject.clone(c => {
      c.set({left:(c.left||0)+22, top:(c.top||0)+22});
      c.s1kind = copiedObject.s1kind; c.name = (copiedObject.name||'Object')+' copy';
      canvas.add(c); canvas.setActiveObject(c); canvas.renderAll();
    }); return; }
  if(mod && e.key.toLowerCase()==='a'){ e.preventDefault();
    canvas.discardActiveObject();
    const sel = new fabric.ActiveSelection(canvas.getObjects().filter(x=>x.selectable!==false), {canvas});
    canvas.setActiveObject(sel); canvas.renderAll(); return; }
  if((e.key==='Delete'||e.key==='Backspace') && o){ e.preventDefault();
    canvas.remove(o); canvas.discardActiveObject(); canvas.renderAll(); return; }
  if(o && e.key.startsWith('Arrow')){
    e.preventDefault();
    const step = e.shiftKey ? 10 : 1;
    if(e.key==='ArrowLeft') o.set('left', o.left-step);
    if(e.key==='ArrowRight')o.set('left', o.left+step);
    if(e.key==='ArrowUp')   o.set('top',  o.top-step);
    if(e.key==='ArrowDown') o.set('top',  o.top+step);
    canvas.renderAll();
  }
});

/* ---------------- canvas size ---------------- */
$('btnResize').onclick = () => openSizeModal(false);
function openSizeModal(first){
  const groups = {};
  PRESETS.filter(p => p.key!=='custom').forEach(p => (groups[p.group] ||= []).push(p));
  $('sizeBody').innerHTML = Object.entries(groups).map(([g, list]) =>
      `<div class="gnote">${esc(g)}</div><div class="sizes">` + list.map(p =>
        `<div class="sizeopt" data-w="${p.w}" data-h="${p.h}"><b>${esc(p.label)}</b><span>${p.w} × ${p.h}</span></div>`).join('')
      + '</div>').join('')
    + `<div class="gnote">Custom</div>
       <div class="row"><div><label class="f">Width</label><input type="number" id="cw" value="${canvas?canvas.getWidth():1080}" min="40" max="6000"></div>
       <div><label class="f">Height</label><input type="number" id="ch" value="${canvas?canvas.getHeight():1080}" min="40" max="6000"></div></div>`;
  $('sizeBody').querySelectorAll('.sizeopt').forEach(el => el.onclick = () => {
    $('sizeBody').querySelectorAll('.sizeopt').forEach(x => x.classList.remove('on'));
    el.classList.add('on');
    $('cw').value = el.dataset.w; $('ch').value = el.dataset.h;
  });
  $('sizeWarn').textContent = first ? '' : 'Resizing keeps your objects where they are.';
  // Only ask on a NEW image; resizing an open canvas shouldn't re-ask.
  var ncRow = $('newClientRow');
  if(ncRow) ncRow.style.display = first ? '' : 'none';
  $('sizeModal').classList.add('open');
  $('sizeApply').onclick = () => {
    const w = Math.max(40, Math.min(+$('cw').value||1080, 6000));
    const h = Math.max(40, Math.min(+$('ch').value||1080, 6000));
    if(!canvas || first){ initCanvas(w,h); }
    else { canvas.setWidth(w); canvas.setHeight(h);
           $('canvasLabel').textContent = w+' × '+h; fitZoom(); pushHistory(); }
    $('sizeModal').classList.remove('open');
  };
}

/* Same lookup the save dialog uses, on the new-image step. Whatever is picked
   here pre-selects the client at save time, so the attach isn't a second
   thing to remember. */
var newClientTimer = null;
(function(){
  var box = $('newClient');
  if(!box) return;
  box.addEventListener('input', function(){
    clearTimeout(newClientTimer);
    newClientTimer = setTimeout(searchNewClient, 220);
  });
  box.addEventListener('blur', function(){
    setTimeout(function(){ var d=$('newClientDrop'); if(d) d.style.display='none'; }, 150);
  });
})();

async function searchNewClient(){
  var q = $('newClient').value.trim();
  var drop = $('newClientDrop');
  if(q.length < 2){ drop.style.display='none'; return; }
  try{
    var d = await fetch('api/clients?q='+encodeURIComponent(q)).then(function(r){return r.json();});
    if(!d.clients || !d.clients.length){
      drop.innerHTML = '<div style="padding:8px 10px;color:var(--muted)">No client matches — '
        + 'leave blank to save it unattached.</div>';
      drop.style.display='block'; return;
    }
    drop.innerHTML = d.clients.map(function(c){
      return '<div data-c=\''+esc(JSON.stringify(c))+'\'><b>'+esc(c.name)+'</b>'
        + '<div style="color:var(--muted);font-size:11px">'+esc(c.domain||'')+'</div></div>';
    }).join('');
    drop.style.display='block';
    drop.querySelectorAll('[data-c]').forEach(function(el){
      el.onmousedown = function(ev){
        ev.preventDefault();
        chosenClient = JSON.parse(el.dataset.c);
        $('newClient').value = chosenClient.name;
        $('newClientTag').textContent = 'This image will be filed under ' + chosenClient.name + '.';
        drop.style.display = 'none';
        // Carry it to the save dialog so it isn't asked twice.
        if($('svClient')) $('svClient').value = chosenClient.name;
        if($('svClientTag')) $('svClientTag').textContent = 'Saved against ' + chosenClient.name;
      };
    });
  }catch(e){ drop.style.display='none'; }
}
document.querySelectorAll('[data-close]').forEach(b => b.onclick = () =>
  b.closest('.modal').classList.remove('open'));
document.querySelectorAll('.modal').forEach(m => m.onclick = e => {
  if(e.target===m) m.classList.remove('open'); });

/* ---------------- download ---------------- */
let dlFmt='png', dlScale=1;
$('btnDownload').onclick = () => {
  $('dlModal').classList.add('open');
  updateDlUi();
};
$('dlFormat').querySelectorAll('.chip').forEach(c => c.onclick = () => {
  dlFmt = c.dataset.f;
  $('dlFormat').querySelectorAll('.chip').forEach(x => x.classList.toggle('on', x===c));
  updateDlUi();
});
$('dlScale').querySelectorAll('.chip').forEach(c => c.onclick = () => {
  dlScale = +c.dataset.s;
  $('dlScale').querySelectorAll('.chip').forEach(x => x.classList.toggle('on', x===c));
  updateDlUi();
});
$('dlQuality').oninput = () => { $('dlQLabel').textContent = $('dlQuality').value+'%'; };
function hasBackground(){
  return !!canvas.backgroundColor || canvas.getObjects().some(o => o.s1kind==='background');
}
function updateDlUi(){
  $('dlQualityWrap').style.display = dlFmt==='png' ? 'none' : 'block';
  const canTrans = dlFmt!=='jpeg' && !hasBackground();
  $('dlTransparent').disabled = !canTrans;
  if(!canTrans) $('dlTransparent').checked = false;
  $('dlSize').textContent = 'Output: ' + (canvas.getWidth()*dlScale) + ' × ' + (canvas.getHeight()*dlScale) + ' px'
    + (canTrans ? '' : (dlFmt==='jpeg' ? ' · JPG has no transparency' : ' · a background layer is set'));
}
function exportDataURL(){
  const opts = {format: dlFmt, multiplier: dlScale};
  if(dlFmt!=='png') opts.quality = (+$('dlQuality').value)/100;
  const bg = canvas.backgroundColor;
  if($('dlTransparent').checked) canvas.backgroundColor = null;
  else if(dlFmt==='jpeg' && !hasBackground()) canvas.backgroundColor = '#ffffff';
  canvas.renderAll();
  const url = canvas.toDataURL(opts);
  canvas.backgroundColor = bg; canvas.renderAll();
  return url;
}
$('dlGo').onclick = async () => {
  $('dlGo').disabled = true;
  try{
    let url = exportDataURL();
    // Best-effort server-side optimization pass — a real compress/strip-
    // metadata step before the file leaves the browser, the gap noted
    // against the spec's Sharp-based description (§37). Re-saving through
    // Pillow drops any metadata for free; if the request fails for any
    // reason the raw client-rendered export still downloads untouched, so
    // this can never turn a working download into a broken one.
    try{
      $('dlMsg').textContent = 'Optimizing…';
      const opt = await fetch('api/export/optimize', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({image:url, format:dlFmt, quality:+$('dlQuality').value||92})
      }).then(r=>r.json());
      if(opt && opt.image) url = opt.image;
    }catch(e){ /* optimization is optional — fall through with the raw export */ }
    const a = document.createElement('a');
    a.href = url;
    a.download = ($('projName').value.trim() || 'image').replace(/[^\w -]/g,'').trim()
                 + '.' + (dlFmt==='jpeg'?'jpg':dlFmt);
    a.click();
    $('dlMsg').textContent = 'Downloaded ✓';
  }catch(e){ $('dlMsg').textContent = 'Export failed: '+e.message; }
  $('dlGo').disabled = false;
};
$('dlCloud').onclick = async () => {
  if(!CLOUD_ON){ $('dlMsg').textContent='Cloudinary isn\'t configured.'; return; }
  $('dlCloud').disabled = true; $('dlMsg').textContent = 'Uploading…';
  try{
    const d = await fetch('api/export/cloudinary', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({image: exportDataURL(),
        name: $('projName').value.trim() || 'export',
        client: chosenClient ? chosenClient.name : '',
        project: projectId})}).then(r=>r.json());
    $('dlCloud').disabled = false;
    if(d.error){ $('dlMsg').textContent = d.error; return; }
    $('dlMsg').innerHTML = 'Saved ✓ <a href="'+esc(d.url)+'" target="_blank" rel="noopener">open</a>';
  }catch(e){ $('dlCloud').disabled=false; $('dlMsg').textContent='Upload failed.'; }
};

/* ---------------- save project ---------------- */
$('btnSave').onclick = () => {
  $('svName').value = $('projName').value.trim();
  $('svInfo').textContent = canvas.getObjects().length + ' object(s) · '
    + canvas.getWidth() + ' × ' + canvas.getHeight();
  $('saveModal').classList.add('open');
};
let clientTimer;
$('svClient').addEventListener('input', () => {
  chosenClient = null; $('svClientTag').textContent='';
  clearTimeout(clientTimer); clientTimer = setTimeout(searchClients, 220);
});
$('svClient').addEventListener('blur', () => setTimeout(()=> $('svClientDrop').style.display='none', 170));
async function searchClients(){
  const q = $('svClient').value.trim();
  const drop = $('svClientDrop');
  if(q.length < 2){ drop.style.display='none'; return; }
  try{
    const d = await fetch('api/clients?q='+encodeURIComponent(q)).then(r=>r.json());
    if(!d.clients || !d.clients.length){ drop.style.display='none'; return; }
    drop.innerHTML = d.clients.map(c => `<div data-c='${esc(JSON.stringify(c))}'>
      <b>${esc(c.name)}</b> ${c.is_house?'<span class="cbadge house">house</span>':''}${c.is_seo?'<span class="cbadge seo">SEO</span>':''}
      <div style="color:var(--muted);font-size:11px">${esc(c.domain||'')}</div></div>`).join('');
    drop.style.display='block';
    drop.querySelectorAll('[data-c]').forEach(el => el.onmousedown = ev => {
      ev.preventDefault();
      chosenClient = JSON.parse(el.dataset.c);
      $('svClient').value = chosenClient.name;
      $('svClientTag').textContent = 'Saved against ' + chosenClient.name;
      drop.style.display='none';
    });
  }catch(e){ drop.style.display='none'; }
}
$('svGo').onclick = async () => {
  const name = $('svName').value.trim();
  if(!name){ $('svMsg').textContent = 'Give the project a name.'; return; }
  $('svGo').disabled = true; $('svMsg').textContent = 'Saving…';
  try{
    const preview = canvas.toDataURL({format:'jpeg', quality:0.72,
      multiplier: Math.min(1, 600/Math.max(canvas.getWidth(), canvas.getHeight()))});
    const d = await fetch('api/projects', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        id: projectId, name, preview,
        canvas: canvas.toJSON(['name','s1kind','s1meta']),
        client: chosenClient ? chosenClient.name : $('svClient').value.trim(),
        client_slug: chosenClient ? chosenClient.slug : '',
        tags: $('svTags').value, width: canvas.getWidth(), height: canvas.getHeight()
      })}).then(r=>r.json());
    $('svGo').disabled = false;
    if(d.error){ $('svMsg').textContent = d.error; return; }
    projectId = d.project.id;
    $('projName').value = d.project.name;
    $('svMsg').textContent = 'Saved ✓';
    $('saveMsg').textContent = 'Saved ' + d.project.updated;
    setTimeout(() => $('saveModal').classList.remove('open'), 800);
  }catch(e){ $('svGo').disabled=false; $('svMsg').textContent='Save failed: '+e; }
};

/* ---------------- boot ---------------- */
(async function boot(){
  if(OPEN_PROJECT){
    try{
      const d = await fetch('api/projects/'+encodeURIComponent(OPEN_PROJECT)).then(r=>r.json());
      if(d.canvas){
        const w = d.project.width || d.canvas.width || 1080;
        const h = d.project.height || d.canvas.height || 1080;
        initCanvas(w,h);
        histLock = true;
        canvas.loadFromJSON(d.canvas, () => {
          canvas.renderAll(); histLock=false; history=[]; histIndex=-1; pushHistory();
          renderLayers();
        });
        projectId = d.project.id || '';
        $('projName').value = d.project.name || '';
        if(d.project.client){
          chosenClient = {name:d.project.client, slug:d.project.client_slug||'', domain:''};
          $('svClient').value = d.project.client;
        }
        openPanel('layers');
        return;
      }
    }catch(e){ /* fall through to a new canvas */ }
  }
  initCanvas(1080, 1080);
  if(PREFILL_CLIENT){
    try{
      const d = await fetch('api/clients?q='+encodeURIComponent(PREFILL_CLIENT)).then(r=>r.json());
      const hit = (d.clients||[]).find(c => c.name.toLowerCase()===PREFILL_CLIENT.toLowerCase());
      if(hit){ chosenClient = hit; $('svClient').value = hit.name; }
    }catch(e){}
  }
  openPanel('upload');
  openSizeModal(true);
})();
