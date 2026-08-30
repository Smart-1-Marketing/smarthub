/**
 * Declared and never wired — the TypeScript half.
 *
 * test_unwired.py sweeps the Hub for this, and it is Python. This module is
 * the one that is not, which is the note CLAUDE.md already makes about it:
 * "a sweep for `.spin` in templates and stylesheets went straight past it."
 * Every check in that file walks past this directory, so the failure this
 * codebase has paid for most often had no sweep here at all.
 *
 * It found two, and the first was the interesting one. `ALL_SIZES` was an
 * exported list named as the authoritative set of sizes, and it held eight of
 * the fifteen the three platform configs now buy — missing all four Meta
 * sizes, all three Google responsive assets, and 250x250. Nothing imported
 * it, which is the only reason it had not dropped a Meta buy: that exact
 * failure has happened four times in this app already, in three
 * `.filter(p => p === 'google' || p === 'amazon')` calls and again in
 * `retention.PRUNABLE`. A stale list nothing reads is the same bug with the
 * trigger not yet pulled.
 *
 * An allowlist rather than a rule, held to the same shape as the Python one:
 * an entry has to say WHY, and an entry naming something that is now called,
 * or now gone, fails. An exemption that outlives what it exempted goes on
 * covering whatever is written at that name next.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';

const ROOT = path.resolve(__dirname, '..');

/**
 * Exported symbols nothing references, with the reason each is kept.
 *
 * Empty on purpose. It is the only way this check was worth adding — one
 * that starts red is one somebody switches off, which is the note
 * tools/integritycheck.py already carries.
 */
const ALLOWED: Record<string, string> = {};

/**
 * Source with its comments removed.
 *
 * Without this the sweep cannot fail, and it fails silently: the comment
 * added beside a fix that says "ALL_SIZES listed eight of fifteen" is itself
 * a match for ALL_SIZES, so putting the dead constant back leaves the check
 * green. Both mutations of this change passed until the strip existed. It is
 * the reason test_unwired.py and hub/config.py's drift check read the AST
 * rather than matching text — prose is not a call site.
 *
 * String literals are kept: a symbol named in one is how public/*.html and
 * the route table reach a server function, and dropping those would report a
 * live reference as dead.
 */
function stripComments(body: string): string {
  return body
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1')
    // An import is not a use. Left in, a symbol that is imported and then
    // never called reads as referenced -- which is what let un-wiring
    // renderableSizes() from render.ts pass this sweep while the dangling
    // import stood. tsconfig has no `noUnusedLocals`, so nothing else catches
    // that either: tsc is happy and the sweep was too.
    .replace(/^\s*import\s+[\s\S]*?from\s+'[^']*';/gm, ' ');
}

function walk(dir: string, out: string[] = []): string[] {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

function read(rel: string, exts?: string[]): { file: string; body: string }[] {
  const dir = path.join(ROOT, rel);
  if (!fs.existsSync(dir)) return [];
  return walk(dir)
    .filter((f) => !exts || exts.includes(path.extname(f)))
    .map((f) => ({ file: path.relative(ROOT, f), body: fs.readFileSync(f, 'utf8') }));
}

function unreferenced() {
  const src = read('src', ['.ts']);
  // Everything that can reach a symbol: the module itself, its tests, the
  // pages the server hands the browser (which name server functions in
  // strings), and the one-off scripts. A symbol used only from a template is
  // wired — the same allowance test_unwired.py makes.
  //
  // This file is excluded from that corpus. It names symbols to explain them
  // and, in the probe below, invents two — counting its own mentions as usage
  // would make the sweep unable to fail, which is the "prose is not a call
  // site" rule this codebase states of every check that reads source.
  const elsewhere = [...read('tests', ['.ts']), ...read('public'), ...read('scripts')]
    .filter((f) => path.basename(f.file) !== 'unwired.test.ts')
    .map((f) => stripComments(f.body)).join('\n');
  const all = src.map((f) => stripComments(f.body)).join('\n');

  const found: { name: string; file: string }[] = [];
  for (const { file, body } of src) {
    const declared = stripComments(body);
    const names = [
      ...declared.matchAll(/^export (?:async )?function (\w+)/gm),
      ...declared.matchAll(/^export const (\w+)\s*[:=]/gm),
    ].map((m) => m[1]);
    for (const name of new Set(names)) {
      // Two regexes on purpose: a /g/ one carries lastIndex between calls, and
      // reusing it for the second question answers about the wrong offset.
      const inSrc = (all.match(new RegExp(`\\b${name}\\b`, 'g')) ?? []).length - 1;
      if (inSrc > 0) continue;                       // minus one: the declaration
      if (new RegExp(`\\b${name}\\b`).test(elsewhere)) continue;
      found.push({ name, file });
    }
  }
  return found.sort((a, b) => a.name.localeCompare(b.name));
}

test('nothing is exported and left unreferenced without a reason on it', () => {
  const open = unreferenced().filter((f) => !(f.name in ALLOWED));
  assert.deepEqual(
    open.map((f) => `${f.name} (${f.file})`), [],
    'an uncalled export is indistinguishable from a working one until somebody '
    + 'goes looking for the feature it was half of — wire it, delete it, or say '
    + 'in ALLOWED why it stays',
  );
});

test('and no entry names something that is called, or gone', () => {
  const names = new Set(unreferenced().map((f) => f.name));
  const stale = Object.keys(ALLOWED).filter((n) => !names.has(n));
  assert.deepEqual(stale, [],
    'an exemption that outlives what it exempted goes on covering whatever is '
    + 'written at that name next');
  for (const [name, why] of Object.entries(ALLOWED)) {
    assert.ok(why.trim().length > 20, `${name} needs a reason, not a placeholder`);
  }
});

test('the sweep reads something, and bites', () => {
  // A sweep that quietly stops sweeping is worse than none — the shape
  // test_blueprint_guards.py had when it walked no mounts and reported a
  // clean run. So: prove it found files, and prove it can still fail.
  const src = read('src', ['.ts']);
  assert.ok(src.length > 30, `only found ${src.length} source files`);

  const scratch = path.join(ROOT, 'src', '__unwired_probe.ts');
  try {
    fs.writeFileSync(scratch, 'export function nothingCallsThisProbe(): number { return 1; }\n');
    assert.ok(unreferenced().some((f) => f.name === 'nothingCallsThisProbe'),
      'it names an export nothing calls');
    fs.writeFileSync(scratch,
      'export function probeIsCalled(): number { return 1; }\n'
      + 'export function probeCaller(): number { return probeIsCalled(); }\n');
    const names = unreferenced().map((f) => f.name);
    assert.ok(!names.includes('probeIsCalled'), 'and does not name one something calls');
    assert.ok(names.includes('probeCaller'), 'while still naming the one nothing calls');
  } finally {
    fs.rmSync(scratch, { force: true });
  }
});
