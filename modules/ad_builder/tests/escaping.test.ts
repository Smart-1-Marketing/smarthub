/**
 * What reaches a page as markup, and what the server owns.
 *
 * Three findings that share one shape: a value somebody outside this process
 * chose, interpolated into a page by a helper built for a different context.
 *
 *  - A render job's `error` is `err.message` from the pipeline, and several
 *    of those carry a value a rep supplied -- `Fetching background failed:
 *    404 <the url they pasted>`. assetUrlIsSafe() vouches for a URL's scheme
 *    and host, which is all an SSRF guard should do; the path and query are
 *    still whatever was typed. The diagnostics page rendered that raw, on the
 *    screen somebody opens *because* a job has already failed.
 *  - overview.ts put the requestId inside a single-quoted JS string using
 *    esc(), which escapes & < > " and not the apostrophe that closes it.
 *  - The intake route generated an unguessable requestId and then spread the
 *    submitted body over the top of it.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { renderDiagnostics } from '../src/diagnostics-page';
import { renderOverview } from '../src/overview';
import { assetUrlIsSafe } from '../src/assets';
import { withServerIdentity } from '../src/submission';

const report = (): any => ({
  host: 'renderer', generatedAt: '2026-01-01T00:00:00Z', uptimeSec: 120,
  verdict: 'healthy', summary: { ok: 1, warn: 0, fail: 0, skip: 0 }, checks: [],
});

const MARKUP = '<img src=x onerror=alert(1)>';

test('a job error carrying markup is escaped on the diagnostics page', () => {
  // The exact string assets.ts builds when a pasted background 404s. Its host
  // is ordinary, so the SSRF guard passes it -- correctly.
  const pasted = `https://res.cloudinary.com/demo/image/upload/a.jpg?x=${MARKUP}`;
  assert.equal(assetUrlIsSafe(pasted).ok, true, 'the host is fine; the guard is not the thing that stops this');

  const html = renderDiagnostics(report(), [{
    id: 'abc12345', status: 'failed', progress: { done: 0, total: 8 },
    startedAt: new Date().toISOString(), error: `Fetching background failed: 404 ${pasted}`,
  }]);

  assert.ok(!html.includes(MARKUP), 'the tag does not reach the page as markup');
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'), 'it is shown as text, because it is still the diagnosis');
});

test('a job id and status are escaped too', () => {
  const html = renderDiagnostics(report(), [{
    id: `A${MARKUP}`, status: `failed${MARKUP}`,
    progress: { done: 1, total: 2 }, startedAt: new Date().toISOString(),
  }]);
  assert.ok(!html.includes(MARKUP), 'no unescaped tag from either field');
  // Ids are a uuid slice today. The rule is the point: the next field added
  // to this row must not depend on where its value happens to come from.
  // Case-insensitive because the status cell upper-cases what it is given,
  // so the two escaped tags do not read alike.
  assert.equal((html.match(/&lt;img/gi) ?? []).length, 2, 'both fields escaped, neither dropped');
});

test('an apostrophe in the campaign id cannot close the brief form script', () => {
  // esc() is right for the attribute and wrong for a JS string; the endpoint
  // rides on a data attribute now, so there is no JS-string context left.
  const hostile = "x'+alert(document.domain)+'";
  const html = renderOverview({
    requestId: hostile, editable: true, ads: [],
    project: {}, campaign: {}, submission: {},
  } as any);

  assert.ok(html.includes('<form id="briefForm" data-endpoint='), 'the endpoint is an attribute');
  assert.ok(html.includes('fetch(form.dataset.endpoint'), 'the script reads it from the DOM');
  // Asserted against the SCRIPT, not the whole page. The id is also printed
  // as ordinary HTML text in the header ("Campaign x'+alert(...)+'"), where
  // an apostrophe means nothing -- a check over the whole document matches
  // that harmless site and passes whether or not the script was ever fixed.
  const script = html.slice(html.indexOf('<script>', html.indexOf('briefForm')));
  assert.ok(script.includes('fetch(form.dataset.endpoint'), 'the script reads it from the DOM');
  assert.ok(!script.includes(hostile), 'the value is not in the script at all');
  assert.ok(!/fetch\('[^']*'\s*\+/.test(script), 'nothing concatenates out of a quoted string');
});

test('the brief form still posts to the campaign it is showing', () => {
  // The fix must not cost the feature: a real id still reaches the endpoint.
  const html = renderOverview({
    requestId: 'AD-2026-ABCD1234', editable: true, ads: [],
    project: {}, campaign: {}, submission: {},
  } as any);
  assert.ok(html.includes('data-endpoint="/api/campaign/AD-2026-ABCD1234/brief"'));
});

test('the server owns the requestId, whatever the submission says', () => {
  const body = { business: 'Acme', email: 'a@b.test', requestId: '../../../etc/passwd' };
  const generated = 'AD-2026-K7M2PQ4R';

  const record = withServerIdentity(body, { requestId: generated, receivedAt: 'now' });
  assert.equal(record.requestId, generated, 'the generated id wins');
  assert.equal(record.business, 'Acme', 'and everything else the client sent is kept');
  assert.equal(record.receivedAt, 'now');

  const submission = withServerIdentity(body, { requestId: generated });
  assert.equal(submission.requestId, generated);
});
