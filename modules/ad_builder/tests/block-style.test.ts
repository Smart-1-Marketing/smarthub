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
   There was no color control for a text block at all: the layout's ink was
   the only ink, so a client whose brand is a dark green on cream got
   near-black. The rule that matters is the same one the font check follows --
   resolveColor falls back for anything it does not recognize, so an
   unvalidated value renders as near-black while the panel still shows the
   color that was asked for. */

test('a text block takes a hex ink', () => {
  const out = applyBlockStyles(layout(), { headline: { color: '#1F3A5F' } });
  assert.equal((out as any).headline.color, '#1F3A5F');
});

test('a text block takes a brand color by name', () => {
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
  // accepting one paints a button a color nobody chose.
  const out = applyBlockStyles(layout(), { cta: { bg: 'not-a-color' } });
  assert.equal((out as any).cta.bg, 'accent');       // the template's own
});

/* --------------------------------------------- the button, and the panel */

test('aligning the button moves the BUTTON, not the label inside it', () => {
  // The bug this fixes: `align` set the label's alignment inside the button,
  // which every template already centres — so Center, Left and Right all
  // rendered identically and the control read as broken.
  const l = layout();                       // 300x250, safe 16, cta w=150 x=20
  const centered = applyBlockStyles(l, { cta: { align: 'center' } });
  assert.equal((centered as any).cta.x, 75, 'centered in the 268px safe region');
  assert.equal((centered as any).cta.align, undefined, 'the label is left alone');

  const right = applyBlockStyles(l, { cta: { align: 'right' } });
  assert.equal((right as any).cta.x, 134, 'flush with the right safe edge');

  const left = applyBlockStyles(l, { cta: { align: 'left' } });
  assert.equal((left as any).cta.x, 16, 'flush with the left safe edge');
});

test('the button can be nudged, and cannot be nudged off the canvas', () => {
  const out = applyBlockStyles(layout(), { cta: { x: 40, y: 200 } });
  assert.equal((out as any).cta.x, 40);
  assert.equal((out as any).cta.y, 200);

  const off = applyBlockStyles(layout(), { cta: { x: 9999, y: -9999 } });
  assert.equal((off as any).cta.x, 150, 'clamped to canvas width less the button');
  assert.equal((off as any).cta.y, 0);
});

test('only the button moves; type is still placed by the layout', () => {
  // Moving a block of copy is a layout decision, and layouts are chosen by
  // picking a family. Accepting x/y here would be a control that quietly
  // dismantles that.
  const out = applyBlockStyles(layout(), { headline: { x: 5, y: 5 } as any });
  assert.equal((out as any).headline.x, 20, 'untouched');
  assert.equal((out as any).headline.y, 30, 'untouched');
});

test('the panel behind the copy takes a fill and an opacity', () => {
  // "Full background with copy panel" puts the headline on this card, and the
  // fill decides whether the copy can be read at all. It was a constant.
  const l = { ...layout(), panels: [{ x: 0, y: 130, w: 300, h: 120, fill: 'primary', opacity: 0.88 }] } as any;
  const out = applyBlockStyles(l, { panel: { fill: '#123456', opacity: 0.5 } });
  assert.equal((out as any).panels[0].fill, '#123456');
  assert.equal((out as any).panels[0].opacity, 0.5);
  assert.equal(l.panels[0].fill, 'primary', 'the shared template is not mutated');
});

test('a panel opacity outside 0..1 is clamped, and a bad fill dropped', () => {
  const l = { ...layout(), panels: [{ x: 0, y: 130, w: 300, h: 120, fill: 'primary' }] } as any;
  assert.equal((applyBlockStyles(l, { panel: { opacity: 4 } }) as any).panels[0].opacity, 1);
  assert.equal((applyBlockStyles(l, { panel: { opacity: -2 } }) as any).panels[0].opacity, 0);
  assert.equal((applyBlockStyles(l, { panel: { fill: 'not-a-color' } }) as any).panels[0].fill,
               'primary', 'an unresolvable fill leaves the template alone');
});

test('a panel override on a layout with no panel changes nothing', () => {
  const l = layout();
  assert.equal(applyBlockStyles(l, { panel: { fill: '#123456' } }), l);
});

/* ------------------------------------------------------ the logo, sized */

test('one slider scales the logo box proportionally', () => {
  // Two number fields for width and height is the wrong control for "make it
  // bigger": they have to be kept in step, and out of step they do nothing
  // visible (the mark is contained inside the box) until they crop.
  const l = { ...layout(), logo: { x: 20, y: 20, w: 100, h: 30 } } as any;
  const out = applyBlockStyles(l, { logo: { scale: 1.5 } });
  assert.equal((out as any).logo.w, 150);
  assert.equal((out as any).logo.h, 45);
});

test('an explicit width still wins over the slider', () => {
  // Or Advanced is a second control fighting the first.
  const l = { ...layout(), logo: { x: 20, y: 20, w: 100, h: 30 } } as any;
  const out = applyBlockStyles(l, { logo: { scale: 2, w: 60 } });
  assert.equal((out as any).logo.w, 60);
  assert.equal((out as any).logo.h, 60, 'the height still took the scale');
});

test('logo scale is bounded at both ends', () => {
  const l = { ...layout(), logo: { x: 20, y: 20, w: 100, h: 30 } } as any;
  assert.equal((applyBlockStyles(l, { logo: { scale: 99 } }) as any).logo.w, 280,
               'clamped to 3x, then clamped again to the canvas');
  const tiny = applyBlockStyles(l, { logo: { scale: 0.001 } }) as any;
  assert.ok(tiny.logo.w >= MIN_LOGO, 'never smaller than a logo can be');
});
