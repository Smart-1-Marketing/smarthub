## What the ad-report syncs propose, and the person who stands behind each

`modules/reports` is the ad-performance fact table -- every platform's
campaign-days, the map from a campaign to a Hub client, the budgets those
campaigns pace against, and the one page a client reads at a link of their
own. It was mounted, reviewed and merged with seven defects fixed on the way
in (`docs/reports-review-and-next-steps.md` is the review), and then
hardened around one idea: **a sync proposes, and a person stands behind
what reaches a client's page.** Five gates, each its own file and test, and
each closing a way the module could be confidently wrong with every screen
internally consistent.

**Production is Postgres and the tests were not.** Two of the seven review
findings were Postgres-only -- `Numeric(8, 4)` refusing a pace of 10,000x
and failing the whole hourly insert, and `substr()` over a timestamp, which
Postgres has no function for -- and SQLite cannot see either by construction.
`_reports_testdb.py` is the one place a reports test binds its database:
the SQLite file by default, `REPORTS_TEST_DATABASE_URL` when set, and
`checks.yml` runs every `test_reports_*.py` a second time against the job's
Postgres. `reset()` drops and recreates the module's own tables there so a
run starts from an empty book -- `test_jsonstore.py`'s note about a fresh
directory in front of an inherited database, one engine over.

**Feed health is one reading drawn three ways.** Every platform's watermark
and newest fact day sat on `/reports/` and only there, so a pull failing for
three days was visible to whoever opened that page and did the arithmetic
per row -- the scheduler-panel failure above, wearing a feed.
`modules/reports/health.py` gives each platform one of four states (never,
failing, stale past `STALE_DAYS`, ok) and `/status`, the dashboard's feeds
card and the module's own index all read it; a store that will not answer is
*not measured*, never a healthy-looking empty list. It also refuses the
SQLite fallback in production: with neither database variable set the module
lands on a file on the data disk that every deploy wipes, and `/status` says
so as an **error** naming the variable. And the client's page says the day
its figures run through, under the title and on the PDF, and says in words
when that day is more than `DATA_STALE_DAYS` old -- a client reading "this
month" on the 13th with rows through the 8th was reading a smaller number as
the whole.

**A campaign filed from its name is a proposal.** The auto-mapper reads
`S1M | <ClientKey> | <Product> | ...` off the campaign name and files it,
and a name is somebody's typing in somebody else's platform: a typo files
one client's spend under another, on the client's own page. So the mapping
row carries `confirmed_by` / `confirmed_at` -- LATE columns, because
`create_all()` never adds one -- and `store.facts_for()`, the one reader the
client's page, its PDF, its data.json, the pacing board and the cost report
go through, returns confirmed mappings and nothing else. A person's own
mapping is confirmed by the making; the auto-mapper's waits on
`/reports/unmapped` and the client's staff page, counted on the index, the
dashboard and the pacing board (a sold line whose only campaigns are pending
reads unmapped and says how many are one press away, laid over the snapshot
rather than stored in it). **Not theirs needed memory**: refusing deletes the
row and the auto-mapper considers every campaign with no row, so without
`reports_map_refusals` the next hourly run would file the same name under the
same client again and the button would undo itself. A refusal remembers the
name it was refused under; renamed, the campaign is a new decision.

**A provider map that resolves is not read until a person confirms it.**
`provider_map.py`'s column names are placeholders until the first Windsor
sync lands, and the dangerous case is the one that resolves: `spend` is a
plausible name for a column holding micros. `/reports/provider-check` prints
the newest raw row under each mapped column with the spend **as it would be
filed** after the divisor, and a Confirm button; the normalize reads confirmed
platforms and nothing else. The confirmation is against
`provider_map.fingerprint()` -- the table, every column and the divisor, and
deliberately not `restate_days` -- so editing the map retires it and the page
reads *map changed since confirmed*, naming who confirmed the old one. A
platform that has synced and now cannot says so on its watermark; one awaiting
its first confirmation records nothing, because on a fresh deployment that is
every platform and twelve red rows for a queue about to be worked is the
check that gets switched off.

**A fact row that cannot be true is held, not filed and not refused whole.**
`modules/reports/quarantine.py` stands in `store.upsert_rows()`, the one door
every writer goes through. Four rules with their source written down: more
clicks than impressions, a negative figure, a day after today, and spend over
`SPIKE_MULTIPLIER` times the campaign's own trailing average given
`BASELINE_MIN_DAYS` of history averaging `BASELINE_MIN_SPEND` -- the divisor
rule, and those three numbers are **house**. A zero is deliberately not a
rule. The held row is kept exactly as it would have been written and the
rest of the batch goes in; `report=` on the call carries how many were held
and why. **A decision is about the row as it was**: Accept writes that row
and the same figure passes next time, Discard drops it and the same figure is
dropped in silence and counted, a different figure under the same key is a
new proposal either way, and a clean restatement from the provider supersedes
a held row by itself. The screen takes the sync's own clock (`today=`), which
is how `test_reports_pacing.py`, which drives the date, writes the days it is
about -- and its fixture stopped spending $900 on a $10/day campaign to test
"over", because that is exactly what the spike rule holds.

**The fact table's month is reconciled against the platform's own total.**
Everything a client reads is campaign-days summed, and nothing could say
whether that sum was the month the platform would invoice.
`modules/reports/reconcile.py` asks nightly, this month and the last,
**through yesterday on both sides** -- today is partial at two different
moments. Two kinds of source, and the row says which because they catch
different mistakes: Google's customer-level query (`FROM customer`, the
window as a range, nothing segmented) is *independent*, the only kind that
can catch a systematic error; a confirmed provider table summed whole or
StackAdapt's month fetched again is a *re-read*, which catches what we
dropped or never read and not the feed being wrong. The Trade Desk is not
measurable by design -- the MyReports file is the pull's own input -- and
says so. Ours counts every row of the platform, mapped or not; held rows for
the month ride on the row as the likeliest explanation of a drift;
`TOLERANCE_PCT` is house and the page says so; and a state **change** is
logged, never a state.

**Everything the module knew about a client was on a page reached by
knowing the client's key.** The campaigns filed under them, this month's
spend beside what they are billed, whether the sold lines are pacing, the
days held in quarantine, the live link and whether it was opened -- all on
`/reports/client/<key>`, and the record a rep actually opens for a client
said nothing. `modules/reports/client_card.py` is the one reading behind
Client 360's *Ad performance* card (`/api/client/ad-performance`, under
`/api/client/` so it draws inside the Suite frame), and two things in it
are decisions rather than plumbing. **A client is filed under more than
one spelling, and all of them are read**: the staff screens and the
auto-mapper file under the `hub/client_key.py` key, and
`hub/proposal_adapters/reports.py` mints its link and budget lines under
the display name -- both are real rows about one business, so
`candidate_keys()` gathers the registry key, the name key, the raw name and
any key whose stored display name is an exact normalized match, and never
a substring. And **four kinds of nothing are kept apart** -- the store would
not answer, nothing is filed, everything filed is waiting for a person to
confirm it, and the confirmed campaigns spent nothing this period --
because a card that draws the first three as the fourth is a report
answering zero when it could not look. Every figure is `client_view`'s own,
so the card and the module's page cannot disagree about a month.

**And the two queues inside the module were where a queue goes unworked.**
The unmapped list and the pacing board are pages inside a module, which is
the reason six chase lists moved to `/qa` -- so *Campaigns With No Client*
and *Lines Pacing Under* are QA reports now, reading the same persisted
store the module's pages read. A store that will not answer is
`_unmeasured()`, and a pacing job that has never run is too, because every
line reading as on pace on the strength of no reading is the confident
wrong answer. **The CSV parser has a door.** `parsers/audiogo_csv.py`
was written for AudioGo and reads the ordinary columns every export
carries, so it is the one reader behind *Upload a CSV* on `/reports/` for
any platform; the rows go through `store.upsert_rows` like a sync's, the
watermark says `csv` wrote them because a hand upload is not the feed, and
the notice names what was written, held and skipped. `test_reports_pages.py`
asserts all three, on SQLite and again on Postgres.

**And four more, each the smallest thing that closed a real hole.** The
**proposal adapter** minted the client's link and budget lines under
``run.client`` -- the display name -- because the resolver lived in the
Flask app and the adapter has no request; every reader that takes a key
then found one of the two spellings. ``store.resolve_client()`` is that
reader now, Flask-free, ``app.py``'s ``_resolve_client`` delegates to it,
and the adapter files under the module's own key. A link or a line
already sitting under the display name is found **by the name it
carries** (``links_named``, ``budget_lines_named`` -- exact on the
normalised form, never a substring) and reused, never re-minted: that
also covers a registry that answers differently on a retry, since Knack
down resolves the client to a name key and a second live link under the
new spelling is the thing ``create_link()`` exists to prevent. Nothing
is moved, and the Client 360 card goes on reading both.

**The pricing rule is bounded, and a change says what it reaches.** A
platform markup is global and live: "15" typed as "1500" is sixteen times
cost on every client page that reads the rule, the moment it is saved,
with nothing on those pages saying so. ``store.MARKUP_MAX_PCT`` and
``CPM_MAX`` are house ceilings -- no platform publishes one, and the row
says whose they are -- refused by name at every door: the platform rule,
the per-link override and the store itself, because a rule the form
keeps while the write breaks it is not a rule.
``pages_on_platform_rule()`` counts the live pages **showing Investment**
that read each platform's rule (a confirmed campaign on the platform, no
override of their own), the markup page prints it per row, and a save
that changes a rule any of them reads is **shown and confirmed** rather
than written on the first press; a change reaching none saves as it
always did, because a confirmation on every press is one nobody reads.

**Pacing alerts reach the owner's desk.** The board computed a three-day
alert on every off-pace line and told nobody -- a page inside a module,
which is where a queue goes unworked. ``hub/client_health.py`` reads the
board's own persisted run (never recomputed, so the page, the board, the
card and the QA report answer from one run) and raises ``pacing_alert``
once per alerting line, joined on the line's **client name** because
the store files a client under ``d:acme.com`` or under the display name
and only the name joins either spelling to the row. The figures that
move daily are in the title and the fingerprint is over the detail, so a
Done or Ignored mark stands while the line is in the same trouble and is
superseded when the trouble changes kind -- a mark retired by tomorrow's
spend figure is a mark nobody can keep. A job that has never run is a
named unmeasured source, never an empty book.

**And the PDF is cached like the page.** It was rebuilt on every request
while the page beside it was served from cache -- a client refreshing the
download was a reportlab render each time, on a route a stranger reaches
with no login. ``client_view.pdf_bytes()`` keys on the aggregate's own
key with a marker on the end, so a markup saved on the other worker or a
display name corrected on the staff page reaches the document exactly
when it reaches the page, and ``forget()`` drops both.

**A YouTube campaign is a Google Ads campaign, and it read as search.**
``google_ads_perf.py`` pulled the campaign and the day and no channel
type, so every Google Ads campaign whose name carried no product segment
filed under the platform default -- Paid Search -- and a TrueView buy
read as search on the client's own page. The query reads
``campaign.advertising_channel_type`` now and carries it on the row's
``extras``; ``products.GOOGLE_CHANNEL_PRODUCTS`` maps the three types that
map cleanly (VIDEO is Online Video, SEARCH is Paid Search, DISPLAY is
Programmatic Display) and **nothing else** -- Performance Max, Demand Gen
and Shopping take the platform default with the rule on the mapping row
saying so (``name_v1+default_product`` against ``+channel_product``),
because a guess filed as a product is a bar on the client's page that no
budget line can pace. The unmapped queue opens its product box on the
channel's product for the same reason a rep should not have to know
what a campaign's channel type is.

**And the completes tile had nothing to draw for Google.** Google
publishes no completes count; it publishes ``video_quartile_p100_rate``,
and rate times impressions is the figure the tile draws for the Trade
Desk. It is carried **only on a row that served video** -- the VIDEO
channel, or a row carrying views -- because a search campaign's rate is
zero and "0 completes" on it is a measurement of a metric that does not
apply. That is also why the client page's completion kind is decided
**per row** for Google rather than per platform: one account serves
search and YouTube alike, and listing ``google`` in ``COMPLETION`` would
draw "Video ads completed 0" for every search-only client, the measured
nought the tile's own gate exists to refuse.

**The projection's first week was the week it understated.** The daily
rate for ``projected_month_end`` averaged the last seven completed days
over seven however few the flight had run, so a line four days into its
flight at $150 a day projected at $85.71 a day -- on the week a projection
is read hardest. It averages over the completed days on or after the
flight start now; a line with no flight start still takes the whole
window, because nothing says when it should have begun and a zero day
inside the month is a real zero.

**And the StackAdapt wait came off the scheduler thread.** ``fetch()``
polled a report the platform was still preparing for up to thirty
seconds, asleep on the one thread every job shares -- the pacing
snapshot, the Google sweep and the Knack pulls all behind it. It is
under ``BUDGET_SECONDS`` now (twenty, house), measured by an injectable
clock, and past it the report is **pending** rather than failed: nothing
is stamped on the watermark, because nothing landed and nothing broke and
the last good pull is still the current one, whose age is what
``/status`` reads; the module's own note says so on the index line, the
job counts it apart from the failures, and the next pull asks the same
query again, which the platform answers from the report it has since
finished. ``PROGRESS_TRIES`` still caps the polls whatever the clock says,
since a platform answering Progress instantly for ever must not be polled
for ever either.

**Then a review found three more in that pull, and each read as a working
feed.** The ASSUMED names in the module docstring were always the risk; what
was missing was what happens when one of them is wrong.

* **A record with no advertiser id was filed under `advertiser:unknown`**, so
  the row still landed and the unmapped queue showed it -- a deliberate
  decision, and wrong, because `account_id` is part of `store._FACT_KEY`. The
  moment the platform did name the advertiser, the thirty-day re-read wrote a
  SECOND row for the same campaign-day, and a client's report added them
  together: fifty dollars of delivery read as a hundred, on a page somebody is
  invoiced against. It is skipped and counted now, like every other row that
  cannot be keyed. A row absent and counted is worse than a row present; a row
  that doubles a spend is worse than both.
* **Records read and none filed answered `ok`.** If `startTime` or `records`
  is not what the platform calls it, every record is skipped -- and the pull
  stamped a clean watermark, the index said *connected*, and
  `report_schedule` marked the platform complete for the day. Two hundred
  records in, nothing out, and nothing on any screen saying so. That case is
  now a failure that names the count and points at the docstring's assumed
  names.
* **A run that landed nothing overwrote the note's counts with its own
  zeroes**, so one pending tick turned *connected, 7 advertisers, last pull
  03:00* into *connected, 0 advertisers, last pull now* -- about a feed that
  had read seven the night before and had not been asked since. The counts and
  the stamp are kept for any run that landed nothing.

**A name without the mark is read for a likeness, and the likeness is
shown before it is trusted (September 17, 2026).** Most campaigns were
named before the `S1M` shape existed, and the unmapped queue held them
with the client's name plainly in the campaign name and nobody to type
it. `automap.suggest_clients()` scores every registry client against a
campaign name -- the client's name contained whole (or run together),
the client's domain label, a near spelling by `difflib`, or one
distinctive word of the name as a lead -- and the queue's picker **opens
on the likeliest client** with the rest a click away and the reason in
words, so Map is one press and a wrong guess is one click to change.
`decide()` names the one the hourly run files, and only on a clear best:
`FUZZY_FILE_SCORE` or better with the runner-up `FUZZY_MARGIN` behind,
so `Acme | Search` against Acme Plumbing and Acme Roofing files nobody
and shows both. What it files is `auto_rule="fuzzy_v1"`, a proposal like
`name_v1`'s -- pending, on no page until confirmed, refusable with the
same memory, and the refused client is never suggested for that name
again. The product is read only where a catalog name is in the campaign
name whole (`fuzzy_v1+name_product`); `search` on a Meta campaign is not
Paid Search. **Move** is the third press beside Confirm and Not theirs,
on the queue and the client's staff page: the same `POST /unmapped` a
person maps with, which replaces the proposal and is confirmed by the
making, and the activity row says where it came from. The
`hub/client_key.py` rule -- never guess silently -- holds: nothing this
pass does reaches a figure without a person's press, and every screen
that shows a likeness shows the percentage and why.

**The ad account is evidence before the name is.** Most platforms seat
one client per account, so once a person has confirmed one campaign on
an account as a client's, a new campaign there is theirs until somebody
says otherwise. `store.account_evidence()` reads the book per (platform,
account): the clients with confirmed campaigns there, the ones with only
proposals, and the ones a filing was refused under. `automap.account_suggestion()`
files on it (`account_v1`) only when exactly one client has confirmed
campaigns on the account and no refusal names that client there --
**pending proposals are not evidence**, so one wrong filing cannot become
an account's worth, and a mixed account (an agency seat, a reseller)
says nothing. Where the pull carries the platform's own name for the
account (StackAdapt's and Amazon DSP's advertiser, Microsoft's account
name, AudioGO's and GroundTruth's organization) it is read for a likeness
too, under `account_name_v1`, since it is the client's own name more
often than the campaign's is. `suggest_for_row()` merges the three
readings and `decide()` reads the merged list, so an account that says one
client and a name that says another are two clients at the top and file
neither: the run counts it as `conflicted`, and the queue shows both with
the account's reason under the account id. Every filing is still a
proposal, confirmable, refusable and movable like the rest.


**What the campaigns call a client is learned from the people who file
them.** A registry name is not always the name in the platform -- "BLW"
for Buckeye Lake Winery matches nothing the likeness pass can see. So
every mapping, confirmation and move a person makes teaches
`automap.alias_phrase()` -- the campaign name's words with the client's
own words, the `S1M` mark and the ad-ops noise (`_NOISE_WORDS`, the
catalog's products and vendors, calendar words, anything with a digit)
left out, at most `ALIAS_MAX_WORDS` of them -- as a name for that client,
in `store.CampaignAlias`, keyed on (alias, client) with a count. **Taught
once it is a suggestion** the picker opens on (`ALIAS_ONCE_SCORE`);
**taught `ALIAS_FILE_COUNT` times it files**, under `alias_v1`, a
proposal like every other; **taught for two clients it is a lead for
each and a filing for neither**, and says which. A refusal forgets what
that campaign's name taught for that client, and the queue's *learned
names* card lists every alias with who taught it, from what, and a
Forget button. The lesson rides on the person's press, never on the
auto-mapper's own filings: a proposal that teaches its own alias is a
guess reinforcing itself.


**The queue is worked a client at a time, and the board points at it.**
`/reports/unmapped?client=<key>` shows one client's proposals and the
unmapped campaigns that look like theirs; the pacing board's unmapped
lines link there twice, "N waiting for confirmation" and "N unmapped
campaigns look like theirs" (`automap.likely_by_client()`, the queue's
likeness counted per client and held per process for
`LIKELY_TTL_SECONDS`, forgotten on any mapping press; a reading that
cannot be made is None and the board draws nothing rather than none).
**Confirm ticked** confirms a batch in one press, each campaign on its
own activity row naming the batch, through the same `store.confirm_mapping`
as the single button, and "Tick the 100% ones" picks the proposals whose
evidence was exact and leaves the near spellings for a look. The product
box opens on what the campaign name says (`products.product_hint`: a
catalog name, else a synonym that means one product on every platform --
CTV, OTT, pre-roll, podcast, geofence, retargeting, PMax; never "search"
or "video" alone) and says so; a hint is for the person reading the box,
and the auto-mapper's own filings still read catalog names only.


**The thresholds are tuned on people's decisions, not on a feeling about
the queue.** `store.automap_scorecard()` counts, per rule family (the
part of `auto_rule` before any `+`), the filings a person confirmed, the
ones refused (`MapRefusal.rule`) and the ones still waiting, and the
queue draws it as a card. A rule whose refusals keep pace with its
confirmations is filing wrong more than it should, and that reading --
not the size of the queue -- is what a bar (`FUZZY_FILE_SCORE`,
`ALIAS_FILE_COUNT`, `ACCOUNT_MIN_CONFIRMED`) is raised on. An unreadable
store is *not measured*, never a scorecard of noughts.
