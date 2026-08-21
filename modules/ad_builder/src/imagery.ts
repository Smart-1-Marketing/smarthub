/**
 * Imagery.
 *
 * Two ways to put a real picture behind an ad instead of a grey placeholder:
 *
 *   Pixabay search — free, licence-clean stock, chosen by keywords derived
 *   from the landing page and campaign. Fast and cheap.
 *
 *   AI generation — an original hero built to the campaign, when stock does
 *   not fit or the customer wants something bespoke. Uses OpenAI images.
 *
 * Both return candidates the person picks from; nothing is auto-applied. And
 * both write their output through fitImageToBudget, so every image — stock or
 * generated — is guaranteed a valid raster under 150 KB before it can become
 * part of a creative. The rule holds by construction: there is no path here
 * that skips the enforcer.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import { fitImageToBudget } from './image-budget';
import type { LandingAnalysis } from './projects';

export interface ImageCandidate {
  /** Local path to the under-budget image. */
  file: string;
  /** Public URL the proof/build screen can show it at. */
  url: string;
  bytes: number;
  width: number;
  height: number;
  source: 'pixabay' | 'openai';
  /** Pixabay page or generation prompt, for attribution/debugging. */
  credit?: string;
}

export interface ImageQuery {
  business: string;
  promoting: string;
  objective?: string;
  landing?: LandingAnalysis;
  /** Extra keywords the caller wants to force in. */
  keywords?: string[];
  /** The single most important benefit, if known — sharpens the subject. */
  benefit?: string;
  /** The offer/promotion, so a sale image differs from a lead-gen image. */
  offer?: string;
  /** Brand palette, so the generated scene tones match the ad. */
  palette?: { primary?: string; secondary?: string; accent?: string };
  /** Local reference images the customer uploaded (product/location/staff
   *  photos). When present, the generator is told to match their look, and —
   *  where the model supports it — they are sent as visual references. */
  referenceImages?: string[];
  /** A plain-English change to apply to an existing image, e.g. "make the
   *  sky darker" or "move the player to the left". Present only on a revise. */
  reviseInstruction?: string;
}

/**
 * Turn a campaign into a short, concrete image search phrase. Landing-page
 * nouns beat form fields — "artisan pizza wood oven" finds better stock than
 * "Italian restaurant lead generation".
 */
export function imageKeywords(q: ImageQuery): string {
  if (q.keywords?.length) return q.keywords.join(' ');
  const stop = /\b(the|and|for|with|your|our|a|an|to|of|in|on|get|best|now|today|free|new)\b/gi;
  const fromLanding = (q.landing?.summary ?? '').replace(stop, ' ');
  const source = `${q.promoting} ${fromLanding}`.replace(stop, ' ');
  const words = source.toLowerCase().match(/[a-z]{4,}/g) ?? [];
  const seen = new Set<string>();
  const picked: string[] = [];
  for (const w of words) {
    if (seen.has(w)) continue;
    seen.add(w);
    picked.push(w);
    if (picked.length >= 4) break;
  }
  if (!picked.length) picked.push(...q.business.toLowerCase().match(/[a-z]{4,}/g) ?? ['business']);
  return picked.join(' ');
}

interface PixabayHit { largeImageURL: string; webformatURL: string; pageURL: string; imageWidth: number; imageHeight: number; }

/**
 * Search Pixabay for landscape photos matching the campaign. Returns up to
 * `count` candidates, each downloaded and squeezed under 150 KB.
 */
export async function searchPixabay(
  q: ImageQuery,
  opts: { cacheDir: string; count?: number; apiKey?: string; fetchImpl?: typeof fetch; timeoutMs?: number },
): Promise<ImageCandidate[]> {
  const apiKey = opts.apiKey ?? process.env.PIXABAY_API;
  if (!apiKey) throw new Error('PIXABAY_API is not set.');
  const doFetch = opts.fetchImpl ?? fetch;
  const query = imageKeywords(q);
  const url = `https://pixabay.com/api/?key=${apiKey}&q=${encodeURIComponent(query)}` +
    `&image_type=photo&orientation=horizontal&safesearch=true&per_page=${Math.max(3, (opts.count ?? 2) * 3)}`;

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 12_000);
  let hits: PixabayHit[] = [];
  try {
    const res = await doFetch(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`Pixabay returned ${res.status}`);
    const data: any = await res.json();
    hits = (data.hits ?? []) as PixabayHit[];
  } finally {
    clearTimeout(timer);
  }
  if (!hits.length) return [];

  fs.mkdirSync(opts.cacheDir, { recursive: true });
  const out: ImageCandidate[] = [];
  for (const hit of hits.slice(0, (opts.count ?? 2) * 2)) {
    if (out.length >= (opts.count ?? 2)) break;
    try {
      const imgRes = await doFetch(hit.largeImageURL ?? hit.webformatURL);
      if (!imgRes.ok) continue;
      const raw = Buffer.from(await imgRes.arrayBuffer());
      const base = `pixabay-${slugKey(query)}-${out.length}`;
      const fitted = await fitImageToBudget(raw, path.join(opts.cacheDir, `${base}.jpg`));
      out.push({
        file: fitted.file, url: '', bytes: fitted.bytes, width: fitted.width, height: fitted.height,
        source: 'pixabay', credit: hit.pageURL,
      });
    } catch {
      /* skip a bad hit */
    }
  }
  return out;
}

/**
 * Generate an original hero with OpenAI images, prompted from the campaign and
 * landing page. Returns one candidate, under 150 KB.
 */

/**
 * Build a structured, five-tier text-to-image prompt (subject, setting,
 * composition, style/lighting, and a negative-prompt clause). This mirrors the
 * design-guidelines recommendation: a controlled prompt produces on-brand
 * imagery with clean negative space for the text overlay, instead of the loose
 * one-line prompt we used before.
 */
export function buildHeroPrompt(q: ImageQuery): { prompt: string; negative: string } {
  // 1. Subject — the concrete thing the ad is about. Landing summary is the
  //    richest source; benefit and offer sharpen it.
  const subjectBits = [q.landing?.summary, q.promoting, q.benefit]
    .filter(Boolean)
    .map((s) => String(s).replace(/\.\s*$/, ''))
    .join('. ');
  const subject = subjectBits || q.business;

  // 2. Context / setting — tie to the campaign, note the offer if any.
  const setting = q.offer
    ? `A real-world setting that suits ${q.business}, evoking the promotion "${q.offer}".`
    : `A natural, real-world setting that suits ${q.business}.`;

  // 3. Spatial composition — enforce negative space for copy.
  const composition =
    'Rule-of-thirds composition with the subject offset to one side, leaving ' +
    'generous, uncluttered negative space on the opposite side for headline and ' +
    'button text. Nothing important near the edges.';

  // 4. Style & lighting, tinted toward the brand palette when known.
  const paletteHint = q.palette?.primary
    ? `Colour palette in harmony with ${q.palette.primary}${q.palette.accent ? ` and ${q.palette.accent}` : ''}.`
    : 'Colour palette suited to a professional brand.';
  const style =
    `Realistic commercial advertising photography, soft natural lighting, ` +
    `crisp focus, high resolution. ${paletteHint}`;

  const prompt = [
    `Advertising background photo for ${q.business}.`,
    `Subject: ${subject}.`,
    setting,
    composition,
    style,
    // A revision keeps the same scene but applies the customer's change on top.
    q.reviseInstruction ? `Apply this change while keeping the same overall scene: ${q.reviseInstruction}.` : '',
    // The overlay copy is added by our renderer, never by the image model.
    // This is stated positively AND up front because a single negative clause
    // is not reliably honoured — a background with baked-in words is unusable.
    'CRITICAL: the image must contain absolutely no text, letters, words, numbers, ' +
    'captions, labels, logos, watermarks, or signage of any kind. It is a purely ' +
    'photographic background; all wording is added separately afterwards. ' +
    'Any visible text makes the image unusable.',
  ].filter(Boolean).join(' ');

  // 5. Negative prompt — keep the model from producing anything that fights
  //    the overlaid copy or looks artificial. Text terms are listed first and
  //    repeated because no-wording is the hard requirement here.
  const negative =
    'text, words, letters, lettering, typography, captions, labels, numbers, ' +
    'writing, handwriting, fonts, signage, street signs, banners with text, ' +
    'embedded logos, brand marks, watermarks, ' +
    'cluttered background, busy patterns behind text, distorted or extra limbs, ' +
    'oversaturated colours, harsh high-contrast shadows in the copy area, ' +
    'collage, borders, low resolution, noise artifacts';

  return { prompt, negative };
}

export async function generateHero(
  q: ImageQuery,
  opts: { cacheDir: string; apiKey?: string; fetchImpl?: typeof fetch; timeoutMs?: number; size?: string },
): Promise<ImageCandidate> {
  const apiKey = opts.apiKey ?? process.env.OPENAI_API_KEY;
  if (!apiKey) throw new Error('OPENAI_API_KEY is not set.');
  const doFetch = opts.fetchImpl ?? fetch;

  const { prompt, negative } = buildHeroPrompt(q);
  // gpt-image-1 has no separate negative-prompt field, so fold it in as an
  // explicit exclusion clause the model honours well in practice. End with a
  // final no-text reminder — models weight the closing instruction heavily.
  const fullPrompt = `${prompt} Do not include: ${negative}. Remember: zero text or wording anywhere in the image.`;

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 90_000);
  try {
    // When the customer uploaded reference photos, use the edits endpoint so
    // the generated hero echoes their real product/location, not a generic
    // stock scene. Fall back to plain generation when there are none.
    const refs = (q.referenceImages ?? []).filter((f) => fs.existsSync(f)).slice(0, 4);
    let res: Response;
    if (refs.length) {
      const form = new FormData();
      form.set('model', process.env.OPENAI_IMAGE_MODEL ?? 'gpt-image-1');
      form.set('prompt',
        `${fullPrompt} Use the supplied image(s) as reference for the real subject, ` +
        `style and colours, but recompose with clean negative space for text.`);
      form.set('size', opts.size ?? '1536x1024');
      for (const r of refs) {
        const buf = fs.readFileSync(r);
        form.append('image[]', new Blob([buf], { type: 'image/png' }), path.basename(r));
      }
      res = await doFetch('https://api.openai.com/v1/images/edits', {
        method: 'POST',
        headers: { authorization: `Bearer ${apiKey}` },
        body: form as any,
        signal: ctrl.signal,
      });
    } else {
      res = await doFetch('https://api.openai.com/v1/images/generations', {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${apiKey}` },
        body: JSON.stringify({
          model: process.env.OPENAI_IMAGE_MODEL ?? 'gpt-image-1',
          prompt: fullPrompt,
          n: 1,
          size: opts.size ?? '1536x1024',
        }),
        signal: ctrl.signal,
      });
    }
    if (!res.ok) throw new Error(`OpenAI images returned ${res.status}: ${(await res.text()).slice(0, 150)}`);
    const data: any = await res.json();
    const b64 = data.data?.[0]?.b64_json;
    const raw = b64
      ? Buffer.from(b64, 'base64')
      : Buffer.from(await (await doFetch(data.data[0].url)).arrayBuffer());

    fs.mkdirSync(opts.cacheDir, { recursive: true });
    const fitted = await fitImageToBudget(raw, path.join(opts.cacheDir, `ai-hero-${Date.now().toString(36)}.jpg`));
    return {
      file: fitted.file, url: '', bytes: fitted.bytes, width: fitted.width, height: fitted.height,
      source: 'openai', credit: prompt,
    };
  } finally {
    clearTimeout(timer);
  }
}

function slugKey(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 30);
}
