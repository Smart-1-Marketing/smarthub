/**
 * Font registry.
 *
 * Every glyph is converted to an SVG <path> before rasterisation. That means
 * output is byte-identical on a laptop and on Render, and we never depend on
 * fontconfig having the brand font installed on the host. It also removes
 * librsvg's text-layout quirks from the equation entirely.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as opentype from 'opentype.js';
import type { Weight } from './types';

/**
 * Resolve node_modules by walking up from this file. `__dirname/../node_modules`
 * is correct when running from src/ but points at dist/node_modules once
 * compiled, where nothing exists — every family then falls back silently.
 */
function findNodeModules(): string {
  let dir = __dirname;
  for (let i = 0; i < 6; i++) {
    const candidate = path.join(dir, 'node_modules');
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return path.resolve(process.cwd(), 'node_modules');
}

const NM = findNodeModules();

export interface FamilySpec {
  family: string;
  files: Partial<Record<Weight, string>>;
}

/**
 * Registry of families available to the renderer. In production this is
 * populated from a fonts manifest; brand fonts get added here once licensed
 * files are vendored into the repo (never uploaded by the customer).
 */
const REGISTRY: FamilySpec[] = [
  {
    family: 'Montserrat',
    files: {
      regular: `${NM}/@fontsource/montserrat/files/montserrat-latin-400-normal.woff`,
      medium: `${NM}/@fontsource/montserrat/files/montserrat-latin-600-normal.woff`,
      bold: `${NM}/@fontsource/montserrat/files/montserrat-latin-700-normal.woff`,
    },
  },
  {
    family: 'Open Sans',
    files: {
      regular: `${NM}/@fontsource/open-sans/files/open-sans-latin-400-normal.woff`,
      medium: `${NM}/@fontsource/open-sans/files/open-sans-latin-600-normal.woff`,
      bold: `${NM}/@fontsource/open-sans/files/open-sans-latin-700-normal.woff`,
    },
  },
  {
    family: 'Poppins',
    files: {
      regular: `${NM}/@fontsource/poppins/files/poppins-latin-400-normal.woff`,
      medium: `${NM}/@fontsource/poppins/files/poppins-latin-500-normal.woff`,
      bold: `${NM}/@fontsource/poppins/files/poppins-latin-700-normal.woff`,
    },
  },
];

/**
 * Poppins, not DejaVu. opentype.js cannot parse DejaVu's ccmp lookup
 * (substFormat 2) and throws on any getPath call, so using it as the fallback
 * turns a missing-font warning into a crashed render.
 */
const FALLBACK = 'Poppins';
const cache = new Map<string, opentype.Font>();

function loadFile(file: string): opentype.Font {
  const hit = cache.get(file);
  if (hit) return hit;
  const buf = fs.readFileSync(file);
  // Copy into a clean ArrayBuffer — Node Buffers are views into a pool.
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  const font = verify(opentype.parse(ab as ArrayBuffer), file);
  cache.set(file, font);
  return font;
}

/**
 * Some fonts parse fine but throw on glyph lookup because opentype.js does not
 * implement every OpenType feature table. Verify once, at load, rather than
 * discovering it halfway through a package.
 */
const verified = new Set<string>();

function verify(font: opentype.Font, file: string): opentype.Font {
  if (verified.has(file)) return font;
  try {
    font.getPath('Ag 1', 0, 0, 12, { kerning: true });
  } catch (e: any) {
    throw new Error(
      `Font at ${file} cannot be rendered by opentype.js (${e?.message ?? e}). ` +
        'Remove it from the registry or substitute another weight.',
    );
  }
  verified.add(file);
  return font;
}

export function listFamilies(): string[] {
  return REGISTRY.map((f) => f.family);
}

/** Resolve a family+weight to a parsed font, falling back predictably. */
export function resolveFont(family: string, weight: Weight = 'regular'): opentype.Font {
  const want = family.trim().toLowerCase();
  const spec =
    REGISTRY.find((f) => f.family.toLowerCase() === want) ??
    REGISTRY.find((f) => f.family === FALLBACK)!;

  const file =
    spec.files[weight] ?? spec.files.bold ?? spec.files.medium ?? spec.files.regular;
  if (!file || !fs.existsSync(file)) {
    const fb = REGISTRY.find((f) => f.family === FALLBACK)!;
    return loadFile(fb.files.regular!);
  }
  return loadFile(file);
}

export function fontIsAvailable(family: string): boolean {
  return REGISTRY.some((f) => f.family.toLowerCase() === family.trim().toLowerCase());
}

/** Advance width of a string in px at a given font size. */
export function measure(font: opentype.Font, text: string, size: number, tracking = 0): number {
  const base = font.getAdvanceWidth(text, size, { kerning: true });
  return base + Math.max(0, text.length - 1) * tracking;
}

export interface Metrics {
  ascender: number;
  descender: number;
  capHeight: number;
}

export function metrics(font: opentype.Font, size: number): Metrics {
  const s = size / font.unitsPerEm;
  const os2 = (font.tables as any).os2;
  const cap = os2?.sCapHeight ?? font.ascender * 0.72;
  return {
    ascender: font.ascender * s,
    descender: Math.abs(font.descender) * s,
    capHeight: cap * s,
  };
}

/**
 * Convert a line of text to SVG path data. `x, y` is the baseline origin.
 * Letter-spacing is applied by walking glyphs manually when tracking != 0.
 */
export function textPath(
  font: opentype.Font,
  text: string,
  x: number,
  y: number,
  size: number,
  tracking = 0,
): string {
  // Build the path one glyph at a time with manual kerning, rather than
  // opentype's whole-string getPath. The string version intermittently emits
  // NaN coordinates on some kerned sequences, and a single NaN makes librsvg
  // abort the whole path — a headline line silently vanishes from the ad.
  // Per-glyph paths are immune, and the guard below drops any individually
  // corrupt glyph instead of losing the line.
  const scale = size / font.unitsPerEm;
  const glyphs = font.stringToGlyphs(text);
  let cursor = x;
  const parts: string[] = [];
  const f = (n: number) => (Math.round(n * 100) / 100).toString();
  for (let i = 0; i < glyphs.length; i++) {
    const g = glyphs[i];
    // Transform the raw outline ourselves (font units are y-up; SVG is
    // y-down). opentype's glyph.getPath() intermittently emits NaN even when
    // every outline point, the cursor, and the scale are finite — bypassing
    // its transform entirely removes the failure mode.
    const cmds = (g.path?.commands ?? []) as any[];
    let d = '';
    let ok = true;
    for (const c of cmds) {
      switch (c.type) {
        case 'M': d += `M${f(cursor + c.x * scale)} ${f(y - c.y * scale)}`; break;
        case 'L': d += `L${f(cursor + c.x * scale)} ${f(y - c.y * scale)}`; break;
        case 'Q': d += `Q${f(cursor + c.x1 * scale)} ${f(y - c.y1 * scale)} ${f(cursor + c.x * scale)} ${f(y - c.y * scale)}`; break;
        case 'C': d += `C${f(cursor + c.x1 * scale)} ${f(y - c.y1 * scale)} ${f(cursor + c.x2 * scale)} ${f(y - c.y2 * scale)} ${f(cursor + c.x * scale)} ${f(y - c.y * scale)}`; break;
        case 'Z': d += 'Z'; break;
        default: break;
      }
      if (d.includes('NaN')) { ok = false; break; }
    }
    if (ok && d) parts.push(d);
    cursor += (g.advanceWidth ?? 0) * scale;
    if (i < glyphs.length - 1) {
      const kern = font.getKerningValue(g, glyphs[i + 1]);
      if (Number.isFinite(kern)) cursor += kern * scale;
    }
    cursor += tracking;
  }
  return parts.join(' ');
}
