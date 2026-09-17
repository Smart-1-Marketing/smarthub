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
import { fontIsAvailable, knownGoogleFamilies, listFamilies, resolveFont, textPath } from '../src/fonts';

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
