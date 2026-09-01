/**
 * A button that says "Copied" when it did not.
 *
 * `navigator.clipboard` is absent on http and is allowed to refuse on
 * https, which is the failure CLAUDE.md names on the Smart 1 Ads estimate:
 * "The copy button reported success it never had." The build screen had
 * three copy affordances and kept that rule at two of them.
 *
 * The logo-palette swatches asked, and said so either way. The proof link
 * asked, and fell back to selecting the URL. The **site-brand** swatches --
 * the ones carrying the colors read off the client's own website -- did
 * this:
 *
 *     if (navigator.clipboard) navigator.clipboard.writeText(hex).catch(function () {});
 *     el.title = hex + ' copied — paste it into a swatch below';
 *
 * An empty catch, and the claim on the next line regardless. On http
 * `navigator.clipboard` is undefined, so nothing was even attempted and
 * the tooltip still said copied. A rule two of three call sites keep is
 * not a rule, so `copyText` is the one reading and every control draws
 * what it answers.
 *
 * The block is LIFTED from public/build.html between its own markers and
 * run here, the arrangement test_menu_layout.py uses over hub-crumbs.js: a
 * copy restated in the test would be a third thing to keep in step.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';

const ROOT = path.resolve(__dirname, '..');
const PAGE = path.join(ROOT, 'public', 'build.html');
const html = fs.readFileSync(PAGE, 'utf8');

function liftCopyText(): (text: unknown, doc?: any, nav?: any) => Promise<boolean> {
  const start = html.indexOf('/* --- s1:copy-start --- */');
  const end = html.indexOf('/* --- s1:copy-end --- */');
  assert.ok(start > 0 && end > start, 'the copy block still carries its markers');
  const block = html.slice(start, end);
  assert.match(block, /function copyText\(/, 'and the block is the function');
  // eslint-disable-next-line no-new-func
  return new Function(`${block}; return copyText;`)() as any;
}

const copyText = liftCopyText();

/** A document whose execCommand answers however the test says. */
function fakeDoc(execCommand: null | (() => boolean)) {
  const removed: any[] = [];
  const body = {
    appendChild(n: any) { n.parentNode = body; return n; },
    removeChild(n: any) { removed.push(n); return n; },
  };
  const doc: any = {
    body, removed,
    createElement: () => ({ value: '', setAttribute() {}, select() {}, parentNode: null }),
  };
  if (execCommand) doc.execCommand = execCommand;
  return doc;
}

test('a clipboard that took the text answers copied', async () => {
  const written: string[] = [];
  const nav = { clipboard: { writeText: (v: string) => { written.push(v); return Promise.resolve(); } } };
  assert.equal(await copyText('#2E5A88', fakeDoc(null), nav), true);
  assert.deepEqual(written, ['#2E5A88'], 'and the text it was given');
});

test('a clipboard that refused, with nothing behind it, answers false', async () => {
  // The live case: https, permission refused, and no execCommand to fall
  // back to. The old site-brand handler swallowed exactly this and said
  // "copied" anyway.
  const nav = { clipboard: { writeText: () => Promise.reject(new Error('NotAllowedError')) } };
  assert.equal(await copyText('#2E5A88', fakeDoc(null), nav), false);
});

test('no clipboard at all is not a copy', async () => {
  // http: navigator.clipboard is undefined, so nothing is attempted. The
  // old handler's `if (navigator.clipboard)` skipped the write and set the
  // tooltip on the next line regardless.
  assert.equal(await copyText('#2E5A88', fakeDoc(null), {}), false);
  assert.equal(await copyText('#2E5A88', fakeDoc(null), null), false);
});

test('execCommand is a fallback, and its answer is believed both ways', async () => {
  const nav = { clipboard: { writeText: () => Promise.reject(new Error('no')) } };
  assert.equal(await copyText('#2E5A88', fakeDoc(() => true), nav), true, 'it worked, so true');
  assert.equal(await copyText('#2E5A88', fakeDoc(() => false), nav), false,
    'execCommand returning false is a refusal, not a success');
});

test('nothing in it raises, whatever the host does', async () => {
  // A copy button that breaks the page it sits on is worse than one that
  // cannot copy, so every path resolves rather than throwing.
  // writeText throwing SYNCHRONOUSLY is the one that got past the first
  // version of copyText: Promise.resolve() wrapped around the call cannot
  // catch a throw from the call itself.
  const throwingNav = { clipboard: { writeText: () => { throw new Error('boom'); } } };
  assert.equal(await copyText('x', fakeDoc(() => true), throwingNav), true,
    'it falls through to execCommand rather than escaping');
  assert.equal(await copyText('x', fakeDoc(null), throwingNav), false,
    'and answers false when there is nothing behind it');
  assert.equal(await copyText('x', fakeDoc(() => { throw new Error('boom'); }), {}), false);
  const hostileDoc: any = { body: null, createElement: () => { throw new Error('boom'); }, execCommand: () => true };
  assert.equal(await copyText('x', hostileDoc, {}), false);
});

test('the scratch element is removed even when the copy failed', async () => {
  // Left behind, every refused copy leaks a textarea into the body.
  const doc = fakeDoc(() => false);
  await copyText('#2E5A88', doc, {});
  assert.equal(doc.removed.length, 1, 'the textarea it appended was taken back out');
});

test('a null or missing value copies an empty string rather than "null"', async () => {
  const written: string[] = [];
  const nav = { clipboard: { writeText: (v: string) => { written.push(v); return Promise.resolve(); } } };
  await copyText(null, fakeDoc(null), nav);
  await copyText(undefined, fakeDoc(null), nav);
  assert.deepEqual(written, ['', ''], 'never the string "null"');
});

test('every copy affordance on the page reads the one answer', () => {
  // The whole point: three controls, one rule. A fourth added next month
  // must not be able to go back to claiming a copy it never made.
  const body = html.slice(html.indexOf('/* --- s1:copy-end --- */'));
  const claims = body.match(/navigator\s*\.\s*clipboard/g) ?? [];
  assert.deepEqual(claims, [],
    'no call site reaches navigator.clipboard directly; copyText is the one reading');
  const calls = body.match(/copyText\(/g) ?? [];
  assert.ok(calls.length >= 3, `all three controls call it (found ${calls.length})`);
});

test('the site-brand swatch draws the failure rather than swallowing it', () => {
  const at = html.indexOf("querySelectorAll('[data-sitecolor]')");
  assert.ok(at > 0, 'the site-brand handler is still there');
  const handler = html.slice(at, at + 900);
  assert.match(handler, /copyText\(/, 'it asks');
  assert.match(handler, /could not copy/, 'and it says so when the answer is no');
  assert.doesNotMatch(handler, /catch\s*\(\s*function\s*\(\s*\)\s*\{\s*\}\s*\)/,
    'the empty catch that hid this is gone');
});
