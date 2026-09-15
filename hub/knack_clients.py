"""Knack object_20 — the client roster's own Partner and Client Success fields.

Every other reading of "who is this client's media partner" in this Hub is
*derived* — `hub/client_owner.clients_by_partner()` reads it off the Media
Partner field on each **product** line, because that is where the billing
relationship actually lives and several reports (`qa.client_groups()`, the
prospect queue's chase bands) need exactly that reading. This module is a
different question: object_20 carries a Partner field **on the client record
itself** (`field_872`), which is the one a client can hold before they have a
single product on file, and it is what decides whether a client "needs
assigned" at all — a client already carried by a partner does not, because the
standing-rule machinery in `hub/client_owner.py` already resolves them the
moment that partner has an owner.

`field_3128` is the client's Client Success assignment — Brandon or Traci,
named as people rather than as a rule. It is not stored here at all: it is
read from Knack on every call and resolved to a Hub account by name, the same
way a standing rule resolves a partner to an owner, and never overwritten
locally, because Knack is the record of who is assigned and this Hub does not
second-guess it.

## The rules, each a way this goes quietly wrong

**Nothing is written to Knack.** This is a read-only mirror of one object, the
`hub/client_urls.py` rule: Knack owns the client record.

**A client is matched by name, never a substring.** The `hub/client_key.py`
rule, because "Riverside HVAC" must not collect "Riverside HVAC Supply".

**A failed read is named, never read as an empty book.** `rows()` returns
`([], error)` rather than `[]` alone — a Knack outage must not read as "no
client has a partner", which would un-assign the whole book on the page that
draws from it.

**Client Success is resolved by an exact name, or not at all.** The value in
Knack is a person's name ("Brandon", "Brandon Lipps", …); it is matched
against the Hub's own account roster on the *first name*, case-insensitively,
and only when that resolves to exactly one account. A name that matches nobody
or matches more than one is kept as the raw text and reported `known: False` —
naming somebody without an account behind them is the `unknown_owner` rule one
field over, not a guess at which of two people was meant.
"""
from __future__ import annotations

import os
import re
import time

OBJECT = os.environ.get("KNACK_CLIENTS_OBJECT", "object_20")
F_PARTNER = os.environ.get("KNACK_CLIENT_PARTNER_FIELD", "field_872")
F_CLIENT_SUCCESS = os.environ.get("KNACK_CLIENT_SUCCESS_FIELD", "field_3128")

_CACHE: dict = {"at": 0.0, "rows": []}
_CACHE_SECONDS = 60
_STATE: dict = {"error": ""}


def configured() -> bool:
    return bool((os.environ.get("KNACK_APP_ID") or "").strip()
                and (os.environ.get("KNACK_API_KEY") or "").strip())


def _headers() -> dict:
    return {"X-Knack-Application-Id": (os.environ.get("KNACK_APP_ID") or "").strip(),
            "X-Knack-REST-API-Key": (os.environ.get("KNACK_API_KEY") or "").strip(),
            "Accept": "application/json", "Content-Type": "application/json"}


def _text(v) -> str:
    """Knack returns strings, dicts, or lists of connection objects."""
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(_text(x) for x in v if x).strip(", ")
    if isinstance(v, dict):
        for k in ("identifier", "label", "name", "value", "url", "email"):
            if v.get(k):
                return str(v[k]).strip()
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    s = re.sub(r"<[^>]+>", " ", str(v))
    return re.sub(r"\s+", " ", s).strip()


def _identifier() -> str | None:
    """Object 20's own display field — the client's name.

    Discovered rather than pinned, the way `knack_api._object_identifier`
    already is for a connection's target object: object_20 is not named here
    with a field id of its own because nobody has confirmed one, and Knack
    publishes which field is the identifier on every object.
    """
    key = "ident"
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    ident = None
    try:
        import requests
        r = requests.get(f"https://api.knack.com/v1/objects/{OBJECT}",
                         headers=_headers(), timeout=20)
        if r.ok:
            ident = ((r.json() or {}).get("object") or {}).get("identifier")
    except Exception:                                       # noqa: BLE001
        ident = None
    if not ident:
        try:
            from hub.knack_api import object_fields
            for f in object_fields(OBJECT):
                if f.get("type") in ("name", "short_text"):
                    ident = f.get("key")
                    break
        except Exception:                                   # noqa: BLE001
            ident = None
    _CACHE[key] = ident
    return ident


def forget() -> None:
    """Drop the read cache. There is no write path here to call it from
    automatically, so this exists for a caller that knows a record changed."""
    _CACHE["at"] = 0.0
    _CACHE["rows"] = []


def last_error() -> str:
    return _STATE.get("error", "")


def rows(limit: int = 3000, refresh: bool = False) -> list[dict]:
    """Every client record on object_20, normalised. Never raises.

    `{"client": name, "partner": text, "client_success_raw": text}` per row.
    Cached for a minute — this is read on every open of Client 360 as well as
    the client-owners page, and a paged pull of the whole roster per page load
    is the cost `knack_websites.rows()` already refuses to pay.
    """
    if not refresh and _CACHE["rows"] and time.time() - _CACHE["at"] < _CACHE_SECONDS:
        return _CACHE["rows"][:limit]
    if not configured():
        _STATE["error"] = ("KNACK_APP_ID / KNACK_API_KEY are not set on this "
                           "deployment, so the client roster was not read.")
        return []
    ident = _identifier()
    if not ident:
        _STATE["error"] = (f"{OBJECT} publishes no name field this Hub can "
                           "find, so its rows could not be read.")
        return []
    import requests
    out, page = [], 1
    headers = _headers()
    _STATE["error"] = ""
    while len(out) < limit and page <= 30:
        try:
            r = requests.get(
                f"https://api.knack.com/v1/objects/{OBJECT}/records",
                headers=headers, params={"page": page, "rows_per_page": 100},
                timeout=25)
            if not r.ok:
                _STATE["error"] = f"Knack returned HTTP {r.status_code}."
                break
            data = r.json()
        except Exception as exc:                            # noqa: BLE001
            _STATE["error"] = f"Knack was unreachable ({type(exc).__name__})."
            break
        recs = data.get("records") or []
        if not recs:
            break
        for rec in recs:
            name = _text(rec.get(f"{ident}_raw")) or _text(rec.get(ident))
            if not name:
                continue
            out.append({
                "client": name,
                "partner": _text(rec.get(f"{F_PARTNER}_raw"))
                           or _text(rec.get(F_PARTNER)),
                "client_success_raw": _text(rec.get(f"{F_CLIENT_SUCCESS}_raw"))
                                      or _text(rec.get(F_CLIENT_SUCCESS)),
            })
        if len(recs) < 100:
            break
        page += 1
    if out:
        _CACHE["rows"], _CACHE["at"] = out, time.time()
    return out[:limit]


def clients_by_partner() -> tuple[dict[str, list[str]], str]:
    """`({partner: [client names]}, error)` — object_20's own Partner field.

    Deliberately the same shape `hub/client_owner.clients_by_partner()`
    returns from the products table, so it can be handed straight into
    `hub/client_owner.py`'s existing `partner_map=` argument and every rule
    that already exists there — the standing rules, `contested`, the
    provenance on each row — applies unchanged. Only the source of the
    mapping moves.
    """
    recs = rows()
    err = last_error()
    if err and not recs:
        return {}, err
    out: dict[str, list[str]] = {}
    seen: dict[str, set] = {}
    for r in recs:
        client = str(r.get("client") or "").strip()
        partner = str(r.get("partner") or "").strip()
        if not client:
            continue
        from hub.client_key import normalise_name
        key = normalise_name(client) or client.strip().casefold()
        bucket = seen.setdefault(partner, set())
        if key in bucket:
            continue
        bucket.add(key)
        out.setdefault(partner, []).append(client)
    for names in out.values():
        names.sort(key=str.lower)
    return out, ""


def _split_names(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,/;]|\band\b", raw or "", flags=re.I)
            if p.strip()]


def _match_user(name: str, users: list[dict]) -> dict | None:
    """The one account this name can only mean, or None.

    Matched on the account's own first name, case-insensitively — the value in
    Knack is a bare first name as often as a full one ("Brandon" as well as
    "Brandon Lipps") — and only when exactly one account answers to it. Two
    accounts sharing a first name would make this a guess, which is the
    `hub/client_key.py` rule this Hub refuses everywhere else.
    """
    want = re.sub(r"\s+", " ", str(name or "")).strip().casefold()
    if not want:
        return None
    exact = [u for u in users if u.get("name", "").strip().casefold() == want]
    if len(exact) == 1:
        return exact[0]
    first_of = lambda u: (u.get("name") or "").split(" ")[0].strip().casefold()
    by_first = [u for u in users if first_of(u) == want.split(" ")[0]]
    if len(by_first) == 1:
        return by_first[0]
    return None


def resolve_client_success(raw: str) -> dict:
    """One name off field_3128, resolved to a Hub account. Never raises.

    `{"raw": str, "email": str, "name": str, "known": bool}`. `known` is
    False for a name that matched nobody or matched more than one account —
    that is named rather than guessed at, the way an owner with no Hub account
    is named rather than read as unassigned.
    """
    raw = str(raw or "").strip()
    if not raw:
        return {"raw": "", "email": "", "name": "", "known": False}
    try:
        from hub import client_owner
        users, _err = client_owner.assignable_users()
    except Exception:                                       # noqa: BLE001
        users = []
    # A field can carry more than one name ("Brandon or Traci" was typed
    # literally on a handful of records during the changeover); the first one
    # that resolves to an account is used, and the raw text is always kept so
    # a record that resolves to nobody still says what Knack holds.
    for candidate in _split_names(raw) or [raw]:
        hit = _match_user(candidate, users)
        if hit:
            return {"raw": raw, "email": hit["email"], "name": hit["name"],
                    "known": True}
    return {"raw": raw, "email": "", "name": "", "known": False}


def client_success_map() -> tuple[dict[str, dict], str]:
    """`{client key: resolve_client_success() result}` for the whole roster.

    Read once and resolved once rather than per client — `resolve_client_success`
    reads the account roster, and doing that per row of a several-hundred-row
    book is the same cost `hub/client_owner.partner_index()` already refuses
    to pay per client.
    """
    recs = rows()
    err = last_error()
    if err and not recs:
        return {}, err
    try:
        from hub import client_owner
        users, _err = client_owner.assignable_users()
    except Exception:                                       # noqa: BLE001
        users = []
    from hub.client_key import normalise_name
    out: dict[str, dict] = {}
    for r in recs:
        client = str(r.get("client") or "").strip()
        raw = str(r.get("client_success_raw") or "").strip()
        if not client or not raw:
            continue
        key = normalise_name(client) or client.strip().casefold()
        resolved = None
        for candidate in _split_names(raw) or [raw]:
            hit = _match_user(candidate, users)
            if hit:
                resolved = {"raw": raw, "email": hit["email"],
                           "name": hit["name"], "known": True}
                break
        out[key] = resolved or {"raw": raw, "email": "", "name": "", "known": False}
    return out, ""


def partner_for(client: str) -> str:
    """One client's Partner, off object_20 alone. "" where there is none or
    the read failed — a caller that needs to tell those apart uses `rows()`."""
    from hub.client_key import normalise_name
    key = normalise_name(client) or str(client or "").strip().casefold()
    for r in rows():
        rkey = normalise_name(r.get("client") or "") or str(r.get("client") or "").strip().casefold()
        if rkey == key:
            return str(r.get("partner") or "").strip()
    return ""
