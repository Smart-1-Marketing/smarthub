/**
 * The in-memory job table. Single-process by design — see the module
 * docstring in `src/jobs.ts` for why that is a decision rather than a
 * shortcut.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { createJob, getJob, updateJob } from "../src/jobs.js";

test("a created job starts queued with nothing else set", () => {
  const job = createJob("paint-animation");
  assert.equal(job.status, "queued");
  assert.equal(job.url, null);
  assert.equal(job.error, null);
  assert.ok(job.id.length > 0);
});

test("getJob returns the same row by id, and undefined for an unknown one", () => {
  const job = createJob("paint-animation");
  assert.equal(getJob(job.id)?.id, job.id);
  assert.equal(getJob("not-a-real-id"), undefined);
});

test("updateJob merges rather than replaces", () => {
  const job = createJob("vox-explainer");
  updateJob(job.id, { status: "rendering", progress: 0.5 });
  const mid = getJob(job.id);
  assert.equal(mid?.status, "rendering");
  assert.equal(mid?.progress, 0.5);

  updateJob(job.id, { status: "done", url: "/files/x.mp4" });
  const done = getJob(job.id);
  assert.equal(done?.status, "done");
  assert.equal(done?.url, "/files/x.mp4");
  // progress from the earlier update must survive a later partial one.
  assert.equal(done?.progress, 0.5);
});

test("updateJob on an unknown id is a no-op, not a throw", () => {
  assert.equal(updateJob("does-not-exist", { status: "done" }), undefined);
});
