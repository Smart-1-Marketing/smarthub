"""The email campaigns section of a client's report: what was sent from
their own sub-account, and how many were delivered, opened and clicked,
as the Suite answered last night.

``gate(client_key, client_name)`` decides whether a client gets the section
and ``section(link, today)`` builds it, the ``youtube.py`` shape: both are
read by ``client_view.build()``, so the page, its ``data.json`` and the PDF
carry one answer, and by the staff page, which prints the gate so a rep
can see why the section is or is not on the client's report.

## The gate

Two things have to be true, and each is read from the module that owns it:

* **The client has a live email product** -- a row on the live product
  book whose medium ``hub.creative_needs.medium_of`` calls email,
  matched on the exact normalised name and never a substring. A client
  with none gets no email section however many campaigns they send,
  because the section sits on a report about work Smart 1 does.
* **A Suite sub-account is linked** -- ``hub.suite_accounts.location_for``,
  the one reader of that mapping.

A linked account with no reading yet, or whose reading found no sent
campaign, is gated in and absent from the page: ``public_view()`` drops a
block nothing measured, because "linked and not read yet" is a sentence
about our tooling on a document about their business. The staff page
keeps the reason.

## What is measured, and what is said

The campaigns' own names, subjects and send dates, the counts the Suite
reports for each, and the 30- and 90-day totals over them -- arithmetic on
the stored rows (``hub/suite_email_stats.py``). No vendor is named: the
section is titled "Email campaigns" and the platform that sent them does
not appear, the ``products.FORBIDDEN`` rule.
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger(__name__)

LABELS = {
    "section": "Email campaigns",
    "campaigns": "Campaigns sent",
    "delivered": "Emails delivered",
    "opened": "Opened",
    "clicked": "Clicked",
    "period": "Over the last 30 days",
    "table_campaign": "Campaign",
    "table_sent": "Sent",
    "table_delivered": "Delivered",
    "table_opened": "Opened",
    "table_clicked": "Clicked",
    "no_counts": "counts not yet reported",
}


# ---------------------------------------------------------------------------
# Sources -- each behind its own function, so a test stands in for one
# ---------------------------------------------------------------------------

def _product_rows() -> list[dict]:
    from hub import knack_data
    return knack_data._product_source()[0]


def _running(row: dict) -> bool:
    from hub import knack_data
    return bool(knack_data.is_running(row))


def _is_email(row: dict) -> bool:
    """Two readings of one question, both consulted: the creative gate's
    medium (which decides by category, and the product book carries none)
    and the spec kit's channel list, which knows every email line on the
    rate card by name. Either saying email is enough."""
    from hub import creative_needs, creative_specs
    product = str(row.get("product") or "")
    item = {"product": product, "category": str(row.get("category") or ""),
            "description": str(row.get("tactics") or "")}
    if creative_needs.medium_of(item) == creative_needs.EMAIL:
        return True
    return "email" in creative_specs.channels_for_product(product, item["category"])


def _account(name: str) -> dict:
    from hub import suite_accounts
    return suite_accounts.location_for(name)


def _reading(name: str, today: date) -> dict:
    from hub import suite_email_stats
    return suite_email_stats.reading(name, today=today)


def _public(r: dict) -> dict | None:
    from hub import suite_email_stats
    return suite_email_stats.public_view(r)


def _norm(name: str) -> str:
    try:
        from hub import client_key as ck
        return ck.normalise_name(name)
    except Exception:                                   # noqa: BLE001
        return str(name or "").strip().lower()


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def gate(client_key: str, client_name: str = "") -> dict:
    """Whether this client gets the section, and why or why not.

    ``{"gated", "product", "account", "read", "products", "name",
    "location_id", "why": [...], "errors": [...]}``.
    """
    name = (client_name or "").strip()
    if not name:
        try:
            from hub import client_key as ck
            name = ck.key_label(client_key)
        except Exception:                               # noqa: BLE001
            name = client_key
    out = {"gated": False, "product": False, "account": False, "read": False,
           "products": [], "name": name, "location_id": "", "why": [], "errors": []}
    try:
        want = _norm(name)
        seen = []
        for row in _product_rows():
            if _norm(str(row.get("client") or "")) != want or not _running(row):
                continue
            if _is_email(row):
                p = str(row.get("product") or "").strip()
                if p and p not in seen:
                    seen.append(p)
        if seen:
            out["product"] = True
            out["products"] = seen
    except Exception as exc:                            # noqa: BLE001
        out["errors"].append(f"the product book could not be read ({type(exc).__name__})")
    try:
        acct = _account(name)
    except Exception as exc:                            # noqa: BLE001
        acct = {"state": "not_measured"}
        out["errors"].append(f"the Suite link could not be read ({type(exc).__name__})")
    if acct.get("state") == "connected":
        out["account"] = True
        out["location_id"] = str(acct.get("location_id") or "")
    if not out["product"]:
        out["why"].append("no live email product on the client's book")
    if not out["account"]:
        out["why"].append("no Smart 1 Suite sub-account is linked to the client")
    out["gated"] = out["product"] and out["account"]
    return out


# ---------------------------------------------------------------------------
# The section
# ---------------------------------------------------------------------------

def section(link, today: date | None = None, gate_: dict | None = None) -> dict | None:
    """The email block of the aggregate, or None when the client is not
    gated in. ``link`` is the ReportLink row."""
    today = today or date.today()
    g = gate_ or gate(link.client, link.client_name)
    if not g["gated"]:
        return None
    out = {"labels": dict(LABELS), "measured": False, "staff_note": "", "state": ""}
    try:
        r = _reading(g["name"], today)
    except Exception as exc:                            # noqa: BLE001
        out["staff_note"] = f"The campaign store could not be read ({type(exc).__name__})."
        return out
    card = _public(r)
    if not card:
        out["staff_note"] = r.get("staff_note") or r.get("error") or "No reading."
        out["state"] = r.get("state") or ""
        return out
    out.update(card)
    out["staff_note"] = r.get("staff_note") or ""
    out["state"] = "ok"
    return out


def public_view(block: dict | None) -> dict | None:
    """The block as the client's page and data.json carry it: nothing
    measured is no block at all, and the staff wording never travels."""
    if not block or not block.get("measured"):
        return None
    out = {k: v for k, v in block.items()}
    out.pop("staff_note", None)
    out.pop("state", None)
    return out


def staff_gate(client: str, name: str) -> dict:
    """The gate for the staff page's settings row, plus whether a reading
    exists. Never raises -- a gate that 500s the page it explains is worse
    than none."""
    try:
        g = gate(client, name)
    except Exception as exc:                            # noqa: BLE001
        g = {"gated": False, "product": False, "account": False, "read": False,
             "products": [], "name": name, "location_id": "",
             "why": [f"the gate could not be read ({type(exc).__name__})"], "errors": []}
    if g.get("account"):
        try:
            r = _reading(g.get("name") or name, date.today())
            g["read"] = r.get("state") == "ok"
            g["read_note"] = r.get("staff_note") or (
                "" if g["read"] else r.get("error") or "")
            g["as_of"] = r.get("as_of") or ""
            g["scope_missing"] = list((r.get("scopes") or {}).get("missing") or [])
        except Exception as exc:                        # noqa: BLE001
            g["errors"].append(f"the reading could not be taken ({type(exc).__name__})")
    return g
