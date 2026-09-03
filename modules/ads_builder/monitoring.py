"""Unattended monitoring of the live accounts this Hub deployed.

`optimization.scan_account()` already did the hard part. What it could not do
is run without somebody opening /tools/ads/optimization and pressing Scan — so
an account that started burning money on a Tuesday was found on whatever day
a rep next happened to look at it, and "3 accounts need attention" was not a
sentence anything in the Hub could say.

Three rules hold this up, and each is a way an unattended sweep goes wrong.

**A failed scan is a row, not a silence.** `record_optimization_run()` writes
whether or not Google answered, so "we looked and the account is clean" and
"we could not look" are different answers on the panel. One account's failure
never stops the loop — the isolation `job_smartforecast_weather` already
works to.

**It paces itself against the daily operation budget.** Google's Basic access
allows 15,000 operations a day and a cap reached mid-afternoon stops a rep's
own campaign deploy until midnight Pacific. `quotas.ads_headroom()` is read
**once** before the loop — it is a full scan of the activity log — and the
remaining allowance is then decremented locally by what each account costs.
Accounts that do not fit are **skipped and counted**, not failed: a sweep that
spends the last of the day's budget to produce a finding nobody is waiting on
is worse than a sweep that stops.

**A scan spends quota whether or not it answers.** The allowance is decremented
for a failed account too, or one broken account rate-limiting every query
would look free and the run would walk straight through the margin it was
checking.
"""
from __future__ import annotations

import logging

from . import optimization, store

log = logging.getLogger(__name__)

DEFAULT_DATE_RANGE = "LAST_30_DAYS"

# Bounded on both axes, because these are outbound calls sharing one scheduler
# thread. Whatever is left is due again on the next tick.
MAX_ACCOUNTS_PER_RUN = 40


def _headroom():
    """Today's Google Ads allowance, or None when nothing publishes a ceiling.

    None is *not measured*, never zero: refusing to scan on the strength of a
    number nobody stated would silence the whole feature, and Google's own
    refusal is the backstop. The state is reported either way.
    """
    try:
        from hub import quotas
    except Exception:                                    # noqa: BLE001
        return None, {"measured": False, "note": "hub.quotas is not importable here."}
    try:
        head = quotas.ads_headroom()
    except Exception as exc:                             # noqa: BLE001
        return None, {"measured": False, "note": f"could not be read ({type(exc).__name__})"}
    return (head.get("remaining") if head.get("measured") else None), head


def _cost_per_account() -> int:
    try:
        from hub import quotas
        return int(quotas.ADS_QUERIES_PER_SCAN)
    except Exception:                                    # noqa: BLE001
        return 6


def sweep(actor: str = "scheduler", date_range: str = DEFAULT_DATE_RANGE,
          triggered: str = "scheduled", limit: int = MAX_ACCOUNTS_PER_RUN) -> dict:
    """Scan every live account once, persist each result, report the run."""
    accounts = store.deployed_accounts()[:max(1, int(limit))]
    if not accounts:
        # A state, not a failure. An unconfigured Hub would otherwise write an
        # identical activity row twice a day for ever -- the noise
        # hub/google_index.py had to learn to stop making.
        return {"ok": True, "accounts": 0, "scanned": 0, "failed": 0,
                "skipped": "no deployed proposal carries a Google customer id"}

    allowance, headroom = _headroom()
    per_account = _cost_per_account()
    scanned = failed = quota_skipped = high = 0
    failures: list[dict] = []

    for account in accounts:
        cid, client_name = account["customer_id"], account["client_name"]
        if allowance is not None and allowance < per_account:
            quota_skipped += 1
            continue
        result, error = {}, ""
        try:
            result = optimization.scan_account(cid, date_range, store)
        except Exception as exc:                         # noqa: BLE001
            # One account's Google failure must not cost the rest of the book
            # its scan. The reason is kept on the row rather than swallowed.
            error = f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"[:2000]
            failures.append({"customer_id": cid, "error": type(exc).__name__})
        # Spent either way: a refused query counts against the daily quota
        # exactly as an answered one does.
        if allowance is not None:
            allowance = max(0, allowance - per_account)

        row = store.record_optimization_run(
            cid, client_name=client_name, date_range=date_range,
            result=result, error=error, triggered=triggered,
        )
        if error:
            failed += 1
        else:
            scanned += 1
            high += row["high_severity_count"]

        # On the client's own record, not only in this module's table: a rep
        # reading a client 360 page should see that we looked and what we
        # found, without opening the Ads tool at all.
        store.log_event(
            "OPTIMIZATION_SCHEDULED_SCAN", actor,
            client=client_name, customer_id=cid, date_range=date_range,
            items=row["item_count"], high_severity=row["high_severity_count"],
            triggered=triggered, error=error,
        )

    return {
        "ok": True, "accounts": len(accounts), "scanned": scanned,
        "failed": failed, "high_severity": high,
        "quota_skipped": quota_skipped,
        "quota": {k: headroom.get(k) for k in
                  ("measured", "used_today", "daily_quota", "remaining", "note")},
        "failures": failures[:10],
    }


def account_panel(limit: int = 100) -> dict:
    """What the optimization page says before anybody presses Scan.

    Every live account, each carrying its last automatic scan — or saying it
    has never had one, which is a different thing from an account with nothing
    wrong with it.
    """
    runs = {r["customer_id"]: r for r in store.latest_optimization_runs(limit=limit)}
    rows = []
    for account in store.deployed_accounts(limit=limit * 5)[:limit]:
        rows.append({**account, "last_run": runs.get(account["customer_id"])})
    return {
        "accounts": rows,
        "measured": True,
        "note": ("Automatic scans run on the Hub scheduler. An account with no "
                 "last scan has not been swept yet — which is not the same as "
                 "an account with nothing to act on."),
    }
