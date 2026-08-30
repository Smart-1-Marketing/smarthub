/**
 * The five lines an ad carries, drafted from the client's own page.
 *
 * The build screen has five copy boxes and this function answered three, so
 * "draft it for me" produced most of an ad and left the offer and the proof
 * point empty on templates that draw both.
 *
 * The two it gained are also the two that must never be invented. A made-up
 * discount and a made-up review count are the sentences a client has to stand
 * behind, and a model asked for a proof point will always produce one. So an
 * empty answer here is an answer, and these tests are mostly about keeping it
 * empty.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { suggestCopy } from '../src/copy-approval';
import type { LandingAnalysis } from '../src/projects';

const landing = (extra: Partial<LandingAnalysis> = {}): LandingAnalysis => ({
  fetchedAt: '2026-01-01T00:00:00Z',
  url: 'https://riverside-hvac.example',
  summary: 'Furnace and air-conditioning repair across Riverside County.',
  suggestedHeadlines: ['Heat back on today'],
  suggestedSupport: [],
  suggestedCtas: [],
  warnings: [],
  source: 'heuristic',
  ...extra,
});

/** A stubbed OpenAI that answers with exactly this JSON. */
const model = (payload: Record<string, unknown>) =>
  (async () => ({
    ok: true,
    json: async () => ({ choices: [{ message: { content: JSON.stringify(payload) } }] }),
  })) as unknown as typeof fetch;

test('the model is asked for five fields and all five come back', async () => {
  const out = await suggestCopy(
    { business: 'Riverside HVAC', promoting: 'furnace repair', landing: landing() },
    { apiKey: 'test', fetchImpl: model({
        headline: 'Heat Back On Today', support: 'Same-day furnace repair, seven days.',
        offer: '$89 diagnostic', proof: 'Licensed since 1998', cta: 'Book Now' }) },
  );
  assert.equal(out.headline, 'Heat Back On Today');
  assert.equal(out.support, 'Same-day furnace repair, seven days.');
  assert.equal(out.offer, '$89 diagnostic');
  assert.equal(out.trust, 'Licensed since 1998');
  assert.equal(out.cta, 'Book Now');
  assert.equal(out.source, 'openai');
});

test('an offer the page does not state comes back empty, not filled in', async () => {
  // The whole point. Every other field falls back to something sensible when
  // the model omits it; these two have no sensible default, and the empty
  // string is the honest one.
  const out = await suggestCopy(
    { business: 'Riverside HVAC', promoting: 'furnace repair', landing: landing() },
    { apiKey: 'test', fetchImpl: model({
        headline: 'Heat Back On Today', support: 'Same-day repair.',
        offer: '', proof: '', cta: 'Book Now' }) },
  );
  assert.equal(out.offer, '', 'no discount invented');
  assert.equal(out.trust, '', 'no rating, license or count invented');
});

test('the prompt forbids inventing either of them, in words', async () => {
  // A rule that lives only in a comment is a rule the next edit removes. The
  // instruction is asserted here for the same reason test_proposal_spec.py
  // asserts the Smart 1 Labs prohibition: a prompt is a request, and this is
  // the part of it that must not quietly go missing.
  let sent = '';
  const capture = (async (_u: string, init: any) => {
    sent = String(init.body);
    return { ok: true, json: async () => ({ choices: [{ message: { content: '{}' } }] }) };
  }) as unknown as typeof fetch;
  await suggestCopy({ business: 'X', promoting: 'y', landing: landing() },
                    { apiKey: 'test', fetchImpl: capture });
  assert.match(sent, /Never invent a discount, a rating, a review count/);
  // The body is JSON, so the prompt's own quotes arrive backslash-escaped.
  assert.match(sent, /Offer and proof must come from the page/);
  assert.match(sent, /If the page states neither, return/);
});

test('what the page analyzer already read is handed over, not re-guessed', async () => {
  let sent = '';
  const capture = (async (_u: string, init: any) => {
    sent = String(init.body);
    return { ok: true, json: async () => ({ choices: [{ message: { content: '{}' } }] }) };
  }) as unknown as typeof fetch;
  await suggestCopy(
    { business: 'X', promoting: 'y',
      landing: landing({ detectedOffer: '$89 diagnostic', detectedCta: 'Schedule',
                         audience: 'homeowners', title: 'Riverside HVAC' }) },
    { apiKey: 'test', fetchImpl: capture },
  );
  for (const fragment of ['$89 diagnostic', 'Schedule', 'homeowners',
                          'riverside-hvac.example']) {
    assert.ok(sent.includes(fragment), `the model was told: ${fragment}`);
  }
});

test('with no model at all the proof point stays empty', async () => {
  // The fallback assembles from form answers. It has a source for an offer --
  // what somebody typed, or what the analyzer literally found on the page --
  // and no source whatever for a proof point.
  const out = await suggestCopy(
    { business: 'Riverside HVAC', promoting: 'furnace repair',
      landing: landing({ detectedOffer: '$89 diagnostic' }) },
    { apiKey: '' },
  );
  assert.equal(out.source, 'fallback');
  assert.equal(out.offer, '$89 diagnostic', 'read off the page, not written');
  assert.equal(out.trust, '', 'nothing to say, so nothing said');
});

test('a model that answers with nothing does not produce a confident ad', async () => {
  const out = await suggestCopy(
    { business: 'X', promoting: 'y', landing: landing() },
    { apiKey: 'test', fetchImpl: model({}) },
  );
  assert.equal(out.offer, '');
  assert.equal(out.trust, '');
  assert.equal(out.cta, 'Learn More', 'only the CTA has a safe default');
});
