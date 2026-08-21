/**
 * Copy generation.
 *
 * Until now headlines were the customer's "main benefit" field with the words
 * truncated — a placeholder, not copywriting. This replaces that step and
 * nothing else: it emits the same `copy` structure the renderer already reads,
 * so every downstream check still applies.
 *
 * The model is constrained hard, for three reasons drawn from the creative
 * research and from what actually breaks:
 *
 *   Per size, not per campaign. A 320x50 carries about five words and a
 *   300x600 carries twenty-four. Asking for one headline and shrinking it is
 *   how you get an unreadable mobile banner.
 *
 *   Never invent. Prices, ratings, guarantees and credentials must come from
 *   the brief or the landing page. A fabricated "25-year warranty" is a legal
 *   problem, not a copy problem.
 *
 *   QA is still the authority. Anything the model returns is measured,
 *   wrapped and checked exactly like hand-written copy, and a failure comes
 *   back here as a shorten instruction.
 */

import type { CopySet, CreativeConcept, SizeKey } from './types';
import type { LandingAnalysis } from './projects';
import type { Submission } from './intake';
import { copyFromSubmission } from './intake';

export interface CopyBrief {
  business: string;
  promoting: string;
  benefit?: string;
  offer?: string;
  audience?: string;
  geography?: string;
  objective?: string;
  cta?: string;
  tone?: string;
  /** Wording that must appear exactly, e.g. a franchise or legal phrase. */
  required?: string;
  /** Claims or words that must not appear. */
  prohibited?: string;
  landing?: LandingAnalysis;
}

/** Word budgets per canvas, from the creative research. */
const BUDGET: Record<string, { headline: [number, number]; support: [number, number]; total: number }> = {
  '300x250': { headline: [3, 7], support: [4, 10], total: 18 },
  '336x280': { headline: [3, 7], support: [4, 10], total: 20 },
  '728x90': { headline: [3, 6], support: [3, 6], total: 13 },
  '160x600': { headline: [3, 7], support: [4, 9], total: 18 },
  '300x600': { headline: [4, 9], support: [6, 14], total: 26 },
  '320x50': { headline: [3, 5], support: [0, 0], total: 9 },
  '970x250': { headline: [4, 9], support: [6, 12], total: 22 },
  '414x125': { headline: [3, 5], support: [0, 4], total: 12 },
};

const SIZE_NOTES: Record<string, string> = {
  '300x250': 'Universal rectangle. One visual, one promise, one action.',
  '336x280': 'In-content rectangle. Slightly more room than the 300x250, but do not add copy.',
  '728x90': 'Leaderboard. Reads as one horizontal sentence, not a compressed rectangle.',
  '160x600': 'Narrow skyscraper. Short words only; long words break into ugly line stacks.',
  '300x600': 'Half page. The only size where a second supporting sentence stays readable.',
  '320x50': 'Mobile banner. One idea in about one second. No supporting line at all.',
  '970x250': 'Billboard. Wide and premium. Amazon caps this at 20 words.',
  '414x125': 'Amazon mobile. The platform supplies its own CTA, so do not write one.',
};

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['concepts'],
  properties: {
    concepts: {
      type: 'array', minItems: 2, maxItems: 3,
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['conceptId', 'name', 'angle', 'sizes'],
        properties: {
          conceptId: { type: 'string', enum: ['A', 'B', 'C'] },
          name: { type: 'string' },
          angle: { type: 'string', description: 'The single idea this concept tests, in a few words.' },
          sizes: {
            type: 'object',
            additionalProperties: false,
            required: ['300x250'],
            properties: Object.fromEntries(
              Object.keys(BUDGET).map((s) => [s, {
                type: 'object',
                additionalProperties: false,
                required: ['headline'],
                properties: {
                  headline: { type: 'string' },
                  support: { type: ['string', 'null'] },
                  cta: { type: ['string', 'null'] },
                  offer: { type: ['string', 'null'] },
                  trust: { type: ['string', 'null'] },
                },
              }]),
            ),
          },
        },
      },
    },
  },
} as const;

function systemPrompt(): string {
  return [
    'You are a direct-response copywriter producing display advertising for a marketing agency.',
    '',
    'Hard rules:',
    '- Never state a price, percentage, rating, award, guarantee, warranty or credential that is not in the brief or the landing page summary. If it is not given, do not imply it.',
    '- Write each ad size separately for its own canvas. Never write one headline and shorten it.',
    '- Headlines start with an active verb wherever it reads naturally.',
    '- One idea per ad. Do not stack a benefit, an offer and a proof point into the same small canvas.',
    '- Calls to action name the next step ("Get Estimate", "See Inventory"), not "Click Here" or "Submit".',
    '- Sentence case, not Title Case, except for the CTA.',
    '- No exclamation marks. No "Hurry", "Act now", "Last chance" — Amazon rejects pressure language.',
    '',
    'Each concept must test one distinct angle, so the client learns something from the comparison:',
    'typically a benefit-led idea, an offer-led idea, and an outcome- or audience-led idea.',
    'Concepts must share the same underlying promise; only the angle changes.',
  ].join('\n');
}

function userPrompt(brief: CopyBrief, sizes: SizeKey[]): string {
  const lines: string[] = [
    `Advertiser: ${brief.business}`,
    `Promoting: ${brief.promoting}`,
  ];
  if (brief.benefit) lines.push(`Main benefit stated by the client: ${brief.benefit}`);
  if (brief.offer) lines.push(`Offer: ${brief.offer}`);
  if (brief.audience) lines.push(`Audience: ${brief.audience}`);
  if (brief.geography) lines.push(`Area served: ${brief.geography}`);
  if (brief.objective) lines.push(`Campaign goal: ${brief.objective}`);
  if (brief.cta && !/recommend/i.test(brief.cta)) lines.push(`Preferred call to action: ${brief.cta}`);
  if (brief.tone) lines.push(`Tone: ${brief.tone}`);
  if (brief.required) lines.push(`Wording that must appear exactly: ${brief.required}`);
  if (brief.prohibited) lines.push(`Never use: ${brief.prohibited}`);

  if (brief.landing?.summary) {
    lines.push('', 'What the landing page says (the ad must match this promise):', brief.landing.summary);
    if (brief.landing.detectedOffer) lines.push(`Offer on the page: ${brief.landing.detectedOffer}`);
    if (brief.landing.audience) lines.push(`Audience implied by the page: ${brief.landing.audience}`);
  }

  lines.push('', 'Write copy for exactly these sizes:');
  for (const s of sizes) {
    const b = BUDGET[s];
    if (!b) continue;
    const support = b.support[1] === 0 ? 'no supporting line' : `support ${b.support[0]}-${b.support[1]} words`;
    lines.push(`- ${s}: headline ${b.headline[0]}-${b.headline[1]} words, ${support}, ${b.total} words total maximum. ${SIZE_NOTES[s] ?? ''}`);
  }
  lines.push('', 'Return only the sizes listed. Omit a field rather than padding it.');
  return lines.join('\n');
}

export interface GenerateOptions {
  apiKey?: string;
  model?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
  sizes?: SizeKey[];
}

export interface GeneratedCopy {
  concepts: { conceptId: string; name: string; angle: string; copy: CreativeConcept['copy'] }[];
  source: 'openai' | 'fallback';
  warnings: string[];
}

/** Words that must never appear, per Amazon's guidance and basic taste. */
const BANNED = /\b(hurry|act now|last chance|limited time only|don't miss out)\b/i;

function sanitise(text: string | null | undefined, max: number, warnings: string[], where: string): string | undefined {
  if (!text) return undefined;
  let t = String(text).replace(/\s+/g, ' ').trim().replace(/!+/g, '');
  if (BANNED.test(t)) {
    warnings.push(`Removed pressure language from ${where}: "${t}"`);
    t = t.replace(BANNED, '').replace(/\s{2,}/g, ' ').trim();
  }
  const words = t.split(/\s+/).filter(Boolean);
  if (words.length > max) {
    warnings.push(`Trimmed ${where} from ${words.length} to ${max} words.`);
    t = words.slice(0, max).join(' ');
  }
  return t || undefined;
}

export async function generateCopy(
  brief: CopyBrief,
  submission: Submission,
  opts: GenerateOptions = {},
): Promise<GeneratedCopy> {
  const warnings: string[] = [];
  const sizes = opts.sizes ?? (Object.keys(BUDGET) as SizeKey[]);
  const apiKey = opts.apiKey ?? process.env.OPENAI_API_KEY;

  // Without a key the deterministic writer still produces valid, renderable
  // copy. Worse copy, but the pipeline never stops on a missing integration.
  if (!apiKey) {
    return {
      concepts: [{ conceptId: 'A', name: 'Benefit', angle: 'client-stated benefit', copy: copyFromSubmission(submission) }],
      source: 'fallback',
      warnings: ['No OpenAI key configured, so copy was assembled from the form answers rather than written.'],
    };
  }

  const doFetch = opts.fetchImpl ?? fetch;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 45_000);

  try {
    const res = await doFetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${apiKey}` },
      signal: ctrl.signal,
      body: JSON.stringify({
        model: opts.model ?? process.env.OPENAI_MODEL ?? 'gpt-4o-mini',
        temperature: 0.85,
        messages: [
          { role: 'system', content: systemPrompt() },
          { role: 'user', content: userPrompt(brief, sizes) },
        ],
        response_format: { type: 'json_schema', json_schema: { name: 'ad_copy', strict: true, schema: SCHEMA } },
      }),
    });

    if (!res.ok) {
      const detail = await res.text().catch(() => '');
      return {
        concepts: [{ conceptId: 'A', name: 'Benefit', angle: 'client-stated benefit', copy: copyFromSubmission(submission) }],
        source: 'fallback',
        warnings: [`Copy fell back to the form answers: OpenAI returned ${res.status}${detail ? ` (${detail.slice(0, 120)})` : ''}.`],
      };
    }

    const data = await res.json();
    const parsed = JSON.parse(String(data?.choices?.[0]?.message?.content ?? '{}'));
    const concepts: GeneratedCopy['concepts'] = [];

    for (const c of parsed.concepts ?? []) {
      const copy: CreativeConcept['copy'] = {};
      for (const [size, set] of Object.entries(c.sizes ?? {}) as [SizeKey, any][]) {
        const b = BUDGET[size];
        if (!b) continue;
        const where = `${c.conceptId}/${size}`;
        const entry: Partial<CopySet> = {
          headline: sanitise(set.headline, b.headline[1], warnings, `${where} headline`),
          support: b.support[1] ? sanitise(set.support, b.support[1], warnings, `${where} support`) : '',
          cta: sanitise(set.cta, 3, warnings, `${where} CTA`),
          offer: sanitise(set.offer, 4, warnings, `${where} offer`),
          trust: sanitise(set.trust, 6, warnings, `${where} proof point`),
        };
        for (const k of Object.keys(entry) as (keyof CopySet)[]) {
          if (entry[k] === undefined) delete entry[k];
        }
        copy[size] = entry;
      }
      // The 300x250 is required by the schema, so it is the safe default.
      copy.default = (copy['300x250'] ?? {}) as CopySet;
      concepts.push({ conceptId: c.conceptId, name: c.name, angle: c.angle, copy });
    }

    if (!concepts.length) {
      return {
        concepts: [{ conceptId: 'A', name: 'Benefit', angle: 'client-stated benefit', copy: copyFromSubmission(submission) }],
        source: 'fallback',
        warnings: ['The model returned no usable concepts; fell back to the form answers.'],
      };
    }

    return { concepts, source: 'openai', warnings };
  } catch (e: any) {
    return {
      concepts: [{ conceptId: 'A', name: 'Benefit', angle: 'client-stated benefit', copy: copyFromSubmission(submission) }],
      source: 'fallback',
      warnings: [`Copy fell back to the form answers (${e?.message ?? e}).`],
    };
  } finally {
    clearTimeout(timer);
  }
}
