"""The native StackAdapt pull: daily campaign figures from the platform's GraphQL API.

Lands ``platform="stackadapt"``, ``source="native"`` rows through
``store.upsert_rows`` for every advertiser the key can see, so the provider
copy of the same campaign-day defers to them (``normalize.py``'s
native-wins rule, read off the watermark ``store.record_sync`` stamps).

## Authentication and endpoint

``STACKADAPT_API_KEY`` is the API key StackAdapt's support team issues (the
Fivetran and Improvado connectors ask for the same key). It is sent as
``Authorization: Bearer <key>`` -- VERIFIED against the official
``@stackadapt/pa-typescript-sdk`` (v0.73.0), whose ``graphql-client.js``
sets exactly that header and points at ``https://api.stackadapt.com/graphql``
for production (``https://sandbox.stackadapt.com/public/graphql`` for the
sandbox). Third-party write-ups name an ``X-Authorization`` header; that is
the older REST API's spelling and is NOT what the GraphQL client sends. The
header name is overridable with ``STACKADAPT_AUTH_HEADER`` for the day the
platform changes it, without a code change.

The key is never logged, never on a result and never in an error:
``_redact()`` strips it from any provider message before it leaves this
module, and ``test_reports_stackadapt.py`` reads every string a pull
produces to prove it.

## The query

``QUERY`` below is ONE constant. What it asks for was read out of the SDK's
generated schema (``dist/gql/graphql.d.ts``), so the names are the
platform's own rather than guessed:

* VERIFIED against the schema: the root field ``campaignDelivery`` with
  arguments ``dataType`` (``TABLE``), ``granularity`` (``DAILY``) and
  ``date`` (``DateRangeInput {from, to}``, ``to`` EXCLUSIVE); the payload
  union ``CampaignDeliveryOutcome | Progress``; ``records`` (a connection
  taking ``first``/``after``) with ``pageInfo {hasNextPage endCursor}`` and
  ``nodes``; on each record ``campaign {id name advertiser {id name}}``,
  ``granularity {time startTime endTime type}`` and ``metrics``; and on
  ``DeliveryStatsRecord`` the fields ``impressions``, ``clicks``, ``cost``
  (media cost, a ``MoneyValue`` string such as ``"100.57"``),
  ``conversions``, ``videoStarts``, ``videoCompletions`` (95%),
  ``audioStarts`` and ``audioCompletions`` (95%).
* ASSUMED, because no live key has answered yet: that a DAILY table answers
  one record per campaign per day (the SDK's own insight queries are shaped
  that way); that the day is ``granularity.startTime``'s date rather than
  the free-text ``granularity.time``; that a ``Progress`` payload means "ask
  again" the way the SDK treats ``campaignInsight``'s (it polls, so this
  does too, ``PROGRESS_TRIES`` times); and that ``records(first: 500)`` is
  within the platform's page ceiling. Each is read defensively: a record
  missing a campaign id or a day is dropped and counted, never invented.

## What a run answers

``pull()`` returns ``{"ok", "rows", "advertisers", "campaigns", "skipped",
"pages", "error"}`` and records the watermark; a failure stamps its error so
``/reports/`` names it. ``status()`` is the line the Reports index prints,
the shape ``ttd.status()`` answers in.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
import time as _time

import requests

from . import store

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://api.stackadapt.com/graphql"
DEFAULT_HEADER = "Authorization"
TIMEOUT = 60
PAGE_SIZE = 500
DAYS = 14
PROGRESS_TRIES = 6
PROGRESS_WAIT = 5

KEY_ENV = ("STACKADAPT_API_KEY", "STACK_ADAPT_API", "STACK_ADAPT_API_KEY", "STACKADAPT_API")
NOT_CONFIGURED = "not configured: STACKADAPT_API_KEY (or STACK_ADAPT_API) unset"


def _first_env(names) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""

# The one query. See the module docstring for which names were verified
# against the SDK schema and which are assumed.
QUERY = """
query S1HubCampaignDelivery($from: ISO8601Date!, $to: ISO8601Date!, $first: Int!, $after: String) {
  campaignDelivery(dataType: TABLE, granularity: DAILY, date: {from: $from, to: $to}) {
    __typename
    ... on CampaignDeliveryOutcome {
      records(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          campaign { id name advertiser { id name } }
          granularity { time startTime endTime type }
          metrics {
            impressions clicks cost conversions
            videoStarts videoCompletions audioStarts audioCompletions
          }
        }
      }
    }
    ... on Progress { _ }
  }
}
""".strip()


def cfg() -> dict:
    return {
        "key": _first_env(KEY_ENV),
        "endpoint": (os.environ.get("STACKADAPT_API_ENDPOINT") or DEFAULT_ENDPOINT).strip(),
        "header": (os.environ.get("STACKADAPT_AUTH_HEADER") or DEFAULT_HEADER).strip(),
    }


def missing() -> list[str]:
    return [] if cfg()["key"] else ["STACKADAPT_API_KEY"]


def configured() -> bool:
    return not missing()


def _redact(text: str) -> str:
    """Never let the key out, however a provider message quotes it."""
    key = cfg()["key"]
    s = str(text or "")
    if key and key in s:
        s = s.replace(key, "[STACKADAPT key redacted]")
    return s[:500]


class StackAdaptError(Exception):
    """A refusal from the platform, already redacted."""


def headers() -> dict:
    """The request headers: the key under the configured header name --
    ``Authorization: Bearer <key>`` by default, the SDK's own spelling --
    and a Source the way the SDK identifies itself."""
    c = cfg()
    value = c["key"] if c["header"].lower() != "authorization" else "Bearer " + c["key"]
    return {c["header"]: value, "Content-Type": "application/json",
            "Source": "smart1-hub-reports"}


# ---------------------------------------------------------------------------
# HTTP -- one seam, so a test can stand in for the platform
# ---------------------------------------------------------------------------

def _http(url: str, *, headers: dict, body: dict, timeout: int = TIMEOUT):
    """The one call that reaches the network. Replaced whole by the test."""
    return requests.post(url, headers=headers, json=body, timeout=timeout)


def graphql(variables: dict) -> dict:
    """One execution of ``QUERY``; the ``data`` object, or a StackAdaptError."""
    c = cfg()
    if not c["key"]:
        raise StackAdaptError(NOT_CONFIGURED)
    try:
        resp = _http(c["endpoint"], headers=headers(), body={"query": QUERY, "variables": variables})
    except requests.RequestException as exc:
        raise StackAdaptError(_redact(f"StackAdapt could not be reached: {type(exc).__name__}"))
    if resp.status_code >= 400:
        raise StackAdaptError(_redact(f"HTTP {resp.status_code} from the GraphQL endpoint: "
                                      f"{(resp.text or '')[:200]}"))
    try:
        payload = resp.json() if resp.content else {}
    except ValueError:
        raise StackAdaptError("StackAdapt answered with something that is not JSON")
    if not isinstance(payload, dict):
        raise StackAdaptError("StackAdapt answered with something that is not a GraphQL response")
    errors = payload.get("errors")
    if errors:
        msgs = "; ".join(str((e or {}).get("message") or e) for e in errors[:3]
                         if isinstance(e, (dict, str)))
        raise StackAdaptError(_redact(f"GraphQL errors: {msgs}"))
    return payload.get("data") or {}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _int(value) -> int:
    try:
        return int(float(str(value).replace(",", ""))) if value not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def _money(value) -> float:
    """A MoneyValue string ("100.57"), a number, or nothing."""
    try:
        return round(float(str(value).replace(",", "")), 2) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _day(gran: dict) -> date | None:
    """The record's day: ``startTime`` first (an ISO8601DateTime), then
    ``time`` if it happens to lead with a date."""
    gran = gran if isinstance(gran, dict) else {}
    for key in ("startTime", "time"):
        s = str(gran.get(key) or "").strip()
        if len(s) >= 10:
            try:
                return date.fromisoformat(s[:10])
            except ValueError:
                continue
    return None


def parse_records(nodes: list) -> dict:
    """``{"rows": [...], "skipped": n}`` from one page of records.

    Every field is read with a default; a record with no campaign id or no
    day is skipped and counted, never invented. A campaign's advertiser id
    is the fact row's account_id; with none, the campaign is filed under
    ``advertiser:unknown`` so the row still lands and the queue shows it.
    """
    rows, skipped = [], 0
    for node in nodes or []:
        if not isinstance(node, dict):
            skipped += 1
            continue
        camp = node.get("campaign") or {}
        adv = (camp.get("advertiser") or {}) if isinstance(camp, dict) else {}
        m = node.get("metrics") or {}
        cid = str((camp.get("id") if isinstance(camp, dict) else "") or "").strip()
        day = _day(node.get("granularity") or {})
        if not cid or day is None:
            skipped += 1
            continue
        account = str(adv.get("id") or "").strip() or "advertiser:unknown"
        extras = {"advertiser_name": str(adv.get("name") or "").strip()}
        for k in ("videoStarts", "audioStarts", "audioCompletions", "videoCompletions"):
            if m.get(k) not in (None, ""):
                extras[k] = _int(m.get(k))
        video = _int(m.get("videoCompletions"))
        audio = _int(m.get("audioCompletions"))
        row = {
            "platform": "stackadapt", "source": "native", "date": day,
            "account_id": account, "campaign_id": cid,
            "campaign_name": str(camp.get("name") or "").strip(),
            "spend": _money(m.get("cost")),
            "impressions": _int(m.get("impressions")),
            "clicks": _int(m.get("clicks")),
            "conversions": _int(m.get("conversions")),
            "extras": extras,
        }
        if m.get("videoStarts") not in (None, ""):
            row["video_views"] = _int(m.get("videoStarts"))
        if m.get("videoCompletions") not in (None, "") or m.get("audioCompletions") not in (None, ""):
            row["completes"] = video + audio
        rows.append(row)
    return {"rows": rows, "skipped": skipped}


# ---------------------------------------------------------------------------
# The pull
# ---------------------------------------------------------------------------

def _state_path() -> str:
    from hub import jsonstore
    return os.path.join(jsonstore.data_dir("reports"), "stackadapt_status.json")


def _remember(state: dict) -> None:
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


def fetch(start: date, end: date, sleep=None) -> dict:
    """Every record from ``start`` to ``end`` inclusive, paged on the cursor,
    polled while the platform answers Progress. Returns
    ``{"rows", "skipped", "pages", "progress_waits"}``."""
    sleep = sleep or _time.sleep
    variables = {"from": start.isoformat(), "to": (end + timedelta(days=1)).isoformat(),
                 "first": PAGE_SIZE, "after": None}
    rows, skipped, pages, waits = [], 0, 0, 0
    while True:
        data = graphql(variables)
        payload = data.get("campaignDelivery") or {}
        if not isinstance(payload, dict):
            raise StackAdaptError("campaignDelivery came back in a shape this pull does not read")
        if payload.get("__typename") == "Progress" or ("records" not in payload and "_" in payload):
            waits += 1
            if waits > PROGRESS_TRIES:
                raise StackAdaptError(f"the report was still in progress after {PROGRESS_TRIES} polls")
            sleep(PROGRESS_WAIT)
            continue
        records = payload.get("records") or {}
        parsed = parse_records(records.get("nodes") or [])
        rows.extend(parsed["rows"])
        skipped += parsed["skipped"]
        pages += 1
        info = records.get("pageInfo") or {}
        if info.get("hasNextPage") and info.get("endCursor") and pages < 200:
            variables = {**variables, "after": info["endCursor"]}
            continue
        return {"rows": rows, "skipped": skipped, "pages": pages, "progress_waits": waits}


def pull(days: int = DAYS, today: date | None = None, sleep=None) -> dict:
    """The trailing ``days`` days for every advertiser the key can see."""
    out = {"ok": False, "rows": 0, "advertisers": 0, "campaigns": 0, "skipped": 0,
           "pages": 0, "error": ""}
    if not configured():
        out["error"] = NOT_CONFIGURED
        return out
    today = today or date.today()
    start = today - timedelta(days=max(1, int(days)) - 1)
    try:
        got = fetch(start, today, sleep=sleep)
        rows = got["rows"]
        out["skipped"] = got["skipped"]
        out["pages"] = got["pages"]
        out["advertisers"] = len({r["account_id"] for r in rows})
        out["campaigns"] = len({(r["account_id"], r["campaign_id"]) for r in rows})
        out["rows"] = store.upsert_rows(rows) if rows else 0
        out["ok"] = True
        store.record_sync("stackadapt", rows=out["rows"], error="", source="native")
    except StackAdaptError as exc:
        out["error"] = _redact(str(exc))
        store.record_sync("stackadapt", rows=0, error=out["error"], source="native")
    except Exception as exc:                # noqa: BLE001 - one platform, not the job
        log.exception("reports: StackAdapt pull failed")
        out["error"] = _redact(f"{type(exc).__name__}: {exc}")
        store.record_sync("stackadapt", rows=0, error=out["error"], source="native")
    _remember({**out, "at": store.iso(store.now())})
    return out


def status() -> dict:
    """The line /reports/ prints. Nothing here carries the key."""
    c = cfg()
    miss = missing()
    remembered = _remembered()
    sync = store.sync_status().get("stackadapt") or {}
    native = sync if sync.get("source") == "native" else {}
    last = native.get("last_run_at") or remembered.get("at") or "never"
    if miss:
        line = "StackAdapt: " + NOT_CONFIGURED
    else:
        line = "StackAdapt: connected, "
        if remembered.get("advertisers") is not None:
            line += f"{remembered.get('advertisers')} advertisers, "
        line += "last pull " + str(last)
        if native.get("error"):
            line += f" -- {native['error']}"
    return {
        "configured": not miss, "connected": not miss, "missing": miss,
        "endpoint": c["endpoint"], "header": c["header"],
        "advertisers": remembered.get("advertisers") if remembered else None,
        "last_pull": native.get("last_run_at") or remembered.get("at"),
        "last_rows": native.get("rows"),
        "last_error": native.get("error") or (remembered.get("error") if remembered else ""),
        "line": line,
    }
