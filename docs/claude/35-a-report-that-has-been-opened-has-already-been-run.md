## A report that has been opened has already been run

`hub/report_cache.py`. Every QA report and every report-shaped tool page
re-ran its whole build on each open — a year of QuickBooks invoices, a walk of
the GoHighLevel pipeline, a full Knack pull, a name match per row, a Cloudinary
scan of the whole account. For an answer that changes when somebody edits a
record, which is a few times a day. Two people opening the Sites Billing Report
in the same minute paid for it twice; one person pressing Back paid for it
again; the Google orphan list ran `suggest_for()` over the whole book on every
page of twenty-five, so walking two thousand orphans ran two thousand
suggestions eighty times over.

So a report runs **once, on the first open of the day**, and every open after
that reads what was written. **Refresh** re-runs it. Six rules hold it up, each
a way a cache lies.

**The day is the report's own day.** The key comes from `date.today()` — the
same clock `active_clients()` measures "this month" from and `stale_90()`
measures ninety days from. A cache on any other clock serves yesterday's rows
under today's heading on exactly the days it matters.

**A failed run never becomes the answer.** If the build raises, or comes back
carrying `error`, `unavailable` or `measured: False`, it is not stored: the
previous run is served with the failure named beside it. The `knack_products`
rule, with a second edge on it — "QuickBooks isn't connected yet" is a
perfectly successful function call and is not an answer, and storing it would
leave the page saying so all day to somebody who connected QuickBooks at ten
past nine. Which is why `is_answer()` is a shared test rather than a check per
report, and why `stale_creative.build_audit()`, `domain_links` and
`sites_match` now return `measured`: every source in all three degrades to an
empty list rather than raising, so a morning where they all refused produced a
complete-looking page saying every client was overdue for creative and every
Simvoly project was already matched. `sites_match.sites_error()` names that
one — the Sites module failing to start returned no projects at all, which is
the emptiest possible confident answer. With nothing stored to fall back on
the exception is **re-raised** rather than answered with a payload of our
own: half these reports are a columns/rows table and half are not, and a
caller handed the wrong shape fails somewhere that says nothing about why.

**The age travels with the rows.** Every payload carries a `cache` block and
every page renders its `line` — "Run at 9:14 AM today; this is that copy." A
cached figure with no date on it is read as today's, which is the whole way a
cache comes to mislead. `test_report_cache.py` asserts the line is on the page,
not merely in the payload.

**Refresh is a POST.** A GET builds only when nothing is held for today and can
never force a rebuild, however the query string is spelled — a GET that
rebuilds is one a reload, a prefetch or a link preview fires without anybody
asking, which is the entire cost this exists to stop. `?refresh=1` on Stale
Creative was exactly that door. `hub/domain_purchase.py` settled the same point
for the domain calendar.

**A write drops what it changed.** Marking an accounting request, assigning an
invoice to a partner, skipping a client that needs no dashboard, attaching a
Google property, accepting a discovered URL, matching a Simvoly project — each
takes a row off the report the button is on. Left cached, the row is still
there on the next open and the button reads as having done nothing, so it gets
pressed again. The drop lives **beside the write** rather than at the route:
`qa.skip_dashboard()` drops its own reports, `google_index._forget_reports()`
runs inside the sweep *and* `set_client()` *and* `apply_domain_matches()`, and
`client_urls._forget_registry_cache()` gained the reports next to the registry
cache it already cleared for the same reason. Two descriptions of when to
invalidate is one that drifts.

The **evergreen** mark above needs none of this, and the reason is worth
keeping: `_apply_evergreen()` is applied on every *read* of the audit rather
than baked into it, so a mark taken in one worker is never held by the other's
copy. That rule was written against a five-minute memo; a day-long hold puts a
much longer fuse on the same failure, and the mark still costs one small JSON
read per page instead of an invalidation somebody has to remember. Where an
overlay can be applied on read, that beats dropping a cache.

**A free-text search is not a cache key.** `q=acme` and `q=acm` are two files
on a 5 GB disk and a search box types one per keystroke. Where a report filters
after it builds, the *build* is cached and the filter runs per request —
`domain_links.orphans()` and `google_links.orphans()` are split that way, and
their sort moved above the search so the order does not change with what is
typed. Where it cannot be split, `cacheable()` refuses the key and the report
runs live: a slow search beats a full disk.

Entries are `durable=False` — a cache of something that rebuilds by being asked
for again, so mirroring it would cost a write per report per day for rows
nobody would restore. A deploy wipes the disk and the first person to open each
report pays for one run, which is what every open cost before this existed.

`/api/report-cache` says what is held and how old each entry is — names, days
and row counts, never payloads, because a report's rows carry client names and
this is read into a page. `/api/report-cache/clear` empties it, behind
Utilities, because pressing it makes every report on the Hub run again.
`REPORT_CACHE=off` turns the whole thing off for a deployment that must see
live numbers, and the page then says it is not cached rather than showing an
age it does not have.

**Two tests turn it off, and that is the honest thing rather than a
workaround.** `test_domain_links.py` and `test_google_links.py` swap a source
out from under a report between assertions — a Knack that answers, then one
that times out — which is the one thing a report held for the day cannot see.
They assert the report; `test_report_cache.py` asserts the holding.

**And `measured: False` was read by the cache and by nothing on the page.**
That flag is the whole reason a refused run is not frozen into the day's
answer — and `qa_report.html` never looked at it. Its branch order was
`error`, then `needs_qb`, then `unavailable`, then an all-clear, so a report
that carried its refusal in `note` alone fell through to **"Nothing to
report — all clear ✓"**. Four returns are in that shape:
`upsell._unmeasured()` when the client list or the site audits refuse,
`prospect_queue._unmeasured()` when the lead store does, and the two
uploads-gallery refusals in `qa.uploads_not_in_suite()`. So a Knack timeout
drew a green tick directly above the report's own sentence reading *"which is
not the same as nobody"*, and *"Couldn't read the uploads database"* was
rendered as every client's files being safely in Suite — the page contradicting
the line printed beneath it, which is worse than either half alone. The
page's own comment had said exactly this for three years: *"'We looked and it
is fine' and 'we could not look' are different answers, and rendering both as
a green tick is how a page ends up confidently telling you the opposite of the
truth."* It said it about `unavailable`, and `unavailable` was the one case
that already worked.

`cannotLook(d)` is the one reading now — `unavailable` keeps its action button
because it has one, and `measured: false` becomes the same panel with the note
as its message. A report that genuinely found nothing **keeps its green tick**,
because crying wolf on every clean run is its own failure and is the one that
gets a page ignored. `campaign_assets.html` had this right all along and was
the only one of the QA screens that did.

**And `is_answer()` did not read `needs_qb` either, which is the same gap on
the other side of the wire.** This module's own docstring names it — *"it is
also what stops 'QuickBooks isn't connected yet' being cached for a day by
whoever opened the report before anyone connected it"* — and `serve()`'s
comment three hundred lines down calls the connect call-to-action a payload
that "ran and could not measure". `is_answer()` tested `error`, `unavailable`
and `measured is False` and never `needs_qb`, so all three billing reports
stored it: somebody who opened Invoice Off at 08:50, before the QuickBooks
connect at 09:10, left *"QuickBooks isn't configured"* and an Open System
Status button on Customer Billing Comparison, Invoice Off and Sites Billing
for everybody for the rest of the day — and the person who had just connected
it read that as the connection having failed. A POST-Refresh cleared it; a GET
never could.

`test_qa_reports.py` asserts both directions, and it is **a sweep rather than
the four that were wrong** — a test naming those four proves nothing about the
fifth. It reads the **AST** of every module a report is built from and fails
any return of no rows carrying nothing the page draws, because three of those
modules explain this very trap in prose and a check that matches text reports
the explanation as the defect. The decision block is **lifted out of the page**
between its own markers and run in node, the arrangement `test_menu_layout.py`
uses over `hub-crumbs.js`: a copy restated in the test is a third thing to keep
in step, and a regex pinned to one line's formatting fails the day somebody
reindents it, which is how a check gets switched off.
