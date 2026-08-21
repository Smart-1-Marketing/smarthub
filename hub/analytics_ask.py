"""Ask Analytics — a plain-English question turned into a GA4 report.

## Why this replaces what was there

`/api/ga4/ask` matched keywords: "device" in the question meant group by
device, otherwise group by source/medium, always over the last 30 days with
three fixed metrics. "How did conversions do in July versus June?" and "which
cities converted best last quarter?" both returned the same 30-day
source/medium table, which is worse than refusing — it answers confidently
with the wrong report.

## The shape

The model **plans**, it does not fetch. It returns either a clarifying
question or a report plan, and the plan is checked field by field against the
lists below before anything reaches Google. That matters for three reasons:

* a hallucinated metric name is a 400 from GA4 and a dead end for the user,
* the model cannot be talked into requesting a property it was not given —
  the property id comes from the caller, never from the question,
* the failure mode of a bad plan becomes "I don't know how to ask that",
  which is honest, rather than a wrong table.

## Clarifying

A question like "how are we doing?" has no defensible default. Rather than
pick one, the planner may return `clarify` with a single question, and the
page asks it. The previous answer travels back as history so the follow-up is
resolved against the original question rather than starting over.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any

# --------------------------------------------------------------------------
# What the model is allowed to ask for.
#
# Deliberately a curated list rather than the whole GA4 schema. These cover
# what an agency actually reports on, and every one is verified to exist — a
# short list the model uses correctly beats a long one it guesses at.
# --------------------------------------------------------------------------
METRICS = {
    "sessions", "activeUsers", "newUsers", "totalUsers", "screenPageViews",
    "engagedSessions", "engagementRate", "bounceRate", "averageSessionDuration",
    "userEngagementDuration", "eventCount", "keyEvents", "conversions",
    "sessionsPerUser", "screenPageViewsPerSession", "totalRevenue",
    "purchaseRevenue", "transactions", "ecommercePurchases",
    "averagePurchaseRevenue", "itemRevenue",
}

DIMENSIONS = {
    "date", "yearMonth", "month", "year", "week", "dayOfWeek", "hour",
    "sessionSource", "sessionMedium", "sessionSourceMedium", "sessionCampaignName",
    "sessionDefaultChannelGroup", "firstUserDefaultChannelGroup",
    "pagePath", "pageTitle", "landingPage", "fullPageUrl",
    "country", "region", "city",
    "deviceCategory", "browser", "operatingSystem", "platform",
    "eventName", "newVsReturning", "audienceName", "language",
}

MAX_LIMIT = 200

_PLAN_SCHEMA_NOTE = """Return JSON only, in one of these two shapes.

To ask for clarification (use only when the question genuinely cannot be
answered as asked — never to confirm something obvious):
  {"clarify": "one short question"}

To run a report:
  {"title": "short title for the answer",
   "metrics": ["sessions", ...],
   "dimensions": ["date", ...],
   "dateRanges": [{"startDate": "2026-07-01", "endDate": "2026-07-31", "name": "July"}],
   "orderBy": {"metric": "sessions", "desc": true},
   "limit": 25,
   "explain": "one sentence on what this report shows"}

Rules:
- metrics MUST come from: %s
- dimensions MUST come from: %s
- dimensions may be an empty list for a totals-only answer
- dates are YYYY-MM-DD, or GA4 relative forms like 28daysAgo / yesterday / today
- for a comparison, give exactly two dateRanges, each with a name
- prefer few dimensions; one is usually right, two at most
- limit <= %d
"""


def catalogue() -> dict:
    return {"metrics": sorted(METRICS), "dimensions": sorted(DIMENSIONS)}


def _today() -> _dt.date:
    return _dt.datetime.now(_dt.timezone.utc).date()


_REL = re.compile(r"^(today|yesterday|\d{1,4}daysAgo)$")


def _clean_date(v: Any) -> str | None:
    s = str(v or "").strip()
    if _REL.match(s):
        return s
    try:
        return _dt.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def validate(plan: dict) -> tuple[dict | None, str]:
    """Turn a model plan into a GA4 request, or say why it cannot be one.

    Every field is rebuilt from scratch rather than filtered in place — that
    way anything the model added which is not named here simply does not
    survive into the request.
    """
    if not isinstance(plan, dict):
        return None, "The planner returned something that wasn't a plan."

    metrics = [m for m in (plan.get("metrics") or []) if m in METRICS]
    if not metrics:
        bad = [m for m in (plan.get("metrics") or []) if m not in METRICS]
        return None, ("I don't have a measure for that."
                      + (f" (asked for: {', '.join(bad[:3])})" if bad else ""))

    dimensions = [d for d in (plan.get("dimensions") or []) if d in DIMENSIONS][:2]

    ranges = []
    for r in (plan.get("dateRanges") or [])[:2]:
        if not isinstance(r, dict):
            continue
        start, end = _clean_date(r.get("startDate")), _clean_date(r.get("endDate"))
        if not start or not end:
            continue
        item = {"startDate": start, "endDate": end}
        if r.get("name"):
            item["name"] = re.sub(r"[^A-Za-z0-9 _-]", "", str(r["name"]))[:40]
        ranges.append(item)
    if not ranges:
        ranges = [{"startDate": "28daysAgo", "endDate": "yesterday"}]

    try:
        limit = int(plan.get("limit") or 25)
    except (TypeError, ValueError):
        limit = 25
    limit = max(1, min(MAX_LIMIT, limit))

    req: dict = {"metrics": [{"name": m} for m in metrics],
                 "dateRanges": ranges, "limit": limit}
    if dimensions:
        req["dimensions"] = [{"name": d} for d in dimensions]

    ob = plan.get("orderBy") or {}
    metric_name = ob.get("metric") if isinstance(ob, dict) else None
    if metric_name in metrics:
        req["orderBys"] = [{"metric": {"metricName": metric_name},
                            "desc": bool(ob.get("desc", True))}]
    elif dimensions and dimensions[0] in ("date", "yearMonth", "month", "week"):
        req["orderBys"] = [{"dimension": {"dimensionName": dimensions[0]}}]

    return req, ""


def plan(question: str, *, history: list | None = None,
         property_label: str = "") -> dict:
    """Ask the model for a plan. Returns {"clarify": ...} or {"request": ...}."""
    from hub import ai

    today = _today()
    system = (
        "You turn a marketing person's question about a website into a Google "
        "Analytics 4 report plan. You do not answer the question yourself and "
        "you do not invent numbers.\n\n"
        f"Today is {today.isoformat()}. The current month began "
        f"{today.replace(day=1).isoformat()}.\n"
        f"The property is {property_label or 'a client website'}.\n\n"
        + _PLAN_SCHEMA_NOTE % (", ".join(sorted(METRICS)),
                               ", ".join(sorted(DIMENSIONS)), MAX_LIMIT)
    )
    messages = [{"role": "system", "content": system}]
    for turn in (history or [])[-6:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": str(turn.get("text") or "")[:800]})
    messages.append({"role": "user", "content": str(question or "")[:800]})

    got = ai.chat_json(messages, module="analytics_ask", purpose="plan",
                       temperature=0.1, max_tokens=700)

    if got.get("clarify"):
        return {"clarify": str(got["clarify"])[:300]}

    req, err = validate(got)
    if not req:
        return {"clarify": err + " Try naming the measure you want — sessions, "
                                 "users, conversions, revenue — and a period."}
    return {"request": req,
            "title": str(got.get("title") or question)[:120],
            "explain": str(got.get("explain") or "")[:300]}


def shape(report: dict, request: dict) -> dict:
    """GA4's response as columns and rows, with comparisons worked out.

    GA4 returns a comparison as extra rows carrying a dateRange dimension, not
    as extra columns, so a naive render shows every row twice. They are folded
    together here and the change is computed once, rather than each caller
    deciding what "vs" means.
    """
    dim_names = [d["name"] for d in request.get("dimensions", [])]
    met_names = [m["name"] for m in request.get("metrics", [])]
    ranges = request.get("dateRanges", [])
    comparing = len(ranges) > 1

    def label(i):
        return ranges[i].get("name") or f"{ranges[i]['startDate']}–{ranges[i]['endDate']}"

    buckets: dict[tuple, dict] = {}
    for row in report.get("rows", []) or []:
        dvals = [d.get("value", "") for d in row.get("dimensionValues", [])]
        mvals = []
        for mv in row.get("metricValues", []):
            try:
                mvals.append(float(mv.get("value") or 0))
            except (TypeError, ValueError):
                mvals.append(0.0)
        which = 0
        if comparing and dvals:
            # GA4 appends the range as the last dimension value.
            tag = dvals[-1]
            which = 1 if tag.endswith("_1") else 0
            dvals = dvals[:len(dim_names)]
        key = tuple(dvals)
        b = buckets.setdefault(key, {"dims": dvals, "a": None, "b": None})
        b["a" if which == 0 else "b"] = mvals

    rows = []
    for b in buckets.values():
        cur = b["a"] or [0.0] * len(met_names)
        prev = b["b"]
        cells = []
        for i, m in enumerate(met_names):
            cell = {"metric": m, "value": cur[i]}
            if comparing and prev is not None:
                was = prev[i]
                cell["previous"] = was
                cell["change_pct"] = (round((cur[i] - was) / was * 100, 1)
                                      if was else (100.0 if cur[i] else 0.0))
            cells.append(cell)
        rows.append({"dims": b["dims"], "cells": cells})

    primary = met_names[0] if met_names else ""
    rows.sort(key=lambda r: -(r["cells"][0]["value"] if r["cells"] else 0))

    totals = []
    for i, m in enumerate(met_names):
        cur = sum(r["cells"][i]["value"] for r in rows)
        t = {"metric": m, "value": cur}
        if comparing:
            was = sum(r["cells"][i].get("previous") or 0 for r in rows)
            t["previous"] = was
            t["change_pct"] = (round((cur - was) / was * 100, 1)
                               if was else (100.0 if cur else 0.0))
        totals.append(t)

    return {
        "dimensions": dim_names,
        "metrics": met_names,
        "comparing": comparing,
        "range_labels": [label(i) for i in range(len(ranges))],
        "rows": rows[:MAX_LIMIT],
        "totals": totals,
        "primary": primary,
        "row_count": len(rows),
    }


def narrate(question: str, title: str, shaped: dict) -> str:
    """A short plain-English reading of the table.

    Given only the shaped numbers, never the raw response, so it cannot
    introduce a figure the table does not show.
    """
    from hub import ai
    try:
        payload = {
            "question": question, "title": title,
            "dimensions": shaped["dimensions"], "metrics": shaped["metrics"],
            "comparing": shaped["comparing"], "ranges": shaped["range_labels"],
            "totals": shaped["totals"],
            "top_rows": [{"dims": r["dims"],
                          "cells": [{k: c.get(k) for k in
                                     ("metric", "value", "previous", "change_pct")}
                                    for c in r["cells"]]}
                         for r in shaped["rows"][:8]],
        }
        return ai.chat(
            [{"role": "system", "content":
              "You explain a Google Analytics result to a marketing account "
              "manager in two or three sentences. Use only the numbers given. "
              "Never invent a figure, a cause, or a recommendation that the "
              "data does not support. If a change is large, say so plainly. "
              "Round sensibly. No preamble."},
             {"role": "user", "content": json.dumps(payload)}],
            module="analytics_ask", purpose="narrate",
            temperature=0.2, max_tokens=260).strip()
    except Exception:                                   # noqa: BLE001
        # The table is the answer; the sentence is a convenience. Losing it
        # must not lose the report.
        return ""
