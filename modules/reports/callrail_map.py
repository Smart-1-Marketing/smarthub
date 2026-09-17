"""Where CallRail's calls endpoint is, and which response field is which.

CallRail (callrail.com) is call tracking: a client's tracking numbers ring
through it, and every inbound call is a record with the source that drove
it -- Google Ads, Google Organic, a billboard number, Direct. Nothing here
is delivery: no spend, no impressions, no clicks. A CallRail row in the
fact table is an OUTCOME, the way a Smart 1 Suite row is, and the client's
page draws it as its own tile, *Phone calls*, never as a bar beside the
media products and never folded into ``conversions`` (``docs/claude/77``).

**The shape below is a transcription, not a live answer.** The public
reference (apidocs.callrail.com, "API v3") is one of the hosts the Hub's
own outbound proxy refuses, so what is here is what that reference says as
the search index shows it: every request is made to
``https://api.callrail.com/v3/``, the key travels as
``Authorization: Token token="<key>"``, an agency key sees several
*accounts* (``GET /v3/a.json``) and each account holds one *company* per
client, and ``GET /v3/a/{account_id}/calls.json`` lists calls between
``start_date`` and ``end_date`` in pages of up to 250, with the
company, the source and the first-time flag among the optional
``fields``. Each name is marked PLACEHOLDER until ``/reports/callrail-check``
has shown it on a real row, exactly as ``groundtruth_map.py`` treats its
own -- a name that happened to match a field of the wrong meaning would
file the wrong count under the right name, which is the failure nothing
on screen would show.

**The origin is the one placeholder that is never used.** ``DEFAULT_BASE``
is the origin the reference's own examples call and it is printed on the
check page as the likeliest spelling; ``callrail.py`` calls nothing until
``CALLRAIL_API_BASE`` is set. The rule (``docs/claude/53``, restated in
``docs/claude/73``) is about who confirms the host, not how good the
guess is: the pull runs nightly with nobody watching and the key rides in
a header on every request.

So ``callrail.py`` is config-driven and this file is the whole of the
configuration -- the origin, the version path, the header the key is
sent under, the query parameter names, the ``fields`` the request asks
for, and the map from response field to what the fact row carries.
Nothing else in the module knows a CallRail field name. Each name is
overridable by environment variable so a correction can land without a
deploy (``CALLRAIL_CALLS_PATH``, ``CALLRAIL_AUTH_FORMAT``,
``CALLRAIL_FIELD_<name>`` ...), spelled the way the key is; this file is
where the settled answer is written down.

## What a call becomes

The fact table is keyed on (platform, account_id, campaign_id, date).
For CallRail the *account* is the **company** -- the CallRail object that
is one client -- and the *campaign* is the **source** the call is
attributed to, so a client's calls read *Google Ads: 12, Google Organic:
7, Direct: 3* on the staff page and sum to one tile on theirs. The counts
ride in ``extras`` under their own names: ``calls`` (inbound), ``answered``,
``missed``, ``voicemail``, ``first_time_calls``, ``good_leads`` (the
platform's own lead marking) and ``duration_seconds``. Outbound calls are
counted and left out: a call the client's own staff placed is not a lead.
"""
from __future__ import annotations

import os

# The API. CALLRAIL_API_BASE is the origin and has to be SET; this default
# is printed on the check page and never called (see above). The reference's
# own examples call https://api.callrail.com/v3/ (apidocs.callrail.com, as
# the search index shows it, read September 17, 2026).
DEFAULT_BASE = "https://api.callrail.com"             # a documented origin, never called unset
ACCOUNTS_PATH = "/v3/a.json"                          # PLACEHOLDER: the accounts the key can see
CALLS_PATH = "/v3/a/{account_id}/calls.json"          # PLACEHOLDER: {account_id} is filled in
METHOD = "GET"

# How the key is sent. PLACEHOLDER shape from the reference: a Token scheme
# with the key quoted inside it, on the Authorization header.
AUTH_HEADER = "Authorization"
AUTH_FORMAT = 'Token token="{key}"'                   # PLACEHOLDER; {key} is the key

# Query parameter names (inclusive dates, YYYY-MM-DD; pages count from 1).
DATE_PARAMS = {"start": "start_date", "end": "end_date"}   # PLACEHOLDER
PAGE_PARAMS = {"page": "page", "per_page": "per_page"}     # PLACEHOLDER
PER_PAGE = 250                                         # the documented ceiling per page
# The optional fields the request asks for beyond the defaults; the defaults
# already carry start_time, answered, duration, voicemail and direction.
REQUEST_FIELDS = "company_id,company_name,source,first_call,lead_status"   # PLACEHOLDER
FIELDS_PARAM = "fields"                                # PLACEHOLDER

# Where the rows sit in each response: a dotted path into the JSON.
ROWS_PATH = "calls"                                    # PLACEHOLDER
ACCOUNTS_ROWS_PATH = "accounts"                        # PLACEHOLDER
# The paging fields on the body: how many pages there are in all.
TOTAL_PAGES_PATH = "total_pages"                       # PLACEHOLDER

# The response field carrying each thing a fact row is built from.
# PLACEHOLDER names; each may be a dotted path. ``None`` means the platform
# does not report it and the count it feeds is left out of extras.
FIELDS = {
    # start_time is ISO 8601 in the company's own time zone, so its first
    # ten characters are the client's local day -- the day their report
    # should file the call under.
    "date": "start_time",
    "account_id": "company_id",
    "account_name": "company_name",
    # The tracking source the call is attributed to: the "campaign" here.
    "campaign_id": "source",
    "campaign_name": "source",
    "direction": "direction",
    "answered": "answered",
    "voicemail": "voicemail",
    "first_call": "first_call",
    "lead_status": "lead_status",
    "duration": "duration",
    # Never a conversion: a call is filed under its own name (see the
    # module docstring). Named here so the provider page's map shows the
    # decision rather than an absence.
    "conversions": None,
}

# The fields on each row of the accounts list. PLACEHOLDER.
ACCOUNT_FIELDS = {"id": "id", "name": "name"}

# Values the map reads as "this call came in" and "this call is a lead".
INBOUND_VALUES = ("inbound",)                          # PLACEHOLDER
GOOD_LEAD_VALUES = ("good_lead",)                      # PLACEHOLDER

# Names that may be overridden by environment variable, so a correction
# lands without a deploy. The value here is the variable's name.
_ENV = {
    "ACCOUNTS_PATH": "CALLRAIL_ACCOUNTS_PATH",
    "CALLS_PATH": "CALLRAIL_CALLS_PATH",
    "AUTH_HEADER": "CALLRAIL_AUTH_HEADER",
    "AUTH_FORMAT": "CALLRAIL_AUTH_FORMAT",
    "REQUEST_FIELDS": "CALLRAIL_REQUEST_FIELDS",
    "FIELDS_PARAM": "CALLRAIL_FIELDS_PARAM",
    "ROWS_PATH": "CALLRAIL_ROWS_PATH",
    "ACCOUNTS_ROWS_PATH": "CALLRAIL_ACCOUNTS_ROWS_PATH",
    "TOTAL_PAGES_PATH": "CALLRAIL_TOTAL_PAGES_PATH",
}


def _env(name: str, default: str) -> str:
    val = os.environ.get(_ENV[name])
    return val.strip() if val is not None and val.strip() else default


def config() -> dict:
    """The whole configuration, environment overrides applied. The key is
    NOT in it: ``callrail.cfg()`` reads that, and this dict is rendered onto
    the check page. ``base`` is empty until CALLRAIL_API_BASE is set -- the
    default is carried apart, as ``base_default``, so a screen can print
    the guess without anything treating it as an answer. ``account_id`` is
    the one account to read when CALLRAIL_ACCOUNT_ID pins it; empty means
    every account the key can see."""
    fields = dict(FIELDS)
    for k in list(fields):
        override = os.environ.get(f"CALLRAIL_FIELD_{k.upper()}")
        if override is not None:
            fields[k] = override.strip() or None
    params = dict(DATE_PARAMS)
    for k in list(params):
        override = os.environ.get(f"CALLRAIL_PARAM_{k.upper()}")
        if override is not None and override.strip():
            params[k] = override.strip()
    per_page = PER_PAGE
    try:
        per_page = max(1, min(PER_PAGE, int(os.environ.get("CALLRAIL_PER_PAGE") or PER_PAGE)))
    except (TypeError, ValueError):
        pass
    account_fields = dict(ACCOUNT_FIELDS)
    for k in list(account_fields):
        override = os.environ.get(f"CALLRAIL_ACCOUNT_FIELD_{k.upper()}")
        if override is not None and override.strip():
            account_fields[k] = override.strip()
    return {
        "base": (os.environ.get("CALLRAIL_API_BASE") or "").strip().rstrip("/"),
        "base_default": DEFAULT_BASE,
        "account_id": (os.environ.get("CALLRAIL_ACCOUNT_ID") or "").strip(),
        "accounts_path": _env("ACCOUNTS_PATH", ACCOUNTS_PATH),
        "calls_path": _env("CALLS_PATH", CALLS_PATH),
        "method": METHOD,
        "auth_header": _env("AUTH_HEADER", AUTH_HEADER),
        "auth_format": _env("AUTH_FORMAT", AUTH_FORMAT),
        "date_params": params,
        "page_params": dict(PAGE_PARAMS),
        "per_page": per_page,
        "request_fields": _env("REQUEST_FIELDS", REQUEST_FIELDS),
        "fields_param": _env("FIELDS_PARAM", FIELDS_PARAM),
        "rows_path": _env("ROWS_PATH", ROWS_PATH),
        "accounts_rows_path": _env("ACCOUNTS_ROWS_PATH", ACCOUNTS_ROWS_PATH),
        "total_pages_path": _env("TOTAL_PAGES_PATH", TOTAL_PAGES_PATH),
        "fields": fields,
        "account_fields": account_fields,
        "inbound_values": tuple(INBOUND_VALUES),
        "good_lead_values": tuple(GOOD_LEAD_VALUES),
        "placeholder": True,
    }


REQUIRED = ("date", "account_id", "campaign_id")


def required_fields(fields: dict | None = None) -> list[str]:
    fields = fields or config()["fields"]
    return [fields[k] for k in REQUIRED if fields.get(k)]
