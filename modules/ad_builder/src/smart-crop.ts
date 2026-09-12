/**
 * A suggested crop, offered — never applied.
 *
 * `coverRect()` in svg.ts places a background photo from an offset and a
 * zoom a person chose. Cloudinary's `g_auto` crop (subject detection) makes
 * that same choice automatically, and it is a genuinely better first guess
 * than the centred default most photos land on. What it does not do is hand
 * back the offset/zoom pair coverRect() understands — Cloudinary publishes
 * no field carrying the crop rectangle it picked, only the finished pixels.
 *
 * So this recovers it the only way available: ask Cloudinary for the
 * `g_auto,c_fill` crop at one reference size, then find where that crop sits
 * inside the original, full-resolution image by correlating pixels. That
 * position is the one thing actually unknown. The *size* of the crop window
 * is not — `c_fill` covers the target box at `max(W/iw, H/ih)` and never
 * zooms past it (g_auto only ever chooses *where* to crop inside that cover,
 * never how much to show), which is exactly the zoom-1 case coverRect()
 * already computes. `offsetFromCropWindow()` inverts that same arithmetic,
 * scale for scale, rather than approximating a second version of it.
 *
 * Nothing here is applied by running. `suggestCrop()` returns a suggestion;
 * the build screen previews it and a person presses Use it before it touches
 * `backgroundOffset` / `backgroundZoom` on a concept — the same distinction
 * `hub.storage.smart_crop_url()` draws on the Python side of this Hub.
 *
 * And every way this can fail returns `{ ok: false, reason }` rather than
 * raising: a background that is not a Cloudinary asset, a correlation with
 * nothing to recommend it, a fetch that times out. A failed suggestion costs
 * only the suggestion — today's centred placement stands either way.
 */

import sharp from 'sharp';
import { assetUrlIsSafe } from './assets';
import { MAX_BG_ZOOM, MIN_BG_ZOOM } from './svg';

export interface CropSuggestion {
  offset: { x: number; y: number };
  zoom: number;
  /** 0..1. How much the matched position actually resembles the crop. */
  confidence: number;
}

export type CropSuggestionResult =
  | ({ ok: true } & CropSuggestion)
  | { ok: false; reason: string };

/** Below this, the "match" is not one worth showing anybody. */
export const MIN_CONFIDENCE = 0.35;

/** The search image's longest edge, in analysis pixels. Bounds the cost of
 *  the correlation regardless of how large the source photo is. */
export const ANALYSIS_MAX_DIM = 320;

const FETCH_TIMEOUT_MS = 15_000;
const MAX_FETCH_BYTES = 25 * 1024 * 1024;

function clampOffset(n: number): number {
  return Math.max(-1, Math.min(1, n));
}

/**
 * The pure geometry: given where the crop window sits inside the original
 * image (its top-left corner, in the original's own pixels), what offset and
 * zoom would draw the same window through `coverRect()`.
 *
 * This is `coverRect()` run backwards. That function's own note about the
 * sign is the one to reread before touching this: the offset names the part
 * of the PICTURE that shows, so a picture cropped flush to its own left edge
 * (cropX = 0) is offset -1 ("show me the left"), and one cropped flush to its
 * right edge is offset +1 — not the other way round, and a near-symmetrical
 * photo will not tell you if you got it backwards.
 *
 * `c_fill` never zooms past cover, so the zoom this returns is always 1 —
 * said explicitly rather than left implicit, so a caller cannot mistake this
 * for a function that also detects zoom.
 */
export function offsetFromCropWindow(
  iw: number,
  ih: number,
  W: number,
  H: number,
  cropX: number,
  cropY: number,
): { offset: { x: number; y: number }; zoom: number } {
  const scale = Math.max(W / iw, H / ih);
  const w = iw * scale;
  const h = ih * scale;
  const slackX = (w - W) / 2;
  const slackY = (h - H) / 2;

  // The crop window's top-left, translated into the same "virtual, scaled
  // image" coordinate space coverRect()'s own `x`/`y` are drawn in.
  const px = cropX * scale;
  const py = cropY * scale;

  const x = slackX > 1e-6 ? clampOffset(px / slackX - 1) : 0;
  const y = slackY > 1e-6 ? clampOffset(py / slackY - 1) : 0;

  const zoom = Math.max(MIN_BG_ZOOM, Math.min(MAX_BG_ZOOM, 1));
  return { offset: { x, y }, zoom };
}

/**
 * Normalised cross-correlation, template against search, restricted to the
 * position range the caller already knows is possible.
 *
 * A `c_fill` crop has slack on at most one axis — the other is pinned to 0
 * by the cover arithmetic — so in the ordinary case this is a 1-D search
 * wearing a 2-D loop; `rangeX`/`rangeY` is what keeps it cheap regardless.
 */
export function matchCropWindow(
  search: Uint8Array,
  sw: number,
  sh: number,
  template: Uint8Array,
  tw: number,
  th: number,
  rangeX: number,
  rangeY: number,
): { x: number; y: number; score: number } {
  let tSum = 0;
  for (let i = 0; i < template.length; i++) tSum += template[i];
  const tMean = tSum / template.length;
  let tVar = 0;
  for (let i = 0; i < template.length; i++) {
    const d = template[i] - tMean;
    tVar += d * d;
  }
  const tNorm = Math.sqrt(tVar);

  let best = { x: 0, y: 0, score: -Infinity };
  for (let y = 0; y <= rangeY; y++) {
    for (let x = 0; x <= rangeX; x++) {
      let pSum = 0;
      for (let ty = 0; ty < th; ty++) {
        const row = (y + ty) * sw + x;
        for (let tx = 0; tx < tw; tx++) pSum += search[row + tx];
      }
      const pMean = pSum / (tw * th);

      let num = 0;
      let pVar = 0;
      for (let ty = 0; ty < th; ty++) {
        const row = (y + ty) * sw + x;
        for (let tx = 0; tx < tw; tx++) {
          const sv = search[row + tx] - pMean;
          const tv = template[ty * tw + tx] - tMean;
          num += sv * tv;
          pVar += sv * sv;
        }
      }
      const denom = Math.sqrt(pVar) * tNorm;
      const score = denom > 1e-6 ? num / denom : -1;
      if (score > best.score) best = { x, y, score };
    }
  }
  return best;
}

async function grayRaw(buf: Buffer, w: number, h: number): Promise<Uint8Array> {
  const data = await sharp(buf)
    .resize(Math.max(1, Math.round(w)), Math.max(1, Math.round(h)), { fit: 'fill' })
    .greyscale()
    .raw()
    .toBuffer();
  return data;
}

/**
 * Find the crop `g_auto,c_fill` chose and convert it into an offset/zoom,
 * given the two images as bytes already in hand. Split out from
 * `suggestCrop()` so the matching itself — the part most likely to carry a
 * sign or scale bug — is testable without a network call.
 */
export async function matchCropInImage(
  originalBuf: Buffer,
  croppedBuf: Buffer,
  opts: { analysisMaxDim?: number } = {},
): Promise<CropSuggestionResult> {
  try {
    const [origMeta, cropMeta] = await Promise.all([
      sharp(originalBuf).metadata(),
      sharp(croppedBuf).metadata(),
    ]);
    const iw = origMeta.width ?? 0;
    const ih = origMeta.height ?? 0;
    // What Cloudinary actually delivered, not what was asked for -- the
    // `_dimensions()` rule this Hub already applies to a stored file.
    const W = cropMeta.width ?? 0;
    const H = cropMeta.height ?? 0;
    if (!(iw > 0) || !(ih > 0) || !(W > 0) || !(H > 0)) {
      return { ok: false, reason: 'could not read the image dimensions' };
    }

    const coverScale = Math.max(W / iw, H / ih);
    const cropW = W / coverScale;
    const cropH = H / coverScale;
    const rangeXOriginal = Math.max(0, iw - cropW);
    const rangeYOriginal = Math.max(0, ih - cropH);

    const analysisMaxDim = opts.analysisMaxDim ?? ANALYSIS_MAX_DIM;
    const longest = Math.max(iw, ih);
    const searchScale = longest > 0 ? Math.min(1, analysisMaxDim / longest) : 1;

    const sw = Math.max(1, Math.round(iw * searchScale));
    const sh = Math.max(1, Math.round(ih * searchScale));
    const tw = Math.max(1, Math.round(cropW * searchScale));
    const th = Math.max(1, Math.round(cropH * searchScale));
    if (tw > sw || th > sh) {
      return { ok: false, reason: 'the crop window does not fit inside the original' };
    }

    const [search, template] = await Promise.all([
      grayRaw(originalBuf, sw, sh),
      grayRaw(croppedBuf, tw, th),
    ]);

    const rangeXS = Math.max(0, Math.min(sw - tw, Math.round(rangeXOriginal * searchScale)));
    const rangeYS = Math.max(0, Math.min(sh - th, Math.round(rangeYOriginal * searchScale)));

    const match = matchCropWindow(search, sw, sh, template, tw, th, rangeXS, rangeYS);
    const confidence = Math.max(0, match.score);
    if (confidence < MIN_CONFIDENCE) {
      return { ok: false, reason: `low correlation confidence (${confidence.toFixed(2)})` };
    }

    const cropX = searchScale > 0 ? match.x / searchScale : 0;
    const cropY = searchScale > 0 ? match.y / searchScale : 0;
    const { offset, zoom } = offsetFromCropWindow(iw, ih, W, H, cropX, cropY);

    return { ok: true, offset, zoom, confidence };
  } catch (e: any) {
    return { ok: false, reason: String(e?.message ?? e) };
  }
}

/**
 * A Cloudinary delivery URL (or `cloudinary:<publicId>`), split into the
 * pieces needed to address both the plain original and a transformed crop of
 * it.
 *
 * Refuses a URL that already carries a transformation rather than trying to
 * combine a second one onto it — `g_auto,c_fill,...` chained after whatever
 * is already there is not a shape Cloudinary defines consistently, and
 * guessing at it is exactly the kind of invention this file exists to avoid.
 */
function parseCloudinarySource(
  source: string,
): { base: string; version: string; publicPath: string } | { reason: string } {
  const src = String(source ?? '').trim();
  if (!src) return { reason: 'no source image' };

  if (/^cloudinary:/i.test(src)) {
    const cloud = process.env.CLOUDINARY_CLOUD_NAME;
    if (!cloud) return { reason: 'CLOUDINARY_CLOUD_NAME is not set' };
    const publicPath = src.slice('cloudinary:'.length).trim();
    if (!publicPath) return { reason: 'no public id' };
    return { base: `https://res.cloudinary.com/${cloud}/image/upload/`, version: '', publicPath };
  }

  const m = /^(https:\/\/res\.cloudinary\.com\/[^/]+\/image\/upload\/)(.+)$/i.exec(src);
  if (!m) return { reason: 'not a plain Cloudinary delivery URL' };

  const base = m[1];
  const rest = m[2].split('?')[0].split('#')[0];
  const segments = rest.split('/').filter(Boolean);
  if (!segments.length) return { reason: 'no public id' };

  const first = segments[0];
  if (first.includes(',') || /^[a-z]{1,3}_/i.test(first)) {
    return { reason: 'the URL already carries a transformation' };
  }

  let version = '';
  let idx = 0;
  if (/^v\d+$/i.test(first)) {
    version = first;
    idx = 1;
  }
  const publicPath = segments.slice(idx).join('/');
  if (!publicPath) return { reason: 'no public id' };
  return { base, version, publicPath };
}

async function fetchImage(url: string, label: string): Promise<Buffer> {
  const safe = assetUrlIsSafe(url);
  if (!safe.ok) throw new Error(`refusing to fetch ${label}: ${safe.reason}`);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`fetching ${label} failed: ${res.status}`);
    const buf = Buffer.from(await res.arrayBuffer());
    if (buf.length === 0) throw new Error(`empty response fetching ${label}`);
    if (buf.length > MAX_FETCH_BYTES) throw new Error(`${label} exceeds ${MAX_FETCH_BYTES} bytes`);
    return buf;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Suggest a crop for `source` at a reference canvas of `targetW` x `targetH`.
 *
 * `source` is whatever the background editor already holds as the picked
 * URL before it was downloaded to a local file (Cloudinary asks nothing of a
 * local path). Anything that is not a plain Cloudinary delivery URL — the
 * client's own gallery is; a stock pick or an AI generation, once downloaded
 * into this service's own `/files/...`, is not — comes back `{ ok: false }`
 * naming why, never a guess.
 */
export async function suggestCrop(
  source: string,
  targetW: number,
  targetH: number,
): Promise<CropSuggestionResult> {
  const W = Math.round(Number(targetW) || 0);
  const H = Math.round(Number(targetH) || 0);
  if (!(W > 0) || !(H > 0)) return { ok: false, reason: 'no reference canvas' };

  const parsed = parseCloudinarySource(source);
  if ('reason' in parsed) return { ok: false, reason: parsed.reason };

  const { base, version, publicPath } = parsed;
  const versionSeg = version ? `${version}/` : '';
  const originalUrl = `${base}${versionSeg}${publicPath}`;
  const transformedUrl = `${base}g_auto,c_fill,w_${W},h_${H}/${versionSeg}${publicPath}`;

  try {
    const [originalBuf, croppedBuf] = await Promise.all([
      fetchImage(originalUrl, 'original image'),
      fetchImage(transformedUrl, 'g_auto crop'),
    ]);
    return await matchCropInImage(originalBuf, croppedBuf);
  } catch (e: any) {
    return { ok: false, reason: String(e?.message ?? e) };
  }
}
