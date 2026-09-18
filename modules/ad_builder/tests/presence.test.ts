/**
 * Presence.
 *
 * Two staff editing the same campaign meet the 409 recovery dialog, which
 * merges well but late. The build screen pings /api/campaign/:id/presence
 * every 30s while open; the ledger drops a name that has not pinged for
 * 90s. The caller never sees their own name in the returned list, so the
 * "Also open by …" line is always about somebody else.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { noteOpen, openOn, sweepPresence, resetPresence, _agePresenceForTest, PRESENCE_TTL_MS } from '../src/presence';

test('two names on one campaign; each sees the other, never itself', () => {
  resetPresence();
  const forTodd = noteOpen('AD-2026-XXXXXXX1', 'Todd');
  assert.deepEqual(forTodd, [], 'first ping: nobody else here');
  const forJill = noteOpen('AD-2026-XXXXXXX1', 'Jill');
  assert.equal(forJill.length, 1, 'Todd is here');
  assert.equal(forJill[0].who, 'Todd');
  assert.ok(forJill[0].ageSec >= 0 && forJill[0].ageSec < 5);
  const forToddAgain = noteOpen('AD-2026-XXXXXXX1', 'Todd');
  assert.deepEqual(forToddAgain.map((r) => r.who), ['Jill'], 'Todd never sees himself');
});

test('a re-ping refreshes an existing row rather than adding a second one', () => {
  resetPresence();
  noteOpen('AD-2026-XXXXXXX2', 'Todd');
  _agePresenceForTest(60);
  const beforeRepeat = openOn('AD-2026-XXXXXXX2');
  assert.equal(beforeRepeat.length, 1);
  assert.ok(beforeRepeat[0].ageSec >= 60);
  noteOpen('AD-2026-XXXXXXX2', 'Todd');
  const afterRepeat = openOn('AD-2026-XXXXXXX2');
  assert.equal(afterRepeat.length, 1, 'one row per name');
  assert.ok(afterRepeat[0].ageSec < 5, 'the row was refreshed');
});

test('a name that has not pinged for 90s falls off; the ping that expired it still counts as fresh', () => {
  resetPresence();
  noteOpen('AD-2026-XXXXXXX3', 'Todd');
  noteOpen('AD-2026-XXXXXXX3', 'Jill');
  _agePresenceForTest(Math.ceil(PRESENCE_TTL_MS / 1000) + 1);
  // Todd has been quiet past the TTL; Jill pings and should not see him.
  const forJill = noteOpen('AD-2026-XXXXXXX3', 'Jill');
  assert.deepEqual(forJill, [], 'Todd is gone');
  // The ledger has one row, and it is Jill's fresh one.
  const rows = openOn('AD-2026-XXXXXXX3');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].who, 'Jill');
  assert.ok(rows[0].ageSec < 5);
});

test('an anonymous ping counts but the empty name is never drawn', () => {
  resetPresence();
  noteOpen('AD-2026-XXXXXXX4', '');
  const forJill = noteOpen('AD-2026-XXXXXXX4', 'Jill');
  assert.deepEqual(forJill, [], 'a blank name is not "somebody else"');
  const forAnon = noteOpen('AD-2026-XXXXXXX4', '');
  assert.equal(forAnon.length, 1);
  assert.equal(forAnon[0].who, 'Jill', 'the anonymous caller does see the named one');
});

test('a control character or an oversize name is trimmed before storage', () => {
  resetPresence();
  noteOpen('AD-2026-XXXXXXX5', 'X'.repeat(500));
  const forJill = noteOpen('AD-2026-XXXXXXX5', 'Jill');
  assert.equal(forJill[0].who.length, 120, 'names are bounded at 120 characters');
  noteOpen('AD-2026-XXXXXXX5', 'Todd\x00\x1f');
  const rows = openOn('AD-2026-XXXXXXX5').map((r) => r.who);
  assert.ok(rows.includes('Todd'), 'control characters are stripped, the name that remains is Todd');
});

test('the periodic sweep drops rows past their TTL, across every campaign', () => {
  resetPresence();
  noteOpen('AD-2026-XXXXXXX6', 'A');
  noteOpen('AD-2026-XXXXXXX7', 'B');
  _agePresenceForTest(Math.ceil(PRESENCE_TTL_MS / 1000) + 1);
  const dropped = sweepPresence();
  assert.equal(dropped, 2, 'both rows are past the TTL');
  assert.deepEqual(openOn('AD-2026-XXXXXXX6'), []);
  assert.deepEqual(openOn('AD-2026-XXXXXXX7'), []);
});

test('an empty requestId is a no-op that never adds a row', () => {
  resetPresence();
  const others = noteOpen('', 'Todd');
  assert.deepEqual(others, []);
});
