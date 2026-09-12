/**
 * Market intelligence: service-area population and likely competitors.
 *
 * Both of these ask the model for figures it can only approximate. Every result
 * is returned with an explicit confidence and the reasoning behind it, and the
 * UI presents them as starting points the partner confirms — never as fact.
 */

const POP_SYSTEM = `You estimate the size of a local service area for a marketing audit.

You are given a ZIP code, a city, an industry, and how far the business travels for work. Estimate the population that business can realistically reach.

Rules:
- Use what you know about US geography and census figures. Approximate is fine and expected.
- The radius matters more than the ZIP. A business serving 25-50 miles from a suburban ZIP reaches the whole metro, not just that ZIP.
- If the ZIP is unfamiliar or looks invalid, fall back to the city, then to a typical US metro, and lower your confidence accordingly.
- Never return zero. If you have almost nothing to work with, return a reasonable US median and mark confidence low.
- Round to something honest: two significant figures, not a precise-looking number.

Return ONLY valid JSON:
{
  "population": 0,
  "households": 0,
  "areaName": "the place this covers, e.g. 'Columbus OH metro'",
  "confidence": "high|medium|low",
  "basis": "one sentence on what you based this on",
  "medianHouseholdIncome": 0,
  "medianAge": 0,
  "homeownershipRate": 0,
  "demographicNote": "one or two sentences on anything about this area that affects marketing — growth, income spread, age skew, urban or rural character"
}
Use 0 for any demographic figure you genuinely cannot estimate.`;

const COMP_SYSTEM = `You identify likely local competitors for a marketing audit.

You are given a business's website, ZIP code, city, and industry. Name businesses that plausibly compete with them for the same customers in that area.

Rules:
- Prefer real businesses you actually know operate in or near that market, including regional and national chains that serve it.
- Never invent a specific local business name to fill the list. If you only know national or regional players, return those and say so in "basis".
- Give a website only when you are reasonably confident of the domain. An empty string is much better than a wrong one.
- Do not include the client's own business.
- Order by how directly they compete.
- Never state a competitor's revenue, ad spend, or traffic. You have no such data.

Return ONLY valid JSON:
{
  "competitors": [
    {"name": "Business name", "website": "domain.com or empty string", "type": "local|regional|national", "why": "one short phrase on why they compete", "confidence": "high|medium|low"}
  ],
  "basis": "one sentence on how confident you are about this market and why",
  "note": "anything the partner should verify"
}
Return 4-8 competitors.`;

const COMPARE_SYSTEM = `You compare a client's website against a competitor's for a marketing audit.

You are given the results of automated scans of both home pages — conversion points, tracking tags, and structural signals. You did not see either rendered page.

Rules:
- Compare only what the scans show. Never assess design, speed, or copy quality.
- Be specific about what the competitor does that the client does not, and the reverse.
- If the competitor's scan failed or is thin, say so rather than inferring.
- No hype. This is for a business owner deciding where to spend.

Return ONLY valid JSON:
{
  "verdict": "one sentence on how the client's site compares",
  "clientAdvantages": ["what the client does that this competitor does not"],
  "competitorAdvantages": ["what this competitor does that the client does not"],
  "takeaway": "one sentence on the most useful thing to do about it"
}`;

async function askJson({ system, user, apiKey, model, maxTokens = 1200, timeout = 45000 }) {
  if (!apiKey) throw new Error('OpenAI is not configured');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const r = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({
        model, temperature: 0.2, max_tokens: maxTokens,
        response_format: { type: 'json_object' },
        messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
      }),
    });
    if (!r.ok) throw new Error(`OpenAI ${r.status}: ${(await r.text()).slice(0, 200)}`);
    const data = await r.json();
    return JSON.parse(data.choices?.[0]?.message?.content || '{}');
  } finally {
    clearTimeout(timer);
  }
}

/* ---------------------------------------------------------------
   Population
---------------------------------------------------------------- */

/** Last-resort figures so the audit always has an audience number to work with. */
const FALLBACK_POPULATION = {
  'Single neighborhood or town': 18000,
  'Up to 25 miles': 250000,
  '25–50 miles': 750000,
  'Statewide or regional': 3000000,
  'National': 330000000,
};

function fallbackMarket(ctx = {}) {
  const population = FALLBACK_POPULATION[ctx.serviceRadius] || 250000;
  return {
    population,
    households: Math.round(population / 2.5),
    areaName: ctx.cityMarket || (ctx.zipCode ? `ZIP ${ctx.zipCode}` : 'the service area'),
    confidence: 'low',
    basis: 'A typical US figure for this service radius. No local data was available.',
    medianHouseholdIncome: 0,
    medianAge: 0,
    homeownershipRate: 0,
    demographicNote: '',
    estimated: 'fallback',
  };
}

async function estimateMarket({ context = {}, apiKey, model = 'gpt-4o-mini' }) {
  if (!apiKey) return fallbackMarket(context);
  try {
    const user = `ZIP code: ${context.zipCode || 'not provided'}
City / market: ${context.cityMarket || 'not provided'}
Industry: ${context.industry || 'not provided'}
Service radius: ${context.serviceRadius || 'not provided'}
Locations: ${context.locations || 1}
Sells to: ${context.audienceType || 'not provided'}

Estimate the reachable service area.`;

    const out = await askJson({ system: POP_SYSTEM, user, apiKey, model, maxTokens: 700 });
    const population = Math.round(Number(out.population) || 0);
    if (!population || population < 500) return fallbackMarket(context);

    return {
      population,
      households: Math.round(Number(out.households) || population / 2.5),
      areaName: out.areaName || context.cityMarket || 'the service area',
      confidence: ['high', 'medium', 'low'].includes(out.confidence) ? out.confidence : 'medium',
      basis: out.basis || '',
      medianHouseholdIncome: Math.round(Number(out.medianHouseholdIncome) || 0),
      medianAge: Number(out.medianAge) || 0,
      homeownershipRate: Number(out.homeownershipRate) || 0,
      demographicNote: out.demographicNote || '',
      estimated: 'ai',
    };
  } catch (err) {
    console.error('market estimate failed:', err.message);
    return fallbackMarket(context);
  }
}

/* ---------------------------------------------------------------
   Competitors
---------------------------------------------------------------- */
async function suggestCompetitors({ context = {}, apiKey, model = 'gpt-4o-mini' }) {
  if (!apiKey) return { competitors: [], basis: 'AI suggestions are not configured.', note: '' };
  try {
    const user = `Client website: ${context.website || 'not provided'}
Client business name: ${context.clientName || 'not provided'}
ZIP code: ${context.zipCode || 'not provided'}
City / market: ${context.cityMarket || 'not provided'}
Industry: ${context.industry || 'not provided'}
Service radius: ${context.serviceRadius || 'not provided'}

Who competes with this business for the same customers?`;

    const out = await askJson({ system: COMP_SYSTEM, user, apiKey, model, maxTokens: 1200 });
    const clean = (out.competitors || [])
      .filter((c) => c && c.name)
      .slice(0, 8)
      .map((c) => ({
        name: String(c.name).slice(0, 80),
        website: String(c.website || '').replace(/^https?:\/\//i, '').replace(/\/.*$/, '').slice(0, 80),
        type: ['local', 'regional', 'national'].includes(c.type) ? c.type : 'local',
        why: String(c.why || '').slice(0, 140),
        confidence: ['high', 'medium', 'low'].includes(c.confidence) ? c.confidence : 'medium',
      }));
    return { competitors: clean, basis: out.basis || '', note: out.note || '' };
  } catch (err) {
    console.error('competitor suggestion failed:', err.message);
    return { competitors: [], basis: 'Competitor suggestions could not be generated.', note: '' };
  }
}

/* ---------------------------------------------------------------
   Head-to-head site comparison
---------------------------------------------------------------- */
function scanSummary(scan) {
  if (!scan) return 'scan unavailable';
  return [
    `${scan.counts?.forms ?? 0} form(s)`,
    `${scan.counts?.telLinks ?? 0} click-to-call`,
    `booking: ${(scan.booking || []).join(', ') || 'none'}`,
    `chat: ${(scan.chat || []).join(', ') || 'none'}`,
    `tracking: ${(scan.trackers || []).join(', ') || 'none'}`,
    `reviews shown: ${scan.flags?.reviews ? 'yes' : 'no'}`,
    `schema: ${scan.flags?.schema ? 'yes' : 'no'}`,
    `mobile viewport: ${scan.flags?.viewport ? 'yes' : 'no'}`,
  ].join(' | ');
}

async function compareSites({ clientScan, competitorName, competitorScan, apiKey, model = 'gpt-4o-mini' }) {
  if (!apiKey) return null;
  try {
    const user = `CLIENT SITE (${clientScan?.finalUrl || 'unknown'})
${scanSummary(clientScan)}

COMPETITOR: ${competitorName} (${competitorScan?.finalUrl || 'scan failed'})
${scanSummary(competitorScan)}

Compare them.`;
    return await askJson({ system: COMPARE_SYSTEM, user, apiKey, model, maxTokens: 700, timeout: 40000 });
  } catch (err) {
    console.error('site comparison failed:', err.message);
    return null;
  }
}

module.exports = { estimateMarket, suggestCompetitors, compareSites, fallbackMarket };
