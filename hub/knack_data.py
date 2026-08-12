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


def _period_label(yyyymm) -> str:
    s = str(yyyymm or "")
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if len(s) == 6 and s.isdigit():
        return f"{months[int(s[4:6])]} {s[:4]}"
    return s


def _history_path() -> str:
    base = "/var/data" if os.path.isdir("/var/data") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "hub-metrics-history.json")


def _website_movement(period, websites_active) -> int | None:
    """Websites carry no month-over-month fields, so the Hub snapshots the
    active count per Knack period and compares to the previous period."""
    path = _history_path()
    try:
        with open(path, encoding="utf-8") as fh:
            hist = json.load(fh)
    except (OSError, ValueError):
        hist = {}
    key = str(period or "")
    if key:
        entry = hist.get(key) or {}
        entry["websites_active"] = websites_active
        hist[key] = entry
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(hist, fh)
        except OSError:
            pass
    prev_keys = sorted(k for k in hist if k.isdigit() and k < key)
    if not prev_keys:
        return None
    prev = hist[prev_keys[-1]].get("websites_active")
    return websites_active - prev if isinstance(prev, (int, float)) else None


def month_over_month(prods: list[dict]) -> dict:
    """Per-client budget totals for this month vs last month, from the
    lastM/thisM active flags Knack exports on every IO row."""
    this_by, last_by = {}, {}
    for r in prods:
        client = str(r.get("client", "")).strip()
        if not client:
            continue
        m = _num(r.get("monthly"))
        if r.get("thisM"):
            this_by[client] = this_by.get(client, 0.0) + m
        if r.get("lastM"):
            last_by[client] = last_by.get(client, 0.0) + m
    new = sum(1 for c in this_by if c not in last_by)
    lost = sum(1 for c in last_by if c not in this_by)
    increased = sum(1 for c, v in this_by.items() if c in last_by and v > last_by[c] + 0.5)
    decreased = sum(1 for c, v in this_by.items() if c in last_by and v < last_by[c] - 0.5)
    return {"new": new, "lost": lost, "increased": increased, "decreased": decreased}


def summary() -> dict:
    raw = _load("products.json")
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

    this_period = raw.get("thisMonth") if isinstance(raw, dict) else None
    last_period = raw.get("lastMonth") if isinstance(raw, dict) else None
    mom = month_over_month(prods)
    try:
        movement = _website_movement(this_period, len(active_sites))
    except Exception:  # noqa: BLE001 — never break the dashboard on history I/O
        movement = None

    return {
        "clients_total": len(all_clients),
        "clients_live": len(live_clients),
        "live_products": len(live),
        "live_budget_monthly": round(live_budget),
        "websites_total": len(webs),
        "websites_active": len(active_sites),
        "hm_monthly": round(hm_monthly),
        "estimated_total_monthly": round(live_budget + hm_monthly),
        "new_customers": mom["new"],
        "lost_customers": mom["lost"],
        "increased_customers": mom["increased"],
        "decreased_customers": mom["decreased"],
        "website_movement": movement,
        "this_period": _period_label(this_period),
        "last_period": _period_label(last_period),
        "data_age_hours": data_age_hours(),
    }


CREATIVE_EXCLUDE = ("sem", "website seo", "listings", "email blast")


def _creative_items(prod_records: list[dict]) -> list[dict]:
    """Creative file links (PDF / Drive / Dropbox / file) grouped for display,
    newest year first — mirrors the Clients module's creative section."""
    seen = set()
    items = []
    for r in prod_records:
        url, kind = r.get("url"), (r.get("kind") or "none")
        if not url or kind == "none":
            continue
        pname = str(r.get("product", "")).lower()
        if any(x in pname for x in CREATIVE_EXCLUDE):
            continue
        key = (r.get("io"), url)
        if key in seen:
            continue
        seen.add(key)
        ts = str(r.get("ts") or "")
        year = ts[:4] if len(ts) >= 4 and ts[:4].isdigit() else str(r.get("start", ""))[-4:]
        items.append({
            "year": year,
            "product": r.get("product"),
            "campaign": r.get("campaign"),
            "io": r.get("io"),
            "url": url,
            "kind": kind,
            "start": r.get("start"),
        })
    items.sort(key=lambda i: (i["year"], str(i.get("start") or "")), reverse=True)
    return items


def _parse_gtm(value) -> dict | None:
    """websites.json holds strings like 'AdOps: GTM-TG6FPR8M'."""
    s = str(value or "").strip()
    if not s:
        return None
    label, _, rest = s.partition(":")
    if rest.strip():
        return {"login": label.strip(), "id": rest.strip()}
    return {"login": "", "id": s}


def search_client(q: str, limit: int = 8) -> list[dict]:
    """Group products + website records by client for Client 360."""
    ql = (q or "").strip().lower()
    if not ql:
        return []
    groups: dict[str, dict] = {}

    raw_by_group: dict[str, list[dict]] = {}

    for r in products():
        client = str(r.get("client", "")).strip()
        if not client or ql not in client.lower():
            continue
        g = groups.setdefault(client.lower(), {"client": client, "products": [], "websites": []})
        raw_by_group.setdefault(client.lower(), []).append(r)
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

    # Hub-attached website records (attach-only, never written back to Knack)
    try:
        from . import seo as _seo
        for g in groups.values():
            att = _seo.get_links(str(g["client"])).get("website", [])
            if not att:
                continue
            have = {str(w.get("domain") or "").lower() for w in g["websites"]}
            for w in _seo._client_websites(str(g["client"])):
                d = str(w.get("domain") or "").lower()
                if d and d in have:
                    continue
                have.add(d)
                g["websites"].append({
                    "name": w.get("name"), "domain": w.get("domain"),
                    "liveUrl": w.get("liveUrl"), "platform": w.get("platform"),
                    "status": w.get("status"), "hmMonthly": w.get("hmMonthly"),
                    "partner": w.get("partner"), "manager": w.get("manager"),
                    "ga": w.get("ga"), "gtm": w.get("gtm"),
                    "registrar": w.get("registrar"),
                    "domainPurchased": w.get("domainPurchased"),
                    "attached": True,
                })
    except Exception:  # noqa: BLE001 — attachments must never break search
        pass

    out = list(groups.values())
    # clients with most live products first
    out.sort(key=lambda g: (-len(g["products"]), str(g["client"]).lower()))
    for g in out:
        g["products"].sort(key=lambda p: (0 if str(p.get("status", "")).lower() == "live" else 1, str(p.get("product") or "")))

        # ---- header extras: billing, dashboard link, Smart 1 Site flag ----
        live = [p for p in g["products"] if str(p.get("status", "")).lower() == "live"]
        g["billing_monthly"] = round(sum(_num(p.get("monthly")) for p in live))
        g["dash_url"] = next(
            (p.get("dash") for p in live if isinstance(p.get("dash"), str) and p["dash"].startswith("http")),
            next((p.get("dash") for p in g["products"]
                  if isinstance(p.get("dash"), str) and p["dash"].startswith("http")), None),
        )
        g["smart1_site"] = any(
            "smart1" in str(w.get("platform", "")).replace(" ", "").lower() for w in g["websites"]
        )

        # ---- creative files + GTM containers ----
        gkey = str(g["client"]).strip().lower()
        g["creative"] = _creative_items(raw_by_group.get(gkey, []))[:24]
        gtms, seen_gtm = [], set()
        for w in g["websites"]:
            parsed = _parse_gtm(w.get("gtm"))
            if parsed and parsed["id"] not in seen_gtm:
                seen_gtm.add(parsed["id"])
                parsed["site"] = w.get("name") or w.get("domain")
                gtms.append(parsed)
        g["gtm_containers"] = gtms
    return out[:limit]
