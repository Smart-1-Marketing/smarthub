"""
Smart 1 Hub — which member of staff owns which client.

Every report in this Hub answers a question about *the book*: which sites
nobody is billing, which campaigns are waiting on artwork, which quotes have
gone unopened. Not one of them could answer the question a person actually
opens the Hub with, which is **"what is on my desk?"** — because nothing
anywhere recorded whose desk a client was on. A rep read the whole list,
recognised four names, and the other hundred and fifty were somebody's.

This is that record: one client, one owner, stored as a small Hub overlay.

## The rules, each a way this goes quietly wrong

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

**A media partner can be assigned as a standing rule, and the rule is
resolved on read rather than written into rows.** "Everything Moto Media
carries belongs to Erik" is the ordinary shape of this book, and expanding it
into one row per client meant a re-press every time a partner gained one —
which in practice means the new client belongs to nobody until somebody
notices. `RULES` is that decision stored once.

What makes a standing rule safe is that it can never quietly become the only
account of who owns a client. Four things follow, and each is a way this goes
wrong otherwise:

  * **A rule materialises nothing.** `resolved()` lays it over the direct
    assignments on every read, so a rule that changes, or a client that leaves
    the partner, takes effect at once and leaves no orphan row behind. The
    same reason `hub/creative_evergreen.py` applies its mark on read: two
    gunicorn workers, and rows written by one of them are a second account of
    the truth.

  * **Every row says where its owner came from.** `source` is `direct` or
    `rule`, and a rule names the partner it came from. The objection to a
    standing rule is that somebody ends up holding a client with nothing
    anywhere saying why; carrying the provenance on the row is the answer to
    it, and the screens print it.

  * **A person beats a rule, in both directions.** A direct assignment on one
    client wins over any rule, and taking a client off everyone *sticks*:
    where a rule would otherwise re-claim it, `unassign()` writes an explicit
    "nobody" rather than deleting the row, or the button would appear to do
    nothing on the next read. `follow_rule()` is how that is undone.

  * **Two rules claiming one client is named and refused.** A client billed
    under two media partners is under both rules, and picking between them is
    picking whose book a client is on. `resolved()` leaves them unassigned,
    marks them `contested` and names both partners — the rule
    `hub/suite_accounts.py` applies to two rows naming different sub-accounts,
    and `hub/client_key.py` to a name two clients answer to.

A **one-off bulk assignment** stays beside it, because the two are different
statements: "these forty clients are Erik's" is a fact about those forty, and
"whatever this partner carries is Erik's" is a fact about the partner. Only
the second follows the book as it changes.

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
# Standing partner rules, in a file of their own rather than as rows in the
# one above. They are different kinds of statement -- one is about a named
# client and the other about whatever a partner carries -- and keeping them
# apart is what lets `resolved()` say which of the two answered for a row.
_RULES_FILE = "partner_rules.json"

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
    # An empty email is kept: it is an explicit "nobody owns this", written
    # to stop a standing rule re-claiming a client somebody has deliberately
    # taken off everyone. Dropping it here would make that press appear to do
    # nothing on the next read, which is the failure `hub/client_urls.missing()`
    # had to undo.
    return [r for r in rows
            if isinstance(r, dict) and str(r.get("client") or "").strip()]


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


def owner_of(client: str, *, partner_map=None) -> dict | None:
    """Who owns one client, direct row or standing rule. None if nobody.

    The resolution rather than the raw row: a client owned through a partner
    rule is owned, and a reader handed `None` for one would go and assign them
    a second time.
    """
    key = _key(client)
    if not key:
        return None
    hit = resolved([client], partner_map=partner_map).get(key)
    return hit if hit and hit.get("email") else None


def clients_for(email: str, *, partner_map=None) -> list[str]:
    """The client names one account owns, alphabetically -- rules included."""
    want = normalise_email(email)
    if not want:
        return []
    names = [str(r.get("client") or "").strip()
             for r in resolved(partner_map=partner_map).values()
             if r.get("email") == want]
    return sorted({n for n in names if n}, key=str.lower)


def normalise_email(value) -> str:
    return str(value or "").strip().lower()


# ---------------------------------------------------------------------------
# Standing partner rules
# ---------------------------------------------------------------------------
#
# "Everything this media partner carries belongs to X." Stored once, applied
# on read, and never expanded into rows: a rule that wrote one assignment per
# client would go on claiming a client after it left the partner, and would
# have to be re-pressed every time the partner gained one -- which is the
# whole reason for having a rule.

def _rules_path() -> str:
    return os.path.join(jsonstore.data_dir("client_owner"), _RULES_FILE)


def _partner_key(name: str) -> str:
    """Partner names fold on case, the way `qa._join()` folds them.

    Knack holds "MOTO" and "Moto" as separate values for one company, so a
    rule written against either has to answer for both -- otherwise a rule
    reads as covering a partner while half their clients sit outside it.
    """
    return " ".join(str(name or "").split()).casefold()


def _load_rules() -> list[dict]:
    rows = jsonstore.read_json(_rules_path(), default=None)
    if isinstance(rows, dict):
        rows = rows.get("rules")
    if not isinstance(rows, list):
        return []
    return [r for r in rows
            if isinstance(r, dict)
            and str(r.get("partner") or "").strip()
            and str(r.get("email") or "").strip()]


def _save_rules(rows: list[dict]) -> bool:
    return jsonstore.write_json(_rules_path(), {"rules": rows}, indent=2)


def rules() -> list[dict]:
    """Every standing rule, newest first. Never raises."""
    try:
        rows = _load_rules()
    except Exception:                                   # noqa: BLE001
        return []
    return sorted(rows, key=lambda r: str(r.get("at") or ""), reverse=True)


def rules_by_partner() -> dict[str, dict]:
    """`{folded partner name: rule}` — one rule per partner, newest winning."""
    out: dict[str, dict] = {}
    for row in rules():                                 # newest first
        key = _partner_key(row.get("partner"))
        if key:
            out.setdefault(key, row)
    return out


def set_rule(partner: str, email: str, *, actor: str = "",
             note: str = "") -> dict:
    """Everything this partner carries belongs to this account, from now on.

    Replacing a rule carries `previous`, like a reassignment: a partner
    changing hands is the ordinary case, and the row that records it is the
    only place anybody can later find out that it did.
    """
    name = " ".join(str(partner or "").split())
    addr = normalise_email(email)
    if not name:
        return {"ok": False, "partner": name, "error": "No partner named."}
    if not addr or "@" not in addr:
        return {"ok": False, "partner": name,
                "error": f"{addr or 'That'} is not an email address."}
    key = _partner_key(name)
    try:
        with _LOCK:
            rows = _load_rules()
            previous = ""
            keep = []
            for r in rows:
                if _partner_key(r.get("partner")) == key:
                    previous = previous or normalise_email(r.get("email"))
                    continue
                keep.append(r)
            keep.append({
                "partner": name, "email": addr, "previous": previous,
                "note": str(note or "")[:MAX_NOTE],
                "by": str(actor or "")[:120], "at": _now(),
            })
            if not _save_rules(keep):
                return {"ok": False, "partner": name,
                        "error": "The rule could not be saved. Nothing changed."}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "partner": name,
                "error": f"The rule could not be saved: {exc}"[:200]}
    return {"ok": True, "partner": name, "email": addr, "previous": previous,
            "replaced": bool(previous and previous != addr)}


def clear_rule(partner: str, *, actor: str = "") -> dict:
    """Drop a standing rule. Never raises.

    The clients it was claiming go back to unassigned on the next read, unless
    somebody has assigned them directly. Nothing has to be undone row by row,
    which is the point of never having written the rows.
    """
    name = " ".join(str(partner or "").split())
    if not name:
        return {"ok": False, "partner": name, "error": "No partner named."}
    key = _partner_key(name)
    try:
        with _LOCK:
            rows = _load_rules()
            keep = [r for r in rows if _partner_key(r.get("partner")) != key]
            if len(keep) == len(rows):
                return {"ok": True, "partner": name, "already": True}
            if not _save_rules(keep):
                return {"ok": False, "partner": name,
                        "error": "The change could not be saved. "
                                 "Nothing changed."}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "partner": name,
                "error": f"The change could not be saved: {exc}"[:200]}
    return {"ok": True, "partner": name}


# ---------------------------------------------------------------------------
# Resolution: the direct assignments, with the rules laid over them
# ---------------------------------------------------------------------------

def partner_index(mapping=None) -> tuple[dict[str, list[str]], dict[str, str], str]:
    """`({client key: [partners]}, {client key: name}, error)`.

    `mapping` is `clients_by_partner()`'s answer, handed in where the caller
    already has it: `hub/client_health.build()` reads the same products for
    its own grouping, and a second pull there would be a second reading of one
    question.

    A client billed under two partners is under both, named twice rather than
    filed under whichever came out of the dict first — which is what makes
    `contested` below possible to detect at all.

    The **names** come back beside the keys because the key is normalised and
    a caller that has only met a client through a rule has nowhere else to
    learn what they are actually called: `clients_for()` would otherwise
    answer with a list of blanks.
    """
    error = ""
    if mapping is None:
        mapping, error = clients_by_partner()
    out: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for partner, clients in (mapping or {}).items():
        if not str(partner or "").strip():
            continue                                    # no partner recorded
        for name in clients or ():
            key = _key(name)
            if not key:
                continue
            if partner not in out.setdefault(key, []):
                out[key].append(partner)
            if name and not names.get(key):
                names[key] = str(name)
    return out, names, error


def _blank(name: str = "") -> dict:
    return {"client": name, "email": "", "source": "", "partner": "",
            "contested": [], "at": "", "by": "", "pinned": False}


def resolved(clients=None, *, partner_map=None) -> dict[str, dict]:
    """`{client key: resolution}` — who owns each client, and how.

    Resolution order, and every step of it is a decision somebody could
    otherwise not account for:

      1. **A direct row wins**, including one with an empty email, which is an
         explicit "nobody owns this" and is what stops a standing rule
         re-claiming a client somebody has taken off everyone.
      2. **Otherwise the rules for the partners this client is carried by.**
         Exactly one distinct owner among them is the answer, and the row says
         which partner it came from.
      3. **Two rules naming different people leave the client unassigned**,
         marked `contested` with both partners named. Picking between them is
         picking whose book a client is on, which is not a thing this file
         decides — the rule `hub/suite_accounts.py` applies to two rows naming
         different sub-accounts.

    `clients` bounds the answer to a book; left out, it answers for every
    client any row or rule reaches. Never raises.
    """
    try:
        direct = owners()
    except Exception:                                   # noqa: BLE001
        direct = {}
    try:
        by_partner = rules_by_partner()
    except Exception:                                   # noqa: BLE001
        by_partner = {}

    # The products are only read when there is a rule to apply. A book with no
    # standing rules must not pay for a pull it cannot use.
    index: dict[str, list[str]] = {}
    index_names: dict[str, str] = {}
    if by_partner:
        index, index_names, _err = partner_index(partner_map)

    # A key can be reached three ways and only some of them know the client's
    # actual name. A non-empty one always wins: the key is normalised, so a
    # row that kept "" here would hand `clients_for()` a blank for every
    # client owned through a rule.
    keys: dict[str, str] = {}

    def _remember(key: str, name) -> None:
        name = str(name or "").strip()
        if not key:
            return
        if name or key not in keys:
            keys[key] = name or keys.get(key, "")

    for key, row in direct.items():
        _remember(key, row.get("client"))
    for key in index:
        _remember(key, index_names.get(key))
    for name in (clients or ()):
        nm = name.get("name") if isinstance(name, dict) else name
        _remember(_key(nm), nm)
    if clients is not None:
        wanted = {_key(c.get("name") if isinstance(c, dict) else c)
                  for c in clients}
        keys = {k: v for k, v in keys.items() if k in wanted}

    out: dict[str, dict] = {}
    for key, name in keys.items():
        row = direct.get(key)
        if row is not None:
            email = normalise_email(row.get("email"))
            out[key] = {
                "client": str(row.get("client") or name),
                "email": email,
                # An empty email is a decision, and it says so: "nobody owns
                # this" and "nobody has got to this yet" are different states
                # and only the second is somebody's to fix.
                "source": "direct" if email else "",
                "partner": "", "contested": [],
                "at": str(row.get("at") or ""), "by": str(row.get("by") or ""),
                "pinned": not email,
            }
            continue

        claims: dict[str, list[str]] = {}
        for partner in index.get(key) or ():
            rule = by_partner.get(_partner_key(partner))
            if rule:
                claims.setdefault(normalise_email(rule.get("email")),
                                  []).append(partner)
        if len(claims) == 1:
            email, partners = next(iter(claims.items()))
            rule = by_partner.get(_partner_key(partners[0])) or {}
            out[key] = {
                "client": name, "email": email, "source": "rule",
                "partner": ", ".join(sorted(partners, key=str.lower)),
                "contested": [],
                "at": str(rule.get("at") or ""), "by": str(rule.get("by") or ""),
                "pinned": False,
            }
        elif len(claims) > 1:
            out[key] = dict(_blank(name), contested=sorted(
                {p for ps in claims.values() for p in ps}, key=str.lower))
        else:
            out[key] = _blank(name)
    return out


def rule_for_client(client: str, *, partner_map=None) -> dict | None:
    """The standing rule that would claim one client, ignoring direct rows.

    What `unassign()` asks before it decides whether taking a client off
    somebody means deleting the row or writing an explicit "nobody".
    """
    key = _key(client)
    if not key:
        return None
    try:
        by_partner = rules_by_partner()
    except Exception:                                   # noqa: BLE001
        return None
    if not by_partner:
        return None                                     # no rule, no pull
    index, _names, _err = partner_index(partner_map)
    for partner in index.get(key) or ():
        rule = by_partner.get(_partner_key(partner))
        if rule:
            return dict(rule, partner=partner)
    return None


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


def _unassign_rows(rows: list[dict], name: str, actor: str,
                   pin: bool) -> dict:
    """Take one client off whoever holds it, in `rows`, in place.

    `pin` is the whole subtlety. With a standing rule covering this client,
    deleting the row hands them straight back to the rule on the next read --
    a button that appears to do nothing, which is the failure
    `hub/client_urls.missing()` had to undo. So it writes an explicit
    "nobody" instead, and says so. With no rule in play the row is simply
    removed, because a pin nothing is overriding is a row that reads as a
    decision somebody made about a client nobody was claiming.
    """
    key = _key(name)
    keep = [r for r in rows if _key(r.get("client")) != key]
    changed = len(keep) != len(rows)
    was_pinned = any(r for r in rows
                     if _key(r.get("client")) == key
                     and not normalise_email(r.get("email")))
    if pin:
        keep.append({"client": name, "email": "", "previous": "",
                     "note": "", "by": str(actor or "")[:120], "at": _now()})
    rows[:] = keep
    return {"ok": True, "client": name, "email": "", "pinned": bool(pin),
            "already": (not changed and not pin) or (was_pinned and pin)}


def unassign(client: str, *, actor: str = "", partner_map=None) -> dict:
    """Take a client off whoever holds it. Never raises.

    Where a standing rule would otherwise claim them back, this records an
    explicit "nobody owns this" rather than deleting the row, and the answer
    says `pinned` so a screen can offer the way back (`follow_rule`).
    """
    name = str(client or "").strip()
    if not name:
        return {"ok": False, "client": name, "error": "No client named."}
    rule = rule_for_client(name, partner_map=partner_map)
    try:
        with _LOCK:
            rows = _load()
            out = _unassign_rows(rows, name, actor, pin=bool(rule))
            if not _save(rows):
                return {"ok": False, "client": name,
                        "error": "The change could not be saved. "
                                 "Nothing changed."}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "client": name,
                "error": f"The change could not be saved: {exc}"[:200]}
    if rule:
        out["overrides_rule"] = str(rule.get("partner") or "")
    return out


def follow_rule(client: str, *, actor: str = "") -> dict:
    """Drop the direct row so this client follows the standing rule again.

    The undo for both halves of a direct decision -- an assignment to somebody
    and a pin to nobody. Deliberately its own verb rather than a second
    meaning for `unassign`: "take this off everyone" and "let the partner rule
    decide again" are different answers and a screen offering one control for
    both cannot say which it did.
    """
    name = str(client or "").strip()
    if not name:
        return {"ok": False, "client": name, "error": "No client named."}
    key = _key(name)
    try:
        with _LOCK:
            rows = _load()
            keep = [r for r in rows if _key(r.get("client")) != key]
            if len(keep) == len(rows):
                return {"ok": True, "client": name, "already": True}
            if not _save(keep):
                return {"ok": False, "client": name,
                        "error": "The change could not be saved. "
                                 "Nothing changed."}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "client": name,
                "error": f"The change could not be saved: {exc}"[:200]}
    return {"ok": True, "client": name}


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


def unassign_many(clients, *, actor: str = "", partner_map=None) -> dict:
    """Take a selection off whoever holds it, reporting each row separately.

    Each row is pinned or deleted on its own merits: a selection can hold one
    client a standing rule covers and one it does not, and treating them alike
    would either leave a pin on a client nothing was claiming or hand a client
    straight back to the rule the press was meant to override.
    """
    names = _selection(clients)
    if not names:
        return {"ok": False, "results": [], "cleared": 0, "failed": 0,
                "error": "No clients were selected."}

    # Asked once for the whole selection rather than once per client: the
    # rules are one small file and the partner index is one products read.
    covered: dict[str, str] = {}
    try:
        by_partner = rules_by_partner()
        if by_partner:
            index, _names, _err = partner_index(partner_map)
            for name in names:
                for partner in index.get(_key(name)) or ():
                    if by_partner.get(_partner_key(partner)):
                        covered[_key(name)] = partner
                        break
    except Exception:                                   # noqa: BLE001
        covered = {}

    results = []
    try:
        with _LOCK:
            rows = _load()
            for name in names:
                out = _unassign_rows(rows, name, actor,
                                     pin=_key(name) in covered)
                if out["pinned"]:
                    out["overrides_rule"] = covered[_key(name)]
                results.append(out)
            if not _save(rows):
                results = [dict(r, ok=False,
                                error="The change could not be saved. "
                                      "Nothing changed.")
                           for r in results]
    except Exception as exc:                            # noqa: BLE001
        results = [{"ok": False, "client": n,
                    "error": f"The change could not be saved: {exc}"[:200]}
                   for n in names]
    cleared = sum(1 for r in results if r.get("ok"))
    return {"ok": cleared > 0, "results": results, "cleared": cleared,
            "failed": len(results) - cleared,
            "pinned": sum(1 for r in results if r.get("pinned")),
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

def summary(clients=None, *, partner_map=None) -> dict:
    """Who owns what, against the client list as it stands.

    `clients` is the client book — handed in rather than imported, so this
    cannot come to disagree with a report about which clients exist. Left out,
    the registry is read and a registry that would not answer is **named**:
    counting assignments against an empty book would report every client as
    assigned, which is a confident wrong answer of exactly the kind this
    codebase keeps having to undo.

    Every row carries how its owner was decided — `direct`, `rule` with the
    partner named, an explicit `pinned` nobody, or `contested` where two rules
    disagree. That provenance is the whole safety of a standing rule: the
    objection to one is that somebody ends up holding a client with nothing
    saying why, and this is where the why lives.
    """
    error = ""
    if clients is None:
        try:
            from hub import clients_registry
            clients = clients_registry.all_clients()
        except Exception as exc:                        # noqa: BLE001
            clients, error = [], f"{type(exc).__name__}: {exc}"[:200]

    names = []
    for c in clients or ():
        name = (str(c.get("name") or "").strip() if isinstance(c, dict)
                else str(c or "").strip())
        if name:
            names.append(name)

    held = resolved(names, partner_map=partner_map)
    index = user_index()
    rows, by_email, by_rule = [], {}, {}
    unassigned = contested = pinned = 0
    for name in names:
        row = held.get(_key(name)) or _blank(name)
        email = row.get("email") or ""
        if email:
            by_email.setdefault(email, []).append(name)
            if row.get("source") == "rule":
                by_rule.setdefault(email, []).append(name)
        else:
            unassigned += 1
            if row.get("contested"):
                contested += 1
            elif row.get("pinned"):
                pinned += 1
        rows.append({
            "client": name,
            "email": email,
            "owner": display_name(email, index) if email else "",
            "known": bool(email and email in index),
            "source": row.get("source") or "",
            "partner": row.get("partner") or "",
            "contested": row.get("contested") or [],
            "pinned": bool(row.get("pinned")),
            "at": str(row.get("at") or ""),
            "by": str(row.get("by") or ""),
        })

    # An assignment whose account is gone. Named rather than counted as
    # assigned: the client has an owner on paper and nobody in practice, which
    # is the one state a handover has to be able to find. A standing rule can
    # be in that state too, and for a whole partner at once.
    unknown = sorted({r["email"] for r in rows if r["email"] and not r["known"]})
    rule_rows = []
    for rule in rules():
        addr = normalise_email(rule.get("email"))
        rule_rows.append({
            "partner": str(rule.get("partner") or ""),
            "email": addr,
            "owner": display_name(addr, index) if addr else "",
            "known": bool(addr and addr in index),
            "by": str(rule.get("by") or ""),
            "at": str(rule.get("at") or ""),
            "note": str(rule.get("note") or ""),
            # What the rule is actually claiming today, which is the number
            # that makes it reviewable: a rule naming a partner nobody carries
            # any more reads exactly like a working one without it.
            "claims": len(by_rule.get(addr) or []),
        })
    return {
        "measured": not error,
        "error": error,
        "rows": sorted(rows, key=lambda r: r["client"].lower()),
        "rules": rule_rows,
        "counts": {
            "clients": len(rows),
            "assigned": len(rows) - unassigned,
            "unassigned": unassigned,
            "by_rule": sum(1 for r in rows if r["source"] == "rule"),
            "by_hand": sum(1 for r in rows if r["source"] == "direct"),
            "contested": contested,
            "pinned": pinned,
            "owners": len(by_email),
            "unknown_owners": len(unknown),
            "rules": len(rule_rows),
        },
        "by_owner": {e: sorted(v, key=str.lower) for e, v in by_email.items()},
        "unknown_owners": unknown,
    }
