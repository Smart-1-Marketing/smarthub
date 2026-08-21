/**
 * Brandfetch cache.
 *
 * Brand records rarely change, and every lookup costs a Brandfetch API call.
 * We cache each domain's discovery result on disk with the time it was
 * fetched, and only re-hit the API when the cached copy is older than the TTL
 * (three months by default). This also gives us a durable log of the website
 * and brand details we pulled for each domain, which is useful long after the
 * campaign that triggered the lookup.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

export const BRANDFETCH_TTL_MS = 90 * 24 * 60 * 60 * 1000; // ~3 months

export interface CachedBrand<T = unknown> {
  domain: string;
  fetchedAt: string;
  /** The full discovery payload (brand, colours, fonts, logos, source). */
  result: T;
}

export class BrandCache {
  constructor(private readonly dir: string) {
    fs.mkdirSync(dir, { recursive: true });
  }

  private file(domain: string): string {
    const safe = domain.toLowerCase().replace(/[^a-z0-9.-]+/g, '_');
    return path.join(this.dir, `${safe}.json`);
  }

  /** Return the cached record for a domain, or null if absent/unreadable. */
  read<T = unknown>(domain: string): CachedBrand<T> | null {
    try {
      const raw = fs.readFileSync(this.file(domain), 'utf8');
      return JSON.parse(raw) as CachedBrand<T>;
    } catch {
      return null;
    }
  }

  /** True when there is a cached record and it is still within the TTL. */
  isFresh(domain: string, ttlMs = BRANDFETCH_TTL_MS): boolean {
    const rec = this.read(domain);
    if (!rec) return false;
    const age = Date.now() - new Date(rec.fetchedAt).getTime();
    return Number.isFinite(age) && age >= 0 && age < ttlMs;
  }

  /** Age of the cached record in days, or null if none. */
  ageDays(domain: string): number | null {
    const rec = this.read(domain);
    if (!rec) return null;
    return Math.floor((Date.now() - new Date(rec.fetchedAt).getTime()) / 86_400_000);
  }

  /** Store (or replace) the discovery result for a domain. */
  write<T = unknown>(domain: string, result: T): CachedBrand<T> {
    const rec: CachedBrand<T> = { domain, fetchedAt: new Date().toISOString(), result };
    fs.writeFileSync(this.file(domain), JSON.stringify(rec, null, 2));
    return rec;
  }

  /**
   * Return a fresh cached result if one exists, otherwise run `fetcher`, cache
   * its result, and return it. `refresh` forces a re-fetch regardless of age.
   */
  async getOrFetch<T = unknown>(
    domain: string,
    fetcher: () => Promise<T>,
    opts: { ttlMs?: number; refresh?: boolean } = {},
  ): Promise<{ result: T; cached: boolean; ageDays: number | null }> {
    if (!opts.refresh && this.isFresh(domain, opts.ttlMs)) {
      const rec = this.read<T>(domain)!;
      return { result: rec.result, cached: true, ageDays: this.ageDays(domain) };
    }
    const result = await fetcher();
    this.write(domain, result);
    return { result, cached: false, ageDays: 0 };
  }
}
