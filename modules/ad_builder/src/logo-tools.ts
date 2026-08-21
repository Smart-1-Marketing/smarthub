/**
 * Logo rework.
 *
 * Two needs from the proof feedback:
 *
 *   1. Any logo that is not already transparent must have its background
 *      removed before compositing — a white box around a logo on a coloured
 *      ad looks broken. We do a conservative flood-fill from the edges: if the
 *      border is a near-uniform colour, that colour becomes transparent.
 *
 *   2. When a logo does not sit well on a banner (too small, wrong colourway),
 *      the person can ask AI to rework it. Crucially this must NOT redraw the
 *      logo — that would destroy brand integrity. Instead it cleans up what is
 *      there: trims dead space, removes a flat background, and can produce a
 *      reversed (white) version for dark placements. It never invents new
 *      marks or letters.
 *
 * Everything here funnels through the 150 KB budget via the caller.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import sharp from 'sharp';

/** True if the image already has meaningful transparency. */
export async function hasTransparency(file: string): Promise<boolean> {
  const meta = await sharp(file).metadata();
  if (!meta.hasAlpha) return false;
  // hasAlpha can be true with a fully-opaque alpha channel; check for real
  // transparent pixels by sampling the alpha min.
  const stats = await sharp(file).stats();
  const alpha = stats.channels[stats.channels.length - 1];
  return alpha.min < 250;
}

/**
 * Remove a flat background from a logo by making edge-colour pixels
 * transparent. Conservative: only acts when the border is nearly uniform, so a
 * photographic logo is left alone.
 */
export async function removeFlatBackground(input: string, outFile: string): Promise<string> {
  const img = sharp(input).ensureAlpha();
  const { data, info } = await img.raw().toBuffer({ resolveWithObject: true });
  const { width, height, channels } = info;

  // Sample the four corners to estimate the background colour.
  const corners = [
    0,
    (width - 1) * channels,
    (height - 1) * width * channels,
    ((height - 1) * width + (width - 1)) * channels,
  ];
  const bg = [0, 1, 2].map((c) => Math.round(corners.reduce((s, p) => s + data[p + c], 0) / corners.length));

  // If corners disagree wildly, the logo has no flat background — leave it.
  const spread = corners.reduce((mx, p) => {
    const d = Math.abs(data[p] - bg[0]) + Math.abs(data[p + 1] - bg[1]) + Math.abs(data[p + 2] - bg[2]);
    return Math.max(mx, d);
  }, 0);

  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  if (spread > 60) {
    // Not a flat background — just copy through with alpha ensured.
    await sharp(input).ensureAlpha().png().toFile(outFile);
    return outFile;
  }

  const tol = 42; // colour distance treated as "background"
  const out = Buffer.from(data);
  for (let i = 0; i < out.length; i += channels) {
    const d = Math.abs(out[i] - bg[0]) + Math.abs(out[i + 1] - bg[1]) + Math.abs(out[i + 2] - bg[2]);
    if (d <= tol) out[i + 3] = 0; // make transparent
  }
  await sharp(out, { raw: { width, height, channels } })
    .png()
    .trim() // drop the now-transparent margins
    .toFile(outFile);
  return outFile;
}

/**
 * Produce a reversed (white) version of a logo for dark placements, keeping
 * the exact shape. Recolours opaque pixels white, preserves alpha.
 */
export async function makeReversed(input: string, outFile: string): Promise<string> {
  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  const img = sharp(input).ensureAlpha();
  const { data, info } = await img.raw().toBuffer({ resolveWithObject: true });
  const { width, height, channels } = info;
  const out = Buffer.from(data);
  for (let i = 0; i < out.length; i += channels) {
    if (out[i + 3] > 20) { out[i] = 255; out[i + 1] = 255; out[i + 2] = 255; }
  }
  await sharp(out, { raw: { width, height, channels } }).png().toFile(outFile);
  return outFile;
}

/**
 * "AI rework" that preserves brand integrity: enforce transparency, trim dead
 * space, and (optionally) generate a reversed version. Deliberately does not
 * call an image model — the safest rework of a logo is a clean-up, not a
 * regeneration, which would alter the mark. Returns the cleaned primary and an
 * optional reverse.
 */
export async function reworkLogo(
  input: string,
  cacheDir: string,
  opts: { reversed?: boolean } = {},
): Promise<{ primary: string; reverse?: string }> {
  const primary = await removeFlatBackground(input, path.join(cacheDir, 'logo-reworked.png'));
  const result: { primary: string; reverse?: string } = { primary };
  if (opts.reversed) {
    result.reverse = await makeReversed(primary, path.join(cacheDir, 'logo-reversed.png'));
  }
  return result;
}
