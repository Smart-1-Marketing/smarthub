/**
 * tsc compiles .ts and ignores everything else, so the HTML templates this
 * service renders — and the p5.js build they load — never reach dist/ on
 * their own. This copies them, the way `modules/ad_builder`'s own
 * copy-assets.mjs copies its templates and config.
 *
 * p5.min.js is copied from node_modules rather than committed to the repo
 * or fetched from a CDN at render time: a template that reached out to a
 * CDN would make every render depend on outbound network reaching a third
 * party, which this self-hosted service has no business doing for a static
 * library it already declares as a dependency.
 */
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const templatesSrc = join(root, "render_templates");
const templatesDest = join(root, "dist", "render_templates");
mkdirSync(templatesDest, { recursive: true });
cpSync(templatesSrc, templatesDest, { recursive: true });
console.log("copied render_templates -> dist/render_templates");

const p5Src = join(root, "node_modules", "p5", "lib", "p5.min.js");
const p5Dest = join(templatesDest, "p5.min.js");
if (existsSync(p5Src)) {
  cpSync(p5Src, p5Dest);
  console.log("copied p5.min.js -> dist/render_templates/p5.min.js");
} else {
  console.warn("p5 is not installed — templates will fail to render until `npm ci` runs.");
}
