/**
 * Website conversion scan.
 *
 * Fetches a page, detects conversion and measurement signals deterministically
 * (forms, click-to-call, booking, chat, tracking pixels, and so on), then hands
 * the findings to the model for commentary. The detection is regex over the
 * served HTML, so it sees what is in the initial response — a site that renders
 * everything client-side will under-report, and the result says so.
 */

const dns = require('dns').promises;
const net = require('net');

const ALLOW_LOCAL = process.env.ALLOW_LOCAL_FETCH === '1';
const FETCH_TIMEOUT = 15000;
const MAX_BYTES = 1_500_000;

/* ---------------------------------------------------------------
   URL safety: don't let a submitted URL reach internal services
---------------------------------------------------------------- */
function isPrivateIp(ip) {
  if (net.isIPv4(ip)) {
    const [a, b] = ip.split('.').map(Number);
    return a === 10 || a === 127 || a === 0 || (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) || (a === 169 && b === 254) || a >= 224;
  }
  const low = ip.toLowerCase();
  return low === '::1' || low.startsWith('fc') || low.startsWith('fd') || low.startsWith('fe80');
}

async function safeUrl(raw) {
  let url;
  try {
    url = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`);
  } catch {
    throw new Error('That does not look like a valid website address.');
  }
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Only http and https addresses can be scanned.');
  if (ALLOW_LOCAL) return url;

  const host = url.hostname.toLowerCase();
  if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.internal')) {
    throw new Error('That address cannot be scanned.');
  }
  try {
    const records = await dns.lookup(host, { all: true });
    if (records.some((r) => isPrivateIp(r.address))) throw new Error('That address cannot be scanned.');
  } catch (err) {
    if (err.message.includes('cannot be scanned')) throw err;
    throw new Error('That website could not be found.');
  }
  return url;
}

async function fetchPage(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT);
  try {
    const r = await fetch(url.href, {
      signal: controller.signal,
      redirect: 'follow',
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; Smart1MarketingAudit/1.0; +https://smart1marketing.com)',
        Accept: 'text/html,application/xhtml+xml',
      },
    });
    if (!r.ok) throw new Error(`The site returned ${r.status}.`);
    const type = r.headers.get('content-type') || '';
    if (!/html/i.test(type)) throw new Error('That address did not return a web page.');
    const buf = Buffer.from(await r.arrayBuffer());
    return { html: buf.slice(0, MAX_BYTES).toString('utf8'), finalUrl: r.url || url.href, status: r.status };
  } finally {
    clearTimeout(timer);
  }
}

/* ---------------------------------------------------------------
   Signal detection
---------------------------------------------------------------- */
const TRACKERS = [
  ['Google Analytics 4', /gtag\/js\?id=G-|googletagmanager\.com\/gtag/i],
  ['Google Tag Manager', /googletagmanager\.com\/gtm\.js|GTM-[A-Z0-9]{4,}/],
  ['Google Ads conversion tag', /gtag\(['"]config['"],\s*['"]AW-|googleadservices\.com\/pagead\/conversion/i],
  ['Meta pixel', /connect\.facebook\.net\/[^"']*fbevents\.js|fbq\(/i],
  ['LinkedIn insight tag', /snap\.licdn\.com\/li\.lms-analytics/i],
  ['Microsoft Advertising UET', /bat\.bing\.com\/bat\.js|uetq/i],
  ['TikTok pixel', /analytics\.tiktok\.com/i],
  ['Hotjar or Clarity', /static\.hotjar\.com|clarity\.ms/i],
  ['CallRail or call tracking', /callrail|calltrk\.com|call-tracking/i],
];

const CHAT = [
  ['Live chat or chatbot', /tawk\.to|intercom|drift\.com|livechatinc|zendesk|hubspot.*conversations|podium|birdeye/i],
];

const BOOKING = [
  ['Online booking or scheduling', /calendly|acuityscheduling|housecallpro|servicetitan|schedule[-_]?now|book[-_]?(now|online)|setmore|squareup\.com\/appointments/i],
];

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", apos: "'", nbsp: ' ', ndash: '–', mdash: '—' };
const decode = (t = '') => t.replace(/&(#?\w+);/g, (m, e) => ENTITIES[e] ?? (e[0] === '#' ? String.fromCharCode(+e.slice(1)) : m));

function detect(html, finalUrl) {
  const lower = html.toLowerCase();
  const found = [];
  const missing = [];

  /* Forms */
  const formCount = (html.match(/<form\b/gi) || []).length;
  const emailInputs = (html.match(/type=["']email["']/gi) || []).length;
  const telInputs = (html.match(/type=["']tel["']/gi) || []).length;

  /* Click-to-call and mailto */
  const telLinks = (html.match(/href=["']tel:/gi) || []).length;
  const mailLinks = (html.match(/href=["']mailto:/gi) || []).length;

  /* Visible phone number pattern */
  const phoneText = /\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}/.test(html.replace(/<[^>]+>/g, ' '));

  /* CTA language */
  const ctaWords = ['get a quote', 'free quote', 'request a quote', 'schedule', 'book now', 'contact us',
    'call now', 'get started', 'free estimate', 'request service', 'talk to', 'consultation'];
  const ctas = ctaWords.filter((w) => lower.includes(w));

  /* Structure */
  const h1 = decode((html.match(/<h1[^>]*>([\s\S]{0,160}?)<\/h1>/i) || [, ''])[1].replace(/<[^>]+>/g, '').trim());
  const title = decode((html.match(/<title[^>]*>([\s\S]{0,200}?)<\/title>/i) || [, ''])[1].trim());
  const metaDesc = /<meta[^>]+name=["']description["'][^>]+content=["']([^"']{10,})/i.test(html);
  const viewport = /<meta[^>]+name=["']viewport["']/i.test(html);
  const schema = /application\/ld\+json/i.test(html);
  const https = finalUrl.startsWith('https://');
  const reviews = /(google review|five[- ]star|★|⭐|reviews?\b.{0,20}\d)/i.test(html);

  const matchList = (list) => list.filter(([, re]) => re.test(html)).map(([name]) => name);
  const trackers = matchList(TRACKERS);
  const chat = matchList(CHAT);
  const booking = matchList(BOOKING);

  /* Build the report */
  if (formCount) found.push(`${formCount} form${formCount > 1 ? 's' : ''} on the page`);
  else missing.push('No form found on the home page');

  if (telLinks) found.push(`${telLinks} click-to-call link${telLinks > 1 ? 's' : ''}`);
  else if (phoneText) missing.push('Phone number appears as text but is not a tappable click-to-call link');
  else missing.push('No phone number found on the home page');

  if (mailLinks) found.push('Email link present');
  if (emailInputs || telInputs) found.push('Form captures contact details directly');
  if (booking.length) found.push(booking.join(', '));
  else missing.push('No online booking or scheduling found');
  if (chat.length) found.push(chat.join(', '));
  if (ctas.length) found.push(`Call-to-action language present: ${ctas.slice(0, 4).join(', ')}`);
  else missing.push('No recognizable call-to-action language on the page');
  if (reviews) found.push('Reviews or ratings referenced');
  else missing.push('No reviews or social proof visible on the home page');

  if (trackers.length) found.push(`Tracking detected: ${trackers.join(', ')}`);
  else missing.push('No analytics or advertising tracking detected — conversions cannot be attributed');

  const hasConversionTag = trackers.some((t) => /conversion|Meta pixel|UET|TikTok/i.test(t));
  if (trackers.length && !hasConversionTag) {
    missing.push('Analytics present but no advertising conversion tag detected');
  }

  if (!viewport) missing.push('No mobile viewport tag — the page may not be mobile friendly');
  if (!metaDesc) missing.push('No meta description');
  if (!h1) missing.push('No H1 heading');
  if (!schema) missing.push('No structured data (schema markup)');
  if (!https) missing.push('Site is not served over HTTPS');

  return {
    finalUrl, title, h1,
    counts: { forms: formCount, telLinks, mailLinks, emailInputs, telInputs },
    trackers, chat, booking, ctas,
    flags: { https, viewport, metaDesc, schema, reviews, phoneText },
    found, missing,
    conversionPoints: formCount + telLinks + mailLinks + booking.length + chat.length,
  };
}

/* ---------------------------------------------------------------
   AI commentary
---------------------------------------------------------------- */
const SYSTEM = `You are a conversion analyst at Smart 1 Marketing reviewing a client's website home page for an audit that an accounting partner will discuss with the client.

You are given the results of an automated scan of the served HTML, not a visual review. Work only from those findings.

Rules:
- Be concrete and practical. Name the specific fix, not a principle.
- Rank by revenue impact: a missing conversion tag or a phone number that is not tappable outranks a missing meta description.
- The scan reads only the initial HTML response. If signals are thin, say the site may render client-side and needs a manual look rather than concluding the features are absent.
- Never claim to have assessed design, speed, or copy quality. You did not see the rendered page.
- No hype. Plain English a business owner can act on.

Return ONLY valid JSON:
{
  "summary": "2-3 sentences on the state of conversion readiness",
  "conversionPoints": ["each way a visitor can currently become a lead, as found"],
  "gaps": [{"issue": "what is missing or broken", "impact": "high|medium|low", "fix": "the specific change to make"}],
  "measurementVerdict": "one or two sentences on whether marketing spend on this site could be attributed to leads today",
  "quickWins": ["change that could be made this week"]
}
3-6 gaps, 2-4 quick wins.`;

async function analyzeWebsite({ url, context = {}, apiKey, model = 'gpt-4o-mini' }) {
  const safe = await safeUrl(url);
  const { html, finalUrl, status } = await fetchPage(safe);
  const scan = detect(html, finalUrl);

  if (!apiKey) return { scan, analysis: null, note: 'AI commentary is not configured. The scan findings above are complete.' };

  const prompt = `CLIENT
Business: ${context.clientName || 'not provided'}
Industry: ${context.industry || 'not provided'}
Service area: ${context.serviceArea || 'not provided'}
Monthly marketing spend: ${context.monthlySpend ? '$' + Number(context.monthlySpend).toLocaleString('en-US') : 'not provided'}

SCAN OF ${finalUrl} (HTTP ${status})
Page title: ${scan.title || 'none found'}
H1: ${scan.h1 || 'none found'}
Forms: ${scan.counts.forms} | click-to-call links: ${scan.counts.telLinks} | email links: ${scan.counts.mailLinks}
Booking tools: ${scan.booking.join(', ') || 'none detected'}
Chat tools: ${scan.chat.join(', ') || 'none detected'}
Tracking detected: ${scan.trackers.join(', ') || 'none'}
CTA language found: ${scan.ctas.join(', ') || 'none'}
HTTPS: ${scan.flags.https} | mobile viewport: ${scan.flags.viewport} | meta description: ${scan.flags.metaDesc} | schema: ${scan.flags.schema} | reviews referenced: ${scan.flags.reviews}

WHAT THE SCAN FOUND
${scan.found.map((f) => `- ${f}`).join('\n') || '- nothing'}

WHAT THE SCAN DID NOT FIND
${scan.missing.map((f) => `- ${f}`).join('\n') || '- nothing'}

Write the conversion review.`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60000);
  try {
    const r = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({
        model, temperature: 0.3, max_tokens: 1400,
        response_format: { type: 'json_object' },
        messages: [{ role: 'system', content: SYSTEM }, { role: 'user', content: prompt }],
      }),
    });
    if (!r.ok) throw new Error(`OpenAI ${r.status}`);
    const data = await r.json();
    return { scan, analysis: JSON.parse(data.choices?.[0]?.message?.content || '{}') };
  } catch (err) {
    console.error('website analysis failed:', err.message);
    return { scan, analysis: null, note: 'The scan completed but the written review could not be generated.' };
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { analyzeWebsite, detect, safeUrl };
