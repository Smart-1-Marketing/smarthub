"""Where GroundTruth's reporting endpoint is, and which response field is which.

**Everything below is a PLACEHOLDER**, in the sense ``audiogo_map.py`` and
the column names in ``provider_map.py`` are: the key arrived before the
document could be read. GroundTruth publishes its Public API at
api-docs.groundtruth.com -- campaign, ad group and creative *timeseries*
metric endpoints, by day, by day of week and by time of day, which is as
much as the search index shows of it -- and that host, together with
reporting.groundtruth.com, docs.groundtruth.com and the help center, is
refused by the Hub's own outbound proxy. Nothing here has read a page of
it. The shape below is a reporting API's ordinary shape wearing
GroundTruth's own vocabulary, and it is wrong until a person says
otherwise.

So ``groundtruth.py`` is config-driven and this file is the whole of the
configuration -- the API origin, the endpoint path, the header the key is
sent under, the query parameter names for the date range, and the map from
response field to fact-table column. Nothing else in the module knows a
GroundTruth field name.

**The origin is the one placeholder that is never used.** ``DEFAULT_BASE``
is printed on the check page as the likeliest spelling, and
``groundtruth.py`` calls nothing until ``GROUND_TRUTH_API_BASE`` is set.
The pull runs nightly with nobody watching, and a request to a host nobody
has confirmed carries the key in its header: a guessed field name costs a
refusal by name on ``/reports/``, a guessed host hands the key to whoever
answers there.

Todd corrects the rest from ``/reports/groundtruth-check``, which calls the
configured endpoint for yesterday and prints the raw JSON keys that came
back (the key itself redacted, nothing else), so the real names can be
pasted in here. Until the map resolves against a real response, ``pull()``
lands nothing and ``/reports/`` says which field is missing: a guess that
happened to match a field of the wrong meaning would file the wrong number
under the right name, which is the failure nothing on screen would show.

Each name is overridable by environment variable so a correction can land
without a deploy (``GROUND_TRUTH_REPORT_PATH``, ``GROUND_TRUTH_AUTH_HEADER``,
...), spelled the way the key is; this file is where the settled answer is
written down.

## GroundTruth's own vocabulary

A geofencing buy is bought for **visits** -- the platform's own count of
devices that saw the ad and were later observed at the client's location
-- and GroundTruth reports them beside a visit rate and a cost per visit.
The fact table has no visits column, so they ride in ``extras_json`` under
their own name and are **never folded into conversions**: a visit is an
observation the platform makes about a device and a conversion is an
action a person takes, and one figure holding both is a number nobody can
explain to the client whose page it is on. The client's page draws them as
their own tile, *Store visits*, gated on a row that actually carries one.
"""
from __future__ import annotations

import os

# The API. GROUND_TRUTH_API_BASE is the origin and has to be SET; this
# default is printed on the check page and never called (see above).
# The origin the Public API's own examples call (api-docs.groundtruth.com,
# read September 17, 2026 from the search index: the request examples are
# curl against https://api-public.groundtruth.com with Content-Type:
# application/json). Printed on the check page as the likeliest spelling
# and STILL never called until GROUND_TRUTH_API_BASE is set to it: the rule
# above is about who confirms the host, not about how good the guess is.
DEFAULT_BASE = "https://api-public.groundtruth.com"   # a documented origin, never called unset
REPORT_PATH = "/v1/reports/campaigns/timeseries"      # PLACEHOLDER
METHOD = "GET"                                        # PLACEHOLDER (GET with query params)

# How the key is sent. PLACEHOLDER: a bearer token is the commonest shape
# for a reporting API issued per organization; "X-API-Key" is the other.
AUTH_HEADER = "Authorization"                         # PLACEHOLDER
AUTH_PREFIX = "Bearer "                               # PLACEHOLDER ("" for a bare key)

# Query parameter names for the date range (inclusive dates, YYYY-MM-DD).
DATE_PARAMS = {"start": "start_date", "end": "end_date"}   # PLACEHOLDER
EXTRA_PARAMS = {"interval": "day"}                    # PLACEHOLDER: whatever asks for daily rows

# Where the rows sit in the response: a dotted path into the JSON, or ""
# for "the body is the list". PLACEHOLDER.
ROWS_PATH = "data"

# The response field carrying each fact-table column. PLACEHOLDER names;
# each may be a dotted path (``campaign.id``). ``None`` means the platform
# does not report it.
FIELDS = {
    "date": "date",
    "account_id": "organization_id",
    "account_name": "organization_name",
    "campaign_id": "campaign_id",
    "campaign_name": "campaign_name",
    "spend": "spend",
    "impressions": "impressions",
    "clicks": "clicks",
    # GroundTruth's secondary actions (calls, directions) are the nearest
    # thing to a conversion and are NOT visits. None until the document
    # says which field: a guess here files somebody's store visits as
    # conversions on their own page.
    "conversions": None,
    # The figure a geofencing buy is bought for. Lands in extras under its
    # own name, with the rate beside it where the platform reports one.
    "visits": "visits",
    "visit_rate": "visit_rate",
}

# What raw spend is divided by on the way in (GroundTruth bills dollars).
SPEND_DIVISOR = 1                                     # PLACEHOLDER

# Names that may be overridden by environment variable, so a correction
# lands without a deploy. The value here is the variable's name.
_ENV = {
    "REPORT_PATH": "GROUND_TRUTH_REPORT_PATH",
    "AUTH_HEADER": "GROUND_TRUTH_AUTH_HEADER",
    "AUTH_PREFIX": "GROUND_TRUTH_AUTH_PREFIX",
    "ROWS_PATH": "GROUND_TRUTH_ROWS_PATH",
}


def _env(name: str, default: str) -> str:
    val = os.environ.get(_ENV[name])
    return val.strip() if val is not None and val.strip() else default


def config() -> dict:
    """The whole configuration, environment overrides applied. The key is
    NOT in it: ``groundtruth.cfg()`` reads that, and this dict is rendered
    onto the check page. ``base`` is empty until GROUND_TRUTH_API_BASE is
    set -- the default is carried apart, as ``base_default``, so a screen
    can print the guess without anything treating it as an answer."""
    fields = dict(FIELDS)
    for k in list(fields):
        override = os.environ.get(f"GROUND_TRUTH_FIELD_{k.upper()}")
        if override is not None:
            fields[k] = override.strip() or None
    params = dict(DATE_PARAMS)
    for k in list(params):
        override = os.environ.get(f"GROUND_TRUTH_PARAM_{k.upper()}")
        if override is not None and override.strip():
            params[k] = override.strip()
    return {
        "base": (os.environ.get("GROUND_TRUTH_API_BASE") or "").strip().rstrip("/"),
        "base_default": DEFAULT_BASE,
        "path": _env("REPORT_PATH", REPORT_PATH),
        "method": METHOD,
        "auth_header": _env("AUTH_HEADER", AUTH_HEADER),
        "auth_prefix": os.environ.get("GROUND_TRUTH_AUTH_PREFIX", AUTH_PREFIX),
        "date_params": params,
        "extra_params": dict(EXTRA_PARAMS),
        "rows_path": _env("ROWS_PATH", ROWS_PATH),
        "fields": fields,
        "spend_divisor": SPEND_DIVISOR,
        "placeholder": True,
    }


REQUIRED = ("date", "account_id", "campaign_id")


def required_fields(fields: dict | None = None) -> list[str]:
    fields = fields or config()["fields"]
    return [fields[k] for k in REQUIRED if fields.get(k)]
