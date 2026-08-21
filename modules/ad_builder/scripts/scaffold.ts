/**
 * New-client scaffold.
 *
 *   npx tsx scripts/scaffold.ts \
 *     --client "Bella Vista Catering" --domain bellavista.com \
 *     --campaign "Fall Catering" --request AD-2026-002214 \
 *     --primary '#7A2E1F' --secondary '#C4713C' --accent '#F2C14E' \
 *     --headline-font Poppins --body-font "Open Sans"
 *
 * Produces a validated campaign JSON plus neutral, brand-coloured placeholder
 * assets so the client can be rendered immediately, before Brandfetch has run
 * or the customer has uploaded anything. The placeholders are abstract on
 * purpose — no invented photography that could be mistaken for approved art.
 */

import * as fs from 'fs';
import * as path from 'path';
import sharp from 'sharp';
import { fontIsAvailable, listFamilies, measure, resolveFont, textPath } from '../src/fonts';
import { validateCampaign, printFindings } from '../src/validate';
import type { Campaign } from '../src/types';

const ROOT = path.resolve(__dirname, '..');

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--')
    ? process.argv[i + 1]
    : fallback;
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

/** Wordmark from the brand name in the brand's own headline face. */
function wordmark(name: string, family: string, fg: string, mark: string): string {
  const font = resolveFont(family, 'bold');
  const size = 44;
  const label = name.toUpperCase();
  const w = measure(font, label, size, 1.2);
  const total = Math.ceil(w + 74);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${total}" height="66" viewBox="0 0 ${total} 66">
    <rect x="8" y="14" width="38" height="38" rx="9" fill="${mark}"/>
    <path d="${textPath(font, name.slice(0, 1).toUpperCase(), 20, 44, 30)}" fill="${fg}"/>
    <path d="${textPath(font, label, 64, 47, size, 1.2)}" fill="${fg}"/>
  </svg>`;
}

/** Abstract, obviously-placeholder hero built from the brand palette. */
function placeholderHero(w: number, h: number, colors: string[]): string {
  const bands = colors.map((c, i) => {
    const a = (i + 1) / (colors.length + 1);
    return `<circle cx="${(w * (0.25 + i * 0.28)).toFixed(0)}" cy="${(h * (0.35 + (i % 2) * 0.25)).toFixed(0)}" r="${(Math.min(w, h) * (0.34 - i * 0.06)).toFixed(0)}" fill="${c}" opacity="${(0.55 - i * 0.1).toFixed(2)}"/>`;
  }).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="${colors[0]}"/><stop offset="1" stop-color="${colors[1] ?? colors[0]}"/>
    </linearGradient></defs>
    <rect width="${w}" height="${h}" fill="url(#g)"/>
    ${bands}
    <text x="${w * 0.5}" y="${h * 0.53}" text-anchor="middle" font-family="monospace"
      font-size="${Math.max(13, Math.min(w, h) * 0.055)}" fill="#ffffff" opacity="0.85">PLACEHOLDER</text>
    <text x="${w * 0.5}" y="${h * 0.53 + Math.max(16, Math.min(w, h) * 0.07)}" text-anchor="middle"
      font-family="monospace" font-size="${Math.max(10, Math.min(w, h) * 0.036)}"
      fill="#ffffff" opacity="0.7">REPLACE BEFORE CLIENT PROOF</text>
  </svg>`;
}

async function main() {
  const client = arg('client');
  const domain = arg('domain');
  const campaignName = arg('campaign');
  if (!client || !domain || !campaignName) {
    console.error('Required: --client "Name" --domain example.com --campaign "Campaign Name"');
    console.error('Optional: --request --primary --secondary --accent --light --dark --headline-font --body-font --out');
    process.exit(1);
  }

  const colors = {
    primary: arg('primary', '#123456')!,
    secondary: arg('secondary', '#2F6BA8')!,
    accent: arg('accent', '#FFC400')!,
    light: arg('light', '#FFFFFF')!,
    dark: arg('dark', '#10233A')!,
  };
  const headlineFont = arg('headline-font', 'Montserrat')!;
  const bodyFont = arg('body-font', 'Open Sans')!;

  for (const [slot, family] of [['headline', headlineFont], ['body', bodyFont]]) {
    if (!fontIsAvailable(family)) {
      console.error(
        `\nFont "${family}" (${slot}) is not in the registry, so it cannot be rendered.\n` +
          `Available: ${listFamilies().join(', ')}\n` +
          `To add it: vendor the licensed font files into the repo and add an entry to REGISTRY in src/fonts.ts.\n`,
      );
      process.exit(1);
    }
  }

  const clientSlug = slug(client);
  const assetDir = path.join(ROOT, 'assets', clientSlug);
  fs.mkdirSync(assetDir, { recursive: true });

  await sharp(Buffer.from(wordmark(client, headlineFont, colors.primary, colors.accent)))
    .png().toFile(path.join(assetDir, 'logo-primary.png'));
  await sharp(Buffer.from(wordmark(client, headlineFont, colors.light, colors.accent)))
    .png().toFile(path.join(assetDir, 'logo-reverse.png'));

  const palette = [colors.primary, colors.secondary, colors.accent];
  for (const [name, w, h] of [
    ['hero-landscape.jpg', 1600, 900],
    ['hero-square.jpg', 1200, 1200],
    ['hero-vertical.jpg', 900, 1500],
  ] as [string, number, number][]) {
    await sharp(Buffer.from(placeholderHero(w, h, palette)))
      .jpeg({ quality: 88 }).toFile(path.join(assetDir, name));
  }

  const rel = (f: string) => path.posix.join('assets', clientSlug, f);
  const campaign: Campaign = {
    requestId: arg('request', `AD-${new Date().getFullYear()}-${Date.now().toString().slice(-6)}`)!,
    campaignName,
    brand: {
      name: client,
      domain,
      colors,
      fonts: { headline: headlineFont, body: bodyFont },
      logos: { primary: rel('logo-primary.png'), reverse: rel('logo-reverse.png') },
    },
    concepts: [
      {
        conceptId: 'A',
        name: 'Benefit',
        layoutFamily: 'T01',
        hero: {
          landscape: rel('hero-landscape.jpg'),
          square: rel('hero-square.jpg'),
          vertical: rel('hero-vertical.jpg'),
        },
        copy: {
          default: {
            headline: 'Replace with a benefit-led headline',
            support: 'One short supporting sentence.',
            cta: 'Learn More',
          },
        },
      },
    ],
  };

  const outFile = arg('out', path.join(ROOT, 'campaigns', `${clientSlug}.json`))!;
  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  fs.writeFileSync(outFile, JSON.stringify(campaign, null, 2) + '\n');

  console.log(`\nScaffolded ${client}`);
  console.log(`  campaign  ${outFile}`);
  console.log(`  assets    ${path.relative(ROOT, assetDir)}/ (placeholder — replace before any client proof)`);

  const findings = validateCampaign(campaign, { assetRoot: ROOT });
  console.log('\nValidation:');
  const { errors, warnings } = printFindings(findings);
  if (!errors && !warnings) console.log('  all checks passed');

  console.log(`\nNext: edit the copy, then\n  npx tsx src/cli.ts --campaign ${path.relative(ROOT, outFile)} --platform google\n`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
