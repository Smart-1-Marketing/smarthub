"""What this client is worth, and what they owe -- the Account value card.

Client 360 already carried both figures, in two places nobody read
together: the Products card sums live insertion orders into a monthly
total (`hub/knack_data.search_client()`'s own `billing_monthly`, which
`hub/record_health.py` already reads for its Billing pill), and the
Invoices card already resolves this client's QuickBooks customer and
reads their balance (`hub/quickbooks.lookup_for_clients()`, the same call
`/api/qb/invoices?client=` makes). Nothing here is a new source or a new
reading of either one -- it is the same two numbers, fetched together and
framed as the question a rep actually asks: what does this account bring
in, and is anything overdue.

Two rules, the ones every other Client 360 reader keeps:

  * **Absent is not zero.** A product book that could not be read leaves
    `billing.measured` False rather than reporting $0/mo; a QuickBooks
    that is not connected, or refuses, is its own state
    (`not_connected` / `not_measured`) and never reads as "nothing owed".
  * **A member with no matched customer is named, not folded into the
    total.** `hub/quickbooks.lookup_for_clients()` already keeps this
    distinction for the Invoices card -- "no invoices on file" and "we
    never found their QuickBooks customer" send somebody to different
    places, and only the first means there is nothing to chase. This
    reads the identical `unmatched` list rather than deciding again.

Grouped clients (`hub/client_groups.py`) are billed as one company, and
each half already handles it its own way rather than this module
iterating members and adding the risk of counting one twice.
`knack_data.search_client()` merges every other member's live products
into the group's own `billing_monthly` the moment it is asked about
*any* member -- the same merge `record_health.py`'s Billing pill reads
-- so this asks once, for the name on screen, exactly as that pill does.
The QuickBooks half genuinely has to walk the members, because a group's
attached customers are recorded one per member; `lookup_for_clients()`
is the one place that walk happens and it already deduplicates by
customer id, which is why this calls it rather than repeating the walk.
"""
from __future__ import annotations

__all__ = ["for_client"]


def _billing(name: str) -> dict:
    """The monthly total already merged onto this client's group, read
    the way `hub/record_health.py`'s Billing pill reads it."""
    try:
        from . import knack_data
    except Exception as exc:                              # noqa: BLE001
        return {"measured": False, "monthly": 0.0,
                "error": f"The product book could not be read ({type(exc).__name__})."}
    want = name.strip().lower()
    try:
        groups = knack_data.search_client(name)
    except Exception as exc:                              # noqa: BLE001
        return {"measured": False, "monthly": 0.0,
                "error": f"The product book could not be read ({type(exc).__name__})."}
    g = next((x for x in groups if str(x.get("client") or "").strip().lower() == want), None)
    if g is None and not groups:
        try:
            err = knack_data.products_error()
        except Exception:                                 # noqa: BLE001
            err = ""
        if err:
            return {"measured": False, "monthly": 0.0, "error": err}
    monthly = float((g or (groups[0] if groups else {})).get("billing_monthly") or 0)
    return {"measured": True, "monthly": round(monthly, 2), "error": ""}


def _owed(name: str, url: str) -> dict:
    """Outstanding balance across the group's own QuickBooks customers --
    the identical lookup the Invoices card runs for `?client=`."""
    try:
        from . import quickbooks as qb
        from . import seo, client_groups
        entries = []
        for member in client_groups.member_names(name, url) or [name]:
            att = seo.get_links(member).get("qb") or []
            if not isinstance(att, list):
                att = [att]
            entries.append({"client": member,
                            "ids": [str(a.get("id")) for a in att
                                    if isinstance(a, dict) and a.get("id")]})
        out = qb.lookup_for_clients(entries)
    except Exception as exc:                              # noqa: BLE001
        return {"state": "not_measured", "balance": 0.0, "overdue_count": 0,
                "customers": 0, "unmatched": [],
                "error": f"QuickBooks could not be read ({type(exc).__name__})."}
    if not out.get("configured"):
        return {"state": "not_connected", "balance": 0.0, "overdue_count": 0,
                "customers": 0, "unmatched": [], "error": "QuickBooks is not configured."}
    if not out.get("connected"):
        return {"state": "not_connected", "balance": 0.0, "overdue_count": 0,
                "customers": 0, "unmatched": [], "error": "QuickBooks is not connected."}
    if out.get("errors"):
        first = out["errors"][0]
        return {"state": "not_measured", "balance": 0.0, "overdue_count": 0,
                "customers": 0, "unmatched": out.get("unmatched") or [],
                "error": first.get("error") or "QuickBooks could not be read."}
    customers = out.get("customers") or []
    balance = round(sum(float(c.get("balance") or 0) for c in customers), 2)
    overdue = sum(1 for c in customers for inv in (c.get("invoices") or [])
                  if inv.get("status") == "Overdue")
    return {"state": "connected", "balance": balance, "overdue_count": overdue,
            "customers": len(customers), "unmatched": out.get("unmatched") or [], "error": ""}


def for_client(name: str, url: str = "") -> dict:
    """Never raises -- each half names its own failure rather than the
    whole card going blank over one of the two sources."""
    name = str(name or "").strip()
    if not name:
        return {"measured": False, "error": "A client is required.",
                "billing": {"measured": False, "monthly": 0.0, "error": ""},
                "owed": {"state": "not_measured", "balance": 0.0, "overdue_count": 0,
                        "customers": 0, "unmatched": [], "error": ""}}
    billing = _billing(name)
    owed = _owed(name, url)
    return {"measured": bool(billing.get("measured")) or owed.get("state") == "connected",
            "billing": billing, "owed": owed}
