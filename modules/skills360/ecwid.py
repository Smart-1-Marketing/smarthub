"""Ecwid -- the Ecommerce skill's data source.

A port of the Buckeye Lake Winery / Schmidt's hotsheet server (`schmidtdash`,
`server.js`): fetch every order with offset pagination, filter locally, and
derive this week / last week / this month / last month, coupon and discount
use, abandoned carts, top products and a twelve-month revenue series.

Two rules carried over from that build, each learned the hard way there:

  * **Paginate.** The API answers the first 100 records and nothing says so.
    Every metric that reads order history walks ``offset`` until a short page.
  * **Filter locally.** The API-side date parameters returned nothing for
    months; fetching everything and cutting it in Python is what worked.

Nothing here raises to a caller. A store that will not answer comes back as
``{"error": ...}`` so a card can say *could not be read* rather than *zero*.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

API_BASE = os.environ.get("ECWID_API_BASE", "https://app.ecwid.com/api/v3")
TIMEOUT = 25
PAGE = 100
MAX_PAGES = 60            # 6,000 orders -- a ceiling, not an expectation
CACHE_TTL = 600           # ten minutes; a hotsheet, not a ledger

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


class EcwidError(RuntimeError):
    """Message is safe to show a rep -- never contains the token."""


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _get(store_id: str, token: str, path: str, params: dict | None = None) -> dict:
    url = f"{API_BASE}/{store_id}{path}"
    try:
        r = requests.get(url, params=params or {}, headers=_headers(token), timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise EcwidError(f"Ecwid did not answer ({type(exc).__name__}).") from exc
    if r.status_code in (401, 403):
        raise EcwidError("Ecwid refused the token. Check the secret token (it starts "
                         "with `secret_`) and that the app has read access to orders "
                         "and products.")
    if r.status_code == 404:
        raise EcwidError("Ecwid has no store with that ID.")
    if not r.ok:
        raise EcwidError(f"Ecwid answered HTTP {r.status_code}.")
    try:
        return r.json() or {}
    except ValueError as exc:
        raise EcwidError("Ecwid answered with something other than JSON.") from exc


def verify(store_id: str, token: str) -> dict:
    """Prove the credentials read this store. What activation is gated on."""
    store_id = str(store_id or "").strip()
    token = str(token or "").strip()
    if not store_id.isdigit():
        return {"ok": False, "error": "The Store ID is the number Ecwid shows under Settings › General (digits only)."}
    if not token.startswith("secret_"):
        return {"ok": False, "error": "Use the secret token from the Ecwid app (it starts with `secret_`) -- "
                                      "not the public token and not the OAuth client secret."}
    try:
        prof = _get(store_id, token, "/profile")
        # A profile read proves the token; an orders read proves the scope
        # the dashboard actually needs. Both, or activation is a promise.
        orders = _get(store_id, token, "/orders", {"limit": 1})
    except EcwidError as exc:
        return {"ok": False, "error": str(exc)}
    gen = prof.get("generalInfo") or {}
    acct = prof.get("account") or {}
    settings = prof.get("settings") or {}
    return {"ok": True,
            "store_name": str(settings.get("storeName") or acct.get("accountName") or ""),
            "store_url": str(gen.get("storeUrl") or ""),
            "currency": str((prof.get("formatsAndUnits") or {}).get("currency") or "USD"),
            "order_count": int(orders.get("total") or 0)}


def _all(store_id: str, token: str, path: str) -> list[dict]:
    items: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES):
        page = _get(store_id, token, path, {"limit": PAGE, "offset": offset})
        batch = page.get("items") or []
        items.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return items


# ------------------------------------------------------------------ dates
def _parse(value) -> datetime | None:
    """Ecwid's `createDate` is "2024-03-01 14:05:00 +0000"."""
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ranges(now: datetime) -> dict:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today - timedelta(days=(today.weekday() + 1) % 7)   # Sunday, as the hotsheet counted
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    last_month_end = month_start
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    return {
        "week": (week_start, None), "last_week": (week_start - timedelta(days=7), week_start),
        "month": (month_start, None), "last_month": (last_month_start, last_month_end),
        "year": (year_start, None),
    }


def _within(dt, start, end) -> bool:
    return bool(dt) and dt >= start and (end is None or dt < end)


def _period(orders: list[dict], start, end=None) -> dict:
    rows = [o for o in orders if _within(o.get("_dt"), start, end)]
    status = {"pending": 0, "processing": 0, "shipped": 0, "delivered": 0, "other": 0}
    for o in rows:
        s = str(o.get("fulfillmentStatus") or o.get("status") or "other").lower()
        key = {"awaiting_processing": "pending", "processing": "processing",
               "shipped": "shipped", "delivered": "delivered"}.get(s, "other")
        status[key] += 1
    with_disc = [o for o in rows if (o.get("couponDiscount") or 0) + (o.get("discount") or 0) > 0]
    disc_total = sum((o.get("couponDiscount") or 0) + (o.get("discount") or 0) for o in rows)
    return {
        "orders": len(rows),
        "revenue": round(sum(float(o.get("total") or 0) for o in rows), 2),
        "avg_order": round(sum(float(o.get("total") or 0) for o in rows) / len(rows), 2) if rows else 0,
        "status": status,
        "discounts": {"orders_with": len(with_disc), "amount": round(disc_total, 2),
                      "rate": round(100.0 * len(with_disc) / len(rows), 1) if rows else 0},
    }


def _top_products(orders: list[dict], start, names: dict, limit: int = 10) -> list[dict]:
    agg: dict = {}
    for o in orders:
        if not _within(o.get("_dt"), start, None):
            continue
        for it in o.get("items") or []:
            pid = it.get("productId")
            row = agg.setdefault(pid, {"product_id": pid,
                                       "name": names.get(pid) or it.get("name") or "Unknown",
                                       "quantity": 0, "revenue": 0.0})
            qty = int(it.get("quantity") or 0)
            row["quantity"] += qty
            row["revenue"] += float(it.get("price") or 0) * qty
    out = sorted(agg.values(), key=lambda r: r["revenue"], reverse=True)[:limit]
    for r in out:
        r["revenue"] = round(r["revenue"], 2)
    return out


def _monthly(orders: list[dict], now: datetime) -> list[dict]:
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = []
    cur = first
    for _ in range(12):
        months.append(cur)
        cur = (cur - timedelta(days=1)).replace(day=1)
    months.reverse()
    out = []
    for i, start in enumerate(months):
        end = months[i + 1] if i + 1 < len(months) else None
        rows = [o for o in orders if _within(o.get("_dt"), start, end)]
        out.append({"month": start.strftime("%b %y"),
                    "revenue": round(sum(float(o.get("total") or 0) for o in rows), 2),
                    "orders": len(rows)})
    return out


def dashboard(store_id: str, token: str, *, now: datetime | None = None,
              fresh: bool = False, orders: list[dict] | None = None,
              products: list[dict] | None = None,
              carts: list[dict] | None = None) -> dict:
    """The hotsheet. Cached per store for CACHE_TTL; ``fresh=True`` skips it.

    ``orders``/``products``/``carts`` let a test hand in fixtures rather than
    a network; production callers pass none of them.
    """
    now = now or datetime.now(timezone.utc)
    key = str(store_id)
    if orders is None and not fresh:
        with _lock:
            hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return dict(hit[1], cached=True)
    try:
        if orders is None:
            orders = _all(store_id, token, "/orders")
        if products is None:
            products = _all(store_id, token, "/products")
        if carts is None:
            try:
                carts = _all(store_id, token, "/abandoned_sales")
            except EcwidError:
                carts = None                        # a scope some tokens lack
    except EcwidError as exc:
        return {"ok": False, "error": str(exc)}

    for o in orders:
        o["_dt"] = _parse(o.get("createDate"))
    names = {p.get("id"): p.get("name") for p in products}
    R = _ranges(now)
    out = {
        "ok": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "order_count": len(orders),
        "periods": {k: _period(orders, *R[k]) for k in ("week", "last_week", "month", "last_month", "year")},
        "top_products": {"week": _top_products(orders, R["week"][0], names),
                         "month": _top_products(orders, R["month"][0], names),
                         "year": _top_products(orders, R["year"][0], names)},
        "monthly": _monthly(orders, now),
        "abandoned": None,
        "recent": [],
    }
    if carts is not None:
        ab = {}
        for k in ("week", "month"):
            start = R[k][0]
            rows = [c for c in carts if _within(_parse(c.get("createDate")), start, None)]
            ab[k] = {"carts": len(rows),
                     "value": round(sum(float((c.get("cart") or {}).get("total") or c.get("cartValue") or 0)
                                        for c in rows), 2)}
        out["abandoned"] = ab
    recent = sorted([o for o in orders if o.get("_dt")], key=lambda o: o["_dt"], reverse=True)[:8]
    out["recent"] = [{"number": o.get("orderNumber") or o.get("id"),
                      "date": o["_dt"].strftime("%b %d"),
                      "total": round(float(o.get("total") or 0), 2),
                      "items": sum(int(i.get("quantity") or 0) for i in (o.get("items") or [])),
                      "status": str(o.get("fulfillmentStatus") or o.get("paymentStatus") or "").replace("_", " ").title()}
                     for o in recent]
    for o in orders:
        o.pop("_dt", None)
    with _lock:
        _cache[key] = (time.time(), out)
    return out


def forget(store_id: str) -> None:
    with _lock:
        _cache.pop(str(store_id), None)
