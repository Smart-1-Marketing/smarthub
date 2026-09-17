/**
 * A white or black version of the mark.
 *
 * A dark logo on a dark photo has no palette fix, and the mark is the one
 * asset nobody may recolour by hand. A one-colour version is the industry's
 * own answer and every brand guide carries one: the shape is untouched, every
 * opaque pixel is painted the tone, the alpha is kept exactly.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import sharp from 'sharp';
import { makeMono } from '../src/logo-tools';

async function markFile(dir: string): Promise<string> {
  // A 4x2 mark: navy, yellow, half-transparent red, and fully transparent.
  const px = Buffer.from([
    10, 20, 120, 255,   240, 200, 30, 255,   200, 0, 0, 128,   0, 0, 0, 0,
    10, 20, 120, 255,   240, 200, 30, 255,   200, 0, 0, 128,   0, 0, 0, 0,
  ]);
  const file = path.join(dir, 'mark.png');
  await sharp(px, { raw: { width: 4, height: 2, channels: 4 } }).png().toFile(file);
  return file;
}

test('every opaque pixel takes the tone and the alpha is kept', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'logo-mono-'));
  const src = await markFile(dir);
  for (const tone of ['white', 'black'] as const) {
    const out = await makeMono(src, path.join(dir, `${tone}.png`), tone);
    const { data, info } = await sharp(out).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    assert.equal(info.width, 4);
    const v = tone === 'white' ? 255 : 0;
    for (let i = 0; i < data.length; i += 4) {
      const alpha = data[i + 3];
      if (alpha > 20) {
        assert.deepEqual([data[i], data[i + 1], data[i + 2]], [v, v, v], `${tone}: pixel ${i / 4} painted`);
      }
    }
    assert.equal(data[3], 255, 'opaque stays opaque');
    assert.equal(data[11], 128, 'half-transparent stays half-transparent');
    assert.equal(data[15], 0, 'transparent stays transparent');
  }
});
