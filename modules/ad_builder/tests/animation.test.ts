/**
 * Animated banners: the arithmetic in `src/animation.ts`.
 *
 * Every published rule this file enforces (the 5fps floor, the 30-second
 * ceiling, the endless-loop trap) was checked so far only by `test_display_ads.py`
 * reading the source text -- does the string "200" appear near `minFrameMs`,
 * does the module export `planAnimation`. That proves the numbers are still
 * written down; it proves nothing about what `planAnimation()` actually
 * returns when handed a real spec. This drives the real functions.
 *
 * Two bugs turned up doing that, both in the same corner: a text animation
 * given more slides than the 3-slide cap AND a blank slide mixed in among
 * them. `usable.length + 1` for "how many were asked for" undercounts by
 * exactly the blank ones, already reported in the sentence above it -- and
 * naming which slide was "left out" by position in the filtered list rather
 * than the operator's own slide number pointed at the wrong slide entirely
 * (kept content renumbers to fill the gap a blank left; the dropped slide
 * does not move with it). Both fixed by carrying the real slide number
 * alongside each slide through the filter instead of recovering it from a
 * position that had already been decided by unrelated blanks.
 *
 * The second is smaller and found by the same read: `planAnimation`'s own
 * docstring says "every clamp is recorded rather than applied quietly", and
 * the button pulse's frame count enforced that going over the 5-frame
 * maximum and not going under the 3-frame minimum a pulse needs to have a
 * peak at all.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import sharp from 'sharp';
import {
  ANIMATION_RULES,
  DEFAULTS,
  loopsWithin,
  planAnimation,
  pulseColor,
  animationSupport,
  animatableSizes,
  encodeAnimation,
  animationFindings,
  repeatedSlides,
  slidesFor,
} from '../src/animation';
import type { CopySet } from '../src/types';

function flat(w: number, h: number, hex: string) {
  const [r, g, b] = [hex.slice(0, 2), hex.slice(2, 4), hex.slice(4, 6)].map((h) => parseInt(h, 16));
  return sharp({ create: { width: w, height: h, channels: 3, background: { r, g, b } } });
}

/* ------------------------------------------------------------- loopsWithin */

test('loopsWithin plays as many times as fit in 30s, and never zero', () => {
  assert.equal(loopsWithin(5_000), 6);
  assert.equal(loopsWithin(1_000), 30);
  // A cycle longer than the ceiling still plays once -- never the endless
  // loop:0 the file's whole docstring is about avoiding.
  assert.equal(loopsWithin(40_000), 1);
  assert.equal(loopsWithin(0), 1);
  assert.equal(loopsWithin(-5), 1);
});

/* --------------------------------------------------------------- slidesFor */

test('a per-size slide is merged field by field over the base, not swapped whole', () => {
  const spec = {
    kind: 'text' as const,
    slides: [{ headline: 'Base headline', support: 'Base support' } as Partial<CopySet>],
    sizeSlides: { '320x50': [{ headline: 'Short headline' }] },
  };
  const wide = slidesFor(spec, '300x250' as any);
  assert.deepEqual(wide, spec.slides, 'a size with no override gets the base slide');
  const narrow = slidesFor(spec, '320x50' as any);
  assert.equal(narrow[0].headline, 'Short headline', 'the override wins on the field it names');
  assert.equal(narrow[0].support, 'Base support', 'and the base still supplies what the override left out');
});

/* -------------------------------------------------------- planAnimation:text */

test('two real slides plan cleanly with no adjustments', () => {
  const plan = planAnimation(
    { kind: 'text', slides: [{ headline: 'Second thing' }] },
    {},
  );
  assert.equal(plan.refused, undefined);
  assert.equal(plan.frames.length, 2);
  assert.equal(plan.frames[0].label, 'Slide 1 — the ad as built');
  assert.equal(plan.frames[0].copy, undefined, 'slide 1 carries no patch -- it is the concept itself');
  assert.equal(plan.frames[1].copy!.headline, 'Second thing');
  assert.deepEqual(plan.adjustments, []);
});

test('a frame held under the 5fps floor is raised to it, and says so', () => {
  const plan = planAnimation(
    { kind: 'text', slides: [{ headline: 'x' }], frameMs: 80 },
    {},
  );
  assert.equal(plan.frames[0].ms, ANIMATION_RULES.minFrameMs);
  assert.ok(plan.adjustments.some((a) => a.includes('80ms') && a.includes('200ms')));
});

test('a frame held past the house ceiling is cut back, and says so', () => {
  const plan = planAnimation(
    { kind: 'text', slides: [{ headline: 'x' }], frameMs: 9_000 },
    {},
  );
  assert.equal(plan.frames[0].ms, ANIMATION_RULES.maxFrameMs);
  assert.ok(plan.adjustments.some((a) => a.includes('9000ms')));
});

test('a text animation with nothing typed on slide 2 refuses rather than animating nothing', () => {
  const plan = planAnimation({ kind: 'text', slides: [{}] }, {});
  assert.equal(plan.frames.length, 0);
  assert.ok(plan.refused?.includes('second slide'));
});

test('a blank slide is dropped and named as blank, not as over the limit', () => {
  const plan = planAnimation(
    { kind: 'text', slides: [{ headline: 'two' }, {}] },
    {},
  );
  assert.equal(plan.frames.length, 2, 'slide 1 and the one real extra slide');
  assert.deepEqual(plan.adjustments, ['1 slide(s) had nothing typed on them and were left out.']);
});

test('over the 3-slide cap: the true total, and which slides really got cut', () => {
  // No blanks -- the case the code always got right, kept as the baseline
  // the two bug-fix cases below are compared against.
  const plan = planAnimation(
    {
      kind: 'text',
      slides: [{ headline: 'two' }, { headline: 'three' }, { headline: 'four' }, { headline: 'five' }],
    },
    {},
  );
  assert.equal(plan.frames.length, ANIMATION_RULES.maxSlides, 'slide 1 plus the 2 extra this file allows');
  assert.deepEqual(plan.adjustments, [
    '5 slides were asked for; 3 is the most a banner carries here, so slides 4 and 5 were left out.',
  ]);
});

test('a blank slide ahead of the cutoff must not shift which slide is blamed', () => {
  // Regression for the actual bug: slides 2, 3(blank), 4, 5 asked for. Slide 3
  // is reported blank; of the three with real content only 2 fit, so slide 5
  // -- the operator's own numbering -- is the one left out. The old code
  // renumbered post-filter and blamed "slide 4" (which, per this same plan,
  // is what actually survived into the ad as its second extra slide).
  const plan = planAnimation(
    {
      kind: 'text',
      slides: [
        { headline: 'Slide two content' },
        {},
        { headline: 'Slide four content' },
        { headline: 'Slide five content' },
      ],
    },
    {},
  );
  assert.deepEqual(plan.adjustments, [
    '1 slide(s) had nothing typed on them and were left out.',
    '5 slides were asked for; 3 is the most a banner carries here, so slide 5 was left out.',
  ]);
  // And slide 4's content is what actually made the ad, as the plan's own
  // second extra slide -- proving "slide 4" in the old message pointed at
  // content that was never dropped at all.
  assert.equal(plan.frames[2].copy!.headline, 'Slide four content');
});

test('a non-contiguous drop lists the real slide numbers rather than assuming a range', () => {
  const plan = planAnimation(
    {
      kind: 'text',
      slides: [{ headline: 'two' }, {}, { headline: 'four' }, {}, { headline: 'six' }],
    },
    {},
  );
  assert.deepEqual(plan.adjustments, [
    '2 slide(s) had nothing typed on them and were left out.',
    '6 slides were asked for; 3 is the most a banner carries here, so slide 6 was left out.',
  ]);
});

test('per-size slide copy reaches the plan for that size', () => {
  const spec = {
    kind: 'text' as const,
    slides: [{ headline: 'Wide headline' }],
    sizeSlides: { '320x50': [{ headline: 'Narrow headline' }] },
  };
  const wide = planAnimation(spec, { size: '300x250' as any });
  const narrow = planAnimation(spec, { size: '320x50' as any });
  assert.equal(wide.frames[1].copy!.headline, 'Wide headline');
  assert.equal(narrow.frames[1].copy!.headline, 'Narrow headline');
});

/* ------------------------------------------------------ planAnimation:button */

test('a button pulse refuses on a layout with no button to pulse', () => {
  const plan = planAnimation({ kind: 'button' }, { hasCta: false });
  assert.equal(plan.frames.length, 0);
  assert.ok(plan.refused?.includes('no button'));
});

test('a button pulse refuses with no color to pulse from', () => {
  const plan = planAnimation({ kind: 'button' }, { hasCta: true });
  assert.equal(plan.frames.length, 0);
  assert.ok(plan.refused?.includes('nothing to pulse between'));
});

test('an invalid highlight color falls back to a computed pulse, and says so', () => {
  const plan = planAnimation(
    { kind: 'button', highlight: 'chartreuse' },
    { hasCta: true, baseCtaFill: '#14284b' },
  );
  assert.equal(plan.refused, undefined);
  assert.ok(plan.adjustments.some((a) => a.includes('chartreuse')));
});

test('a dark button lightens and a light button darkens, so the pulse is always visible', () => {
  assert.equal(pulseColor('#000000'), '#383838', 'black lifts toward white');
  const near = pulseColor('#fefefe');
  assert.notEqual(near, '#ffffff', 'a near-white fill must still visibly change');
});

test('the brightest frame sits in the middle of the pulse, not at either end', () => {
  const plan = planAnimation(
    { kind: 'button', frames: 5 },
    { hasCta: true, baseCtaFill: '#14284b' },
  );
  const brightest = plan.frames.findIndex((f) => f.label === 'Button, brightest');
  assert.equal(brightest, 2, 'the peak of 5 frames is the 3rd');
  assert.equal(plan.frames[0].style?.cta?.bg, '#14284b', 'the first frame starts at the button\'s own color');
});

test('more pulse frames than the house maximum are cut back, and it says so', () => {
  const plan = planAnimation(
    { kind: 'button', frames: 9 },
    { hasCta: true, baseCtaFill: '#14284b' },
  );
  assert.equal(plan.frames.length, ANIMATION_RULES.maxFrames);
  assert.deepEqual(plan.adjustments, ['9 frames were asked for; 5 is the most used here.']);
});

test('fewer pulse frames than a triangle needs are raised to 3, and it says so', () => {
  // This is the second bug: planAnimation's own docstring says every clamp is
  // recorded, and the low end of this exact clamp was not.
  const plan = planAnimation(
    { kind: 'button', frames: 2 },
    { hasCta: true, baseCtaFill: '#14284b' },
  );
  assert.equal(plan.frames.length, 3);
  assert.deepEqual(plan.adjustments, [
    '2 frame(s) were asked for; a pulse needs at least 3 to rise and fall, so it runs at 3.',
  ]);
});

test('zero or negative frames read as no preference, not as an ask that was clamped', () => {
  // Falsy input already falls back to the default elsewhere in this file
  // (frameMs, e.g.) without an adjustment; 0 frames is that same shape and
  // must not be reported as though 0 were a real, deliberate request.
  const plan = planAnimation(
    { kind: 'button', frames: 0 },
    { hasCta: true, baseCtaFill: '#14284b' },
  );
  assert.equal(plan.frames.length, DEFAULTS.buttonFrames);
  assert.deepEqual(plan.adjustments, []);
});

test('the whole sequence never exceeds the 30-second Google ceiling', () => {
  const plan = planAnimation(
    { kind: 'button', frames: 5, frameMs: 4_000 },
    { hasCta: true, baseCtaFill: '#14284b' },
  );
  assert.ok(plan.totalMs <= ANIMATION_RULES.maxTotalMs,
            `${plan.totalMs}ms of ${plan.loop} loops must not exceed 30000ms`);
  assert.ok(plan.loop >= 1, 'never the endless loop:0');
});

/* -------------------------------------------------------- animationSupport */

test('an unknown platform is refused by name', () => {
  const support = animationSupport('not-a-real-platform', '300x250' as any);
  assert.equal(support.supported, false);
  assert.ok(support.reason?.includes('not-a-real-platform'));
});

test('a size the platform does not run at all is refused by name', () => {
  const support = animationSupport('google', '999x999' as any);
  assert.equal(support.supported, false);
  assert.ok(support.reason?.includes('does not run'));
});

test('a size that only ships jpg/png ships as the static ad, and says why', () => {
  const support = animationSupport('google', '1200x628' as any);
  assert.equal(support.supported, false);
  assert.ok(support.reason?.includes('static ad'));
});

test('a Google banner size that lists gif is animatable, and carries its ceiling', () => {
  const support = animationSupport('google', '300x250' as any);
  assert.equal(support.supported, true);
  assert.equal(support.maxFileBytes, 153_600);
});

test('animatableSizes names only the sizes that actually list gif', () => {
  const sizes = animatableSizes('google');
  assert.ok(sizes.includes('300x250' as any));
  assert.ok(!sizes.includes('1200x628' as any), 'the responsive-display image asset is not animatable');
  assert.deepEqual(animatableSizes('not-a-real-platform'), []);
});

/* ---------------------------------------------------------- encodeAnimation */

test('encoding produces a real GIF, under budget, from real frames', async () => {
  const frames = await Promise.all([
    flat(300, 250, '14284b').png().toBuffer(),
    flat(300, 250, 'c0392b').png().toBuffer(),
  ]);
  const plan = planAnimation({ kind: 'text', slides: [{ headline: 'x' }] }, {});
  const result = await encodeAnimation({ frames, plan, maxFileBytes: 153_600 });
  assert.equal(result.buffer.subarray(0, 3).toString('ascii'), 'GIF', 'a real GIF header');
  assert.equal(result.overweight, false);
  assert.ok(result.bytes <= 153_600);
  assert.ok(result.attempts >= 1);
  // Decode it back and check the frame count and loop count survive the
  // round trip. sharp's own metadata reader normalizes the raw Netscape
  // block back to the same "how many times it plays" semantic it was given
  // -- this is what proves the file actually carries the number
  // planAnimation computed, rather than trusting the arithmetic on its own.
  const meta = await sharp(result.buffer, { animated: true }).metadata();
  assert.equal(meta.pages, 2);
  assert.equal(meta.loop, plan.loop, 'the file plays exactly as many times as the plan asked for');
});

test('a file over budget on every ladder step is reported overweight, not silently truncated', async () => {
  // Noise is what defeats the GIF palette ladder -- a flat fixture compresses
  // to a few hundred bytes at every step and never exercises this branch.
  function noiseFrame(seedStart: number) {
    const w = 300, h = 250;
    const px = Buffer.alloc(w * h * 3);
    let seed = seedStart;
    for (let i = 0; i < px.length; i++) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      px[i] = (seed >> 16) & 0xff;
    }
    return sharp(px, { raw: { width: w, height: h, channels: 3 } }).png().toBuffer();
  }
  const frames = await Promise.all([noiseFrame(1), noiseFrame(2), noiseFrame(3)]);
  const plan = planAnimation(
    { kind: 'text', slides: [{ headline: 'a' }, { headline: 'b' }] },
    {},
  );
  const result = await encodeAnimation({ frames, plan, maxFileBytes: 1 });
  assert.equal(result.overweight, true);
  assert.equal(result.attempts, 5, 'every rung of the ladder was tried');
  assert.ok(result.bytes > 1);
});

/* -------------------------------------------------------- animationFindings */

const RULE = { maxFileBytes: 153_600 };

test('a compliant plan and a fitting file pass all four checks', () => {
  const plan = planAnimation({ kind: 'text', slides: [{ headline: 'x' }] }, {});
  const findings = animationFindings(plan, { bytes: 50_000, overweight: false, settings: 'full palette' }, RULE);
  assert.equal(findings.length, 4);
  assert.ok(findings.every((f) => f.status === 'pass'), JSON.stringify(findings));
});

test('a frame under the 5fps floor fails, even though planAnimation would already have raised it', () => {
  // animationFindings is handed a plan directly rather than reconstructing
  // one, so it has to judge whatever plan it is given -- this proves it
  // actually reads plan.frames rather than trusting plan.fps blindly.
  const plan = planAnimation({ kind: 'text', slides: [{ headline: 'x' }] }, {});
  plan.frames[1].ms = 50;
  const findings = animationFindings(plan, { bytes: 1000, overweight: false, settings: 'x' }, RULE);
  const fps = findings.find((f) => f.check === 'animation:fps');
  assert.equal(fps?.status, 'fail');
});

test('a loop of zero fails the loop check outright', () => {
  const plan = planAnimation({ kind: 'text', slides: [{ headline: 'x' }] }, {});
  const findings = animationFindings({ ...plan, loop: 0 }, { bytes: 1000, overweight: false, settings: 'x' }, RULE);
  assert.equal(findings.find((f) => f.check === 'animation:loop')?.status, 'fail');
});

test('an overweight file fails the weight check and names both figures', () => {
  const plan = planAnimation({ kind: 'text', slides: [{ headline: 'x' }] }, {});
  const findings = animationFindings(plan, { bytes: 200_000, overweight: true, settings: '32 colors' }, RULE);
  const weight = findings.find((f) => f.check === 'animation:weight');
  assert.equal(weight?.status, 'fail');
  assert.ok(weight!.detail.includes('195') || weight!.detail.includes('200'));
  assert.ok(weight!.detail.includes('150'), 'and the ceiling it was measured against');
});

/* ----------------------------------------------------------- repeatedSlides */

test('an unchanged slide warns, because a still animation reads as a broken build', () => {
  const findings = repeatedSlides(
    [{ headline: 'same' }, { headline: 'same' }, { headline: 'different' }],
    'text',
  );
  assert.equal(findings.length, 1);
  assert.equal(findings[0].status, 'warn');
  assert.ok(findings[0].detail.includes('slide 2'));
});

test('a button pulse is exempt -- every frame says the same thing by design', () => {
  const findings = repeatedSlides(
    [{ headline: 'same' }, { headline: 'same' }, { headline: 'same' }],
    'button',
  );
  assert.deepEqual(findings, []);
});
