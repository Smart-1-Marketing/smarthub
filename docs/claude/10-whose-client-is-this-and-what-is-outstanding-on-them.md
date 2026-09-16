## Whose client is this, and what is outstanding on them

`hub/client_owner.py`, `hub/client_health.py`, `/my-clients` and
`/qa/client-owners`. Every report in this Hub answers a question about *the
book* — which sites nobody is billing, which campaigns are waiting on artwork,
which quotes have gone unopened. Not one of them answers the question a person
actually opens the Hub with, which is **"what is on my desk?"**, because
nothing anywhere recorded whose desk a client was on. A rep read the whole
list, recognised four names, and the other hundred and fifty were somebody's.
Client 360 was no help either: it answers *what do we know about this client*
in nineteen cards and says nothing at all about what needs doing.

So the six things that actually stall a campaign were spread across six
screens — a clarification sitting on an insertion order, a spot nobody has
made, a proposal the client never opened, a price about to lapse, an IO ending
in three weeks, a dashboard that was never built — each with a report of its
own, each report a list of every client at once.

**An assignment is a Hub overlay and is never written to Knack.** The rule
`hub/client_urls.py` and `hub/client_groups.py` both work to: removing one
leaves the client record exactly as it was. **The client is stored by name and
the person by email** — never the derived key, for the reason
`hub/client_key.py` gives at length, and never a display name, because two
people on this roster share a first name and `hub/celebrations.mine()` has
already paid for that. **A client has at most one owner**: two makes "whose
client is this?" unanswerable, which is the whole question the record exists
to answer. Reassigning is allowed and carries who held it before — a handover
is the ordinary case, and refusing it would be two presses to record one
decision.

**A media partner is a standing rule, resolved on read rather than written
into rows.** "Everything Moto Media carries belongs to Erik" is the ordinary
shape of this book, and expanding it into one assignment per client meant a
re-press every time a partner gained one — which in practice means the new
client belongs to nobody until somebody notices.

What makes a standing rule safe is that it can never quietly become the only
account of who owns a client, and four things carry that:

- **A rule materialises nothing.** `resolved()` lays it over the direct
  assignments on every read, so a rule that changes, or a client who leaves
  the partner, takes effect at once and leaves no orphan row behind — and
  clearing a rule needs no row-by-row undo, because there were never any rows.
  The reason `hub/creative_evergreen.py` applies its mark on read: two
  gunicorn workers, and rows written by one of them are a second account of
  the truth. `test_client_owners.py` reads the stored file and requires a rule
  to have written no assignment rows at all.
- **Every row says where its owner came from** — `direct`, or `rule` with the
  partner named. The objection to a standing rule is that somebody ends up
  holding a client with nothing anywhere saying why; the provenance is on the
  row and all three screens print it.
- **A person beats a rule, in both directions.** A direct assignment wins, and
  taking a client off everybody **sticks**: where a rule would otherwise
  re-claim them, `unassign()` records an explicit "nobody" rather than
  deleting the row, or the press would undo itself on the next read — the
  failure `hub/client_urls.missing()` had to undo. `follow_rule()` is the way
  back, and it is its own verb rather than a second meaning for unassign,
  because "take this off everyone" and "let the rule decide again" are
  different answers and one control for both cannot say which it did.
- **Two rules claiming one client are named and refused.** A client billed
  under two media partners is under both rules, and picking between them is
  picking whose book a client is on. They are left unassigned, marked
  `contested` with both partners named — the rule `hub/suite_accounts.py`
  applies to two rows naming different sub-accounts.

The **one-off bulk assignment** stays beside it, because the two are different
statements: "these forty clients are Erik's" is a fact about those forty, and
"whatever this partner carries is Erik's" is a fact about the partner. Only
the second follows the book as it changes.

**The partner map is read once per run, not once per reader.** `resolved()`,
`summary()` and `unassign_many()` all take one, and `hub/client_health.build()`
hands over the grouping it already has — `qa.client_groups()` carries the
partners on every client's lines. Two pulls would be two answers to "who
carries this client", taken a moment apart. And the products are only read at
all when a rule exists: a book with no standing rules must not pay for a pull
it cannot use.

**A bulk assignment reports every row's own outcome, and writes the file
once.** The first half is `client_urls.accept_many()`'s rule — one number back
hides the two that failed. The second is this deployment's own arithmetic: the
largest partner here carries **eighty-seven** clients and `jsonstore` mirrors
every write into the database, so a loop calling the single-assignment path
would be eighty-seven read-modify-write cycles and eighty-seven round trips on
one press. The refusals stay per row; what is shared is the file, not the
answer.

**An owner whose account is gone is named, never read as unassigned.** "Nobody
owns this" and "the person who owned this no longer has an account" are
different situations and only the second has a handover behind it — the rule
`check_stale_json_exemptions()` works to, wearing a client. And
`assignable_users()` answers `(users, error)` with **which list answered on
every row**: the account table first, the census roster as the fallback, because
an account created or suspended since the census is only in the first, and a
picker drawn from an empty list over a table that would not answer is a screen
saying this company employs nobody.

### What "outstanding" is, and what it is not

Everything on `/my-clients` is **read**: the Knack product rows, the creative
audit, the proposal store, the review rounds, the last website reading.
Nothing is entered, so nothing can go stale by being forgotten — and each
source is asked in its own function, each returns `(answer, error)`, and **a
source that could not be read is named rather than counted as nothing**. A
client with a hole in their row must not read as a client with nothing
outstanding: that is the confident wrong answer this codebase keeps having to
undo, and here it sends a rep away from the one client who needed them. The
note at the top of the page says *"anything those would have raised is missing
from every row, not absent from it"*, which is the distinction the whole report
rests on.

Nothing is re-derived. `qa.client_groups()` is the products grouping every
report on that page already reads, `stale_creative.by_client()` is that audit
keyed on **its own** match key, `sales_status.by_client()` is `scoreboard()`
with the cap lifted, and the review rounds go through the Commercial Builder's
own `_acted_on()` — a second reading of *has this been dealt with* would put a
client's reply on this page after somebody had already handled it, or drop the
one they are waiting for. `upsell.audits_for()` gained the traffic figures
rather than this module querying the same scans table again: two readers of one
audit is how the client record and a QA report come to quote different numbers
for one business.

**Clients are sorted by how much is outstanding, worst first.** Not by name and
not by billing — the question is which client needs an hour today, and an
alphabetical list answers a different one. Ties break on the money, then the
name so the order is stable between runs.

**Every issue carries the screen it is fixed on, and that screen is never this
page.** `ISSUE_KINDS` is the table and the test asserts it: the work already
has somewhere to happen, and what was missing was knowing which client it was
about. This report finds the work; it does not become a fourteenth place to do
it.

**Ignore and Done are three states, not a Dismiss button.** "We are not going
to act on this" and "this has been dealt with" are different claims about the
same row, and folding them into one loses the only one of the two anybody would
want to audit later. Neither deletes anything: both move the row into its own
list under the client with who marked it and when, and one press puts it back —
a list that quietly gets shorter cannot be told from a report that failed to
load, `hub/creative_evergreen.py`'s rule.

**A Done mark is about the issue as it stood.** `fingerprint()` digests what
the issue actually *said*, so an asset ask that changes retires the mark and
the row says it was **superseded** rather than vanishing or standing over
something nobody has read — the shape
`modules/commercial_builder/compliance_spec.py` arrived at for a sign-off. It
is keyed on the evidence and not on the label, because rewording a label in
`ISSUE_KINDS` is our edit rather than the client's campaign changing and must
not retire every mark on the book.

**Owners, marks and notes are applied on read, never baked into the cache.**
The build is held for the day by `hub/report_cache.py` and there are two
gunicorn workers, so a mark folded into a cached payload is a button that
appears to do nothing to whichever worker did not take it —
`hub/creative_evergreen.py` had to undo exactly that with a five-minute cache
and a day-long one puts a much longer fuse on it. It is also why assigning a
client **does not** drop the cached run: where an overlay can be applied on
read, that beats dropping a cache, and dropping it would rebuild the products,
the creative audit, the proposal store and a batch of website audits once per
press.

**The search and the owner filter run per request.** They are not cache keys,
for the reason `hub/report_cache.py` gives: a free-text box types one file per
keystroke onto a 5 GB disk. The *build* is cached; the filter is not — the
split `domain_links.orphans()` already uses.

**Four kinds of nothing on the traffic block**, and each is a different thing
to do: never audited, audited and out of date, the scans table would not
answer, and a current reading. Every figure is left out where the plan did not
measure it rather than printed as a zero — a zero there reads as a claim about
the client's business instead of about our audit — and a genuine zero is kept.

**"My clients" cannot be answered from the session cookie.** It carries a
display *name* and nothing else, so `viewer()` reads the account row through
`users_routes.current_account()`. A `PANEL_PASSWORD` session has no account at
all and is **not** given somebody's book on a name match: "Shared login" is a
true statement about the session and a useless one where the whole value is
whose it is, which is `hub/ad_copy.py`'s refusal one form along. It is told so
and shown everybody's instead.

**And a zero on the dashboard card says which kind it is.** "Nothing is
outstanding on your clients" and "nobody has assigned you a client yet" render
identically as a nought and only the second is somebody's to fix. The card
reads the *same run* the page draws rather than counting again — two screens
answering "what is on my desk" separately is the `/api/db/structure` versus
`/api/integrity` trap.

**No `data-screen` and no `data-module` on either page.** A tour is offered
only where one is registered and `hub-demo.js` floats "Walk me through this"
onto any page carrying a module name, so naming one that does not exist is the
silence Smart 1 Ads shipped on Settings and Live campaigns. The bubbles that
*are* placed have entries behind them, and `test_client_owners.py` requires it:
a bubble whose key is missing is removed client-side, so the template reads as
helped and the screen shows nothing.

`test_client_owners.py` asserts all of it, including that every one of the
twelve routes refuses a stranger — they name every client, what is wrong with
each and who owns them.
