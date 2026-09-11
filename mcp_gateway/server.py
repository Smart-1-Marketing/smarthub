"""Read-only MCP gateway for SmartHub.

This is intentionally a separate ASGI process from the production Flask app.
It imports SmartHub's existing data adapters and audit logger, but a bug or
SDK upgrade here cannot take the main Hub down.

V1 rules:
- bearer-token authentication, fail closed when MCP_API_TOKEN is unset
- read-only tools only
- no raw credentials or arbitrary file/API access
- every tool invocation is written to SmartHub's existing activity log
- source freshness is returned with product data instead of being hidden
"""
from __future__ import annotations

import hmac
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from mcp.server import MCPServer
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from hub import audit, knack_data, knack_products


SERVER_NAME = "SmartHub MCP"
MCP_ACTOR = (os.environ.get("MCP_ACTOR") or "SmartHub MCP").strip()[:60]

mcp = MCPServer(
    SERVER_NAME,
    instructions=(
        "Smart 1 Marketing's read-only SmartHub gateway. Use search_clients "
        "before client-specific tools when the client name is uncertain. "
        "Never imply that stale or fallback data is live; source and age are "
        "included where SmartHub knows them."
    ),
)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _clean(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _product_source() -> dict:
    """Read the same live/cache/fallback source the Hub itself uses."""
    data = knack_products.rows()
    return {
        "rows": data.get("rows") or [],
        "source": data.get("source") or "unknown",
        "age_minutes": data.get("age_minutes"),
        "note": data.get("note") or "",
    }


def _client_names() -> dict[str, str]:
    """Normalized client key -> best display name across Hub registries."""
    names: dict[str, str] = {}
    data = _product_source()
    for row in data["rows"]:
        for key in ("client", "organization"):
            value = _clean(row.get(key), 160)
            if value:
                names.setdefault(_norm(value), value)
    for row in knack_data.websites():
        value = _clean(row.get("name"), 160)
        if value:
            names.setdefault(_norm(value), value)
    return names


def _resolve_client(name: str) -> tuple[str | None, list[str]]:
    query = _clean(name, 160)
    want = _norm(query)
    if not want:
        return None, []
    names = _client_names()
    if want in names:
        return names[want], []

    contains = [display for key, display in names.items()
                if want in key or key in want]
    contains = sorted(dict.fromkeys(contains), key=str.lower)
    if len(contains) == 1:
        return contains[0], []
    if contains:
        return None, contains[:10]

    # Conservative token fallback: enough to help "Quality Air" find
    # "Quality Air Columbus", without silently picking between several hits.
    tokens = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) >= 3]
    ranked = []
    for display in names.values():
        low = display.lower()
        score = sum(1 for token in tokens if token in low)
        if score:
            ranked.append((score, display))
    ranked.sort(key=lambda item: (-item[0], item[1].lower()))
    choices = list(dict.fromkeys(display for _, display in ranked[:10]))
    if len(choices) == 1:
        return choices[0], []
    return None, choices


def _audit(tool: str, *, client: str = "", status: str = "ok", **extra: Any) -> None:
    try:
        audit.log(
            "mcp",
            "tool_called",
            actor=MCP_ACTOR,
            tool=tool,
            client=_clean(client, 160) or None,
            status=status,
            **{k: v for k, v in extra.items() if v is not None},
        )
    except Exception:
        # Audit is best-effort everywhere else in SmartHub too; a logging
        # failure must not turn a read into a server error.
        pass


def _not_found(requested: str, suggestions: list[str]) -> dict:
    return {
        "found": False,
        "requested": requested,
        "suggestions": suggestions,
        "message": (
            "Client name is ambiguous; use search_clients and retry with the "
            "exact display name." if suggestions else "No matching SmartHub client was found."
        ),
    }


@mcp.tool()
def search_clients(query: str = "", limit: int = 20) -> dict:
    """Search SmartHub clients by name across products and website records."""
    limit = max(1, min(int(limit or 20), 50))
    q = _clean(query, 160).lower()
    names = sorted(set(_client_names().values()), key=str.lower)
    if q:
        qn = _norm(q)
        exact = [n for n in names if _norm(n) == qn]
        starts = [n for n in names if _norm(n).startswith(qn) and n not in exact]
        contains = [n for n in names if qn in _norm(n) and n not in exact and n not in starts]
        names = exact + starts + contains
    result = names[:limit]
    _audit("search_clients", result_count=len(result))
    return {"query": query, "count": len(result), "clients": result}


@mcp.tool()
def get_client(client_name: str) -> dict:
    """Get a concise Client 360-style summary for one SmartHub client."""
    resolved, suggestions = _resolve_client(client_name)
    if not resolved:
        _audit("get_client", client=client_name, status="not_found")
        return _not_found(client_name, suggestions)

    products = knack_products.for_client(resolved)
    websites = []
    want = _norm(resolved)
    for row in knack_data.websites():
        if _norm(row.get("name")) == want:
            websites.append({
                "name": _clean(row.get("name"), 160),
                "domain": _clean(row.get("domain"), 250),
                "url": _clean(row.get("liveUrl"), 500),
                "platform": _clean(row.get("platform"), 100),
                "status": _clean(row.get("status"), 100),
                "ga_account": _clean(row.get("ga"), 160),
                "gtm_account": _clean(row.get("gtm"), 160),
            })

    live_products = [p for p in products.get("products", []) if knack_data.is_running(p)]
    product_kinds = Counter(_clean(p.get("product") or p.get("kind"), 160)
                            for p in live_products)
    product_kinds.pop("", None)

    result = {
        "found": True,
        "client": resolved,
        "active_product_count": len(live_products),
        "active_monthly_media_or_service_value": products.get("monthly", 0),
        "active_products": [
            {"name": name, "count": count}
            for name, count in product_kinds.most_common(30)
        ],
        "campaigns": products.get("campaigns", [])[:50],
        "websites": websites,
        "product_data": {
            "source": products.get("source"),
            "age_minutes": products.get("age_minutes"),
            "note": products.get("note") or "",
        },
    }
    _audit("get_client", client=resolved)
    return result


@mcp.tool()
def get_client_services(client_name: str, include_inactive: bool = False) -> dict:
    """List a client's SmartHub/Knack products with dates, status and billing."""
    resolved, suggestions = _resolve_client(client_name)
    if not resolved:
        _audit("get_client_services", client=client_name, status="not_found")
        return _not_found(client_name, suggestions)

    data = knack_products.for_client(resolved)
    rows = data.get("products") or []
    if not include_inactive:
        rows = [row for row in rows if knack_data.is_running(row)]

    services = []
    for row in rows[:250]:
        services.append({
            "product": _clean(row.get("product") or row.get("kind"), 180),
            "io": _clean(row.get("io"), 80),
            "campaign": _clean(row.get("campaign"), 180),
            "status": _clean(row.get("status") or row.get("io_status"), 100),
            "start": _clean(row.get("start"), 20),
            "end": _clean(row.get("end"), 20),
            "monthly": row.get("monthly") or 0,
            "billing": row.get("billing") or 0,
            "partner": _clean(row.get("partner"), 120),
            "dashboard": _clean(row.get("dash"), 500),
        })

    _audit("get_client_services", client=resolved, result_count=len(services))
    return {
        "found": True,
        "client": resolved,
        "include_inactive": bool(include_inactive),
        "count": len(services),
        "services": services,
        "source": data.get("source"),
        "age_minutes": data.get("age_minutes"),
        "note": data.get("note") or "",
    }


@mcp.tool()
def get_client_websites(client_name: str) -> dict:
    """List website records SmartHub associates with one client."""
    resolved, suggestions = _resolve_client(client_name)
    if not resolved:
        _audit("get_client_websites", client=client_name, status="not_found")
        return _not_found(client_name, suggestions)
    want = _norm(resolved)
    matches = []
    for row in knack_data.websites():
        if _norm(row.get("name")) != want:
            continue
        matches.append({
            "name": _clean(row.get("name"), 160),
            "domain": _clean(row.get("domain"), 250),
            "url": _clean(row.get("liveUrl"), 500),
            "platform": _clean(row.get("platform"), 100),
            "status": _clean(row.get("status"), 100),
            "hosting_maintenance_monthly": row.get("hmMonthly") or row.get("hm") or 0,
            "ga_account": _clean(row.get("ga"), 160),
            "gtm_account": _clean(row.get("gtm"), 160),
            "registrar": _clean(row.get("registrar"), 120),
        })
    _audit("get_client_websites", client=resolved, result_count=len(matches))
    return {
        "found": True,
        "client": resolved,
        "count": len(matches),
        "websites": matches,
        "source": knack_data.websites_source(),
    }


@mcp.tool()
def get_campaign_inventory(client_name: str, active_only: bool = True) -> dict:
    """Group one client's product rows into campaigns for a quick inventory."""
    resolved, suggestions = _resolve_client(client_name)
    if not resolved:
        _audit("get_campaign_inventory", client=client_name, status="not_found")
        return _not_found(client_name, suggestions)
    data = knack_products.for_client(resolved)
    rows = data.get("products") or []
    if active_only:
        rows = [row for row in rows if knack_data.is_running(row)]

    grouped: dict[str, dict] = defaultdict(lambda: {
        "products": set(), "ios": set(), "partners": set(), "monthly": 0.0,
        "start": "", "end": "",
    })
    for row in rows:
        campaign = _clean(row.get("campaign"), 180) or "Unlabeled campaign"
        bucket = grouped[campaign]
        if row.get("product") or row.get("kind"):
            bucket["products"].add(_clean(row.get("product") or row.get("kind"), 180))
        if row.get("io"):
            bucket["ios"].add(_clean(row.get("io"), 80))
        if row.get("partner"):
            bucket["partners"].add(_clean(row.get("partner"), 120))
        try:
            bucket["monthly"] += float(row.get("monthly") or 0)
        except (TypeError, ValueError):
            pass
        start = _clean(row.get("start"), 20)
        end = _clean(row.get("end"), 20)
        if start and (not bucket["start"] or start < bucket["start"]):
            bucket["start"] = start
        if end and end > bucket["end"]:
            bucket["end"] = end

    campaigns = []
    for name, bucket in sorted(grouped.items(), key=lambda item: item[0].lower()):
        campaigns.append({
            "campaign": name,
            "products": sorted(bucket["products"]),
            "ios": sorted(bucket["ios"]),
            "partners": sorted(bucket["partners"]),
            "monthly": round(bucket["monthly"], 2),
            "start": bucket["start"],
            "end": bucket["end"],
        })
    _audit("get_campaign_inventory", client=resolved, result_count=len(campaigns))
    return {
        "found": True,
        "client": resolved,
        "active_only": bool(active_only),
        "count": len(campaigns),
        "campaigns": campaigns,
        "source": data.get("source"),
        "age_minutes": data.get("age_minutes"),
        "note": data.get("note") or "",
    }


@mcp.tool()
def get_mcp_activity(limit: int = 50) -> dict:
    """Return recent MCP audit events from SmartHub's append-only activity log."""
    limit = max(1, min(int(limit or 50), 200))
    rows = audit.read(limit=limit, module="mcp")
    # Do not recursively audit this read: it would cause the act of reading the
    # activity log to change the first row every time and obscure the event the
    # operator was trying to inspect.
    return {"count": len(rows), "activity": rows}


@mcp.resource("smarthub://capabilities")
def capabilities() -> str:
    """Human-readable contract for this MCP deployment."""
    return (
        "SmartHub MCP V1 is read-only. It can search clients, summarize a "
        "client, list products/services, list website records, group campaign "
        "inventory, and read its own MCP activity. It cannot change campaigns, "
        "post accounting transactions, modify websites, send messages, create "
        "invoices, or alter SmartHub records. All normal tool calls are audited."
    )


@mcp.resource("smarthub://data-freshness")
def data_freshness() -> str:
    """Current source/freshness statement for the product and website registries."""
    products = _product_source()
    website_source = knack_data.websites_source()
    return (
        f"Products: source={products['source']}, age_minutes={products['age_minutes']}, "
        f"note={products['note'] or 'none'}. Websites: source={website_source}."
    )


async def health(_request):
    token_configured = bool((os.environ.get("MCP_API_TOKEN") or "").strip())
    product_data = _product_source()
    return JSONResponse({
        "ok": token_configured,
        "service": SERVER_NAME,
        "mode": "read-only",
        "token_configured": token_configured,
        "product_source": product_data["source"],
        "product_age_minutes": product_data["age_minutes"],
        "website_source": knack_data.websites_source(),
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, status_code=200 if token_configured else 503)


class BearerAuth:
    """Small fail-closed ASGI auth wrapper around the MCP endpoint.

    V1 deliberately uses one service token rather than pretending SmartHub's
    browser cookie is suitable for a remote AI client. Per-user OAuth and tool
    scopes belong in V2; this token is the narrow bootstrap credential.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path") or "/"
        if path == "/health":
            return await self.app(scope, receive, send)
        expected = (os.environ.get("MCP_API_TOKEN") or "").strip()
        supplied = ""
        for raw_key, raw_value in scope.get("headers") or []:
            if raw_key.lower() == b"authorization":
                value = raw_value.decode("latin-1", errors="ignore")
                if value.lower().startswith("bearer "):
                    supplied = value[7:].strip()
                break
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            response = JSONResponse(
                {"error": "Unauthorized SmartHub MCP request."}, status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


# MCPServer's ASGI app owns /mcp and its lifespan. Mount it at / so the public
# endpoint stays exactly /mcp, and keep /health outside auth for Render.
_mcp_app = mcp.streamable_http_app()
_base_app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Mount("/", app=_mcp_app),
    ],
    lifespan=_mcp_app.router.lifespan_context,
)
app = BearerAuth(_base_app)
