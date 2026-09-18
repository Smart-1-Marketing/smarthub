"""Where AudioGo's reporting endpoint is, and which response field is which.

**Everything below but the origin and the auth header is a PLACEHOLDER**, in
the same sense as the column names in ``provider_map.py``: AudioGo's
Reporting API is specified in a PDF that was requested and has not arrived,
and the public pages (``audiogo.com/how-to/audiogo-reporting-api``,
``/how-to/api-base-url``, ``/how-to/api-access``, ``/integrations``,
``/faqs/api-reporting``, ``/how-to/dimensions-metrics-api``) say that the
spec exists, that the key goes in an ``x-api-key`` header, that the API is
AdsWizz's Domain API under ``https://api.adswizz.com/domain``, that separate
Dimensions and Metrics endpoints list what a report may ask for (metric
names are camelCase -- ``demandAudioImp`` is impressions; dimensions such
as ``geoCity`` and ``playerName``), that a synchronous report takes up to
three dimensions, and nothing about the report path below the origin.
``/reports/provider-check/audiogo`` carries all of it, with the Render
list. So ``audiogo.py`` is
config-driven and this file is the whole of the configuration -- the
endpoint path, the method, the header the key is sent under, the query
parameter names for the date range, and the map from response field to
fact-table column. Nothing else in the module knows an AudioGo field name.

Todd corrects it from ``/reports/audiogo-check``, which calls the configured
endpoint for yesterday and prints the raw JSON keys that came back (the key
itself redacted, nothing else), so the real names can be pasted in here.
Until the map resolves against a real response, ``pull()`` lands nothing
and ``/reports/`` says so: a guess that happened to match a field of the
wrong meaning would file the wrong number under the right name, which is
the failure nothing on screen would show.

Each name is overridable by environment variable so a correction can land
without a deploy (``AUDIOGO_REPORT_PATH``, ``AUDIOGO_METHOD``,
``AUDIOGO_AUTH_HEADER``, ...); this file is where the settled answer is
written down.

## The URL is whatever AUDIOGO_API_BASE says, and nothing is bolted onto it

**``REPORT_PATH`` defaults to nothing on purpose.** It used to default to a
PLACEHOLDER path, ``/v1/reports/campaigns/daily``, which was appended to
whatever origin was configured. On September 18, 2026 the check page called

    GET https://api.adswizz.com/domain/v8/reports/query/v1/reports/campaigns/daily

and AudioGo answered 404 -- because ``AUDIOGO_API_BASE`` had been set to the
whole endpoint, correctly, and an invented path was then glued to the end of
it. The environment could not undo that: ``AUDIOGO_REPORT_PATH=""`` read as
*unset* and handed back the placeholder, so the one override that exists to
land a correction without a deploy could not express the correction needed.

Now the base is called exactly as it is set, and a path is appended only
when somebody asks for one. Set ``AUDIOGO_API_BASE`` to the full report
endpoint (the simple case), or set it to the origin and put the rest in
``AUDIOGO_REPORT_PATH``. ``AUDIOGO_REPORT_PATH`` can be cleared back to
nothing with ``-``, ``/`` or ``none`` as well as an empty value, because a
blank environment variable does not survive every panel.

## A report here may be a query, not a date range on a URL

AudioGo describes building a report as *fetch the available dimensions and
metrics, apply filters, then issue a query combining filters, splitters and
metrics* (``audiogo.com/how-to/api-based-reporting``), which is a request
body rather than ``?start_date=&end_date=``. ``METHOD`` is therefore a
PLACEHOLDER too, and overridable: set ``AUDIOGO_METHOD=POST`` and the date
parameters, the extra parameters and ``AUDIOGO_BODY`` (a JSON object) are
sent as the JSON body instead of the query string. Nothing is guessed --
the default is still the GET that has been tried -- but the shape the spec
describes is now reachable from the Render panel.

All AdsWizz time values are UTC regardless of the agency's own time zone
(``audiogo.com/how-to/api-base-url``), so the dates sent here are UTC days.

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

import json
import os

# The API. AUDIOGO_API_BASE is what gets called; REPORT_PATH is appended to
# it only if somebody sets one. NOT a placeholder: AudioGo's own "What is
# the base URL for accessing the AudioGo API?" page names
# https://api.adswizz.com/domain -- AudioGo's reporting runs on AdsWizz's
# Domain API -- read September 18, 2026 from the search index, the host
# itself being one the Hub's environment cannot reach. The report path
# below that origin is in the spec PDF and on no public page, which is why
# AUDIOGO_API_BASE still has to be set by a person before anything is
# called (docs/claude/53: the rule is about who confirms the endpoint, not
# how good the guess is).
DEFAULT_BASE = "https://api.adswizz.com/domain"
REPORT_PATH = ""                                  # nothing is appended unless asked
METHOD = "GET"                                    # PLACEHOLDER: the spec may want a POST query
BODY: dict = {}                                   # PLACEHOLDER: the query a POST would carry

# Values of AUDIOGO_REPORT_PATH that mean "append nothing", because a blank
# environment variable does not survive every settings panel.
_NO_PATH = {"", "-", "/", "none", "(none)"}

# How the key is sent. NOT a placeholder: AudioGo's own FAQ
# (audiogo.com/faqs/api-reporting) says "include your API key in the header
# of your requests using the following format: x-api-key: your_api_key_here",
# read September 17, 2026 from the search index -- the page itself sits
# behind a host the Hub's environment cannot reach. A bare key, no prefix.
AUTH_HEADER = "x-api-key"
AUTH_PREFIX = ""                                  # a bare key; "Bearer " would be wrong here

# Query parameter names for the date range (inclusive UTC dates, YYYY-MM-DD).
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

# Methods that carry the report as a JSON body rather than a query string.
BODY_METHODS = ("POST", "PUT", "PATCH")

# Names that may be overridden by environment variable, so a correction
# lands without a deploy. The value here is the variable's suffix.
_ENV = {
    "REPORT_PATH": "AUDIOGO_REPORT_PATH",
    "METHOD": "AUDIOGO_METHOD",
    "AUTH_HEADER": "AUDIOGO_AUTH_HEADER",
    "AUTH_PREFIX": "AUDIOGO_AUTH_PREFIX",
    "ROWS_PATH": "AUDIOGO_ROWS_PATH",
}


def _env(name: str, default: str) -> str:
    val = os.environ.get(_ENV[name])
    return val.strip() if val is not None and val.strip() else default


def _path() -> str:
    """The path appended to the base: nothing unless somebody asks for one.

    Unlike every other override, an *empty* AUDIOGO_REPORT_PATH is an answer
    rather than a silence -- it is how "the base is the whole endpoint" is
    said -- so this does not fall back to the module default on a blank
    value, and reads ``-``, ``/`` and ``none`` as the same answer.
    """
    raw = os.environ.get(_ENV["REPORT_PATH"])
    path = (REPORT_PATH if raw is None else raw).strip()
    if path.lower() in _NO_PATH:
        return ""
    return path if path.startswith("/") else "/" + path


def _body() -> dict:
    """AUDIOGO_BODY, a JSON object, merged under the date and extra params
    by ``audiogo.request_shape()``. A value that is not a JSON object is
    ignored rather than raising -- the check page prints the result, so a
    typo shows there rather than 500ing the page it is corrected from."""
    raw = (os.environ.get("AUDIOGO_BODY") or "").strip()
    if not raw:
        return dict(BODY)
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def url(base: str, path: str) -> str:
    """The endpoint: the base as configured, plus the path if there is one.

    Nothing is deduplicated or repaired here. The base is what a person set
    and it is called as set; if it is wrong, the check page prints it and
    the refusal names it, which is how it gets corrected.
    """
    return base.rstrip("/") + path


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
    base = (os.environ.get("AUDIOGO_API_BASE") or DEFAULT_BASE).strip().rstrip("/")
    path = _path()
    method = _env("METHOD", METHOD).upper()
    return {
        "base": base,
        "path": path,
        "url": url(base, path),
        "method": method,
        "body": _body(),
        "sends_body": method in BODY_METHODS,
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
