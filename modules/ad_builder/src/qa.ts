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
import { inkOverBackground, resolveColor } from './svg';
import sharp from 'sharp';
import { flatBackdrop, type FlatBackdrop } from './logo-tools';
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
   *  is measured against the text colour the composer actually used. */
  backgroundImage?: string;
  /** The overlay the composer painted over that image. Both are needed here:
   *  the ink over a photo depends on them, and measuring the wrong ink reports
   *  a contrast result the render does not have. */
  backgroundOverlay?: number;
  backgroundOverlayColor?: string;
}

const TEXT_ROLES = ['headline', 'support', 'offer', 'trust'] as const;

/**
 * How far past its own pixels a photograph may be painted before it is worth
 * saying so. Chosen to be quiet in the ordinary case and loud in the one that
 * matters: stock photography comes back at 3000px and never trips it, while a
 * 1024px generated still asked to fill Amazon's 1940x500 billboard is painted
 * at nearly twice its own width and does.
 */
/**
 * How far a logo's plate must sit from the panel behind it before the box
 * is worth naming. Luminance, 0-1. Below this the plate is invisible in the
 * render and a warning would be noise on every ad that has one.
 */
const PLATE_VISIBLE_DELTA = 0.12;

/**
 * Will this plate actually read as a box on the panel behind it?
 *
 * Its own function because no fixture lands on the boundary, and a threshold
 * nothing exercises is a threshold that can be deleted without a test
 * noticing -- which is what a first pass here proved when the check was
 * inline. Symmetric on purpose: a dark plate on a white card is the same box
 * as a white plate on a navy one.
 */
export function plateShowsAgainst(plate: FlatBackdrop | null, behind: number): boolean {
  if (!plate) return false;
  return Math.abs(plate.luminance - behind) > PLATE_VISIBLE_DELTA;
}

const UPSCALE_LIMIT = 1.25;

/**
 * What the proof prints, and the verdict taken on it — decided together.
 *
 * These are one function because separating them is how they disagree: round
 * to whole percent for the screen while judging the unrounded value and the
 * proof reads "20% of the canvas, over the 20% Meta recommends", which looks
 * like a broken check rather than a fact about the ad. The verdict is taken on
 * the number as printed, so whatever a reader can see is what was decided.
 *
 * Exported so the boundary can be asserted directly. Left inside runQa it was
 * only ever exercised by whatever coverage the fixtures happened to land on,
 * and neither fixture lands on 20.
 */
export function coverageVerdict(pct: number, limitPct: number): { shown: string; over: boolean } {
  const rounded = Number(pct.toFixed(1));
  return { shown: `${rounded.toFixed(1)}%`, over: rounded > limitPct };
}

/**
 * Mean luminance of a logo's visible (non-transparent) pixels. This is what
 * the eye compares against the backdrop — transparent padding must not count,
 * or a white mark on a transparent canvas would average out to "grey" and
 * sneak past the check.
 */
export async function logoInkLuminance(file: string): Promise<number | null> {
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
    const behind = await regionLuminance(backgroundPng, {
      left: Math.max(0, Math.round(logoBox.x * scale)),
      top: Math.max(0, Math.round(logoBox.y * scale)),
      width: Math.max(1, Math.round(logoBox.w * scale)),
      height: Math.max(1, Math.round(logoBox.h * scale)),
    });

    /* ---------------------------------------------------------- logo plate */
    // logo-tools.ts opens with this as rule 1: "Any logo that is not already
    // transparent must have its background removed before compositing -- a
    // white box around a logo on a coloured ad looks broken." Nothing asked.
    // hasTransparency() was written for it and had no caller anywhere.
    //
    // The contrast check below could not catch it either, and read BETTER on
    // the broken ad: logoInkLuminance() averages every opaque pixel, so on a
    // plated logo it measures the PLATE. The same navy wordmark scores 2.3:1
    // on a transparent canvas and 9.9:1 with a white box behind it, on a navy
    // panel -- so the box makes QA more confident about the one ad that has a
    // white rectangle stamped across it.
    //
    // Only a finding when it will actually show. A white plate on a white
    // card is invisible, and a warning that fires on every ad is one people
    // stop reading -- the note hub/qr_codes.py makes about a QR warning on
    // every social spot.
    const plate = await flatBackdrop(logoFile);
    const plateShows = plateShowsAgainst(plate, behind);
    if (plateShows) {
      const [r, g, b] = plate!.rgb;
      warn('logo-plate',
        `the logo has an opaque rgb(${r}, ${g}, ${b}) background that will show as a box against this panel. Use Rework logo to strip it, or supply a transparent PNG.`);
    } else if (plate !== null) {
      pass('logo-plate', 'the logo carries a flat background, but it matches the panel behind it');
    } else {
      pass('logo-plate', 'the logo carries its own transparency');
    }

    if (ink !== null && !plateShows) {
      const ratio = contrastRatio(ink, behind);
      if (ratio < 1.7) {
        // Suggest the opposite of what's there: a light logo needs the
        // darker/full-colour version and vice versa.
        const suggestion = ink > 0.5 ? 'full-color (darker)' : 'white';
        warn('logo-contrast', `the logo is nearly invisible against its background (${ratio.toFixed(1)}:1). Try the ${suggestion} logo on this size.`);
      } else {
        pass('logo-contrast', `logo reads clearly against its background (${ratio.toFixed(1)}:1)`);
      }
    }
    // A plated logo gets NO contrast finding rather than a passing one. What
    // that number measures is the plate against the panel, and printing it
    // under a heading about the logo is the confident wrong answer -- two
    // findings disagreeing about one ad, with the reassuring one on top.
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
      `${kb(raster.bytes)} exceeds the ${kb(rule.maxFileBytes)} limit after ${raster.attempts} compression attempts — simplify the hero image or switch to a flat-color treatment`,
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

  /* ---------------------------------------------------------- collisions
     Two blocks sharing space is invisible to every other check here, because
     each one measures a box on its own: the contrast pass samples what is
     behind the ink and finds the other block's fill, the fit pass finds
     copy that fits its own box perfectly, and the safe-area pass finds both
     boxes inside the margin. The ad has a headline printed through a button.

     Newly worth checking because blocks can now be MOVED. Until the button
     and the logo gained a nudge, every box came from a hand-authored template
     that the diagnostics page already checks for overlaps at boot; an arrow
     pad is how the two get put on top of each other one press at a time. */
  const placed = Object.entries(composed.rects)
    .filter(([role]) => role !== 'hero');       // deliberately full-bleed
  const collisions: string[] = [];
  for (let i = 0; i < placed.length; i++) {
    for (let j = i + 1; j < placed.length; j++) {
      const [an, a] = placed[i];
      const [bn, b] = placed[j];
      // A couple of pixels of touching is kerning, not a collision. The
      // template check uses the same tolerance.
      const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      if (ox > 1 && oy > 1) collisions.push(`${an} over ${bn}`);
    }
  }
  if (collisions.length) {
    fail('collision', `${collisions.join(', ')} — these are printed on top of each other`);
  } else {
    pass('collision', 'no two elements share space');
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

  /* ----------------------------------------------------------- word count
     There is deliberately no word-count check.

     It counted words against a per-size guidance band and warned either side
     of it, which made a perfectly legible ad amber for being four words short
     and taught people that amber means nothing. The question it was standing
     in for -- does the copy fit -- is answered properly and per block by the
     overflow check above, from real glyph measurements rather than a word
     tally. `composed.wordCount` is still reported on the proof and in the
     manifest, where it is information rather than a verdict. */

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
    if ((input as any).backgroundImage && role !== 'offer' && !(spec as any).keepColorOnBg) {
      textColor = resolveColor(inkOverBackground(input, brand), brand, '#ffffff');
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

  /* ------------------------------------------------------ source resolution
     A photograph stretched past its own pixels goes soft, and soft is the one
     defect that survives every other check here: it sits inside the safe area,
     it collides with nothing, its contrast is fine, and the file is comfortably
     under the cap because a blurry JPEG compresses well. It is found by
     somebody looking at the proof, which is late.

     The comparison is against the pixels actually PAINTED at delivery scale,
     not against the canvas. A `cover` fit paints a wide photo much wider than
     a tall canvas and crops the overflow, so measuring the canvas would report
     a 1024px source filling a 300px slot as a comfortable fit while most of
     its width was being thrown away — and Amazon's 2x sizes double the ask
     again, which is exactly where a stock photo runs out.

     Never a fail. A slightly soft photograph is a judgement call somebody may
     well ship on a deadline, and blocking delivery over it would mean the
     override click gets learned as routine — which is what makes it useless on
     the checks that genuinely must stop a delivery. */
  const measurable = composed.images.filter((i) => i.naturalW > 0 && i.naturalH > 0);
  if (composed.images.length) {
    if (!measurable.length) {
      // Every source is vector. That is not an unmeasured case: an SVG has no
      // resolution to outrun, so this is a real answer rather than a gap.
      pass('source-resolution', 'vector artwork — no resolution to outrun');
    } else {
      const stretched = measurable
        .map((i) => ({ ...i, factor: (i.drawnW * scale) / i.naturalW }))
        .filter((i) => i.factor > UPSCALE_LIMIT)
        .sort((a, b) => b.factor - a.factor);
      if (stretched.length) {
        const worst = stretched[0];
        warn(
          'source-resolution',
          `the ${worst.role} image is ${worst.naturalW}x${worst.naturalH} and is painted at ` +
          `${Math.round(worst.drawnW * scale)}x${Math.round(worst.drawnH * scale)} — ` +
          `${worst.factor.toFixed(1)}x its own pixels, which will show as softness. ` +
          `Use a larger source.`,
        );
      } else {
        const tightest = measurable
          .map((i) => (i.drawnW * scale) / i.naturalW)
          .sort((a, b) => b - a)[0];
        pass(
          'source-resolution',
          `every image has the pixels for the size it is drawn at (largest ask ${tightest.toFixed(2)}x)`,
        );
      }
    }
  }

  /* --------------------------------------------------- text coverage (Meta)
     Meta retired the 20% text rule as a rejection in September 2020. It is a
     DELIVERY guideline now: a text-heavy image is not refused, it is served
     less. So this is a warning that says which of those two it is, and it can
     never fail a render — reporting a retired rule as a hard failure would
     block a delivery Meta itself would accept.

     It is scoped to Meta and to nothing else on purpose. A 300x250 banner is
     mostly type by design and always will be; running this check against one
     would put an amber chip on every display size in the campaign, and an
     amber chip on everything is read as amber meaning nothing — which is the
     state the word-count check was removed for a few sections up.

     The number is an estimate and says so. Meta scored a 5x5 grid; this is the
     area of the ink boxes over the area of the canvas, which is the same
     question asked more coarsely. */
  if (rule.textCoverageWarnPct != null) {
    const canvasArea = layout.canvas.w * layout.canvas.h;
    let inkArea = 0;
    for (const role of [...TEXT_ROLES, 'cta'] as const) {
      const box = composed.rects[role];
      if (!box) continue;
      inkArea += box.w * box.h;
    }
    const pct = canvasArea > 0 ? (inkArea / canvasArea) * 100 : 0;
    const { shown, over } = coverageVerdict(pct, rule.textCoverageWarnPct);
    if (over) {
      warn(
        'text-coverage',
        `text covers roughly ${shown} of the canvas, over the ${rule.textCoverageWarnPct}% Meta ` +
        `recommends. Not a rejection — Meta dropped that rule in 2020 — but text-heavy images ` +
        `are served less, so this one may reach fewer people. A layout that gives the photo ` +
        `more of the canvas is the fix if the reach matters more than the copy.`,
      );
    } else {
      pass('text-coverage', `text covers roughly ${shown} of the canvas`);
    }
  }

  return findings;
}

export function rollUp(findings: QaFinding[]): 'pass' | 'warn' | 'fail' {
  if (findings.some((f) => f.status === 'fail')) return 'fail';
  if (findings.some((f) => f.status === 'warn')) return 'warn';
  return 'pass';
}
