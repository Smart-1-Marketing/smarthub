/* ==========================================================================
   Smart 1 Seasonal Tourism Marketing Planner — Wizard App
   ========================================================================== */

const TRAVEL_DISTANCES = ['Up to 5 miles','Up to 10 miles','Up to 20 miles','Up to 30 miles','Up to 50 miles','Regional destination',"Let Smart 1 estimate"];
const PURCHASE_TYPES = ['Individual purchase','Family or group purchase','Reservation','Ticket package','Rental','Overnight booking','Service appointment','Qualified lead','Professional consultation'];
const PRIMARY_GOALS = ['Increase reservations','Sell more tickets','Increase foot traffic','Increase same-day purchases','Reach tourists before arrival','Reach visitors already nearby','Promote slower days or times','Increase calls or inquiries','Generate qualified leads','Build overall destination awareness'];
const SECONDARY_GOALS = ['Improve weekday demand','Increase morning traffic','Increase afternoon traffic','Increase evening traffic','Promote weather-dependent offers','Promote indoor alternatives','Reach hotel and resort guests','Reach competitor visitors'];
const PLANNING_WINDOWS = ['The same day','One to three days before','Four to seven days before','One to four weeks before','One to three months before','More than three months before',"Let Smart 1 estimate"];
const ALL_DAYPARTS = ['Early morning','Morning','Midday','Afternoon','Evening','Late night'];
const WEATHER_PACING_OPTIONS = ['Reduce delivery by 25% during unfavorable conditions','Reduce delivery by 50% during unfavorable conditions','Maintain only essential in-market advertising','Shift budget into the next favorable period','Increase delivery when favorable weather returns'];

const PHASES = [
  { key: 'business', label: 'Business', steps: [0,1,2,3,4] },
  { key: 'season', label: 'Goals & Season', steps: [5,6,7,8,9,10] },
  { key: 'strategy', label: 'Strategy', steps: [11,12,13] },
  { key: 'investment', label: 'Investment', steps: [14,15] },
  { key: 'report', label: 'Report', steps: [16] },
];
const TOTAL_STEPS = 17;
const REPORT_STEP = 16;

const STATE = {
  step: 0,
  a: {
    contact: {}, secondaryGoals: [], seasonalityResponse: null, daypartsResponse: null, holidaysResponse: null,
    weatherResponse: null, audienceResponse: null,
    customSeasons: null, customDayparts: null, customHolidays: null, customWeatherPacing: [],
    customAudiences: null,
    aiSuggestion: null, aiSuggestionStatus: 'idle', // idle | loading | done | error
  },
  zipLookupStatus: 'idle', // idle | loading | ok | error
  manualStateFallback: false,
};

/* ---------------------------- attribution ----------------------------
   The landing page copies UTM/click-id params from its own URL onto the
   iframe src (see landing/*.html), so ad attribution survives the hop
   into this embedded tool and rides along with the lead into Smart 1
   Suite. Whitelisted keys only, values length-capped — everything here
   is untrusted URL input headed for a CRM field. document.referrer (the
   embedding page's URL, when iframed) is captured too, so each lead
   records which landing page produced it. */
const ATTRIBUTION_KEYS = ['utm_source','utm_medium','utm_campaign','utm_term','utm_content','gclid','fbclid','msclkid'];
(function captureAttribution() {
  try {
    const qs = new URLSearchParams(window.location.search);
    const utm = {};
    ATTRIBUTION_KEYS.forEach((k) => {
      const v = qs.get(k);
      if (v) utm[k] = String(v).slice(0, 200);
    });
    if (Object.keys(utm).length) STATE.a.utm = utm;
    // Prefer the explicit s1_ref param the landing page passes (full URL);
    // fall back to document.referrer (often origin-only cross-origin).
    const ref = qs.get('s1_ref') || document.referrer || '';
    if (ref) STATE.a.referrerUrl = String(ref).slice(0, 300);
  } catch (e) { /* attribution is best-effort — never break the wizard */ }
})();

/* ---------------------------- partial lead capture ----------------------------
   A stable per-session lead id rides along with both the partial ping and
   the final /api/submit payload, so Smart 1 Suite can merge the two into a
   single lead record. The partial ping fires once — when the visitor
   advances past step 0 with at least a business name or website, or when
   they leave the page mid-wizard — and salvages whatever fields they had
   filled in by then. */
const LEAD_ID = (window.crypto && crypto.randomUUID)
  ? crypto.randomUUID()
  : Date.now() + '-' + Math.random().toString(16).slice(2);
STATE.a.lead_id = LEAD_ID;

let partialSent = false;
function partialPayload() {
  const a = STATE.a;
  const p = { lead_id: LEAD_ID };
  const fields = ['businessName','website','noWebsite','zip','city','state','region','category','categoryOther',
    'activity','purchaseType','avgValue','objective','planningWindow','travelDistance','planChoice'];
  fields.forEach((k) => { if (a[k] !== undefined && a[k] !== null && a[k] !== '') p[k] = a[k]; });
  const contact = a.contact || {};
  ['name','email','phone','preferredMethod'].forEach((k) => { if (contact[k]) { p.contact = p.contact || {}; p.contact[k] = contact[k]; } });
  if (a.utm) p.utm = a.utm;
  if (a.referrerUrl) p.referrerUrl = a.referrerUrl;
  return p;
}
function sendPartial() {
  try {
    if (partialSent) return;
    const a = STATE.a;
    if (a.submitted) return; // full lead already delivered — nothing to salvage
    const p = partialPayload();
    if (!p.website && !p.businessName) return; // need at least something identifying
    partialSent = true;
    const body = JSON.stringify(p);
    if (navigator.sendBeacon) {
      navigator.sendBeacon('/api/partial-lead', new Blob([body], { type: 'application/json' }));
    } else {
      // Relative, not '/api/...': this file is served as a plain asset under
      // the /land/tourism mount, so there is no Jinja to inject the prefix.
      // fetch() resolves against the document URL, which is always
      // /land/tourism/ (Werkzeug 308s the slashless form).
      fetch('api/partial-lead', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true });
    }
  } catch (e) { /* best-effort — never break the wizard */ }
}
window.addEventListener('pagehide', sendPartial);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') sendPartial(); });

/* ---------------------------- analytics events ----------------------------
   The tool itself stays analytics-free; instead it posts named events to
   the parent page (the landing page), which owns GA4/Meta-pixel and maps
   them to real conversion events. No-ops harmlessly when not embedded. */
const firedToolEvents = {};
function postToolEvent(name) {
  if (firedToolEvents[name]) return; // each conversion event fires once per session
  firedToolEvents[name] = true;
  try {
    if (window.self !== window.top) {
      window.parent.postMessage({ type: 'smart1-tool-event', event: name }, '*');
    }
  } catch (e) { /* best-effort */ }
}

/* ---------------------------- render shell ---------------------------- */
function render() {
  const app = document.getElementById('app');

  // The report step renders as a standalone branded document — no wizard
  // chrome (progress bar / step dots) — so it looks and prints like the
  // deliverable it is, matching the Smart 1 PDF report format.
  if (STATE.step === REPORT_STEP) {
    app.innerHTML = `<div id="step-body"></div>`;
    renderStep(document.getElementById('step-body'));
    return;
  }

  const phaseIdx = PHASES.findIndex(p => p.steps.includes(STATE.step));
  const phase = PHASES[phaseIdx];
  const pct = Math.round(((STATE.step) / (TOTAL_STEPS - 1)) * 100);

  const dots = PHASES.map((p, i) => {
    const state = i < phaseIdx ? 'done' : (i === phaseIdx ? 'current' : '');
    const circleContent = i < phaseIdx ? '✓' : (i + 1);
    return `<div class="step-dot">
      <div class="dot-circle ${state}">${circleContent}</div>
      <div class="dot-label ${i === phaseIdx ? 'active' : ''}">${p.label}</div>
    </div>`;
  }).join('');

  app.innerHTML = `
    <div class="card-head">
      <div class="card-head-row">
        <div><span class="step-label">STEP ${STATE.step + 1} OF ${TOTAL_STEPS}</span><span class="step-title">${phase.label}</span></div>
        <div class="time-note">About 5 minutes total</div>
      </div>
      <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
    </div>
    <div class="step-dots">${dots}</div>
    <div class="card-body" id="step-body"></div>
  `;
  renderStep(document.getElementById('step-body'));
}

function rerender() { render(); }

function navRow(container, { nextLabel = 'Continue →', onNext, nextDisabled = false, showBack = true, note = '' } = {}) {
  const row = document.createElement('div');
  row.innerHTML = `
    ${note ? `<div class="subtle-note">${note}</div>` : ''}
    <div class="nav-row">
      <button class="btn btn-secondary" id="btn-back" ${showBack ? '' : 'style="visibility:hidden"'}>Back</button>
      <button class="btn btn-primary" id="btn-next" ${nextDisabled ? 'disabled' : ''}>${nextLabel}</button>
    </div>`;
  container.appendChild(row);
  row.querySelector('#btn-back').addEventListener('click', () => { STATE.step = Math.max(0, STATE.step - 1); render(); window.scrollTo({top:0, behavior:'smooth'}); });
  // Check the button's *live* disabled state at click time, not the boolean
  // captured when navRow() was called — steps that toggle validity via
  // updateNextBtn() (text inputs) mutate the DOM attribute without a full
  // rerender, so a captured closure value would go stale.
  row.querySelector('#btn-next').addEventListener('click', () => { if (!row.querySelector('#btn-next').disabled) { onNext(); } });
}

function qHead(container, icon, title, sub) {
  const div = document.createElement('div');
  div.className = 'q-head';
  div.innerHTML = `<div class="q-icon">${icon}</div><div><h2 class="q-title">${title}</h2><p class="q-sub">${sub}</p></div>`;
  container.appendChild(div);
}

function goNext() { STATE.step = Math.min(TOTAL_STEPS - 1, STATE.step + 1); render(); window.scrollTo({top:0, behavior:'smooth'}); }

/* ---------------------------- small UI builders ---------------------------- */
function chip(value, label, selected) {
  return `<div class="chip ${selected ? 'selected' : ''}" data-chip="${escapeAttr(value)}">${label}</div>`;
}
function radioCard(value, title, sub, selected) {
  return `<div class="radio-card ${selected ? 'selected' : ''}" data-radio="${escapeAttr(value)}">
    <div class="radio-dot"></div>
    <div><div class="rc-title">${title}</div>${sub ? `<div class="rc-sub">${sub}</div>` : ''}</div>
  </div>`;
}
function checkRow(value, label, selected) {
  return `<div class="check-row ${selected ? 'selected' : ''}" data-check="${escapeAttr(value)}">
    <div class="check-box">${selected ? '✓' : ''}</div><div>${label}</div>
  </div>`;
}
/* escapeAttr(), escapeHtml(), and money() now live in report-template.js
   (loaded before this file) so report-print.html can use them too. */

function wireChips(container, onPick) {
  container.querySelectorAll('[data-chip]').forEach(el => el.addEventListener('click', () => onPick(el.getAttribute('data-chip'))));
}
function wireRadios(container, onPick) {
  container.querySelectorAll('[data-radio]').forEach(el => el.addEventListener('click', () => onPick(el.getAttribute('data-radio'))));
}
function wireChecks(container, onToggle) {
  container.querySelectorAll('[data-check]').forEach(el => el.addEventListener('click', () => onToggle(el.getAttribute('data-check'))));
}

/* ============================== STEP ROUTER ============================== */
function renderStep(c) {
  switch (STATE.step) {
    case 0: return stepBusinessInfo(c);
    case 1: return stepLocation(c);
    case 2: return stepCategory(c);
    case 3: return stepActivity(c);
    case 4: return stepAvgValue(c);
    case 5: return stepObjective(c);
    case 6: return stepPlanningWindow(c);
    case 7: return stepSeasonality(c);
    case 8: return stepDayparts(c);
    case 9: return stepHolidays(c);
    case 10: return stepWeather(c);
    case 11: return stepAudience(c);
    case 12: return stepProducts(c);
    case 13: return stepSuite(c);
    case 14: return stepBudget(c);
    case 15: return stepSeasonalSplit(c);
    case 16: return stepReport(c);
  }
}

/* ---- Step 0: Business info ----
   Progressive capture: only the business name + website up front — contact
   details (name/email/phone) are asked for at the final gate (stepReport),
   right where the finished report is unlocked. If a website is given, we
   kick off a background lookup (maybeAnalyzeWebsite) that never blocks
   navigation — it just quietly pre-fills category/activity/price guesses
   a few steps later if it finishes in time, and is silently skipped if it
   doesn't or fails. */
function stepBusinessInfo(c) {
  qHead(c, '👋', "Let's start with your business.", "We'll use this to personalize your seasonal plan — nothing is submitted until the end.");
  const a = STATE.a;
  a.contact = a.contact || {};
  const body = document.createElement('div');
  body.innerHTML = `
    <div class="field-row">
      <div class="field"><label class="field-label">Business name<span class="tag-req">Required</span></label><input type="text" id="biz-name" value="${escapeAttr(a.businessName||'')}" placeholder="Your business name" /></div>
      <div class="field"><label class="field-label">Website or Facebook page<span class="tag-opt">Optional</span></label><input type="text" id="biz-website" value="${escapeAttr(a.website||'')}" placeholder="yourbusiness.com or facebook.com/yourbusiness" ${a.noWebsite ? 'disabled' : ''} /></div>
    </div>
    <div id="no-web-wrap" class="check-grid" style="margin-bottom:6px;">
      ${checkRow('no-web', "We don't have a website or Facebook page yet", !!a.noWebsite)}
    </div>
    ${a.noWebsite ? `<div class="callout" style="margin-bottom:10px;">No problem — plenty of great tourism businesses start exactly there. Your plan will include a recommendation for a simple landing page so your ads always have somewhere to send visitors, and Smart 1 can build one for you.</div>` : ''}
    <div id="ai-status" class="subtle-note" style="min-height:16px;">${aiStatusNote(a)}</div>
    <div style="position:absolute; left:-9999px; top:auto; width:1px; height:1px; overflow:hidden;" aria-hidden="true">
      <label for="s1_hp_field">Leave this field blank</label>
      <input type="text" id="s1_hp_field" name="s1_hp_field" tabindex="-1" autocomplete="off" />
    </div>
  `;
  c.appendChild(body);

  body.querySelector('#biz-name').addEventListener('input', (e) => { a.businessName = e.target.value; updateNextBtn(); });
  const websiteInput = body.querySelector('#biz-website');
  websiteInput.addEventListener('input', (e) => { a.website = e.target.value; });
  websiteInput.addEventListener('blur', () => maybeAnalyzeWebsite(a));
  wireChecks(body.querySelector('#no-web-wrap'), () => {
    a.noWebsite = !a.noWebsite;
    if (a.noWebsite) a.website = '';
    rerender();
  });

  function updateNextBtn() { const btn = document.getElementById('btn-next'); if (btn) btn.disabled = !canProceedBusinessInfo(); }
  navRow(c, { showBack: false, nextDisabled: !canProceedBusinessInfo(), onNext: () => { postToolEvent('form_start'); maybeAnalyzeWebsite(a); sendPartial(); goNext(); } });
}
function canProceedBusinessInfo() {
  const a = STATE.a;
  return !!(a.businessName && a.businessName.trim());
}
function aiStatusNote(a) {
  if (a.aiSuggestionStatus === 'loading') return 'Taking a quick look at your website…';
  if (a.aiSuggestionStatus === 'done' && a.aiSuggestion) return "Found a few details from your website — we'll suggest them a little later, and you can always change them.";
  return '';
}

/* Fire-and-forget background lookup. Never blocks navigation and fails
   silently — this is a convenience pre-fill, not a required step. */
let lastAnalyzedWebsite = null;
function looksLikeUrl(v) {
  const s = String(v || '').trim();
  return !!s && /^(https?:\/\/)?[a-z0-9-]+(\.[a-z0-9-]+)+([/?#].*)?$/i.test(s);
}
function maybeAnalyzeWebsite(a) {
  if (a.noWebsite) return;
  const site = String(a.website || '').trim();
  if (!site || !looksLikeUrl(site) || site === lastAnalyzedWebsite) return;
  // Facebook/Instagram pages sit behind a login wall the server can't get
  // past — skip the background lookup rather than show "taking a look…"
  // and silently fail. The report still handles social-only businesses
  // (see classifyWebPresence in report-template.js).
  if (/facebook\.com|fb\.com|fb\.me|instagram\.com/i.test(site)) return;
  lastAnalyzedWebsite = site;
  a.aiSuggestionStatus = 'loading';
  fetch('api/analyze-business', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ website: site }),
  })
    .then((res) => (res.ok ? res.json() : Promise.reject(new Error('analyze failed'))))
    .then((data) => {
      if (String(a.website || '').trim() !== site) return; // website changed since we asked
      a.aiSuggestion = (data && data.ok && data.suggestion) || null;
      a.aiSuggestionStatus = a.aiSuggestion ? 'done' : 'error';
      // Only auto-refresh steps that actually consume the suggestion — a
      // background rerender any later would clobber in-progress UI state
      // (e.g. the submit button's "Building your plan…" text).
      if (STATE.step <= 4) rerender();
    })
    .catch(() => {
      if (String(a.website || '').trim() !== site) return;
      a.aiSuggestionStatus = 'error';
    });
}

/* ---- Step 0: Location ---- */
function stepLocation(c) {
  qHead(c, '📍', 'Where is your business located?', 'We use your ZIP code to estimate climate, seasonality, and nearby tourism activity.');

  const a = STATE.a;
  const body = document.createElement('div');
  body.innerHTML = `
    <div class="field">
      <label class="field-label">Business ZIP code<span class="tag-req">Required</span></label>
      <input type="text" id="zip-input" inputmode="numeric" maxlength="5" placeholder="Enter ZIP code" value="${a.zip || ''}" />
      <div id="zip-status" class="subtle-note"></div>
    </div>
    <div id="manual-state-wrap"></div>
    <label class="field-label" style="margin-top:6px;">Customer travel distance<span class="tag-req">Required</span></label>
    <div class="radio-grid" id="distance-grid">
      ${TRAVEL_DISTANCES.map(d => radioCard(d, d, null, a.travelDistance === d)).join('')}
    </div>
  `;
  c.appendChild(body);

  const zipInput = body.querySelector('#zip-input');
  const statusEl = body.querySelector('#zip-status');

  function paintStatus() {
    if (STATE.zipLookupStatus === 'loading') statusEl.innerHTML = 'Looking up your area…';
    else if (STATE.zipLookupStatus === 'ok') statusEl.innerHTML = `<b>${a.city}, ${a.state}</b> — ${REGIONS[a.region].label}`;
    else if (STATE.zipLookupStatus === 'error') statusEl.innerHTML = `We couldn't automatically look up that ZIP. Select your state below.`;
    else statusEl.innerHTML = '';
  }
  paintStatus();

  if (STATE.manualStateFallback) renderManualState(body.querySelector('#manual-state-wrap'));

  zipInput.addEventListener('input', (e) => {
    a.zip = e.target.value.replace(/\D/g, '').slice(0, 5);
    e.target.value = a.zip;
    STATE.zipLookupStatus = 'idle';
    STATE.manualStateFallback = false;
    a.city = a.state = a.region = null;
    paintStatus();
    updateNextState();
  });
  zipInput.addEventListener('blur', () => { if (a.zip && a.zip.length === 5) doZipLookup(body, statusEl); });

  wireRadios(body.querySelector('#distance-grid'), (val) => { a.travelDistance = val; rerender(); });

  navRow(c, { nextDisabled: !canProceedLocation(), onNext: async () => {
    if (a.zip && a.zip.length === 5 && STATE.zipLookupStatus !== 'ok' && !STATE.manualStateFallback) {
      await doZipLookup(body, statusEl);
      // Lookup succeeded and everything else on the step is valid → advance
      // in the same click instead of making the visitor press Continue twice.
      if (STATE.zipLookupStatus === 'ok' && canProceedLocation()) goNext();
      // Otherwise doZipLookup() already rerendered (showing the manual state
      // fallback on failure) — stay on this step.
      return;
    }
    goNext();
  }});

  function updateNextState() { rerender(); }
}

function canProceedLocation() {
  const a = STATE.a;
  const hasLocation = (STATE.zipLookupStatus === 'ok') || (STATE.manualStateFallback && a.state);
  return !!(a.zip && a.zip.length === 5 && hasLocation && a.travelDistance);
}

async function doZipLookup(body, statusEl) {
  const a = STATE.a;
  STATE.zipLookupStatus = 'loading';
  statusEl.innerHTML = 'Looking up your area…';
  try {
    const info = await lookupZip(a.zip);
    a.city = info.city; a.state = info.state; a.lat = info.lat; a.lon = info.lon;
    a.region = regionForZip(info.state, info.lat, info.lon);
    STATE.zipLookupStatus = 'ok';
    STATE.manualStateFallback = false;
  } catch (err) {
    STATE.zipLookupStatus = 'error';
    STATE.manualStateFallback = true;
  }
  rerender();
}

function renderManualState(wrap) {
  const a = STATE.a;
  const states = Object.keys(REGIONS).length ? US_STATE_LIST : [];
  wrap.innerHTML = `
    <div class="field">
      <label class="field-label">State<span class="tag-req">Required</span></label>
      <select id="state-select">
        <option value="">Select your state</option>
        ${US_STATE_LIST.map(s => `<option value="${s.abbr}" ${a.state === s.abbr ? 'selected' : ''}>${s.name}</option>`).join('')}
      </select>
    </div>`;
  wrap.querySelector('#state-select').addEventListener('change', (e) => {
    a.state = e.target.value;
    if (a.state) { a.region = regionForZip(a.state, 0, 0); a.city = a.city || ''; }
    rerender();
  });
}

const US_STATE_LIST = [
  {abbr:'AL',name:'Alabama'},{abbr:'AK',name:'Alaska'},{abbr:'AZ',name:'Arizona'},{abbr:'AR',name:'Arkansas'},{abbr:'CA',name:'California'},
  {abbr:'CO',name:'Colorado'},{abbr:'CT',name:'Connecticut'},{abbr:'DE',name:'Delaware'},{abbr:'DC',name:'District of Columbia'},{abbr:'FL',name:'Florida'},
  {abbr:'GA',name:'Georgia'},{abbr:'HI',name:'Hawaii'},{abbr:'ID',name:'Idaho'},{abbr:'IL',name:'Illinois'},{abbr:'IN',name:'Indiana'},
  {abbr:'IA',name:'Iowa'},{abbr:'KS',name:'Kansas'},{abbr:'KY',name:'Kentucky'},{abbr:'LA',name:'Louisiana'},{abbr:'ME',name:'Maine'},
  {abbr:'MD',name:'Maryland'},{abbr:'MA',name:'Massachusetts'},{abbr:'MI',name:'Michigan'},{abbr:'MN',name:'Minnesota'},{abbr:'MS',name:'Mississippi'},
  {abbr:'MO',name:'Missouri'},{abbr:'MT',name:'Montana'},{abbr:'NE',name:'Nebraska'},{abbr:'NV',name:'Nevada'},{abbr:'NH',name:'New Hampshire'},
  {abbr:'NJ',name:'New Jersey'},{abbr:'NM',name:'New Mexico'},{abbr:'NY',name:'New York'},{abbr:'NC',name:'North Carolina'},{abbr:'ND',name:'North Dakota'},
  {abbr:'OH',name:'Ohio'},{abbr:'OK',name:'Oklahoma'},{abbr:'OR',name:'Oregon'},{abbr:'PA',name:'Pennsylvania'},{abbr:'RI',name:'Rhode Island'},
  {abbr:'SC',name:'South Carolina'},{abbr:'SD',name:'South Dakota'},{abbr:'TN',name:'Tennessee'},{abbr:'TX',name:'Texas'},{abbr:'UT',name:'Utah'},
  {abbr:'VT',name:'Vermont'},{abbr:'VA',name:'Virginia'},{abbr:'WA',name:'Washington'},{abbr:'WV',name:'West Virginia'},{abbr:'WI',name:'Wisconsin'},{abbr:'WY',name:'Wyoming'},
];

/* ---- Step 2: Category ----
   Two-level picker: choose a category group first, then the specific
   business type appears within it — instead of dumping all ~60 items on
   screen at once. A back link at the item level lets them reconsider if
   their business isn't in that group. */
function stepCategory(c) {
  qHead(c, '🏪', 'What type of business are you promoting?', 'Pick the category that best describes your business.');
  const a = STATE.a;
  const body = document.createElement('div');
  const suggestion = a.category ? null : suggestedCategoryFromAi(a);

  let activeGroup = null;
  if (a.category && a.category !== '__other__') {
    activeGroup = CATEGORY_GROUPS.find(g => g.items.includes(a.category)) || null;
  } else if (a.categoryGroupPick) {
    activeGroup = CATEGORY_GROUPS.find(g => g.group === a.categoryGroupPick) || null;
  }

  if (activeGroup) {
    // Level 2: the specific business type within the chosen group.
    body.innerHTML = `
      <button type="button" class="btn btn-text" id="btn-back-category" style="padding-left:0; margin-bottom:10px;">← Don't see your business? Choose a different category</button>
      <label class="field-label" style="display:block;">${escapeHtml(activeGroup.group)}</label>
      <div class="choice-grid">${activeGroup.items.map(i => chip(i, i, a.category === i)).join('')}</div>
    `;
    c.appendChild(body);
    body.querySelector('#btn-back-category').addEventListener('click', () => { a.category = null; a.categoryGroupPick = null; rerender(); });
    wireChips(body, (val) => { a.category = val; rerender(); });
  } else if (a.category === '__other__') {
    body.innerHTML = `
      <button type="button" class="btn btn-text" id="btn-back-category" style="padding-left:0; margin-bottom:10px;">← Choose a category instead</button>
      <div class="field"><label class="field-label">Describe your business<span class="tag-req">Required</span></label><input type="text" id="cat-other" placeholder="Describe your business" value="${escapeAttr(a.categoryOther || '')}" /></div>
    `;
    c.appendChild(body);
    body.querySelector('#btn-back-category').addEventListener('click', () => { a.category = null; rerender(); });
    body.querySelector('#cat-other').addEventListener('input', (e) => { a.categoryOther = e.target.value; updateNextBtn(); });
  } else {
    // Level 1: pick a category group.
    body.innerHTML = `
      ${suggestion ? `
        <div class="callout" style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">
          <span>Based on ${escapeHtml(a.website || 'your website')}, this might be <b>${escapeHtml(suggestion.label)}</b>.</span>
          <button type="button" class="btn btn-secondary" id="btn-use-suggestion" style="white-space:nowrap;">Use this →</button>
        </div>` : ''}
      <div class="choice-grid" style="${suggestion ? 'margin-top:14px;' : ''}">${CATEGORY_GROUPS.map(g => chip(g.group, g.group, false)).join('')}</div>
      <label class="field-label" style="margin-top:14px; display:block;">Not seeing your business?</label>
      <div class="choice-grid">${chip('__other__', 'Other seasonal or tourism-related business', false)}</div>
    `;
    c.appendChild(body);
    if (suggestion) body.querySelector('#btn-use-suggestion').addEventListener('click', () => { a.categoryGroupPick = suggestion.group; a.category = suggestion.item; rerender(); });
    wireChips(body, (val) => {
      if (val === '__other__') { a.category = '__other__'; a.categoryGroupPick = null; }
      else { a.categoryGroupPick = val; }
      rerender();
    });
  }

  function updateNextBtn() {
    const btn = document.getElementById('btn-next');
    if (btn) btn.disabled = !canProceedCategory();
  }
  navRow(c, { nextDisabled: !canProceedCategory(), onNext: goNext });
}
function suggestedCategoryFromAi(a) {
  const item = a.aiSuggestion && a.aiSuggestion.categoryItem;
  if (!item || !CATEGORY_ITEM_MAP[item]) return null;
  const group = CATEGORY_GROUPS.find(g => g.items.includes(item));
  if (!group) return null;
  return { item, label: item, group: group.group };
}
function canProceedCategory() {
  const a = STATE.a;
  if (!a.category) return false;
  if (a.category === '__other__') return !!(a.categoryOther && a.categoryOther.trim());
  return true;
}

/* ---- Step 3: Activity / purchase type ----
   Purchase-type choices are narrowed to what's relevant for the chosen
   business (via PURCHASE_TYPE_SUGGESTIONS in data.js) instead of always
   showing all 9 generic types — with a "show all" escape hatch in case
   the right one isn't in the suggested subset. */
function stepActivity(c) {
  qHead(c, '🎫', 'What do you most want tourists to purchase?', 'Describe the primary product, service, or booking you want to sell more of.');
  const a = STATE.a;
  const profile = getCategoryProfile(a.category === '__other__' ? null : a.category);
  if (!a.activity && a.aiSuggestion && a.aiSuggestion.activity) a.activity = a.aiSuggestion.activity;

  const grouped = suggestedPurchaseTypes(profile.familyKey, PURCHASE_TYPES);
  const suggested = Array.isArray(grouped) ? grouped : grouped.suggested;
  const rest = Array.isArray(grouped) ? [] : grouped.rest;
  const displayAll = rest.length === 0 || a.showAllPurchaseTypes || (a.purchaseType && rest.includes(a.purchaseType));
  const list = displayAll ? PURCHASE_TYPES : suggested;
  const wasAiFilled = !!(a.aiSuggestion && a.aiSuggestion.activity && a.activity === a.aiSuggestion.activity);

  const body = document.createElement('div');
  body.innerHTML = `
    <div class="field">
      <label class="field-label">Primary product or activity<span class="tag-req">Required</span></label>
      <input type="text" id="activity-input" placeholder="Example: Sunset cruise, family dinner, cabin reservation, attraction tickets" value="${escapeAttr(a.activity || '')}" />
      ${wasAiFilled ? `<div class="subtle-note">Suggested from your website — feel free to edit.</div>` : ''}
    </div>
    <label class="field-label">Purchase type<span class="tag-req">Required</span></label>
    <div class="radio-grid">${list.map(p => radioCard(p, p, null, a.purchaseType === p)).join('')}</div>
    ${!displayAll ? `<button type="button" class="btn btn-text" id="btn-show-all-types" style="padding-left:0; margin-top:6px;">Show all purchase types →</button>` : ''}
  `;
  c.appendChild(body);
  body.querySelector('#activity-input').addEventListener('input', (e) => { a.activity = e.target.value; updateNextBtn(); });
  wireRadios(body, (val) => { a.purchaseType = val; rerender(); });
  const showAllBtn = body.querySelector('#btn-show-all-types');
  if (showAllBtn) showAllBtn.addEventListener('click', () => { a.showAllPurchaseTypes = true; rerender(); });
  function updateNextBtn() { const btn = document.getElementById('btn-next'); if (btn) btn.disabled = !canProceedActivity(); }
  navRow(c, { nextDisabled: !canProceedActivity(), onNext: goNext });
}
function canProceedActivity() { const a = STATE.a; return !!(a.activity && a.activity.trim() && a.purchaseType); }

/* ---- Step 4: Average value (slider) ----
   A single slider replaces the old exact-number / bucket-radio choice.
   The dollar scale is exponential (not linear) so the low end — where
   most tourism transactions cluster — still gets fine control, while the
   slider can still reach real-estate-commission-sized values at the top.
   An "enter exact number" fallback stays available for edge cases. */
const AVG_VALUE_SLIDER_MIN = 10;
const AVG_VALUE_SLIDER_MAX = 20000;
const AVG_VALUE_SLIDER_STEPS = 1000;
function avgValueSliderToDollars(pct) {
  const ratio = AVG_VALUE_SLIDER_MAX / AVG_VALUE_SLIDER_MIN;
  return Math.round(AVG_VALUE_SLIDER_MIN * Math.pow(ratio, pct / AVG_VALUE_SLIDER_STEPS));
}
function avgValueDollarsToSlider(value) {
  const v = Math.max(AVG_VALUE_SLIDER_MIN, Math.min(AVG_VALUE_SLIDER_MAX, value || AVG_VALUE_SLIDER_MIN));
  const ratio = AVG_VALUE_SLIDER_MAX / AVG_VALUE_SLIDER_MIN;
  return Math.round(AVG_VALUE_SLIDER_STEPS * Math.log(v / AVG_VALUE_SLIDER_MIN) / Math.log(ratio));
}
function stepAvgValue(c) {
  qHead(c, '💰', 'What is the average value of one customer or booking?', 'Drag the slider — this drives your acquisition-cost target and revenue estimate.');
  const a = STATE.a;
  const profile = getCategoryProfile(a.category === '__other__' ? null : a.category);
  if (!a.avgValue && a.aiSuggestion && a.aiSuggestion.avgValue) a.avgValue = a.aiSuggestion.avgValue;
  const wasAiFilled = !!(a.aiSuggestion && a.aiSuggestion.avgValue && a.avgValue === a.aiSuggestion.avgValue);
  const sliderPct = avgValueDollarsToSlider(a.avgValue || AVG_VALUE_SLIDER_MIN);

  const body = document.createElement('div');
  body.innerHTML = `
    <div class="field">
      <label class="field-label">Average transaction or booking value<span class="tag-req">Required</span></label>
      <div id="avgval-readout" style="font-size:32px; font-weight:800; color:var(--navy); margin:6px 0 14px;">${a.avgValue ? money(a.avgValue) : '—'}</div>
      <input type="range" id="avgval-slider" min="0" max="${AVG_VALUE_SLIDER_STEPS}" step="1" value="${sliderPct}" style="width:100%;" />
      <div style="display:flex; justify-content:space-between; font-size:11.5px; color:var(--muted); margin-top:4px;"><span>${money(AVG_VALUE_SLIDER_MIN)}</span><span>${money(AVG_VALUE_SLIDER_MAX)}+</span></div>
      <div class="subtle-note">Typical basis for this category: ${profile.cacBasis}.</div>
      ${wasAiFilled ? `<div class="subtle-note">Suggested from pricing found on your website${a.aiSuggestion.priceEvidence ? ' — ' + escapeHtml(a.aiSuggestion.priceEvidence) : ''}.</div>` : ''}
    </div>
    <button class="btn btn-text" id="toggle-exact" style="padding-left:0;">${a.avgValueExactEntry ? 'Use the slider instead' : 'Know the exact number? Enter it →'}</button>
    ${a.avgValueExactEntry ? `<div class="field" style="margin-top:10px;"><input type="number" min="0" step="1" id="avgval-exact" value="${a.avgValue || ''}" placeholder="$ Average value" /></div>` : ''}
    <div class="callout" style="margin-top:20px;">
      <b>Industry planning assumption:</b> businesses like yours typically run a ${profile.margin[0]}%–${profile.margin[1]}% gross profit margin. This is used only to frame the plan — you don't need to enter your actual margin.
    </div>
  `;
  c.appendChild(body);

  const readout = body.querySelector('#avgval-readout');
  body.querySelector('#avgval-slider').addEventListener('input', (e) => {
    a.avgValue = avgValueSliderToDollars(parseInt(e.target.value, 10));
    readout.textContent = money(a.avgValue);
    updateNextBtn();
  });
  body.querySelector('#toggle-exact').addEventListener('click', () => { a.avgValueExactEntry = !a.avgValueExactEntry; rerender(); });
  const exact = body.querySelector('#avgval-exact');
  if (exact) exact.addEventListener('input', (e) => { a.avgValue = parseFloat(e.target.value) || null; updateNextBtn(); });

  function updateNextBtn() { const btn = document.getElementById('btn-next'); if (btn) btn.disabled = !canProceedAvgValue(); }
  navRow(c, { nextDisabled: !canProceedAvgValue(), onNext: goNext });
}
function canProceedAvgValue() { return !!(STATE.a.avgValue && STATE.a.avgValue > 0); }

/* ---- Step 4: Objective ---- */
function stepObjective(c) {
  qHead(c, '🎯', 'What is your primary goal?', 'Choose the main outcome you want from this plan, plus any secondary priorities.');
  const a = STATE.a;
  const body = document.createElement('div');
  body.innerHTML = `
    <label class="field-label">Primary goal<span class="tag-req">Required</span></label>
    <div class="radio-grid">${PRIMARY_GOALS.map(g => radioCard(g, g, null, a.objective === g)).join('')}</div>
    <label class="field-label" style="margin-top:18px;">Secondary goals<span class="tag-opt">Optional</span></label>
    <div class="check-grid">${SECONDARY_GOALS.map(g => checkRow(g, g, a.secondaryGoals.includes(g))).join('')}</div>
  `;
  c.appendChild(body);
  wireRadios(body, (val) => { a.objective = val; rerender(); });
  wireChecks(body, (val) => {
    const idx = a.secondaryGoals.indexOf(val);
    if (idx >= 0) a.secondaryGoals.splice(idx, 1); else a.secondaryGoals.push(val);
    rerender();
  });
  navRow(c, { nextDisabled: !a.objective, onNext: goNext });
}

/* ---- Step 5: Planning window ---- */
function stepPlanningWindow(c) {
  qHead(c, '⏱️', 'When do customers usually make their decision?', 'This determines the split between pre-arrival and in-market advertising.');
  const a = STATE.a;
  const body = document.createElement('div');
  body.innerHTML = `<div class="radio-grid">${PLANNING_WINDOWS.map(w => radioCard(w, w, null, a.planningWindow === w)).join('')}</div>`;
  c.appendChild(body);
  wireRadios(body, (val) => { a.planningWindow = val; rerender(); });
  navRow(c, { nextDisabled: !a.planningWindow, onNext: goNext });
}

/* ---- Step 6: Seasonality ---- */
/* computeDefaultSeasons() and getSeasons() now live in report-template.js
   so the report renderer can share them without pulling in the wizard. */
function stepSeasonality(c) {
  qHead(c, '📅', 'We estimated your tourism seasons.', 'Based on your ZIP code, climate, and business category.');
  const a = STATE.a;
  const seasons = getSeasons(a);
  const body = document.createElement('div');
  body.innerHTML = `
    <div class="summary-box">
      <div class="summary-item"><div class="si-label">Primary season</div><div class="si-value">${monthsToRangeString(seasons.primary)}</div></div>
      <div class="summary-item"><div class="si-label">Shoulder season</div><div class="si-value">${monthsToRangeString(seasons.shoulder)}</div></div>
      <div class="summary-item"><div class="si-label">Slow season</div><div class="si-value">${monthsToRangeString(seasons.slow)}</div></div>
    </div>
    <div class="radio-grid" style="grid-template-columns:1fr 1fr 1fr;">
      ${radioCard('accurate', 'This looks accurate', null, a.seasonalityResponse === 'accurate')}
      ${radioCard('mostly', 'Mostly accurate', null, a.seasonalityResponse === 'mostly')}
      ${radioCard('adjust', 'Adjust the seasons', null, a.seasonalityResponse === 'adjust')}
    </div>
    <div id="adjust-wrap"></div>
  `;
  c.appendChild(body);
  wireRadios(body, (val) => {
    a.seasonalityResponse = val;
    if (val === 'adjust' && !a.customSeasons) a.customSeasons = computeDefaultSeasons(a);
    if (val !== 'adjust') a.customSeasons = a.customSeasons; // keep edits if user goes back and forth
    rerender();
  });
  if (a.seasonalityResponse === 'adjust') renderSeasonAdjust(body.querySelector('#adjust-wrap'));
  navRow(c, { nextDisabled: !a.seasonalityResponse, onNext: goNext });
}
function renderSeasonAdjust(wrap) {
  const a = STATE.a;
  const s = a.customSeasons;
  const monthChip = (bucket, m) => {
    const active = s[bucket].includes(m);
    return `<div class="chip ${active ? 'selected' : ''}" data-season-chip="${bucket}:${m}" style="padding:6px 10px; font-size:11.5px;">${MONTHS[m-1].slice(0,3)}</div>`;
  };
  wrap.innerHTML = ['primary','shoulder','slow'].map(bucket => `
    <label class="field-label" style="margin-top:14px;">${bucket === 'primary' ? 'Primary season' : bucket === 'shoulder' ? 'Shoulder season' : 'Slow season'}</label>
    <div class="choice-grid">${Array.from({length:12}, (_,i) => monthChip(bucket, i+1)).join('')}</div>
  `).join('');
  wrap.querySelectorAll('[data-season-chip]').forEach(el => el.addEventListener('click', () => {
    const [bucket, mStr] = el.getAttribute('data-season-chip').split(':');
    const m = parseInt(mStr, 10);
    const idx = s[bucket].indexOf(m);
    if (idx >= 0) s[bucket].splice(idx, 1); else s[bucket].push(m);
    rerender();
  }));
}

/* ---- Step 7: Dayparts ---- */
/* getDayparts(a) now lives in report-template.js. */
function stepDayparts(c) {
  qHead(c, '⏰', 'We estimated your strongest advertising times.', 'Based on your business category.');
  const a = STATE.a;
  const recommended = getCategoryProfile(a.category === '__other__' ? null : a.category).dayparts;
  const body = document.createElement('div');
  body.innerHTML = `
    <div class="callout"><ul>${recommended.map(d => `<li>${d}</li>`).join('')}</ul></div>
    <div class="radio-grid" style="grid-template-columns:1fr 1fr 1fr;">
      ${radioCard('use', 'Use the recommended times', null, a.daypartsResponse === 'use')}
      ${radioCard('allday', 'Advertise throughout the day', null, a.daypartsResponse === 'allday')}
      ${radioCard('adjust', 'Let me adjust the times', null, a.daypartsResponse === 'adjust')}
    </div>
    <div id="daypart-adjust-wrap"></div>
  `;
  c.appendChild(body);
  wireRadios(body, (val) => {
    a.daypartsResponse = val;
    if (val === 'use') a.customDayparts = recommended;
    else if (val === 'allday') a.customDayparts = ALL_DAYPARTS;
    else if (val === 'adjust' && !a.customDayparts) a.customDayparts = [...recommended];
    rerender();
  });
  if (a.daypartsResponse === 'adjust') {
    const wrap = body.querySelector('#daypart-adjust-wrap');
    wrap.innerHTML = `<label class="field-label" style="margin-top:14px;">Select dayparts</label><div class="check-grid">${ALL_DAYPARTS.map(d => checkRow(d, d, a.customDayparts.includes(d))).join('')}</div>`;
    wireChecks(wrap, (val) => {
      const idx = a.customDayparts.indexOf(val);
      if (idx >= 0) a.customDayparts.splice(idx, 1); else a.customDayparts.push(val);
      rerender();
    });
  }
  navRow(c, { nextDisabled: !a.daypartsResponse, onNext: goNext });
}

/* ---- Step 8: Holidays ---- */
/* getHolidayRecs(a) now lives in report-template.js. */
function stepHolidays(c) {
  qHead(c, '🎉', 'We identified your strongest holiday opportunities.', 'Based on your ZIP code, climate, and business activity. Local festivals and special events are not included.');
  const a = STATE.a;
  const recs = getHolidayRecs(a);
  const body = document.createElement('div');
  body.innerHTML = `
    <div class="callout"><ul>${recs.map(h => `<li>${h.label}</li>`).join('')}</ul></div>
    <div class="radio-grid" style="grid-template-columns:1fr 1fr 1fr;">
      ${radioCard('include', 'Include recommended holiday periods', null, a.holidaysResponse === 'include')}
      ${radioCard('none', 'Do not prioritize holidays', null, a.holidaysResponse === 'none')}
      ${radioCard('choose', 'Let me choose', null, a.holidaysResponse === 'choose')}
    </div>
    <div id="holiday-choose-wrap"></div>
  `;
  c.appendChild(body);
  wireRadios(body, (val) => {
    a.holidaysResponse = val;
    if (val === 'include') a.customHolidays = recs.map(h => h.key);
    else if (val === 'none') a.customHolidays = [];
    else if (val === 'choose' && !a.customHolidays) a.customHolidays = recs.map(h => h.key);
    rerender();
  });
  if (a.holidaysResponse === 'choose') {
    const wrap = body.querySelector('#holiday-choose-wrap');
    wrap.innerHTML = `<div class="check-grid" style="margin-top:14px;">${HOLIDAYS.map(h => checkRow(h.key, h.label, a.customHolidays.includes(h.key))).join('')}</div>`;
    wireChecks(wrap, (val) => {
      const idx = a.customHolidays.indexOf(val);
      if (idx >= 0) a.customHolidays.splice(idx, 1); else a.customHolidays.push(val);
      rerender();
    });
  }
  navRow(c, { nextDisabled: !a.holidaysResponse, onNext: goNext });
}

/* ---- Step 9: Weather ---- */
function stepWeather(c) {
  qHead(c, '⛅', 'Use the weather to avoid wasting advertising dollars.', 'Weather is used to slow down or speed up delivery — not to pause your campaign entirely.');
  const a = STATE.a;
  const profile = getCategoryProfile(a.category === '__other__' ? null : a.category);
  const region = REGIONS[a.region] || REGIONS.SOUTHEAST;
  const lines = weatherStrategyText(profile.class, region.flags);
  const body = document.createElement('div');
  body.innerHTML = `
    <div class="callout"><ul>${lines.map(l => `<li>${l}</li>`).join('')}</ul></div>
    <div class="radio-grid">
      ${radioCard('recommended', "Use Smart 1's recommended weather settings", 'Reduce delivery ~40% in unfavorable conditions and shift the saved budget to the next high-opportunity period.', a.weatherResponse === 'recommended')}
      ${radioCard('consistent', 'Keep advertising consistent in all weather', null, a.weatherResponse === 'consistent')}
      ${radioCard('choose', 'Let me choose weather settings', null, a.weatherResponse === 'choose')}
    </div>
    <div id="weather-choose-wrap"></div>
  `;
  c.appendChild(body);
  wireRadios(body, (val) => {
    a.weatherResponse = val;
    if (val === 'choose' && a.customWeatherPacing.length === 0) a.customWeatherPacing = [WEATHER_PACING_OPTIONS[0], WEATHER_PACING_OPTIONS[3]];
    rerender();
  });
  if (a.weatherResponse === 'choose') {
    const wrap = body.querySelector('#weather-choose-wrap');
    wrap.innerHTML = `<label class="field-label" style="margin-top:14px;">Weather pacing options</label><div class="check-grid">${WEATHER_PACING_OPTIONS.map(o => checkRow(o, o, a.customWeatherPacing.includes(o))).join('')}</div>`;
    wireChecks(wrap, (val) => {
      const idx = a.customWeatherPacing.indexOf(val);
      if (idx >= 0) a.customWeatherPacing.splice(idx, 1); else a.customWeatherPacing.push(val);
      rerender();
    });
  }
  navRow(c, { nextDisabled: !a.weatherResponse, onNext: goNext });
}

/* ---- Step 10: Audience ---- */
function stepAudience(c) {
  qHead(c, '👥', 'Who should Smart 1 reach?', 'Recommended audiences based on your ZIP code and business activity.');
  const a = STATE.a;
  const profile = getCategoryProfile(a.category === '__other__' ? null : a.category);
  const region = REGIONS[a.region] || REGIONS.SOUTHEAST;
  const recs = recommendAudiences(profile.class, profile.familyKey, region.flags);
  const body = document.createElement('div');
  body.innerHTML = `
    <div class="report-grid">
      <div><label class="field-label">Before-arrival (recommended)</label><div class="callout"><ul>${recs.before.map(x=>`<li>${x}</li>`).join('')}</ul></div></div>
      <div><label class="field-label">In-market (recommended)</label><div class="callout"><ul>${recs.inMarket.map(x=>`<li>${x}</li>`).join('')}</ul></div></div>
    </div>
    <div class="radio-grid">
      ${radioCard('all', 'Use all recommended audiences', null, a.audienceResponse === 'all')}
      ${radioCard('before', 'Focus primarily on pre-arrival visitors', null, a.audienceResponse === 'before')}
      ${radioCard('inmarket', 'Focus primarily on visitors already nearby', null, a.audienceResponse === 'inmarket')}
      ${radioCard('choose', 'Let me choose audiences', null, a.audienceResponse === 'choose')}
    </div>
    <div id="audience-choose-wrap"></div>
  `;
  c.appendChild(body);
  wireRadios(body, (val) => {
    a.audienceResponse = val;
    if (val === 'all') a.customAudiences = { before: recs.before, inMarket: recs.inMarket };
    else if (val === 'before') a.customAudiences = { before: recs.before, inMarket: [] };
    else if (val === 'inmarket') a.customAudiences = { before: [], inMarket: recs.inMarket };
    else if (val === 'choose' && !a.customAudiences) a.customAudiences = { before: [...recs.before], inMarket: [...recs.inMarket] };
    rerender();
  });
  if (a.audienceResponse === 'choose') {
    const wrap = body.querySelector('#audience-choose-wrap');
    wrap.innerHTML = `
      <div class="report-grid" style="margin-top:14px;">
        <div><label class="field-label">Before-arrival audiences</label><div class="check-grid">${BEFORE_ARRIVAL_AUDIENCES.map(x => checkRow('b:'+x, x, a.customAudiences.before.includes(x))).join('')}</div></div>
        <div><label class="field-label">In-market audiences</label><div class="check-grid">${IN_MARKET_AUDIENCES.map(x => checkRow('i:'+x, x, a.customAudiences.inMarket.includes(x))).join('')}</div></div>
      </div>`;
    wireChecks(wrap, (val) => {
      const [side, label] = [val.slice(0,1), val.slice(2)];
      const list = side === 'b' ? a.customAudiences.before : a.customAudiences.inMarket;
      const idx = list.indexOf(label);
      if (idx >= 0) list.splice(idx, 1); else list.push(label);
      rerender();
    });
  }
  navRow(c, { nextDisabled: !a.audienceResponse, onNext: goNext });
}
/* getAudiences(a) now lives in report-template.js. */

/* ---- Step 11: Product plan (display) ---- */
/* getProductPlan(a) now lives in report-template.js. */
const PRODUCT_WHY = {
  'Programmatic display': 'Travel-intent and behavioral targeting across mobile and desktop.',
  'Location targeting and geofencing': 'Reach visitors at hotels, attractions, and travel corridors near you.',
  'Location look-back': 'Re-engage people who recently visited relevant tourism locations.',
  'Mobile display': 'Drive same-day decisions, directions, calls, and reservations.',
  'Mobile and online video': 'Show the customer experience to build interest before they arrive.',
  'Connected TV': 'Pre-arrival destination awareness in relevant feeder markets.',
  'Streaming radio': 'Reach road-trip travelers and destination planners.',
  'Podcast advertising': 'Align with travel, outdoor, food, or lifestyle listening audiences.',
  'Digital Out-of-Home': 'Presence near airports, hotels, and visitor corridors.',
  'Smart 1 Suite core tools': 'Review management, social planning, and a consolidated inbox.',
};
function stepProducts(c) {
  qHead(c, '📡', 'Your recommended Smart 1 media products', 'Paid search and paid social are not included in the automatic plan.');
  const products = getProductPlan(STATE.a);
  const body = document.createElement('div');
  body.innerHTML = `<div class="check-grid">${products.map(p => `
    <div class="check-row selected" style="cursor:default;"><div class="check-box">✓</div><div><b>${p}</b><div class="rc-sub">${PRODUCT_WHY[p] || ''}</div></div></div>
  `).join('')}</div>`;
  c.appendChild(body);
  navRow(c, { onNext: goNext });
}

/* ---- Step 12: Suite ---- */
function stepSuite(c) {
  qHead(c, '📬', 'Keep your visitor communications in one place', 'Smart 1 Suite core tools are included in every plan.');
  const body = document.createElement('div');
  body.innerHTML = `
    <div class="radio-grid" style="grid-template-columns:1fr 1fr 1fr;">
      <div class="radio-card selected" style="cursor:default;"><div class="radio-dot"></div><div><div class="rc-title">Review management</div><div class="rc-sub">Monitor, request, and respond to reviews.</div></div></div>
      <div class="radio-card selected" style="cursor:default;"><div class="radio-dot"></div><div><div class="rc-title">Social media planner</div><div class="rc-sub">Plan and schedule seasonal content.</div></div></div>
      <div class="radio-card selected" style="cursor:default;"><div class="radio-dot"></div><div><div class="rc-title">Consolidated messaging</div><div class="rc-sub">One inbox for visitor questions.</div></div></div>
    </div>
    <div class="callout" style="margin-top:16px;">Smart 1 Suite includes additional marketing automation, lead follow-up, scheduling, and customer communication options. These can be added after a Smart 1 review if they support your business goals.</div>
  `;
  c.appendChild(body);
  navRow(c, { onNext: goNext });
}

/* ---- Step 14: Budget & ROI ----
   There is no tier-picker step anymore — Smart 1 determines the Good /
   Better / Best plan level from the business's own answers (average
   value + category), and it's revealed as a suggestion on the final
   report rather than something the visitor chooses mid-wizard. This step
   still previews the resulting numbers so the plan feels concrete before
   the report — just without ever presenting "Good/Better/Best" as a
   choice to make. */
function stepBudget(c) {
  qHead(c, '📈', 'Your estimated seasonal return', 'Based on your business, average value, and industry-assumed acquisition cost.');
  const a = STATE.a;
  const profile = getCategoryProfile(a.category === '__other__' ? null : a.category);
  a.tier = recommendTier(a.avgValue || 0, profile.familyKey);
  const r = calcBudgetAndROI(a.tier, a.avgValue, profile.cac);
  a.budgetCalc = r;
  const body = document.createElement('div');
  body.innerHTML = `
    <div class="stat-grid">
      <div class="stat-card"><div class="st-label">Suggested monthly budget</div><div class="st-value">${money(r.monthlyBudget)}</div><div class="st-sub">${money(r.monthlyMin)}–${money(r.monthlyMax)} range</div></div>
      <div class="stat-card"><div class="st-label">Est. acquisition cost</div><div class="st-value">${money(r.cac)}</div><div class="st-sub">${profile.cac[0]}–${profile.cac[1]}% of ${profile.cacBasis}</div></div>
      <div class="stat-card"><div class="st-label">Est. bookings / customers</div><div class="st-value">${r.customers.toLocaleString()}</div><div class="st-sub">per month</div></div>
      <div class="stat-card"><div class="st-label">Est. monthly revenue</div><div class="st-value">${money(r.revenue)}</div><div class="st-sub">at ${money(a.avgValue)} avg. value</div></div>
      <div class="stat-card"><div class="st-label">Revenue-to-spend ratio</div><div class="st-value">${r.roas}x</div><div class="st-sub">estimated return</div></div>
      <div class="stat-card"><div class="st-label">Break-even customers</div><div class="st-value">${r.breakeven.toLocaleString()}</div><div class="st-sub">per month, to cover spend</div></div>
    </div>
    <div class="callout"><b>Industry planning assumption:</b> figures use a suggested budget level for your business and a ${profile.cac[0]}–${profile.cac[1]}% target acquisition cost for this category. These are estimates, not guarantees of performance. Your full suggested plan is on the next page.</div>
  `;
  c.appendChild(body);
  navRow(c, { onNext: goNext });
}

/* ---- Step 15: Seasonal budget distribution ---- */
function stepSeasonalSplit(c) {
  qHead(c, '📆', 'Spend more when tourism demand is stronger', 'Your budget is distributed across primary, shoulder, and slow seasons.');
  const a = STATE.a;
  const seasons = getSeasons(a);
  const dist = seasonalDistribution(a.budgetCalc.monthlyBudget, seasons);
  a.seasonalDist = dist;
  const rows = [
    { key: 'primary', label: 'Primary season', months: seasons.primary },
    { key: 'shoulder', label: 'Shoulder season', months: seasons.shoulder },
    { key: 'slow', label: 'Slow season', months: seasons.slow },
  ];
  const body = document.createElement('div');
  body.innerHTML = rows.map(row => `
    <div class="stat-card" style="margin-bottom:12px;">
      <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:6px;">
        <div><b style="color:var(--navy);">${row.label}</b> <span class="rc-sub">${monthsToRangeString(row.months)}</span></div>
        <div style="font-weight:800; color:var(--navy);">${dist[row.key].pct}% · ${money(dist[row.key].total)} total</div>
      </div>
      <div class="progress-track" style="margin-bottom:6px;"><div class="progress-fill" style="width:${dist[row.key].pct}%"></div></div>
      <div class="rc-sub">≈ ${money(dist[row.key].effectiveMonthly)}/mo across ${dist[row.key].monthCount} month${dist[row.key].monthCount>1?'s':''}</div>
    </div>
  `).join('') + `<div class="callout">Budget saved during reduced-delivery weather windows is reassigned to the next favorable-weather period, the next holiday travel window, or the primary/shoulder season.</div>`;
  c.appendChild(body);
  navRow(c, { onNext: goNext });
}

/* ---- Step 16: Final report + contact gate ----
   Progressive capture: this is the first (and only) place contact details
   are requested — the final gate that unlocks emailing the finished plan
   and the PDF. The step shows the full report (built from the same
   buildReportMarkup() the server renders for the PDF, so they can never
   drift apart), then asks for name/email/phone + how to proceed, and the
   submit button stays disabled until name + a valid email are present. */
const PLAN_CHOICES = ['Use this suggested plan','Request a custom plan','Speak with a Smart 1 strategist','Send me the report first'];
function canSubmitContact() {
  const contact = STATE.a.contact || {};
  return !!(contact.name && contact.name.trim() && contact.email && /\S+@\S+\.\S+/.test(contact.email));
}
// Measured client-side and sent with the submission so the backend can
// reject bot-speed fills without comparing against server wall-clock (the
// visitor's device clock may be skewed from the server's).
const PAGE_LOADED_AT = Date.now();

function stepReport(c) {
  const a = STATE.a;
  a.contact = a.contact || {};
  const body = document.createElement('div');
  const result = a.submitResult || null;

  body.innerHTML = `
    ${result ? reportConfirmationBanner(a, result) : ''}
    ${buildReportMarkup(a)}
    <div class="no-print" style="margin-top:20px;">
      ${result ? '' : `
        <label class="field-label" style="display:block; margin-bottom:8px;">Where should we send your plan &amp; PDF?</label>
        <div class="field-row">
          <div class="field"><label class="field-label">Contact name<span class="tag-req">Required</span></label><input type="text" id="c-name" value="${escapeAttr(a.contact.name||'')}" placeholder="First and last name" /></div>
          <div class="field"><label class="field-label">Email<span class="tag-req">Required</span></label><input type="email" id="c-email" value="${escapeAttr(a.contact.email||'')}" placeholder="name@company.com" /></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Phone<span class="tag-opt">Optional</span></label><input type="tel" id="c-phone" value="${escapeAttr(a.contact.phone||'')}" placeholder="(555) 555-5555" /></div>
          <div class="field">
            <label class="field-label">Preferred contact method<span class="tag-opt">Optional</span></label>
            <div class="choice-grid" id="contact-method-grid">${['Email','Phone','Text'].map(m => chip(m, m, a.contact.preferredMethod === m)).join('')}</div>
          </div>
        </div>
        <label class="field-label" style="display:block; margin-bottom:8px;">How would you like to proceed?<span class="tag-opt">Optional</span></label>
        <div class="choice-grid" id="plan-choice-grid" style="margin-bottom:16px;">${PLAN_CHOICES.map(ch => chip(ch, ch, a.planChoice === ch)).join('')}</div>
        <div style="position:absolute; left:-9999px; top:auto; width:1px; height:1px; overflow:hidden;" aria-hidden="true">
          <label for="s1_hp_field">Leave this field blank</label>
          <input type="text" id="s1_hp_field" name="s1_hp_field" tabindex="-1" autocomplete="off" />
        </div>
        <div id="submit-error" class="callout" style="display:none; border-color:#c0392b; color:#c0392b;"></div>
        <div class="nav-row">
          <button class="btn btn-secondary" id="btn-print">Print / Save as PDF</button>
          <button class="btn btn-primary" id="btn-get-plan" ${canSubmitContact() ? '' : 'disabled'}>Email me my plan &amp; PDF →</button>
        </div>
      `}
    </div>
  `;
  c.appendChild(body);

  if (!result) {
    ['name','email','phone'].forEach(f => body.querySelector('#c-'+f).addEventListener('input', (e) => { a.contact[f] = e.target.value; updateSubmitBtn(); }));
    wireChips(body.querySelector('#contact-method-grid'), (val) => { a.contact.preferredMethod = val; rerender(); });
    wireChips(body.querySelector('#plan-choice-grid'), (val) => { a.planChoice = val; rerender(); });
    body.querySelector('#btn-print').addEventListener('click', () => window.print());
    body.querySelector('#btn-get-plan').addEventListener('click', () => { if (canSubmitContact()) submitPlan(body); });
    const ctaBtn = body.querySelector('#btn-continue-report');
    if (ctaBtn) ctaBtn.addEventListener('click', () => body.querySelector('#c-name').scrollIntoView({ behavior: 'smooth', block: 'center' }));
  }

  function updateSubmitBtn() {
    const btn = body.querySelector('#btn-get-plan');
    if (btn) btn.disabled = !canSubmitContact();
  }

  const backRow = document.createElement('div');
  backRow.className = 'no-print';
  backRow.innerHTML = `<button class="btn btn-text" id="btn-report-back">← Back</button>`;
  c.insertBefore(backRow, body);
  backRow.querySelector('#btn-report-back').addEventListener('click', () => { STATE.step -= 1; render(); window.scrollTo({top:0,behavior:'smooth'}); });
}

function reportConfirmationBanner(a, result) {
  const leadNote = result.error
    ? `<div class="callout" style="border-color:#c0392b; color:#c0392b;">${escapeHtml(result.error)}</div>`
    : '';
  const pdfRow = result.pdf_url
    ? `<a class="btn btn-secondary" style="text-decoration:none; display:inline-block;" href="${escapeAttr(result.pdf_url)}" target="_blank" rel="noopener">Download PDF →</a>`
    : '';
  const summary = buildPlainTextSummary(a);
  const mailBody = encodeURIComponent(summary).slice(0, 6000);
  const firstName = (a.contact.name || '').split(' ')[0] || 'there';
  return `
    <div class="q-head no-print"><div class="q-icon">✅</div><div><h2 class="q-title">Thanks, ${escapeHtml(firstName)}!</h2><p class="q-sub">Your seasonal plan is ready below.${result.pdf_url ? ' A downloadable PDF is ready too.' : ''}</p></div></div>
    ${leadNote}
    <div class="nav-row no-print" style="margin-bottom:20px;">
      ${pdfRow}
      <a class="btn btn-primary" style="text-decoration:none; display:inline-block;" href="mailto:?subject=${encodeURIComponent('Smart 1 Seasonal Tourism Plan — ' + (a.businessName || a.contact.name))}&body=${mailBody}">Email this plan →</a>
    </div>
  `;
}

/* ---------------------------- loading overlay ----------------------------
   The full-screen navy overlay (wordmark + animated sun-over-waves SVG)
   lives in index.html so it can cover the page before any script runs.
   app.js hides it once the wizard has rendered, then re-uses it during the
   final submit — the 10–30 second AI + PDF + webhook wait — with rotating
   status captions instead of just changing the button text. */
let overlayCaptionTimer = null;
function showLoadingOverlay(captions) {
  const overlay = document.getElementById('s1-loading-overlay');
  if (!overlay) return;
  const status = document.getElementById('s1-overlay-status');
  if (status && captions && captions.length) {
    let i = 0;
    status.textContent = captions[0];
    clearInterval(overlayCaptionTimer);
    overlayCaptionTimer = setInterval(() => { i = (i + 1) % captions.length; status.textContent = captions[i]; }, 4000);
  }
  overlay.classList.remove('s1-overlay-hidden');
}
function hideLoadingOverlay() {
  const overlay = document.getElementById('s1-loading-overlay');
  if (!overlay) return;
  clearInterval(overlayCaptionTimer);
  overlayCaptionTimer = null;
  overlay.classList.add('s1-overlay-hidden');
}

/* Posts the wizard's answers to this app's own backend (server.js), which
   builds the PDF, uploads it to Cloudinary, and relays the lead to the
   Smart 1 Suite webhook. Same-origin fetch — works unmodified whether the
   tool is loaded directly or embedded in an iframe on the landing page. */
async function submitPlan(body) {
  const a = STATE.a;
  if (a.submitted) return;
  const btn = document.getElementById('btn-get-plan');
  const errBox = body.querySelector('#submit-error');
  if (errBox) { errBox.style.display = 'none'; errBox.textContent = ''; }
  if (btn) { btn.disabled = true; btn.dataset.origLabel = btn.dataset.origLabel || btn.textContent; btn.textContent = 'Building your plan…'; }
  showLoadingOverlay(['Mapping your season…', 'Analyzing your market…', 'Building your PDF…']);

  const hp = body.querySelector('#s1_hp_field');
  const payload = { ...a, s1_hp: hp ? hp.value : '', form_elapsed_ms: Date.now() - PAGE_LOADED_AT };
  delete payload._defaultSeasons;
  delete payload.aiSuggestion; // internal client state — not part of the CRM payload
  // The PDF is laid out server-side from this, so the plan in the download is
  // the same plan shown on screen — computed once, here, not a second time in
  // Python where the two could drift apart.
  try {
    payload.report_model = window.Smart1ReportTemplate.buildReportModel(a);
  } catch (e) {
    // A PDF is worth having but never worth losing the lead over.
    console.warn('report model failed', e);
  }

  try {
    const res = await fetch('api/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'We could not submit your plan. Please try again.');
    a.submitted = true;
    a.submitResult = data;
    postToolEvent('lead_submitted');
    // Server-generated (optional) narrative — merge it in so the on-screen
    // confirmation view matches the PDF the customer just received. Absent
    // whenever OPENAI_API_KEY isn't configured or generation failed; the
    // report renders exactly as before in that case (see report-template.js).
    if (Array.isArray(data.ai_narrative) && data.ai_narrative.length) a.aiNarrative = data.ai_narrative;
    hideLoadingOverlay();
    rerender();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (err) {
    hideLoadingOverlay();
    if (errBox) { errBox.textContent = (err && err.message) || 'Something went wrong. Please check your connection and try again.'; errBox.style.display = 'block'; }
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.origLabel || 'Email me my plan & PDF →'; }
  }
}
function buildPlainTextSummary(a) {
  const seasons = getSeasons(a);
  const lines = [
    'SMART 1 SEASONAL TOURISM MARKETING PLAN',
    `Business: ${a.businessName || ''}${a.website ? ' (' + a.website + ')' : ''}`,
    `Contact: ${a.contact.name} (${a.contact.email}${a.contact.phone ? ', ' + a.contact.phone : ''})`,
    `Category: ${a.category === '__other__' ? a.categoryOther : a.category}`,
    `ZIP: ${a.zip}${a.city ? ' - ' + a.city + ', ' + a.state : ''}`,
    `Primary activity: ${a.activity} (${a.purchaseType})`,
    `Average value: ${money(a.avgValue)}`,
    `Primary goal: ${a.objective}`,
    `Primary season: ${monthsToRangeString(seasons.primary)}`,
    `Shoulder season: ${monthsToRangeString(seasons.shoulder)}`,
    `Slow season: ${monthsToRangeString(seasons.slow)}`,
    `Suggested tier: ${a.tier}`,
    `Monthly budget: ${money(a.budgetCalc.monthlyBudget)}`,
    `Estimated monthly revenue: ${money(a.budgetCalc.revenue)}`,
    `Estimated return: ${a.budgetCalc.roas}x`,
    `Requested next step: ${a.planChoice || 'Not specified'}`,
    '',
    'This is a suggested marketing plan using estimated market conditions and industry assumptions. Results are not guaranteed. Contact Smart 1 Marketing to customize the targeting, media products, timing, or budget.',
  ];
  return lines.join('\n');
}

/* ---------------------------- embed resize ----------------------------
   When this tool is loaded inside an <iframe> (e.g. on a landing page),
   post our document height to the parent window on every change so the
   host page can size the iframe to fit instead of showing a scrollbar
   or clipping content. No-ops harmlessly when not embedded. -------- */
function postEmbedHeight() {
  if (window.self === window.top) return;
  const h = Math.ceil(document.documentElement.getBoundingClientRect().height);
  window.parent.postMessage({ type: 'smart1-embed-resize', height: h }, '*');
}
if (window.self !== window.top) {
  window.addEventListener('load', postEmbedHeight);
  window.addEventListener('resize', postEmbedHeight);
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(postEmbedHeight).observe(document.documentElement);
  } else {
    setInterval(postEmbedHeight, 500);
  }
}

/* ---------------------------- boot ---------------------------- */
render();
postEmbedHeight();
hideLoadingOverlay(); // wizard has rendered — dismiss the page-load overlay
