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

``TTD_PARTNER_ID`` is the partner every advertiser hangs off, and it is
optional: ``POST /v3/partner/query`` lists the partners the token's user can
see, and when there is exactly one the pull uses it and says so. The first
production run with a partner id typed by hand answered ``HTTP 403 ... not
authorized to access Partner '1739'`` -- a seat number off a settings page,
not the API's partner id -- so a refused or absent partner id is answered
with the partners the token can actually reach, by name and id. Several
partners and no variable is a refusal naming them: the pull will not pick.
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
    28 days the platform restates). The schedule is **partner-wide**: it
    carries no ``AdvertiserFilters``, so it covers every advertiser under
    the partner, including the ones added after it was created. Clients are
    added all the time, and a schedule pinned to the advertiser list of the
    day it was created would silently miss every one of them. A schedule
    found with a filter on it (one the earlier version of this pull
    created) is refused by name rather than read: delete it in the platform
    and the next pull recreates it partner-wide.
  * **The template.** ``POST /v3/myreports/reporttemplate/query/partner``
    (``{"PartnerId", "NameContains", "PageStartIndex", "PageSize"}``) lists
    the templates the partner can run, read as ``Result[]`` of
    ``{"ReportTemplateId", "ReportTemplateName"}``. ``pick_template()``
    takes the standard Performance report by name (``TEMPLATE_PREFERENCE``,
    most specific first: "Performance Report", then a campaign performance
    template, then any performance template that is not an hourly, creative,
    site, geo or device breakout). Extra dimensions are harmless -- the
    parser sums rows for the same campaign-day -- but a template with no
    campaign or day column files nothing, which is why the pick is by name
    and never "the first one". ``TTD_REPORT_TEMPLATE_ID``, when set, wins
    over the pick; when nothing is picked the pull refuses and lists the
    template names it saw, so the variable can be set from that list. The
    pick is only made once: after that the schedule carries the template.
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
PARTNER_QUERY = "/partner/query"
TEMPLATE_QUERY = "/myreports/reporttemplate/query/partner"
SCHEDULE_QUERY = "/myreports/reportschedule/query"
SCHEDULE_CREATE = "/myreports/reportschedule"
EXECUTION_QUERY = "/myreports/reportexecution/query/partners"

# Render carries the token as TRADE_DESK_API; the docs said TTD_API_TOKEN. Both
# spellings are read, first non-empty wins, same rule hub/config.py applies to
# PEXELS_API / PEXELS_API_KEY.
TOKEN_ENV = ("TTD_API_TOKEN", "TRADE_DESK_API", "TRADE_DESK_API_KEY", "TRADE_DESK_API_TOKEN", "TTD_API")
NOT_CONFIGURED = "not configured: TTD_API_TOKEN (or TRADE_DESK_API) unset"

# How a template is chosen when TTD_REPORT_TEMPLATE_ID is unset: the first
# rule with a match wins, and within a rule the shortest name (the plainest
# template) wins. A name matching a word in TEMPLATE_AVOID is a breakout the
# daily campaign pull does not want, whatever else it is called.
TEMPLATE_PREFERENCE = (
    ("performance report",),
    ("campaign", "performance"),
    ("performance",),
)
TEMPLATE_AVOID = ("hourly", "creative", "site", "geo", "device", "frequency",
                  "data element", "audience", "conversion", "attribution",
                  "publisher", "deal", "inventory")


def _first_env(names) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


# The partner the token sees, when TTD_PARTNER_ID is unset and there is
# exactly one: found by resolve_partner() during a pull and remembered in
# the module's note, so cfg() answers without a call.
_partner: dict = {}


def _remembered_partner() -> dict:
    global _partner
    if not _partner:
        _partner = {k: v for k, v in (_remembered().get("partner") or {}).items() if k in ("id", "name")}
    return _partner


def cfg() -> dict:
    env_partner = (os.environ.get("TTD_PARTNER_ID") or "").strip()
    return {
        "token": _first_env(TOKEN_ENV),
        "partner_id": env_partner or str(_remembered_partner().get("id") or ""),
        "partner_source": "env" if env_partner else ("discovered" if _remembered_partner().get("id") else ""),
        "base": (os.environ.get("TTD_API_BASE") or DEFAULT_BASE).strip().rstrip("/"),
        "template_id": (os.environ.get("TTD_REPORT_TEMPLATE_ID") or "").strip(),
        "date_range": (os.environ.get("TTD_REPORT_DATE_RANGE") or "LastThirtyDays").strip(),
    }


def missing() -> list[str]:
    """The variables without which nothing can be asked: the token. The
    partner id is asked for from the platform when it is not set."""
    c = cfg()
    return [name for name, value in (("TTD_API_TOKEN", c["token"]),) if not value]


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
# The partner
# ---------------------------------------------------------------------------

def list_partners() -> list[dict]:
    """The partners the token's user can see: ``[{"id", "name"}]``. Rows
    with no ``PartnerId`` are refused naming the keys, like the templates."""
    rows = _paged(PARTNER_QUERY, {})
    out = [{"id": str(r.get("PartnerId") or "").strip(),
            "name": str(r.get("PartnerName") or r.get("Name") or "").strip()} for r in rows]
    out = [p for p in out if p["id"]]
    if rows and not out:
        keys = sorted({k for r in rows for k in r})[:12]
        raise TTDError("The partner list carried no PartnerId; the rows have " + ", ".join(keys))
    return out


def _partners_text(partners: list[dict]) -> str:
    return ", ".join(f"{p['name'] or 'unnamed'} ({p['id']})" for p in partners) or "none"


def resolve_partner() -> dict:
    """``{"id", "name", "source"}``: the env variable, else the one partner
    the token sees (remembered for cfg()). Raises, naming what the token
    sees, when there are several or none."""
    global _partner
    env_partner = (os.environ.get("TTD_PARTNER_ID") or "").strip()
    if env_partner:
        return {"id": env_partner, "name": "", "source": "env"}
    partners = list_partners()
    if len(partners) == 1:
        _partner = {"id": partners[0]["id"], "name": partners[0]["name"]}
        return {**_partner, "source": "discovered"}
    if not partners:
        raise TTDError("The token's user can see no partner at all, so nothing can be read: "
                       "the API token has to belong to a user with partner-level access.")
    raise TTDError("The token's user can see several partners and TTD_PARTNER_ID is unset; "
                   "set it to one of: " + _partners_text(partners))


def _refused_partner_help(exc: TTDError) -> str:
    """After a 403 on the partner: what the token can see, or why not."""
    text = str(exc)
    if "403" not in text and "not authorized" not in text.lower():
        return ""
    try:
        partners = list_partners()
    except TTDError as inner:
        return f" (the partner list could not be read either: {inner})"
    if not partners:
        return (" The token's user can see no partner: the API token has to belong to a "
                "user with partner-level access.")
    return (" The token's user can see: " + _partners_text(partners)
            + ". Set TTD_PARTNER_ID to one of those ids, or unset it when there is one.")


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
# The template
# ---------------------------------------------------------------------------

def _template_name(row: dict) -> str:
    return str(row.get("ReportTemplateName") or row.get("Name") or "").strip()


def list_templates() -> list[dict]:
    """Every MyReports template the partner can run: ``[{"id", "name"}]``.
    A response with rows but no ``ReportTemplateId`` on any of them is
    refused naming the keys it did carry, so a changed shape reads as a
    changed shape and never as "the partner has no templates"."""
    c = cfg()
    rows = _paged(TEMPLATE_QUERY, {"PartnerId": c["partner_id"], "NameContains": ""})
    out = [{"id": str(r.get("ReportTemplateId") or "").strip(), "name": _template_name(r)}
           for r in rows]
    out = [t for t in out if t["id"]]
    if rows and not out:
        keys = sorted({k for r in rows for k in r})[:12]
        raise TTDError("The template list carried no ReportTemplateId; the rows have "
                       + ", ".join(keys))
    return out


def _norm_name(name: str) -> str:
    return " ".join(str(name or "").lower().replace("_", " ").replace("-", " ").split())


def pick_template(templates: list[dict]) -> dict | None:
    """The template the daily campaign schedule should run, by name and
    never by position: the first TEMPLATE_PREFERENCE rule with a match, the
    plainest name within it, skipping the breakouts in TEMPLATE_AVOID."""
    usable = [t for t in templates
              if t.get("id") and not any(w in _norm_name(t.get("name")) for w in TEMPLATE_AVOID)]
    for words in TEMPLATE_PREFERENCE:
        hits = [t for t in usable if all(w in _norm_name(t["name"]) for w in words)]
        if hits:
            hits.sort(key=lambda t: (len(_norm_name(t["name"])), _norm_name(t["name"])))
            return hits[0]
    return None


def resolve_template() -> dict:
    """``{"id", "name", "source"}``: the env override (``source="env"``) or
    the automatic pick (``source="auto"``). Raises, naming the templates it
    saw, when nothing fits -- the variable is set from that list."""
    c = cfg()
    if c["template_id"]:
        return {"id": c["template_id"], "name": "", "source": "env"}
    templates = list_templates()
    picked = pick_template(templates)
    if picked:
        return {**picked, "source": "auto"}
    names = ", ".join(sorted(t["name"] or t["id"] for t in templates)) or "none"
    raise TTDError("No MyReports template reads as the campaign-by-day performance report, "
                   "so the schedule cannot be created. Set TTD_REPORT_TEMPLATE_ID to one of "
                   "the partner's templates: " + names)


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


def schedule_body(template_id: str) -> dict:
    """What the schedule is created with. A function so the test reads it.
    No ``AdvertiserFilters``: the schedule is the partner's, so it covers
    every advertiser under the partner, now and later."""
    c = cfg()
    template_id = str(template_id or "").strip()
    return {
        "ReportScheduleName": SCHEDULE_NAME,
        "ReportTemplateId": int(template_id) if template_id.isdigit() else template_id,
        "ReportFileFormat": "CSV",
        "ReportFrequency": "Daily",
        "ReportDateRange": c["date_range"],
        "ReportDateFormat": "Sortable",
        "TimeZone": "UTC",
        "PartnerId": c["partner_id"],
        "ReportStartDateInclusive": date.today().isoformat(),
    }


def ensure_schedule() -> dict:
    """The schedule this pull reads, found or created once. Returns the
    schedule with ``template`` beside it: ``{"id", "name", "source"}`` for a
    schedule created now, or ``{"id": <the schedule's>, "source":
    "schedule"}`` for one found."""
    found = find_schedule()
    if found:
        filters = found.get("AdvertiserFilters") or []
        if filters:
            raise TTDError("The report schedule %r is pinned to %d advertisers, so clients "
                           "added since would be missing from it. Delete that schedule in the "
                           "platform (Reports, My Reports, Schedules) and the next pull "
                           "recreates it partner-wide." % (SCHEDULE_NAME, len(filters)))
        return {**found, "template": {"id": str(found.get("ReportTemplateId") or ""),
                                      "name": "", "source": "schedule"}}
    template = resolve_template()
    created = request("POST", SCHEDULE_CREATE, schedule_body(template["id"]))
    if not created.get("ReportScheduleId"):
        raise TTDError("The Trade Desk accepted the schedule without a ReportScheduleId")
    return {**created, "template": template}


def list_executions(schedule_id, days: int = RESTATE_DAYS, *, since: str | None = None,
                    until: str | None = None) -> list[dict]:
    c = cfg()
    since = since or (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    until = until or (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
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
           "executions": 0, "error": "", "template": {}, "partner": {}}
    if not configured():
        out["error"] = "not configured: " + ", ".join(missing()) + " unset"
        return out
    try:
        out["partner"] = resolve_partner()
        # The advertiser list is the count the index prints and the first
        # call that proves the token and partner id agree; the schedule no
        # longer carries it. Refused, the error carries what the token can
        # see instead, so the next thing to do is on the page.
        try:
            advertisers = list_advertisers()
        except TTDError as exc:
            raise TTDError(str(exc) + _refused_partner_help(exc))
        out["advertisers"] = len(advertisers)
        schedule = ensure_schedule()
        out["template"] = schedule.get("template") or {}
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


# ---------------------------------------------------------------------------
# History: one window, through a one-off schedule
# ---------------------------------------------------------------------------

def history_schedule_name(start: date, end: date) -> str:
    return f"{SCHEDULE_NAME} history {start.isoformat()} to {end.isoformat()}"


def history_schedule_body(template_id: str, start: date, end: date) -> dict:
    """A one-off schedule for a fixed window: ``ReportDateRange`` Custom with
    the window as ``ReportStartDateInclusive`` / ``ReportEndDateExclusive``,
    ``ReportFrequency`` Once. Partner-wide like the daily one."""
    c = cfg()
    template_id = str(template_id or "").strip()
    return {
        "ReportScheduleName": history_schedule_name(start, end),
        "ReportTemplateId": int(template_id) if template_id.isdigit() else template_id,
        "ReportFileFormat": "CSV",
        "ReportFrequency": "Once",
        "ReportDateRange": "Custom",
        "ReportStartDateInclusive": start.isoformat(),
        "ReportEndDateExclusive": (end + timedelta(days=1)).isoformat(),
        "ReportDateFormat": "Sortable",
        "TimeZone": "UTC",
        "PartnerId": c["partner_id"],
    }


def find_schedule_named(name: str) -> dict | None:
    c = cfg()
    rows = _paged(SCHEDULE_QUERY, {"PartnerId": c["partner_id"], "NameContains": name})
    for r in rows:
        if str(r.get("ReportScheduleName") or "").strip() == name:
            return r
    return None


def pull_window(start: date, end: date) -> dict:
    """One fixed window of history, for ``modules/reports/backfill.py``.

    MyReports cannot answer a window on the spot: it runs a schedule and
    delivers a file later. So the first call creates a one-off schedule for
    the window and answers ``pending``; a later call finds the schedule,
    reads its completed execution, lands the rows and answers ``ok`` with
    the count. ``{"ok", "rows", "skipped", "pending", "error", "schedule"}``.
    The nightly watermark is not stamped: history landing is not the
    night's pull happening."""
    out = {"ok": False, "rows": 0, "skipped": 0, "pending": False, "error": "",
           "schedule": history_schedule_name(start, end)}
    if not configured():
        out["error"] = "not configured: " + ", ".join(missing()) + " unset"
        return out
    try:
        found = find_schedule_named(out["schedule"])
        if found is None:
            daily = find_schedule()
            template_id = str((daily or {}).get("ReportTemplateId") or "") or resolve_template()["id"]
            created = request("POST", SCHEDULE_CREATE, history_schedule_body(template_id, start, end))
            if not created.get("ReportScheduleId"):
                raise TTDError("The Trade Desk accepted the history schedule without a ReportScheduleId")
            out["pending"] = True
            out["note"] = "history report scheduled; the file lands on the next run"
            return out
        executions = list_executions(found.get("ReportScheduleId"),
                                     since=start.strftime("%Y-%m-%dT00:00:00"),
                                     until=(end + timedelta(days=2)).strftime("%Y-%m-%dT00:00:00"))
        newest = latest_complete(executions)
        if newest is None:
            out["pending"] = True
            out["note"] = "history report scheduled, no completed run yet"
            return out
        url = next(d["DownloadURL"] for d in newest["ReportDeliveries"] if d.get("DownloadURL"))
        parsed = ttd_myreports.parse(download(url))
        if parsed["error"]:
            raise TTDError(parsed["error"])
        out["skipped"] = parsed["skipped"]
        out["rows"] = store.upsert_rows(parsed["rows"]) if parsed["rows"] else 0
        out["ok"] = True
    except TTDError as exc:
        out["error"] = _redact(str(exc))
    except Exception as exc:                # noqa: BLE001 - one window, not the job
        log.exception("reports: Trade Desk history pull failed")
        out["error"] = _redact(f"{type(exc).__name__}: {exc}")
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
    template = remembered.get("template") or {}
    partner = remembered.get("partner") or {}
    return {
        "configured": not miss,
        "connected": not miss,
        "missing": miss,
        "base": c["base"],
        "partner_id": c["partner_id"],
        "partner_name": str(partner.get("name") or ""),
        "partner_source": c["partner_source"],
        # The env override when set; else what the last pull picked or found.
        "template_id": c["template_id"] or str(template.get("id") or ""),
        "template_name": str(template.get("name") or ""),
        "template_source": "env" if c["template_id"] else str(template.get("source") or ""),
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
                 + (f" (partner {partner.get('name') or c['partner_id']}, "
                    f"{'from TTD_PARTNER_ID' if c['partner_source'] == 'env' else 'the one the token sees'})"
                    if c["partner_id"] else " (partner: not set; the pull asks the platform)")
                 + (f" -- {native.get('error')}" if native.get("error") else "")),
    }
