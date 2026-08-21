/**
 * Wordmark fallback.
 *
 * A missing logo used to be a dead end: the campaign was flagged un-renderable
 * and a human had to step in. But most small businesses do not have a logo
 * file to hand, and Brandfetch does not always return a usable one (an SVG the
 * pipeline can't rasterize, a dead URL, an icon with no wordmark). The tool
 * should still produce a real, presentable ad.
 *
 * So when no image logo resolves, we set the business name in the brand's
 * headline font, on a transparent PNG, and use that as the logo. It is not a
 * designed identity — but "Northside Dental" cleanly set in Poppins reads as a
 * brand lockup, renders on every layout, and keeps the customer moving instead
 * of waiting on us.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import sharp from 'sharp';
import { resolveFont, measure, textPath, metrics } from './fonts';
import type { Brand } from './types';

export interface WordmarkOptions {
  /** Where to write the PNG. */
  cacheDir: string;
  /** Ink colour. Defaults to the brand's dark, then near-black. */
  color?: string;
  /** Rendered height budget; width follows the text. 2x for crispness. */
  height?: number;
}

/**
 * Render `name` as a wordmark PNG and return its path. Deterministic filename
 * per name+font+colour so repeated builds reuse it.
 */
export async function makeWordmark(
  name: string,
  brand: Pick<Brand, 'fonts' | 'colors' | 'name'>,
  opts: WordmarkOptions,
): Promise<string> {
  const text = (name || brand.name || 'Your Business').trim();
  const family = brand.fonts?.headline || 'Poppins';
  const ink = opts.color || brand.colors?.dark || '#111318';
  const H = opts.height ?? 120;

  // Fit the type to ~64% of the height, then pad. A wordmark wants air.
  const fontSize = Math.round(H * 0.62);
  const font = resolveFont(family, 'bold');
  const m = metrics(font, fontSize);
  const padX = Math.round(fontSize * 0.28);
  const padY = Math.round((H - (m.ascender + m.descender)) / 2);

  const textWidth = Math.ceil(measure(font, text, fontSize));
  const width = textWidth + padX * 2;

  const baseline = padY + m.ascender;
  const d = textPath(font, text, padX, baseline, fontSize);

  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${H}" viewBox="0 0 ${width} ${H}">` +
    `<path d="${d}" fill="${ink}"/></svg>`;

  fs.mkdirSync(opts.cacheDir, { recursive: true });
  const slug = text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);
  const out = path.join(opts.cacheDir, `wordmark-${slug}-${family.toLowerCase().replace(/\s+/g, '')}.png`);

  await sharp(Buffer.from(svg))
    .resize({ width: width * 2, height: H * 2, fit: 'fill' }) // 2x for crisp downscale
    .png()
    .toFile(out);

  return out;
}
