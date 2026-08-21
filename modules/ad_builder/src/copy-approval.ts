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
  return { ...base, warnings: critiqueCopy(base), source: 'fallback', rationale: 'Assembled from your form answers.' };
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
    'Return ONE headline, ONE supporting line, and ONE call-to-action.',
    'Rules:',
    '- Headline: 3-7 words, a complete, benefit-led thought. Never a bare category label like "Football Starts".',
    '- Supporting line: one short sentence (4-12 words) that adds a NEW point, not a repeat of the headline.',
    '- CTA: 1-3 words, an action the viewer takes.',
    '- Never truncate. Never output fragments ending in "Str", "Adv", etc.',
    '- Plain, concrete, human. No hype, no "Hurry".',
    'Respond ONLY with JSON: {"headline":"","support":"","cta":"","rationale":""}. No markdown.',
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
    return { ...base, rationale: parsed.rationale, warnings: critiqueCopy(base), source: 'openai' };
  } catch {
    return fallbackSuggestion(input);
  } finally {
    clearTimeout(timer);
  }
}
