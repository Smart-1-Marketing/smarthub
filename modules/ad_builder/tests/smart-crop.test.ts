/**
 * Recovering an offset/zoom from a `g_auto` crop Cloudinary already made.
 *
 * Cloudinary publishes no field carrying the crop rectangle `g_auto,c_fill`
 * picked -- only the finished pixels -- so `smart-crop.ts` finds it by
 * correlating those pixels against the original, full-resolution image, then
 * converts the matched position into `coverRect()`'s own offset/zoom
 * convention. That conversion is `offsetFromCropWindow()`, and it is the
 * part most likely to carry a sign or scale bug: `coverRect()`'s own
 * docstring warns that a near-symmetrical photograph will not tell you if
 * you got the sign backwards.
 *
 * So most of this drives `offsetFromCropWindow()` against `coverRect()`
 * itself -- generate a crop window forward, from a chosen offset, and
 * require the inverse to recover that same offset, on the axis that
 * actually has slack and with the sign that names the correct edge. And the
 * pixel correlation is exercised for real, entirely in-process with images
 * built by sharp, so `matchCropInImage()` runs against real bytes rather
 * than only its own arithmetic.
 *
 * What is NOT exercised here is a live Cloudinary account: `suggestCrop()`'s
 * fetch of a real `res.cloudinary.com` URL is not called anywhere in this
 * file, because doing so from a test would depend on network reachability
 * and a real, correctly-configured account. Its URL parsing and its
 * fast-reject paths -- which never touch the network -- are.
 *
 * Run with: npm test
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import sharp from 'sharp';
import { coverRect } from '../src/svg';
import {
  MIN_CONFIDENCE,
  matchCropInImage,
  matchCropWindow,
  offsetFromCropWindow,
  suggestCrop,
} from '../src/smart-crop';

/**
 * Where, in the ORIGINAL image's own pixels, `coverRect()` would place the
 * crop window for a given offset -- the forward direction, used here only to
 * build fixtures for the reverse one.
 */
function forwardCropWindow(
  iw: number, ih: number, W: number, H: number,
  offset: { x: number; y: number },
): { cropX: number; cropY: number } {
  const place = coverRect(iw, ih, W, H, { offset })!;
  const scale = Math.max(W / iw, H / ih);
  return { cropX: -place.x / scale, cropY: -place.y / scale };
}

/* ------------------------------------------------- the geometry, both ways */

test('a dead-center crop round-trips to offset {0,0} and zoom 1', () => {
  const iw = 1000, ih = 600, W = 300, H = 250;
  const { cropX, cropY } = forwardCropWindow(iw, ih, W, H, { x: 0, y: 0 });
  const { offset, zoom } = offsetFromCropWindow(iw, ih, W, H, cropX, cropY);
  assert.ok(Math.abs(offset.x) < 1e-9, `x should be 0, got ${offset.x}`);
  assert.ok(Math.abs(offset.y) < 1e-9, `y should be 0, got ${offset.y}`);
  assert.equal(zoom, 1);
});

test('an offset shifted toward an edge recovers with the same value and sign', () => {
  // 1000x600 into 300x250: H/ih (0.4167) exceeds W/iw (0.3), so the cover
  // scale is set by height and the Y axis has zero slack -- only X can move.
  const iw = 1000, ih = 600, W = 300, H = 250;
  const scale = Math.max(W / iw, H / ih);
  const slackX = (iw * scale - W) / 2;
  const slackY = (ih * scale - H) / 2;
  assert.ok(slackX > 1, 'fixture assumption: X carries the slack');
  assert.ok(slackY < 1e-6, 'fixture assumption: Y has none');

  for (const off of [-1, -0.6, 0.4, 1]) {
    const { cropX, cropY } = forwardCropWindow(iw, ih, W, H, { x: off, y: 0 });
    const { offset, zoom } = offsetFromCropWindow(iw, ih, W, H, cropX, cropY);
    assert.ok(Math.abs(offset.x - off) < 1e-6, `expected x=${off}, got ${offset.x}`);
    assert.ok(Math.abs(offset.y) < 1e-9, `y should stay 0, got ${offset.y}`);
    assert.equal(zoom, 1);
  }
});

test('offset -1 and +1 crop from opposite ends of the original, not the same one twice', () => {
  // The exact failure coverRect()'s own comment warns about: getting the
  // sign backwards puts every nudge in the opposite direction from the arrow
  // that was pressed, and a symmetrical fixture would hide it. This does not
  // rely on symmetry -- it asserts the two ends are on the sides they claim.
  const iw = 1000, ih = 600, W = 300, H = 250;
  const scale = Math.max(W / iw, H / ih);
  const cropW = W / scale;

  const left = forwardCropWindow(iw, ih, W, H, { x: -1, y: 0 });
  const right = forwardCropWindow(iw, ih, W, H, { x: 1, y: 0 });

  // -1 ("show me the left") is flush with the original's left edge.
  assert.ok(Math.abs(left.cropX - 0) < 1e-6, `left.cropX should be 0, got ${left.cropX}`);
  // +1 ("show me the right") is flush with the original's right edge.
  assert.ok(Math.abs(right.cropX - (iw - cropW)) < 1e-6,
    `right.cropX should be ${iw - cropW}, got ${right.cropX}`);
  assert.ok(right.cropX > left.cropX);

  // And each recovers its own sign back out, not the other one's.
  assert.equal(offsetFromCropWindow(iw, ih, W, H, left.cropX, left.cropY).offset.x, -1);
  assert.equal(offsetFromCropWindow(iw, ih, W, H, right.cropX, right.cropY).offset.x, 1);
});

test('the slack axis can be Y instead of X, and the inversion still holds', () => {
  // Flip the aspect ratios so W/iw dominates instead: now Y carries the
  // slack. Same function, the other axis -- a fix that only worked on one
  // axis would pass the tests above and still be wrong here.
  const iw = 600, ih = 1000, W = 250, H = 300;
  const scale = Math.max(W / iw, H / ih);
  const slackX = (iw * scale - W) / 2;
  const slackY = (ih * scale - H) / 2;
  assert.ok(slackY > 1, 'fixture assumption: Y carries the slack');
  assert.ok(slackX < 1e-6, 'fixture assumption: X has none');

  for (const off of [-1, -0.3, 0.7, 1]) {
    const { cropX, cropY } = forwardCropWindow(iw, ih, W, H, { x: 0, y: off });
    const { offset } = offsetFromCropWindow(iw, ih, W, H, cropX, cropY);
    assert.ok(Math.abs(offset.y - off) < 1e-6, `expected y=${off}, got ${offset.y}`);
    assert.ok(Math.abs(offset.x) < 1e-9);
  }
});

/* ---------------------------------------------- the correlation, for real */

/** A gradient with no two positions alike, so a correlation search has
 *  exactly one place to land rather than a field of equally-good ties the
 *  way a flat or repeating pattern would offer. */
async function gradientImage(iw: number, ih: number): Promise<Buffer> {
  const data = Buffer.alloc(iw * ih * 3);
  for (let y = 0; y < ih; y++) {
    for (let x = 0; x < iw; x++) {
      const i = (y * iw + x) * 3;
      data[i] = Math.round((255 * x) / Math.max(1, iw - 1));
      data[i + 1] = Math.round((255 * y) / Math.max(1, ih - 1));
      data[i + 2] = 96;
    }
  }
  return sharp(data, { raw: { width: iw, height: ih, channels: 3 } }).png().toBuffer();
}

/** What Cloudinary's `c_fill` does: crop a window out of the original at its
 *  own resolution, then resize that window down to the delivered size. */
async function extractAndDeliver(
  originalPng: Buffer,
  cropX: number, cropY: number, cropW: number, cropH: number,
  W: number, H: number,
): Promise<Buffer> {
  return sharp(originalPng)
    .extract({
      left: Math.round(cropX), top: Math.round(cropY),
      width: Math.round(cropW), height: Math.round(cropH),
    })
    .resize(W, H, { fit: 'fill' })
    .png()
    .toBuffer();
}

test('matchCropInImage recovers a known crop from real pixels', async () => {
  const iw = 800, ih = 500, W = 300, H = 250;
  const scale = Math.max(W / iw, H / ih);
  const cropW = W / scale, cropH = H / scale;
  assert.equal(cropH, ih, 'fixture assumption: Y is pinned to the full height');

  const wantOffset = { x: -0.4, y: 0 };
  const { cropX, cropY } = forwardCropWindow(iw, ih, W, H, wantOffset);

  const original = await gradientImage(iw, ih);
  const cropped = await extractAndDeliver(original, cropX, cropY, cropW, cropH, W, H);

  // analysisMaxDim matches the source resolution so the search runs at full
  // precision -- what is under test here is the matching arithmetic, not how
  // much precision a downsampled analysis image costs it.
  const result = await matchCropInImage(original, cropped, { analysisMaxDim: iw });
  assert.equal(result.ok, true, result.ok ? '' : (result as any).reason);
  if (!result.ok) return;

  assert.ok(result.confidence > 0.6, `confidence too low: ${result.confidence}`);
  assert.ok(Math.abs(result.offset.x - wantOffset.x) < 0.1,
    `expected x near ${wantOffset.x}, got ${result.offset.x}`);
  assert.ok(Math.abs(result.offset.y) < 0.1, `expected y near 0, got ${result.offset.y}`);
  assert.equal(result.zoom, 1);
});

test('matchCropInImage reports low confidence rather than a plausible-looking guess', async () => {
  const iw = 800, ih = 500, W = 300, H = 250;
  const original = await gradientImage(iw, ih);
  // Noise unrelated to any part of the gradient: nothing in it should
  // correlate strongly with any position.
  const noise = Buffer.alloc(W * H * 3);
  for (let i = 0; i < noise.length; i++) noise[i] = (i * 2654435761) % 256;
  const croppedNoise = await sharp(noise, { raw: { width: W, height: H, channels: 3 } })
    .png().toBuffer();

  const result = await matchCropInImage(original, croppedNoise, { analysisMaxDim: iw });
  assert.equal(result.ok, false);
  if (result.ok) return;
  assert.match(result.reason, /confidence/i);
});

test('matchCropWindow finds the exact position on a tiny synthetic grid', () => {
  // A small, hand-checkable case, independent of sharp or any image codec.
  // A flat run either side of one distinctive, non-monotonic bump: the flat
  // stretches score -1 (zero variance, so the correlation is undefined and
  // refused rather than guessed at), so the bump at x=8 is the only position
  // that can win. A plain ramp will not do for this -- a 2- or 3-sample
  // window of a straight line is always "perfectly correlated" with any
  // other, which would make every position tie.
  const search = new Uint8Array([5, 5, 5, 5, 5, 5, 5, 5, 40, 10, 45, 8]);
  const sw = 12, sh = 1;
  const template = new Uint8Array([40, 10, 45, 8]);
  const tw = 4, th = 1;
  const best = matchCropWindow(search, sw, sh, template, tw, th, sw - tw, sh - th);
  assert.equal(best.x, 8);
  assert.equal(best.y, 0);
  assert.ok(best.score > 0.99, `expected a near-perfect match, got ${best.score}`);
});

/* ---------------------------------------------------- suggestCrop, refused */

test('suggestCrop refuses with no reference canvas rather than guessing one', async () => {
  const result = await suggestCrop('https://res.cloudinary.com/demo/image/upload/x.jpg', 0, 0);
  assert.equal(result.ok, false);
  if (result.ok) return;
  assert.match(result.reason, /reference canvas/);
});

test('suggestCrop refuses a URL that is not a plain Cloudinary delivery URL', async () => {
  for (const url of [
    '',
    'https://example.com/photo.jpg',
    'https://res.cloudinary.com/demo/image/upload/g_auto,c_fill,w_300,h_250/x.jpg',
    'not a url at all',
  ]) {
    const result = await suggestCrop(url, 300, 250);
    assert.equal(result.ok, false, `expected ${JSON.stringify(url)} to be refused`);
  }
});

test('suggestCrop refuses a cloudinary: reference with no configured cloud name', async () => {
  const had = process.env.CLOUDINARY_CLOUD_NAME;
  delete process.env.CLOUDINARY_CLOUD_NAME;
  try {
    const result = await suggestCrop('cloudinary:smart1-ads/example/photo', 300, 250);
    assert.equal(result.ok, false);
    if (result.ok) return;
    assert.match(result.reason, /CLOUDINARY_CLOUD_NAME/);
  } finally {
    if (had === undefined) delete process.env.CLOUDINARY_CLOUD_NAME;
    else process.env.CLOUDINARY_CLOUD_NAME = had;
  }
});

test('MIN_CONFIDENCE is the one threshold both the module and this file read', () => {
  // Not a claim about its value -- only that there is exactly one, so a
  // change to it cannot silently drift between the source and a copy here.
  assert.ok(MIN_CONFIDENCE > 0 && MIN_CONFIDENCE < 1);
});
