/**
 * A hand-typed headline that carries pressure language reached QA clean.
 *
 * `copywriter.ts`'s `sanitise()` is the only gate on pressure language
 * anywhere in this renderer, and #323 fixed the one bug in it -- but that fix
 * still only runs on copy `generateCopy()` produced. `qa.ts` measures
 * dimensions, weight, contrast, safe areas, collisions and text coverage and
 * reads no words at all, so an operator who types "Hurry" straight into the
 * build screen bypasses `sanitise()` entirely and nothing downstream ever
 * looks at the copy again. This is the read that was missing: it cannot cut
 * the phrase out -- an operator's own words are not this check's to rewrite
 * -- but it can refuse to let it ship silently.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { renderPreview } from '../src/render';
import { bannedPressurePhrase, PRESSURE_PHRASES } from '../src/copywriter';
import type { Campaign } from '../src/types';

const ROOT = path.resolve(__dirname, '..');
const campaign: Campaign = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'src/examples/icon-solar.json'), 'utf8'),
);

function finding(qa: { check: string; status: string; detail: string }[], key: string) {
  return qa.find((f) => f.check === key);
}

/* -------------------------------------------------------- bannedPressurePhrase */

test('bannedPressurePhrase finds every phrase on the shipped list', () => {
  for (const phrase of PRESSURE_PHRASES) {
    assert.equal(
      bannedPressurePhrase(`Book service ${phrase} in Riverside`),
      phrase,
      `did not catch "${phrase}"`,
    );
  }
});

test('bannedPressurePhrase is case-insensitive and word-bounded', () => {
  assert.equal(bannedPressurePhrase('HURRY and book today'), 'HURRY');
  // "hurrying" must not trip the bare word "hurry" -- a real word containing
  // one is not the phrase.
  assert.equal(bannedPressurePhrase('We are hurrying to help you'), undefined);
});

test('bannedPressurePhrase catches a curly apostrophe, not only a plain one', () => {
  // "don't miss out" is written with a plain ' in PRESSURE_PHRASES, and this
  // function's whole job is text an operator typed rather than text a model
  // generated -- which is exactly where a curly ’ (a phone's smart
  // punctuation, or a paste from a word processor) shows up instead.
  assert.equal(bannedPressurePhrase('Don’t miss out on this offer'), "Don't miss out");
  assert.equal(bannedPressurePhrase('Don‘t miss out on this offer'), "Don't miss out");
});

test('bannedPressurePhrase returns undefined for clean text and for nothing', () => {
  assert.equal(bannedPressurePhrase('Same-day furnace repair'), undefined);
  assert.equal(bannedPressurePhrase(undefined), undefined);
  assert.equal(bannedPressurePhrase(null), undefined);
  assert.equal(bannedPressurePhrase(''), undefined);
});

/* --------------------------------------------------------------------- runQa */

test('hand-typed pressure language in the headline fails QA', async () => {
  const concept = {
    ...campaign.concepts[0],
    copy: { ...campaign.concepts[0].copy, default: { ...campaign.concepts[0].copy.default, headline: 'Hurry, solar prices are rising' } },
  };
  const out = await renderPreview({
    brand: campaign.brand, concept, platform: 'google', size: '300x250', assetRoot: ROOT,
  });
  const f = finding(out.qa, 'pressure-language');
  assert.ok(f, 'the check should run and report');
  assert.equal(f!.status, 'fail');
  assert.match(f!.detail, /headline/);
  assert.match(f!.detail, /"Hurry"/i);
});

test('every text field is checked, not only the headline', async () => {
  // 300x250's own copy in the fixture overrides some roles and not others
  // (`copyForSize` merges `{...default, ...specific}`, specific winning) --
  // so the size-specific entry is fully populated here rather than partially
  // overridden, or a role this fixture's 300x250 entry already sets would
  // silently mask whatever this test wrote onto `default`.
  const clean = {
    headline: 'Same-day furnace repair',
    support: 'Licensed technicians across Riverside County',
    cta: 'Get Estimate',
    offer: 'Free Estimate',
    trust: 'Rated well by neighbors',
  };
  for (const [role, phrase] of [
    ['headline', 'Act now for a free estimate'],
    ['support', 'Last chance to save this month'],
    ['cta', 'Hurry'],
    ['offer', "Don't miss out on savings"],
    ['trust', 'Limited time only rated 5 stars'],
  ] as const) {
    const concept = {
      ...campaign.concepts[0],
      copy: { ...campaign.concepts[0].copy, '300x250': { ...clean, [role]: phrase } },
    };
    const out = await renderPreview({
      brand: campaign.brand, concept, platform: 'google', size: '300x250', assetRoot: ROOT,
    });
    const f = finding(out.qa, 'pressure-language');
    assert.ok(f, `${role} carrying "${phrase}" should have been caught`);
    assert.equal(f!.status, 'fail', `${role}: ${f?.detail}`);
  }
});

test('clean copy passes the check rather than simply not failing it', async () => {
  const out = await renderPreview({
    brand: campaign.brand, concept: campaign.concepts[0], platform: 'google', size: '300x250', assetRoot: ROOT,
  });
  const f = finding(out.qa, 'pressure-language');
  assert.ok(f, 'the check should still report on clean copy, so its absence is never mistaken for having run');
  assert.equal(f!.status, 'pass');
});

test('qa.ts reads the shared list rather than keeping a second copy of it', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '../src/qa.ts'), 'utf8');
  assert.match(src, /import\s*\{\s*bannedPressurePhrase\s*\}\s*from\s*'\.\/copywriter'/, 'qa.ts must import the check from copywriter.ts');
  const alternations = src.match(/hurry\|act now/g) ?? [];
  assert.equal(
    alternations.length,
    0,
    'the phrase alternation is written out by hand in qa.ts -- it must be built from copywriter.ts\'s PRESSURE_PHRASES only',
  );
});
