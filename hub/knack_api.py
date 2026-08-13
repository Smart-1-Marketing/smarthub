"""Knack REST API — web tickets (object_107) for Client 360.

The Hub discovers object_107's field IDs at runtime from the object schema
(matching by field label), so nothing is hard-coded against your Knack app.
Requires KNACK_APP_ID + KNACK_API_KEY.

Read AND create tickets only — nothing else is ever written to Knack.
"""
import json
import os
import re

import requests

BASE = "https://api.knack.com/v1"
TICKETS_OBJECT = os.environ.get("KNACK_TICKETS_OBJECT", "object_107")
CHANGE_OBJECT = os.environ.get("KNACK_CHANGE_OBJECT", "object_140")     # Campaign Change Requests
SUPPORT_OBJECT = os.environ.get("KNACK_SUPPORT_OBJECT", "object_121")   # Campaign Support Requests

_schema_cache: dict = {}


def configured() -> bool:
    return bool(os.environ.get("KNACK_APP_ID") and os.environ.get("KNACK_API_KEY"))


def _headers():
    return {"X-Knack-Application-Id": os.environ.get("KNACK_APP_ID", ""),
            "X-Knack-REST-API-Key": os.environ.get("KNACK_API_KEY", ""),
            "Content-Type": "application/json"}


def object_fields(obj: str) -> list[dict]:
    key = "fields:" + obj
    if key in _schema_cache:
        return _schema_cache[key]
    r = requests.get(f"{BASE}/objects/{obj}/fields", headers=_headers(), timeout=20)
    r.raise_for_status()
    fields = (r.json() or {}).get("fields", [])
    _schema_cache[key] = fields
    return fields


def _fields() -> list[dict]:
    return object_fields(TICKETS_OBJECT)


def _find_in(obj: str, *label_keywords, types=None) -> str | None:
    for kw in label_keywords:
        for f in object_fields(obj):
            label = str(f.get("label", "")).lower()
            if kw in label and (not types or f.get("type") in types):
                return f.get("key")
    return None


def _find_field(*label_keywords, types=None) -> str | None:
    """First tickets-object field whose label contains any keyword."""
    return _find_in(TICKETS_OBJECT, *label_keywords, types=types)


def field_map() -> dict:
    """Best-effort label→field mapping for object_107, cached."""
    if "map" in _schema_cache:
        return _schema_cache["map"]
    m = {
        "title": _find_field("ticket name", "subject", "title", "ticket",
                             types=("short_text", "paragraph_text")),
        "description": _find_field("description", "details", "request", "issue",
                                   types=("paragraph_text", "rich_text", "short_text")),
        "website": _find_field("website", "site", "domain", "url"),
        "client": _find_field("client", "customer", "business"),
        "status": _find_field("status"),
        "date": _find_field("date created", "created", "date"),
        "requested_by": _find_field("requested by", "submitted by", "created by", "name"),
    }
    _schema_cache["map"] = m
    return m


def create_ticket(client: str, website: str, subject: str,
                  description: str, author: str = "",
                  requested_by: str = "") -> dict:
    """Create a web ticket in Knack. Text is written into the best-matched
    fields; connection fields are skipped rather than guessed."""
    m = field_map()
    payload = {}
    conn_types = ("connection",)
    fields_by_key = {f.get("key"): f for f in _fields()}

    def put_text(key, value):
        if not key or not value:
            return
        f = fields_by_key.get(key, {})
        if f.get("type") in conn_types:      # never guess connection record ids
            return
        payload[key] = value

    put_text(m["title"], subject[:120])
    body = description
    if author:
        body = f"{description}\n\n— submitted by {author} via Smart 1 Hub"
    put_text(m["description"], body)
    put_text(m["website"], website)
    put_text(m["client"], client)
    put_text(m["requested_by"], requested_by or author)
    if not payload:
        raise RuntimeError(
            "Could not match any writable fields on "
            f"{TICKETS_OBJECT} — check the object's field labels.")
    r = requests.post(f"{BASE}/objects/{TICKETS_OBJECT}/records",
                      headers=_headers(), json=payload, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Knack rejected the ticket (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()


# ---------------- Campaign Change (object_140) / Support (object_121) ----
def campaign_object(kind: str) -> str:
    return CHANGE_OBJECT if kind == "change" else SUPPORT_OBJECT


def campaign_field_map(kind: str) -> dict:
    """Best-effort label→field mapping for the campaign request objects,
    with the matched labels included so the UI can show exactly what will
    be written before anything is sent."""
    obj = campaign_object(kind)
    cache_key = f"cmap:{obj}"
    if cache_key in _schema_cache:
        return _schema_cache[cache_key]
    labels = {f.get("key"): f.get("label") for f in object_fields(obj)}
    text_types = ("short_text", "paragraph_text", "rich_text")
    m = {
        "title": _find_in(obj, "request name", "subject", "title", "request",
                          "name", types=text_types),
        "description": _find_in(obj, "description", "details", "change",
                                "request", "notes", "issue",
                                types=("paragraph_text", "rich_text", "short_text")),
        "client": _find_in(obj, "client", "customer", "business", types=text_types),
        "campaign": _find_in(obj, "campaign", "product", types=text_types),
        "io": _find_in(obj, "io", "insertion", "order", types=text_types),
        "requested_by": _find_in(obj, "requested by", "submitted by",
                                 "created by", types=text_types),
    }
    # never map two purposes onto the same field
    seen = set()
    for k in list(m):
        if m[k] in seen:
            m[k] = None
        elif m[k]:
            seen.add(m[k])
    out = {"object": obj,
           "map": {k: {"field": v, "label": labels.get(v, "")} if v else None
                   for k, v in m.items()},
           "all_fields": [{"key": f.get("key"), "label": f.get("label"),
                           "type": f.get("type")} for f in object_fields(obj)]}
    _schema_cache[cache_key] = out
    return out


def create_campaign_request(kind: str, client: str, campaign: str, io: str,
                            subject: str, description: str,
                            author: str = "", requested_by: str = "") -> dict:
    info = campaign_field_map(kind)
    obj = info["object"]
    fields_by_key = {f.get("key"): f for f in object_fields(obj)}
    payload = {}

    def put(role, value):
        slot = info["map"].get(role)
        if not slot or not value:
            return
        if fields_by_key.get(slot["field"], {}).get("type") == "connection":
            return                       # never guess connection record ids
        payload[slot["field"]] = value

    put("title", subject[:120])
    body = description
    if author:
        body = f"{description}\n\n— submitted by {author} via Smart 1 Hub"
    put("description", body)
    put("client", client)
    put("campaign", campaign)
    put("io", io)
    put("requested_by", requested_by or author)
    if not payload:
        raise RuntimeError(f"Could not match any writable fields on {obj} — "
                           "check the object's field labels.")
    r = requests.post(f"{BASE}/objects/{obj}/records",
                      headers=_headers(), json=payload, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Knack rejected the request (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()


# ---------------- People (Requested By dropdown: object_161 + object_109) ----
PEOPLE_OBJECTS = tuple(
    x.strip() for x in os.environ.get(
        "KNACK_PEOPLE_OBJECTS", "object_161,object_109").split(",") if x.strip())


def _object_identifier(obj: str) -> str | None:
    """The object's display/identifier field key."""
    key = "ident:" + obj
    if key in _schema_cache:
        return _schema_cache[key]
    ident = None
    try:
        r = requests.get(f"{BASE}/objects/{obj}", headers=_headers(), timeout=20)
        if r.ok:
            ident = ((r.json() or {}).get("object") or {}).get("identifier")
    except requests.RequestException:
        ident = None
    if not ident:
        for f in object_fields(obj):
            if f.get("type") in ("name", "short_text"):
                ident = f.get("key")
                break
    _schema_cache[key] = ident
    return ident


def people_names() -> list[str]:
    """Names pulled from the people objects, merged + deduped, for the
    Requested By dropdowns."""
    if "people" in _schema_cache:
        return _schema_cache["people"]
    names = set()
    for obj in PEOPLE_OBJECTS:
        try:
            ident = _object_identifier(obj)
            if not ident:
                continue
            r = requests.get(f"{BASE}/objects/{obj}/records",
                             headers=_headers(),
                             params={"rows_per_page": 1000,
                                     "sort_field": ident, "sort_order": "asc"},
                             timeout=20)
            r.raise_for_status()
            for rec in (r.json() or {}).get("records", []):
                n = _plain(rec.get(ident))
                if n:
                    names.add(n)
        except Exception:  # noqa: BLE001 — one bad object shouldn't kill the list
            continue
    out = sorted(names, key=str.lower)
    _schema_cache["people"] = out
    return out


def _plain(v) -> str:
    if isinstance(v, dict):
        return str(v.get("identifier") or v.get("value") or
                   v.get("url") or v.get("date") or "")
    if isinstance(v, list):
        return ", ".join(_plain(x) for x in v)
    return re.sub(r"<[^>]+>", " ", str(v or "")).strip()


def list_tickets(client: str, website: str = "", limit: int = 25) -> list[dict]:
    """Tickets whose client/website field mentions this client (or domain)."""
    m = field_map()
    rules = []
    if m["client"]:
        rules.append({"field": m["client"], "operator": "contains", "value": client})
    dom = str(website or "").replace("https://", "").replace("http://", "").split("/")[0]
    if m["website"] and dom:
        rules.append({"field": m["website"], "operator": "contains", "value": dom})
    if not rules:
        return []
    filters = {"match": "or", "rules": rules}
    r = requests.get(f"{BASE}/objects/{TICKETS_OBJECT}/records",
                     headers=_headers(),
                     params={"filters": json.dumps(filters),
                             "rows_per_page": int(limit),
                             "sort_field": m["date"] or "id",
                             "sort_order": "desc"},
                     timeout=20)
    r.raise_for_status()
    out = []
    for rec in (r.json() or {}).get("records", []):
        out.append({
            "id": rec.get("id"),
            "title": _plain(rec.get(m["title"])) if m["title"] else "(ticket)",
            "description": _plain(rec.get(m["description"]))[:400] if m["description"] else "",
            "website": _plain(rec.get(m["website"])) if m["website"] else "",
            "status": _plain(rec.get(m["status"])) if m["status"] else "",
            "date": _plain(rec.get(m["date"])) if m["date"] else "",
        })
    return out
