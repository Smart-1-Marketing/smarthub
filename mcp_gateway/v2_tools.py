"""Read-only V2 tools layered on top of the stable MCP V1 transport.

This module deliberately contains no write actions.  It reuses SmartHub's
canonical client identity and existing QuickBooks read adapter, but only
returns a narrow, sanitized surface suitable for an external MCP client.
"""
from __future__ import annotations

from typing import Any

from hub import audit, client_key as hub_client_key, clients_registry, quickbooks


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


def register(mcp) -> None:
    """Attach V2 read tools once to the existing V1 MCP server object."""
    if getattr(mcp, "_smarthub_v2_registered", False):
        return

    @mcp.tool()
    def explain_client_identity(client_name: str = "", url: str = "") -> dict:
        """Explain SmartHub's canonical client match, key, domain and confidence."""
        ident = resolve_identity(client_name, url)
        _audit("explain_client_identity", ident,
               status="ok" if ident.get("known") else "not_found")
        return ident

    @mcp.tool()
    def search_clients_v2(query: str = "", limit: int = 20) -> dict:
        """Search SmartHub's canonical client registry with stable client keys."""
        result = search_registry(query, limit)
        _audit("search_clients_v2", result_count=result.get("count"))
        return result

    @mcp.tool()
    def get_quickbooks_status() -> dict:
        """Get sanitized QuickBooks connection health; never returns credentials."""
        result = quickbooks_status()
        _audit("get_quickbooks_status",
               status="ok" if result.get("available") else "unavailable")
        return result

    @mcp.tool()
    def get_client_quickbooks(client_name: str, invoice_limit: int = 8) -> dict:
        """Get a client's QuickBooks customer, balance and recent invoice data."""
        return client_quickbooks(client_name, invoice_limit)

    @mcp.tool()
    def get_google_access_summary(client_name: str) -> dict:
        """Get recorded GA4/GTM/Search Console access state for a client."""
        return google_access_summary(client_name)

    @mcp.tool()
    def get_client_ga4_properties(client_name: str) -> dict:
        """List GA4 properties mapped to a canonical SmartHub client."""
        return client_ga4_properties(client_name)

    @mcp.tool()
    def get_client_ga4_summary(client_name: str, property_id: str = "",
                               start_date: str = "28daysAgo",
                               end_date: str = "yesterday",
                               compare_start: str = "",
                               compare_end: str = "") -> dict:
        """Get bounded GA4 channel metrics for a mapped client property."""
        return client_ga4_summary(client_name, property_id, start_date, end_date,
                                  compare_start, compare_end)

    @mcp.tool()
    def get_client_proposals(client_name: str) -> dict:
        """Get saved-builder and uploaded proposal summaries for a client."""
        return client_proposals(client_name)

    @mcp.tool()
    def get_client_insertion_orders(client_name: str, limit: int = 20) -> dict:
        """Get submitted insertion-order summaries for a client."""
        return client_insertion_orders(client_name, limit)

    setattr(mcp, "_smarthub_v2_registered", True)
