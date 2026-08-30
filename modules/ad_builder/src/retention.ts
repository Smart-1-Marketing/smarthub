/**
 * Local file retention.
 *
 * Cloudinary is the permanent home for finished creatives; the copies on this
 * disk exist only long enough to be uploaded and proofed. Nothing pruned them,
 * which on a shared box means this service slowly eats the volume its
 * neighbours live on.
 *
 * Deliberately conservative: it only ever removes rendered output, generated
 * drafts and cached downloads, never project records, campaigns, manifests,
 * requests or delivered packs. Those are small, or somebody is holding a link
 * to them.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import { loadPlatforms } from './registry';

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

/**
 * Directories safe to prune, with their own retention windows.
 *
 * The render directories are **read from the platform registry**, never typed
 * out. `render.ts` writes to `<outDir>/<platform>/<concept>`, so a list
 * spelled here is a second answer to which platforms exist -- and this module
 * had `google` and `amazon` in it while `meta.json` sat in the registry being
 * rendered. Nothing errored: Meta creative simply accumulated on the volume
 * for ever, on the one module whose whole job is to stop that. That is the
 * fourth copy of a hardcoded platform list this app has had, after the three
 * `.filter(p => p === 'google' || p === 'amazon')` calls that dropped a Meta
 * buy outright. A platform added next month is swept without anybody
 * remembering.
 */
function prunable(): { sub: string; kind: 'render' | 'cache' }[] {
  const platforms = [...loadPlatforms().keys()].map(
    (sub) => ({ sub, kind: 'render' as const }),
  );
  return [
    ...platforms,
    { sub: 'cache', kind: 'cache' as const },
    // Completed/failed job records are debug history once terminal; queued or
    // running ones are far younger than the retention window and untouched.
    { sub: 'jobs', kind: 'cache' as const },
    // Generated pictures are drafts. `POST /api/imagery/keep` is the press
    // that moves one to Cloudinary, and the reason that route exists at all
    // is that the draft on this disk does not last -- so a gallery row
    // pointing at it would open today and 404 after the sweep. That was the
    // written contract and the sweep did not honour it: `imagery/` was in no
    // list, so every generated hero stayed for ever. A rule the code does not
    // keep is worse than no rule, because it is the rule people reason from.
    { sub: 'imagery', kind: 'cache' as const },
  ];
}

/**
 * Never touched, whatever their age.
 *
 * `deliveries` is here rather than merely absent, and the difference matters:
 * an omission reads as an oversight and gets 'fixed'. A delivered pack is the
 * file behind the download button on the proof, which stays there however many
 * times the client opens the page -- sweeping it turns a working link into a
 * 404 for the one person the whole tool is for. It is bounded in practice
 * because a campaign is delivered once.
 */
const PROTECTED = new Set(['projects', 'campaigns', 'requests', 'reports', 'deliveries']);

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

  for (const { sub, kind } of prunable()) {
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
