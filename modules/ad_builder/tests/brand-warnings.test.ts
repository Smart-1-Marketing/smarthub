/**
 * The caveats the customer read, and the project that said nothing about them.
 *
 * Brand discovery runs in two places. `buildCampaign` runs it server-side
 * when a submission carries no brand, and pushes every warning it came back
 * with onto the campaign notes. The customer-facing intake form runs it in
 * the browser instead -- and then submits the brand, which makes that branch
 * unreachable. So `discoverBrand`'s own findings ("No logo was found. The
 * customer will need to upload one.", "Only raster logos without
 * transparency were found.", a low-confidence flag) were shown to the
 * customer on the form and reached the rep nowhere at all. What the rep
 * opened instead was one line reading "Brand details on file for acme.com
 * (source: brandfetch)" -- a confident clean answer standing over three
 * caveats, on the path a stranger uses.
 *
 * The form posted a `brandWarnings` array of its own, which no TypeScript
 * file declared or read. Forwarding it was the shorter fix and the wrong
 * one: it is a value the page can put anything in, printed against
 * somebody's brand. The warnings are recovered from our own cached
 * discovery record -- a disk read, nothing re-fetched and nothing billed.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { buildCampaign, LOW_CONFIDENCE_NOTE } from '../src/intake';
import type { Submission, BuildOptions } from '../src/intake';

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'src');

const WARNINGS = [
  'No logo was found. The customer will need to upload one.',
  'Only raster logos without transparency were found. Ask the customer for an SVG or transparent PNG.',
];

/** The brand the intake form submits when discovery answered in the browser. */
const DISCOVERED = {
  name: 'Acme Roofing',
  domain: 'acmeroofing.com',
  colors: { primary: '#2E5A88', secondary: '#1B3A5C', accent: '#E8A317', light: '#FFFFFF', dark: '#111111' },
  fonts: { headline: 'Montserrat', body: 'Open Sans' },
  logos: { primary: '' },
};

async function build(extra: Partial<Submission>, opts: Partial<BuildOptions> = {}) {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'brandwarn-'));
  return buildCampaign(
    {
      requestId: 'w' + Math.random().toString(36).slice(2),
      business: 'Acme Roofing', campaignName: 'Spring',
      website: 'acmeroofing.com', landingPage: 'https://acmeroofing.com',
      headline: 'New roof in a week', cta: 'Get a quote', platforms: ['google'],
      ...extra,
    } as Submission,
    {
      assetRoot: ROOT, cacheDir: path.join(d, 'cache'), outputDir: d,
      aiCopy: false, discover: false, ...opts,
    },
  );
}

/** Our stored discovery record for the domain, as the cache holds it. */
const record = () => ({ warnings: [...WARNINGS], needsReview: true });

test('the warnings the customer was shown reach the project the rep opens', async () => {
  const r = await build(
    { brand: DISCOVERED, brandSource: 'brandfetch' },
    { brandRecord: () => record() },
  );
  for (const w of WARNINGS) {
    assert.ok(r.notes.includes(w), `missing from the notes: ${w}`);
  }
  assert.ok(r.notes.includes(LOW_CONFIDENCE_NOTE), 'and the low-confidence flag travels with them');
});

test('a brand the customer rejected carries none of its warnings', async () => {
  // Pressing "This isn't my brand" clears the discovery in the browser and
  // the manual fields are submitted instead -- with no brandSource on them.
  // The cached record still exists for that domain, so a gate on the domain
  // alone would print caveats about a brand they explicitly turned down.
  const r = await build(
    { brand: { ...DISCOVERED, name: 'Acme Roofing LLC' } },
    { brandRecord: () => record() },
  );
  for (const w of WARNINGS) {
    assert.ok(!r.notes.includes(w), `attached to a rejected brand: ${w}`);
  }
  assert.ok(!r.notes.includes(LOW_CONFIDENCE_NOTE));
});

test('nothing stored is silence, not an invented all-clear', async () => {
  const r = await build(
    { brand: DISCOVERED, brandSource: 'brandfetch' },
    { brandRecord: () => null },
  );
  assert.ok(!r.notes.includes(LOW_CONFIDENCE_NOTE));
  assert.ok(!r.notes.some((n) => /discovery/i.test(n)),
    'no claim either way about a lookup we have no record of');
});

test('a record that is there and says nothing adds nothing', async () => {
  const r = await build(
    { brand: DISCOVERED, brandSource: 'brandfetch' },
    { brandRecord: () => ({ warnings: [], needsReview: false }) },
  );
  assert.ok(!r.notes.includes(LOW_CONFIDENCE_NOTE));
});

test('an empty or non-string warning is dropped rather than drawn as a blank bullet', async () => {
  const r = await build(
    { brand: DISCOVERED, brandSource: 'brandfetch' },
    { brandRecord: () => ({ warnings: ['', '   ', null as any, 42 as any, WARNINGS[0]] }) },
  );
  assert.deepEqual(
    r.notes.filter((n) => !n.trim()), [],
    'nothing blank reached the notes',
  );
  assert.ok(r.notes.includes(WARNINGS[0]), 'and the real one still did');
});

test('both halves say the low-confidence sentence the same way', () => {
  // Discovery happens here and in the browser, and a rep reading a project
  // cannot tell which ran. Two spellings would read as two different
  // findings. The constant is the one reading; nothing may restate it.
  const sources = fs.readdirSync(SRC).filter((f) => f.endsWith('.ts'))
    .map((f) => fs.readFileSync(path.join(SRC, f), 'utf8'));
  const literal = sources.filter((s) => s.includes('Brand discovery confidence was low'));
  assert.equal(literal.length, 1, 'the sentence is written out in exactly one file');
  assert.ok(literal[0].includes('export const LOW_CONFIDENCE_NOTE'),
    'and that file is the one declaring the constant');
});

test('the intake form no longer posts a warnings array nothing reads', () => {
  const embed = fs.readFileSync(path.join(ROOT, 'public', 'embed.html'), 'utf8');
  assert.ok(!/data\.brandWarnings\s*=/.test(embed),
    'the browser copy is not the authority for a caveat printed against a brand');
  assert.ok(/data\.brandSource\s*=/.test(embed),
    'but brandSource still goes: it is what tells the server the lookup was kept');
});
