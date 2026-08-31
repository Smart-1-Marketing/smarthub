"""
Configuration for the Google Access module.

Design rule that drives everything here: we never hold a client's Google
credentials. The client's OAuth token exists only inside a single request,
is used to add our agency email to their property, and is then revoked.
`access_type=online` is deliberate -- it means Google never issues us a
refresh token in the first place.

PARKED: Google Ads
------------------
Ads used to be the third service in the registry below. It is out for now, on
purpose, and this is the note to read before putting it back.

Ads never worked like the others. There is no "add this email" call: we send a
manager-account link invitation FROM our own MCC and the client accepts it in
their own Ads UI. That needs three things this deployment does not have --
`GOOGLE_ADS_DEVELOPER_TOKEN` (a separate Google application against a manager
account), `GOOGLE_ADS_MANAGER_ID`, and a long-lived `GOOGLE_ADS_REFRESH_TOKEN`
that is *ours*, not the client's, and so is the one credential in this module
that has to be stored.

Without a valid token the flow degraded in the worst available way: the client
ticked Google Ads on a page that said we would ask for it, consented, and the
grant failed at our end for a reason that was nothing to do with them. So the
service is removed rather than left ticked-and-failing, and the admin page no
longer carries a banner about a feature nobody can switch on.

To bring it back: get the developer token approved, set the three variables
above, restore the `ads` entry in SERVICES (mode `assisted`, scope
`https://www.googleapis.com/auth/adwords`, role text "Manager account link"),
its branch in `grants.run_grants`, `grants.refresh_ads_status`, the
`ads_*` helpers in `google_client.py` and the `/api/requests/<id>/refresh`
route. Git history at the commit that added this note has all of it.
"""

import os


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def _bool(name, default=False):
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


# --- OAuth client (the "Smart 1 Access" app clients consent to) ------------
#
# `GOOGLE_ACCESS_CLIENT_ID` is the dedicated spelling. Where it is unset we
# fall back to `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` -- the OAuth client
# Google Finder and the Hub's own Google sign-in already share, and which is
# already set on this service.
#
# The fallback is *named* on the admin page rather than applied silently,
# because which client is in use decides two things a green tick would hide:
# whose consent screen a paying client lands on, and which client's Authorised
# redirect URIs have to carry `<PUBLIC_BASE_URL>/connect/callback`. A missing
# redirect URI is a `redirect_uri_mismatch` in front of the customer, not a
# log line.
HUB_CLIENT_ID = _env("GOOGLE_CLIENT_ID")
HUB_CLIENT_SECRET = _env("GOOGLE_CLIENT_SECRET")

GOOGLE_CLIENT_ID = _env("GOOGLE_ACCESS_CLIENT_ID") or HUB_CLIENT_ID
GOOGLE_CLIENT_SECRET = _env("GOOGLE_ACCESS_CLIENT_SECRET") or HUB_CLIENT_SECRET

# Public base URL of the Hub, used to build the OAuth redirect URI.
# Must exactly match an Authorized redirect URI in the Google Cloud console.
#
# The ORIGIN, through hub.config, which is the same reading
# hub/oauth_redirects.py applies when it prints which URI to register. With
# only a trailing slash removed, a PUBLIC_BASE_URL carrying a path built
# `<base>/tools/ads/oauth/callback/connect/callback` while the panel printed
# `<origin>/connect/callback` -- and the comment twelve lines above this one
# already says what that costs: "A missing redirect URI is a
# redirect_uri_mismatch in front of the customer, not a log line." This is the
# one of the six flows the panel itself marks client-facing.
# Read at CALL time, not bound at import: this is the one variable somebody
# corrects mid-incident after the diagnostics panel names it, and a module-level
# snapshot would need a redeploy to take effect. `PUBLIC_BASE_URL` stays as a
# name because `status()` and the admin page read it, but it is a property of
# the module rather than a value frozen at import.
def _public_base_url() -> str:
    try:
        from hub.config import public_base_origin
        return public_base_origin()
    except Exception:                                 # noqa: BLE001
        from urllib.parse import urlsplit
        raw = _env("PUBLIC_BASE_URL").rstrip("/")
        if not raw:
            return ""
        p = urlsplit(raw if "://" in raw else "https://" + raw)
        return f"{p.scheme}://{p.netloc}" if p.netloc else raw


def __getattr__(name: str):
    """`config.PUBLIC_BASE_URL` resolves when it is read, not at import.

    A module-level __getattr__ rather than five edited call sites, for the
    reason hub/blueprint_guard.py sits on the blueprint rather than on forty
    views: the sixth reader added next month is right by default instead of
    having to remember. Two of the five build the link a CLIENT is emailed to
    grant us access, so a path carried into them is a 404 in front of a
    customer -- the failure this file's own comment about redirect_uri_mismatch
    already describes, one URL along.
    """
    if name == "PUBLIC_BASE_URL":
        return _public_base_url()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# --- Who we add ------------------------------------------------------------
# The agency Google account that gets added to each client property.
AGENCY_EMAIL = _env("GOOGLE_ACCESS_AGENCY_EMAIL")
AGENCY_NAME = _env("GOOGLE_ACCESS_AGENCY_NAME", "Smart 1 Marketing")
AGENCY_SUPPORT_EMAIL = _env("GOOGLE_ACCESS_SUPPORT_EMAIL")
AGENCY_SUPPORT_PHONE = _env("GOOGLE_ACCESS_SUPPORT_PHONE")

# --- Business Profile ------------------------------------------------------
# The Business Profile APIs require a separate allowlist application with
# Google. Off until that is approved, otherwise every call 403s and the
# client sees a failure that isn't their fault.
GBP_ENABLED = _bool("GOOGLE_ACCESS_GBP_ENABLED", False)

# --- Invite behaviour ------------------------------------------------------
INVITE_TTL_DAYS = int(_env("GOOGLE_ACCESS_INVITE_TTL_DAYS", "14") or 14)
STATE_TTL_MINUTES = 30

# Per-IP attempts against the public connect routes, per hour.
PUBLIC_RATE_LIMIT = int(_env("GOOGLE_ACCESS_RATE_LIMIT", "60") or 60)


# --- Service registry ------------------------------------------------------
# `automated` services are granted by us on the client's behalf during the
# OAuth callback. `manual` services need a human step somewhere -- we start
# them and track them, but cannot finish them alone. Being honest about which
# is which on the client-facing page is the whole trust proposition.
#
# Google Ads is deliberately absent. See PARKED at the top of this file.

SERVICES = {
    "ga4": {
        "label": "Google Analytics",
        "detail": "See traffic and conversions, and fix tracking when it breaks.",
        "note": None,
        "role_text": "Administrator",
        "role_code": "predefinedRoles/admin",
        "mode": "automated",
        "scopes": [
            "https://www.googleapis.com/auth/analytics.manage.users",
            "https://www.googleapis.com/auth/analytics.readonly",
        ],
    },
    "gtm": {
        "label": "Google Tag Manager",
        "detail": "Add and repair the tags that record leads, calls and form fills.",
        "note": None,
        "role_text": "Administrator",
        "role_code": "admin",
        "mode": "automated",
        "scopes": [
            "https://www.googleapis.com/auth/tagmanager.manage.users",
            "https://www.googleapis.com/auth/tagmanager.readonly",
        ],
    },
    "gbp": {
        "label": "Google Business Profile",
        "detail": "Post updates, reply to reviews and keep your hours accurate.",
        "note": (
            "We are still waiting on Google to approve automated access here, "
            "so this one is a short manual invite. Steps come next."
        ),
        "role_text": "Manager",
        "role_code": "MANAGER",
        "mode": "automated" if GBP_ENABLED else "manual",
        "scopes": [
            "https://www.googleapis.com/auth/business.manage",
        ],
    },
    "search_console": {
        "label": "Google Search Console",
        "detail": "Track which searches find you and catch indexing problems.",
        "note": (
            "Google publishes no way to automate this one, so it is a short "
            "manual add. Steps come next."
        ),
        "role_text": "Full user",
        "role_code": "full",
        # Google publishes no user-management API for Search Console. There is
        # no automated path here and there never has been -- don't pretend.
        "mode": "manual",
        "scopes": [],
    },
}

SERVICE_ORDER = ["ga4", "gtm", "gbp", "search_console"]

AUTOMATED_SERVICES = [k for k in SERVICE_ORDER if SERVICES[k]["mode"] == "automated"]

# Requests created before Ads was parked still carry it in their stored
# service list. Reading one must not KeyError, and the row must not silently
# vanish either -- `label_for` names it as retired instead.
RETIRED_SERVICES = {"ads": "Google Ads (paused)"}


def label_for(key):
    """Display name for a service key, including ones no longer offered."""
    if key in SERVICES:
        return SERVICES[key]["label"]
    return RETIRED_SERVICES.get(key, key)


def scopes_for(service_keys):
    """Minimum scope set for the services actually being requested."""
    out = []
    for key in service_keys:
        for scope in SERVICES.get(key, {}).get("scopes", []):
            if scope not in out:
                out.append(scope)
    if out:
        out.append("openid")
        out.append("https://www.googleapis.com/auth/userinfo.email")
    return out


def redirect_uri():
    # Lives on the public blueprint, not under /tools, so the Hub's AuthGuard
    # never intercepts Google's redirect and bounces the client to a login.
    # _public_base_url(), not the bare name: a module __getattr__ covers
    # readers OUTSIDE the module and not a global lookup inside it.
    return f"{_public_base_url()}/connect/callback"


def oauth_client_source():
    """Which environment variable the OAuth client actually came from.

    Rendered on the admin page. "Configured" and "configured with the client
    you think it is" are different answers, and only one of them tells you
    where to add the redirect URI.
    """
    if _env("GOOGLE_ACCESS_CLIENT_ID"):
        return "GOOGLE_ACCESS_CLIENT_ID"
    if HUB_CLIENT_ID:
        return "GOOGLE_CLIENT_ID"
    return ""


def configured():
    """Hard requirements for the client-facing flow to work at all."""
    missing = []
    if not GOOGLE_CLIENT_ID:
        missing.append("GOOGLE_ACCESS_CLIENT_ID")
    if not GOOGLE_CLIENT_SECRET:
        missing.append("GOOGLE_ACCESS_CLIENT_SECRET")
    if not _public_base_url():
        missing.append("PUBLIC_BASE_URL")
    if not AGENCY_EMAIL:
        missing.append("GOOGLE_ACCESS_AGENCY_EMAIL")
    return missing
