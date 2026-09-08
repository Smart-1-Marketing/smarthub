/**
 * HTTP-level behaviour: the wire contract's shape, refusals, and 404s.
 * Deliberately does not submit a *valid* render here — that starts a real
 * Puppeteer capture, which is `render.test.ts`'s job and would otherwise
 * make this file slow for no reason a routing test needs.
 *
 * `NODE_ENV=test` keeps `server.ts` from calling `.listen()` on import, so
 * this file binds it to an ephemeral port itself.
 */
import test from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

process.env.NODE_ENV = "test";
process.env.HF_OUTPUT_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "hf-server-test-"));

const { server } = await import("../src/server.js");

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
});

test("GET /health answers 200 with ok:true", async () => {
  const res = await fetch(`${base}/health`);
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.ok, true);
});

test("an unknown template is refused with 404, not 500", async () => {
  const res = await fetch(`${base}/render/not-a-real-template`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  assert.equal(res.status, 404);
  const body = await res.json();
  assert.match(body.error, /not-a-real-template/);
});

test("a paint-animation request with nothing to paint is refused with 400", async () => {
  const res = await fetch(`${base}/render/paint-animation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: "", imageUrl: "" }),
  });
  assert.equal(res.status, 400);
});

test("a vox-explainer request with too few beats is refused with 400", async () => {
  const res = await fetch(`${base}/render/vox-explainer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ beats: [{ headline: "Only one", seconds: 8 }] }),
  });
  assert.equal(res.status, 400);
});

test("a request body that is not JSON is refused with 400, not a crash", async () => {
  const res = await fetch(`${base}/render/paint-animation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{not json",
  });
  assert.equal(res.status, 400);
});

test("polling a job id that was never issued is 404", async () => {
  const res = await fetch(`${base}/render/never-issued/status`);
  assert.equal(res.status, 404);
});

test("a file that was never rendered is 404", async () => {
  const res = await fetch(`${base}/files/never-rendered.mp4`);
  assert.equal(res.status, 404);
});

test("a file path is not readable as a directory traversal", async () => {
  const res = await fetch(`${base}/files/${encodeURIComponent("../../etc/passwd")}`);
  // Either the route pattern itself refuses it (a literal ".." segment does
  // not match /files/<one-segment>) or the sanitised id check does — either
  // way this must never resolve outside the output directory.
  assert.notEqual(res.status, 200);
});

test("GET is not accepted on /render/<template> — only POST submits a job", async () => {
  const res = await fetch(`${base}/render/paint-animation`);
  assert.equal(res.status, 404); // no route matches GET here, which is
  // correct: this path only exists as a POST target.
});
