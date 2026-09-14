# Reports module: review and next steps

*Written 2026-09-13, against the module as merged in PR #551.*

`modules/reports` is the ad-performance reporting foundation: a fact table
fed by every platform sync, the campaign-to-client map, budget lines with
hourly pacing, a cost report, and one client-facing dashboard per client at
an unguessable link. This note records what the review found and fixed,
what it left alone on purpose, and where the two APIs now available to the
Hub — Google Places and YouTube — plug in.

## What was fixed on the way in

Each of these was verified by running the code before it was changed, and
each carries a regression check in the `test_reports_*.py` file beside it.

| Finding | What it did | What it does now |
|---|---|---|
| Two pacing engines | The staff client page summed the client's whole spend per line, filtered by platform only and never prorated to the flight; the board and the page disagreed about one line on one day. | The page is an adapter over `pacing.compute(client=)`, the board's own engine. |
| Staff login on the client's page | A failed Google token refresh printed *"adops@… is not connected"* onto the public page and PDF. | The client reads *"Website visit data is not connected for this site yet."*; the staff wording rides in `staff_note` and is stripped. |
| Margin inflated mid-month | Spend was read to today, the sold amount for the whole month. On the 20th, $3,430 spent against $8,100 sold read as 57.7%. | Sold is prorated to the same window (36.5%); the month figure is carried beside it, in the table, tiles, hint and CSV. |
| Cache blind to pricing changes | The client page's cache key carried the link's version only, and `forget()` is per gunicorn worker. A markup change kept serving the old page for fifteen minutes on one worker. | The pricing rule's timestamp and a digest of the client's mappings are in the key. |
| Pacing run fails on Postgres | `pace` is `Numeric(8,4)`; a $1/month line paces at 10,000x and Postgres refuses the row, which failed the **whole** hourly insert. SQLite ignores precision, so no test saw it. | Capped at `PACE_CAP`, band computed from the true ratio. |
| Scan count missing on Postgres | `substr()` over a timestamp column raises on Postgres; the error was swallowed and stripped, so "Site audits run" was silently absent. | `date()` on both engines. |
| Zeros for what was never measured | Completion and leads tiles were gated on every platform ever mapped, so a client whose video campaign ran last year read "Video ads completed 0". | Gated on platforms with rows in the period. |
| Smaller | A vanished provider table read as healthy; a typo'd product segment became a phantom product; a registry outage read as "no such client"; three activity-log writers wrote a row per hourly run; StackAdapt truncated fractional conversions; a form setting read by nothing; index help text that said the opposite of `pricing.py`. | All addressed. |

## Decisions that are Todd's, not the module's

**A replaced link answers 410, a never-issued one 404.** The module argues
for it: a client working from an old email is told to ask for the new link
rather than meeting a dead page. The house rule everywhere else (Smart 1
Ads, the Commercial Builder, the proposal share) is that revoked and
never-existed answer alike, because a URL that says "this one expired" tells
a prober which tokens are real. The tokens are 27 random characters, so
enumeration is not a practical risk; the question is whether one module may
differ from the rule. Left as written; worth a decision.

**Its own database.** `store.py` binds to `REPORTS_DATABASE_URL` first and
says why: the Hub's Postgres sits on a 1 GB disk and a fact table growing by
one row per campaign per day across thirteen platforms is the first thing
that would fill it. Blank, it shares `DATABASE_URL`. Creating
`smart1-reports-db` on Render is a real monthly cost; sharing is free until
it is not. Recommendation: share for the first month, watch the row count
on `/reports/`, and split when it passes a few hundred thousand rows.

**The provider.** `provider_map.py`'s column names are placeholders until
the first Windsor sync lands; `/reports/provider-check` is where they are
corrected from. Two of them are wrong by six orders of magnitude if left:
Google and Bing report cost in micros and both ship with `spend_divisor=1`.
Nothing can check this until a table exists. If Windsor is not going ahead,
the native pulls plus a CSV upload route cover the same ground.

**Three credentials.** `TTD_API_TOKEN` and `TTD_PARTNER_ID` (a long-lived
token from the platform's Developer Portal), `STACKADAPT_API_KEY`, and
AudioGo's key plus the spec PDF that `audiogo_map.py` is a placeholder for.
Until each is set, `/reports/` says "not configured" and the pull skips it.

## Known gaps, left for later on purpose

- **Scale.** `unmapped_campaigns()` reads the whole fact table for its
  latest-name pass; `facts_for()` reads every client's rows on a shared
  advertiser account; `band_history()` reads every snapshot for a line on
  every hourly run. All fine at launch. Add date filters and a per-run
  history query before the fact table passes about a million rows.
- **Per-process state.** The public rate limit counts per worker, so it is
  effectively double the configured figure. Acceptable; move it to the
  shared disk if abuse ever shows up.
- **Two spellings of one client in the store.** The staff screens and the
  auto-mapper file a campaign under the Hub-wide key from
  `hub/client_key.py` (`d:acme.com`, `n:acme`); `hub/proposal_adapters/
  reports.py` mints the link and the budget lines under the client's
  display name. Every reader that takes a key finds one of the two.
  `modules/reports/client_card.py` reads across both, so the Client 360
  card is right; the adapter should resolve through the module's own
  `_resolve_client()` so the Reports index lists one client rather than
  two. Small, and worth doing before the adapter has minted many.
- **Projection in a flight's first week** divides by seven days regardless
  of how many have run, so it understates. Minor.
- **StackAdapt's pull sleeps** up to thirty seconds on the shared scheduler
  thread while a report is prepared. Fine at one advertiser; watch it.

## Hardening, done

Five gates went in after the merge, each its own file and test, each around
one idea: a sync proposes, and a person stands behind what reaches a
client's page. `CLAUDE.md` carries the reasoning; this is the map.

| Gate | Where it is worked | What it closes |
|---|---|---|
| Feed health, one reading drawn three ways | `/status`, the dashboard's feeds card, `/reports/` | A pull failing for days, visible only to whoever opened the index and did the arithmetic per row. The SQLite fallback in production is an error naming the variable; the client's page says the day its figures run through. |
| Every reports test against Postgres | `checks.yml`, `_reports_testdb.py` | Two of the seven review findings were Postgres-only and no test could see either. |
| A campaign filed from its name waits for confirmation | `/reports/unmapped`, the client's staff page | A typo in somebody else's platform filing one client's spend under another. Not theirs is remembered, so the next run cannot undo it. |
| The provider map is read only once confirmed | `/reports/provider-check` | A placeholder column that happens to resolve filing the wrong number under the right name. Confirmed against a sample row with the spend as it would be filed; editing the map retires it. |
| Impossible rows are held, not filed | `/reports/quarantine` | More clicks than impressions, a negative figure, a day after today, a spend spike -- each reaching the client's page as a figure. A decision is about the row as it was. |
| Each platform's month against the platform's own total | `/reports/reconcile`, nightly | The fact table's sum being smaller or larger than the month the platform would invoice, with every screen internally consistent. Google's customer query is independent; a provider table or StackAdapt re-read is the same feed summed whole. |
| The client's record says what their advertising is doing | Client 360, the *Ad performance* card | Everything the module knew about a client living on `/reports/client/<key>`, reached by knowing the key. The card reads across every spelling the store files a client under and keeps four kinds of nothing apart: unread, nothing filed, all pending, no spend this period. |
| Two QA entries | `/qa`: *Campaigns With No Client*, *Lines Pacing Under* | The unmapped queue and the pacing board being pages inside a module, which is where a queue goes unworked. Both read the persisted store; a store that will not answer is not measured rather than an all-clear table. |
| A CSV door for every platform | `/reports/`, the *Upload a CSV* card | The parser being written, tested and reachable from no route. Any platform's export lands through `store.upsert_rows` -- held if it cannot be true, replaced rather than doubled -- and the watermark says `csv` wrote it. |

## Where Google Places fits

The client dashboard already has a **Google Business Profile** card that
says *"Coming soon: calls, direction requests and profile views"*
(`organic.py`, `LABELS["gbp_note"]`). That card is the hook. Two APIs answer
it, for two kinds of client:

**Business Profile Performance API** (OAuth). Calls, direction requests,
website clicks and profile impressions per day — exactly what the card
promises. Google Finder already holds the `business.manage` scope and the
`GOOGLE_GMB_ENABLED` gate, so a client whose profile is managed through a
connected login needs no new consent. The reads need the API enabled on the
project, which is the same per-project access the gate exists for.

**Places API** (key, no consent). For the majority of local businesses
whose profile nobody has connected: rating, review count, business status,
hours and the five most relevant reviews, by place. It is the live version
of what Client 360 already shows from the last Insites scan (rating, review
count, claimed, photos), which is a snapshot as of scan time.

Rules that follow from this codebase:

1. **Resolve a place once, and a person confirms it.** Text Search by name
   plus address proposes a `place_id`; a rep confirms it; two candidates
   propose neither. Stored as an overlay against the client name, the way
   the Google-account attachments are. Never matched on a substring.
2. **A daily snapshot, never a page-load call.** Place Details is billed
   (Pro SKU for rating and count; Enterprise when review text is asked
   for). The public page is opened by clients. The scheduler snapshots once
   a day into a small table; the page reads the snapshot and its date.
3. **Every call goes through `quotas.record_google()`** and the Places host
   is added to `_PROVIDER_MARKERS`, or the usage page cannot name it.
4. **No listing is not-measured, never a zero rating.** A rating is always
   printed with its review count. Review *text* is the reviewer's: count and
   rating on the client's page, text on the staff page only.
5. **Snapshot and scan stay apart.** "Observed at the last scan" and "read
   today" are two claims; the card names which.

What it gives the client's page: rating and review count with their 30-day
change, new reviews this period, and (where a login is connected) calls,
directions and website clicks. What it gives the rest of the Hub: a live
reputation reading for the upsell report's "unclaimed listing" finding, and
evidence for the Local SEO product. About two days: `hub/places.py`
(resolve, details, snapshot), one scheduler job, the card, the PDF, tests.

## Where the YouTube APIs fit

These are two different jobs and the first is nearly free.

**Paid YouTube is already arriving.** The rate card sells YouTube
(TrueView, bumpers) under the "Online Video" product, and those are Google
Ads video campaigns, which the Google Ads native pull already reads. Two
small changes make them right:

- Pull `campaign.advertising_channel_type` in the query and use it for the
  auto-mapper's default product. Today every Google Ads campaign without a
  product segment files as **Paid Search**, so a YouTube campaign reads as
  search on the client's page.
- Pull `metrics.video_quartile_p100_rate` and write completes (rate ×
  impressions) so the "Video ads completed" tile works for YouTube as it
  does for The Trade Desk.

Half a day, and it fixes a wrong product label on real client pages.

**Organic YouTube is a new section, like organic search.** For a client
whose channel we manage (Social Media Management, Online Video):

- **YouTube Data API v3** (key, public, free within quota): subscriber,
  view and video counts for the channel. A daily snapshot gives the trend
  with no consent, the same shape as the Places snapshot.
- **YouTube Analytics API** (OAuth, `yt-analytics.readonly`): views, watch
  time, subscribers gained per day, top videos, traffic sources. The scope
  is added to Google Finder's list, and every login connected before that
  keeps its old grant and has to re-consent, which CLAUDE.md records as the
  rule for any scope added later.

Gate it the way `organic.gate()` gates search: the product must be sold
**and** a channel id must be linked on the client's SEO record, or the
section is absent rather than empty. "YouTube" may be named in that section
the way "Google search" is, because it is the client's own channel and not
a platform Smart 1 buys from; it needs an entry in `products.ALLOWED` with
that reason or the forbidden-word sweep will refuse it. About two days.

## Suggested order

1. ~~Merge #551.~~ Done. Set `REPORTS_DATABASE_URL` (or leave it blank to
   share -- but set one of the two: the SQLite fallback is now an error on
   `/status` in production), set the Trade Desk token and partner id, and
   watch `/reports/` after the first six-hourly pull. Confirm each provider
   map on `/reports/provider-check` once Windsor's first tables land, and
   work the confirmation queue on `/reports/unmapped`.
2. ~~Add the Postgres run to CI.~~ Done, for every reports test.
3. ~~Client 360 card and the two QA entries.~~ Done; the dashboard line
   was already done.
4. ~~CSV upload route.~~ Done, for every platform.
5. Google Ads channel type and video completes (half a day).
6. Google Places → the Business Profile card.
7. YouTube organic section.
8. File the proposal adapter's link and lines under the module's own key
   (the "two spellings" gap above).
