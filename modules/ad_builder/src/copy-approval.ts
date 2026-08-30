/**
 * Pre-build copy suggestion + approval.
 *
 * The "Football Starts" problem: nonsense headlines reached a rendered proof
 * because copy was only generated at build time, with nothing between the
 * model's output and 22 finished creatives. The fix is a gate — propose the
 * three core elements (headline, supporting line, CTA), let a human read and
 * edit them, and only then build.
 *
 * This module produces ONE canonical set of copy for approval (not the
 * per-size budgets — those come later at render time, derived from the
 * approved wording). It also runs a cheap sanity check so obviously-broken
 * suggestions ("Football Starts", a support line that repeats the headline,
 * a truncated fragment) are flagged for the person rather than silently
 * shipped.
 */

import type { LandingAnalysis } from './projects';

export interface CopySuggestion {
  headline: string;
  support: string;
  cta: string;
  /**
   * The two fields every ad in this tool has a box for and nothing ever
   * wrote.
   *
   * A build screen offers five copy fields; this answered three, so an
   * operator asking for a draft got most of an ad and typed the rest --
   * usually meaning the offer and the proof point stayed empty, on templates
   * that draw both. Empty is the one outcome the layouts handle worst: an
   * offer band with nothing in it and a proof line that is white space.
   *
   * Both are optional in the ANSWER as well as the type, and deliberately:
   * a business with no offer running should not have one invented for it,
   * and a proof point nobody can substantiate is the single riskiest
   * sentence on a banner. An empty string here means "this page gave me
   * nothing to say", which is a better answer than a plausible one.
   */
  offer?: string;
  /** The proof point -- the reason to believe the headline. */
  trust?: string;
  /** Short note on the angle, shown to the person for context. */
  rationale?: string;
  /** Non-blocking quality flags: things a human should look at. */
  warnings: string[];
  source: 'openai' | 'fallback';
}

export interface SuggestInput {
  business: string;
  promoting: string;
  benefit?: string;
  offer?: string;
  audience?: string;
  geography?: string;
  objective?: string;
  cta?: string;
  landing?: LandingAnalysis;
}

const CTA_VOCAB = [
  'Get Estimate', 'Request Pricing', 'Learn More', 'Shop Now', 'View Inventory',
  'Schedule Now', 'Book Today', 'Call Now', 'Register Now', 'Get Offer',
  'Check Availability', 'Visit Us', 'Get Started', 'See Menu', 'Order Now',
];

/**
 * Quality gate. Returns human-readable warnings for copy that looks broken.
 * Deliberately conservative — these are prompts to look, not hard rejections.
 */
export function critiqueCopy(c: { headline: string; support: string; cta: string; offer?: string }): string[] {
  const w: string[] = [];
  const h = (c.headline ?? '').trim();
  const s = (c.support ?? '').trim();

  if (!h) w.push('The headline is empty.');
  else {
    const words = h.split(/\s+/);
    if (words.length < 2) w.push(`The headline "${h}" is only one word — it may read as a fragment rather than a message.`);
    if (words.length > 9) w.push('The headline is longer than 9 words and will shrink to fit small sizes.');
    if (/[.]{2,}$|â€¦$|…$/.test(h) || /\b(str|stre|adv|inc)$/i.test(h)) {
      w.push(`The headline "${h}" looks truncated. Please check it reads as a complete thought.`);
    }
    // Bare category label with no benefit: short, no verb, no "you/your", no
    // number/offer. "Football Starts", "Roofing Services", "Dental Care" —
    // grammatically fine, but they say nothing a viewer should act on.
    const VERBS = /\b(get|save|find|book|call|shop|see|start|lower|reach|earn|win|join|discover|schedule|request|claim|grow|cut|boost|meet|try|order|visit|register|upgrade|protect|is|are|your|you|now|today|free|off|%|\$)\b/i;
    if (words.length >= 2 && words.length <= 3 && !VERBS.test(h)) {
      w.push(`The headline "${h}" reads as a category label, not a benefit. A headline that tells the viewer what they get ("Lower Your Energy Bill") works far harder.`);
    }
  }

  if (s) {
    if (/[.]{2,}$|â€¦$|…$/.test(s) || /\b(str|stre|adv|inc)$/i.test(s)) {
      w.push(`The supporting line "${s}" looks truncated — the full sentence may have been cut.`);
    }
    if (h && s.toLowerCase().startsWith(h.toLowerCase().slice(0, Math.min(h.length, 12)))) {
      w.push('The supporting line repeats the headline. A second, different point works harder.');
    }
    // A support line that is a bare noun phrase with no verb and no benefit
    // often reads as filler. Flag very short, choppy ones.
    if (s.split(/\s+/).length < 3 && !c.offer) {
      w.push(`The supporting line "${s}" is very short and may not add meaning.`);
    }
  }

  if (c.cta && !CTA_VOCAB.some((v) => v.toLowerCase() === c.cta.toLowerCase())) {
    // Not an error — custom CTAs are fine — but flag unusually long ones.
    if (c.cta.split(/\s+/).length > 3) w.push(`The call-to-action "${c.cta}" is long for a button.`);
  }
  return w;
}

function fallbackSuggestion(input: SuggestInput): CopySuggestion {
  const headline = (input.benefit || input.landing?.suggestedHeadlines?.[0] || input.promoting || input.business)
    .split(/\s+/).slice(0, 7).join(' ');
  const support = (input.offer || input.landing?.summary || input.promoting || '')
    .split(/\s+/).slice(0, 10).join(' ');
  const cta = input.cta && input.cta !== 'Smart 1 should recommend' ? input.cta : 'Learn More';
  const base = { headline, support, cta };
  // With no model in play the offer can only be what somebody typed or what
  // the page analyser literally found on the page, and the proof point has no
  // source at all. Both stay empty rather than being padded out of the
  // summary, for the reason the prompt gives at length: these are the two
  // lines a client has to stand behind.
  const offer = (input.offer || input.landing?.detectedOffer || '').trim();
  return { ...base, offer, trust: '', warnings: critiqueCopy(base),
           source: 'fallback', rationale: 'Assembled from your form answers.' };
}

export async function suggestCopy(
  input: SuggestInput,
  opts: { apiKey?: string; fetchImpl?: typeof fetch; timeoutMs?: number } = {},
): Promise<CopySuggestion> {
  const apiKey = opts.apiKey ?? process.env.OPENAI_API_KEY;
  if (!apiKey) return fallbackSuggestion(input);

  const doFetch = opts.fetchImpl ?? fetch;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 30_000);

  const sys = [
    'You write display-ad copy for local and regional businesses.',
    'You are given what a business\u2019s own web page says. Draft the five lines',
    'an ad carries, from that page and nothing else.',
    'Rules:',
    '- Headline: 3-7 words, a complete, benefit-led thought. Never a bare category label like "Football Starts".',
    '- Supporting line: one short sentence (4-12 words) that adds a NEW point, not a repeat of the headline.',
    '- Offer: the specific thing on the page - a price, a discount, a free first step. 1-6 words.',
    '- Proof: the reason to believe it - years in business, a rating, a license, a count. 1-8 words.',
    '- CTA: 1-3 words, an action the viewer takes.',
    '- Never truncate. Never output fragments ending in "Str", "Adv", etc.',
    '- Plain, concrete, human. No hype, no "Hurry".',
    // Two lines a client has to stand behind if a regulator or a customer
    // asks. A model asked for a proof point will always produce one, and
    // "Trusted by thousands" about a business nobody has counted is the ad
    // that costs an account. So the instruction is to LEAVE IT EMPTY, twice,
    // and the code below keeps whatever it returns without topping it up.
    '- Offer and proof must come from the page. If the page states neither, return "" for that field.',
    '- Never invent a discount, a rating, a review count, a number of years or an award.',
    'Respond ONLY with JSON: {"headline":"","support":"","offer":"","proof":"","cta":"","rationale":""}. No markdown.',
  ].join('\n');

  const user = [
    `Business: ${input.business}`,
    `Promoting: ${input.promoting}`,
    input.benefit ? `Main benefit: ${input.benefit}` : '',
    input.offer ? `Offer: ${input.offer}` : '',
    input.audience ? `Audience: ${input.audience}` : '',
    input.geography ? `Area: ${input.geography}` : '',
    input.objective ? `Goal: ${input.objective}` : '',
    input.cta && input.cta !== 'Smart 1 should recommend' ? `Preferred CTA: ${input.cta}` : '',
    input.landing?.summary ? `Landing page says: ${input.landing.summary}` : '',
    // The analyser already read these off the page. Passing them saves the
    // model guessing at the two fields it is most likely to invent.
    input.landing?.detectedOffer ? `Offer stated on the page: ${input.landing.detectedOffer}` : '',
    input.landing?.detectedCta ? `The page\u2019s own button says: ${input.landing.detectedCta}` : '',
    input.landing?.audience ? `Who the page addresses: ${input.landing.audience}` : '',
    input.landing?.title ? `Page title: ${input.landing.title}` : '',
    input.landing?.url ? `Page: ${input.landing.url}` : '',
  ].filter(Boolean).join('\n');

  try {
    const res = await doFetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({
        model: process.env.OPENAI_COPY_MODEL ?? 'gpt-4o-mini',
        temperature: 0.6,
        messages: [{ role: 'system', content: sys }, { role: 'user', content: user }],
        response_format: { type: 'json_object' },
      }),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`OpenAI ${res.status}`);
    const data: any = await res.json();
    const parsed = JSON.parse(data.choices[0].message.content);
    const base = {
      headline: String(parsed.headline ?? '').trim(),
      support: String(parsed.support ?? '').trim(),
      cta: String(parsed.cta ?? input.cta ?? 'Learn More').trim(),
    };
    // No fallback for these two. Every other field defaults to something
    // sensible when the model omits it; an offer and a proof point that were
    // not on the page have no sensible default, and the empty string is the
    // honest one.
    const extra = {
      offer: String(parsed.offer ?? '').trim(),
      trust: String(parsed.proof ?? parsed.trust ?? '').trim(),
    };
    return { ...base, ...extra, rationale: parsed.rationale,
             warnings: critiqueCopy(base), source: 'openai' };
  } catch {
    return fallbackSuggestion(input);
  } finally {
    clearTimeout(timer);
  }
}
