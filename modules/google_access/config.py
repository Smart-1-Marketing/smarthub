"""
Configuration for the Google Access module.

Design rule that drives everything here: we never hold a client's Google
credentials. The client's OAuth token exists only inside a single request,
is used to add our agency email to their property, and is then revoked.
`access_type=online` is deliberate -- it means Google never issues us a
refresh token in the first place.
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
GOOGLE_CLIENT_ID = _env("GOOGLE_ACCESS_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _env("GOOGLE_ACCESS_CLIENT_SECRET")

# Public base URL of the Hub, used to build the OAuth redirect URI.
# Must exactly match an Authorized redirect URI in the Google Cloud console.
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL").rstrip("/")

# --- Who we add ------------------------------------------------------------
# The agency Google account that gets added to each client property.
AGENCY_EMAIL = _env("GOOGLE_ACCESS_AGENCY_EMAIL")
AGENCY_NAME = _env("GOOGLE_ACCESS_AGENCY_NAME", "Smart 1 Marketing")
AGENCY_SUPPORT_EMAIL = _env("GOOGLE_ACCESS_SUPPORT_EMAIL")
AGENCY_SUPPORT_PHONE = _env("GOOGLE_ACCESS_SUPPORT_PHONE")

# --- Google Ads (agency-side credentials, not the client's) ---------------
# Ads works the other way round: we send an invitation FROM our manager
# account, the client accepts it in their own Ads UI. That needs our own
# long-lived credentials plus an approved developer token.
ADS_DEVELOPER_TOKEN = _env("GOOGLE_ADS_DEVELOPER_TOKEN")
ADS_MANAGER_ID = _env("GOOGLE_ADS_MANAGER_ID").replace("-", "")
ADS_REFRESH_TOKEN = _env("GOOGLE_ADS_REFRESH_TOKEN")
ADS_CLIENT_ID = _env("GOOGLE_ADS_CLIENT_ID") or GOOGLE_CLIENT_ID
ADS_CLIENT_SECRET = _env("GOOGLE_ADS_CLIENT_SECRET") or GOOGLE_CLIENT_SECRET
ADS_API_VERSION = _env("GOOGLE_ADS_API_VERSION", "v21")

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
# OAuth callback. `assisted` services need a human step somewhere -- we start
# them and track them, but cannot finish them alone. Being honest about which
# is which on the client-facing page is the whole trust proposition.

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
    "ads": {
        "label": "Google Ads",
        "detail": "Manage campaigns and budgets, and report on what each lead cost.",
        "note": "One extra step: you accept our invitation inside Google Ads afterwards.",
        "role_text": "Manager account link",
        "role_code": "manager_link",
        "mode": "assisted",
        "scopes": [
            "https://www.googleapis.com/auth/adwords",
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

SERVICE_ORDER = ["ga4", "gtm", "ads", "gbp", "search_console"]

AUTOMATED_SERVICES = [k for k in SERVICE_ORDER if SERVICES[k]["mode"] == "automated"]


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
    return f"{PUBLIC_BASE_URL}/connect/callback"


def configured():
    """Hard requirements for the client-facing flow to work at all."""
    missing = []
    if not GOOGLE_CLIENT_ID:
        missing.append("GOOGLE_ACCESS_CLIENT_ID")
    if not GOOGLE_CLIENT_SECRET:
        missing.append("GOOGLE_ACCESS_CLIENT_SECRET")
    if not PUBLIC_BASE_URL:
        missing.append("PUBLIC_BASE_URL")
    if not AGENCY_EMAIL:
        missing.append("GOOGLE_ACCESS_AGENCY_EMAIL")
    return missing


def ads_configured():
    missing = []
    if not ADS_DEVELOPER_TOKEN:
        missing.append("GOOGLE_ADS_DEVELOPER_TOKEN")
    if not ADS_MANAGER_ID:
        missing.append("GOOGLE_ADS_MANAGER_ID")
    if not ADS_REFRESH_TOKEN:
        missing.append("GOOGLE_ADS_REFRESH_TOKEN")
    return missing
