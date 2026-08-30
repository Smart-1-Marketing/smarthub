/**
 * What is actually in the zip a client receives.
 *
 * Two documents describe one delivery -- README.txt for the person who opens
 * it, campaign-manifest.json for the ad operations person trafficking twenty
 * of them a week -- and the failure worth testing for is not that either is
 * malformed. It is that they disagree: one saying eight files shipped while
 * the other lists seven, or a size withheld from the folder appearing in
 * neither. So these assertions read the zip back and check the manifest
 * against the bytes actually in it, not against the arrays it was built from.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as zlib from 'zlib';
import { deliverProject } from '../src/deliver';
import type { Manifest } from '../src/manifest';
import type { Project } from '../src/projects';

/** Read a STORE/DEFLATE zip back into { name: Buffer } via its central directory. */
function readZip(buf: Buffer): Record<string, Buffer> {
  const out: Record<string, Buffer> = {};
  // End-of-central-directory, scanned from the back.
  let eocd = buf.length - 22;
  while (eocd >= 0 && buf.readUInt32LE(eocd) !== 0x06054b50) eocd--;
  assert.ok(eocd >= 0, 'zip has an end-of-central-directory record');
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  for (let i = 0; i < count; i++) {
    assert.equal(buf.readUInt32LE(p), 0x02014b50, 'central directory entry');
    const method = buf.readUInt16LE(p + 10);
    const compSize = buf.readUInt32LE(p + 20);
    const nameLen = buf.readUInt16LE(p + 28);
    const extraLen = buf.readUInt16LE(p + 30);
    const commentLen = buf.readUInt16LE(p + 32);
    const local = buf.readUInt32LE(p + 42);
    const name = buf.subarray(p + 46, p + 46 + nameLen).toString('utf8');
    const lNameLen = buf.readUInt16LE(local + 26);
    const lExtraLen = buf.readUInt16LE(local + 28);
    const start = local + 30 + lNameLen + lExtraLen;
    const raw = buf.subarray(start, start + compSize);
    out[name] = method === 0 ? raw : zlib.inflateRawSync(raw);
    p += 46 + nameLen + extraLen + commentLen;
  }
  return out;
}

/** A rendered project on disk: two creatives that ship, one that must not. */
function fixture() {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'deliver-'));
  const art = path.join(outDir, 'art');
  fs.mkdirSync(art, { recursive: true });

  const file = (n: string) => {
    const f = path.join(art, n);
    fs.writeFileSync(f, Buffer.from(`fake-image-${n}`.repeat(8)));
    return f;
  };

  const manifest: Manifest = {
    requestId: 'req-1',
    client: 'Icon Solar',
    campaign: 'Summer',
    projectFolder: 'icon-solar',
    generatedAt: new Date().toISOString(),
    uploaded: false,
    dryRun: true,
    totals: { creatives: 3, uploadedCount: 0, pass: 2, warn: 0, fail: 1, bytes: 0 },
    entries: [
      {
        conceptId: 'A', conceptName: 'Split', layoutFamily: 'T01',
        // One creative satisfying two platforms — it is written into both
        // folders, so the manifest must account for two files.
        platform: 'google', platforms: ['google', 'amazon'],
        size: '300x250', deliveredDimensions: '300x250', format: 'jpg',
        bytes: 100, wordCount: 9, qaStatus: 'pass', qaIssues: [],
        localFile: file('a.jpg'),
      },
      {
        conceptId: 'A', conceptName: 'Split', layoutFamily: 'T01',
        // Amazon takes this one at 2x, so the size bought and the pixels
        // delivered are different claims.
        platform: 'amazon', platforms: ['amazon'],
        size: '320x50', deliveredDimensions: '640x100', format: 'jpg',
        bytes: 100, wordCount: 5, qaStatus: 'warn', qaIssues: ['legibility: tight'],
        localFile: file('b.jpg'),
      },
      {
        conceptId: 'A', conceptName: 'Split', layoutFamily: 'T01',
        platform: 'google', platforms: ['google'],
        size: '728x90', deliveredDimensions: '728x90', format: 'jpg',
        bytes: 100, wordCount: 7, qaStatus: 'fail', qaIssues: ['overflow: cta'],
        localFile: file('c.jpg'),
      },
    ],
  } as Manifest;

  const reports = path.join(outDir, 'reports');
  fs.mkdirSync(reports, { recursive: true });
  fs.writeFileSync(path.join(reports, 'manifest_req-1.json'), JSON.stringify(manifest));

  const project = {
    projectName: 'Icon Solar Summer', projectId: 'icon-solar-summer',
    requestId: 'req-1', client: 'Icon Solar', domain: 'iconsolar.com',
    campaignName: 'Summer', createdAt: '', updatedAt: '', status: 'approved',
    landingPage: 'https://iconsolar.com/summer', approvedConcept: 'A',
    batches: [],
  } as unknown as Project;

  return { outDir, project };
}

async function deliver() {
  const { outDir, project } = fixture();
  const result = await deliverProject(project, { outDir });
  const files = readZip(fs.readFileSync(result.zipFile));
  const manifestName = Object.keys(files).find((n) => n.endsWith('campaign-manifest.json'))!;
  return { result, files, manifest: JSON.parse(files[manifestName].toString('utf8')) };
}

test('the zip carries a manifest beside the readme', async () => {
  const { files } = await deliver();
  const names = Object.keys(files);
  assert.ok(names.some((n) => n.endsWith('README.txt')), 'README.txt ships');
  assert.ok(names.some((n) => n.endsWith('campaign-manifest.json')), 'the manifest ships');
  // Both at the root of the one campaign folder, not loose in the zip.
  const roots = new Set(names.map((n) => n.split('/')[0]));
  assert.equal(roots.size, 1, `one campaign folder, got ${[...roots]}`);
});

test('the manifest accounts for every image in the zip, and only those', async () => {
  const { files, manifest } = await deliver();
  const images = Object.keys(files).filter((n) => /\.(jpg|png)$/.test(n)).sort();
  const listed = manifest.assets.map((a: { file: string }) => a.file).sort();
  assert.deepEqual(listed, images, 'every listed path is a file in the zip and vice versa');
  assert.equal(manifest.totals.files, images.length);
});

test('a creative serving two platforms is filed and counted under both', async () => {
  const { files, manifest } = await deliver();
  assert.ok(Object.keys(files).some((n) => n.includes('/google/')), 'google folder');
  assert.ok(Object.keys(files).some((n) => n.includes('/amazon/')), 'amazon folder');

  const shared = manifest.assets.filter((a: { size: string }) => a.size === '300x250');
  assert.equal(shared.length, 2, 'one creative, two platform folders, two rows');
  assert.deepEqual(
    shared.map((a: { platform: string }) => a.platform).sort(),
    ['amazon', 'google'],
  );
  for (const a of shared) {
    assert.ok(a.sharedWith.length === 1, 'each row names the other platform carrying it');
  }
  // Files and creatives are different numbers and both are reported, or a
  // count on a screen fails to match the folder it describes.
  assert.equal(manifest.totals.creatives, 2);
  assert.equal(manifest.totals.files, 3);
});

test('the size bought and the pixels delivered are both stated', async () => {
  const { manifest } = await deliver();
  const mobile = manifest.assets.find((a: { size: string }) => a.size === '320x50');
  assert.ok(mobile, 'the 2x size shipped');
  assert.equal(mobile.size, '320x50', 'what the media plan bought');
  assert.equal(mobile.delivered, '640x100', 'what is actually in the file');
});

test('a withheld size is named in the manifest, not merely absent from it', async () => {
  const { files, manifest } = await deliver();
  assert.ok(
    !Object.keys(files).some((n) => n.includes('728x90')),
    'the QA-failing size is not in the folder',
  );
  assert.equal(manifest.withheld.length, 1);
  assert.match(manifest.withheld[0].size, /728x90/);
  assert.match(manifest.withheld[0].reason, /creative checks/);
  assert.equal(manifest.totals.withheld, 1);
});

test('the manifest and the readme agree about what shipped', async () => {
  const { files, manifest } = await deliver();
  const readme = files[Object.keys(files).find((n) => n.endsWith('README.txt'))!].toString('utf8');
  for (const a of manifest.assets) {
    assert.ok(
      readme.includes(a.delivered),
      `README names ${a.delivered}, which the manifest lists`,
    );
  }
  for (const w of manifest.withheld) {
    assert.ok(readme.includes('NOT INCLUDED'), 'the README has a withheld section');
    assert.ok(readme.includes(w.size.split('/').pop()!), `README names withheld ${w.size}`);
  }
});

test('no path from our render disk travels to the client', async () => {
  const { files, manifest } = await deliver();
  const raw = JSON.stringify(manifest);
  assert.ok(!raw.includes(os.tmpdir()), 'no local render path in the manifest');
  assert.ok(!/"localFile"/.test(raw), 'localFile is not carried');
  const readme = files[Object.keys(files).find((n) => n.endsWith('README.txt'))!].toString('utf8');
  assert.ok(!readme.includes(os.tmpdir()), 'nor in the README');
});
