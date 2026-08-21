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
