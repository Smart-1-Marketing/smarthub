/**
 * The job table. In-memory and single-process, on purpose — this service
 * runs as one background process in the Hub's own container
 * (`docker-start.sh`), never scaled or replicated, so there is no second
 * process that could hold a job this one does not know about. That is the
 * same constraint `modules/ad_builder`'s own `render.yaml` documents about
 * its worker: "the job queue is currently in-memory, so a separate worker
 * process cannot see jobs enqueued by the web service." A restart loses
 * whatever was mid-render; the Hub's `status()` reads that as the render
 * service "having no record of that job," which is the documented 404
 * behaviour rather than a special case.
 */

import * as crypto from "node:crypto";

export type JobStatus = "queued" | "rendering" | "done" | "failed";

export interface Job {
  id: string;
  template: string;
  status: JobStatus;
  url: string | null;
  error: string | null;
  durationSeconds: number | null;
  progress: number | null;
  createdAt: number;
  updatedAt: number;
}

const jobs = new Map<string, Job>();

// Bounded on both age and count, the way `modules/hyperframes_tools/jobs.py`
// bounds its own store — a queue that never forgets a finished job is a
// queue that eventually holds every render this service has ever made.
const MAX_JOBS = 500;
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

export function createJob(template: string): Job {
  const job: Job = {
    id: crypto.randomUUID(),
    template,
    status: "queued",
    url: null,
    error: null,
    durationSeconds: null,
    progress: null,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
  jobs.set(job.id, job);
  sweep();
  return job;
}

export function getJob(id: string): Job | undefined {
  return jobs.get(id);
}

export function updateJob(id: string, patch: Partial<Omit<Job, "id" | "createdAt">>): Job | undefined {
  const job = jobs.get(id);
  if (!job) return undefined;
  Object.assign(job, patch, { updatedAt: Date.now() });
  return job;
}

/** Only a finished job (done/failed) is ever swept — a job still queued or
 * rendering is never removed on age alone, or a slow render on a busy day
 * would lose its own job row while it was still working. */
function sweep(): void {
  const now = Date.now();
  const finished = [...jobs.values()]
    .filter((j) => j.status === "done" || j.status === "failed")
    .sort((a, b) => a.updatedAt - b.updatedAt);

  for (const job of finished) {
    if (now - job.updatedAt > MAX_AGE_MS) jobs.delete(job.id);
  }

  const over = jobs.size - MAX_JOBS;
  if (over > 0) {
    const stillFinished = [...jobs.values()]
      .filter((j) => j.status === "done" || j.status === "failed")
      .sort((a, b) => a.updatedAt - b.updatedAt);
    for (const job of stillFinished.slice(0, over)) jobs.delete(job.id);
  }
}

export function jobCount(): number {
  return jobs.size;
}
