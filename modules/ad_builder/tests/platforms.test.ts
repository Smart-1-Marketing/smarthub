/**
 * Which platforms a campaign may be bought on, and who decides.
 *
 * These exist because `meta.json` sat in `src/config/platforms` with four
 * sizes every template already carried, and no campaign could be built for
 * it. Three separate `.filter(p => p === 'google' || p === 'amazon')` calls
 * dropped it: no error, no warning, and a Meta buy came back as a set of
 * Google banners. The failure is invisible from both ends -- the config is
 * right, the templates are right, and the answer is wrong.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { acceptPlatforms, loadPlatforms, getPlatform } from '../src/registry';
import { ceilingDoubt } from '../src/diagnostics';
import { loadTemplates } from '../src/registry';

test('a platform with a config file is a platform you can buy', () => {
  const { platforms, refused } = acceptPlatforms(['google', 'meta']);
  assert.deepEqual(platforms, ['google', 'meta']);
  assert.deepEqual(refused, [], 'nothing refused');
});

test('meta is in the registry with the four shapes Meta actually runs', () => {
  const meta = getPlatform('meta');
  const sizes = Object.keys(meta.sizes);
  for (const s of ['1080x1080', '1200x628', '1080x1350', '1080x1920']) {
    assert.ok(sizes.includes(s), `${s} is offered`);
  }
});

test('every template family can draw every meta size', () => {
  // The reason Meta could be switched on rather than built: the layouts were
  // already there. If this fails, a size was added to the platform and no
  // template carries it -- which renders as a 422 in the preview pane.
  const metaSizes = Object.keys(getPlatform('meta').sizes);
  for (const [id, spec] of loadTemplates()) {
    for (const s of metaSizes) {
      assert.ok((spec.sizes as any)[s], `${id} draws ${s}`);
    }
  }
});

test('meta carries its own file-weight ceiling, not the display one', () => {
  // 150 KB is the Google Display limit. Applied to a 1080x1920 it makes the
  // quality ladder step a full-height story frame down until it is mushy, to
  // satisfy a rule Meta does not impose.
  const meta = getPlatform('meta');
  for (const [size, spec] of Object.entries(meta.sizes)) {
    assert.ok((spec as any).maxFileBytes > 1_000_000,
              `${size} is not held to a display-banner ceiling`);
  }
  assert.equal((getPlatform('google').sizes['300x250'] as any).maxFileBytes, 153600,
               'and google keeps its own');
});

test('an unknown platform is refused by name, never dropped in silence', () => {
  const { platforms, refused } = acceptPlatforms(['google', 'tiktok']);
  assert.deepEqual(platforms, ['google']);
  assert.deepEqual(refused, ['tiktok'],
                   'the caller can say which name it did not recognize');
});

test('"not sure - recommend" is a form answer, not a platform', () => {
  // It is dropped rather than refused: reporting it as an unknown platform
  // puts a warning in front of somebody who filled the form in correctly.
  const { platforms, refused } = acceptPlatforms(['google', 'unsure']);
  assert.deepEqual(platforms, ['google']);
  assert.deepEqual(refused, []);
});

test('nothing chosen falls back rather than building nothing', () => {
  assert.deepEqual(acceptPlatforms(undefined).platforms, ['google']);
  assert.deepEqual(acceptPlatforms([]).platforms, ['google']);
  assert.deepEqual(acceptPlatforms(['nope']).platforms, ['google'],
                   'and a wholly unknown list still builds something');
});

test('duplicates and casing do not multiply the render', () => {
  assert.deepEqual(acceptPlatforms(['Meta', 'meta', ' META ']).platforms, ['meta']);
});

test('every platform config on disk is loadable', () => {
  const all = loadPlatforms();
  assert.ok(all.size >= 3);
  for (const [id, cfg] of all) {
    assert.equal(cfg.platform, id, 'the file names itself');
    assert.ok(Object.keys(cfg.sizes).length > 0, `${id} buys at least one size`);
  }
});

/*
 * Where a ceiling came from.
 *
 * `source: 'doc'` is the rule's own claim, and the diagnostics panel read
 * only that: it flagged a size where somebody had typed `source: 'verify'`,
 * which nothing ever had. So it reported "all limits sourced from
 * documentation" over 23 rules of which 13 recorded no source at all, and
 * would have gone on doing so however many more were added. ceilingDoubt()
 * derives it from the record instead.
 */
test('a ceiling with nothing behind it is not a sourced ceiling', () => {
  assert.equal(ceilingDoubt({ source: 'doc', _verifiedAgainst: 'the spec sheet' }), null);
  assert.equal(ceilingDoubt({ source: 'doc' }), 'no source recorded');
  assert.equal(ceilingDoubt({ source: 'doc', _verifiedAgainst: '   ' }), 'no source recorded',
    'whitespace is not a source');
  assert.equal(ceilingDoubt({}), 'no source recorded', 'nor is declaring nothing at all');
});

test('and one somebody looked at and could not confirm says which it is', () => {
  // Two different reasons on purpose: "nobody recorded where this came from"
  // and "somebody looked and it did not check out" send you to different
  // places, and a single word for both loses the difference.
  assert.equal(ceilingDoubt({ source: 'verify', _verifiedAgainst: 'not in the published list' }),
    'marked for confirmation');
});

test('every rule shipped today records its source, bar the one that says it does not', () => {
  const open: string[] = [];
  for (const [id, cfg] of loadPlatforms()) {
    for (const [size, rule] of Object.entries(cfg.sizes)) {
      const why = ceilingDoubt(rule as never);
      if (why) open.push(`${id}/${size} ${why}`);
    }
  }
  // Amazon's 250x250 is genuinely open -- it is not in Amazon's published
  // desktop static list and its 50 KB was never checked -- so it is named
  // rather than dropped or moved on a guess. Anything ELSE appearing here is
  // a rule that arrived without provenance.
  assert.deepEqual(open, ['amazon/250x250 marked for confirmation']);
});
