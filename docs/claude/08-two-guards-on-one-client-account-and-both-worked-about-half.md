## Two guards on one client account, and both worked about half the time

`modules/suite_panel` creates and deletes clients' Smart 1 Suite sub-accounts.
It is the most destructive thing in this Hub, none of it is undoable from the
panel, and it had no test of its own. Three findings, and the caller got a
clean answer on every one.

**A double-submit guard held in memory is a guard on one worker in two.**
`_idem` was a dict, gunicorn runs two workers, and a resubmitted idempotency
key that landed on the worker which had not seen the first one found nothing
cached and **created a second sub-account**. That is the `_state`-is-per-process
trap this file already names for the scheduler and for `clients_registry`'s
two-minute cache, on the route where it costs a duplicate client account. The
claim is a file on the shared data disk now.

**And it was written after the work, so it never covered a double-click at
all.** `idem_get` read at the top and `idem_set` wrote once the account
existed, so two requests arriving together both found nothing cached and both
created one — which is exactly the shape a double-submit is. The key is
**claimed before the work starts**, `O_EXCL` so the claim is atomic between
workers, and a request whose twin is still in flight is refused by name rather
than creating the second account. A claim is **released** when nothing was
created — a refused name, a duplicate the rep is about to confirm, a create
that failed — or the retry replays a refusal for five minutes and the rep
concludes the panel is broken.

**A check that could not run is not a check that passed.** The duplicate
search 500s, the route logged a warning and returned a clean 201, so a rep
could not tell "there is no account of this name" from "we could not look" —
`connected_accounts_result()`'s rule, on the one check that exists to stop a
client having two accounts. It is `clear` / `not_measured` / `skipped` now, in
the response and on the activity entry. And the confirm-and-resubmit path sets
`confirmDuplicate`, which switches the check off entirely, so on the retry both
guards were down at once and nothing said so.

**A record of a deletion that names the wrong account is worse than one that
names none.** The activity entry carried `?name=` from the query string, never
checked against the account being deleted: delete `loc_9` while passing another
company's name and that is what the log said, and omitting the parameter
recorded an empty one. It is the only record that the deletion happened, and it
is what somebody reconstructs an incident from. The account is read first and
**its own name** is recorded; a read that fails does not stop the deletion —
the rep asked for it and GoHighLevel is the authority on whether it can
happen — but the claimed name is then kept as `claimedName` with
`nameSource: "not confirmed"`, never promoted to fact. A deletion GHL refused
is not written down as one.

`test_suite_panel.py` asserts all of it, including that the claim cannot
quietly go back to being per-process.
