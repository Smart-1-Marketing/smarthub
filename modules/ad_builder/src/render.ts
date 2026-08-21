/**
 * The orchestrator.
 *
 *   Template JSON + Brand JSON + Creative JSON + assets
 *      -> SVG -> Sharp -> PNG/JPG -> QA -> RenderResult
 *
 * One approved concept fans out to every size in the package. Copy is looked
 * up per size first and only falls back to `default`, which is what lets the
 * 320x50 carry five words while the 300x600 carries twenty-four.
 */

import * as fs from 'fs';
import * as path from 'path';
import sharp from 'sharp';
import type {
  QaFinding,
  Brand,
  CopySet,
  CreativeConcept,
  RenderResult,
  SizeKey,
} from './types';
import { compose } from './svg';
import { rasterise } from './raster';
import { rollUp, runQa } from './qa';
import { getPlatform, getTemplate } from './registry';

export interface RenderOneOptions {
  brand: Brand;
  concept: CreativeConcept;
  platform: string;
  size: SizeKey;
  outDir: string;
  assetRoot?: string;
  /** Also write the intermediate SVG next to the raster. Useful for debugging. */
  emitSvg?: boolean;
}

export function copyForSize(concept: CreativeConcept, size: SizeKey): CopySet {
  const specific = concept.copy[size];
  const fallback = concept.copy.default;
  if (!specific && !fallback) {
    throw new Error(`Concept ${concept.conceptId} has no copy for ${size} and no default`);
  }
  return { ...(fallback ?? {}), ...(specific ?? {}) } as CopySet;
}

export async function renderOne(opts: RenderOneOptions): Promise<RenderResult> {
  const { brand, concept, platform, size, outDir, assetRoot, emitSvg } = opts;

  const template = getTemplate(concept.layoutFamily);
  const layout = template.sizes[size];
  if (!layout) {
    throw new Error(`Template ${template.id} has no layout for ${size}`);
  }
  const rule = getPlatform(platform).sizes[size];
  if (!rule) {
    throw new Error(`Platform ${platform} does not define ${size}`);
  }

  const scale = rule.deliverScale;
  const copy = copyForSize(concept, size);
  // Passed through so the composer can pick the reverse logo on dark panels.
  (copy as any).__useReverseLogo = concept.useReverseLogo ?? layout.background === 'dark';

  const composed = await compose({
    layout,
    brand,
    copy,
    hero: concept.hero,
    scale,
    noBakedCta: rule.noBakedCta,
    backgroundImage: concept.backgroundImage,
    backgroundOverlay: concept.backgroundOverlay,
    assetRoot,
  });

  // Background-only pass: same geometry, no glyphs. Sampling this tells us the
  // real contrast under each text block, including over photography.
  const bgPass = await compose({
    layout,
    brand,
    copy,
    hero: concept.hero,
    scale,
    includeText: false,
    noBakedCta: rule.noBakedCta,
    backgroundImage: concept.backgroundImage,
    backgroundOverlay: concept.backgroundOverlay,
    assetRoot,
  });
  const backgroundPng = await sharp(Buffer.from(bgPass.svg)).png().toBuffer();

  const raster = await rasterise({
    svg: composed.svg,
    formats: rule.formats.filter((f): f is 'png' | 'jpg' => f !== 'gif'),
    maxFileBytes: rule.maxFileBytes,
  });

  const qa = await runQa({
    layout,
    brand,
    copy,
    rule,
    composed,
    raster,
    backgroundPng,
    scale,
    backgroundImage: concept.backgroundImage,
  });

  const dir = path.join(outDir, platform, concept.conceptId);
  fs.mkdirSync(dir, { recursive: true });
  const base = `${brand.domain.replace(/\W+/g, '-')}_${concept.conceptId}_${size}`;
  const file = path.join(dir, `${base}.${raster.format}`);
  fs.writeFileSync(file, raster.buffer);
  if (emitSvg) fs.writeFileSync(path.join(dir, `${base}.svg`), composed.svg);

  return {
    platform,
    size,
    conceptId: concept.conceptId,
    file,
    format: raster.format,
    width: layout.canvas.w * scale,
    height: layout.canvas.h * scale,
    bytes: raster.bytes,
    wordCount: composed.wordCount,
    qa,
    status: rollUp(qa),
  };
}

/**
 * Render one creative to a buffer without touching disk. This is what makes a
 * build screen possible: an editor needs a new preview on every keystroke, and
 * writing a file per keystroke is both slow and messy.
 */
export async function renderPreview(opts: {
  brand: Brand;
  concept: CreativeConcept;
  platform: string;
  size: SizeKey;
  assetRoot?: string;
}): Promise<{ png: Buffer; width: number; height: number; qa: QaFinding[]; status: 'pass' | 'warn' | 'fail'; wordCount: number }> {
  const { brand, concept, platform, size, assetRoot } = opts;
  const template = getTemplate(concept.layoutFamily);
  const layout = template.sizes[size];
  if (!layout) throw new Error(`${template.id} has no layout for ${size}`);
  const rule = getPlatform(platform).sizes[size];
  if (!rule) throw new Error(`${platform} does not define ${size}`);

  const scale = rule.deliverScale;
  const copy = copyForSize(concept, size);
  (copy as any).__useReverseLogo = concept.useReverseLogo ?? layout.background === 'dark';

  const composed = await compose({
    layout, brand, copy, hero: concept.hero, scale,
    noBakedCta: rule.noBakedCta, assetRoot,
    backgroundImage: concept.backgroundImage, backgroundOverlay: concept.backgroundOverlay,
  });
  const bgPass = await compose({
    layout, brand, copy, hero: concept.hero, scale,
    includeText: false, noBakedCta: rule.noBakedCta, assetRoot,
    backgroundImage: concept.backgroundImage, backgroundOverlay: concept.backgroundOverlay,
  });
  const backgroundPng = await sharp(Buffer.from(bgPass.svg)).png().toBuffer();

  // Preview is always PNG: the editor cares about layout, not the compression
  // ladder, and re-running that ladder on every keystroke would be wasteful.
  const png = await sharp(Buffer.from(composed.svg)).png().toBuffer();
  const raster = { buffer: png, format: 'png' as const, bytes: png.length, overweight: false, attempts: 1 };

  const qa = await runQa({ layout, brand, copy, rule, composed, raster, backgroundPng, scale, backgroundImage: concept.backgroundImage });

  return {
    png,
    width: layout.canvas.w * scale,
    height: layout.canvas.h * scale,
    qa,
    status: rollUp(qa),
    wordCount: composed.wordCount,
  };
}

export interface RenderPackageOptions {
  brand: Brand;
  concept: CreativeConcept;
  platform: string;
  outDir: string;
  assetRoot?: string;
  sizes?: SizeKey[];
  emitSvg?: boolean;
}

/** Render every size a template and platform have in common. */
export async function renderPackage(opts: RenderPackageOptions): Promise<RenderResult[]> {
  const template = getTemplate(opts.concept.layoutFamily);
  const platform = getPlatform(opts.platform);
  const sizes =
    opts.sizes ??
    (Object.keys(template.sizes) as SizeKey[]).filter((s) => platform.sizes[s]);

  const results: RenderResult[] = [];
  for (const size of sizes) {
    results.push(await renderOne({ ...opts, size }));
  }
  return results;
}
