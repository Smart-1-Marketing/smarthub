"""Durable links from source records to Smart 1 master identities.

A master identity should *reference* source data, not copy it. QuickBooks,
Smart 1 Suite, reports, insertion orders and future tools remain authoritative
for their own fields; this store answers which S1-###### identity a source
record belongs to and which source records belong to an identity.

The same source record can have more than one relationship role (for example a
quote has a client and a salesperson), so role is part of the key.
"""
from __future__ import annotations

import copy
import hashlib
import os
from datetime import datetime, timezone

from . import jsonstore
from . import master_identity

_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value) -> str:
    return str(value or "").strip()


def _path() -> str:
    return os.path.join(jsonstore.data_root(), "master_links.json")


def _empty() -> dict:
    return {"version": _VERSION, "links": {}, "updated_at": ""}


def _state() -> dict:
    data = jsonstore.read_json(_path(), default=_empty())
    if not isinstance(data, dict):
        data = _empty()
    data.setdefault("version", _VERSION)
    data.setdefault("links", {})
    data.setdefault("updated_at", "")
    return data


def _key(system: str, record_type: str, record_id: str, role: str) -> str:
    raw = "|".join([
        _clean(system).lower(),
        _clean(record_type).lower(),
        _clean(record_id),
        _clean(role).lower(),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def link(*, master_id: str, system: str, record_type: str, record_id: str,
         role: str, source_label: str = "", evidence: str = "",
         actor: str = "") -> dict:
    """Link one source record to one identity for a named role.

    Re-linking the exact same source+role to a different master ID is refused.
    That change requires an explicit unlink/review operation rather than a
    later import silently stealing a record from the identity already using it.
    """
    mid = _clean(master_id).upper()
    system = _clean(system).lower()
    record_type = _clean(record_type).lower()
    record_id = _clean(record_id)
    role = _clean(role).lower()
    if not (mid and system and record_type and record_id and role):
        raise ValueError("master_id, system, record_type, record_id and role are required")
    if master_identity.get(mid) is None:
        raise KeyError(f"Unknown Smart 1 master ID {mid}")
    link_id = _key(system, record_type, record_id, role)
    result = {}

    def change(data):
        nonlocal result
        if not isinstance(data, dict):
            data = _empty()
        links = data.setdefault("links", {})
        existing = links.get(link_id) or {}
        old_mid = _clean(existing.get("master_id")).upper()
        if old_mid and old_mid != mid:
            raise master_identity.IdentityConflict(
                f"{system}:{record_type}:{record_id} ({role}) is already linked to {old_mid}."
            )
        row = {
            "id": link_id,
            "master_id": mid,
            "system": system,
            "record_type": record_type,
            "record_id": record_id,
            "role": role,
            "source_label": _clean(source_label)[:300],
            "evidence": _clean(evidence)[:300],
            "actor": _clean(actor)[:160],
            "created_at": existing.get("created_at") or _now(),
            "updated_at": _now(),
        }
        links[link_id] = row
        data["version"] = _VERSION
        data["updated_at"] = _now()
        result = copy.deepcopy(row)
        return data

    jsonstore.update_json(_path(), change, default=_empty(), durable=True, indent=2)
    return result


def resolve(*, system: str, record_type: str, record_id: str, role: str) -> dict | None:
    row = (_state().get("links") or {}).get(_key(system, record_type, record_id, role))
    return copy.deepcopy(row) if row else None


def for_master(master_id: str) -> list[dict]:
    mid = _clean(master_id).upper()
    rows = [
        copy.deepcopy(row)
        for row in (_state().get("links") or {}).values()
        if _clean(row.get("master_id")).upper() == mid
    ]
    rows.sort(key=lambda r: (r.get("system", ""), r.get("record_type", ""),
                             r.get("role", ""), r.get("record_id", "")))
    return rows


def unlink(*, system: str, record_type: str, record_id: str, role: str,
           expected_master_id: str = "") -> bool:
    """Explicitly remove a link, optionally refusing if ownership changed."""
    link_id = _key(system, record_type, record_id, role)
    removed = False

    def change(data):
        nonlocal removed
        if not isinstance(data, dict):
            data = _empty()
        links = data.setdefault("links", {})
        row = links.get(link_id)
        if not row:
            return None
        expected = _clean(expected_master_id).upper()
        if expected and _clean(row.get("master_id")).upper() != expected:
            raise master_identity.IdentityConflict(
                f"Link belongs to {row.get('master_id')}, not {expected}."
            )
        links.pop(link_id, None)
        data["updated_at"] = _now()
        removed = True
        return data

    jsonstore.update_json(_path(), change, default=_empty(), durable=True, indent=2)
    return removed


def status() -> dict:
    state = _state()
    links = list((state.get("links") or {}).values())
    by_system = {}
    by_role = {}
    for row in links:
        system = row.get("system") or "unknown"
        role = row.get("role") or "unknown"
        by_system[system] = by_system.get(system, 0) + 1
        by_role[role] = by_role.get(role, 0) + 1
    return {
        "links": len(links),
        "by_system": by_system,
        "by_role": by_role,
        "updated_at": state.get("updated_at") or "",
    }
