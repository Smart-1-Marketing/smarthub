/**
 * Automated QA. Runs before a proof is ever shown to a customer.
 *
 * Findings are machine-readable so the copy-shortening step can act on them
 * without a human in the loop: a `fix` of { action: 'shorten', role, maxWords }
 * goes straight back to the OpenAI copywriter for that one size.
 */

import type {
  BoxRole,
  Box,
  Brand,
  CopySet,
  PlatformSizeRule,
  QaFinding,
  SizeLayout,
} from './types';
import type { ComposeOutput } from './svg';
import { resolveColor } from './svg';
import sharp from 'sharp';
import { contrastRatio, hexLuminance, regionLuminance, type RasterResult } from './raster';

export interface QaInput {
  layout: SizeLayout;
  brand: Brand;
  copy: CopySet;
  rule: PlatformSizeRule;
  composed: ComposeOutput;
  raster: RasterResult;
  /** Background-only render at delivery scale, for contrast sampling. */
  backgroundPng: Buffer;
  scale: number;
  /** Present when the concept uses a full-bleed background image, so contrast
   *  is measured against the light text the composer actually used. */
  backgroundImage?: string;
}

const TEXT_ROLES = ['headline', 'support', 'offer', 'trust'] as const;

/**
 * Mean luminance of a logo's visible (non-transparent) pixels. This is what
 * the eye compares against the backdrop — transparent padding must not count,
 * or a white mark on a transparent canvas would average out to "grey" and
 * sneak past the check.
 */
async function logoInkLuminance(file: string): Promise<number | null> {
  try {
    const { data, info } = await sharp(file).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    let sum = 0;
    let weight = 0;
    for (let i = 0; i < data.length; i += info.channels) {
      const a = data[i + 3] / 255;
      if (a < 0.1) continue;
      // Rec. 709 luma on linearised-ish sRGB is close enough for a warning check.
      const lum = (0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2]) / 255;
      sum += lum * a;
      weight += a;
    }
    return weight > 0 ? sum / weight : null;
  } catch {
    return null;
  }
}

function safeRegion(layout: SizeLayout): Box {
  if (layout.safeBox) return layout.safeBox;
  const s = layout.safe;
  return { x: s, y: s, w: layout.canvas.w - s * 2, h: layout.canvas.h - s * 2 };
}

function within(box: Box, region: Box): boolean {
  return (
    box.x >= region.x - 0.5 &&
    box.y >= region.y - 0.5 &&
    box.x + box.w <= region.x + region.w + 0.5 &&
    box.y + box.h <= region.y + region.h + 0.5
  );
}

export async function runQa(input: QaInput): Promise<QaFinding[]> {
  const { layout, brand, copy, rule, composed, raster, backgroundPng, scale } = input;
  const findings: QaFinding[] = [];
  const pass = (check: string, detail: string) =>
    findings.push({ check, status: 'pass', detail });
  const warn = (check: string, detail: string, fix?: QaFinding['fix']) =>
    findings.push({ check, status: 'warn', detail, fix });
  const fail = (check: string, detail: string, fix?: QaFinding['fix']) =>
    findings.push({ check, status: 'fail', detail, fix });

  /* ---------------------------------------------------------- safe zones */
  // Meta story/reel formats reserve the top 14% and bottom 35% for platform
  // UI. Any element overlapping those zones gets covered by native controls.
  if (layout.safeZone) {
    const sz = layout.safeZone;
    const topLimit = sz.top ?? 0;
    const bottomLimit = layout.canvas.h - (sz.bottom ?? 0);
    const intruders: string[] = [];
    for (const role of ['logo', 'headline', 'support', 'offer', 'cta'] as const) {
      const box = composed.rects[role];
      if (!box) continue;
      if (box.y < topLimit || box.y + box.h > bottomLimit) intruders.push(role);
    }
    if (intruders.length) {
      warn('safe-zone', `${intruders.join(', ')} extend into the platform UI exclusion zone.`);
    } else {
      pass('safe-zone', 'all elements sit within the platform-safe core');
    }
  }

  /* ------------------------------------------------------- logo contrast */
  // A white logo on a white panel is invisible, and the text-contrast check
  // never sees it because logos are images, not glyphs. Measure the logo's
  // own ink against what actually sits behind it in the render.
  const logoBox = composed.rects.logo;
  if (logoBox && input.brand.logos?.primary) {
    const useReverse = (copy as any).__useReverseLogo === true;
    const logoFile = useReverse && input.brand.logos.reverse ? input.brand.logos.reverse : input.brand.logos.primary;
    const ink = await logoInkLuminance(logoFile);
    if (ink !== null) {
      const behind = await regionLuminance(backgroundPng, {
        left: Math.max(0, Math.round(logoBox.x * scale)),
        top: Math.max(0, Math.round(logoBox.y * scale)),
        width: Math.max(1, Math.round(logoBox.w * scale)),
        height: Math.max(1, Math.round(logoBox.h * scale)),
      });
      const ratio = contrastRatio(ink, behind);
      if (ratio < 1.7) {
        // Suggest the opposite of what's there: a light logo needs the
        // darker/full-colour version and vice versa.
        const suggestion = ink > 0.5 ? 'full-colour (darker)' : 'white';
        warn('logo-contrast', `the logo is nearly invisible against its background (${ratio.toFixed(1)}:1). Try the ${suggestion} logo on this size.`);
      } else {
        pass('logo-contrast', `logo reads clearly against its background (${ratio.toFixed(1)}:1)`);
      }
    }
  }

  /* ---------------------------------------------------------- dimensions */
  const expW = rule.w * rule.deliverScale;
  const expH = rule.h * rule.deliverScale;
  const actW = layout.canvas.w * scale;
  const actH = layout.canvas.h * scale;
  if (actW === expW && actH === expH) {
    pass('dimensions', `${actW}x${actH}`);
  } else {
    fail('dimensions', `rendered ${actW}x${actH}, platform expects ${expW}x${expH}`);
  }

  /* -------------------------------------------------------- file weight */
  const kb = (n: number) => `${(n / 1024).toFixed(1)} KB`;
  if (!raster.overweight) {
    pass('file-weight', `${kb(raster.bytes)} of ${kb(rule.maxFileBytes)} (${raster.format})`);
  } else {
    fail(
      'file-weight',
      `${kb(raster.bytes)} exceeds the ${kb(rule.maxFileBytes)} limit after ${raster.attempts} compression attempts — simplify the hero image or switch to a flat-colour treatment`,
    );
  }

  /* ------------------------------------------------------ missing assets */
  if (composed.missingAssets.length) {
    fail('assets', `missing files: ${composed.missingAssets.join(', ')}`);
  }

  /* ----------------------------------------------------------- overflow */
  let anyOverflow = false;
  for (const role of TEXT_ROLES) {
    const fit = composed.fits[role];
    if (!fit) continue;
    if (fit.overflow) {
      anyOverflow = true;
      const words = (copy[role] ?? '').split(/\s+/).filter(Boolean).length;
      fail(
        `overflow:${role}`,
        `"${copy[role]}" does not fit (${fit.lines.length} lines at ${fit.fontSize}px, limit ${layout[role]?.maxLines})`,
        { action: 'shorten', role, maxWords: Math.max(2, Math.floor(words * 0.7)) },
      );
    }
  }
  if (composed.fits.cta?.overflow) {
    anyOverflow = true;
    fail('overflow:cta', `CTA "${copy.cta}" is too long for the button`, {
      action: 'shorten',
      role: 'cta',
      maxWords: 2,
    });
  }
  if (!anyOverflow) pass('overflow', 'all copy fits its box');

  /* ------------------------------------------------------------ safe area */
  const region = safeRegion(layout);
  const label = layout.safeBox
    ? `${region.w}x${region.h} safe zone`
    : `${layout.safe}px margin`;
  const offenders: string[] = [];
  for (const [role, box] of Object.entries(composed.rects)) {
    if (role === 'hero' && !layout.safeBox) continue; // hero is deliberately full-bleed
    if (!within(box, region)) offenders.push(role);
  }
  if (offenders.length === 0) {
    pass('safe-area', `all elements inside the ${label}`);
  } else {
    warn('safe-area', `outside the ${label}: ${offenders.join(', ')}`);
  }

  /* ------------------------------------------------------------ branding */
  if (composed.rects.logo) {
    const logoShare = (composed.rects.logo.w * composed.rects.logo.h) /
      (layout.canvas.w * layout.canvas.h);
    if (logoShare > 0.25) {
      warn('logo', `logo occupies ${(logoShare * 100).toFixed(0)}% of the canvas (target under 25%)`);
    } else {
      pass('logo', `present, ${(logoShare * 100).toFixed(1)}% of canvas`);
    }
  } else {
    fail('logo', 'no logo rendered — the advertiser must be identifiable');
  }

  /* ----------------------------------------------------------------- cta */
  if (rule.noBakedCta) {
    if (composed.fits.cta) {
      fail('cta', 'this placement supplies its own CTA — remove the baked-in button');
    } else {
      pass('cta', 'no baked CTA, as required for this placement');
    }
  } else if (composed.fits.cta) {
    pass('cta', `"${copy.cta}" at ${composed.fits.cta.fontSize}px`);
  } else {
    warn('cta', 'no CTA in the creative');
  }

  /* ----------------------------------------------------------- word count */
  const [minW, maxW] = rule.words;
  if (composed.wordCount > maxW) {
    warn(
      'word-count',
      `${composed.wordCount} words, guidance for this size is ${minW}-${maxW}`,
      { action: 'shorten', role: 'support', maxWords: maxW },
    );
  } else if (composed.wordCount < minW) {
    warn('word-count', `${composed.wordCount} words, below the ${minW}-${maxW} guidance`);
  } else {
    pass('word-count', `${composed.wordCount} words (guidance ${minW}-${maxW})`);
  }

  /* ------------------------------------------------------- min font size */
  const deliveredMin = composed.minFontSize * scale;
  const floor = rule.minFontPx ?? 11;
  const smallest = Object.entries(composed.fits).sort(
    (a, b) => a[1].fontSize - b[1].fontSize,
  )[0];
  const smallestRole = (smallest?.[0] ?? 'support') as BoxRole;
  if (deliveredMin < floor) {
    warn(
      'legibility',
      `"${smallestRole}" renders at ${deliveredMin.toFixed(1)}px at delivery scale, below the ${floor}px floor`,
      { action: 'shorten', role: smallestRole },
    );
  } else {
    pass(
      'legibility',
      `smallest text ("${smallestRole}") ${deliveredMin.toFixed(1)}px at delivery scale`,
    );
  }

  /* ----------------------------------------------------- visual hierarchy */
  // Amazon's creative guidance is explicit that size variation is how an ad
  // signals importance without instructions: "deliberately sizing each element
  // to signal its importance". If the headline and the supporting line render
  // at nearly the same size, the viewer has no focal point in the second or
  // two they give the unit.
  const headFit = composed.fits.headline;
  const supFit = composed.fits.support;
  if (headFit && supFit) {
    const ratio = headFit.fontSize / supFit.fontSize;
    if (ratio < 1.35) {
      warn(
        'hierarchy',
        `headline is only ${ratio.toFixed(2)}x the supporting text; aim for 1.4x or more so the eye has one clear entry point`,
        { action: 'shorten', role: 'headline' },
      );
    } else {
      pass('hierarchy', `headline ${ratio.toFixed(2)}x supporting text`);
    }
  }

  // One focal point. Both sources say the same thing from different angles:
  // "stick to one point of focus for each ad" and "minimal clutter". Offer,
  // trust and a support line all competing below the headline is the common
  // way a small unit turns into a brochure.
  const secondary = (['support', 'offer', 'trust'] as const).filter(
    (r) => layout[r] && (copy[r] ?? '').trim(),
  );
  const capacity = layout.canvas.w * layout.canvas.h >= 300 * 600 ? 3 : 2;
  if (secondary.length > capacity) {
    warn(
      'focal-point',
      `${secondary.length} supporting elements (${secondary.join(', ')}) compete below the headline; this canvas comfortably carries ${capacity}`,
    );
  } else {
    pass('focal-point', `${secondary.length} supporting element(s)`);
  }

  /* ------------------------------------------------------------ contrast */
  const lowContrast: string[] = [];
  for (const role of TEXT_ROLES) {
    const box = composed.rects[role];
    const spec = layout[role];
    if (!box || !spec) continue;
    // Use the colour actually rendered, not the template default. Over a
    // background image the composer forces light text, so measuring the
    // template's dark ink here would report a false low-contrast failure.
    let textColor = resolveColor(spec.color, brand, '#111111');
    if ((input as any).backgroundImage && role !== 'offer') {
      textColor = resolveColor('light', brand, '#ffffff');
    } else if (role === 'headline' && (brand.colors as any).headlineInk) {
      textColor = resolveColor('headlineInk', brand);
    }
    const fg = hexLuminance(textColor);
    const bg = await regionLuminance(backgroundPng, {
      left: box.x * scale,
      top: box.y * scale,
      width: Math.max(1, box.w * scale),
      height: Math.max(1, box.h * scale),
    });
    const ratio = contrastRatio(fg, bg);
    if (ratio < 4.5) lowContrast.push(`${role} ${ratio.toFixed(1)}:1`);
  }
  if (layout.cta && composed.fits.cta) {
    const fg = hexLuminance(resolveColor(layout.cta.color ?? 'dark', brand, '#111111'));
    const bg = hexLuminance(resolveColor(layout.cta.bg ?? 'accent', brand, '#ffc400'));
    const ratio = contrastRatio(fg, bg);
    if (ratio < 4.5) lowContrast.push(`cta ${ratio.toFixed(1)}:1`);
  }
  if (lowContrast.length) {
    warn('contrast', `below 4.5:1 — ${lowContrast.join(', ')}`);
  } else {
    pass('contrast', 'all text at or above 4.5:1 against what sits behind it');
  }

  return findings;
}

export function rollUp(findings: QaFinding[]): 'pass' | 'warn' | 'fail' {
  if (findings.some((f) => f.status === 'fail')) return 'fail';
  if (findings.some((f) => f.status === 'warn')) return 'warn';
  return 'pass';
}
