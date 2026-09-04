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

from . import optimization, performance_report, store

log = logging.getLogger(__name__)

DEFAULT_DATE_RANGE = "LAST_30_DAYS"

# The module's own mount, so a client-facing link is built once here rather
# than in each caller.
MOUNT = "/tools/ads"

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


def _auto_appliable(result: dict, categories) -> list[dict]:
    """The findings unattended work is allowed to act on, in the order it will.

    Three gates, and each is doing its own job. The **category** is what a rep
    switched on. The **action** is what actually reaches Google, checked
    separately because a detector added under an allowed heading tomorrow must
    not become an unattended write by inheriting it. And **high severity
    only**: the medium and low findings are the ones worth a human reading,
    and acting on those is what turns an assistant into something nobody
    trusts.

    Costliest first, so a run that hits the cap has stopped the most expensive
    waste rather than an arbitrary ten items.
    """
    allowed = set(categories or [])
    if not allowed:
        return []
    picked = [item for item in (result.get("items") or [])
              if item.get("category") in allowed
              and item.get("action") in store.AUTO_APPLY_ACTIONS
              and item.get("severity") == "high"]
    picked.sort(key=lambda i: float((i.get("data") or {}).get("cost") or 0), reverse=True)
    return picked[:store.AUTO_APPLY_MAX_PER_RUN]


def _auto_apply(account: dict, result: dict, actor: str, allowance) -> tuple[dict, object]:
    """Apply what this account has opted into, one item at a time.

    Nobody clicked anything, so the activity row is the only account of what
    changed -- it carries the finding, the reason the detector gave and the
    exact mutate, because a rep reading a client record has to be able to see
    what happened and reverse it by hand. Every apply is its own row: one row
    covering ten mutates is a record nobody can act on.
    """
    cid = account["customer_id"]
    settings = store.auto_apply_settings(cid)
    if not settings["enabled"]:
        return {"enabled": False, "applied": 0, "failed": 0, "considered": 0}, allowance
    candidates = _auto_appliable(result, settings["categories"])
    applied, failed, skipped = 0, 0, 0
    for item in candidates:
        if allowance is not None and allowance < 1:
            skipped += 1
            continue
        action = item["action"]
        payload = dict(item.get("data") or {})
        payload["confirmation"] = optimization.ACTION_CONFIRMATIONS[action]
        error = ""
        try:
            outcome = optimization.apply_action(cid, action, payload, store)
            detail = outcome.get("detail") or {}
            applied += 1
        except Exception as exc:                         # noqa: BLE001
            detail, error = {}, f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"[:500]
            failed += 1
        if allowance is not None:
            allowance = max(0, allowance - 1)
        store.log_event(
            "OPTIMIZATION_AUTO_APPLIED", actor,
            client=account["client_name"], customer_id=cid,
            optimization_action=action, finding=item.get("title", ""),
            why=item.get("why", ""), category=item.get("category", ""),
            item_id=item.get("id", ""), error=error, **detail,
        )
    return ({"enabled": True, "applied": applied, "failed": failed,
             "considered": len(candidates), "quota_skipped": skipped},
            allowance)


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
    auto_applied = auto_failed = auto_accounts = 0
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
            # Only on a scan that answered: acting on an empty result set is
            # acting on the absence of a reading rather than on a finding.
            auto, allowance = _auto_apply(account, result, actor, allowance)
            auto_applied += auto["applied"]
            auto_failed += auto["failed"]
            if auto["enabled"]:
                auto_accounts += 1

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
        "auto_apply": {"accounts": auto_accounts, "applied": auto_applied,
                       "failed": auto_failed},
        "quota": {k: headroom.get(k) for k in
                  ("measured", "used_today", "daily_quota", "remaining", "note")},
        "failures": failures[:10],
    }


def _public_base() -> str:
    """The origin a client's report link is built from.

    config.public_base_origin() and never request.url_root: this runs on the
    scheduler with no request at all, and that helper is the one reading of
    what the Hub's own origin is -- the rule hub/oauth_redirects.py had to
    undo when a panel printed one string and the code sent another.
    """
    try:
        from hub.config import public_base_origin
        return (public_base_origin() or "").rstrip("/")
    except Exception:                                    # noqa: BLE001
        return ""


def send_report(account: dict, *, cadence: str = "", recipient: str = "",
                actor: str = "scheduler") -> dict:
    """Build one client's report, store it, and hand the link to hub/leads.py.

    It stops at `capture_and_deliver`. There is no mail sender in this Hub, so
    what this does is put the link on the client's Smart 1 Suite contact under
    the two custom fields hub/ghl_contacts.py already carries; the email itself
    is a Suite workflow, built separately. Claiming to have emailed anybody
    from here would be the confident wrong answer this codebase keeps undoing.

    The contact is matched by email inside the location -- GoHighLevel's
    /contacts/upsert matches on email or phone, not on a stored id -- so a
    correct address attaches to the client's EXISTING contact and a wrong one
    creates a duplicate. That risk is real, which is why a schedule cannot be
    switched on without an address and why the panel says whose it is.
    """
    cid, client_name = account["customer_id"], account.get("client_name") or ""
    try:
        result = performance_report.report(cid, client_name=client_name, store=store)
    except Exception as exc:                             # noqa: BLE001
        return {"ok": False, "customer_id": cid,
                "error": f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"[:300]}
    if not result.get("measured"):
        # Never sent: a client reading a month of zeros because Google refused
        # is worse than a month with no report in it, and it cannot be undone
        # once it is in their inbox.
        return {"ok": False, "customer_id": cid, "skipped": "not measured",
                "error": (result.get("errors") or {}).get("campaigns", "")}

    row = store.create_performance_report(
        cid, client_name=client_name, proposal_id=account.get("proposal_id") or "",
        report=result, cadence=cadence, recipient=recipient,
        period_label=result.get("period_label") or "")
    base = _public_base()
    report_url = f"{base}{MOUNT}/r/{row['token']}" if base else ""
    pdf_url = f"{report_url}.pdf" if report_url else ""

    delivered, note = False, ""
    if recipient:
        try:
            from hub import leads as hub_leads
            answer = hub_leads.capture_and_deliver(
                source="ads_reports", page=(client_name or cid)[:120],
                fields={"email": recipient, "company": client_name,
                        "name": client_name},
                pdf_url=pdf_url, client=client_name,
                meta={"report_url": report_url, "customer_id": cid,
                      "cadence": cadence})
            delivered = bool(answer.get("delivered"))
            note = str(answer.get("note") or "")[:300]
        except Exception as exc:                         # noqa: BLE001
            note = f"{type(exc).__name__}: {exc}"[:300]
    else:
        # A schedule with no address is a report nobody receives, and saying so
        # is the answer -- not a silent success.
        note = "No recipient is recorded for this account, so nothing was sent."
    store.note_report_delivered(row["token"], delivered=delivered, note=note)
    store.mark_report_sent(cid)

    store.log_event(
        "PERFORMANCE_REPORT", actor, client=client_name, customer_id=cid,
        cadence=cadence, delivered=delivered, recipient=bool(recipient),
        spend=result["totals"]["cost"], conversions=result["totals"]["conversions"],
        report=report_url, detail=note,
    )
    return {"ok": True, "customer_id": cid, "client_name": client_name,
            "token": row["token"], "report_url": report_url, "pdf_url": pdf_url,
            "delivered": delivered, "note": note}


def report_sweep(actor: str = "scheduler", limit: int = MAX_ACCOUNTS_PER_RUN) -> dict:
    """Send every recurring report that is due.

    Per account, isolated: one client's Google failure must not cost the rest
    of the book their report. The same daily operation allowance the scans
    spend from, read once and decremented locally.
    """
    due = store.due_report_accounts()[:max(1, int(limit))]
    if not due:
        return {"ok": True, "due": 0, "sent": 0,
                "skipped": "no account has a recurring report switched on"}
    known = {a["customer_id"]: a for a in store.deployed_accounts(limit=500)}
    allowance, headroom = _headroom()
    per_account = _cost_per_account()
    sent = failed = quota_skipped = 0
    failures = []
    for schedule in due:
        cid = schedule["customer_id"]
        if allowance is not None and allowance < per_account:
            quota_skipped += 1
            continue
        account = known.get(cid) or {"customer_id": cid, "client_name": "",
                                     "proposal_id": ""}
        outcome = send_report(account, cadence=schedule["cadence"],
                              recipient=schedule["recipient"], actor=actor)
        if allowance is not None:
            allowance = max(0, allowance - per_account)
        if outcome.get("ok"):
            sent += 1
        else:
            failed += 1
            failures.append({"customer_id": cid,
                             "error": outcome.get("error") or outcome.get("skipped") or ""})
    return {"ok": True, "due": len(due), "sent": sent, "failed": failed,
            "quota_skipped": quota_skipped,
            "quota": {k: headroom.get(k) for k in ("measured", "remaining")},
            "failures": failures[:10]}


def account_panel(limit: int = 100) -> dict:
    """What the optimization page says before anybody presses Scan.

    Every live account, each carrying its last automatic scan — or saying it
    has never had one, which is a different thing from an account with nothing
    wrong with it.
    """
    runs = {r["customer_id"]: r for r in store.latest_optimization_runs(limit=limit)}
    rows = []
    for account in store.deployed_accounts(limit=limit * 5)[:limit]:
        rows.append({**account,
                     "last_run": runs.get(account["customer_id"]),
                     "auto_apply": store.auto_apply_settings(account["customer_id"]),
                     "report": store.report_schedule(account["customer_id"])})
    return {
        "accounts": rows,
        "measured": True,
        # Served rather than restated in the page: a screen offering a category
        # the write refuses is a control that reports a clean save and changes
        # nothing.
        "auto_apply_categories": list(store.AUTO_APPLY_CATEGORIES),
        "auto_apply_cap": store.AUTO_APPLY_MAX_PER_RUN,
        "report_cadences": sorted(store.REPORT_CADENCES),
        "note": ("Automatic scans run on the Hub scheduler. An account with no "
                 "last scan has not been swept yet — which is not the same as "
                 "an account with nothing to act on. Auto-apply is off for "
                 "every account until somebody turns it on."),
    }
