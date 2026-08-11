"""Read-only access to the Knack data JSONs that ship with the Clients app.

The files live in clients_app/data/ (committed to the repo, refreshed by the
existing `npm run refresh` flow / GitHub Action).  Loaded lazily and cached
until the file's mtime changes, so a data refresh + redeploy (or a mounted
newer file) is picked up automatically.
"""
import json
import os
import threading

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clients_app", "data")

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _load(name: str):
    path = os.path.join(BASE, name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _lock:
        hit = _cache.get(name)
        if hit and hit[0] == mtime:
            return hit[1]
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    with _lock:
        _cache[name] = (mtime, data)
    return data


def _records(data) -> list[dict]:
    if data is None:
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("records", "rows", "data", "items"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
        # dict of lists? take the longest list value
        lists = [v for v in data.values() if isinstance(v, list)]
        if lists:
            longest = max(lists, key=len)
            return [r for r in longest if isinstance(r, dict)]
    return []


def products() -> list[dict]:
    return _records(_load("products.json"))


def websites() -> list[dict]:
    return _records(_load("websites.json"))


def data_age_hours() -> float | None:
    import time
    try:
        mtime = os.path.getmtime(os.path.join(BASE, "products.json"))
    except OSError:
        return None
    return (time.time() - mtime) / 3600.0


def _num(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace("$", "").replace(",", "").strip()
        try:
            return float(s)
        except ValueError:
            return 0.0
    return 0.0


def _is_live(rec: dict) -> bool:
    return str(rec.get("status", "")).strip().lower() == "live"


def summary() -> dict:
    prods = products()
    webs = websites()

    live = [r for r in prods if _is_live(r)]
    live_clients = {str(r.get("client", "")).strip() for r in live if r.get("client")}
    all_clients = {str(r.get("client", "")).strip() for r in prods if r.get("client")}
    live_budget = sum(_num(r.get("monthly")) for r in live)

    def _active(w):
        a = w.get("active")
        if isinstance(a, bool):
            return a
        return str(w.get("status", "")).strip().lower() == "active"

    active_sites = [w for w in webs if _active(w)]
    hm_monthly = sum(_num(w.get("hmMonthly")) for w in active_sites)

    return {
        "clients_total": len(all_clients),
        "clients_live": len(live_clients),
        "live_products": len(live),
        "live_budget_monthly": round(live_budget),
        "websites_total": len(webs),
        "websites_active": len(active_sites),
        "hm_monthly": round(hm_monthly),
        "data_age_hours": data_age_hours(),
    }


def search_client(q: str, limit: int = 8) -> list[dict]:
    """Group products + website records by client for Client 360."""
    ql = (q or "").strip().lower()
    if not ql:
        return []
    groups: dict[str, dict] = {}

    for r in products():
        client = str(r.get("client", "")).strip()
        if not client or ql not in client.lower():
            continue
        g = groups.setdefault(client.lower(), {"client": client, "products": [], "websites": []})
        g["products"].append({
            "product": r.get("product"),
            "io": r.get("io"),
            "status": r.get("status"),
            "monthly": r.get("monthly"),
            "sales": r.get("sales"),
            "partner": r.get("partner"),
            "start": r.get("start"),
            "end": r.get("end"),
            "dash": r.get("dash"),
        })

    for w in websites():
        hay = " ".join(str(w.get(k, "")) for k in ("name", "domain", "liveUrl")).lower()
        if ql not in hay:
            continue
        key = str(w.get("name", "")).strip().lower() or str(w.get("domain", "")).lower()
        # attach to an existing client group when names align, else own group
        target = None
        for gk, g in groups.items():
            if gk and (gk in key or key in gk):
                target = g
                break
        if target is None:
            target = groups.setdefault(key, {"client": w.get("name") or w.get("domain"), "products": [], "websites": []})
        target["websites"].append({
            "name": w.get("name"),
            "domain": w.get("domain"),
            "liveUrl": w.get("liveUrl"),
            "platform": w.get("platform"),
            "status": w.get("status"),
            "hmMonthly": w.get("hmMonthly"),
            "partner": w.get("partner"),
            "manager": w.get("manager"),
            "ga": w.get("ga"),
            "gtm": w.get("gtm"),
            "registrar": w.get("registrar"),
            "domainPurchased": w.get("domainPurchased"),
        })

    out = list(groups.values())
    # clients with most live products first
    out.sort(key=lambda g: (-len(g["products"]), str(g["client"]).lower()))
    for g in out:
        g["products"].sort(key=lambda p: (0 if str(p.get("status", "")).lower() == "live" else 1, str(p.get("product") or "")))
    return out[:limit]
