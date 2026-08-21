// For local testing leave blank; for a cross-domain embed set to your Render URL.
const API_BASE = window.SMART1RV_API_BASE || '';
const LEAD_ID = (function(){ try { return crypto.randomUUID(); } catch(e){ return 'rv-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8); } })();

// Attribution captured once at load (merged into every payload, additive).
const ATTR = (function(){
  const q = new URLSearchParams(location.search);
  const keys = ['utm_source','utm_medium','utm_campaign','utm_term','utm_content','gclid','fbclid'];
  const o = {};
  keys.forEach(function(k){ const v = q.get(k); if (v) o[k] = v.slice(0,200); });
  o.referrer_url = (document.referrer || '').slice(0,300);
  o.landing_page_url = (q.get('s1_ref') || location.href).slice(0,300);
  return o;
})();

// Warm the server the instant the page loads so a Render cold start finishes
// while the visitor is still filling out the form (avoids the first-submit stall).
(function warmup(){ try { fetch(`${API_BASE}/health`, { method:'GET', cache:'no-store', keepalive:true }).catch(function(){}); } catch(e){} })();

const ALL_WEATHER_TRIGGERS = ['First 50°–60° forecast','60°+ forecast','70°+ weekend','Sunny weekend','Holiday weekend forecast','Heavy rain','Thunderstorms','High wind','Severe storm watch','85°+ forecast','90°+ heat','Heat advisory','Heat index 100°+','First frost','Frost warning','Freeze warning','Ice storm','Snow forecast','Cold snap under 45°','Tropical storm or hurricane watch','Snowbird season'];
const AUDIENCE_TARGETING = [['In-Market RV Buyer Data','households actively shopping for RVs'],['Campground & State-Park Geotargeting','reach campers where they camp'],['Location Look-Back Retargeting','recent campground / RV-park visitors']];
const PACKAGE_TIERS = [
  { key:'Starter', amount:3500, tagline:'Best for smaller or single-location markets testing weather-triggered demand.', includes:['Core CTV, Streaming Radio & Targeted Display','Primary weather triggers for your region','Campground & state-park geotargeting'] },
  { key:'Growth', amount:5000, tagline:'Our most popular level — the full weather-triggered stack with strong peak-season weight.', includes:['Everything in Starter','Podcasts + Location Look-Back Retargeting','Broader weather-trigger coverage','Higher share of voice in peak weeks'] },
  { key:'Premium', amount:7500, tagline:'Best for larger or multi-location dealers and competitive markets.', includes:['Everything in Growth','Digital Out-of-Home (DOOH) at local bars, restaurants, gas & shopping','Maximum trigger coverage','Highest peak-season media weight'] }
];
const CAMPFIRE = '<div class="cf-scene"><svg class="cf-svg" viewBox="0 0 160 160" xmlns="http://www.w3.org/2000/svg">'
  +'<ellipse class="cf-glow" cx="80" cy="118" rx="52" ry="20" fill="#F0A93B"/>'
  +'<g><rect x="40" y="120" width="80" height="12" rx="6" fill="#7a4a24" transform="rotate(-11 80 126)"/>'
  +'<rect x="40" y="120" width="80" height="12" rx="6" fill="#98622f" transform="rotate(11 80 126)"/></g>'
  +'<path class="cf-flame cf-f1" d="M80 44 C58 80,58 106,80 124 C102 106,102 80,80 44 Z" fill="#E8532B"/>'
  +'<path class="cf-flame cf-f2" d="M80 64 C67 88,67 106,80 120 C93 106,93 88,80 64 Z" fill="#F5A623"/>'
  +'<path class="cf-flame cf-f3" d="M80 82 C73 96,73 108,80 118 C87 108,87 96,80 82 Z" fill="#FFD65A"/>'
  +'<circle class="cf-spark cf-s1" cx="66" cy="86" r="2.4"/><circle class="cf-spark cf-s2" cx="94" cy="80" r="2"/>'
  +'<circle class="cf-spark cf-s3" cx="80" cy="74" r="2.6"/><circle class="cf-spark cf-s4" cx="72" cy="92" r="1.8"/>'
  +'</svg><p class="cf-text">Calculating Marketing Conditions<span class="cf-dots"></span></p>'
  +'<p class="cf-sub">Reading your market, weather patterns &amp; campground data…</p></div>';

const form = document.getElementById('rvDemandForm');
const steps = document.querySelectorAll('.srv-step');
const progressBar = document.getElementById('srvProgressBar');
const buildButton = document.getElementById('buildOpportunityBtn');
const box = document.getElementById('marketOpportunityResult');
const unlockPanel = document.getElementById('srvUnlock');
const unlockBtn = document.getElementById('srvUnlockBtn');
const unlockErr = document.getElementById('srvUnlockErr');
const resultActions = document.getElementById('srvResultActions');
const downloadButton = document.getElementById('downloadReportBtn');
const pillError = document.getElementById('srvPillError');

let currentStep = 1, estimateData = null, proposalPdfUrl = '';

function showStep(step){steps.forEach(s=>s.classList.toggle('active', Number(s.dataset.step)===step));currentStep=step;progressBar.style.width=`${Math.min((step/steps.length)*100,100)}%`;}

document.querySelectorAll('.srv-pillgroup').forEach(group => {
  const hidden = document.querySelector(`input[name="${group.dataset.name}"]`);
  group.querySelectorAll('.srv-pill').forEach(pill => pill.addEventListener('click', () => {
    group.querySelectorAll('.srv-pill').forEach(p=>p.classList.remove('is-selected'));
    pill.classList.add('is-selected');
    if (hidden) hidden.value = pill.dataset.value;
    if (pillError) pillError.hidden = true;
  }));
});
function validatePillGroups(activeStep){let ok=true;activeStep.querySelectorAll('.srv-pillgroup[data-required="true"]').forEach(g=>{const h=document.querySelector(`input[name="${g.dataset.name}"]`);if(!h||!h.value)ok=false;});if(!ok&&pillError)pillError.hidden=false;return ok;}
function validateCurrentStep(){const a=document.querySelector(`.srv-step[data-step="${currentStep}"]`);const req=a.querySelectorAll('input[required], select[required], textarea[required]');for(const f of req){if(f.type!=='hidden'&&!f.value){f.reportValidity();return false;}}if(!validatePillGroups(a))return false;return true;}

function getPayload(extra){const data=new FormData(form);const p=Object.fromEntries(data.entries());p.weather_triggers=ALL_WEATHER_TRIGGERS.slice();p.lead_id=LEAD_ID;Object.assign(p,ATTR);if(extra)Object.assign(p,extra);return p;}
function fmt(v){return Number(v||0).toLocaleString();}
function money(v){return '$'+Number(v||0).toLocaleString('en-US');}
function esc(v){return String(v==null?'':v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function chips(list){if(!Array.isArray(list)||!list.length)return '';return `<div class="srv-chips">${list.map(i=>`<span class="srv-chip">${esc(i)}</span>`).join('')}</div>`;}
function pkgBase(e){if(Number(e.base_monthly_budget)>0)return Number(e.base_monthly_budget);const m=String(e.recommended_package||'').replace(/,/g,'').match(/\$?\s*(\d{3,6})/);return m?Number(m[1]):5000;}
function validEmail(v){return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(v||'').trim());}

function buildPackageDetails(e){
  const rec=pkgBase(e);const ch=Array.isArray(e.recommended_channels)?e.recommended_channels:[];
  const cl=ch.length?`<ul class="srv-detail-list">${ch.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>`:'';
  const tiers=PACKAGE_TIERS.map(t=>{const r=t.amount===rec;return `<div class="srv-tier${r?' srv-tier-rec':''}"><div class="srv-tier-head"><span class="srv-tier-name">${esc(t.key)}</span>${r?'<span class="srv-tier-badge">Recommended</span>':''}</div><div class="srv-tier-price">${money(t.amount)}<span>/month</span></div><p class="srv-tier-tagline">${esc(t.tagline)}</p><ul class="srv-detail-list">${t.includes.map(i=>`<li>${esc(i)}</li>`).join('')}</ul></div>`;}).join('');
  return `<button type="button" class="srv-details-toggle" id="srvPkgDetailsToggle" aria-expanded="false">See package details</button><div class="srv-details" id="srvPkgDetails" hidden><h4>What’s included in your SmartForecast</h4><p>Your monthly fund runs weather-triggered advertising across the channels we recommend for your market:</p>${cl}<ul class="srv-detail-list"><li><strong>Data-driven audience targeting</strong> — in-market RV buyer data, campground &amp; state-park geotargeting, and location look-back retargeting.</li><li><strong>Weather-triggered activation</strong> — ads turn on when your selected weather conditions hit and pause when they don’t.</li><li><strong>Campaign management &amp; optimization</strong> — Smart 1 builds, runs, and tunes the campaigns for you.</li><li><strong>Reporting</strong> — track delivery and performance throughout the campaign.</li><li><strong>Rollover protection</strong> — unused media from poor-weather windows rolls into the next month.</li><li><strong>Off-season off-ramp</strong> — leftover peak-season budget can shift to winterization, spring prep, A/C or generator service campaigns, or carry 100% forward as credit.</li></ul><h4>Package levels</h4><div class="srv-tier-grid">${tiers}</div><p class="srv-detail-fineprint">All levels are month-to-month SmartForecast subscriptions. Your recommended level is based on your market size and estimated opportunity — you can move up or down after a strategy review.</p></div>`;
}
function wirePackageDetails(){const t=document.getElementById('srvPkgDetailsToggle');const d=document.getElementById('srvPkgDetails');if(!t||!d)return;t.addEventListener('click',()=>{const h=d.hasAttribute('hidden');if(h){d.removeAttribute('hidden');t.textContent='Hide package details';}else{d.setAttribute('hidden','');t.textContent='See package details';}});}

function tiersHtml(e){
  const rec=pkgBase(e);
  return PACKAGE_TIERS.map(t=>{const r=t.amount===rec;return `<div class="srv-tier${r?' srv-tier-rec':''}"><div class="srv-tier-head"><span class="srv-tier-name">${esc(t.key)}</span>${r?'<span class="srv-tier-badge">Recommended</span>':''}</div><div class="srv-tier-price">${money(t.amount)}<span>/month</span></div><p class="srv-tier-tagline">${esc(t.tagline)}</p><ul class="srv-detail-list">${t.includes.map(i=>`<li>${esc(i)}</li>`).join('')}</ul></div>`;}).join('');
}
function compareBarHtml(e){
  const base=pkgBase(e);
  const plan=Number(e.suggested_budget_total)||0;
  const flat=base*12;
  if(!flat||!plan)return '';
  const frac=Math.min(100,Math.max(10,Math.round((plan/flat)*100)));
  const pctLess=Math.max(0,Math.round((1-plan/flat)*100));
  return `<div class="srv-report-section"><h3>How Your Budget Works Harder</h3><div class="srv-bar-row"><span class="srv-bar-label">Always-on flat schedule at peak rate</span><div class="srv-bar-track"><div class="srv-bar-fill srv-bar-flat" style="width:100%"><span>${money(flat)}/yr</span></div></div></div><div class="srv-bar-row"><span class="srv-bar-label">Weather-triggered plan</span><div class="srv-bar-track"><div class="srv-bar-fill srv-bar-plan" style="width:${frac}%"><span>${money(plan)}/yr</span></div></div></div><p class="srv-budget-note">That’s ${pctLess}% less budget for the same peak-season presence — spend steps down automatically when demand is low, instead of running flat all year.</p></div>`;
}
const FOOTER_HTML='<p class="srv-report-footer">Smart 1 Marketing · (614) 536-0768 · <a href="https://smart1marketing.com" target="_blank" rel="noopener">smart1marketing.com</a></p>';
function regionHtml(e){return e.market_climate_region?`<div class="srv-report-section srv-region"><span class="srv-region-badge">${esc(e.market_climate_region)}</span>${e.market_region_reason?`<p>${esc(e.market_region_reason)}</p>`:''}</div>`:'';}
function statsHtml(e){return `<div class="srv-stat-grid"><div class="srv-stat"><span class="opportunity-number">${fmt(e.campground_count_low)}–${fmt(e.campground_count_high)}</span><span class="srv-stat-label">campgrounds &amp; RV parks</span></div><div class="srv-stat"><span class="opportunity-number">${fmt(e.estimated_site_count_low)}–${fmt(e.estimated_site_count_high)}</span><span class="srv-stat-label">estimated RV / camping sites</span></div><div class="srv-stat"><span class="opportunity-number">${fmt(e.estimated_peak_season_reach_low)}–${fmt(e.estimated_peak_season_reach_high)}</span><span class="srv-stat-label">estimated peak-season camper reach</span></div></div>`;}

function renderPreview(e){
  const d=(getPayload().dealership_name||'your dealership').trim();
  box.innerHTML=`<p class="srv-report-intro">Here’s a preview of ${esc(d)}’s weather-triggered RV demand opportunity:</p>${regionHtml(e)}${statsHtml(e)}<div class="srv-teaser"><p class="srv-teaser-h">🔒 Your full report also includes:</p><ul><li>Recommended package &amp; suggested monthly budget</li><li>Recommended media channels &amp; audience/data targeting</li><li>Best weather triggers for your market</li><li>A month-by-month campaign plan &amp; budget</li><li>A downloadable PDF proposal</li></ul></div>`;
  unlockPanel.style.display='block';resultActions.style.display='none';
}

function renderFull(e){
  const d=(getPayload().dealership_name||'your dealership').trim();
  const summary=e.dealer_summary?`<div class="srv-report-section"><h3>Your Market Opportunity</h3><p>${esc(e.dealer_summary)}</p></div>`:'';
  const pkg=e.recommended_package?`<div class="srv-report-section srv-package"><h3>Recommended Package</h3><p class="srv-package-name">${esc(e.recommended_package)}</p>${e.recommended_package_reason?`<p>${esc(e.recommended_package_reason)}</p>`:''}${buildPackageDetails(e)}</div>`:'';
  const channels=(Array.isArray(e.recommended_channels)&&e.recommended_channels.length)?`<div class="srv-report-section"><h3>Recommended Media Channels</h3>${chips(e.recommended_channels)}</div>`:'';
  const targeting=`<div class="srv-report-section"><h3>Audience &amp; Data Targeting</h3><p>Every campaign is powered by data-driven audience targeting:</p><ul class="srv-detail-list">${AUDIENCE_TARGETING.map(t=>`<li><strong>${esc(t[0])}</strong> — ${esc(t[1])}</li>`).join('')}</ul></div>`;
  const triggers=(Array.isArray(e.best_weather_triggers)&&e.best_weather_triggers.length)?`<div class="srv-report-section"><h3>Recommended Weather Triggers for Your Market</h3>${chips(e.best_weather_triggers)}</div>`:'';
  const budget=e.base_monthly_budget_text?`<div class="srv-report-section srv-budget"><h3>Suggested Media Budget</h3><div class="srv-budget-grid"><div class="srv-budget-tile"><span class="srv-budget-amount">${esc(e.base_monthly_budget_text)}/mo</span><span class="srv-stat-label">at peak demand</span></div><div class="srv-budget-tile"><span class="srv-budget-amount">${esc(e.average_monthly_budget_text||'')}/mo</span><span class="srv-stat-label">blended average</span></div><div class="srv-budget-tile"><span class="srv-budget-amount">${esc(e.suggested_budget_total_text||'')}</span><span class="srv-stat-label">plan total (${esc(String(e.suggested_budget_months||''))} months)</span></div></div>${e.budget_note?`<p class="srv-budget-note">${esc(e.budget_note)}</p>`:''}</div>`:'';
  let plan='';
  if(Array.isArray(e.month_by_month_plan)&&e.month_by_month_plan.length){
    const rows=e.month_by_month_plan.map(row=>`<tr><td class="srv-plan-month">${esc(row.month)}${row.season?`<div class="srv-plan-season">${esc(row.season)}</div>`:''}</td><td><strong>${esc(row.campaign_focus)}</strong><div class="srv-plan-message">${esc(row.recommended_message)}</div>${chips(row.weather_triggers)}</td><td class="srv-plan-budget">${esc(row.suggested_budget_text||'')}</td></tr>`).join('');
    plan=`<div class="srv-report-section"><h3>Month-by-Month Campaign Plan &amp; Budget</h3><table class="srv-plan-table"><thead><tr><th>Month</th><th>Focus &amp; Triggers</th><th>Budget</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  const disc=e.estimate_disclaimer?`<p class="disclaimer">${esc(e.estimate_disclaimer)}</p>`:'';
  const bars=compareBarHtml(e);
  const tierSection=`<div class="srv-report-section"><h3>Package Levels — Good · Better · Best</h3><div class="srv-tier-grid">${tiersHtml(e)}</div></div>`;
  box.innerHTML=`<p class="srv-report-intro">Based on ${esc(d)}’s ZIP code, selected buyer radius, and regional campground density, here’s your full weather-triggered RV demand plan:</p>${regionHtml(e)}${statsHtml(e)}${summary}${pkg}${channels}${targeting}${triggers}${bars}${budget}${tierSection}${plan}${disc}${FOOTER_HTML}`;
  wirePackageDetails();unlockPanel.style.display='none';resultActions.style.display='flex';
}

async function postLead(extra){
  const response=await fetch(`${API_BASE}/api/rv-demand/estimate-and-submit`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(getPayload(extra))});
  const result=await response.json();
  if(!response.ok||!result.ok)throw new Error(result.detail||result.error||'Submission failed');
  return result;
}

function loadHtml2Pdf(){return new Promise((res,rej)=>{if(window.html2pdf)return res();const s=document.createElement('script');s.src='https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js';s.onload=()=>res();s.onerror=()=>rej(new Error('Could not load the PDF library.'));document.head.appendChild(s);});}
function safeName(n){return String(n||'RV Dealer').replace(/[\\/:*?"<>|]+/g,'').replace(/\s+/g,' ').trim();}
async function downloadProposal(){
  // Preferred path: the server-generated proposal PDF (Cloudinary/GHL URL returned
  // by the unlock call). html2pdf below is only a fallback when no URL exists.
  if(proposalPdfUrl){window.open(proposalPdfUrl,'_blank','noopener');return;}
  if(!estimateData)return;
  const d=(getPayload().dealership_name||'RV Dealer').trim();
  const filename=`${safeName(d)} Weather Marketing Proposal.pdf`;
  const header=document.createElement('div');header.className='srv-pdf-header';
  header.innerHTML=`<div class="srv-pdf-title">${esc(d)}</div><div class="srv-pdf-sub">Weather Marketing Proposal</div><div class="srv-pdf-meta">Prepared by Smart 1 Marketing &middot; ${new Date().toLocaleDateString()}</div>`;
  box.insertBefore(header, box.firstChild);
  const toggle=document.getElementById('srvPkgDetailsToggle');const td=toggle?toggle.style.display:null;if(toggle)toggle.style.display='none';
  const prevBg=box.style.background;box.style.background='#fff';
  const orig=downloadButton?downloadButton.textContent:'';if(downloadButton){downloadButton.disabled=true;downloadButton.textContent='Preparing PDF…';}
  try{
    await loadHtml2Pdf();
    await window.html2pdf().set({margin:[10,10,12,10],filename,image:{type:'jpeg',quality:0.98},html2canvas:{scale:2,useCORS:true,backgroundColor:'#ffffff'},jsPDF:{unit:'mm',format:'a4',orientation:'portrait'},pagebreak:{mode:['css','legacy']}}).from(box).save();
  }catch(err){alert('Sorry — the PDF could not be generated. '+err.message);}
  finally{if(header.parentNode)header.parentNode.removeChild(header);if(toggle)toggle.style.display=td||'';box.style.background=prevBg;if(downloadButton){downloadButton.disabled=false;downloadButton.textContent=orig;}}
}

document.querySelectorAll('.next-btn').forEach(b=>b.addEventListener('click',()=>{if(!validateCurrentStep())return;showStep(currentStep+1);}));
document.querySelectorAll('.prev-btn').forEach(b=>b.addEventListener('click',()=>showStep(Math.max(currentStep-1,1))));
if(downloadButton)downloadButton.addEventListener('click',downloadProposal);

buildButton.addEventListener('click',async()=>{
  if(!validateCurrentStep())return;
  showStep(3);unlockPanel.style.display='none';resultActions.style.display='none';box.innerHTML=CAMPFIRE;buildButton.disabled=true;
  try{
    const result=await postLead({lead_stage:'Preview — email not yet provided'});
    previewSent=true; // the preview POST doubles as the partial-lead capture
    estimateData=result.estimate;proposalPdfUrl=result.proposal_pdf_url||'';
    renderPreview(estimateData);
  }catch(error){
    box.innerHTML=`<p>We could not complete the automated estimate right now.</p><p>Error: ${esc(error.message)}</p>`;
    console.error(error);
  }finally{buildButton.disabled=false;}
});

unlockBtn.addEventListener('click',async()=>{
  const emailEl=document.getElementById('srvEmail');
  if(!validEmail(emailEl.value)){unlockErr.hidden=false;emailEl.focus();return;}
  unlockErr.hidden=true;
  // Show the full report immediately — the unlock POST (which also generates the
  // server PDF) runs after and just enriches the lead + fills in the download URL.
  renderFull(estimateData);
  unlockBtn.disabled=true;
  try{
    const result=await postLead({lead_stage:'Full Report Unlocked', reuse_estimate:estimateData, reuse_pdf_url:proposalPdfUrl});
    if(result&&result.proposal_pdf_url)proposalPdfUrl=result.proposal_pdf_url;
  }
  catch(err){ console.error('Enrich failed:',err); }
  finally{ unlockBtn.disabled=false; }
});

// ---- Partial lead capture (SPEC §3) ----------------------------------------
// If the visitor filled step 1 (dealership + ZIP) but never clicked Build, fire
// the same preview POST once via sendBeacon when the page is hidden/closed.
let previewSent=false, partialSent=false;
function sendPartialBeacon(){
  if(previewSent||partialSent)return;
  let p;
  try{ p=getPayload({lead_stage:'Preview — email not yet provided'}); }catch(e){ return; }
  if(!p.dealership_name||!p.zip)return;
  partialSent=true;
  const body=JSON.stringify(p);
  try{
    if(navigator.sendBeacon){
      navigator.sendBeacon(`${API_BASE}/api/rv-demand/estimate-and-submit`,new Blob([body],{type:'application/json'}));
    }else{
      fetch(`${API_BASE}/api/rv-demand/estimate-and-submit`,{method:'POST',headers:{'Content-Type':'application/json'},body,keepalive:true}).catch(function(){});
    }
  }catch(e){}
}
window.addEventListener('pagehide',sendPartialBeacon);
document.addEventListener('visibilitychange',function(){ if(document.visibilityState==='hidden')sendPartialBeacon(); });
