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

Resources:

- `smarthub://capabilities`
- `smarthub://data-freshness`

All normal tool calls are written to SmartHub's existing `hub.audit` activity log under module `mcp`.

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
3. GA4, proposal, IO, QuickBooks read tools.
4. Draft-only write tools.
5. Explicit-confirmation financial/external writes.
6. MCP Apps UI for Client 360/reporting views.
