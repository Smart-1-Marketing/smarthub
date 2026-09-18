/**
 * Google Fonts on the renderer.
 *
 * The registry was three families, so a brand whose site sets Lato rendered
 * in Poppins while the control showed Lato. It is the Google families a brand
 * is likely to actually use now, vendored through @fontsource -- and every one
 * is probed at load, because opentype.js cannot draw every Google family:
 * Roboto, Inter, Nunito, Oswald, Rubik and ten others ship a GSUB lookup it
 * does not implement and throw on the first glyph. Those are left off rather
 * than offered and swapped mid-render.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fontIsAvailable, knownGoogleFamilies, listFamilies, resolveFont, textPath, probeFamilies, _resetRegistryForTest } from '../src/fonts';

test('the list is the Google families the renderer can actually draw', () => {
  const have = listFamilies();
  assert.ok(have.length >= 30, `${have.length} families`);
  for (const f of ['Montserrat', 'Open Sans', 'Poppins', 'Lato', 'Raleway', 'Playfair Display', 'Bebas Neue']) {
    assert.ok(have.includes(f), `${f} is offered`);
  }
  // Known to throw in opentype.js; must never be offered.
  for (const f of ['Roboto', 'Inter', 'Nunito', 'Oswald', 'Rubik', 'Lora', 'Merriweather']) {
    assert.ok(!have.includes(f), `${f} would render as Poppins and is not offered`);
    assert.equal(fontIsAvailable(f), false);
  }
  assert.deepEqual(knownGoogleFamilies().filter((f) => !have.includes(f)), [],
    'every family the build names is installed and renders');
});

test('every offered family draws every weight without throwing', () => {
  for (const family of listFamilies()) {
    for (const weight of ['regular', 'medium', 'bold'] as const) {
      const font = resolveFont(family, weight);
      const d = textPath(font, 'Quick brown fox 123', 0, 20, 18);
      assert.ok(d.length > 50, `${family} ${weight} draws glyphs`);
    }
  }
});

test('the committed manifest names exactly the families the probe would find', () => {
  // The manifest is loaded at boot in ~3ms; the probe is ~350ms because
  // opentype.js parses every file. So the two must agree, or the manifest
  // is a wrong answer served fast. A drift (a new @fontsource added without
  // re-running prebuild, a family opentype.js can now draw) is exactly what
  // this catches.
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'src', 'fonts.manifest.json'), 'utf8'));
  const probed = probeFamilies();
  assert.deepEqual(manifest.map((r: any) => r.family), probed.map((r) => r.family),
    'manifest and probe list the same families in the same order; run `npm run prebuild` to refresh');
  for (const family of ['Montserrat', 'Poppins']) {
    assert.ok(manifest.find((r: any) => r.family === family), `${family} is in the manifest`);
  }
  for (const family of ['Roboto', 'Inter']) {
    assert.ok(!manifest.find((r: any) => r.family === family), `${family} opentype.js cannot draw and must not be in the manifest`);
  }
});

test('a missing manifest falls back to the probe and still draws Poppins', (t) => {
  // The probe is the fallback -- so a manifest that has not been generated in
  // a dev checkout is at worst the old startup cost, never a wrong answer.
  const file = path.join(__dirname, '..', 'src', 'fonts.manifest.json');
  const raw = fs.readFileSync(file);
  t.after(() => { fs.writeFileSync(file, raw); _resetRegistryForTest(); });
  fs.unlinkSync(file);
  _resetRegistryForTest();
  const font = resolveFont('Poppins', 'regular');
  assert.ok(textPath(font, 'Ag', 0, 20, 18).length > 20, 'the fallback path still draws');
  assert.ok(listFamilies().includes('Montserrat'));
});
