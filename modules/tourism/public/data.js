/* ==========================================================================
   Smart 1 Seasonal Tourism Marketing Planner — Data
   Region climate profiles, business category assumptions, and holiday data.
   All figures are industry planning assumptions, not guarantees.
   ========================================================================== */

const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];

/* Good / Better / Best tier copy (monthly $ ranges live in engine.js's
   TIERS). Used by the wizard's tier-selection step and by the shared
   report template (report-template.js). */
const TIER_COPY = {
  Good: { tagline: 'Capture visitors already looking for something to do.', items: ['Programmatic display','Mobile display','Local geofencing','Retargeting','Weather-based pacing','Smart 1 Suite core tools','Monthly reporting'] },
  Better: { tagline: 'Reach tourists before arrival and while they are nearby.', items: ['Everything in Good','Travel-intent audiences','Multiple lodging & visitor geofences','Mobile video','Streaming radio','Location look-back','Expanded weather pacing','Multiple creative messages'] },
  Best: { tagline: 'Build destination awareness and own the seasonal opportunity.', items: ['Everything in Better','Connected TV','Online video','Podcast targeting','Digital Out-of-Home','Multiple feeder markets','Advanced location strategies','Frequent optimization'] },
};

function monthsToRangeString(arr) {
  if (!arr || !arr.length) return 'Not enough data';
  const set = new Set(arr);
  if (set.size === 12) return 'Year-round';
  const sorted = [...set].sort((a, b) => a - b);
  // Group cyclically (Dec -> Jan) so winter seasons like [12,1,2,3] read as
  // "December through March" instead of splitting at the year boundary.
  const prevMonth = m => (m === 1 ? 12 : m - 1);
  const nextMonth = m => (m === 12 ? 1 : m + 1);
  let startIdx = sorted.findIndex(m => !set.has(prevMonth(m)));
  if (startIdx === -1) startIdx = 0;
  const rotated = sorted.slice(startIdx).concat(sorted.slice(0, startIdx));
  const ranges = [];
  let start = rotated[0], prev = rotated[0];
  for (let i = 1; i <= rotated.length; i++) {
    const cur = rotated[i];
    if (i < rotated.length && cur === nextMonth(prev)) { prev = cur; continue; }
    ranges.push([start, prev]);
    if (i < rotated.length) { start = cur; prev = cur; }
  }
  return ranges.map(([s, e]) => (s === e ? MONTHS[s - 1] : `${MONTHS[s - 1]} through ${MONTHS[e - 1]}`)).join(' and ');
}

/* ---------------------------------------------------------------------
   Region / climate profiles.
   Each profile gives default primary / shoulder / slow season months
   (1-12), a human label, and weather-risk flags used to drive the
   weather-smart pacing logic and holiday relevance.
   --------------------------------------------------------------------- */
const REGIONS = {
  FL_SOUTH: { label: 'South Florida & the Keys', primary: [12,1,2,3,4], shoulder: [11,5], slow: [6,7,8,9,10], flags: { hurricane: true, heat: true, snow: false, rain: false } },
  FL_CENTRAL: { label: 'Central Florida', primary: [6,7,8,12], shoulder: [3,4,5,9,10], slow: [1,2,11], flags: { hurricane: true, heat: true, snow: false, rain: false } },
  FL_NORTH: { label: 'North Florida & the Panhandle', primary: [6,7,8], shoulder: [3,4,5,9,10], slow: [1,2,11,12], flags: { hurricane: true, heat: true, snow: false, rain: false } },
  GULF_SOUTH: { label: 'Gulf South (AL / MS / LA)', primary: [6,7,8], shoulder: [3,4,5,9,10], slow: [11,12,1,2], flags: { hurricane: true, heat: true, snow: false, rain: false } },
  TX_GULF: { label: 'Texas Gulf Coast', primary: [6,7,8], shoulder: [3,4,5,9,10], slow: [11,12,1,2], flags: { hurricane: true, heat: true, snow: false, rain: false } },
  TX_INLAND: { label: 'Inland Texas', primary: [3,4,5,10,11], shoulder: [2,9,12], slow: [6,7,8,1], flags: { hurricane: false, heat: true, snow: false, rain: false } },
  SOUTHEAST: { label: 'Southeast & Appalachia', primary: [6,7,8], shoulder: [3,4,5,9,10], slow: [12,1,2], flags: { hurricane: false, heat: true, snow: false, rain: false } },
  NORTHEAST: { label: 'Northeast & Mid-Atlantic', primary: [6,7,8], shoulder: [4,5,9,10], slow: [11,12,1,2,3], flags: { hurricane: false, heat: false, snow: true, rain: false } },
  MIDWEST_LAKES: { label: 'Great Lakes & Midwest', primary: [6,7,8], shoulder: [4,5,9,10], slow: [11,12,1,2,3], flags: { hurricane: false, heat: false, snow: true, rain: false } },
  GREAT_PLAINS: { label: 'Great Plains', primary: [6,7,8], shoulder: [5,9], slow: [10,11,12,1,2,3,4], flags: { hurricane: false, heat: true, snow: true, rain: false } },
  DESERT_SOUTHWEST: { label: 'Desert Southwest', primary: [10,11,12,1,2,3,4], shoulder: [5,9], slow: [6,7,8], flags: { hurricane: false, heat: true, snow: false, rain: false } },
  MOUNTAIN_SKI: { label: 'Mountain & Ski Country', primary: [12,1,2,3,6,7,8], shoulder: [4,5,9,10], slow: [11], flags: { hurricane: false, heat: false, snow: true, rain: false, ski: true } },
  PACIFIC_NW: { label: 'Pacific Northwest', primary: [6,7,8,9], shoulder: [4,5,10], slow: [11,12,1,2,3], flags: { hurricane: false, heat: false, snow: false, rain: true } },
  CALIFORNIA_NORTH: { label: 'Northern California Coastal', primary: [6,7,8,9], shoulder: [4,5,10], slow: [11,12,1,2,3], flags: { hurricane: false, heat: false, snow: false, rain: true } },
  CALIFORNIA_SOUTH: { label: 'Southern California Coastal', primary: [6,7,8], shoulder: [3,4,5,9,10], slow: [11,12,1,2], flags: { hurricane: false, heat: true, snow: false, rain: false } },
  ALASKA: { label: 'Alaska', primary: [6,7,8], shoulder: [5,9], slow: [10,11,12,1,2,3,4], flags: { hurricane: false, heat: false, snow: true, rain: true } },
  HAWAII: { label: 'Hawaii', primary: [12,1,2,3,4,6,7,8], shoulder: [5,9], slow: [10,11], flags: { hurricane: false, heat: true, snow: false, rain: true } },
};

function regionForZip(state, lat, lon) {
  const abbr = (state || '').toUpperCase();
  lat = Number(lat); lon = Number(lon);
  if (abbr === 'FL') {
    if (lat < 27) return 'FL_SOUTH';
    if (lat < 29.5) return 'FL_CENTRAL';
    return 'FL_NORTH';
  }
  if (abbr === 'CA') return lat >= 36 ? 'CALIFORNIA_NORTH' : 'CALIFORNIA_SOUTH';
  if (abbr === 'TX') return (lon > -97.5 && lat < 30.5) ? 'TX_GULF' : 'TX_INLAND';
  if (['AL','MS','LA'].includes(abbr)) return 'GULF_SOUTH';
  if (['GA','SC','NC','VA','TN','KY','WV','AR'].includes(abbr)) return 'SOUTHEAST';
  if (['ME','NH','VT','MA','RI','CT','NY','NJ','PA','MD','DE','DC'].includes(abbr)) return 'NORTHEAST';
  if (['MI','WI','MN','IL','IN','OH','IA'].includes(abbr)) return 'MIDWEST_LAKES';
  if (['ND','SD','NE','KS','OK','MO'].includes(abbr)) return 'GREAT_PLAINS';
  if (['AZ','NV','NM'].includes(abbr)) return 'DESERT_SOUTHWEST';
  if (['CO','UT','MT','WY','ID'].includes(abbr)) return 'MOUNTAIN_SKI';
  if (['WA','OR'].includes(abbr)) return 'PACIFIC_NW';
  if (abbr === 'AK') return 'ALASKA';
  if (abbr === 'HI') return 'HAWAII';
  return 'SOUTHEAST';
}

/* ---------------------------------------------------------------------
   Business categories — grouped exactly as the Smart 1 planner slides,
   each item tagged with a "class" (drives weather logic + dayparts)
   and a family key (drives margin / CAC / product assumptions).
   --------------------------------------------------------------------- */
const CATEGORY_GROUPS = [
  { group: 'Food and beverage', items: [
    'Restaurant','Bar or brewery','Breakfast or brunch','Coffee shop','Ice cream or dessert','Fine dining','Quick-service restaurant','Dinner show','Food tour',
  ]},
  { group: 'Attractions and entertainment', items: [
    'Family attraction','Theme park','Museum','Aquarium','Live entertainment','Dinner theater','Escape room','Miniature golf','Mountain coaster','Water park','Indoor attraction',
  ]},
  { group: 'Outdoor recreation', items: [
    'Boat rental','Fishing charter','Dolphin or wildlife tour','Jet ski rental','Kayak rental','Surfing lesson','Guided hiking tour','Zip line','Ski resort','Snowmobile rental','Golf course','Bicycle or scooter rental',
  ]},
  { group: 'Lodging and hospitality', items: [
    'Hotel','Resort','Vacation rental','Cabin rental','Campground','RV resort','Bed-and-breakfast','Property management company',
  ]},
  { group: 'Retail', items: [
    'Souvenir store','Beachwear store','Outdoor equipment store','Art gallery','Jewelry store','Specialty food retailer','Shopping center','Local craft store',
  ]},
  { group: 'Visitor services', items: [
    'Shuttle service','Rental car company','Golf cart rental','Parking service','Airport transportation','Tour operator','Wedding service','Photographer',
  ]},
  { group: 'Professional and emergency services', items: [
    'Vacation-home real estate','Personal injury law','Urgent care','Emergency dental','Towing','Automotive repair','Marine repair','Insurance','Wedding venue','Event planning',
  ]},
];

// family assumptions: class drives weather + dayparts, margin/cac drive Slide 4 & 15
const CATEGORY_FAMILIES = {
  restaurant:    { class: 'restaurant',   margin: [60,70], cacBasis: 'average party check',      cac: [10,20], dayparts: ['Breakfast','Lunch','Happy hour','Dinner','Late night'] },
  attraction:    { class: 'attraction',   margin: [60,75], cacBasis: 'average family purchase',   cac: [15,25], dayparts: ['Morning planning period','Midday','Early evening','Weekend mornings'] },
  outdoor:       { class: 'outdoor',      margin: [50,70], cacBasis: 'average booking value',      cac: [15,30], dayparts: ['Early morning','Evening planning period','Weekend mornings','1-3 days before favorable weather'] },
  lodging:       { class: 'lodging',      margin: [55,70], cacBasis: 'average reservation value',  cac: [10,25], dayparts: ['Evening research','Weekend planning','Lunch-hour research','Sunday travel planning'] },
  vacationmgmt:  { class: 'lodging',      margin: [30,50], cacBasis: 'average reservation value',  cac: [10,25], dayparts: ['Evening research','Weekend planning','Lunch-hour research','Sunday travel planning'] },
  retail:        { class: 'retail',       margin: [40,55], cacBasis: 'average transaction',        cac: [10,20], dayparts: ['Midday','Afternoon','Early evening','Weekend afternoons'] },
  service:       { class: 'service',      margin: [50,65], cacBasis: 'average booking or fare',     cac: [15,25], dayparts: ['Morning','Midday','Afternoon','Airport & travel-corridor windows'] },
  professional:  { class: 'professional', margin: [60,85], cacBasis: 'estimated signed-client value', cac: [15,25], dayparts: ['Morning','Midday','Weekday business hours'] },
  realestate:    { class: 'professional', margin: [70,90], cacBasis: 'estimated commission value',  cac: [10,20], dayparts: ['Morning','Evening research','Weekend planning'] },
  emergency:     { class: 'emergency',    margin: [50,70], cacBasis: 'average completed-client value', cac: [15,25], dayparts: ['Anytime — immediate need', 'Daytime hours', 'Weekend coverage'] },
  eventvenue:    { class: 'attraction',   margin: [60,75], cacBasis: 'average booking value',       cac: [15,25], dayparts: ['Evening research','Weekend planning','Lunch-hour research'] },
  other:         { class: 'service',      margin: [50,65], cacBasis: 'average transaction',         cac: [15,25], dayparts: ['Midday','Afternoon','Evening'] },
};

// map every specific business item to a family + a few per-item overrides (dayparts/class)
const CATEGORY_ITEM_MAP = {
  'Restaurant': { family: 'restaurant' },
  'Bar or brewery': { family: 'restaurant', dayparts: ['Happy hour','Dinner','Late night','Weekend afternoons'] },
  'Breakfast or brunch': { family: 'restaurant', dayparts: ['Early morning','Breakfast','Weekend mornings','Lunch'] },
  'Coffee shop': { family: 'restaurant', dayparts: ['Early morning','Morning commute','Midday'] },
  'Ice cream or dessert': { family: 'restaurant', dayparts: ['Afternoon','Evening','Weekend afternoons'] },
  'Fine dining': { family: 'restaurant', dayparts: ['Dinner','Late night','Weekend evenings'] },
  'Quick-service restaurant': { family: 'restaurant', dayparts: ['Lunch','Dinner','Late night'] },
  'Dinner show': { family: 'attraction', dayparts: ['Early evening','Dinner','Weekend evenings'] },
  'Food tour': { family: 'attraction', dayparts: ['Morning planning period','Midday','Early evening'] },

  'Family attraction': { family: 'attraction' },
  'Theme park': { family: 'attraction' },
  'Museum': { family: 'attraction', class: 'attraction' },
  'Aquarium': { family: 'attraction' },
  'Live entertainment': { family: 'attraction', dayparts: ['Early evening','Weekend evenings','Midday'] },
  'Dinner theater': { family: 'attraction', dayparts: ['Early evening','Dinner','Weekend evenings'] },
  'Escape room': { family: 'attraction', dayparts: ['Afternoon','Early evening','Weekend afternoons'] },
  'Miniature golf': { family: 'attraction', dayparts: ['Afternoon','Early evening','Weekend afternoons'] },
  'Mountain coaster': { family: 'outdoor' },
  'Water park': { family: 'attraction', dayparts: ['Midday','Early afternoon','Weekend mornings'] },
  'Indoor attraction': { family: 'attraction' },

  'Boat rental': { family: 'outdoor' },
  'Fishing charter': { family: 'outdoor' },
  'Dolphin or wildlife tour': { family: 'outdoor' },
  'Jet ski rental': { family: 'outdoor' },
  'Kayak rental': { family: 'outdoor' },
  'Surfing lesson': { family: 'outdoor' },
  'Guided hiking tour': { family: 'outdoor' },
  'Zip line': { family: 'outdoor' },
  'Ski resort': { family: 'outdoor' },
  'Snowmobile rental': { family: 'outdoor' },
  'Golf course': { family: 'outdoor', dayparts: ['Early morning','Weekend mornings','Afternoon tee times','1-3 days before favorable weather'] },
  'Bicycle or scooter rental': { family: 'outdoor' },

  'Hotel': { family: 'lodging' },
  'Resort': { family: 'lodging' },
  'Vacation rental': { family: 'lodging' },
  'Cabin rental': { family: 'lodging' },
  'Campground': { family: 'lodging' },
  'RV resort': { family: 'lodging' },
  'Bed-and-breakfast': { family: 'lodging' },
  'Property management company': { family: 'vacationmgmt' },

  'Souvenir store': { family: 'retail' },
  'Beachwear store': { family: 'retail' },
  'Outdoor equipment store': { family: 'retail' },
  'Art gallery': { family: 'retail' },
  'Jewelry store': { family: 'retail' },
  'Specialty food retailer': { family: 'retail' },
  'Shopping center': { family: 'retail' },
  'Local craft store': { family: 'retail' },

  'Shuttle service': { family: 'service', dayparts: ['Early morning','Evening','Airport & travel-corridor windows'] },
  'Rental car company': { family: 'service', dayparts: ['Morning','Airport & travel-corridor windows'] },
  'Golf cart rental': { family: 'service' },
  'Parking service': { family: 'service' },
  'Airport transportation': { family: 'service', dayparts: ['Early morning','Evening','Airport & travel-corridor windows'] },
  'Tour operator': { family: 'attraction' },
  'Wedding service': { family: 'eventvenue' },
  'Photographer': { family: 'service' },

  'Vacation-home real estate': { family: 'realestate' },
  'Personal injury law': { family: 'professional' },
  'Urgent care': { family: 'emergency' },
  'Emergency dental': { family: 'emergency' },
  'Towing': { family: 'emergency' },
  'Automotive repair': { family: 'emergency' },
  'Marine repair': { family: 'emergency' },
  'Insurance': { family: 'professional' },
  'Wedding venue': { family: 'eventvenue' },
  'Event planning': { family: 'eventvenue' },
};

function getCategoryProfile(categoryName) {
  const map = CATEGORY_ITEM_MAP[categoryName];
  const familyKey = map ? map.family : 'other';
  const family = CATEGORY_FAMILIES[familyKey];
  return {
    familyKey,
    class: (map && map.class) || family.class,
    margin: family.margin,
    cacBasis: family.cacBasis,
    cac: family.cac,
    dayparts: (map && map.dayparts) || family.dayparts,
  };
}

/* ---------------------------------------------------------------------
   Holidays / travel periods.
   months: rough travel-decision window; tags used for relevance filtering.
   --------------------------------------------------------------------- */
const HOLIDAYS = [
  { key: 'newyear', label: 'New Year travel', months: [12,1], tags: ['all'] },
  { key: 'presidents', label: 'Presidents Day weekend', months: [2], tags: ['ski','snowbird','warmwinter'] },
  { key: 'springbreak', label: 'Spring break', months: [3,4], tags: ['all'] },
  { key: 'easter', label: 'Easter travel', months: [3,4], tags: ['all'] },
  { key: 'mothersday', label: "Mother's Day", months: [5], tags: ['restaurant','attraction','lodging'] },
  { key: 'memorial', label: 'Memorial Day weekend', months: [5], tags: ['all'] },
  { key: 'fathersday', label: "Father's Day", months: [6], tags: ['restaurant','attraction','outdoor'] },
  { key: 'independence', label: 'Independence Day', months: [7], tags: ['all'] },
  { key: 'labor', label: 'Labor Day weekend', months: [9], tags: ['all'] },
  { key: 'fallbreak', label: 'Fall break', months: [10], tags: ['leaf','attraction','family'] },
  { key: 'halloween', label: 'Halloween travel', months: [10], tags: ['attraction','family'] },
  { key: 'thanksgiving', label: 'Thanksgiving', months: [11], tags: ['all'] },
  { key: 'christmas', label: 'Christmas travel', months: [12], tags: ['all'] },
  { key: 'winterbreak', label: 'Winter school break', months: [12,1], tags: ['ski','snowbird','warmwinter'] },
  { key: 'valentines', label: "Valentine's Day", months: [2], tags: ['restaurant','lodging','eventvenue'] },
];

function recommendHolidays(regionKey, familyKey) {
  const region = REGIONS[regionKey];
  const relevantTags = ['all'];
  if (region.flags.ski) relevantTags.push('ski');
  if (['DESERT_SOUTHWEST','FL_SOUTH','FL_CENTRAL','HAWAII'].includes(regionKey)) relevantTags.push('snowbird', 'warmwinter');
  if (['NORTHEAST','MIDWEST_LAKES','SOUTHEAST'].includes(regionKey)) relevantTags.push('leaf');
  if (familyKey) relevantTags.push(familyKey);
  if (['attraction','eventvenue'].includes(familyKey)) relevantTags.push('family', 'attraction');
  return HOLIDAYS.filter(h => h.tags.some(t => relevantTags.includes(t)));
}

/* ---------------------------------------------------------------------
   Purchase-type suggestions, by category family. Shown first (as the
   "suggested for your business" set) on the activity step instead of
   always showing all 9 generic purchase types to every business. The
   full list is still reachable via a "show all types" toggle in case
   a business's actual purchase type isn't in the suggested subset.
   --------------------------------------------------------------------- */
const PURCHASE_TYPE_SUGGESTIONS = {
  restaurant:   ['Individual purchase', 'Family or group purchase', 'Reservation'],
  attraction:   ['Individual purchase', 'Family or group purchase', 'Ticket package', 'Reservation'],
  outdoor:      ['Reservation', 'Rental', 'Family or group purchase', 'Individual purchase'],
  lodging:      ['Overnight booking', 'Reservation', 'Family or group purchase'],
  vacationmgmt: ['Overnight booking', 'Reservation', 'Qualified lead'],
  retail:       ['Individual purchase', 'Family or group purchase'],
  service:      ['Reservation', 'Service appointment', 'Individual purchase'],
  professional: ['Professional consultation', 'Qualified lead', 'Service appointment'],
  realestate:   ['Qualified lead', 'Professional consultation'],
  emergency:    ['Service appointment', 'Individual purchase'],
  eventvenue:   ['Reservation', 'Qualified lead', 'Professional consultation'],
  other:        null, // null = no filtering, show the full PURCHASE_TYPES list
};

function suggestedPurchaseTypes(familyKey, allTypes) {
  const suggested = PURCHASE_TYPE_SUGGESTIONS[familyKey];
  if (!suggested) return allTypes;
  // Keep the suggested ones in their curated order, then anything else the
  // family list didn't mention (so "show all" never loses an option).
  const rest = allTypes.filter(t => !suggested.includes(t));
  return { suggested, rest };
}

/* Everything above is also loaded by the Node backend (lib/analyzeBusiness.js)
   for its category-keyword heuristic. Guarded so the plain <script> load in
   the browser (where `module` doesn't exist) is unaffected. */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    MONTHS, TIER_COPY, monthsToRangeString, REGIONS, CATEGORY_GROUPS, CATEGORY_FAMILIES,
    CATEGORY_ITEM_MAP, getCategoryProfile, HOLIDAYS, recommendHolidays,
    PURCHASE_TYPE_SUGGESTIONS, suggestedPurchaseTypes,
  };
}
