# Google Access — Smart 1 Hub module

Send a client one link. They sign in with Google once, and `access@smart1marketing.com`
ends up on their Analytics, Tag Manager, Ads, Business Profile and Search Console —
the same result as adding us by hand, without the three-week email thread.

## The design decision that matters

**We never hold a client credential.** The client's OAuth token exists only inside the
callback request, is used to add our agency email to their properties, and is revoked
before the response is sent. `access_type=online` means Google never issues a refresh
token in the first place, so there is nothing to leak, nothing to rotate, and nothing
that breaks when the client changes their password.

The one exception is our *own* Google Ads manager credentials (`GOOGLE_ADS_REFRESH_TOKEN`),
which are ours, not a client's, and are used to send account-link invitations.

## What is actually automatic

| Service | Automatic? | What happens |
|---|---|---|
| Google Analytics | Yes | We're added as Administrator on every GA4 property the signer can see |
| Google Tag Manager | Yes | We're added as Administrator on every GTM account the signer can see |
| Google Ads | Half | We send a manager-link invitation; the client accepts it in their own Ads UI |
| Business Profile | Once allowlisted | API exists but needs Google's approval; manual instructions until then |
| Search Console | No | Google publishes no user-management API. Manual step, tracked in the Hub |

The client-facing page says all of this in plain English rather than implying five
green checks are coming.

## Setup — start the Google side first, it is the long pole

### 1. OAuth client

In the Google Cloud console, on the project that will own this:

1. Enable **Google Analytics Admin API**, **Tag Manager API**, **Google Ads API**, and
   (if allowlisted) **My Business Account Management API**.
2. OAuth consent screen → **External**, publish it, add the privacy policy and terms URLs.
3. Credentials → **OAuth client ID** → Web application. Add the authorized redirect URI:

       https://<your-hub-domain>/connect/callback

   This must match `PUBLIC_BASE_URL` exactly, scheme and all.

### 2. App verification — do this now, not last

Every scope this module uses is **sensitive tier**:

    analytics.manage.users
    tagmanager.manage.users
    adwords
    business.manage

That means Google review: verified domain ownership, a published privacy policy, and a
screen recording of the consent flow. Historically this has run to weeks. Until it
passes, the app works only for accounts added as test users on the consent screen —
which is fine for internal testing but not for clients.

### 3. Google Ads developer token

Separate application, tied to a manager (MCC) account. Basic access is enough. Without
it, leave Ads unticked when creating links; everything else works.

### 4. Business Profile allowlist

Separate application again. Leave `GOOGLE_ACCESS_GBP_ENABLED` off until approved — with
it off the client gets clear manual instructions instead of a 403 that looks like their
fault. Confirm the current requirements in Google's docs before applying; that programme
has changed before.

## Environment

```
# Required
PUBLIC_BASE_URL=https://hub.smart1marketing.com
GOOGLE_ACCESS_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_ACCESS_CLIENT_SECRET=...
GOOGLE_ACCESS_AGENCY_EMAIL=access@smart1marketing.com

# Shown to clients
GOOGLE_ACCESS_AGENCY_NAME=Smart 1 Marketing
GOOGLE_ACCESS_SUPPORT_EMAIL=hello@smart1marketing.com
GOOGLE_ACCESS_SUPPORT_PHONE=(555) 010-0100

# Google Ads (optional — Ads invitations are skipped without these)
GOOGLE_ADS_DEVELOPER_TOKEN=...
GOOGLE_ADS_MANAGER_ID=1234567890
GOOGLE_ADS_REFRESH_TOKEN=...
GOOGLE_ADS_CLIENT_ID=          # defaults to GOOGLE_ACCESS_CLIENT_ID
GOOGLE_ADS_CLIENT_SECRET=      # defaults to GOOGLE_ACCESS_CLIENT_SECRET
GOOGLE_ADS_API_VERSION=v21

# Optional
GOOGLE_ACCESS_GBP_ENABLED=false
GOOGLE_ACCESS_INVITE_TTL_DAYS=14
GOOGLE_ACCESS_RATE_LIMIT=60
```

`PUBLIC_BASE_URL` is already used by the Scans module. Note the Scans post-mortem: it
ships blank in `render.yaml` with `sync: false`. **Set it, or every client link will
point at nothing.** `/tools/google-access/api/health` reports what is missing.

## Merging into smarthub

1. Drop `modules/google_access/` into `smarthub/modules/`.
2. In `models.py`, delete the standalone fallback and use `from hub.extensions import db`
   (marked with a MERGE NOTE), then delete `init_standalone_db`.
3. In the Hub app factory:

   ```python
   from modules.google_access import register_google_access
   register_google_access(app)
   ```

4. **Exempt `/connect` from the AuthGuard.** `register_google_access` appends it to
   `app.config["AUTH_EXEMPT_PREFIXES"]`; confirm the Hub's guard reads that key. If it
   uses a different mechanism, wire it up — otherwise clients get bounced to a Hub login.
5. Add a HubBar / Tools link to `/tools/google-access`.
6. Templates `_admin_base.html` — swap for `{% extends "base.html" %}` to pick up the
   HubBar and version footer, then delete the file.
7. Bump `hub/version.py`.

No new Python dependencies. Everything talks to Google over `requests`, which the Hub
already has.

## Client 360 card

`GET /tools/google-access/api/client/<hub_client_id>/status` returns the latest request
and per-service status, ready to render as an access card next to the existing Client
Images card:

```json
{"ok": true, "found": true, "status": "waiting",
 "consent_email": "owner@riversidehvac.com",
 "services": {"ga4": {"status": "granted", "role": "Administrator",
                      "resource": "Riverside HVAC - Web", "granted_at": "..."}}}
```

## Tests

`python3 test_google_access.py` — 62 checks, offline, Google mocked at the HTTP
boundary so call construction, error handling and token revocation are all exercised.
Covers the happy path, partial failure, declined consent, expiry, replayed OAuth state,
rate limiting, and that raw Google errors reach the database but never the client.

## Known gaps

- **No periodic re-verification.** We record what we were granted but do not yet poll to
  confirm it is still there. Clients revoke access by accident constantly. The next step
  is a nightly job using agency GA4/GTM credentials — `google_client.verify_ga4()` is
  the start of it.
- **No reminder emails.** Requests that sit in `waiting` are visible in the Hub but
  nobody is nudged. Hook to the Hub's existing mail path.
- **Ads status is polled on demand**, not on a schedule.
- **GA4 and GTM grant everything the signer can see.** If a client's Google account also
  holds unrelated properties, we get added to those too. If that becomes a problem, add
  a property picker between consent and grant.
