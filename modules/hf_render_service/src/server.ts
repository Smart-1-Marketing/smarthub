/**
 * hf-render-service — the whole of the HTTP surface `hub/hyperframes.py`
 * talks to. Node's built-in http rather than a framework, the way
 * `modules/ad_builder/src/server.ts` reasons about its own four routes:
 * this has four too, and every one of them is documented in
 * `hub/hyperframes.py`'s own module docstring as the wire contract this
 * file exists to satisfy exactly.
 *
 * Reached only from the Hub's own Python process, over loopback
 * (`docker-start.sh` binds this to 127.0.0.1 and never 0.0.0.0) — there is
 * no browser-facing surface here at all, unlike `ad_builder`'s proxy. No
 * token, no API key: "self-hosted, and there is nothing to authenticate to
 * a vendor" is `hub/hyperframes.py`'s own reasoning, and the loopback bind
 * is what makes that safe rather than a second thing to configure.
 */

import * as http from "node:http";
import * as fs from "node:fs";
import * as url from "node:url";
import { createJob, getJob, jobCount, Job } from "./jobs.js";
import { enqueuePaintRender, enqueueVoxRender, outputFile, queueDepth, sweepOutput } from "./render.js";
import { isKnownTemplate, TEMPLATE_NAMES } from "./templates.js";
import { validatePaintParams, validateVoxParams } from "./validate.js";

const PORT = parseInt(process.env.PORT || process.env.HF_RENDER_PORT || "8792", 10);
const HOST = process.env.HOST || "127.0.0.1";

function sendJson(res: http.ServerResponse, status: number, body: unknown): void {
  const data = JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Content-Length": Buffer.byteLength(data) });
  res.end(data);
}

function readJsonBody(req: http.IncomingMessage): Promise<unknown> {
  return new Promise((resolve) => {
    let raw = "";
    let tooBig = false;
    req.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > 2_000_000) { // ~2MB — a beat list is at most ten
        // short objects; anything past this is a caller sending something
        // this service was never meant to hold, not a legitimate job.
        tooBig = true;
        req.destroy();
      }
    });
    req.on("end", () => {
      if (tooBig) { resolve(undefined); return; }
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve(undefined);
      }
    });
    req.on("error", () => resolve(undefined));
  });
}

function jobToStatus(job: Job) {
  return {
    status: job.status,
    url: job.url,
    error: job.error,
    durationSeconds: job.durationSeconds,
    progress: job.progress,
  };
}

async function handleRender(req: http.IncomingMessage, res: http.ServerResponse, template: string): Promise<void> {
  if (!isKnownTemplate(template)) {
    sendJson(res, 404, { error: `There is no "${template}" template. This service knows ${TEMPLATE_NAMES.join(", ")}.` });
    return;
  }
  const body = await readJsonBody(req);
  if (body === undefined) {
    sendJson(res, 400, { error: "The request body is not readable JSON." });
    return;
  }

  if (template === "paint-animation") {
    const result = validatePaintParams(body);
    if ("error" in result) { sendJson(res, 400, { error: result.error }); return; }
    const job = createJob(template);
    enqueuePaintRender(job, result.params);
    sendJson(res, 202, { jobId: job.id, status: job.status });
    return;
  }

  const result = validateVoxParams(body);
  if ("error" in result) { sendJson(res, 400, { error: result.error }); return; }
  const job = createJob(template);
  enqueueVoxRender(job, result.params);
  sendJson(res, 202, { jobId: job.id, status: job.status });
}

function handleStatus(res: http.ServerResponse, jobId: string): void {
  const job = getJob(jobId);
  if (!job) {
    sendJson(res, 404, { error: "No record of that job. The service restarted, or the job expired." });
    return;
  }
  sendJson(res, 200, jobToStatus(job));
}

function handleFile(res: http.ServerResponse, jobId: string): void {
  const safeId = jobId.replace(/[^a-zA-Z0-9-]/g, "");
  if (!safeId || safeId !== jobId) {
    sendJson(res, 404, { error: "Not found." });
    return;
  }
  const file = outputFile(safeId);
  if (!fs.existsSync(file)) {
    sendJson(res, 404, { error: "That render is not on disk. It expired, or never finished." });
    return;
  }
  const stat = fs.statSync(file);
  res.writeHead(200, {
    "Content-Type": "video/mp4",
    "Content-Length": stat.size,
    "Cache-Control": "private, max-age=3600",
  });
  fs.createReadStream(file).pipe(res);
}

const server = http.createServer(async (req, res) => {
  try {
    const parsed = url.parse(req.url || "", true);
    const pathname = parsed.pathname || "";
    const parts = pathname.split("/").filter(Boolean);

    if (req.method === "GET" && pathname === "/health") {
      sendJson(res, 200, { ok: true, jobs: jobCount(), queue: queueDepth(), uptimeSeconds: Math.round(process.uptime()) });
      return;
    }

    if (req.method === "POST" && parts[0] === "render" && parts.length === 2) {
      await handleRender(req, res, decodeURIComponent(parts[1]));
      return;
    }

    if (req.method === "GET" && parts[0] === "render" && parts.length === 3 && parts[2] === "status") {
      handleStatus(res, decodeURIComponent(parts[1]));
      return;
    }

    if (req.method === "GET" && parts[0] === "files" && parts.length === 2) {
      const name = decodeURIComponent(parts[1]).replace(/\.mp4$/, "");
      handleFile(res, name);
      return;
    }

    sendJson(res, 404, { error: "No such route." });
  } catch (err) {
    // Nothing here may take the process down over one bad request — every
    // job already in flight would go with it.
    const message = err instanceof Error ? err.message : String(err);
    sendJson(res, 500, { error: `Unhandled error: ${message.slice(0, 300)}` });
  }
});

// A periodic sweep of finished output files, the way `render.ts`'s own
// docstring describes — plus one at boot, so a container that restarted
// mid-backlog does not wait six hours to reclaim disk from the last run.
const SWEEP_INTERVAL_MS = 6 * 60 * 60 * 1000;
sweepOutput();
setInterval(sweepOutput, SWEEP_INTERVAL_MS).unref();

if (process.env.NODE_ENV !== "test") {
  server.listen(PORT, HOST, () => {
    // eslint-disable-next-line no-console
    console.log(`[hf-render-service] listening on ${HOST}:${PORT}`);
  });
}

export { server };
