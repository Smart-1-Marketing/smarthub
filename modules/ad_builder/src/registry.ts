/**
 * Loaders for the two JSON config surfaces.
 *
 * Both live in the repo, not the database, so a platform spec change is a
 * one-file pull request rather than an application deploy.
 */

import * as fs from 'fs';
import * as path from 'path';
import type { PlatformConfig, SizeKey, TemplateSpec } from './types';

const TEMPLATE_DIR = path.resolve(__dirname, 'templates');
const PLATFORM_DIR = path.resolve(__dirname, 'config', 'platforms');

const templates = new Map<string, TemplateSpec>();
const platforms = new Map<string, PlatformConfig>();

function loadJson<T>(file: string): T {
  return JSON.parse(fs.readFileSync(file, 'utf8')) as T;
}

export function loadTemplates(): Map<string, TemplateSpec> {
  if (templates.size) return templates;
  for (const f of fs.readdirSync(TEMPLATE_DIR).filter((f) => f.endsWith('.json'))) {
    const spec = loadJson<TemplateSpec>(path.join(TEMPLATE_DIR, f));
    templates.set(spec.id, spec);
  }
  return templates;
}

export function getTemplate(id: string): TemplateSpec {
  const t = loadTemplates().get(id);
  if (!t) {
    throw new Error(
      `Unknown layout family "${id}". Available: ${[...loadTemplates().keys()].join(', ')}`,
    );
  }
  return t;
}

export function loadPlatforms(): Map<string, PlatformConfig> {
  if (platforms.size) return platforms;
  for (const f of fs.readdirSync(PLATFORM_DIR).filter((f) => f.endsWith('.json'))) {
    const cfg = loadJson<PlatformConfig>(path.join(PLATFORM_DIR, f));
    platforms.set(cfg.platform, cfg);
  }
  return platforms;
}

export function getPlatform(id: string): PlatformConfig {
  const p = loadPlatforms().get(id);
  if (!p) {
    throw new Error(
      `Unknown platform "${id}". Available: ${[...loadPlatforms().keys()].join(', ')}`,
    );
  }
  return p;
}

/**
 * The platforms a submission may name.
 *
 * There is a config file per platform on disk and that file is the whole
 * definition of one -- its sizes, their weight ceilings, the scale it
 * delivers at. So "is this a platform?" has exactly one right answer and it
 * is this directory listing.
 *
 * It is a function because the alternative was a literal, and the literal was
 * written out three times: `.filter(p => p === 'google' || p === 'amazon')`
 * in the request route, in the auto-render branch and in the validator. A
 * platform whose config file existed and whose sizes every template already
 * carried -- which is exactly what meta.json was -- got as far as those three
 * lines and was dropped, with no error, no note, and a campaign that came
 * back built for Google. That is the failure this codebase names most often:
 * the tool looks healthy and answers a different question from the one asked.
 *
 * Refusals are returned rather than swallowed, so a caller can say which name
 * it did not recognise instead of quietly building something smaller.
 */
export function acceptPlatforms(
  value: unknown,
  fallback: string[] = ['google'],
): { platforms: string[]; refused: string[] } {
  const asked = Array.isArray(value) ? value.map((v) => String(v ?? '').trim().toLowerCase()) : [];
  const known = loadPlatforms();
  const platforms: string[] = [];
  const refused: string[] = [];
  for (const p of asked) {
    if (!p) continue;
    // "Not sure -- recommend" is an answer to a question on the intake form,
    // not a platform. It is dropped rather than refused, because reporting it
    // as an unknown platform would put a warning in front of somebody who
    // answered the form correctly.
    if (p === 'unsure') continue;
    if (known.has(p)) { if (!platforms.includes(p)) platforms.push(p); }
    else if (!refused.includes(p)) refused.push(p);
  }
  return { platforms: platforms.length ? platforms : fallback, refused };
}

/** Sizes a given template can actually render for a given platform. */
export function renderableSizes(templateId: string, platformId: string): SizeKey[] {
  const t = getTemplate(templateId);
  const p = getPlatform(platformId);
  return (Object.keys(t.sizes) as SizeKey[]).filter((s) => p.sizes[s]);
}

/** Sizes the platform wants that this template has no layout for. */
export function missingSizes(templateId: string, platformId: string): SizeKey[] {
  const t = getTemplate(templateId);
  const p = getPlatform(platformId);
  return (Object.keys(p.sizes) as SizeKey[]).filter((s) => !t.sizes[s]);
}
