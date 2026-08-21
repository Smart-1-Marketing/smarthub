/**
 * Campaign validation.
 *
 * Runs before any rendering. The failure mode this exists to prevent is a new
 * client's campaign rendering "successfully" in the wrong typeface because
 * their brand font was not in the registry and the renderer quietly fell back.
 * A silent substitution is worse than a hard stop: the proof looks finished,
 * so nobody checks.
 */

import * as fs from 'fs';
import * as path from 'path';
import type { Campaign, CreativeConcept, SizeKey } from './types';
import { fontIsAvailable, listFamilies } from './fonts';
import { getPlatform, getTemplate, loadTemplates } from './registry';

export interface Finding {
  level: 'error' | 'warning' | 'info';
  field: string;
  message: string;
}

const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
const COLOR_ROLES = ['primary', 'secondary', 'accent', 'light', 'dark'] as const;

function exists(assetRoot: string, p: string): boolean {
  return fs.existsSync(path.isAbsolute(p) ? p : path.resolve(assetRoot, p));
}

export function validateCampaign(
  campaign: Campaign,
  opts: { assetRoot: string; platforms?: string[] },
): Finding[] {
  const f: Finding[] = [];
  const { assetRoot } = opts;
  const platforms = opts.platforms ?? ['google', 'amazon'];
  const err = (field: string, message: string) => f.push({ level: 'error', field, message });
  const warn = (field: string, message: string) => f.push({ level: 'warning', field, message });
  const info = (field: string, message: string) => f.push({ level: 'info', field, message });

  /* ------------------------------------------------------------ identity */
  if (!campaign.requestId?.trim()) err('requestId', 'required — it names the report and manifest files');
  if (!campaign.campaignName?.trim()) err('campaignName', 'required — it forms the Cloudinary folder');
  const b = campaign.brand;
  if (!b) {
    err('brand', 'missing');
    return f;
  }
  if (!b.name?.trim()) err('brand.name', 'required — it forms the Cloudinary folder');
  if (!b.domain?.trim()) err('brand.domain', 'required — it prefixes every output filename');

  /* -------------------------------------------------------------- colors */
  for (const role of COLOR_ROLES) {
    const v = b.colors?.[role];
    if (!v) {
      err(`brand.colors.${role}`, 'missing — templates reference all five roles by name');
    } else if (!HEX.test(v)) {
      err(`brand.colors.${role}`, `"${v}" is not a hex colour`);
    }
  }
  if (b.colors?.light && b.colors?.dark && b.colors.light.toLowerCase() === b.colors.dark.toLowerCase()) {
    err('brand.colors', 'light and dark are identical — every contrast check will fail');
  }

  /* --------------------------------------------------------------- fonts */
  for (const slot of ['headline', 'body'] as const) {
    const family = b.fonts?.[slot];
    if (!family?.trim()) {
      err(`brand.fonts.${slot}`, 'required');
    } else if (!fontIsAvailable(family)) {
      err(
        `brand.fonts.${slot}`,
        `"${family}" is not in the font registry, so it would silently render in the fallback face. ` +
          `Add it to src/fonts.ts (registry has: ${listFamilies().join(', ')}), or set this to one of those.`,
      );
    }
  }

  /* --------------------------------------------------------------- logos */
  if (!b.logos?.primary) {
    err('brand.logos.primary', 'required — QA fails any creative without a visible advertiser');
  } else if (!exists(assetRoot, b.logos.primary)) {
    err('brand.logos.primary', `file not found: ${b.logos.primary}`);
  }
  if (b.logos?.reverse && !exists(assetRoot, b.logos.reverse)) {
    err('brand.logos.reverse', `file not found: ${b.logos.reverse}`);
  }

  /* ------------------------------------------------------------ concepts */
  if (!campaign.concepts?.length) {
    err('concepts', 'no concepts defined');
    return f;
  }

  const known = [...loadTemplates().keys()];
  for (const c of campaign.concepts) {
    const at = `concepts[${c.conceptId}]`;
    if (!c.conceptId?.trim()) err(at, 'conceptId is required');
    if (!known.includes(c.layoutFamily)) {
      err(`${at}.layoutFamily`, `"${c.layoutFamily}" is not a template. Available: ${known.join(', ')}`);
      continue;
    }

    const template = getTemplate(c.layoutFamily);
    if (!c.copy?.default) {
      warn(`${at}.copy.default`, 'no default copy — every size must then define its own');
    }

    // Which hero orientations does this template actually ask for?
    const needed = new Set<string>();
    for (const layout of Object.values(template.sizes)) {
      if (layout?.hero) needed.add(layout.hero.orientation);
    }
    for (const o of needed) {
      const src = (c.hero as Record<string, string | undefined>)?.[o];
      if (!src) {
        warn(
          `${at}.hero.${o}`,
          `${c.layoutFamily} uses a ${o} hero but none is supplied — that area will render as a flat colour block`,
        );
      } else if (!exists(assetRoot, src)) {
        err(`${at}.hero.${o}`, `file not found: ${src}`);
      }
    }

    checkCopyCoverage(c, template.id, platforms, f);
  }

  /* ------------------------------------------------------------ coverage */
  for (const platform of platforms) {
    let cfg;
    try {
      cfg = getPlatform(platform);
    } catch {
      err('platform', `unknown platform "${platform}"`);
      continue;
    }
    for (const c of campaign.concepts) {
      if (!known.includes(c.layoutFamily)) continue;
      const t = getTemplate(c.layoutFamily);
      const covered = (Object.keys(cfg.sizes) as SizeKey[]).filter((s) => t.sizes[s]);
      const gaps = (Object.keys(cfg.sizes) as SizeKey[]).filter((s) => !t.sizes[s]);
      info(
        `concepts[${c.conceptId}]`,
        `${platform}: ${covered.length} of ${Object.keys(cfg.sizes).length} sizes` +
          (gaps.length ? ` (no ${c.layoutFamily} layout for ${gaps.join(', ')})` : ''),
      );
    }
  }

  return f;
}

function checkCopyCoverage(
  c: CreativeConcept,
  templateId: string,
  platforms: string[],
  f: Finding[],
): void {
  const template = getTemplate(templateId);
  for (const [size, layout] of Object.entries(template.sizes)) {
    if (!layout) continue;
    const copy = { ...(c.copy.default ?? {}), ...(c.copy[size as SizeKey] ?? {}) };
    const at = `concepts[${c.conceptId}].copy.${size}`;

    if (layout.headline && !copy.headline?.trim()) {
      f.push({ level: 'error', field: at, message: 'headline is required by this layout' });
    }
    if (layout.offer && !copy.offer?.trim()) {
      f.push({
        level: 'warning',
        field: at,
        message: `${templateId} places an offer at this size but none is supplied`,
      });
    }
    // A baked CTA is required except where the platform inserts its own; that
    // exception is checked per-platform at render time, so this stays a warning.
    if (layout.cta && !copy.cta?.trim()) {
      f.push({ level: 'warning', field: at, message: 'no CTA — the button will not render' });
    }
  }
}

export function printFindings(findings: Finding[], verbose = false): { errors: number; warnings: number } {
  const errors = findings.filter((x) => x.level === 'error');
  const warnings = findings.filter((x) => x.level === 'warning');
  const infos = findings.filter((x) => x.level === 'info');

  for (const x of errors) console.log(`  x  ${x.field}: ${x.message}`);
  for (const x of warnings) console.log(`  !  ${x.field}: ${x.message}`);
  if (verbose) for (const x of infos) console.log(`  -  ${x.field}: ${x.message}`);

  return { errors: errors.length, warnings: warnings.length };
}
