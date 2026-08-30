/**
 * Serving this app from under a URL prefix.
 *
 * Every page here links and fetches from the site root. Standalone that is
 * correct; inside the Hub the app is mounted at /tools/display-ads, so those
 * URLs leave the mount, the Hub has no such route, and -- this is the whole
 * point -- the page loads looking perfect while every button does nothing.
 * Nothing says why.
 *
 * Eighty-three lines with no tests until now, which is an odd place for this
 * codebase to have left a gap: it is the single file standing between a
 * working tool and one that is silently inert.
 *
 * Two things are checked here that a reading of the source does not settle.
 * The header is caller-controlled and is interpolated into a page, so what it
 * refuses matters. And the shim's own URL rule lives inside a string literal,
 * where a typo compiles perfectly -- so it is pulled out and run.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import type { IncomingMessage } from 'node:http';
import { basePrefix, withBase } from '../src/basepath';

const req = (prefix?: string) =>
  ({ headers: prefix === undefined ? {} : { 'x-forwarded-prefix': prefix } }) as unknown as IncomingMessage;

const PAGE = '<!doctype html><html><head><title>x</title></head><body>hi</body></html>';

/* ------------------------------------------------------------- the prefix */

test('no header means no prefix, and the page is returned untouched', () => {
  // Standalone. Every byte identical, so mounting cannot change behaviour for
  // the deployment that does not mount.
  assert.equal(basePrefix(req()), '');
  assert.equal(withBase(req(), PAGE), PAGE);
});

test('a mount prefix is read, and a trailing slash is not part of it', () => {
  assert.equal(basePrefix(req('/tools/display-ads')), '/tools/display-ads');
  assert.equal(basePrefix(req('/tools/display-ads/')), '/tools/display-ads',
               'or every rewritten URL gains a double slash');
});

test('a header that is not a simple absolute path is refused, not escaped', () => {
  // It comes from our own proxy, but a header is still caller-controlled
  // input and this value is interpolated into a page. Anything with a quote,
  // an angle bracket or a scheme in it has no legitimate use here.
  for (const hostile of [
    '"></script><script>alert(1)</script>',
    "/tools'; alert(1); //",
    'https://evil.example/tools',
    '//evil.example',
    '/tools<script>',
    'tools/display-ads',        // not absolute
    '/tools/display ads',       // a space
  ]) {
    assert.equal(basePrefix(req(hostile)), '',
                 `refused: ${hostile}`);
  }
});

test('a refused header leaves the page exactly as it was', () => {
  // Refusing the value and then injecting a shim built from '' would be the
  // worst of both: no rewriting, and a script tag that says there is.
  assert.equal(withBase(req('https://evil.example'), PAGE), PAGE);
});

/* --------------------------------------------------------------- the shim */

test('the shim goes after <head>, so it patches fetch before any page script', () => {
  const out = withBase(req('/tools/display-ads'), PAGE);
  assert.ok(out.includes('var B="/tools/display-ads"'), 'the base is bound');
  assert.ok(out.includes('window.S1_BASE=B'), 'and published for the page to read');
  assert.ok(out.indexOf('window.S1_BASE') < out.indexOf('<title>'),
            'ahead of anything the page itself loads');
});

test('a page with no head is prepended to rather than silently skipped', () => {
  const fragment = '<body>no head here</body>';
  const out = withBase(req('/tools/display-ads'), fragment);
  assert.ok(out.includes('window.S1_BASE'), 'the shim is still there');
  assert.ok(out.endsWith(fragment), 'and the page is intact behind it');
});

/**
 * The shim's URL rule, lifted out of the string it lives in and run.
 *
 * `pre()` decides what gets the mount prefix. It is three conditions deep and
 * sits inside a template literal, where getting it wrong compiles cleanly and
 * fails only in a browser nobody has open.
 */
function prefixer(base: string) {
  const shim = withBase(req(base), PAGE);
  const body = shim.match(/function pre\(u\)\{return ([^;]+);\}/);
  assert.ok(body, 'pre() is still shaped the way this test reads it');
  const ours = shim.match(/function ours\(u\)\{return ([^;]+);\}/);
  assert.ok(ours, 'ours() is still shaped the way this test reads it');
  return new Function('B', 'u',
    `function ours(u){return ${ours![1]};}
     return ${body![1]};`).bind(null, base) as (u: string) => string;
}

test('a root-absolute URL gains the mount', () => {
  const pre = prefixer('/tools/display-ads');
  assert.equal(pre('/api/render'), '/tools/display-ads/api/render');
  assert.equal(pre('/build'), '/tools/display-ads/build');
});

test('a URL already carrying the mount is not prefixed twice', () => {
  // Otherwise the MutationObserver, which re-runs over markup added later,
  // walks a URL further from the truth on every pass.
  const pre = prefixer('/tools/display-ads');
  assert.equal(pre('/tools/display-ads/api/render'), '/tools/display-ads/api/render');
  assert.equal(pre(pre(pre('/api/render'))), '/tools/display-ads/api/render',
               'and it is idempotent');
});

test("the Hub's own assets are left alone", () => {
  // The Hub injects its sidebar and /hub-*.js into this page AFTER this
  // service has produced it. Those URLs are the Hub's: prefixing them points
  // the whole sidebar back into this service, which answers "No route for
  // GET /sales/landing" and looks like the Hub is broken.
  const pre = prefixer('/tools/display-ads');
  assert.equal(pre('/hub-help.js'), '/hub-help.js');
  assert.equal(pre('/hub-crumbs.js'), '/hub-crumbs.js');
});

test('what is not a root-absolute path is not touched', () => {
  const pre = prefixer('/tools/display-ads');
  assert.equal(pre('https://res.cloudinary.com/x.png'), 'https://res.cloudinary.com/x.png',
               'cross-origin');
  assert.equal(pre('//cdn.example/x.png'), '//cdn.example/x.png',
               'protocol-relative is cross-origin too');
  assert.equal(pre('api/render'), 'api/render', 'relative');
  assert.equal(pre('data:image/png;base64,AAAA'), 'data:image/png;base64,AAAA');
  assert.equal(pre('#top'), '#top');
});
