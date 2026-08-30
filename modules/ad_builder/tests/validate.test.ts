/**
 * The gate in front of every render.
 *
 * `validateCampaign` decides whether a campaign is built at all, and the
 * failure it exists to stop is stated in its own header: a new client's ads
 * rendering "successfully" in the wrong typeface because their brand font was
 * not in the registry and the renderer quietly fell back. A silent
 * substitution is worse than a hard stop, because the proof looks finished
 * and so nobody checks.
 *
 * 194 lines with no tests until now. These are about the two things a reading
 * of the source will not tell you: which findings are errors (only an error
 * stops a render — `renderable` is `!findings.some(level === 'error')`), and
 * that a campaign which is genuinely fine produces none of them.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as path from 'node:path';
import { validateCampaign } from '../src/validate';
import { listFamilies } from '../src/fonts';
import { getTemplate } from '../src/registry';
import type { Campaign } from '../src/types';

const ROOT = path.resolve(__dirname, '..');
const FONT = listFamilies()[0];

/** A campaign with nothing wrong with it. */
function good(): Campaign {
  return {
    requestId: 'AD-2026-000001',
    campaignName: 'Spring Service',
    brand: {
      name: 'Riverside HVAC',
      domain: 'riverside-hvac.example',
      colors: { primary: '#0F2A44', secondary: '#1D4E76', accent: '#F2B705',
                light: '#FFFFFF', dark: '#12202E' },
      fonts: { headline: FONT, body: FONT },
      logos: { primary: 'assets/brand/icon-solar-primary.png' },
    },
    concepts: [{ conceptId: 'A', name: 'Direct', layoutFamily: 'T07', copy: {
      default: { headline: 'Heat back on today', support: 'Same-day repair', cta: 'Book Now' },
    } }],
  } as unknown as Campaign;
}

const errors = (c: Campaign) =>
  validateCampaign(c, { assetRoot: ROOT }).filter((x) => x.level === 'error');
const fields = (c: Campaign) => errors(c).map((x) => x.field);

/* ------------------------------------------------- the case that must pass */

test('a campaign with nothing wrong with it produces no errors', () => {
  // The half that matters most. A validator that refuses everything stops no
  // bad ad and stops every good one, and it is the direction a check drifts
  // as rules are added to it.
  assert.deepEqual(fields(good()), [],
                   'T07 is type-only, so it needs no hero image');
});

/* ---------------------------------------------------------- the substitution */

test('a font the renderer does not have is an error, not a warning', () => {
  // The whole reason this file exists. A warning here renders the ad in the
  // fallback face and calls it a success.
  const c = good();
  (c.brand as any).fonts.headline = 'Definitely Not Installed Sans';
  const found = errors(c);
  assert.deepEqual(found.map((x) => x.field), ['brand.fonts.headline']);
  assert.match(found[0].message, /silently render in the fallback face/);
  assert.match(found[0].message, new RegExp(FONT.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
               'and it names what IS available, so the message is actionable');
});

test('a missing font is an error too', () => {
  const c = good();
  (c.brand as any).fonts.body = '   ';
  assert.deepEqual(fields(c), ['brand.fonts.body']);
});

/* ------------------------------------------------------------------ colors */

test('every one of the five roles is required, because templates name them', () => {
  const c = good();
  delete (c.brand as any).colors.accent;
  assert.deepEqual(fields(c), ['brand.colors.accent']);
});

test('a value that is not a hex color is refused rather than resolved', () => {
  const c = good();
  (c.brand as any).colors.accent = 'goldenrod';
  const found = errors(c);
  assert.deepEqual(found.map((x) => x.field), ['brand.colors.accent']);
  assert.match(found[0].message, /not a hex color/);
});

test('light and dark being the same color is its own error', () => {
  // Not a missing value -- both are present and both are valid. It is caught
  // because every contrast check downstream resolves ink against these two by
  // name, so identical values fail all of them for a reason no QA finding
  // would explain.
  const c = good();
  (c.brand as any).colors.light = '#FFFFFF';
  (c.brand as any).colors.dark = '#ffffff';
  assert.deepEqual(fields(c), ['brand.colors'], 'and case does not hide it');
});

/* ------------------------------------------------------------------- logos */

test('a logo is required, because QA fails any ad without an advertiser', () => {
  const c = good();
  delete (c.brand as any).logos.primary;
  assert.deepEqual(fields(c), ['brand.logos.primary']);
});

test('a logo path that is not on disk is caught here, not at render time', () => {
  const c = good();
  (c.brand as any).logos.primary = 'assets/brand/no-such-logo.png';
  const found = errors(c);
  assert.deepEqual(found.map((x) => x.field), ['brand.logos.primary']);
  assert.match(found[0].message, /file not found/);
});

test('a reverse logo is optional, but a named one must exist', () => {
  const ok = good();
  assert.deepEqual(fields(ok), [], 'absent is fine');
  const c = good();
  (c.brand as any).logos.reverse = 'assets/brand/nope.png';
  assert.deepEqual(fields(c), ['brand.logos.reverse']);
});

/* ---------------------------------------------------------------- identity */

test('the two fields that name output files are required', () => {
  const c = good();
  c.requestId = '';
  (c as any).campaignName = '  ';
  assert.deepEqual(fields(c).sort(), ['campaignName', 'requestId']);
});

/* ---------------------------------------------------------------- concepts */

test('a campaign with no concepts stops there rather than reporting more', () => {
  const c = good();
  (c as any).concepts = [];
  const found = errors(c);
  assert.deepEqual(found.map((x) => x.field), ['concepts'],
                   'nothing downstream of an empty list is worth saying');
});

test('an unknown layout family is an error and names the ones that exist', () => {
  const c = good();
  (c.concepts[0] as any).layoutFamily = 'T99';
  const found = errors(c);
  assert.deepEqual(found.map((x) => x.field), ['concepts[A].layoutFamily']);
  assert.match(found[0].message, /T01/, 'the message lists the real families');
});

test('no default copy is only a warning when every size supplies its own', () => {
  // The sentence the validator prints is "every size must then define its
  // own", and it means it: the warning is raised, and then the per-size check
  // decides whether the campaign renders. Both halves are the contract, and
  // conflating them is how a set of ads with no headline gets built.
  const c = good();
  delete (c.concepts[0] as any).copy.default;

  // Nothing anywhere: the warning AND an error for every size in the layout.
  const bare = validateCampaign(c, { assetRoot: ROOT, platforms: ['google'] });
  assert.ok(bare.some((x) => x.level === 'warning' && x.field === 'concepts[A].copy.default'),
            'the warning is said');
  assert.ok(bare.some((x) => x.level === 'error' && /headline is required/.test(x.message)),
            'and a size with no copy at all is an error, not a shrug');

  // Every size covered: the warning stands, and nothing blocks.
  const line = { headline: 'Heat back on today', support: 'Same-day repair', cta: 'Book Now' };
  // Every size the LAYOUT draws, which is the set the validator walks -- not
  // the platform's, which is a subset of it.
  for (const size of Object.keys(getTemplate('T07').sizes)) {
    (c.concepts[0] as any).copy[size] = { ...line };
  }
  const covered = validateCampaign(c, { assetRoot: ROOT, platforms: ['google'] });
  assert.deepEqual(covered.filter((x) => x.level === 'error').map((x) => x.field), [],
                   'nothing blocking');
  assert.ok(covered.some((x) => x.level === 'warning' && x.field === 'concepts[A].copy.default'),
            'but it is still worth saying');
});

/* --------------------------------------------------------------- platforms */

test('an unknown platform is an error', () => {
  const found = validateCampaign(good(), { assetRoot: ROOT, platforms: ['tiktok'] })
    .filter((x) => x.level === 'error');
  assert.deepEqual(found.map((x) => x.field), ['platform']);
});

test('every platform the registry ships validates clean', () => {
  for (const p of ['google', 'meta', 'amazon']) {
    assert.deepEqual(
      validateCampaign(good(), { assetRoot: ROOT, platforms: [p] })
        .filter((x) => x.level === 'error').map((x) => x.field),
      [], `${p} is buyable`);
  }
});
