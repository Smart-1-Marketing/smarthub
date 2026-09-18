/**
 * The third list: a size's look adopted by the set, a client's note on one
 * ad, and a line of health per campaign.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { adoptLook, styleFor, carryFor } from '../src/carry';
import { familyFor, getTemplate } from '../src/registry';
import { campaignHealth } from '../src/health';
import { commentOnClientProof, getClientProof, clientProofHtml, clientProofsByProject, COMMENT_LIMIT, type ClientProof } from '../src/workflow';
import { ProjectStore } from '../src/projects';
import type { CreativeConcept } from '../src/types';

const concept = (extra: Partial<CreativeConcept> = {}): CreativeConcept => ({
  conceptId: 'A', name: 'x', layoutFamily: 'T01', hero: {}, copy: { default: { headline: 'h' } }, ...extra,
} as unknown as CreativeConcept);

test('adopting a size makes its resolved look the authored one and drops the other corrections', () => {
  const c = concept({
    styleOverrides: {
      authoredFor: '300x250',
      headline: { size: 40, color: '#FF0000' },
      cta: { x: 80 },
      bySize: { '728x90': { headline: { size: 22 } }, '160x600': { cta: { y: 300 } }, '300x600': {} },
    },
  } as never);
  const looked = styleFor(c, '728x90');
  const out = adoptLook(c, '728x90', ['300x250', '728x90', '160x600', '300x600']);
  assert.equal(out.styleOverrides?.authoredFor, '728x90');
  // The headline on the 728x90 was its own correction (22px) laid over the
  // carried color: both survive, as this size's own values.
  assert.equal(out.styleOverrides?.headline?.size, 22);
  assert.equal(out.styleOverrides?.headline?.color, '#FF0000');
  assert.equal(out.styleOverrides?.cta?.x, looked?.cta?.x, 'the carried button position becomes authored');
  assert.equal((out.styleOverrides as any).bySize, undefined, 'no correction survives');
  assert.deepEqual(out.droppedCorrections, ['160x600'], 'an empty correction is not reported as dropped');
  // Applied back, the adopted size carries nothing and the others carry from it.
  const next = concept({ styleOverrides: out.styleOverrides } as never);
  assert.equal(carryFor(next, '728x90').carried, false);
  assert.equal(carryFor(next, '300x250').from, '728x90');
});

test('a concept with no adjustments adopts nothing rather than an empty record', () => {
  const out = adoptLook(concept(), '300x250', ['300x250', '728x90']);
  assert.equal(out.styleOverrides, undefined);
  assert.deepEqual(out.droppedCorrections, []);
  assert.equal(out.layoutFamily, 'T01');
  assert.equal(out.layoutApplied, true);
});

test('a size\'s own layout becomes the set\'s only when it draws every size', () => {
  const c = concept({ layoutBySize: { '728x90': 'T04', '300x600': 'T02' } } as never);
  assert.equal(familyFor(c, '728x90'), 'T04');
  const all = adoptLook(c, '728x90', ['300x250', '728x90', '300x600']);
  assert.equal(all.layoutApplied, true);
  assert.equal(all.layoutFamily, 'T04');
  assert.equal(all.layoutBySize, undefined, 'every per-size pick goes');
  assert.deepEqual(all.droppedLayouts, ['300x600']);

  // Narrow T04 so it cannot draw one size in the set: the family stays this
  // size's own and the report names the size that kept it out.
  const sizes = getTemplate('T04').sizes as Record<string, unknown>;
  const kept = sizes['160x600'];
  delete sizes['160x600'];
  try {
    const some = adoptLook(c, '728x90', ['300x250', '728x90', '160x600']);
    assert.equal(some.layoutApplied, false);
    assert.equal(some.layoutFamily, 'T01');
    assert.deepEqual(some.layoutBySize, { '728x90': 'T04' });
    assert.deepEqual(some.layoutMissing, ['160x600']);
  } finally {
    sizes['160x600'] = kept;
  }
});

/* ------------------------------------------------------ client notes */

function proofFixture(t: any) {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'adb-notes-'));
  t.after(() => fs.rmSync(out, { recursive: true, force: true }));
  const store = new ProjectStore(out);
  const project = store.create({ projectName: 'Spring', client: 'Acme', domain: 'acme.test', campaignName: 'Spring', requestId: 'AD-NOTE-1' });
  const token = '11111111-2222-4333-8444-555555555555';
  const dir = path.join(out, 'client-proofs', token);
  fs.mkdirSync(dir, { recursive: true });
  const png = path.join(dir, '0.png');
  fs.writeFileSync(png, Buffer.from('89504e470d0a1a0a', 'hex'));
  const proof: ClientProof = {
    token, version: 2, projectId: project.projectId, reviewId: 'r1', revision: 'abc', client: 'Acme', campaign: 'Spring',
    createdAt: '2026-09-01T00:00:00.000Z', status: 'sent', sentAt: '2026-09-02T00:00:00.000Z',
    cells: [
      { conceptId: 'A', platform: 'google', size: '300x250', file: png, fileHash: 'x', inputHash: 'y' },
      { conceptId: 'A', platform: 'google', size: '728x90', file: png, fileHash: 'x', inputHash: 'y' },
    ],
  };
  fs.writeFileSync(path.join(dir, 'proof.json'), JSON.stringify(proof));
  return { out, store, project, token, proof };
}

test('a client note lands on the ad it was written under and on the project record', (t) => {
  const { out, store, project, token } = proofFixture(t);
  const note = commentOnClientProof(out, store, token, { cell: 'A/google/728x90', text: '  The logo is   cut off on the right ' });
  assert.equal(note.size, '728x90');
  assert.equal(note.text, 'The logo is cut off on the right');
  const saved = getClientProof(out, token);
  assert.equal(saved.comments?.length, 1);
  assert.equal(saved.status, 'sent', 'a note is not a decision');
  const fresh = store.get(project.projectId)!;
  assert.match(fresh.notes[fresh.notes.length - 1], /Client note on 728x90 \(google\), version 2: The logo is cut off/);
  // A note on the set as a whole names no size.
  const whole = commentOnClientProof(out, store, token, { cell: '', text: 'Love the blue.' });
  assert.equal(whole.size, '');
  assert.match(store.get(project.projectId)!.notes.pop()!, /Client note on the whole set/);
  // The list view reads them grouped.
  assert.equal(clientProofsByProject(out).get(project.projectId)?.[0].comments?.length, 2);
});

test('a note tells staff at once, with the size and a link to that size on the build screen', (t) => {
  // A note is written to the project record and drawn on the build screen,
  // and until now nobody heard about it until they opened that campaign. A
  // fake notifier proves the alert goes out with the size, the text and the
  // deep link -- the way notify.test.ts checks the transport itself.
  const { out, store, project, token } = proofFixture(t);
  const alerts: Array<{ project: any; proof: any; note: any; platform: string }> = [];
  commentOnClientProof(out, store, token, { cell: 'A/google/728x90', text: 'Logo is cut off.' }, (c) => alerts.push(c));
  assert.equal(alerts.length, 1, 'one note, one alert');
  assert.equal(alerts[0].note.size, '728x90');
  assert.equal(alerts[0].platform, 'google', 'the platform tells staff which ad on that size');
  assert.equal(alerts[0].note.text, 'Logo is cut off.');
  assert.equal(alerts[0].project.requestId, project.requestId, 'so the caller can build the build-screen link');
  assert.equal(alerts[0].proof.version, 2, 'and name the version in the alert');

  // A whole-set note names no size, so the build-screen link stays on the campaign.
  commentOnClientProof(out, store, token, { cell: '', text: 'Nice palette.' }, (c) => alerts.push(c));
  assert.equal(alerts[1].note.size, '');
  assert.equal(alerts[1].platform, '', 'no platform when it is about the whole set');

  // A notifier that throws must not swallow the client's note.
  const before = getClientProof(out, token).comments?.length ?? 0;
  const saved = commentOnClientProof(out, store, token, { cell: '', text: 'Also great.' }, () => { throw new Error('outbox is down'); });
  assert.equal(saved.text, 'Also great.');
  assert.equal(getClientProof(out, token).comments?.length, before + 1);
});

test('a note is refused on a size that is not on the proof, when empty, and once the proof is approved', (t) => {
  const { out, store, token, proof } = proofFixture(t);
  assert.throws(() => commentOnClientProof(out, store, token, { cell: 'A/google/999x999', text: 'x' }), /Choose an ad from this proof/);
  assert.throws(() => commentOnClientProof(out, store, token, { cell: '', text: '   ' }), /Write a note/);
  assert.throws(() => commentOnClientProof(out, store, token, { cell: '', text: 'x'.repeat(1001) }), /Write a note/);
  fs.writeFileSync(path.join(out, 'client-proofs', token, 'proof.json'),
    JSON.stringify({ ...proof, comments: Array.from({ length: COMMENT_LIMIT }, (_, i) => ({ id: String(i), cell: '', size: '', text: 'n', at: 'now' })) }));
  assert.throws(() => commentOnClientProof(out, store, token, { cell: '', text: 'one more' }), /all the notes it can hold/);
  fs.writeFileSync(path.join(out, 'client-proofs', token, 'proof.json'), JSON.stringify({ ...proof, status: 'approved' }));
  assert.throws(() => commentOnClientProof(out, store, token, { cell: '', text: 'late' }), /approved/);
});

test('the proof page has a note box under each ad and lists what was said', (t) => {
  const { out, token, proof } = proofFixture(t);
  const withNotes = { ...getClientProof(out, token), comments: [{ id: '1', cell: 'A/google/300x250', size: '300x250', text: 'Bigger <logo>', at: 'now' }] };
  const html = clientProofHtml(withNotes);
  assert.equal((html.match(/data-send-note=/g) || []).length, 3, 'one per ad, one for the set');
  assert.match(html, /Bigger &lt;logo&gt;/, 'the note is escaped');
  assert.match(html, /\/client-proof\/11111111-2222-4333-8444-555555555555\/comment/);
  const closed = clientProofHtml({ ...proof, status: 'complete' });
  assert.doesNotMatch(closed, /<button type="button" data-send-note/, 'no note box once approved');
});

test('a failed decision writes under the buttons, not over the page\'s status', (t) => {
  const { out, token } = proofFixture(t);
  const html = clientProofHtml(getClientProof(out, token));
  // The decision has its own alert line under the Approve / Request changes buttons.
  assert.match(html, /<p id="decision-said" role="status" aria-live="polite"><\/p>/);
  // The script restores the page's original status sentence on a failed send.
  assert.match(html, /const originalStatus=status\.textContent/);
  assert.match(html, /status\.textContent=originalStatus;decSay\(e\.message/);
});

test('the proof page streams cells from disk instead of inlining them as base64', (t) => {
  // A Meta set with the story and the square at 2x is ten megabytes of HTML
  // per open, on a phone. The frozen cell URL brings the page under 20 KB
  // and the browser caches the images between opens.
  const { out, token } = proofFixture(t);
  const html = clientProofHtml(getClientProof(out, token));
  assert.doesNotMatch(html, /data:image\//, 'no cell is inlined as base64');
  assert.match(html, /src="\/client-proof\/11111111-2222-4333-8444-555555555555\/cell\/0"/, 'each ad links to its own frozen file');
  assert.match(html, /src="\/client-proof\/11111111-2222-4333-8444-555555555555\/cell\/1"/);
  assert.match(html, /loading="lazy"/, 'and the browser can defer offscreen ones');
  assert.ok(html.length < 20_000, `two-cell proof page is ${html.length} bytes; the ceiling in docs/claude/83 is 20 KB`);
});

test('the proof cell URL matches only a UUID token and a digit index, so a bad index or a "../" cannot land', () => {
  // The server matches these paths with this regex; refusing at the regex
  // level means no case ever reaches the cells array with an out-of-shape
  // value. A "../" cannot occur inside \d+, and a non-UUID token cannot
  // occur inside [a-f0-9-]{36}.
  const rx = /^\/client-proof\/([a-f0-9-]{36})(?:\/(decision|download|comment|cell))?(?:\/(\d+))?$/;
  assert.ok(rx.test('/client-proof/11111111-2222-4333-8444-555555555555/cell/0'), 'a good cell path matches');
  assert.ok(rx.test('/client-proof/11111111-2222-4333-8444-555555555555/cell/17'));
  assert.ok(!rx.test('/client-proof/not-a-uuid/cell/0'), 'a non-UUID token is refused');
  assert.ok(!rx.test('/client-proof/11111111-2222-4333-8444-555555555555/cell/../etc/passwd'), 'a "../" in the index is refused');
  assert.ok(!rx.test('/client-proof/11111111-2222-4333-8444-555555555555/cell/-1'), 'a negative index is refused');
  assert.ok(!rx.test('/client-proof/11111111-2222-4333-8444-555555555555/cell/'), 'an empty index is refused');
  assert.ok(!rx.test('/client-proof/11111111-2222-4333-8444-555555555555/cell/0x'), 'a mixed index is refused');
});

/* ------------------------------------------------------------ health */

const proj = (extra: any = {}) => ({
  projectId: 'p', requestId: 'r', projectName: 'n', client: 'c', domain: '', campaignName: 'n',
  createdAt: '2026-09-01T00:00:00Z', updatedAt: '2026-09-01T00:00:00Z', status: 'in-build',
  assets: [], batches: [], notes: [], keywords: [], ...extra,
});
const cells = (...s: Array<'pass' | 'warn' | 'fail'>) => s.map((status, i) => ({ conceptId: 'A', size: `${300 + i}x250`, status }));

test('the health line says what is left, and names what is absent', () => {
  const none = campaignHealth(proj() as any, null, []);
  assert.equal(none.line, 'No review yet');
  assert.equal(none.sizes, null);
  assert.equal(none.tone, 'quiet');

  const review = { status: 'ready' as const, createdAt: 'now', cells: cells('pass', 'warn', 'fail', 'pass') };
  const some = campaignHealth(proj({ approvals: [{ conceptId: 'A', size: '300x250' }, { conceptId: 'A', size: '999x999' }] }) as any, review, []);
  assert.equal(some.line, '1 of 4 sizes approved · 1 failing · 1 with warnings');
  assert.equal(some.approved, 1, 'an approval the review does not know is not counted');
  assert.equal(some.tone, 'attention');

  const sent = campaignHealth(proj() as any, { ...review, cells: cells('pass') },
    [{ status: 'sent', version: 1, createdAt: '2026-09-10T00:00:00Z', sentAt: '2026-09-12T12:00:00Z' }]);
  assert.match(sent.line, /proof with the client Sep 12/);
  assert.equal(sent.tone, 'waiting');

  const noted = campaignHealth(proj() as any, null,
    [{ status: 'sent', version: 1, createdAt: '2026-09-10T00:00:00Z', comments: [{ at: 'x' }, { at: 'y' }] }]);
  assert.match(noted.line, /2 client notes/);
  assert.equal(noted.tone, 'attention');

  const done = campaignHealth(proj({ status: 'complete' }) as any, null, [{ status: 'complete', version: 1, createdAt: 'x', decisionAt: '2026-09-14T00:00:00Z' }]);
  assert.match(done.line, /^Delivered/);
  assert.equal(done.tone, 'done');

  const building = campaignHealth(proj() as any, { status: 'building', createdAt: 'x', cells: [] }, []);
  assert.equal(building.line, 'No review yet', 'a sheet still building is not a review');
});
