/**
 * Where the palette came from, and the ad that looked branded and was not.
 *
 * `assetSources` has recorded provenance for the logo and the hero since it
 * was written. The palette had no equivalent: `finalizeColors()` spread
 * DEFAULTS underneath whatever was discovered and said nothing at all. So a
 * client with no brand colours on file got Smart 1's placeholder navy and
 * gold on every size, in an ad that looks plausibly branded, with nothing on
 * any screen saying so — absent data reading as a confident value, on the
 * thing the client receives.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { buildCampaign } from '../src/intake';
import type { Submission } from '../src/intake';

const ROOT = path.resolve(__dirname, '..');
const ROLES = ['primary', 'secondary', 'accent', 'light', 'dark'] as const;

async function build(extra: Partial<Submission> = {}, id = 'p' + Math.random().toString(36).slice(2)) {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'palette-'));
  return buildCampaign(
    {
      requestId: id, business: 'Acme Roofing', campaignName: 'Spring',
      landingPage: 'https://example.com', headline: 'New roof in a week',
      cta: 'Get a quote', platforms: ['google'], ...extra,
    } as Submission,
    { assetRoot: ROOT, cacheDir: path.join(d, 'cache'), outputDir: d },
  );
}

test('a campaign nobody supplied colors for says so, plainly and once', async () => {
  const r = await build();
  assert.deepEqual(
    ROLES.map((k) => r.colorSources[k]),
    ROLES.map(() => 'default'),
    'every role is the placeholder',
  );
  const said = r.notes.filter((n) => /placeholder palette/.test(n));
  assert.equal(said.length, 1, 'said once, not per role');
  assert.match(said[0], /before sending a proof/, 'and names what to do about it');
});

test('a supplied color is recorded as supplied, and retires the warning', async () => {
  const r = await build({ colorOverrides: { primary: '#aa0000' } });
  assert.equal(r.colorSources.primary, 'override');
  assert.equal(r.colorSources.secondary, 'default', 'the roles nobody answered still say so');
  assert.equal(r.notes.filter((n) => /placeholder palette/.test(n)).length, 0,
    'the all-default warning is about the whole palette, not one role');
});

test('a color we moved to keep text readable is ours, not theirs', async () => {
  // finalizeColors nudges the accent when it is unreadable on the primary,
  // and substitutes light/dark when they do not contrast. Those are our
  // edits: reporting them as discovered would credit the client with a
  // colour they never chose.
  const r = await build({ colorOverrides: { primary: '#aa0000' } });
  assert.equal(r.colorSources.accent, 'adjusted');
  assert.ok(r.notes.some((n) => /adjusted to/.test(n)), 'and the note says what it was');
});

/* ------------------------------------------------------------ the screen */

// The page's own source, run here rather than restated — the shape
// test_proposal_targeting.py uses for the proposal wizard's step logic. A
// second copy of this decision is one that drifts from the line the operator
// actually reads.
function paletteProvenanceFromPage() {
  const src = fs.readFileSync(path.join(ROOT, 'public/build.html'), 'utf8');
  const from = src.indexOf('function paletteProvenance()');
  assert.notEqual(from, -1, 'the build screen still draws the line');
  const body = src.slice(from);
  const fn = body.slice(0, body.indexOf('\n  }\n') + 5);
  const state: { doc: unknown } = { doc: null };
  const esc = (v: unknown) => String(v);
  const drawn = new Function('state', 'esc', `return (${fn})`)(state, esc);
  return (sources: unknown) => {
    state.doc = sources === undefined ? null : { colorSources: sources };
    return (drawn() as string).replace(/<[^>]+>/g, '').replace(/&mdash;/g, '—').trim();
  };
}

test('the line says nothing about a campaign built before the field existed', () => {
  const draw = paletteProvenanceFromPage();
  // The campaign JSONs already on disk carry no colorSources. Reading their
  // absence as "default" would put a warning about a stock palette on an ad
  // whose colours may have been perfectly correct — absent is not the same
  // answer as placeholder, which is the whole point of the line.
  assert.equal(draw(undefined), '', 'no doc');
  assert.equal(draw(null), '', 'a doc with no colorSources');
  assert.equal(draw({}), '', 'or one carrying an empty map');
});

test('and it only speaks when there is something to say', () => {
  const draw = paletteProvenanceFromPage();
  const all = (v: string) => Object.fromEntries(ROLES.map((r) => [r, v]));

  assert.match(draw(all('default')), /placeholder palette/, 'the case that ships a stock ad');
  assert.equal(draw(all('discovered')), '',
    'five rows of "from Brandfetch" is noise, and a line on every campaign is one people stop reading');

  const mixed = draw({ ...all('discovered'), secondary: 'default', accent: 'adjusted' });
  assert.match(mixed, /secondary still placeholder/);
  assert.match(mixed, /accent adjusted for readability/);
  assert.doesNotMatch(mixed, /primary/, 'a role somebody answered for is not listed');
});
