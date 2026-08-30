/**
 * The two checks a finished ad could fail without any other check noticing.
 *
 * Softness and text weight both sit inside the safe area, collide with
 * nothing, pass contrast, and compress to a comfortable file size. They are
 * found by somebody looking at the proof, which is after the render has been
 * paid for -- so they are asserted here against real renders rather than
 * against the presence of the code that performs them.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import sharp from 'sharp';
import { renderPreview } from '../src/render';
import { coverageVerdict } from '../src/qa';
import { getPlatform, loadTemplates } from '../src/registry';
import type { Campaign, SizeKey } from '../src/types';

const ROOT = path.resolve(__dirname, '..');
const campaign: Campaign = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'src/examples/icon-solar.json'), 'utf8'),
);

function finding(qa: { check: string; status: string; detail: string }[], key: string) {
  return qa.find((f) => f.check === key);
}

/** A flat JPEG of a given square size, standing in for a supplied photograph. */
async function square(px: number): Promise<string> {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'adqa-'));
  const file = path.join(dir, `${px}.jpg`);
  await sharp({
    create: { width: px, height: px, channels: 3, background: { r: 90, g: 120, b: 160 } },
  })
    .jpeg()
    .toFile(file);
  return file;
}

test('a photograph painted past its own pixels is reported', async () => {
  // Amazon's billboard delivers at 2x, so a 970x250 placement asks for
  // 1940x500 -- which is where an ordinary 1024px generated still runs out.
  const concept = { ...campaign.concepts[0], backgroundImage: await square(1024) };
  const out = await renderPreview({
    brand: campaign.brand, concept, platform: 'amazon', size: '970x250', assetRoot: ROOT,
  });
  const f = finding(out.qa, 'source-resolution');
  assert.ok(f, 'the check ran');
  assert.equal(f!.status, 'warn');
  assert.match(f!.detail, /1024x1024/, 'it names the pixels the source actually has');
  assert.match(f!.detail, /1940x/, 'and the pixels it was asked for at delivery scale');
});

test('the same photograph at a size it covers is not reported', async () => {
  const concept = { ...campaign.concepts[0], backgroundImage: await square(1024) };
  const out = await renderPreview({
    brand: campaign.brand, concept, platform: 'google', size: '300x250', assetRoot: ROOT,
  });
  assert.equal(finding(out.qa, 'source-resolution')!.status, 'pass');
});

test('softness is never a fail — it is a judgment somebody may ship on', async () => {
  // 320px into Amazon's 1940x500 is as stretched as this gets. If even that
  // blocks delivery, the override click becomes routine and stops meaning
  // anything on the checks that must genuinely stop one.
  const concept = { ...campaign.concepts[0], backgroundImage: await square(320) };
  const out = await renderPreview({
    brand: campaign.brand, concept, platform: 'amazon', size: '970x250', assetRoot: ROOT,
  });
  const f = finding(out.qa, 'source-resolution')!;
  assert.equal(f.status, 'warn');
  assert.notEqual(f.status, 'fail');
});

test('text coverage is asked on Meta and asked nowhere else', async () => {
  const concept = campaign.concepts[0];
  const onMeta = await renderPreview({
    brand: campaign.brand, concept, platform: 'meta', size: '1080x1080', assetRoot: ROOT,
  });
  assert.ok(finding(onMeta.qa, 'text-coverage'), 'Meta publishes the guideline, so Meta is asked');

  // A 300x250 banner is mostly type by design. Asking there would put an
  // amber chip on every display size in every campaign, which is how amber
  // comes to mean nothing.
  const onGoogle = await renderPreview({
    brand: campaign.brand, concept, platform: 'google', size: '300x250', assetRoot: ROOT,
  });
  assert.equal(finding(onGoogle.qa, 'text-coverage'), undefined);
});

test('text coverage separates a photo layout from an all-type one', async () => {
  const results: Record<string, number> = {};
  for (const concept of campaign.concepts) {
    const out = await renderPreview({
      brand: campaign.brand, concept, platform: 'meta', size: '1080x1080', assetRoot: ROOT,
    });
    const f = finding(out.qa, 'text-coverage')!;
    results[concept.layoutFamily] = Number(f.detail.match(/([\d.]+)%/)![1]);
  }
  const values = Object.values(results);
  assert.ok(
    Math.max(...values) - Math.min(...values) > 10,
    `the measure must discriminate between layouts, got ${JSON.stringify(results)}`,
  );
});

test('the coverage verdict is taken on the number that is printed', () => {
  // Asserted directly rather than through a render: neither fixture happens to
  // land on 20%, so a version that rounded for the screen and judged the
  // unrounded value passed the render-driven version of this test by luck.
  for (const [pct, shown, over] of [
    [19.94, '19.9%', false],   // rounds down, under — under either way
    [20.0, '20.0%', false],    // exactly the limit is not over it
    [20.04, '20.0%', false],   // prints 20.0, so it must not be called over 20
    [20.05, '20.1%', true],    // prints 20.1, so it must be
    [40.02, '40.0%', true],
  ] as [number, string, boolean][]) {
    const v = coverageVerdict(pct, 20);
    assert.equal(v.shown, shown, `${pct} prints as ${shown}`);
    assert.equal(v.over, over, `${pct} printed as ${v.shown} against a 20% limit`);
  }
});

test('no rendered size prints a number that contradicts its own verdict', async () => {
  for (const concept of campaign.concepts) {
    for (const size of ['1080x1080', '1200x628', '1080x1350', '1080x1920'] as SizeKey[]) {
      const out = await renderPreview({
        brand: campaign.brand, concept, platform: 'meta', size, assetRoot: ROOT,
      });
      const f = finding(out.qa, 'text-coverage')!;
      const shown = Number(f.detail.match(/([\d.]+)%/)![1]);
      const limit = getPlatform('meta').sizes[size]!.textCoverageWarnPct!;
      assert.equal(
        f.status === 'warn', shown > limit,
        `${concept.conceptId}/${size}: printed ${shown}%, limit ${limit}%, said ${f.status}`,
      );
    }
  }
});

test('every Meta size carries the guideline, and no other platform does', () => {
  for (const [size, rule] of Object.entries(getPlatform('meta').sizes)) {
    assert.equal(rule!.textCoverageWarnPct, 20, `meta ${size}`);
  }
  for (const p of ['google', 'amazon']) {
    for (const [size, rule] of Object.entries(getPlatform(p).sizes)) {
      assert.equal(rule!.textCoverageWarnPct, undefined, `${p} ${size} must not be asked`);
    }
  }
});

test('a family that draws no photograph still answers the resolution check', async () => {
  // T04 is all type on a brand field. Nothing is placed, so there is nothing
  // to measure -- and the honest answer is no finding at all rather than a
  // green tick over a question that was never asked.
  const flat = campaign.concepts.find((c) => c.layoutFamily === 'T04');
  if (!flat) return;
  const { backgroundImage, ...noPhoto } = flat as Record<string, unknown>;
  const out = await renderPreview({
    brand: campaign.brand, concept: noPhoto as never, platform: 'google',
    size: '300x250', assetRoot: ROOT,
  });
  const f = finding(out.qa, 'source-resolution');
  if (f) assert.equal(f.status, 'pass');
});

test('the new Google responsive assets render at their exact pixels', async () => {
  const cfg = getPlatform('google');
  for (const size of ['1200x628', '1200x1200', '1200x1500'] as SizeKey[]) {
    assert.ok(cfg.sizes[size], `google buys ${size}`);
    const out = await renderPreview({
      brand: campaign.brand, concept: campaign.concepts[0], platform: 'google', size, assetRoot: ROOT,
    });
    const meta = await sharp(out.png).metadata();
    const [w, h] = size.split('x').map(Number);
    assert.equal(meta.width, w);
    assert.equal(meta.height, h);
    assert.ok(out.png.length < cfg.sizes[size]!.maxFileBytes, 'inside the asset ceiling');
    assert.deepEqual(out.qa.filter((f) => f.status === 'fail'), [], `${size} has no hard failure`);
  }
});

test('every family can draw all three shapes Google composes from', () => {
  for (const [id, t] of loadTemplates()) {
    for (const [key, w, h] of [
      ['1200x628', 1200, 628], ['1200x1200', 1200, 1200], ['1200x1500', 1200, 1500],
    ] as [SizeKey, number, number][]) {
      assert.ok(t.sizes[key], `${id} draws ${key}`);
      const c = t.sizes[key]!.canvas;
      assert.deepEqual({ w: c.w, h: c.h }, { w, h }, `${id} ${key} canvas`);
    }
  }
});

test('a derived Google asset is its Meta twin scaled, not a fresh guess', () => {
  // The two derived shapes are the same aspect ratio 10/9 larger, so every
  // length is exactly 10/9 of the source rounded to a pixel. If that stops
  // being true, somebody has hand-edited one of them and the two shapes no
  // longer say the same thing about where the copy sits.
  const S = 1200 / 1080;
  for (const [id, t] of loadTemplates()) {
    for (const [from, to] of [['1080x1080', '1200x1200'], ['1080x1350', '1200x1500']] as [SizeKey, SizeKey][]) {
      const a = t.sizes[from]!;
      const b = t.sizes[to]!;
      for (const role of ['logo', 'headline', 'support', 'offer', 'trust', 'cta'] as const) {
        const src = (a as Record<string, any>)[role];
        const dst = (b as Record<string, any>)[role];
        if (!src) { assert.equal(dst, undefined, `${id} ${to} ${role} appeared from nowhere`); continue; }
        for (const k of ['x', 'y', 'w', 'h'] as const) {
          assert.equal(dst[k], Math.round(src[k] * S), `${id} ${to} ${role}.${k}`);
        }
      }
    }
  }
});

test("Meta's interface exclusion zone does not travel to a Google asset", () => {
  // A Google responsive asset is composed into a unit Google draws. Carrying
  // Meta's story safe zone across would reserve space against a platform that
  // is not showing anything there.
  for (const [id, t] of loadTemplates()) {
    for (const key of ['1200x1200', '1200x1500'] as SizeKey[]) {
      assert.equal(t.sizes[key]!.safeZone, undefined, `${id} ${key}`);
    }
  }
});

test('the Amazon medium rectangle carries a rule, not an assumption', () => {
  // It was 40 KB by analogy with two neighbours. Amazon publishes a rule that
  // covers it -- 40 KB for every static desktop unit except the billboard --
  // so the number is unchanged and the claim behind it is not.
  const amazon = getPlatform('amazon');
  assert.equal(amazon.sizes['300x250']!.maxFileBytes, 40960);
  assert.equal(amazon.sizes['970x250']!.maxFileBytes, 204800, 'the billboard is the exception');
  for (const key of ['300x250', '728x90', '160x600'] as SizeKey[]) {
    assert.equal(amazon.sizes[key]!.maxFileBytes, 40960, `${key} takes the non-billboard rule`);
  }
  assert.ok(
    !/VERIFY/.test(JSON.stringify(amazon.sizes['300x250'])),
    'and it is no longer flagged as needing confirmation',
  );
});
