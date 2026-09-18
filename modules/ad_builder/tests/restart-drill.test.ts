/**
 * The restore drill: a render interrupted by a restart finishes after it.
 *
 * Every merge restarts the container mid-whatever it was doing. jobs.ts
 * mirrors each job to disk and recoverJobs() requeues what was running, and
 * the unit tests cover that function -- but nothing had ever killed the
 * process with a render in flight and checked that a second process picks
 * it up and finishes. This does: start the renderer, queue a job for three
 * sizes, wait until it is running, SIGKILL the process, start a fresh one on
 * the same output directory, and wait for the job to end with every size.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as net from 'node:net';
import { spawn, type ChildProcess } from 'node:child_process';
import { seedCampaign } from '../tests-browser/seed';

const ROOT = path.resolve(__dirname, '..');
const TOKEN = 'disposable-local-test-token-only';

async function freePort(): Promise<number> {
  const probe = net.createServer();
  await new Promise<void>((r) => probe.listen(0, '127.0.0.1', r));
  const port = (probe.address() as net.AddressInfo).port;
  await new Promise<void>((r) => probe.close(() => r()));
  return port;
}

async function boot(out: string, port: number): Promise<{ child: ChildProcess; logs: () => string }> {
  const child = spawn(process.execPath, ['--import', 'tsx', 'src/server.ts'], { cwd: ROOT,
    env: { PATH: process.env.PATH, HOST: '127.0.0.1', PORT: String(port), ADMIN_TOKEN: TOKEN, OUTPUT_DIR: out, HEALTH_CHECK_HOURS: '0' },
    stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = ''; child.stdout?.on('data', (d) => { logs += d; }); child.stderr?.on('data', (d) => { logs += d; });
  for (let i = 0; i < 600; i++) {
    try { if ((await fetch(`http://127.0.0.1:${port}/healthz`)).ok) return { child, logs: () => logs }; } catch { /* starting */ }
    if (child.exitCode !== null) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`renderer did not start:\n${logs}`);
}

const stop = (child: ChildProcess, signal: NodeJS.Signals = 'SIGKILL') => new Promise<void>((r) => {
  if (child.exitCode !== null || child.signalCode) return r();
  child.once('exit', () => r()); child.kill(signal);
});

test('a render interrupted by a restart is finished by the next process', { timeout: 300_000 }, async (t) => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'adb-drill-'));
  const port = await freePort();
  seedCampaign(out, 'AD-DRILL-1');
  const doc = JSON.parse(fs.readFileSync(path.join(out, 'campaigns', 'AD-DRILL-1.json'), 'utf8'));
  const sizes = ['300x250', '728x90', '160x600'];
  const headers = { 'x-admin-token': TOKEN, 'content-type': 'application/json' };

  let first = await boot(out, port);
  t.after(async () => { await stop(first.child); fs.rmSync(out, { recursive: true, force: true }); });

  const accepted = await fetch(`http://127.0.0.1:${port}/api/render`, { method: 'POST', headers,
    body: JSON.stringify({ campaign: doc.campaign, platforms: doc.platforms, sizes }) });
  const acceptedText = await accepted.text();
  assert.equal(accepted.status, 202, acceptedText);
  const { jobId } = JSON.parse(acceptedText) as { jobId: string };
  assert.ok(jobId);

  // Wait until the job is actually running, then pull the plug.
  let seen = '';
  for (let i = 0; i < 300; i++) {
    const r = await fetch(`http://127.0.0.1:${port}/api/render/${jobId}`, { headers });
    const job = await r.json() as { status: string; progress: { done: number } };
    seen = job.status;
    if (job.status === 'running') break;
    if (job.status !== 'queued') break;
    await new Promise((r2) => setTimeout(r2, 100));
  }
  assert.equal(seen, 'running', 'the job was running when the process was killed');
  await stop(first.child, 'SIGKILL');
  const mirrored = JSON.parse(fs.readFileSync(path.join(out, 'jobs', `${jobId}.json`), 'utf8'));
  assert.equal(mirrored.job.status, 'running', 'the job on disk still says running: nothing finished it');

  // A fresh process on the same directory picks it up from the start.
  const second = await boot(out, port);
  t.after(() => stop(second.child));
  assert.match(second.logs(), /recovered 1 interrupted job|requeued 1 job/, 'the boot log says the job was recovered');

  let final: any = null;
  for (let i = 0; i < 1200; i++) {
    const r = await fetch(`http://127.0.0.1:${port}/api/render/${jobId}`, { headers });
    assert.equal(r.status, 200, 'the job is known to the new process');
    final = await r.json();
    if (final.status !== 'queued' && final.status !== 'running') break;
    await new Promise((r2) => setTimeout(r2, 250));
  }
  assert.ok(final && final.status !== 'queued' && final.status !== 'running', `the job ended (${final?.status})`);
  assert.ok(!final.error, `the job did not fail: ${final.error}`);
  assert.ok(Array.isArray(final.results) && final.results.length >= sizes.length,
    `every size was rendered by the second process (${final.results?.length} results for ${sizes.length} sizes)`);
  for (const r of final.results) {
    if (r.file) assert.ok(fs.existsSync(r.file), `the file exists: ${r.file}`);
  }
});
