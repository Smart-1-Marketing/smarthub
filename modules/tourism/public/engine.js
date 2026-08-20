/* ==========================================================================
   Smart 1 Seasonal Tourism Marketing Planner — Recommendation Engine
   Pure functions that turn wizard answers into planning-assumption output.
   ========================================================================== */

/* ---- ZIP lookup (client-side, no key required) ------------------------- */
async function lookupZip(zip) {
  const res = await fetch(`https://api.zippopotam.us/us/${encodeURIComponent(zip)}`);
  if (!res.ok) throw new Error('ZIP not found');
  const data = await res.json();
  const place = data.places && data.places[0];
  if (!place) throw new Error('ZIP not found');
  return {
    city: place['place name'],
    state: place['state abbreviation'],
    stateName: place['state'],
    lat: parseFloat(place['latitude']),
    lon: parseFloat(place['longitude']),
  };
}

/* ---- Weather-smart pacing text ----------------------------------------- */
function weatherStrategyText(categoryClass, regionFlags) {
  const lines = [];
  if (categoryClass === 'outdoor') {
    lines.push('Reduce delivery when heavy rain, high wind, unsafe temperatures, storms, or poor marine/trail conditions are forecast.');
    lines.push('Increase normal delivery when conditions turn favorable — clear skies, comfortable temperatures, or improving weekend forecasts.');
    if (regionFlags.hurricane) lines.push('Build in seasonal hurricane-window pacing rules (June–November).');
    if (regionFlags.snow || regionFlags.ski) lines.push('Use snow and ice conditions to trigger increased delivery for winter-dependent activities.');
  } else if (categoryClass === 'attraction') {
    lines.push('Reduce general destination advertising during unusually low visitor-demand weather.');
    lines.push('Maintain or moderately increase in-market delivery during rain, excessive heat, or cold weather with "rainy-day," "stay cool indoors," and "open today" messaging.');
  } else if (categoryClass === 'restaurant') {
    lines.push('Reduce delivery during periods when tourism traffic is expected to be lower.');
    lines.push('Prioritize delivery around meal-decision windows, rain that keeps visitors near lodging districts, warm patio weather, and high visitor travel weekends.');
  } else if (categoryClass === 'lodging') {
    lines.push('Reduce short-term media when severe weather is likely to suppress travel.');
    lines.push('Maintain future-booking media when the weather only affects the current week.');
  } else {
    lines.push('Reduce delivery during conditions that typically suppress visitor travel in your market.');
    lines.push('Increase delivery when favorable weather or travel conditions return.');
  }
  return lines;
}

/* ---- Audience recommendations ------------------------------------------ */
const BEFORE_ARRIVAL_AUDIENCES = ['Travelers researching the destination','People shopping for lodging','Family vacation planners','Couples planning getaways','Road-trip travelers','Outdoor recreation enthusiasts','Beach travelers','Boaters','Golf travelers','Ski travelers','Wedding travelers','Likely visitors from feeder markets'];
const IN_MARKET_AUDIENCES = ['Hotel and resort guests','Vacation rental guests','Campground and RV resort visitors','Beach visitors','Park visitors','Attraction visitors','Airport arrivals','Cruise passengers','Competitor visitors','Shopping district visitors','Visitors traveling on nearby highways'];

function recommendAudiences(categoryClass, familyKey, regionFlags) {
  let before = ['Travelers researching the destination', 'Family vacation planners', 'Likely visitors from feeder markets'];
  let inMarket = ['Hotel and resort guests', 'Attraction visitors', 'Competitor visitors'];

  if (familyKey === 'outdoor') { before.push('Outdoor recreation enthusiasts', 'Road-trip travelers'); inMarket.push('Beach visitors', 'Park visitors', 'Visitors traveling on nearby highways'); }
  if (categoryClass === 'lodging' || familyKey === 'vacationmgmt') { before.push('People shopping for lodging', 'Couples planning getaways'); inMarket.push('Vacation rental guests', 'Campground and RV resort visitors'); }
  if (familyKey === 'restaurant') { inMarket.push('Shopping district visitors', 'Beach visitors'); }
  if (familyKey === 'attraction') { before.push('Family vacation planners'); inMarket.push('Park visitors', 'Airport arrivals'); }
  if (familyKey === 'retail') { inMarket.push('Shopping district visitors', 'Beach visitors'); }
  if (familyKey === 'eventvenue') { before.push('Wedding travelers', 'Couples planning getaways'); }
  if (regionFlags.ski) { before.push('Ski travelers'); }
  if (['Boat rental','Fishing charter','Jet ski rental','Dolphin or wildlife tour','Marine repair'].includes(familyKey)) { before.push('Boaters'); }
  if (familyKey === 'realestate' || familyKey === 'vacationmgmt') { before.push('Likely visitors from feeder markets'); }
  if (['professional','emergency'].includes(categoryClass)) { inMarket = ['Hotel and resort guests','Vacation rental guests','Campground and RV resort visitors','Beach visitors','Attraction visitors']; before = ['Travelers researching the destination','Family vacation planners','Road-trip travelers']; }

  const dedupe = arr => [...new Set(arr)];
  return { before: dedupe(before), inMarket: dedupe(inMarket) };
}

/* ---- Product plan -------------------------------------------------------
   Core products are always included. Everything else is conditional on
   category class, average value, and planning window (used as a proxy
   for buying-window length / regional destination appeal).
   -------------------------------------------------------------------- */
function recommendProducts(categoryClass, familyKey, avgValue, planningWindowKey, tier) {
  const core = ['Programmatic display', 'Location targeting and geofencing', 'Location look-back', 'Mobile display', 'Smart 1 Suite core tools'];
  const products = new Set(core);

  const videoCategories = ['attraction', 'lodging', 'outdoor', 'restaurant'];
  if (videoCategories.includes(categoryClass)) products.add('Mobile and online video');

  const longWindow = ['1 to 4 weeks before', '1 to 3 months before', 'More than 3 months before', "Let Smart 1 estimate"].includes(planningWindowKey);
  if ((avgValue >= 250 || longWindow) && tier !== 'Good') products.add('Connected TV');

  const roadtripFamilies = ['outdoor', 'lodging', 'vacationmgmt'];
  if (roadtripFamilies.includes(familyKey) || longWindow) products.add('Streaming radio');

  const podcastFamilies = ['outdoor', 'lodging', 'attraction', 'restaurant', 'vacationmgmt', 'realestate'];
  if (podcastFamilies.includes(familyKey) && tier !== 'Good') products.add('Podcast advertising');

  if (tier === 'Better' || tier === 'Best') products.add('Digital Out-of-Home');

  return [...products];
}

/* ---- Good / Better / Best tiers ----------------------------------------- */
const TIERS = {
  Good: { monthlyMin: 2500, monthlyMax: 5000 },
  Better: { monthlyMin: 5000, monthlyMax: 12500 },
  Best: { monthlyMin: 12500, monthlyMax: 30000 },
};

function recommendTier(avgValue, familyKey) {
  if (avgValue >= 400 || ['realestate'].includes(familyKey)) return 'Best';
  if (avgValue >= 90) return 'Better';
  return 'Good';
}

/* ---- Budget / ROI calculation -------------------------------------------
   CAC = midpoint of family CAC% x average value (professional/real estate
   use the same % applied to lead/commission value, clearly caveated).
   -------------------------------------------------------------------- */
function calcBudgetAndROI(tier, avgValue, cacRange) {
  const { monthlyMin, monthlyMax } = TIERS[tier];
  const monthlyBudget = Math.round((monthlyMin + monthlyMax) / 2);
  const cacPct = (cacRange[0] + cacRange[1]) / 2 / 100;
  const cac = Math.max(avgValue * cacPct, 1);
  const customers = monthlyBudget / cac;
  const revenue = customers * avgValue;
  const roas = revenue / monthlyBudget;
  const breakeven = monthlyBudget / avgValue;
  return {
    monthlyBudget, monthlyMin, monthlyMax,
    cac: Math.round(cac),
    customers: Math.round(customers),
    revenue: Math.round(revenue),
    roas: Math.round(roas * 10) / 10,
    breakeven: Math.round(breakeven),
  };
}

function seasonalDistribution(monthlyBudget, seasons) {
  const annual = monthlyBudget * 12;
  const split = { primary: 0.60, shoulder: 0.30, slow: 0.10 };
  const out = {};
  ['primary', 'shoulder', 'slow'].forEach(key => {
    const portion = annual * split[key];
    const monthCount = Math.max(seasons[key].length, 1);
    out[key] = { total: Math.round(portion), effectiveMonthly: Math.round(portion / monthCount), monthCount, pct: Math.round(split[key] * 100) };
  });
  return out;
}

/* ---- Dayparts / seasonality confirm defaults are read straight from
   data.js (getCategoryProfile, REGIONS) by app.js. */

/* ---- Month-by-month campaign plan -------------------------------------
   Buckets every calendar month into primary/shoulder/slow, assigns that
   season's effective monthly budget, and pairs it with a short focus
   message and the weather triggers relevant to the category. */
const MONTH_MESSAGE_TEMPLATES = {
  restaurant: { primary: ['Peak dining season', "Tonight's table is waiting — book now."], shoulder: ['Ramp-up dining traffic', 'Beat the rush — reserve your table.'], slow: ['Off-season loyalty & local guests', 'A warm table, any time of year.'] },
  attraction: { primary: ['Peak visitor season', 'Plan your visit — tickets moving fast.'], shoulder: ['Ramp-up trip planning', 'Add us to your trip itinerary.'], slow: ['Off-season awareness & value offers', 'A great day out, even in the off-season.'] },
  outdoor: { primary: ['Peak conditions & demand', 'Conditions are perfect — book your trip.'], shoulder: ['Favorable-weather windows', 'Catch the season before it turns.'], slow: ['Minimal, weather-triggered only', 'Watching for the next good-weather window.'] },
  lodging: { primary: ['Peak booking season', 'Book your stay before dates fill up.'], shoulder: ['Advance shoulder-season bookings', 'Lock in shoulder-season rates.'], slow: ['Off-season nurture & future bookings', 'Plan your next getaway now.'] },
  vacationmgmt: { primary: ['Peak booking season', 'Book your stay before dates fill up.'], shoulder: ['Advance shoulder-season bookings', 'Lock in shoulder-season rates.'], slow: ['Off-season nurture & future bookings', 'Plan your next getaway now.'] },
  retail: { primary: ['Peak shopper traffic', 'Stop in while you’re in town.'], shoulder: ['Ramp-up visitor traffic', 'Find something special for your trip.'], slow: ['Local & off-season promotions', 'Something for everyone, year-round.'] },
  service: { primary: ['Peak service demand', 'Book now — availability is limited.'], shoulder: ['Ramp-up bookings', 'Reserve ahead for your trip.'], slow: ['Off-season availability & value', 'We’re available when you need us.'] },
  professional: { primary: ['Peak inquiry season', 'Reach out for a consultation.'], shoulder: ['Ramp-up inquiries', 'Get ahead of the season — reach out today.'], slow: ['Off-season lead nurture', 'Planning ahead? Let’s talk.'] },
  emergency: { primary: ['Peak demand season', 'Available now — call today.'], shoulder: ['Ramp-up availability awareness', 'Available when you need us.'], slow: ['Steady off-season coverage', 'Here year-round when you need us.'] },
  eventvenue: { primary: ['Peak booking season', 'Reserve your date before it’s gone.'], shoulder: ['Advance booking push', 'Book your date for the season ahead.'], slow: ['Off-season inquiries & planning', 'Start planning your next event.'] },
  other: { primary: ['Peak season', 'Reach ready visitors now.'], shoulder: ['Ramp-up awareness', 'Plan ahead for the season.'], slow: ['Off-season awareness', 'Stay visible year-round.'] },
};

function buildMonthlyPlan(seasons, dist, familyKey, weatherTriggerLabels) {
  const templates = MONTH_MESSAGE_TEMPLATES[familyKey] || MONTH_MESSAGE_TEMPLATES.other;
  const bucketFor = (m) => {
    if (seasons.primary.includes(m)) return 'primary';
    if (seasons.shoulder.includes(m)) return 'shoulder';
    return 'slow';
  };
  const bucketMeta = { primary: { tag: 'PEAK', class: 'peak' }, shoulder: { tag: 'SHOULDER', class: 'shoulder' }, slow: { tag: 'OFF-SEASON', class: 'slow' } };
  return Array.from({ length: 12 }, (_, i) => {
    const m = i + 1;
    const bucket = bucketFor(m);
    const [focus, message] = templates[bucket];
    return {
      month: MONTHS[m - 1],
      bucket, tag: bucketMeta[bucket].tag, tagClass: bucketMeta[bucket].class,
      focus, message,
      budget: dist[bucket].effectiveMonthly,
      triggers: weatherTriggerLabels,
    };
  });
}

/* Lets Node (server.js's narrative-generation helper, lib/reportFacts.js)
   require() this same engine instead of re-implementing recommendation
   logic — mirrors the identical guard at the bottom of data.js. Inert in
   the browser (no `module` global there), so this changes nothing about
   how app.js/report-template.js load this file via <script>. */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    weatherStrategyText, BEFORE_ARRIVAL_AUDIENCES, IN_MARKET_AUDIENCES, recommendAudiences,
    recommendProducts, TIERS, recommendTier, calcBudgetAndROI, seasonalDistribution,
    MONTH_MESSAGE_TEMPLATES, buildMonthlyPlan,
  };
}
