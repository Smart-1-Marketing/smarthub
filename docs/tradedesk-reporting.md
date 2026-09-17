# Trade Desk reporting

The Hub factory registers `hub.reporting_routes` before its existing guarded
`create_all()` step. Schema v1 is additive and lives on the shared SQLAlchemy
metadata/connection pool, using the existing PostgreSQL DDL lock. No new app,
database, dependency or destructive migration is introduced. On an existing
deployment, restart the Hub to create the `reporting_*` tables and active-run
index. The nine taxonomy rows are seeded idempotently on first sync.

Open Diagnostics â†’ Reporting providers (`/diagnostics/reporting`). All reporting
routes require an administrator account. Mutations require JSON and reject
cross-origin requests. JSON health: `/diagnostics/reporting?format=json`.

## Configuration

- `TTD_API_TOKEN`: Trade Desk Platform API token, sent only to the official API.
  Also accepts the existing `TRADE_DESK_API`, `TRADE_DESK_API_KEY`,
  `TRADE_DESK_API_TOKEN`, and `TTD_API` names, in that precedence after
  `TTD_API_TOKEN`. Check the linked Render environment group before adding
  a service override.
- `TTD_PARTNER_ID`: the partner whose advertisers are allowed into this sync.
- `TTD_REPORT_URL`: completed, uncompressed UTF-8 CSV delivery URL from a scheduled
  Trade Desk report, on HTTPS `api.thetradedesk.com`. Credentials and signed URLs
  are never returned in health output. Redirects are rejected.

Discovery calls the Platform v3 advertiser/partner, campaign/advertiser,
adgroup/campaign and creative/advertiser query endpoints with pagination.
The configured export supplies metrics, not campaign budget or lifetime totals.
Configure a daily creative-level export with exactly one row per:
Advertiser ID + Campaign ID + Ad Group ID + Creative ID + Date + Currency.
Do not include totals or extra dimensional breakdowns in this export.

Required columns: AdvertiserId, CampaignId, Date (YYYY-MM-DD), Currency,
Impressions, and Spend (or AdvertiserCost).
Supported dimensions: AdGroupId, CreativeId, CampaignName, AdGroupName,
CreativeName, MediaType (or Channel / AdFormat / product_subtype), IsRetargeting,
CreativeWidth, CreativeHeight, CreativeSize. Spaces and underscores in headers
are ignored. Metric columns: Impressions, Clicks, AdvertiserCost (or Spend),
VideoStarts/AudioStarts (or Starts), VideoCompletions/AudioCompletions (or
Completions). Absent optional counts default to zero; rates with a zero denominator are
null. Currency must be explicit. Spend is retained as a decimal.

The integration does not create report schedules or automatically replace an
expired delivery URL. Update TTD_REPORT_URL when its delivery expires. Live
account access and account-specific report headers must be verified with a
real export before relying on production numbers. Provider API guidance:
https://partner.thetradedesk.com/v3/portal/api/ref/post-advertiser-query-partner
and report delivery changes:
https://partner.thetradedesk.com/v3/portal/reds/doc/ReportingUpcomingChanges

## Behavior and recovery

Sync Now is POST `/diagnostics/reporting/tradedesk/sync`. A database unique active-run index
excludes concurrent workers. Discovery is persisted incrementally. Source metric
rows are retained before validation; malformed, duplicate-grain or foreign-partner
rows fail the entire metrics transaction, preserving previous good facts. Repeat
syncs replace daily figures, never add them a second time. Dates missing from an
export are preserved; this does not interpret missing rows as deletion.

Provider discovery is capped at 10,000 rows per query; downloads at 25 MB/100,000
CSV rows/100 seconds; the metrics transaction stops after a 140-second overall
budget. Use smaller daily exports when a limit is reached. These limits leave
headroom under the Hub's 180-second web-worker timeout.

Unknown media stays unmapped. CTV, OTT and online video remain distinct, as do
streaming audio and podcasts. No classification is guessed from campaign names.
Only positive creative dimensions are stored; 0x0 is suppressed. Account matching
uses the Hub client registry's canonical name key. Exact name matches are only
suggestions until an admin saves them. Mapping changes record actor/time and
survive subsequent discovery. Facts join through their account to the current
mapping; no duplicated client foreign key can become stale.

Health reports counts, unmapped accounts/products, recent runs/errors, data date,
staleness and totals separated by currency. Rates are calculated from summed
numerators and denominators, not averages of row rates. Totals cover all stored
dates. No live API call is made by opening health.

If a process is forcibly terminated, its run remains `running`, visibly blocking
another sync. After confirming that no worker is executing that run, an operator
can mark that specific run `failed` with finished_at and an interruption error
in the database, then retry. No automatic timeout unlock risks overlapping writes.
Raw export history is retained in reporting_raw_payloads and discovery payloads
on entity rows; apply your database retention policy as volume grows.

## Verification

`python test_reporting.py` runs disposable SQLite integration tests and mocked
provider tests. The existing checks workflow also runs this test. PostgreSQL
uses its native ON CONFLICT and the same schema; run CI with its PostgreSQL
service for composed-app verification. Credentials are not required to boot.

The admin page is under Diagnostics because `/reports` is owned by the existing
Reports application. This canonical import does not replace that application's
scheduled sync or automatically populate its client dashboards.

CI runs this suite against disposable SQLite and PostgreSQL databases.
`REPORTING_TEST_DATABASE_URL` is a test-only override; never point it at a
production database because these tests clear the canonical reporting tables.
