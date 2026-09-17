# SmartHub MCP Gateway — V1

A separate, read-only MCP service that exposes selected SmartHub data to MCP clients without putting the production Flask app in the MCP request path.

## Why it is separate

The gateway imports SmartHub's existing data adapters and audit logger, but runs as its own ASGI process. An MCP SDK upgrade, malformed client request, or AI integration problem therefore cannot take `smart1-hub` down.

## Endpoint

- `GET /health` — unauthenticated Render health check; never returns secrets
- `POST /mcp` — MCP Streamable HTTP; requires `Authorization: Bearer <MCP_API_TOKEN>`

The server uses the current stable `mcp` Python SDK v2 line and supports the SDK's current/legacy protocol compatibility behavior.

## V1 tools

- `search_clients`
- `get_client`
- `get_client_services`
- `get_client_websites`
- `get_campaign_inventory`
- `get_mcp_activity`

## V2 read tools

- `explain_client_identity`
- `search_clients_v2`
- `get_quickbooks_status`
- `get_client_quickbooks`
- `get_google_access_summary`
- `get_client_ga4_properties`
- `get_client_ga4_summary`
- `get_client_proposals`
- `get_client_insertion_orders`
- `get_client_performance`
- `get_client_ads_findings`

V2 client-specific tools resolve through SmartHub's canonical identity layer.
GA4 reports can run only against a property already mapped to that client in
the durable Google index; an unknown or ambiguous property is refused. Google
login/token material and internal Suite delivery identifiers are never returned.

### Periods

`get_client_performance` and `get_client_ga4_summary` take a **named** period
rather than dates: `last_7`, `last_14`, `last_30`, `last_90`, `this_month`,
`last_month`, `this_quarter`, `last_quarter`, `this_year`, `last_year`, or
`custom` with ISO `start_date`/`end_date`. `hub/periods.py` resolves the name,
so no caller -- and no model -- computes a date. Every window is complete days
(today is excluded) and every result echoes the window it read, with a human
label, so an answer can say which days it is about. `compare` is
`previous_period` (the default, always the same number of days),
`same_period_last_year`, or `none`. An unknown period is refused by name with
the list of working ones; it is never rounded to the nearest sensible guess.

### `get_client_performance`

One client's campaign table, per-platform and account totals, period-over-period
deltas, the pacing board's own rows and the prorated margin, from the
`modules/reports` fact table. Optional exact-match `platform` or `product`
filter; an unknown value is refused with the vocabulary that would work.

Only **confirmed** campaign mappings reach a figure -- `store.facts_for`'s rule
for every reader in the Hub -- and pending ones are counted and named in
`pending_campaigns` so an answer can say what is not in the total. Quarantined
days in the window are reported. A ratio with no denominator is `null`, never
`0` and never infinity. `roas` is `null` with a note naming the platform,
because no sync writes a conversion value today and one computed from
conversion counts as though they were dollars is a figure this Hub did not
measure.

The `pacing` block is `pacing.compute(client=)` passed through field for field.
It is not recomputed here: two readings of one question drift the day either is
edited, which is the failure `client_view.pacing` records about its own first
draft.

### `get_client_ads_findings`

What the twice-daily `modules/ads_builder` sweep already recorded for a
client's Google Ads account -- findings, when it last scanned, and whether that
reading is current. Account state comes from `hub/ads_status.py`, the same
function the dashboard card uses. Nothing is re-analysed. `platform="bing"`
answers *not built*, because an empty finding list from a sweep that never ran
reads as a clean account.

### Flags

`get_client_performance` and `get_client_ga4_summary` carry a `flags` list and
the `thresholds` those flags were judged against. The rules live in
`modules/reports/flags.py` and are computed in Python, never by a model: a
model asked to judge a significant drop judges differently on two runs of one
question. Each flag carries a plain-English `text` for a caller to quote
verbatim. A caller may explain *why* a flag fired; it may not change *whether*
one did, and it must not add flags of its own.

## Ask SmartHub

The Hub application exposes a read-only natural-language workspace at
`/ask-smarthub` and a floating drawer across authenticated staff pages. Its
server-side planner can select only from the V2 allowlist above. Account roles
are re-read on every request; QuickBooks reads are limited to admins, demo mode
is blocked from spending AI credits, and every question plus underlying tool
call is written to the activity log.

`hub/ask_recipes.py` is the library of questions the Hub is good at -- the
chips on `/ask-smarthub`, the dashboard, the reporting hub, Client 360, the
pacing board and the optimization page. A recipe is a question template, the
tools that question is about, and rendering guidance for the answer. It is a
**hint**, never a second allowlist: the planner's catalog is unchanged, the
recipe's tools ride in the payload as a suggestion, and every planned call is
re-validated against the caller's role exactly as for a typed question. The
rendering guidance is appended to the answer prompt and never to the planner's.
A recipe whose roles cannot reach its own tools fails at import. The recipe key
is written to the activity row as `recipe=`.

Resources:

- `smarthub://capabilities`
- `smarthub://data-freshness`

All normal tool calls are written to SmartHub's existing `hub.audit` activity log under module `mcp`.


## Tool metadata

Every exported tool includes a human-readable title and explicit MCP safety
annotations:

- `readOnlyHint: true`
- `destructiveHint: false`
- `idempotentHint: true`
- `openWorldHint: false`

The tools read from SmartHub and its bounded, configured account connections;
they do not browse or operate on an unrestricted external domain. These hints
help MCP hosts present and review the tools accurately, but they are not an
authorization mechanism. The gateway's authentication and server-side role
checks remain authoritative.

## Security model

V1 is deliberately read-only. The gateway cannot:

- post QuickBooks transactions
- change campaign budgets/status
- change a website
- send email/SMS
- create or modify client records
- create invoices/payments

Authentication is one strong bearer token stored only in Render as `MCP_API_TOKEN`. If that variable is blank, `/health` returns 503 and `/mcp` fails closed.

V2 should replace the bootstrap token with per-user OAuth/authorization and tool scopes before write actions are introduced.

## Local run

From repository root:

```bash
pip install -r mcp_gateway/requirements.txt
export MCP_API_TOKEN='use-a-long-random-value'
export KNACK_APP_ID='...'
export KNACK_API_KEY='...'
uvicorn mcp_gateway.server:app --host 0.0.0.0 --port 8000
```

Connect an MCP client to `http://127.0.0.1:8000/mcp` with an Authorization header containing the bearer token.

## Render service

Recommended settings:

- Name: `smart1-hub-mcp`
- Runtime: Python
- Region: Ohio
- Branch while testing: `feature/smarthub-mcp-v1`
- Build command: `pip install -r mcp_gateway/requirements.txt`
- Start command: `uvicorn mcp_gateway.server:app --host 0.0.0.0 --port $PORT`
- Health check: `/health`

Required environment variables for useful live client data:

- `MCP_API_TOKEN`
- `KNACK_APP_ID`
- `KNACK_API_KEY`

Recommended shared variables:

- `HUB_DATA_DIR` or the same persistent-data arrangement used by SmartHub if audit continuity is required across services
- `MCP_ACTOR=SmartHub MCP`

The first production deployment should remain on the feature branch until MCP Inspector/client checks pass, then merge to `main` and point the service at `main`.

## Next phases

1. Per-user auth + scopes (`admin`, `client_success`, `ad_ops`, `client`).
2. Universal SmartHub client ID and external-system mappings.
3. Per-user OAuth/scopes and role enforcement.
4. Draft-only proposal, IO, report, and task write tools.
5. Explicit-confirmation financial/external writes.
6. MCP Apps UI for Client 360/reporting views.
