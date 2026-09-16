# A scanned business that is not a client is a lead

Running an audit on a website is this Hub saying "somebody here thinks this
business is worth looking at". Until `modules/scans/prospect_leads.py` that
thought stopped at the scans table.

## What the gap actually was

A scan starts in one of three places, and only one of them filed a lead:

* **the widget**, where a stranger on a client's own website types a name, an
  email and a phone before the audit is even bought. `app._capture_lead()`
  has always filed that one.
* **`POST /scans/api/scans`**, which is the scan a rep runs — from `/scans`,
  from Client 360, from the Website Audit tool, from the prospect record and
  from the Sales Builder. This is most of them.
* **the bulk run**, which scans the client registry and is therefore clients
  by construction.

The middle one wrote a row with a domain, a score and a tier, and told
nothing else in the Hub. So:

* the leads panel did not have it, and that panel is the one place that
  answers "who came in, and from where";
* `hub/prospect_queue.py` could not rank it — and its band 4, *never
  audited*, is the exact mirror image of this: a lead with no audit was
  worked, an audit with no lead was not;
* `hub/prospect.py` exists to be "the record a scanned business gets before
  it is a client" and hangs off a **lead id**, so a scanned business with no
  lead had no record to open at all; and
* `_tag_lead_temperature()` has scored every completed audit hot/warm/cold
  since WO-3d and written the answer onto `Scan.lead_id` — a column that did
  not exist on the model. Every staff scan scored itself and wrote the result
  nowhere.

## The rules, each of which is a way this goes wrong instead

**A client is not a lead.** `modules/reports` wrote the reason down at its own
Suite write: a client we bill is not a lead, and a row in the leads panel
would say otherwise. The check is `hub/client_key.resolve()`, which is the
one place in this Hub that decides whether a name and a domain are somebody
we already have, and it is asked without `allow_fuzzy` — a probable match is
not a reason to withhold a prospect, and an exact one is.

**"We could not look" is not "they are not a client."** This is the failure
that would be worst and quietest: an unreadable client registry answers "not
a client" for every client in the business at once, and one bulk sweep files
the whole client book as fresh prospects. So `customer()` reads the index for
its own `error` first, treats an index that came back with **no clients at
all** as not having answered, and leaves the scan `undecided` with the reason
on it. Undecided is revisited — the refresh button, a late callback and the
scheduler's sweep over stuck rows all land back in `_apply_report()`.

**One business, one row.** A second lead for a website already in the panel
is the duplicate `hub/leads.merge_candidates()` exists to find and band 2 of
the prospect queue exists to make somebody fix. `hub/leads.find_by_domain()`
is the ask, and it **raises** where the store could not be read rather than
answering "none": a caller that swallowed that would file a duplicate every
time the database blinked.

**A lead nobody can contact is not filed.** The rule
`hub/website_audit_routes.py` and `modules/ads_builder` both arrived at
independently: a contactless lead reads as a live prospect on every count
that follows it, and `hub/ghl_contacts.upsert()` refuses it as non-retryable,
so it sits in "needs attention" for ever. The audit usually detects a phone
number, which is a contact point. Where it detects none the scan says so, and
`POST /scans/api/scans/<id>/lead` is the way back: a rep types in one they
have. That route deliberately will not override a **client** verdict —
filing a client as a lead is something this Hub does on purpose elsewhere,
and doing it because somebody pressed a button on a scan is how a business we
bill turns up in the prospect queue with nobody able to say why.

**The verdict is written down, not inferred.** `Scan.lead_state` and
`Scan.lead_note` carry which of the seven answers this scan got and why,
because "no lead" reads identically whether it was decided or dropped. The
scan detail page renders both.

**Never raises, and never blocks the scan.** A completed audit is the
transaction that matters, the same way `_seed_brand_review()` and
`_resolve_industry_from_scan()` either side of it are not allowed to cost it.

## Why it delivers rather than only capturing

`hub/lead_tags.CAPTURE_ONLY` exists for sources that write a row and
deliberately never push it to Smart 1 Suite, and a business that never asked
us for anything looks like it belongs there. It does not, and the reason is
worth writing down: `hub/leads.retry_undelivered()` sweeps **every**
undelivered row on a schedule regardless of source, so a capture-only lead
reaches Suite within the hour anyway. Capturing quietly and letting the sweep
push it would only mean the write happened where nobody was looking. So
`site_scan` is a `SOURCES` entry like every other prospect source, with no
workflow behind the tag and a note saying why there should not be one: the
business did not ask us for anything, so a workflow that mailed them off this
tag would be a cold send nobody chose.

## The columns

`scans` gains `lead_id`, `lead_state` and `lead_note`. `create_all()` never
adds a column to an existing table — the trap the root `CLAUDE.md` names — so
all three are in `_LATE_COLUMNS` in `modules/scans/app.py` as well as on the
model. None of them is indexed: `_add_missing_columns()` adds columns and not
indexes, and a model that declares an index the live Postgres does not have
is a divergence nothing reports.

## Render environment

**Nothing to add for the lead to be filed.** It is written to the Hub
database through `hub/leads.py`, which needs `DATABASE_URL` — already wired
from the Hub database in `render.yaml` and depended on by every other store.

What to **check in the env group**, because it decides whether the lead
reaches Smart 1 Suite rather than sitting queued in the panel:
`GHL_PRIVATE_TOKEN` and `GHL_LEAD_LOCATION_ID`. Unset, `delivery_mode()` is
`"none"`: the lead is still stored, still on the panel and still on the
prospect record, and the panel says the route is not configured. This is the
same pair every other lead source already depends on, so nothing here is a
new requirement — and per the root `CLAUDE.md`, nothing said from here about
what is already set is a measurement. `/status` and `/diagnostics` are where
the live answer is.
