/**
 * Finding a client's assets again, in whichever folder mode the account is in.
 *
 * Cloudinary publishes a folder two ways: `asset_folder` in dynamic-folder
 * mode, `folder` (derived from the public_id path) in fixed. A search that
 * asks for the wrong one returns **zero**, with the request succeeding and the
 * page looking perfectly healthy — a client's gallery reading as a client with
 * nothing in it.
 *
 * This picked between them from `CLOUDINARY_FOLDER_MODE`, which is set in this
 * module's own `render.yaml` — the manifest for running the renderer as a
 * standalone service. On the Hub it is a second process in the Hub's
 * container, and `docker-start.sh` derives its Cloudinary settings from
 * `CLOUDINARY_URL` and does not set the mode. So the default, `fixed`, was
 * answering for an account nobody had checked, and `gallery.ts` is what reads
 * it.
 *
 * `hub/video_library.py` reached this first, ran both fields against this
 * account, found they answer identically, and asks for both — so the extra
 * clause costs nothing and there is no setting left that can be silently
 * wrong. This is that, in TypeScript.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { folderExpression, slug } from '../src/cloudinary';

test('both folder fields are asked for, so neither mode comes back empty', () => {
  const e = folderExpression('smart1-ads/icon-solar/summer');

  assert.match(e, /asset_folder=/, 'dynamic-folder accounts publish asset_folder');
  assert.match(e, /(^|\s)folder=/, 'fixed-folder accounts publish folder');
});

test('the folder itself and everything under it, which is two different clauses', () => {
  // An exact match misses every asset in a subfolder; a trailing wildcard
  // misses every asset sitting directly in the folder. Neither alone is the
  // answer, and the old expression used `:` for both — so the folder's own
  // assets were matched by a contains rather than an equality.
  const e = folderExpression('smart1-ads/icon-solar/summer');

  assert.ok(e.includes('asset_folder="smart1-ads/icon-solar/summer"'), 'the folder itself');
  assert.ok(e.includes('asset_folder:"smart1-ads/icon-solar/summer/*"'), 'and everything below it');
  assert.ok(e.includes('folder="smart1-ads/icon-solar/summer"'));
  assert.ok(e.includes('folder:"smart1-ads/icon-solar/summer/*"'));
});

test('a non-recursive search asks only about the folder itself', () => {
  const e = folderExpression('smart1-ads/icon-solar/summer', { recursive: false });

  assert.ok(!e.includes('/*'), 'no subtree clause');
  assert.match(e, /asset_folder=/);
  assert.match(e, /(^|\s)folder=/, 'still both fields — the mode is still unknown');
});

test('the clauses are ORed, because an asset matches exactly one of them', () => {
  const e = folderExpression('a/b');
  assert.equal(e.split(' OR ').length, 4, `expected four terms, got: ${e}`);
  assert.ok(!e.includes(' AND '), 'ANDing them matches nothing at all');
});

test('a quote cannot close the value and become syntax', () => {
  // Every folder reaching here is built from slug(), so this is belt and
  // braces on the day somebody passes a name straight in.
  const e = folderExpression('smart1-ads/ac" OR tags:secret/x');

  // The property is that every quote in the expression is a delimiter of ours:
  // four terms, two quotes each. Counting terms by splitting on " OR " would
  // not show it — the injected text contains an OR, and it is inert precisely
  // because it stays inside a quoted value.
  assert.equal((e.match(/"/g) ?? []).length, 8, `unbalanced quoting: ${e}`);
  assert.ok(e.includes('asset_folder="smart1-ads/ac OR tags:secret/x"'),
            'the whole thing should stay one quoted folder name');
});

test('nothing to search is an empty expression, never a search for everything', () => {
  // The caller returns [] on an empty expression. An expression that widens to
  // the whole account when a folder name is blank is the failure the scope
  // exists to prevent.
  for (const empty of ['', '   ', '/', '///', null, undefined]) {
    assert.equal(folderExpression(empty as unknown as string), '', `${String(empty)} widened`);
  }
});

test('surrounding slashes do not change which folder is meant', () => {
  const bare = folderExpression('a/b');
  assert.equal(folderExpression('/a/b'), bare);
  assert.equal(folderExpression('a/b/'), bare);
});

test('a client folder is a slug, so two spellings of one client are one folder', () => {
  assert.equal(slug('Icon Solar'), 'icon-solar');
  assert.equal(slug('ICON  SOLAR!'), 'icon-solar');
  assert.equal(slug("Smitty's Fireplace & Patio"), 'smitty-s-fireplace-patio');
});

test('a slug never carries a path separator out of its own folder', () => {
  // The slug becomes a path segment. Anything that could add a level, or climb
  // one, would file a client's creative somewhere else in the tree.
  for (const nasty of ['../../etc', 'a/b/c', 'x\\y']) {
    const out = slug(nasty);
    assert.ok(!out.includes('/'), `${nasty} -> ${out}`);
    assert.ok(!out.includes('\\'), `${nasty} -> ${out}`);
    assert.ok(!out.includes('..'), `${nasty} -> ${out}`);
  }
});
