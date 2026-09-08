import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import sharp from 'sharp';
import { compose } from '../src/svg';
import { getTemplate } from '../src/registry';
import { validateCampaign } from '../src/validate';
import type { Brand, Campaign } from '../src/types';

const brand: Brand = {
  name: 'Image control test', domain: 'example.test',
  colors: { primary: '#17385A', secondary: '#335577', accent: '#EEAA22', light: '#FFFFFF', dark: '#111111' },
  fonts: { headline: 'Montserrat', body: 'Open Sans' }, logos: { primary: '' },
};

test('Campaign validation checks hero files only when the design uses them', () => {
  const concept = { conceptId: 'image-control', name: 'Image control', layoutFamily: 'T01',
    hero: { landscape: '/missing/hero.jpg' }, copy: { default: { headline: 'Reliable home services' } } };
  const campaign = { requestId: 'image-control', brand, concepts: [concept] } as Campaign;
  const heroFindings = (design: typeof concept & { hideHero?: boolean; backgroundImage?: string }) =>
    validateCampaign({ ...campaign, concepts: [design] }, { assetRoot: '.', platforms: [] })
      .filter(f => f.field.includes('.hero.'));
  assert.ok(heroFindings(concept).some(f => f.level === 'error'));
  assert.deepEqual(heroFindings({ ...concept, hideHero: true }), []);
  assert.deepEqual(heroFindings({ ...concept, backgroundImage: '/replacement.jpg' }), []);
});

test('No image suppresses the hero, restores it without losing the asset, and allows a replacement background', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ad-no-image-'));
  try {
    const file = path.join(dir, 'hero.png');
    await sharp({ create: { width: 100, height: 100, channels: 3, background: '#FF0000' } }).png().toFile(file);
    const input = { layout: getTemplate('T01').sizes['300x250']!, brand,
      copy: { headline: 'Reliable home services' }, hero: { landscape: file }, scale: 1, includeLogo: false };
    const original = await compose(input);
    assert.equal(original.images.some(p => p.role === 'hero'), true);
    const hidden = await compose({ ...input, hideHero: true });
    assert.equal(hidden.images.length, 0);
    assert.deepEqual(hidden.missingAssets, ['(no logo supplied)']);
    assert.doesNotMatch(hidden.svg, /<image\b/);
    const restored = await compose({ ...input, hideHero: false });
    assert.equal(restored.svg, original.svg);
    const replacement = await compose({ ...input, hideHero: true, backgroundImage: file });
    assert.equal(replacement.images.some(p => p.role === 'background'), true);
    assert.equal(replacement.images.some(p => p.role === 'hero'), false);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('No image can render even when the old hero file has gone missing', async () => {
  const result = await compose({ layout: getTemplate('T01').sizes['300x250']!, brand,
    copy: { headline: 'Reliable home services' }, hero: { landscape: '/missing/placeholder.jpg' },
    hideHero: true, scale: 1, includeLogo: false });
  // This fixture intentionally has no advertiser logo; the hidden hero must
  // not add a missing-image failure of its own.
  assert.deepEqual(result.missingAssets, ['(no logo supplied)']);
  assert.equal(result.images.length, 0);
});
