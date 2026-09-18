import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vm from 'node:vm';

function loadReview(_t: any, extraTail = '') {
  const html = fs.readFileSync(path.resolve(__dirname, '../public/review.html'), 'utf8');
  let code = html.match(/<script>([\s\S]*?)<\/script>/)![1];
  const end = code.lastIndexOf('})();');
  code = code.slice(0, end) + 'window.showReview=value=>{review=value;requestId="R1";showSheet();};window.warnSizesForTest=()=>warnSizes();window.setLastReview=value=>{review=value;};' + extraTail + code.slice(end);
  const nodes = new Map<string, any>(), events = new Map<string, () => void>();
  const node = (id: string) => { if (!nodes.has(id)) nodes.set(id, { checked: false, disabled: false, innerHTML: '', hidden: false, textContent: '', style: {}, onclick: undefined, onchange: undefined, appendChild: () => {} }); return nodes.get(id); };
  const window: any = { addEventListener: (name: string, fn: () => void) => events.set(name, fn) };
  const calls: any[] = [];
  const lastReview = { current: null as any };
  const context: any = { document: { getElementById: node, createElement: (tag: string) => ({ tagName: tag, style: {}, textContent: '', type: '', appendChild: () => {} }) }, window, location: { search: '?project=P1', origin: 'https://ex' }, URLSearchParams, console, encodeURIComponent,
    fetch: async (url: string, init: any) => {
      calls.push({ url, init });
      let body: any = {};
      if (/\/review-set\/[a-zA-Z0-9-]+$/.test(url)) body = lastReview.current;
      else if (/\/workflow$/.test(url)) body = { events: [], next: '' };
      else if (/\/approve-size$/.test(url)) body = { approvals: [] };
      return { ok: true, redirected: false, url, headers: { get: () => 'application/json' }, status: 200, json: async () => body || {} };
    } };
  vm.runInNewContext(code, context);
  const oldShow = window.showReview;
  window.showReview = (value: any) => { lastReview.current = value; oldShow(value); };
  return { node, window, events, calls };
}

test('printing includes the whole set and restores the problem filter afterward', () => {
  const { node, window, events } = loadReview(null);
  node('problems-only').checked = true;
  const cells = [{ conceptId: 'A', platform: 'google', size: '300x250', status: 'pass', qa: [], approved: false }, { conceptId: 'A', platform: 'google', size: '300x600', status: 'fail', qa: [], approved: false }];
  window.showReview({ status: 'ready', cells }); assert.doesNotMatch(node('sheet').innerHTML, /300x250/); assert.equal(node('send-next').disabled, true);
  events.get('beforeprint')!(); assert.match(node('sheet').innerHTML, /300x250/); assert.match(node('sheet').innerHTML, /300x600/);
  events.get('afterprint')!(); assert.equal(node('problems-only').checked, true); assert.doesNotMatch(node('sheet').innerHTML, /300x250/);
  window.showReview({ status: 'ready', cells: [{ ...cells[0], approved: true }] }); assert.equal(node('send-next').disabled, false);
});

test('the warn panel offers only sizes whose every platform is pass or warn, and never one with a fail', () => {
  const { node, window } = loadReview(null);
  // Three sizes:
  //   A/300x250 — warn on both platforms (offered)
  //   A/300x600 — warn on google, fail on meta (NOT offered: a fail excludes)
  //   A/728x90  — pass on both (NOT offered: it's already in the pass button)
  const cells = [
    { conceptId: 'A', platform: 'google', size: '300x250', status: 'warn', qa: [{ status: 'warn', detail: 'Text coverage is 22%.' }], approved: false },
    { conceptId: 'A', platform: 'meta',   size: '300x250', status: 'warn', qa: [{ status: 'warn', detail: 'Contrast is 3.9:1.' }], approved: false },
    { conceptId: 'A', platform: 'google', size: '300x600', status: 'warn', qa: [{ status: 'warn', detail: 'Anything.' }], approved: false },
    { conceptId: 'A', platform: 'meta',   size: '300x600', status: 'fail', qa: [{ status: 'fail', detail: 'Broken.' }], approved: false },
    { conceptId: 'A', platform: 'google', size: '728x90',  status: 'pass', qa: [], approved: false },
  ];
  window.showReview({ id: 'R', revision: 'v1', status: 'ready', cells });
  const offered = Array.from(window.warnSizesForTest());
  const keys = offered.map((g: any) => g.conceptId + '/' + g.size);
  assert.equal(keys.length, 1, 'exactly one size offered');
  assert.equal(keys[0], 'A/300x250', 'a fail on any platform excludes the size');
  assert.equal(node('warn-panel').hidden, false, 'the panel appears');
  assert.match(node('warn-list').innerHTML, /Text coverage is 22%\./, 'each finding is drawn in plain English');
  assert.match(node('warn-list').innerHTML, /Contrast is 3\.9:1\./);
  assert.match(node('warn-list').innerHTML, /data-warn-key="A\/300x250"/);
  assert.doesNotMatch(node('warn-list').innerHTML, /300x600/, 'the fail-tainted size is not offered');
  assert.doesNotMatch(node('warn-list').innerHTML, /728x90/, 'a clean pass belongs to the other button');

  // An already-approved warn size is not offered again.
  window.showReview({ id: 'R', revision: 'v1', status: 'ready', cells: cells.map(c => ({ ...c, approved: c.size === '300x250' })) });
  assert.equal(window.warnSizesForTest().length, 0);
  assert.equal(node('warn-panel').hidden, true);
});

test('ticking a warn size and pressing the button POSTs /approve-size with acceptWarnings for that size, never for a fail', async () => {
  const { node, window, calls } = loadReview(null);
  const cells = [
    { conceptId: 'A', platform: 'google', size: '300x250', status: 'warn', qa: [{ status: 'warn', detail: 'Text coverage.' }], approved: false },
    { conceptId: 'A', platform: 'meta',   size: '300x250', status: 'warn', qa: [{ status: 'warn', detail: 'Contrast.' }], approved: false },
    { conceptId: 'A', platform: 'google', size: '300x600', status: 'fail', qa: [{ status: 'fail', detail: 'Fail.' }], approved: false },
  ];
  window.showReview({ id: 'R', revision: 'v1', status: 'ready', cells });

  // Simulate the ticked checkbox for A/300x250 by stubbing the list's querySelectorAll.
  const list = node('warn-list');
  list.querySelectorAll = () => [{ checked: true, getAttribute: () => 'A/300x250' }];

  await node('approve-warnings').onclick();

  const approveCalls = calls.filter((c: any) => /\/approve-size$/.test(c.url));
  assert.equal(approveCalls.length, 1, 'one call per ticked size, never one for the fail');
  const body = JSON.parse(approveCalls[0].init.body);
  assert.equal(body.conceptId, 'A');
  assert.equal(body.size, '300x250');
  assert.equal(body.acceptWarnings, true, 'the whole point');
  assert.equal(body.revision, 'v1', 'so a stale review 409s rather than approving against a moved campaign');
  assert.equal(body.platform, 'google', 'any bought platform is enough; the server signs off every platform for that size');
});
