"""Where AudioGo's reporting endpoint is, and which response field is which.

**Everything below but the auth header is a PLACEHOLDER**, in the same
sense as the column names in ``provider_map.py``: AudioGo's Reporting API is
specified in a PDF that was requested and has not arrived, and the public
pages (``audiogo.com/how-to/audiogo-reporting-api``, ``/integrations``,
``/faqs/api-reporting``, ``/how-to/dimensions-metrics-api``) say that the
spec exists, that the key goes in an ``x-api-key`` header, that separate
Dimensions and Metrics endpoints list what a report may ask for (metric
names are camelCase -- ``demandAudioImp`` is impressions; dimensions such
as ``geoCity`` and ``playerName``), that a synchronous report takes up to
three dimensions, and nothing about the origin or the report path.
``/reports/provider-check/audiogo`` carries all of it, with the Render
list. So ``audiogo.py`` is
config-driven and this file is the whole of the configuration -- the
endpoint path, the header the key is sent under, the query parameter names
for the date range, and the map from response field to fact-table column.
Nothing else in the module knows an AudioGo field name.

Todd corrects it from ``/reports/audiogo-check``, which calls the configured
endpoint for yesterday and prints the raw JSON keys that came back (the key
itself redacted, nothing else), so the real names can be pasted in here.
Until the map resolves against a real response, ``pull()`` lands nothing
and ``/reports/`` says so: a guess that happened to match a field of the
wrong meaning would file the wrong number under the right name, which is
the failure nothing on screen would show.

Each name is overridable by environment variable so a correction can land
without a deploy (``AUDIOGO_REPORT_PATH``, ``AUDIOGO_AUTH_HEADER``, ...);
this file is where the settled answer is written down.

## AudioGo's own vocabulary

AudioGo reports **listens** and a **listen-through rate (LTR)** where a
video platform reports completes. The fact table's one completion column is
``completes``, so a listens/completed-listens figure lands there (the
client dashboard's tile reads "Listens" for an audio platform) and every
audio-specific figure also rides in ``extras_json`` under its own name --
``listens``, ``ltr``, ``completed_listens`` -- so nothing the platform said
is lost to the one column.
"""
from __future__ import annotations

import os

# The API. AUDIOGO_API_BASE is the origin; the report path is appended.
DEFAULT_BASE = "https://api.audiogo.com"          # PLACEHOLDER
REPORT_PATH = "/v1/reports/campaigns/daily"       # PLACEHOLDER
METHOD = "GET"                                    # PLACEHOLDER (GET with query params)

# How the key is sent. NOT a placeholder: AudioGo's own FAQ
# (audiogo.com/faqs/api-reporting) says "include your API key in the header
# of your requests using the following format: x-api-key: your_api_key_here",
# read September 17, 2026 from the search index -- the page itself sits
# behind a host the Hub's environment cannot reach. A bare key, no prefix.
AUTH_HEADER = "x-api-key"
AUTH_PREFIX = ""                                  # a bare key; "Bearer " would be wrong here

# Query parameter names for the date range (inclusive dates, YYYY-MM-DD).
DATE_PARAMS = {"start": "start_date", "end": "end_date"}   # PLACEHOLDER
EXTRA_PARAMS = {"granularity": "day"}             # PLACEHOLDER: whatever asks for daily rows

# Where the rows sit in the response: a dotted path into the JSON, or ""
# for "the body is the list". PLACEHOLDER.
ROWS_PATH = "data"

# The response field carrying each fact-table column. PLACEHOLDER names;
# each may be a dotted path (``campaign.id``). ``None`` means the platform
# does not report it.
FIELDS = {
    "date": "date",
    "account_id": "advertiser_id",
    "account_name": "advertiser_name",
    "campaign_id": "campaign_id",
    "campaign_name": "campaign_name",
    "spend": "spend",
    "impressions": "impressions",
    "clicks": "clicks",
    "conversions": None,
    # AudioGo's completion figure. Listens land in `completes`; whichever
    # of these the response carries is also kept in extras under its name.
    "listens": "listens",
    "completed_listens": "completed_listens",
    "ltr": "ltr",
}

# What raw spend is divided by on the way in (AudioGo bills dollars).
SPEND_DIVISOR = 1                                 # PLACEHOLDER

# Names that may be overridden by environment variable, so a correction
# lands without a deploy. The value here is the variable's suffix.
_ENV = {
    "REPORT_PATH": "AUDIOGO_REPORT_PATH",
    "AUTH_HEADER": "AUDIOGO_AUTH_HEADER",
    "AUTH_PREFIX": "AUDIOGO_AUTH_PREFIX",
    "ROWS_PATH": "AUDIOGO_ROWS_PATH",
}


def _env(name: str, default: str) -> str:
    val = os.environ.get(_ENV[name])
    return val.strip() if val is not None and val.strip() else default


def config() -> dict:
    """The whole configuration, environment overrides applied. The key is
    NOT in it: ``audiogo.cfg()`` reads that, and this dict is rendered onto
    the check page."""
    fields = dict(FIELDS)
    for k in list(fields):
        override = os.environ.get(f"AUDIOGO_FIELD_{k.upper()}")
        if override is not None:
            fields[k] = override.strip() or None
    params = dict(DATE_PARAMS)
    for k in list(params):
        override = os.environ.get(f"AUDIOGO_PARAM_{k.upper()}")
        if override is not None and override.strip():
            params[k] = override.strip()
    return {
        "base": (os.environ.get("AUDIOGO_API_BASE") or DEFAULT_BASE).strip().rstrip("/"),
        "path": _env("REPORT_PATH", REPORT_PATH),
        "method": METHOD,
        "auth_header": _env("AUTH_HEADER", AUTH_HEADER),
        "auth_prefix": os.environ.get("AUDIOGO_AUTH_PREFIX", AUTH_PREFIX),
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
