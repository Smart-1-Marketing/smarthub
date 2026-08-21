/**
 * Typesetting: wrap and autofit copy into a fixed box.
 *
 * The template declares a box and a legal size range. This walks the range
 * downward until the copy fits in the allowed number of lines. If it never
 * fits, we return the best attempt flagged `overflow: true` — QA turns that
 * into a machine-readable "shorten this role" instruction rather than
 * silently shipping clipped text.
 */

import type { Font } from 'opentype.js';
import { measure, metrics } from './fonts';

export interface FitOptions {
  font: Font;
  text: string;
  maxWidth: number;
  maxHeight: number;
  maxLines: number;
  /** [min, max] px */
  sizeRange: [number, number];
  lineHeight?: number;
  tracking?: number;
}

export interface FitResult {
  fontSize: number;
  lines: string[];
  lineHeight: number;
  /** Widest rendered line. */
  width: number;
  /** Total block height (lines * lineHeight). */
  height: number;
  overflow: boolean;
  /** How the copy exceeded the box, when it did. */
  reason?: 'lines' | 'height' | 'width';
}

/** Greedy wrap at a fixed size. Honours explicit \n. Never breaks mid-word. */
export function wrap(
  font: Font,
  text: string,
  size: number,
  maxWidth: number,
  tracking = 0,
): string[] {
  const out: string[] = [];
  for (const para of text.split('\n')) {
    const words = para.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) {
      out.push('');
      continue;
    }
    let line = words[0];
    for (let i = 1; i < words.length; i++) {
      const candidate = `${line} ${words[i]}`;
      if (measure(font, candidate, size, tracking) <= maxWidth) {
        line = candidate;
      } else {
        out.push(line);
        line = words[i];
      }
    }
    out.push(line);
  }
  return out;
}

export function fitText(opts: FitOptions): FitResult {
  const {
    font,
    text,
    maxWidth,
    maxHeight,
    maxLines,
    sizeRange,
    lineHeight = 1.2,
    tracking = 0,
  } = opts;

  const [min, max] = sizeRange;
  let last: FitResult | null = null;

  for (let size = max; size >= min; size -= 0.5) {
    const lines = wrap(font, text, size, maxWidth, tracking);
    const lh = size * lineHeight;
    const height = lines.length * lh;
    const width = Math.max(0, ...lines.map((l) => measure(font, l, size, tracking)));

    const tooManyLines = lines.length > maxLines;
    const tooTall = height > maxHeight + 0.5;
    // A single unbreakable word can still exceed the box width.
    const tooWide = width > maxWidth + 0.5;

    const result: FitResult = {
      fontSize: size,
      lines,
      lineHeight: lh,
      width,
      height,
      overflow: tooManyLines || tooTall || tooWide,
      reason: tooManyLines ? 'lines' : tooTall ? 'height' : tooWide ? 'width' : undefined,
    };

    if (!result.overflow) return result;
    last = result;
  }

  // Nothing fit. Return the smallest attempt so the proof still renders and
  // the failure is visible rather than hidden.
  return last!;
}

/** Baseline y positions for a fitted block, given vertical alignment. */
export function baselines(
  font: Font,
  fit: FitResult,
  boxY: number,
  boxH: number,
  valign: 'top' | 'middle' | 'bottom' = 'top',
): number[] {
  const m = metrics(font, fit.fontSize);
  const blockH = fit.height;
  let top = boxY;
  if (valign === 'middle') top = boxY + (boxH - blockH) / 2;
  if (valign === 'bottom') top = boxY + boxH - blockH;

  // Centre each line's ink within its line box.
  const inset = (fit.lineHeight - (m.ascender + m.descender)) / 2;
  return fit.lines.map((_, i) => top + i * fit.lineHeight + inset + m.ascender);
}

export function xForAlign(
  lineWidth: number,
  boxX: number,
  boxW: number,
  align: 'left' | 'center' | 'right' = 'left',
): number {
  if (align === 'center') return boxX + (boxW - lineWidth) / 2;
  if (align === 'right') return boxX + boxW - lineWidth;
  return boxX;
}

export function countWords(...parts: (string | undefined)[]): number {
  return parts
    .filter(Boolean)
    .join(' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean).length;
}
