/* Smart 1 Marketing — Marketing Efficiency Audit™
   Scoring, benchmarks, and formulas from the Client Profit Leak Assessment™,
   Marketing ROI Calculator Toolkit™, and Marketing Expense Benchmark Report™. */

/* ---------- Benchmark data ---------- */
const INDUSTRIES = {
  'Home Services':          { budget:[8,15],  cpl:[40,250],  mix:[70,30], facts:['Search intent converts far better than display for urgent needs','Google Local Services Ads charge per lead, not per click','Review count and rating drive map-pack visibility more than website design','Response time under five minutes materially raises booking rates'], note:'Search and local service ads dominate; direct mail and radio still pull in dense suburban markets.' },
  'HVAC':                   { budget:[8,15],  cpl:[50,250],  mix:[65,35], facts:['Demand concentrates in the first heat wave and first freeze','Maintenance-agreement customers cost far less to sell to than new ones','Lead costs climb steeply during peak weeks as competitors bid up','Off-season spend often buys the cheapest leads of the year'], note:'Highly seasonal. Demand spikes in the first heat wave and first freeze, and cost per lead climbs with it.' },
  'Plumbing':               { budget:[8,15],  cpl:[40,200],  mix:[70,30], facts:['Emergency searches happen at all hours; ad scheduling that stops at 5pm loses them','Drain and water-heater terms carry very different lead costs','Repeat and referral work is a large share of revenue for established shops','Map-pack presence often outperforms the website for call volume'], note:'Emergency demand rewards always-on search presence over campaign bursts.' },
  'Roofing':                { budget:[8,15],  cpl:[100,450], mix:[60,40], facts:['Storm events reset the competitive landscape within days','Insurance-driven work behaves differently from retail replacement','Lead costs are among the highest in home services','Door-knocking and digital compete for the same post-storm window'], note:'Storm-driven. Lead costs swing hard with weather events and competitor surges.' },
  'Solar':                  { budget:[8,15],  cpl:[150,500], mix:[75,25], facts:['Sales cycles commonly run 30 to 90 days','Lead-to-appointment rate matters more than raw lead count','Incentive and policy changes swing demand sharply','Purchased leads are frequently sold to several installers at once'], note:'Long sales cycles and expensive leads; close rate matters more than lead volume.' },
  'Construction':           { budget:[5,10],  cpl:null,      mix:[55,45], facts:['Referral and repeat business dominate revenue','Portfolio and project photography outperform ad copy','Long cycles make monthly attribution misleading','Trade relationships often generate more work than paid media'], note:'Referral and relationship driven; digital mostly supports credibility rather than direct lead generation.' },
  'Healthcare':             { budget:[5,12],  cpl:[50,300],  mix:[60,40], facts:['Reviews and reputation weigh more heavily than ad spend','Insurance acceptance is a primary search filter','Patient acquisition cost varies enormously by service line','Compliance limits what can be said in advertising'], note:'Reputation and reviews carry disproportionate weight relative to ad spend.' },
  'Legal':                  { budget:[7,15],  cpl:[150,700], mix:[70,30], facts:['Among the most expensive keywords in all of search','Practice area drives cost more than geography','Case value varies so widely that blended cost per lead can mislead','Intake handling often loses more cases than marketing produces'], note:'Among the most expensive keywords in search. Practice area drives cost more than geography.' },
  'Insurance':              { budget:[5,12],  cpl:[50,250],  mix:[60,40], facts:['Purchased leads are commonly resold to several agents','Retention economics usually beat new-customer acquisition','Bundling raises lifetime value substantially','Carrier co-op funds frequently go unused'], note:'Lead-service purchasing is common and often duplicates organic lead flow.' },
  'Real Estate':            { budget:[5,15],  cpl:[50,300],  mix:[65,35], facts:['Agent-funded spend is often invisible in company books','Listing inventory drives lead volume more than ad budget','Portal leads and owned leads have very different close rates','Sphere-of-influence marketing outperforms paid on cost per transaction'], note:'Agent-level spend is often invisible in company books; ask what agents fund themselves.' },
  'Restaurants / Catering': { budget:[3,8],   cpl:[15,100],  mix:[70,30], facts:['Attribution is weak; measure covers and repeat visits, not leads','Local social discovery drives more traffic than search for most concepts','Third-party delivery commissions function as acquisition cost','Email and SMS lists produce the cheapest repeat visits'], note:'Social and local discovery lead. Attribution is weak, so foot traffic and repeat rate matter more than lead counts.' },
  'Retail':                 { budget:[5,10],  cpl:null,      mix:[65,35], facts:['The promotional calendar drives spend more than strategy','Q4 typically carries a disproportionate share of budget','In-store conversion is rarely tracked against ad spend','Loyalty programs materially raise lifetime value'], note:'Promotional calendar drives spend; Q4 typically carries a disproportionate share.' },
  'E-commerce':             { budget:[10,20], cpl:null,      mix:[90,10], facts:['Return on ad spend and repeat purchase rate matter more than cost per lead','Rising acquisition costs make first-order profitability rare','Email and SMS drive a large share of profitable revenue','Shipping and returns policy affect conversion as much as ads'], note:'Nearly all digital. Return on ad spend and repeat purchase rate matter more than cost per lead.' },
  'Automotive':             { budget:[6,12],  cpl:null,      mix:[55,45], facts:['Manufacturer co-op funds often offset a large share of spend','Traditional media remains a heavy line item','Third-party listing sites take a large share of the digital budget','Service and parts marketing is usually underfunded relative to its margin'], note:'Traditional media remains heavy; co-op funds from manufacturers often offset a large share of spend.' },
  'Manufacturing':          { budget:[2,7],   cpl:null,      mix:[50,50], facts:['Trade shows and trade publications still take a meaningful share','Sales cycles can run a year or more','Distributor relationships often matter more than direct demand','Technical content outperforms advertising for qualified inquiries'], note:'Trade shows and trade publications still take a meaningful slice of budget.' },
  'Professional / B2B Services': { budget:[5,12], cpl:[100,500], mix:[70,30], facts:['Referral remains the leading source of new business','Long consideration cycles make monthly lead counts misleading','Content and speaking build pipeline that paid media cannot','Deal size variation makes blended cost per lead nearly meaningless'], note:'Long consideration cycles; content and referral outrank paid volume.' },
  'Senior Living':          { budget:[6,12],  cpl:null,      mix:[55,45], facts:['Adult children are usually the decision makers, not the resident','Sales cycles commonly run months','Tour-to-move-in rate matters more than inquiry volume','Reputation and referral sources drive a large share of occupancy'], note:'Adult children are usually the decision makers, which changes both targeting and channel mix.' },
  'Campgrounds / RV':       { budget:[4,10],  cpl:null,      mix:[70,30], facts:['Booking windows run months ahead of the stay','Demand is sharply seasonal and weather sensitive','Repeat guests are far cheaper to reach than new ones','Third-party booking platforms take a significant commission'], note:'Sharply seasonal with booking windows months ahead of the stay.' },
  'Hospitality':            { budget:[4,12],  cpl:null,      mix:[70,30], facts:['Online travel agency commissions function as acquisition cost','Direct bookings are substantially more profitable than OTA bookings','Review scores directly affect achievable rate','Demand is seasonal and event driven'], note:'Online travel agency commissions often sit outside the marketing line but function as acquisition cost.' },
  'Other / Not listed':     { budget:[5,12],  cpl:null,      mix:[65,35], facts:['Benchmark ranges here are cross-industry and directional only','Measurement quality usually explains more variance than budget size','Consistency of spend tends to outperform campaign bursts','Cost per lead means little without close rate and average sale'], note:'General cross-industry range. Treat as a rough guide only.' },
};

const TRADITIONAL_MEDIA = [
  'Broadcast TV', 'Cable TV', 'Radio', 'Newspaper', 'Magazine / print',
  'Direct mail', 'Billboards / outdoor', 'Yellow pages / directories',
  'Event sponsorships', 'Trade shows', 'Vehicle wraps', 'Door hangers / flyers',
];

const AGE_RANGES = ['18–24', '25–34', '35–44', '45–54', '55–64', '65+'];

const FLAGS = [
  { id:'f2',  label:'Cannot clearly explain marketing ROI', points:3 },
  { id:'f3',  label:'Lead volume has declined over the last 6 months', points:3 },
  { id:'f4',  label:'Marketing expenses increased without revenue growth', points:4 },
  { id:'f6',  label:'Website receives traffic but few inquiries', points:3 },
  { id:'f7',  label:'Does not know cost per lead', points:4 },
  { id:'f8',  label:'Does not track conversions', points:4 },
  { id:'f9',  label:'Agency reporting is difficult to understand', points:2 },
  { id:'f10', label:'No marketing strategy review within 12 months', points:3 },
];

/* Total weight of all warning signs, used to normalize the score. */
const FLAG_MAX = FLAGS.reduce((t, f) => t + f.points, 0);

const SPEND_ITEMS = [
  { name:'Google Advertising', max:100000, step:250,
    help:'Google Search and Shopping ads, Performance Max, YouTube, and Local Services Ads (the "Google Guaranteed" badge). Media spend plus any management fee billed inside the account.' },
  { name:'Social Media Advertising', max:100000, step:250,
    help:'Paid ads on Facebook, Instagram, TikTok, LinkedIn, Nextdoor, or Pinterest. Boosted posts count. Unpaid posting time belongs under in-house staff.' },
  { name:'SEO', max:25000, step:100,
    help:'Retainers for search optimization: content writing, link building, technical fixes, Google Business Profile management, citation and directory listings.' },
  { name:'Streaming TV', max:100000, step:250,
    help:'Connected TV and over-the-top video — Hulu, Roku, Amazon Fire, Samsung TV Plus, Peacock. Streaming audio like Spotify or Pandora goes here too.' },
  { name:'Display / Programmatic Ads', max:100000, step:250,
    help:'Banner and video ads bought across websites and apps through a demand-side platform. Includes geofencing and addressable audience buys.' },
  { name:'Retargeting', max:25000, step:100,
    help:'Ads shown to people who already visited the site. Often billed inside a Google or social account — only enter it here if it is a separate line item.' },
  { name:'Linear TV Advertising', max:50000, step:250,
    help:'Traditional broadcast and cable spots — local network affiliates, cable inserts, regional sports. Includes production if it is billed monthly.' },
  { name:'Radio Advertising', max:25000, step:100,
    help:'Terrestrial radio spots, live reads, station sponsorships, remotes, and traffic or weather sponsorships.' },
  { name:'Billboard Advertising', max:25000, step:100,
    help:'Out-of-home: static and digital billboards, bus and transit ads, bench and shelter placements, mall and airport signage.' },
  { name:'Direct Mail', max:25000, step:100,
    help:'Postcards, EDDM saturation mail, shared mail packs like Valpak or Money Mailer, door hangers, and newsletters. Include printing and postage.' },
  { name:'Website Expenses', max:25000, step:100,
    help:'Hosting, domain, SSL, maintenance retainer, plugins, chat widgets, booking tools, and any ongoing design or development work.' },
  { name:'Marketing Software', max:25000, step:100,
    help:'CRM, marketing automation, call tracking, review management, scheduling, analytics, and reporting dashboards. Subscription fees only.' },
  { name:'Agency Fees', max:25000, step:100,
    help:'Retainers and management fees paid to an agency or consultant, separate from the media they buy. If a single invoice bundles both, split it.' },
  { name:'Email Marketing', max:25000, step:100,
    help:'Platform costs and campaign management — Mailchimp, Constant Contact, Klaviyo, or an agency running newsletters and drip campaigns.' },
  { name:'In-house Marketing Staff', max:50000, step:250,
    help:'Wages, payroll taxes, and benefits for the share of employee time spent on marketing. A half-time coordinator counts at half their loaded cost.' },
  { name:'Live Events / Sponsorships', max:25000, step:100,
    help:'Home shows, fairs, festivals, open houses, seminars, team and league sponsorships, booth fees, and event giveaways. Enter the monthly average.' },
  { name:'Other', max:25000, step:100,
    help:'Anything not covered above — vehicle wraps, promotional items, print collateral, trade publications, yellow pages, or spend nobody can categorize.' },
];

const SPEND_CATEGORIES = SPEND_ITEMS.map((i) => i.name);

/* ---------- State ---------- */
const state = {
  flags:{}, spend:{}, lead:null, results:null, analysis:null, pdfUrl:null, unlocked:false,
  profile:{}, media:new Set(), ages:new Set(), expenses:null, uploadMode:'ai',
  competitors:[], comparisons:{}, competitorBasis:'', website:null, market:null, scanRunning:false,
};
let gaugeTimer = null;

/* ---------- Helpers ---------- */
const $ = (s) => document.querySelector(s);
const num = (id) => { const v = parseFloat(($(id)?.value ?? '').toString().replace(/,/g,'')); return isFinite(v) ? v : 0; };
const usd = (n, dp = 0) => (n == null || !isFinite(n)) ? '—' :
  '$' + n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });

/* ---------- Build dynamic UI ---------- */
function buildUI() {
  const sel = $('#industry');
  Object.keys(INDUSTRIES).forEach((k) => {
    const o = document.createElement('option');
    o.value = k; o.textContent = k; sel.appendChild(o);
  });
  sel.value = 'Home Services';

  $('#flags').innerHTML = FLAGS.map((f) => `
    <div class="flag">
      <p>${f.label}</p>
      <div class="toggle tri" data-flag="${f.id}" role="group" aria-label="${f.label}">
        <button type="button" data-v="yes">Yes</button>
        <button type="button" data-v="no">No</button>
        <button type="button" data-v="unsure">Unsure</button>
      </div>
    </div>`).join('');

  $('#flags').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-v]');
    if (!btn) return;
    const group = btn.parentElement;
    group.querySelectorAll('button').forEach((b) => { b.classList.remove('sel'); b.setAttribute('aria-pressed', 'false'); });
    btn.classList.add('sel');
    btn.setAttribute('aria-pressed', 'true');
    state.flags[group.dataset.flag] = btn.dataset.v;
    updateLeakTotal();
  });

  $('#spend').innerHTML = SPEND_ITEMS.map((it, i) => `
    <div class="sline" data-cat="${esc(it.name)}">
      <div class="sline-head">
        <span class="sline-name">${esc(it.name)}
          <button type="button" class="info" aria-label="What counts as ${esc(it.name)}" aria-expanded="false" data-info="${i}">i</button>
        </span>
        <div class="sline-amt">
          <span class="dollar">$</span>
          <input type="number" class="amt-input" min="0" max="${it.max}" step="${it.step}"
                 data-spend="${esc(it.name)}" id="sp${i}" placeholder="0" aria-label="${esc(it.name)} monthly amount">
        </div>
      </div>
      <input type="range" class="slider" min="0" max="${it.max}" step="${it.step}" value="0"
             data-slider="${esc(it.name)}" aria-label="${esc(it.name)} slider">
      <div class="sline-scale"><span>$0</span><span>${usd(it.max)}+</span></div>
      <p class="sline-help" id="info${i}" hidden>${esc(it.help)}</p>
    </div>`).join('');

  const syncSpend = (name, value, from) => {
    const v = Math.max(0, Number(value) || 0);
    state.spend[name] = v;
    const row = $('#spend').querySelector(`.sline[data-cat="${CSS.escape(name)}"]`);
    if (!row) return;
    const slider = row.querySelector('.slider');
    const input = row.querySelector('.amt-input');
    // The slider is capped; the box is not, so a bigger number parks the handle at max
    if (from !== 'slider') slider.value = Math.min(v, Number(slider.max));
    if (from !== 'input') input.value = v > 0 ? v : '';
    row.classList.toggle('active', v > 0);
    paintSlider(slider);
    $('#spendTotal').textContent = usd(totalSpend());
  };

  $('#spend').addEventListener('input', (e) => {
    if (e.target.dataset.slider) syncSpend(e.target.dataset.slider, e.target.value, 'slider');
    else if (e.target.dataset.spend) syncSpend(e.target.dataset.spend, e.target.value, 'input');
  });

  $('#spend').addEventListener('click', (e) => {
    const btn = e.target.closest('.info');
    if (!btn) return;
    const help = $(`#info${btn.dataset.info}`);
    const open = !help.hidden;
    help.hidden = open;
    btn.setAttribute('aria-expanded', String(!open));
    btn.classList.toggle('on', !open);
  });

  $('#spend').querySelectorAll('.slider').forEach(paintSlider);

  buildChips('#traditionalMedia', TRADITIONAL_MEDIA, state.media);
  buildChips('#ageRanges', AGE_RANGES, state.ages);
  buildTriToggles();
  buildUpload();

  buildCompetitors();
  buildScan();
  buildCountSliders();
  buildInfoBubbles();

  $('#ageAll').addEventListener('click', () => {
    const all = state.ages.size === AGE_RANGES.length;
    state.ages.clear();
    if (!all) AGE_RANGES.forEach((a) => state.ages.add(a));
    $('#ageRanges').querySelectorAll('.chip-btn').forEach((b) => {
      b.classList.toggle('sel', !all);
      b.setAttribute('aria-pressed', String(!all));
    });
    $('#ageAll').textContent = all ? 'Select all' : 'Clear all';
  });

  // Prefill the scan box from the snapshot, and scan in the background once we have a URL
  $('#website').addEventListener('change', () => {
    const url = $('#website').value.trim();
    if (!$('#scanUrl').value) $('#scanUrl').value = url;
    if (url && !state.website && !state.scanRunning) runScan({ background: true });
  });

  // Reveal the training question only when some work is done in house
  $('#buyingModel').addEventListener('change', (e) => {
    const inHouse = /in house/i.test(e.target.value);
    $('#trainingWrap').hidden = !inHouse;
    if (!inHouse) delete state.profile.providesTraining;
  });
}

/** Paints the filled portion of a range input, since browsers don't do it natively. */
/** Locations and vendor count, shown as sliders with a live read-out. */
function buildCountSliders() {
  [['locations', '#locationsVal', 20], ['vendors', '#vendorsVal', 20]].forEach(([id, out, max]) => {
    const el = $(`#${id}`);
    if (!el) return;
    const sync = () => {
      const v = Number(el.value);
      $(out).textContent = v >= max ? `${max}+` : String(v);
      paintSlider(el);
    };
    el.addEventListener('input', sync);
    sync();
  });
}

/** Info circles that reveal a short explanation beneath the question. */
function buildInfoBubbles() {
  const map = { loc: '#infoLoc', ven: '#infoVen', dig: '#infoDig', trad: '#infoTrad', leads: '#infoLeads' };
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-count-info],[data-q-info]');
    if (!btn) return;
    e.preventDefault();
    const key = btn.dataset.countInfo || btn.dataset.qInfo;
    const help = $(map[key]);
    if (!help) return;
    const open = !help.hidden;
    help.hidden = open;
    btn.setAttribute('aria-expanded', String(!open));
    btn.classList.toggle('on', !open);
  });
}

function paintSlider(el) {
  const pct = (Number(el.value) / Number(el.max)) * 100;
  el.style.setProperty('--fill', `${pct}%`);
}

function buildChips(sel, items, store) {
  const host = $(sel);
  if (!host) return;
  host.innerHTML = items.map((t) => `<button type="button" class="chip-btn" data-val="${t}">${t}</button>`).join('');
  host.addEventListener('click', (e) => {
    const btn = e.target.closest('.chip-btn');
    if (!btn) return;
    const v = btn.dataset.val;
    if (store.has(v)) { store.delete(v); btn.classList.remove('sel'); btn.setAttribute('aria-pressed', 'false'); }
    else { store.add(v); btn.classList.add('sel'); btn.setAttribute('aria-pressed', 'true'); }
  });
}

function buildTriToggles() {
  document.querySelectorAll('.toggle[data-q]').forEach((group) => {
    group.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-v]');
      if (!btn) return;
      group.querySelectorAll('button').forEach((b) => { b.classList.remove('sel'); b.setAttribute('aria-pressed', 'false'); });
      btn.classList.add('sel');
      btn.setAttribute('aria-pressed', 'true');
      state.profile[group.dataset.q] = btn.dataset.v;
      if (group.dataset.q === 'seasonalMarketing') $('#seasonWrap').hidden = btn.dataset.v !== 'yes';
      if (group.dataset.q === 'liveEvents') $('#eventsWrap').hidden = btn.dataset.v !== 'yes';
    });
  });
}

const totalSpend = () => Object.values(state.spend).reduce((a, b) => a + (b || 0), 0);
/* An unknown is itself a warning sign, so "unsure" carries the same weight as "yes". */
const isFlagged = (v) => v === 'yes' || v === 'unsure';
const leakPoints = () => FLAGS.reduce((sum, f) => sum + (isFlagged(state.flags[f.id]) ? f.points : 0), 0);
const answeredCount = () => FLAGS.filter((f) => state.flags[f.id]).length;

function updateLeakTotal() {
  const n = answeredCount();
  $('#leakRunning').textContent = n === FLAGS.length
    ? `${FLAGS.length} of ${FLAGS.length} answered`
    : `${n} of ${FLAGS.length} answered`;
}

/* ---------- Lead identity and attribution ----------
   The standalone build fired a lead twice: once here, as an unauthenticated
   "we lost them mid-form" signal with no contact info, and once at the gate.
   Smart 1 Hub's lead store (hub/leads.py) refuses a submission carrying
   neither an email nor a phone, by design -- a contactless row would read as
   a live prospect on every report that counts leads. So there is one
   delivery now, made automatically once the visitor unlocks their results
   (see the #unlock handler and buildPdf() below); the abandoned-form signal
   is gone rather than posted somewhere it would only ever be refused. The
   save-and-resume behavior lower in this file still protects an
   interrupted visitor's answers in this browser's own localStorage. */
const LEAD_ID = (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(16).slice(2));
const ATTR = (() => {
  const q = new URLSearchParams(location.search);
  const keys = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'gclid', 'fbclid'];
  const o = {};
  keys.forEach((k) => { const v = q.get(k); if (v) o[k] = v.slice(0, 200); });
  o.referrer_url = (document.referrer || '').slice(0, 300);
  o.landing_page_url = (q.get('s1_ref') || location.href).slice(0, 300);
  return o;
})();

/* ---------- Embed mode ---------- */
const EMBED = new URLSearchParams(location.search).get('embed') === '1';
if (EMBED) document.documentElement.classList.add('embed');

let lastHeight = 0;
function postHeight(force) {
  if (!EMBED) return;
  const h = Math.ceil(document.documentElement.scrollHeight);
  if (!force && Math.abs(h - lastHeight) < 8) return;
  lastHeight = h;
  parent.postMessage({ type: 's1-audit-height', height: h }, '*');
}
if (EMBED) {
  if (window.ResizeObserver) new ResizeObserver(() => postHeight()).observe(document.body);
  window.addEventListener('load', () => postHeight(true));
  setInterval(() => postHeight(), 800);
}



/* ---------- Competitors ---------- */
function buildCompetitors() {
  const find = $('#findCompetitors');
  if (!find) return;
  find.addEventListener('click', discoverCompetitors);

  $('#addCompetitor').addEventListener('click', () => {
    const name = $('#newCompName').value.trim();
    const site = $('#newCompSite').value.trim().replace(/^https?:\/\//i, '').replace(/\/.*$/, '');
    if (!name && !site) return;
    addConfirmed({ name: name || site, website: site, type: 'added', why: 'Added by the partner', confidence: 'high' });
    $('#newCompName').value = '';
    $('#newCompSite').value = '';
  });

  $('#compSuggestions').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const card = btn.closest('.sug');
    const c = JSON.parse(card.dataset.comp);
    if (btn.dataset.act === 'yes') addConfirmed(c);
    card.remove();
    if (!$('#compSuggestions').querySelector('.sug')) $('#compSuggestions').hidden = true;
  });

  $('#confirmedRows').addEventListener('click', (e) => {
    const btn = e.target.closest('.comp-del');
    if (!btn) return;
    const i = Number(btn.dataset.i);
    state.competitors.splice(i, 1);
    renderConfirmed();
  });
}

function compStatus(kind, message) {
  const el = $('#compStatus');
  el.hidden = false;
  el.className = 'upload-status' + (kind === 'error' ? ' err' : kind === 'ok' ? ' ok' : '');
  el.innerHTML = kind === 'working'
    ? `<svg class="spinner" viewBox="0 0 56 56" width="22" height="22" aria-hidden="true">
         <circle class="sp-track" cx="28" cy="28" r="22" fill="none" stroke-width="5"/>
         <circle class="sp-arc" cx="28" cy="28" r="22" fill="none" stroke-width="5" stroke-linecap="round"/>
       </svg><span>${esc(message)}</span>`
    : esc(message);
}

async function discoverCompetitors() {
  const btn = $('#findCompetitors');
  btn.disabled = true;
  compStatus('working', 'Looking up businesses that compete in this market…');
  try {
    const res = await fetch('api/competitors', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ context: {
        website: $('#website').value.trim(),
        clientName: $('#clientName').value.trim(),
        zipCode: $('#zipCode').value.trim(),
        cityMarket: $('#cityMarket').value.trim(),
        industry: $('#industry').value,
        serviceRadius: $('#serviceRadius')?.value || '',
      } }),
    });
    const data = await res.json();
    const list = data.competitors || [];
    if (!list.length) {
      compStatus('error', data.basis || 'No competitors could be suggested for this market. Add them manually below.');
      return;
    }
    state.competitorBasis = data.basis || '';
    compStatus('ok', `${list.length} possible competitors. Confirm the ones that actually compete.${data.note ? ' ' + data.note : ''}`);
    renderSuggestions(list);
  } catch (err) {
    compStatus('error', 'Competitor lookup failed. Add them manually below.');
  } finally {
    btn.disabled = false;
  }
}

function renderSuggestions(list) {
  const host = $('#compSuggestions');
  host.hidden = false;
  host.innerHTML = list.map((c) => `
    <div class="sug" data-comp='${esc(JSON.stringify(c))}'>
      <div class="sug-main">
        <b>${esc(c.name)}</b>
        <span class="sug-meta">${c.website ? esc(c.website) : 'no website found'} · ${esc(c.type)}${c.why ? ` · ${esc(c.why)}` : ''}</span>
      </div>
      <div class="sug-acts">
        <button type="button" class="sug-yes" data-act="yes">Competitor</button>
        <button type="button" class="sug-no" data-act="no">Not one</button>
      </div>
    </div>`).join('');
}

function addConfirmed(c) {
  if (state.competitors.some((x) => x.name.toLowerCase() === c.name.toLowerCase())) return;
  state.competitors.push(c);
  renderConfirmed();
  if (c.website) queueComparison(c);
}

function renderConfirmed() {
  const wrap = $('#compConfirmed');
  wrap.hidden = state.competitors.length === 0;
  $('#confirmedRows').innerHTML = state.competitors.map((c, i) => {
    const cmp = state.comparisons[c.name];
    const badge = !c.website ? '<span class="cmp-badge none">no site to check</span>'
      : cmp === 'pending' ? '<span class="cmp-badge pending">checking site…</span>'
      : cmp && cmp.ok ? '<span class="cmp-badge done">site checked</span>'
      : cmp ? '<span class="cmp-badge fail">site unreachable</span>' : '';
    return `<div class="comp-item confirmed">
      <div><b>${esc(c.name)}</b><span>${c.website ? esc(c.website) : 'no website'}</span>${badge}</div>
      <button type="button" class="comp-del" data-i="${i}" aria-label="Remove ${esc(c.name)}">&times;</button>
    </div>`;
  }).join('');
}

/** Scan and compare a competitor's site without blocking the questionnaire. */
function queueComparison(c) {
  state.comparisons[c.name] = 'pending';
  renderConfirmed();
  fetch('api/compare', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ competitor: c, clientScan: state.website?.scan || null }),
  })
    .then((r) => r.json())
    .then((d) => { state.comparisons[c.name] = d; renderConfirmed(); })
    .catch(() => { state.comparisons[c.name] = { ok: false }; renderConfirmed(); });
}

function readCompetitors() {
  return state.competitors.map((c) => ({
    name: c.name, website: c.website, type: c.type, why: c.why,
    comparison: state.comparisons[c.name] && state.comparisons[c.name].ok
      ? state.comparisons[c.name] : null,
  }));
}

/* ---------- Website scan ---------- */
function buildScan() {
  const btn = $('#scanBtn');
  if (!btn) return;
  btn.addEventListener('click', runScan);
  $('#scanUrl').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runScan(); } });
}

function scanStatus(kind, message) {
  const el = $('#scanStatus');
  el.hidden = false;
  el.className = 'upload-status' + (kind === 'error' ? ' err' : kind === 'ok' ? ' ok' : '');
  el.innerHTML = kind === 'working'
    ? `<svg class="spinner" viewBox="0 0 56 56" width="22" height="22" aria-hidden="true">
         <circle class="sp-track" cx="28" cy="28" r="22" fill="none" stroke-width="5"/>
         <circle class="sp-arc" cx="28" cy="28" r="22" fill="none" stroke-width="5" stroke-linecap="round"/>
       </svg><span>${esc(message)}</span>`
    : esc(message);
}

async function runScan(opts = {}) {
  const url = $('#scanUrl').value.trim() || $('#website').value.trim();
  if (!url) { if (!opts.background) scanStatus('error', 'Enter the website address first.'); return; }
  if (state.scanRunning) return;

  state.scanRunning = true;
  $('#scanResult').hidden = true;
  $('#scanBtn').disabled = true;
  scanStatus('working', opts.background
    ? `Checking ${url} in the background — carry on, results appear in the report.`
    : `Reading ${url} for conversion points…`);

  try {
    const res = await fetch('api/website', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url,
        context: {
          clientName: $('#clientName').value.trim(),
          industry: $('#industry').value,
          serviceArea: $('#cityMarket').value.trim(),
          monthlySpend: totalSpend(),
        },
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'The scan failed.');

    state.website = { url, scan: data.scan, analysis: data.analysis || null };
    const points = data.scan.conversionPoints;
    scanStatus('ok', `Scanned ${data.scan.finalUrl} — ${points} conversion point${points === 1 ? '' : 's'} found.`);
    renderScanResult(data);
  } catch (err) {
    scanStatus('error', err.message || 'That site could not be scanned.');
  } finally {
    state.scanRunning = false;
    $('#scanBtn').disabled = false;
  }
}

function renderScanResult(d) {
  const s2 = d.scan, a = d.analysis;
  $('#scanResult').hidden = false;
  $('#scanResult').innerHTML = `
    <div class="er-head">
      <b>${esc(s2.title || s2.finalUrl)}</b>
      <span>${s2.counts.forms} form${s2.counts.forms === 1 ? '' : 's'} ·
        ${s2.counts.telLinks} click-to-call · ${s2.trackers.length} tracking tag${s2.trackers.length === 1 ? '' : 's'}</span>
    </div>
    ${s2.found.map((f) => `<div class="er-row"><span class="cat scan-yes">${esc(f)}</span></div>`).join('')}
    ${s2.missing.map((f) => `<div class="er-row"><span class="cat scan-no">${esc(f)}</span></div>`).join('')}
    ${a?.summary ? `<div class="er-foot"><p>${esc(a.summary)}</p>
      ${a.quickWins?.length ? `<ul>${a.quickWins.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}</div>` : ''}`;
}

/* ---------- Expense upload ---------- */
function buildUpload() {
  const zone = $('#dropzone'), input = $('#expenseFile');
  if (!zone) return;

  $('#uploadMode').addEventListener('click', (e) => {
    const btn = e.target.closest('.mode');
    if (!btn) return;
    $('#uploadMode').querySelectorAll('.mode').forEach((m) => m.classList.remove('sel'));
    btn.classList.add('sel');
    state.uploadMode = btn.dataset.mode;
  });

  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => { if (input.files[0]) sendExpenseFile(input.files[0]); });

  ['dragenter', 'dragover'].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('over'); }));
  zone.addEventListener('drop', (e) => {
    const f = e.dataTransfer?.files?.[0];
    if (f) sendExpenseFile(f);
  });
}

function uploadStatus(kind, message) {
  const el = $('#uploadStatus');
  el.hidden = false;
  el.className = 'upload-status' + (kind === 'error' ? ' err' : kind === 'ok' ? ' ok' : '');
  el.innerHTML = kind === 'working'
    ? `<svg class="spinner" viewBox="0 0 56 56" width="22" height="22" aria-hidden="true">
         <circle class="sp-track" cx="28" cy="28" r="22" fill="none" stroke-width="5"/>
         <circle class="sp-arc" cx="28" cy="28" r="22" fill="none" stroke-width="5" stroke-linecap="round"/>
       </svg><span>${esc(message)}</span>`
    : esc(message);
}

async function sendExpenseFile(file) {
  if (file.size > 15 * 1024 * 1024) {
    uploadStatus('error', 'That file is larger than 15 MB. Try exporting just the marketing expense lines.');
    return;
  }
  $('#expenseResult').hidden = true;
  uploadStatus('working', state.uploadMode === 'ai'
    ? `Reading ${file.name} and sorting the line items…`
    : `Uploading ${file.name}…`);

  const form = new FormData();
  form.append('file', file);
  form.append('mode', state.uploadMode);
  form.append('context', JSON.stringify({
    clientName: $('#clientName').value.trim(),
    industry: $('#industry').value,
    annualRevenue: num('#annualRevenue'),
    digitalVendors: $('#digitalVendors').value.trim(),
    buyingModel: $('#buyingModel').value,
  }));

  try {
    const res = await fetch('api/expenses', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Upload failed');

    state.expenses = {
      filename: data.filename, url: data.url, storage: data.storage,
      mode: state.uploadMode, analysis: data.analysis || null,
    };

    if (data.analysis) {
      applyExpenseAnalysis(data.analysis);
      uploadStatus('ok', `Read ${data.filename}. The categories below have been filled in — review and adjust anything that looks off.`);
      renderExpenseResult(data.analysis);
    } else if (data.analysisError) {
      uploadStatus('error', data.analysisError);
    } else {
      uploadStatus('ok', `${data.filename} saved for review. Enter the figures below as best you can.`);
    }
  } catch (err) {
    console.error(err);
    uploadStatus('error', err.message || 'That file could not be processed. Enter the figures manually below.');
  }
}

/** Write the model's category figures into the spend inputs. */
function applyExpenseAnalysis(analysis) {
  SPEND_CATEGORIES.forEach((c) => { state.spend[c] = 0; });
  (analysis.categories || []).forEach((c) => {
    if (SPEND_CATEGORIES.includes(c.name)) state.spend[c.name] = c.monthlyAmount;
  });
  $('#spend').querySelectorAll('.sline').forEach((row) => {
    const v = state.spend[row.dataset.cat] || 0;
    const input = row.querySelector('.amt-input');
    const slider = row.querySelector('.slider');
    input.value = v > 0 ? v : '';
    slider.value = Math.min(v, Number(slider.max));
    row.classList.toggle('active', v > 0);
    paintSlider(slider);
  });
  $('#spendTotal').textContent = usd(totalSpend());
}

function renderExpenseResult(a) {
  const rows = (a.categories || []).map((c) => `
    <div class="er-row">
      <span class="cat">${esc(c.name)}<span class="conf ${c.confidence}">${c.confidence}</span>
        ${c.sourceLines ? `<span class="src">${esc(c.sourceLines)}</span>` : ''}</span>
      <span class="amt">${usd(c.monthlyAmount)}</span>
    </div>`).join('');

  $('#expenseResult').hidden = false;
  $('#expenseResult').innerHTML = `
    <div class="er-head">
      <b>What we found — ${usd(a.totalMonthly)} per month</b>
      <span>${esc(a.period)}${a.currency && a.currency !== 'USD' ? ` · amounts in ${esc(a.currency)}` : ''}</span>
    </div>
    ${rows || '<div class="er-row"><span class="cat">No marketing expenses could be identified</span></div>'}
    ${(a.notes || a.questions?.length) ? `
      <div class="er-foot">
        ${a.notes ? `<p>${esc(a.notes)}</p>` : ''}
        ${a.questions?.length ? `<ul>${a.questions.map((q) => `<li>${esc(q)}</li>`).join('')}</ul>` : ''}
        <p><b>These figures are a starting point.</b> Correct anything below before continuing — the audit uses what is in the fields, not what we read.</p>
      </div>` : ''}`;
}

/* ---------- Navigation ---------- */
const ORDER = ['intro','step1','profile','compete','step2','step3','step4','market','results'];
let current = 'intro';
function goto(id) {
  // Used to also capture a partial lead on leaving step1 -- removed with
  // sendPartial(); see the note above the LEAD_ID declaration: the Hub's
  // capture route refuses a lead with no contact info, which a client
  // snapshot alone never has.
  current = id;
  if (id !== 'intro' && id !== 'results') scheduleSave();
  document.querySelectorAll('.screen').forEach((s) => s.classList.toggle('active', s.id === id));
  const i = ORDER.indexOf(id);
  const prog = $('#progress');
  prog.hidden = (id === 'intro');
  $('#pfill').style.width = ((i / (ORDER.length - 1)) * 100) + '%';
  $('#psteps').querySelectorAll('span').forEach((s) => s.classList.toggle('on', Number(s.dataset.s) <= i));
  if (EMBED) {
    parent.postMessage({ type: 's1-audit-scroll' }, '*');
    postHeight(true);
  } else {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}
document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-goto]');
  if (!t) return;
  if (t.tagName === 'A') e.preventDefault();
  goto(t.dataset.goto);
});

/* ---------- Calculations ---------- */
function calculate() {
  const industry = $('#industry').value;
  const bm = INDUSTRIES[industry] || INDUSTRIES['Other / Not listed'];
  const annualRevenue = num('#annualRevenue');
  const spend = totalSpend();
  // Benchmark ranges are published for media spend. Staff payroll and events are
  // real costs (kept in ROI) but comparing them against media-only ranges would
  // make every client with an in-house team look like an overspender.
  const NON_MEDIA = ['In-house Marketing Staff', 'Live Events / Sponsorships'];
  const mediaSpend = SPEND_CATEGORIES.reduce(
    (t, c) => t + (NON_MEDIA.includes(c) ? 0 : (Number(state.spend[c]) || 0)), 0);
  const leads = num('#leads');
  const customers = num('#customers');
  const avgSale = num('#avgSale');
  const purchasesPerYear = num('#purchasesPerYear') || 1;
  const customerYears = num('#customerYears') || 1;
  const LIFTS = [15, 25];

  const cpl = leads > 0 && spend > 0 ? spend / leads : null;
  const cac = customers > 0 && spend > 0 ? spend / customers : null;
  const closeRate = leads > 0 ? (customers / leads) * 100 : null;
  const clv = avgSale > 0 ? avgSale * purchasesPerYear * customerYears : null;
  const revenue = customers > 0 && avgSale > 0 ? customers * avgSale : null;
  const roi = revenue != null && spend > 0 ? ((revenue - spend) / spend) * 100 : null;
  const spendPct = annualRevenue > 0 && mediaSpend > 0 ? ((mediaSpend * 12) / annualRevenue) * 100 : null;

  const scenarios = LIFTS.map((pct2) => {
    const addLeads = leads * (pct2 / 100);
    const addCustomers = closeRate != null ? addLeads * (closeRate / 100) : 0;
    const monthly = addCustomers * avgSale;
    return { liftPct: pct2, addLeads, addCustomers, monthly, annual: monthly * 12 };
  });
  const opportunityMonthly = scenarios[0].monthly;
  const opportunityAnnual = scenarios[0].annual;

  /* Benchmark verdicts */
  let spendVerdict = 'unknown', spendClass = 'na';
  if (spendPct != null) {
    if (spendPct < bm.budget[0]) { spendVerdict = 'below range'; spendClass = 'warn'; }
    else if (spendPct <= bm.budget[1]) { spendVerdict = 'within range'; spendClass = 'ok'; }
    else { spendVerdict = 'above range'; spendClass = 'bad'; }
  }
  let cplVerdict = 'no published range', cplClass = 'na';
  if (cpl != null && bm.cpl) {
    if (cpl < bm.cpl[0]) { cplVerdict = 'below range'; cplClass = 'ok'; }
    else if (cpl <= bm.cpl[1]) { cplVerdict = 'within range'; cplClass = 'ok'; }
    else { cplVerdict = 'above range'; cplClass = 'bad'; }
  }

  /* Marketing Efficiency Score™ — 100 points */
  const pts = leakPoints();
  let score = 45 * (1 - pts / FLAG_MAX);                // measurement & visibility
  if (spendVerdict === 'within range') score += 20;      // spend alignment
  else if (spendVerdict === 'below range') score += 14;
  else if (spendVerdict === 'above range') score += (spendPct <= bm.budget[1] * 1.25 ? 12 : 5);
  else score += 10;

  if (cplClass === 'ok') score += 15;                    // acquisition efficiency
  else if (cplVerdict === 'above range') score += (cpl <= bm.cpl[1] * 1.5 ? 7 : 2);
  else score += 10;

  if (roi == null) score += 3;                           // return
  else if (roi >= 300) score += 20;
  else if (roi >= 150) score += 15;
  else if (roi >= 50) score += 10;
  else if (roi >= 0) score += 5;

  score = Math.max(0, Math.min(100, Math.round(score)));

  const scoreTier =
    score >= 80 ? 'Strong performance' :
    score >= 65 ? 'Monitor' :
    score >= 50 ? 'Opportunity exists' :
    score >= 35 ? 'Significant opportunity' : 'Immediate review recommended';

  const scoreNote =
    score >= 80 ? 'Spending and measurement appear aligned. Continue quarterly reviews.' :
    score >= 65 ? 'Some inefficiencies may exist. Review reporting and channel performance.' :
    score >= 50 ? 'Several indicators suggest room to optimize spend and conversion.' :
    score >= 35 ? 'Hidden inefficiencies may be affecting profitability. A detailed review is advised.' :
    'Multiple indicators suggest marketing spend is not optimized. Review strongly encouraged.';

  const leakRatio = FLAG_MAX ? pts / FLAG_MAX : 0;
  const leakTier =
    leakRatio <= 0.17 ? 'Healthy' :
    leakRatio <= 0.34 ? 'Monitor' :
    leakRatio <= 0.50 ? 'Opportunity exists' :
    leakRatio <= 0.67 ? 'Significant opportunity' : 'Immediate review recommended';

  return {
    snapshot: {
      clientName: $('#clientName').value.trim(), industry,
      annualRevenue,
      locations: Number($('#locations').value) || 1,
      vendors: Number($('#vendors').value) || 0,
      preparedBy: $('#preparedBy').value.trim(),
      partnerFirm: $('#partnerFirm').value.trim(),
      website: $('#website').value.trim(),
      zipCode: $('#zipCode').value.trim(),
      cityMarket: $('#cityMarket').value.trim(),
    },
    spend: state.spend,
    profile: {
      digitalOver2500: state.profile.digitalOver2500 || 'not answered',
      traditionalOver2500: state.profile.traditionalOver2500 || 'not answered',
      buysLeadServices: state.profile.buysLeadServices || 'not answered',
      buyingModel: $('#buyingModel').value || 'not answered',
      providesTraining: state.profile.providesTraining || 'not asked',
      traditionalMedia: [...state.media],
      traditionalOther: $('#traditionalOther').value.trim(),
      digitalVendors: $('#digitalVendors').value.trim(),
      seasonalMarketing: state.profile.seasonalMarketing || 'not answered',
      seasonDetail: $('#seasonDetail').value.trim(),
      monthlyConsistency: state.profile.monthlyConsistency || 'not answered',
      liveEvents: state.profile.liveEvents || 'not answered',
      eventsDetail: $('#eventsDetail').value.trim(),
      eventsCost: num('#eventsCost'),
      marketingHeadcount: num('#marketingHeadcount'),
      marketingPayroll: num('#marketingPayroll'),
      assetOwnership: $('#assetOwnership').value,
      leadResponseTime: $('#leadResponseTime').value,
      crmTracking: $('#crmTracking').value,
    },
    competition: {
      competitors: readCompetitors(),
      suggestionBasis: state.competitorBasis || '',
      differentiation: $('#differentiation').value.trim(),
      losingTo: $('#losingTo').value.trim(),
    },
    website: state.website ? {
      url: state.website.url,
      finalUrl: state.website.scan.finalUrl,
      title: state.website.scan.title,
      conversionPoints: state.website.scan.conversionPoints,
      counts: state.website.scan.counts,
      trackers: state.website.scan.trackers,
      found: state.website.scan.found,
      missing: state.website.scan.missing,
      analysis: state.website.analysis,
    } : null,
    marketData: state.market,
    savings: null,
    market: {
      audienceType: $('#audienceType').value,
      serviceRadius: $('#serviceRadius').value,
      ageRanges: [...state.ages],
      incomeBand: $('#incomeBand').value,
      genderSkew: $('#genderSkew').value,
      homeownersOnly: state.profile.homeownersOnly === 'yes',
      audienceNotes: $('#audienceNotes').value.trim(),
      contextNotes: $('#contextNotes').value.trim(),
    },
    expenses: state.expenses ? {
      filename: state.expenses.filename,
      url: state.expenses.url,
      mode: state.expenses.mode,
      totalMonthly: state.expenses.analysis?.totalMonthly ?? null,
      period: state.expenses.analysis?.period ?? null,
    } : null,
    context2: {
      repeatShare: $('#repeatShare').value,
      capacity: $('#capacity').value,
      gRating: $('#gRating').value,
      gReviews: $('#gReviews').value,
      topServices: $('#topServices').value.trim(),
    },
    metrics: { spend, leads, customers, avgSale, purchasesPerYear, customerYears,
      cpl, cac, closeRate, clv, revenue, roi, spendPct, mediaSpend,
      liftPct: LIFTS[0], scenarios,
      opportunityMonthly, opportunityAnnual },
    benchmark: { budgetLo: bm.budget[0], budgetHi: bm.budget[1],
      budgetMid: (bm.budget[0] + bm.budget[1]) / 2,
      mixDigital: bm.mix[0], mixTraditional: bm.mix[1], industryNote: bm.note, industryFacts: bm.facts || [],
      cplLo: bm.cpl?.[0] ?? null, cplHi: bm.cpl?.[1] ?? null,
      spendVerdict, spendClass, cplVerdict, cplClass },
    flags: FLAGS.map((f) => ({
      label: f.label,
      points: f.points,
      response: state.flags[f.id] || 'not answered',
      answer: isFlagged(state.flags[f.id]),
    })),
    leakPoints: pts, leakMax: FLAG_MAX, leakTier, score, scoreTier, scoreNote,
  };
}

/* ---------- Rendering ---------- */
function rangeBar({ lo, hi, value, axisMax, fmt }) {
  if (value == null || lo == null) return '<p class="bverdict na">Not enough information to compare.</p>';
  const max = axisMax || Math.max(hi * 1.6, value * 1.15);
  const pct = (v) => Math.min(100, Math.max(0, (v / max) * 100));
  return `
    <div class="brange">
      <div class="bband" style="left:${pct(lo)}%;width:${pct(hi) - pct(lo)}%"></div>
      <div class="bmark" style="left:${pct(value)}%" title="This client"></div>
    </div>
    <div class="blabels"><span>0</span><span>benchmark ${fmt(lo)}–${fmt(hi)}</span><span>${fmt(max)}</span></div>`;
}

function renderReport(r) {
  const m = r.metrics, b = r.benchmark;
  $('#rTitle').textContent = r.snapshot.clientName ? `Findings for ${r.snapshot.clientName}` : 'Findings summary';
  $('#rMeta').textContent = [
    r.snapshot.preparedBy ? `Prepared by ${r.snapshot.preparedBy}${r.snapshot.partnerFirm ? ', ' + r.snapshot.partnerFirm : ''}` : null,
    r.snapshot.industry,
    m.spend ? usd(m.spend) + '/mo marketing investment' : null,
    r.snapshot.annualRevenue ? usd(r.snapshot.annualRevenue) + ' annual revenue' : null,
    'Prepared ' + new Date().toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }),
  ].filter(Boolean).join(' · ');

  /* gauge */
  $('#scoreTier').textContent = r.scoreTier;
  $('#scoreNote').textContent = r.scoreNote;
  const arc = 270;
  const fill = $('#gFill');
  fill.style.stroke = r.score >= 65 ? 'var(--green)' : r.score >= 50 ? 'var(--gold)' : 'var(--red)';
  let n = 0;
  clearInterval(gaugeTimer);
  gaugeTimer = setInterval(() => {
    n += Math.max(1, Math.round(r.score / 28));
    if (n >= r.score) { n = r.score; clearInterval(gaugeTimer); }
    $('#scoreNum').textContent = n;
  }, 26);
  requestAnimationFrame(() => { fill.style.strokeDashoffset = arc - (arc * r.score) / 100; });

  /* benchmarks */
  $('#benchSpend').innerHTML = rangeBar({
    lo: b.budgetLo, hi: b.budgetHi, value: m.spendPct,
    axisMax: Math.max(b.budgetHi * 1.8, (m.spendPct || 0) * 1.2),
    fmt: (v) => v.toFixed(0) + '%',
  }) + (m.spendPct != null
    ? `<p class="bverdict ${b.spendClass}">This client: ${m.spendPct.toFixed(1)}% of revenue — ${b.spendVerdict}</p>`
    : '');

  $('#benchCpl').innerHTML = (b.cplLo != null
    ? rangeBar({ lo: b.cplLo, hi: b.cplHi, value: m.cpl, fmt: (v) => usd(v) }) +
      (m.cpl != null ? `<p class="bverdict ${b.cplClass}">This client: ${usd(m.cpl)} per lead — ${b.cplVerdict}</p>` : '')
    : `<p class="bverdict na">No published cost-per-lead range for ${r.snapshot.industry}. Client cost per lead: ${m.cpl != null ? usd(m.cpl) : 'not calculable'}.</p>`);

  /* metrics */
  const cards = [
    { k: 'Monthly investment', v: usd(m.spend), n: usd(m.spend * 12) + ' annualized' },
    { k: 'Cost per lead', v: m.cpl != null ? usd(m.cpl) : '—', n: m.leads ? `${m.leads} leads / month` : 'Leads not provided', hi: b.cplClass === 'bad' },
    { k: 'Customer acquisition cost', v: m.cac != null ? usd(m.cac) : '—', n: m.customers ? `${m.customers} new customers / month` : 'Customers not provided' },
    { k: 'Close rate', v: m.closeRate != null ? m.closeRate.toFixed(1) + '%' : '—', n: 'Customers ÷ leads' },
    { k: 'Customer lifetime value', v: m.clv != null ? usd(m.clv) : '—', n: `${usd(m.avgSale)} × ${m.purchasesPerYear}/yr × ${m.customerYears} yrs` },
    { k: 'Marketing ROI', v: m.roi != null ? Math.round(m.roi) + '%' : '—', n: m.revenue != null ? usd(m.revenue) + ' monthly revenue' : 'Revenue not calculable', hi: m.roi != null && m.roi < 100 },
  ];
  $('#metrics').innerHTML = cards.map((c) =>
    `<div class="metric${c.hi ? ' hi' : ''}"><div class="k">${c.k}</div><div class="v">${c.v}</div><div class="n">${c.n}</div></div>`).join('');

  /* opportunity */
  const sc = m.scenarios || [];
  $('#oppPanel').innerHTML = `
    <h4>Growth opportunity</h4>
    <div class="opp-grid">
      ${sc.map((x) => `
        <div class="opp-card">
          <div class="opp-lift">+${x.liftPct}% lead volume</div>
          <div class="big">${usd(x.annual)}</div>
          <div class="opp-sub">${usd(x.monthly)} per month · about ${x.addCustomers.toFixed(1)} more customers</div>
        </div>`).join('')}
    </div>
    <p>Additional annual revenue at the current close rate of
    ${m.closeRate != null ? m.closeRate.toFixed(1) + '%' : 'unknown'} and average sale of ${usd(m.avgSale)},
    with no change to average sale value. Both figures assume the same close rate holds as volume rises.</p>`;

  renderIndustry(r);
  renderFacts(r);
  renderAudience(r);
  renderWebsite(r);
  renderCompetition(r);
  renderConsolidation(r);
  renderSavings(r);
  renderVendorQuestions(r);
  renderProfile(r);
  renderMarket(r);

  /* recap */
  const chip = (f) => {
    if (f.response === 'yes') return `<span class="chip">${esc(f.label)}</span>`;
    if (f.response === 'unsure') return `<span class="chip unsure">${esc(f.label)} — unsure</span>`;
    if (f.response === 'no') return `<span class="chip clear">${esc(f.label)}</span>`;
    return `<span class="chip none">${esc(f.label)} — not answered</span>`;
  };
  const order = { yes: 0, unsure: 1, 'not answered': 2, no: 3 };
  $('#flagRecap').innerHTML = [...r.flags]
    .sort((a, b) => (order[a.response] ?? 4) - (order[b.response] ?? 4))
    .map(chip).join('');
}


/* ---------- Report panels: industry, profile, market ---------- */
const YN = { yes: 'Yes', no: 'No', unsure: 'Not sure', consistent: 'Consistent', semi: 'Semi-consistent', sporadic: 'Starts and stops' };
const label = (v) => YN[v] || (v && v !== 'not answered' && v !== 'not asked' ? v : 'Not answered');

function renderIndustry(r) {
  const b = r.benchmark, m = r.metrics, s = r.snapshot;
  const avgSpend = s.annualRevenue ? (s.annualRevenue * b.budgetMid) / 100 / 12 : null;
  const gap = avgSpend != null && m.mediaSpend ? m.mediaSpend - avgSpend : null;

  $('#industryPanel').innerHTML = `
    <h4>What ${esc(s.industry)} businesses typically spend</h4>
    <p class="ind-note src-note">Benchmark ranges cover media and vendor spend. In-house staff and live events are counted in ROI but excluded here so the comparison is like for like.</p>
    <div class="kv">
      <div class="kv-item"><div class="k">Typical budget range</div><div class="v">${b.budgetLo}%–${b.budgetHi}% of revenue</div></div>
      <div class="kv-item"><div class="k">Industry midpoint</div><div class="v">${b.budgetMid.toFixed(1)}% of revenue</div></div>
      <div class="kv-item"><div class="k">Midpoint at this revenue</div><div class="v">${avgSpend != null ? usd(avgSpend) + ' / mo' : 'Revenue not provided'}</div></div>
      <div class="kv-item"><div class="k">This client (media spend)</div>
        <div class="v ${b.spendClass === 'ok' ? 'good' : b.spendClass === 'bad' ? 'flag' : ''}">${m.spendPct != null ? m.spendPct.toFixed(1) + '% of revenue' : 'Not calculable'}</div></div>
    </div>
    ${gap != null ? `<p class="ind-note"><b>${gap >= 0 ? 'Above' : 'Below'} the industry midpoint by ${usd(Math.abs(gap))} per month</b>
      (${usd(Math.abs(gap) * 12)} a year). ${gap >= 0
        ? 'Spending above the midpoint is not itself a problem — the question is what the extra is producing.'
        : 'Spending below the midpoint can mean efficiency or underinvestment. The measurement answers tell you which.'}</p>` : ''}
    <div class="ind-mix" title="Typical channel mix">
      <div style="width:${b.mixDigital}%;background:var(--sky)">${b.mixDigital}% digital</div>
      <div style="width:${b.mixTraditional}%;background:var(--navy)">${b.mixTraditional}% traditional</div>
    </div>
    <div class="ind-legend"><span>Typical channel mix for this industry</span></div>
    <p class="ind-note">${esc(b.industryNote)}</p>
    <p class="ind-note"><em>Ranges vary by market, competition, business maturity, and geography. Treat them as a guideline, not a rule.</em></p>`;
}

function renderProfile(r) {
  const p = r.profile;
  const media = p.traditionalMedia.length
    ? p.traditionalMedia.join(', ') + (p.traditionalOther ? `, ${p.traditionalOther}` : '')
    : (p.traditionalOther || 'None reported');

  $('#profilePanel').innerHTML = `
    <h4>How this client buys marketing</h4>
    <div class="kv">
      <div class="kv-item"><div class="k">Digital spend over $2,500/mo</div><div class="v">${label(p.digitalOver2500)}</div></div>
      <div class="kv-item"><div class="k">Traditional spend over $2,500/mo</div><div class="v">${label(p.traditionalOver2500)}</div></div>
      <div class="kv-item"><div class="k">Buys lead services</div>
        <div class="v ${p.buysLeadServices === 'yes' ? 'flag' : ''}">${label(p.buysLeadServices)}</div></div>
      <div class="kv-item"><div class="k">Buying model</div><div class="v">${esc(label(p.buyingModel))}</div></div>
      ${p.providesTraining !== 'not asked' ? `<div class="kv-item"><div class="k">Trains in-house staff</div>
        <div class="v ${p.providesTraining === 'no' ? 'flag' : p.providesTraining === 'yes' ? 'good' : ''}">${label(p.providesTraining)}</div></div>` : ''}
      <div class="kv-item"><div class="k">Seasonal pushes</div><div class="v">${label(p.seasonalMarketing)}${p.seasonDetail ? ` — ${esc(p.seasonDetail)}` : ''}</div></div>
      <div class="kv-item"><div class="k">Monthly consistency</div>
        <div class="v ${p.monthlyConsistency === 'sporadic' ? 'flag' : p.monthlyConsistency === 'consistent' ? 'good' : ''}">${label(p.monthlyConsistency)}</div></div>
    </div>
    <p class="ind-note"><b>Traditional media:</b> ${esc(media)}</p>
    ${p.digitalVendors ? `<p class="ind-note"><b>Digital vendors:</b> ${esc(p.digitalVendors)}</p>` : ''}
    ${r.expenses ? `<p class="ind-note"><b>Expense document:</b> ${esc(r.expenses.filename)} —
      ${r.expenses.mode === 'ai' ? `read automatically${r.expenses.period ? ` (${esc(r.expenses.period)})` : ''}` : 'saved for analyst review'}.</p>` : ''}`;
}

function renderMarket(r) {
  const m = r.market;
  const any = m.audienceType || m.serviceRadius || m.ageRanges.length || m.incomeBand || m.genderSkew || m.audienceNotes || m.contextNotes;
  if (!any) { $('#marketPanel').style.display = 'none'; return; }
  $('#marketPanel').style.display = '';
  $('#marketPanel').innerHTML = `
    <h4>Target market</h4>
    <div class="kv">
      ${m.audienceType ? `<div class="kv-item"><div class="k">Sells to</div><div class="v">${esc(m.audienceType)}</div></div>` : ''}
      ${m.serviceRadius ? `<div class="kv-item"><div class="k">Service area</div><div class="v">${esc(m.serviceRadius)}</div></div>` : ''}
      ${m.ageRanges.length ? `<div class="kv-item"><div class="k">Primary age range</div><div class="v">${m.ageRanges.join(', ')}</div></div>` : ''}
      ${m.incomeBand ? `<div class="kv-item"><div class="k">Household income</div><div class="v">${esc(m.incomeBand)}</div></div>` : ''}
      ${m.genderSkew ? `<div class="kv-item"><div class="k">Gender skew</div><div class="v">${esc(m.genderSkew)}</div></div>` : ''}
    </div>
    ${m.audienceNotes ? `<p class="ind-note"><b>Audience notes:</b> ${esc(m.audienceNotes)}</p>` : ''}
    ${m.contextNotes ? `<p class="ind-note"><b>Business context:</b> ${esc(m.contextNotes)}</p>` : ''}`;
}


/* ---------- Audience estimate (mirrors audience.js on the server) ---------- */
const AGE_SHARE = { '18–24':0.09, '25–34':0.14, '35–44':0.13, '45–54':0.12, '55–64':0.13, '65+':0.17 };
const INCOME_SHARE = { 'Under $50k':0.36, '$50k–$100k':0.28, '$100k–$200k':0.26, 'Over $200k':0.10, 'Mixed / not targeted':1.0 };
const GENDER_SHARE = { 'Mostly women':0.51, 'Mostly men':0.49, 'No meaningful skew':1.0 };

function estimateAudience(mk, population) {
  if (!population || population <= 0) return null;
  const steps = [{ label:'Service-area population', value: Math.round(population) }];
  const b2b = /B2B/i.test(mk.audienceType || ''), both = /both/i.test(mk.audienceType || '');
  let consumers = null, businesses = null;

  if (!b2b || both) {
    let people = population;
    const ages = (mk.ageRanges || []).filter((a) => AGE_SHARE[a]);
    if (ages.length && ages.length < 6) {
      const share = ages.reduce((t, a) => t + AGE_SHARE[a], 0);
      people *= share;
      steps.push({ label:`Aged ${ages.join(', ')}`, value: Math.round(people), note:`${Math.round(share*100)}% of population` });
    }
    const g = GENDER_SHARE[mk.genderSkew] ?? 1;
    if (g < 1) { people *= g; steps.push({ label: mk.genderSkew, value: Math.round(people), note:`${Math.round(g*100)}% of the above` }); }
    const inc = INCOME_SHARE[mk.incomeBand] ?? 1;
    if (inc < 1) { people *= inc; steps.push({ label:`Income ${mk.incomeBand}`, value: Math.round(people), note:`${Math.round(inc*100)}% of households` }); }
    if (mk.homeownersOnly) { people *= 0.65; steps.push({ label:'Homeowners only', value: Math.round(people), note:'65% ownership rate' }); }
    consumers = Math.round(people);
    steps.push({ label:'Estimated reachable people', value: consumers, emphasis:true });
    steps.push({ label:'Estimated reachable households', value: Math.round(consumers / 2.5) });
  }
  if (b2b || both) {
    businesses = Math.round((population / 1000) * 25);
    steps.push({ label:'Estimated businesses in the area', value: businesses, emphasis:true, note:'~25 establishments per 1,000 people' });
  }
  const primary = (b2b && !both) ? businesses : consumers;
  return { steps, primary, low: Math.round(primary*0.7), high: Math.round(primary*1.3) };
}

function renderAudience(r) {
  const mk2 = r.marketData || {};
  const est = estimateAudience(r.market, mk2.population);
  const host = $('#audiencePanel');
  host.style.display='';
  if (!est) { host.style.display='none'; return; }
  const n = (v) => v.toLocaleString('en-US');
  const cpl = r.metrics.cpl;
  host.innerHTML = `
    <h4>Estimated reachable audience${mk2.areaName ? ` — ${esc(mk2.areaName)}` : ''}</h4>
    ${mk2.basis ? `<p class="ind-note src-note">Service area sized from ${r.snapshot.zipCode ? `ZIP ${esc(r.snapshot.zipCode)}` : 'the market supplied'} and the service radius. ${esc(mk2.basis)} <span class="conf ${mk2.confidence}">${esc(mk2.confidence)} confidence</span></p>` : ''}
    <div class="funnel">
      ${est.steps.map((s2) => `
        <div class="fn-row${s2.emphasis ? ' em' : ''}">
          <span class="fn-lab">${esc(s2.label)}${s2.note ? `<i>${esc(s2.note)}</i>` : ''}</span>
          <span class="fn-val">${n(s2.value)}</span>
        </div>`).join('')}
    </div>
    <p class="ind-note"><b>Working range: ${n(est.low)} – ${n(est.high)}.</b>
    ${r.metrics.leads ? `At ${r.metrics.leads} leads a month, the client is currently reaching roughly
      ${((r.metrics.leads / est.primary) * 100).toFixed(2)}% of that audience each month.` : ''}
    ${cpl ? ` Reaching one percent of it would be about ${((est.primary * 0.01)).toLocaleString('en-US', {maximumFractionDigits:0})} people.` : ''}</p>
    ${mk2.demographicNote ? `<p class="ind-note"><b>About this area:</b> ${esc(mk2.demographicNote)}</p>` : ''}
    ${(mk2.medianHouseholdIncome || mk2.medianAge || mk2.homeownershipRate) ? `
      <div class="kv">
        ${mk2.medianHouseholdIncome ? `<div class="kv-item"><div class="k">Median household income</div><div class="v">${usd(mk2.medianHouseholdIncome)}</div></div>` : ''}
        ${mk2.medianAge ? `<div class="kv-item"><div class="k">Median age</div><div class="v">${mk2.medianAge}</div></div>` : ''}
        ${mk2.homeownershipRate ? `<div class="kv-item"><div class="k">Homeownership</div><div class="v">${Math.round(mk2.homeownershipRate * (mk2.homeownershipRate <= 1 ? 100 : 1))}%</div></div>` : ''}
      </div>` : ''}
    <p class="ind-note"><em>A directional estimate: an approximate service-area population filtered by national age, income,
    household, and business-density averages. Not local census data. Confirm against census or ad-platform reach figures before setting a budget.</em></p>`;
}

/* ---------- Industry benchmarks and facts ---------- */
function renderFacts(r) {
  const b = r.benchmark, s2 = r.snapshot;
  const facts = b.industryFacts || [];
  if (!facts.length) { $('#factsPanel').style.display='none'; return; }
  $('#factsPanel').style.display='';
  $('#factsPanel').innerHTML = `
    <h4>${esc(s2.industry)} benchmarks and facts</h4>
    <div class="kv">
      <div class="kv-item"><div class="k">Budget range</div><div class="v">${b.budgetLo}%–${b.budgetHi}% of revenue</div></div>
      <div class="kv-item"><div class="k">Cost per lead range</div><div class="v">${b.cplLo != null ? usd(b.cplLo)+'–'+usd(b.cplHi) : 'No published range'}</div></div>
      <div class="kv-item"><div class="k">Typical channel mix</div><div class="v">${b.mixDigital}% digital / ${b.mixTraditional}% traditional</div></div>
    </div>
    <ul class="facts">${facts.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>
    <p class="ind-note"><em>Directional industry patterns, not guarantees. Any individual business can sit well outside them for good reasons.</em></p>`;
}

/* ---------- Website conversion ---------- */
function renderWebsite(r) {
  const w = r.website, host = $('#websitePanel');
  if (!w) {
    if (!r.snapshot.website) { host.style.display='none'; return; }
    host.style.display='';
    host.innerHTML = `<h4>Website conversion review</h4>
      <p class="ind-note">${esc(r.snapshot.website)} was not scanned during this audit. Running the scan shows how many ways
      a visitor can currently become a lead, and whether any of it is being measured.</p>`;
    return;
  }
  host.style.display='';
  const a = w.analysis;
  host.innerHTML = `
    <h4>Website conversion review — ${esc(w.finalUrl)}</h4>
    <div class="kv">
      <div class="kv-item"><div class="k">Ways to convert</div>
        <div class="v ${w.conversionPoints < 2 ? 'flag' : 'good'}">${w.conversionPoints} found</div></div>
      <div class="kv-item"><div class="k">Forms</div><div class="v">${w.counts.forms}</div></div>
      <div class="kv-item"><div class="k">Click-to-call links</div>
        <div class="v ${w.counts.telLinks ? 'good' : 'flag'}">${w.counts.telLinks}</div></div>
      <div class="kv-item"><div class="k">Tracking tags</div>
        <div class="v ${w.trackers.length ? 'good' : 'flag'}">${w.trackers.length ? w.trackers.length + ' detected' : 'None detected'}</div></div>
    </div>
    ${a?.summary ? `<p class="ind-note">${esc(a.summary)}</p>` : ''}
    ${a?.measurementVerdict ? `<p class="ind-note"><b>Measurement:</b> ${esc(a.measurementVerdict)}</p>` : ''}
    ${a?.gaps?.length ? `<h4 class="mt">What to fix</h4>${a.gaps.map((g) => `
      <div class="finding ${g.impact === 'high' ? 'high' : g.impact === 'medium' ? 'medium' : 'low'}">
        <b>${esc(g.issue)}</b><span>${esc(g.fix)}</span></div>`).join('')}` : `
      <h4 class="mt">What the scan did not find</h4>
      <ul class="facts">${w.missing.map((m) => `<li>${esc(m)}</li>`).join('')}</ul>`}
    ${a?.quickWins?.length ? `<h4 class="mt">Quick wins</h4><ul class="facts">${a.quickWins.map((q) => `<li>${esc(q)}</li>`).join('')}</ul>` : ''}
    <p class="ind-note"><em>Based on the served home page only. Design, speed, and copy quality were not assessed.</em></p>`;
}

/* ---------- Competition ---------- */
function renderCompetition(r) {
  const c = r.competition, host = $('#competitionPanel');
  const any = c.competitors.length || c.differentiation || c.losingTo;
  if (!any) { host.style.display='none'; return; }
  host.style.display='';

  const b = r.benchmark, m = r.metrics;
  const listed = c.competitors.length;
  host.innerHTML = `
    <h4>Competitive position</h4>
    ${listed ? `<div class="comp-list">${c.competitors.map((x) => `
      <div class="comp-item"><b>${esc(x.name || x.website)}</b>${x.website ? `<span>${esc(x.website)}</span>` : '<span>No website supplied</span>'}</div>`).join('')}</div>`
      : `<p class="ind-note">No competitors were named. That is itself worth noting — a business that cannot name who it loses
         work to is usually bidding against them blind in search auctions.</p>`}
    ${c.differentiation ? `<p class="ind-note"><b>What sets them apart:</b> ${esc(c.differentiation)}</p>` : `
      <p class="ind-note"><b>No differentiation was stated.</b> Where a business cannot articulate why a customer should choose
      them, advertising tends to compete on price by default, which raises cost per lead.</p>`}
    ${c.losingTo ? `<p class="ind-note"><b>Losing work to:</b> ${esc(c.losingTo)}</p>` : ''}
    ${c.competitors.filter((x) => x.comparison?.comparison).map((x) => {
      const cc = x.comparison.comparison;
      return `<div class="cmp-block">
        <b>${esc(x.name)} — site comparison</b>
        ${cc.verdict ? `<p>${esc(cc.verdict)}</p>` : ''}
        ${(cc.competitorAdvantages || []).length ? `<span class="cmp-lab">What they do that this client does not</span>
          <ul class="facts">${cc.competitorAdvantages.map((a2) => `<li>${esc(a2)}</li>`).join('')}</ul>` : ''}
        ${(cc.clientAdvantages || []).length ? `<span class="cmp-lab">What this client does that they do not</span>
          <ul class="facts">${cc.clientAdvantages.map((a2) => `<li>${esc(a2)}</li>`).join('')}</ul>` : ''}
        ${cc.takeaway ? `<p class="cmp-take">${esc(cc.takeaway)}</p>` : ''}
      </div>`;
    }).join('')}
    <p class="ind-note">${listed
      ? `With ${listed} named competitor${listed === 1 ? '' : 's'}, the practical next step is to compare their visibility on the
         terms this client depends on. In ${esc(r.snapshot.industry)}, ${b.mixDigital}% of typical spend goes to digital, so
         competitors are most likely bidding on the same search terms${m.cpl ? ` that currently cost this client ${usd(m.cpl)} per lead` : ''}.
         Competitor sites are worth reviewing for the conversion points listed above — the offer, the guarantee, and how fast
         a visitor can reach a person.`
      : `Identifying three or four direct competitors and reviewing their sites and ad presence is a low-cost exercise that
         usually explains more about lead cost than any change to budget.`}</p>`;
}

/* ---------- Vendor consolidation ---------- */
function renderConsolidation(r) {
  const p = r.profile, m = r.metrics, host = $('#consolidationPanel');
  const vendorText = p.digitalVendors || '';
  const vendorCount = vendorText ? vendorText.split(/[,\n;]+/).map((v) => v.trim()).filter(Boolean).length : (r.snapshot.vendors || 0);
  const activeCats = Object.entries(state.spend).filter(([, v]) => v > 0).length;
  const tracked = r.flags.find((f) => /does not track conversions/i.test(f.label))?.answer;
  const knowsCpl = !r.flags.find((f) => /does not know cost per lead/i.test(f.label))?.answer;

  if (vendorCount < 2 && activeCats < 3) { host.style.display='none'; return; }
  host.style.display='';

  const est = m.spend ? m.spend * 0.1 : null;
  host.innerHTML = `
    <h4>Why consolidating digital vendors is worth considering</h4>
    <div class="kv">
      <div class="kv-item"><div class="k">Vendors named</div><div class="v ${vendorCount > 2 ? 'flag' : ''}">${vendorCount || 'Not listed'}</div></div>
      <div class="kv-item"><div class="k">Spend categories in use</div><div class="v">${activeCats}</div></div>
      <div class="kv-item"><div class="k">Conversion tracking</div>
        <div class="v ${tracked ? 'flag' : 'good'}">${tracked ? 'Not in place' : 'In place'}</div></div>
      <div class="kv-item"><div class="k">Cost per lead known</div>
        <div class="v ${knowsCpl ? 'good' : 'flag'}">${knowsCpl ? 'Yes' : 'No'}</div></div>
    </div>
    <p class="ind-note"><b>The problem is not the number of vendors. It is that no one sees the whole picture.</b>
    When ${vendorCount || 'several'} vendors each run part of the marketing, each one reports on its own slice and each slice
    looks acceptable in isolation. Nobody can answer the only question that matters: which dollar produced which customer.</p>
    <ul class="facts">
      <li><b>Attribution breaks at the seams.</b> A visitor who sees a social ad, searches the brand two days later, and calls
      from the map listing gets counted by whichever vendor claims the last click. Two vendors bill for the same customer, and
      the channel that actually created the demand looks like the weakest performer.</li>
      <li><b>Budget moves in the wrong direction.</b> Without a single view, spend shifts toward whichever vendor reports most
      confidently rather than whichever produces revenue. That is how the cheapest-looking channel quietly gets defunded.</li>
      <li><b>Duplicate spend goes unnoticed.</b> Overlapping retargeting, brand-term bidding against the client's own organic
      listing, and two vendors buying the same audience are only visible when someone can see every account at once.
      ${est ? `In a program at ${usd(m.spend)} a month, ten percent overlap is ${usd(est)} a month, or ${usd(est * 12)} a year.` : ''}</li>
      <li><b>Testing becomes impossible.</b> Meaningful optimization needs one variable changed at a time against one measure of
      success. Separate vendors optimizing separate metrics on separate schedules cancel each other's results out.</li>
      <li><b>Nobody owns the outcome.</b> When leads fall, each vendor can point to its own numbers and be right. Consolidation
      creates one accountable party for cost per acquired customer, not per click.</li>
    </ul>
    <p class="ind-note">Consolidation does not have to mean one vendor for everything. It means one place where all spend, leads,
    and closed sales are visible together, and one party accountable for that view.
    ${tracked ? 'That work has to start with conversion tracking — until that exists, consolidating vendors just centralizes the same blind spot.' : ''}</p>`;
}


/* ---------- Consolidation savings ---------- */
/**
 * Two savings levers, applied to the spend they actually affect:
 *  - consolidating digital vendors: 20% of digital spend
 *  - removing overlap between traditional and digital: 25% of traditional spend
 * Both are illustrative percentages, labeled as such wherever they appear.
 */
const DIGITAL_CATS = ['Google Advertising','Social Media Advertising','SEO','Streaming TV',
  'Display / Programmatic Ads','Retargeting','Website Expenses','Marketing Software',
  'Email Marketing','Agency Fees'];
const TRADITIONAL_CATS = ['Linear TV Advertising','Radio Advertising','Billboard Advertising',
  'Direct Mail','Live Events / Sponsorships'];

function savingsModel(r) {
  const spend = r.spend || {};
  const sum = (cats) => cats.reduce((t, c) => t + (Number(spend[c]) || 0), 0);
  const digital = sum(DIGITAL_CATS);
  const traditional = sum(TRADITIONAL_CATS);
  const total = r.metrics.spend || 0;

  const vendorText = r.profile.digitalVendors || '';
  const vendorCount = vendorText
    ? vendorText.split(/[,\n;]+/).map((v) => v.trim()).filter(Boolean).length
    : (r.snapshot.vendors || 0);

  const consolidate = vendorCount >= 2 ? digital * 0.20 : 0;
  const overlap = (traditional > 0 && digital > 0) ? traditional * 0.25 : 0;
  const monthly = consolidate + overlap;

  return { digital, traditional, total, vendorCount, consolidate, overlap, monthly, annual: monthly * 12 };
}

function renderSavings(r) {
  const host = $('#savingsPanel');
  const s2 = savingsModel(r);
  if (s2.monthly <= 0) { host.style.display = 'none'; return; }
  host.style.display = '';

  const m = r.metrics;
  // Both columns derive from the same spend figure, so the comparison stays consistent
  const oldSpend = m.spend || 0;
  const newSpend = Math.max(0, oldSpend - s2.monthly);
  const per = (total, divisor) => (divisor > 0 && total > 0 ? total / divisor : null);
  const roiOf = (cost) => (m.revenue != null && cost > 0 ? ((m.revenue - cost) / cost) * 100 : null);
  const oldCpl = per(oldSpend, m.leads), newCpl = per(newSpend, m.leads);
  const oldCac = per(oldSpend, m.customers), newCac = per(newSpend, m.customers);
  const oldRoi = roiOf(oldSpend), newRoi = roiOf(newSpend);

  host.innerHTML = `
    <h4>Possible savings from tightening the program</h4>
    <div class="save-hero">
      <div class="save-big">${usd(s2.annual)}<span>a year</span></div>
      <div class="save-sub">${usd(s2.monthly)} per month, or ${((s2.monthly / (s2.total || 1)) * 100).toFixed(1)}% of current spend</div>
    </div>

    <div class="save-rows">
      ${s2.consolidate > 0 ? `
        <div class="save-row">
          <div class="save-lab"><b>Consolidating digital vendors</b>
            <span>${s2.vendorCount} vendors across ${usd(s2.digital)} a month of digital spend. Where several vendors work
            from one plan and one measurement standard rather than their own, roughly <b>20%</b> of digital spend is
            typically recoverable — duplicate tools, overlapping audiences, brand terms bid against organic listings,
            and management fees paid twice on the same work.</span>
          </div>
          <div class="save-amt">${usd(s2.consolidate)}<span>/mo</span></div>
        </div>` : ''}
      ${s2.overlap > 0 ? `
        <div class="save-row">
          <div class="save-lab"><b>Removing traditional and digital overlap</b>
            <span>${usd(s2.traditional)} a month in traditional media running alongside ${usd(s2.digital)} in digital.
            Where the two are bought separately they usually reach the same people at the same time without either side
            knowing. Aligning the calendar and the audience typically frees about <b>25%</b> of traditional spend
            without reducing reach.</span>
          </div>
          <div class="save-amt">${usd(s2.overlap)}<span>/mo</span></div>
        </div>` : ''}
    </div>

    <h4 class="mt">What that would change</h4>
    <div class="save-compare">
      <div class="sc-head"><span></span><span>Today</span><span>After savings</span></div>
      <div class="sc-row"><span>Monthly spend</span><span>${usd(oldSpend)}</span><span class="good">${usd(newSpend)}</span></div>
      ${newCpl != null ? `<div class="sc-row"><span>Cost per lead</span><span>${usd(oldCpl)}</span><span class="good">${usd(newCpl)}</span></div>` : ''}
      ${newCac != null ? `<div class="sc-row"><span>Customer acquisition cost</span><span>${usd(oldCac)}</span><span class="good">${usd(newCac)}</span></div>` : ''}
      ${newRoi != null ? `<div class="sc-row"><span>Marketing ROI</span><span>${Math.round(oldRoi)}%</span><span class="good">${Math.round(newRoi)}%</span></div>` : ''}
    </div>

    <p class="ind-note">${usd(s2.annual)} a year is money that either stays as profit or buys more of what already works.
    ${m.cpl != null && newCpl != null ? `At the current close rate, redirecting it into working media rather than banking it would fund
    roughly ${Math.round(s2.monthly / newCpl)} more leads a month.` : ''}</p>
    <p class="ind-note"><em>The 20% and 25% figures are typical recovery rates for programs with these characteristics, not a
    quote or a guarantee. The actual figure depends on what the vendor invoices contain, which is what the expense review
    exists to establish.</em></p>`;
}


/* ---------- Questions for any marketing vendor ---------- */
const VENDOR_QUESTIONS = [
  ['Can you show me cost per acquired customer by channel?', 'Not cost per click or per lead — per customer who paid. A vendor who cannot produce this is reporting activity, not results.'],
  ['Who owns the ad accounts, the website, and the tracking?', 'The client should own all three. A vendor who owns them has leverage a supplier should not have.'],
  ['What is the notice period, and what transfers when we leave?', 'Month-to-month with full asset transfer is the professional standard. Long lock-ins usually protect the vendor, not the work.'],
  ['What did you change last month, and what happened?', 'Ongoing management should produce a running log of tests and results. No log usually means the account is on autopilot while fees continue.'],
  ['Where does my budget overlap with anything else we run?', 'A vendor who only sees their own slice cannot answer. That inability is the cost of fragmentation, stated out loud.'],
];

function renderVendorQuestions(r) {
  const host = $('#vendorQPanel');
  if (!host) return;
  host.innerHTML = `
    <h4>Five questions to ask any marketing vendor</h4>
    <p class="ind-note">These apply to the current vendors and to anyone the client might hire — us included. The answers separate vendors who manage outcomes from vendors who bill for activity.</p>
    ${VENDOR_QUESTIONS.map(([q, why], i) => `
      <div class="vq-row">
        <span class="vq-n">${i + 1}</span>
        <div><b>${esc(q)}</b><span>${esc(why)}</span></div>
      </div>`).join('')}`;
}

/* ---------- AI findings ---------- */
function esc(s = '') { return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }

const THINK_STEPS = [
  ['Reading the numbers', 'Working through spend, leads, and close rate.'],
  ['Comparing to industry benchmarks', 'Checking spend and cost per lead against the published ranges.'],
  ['Weighing the warning signs', 'Looking at what is measured and what is not.'],
  ['Factoring in how they buy', 'Vendors, agency or in house, seasonality, and consistency.'],
  ['Considering the target market', 'Audience, service area, and anything happening in the business.'],
  ['Writing the findings', 'Putting it in language a client can act on.'],
];

let thinkTimer = null;
function startThinking() {
  const el = $('#thinking');
  if (!el) return;
  el.style.display = '';
  let i = 0;
  const tick = () => {
    $('#thinkStep').textContent = THINK_STEPS[i][0];
    $('#thinkNote').textContent = THINK_STEPS[i][1];
    i = Math.min(i + 1, THINK_STEPS.length - 1);
  };
  tick();
  clearInterval(thinkTimer);
  thinkTimer = setInterval(tick, 2600);
}
function stopThinking() { clearInterval(thinkTimer); thinkTimer = null; }

async function loadAnalysis(r) {
  startThinking();
  const started = Date.now();
  try {
    const res = await fetch('api/analyze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(r),
    });
    const data = await res.json();
    const elapsed = Date.now() - started;
    if (elapsed < 2400) await new Promise((ok) => setTimeout(ok, 2400 - elapsed));
    stopThinking();
    state.analysis = data.analysis;
    renderAnalysis(data.analysis, data.source);
    buildPdf();
  } catch {
    stopThinking();
    $('#aiBadge').textContent = 'Unavailable';
    $('#aiBody').innerHTML = '<p>Written findings could not be generated. The scores and calculations above are complete and can be printed.</p>';
    buildPdf();
  }
}

function renderAnalysis(a, source) {
  $('#aiBadge').textContent = source === 'openai' ? 'AI-assisted analysis' : 'Rules-based analysis';
  if (!a) return;
  $('#aiBody').innerHTML = `
    <p class="headline">${esc(a.headline || '')}</p>
    <p>${esc(a.executiveSummary || '')}</p>
    ${a.findings?.length ? '<h5>What the numbers show</h5>' + a.findings.map((f) =>
      `<div class="finding ${['high','medium','low'].includes(f.severity) ? f.severity : 'low'}">
         <b>${esc(f.title)}</b><span>${esc(f.detail)}</span></div>`).join('') : ''}
    ${a.leaks?.length ? '<h5>Where money may be leaking</h5>' + a.leaks.map((l) =>
      `<div class="leak"><span><b>${esc(l.area)}</b><i>${esc(l.why)}</i></span>
         <span class="amt">${esc(l.estimatedMonthlyImpact)}</span></div>`).join('') : ''}
    ${a.questionsToAsk?.length ? '<h5>Questions to ask in your next client meeting</h5><ul>' +
      a.questionsToAsk.map((q) => `<li>${esc(q)}</li>`).join('') + '</ul>' : ''}
    ${a.nextSteps?.length ? '<h5>Recommended next steps</h5><ul>' +
      a.nextSteps.map((s) => `<li>${esc(s)}</li>`).join('') + '</ul>' : ''}
    ${a.partnerTalkingPoint ? `<div class="quote">“${esc(a.partnerTalkingPoint)}”</div>` : ''}`;
}



/* ---------- Save & resume ---------- */
/**
 * Everything typed is kept in this browser (localStorage) as it changes, so a
 * closed tab costs nothing. Cleared when the audit completes or on "Start over".
 * Nothing here leaves the machine until the partner submits.
 */
const SAVE_KEY = 'smart1-audit-v1';
const SAVE_FIELDS = ['clientName','industry','annualRevenue','website','zipCode','cityMarket',
  'preparedBy','partnerFirm','buyingModel','digitalVendors','traditionalOther','seasonDetail',
  'eventsDetail','eventsCost','marketingHeadcount','marketingPayroll','assetOwnership',
  'leadResponseTime','crmTracking','differentiation','losingTo','scanUrl','audienceType',
  'serviceRadius','incomeBand','genderSkew','audienceNotes','contextNotes','leads','customers',
  'avgSale','purchasesPerYear','customerYears','repeatShare','capacity','gRating','gReviews','topServices'];

let saveTimer = null;
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveProgress, 400);
}

function saveProgress() {
  try {
    const data = {
      v: 1, at: Date.now(), screen: current,
      fields: Object.fromEntries(SAVE_FIELDS.map((id) => [id, $(`#${id}`)?.value ?? ''])),
      sliders: { locations: $('#locations').value, vendors: $('#vendors').value },
      flags: state.flags,
      profile: state.profile,
      spend: state.spend,
      media: [...state.media],
      ages: [...state.ages],
      competitors: state.competitors,
    };
    localStorage.setItem(SAVE_KEY, JSON.stringify(data));
  } catch { /* storage full or blocked — resume just won't be offered */ }
}

function clearProgress() {
  try { localStorage.removeItem(SAVE_KEY); } catch { /* ignore */ }
}

function loadProgress() {
  try {
    const raw = localStorage.getItem(SAVE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (data.v !== 1) return null;
    // Older than 14 days: treat as a fresh start
    if (Date.now() - (data.at || 0) > 14 * 24 * 3600 * 1000) { clearProgress(); return null; }
    return data;
  } catch { return null; }
}

function applyProgress(data) {
  Object.entries(data.fields || {}).forEach(([id, v]) => { const el = $(`#${id}`); if (el) el.value = v; });
  if (data.sliders) {
    $('#locations').value = data.sliders.locations || 1;
    $('#vendors').value = data.sliders.vendors || 0;
    buildCountSliders();
  }
  state.flags = data.flags || {};
  state.profile = data.profile || {};
  state.spend = data.spend || {};
  state.media = new Set(data.media || []);
  state.ages = new Set(data.ages || []);
  state.competitors = data.competitors || [];

  // Reflect restored state into the widgets
  document.querySelectorAll('.toggle[data-flag]').forEach((g) => {
    const v = state.flags[g.dataset.flag];
    if (!v) return;
    const btn = g.querySelector(`button[data-v="${v}"]`);
    if (btn) { btn.classList.add('sel'); btn.setAttribute('aria-pressed', 'true'); }
  });
  document.querySelectorAll('.toggle[data-q]').forEach((g) => {
    const v = state.profile[g.dataset.q];
    if (!v) return;
    const btn = g.querySelector(`button[data-v="${v}"]`);
    if (btn) { btn.classList.add('sel'); btn.setAttribute('aria-pressed', 'true'); }
    if (g.dataset.q === 'seasonalMarketing') $('#seasonWrap').hidden = v !== 'yes';
    if (g.dataset.q === 'liveEvents') $('#eventsWrap').hidden = v !== 'yes';
  });
  const inHouse = /in house/i.test($('#buyingModel').value);
  $('#trainingWrap').hidden = !inHouse;
  ['#traditionalMedia', '#ageRanges'].forEach((sel, i) => {
    const store = i === 0 ? state.media : state.ages;
    document.querySelectorAll(`${sel} .chip-btn`).forEach((b) => {
      const on = store.has(b.dataset.val);
      b.classList.toggle('sel', on);
      b.setAttribute('aria-pressed', String(on));
    });
  });
  $('#spend').querySelectorAll('.sline').forEach((row) => {
    const v = state.spend[row.dataset.cat] || 0;
    row.querySelector('.amt-input').value = v > 0 ? v : '';
    const slider = row.querySelector('.slider');
    slider.value = Math.min(v, Number(slider.max));
    row.classList.toggle('active', v > 0);
    paintSlider(slider);
  });
  $('#spendTotal').textContent = usd(totalSpend());
  renderConfirmed();
  updateLeakTotal();
}

function offerResume() {
  const data = loadProgress();
  if (!data || !data.fields?.clientName) return;
  const bar = document.createElement('div');
  bar.className = 'resume-bar';
  bar.innerHTML = `
    <span>You have an audit in progress for <b>${esc(data.fields.clientName)}</b>.</span>
    <span class="resume-acts">
      <button type="button" class="btn btn-primary small" id="resumeYes">Pick up where you left off</button>
      <button type="button" class="btn btn-ghost dark small" id="resumeNo">Start over</button>
    </span>`;
  $('#intro .wrap').prepend(bar);
  $('#resumeYes').addEventListener('click', () => {
    applyProgress(data);
    bar.remove();
    goto(data.screen && data.screen !== 'results' ? data.screen : 'step1');
  });
  $('#resumeNo').addEventListener('click', () => { clearProgress(); bar.remove(); });
}


/* ---------- Pre-report completeness check ---------- */
/**
 * Before calculating, show what is blank and what it costs the report. This is
 * the difference between "why are there dashes" and a partner choosing to skip.
 */
function completenessGaps() {
  const gaps = [];
  const spend = totalSpend();
  if (!num('#annualRevenue')) gaps.push({ field: 'Annual revenue', cost: 'No industry benchmark comparison or budget-gap figure.' });
  if (spend <= 0) gaps.push({ field: 'Monthly investment', cost: 'No cost per lead, acquisition cost, ROI, or savings model. This is the most important section to fill.' });
  if (!num('#leads')) gaps.push({ field: 'Marketing-generated leads', cost: 'No cost per lead or close rate.' });
  if (!num('#customers')) gaps.push({ field: 'New customers per month', cost: 'No acquisition cost, ROI, or growth scenarios.' });
  if (!num('#avgSale')) gaps.push({ field: 'Average sale value', cost: 'No revenue, lifetime value, or growth figures.' });
  if (!$('#zipCode').value.trim() && !$('#cityMarket').value.trim()) gaps.push({ field: 'ZIP code or market', cost: 'Audience size falls back to a national average instead of this market.' });
  if (!$('#website').value.trim()) gaps.push({ field: 'Client website', cost: 'No conversion scan and no competitor site comparison.' });
  const answered = FLAGS.filter((f) => state.flags[f.id]).length;
  if (answered < FLAGS.length) gaps.push({ field: `Warning signs (${FLAGS.length - answered} unanswered)`, cost: 'Unanswered questions cannot count toward the efficiency score.' });
  return gaps;
}

function showSummary() {
  const gaps = completenessGaps();
  if (!gaps.length) return false; // nothing missing — go straight to the report
  const host = $('#gapList');
  host.innerHTML = gaps.map((g) => `
    <div class="gap-row">
      <b>${esc(g.field)}</b>
      <span>${esc(g.cost)}</span>
    </div>`).join('');
  $('#summaryModal').hidden = false;
  document.body.style.overflow = 'hidden';
  return true;
}

function hideSummary() {
  $('#summaryModal').hidden = true;
  document.body.style.overflow = '';
}

/* ---------- Compiling overlay ---------- */
const COMPILE_STEPS = [
  ['Reading the inputs', 'Spend, leads, customers, and average sale.'],
  ['Sizing the service area', 'Working out how many people this business can reach.'],
  ['Comparing to industry benchmarks', 'Spend and cost per lead against the published ranges.'],
  ['Checking the website', 'Conversion points and whether any of it is measured.'],
  ['Reviewing the competition', 'How the confirmed competitors present themselves.'],
  ['Modeling savings and growth', 'Where the money could go further.'],
  ['Writing the findings', 'Putting it in language a client can act on.'],
];

let compileTimer = null;
function startCompiling() {
  const el = $('#compiling');
  el.hidden = false;
  document.body.style.overflow = 'hidden';
  let i = 0;
  const tick = () => {
    $('#compStep').textContent = COMPILE_STEPS[i][0];
    $('#compNote').textContent = COMPILE_STEPS[i][1];
    $('#compFill').style.width = `${((i + 1) / COMPILE_STEPS.length) * 100}%`;
    i = Math.min(i + 1, COMPILE_STEPS.length - 1);
  };
  tick();
  clearInterval(compileTimer);
  compileTimer = setInterval(tick, 1400);
}
function stopCompiling() {
  clearInterval(compileTimer);
  compileTimer = null;
  $('#compFill').style.width = '100%';
  setTimeout(() => {
    $('#compiling').hidden = true;
    document.body.style.overflow = '';
  }, 320);
}

/* ---------- PDF report ---------- */
function setDownload(state_, url) {
  [$('#dlBtn'), $('#dlBtn2')].forEach((btn) => {
    if (!btn) return;
    if (state_ === 'ready') {
      btn.setAttribute('href', url);
      btn.removeAttribute('aria-disabled');
      // A cross-origin file (Cloudinary) ignores the download attribute, so open it in a tab instead
      if (url.startsWith('http') && !url.startsWith(location.origin)) {
        btn.setAttribute('target', '_blank');
        btn.setAttribute('rel', 'noopener');
        btn.removeAttribute('download');
      } else {
        btn.setAttribute('download', '');
      }
      btn.textContent = btn.id === 'dlBtn' ? 'Download PDF report' : 'Download the PDF report';
    } else if (state_ === 'error') {
      btn.setAttribute('aria-disabled', 'true');
      btn.textContent = 'PDF unavailable';
    } else {
      btn.setAttribute('aria-disabled', 'true');
      btn.textContent = 'Preparing PDF…';
    }
  });
}

async function buildPdf() {
  if (!state.results) return;
  setDownload('pending');
  try {
    const res = await fetch('api/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...state.results, analysis: state.analysis || null, lead: state.lead || null, lead_id: LEAD_ID }),
    });
    const data = await res.json();
    if (!data.url) throw new Error('no url');
    state.pdfUrl = data.url;
    setDownload('ready', data.url);
  } catch (err) {
    console.error('PDF generation failed', err);
    setDownload('error');
  }
}

/* ---------- Gate ---------- */
async function fetchMarket() {
  try {
    const res = await fetch('api/market', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ context: {
        zipCode: $('#zipCode').value.trim(),
        cityMarket: $('#cityMarket').value.trim(),
        industry: $('#industry').value,
        serviceRadius: $('#serviceRadius').value,
        locations: Number($('#locations').value) || 1,
        audienceType: $('#audienceType').value,
      } }),
    });
    const data = await res.json();
    state.market = data.market || null;
  } catch { state.market = null; }
}

async function runReport() {
  startCompiling();
  const began = Date.now();

  if (!state.market) await fetchMarket();
  state.results = calculate();
  state.results.savings = savingsModel(state.results);

  // Let the animation play long enough to read, without stalling if it was slow
  const elapsed = Date.now() - began;
  if (elapsed < 3200) await new Promise((ok) => setTimeout(ok, 3200 - elapsed));

  renderReport(state.results);
  stopCompiling();
  goto('results');
  clearProgress(); // audit complete: nothing to resume

  if (state.unlocked) {
    $('#aiBadge').textContent = 'Generating…';
    $('#aiBody').innerHTML = '<div class="skel"></div><div class="skel"></div><div class="skel short"></div>';
    loadAnalysis(state.results);
  }
}

$('#toGate').addEventListener('click', () => {
  if (!showSummary()) runReport();
});
$('#gapBack')?.addEventListener('click', () => {
  hideSummary();
});
$('#gapGo')?.addEventListener('click', () => {
  hideSummary();
  runReport();
});

$('#unlock').addEventListener('click', async () => {
  const name = $('#gName').value.trim(), firm = $('#gFirm').value.trim(), email = $('#gEmail').value.trim();
  if (!name || !firm || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    $('#gateErr').textContent = 'Enter your name, firm, and a valid email address to continue.';
    return;
  }
  $('#gateErr').textContent = '';
  $('#unlock').disabled = true;
  $('#unlock').textContent = 'Preparing findings…';

  state.lead = { name, firm, email, phone: $('#gPhone').value.trim() };
  fetch('api/lead', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...state.lead,
      ...ATTR,
      lead_id: LEAD_ID,
      partnerName: state.results.snapshot.preparedBy || state.lead.name,
      partnerFirm: state.results.snapshot.partnerFirm || state.lead.firm,
      client: state.results.snapshot,
      score: state.results.score,
      scoreTier: state.results.scoreTier,
      leakPoints: state.results.leakPoints,
      leakTier: state.results.leakTier,
      monthlySpend: state.results.metrics.spend,
      lastScreen: current,
    }),
  }).catch(() => {});

  $('#gate').style.display = 'none';
  $('#report').classList.remove('locked');
  state.unlocked = true;
  renderReport(state.results);
  loadAnalysis(state.results);
});

$('#printBtn').addEventListener('click', () => window.print());
$('#restart').addEventListener('click', () => location.reload());

/* ---------- Init ---------- */
buildUI();
document.addEventListener('input', scheduleSave);
document.addEventListener('change', scheduleSave);
document.addEventListener('click', (e) => {
  if (e.target.closest('.toggle,.chip-btn,.sug-acts,.comp-del,#ageAll')) scheduleSave();
});
offerResume();

// The booking link is server-configured, so the button is wired at runtime
fetch('api/config')
  .then((r) => r.json())
  .then((cfg) => { if (cfg.bookingUrl) $('#bookBtn').href = cfg.bookingUrl; })
  .catch(() => { $('#bookBtn').href = 'https://smart1marketing.com/contact'; });

// Partner-specific links: /?partner=Jane%20Doe&firm=Doe%20CPA%20Group
const qs = new URLSearchParams(location.search);
if (qs.get('partner')) $('#preparedBy').value = qs.get('partner');
if (qs.get('firm')) $('#partnerFirm').value = qs.get('firm');

// Carry the partner's details into the gate so they aren't typed twice
$('#toGate').addEventListener('click', () => {
  if (!$('#gName').value) $('#gName').value = $('#preparedBy').value;
  if (!$('#gFirm').value) $('#gFirm').value = $('#partnerFirm').value;
});

goto('intro');
postHeight(true);
