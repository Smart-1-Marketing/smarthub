import * as fs from 'node:fs';
import * as path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import type { Campaign, SizeKey } from './types';
import type { Project } from './projects';
import { getTemplate, getPlatform } from './registry';

export interface CampaignDocument { campaign: Campaign; platforms?: string[]; [key: string]: any }
export interface ArtworkKey { conceptId: string; platform: string; size: string }
export class CampaignConflict extends Error { readonly statusCode = 409; }

function canonical(value: any): any {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().filter(k => value[k] !== undefined).map(k => [k, canonical(value[k])]),
  );
  return value;
}
export function digest(value: any): string {
  return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
}
export function campaignRevision(doc: CampaignDocument): string {
  return digest({ campaign: doc.campaign, platforms: doc.platforms ?? ['google'] });
}

/** Effective inputs for one placement, including local asset bytes. Edits to
 * another size's copy remain possible while shared brand/layout edits lock. */
export function artworkFingerprint(doc: CampaignDocument, key: ArtworkKey, assetRoot: string): string {
  const concept = doc.campaign.concepts.find(c => c.conceptId === key.conceptId);
  if (!concept || !(doc.platforms ?? ['google']).includes(key.platform)) throw new CampaignConflict('That placement is no longer in this campaign.');
  const layout = getTemplate(concept.layoutFamily).sizes[key.size as SizeKey];
  const rule = getPlatform(key.platform).sizes[key.size as SizeKey];
  if (!layout || !rule) throw new CampaignConflict('That size is not supported by this layout and platform.');
  const { copy, animation: _animation, name: _name, ...design } = concept;
  const effectiveCopy = { ...copy.default, ...copy[key.size as SizeKey] };
  const refs = [doc.campaign.brand.logos.primary, doc.campaign.brand.logos.reverse,
    concept.backgroundImage, ...Object.values(concept.hero ?? {}), (effectiveCopy as any).__logoFile].filter(Boolean) as string[];
  const assets = refs.map(ref => {
    const file = path.resolve(assetRoot, ref);
    return [ref, fs.existsSync(file) && fs.statSync(file).isFile()
      ? createHash('sha256').update(fs.readFileSync(file)).digest('hex') : 'unavailable'];
  });
  return digest({ brand: doc.campaign.brand, design, copy: effectiveCopy, layout, rule, assets });
}

export function readCampaign(file: string): CampaignDocument {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

/** Synchronous compare-and-write: no asynchronous gap between version/lock
 * checks and replacement in the service's single writer process. */
export function saveCampaignDocument(file: string, next: CampaignDocument, revision: string | undefined,
  project: Project | null | undefined, assetRoot: string): string {
  const current = readCampaign(file);
  if (!revision || revision !== campaignRevision(current)) {
    throw new CampaignConflict('This campaign changed in another session. Your edits are still on screen. Reload the latest version before saving.');
  }
  if (next.campaign?.requestId !== current.campaign.requestId) throw new CampaignConflict('The campaign identity cannot be changed.');
  for (const approval of project?.approvals ?? []) {
    const before = approval.inputHash ?? artworkFingerprint(current, approval, assetRoot);
    if (before !== artworkFingerprint(next, approval, assetRoot)) {
      throw new CampaignConflict(`${approval.size} on ${approval.platform} is approved. Unapprove it before changing artwork that affects it.`);
    }
  }
  // Browser-only response metadata must not become the stored source of truth.
  const stored = { ...current, campaign: next.campaign, platforms: next.platforms ?? current.platforms };
  const temp = `${file}.${randomUUID()}.tmp`;
  try {
    fs.writeFileSync(temp, JSON.stringify(stored, null, 2));
    fs.renameSync(temp, file);
  } finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
  return campaignRevision(stored);
}
