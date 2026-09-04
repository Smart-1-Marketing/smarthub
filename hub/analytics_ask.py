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

A question back, on its own, hands the work of guessing what this thing can
answer to the person who already showed they did not know — "which measure?"
is a fair question and a dead end for anyone who does not know the list. So
the planner also returns `suggestions`: two to four complete questions it
*could* run, phrased the way the person put theirs. The page shows them as
one-click choices beside the question and a box for anyone whose answer is
none of them. Suggestions are a convenience, never a substitute: a clarify
with none still renders, and a suggestion is only ever asked when clicked.
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
  {"clarify": "one short question",
   "suggestions": ["Sessions by channel over the last 30 days",
                   "Key events this month vs last month"]}

  `suggestions` is two to four complete questions you could answer, each a
  plausible reading of what was actually asked. Write them as the person
  would type them, not as report specifications, and make each one different
  from the others in a way that matters — a different measure, a different
  breakdown or a different period. Every suggestion must be answerable with
  the metrics and dimensions listed below. Omit the field only when you truly
  cannot guess at what was meant.

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


# What to offer when the model gave nothing to offer. Deliberately the four
# questions this module is certain to be able to plan, so a suggestion never
# leads to a second refusal.
FALLBACK_SUGGESTIONS = (
    "Sessions by channel over the last 30 days",
    "Top landing pages last month",
    "Key events this month vs last month",
    "Mobile vs desktop sessions over the last 90 days",
)


def _suggestions(raw: Any) -> list:
    """The model's suggested questions, cleaned up.

    Strings only, four at most, de-duplicated case-insensitively and short
    enough to sit on a button. Anything else in the field is dropped rather
    than rendered — a suggestion is a thing somebody is going to click."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())[:120].strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) == 4:
            break
    return out


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
        return {"clarify": str(got["clarify"])[:300],
                "suggestions": _suggestions(got.get("suggestions"))}

    req, err = validate(got)
    if not req:
        # A plan that failed validation is still a question the model
        # understood; the fallbacks below are the four things this module can
        # always answer, so the reader has somewhere to click rather than only
        # a sentence telling them to try again.
        return {"clarify": err + " Try naming the measure you want — sessions, "
                                 "users, conversions, revenue — and a period.",
                "suggestions": _suggestions(got.get("suggestions")) or list(FALLBACK_SUGGESTIONS)}
    return {"request": req,
            "title": str(got.get("title") or question)[:120],
            "explain": str(got.get("explain") or "")[:300]}


def range_index(tag: str, ranges: list) -> int | None:
    """Which of the requested date ranges a GA4 row belongs to, or None.

    This is the whole of the comparison, and it was `tag.endswith("_1")`.

    GA4 values the `dateRange` dimension with the range's **name** where one
    was given, and only falls back to `date_range_0` / `date_range_1` where
    none was — and `_PLAN_SCHEMA_NOTE` *requires* names: "for a comparison,
    give exactly two dateRanges, each with a name". So "July" and "June" both
    tested false, both rows landed in the same bucket, and the second
    overwrote the first.

    What that produced, from real numbers, on the report whose entire purpose
    is the comparison: Dublin at 900 in July against 600 in June rendered as
    **600, with no previous and no change** — the older period's figure shown
    as the current one — and the totals row read **"600, up 100% on 0"**. The
    identical data with unnamed ranges worked perfectly, so the path that
    works is the one the planner is told never to take.
    """
    tag = str(tag or "")
    for i, r in enumerate(ranges):
        if r.get("name") and tag == r["name"]:
            return i
    if tag.startswith("date_range_"):
        try:
            i = int(tag.rsplit("_", 1)[-1])
        except ValueError:
            return None
        return i if 0 <= i < len(ranges) else None
    return None


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
    unaligned = 0
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
            which = range_index(dvals[-1], ranges)
            if which is None:
                # A row we cannot place in a period. Counted rather than
                # quietly folded into the first: a comparison built out of
                # rows that may be from either side is the confident wrong
                # answer this whole function exists to produce correctly.
                unaligned += 1
                which = 0
            dvals = dvals[:len(dim_names)]
        key = tuple(dvals)
        b = buckets.setdefault(key, {"dims": dvals, "a": None, "b": None})
        b["a" if which == 0 else "b"] = mvals

    aligned = comparing and not unaligned
    rows = []
    for b in buckets.values():
        cur = b["a"] or [0.0] * len(met_names)
        prev = b["b"]
        cells = []
        for i, m in enumerate(met_names):
            cell = {"metric": m, "value": cur[i]}
            if aligned and prev is not None:
                was = prev[i]
                cell["previous"] = was
                cell["change_pct"] = (round((cur[i] - was) / was * 100, 1)
                                      if was else (100.0 if cur[i] else 0.0))
            cells.append(cell)
        rows.append({"dims": b["dims"], "cells": cells})

    primary = met_names[0] if met_names else ""
    # Only when nothing was asked for. GA4 honoured the orderBys this module
    # sent it, and re-sorting by the first metric threw that away -- so
    # "sessions by day for July", ordered by date on the way out and ordered
    # by date on the way back, was rendered as a ranking: 2nd, 3rd, 4th, 1st.
    # Every number right, the one thing a time series is for gone.
    if not request.get("orderBys"):
        rows.sort(key=lambda r: -(r["cells"][0]["value"] if r["cells"] else 0))

    # GA4 supplies totals only where metricAggregations was asked for, which
    # this module does not ask for; summing the rows is the fallback and it is
    # the total of the rows that came back, which is not the property's total
    # once `limit` has cut the list off. Said rather than left to be read as
    # the whole -- the SEO gallery's "Showing 1 of 7", one report along.
    ga_totals = _reported_totals(report, met_names)
    limit = int(request.get("limit") or 0)
    capped = bool(limit) and len(rows) >= limit
    totals = []
    for i, m in enumerate(met_names):
        cur = sum(r["cells"][i]["value"] for r in rows)
        t = {"metric": m, "value": ga_totals.get(m, cur)}
        if aligned:
            was = sum(r["cells"][i].get("previous") or 0 for r in rows)
            t["previous"] = was
            t["change_pct"] = (round((cur - was) / was * 100, 1)
                               if was else (100.0 if cur else 0.0))
        totals.append(t)

    return {
        "dimensions": dim_names,
        "metrics": met_names,
        "comparing": comparing,
        "compared": aligned,
        "unaligned_rows": unaligned,
        "range_labels": [label(i) for i in range(len(ranges))],
        "rows": rows[:MAX_LIMIT],
        "totals": totals,
        "totals_of": ("everything measured" if ga_totals else
                      "the rows shown" if capped else "all the rows"),
        "primary": primary,
        "row_count": len(rows),
        "note": ("The two periods could not be told apart in what Google "
                 "returned, so this is not a comparison." if unaligned else ""),
    }


def _reported_totals(report: dict, met_names: list) -> dict:
    """GA4's own totals row, where one was returned. Never invented."""
    out: dict[str, float] = {}
    for row in (report.get("totals") or [])[:1]:
        for i, mv in enumerate(row.get("metricValues", []) or []):
            if i < len(met_names):
                try:
                    out[met_names[i]] = float(mv.get("value") or 0)
                except (TypeError, ValueError):
                    pass
    return out


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
            # `comparing` is what was asked for; `compared` is whether the two
            # periods could actually be told apart in what Google returned.
            # Handing over the first alone invites a sentence about a change
            # nothing computed -- the invented-figure failure hub/audit_summary
            # exists to refuse, one module over.
            "comparing": bool(shaped.get("compared")),
            "ranges": shaped["range_labels"],
            "totals": shaped["totals"],
            # What the totals are the total OF, in the model's own payload:
            # a capped report's sum is the top N, and "sessions were 1,600"
            # about a property that had 50,000 is a figure nobody measured.
            "totals_cover": shaped.get("totals_of", ""),
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
              "`totals_cover` says what the totals are the total of -- where "
              "it is 'the rows shown', say so rather than calling it the "
              "whole. Where `comparing` is false, do not describe a change "
              "between periods. Round sensibly. No preamble."},
             {"role": "user", "content": json.dumps(payload)}],
            module="analytics_ask", purpose="narrate",
            temperature=0.2, max_tokens=260).strip()
    except Exception:                                   # noqa: BLE001
        # The table is the answer; the sentence is a convenience. Losing it
        # must not lose the report.
        return ""
