"""Web ticket audit reports.

Each report returns the same envelope so the /qa page can render them all with
one template: key, title, why it matters, columns, rows, and a severity per row
(flag / watch / info).
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from . import config, service

GROUP = "Web Tickets"


def _money(value):
    return f"${value:,.0f}" if value else "—"


def _envelope(key, title, blurb, columns, rows, empty):
    return {
        "key": key,
        "group": GROUP,
        "title": title,
        "blurb": blurb,
        "columns": columns,
        "rows": rows,
        "count": len(rows),
        "flags": len([r for r in rows if r.get("severity") == "flag"]),
        "empty": empty,
    }


# --------------------------------------------------------------------------
def load_vs_plan(data):
    """Who is consuming more support than their plan pays for."""
    rows = []
    for r in data["rows"]:
        if not r["matched"] or r["window_total"] == 0:
            continue
        if r["load_state"] not in ("over", "watch"):
            continue
        rows.append({
            "severity": "flag" if r["load_state"] == "over" else "watch",
            "key": r["key"],
            "cells": [
                r["name"],
                _money(r["monthly_price"]),
                f'{r["per_month"]}/mo',
                str(r["allowance"]),
                f'{r["hours_window"]:.1f}h' + ("*" if r["hours_estimated"] else ""),
                _money(r["cost_to_serve"]),
                f'{r["pct_of_revenue"]}%' if r["pct_of_revenue"] is not None else "—",
                r["top_type"] or "—",
            ],
        })
    rows.sort(key=lambda x: -float(x["cells"][6].rstrip("%") or 0))
    return _envelope(
        "tickets_load_vs_plan",
        "Ticket load vs. plan",
        f'Cost to serve over the last {data["window_days"]} days against monthly '
        f'revenue, at ${config.INTERNAL_HOURLY_RATE:,.0f}/hr. Flagged at '
        f'{config.LOAD_FLAG_PCT:.0f}% of revenue, watched at {config.LOAD_WARN_PCT:.0f}%. '
        'An asterisk means the tickets carried no time, so hours are estimated at '
        f'{config.ASSUMED_HOURS_PER_TICKET}h each.',
        ["Client", "Monthly", "Tickets", "Plan absorbs",
         "Hours", "Cost to serve", "% of revenue", "Most common"],
        rows,
        "No client is running hot against their plan.",
    )


def sla_breaches(data):
    """Open tickets past the SLA, oldest first."""
    rows = []
    for t in data["tickets"]:
        if t["state"] != "open" or t["blocked"]:
            continue
        age = t["age_days"] or 0
        if age <= config.SLA_WARN_DAYS:
            continue
        rows.append({
            "severity": "flag" if age > config.SLA_DAYS else "watch",
            "key": t["client_key"],
            "sort": age,
            "cells": [
                t["client_name"] or "(no client)",
                f'#{t["number"]}',
                t["summary"][:70] or t["type"],
                t["status"],
                t["assignee"],
                f"{age:.0f} days",
            ],
        })
    rows.sort(key=lambda x: -x["sort"])
    return _envelope(
        "tickets_sla_breaches",
        f"Open past {config.SLA_DAYS} days",
        f'Every ticket still open beyond the {config.SLA_DAYS}-day target, plus '
        f'those approaching it at {config.SLA_WARN_DAYS} days. Tickets marked as '
        'waiting on the client are excluded — the clock is not ours.',
        ["Client", "Ticket", "Summary", "Status", "Assigned", "Age"],
        rows,
        "Nothing is past the target.",
    )


def stale_open(data):
    """Open tickets nobody has touched."""
    rows = []
    for t in data["tickets"]:
        if t["state"] != "open":
            continue
        idle = t["idle_days"] or 0
        if idle <= config.STALE_DAYS:
            continue
        rows.append({
            "severity": "flag" if idle > config.STALE_DAYS * 2 else "watch",
            "key": t["client_key"],
            "sort": idle,
            "cells": [
                t["client_name"] or "(no client)",
                f'#{t["number"]}',
                t["summary"][:70] or t["type"],
                t["assignee"],
                f"{idle:.0f} days",
                "Waiting on client" if t["blocked"] else "Ours",
            ],
        })
    rows.sort(key=lambda x: -x["sort"])
    return _envelope(
        "tickets_stale",
        f"No movement in {config.STALE_DAYS}+ days",
        "Open tickets with no update since the date shown. Different from the SLA "
        "report: a ticket can be young and already abandoned, or old and actively "
        "worked.",
        ["Client", "Ticket", "Summary", "Assigned", "Idle", "Whose court"],
        rows,
        "Every open ticket has been touched recently.",
    )


def unmatched_clients(data):
    """Tickets whose client name matches nothing on file."""
    rows = []
    for r in data["rows"]:
        if r["matched"] or r["total"] == 0:
            continue
        rows.append({
            "severity": "watch",
            "key": r["key"],
            "cells": [
                r["name"],
                str(r["total"]),
                str(r["open"]),
                r["last_opened"][:10] if r["last_opened"] else "—",
            ],
        })
    rows.sort(key=lambda x: -int(x["cells"][1]))
    return _envelope(
        "tickets_unmatched",
        "Tickets with no client on file",
        "The ticket's client name matched nothing in the client registry, even "
        "after fuzzy matching. Usually a spelling difference rather than a missing "
        "client — check the name before assuming anything is wrong.",
        ["Name on the ticket", "Tickets", "Open", "Last ticket"],
        rows,
        "Every ticket resolves to a client on file.",
    )


def quiet_clients(data):
    """Paying clients who have not raised a ticket."""
    rows = []
    for r in data["rows"]:
        if not r["matched"] or not r["monthly_price"]:
            continue
        since = r["days_since_last"]
        if since is not None and since < config.QUIET_DAYS:
            continue
        rows.append({
            "severity": "watch" if r["monthly_price"] >= 1000 else "info",
            "key": r["key"],
            "sort": r["monthly_price"],
            "cells": [
                r["name"],
                _money(r["monthly_price"]),
                ", ".join(r["products"][:3]) or "—",
                f"{since:.0f} days ago" if since is not None else "Never",
            ],
        })
    rows.sort(key=lambda x: -x["sort"])
    return _envelope(
        "tickets_quiet",
        f"No tickets in {config.QUIET_DAYS}+ days",
        "Paying clients with nothing logged. Sometimes that means everything is "
        "working. Sometimes it means nobody has spoken to them since onboarding, "
        "which is what a renewal conversation looks like in advance.",
        ["Client", "Monthly", "Products", "Last ticket"],
        rows,
        "Every paying client has raised something recently.",
    )


def repeat_issues(data):
    """The same problem, over and over, for one client."""
    cutoff = datetime.now() - timedelta(days=config.REPEAT_WINDOW_DAYS)
    grouped = defaultdict(list)
    for t in data["tickets"]:
        if t["opened"] and t["opened"] >= cutoff and t["type"]:
            grouped[(t["client_key"], t["client_name"], t["type"])].append(t)

    rows = []
    for (key, name, ttype), tickets in grouped.items():
        if len(tickets) < config.REPEAT_THRESHOLD:
            continue
        hours = sum(t["hours"] or 0 for t in tickets)
        rows.append({
            "severity": "flag",
            "key": key,
            "sort": len(tickets),
            "cells": [
                name or "(no client)",
                ttype,
                str(len(tickets)),
                f"{hours:.1f}h" if hours else "—",
                max(t["opened"] for t in tickets).strftime("%Y-%m-%d"),
            ],
        })
    rows.sort(key=lambda x: -x["sort"])
    return _envelope(
        "tickets_repeat",
        "Same issue, repeatedly",
        f'{config.REPEAT_THRESHOLD} or more tickets of the same type from one '
        f'client inside {config.REPEAT_WINDOW_DAYS} days. Each row is a symptom '
        'being handled instead of a cause being fixed.',
        ["Client", "Request type", "Tickets", "Hours", "Most recent"],
        rows,
        "No client is raising the same issue repeatedly.",
    )


BUILDERS = [load_vs_plan, sla_breaches, stale_open, unmatched_clients,
            quiet_clients, repeat_issues]


def run_all(force=False):
    data = service.build(force=force)
    reports = [builder(data) for builder in BUILDERS]
    return {
        "reports": reports,
        "totals": data["totals"],
        "fetched_at": data["fetched_at"],
        "demo": data["demo"],
        "window_days": data["window_days"],
    }


def run_one(key, force=False):
    data = service.build(force=force)
    for builder in BUILDERS:
        report = builder(data)
        if report["key"] == key:
            return report
    return None


def summary_counts(force=False):
    """One line per report, for the /qa index and the dashboard tile."""
    payload = run_all(force=force)
    return [{"key": r["key"], "title": r["title"], "group": r["group"],
             "count": r["count"], "flags": r["flags"]} for r in payload["reports"]]
