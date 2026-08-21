/**
 * Final delivery.
 *
 * The end of the pipeline that was missing: after a client approves, someone
 * still has to hand over finished files. Without this step that meant fishing
 * individual PNGs out of folders — the one part of the flow that stayed
 * manual, and the part a client actually receives.
 *
 * deliverProject() packages the approved concept into one zip:
 *
 *   <client>-<campaign>/
 *     google/<client>_300x250.jpg          (files named for the platform ops
 *     google/<client>_728x90.jpg            person who uploads them, not for
 *     ...                                   our internal request ids)
 *     README.txt                           (what each file is, its delivered
 *                                           pixel size, weight, and where it
 *                                           is allowed to run)
 *
 * Two rules that matter:
 *
 *   Overrides win. If a size has a manually-edited replacement on the
 *   project, the override ships and the rendered original does not. The
 *   README says so — a hand-tweaked file in the package should never be a
 *   surprise later.
 *
 *   Failing creatives never ship. A QA-failing size is skipped and listed in
 *   the README under "not included", because silently delivering a broken ad
 *   is worse than delivering one size short.
 *
 * The zip is written by a ~60-line STORE-method writer below rather than a
 * dependency: the payload is already-compressed JPG/PNG, so zip compression
 * would buy nothing, `archiver` is ESM-only against this CommonJS build, and
 * shelling out assumes a zip binary the host may not have. STORE is part of
 * the original 1989 PKZIP spec and every extractor ever written opens it.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import * as zlib from 'node:zlib';
import type { Manifest, ManifestEntry } from './manifest';
import type { Project } from './projects';
import { slug } from './cloudinary';

export interface DeliverOptions {
  outDir: string;
  /** Concept to deliver. Defaults to the one recorded at approval. */
  conceptId?: string;
}

export interface DeliverResult {
  zipFile: string;          // absolute path
  zipUrl: string;           // /files/... path for the browser
  fileCount: number;
  overrideCount: number;
  skipped: { size: string; reason: string }[];
  bytes: number;
}

/** The newest manifest for a request, or null if nothing has rendered. */
export function latestManifest(outDir: string, requestId: string): Manifest | null {
  const file = path.join(outDir, 'reports', `manifest_${requestId}.json`);
  if (!fs.existsSync(file)) return null;
  return JSON.parse(fs.readFileSync(file, 'utf8')) as Manifest;
}

function human(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1048576).toFixed(1)} MB`
    : `${(bytes / 1024).toFixed(1)} KB`;
}

function readme(
  project: Project,
  concept: string,
  clientSlug: string,
  shipped: { entry: ManifestEntry; overridden: boolean; finalFile: string }[],
  skipped: { size: string; reason: string }[],
): string {
  const lines: string[] = [];
  lines.push(`${project.client} — ${project.campaignName}`);
  lines.push(`Concept ${concept} · delivered ${new Date().toISOString().slice(0, 10)}`);
  lines.push(`Prepared by Smart 1 Marketing`);
  lines.push('');
  lines.push('FILES');
  lines.push('-----');
  for (const s of shipped) {
    const e = s.entry;
    lines.push(
      `${clientSlug}_${e.deliveredDimensions}${path.extname(s.finalFile)}` +
      `  ·  ${(e.platforms ?? [e.platform]).join(' + ')}  ·  ${e.deliveredDimensions}px` +
      (e.deliveredDimensions !== e.size ? ` (${e.size} at 2x)` : '') +
      `  ·  ${human(fs.statSync(s.finalFile).size)}` +
      (s.overridden ? '  ·  MANUALLY EDITED replacement supplied by Smart 1' : ''),
    );
  }
  if (skipped.length) {
    lines.push('');
    lines.push('NOT INCLUDED');
    lines.push('------------');
    for (const s of skipped) lines.push(`${s.size}: ${s.reason}`);
  }
  lines.push('');
  lines.push('NOTES');
  lines.push('-----');
  lines.push('Files are named <client>_<size> and sized at each platform\'s required');
  lines.push('delivery scale (some mobile sizes ship at 2x resolution).');
  lines.push('Upload each file only to the platform folder it sits in.');
  if (project.landingPage) lines.push(`Click-through destination: ${project.landingPage}`);
  return lines.join('\n');
}

export async function deliverProject(
  project: Project,
  opts: DeliverOptions,
): Promise<DeliverResult> {
  const manifest = latestManifest(opts.outDir, project.requestId);
  if (!manifest) throw new Error('Nothing has been rendered for this project yet.');

  const concept =
    opts.conceptId ??
    project.approvedConcept ??
    manifest.entries[0]?.conceptId;
  if (!concept) throw new Error('No concept to deliver.');

  const entries = manifest.entries.filter((e) => e.conceptId === concept);
  if (!entries.length) throw new Error(`Concept ${concept} has no rendered creatives.`);

  const overrides = project.overrides ?? [];
  const clientSlug = slug(project.client);

  const shipped: { entry: ManifestEntry; overridden: boolean; finalFile: string }[] = [];
  const skipped: { size: string; reason: string }[] = [];

  for (const e of entries) {
    const override = overrides.find(
      (o) => o.conceptId === concept && o.size === e.size && o.platform === e.platform,
    );
    if (override && fs.existsSync(override.file)) {
      shipped.push({ entry: e, overridden: true, finalFile: override.file });
      continue;
    }
    if (e.qaStatus === 'fail') {
      skipped.push({ size: `${e.platform}/${e.size}`, reason: 'did not pass creative checks and was withheld' });
      continue;
    }
    if (!fs.existsSync(e.localFile)) {
      skipped.push({ size: `${e.platform}/${e.size}`, reason: 'rendered file no longer on disk — re-render and deliver again' });
      continue;
    }
    shipped.push({ entry: e, overridden: false, finalFile: e.localFile });
  }

  if (!shipped.length) throw new Error('Nothing deliverable: every size was withheld or missing.');

  const deliveriesDir = path.join(opts.outDir, 'deliveries');
  fs.mkdirSync(deliveriesDir, { recursive: true });
  const zipName = `${clientSlug}_${slug(project.campaignName)}_${concept}_${Date.now().toString(36)}.zip`;
  const zipFile = path.join(deliveriesDir, zipName);

  const root = `${clientSlug}-${slug(project.campaignName)}`;
  // A deduplicated creative is filed under every platform it serves, so an ops
  // person uploading to Amazon finds all Amazon sizes in the amazon/ folder
  // even though the file was rendered once. The on-disk NAME reflects the
  // actual delivered pixels, not the size key: Amazon's 320x50 ships at
  // 640x100 (2x), so it must not be labelled 320x50.
  const zipEntries: { name: string; data: Buffer }[] = [];
  for (const s of shipped) {
    const ext = path.extname(s.finalFile) || `.${s.entry.format}`;
    const data = fs.readFileSync(s.finalFile);
    const labelSize = s.entry.deliveredDimensions || s.entry.size; // real pixels
    const targets = s.entry.platforms && s.entry.platforms.length ? s.entry.platforms : [s.entry.platform];
    for (const plat of targets) {
      zipEntries.push({
        name: `${root}/${plat}/${clientSlug}_${labelSize}${ext}`,
        data,
      });
    }
  }
  zipEntries.push({
    name: `${root}/README.txt`,
    data: Buffer.from(readme(project, concept, clientSlug, shipped, skipped), 'utf8'),
  });
  fs.writeFileSync(zipFile, buildZip(zipEntries));

  return {
    zipFile,
    zipUrl: `/files/deliveries/${zipName}`,
    fileCount: shipped.length,
    overrideCount: shipped.filter((s) => s.overridden).length,
    skipped,
    bytes: fs.statSync(zipFile).size,
  };
}


/* ------------------------------------------------------------- zip writer */

/**
 * Minimal ZIP with the STORE method (no compression). Local file header +
 * data per entry, then a central directory, then the end record. CRC-32 via
 * zlib, which Node ships.
 */
function crc32(buf: Buffer): number {
  // zlib exposes crc32 from Node 20.15; fall back to a table implementation
  // for anything older so the build does not silently depend on a point release.
  const z = zlib as unknown as { crc32?: (b: Buffer) => number };
  if (typeof z.crc32 === 'function') return z.crc32(buf) >>> 0;
  let c: number;
  const table = crc32.table ?? (crc32.table = (() => {
    const t = new Int32Array(256);
    for (let n = 0; n < 256; n++) {
      c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c;
    }
    return t;
  })());
  c = 0 ^ -1;
  for (let i = 0; i < buf.length; i++) c = (c >>> 8) ^ table[(c ^ buf[i]) & 0xff];
  return (c ^ -1) >>> 0;
}
// eslint-disable-next-line @typescript-eslint/no-namespace
namespace crc32 { export let table: Int32Array | undefined; }

function dosDateTime(d = new Date()): { date: number; time: number } {
  return {
    time: (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1),
    date: (((d.getFullYear() - 1980) & 0x7f) << 9) | ((d.getMonth() + 1) << 5) | d.getDate(),
  };
}

export function buildZip(entries: { name: string; data: Buffer }[]): Buffer {
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  const { date, time } = dosDateTime();

  for (const e of entries) {
    const name = Buffer.from(e.name, 'utf8');
    const crc = crc32(e.data);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);   // local file header signature
    local.writeUInt16LE(20, 4);           // version needed
    local.writeUInt16LE(0x0800, 6);       // flags: UTF-8 names
    local.writeUInt16LE(0, 8);            // method: STORE
    local.writeUInt16LE(time, 10);
    local.writeUInt16LE(date, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(e.data.length, 18);
    local.writeUInt32LE(e.data.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(0, 28);           // extra length
    locals.push(local, name, e.data);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0); // central directory signature
    central.writeUInt16LE(20, 4);         // version made by
    central.writeUInt16LE(20, 6);         // version needed
    central.writeUInt16LE(0x0800, 8);     // flags: UTF-8 names
    central.writeUInt16LE(0, 10);         // method: STORE
    central.writeUInt16LE(time, 12);
    central.writeUInt16LE(date, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(e.data.length, 20);
    central.writeUInt32LE(e.data.length, 24);
    central.writeUInt16LE(name.length, 28);
    // extra(30), comment(32), disk(34), int attrs(36) all zero
    central.writeUInt32LE(0, 38);         // external attrs
    central.writeUInt32LE(offset, 42);    // local header offset
    centrals.push(central, name);

    offset += 30 + name.length + e.data.length;
  }

  const centralSize = centrals.reduce((n, b) => n + b.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);       // end of central directory
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);

  return Buffer.concat([...locals, ...centrals, end]);
}
