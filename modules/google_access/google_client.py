"""
Google API calls, over plain HTTP.

Everything Google exposes here is REST, so this module deliberately uses
`requests` rather than google-api-python-client. That keeps
`requirements.txt` unchanged, which is the pattern the rest of the Hub
follows.

Two credential paths, and they are not the same thing:

  * CLIENT tokens  -- short-lived, obtained during consent, used to add our
    agency email to their property, then revoked. Never written to disk.
  * AGENCY tokens  -- our own Google Ads manager credentials, held in env,
    used to send account-link invitations. Nothing to do with the client.
"""

import logging
import time
from urllib.parse import urlencode

import requests

from . import config

log = logging.getLogger(__name__)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

GA_ADMIN = "https://analyticsadmin.googleapis.com/v1beta"
GTM_API = "https://tagmanager.googleapis.com/tagmanager/v2"
GBP_API = "https://mybusinessaccountmanagement.googleapis.com/v1"

TIMEOUT = 25


class GoogleError(Exception):
    """An API call failed. `.public` is safe to show a client."""

    def __init__(self, detail, public="Google did not accept that request."):
        super().__init__(detail)
        self.detail = detail
        self.public = public


def _raise_for(resp, what):
    """Turn a Google error body into something with a usable message."""
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    err = (payload.get("error") or {})
    message = err.get("message") or resp.text[:300]
    status = err.get("status") or resp.status_code

    public = "Google did not accept that request."
    lowered = str(message).lower()
    if resp.status_code == 403 and "permission" in lowered:
        public = (
            "That Google account does not have permission to add users to this "
            "account. Sign in with the account that owns it, or ask its owner "
            "to run this link."
        )
    elif resp.status_code == 403:
        public = "Google refused that request for this account."
    elif resp.status_code == 404:
        public = "We could not find that account under the Google login you used."
    elif resp.status_code == 429:
        public = "Google is rate limiting us right now. Try again in a few minutes."
    elif resp.status_code >= 500:
        public = "Google returned an error. This is on their side; try again shortly."

    raise GoogleError(f"{what}: {status} {message}", public)


def _note(url, ok=True):
    """Count one Google API call against the daily quota on /diagnostics.

    Recorded before the error check, because a refused call spent a request
    against the quota exactly as an accepted one did — and a wall of 429s is
    the shape a spent quota takes.
    """
    try:
        from hub import quotas as _q
        _q.record_google(url, module="google_access", ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


def _get(url, token, params=None):
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=TIMEOUT,
    )
    _note(url, ok=resp.ok)
    if not resp.ok:
        _raise_for(resp, f"GET {url}")
    return resp.json()


def _post(url, token, body, extra_headers=None):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    headers.update(extra_headers or {})
    resp = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
    _note(url, ok=resp.ok)
    if not resp.ok:
        _raise_for(resp, f"POST {url}")
    return resp.json() if resp.text else {}


# --------------------------------------------------------------------------
# OAuth
# --------------------------------------------------------------------------

def authorization_url(state, scopes, login_hint=None):
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.redirect_uri(),
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        # No refresh token, by design. We want access that expires on its own
        # within the hour even if something goes wrong at our end.
        "access_type": "online",
        "prompt": "consent select_account",
        "include_granted_scopes": "true",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code(code):
    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": config.redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=TIMEOUT,
    )
    if not resp.ok:
        _raise_for(resp, "token exchange")
    return resp.json()


def revoke(token):
    """Best effort. A failure here must never break the client's flow."""
    if not token:
        return
    try:
        requests.post(REVOKE_ENDPOINT, params={"token": token}, timeout=10)
    except requests.RequestException as exc:
        log.warning("google_access: token revoke failed: %s", exc)


def userinfo_email(token):
    try:
        data = _get("https://www.googleapis.com/oauth2/v3/userinfo", token)
        return data.get("email")
    except GoogleError:
        return None


def granted_scopes(token):
    """Which scopes the client actually ticked. They can uncheck any of them."""
    try:
        resp = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"access_token": token},
            timeout=TIMEOUT,
        )
        if resp.ok:
            return (resp.json().get("scope") or "").split()
    except requests.RequestException:
        pass
    return []


# --------------------------------------------------------------------------
# Google Analytics 4
# --------------------------------------------------------------------------

def ga4_list_properties(token):
    """Every GA4 property the consenting user can see, flattened."""
    out = []
    page = None
    for _ in range(10):
        params = {"pageSize": 200}
        if page:
            params["pageToken"] = page
        data = _get(f"{GA_ADMIN}/accountSummaries", token, params)
        for account in data.get("accountSummaries", []):
            for prop in account.get("propertySummaries", []):
                out.append({
                    "resource": prop.get("property"),          # properties/123
                    "name": prop.get("displayName") or prop.get("property"),
                    "account": account.get("displayName"),
                    "account_resource": account.get("account"),
                })
        page = data.get("nextPageToken")
        if not page:
            break
    return out


def ga4_add_user(token, property_resource, email, role="predefinedRoles/admin"):
    """Add `email` to a GA4 property. Idempotent enough to retry safely."""
    try:
        return _post(
            f"{GA_ADMIN}/{property_resource}/accessBindings",
            token,
            {"user": email, "roles": [role]},
        )
    except GoogleError as exc:
        if "already exists" in str(exc.detail).lower():
            return {"already": True}
        raise


# --------------------------------------------------------------------------
# Google Tag Manager
# --------------------------------------------------------------------------

def gtm_list_accounts(token):
    data = _get(f"{GTM_API}/accounts", token)
    return [
        {
            "resource": a.get("path"),                  # accounts/123
            "account_id": a.get("accountId"),
            "name": a.get("name") or a.get("accountId"),
        }
        for a in data.get("account", [])
    ]


def gtm_add_user(token, account_id, email, permission="admin"):
    try:
        return _post(
            f"{GTM_API}/accounts/{account_id}/user_permissions",
            token,
            {
                "emailAddress": email,
                "accountAccess": {"permission": permission},
            },
        )
    except GoogleError as exc:
        low = str(exc.detail).lower()
        if "already" in low or "duplicate" in low:
            return {"already": True}
        raise


# --------------------------------------------------------------------------
# Google Business Profile  (behind the allowlist flag)
# --------------------------------------------------------------------------

def gbp_list_accounts(token):
    data = _get(f"{GBP_API}/accounts", token)
    return [
        {
            "resource": a.get("name"),                  # accounts/123
            "name": a.get("accountName") or a.get("name"),
        }
        for a in data.get("accounts", [])
    ]


def gbp_add_admin(token, account_resource, email, role="MANAGER"):
    try:
        return _post(
            f"{GBP_API}/{account_resource}/admins",
            token,
            {"admin": email, "role": role},
        )
    except GoogleError as exc:
        if "already" in str(exc.detail).lower():
            return {"already": True}
        raise


# --------------------------------------------------------------------------
# Google Ads
# --------------------------------------------------------------------------
# Ads does not work like the others. There is no "add this email" call. We
# send a link invitation from our manager account and the client accepts it
# inside their own Ads UI. So the client's OAuth is used only to READ which
# customer IDs they own -- saving them from hunting for a ten digit number.

def ads_list_accessible_customers(token):
    if not config.ADS_DEVELOPER_TOKEN:
        raise GoogleError(
            "GOOGLE_ADS_DEVELOPER_TOKEN is not set",
            "Google Ads is not switched on yet at our end.",
        )
    url = (
        f"https://googleads.googleapis.com/{config.ADS_API_VERSION}"
        "/customers:listAccessibleCustomers"
    )
    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "developer-token": config.ADS_DEVELOPER_TOKEN,
        },
        timeout=TIMEOUT,
    )
    if not resp.ok:
        _raise_for(resp, "listAccessibleCustomers")
    names = resp.json().get("resourceNames", [])
    return [n.split("/")[-1] for n in names]


def _ads_agency_token():
    """Exchange our stored refresh token for a short-lived access token."""
    missing = config.ads_configured()
    if missing:
        raise GoogleError(
            "missing Ads config: " + ", ".join(missing),
            "Google Ads is not switched on yet at our end.",
        )
    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "client_id": config.ADS_CLIENT_ID,
            "client_secret": config.ADS_CLIENT_SECRET,
            "refresh_token": config.ADS_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT,
    )
    if not resp.ok:
        _raise_for(resp, "agency token refresh")
    return resp.json().get("access_token")


def ads_send_link_invitation(customer_id):
    """
    Invite `customer_id` to link to our manager account.

    Returns the resource name of the pending link. The client still has to
    accept it in their own Google Ads account -- there is no way around that,
    and the client-facing copy says so.
    """
    customer_id = str(customer_id).replace("-", "").strip()
    token = _ads_agency_token()
    url = (
        f"https://googleads.googleapis.com/{config.ADS_API_VERSION}"
        f"/customers/{config.ADS_MANAGER_ID}/customerClientLinks:mutate"
    )
    body = {
        "operation": {
            "create": {
                "clientCustomer": f"customers/{customer_id}",
                "status": "PENDING",
            }
        }
    }
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "developer-token": config.ADS_DEVELOPER_TOKEN,
            "login-customer-id": config.ADS_MANAGER_ID,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=TIMEOUT,
    )
    if not resp.ok:
        _raise_for(resp, "customerClientLinks:mutate")
    result = resp.json().get("result", {})
    return result.get("resourceName")


def ads_link_status(customer_id):
    """
    Has the client accepted our invitation yet?

    Queried from our manager account, so this works without the client being
    involved -- which is what lets the Hub show live status days later.
    """
    customer_id = str(customer_id).replace("-", "").strip()
    token = _ads_agency_token()
    url = (
        f"https://googleads.googleapis.com/{config.ADS_API_VERSION}"
        f"/customers/{config.ADS_MANAGER_ID}/googleAds:search"
    )
    query = (
        "SELECT customer_client_link.status, customer_client_link.client_customer "
        "FROM customer_client_link "
        f"WHERE customer_client_link.client_customer = 'customers/{customer_id}'"
    )
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "developer-token": config.ADS_DEVELOPER_TOKEN,
            "login-customer-id": config.ADS_MANAGER_ID,
            "Content-Type": "application/json",
        },
        json={"query": query},
        timeout=TIMEOUT,
    )
    if not resp.ok:
        _raise_for(resp, "customer_client_link search")
    rows = resp.json().get("results", [])
    if not rows:
        return None
    return (rows[0].get("customerClientLink") or {}).get("status")


# --------------------------------------------------------------------------
# Verification -- is the access we were given still there?
# --------------------------------------------------------------------------

def verify_ga4(agency_token, property_resource, email):
    """Confirm `email` still holds a binding. Needs an agency GA4 token."""
    data = _get(f"{GA_ADMIN}/{property_resource}/accessBindings", agency_token)
    for binding in data.get("accessBindings", []):
        if (binding.get("user") or "").lower() == (email or "").lower():
            return True
    return False


def retry(fn, attempts=3, delay=1.5):
    """Small retry for transient 5xx/429, used around the grant calls."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except GoogleError as exc:
            last = exc
            transient = any(
                s in str(exc.detail) for s in ("429", "500", "502", "503", "504")
            )
            if not transient or i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))
    if last:
        raise last
