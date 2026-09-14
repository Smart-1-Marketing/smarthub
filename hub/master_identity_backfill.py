"""Backfill permanent Smart 1 master IDs from existing SmartHub records.

Run with::

    python -m hub.master_identity_backfill

The job is intentionally conservative. Clients are fed through the canonical
client registry, Hub users are keyed by their immutable hub_users.id plus email,
and partner-like database tables are only imported when a durable row ID or
email is present. Names are descriptive data, never a merge key.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import inspect, text

from .extensions import shared_engine
from . import master_identity as identity


def _s(value: Any) -> str:
    return str(value or "").strip()


def sync_clients() -> dict:
    from . import clients_registry

    rows = clients_registry.all_clients(refresh=True)
    canonical = [r for r in rows if not r.get("is_alias")]
    assigned = {r.get("master_id") for r in canonical if r.get("master_id")}
    missing = [r.get("name") for r in canonical if not r.get("master_id")]
    return {
        "observed": len(canonical),
        "assigned": len(assigned),
        "missing": missing[:100],
    }


def sync_salespeople() -> dict:
    """Give every Hub user a person identity using hard account keys."""
    engine = shared_engine()
    inspector = inspect(engine)
    if "hub_users" not in inspector.get_table_names():
        return {"observed": 0, "assigned": 0, "skipped": 0}

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, email, name, status FROM hub_users ORDER BY id"
        )).mappings().all()

    assigned = 0
    skipped = 0
    for raw in rows:
        user_id = _s(raw.get("id"))
        email = _s(raw.get("email")).lower()
        name = _s(raw.get("name")) or (email.split("@", 1)[0] if email else "")
        if not user_id or not name:
            skipped += 1
            continue
        identity.ensure_salesperson(
            name,
            natural_key=f"hub-user:{user_id}",
            emails=[email] if email else [],
            external_ids={"hub_user": user_id},
            metadata={"hub_status": _s(raw.get("status"))},
        )
        assigned += 1
    return {"observed": len(rows), "assigned": assigned, "skipped": skipped}


def _first(columns: set[str], *choices: str) -> str:
    return next((x for x in choices if x in columns), "")


def sync_partners() -> dict:
    """Import explicit partner tables without guessing from ordinary contacts.

    Only a table whose name itself contains ``partner`` is considered. A row
    must have either an ID column or an email. This deliberately excludes a
    generic company/contact table with a partner-ish free-text field: a shared
    address, phone, contact name or similar company name is not identity.
    """
    engine = shared_engine()
    inspector = inspect(engine)
    observed = assigned = skipped = 0
    tables = []

    for table in inspector.get_table_names():
        if "partner" not in table.lower():
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        name_col = _first(cols, "partner_name", "company_name", "business_name", "name")
        id_col = _first(cols, "partner_id", "id", "uuid", "external_id")
        email_col = _first(cols, "partner_email", "email", "contact_email")
        domain_col = _first(cols, "domain", "website", "url")
        if not name_col or (not id_col and not email_col):
            continue

        select_cols = [name_col]
        for col in (id_col, email_col, domain_col):
            if col and col not in select_cols:
                select_cols.append(col)
        quoted = ", ".join(f'"{c}"' for c in select_cols)
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(
                    f'SELECT {quoted} FROM "{table}" LIMIT 10000'
                )).mappings().all()
        except Exception:
            continue

        tables.append(table)
        for raw in rows:
            observed += 1
            name = _s(raw.get(name_col))
            row_id = _s(raw.get(id_col)) if id_col else ""
            email = _s(raw.get(email_col)).lower() if email_col else ""
            domain = _s(raw.get(domain_col)) if domain_col else ""
            if not name or (not row_id and not email):
                skipped += 1
                continue
            # Most partner tables describe organizations. An email is attached
            # as corroborating data, but the durable table row ID is preferred.
            identity.ensure_partner(
                name,
                entity_kind="organization",
                natural_key=f"partner:{table}:{row_id}" if row_id else "",
                emails=[email] if email else [],
                domains=[domain] if domain else [],
                external_ids={f"partner_{table}": row_id} if row_id else {},
                metadata={"partner_source_table": table},
            )
            assigned += 1

    return {
        "tables": tables,
        "observed": observed,
        "assigned": assigned,
        "skipped": skipped,
    }


def run() -> dict:
    before = identity.status()
    result = {
        "clients": sync_clients(),
        "salespeople": sync_salespeople(),
        "partners": sync_partners(),
    }
    result["before"] = before
    result["after"] = identity.status()
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
