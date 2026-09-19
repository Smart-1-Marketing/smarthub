/**
 * The identity the server decides, stamped over what was submitted.
 *
 * `POST /api/intake` is the one unauthenticated write in this renderer: it is
 * the form a client frames on their own marketing site. It generates the
 * requestId itself, from `crypto.randomBytes`, and the comment where it does
 * so says why -- the id doubles as the proof-link capability, so it must not
 * be enumerable.
 *
 * Two lines later that guarantee was handed back. The record was built
 * `{ requestId, receivedAt, ...body }` and the campaign `{ requestId,
 * ...body }` -- spread last, so a submission carrying its own `requestId`
 * replaced the generated one. It never reached a file path (every write uses
 * the local const), but it did reach the stored campaign, and the campaigns
 * listing reads `d.campaign?.requestId` straight back out onto the staff
 * screen. A submitter choosing the id a capability is minted from is the
 * opposite of unguessable.
 *
 * Which way round a spread goes is not a thing to re-derive at each call
 * site, so it is this function and the argument order cannot express the
 * wrong answer. `projects.save({ ...existing, ...body, projectId })` had
 * always pinned its id after the spread; this is that rule, named.
 */
export function withServerIdentity<T extends object, I extends object>(body: T, identity: I): T & I {
  return { ...body, ...identity };
}
