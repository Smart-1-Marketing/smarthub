/**
 * Rasterisation and file-weight targeting.
 *
 * Amazon's static limits are tight (40 KB for 728x90 and 160x600 per the
 * background research), so "render then hope" is not viable. We step down a
 * quality ladder until the artefact fits, and report honestly when it can't.
 */

import sharp from 'sharp';

export interface RasterOptions {
  svg: string;
  /** Preferred output format; we may fall back to jpg to hit a weight limit. */
  formats: ('png' | 'jpg')[];
  maxFileBytes: number;
  /** Flatten transparency onto this colour for jpg output. */
  background?: string;
}

export interface RasterResult {
  buffer: Buffer;
  format: 'png' | 'jpg';
  bytes: number;
  /** True when even the lowest quality step exceeded the limit. */
  overweight: boolean;
  attempts: number;
}

const JPEG_LADDER = [92, 86, 80, 74, 68, 62, 55, 48, 40];
const PNG_COLOUR_LADDER = [256, 192, 128, 96, 64, 48, 32];

export async function rasterise(opts: RasterOptions): Promise<RasterResult> {
  const { svg, formats, maxFileBytes, background = '#ffffff' } = opts;
  const input = Buffer.from(svg);
  let attempts = 0;
  let best: { buffer: Buffer; format: 'png' | 'jpg' } | null = null;

  for (const format of formats) {
    if (format === 'png') {
      for (const colours of PNG_COLOUR_LADDER) {
        attempts++;
        const buffer = await sharp(input)
          .png({ palette: true, colours, effort: 8, compressionLevel: 9 })
          .toBuffer();
        if (!best || buffer.length < best.buffer.length) best = { buffer, format };
        if (buffer.length <= maxFileBytes) {
          return { buffer, format, bytes: buffer.length, overweight: false, attempts };
        }
      }
    } else {
      for (const quality of JPEG_LADDER) {
        attempts++;
        const buffer = await sharp(input)
          .flatten({ background })
          .jpeg({ quality, mozjpeg: true, chromaSubsampling: '4:2:0' })
          .toBuffer();
        if (!best || buffer.length < best.buffer.length) best = { buffer, format };
        if (buffer.length <= maxFileBytes) {
          return { buffer, format, bytes: buffer.length, overweight: false, attempts };
        }
      }
    }
  }

  return {
    buffer: best!.buffer,
    format: best!.format,
    bytes: best!.buffer.length,
    overweight: true,
    attempts,
  };
}

/**
 * Mean relative luminance of a region of a rendered background, used to check
 * that text placed over imagery actually has contrast against what is behind
 * it — not against the flat colour the template nominally declared.
 */
export async function regionLuminance(
  png: Buffer,
  region: { left: number; top: number; width: number; height: number },
): Promise<number> {
  const meta = await sharp(png).metadata();
  const W = meta.width ?? 0;
  const H = meta.height ?? 0;
  const left = Math.max(0, Math.min(W - 1, Math.round(region.left)));
  const top = Math.max(0, Math.min(H - 1, Math.round(region.top)));
  const width = Math.max(1, Math.min(W - left, Math.round(region.width)));
  const height = Math.max(1, Math.min(H - top, Math.round(region.height)));

  // sharp's .stats() reports on the *input* image and ignores earlier pipeline
  // operations, so the crop has to be materialised to a buffer first. Calling
  // .extract().stats() silently returns whole-canvas statistics instead.
  const cropped = await sharp(png)
    .extract({ left, top, width, height })
    .removeAlpha()
    .toBuffer();
  const stats = await sharp(cropped).stats();
  const [r, g, b] = stats.channels.map((c) => c.mean / 255);
  return relativeLuminance(r, g, b);
}

function channel(c: number): number {
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

export function relativeLuminance(r: number, g: number, b: number): number {
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function hexLuminance(hex: string): number {
  const m = hex.replace('#', '');
  const full = m.length === 3 ? m.split('').map((c) => c + c).join('') : m;
  const r = parseInt(full.slice(0, 2), 16) / 255;
  const g = parseInt(full.slice(2, 4), 16) / 255;
  const b = parseInt(full.slice(4, 6), 16) / 255;
  return relativeLuminance(r, g, b);
}

export function contrastRatio(l1: number, l2: number): number {
  const hi = Math.max(l1, l2);
  const lo = Math.min(l1, l2);
  return (hi + 0.05) / (lo + 0.05);
}
