/**
 * Real headless Chromium against local fixtures, not a mock of Puppeteer --
 * the whole point of `extractLargestImage()` is what a browser actually
 * sees once a page's own script has run, which a mocked page object cannot
 * stand in for. `render.test.ts` makes the identical argument about the
 * video pipeline; this is the same choice for the same reason, one file
 * over.
 */
import test from "node:test";
import assert from "node:assert/strict";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

process.env.NODE_ENV = "test";

const { extractLargestImage, resolveImageUrl, ALLOWED_HOSTS } = await import("../src/resolve.js");
const { closeBrowser } = await import("../src/capture.js");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(HERE, "fixtures");

test.after(async () => {
  await closeBrowser();
});

test("waits past hydration and picks the larger image over the site's own /static/ asset", async () => {
  const fileUrl = `file://${path.join(FIXTURES, "spa-shell", "index.html")}`;
  const result = await extractLargestImage(fileUrl);
  assert.ok("url" in result, JSON.stringify(result));
  assert.ok((result as { url: string }).url.endsWith("shot.svg"),
    `expected the larger shot.svg, got ${JSON.stringify(result)}`);
});

test("a page with nothing but a /static/ asset reports it found no real image", async () => {
  const fileUrl = `file://${path.join(FIXTURES, "spa-shell-static-only", "index.html")}`;
  const result = await extractLargestImage(fileUrl);
  assert.ok("error" in result, JSON.stringify(result));
  assert.match((result as { error: string }).error, /no real image/);
});

test("a page that never loads at all reports why, rather than throwing", async () => {
  const result = await extractLargestImage("file:///no/such/file/anywhere.html");
  assert.ok("error" in result, JSON.stringify(result));
});

test("the host allowlist refuses anything not Awesome Screenshot, before ever navigating", async () => {
  assert.deepEqual([...ALLOWED_HOSTS].sort(),
    ["awesomescreenshot.com", "www.awesomescreenshot.com"]);

  const started = Date.now();
  const result = await resolveImageUrl("https://evil.example.com/x");
  const elapsedMs = Date.now() - started;

  assert.ok("error" in result, JSON.stringify(result));
  assert.match((result as { error: string }).error, /not on a host this service will navigate to/);
  // A real navigation attempt against an unreachable host would take
  // seconds to time out; refusing by hostname first is what keeps this
  // fast, and a slow result here would mean the allowlist check stopped
  // running before the browser opens a page.
  assert.ok(elapsedMs < 2000, `refusal took ${elapsedMs}ms -- did it try to navigate?`);
});

test("http (not https) is refused even for an allowed host", async () => {
  const result = await resolveImageUrl("http://www.awesomescreenshot.com/image/1");
  assert.ok("error" in result, JSON.stringify(result));
});
