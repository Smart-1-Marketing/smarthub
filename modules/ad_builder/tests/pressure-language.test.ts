/**
 * "Removed pressure language" -- about a line that still carried it.
 *
 * `sanitise()` is the only gate on pressure language anywhere in this
 * renderer. QA measures dimensions, weight, contrast, safe areas and
 * collisions and reads no words at all, so nothing downstream of this
 * function ever looks at the copy again. The regex carried no `g` flag, so
 * `t.replace(BANNED, '')` took out the first phrase and left the rest --
 * and then pushed a warning saying the pressure language had been removed.
 * Measured on four ordinary headlines, three came back still carrying a
 * banned phrase and all four warned that they had been cleaned.
 *
 * That warning is not a debug line: `buildCampaign` does
 * `notes.push(...written.warnings)`, so it is what the rep reads on the
 * project. So the failure was silent from both ends -- the operator was told
 * it was handled, the file that says "Amazon rejects pressure language"
 * shipped the ad, and nothing anywhere disagreed.
 *
 * The second half is the seam. Removing the words alone left "Act now --
 * last chance on winter service" as "-- last chance on winter service", so
 * even the phrase it did remove left a headline opening on an em dash. The
 * cut takes the punctuation that joined the phrase to the rest with it.
 *
 * The two regexes are built from one list, for the reason this codebase
 * gives everywhere else: a phrase added to the detector and not to the
 * remover is exactly this bug back again, under a warning that reads clean.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { generateCopy, PRESSURE_PHRASES } from '../src/copywriter';
import type { Submission } from '../src/intake';

const SUB = {
  requestId: 'p1',
  business: 'Riverside HVAC',
  campaignName: 'Winter',
  website: 'riverside-hvac.example',
  landingPage: 'https://riverside-hvac.example',
  headline: 'Heat back on today',
  cta: 'Get a quote',
  platforms: ['google'],
} as unknown as Submission;

const BRIEF = { business: 'Riverside HVAC', promoting: 'furnace repair' };

/** A stubbed OpenAI that answers with exactly this 300x250 copy set. */
const model = (set: Record<string, string>) =>
  (async () => ({
    ok: true,
    json: async () => ({
      choices: [
        {
          message: {
            content: JSON.stringify({
              concepts: [
                { conceptId: 'A', name: 'Benefit', angle: 'benefit', sizes: { '300x250': set } },
              ],
            }),
          },
        },
      ],
    }),
  })) as unknown as typeof fetch;

async function write(set: Record<string, string>) {
  const out = await generateCopy(BRIEF, SUB, {
    apiKey: 'test-key',
    fetchImpl: model(set),
    sizes: ['300x250'],
  });
  assert.equal(out.source, 'openai', 'the stub should be taken as a real answer');
  return { copy: out.concepts[0].copy['300x250'] ?? {}, warnings: out.warnings };
}

/** Detection is asked of the shipped list, so the test cannot drift from it. */
const carries = (text: string) =>
  PRESSURE_PHRASES.some((p) => new RegExp(String.raw`\b${p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\b`, 'i').test(text));

test('every banned phrase is removed, not just the first', async () => {
  const { copy } = await write({ headline: 'Act now \u2014 last chance on winter service' });
  assert.ok(copy.headline, 'the headline should survive');
  assert.equal(
    carries(copy.headline as string),
    false,
    `headline still carries pressure language: ${JSON.stringify(copy.headline)}`,
  );
});

test('a repeated phrase is removed every time it appears', async () => {
  const { copy } = await write({ headline: 'Hurry, hurry, book today' });
  assert.equal(carries(copy.headline as string), false, JSON.stringify(copy.headline));
  assert.equal(copy.headline, 'book today');
});

test('the punctuation that joined the phrase goes with it', async () => {
  const { copy } = await write({ headline: 'Act now \u2014 last chance on winter service' });
  assert.doesNotMatch(
    copy.headline as string,
    /^[\s,;:!\u2013\u2014-]/,
    'the headline is left opening on the punctuation that joined the removed phrase',
  );
  assert.equal(copy.headline, 'on winter service');
});

test('the warning says what the line became, not just that it was cleaned', async () => {
  const { warnings } = await write({ headline: 'Act now for a free estimate' });
  const w = warnings.find((x) => /pressure language/i.test(x));
  assert.ok(w, 'a warning should be raised');
  assert.match(w as string, /became "for a free estimate"/, w as string);
});

test('a line that is nothing but pressure language is dropped and said to be dropped', async () => {
  const { copy, warnings } = await write({ headline: 'Hurry' });
  assert.equal(copy.headline, undefined, 'nothing is left, so no headline should be claimed');
  assert.ok(
    warnings.some((w) => /^Dropped /.test(w)),
    `no warning said the line was dropped: ${JSON.stringify(warnings)}`,
  );
});

test('every phrase on the list is both detected and removed', async () => {
  for (const phrase of PRESSURE_PHRASES) {
    const { copy, warnings } = await write({ headline: `Book service ${phrase} in Riverside` });
    const left = (copy.headline as string) ?? '';
    assert.equal(carries(left), false, `"${phrase}" survived: ${JSON.stringify(left)}`);
    assert.ok(
      warnings.some((w) => /pressure language/i.test(w)),
      `"${phrase}" was removed without a warning`,
    );
  }
});

test('a phrase is caught at the start, the middle and the end of a line', async () => {
  for (const phrase of PRESSURE_PHRASES) {
    for (const line of [
      `${phrase} and book your service`,
      `Book your ${phrase} service now`,
      `Book your winter service, ${phrase}`,
    ]) {
      const { copy } = await write({ headline: line });
      const left = (copy.headline as string) ?? '';
      assert.equal(carries(left), false, `${JSON.stringify(line)} left ${JSON.stringify(left)}`);
      // Both ends: the phrase is as often the last thing on the line as the
      // first, and a headline ending on the comma that introduced it is the
      // same copy nobody wrote.
      assert.doesNotMatch(left, /^[\s,;:!\u2013\u2014-]/, `${JSON.stringify(line)} left ${JSON.stringify(left)}`);
      assert.doesNotMatch(left, /[\s,;:!\u2013\u2014-]$/, `${JSON.stringify(line)} left ${JSON.stringify(left)}`);
    }
  }
});

test('only the pressure language is taken, never the words around it', async () => {
  // The cut swallows the punctuation that joined the phrase to the line, and
  // that is exactly how it could come to swallow a word: widen the class by
  // one character and "Book winter service, hurry" becomes "Book winter",
  // clean by every other measure here. So the words either side are named.
  for (const phrase of PRESSURE_PHRASES) {
    for (const line of [
      `Book winter service ${phrase}`,
      `Book winter service, ${phrase}`,
      `${phrase} book winter service`,
      `Book ${phrase} winter service`,
    ]) {
      const { copy } = await write({ headline: line });
      const left = ((copy.headline as string) ?? '').toLowerCase();
      for (const word of ['book', 'winter', 'service']) {
        assert.ok(
          left.includes(word),
          `removing "${phrase}" from ${JSON.stringify(line)} also took "${word}": ${JSON.stringify(left)}`,
        );
      }
    }
  }
});

test('every field the model writes is scrubbed, not only the headline', async () => {
  const { copy } = await write({
    headline: 'Book winter service, hurry',
    support: 'Act now and save on the annual tune-up',
    cta: 'Hurry now',
    offer: 'Last chance saving',
    trust: 'Rated well by neighbors, act now',
  });
  for (const [field, value] of Object.entries(copy)) {
    if (typeof value !== 'string' || !value) continue;
    assert.equal(carries(value), false, `${field} still carries pressure language: ${JSON.stringify(value)}`);
  }
});

test('copy with no pressure language is returned untouched and warns nothing', async () => {
  const clean = {
    headline: 'Same-day furnace repair',
    support: 'Licensed technicians across Riverside County',
    cta: 'Get Estimate',
  };
  const { copy, warnings } = await write(clean);
  assert.equal(copy.headline, clean.headline, 'a hyphenated word must not be cut');
  assert.equal(copy.support, clean.support);
  assert.equal(copy.cta, clean.cta);
  assert.equal(
    warnings.filter((w) => /pressure language|^Dropped /.test(w)).length,
    0,
    `clean copy raised a warning: ${JSON.stringify(warnings)}`,
  );
});

test('the detector and the remover are built from one list', async () => {
  const src = fs.readFileSync(path.resolve(__dirname, '../src/copywriter.ts'), 'utf8');
  const alternations = src.match(/hurry\|act now/g) ?? [];
  assert.equal(
    alternations.length,
    0,
    'the phrase alternation is written out by hand somewhere -- both regexes must be built from PRESSURE_PHRASES',
  );
  assert.ok(PRESSURE_PHRASES.length >= 5, 'the shipped list should still hold the phrases Amazon names');
});
