/**
 * The white box around a logo, which QA read as excellent contrast.
 *
 * logo-tools.ts opens with this as rule 1: "Any logo that is not already
 * transparent must have its background removed before compositing — a white
 * box around a logo on a coloured ad looks broken." Nothing asked.
 * `hasTransparency()` was written for it and had no caller anywhere in the
 * repo — it is gone now, superseded by flatBackdrop(), which answers the
 * same question and can also say what colour the plate is. The QA pass that
 * could have caught it read BETTER on the broken
 * ad: `logoInkLuminance()` averages every opaque pixel, so on a plated logo
 * it measures the plate. The same navy wordmark scores about 2.3:1 on a
 * transparent canvas and about 9.9:1 with a white box behind it, against a
 * navy panel — so the box makes QA more confident about the one ad with a
 * white rectangle stamped across it.
 *
 * The same corner sample had a second failure in it, in the opposite
 * direction: it never asked whether the corners were OPAQUE. On a logo that
 * is already transparent those pixels are (0,0,0,alpha 0), so the corners
 * agree, black is taken for the background, and every near-black pixel in
 * the mark is made transparent. Rework logo erased a #0a0a0a wordmark
 * outright and reported success.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import sharp from 'sharp';
import { flatBackdrop, removeFlatBackground } from '../src/logo-tools';
import { logoInkLuminance, plateShowsAgainst } from '../src/qa';
import { contrastRatio } from '../src/raster';
import { renderPreview } from '../src/render';
import type { Campaign, SizeKey } from '../src/types';

const ROOT = path.resolve(__dirname, '..');
const MARK = '<rect x="20" y="25" width="160" height="30" fill="#0b2545"/>';

function dir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'logo-plate-'));
}

async function logo(file: string, plate: string | null, fill = '#0b2545'): Promise<string> {
  let p = sharp(Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80">${MARK.replace('#0b2545', fill)}</svg>`));
  if (plate) p = p.flatten({ background: plate });
  await p.png().toFile(file);
  return file;
}

test('an already-transparent logo has no plate to strip', async () => {
  const d = dir();
  assert.equal(await flatBackdrop(await logo(path.join(d, 'a.png'), null)), null);
  // Near-black is the case that was destroyed: its corners are (0,0,0) with
  // alpha 0, which reads as a flat black plate unless opacity is checked.
  assert.equal(await flatBackdrop(await logo(path.join(d, 'b.png'), null, '#0a0a0a')), null);
});

test('and a plated one reports the color that will show', async () => {
  const d = dir();
  const white = await flatBackdrop(await logo(path.join(d, 'w.png'), '#ffffff'));
  assert.deepEqual(white?.rgb, [255, 255, 255]);
  assert.ok(white!.luminance > 0.9);
  const navy = await flatBackdrop(await logo(path.join(d, 'n.png'), '#0b2545', '#ffffff'));
  assert.ok(navy!.luminance < 0.2, 'a dark plate is a plate too');
});

test('rework no longer eats a near-black mark on a transparent canvas', async () => {
  const d = dir();
  const src = await logo(path.join(d, 'src.png'), null, '#0a0a0a');
  const before = (await sharp(src).stats()).channels[3].mean;
  const out = await removeFlatBackground(src, path.join(d, 'out.png'));
  const after = (await sharp(out).stats()).channels[3].mean;
  assert.notEqual(after, 0, 'the mark is still there');
  assert.equal(Math.round(after), Math.round(before), 'and untouched, not merely non-empty');
});

test('while a real plate is still stripped', async () => {
  const d = dir();
  const src = await logo(path.join(d, 'boxed.png'), '#ffffff');
  // flatten() drops the alpha channel entirely, which is exactly what a
  // logo exported over a white artboard looks like. flatBackdrop() calls
  // ensureAlpha() so a three-channel PNG reads as fully opaque.
  assert.equal((await sharp(src).metadata()).hasAlpha, false, 'starts fully opaque');
  const out = await removeFlatBackground(src, path.join(d, 'out.png'));
  const meta = await sharp(out).metadata();
  assert.ok(meta.width! < 200, 'the plate was removed and the margin trimmed');
});

test('the contrast check measured the plate, which is why it could not catch this', async () => {
  const d = dir();
  const bare = await logoInkLuminance(await logo(path.join(d, 'bare.png'), null));
  const boxed = await logoInkLuminance(await logo(path.join(d, 'boxed.png'), '#ffffff'));
  const navyPanel = 0.03;
  assert.ok(boxed! > bare!, 'the white plate dominates the average');
  assert.ok(
    contrastRatio(boxed!, navyPanel) > contrastRatio(bare!, navyPanel),
    'so the broken ad scores BETTER than the correct one — the reason a passing '
    + 'logo-contrast finding is now withheld when a visible plate is found',
  );
});

test('a rendered ad names the box, and withholds the contrast number', async () => {
  const file = path.join(ROOT, 'campaigns/bella-vista-catering.json');
  const campaign: Campaign = JSON.parse(fs.readFileSync(file, 'utf8'));
  const d = dir();
  const plated = await logo(path.join(d, 'plated.png'), '#ffffff');

  const concept = campaign.concepts[0];
  const size = '300x250' as SizeKey;

  const clean = await renderPreview({
    brand: campaign.brand, concept, platform: 'google', size, assetRoot: ROOT,
  });
  const cleanPlate = clean.qa.find((f) => f.check === 'logo-plate');
  assert.equal(cleanPlate?.status, 'pass', 'the shipped logo carries its own transparency');
  assert.ok(clean.qa.some((f) => f.check === 'logo-contrast'),
    'and its contrast is measured as it always was');

  // This size's panel is `light`, i.e. #FFFFFF, so a WHITE plate on it is
  // genuinely invisible and correctly says nothing. That is the rule the check
  // states for itself, and it only became reachable once the background pass
  // stopped containing the logo: `behind` used to be the plated logo's own
  // average (a white box around a navy mark, ~0.8), which differs from the
  // plate by enough to warn -- so this warned about the mark inside the box
  // rather than about the box standing out from the panel.
  const invisible = await renderPreview({
    brand: { ...campaign.brand, logos: { primary: plated } },
    concept, platform: 'google', size, assetRoot: ROOT,
  });
  const quiet = invisible.qa.find((f) => f.check === 'logo-plate');
  assert.equal(quiet?.status, 'pass', 'a white plate on a white card is not a box');

  // A plate that will actually show is still named.
  const showy = await logo(path.join(d, 'showy.png'), '#7A2E1F');
  const boxed = await renderPreview({
    brand: { ...campaign.brand, logos: { primary: showy } },
    concept, platform: 'google', size, assetRoot: ROOT,
  });
  const finding = boxed.qa.find((f) => f.check === 'logo-plate');
  assert.equal(finding?.status, 'warn', 'the box is named');
  assert.match(finding!.detail, /opaque rgb\(122, 46, 31\)/);
  assert.match(finding!.detail, /Rework logo/, 'and points at the tool that fixes it');
  assert.equal(boxed.qa.find((f) => f.check === 'logo-contrast'), undefined,
    'no contrast finding at all — a passing one would be the plate measured '
    + 'against the panel, printed under a heading about the logo');
});

test('a plate is only a finding when it will actually show', () => {
  // A white plate on a white card is invisible in the render, and a warning
  // that fires on every ad carrying a plate is one people stop reading --
  // the note hub/qr_codes.py makes about a QR warning on every social spot.
  const white = { rgb: [255, 255, 255] as [number, number, number], luminance: 1 };
  const navy = { rgb: [11, 37, 69] as [number, number, number], luminance: 0.1 };

  assert.equal(plateShowsAgainst(white, 0.03), true, 'white plate, navy panel');
  assert.equal(plateShowsAgainst(white, 0.95), false, 'white plate, white card');
  assert.equal(plateShowsAgainst(navy, 0.95), true, 'and it is symmetric');
  assert.equal(plateShowsAgainst(navy, 0.08), false, 'a plate that matches is no box');
  assert.equal(plateShowsAgainst(null, 0.03), false, 'no plate is never a finding');

  // The boundary itself, which no rendered fixture lands on.
  assert.equal(plateShowsAgainst({ rgb: [0, 0, 0], luminance: 0.12 }, 0), false, 'exactly at the limit is quiet');
  assert.equal(plateShowsAgainst({ rgb: [0, 0, 0], luminance: 0.121 }, 0), true, 'just past it is not');
});
