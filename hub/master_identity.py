"""Permanent Smart 1 identity numbers for people and organizations.

A master ID is the durable join key for SmartHub. Names, roles, URLs, provider
IDs and other attributes may change; the master ID does not. The same person
can be both a salesperson and a partner without creating a second identity.

The store is intentionally conservative. It automatically resolves only hard
keys already attached to an identity (natural keys, external IDs, exact email
or exact organization domain). Name similarity belongs in the review-oriented
company identity layer and must never silently merge two master identities.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Iterable

from . import jsonstore

_VERSION = 1
_PREFIX = "S1-"
_VALID_KINDS = {"person", "organization"}
_VALID_ROLES = {"client", "salesperson", "partner", "contact", "vendor", "employee"}


class IdentityConflict(ValueError):
    """Raised when one hard identifier points at two master identities."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path() -> str:
    return os.path.join(jsonstore.data_root(), "master_identity.json")


def _empty() -> dict:
    return {
        "version": _VERSION,
        "next_number": 1,
        "entities": {},
        "indexes": {
            "natural_keys": {},
            "external_ids": {},
            "aliases": {},
            "domains": {},
            "emails": {},
        },
        "relationships": {},
        "conflicts": [],
        "runs": [],
        "updated_at": "",
    }


def _prepare(data) -> dict:
    if not isinstance(data, dict):
        data = _empty()
    base = _empty()
    for key, default in base.items():
        data.setdefault(key, copy.deepcopy(default))
    indexes = data.setdefault("indexes", {})
    for key, default in base["indexes"].items():
        indexes.setdefault(key, copy.deepcopy(default))
    try:
        data["next_number"] = max(1, int(data.get("next_number") or 1))
    except (TypeError, ValueError):
        data["next_number"] = 1
    return data


def _state() -> dict:
    return _prepare(jsonstore.read_json(_path(), default=_empty()))


def _save(mutator):
    def change(data):
        data = _prepare(data)
        out = mutator(data)
        if out is None:
            return None
        out["version"] = _VERSION
        out["updated_at"] = _now()
        return out

    return jsonstore.update_json(
        _path(), change, default=_empty(), durable=True, indent=2
    )


def _clean(value) -> str:
    return str(value or "").strip()


def _name_key(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", _clean(value).lower())
    return " ".join(text.split())


def _email(value: str) -> str:
    return _clean(value).lower()


def _domain(value: str) -> str:
    value = _clean(value)
    if not value:
        return ""
    try:
        from .client_context import canonical_domain
        return canonical_domain(value)
    except Exception:  # pragma: no cover - defensive boot fallback
        value = re.sub(r"^https?://", "", value.lower()).split("/", 1)[0]
        return value[4:] if value.startswith("www.") else value


def _source_key(source: str, source_id: str) -> str:
    return f"{_clean(source).lower()}:{_clean(source_id)}"


def _next_id(data: dict) -> str:
    number = int(data.get("next_number") or 1)
    entities = data.setdefault("entities", {})
    while f"{_PREFIX}{number:06d}" in entities:
        number += 1
    data["next_number"] = number + 1
    return f"{_PREFIX}{number:06d}"


def _list(values: Iterable | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out = []
    seen = set()
    for value in values:
        text = _clean(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _roles(values: Iterable | str | None) -> list[str]:
    roles = []
    for role in _list(values):
        role = role.lower()
        if role not in _VALID_ROLES:
            raise ValueError(f"Unsupported Smart 1 role: {role}")
        if role not in roles:
            roles.append(role)
    return roles


def _record_conflict(data: dict, *, key_type: str, key: str,
                     existing: str, attempted: str) -> None:
    row = {
        "at": _now(),
        "key_type": key_type,
        "key": key,
        "existing_master_id": existing,
        "attempted_master_id": attempted,
    }
    conflicts = data.setdefault("conflicts", [])
    if not any(
        x.get("key_type") == key_type
        and x.get("key") == key
        and x.get("existing_master_id") == existing
        and x.get("attempted_master_id") == attempted
        for x in conflicts[-200:]
    ):
        conflicts.append(row)
        data["conflicts"] = conflicts[-500:]


def _claim_index(data: dict, index_name: str, key: str, master_id: str,
                 *, strict: bool = True) -> bool:
    if not key:
        return False
    index = data.setdefault("indexes", {}).setdefault(index_name, {})
    current = index.get(key)
    if current and current != master_id:
        _record_conflict(
            data,
            key_type=index_name,
            key=key,
            existing=current,
            attempted=master_id,
        )
        if strict:
            raise IdentityConflict(
                f"{index_name} key {key!r} already belongs to {current}."
            )
        return False
    index[key] = master_id
    return True


def _resolve_hard_keys(data: dict, *, natural_key: str = "",
                       external_ids: dict | None = None,
                       emails: Iterable | str | None = None,
                       domains: Iterable | str | None = None) -> str:
    candidates = set()
    indexes = data.get("indexes") or {}
    if natural_key:
        found = (indexes.get("natural_keys") or {}).get(_clean(natural_key).lower())
        if found:
            candidates.add(found)
    for source, values in (external_ids or {}).items():
        for source_id in _list(values):
            found = (indexes.get("external_ids") or {}).get(
                _source_key(source, source_id)
            )
            if found:
                candidates.add(found)
    for value in _list(emails):
        found = (indexes.get("emails") or {}).get(_email(value))
        if found:
            candidates.add(found)
    for value in _list(domains):
        found = (indexes.get("domains") or {}).get(_domain(value))
        if found:
            candidates.add(found)
    if len(candidates) > 1:
        raise IdentityConflict(
            "Hard identifiers resolve to different Smart 1 master IDs: "
            + ", ".join(sorted(candidates))
        )
    return next(iter(candidates), "")


def _new_entity(master_id: str, *, entity_kind: str, canonical_name: str) -> dict:
    now = _now()
    return {
        "master_id": master_id,
        "entity_kind": entity_kind,
        "roles": [],
        "canonical_name": canonical_name,
        "aliases": [],
        "external_ids": {},
        "natural_keys": [],
        "domains": [],
        "urls": [],
        "emails": [],
        "phones": [],
        "metadata": {},
        "active": True,
        "created_at": now,
        "updated_at": now,
    }


def ensure_entity(*, entity_kind: str, canonical_name: str,
                  roles: Iterable | str | None = None,
                  natural_key: str = "", external_ids: dict | None = None,
                  aliases: Iterable | str | None = None,
                  domains: Iterable | str | None = None,
                  urls: Iterable | str | None = None,
                  emails: Iterable | str | None = None,
                  phones: Iterable | str | None = None,
                  metadata: dict | None = None) -> dict:
    """Create or enrich one identity and return its permanent master record.

    Existing hard identifiers win. A name by itself never joins identities.
    This keeps two people with the same name, or two related businesses at the
    same address, from being silently collapsed.
    """
    kind = _clean(entity_kind).lower()
    name = _clean(canonical_name)
    if kind not in _VALID_KINDS:
        raise ValueError("entity_kind must be 'person' or 'organization'.")
    if not name:
        raise ValueError("canonical_name is required.")
    wanted_roles = _roles(roles)
    wanted_aliases = _list(aliases)
    wanted_domains = [_domain(x) for x in _list(domains) if _domain(x)]
    wanted_urls = _list(urls)
    wanted_emails = [_email(x) for x in _list(emails) if _email(x)]
    wanted_phones = _list(phones)
    wanted_external = {
        _clean(source).lower(): _list(values)
        for source, values in (external_ids or {}).items()
        if _clean(source) and _list(values)
    }
    wanted_natural = _clean(natural_key).lower()
    result = {}

    def apply(data):
        nonlocal result
        master_id = _resolve_hard_keys(
            data,
            natural_key=wanted_natural,
            external_ids=wanted_external,
            emails=wanted_emails,
            domains=wanted_domains if kind == "organization" else None,
        )
        if not master_id:
            master_id = _next_id(data)
            data.setdefault("entities", {})[master_id] = _new_entity(
                master_id, entity_kind=kind, canonical_name=name
            )
        row = data.setdefault("entities", {}).get(master_id)
        if row is None:
            raise IdentityConflict(f"Index points to missing master ID {master_id}.")
        if row.get("entity_kind") and row.get("entity_kind") != kind:
            raise IdentityConflict(
                f"{master_id} is {row.get('entity_kind')}, not {kind}."
            )

        row["canonical_name"] = row.get("canonical_name") or name
        row["roles"] = sorted(set(row.get("roles") or []) | set(wanted_roles))
        row["aliases"] = sorted(
            set(row.get("aliases") or []) | set(wanted_aliases) | {name},
            key=str.lower,
        )
        row["domains"] = sorted(
            set(row.get("domains") or []) | set(wanted_domains)
        )
        row["urls"] = sorted(set(row.get("urls") or []) | set(wanted_urls))
        row["emails"] = sorted(set(row.get("emails") or []) | set(wanted_emails))
        row["phones"] = sorted(set(row.get("phones") or []) | set(wanted_phones))
        row["metadata"] = {**(row.get("metadata") or {}), **(metadata or {})}
        row["active"] = True
        row["updated_at"] = _now()

        if wanted_natural:
            if _claim_index(data, "natural_keys", wanted_natural, master_id):
                keys = set(row.get("natural_keys") or [])
                keys.add(wanted_natural)
                row["natural_keys"] = sorted(keys)
        for source, values in wanted_external.items():
            bucket = set((row.get("external_ids") or {}).get(source) or [])
            for source_id in values:
                _claim_index(
                    data, "external_ids", _source_key(source, source_id), master_id
                )
                bucket.add(source_id)
            row.setdefault("external_ids", {})[source] = sorted(bucket)
        for value in wanted_emails:
            _claim_index(data, "emails", value, master_id)
        if kind == "organization":
            for value in wanted_domains:
                _claim_index(data, "domains", value, master_id)
        # Alias collisions are recorded but not used to merge identities.
        for value in row.get("aliases") or []:
            _claim_index(
                data, "aliases", _name_key(value), master_id, strict=False
            )

        result = copy.deepcopy(row)
        return data

    _save(apply)
    return result


def get(master_id: str) -> dict | None:
    row = (_state().get("entities") or {}).get(_clean(master_id).upper())
    return copy.deepcopy(row) if row else None


def resolve(*, master_id: str = "", natural_key: str = "",
            source: str = "", source_id: str = "", email: str = "",
            domain: str = "", alias: str = "") -> dict | None:
    """Resolve a record to a master identity without fuzzy matching."""
    state = _state()
    entities = state.get("entities") or {}
    if master_id:
        return copy.deepcopy(entities.get(_clean(master_id).upper()))
    indexes = state.get("indexes") or {}
    probes = []
    if natural_key:
        probes.append(("natural_keys", _clean(natural_key).lower()))
    if source and source_id:
        probes.append(("external_ids", _source_key(source, source_id)))
    if email:
        probes.append(("emails", _email(email)))
    if domain:
        probes.append(("domains", _domain(domain)))
    if alias:
        probes.append(("aliases", _name_key(alias)))
    found = {
        (indexes.get(index_name) or {}).get(key)
        for index_name, key in probes if key
    }
    found.discard(None)
    found.discard("")
    if len(found) > 1:
        raise IdentityConflict(
            "Lookup keys resolve to different Smart 1 master IDs: "
            + ", ".join(sorted(found))
        )
    selected = next(iter(found), "")
    return copy.deepcopy(entities.get(selected)) if selected else None


def add_role(master_id: str, role: str) -> dict:
    row = get(master_id)
    if not row:
        raise KeyError(master_id)
    return ensure_entity(
        entity_kind=row["entity_kind"],
        canonical_name=row["canonical_name"],
        roles=[role],
        natural_key=(row.get("natural_keys") or [""])[0],
        external_ids=row.get("external_ids") or {},
        aliases=row.get("aliases") or [],
        domains=row.get("domains") or [],
        urls=row.get("urls") or [],
        emails=row.get("emails") or [],
        phones=row.get("phones") or [],
        metadata=row.get("metadata") or {},
    )


def ensure_salesperson(name: str, **kwargs) -> dict:
    roles = set(_roles(kwargs.pop("roles", None))) | {"salesperson"}
    return ensure_entity(
        entity_kind="person", canonical_name=name, roles=roles, **kwargs
    )


def ensure_partner(name: str, *, entity_kind: str = "organization", **kwargs) -> dict:
    roles = set(_roles(kwargs.pop("roles", None))) | {"partner"}
    return ensure_entity(
        entity_kind=entity_kind, canonical_name=name, roles=roles, **kwargs
    )


def add_relationship(from_master_id: str, relationship: str,
                     to_master_id: str, metadata: dict | None = None) -> dict:
    """Attach a durable relationship such as salesperson -> client."""
    source = _clean(from_master_id).upper()
    target = _clean(to_master_id).upper()
    relation = _clean(relationship).lower().replace(" ", "_")
    if not source or not target or not relation:
        raise ValueError("Both master IDs and relationship are required.")
    result = {}

    def apply(data):
        nonlocal result
        entities = data.get("entities") or {}
        if source not in entities or target not in entities:
            raise KeyError("Both sides of a relationship must have master IDs.")
        rid = hashlib.sha1(
            f"{source}|{relation}|{target}".encode("utf-8")
        ).hexdigest()[:20]
        old = (data.get("relationships") or {}).get(rid) or {}
        row = {
            "id": rid,
            "from_master_id": source,
            "relationship": relation,
            "to_master_id": target,
            "metadata": {**(old.get("metadata") or {}), **(metadata or {})},
            "created_at": old.get("created_at") or _now(),
            "updated_at": _now(),
        }
        data.setdefault("relationships", {})[rid] = row
        result = copy.deepcopy(row)
        return data

    _save(apply)
    return result


def relationships(master_id: str) -> list[dict]:
    mid = _clean(master_id).upper()
    return [
        copy.deepcopy(row)
        for row in (_state().get("relationships") or {}).values()
        if row.get("from_master_id") == mid or row.get("to_master_id") == mid
    ]


def attach_client_master_ids(rows: list[dict]) -> list[dict]:
    """Assign one permanent master ID to each canonical client in one write.

    Alias rows share the canonical row's existing ``key`` and therefore receive
    the same master ID. The source list is not merged or renamed.
    """
    if not rows:
        return rows
    canonical = {}
    for row in rows:
        if row.get("is_alias"):
            continue
        key = _clean(row.get("key"))
        name = _clean(row.get("name"))
        if key and name:
            canonical.setdefault(key, row)
    assigned = {}

    def apply(data):
        for key, row in canonical.items():
            natural_key = f"client:{key}".lower()
            indexes = data.setdefault("indexes", {})
            master_id = (indexes.setdefault("natural_keys", {})
                         .get(natural_key, ""))
            domain = _domain(row.get("domain") or row.get("url") or "")
            if not master_id and domain:
                possible = indexes.setdefault("domains", {}).get(domain, "")
                if possible:
                    entity = (data.get("entities") or {}).get(possible) or {}
                    if entity.get("entity_kind") == "organization":
                        master_id = possible
            if not master_id:
                master_id = _next_id(data)
                data.setdefault("entities", {})[master_id] = _new_entity(
                    master_id,
                    entity_kind="organization",
                    canonical_name=_clean(row.get("canonical_name") or row.get("name")),
                )
            entity = data["entities"][master_id]
            entity["roles"] = sorted(set(entity.get("roles") or []) | {"client"})
            entity["canonical_name"] = (
                entity.get("canonical_name")
                or _clean(row.get("canonical_name") or row.get("name"))
            )
            names = {_clean(row.get("name")), _clean(row.get("canonical_name"))}
            names.discard("")
            entity["aliases"] = sorted(
                set(entity.get("aliases") or []) | names, key=str.lower
            )
            entity["natural_keys"] = sorted(
                set(entity.get("natural_keys") or []) | {natural_key}
            )
            _claim_index(data, "natural_keys", natural_key, master_id)
            if domain:
                if _claim_index(data, "domains", domain, master_id, strict=False):
                    entity["domains"] = sorted(
                        set(entity.get("domains") or []) | {domain}
                    )
            url = _clean(row.get("url"))
            if url:
                entity["urls"] = sorted(set(entity.get("urls") or []) | {url})
            for name in names:
                _claim_index(
                    data, "aliases", _name_key(name), master_id, strict=False
                )
            entity["updated_at"] = _now()
            assigned[key] = master_id
        run = {
            "at": _now(),
            "kind": "client-registry",
            "canonical_clients": len(canonical),
            "assigned": len(assigned),
        }
        data.setdefault("runs", []).append(run)
        data["runs"] = data["runs"][-20:]
        return data

    _save(apply)
    for row in rows:
        key = _clean(row.get("canonical_key") or row.get("key"))
        if key in assigned:
            row["master_id"] = assigned[key]
    return rows


def status() -> dict:
    state = _state()
    entities = list((state.get("entities") or {}).values())
    by_role = {}
    by_kind = {}
    for row in entities:
        kind = row.get("entity_kind") or "unknown"
        by_kind[kind] = by_kind.get(kind, 0) + 1
        for role in row.get("roles") or []:
            by_role[role] = by_role.get(role, 0) + 1
    return {
        "entities": len(entities),
        "next_master_id": f"{_PREFIX}{int(state.get('next_number') or 1):06d}",
        "by_kind": by_kind,
        "by_role": by_role,
        "relationships": len(state.get("relationships") or {}),
        "conflicts": len(state.get("conflicts") or []),
        "updated_at": state.get("updated_at") or "",
        "last_run": (state.get("runs") or [])[-1] if state.get("runs") else None,
    }
