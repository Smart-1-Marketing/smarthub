"""Clients whose first trace in this Hub is an insertion order.

Client 360 is a view, not a table: it reads Knack's products and website
records and joins the Hub's own overlays onto them. That works from the day a
campaign is set up in Knack and not before — so the moment a new client is
most worth looking at, the day somebody wrote their first IO, their record is
blank. Nothing errors; the page simply says it found nothing, which is
indistinguishable from a name typed wrong.

So a submitted IO registers the client. Every rule here is about not making
that cure worse than the disease:

* **Only when they are genuinely new.** If the name or the website resolves
  to a client Knack or the Hub registry already knows, this writes nothing at
  all. A second row under a name that already exists is how one company
  becomes two on every report keyed on a client, and the IO Builder's own
  client picker exists precisely so most orders name a client we already have.

* **It is an overlay, never a write to Knack.** Knack owns the client record.
  The day the real one appears it wins and this row stops being consulted —
  the arrangement `hub/client_urls.py` uses for discovered URLs, and for the
  same reason.

* **The row says where it came from.** `source: "io"` with the order number
  and the date, so nothing downstream mistakes a client we have only quoted
  for one Knack has confirmed. `hub/clients_registry.py` marks these
  `is_io_only`, and does not touch `source` or `is_house` on a Knack client —
  an earlier version of that merge reused `house_clients()` and quietly
  relabelled real Knack clients as ours.

* **Matching is exact.** Through `hub/client_key.py`: domain first, then an
  exactly normalised name, never a substring. "Riverside HVAC" must not be
  taken as already-known because "Riverside HVAC Supply" exists — that would
  silently drop the new client instead of registering them.

* **A name with nothing else on it is still registered.** A new business
  often has no website yet. The row carries what the IO had, and the URL can
  arrive later from `hub/client_urls.py` like anybody else's.

Stored through `hub/jsonstore.py`, so it survives the data disk being
recreated — this is the only record that a prospect exists at all.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from hub import jsonstore
from hub.client_key import normalise_name
from hub.client_context import canonical_domain


def _path() -> str:
    return os.path.join(jsonstore.data_dir("hub"), "io_clients.json")


def overlay() -> dict:
    """{normalised name: row}. Never raises — a caller is mid-render."""
    rows = jsonstore.read_json(_path(), default={})
    return rows if isinstance(rows, dict) else {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def known_elsewhere(name: str, url: str = "") -> tuple[bool, str]:
    """(is this client already on the books somewhere that is not this file).

    Answers False only when both sources were readable and neither knew them.
    A source that could not be read is reported as *known* — refusing to
    register beats inventing a duplicate of a client Knack holds but was
    briefly unable to answer for, which is the failure that cannot be undone
    by deleting a row.

    **"Elsewhere" excludes this overlay, and that is the whole subtlety.**
    These rows are merged into `clients_registry.all_clients()`, so the moment
    one is written the client resolves — and a naive check would then read its
    own output as proof that somebody else already knew them. The second order
    for that client would be silently dropped instead of being recorded
    against the row. Worse, it would depend on *when* the registry's two-minute
    per-process cache last refreshed, so it would work in a test, work on the
    first worker and fail on the second, which is the shape of bug this
    codebase spends most of its comments on. Rows carrying `is_io_only` are
    ours, and are not an answer to this question.
    """
    mine = set(overlay().keys())

    try:
        from hub.client_key import resolve
        hit = resolve(name=name or "", url=url or "")
        if hit.get("known") and normalise_name(hit.get("client") or "") not in mine:
            return True, "the client registry"
    except Exception as exc:                              # noqa: BLE001
        return True, f"the client registry could not be read ({type(exc).__name__})"

    try:
        from hub import clients_registry
        row = clients_registry.find_client(name or "")
        if row and not row.get("is_io_only"):
            return True, "the client registry"
    except Exception as exc:                              # noqa: BLE001
        return True, f"the client registry could not be read ({type(exc).__name__})"

    return False, ""


def register(name: str, url: str = "", *, order: str = "", actor: str = "",
             contact: dict | None = None) -> dict:
    """Record a client first seen on an IO. Returns what it decided and why."""
    name = str(name or "").strip()
    key = normalise_name(name)
    if not key:
        return {"ok": False, "reason": "no name",
                "note": "The order carried no client name, so there is nothing "
                        "to file it under."}

    rows = overlay()
    row = rows.get(key) if isinstance(rows.get(key), dict) else {}

    # A client we registered from an earlier order is ours to update: the
    # second IO is recorded against the same row rather than refused. Only a
    # client nobody has registered is put to the "is this genuinely new?"
    # test, so that test is asked exactly once per client.
    if not row:
        known, where = known_elsewhere(name, url)
        if known:
            return {"ok": True, "registered": False, "reason": "already known",
                    "known_in": where,
                    "note": f"{name} is already on the books ({where}), so "
                            "nothing was added."}

    domain = canonical_domain(url)
    orders = [o for o in (row.get("orders") or []) if o]
    if order and order not in orders:
        orders.append(str(order))

    rows[key] = {
        "name": row.get("name") or name,
        "url": row.get("url") or (url or ""),
        "domain": row.get("domain") or domain,
        "source": "io",
        "orders": orders[:50],
        "contact": row.get("contact") or (contact or {}),
        "first_seen": row.get("first_seen") or _now(),
        "last_seen": _now(),
        "created_by": row.get("created_by") or actor,
    }
    jsonstore.write_json(_path(), rows)

    try:
        from hub import audit
        audit.log("io_builder", "client_registered", client=name,
                  actor=actor or "", order=str(order or ""))
    except Exception:                                     # noqa: BLE001
        pass

    return {"ok": True, "registered": True,
            "new": not bool(row), "client": rows[key],
            "note": (f"{name} had no record anywhere, so their Client 360 page "
                     "would have come back empty. Registered from this order."
                     if not row else
                     f"{name} was already registered from an earlier order.")}


def register_from_io(payload: dict) -> dict:
    """The IO Builder's submit payload, in whatever shape it arrived in."""
    p = payload or {}
    contact = {k: str(p.get(v) or "") for k, v in (
        ("name", "clientContactName"), ("email", "clientContactEmail"),
        ("phone", "clientContactPhone"))}
    return register(
        str(p.get("client") or p.get("client_name") or ""),
        str(p.get("url") or p.get("client_website") or ""),
        order=str(p.get("orderNumber") or p.get("order_number") or ""),
        actor=str(p.get("salesContact") or p.get("sales_contact") or ""),
        contact={k: v for k, v in contact.items() if v},
    )


def forget(name: str) -> bool:
    """Drop a row — for when the real client record turns up and this one is
    noise, or a name was typed wrong on the order that created it."""
    rows = overlay()
    key = normalise_name(name)
    if key not in rows:
        return False
    rows.pop(key, None)
    jsonstore.write_json(_path(), rows)
    return True
