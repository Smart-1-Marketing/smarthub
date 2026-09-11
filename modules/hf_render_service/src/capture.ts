/**
 * Puppeteer's half of the pipeline: load a template, drive its clock frame
 * by frame, screenshot each one. Nothing here waits on real time — capture
 * is driven entirely by `window.__setFrame(t)`, so a slow container and a
 * fast one produce byte-identical frame sequences for the same params. That
 * determinism is the whole argument `hub/hyperframes.py` makes for a
 * pre-authored template over a model writing fresh HTML per request.
 */

import puppeteer, { Browser } from "puppeteer";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

// This file drives a browser page via `page.evaluate()`, whose callback runs
// in the page's own context, not Node's — `window` there is real, but this
// module is compiled without the DOM lib (it is a Node/server file in every
// other respect). Declared rather than pulling in "dom" for the one file
// that needs it.
declare const window: any;

const HERE = path.dirname(fileURLToPath(import.meta.url));
// dist/src/capture.js -> dist/render_templates (copied there at build time
// by scripts/copy-assets.mjs, alongside a build-time copy of p5.min.js so
// nothing in this service reaches a CDN at render time).
//
// Named render_templates rather than templates deliberately:
// hub/integrity.py's orphan-template check walks every modules/*/templates
// directory looking for Jinja pages nothing renders, and paint.html/vox.html
// are not Jinja — they are navigated to directly by Puppeteer
// (`page.goto('file://...')`) and nothing here ever calls Flask's
// render_template() on them. The exact collision modules/ad_builder avoids
// by nesting its own layout JSON under src/templates rather than a bare
// templates/ at the module root.
export const TEMPLATES_DIR = path.join(HERE, "..", "render_templates");

// `npm run build`'s copy-assets step is what normally puts p5.min.js next
// to the templates — but `npm run dev` and `npm test` both run tsx
// directly against src/*.ts, with no build step in between, and land on
// this same TEMPLATES_DIR (the source templates/ directory this time,
// never dist/). Without this, both modes fail every render with
// "resizeCanvas is not defined": the template loads, p5 never did,
// because there is nothing at templates/p5.min.js to load it from. Copied
// from node_modules on first use rather than committed to the repo — the
// reason scripts/copy-assets.mjs gives is the same one here.
let p5Ensured = false;
function ensureP5Vendored(): void {
  if (p5Ensured) return;
  const dest = path.join(TEMPLATES_DIR, "p5.min.js");
  if (!fs.existsSync(dest)) {
    // Two candidate depths: HERE is src/ when running under tsx (one level
    // above the package root) and dist/src/ when running the compiled
    // build (two levels above it).
    const candidates = [
      path.join(HERE, "..", "node_modules", "p5", "lib", "p5.min.js"),
      path.join(HERE, "..", "..", "node_modules", "p5", "lib", "p5.min.js"),
    ];
    const src = candidates.find((p) => fs.existsSync(p));
    if (src) {
      fs.mkdirSync(TEMPLATES_DIR, { recursive: true });
      fs.copyFileSync(src, dest);
    }
  }
  p5Ensured = true;
}

export interface CaptureOptions {
  templateFile: "paint.html" | "vox.html";
  params: object;
  width: number;
  height: number;
  fps: number;
  totalSeconds: number;
  framesDir: string;
  onProgress?: (fraction: number) => void;
}

let sharedBrowser: Browser | null = null;

async function browser(): Promise<Browser> {
  // One Chromium instance for the process's lifetime rather than one per
  // job: launching is the most expensive single step (roughly a second),
  // and jobs are already serialised by the render queue, so nothing here
  // needs per-job process isolation to stay correct.
  if (sharedBrowser && sharedBrowser.connected) return sharedBrowser;
  sharedBrowser = await puppeteer.launch({
    headless: true,
    // Unset everywhere today — Puppeteer resolves its own downloaded Chrome,
    // the path the Dockerfile's PUPPETEER_CACHE_DIR pins so that download and
    // this launch agree regardless of what $HOME resolves to at either
    // point. Kept as an explicit, named override rather than assumed: the
    // day something here needs to launch a different binary (the apt
    // `chromium` package already sitting in the image for its shared
    // libraries, say), it is one environment variable rather than a code
    // change.
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: [
      "--no-sandbox", // the container has no setuid sandbox helper
      "--disable-setuid-sandbox",
      "--disable-gpu",
      "--disable-dev-shm-usage", // /dev/shm is small in a container; write
      // Chromium's shared memory to disk instead of crashing on a big page.
    ],
  });
  return sharedBrowser;
}

export async function closeBrowser(): Promise<void> {
  if (sharedBrowser) {
    await sharedBrowser.close().catch(() => undefined);
    sharedBrowser = null;
  }
}

export async function captureFrames(opts: CaptureOptions): Promise<number> {
  fs.mkdirSync(opts.framesDir, { recursive: true });
  ensureP5Vendored();

  const b = await browser();
  const page = await b.newPage();
  try {
    await page.setViewport({ width: opts.width, height: opts.height, deviceScaleFactor: 1 });

    const templatePath = path.join(TEMPLATES_DIR, opts.templateFile);
    await page.goto(`file://${templatePath}`, { waitUntil: "networkidle0", timeout: 30000 });
    await page.waitForFunction("window.__ready === true", { timeout: 15000 });

    const params = { ...opts.params, width: opts.width, height: opts.height };
    // __init may fetch a remote image; give it real headroom rather than
    // the page's own default navigation timeout.
    await page.evaluate((p) => window.__init(p), params);

    const totalFrames = Math.max(1, Math.round(opts.fps * opts.totalSeconds));
    for (let i = 0; i < totalFrames; i++) {
      const t = totalFrames === 1 ? 0 : i / (totalFrames - 1);
      await page.evaluate((tt) => window.__setFrame(tt), t);
      const file = path.join(opts.framesDir, `frame_${String(i).padStart(5, "0")}.png`);
      await page.screenshot({ path: file as `${string}.png` });
      if (opts.onProgress) opts.onProgress((i + 1) / totalFrames);
    }
    return totalFrames;
  } finally {
    await page.close().catch(() => undefined);
  }
}
