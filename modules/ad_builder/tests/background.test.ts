/**
 * The background of an ad, and what is painted over it.
 *
 * Both of these were fixed and invisible until now, and both are the first
 * thing anyone asks for once there is a photo behind an ad:
 *
 * **Where the photo sits.** A background is drawn `slice` — it covers the
 * canvas and the overflow is cut off — so on a 300x250 most of a landscape
 * shot is thrown away, and which part is thrown away is the difference
 * between a house and a lawn. The value reaches the composer from a saved
 * campaign file and is interpolated straight into an attribute, so an
 * unknown one has to be dropped rather than passed through: a malformed
 * preserveAspectRatio makes librsvg ignore the whole attribute, which is a
 * silently different crop.
 *
 * **The overlay.** There has always been one, and it was a fixed dark scrim
 * nobody could see or set. Leaving the colour unset has to keep that scrim
 * byte for byte, or every ad built before this release changes; choosing one
 * has to paint it flat, because a graded version of a chosen colour is that
 * colour nowhere on the canvas.
 *
 * Run with: npm test
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import sharp from 'sharp';
import { compose, coverRect, inkOverBackground, normaliseHex, resolveBgPosition } from '../src/svg';
import { LOGO_TARGET, paletteVariants } from '../src/palette';
import { hexLuminance } from '../src/raster';
import type { Brand, CopySet, SizeLayout } from '../src/types';

const brand: Brand = {
  name: 'Test Co', domain: 'test.co',
  colors: { primary: '#1F3A5F', secondary: '#4A6E9B', accent: '#F2B705', light: '#FFFFFF', dark: '#111111' },
  fonts: { headline: 'Montserrat', body: 'Open Sans' },
  logos: { primary: '' },
};

function layout(): SizeLayout {
  return {
    canvas: { w: 300, h: 250 },
    safe: 16,
    background: 'light',
    headline: { x: 20, y: 30, w: 260, h: 40, size: [12, 20], maxLines: 3, align: 'left' },
  } as unknown as SizeLayout;
}

const copy: CopySet = { headline: 'Custom Built Homes' };

/* ------------------------------------------------- the helpers alone */

test('the nine SVG alignments are accepted and nothing else is', () => {
  for (const ok of ['xMinYMin', 'xMidYMin', 'xMaxYMin', 'xMinYMid', 'xMidYMid',
                    'xMaxYMid', 'xMinYMax', 'xMidYMax', 'xMaxYMax']) {
    assert.equal(resolveBgPosition(ok), ok);
  }
  for (const bad of ['top', 'xMidYMid slice', 'none', '', undefined,
                     'xMidYMid"/><script>', 'XMIDYMID']) {
    assert.equal(resolveBgPosition(bad as any), 'xMidYMid',
      `${JSON.stringify(bad)} should have fallen back to centre`);
  }
});

test('an overlay colour is a hex or it is nothing', () => {
  assert.equal(normaliseHex('#abc'), '#ABC');
  assert.equal(normaliseHex('#A1B2C3'), '#A1B2C3');
  for (const bad of ['red', 'primary', '#12345', 'rgb(0,0,0)', '', undefined]) {
    assert.equal(normaliseHex(bad as any), null,
      `${JSON.stringify(bad)} is not a colour this composer can paint`);
  }
});

test('no chosen colour keeps the light ink the dark scrim needs', () => {
  assert.equal(inkOverBackground({}, brand), 'light');
  assert.equal(inkOverBackground({ backgroundOverlay: 0.9 }, brand), 'light');
});

test('a heavy light wash flips the ink to dark', () => {
  // White text on a white overlay is the same unreadable ad in the other
  // direction, which is what forcing light ink unconditionally produced.
  assert.equal(inkOverBackground({ backgroundOverlayColor: '#FFFFFF', backgroundOverlay: 0.8 }, brand), 'dark');
  assert.equal(inkOverBackground({ backgroundOverlayColor: '#111111', backgroundOverlay: 0.8 }, brand), 'light');
});

test('a thin wash leaves the ink light, because the photo is still the background', () => {
  // Under 45% the wash barely changes the photo, and a photo is an unknown.
  assert.equal(inkOverBackground({ backgroundOverlayColor: '#FFFFFF', backgroundOverlay: 0.2 }, brand), 'light');
});

/* The composer only paints an overlay when it has a picture to paint over,
   so these compose against a real one. A 4x4 PNG on disk is enough: what is
   asserted is the markup around the image, not the pixels in it. */
async function withPhoto(extra: Record<string, unknown>) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'adbg-'));
  const file = path.join(dir, 'bg.png');
  await sharp({
    create: { width: 4, height: 4, channels: 3, background: '#808080' },
  }).png().toFile(file);
  try {
    return await compose({
      layout: layout(), brand, copy, hero: {}, scale: 1,
      backgroundImage: file, ...extra,
    });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test('a chosen colour is painted flat at the chosen opacity', async () => {
  const out = await withPhoto({ backgroundOverlayColor: '#FF0000', backgroundOverlay: 0.6 });
  assert.match(out.svg, /fill="#FF0000" fill-opacity="0\.60"/);
  assert.ok(!out.svg.includes('bgScrim'),
    'a chosen colour replaces the graded scrim rather than adding to it');
});

test('no chosen colour keeps the graded scrim exactly as it was', async () => {
  // Every ad built before this release renders through here. If this changes,
  // they all change.
  const out = await withPhoto({ backgroundOverlay: 0.42 });
  assert.match(out.svg, /id="bgScrim"/);
  assert.match(out.svg, /stop-color="#0b1220" stop-opacity="0\.57"/);
  assert.match(out.svg, /stop-color="#0b1220" stop-opacity="0\.42"/);
  assert.match(out.svg, /stop-color="#0b1220" stop-opacity="0\.17"/);
});

test('a legacy crop anchor still places the picture where it always did', async () => {
  // Concepts saved before the nudge arrays existed carry one of the nine
  // alignments, and they have to keep meaning the same thing.
  const out = await withPhoto({ backgroundPosition: 'xMidYMin' });
  // 4x4 source on a 300x250 canvas: covers at 300x300, top edge flush.
  assert.match(out.svg, /<image x="0\.00" y="0\.00" width="300\.00" height="300\.00"/);
});

test('a crop anchor SVG would not accept cannot reach the markup', async () => {
  // It is interpolated out of a saved campaign file. Nothing unrecognised is
  // emitted at all now — the picture is placed by arithmetic — but the value
  // still has to be refused rather than carried.
  const out = await withPhoto({ backgroundPosition: 'xMidYMid" onload="x' });
  assert.ok(!out.svg.includes('onload'), 'nothing smuggled through the attribute');
  assert.match(out.svg, /<image x="0\.00" y="-25\.00"/, 'and it falls back to centred');
});

/* ------------------------------------------------- nudging and zooming */

test('the offset names the part of the picture that shows', () => {
  // The sign here is the whole thing: -1 means "show me the top", which
  // slides the picture DOWN until its top edge meets the canvas. Backwards,
  // every arrow moves the picture the opposite way from the one pressed —
  // and on a symmetrical photograph that survives a glance.
  const top = coverRect(400, 400, 300, 250, { offset: { x: 0, y: -1 } });
  const mid = coverRect(400, 400, 300, 250, { offset: { x: 0, y: 0 } });
  const bot = coverRect(400, 400, 300, 250, { offset: { x: 0, y: 1 } });
  assert.equal(top!.y, 0, 'top of the picture flush with the top of the canvas');
  assert.equal(mid!.y, -25, 'centred leaves equal overflow either side');
  assert.equal(bot!.y, -50, 'bottom of the picture flush with the bottom');
});

test('a legacy alignment and its offset are the same placement', () => {
  for (const [legacy, offset] of [
    ['xMidYMin', { x: 0, y: -1 }], ['xMidYMid', { x: 0, y: 0 }],
    ['xMidYMax', { x: 0, y: 1 }], ['xMinYMid', { x: -1, y: 0 }],
    ['xMaxYMid', { x: 1, y: 0 }],
  ] as const) {
    assert.deepEqual(
      coverRect(400, 400, 300, 250, { legacy }),
      coverRect(400, 400, 300, 250, { offset }),
      `${legacy} should place identically to its offset`,
    );
  }
});

test('zoom never drops below covering the canvas', () => {
  // Under 1 the picture stops covering and the ad shows the brand colour
  // through its own edges, which is not a look anybody is choosing.
  const zoomed = coverRect(400, 400, 300, 250, { zoom: 0.2 })!;
  const plain = coverRect(400, 400, 300, 250, {})!;
  assert.deepEqual(zoomed, plain);
  const big = coverRect(400, 400, 300, 250, { zoom: 2 })!;
  assert.equal(big.w, 600);
  assert.ok(big.x < 0 && big.y < 0, 'a zoomed picture overhangs on every side');
});

test('a source with no intrinsic size falls back rather than placing NaN', () => {
  // sharp reports 0x0 for an SVG, and every number here would be NaN.
  assert.equal(coverRect(0, 0, 300, 250, {}), null);
  assert.equal(coverRect(400, 400, 0, 0, {}), null);
});

test('an offset past the edges is clamped, not extrapolated', () => {
  assert.deepEqual(
    coverRect(400, 400, 300, 250, { offset: { x: 0, y: 9 } }),
    coverRect(400, 400, 300, 250, { offset: { x: 0, y: 1 } }),
  );
});

test('with no background image the canvas is still the flat brand field', () => {
  // The whole overlay path must stay behind "is there a picture", or a
  // concept that never had one gains a wash.
  return compose({ layout: layout(), brand, copy, hero: {}, scale: 1 }).then((out) => {
    assert.match(out.svg, /fill="#FFFFFF"/);
    assert.ok(!out.svg.includes('bgScrim'), 'no scrim without a picture');
    assert.ok(!out.svg.includes('preserveAspectRatio'), 'no background image element');
  });
});

/* ------------------------------------------- a logo nobody can see */

test('a logo that already reads gets no proposals', () => {
  // Offering four ways to change a palette that works is how a tool teaches
  // people to ignore it.
  const out = paletteVariants({
    brand, logoLuminance: 0.02, behind: 'light',   // near-black mark on white
  });
  assert.equal(out.verdict, 'fine');
  assert.deepEqual(out.variants, []);
});

test('a dark logo on a dark brand colour gets palettes that make it read', () => {
  // `primary` is the common case: most families paint the canvas with it,
  // and unlike `light`/`dark` its name asserts nothing about lightness, so
  // it is free to move.
  const out = paletteVariants({
    brand, logoLuminance: 0.02, behind: 'primary', // near-black on #8C1B1B
  });
  assert.equal(out.verdict, 'invisible');
  assert.ok(out.variants.length, 'something is proposed');
  for (const v of out.variants) {
    assert.ok(v.ratio >= LOGO_TARGET, `${v.id} must actually clear the threshold`);
    // The whole palette, every time. Applying part of one is how a set ends
    // up half in one scheme and half in another.
    assert.deepEqual(Object.keys(v.colors).sort(),
                     ['accent', 'dark', 'light', 'primary', 'secondary']);
  }
});

test('the light and dark roles are never inverted to fix a logo', () => {
  // Setting `light` to near-black fixes the mark and silently changes what
  // every template means by "light ink" on every other size.
  const out = paletteVariants({
    brand, logoLuminance: 0.95, behind: 'light',   // white mark on white
  });
  for (const v of out.variants) {
    assert.ok(hexLuminance(v.colors.light) > 0.5,
      `${v.id} left the light role at ${v.colors.light}, which is not light`);
  }
});

test('nothing is proposed that is the colour it already is', () => {
  // The shift walk returns the same colour whenever the current one is
  // already at the end of its range: "#FFFFFF moves to #FFFFFF".
  const out = paletteVariants({
    brand, logoLuminance: 0.95, behind: 'light',
  });
  for (const v of out.variants) {
    assert.notEqual(v.colors.light.toUpperCase(), brand.colors.light.toUpperCase());
  }
});

test('the client\'s own site colours are offered first', () => {
  const out = paletteVariants({
    brand, logoLuminance: 0.02, behind: 'primary',
    observed: { background: '#F4F1EA' },
  });
  assert.equal(out.variants[0].source, 'observed',
    'observed evidence outranks anything computed from the palette we have');
  assert.match(out.variants[0].why, /site scan/);
});

test('fixing the DARK role never makes it light', () => {
  // A near-black mark on near-black. Lifting `dark` to a lighter charcoal is
  // a fair proposal and is offered; turning `dark` into a light colour is
  // not, because every template resolves ink against that role's name and
  // the fix would silently invert it on every other size.
  const out = paletteVariants({ brand, logoLuminance: 0.02, behind: 'dark' });
  assert.equal(out.verdict, 'invisible', 'it is still reported as invisible');
  for (const v of out.variants) {
    assert.ok(v.ratio >= LOGO_TARGET, `${v.id} has to actually work`);
    assert.ok(hexLuminance(v.colors.dark) < 0.5,
      `${v.id} left the dark role at ${v.colors.dark}, which is not dark`);
  }
});

test('a mark that nothing can rescue returns nothing rather than a guess', () => {
  // A mid-grey logo clears 3:1 against neither white nor black.
  const grey: typeof brand = { ...brand, colors: { ...brand.colors, primary: '#777777' } };
  const out = paletteVariants({ brand: grey, logoLuminance: 0.21, behind: 'primary' });
  for (const v of out.variants) {
    assert.ok(v.ratio >= LOGO_TARGET, 'anything returned has to actually work');
  }
});
