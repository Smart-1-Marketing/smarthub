"""One list of every client the Hub knows about, from every source.

Knack remains the billing source of truth. Website records, house clients,
discovered URLs and IO-only clients are overlays. Approved company aliases
from hub.company_identity are virtual rows: they inherit the canonical record's
URL/domain/metadata without editing, deleting or merging any upstream record.
"""
import datetime as _dt
import os
import re
import threading

from . import jsonstore
from . import knack_data

_lock = threading.Lock()


def _store_base() -> str:
    return jsonstore.data_root()


def _house_path() -> str:
    return os.path.join(_store_base(), "house_clients.json")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s[:80] or "client"


def norm_domain(value: str) -> str:
    from hub.client_context import canonical_domain
    return canonical_domain(value)


# ------------------------------------------------------------- house clients
def house_clients() -> list[dict]:
    rows = jsonstore.read_json(_house_path(), default=[])
    return rows if isinstance(rows, list) else []


def _write_house(rows: list[dict]):
    with _lock:
        jsonstore.write_json(_house_path(), rows, indent=1)


def add_house_client(name: str, url: str = "", notes: str = "",
                     actor: str = "") -> dict:
    name = str(name or "").strip()[:200]
    if not name:
        raise ValueError("A name is required.")
    url = str(url or "").strip()[:400]
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    rows = house_clients()
    key = slugify(name)
    if any(r.get("slug") == key for r in rows):
        raise ValueError(f"“{name}” is already on the house list.")

    existing = next((c for c in all_clients()
                     if c["name"].lower() == name.lower() and not c["is_house"]), None)
    if existing:
        detail = (f" with {existing['product_count']} product"
                  f"{'s' if existing['product_count'] != 1 else ''}"
                  if existing.get("product_count") else "")
        raise ValueError(
            f"“{existing['name']}” is already a client{detail}, so it's "
            "selectable everywhere already — no need to add it as a house site.")
    row = {
        "slug": key,
        "name": name,
        "url": url,
        "domain": norm_domain(url),
        "notes": str(notes or "").strip()[:500],
        "added_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "added_by": str(actor or "").strip()[:120],
    }
    rows.insert(0, row)
    _write_house(rows)
    return row


def update_house_client(slug: str, updates: dict) -> dict | None:
    rows = house_clients()
    hit = next((r for r in rows if r.get("slug") == slug), None)
    if hit is None:
        return None
    if "name" in updates and str(updates["name"]).strip():
        hit["name"] = str(updates["name"]).strip()[:200]
    if "url" in updates:
        u = str(updates["url"] or "").strip()[:400]
        if u and not u.startswith(("http://", "https://")):
            u = "https://" + u
        hit["url"] = u
        hit["domain"] = norm_domain(u)
    if "notes" in updates:
        hit["notes"] = str(updates["notes"] or "").strip()[:500]
    _write_house(rows)
    return hit


def delete_house_client(slug: str) -> bool:
    rows = house_clients()
    if not any(r.get("slug") == slug for r in rows):
        return False
    _write_house([r for r in rows if r.get("slug") != slug])
    return True


# --------------------------------------------------------- the combined list
_cache: dict = {"at": 0.0, "rows": []}
_CACHE_SECONDS = 120


def all_clients(refresh: bool = False) -> list[dict]:
    """Every client plus approved aliases, newest knowledge winning."""
    import time
    if not refresh and _cache["rows"] and time.time() - _cache["at"] < _CACHE_SECONDS:
        return _cache["rows"]

    by_key: dict[str, dict] = {}

    # 1. Website records give us domains.
    web_by_client: dict[str, dict] = {}
    for w in knack_data.websites():
        nm = str(w.get("name") or "").strip()
        if not nm:
            continue
        key = nm.lower()
        cur = web_by_client.get(key)
        if cur is None or (not cur.get("liveUrl") and w.get("liveUrl")):
            web_by_client[key] = w

    # 2. Knack clients (anyone with a product) — billing source of truth.
    seo_clients: set[str] = set()
    for r in knack_data.products():
        nm = str(r.get("client") or "").strip()
        if not nm:
            continue
        key = nm.lower()
        entry = by_key.setdefault(key, {
            "name": nm, "slug": slugify(nm), "source": "knack",
            "url": "", "domain": "", "products": set(), "running": set(),
            "is_seo": False, "is_house": False, "live": False,
        })
        pname = str(r.get("product") or "")
        if pname:
            entry["products"].add(pname)
        if knack_data.is_running(r):
            entry["live"] = True
            if pname:
                entry["running"].add(pname)
            if "seo" in pname.lower():
                entry["is_seo"] = True
                seo_clients.add(key)

    # 3. Website records with no exact Knack name are still clients.
    for key, w in web_by_client.items():
        nm = str(w.get("name") or "").strip()
        entry = by_key.setdefault(key, {
            "name": nm, "slug": slugify(nm), "source": "website",
            "url": "", "domain": "", "products": set(), "running": set(),
            "is_seo": False, "is_house": False,
            "live": str(w.get("status") or "").lower() == "live",
        })
        if not entry["domain"]:
            entry["domain"] = norm_domain(w.get("domain") or w.get("liveUrl") or "")
        if not entry["url"]:
            u = str(w.get("liveUrl") or "").strip() or str(w.get("domain") or "").strip()
            if u:
                entry["url"] = u if u.startswith("http") else "https://" + u

    # Fill a Knack client's missing URL from a website-name match.
    for key, entry in by_key.items():
        if entry["domain"]:
            continue
        w = web_by_client.get(key)
        if w is None:
            flat = re.sub(r"[^a-z0-9]", "", key)[:12]
            if flat:
                w = next((x for k, x in web_by_client.items()
                          if flat in re.sub(r"[^a-z0-9]", "", k)), None)
        if w:
            entry["domain"] = norm_domain(w.get("domain") or w.get("liveUrl") or "")
            u = str(w.get("liveUrl") or "").strip() or str(w.get("domain") or "").strip()
            if u and not entry["url"]:
                entry["url"] = u if u.startswith("http") else "https://" + u

    # 4. House URLs — ours, no products, flagged as such.
    for h in house_clients():
        key = str(h.get("name", "")).lower()
        entry = by_key.setdefault(key, {
            "name": h.get("name", ""),
            "slug": h.get("slug") or slugify(h.get("name", "")),
            "source": "house", "url": "", "domain": "", "products": set(),
            "running": set(), "is_seo": False, "is_house": True, "live": True,
        })
        entry["is_house"] = True
        entry["source"] = "house"
        entry["url"] = h.get("url") or entry["url"]
        entry["domain"] = h.get("domain") or entry["domain"]
        entry["notes"] = h.get("notes", "")

    # 5. Human-accepted URLs discovered in another data set.
    try:
        from hub.client_key import normalise_name as _norm
        from hub.client_urls import overlay as _discovered
        found = _discovered()
        if found:
            for entry in by_key.values():
                if entry.get("url") or entry.get("domain"):
                    continue
                hit = found.get(_norm(entry.get("name", "")))
                if not hit:
                    continue
                entry["url"] = hit.get("url", "")
                entry["domain"] = hit.get("domain", "")
                entry["url_source"] = "discovered"
                entry["url_from"] = hit.get("source", "")
                entry["url_accepted_by"] = hit.get("accepted_by", "")
    except Exception:  # noqa: BLE001
        pass

    # 6. Clients whose only trace is an insertion order.
    try:
        from hub import io_clients as _ioc
        for row in _ioc.overlay().values():
            nm = str(row.get("name") or "").strip()
            if not nm or nm.lower() in by_key:
                continue
            by_key[nm.lower()] = {
                "name": nm, "slug": slugify(nm), "source": "io",
                "url": row.get("url", ""), "domain": row.get("domain", ""),
                "products": set(), "running": set(),
                "is_seo": False, "is_house": False, "live": True,
                "is_io_only": True, "io_orders": list(row.get("orders") or []),
            }
    except Exception:  # noqa: BLE001
        pass

    rows = []
    for entry in by_key.values():
        products = sorted(entry.pop("products", set()))
        running = sorted(entry.pop("running", set()))
        from hub.client_key import client_key
        rows.append({**entry, "products": products, "product_count": len(products),
                     "running_products": running, "running_count": len(running),
                     "key": client_key(entry["name"], entry.get("url")
                                       or entry.get("domain") or "")})

    # 7. Durable company aliases. These are virtual views of a canonical row,
    # not new source-system clients. The alias therefore inherits website,
    # domain and every future metadata field that is added to the canonical row.
    try:
        from hub.company_identity import augment_registry
        rows = augment_registry(rows)
    except Exception:  # noqa: BLE001
        # Identity enrichment must never make the client book unavailable.
        pass

    rows.sort(key=lambda r: r["name"].lower())
    _cache["rows"] = rows
    _cache["at"] = time.time()
    return rows


def search_clients(q: str, limit: int = 12) -> list[dict]:
    """Type-ahead without cluttering suggestions with every stored alias.

    An exact alias is returned (so pasted provider names resolve); fuzzy/prefix
    suggestions prefer canonical rows.
    """
    q = str(q or "").strip().lower()
    rows = all_clients()
    if not q:
        return [r for r in rows if r["is_house"] and not r.get("is_alias")][:limit]

    exact, prefix, contains = [], [], []
    for r in rows:
        name, dom = r["name"].lower(), (r.get("domain") or "").lower()
        if name == q or dom == q:
            exact.append(r)
        elif r.get("is_alias"):
            continue
        elif name.startswith(q) or dom.startswith(q):
            prefix.append(r)
        elif q in name or (dom and q in dom):
            contains.append(r)
    return (exact + prefix + contains)[:limit]


def find_client(name: str) -> dict | None:
    """Find canonical data from either its filed name or an approved alias."""
    key = str(name or "").strip().lower()
    if not key:
        return None
    rows = all_clients()
    exact = next((r for r in rows if r["name"].lower() == key), None)
    if exact:
        return exact
    # Legal suffix and punctuation differences should work even if the raw
    # alias was never worth persisting during backfill.
    try:
        from hub.client_key import normalise_name
        norm = normalise_name(name)
        return next((r for r in rows if normalise_name(r.get("name", "")) == norm), None)
    except Exception:  # noqa: BLE001
        return None


def is_seo_client(name: str) -> bool:
    hit = find_client(name)
    return bool(hit and hit.get("is_seo"))
