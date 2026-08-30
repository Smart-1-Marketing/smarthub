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
import { compose, resolveColor, reverseLogoOnBackdrop } from './svg';
import { rasterise } from './raster';
import { rollUp, runQa } from './qa';
import { applyBlockStyles, type StyleOverrides } from './block-style';
import { getPlatform, getTemplate, renderableSizes } from './registry';
import {
  animationFindings,
  animationSupport,
  encodeAnimation,
  planAnimation,
  repeatedSlides,
  type AnimationPlan,
  type AnimationSpec,
} from './animation';
import type { AnimatedResult } from './types';

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
  const rawLayout = template.sizes[size];
  if (!rawLayout) {
    throw new Error(`Template ${template.id} has no layout for ${size}`);
  }
  const rule = getPlatform(platform).sizes[size];
  if (!rule) {
    throw new Error(`Platform ${platform} does not define ${size}`);
  }
  // Same overrides the preview applied, so what was approved on screen is what
  // ships. Clamped in block-style.ts, not here.
  const layout = applyBlockStyles(rawLayout, concept.styleOverrides);

  const scale = rule.deliverScale;
  const copy = copyForSize(concept, size);
  // Background-only pass: same geometry, no glyphs and no logo. Sampling this
  // tells us the real contrast under each text block, including over
  // photography -- and it is composed FIRST because the logo variant is
  // decided from it. Nothing here depends on that choice (the logo is not
  // drawn), so the ordering is free: the same two composites either way.
  const bgPass = await compose({
    layout,
    brand,
    copy,
    hero: concept.hero,
    scale,
    includeText: false,
    includeLogo: false,
    noBakedCta: rule.noBakedCta,
    backgroundImage: concept.backgroundImage,
    backgroundOverlay: concept.backgroundOverlay,
    backgroundOverlayColor: concept.backgroundOverlayColor,
    backgroundPosition: concept.backgroundPosition,
    backgroundOffset: concept.backgroundOffset,
    backgroundZoom: concept.backgroundZoom,
    assetRoot,
  });
  const backgroundPng = await sharp(Buffer.from(bgPass.svg)).png().toBuffer();

  // Passed through so the composer can pick the reverse logo on dark panels,
  // measured off that pass rather than inferred from the layout -- see
  // reverseLogoOnBackdrop(). The concept-wide flag stays an explicit override,
  // but it is no longer the only thing that can get this right: it is one
  // boolean per concept while what sits under the mark varies per size, so on
  // a mixed template (T04 ships 2 `primary` sizes and 13 `light` ones) no
  // single value of it is correct for every size.
  (copy as any).__useReverseLogo = concept.useReverseLogo
    ?? await reverseLogoOnBackdrop(backgroundPng, layout, scale, brand, concept);

  const composed = await compose({
    layout,
    brand,
    copy,
    hero: concept.hero,
    scale,
    noBakedCta: rule.noBakedCta,
    backgroundImage: concept.backgroundImage,
    backgroundOverlay: concept.backgroundOverlay,
    backgroundOverlayColor: concept.backgroundOverlayColor,
    backgroundPosition: concept.backgroundPosition,
    backgroundOffset: concept.backgroundOffset,
    backgroundZoom: concept.backgroundZoom,
    assetRoot,
  });

  // GIF is dropped here on purpose and not because it is unsupported: a
  // static ad is a still, and `gif` in a placement's format list is what says
  // that placement will ALSO take an animated file. That path is
  // `renderAnimated` below, and it produces a second file beside this one
  // rather than replacing it -- see src/animation.ts.
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
    backgroundOverlay: concept.backgroundOverlay,
    backgroundOverlayColor: concept.backgroundOverlayColor,
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
  const rawLayout = template.sizes[size];
  if (!rawLayout) throw new Error(`${template.id} has no layout for ${size}`);
  // Both render paths apply the concept's overrides here, so the preview and
  // the delivered file cannot disagree about the type.
  const layout = applyBlockStyles(rawLayout, concept.styleOverrides);
  const rule = getPlatform(platform).sizes[size];
  if (!rule) throw new Error(`${platform} does not define ${size}`);

  const scale = rule.deliverScale;
  const copy = copyForSize(concept, size);
  const bgPass = await compose({
    layout, brand, copy, hero: concept.hero, scale,
    includeText: false, includeLogo: false, noBakedCta: rule.noBakedCta, assetRoot,
    backgroundImage: concept.backgroundImage, backgroundOverlay: concept.backgroundOverlay,
    backgroundOverlayColor: concept.backgroundOverlayColor,
    backgroundPosition: concept.backgroundPosition,
    backgroundOffset: concept.backgroundOffset,
    backgroundZoom: concept.backgroundZoom,
  });
  const backgroundPng = await sharp(Buffer.from(bgPass.svg)).png().toBuffer();

  // Same rule as the deliver path, or the preview and the file disagree.
  (copy as any).__useReverseLogo = concept.useReverseLogo
    ?? await reverseLogoOnBackdrop(backgroundPng, layout, scale, brand, concept);

  const composed = await compose({
    layout, brand, copy, hero: concept.hero, scale,
    noBakedCta: rule.noBakedCta, assetRoot,
    backgroundImage: concept.backgroundImage, backgroundOverlay: concept.backgroundOverlay,
    backgroundOverlayColor: concept.backgroundOverlayColor,
    backgroundPosition: concept.backgroundPosition,
    backgroundOffset: concept.backgroundOffset,
    backgroundZoom: concept.backgroundZoom,
  });

  // Preview is always PNG: the editor cares about layout, not the compression
  // ladder, and re-running that ladder on every keystroke would be wasteful.
  const png = await sharp(Buffer.from(composed.svg)).png().toBuffer();
  const raster = { buffer: png, format: 'png' as const, bytes: png.length, overweight: false, attempts: 1 };

  const qa = await runQa({
    layout, brand, copy, rule, composed, raster, backgroundPng, scale,
    backgroundImage: concept.backgroundImage,
    backgroundOverlay: concept.backgroundOverlay,
    backgroundOverlayColor: concept.backgroundOverlayColor,
  });

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
  // renderableSizes() is the one reading of "what do this template and this
  // platform have in common". It was written for exactly this and had no
  // caller, while this line said the same thing a second time -- which is how
  // ALL_SIZES came to list eight of the fifteen sizes now bought.
  const sizes = opts.sizes ?? renderableSizes(opts.concept.layoutFamily, opts.platform);

  const results: RenderResult[] = [];
  for (const size of sizes) {
    results.push(await renderOne({ ...opts, size }));
  }
  return results;
}

/* ---------------------------------------------------------------- animated */

/**
 * The frame builder, shared by the preview and the delivered file.
 *
 * A frame is `compose()` again. That is the whole reason animation was cheap
 * to add here: there is no second renderer, no second idea of where the
 * headline sits, and no way for the moving version of an ad to disagree with
 * the still one about anything except the thing that is moving.
 *
 * Two things it has to get right, and both are ways to ship a broken ad that
 * every screen calls fine:
 *
 *   QA runs PER FRAME. Slide 2 is different copy in the same box: it can
 *   overflow, collide with the button, or lose its contrast where slide 1 fit
 *   perfectly. Checking frame one and calling the animation checked is how a
 *   clipped headline reaches a client on the second slide of a set that
 *   passed. Frame 1's findings are kept whole because frame 1 IS the static
 *   ad; later frames contribute only what they got wrong, named by slide, so
 *   the panel is a list of problems rather than the same eight passes three
 *   times over.
 *
 *   The background pass is composed ONCE, from frame 1. Contrast is sampled
 *   against what is behind the ink, and none of the motions offered changes
 *   what is behind the ink -- the picture, the panel and the overlay are
 *   identical on every frame. Re-composing it per frame would triple the
 *   slowest step of the render to produce the same bytes each time.
 */
async function buildFrames(opts: {
  brand: Brand;
  concept: CreativeConcept;
  platform: string;
  size: SizeKey;
  plan: AnimationPlan;
  assetRoot?: string;
}): Promise<{ pngs: Buffer[]; qa: QaFinding[]; resolvedCopy: CopySet[] }> {
  const { brand, concept, platform, size, plan, assetRoot } = opts;
  const template = getTemplate(concept.layoutFamily);
  const rawLayout = template.sizes[size]!;
  const rule = getPlatform(platform).sizes[size]!;
  const scale = rule.deliverScale;
  const baseCopy = copyForSize(concept, size);

  const pngs: Buffer[] = [];
  const qa: QaFinding[] = [];
  const resolvedCopy: CopySet[] = [];
  const seen = new Set<string>();
  let backgroundPng: Buffer | null = null;

  for (let i = 0; i < plan.frames.length; i++) {
    const frame = plan.frames[i];
    const overrides: StyleOverrides = mergeStyle(concept.styleOverrides, frame.style);
    const layout = applyBlockStyles(rawLayout, overrides);
    const copy = { ...baseCopy, ...(frame.copy ?? {}) } as CopySet;
    const shared = {
      layout, brand, copy, hero: concept.hero, scale, assetRoot,
      noBakedCta: rule.noBakedCta,
      backgroundImage: concept.backgroundImage,
      backgroundOverlay: concept.backgroundOverlay,
      backgroundOverlayColor: concept.backgroundOverlayColor,
      backgroundPosition: concept.backgroundPosition,
      backgroundOffset: concept.backgroundOffset,
      backgroundZoom: concept.backgroundZoom,
    };

    // Composed before the logo is chosen, because the choice is measured off
    // it -- and with includeLogo off, or the mark is compared against a region
    // containing that same mark. The frames share one background, so it is
    // built once and every frame's logo is decided against it.
    if (!backgroundPng) {
      const bgPass = await compose({ ...shared, includeText: false, includeLogo: false });
      backgroundPng = await sharp(Buffer.from(bgPass.svg)).png().toBuffer();
    }

    // The same rule the static render uses, called rather than restated: an
    // animated ad picking a different logo from its own still sibling is
    // exactly the drift this module keeps having to undo, and it would be
    // invisible -- both files render, and only the pair side by side shows it.
    // `shared` holds this same object, so setting the flag here reaches the
    // composite below.
    (copy as any).__useReverseLogo = concept.useReverseLogo
      ?? await reverseLogoOnBackdrop(backgroundPng, layout, scale, brand, concept);
    resolvedCopy.push(stripInternal(copy));

    const composed = await compose(shared);
    const png = await sharp(Buffer.from(composed.svg)).png().toBuffer();
    pngs.push(png);

    const findings = await runQa({
      layout, brand, copy, rule, composed, backgroundPng,
      // The frame itself, not the delivered GIF: these checks are about what
      // is drawn. The animation's own weight is judged once, against the
      // encoded file, in animationFindings().
      raster: { buffer: png, format: 'png', bytes: png.length, overweight: false, attempts: 1 },
      scale,
      backgroundImage: concept.backgroundImage,
      backgroundOverlay: concept.backgroundOverlay,
      backgroundOverlayColor: concept.backgroundOverlayColor,
    });

    if (i === 0) {
      qa.push(...findings);
      for (const f of findings) seen.add(`${f.check}\u0000${f.detail}`);
    } else {
      for (const f of findings) {
        if (f.status === 'pass') continue;
        // Only what THIS frame got wrong that frame 1 did not. A warning the
        // static ad already carries -- the type hierarchy, the logo's share of
        // the canvas -- is true of every frame because none of the motions
        // offered changes it, and repeating it once per frame turns one note
        // into five and buries the one that is about slide 2 in the middle of
        // them.
        const key = `${f.check}\u0000${f.detail}`;
        if (seen.has(key)) continue;
        seen.add(key);
        qa.push({ ...f, check: `${f.check} · ${frame.tag}`, detail: `${frame.label}: ${f.detail}` });
      }
    }
  }

  return { pngs, qa, resolvedCopy };
}

/** Frame overrides sit on top of the concept's, block by block, so animating
 *  the button cannot drop the type size somebody set on the headline. */
function mergeStyle(base: StyleOverrides | undefined, patch: StyleOverrides | undefined): StyleOverrides {
  if (!patch) return base ?? {};
  const out: Record<string, any> = { ...(base ?? {}) };
  for (const [block, style] of Object.entries(patch)) {
    out[block] = { ...(out[block] ?? {}), ...(style as object) };
  }
  return out as StyleOverrides;
}

/** The composer is handed a private flag on the copy object; it must not reach
 *  a comparison of what two slides say. */
function stripInternal(copy: CopySet): CopySet {
  const out: Record<string, unknown> = { ...(copy as object) };
  delete out.__useReverseLogo;
  return out as unknown as CopySet;
}

/**
 * The button's own fill on this size, resolved to a hex.
 *
 * The pulse is computed FROM it rather than replacing it with two colours
 * somebody picked, so a client whose accent is corrected next month gets a
 * pulse in the new colour without anyone reopening the animation.
 */
function ctaFillFor(
  brand: Brand,
  concept: CreativeConcept,
  layoutFamily: string,
  size: SizeKey,
): { hasCta: boolean; fill?: string } {
  const raw = getTemplate(layoutFamily).sizes[size];
  if (!raw?.cta) return { hasCta: false };
  const layout = applyBlockStyles(raw, concept.styleOverrides);
  const bg = layout.cta?.bg;
  if (!bg) return { hasCta: true };
  return { hasCta: true, fill: resolveColor(bg, brand, '#000000') };
}

export interface AnimatePreview {
  gif: Buffer;
  width: number;
  height: number;
  plan: AnimationPlan;
  bytes: number;
  overweight: boolean;
  settings: string;
  qa: QaFinding[];
  status: 'pass' | 'warn' | 'fail';
}

/**
 * The moving preview for the build screen. Nothing reaches disk.
 *
 * Unlike the static preview, this one DOES run the weight ladder. A GIF's
 * weight is the thing most likely to refuse it, it is invisible on screen, and
 * finding out at delivery that a set is over 150 KB means the slides were
 * written for nothing.
 */
export async function renderAnimatedPreview(opts: {
  brand: Brand;
  concept: CreativeConcept;
  platform: string;
  size: SizeKey;
  assetRoot?: string;
  /** Overrides what is stored on the concept, so the panel previews what is
   *  being typed rather than what was last saved. */
  animation?: AnimationSpec;
}): Promise<AnimatePreview> {
  const { brand, concept, platform, size, assetRoot } = opts;
  const spec = opts.animation ?? concept.animation;
  if (!spec) throw new Error('This concept has no animation on it.');

  const support = animationSupport(platform, size);
  if (!support.supported) throw new Error(support.reason ?? `${size} cannot carry an animation.`);

  const template = getTemplate(concept.layoutFamily);
  const rawLayout = template.sizes[size];
  if (!rawLayout) throw new Error(`${template.id} has no layout for ${size}`);
  const rule = getPlatform(platform).sizes[size]!;

  const { hasCta, fill } = ctaFillFor(brand, concept, concept.layoutFamily, size);
  const plan = planAnimation(spec, { hasCta, baseCtaFill: fill, size });
  if (plan.refused) throw new Error(plan.refused);

  const { pngs, qa, resolvedCopy } = await buildFrames({ brand, concept, platform, size, plan, assetRoot });
  const gif = await encodeAnimation({ frames: pngs, plan, maxFileBytes: rule.maxFileBytes });

  const findings = [
    ...qa,
    ...repeatedSlides(resolvedCopy, plan.kind),
    ...animationFindings(plan, gif, rule),
  ];
  return {
    gif: gif.buffer,
    width: rawLayout.canvas.w * rule.deliverScale,
    height: rawLayout.canvas.h * rule.deliverScale,
    plan,
    bytes: gif.bytes,
    overweight: gif.overweight,
    settings: gif.settings,
    qa: findings,
    status: rollUp(findings),
  };
}

/**
 * The delivered animation for one size.
 *
 * Written beside its static sibling with `_animated` on the end rather than
 * over it. The static file is what runs on every placement that does not take
 * a GIF -- which on this Hub's platform list is most of them -- so an
 * animation that replaced it would silently take a set off Amazon.
 */
export async function renderAnimated(opts: RenderOneOptions & { animation?: AnimationSpec }): Promise<AnimatedResult> {
  const { brand, concept, platform, size, outDir, assetRoot } = opts;
  const spec = opts.animation ?? concept.animation;
  if (!spec) throw new Error('This concept has no animation on it.');

  const support = animationSupport(platform, size);
  if (!support.supported) throw new Error(support.reason ?? `${size} cannot carry an animation.`);

  const template = getTemplate(concept.layoutFamily);
  const rawLayout = template.sizes[size];
  if (!rawLayout) throw new Error(`Template ${template.id} has no layout for ${size}`);
  const rule = getPlatform(platform).sizes[size]!;

  const { hasCta, fill } = ctaFillFor(brand, concept, concept.layoutFamily, size);
  const plan = planAnimation(spec, { hasCta, baseCtaFill: fill, size });
  if (plan.refused) throw new Error(plan.refused);

  const { pngs, qa, resolvedCopy } = await buildFrames({ brand, concept, platform, size, plan, assetRoot });
  const gif = await encodeAnimation({ frames: pngs, plan, maxFileBytes: rule.maxFileBytes });

  const dir = path.join(outDir, platform, concept.conceptId);
  fs.mkdirSync(dir, { recursive: true });
  const base = `${brand.domain.replace(/\W+/g, '-')}_${concept.conceptId}_${size}_animated`;
  const file = path.join(dir, `${base}.gif`);
  fs.writeFileSync(file, gif.buffer);

  const findings = [
    ...qa,
    ...repeatedSlides(resolvedCopy, plan.kind),
    ...animationFindings(plan, gif, rule),
  ];

  return {
    platform,
    size,
    conceptId: concept.conceptId,
    file,
    format: 'gif',
    width: rawLayout.canvas.w * rule.deliverScale,
    height: rawLayout.canvas.h * rule.deliverScale,
    bytes: gif.bytes,
    frames: plan.frames.length,
    loop: plan.loop,
    totalMs: plan.totalMs,
    fps: plan.fps,
    kind: plan.kind,
    qa: findings,
    status: rollUp(findings),
  };
}

/**
 * Every size of this concept that can carry an animation, animated.
 *
 * A size the placement will not take is skipped and NAMED, never dropped: a
 * set that came back with five moving ads out of eight, with nothing saying
 * which three or why, is exactly the silence this module was written to avoid.
 */
export async function renderAnimatedPackage(opts: {
  brand: Brand;
  concept: CreativeConcept;
  platform: string;
  outDir: string;
  assetRoot?: string;
  sizes?: SizeKey[];
  animation?: AnimationSpec;
  onProgress?: (done: number) => void;
}): Promise<{ results: AnimatedResult[]; skipped: { size: SizeKey; reason: string }[] }> {
  const template = getTemplate(opts.concept.layoutFamily);
  const wanted = opts.sizes?.length
    ? opts.sizes
    : (Object.keys(template.sizes) as SizeKey[]);

  const results: AnimatedResult[] = [];
  const skipped: { size: SizeKey; reason: string }[] = [];
  for (const size of wanted) {
    if (!template.sizes[size]) {
      skipped.push({ size, reason: `${template.id} has no layout for ${size}.` });
      continue;
    }
    const support = animationSupport(opts.platform, size);
    if (!support.supported) {
      skipped.push({ size, reason: support.reason ?? `${size} cannot carry an animation.` });
      continue;
    }
    try {
      results.push(await renderAnimated({ ...opts, size }));
      opts.onProgress?.(results.length);
    } catch (e: any) {
      skipped.push({ size, reason: e?.message ?? 'could not be animated' });
    }
  }
  return { results, skipped };
}
