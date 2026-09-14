/**
 * Smart 1 Marketing — Marketing Efficiency Audit™
 * Express server: serves the audit app and proxies analysis requests to the OpenAI API.
 *
 * Env vars (set these in Render → Environment):
 *   OPENAI_API_KEY   required for AI narrative (app still works without it)
 *   OPENAI_MODEL     optional, default gpt-4o-mini
 *   HUB_BASE_URL     required for lead delivery; see hubLeads.js
 *   PORT             set automatically by Render
 */

const express = require('express');
const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');
const { buildAuditPdf } = require('./pdf');
const cloudinary = require('./cloudinary');
const hubLeads = require('./hubLeads');
const multer = require('multer');
const { analyzeExpenses, isAccepted, ACCEPTED } = require('./expenses');
const { analyzeWebsite } = require('./website');
const { estimateAudience } = require('./audience');
const { estimateMarket, suggestCompetitors, compareSites } = require('./market');
const scanSite = analyzeWebsite;

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 15 * 1024 * 1024, files: 1 },
});

const app = express();
const PORT = process.env.PORT || 3000;
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const OPENAI_MODEL = process.env.OPENAI_MODEL || 'gpt-4o-mini';
/* Where the "Schedule a review" button sends people. Set BOOKING_URL in Render
   to your scheduling link; the fallback is the Smart 1 contact page. */
const BOOKING_URL = process.env.BOOKING_URL || 'https://smart1marketing.com/contact';

app.set('trust proxy', 1);
app.use(express.json({ limit: '2mb' }));

/* Embedding: by default any site may iframe the audit. Set EMBED_ALLOWED_ORIGINS
   to a space-separated list (e.g. "https://smart1marketing.com https://www.smart1marketing.com")
   to restrict it to your own domains. */
const FRAME_ANCESTORS = process.env.EMBED_ALLOWED_ORIGINS || '*';
app.use((req, res, next) => {
  res.setHeader('Content-Security-Policy', `frame-ancestors ${FRAME_ANCESTORS}`);
  next();
});

app.use(express.static(path.join(__dirname, 'public'), { maxAge: '1h' }));

/* ---------------------------------------------------------------
   Simple in-memory rate limit: 15 analyses per IP per hour.
   Resets on redeploy — fine for a lead-gen tool.
---------------------------------------------------------------- */
const hits = new Map();
function rateLimited(ip, max = 15, windowMs = 60 * 60 * 1000) {
  const now = Date.now();
  const list = (hits.get(ip) || []).filter((t) => now - t < windowMs);
  list.push(now);
  hits.set(ip, list);
  if (hits.size > 5000) hits.clear();
  return list.length > max;
}

/* ---------------------------------------------------------------
   Prompt construction
---------------------------------------------------------------- */
const SYSTEM_PROMPT = `You are a senior marketing performance analyst at Smart 1 Marketing, a full-service digital agency with 20+ years of experience serving 4,000+ businesses.

You are writing the findings section of a Marketing Efficiency Audit™ that an accounting or bookkeeping partner will review with their client. Your reader is financially literate but NOT a marketer. Write like a CFO advisor: concrete, numbers-first, plain English, no jargon and no hype.

Rules:
- Reference the client's own numbers directly. Never invent data that was not supplied.
- When a metric is missing or zero, say what it prevents you from knowing instead of guessing.
- Frame everything as "may", "appears", "suggests" — this is a directional assessment, not an audit opinion.
- Estimated dollar impacts must be arithmetically traceable to the supplied inputs. Show the math in one short clause.
- No emojis. No exclamation points. No "unlock", "supercharge", "game-changer", "in today's landscape".
- Recommended next steps must be specific actions the client can take with their own marketing. Do not include "schedule a review", "book a consultation", or any sales step — the report carries its own call to action at the end, and repeating it inside the findings reads as a pitch.
- Use the buying model, seasonality, consistency, lead-service purchasing, training, vendor list, and business context where they change the reading of the numbers. Inconsistent monthly spend, purchased leads, untrained in-house staff, and a long unnamed vendor list are each worth a finding when present.
- Where business context is supplied (leadership change, lost account, new location), say plainly how it should change the interpretation rather than ignoring it.
- Compare the client's spend to the industry midpoint at their revenue, in dollars, at least once.
- Two growth scenarios are supplied (+15% and +25% lead volume). Reference both when they are calculable, so the client sees a realistic near-term figure and a stretch figure.
- Where a warning sign was answered "unsure", treat the uncertainty itself as the finding. Do not describe it as a yes or a no.
- If capacity is "No — already at capacity", do NOT recommend growing lead volume. Frame the opportunity as pricing, efficiency, cost per customer, and mix instead, and say why. If capacity allows growth, the growth scenarios apply as usual.
- If revenue is mostly repeat customers, note that marketing's real job is replacement rate and reactivation, not volume, and judge the spend accordingly.
- If a Google rating and review count are supplied, weigh them: in local services a sub-4.0 rating or thin review count often suppresses lead flow more than any budget decision. A strong rating that is not displayed on the website is a finding.
- If top services are named, connect at least one finding or next step to them specifically — for example whether the highest-revenue service is visible on the website and in the spend.
- In next steps, be honest about the build-vs-hire fork once: these fixes can be handled in-house given someone with the hours and analytics experience, or by a single accountable partner. Never name Smart 1 in that sentence.
- ROI is deliberately conservative first-month math: (new customers times average sale, minus spend) divided by spend. If lifetime value is meaningfully higher than average sale, you may note once that the true return including repeat purchases is higher than the ROI figure shown, without recalculating it.
- If the target market is provided, note at least one implication for channel choice or targeting. If it is not provided, say what you cannot assess without it.
- When savings figures are supplied above, state the annual figure at least once and frame it as profit that either stays in the business or buys more of what already works. Always call the 20% and 25% rates typical or illustrative, never a quote or a guarantee.
- Where multiple digital vendors are in use, explain why a single consolidated view of spend, leads, and closed sales would change what the client can optimize. Be concrete about what breaks without it: duplicate billing for the same customer, last-click credit misallocating budget, overlapping audiences, and no accountable owner of cost per acquired customer.
- Treat in-house marketing payroll as part of the true cost of acquisition. If headcount cost is supplied, state the fully loaded monthly figure at least once.
- Asset ownership, lead response time, and CRM tracking each deserve a finding when the answer is a risk: a vendor owning the domain or ad accounts, a response time of a day or more, or no tracking from lead to sale.
- Where the audience estimate exists, use it for scale, never as precision. Say "roughly" and never quote it to more than two significant figures.
- Never state a competitor's spend, traffic, or performance. You have no data on them. You may only suggest what the partner should look into.

Return ONLY valid JSON matching this shape:
{
  "headline": "one sentence, under 15 words, the single most important finding",
  "executiveSummary": "2-3 sentences a CPA could read aloud to their client",
  "findings": [{"title": "short label", "detail": "2-3 sentences using their numbers", "severity": "high|medium|low"}],
  "leaks": [{"area": "where money may be leaking", "estimatedMonthlyImpact": "$X,XXX or 'Unknown — not tracked'", "why": "one sentence"}],
  "questionsToAsk": ["question the partner should ask the client in their next meeting"],
  "nextSteps": ["specific action, most valuable first"],
  "partnerTalkingPoint": "one sentence the accounting partner can say to open the conversation"
}
Include 3-5 findings, 2-4 leaks, 4-6 questions, 3-4 next steps.`;

function money(n) {
  if (n === null || n === undefined || !isFinite(n)) return 'not provided';
  return '$' + Math.round(n).toLocaleString('en-US');
}

function buildUserPrompt(p = {}) {
  const s = p.snapshot || {};
  const m = p.metrics || {};
  const b = p.benchmark || {};
  const pr = p.profile || {};
  const mk = p.market || {};
  const cp = p.competition || {};
  const wb = p.website || null;
  const aud = estimateAudience({
    population: p.marketData?.population,
    audienceType: mk.audienceType, ageRanges: mk.ageRanges,
    incomeBand: mk.incomeBand, genderSkew: mk.genderSkew, homeownersOnly: mk.homeownersOnly,
  });
  const flagsYes = (p.flags || []).filter((f) => f.response === 'yes').map((f) => `- ${f.label}`);
  const flagsUnsure = (p.flags || []).filter((f) => f.response === 'unsure').map((f) => `- ${f.label}`);
  const flagsNo = (p.flags || []).filter((f) => f.response === 'no').map((f) => `- ${f.label}`);
  const spendLines = Object.entries(p.spend || {})
    .filter(([, v]) => Number(v) > 0)
    .map(([k, v]) => `- ${k}: ${money(Number(v))}/mo`);

  return `CLIENT SNAPSHOT
Business: ${s.clientName || 'Not provided'}
Industry: ${s.industry || 'Not provided'}
Annual revenue: ${money(s.annualRevenue)}
Locations: ${s.locations || 'Not provided'}
ZIP code: ${s.zipCode || 'not provided'}
Primary market: ${s.cityMarket || 'not provided'}
Website: ${s.website || 'not provided'}
Marketing vendors in use: ${s.vendors || 'Not provided'}

MONTHLY MARKETING INVESTMENT (total ${money(m.spend)})
${spendLines.length ? spendLines.join('\n') : '- Not itemized'}

CALCULATED PERFORMANCE
Monthly leads: ${m.leads ?? 'not provided'}
New customers per month: ${m.customers ?? 'not provided'}
Close rate: ${m.closeRate != null ? m.closeRate.toFixed(1) + '%' : 'not provided'}
Cost per lead: ${money(m.cpl)}
Customer acquisition cost: ${money(m.cac)}
Average sale: ${money(m.avgSale)}
Customer lifetime value: ${money(m.clv)}
Estimated monthly revenue from marketing: ${money(m.revenue)}
Marketing ROI: ${m.roi != null ? Math.round(m.roi) + '%' : 'not calculable'}
Marketing spend as % of revenue: ${m.spendPct != null ? m.spendPct.toFixed(1) + '%' : 'not calculable'}
Growth scenarios (same close rate, no change to average sale):
${(m.scenarios || []).map((x) => `- at +${x.liftPct}% lead volume: ${money(x.annual)} additional annual revenue (${money(x.monthly)}/month, about ${x.addCustomers.toFixed(1)} more customers a month)`).join('\n') || '- not calculable'}

INDUSTRY BENCHMARKS (${s.industry || 'general'})
Typical marketing budget: ${b.budgetLo ?? '?'}%–${b.budgetHi ?? '?'}% of revenue → client is at ${m.spendPct != null ? m.spendPct.toFixed(1) + '%' : 'unknown'} (${b.spendVerdict || 'unknown'})
Typical cost per lead: ${b.cplLo != null ? money(b.cplLo) + '–' + money(b.cplHi) : 'no published range'} → client is at ${money(m.cpl)} (${b.cplVerdict || 'unknown'})

HOW THEY BUY MARKETING
Digital spend over $2,500/mo: ${pr.digitalOver2500 || 'not answered'}
Traditional spend over $2,500/mo: ${pr.traditionalOver2500 || 'not answered'}
Buys lead services: ${pr.buysLeadServices || 'not answered'}
Buying model: ${pr.buyingModel || 'not answered'}
Trains in-house marketing staff: ${pr.providesTraining || 'not applicable'}
Traditional media bought: ${(pr.traditionalMedia || []).join(', ') || 'none reported'}${pr.traditionalOther ? `, ${pr.traditionalOther}` : ''}
Digital vendors named: ${pr.digitalVendors || 'none named'}
Seasonal pushes: ${pr.seasonalMarketing || 'not answered'}${pr.seasonDetail ? ` — ${pr.seasonDetail}` : ''}
Monthly consistency: ${pr.monthlyConsistency || 'not answered'}
Expense document supplied: ${p.expenses ? `${p.expenses.filename} (${p.expenses.mode === 'ai' ? 'read automatically' : 'saved for analyst review'})${p.expenses.period ? `, covering ${p.expenses.period}` : ''}` : 'none'}

IN-HOUSE TEAM, EVENTS, AND OPERATIONS
Marketing employees: ${pr.marketingHeadcount || 'not provided'} at ${pr.marketingPayroll ? money(pr.marketingPayroll) + '/month in wages and benefits' : 'cost not provided'}
Live events: ${pr.liveEvents || 'not answered'}${pr.eventsDetail ? ` — ${pr.eventsDetail}` : ''}${pr.eventsCost ? `, about ${money(pr.eventsCost)} a year` : ''}
Who owns the website, domain, and ad accounts: ${pr.assetOwnership || 'not answered'}
Lead response time: ${pr.leadResponseTime || 'not answered'}
Tracks leads to closed sale: ${pr.crmTracking || 'not answered'}

COMPETITION
Confirmed competitors: ${(cp.competitors || []).map((c) => `${c.name || 'unnamed'}${c.website ? ` (${c.website})` : ''}`).join('; ') || 'none confirmed'}
Head-to-head site comparisons: ${(cp.competitors || []).filter((c) => c.comparison?.comparison).map((c) => `${c.name}: ${c.comparison.comparison.verdict || ''}`).join(' | ') || 'none completed'}
Stated differentiation: ${cp.differentiation || 'none stated'}
Losing work to: ${cp.losingTo || 'not stated'}

WEBSITE SCAN
${wb ? `${wb.finalUrl} — ${wb.conversionPoints} conversion point(s): ${wb.counts.forms} form(s), ${wb.counts.telLinks} click-to-call link(s)
Tracking detected: ${(wb.trackers || []).join(', ') || 'none'}
Not found: ${(wb.missing || []).join('; ') || 'nothing'}
${wb.analysis?.summary ? `Reviewer summary: ${wb.analysis.summary}` : ''}` : (s.website ? `${s.website} was supplied but not scanned.` : 'No website supplied.')}

ESTIMATED REACHABLE AUDIENCE
${aud ? `Service area${p.marketData?.areaName ? ` (${p.marketData.areaName})` : ''} population ${aud.population.toLocaleString('en-US')} (${p.marketData?.confidence || 'estimated'} confidence: ${p.marketData?.basis || 'estimated'})
${p.marketData?.demographicNote ? `Area character: ${p.marketData.demographicNote}` : ''}
${p.marketData?.medianHouseholdIncome ? `Median household income: ${money(p.marketData.medianHouseholdIncome)}` : ''}
Estimated reachable primary audience: ${aud.primary.toLocaleString('en-US')} (working range ${aud.low.toLocaleString('en-US')}–${aud.high.toLocaleString('en-US')})
This is a directional estimate from national averages, not local census data. Treat it as an order of magnitude.` : 'Not estimated — service-area population was not supplied.'}

TARGET MARKET
Sells to: ${mk.audienceType || 'not provided'}
Service area: ${mk.serviceRadius || 'not provided'}
Primary age ranges: ${(mk.ageRanges || []).join(', ') || 'not provided'}
Household income: ${mk.incomeBand || 'not provided'}
Gender skew: ${mk.genderSkew || 'not provided'}
Audience notes: ${mk.audienceNotes || 'none'}

BUSINESS CONTEXT FROM THE PARTNER
${mk.contextNotes || 'None supplied.'}

ADDITIONAL SIGNALS (optional questions)
Revenue mix: ${p.context2?.repeatShare || 'not answered'}
Capacity for more leads: ${p.context2?.capacity || 'not answered'}
Google rating: ${p.context2?.gRating || 'not answered'}${p.context2?.gReviews ? `, ${p.context2.gReviews} reviews` : ''}
Top services by revenue: ${p.context2?.topServices || 'not provided'}

POSSIBLE SAVINGS (illustrative rates, already calculated)
${p.savings && p.savings.monthly > 0 ? `Consolidating digital vendors: ${money(p.savings.consolidate)}/month (20% of ${money(p.savings.digital)} digital spend, ${p.savings.vendorCount} vendors)
Removing traditional/digital overlap: ${money(p.savings.overlap)}/month (25% of ${money(p.savings.traditional)} traditional spend)
Combined: ${money(p.savings.monthly)}/month, ${money(p.savings.annual)}/year` : 'No consolidation savings apply — too few vendors or no overlapping spend.'}

INDUSTRY CONTEXT
Known patterns in this industry:
${(b.industryFacts || []).map((f) => `- ${f}`).join('\n') || '- none supplied'}
Typical channel mix for ${s.industry || 'this industry'}: ${b.mixDigital ?? '?'}% digital / ${b.mixTraditional ?? '?'}% traditional
Industry midpoint budget: ${b.budgetMid != null ? b.budgetMid.toFixed(1) + '% of revenue' : 'unknown'}${s.annualRevenue && b.budgetMid ? ` — about ${money((s.annualRevenue * b.budgetMid) / 100 / 12)}/month at this revenue` : ''}
Industry note: ${b.industryNote || 'none'}

PROFIT LEAK WARNING SIGNS — tier: ${p.leakTier || 'unknown'}
Present (answered yes):
${flagsYes.join('\n') || '- None'}
Unknown (answered unsure — the client could not say):
${flagsUnsure.join('\n') || '- None'}
Not present (answered no):
${flagsNo.join('\n') || '- None'}

MARKETING EFFICIENCY SCORE™: ${p.score ?? '?'}/100 (${p.scoreTier || ''})

Write the findings for this client.`;
}

/* ---------------------------------------------------------------
   Fallback narrative — keeps the app useful with no API key
---------------------------------------------------------------- */
function fallbackAnalysis(p = {}) {
  const m = p.metrics || {};
  const b = p.benchmark || {};
  const findings = [];

  if (m.cpl != null) {
    findings.push({
      title: 'Cost per lead',
      detail: `At ${money(m.spend)} per month generating ${m.leads} leads, cost per lead is ${money(m.cpl)}. ${
        b.cplLo != null ? `The published range for this industry is ${money(b.cplLo)}–${money(b.cplHi)}.` : 'No published range applies to this industry.'
      }`,
      severity: b.cplVerdict === 'above range' ? 'high' : 'low',
    });
  }
  if (m.spendPct != null) {
    findings.push({
      title: 'Spend relative to revenue',
      detail: `Annualized marketing investment is ${m.spendPct.toFixed(1)}% of revenue against a typical range of ${b.budgetLo}%–${b.budgetHi}%. This is ${b.spendVerdict}.`,
      severity: b.spendVerdict === 'above range' ? 'high' : 'medium',
    });
  }
  if (m.roi != null) {
    findings.push({
      title: 'Return on marketing investment',
      detail: `Estimated revenue attributed to marketing is ${money(m.revenue)} per month against ${money(m.spend)} in spend, a return of ${Math.round(m.roi)}%.`,
      severity: m.roi < 100 ? 'high' : 'low',
    });
  }
  const flagged = (p.flags || []).filter((f) => f.answer).length;
  const unsure = (p.flags || []).filter((f) => f.response === 'unsure').length;
  findings.push({
    title: 'Measurement gaps',
    detail: `${flagged} of ${(p.flags || []).length || 10} warning signs are present${unsure ? `, ${unsure} of them because the answer is not known` : ''}, placing this business in the "${p.leakTier}" tier. Gaps like these make it difficult to know which spend is producing customers.`,
    severity: (p.leakPoints || 0) > 12 ? 'high' : 'medium',
  });

  return {
    headline: `Marketing Efficiency Score of ${p.score}/100 — ${p.scoreTier}.`,
    executiveSummary: `Based on the inputs provided, this business invests ${money(m.spend)} per month in marketing and shows a Marketing Efficiency Score of ${p.score} out of 100. ${
      m.opportunityAnnual ? `A ${m.liftPct}% improvement in lead volume at the current close rate would represent roughly ${money(m.opportunityAnnual)} in additional annual revenue.` : ''
    } A detailed review is recommended to confirm these directional findings.`,
    findings,
    leaks: [
      { area: 'Untracked conversions', estimatedMonthlyImpact: 'Unknown — not tracked', why: 'Spend cannot be attributed to revenue without conversion tracking.' },
      { area: 'Multiple vendors and overlapping tools', estimatedMonthlyImpact: money((m.spend || 0) * 0.1), why: 'Duplicate platforms and fragmented reporting commonly account for 10% or more of spend.' },
    ],
    questionsToAsk: [
      'How do you currently determine whether marketing is working?',
      'What does one new customer cost you today?',
      'Which channel produced your last ten customers?',
      'When was your marketing strategy last reviewed?',
    ],
    nextSteps: [
      'Confirm lead and conversion tracking is in place before changing any budget.',
      'Consolidate reporting into a single monthly view of spend, leads, and customers.',
      'Establish a cost per acquired customer for each channel before shifting any budget.',
    ],
    partnerTalkingPoint: `Your marketing line is ${money((m.spend || 0) * 12)} a year — let's find out what it's producing.`,
    _fallback: true,
  };
}

/* ---------------------------------------------------------------
   Routes
---------------------------------------------------------------- */
app.get('/api/config', (req, res) => {
  res.json({ bookingUrl: BOOKING_URL });
});

app.get('/api/health', (req, res) => {
  res.json({
    ok: true,
    model: OPENAI_MODEL,
    aiEnabled: Boolean(OPENAI_API_KEY),
    pdfEnabled: true,
    pdfStorage: cloudinary.isConfigured() ? 'cloudinary' : 'local-disk',
    // Lead delivery now goes through the Hub's own /api/leads/capture (see
    // hubLeads.js) rather than a GoHighLevel client or a generic webhook
    // this tool held credentials for itself.
    hubLeads: hubLeads.isConfigured(),
  });
});

app.post('/api/analyze', async (req, res) => {
  const payload = req.body || {};

  if (rateLimited(req.ip)) {
    return res.status(429).json({ error: 'Too many audits from this connection. Try again in an hour.' });
  }

  if (!OPENAI_API_KEY) {
    return res.json({ analysis: fallbackAnalysis(payload), source: 'fallback' });
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);

    const r = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${OPENAI_API_KEY}`,
      },
      body: JSON.stringify({
        model: OPENAI_MODEL,
        temperature: 0.4,
        max_tokens: 1600,
        response_format: { type: 'json_object' },
        messages: [
          { role: 'system', content: SYSTEM_PROMPT },
          { role: 'user', content: buildUserPrompt(payload) },
        ],
      }),
    });
    clearTimeout(timeout);

    if (!r.ok) {
      const text = await r.text();
      console.error('OpenAI error', r.status, text.slice(0, 500));
      return res.json({ analysis: fallbackAnalysis(payload), source: 'fallback', note: 'AI service unavailable' });
    }

    const data = await r.json();
    const raw = data.choices?.[0]?.message?.content || '{}';
    let analysis;
    try {
      analysis = JSON.parse(raw);
    } catch {
      analysis = fallbackAnalysis(payload);
    }
    res.json({ analysis, source: 'openai', model: OPENAI_MODEL });
  } catch (err) {
    console.error('analyze failed:', err.message);
    res.json({ analysis: fallbackAnalysis(payload), source: 'fallback', note: 'AI service unavailable' });
  }
});

/* ---------------------------------------------------------------
   Lead delivery: the Hub, and only the Hub.

   The standalone tool fired a lead twice -- "started" at the results gate,
   "completed" once the PDF existed -- and dedupe was left to whoever set up
   the GHL workflow, matching on lead_id or email. hub/leads.py's public
   capture route has no such matching: every call creates a new row, and it
   refuses a submission with neither an email nor a phone (the "started"
   stage never had either at that point). So there is one call now, made
   once the PDF exists and the full picture -- contact, business, score, and
   where the report lives -- can go in a single row. The gate submission
   (`/api/lead` in the old build) no longer posts anywhere by itself; the
   browser still asks for it so the button can disable itself immediately,
   and the delivery happens moments later from /api/report, automatically,
   without the visitor doing anything else.
---------------------------------------------------------------- */
app.post('/api/lead', (req, res) => {
  // Acknowledged, not delivered. See the block comment above: the one
  // delivery to the Hub happens from /api/report, once the PDF exists.
  res.json({ ok: true });
});

async function deliverLead(body, pdfUrl) {
  const snap = body.client || body.snapshot || {};
  const fields = {
    name: body.name || '',
    email: body.email || '',
    phone: body.phone || '',
    company: body.firm || body.partnerFirm || '',
    partner_name: body.partnerName || snap.preparedBy || body.name || '',
    partner_firm: body.partnerFirm || snap.partnerFirm || body.firm || '',
    client_business: snap.clientName || '',
    client_industry: snap.industry || '',
    client_annual_revenue: snap.annualRevenue ?? null,
    website: snap.website || body.website || '',
    zip_code: snap.zipCode || body.zipCode || '',
    city_market: snap.cityMarket || body.cityMarket || '',
    locations: snap.locations ?? null,
    monthly_marketing_spend: body.monthlySpend ?? body.metrics?.spend ?? null,
    efficiency_score: body.score ?? null,
    score_tier: body.scoreTier || '',
    leak_points: body.leakPoints ?? null,
    leak_tier: body.leakTier || '',
  };
  const meta = {
    lead_id: body.lead_id || '',
    last_screen: body.lastScreen || '',
  };
  console.log('LEAD:', JSON.stringify({ fields, pdfUrl }));

  try {
    const result = await hubLeads.captureLead({
      page: 'accounting-partner-audit',
      fields,
      pdfUrl,
      client: snap.clientName || '',
      meta,
    });
    console.log('HUB LEAD:', JSON.stringify(result));
    return result;
  } catch (err) {
    console.error('hub lead delivery failed:', err.message);
    return { ok: false, error: err.message };
  }
}

/* ---------------------------------------------------------------
   PDF report: build, store, hand back a link, push to GHL
---------------------------------------------------------------- */
const PDF_DIR = process.env.PDF_DIR || path.join(os.tmpdir(), 'smart1-audits');
const PDF_TTL_HOURS = Number(process.env.PDF_TTL_HOURS || 720); // 30 days
fs.mkdirSync(PDF_DIR, { recursive: true });

function baseUrl(req) {
  if (process.env.PUBLIC_BASE_URL) return process.env.PUBLIC_BASE_URL.replace(/\/$/, '');
  const proto = req.headers['x-forwarded-proto'] || req.protocol || 'https';
  return `${proto}://${req.headers.host}`;
}

function slug(text, fallback) {
  const s = String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return s.slice(0, 40) || fallback;
}

/** Delete expired reports so an instance with a mounted disk doesn't grow forever. */
function sweepPdfs() {
  try {
    const cutoff = Date.now() - PDF_TTL_HOURS * 3600 * 1000;
    for (const f of fs.readdirSync(PDF_DIR)) {
      const p = path.join(PDF_DIR, f);
      if (fs.statSync(p).mtimeMs < cutoff) fs.unlinkSync(p);
    }
  } catch (err) { console.error('pdf sweep failed:', err.message); }
}
setInterval(sweepPdfs, 6 * 3600 * 1000).unref();
sweepPdfs();

app.post('/api/report', async (req, res) => {
  const payload = req.body || {};
  try {
    if (!payload.audience && payload.marketData?.population) {
      payload.audience = estimateAudience({
        population: payload.marketData.population,
        audienceType: payload.market?.audienceType,
        ageRanges: payload.market?.ageRanges,
        incomeBand: payload.market?.incomeBand,
        genderSkew: payload.market?.genderSkew,
        homeownersOnly: payload.market?.homeownersOnly,
      });
    }
    const buf = await buildAuditPdf({ ...payload, bookingUrl: BOOKING_URL });
    const id = crypto.randomBytes(12).toString('hex');
    const name = `marketing-efficiency-audit-${slug(payload.snapshot?.clientName, 'client')}-${id}.pdf`;

    let url = null;
    let storage = 'local';

    if (cloudinary.isConfigured()) {
      try {
        const up = await cloudinary.uploadPdf(buf, name);
        url = up.url;
        storage = 'cloudinary';
      } catch (err) {
        console.error('cloudinary upload failed, falling back to local disk:', err.message);
      }
    }

    if (!url) {
      fs.writeFileSync(path.join(PDF_DIR, name), buf);
      url = `${baseUrl(req)}/audit/${name}`;
    }

    deliverLead({ ...(payload.lead || {}), ...payload, client: payload.snapshot }, url)
      .catch((e) => console.error('lead delivery failed:', e.message));

    res.json({ ok: true, url, filename: name, bytes: buf.length, storage });
  } catch (err) {
    console.error('pdf build failed:', err);
    res.status(500).json({ error: 'Report could not be generated.' });
  }
});

/* ---------------------------------------------------------------
   Market sizing — always returns a population, AI or fallback
---------------------------------------------------------------- */
app.post('/api/market', async (req, res) => {
  const context = req.body?.context || {};
  if (rateLimited(req.ip, 30)) return res.status(429).json({ error: 'Too many requests. Try again later.' });
  const market = await estimateMarket({ context, apiKey: OPENAI_API_KEY, model: OPENAI_MODEL });
  res.json({ ok: true, market });
});

/* ---------------------------------------------------------------
   Competitor discovery
---------------------------------------------------------------- */
app.post('/api/competitors', async (req, res) => {
  const context = req.body?.context || {};
  if (rateLimited(req.ip, 25)) return res.status(429).json({ error: 'Too many requests. Try again later.' });
  const out = await suggestCompetitors({ context, apiKey: OPENAI_API_KEY, model: OPENAI_MODEL });
  res.json({ ok: true, ...out });
});

/* ---------------------------------------------------------------
   Head-to-head: scan a competitor site and compare it to the client's
---------------------------------------------------------------- */
app.post('/api/compare', async (req, res) => {
  const { clientScan, competitor } = req.body || {};
  if (!competitor?.website) return res.status(400).json({ error: 'No competitor website supplied.' });
  if (rateLimited(req.ip, 40)) return res.status(429).json({ error: 'Too many requests. Try again later.' });

  try {
    const scanned = await scanSite({ url: competitor.website, context: {}, apiKey: null });
    const comparison = await compareSites({
      clientScan, competitorName: competitor.name || competitor.website,
      competitorScan: scanned.scan, apiKey: OPENAI_API_KEY, model: OPENAI_MODEL,
    });
    res.json({
      ok: true,
      name: competitor.name,
      website: competitor.website,
      scan: {
        finalUrl: scanned.scan.finalUrl,
        conversionPoints: scanned.scan.conversionPoints,
        counts: scanned.scan.counts,
        trackers: scanned.scan.trackers,
        booking: scanned.scan.booking,
        chat: scanned.scan.chat,
        flags: scanned.scan.flags,
      },
      comparison,
    });
  } catch (err) {
    res.json({ ok: false, name: competitor.name, website: competitor.website, error: err.message });
  }
});

/* ---------------------------------------------------------------
   Website conversion scan
---------------------------------------------------------------- */
app.post('/api/website', async (req, res) => {
  const { url, context } = req.body || {};
  if (!url || typeof url !== 'string') return res.status(400).json({ error: 'No website address supplied.' });
  if (rateLimited(req.ip, 25)) {
    return res.status(429).json({ error: 'Too many scans from this connection. Try again in an hour.' });
  }
  try {
    const result = await analyzeWebsite({
      url, context: context || {}, apiKey: OPENAI_API_KEY, model: OPENAI_MODEL,
    });
    res.json({ ok: true, ...result });
  } catch (err) {
    console.error('website scan failed:', err.message);
    res.status(400).json({ error: err.message || 'That website could not be scanned.' });
  }
});

/* ---------------------------------------------------------------
   Marketing expense upload: store on Cloudinary, optionally AI-categorize
---------------------------------------------------------------- */
app.post('/api/expenses', upload.single('file'), async (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).json({ error: 'No file was received.' });
  if (!isAccepted(file.originalname)) {
    return res.status(400).json({ error: `Unsupported file type. Accepted: ${ACCEPTED.join(', ')}.` });
  }
  if (rateLimited(req.ip, 20)) {
    return res.status(429).json({ error: 'Too many uploads from this connection. Try again in an hour.' });
  }

  let context = {};
  try { context = JSON.parse(req.body.context || '{}'); } catch { /* ignore */ }
  const wantsAi = String(req.body.mode || 'review') === 'ai';

  const result = { ok: true, filename: file.originalname, bytes: file.size, storage: null, url: null, analysis: null };

  // Always keep a copy so a human can review it later
  if (cloudinary.isConfigured()) {
    try {
      const safe = file.originalname.replace(/[^a-zA-Z0-9._-]/g, '-');
      const up = await cloudinary.uploadExpenseDoc(
        file.buffer,
        `expenses-${crypto.randomBytes(6).toString('hex')}-${safe}`,
        file.mimetype || 'application/octet-stream'
      );
      result.url = up.url;
      result.storage = 'cloudinary';
    } catch (err) {
      console.error('expense upload to cloudinary failed:', err.message);
    }
  }
  if (!result.url) {
    try {
      const safe = file.originalname.replace(/[^a-zA-Z0-9._-]/g, '-');
      const name = `expenses-${crypto.randomBytes(6).toString('hex')}-${safe}`;
      fs.writeFileSync(path.join(PDF_DIR, name), file.buffer);
      result.url = `${baseUrl(req)}/audit/${name}`;
      result.storage = 'local';
    } catch (err) {
      console.error('expense local write failed:', err.message);
    }
  }

  if (wantsAi) {
    if (!OPENAI_API_KEY) {
      result.analysisError = 'AI evaluation is not available right now. The file was saved for manual review.';
    } else {
      try {
        result.analysis = await analyzeExpenses({
          buffer: file.buffer,
          filename: file.originalname,
          context,
          apiKey: OPENAI_API_KEY,
          model: OPENAI_MODEL,
        });
      } catch (err) {
        console.error('expense analysis failed:', err.message);
        result.analysisError = err.message.startsWith('No readable text')
          ? err.message
          : 'The file could not be read automatically. It has been saved for manual review.';
      }
    }
  }

  res.json(result);
});

app.get('/sample-report.pdf', (req, res) => {
  const sample = path.join(__dirname, 'public', 'sample-report.pdf');
  if (!fs.existsSync(sample)) return res.status(404).send('Sample report not yet generated.');
  res.setHeader('Content-Type', 'application/pdf');
  res.sendFile(sample);
});

app.get('/audit/:file', (req, res) => {
  const file = req.params.file;
  if (!/^[a-zA-Z0-9._-]+\.pdf$/.test(file)) return res.status(400).send('Bad request');
  const full = path.join(PDF_DIR, file);
  if (!fs.existsSync(full)) {
    return res.status(404).send('This report link has expired. Run the audit again to generate a new copy.');
  }
  res.setHeader('Content-Type', 'application/pdf');
  res.setHeader('Content-Disposition', `attachment; filename="${file}"`);
  fs.createReadStream(full).pipe(res);
});

app.get('*', (req, res) => {
  if (req.path.startsWith('/api/')) return res.status(404).json({ error: 'Not found' });
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, () => console.log(`Marketing Efficiency Audit running on :${PORT}`));
