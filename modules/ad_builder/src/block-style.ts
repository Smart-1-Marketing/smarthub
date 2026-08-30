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
  /**
   * The ink for this block: a literal hex, or one of the brand's five colour
   * names. A name is the better answer where it fits, because it follows the
   * client's palette when somebody corrects a swatch; a hex is what a colour
   * picker produces and so has to be accepted too.
   *
   * Deliberately NOT applied over a full-bleed background photo. The composer
   * forces an ink that survives the overlay there, and a chosen colour that
   * the overlay swallows is the one change that looks right in the panel and
   * ships an unreadable ad -- so on a photo background this is ignored and the
   * panel says so rather than pretending.
   */
  color?: string;
  /** Block width in px at 1x — where the line wraps. */
  w?: number;
  lineHeight?: number;
  /** CTA only: the button fill. */
  bg?: string;
  /**
   * CTA only: where the button sits.
   *
   * Type is not movable — moving a block of copy is a layout decision, and
   * layouts are chosen by picking a family — but the button is the same
   * exception the logo is. "Nudge the button" and "centre the button" are
   * both notes people write, and neither had anywhere to go: `align` set the
   * label's alignment inside the button, which the templates already centre,
   * so choosing Center appeared to do nothing at all.
   */
  x?: number;
  y?: number;
}

/** The brand's five roles, plus any literal hex. */
export const COLOR_NAMES = ['primary', 'secondary', 'accent', 'light', 'dark'] as const;

/**
 * A colour this renderer will actually resolve, or nothing.
 *
 * `resolveColor` falls back to a default for anything it does not recognise,
 * so an unvalidated value renders as near-black while the control shows the
 * colour that was asked for -- the ad is wrong and the panel says it is right.
 * Same rule the font check above follows, for the same reason.
 */
export function resolveStyleColor(value: string | undefined): string | null {
  const v = String(value ?? '').trim();
  if (!v) return null;
  if ((COLOR_NAMES as readonly string[]).includes(v)) return v;
  // Kept exactly as written rather than normalised to a case. SVG does not
  // care, and rewriting what somebody typed means the value they read back
  // out of a saved campaign is not the value they put in.
  if (/^#[0-9a-fA-F]{3}$/.test(v) || /^#[0-9a-fA-F]{6}$/.test(v)) return v;
  return null;
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
  /**
   * Bigger or smaller, proportionally, as a multiple of the template's box.
   *
   * Two numbers for width and height is the wrong control for "make the logo
   * bigger": it is two fields to keep in step, and getting them out of step
   * does nothing visible (the renderer contains the mark inside the box
   * preserving aspect) right up until it crops. One slider scales both, and
   * the numbers stay available behind Advanced for the rare case where the
   * box itself is the thing that needs reshaping.
   *
   * An explicit w or h wins over this, so Advanced is genuinely advanced
   * rather than a second control fighting the first.
   */
  scale?: number;
}

/** A logo below a third of the template's box is a smudge; above three times
 *  it is the ad. */
export const MIN_LOGO_SCALE = 0.3;
export const MAX_LOGO_SCALE = 3;

/**
 * The card the copy sits on.
 *
 * "Full background with copy panel" puts the headline on a filled panel over
 * the photograph, and the fill is the single thing that decides whether that
 * copy can be read. It was a template constant — brand primary at 88% — so a
 * client whose primary is a mid-grey got copy nobody could read and there was
 * no control anywhere that could change it.
 *
 * Applies to every panel the layout draws. Each family that has one has
 * exactly one, and it is always the card behind the copy; a family that grows
 * a second decorative panel should name them rather than making this pick.
 */
export interface PanelStyle {
  /** Brand colour name or hex. */
  fill?: string;
  /** 0..1. Below about 0.5 the photograph behind starts to win. */
  opacity?: number;
}

export type StyleOverrides = Partial<Record<StyleableBlock, BlockStyle>> & {
  logo?: LogoStyle;
  panel?: PanelStyle;
};

/** A logo smaller than this is not a logo, it is a smudge. */
export const MIN_LOGO = 8;

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

/** The region content is meant to stay inside — the same one QA measures. */
function safeRegion(layout: SizeLayout): { x: number; y: number; w: number; h: number } {
  if (layout.safeBox) return { ...layout.safeBox };
  const m = layout.safe ?? 0;
  return { x: m, y: m, w: layout.canvas.w - m * 2, h: layout.canvas.h - m * 2 };
}

/** Where a box of width `w` sits when aligned within `region`. */
function alignedX(w: number, region: { x: number; w: number }, align: HAlign): number {
  if (align === 'center') return Math.round(region.x + (region.w - w) / 2);
  if (align === 'right') return Math.round(region.x + region.w - w);
  return Math.round(region.x);
}

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
    // Align means two different things, and conflating them is why choosing
    // Center on the button did nothing. For type it is where the line sits in
    // its box. For the button it is where the BUTTON sits — the label inside
    // it is centred by the template already, so aligning that was a control
    // whose every setting looked identical.
    if (style.align) {
      if (key === 'cta') {
        const region = safeRegion(layout);
        patched.x = alignedX(box.w, region, style.align);
      } else {
        patched.align = style.align;
      }
      changed = true;
    }

    const ink = resolveStyleColor(style.color);
    if (ink) { patched.color = ink; changed = true; }

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
    if (key === 'cta') {
      const fill = resolveStyleColor(style.bg);
      if (fill) { patched.bg = fill; changed = true; }
      // ...and only the CTA may be moved. Clamped so a nudge cannot walk the
      // button off the canvas, which is the one thing an arrow pad invites.
      if (typeof style.x === 'number' && Number.isFinite(style.x)) {
        patched.x = clamp(Math.round(style.x), 0, Math.max(0, canvas.w - box.w));
        changed = true;
      }
      if (typeof style.y === 'number' && Number.isFinite(style.y)) {
        patched.y = clamp(Math.round(style.y), 0, Math.max(0, canvas.h - box.h));
        changed = true;
      }
    }

    if (changed) {
      (next as any)[key] = patched;
      touched = true;
    }
  }

  // The card the copy sits on. Every panel the layout draws, because each
  // family that has one has exactly one and it is always that card.
  const panel = overrides.panel;
  if (panel && layout.panels?.length) {
    const fill = resolveStyleColor(panel.fill);
    const hasOpacity = typeof panel.opacity === 'number' && Number.isFinite(panel.opacity);
    if (fill || hasOpacity) {
      next.panels = layout.panels.map((p) => {
        const q: any = { ...p };
        if (fill) q.fill = fill;
        if (hasOpacity) q.opacity = clamp(panel.opacity as number, 0, 1);
        return q;
      });
      touched = true;
    }
  }

  const logo = overrides.logo;
  if (logo && layout.logo) {
    const lb: any = { ...layout.logo };
    let changed = false;

    // Proportional resize first, so an explicit w/h below still wins over it.
    if (typeof logo.scale === 'number' && Number.isFinite(logo.scale) && logo.scale !== 1) {
      const k = clamp(logo.scale, MIN_LOGO_SCALE, MAX_LOGO_SCALE);
      lb.w = Math.max(MIN_LOGO, Math.round(layout.logo.w * k));
      lb.h = Math.max(MIN_LOGO, Math.round(layout.logo.h * k));
      changed = true;
    }

    // Position next, because the size clamps depend on where it ends up.
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
