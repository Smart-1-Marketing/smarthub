"""The native Microsoft Advertising pull: daily campaign figures for every
account under the manager, through the Reporting API.

Reads through ``modules/ads_builder/bing_ads`` -- the one Microsoft
Advertising client in this Hub, with the OAuth refresh, the developer token,
the manager customer id and the per-call recording already on it -- rather
than a second one, the arrangement ``google_ads_perf.py`` has with
``google_ads.py``. The rows land as ``platform="bing"``, ``source="native"``
so the provider's copy of the same campaign-day defers to them
(``normalize.py``'s native-wins rule, read off the watermark).

## The StackAdapt shape, not the Google one

Reporting v13 is asynchronous: ``GenerateReport/Submit`` with a daily
``CampaignPerformanceReportRequest`` scoped to every account the manager
can see, ``GenerateReport/Poll`` until it is ready, then a ZIP holding one
CSV. So the wait is ``stackadapt.py``'s submit-poll-pending under
``BUDGET_SECONDS`` on the shared scheduler thread: past the budget the
report is **pending**, not failed -- nothing is stamped on the watermark,
because nothing landed and nothing broke, and the next pull asks again.
``POLL_TRIES`` still caps the polls whatever the clock says.

**Spend comes back in the account's currency, not micros** -- the divisor
note in ``provider_map.py`` is about Windsor's table, not this.

**``CampaignType`` is what the auto-mapper files a default product from**
(``products.BING_CHANNEL_PRODUCTS``: Search is Paid Search, Audience is
Programmatic Display, and Shopping, DynamicSearchAds, Hotel and
PerformanceMax take the platform default with the mapping row saying so),
carried on the row's ``extras`` as ``channel_type`` the way the Google pull
carries ``advertising_channel_type``.

## Not connected is a sentence, never a stack trace

Until somebody presses Connect Microsoft Ads on ``/tools/ads/settings`` the
ordinary answer of ``pull()`` is ``{"error": NOT_CONNECTED}``, which the
scheduler job skips by name and ``/reports/`` prints. Unconfigured names
the variables. Neither touches the watermark, so a provider row for
``bing`` stays the current one.

## What a run answers

``pull()`` returns ``{"ok", "rows", "accounts", "campaigns", "skipped",
"polls", "error", "pending", "environment"}`` and records the watermark; a
failure stamps its error so ``/reports/`` names it. ``status()`` is the
line the Reports index prints. No credential reaches any of them:
``bing_ads._redact()`` runs over every provider sentence and
``test_reports_bing.py`` reads every string a pull produces to prove it.
"""
from __future__ import annotations

import logging
import os
import re
import time as _time
from datetime import date, datetime, timedelta

from . import store

log = logging.getLogger(__name__)

DAYS = 14
POLL_TRIES = 6
POLL_WAIT = 5
# A house number, like StackAdapt's: long enough that a report the platform
# has nearly finished lands, short enough that every job behind this one on
# the scheduler thread is not held for a report that is not coming yet.
BUDGET_SECONDS = 20

NOT_CONNECTED = "not connected — Connect Microsoft Ads in /tools/ads/settings"

# The report's columns, by the name the CSV header carries. ``columns()``
# normalizes spacing and case, so "Time period" and "TimePeriod" both land.
ALIASES = {
    "date": ("timeperiod", "time period", "gregoriandate", "date"),
    "account_id": ("accountid", "account id"),
    "account_name": ("accountname", "account name"),
    "campaign_id": ("campaignid", "campaign id"),
    "campaign_name": ("campaignname", "campaign name"),
    "campaign_type": ("campaigntype", "campaign type"),
    "spend": ("spend", "cost"),
    "impressions": ("impressions", "impr"),
    "clicks": ("clicks",),
    "conversions": ("conversions", "conv"),
    "currency": ("currencycode", "currency code"),
}
REQUIRED = ("date", "account_id", "campaign_id", "spend", "impressions", "clicks")


class PullError(Exception):
    """A refusal, already redacted."""


class ReportPending(PullError):
    """The platform was still preparing the report when the wait budget ran
    out. Not a refusal: nothing is wrong at either end."""


def _client():
    """(bing_ads, ads_store) or (None, why)."""
    try:
        from modules.ads_builder import bing_ads, store as ads_store
    except Exception as exc:                            # noqa: BLE001
        return None, f"Smart 1 Ads is not importable ({type(exc).__name__})"
    return bing_ads, ads_store


def missing() -> list[str]:
    ba, _ = _client()
    if ba is None:
        return []
    return ba.connection_status(None)["missing"]


def configured() -> bool:
    ba, ads_store = _client()
    return bool(ba) and bool(ba.connection_status(ads_store)["configured"])


def not_configured_line() -> str:
    miss = missing()
    return "not configured: " + (", ".join(miss) if miss else "BING_AD_*") + " unset"


def _problem_line(st: dict) -> str:
    """The one sentence for a connection that is set but cannot be used:
    a manager id that is not one, or a pinned callback at a page this Hub
    does not answer. "" when neither applies."""
    if st.get("manager_id_problem"):
        return "not configured: BING_MANAGER_ACCOUNT_ID " + st["manager_id_problem"]
    if st.get("redirect_uri_problem"):
        return "not configured: MICROSOFT_ADS_REDIRECT_URI " + st["redirect_uri_problem"]
    return ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _norm(header) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(header or "").lower())


def columns(header: list) -> dict[str, str]:
    """{field: the header actually present}, for every field one is."""
    normed = {_norm(h): h for h in header if h is not None}
    out = {}
    for field, names in ALIASES.items():
        for n in names:
            key = _norm(n)
            if key in normed:
                out[field] = normed[key]
                break
    return out


def _num(value) -> float:
    s = str(value if value is not None else "").strip().replace(",", "").replace("$", "")
    if not s or s in ("-", "--"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _day(value) -> date | None:
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19] if "T" in s or " " in s else s, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def parse_report(text: str) -> dict:
    """``{"rows": [...], "skipped": n, "columns": {...}, "error": str}`` from
    a CampaignPerformanceReport CSV.

    The column row is found rather than assumed at line one -- a report
    that still carries its header block starts several lines down -- and
    every field is read with a default: a row with no campaign id, no
    account id or no day is skipped and counted, never invented. Spend is
    the account's currency as the platform wrote it.
    """
    ba, why = _client()
    if ba is None:
        return {"rows": [], "skipped": 0, "columns": {}, "error": why}
    rows = ba.rows_of(text)
    header_at = None
    cols: dict = {}
    for i, row in enumerate(rows):
        c = columns(row)
        if all(f in c for f in REQUIRED):
            header_at, cols = i, c
            break
    if header_at is None:
        seen = ", ".join(str(h) for h in (rows[0] if rows else []))[:300]
        return {"rows": [], "skipped": 0, "columns": {},
                "error": "the report carries no column row naming " + ", ".join(REQUIRED)
                         + (f" -- its first row is: {seen}" if seen else " -- it is empty")}
    header = rows[header_at]
    idx = {f: header.index(h) for f, h in cols.items()}

    def get(row, field):
        i = idx.get(field)
        return row[i] if i is not None and i < len(row) else None

    out, skipped = [], 0
    for row in rows[header_at + 1:]:
        day = _day(get(row, "date"))
        acct = str(get(row, "account_id") or "").strip()
        camp = str(get(row, "campaign_id") or "").strip()
        if day is None or not acct or not camp:
            skipped += 1
            continue
        ctype = str(get(row, "campaign_type") or "").strip()
        extras = {"account_name": str(get(row, "account_name") or "").strip()}
        if ctype:
            extras["channel_type"] = re.sub(r"[^A-Z0-9]", "", ctype.upper())
            extras["campaign_type"] = ctype
        cur = str(get(row, "currency") or "").strip()
        if cur:
            extras["currency"] = cur
        out.append({
            "platform": "bing", "source": "native", "date": day,
            "account_id": acct, "campaign_id": camp,
            "campaign_name": str(get(row, "campaign_name") or "").strip(),
            "spend": round(_num(get(row, "spend")), 2),
            "impressions": int(_num(get(row, "impressions"))),
            "clicks": int(_num(get(row, "clicks"))),
            "conversions": _num(get(row, "conversions")) if "conversions" in cols else 0.0,
            "extras": extras,
        })
    return {"rows": out, "skipped": skipped, "columns": cols, "error": ""}


# ---------------------------------------------------------------------------
# The pull
# ---------------------------------------------------------------------------

def _state_path() -> str:
    from hub import jsonstore
    return os.path.join(jsonstore.data_dir("reports"), "bing_status.json")


def _remember(state: dict) -> None:
    try:
        from hub import jsonstore
        jsonstore.write_json(_state_path(), state, durable=False)
    except Exception:                                   # noqa: BLE001 - a note is not the pull
        pass


def _remembered() -> dict:
    try:
        from hub import jsonstore
        return jsonstore.read_json(_state_path(), default={}) or {}
    except Exception:                                   # noqa: BLE001
        return {}


def wait_for(store_, request_id: str, *, sleep=None, clock=None, budget=None,
             module: str = "reports") -> str:
    """Poll until the report is ready and answer its download URL. Raises
    ``ReportPending`` when the budget runs out with it still preparing,
    ``PullError`` when the platform says Error or after ``POLL_TRIES``
    polls whatever the clock says."""
    ba, why = _client()
    if ba is None:
        raise PullError(why)
    sleep = sleep or _time.sleep
    clock = clock or _time.monotonic
    budget = BUDGET_SECONDS if budget is None else float(budget)
    started = clock()
    polls = 0
    while True:
        try:
            got = ba.poll_report(store_, request_id, module=module)
        except ba.BingAdsError as exc:
            raise PullError(ba._redact(exc.message))
        polls += 1
        if got["status"] == "Success":
            if not got["url"]:
                raise PullError("the report finished and Microsoft answered with no download URL")
            return got["url"]
        if got["status"] == "Error":
            raise PullError("Microsoft Advertising reported the report request as failed")
        if polls >= POLL_TRIES:
            raise PullError(f"the report was still in progress after {POLL_TRIES} polls")
        elapsed = clock() - started
        if elapsed + POLL_WAIT > budget:
            raise ReportPending(
                f"the report was still preparing after {elapsed:.0f}s; the next pull "
                "asks again rather than hold the scheduler for it")
        sleep(POLL_WAIT)


def fetch(start: date, end: date, *, sleep=None, clock=None, budget=None,
          accounts: list[dict] | None = None, module: str = "reports") -> dict:
    """Every campaign-day from ``start`` to ``end`` inclusive across every
    account under the manager: one report submitted, polled inside the
    wall-clock budget, downloaded and parsed. ``{"rows", "skipped",
    "accounts", "polls", "columns"}``."""
    ba, why = _client()
    if ba is None:
        raise PullError(why)
    _, ads_store = _client()
    try:
        accts = accounts if accounts is not None else ba.list_accounts(ads_store, module=module)
        if not accts:
            raise PullError("the manager account has no advertiser accounts under it that "
                            "this login can see")
        body = ba.report_request("CampaignPerformanceReportRequest", start, end,
                                 [a["id"] for a in accts], aggregation="Daily",
                                 columns=ba.CAMPAIGN_COLUMNS,
                                 name=f"smart1-hub-campaigns-{start.isoformat()}-{end.isoformat()}")
        rid = ba.submit_report(ads_store, body, module=module)
        url = wait_for(ads_store, rid, sleep=sleep, clock=clock, budget=budget, module=module)
        text = ba.download_report(url, module=module)
    except (PullError, ReportPending):
        raise
    except ba.BingAdsError as exc:
        raise PullError(ba._redact(exc.message))
    parsed = parse_report(text)
    if parsed["error"]:
        raise PullError(parsed["error"])
    return {"rows": parsed["rows"], "skipped": parsed["skipped"], "accounts": len(accts),
            "columns": parsed["columns"]}


def pull(days: int = DAYS, today: date | None = None, sleep=None, clock=None,
         budget: float | None = None) -> dict:
    """The trailing ``days`` days for every account under the manager.

    A report still preparing when the wait budget runs out answers
    ``pending: True`` with the sentence in ``error`` and touches NO
    watermark -- the last good pull is still the current one. Not
    configured and not connected are likewise clean: nothing was
    attempted."""
    out = {"ok": False, "rows": 0, "accounts": 0, "campaigns": 0, "skipped": 0,
           "polls": 0, "error": "", "pending": False, "environment": ""}
    ba, ads_store = _client()
    if ba is None:
        out["error"] = ads_store
        return out
    st = ba.connection_status(ads_store)
    out["environment"] = st.get("environment") or ""
    if not st["configured"]:
        out["error"] = not_configured_line() if st["missing"] else (
            _problem_line(st) or not_configured_line())
        return out
    if not st["connected"]:
        out["error"] = NOT_CONNECTED
        return out
    today = today or date.today()
    start = today - timedelta(days=max(1, int(days)) - 1)
    try:
        got = fetch(start, today, sleep=sleep, clock=clock, budget=budget)
        rows = got["rows"]
        out["skipped"] = got["skipped"]
        out["accounts"] = got["accounts"]
        out["campaigns"] = len({(r["account_id"], r["campaign_id"]) for r in rows})
        out["rows"] = store.upsert_rows(rows, today=today) if rows else 0
        out["ok"] = True
        store.record_sync("bing", rows=out["rows"], error="", source="native")
    except ReportPending as exc:
        out["pending"] = True
        out["error"] = ba._redact(str(exc))
    except PullError as exc:
        out["error"] = ba._redact(str(exc))
        store.record_sync("bing", rows=0, error=out["error"], source="native")
    except Exception as exc:                            # noqa: BLE001 - one platform, not the job
        log.exception("reports: Microsoft Ads pull failed")
        out["error"] = ba._redact(f"{type(exc).__name__}: {exc}")
        store.record_sync("bing", rows=0, error=out["error"], source="native")
    _remember({**out, "at": store.iso(store.now())})
    return out


def status() -> dict:
    """The line /reports/ prints. Nothing here carries a credential."""
    ba, ads_store = _client()
    if ba is None:
        return {"connected": False, "configured": False, "missing": [],
                "line": f"Microsoft Ads: {ads_store}", "last_pull": None, "last_error": ""}
    try:
        st = ba.connection_status(ads_store)
    except Exception as exc:                            # noqa: BLE001
        st = {"connected": False, "configured": False, "missing": [], "deploy_ready": False,
              "environment": "", "manager_id_problem": f"{type(exc).__name__}"}
    remembered = _remembered()
    sync = store.sync_status().get("bing") or {}
    native = sync if sync.get("source") == "native" else {}
    ready = bool(st.get("deploy_ready"))
    if ready:
        line = "Microsoft Ads: connected"
        if st.get("environment") == "sandbox":
            line += " (sandbox)"
        if remembered.get("accounts"):
            line += f", {remembered['accounts']} accounts"
        line += ", last pull " + (native.get("last_run_at") or remembered.get("at") or "never")
        if native.get("error"):
            line += f" -- {native['error']}"
        elif remembered.get("pending"):
            line += f" -- {remembered.get('error') or 'report still preparing'}"
    elif not st.get("configured"):
        if _problem_line(st):
            line = "Microsoft Ads: " + _problem_line(st)
        else:
            line = "Microsoft Ads: " + not_configured_line()
    else:
        line = "Microsoft Ads: " + NOT_CONNECTED
    return {"connected": ready, "configured": bool(st.get("configured")),
            "missing": st.get("missing") or [], "line": line,
            "environment": st.get("environment") or "",
            "accounts": remembered.get("accounts") if remembered else None,
            "last_pull": native.get("last_run_at"), "last_rows": native.get("rows"),
            "last_error": native.get("error") or "", "pending": bool(remembered.get("pending"))}
