/**
 * The render queue, and what a restart does to it.
 *
 * Two things here are worth pinning and neither had a test.
 *
 * The **progress denominator**. `enqueue` counts the sizes it is about to
 * render, and if that count is over the set the worker actually walks, the
 * bar reports a total the job can never reach — which reads on the build
 * screen as a render that stalled at 6 of 8 and stayed there.
 *
 * And **recovery**. A Render deploy restarts the service mid-render, so
 * `recoverJobs()` is not a rainy-day path, it is what happens on every
 * deploy. It has to requeue what was in flight, leave what was finished
 * alone, and survive a file it cannot read — all on a directory the sweep
 * has been pruning underneath it.
 *
 * Nothing here renders. `startWorkerLoop` is never called, so the queue is
 * inspected rather than drained: these are the decisions made before any
 * pixels exist.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { enqueue, getJob, recoverJobs } from '../src/jobs';
import { getPlatform, getTemplate } from '../src/registry';
import type { Campaign } from '../src/types';

const ROOT = path.resolve(__dirname, '..');

function tmp(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'jobs-test-'));
}

function campaign(): Campaign {
  return {
    requestId: 'AD-2026-000002',
    campaignName: 'Queue Test',
    brand: {
      name: 'Riverside HVAC',
      domain: 'riverside-hvac.example',
      colors: { primary: '#0F2A44', secondary: '#1D4E76', accent: '#F2B705',
                light: '#FFFFFF', dark: '#12202E' },
      fonts: { headline: 'Poppins', body: 'Poppins' },
      logos: { primary: 'assets/brand/icon-solar-primary.png' },
    },
    concepts: [{ conceptId: 'A', name: 'Direct', layoutFamily: 'T07', copy: {
      default: { headline: 'Heat back on today', support: 'Same-day repair', cta: 'Book Now' },
    } }],
  } as unknown as Campaign;
}

const input = (over: Record<string, unknown> = {}) => ({
  campaign: campaign(), platforms: ['google'], upload: false,
  outDir: tmp(), assetRoot: ROOT, ...over,
});

/* --------------------------------------------------- the denominator */

/** How many sizes one concept on this template actually buys on a platform. */
function expected(platform: string, family = 'T07'): number {
  const cfg = getPlatform(platform).sizes as Record<string, unknown>;
  return Object.keys(getTemplate(family).sizes).filter((s) => cfg[s]).length;
}

test('the total counts the sizes the worker will actually render', () => {
  const job = enqueue(input());
  assert.equal(job.progress.total, expected('google'));
  assert.equal(job.progress.done, 0);
});

test('two platforms count separately, because both get rendered', () => {
  const job = enqueue(input({ platforms: ['google', 'meta'] }));
  assert.equal(job.progress.total, expected('google') + expected('meta'));
});

test('asking for a subset moves the denominator with it', () => {
  // The bar has to end where the work ends. Counted over the full set, a
  // one-size render reports 1 of 8 and looks stalled for ever.
  const job = enqueue(input({ sizes: ['300x250'] }));
  assert.equal(job.progress.total, 1);
});

test('a size neither the template nor the platform carries is not an error', () => {
  // It drops out of the intersection, which is the documented behaviour --
  // and it must not inflate the denominator on its way out.
  const job = enqueue(input({ sizes: ['300x250', '9999x9999'] }));
  assert.equal(job.progress.total, 1, 'the impossible one is simply not counted');
});

test('an unknown platform contributes nothing rather than throwing', () => {
  const job = enqueue(input({ platforms: ['google', 'tiktok'] }));
  assert.equal(job.progress.total, expected('google'));
});

test('a concept on a layout that does not exist does not sink the job', () => {
  const c = campaign();
  (c.concepts[0] as any).layoutFamily = 'T99';
  const job = enqueue(input({ campaign: c }));
  assert.equal(job.progress.total, 0, 'nothing to render, and nothing thrown');
  assert.equal(job.status, 'queued');
});

/* ------------------------------------------------------ what a job carries */

test('a queued job names the client and campaign it belongs to', () => {
  // These are what the projects dashboard lists a running render under.
  const job = enqueue(input());
  assert.equal(job.client, 'Riverside HVAC');
  assert.equal(job.campaignName, 'Queue Test');
  assert.equal(job.status, 'queued');
  assert.ok(getJob(job.id), 'and it is findable by id');
});

/* ------------------------------------------------------------- recovery */

/** Write a job file the way persist() does, without running a render. */
function seed(dir: string, job: Record<string, unknown>, withInput = true) {
  const jobsDir = path.join(dir, 'jobs');
  fs.mkdirSync(jobsDir, { recursive: true });
  fs.writeFileSync(path.join(jobsDir, `${job.id}.json`), JSON.stringify({
    job, input: withInput ? input({ outDir: dir }) : undefined,
  }));
}

const stub = (id: string, status: string) => ({
  id, status, createdAt: new Date().toISOString(),
  client: 'Riverside HVAC', campaignName: 'Queue Test',
  platforms: ['google'], upload: false, progress: { done: 3, total: 8 },
});

test('a render interrupted by a deploy is requeued, from the start', () => {
  // There is no partial resume on purpose: a requeued job re-renders
  // everything, which is simple and safe rather than clever. So the progress
  // it had reached must be cleared, or the bar starts at 3 of 8 and the first
  // finished size takes it to 4 while only one file is new.
  const dir = tmp();
  seed(dir, stub('aaaa1111', 'running'));
  const out = recoverJobs(dir);
  assert.equal(out.recovered, 1);
  const job = getJob('aaaa1111')!;
  assert.equal(job.status, 'queued', 'it will be picked up again');
  assert.equal(job.progress.done, 0, 'from scratch');
  assert.equal(job.progress.total, 8, 'against the same denominator');
});

test('a job that was still queued is requeued too', () => {
  const dir = tmp();
  seed(dir, stub('aaaa2222', 'queued'));
  assert.equal(recoverJobs(dir).recovered, 1);
  assert.equal(getJob('aaaa2222')!.status, 'queued');
});

test('a finished job is left alone, not run again', () => {
  // The files are already delivered. Re-rendering them on every deploy would
  // burn the instance and rewrite creative somebody may have approved.
  const dir = tmp();
  seed(dir, stub('aaaa3333', 'complete'));
  seed(dir, stub('aaaa4444', 'failed'));
  const out = recoverJobs(dir);
  assert.equal(out.recovered, 0);
  assert.equal(out.discarded, 2);
  assert.equal(getJob('aaaa3333'), undefined, 'and not resurrected into memory');
});

test('a job whose input did not survive is discarded rather than half-run', () => {
  // Without the input there is nothing to render from. Requeueing it would
  // put a job in the queue that fails the moment the worker reaches it.
  const dir = tmp();
  seed(dir, stub('aaaa5555', 'running'), false);
  const out = recoverJobs(dir);
  assert.equal(out.recovered, 0);
  assert.equal(out.discarded, 1);
});

test('a file that cannot be read costs one job, not the boot', () => {
  // The sweep prunes this directory underneath us, and a half-written file
  // from a process killed mid-persist is exactly what a deploy produces.
  const dir = tmp();
  const jobsDir = path.join(dir, 'jobs');
  fs.mkdirSync(jobsDir, { recursive: true });
  fs.writeFileSync(path.join(jobsDir, 'aaaa6666.json'), '{"job": {"id": "aaaa6');
  seed(dir, stub('aaaa7777', 'running'));
  const out = recoverJobs(dir);
  assert.equal(out.recovered, 1, 'the good one still comes back');
  assert.equal(out.discarded, 1, 'and the torn one is counted, not thrown');
});

test('an empty or absent jobs directory is a clean boot, not a crash', () => {
  const dir = tmp();
  const out = recoverJobs(dir);
  assert.deepEqual(out, { recovered: 0, discarded: 0 });
  assert.ok(fs.existsSync(path.join(dir, 'jobs')), 'and the directory is made ready');
});

test('recovery writes the job back, so a second restart still finds it', () => {
  // A deploy can land during a deploy. If recovery only restored to memory,
  // the next restart would find the file still saying 'running' -- or, worse,
  // find it saying 'queued' with the progress it had before.
  const dir = tmp();
  seed(dir, stub('aaaa8888', 'running'));
  recoverJobs(dir);
  const onDisk = JSON.parse(fs.readFileSync(path.join(dir, 'jobs', 'aaaa8888.json'), 'utf8'));
  assert.equal(onDisk.job.status, 'queued');
  assert.equal(onDisk.job.progress.done, 0);
});
