/**
 * The failure paths: a proof link replayed by a script meets a ceiling, a
 * body that is not JSON is the caller's mistake and not a crash, and the
 * seeded browser-test campaign has the project record every real build has.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as net from 'node:net';
import { spawn } from 'node:child_process';
import { budgetKey, BUDGETS, rateLimit, resetBuckets, loadBuckets, flushBuckets, bucketCount, bucketsDirtyForTest } from '../src/auth';
import { seedCampaign } from '../tests-browser/seed';
import { ProjectStore } from '../src/projects';

const req = (ip: string) => ({ headers: { 'x-forwarded-for': ip }, socket: { remoteAddress: ip } } as any);

test('one budget covers every proof link, not one per token', () => {
  assert.equal(budgetKey('POST /client-proof/11111111-2222-4333-8444-555555555555/comment'), 'POST /client-proof/:token/comment');
  assert.equal(budgetKey('POST /client-proof/11111111-2222-4333-8444-555555555555/decision'), 'POST /client-proof/:token/decision');
  assert.equal(budgetKey('POST /api/proof/acme_spring_2026-09-17/approve'), 'POST /api/proof/:id/approve');
  assert.equal(budgetKey('POST /api/preview'), 'POST /api/preview', 'an ordinary route is untouched');
  assert.equal(budgetKey('GET /client-proof/11111111-2222-4333-8444-555555555555'), 'GET /client-proof/:token');
  assert.ok(BUDGETS['POST /client-proof/:token/comment'].limit <= 60, 'a client clicking through never meets it');
  assert.ok(BUDGETS['POST /client-proof/:token/decision']);
  assert.ok(BUDGETS['POST /api/proof/:id/approve'] && BUDGETS['POST /api/proof/:id/revision']);
  assert.equal(BUDGETS['GET /client-proof/:token'], undefined, 'reading the proof is not budgeted');
});

test('a script replaying a proof link is refused after the budget, per address, across tokens', () => {
  resetBuckets();
  const limit = BUDGETS['POST /client-proof/:token/comment'].limit;
  const tokens = ['11111111-2222-4333-8444-555555555555', '22222222-2222-4333-8444-555555555555'];
  let allowed = 0;
  for (let i = 0; i < limit + 5; i++) {
    const r = rateLimit(`POST /client-proof/${tokens[i % 2]}/comment`, req('203.0.113.9'));
    if (r.allowed) allowed++;
  }
  assert.equal(allowed, limit, 'the two tokens share one allowance');
  assert.equal(rateLimit(`POST /client-proof/${tokens[0]}/comment`, req('203.0.113.10')).allowed, true, 'another address has its own');
  resetBuckets();
});

test('the ceiling survives a restart: buckets flush to disk and rehydrate at boot', (t) => {
  // The buckets used to reset on every deploy, which handed a fresh allowance
  // to whoever was climbing them in the ninety minutes before -- the whole
  // point of a per-hour budget. A flushed file rehydrates on the next boot,
  // and expired rows are dropped.
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'adb-limits-'));
  t.after(() => fs.rmSync(out, { recursive: true, force: true }));
  resetBuckets();

  const limit = BUDGETS['POST /client-proof/:token/decision'].limit;
  for (let i = 0; i < limit; i++) rateLimit('POST /client-proof/33333333-2222-4333-8444-555555555555/decision', req('198.51.100.20'));
  assert.equal(bucketsDirtyForTest(), true, 'ratelimit writes mark the map dirty');

  const wrote = flushBuckets(out);
  assert.equal(wrote, true, 'the flush wrote a file');
  const file = path.join(out, 'limits.json');
  assert.ok(fs.existsSync(file), 'and it is where the process would look for it');
  assert.equal(bucketsDirtyForTest(), false, 'and the dirty flag is cleared');
  assert.equal(flushBuckets(out), false, 'a second flush with no changes is a no-op');

  // Simulate a restart: clear the in-memory map, then rehydrate from disk.
  resetBuckets();
  assert.equal(bucketCount(), 0);
  const { loaded } = loadBuckets(out);
  assert.equal(loaded, 1, 'one bucket, one client key, one route');
  assert.equal(bucketsDirtyForTest(), false, 'the load itself does not mark dirty');

  // The ceiling still stands: one more request refuses, from the same address.
  const next = rateLimit('POST /client-proof/33333333-2222-4333-8444-555555555555/decision', req('198.51.100.20'));
  assert.equal(next.allowed, false, 'the client keeps its position on the ladder across the restart');
  assert.ok(next.retryAfterSec > 0);

  resetBuckets();
});

test('an expired bucket is dropped at load time so the file cannot rehydrate stale ceilings', (t) => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'adb-limits-expired-'));
  t.after(() => fs.rmSync(out, { recursive: true, force: true }));
  fs.writeFileSync(path.join(out, 'limits.json'), JSON.stringify({
    'POST /client-proof/:token/decision|198.51.100.30': { count: 99, resetAt: Date.now() - 60_000 },
    'POST /client-proof/:token/decision|198.51.100.31': { count: 1, resetAt: Date.now() + 60_000 },
  }));
  resetBuckets();
  const { loaded, dropped } = loadBuckets(out);
  assert.equal(loaded, 1, 'only the live bucket survives');
  assert.equal(dropped, 1, 'and the expired one is dropped');

  // A fresh caller on the expired row still gets an allowance.
  const fresh = rateLimit('POST /client-proof/33333333-2222-4333-8444-555555555555/decision', req('198.51.100.30'));
  assert.equal(fresh.allowed, true);
  resetBuckets();
});

test('a corrupt limits.json is logged and treated as an empty map, never a boot failure', (t) => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'adb-limits-torn-'));
  t.after(() => fs.rmSync(out, { recursive: true, force: true }));
  fs.writeFileSync(path.join(out, 'limits.json'), '{not json');
  resetBuckets();
  const errs: any[] = []; const orig = console.error; console.error = (...a: any[]) => errs.push(a);
  try {
    const r = loadBuckets(out);
    assert.equal(r.loaded, 0);
    assert.equal(bucketCount(), 0);
    assert.ok(errs.some((a) => String(a).includes('unreadable')), 'and it is logged');
  } finally { console.error = orig; resetBuckets(); }
});

test('the seeded campaign has a project record, once', (t) => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'adb-seed-'));
  t.after(() => fs.rmSync(out, { recursive: true, force: true }));
  seedCampaign(out, 'AD-SEED-1');
  seedCampaign(out, 'AD-SEED-1');
  const store = new ProjectStore(out);
  const p = store.byRequest('AD-SEED-1');
  assert.ok(p, 'a project record exists for the seeded request');
  assert.equal(store.all().filter((x) => x.requestId === 'AD-SEED-1').length, 1, 'seeding twice makes one record');
  assert.ok(fs.existsSync(path.join(out, 'campaigns', 'AD-SEED-1.json')));
});

test('a body that is not JSON is a 400, and a budgeted public route answers 429 in JSON', { timeout: 120_000 }, async (t) => {
  const root = path.resolve(__dirname, '..');
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'adb-fail-'));
  const probe = net.createServer();
  await new Promise<void>((r) => probe.listen(0, '127.0.0.1', r));
  const port = (probe.address() as net.AddressInfo).port;
  await new Promise<void>((r) => probe.close(() => r()));
  const token = 'disposable-local-test-token-only';
  const child = spawn(process.execPath, ['--import', 'tsx', 'src/server.ts'], { cwd: root,
    env: { PATH: process.env.PATH, HOST: '127.0.0.1', PORT: String(port), ADMIN_TOKEN: token, OUTPUT_DIR: out, HEALTH_CHECK_HOURS: '0' },
    stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = ''; child.stdout.on('data', (d) => { logs += d; }); child.stderr.on('data', (d) => { logs += d; });
  t.after(async () => { child.kill(); await new Promise<void>((r) => { if (child.exitCode !== null) r(); else child.once('exit', () => r()); }); fs.rmSync(out, { recursive: true, force: true }); });
  const base = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let i = 0; i < 600 && !ready; i++) {
    try { ready = (await fetch(base + '/healthz')).ok; } catch { /* starting */ }
    if (child.exitCode !== null) break;
    if (!ready) await new Promise((r) => setTimeout(r, 100));
  }
  assert.ok(ready, logs);

  const bad = await fetch(base + '/api/qa/advise', { method: 'POST', headers: { 'x-admin-token': token, 'content-type': 'application/json' }, body: '{not json' });
  assert.equal(bad.status, 400);
  assert.match((await bad.json()).error, /not valid JSON/);

  // The public decision route: no token, a fake proof link, and a budget.
  const tok = '11111111-2222-4333-8444-555555555555';
  const limit = BUDGETS['POST /client-proof/:token/decision'].limit;
  let last = 0;
  for (let i = 0; i < limit + 1; i++) {
    const r = await fetch(`${base}/client-proof/${tok}/decision`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
    last = r.status;
    if (i < limit) assert.notEqual(r.status, 429, `call ${i + 1} is within the budget`);
  }
  assert.equal(last, 429, 'the call past the budget is refused');
});
