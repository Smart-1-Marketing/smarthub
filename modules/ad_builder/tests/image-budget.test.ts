/**
 * The source-image ceiling, which is the one place that guarantees it.
 *
 * Every ingest path — a customer upload, a Pixabay hit, an AI generation —
 * goes through `fitImageToBudget`, and `imagery.ts` says the rule "holds by
 * construction: there is no path here that skips the enforcer". Nothing
 * tested the enforcer.
 *
 * Two things it was getting wrong, both about what it then *said*:
 *
 * `reencoded` was `encoded.length !== original.length`, which is true of very
 * nearly every image, because the file is always re-encoded and the bytes
 * always differ. Its own description said "had to be compressed or downscaled
 * to fit". So a 600-byte logo came back flagged, with a note saying it had
 * been optimized "to meet the 150 KB limit" — about a file at 0.4% of that
 * limit — and `toFixed(0)` printed its size as "0 KB".
 *
 * And a function whose whole job is to make a file smaller could make it
 * bigger: a high-entropy source already saved hard re-encodes larger at q82.
 * Measured, a 52 KB input came back 137 KB, with the note calling it
 * optimized.
 *
 * Fixtures are small and synthetic on purpose — each call is a real sharp
 * encode, and the quality ladder runs it up to thirteen times.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import sharp from 'sharp';
import { fitImageToBudget, MAX_IMAGE_BYTES } from '../src/image-budget';

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'budget-'));
const out = (n: string) => path.join(dir, n);

/** A flat image compresses to almost nothing: cheap, and well under budget. */
function flat(w: number, h: number, alpha = false) {
  // A *transparent* background when alpha is asked for: a fully opaque alpha
  // channel is dropped by the encoder, so an `alpha: 1` fixture proves nothing
  // about whether transparency survived.
  return sharp({
    create: {
      width: w, height: h, channels: alpha ? 4 : 3,
      background: alpha ? { r: 20, g: 80, b: 160, alpha: 0.5 } : '#1450a0',
    },
  });
}

/**
 * Noise is the worst case for every codec: the way to exceed a budget.
 *
 * Deliberately an LCG rather than `(i * k) % 256`, which is periodic and so
 * compresses beautifully -- a "noise" fixture that PNG squashes to 40 KB
 * tests nothing about a budget of 150.
 */
function noise(w: number, h: number) {
  const px = Buffer.alloc(w * h * 3);
  let seed = 0x2545f491;
  for (let i = 0; i < px.length; i++) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    px[i] = (seed >> 16) & 0xff;
  }
  return sharp(px, { raw: { width: w, height: h, channels: 3 } });
}

test('an image that needed nothing is not reported as having been squeezed', async () => {
  const src = await flat(600, 200, true).png().toBuffer();
  assert.ok(src.length < MAX_IMAGE_BYTES, 'fixture must already fit');

  const r = await fitImageToBudget(src, out('logo.png'), { keepAlpha: true });

  assert.equal(r.reencoded, false, 'nothing had to be done to it');
  assert.equal(r.note, undefined, 'so there is nothing to tell anybody');
});

test('a size a person can read, never "0 KB"', async () => {
  // The note is surfaced in the UI, and `toFixed(0)` printed every
  // sub-kilobyte figure as 0 — so this needs a case where the note is
  // genuinely emitted *and* one of the two figures is tiny. A wide flat image
  // is capped on its dimensions (so it reports) and compresses to almost
  // nothing (so it would have read "to 0 KB").
  const src = await flat(1200, 400, true).png().toBuffer();
  const r = await fitImageToBudget(src, out('tiny.png'), { keepAlpha: true, maxDimension: 200 });

  assert.equal(r.reencoded, true, 'a downscale is a reduction and is reported');
  assert.ok(r.bytes < 512, `fixture must land under half a kilobyte, got ${r.bytes}`);
  assert.match(r.note ?? '', /Optimized from .+ to .+ to meet the 150 KB limit\./);
  assert.doesNotMatch(r.note ?? '', /\b0 KB\b/, 'a size printed as nothing');
});

test('an oversized image is brought under budget', async () => {
  const src = await noise(700, 700).png().toBuffer();
  assert.ok(src.length > MAX_IMAGE_BYTES, 'fixture must start over budget');

  const r = await fitImageToBudget(src, out('fit.jpg'));

  assert.ok(r.bytes <= MAX_IMAGE_BYTES, `${r.bytes} is over budget`);
  assert.equal(fs.statSync(r.file).size, r.bytes, 'the reported size is the file on disk');
  assert.equal(r.reencoded, true);
});

test('an image that genuinely will not fit is refused in words, not written over budget', async () => {
  // The ladder is bounded at twelve steps, and it spends four of every five on
  // quality before it shrinks -- so a large, genuinely incompressible source
  // runs out of steps. That is the right outcome and the message says what to
  // do about it; what would be wrong is writing something over budget and
  // reporting success, because this function is the only thing standing
  // between an ingest path and the rule.
  const src = await noise(1600, 1600).png().toBuffer();

  await assert.rejects(
    () => fitImageToBudget(src, out('impossible.jpg')),
    /Could not compress this image under 150 KB/,
  );
  assert.ok(!fs.existsSync(out('impossible.jpg')), 'nothing should be written on a refusal');
});

test('the function never hands back a bigger file than it was given', async () => {
  // The whole point is reduction. A high-entropy source already saved hard
  // grows under a q82 re-encode, and it was written out and called optimized.
  const src = await noise(900, 600).jpeg({ quality: 30 }).toBuffer();
  assert.ok(src.length < MAX_IMAGE_BYTES, 'fixture must already fit');

  const r = await fitImageToBudget(src, out('nogrow.jpg'));

  assert.ok(r.bytes <= src.length, `grew from ${src.length} to ${r.bytes}`);
  assert.equal(r.reencoded, false, 'and nothing had to be done to it');
});

test('dimensions are capped, and that counts as having been reduced', async () => {
  const src = await flat(3000, 1000).jpeg().toBuffer();

  const r = await fitImageToBudget(src, out('capped.jpg'), { maxDimension: 800 });

  assert.equal(r.width, 800, 'the long edge should be the cap');
  assert.equal(r.reencoded, true, 'a downscale is a reduction and is reported');
});

test('transparency survives when it is asked for, and is flattened when it is not', async () => {
  const src = await flat(400, 400, true).png().toBuffer();

  const kept = await fitImageToBudget(src, out('alpha.png'), { keepAlpha: true });
  assert.equal(kept.format, 'png');
  assert.equal((await sharp(fs.readFileSync(kept.file)).metadata()).hasAlpha, true);

  const flatd = await fitImageToBudget(src, out('flat.jpg'), { keepAlpha: false });
  assert.equal(flatd.format, 'jpg');
  assert.equal((await sharp(fs.readFileSync(flatd.file)).metadata()).hasAlpha, false);
});

test('a file that is not an image is refused, and says so in words', async () => {
  await assert.rejects(
    () => fitImageToBudget(Buffer.from('this is not an image at all'), out('no.jpg')),
    /not a readable image/,
  );
});

test('it accepts a path as readily as a buffer', async () => {
  const src = out('in.png');
  fs.writeFileSync(src, await flat(300, 300).png().toBuffer());

  const r = await fitImageToBudget(src, out('frompath.jpg'));

  assert.ok(fs.existsSync(r.file));
  assert.ok(r.bytes > 0);
});

test('the output directory is created rather than required', async () => {
  const nested = path.join(dir, 'a', 'b', 'c', 'deep.jpg');
  const r = await fitImageToBudget(await flat(200, 200).png().toBuffer(), nested);
  assert.equal(r.file, nested);
  assert.ok(fs.existsSync(nested));
});
