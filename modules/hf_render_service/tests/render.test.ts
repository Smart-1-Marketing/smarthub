/**
 * The real thing, end to end: submit a render, poll it to completion, and
 * verify a valid, correctly-dimensioned, correctly-timed MP4 came out the
 * other end. This is the expensive test in this suite — a real headless
 * Chromium capture and a real ffmpeg encode — and it is deliberately not
 * mocked, because a mocked version of this test would have missed every
 * one of the bugs it was written to catch: two variable-shadowing crashes
 * (`text`/`pop` colliding with p5's own functions) and a headline running
 * off the edge of the frame because p5's box-wrapped text() does not
 * behave the way it looks like it should.
 *
 * Needs `ffmpeg` on PATH and enough of Puppeteer's Chromium dependencies to
 * launch headless — the same requirement the README states. If either is
 * missing, `npm run build` still succeeds and this file's assertions do
 * the real work of catching a broken render pipeline before it reaches the
 * Hub.
 */
import test from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

process.env.NODE_ENV = "test";
const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "hf-render-test-"));
process.env.HF_OUTPUT_DIR = outDir;
process.env.HF_RENDER_CONCURRENCY = "1";

const { server } = await import("../src/server.js");
const { closeBrowser } = await import("../src/capture.js");

let base = "";

test.before(async () => {
  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const addr = server.address();
  const port = typeof addr === "object" && addr ? addr.port : 0;
  base = `http://127.0.0.1:${port}`;
});

test.after(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
  await closeBrowser();
});

async function pollToDone(jobId: string, timeoutMs = 60000): Promise<any> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = await fetch(`${base}/render/${jobId}/status`);
    const body = await res.json();
    if (body.status === "done" || body.status === "failed") return body;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`job ${jobId} did not finish within ${timeoutMs}ms`);
}

async function ffprobe(file: string): Promise<{ width: number; height: number; duration: number }> {
  const { stdout } = await execFileAsync("ffprobe", [
    "-v", "error",
    "-select_streams", "v:0",
    "-show_entries", "stream=width,height:format=duration",
    "-of", "json",
    file,
  ]);
  const parsed = JSON.parse(stdout);
  return {
    width: parsed.streams[0].width,
    height: parsed.streams[0].height,
    duration: parseFloat(parsed.format.duration),
  };
}

test("a paint-animation job renders a real, correctly-sized MP4", async () => {
  const submit = await fetch(`${base}/render/paint-animation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: "Smart 1 Marketing",
      style: "handwriting",
      durationSeconds: 1,
      format: "1:1",
    }),
  });
  assert.equal(submit.status, 202);
  const { jobId } = await submit.json();
  assert.ok(jobId);

  const finished = await pollToDone(jobId);
  assert.equal(finished.status, "done", JSON.stringify(finished));
  assert.ok(finished.url, "a done job must carry a url");
  assert.equal(finished.durationSeconds, 1);

  const fileRes = await fetch(`${base}${finished.url}`);
  assert.equal(fileRes.status, 200);
  assert.equal(fileRes.headers.get("content-type"), "video/mp4");

  const filePath = path.join(outDir, `${jobId}.mp4`);
  assert.ok(fs.existsSync(filePath), "the rendered file must actually be on disk");
  const probed = await ffprobe(filePath);
  assert.equal(probed.width, 1080);
  assert.equal(probed.height, 1080);
  assert.ok(Math.abs(probed.duration - 1) < 0.5, `expected ~1s, got ${probed.duration}`);
});

test("every paint style renders without crashing (the text()/pop() shadowing class of bug)", async () => {
  for (const style of ["handwriting", "paint_on", "living_painting"]) {
    const submit = await fetch(`${base}/render/paint-animation`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: `Style ${style}`, style, durationSeconds: 1, format: "16:9" }),
    });
    const { jobId } = await submit.json();
    const finished = await pollToDone(jobId);
    assert.equal(finished.status, "done", `${style}: ${JSON.stringify(finished)}`);
  }
});

test("a vox-explainer job with all four beat treatments renders a real MP4 of the right length", async () => {
  const beats = [
    { headline: "A statement beat.", treatment: "statement", seconds: 2 },
    { headline: "42%", support: "a data beat", treatment: "data", seconds: 2 },
    { headline: "A quoted line.", source: "Somebody", treatment: "quote", seconds: 2 },
    { headline: "A collage beat with a longer headline that has to wrap across more than one line to fit the frame.", treatment: "collage", seconds: 2, image_query: "a query" },
  ];
  const submit = await fetch(`${base}/render/vox-explainer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: "Test", format: "16:9", beats }),
  });
  assert.equal(submit.status, 202);
  const { jobId } = await submit.json();

  const finished = await pollToDone(jobId, 90000);
  assert.equal(finished.status, "done", JSON.stringify(finished));

  const filePath = path.join(outDir, `${jobId}.mp4`);
  const probed = await ffprobe(filePath);
  assert.equal(probed.width, 1920);
  assert.equal(probed.height, 1080);
  assert.ok(Math.abs(probed.duration - 8) < 0.5, `expected ~8s (4 beats x 2s), got ${probed.duration}`);
});

test("a broken image URL falls back gracefully rather than failing the job", async () => {
  const submit = await fetch(`${base}/render/paint-animation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: "fallback text",
      imageUrl: "https://127.0.0.1:1/does-not-exist.png",
      style: "paint_on",
      durationSeconds: 1,
      format: "16:9",
    }),
  });
  const { jobId } = await submit.json();
  const finished = await pollToDone(jobId);
  assert.equal(finished.status, "done", JSON.stringify(finished));
});
