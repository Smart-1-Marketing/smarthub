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

**One report per owning customer, usually one.** A report is scoped to
account ids and authorized by the ``CustomerId`` header, which has to be
the customer that owns those accounts; an agency's manager customer
manages accounts it does not own. ``account_groups()`` files every account
under its ``parent_customer_id`` and ``run_reports()`` submits one report
per group with that customer in the header.

**A report still preparing is carried, not resubmitted.** The request id
is written to the module's note the moment Microsoft issues it, keyed by
the customer it was asked for, and the next pull -- the nightly one, or a
press on the check page -- polls that same id first, for up to
``PENDING_TTL_HOURS``. Rows that did land are written (every row is an
upsert), the watermark is stamped only when every group has landed, and
a group that has been preparing past the TTL is asked for afresh.

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
# How long a report request id is carried between pulls before a fresh
# report is asked for instead. Microsoft finishes a report in seconds to
# minutes; one that has been preparing for hours is one it lost.
PENDING_TTL_HOURS = 6

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
    """A number out of whatever the CSV wrote: ``"1,234.56"``, ``"$12"``,
    ``"12.5%"``, a blank, a dash. Anything that is not a number is 0, never
    a raise -- one odd cell must not cost the report."""
    s = str(value if value is not None else "").strip()
    if not s or s in ("-", "--", "N/A", "n/a"):
        return 0.0
    s = re.sub(r"[^0-9.\-eE]", "", s.replace(",", ""))
    if not s or s in ("-", ".", "-."):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


_DAY_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M", "%d.%m.%Y",
                "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y")


def _day(value) -> date | None:
    """A day out of the ``TimePeriod`` cell in whichever shape the report
    wrote it: ISO, US, US with a time of day, or a spelled month. ``None``
    for anything else, which the parser counts as a skipped row."""
    s = str(value or "").strip().strip('"')
    if not s:
        return None
    for fmt in _DAY_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).date()
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


def _remember(state: dict, *, keep_pending: bool = True) -> None:
    """Write the note. The carried report ids (``pending_ids``) ride through
    unless the caller replaces them: a run that stopped at "not connected"
    must not throw away a report the last run was waiting on."""
    if keep_pending and "pending_ids" not in state:
        held = _remembered().get("pending_ids")
        state = {**state, "pending_ids": held if isinstance(held, dict) else {}}
    try:
        from hub import jsonstore
        jsonstore.write_json(_state_path(), state, durable=False)
    except Exception:                                   # noqa: BLE001 - a note is not the pull
        pass


def _remember_pending(customer_id: str, request_id: str, start: date, end: date) -> None:
    """Record a report the moment Microsoft issues its id, keyed by the
    customer it was asked for, without disturbing the rest of the note --
    so a report asked for from the check page is one the nightly pull
    collects rather than an orphan, and a pull that ran out of budget does
    not pay for a second report tomorrow."""
    state = _remembered()
    held = dict(state.get("pending_ids") or {})
    held[str(customer_id)] = {"request_id": str(request_id), "since": store.iso(store.now()),
                              "start": start.isoformat(), "end": end.isoformat()}
    _remember({**state, "pending_ids": held}, keep_pending=False)


def _forget_pending(customer_id: str) -> None:
    state = _remembered()
    held = dict(state.get("pending_ids") or {})
    if str(customer_id) in held:
        del held[str(customer_id)]
        _remember({**state, "pending_ids": held}, keep_pending=False)


def pending_ids(now=None) -> dict:
    """``{customer_id: request_id}`` still worth polling: carried for less
    than PENDING_TTL_HOURS. Older ones are dropped here, so the caller asks
    for a fresh report instead of polling one Microsoft has lost."""
    now = now or store.now()
    out, dropped = {}, []
    for cust, held in (_remembered().get("pending_ids") or {}).items():
        rid = held.get("request_id") if isinstance(held, dict) else held
        since = held.get("since") if isinstance(held, dict) else None
        age_h = None
        try:
            if since:
                dt = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
                ref = now if getattr(now, "tzinfo", None) or not getattr(dt, "tzinfo", None) else now.replace(tzinfo=dt.tzinfo)
                age_h = (ref - dt).total_seconds() / 3600.0
        except Exception:                               # noqa: BLE001 - an unreadable stamp is a stale one
            age_h = None
        if rid and age_h is not None and 0 <= age_h < PENDING_TTL_HOURS:
            out[str(cust)] = str(rid)
        else:
            dropped.append(str(cust))
    for cust in dropped:
        _forget_pending(cust)
    return out


def _remembered() -> dict:
    try:
        from hub import jsonstore
        return jsonstore.read_json(_state_path(), default={}) or {}
    except Exception:                                   # noqa: BLE001
        return {}


def wait_for(store_, request_id: str, *, sleep=None, clock=None, budget=None,
             module: str = "reports", customer_id: str = "") -> str:
    """Poll until the report is ready and answer its download URL -- or
    ``""`` on a Success that carries no URL, which is how Microsoft answers
    a report with no rows. Raises ``ReportPending`` when the budget runs out
    with it still preparing, ``PullError`` when the platform says Error or
    after ``POLL_TRIES`` polls whatever the clock says."""
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
            got = ba.poll_report(store_, request_id, module=module, customer_id=customer_id)
        except ba.BingAdsError as exc:
            raise PullError(ba._redact(exc.message))
        polls += 1
        if got["status"] == "Success":
            return got["url"] or ""
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


def account_groups(accts: list[dict]) -> dict[str, list[str]]:
    """``{customer_id: [account ids]}`` -- every account under the customer
    that owns it, which is the ``CustomerId`` a report over it is
    authorized by. An account with no parent recorded goes under the
    manager."""
    ba, _ = _client()
    manager = ba.cfg()["manager_id"] if ba else ""
    groups: dict[str, list[str]] = {}
    for a in accts:
        cust = str(a.get("parent_customer_id") or manager or "")
        groups.setdefault(cust, []).append(str(a["id"]))
    return groups


def run_reports(kind: str, start: date, end: date, groups: dict[str, list[str]], *,
                aggregation: str, columns: tuple, name: str, sleep=None, clock=None,
                budget=None, carry: bool = False, module: str = "reports") -> dict:
    """One report per customer group, submitted (or, with ``carry``, the id
    the last run was waiting on polled first), waited for inside ONE shared
    wall-clock budget, downloaded and answered as text.

    ``{"texts": {customer_id: csv text or ""}, "pending": {customer_id:
    request_id}, "polls": n, "submitted": [customer ids]}``. A group still
    preparing when the budget runs out is in ``pending`` with its id
    written to the note (when ``carry``), and the groups after it are not
    waited for this run -- they are submitted, remembered, and collected
    next time. Nothing here raises for pending; a refusal raises PullError.
    """
    ba, why = _client()
    if ba is None:
        raise PullError(why)
    _, ads_store = _client()
    sleep = sleep or _time.sleep
    clock = clock or _time.monotonic
    budget = BUDGET_SECONDS if budget is None else float(budget)
    started = clock()
    carried = pending_ids() if carry else {}
    out = {"texts": {}, "pending": {}, "polls": 0, "submitted": [], "carried": []}
    for cust, ids in groups.items():
        try:
            rid = carried.get(cust)
            if rid:
                out["carried"].append(cust)
            else:
                body = ba.report_request(kind, start, end, ids, aggregation=aggregation,
                                         columns=columns, name=name)
                rid = ba.submit_report(ads_store, body, module=module, customer_id=cust)
                out["submitted"].append(cust)
                if carry:
                    _remember_pending(cust, rid, start, end)
            left = budget - (clock() - started)
            if left <= 0:
                out["pending"][cust] = rid
                continue
            url = wait_for(ads_store, rid, sleep=sleep, clock=clock, budget=left,
                           module=module, customer_id=cust)
            out["texts"][cust] = ba.download_report(url, module=module) if url else ""
            if carry:
                _forget_pending(cust)
        except ReportPending:
            out["pending"][cust] = rid
        except (PullError,) :
            if carry:
                _forget_pending(cust)
            raise
        except ba.BingAdsError as exc:
            if carry:
                _forget_pending(cust)
            raise PullError(ba._redact(exc.message))
    return out


def fetch(start: date, end: date, *, sleep=None, clock=None, budget=None,
          accounts: list[dict] | None = None, module: str = "reports") -> dict:
    """Every campaign-day from ``start`` to ``end`` inclusive across every
    account the consent can see: one daily campaign report per owning
    customer, polled inside the wall-clock budget, downloaded and parsed.
    ``{"rows", "skipped", "accounts", "groups", "pending", "columns",
    "strategy", "notes"}``. Rows from the groups that landed are answered
    even when another group is still preparing; ``pending`` names those."""
    ba, why = _client()
    if ba is None:
        raise PullError(why)
    _, ads_store = _client()
    try:
        if accounts is not None:
            found = {"accounts": accounts, "strategy": "given", "notes": [], "skipped": []}
        else:
            found = ba.discover_accounts(ads_store, module=module)
        accts = found["accounts"]
        if not accts:
            why = "; ".join(found.get("notes") or []) or "no advertiser account is visible to this login"
            skipped = found.get("skipped") or []
            if skipped:
                why += f" ({len(skipped)} skipped as {', '.join(sorted({a['status'] for a in skipped}))})"
            raise PullError("no advertiser accounts to report on: " + why)
        groups = account_groups(accts)
        got = run_reports("CampaignPerformanceReportRequest", start, end, groups,
                          aggregation="Daily", columns=ba.CAMPAIGN_COLUMNS,
                          name=f"smart1-hub-campaigns-{start.isoformat()}-{end.isoformat()}",
                          sleep=sleep, clock=clock, budget=budget, carry=True, module=module)
    except (PullError, ReportPending):
        raise
    except ba.BingAdsError as exc:
        raise PullError(ba._redact(exc.message))
    rows, skipped, cols = [], 0, {}
    for cust, text in got["texts"].items():
        if not text:
            continue                      # a Success with no URL: no rows for that customer
        parsed = parse_report(text)
        if parsed["error"]:
            raise PullError(parsed["error"])
        rows.extend(parsed["rows"])
        skipped += parsed["skipped"]
        cols = cols or parsed["columns"]
    return {"rows": rows, "skipped": skipped, "accounts": len(accts), "groups": len(groups),
            "pending": got["pending"], "carried": got["carried"], "polls": got["polls"],
            "columns": cols, "strategy": found.get("strategy", ""), "notes": found.get("notes") or [],
            "accounts_skipped": len(found.get("skipped") or [])}


def pull(days: int = DAYS, today: date | None = None, sleep=None, clock=None,
         budget: float | None = None) -> dict:
    """The trailing ``days`` days for every account under the manager.

    A report still preparing when the wait budget runs out answers
    ``pending: True`` with the sentence in ``error`` and touches NO
    watermark -- the last good pull is still the current one. Not
    configured and not connected are likewise clean: nothing was
    attempted."""
    out = {"ok": False, "rows": 0, "accounts": 0, "campaigns": 0, "skipped": 0,
           "polls": 0, "error": "", "pending": False, "environment": "", "strategy": "",
           "groups": 0, "pending_groups": 0}
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
        out["strategy"] = got["strategy"]
        out["groups"] = got["groups"]
        out["campaigns"] = len({(r["account_id"], r["campaign_id"]) for r in rows})
        # What landed is written now -- every row is an upsert -- so a
        # customer whose report is still preparing costs nothing the others
        # already delivered.
        out["rows"] = store.upsert_rows(rows, today=today) if rows else 0
        if got["pending"]:
            out["pending"] = True
            out["pending_groups"] = len(got["pending"])
            n = len(got["pending"])
            out["error"] = (f"{n} of {got['groups']} report{'s' if got['groups'] != 1 else ''} still preparing "
                            f"at Microsoft after the wait budget; the request id is kept and the next "
                            f"pull asks for it again rather than paying for a new report")
        else:
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
            line += f", {remembered['accounts']} account{'s' if remembered['accounts'] != 1 else ''}"
            if remembered.get("groups", 0) > 1:
                line += f" under {remembered['groups']} customers"
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
            "check_page": "/reports/bing-check",
            "environment": st.get("environment") or "",
            "accounts": remembered.get("accounts") if remembered else None,
            "last_pull": native.get("last_run_at"), "last_rows": native.get("rows"),
            "last_error": native.get("error") or "", "pending": bool(remembered.get("pending"))}


# ---------------------------------------------------------------------------
# /reports/bing-check
# ---------------------------------------------------------------------------

LADDER = (
    (1, "The variables are set: the app registration, the developer token, the manager's customer id",
        "named, one by one, on the settings card"),
    (2, "The refresh token mints an access token",
        "invalid_grant -- a consent revoked or given to another registration, never a bad secret"),
    (3, "The connected user is readable through Customer Management",
        "InvalidCredentials -- the developer token is for the other environment, or is not yet approved"),
    (4, "Advertiser accounts are visible to that user",
        "none listed -- Connect was pressed by a login without a role on the manager account"),
    (5, "A one-day account report comes back with a column row this pull reads",
        "no column row, or names this module does not know -- the transcription to correct"),
)


def check(today: date | None = None, *, budget: float | None = None, sleep=None, clock=None) -> dict:
    """``_check()``, and never a raise: a page that 500s costs the panel,
    and this one is opened precisely when the connection is behaving oddly.
    Any escape is reported in the page's own ``error``."""
    try:
        return _check(today=today, budget=budget, sleep=sleep, clock=clock)
    except Exception as exc:                            # noqa: BLE001 - a page, not a pull
        log.exception("reports: the Microsoft Ads check page could not be built")
        ba, _ = _client()
        redact = ba._redact if ba else (lambda t: str(t)[:300])
        return {"status": {"configured": False, "connected": False, "line": "Microsoft Ads: the check page could not be built"},
                "ladder": list(LADDER), "rung": 0, "problems": [], "user": {}, "accounts": [],
                "skipped": [], "strategy": "", "raw_keys": [], "groups": {}, "report": None,
                "columns": {}, "aliases": {k: list(v) for k, v in ALIASES.items()},
                "required": list(REQUIRED), "last": _remembered(), "carried": {},
                "window": {"start": "", "end": ""}, "environment": "",
                "error": redact(f"the check page could not be built: {type(exc).__name__}: {exc}")}


def _check(today: date | None = None, *, budget=None, sleep=None, clock=None) -> dict:
    """The ladder, climbed as far as it goes, calling nothing at all while
    the connection is unconfigured or unconsented: a credential is not
    sent to find out whether it is set. Rung 5 asks for a one-day account
    report (yesterday), the smallest report the service makes, and shows
    its column row against what the parser reads -- the check the module
    docstring says the first live pull is."""
    today = today or date.today()
    day = today - timedelta(days=1)
    out = {"status": status(), "ladder": list(LADDER), "rung": 1, "problems": [],
           "user": {}, "accounts": [], "skipped": [], "strategy": "", "raw_keys": [],
           "groups": {}, "report": None, "columns": {},
           "aliases": {k: list(v) for k, v in ALIASES.items()}, "required": list(REQUIRED),
           "last": _remembered(), "carried": {}, "window": {"start": day.isoformat(), "end": day.isoformat()},
           "environment": "", "error": ""}
    ba, ads_store = _client()
    if ba is None:
        out["error"] = str(ads_store)
        return out
    st = ba.connection_status(ads_store)
    out["environment"] = st.get("environment") or ""
    if not st["configured"]:
        out["error"] = (not_configured_line() if st["missing"] else _problem_line(st))
        out["problems"] = [b["name"] + ": " + b["why"] for b in st.get("blocks") or []]
        return out
    if not st["connected"]:
        out["rung"] = 2
        out["error"] = NOT_CONNECTED
        return out
    try:
        ba.access_token(ads_store)
    except ba.BingAdsError as exc:
        out["rung"] = 2
        out["error"] = ba._redact(exc.message)
        return out
    out["rung"] = 3
    try:
        found = ba.discover_accounts(ads_store, module="reports")
    except ba.BingAdsError as exc:
        out["error"] = ba._redact(exc.message)
        return out
    out["user"] = found["user"]
    out["problems"] = list(found["notes"])
    if not found["user"].get("id"):
        # The user could not be read; the customer-id search still ran.
        out["rung"] = 3 if not found["accounts"] else 4
    else:
        out["rung"] = 4
    out["accounts"], out["skipped"] = found["accounts"], found["skipped"]
    out["strategy"], out["raw_keys"] = found["strategy"], found["raw_keys"]
    if not found["accounts"]:
        out["error"] = "no advertiser accounts are visible to this login"
        return out
    out["rung"] = 5
    groups = account_groups(found["accounts"])
    out["groups"] = groups
    out["carried"] = pending_ids()
    # Rung 5: the smallest report the service makes, one day, account
    # level, over the first group only -- what the pull will do at scale,
    # and enough to see the column row.
    first = next(iter(groups))
    try:
        got = run_reports("AccountPerformanceReportRequest", day, day, {first: groups[first]},
                          aggregation="Daily", columns=ba.ACCOUNT_COLUMNS + ("TimePeriod",),
                          name=f"smart1-hub-check-{day.isoformat()}", sleep=sleep, clock=clock,
                          budget=budget, carry=False, module="reports")
    except PullError as exc:
        out["error"] = str(exc)
        return out
    if got["pending"]:
        out["report"] = {"pending": True, "header": [], "first": [], "rows": 0,
                         "note": "Microsoft is still preparing the one-day report; refresh in a moment."}
        return out
    text = got["texts"].get(first, "")
    rows = ba.rows_of(text) if text else []
    header, first_row, cols = [], [], {}
    for i, row in enumerate(rows):
        c = columns(row)
        if "account_id" in c and "spend" in c:
            header, cols = row, c
            first_row = rows[i + 1] if i + 1 < len(rows) else []
            break
    out["columns"] = cols
    out["report"] = {"pending": False, "header": header, "first": first_row,
                     "rows": max(0, len(rows) - 1) if header else 0,
                     "note": ("Microsoft answered a report with no rows for yesterday (a Success with no "
                              "download URL), which is how an account with no spend that day reads."
                              if not text else
                              "" if header else
                              "the report carried no column row this module recognizes; the first row is shown")}
    if text and not header:
        out["report"]["first"] = rows[0] if rows else []
        out["rung"] = 5
        out["error"] = "the one-day report carried no column row naming account id and spend"
    if header or not text:
        # A column row this module reads, or a Success with no rows at all
        # (an account that spent nothing yesterday): the service answered
        # the report, which is what the rung asks.
        out["rung"] = 6
    return out
