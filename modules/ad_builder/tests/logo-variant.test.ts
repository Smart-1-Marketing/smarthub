/**
 * Which logo goes on which panel, and the three measurements that hid it.
 *
 * The automatic choice was `layout.background === 'dark'`, evaluated per size
 * in render.ts. 'dark' is a legal ColorRef -- it is a brand palette *role* --
 * so it read as deliberate, and it was never once true: across the five
 * shipped templates a background is `light` or `primary`, never `dark`. The
 * panels that are genuinely dark are the `primary` ones. So the choice this
 * file's own comment promised ("the composer can pick the reverse logo on
 * dark panels") could not fire, and Icon Solar's navy wordmark went onto a
 * navy panel with only its yellow sun left showing.
 *
 * Two measurements then agreed it was fine:
 *
 *  - the background pass QA samples "behind" from carried the logo, because
 *    includeText only ever gated glyphs -- so the mark was compared against a
 *    region containing that same mark, which biases every reading toward a
 *    false "invisible" and read 1.0:1 for a white logo on a white panel;
 *  - logoInkContrast()'s predecessor averaged every opaque pixel into one
 *    number, and Icon Solar's mark is 5,193 navy pixels and 1,176 yellow
 *    ones, so it averaged to a tone that is nowhere in the logo and cleared
 *    the threshold while four fifths of the mark was invisible.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import sharp from 'sharp';
import { compose, reverseLogoOnPanel } from '../src/svg';
import { logoInkContrast, logoInkLuminance, LOGO_MAX_FAIL_FRACTION } from '../src/qa';
import { contrastRatio } from '../src/raster';
import { renderPreview } from '../src/render';
import type { Brand, Campaign, SizeKey } from '../src/types';

const ROOT = path.resolve(__dirname, '..');
const EXAMPLE = path.join(ROOT, 'src/examples/icon-solar.json');

function example(): Campaign {
  return JSON.parse(fs.readFileSync(EXAMPLE, 'utf8'));
}

test('no shipped template declares the background the old rule tested for', () => {
  const dir = path.join(ROOT, 'src/templates');
  const seen = new Set<string>();
  for (const f of fs.readdirSync(dir).filter((n) => n.endsWith('.json'))) {
    const tpl = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
    for (const layout of Object.values<any>(tpl.sizes ?? {})) {
      if (typeof layout?.background === 'string') seen.add(layout.background);
    }
  }
  assert.ok(seen.size > 0, 'templates declare a background at all');
  assert.equal(seen.has('dark'), false,
    'nothing uses the `dark` role, which is why comparing against it was dead');
  assert.ok(seen.has('primary'), 'the dark panels in practice are the `primary` ones');
});

test('the panel decides the variant, by color rather than by role name', () => {
  const brand = example().brand;
  assert.equal(reverseLogoOnPanel({ background: 'primary' }, brand), true,
    'Icon Solar primary is navy, so the white logo belongs on it');
  assert.equal(reverseLogoOnPanel({ background: 'light' }, brand), false,
    'and the full-color one belongs on white');

  // The role name cannot answer this and the resolved color can: a brand
  // whose `primary` is pale needs the dark logo on exactly the same layout.
  const pale: Brand = { ...brand, colors: { ...brand.colors, primary: '#FFE9A8' } };
  assert.equal(reverseLogoOnPanel({ background: 'primary' }, pale), false,
    'a pale primary takes the full-color logo, though the role is unchanged');

  // A photo is not a flat color, so it defers to the same call the text ink
  // already makes rather than being a second opinion about one panel.
  assert.equal(
    reverseLogoOnPanel({ background: 'light' }, brand, { backgroundImage: 'hero.jpg' }),
    true,
    'over a scrimmed photo the logo follows the light ink the copy is using',
  );
});

test('a panel under the logo is what the logo sits on, not the canvas', () => {
  const brand = example().brand;
  const t02 = JSON.parse(fs.readFileSync(path.join(ROOT, 'src/templates/T02.json'), 'utf8'));

  // T02 drops a `light` content card under the mark on nine layouts while the
  // canvas behind it is `primary`. Reading the canvas puts the white logo on
  // a white card -- the same failure, one panel further in -- so this is the
  // case that makes the naive "is the background dark?" fix wrong too.
  const withCard = Object.entries<any>(t02.sizes).filter(([, l]) => {
    const lb = l.logo;
    return lb && (l.panels ?? []).some((p: any) =>
      lb.x < p.x + p.w && p.x < lb.x + lb.w && lb.y < p.y + p.h && p.y < lb.y + lb.h);
  });
  assert.ok(withCard.length > 0, 'T02 still ships layouts shaped like this');

  for (const [size, layout] of withCard) {
    assert.equal(layout.background, 'primary', `${size}: the canvas is the dark role`);
    assert.equal(reverseLogoOnPanel(layout, brand), false,
      `${size}: but the logo is on a light card, so it takes the full-color mark`);
  }
});

test('the background pass QA measures against no longer contains the logo', async () => {
  const campaign = example();
  const tpl = JSON.parse(fs.readFileSync(path.join(ROOT, 'src/templates/T04.json'), 'utf8'));
  const layout = tpl.sizes['728x90'];
  const copy: any = { headline: 'Home Solar', supporting: 'Financing', cta: 'Get Offer' };

  const withLogo = await compose({
    layout, brand: campaign.brand, copy, scale: 1, includeText: false, assetRoot: ROOT,
  });
  const without = await compose({
    layout, brand: campaign.brand, copy, scale: 1,
    includeText: false, includeLogo: false, assetRoot: ROOT,
  });

  assert.match(withLogo.svg, /<image/, 'the logo is drawn when it is wanted');
  assert.equal(/<image/.test(without.svg), false,
    'and omitted from the pass that exists to measure what sits behind it');
  assert.deepEqual(without.rects.logo, withLogo.rects.logo,
    'the rect is still published either way -- it is the region to sample');
});

test('a logo over a hero photograph is decided from the photograph', async () => {
  const campaign = example();
  const bella = JSON.parse(fs.readFileSync(path.join(ROOT, 'campaigns/bella-vista-catering.json'), 'utf8'));
  const t03 = JSON.parse(fs.readFileSync(path.join(ROOT, 'src/templates/T03.json'), 'utf8'));

  // T03 is "Full background with copy panel": its hero box is the top of the
  // canvas and its logo sits inside that box, so the layout says `light` while
  // the mark lands on the picture. reverseLogoOnPanel() cannot see that -- it
  // reads roles and panels -- and the navy copy panel it CAN see is nowhere
  // near the logo.
  const layout = t03.sizes['300x250'];
  const lb = layout.logo, hb = layout.hero;
  assert.equal(layout.background, 'light', 'the canvas role is the light one');
  assert.ok(lb.x >= hb.x && lb.y >= hb.y && lb.y + lb.h <= hb.y + hb.h,
    'and the logo sits inside the hero box');
  assert.equal(reverseLogoOnPanel(layout, campaign.brand), false,
    'so the layout-only rule reaches for the full-color logo');

  // Measured against what actually renders, the answer changes.
  const concept = {
    conceptId: 'H', name: 'hero', layoutFamily: 'T03',
    hero: bella.concepts[0].hero,
    copy: { default: { headline: 'Solar Installation', supporting: 'Financing available.', cta: 'Get Offer' } },
  } as any;
  const out = await renderPreview({
    brand: campaign.brand, concept, platform: 'google', size: '300x250' as SizeKey, assetRoot: ROOT,
  });
  const finding = out.qa.find((f) => f.check === 'logo-contrast');
  assert.equal(finding?.status, 'pass',
    'the mark reads against the photo it is actually printed on');
});

test('a two-tone logo is measured per pixel, not averaged into a tone it has nowhere', async () => {
  const primary = path.join(ROOT, 'assets/brand/icon-solar-primary.png');
  const navyPanel = 0.033;                       // the resolved `primary` panel

  const averaged = await logoInkLuminance(primary);
  assert.ok(contrastRatio(averaged!, navyPanel) > 1.7,
    'the average clears the threshold, which is how this shipped');

  const measured = await logoInkContrast(primary, navyPanel);
  assert.ok(measured!.failFraction > LOGO_MAX_FAIL_FRACTION,
    'while most of the mark is actually invisible');
  assert.ok(measured!.failRatio < 1.7,
    'and the number reported is the tone that fails, not the average');

  // The same logo on the panel it belongs on raises nothing.
  const onWhite = await logoInkContrast(primary, 0.974);
  assert.ok(onWhite!.failFraction <= LOGO_MAX_FAIL_FRACTION,
    'the yellow sun is a fifth of the mark and the wordmark carries the name, '
    + 'so a check that fired here would be one people stop reading');
});

test('the bundled example renders with no logo left invisible', async () => {
  const campaign = example();
  const concept = campaign.concepts.find((c) => c.conceptId === 'C')!;
  assert.equal((concept as any).useReverseLogo, undefined,
    'it leans on the per-size choice; one flag per concept cannot answer a '
    + 'template that mixes 2 `primary` sizes with 13 `light` ones');

  for (const size of ['300x250', '728x90'] as SizeKey[]) {
    const out = await renderPreview({
      brand: campaign.brand, concept, platform: 'google', size, assetRoot: ROOT,
    });
    const finding = out.qa.find((f) => f.check === 'logo-contrast');
    assert.equal(finding?.status, 'pass',
      `${size}: the variant that reads on this panel was chosen`);
  }
});
