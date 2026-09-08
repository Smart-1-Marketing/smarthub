import test from 'node:test';
import assert from 'node:assert/strict';
import { generateCopy } from '../src/copywriter';
import type { SizeKey } from '../src/types';

const brief = { business: 'Audit sample', promoting: 'Home services' };
const submission = { ...brief, website: 'https://example.test', campaignName: 'Test campaign' };

function strictObjects(schema: any): void {
  if (schema.type === 'object') {
    assert.equal(schema.additionalProperties, false);
    assert.deepEqual([...schema.required].sort(), Object.keys(schema.properties).sort());
    Object.values(schema.properties).forEach(strictObjects);
  }
  if (schema.items) strictObjects(schema.items);
}

for (const sizes of [['300x250', '320x50'], ['1200x628'], ['728x90']] as SizeKey[][]) {
  test(`copy request uses a valid strict schema for exactly ${sizes.join(', ')}`, async () => {
    let sent: any;
    const fetchImpl: typeof fetch = async (_url, init) => {
      sent = JSON.parse(String(init?.body));
      strictObjects(sent.response_format.json_schema.schema);
      const sizeSchema = sent.response_format.json_schema.schema.properties.concepts.items.properties.sizes;
      assert.deepEqual(Object.keys(sizeSchema.properties), sizes);
      for (const size of sizes) assert.ok(sent.messages[1].content.includes(`- ${size}:`));
      return new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify({ concepts: [
        { conceptId: 'A', name: 'Benefit', angle: 'Service', sizes: Object.fromEntries(sizes.map(size => [size,
          { headline: 'Get reliable home service', support: null, cta: 'Learn More', offer: null, trust: null }])) },
      ] }) } }] }));
    };
    const result = await generateCopy(brief, submission, { sizes, apiKey: 'test-only', fetchImpl });
    assert.ok(sent);
    assert.equal(result.source, 'openai', result.warnings.join('; '));
    assert.equal(result.concepts[0].copy.default?.headline, 'Get reliable home service');
    for (const size of sizes) assert.ok(result.concepts[0].copy[size]?.headline);
  });
}

test('missing requested copy is a visible fallback, never a successful empty concept', async () => {
  const fetchImpl: typeof fetch = async () => new Response(JSON.stringify({ choices: [{ message: {
    content: JSON.stringify({ concepts: [{ conceptId: 'A', sizes: {} }] }),
  } }] }));
  const result = await generateCopy(brief, submission, { sizes: ['728x90'], apiKey: 'test-only', fetchImpl });
  assert.equal(result.source, 'fallback');
  assert.match(result.warnings.join(' '), /no headline for 728x90/);
});

test('an upstream schema refusal reports the failure instead of claiming AI copy', async () => {
  const fetchImpl: typeof fetch = async () => new Response('Invalid schema', { status: 400 });
  const result = await generateCopy(brief, submission, { apiKey: 'test-only', fetchImpl });
  assert.equal(result.source, 'fallback');
  assert.match(result.warnings.join(' '), /400.*Invalid schema/);
});
