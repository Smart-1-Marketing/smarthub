"""360 Skills -- the per-client record of which skills are switched on.

One file per client under ``<data>/skills360/<slug>.json`` (hub/seo.py's
shape), every mutation through ``jsonstore.update_json`` so two reps editing
two different clients cannot drop each other's write on the way to disk.

Shape::

    {
      "client": "Buckeye Lake Winery",
      "key": "d:buckeyelakewinery.com",        # hub/client_key, for joins
      "skills": {
        "ecwid": {"active": true, "activated_at": ..., "activated_by": ...,
                  "store_id": "111281497", "token": {"enc": true, "data": ...},
                  "verified_at": ..., "store_name": "...", "store_url": "..."},
        "email": {"active": true, ..., "location_id": "...",
                  "from_name": "...", "from_email": "...",
                  "check": {...last send-readiness check...}}
      },
      "shares": [{"token": "...", "skill": "ecwid", "created": ..., "by": ...}],
      "events": [...]
    }

Secrets are stored the way hub/ghl_oauth.py stores the agency refresh token:
Fernet-encrypted with ``TOKEN_ENCRYPTION_KEY`` when that key is set, in the
clear (and flagged ``enc: false``) when it is not -- refusing to work without
the key would make this harder to adopt than pasting the key into an env
var, which is exactly the thing 360 Skills replaces. A secret is never
returned by ``get()``; callers that need it ask ``secret()`` and never put
the answer in a dict that reaches a page.
"""
from __future__ import annotations

import os
import re
import secrets as _secrets
from datetime import datetime, timezone

from hub import jsonstore

MAX_EVENTS = 100
MAX_SHARES = 20
_TOKEN_RE = re.compile(r"[^0-9A-Za-z_-]+")


class MutationFailed(Exception):
    """Raised inside an ``update_json`` mutate callback to say this specific
    write cannot happen (modules/weather_setup/store.py's rule) -- never to
    signal "nothing changed"."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dir() -> str:
    return jsonstore.data_dir("skills360")


def slug(client: str) -> str:
    try:
        from hub.clients_registry import slugify
        return slugify(client)
    except Exception:                                      # noqa: BLE001
        s = re.sub(r"[^a-z0-9]+", "-", str(client or "").lower()).strip("-")
        return s[:80] or "client"


def _path(client: str) -> str:
    return os.path.join(_dir(), slug(client) + ".json")


def _client_key(client: str, url: str = "") -> str:
    try:
        from hub.client_key import client_key
        return client_key(client, url)
    except Exception:                                      # noqa: BLE001
        return ""


def _blank(client: str) -> dict:
    return {"client": client, "key": "", "skills": {}, "shares": [], "events": []}


# ------------------------------------------------------------------ secrets
def _fernet():
    key = (os.environ.get("TOKEN_ENCRYPTION_KEY") or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode("utf-8"))
    except Exception:                                      # noqa: BLE001
        return None


def seal(value: str) -> dict:
    raw = str(value or "")
    f = _fernet()
    if f:
        return {"enc": True, "data": f.encrypt(raw.encode("utf-8")).decode("ascii")}
    return {"enc": False, "data": raw}


def unseal(blob) -> str:
    if not isinstance(blob, dict):
        return ""
    data = blob.get("data") or ""
    if not blob.get("enc"):
        return str(data)
    f = _fernet()
    if not f:
        return ""                    # key rotated away: re-enter the secret
    try:
        return f.decrypt(str(data).encode("ascii")).decode("utf-8")
    except Exception:                                      # noqa: BLE001
        return ""


def mask(value: str) -> str:
    v = str(value or "")
    if len(v) <= 8:
        return "*" * len(v)
    return v[:4] + "…" + v[-4:]


# ------------------------------------------------------------------ reads
def _raw(client: str) -> dict:
    try:
        row = jsonstore.read_json(_path(client), default=None)
    except Exception:                                      # noqa: BLE001
        row = None
    if not isinstance(row, dict):
        return _blank(client)
    row.setdefault("skills", {})
    row.setdefault("shares", [])
    row.setdefault("events", [])
    return row


def _public_skill(key: str, rec: dict) -> dict:
    """A skill row with every secret replaced by its mask."""
    out = {}
    for k, v in (rec or {}).items():
        if k == "token":
            out["token_set"] = bool(unseal(v)) or bool(isinstance(v, dict) and v.get("data"))
            out["token_masked"] = mask(unseal(v)) if unseal(v) else ""
            out["token_readable"] = bool(unseal(v)) if (isinstance(v, dict) and v.get("data")) else True
        else:
            out[k] = v
    out["key"] = key
    out.setdefault("active", False)
    return out


def get(client: str) -> dict:
    """The record with secrets masked. Never raises."""
    row = _raw(client)
    return {
        "client": row.get("client") or client,
        "key": row.get("key") or "",
        "skills": {k: _public_skill(k, v) for k, v in (row.get("skills") or {}).items()},
        "shares": list(row.get("shares") or []),
        "events": list(row.get("events") or [])[-20:],
    }


def skill(client: str, key: str) -> dict:
    return get(client)["skills"].get(key) or {"key": key, "active": False}


def active_keys(client: str) -> list[str]:
    """Which skills are switched on -- what Client 360 gates its cards by."""
    try:
        row = _raw(client)
    except Exception:                                      # noqa: BLE001
        return []
    return sorted(k for k, v in (row.get("skills") or {}).items()
                  if isinstance(v, dict) and v.get("active"))


def secret(client: str, key: str, field: str = "token") -> str:
    """A stored secret, for a server-side call and nothing else."""
    rec = (_raw(client).get("skills") or {}).get(key) or {}
    return unseal(rec.get(field))


# ------------------------------------------------------------------ writes
def _mutate(client: str, fn, url: str = ""):
    def mutate(row):
        if not isinstance(row, dict):
            row = _blank(client)
        row.setdefault("client", client)
        row.setdefault("skills", {})
        row.setdefault("shares", [])
        row.setdefault("events", [])
        if not row.get("key"):
            row["key"] = _client_key(client, url)
        fn(row)
        row["events"] = row["events"][-MAX_EVENTS:]
        return row
    return jsonstore.update_json(_path(client), mutate, default=_blank(client), indent=1)


def _event(row: dict, kind: str, by: str, **extra) -> None:
    ev = {"at": _now(), "type": kind, "by": (by or "")[:60]}
    ev.update({k: v for k, v in extra.items() if v is not None})
    row["events"].append(ev)


def set_skill(client: str, key: str, fields: dict, *, by: str = "",
              url: str = "", secret_fields: tuple[str, ...] = ()) -> dict:
    """Write configuration for one skill without changing whether it is
    active. Secret fields are sealed on the way in."""
    def fn(row):
        rec = row["skills"].setdefault(key, {"active": False})
        for k, v in (fields or {}).items():
            if k in secret_fields:
                if v:                          # blank keeps the stored one
                    rec[k] = seal(str(v))
            else:
                rec[k] = v
        rec["updated_at"] = _now()
        rec["updated_by"] = (by or "")[:60]
        _event(row, f"{key}_configured", by)
    _mutate(client, fn, url)
    return skill(client, key)


def activate(client: str, key: str, *, by: str = "", url: str = "",
             verified: dict | None = None) -> dict:
    def fn(row):
        rec = row["skills"].setdefault(key, {})
        rec["active"] = True
        rec["activated_at"] = _now()
        rec["activated_by"] = (by or "")[:60]
        if verified:
            rec["verified_at"] = _now()
            for k, v in verified.items():
                rec[k] = v
        _event(row, f"{key}_activated", by)
    _mutate(client, fn, url)
    return skill(client, key)


def deactivate(client: str, key: str, *, by: str = "", forget: bool = False) -> dict:
    def fn(row):
        rec = row["skills"].setdefault(key, {})
        rec["active"] = False
        rec["deactivated_at"] = _now()
        if forget:
            for k in list(rec.keys()):
                if k not in ("active", "deactivated_at"):
                    rec.pop(k, None)
            row["shares"] = [s for s in row["shares"] if s.get("skill") != key]
        _event(row, f"{key}_deactivated", by, forgot=forget or None)
    _mutate(client, fn)
    return skill(client, key)


# ------------------------------------------------------------------ shares
def new_token() -> str:
    return _secrets.token_urlsafe(24)


def add_share(client: str, key: str, *, by: str = "", label: str = "") -> dict:
    token = new_token()

    def fn(row):
        if len(row["shares"]) >= MAX_SHARES:
            raise MutationFailed("This client already has the maximum number of links. Revoke one first.")
        row["shares"].append({"token": token, "skill": key, "created": _now(),
                              "by": (by or "")[:60], "label": (label or "")[:80]})
        _event(row, "share_created", by, skill=key)
    try:
        _mutate(client, fn)
    except MutationFailed as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "token": token, "skill": key}


def revoke_share(client: str, token: str, *, by: str = "") -> dict:
    def fn(row):
        before = len(row["shares"])
        row["shares"] = [s for s in row["shares"] if s.get("token") != token]
        if len(row["shares"]) == before:
            raise MutationFailed("No such link.")
        _event(row, "share_revoked", by)
    try:
        _mutate(client, fn)
    except MutationFailed as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def resolve_share(token: str) -> dict | None:
    """``{"client", "skill", "share"}`` for a live link, or None.

    Exact token equality only -- a prefix match here would be a way to reach
    another client's dashboard (hub/suite_map.recorded_client's rule).
    """
    tok = _TOKEN_RE.sub("", str(token or ""))[:80]
    if not tok:
        return None
    try:
        names = sorted(os.listdir(_dir()))
    except OSError:
        return None
    for name in names:
        if not name.endswith(".json"):
            continue
        row = _raw(name[:-5])
        for s in row.get("shares") or []:
            if s.get("token") == tok:
                rec = (row.get("skills") or {}).get(s.get("skill")) or {}
                if not rec.get("active"):
                    return None            # a revoked skill retires its links
                return {"client": row.get("client") or name[:-5],
                        "skill": s.get("skill"), "share": s}
    return None


def all_active() -> list[dict]:
    """Every client with at least one skill on -- the tool's index."""
    try:
        names = sorted(os.listdir(_dir()))
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".json"):
            continue
        row = _raw(name[:-5])
        keys = [k for k, v in (row.get("skills") or {}).items()
                if isinstance(v, dict) and v.get("active")]
        if keys:
            out.append({"client": row.get("client") or name[:-5], "skills": keys,
                        "shares": len(row.get("shares") or [])})
    return out
