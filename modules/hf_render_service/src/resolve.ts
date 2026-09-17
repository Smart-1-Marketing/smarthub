/**
 * Reading a page that only draws itself after its own JavaScript runs.
 *
 * `hub/qa_tasks.py`'s Yoda attaches an og:image or twitter:image reader to
 * every share link pasted into a QA task, and Awesome Screenshot's share
 * pages carry neither: the fetched HTML is a bare client-rendered shell --
 * charset, viewport, a favicon, and nothing past it -- because the real
 * screenshot is drawn by a script bundle a plain `requests.get()` never
 * runs. No amount of relaxing that reader closes the gap; the tag it is
 * looking for is never server-rendered in the first place. This is the
 * other way to read that same page: actually run it, the way a person
 * opening the link in a browser would, and read back whatever image it
 * ends up showing.
 *
 * **This is not a general URL-fetch proxy, and must never become one.** The
 * browser this drives lives in the same container as the Hub itself, on the
 * loopback interface `server.ts`'s own docstring already reasons is safe
 * because nothing outside the Hub's own Python process can reach this
 * service at all -- and that reasoning holds only for a Node process that
 * cannot be told to navigate wherever a caller likes. `ALLOWED_HOSTS` is the
 * boundary: a caller can ask this to open an Awesome Screenshot share link
 * and nothing else, the same shape `assetUrlIsSafe` holds the Display Ad
 * Builder's asset fetch to one layer over. Widening it to "any https host"
 * turns a narrow, auditable capability into an open relay a QA task's own
 * free-text instructions field could be made to drive.
 */

import { getBrowser } from "./capture.js";

// A `page.evaluate()` callback runs in the browser's own context, not
// Node's, so `document` is real there and absent from this file's `lib`
// (capture.ts's own note explains why: this is a server file in every
// other respect, and pulling in "dom" for the one function that needs it
// would type the rest of the file against browser globals it never has).
declare const document: any;

export const ALLOWED_HOSTS = new Set(["awesomescreenshot.com", "www.awesomescreenshot.com"]);

// A page under this path is the site's own template asset (the favicon, a
// loading spinner) -- never a screenshot somebody made -- the identical
// exclusion `hub/qa_tasks.py`'s own body scan already applies for the same
// reason, on the other half of this same fallback.
const EXCLUDED_PATH = "/static/";

const NAV_TIMEOUT_MS = 15_000;
// The shell answers instantly; the image it goes on to draw does not. This
// is extra time on top of navigation settling, for the bundle to fetch,
// hydrate and paint -- not a page-load timeout in its own right, which is
// why it is allowed to simply time out and let extraction proceed with
// whatever is on the page by then rather than failing the whole request.
const IMAGE_WAIT_MS = 8_000;

export type ResolveResult = { url: string } | { error: string };

function hostAllowed(raw: string): boolean {
  try {
    const u = new URL(raw);
    return u.protocol === "https:" && ALLOWED_HOSTS.has(u.hostname.toLowerCase());
  } catch {
    return false;
  }
}

/**
 * Loads `pageUrl` in the shared headless browser and returns the largest
 * real, loaded image it finds -- by rendered pixel area, since the actual
 * screenshot is invariably the biggest thing such a viewer page draws and a
 * decorative icon or spinner is not. `naturalWidth > 0` is what "actually
 * loaded" means to a browser; an `<img>` whose `src` never resolved reports
 * zero regardless of what markup asked for.
 *
 * Exported separately from `resolveImageUrl()` so a test can drive the real
 * extraction against a local `file://` fixture without needing the host
 * allowlist to admit a test URL -- the allowlist is the security boundary
 * and belongs in its own test, not entangled with whether the DOM-reading
 * logic itself is correct.
 */
export async function extractLargestImage(pageUrl: string): Promise<ResolveResult> {
  const browser = await getBrowser();
  const page = await browser.newPage();
  try {
    await page.setViewport({ width: 1280, height: 900, deviceScaleFactor: 1 });
    await page.goto(pageUrl, { waitUntil: "networkidle0", timeout: NAV_TIMEOUT_MS });

    await page
      .waitForFunction(
        () => Array.from(document.images).some((img: any) => img.complete && img.naturalWidth > 0),
        { timeout: IMAGE_WAIT_MS },
      )
      .catch(() => undefined); // proceed with whatever is on the page either way

    const candidate = await page.evaluate((excludedPath: string) => {
      let best: { src: string; area: number } | null = null;
      for (const img of Array.from(document.images) as any[]) {
        if (!img.complete || img.naturalWidth <= 0) continue;
        if (!img.src || img.src.includes(excludedPath)) continue;
        const area = img.naturalWidth * img.naturalHeight;
        if (!best || area > best.area) best = { src: img.src, area };
      }
      return best;
    }, EXCLUDED_PATH);

    if (!candidate) {
      return { error: "the page loaded but no real image ever appeared on it" };
    }
    return { url: candidate.src };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { error: `could not load the page: ${message.slice(0, 300)}` };
  } finally {
    await page.close().catch(() => undefined);
  }
}

export async function resolveImageUrl(pageUrl: string): Promise<ResolveResult> {
  if (!hostAllowed(pageUrl)) {
    return { error: `${pageUrl} is not on a host this service will navigate to` };
  }
  return extractLargestImage(pageUrl);
}
