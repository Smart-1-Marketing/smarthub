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
 *     animated/<client>_300x250_animated.gif (present only when somebody built
 *                                           one after the static design was
 *                                           saved -- an EXTRA version of an ad
 *                                           already in the platform folders,
 *                                           never a replacement for it)
 *     campaign-manifest.json               (the same delivery for a machine:
 *                                           the ad ops person trafficking it
 *                                           was inferring platform and size
 *                                           from filenames)
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
import type { AnimationRecord, Project } from './projects';
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
  /** Animated versions riding along beside the static files. Counted apart
   *  from fileCount, because "8 files delivered" about a pack containing five
   *  ads and three GIFs is a sentence a client reads as eight ads. */
  animatedCount: number;
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
  animated: AnimationRecord[],
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
  if (animated.length) {
    lines.push('');
    lines.push('ANIMATED VERSIONS');
    lines.push('-----------------');
    lines.push('In animated/. These are the SAME ads with motion on them, not');
    lines.push('replacements — upload the static file anywhere an animated one is');
    lines.push('not accepted. Each runs at 5 frames a second or slower, stops');
    lines.push('animating within 30 seconds, and is inside the placement\'s file');
    lines.push('weight, which is what Google requires of an animated image ad.');
    for (const a of animated) {
      lines.push(
        `${clientSlug}_${a.size}_animated.gif  ·  ${a.platform}  ·  ${a.frames} frames  ·  ` +
        `plays ${a.loop}x, ${(a.totalMs / 1000).toFixed(1)}s in total  ·  ${human(a.bytes)}`,
      );
    }
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

/**
 * The same delivery, for a machine.
 *
 * README.txt answers "what am I looking at" for the person who opens the zip.
 * This answers "what is in here" for the ad operations person trafficking it,
 * who is reading twenty of these a week and pulling platform, size and weight
 * into a sheet -- and, before this existed, was inferring all three from
 * filenames. It is built from the same `shipped` and `skipped` arrays the
 * README is built from, in the same function call, which is the only thing
 * that stops the two documents in one zip disagreeing about what shipped.
 *
 * Three things it is careful about:
 *
 * The unit is a FILE IN THE ZIP, not a render. One creative that satisfies
 * Google and Amazon is written into both platform folders -- deliverProject
 * has always done that -- so a manifest listing it once would not account for
 * the file count of the folder it describes. Each row carries the path it
 * sits at, and `sharedWith` names the other platforms carrying the identical
 * file, so nobody double-counts a buy either.
 *
 * `size` and `delivered` are both present and are not the same claim. Amazon
 * takes 320x50 as a 640x100 file; a sheet keyed on the size a media plan was
 * bought at needs the first, and anyone checking the pixels in front of them
 * needs the second.
 *
 * Withheld sizes are in the file. A manifest listing only what shipped reads
 * as a complete delivery that happens to be short, and "the 728x90 is missing"
 * is then a question that comes back to us days later. Absent is not the same
 * as failed, and each one says which it was.
 *
 * No local path is carried. This goes to a client and to a media partner, and
 * `localFile` is a path on our render disk.
 */
function campaignManifest(
  project: Project,
  concept: string,
  clientSlug: string,
  root: string,
  shipped: { entry: ManifestEntry; overridden: boolean; finalFile: string }[],
  skipped: { size: string; reason: string }[],
  animated: AnimationRecord[],
): string {
  const assets = [];
  for (const s of shipped) {
    const e = s.entry;
    const ext = path.extname(s.finalFile) || `.${e.format}`;
    const targets = e.platforms && e.platforms.length ? e.platforms : [e.platform];
    const bytes = fs.statSync(s.finalFile).size;
    for (const platform of targets) {
      assets.push({
        file: `${root}/${platform}/${clientSlug}_${e.deliveredDimensions}${ext}`,
        platform,
        /** The size as bought and as the layout is named. */
        size: e.size,
        /** The pixels actually in the file, which differ wherever a platform
         *  requires 2x. */
        delivered: e.deliveredDimensions,
        format: e.format,
        bytes,
        qa: e.qaStatus,
        qaIssues: e.qaIssues,
        /** Present only where the same file serves more than one platform. */
        sharedWith: targets.filter((p) => p !== platform),
        /** A hand-edited replacement shipped in place of the render. Named
         *  because a tweaked file turning up in the package should never be a
         *  surprise later. */
        manuallyEdited: s.overridden,
      });
    }
  }

  return JSON.stringify(
    {
      client: project.client,
      campaign: project.campaignName,
      concept,
      landingPage: project.landingPage ?? null,
      preparedBy: 'Smart 1 Marketing',
      deliveredAt: new Date().toISOString(),
      totals: {
        // Files in the zip, and the renders behind them. They differ whenever
        // one creative serves two platforms, and reporting only one of them
        // is how a count on a screen fails to match the folder.
        files: assets.length,
        creatives: shipped.length,
        withheld: skipped.length,
        animated: animated.length,
        bytes: assets.reduce((n, a) => n + a.bytes, 0),
      },
      assets,
      // Listed apart from `assets` on purpose. An animated file is an EXTRA
      // version of an ad that is already in `assets`, not a ninth ad -- folded
      // in, it would double the creative count on any sheet keyed on this
      // file, and an ops person would traffic the same placement twice.
      animated: animated.map((a) => ({
        file: `${root}/animated/${clientSlug}_${a.size}_animated.gif`,
        platform: a.platform,
        size: a.size,
        format: 'gif',
        bytes: a.bytes,
        frames: a.frames,
        loop: a.loop,
        totalSeconds: Number((a.totalMs / 1000).toFixed(1)),
        fps: a.fps,
        motion: a.kind,
        qa: a.status,
        qaIssues: a.issues,
      })),
      withheld: skipped.map((s) => ({ size: s.size, reason: s.reason })),
    },
    null,
    2,
  );
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

  /* Animated versions ride along, under the same two rules the static files
     follow and for the same reasons.

     A QA-FAILING ANIMATION IS WITHHELD. Delivering a GIF that is over the file
     weight, or whose second slide is clipped, is worse than delivering the
     static ad on its own -- the static one runs, and the client can see for
     themselves that the animation is missing.

     IT NEVER REPLACES ITS STATIC SIBLING. Most of this Hub's placements do not
     take an animated file at all, so a folder holding only GIFs is a set that
     cannot be trafficked. They sit in animated/ and the README says which is
     which. */
  const animated = (project.animations ?? []).filter((a) => {
    if (a.conceptId !== concept) return false;
    if (a.status === 'fail') {
      skipped.push({
        size: `${a.platform}/${a.size} (animated)`,
        reason: 'the animated version did not pass creative checks and was withheld; the static file is included',
      });
      return false;
    }
    if (!fs.existsSync(a.file)) {
      skipped.push({
        size: `${a.platform}/${a.size} (animated)`,
        reason: 'the animated file is no longer on disk — animate again and deliver',
      });
      return false;
    }
    return true;
  });

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
  for (const a of animated) {
    zipEntries.push({
      name: `${root}/animated/${clientSlug}_${a.size}_animated.gif`,
      data: fs.readFileSync(a.file),
    });
  }
  zipEntries.push({
    name: `${root}/README.txt`,
    data: Buffer.from(readme(project, concept, clientSlug, shipped, skipped, animated), 'utf8'),
  });
  zipEntries.push({
    name: `${root}/campaign-manifest.json`,
    data: Buffer.from(
      campaignManifest(project, concept, clientSlug, root, shipped, skipped, animated),
      'utf8',
    ),
  });
  fs.writeFileSync(zipFile, buildZip(zipEntries));

  return {
    zipFile,
    zipUrl: `/files/deliveries/${zipName}`,
    fileCount: shipped.length,
    animatedCount: animated.length,
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
