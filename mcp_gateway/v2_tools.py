"""Read-only V2 tools layered on top of the stable MCP V1 transport.

This module deliberately contains no write actions.  It reuses SmartHub's
canonical client identity and existing QuickBooks read adapter, but only
returns a narrow, sanitized surface suitable for an external MCP client.
"""
from __future__ import annotations

from typing import Any

from hub import audit, client_key as hub_client_key, clients_registry, quickbooks
from mcp_gateway.metadata import READ_ONLY_TOOL_ANNOTATIONS


MCP_ACTOR = "SmartHub MCP"


def _clean(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def public_identity(identity: dict) -> dict:
    """Return only identity fields that are safe and useful to an MCP client."""
    return {
        "known": bool(identity.get("known")),
        "client": _clean(identity.get("client"), 160),
        "client_key": _clean(identity.get("key"), 220),
        "domain": _clean(identity.get("domain"), 250),
        "matched_on": _clean(identity.get("matched_on"), 40),
        "confidence": _clean(identity.get("confidence"), 40),
        "candidates": [_clean(x, 160) for x in (identity.get("candidates") or [])[:10]],
        "why": _clean(identity.get("why"), 600),
    }


def resolve_identity(name: str = "", url: str = "") -> dict:
    """Resolve using the Hub-wide identity contract; never choose ambiguity."""
    return public_identity(hub_client_key.resolve(
        name=_clean(name, 160),
        url=_clean(url, 500),
        allow_fuzzy=True,
    ))


def _audit(tool: str, identity: dict | None = None, status: str = "ok", **extra: Any) -> None:
    ident = identity or {}
    try:
        audit.log(
            "mcp",
            "tool_called",
            actor=MCP_ACTOR,
            tool=tool,
            client=ident.get("client") or None,
            client_key=ident.get("client_key") or None,
            confidence=ident.get("confidence") or None,
            status=status,
            **{k: v for k, v in extra.items() if v is not None},
        )
    except Exception:
        pass


def _not_found(requested: str, identity: dict) -> dict:
    return {
        "found": False,
        "requested": _clean(requested, 160),
        "identity": identity,
        "message": (
            "Client identity is ambiguous; retry with an exact client name or domain."
            if identity.get("candidates")
            else "No matching SmartHub client was found."
        ),
    }


def search_registry(query: str = "", limit: int = 20) -> dict:
    """Search the canonical registry rather than rebuilding client names locally."""
    limit = max(1, min(int(limit or 20), 50))
    q = hub_client_key.normalise_name(query)
    rows = []
    for row in clients_registry.all_clients():
        name = _clean(row.get("name"), 160)
        if not name:
            continue
        normalized = hub_client_key.normalise_name(name)
        if q and q not in normalized:
            continue
        ident = public_identity(hub_client_key.resolve(
            name=name,
            url=row.get("url") or row.get("domain") or "",
            allow_fuzzy=False,
        ))
        rows.append({
            "client": name,
            "client_key": ident.get("client_key"),
            "domain": ident.get("domain"),
            "confidence": ident.get("confidence"),
            "source": _clean(row.get("source"), 80),
            "live": bool(row.get("live")),
            "is_house": bool(row.get("is_house")),
        })
    rows.sort(key=lambda r: (0 if hub_client_key.normalise_name(r["client"]) == q else 1,
                             r["client"].lower()))
    return {"query": query, "count": min(len(rows), limit), "clients": rows[:limit]}


def quickbooks_status() -> dict:
    """Sanitized QuickBooks health with no token paths, credentials, or realm id."""
    try:
        health = quickbooks.health() or {}
        configured = bool(quickbooks.configured())
        connected = bool(quickbooks.connected())
    except Exception as exc:  # connector failure is a result, not fabricated health
        return {
            "available": False,
            "configured": False,
            "connected": False,
            "error": _clean(f"{type(exc).__name__}: {exc}", 500),
        }
    return {
        "available": True,
        "configured": configured,
        "connected": connected,
        "ok": bool(health.get("ok")) and connected,
        "environment": _clean(health.get("environment"), 40),
        "persistent_storage": bool(health.get("persistent_storage")),
        "disk_mounted": bool(health.get("disk_mounted")),
        "backed_up": bool(health.get("backed_up")),
        "refresh_age_days": health.get("refresh_age_days"),
        "refresh_days_left": health.get("refresh_days_left"),
        "problems": [_clean(x, 500) for x in (health.get("problems") or [])[:10]],
    }


def _sanitize_invoice(inv: dict) -> dict:
    return {
        "doc_number": _clean(inv.get("doc_number"), 80),
        "date": _clean(inv.get("date"), 20),
        "due_date": _clean(inv.get("due_date"), 20),
        "total": inv.get("total") or 0,
        "balance": inv.get("balance") or 0,
        "status": _clean(inv.get("status"), 40),
        "link": _clean(inv.get("link"), 700),
    }


def _sanitize_customer(customer: dict, invoice_limit: int) -> dict:
    invoices = customer.get("invoices") or []
    return {
        "id": _clean(customer.get("id"), 80),
        "name": _clean(customer.get("name"), 180),
        "balance": customer.get("balance") or 0,
        "customer_since": _clean(customer.get("customer_since"), 20),
        "customer_since_label": _clean(customer.get("customer_since_label"), 40),
        "customer_years": customer.get("customer_years"),
        "link": _clean(customer.get("link"), 700),
        "invoices": [_sanitize_invoice(x) for x in invoices[:invoice_limit]],
        "error": _clean(customer.get("error"), 500) or None,
    }


def client_quickbooks(client_name: str, invoice_limit: int = 8) -> dict:
    """Read one canonical client's QuickBooks customer/balance/invoice view."""
    invoice_limit = max(1, min(int(invoice_limit or 8), 25))
    identity = resolve_identity(client_name)
    if not identity.get("known"):
        _audit("get_client_quickbooks", identity, status="not_found")
        return _not_found(client_name, identity)

    status = quickbooks_status()
    if not status.get("available") or not status.get("configured") or not status.get("connected"):
        _audit("get_client_quickbooks", identity, status="unavailable")
        return {
            "found": True,
            "available": False,
            "identity": identity,
            "quickbooks": status,
            "customers": [],
            "message": "QuickBooks is not currently available to the MCP gateway.",
        }

    try:
        result = quickbooks.lookup(identity["client"]) or {}
        customers = [_sanitize_customer(c, invoice_limit)
                     for c in (result.get("customers") or [])[:5]]
    except Exception as exc:
        _audit("get_client_quickbooks", identity, status="error")
        return {
            "found": True,
            "available": False,
            "identity": identity,
            "quickbooks": status,
            "customers": [],
            "error": _clean(f"{type(exc).__name__}: {exc}", 500),
        }

    _audit("get_client_quickbooks", identity, result_count=len(customers))
    return {
        "found": True,
        "available": True,
        "identity": identity,
        "quickbooks": status,
        "count": len(customers),
        "customers": customers,
    }


def google_access_summary(client_name: str) -> dict:
    """Read recorded Google-access state without requesting or returning OAuth tokens."""
    identity = resolve_identity(client_name)
    if not identity.get("known"):
        _audit("get_google_access_summary", identity, status="not_found")
        return _not_found(client_name, identity)
    try:
        from modules.google_access.models import AccessRequest
        rows = AccessRequest.query.order_by(AccessRequest.created_at.desc()).limit(500).all()
        matches = [r for r in rows if r.client_key() == identity.get("client_key")]
    except Exception as exc:
        _audit("get_google_access_summary", identity, status="unavailable")
        return {
            "found": True,
            "available": False,
            "identity": identity,
            "requests": [],
            "error": _clean(f"{type(exc).__name__}: {exc}", 500),
        }

    out = []
    for req in matches[:10]:
        out.append({
            "status": _clean(req.rollup(), 40),
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "completed_at": req.completed_at.isoformat() if req.completed_at else None,
            "services": [
                {
                    "service": _clean(g.service, 40),
                    "status": _clean(g.status, 40),
                    "resource_id": _clean(g.resource_id, 180),
                    "resource_name": _clean(g.resource_name, 180),
                    "role": _clean(g.role_granted, 80),
                    "granted_at": g.granted_at.isoformat() if g.granted_at else None,
                    "checked_at": g.checked_at.isoformat() if g.checked_at else None,
                }
                for g in (req.grants or [])
            ],
        })
    _audit("get_google_access_summary", identity, result_count=len(out))
    return {
        "found": True,
        "available": True,
        "identity": identity,
        "count": len(out),
        "requests": out,
        "note": "This reports SmartHub's recorded GA4/GTM/Search Console access state; it does not expose client OAuth credentials.",
    }


def client_ga4_properties(client_name: str) -> dict:
    """Return GA4 properties already mapped to one canonical client."""
    identity = resolve_identity(client_name)
    if not identity.get("known"):
        _audit("get_client_ga4_properties", identity, status="not_found")
        return _not_found(client_name, identity)
    try:
        from hub import google_index
        found = google_index.for_client(identity["client"], identity.get("domain") or "")
    except Exception as exc:
        _audit("get_client_ga4_properties", identity, status="unavailable")
        return {
            "found": True, "available": False, "identity": identity,
            "properties": [],
            "error": _clean(f"{type(exc).__name__}: {exc}", 500),
        }

    properties = []
    for item in (found.get("ga4") or [])[:20]:
        properties.append({
            "property_id": _clean(item.get("resource_id"), 80),
            "name": _clean(item.get("name"), 180),
            "account_name": _clean(item.get("account_name"), 180),
            "match": _clean(item.get("match"), 40),
            "match_detail": _clean(item.get("match_detail"), 400),
            "open_url": _clean(item.get("open_url"), 700),
        })
    status = "unavailable" if found.get("never_built") else "ok"
    _audit("get_client_ga4_properties", identity, status=status,
           result_count=len(properties))
    return {
        "found": True,
        "available": not bool(found.get("never_built")),
        "identity": identity,
        "index_built_at": found.get("built_at"),
        "index_stale": bool(found.get("stale")),
        "count": len(properties),
        "properties": properties,
        "message": ("The Google resource index has not been built yet."
                    if found.get("never_built") else ""),
    }


def _ga4_selection(identity: dict, property_id: str = "") -> tuple[dict | None, dict]:
    """Select only from GA4 properties the canonical client is mapped to."""
    from hub import google_index
    found = google_index.for_client(identity["client"], identity.get("domain") or "")
    rows = found.get("ga4") or []
    wanted = _clean(property_id, 80)
    if wanted:
        hit = next((r for r in rows
                    if _clean(r.get("resource_id"), 80) == wanted), None)
        if hit:
            return hit, found
        return None, found
    return (rows[0] if len(rows) == 1 else None), found


def client_ga4_summary(client_name: str, property_id: str = "",
                       start_date: str = "28daysAgo", end_date: str = "yesterday",
                       compare_start: str = "", compare_end: str = "") -> dict:
    """Read a bounded GA4 channel summary from a mapped client property."""
    identity = resolve_identity(client_name)
    if not identity.get("known"):
        _audit("get_client_ga4_summary", identity, status="not_found")
        return _not_found(client_name, identity)
    try:
        selected, index = _ga4_selection(identity, property_id)
    except Exception as exc:
        _audit("get_client_ga4_summary", identity, status="unavailable")
        return {"found": True, "available": False, "identity": identity,
                "error": _clean(f"{type(exc).__name__}: {exc}", 500)}

    choices = [{"property_id": _clean(r.get("resource_id"), 80),
                "name": _clean(r.get("name"), 180)}
               for r in (index.get("ga4") or [])[:20]]
    if selected is None:
        reason = "property_not_mapped" if property_id else "property_selection_required"
        _audit("get_client_ga4_summary", identity, status=reason,
               result_count=len(choices))
        return {
            "found": True, "available": False, "identity": identity,
            "reason": reason, "properties": choices,
            "message": ("That property is not mapped to this client."
                        if property_id else
                        "Choose a mapped GA4 property before running the report."),
        }

    from hub import analytics_ask
    ranges = [{"startDate": start_date, "endDate": end_date, "name": "Current"}]
    if compare_start or compare_end:
        if not (compare_start and compare_end):
            return {"found": True, "available": False, "identity": identity,
                    "reason": "invalid_date_range",
                    "message": "Both comparison dates are required."}
        ranges.append({"startDate": compare_start, "endDate": compare_end,
                       "name": "Comparison"})
    request, error = analytics_ask.validate({
        "metrics": ["sessions", "activeUsers", "newUsers", "engagedSessions",
                    "engagementRate", "keyEvents", "conversions"],
        "dimensions": ["sessionDefaultChannelGroup"],
        "dateRanges": ranges,
        "orderBy": {"metric": "sessions", "desc": True},
        "limit": 20,
    })
    if error or request is None:
        _audit("get_client_ga4_summary", identity, status="invalid_request")
        return {"found": True, "available": False, "identity": identity,
                "reason": "invalid_date_range", "message": _clean(error, 300)}

    try:
        from modules.google_finder import app as google_finder
        login = _clean(selected.get("google_login"), 240).lower()
        account = next((a for a in google_finder.connected_accounts()
                        if _clean(a.get("email"), 240).lower() == login), None)
        if not account:
            raise LookupError("The mapped Google login is not connected.")
        token = google_finder.refresh_access_token(login, account["refresh_token"])
        url = ("https://analyticsdata.googleapis.com/v1beta/properties/"
               f"{_clean(selected.get('resource_id'), 80)}:runReport")
        report = google_finder.google_post(token, url, request)
        shaped = analytics_ask.shape(report, request)
    except Exception as exc:
        _audit("get_client_ga4_summary", identity, status="error")
        return {
            "found": True, "available": False, "identity": identity,
            "property": choices and next(
                (p for p in choices if p["property_id"] ==
                 _clean(selected.get("resource_id"), 80)), choices[0]),
            "error": _clean(f"{type(exc).__name__}: {exc}", 500),
        }

    _audit("get_client_ga4_summary", identity, result_count=shaped.get("row_count"))
    return {
        "found": True, "available": True, "identity": identity,
        "property": {"property_id": _clean(selected.get("resource_id"), 80),
                     "name": _clean(selected.get("name"), 180)},
        "index_built_at": index.get("built_at"),
        "index_stale": bool(index.get("stale")),
        "query": {"metrics": [m["name"] for m in request["metrics"]],
                  "dimensions": [d["name"] for d in request.get("dimensions", [])],
                  "date_ranges": request["dateRanges"]},
        "result": shaped,
    }


def client_proposals(client_name: str) -> dict:
    """Read saved and uploaded proposals already associated with a client."""
    identity = resolve_identity(client_name)
    if not identity.get("known"):
        _audit("get_client_proposals", identity, status="not_found")
        return _not_found(client_name, identity)
    try:
        from hub import proposals
        result = proposals.proposals_for(identity["client"])
    except Exception as exc:
        _audit("get_client_proposals", identity, status="unavailable")
        return {"found": True, "available": False, "identity": identity,
                "saved": [], "uploaded": [],
                "error": _clean(f"{type(exc).__name__}: {exc}", 500)}
    _audit("get_client_proposals", identity, result_count=result.get("count"))
    return {"found": True, "available": True, "identity": identity,
            "count": result.get("count", 0),
            "saved": result.get("saved") or [],
            "uploaded": result.get("uploaded") or [],
            "note": _clean(result.get("note"), 500)}


def _public_io(row: dict) -> dict:
    return {
        "order": _clean(row.get("order"), 40),
        "client": _clean(row.get("client"), 180),
        "partner": _clean(row.get("partner"), 160),
        "io_type": _clean(row.get("io_type"), 80),
        "start": _clean(row.get("start"), 40),
        "end": _clean(row.get("end"), 40),
        "monthly": row.get("monthly") or 0,
        "campaign_total": row.get("campaign_total") or 0,
        "line_count": row.get("line_count") or 0,
        "lines": [{
            "product": _clean(line.get("product"), 160),
            "kind": _clean(line.get("kind"), 100),
            "budget": line.get("budget") or 0,
            "campaign_budget": line.get("campaign_budget") or 0,
            "start": _clean(line.get("start"), 40),
            "end": _clean(line.get("end"), 40),
        } for line in (row.get("lines") or [])[:60] if isinstance(line, dict)],
        "submitted_at": _clean(row.get("submitted_at"), 40),
        "last_submitted_at": _clean(row.get("last_submitted_at"), 40),
        "delivered_to_suite": bool((row.get("suite") or {}).get("delivered")),
        "ever_delivered_to_suite": bool((row.get("suite") or {}).get("ever_delivered")),
        "replaces_io": _clean(row.get("replaces_io"), 40),
    }


def client_insertion_orders(client_name: str, limit: int = 20) -> dict:
    """Read submitted insertion-order records for one canonical client."""
    identity = resolve_identity(client_name)
    if not identity.get("known"):
        _audit("get_client_insertion_orders", identity, status="not_found")
        return _not_found(client_name, identity)
    limit = max(1, min(int(limit or 20), 50))
    try:
        from hub import io_records
        result = io_records.listing(identity["client"])
    except Exception as exc:
        _audit("get_client_insertion_orders", identity, status="unavailable")
        return {"found": True, "available": False, "identity": identity,
                "orders": [],
                "error": _clean(f"{type(exc).__name__}: {exc}", 500)}
    if not result.get("measured"):
        _audit("get_client_insertion_orders", identity, status="unavailable")
        return {"found": True, "available": False, "identity": identity,
                "orders": [], "error": _clean(result.get("error"), 500)}
    orders = [_public_io(r) for r in (result.get("rows") or [])[:limit]]
    _audit("get_client_insertion_orders", identity, result_count=len(orders))
    return {"found": True, "available": True, "identity": identity,
            "count": len(orders), "orders": orders}


# ---------------------------------------------------------------------------
# Ad performance, out of the reports fact table
# ---------------------------------------------------------------------------
# The Hub already holds every figure this answers from: modules/reports keeps
# a fact table fed by Google Ads, Microsoft Ads, The Trade Desk, StackAdapt,
# GroundTruth and the CSV door, with hourly pacing and prorated margin over
# the top. What it did not have was a reading an assistant could ask a
# question of. Three rules hold this one honest:
#
# * It is a SUPERSET of the Client 360 ad-performance card, never a rival.
#   The two-spellings reading is ``client_card.candidate_keys()`` -- the same
#   function the card calls -- and the pacing block is
#   ``pacing.compute(client=)`` passed through field for field. Two readings
#   of one question drift the day either is edited, which is the failure
#   ``client_view.pacing`` records about its own first draft.
# * Only CONFIRMED mappings reach a figure. ``store.facts_for`` enforces that
#   for every reader in the Hub; pending campaigns are counted and named so
#   an answer can say what is not in the total.
# * A ratio with no denominator is ``None``, never 0 and never infinity. An
#   answer renders None as "not measured"; rendering it as zero is a figure
#   this Hub did not measure, which is ``hub/audit_summary.py``'s house rule.


def _f(value, places: int = 2):
    """A Decimal or number as a rounded float; None stays None."""
    if value is None:
        return None
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


def _ratio(top, bottom, *, scale: float = 1.0, places: int = 2):
    """One derived metric, or None when there is nothing to divide by."""
    try:
        bottom = float(bottom or 0)
        if not bottom:
            return None
        return round(float(top or 0) / bottom * scale, places)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# Where a sync would put a conversion value if it carried one. None of the
# pulls writes one today, which is why ``roas`` reads null with a note rather
# than a number: a return on ad spend computed against conversions counted as
# dollars is a figure nobody measured.
_VALUE_KEYS = ("conversion_value", "conversions_value", "conv_value",
               "revenue", "total_conversion_value")


def _conversion_value(fact: dict):
    extras = fact.get("extras") if isinstance(fact.get("extras"), dict) else {}
    for key in _VALUE_KEYS:
        if extras.get(key) is not None:
            try:
                return float(extras[key])
            except (TypeError, ValueError):
                return None
    return None


def _blank() -> dict:
    return {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0,
            "value": None, "value_rows": 0, "rows": 0}


def _add(bucket: dict, fact: dict) -> None:
    bucket["spend"] += float(fact.get("spend") or 0)
    bucket["impressions"] += int(fact.get("impressions") or 0)
    bucket["clicks"] += int(fact.get("clicks") or 0)
    bucket["conversions"] += float(fact.get("conversions") or 0)
    bucket["rows"] += 1
    value = _conversion_value(fact)
    if value is not None:
        bucket["value"] = (bucket["value"] or 0.0) + value
        bucket["value_rows"] += 1


def _metrics(bucket: dict) -> dict:
    """The eight headline figures for one bucket of fact rows."""
    imps, clicks = bucket["impressions"], bucket["clicks"]
    spend, convs = bucket["spend"], bucket["conversions"]
    return {
        "spend": round(spend, 2), "impressions": imps, "clicks": clicks,
        "conversions": round(convs, 2),
        "ctr": _ratio(clicks, imps, scale=100),
        "cpc": _ratio(spend, clicks),
        "cpa": _ratio(spend, convs),
        "conv_rate": _ratio(convs, clicks, scale=100),
    }


_DELTA_METRICS = ("spend", "impressions", "clicks", "conversions",
                  "ctr", "cpc", "cpa", "conv_rate")


def _delta(now_metrics: dict, was_metrics: dict | None, *,
           asked: bool = True) -> dict | None:
    """Percent move per metric.

    Three answers, and keeping them apart is the point. ``None`` for the
    whole block means no comparison was ASKED for (``compare=none``). A block
    of nulls means one was asked for and could not be made -- nothing was
    filed in the compare window -- which is a client's first period, and
    reporting it as a rise of zero or of infinity is the same lie twice.
    A figure means it was measured.
    """
    if not asked:
        return None
    if was_metrics is None:
        return {m: None for m in _DELTA_METRICS}
    from hub.periods import pct_change
    return {m: pct_change(now_metrics.get(m), was_metrics.get(m))
            for m in _DELTA_METRICS}


def _has_compare(buckets: dict) -> bool:
    return any(b["rows"] for b in buckets.values())


def _prorated_sold(lines: list[dict], start, end):
    """The sold figure for a window, prorated day by day.

    ``sold_amount`` on a budget line is a MONTHLY figure and a window is
    rarely a whole month, so comparing them whole inflates every margin by
    the days not yet spent -- the failure ``pacing.cost()`` records at
    length. Each day the line is in flight contributes one month-day of it,
    which reproduces that report's own arithmetic for a window inside one
    month and keeps working for one that spans several.

    Returns (prorated, monthly, lines_counted); prorated is None -- not
    zero -- when no line in the window carries a sold figure at all, because
    "not sold through this Hub" and "sold for nothing" are different answers.
    """
    import calendar
    from datetime import date as _date, timedelta as _td
    priced = [b for b in lines if b.get("sold_amount") is not None]
    if not priced:
        return None, None, 0
    total = 0.0
    counted = set()
    day = start
    while day <= end:
        days_in_month = calendar.monthrange(day.year, day.month)[1]
        for index, line in enumerate(priced):
            fs = line.get("flight_start") or ""
            fe = line.get("flight_end") or ""
            begins = _date.fromisoformat(fs) if fs else None
            ends = _date.fromisoformat(fe) if fe else None
            if (begins and day < begins) or (ends and day > ends):
                continue
            total += float(line["sold_amount"]) / days_in_month
            counted.add(index)
        day += _td(days=1)
    if not counted:
        return None, None, 0
    monthly = sum(float(priced[i]["sold_amount"]) for i in sorted(counted))
    return round(total, 2), round(monthly, 2), len(counted)


def _perf_unavailable(identity: dict, exc: Exception) -> dict:
    _audit("get_client_performance", identity, status="unavailable")
    return {"found": True, "available": False, "identity": identity,
            "error": _clean(f"{type(exc).__name__}: {exc}", 500),
            "message": "The reports store could not be read."}


def client_performance(client_name: str, period: str = "last_30",
                       compare: str = "previous_period",
                       platform: str = "", product: str = "",
                       start_date: str = "", end_date: str = "",
                       limit: int = 50) -> dict:
    """One client's ad performance for a named period, with the flags already
    decided.

    The campaign table, per-platform and account totals, period-over-period
    deltas, the pacing board's own rows and the prorated margin -- as one
    payload, so an answer never has to compute a figure to say one.
    """
    limit = max(1, min(int(limit or 50), 200))
    identity = resolve_identity(client_name)
    if not identity.get("known"):
        _audit("get_client_performance", identity, status="not_found")
        return _not_found(client_name, identity)

    from hub import periods

    # The window first: a bad period is refused before any query runs.
    try:
        window = periods.resolve(period, start=start_date, end=end_date)
        compare_win = periods.compare_window(window, compare)
    except ValueError as exc:
        _audit("get_client_performance", identity, status="invalid_period")
        return {"found": True, "available": False, "identity": identity,
                "error": _clean(str(exc), 300), "reason": "invalid_period",
                "periods": list(periods.PERIODS), "compares": list(periods.COMPARES)}

    try:
        from modules.reports import (client_card, flags as flag_rules, pacing,
                                     pricing, products, quarantine, store)
    except Exception as exc:                                # noqa: BLE001
        return _perf_unavailable(identity, exc)

    # Filters are matched exactly against the vocabularies that exist, and a
    # miss is refused by name. Fuzzy-matching a platform files one client's
    # spend under a filter they did not ask for and reports it as working.
    wanted_platform = _clean(platform, 40).lower()
    if wanted_platform and wanted_platform not in store.PLATFORMS:
        _audit("get_client_performance", identity, status="unknown_platform")
        return {"found": True, "available": False, "identity": identity,
                "error": "unknown platform", "reason": "unknown_platform",
                "platforms": [{"platform": p, "label": store.platform_label(p)}
                              for p in store.PLATFORMS]}
    wanted_product = _clean(product, 120)
    if wanted_product and wanted_product not in products.PRODUCTS:
        _audit("get_client_performance", identity, status="unknown_product")
        return {"found": True, "available": False, "identity": identity,
                "error": "unknown product", "reason": "unknown_product",
                "products": list(products.PRODUCTS)}

    # Every key this client's rows may be filed under -- the card's own
    # reading, not a second one. Summed once: a key that resolves to rows
    # already counted under another spelling would double the client's spend.
    try:
        keys = client_card.candidate_keys(identity["client"],
                                          identity.get("domain") or "")
        seen: set[tuple] = set()
        current, prior = [], []
        for key in keys:
            for row in store.facts_for(key, window.start, window.end):
                ident = (row["platform"], row["account_id"], row["campaign_id"],
                         row["date"])
                if ident in seen:
                    continue
                seen.add(ident)
                current.append(row)
        if compare_win is not None:
            seen_prior: set[tuple] = set()
            for key in keys:
                for row in store.facts_for(key, compare_win.start, compare_win.end):
                    ident = (row["platform"], row["account_id"], row["campaign_id"],
                             row["date"])
                    if ident in seen_prior:
                        continue
                    seen_prior.add(ident)
                    prior.append(row)
        campaigns_filed = [m for m in store.mapped_campaigns(limit=10000)
                           if m["client"] in keys]
        budget_lines = [b for b in store.budget_lines(limit=5000)
                        if b["client"] in keys
                        and (b.get("status") or "active") == "active"]
        links = [l for l in (store.link_for_client(k) for k in keys) if l is not None]
    except Exception as exc:                                # noqa: BLE001
        return _perf_unavailable(identity, exc)

    def keep(row: dict) -> bool:
        if wanted_platform and row["platform"] != wanted_platform:
            return False
        if wanted_product and (row.get("product") or "") != wanted_product:
            return False
        return True

    current = [r for r in current if keep(r)]
    prior = [r for r in prior if keep(r)]

    def name_of(row: dict) -> str:
        return (row.get("display_name") or row.get("campaign_name")
                or row["campaign_id"])

    # One bucket per scope, current and compare side by side, so every delta
    # is the same arithmetic on the same two windows.
    totals_now, totals_was = _blank(), _blank()
    plat_now: dict[str, dict] = {}
    plat_was: dict[str, dict] = {}
    camp_now: dict[tuple, dict] = {}
    camp_was: dict[tuple, dict] = {}
    camp_meta: dict[tuple, dict] = {}
    converting: set[str] = set()
    for rows, totals, by_plat, by_camp in ((current, totals_now, plat_now, camp_now),
                                           (prior, totals_was, plat_was, camp_was)):
        for row in rows:
            _add(totals, row)
            _add(by_plat.setdefault(row["platform"], _blank()), row)
            key = (row["platform"], row["account_id"], row["campaign_id"])
            _add(by_camp.setdefault(key, _blank()), row)
            camp_meta.setdefault(key, {
                "campaign": name_of(row), "platform": row["platform"],
                "label": store.platform_label(row["platform"]),
                "product": row.get("product") or ""})
            if float(row.get("conversions") or 0) > 0:
                converting.add(row["platform"])

    compare_present = compare_win is not None and totals_was["rows"] > 0
    totals = _metrics(totals_now)
    asked = compare_win is not None
    totals["delta"] = _delta(totals, _metrics(totals_was) if compare_present else None,
                             asked=asked)

    # Pricing, through the module's own rule rather than a second markup
    # table here. None -- "not priced" -- whenever any contributing platform
    # has no rule; a partial sum is a smaller number reported as a total.
    link = links[0] if links else None
    priced_total, all_priced = 0.0, bool(plat_now)
    by_platform = []
    for code, bucket in sorted(plat_now.items(), key=lambda kv: -kv[1]["spend"]):
        row = {"platform": code, "label": store.platform_label(code),
               "product": ", ".join(sorted({
                   meta["product"] for key, meta in camp_meta.items()
                   if key[0] == code and meta["product"]})) or ""}
        row.update(_metrics(bucket))
        was = plat_was.get(code)
        row["delta"] = _delta(row, _metrics(was) if (compare_present and was) else None,
                              asked=asked)
        try:
            price = pricing.client_price(code, bucket["spend"],
                                         bucket["impressions"], link)
        except Exception:                                   # noqa: BLE001
            price = None
        row["client_price"] = _f(price)
        if price is None:
            all_priced = False
        else:
            priced_total += float(price)
        by_platform.append(row)
    totals["client_price"] = round(priced_total, 2) if all_priced and plat_now else None

    sold_prorated, sold_month, sold_lines = _prorated_sold(
        budget_lines, window.start, window.end)
    totals["sold_prorated"] = sold_prorated
    totals["sold_month"] = sold_month
    totals["margin_pct"] = (round((sold_prorated - totals["spend"]) / sold_prorated * 100, 1)
                            if sold_prorated else None)

    # Return on ad spend needs a conversion VALUE, and no sync writes one
    # today. Null with a note naming the platforms, never a number computed
    # from conversion counts as though they were dollars.
    missing_value = sorted({code for code, bucket in plat_now.items()
                            if bucket["value_rows"] < bucket["rows"]})
    if plat_now and not missing_value and totals_now["value"]:
        totals["roas"] = _ratio(totals_now["value"], totals_now["spend"], places=2)
    else:
        totals["roas"] = None
        if plat_now:
            totals["roas_note"] = _clean(
                "conversion value not reported by "
                + ", ".join(store.platform_label(p) for p in missing_value), 300)

    campaigns = []
    for key, bucket in sorted(camp_now.items(), key=lambda kv: -kv[1]["spend"]):
        meta = camp_meta[key]
        row = {"campaign": meta["campaign"], "platform": meta["platform"],
               "platform_label": meta["label"], "product": meta["product"],
               "partner": ""}
        row.update(_metrics(bucket))
        was = camp_was.get(key)
        row["delta"] = _delta(row, _metrics(was) if (compare_present and was) else None,
                              asked=asked)
        row["compare_impressions"] = was["impressions"] if was else None
        campaigns.append(row)
    shown, more = campaigns[:limit], max(0, len(campaigns) - limit)

    # The pacing board's own rows, field for field. The utilization and the
    # line's CPA are added beside them for the flag rules; nothing the board
    # computed is recomputed here.
    pacing_rows = []
    try:
        for key in keys:
            for row in pacing.compute(client=key):
                line = {k: (_f(v) if hasattr(v, "quantize") else v)
                        for k, v in row.items()}
                line["as_of"] = row["as_of"].isoformat()
                line["period_start"] = row["period_start"].isoformat()
                line["period_end"] = row["period_end"].isoformat()
                line["last_spend_date"] = (row["last_spend_date"].isoformat()
                                           if row.get("last_spend_date") else None)
                line["last_sync"] = store.iso(row.get("last_sync"))
                line["band_label"] = pacing.BAND_LABELS.get(
                    row["band"], ("", row["band"]))[1]
                line["spent"] = _f(row["actual_to_date"])
                line["expected"] = _f(row["expected_to_date"])
                line["utilization_pct"] = _ratio(
                    row["actual_to_date"], row["monthly_budget"], scale=100, places=1)
                # The line's own cost per conversion, on the campaigns that
                # line actually covers, so the flag compares like with like.
                covered = [c for c in campaigns
                           if (not line.get("product") or c["product"] == line["product"])
                           and (not line.get("platform") or c["platform"] == line["platform"])]
                line_spend = sum(c["spend"] for c in covered)
                line_convs = sum(c["conversions"] for c in covered)
                line["line_cpa"] = _ratio(line_spend, line_convs)
                pacing_rows.append(line)
    except Exception:                                       # noqa: BLE001
        # A pacing board that blinked must not lose the spend table; the
        # answer says the block is absent rather than reporting no lines.
        pacing_rows = None

    try:
        held = [h for key in keys for h in quarantine.held_for_client(key)]
        quarantined = len({h["date"] for h in held
                           if h.get("date")
                           and window.start.isoformat() <= h["date"] <= window.end.isoformat()})
    except Exception:                                       # noqa: BLE001
        quarantined = None

    confirmed = [m for m in campaigns_filed if not m.get("pending")]
    pending = [m for m in campaigns_filed if m.get("pending")]
    if not campaigns_filed and not budget_lines and not links:
        state = "nothing_filed"
    elif campaigns_filed and not confirmed:
        state = "all_pending"
    elif not current:
        state = "nothing_this_period"
    else:
        state = "ok"

    computed = flag_rules.compute_flags(
        totals, campaigns, pacing_rows or [], compare_present,
        by_platform=by_platform, window_label=(compare_win.label if compare_win else ""),
        conversion_platforms=converting or None)
    # Campaign flags are hung on the row they are about as well as listed at
    # the top, so a table can carry them without the reader matching names.
    per_campaign = flag_rules.campaign_flags(
        campaigns, totals["cpa"],
        window_label=(compare_win.label if compare_win else ""),
        conversion_platforms=converting or None)
    for index, row in enumerate(shown):
        row["flags"] = [f["code"] for f in per_campaign.get(str(index), [])]
        row.pop("compare_impressions", None)

    data_through = max((r["date"] for r in current), default=None)
    notes = []
    if quarantined:
        notes.append(f"{quarantined} day{'' if quarantined == 1 else 's'} in this "
                     "window are held in quarantine, so these figures are incomplete.")
    if pending:
        notes.append(f"{len(pending)} campaign{'' if len(pending) == 1 else 's'} "
                     "filed under this client are still waiting for confirmation; "
                     "their spend is not in these totals.")
    if compare_win is not None and not compare_present:
        notes.append("Nothing was filed in the comparison window, so no change "
                     "figures could be measured.")
    if pacing_rows is None:
        notes.append("The pacing board could not be read, so no pacing lines are shown.")
    if more:
        notes.append(f"{more} further campaigns are not listed; the {limit} "
                     "largest by spend are.")

    sources = []
    try:
        synced = store.sync_status()
    except Exception:                                       # noqa: BLE001
        synced = {}
    for code in sorted(plat_now):
        row = synced.get(code) or {}
        sources.append({"platform": code, "label": store.platform_label(code),
                        "written_by": _clean(row.get("source"), 40) or "native",
                        "synced_at": _clean(row.get("at") or row.get("synced_at"), 40) or None})

    _audit("get_client_performance", identity, result_count=len(shown),
           period=window.period, platform=wanted_platform or None,
           product=wanted_product or None, state=state)
    return {
        "found": True, "available": True, "identity": identity,
        "window": window.as_dict(),
        "compare": ({**compare_win.as_dict(), "mode": compare}
                    if compare_win is not None else None),
        "filters": {"platform": wanted_platform, "product": wanted_product},
        "state": state,
        "keys": keys,
        "data_through": data_through.isoformat() if data_through else None,
        "campaigns_confirmed": len(confirmed),
        "pending_campaigns": len(pending),
        "pending_campaign_names": sorted(
            {_clean(m.get("display_name") or m.get("campaign_name")
                    or m.get("campaign_id"), 200) for m in pending})[:25],
        "quarantined_days": quarantined,
        "sources": sources,
        "totals": totals,
        "by_platform": by_platform,
        "campaigns": shown,
        "campaign_count": len(campaigns),
        "campaigns_omitted": more,
        "pacing": pacing_rows,
        "flags": computed,
        "thresholds": flag_rules.thresholds(),
        "conversion_platforms": sorted(converting),
        "staff_url": client_card._staff_url(keys[0]) if keys else "",
        "note": " ".join(notes),
    }

def register(mcp) -> None:
    """Attach V2 read tools once to the existing V1 MCP server object."""
    if getattr(mcp, "_smarthub_v2_registered", False):
        return

    @mcp.tool(title="Explain client identity", annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def explain_client_identity(client_name: str = "", url: str = "") -> dict:
        """Explain SmartHub's canonical client match, key, domain and confidence."""
        ident = resolve_identity(client_name, url)
        _audit("explain_client_identity", ident,
               status="ok" if ident.get("known") else "not_found")
        return ident

    @mcp.tool(title="Search clients with identity", annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def search_clients_v2(query: str = "", limit: int = 20) -> dict:
        """Search SmartHub's canonical client registry with stable client keys."""
        result = search_registry(query, limit)
        _audit("search_clients_v2", result_count=result.get("count"))
        return result

    @mcp.tool(title="Check QuickBooks status", annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_quickbooks_status() -> dict:
        """Get sanitized QuickBooks connection health; never returns credentials."""
        result = quickbooks_status()
        _audit("get_quickbooks_status",
               status="ok" if result.get("available") else "unavailable")
        return result

    @mcp.tool(title="Get client QuickBooks summary", annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_client_quickbooks(client_name: str, invoice_limit: int = 8) -> dict:
        """Get a client's QuickBooks customer, balance and recent invoice data."""
        return client_quickbooks(client_name, invoice_limit)

    @mcp.tool(title="Get Google access summary", annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_google_access_summary(client_name: str) -> dict:
        """Get recorded GA4/GTM/Search Console access state for a client."""
        return google_access_summary(client_name)

    @mcp.tool(title="List client GA4 properties", annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_client_ga4_properties(client_name: str) -> dict:
        """List GA4 properties mapped to a canonical SmartHub client."""
        return client_ga4_properties(client_name)

    @mcp.tool(title="Get client GA4 summary", annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_client_ga4_summary(client_name: str, property_id: str = "",
                               start_date: str = "28daysAgo",
                               end_date: str = "yesterday",
                               compare_start: str = "",
                               compare_end: str = "") -> dict:
        """Get bounded GA4 channel metrics for a mapped client property."""
        return client_ga4_summary(client_name, property_id, start_date, end_date,
                                  compare_start, compare_end)

    @mcp.tool(title="Get client ad performance", annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_client_performance(client_name: str, period: str = "last_30",
                               compare: str = "previous_period",
                               platform: str = "", product: str = "",
                               start_date: str = "", end_date: str = "",
                               limit: int = 50) -> dict:
        """Get a client's campaign performance, pacing and margin for a named period."""
        return client_performance(client_name, period, compare, platform, product,
                                  start_date, end_date, limit)

    @mcp.tool(title="List client proposals", annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_client_proposals(client_name: str) -> dict:
        """Get saved-builder and uploaded proposal summaries for a client."""
        return client_proposals(client_name)

    @mcp.tool(title="List client insertion orders", annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_client_insertion_orders(client_name: str, limit: int = 20) -> dict:
        """Get submitted insertion-order summaries for a client."""
        return client_insertion_orders(client_name, limit)

    setattr(mcp, "_smarthub_v2_registered", True)
