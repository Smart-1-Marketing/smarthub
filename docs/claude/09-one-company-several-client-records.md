## One company, several client records

National Background Check and Fast Fingerprints are one business. Every
insertion order and every invoice is filed under National Background Check,
and Fast Fingerprints exists in Knack as its own client record because that is
the name on the campaign. Open it in Client 360 and it reads as a client with
no products, no invoices and no history — a confidently wrong answer of exactly
the kind this codebase treats as worse than an error.

The **Group** button sits beside Expand all / Collapse all on Client 360,
because what it changes is the whole record rather than one card: products and
IOs, creative, client notes, work, proposals and invoices are then read across
every member of the group, from whichever member you opened.

`hub/client_groups.py` owns it, and the merge happens **server-side** so a card
added later reads the group without knowing there is one — `/api/c360`,
`/api/client/work`, `/api/client/profile`, `/api/client/proposals` and
`/api/qb/invoices?client=` each resolve the roster themselves. `roster()`
always answers, and an ungrouped client comes back with `names` holding only
itself, so no caller has to branch.

Every rule in it is a way to be wrong quietly:

- **Members match exactly or not at all** — canonical domain first, exact
  normalised name second, through `hub/client_key.py`. "Riverside HVAC" must
  not collect "Riverside HVAC Supply": attributing one company's insertion
  orders to another is the worst outcome available here. `search_client()`
  matches the *query* loosely on purpose, and `_exact_client_rows()` is
  deliberately a different, stricter test.
- **A duplicate is dropped once.** A product filed under the organisation name
  is found under the parent *and* the member; merged twice it doubles the
  "Active billing" pill, and a wrong total looks exactly like a right one.
  `merge_rows()` is the only place that decides what a duplicate is.
- **Every merged row keeps its own name.** The group is a billing relationship,
  not a rename, so each row carries `member` and the page prints it — and a
  proposal merged in from another record is written back to *that* record's
  store, not the one on screen. Posting the edit against the client on screen
  would read as saved and change nothing.
- **A client is in at most one group.** Two groups claiming one company makes
  "whose bill is this on?" unanswerable. The refusal names the group that
  already holds them.
- **Removing the parent dissolves the group** rather than promoting a member.
  Which sibling holds the bill is not a question this file can answer, and
  guessing it moves every invoice on the record.
- **Nothing is written to Knack.** This is a Hub overlay, like the discovered
  URLs in `hub/client_urls.py`: ungrouping leaves both client records exactly
  as they were. Stored through `jsonstore`, as names and URLs — never the
  derived key, for the reason `client_key` gives at length.
- **A member we could not find is named.** "This member has no invoices" and
  "we never found their QuickBooks customer" are different answers, and only
  one of them means stop chasing the bill.

`test_client_groups.py` asserts all of it.
