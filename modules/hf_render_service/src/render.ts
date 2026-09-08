/**
 * Orchestration: turn a validated job into a finished MP4, one at a time
 * (or as many as `HF_RENDER_CONCURRENCY` allows) rather than as many as
 * arrive. A render is minutes of headless Chrome plus an ffmpeg encode —
 * unbounded concurrency on a 2-CPU container is how the second job starves
 * the first and both starve the Hub's own gunicorn workers sharing the box.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { captureFrames } from "./capture.js";
import { encodeMp4, probeDuration } from "./ffmpeg.js";
import { Job, updateJob } from "./jobs.js";
import { dimensionsFor, FPS } from "./templates.js";
import { PaintParams, VoxParams } from "./validate.js";

const MAX_CONCURRENT = Math.max(1, parseInt(process.env.HF_RENDER_CONCURRENCY || "1", 10) || 1);

let active = 0;
const queue: Array<() => Promise<void>> = [];

function schedule(task: () => Promise<void>): void {
  queue.push(task);
  pump();
}

function pump(): void {
  while (active < MAX_CONCURRENT && queue.length > 0) {
    const task = queue.shift()!;
    active++;
    task().finally(() => {
      active--;
      pump();
    });
  }
}

export function queueDepth(): number {
  return queue.length + active;
}

function outputDir(): string {
  const dir = process.env.HF_OUTPUT_DIR || path.join(process.cwd(), "out");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

export function outputFile(jobId: string): string {
  return path.join(outputDir(), `${jobId}.mp4`);
}

function tmpFramesDir(jobId: string): string {
  return path.join(outputDir(), "tmp", jobId);
}

function cleanupFrames(dir: string): void {
  fs.rm(dir, { recursive: true, force: true }, () => undefined);
}

export function enqueuePaintRender(job: Job, params: PaintParams): void {
  schedule(() => runPaint(job, params));
}

export function enqueueVoxRender(job: Job, params: VoxParams): void {
  schedule(() => runVox(job, params));
}

async function runPaint(job: Job, params: PaintParams): Promise<void> {
  updateJob(job.id, { status: "rendering" });
  const { width, height } = dimensionsFor(params.format);
  const framesDir = tmpFramesDir(job.id);
  try {
    await captureFrames({
      templateFile: "paint.html",
      params,
      width,
      height,
      fps: FPS,
      totalSeconds: params.durationSeconds,
      framesDir,
      onProgress: (frac) => updateJob(job.id, { progress: Math.round(frac * 100) / 100 }),
    });
    const outFile = outputFile(job.id);
    await encodeMp4({ framesDir, framePattern: "frame_%05d.png", fps: FPS, outFile });
    const duration = await probeDuration(outFile);
    updateJob(job.id, {
      status: "done",
      url: `/files/${job.id}.mp4`,
      durationSeconds: duration,
      progress: 1,
      error: null,
    });
  } catch (err) {
    updateJob(job.id, { status: "failed", error: describeError(err) });
  } finally {
    cleanupFrames(framesDir);
  }
}

async function runVox(job: Job, params: VoxParams): Promise<void> {
  updateJob(job.id, { status: "rendering" });
  const { width, height } = dimensionsFor(params.format);
  const totalSeconds = Math.max(1, params.beats.reduce((sum, b) => sum + (b.seconds || 0), 0));
  const framesDir = tmpFramesDir(job.id);
  try {
    await captureFrames({
      templateFile: "vox.html",
      params,
      width,
      height,
      fps: FPS,
      totalSeconds,
      framesDir,
      onProgress: (frac) => updateJob(job.id, { progress: Math.round(frac * 100) / 100 }),
    });
    const outFile = outputFile(job.id);
    await encodeMp4({
      framesDir,
      framePattern: "frame_%05d.png",
      fps: FPS,
      outFile,
      audioUrl: params.voiceTrackUrl || undefined,
    });
    const duration = await probeDuration(outFile);
    updateJob(job.id, {
      status: "done",
      url: `/files/${job.id}.mp4`,
      durationSeconds: duration,
      progress: 1,
      error: null,
    });
  } catch (err) {
    updateJob(job.id, { status: "failed", error: describeError(err) });
  } finally {
    cleanupFrames(framesDir);
  }
}

function describeError(err: unknown): string {
  // An exception is not a message — the rule this Hub's own image and PDF
  // optimizers were fixed for. A Puppeteer or ffmpeg failure carries paths
  // and process internals a caller cannot act on; the shape is kept, the
  // detail bounded, so the Hub's `_service_error()` has a real sentence
  // rather than a stack trace to render into a page.
  const message = err instanceof Error ? err.message : String(err);
  return message.length > 300 ? message.slice(0, 300) + "…" : message;
}

/** Output files older than this are removed on a periodic sweep — the
 * Hub's `_keep()` re-uploads to Cloudinary promptly after a job finishes,
 * so a local file only needs to survive long enough for that plus a person
 * previewing it directly. Left indefinitely, every render this service has
 * ever produced accumulates on the shared disk, which is the failure
 * `modules/ad_builder/src/retention.ts` exists to prevent one module over. */
const MAX_FILE_AGE_MS = 48 * 60 * 60 * 1000;

export function sweepOutput(): { removed: number } {
  const dir = outputDir();
  let removed = 0;
  let entries: string[] = [];
  try {
    entries = fs.readdirSync(dir);
  } catch {
    return { removed: 0 };
  }
  const now = Date.now();
  for (const name of entries) {
    if (name === "tmp") continue;
    const full = path.join(dir, name);
    try {
      const stat = fs.statSync(full);
      if (now - stat.mtimeMs > MAX_FILE_AGE_MS) {
        fs.unlinkSync(full);
        removed++;
      }
    } catch {
      // A file that vanished or could not be stat'd between the readdir and
      // here is not this sweep's problem to raise about.
    }
  }
  return { removed };
}
