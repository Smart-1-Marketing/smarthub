/**
 * A different layout for one size.
 *
 * `layoutFamily` is the set's answer and decides which sizes exist. A layout
 * that suits a 300x250 is often wrong on a 728x90, and the only way to change
 * it was to change every size with it -- which read as the control not working
 * once somebody had tuned the first size. `layoutBySize` names a family for one
 * canvas; `registry.familyFor()` is the one reading, and every render path asks
 * it, so the preview and the delivered file draw the same family.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { familyFor, templateFor, getTemplate } from '../src/registry';
import { styleFor, styleForSize, carryFor } from '../src/carry';
import { validateCampaign } from '../src/validate';
import { renderPreview } from '../src/render';
import type { Campaign, CreativeConcept } from '../src/types';

const ROOT = path.resolve(__dirname, '..');
const example = (): Campaign => JSON.parse(fs.readFileSync(path.join(ROOT, 'src/examples/icon-solar.json'), 'utf8'));

test('a size renders from its own family when it has one, and the set\'s otherwise', () => {
  const c = { layoutFamily: 'T01', layoutBySize: { '728x90': 'T04', '300x600': 'T99' } };
  assert.equal(familyFor(c, '728x90'), 'T04');
  assert.equal(familyFor(c, '300x250'), 'T01', 'no pick, the set\'s family');
  assert.equal(familyFor(c, '300x600'), 'T01', 'an unknown family falls back rather than 422ing');
  assert.equal(templateFor(c, '728x90').id, 'T04');
  assert.equal(familyFor({ layoutFamily: 'T02' }, '728x90'), 'T02');
});

test('a per-size pick that does not draw the size falls back to the set', () => {
  // Every shipped family draws every size today, so the guard is exercised
  // against a family whose size list has been narrowed on purpose.
  const t = getTemplate('T04');
  const sizes = t.sizes as Record<string, unknown>;
  const kept = sizes['160x600'];
  delete sizes['160x600'];
  try {
    assert.equal(familyFor({ layoutFamily: 'T01', layoutBySize: { '160x600': 'T04' } }, '160x600'), 'T01');
  } finally {
    sizes['160x600'] = kept;
  }
});

test('the carry reads the departure against the authored size\'s own family', () => {
  const concept = {
    conceptId: 'A', name: 'x', layoutFamily: 'T01', layoutBySize: { '728x90': 'T04' },
    hero: {}, copy: { default: { headline: 'h' } },
    styleOverrides: { authoredFor: '300x250', headline: { size: 40, color: '#FF0000' } },
  } as unknown as CreativeConcept;
  // The same answer as styleForSize handed T04 for the target and T01 for the
  // source explicitly -- which is the pairing a caller reading
  // `getTemplate(concept.layoutFamily)` for both sides gets wrong.
  const expected = styleForSize(concept.styleOverrides, getTemplate('T04'), '728x90', getTemplate('T01'));
  assert.deepEqual(styleFor(concept, '728x90'), expected);
  assert.equal(styleFor(concept, '728x90')?.headline?.color, '#FF0000', 'color carries verbatim');
  assert.ok(carryFor(concept, '728x90').carried);
  assert.deepEqual(carryFor(concept, '728x90').from, '300x250');
  // On the authored size nothing is carried.
  assert.equal(carryFor(concept, '300x250').carried, false);
});

test('the validator names a per-size family that does not exist or does not draw the size', () => {
  const campaign = example();
  const c = campaign.concepts[0];
  (c as any).layoutBySize = { '728x90': 'T99', '300x250': 'T04' };
  const out = validateCampaign(campaign, { assetRoot: ROOT });
  const messages = out.map((x) => `${x.field}: ${x.message}`);
  assert.ok(messages.some((m: string) => m.includes('layoutBySize.728x90') && m.includes('T99')), messages.join('\n'));
  assert.ok(!messages.some((m: string) => m.includes('layoutBySize.300x250')), 'a real family that draws the size is fine');
});

test('the preview says which family drew the size', async () => {
  const campaign = example();
  const concept = campaign.concepts[0];
  (concept as any).layoutBySize = { '728x90': 'T04' };
  const a = await renderPreview({ brand: campaign.brand, concept, platform: 'google', size: '300x250', assetRoot: ROOT });
  const b = await renderPreview({ brand: campaign.brand, concept, platform: 'google', size: '728x90', assetRoot: ROOT });
  assert.equal(a.layoutFamily, concept.layoutFamily);
  assert.equal(b.layoutFamily, 'T04');
  assert.ok(typeof b.fontSizes.headline === 'number' && b.fontSizes.headline > 0, 'fitted type sizes travel with the preview');
});
