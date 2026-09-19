/**
 * Who else is looking at this campaign.
 *
 * Two staff editing the same campaign meet the 409 recovery dialog, which
 * merges well but late. This is an in-memory ledger the build screen pings
 * every 30 seconds while it is open; a name that has not pinged for 90
 * seconds falls off. No locking -- the recovery dialog stays the safety
 * net -- but a line under the campaign name ("Also open by Todd, 2 minutes
 * ago") means the second person notices before the conflict costs work.
 *
 * In memory rather than on disk: the renderer is a single process, and
 * presence should reset with it. A rehydration on boot would report names
 * as still open when the process (and any browser they were in) is gone.
 */

const TTL_MS = 90_000;

interface Row { who: string; at: number }

const opens = new Map<string, Row[]>();

function purge(rows: Row[]): Row[] {
  const now = Date.now();
  return rows.filter((r) => now - r.at < TTL_MS);
}

/**
 * Record that `who` is open on `requestId` right now and return the OTHERS
 * still open (so the caller never sees their own name in the returned list).
 * `who` is cleaned to a bounded ASCII display name; an anonymous request is
 * still counted, but its name is dropped from the returned list so the
 * screen never draws "Also open by anonymous".
 */
export function noteOpen(requestId: string, who: string): Array<{ who: string; ageSec: number }> {
  if (!requestId) return [];
  const cleaned = String(who ?? '').replace(/[\x00-\x1f]/g, '').trim().slice(0, 120);
  const now = Date.now();
  const existing = purge(opens.get(requestId) ?? []);
  // A person switching tabs pings twice -- one row per name, not one per ping.
  const idx = cleaned ? existing.findIndex((r) => r.who === cleaned) : -1;
  if (idx >= 0) existing[idx] = { who: cleaned, at: now };
  else existing.push({ who: cleaned || '', at: now });
  opens.set(requestId, existing);
  return existing
    .filter((r) => r.who && r.who !== cleaned)
    .map((r) => ({ who: r.who, ageSec: Math.round((now - r.at) / 1000) }));
}

/** For tests: everyone on that campaign, freshest first, ages included. */
export function openOn(requestId: string): Array<{ who: string; ageSec: number }> {
  const now = Date.now();
  const rows = purge(opens.get(requestId) ?? []);
  opens.set(requestId, rows);
  return rows.map((r) => ({ who: r.who, ageSec: Math.round((now - r.at) / 1000) }));
}

/** Drop every row past its TTL, across all campaigns. Called on a timer. */
export function sweepPresence(): number {
  let dropped = 0;
  for (const [id, rows] of opens) {
    const kept = purge(rows);
    dropped += rows.length - kept.length;
    if (!kept.length) opens.delete(id); else opens.set(id, kept);
  }
  return dropped;
}

/** For tests: clear the whole ledger. */
export function resetPresence(): void { opens.clear(); }

/** For tests: shift every row back so a next ping treats it as N seconds old. */
export function _agePresenceForTest(seconds: number): void {
  for (const [id, rows] of opens) {
    opens.set(id, rows.map((r) => ({ ...r, at: r.at - seconds * 1000 })));
  }
}

export const PRESENCE_TTL_MS = TTL_MS;
