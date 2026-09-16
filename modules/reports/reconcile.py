"""Does the fact table add up to the month the platform would invoice?

Everything a client reads is campaign-days summed. Nothing in the module
could say whether that sum was the platform's month: a day the restate
window never re-read, a campaign a pull's filter left out, a batch that
failed halfway, a divisor wrong from day one, a row held in quarantine --
every one of those makes the sum smaller or larger than the platform's own
figure, and every screen stays internally consistent while it does.

``run(month)`` asks each platform for its own total for the month and
compares. Two kinds of source, and the row says which, because they catch
different mistakes:

* **independent** -- a different aggregation the platform computed. Google
  Ads answers a customer-level query (``FROM customer``, the month as a
  date range and nothing segmented), which is the account's own total and
  not our campaign rows re-added. This is the only kind that can catch a
  systematic error.
* **re-read** -- the same feed summed whole: a provider's raw table for the
  month in one ``SUM``, or StackAdapt's month fetched again in one call.
  It catches what our normalize dropped, held or never read, and cannot
  catch the feed itself being wrong.

Rules, each a way the comparison would go confidently wrong:

* **The window ends yesterday**, on both sides. Today is partial on the
  platform and partial in the fact table, at two different moments, and
  comparing partials reports a drift that is only the clock.
* **A platform that cannot be asked is not measured**, with the reason,
  never "agrees": no connection, no confirmed map, a reader that raised.
  The Trade Desk is not measured by design -- the MyReports file is the
  pull's own input, so re-reading it proves the parser and nothing else,
  and a partner token can call no other figure -- and the row says so.
* **Ours counts every row of the platform**, mapped or not, confirmed or
  not: the platform's total covers the whole account and a comparison
  against the confirmed subset would report the unconfirmed campaigns as
  drift.
* **Rows held in quarantine are counted beside the drift**, because they
  are the drift's most likely explanation and the person reading the row
  should not have to go looking.
* **The tolerance is ours.** No platform publishes one;
  ``TOLERANCE_PCT`` is house and the page says so. Attribution restates a
  day or two for weeks and Google's customer total includes campaigns the
  daily pull's ``status != REMOVED`` filter has since dropped, so exact
  agreement is not the normal case and a rule that fires on every cent is
  one somebody switches off.

Nothing here writes a fact row. It writes one ``Reconcile`` row per
platform-month and an activity row when a platform's state CHANGES --
``hub/google_index.py``'s rule; a drift logged every night for ever is the
noise the real ones hide in.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text

from . import provider_map, store

log = logging.getLogger(__name__)

# House: no platform publishes how far its own restatements move a month.
TOLERANCE_PCT = Decimal("2.0")
TOLERANCE_SOURCE = "house"

STATES = ("agree", "drift", "not_measured")
STATE_LABELS = {"agree": "Agrees", "drift": "Drift", "not_measured": "Not measured"}

# Platforms with no total reachable, named with the reason rather than
# quietly absent from the answer.
NOT_MEASURABLE = {
    "ttd": ("the MyReports file is the pull's own input, so re-reading it proves the parser "
            "and nothing else, and a partner token can call no other figure"),
    "suite": "Smart 1 Suite rows are outcomes, not delivery; there is no spend to reconcile",
}

CENT = Decimal("0.01")


def _q(d) -> Decimal:
    return Decimal(d or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def month_window(month: str | None, today: date | None = None) -> dict:
    """``{"key", "start", "end", "through"}`` -- the month, and the day the
    comparison runs through: yesterday inside the current month, the last
    day of a month that has closed."""
    today = today or date.today()
    try:
        y, m = int(month[:4]), int(month[5:7])
        start = date(y, m, 1)
    except (TypeError, ValueError):
        start = today.replace(day=1)
    end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    through = min(end, today - timedelta(days=1))
    return {"key": f"{start:%Y-%m}", "start": start, "end": end, "through": through}


# ---------------------------------------------------------------------------
# Their side
# ---------------------------------------------------------------------------

def customer_gaql(start: date, end: date) -> str:
    """The account's own total: ``FROM customer`` with the window as a date
    range and NO segment selected, so the platform answers one row per
    account rather than our campaign rows re-added."""
    return (
        "SELECT metrics.cost_micros, metrics.impressions, metrics.clicks "
        "FROM customer "
        f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
    )


def google_total(start: date, end: date) -> dict:
    """Google Ads' customer-level total across every client account under
    the manager. Independent of the daily pull: a different query, a
    different aggregation, computed by Google."""
    from . import google_ads_perf
    ga, ads_store = google_ads_perf._client()
    if ga is None:
        return {"measured": False, "reason": str(ads_store)}
    st = ga.connection_status(ads_store)
    if not st.get("deploy_ready"):
        return {"measured": False, "reason": google_ads_perf.NOT_CONNECTED}
    accounts = ga.list_client_accounts(ads_store)
    spend, imps, clicks, asked, refused = Decimal(0), 0, 0, 0, []
    for acct in accounts:
        if acct.get("is_manager") or acct.get("error"):
            continue
        cid = str(acct.get("id") or "")
        try:
            rows = ga.search(cid, customer_gaql(start, end), store=ads_store,
                             login_customer_id=acct.get("manager_id"))
        except Exception as exc:                        # noqa: BLE001 - one account
            refused.append(f"{cid}: {getattr(exc, 'message', None) or type(exc).__name__}")
            continue
        asked += 1
        for row in rows:
            m = row.get("metrics") or {}
            spend += Decimal(str(ga.micros(m.get("costMicros"))))
            imps += int(float(m.get("impressions") or 0))
            clicks += int(float(m.get("clicks") or 0))
    if not asked:
        return {"measured": False,
                "reason": "no client account answered" + (": " + "; ".join(refused[:3]) if refused else "")}
    out = {"measured": True, "spend": _q(spend), "impressions": imps, "clicks": clicks,
           "independent": True,
           "label": f"Google Ads customer-level query across {asked} account{'s' if asked != 1 else ''}"}
    if refused:
        out["note"] = f"{len(refused)} account(s) refused and are not in the total: " + "; ".join(refused[:3])
    return out


def provider_total(platform: str, start: date, end: date) -> dict:
    """The provider's raw table for the month, summed whole through the
    confirmed map. A re-read of the same feed: it catches what the
    normalize dropped, held or never read, and cannot catch the feed being
    wrong. Only through a CONFIRMED map -- summing placeholder columns
    compares our total against a guess."""
    from . import normalize
    src = provider_map.PLATFORM_SOURCES.get(platform)
    if not src:
        return {"measured": False, "reason": "no provider source for this platform"}
    checks = {c["platform"]: c for c in normalize.check_sources()}
    c = checks.get(platform)
    if not c or c["status"] != "resolved":
        return {"measured": False,
                "reason": f"the provider table does not resolve ({(c or {}).get('status', 'unknown').replace('_', ' ')})"}
    if c["confirmation"]["state"] != "confirmed":
        return {"measured": False,
                "reason": "the provider map is not confirmed, so its columns are still a guess"}
    schema = normalize._schema()
    table = (f"{normalize._q(schema)}." if schema else "") + normalize._q(src["table"])
    sql = (f"SELECT SUM({normalize._q(src['spend'])}), SUM({normalize._q(src['impressions'])}), "
           f"SUM({normalize._q(src['clicks'])}) FROM {table} "
           f"WHERE {normalize._q(src['date'])} >= :a AND {normalize._q(src['date'])} <= :b")
    bound = ((start.isoformat(), end.isoformat()) if not store.is_postgres() else (start, end))
    with store.engine.connect() as conn:
        spend, imps, clicks = conn.execute(text(sql), {"a": bound[0], "b": bound[1]}).one()
    divisor = src.get("spend_divisor") or 1
    return {"measured": True, "spend": _q(Decimal(str(spend or 0)) / divisor),
            "impressions": int(imps or 0), "clicks": int(clicks or 0), "independent": False,
            "label": f"the provider's {c['table']} summed whole"}


def stackadapt_total(start: date, end: date) -> dict:
    """StackAdapt's month fetched again in one call and summed. A re-read:
    the same endpoint the pull reads, so it catches a day the restate
    window never re-read and not the feed being wrong."""
    from . import stackadapt
    if not stackadapt.configured():
        return {"measured": False, "reason": stackadapt.NOT_CONFIGURED}
    got = stackadapt.fetch(start, end)
    rows = got["rows"]
    return {"measured": True,
            "spend": _q(sum((Decimal(str(r.get("spend") or 0)) for r in rows), Decimal(0))),
            "impressions": sum(int(r.get("impressions") or 0) for r in rows),
            "clicks": sum(int(r.get("clicks") or 0) for r in rows),
            "independent": False,
            "label": f"StackAdapt's month fetched again ({len(rows)} campaign-days)"}


def bing_total(start: date, end: date) -> dict:
    """Microsoft Advertising's month as an AccountPerformanceReport over the
    same window, one row per account, summed. A re-read: the same Reporting
    service the pull reads, on a different report, so it catches a day the
    restate window never re-read and a campaign a report scope left out --
    and not the feed being wrong. Polled inside the pull's own budget, so
    a report not ready in time is *not measured* for tonight rather than
    the scheduler thread being held for it."""
    from . import bing
    ba, ads_store = bing._client()
    if ba is None:
        return {"measured": False, "reason": str(ads_store)}
    st = ba.connection_status(ads_store)
    if not st.get("configured"):
        return {"measured": False, "reason": bing.not_configured_line()}
    if not st.get("connected"):
        return {"measured": False, "reason": bing.NOT_CONNECTED}
    try:
        accts = ba.list_accounts(ads_store, module="reports")
        if not accts:
            return {"measured": False, "reason": "no advertiser account under the manager"}
        body = ba.report_request("AccountPerformanceReportRequest", start, end,
                                 [a["id"] for a in accts], aggregation="Summary",
                                 columns=ba.ACCOUNT_COLUMNS,
                                 name=f"smart1-hub-accounts-{start.isoformat()}-{end.isoformat()}")
        rid = ba.submit_report(ads_store, body, module="reports")
        url = bing.wait_for(ads_store, rid, module="reports")
        rows = ba.rows_of(ba.download_report(url, module="reports"))
    except bing.ReportPending as exc:
        return {"measured": False, "reason": str(exc)}
    except bing.PullError as exc:
        return {"measured": False, "reason": str(exc)}
    except ba.BingAdsError as exc:
        return {"measured": False, "reason": ba._redact(exc.message)}
    cols = None
    spend, imps, clicks, counted = Decimal(0), 0, 0, 0
    for row in rows:
        if cols is None:
            c = bing.columns(row)
            if all(f in c for f in ("account_id", "spend", "impressions", "clicks")):
                cols = {f: row.index(h) for f, h in c.items()}
            continue
        def get(field):
            i = cols.get(field)
            return row[i] if i is not None and i < len(row) else None
        if not str(get("account_id") or "").strip():
            continue
        counted += 1
        spend += Decimal(str(bing._num(get("spend"))))
        imps += int(bing._num(get("impressions")))
        clicks += int(bing._num(get("clicks")))
    if cols is None:
        return {"measured": False, "reason": "the account report carried no column row"}
    return {"measured": True, "spend": _q(spend), "impressions": imps, "clicks": clicks,
            "independent": False,
            "label": f"Microsoft Ads account report over the same window ({counted} account{'s' if counted != 1 else ''})"}


def amazon_dsp_total(start: date, end: date) -> dict:
    """Amazon's month asked for again, one report per advertiser under the
    entity, summed. A re-read: the same reporting endpoint the pull reads,
    over the whole window rather than the trailing fortnight, so it catches
    a day the restate window never re-read and an advertiser a pull failed
    on -- and not the feed itself being wrong. A report Amazon is still
    preparing inside the budget is *not measured* tonight rather than the
    scheduler thread being held for it."""
    from . import amazon_dsp
    st = amazon_dsp.amazon_status()
    if not st["configured"]:
        return {"measured": False, "reason": amazon_dsp.not_configured_line()}
    if not st["connected"]:
        return {"measured": False, "reason": amazon_dsp.NOT_CONNECTED}
    try:
        got = amazon_dsp.month_total(start, end)
    except (amazon_dsp.amz.AmazonAuthError, amazon_dsp.amz.AmazonApiError) as exc:
        return {"measured": False, "reason": amazon_dsp.amz._redact(exc)}
    rows = got["rows"]
    return {"measured": True,
            "spend": _q(sum((Decimal(str(r.get("spend") or 0)) for r in rows), Decimal(0))),
            "impressions": sum(int(r.get("impressions") or 0) for r in rows),
            "clicks": sum(int(r.get("clicks") or 0) for r in rows),
            "independent": False,
            "label": (f"Amazon's month fetched again across {got['advertisers']} advertiser"
                      f"{'s' if got['advertisers'] != 1 else ''} ({len(rows)} order-days)")}


def theirs(platform: str, start: date, end: date) -> dict:
    """The platform's own figure for the window, or not measured with the
    reason. Google's customer query first where it is connected -- the one
    independent source -- and the provider table where it is not; a
    platform with neither says which it lacks."""
    if platform in NOT_MEASURABLE:
        return {"measured": False, "reason": NOT_MEASURABLE[platform]}
    readers = []
    if platform == "google":
        readers.append(google_total)
    if platform == "stackadapt":
        readers.append(stackadapt_total)
    if platform == "bing":
        readers.append(bing_total)
    if platform == "amazon_dsp":
        readers.append(amazon_dsp_total)
    readers.append(lambda a, b: provider_total(platform, a, b))
    reasons = []
    for reader in readers:
        try:
            got = reader(start, end)
        except Exception as exc:                        # noqa: BLE001 - a reader, not the run
            got = {"measured": False, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}
        if got.get("measured"):
            return got
        reasons.append(got.get("reason") or "unknown")
    return {"measured": False, "reason": "; ".join(reasons)}


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------

def compare(ours: dict, their: dict) -> dict:
    """State and drift from the two totals. Drift is against the platform's
    figure; when the platform says nothing ran and we hold rows, that is a
    whole-figure drift rather than a division by zero."""
    if not their.get("measured"):
        return {"state": "not_measured", "drift_pct": None}
    o, t = _q(ours["spend"]), _q(their["spend"])
    if t == 0 and o == 0:
        return {"state": "agree", "drift_pct": Decimal("0.00")}
    base = t if t else o
    drift = (abs(o - t) / base * 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return {"state": "agree" if drift <= TOLERANCE_PCT else "drift", "drift_pct": drift}


def _held_in(platform: str, start: date, end: date) -> int:
    try:
        from . import quarantine
        return sum(1 for h in quarantine.held(limit=5000)
                   if h["platform"] == platform and h["date"] and start.isoformat() <= h["date"] <= end.isoformat())
    except Exception:                                   # noqa: BLE001
        return 0


def reconcile_platform(platform: str, month: str | None = None, today: date | None = None) -> dict:
    w = month_window(month, today)
    start, through = w["start"], w["through"]
    row = {"platform": platform, "label": store.platform_label(platform), "month": w["key"],
           "through": through, "held": _held_in(platform, start, through)}
    if through < start:
        row.update({"state": "not_measured", "reason": "the month has no completed day yet",
                    "ours": None, "theirs": None, "drift_pct": None, "independent": None,
                    "source_label": "", "ours_impressions": None, "theirs_impressions": None})
        return row
    ours = store.month_totals(platform, start, through)
    their = theirs(platform, start, through)
    verdict = compare(ours, their)
    row.update({
        "state": verdict["state"], "drift_pct": verdict["drift_pct"],
        "ours": _q(ours["spend"]), "ours_impressions": ours["impressions"],
        "theirs": _q(their["spend"]) if their.get("measured") else None,
        "theirs_impressions": their.get("impressions") if their.get("measured") else None,
        "independent": their.get("independent") if their.get("measured") else None,
        "source_label": their.get("label", "") if their.get("measured") else "",
        "reason": (their.get("reason", "") if not their.get("measured") else their.get("note", "")),
    })
    return row


def _previous_states() -> dict[tuple, str]:
    return {(r["platform"], r["month"]): r["state"] for r in store.reconcile_rows(months=12)}


def run(months: tuple[str, ...] | None = None, today: date | None = None,
        actor: str = "scheduler") -> dict:
    """Every platform, for this month and the last (a month closes and
    restates for weeks). Writes the ledger and logs a state CHANGE per
    platform-month, never a state."""
    today = today or date.today()
    if not months:
        this = today.replace(day=1)
        last = (this - timedelta(days=1)).replace(day=1)
        months = (f"{this:%Y-%m}", f"{last:%Y-%m}")
    before = _previous_states()
    out = {"months": list(months), "rows": [], "agree": 0, "drift": 0, "not_measured": 0,
           "changed": []}
    try:
        from hub import audit as hub_audit
    except Exception:                                   # noqa: BLE001 - standalone
        hub_audit = None
    for month in months:
        for platform in store.PLATFORMS:
            try:
                row = reconcile_platform(platform, month, today)
            except Exception as exc:                    # noqa: BLE001 - one platform, not the run
                log.exception("reports: reconcile %s %s failed", platform, month)
                row = {"platform": platform, "label": store.platform_label(platform), "month": month,
                       "state": "not_measured", "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
                       "ours": None, "theirs": None, "drift_pct": None, "independent": None,
                       "source_label": "", "held": 0, "through": None,
                       "ours_impressions": None, "theirs_impressions": None}
            try:
                store.write_reconcile(row)
            except Exception as exc:                    # noqa: BLE001
                log.warning("reports: reconcile ledger write failed: %s", exc)
            out[row["state"]] += 1
            out["rows"].append(row)
            prior = before.get((platform, month))
            if prior is not None and prior != row["state"] and hub_audit is not None:
                out["changed"].append((platform, month, prior, row["state"]))
                try:
                    hub_audit.log("reports", "reports_reconcile", actor=actor, action="reports_reconcile",
                                  platform=platform, month=month, state=row["state"],
                                  detail=(f"{row['label']} {month}: {STATE_LABELS[prior]} -> "
                                          f"{STATE_LABELS[row['state']]}"
                                          + (f" (ours ${row['ours']:,.2f}, theirs ${row['theirs']:,.2f}, "
                                             f"{row['drift_pct']}% apart)" if row["state"] == "drift" else "")))
                except Exception:                       # noqa: BLE001 - a log line is not the run
                    pass
    return out


def drifting() -> list[dict]:
    """The platform-months in drift as the ledger last measured them --
    what /status and the dashboard name."""
    return [r for r in store.reconcile_rows(months=2) if r["state"] == "drift"]
