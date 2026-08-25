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
import { applyBlockStyles, MAX_TYPE, MIN_LOGO, MIN_TYPE } from '../src/block-style';
import { listFamilies } from '../src/fonts';
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

test('a block can take a family the renderer actually has', () => {
  const family = listFamilies()[0];
  const out = applyBlockStyles(layout(), { headline: { font: family } });
  assert.equal((out as any).headline.font, family);
});

test('a family the renderer does not have is dropped, not passed on', () => {
  // resolveFont falls back to Montserrat predictably, so accepting this would
  // render one face while the control still showed the name that was asked
  // for -- the ad looks wrong and the panel says it is right.
  const out = applyBlockStyles(layout(), { headline: { font: 'Helvetica Neue LT Pro' } });
  assert.equal((out as any).headline.font, undefined);
});

test('an unavailable family does not discard the edits beside it', () => {
  const out = applyBlockStyles(layout(), { headline: { font: 'Nonesuch', size: 30 } });
  assert.equal((out as any).headline.font, undefined);
  assert.equal((out as any).headline.size[1], 30, 'the size still applied');
});

/* ------------------------------------------------------------------ logo */

test('the logo can be moved, unlike a block of copy', () => {
  const l = { ...layout(), logo: { x: 20, y: 20, w: 100, h: 30 } } as any;
  const out = applyBlockStyles(l, { logo: { x: 150, y: 200 } }) as any;
  assert.equal(out.logo.x, 150);
  assert.equal(out.logo.y, 200);
});

test('a logo moved near the edge has its box re-fitted, not left hanging off', () => {
  // x 250 on a 300-wide canvas leaves 50px. The untouched 100px box would
  // otherwise run 50px past the edge and clip the client's mark.
  const l = { ...layout(), logo: { x: 20, y: 20, w: 100, h: 30 } } as any;
  const out = applyBlockStyles(l, { logo: { x: 250 } }) as any;
  assert.equal(out.logo.x, 250);
  assert.equal(out.logo.w, 50, 'box shrunk to the room left');
});

test('logo position is clamped inside the canvas', () => {
  const l = { ...layout(), logo: { x: 20, y: 20, w: 100, h: 30 } } as any;
  const out = applyBlockStyles(l, { logo: { x: 9999, y: -50 } }) as any;
  assert.ok(out.logo.x <= 300 - MIN_LOGO, 'x kept on canvas');
  assert.equal(out.logo.y, 0, 'negative y pulled back to the top edge');
});

test('a logo cannot be shrunk to a smudge', () => {
  const l = { ...layout(), logo: { x: 20, y: 20, w: 100, h: 30 } } as any;
  const out = applyBlockStyles(l, { logo: { w: 1, h: 1 } }) as any;
  assert.equal(out.logo.w, MIN_LOGO);
  assert.equal(out.logo.h, MIN_LOGO);
});

test('logo alignment passes through', () => {
  const l = { ...layout(), logo: { x: 20, y: 20, w: 100, h: 30 } } as any;
  const out = applyBlockStyles(l, { logo: { align: 'right', valign: 'bottom' } }) as any;
  assert.equal(out.logo.align, 'right');
  assert.equal(out.logo.valign, 'bottom');
});

test('a logo override on a layout with no logo is skipped', () => {
  const out = applyBlockStyles(layout(), { logo: { x: 10 } }) as any;
  assert.equal(out.logo, undefined);
});

/* -------------------------------------------------------------- text ink
   There was no colour control for a text block at all: the layout's ink was
   the only ink, so a client whose brand is a dark green on cream got
   near-black. The rule that matters is the same one the font check follows --
   resolveColor falls back for anything it does not recognise, so an
   unvalidated value renders as near-black while the panel still shows the
   colour that was asked for. */

test('a text block takes a hex ink', () => {
  const out = applyBlockStyles(layout(), { headline: { color: '#1F3A5F' } });
  assert.equal((out as any).headline.color, '#1F3A5F');
});

test('a text block takes a brand colour by name', () => {
  // The better answer where it fits: a name follows the palette when somebody
  // corrects a swatch upstream, where a hex is a snapshot that goes stale.
  const out = applyBlockStyles(layout(), { headline: { color: 'accent' } });
  assert.equal((out as any).headline.color, 'accent');
});

test('an ink the renderer cannot resolve is dropped, not passed on', () => {
  for (const bad of ['rebeccapurple', 'rgb(1,2,3)', '#12345', 'accentt', '']) {
    const out = applyBlockStyles(layout(), { headline: { color: bad } });
    assert.equal(
      (out as any).headline.color, undefined,
      `${JSON.stringify(bad)} should have been dropped`,
    );
  }
});

test('an unresolvable button fill is dropped too', () => {
  // Same reasoning: an unrecognised fill resolves to the #ffc400 default, so
  // accepting one paints a button a colour nobody chose.
  const out = applyBlockStyles(layout(), { cta: { bg: 'not-a-colour' } });
  assert.equal((out as any).cta.bg, 'accent');       // the template's own
});
