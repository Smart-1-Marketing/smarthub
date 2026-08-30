/**
 * "A path this service wrote", and the route that was not asking.
 *
 * Two routes take a `/files/...` URL out of a request body and act on the file
 * behind it. `POST /api/imagery/keep` uploads it to our own Cloudinary account
 * and has always checked — a route that uploads whatever URL it is handed is
 * an open relay, and its comment says so. `POST /api/images/generate` copies
 * the previous picture in as a reference so "make the sky darker" iterates
 * rather than re-rolls, and it checked nothing:
 *
 *     const rel = String(body.previousUrl).replace(/^\/files\//, '');
 *     const prev = path.join(OUT, rel);
 *
 * `path.join(OUT, "../../../etc/passwd")` is `/etc/passwd`. The route then
 * copies that file into the campaign's cache directory, which lives under
 * `imagery/` and is served at `/files/imagery/` — so an arbitrary readable
 * file could be lifted into a web-served folder and handed to an image model,
 * from a value in a POST body. Nothing errors: a path that resolves is a path
 * that copies.
 *
 * One function now, read by both, because the second copy of a rule is the one
 * that drifts — and here the second copy was never written at all.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as path from 'node:path';
import { generatedImagePath, assetUrlIsSafe } from '../src/assets';

const OUT = path.resolve('/var/data/adbuilder');

test('a picture this service wrote resolves', () => {
  assert.equal(
    generatedImagePath('/files/imagery/AD-1/hero.png', OUT),
    path.join(OUT, 'imagery/AD-1/hero.png'),
  );
});

test('the /files prefix is optional, because both routes are handed both', () => {
  assert.equal(
    generatedImagePath('imagery/AD-1/hero.png', OUT),
    path.join(OUT, 'imagery/AD-1/hero.png'),
  );
});

test('a traversal is refused rather than resolved', () => {
  for (const bad of [
    '/files/../../../etc/passwd',
    '/files/imagery/../../../etc/passwd',
    '/files/imagery/AD-1/../../../../etc/passwd.png',
    'imagery/../campaigns/AD-1.json',
  ]) {
    assert.equal(generatedImagePath(bad, OUT), null, `${bad} escaped`);
  }
});

test('nothing outside imagery/ resolves, whatever it is', () => {
  // campaigns, requests and projects are the audit trail, and deliveries is a
  // client's finished pack. None of them is a generated picture.
  for (const bad of [
    '/files/campaigns/AD-1.json',
    '/files/deliveries/AD-1/pack.zip',
    '/files/google/concept-a/300x250.png',
    '/files/projects/p1.json',
  ]) {
    assert.equal(generatedImagePath(bad, OUT), null, `${bad} resolved`);
  }
});

test('only an image extension resolves', () => {
  assert.ok(generatedImagePath('/files/imagery/AD-1/a.jpg', OUT));
  assert.ok(generatedImagePath('/files/imagery/AD-1/a.JPEG', OUT));
  assert.ok(generatedImagePath('/files/imagery/AD-1/a.webp', OUT));
  assert.equal(generatedImagePath('/files/imagery/AD-1/a.json', OUT), null);
  assert.equal(generatedImagePath('/files/imagery/AD-1/a.svg', OUT), null);
  assert.equal(generatedImagePath('/files/imagery/AD-1/hero', OUT), null);
});

test('nothing at all resolves to nothing, rather than to the directory itself', () => {
  for (const bad of [undefined, null, '', '/files/', 'imagery/', 42, {}]) {
    assert.equal(generatedImagePath(bad, OUT), null, `${String(bad)} resolved`);
  }
});

test('a reference photo is fetched only where the asset guard allows it', () => {
  // The generate route fetched any `^https?://` URL on the caller's behalf.
  // These are the addresses that admits, and the guard the neighbouring
  // routes already stand behind refuses every one of them.
  for (const bad of [
    'http://169.254.169.254/latest/meta-data/',   // cloud metadata
    'http://127.0.0.1:8000/api/status',           // the Hub shares this container
    'http://localhost/admin',
    'https://localhost/admin',
    'http://10.0.0.5/',
    'http://192.168.1.1/',
    'https://192.168.1.1/',
    'http://[::1]/',
    'https://something.internal/',
    'http://example.com/photo.png',               // plain http at all
  ]) {
    assert.equal(assetUrlIsSafe(bad).ok, false, `${bad} was allowed`);
  }
  assert.equal(assetUrlIsSafe('https://res.cloudinary.com/x/photo.png').ok, true);
});
