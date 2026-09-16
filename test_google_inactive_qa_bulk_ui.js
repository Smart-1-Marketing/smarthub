// The inactive-accounts QA page's own bulk-action script, run for real.
//
//     node test_google_inactive_qa_bulk_ui.js
//
// Same shape as the other browser-script tests here (test_background_ui.js,
// test_io_media_mix.js): the page's inline script is pulled out of the
// template and run in a `vm` context against a small DOM stub, so what is
// asserted is the shipped code rather than a copy of it. No new dependencies,
// nothing fetched.
//
// What this is guarding:
//
//   * Selection is per section and survives the re-render every action ends
//     in. A row that is no longer in the section it was picked in drops out
//     of the selection rather than staying picked invisibly -- otherwise a
//     press after a rescan acts on rows the person can no longer see.
//   * What each bulk action POSTs is the selected row objects themselves,
//     and only the rows that action applies to: Check sites sends GTM
//     containers and never a GA4 property, which has no tag to look for.
//   * A long selection is sent in several requests, at the same per-request
//     caps the endpoints enforce. One request carrying a hundred deletions
//     would outlive gunicorn's --timeout and be killed mid-flight.
//   * Delete asks for the count it is about to delete, and refuses to send
//     anything at all until that is typed correctly.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const html = fs.readFileSync('modules/google_access/templates/qa_inactive.html', 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function makeEl(id) {
  return {
    id, value: '', textContent: '', innerHTML: '', checked: false, indeterminate: false,
    disabled: false, dataset: {}, style: {}, hidden: false,
    classList: {add() {}, remove() {}, toggle() {}},
    _handlers: {},
    addEventListener(name, fn) { this._handlers[name] = fn; },
    showModal() { this.open = true; }, close() { this.open = false; },
    querySelectorAll() { return []; },
  };
}

// Row checkboxes only exist inside a tbody's rendered innerHTML, so they are
// read back out of it the way the browser would see them.
function checkboxesFrom(doc, selector) {
  const want = /input\[data-sec="([a-z]+)"\]/.exec(selector);
  const out = [];
  for (const id of ['#inactiveBody', '#reviewBody', '#skippedBody']) {
    const body = doc.elements.get(id);
    if (!body) continue;
    const re = /<input type="checkbox" data-sec="([a-z]+)" data-key="([^"]*)"( checked)?/g;
    let m;
    while ((m = re.exec(body.innerHTML))) {
      if (want && m[1] !== want[1]) continue;
      const key = m[2].replace(/&amp;/g, '&');
      out.push(doc.checkbox(m[1], key, Boolean(m[3])));
    }
  }
  return out;
}

function setup() {
  const elements = new Map();
  const requests = [];
  const boxes = new Map();
  const doc = {
    elements,
    checkbox(sec, key, checked) {
      const id = sec + '|' + key;
      if (!boxes.has(id)) boxes.set(id, {...makeEl(id), dataset: {sec, key}, checked});
      const box = boxes.get(id);
      box.checked = checked;
      return box;
    },
    querySelector(sel) {
      if (!elements.has(sel)) elements.set(sel, makeEl(sel));
      return elements.get(sel);
    },
    querySelectorAll(sel) {
      if (sel.includes('data-sec')) return checkboxesFrom(doc, sel);
      return [];
    },
  };
  const context = vm.createContext({
    document: doc, window: {addEventListener() {}}, console,
    setInterval: () => 0, clearInterval() {}, setTimeout, confirm: () => false,
    Date, Number, Math, JSON, Set, Array, String, Boolean, encodeURIComponent,
    decodeURIComponent,
    fetch: async (url, options) => {
      const body = options && options.body ? JSON.parse(options.body) : null;
      requests.push({url, body, method: (options || {}).method || 'GET'});
      return {ok: true, status: 200, json: async () => bodyFor(url, body)};
    },
  });
  function bodyFor(url, body) {
    if (url.includes('site-check/bulk')) {
      return {ok: true, checked: body.rows.length, found: 0,
              results: body.rows.map(r => ({ok: true, kind: r.kind, login: r.login,
                resource: r.resource, name: r.name, url: 'https://x.test', found: false,
                checked_at: '2026-09-16T00:00:00+00:00', status: 200, error: '', by: 'me'}))};
    }
    if (url.includes('delete/bulk')) {
      return {ok: true, deleted: body.rows.length, failed: 0,
              results: body.rows.map(r => ({ok: true, name: r.name, kind: r.kind,
                resource: r.resource, message: 'done'}))};
    }
    if (url.includes('/bulk')) return {ok: true, done: body.rows.length, failed: []};
    if (url.includes('scan/progress')) return {running: false};
    return {pending: true};
  }
  vm.runInContext(source, context);
  return {doc, context, requests, $: sel => doc.querySelector(sel)};
}

const row = (kind, resource, extra = {}) => ({
  kind, login: 'adops@example.com', account: 'Acme Plumbing', account_id: '9001',
  name: kind + ' ' + resource, resource, public_id: kind === 'GTM' ? 'GTM-' + resource : '',
  events: 0, sessions: 0, reason: 'No traffic', ...extra,
});

function payload(inactive, review, skipped) {
  return {ok: true, window_days: 60, scanned_at: '2026-09-16T00:00:00+00:00',
          connected_logins: 1, active_count: 0, inactive, review, skipped,
          source_error: '', errors: []};
}

function render(s, data) { vm.runInContext('render(STATE)', Object.assign(s.context, {STATE: data})); }
function pick(s, sec, keys) {
  for (const key of keys) {
    const box = s.doc.checkbox(sec, key, true);
    box.checked = true;
    if (box.onchange) box.onchange();
  }
}
function bulkBarState(s, sec) {
  const ids = {inactive: ['#cntInactive', '#bulkInactiveCheck', '#bulkInactiveSkip', '#bulkInactiveDelete'],
               review: ['#cntReview', '#bulkReviewCheck'],
               skipped: ['#cntSkipped', '#bulkSkippedCheck', '#bulkSkippedUnskip']}[sec];
  return ids.map(id => (id.startsWith('#cnt') ? s.$(id).textContent : s.$(id).disabled));
}

(async () => {
  // --- Nothing selected: every bulk button is off ---------------------------
  let s = setup();
  render(s, payload([row('GA4', 'p1'), row('GTM', 'c1')], [], []));
  assert.deepEqual(bulkBarState(s, 'inactive'), [0, true, true, true],
    'no selection must leave every bulk button disabled');

  // --- Picking rows enables exactly what applies ----------------------------
  pick(s, 'inactive', ['GA4:adops@example.com:p1']);
  assert.deepEqual(bulkBarState(s, 'inactive'), [1, true, false, false],
    'a GA4 row alone enables Skip and Delete but not the site check');
  assert.equal(s.$('#bulkInactiveCheck').textContent, 'Check sites');

  pick(s, 'inactive', ['GTM:adops@example.com:c1']);
  assert.deepEqual(bulkBarState(s, 'inactive'), [2, false, false, false]);
  assert.equal(s.$('#bulkInactiveCheck').textContent, 'Check 1 site',
    'the site-check button counts the GTM rows, not the selection');

  // --- Select-all takes the section, and the header box reflects it ----------
  s = setup();
  render(s, payload([row('GA4', 'p1'), row('GTM', 'c1'), row('GTM', 'c2')], [], []));
  const all = s.$('#allInactive');
  all.checked = true; all.onchange();
  assert.equal(s.$('#cntInactive').textContent, 3);
  assert.equal(s.$('#bulkInactiveCheck').textContent, 'Check 2 sites');
  all.checked = false; all.onchange();
  assert.equal(s.$('#cntInactive').textContent, 0, 'unticking the header clears the section');

  // --- Skip in bulk posts the rows and the one shared reason ----------------
  s = setup();
  render(s, payload([row('GA4', 'p1'), row('GTM', 'c1')], [], []));
  const all2 = s.$('#allInactive'); all2.checked = true; all2.onchange();
  s.$('#bulkInactiveSkip').onclick();
  assert.equal(s.$('#bulkSkipDialog').open, true, 'Skip opens its confirmation first');
  s.$('#bulkSkipReason').value = 'old client';
  await s.$('#bulkSkipForm')._handlers.submit({preventDefault() {}});
  const skipPost = s.requests.find(r => r.url.includes('skip/bulk'));
  assert.equal(skipPost.body.rows.length, 2);
  assert.equal(skipPost.body.reason, 'old client');
  assert.deepEqual(skipPost.body.rows.map(r => r.resource), ['p1', 'c1'],
    'the row objects themselves are posted, not just their ids');
  assert.equal(s.$('#cntInactive').textContent, 0, 'the selection is cleared once it is done');

  // --- Delete in bulk: the count is typed, or nothing is sent ---------------
  s = setup();
  render(s, payload([row('GA4', 'p1'), row('GTM', 'c1')], [], []));
  const all3 = s.$('#allInactive'); all3.checked = true; all3.onchange();
  s.$('#bulkInactiveDelete').onclick();
  assert.equal(s.$('#bulkDeletePhrase').textContent, 'DELETE 2');
  s.$('#bulkDeleteConfirm').value = 'DELETE';
  await s.$('#bulkDeleteForm')._handlers.submit({preventDefault() {}});
  assert.equal(s.requests.filter(r => r.url.includes('delete/bulk')).length, 0,
    'a half-typed confirmation sends nothing at all');
  assert.match(s.$('#bulkDeleteResult').textContent, /Type DELETE 2/);
  s.$('#bulkDeleteConfirm').value = 'delete 2';
  await s.$('#bulkDeleteForm')._handlers.submit({preventDefault() {}});
  const delPost = s.requests.find(r => r.url.includes('delete/bulk'));
  assert.equal(delPost.body.total, 2);
  assert.equal(delPost.body.confirm, 'delete 2');
  assert.equal(delPost.body.rows.length, 2);

  // --- A long selection is chunked at the endpoints' own caps ---------------
  s = setup();
  const many = Array.from({length: 45}, (_, i) => row('GTM', 'c' + i));
  render(s, payload(many, [], []));
  const all4 = s.$('#allInactive'); all4.checked = true; all4.onchange();
  assert.equal(s.$('#cntInactive').textContent, 45);
  s.$('#bulkInactiveDelete').onclick();
  s.$('#bulkDeleteConfirm').value = 'DELETE 45';
  await s.$('#bulkDeleteForm')._handlers.submit({preventDefault() {}});
  const delPosts = s.requests.filter(r => r.url.includes('delete/bulk'));
  assert.deepEqual(delPosts.map(r => r.body.rows.length), [20, 20, 5],
    '45 deletions go as three requests of at most 20');
  assert.deepEqual([...new Set(delPosts.map(r => r.body.total))], [45],
    'every chunk carries the selection total, never the chunk size');

  // --- Check sites sends GTM rows only, chunked at 12 -----------------------
  s = setup();
  const mixed = [row('GA4', 'p1'), ...Array.from({length: 14}, (_, i) => row('GTM', 'c' + i))];
  render(s, payload(mixed, [], []));
  const all5 = s.$('#allInactive'); all5.checked = true; all5.onchange();
  s.$('#bulkInactiveCheck').onclick();
  await s.$('#bulkCheckForm')._handlers.submit({preventDefault() {}});
  const checkPosts = s.requests.filter(r => r.url.includes('site-check/bulk'));
  assert.deepEqual(checkPosts.map(r => r.body.rows.length), [12, 2]);
  assert.ok(checkPosts.every(r => r.body.rows.every(x => x.kind === 'GTM')),
    'a GA4 property is never sent to the site check');
  assert.match(s.$('#bulkCheckResult').textContent, /0 tags found, 14 not found/);

  // --- Needs review and Skipped carry their own selections ------------------
  s = setup();
  render(s, payload([row('GA4', 'p1')], [row('GTM', 'r1')],
                    [Object.assign(row('GTM', 's1'), {skip: {reason: 'kept', by: 'me', at: '2026-09-01'}})]));
  s.$('#allReview').checked = true; s.$('#allReview').onchange();
  assert.equal(s.$('#cntReview').textContent, 1);
  assert.equal(s.$('#cntInactive').textContent, 0, 'picking in one section does not pick in another');
  assert.equal(s.$('#bulkReviewCheck').disabled, false);
  s.$('#allSkipped').checked = true; s.$('#allSkipped').onchange();
  assert.equal(s.$('#bulkSkippedUnskip').disabled, false);
  s.$('#bulkSkippedUnskip').onclick();
  await new Promise(r => setTimeout(r, 0));
  const unskipPost = s.requests.find(r => r.url.includes('unskip/bulk'));
  assert.equal(unskipPost.body.rows[0].resource, 's1');

  // --- A row that leaves its section leaves the selection with it -----------
  s = setup();
  render(s, payload([row('GA4', 'p1'), row('GTM', 'c1')], [], []));
  const all6 = s.$('#allInactive'); all6.checked = true; all6.onchange();
  assert.equal(s.$('#cntInactive').textContent, 2);
  render(s, payload([row('GA4', 'p1')], [row('GTM', 'c1')], []));
  assert.equal(s.$('#cntInactive').textContent, 1,
    'a promoted row is no longer selected in the section it left');
  assert.equal(s.$('#cntReview').textContent, 0,
    '...and is not silently selected in the one it arrived in');

  console.log('9 bulk-action UI scenarios passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
