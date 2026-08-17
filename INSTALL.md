# Smart 1 Ads — v6.1ads

Google Ads campaign operations as a Smart 1 Hub module. Mounts at `/tools/ads`,
behind the Hub's existing login. No second password, no separate URL.

---

## Install

```bash
python3 install_into_hub.py /path/to/smarthub-main
```

That copies `modules/ads_builder/` in and makes four small edits. Every edit is
idempotent and writes a `.bak` first, so re-running it is safe. Use `--dry-run`
to see what it would touch.

Then set the environment variables below, redeploy, and open
`/tools/ads/settings` → **Connect Google Ads**.

**No new Python dependencies.** Flask, `requests` and SQLAlchemy are already in
the Hub's `requirements.txt`; the module uses nothing else.

---

## What the installer changes

If you would rather patch by hand, these are the four edits.

### 1. `wsgi.py` — three additions

In `_MOUNT_ACTIVE`, so the sidebar highlights correctly:

```python
    "/tools/image": "tools", "/tools/pdf": "tools",
    "/tools/ads": "ads",
```

Just above `def _mount(...)`, alongside the other module loads:

```python
try:
    import importlib as _il3
    adsb = _il3.import_module("modules.ads_builder.app")
    adsb_fb = None
except Exception as _ads_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    adsb, adsb_fb = None, _fallback_app("Smart 1 Ads", str(_ads_exc))
```

In the `DispatcherMiddleware` table:

```python
    "/tools/ads": _mount(adsb.app, "/tools/ads") if adsb else adsb_fb,
```

If the module ever fails to import, `_fallback_app` shows a plain explanation on
that one page and the rest of the Hub keeps working — the same pattern the other
modules use.

### 2. `hub/sidebar.py` — a new section in `_ITEMS`, above Sales

```python
    ("_secads", "", "", "Ads"),
    ("ads", "/tools/ads/", "&#9679;", "Smart 1 Ads"),
```

### 3. `hub/templates/tools.html` — one more tile

```html
  <a class="tool-tile" href="/tools/ads/">
    <div class="t-ico">&#128200;</div>
    <h3>Smart 1 Ads</h3>
    <p>Google Ads campaign operations …</p>
  </a>
```

### 4. `env.example` — the `GOOGLE_ADS_*` block

---

## Environment variables

| Variable | Where it comes from |
|---|---|
| `GOOGLE_ADS_CLIENT_ID` | Google Cloud Console → Credentials → OAuth client |
| `GOOGLE_ADS_CLIENT_SECRET` | Same screen |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Google Ads **manager** account → Tools → API Center |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | Your MCC id, digits only, no dashes |
| `GOOGLE_ADS_REDIRECT_URI` | `https://YOUR-HUB-URL/tools/ads/oauth/callback` |
| `GOOGLE_ADS_REFRESH_TOKEN` | Shown once after you connect — paste it back to pin it |
| `GOOGLE_ADS_API_VERSION` | Optional, defaults to `v25` |

The names are deliberately `GOOGLE_ADS_*` rather than `GOOGLE_*`, so this module
can never fight the Google Finder module over one OAuth client. If
`GOOGLE_ADS_CLIENT_ID` is blank it falls back to `GOOGLE_CLIENT_ID` /
`GOOGLE_CLIENT_SECRET`.

`OPENAI_API_KEY` and `OPENAI_MODEL` are shared with the SEO, FAQ Builder and
proposal tools — nothing new to set.

### Google Cloud Console

1. **APIs & Services → Library** → enable **Google Ads API**.
2. Your OAuth 2.0 Client ID (Web application):
   - Authorised JavaScript origins: your Hub URL
   - Authorised redirect URIs: `https://YOUR-HUB-URL/tools/ads/oauth/callback`
     — character for character, trailing slash included.
3. **OAuth consent screen** → scope `https://www.googleapis.com/auth/adwords`.
   Add yourself under Test users while the app is in Testing.

---

## What it does

| | |
|---|---|
| Live campaigns | Real spend, clicks, CTR, avg CPC, conversions and CPA per campaign, across any account in your MCC. Pause, enable, delete. Click a campaign to see its ad groups and keywords. |
| Campaign generator | Client details → budget slider with sector CPC viability warnings → an AI strategist builds 2–3 themed ad groups with 20–50 keywords each, RSA copy, sitelinks, callouts, structured snippets and a categorised negative keyword vault. |
| Approval hub | Status workflow (Draft → In Review → Changes Requested → Approved → Deployed) with a discussion thread. |
| Deploy | Pushes an approved proposal into Google Ads as one atomic mutate. |
| Client proposal | A clean printable page with match types stripped out. Print / Save as PDF. |
| Activity | Every generation, status change, deployment and API error, stamped with the Hub user. |

### Safety rails

- **Everything is created paused.** No campaign, ad group or ad this module
  writes can spend money until a person enables it.
- **Deployment is one atomic `googleAds:mutate`.** If any single operation
  fails, Google rolls the whole batch back — a half-built campaign is
  structurally impossible.
- **Dry run** sends the entire batch to Google's own `validateOnly` validator
  and writes nothing. It works at any status.
- **Only Approved proposals can deploy.**
- Enabling or deleting a live campaign asks for confirmation and names the
  daily budget it is about to start spending.

---

## Storage

SQLite locally, Postgres on Render through the Hub's existing `DATABASE_URL` —
the same dual-mode pattern as `scans` and `sales_builder`. Three tables:
`ads_proposals`, `ads_settings`, `ads_events`.

The Google refresh token is written to `ads_settings` so the connection works
the moment you authorise it. `GOOGLE_ADS_REFRESH_TOKEN` in the environment always
wins, because that is the copy that survives a database reset.

---

## Tests

```bash
python3 test_ads_module.py     # 83 assertions, no credentials needed
python3 ui_check.py            # headless pass over every page (needs playwright)
```

`test_ads_module.py` mounts the module the way `wsgi.py` does — DispatcherMiddleware
plus an AuthGuard that injects `s1hub.user` — then checks the API surface, the
proposal lifecycle, and the shape of the deploy payload itself: that campaigns
and ads come out `PAUSED`, that no RSA headline exceeds 30 characters, that
sitelink descriptions are paired or absent (never half, which Google rejects),
that match types survive the round trip, and that the client proposal really
does strip them.

---

## Adding Bing later

The proposal format is platform neutral. Phase two is a sibling
`modules/ads_builder/bing_ads.py` — OAuth against `login.microsoftonline.com`
with scope `https://ads.microsoft.com/msads.manage offline_access`, then the
Campaign Management service (`AddCampaigns`, `AddAdGroups`, `AddKeywords`) —
plus replacing the `/api/bing/*` 501 stub in `app.py`.

The generator, approval hub, client proposal and activity log need no changes.

---

## Files

```
modules/ads_builder/
├── __init__.py            version stamp (6.1ads)
├── app.py                 Flask routes, mounted at /tools/ads
├── google_ads.py          OAuth, REST client, reads, status changes, deploy
├── campaign_ai.py         OpenAI generator + sector CPC viability engine
├── store.py               SQLAlchemy models, settings, activity log
└── templates/             ads_base, campaigns, generator, approvals,
                           proposal, client_proposal, activity, settings,
                           connected, error
install_into_hub.py        idempotent installer
test_ads_module.py         API + deploy-payload assertions
ui_check.py                headless browser pass, writes ./shots
```
