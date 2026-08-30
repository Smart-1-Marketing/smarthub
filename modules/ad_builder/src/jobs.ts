/**
 * Job queue.
 *
 * In-memory for now, which is honest about what it is: fine for a single
 * instance, wrong the moment there are two. The plan calls for Render Key Value
 * as the queue, and this module is the seam where that swap happens — nothing
 * outside it knows how jobs are stored.
 *
 * Jobs do not survive a restart. On Render that matters, because a deploy
 * restarts the service, so anything in flight is lost. Persist to Render Key
 * Value or Postgres before this handles customer work.
 */

import { randomUUID } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { AnimatedResult, Campaign, RenderResult } from './types';
import { animationSupport } from './animation';
import { renderPackage, renderAnimatedPackage } from './render';
import { CloudinaryService, slug, type UploadedAsset } from './cloudinary';
import { buildManifest, contextFor, tagsFor } from './manifest';
import { writeReports } from './report';
import { copyForSize } from './render';
import { getPlatform, getTemplate } from './registry';
import type { SizeKey } from './types';

export type JobStatus = 'queued' | 'running' | 'complete' | 'failed';

export interface Job {
  id: string;
  status: JobStatus;
  /**
   * What this job builds.
   *
   * 'static' is the original render and is the default, so every existing
   * caller and every job recovered off disk from before this existed means
   * exactly what it always meant. 'animated' is its own job on the same queue
   * rather than extra work bolted onto the render, which is the whole point:
   * a static build must not get slower because animation exists, and an
   * animation asked for on a Friday must not require re-rendering eight ads
   * that were signed off on Tuesday.
   */
  mode?: 'static' | 'animated';
  createdAt: string;
  startedAt?: string;
  finishedAt?: string;
  client: string;
  campaignName: string;
  platforms: string[];
  upload: boolean;
  progress: { done: number; total: number };
  results?: RenderResult[];
  /** 'animated' jobs only. */
  animations?: AnimatedResult[];
  /** Sizes an 'animated' job did not produce, each with the reason. Never a
   *  silent gap: a set that came back with five of eight and no explanation is
   *  the answer this module exists to avoid. */
  animationSkipped?: { size: string; platform: string; reason: string }[];
  reports?: string[];
  projectFolder?: string;
  error?: string;
}

interface JobInput {
  campaign: Campaign;
  platforms: string[];
  upload: boolean;
  outDir: string;
  assetRoot: string;
  mode?: 'static' | 'animated';
  /** 'animated' jobs: which concept to animate. Absent means every one that
   *  carries an animation, which is how a two-concept set is done in one go. */
  conceptId?: string;
  /**
   * Render only these sizes.
   *
   * Absent means every size the template and platform have in common, which
   * is what "Render all sizes" has always done and stays the default. A
   * package of eight takes a couple of minutes, and an operator who has
   * changed one headline on the 300x600 is waiting on seven renders they did
   * not ask for — so they may now name the ones they want.
   *
   * A size the template or the platform does not carry is simply not in the
   * intersection and drops out; asking for one is not an error.
   */
  sizes?: SizeKey[];
}

const jobs = new Map<string, Job>();
const inputs = new Map<string, JobInput>();
const queue: string[] = [];
let running = false;

/**
 * Durability without new infrastructure. The queue itself stays in-memory —
 * still one instance only — but a job's state is mirrored to disk as it
 * changes, so a restart mid-render (a Render deploy is exactly this) does not
 * silently drop it. On boot the server calls recoverJobs(), which finds
 * anything left 'queued' or 'running' from before the restart and requeues it
 * from scratch. There is no partial-render resume; a requeued job re-renders
 * everything, which is simple and safe rather than clever.
 */
let jobsDir = '';

function jobFile(id: string): string {
  return path.join(jobsDir, `${id}.json`);
}

function persist(id: string): void {
  if (!jobsDir) return;
  const job = jobs.get(id);
  const input = inputs.get(id);
  if (!job) return;
  try {
    fs.mkdirSync(jobsDir, { recursive: true });
    fs.writeFileSync(jobFile(id), JSON.stringify({ job, input }, null, 2));
  } catch (e: any) {
    console.warn(`[jobs] could not persist ${id}: ${e?.message ?? e}`);
  }
}

/** Call once at boot, before startWorkerLoop, so recovered jobs are queued
 *  before the loop starts draining. */
export function recoverJobs(outDir: string): { recovered: number; discarded: number } {
  jobsDir = path.join(outDir, 'jobs');
  fs.mkdirSync(jobsDir, { recursive: true });

  let recovered = 0;
  let discarded = 0;
  for (const f of fs.readdirSync(jobsDir).filter((f) => f.endsWith('.json'))) {
    try {
      const { job, input } = JSON.parse(fs.readFileSync(path.join(jobsDir, f), 'utf8')) as {
        job: Job; input?: JobInput;
      };
      if ((job.status === 'queued' || job.status === 'running') && input) {
        job.status = 'queued';
        job.progress = { done: 0, total: job.progress.total };
        jobs.set(job.id, job);
        inputs.set(job.id, input);
        queue.push(job.id);
        persist(job.id);
        recovered++;
      } else {
        discarded++;
      }
    } catch {
      discarded++;
    }
  }
  if (recovered) console.log(`[jobs] recovered ${recovered} interrupted job(s) from before restart`);
  return { recovered, discarded };
}

/**
 * Which concepts an animated job is for.
 *
 * A concept with no animation on it is not an omission, it is a concept
 * nobody asked to animate -- so it is filtered out here rather than refused,
 * and a job with none left is what the route checks before enqueuing.
 */
function animatedConcepts(input: JobInput) {
  return input.campaign.concepts.filter(
    (c) => c.animation && c.animation.enabled !== false &&
      (!input.conceptId || c.conceptId === input.conceptId),
  );
}

/**
 * How many files an animated job will actually write.
 *
 * Counted over the intersection of what the template draws, what the operator
 * asked for, and what the PLACEMENT accepts -- the last of which is the one a
 * naive count gets wrong. Google runs eight animatable sizes out of eleven, so
 * a denominator of eleven leaves a progress bar that stops at 8/11 on a job
 * that finished perfectly.
 */
function countAnimated(input: JobInput, wanted: Set<string> | null): number {
  let n = 0;
  for (const concept of animatedConcepts(input)) {
    let template;
    try {
      template = getTemplate(concept.layoutFamily);
    } catch {
      continue;
    }
    for (const platform of input.platforms) {
      for (const size of Object.keys(template.sizes) as SizeKey[]) {
        if (wanted && !wanted.has(size)) continue;
        if (animationSupport(platform, size).supported) n++;
      }
    }
  }
  return n;
}

export function enqueue(input: JobInput): Job {
  const id = randomUUID().slice(0, 8);
  // Counted over exactly the set runJob will render, or the progress bar
  // reports a denominator the job can never reach.
  const wanted = input.sizes?.length ? new Set<string>(input.sizes) : null;
  const total = input.mode === 'animated'
    ? countAnimated(input, wanted)
    : input.platforms.reduce((n, p) => {
    try {
      const cfg = getPlatform(p);
      return (
        n +
        input.campaign.concepts.reduce((m, c) => {
          try {
            const t = getTemplate(c.layoutFamily);
            return m + Object.keys(t.sizes)
              .filter((s) => (cfg.sizes as any)[s] && (!wanted || wanted.has(s)))
              .length;
          } catch {
            return m;
          }
        }, 0)
      );
    } catch {
      return n;
    }
  }, 0);

  const job: Job = {
    id,
    status: 'queued',
    mode: input.mode ?? 'static',
    createdAt: new Date().toISOString(),
    client: input.campaign.brand.name,
    campaignName: input.campaign.campaignName,
    platforms: input.platforms,
    upload: input.upload,
    progress: { done: 0, total },
  };
  jobs.set(id, job);
  inputs.set(id, input);
  queue.push(id);
  persist(id);
  return job;
}

export const getJob = (id: string): Job | undefined => jobs.get(id);
export const listJobs = (): Job[] => [...jobs.values()];

async function runJob(id: string): Promise<void> {
  const job = jobs.get(id)!;
  const input = inputs.get(id)!;
  job.status = 'running';
  job.startedAt = new Date().toISOString();
  persist(id);

  try {
    const { campaign, platforms, upload, outDir, assetRoot } = input;

    // An animated job is its own path from here down. It writes GIFs beside
    // the static files, records them on the project, and deliberately does NOT
    // write a manifest or a report: those describe the static delivery pack an
    // ad ops person traffics, and a manifest containing only GIFs would be
    // read by `deliverProject` as the whole of what was built.
    if (input.mode === 'animated') {
      const all: AnimatedResult[] = [];
      const skipped: { size: string; platform: string; reason: string }[] = [];
      for (const concept of animatedConcepts(input)) {
        for (const platform of platforms) {
          const out = await renderAnimatedPackage({
            brand: campaign.brand,
            concept,
            platform,
            outDir,
            assetRoot,
            sizes: input.sizes,
            onProgress: (n) => { job.progress.done = all.length + n; persist(id); },
          });
          for (const r of out.results) r.url = `/files/${path.relative(outDir, r.file)}`;
          all.push(...out.results);
          for (const sk of out.skipped) skipped.push({ ...sk, platform });
          job.progress.done = all.length;
        }
      }
      job.animations = all;
      job.animationSkipped = skipped;
      job.status = 'complete';
      return;
    }

    const cld = new CloudinaryService();
    const projectFolder = cld.projectFolder(campaign.brand.name, campaign.campaignName);
    job.projectFolder = projectFolder;

    const all: RenderResult[] = [];
    const uploads = new Map<string, UploadedAsset>();

    // Deduplicate by size across platforms — but only when the delivered
    // output is byte-for-byte identical. A 300x250 is the same for Google and
    // Amazon, so render once and tag both. A 320x50 is NOT: Google delivers it
    // at 1x (320x50) and Amazon at 2x (640x100), so it must render per
    // platform. The dedup key is therefore size + deliverScale, not size alone.
    for (const concept of campaign.concepts) {
      const doneKeys = new Set<string>();
      for (const platform of platforms) {
        const rule = getPlatform(platform);
        const only = input.sizes?.length ? new Set<string>(input.sizes) : null;
        const sizes = (Object.keys(getTemplate(concept.layoutFamily).sizes) as SizeKey[])
          .filter((s) => {
            if (only && !only.has(s)) return false;
            const sr = rule.sizes[s];
            if (!sr) return false;
            return !doneKeys.has(`${s}@${sr.deliverScale}`);
          });
        if (!sizes.length) continue;

        const results = await renderPackage({
          brand: campaign.brand, concept, platform, outDir, assetRoot, sizes,
        });
        for (const r of results) {
          const myScale = rule.sizes[r.size]!.deliverScale;
          // Tag every platform that supports this size AT THE SAME scale.
          r.platforms = platforms.filter((p) => {
            const pr = getPlatform(p).sizes[r.size];
            return pr && pr.deliverScale === myScale;
          });
          doneKeys.add(`${r.size}@${myScale}`);
        }
        all.push(...results);
        job.progress.done = all.length;
      }
    }

    if (upload) {
      cld.assertUsable();
      await cld.createProjectFolders(campaign.brand.name, campaign.campaignName);
      for (const r of all.filter((x) => x.status !== 'fail')) {
        const concept = campaign.concepts.find((c) => c.conceptId === r.conceptId)!;
        const asset = await cld.uploadCreative({
          file: r.file,
          folder: cld.finalFolder(campaign.brand.name, campaign.campaignName, r.platform, r.conceptId),
          publicId: `${slug(campaign.brand.domain)}_${r.conceptId}_${r.size}`,
          tags: tagsFor(campaign, r, concept.name),
          context: contextFor(campaign, r, copyForSize(concept, r.size).headline ?? ''),
        });
        uploads.set(r.file, asset);
      }
    }

    const manifest = buildManifest({
      campaign,
      projectFolder,
      results: all,
      uploads,
      dryRun: false,
      uploaded: upload,
    });
    const reports = writeReports(manifest, path.join(outDir, 'reports'));

    job.results = all;
    job.reports = reports.map((f) => `/files/${path.relative(outDir, f)}`);
    job.status = 'complete';
  } catch (err: any) {
    job.status = 'failed';
    job.error = err?.message ?? String(err);
    console.error(`[job ${id}] failed:`, err);
  } finally {
    job.finishedAt = new Date().toISOString();
    inputs.delete(id);
    persist(id);
  }
}

/** Drain the queue one job at a time. Rendering is CPU-bound; parallelism here
 *  just makes every job slower and risks the memory limit on a small instance. */
/**
 * Watchdog: a job 'running' longer than RENDER_WATCHDOG_MIN (default 10) is
 * marked failed. A render that genuinely needs longer is indistinguishable
 * from a hang to the person staring at the skeleton screen — and a failed
 * state notifies staff and tells the customer the truth, while 'running
 * forever' does neither. Restart-resistant because jobs persist to disk with
 * their startedAt.
 */
export function startWatchdog(onTimeout?: (job: Job) => void): void {
  const limitMs = Number(process.env.RENDER_WATCHDOG_MIN ?? 10) * 60_000;
  if (limitMs <= 0) return;
  setInterval(() => {
    for (const job of jobs.values()) {
      if (job.status !== 'running' || !job.startedAt) continue;
      const age = Date.now() - Date.parse(job.startedAt);
      if (age > limitMs) {
        job.status = 'failed';
        job.error = `Watchdog: still running after ${Math.round(age / 60000)} minutes — treated as hung. ` +
          `The instance may be underpowered for a batch this size.`;
        job.finishedAt = new Date().toISOString();
        inputs.delete(job.id);
        persist(job.id);
        console.error(`[watchdog] job ${job.id} killed after ${Math.round(age / 60000)}m`);
        try { onTimeout?.(job); } catch { /* notification failure must not kill the loop */ }
      }
    }
  }, 30_000).unref();
}

export function startWorkerLoop(intervalMs = 500): void {
  setInterval(async () => {
    if (running || queue.length === 0) return;
    running = true;
    const id = queue.shift()!;
    try {
      await runJob(id);
    } finally {
      running = false;
    }
  }, intervalMs).unref();
}
