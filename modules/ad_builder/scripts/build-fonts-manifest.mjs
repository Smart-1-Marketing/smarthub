#!/usr/bin/env node
// Walks every @fontsource family this renderer knows and writes the ones
// opentype.js can actually draw to src/fonts.manifest.json. Loading the
// manifest at boot is ~3ms; probing every file the way fonts.ts used to
// takes ~350ms because opentype.js has to parse each one. The probe stays
// as the fallback in fonts.ts, so a missing or stale manifest is at worst
// the old startup cost, never a wrong answer.
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const t0 = Date.now();

// tsx is the same runner scripts/gen-layouts.ts uses; it lets us import the
// TypeScript source directly so the family list stays in one place.
const { probeFamilies } = await import('tsx/esm/api').then((m) => m.tsImport(path.join(here, '..', 'src', 'fonts.ts'), import.meta.url));

const manifest = probeFamilies();
const out = path.join(here, '..', 'src', 'fonts.manifest.json');
writeFileSync(out, JSON.stringify(manifest, null, 2) + '\n');
const dt = Date.now() - t0;
console.log(`Wrote ${manifest.length} families to src/fonts.manifest.json in ${dt}ms`);
