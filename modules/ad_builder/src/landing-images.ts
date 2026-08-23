/**
 * Pictures already on the landing page.
 *
 * The best background for an ad is usually one the client has already chosen
 * for themselves: it is their premises, their van, their crew, and nobody has
 * to clear the licence because they are already publishing it. So this reads
 * the landing page the campaign points at and offers what is on it.
 *
 * Two decisions worth stating, because both are easy to get wrong.
 *
 * **Dimensions are measured, not read.** A width attribute in the markup is
 * the size the page displays an image at, not the size of the file — a 2000px
 * photo shown in a 240px column would be rejected by its attribute and a 90px
 * icon stretched to 400px would be accepted. Both are exactly backwards for
 * choosing a background, so every candidate is fetched and measured with the
 * same library that rasterises the ads. That costs a request per image, which
 * is why the candidate list is capped and the fetches are bounded.
 *
 * **SVG is skipped.** On a marketing page an SVG is nearly always the logo,
 * an icon or a decorative blob, and the brief for this feature is explicit
 * that a logo must never end up inside the picture.
 *
 * Nothing here is auto-applied. It returns candidates a person picks from,
 * the same as the stock and AI paths.
 */

import sharp from 'sharp';

import { assetUrlIsSafe } from './assets';

const UA = 'Mozilla/5.0 (compatible; Smart1AdBuilder/1.0; +https://smart1marketing.com)';

/** Below this a picture cannot carry an ad background at any useful size. */
export const MIN_WIDTH = 300;

/** Fetching every image on a long page is someone else's bandwidth. */
const MAX_CANDIDATES = 24;

/** A background photo far larger than this is not worth pulling to measure. */
const MAX_BYTES = 12 * 1024 * 1024;

/** How many measurements run at once. Politeness, not throughput. */
const CONCURRENCY = 4;

export interface ImageCandidate {
  url: string;
  alt: string;
  /** Where on the page it came from, which is also how it is ranked. */
  origin: 'og' | 'img' | 'css';
}

export interface PageImage extends ImageCandidate {
  width: number;
  height: number;
  bytes: number;
  format: string;
}

export interface LandingImagesResult {
  url: string;
  fetchedAt: string;
  images: PageImage[];
  /** Considered and measured, so "3 of 19" can be shown rather than just 3. */
  examined: number;
  /** Said in words, because an empty list with no reason reads as a failure. */
  warnings: string[];
}

export interface LandingImagesOptions {
  timeoutMs?: number;
  minWidth?: number;
  maxCandidates?: number;
  /** Skip the network and read this HTML instead. Used by tests. */
  html?: string;
  fetchImpl?: typeof fetch;
}

const decode = (s: string) =>
  s.replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').trim();

/**
 * The largest URL in a srcset.
 *
 * Responsive markup lists the small variants first, so taking the first entry
 * would systematically pick the phone-sized copy of every photo on the page.
 */
function largestFromSrcset(srcset: string): string {
  let best = '';
  let bestScore = -1;
  for (const part of srcset.split(',')) {
    const bits = part.trim().split(/\s+/);
    const url = bits[0];
    if (!url) continue;
    const d = bits[1] ?? '';
    // "1600w" sorts by pixel width; "2x" by density; a bare entry is 1x.
    const score = d.endsWith('w') ? parseInt(d, 10)
      : d.endsWith('x') ? parseFloat(d) * 1000
      : 1;
    if (Number.isFinite(score) && score > bestScore) {
      bestScore = score;
      best = url;
    }
  }
  return best;
}

/**
 * Every image URL the page refers to, absolute and deduplicated.
 *
 * Pure and exported so the parsing can be tested without a network.
 */
export function extractImageUrls(html: string, pageUrl: string): ImageCandidate[] {
  const out: ImageCandidate[] = [];
  const seen = new Set<string>();

  const add = (raw: string, alt: string, origin: ImageCandidate['origin']) => {
    const src = decode(raw ?? '');
    if (!src) return;
    // A data: URI is usually a placeholder or a tracking pixel, and it is
    // already in the page rather than something we can offer as a file.
    if (/^data:/i.test(src)) return;
    let abs: string;
    try {
      abs = new URL(src, pageUrl).toString();
    } catch {
      return;
    }
    if (!/^https?:/i.test(abs)) return;
    // See the note at the top: on a marketing page this is the logo.
    if (/\.svg($|[?#])/i.test(abs)) return;
    // Offer only what can actually be applied. Applying a background fetches
    // it through the same guard every other asset uses -- https only, no
    // internal hosts -- so listing a picture that guard will refuse produces
    // a chooser where clicking a thumbnail fails for a reason the person
    // cannot see. Better not to offer it.
    if (!assetUrlIsSafe(abs).ok) return;
    const key = abs.split('#')[0];
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ url: abs, alt: decode(alt ?? ''), origin });
  };

  // og:image first — it is the picture the client chose to represent the page,
  // so it is the best single guess and is ranked accordingly below.
  const metaRe = /<meta\b[^>]*>/gi;
  for (const m of html.match(metaRe) ?? []) {
    const prop = /(?:property|name)\s*=\s*["']([^"']+)["']/i.exec(m)?.[1]?.toLowerCase() ?? '';
    if (prop !== 'og:image' && prop !== 'og:image:secure_url' && prop !== 'twitter:image') continue;
    const content = /content\s*=\s*["']([^"']+)["']/i.exec(m)?.[1];
    if (content) add(content, '', 'og');
  }

  const imgRe = /<img\b[^>]*>/gi;
  for (const tag of html.match(imgRe) ?? []) {
    const alt = /\balt\s*=\s*["']([^"']*)["']/i.exec(tag)?.[1] ?? '';
    const srcset = /\bsrcset\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1];
    let tookSrcset = false;
    if (srcset) {
      const best = largestFromSrcset(srcset);
      if (best) { add(best, alt, 'img'); tookSrcset = true; }
    }
    // Lazy-loading pages leave src as a placeholder and put the real file in
    // data-src, so those are taken and the measurement sorts them out. `src`
    // itself is skipped once a srcset produced something, because a srcset
    // lists variants of the SAME picture -- taking both offered the phone-sized
    // and desktop-sized copy of one photo as two separate choices.
    const attrs = tookSrcset
      ? ['data-src', 'data-lazy-src', 'data-original']
      : ['src', 'data-src', 'data-lazy-src', 'data-original'];
    for (const attr of attrs) {
      const v = new RegExp(`\\b${attr}\\s*=\\s*["']([^"']+)["']`, 'i').exec(tag)?.[1];
      if (v) add(v, alt, 'img');
    }
  }

  // Hero images are very often a CSS background rather than an <img>.
  const cssRe = /background(?:-image)?\s*:\s*[^;"']*url\(\s*["']?([^"')]+)["']?\s*\)/gi;
  let cm: RegExpExecArray | null;
  while ((cm = cssRe.exec(html)) !== null) add(cm[1], '', 'css');

  return out;
}

/** Fetch just enough of an image to measure it. */
async function measure(
  cand: ImageCandidate,
  doFetch: typeof fetch,
  timeoutMs: number,
): Promise<PageImage | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await doFetch(cand.url, {
      headers: { 'user-agent': UA, accept: 'image/*' },
      signal: ctrl.signal,
      redirect: 'follow',
    });
    if (!res.ok) return null;
    const declared = Number(res.headers.get('content-length') ?? 0);
    if (declared && declared > MAX_BYTES) return null;
    const buf = Buffer.from(await res.arrayBuffer());
    if (buf.byteLength > MAX_BYTES) return null;
    const meta = await sharp(buf).metadata();
    if (!meta.width || !meta.height) return null;
    return {
      ...cand,
      width: meta.width,
      height: meta.height,
      bytes: buf.byteLength,
      format: meta.format ?? '',
    };
  } catch {
    // One unreadable image must not lose the other twenty.
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function inBatches<T, R>(
  items: T[],
  size: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const out: R[] = [];
  for (let i = 0; i < items.length; i += size) {
    out.push(...(await Promise.all(items.slice(i, i + size).map(fn))));
  }
  return out;
}

/**
 * Usable background photos from a landing page, biggest first.
 *
 * Never throws: a page that cannot be read returns an empty list and a
 * warning saying so, because this is one of five ways to pick a background
 * and the other four should still work.
 */
export async function landingImages(
  url: string,
  opts: LandingImagesOptions = {},
): Promise<LandingImagesResult> {
  const doFetch = opts.fetchImpl ?? fetch;
  const timeoutMs = opts.timeoutMs ?? 12_000;
  const minWidth = opts.minWidth ?? MIN_WIDTH;
  const cap = opts.maxCandidates ?? MAX_CANDIDATES;
  const warnings: string[] = [];
  const fetchedAt = new Date().toISOString();

  let html = opts.html;
  if (html === undefined) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await doFetch(url, {
        headers: { 'user-agent': UA },
        signal: ctrl.signal,
        redirect: 'follow',
      });
      if (!res.ok) throw new Error(`the page returned ${res.status}`);
      html = await res.text();
    } catch (e: any) {
      return {
        url,
        fetchedAt,
        images: [],
        examined: 0,
        warnings: [
          `Could not read ${url} (${e?.message ?? e}). Pick a background another way.`,
        ],
      };
    } finally {
      clearTimeout(timer);
    }
  }

  const candidates = extractImageUrls(html, url);
  if (!candidates.length) {
    return {
      url,
      fetchedAt,
      images: [],
      examined: 0,
      warnings: ['That page has no images we can read. It may build them in JavaScript.'],
    };
  }

  const considered = candidates.slice(0, cap);
  if (candidates.length > considered.length) {
    // Said out loud rather than silently truncated: a capped list that reads
    // as the whole list is how "there was nothing good" becomes wrong.
    warnings.push(
      `Only the first ${considered.length} of ${candidates.length} images were measured.`,
    );
  }

  const measured = (await inBatches(considered, CONCURRENCY, (c) =>
    measure(c, doFetch, timeoutMs))).filter(Boolean) as PageImage[];

  const big = measured.filter((m) => m.width >= minWidth);
  if (measured.length && !big.length) {
    warnings.push(
      `All ${measured.length} images on that page are under ${minWidth}px wide — too small for a background.`,
    );
  }

  // The page's own chosen picture first, then by area: the largest file is the
  // one that survives being scaled into a 970x250 banner.
  big.sort((a, b) => {
    if (a.origin !== b.origin) {
      if (a.origin === 'og') return -1;
      if (b.origin === 'og') return 1;
    }
    return b.width * b.height - a.width * a.height;
  });

  return { url, fetchedAt, images: big, examined: measured.length, warnings };
}
