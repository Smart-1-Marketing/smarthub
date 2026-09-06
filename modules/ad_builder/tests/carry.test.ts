/**
 * Carrying one size's adjustments to the rest of the set.
 *
 * The defect these are written against is not a crash. `styleOverrides` is per
 * concept and carried raw pixels, so an operator who perfected the 300x250 was
 * rewriting the other ten -- and every one of them still rendered, still passed
 * the platform's minimum type size, and still went into the delivery pack.
 * Measured against T01 on this repo's own templates, one ordinary tuning pass
 * put the 728x90's logo at 8px (MIN_LOGO, the smudge floor), the 970x250's
 * headline at [18,18] on a billboard, and the 1080x1920's button 900px above
 * where its layout puts it.
 *
 * So the assertions are about proportion and about what gets flagged, not about
 * whether anything threw.
 *
 * Run with: npm test
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  carriedInto, needsReview, styleForSize, departureRatio, departureShift,
} from '../src/carry';
import { applyBlockStyles, MIN_LOGO, MIN_TYPE } from '../src/block-style';
import { getTemplate } from '../src/registry';
import type { StyleOverrides } from '../src/block-style';
import type { TemplateSpec } from '../src/types';

const T = () => getTemplate('T01') as TemplateSpec;

/** One ordinary tuning pass on the 300x250: button nudged right and up, its
 *  label a little bigger, headline a tenth bigger, logo a quarter bigger. */
const tuned = (): StyleOverrides => ({
  authoredFor: '300x250',
  cta: { x: 90, y: 190, size: 16 },
  headline: { size: 20, w: 260 },
  logo: { x: 20, y: 106, w: 168, h: 25 },
});

const box = (t: TemplateSpec, size: string, role: string) =>
  (t.sizes as any)[size][role];

/* ------------------------------------------------------------- the arithmetic */

test('a departure is a ratio to the template, not the pixel that was typed', () => {
  assert.equal(departureRatio(20, 18)!.toFixed(4), (20 / 18).toFixed(4));
  // A template value of zero would make every departure infinite, so there is
  // no departure to carry rather than an Infinity to paste.
  assert.equal(departureRatio(20, 0), null);
  assert.equal(departureRatio(20, null), null);
});

test('a move is a fraction of the frame it was made in', () => {
  assert.equal(departureShift(90, 20, 300).toFixed(4), (70 / 300).toFixed(4));
  assert.equal(departureShift(90, 20, 0), 0);   // never a division by zero
});

/* ------------------------------------------------------------------ carrying */

test('no authoredFor carries nothing, so an old concept renders as it always did', () => {
  const legacy: StyleOverrides = { cta: { x: 90, y: 190 }, headline: { size: 20 } };
  const out = styleForSize(legacy, T(), '728x90' as any);
  assert.deepEqual(out, legacy);
  assert.equal(carriedInto(legacy, T(), '728x90' as any).carried, false);
});

test('the size it was authored on is left exactly alone', () => {
  const o = tuned();
  assert.deepEqual(styleForSize(o, T(), '300x250' as any), {
    cta: o.cta, headline: o.headline, logo: o.logo,
  });
  assert.equal(carriedInto(o, T(), '300x250' as any).carried, false);
});

test('type keeps each canvas its own typography, scaled by the departure', () => {
  const t = T();
  for (const size of ['728x90', '970x250', '1080x1920']) {
    const out = styleForSize(tuned(), t, size as any)!;
    const ceiling = (box(t, size, 'headline').size as [number, number])[1];
    // 20 against the 300x250's ceiling of 18 is a tenth bigger, and a tenth
    // bigger is what each canvas gets -- not 20px.
    assert.equal(out.headline!.size, Math.round(ceiling * (20 / 18)),
      `${size} headline type`);
    assert.ok(out.headline!.size! > MIN_TYPE,
      `${size} headline must not collapse to the floor`);
  }
});

test('the leaderboard logo is no longer driven to the smudge floor', () => {
  const t = T();
  const out = styleForSize(tuned(), t, '728x90' as any)!;
  const tmpl = box(t, '728x90', 'logo');
  assert.equal(out.logo!.w, Math.round(tmpl.w * (168 / 135)));
  assert.equal(out.logo!.h, Math.round(tmpl.h * (25 / 20)));
  assert.ok(out.logo!.h! > MIN_LOGO,
    'the defect this was written for: 20px on a 300x250 became 8px here');
  // And the rendered layout agrees, which is the half that ships.
  const laid: any = applyBlockStyles(box(t, '728x90', 'logo') && (t.sizes as any)['728x90'], out);
  assert.ok(laid.logo.h > MIN_LOGO);
});

test('a width equal to the template is no departure, so each size keeps its own', () => {
  const t = T();
  // The panel writes the current value into the override the moment a control
  // is touched, so "unchanged" is the commonest thing in here.
  for (const size of ['728x90', '970x250', '1080x1920']) {
    const out = styleForSize(tuned(), t, size as any)!;
    assert.equal(out.headline!.w, box(t, size, 'headline').w,
      `${size} keeps its own headline width`);
  }
});

test('a position moves from the target template, never from the source', () => {
  const t = T();
  const out = styleForSize(tuned(), t, '1080x1920' as any)!;
  const tmpl = box(t, '1080x1920', 'cta');
  // Nudged 70/300 right and 10/250 up, applied to THIS canvas's own button.
  assert.equal(out.cta!.x, Math.round(tmpl.x + (70 / 300) * 1080));
  assert.equal(out.cta!.y, Math.round(tmpl.y + (-10 / 250) * 1920));
});

/* -------------------------------------------------------------- the flagging */

test('a carry that will not fit is flagged, and one that fits is not', () => {
  const t = T();
  // The leaderboard's button already sits against its right edge, so a quarter
  // of the canvas to the right is a move it cannot make.
  const tight = carriedInto(tuned(), t, '728x90' as any);
  assert.equal(tight.carried, true);
  assert.equal(tight.from, '300x250');
  assert.ok(tight.strained.includes('cta'), 'the leaderboard has no room to move the button');
  assert.equal(needsReview(tight), true);

  // The story has room for the same move.
  const roomy = carriedInto(tuned(), t, '1080x1920' as any);
  assert.equal(roomy.carried, true);
  assert.deepEqual(roomy.strained, []);
  assert.equal(needsReview(roomy), false);
});

test('the flag names the roles, so a reviewer knows where to look', () => {
  const r = carriedInto(tuned(), T(), '728x90' as any);
  assert.ok(r.moved.includes('headline') && r.moved.includes('logo'));
});

/* ------------------------------------------------------- the per-size correction */

test('a correction lands on its own size and is carried nowhere', () => {
  const o = tuned();
  o.bySize = { '728x90': { cta: { x: 400 } } } as any;
  const t = T();

  assert.equal(styleForSize(o, t, '728x90' as any)!.cta!.x, 400);
  // Merged per block, so correcting the button does not drop the carried type.
  assert.ok(styleForSize(o, t, '728x90' as any)!.headline!.size! > MIN_TYPE);
  // ...and no other size hears about it.
  assert.notEqual(styleForSize(o, t, '970x250' as any)!.cta!.x, 400);
  assert.equal(styleForSize(o, t, '300x250' as any)!.cta!.x, 90);
});

test('a corrected size is no longer asking to be reviewed', () => {
  const o = tuned();
  o.bySize = { '728x90': { cta: { x: 400 } } } as any;
  const r = carriedInto(o, T(), '728x90' as any);
  assert.equal(r.corrected, true);
  assert.equal(needsReview(r), false, 'somebody has already looked at this one');
});

/* --------------------------------------------------------------- the refusals */

test('geometry with no frame to come from is dropped, never pasted', () => {
  const o = tuned();
  (o as any).authoredFor = '999x999';          // a family switched after tuning
  const out = styleForSize(o, T(), '728x90' as any)!;
  assert.equal(out.cta?.x, undefined, 'a pixel from a canvas we cannot see is not applied');
  assert.equal(out.headline?.size, undefined);
  const r = carriedInto(o, T(), '728x90' as any);
  assert.ok(r.dropped.length > 0, 'and it is reported rather than silently lost');
  assert.equal(needsReview(r), true);
});

test('what means the same on any canvas is carried verbatim', () => {
  const o: StyleOverrides = {
    authoredFor: '300x250',
    headline: { color: '#ff0000', weight: 'bold', align: 'center', lineHeight: 1.3 },
    cta: { bg: 'accent' },
    panel: { fill: 'primary', opacity: 0.9 },
    logo: { scale: 1.4 },
  };
  const out = styleForSize(o, T(), '970x250' as any)!;
  assert.equal(out.headline!.color, '#ff0000');
  assert.equal(out.headline!.weight, 'bold');
  assert.equal(out.headline!.align, 'center');
  assert.equal(out.headline!.lineHeight, 1.3);
  assert.equal(out.cta!.bg, 'accent');
  assert.deepEqual(out.panel, { fill: 'primary', opacity: 0.9 });
  // scale is already a multiple of this size's own box, so it is left alone --
  // turning it into pixels and back is how a control that worked starts drifting.
  assert.equal(out.logo!.scale, 1.4);
});

test('the whole set carries in proportion, and needs no clamp to fit', () => {
  const t = T();
  const sizes = Object.keys(t.sizes);
  assert.ok(sizes.length >= 10, 'this only means something across a real set');

  for (const size of sizes) {
    const out = styleForSize(tuned(), t, size as any)!;
    const report = carriedInto(tuned(), t, size as any);
    const canvas = (t.sizes as any)[size].canvas;

    // The departure, not the pixel. Asserted against every size rather than
    // three, because the first version of this checked only that type stayed
    // above MIN_TYPE -- which applyBlockStyles guarantees, so it was an
    // assertion that could not fail and it duly passed on the unfixed code.
    if (size !== '300x250') {
      const ceiling = (box(t, size, 'headline').size as [number, number])[1];
      assert.equal(out.headline!.size, Math.round(ceiling * (20 / 18)),
        `${size} headline type is a tenth over its own ceiling`);
      assert.equal(out.headline!.w, box(t, size, 'headline').w,
        `${size} keeps its own headline width`);
      assert.equal(out.logo!.w, Math.round(box(t, size, 'logo').w * (168 / 135)),
        `${size} logo is a quarter over its own box`);
    }

    // Anything the carry did NOT flag must already sit on the canvas, with no
    // help from the clamp. Asserting the clamped output instead would be
    // asserting applyBlockStyles, which cannot fail.
    if (!report.strained.includes('cta') && out.cta) {
      const b = box(t, size, 'cta');
      if (typeof out.cta.x === 'number') {
        assert.ok(out.cta.x >= 0 && out.cta.x + b.w <= canvas.w,
          `${size} carried the button onto the canvas unaided`);
      }
      if (typeof out.cta.y === 'number') {
        assert.ok(out.cta.y >= 0 && out.cta.y + b.h <= canvas.h,
          `${size} carried the button onto the canvas unaided`);
      }
    }
  }
});

test('the strain flag is measured, not assumed', () => {
  const t = T();
  // Nudged one pixel: nothing anywhere should have to be pulled back.
  const gentle: StyleOverrides = { authoredFor: '300x250', cta: { x: 21 } };
  for (const size of Object.keys(t.sizes)) {
    assert.deepEqual(carriedInto(gentle, t, size as any).strained, [],
      `${size} had room for a one-pixel nudge`);
  }
  // Nudged most of the way across a 300-wide ad: the narrow canvases cannot.
  const shove: StyleOverrides = { authoredFor: '300x250', cta: { x: 140 } };
  const strained = Object.keys(t.sizes)
    .filter((sz) => carriedInto(shove, t, sz as any).strained.length > 0);
  assert.ok(strained.length > 0, 'a shove that big does not fit everywhere');
  assert.ok(!strained.includes('300x250'), 'the size it was authored on is never strained');
});
