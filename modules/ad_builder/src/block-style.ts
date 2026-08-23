/**
 * Per-block style overrides.
 *
 * The templates decide where the type sits and how big it is, and for most
 * campaigns that is right — the geometry came from the platform specs and the
 * creative research, not from taste. But a real headline sometimes needs to be
 * a little smaller, a CTA sometimes needs to be centred, and a button
 * sometimes needs to be a different colour than the accent.
 *
 * So the template stays the default and a concept may carry an override on top
 * of it. Two rules make that safe rather than a way to break the ad:
 *
 * **Only the properties a person is actually choosing.** For type that means
 * no x, no y, no height: moving a block of copy is a layout decision, and
 * layouts are chosen by picking a family. The logo is the deliberate
 * exception — see LogoStyle for why.
 *
 * **Everything is clamped to the canvas.** A width that runs the text off the
 * edge is the one change that looks fine in the control panel and produces a
 * clipped ad, so a width is bounded by what is left between the block's x and
 * the canvas edge, and type sizes are bounded to a range that can still be
 * measured and fitted.
 *
 * Applied in render.ts immediately after the layout is loaded, so both the
 * preview and the final render see exactly the same boxes.
 */

import { fontIsAvailable } from './fonts';
import type { HAlign, SizeLayout, TextBox, VAlign, Weight } from './types';

/** The blocks a person can restyle. Geometry-only blocks are deliberately out. */
export const STYLEABLE = ['headline', 'support', 'offer', 'cta', 'trust'] as const;
export type StyleableBlock = (typeof STYLEABLE)[number];

/** Type below this cannot be read in a banner; above it nothing fits. */
export const MIN_TYPE = 8;
export const MAX_TYPE = 96;

export interface BlockStyle {
  /** Largest type size in px at 1x. Autofit still steps down to fit. */
  size?: number;
  /**
   * Family for this block only. Blank means the brand's face for this role.
   * A family the renderer does not have is DROPPED rather than passed on:
   * resolveFont falls back predictably, so an unavailable name would render
   * as Montserrat while the control still showed the name that was asked for
   * -- the ad looks wrong and the panel says it is right.
   */
  font?: string;
  weight?: Weight;
  align?: HAlign;
  /** Block width in px at 1x — where the line wraps. */
  w?: number;
  lineHeight?: number;
  /** CTA only: the button fill. */
  bg?: string;
}

/**
 * The logo is the one block a person may move.
 *
 * For type, moving a block is a layout decision and layouts are chosen by
 * picking a family. The logo is different: it is the client's mark, its
 * correct placement depends on the picture behind it, and "nudge the logo"
 * is the single most common note on a proof. So position and size are both
 * allowed here, and both are clamped to the canvas.
 *
 * The box is a bounding box, not the drawn size: the renderer contains the
 * logo inside it, preserving aspect. Making the box bigger makes the logo
 * bigger only up to its own proportions.
 */
export interface LogoStyle {
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  align?: HAlign;
  valign?: VAlign;
}

export type StyleOverrides = Partial<Record<StyleableBlock, BlockStyle>> & {
  logo?: LogoStyle;
};

/** A logo smaller than this is not a logo, it is a smudge. */
export const MIN_LOGO = 8;

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

/**
 * A copy of `layout` with the overrides applied, clamped to the canvas.
 *
 * Pure: never mutates the template, because templates are loaded once and
 * shared by every concept in the process. Mutating one here would restyle
 * everybody's ads.
 */
export function applyBlockStyles(
  layout: SizeLayout,
  overrides: StyleOverrides | undefined,
): SizeLayout {
  if (!overrides) return layout;
  const canvas = layout.canvas;
  if (!canvas?.w) return layout;

  let touched = false;
  const next: SizeLayout = { ...layout };

  for (const key of STYLEABLE) {
    const style = overrides[key];
    const box = (layout as any)[key] as TextBox | undefined;
    if (!style || !box) continue;

    const patched: any = { ...box };
    let changed = false;

    if (typeof style.size === 'number' && Number.isFinite(style.size)) {
      const max = clamp(Math.round(style.size), MIN_TYPE, MAX_TYPE);
      // The template's pair is [floor, ceiling] and autofit walks down from the
      // ceiling. Keep a floor that is still below the new ceiling, or autofit
      // has no room to shrink and long copy overflows instead of fitting.
      const floor = Math.min(Array.isArray(box.size) ? box.size[0] : max, max);
      patched.size = [Math.max(MIN_TYPE, floor), max];
      changed = true;
    }

    if (style.font && fontIsAvailable(style.font)) {
      patched.font = style.font.trim();
      changed = true;
    }

    if (style.weight) { patched.weight = style.weight; changed = true; }
    if (style.align) { patched.align = style.align; changed = true; }

    if (typeof style.lineHeight === 'number' && Number.isFinite(style.lineHeight)) {
      patched.lineHeight = clamp(style.lineHeight, 0.8, 2.5);
      changed = true;
    }

    if (typeof style.w === 'number' && Number.isFinite(style.w)) {
      // Bounded by what is actually left on the canvas from this block's x.
      // A width that overruns the edge is the change that looks correct in a
      // panel and ships a clipped ad.
      const room = Math.max(1, canvas.w - (box.x ?? 0));
      patched.w = clamp(Math.round(style.w), 1, room);
      changed = true;
    }

    // Only the CTA has a fill; on anything else a background is not a thing
    // the composer draws, so accepting it would be a control that does nothing.
    if (style.bg && key === 'cta') { patched.bg = style.bg; changed = true; }

    if (changed) {
      (next as any)[key] = patched;
      touched = true;
    }
  }

  const logo = overrides.logo;
  if (logo && layout.logo) {
    const lb: any = { ...layout.logo };
    let changed = false;

    // Position first, because the size clamps depend on where it ends up.
    if (typeof logo.x === 'number' && Number.isFinite(logo.x)) {
      lb.x = clamp(Math.round(logo.x), 0, Math.max(0, canvas.w - MIN_LOGO));
      changed = true;
    }
    if (typeof logo.y === 'number' && Number.isFinite(logo.y)) {
      lb.y = clamp(Math.round(logo.y), 0, Math.max(0, canvas.h - MIN_LOGO));
      changed = true;
    }
    if (typeof logo.w === 'number' && Number.isFinite(logo.w)) {
      lb.w = clamp(Math.round(logo.w), MIN_LOGO, Math.max(MIN_LOGO, canvas.w - lb.x));
      changed = true;
    }
    if (typeof logo.h === 'number' && Number.isFinite(logo.h)) {
      lb.h = clamp(Math.round(logo.h), MIN_LOGO, Math.max(MIN_LOGO, canvas.h - lb.y));
      changed = true;
    }
    // A move can leave the existing box hanging off the edge even when the
    // size was not touched, so the box is re-fitted either way.
    if (changed) {
      lb.w = clamp(lb.w, MIN_LOGO, Math.max(MIN_LOGO, canvas.w - lb.x));
      lb.h = clamp(lb.h, MIN_LOGO, Math.max(MIN_LOGO, canvas.h - lb.y));
    }
    if (logo.align) { lb.align = logo.align; changed = true; }
    if (logo.valign) { lb.valign = logo.valign; changed = true; }

    if (changed) { next.logo = lb; touched = true; }
  }

  return touched ? next : layout;
}
