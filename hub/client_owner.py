"""
Smart 1 Hub — which member of staff owns which client.

Every report in this Hub answers a question about *the book*: which sites
nobody is billing, which campaigns are waiting on artwork, which quotes have
gone unopened. Not one of them could answer the question a person actually
opens the Hub with, which is **"what is on my desk?"** — because nothing
anywhere recorded whose desk a client was on. A rep read the whole list,
recognised four names, and the other hundred and fifty were somebody's.

This is that record: one client, one owner, stored as a small Hub overlay.

## Six rules, each a way this goes quietly wrong

**Nothing is written to Knack.** Knack owns the client record and this Hub
does not write to it — the rule `hub/client_urls.py` and `hub/client_groups.py`
both work to. Removing an assignment leaves the client record exactly as it
was.

**The client is stored by name and the user by email, never by the derived
key and never by a display name.** `hub/client_key.py` gives the first half at
length: a stored key outlives the matcher it was derived from, so the key is
re-derived on every read. The second half is `hub/celebrations.mine()`'s —
two people on this roster share a first name, so a display name identifies
nobody and an email identifies exactly one account.

**A client has at most one owner.** Two owners makes "whose client is this?"
unanswerable, which is the whole question the record exists to answer — the
rule `hub/client_groups.py` applies to a client being in two groups.
Reassigning is allowed and says who held it before, because a handover is the
ordinary case and a refusal there would send somebody to unassign first.

**A bulk assignment reports every row's own outcome.** `accept_many()` in
`hub/client_urls.py` says why: a bulk action that answers with one number
hides the two that failed.

**Assigning "at partner level" is expanded to the clients it names, now.** It
is deliberately *not* stored as a standing rule. A rule would silently claim
next year's clients for whoever was on the screen this year — including
somebody who has since left — and nothing on any record would say the
assignment had been made by a rule nobody remembers writing. So the partner
control is a way of *selecting* a group of clients, and what is written is
one ordinary assignment per client, each with the name of whoever pressed it
against it. What that costs is a re-press when the partner gains a client,
and the page says so rather than leaving somebody to discover it.

**An owner who has left is named, never dropped.** An assignment pointing at
an email that is no longer an account is reported as `unknown_owner` rather
than reading as unassigned: "nobody owns this" and "the person who owned this
no longer has an account" are different situations and only the second has a
handover behind it. Same rule `check_stale_json_exemptions()` works to.

Stored through `hub/jsonstore.py`, so it survives the Render disk being
handed back empty. Nothing in this module raises.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

from hub import jsonstore

_LOCK = threading.Lock()
_FILE = "owners.json"

MAX_NOTE = 300
# A bulk press is a selection somebody made on one screen. The cap is here so
# a scripted post cannot walk the whole book in one write while holding the
# lock; the page never offers more than a page of rows at a time.
MAX_BULK = 500


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _path() -> str:
    return os.path.join(jsonstore.data_dir("client_owner"), _FILE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(name: str) -> str:
    """The join key for a client name — exact, normalised, never a substring.

    Derived on every read. `hub/client_key.py` refuses a substring match for
    the reason this module would pay for hardest: "Riverside HVAC" collecting
    "Riverside HVAC Supply" would put one company's book on another rep's
    screen and read as a clean assignment.
    """
    try:
        from hub.client_key import normalise_name
        out = normalise_name(name)
        if out:
            return out
    except Exception:                                   # noqa: BLE001
        pass
    return str(name or "").strip().casefold()


def _load() -> list[dict]:
    rows = jsonstore.read_json(_path(), default=None)
    if isinstance(rows, dict):                          # {"owners": [...]}
        rows = rows.get("owners")
    if not isinstance(rows, list):
        return []
    return [r for r in rows
            if isinstance(r, dict)
            and str(r.get("client") or "").strip()
            and str(r.get("email") or "").strip()]


def _save(rows: list[dict]) -> bool:
    return jsonstore.write_json(_path(), {"owners": rows}, indent=2)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def assignments() -> list[dict]:
    """Every assignment, newest first. Never raises."""
    try:
        rows = _load()
    except Exception:                                   # noqa: BLE001
        return []
    return sorted(rows, key=lambda r: str(r.get("at") or ""), reverse=True)


def owners() -> dict[str, dict]:
    """`{client key: assignment}` — one owner per client, the newest winning.

    A file written with two rows for one client (two workers, one press each)
    resolves to the newest rather than to whichever came first in the list:
    the last decision is the one somebody made.
    """
    out: dict[str, dict] = {}
    for row in assignments():                           # newest first
        key = _key(row.get("client"))
        if key:
            out.setdefault(key, row)
    return out


def owner_of(client: str) -> dict | None:
    """The assignment for one client, or None. Never raises."""
    key = _key(client)
    return owners().get(key) if key else None


def clients_for(email: str) -> list[str]:
    """The client names assigned to one account, alphabetically."""
    want = normalise_email(email)
    if not want:
        return []
    names = [str(r.get("client") or "").strip() for r in owners().values()
             if normalise_email(r.get("email")) == want]
    return sorted({n for n in names if n}, key=str.lower)


def normalise_email(value) -> str:
    return str(value or "").strip().lower()


# ---------------------------------------------------------------------------
# Who can be assigned
# ---------------------------------------------------------------------------

def assignable_users() -> tuple[list[dict], str]:
    """`(users, error)` — the accounts a client may be assigned to.

    A pair rather than a bare list, for the reason `connected_accounts_result`
    gives in Google Finder: *nobody has an account* and *we could not read the
    accounts table* are different answers, and only the first would ever be
    true here. A picker drawn from an empty list over a table that would not
    answer is a screen saying this company employs nobody.

    The user table is asked first and the census roster is the fallback, and
    **which one answered is carried on every row** — an account created since
    the census, or one suspended since, is only in the first, so a page
    drawing the second is drawing a list that is right about most people and
    wrong about the one somebody is looking for. An empty table falls back
    too: on a deployment where `sync_roster()` has not run yet, an empty
    picker and an unreadable one look identical and neither is the truth.
    """
    rows, why = [], ""
    try:
        from hub.users import User
        rows = [{"email": normalise_email(u.email),
                 "name": (u.name or "").strip() or normalise_email(u.email),
                 "role": u.role or "member",
                 "status": u.status or "",
                 "active": (u.status or "") == "active",
                 "source": "accounts"}
                for u in User.query.order_by(User.name, User.email).all()]
        rows = [r for r in rows if r["email"]]
        if not rows:
            why = ("No Hub accounts have been created yet, so this is the "
                   "staff roster instead.")
    except Exception as exc:                            # noqa: BLE001
        why = (f"The account list could not be read ({type(exc).__name__}), "
               "so this is the staff roster instead.")
    if rows:
        return rows, ""

    try:
        from hub.user_directory import roster_rows
        fallback = [{"email": normalise_email(r["email"]), "name": r["name"],
                     "role": r["role"], "status": "", "active": True,
                     "source": "roster"}
                    for r in roster_rows()]
        fallback = [r for r in fallback if r["email"]]
    except Exception as exc:                            # noqa: BLE001
        return [], (why + " " if why else "") + \
            f"The staff roster could not be read either: {type(exc).__name__}"
    if not fallback:
        return [], why
    return fallback, (why + " An account added or suspended since the roster "
                            "was loaded is not on it.")


def user_index() -> dict[str, dict]:
    """`{email: user}` for whoever could be listed. Never raises."""
    users, _ = assignable_users()
    return {u["email"]: u for u in users}


def display_name(email: str, index: dict | None = None) -> str:
    """The name to print for an owner, or the email where there is no account.

    Never invents a name from the address: `todd@` is not "Todd" — it is an
    address this Hub has no account for, which is exactly what the reader
    needs to be told.
    """
    email = normalise_email(email)
    if not email:
        return ""
    idx = index if index is not None else user_index()
    hit = idx.get(email)
    return (hit or {}).get("name") or email


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _check(client: str, email: str) -> dict:
    """`{"ok": True, ...}` or the refusal. Shared by the one and the many.

    Written once rather than in each: a rule the single assignment keeps while
    the bulk one does not is not a rule, which is the note
    `hub/request_triage.py` makes about a gate on the form and not on the
    endpoint.
    """
    name = str(client or "").strip()
    addr = normalise_email(email)
    if not name:
        return {"ok": False, "client": name, "error": "No client named."}
    if not addr:
        return {"ok": False, "client": name, "error": "No account named."}
    if "@" not in addr:
        return {"ok": False, "client": name,
                "error": f"{addr} is not an email address."}
    return {"ok": True, "client": name, "email": addr}


def _apply(rows: list[dict], name: str, addr: str, actor: str,
           note: str) -> dict:
    """Put one assignment into `rows` in place. Returns what happened.

    Re-assigning is allowed and carries `previous`, because a handover is the
    ordinary case — refusing it would send somebody to unassign first and then
    assign, which is two presses to record one decision.
    """
    key = _key(name)
    previous = ""
    keep = []
    for r in rows:
        if _key(r.get("client")) == key:
            previous = previous or normalise_email(r.get("email"))
            continue
        keep.append(r)
    keep.append({
        "client": name,
        "email": addr,
        "previous": previous,
        "note": str(note or "")[:MAX_NOTE],
        "by": str(actor or "")[:120],
        "at": _now(),
    })
    rows[:] = keep
    return {"ok": True, "client": name, "email": addr, "previous": previous,
            "reassigned": bool(previous and previous != addr)}


def assign(client: str, email: str, *, actor: str = "", note: str = "") -> dict:
    """Give one client to one account. Returns `{"ok": …}` and never raises."""
    checked = _check(client, email)
    if not checked["ok"]:
        return checked
    name, addr = checked["client"], checked["email"]
    try:
        with _LOCK:
            rows = _load()
            out = _apply(rows, name, addr, actor, note)
            if not _save(rows):
                return {"ok": False, "client": name,
                        "error": "The assignment could not be saved. "
                                 "Nothing changed."}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "client": name,
                "error": f"The assignment could not be saved: {exc}"[:200]}
    return out


def unassign(client: str, *, actor: str = "") -> dict:
    """Take a client off whoever holds it. Never raises."""
    name = str(client or "").strip()
    if not name:
        return {"ok": False, "client": name, "error": "No client named."}
    key = _key(name)
    try:
        with _LOCK:
            rows = _load()
            keep = [r for r in rows if _key(r.get("client")) != key]
            if len(keep) == len(rows):
                # Not an error: the row is already in the state that was asked
                # for. Saying so is what stops a second press reading as a
                # failure on a screen two workers are answering.
                return {"ok": True, "client": name, "email": "", "already": True}
            if not _save(keep):
                return {"ok": False, "client": name,
                        "error": "The change could not be saved. "
                                 "Nothing changed."}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "client": name,
                "error": f"The change could not be saved: {exc}"[:200]}
    return {"ok": True, "client": name, "email": ""}


def _selection(clients) -> list[str]:
    """The names in a selection, deduplicated, in the order they arrived.

    A name repeated is written once: the page offers a partner's clients and a
    hand-picked list from the same control, and ticking a client that is
    already in the partner's set is not two decisions.
    """
    names, seen = [], set()
    for raw in list(clients or [])[:MAX_BULK]:
        name = str(raw or "").strip()
        key = _key(name)
        if not name or not key or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def assign_many(clients, email: str, *, actor: str = "",
                note: str = "") -> dict:
    """Assign a selection to one account, reporting each row separately.

    One number back would hide the two that failed — the rule
    `hub/client_urls.accept_many()` works to, and `hub/domain_links.py` gives
    at length.

    **One write, not one per client.** A partner here carries eighty-seven
    clients, and calling `assign()` in a loop would read and rewrite the whole
    file eighty-seven times — and `jsonstore` mirrors every write into the
    database, so that is eighty-seven round trips on one press. The refusals
    are still per row: what is shared is the file, not the answer.
    """
    names = _selection(clients)
    if not names:
        return {"ok": False, "results": [], "assigned": 0, "failed": 0,
                "error": "No clients were selected."}

    checked = [_check(n, email) for n in names]
    good = [c for c in checked if c["ok"]]
    results = [c for c in checked if not c["ok"]]

    if good:
        try:
            with _LOCK:
                rows = _load()
                for c in good:
                    results.append(_apply(rows, c["client"], c["email"],
                                          actor, note))
                if not _save(rows):
                    # Nothing landed, so nothing may be reported as landed.
                    results = [dict(c, ok=False,
                                    error="The assignments could not be saved. "
                                          "Nothing changed.")
                               for c in checked]
        except Exception as exc:                        # noqa: BLE001
            results = [dict(c, ok=False,
                            error=f"The assignments could not be saved: {exc}"[:200])
                       for c in checked]

    assigned = sum(1 for r in results if r.get("ok"))
    # The order the caller sent them in, so a page can line the answers up
    # against the rows somebody ticked.
    order = {n.strip().casefold(): i for i, n in enumerate(names)}
    results.sort(key=lambda r: order.get(str(r.get("client") or "").strip().casefold(),
                                         len(order)))
    return {
        "ok": assigned > 0,
        "results": results,
        "assigned": assigned,
        "failed": len(results) - assigned,
        "email": normalise_email(email),
        "skipped": max(0, len(list(clients or [])) - len(names)),
        "error": ("" if assigned else
                  "Nothing was assigned — every row was refused."),
    }


def unassign_many(clients, *, actor: str = "") -> dict:
    """Take a selection off whoever holds it, reporting each row separately."""
    names = _selection(clients)
    if not names:
        return {"ok": False, "results": [], "cleared": 0, "failed": 0,
                "error": "No clients were selected."}
    wanted = {_key(n) for n in names}
    results = [{"ok": True, "client": n, "email": ""} for n in names]
    try:
        with _LOCK:
            rows = _load()
            keep = [r for r in rows if _key(r.get("client")) not in wanted]
            if len(keep) != len(rows) and not _save(keep):
                results = [dict(r, ok=False,
                                error="The change could not be saved. "
                                      "Nothing changed.")
                           for r in results]
    except Exception as exc:                            # noqa: BLE001
        results = [dict(r, ok=False,
                        error=f"The change could not be saved: {exc}"[:200])
                   for r in results]
    cleared = sum(1 for r in results if r.get("ok"))
    return {"ok": cleared > 0, "results": results, "cleared": cleared,
            "failed": len(results) - cleared,
            "error": "" if cleared else "Nothing was changed."}


# The client-health report is deliberately **not** invalidated here.
#
# `hub/client_health._apply_overlay()` reads this file on every request and
# lays the owner onto the cached run, so an assignment made in one gunicorn
# worker is visible in the other immediately — no cache to be stale. Dropping
# the day's run would instead make the next page load rebuild the whole thing:
# the products, the creative audit, the proposal store and a batch of website
# audits, once per press, and a partner here carries eighty-seven clients.
# `hub/report_cache.py` states the general rule that a write drops what it
# changed; where an overlay can be applied on read, that beats dropping a
# cache, which is the arrangement `hub/creative_evergreen.py` arrived at.


# ---------------------------------------------------------------------------
# Partners — the selection, never a stored rule
# ---------------------------------------------------------------------------

def clients_by_partner() -> tuple[dict[str, list[str]], str]:
    """`({partner: [client names]}, error)` from the products on file.

    A media partner is a property of a client's *products*, not of the client,
    so a client billed under two partners appears under both — named twice
    rather than filed under whichever came out of the dict first, which is the
    guess `hub/client_key.py` refuses one table along. A client with no
    partner recorded is under `""` and the page labels it rather than dropping
    it: a client nobody has filed is exactly the one nobody has assigned.
    """
    try:
        from hub import knack_data
        rows = knack_data.products()
    except Exception as exc:                            # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"[:200]

    out: dict[str, list[str]] = {}
    seen: dict[str, set] = {}
    for r in rows or ():
        client = str(r.get("client") or "").strip()
        if not client:
            continue
        partner = str(r.get("partner") or "").strip()
        key = _key(client)
        bucket = seen.setdefault(partner, set())
        if key in bucket:
            continue
        bucket.add(key)
        out.setdefault(partner, []).append(client)
    for names in out.values():
        names.sort(key=str.lower)
    return out, ""


# ---------------------------------------------------------------------------
# The whole picture
# ---------------------------------------------------------------------------

def summary(clients=None) -> dict:
    """Who owns what, against the client list as it stands.

    `clients` is the client book — handed in rather than imported, so this
    cannot come to disagree with a report about which clients exist. Left out,
    the registry is read and a registry that would not answer is **named**:
    counting assignments against an empty book would report every client as
    assigned, which is a confident wrong answer of exactly the kind this
    codebase keeps having to undo.
    """
    error = ""
    if clients is None:
        try:
            from hub import clients_registry
            clients = clients_registry.all_clients()
        except Exception as exc:                        # noqa: BLE001
            clients, error = [], f"{type(exc).__name__}: {exc}"[:200]

    held = owners()
    index = user_index()
    rows, by_email = [], {}
    unassigned = 0
    for c in clients or ():
        name = str(c.get("name") or "").strip() if isinstance(c, dict) else str(c or "").strip()
        if not name:
            continue
        row = held.get(_key(name))
        email = normalise_email((row or {}).get("email"))
        if email:
            by_email.setdefault(email, []).append(name)
        else:
            unassigned += 1
        rows.append({
            "client": name,
            "email": email,
            "owner": display_name(email, index) if email else "",
            "known": bool(email and email in index),
            "at": str((row or {}).get("at") or ""),
            "by": str((row or {}).get("by") or ""),
        })

    # An assignment whose account is gone. Named rather than counted as
    # assigned: the client has an owner on paper and nobody in practice, which
    # is the one state a handover has to be able to find.
    unknown = sorted({r["email"] for r in rows if r["email"] and not r["known"]})
    return {
        "measured": not error,
        "error": error,
        "rows": sorted(rows, key=lambda r: r["client"].lower()),
        "counts": {
            "clients": len(rows),
            "assigned": len(rows) - unassigned,
            "unassigned": unassigned,
            "owners": len(by_email),
            "unknown_owners": len(unknown),
        },
        "by_owner": {e: sorted(v, key=str.lower) for e, v in by_email.items()},
        "unknown_owners": unknown,
    }
