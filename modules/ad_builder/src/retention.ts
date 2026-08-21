/**
 * Local file retention.
 *
 * Cloudinary is the permanent home for finished creatives; the copies on this
 * disk exist only long enough to be uploaded and proofed. Nothing pruned them,
 * which on a shared box means this service slowly eats the volume its
 * neighbours live on.
 *
 * Deliberately conservative: it only ever removes rendered output and cached
 * downloads, never project records, campaigns, manifests or requests. Those
 * are small and are the audit trail.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

export interface SweepOptions {
  outDir: string;
  /** Delete rendered creatives older than this. */
  renderDays?: number;
  /** Delete cached remote assets older than this. */
  cacheDays?: number;
  /** Report what would be removed without removing it. */
  dryRun?: boolean;
}

export interface SweepResult {
  scanned: number;
  removed: number;
  bytesFreed: number;
  dryRun: boolean;
  details: string[];
}

/** Directories safe to prune, with their own retention windows. */
const PRUNABLE = [
  { sub: 'google', kind: 'render' as const },
  { sub: 'amazon', kind: 'render' as const },
  { sub: 'cache', kind: 'cache' as const },
  // Completed/failed job records are debug history once terminal; queued or
  // running ones are far younger than the retention window and untouched.
  { sub: 'jobs', kind: 'cache' as const },
];

/** Never touched, whatever their age. */
const PROTECTED = new Set(['projects', 'campaigns', 'requests', 'reports']);

function walk(dir: string, onFile: (f: string, stat: fs.Stats) => void): void {
  let entries: fs.Dirent[];
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, onFile);
    else {
      try { onFile(full, fs.statSync(full)); } catch { /* raced with another sweep */ }
    }
  }
}

/** Remove now-empty directories left behind, deepest first. */
function pruneEmptyDirs(dir: string): void {
  let entries: fs.Dirent[];
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    if (e.isDirectory()) pruneEmptyDirs(path.join(dir, e.name));
  }
  try {
    if (fs.readdirSync(dir).length === 0) fs.rmdirSync(dir);
  } catch { /* not empty, or in use */ }
}

export function sweep(opts: SweepOptions): SweepResult {
  const { outDir, renderDays = 30, cacheDays = 7, dryRun = false } = opts;
  const now = Date.now();
  const result: SweepResult = { scanned: 0, removed: 0, bytesFreed: 0, dryRun, details: [] };

  for (const { sub, kind } of PRUNABLE) {
    if (PROTECTED.has(sub)) continue;
    const dir = path.join(outDir, sub);
    if (!fs.existsSync(dir)) continue;

    const maxAgeMs = (kind === 'cache' ? cacheDays : renderDays) * 86_400_000;

    walk(dir, (file, stat) => {
      result.scanned++;
      const age = now - stat.mtimeMs;
      if (age < maxAgeMs) return;
      result.bytesFreed += stat.size;
      result.removed++;
      if (result.details.length < 20) {
        result.details.push(`${path.relative(outDir, file)} (${Math.round(age / 86_400_000)}d)`);
      }
      if (!dryRun) {
        try { fs.unlinkSync(file); } catch { /* already gone */ }
      }
    });

    if (!dryRun) pruneEmptyDirs(dir);
  }

  return result;
}

/** Run on a schedule. Returns the timer so callers can unref it. */
export function scheduleSweep(opts: SweepOptions, everyMs = 24 * 3_600_000): NodeJS.Timeout {
  const run = () => {
    try {
      const r = sweep(opts);
      if (r.removed) {
        console.log(`[retention] removed ${r.removed} file(s), freed ${(r.bytesFreed / 1048576).toFixed(1)} MB`);
      }
    } catch (e: any) {
      console.warn(`[retention] sweep failed: ${e?.message ?? e}`);
    }
  };
  // Not on boot: a deploy loop would otherwise sweep repeatedly.
  const timer = setInterval(run, everyMs);
  return timer;
}
