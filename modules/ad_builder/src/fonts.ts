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
 * Registry of families available to the renderer.
 *
 * Every family is a Google Font, vendored through its `@fontsource` package so
 * the renderer never fetches a face at render time and the output is
 * byte-identical on a laptop and on Render. The three originals (Montserrat,
 * Open Sans, Poppins) are listed first because every campaign saved before
 * the wider set existed names one of them; the rest are the Google families
 * a brand is most likely to actually use, so a client whose site sets Lato
 * or Oswald can be matched rather than approximated.
 *
 * The files are looked up on disk at load rather than written out by hand.
 * Not every family ships every weight -- Lato has no 500 or 600, Bebas Neue
 * and Anton have only a 400 -- and a registry that names a file the package
 * does not carry falls back to Poppins for that weight while the control
 * still shows the family that was asked for. So each weight is the first of
 * the candidate files that exists, and a family with no regular file at all
 * is left out of the list rather than offered and silently swapped.
 *
 * Adding a family is one line here plus `npm install @fontsource/<pkg>`.
 */
const GOOGLE_FAMILIES: Array<[family: string, pkg: string]> = [
  ['Montserrat', 'montserrat'],
  ['Open Sans', 'open-sans'],
  ['Poppins', 'poppins'],
  // Sans
  ['Lato', 'lato'],
  ['Raleway', 'raleway'],
  ['Work Sans', 'work-sans'],
  ['Barlow', 'barlow'],
  ['Manrope', 'manrope'],
  ['Libre Franklin', 'libre-franklin'],
  ['Noto Sans', 'noto-sans'],
  ['Fira Sans', 'fira-sans'],
  ['Cabin', 'cabin'],
  ['Hind', 'hind'],
  ['Arimo', 'arimo'],
  ['PT Sans', 'pt-sans'],
  ['Ubuntu', 'ubuntu'],
  ['Oxygen', 'oxygen'],
  ['Titillium Web', 'titillium-web'],
  ['Exo 2', 'exo-2'],
  ['Kanit', 'kanit'],
  ['Josefin Sans', 'josefin-sans'],
  ['Quicksand', 'quicksand'],
  ['Dosis', 'dosis'],
  // Serif
  ['Playfair Display', 'playfair-display'],
  ['Libre Baskerville', 'libre-baskerville'],
  ['PT Serif', 'pt-serif'],
  ['Noto Serif', 'noto-serif'],
  ['Crimson Text', 'crimson-text'],
  ['EB Garamond', 'eb-garamond'],
  ['Cormorant Garamond', 'cormorant-garamond'],
  // Display
  ['Bebas Neue', 'bebas-neue'],
  ['Anton', 'anton'],
  ['Fjalla One', 'fjalla-one'],
  ['Archivo Black', 'archivo-black'],
  ['Righteous', 'righteous'],
  ['Abril Fatface', 'abril-fatface'],
];

/**
 * Not on the list, and not by oversight: Roboto, Inter, DM Sans, Nunito,
 * Oswald, Rubik, Source Sans 3, Archivo, Lora, Merriweather, Karla, Mulish,
 * Nunito Sans, Roboto Condensed and Roboto Slab. Every weight of each ships
 * a GSUB chained-context lookup (type 6, format 2) that opentype.js does not
 * implement, so `getPath` throws on the first glyph -- the DejaVu failure
 * `FALLBACK` records, fifteen times over. Measured against the installed
 * packages before this list was written, and `familyFromPackage` below
 * probes every file again at load so a package update cannot put one back
 * silently: a family that throws is left off rather than offered and swapped
 * for Poppins mid-render.
 */

/** The @fontsource weights each of our three named weights may be drawn from,
 *  in order of preference. Medium prefers 600 over 500 because at banner sizes
 *  500 is barely distinguishable from regular. */
const WEIGHT_CANDIDATES: Record<Weight, number[]> = {
  regular: [400, 500, 300],
  medium: [600, 500, 700],
  bold: [700, 800, 600, 900],
};

/**
 * Can opentype.js actually draw this file? Parsed and exercised once here,
 * then dropped: keeping a hundred parsed fonts in memory for the sake of a
 * boot-time check is not the trade, and resolveFont() parses and caches the
 * handful a campaign really uses.
 */
function renders(file: string): boolean {
  try {
    const buf = fs.readFileSync(file);
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    opentype.parse(ab as ArrayBuffer).getPath('Ag 1', 0, 0, 12, { kerning: true });
    return true;
  } catch {
    return false;
  }
}

function familyFromPackage(family: string, pkg: string): FamilySpec | null {
  const dir = `${NM}/@fontsource/${pkg}/files`;
  const files: Partial<Record<Weight, string>> = {};
  for (const weight of Object.keys(WEIGHT_CANDIDATES) as Weight[]) {
    for (const w of WEIGHT_CANDIDATES[weight]) {
      const file = `${dir}/${pkg}-latin-${w}-normal.woff`;
      if (fs.existsSync(file) && renders(file)) { files[weight] = file; break; }
    }
  }
  if (!files.regular) return null;
  return { family, files };
}

let registry: FamilySpec[] | null = null;
/** Built on first use rather than at import, so a test that imports this
 *  module for `measure()` does not pay for probing forty packages. */
function families(): FamilySpec[] {
  if (!registry) {
    registry = GOOGLE_FAMILIES
      .map(([family, pkg]) => familyFromPackage(family, pkg))
      .filter((f): f is FamilySpec => f !== null);
  }
  return registry;
}

/** Every Google family this build knows how to load, whether or not its
 *  package is installed -- so a diagnostics page can say which are missing
 *  rather than the list silently being shorter. */
export function knownGoogleFamilies(): string[] {
  return GOOGLE_FAMILIES.map(([family]) => family);
}

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
  return families().map((f) => f.family);
}

/** Resolve a family+weight to a parsed font, falling back predictably. */
export function resolveFont(family: string, weight: Weight = 'regular'): opentype.Font {
  const want = family.trim().toLowerCase();
  const spec =
    families().find((f) => f.family.toLowerCase() === want) ??
    families().find((f) => f.family === FALLBACK)!;

  const file =
    spec.files[weight] ?? spec.files.bold ?? spec.files.medium ?? spec.files.regular;
  if (!file || !fs.existsSync(file)) {
    const fb = families().find((f) => f.family === FALLBACK)!;
    return loadFile(fb.files.regular!);
  }
  return loadFile(file);
}

export function fontIsAvailable(family: string): boolean {
  return families().some((f) => f.family.toLowerCase() === family.trim().toLowerCase());
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
