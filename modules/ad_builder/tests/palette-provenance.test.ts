/**
 * Where the palette came from, and the ad that looked branded and was not.
 *
 * `assetSources` has recorded provenance for the logo and the hero since it
 * was written. The palette had no equivalent: `finalizeColors()` spread
 * DEFAULTS underneath whatever was discovered and said nothing at all. So a
 * client with no brand colors on file got Smart 1's placeholder navy and
 * gold on every size, in an ad that looks plausibly branded, with nothing on
 * any screen saying so — absent data reading as a confident value, on the
 * thing the client receives.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { buildCampaign, colorsFromImage } from '../src/intake';
import sharp from 'sharp';
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
  // color they never chose.
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
  // paletteProvenance() calls the offer below it, so the page's own copy of
  // that is lifted too rather than stubbed — a stub would let the two drift
  // and still pass.
  const offerFrom = src.indexOf('function logoPaletteOffer()');
  assert.notEqual(offerFrom, -1, 'the offer is still drawn from the same place');
  const offerBody = src.slice(offerFrom);
  const offerFn = offerBody.slice(0, offerBody.indexOf('\n  }\n') + 5);
  const offer = new Function('state', 'esc', `return (${offerFn})`)(state, esc);
  const drawn = new Function('state', 'esc', 'logoPaletteOffer', `return (${fn})`)(state, esc, offer);
  return (sources: unknown) => {
    state.doc = sources === undefined ? null : { colorSources: sources };
    return (drawn() as string).replace(/<[^>]+>/g, '').replace(/&mdash;/g, '—').trim();
  };
}

test('the line says nothing about a campaign built before the field existed', () => {
  const draw = paletteProvenanceFromPage();
  // The campaign JSONs already on disk carry no colorSources. Reading their
  // absence as "default" would put a warning about a stock palette on an ad
  // whose colors may have been perfectly correct — absent is not the same
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

/* ------------------------------------------- the colors the logo can offer */

const MARK = '<rect x="10" y="10" width="120" height="60" fill="#C8102E"/>'
           + '<rect x="140" y="25" width="50" height="30" fill="#00843D"/>';

async function logoFile(plate: string | null): Promise<string> {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'logo-colors-'));
  const f = path.join(d, 'logo.png');
  let p = sharp(Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80">${MARK}</svg>`));
  if (plate) p = p.flatten({ background: plate });
  await p.png().toFile(f);
  return f;
}

test('colorsFromImage reads the mark, not what is behind it', async () => {
  // It returned sharp's `dominant`, which is a histogram over RGB and takes
  // no notice of alpha — so a fully transparent pixel votes with whatever RGB
  // it carries, and a logo is mostly background by area. Measured on this
  // exact mark it gave #080808 on a transparent canvas and #F8F8F8 on a white
  // plate: the padding and the plate, never the logo. It had no caller, so
  // nothing ever found out.
  for (const plate of [null, '#ffffff', '#0b2545']) {
    const read = await colorsFromImage(await logoFile(plate));
    assert.equal(read[0], '#C8102E', `largest area first (plate ${plate})`);
    assert.ok(read.includes('#00843D'), 'and the second color is there too');
    assert.ok(!read.some((h) => h === '#FFFFFF' || h === '#0B2545'),
      'the plate is never offered as a brand color');
  }
});

test('the logo is offered only where nobody has answered for the palette', async () => {
  const logo = await logoFile(null);
  const withLogo = await build({ uploads: { logo: [{ url: logo }] } } as never);
  assert.equal(withLogo.assetSources.logo, 'upload');
  assert.deepEqual(withLogo.logoPalette, ['#C8102E', '#00843D']);

  const answered = await build({
    uploads: { logo: [{ url: logo }] }, colorOverrides: { primary: '#123456' },
  } as never);
  assert.equal(answered.logoPalette, undefined,
    'a palette somebody set needs no offer');
});

test('and never from a wordmark, which we drew in the placeholder ink', async () => {
  // makeWordmark() sets the business name in brand.colors.dark, which in this
  // exact case IS the placeholder #111111. Reading it back would offer the
  // tool its own default as the client's brand color — discovering what it
  // just invented.
  const r = await build();
  assert.equal(r.assetSources.logo, 'wordmark');
  assert.equal(r.logoPalette, undefined);
});

test('the screen offers them to copy and never applies them', () => {
  const src = fs.readFileSync(path.join(ROOT, 'public/build.html'), 'utf8');
  const from = src.indexOf('function logoPaletteOffer()');
  assert.notEqual(from, -1);
  const body = src.slice(from);
  const fn = body.slice(0, body.indexOf('\n  }\n') + 5);
  const state: { doc: unknown } = { doc: null };
  const drawn = new Function('state', 'esc', `return (${fn})`)(state, (v: unknown) => String(v));

  state.doc = { logoPalette: undefined };
  assert.equal(drawn(), '', 'nothing to offer says nothing');
  state.doc = { logoPalette: [] };
  assert.equal(drawn(), '', 'and an empty reading is not an offer either');

  state.doc = { logoPalette: ['#C8102E', '#00843D'] };
  const html = drawn() as string;
  assert.match(html, /data-hex="#C8102E"/);
  assert.match(html, /click to copy/, 'the control says what it does');
  assert.doesNotMatch(html, /data-color=/,
    'it must not write into a swatch: which of the five roles a color should '
    + 'become is a judgment, and guessing it moves four other things');
});
