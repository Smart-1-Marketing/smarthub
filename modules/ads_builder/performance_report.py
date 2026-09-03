"""What a client's Google Ads campaign actually did last month.

`optimization.py` answers "what is wrong with this account" for a rep.
This answers "what did my money buy" for the client, and it is a different
document: no findings, no drafts, no mutate buttons — spend, clicks,
conversions, cost per conversion, the campaigns behind them, and how the month
compares with the one before.

Section builders and one `report()` entry point, the shape
`modules/scans/reports.py` uses at a much larger scale. What is deliberately
NOT reused from that file is its content: its "ads" report is a paid-search
READINESS section inferred from a crawl of the client's own website, which is
a different question about a different source. This is live data from an
account that is running.

Three rules, and each is a way a client-facing number goes quietly wrong.

**The queries are `optimization.py`'s.** The same summary and campaign GAQL
that the scanner already sends, so a report and a scan taken minutes apart
cannot quote different spend for one month. A second copy of that query is how
two screens come to disagree about a client's own money.

**A period that could not be read is not a period of zero.** A comparison
against a baseline nothing measured prints "up 100%" over a month nobody
looked at — the failure `hub/ghl_forms.py` had to undo. `previous` is `None`
when the earlier window refused, and every delta beside it is `None` too.

**Nothing here writes.** It reads six GAQL rows and renders them. The
recurring send is `hub/scheduler.py`'s and the delivery is `hub/leads.py`'s;
this file has no idea either exists.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import google_ads, optimization

# The current window is Google's own named range -- the same one the scanner
# asks for, so a report and a scan taken minutes apart cannot quote different
# spend for one month.
THIS_PERIOD = "LAST_30_DAYS"

# Google publishes no "previous 30 days" enum, so the earlier window is a date
# segment computed against the account's own reporting clock. Written out here
# rather than inline, because a report that silently compares two overlapping
# windows reads as a client whose spend doubled.
PREVIOUS_QUERY = """
    SELECT campaign.id, campaign.name, metrics.cost_micros, metrics.clicks,
           metrics.impressions, metrics.conversions
    FROM campaign
    WHERE campaign.status != 'REMOVED'
      AND segments.date BETWEEN '{start}' AND '{end}'
"""

CAVEAT = ("Figures are Google Ads' own, for the account and window named above. "
          "Conversions count what the account's conversion tracking records, "
          "which is not always every inquiry a campaign produced.")


def _period_dates(days_back: int, length: int = 30) -> tuple[str, str]:
    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    end = today - timedelta(days=days_back)
    return (end - timedelta(days=length - 1)).isoformat(), end.isoformat()


def _totals(campaigns: list[dict]) -> dict:
    cost = sum(c["cost"] for c in campaigns)
    clicks = sum(c["clicks"] for c in campaigns)
    conversions = sum(c["conversions"] for c in campaigns)
    impressions = sum(c["impressions"] for c in campaigns)
    return {
        "cost": round(cost, 2), "clicks": clicks, "impressions": impressions,
        "conversions": round(conversions, 2),
        "avg_cpc": round(cost / clicks, 2) if clicks else None,
        "cost_per_conversion": round(cost / conversions, 2) if conversions else None,
        "ctr": round(100 * clicks / impressions, 2) if impressions else None,
        "campaigns": len(campaigns),
    }


def _delta(now, before):
    """Percentage change, or None where either end was not measured.

    None rather than zero on both sides. A baseline nobody could read printed
    as 0 makes every figure beside it "up 100%", with an arrow, over a month
    that was never counted.
    """
    if now is None or before is None:
        return None
    if not before:
        # A change from zero has no percentage. "First month of spend" is a
        # real and useful thing to say, and "up 100%" is not it.
        return None
    return round(100 * (now - before) / before, 1)


def _previous_rows(customer_id: str, store=None) -> tuple[list[dict], str]:
    start, end = _period_dates(days_back=30)
    try:
        rows = google_ads.search(
            customer_id, PREVIOUS_QUERY.format(start=start, end=end), store=store)
    except Exception as exc:                             # noqa: BLE001
        return [], f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"[:300]
    return optimization._campaign_rows(rows), ""


def section_headline(current: dict, previous: dict | None) -> dict:
    """Spend, clicks, conversions and cost per conversion, with the change."""
    before = previous or {}
    rows = []
    for key, label, prefix in (
            ("cost", "Spend", "$"), ("clicks", "Clicks", ""),
            ("conversions", "Conversions", ""),
            ("cost_per_conversion", "Cost per conversion", "$")):
        rows.append({
            "key": key, "label": label, "prefix": prefix,
            "value": current.get(key),
            "previous": before.get(key) if previous is not None else None,
            "change_percent": (_delta(current.get(key), before.get(key))
                               if previous is not None else None),
            # Said per row rather than once at the top: a client reading a dash
            # in one row and a percentage in the next should be able to tell
            # which figure the comparison failed on.
            "compared": previous is not None,
        })
    return {"key": "headline", "title": "The month at a glance", "rows": rows}


def section_campaigns(campaigns: list[dict]) -> dict:
    """Where the money went, biggest spend first."""
    rows = []
    for c in sorted(campaigns, key=lambda x: x["cost"], reverse=True)[:20]:
        rows.append({
            "name": c["name"], "status": c["status"], "cost": round(c["cost"], 2),
            "clicks": c["clicks"], "impressions": c["impressions"],
            "conversions": round(c["conversions"], 2),
            "cost_per_conversion": (round(c["cost"] / c["conversions"], 2)
                                    if c["conversions"] else None),
        })
    return {"key": "campaigns", "title": "Campaigns", "rows": rows,
            "note": ("A campaign with no cost per conversion recorded no "
                     "conversion in this window, which is not the same as "
                     "having produced no inquiries."
                     if any(r["cost_per_conversion"] is None for r in rows) else "")}


def report(customer_id, *, client_name: str = "", store=None,
           date_range: str = THIS_PERIOD) -> dict:
    """One client's month, ready to render.

    Never raises on a partial read: a window that refused is named and the
    rest of the report still renders, the isolation `scan_account()` already
    works to.
    """
    cid = google_ads.digits(customer_id)
    if not cid:
        raise ValueError("customer_id is required.")

    errors = {}
    try:
        rows = google_ads.search(
            cid, optimization.CAMPAIGNS_QUERY.format(date_range=date_range), store=store)
        campaigns = optimization._campaign_rows(rows)
    except Exception as exc:                             # noqa: BLE001
        campaigns = []
        errors["campaigns"] = f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"[:300]

    account_name = ""
    try:
        summary = google_ads.search(cid, optimization.SUMMARY_QUERY, store=store)
        account_name = str(((summary or [{}])[0].get("customer") or {}).get(
            "descriptiveName") or "")
    except Exception as exc:                             # noqa: BLE001
        errors["summary"] = type(exc).__name__

    previous_rows, previous_error = _previous_rows(cid, store=store)
    if previous_error:
        errors["previous"] = previous_error

    current = _totals(campaigns)
    # None rather than an empty total: "the earlier window refused" and "they
    # spent nothing last month" are different answers and only the second is a
    # fact about the client.
    previous = _totals(previous_rows) if not previous_error else None
    start, end = _period_dates(days_back=30)

    return {
        "customer_id": cid,
        "client_name": client_name or account_name,
        "account_name": account_name,
        "title": "Google Ads performance",
        "date_range": date_range,
        "period_label": "the last 30 days",
        "previous_label": f"{start} to {end}",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # False whenever any window refused, so hub/report_cache.py and every
        # screen can tell a thin month from a failed read.
        "measured": not errors.get("campaigns"),
        "compared": previous is not None,
        "totals": current,
        "previous_totals": previous,
        "sections": [section_headline(current, previous),
                     section_campaigns(campaigns)],
        "caveat": CAVEAT,
        "errors": errors,
    }


def filename(r: dict) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-",
                  (r.get("client_name") or r.get("account_name")
                   or r.get("customer_id") or "google-ads")).strip("-")
    month = (r.get("generated_at") or "")[:7] or "report"
    return f"{base}-google-ads-{month}.pdf"[:120]
