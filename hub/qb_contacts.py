"""The QuickBooks billing contact, filed onto the client record.

Every client with a QuickBooks customer attached (`attached.qb` on their SEO
store, the thing Client 360's "Search & attach customer" writes) has a
billing contact sitting in QuickBooks -- the name, the email invoices go to,
a phone -- and until now none of it reached the Client Info strip, which went
on reading "No contact info on file" for businesses we invoice every month.

This files that contact under the `accounting` role, once a week, and on a
button.

## The rules

* **Never over a typed value.** A row a person entered is the better
  source. A QuickBooks row matches an existing contact by email (or, with
  no email, by name), and on a match only the *blank* fields are filled.
  The row this sync filed itself (`source == "quickbooks"`, `source_id` the
  customer id) is QuickBooks' own and is refreshed from it.
* **A name is not required.** A customer entered as a company with no
  person on it still has an email invoices go to; that is filed with the
  name left blank rather than dropped. A customer with no name, no email
  and no phone is skipped -- there is nothing to file.
* **Never the primary contact, except first.** A filed row is `accounting`,
  not primary; `hub.seo.clean_contacts` makes it primary only when it is
  the only contact on the record, so a record that had nobody now has
  somebody rather than a row nobody can see.
* **Once a week, Sunday at 2pm Eastern**, decided inside `sweep()` from
  the last completed run rather than from the scheduler's interval, the
  arrangement every other nightly job here uses (`hub/nightly.py` explains
  why the window is a real timezone). The scheduler ticks hourly and this
  returns at once when the window has not passed; a leader that restarted
  through Sunday afternoon picks the week up on its next tick.
* **Per-client error isolation, and a refusal stops the sweep.** One bad
  customer costs that client only. A QuickBooks that refuses (no token, an
  expired one) refuses every client, so the first refusal ends the pass
  rather than logging the same refusal a hundred times.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

SOURCE = "quickbooks"
ROLE = "accounting"

# Sunday at 2pm Eastern. The hour follows the house nightly-window helper's
# env-override pattern; the weekday is fixed -- a weekly sync on a Tuesday
# is not something anybody asked to be able to configure.
SYNC_HOUR_ENV = "QB_CONTACTS_SYNC_HOUR"
DEFAULT_HOUR = 14
SYNC_WEEKDAY = 6          # Monday=0 ... Sunday=6


# ---------------------------------------------------------------- the window
def last_window(now: datetime, hour: int = DEFAULT_HOUR,
                weekday: int = SYNC_WEEKDAY) -> datetime:
    """The most recent Eastern-time moment the weekly sync was due at."""
    from hub import nightly
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(nightly.EASTERN)
    mark = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    mark -= timedelta(days=(mark.weekday() - weekday) % 7)
    if mark > local:
        mark -= timedelta(days=7)
    return mark


def due(last_success: datetime | None, *, now: datetime | None = None) -> bool:
    """Has Sunday 2pm Eastern passed since the last completed sweep?

    Never run is due; a last run in the future (a restored snapshot, a
    clock that moved) is due too -- too often is recoverable, never again
    is not, the reading `hub/nightly.due` gives.
    """
    from hub import nightly
    now = now or datetime.now(timezone.utc)
    if last_success is None:
        return True
    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)
    if last_success > now:
        return True
    hour = nightly.hour_for(SYNC_HOUR_ENV, DEFAULT_HOUR)
    return last_success < last_window(now, hour)


# ---------------------------------------------------------------- the merge
def _norm_email(v) -> str:
    return " ".join(str(v or "").split()).lower()


def _norm_name(v) -> str:
    return " ".join(str(v or "").split()).lower()


def merge_contact(contacts: list[dict], found: dict) -> tuple[list[dict], str]:
    """Fold one QuickBooks contact into a contact list.

    Returns `(contacts, outcome)` where outcome is one of `added`,
    `updated`, `unchanged`, `skipped`. Pure: no store, no QuickBooks, so
    the rules above can be tested on their own.
    """
    found = found or {}
    name = " ".join(str(found.get("name") or "").split())
    email = " ".join(str(found.get("email") or "").split())
    phone = " ".join(str(found.get("phone") or "").split())
    cid = str(found.get("id") or "")
    if not (name or email or phone):
        return contacts, "skipped"

    rows = [dict(c) for c in (contacts or []) if isinstance(c, dict)]
    match = None
    for c in rows:
        if c.get("source") == SOURCE and cid and str(c.get("source_id") or "") == cid:
            match = c
            break
    if match is None and email:
        match = next((c for c in rows if _norm_email(c.get("email")) == _norm_email(email)), None)
    if match is None and not email and name:
        match = next((c for c in rows if _norm_name(c.get("name")) == _norm_name(name)), None)

    if match is None:
        rows.append({"name": name, "email": email, "phone": phone, "primary": False,
                     "role": ROLE, "source": SOURCE, "source_id": cid})
        return rows, "added"

    before = dict(match)
    ours = match.get("source") == SOURCE
    for key, value in (("name", name), ("email", email), ("phone", phone)):
        if value and (ours or not str(match.get(key) or "").strip()):
            match[key] = value
    if ours:
        match["source_id"] = cid or match.get("source_id", "")
        match["role"] = match.get("role") or ROLE
    elif not match.get("role"):
        match["role"] = ROLE
    return rows, ("updated" if match != before else "unchanged")


# ---------------------------------------------------------------- one client
def attached_customers(client: str) -> list[dict]:
    """The QuickBooks customers attached to this client, `[{id, name}]`."""
    from hub import seo
    rows = (seo.get_links(client) or {}).get("qb") or []
    if not isinstance(rows, list):
        rows = [rows]
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def sync_client(client: str, *, actor: str = "scheduler") -> dict:
    """File every attached customer's billing contact onto one client.

    `{ok, client, customers, added, updated, unchanged, skipped, errors}`.
    `ok` is False only when QuickBooks itself could not be asked -- a
    customer id that matches nothing is that customer's own error line.
    """
    from hub import seo, quickbooks as qb
    out = {"ok": True, "client": client, "customers": 0, "added": 0, "updated": 0,
           "unchanged": 0, "skipped": 0, "errors": [], "refused": False}
    client = str(client or "").strip()
    if not client:
        out.update(ok=False, errors=["A client is required."])
        return out
    customers = attached_customers(client)
    out["customers"] = len(customers)
    if not customers:
        return out
    if not (qb.configured() and qb.connected()):
        out.update(ok=False, refused=True,
                   errors=["QuickBooks is not connected, so nothing could be read."])
        return out

    prof = seo.get_profile(client)
    contacts = list(prof.get("contacts") or [])
    changed = False
    for cu in customers:
        try:
            found = qb.customer_contact(cu.get("id"))
        except Exception as exc:                          # noqa: BLE001
            out.update(ok=False, refused=True)
            out["errors"].append(f"{cu.get('name') or cu.get('id')}: {type(exc).__name__}: {exc}")
            break
        if not found:
            out["errors"].append(f"{cu.get('name') or cu.get('id')}: no such customer in QuickBooks any more.")
            continue
        contacts, outcome = merge_contact(contacts, found)
        out[outcome] += 1
        changed = changed or outcome in ("added", "updated")
    if changed:
        seo.set_profile(client, {"contacts": contacts}, actor=actor)
        try:
            from hub import audit
            audit.log("hub", "qb_contact_filed", actor=actor, client=client,
                      added=out["added"], updated=out["updated"])
        except Exception:                                 # noqa: BLE001
            pass
    return out


# ---------------------------------------------------------------- the sweep
def _state_path() -> str:
    from hub import seo
    return os.path.join(seo._store_base(), "_qb_contacts_state.json")    # noqa: SLF001


def _state() -> dict:
    from hub import jsonstore
    data = jsonstore.read_json(_state_path(), default={}) or {}
    return data if isinstance(data, dict) else {}


def clients_with_customers() -> list[str]:
    """Every client whose SEO store carries an attached QuickBooks customer.

    Read off the disk the way `hub.industry._client_universe` reads it --
    no Knack call, nothing outside what the Hub already holds.
    """
    from hub import seo, jsonstore
    names: dict[str, str] = {}
    try:
        base = seo._store_base()                          # noqa: SLF001
        for fname in os.listdir(base):
            if not fname.endswith(".json") or fname.startswith("_"):
                continue
            try:
                data = jsonstore.read_json(os.path.join(base, fname), default={})
            except Exception:                              # noqa: BLE001
                continue
            name = str((data or {}).get("client") or "").strip()
            qb_rows = ((data or {}).get("attached") or {}).get("qb")
            if name and qb_rows:
                names[name.lower()] = name
    except Exception:                                      # noqa: BLE001
        pass
    return sorted(names.values(), key=str.lower)


def sweep(*, force: bool = False, now: datetime | None = None) -> dict:
    """Every client with a customer attached, once the weekly window passes."""
    from hub import jsonstore
    now = now or datetime.now(timezone.utc)
    out = {"ran": False, "skipped": "", "clients": 0, "added": 0, "updated": 0,
           "unchanged": 0, "failed": 0, "errors": {}}
    st = _state()
    last = None
    if st.get("last_run_at"):
        try:
            last = datetime.fromisoformat(str(st["last_run_at"]))
        except ValueError:
            last = None
    if not force and not due(last, now=now):
        out["skipped"] = "Not due yet."
        return out
    try:
        from hub import quickbooks as qb
        ready = qb.configured() and qb.connected()
    except Exception:                                      # noqa: BLE001
        ready = False
    if not ready:
        out["skipped"] = "QuickBooks is not connected."
        return out
    names = clients_with_customers()
    out["clients"] = len(names)
    for name in names:
        res = sync_client(name)
        out["added"] += res["added"]
        out["updated"] += res["updated"]
        out["unchanged"] += res["unchanged"]
        if res["errors"]:
            out["failed"] += 1
            out["errors"][name] = "; ".join(res["errors"])[:300]
        if res.get("refused"):
            out["skipped"] = "QuickBooks refused part way through; the rest is due next tick."
            break
    out["ran"] = True
    try:
        jsonstore.write_json(_state_path(), {
            "last_run_at": now.isoformat(timespec="seconds"), "clients": out["clients"],
            "added": out["added"], "updated": out["updated"], "failed": out["failed"],
        }, durable=False)
    except Exception:                                      # noqa: BLE001
        pass
    return out


def sweep_state() -> dict:
    st = _state()
    return {"last_run_at": st.get("last_run_at"), "clients": st.get("clients"),
            "added": st.get("added"), "updated": st.get("updated"),
            "failed": st.get("failed")}
