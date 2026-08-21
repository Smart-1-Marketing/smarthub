/**
 * Landing page analysis.
 *
 * The landing page is the most reliable brief a customer ever gives us: it is
 * what they actually say to buyers, in their own words, already approved. So
 * reading it beats asking them to summarise it a second time in a form field.
 *
 * Two paths, deliberately:
 *
 *   OpenAI, when a key is configured. Structured Outputs constrains the reply
 *   to a schema, so the application gets fields rather than prose it has to
 *   parse.
 *
 *   A heuristic reader when there is no key, or when the call fails. It is
 *   weaker, and says so in `source`, but it means a missing key degrades the
 *   suggestions rather than breaking the intake flow.
 *
 * One rule that matters: the ad's promise has to match the landing page's
 * promise, or the click is wasted and Google penalises the mismatch. Anything
 * this returns is a suggestion for a human to accept, never copy that ships
 * unread.
 */

import type { LandingAnalysis } from './projects';

const UA = 'Mozilla/5.0 (compatible; Smart1AdBuilder/1.0; +https://smart1marketing.com)';

export interface AnalyzeOptions {
  apiKey?: string;
  model?: string;
  timeoutMs?: number;
  /** Skip the network and analyse this HTML instead. Used by tests. */
  html?: string;
  fetchImpl?: typeof fetch;
}

/* ------------------------------------------------------------ extraction */

export interface PageContent {
  title: string;
  description: string;
  headings: string[];
  paragraphs: string[];
  buttons: string[];
  text: string;
}

const strip = (s: string) =>
  s.replace(/\s+/g, ' ').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .trim();

/**
 * Pull the parts of a page that carry the offer. Scripts, styles, nav and
 * footer are dropped first — a footer full of location links otherwise
 * dominates the extracted text and skews every suggestion toward geography.
 */
export function extractContent(html: string): PageContent {
  const cleaned = html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<nav[\s\S]*?<\/nav>/gi, ' ')
    .replace(/<footer[\s\S]*?<\/footer>/gi, ' ');

  const pick = (re: RegExp, limit: number) => {
    const out: string[] = [];
    let m: RegExpExecArray | null;
    const rx = new RegExp(re.source, 'gi');
    while ((m = rx.exec(cleaned)) && out.length < limit) {
      const t = strip(m[1].replace(/<[^>]+>/g, ' '));
      if (t && t.length > 1 && !out.includes(t)) out.push(t);
    }
    return out;
  };

  const title = strip((cleaned.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] ?? '').replace(/<[^>]+>/g, ''));
  const description = strip(
    cleaned.match(/<meta[^>]+name=["']description["'][^>]+content=["']([^"']+)["']/i)?.[1] ??
      cleaned.match(/<meta[^>]+property=["']og:description["'][^>]+content=["']([^"']+)["']/i)?.[1] ??
      '',
  );

  const headings = pick(/<h[12345][^>]*>([\s\S]*?)<\/h[12345]>/, 14);
  const paragraphs = pick(/<p[^>]*>([\s\S]*?)<\/p>/, 20).filter((p) => p.length > 40);
  const buttons = [
    ...pick(/<button[^>]*>([\s\S]*?)<\/button>/, 10),
    ...pick(/<a[^>]+class=["'][^"']*(?:btn|button|cta)[^"']*["'][^>]*>([\s\S]*?)<\/a>/, 10),
  ].filter((b) => b.length < 34);

  const text = [title, description, ...headings, ...paragraphs].join('\n').slice(0, 6000);
  return { title, description, headings, paragraphs, buttons, text };
}

/* -------------------------------------------------------------- heuristic */

const CTA_VOCAB = [
  'Get Estimate', 'Request Pricing', 'Learn More', 'Shop Now', 'View Inventory',
  'Schedule Now', 'Book Today', 'Call Now', 'Register Now', 'Get Offer',
  'Check Availability', 'Visit Us', 'See the Menu',
];

function titleCase(s: string): string {
  return s.replace(/\w\S*/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
}

/** Suggestions without a model. Weaker, and honest about being weaker. */
export function heuristicAnalysis(url: string, page: PageContent): LandingAnalysis {
  const warnings: string[] = [];
  const offer = page.text.match(/(\d{1,3}\s?% ?off|\$\s?\d[\d,]*(?:\.\d{2})?(?:\s?(?:off|down|\/mo))?|free\s+\w+)/i)?.[0];

  // Headings are the page's own headline attempts; the short ones are usable.
  const candidates = page.headings
    .filter((h) => {
      const n = h.split(/\s+/).length;
      return n >= 3 && n <= 9;
    })
    .slice(0, 4);

  if (!candidates.length) {
    warnings.push('The page has no short headings to work from, so these suggestions are thin.');
  }

  const cta = page.buttons.map((b) => titleCase(b))
    .find((b) => CTA_VOCAB.some((v) => v.toLowerCase() === b.toLowerCase()));

  return {
    fetchedAt: new Date().toISOString(),
    url,
    title: page.title,
    summary:
      page.description ||
      page.paragraphs[0]?.slice(0, 240) ||
      'The page did not expose a description or body copy that could be read.',
    detectedOffer: offer ? titleCase(offer) : undefined,
    detectedCta: cta,
    suggestedHeadlines: candidates,
    suggestedSupport: page.paragraphs.slice(0, 2).map((p) => p.split(/(?<=\.)\s/)[0].slice(0, 90)),
    suggestedCtas: cta ? [cta] : CTA_VOCAB.slice(0, 3),
    warnings: warnings.concat(
      'Read without a language model, so these are extracted from the page rather than written for the ad.',
    ),
    source: 'heuristic',
  };
}

/* ----------------------------------------------------------------- OpenAI */

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['summary', 'audience', 'suggestedHeadlines', 'suggestedSupport', 'suggestedCtas', 'warnings'],
  properties: {
    summary: { type: 'string', description: 'What this page offers, in two sentences, plainly.' },
    audience: { type: 'string', description: 'Who the page is written for.' },
    detectedOffer: { type: ['string', 'null'], description: 'The promotion or price if the page states one, else null.' },
    detectedCta: { type: ['string', 'null'], description: "The page's own primary call to action, else null." },
    suggestedHeadlines: {
      type: 'array', minItems: 3, maxItems: 4, items: { type: 'string' },
      description: 'Display ad headlines of 3-7 words, each starting with an active verb where natural. Must be supported by the page.',
    },
    suggestedSupport: {
      type: 'array', minItems: 2, maxItems: 3, items: { type: 'string' },
      description: 'Supporting lines of 4-10 words.',
    },
    suggestedCtas: {
      type: 'array', minItems: 2, maxItems: 3, items: { type: 'string' },
      description: 'Calls to action of 1-3 words naming the next step.',
    },
    warnings: {
      type: 'array', items: { type: 'string' },
      description: 'Anything that would make these ads mismatch the page: a missing offer, an expired promotion, vague value, or a page that is not a landing page.',
    },
  },
} as const;

const SYSTEM = [
  'You write display advertising copy for a marketing agency.',
  'You are given the text of an advertiser landing page. Suggest ad copy that matches what that page actually promises.',
  'Rules: never invent an offer, price, guarantee, rating or credential that the page does not state.',
  'Headlines run 3-7 words and should start with an active verb where it reads naturally.',
  'Prefer a specific next step over "Learn More" when the page supports one.',
  'If the page is thin, contradictory, or is not a landing page, say so in warnings rather than padding the suggestions.',
].join(' ');

export async function analyzeLandingPage(
  url: string,
  opts: AnalyzeOptions = {},
): Promise<LandingAnalysis> {
  const doFetch = opts.fetchImpl ?? fetch;
  const timeoutMs = opts.timeoutMs ?? 12_000;

  /* ---- fetch the page ---- */
  let html = opts.html;
  if (html === undefined) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await doFetch(url, { headers: { 'user-agent': UA }, signal: ctrl.signal, redirect: 'follow' });
      if (!res.ok) throw new Error(`the page returned ${res.status}`);
      html = await res.text();
    } catch (e: any) {
      // A page we cannot read is a normal outcome, not a failure of the flow.
      return {
        fetchedAt: new Date().toISOString(),
        url,
        summary: '',
        suggestedHeadlines: [],
        suggestedSupport: [],
        suggestedCtas: [],
        warnings: [`Could not read the landing page (${e?.message ?? e}). Suggestions will come from the form answers instead.`],
        source: 'heuristic',
      };
    } finally {
      clearTimeout(timer);
    }
  }

  const page = extractContent(html);

  // A page rendered entirely client-side gives us almost nothing. Say so
  // rather than producing confident suggestions from boilerplate.
  if (page.text.replace(/\s/g, '').length < 200) {
    const thin = heuristicAnalysis(url, page);
    thin.warnings.unshift(
      'The page returned almost no readable text, which usually means it builds itself in the browser. Suggestions are unreliable.',
    );
    return thin;
  }

  const apiKey = opts.apiKey ?? process.env.OPENAI_API_KEY;
  if (!apiKey) return heuristicAnalysis(url, page);

  /* ---- ask the model ---- */
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs + 8000);
  try {
    const res = await doFetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${apiKey}` },
      signal: ctrl.signal,
      body: JSON.stringify({
        model: opts.model ?? process.env.OPENAI_MODEL ?? 'gpt-4o-mini',
        temperature: 0.7,
        messages: [
          { role: 'system', content: SYSTEM },
          {
            role: 'user',
            content:
              `Landing page: ${url}\n\n` +
              `Title: ${page.title}\nDescription: ${page.description}\n\n` +
              `Headings:\n${page.headings.map((h) => `- ${h}`).join('\n')}\n\n` +
              `Body:\n${page.paragraphs.slice(0, 8).join('\n\n')}\n\n` +
              `Buttons: ${page.buttons.join(' | ')}`,
          },
        ],
        response_format: {
          type: 'json_schema',
          json_schema: { name: 'landing_analysis', strict: true, schema: SCHEMA },
        },
      }),
    });

    if (!res.ok) {
      const detail = await res.text().catch(() => '');
      const fallback = heuristicAnalysis(url, page);
      fallback.warnings.unshift(
        `Copy suggestions fell back to page extraction: OpenAI returned ${res.status}${detail ? ` (${detail.slice(0, 120)})` : ''}.`,
      );
      return fallback;
    }

    const data = await res.json();
    const raw = data?.choices?.[0]?.message?.content;
    const parsed = JSON.parse(String(raw ?? '{}'));

    return {
      fetchedAt: new Date().toISOString(),
      url,
      title: page.title,
      summary: parsed.summary ?? '',
      audience: parsed.audience ?? undefined,
      detectedOffer: parsed.detectedOffer ?? undefined,
      detectedCta: parsed.detectedCta ?? undefined,
      suggestedHeadlines: parsed.suggestedHeadlines ?? [],
      suggestedSupport: parsed.suggestedSupport ?? [],
      suggestedCtas: parsed.suggestedCtas ?? [],
      warnings: parsed.warnings ?? [],
      source: 'openai',
    };
  } catch (e: any) {
    const fallback = heuristicAnalysis(url, page);
    fallback.warnings.unshift(`Copy suggestions fell back to page extraction (${e?.message ?? e}).`);
    return fallback;
  } finally {
    clearTimeout(timer);
  }
}
