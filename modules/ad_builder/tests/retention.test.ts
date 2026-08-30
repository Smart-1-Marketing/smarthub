/**
 * The sweep that keeps this service off its neighbours' disk.
 *
 * Nothing here errors when it goes wrong, in either direction. A directory
 * left out of the list simply grows for ever on a volume shared with the Hub;
 * a directory wrongly in it deletes a file somebody is holding a link to. Both
 * are silent, and both were live:
 *
 * `PRUNABLE` named `google` and `amazon` while `meta.json` sat in the platform
 * registry being rendered — the fourth hardcoded platform list in this app,
 * after the three `.filter(p => p === 'google' || p === 'amazon')` calls that
 * dropped a Meta buy outright. And `imagery/` was in no list at all, which
 * made a written rule false: the whole reason `POST /api/imagery/keep` exists
 * is that a generated draft does not last, so a gallery row pointing at one
 * would 404 after the sweep. The sweep was not removing them.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { sweep } from '../src/retention';
import { loadPlatforms } from '../src/registry';

const DAY = 86_400_000;

function tmp(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'retention-'));
}

/** A file of known age, so the sweep is driven rather than waited on. */
function aged(dir: string, rel: string, daysOld: number, bytes = 16): string {
  const full = path.join(dir, rel);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, Buffer.alloc(bytes));
  const when = new Date(Date.now() - daysOld * DAY);
  fs.utimesSync(full, when, when);
  return full;
}

test('every platform in the registry is swept, not a list typed out here', () => {
  const out = tmp();
  const platforms = [...loadPlatforms().keys()];
  assert.ok(platforms.includes('meta'), 'meta must be in the registry for this to mean anything');

  for (const p of platforms) aged(out, `${p}/concept-a/300x250.png`, 60);
  sweep({ outDir: out, renderDays: 30 });

  for (const p of platforms) {
    assert.ok(
      !fs.existsSync(path.join(out, p, 'concept-a', '300x250.png')),
      `${p} renders were left on the disk for ever`,
    );
  }
});

test('a generated draft is swept, because that is what makes "keep" mean something', () => {
  const out = tmp();
  const old = aged(out, 'imagery/AD-1/hero.png', 30);
  const fresh = aged(out, 'imagery/AD-2/hero.png', 1);

  sweep({ outDir: out, cacheDays: 7 });

  assert.ok(!fs.existsSync(old), 'an old draft should go — a gallery row on it would 404');
  assert.ok(fs.existsSync(fresh), 'a draft somebody is still working with must stay');
});

test('a delivered pack is never swept, however old', () => {
  // The download button on the proof reads this file, and the client opens
  // that page whenever they like. Sweeping it turns a working link into a 404
  // for the one person the tool is for.
  const out = tmp();
  const zip = aged(out, 'deliveries/AD-1/pack.zip', 400);

  const r = sweep({ outDir: out, renderDays: 1, cacheDays: 1 });

  assert.ok(fs.existsSync(zip), 'the delivered pack was removed');
  assert.equal(r.scanned, 0, 'it should not even be walked');
});

test('the audit trail is never swept', () => {
  const out = tmp();
  const kept = [
    aged(out, 'projects/p1.json', 400),
    aged(out, 'campaigns/AD-1.json', 400),
    aged(out, 'requests/AD-1.json', 400),
    aged(out, 'reports/manifest_AD-1.json', 400),
  ];

  sweep({ outDir: out, renderDays: 1, cacheDays: 1 });

  for (const f of kept) assert.ok(fs.existsSync(f), `${f} was removed`);
});

test('renders and caches keep their own windows', () => {
  // A render is proofed and re-proofed over weeks; a cached download is a copy
  // of something still at its source. One window for both would either evict
  // creative somebody is reviewing or hoard downloads.
  const out = tmp();
  const render = aged(out, 'google/concept-a/300x250.png', 10);
  const cache = aged(out, 'cache/remote-logo.png', 10);

  sweep({ outDir: out, renderDays: 30, cacheDays: 7 });

  assert.ok(fs.existsSync(render), '10 days is inside the 30-day render window');
  assert.ok(!fs.existsSync(cache), '10 days is past the 7-day cache window');
});

test('a dry run reports exactly what a real run would remove, and removes none of it', () => {
  const out = tmp();
  const f = aged(out, 'google/concept-a/300x250.png', 60, 2048);

  const dry = sweep({ outDir: out, renderDays: 30, dryRun: true });
  assert.equal(dry.removed, 1);
  assert.equal(dry.bytesFreed, 2048);
  assert.ok(dry.dryRun);
  assert.ok(fs.existsSync(f), 'a dry run deleted a file');

  const real = sweep({ outDir: out, renderDays: 30 });
  assert.equal(real.removed, dry.removed, 'the dry run promised a different number');
  assert.equal(real.bytesFreed, dry.bytesFreed);
  assert.ok(!fs.existsSync(f));
});

test('the details list is bounded, and the counts still cover everything', () => {
  // The result is logged and rendered; a sweep of a full disk must not put
  // thousands of paths through either. The count is the answer, the paths are
  // a sample.
  const out = tmp();
  for (let i = 0; i < 25; i++) aged(out, `google/concept-a/f${i}.png`, 60);

  const r = sweep({ outDir: out, renderDays: 30 });

  assert.equal(r.removed, 25, 'the count must be of everything, not of the sample');
  assert.equal(r.details.length, 20, 'the sample should be capped');
});

test('a missing directory is not an error', () => {
  // A fresh deploy has rendered nothing. The sweep runs on a timer regardless.
  const r = sweep({ outDir: tmp(), renderDays: 30 });
  assert.equal(r.scanned, 0);
  assert.equal(r.removed, 0);
});

test('emptied directories are cleared up, and a dry run leaves them alone', () => {
  const out = tmp();
  aged(out, 'google/concept-a/300x250.png', 60);

  sweep({ outDir: out, renderDays: 30, dryRun: true });
  assert.ok(fs.existsSync(path.join(out, 'google', 'concept-a')), 'a dry run removed a directory');

  sweep({ outDir: out, renderDays: 30 });
  assert.ok(!fs.existsSync(path.join(out, 'google', 'concept-a')), 'an empty concept dir was left behind');
});
