/* ==========================================================================
   Smart 1 Seasonal Tourism Marketing Planner — Shared Report Template

   Pure functions that turn wizard answers (`a`) into the report's HTML
   markup. Loaded by both the embedded wizard app (public/app.js, for the
   on-screen report step) and the server-driven PDF render (public/report-
   print.html, captured headlessly by lib/pdf.js), so there is exactly one
   copy of the report markup and it can never drift between what the
   customer sees on screen and what ships in the PDF/CRM.

   Depends on globals defined in data.js (getCategoryProfile, REGIONS,
   HOLIDAYS, TIERS is defined in engine.js) and engine.js (recommendAudiences,
   recommendProducts, recommendTier, buildMonthlyPlan). Load order matters:
   data.js, then engine.js, then this file.
   ========================================================================== */

/* ---- generic formatting helpers (also used by the wizard's other steps,
   which is why app.js no longer defines its own copies) ------------------- */

function escapeAttr(s) { return String(s).replace(/"/g, '&quot;'); }
function escapeHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function money(n) { return '$' + Math.round(n).toLocaleString('en-US'); }

/* ---- answer-derived lookups (accept `a` explicitly — no implicit global
   wizard state — so these work identically in the browser and inside the
   headless report-print.html page) ---------------------------------------- */

function computeDefaultSeasons(a) {
  const region = REGIONS[a.region] || REGIONS.SOUTHEAST;
  return { primary: [...region.primary], shoulder: [...region.shoulder], slow: [...region.slow] };
}

function getSeasons(a) {
  if (a.customSeasons) return a.customSeasons;
  if (!a._defaultSeasons) a._defaultSeasons = computeDefaultSeasons(a);
  return a._defaultSeasons;
}

function getDayparts(a) {
  if (a.customDayparts) return a.customDayparts;
  const profile = getCategoryProfile(a.category === '__other__' ? null : a.category);
  return profile.dayparts;
}

function getHolidayRecs(a) {
  const profile = getCategoryProfile(a.category === '__other__' ? null : a.category);
  return recommendHolidays(a.region || 'SOUTHEAST', profile.familyKey);
}

function getAudiences(a) {
  if (a.customAudiences) return a.customAudiences;
  const profile = getCategoryProfile(a.category === '__other__' ? null : a.category);
  const region = REGIONS[a.region] || REGIONS.SOUTHEAST;
  return recommendAudiences(profile.class, profile.familyKey, region.flags);
}

function getProductPlan(a) {
  const profile = getCategoryProfile(a.category === '__other__' ? null : a.category);
  const tier = a.tier || recommendTier(a.avgValue || 0, profile.familyKey);
  return recommendProducts(profile.class, profile.familyKey, a.avgValue || 0, a.planningWindow, tier);
}

function reportDateString() {
  const d = new Date();
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
}

function weatherTriggerLabel(a) {
  if (a.weatherResponse === 'consistent') return 'Consistent delivery in all weather';
  if (a.weatherResponse === 'choose' && a.customWeatherPacing && a.customWeatherPacing.length) return a.customWeatherPacing.join('; ');
  return 'Reduced ~40% in unfavorable conditions; budget shifted to the next opportunity';
}

/* Where do this business's ads have to send people? Used by the report to
   recommend a landing page when there's no real website to land on —
   'website' | 'social_only' (Facebook/Instagram page) | 'none' | 'unknown'. */
function classifyWebPresence(a) {
  if (a.noWebsite) return 'none';
  const s = String(a.website || '').trim().toLowerCase();
  if (!s) return 'unknown';
  if (/facebook\.com|fb\.com|fb\.me|instagram\.com/.test(s)) return 'social_only';
  return 'website';
}

/* ---- the report itself --------------------------------------------------- */

function buildReportMarkup(a) {
  const profile = getCategoryProfile(a.category === '__other__' ? null : a.category);
  const seasons = getSeasons(a);
  const dayparts = getDayparts(a);
  const holidayLabels = (a.customHolidays || []).map(k => (HOLIDAYS.find(h => h.key === k) || {}).label).filter(Boolean);
  const audiences = getAudiences(a);
  const products = getProductPlan(a);
  const region = REGIONS[a.region] || REGIONS.SOUTHEAST;
  const r = a.budgetCalc;
  const dist = a.seasonalDist;
  const annualTotal = r.monthlyBudget * 12;
  const categoryLabel = a.category === '__other__' ? a.categoryOther : a.category;
  const marketLabel = a.city ? `${a.city}, ${a.state}` : (a.state || '—');
  const weatherLabel = weatherTriggerLabel(a);
  const audienceCount = (audiences.before ? audiences.before.length : 0) + (audiences.inMarket ? audiences.inMarket.length : 0);
  const monthlyPlan = buildMonthlyPlan(seasons, dist, profile.familyKey, weatherLabel);
  const pillGroup = (items) => items.map(x => `<span class="rpt-pill">${escapeHtml(x)}</span>`).join('');
  const webPresence = classifyWebPresence(a);
  // Efficiency stat: how much of the annual budget this plan moves out of
  // low-demand months and into peak windows, versus spending the same flat
  // amount every month. Pure arithmetic on the already-computed seasonal
  // distribution — a concrete "this is the money a flat schedule wastes"
  // number, not an invented benchmark.
  const reallocPct = annualTotal > 0
    ? Math.round(['primary','shoulder','slow'].reduce((s, k) =>
        s + Math.max(0, (dist[k].effectiveMonthly - r.monthlyBudget)) * dist[k].monthCount, 0) / annualTotal * 100)
    : 0;
  const annualRevenue = r.revenue * 12;

  return `
    <div class="rpt-doc">
      <div class="rpt-header">
        <div class="rpt-logo"><span class="rpt-logo-mark"></span>SMART<span class="rpt-logo-1">1</span>MARKETING</div>
        <h1 class="rpt-doc-title">Seasonal Tourism Marketing Plan</h1>
        <p class="rpt-doc-subtitle">Suggested seasonal media plan — directional assessment</p>
      </div>
      <div class="rpt-accent-bar"></div>
      <div class="rpt-body">
        <h2 class="rpt-business-name">${escapeHtml(a.businessName || categoryLabel || 'Your business')}</h2>
        <p class="rpt-prepared">Prepared by Smart 1 Marketing · ${reportDateString()}${a.website ? ' · ' + escapeHtml(a.website) : ''}</p>

        <div class="rpt-meta-box">
          <div class="rpt-meta-item"><div class="rpt-meta-label">Market</div><div class="rpt-meta-value">${escapeHtml(marketLabel)}</div></div>
          <div class="rpt-meta-item"><div class="rpt-meta-label">Travel distance</div><div class="rpt-meta-value">${escapeHtml(a.travelDistance || '—')}</div></div>
          <div class="rpt-meta-item"><div class="rpt-meta-label">Category</div><div class="rpt-meta-value">${escapeHtml(categoryLabel || '—')}</div></div>
          <div class="rpt-meta-item"><div class="rpt-meta-label">Primary season</div><div class="rpt-meta-value">${monthsToRangeString(seasons.primary)}</div></div>
          <div class="rpt-meta-item"><div class="rpt-meta-label">Suggested plan</div><div class="rpt-meta-value">${a.tier}</div></div>
          <div class="rpt-meta-item"><div class="rpt-meta-label">Annual plan total</div><div class="rpt-meta-value">${money(annualTotal)}</div></div>
          ${a.contact && a.contact.name ? `<div class="rpt-meta-item"><div class="rpt-meta-label">Contact</div><div class="rpt-meta-value">${escapeHtml(a.contact.name)}</div></div>` : ''}
          ${a.contact && a.contact.email ? `<div class="rpt-meta-item"><div class="rpt-meta-label">Email</div><div class="rpt-meta-value">${escapeHtml(a.contact.email)}</div></div>` : ''}
        </div>

        <div class="rpt-section">
          <div class="rpt-section-label">Your seasonal opportunity at a glance</div>
          <div class="rpt-badge">${region.label}</div>
          <div class="rpt-stat-row">
            <div class="rpt-stat-card"><div class="rsc-label">Primary season length</div><div class="rsc-value">${seasons.primary.length} mo</div><div class="rsc-sub">${monthsToRangeString(seasons.primary)}</div></div>
            <div class="rpt-stat-card"><div class="rsc-label">Recommended audiences</div><div class="rsc-value">${audienceCount}</div><div class="rsc-sub">before-arrival &amp; in-market segments</div></div>
            <div class="rpt-stat-card"><div class="rsc-label">Holiday opportunities</div><div class="rsc-value">${holidayLabels.length}</div><div class="rsc-sub">seasonal travel windows identified</div></div>
          </div>
          <p class="rpt-lede">Based on the ${a.zip ? 'ZIP code ' + a.zip : 'location'} provided, your business category, and ${region.label.toLowerCase()} tourism patterns, demand for ${escapeHtml((categoryLabel||'your business').toLowerCase())} is strongest ${monthsToRangeString(seasons.primary).toLowerCase()}, with a shoulder build-up ${monthsToRangeString(seasons.shoulder).toLowerCase()}. This plan paces your media spend to match that pattern instead of spreading it evenly across the year.</p>
        </div>

        ${Array.isArray(a.aiNarrative) && a.aiNarrative.length ? `
        <div class="rpt-section">
          <div class="rpt-section-label">Strategic assessment</div>
          ${a.aiNarrative.map(p => `<p class="rpt-lede" style="margin-bottom:10px;">${escapeHtml(p)}</p>`).join('')}
          <p class="rpt-disclaimer" style="margin:8px 0 0;">AI-assisted commentary generated from the figures above — provided for context, not a substitute for them.</p>
        </div>` : ''}

        <div class="rpt-section">
          <div class="rpt-section-label">What a seasonal, weather-smart plan delivers</div>
          <div class="rpt-highlight-box">
            <div class="rpt-highlight-title">Estimated monthly performance at your ${a.tier} plan</div>
            <div class="rpt-highlight-row">
              <div class="rpt-highlight-stat"><div class="rhs-label">Est. monthly revenue</div><div class="rhs-value">${money(r.revenue)}</div><div class="rhs-sub">at ${money(a.avgValue)} avg. value</div></div>
              <div class="rpt-highlight-stat"><div class="rhs-label">Revenue-to-spend ratio</div><div class="rhs-value">${r.roas}x</div><div class="rhs-sub">estimated return</div></div>
              <div class="rpt-highlight-stat"><div class="rhs-label">Est. customers / month</div><div class="rhs-value">${r.customers.toLocaleString()}</div><div class="rhs-sub">at ${money(r.cac)} est. acquisition cost</div></div>
            </div>
            <p class="rpt-highlight-note">Over 12 months, that's an estimated ${money(annualRevenue)} in revenue against a ${money(annualTotal)} media investment — roughly ${r.roas}x back for every dollar spent. Break-even is roughly ${r.breakeven.toLocaleString()} customer${r.breakeven === 1 ? '' : 's'} per month at this budget level. Figures are industry planning assumptions, not guarantees.</p>
          </div>
          ${reallocPct > 0 ? `<p class="rpt-lede" style="margin-top:14px;"><b>Where the efficiency comes from:</b> instead of spending the same amount every month, this plan moves ${reallocPct}% of your annual budget out of low-demand months and into your peak travel windows — plus weather-smart pacing that pulls delivery back when conditions suppress demand. That's spend a flat, year-round schedule would have used on weeks when travelers simply aren't there.</p>` : ''}
        </div>

        ${webPresence === 'none' || webPresence === 'social_only' ? `
        <div class="rpt-section">
          <div class="rpt-section-label">One thing your plan needs: a place to send visitors</div>
          <p class="rpt-lede">${webPresence === 'none'
            ? `Every ad in this plan needs somewhere to send interested travelers — a page with your offer, photos, hours, and an easy way to book or call. Since you don't have a website yet, we recommend starting with a simple, mobile-friendly landing page. It doesn't need to be a full website: one great page is enough to launch, and it also lets us measure exactly which ads turn into calls and bookings, so your budget keeps getting more efficient over time. Smart 1 can build and host this page for you as part of your plan.`
            : `A Facebook page is a great start — but ads perform measurably better when they send travelers to a fast, dedicated landing page with your offer, photos, and an easy way to book or call. It also lets us track exactly which ads turn into calls and bookings (something a Facebook page can't do), so your budget keeps getting more efficient over time. Smart 1 can build and host a simple landing page for you as part of your plan — your Facebook page keeps doing what it does best alongside it.`}</p>
        </div>` : ''}

        <div class="rpt-section">
          <div class="rpt-section-label">A focused plan vs. advertising without one</div>
          <div class="rpt-compare-grid">
            <div class="rpt-compare-card bad">
              <div class="rcc-title">Advertising without a seasonal plan</div>
              <div class="rcc-sub">Common outcome</div>
              <ul>
                <li>Budget spent evenly across months regardless of demand</li>
                <li>Full delivery continues during unfavorable weather</li>
                <li>Holiday travel windows and dayparts aren't prioritized</li>
                <li>Same audiences and creative used year-round</li>
              </ul>
            </div>
            <div class="rpt-compare-card good">
              <div class="rcc-title">A weather-smart seasonal plan</div>
              <div class="rcc-sub">This plan</div>
              <ul>
                <li>Budget weighted toward ${monthsToRangeString(seasons.primary).toLowerCase()}</li>
                <li>${weatherLabel}</li>
                <li>${holidayLabels.length ? 'Prioritizes ' + holidayLabels.slice(0,3).join(', ') : 'Dayparts prioritized: ' + dayparts.join(', ')}</li>
                <li>Before-arrival and in-market audiences targeted separately</li>
              </ul>
            </div>
          </div>
        </div>

        <div class="rpt-section">
          <div class="rpt-section-label">Recommended media, dayparts &amp; holidays</div>
          <p class="rpt-lede" style="margin-bottom:8px; font-weight:700; font-size:12px; color:var(--rpt-label); text-transform:uppercase; letter-spacing:.04em;">Media channels</p>
          <div class="rpt-pill-row" style="margin-bottom:16px;">${pillGroup(products)}</div>
          <p class="rpt-lede" style="margin-bottom:8px; font-weight:700; font-size:12px; color:var(--rpt-label); text-transform:uppercase; letter-spacing:.04em;">Strongest dayparts</p>
          <div class="rpt-pill-row" style="margin-bottom:16px;">${pillGroup(dayparts)}</div>
          <p class="rpt-lede" style="margin-bottom:8px; font-weight:700; font-size:12px; color:var(--rpt-label); text-transform:uppercase; letter-spacing:.04em;">Holiday travel windows</p>
          <div class="rpt-pill-row">${pillGroup(holidayLabels.length ? holidayLabels : ['Not prioritized'])}</div>
        </div>

        <div class="rpt-section">
          <div class="rpt-section-label">Good · Better · Best</div>
          <div class="rpt-tier-grid">${['Good','Better','Best'].map(t => {
            const range = TIERS[t];
            const isSelected = a.tier === t;
            return `<div class="rpt-tier-card ${isSelected ? 'rec' : ''}">
              ${isSelected ? '<div class="rtc-badge">Suggested for you</div>' : ''}
              <div class="rtc-kicker">${t} plan</div>
              <div class="rtc-name">${t}</div>
              <div class="rtc-price">${money(range.monthlyMin)}–${money(range.monthlyMax)}<span> /mo</span></div>
              <div class="rtc-tagline">${TIER_COPY[t].tagline}</div>
              <ul>${TIER_COPY[t].items.slice(0,5).map(i => `<li>${i}</li>`).join('')}</ul>
            </div>`;
          }).join('')}</div>
        </div>

        <div class="rpt-section">
          <div class="rpt-section-label">Suggested media budget</div>
          <div class="rpt-stat-row">
            <div class="rpt-stat-card"><div class="rsc-label">At peak (primary season)</div><div class="rsc-value">${money(dist.primary.effectiveMonthly)}</div><div class="rsc-sub">per month</div></div>
            <div class="rpt-stat-card"><div class="rsc-label">Blended monthly average</div><div class="rsc-value">${money(r.monthlyBudget)}</div><div class="rsc-sub">per month</div></div>
            <div class="rpt-stat-card"><div class="rsc-label">Annual plan total</div><div class="rsc-value">${money(annualTotal)}</div><div class="rsc-sub">across 12 months</div></div>
          </div>
        </div>

        <div class="rpt-section">
          <div class="rpt-section-label">Seasonal budget split</div>
          ${[
            { key: 'primary', label: 'Primary season', months: seasons.primary },
            { key: 'shoulder', label: 'Shoulder season', months: seasons.shoulder },
            { key: 'slow', label: 'Slow season', months: seasons.slow },
          ].map(row => `
            <div class="rpt-bar-row">
              <div class="rpt-bar-head">
                <div class="rpt-bar-label">${row.label}<span class="rpt-bar-months">${monthsToRangeString(row.months)}</span></div>
                <div class="rpt-bar-value">${dist[row.key].pct}% · ${money(dist[row.key].total)}</div>
              </div>
              <div class="rpt-bar-track"><div class="rpt-bar-fill ${row.key}" style="width:${dist[row.key].pct}%"></div></div>
              <div class="rpt-bar-sub">≈ ${money(dist[row.key].effectiveMonthly)}/mo across ${dist[row.key].monthCount} month${dist[row.key].monthCount > 1 ? 's' : ''}</div>
            </div>
          `).join('')}
        </div>

        <div class="rpt-section">
          <div class="rpt-section-label">Month-by-month campaign plan &amp; budget</div>
          <table class="rpt-table">
            <thead><tr><th>Month</th><th>Focus</th><th>Budget</th></tr></thead>
            <tbody>${monthlyPlan.map(m => `
              <tr>
                <td class="rt-month">${m.month}<br><span class="rpt-tag ${m.tagClass}">${m.tag}</span></td>
                <td><div class="rt-focus">${m.focus}</div><div class="rt-message">${m.message}</div></td>
                <td>${money(m.budget)}</td>
              </tr>
            `).join('')}</tbody>
          </table>
        </div>

        <div class="rpt-cta-band">
          <div>
            <div class="rcb-title">Ready to put this plan into action?</div>
            <p class="rcb-sub">Speak with a Smart 1 strategist to confirm media availability, pricing, and launch timing for your market.</p>
          </div>
          <button id="btn-continue-report" class="no-print">Get my plan & PDF ↓</button>
        </div>

        <p class="rpt-disclaimer">This report is a suggested planning estimate, not a guarantee of performance. See the full disclaimer below.</p>
        <details class="disclaimer-box no-print">
          <summary>Suggested Planning Disclaimer</summary>
          <p style="margin-top:10px;">This report is a suggested marketing plan based on the business information provided, available market data, industry gross-margin assumptions, modeled audience estimates, local climate information, and Smart 1's planning methodology. Seasonality, dayparts, holiday opportunities, audience size, media costs, customer acquisition, revenue, and return-on-investment figures are estimates and are not guarantees of performance. Actual results may vary based on competition, weather, visitor demand, pricing, creative, website performance, tracking, market conditions, and other factors. Media inventory, pricing, targeting, and final campaign recommendations must be confirmed before activation. To customize the geographic coverage, products, timing, messaging, or budget, contact Smart 1 Marketing.</p>
        </details>
      </div>
      <div class="rpt-footer"><span>Smart 1 Marketing · (614) 536-0768 · smart1marketing.com</span><span>${escapeHtml(marketLabel)}</span></div>
    </div>
  `;
}

/* ---- the same report, as data rather than markup -------------------------
   The PDF is built server-side with reportlab. Rather than reimplement any of
   the arithmetic in Python — which would leave two versions of the same plan
   free to disagree — this returns exactly what buildReportMarkup() renders,
   as plain JSON. The browser owns the numbers; the server owns the layout.

   Blocks are deliberately primitive (paragraph, stats, pills, table…) so the
   Python side lays out shapes it already understands, and a new section here
   needs no Python change unless it needs a new shape.                        */

function buildReportModel(a) {
  const profile = getCategoryProfile(a.category === '__other__' ? null : a.category);
  const seasons = getSeasons(a);
  const dayparts = getDayparts(a);
  const holidayLabels = (a.customHolidays || []).map(k => (HOLIDAYS.find(h => h.key === k) || {}).label).filter(Boolean);
  const audiences = getAudiences(a);
  const products = getProductPlan(a);
  const region = REGIONS[a.region] || REGIONS.SOUTHEAST;
  const r = a.budgetCalc;
  const dist = a.seasonalDist;
  const annualTotal = r.monthlyBudget * 12;
  const annualRevenue = r.revenue * 12;
  const categoryLabel = a.category === '__other__' ? a.categoryOther : a.category;
  const marketLabel = a.city ? (a.city + ', ' + a.state) : (a.state || '—');
  const weatherLabel = weatherTriggerLabel(a);
  const audienceCount = (audiences.before ? audiences.before.length : 0) + (audiences.inMarket ? audiences.inMarket.length : 0);
  const monthlyPlan = buildMonthlyPlan(seasons, dist, profile.familyKey, weatherLabel);
  const webPresence = classifyWebPresence(a);
  const reallocPct = annualTotal > 0
    ? Math.round(['primary','shoulder','slow'].reduce(function (s, k) {
        return s + Math.max(0, (dist[k].effectiveMonthly - r.monthlyBudget)) * dist[k].monthCount;
      }, 0) / annualTotal * 100)
    : 0;

  const meta = [
    { label: 'Market', value: marketLabel },
    { label: 'Travel distance', value: a.travelDistance || '—' },
    { label: 'Category', value: categoryLabel || '—' },
    { label: 'Primary season', value: monthsToRangeString(seasons.primary) },
    { label: 'Suggested plan', value: a.tier },
    { label: 'Annual plan total', value: money(annualTotal) }
  ];
  if (a.contact && a.contact.name) meta.push({ label: 'Contact', value: a.contact.name });
  if (a.contact && a.contact.email) meta.push({ label: 'Email', value: a.contact.email });

  const sections = [];

  sections.push({ label: 'Your seasonal opportunity at a glance', badge: region.label, blocks: [
    { type: 'stats', items: [
      { label: 'Primary season length', value: seasons.primary.length + ' mo', sub: monthsToRangeString(seasons.primary) },
      { label: 'Recommended audiences', value: String(audienceCount), sub: 'before-arrival & in-market segments' },
      { label: 'Holiday opportunities', value: String(holidayLabels.length), sub: 'seasonal travel windows identified' }
    ]},
    { type: 'p', text: 'Based on the ' + (a.zip ? 'ZIP code ' + a.zip : 'location')
        + ' provided, your business category, and ' + region.label.toLowerCase()
        + ' tourism patterns, demand for ' + (categoryLabel || 'your business').toLowerCase()
        + ' is strongest ' + monthsToRangeString(seasons.primary).toLowerCase()
        + ', with a shoulder build-up ' + monthsToRangeString(seasons.shoulder).toLowerCase()
        + '. This plan paces your media spend to match that pattern instead of spreading it evenly across the year.' }
  ]});

  if (Array.isArray(a.aiNarrative) && a.aiNarrative.length) {
    sections.push({ label: 'Strategic assessment', blocks:
      a.aiNarrative.map(function (t) { return { type: 'p', text: t }; })
        .concat([{ type: 'note', text: 'AI-assisted commentary generated from the figures above — provided for context, not a substitute for them.' }]) });
  }

  const deliversBlocks = [
    { type: 'highlight', title: 'Estimated monthly performance at your ' + a.tier + ' plan', items: [
      { label: 'Est. monthly revenue', value: money(r.revenue), sub: 'at ' + money(a.avgValue) + ' avg. value' },
      { label: 'Revenue-to-spend ratio', value: r.roas + 'x', sub: 'estimated return' },
      { label: 'Est. customers / month', value: r.customers.toLocaleString(), sub: 'at ' + money(r.cac) + ' est. acquisition cost' }
    ], note: 'Over 12 months, that’s an estimated ' + money(annualRevenue) + ' in revenue against a '
        + money(annualTotal) + ' media investment — roughly ' + r.roas + 'x back for every dollar spent. Break-even is roughly '
        + r.breakeven.toLocaleString() + ' customer' + (r.breakeven === 1 ? '' : 's')
        + ' per month at this budget level. Figures are industry planning assumptions, not guarantees.' }
  ];
  if (reallocPct > 0) {
    deliversBlocks.push({ type: 'p', bold: 'Where the efficiency comes from:',
      text: ' instead of spending the same amount every month, this plan moves ' + reallocPct
        + '% of your annual budget out of low-demand months and into your peak travel windows — plus weather-smart pacing that pulls delivery back when conditions suppress demand. That’s spend a flat, year-round schedule would have used on weeks when travelers simply aren’t there.' });
  }
  sections.push({ label: 'What a seasonal, weather-smart plan delivers', blocks: deliversBlocks });

  if (webPresence === 'none' || webPresence === 'social_only') {
    sections.push({ label: 'One thing your plan needs: a place to send visitors', blocks: [{ type: 'p', text: webPresence === 'none'
      ? 'Every ad in this plan needs somewhere to send interested travelers — a page with your offer, photos, hours, and an easy way to book or call. Since you don’t have a website yet, we recommend starting with a simple, mobile-friendly landing page. It doesn’t need to be a full website: one great page is enough to launch, and it also lets us measure exactly which ads turn into calls and bookings, so your budget keeps getting more efficient over time. Smart 1 can build and host this page for you as part of your plan.'
      : 'A Facebook page is a great start — but ads perform measurably better when they send travelers to a fast, dedicated landing page with your offer, photos, and an easy way to book or call. It also lets us track exactly which ads turn into calls and bookings (something a Facebook page can’t do), so your budget keeps getting more efficient over time. Smart 1 can build and host a simple landing page for you as part of your plan — your Facebook page keeps doing what it does best alongside it.' }] });
  }

  sections.push({ label: 'A focused plan vs. advertising without one', blocks: [
    { type: 'compare',
      bad: { title: 'Advertising without a seasonal plan', sub: 'Common outcome', items: [
        'Budget spent evenly across months regardless of demand',
        'Full delivery continues during unfavorable weather',
        'Holiday travel windows and dayparts aren’t prioritized',
        'Same audiences and creative used year-round' ] },
      good: { title: 'A weather-smart seasonal plan', sub: 'This plan', items: [
        'Budget weighted toward ' + monthsToRangeString(seasons.primary).toLowerCase(),
        weatherLabel,
        holidayLabels.length ? 'Prioritizes ' + holidayLabels.slice(0, 3).join(', ') : 'Dayparts prioritized: ' + dayparts.join(', '),
        'Before-arrival and in-market audiences targeted separately' ] } }
  ]});

  sections.push({ label: 'Recommended media, dayparts & holidays', blocks: [
    { type: 'pills', label: 'Media channels', items: products },
    { type: 'pills', label: 'Strongest dayparts', items: dayparts },
    { type: 'pills', label: 'Holiday travel windows', items: holidayLabels.length ? holidayLabels : ['Not prioritized'] }
  ]});

  sections.push({ label: 'Good · Better · Best', blocks: [
    { type: 'tiers', items: ['Good', 'Better', 'Best'].map(function (t) {
        return { name: t, selected: a.tier === t,
                 price: money(TIERS[t].monthlyMin) + '–' + money(TIERS[t].monthlyMax) + ' /mo',
                 tagline: TIER_COPY[t].tagline, items: TIER_COPY[t].items.slice(0, 5) };
      }) }
  ]});

  sections.push({ label: 'Suggested media budget', blocks: [
    { type: 'stats', items: [
      { label: 'At peak (primary season)', value: money(dist.primary.effectiveMonthly), sub: 'per month' },
      { label: 'Blended monthly average', value: money(r.monthlyBudget), sub: 'per month' },
      { label: 'Annual plan total', value: money(annualTotal), sub: 'across 12 months' }
    ]}
  ]});

  sections.push({ label: 'Seasonal budget split', blocks: [
    { type: 'bars', items: [
      { key: 'primary', label: 'Primary season', months: seasons.primary },
      { key: 'shoulder', label: 'Shoulder season', months: seasons.shoulder },
      { key: 'slow', label: 'Slow season', months: seasons.slow }
    ].map(function (row) {
      return { label: row.label, months: monthsToRangeString(row.months),
               pct: dist[row.key].pct, total: money(dist[row.key].total),
               sub: '≈ ' + money(dist[row.key].effectiveMonthly) + '/mo across ' + dist[row.key].monthCount
                    + ' month' + (dist[row.key].monthCount > 1 ? 's' : '') };
    }) }
  ]});

  sections.push({ label: 'Month-by-month campaign plan & budget', blocks: [
    { type: 'table', head: ['Month', 'Focus', 'Budget'],
      rows: monthlyPlan.map(function (m) {
        return [m.month + '\n' + m.tag, m.focus + '\n' + m.message, money(m.budget)];
      }) }
  ]});

  return {
    businessName: a.businessName || categoryLabel || 'Your business',
    title: 'Seasonal Tourism Marketing Plan',
    subtitle: 'Suggested seasonal media plan — directional assessment',
    preparedOn: reportDateString(),
    website: a.website || '',
    meta: meta,
    sections: sections,
    cta: { title: 'Ready to put this plan into action?',
           sub: 'Speak with a Smart 1 strategist to confirm media availability, pricing, and launch timing for your market.' }
  };
}

/* Exposed for report-print.html's bootstrap script and app.js's stepReport(). */
if (typeof window !== 'undefined') {
  window.Smart1ReportTemplate = { buildReportMarkup, buildReportModel, getSeasons, getDayparts, getHolidayRecs, getAudiences, getProductPlan, reportDateString, weatherTriggerLabel, classifyWebPresence };
}
