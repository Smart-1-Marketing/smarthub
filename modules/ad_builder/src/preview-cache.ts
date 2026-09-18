/**
 * Preview cache.
 *
 * The build screen fires /api/preview on every edit and on every size
 * switch; switching back to a size the person already rendered used to
 * re-run sharp on inputs that were byte-for-byte the same. This is a small
 * LRU keyed on the campaign JSON plus the concept, size and platform,
 * holding at most 24 responses for 60 seconds each. A brand discovery
 * result or a copy edit is a new campaign JSON, so the key misses on it:
 * cached hits are only for size switches and repeated requests during the
 * same edit session.
 *
 * The cache is bypassed when a manual override is in play; overrides are
 * stored on the project and can change without the campaign JSON changing,
 * so the key would be a stale answer.
 */
import * as crypto from 'node:crypto';

export const PREVIEW_CACHE_MAX = 24;
export const PREVIEW_CACHE_TTL_MS = 60_000;

export class PreviewCache {
  // Kept package-visible so a stale row can be aged in tests without a real
  // sleep; nothing else in the module reads it.
  readonly map = new Map<string, { at: number; body: any }>();
  get(key: string): any | null {
    const hit = this.map.get(key);
    if (!hit) return null;
    if (Date.now() - hit.at > PREVIEW_CACHE_TTL_MS) { this.map.delete(key); return null; }
    // LRU: move to the newest slot on read.
    this.map.delete(key); this.map.set(key, hit);
    return hit.body;
  }
  set(key: string, body: any): void {
    if (this.map.has(key)) this.map.delete(key);
    this.map.set(key, { at: Date.now(), body });
    while (this.map.size > PREVIEW_CACHE_MAX) this.map.delete(this.map.keys().next().value!);
  }
  clear(): void { this.map.clear(); }
  size(): number { return this.map.size; }
}

export const previewCache = new PreviewCache();

export function previewCacheKey(campaign: any, conceptId: string, size: string, platform: string): string {
  return crypto.createHash('sha256')
    .update(JSON.stringify(campaign))
    .update('|' + conceptId + '|' + size + '|' + platform)
    .digest('hex');
}
