/**
 * tsc compiles .ts and ignores everything else, so the JSON that the registry
 * loads at runtime (templates, platform rules, schemas) never reaches dist/.
 * The compiled build then crashes on first read. This copies them.
 */
import { cpSync, existsSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const pairs = [
  ['src/templates', 'dist/src/templates'],
  ['src/config', 'dist/src/config'],
  ['src/examples', 'dist/src/examples'],
  ['schemas', 'dist/schemas'],
];

for (const [from, to] of pairs) {
  const src = join(root, from);
  if (!existsSync(src)) continue;
  mkdirSync(join(root, dirname(to)), { recursive: true });
  cpSync(src, join(root, to), { recursive: true });
  console.log(`copied ${from} -> ${to}`);
}

// Build stamp: a timestamp written at build time, surfaced by /healthz,
// /version, and the boot log. This is how we tell, with certainty, whether the
// code running on Render matches what we shipped — ending the "is the new
// version actually deployed?" guesswork.
import { writeFileSync as _wf } from 'node:fs';
const stamp = {
  builtAt: new Date().toISOString(),
  node: process.version,
};
try {
  _wf(new URL('../dist/src/build-stamp.json', import.meta.url), JSON.stringify(stamp));
  console.log('build stamp:', stamp.builtAt);
} catch (e) { console.warn('could not write build stamp:', e.message); }
