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
import { budgetKey, BUDGETS, rateLimit, resetBuckets } from '../src/auth';
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
