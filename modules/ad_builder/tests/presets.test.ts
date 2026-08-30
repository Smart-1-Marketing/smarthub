/**
 * A client's settled setup, and the CSV that fills it in many times.
 *
 * The second ad for a client is the same ad with a different offer on it, and
 * everything that took the time is already decided. These assert the two ways
 * that goes wrong quietly: a slot that renders nowhere, and a batch that comes
 * back short without saying so.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import {
  PresetStore, presetFromConcept, conceptFromPreset, type Preset,
} from '../src/presets';
import { readBatch, campaignFromBatch, parseCsv, conceptLetter, BATCH_MAX_ROWS } from '../src/batch';
import { renderPreview } from '../src/render';
import type { Campaign } from '../src/types';

const ROOT = path.resolve(__dirname, '..');
const campaign: Campaign = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'src/examples/icon-solar.json'), 'utf8'),
);

/**
 * Only the offer-led family draws an offer box, and only on three of its
 * sizes -- so a preset with an offer slot has to be cut from that one. Getting
 * this wrong is exactly what the refusal below exists to catch, and it caught
 * it in the first draft of this file.
 */
const OFFER_LED = campaign.concepts.find((c) => c.layoutFamily === 'T04')!;
const SPLIT_IMAGE = campaign.concepts.find((c) => c.layoutFamily === 'T01')!;

function cut(
  fields?: { role: string; label?: string; fallback?: string }[],
  concept = OFFER_LED,
) {
  return presetFromConcept({
    name: 'Summer promo', client: 'Icon Solar', domain: 'iconsolar.com',
    brand: campaign.brand, concept,
    fields: fields ?? [{ role: 'headline' }, { role: 'offer', label: 'Deal' }, { role: 'cta' }],
    platforms: ['google'], sourceRequestId: campaign.requestId,
  });
}

/* ------------------------------------------------------------------ presets */

test('a preset keeps the design and drops the campaign', () => {
  const { preset } = cut();
  assert.equal(preset.layoutFamily, OFFER_LED.layoutFamily);
  assert.deepEqual(preset.brand, campaign.brand);
  // A preset carrying the campaign would file every ad made from it against
  // one record, and the second ad would read as a revision of the first.
  const raw = JSON.stringify(preset);
  assert.ok(!raw.includes('"campaignName"'), 'no campaign name');
  assert.ok(!raw.includes('"approvals"'), 'no approvals');
  assert.ok(!raw.includes('"requestId"'), 'no requestId of its own');
  assert.equal(preset.sourceRequestId, campaign.requestId, 'but it says where it came from');
});

test('the client is stored as a name and a domain, never a derived key', () => {
  const { preset } = cut();
  assert.equal(preset.client, 'Icon Solar');
  assert.equal(preset.domain, 'iconsolar.com');
  assert.ok(!('clientKey' in preset), 'no stored key to outlive a rename');
});

test('a slot the family draws nowhere is refused by name', () => {
  // The split-image family draws no offer box on any of its fourteen sizes --
  // an offer flash is the offer-led family's idea. Offering the slot anyway is
  // a form field that is typed into, saved, and rendered nowhere, which is the
  // proof-point failure this module already carries once.
  const { preset, refused } = cut(
    [{ role: 'headline' }, { role: 'offer', label: 'Deal' }],
    SPLIT_IMAGE,
  );
  assert.deepEqual(preset.fields.map((f) => f.role), ['headline']);
  assert.equal(refused.length, 1);
  assert.equal(refused[0].role, 'offer');
  assert.match(refused[0].reason, /T01 draws no offer/);
});

test('the same slot is accepted on the family that does draw it', () => {
  const { preset, refused } = cut(
    [{ role: 'headline' }, { role: 'offer', label: 'Deal' }],
    OFFER_LED,
  );
  assert.deepEqual(preset.fields.map((f) => f.role), ['headline', 'offer']);
  assert.deepEqual(refused, []);
});

test('a role this renderer has never drawn is refused too', () => {
  const { preset, refused } = cut([{ role: 'headline' }, { role: 'nonsense' }]);
  assert.ok(!preset.fields.some((f) => (f.role as string) === 'nonsense'));
  assert.equal(refused.length, 1);
  assert.match(refused[0].reason, /not a copy role/);
});

test('a duplicate slot is refused rather than listed twice', () => {
  const { preset, refused } = cut([{ role: 'headline' }, { role: 'headline', label: 'Again' }]);
  assert.equal(preset.fields.filter((f) => f.role === 'headline').length, 1);
  assert.match(refused[0].reason, /twice/);
});

test('filling a preset writes only the named slots', () => {
  const { preset } = cut([{ role: 'headline' }]);
  const before = preset.copy.default!.support;
  const c = conceptFromPreset(preset, { headline: 'Beat the heat' }, { conceptId: 'A', name: 'x' });
  assert.equal(c.copy.default!.headline, 'Beat the heat');
  assert.equal(c.copy.default!.support, before, 'copy that is not a slot travels untouched');
});

test('a blank slot falls back rather than rendering an empty box', () => {
  const { preset } = cut([{ role: 'headline', fallback: 'Standing headline' }]);
  const c = conceptFromPreset(preset, { headline: '   ' }, { conceptId: 'A', name: 'x' });
  assert.equal(c.copy.default!.headline, 'Standing headline');
});

test("a per-size override never outranks the copy just typed", () => {
  // The 320x50 carries its own short headline. Left in place it would quietly
  // beat the one somebody just supplied, on the size nobody looks at first.
  const { preset } = cut([{ role: 'headline' }]);
  preset.copy['320x50'] = { headline: 'Last month', support: 'kept' };
  const c = conceptFromPreset(preset, { headline: 'This month' }, { conceptId: 'A', name: 'x' });
  assert.equal((c.copy['320x50'] as Record<string, string>)?.headline, undefined,
    'the stale short headline is dropped');
  assert.equal((c.copy['320x50'] as Record<string, string>)?.support, 'kept',
    'a role nobody refilled keeps its override');
});

test('presets are listed for a client exactly, never by substring', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preset-'));
  const store = new PresetStore(dir);
  const a = cut().preset;
  const b = cut().preset;
  b.id = b.id + '-2';
  b.client = 'Icon Solar Supply';
  b.domain = undefined;
  store.save(a);
  store.save(b);
  const mine = store.list('Icon Solar');
  assert.deepEqual(mine.map((p) => p.client), ['Icon Solar'],
    'a different company with a longer name is not collected');
  assert.equal(store.list().length, 2, 'listing them all still works');
});

test('a preset renders — the design survives the round trip', async () => {
  const { preset } = cut();
  const concept = conceptFromPreset(
    preset, { headline: 'Cut your power bill', offer: '$500 off', cta: 'Get a quote' },
    { conceptId: 'A', name: 'from preset' },
  );
  const out = await renderPreview({
    brand: preset.brand, concept, platform: 'google', size: '300x250', assetRoot: ROOT,
  });
  assert.ok(out.png.length > 500, 'it produced a real image');
  assert.deepEqual(out.qa.filter((f) => f.status === 'fail'), [], 'and no hard failure');
});

/* -------------------------------------------------------------------- batch */

test('the CSV reader survives what a spreadsheet actually exports', () => {
  // Quoted commas, doubled quotes, CRLF, a BOM, and no trailing newline --
  // splitting on commas cuts the offer in half and shifts every column after
  // it, which reads on the proof as the right copy in the wrong place.
  const csv = '﻿headline,offer\r\n"Save $500, this month","He said ""yes"""';
  const table = parseCsv(csv);
  assert.deepEqual(table[0], ['headline', 'offer'], 'the BOM is not part of the header');
  assert.deepEqual(table[1], ['Save $500, this month', 'He said "yes"']);
});

test('a header matching no slot fails the file, before anything renders', () => {
  const { preset } = cut();
  const r = readBatch(preset, 'city,notes\nColumbus,x');
  assert.ok(r.error, 'refused');
  assert.match(r.error!, /Deal/, 'it names what was expected');
  assert.match(r.error!, /city/, 'and what arrived');
  assert.equal(r.rows.length, 0);
});

test('a column that is not an ad field is ignored, not fatal', () => {
  // A spreadsheet routinely carries a city or a notes column. Refusing the
  // file over one sends somebody back to edit a CSV that was fine.
  const { preset } = cut();
  const r = readBatch(preset, 'headline,city\nBeat the heat,Columbus');
  assert.equal(r.error, undefined);
  assert.deepEqual(r.ignoredColumns, ['city']);
  assert.equal(r.rows.length, 1);
});

test('a slot is matched on its label or its role', () => {
  const { preset } = cut();
  const byLabel = readBatch(preset, 'headline,Deal\nA,B');
  const byRole = readBatch(preset, 'headline,offer\nA,B');
  assert.equal(byLabel.rows[0].values.offer, 'B', 'the label "Deal" fills offer');
  assert.equal(byRole.rows[0].values.offer, 'B', 'and so does the role');
});

test('a bad row is named and the rest still build', () => {
  const { preset } = cut();
  //                                        an unquoted comma in row 3
  const r = readBatch(preset, [
    'headline,offer',
    'Beat the heat,$500 off',
    'Stay cool, Columbus,$400 off',
    'Warm up,$300 off',
  ].join('\n'));
  assert.equal(r.rows.length, 2, 'the good rows survive');
  assert.equal(r.rejected.length, 1);
  assert.equal(r.rejected[0].line, 3, 'numbered as the spreadsheet numbers it');
  assert.match(r.rejected[0].reason, /unquoted comma/);
});

test('a batch over the cap is refused, never truncated', () => {
  const { preset } = cut();
  const rows = ['headline'];
  for (let i = 0; i < BATCH_MAX_ROWS + 1; i++) rows.push(`Row ${i}`);
  const r = readBatch(preset, rows.join('\n'));
  assert.ok(r.error, 'refused');
  assert.match(r.error!, new RegExp(String(BATCH_MAX_ROWS)));
  assert.equal(r.rows.length, 0, 'and nothing is built from a file that was over');
});

test('every row becomes its own concept with its own id', () => {
  const { preset } = cut();
  const r = readBatch(preset, [
    'headline,offer',
    'Columbus,$500 off',
    'Cincinnati,$500 off',
    'Dayton,$400 off',
  ].join('\n'));
  const c = campaignFromBatch({
    preset, rows: r.rows, requestId: 'AD-1', campaignName: 'Cities',
  });
  assert.equal(c.concepts.length, 3);
  assert.deepEqual(c.concepts.map((x) => x.conceptId), ['A', 'B', 'C']);
  assert.deepEqual(
    c.concepts.map((x) => x.copy.default!.headline),
    ['Columbus', 'Cincinnati', 'Dayton'],
  );
  assert.equal(c.concepts[2].copy.default!.offer, '$400 off', 'each row keeps its own offer');
});

test('concept ids stay unique past twenty-six rows', () => {
  // Two concepts sharing an id would have the second overwrite the first's
  // renders, and a batch of thirty is allowed.
  const ids = new Set<string>();
  for (let i = 0; i < 60; i++) ids.add(conceptLetter(i));
  assert.equal(ids.size, 60);
  assert.equal(conceptLetter(0), 'A');
  assert.equal(conceptLetter(25), 'Z');
  assert.equal(conceptLetter(26), 'AA');
});

test('a batch of rows renders, each carrying its own copy', async () => {
  const { preset } = cut();
  const r = readBatch(preset, [
    'headline,offer,cta',
    'Columbus solar,$500 off,Get a quote',
    'Dayton solar,$400 off,Book a visit',
  ].join('\n'));
  const c = campaignFromBatch({ preset, rows: r.rows, requestId: 'AD-2', campaignName: 'Cities' });
  for (const concept of c.concepts) {
    const out = await renderPreview({
      brand: c.brand, concept, platform: 'google', size: '300x250', assetRoot: ROOT,
    });
    assert.ok(out.png.length > 500, `${concept.conceptId} produced an image`);
    assert.deepEqual(out.qa.filter((f) => f.status === 'fail'), [], `${concept.conceptId} clean`);
  }
  // Two rows, two different ads -- not the same ad twice.
  assert.notEqual(
    c.concepts[0].copy.default!.headline,
    c.concepts[1].copy.default!.headline,
  );
});
