/**
 * Per-block style overrides.
 *
 * The dangerous change here is width: a block wider than the canvas leaves
 * looks correct in a control panel and ships a clipped ad, which is exactly
 * the class of bug the QA pass exists to catch after the fact. So the clamps
 * are the point of these tests, along with the promise that the shared
 * template is never mutated — it is loaded once and used by every concept in
 * the process, so restyling one ad through it would restyle everybody's.
 *
 * Run with: npm test
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyBlockStyles, MAX_TYPE, MIN_TYPE } from '../src/block-style';
import type { SizeLayout } from '../src/types';

function layout(): SizeLayout {
  return {
    canvas: { w: 300, h: 250 },
    safe: 16,
    background: 'light',
    headline: { x: 20, y: 30, w: 260, h: 40, size: [12, 20], maxLines: 3, align: 'left' },
    cta: { x: 20, y: 190, w: 150, h: 34, size: [12, 14], maxLines: 1, bg: 'accent' },
  } as unknown as SizeLayout;
}

test('no overrides returns the very same object', () => {
  const l = layout();
  assert.equal(applyBlockStyles(l, undefined), l);
  assert.equal(applyBlockStyles(l, {}), l);
});

test('the shared template is never mutated', () => {
  // Templates are loaded once and shared by every concept in the process.
  const l = layout();
  const before = JSON.stringify(l);
  const out = applyBlockStyles(l, { headline: { size: 30, align: 'center' } });
  assert.equal(JSON.stringify(l), before, 'input untouched');
  assert.notEqual(out, l, 'a new object came back');
  assert.equal((out as any).headline.size[1], 30);
  assert.equal((out as any).cta, (l as any).cta, 'untouched blocks are shared, not copied');
});

test('a width is clamped to what is left on the canvas', () => {
  // 20 + 500 would run 220px past the edge and clip the copy.
  const out = applyBlockStyles(layout(), { headline: { w: 500 } });
  assert.equal((out as any).headline.w, 280, 'canvas 300 minus x 20');
});

test('a sane width is kept as asked', () => {
  const out = applyBlockStyles(layout(), { headline: { w: 180 } });
  assert.equal((out as any).headline.w, 180);
});

test('type size is bounded at both ends', () => {
  const big = applyBlockStyles(layout(), { headline: { size: 4000 } });
  assert.equal((big as any).headline.size[1], MAX_TYPE);
  const tiny = applyBlockStyles(layout(), { headline: { size: 1 } });
  assert.equal((tiny as any).headline.size[1], MIN_TYPE);
});

test('the autofit floor stays below the new ceiling', () => {
  // The template floor is 12. Asking for a 10px ceiling must not leave
  // [12, 10], or autofit has no room and long copy overflows instead of
  // shrinking to fit.
  const out = applyBlockStyles(layout(), { headline: { size: 10 } });
  const [floor, ceiling] = (out as any).headline.size;
  assert.ok(floor <= ceiling, `floor ${floor} must not exceed ceiling ${ceiling}`);
  assert.equal(ceiling, 10);
});

test('line height is clamped to something readable', () => {
  assert.equal((applyBlockStyles(layout(), { headline: { lineHeight: 99 } }) as any).headline.lineHeight, 2.5);
  assert.equal((applyBlockStyles(layout(), { headline: { lineHeight: 0.1 } }) as any).headline.lineHeight, 0.8);
});

test('weight and alignment pass through', () => {
  const out = applyBlockStyles(layout(), { headline: { weight: 'bold', align: 'center' } });
  assert.equal((out as any).headline.weight, 'bold');
  assert.equal((out as any).headline.align, 'center');
});

test('a button fill applies to the CTA', () => {
  const out = applyBlockStyles(layout(), { cta: { bg: '#ff0000' } });
  assert.equal((out as any).cta.bg, '#ff0000');
});

test('a button fill on a non-button block is ignored', () => {
  // Only the CTA is drawn with a fill, so accepting this elsewhere would be a
  // control that appears to work and changes nothing.
  const out = applyBlockStyles(layout(), { headline: { bg: '#ff0000' } });
  assert.equal((out as any).headline.bg, undefined);
});

test('an override for a block this size does not have is skipped', () => {
  // Not every family carries every block at every size.
  const out = applyBlockStyles(layout(), { offer: { size: 40 } });
  assert.equal((out as any).offer, undefined);
});

test('nonsense numbers are ignored rather than propagated', () => {
  const out = applyBlockStyles(layout(), { headline: { size: NaN, w: Infinity } });
  assert.deepEqual((out as any).headline.size, [12, 20], 'size untouched');
  assert.equal((out as any).headline.w, 260, 'width untouched');
});
