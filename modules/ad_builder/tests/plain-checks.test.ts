/**
 * The checks, for a person.
 *
 * qa.ts measures and its sentences are exact: "below 4.5:1 — headline
 * 2.1:1", "headline is only 1.12x the supporting text; aim for 1.4x". Nobody
 * laying out an ad knows what those numbers are, so every finding gets a
 * second reading beside the measured one -- what is wrong and what to do --
 * and, where the fix is one setting, a change the screen can make and undo.
 *
 * Two layers: a deterministic reading that is always there, and a model's
 * reading of the same list for this ad. The model may reword a finding and
 * may never add one, and a suggestion it makes outside the screen's own
 * vocabulary is dropped rather than passed on as a button that does nothing.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  acceptSuggestion, adviseFindings, explainFinding, withPlain,
} from '../src/plain-checks';
import type { QaFinding } from '../src/types';

const f = (check: string, detail: string, status: QaFinding['status'] = 'warn', fix?: QaFinding['fix']): QaFinding =>
  ({ check, status, detail, fix });

test('every measured sentence gets a reading with no ratio in it', () => {
  const measured: QaFinding[] = [
    f('contrast', 'below 4.5:1 — headline 2.1:1, cta 3.0:1'),
    f('hierarchy', 'headline is only 1.12x the supporting text; aim for 1.4x or more so the eye has one clear entry point'),
    f('legibility', '"support" renders at 9.5px at delivery scale, below the 11px floor'),
    f('overflow:headline', '"A very long headline" does not fit (3 lines at 18px, limit 2)', 'fail', { action: 'shorten', role: 'headline', maxWords: 4 }),
    f('overflow:cta', 'CTA "Book your free consultation today" is too long for the button', 'fail'),
    f('collision', 'headline, cta — these are printed on top of each other', 'fail'),
    f('safe-area', 'outside the safe area: logo, trust'),
    f('safe-zone', 'headline, cta extend into the platform UI exclusion zone.'),
    f('logo-contrast', '62% of the logo is invisible against its background (1.3:1). Try the white logo on this size.'),
    f('logo-plate', 'the logo has an opaque rgb(255, 255, 255) background that will show as a box against this panel.'),
    f('logo', 'logo occupies 31% of the canvas (target under 25%)'),
    f('logo', 'no logo rendered — the advertiser must be identifiable', 'fail'),
    f('cta', 'no CTA in the creative'),
    f('focal-point', '3 supporting elements (support, offer, trust) compete below the headline; this canvas comfortably carries 2'),
    f('source-resolution', 'the hero image is 1024x683 and is painted at 1940x1294 — 1.9x its own pixels, which will show as softness. Use a larger source.'),
    f('text-coverage', 'text covers roughly 24.0% of the canvas, over the 20% Meta recommends.'),
    f('pressure-language', 'headline carries banned pressure language ("Hurry" in "Hurry, ends soon").', 'fail'),
    f('carry', 'carried from 300x250, but headline would not fit this canvas'),
    f('placeholder-artwork', 'Replace the placeholder image before approving or delivering this size.', 'fail'),
    f('animation:weight', 'the encoded file is 182 KB, over the 150 KB Google allows', 'fail'),
    f('animation:slides', 'slide 2 says exactly what slide 1 says, so nothing changes on screen'),
  ];
  for (const m of measured) {
    const p = explainFinding(m, { fontSizes: { headline: 24, support: 20 } });
    assert.ok(p.title.length > 8, `${m.check} has a title`);
    assert.ok(p.how.length > 8, `${m.check} says what to do`);
    assert.doesNotMatch(p.title + ' ' + p.how, /\d+(\.\d+)?:1|\dx\b|WCAG|luminance/i,
      `${m.check}: "${p.title} ${p.how}" still has the maths in it`);
    assert.equal(p.source, 'rule');
  }
});

test('the reading names the control that fixes it, and the one-press change where there is one', () => {
  const hier = explainFinding(f('hierarchy', 'headline is only 1.12x the supporting text'), { fontSizes: { support: 20 } });
  assert.deepEqual(hier.apply, { kind: 'style', block: 'headline', prop: 'size', value: 30 });
  assert.match(hier.applyLabel!, /30px/);
  assert.match(hier.how, /Text Boxes/);

  const logo = explainFinding(f('logo-contrast', '62% of the logo is invisible (1.3:1). Try the white logo on this size.'));
  assert.deepEqual(logo.apply, { kind: 'logo-tone', tone: 'white' });
  const dark = explainFinding(f('logo-contrast', '62% of the logo is invisible (1.3:1). Try the full-color (darker) logo on this size.'));
  assert.deepEqual(dark.apply, { kind: 'logo-tone', tone: 'black' });

  const over = explainFinding(f('overflow:headline', 'does not fit', 'fail', { action: 'shorten', role: 'headline', maxWords: 4 }));
  assert.match(over.how, /4 words/);

  // Over a photo the ink to try is the light one; on a flat layout the dark one.
  const onPhoto = explainFinding(f('contrast', 'below 4.5:1 — support 2.0:1'), { backgroundImage: true });
  assert.deepEqual(onPhoto.apply, { kind: 'style', block: 'support', prop: 'color', value: 'light' });
  const onFlat = explainFinding(f('contrast', 'below 4.5:1 — support 2.0:1'), {});
  assert.equal((onFlat.apply as any).value, 'dark');
  // The button's contrast is its fill against its label; no ink suggestion.
  assert.equal(explainFinding(f('contrast', 'below 4.5:1 — cta 2.0:1')).apply, undefined);
});

test('an animated finding says which slide, and an unknown check still gets a sentence', () => {
  const slide = explainFinding(f('contrast · slide-2', 'Slide 2: below 4.5:1 — headline 2.1:1'));
  assert.match(slide.title, /^On slide 2: /);
  const odd = explainFinding(f('something-new', 'a measured sentence nobody has translated'));
  assert.equal(odd.what, 'a measured sentence nobody has translated');
  assert.match(odd.title, /Something new/);
});

test('withPlain leaves passes alone and reads the rest', () => {
  const out = withPlain([f('contrast', 'all text at or above 4.5:1', 'pass'), f('cta', 'no CTA in the creative')]);
  assert.equal(out[0].plain, undefined);
  assert.equal(out[1].plain?.title, 'There is no call to action.');
});

test('only a suggestion the screen can perform gets through', () => {
  assert.deepEqual(acceptSuggestion({ kind: 'style', block: 'headline', prop: 'size', value: '48' }),
    { kind: 'style', block: 'headline', prop: 'size', value: 48 });
  assert.equal(acceptSuggestion({ kind: 'style', block: 'headline', prop: 'size', value: 900 }), undefined, 'over the 200px cap');
  assert.equal(acceptSuggestion({ kind: 'style', block: 'headline', prop: 'rotate', value: 3 }), undefined, 'not a prop the panel has');
  assert.equal(acceptSuggestion({ kind: 'style', block: 'hero', prop: 'size', value: 3 }), undefined, 'not a block a person styles');
  assert.deepEqual(acceptSuggestion({ kind: 'style', block: 'support', prop: 'color', value: '#abcdef' }),
    { kind: 'style', block: 'support', prop: 'color', value: '#abcdef' });
  assert.equal(acceptSuggestion({ kind: 'style', block: 'support', prop: 'color', value: 'reddish' }), undefined);
  assert.deepEqual(acceptSuggestion({ kind: 'copy', field: 'cta', value: '  Call now ' }), { kind: 'copy', field: 'cta', value: 'Call now' });
  assert.equal(acceptSuggestion({ kind: 'copy', field: 'headline', value: '' }), undefined);
  assert.deepEqual(acceptSuggestion({ kind: 'logo-tone', tone: 'white' }), { kind: 'logo-tone', tone: 'white' });
  assert.equal(acceptSuggestion({ kind: 'logo-tone', tone: 'purple' }), undefined);
  assert.deepEqual(acceptSuggestion({ kind: 'layout', family: 'T04' }, ['T01', 'T04']), { kind: 'layout', family: 'T04' });
  assert.equal(acceptSuggestion({ kind: 'layout', family: 'T99' }, ['T01', 'T04']), undefined);
  assert.equal(acceptSuggestion('nonsense'), undefined);
});

test('with no key the advice is the built-in reading, and says so', async () => {
  const a = await adviseFindings({
    findings: [f('hierarchy', 'headline is only 1.1x'), f('contrast', 'pass', 'pass')],
    fontSizes: { support: 16 },
  }, { apiKey: '' });
  assert.equal(a.source, 'rule');
  assert.equal(a.items.length, 1, 'passes are not items');
  assert.match(a.warnings.join(' '), /No OpenAI key/);
  assert.equal(a.lead?.applyLabel, 'Make the headline 24px');
});

test('the model may reword a finding and may not add one, and its change is checked', async () => {
  const fetchImpl = (async (_url: string, init: any) => {
    const sent = JSON.parse(init.body);
    assert.equal(sent.model, 'gpt-4o-mini');
    const content = JSON.stringify({
      summary: 'Two things to fix before this ships.',
      items: [
        { check: 'hierarchy', title: 'The headline is not standing out.', what: 'Both lines are about the same size.',
          how: 'Make the headline bigger in Text Boxes.', apply: { kind: 'style', block: 'headline', prop: 'size', value: 48 }, applyLabel: 'Make the headline 48px' },
        { check: 'invented', title: 'Made up', what: 'x', how: 'y', apply: null },
        { check: 'contrast', title: 'Hard to read.', what: 'w', how: 'h', apply: { kind: 'style', block: 'headline', prop: 'glow', value: 1 } },
      ],
    });
    return { ok: true, status: 200, json: async () => ({ choices: [{ message: { content } }] }) } as any;
  }) as unknown as typeof fetch;

  const a = await adviseFindings({
    findings: [f('hierarchy', 'headline is only 1.1x'), f('contrast', 'below 4.5:1 — headline 2.0:1')],
    fontSizes: { support: 16 },
  }, { apiKey: 'sk-test', fetchImpl });
  assert.equal(a.source, 'ai');
  assert.equal(a.summary, 'Two things to fix before this ships.');
  assert.deepEqual(a.items.map((i) => i.check), ['hierarchy', 'contrast'], 'same items, same order, nothing invented');
  assert.equal(a.items[0].plain.title, 'The headline is not standing out.');
  assert.deepEqual(a.items[0].plain.apply, { kind: 'style', block: 'headline', prop: 'size', value: 48 });
  assert.equal(a.items[0].plain.source, 'ai');
  // An unusable change falls back to the rule's own, never to a dead button.
  assert.deepEqual(a.items[1].plain.apply, { kind: 'style', block: 'headline', prop: 'color', value: 'dark' });
  assert.equal(a.lead?.applyLabel, 'Make the headline 48px');
  // The measured sentence is still there for the proof and the manifest.
  assert.equal(a.items[1].detail, 'below 4.5:1 — headline 2.0:1');
});

test('a model that does not answer costs nothing but the better sentence', async () => {
  const fetchImpl = (async () => { throw new Error('boom'); }) as unknown as typeof fetch;
  const a = await adviseFindings({ findings: [f('cta', 'no CTA in the creative')] }, { apiKey: 'sk-test', fetchImpl });
  assert.equal(a.source, 'rule');
  assert.match(a.warnings.join(' '), /did not answer/);
  assert.equal(a.items[0].plain.title, 'There is no call to action.');
});
