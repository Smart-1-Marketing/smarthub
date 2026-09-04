/**
 * Carrying one size's hand adjustments to the rest of the set.
 *
 * The Display Ad Builder renders every size from its own hand-authored layout,
 * and that is right: a 970x250 is a different composition from a 300x250, not a
 * stretched one. What an operator does on top of those layouts -- nudge the
 * button, size the headline, scale the logo -- lives in `styleOverrides`, which
 * is per CONCEPT and therefore reaches every size in the set.
 *
 * It reached them as raw pixels. An operator who perfected the 300x250 was
 * silently rewriting the other ten, and every one of them still rendered, still
 * passed the platform's minimum type size, and still went into the delivery
 * pack. Measured against T01 on this repo's own templates, one ordinary tuning
 * pass:
 *
 *     728x90     the logo went from 46px tall to 8px -- MIN_LOGO, the floor
 *                block-style.ts's own constant calls a smudge
 *     970x250    headline type from [32,44] to [18,18], on a billboard
 *     1080x1920  the button from y=1118 to y=200 -- out of the end card and
 *                into the middle of the hero photograph
 *
 * Nothing errored, and each size was internally consistent, which is why it
 * survived: you have to open the other ten to see it.
 *
 * ---------------------------------------------------------------------------
 * The rule: a departure carries, a pixel does not
 *
 * Every size already has a considered layout. So an override is not a position
 * on a canvas -- it is a DEPARTURE from that canvas's own template, and what
 * carries is the departure, expressed in the target's own terms:
 *
 *   * A dimension carries as a RATIO to the template's own. "The headline is a
 *     tenth bigger than default" survives onto a billboard whose default is
 *     already twice the size; "the headline is 20px" does not.
 *   * A position carries as a FRACTION OF THE FRAME, added to the target's own
 *     template position. Nudging the button 70px right on a 300-wide ad means
 *     "about a quarter of the way", and a quarter of the way is what a wider
 *     canvas gets -- rather than the button abandoning the composition its own
 *     layout put it in.
 *
 * The first draft of this scaled everything by the smaller axis ratio, which is
 * what `modules/magic_resize/engine.py` does and is right THERE: that tool
 * resizes one design into empty frames, with no target layout to depart from.
 * Here it is a second wrong answer replacing the first -- 300x250 to 728x90 is
 * a factor of 0.36, so it drove the headline to the 8px floor and put the logo
 * back at the smudge. Measured, then changed. The two tools share the idea and
 * deliberately not the arithmetic, because they are not solving the same
 * problem: `test_display_ads.py` asserts the difference rather than leaving the
 * next reader to assume a shared engine that is not there.
 *
 * ---------------------------------------------------------------------------
 * What does not carry as geometry
 *
 * Font, weight, colour, alignment, line height, the CTA fill and the panel are
 * decisions about the brand rather than about a canvas. They mean the same
 * thing at every size, and are carried verbatim exactly as they were before.
 * `logo.scale` is already a multiple of the template's own box -- a departure
 * by construction -- so it carries untouched; converting a proportion into
 * pixels and back is how a control that worked starts drifting.
 *
 * ---------------------------------------------------------------------------
 * Four rules that keep this from being worse than the bug
 *
 *   **No `authoredFor`, no carry.** Every concept saved before this existed
 *   carries pixels with no record of the canvas they were drawn against, and
 *   guessing one would rewrite ads that have already been approved and
 *   delivered. Those resolve exactly as they did. The `backgroundPosition`
 *   precedent in types.ts: superseded, kept, read only when the newer field is
 *   absent.
 *
 *   **A per-size correction is never carried anywhere.** `bySize` is authored
 *   against the size it names and merges last, so opening a flagged size and
 *   nudging it fixes that size alone. Without it the marking leads nowhere: the
 *   correction would propagate straight back out over the set and the size that
 *   had just been got right would be the next one broken. The shape
 *   `concept.copy` already uses, because it is the same question one field over.
 *
 *   **A carry that would not fit is reported, not hidden.** `applyBlockStyles`
 *   clamps to the canvas, which is right and is also silent. Where the clamp
 *   bites, the departure has not survived and this says so -- that size is the
 *   one somebody has to look at, which is the whole point of carrying at all.
 *
 *   **Geometry with no frame to come from is dropped, never pasted.** If the
 *   authored layout is gone -- a family switched after tuning -- the target's
 *   own composition stands. Pasting the pixels is the defect, and keeping them
 *   silently would be it wearing a fix.
 */

import type { SizeKey, TemplateSpec } from './types';
import type { BlockStyle, LogoStyle, SizeStyle, StyleOverrides } from './block-style';
import { MAX_TYPE, MIN_TYPE, STYLEABLE } from './block-style';

export interface Frame { w: number; h: number }

type AnyBox = { x?: number; y?: number; w?: number; h?: number; size?: number | [number, number] };

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

function boxOf(layout: unknown, role: string): AnyBox | null {
  const b = (layout as Record<string, unknown> | undefined)?.[role] as AnyBox | undefined;
  return b && typeof b === 'object' ? b : null;
}

function frameOf(layout: unknown): Frame | null {
  const c = (layout as { canvas?: Frame } | undefined)?.canvas;
  return c && c.w > 0 && c.h > 0 ? { w: c.w, h: c.h } : null;
}

/** A template's type ceiling for a block: `size` is [floor, ceiling]. */
function ceilingOf(box: AnyBox | null): number | null {
  const s = box?.size;
  if (Array.isArray(s)) return num(s[1]) ? s[1] : null;
  return num(s) ? s : null;
}

/**
 * A ratio to the template's own value, so "a tenth bigger than default" is what
 * travels. Guarded against a template value of zero, which would make every
 * departure infinite.
 */
export function departureRatio(authored: number, template: number | null): number | null {
  if (!num(template) || template <= 0) return null;
  return authored / template;
}

/**
 * A move, as a fraction of the frame it was made in.
 *
 * Nudging 70px on a 300-wide ad is "about a quarter of the way across", which is
 * a thing another canvas can honour. 70px is not.
 */
export function departureShift(authored: number, template: number, extent: number): number {
  if (!(extent > 0)) return 0;
  return (authored - template) / extent;
}

/* ------------------------------------------------------------------ resolve */

/** What the carry did to one size, so a reviewer is told rather than left to
 *  notice. Read by `qa.ts` and drawn on the build screen's size rail. */
export interface CarryReport {
  /** The size these adjustments were authored on. Absent when none were. */
  from?: SizeKey;
  /** True when this size's geometry was carried from another canvas. */
  carried: boolean;
  /** Roles the carry adjusted. */
  moved: string[];
  /** Roles whose carried value would not fit and was pulled back to the edge. */
  strained: string[];
  /** Roles whose geometry had no frame to come from and was dropped. */
  dropped: string[];
  /** True when this size carries a correction authored against itself. */
  corrected: boolean;
}

const GEOM_KEYS = ['x', 'y', 'w', 'h', 'size'] as const;

/** The non-geometric half of a style: what means the same thing on any canvas. */
function styleOnly<T extends object>(style: T): { rest: Partial<T>; hadGeometry: boolean } {
  const rest: Record<string, unknown> = {};
  let hadGeometry = false;
  for (const [k, v] of Object.entries(style)) {
    if ((GEOM_KEYS as readonly string[]).includes(k)) {
      if (num(v)) hadGeometry = true;
      continue;
    }
    rest[k] = v;
  }
  return { rest: rest as Partial<T>, hadGeometry };
}

function carryBlock(
  style: BlockStyle, from: AnyBox, to: AnyBox, fromFrame: Frame, toFrame: Frame,
): { out: BlockStyle; moved: boolean; strained: boolean } {
  const out: BlockStyle = { ...style };
  let moved = false;
  let strained = false;

  if (num(style.size)) {
    const ratio = departureRatio(style.size, ceilingOf(from));
    const target = ceilingOf(to);
    if (ratio !== null && num(target)) {
      const want = Math.round(target * ratio);
      out.size = want;
      // The clamp in applyBlockStyles is silent, so the strain is recorded here
      // while the number it would refuse is still in hand.
      if (want < MIN_TYPE || want > MAX_TYPE) strained = true;
      moved = true;
    } else {
      delete out.size;
    }
  }

  if (num(style.w)) {
    const ratio = departureRatio(style.w, num(from.w) ? from.w : null);
    if (ratio !== null && num(to.w)) {
      const want = Math.round(to.w * ratio);
      out.w = want;
      if (num(to.x) && to.x + want > toFrame.w) strained = true;
      moved = true;
    } else {
      delete out.w;
    }
  }

  if (num(style.x)) {
    if (num(from.x) && num(to.x)) {
      const want = Math.round(to.x + departureShift(style.x, from.x, fromFrame.w) * toFrame.w);
      out.x = want;
      const room = toFrame.w - (num(out.w) ? out.w : (num(to.w) ? to.w : 0));
      if (want < 0 || want > room) strained = true;
      moved = true;
    } else {
      delete out.x;
    }
  }

  if (num(style.y)) {
    if (num(from.y) && num(to.y)) {
      const want = Math.round(to.y + departureShift(style.y, from.y, fromFrame.h) * toFrame.h);
      out.y = want;
      const room = toFrame.h - (num(to.h) ? to.h : 0);
      if (want < 0 || want > room) strained = true;
      moved = true;
    } else {
      delete out.y;
    }
  }

  return { out, moved, strained };
}

function carryLogo(
  style: LogoStyle, from: AnyBox, to: AnyBox, fromFrame: Frame, toFrame: Frame,
): { out: LogoStyle; moved: boolean; strained: boolean } {
  // `scale` is left exactly as it is: it is already a multiple of whatever box
  // this size's own template draws, which is what every other value here is
  // being converted into.
  const out: LogoStyle = { ...style };
  let moved = false;
  let strained = false;

  for (const dim of ['w', 'h'] as const) {
    const v = style[dim];
    if (!num(v)) continue;
    const ratio = departureRatio(v, num(from[dim]) ? (from[dim] as number) : null);
    if (ratio !== null && num(to[dim])) {
      out[dim] = Math.round((to[dim] as number) * ratio);
      moved = true;
    } else {
      delete out[dim];
    }
  }

  for (const [axis, extentFrom, extentTo] of
       [['x', fromFrame.w, toFrame.w], ['y', fromFrame.h, toFrame.h]] as const) {
    const v = style[axis];
    if (!num(v)) continue;
    if (num(from[axis]) && num(to[axis])) {
      const want = Math.round((to[axis] as number) +
                              departureShift(v, from[axis] as number, extentFrom) * extentTo);
      out[axis] = want;
      if (want < 0 || want > extentTo) strained = true;
      moved = true;
    } else {
      delete out[axis];
    }
  }

  return { out, moved, strained };
}

function resolve(
  overrides: StyleOverrides | undefined,
  template: TemplateSpec,
  size: SizeKey,
): { style: StyleOverrides | undefined; report: CarryReport } {
  const empty: CarryReport = {
    carried: false, moved: [], strained: [], dropped: [], corrected: false,
  };
  if (!overrides) return { style: undefined, report: empty };

  const { authoredFor, bySize, ...base } = overrides;
  const correction = bySize?.[size];
  const report: CarryReport = {
    from: authoredFor, carried: false, moved: [], strained: [], dropped: [],
    corrected: !!correction && Object.keys(correction).length > 0,
  };

  let carried: SizeStyle = base;

  if (authoredFor && authoredFor !== size) {
    report.carried = true;
    const sizes = template.sizes as Record<string, unknown>;
    const fromLayout = sizes[authoredFor];
    const toLayout = sizes[size];
    const fromFrame = frameOf(fromLayout);
    const toFrame = frameOf(toLayout);
    const next: SizeStyle = {};

    const dropGeometry = (role: string, style: object) => {
      const { rest, hadGeometry } = styleOnly(style);
      if (hadGeometry) report.dropped.push(role);
      if (Object.keys(rest).length) (next as Record<string, unknown>)[role] = rest;
    };

    for (const role of STYLEABLE) {
      const st = base[role];
      if (!st) continue;
      const b = boxOf(fromLayout, role);
      const t = boxOf(toLayout, role);
      if (!fromFrame || !toFrame || !b || !t) { dropGeometry(role, st); continue; }
      const { out, moved, strained } = carryBlock(st, b, t, fromFrame, toFrame);
      next[role] = out;
      if (moved) report.moved.push(role);
      if (strained) report.strained.push(role);
    }

    if (base.logo) {
      const b = boxOf(fromLayout, 'logo');
      const t = boxOf(toLayout, 'logo');
      if (!fromFrame || !toFrame || !b || !t) { dropGeometry('logo', base.logo); }
      else {
        const { out, moved, strained } = carryLogo(base.logo, b, t, fromFrame, toFrame);
        next.logo = out;
        if (moved) report.moved.push('logo');
        if (strained) report.strained.push('logo');
      }
    }

    if (base.panel) next.panel = base.panel;
    carried = next;
  }

  if (!correction) return { style: carried as StyleOverrides, report };

  // A correction is authored against this size, so it lands verbatim on top --
  // merged per block, so correcting the headline does not drop a carried CTA.
  const merged: Record<string, unknown> = { ...(carried as Record<string, unknown>) };
  for (const [key, patch] of Object.entries(correction)) {
    if (!patch) continue;
    merged[key] = { ...((merged[key] as object) ?? {}), ...(patch as object) };
  }
  return { style: merged as StyleOverrides, report };
}

/**
 * The style this size actually renders with.
 *
 * `copyForSize` one field over: the concept carries the answer, a size may
 * override it, and every render path asks this rather than reading the raw
 * overrides -- so the preview and the delivered file cannot disagree about what
 * was approved on screen.
 */
export function styleForSize(
  overrides: StyleOverrides | undefined,
  template: TemplateSpec,
  size: SizeKey,
): StyleOverrides | undefined {
  return resolve(overrides, template, size).style;
}

/** What the carry did to this size, for the QA panel and the size rail. */
export function carriedInto(
  overrides: StyleOverrides | undefined,
  template: TemplateSpec,
  size: SizeKey,
): CarryReport {
  return resolve(overrides, template, size).report;
}

/** True when this size is worth a person's eye before it ships. */
export function needsReview(report: CarryReport): boolean {
  return report.carried && !report.corrected &&
         (report.strained.length > 0 || report.dropped.length > 0);
}
