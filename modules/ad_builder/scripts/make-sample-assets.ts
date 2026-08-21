/**
 * Placeholder asset generator.
 *
 * These stand in for what the real pipeline supplies: Brandfetch or a customer
 * upload for the logo, and a customer photo or an OpenAI-generated background
 * for the hero. They are deliberately obvious placeholder art — nothing here
 * should ever reach a client proof.
 *
 * Note the three hero orientations. The research is explicit that one master
 * image cannot crop well into both a 970x250 and a 160x600, so the image step
 * produces landscape, square and vertical compositions rather than forcing one
 * photograph into every canvas.
 */

import * as fs from 'fs';
import * as path from 'path';
import sharp from 'sharp';
import { resolveFont, textPath, measure } from '../src/fonts';

const ROOT = path.resolve(__dirname, '..');
const BRAND = path.join(ROOT, 'assets', 'brand');
const HERO = path.join(ROOT, 'assets', 'hero');

const NAVY = '#123456';
const GOLD = '#FFC400';

function logoSvg(fg: string, mark: string): string {
  const font = resolveFont('Montserrat', 'bold');
  const size = 46;
  const label = 'ICON SOLAR';
  const w = measure(font, label, size, 1.5);
  const total = Math.ceil(w + 76);
  const rays = Array.from({ length: 8 }, (_, i) => {
    const a = (i * Math.PI) / 4;
    const x1 = 30 + Math.cos(a) * 20;
    const y1 = 34 + Math.sin(a) * 20;
    const x2 = 30 + Math.cos(a) * 27;
    const y2 = 34 + Math.sin(a) * 27;
    return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${mark}" stroke-width="5" stroke-linecap="round"/>`;
  }).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${total}" height="68" viewBox="0 0 ${total} 68">
    <circle cx="30" cy="34" r="14" fill="${mark}"/>
    ${rays}
    <path d="${textPath(font, label, 68, 50, size, 1.5)}" fill="${fg}"/>
  </svg>`;
}

function heroSvg(w: number, h: number): string {
  // A stylised suburban roofline with a panel array. Wide negative space is
  // kept on the left so copy has somewhere to live in landscape crops.
  const horizon = h * 0.72;
  const houseW = w * 0.52;
  const houseX = w - houseW - w * 0.04;
  const roofH = h * 0.24;
  const bodyY = horizon - h * 0.26;

  const panels: string[] = [];
  const cols = 6;
  const rows = 3;
  const pw = (houseW * 0.52) / cols;
  const ph = (roofH * 0.52) / rows;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const skew = (r - rows / 2) * pw * 0.12;
      panels.push(
        `<rect x="${(houseX + houseW * 0.24 + c * pw + skew).toFixed(1)}" y="${(bodyY - roofH * 0.72 + r * ph).toFixed(1)}" width="${(pw * 0.86).toFixed(1)}" height="${(ph * 0.82).toFixed(1)}" fill="#1b3f6b" stroke="#2f6ba8" stroke-width="1.2" rx="1"/>`,
      );
    }
  }

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    <defs>
      <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#4a90d9"/>
        <stop offset="0.62" stop-color="#a9d3f2"/>
        <stop offset="1" stop-color="#e8f2fb"/>
      </linearGradient>
      <linearGradient id="lawn" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#7fae5c"/>
        <stop offset="1" stop-color="#4f7c3a"/>
      </linearGradient>
    </defs>
    <rect width="${w}" height="${h}" fill="url(#sky)"/>
    <circle cx="${w * 0.16}" cy="${h * 0.18}" r="${h * 0.075}" fill="#ffe9a8" opacity="0.95"/>
    <rect y="${horizon}" width="${w}" height="${h - horizon}" fill="url(#lawn)"/>
    <polygon points="${houseX - houseW * 0.06},${bodyY} ${houseX + houseW * 0.5},${bodyY - roofH} ${houseX + houseW * 1.06},${bodyY}" fill="#8c5a3c"/>
    ${panels}
    <rect x="${houseX}" y="${bodyY}" width="${houseW}" height="${horizon - bodyY}" fill="#f2ede4"/>
    <rect x="${houseX + houseW * 0.12}" y="${bodyY + (horizon - bodyY) * 0.22}" width="${houseW * 0.16}" height="${(horizon - bodyY) * 0.34}" fill="#2f6ba8" opacity="0.75"/>
    <rect x="${houseX + houseW * 0.62}" y="${bodyY + (horizon - bodyY) * 0.22}" width="${houseW * 0.16}" height="${(horizon - bodyY) * 0.34}" fill="#2f6ba8" opacity="0.75"/>
    <rect x="${houseX + houseW * 0.4}" y="${bodyY + (horizon - bodyY) * 0.3}" width="${houseW * 0.14}" height="${(horizon - bodyY) * 0.7}" fill="#5c4433"/>
    <text x="${w * 0.03}" y="${h - 12}" font-family="monospace" font-size="${Math.max(11, h * 0.028)}" fill="#ffffff" opacity="0.55">PLACEHOLDER ART — NOT FOR CLIENT USE</text>
  </svg>`;
}

async function main() {
  fs.mkdirSync(BRAND, { recursive: true });
  fs.mkdirSync(HERO, { recursive: true });

  await sharp(Buffer.from(logoSvg(NAVY, GOLD)))
    .png()
    .toFile(path.join(BRAND, 'icon-solar-primary.png'));
  await sharp(Buffer.from(logoSvg('#FFFFFF', GOLD)))
    .png()
    .toFile(path.join(BRAND, 'icon-solar-reverse.png'));

  const heroes: [string, number, number][] = [
    ['hero-landscape.jpg', 1600, 900],
    ['hero-square.jpg', 1200, 1200],
    ['hero-vertical.jpg', 900, 1500],
  ];
  for (const [name, w, h] of heroes) {
    await sharp(Buffer.from(heroSvg(w, h)))
      .jpeg({ quality: 90 })
      .toFile(path.join(HERO, name));
  }

  console.log('Wrote placeholder assets to assets/brand and assets/hero');
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
