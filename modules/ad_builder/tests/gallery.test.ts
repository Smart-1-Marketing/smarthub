/**
 * The gallery CLI: one reading of the folder, and a path that is relative to
 * where the page actually landed.
 *
 * Two defects, both of which printed a success line and produced something
 * wrong -- the shape this module keeps finding.
 *
 * **The folder was one string with three readers.** `folderExpression()`
 * trimmed a trailing slash for the search; the heading and the output
 * filename took the raw value. The README's own folder tree prints the folder
 * *with* a trailing slash, so pasting it found exactly the right assets and
 * then wrote them to `gallery_.html` -- the same file for every project,
 * each run overwriting the last -- under a heading that had dropped the
 * client and ended in a dash. Nothing errored at any point.
 *
 * **A relative path is relative to the page that carries it.** A dry run's
 * manifest has no hosted URL, so a simulated asset is drawn from a file on
 * this disk. That path was computed against `out/reports` whatever `--out`
 * had been given, so a gallery written anywhere else had every image broken
 * and still printed the file it had written.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as path from 'path';
import { assetsFromManifest, galleryFile, galleryTitle } from '../src/gallery';
import { folderExpression, normalizeFolder } from '../src/cloudinary';
import type { Manifest } from '../src/manifest';

const MANIFEST: Manifest = {
  requestId: 'AD-2026-000001',
  client: 'Icon Solar',
  campaign: 'Summer Solar',
  projectFolder: 'smart1-ads/icon-solar/summer-solar',
  generatedAt: '2026-08-31T10:00:00.000Z',
  uploaded: false,
  dryRun: true,
  totals: { creatives: 2, uploadedCount: 1, pass: 1, warn: 0, fail: 1, bytes: 12345 },
  entries: [
    {
      conceptId: 'a', conceptName: 'Concept A', layoutFamily: 'hero',
      platform: 'google', size: 'medium_rectangle' as Manifest['entries'][0]['size'],
      deliveredDimensions: '300x250', format: 'png', bytes: 12345,
      wordCount: 9, qaStatus: 'pass', qaIssues: [],
      localFile: '/srv/ads/out/google/concept-a/mr.png',
      cloudinary: {
        publicId: 'smart1-ads/icon-solar/summer-solar/final/google/concept-a/mr',
        assetFolder: 'smart1-ads/icon-solar/summer-solar/final/google/concept-a',
        secureUrl: 'https://res.cloudinary.com/x/image/upload/v1/mr.png',
        version: 1, simulated: true,
      },
    },
    {
      conceptId: 'b', conceptName: 'Concept B', layoutFamily: 'hero',
      platform: 'google', size: 'leaderboard' as Manifest['entries'][0]['size'],
      deliveredDimensions: '728x90', format: 'png', bytes: 4321,
      wordCount: 6, qaStatus: 'fail', qaIssues: ['headline clipped'],
      localFile: '/srv/ads/out/google/concept-b/lb.png',
      // No cloudinary block: it failed QA and was withheld from upload.
    },
  ],
};

/* ------------------------------------------------- one reading of a folder */

test('a trailing slash names the same folder, so it names the same file', () => {
  // The README's folder tree prints `smart1-ads/icon-solar/summer-solar/`.
  // Taken raw, `.split('/').pop()` is the empty string.
  const withSlash = galleryFile('smart1-ads/icon-solar/summer-solar/', undefined, '/R');
  const without = galleryFile('smart1-ads/icon-solar/summer-solar', undefined, '/R');

  assert.equal(withSlash, without);
  assert.equal(withSlash, path.join('/R', 'out', 'reports', 'gallery_summer-solar.html'));
  assert.ok(!withSlash.endsWith('gallery_.html'), 'never the one file every project shares');
});

test('two projects pasted with trailing slashes do not collide on one file', () => {
  // The failure was not that the name was ugly. Both wrote `gallery_.html`,
  // so building the second silently replaced the first.
  const a = galleryFile('smart1-ads/icon-solar/summer-solar/', undefined, '/R');
  const b = galleryFile('smart1-ads/acme-hvac/spring-tuneup/', undefined, '/R');

  assert.notEqual(a, b);
});

test('a doubled separator is the same folder too', () => {
  // Asserted through the *title*, not the filename. `pop()` takes the last
  // segment and a doubled separator makes an empty one in the middle, so the
  // filename is immune and an assertion resting on it cannot fail -- the same
  // shape as pinning a rounding bug on a figure too large to round to zero.
  assert.equal(galleryTitle('smart1-ads//icon-solar//summer-solar'), 'icon-solar — summer-solar');
  assert.equal(
    galleryFile('smart1-ads//icon-solar//summer-solar', undefined, '/R'),
    galleryFile('smart1-ads/icon-solar/summer-solar', undefined, '/R'),
  );
});

test('a folder that names nothing is refused rather than named gallery_.html', () => {
  assert.throws(() => galleryFile('/', undefined, '/R'), /Cannot name a gallery file/);
  assert.throws(() => galleryFile('   ', undefined, '/R'), /Cannot name a gallery file/);
});

test('--out wins, and is resolved so the page directory is knowable', () => {
  const file = galleryFile('smart1-ads/icon-solar/summer-solar', '/tmp/elsewhere/g.html', '/R');

  assert.equal(file, '/tmp/elsewhere/g.html');
  assert.ok(path.isAbsolute(file), 'the asset paths are computed against its directory');
});

test('the heading keeps the client when the folder carries a trailing slash', () => {
  // Raw, the last two segments were ['summer-solar', ''] -- so the heading
  // read "summer-solar — ", dropping the client and ending in a dash.
  assert.equal(galleryTitle('smart1-ads/icon-solar/summer-solar/'), 'icon-solar — summer-solar');
  assert.equal(galleryTitle('smart1-ads/icon-solar/summer-solar'), 'icon-solar — summer-solar');
});

test('a manifest names the client and campaign, and outranks the slug', () => {
  assert.equal(galleryTitle('smart1-ads/icon-solar/summer-solar', MANIFEST), 'Icon Solar — Summer Solar');
});

test('the search and the page agree about what the folder is', () => {
  // The whole defect was that these two disagreed. `folderExpression` reads
  // the shared normalizer now, so it cannot drift back.
  const raw = 'smart1-ads/icon-solar/summer-solar/';

  assert.ok(folderExpression(raw).includes(`asset_folder="${normalizeFolder(raw)}"`));
  assert.ok(galleryFile(raw, undefined, '/R').endsWith(`gallery_${normalizeFolder(raw).split('/').pop()}.html`));
});

test('normalizeFolder leaves an ordinary folder exactly as it is', () => {
  assert.equal(normalizeFolder('smart1-ads/icon-solar/summer-solar'), 'smart1-ads/icon-solar/summer-solar');
});

/* ------------------------------------- a path relative to the page it is on */

test('a simulated asset is drawn relative to where the page was written', () => {
  const assets = assetsFromManifest(MANIFEST, '/tmp/elsewhere');

  assert.equal(assets.length, 1);
  assert.equal(
    path.resolve('/tmp/elsewhere', assets[0].secureUrl),
    '/srv/ads/out/google/concept-a/mr.png',
    'the tile resolves to the file it is a picture of',
  );
});

test('the same manifest written to the default directory resolves too', () => {
  // The old constant was right for exactly this case, which is why the
  // failure only showed up once somebody passed --out.
  const assets = assetsFromManifest(MANIFEST, '/srv/ads/out/reports');

  assert.equal(assets[0].secureUrl, path.join('..', 'google', 'concept-a', 'mr.png'));
  assert.equal(
    path.resolve('/srv/ads/out/reports', assets[0].secureUrl),
    '/srv/ads/out/google/concept-a/mr.png',
  );
});

test('an uploaded asset keeps its hosted URL wherever the page lands', () => {
  const real: Manifest = {
    ...MANIFEST,
    entries: [{ ...MANIFEST.entries[0], cloudinary: { ...MANIFEST.entries[0].cloudinary!, simulated: false } }],
  };

  assert.equal(
    assetsFromManifest(real, '/tmp/elsewhere')[0].secureUrl,
    'https://res.cloudinary.com/x/image/upload/v1/mr.png',
  );
  assert.equal(
    assetsFromManifest(real, '/somewhere/quite/different')[0].secureUrl,
    'https://res.cloudinary.com/x/image/upload/v1/mr.png',
  );
});

test('a creative that failed QA was never uploaded, so it is not a tile', () => {
  const assets = assetsFromManifest(MANIFEST, '/tmp/elsewhere');

  assert.equal(assets.length, 1);
  assert.ok(!assets.some((a) => a.tags.includes('concept:b')), 'the withheld one stays withheld');
});

test('the tags a tile is grouped by survive the reconstruction', () => {
  const [a] = assetsFromManifest(MANIFEST, '/tmp/elsewhere');

  assert.ok(a.tags.includes('platform:google'), 'the gallery groups on platform');
  assert.ok(a.tags.includes('concept:a'), 'and on concept');
  assert.ok(a.tags.includes('size:medium_rectangle'), 'and labels the tile with the size');
  assert.equal(a.width, 300);
  assert.equal(a.height, 250);
});
