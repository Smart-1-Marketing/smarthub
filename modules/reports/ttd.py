"""The native Trade Desk pull: daily campaign figures from the platform's own API.

The provider normalize reads a copy of these numbers a day late and restated
by somebody else; this reads them from The Trade Desk directly, for every
advertiser under the partner, and lands them through ``store.upsert_rows``
as ``platform="ttd"``, ``source="native"`` -- so native wins over the
provider copy for the same (account, campaign, day), the rule
``normalize.py`` reads from the watermark.

## Authentication

A **long-lived API token**, generated in the platform UI (Preferences ->
Developer Portal -> API tokens) and set as ``TTD_API_TOKEN``. It is sent as
the ``TTD-Auth`` request header on every call. There is no username/password
exchange here: ``POST /v3/authentication`` mints short tokens from a login
and would put a password into the environment for a job that needs a
header. The token is never logged, never carried on a result and never in
an error -- ``_redact()`` strips it from any provider message before the
message leaves this module, and ``test_reports_ttd.py`` reads every string
a pull produces to prove it.

``TTD_PARTNER_ID`` is the partner every advertiser hangs off.
``TTD_API_BASE`` defaults to ``https://api.thetradedesk.com/v3`` (the
sandbox is ``https://ext-api.sb.thetradedesk.com/v3``).

## The endpoints, and why these

Transcribed from the Platform API reference (partner.thetradedesk.com/v3/
portal/api/ref/...; the pages are rendered client-side, so the shapes below
are also what the Salesforce Data Streams and Adverity connectors describe
reading from "My Reports"). Every request body is a constant here and every
response is read defensively -- an unknown field is ignored and a missing
one is a refusal by name, never a guess.

* **Advertisers** -- ``POST /v3/advertiser/query/partner`` with
  ``{"PartnerId", "PageStartIndex", "PageSize"}``, paged on
  ``PageStartIndex`` until a page comes back short. Reads ``Result[]`` as
  ``{"AdvertiserId", "AdvertiserName"}``.

* **Figures** -- MyReports, which is the one reporting path a partner token
  is granted for. The REDS / aggregated-report stream is a per-advertiser
  S3 delivery that has to be provisioned by the account team, and there is
  no ad-hoc "stats by day" endpoint on the Platform API a token can call.
  So the pull owns ONE report schedule and reads its executions:

  * ``POST /v3/myreports/reportschedule/query`` (``{"PartnerId",
    "NameContains", "PageStartIndex", "PageSize"}``) finds the schedule
    named ``SCHEDULE_NAME``; ``POST /v3/myreports/reportschedule`` creates
    it once if it is absent -- daily, CSV, the trailing window
    ``TTD_REPORT_DATE_RANGE`` (default ``LastThirtyDays``, which covers the
    28 days the platform restates), template ``TTD_REPORT_TEMPLATE_ID``.
    The template is the partner's own MyReports template (the standard
    Performance report, by campaign and day) and it is not guessed at: a
    template of the wrong shape files nothing, and the pull says so.
  * ``POST /v3/myreports/reportexecution/query/partners``
    (``{"PartnerIds", "ReportScheduleIds", "ExecutionSpansStartDate",
    "ExecutionSpansEndDate", "PageStartIndex", "PageSize"}``) lists the
    schedule's runs; the newest whose ``ReportExecutionState`` is
    ``Complete`` carries ``ReportDeliveries[].DownloadURL``.
  * The download URL is a plain signed GET (no auth header) returning the
    CSV, which ``parsers/ttd_myreports.parse()`` turns into fact rows --
    the same parser the emailed copy of that file goes through.

## What a run answers

``pull()`` returns ``{"ok", "rows", "advertisers", "skipped", "error",
"executions"}`` and records the watermark (``store.record_sync(...,
source="native")``) -- a failure stamps its error so ``/reports/`` names it,
and a pull that read nothing writes no rows. ``status()`` is the line the
Reports index prints, the shape ``google_ads.connection_status()`` answers
in: configured or not, and what is missing by variable name.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

import requests

from . import store
from .parsers import ttd_myreports

log = logging.getLogger(__name__)

DEFAULT_BASE = "https://api.thetradedesk.com/v3"
TIMEOUT = 60
PAGE_SIZE = 100
SCHEDULE_NAME = "Smart 1 Hub - daily campaign performance"
RESTATE_DAYS = 28

ADVERTISER_QUERY = "/advertiser/query/partner"
SCHEDULE_QUERY = "/myreports/reportschedule/query"
SCHEDULE_CREATE = "/myreports/reportschedule"
EXECUTION_QUERY = "/myreports/reportexecution/query/partners"

# Render carries the token as TRADE_DESK_API; the docs said TTD_API_TOKEN. Both
# spellings are read, first non-empty wins, same rule hub/config.py applies to
# PEXELS_API / PEXELS_API_KEY.
TOKEN_ENV = ("TTD_API_TOKEN", "TRADE_DESK_API", "TRADE_DESK_API_KEY", "TRADE_DESK_API_TOKEN", "TTD_API")
NOT_CONFIGURED = "not configured: TTD_API_TOKEN (or TRADE_DESK_API) unset"


def _first_env(names) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def cfg() -> dict:
    return {
        "token": _first_env(TOKEN_ENV),
        "partner_id": (os.environ.get("TTD_PARTNER_ID") or "").strip(),
        "base": (os.environ.get("TTD_API_BASE") or DEFAULT_BASE).strip().rstrip("/"),
        "template_id": (os.environ.get("TTD_REPORT_TEMPLATE_ID") or "").strip(),
        "date_range": (os.environ.get("TTD_REPORT_DATE_RANGE") or "LastThirtyDays").strip(),
    }


def missing() -> list[str]:
    c = cfg()
    return [name for name, value in (("TTD_API_TOKEN", c["token"]),
                                     ("TTD_PARTNER_ID", c["partner_id"]))
            if not value]


def configured() -> bool:
    return not missing()


def _redact(text: str) -> str:
    """Never let the token out, however a provider message quotes it."""
    token = cfg()["token"]
    s = str(text or "")
    if token and token in s:
        s = s.replace(token, "[TTD-Auth redacted]")
    return s[:500]


class TTDError(Exception):
    """A refusal from the platform, already redacted."""


# ---------------------------------------------------------------------------
# HTTP -- one seam, so a test can stand in for the platform
# ---------------------------------------------------------------------------

def _http(method: str, url: str, *, headers: dict, body=None, timeout: int = TIMEOUT):
    """The one call that reaches the network. Replaced whole by the test."""
    return requests.request(method, url, headers=headers, json=body, timeout=timeout)


def request(method: str, path: str, body=None) -> dict:
    c = cfg()
    if not c["token"]:
        raise TTDError(NOT_CONFIGURED)
    headers = {"TTD-Auth": c["token"], "Content-Type": "application/json"}
    url = c["base"] + path
    try:
        resp = _http(method, url, headers=headers, body=body)
    except requests.RequestException as exc:
        raise TTDError(_redact(f"The Trade Desk could not be reached: {type(exc).__name__}"))
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("Message") or resp.text
        except Exception:                   # noqa: BLE001
            msg = resp.text
        raise TTDError(_redact(f"HTTP {resp.status_code} on {path}: {msg}"))
    try:
        return resp.json() if resp.content else {}
    except ValueError:
        raise TTDError(f"The Trade Desk answered {path} with something that is not JSON")


def _paged(path: str, body: dict) -> list[dict]:
    """Follow ``PageStartIndex`` until a page comes back short."""
    out, start = [], 0
    while True:
        page = request("POST", path, {**body, "PageStartIndex": start, "PageSize": PAGE_SIZE})
        rows = page.get("Result") or []
        out.extend(r for r in rows if isinstance(r, dict))
        if len(rows) < PAGE_SIZE:
            return out
        start += PAGE_SIZE
        if start > 100_000:               # a partner does not have a thousand pages
            return out


# ---------------------------------------------------------------------------
# Advertisers
# ---------------------------------------------------------------------------

def list_advertisers() -> list[dict]:
    """Every advertiser under the partner: ``[{"id", "name"}]``."""
    c = cfg()
    rows = _paged(ADVERTISER_QUERY, {"PartnerId": c["partner_id"]})
    return [{"id": str(r.get("AdvertiserId") or ""), "name": str(r.get("AdvertiserName") or "")}
            for r in rows if r.get("AdvertiserId")]


# ---------------------------------------------------------------------------
# The schedule and its executions
# ---------------------------------------------------------------------------

def find_schedule() -> dict | None:
    c = cfg()
    rows = _paged(SCHEDULE_QUERY, {"PartnerId": c["partner_id"], "NameContains": SCHEDULE_NAME})
    for r in rows:
        if str(r.get("ReportScheduleName") or "").strip() == SCHEDULE_NAME:
            return r
    return None


def schedule_body(advertiser_ids: list[str]) -> dict:
    """What the schedule is created with. A function so the test reads it."""
    c = cfg()
    return {
        "ReportScheduleName": SCHEDULE_NAME,
        "ReportTemplateId": int(c["template_id"]) if c["template_id"].isdigit() else c["template_id"],
        "ReportFileFormat": "CSV",
        "ReportFrequency": "Daily",
        "ReportDateRange": c["date_range"],
        "ReportDateFormat": "Sortable",
        "TimeZone": "UTC",
        "PartnerId": c["partner_id"],
        "AdvertiserFilters": list(advertiser_ids),
        "ReportStartDateInclusive": date.today().isoformat(),
    }


def ensure_schedule(advertiser_ids: list[str]) -> dict:
    """The schedule this pull reads, found or created once."""
    found = find_schedule()
    if found:
        return found
    c = cfg()
    if not c["template_id"]:
        raise TTDError("No report schedule named %r exists yet and TTD_REPORT_TEMPLATE_ID is "
                       "unset, so one cannot be created: set it to the MyReports template "
                       "(campaign by day) the pull should run." % SCHEDULE_NAME)
    created = request("POST", SCHEDULE_CREATE, schedule_body(advertiser_ids))
    if not created.get("ReportScheduleId"):
        raise TTDError("The Trade Desk accepted the schedule without a ReportScheduleId")
    return created


def list_executions(schedule_id, days: int = RESTATE_DAYS) -> list[dict]:
    c = cfg()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    until = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    return _paged(EXECUTION_QUERY, {
        "PartnerIds": [c["partner_id"]],
        "ReportScheduleIds": [schedule_id],
        "ExecutionSpansStartDate": since,
        "ExecutionSpansEndDate": until,
    })


def latest_complete(executions: list[dict]) -> dict | None:
    """The newest execution that finished with a downloadable file."""
    done = [e for e in executions
            if str(e.get("ReportExecutionState") or "").lower() in ("complete", "completed")
            and any(d.get("DownloadURL") for d in (e.get("ReportDeliveries") or []))]
    if not done:
        return None
    done.sort(key=lambda e: str(e.get("ReportEndDateExclusive") or e.get("ReportExecutionId") or ""))
    return done[-1]


def download(url: str) -> str:
    """The CSV behind a signed delivery URL. No auth header: the URL is the
    credential, and sending the token to a storage host is sending it
    somewhere it was never meant to go."""
    try:
        resp = _http("GET", url, headers={})
    except requests.RequestException as exc:
        raise TTDError(f"The report file could not be downloaded: {type(exc).__name__}")
    if resp.status_code >= 400:
        raise TTDError(f"The report file download answered HTTP {resp.status_code}")
    content = resp.content or b""
    return content.decode("utf-8-sig", errors="replace")


# ---------------------------------------------------------------------------
# The pull
# ---------------------------------------------------------------------------

def _state_path() -> str:
    from hub import jsonstore
    return os.path.join(jsonstore.data_dir("reports"), "ttd_status.json")


def _remember(state: dict) -> None:
    """What the index line prints between pulls: advertiser count and the
    last outcome. Rebuilt by the next pull, so durable=False."""
    try:
        from hub import jsonstore
        jsonstore.write_json(_state_path(), state, durable=False)
    except Exception:                       # noqa: BLE001 - a note is not the pull
        pass


def _remembered() -> dict:
    try:
        from hub import jsonstore
        return jsonstore.read_json(_state_path(), default={}) or {}
    except Exception:                       # noqa: BLE001
        return {}


def pull(days: int = RESTATE_DAYS) -> dict:
    """Read the schedule's newest complete file and land its rows."""
    out = {"ok": False, "rows": 0, "advertisers": 0, "skipped": 0,
           "executions": 0, "error": ""}
    if not configured():
        out["error"] = "not configured: " + ", ".join(missing()) + " unset"
        return out
    try:
        advertisers = list_advertisers()
        out["advertisers"] = len(advertisers)
        schedule = ensure_schedule([a["id"] for a in advertisers])
        sched_id = schedule.get("ReportScheduleId")
        executions = list_executions(sched_id, days)
        out["executions"] = len(executions)
        newest = latest_complete(executions)
        if newest is None:
            out["error"] = ("No completed run of the report schedule in the last "
                            f"{days} days yet; the first one lands after its next scheduled time")
            store.record_sync("ttd", rows=0, error=out["error"], source="native")
            _remember({**out, "at": store.iso(store.now())})
            return out
        url = next(d["DownloadURL"] for d in newest["ReportDeliveries"] if d.get("DownloadURL"))
        parsed = ttd_myreports.parse(download(url))
        if parsed["error"]:
            raise TTDError(parsed["error"])
        out["skipped"] = parsed["skipped"]
        out["rows"] = store.upsert_rows(parsed["rows"]) if parsed["rows"] else 0
        out["ok"] = True
        store.record_sync("ttd", rows=out["rows"], error="", source="native")
    except TTDError as exc:
        out["error"] = _redact(str(exc))
        store.record_sync("ttd", rows=0, error=out["error"], source="native")
    except Exception as exc:                # noqa: BLE001 - one platform, not the job
        log.exception("reports: Trade Desk pull failed")
        out["error"] = _redact(f"{type(exc).__name__}: {exc}")
        store.record_sync("ttd", rows=0, error=out["error"], source="native")
    _remember({**out, "at": store.iso(store.now())})
    return out


def status() -> dict:
    """The line /reports/ prints, mirroring google_ads.connection_status():
    ``configured``, ``missing`` (by variable name), the advertiser count and
    last pull from the last run, and the watermark. Nothing here carries the
    token."""
    c = cfg()
    miss = missing()
    remembered = _remembered()
    sync = store.sync_status().get("ttd") or {}
    native = sync if sync.get("source") == "native" else {}
    return {
        "configured": not miss,
        "connected": not miss,
        "missing": miss,
        "base": c["base"],
        "partner_id": c["partner_id"],
        "template_id": c["template_id"],
        "schedule_name": SCHEDULE_NAME,
        "advertisers": int(remembered.get("advertisers") or 0) if remembered else None,
        "last_pull": native.get("last_run_at") or remembered.get("at"),
        "last_rows": native.get("rows"),
        "last_error": native.get("error") or (remembered.get("error") if remembered else ""),
        "line": ("Trade Desk: " + NOT_CONFIGURED if "TTD_API_TOKEN" in miss else
                 "Trade Desk: not configured: " + ", ".join(miss) + " unset" if miss else
                 "Trade Desk: connected, "
                 + (f"{remembered.get('advertisers')} advertisers, " if remembered.get("advertisers") is not None else "")
                 + "last pull " + (native.get("last_run_at") or remembered.get("at") or "never")
                 + (f" -- {native.get('error')}" if native.get("error") else "")),
    }
