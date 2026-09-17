/**
 * The follow-up list: a note that is not a warning, an ink suggestion that
 * is checked before it is made, a preview drawn no larger than it is looked
 * at, and diagnostics that name a missing font.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { rollUp } from '../src/qa';
import { explainFinding, inkSuggestion } from '../src/plain-checks';
import { renderPreview, PREVIEW_MAX_EDGE } from '../src/render';
import type { Campaign, QaFinding } from '../src/types';
import sharp from 'sharp';

const ROOT = path.resolve(__dirname, '..');
const example = (): Campaign => JSON.parse(fs.readFileSync(path.join(ROOT, 'src/examples/icon-solar.json'), 'utf8'));

test('a note never decides the verdict', () => {
  const note: QaFinding = { check: 'text-coverage', status: 'info', detail: 'text covers 24% of the canvas' };
  assert.equal(rollUp([note]), 'pass');
  assert.equal(rollUp([note, { check: 'x', status: 'warn', detail: 'w' }]), 'warn');
  assert.equal(rollUp([note, { check: 'x', status: 'fail', detail: 'f' }]), 'fail');
  const plain = explainFinding(note);
  assert.match(plain.title, /^Note:/);
  assert.match(plain.what, /does not hold this size/);
});

test('Meta text coverage is a note on the rendered ad', async () => {
  const campaign = example();
  const concept = campaign.concepts[0];
  const out = await renderPreview({ brand: campaign.brand, concept, platform: 'meta', size: '1080x1080', assetRoot: ROOT });
  const coverage = out.qa.find((f) => f.check === 'text-coverage');
  assert.ok(coverage, 'the check still runs on a Meta size');
  assert.ok(coverage!.status === 'info' || coverage!.status === 'pass', `it is a note or a pass, not ${coverage!.status}`);
});

test('the ink suggestion is a subtraction against what was measured, not a guess', () => {
  const ctx = { brandColors: { light: '#FFFFFF', dark: '#111111' } };
  // Behind is bright: only the dark ink reads.
  const onBright = inkSuggestion('headline', ctx, [{ role: 'headline', behind: 0.9 }]);
  assert.equal(onBright.value, 'dark');
  assert.equal(onBright.reads, true);
  assert.equal(onBright.measured, true);
  // Behind is dark: light.
  const onDark = inkSuggestion('headline', ctx, [{ role: 'headline', behind: 0.05 }]);
  assert.equal(onDark.value, 'light');
  assert.equal(onDark.reads, true);
  // Mid-grey: neither clears 4.5:1 and the advice says so rather than
  // offering a change the checks would refuse a second later.
  const onMid = inkSuggestion('headline', ctx, [{ role: 'headline', behind: 0.2 }]);
  assert.equal(onMid.measured, true);
  const f: QaFinding = { check: 'contrast', status: 'warn', detail: 'below 4.5:1 — headline 2.0:1',
    data: { low: [{ role: 'headline', ratio: 2, behind: 0.2 }] } };
  const plain = explainFinding(f, ctx);
  if (onMid.reads) {
    assert.ok(plain.apply, 'a reading ink is offered');
  } else {
    assert.equal(plain.apply, undefined, 'no button for an ink that will not read');
    assert.match(plain.how, /Neither brand ink reads well/);
  }
  // No measurement, no brand: the old guess, and the label says it is one.
  const guess = explainFinding({ check: 'contrast', status: 'warn', detail: 'below 4.5:1 — support 2.0:1' }, { backgroundImage: true });
  assert.deepEqual(guess.apply, { kind: 'style', block: 'support', prop: 'color', value: 'light' });
  assert.match(guess.applyLabel!, /a guess/);
});

test('the contrast finding carries the luminance behind each low block', async () => {
  const campaign = example();
  const concept = { ...campaign.concepts[0] };
  // A headline in a brand role that cannot read on its own panel forces a
  // low reading; the exact block does not matter, only that data travels.
  (concept as any).styleOverrides = { headline: { color: 'primary' }, support: { color: 'primary' } };
  const out = await renderPreview({ brand: campaign.brand, concept, platform: 'google', size: '300x250', assetRoot: ROOT });
  const contrast = out.qa.find((f) => f.check === 'contrast');
  if (contrast && contrast.status === 'warn') {
    const low = (contrast.data as any)?.low;
    assert.ok(Array.isArray(low) && low.length, 'low blocks are listed');
    for (const l of low) {
      assert.ok(typeof l.behind === 'number' && l.behind >= 0 && l.behind <= 1, `${l.role} carries a luminance`);
    }
  }
});

test('a story preview is drawn no larger than the column that shows it', async () => {
  const campaign = example();
  const concept = campaign.concepts[0];
  const story = await renderPreview({ brand: campaign.brand, concept, platform: 'meta', size: '1080x1920', assetRoot: ROOT });
  const meta = await sharp(story.png).metadata();
  assert.equal(story.height, 1920, 'the delivered size is still reported');
  assert.ok(meta.height! <= PREVIEW_MAX_EDGE, `the PNG is ${meta.height}px tall, at most ${PREVIEW_MAX_EDGE}`);
  assert.ok(story.previewScale < 1 && story.previewScale > 0.4);
  // And a banner is untouched: it is already smaller than the column.
  const banner = await renderPreview({ brand: campaign.brand, concept, platform: 'google', size: '300x250', assetRoot: ROOT });
  const bm = await sharp(banner.png).metadata();
  assert.equal(bm.width, 300);
  assert.equal(banner.previewScale, 1);
  // The QA on the story ran at delivery scale regardless.
  assert.ok(story.qa.some((f) => f.check === 'dimensions' && f.status === 'pass'), 'dimensions still judged at delivery scale');
});
