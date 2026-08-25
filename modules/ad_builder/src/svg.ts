/**
 * SVG composition.
 *
 * All geometry is authored in 1x template space and expressed in a viewBox, so
 * the same document renders correctly at 1x for Google and 2x for Amazon.
 */

import * as fs from 'fs';
import * as path from 'path';
import sharp from 'sharp';
import type { Box, Brand, ColorRef, CopySet, HeroSet, SizeLayout, TextBox } from './types';
import { resolveFont, textPath } from './fonts';
import { baselines, countWords, fitText, xForAlign, type FitResult } from './typeset';

export interface ComposeInput {
  layout: SizeLayout;
  brand: Brand;
  copy: CopySet;
  hero: HeroSet;
  scale: number;
  /** false renders background only — used to sample contrast under text. */
  includeText?: boolean;
  /** Amazon responsive / 414x125 supply their own CTA. */
  noBakedCta?: boolean;
  /** Full-bleed background photo path + overlay strength (0..1). */
  backgroundImage?: string;
  backgroundOverlay?: number;
  /** Flat overlay colour. Absent keeps the graded dark scrim. */
  backgroundOverlayColor?: string;
  /** preserveAspectRatio alignment for the background crop. */
  backgroundPosition?: string;
  assetRoot?: string;
}

export interface ComposeOutput {
  svg: string;
  fits: Record<string, FitResult>;
  /** Ink bounding boxes in 1x space, for safe-area and contrast checks. */
  rects: Record<string, Box>;
  wordCount: number;
  minFontSize: number;
  missingAssets: string[];
}

/** Horizontal padding inside a CTA button, total across both sides. */
export const CTA_PADDING = 12;

const ALIGN_MAP: Record<string, string> = {
  center: 'xMidYMid',
  left: 'xMinYMid',
  right: 'xMaxYMid',
  top: 'xMidYMin',
  bottom: 'xMidYMax',
  'top-left': 'xMinYMin',
  'top-right': 'xMaxYMin',
  'bottom-left': 'xMinYMax',
  'bottom-right': 'xMaxYMax',
};

export function resolveColor(ref: ColorRef | undefined, brand: Brand, fallback = '#111111'): string {
  if (!ref) return fallback;
  if (ref.startsWith('#')) return ref;
  const c = (brand.colors as Record<string, string>)[ref];
  return c ?? fallback;
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function dataUri(file: string): Promise<{ uri: string; w: number; h: number } | null> {
  // An empty reference resolves to the asset root, and reading a directory
  // throws EISDIR rather than returning "missing". Guard both.
  if (!file || !fs.existsSync(file)) return null;
  if (fs.statSync(file).isDirectory()) return null;
  const buf = fs.readFileSync(file);
  const ext = path.extname(file).toLowerCase();
  const mime =
    ext === '.png' ? 'image/png' : ext === '.svg' ? 'image/svg+xml' : 'image/jpeg';
  let w = 0;
  let h = 0;
  try {
    const meta = await sharp(buf).metadata();
    w = meta.width ?? 0;
    h = meta.height ?? 0;
  } catch {
    /* svg without intrinsic size — caller only needs aspect for logos */
  }
  return { uri: `data:${mime};base64,${buf.toString('base64')}`, w, h };
}

/**
 * The nine alignments SVG accepts for a `slice` crop, and nothing else.
 *
 * This value reaches the composer from a saved campaign file, so it is
 * attacker-adjacent in the same way a copy field is: it is interpolated
 * straight into an attribute. An unknown value is dropped for centre rather
 * than passed through -- a malformed preserveAspectRatio makes librsvg ignore
 * the whole attribute, which is a silently different crop.
 */
const BG_POSITIONS = new Set([
  'xMinYMin', 'xMidYMin', 'xMaxYMin',
  'xMinYMid', 'xMidYMid', 'xMaxYMid',
  'xMinYMax', 'xMidYMax', 'xMaxYMax',
]);

export function resolveBgPosition(value: string | undefined): string {
  const v = String(value ?? '').trim();
  return BG_POSITIONS.has(v) ? v : 'xMidYMid';
}

/** A hex colour, or nothing. Same reasoning as resolveBgPosition. */
export function normaliseHex(value: string | undefined): string | null {
  const v = String(value ?? '').trim();
  if (/^#[0-9a-fA-F]{3}$/.test(v) || /^#[0-9a-fA-F]{6}$/.test(v)) return v.toUpperCase();
  return null;
}

/**
 * Which brand ink survives whatever is painted over the photo.
 *
 * Exported because QA has to reach the same answer: measuring the template's
 * ink over a photo reports a contrast failure the render does not have, and
 * measuring light ink under a light wash reports a pass it does not have
 * either.
 */
export function inkOverBackground(
  input: { backgroundOverlayColor?: string; backgroundOverlay?: number },
  brand: Brand,
): 'light' | 'dark' {
  const wash = normaliseHex(input.backgroundOverlayColor);
  if (!wash) return 'light';                      // the dark scrim
  const strength = Math.max(0, Math.min(1, input.backgroundOverlay ?? 0.42));
  // A thin wash barely changes the photo, and a photo is an unknown, so the
  // light ink the scrim path uses stays the safer bet until the wash is
  // actually carrying the background.
  if (strength < 0.45) return 'light';
  return hexIsLight(wash) ? 'dark' : 'light';
}

/** Perceived lightness, the cheap way. Good enough to pick an ink. */
function hexIsLight(hex: string): boolean {
  const h = hex.replace('#', '');
  const f = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const r = parseInt(f.slice(0, 2), 16);
  const g = parseInt(f.slice(2, 4), 16);
  const b = parseInt(f.slice(4, 6), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6;
}

function scrimGradient(id: string, dir: string, from: number, to: number): string {
  const coords =
    dir === 'right'
      ? 'x1="0" y1="0" x2="1" y2="0"'
      : dir === 'left'
        ? 'x1="1" y1="0" x2="0" y2="0"'
        : dir === 'down'
          ? 'x1="0" y1="0" x2="0" y2="1"'
          : 'x1="0" y1="1" x2="0" y2="0"';
  return `<linearGradient id="${id}" ${coords}>
      <stop offset="0" stop-color="#000" stop-opacity="${from}"/>
      <stop offset="1" stop-color="#000" stop-opacity="${to}"/>
    </linearGradient>`;
}

export async function compose(input: ComposeInput): Promise<ComposeOutput> {
  const {
    layout,
    brand,
    copy,
    hero,
    scale,
    includeText = true,
    noBakedCta = false,
    assetRoot = process.cwd(),
  } = input;

  const { w: W, h: H } = layout.canvas;
  const defs: string[] = [];
  const body: string[] = [];
  const fits: Record<string, FitResult> = {};
  const rects: Record<string, Box> = {};
  const missingAssets: string[] = [];
  let minFontSize = Infinity;

  const abs = (p: string) => (path.isAbsolute(p) ? p : path.resolve(assetRoot, p));

  /* ---------------------------------------------------------- background */
  // A full-bleed background photo (Concept C's image option) replaces the flat
  // brand colour. It is always followed by a legibility overlay so headline,
  // support and CTA stay readable over any photo — the whole reason a plain
  // photo behind text usually fails. Overlay strength defaults from the
  // caller's brightness read; 0.42 is a safe mid when unknown.
  const bgImg = input.backgroundImage ? await dataUri(abs(input.backgroundImage)) : null;
  if (bgImg) {
    // Which part of the photo survives the crop. `slice` covers the canvas and
    // cuts the overflow, so on a 300x250 most of a landscape shot is thrown
    // away -- and which part is thrown away is the whole picture as far as the
    // person looking at it is concerned. The concept's own choice wins; the
    // intake's per-size hint is the fallback; centred is what everything built
    // before either existed did.
    const bgPos = resolveBgPosition(input.backgroundPosition ?? (copy as any).__bgPos);
    body.push(
      `<image x="0" y="0" width="${W}" height="${H}" preserveAspectRatio="${bgPos} slice" href="${bgImg.uri}"/>`,
    );
    const strength = Math.max(0, Math.min(1, input.backgroundOverlay ?? 0.42));
    const wash = normaliseHex(input.backgroundOverlayColor);
    if (wash) {
      // A chosen colour is painted FLAT. Grading it would mean the colour that
      // was picked appears nowhere on the canvas at the opacity that was
      // picked, which is a control that lies about what it did.
      body.push(
        `<rect x="0" y="0" width="${W}" height="${H}" fill="${wash}" fill-opacity="${strength.toFixed(2)}"/>`,
      );
    } else {
      // No colour chosen: the legibility scrim this has always drawn --
      // heavier where text sits (left on most layouts), lighter elsewhere, so
      // the photo still reads.
      defs.push(
        `<linearGradient id="bgScrim" x1="0" y1="0" x2="1" y2="0">` +
        `<stop offset="0" stop-color="#0b1220" stop-opacity="${(strength + 0.15).toFixed(2)}"/>` +
        `<stop offset="0.6" stop-color="#0b1220" stop-opacity="${strength.toFixed(2)}"/>` +
        `<stop offset="1" stop-color="#0b1220" stop-opacity="${Math.max(0, strength - 0.25).toFixed(2)}"/>` +
        `</linearGradient>`,
      );
      body.push(`<rect x="0" y="0" width="${W}" height="${H}" fill="url(#bgScrim)"/>`);
    }
  } else {
    body.push(
      `<rect x="0" y="0" width="${W}" height="${H}" fill="${resolveColor(layout.background, brand, '#ffffff')}"/>`,
    );
  }

  /* ---------------------------------------------------------------- hero */
  if (layout.hero && !input.backgroundImage) {
    const hb = layout.hero;
    const src = hero[hb.orientation] ?? hero.landscape ?? hero.square ?? hero.vertical;
    const img = src ? await dataUri(abs(src)) : null;
    if (!img) {
      if (src) missingAssets.push(src);
      body.push(
        `<rect x="${hb.x}" y="${hb.y}" width="${hb.w}" height="${hb.h}" fill="${resolveColor('secondary', brand, '#cccccc')}"/>`,
      );
    } else {
      const clip = `heroClip`;
      defs.push(
        `<clipPath id="${clip}"><rect x="${hb.x}" y="${hb.y}" width="${hb.w}" height="${hb.h}"/></clipPath>`,
      );
      const par = `${ALIGN_MAP[hb.focal ?? 'center']} slice`;
      body.push(
        `<g clip-path="url(#${clip})"><image x="${hb.x}" y="${hb.y}" width="${hb.w}" height="${hb.h}" preserveAspectRatio="${par}" href="${img.uri}"/></g>`,
      );
      if (hb.scrim) {
        defs.push(scrimGradient('heroScrim', hb.scrim.direction, hb.scrim.from, hb.scrim.to));
        body.push(
          `<rect x="${hb.x}" y="${hb.y}" width="${hb.w}" height="${hb.h}" fill="url(#heroScrim)"/>`,
        );
      }
    }
    rects.hero = { x: hb.x, y: hb.y, w: hb.w, h: hb.h };
  }

  /* -------------------------------------------------------------- panels */
  // Structural panels normally yield to a full-bleed background photo — but a
  // panel marked overBg is PART of the design over photos (e.g. the floating
  // content card in the overlay-card archetype), optionally translucent.
  for (const [i, p] of (layout.panels ?? []).entries()) {
    if (input.backgroundImage && !(p as any).overBg) continue;
    const op = (p as any).opacity;
    body.push(
      `<rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="${p.radius ?? 0}" fill="${resolveColor(p.fill, brand)}"${typeof op === 'number' ? ` fill-opacity="${op}"` : ''} data-panel="${i}"/>`,
    );
  }

  /* ---------------------------------------------------------------- logo */
  if (layout.logo) {
    const lb = layout.logo;
    const useReverse = (copy as any).__useReverseLogo === true;
    // A per-size logo override (e.g. a square variant for square placements)
    // wins over the brand-wide logo choice.
    const file = (copy as any).__logoFile
      ?? (useReverse && brand.logos.reverse ? brand.logos.reverse : brand.logos.primary);
    const img = file ? await dataUri(abs(file)) : null;
    if (!img) {
      missingAssets.push(file || '(no logo supplied)');
    } else {
      // Contain within the box, preserving aspect and anchoring by align.
      const ar = img.w && img.h ? img.w / img.h : 3;
      let dw = lb.w;
      let dh = dw / ar;
      if (dh > lb.h) {
        dh = lb.h;
        dw = dh * ar;
      }
      const dx = xForAlign(dw, lb.x, lb.w, lb.align ?? 'left');
      const dy =
        lb.valign === 'bottom'
          ? lb.y + lb.h - dh
          : lb.valign === 'middle'
            ? lb.y + (lb.h - dh) / 2
            : lb.y;
      body.push(
        `<image x="${dx.toFixed(2)}" y="${dy.toFixed(2)}" width="${dw.toFixed(2)}" height="${dh.toFixed(2)}" href="${img.uri}"/>`,
      );
      rects.logo = { x: dx, y: dy, w: dw, h: dh };
    }
  }

  /* ---------------------------------------------------------------- text */
  const drawText = (role: 'headline' | 'support' | 'offer' | 'trust', spec: TextBox | undefined) => {
    const raw = copy[role];
    if (!spec || !raw) return;
    const text = spec.uppercase ? raw.toUpperCase() : raw;
    const weight = spec.weight ?? (role === 'headline' || role === 'offer' ? 'bold' : 'regular');
    // A block may name its own family; otherwise it takes the brand's face for
    // its role. The per-block value is validated upstream, so an unavailable
    // name never reaches here as something resolveFont would silently swap.
    const family = spec.font
      ?? (role === 'headline' || role === 'offer' ? brand.fonts.headline : brand.fonts.body);
    const font = resolveFont(family, weight);

    const fit = fitText({
      font,
      text,
      maxWidth: spec.w,
      maxHeight: spec.h,
      maxLines: spec.maxLines,
      sizeRange: spec.size,
      lineHeight: spec.lineHeight,
      tracking: spec.letterSpacing,
    });
    fits[role] = fit;
    minFontSize = Math.min(minFontSize, fit.fontSize);

    const ys = baselines(font, fit, spec.y, spec.h, spec.valign ?? 'top');
    let fill = (role === 'headline' && (brand.colors as any).headlineInk)
      ? resolveColor('headlineInk', brand)
      : resolveColor(spec.color, brand, '#111111');
    // Over a full-bleed background photo, text must survive the overlay. Under
    // the default dark scrim that means light ink; under a chosen wash it
    // means whichever of light/dark the wash can carry, because a white
    // overlay with white text on it is the same unreadable ad in the other
    // direction. QA still measures the real contrast either way.
    if (input.backgroundImage && role !== 'offer' && !(spec as any).keepColorOnBg) {
      fill = resolveColor(inkOverBackground(input, brand), brand, '#ffffff');
    }
    const inkW = fit.width;
    const inkX = xForAlign(inkW, spec.x, spec.w, spec.align);
    rects[role] = { x: inkX, y: spec.y, w: inkW, h: fit.height };

    if (!includeText) return;

    // Emit one <path> per line rather than concatenating every line into a
    // single path. librsvg truncates extremely long path `d` strings, which
    // silently dropped multi-line headlines (only the first line rendered).
    // One path per line keeps each `d` well within safe limits.
    fit.lines.forEach((line, i) => {
      const lw = fit.lines.length === 1 ? inkW : undefined;
      const width =
        lw ?? font.getAdvanceWidth(line, fit.fontSize, { kerning: true }) +
          Math.max(0, line.length - 1) * (spec.letterSpacing ?? 0);
      const x = xForAlign(width, spec.x, spec.w, spec.align);
      const d = textPath(font, line, x, ys[i], fit.fontSize, spec.letterSpacing ?? 0);
      if (d) body.push(`<path d="${d}" fill="${fill}" data-role="${role}"/>`);
    });
  };

  drawText('headline', layout.headline);
  drawText('support', layout.support);
  drawText('offer', layout.offer);
  drawText('trust', layout.trust);

  /* ----------------------------------------------------------------- cta */
  if (layout.cta && copy.cta && !noBakedCta) {
    const cb = layout.cta;
    const label = cb.uppercase === false ? copy.cta : copy.cta.toUpperCase();
    const font = resolveFont(cb.font ?? brand.fonts.body, cb.weight ?? 'bold');
    const fit = fitText({
      font,
      text: label,
      maxWidth: cb.w - CTA_PADDING,
      maxHeight: cb.h,
      maxLines: 1,
      sizeRange: cb.size,
      tracking: cb.letterSpacing,
    });
    fits.cta = fit;
    minFontSize = Math.min(minFontSize, fit.fontSize);
    rects.cta = { x: cb.x, y: cb.y, w: cb.w, h: cb.h };

    body.push(
      `<rect x="${cb.x}" y="${cb.y}" width="${cb.w}" height="${cb.h}" rx="${cb.radius ?? 4}" fill="${(brand.colors as any).accent && cb.bg === undefined ? resolveColor('accent', brand, '#ffc400') : resolveColor(cb.bg ?? 'accent', brand, '#ffc400')}" data-role="cta-bg"/>`,
    );
    if (includeText) {
      const ys = baselines(font, fit, cb.y, cb.h, 'middle');
      const x = xForAlign(fit.width, cb.x, cb.w, cb.align ?? 'center');
      body.push(
        `<path d="${textPath(font, fit.lines[0] ?? '', x, ys[0], fit.fontSize, cb.letterSpacing ?? 0)}" fill="${(brand.colors as any).ctaText ? resolveColor('ctaText', brand) : resolveColor(cb.color ?? 'dark', brand, '#111111')}" data-role="cta"/>`,
      );
    }
  }

  // Count only copy this layout actually renders. A concept's default `offer`
  // should not inflate the 728x90 budget when the leaderboard has no offer box.
  const wordCount = countWords(
    layout.headline ? copy.headline : undefined,
    layout.support ? copy.support : undefined,
    layout.offer ? copy.offer : undefined,
    layout.trust ? copy.trust : undefined,
    layout.cta && !noBakedCta ? copy.cta : undefined,
  );

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${W * scale}" height="${H * scale}" viewBox="0 0 ${W} ${H}">
  <defs>${defs.join('')}</defs>
  ${body.join('\n  ')}
  <!-- ${esc(brand.name)} -->
</svg>`;

  return {
    svg,
    fits,
    rects,
    wordCount,
    minFontSize: Number.isFinite(minFontSize) ? minFontSize : 0,
    missingAssets,
  };
}
