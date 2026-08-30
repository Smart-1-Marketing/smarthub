/**
 * The page a client opens.
 *
 * Two things are asserted here, and both are invisible from either end.
 *
 * The proof is public as of the client-facing review link: whoever holds the
 * URL sees it, with no Hub session. So what the page interpolates matters in
 * a way it did not while only staff could reach it -- and it interpolates the
 * campaign's copy into an inline `<script>` through `JSON.stringify`, which
 * produces perfectly valid JavaScript and does not escape `</script>`. An
 * HTML parser ends a script block at that literal string wherever it appears,
 * a quoted string included, so one of them in the data closes the block early
 * and the rest of the page is parsed as markup. The copy is typed by a rep,
 * and `meta.promoting` falls back to a summary read off the client's own
 * website -- text nobody here vouches for.
 *
 * The second is the editor split: `editor: false` is what makes this page
 * safe to send, because the live editor rebuilds the creative and reaches
 * endpoints that are billed per call. A client must still be able to approve
 * or ask for changes -- a proof they cannot answer is an email attachment
 * with extra steps -- so the two halves are asserted together. Dropping the
 * decision buttons alongside the editor would look like a tidy-up and would
 * quietly retire the whole feature.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { renderProof } from '../src/proof';
import type { Manifest } from '../src/report';

function manifest(): Manifest {
  return {
    requestId: 'AD-TEST-1',
    client: 'Acme Solar',
    campaign: 'Spring rebate',
    generatedAt: '2026-01-01T00:00:00.000Z',
    entries: [],
    concepts: [],
  } as unknown as Manifest;
}

const CLOSER = '</' + 'script>';

test('a script closer in the copy cannot end the page-s script block', () => {
  const html = renderProof(manifest(), {
    initialCopy: { A: { headline: `Buy now ${CLOSER}<script>alert(1)</` + 'script>' } },
  } as never);

  assert.ok(
    !html.includes(`${CLOSER}<script>alert(1)`),
    'the closer reached the page verbatim, so the script block ends inside the data',
  );
  assert.ok(
    html.includes('\\u003c'),
    'the angle bracket should survive as an escape JSON.parse still reads as "<"',
  );
});

test('every value handed to the inline script is escaped, not just the copy', () => {
  // meta.promoting is the one read off somebody else-s website; delivered and
  // perSizeCopy carry rep and client input. A helper applied to one of the six
  // and not the others is the shape this whole check exists to catch.
  const html = renderProof(manifest(), {
    initialCopy: { A: { headline: `h${CLOSER}` } },
    initialColors: { accent: `#fff${CLOSER}` },
    meta: { promoting: `we sell ${CLOSER} panels` },
    perSizeCopy: { A: { '300x250': { headline: `s${CLOSER}` } } },
    delivered: { zipUrl: `/zip${CLOSER}` },
    actionBase: '/api/proof/p1',
  } as never);

  // Only the six assignment lines: the block they open legitimately ends with
  // a closer of its own, and the page-s own script body quotes one too.
  const start = html.indexOf('window.PROOF_ENDPOINT');
  const last = html.indexOf('\n', html.indexOf('window.PROOF_DELIVERED'));
  const assigns = html.slice(start, last);
  assert.ok(!assigns.includes(CLOSER), 'a raw closer survived in one of the six values');
  assert.equal(
    (assigns.match(/\\u003c/g) ?? []).length,
    5,
    'five of the six values carried a closer, so five escapes should be present',
  );
});

test('the escaped value is still what JSON.parse gives back', () => {
  // Escaping that changed the data would be a different bug wearing a fix: the
  // editor prefills from these globals, so a mangled headline is a rep-s copy
  // silently rewritten on the page a client reads.
  const headline = `Buy now ${CLOSER} today`;
  const html = renderProof(manifest(), { initialCopy: { A: { headline } } } as never);
  const line = html.split('\n').find((l) => l.startsWith('window.PROOF_COPY = '))!;
  const value = JSON.parse(line.slice('window.PROOF_COPY = '.length).replace(/;$/, ''));
  assert.equal(value.A.headline, headline, 'the copy came back changed');
});

test('a line separator in the copy is escaped too', () => {
  // U+2028 is legal inside a JSON string and is a line terminator to a
  // JavaScript parser, so unescaped it is a syntax error that costs the page
  // rather than one value.
  const html = renderProof(manifest(), {
    initialCopy: { A: { headline: 'one two' } },
  } as never);
  const script = html.slice(html.indexOf('window.PROOF_COPY'));
  assert.ok(!script.includes(' '), 'a raw U+2028 reached the script block');
  assert.ok(script.includes('\\u2028'), 'it should be escaped rather than dropped');
});

test('a client-s proof carries the decision buttons and none of the editor', () => {
  const html = renderProof(manifest(), {
    editor: false,
    actionBase: '/api/proof/p1',
  } as never);

  assert.ok(html.includes('id="approve"'), 'a client must be able to approve');
  assert.ok(html.includes('id="request"'), 'a client must be able to ask for changes');
  assert.ok(!html.includes('id="rebuild"'), 'rebuild is billed and belongs to the operator');
  assert.ok(!html.includes('id="ed-headline"'), 'the copy editor is staff-only');
  assert.ok(!html.includes('class="edit-size"'), 'per-size editing is staff-only');
});

test('a staff proof keeps the editor', () => {
  const html = renderProof(manifest(), {
    editor: true,
    actionBase: '/api/proof/p1',
  } as never);

  assert.ok(html.includes('id="rebuild"'), 'the operator rebuilds from here');
  assert.ok(html.includes('id="ed-headline"'), 'the operator edits copy from here');
  assert.ok(html.includes('id="approve"'), 'staff sign-off uses the same buttons');
});
