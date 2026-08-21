/**
 * Generate layouts for the standard sizes every template was missing:
 * 250x250, 160x600 (T04), and the four Meta sizes. Rather than hand-author
 * ~26 near-identical JSON blocks, derive them from a few centered archetypes
 * scaled to each canvas. Logo and CTA are centre-aligned on the square and
 * vertical formats, per the brief.
 *
 * Run: npx tsx scripts/gen-layouts.ts
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

type Box = Record<string, unknown>;
const TEMPLATE_DIR = path.resolve('src/templates');

/** A centered, stacked layout: logo top-centre, headline, support, CTA bottom-centre. */
function centeredSquare(w: number, h: number, safe: number): Box {
  const cw = w - safe * 2;
  const logoH = Math.round(h * 0.11);
  const ctaH = Math.round(h * 0.15);
  const ctaW = Math.round(cw * 0.7);
  return {
    canvas: { w, h },
    safe,
    background: 'light',
    logo: { x: safe, y: Math.round(h * 0.08), w: cw, h: logoH, align: 'center', valign: 'top' },
    headline: {
      x: safe, y: Math.round(h * 0.26), w: cw, h: Math.round(h * 0.26),
      size: [Math.round(h * 0.07), Math.round(h * 0.11)], maxLines: 3, lineHeight: 1.12,
      weight: 'bold', color: 'dark', align: 'center',
    },
    support: {
      x: safe, y: Math.round(h * 0.55), w: cw, h: Math.round(h * 0.16),
      size: [Math.round(h * 0.04), Math.round(h * 0.055)], maxLines: 2, lineHeight: 1.25,
      color: 'dark', align: 'center',
    },
    cta: {
      x: Math.round((w - ctaW) / 2), y: Math.round(h - ctaH - safe), w: ctaW, h: ctaH,
      size: [Math.round(h * 0.045), Math.round(h * 0.06)], maxLines: 1,
      radius: 6, bg: 'accent', color: 'dark', align: 'center', valign: 'middle', weight: 'bold',
    },
  };
}

/** A tall vertical layout (160x600, story formats): same centered stack, more air. */
function centeredVertical(w: number, h: number, safe: number): Box {
  const cw = w - safe * 2;
  const ctaH = Math.round(h * 0.08);
  const ctaW = Math.round(cw * 0.86);
  return {
    canvas: { w, h },
    safe,
    background: 'light',
    logo: { x: safe, y: Math.round(h * 0.05), w: cw, h: Math.round(h * 0.07), align: 'center', valign: 'top' },
    headline: {
      x: safe, y: Math.round(h * 0.16), w: cw, h: Math.round(h * 0.28),
      size: [Math.round(w * 0.10), Math.round(w * 0.15)], maxLines: 4, lineHeight: 1.14,
      weight: 'bold', color: 'dark', align: 'center',
    },
    support: {
      x: safe, y: Math.round(h * 0.47), w: cw, h: Math.round(h * 0.2),
      size: [Math.round(w * 0.06), Math.round(w * 0.08)], maxLines: 4, lineHeight: 1.3,
      color: 'dark', align: 'center',
    },
    cta: {
      x: Math.round((w - ctaW) / 2), y: Math.round(h - ctaH - safe * 1.4), w: ctaW, h: ctaH,
      size: [Math.round(w * 0.055), Math.round(w * 0.075)], maxLines: 1,
      radius: 6, bg: 'accent', color: 'dark', align: 'center', valign: 'middle', weight: 'bold',
    },
  };
}

/** A landscape layout (1200x628): logo top-centre, text centred, CTA centred low. */
function centeredLandscape(w: number, h: number, safe: number): Box {
  const cw = w - safe * 2;
  const ctaH = Math.round(h * 0.16);
  const ctaW = Math.round(w * 0.37);
  return {
    canvas: { w, h },
    safe,
    background: 'light',
    logo: { x: safe, y: Math.round(h * 0.09), w: cw, h: Math.round(h * 0.12), align: 'center', valign: 'top' },
    headline: {
      x: safe, y: Math.round(h * 0.28), w: cw, h: Math.round(h * 0.3),
      size: [Math.round(h * 0.1), Math.round(h * 0.16)], maxLines: 2, lineHeight: 1.1,
      weight: 'bold', color: 'dark', align: 'center',
    },
    support: {
      x: safe, y: Math.round(h * 0.6), w: cw, h: Math.round(h * 0.12),
      size: [Math.round(h * 0.05), Math.round(h * 0.07)], maxLines: 1, lineHeight: 1.2,
      color: 'dark', align: 'center',
    },
    cta: {
      x: Math.round((w - ctaW) / 2), y: Math.round(h - ctaH - safe), w: ctaW, h: ctaH,
      size: [Math.round(h * 0.06), Math.round(h * 0.09)], maxLines: 1,
      radius: 6, bg: 'accent', color: 'dark', align: 'center', valign: 'middle', weight: 'bold',
    },
  };
}

const NEW_SIZES: Record<string, Box> = {
  '250x250': centeredSquare(250, 250, 16),
  '160x600': centeredVertical(160, 600, 12),
  '1080x1080': centeredSquare(1080, 1080, 64),
  '1200x628': centeredLandscape(1200, 628, 56),
  '1080x1350': centeredSquare(1080, 1350, 64),
  '1080x1920': centeredVertical(1080, 1920, 80),
};

let added = 0;
for (const file of fs.readdirSync(TEMPLATE_DIR).filter((f) => /^T\d+\.json$/.test(f))) {
  const p = path.join(TEMPLATE_DIR, file);
  const tpl = JSON.parse(fs.readFileSync(p, 'utf8'));
  for (const [size, layout] of Object.entries(NEW_SIZES)) {
    if (!tpl.sizes[size]) {
      tpl.sizes[size] = layout;
      added++;
    }
  }
  fs.writeFileSync(p, JSON.stringify(tpl, null, 2));
  console.log(`${file}: now ${Object.keys(tpl.sizes).length} sizes`);
}
console.log(`Added ${added} layouts.`);
