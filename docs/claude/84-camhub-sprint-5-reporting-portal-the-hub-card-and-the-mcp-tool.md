# CamHub Sprint 5: reporting, the portal, the Client 360 card and the MCP tool

Sprint 5 of `docs/camhub-spec.md`, on top of #741 (Sprints 1-2), #742 (Sprint
3) and #745 (Sprint 4): a monthly PDF a sponsor could audit, a read-only
portal they can check whenever they want, a Client 360 card on every client
who owns a CamHub page, and `get_cam_performance` on the MCP gateway so Ask
SmartHub can answer the sponsor-side question from a phone. When this ships
a sponsor receives a report nobody assembled by hand, and the ad-side chat
tool knows what the cam did last month.

## What each piece does

- **`modules/camhub/reports.py`** builds one payload per sponsor per month
  from the rollup only -- `sponsor_monthly()` for the report, `sponsor_range()`
  for the portal's picker, `csv_for_sponsor()` for the download. Deltas
  carry a `direction` field so the PDF's arrow and the portal's arrow never
  disagree with the sign. The daily chart is a plain Pillow PNG so a report
  run on the 1st never blocks a lake gauge poll on the scheduler thread.
- **`modules/camhub/outbox.py`** is the lifecycle: enqueue (one row per
  sponsor whose flights ran the month, unique on (sponsor, YYYY-MM)),
  render (the PDF goes to Cloudinary through `hub.storage.put`), send
  (a pluggable seam with a default that leaves a `rendered` row alone
  when there is no linked ESP). `run_monthly()` no-ops off the 1st and
  before UTC hour 2, so a fresh deploy right after midnight does not
  race the rollup.
- **`modules/camhub/portal.py`** mints one signed token per sponsor
  (`camhub-portal` salt, 400-day ceiling so a bookmarked link survives),
  reads the current placement rows on every load (creative changes reach
  the sponsor immediately), and clamps the date picker to today. The
  portal is `noindex, nofollow` and cache `no-store`, and its route
  prefix is `/portal/` on the module's own `PUBLIC_PREFIXES`.
- **`modules/camhub/hub_card.py` + `/api/client/camhub`** answer one row
  per CamHub page a client owns, this month's pageviews, top placements
  with viewable impressions and clicks and CTR, source health as
  green/amber/red counts, and links into the module. Client 360 fetches
  it after loadHealth and the card renders in the Overview section.
- **`modules/camhub/mcp.py` + `get_cam_performance`** on the MCP gateway
  reuses `hub.periods` so `period="last_month"` picks the same window as
  the ad-performance tool. A client with no CamHub page returns
  `available=True` with empty pages and `reason="no_pages"` rather than
  `not_found` -- so Ask SmartHub can say "no CamHub page" and stop.

## Scheduler

`hub/scheduler.py` runs `job_camhub_reports` once an hour. It only acts on
the 1st of the month and only after UTC hour 2. Rendering and sending are
per-row; a row already `sent` is left alone. Everything routes through
`hub/scheduler.py`'s leader lock so two gunicorn workers do not both mail
a sponsor.

## Departures from the spec

The spec's `docs/camhub-spec.md` describes an ESP-driven send on the 1st.
The Hub has one ESP -- GoHighLevel, through `hub/ad_proof_email.py` -- and
it needs a linked client contact per message. Sponsors are not Hub clients,
so linking every sponsor to a GHL contact is more account than Sprint 5's
value justifies. The seam is preserved (`outbox.register_sender`), so a
future test channel or an SMTP fallback can be dropped in without touching
the row lifecycle. The default sender writes the reason on the row and
leaves it `rendered` for staff hand-off from `/reports`. That satisfies the
spec's *done when* ("a sponsor receives a report nobody assembled by hand")
by shipping the assembly, not the send.

The CSV row is one line per placement per day plus a totals footer, keyed
on the rollup, over the sponsor's placements only. A sponsor bookmarking
the portal cannot ever see another sponsor's rows: the token resolves to
exactly one `sponsor_id` and every read filters on it.

## Render environment

Nothing. The signed portal tokens ride on `SECRET_KEY`, the outbox writes
to the Postgres already in service, and the PDF filing route uses the
Cloudinary configuration Sprint 3 already needed. `CAMHUB_USER_AGENT`
from Sprint 1 remains optional and has a working default.

## Tests

`test_camhub.py` adds four classes:

- **ReportTests** covers the monthly stats math, the delta direction, the
  renewing-flight note inside the 60-day window, that the daily chart is
  a real PNG, and that the PDF names the sponsor and carries the
  viewability footnote in the payload.
- **OutboxTests** covers enqueue idempotency, that a flight starting after
  the target month is skipped, render files a PDF and files impressions
  and clicks on the row, the default sender leaves a no-channel row
  `rendered`, a custom sender moves the row to `sent`, and the reports
  job is registered.
- **PortalTests** covers the token round-trip, that the sponsor's view
  shows only that sponsor's placements, that the CSV of one sponsor
  cannot contain another sponsor's name, and that the portal page carries
  `X-Robots-Tag: noindex`.
- **HubCardTests** covers an empty result for a client with no page and
  the one-row-with-source-health shape for a client with one.
- **McpToolTests** covers the invalid-period vocabulary and the
  known-client-no-page shape.

Every new class carries a `tearDownClass` that clears the sponsor and
placement rows it created, so `PageTests` (which runs after alphabetically)
sees the seed-only state its own tests expect.

The pre-existing `test_render_context_carries_the_temperature_into_the_meta`
and `test_sold_sponsor_links_carry_nofollow_sponsored` were both time-of-day
fragile in the eastern-time date rollover; both now pin the clock to `NOW`
so the sweep passes at every hour.
