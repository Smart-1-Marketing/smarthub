# Amazon DSP API — integration, and what has to be true before each verb

Read alongside `modules/ads_builder/amazon_ads.py` (the connection, the entity
and the async report) and `modules/reports/amazon_dsp.py` (the pull). Same
shape as `google-ads-api-integration.md`, because the same problem applies:
**"we have Amazon API access" is five separate claims**, and four of them fail
in ways that look like a working configuration.

---

## 1. The seat comes first, and it is not an API thing

Smart 1 has a self-service DSP **entity**. Everything below hangs off it:

* A client is an **advertiser** created under the entity in the DSP console.
  The API lists advertisers; it does not create them for us.
* Consent to the entity is given by an **entity admin**. A rep's own Amazon
  login presses Connect happily, consents happily, and then lists no
  profiles — which is why every screen that offers the button says who has
  to press it.
* The entity lives in **one region** (`AMAZON_ADS_REGION`, NA for us). A
  token consented against NA does not read an EU entity, and that reads as
  an entity nobody can see rather than as a region error.

## 2. The LWA app and the API application

Two different things, both on Amazon's timetable:

1. **Login with Amazon security profile** → `AMAZON_ADS_CLIENT_ID`,
   `AMAZON_ADS_CLIENT_SECRET`. The Allowed Return URL must be exactly
   `<PUBLIC_BASE_URL>/tools/ads/oauth/amazon/callback`; `/diagnostics` prints
   the string to paste, from `hub/oauth_redirects.py`, which is the same
   reading `amazon_ads.redirect_uri()` sends.
2. **Amazon Ads API access application** (advertising.amazon.com/about-api),
   as an *agency*. Until it is approved, every call is a `403` that reads
   exactly like a wrong key. `AmazonApiError.kind == "not_permitted"` keeps
   that apart from `auth`, and the sentence on `/reports/` says which it is.

## 3. Environment variables (Render, spellings as set)

| Variable | What is missing without it |
|---|---|
| `AMAZON_ADS_CLIENT_ID` | the sign-in cannot start; every call lacks its ClientId header |
| `AMAZON_ADS_CLIENT_SECRET` | the refresh token cannot be exchanged |
| `AMAZON_ADS_REFRESH_TOKEN` | optional; pins the consent across redeploys — the Hub database's copy is used until it is set |
| `AMAZON_DSP_ENTITY_ID` | the entity to act through; profile discovery matches on it |
| `AMAZON_DSP_ENTITY_PROFILE_ID` | optional; skips discovery once known |
| `AMAZON_ADS_REGION` | NA / EU / FE; picks the host. Defaults to NA |
| `AMAZON_ADS_REDIRECT_URI` | optional; defaults to the callback path on `PUBLIC_BASE_URL` |

`PUBLIC_BASE_URL` is needed too: without it the callback has no hostname to
come back to, and `connection_status()` names it alongside the rest.

The Render quoting trap and the placeholder trap both apply, and
`amazon_ads._env()` refuses both the way `hub/config.py` does: a value stored
with its quotes is read without them, and a placeholder out of `env.example`
is read as unset rather than sent to Amazon.

## 4. The ladder `preflight()` walks

| # | Rung | Fails as |
|---|---|---|
| 1 | Vars set, not placeholders | "not configured", naming them |
| 2 | Refresh token mints an access token | `invalid_grant` = consent revoked or from another LWA app, **not** a bad secret |
| 3 | Entity's profile visible to this consent | empty match = consent given by a non-admin, or wrong entity id. A `403` on the advertiser list is at this rung too, and is the API application, not the key |
| 4 | Advertisers readable | an entity with none yet: they are created in the DSP console, not by us |
| 5 | Write access in this region | **not probed** — nothing in the Hub writes an Amazon campaign, so nothing in the Hub claims this. `preflight()` answers `unprobed` |

Every unmet rung is reported at once. `/reports/amazon-check` draws the
ladder with the rung it stopped at.

## 5. What each verb needs, and what is built

| Verb | Endpoint family | Built |
|---|---|---|
| **Report** | `POST /accounts/{adv}/dsp/reports` → poll → gzip JSON at a pre-signed URL | `modules/reports/amazon_dsp.py`, the StackAdapt shape, pending under `BUDGET_SECONDS` |
| **Plan** | `POST /adsApi/v1/retrieve/campaignForecasts/dsp` (v1 headers) | `amazon_ads.forecast()` exists and nothing calls it yet; wiring it into the estimate step means Amazon's number as Amazon's, never blended with the sector benchmark |
| **Optimize** | `POST /dsp/v1/guidance/orders/list` + `POST /dsp/v1/quickactions/{id}/executions` | endpoints declared, no UI. An Optimize tab would show guidance and apply a quick action only on confirmation, logged to the activity log — the LSA rule |
| **Create** | v1 campaign / ad group / targeting / creative objects | **not built**. `/api/amazon/*` answers 501 in words that say so. Whether writes are available to this seat in this region is what a first validate-only call would settle, and none is made |

## 6. Decisions the pull carries

* Platform key **`amazon_dsp`**, label *Amazon DSP*, default product
  *Streaming TV* — all three were already in `store.PLATFORMS`,
  `provider_map.py` and `products.py` for the provider feed, and the native
  pull files under the same key rather than a second one.
* Campaign name = **order** name; the line items ride in `extras` by name.
  The unmapped queue works on the order.
* **The report's grain is finer than the fact table's, and is folded before
  it is written.** The request asks for ORDER *and* LINE_ITEM, so Amazon
  answers one row per line item per day, while `AdPerfDaily`'s key is
  (platform, account, campaign, date) with the order as the campaign. Written
  unfolded, three line items on one order-day are three upserts into one row
  and the last one silently wins — an order that spent $60 filed as $30, on a
  client-facing figure, with every screen looking healthy. So the figures are
  summed per order-day, the way `ttd_myreports` sums a split row.
* `totalCost` in the advertiser's currency, **no divisor**. A first row a
  thousand times too big means the field is wrong, not the divisor.
* **Purchases are not conversions.** `totalPurchases` and
  `totalDetailPageViews` land in `extras` under their own names and nothing
  writes either into `conversions`. What that does *not* do is stop a client
  page printing *Conversions 0* for this platform: the fact table's column
  defaults to zero and cannot say "not reported", which is the state AudioGo
  and GroundTruth are already in. Suppressing the tile is one change to the
  client view for every such platform, and it has not been made.
* `completes` only on rows where `videoComplete` is above zero.
* Fourteen-day lookback, because DSP attribution restates.
* Reconcile's month figure is a re-read of the same feed, labeled
  `independent: False`, and *not measured* tonight rather than holding the
  nightly thread for a report Amazon has not finished.
* The pre-signed download is fetched with **no Authorization header**: that
  host is not Amazon's API, and a bearer token sent there is a leak. Its
  **query string is dropped from anything written down** — the signature in
  it is what makes the URL fetchable by whoever holds it, and the usage
  ledger is rendered onto a page and pasted into chats. The ledger records
  the host and path.

## 7. First live pull — the checklist

1. Set the variables; press **Connect** on `/tools/ads/settings` **as the
   entity admin**.
2. `/diagnostics` shows the ninth OAuth row and the callback string to
   register; the Amazon Ads row there reads *connected* without probing.
3. `preflight()` reaches rung 5 and lists the advertisers —
   `/reports/amazon-check` draws it.
4. Watch `/reports/` after the next six-hourly tick: *pending* is expected on
   the first tick and rows on the second.
5. On `/reports/amazon-check`, compare a raw row to `FIELD_MAP`, then flip
   `CONFIRMED` (and each `ENDPOINTS[...]["confirmed"]` that has answered)
   only after a person has seen the shape. Until then the index line says it
   is reading a claim, and `/diagnostics` says so as a warning.

## 8. Wired, and what is deliberately not

Wired: `store.PLATFORMS` / `provider_map.py` / `products.py` (all three
already carried `amazon_dsp`), `hub/oauth_redirects.py` (the ninth flow),
`/tools/ads/connect/amazon` and `/tools/ads/oauth/amazon/callback`,
`hub/quotas.py` (`amazon_ads`, one row per call by endpoint family, and the
unrecorded-call marker), the Connect card on `/tools/ads/settings`, the row
on `/diagnostics`, the six-hourly job in `hub/scheduler.py` (pending
reportIds carried between ticks in the module's own note),
`/reports/amazon-check`, `reconcile.py`, and `test_reports_amazon_dsp.py` in
`checks.yml` (Postgres run too).

Not wired, on purpose: campaign writes of any kind; `forecast()`, which has
no caller until somebody builds the estimate step; and the guidance and
quick-action endpoints, which are declared so the paths live in one place and
have no UI.
