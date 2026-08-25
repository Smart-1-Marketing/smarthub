# Google Access — Smart 1 Hub module

Send a client one link. They sign in with Google once, and our agency account ends up
on their Analytics, Tag Manager, Business Profile and Search Console — the same result
as adding us by hand, without the three-week email thread.

**Google Ads is paused.** See *Google Ads, and why it is out* below.

## The design decision that matters

**We never hold a client credential.** The client's OAuth token exists only inside the
callback request, is used to add our agency email to their properties, and is revoked
before the response is sent. `access_type=online` means Google never issues a refresh
token in the first place, so there is nothing to leak, nothing to rotate, and nothing
that breaks when the client changes their password.

There used to be one exception — our *own* Google Ads manager credentials — and it went
with Ads.

## What is actually automatic

| Service | Automatic? | What happens |
|---|---|---|
| Google Analytics | Yes | We're added as Administrator on every GA4 property the signer can see |
| Google Tag Manager | Yes | We're added as Administrator on every GTM account the signer can see |
| Business Profile | Once allowlisted | API exists but needs Google's approval; manual instructions until then |
| Search Console | No | Google publishes no user-management API. Manual step, tracked in the Hub |

The client-facing page says all of this in plain English rather than implying five
green checks are coming.

## Google Ads, and why it is out

Ads never worked like the others. There is no "add this email" call: we send a
manager-account link invitation *from* our own MCC and the client accepts it inside
their own Ads UI. That needs three things this deployment does not have — an approved
`GOOGLE_ADS_DEVELOPER_TOKEN` (a separate Google application, against a manager account),
`GOOGLE_ADS_MANAGER_ID`, and a long-lived `GOOGLE_ADS_REFRESH_TOKEN` that is *ours*.
That last one is the only stored credential the module ever had.

Left in the list it failed in the worst available place: the client ticked Google Ads on
a page that promised it, signed in, and the grant failed at our end for a reason that was
nothing to do with them. So it is removed rather than offered-and-broken, and the admin
page says it is paused instead of carrying a banner about a feature nobody can switch on.

Bringing it back is a project, not a flag: get the developer token approved, set the
three variables, then restore the `ads` entry in `SERVICES`, its branch in
`grants.run_grants`, `grants.refresh_ads_status`, the `ads_*` helpers in
`google_client.py` and the `/api/requests/<id>/refresh` route. The PARKED note at the
top of `config.py` says the same thing, and git history at that commit has the code.

A request created before the pause still carries `"ads"` in its stored service list.
Those rows are not deleted: `config.label_for()` names the key *Google Ads (paused)*, the
record still shows it, and a human can still mark it off. What has gone is any client
ever being offered it again.

## Setup — start the Google side first, it is the long pole

### 1. OAuth client

In the Google Cloud console, on the project that will own this:

1. Enable **Google Analytics Admin API**, **Tag Manager API**, and (if allowlisted)
   **My Business Account Management API**.
2. OAuth consent screen → **External**, publish it, add the privacy policy and terms URLs.
3. Credentials → **OAuth client ID** → Web application. Add the authorized redirect URI:

       https://<your-hub-domain>/connect/callback

   This must match `PUBLIC_BASE_URL` exactly, scheme and all.

### 2. App verification — do this now, not last

Every scope this module uses is **sensitive tier**:

    analytics.manage.users
    tagmanager.manage.users
    business.manage

That means Google review: verified domain ownership, a published privacy policy, and a
screen recording of the consent flow. Historically this has run to weeks. Until it
passes, the app works only for accounts added as test users on the consent screen —
which is fine for internal testing but not for clients.

### 3. Business Profile allowlist

Separate application again. Leave `GOOGLE_ACCESS_GBP_ENABLED` off until approved — with
it off the client gets clear manual instructions instead of a 403 that looks like their
fault. Confirm the current requirements in Google's docs before applying; that programme
has changed before.

## Environment

```
# Required
PUBLIC_BASE_URL=https://hub.smart1marketing.com
GOOGLE_ACCESS_AGENCY_EMAIL=access@smart1marketing.com

# The OAuth client clients consent to. Optional in the sense that the Hub's
# shared GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET (Google Finder, Hub sign-in)
# are used when these are unset -- and the admin page NAMES which one is in
# use, because that decides whose Authorised redirect URIs need
# <PUBLIC_BASE_URL>/connect/callback on them.
GOOGLE_ACCESS_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_ACCESS_CLIENT_SECRET=...

# Shown to clients
GOOGLE_ACCESS_AGENCY_NAME=Smart 1 Marketing
GOOGLE_ACCESS_SUPPORT_EMAIL=hello@smart1marketing.com
GOOGLE_ACCESS_SUPPORT_PHONE=(555) 010-0100

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

## Who an invite is for

The form asks whether this is an **existing client** or a **new business**, and the
answer is required rather than defaulted, because it decides two different things.

*Existing* is looked up against the Hub's client registry and matched **exactly** —
`clients_registry.find_client`, no substring and no fuzzy pass, for the reason
`hub/client_key.py` gives at length. A name that matches nothing is refused with New
named as the way out, rather than accepted into a request nobody can join to a client.

*New* has no client record to join to, so the business is written through `hub/leads.py`
on the way past. Otherwise the only trace of a prospect we have just asked for Google
access is a row in this module that nothing else reads. Delivery to Smart 1 Suite only
runs when an email was given: a contact nobody can call lands in the Suite, looks
handled, and is worse than no contact. Filing a business as New when the registry
already knows the name is **refused**, not deduplicated — a duplicate contact in the
Suite is the one thing the Leads panel cannot undo.

There is no Hub client ID field. It was optional, typed by hand, and blank on nearly
every row, which is why the Client 360 access card answered "no access on file" for
clients whose Analytics we had been granted months earlier. `AccessRequest.client_key()`
derives the join from the website and the name instead, so nothing has to be filled in
for it to work. The column stays for the legacy rows that carry a value.

## Tests

`python3 test_google_access.py` — offline, no pytest, a throwaway SQLite database and a
temporary data directory. Covers the paused Ads flow (including that a request created
before the pause still renders and can still be closed), the existing/new gate, the
exact-match rule, the lead write, and that no Hub client ID is stored.

## Known gaps

- **No periodic re-verification.** We record what we were granted but do not yet poll to
  confirm it is still there. Clients revoke access by accident constantly. The next step
  is a nightly job using agency GA4/GTM credentials — `google_client.verify_ga4()` is
  the start of it.
- **No reminder emails.** Requests that sit in `waiting` are visible in the Hub but
  nobody is nudged. Hook to the Hub's existing mail path.
- **GA4 and GTM grant everything the signer can see.** If a client's Google account also
  holds unrelated properties, we get added to those too. If that becomes a problem, add
  a property picker between consent and grant.
