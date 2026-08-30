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
export interface FlatBackdrop {
  /** The plate colour, as the render will show it. */
  rgb: [number, number, number];
  /** Rec. 709 luma of that colour, 0-1, for comparing against a panel. */
  luminance: number;
}

/**
 * Does this logo sit on a flat OPAQUE plate, and what colour is it?
 *
 * One reading, because two call sites ask it and they must not disagree:
 * removeFlatBackground() strips the plate, and QA warns that it is about to
 * show as a box. The opacity half is the part that was missing, and it cost
 * both of them.
 *
 * The corner sample used to run over the ensureAlpha() buffer without asking
 * whether the corners were opaque. On a logo that is ALREADY transparent
 * those pixels are (0,0,0,alpha 0) -- so the corners agree perfectly, the
 * spread test passes, and black is taken for the background colour. Every
 * near-black pixel in the mark is then made transparent. A #0a0a0a wordmark
 * on a transparent canvas comes back as a fully transparent PNG, from a
 * route that reports success: the rework tool erases the logo it was asked
 * to clean up. #111111 survives only because it happens to sit 51 units of
 * colour distance from black and the tolerance is 42, which is luck rather
 * than a rule.
 *
 * So a transparent corner means there is no plate. Nothing to strip, and
 * nothing for QA to warn about.
 */
export async function flatBackdrop(file: string): Promise<FlatBackdrop | null> {
  try {
    const { data, info } = await sharp(file).ensureAlpha().raw()
      .toBuffer({ resolveWithObject: true });
    return backdropOf(data, info.width, info.height, info.channels);
  } catch {
    // Unreadable is not "no plate" for a caller that wants to know -- but it
    // is the only safe answer here, since both callers treat null as "leave
    // it alone", which is the direction that changes nothing.
    return null;
  }
}

function backdropOf(data: Buffer | Uint8Array, width: number, height: number,
                    channels: number): FlatBackdrop | null {
  const corners = [
    0,
    (width - 1) * channels,
    (height - 1) * width * channels,
    ((height - 1) * width + (width - 1)) * channels,
  ];

  // A transparent corner means the logo already carries its own transparency.
  if (corners.some((p) => data[p + 3] < 250)) return null;

  const bg = [0, 1, 2].map((c) =>
    Math.round(corners.reduce((s, p) => s + data[p + c], 0) / corners.length));

  // If corners disagree wildly, the logo has no flat background -- leave it.
  const spread = corners.reduce((mx, p) => {
    const d = Math.abs(data[p] - bg[0]) + Math.abs(data[p + 1] - bg[1]) + Math.abs(data[p + 2] - bg[2]);
    return Math.max(mx, d);
  }, 0);
  if (spread > 60) return null;

  return {
    rgb: [bg[0], bg[1], bg[2]],
    luminance: (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) / 255,
  };
}

export async function removeFlatBackground(input: string, outFile: string): Promise<string> {
  const img = sharp(input).ensureAlpha();
  const { data, info } = await img.raw().toBuffer({ resolveWithObject: true });
  const { width, height, channels } = info;

  const plate = backdropOf(data, width, height, channels);

  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  if (!plate) {
    // No flat opaque plate -- a photographic logo, or one that is already
    // transparent. Copy through with alpha ensured rather than eating the mark.
    await sharp(input).ensureAlpha().png().toFile(outFile);
    return outFile;
  }
  const bg = plate.rgb;

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
