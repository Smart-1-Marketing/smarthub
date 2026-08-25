"""Domains Smart 1 bought for a client, and when each one renews.

We buy domains on behalf of clients and bill them back. The record of that has
always been in Knack — `S1M Purchase Domain for Client?` (field_2964) on the
website record, with the fee, the registrar and the renewal billing date
beside it — and nothing read it, so the only way to know what renews next month
was to open object_153 and sort it by eye. A domain that renews unbilled is
money we spent and did not invoice.

So this is the renewal calendar. Three rules it is built on:

**Only ours.** A row appears only where field_2964 says yes. Every other
website record is somebody else's domain and a list that includes them is a
list nobody trusts. `is_ours()` reads a Knack boolean *and* a yes/no dropdown,
because the field can be published either way and a `True` read as the string
"True" would be as wrong as a "Yes" read as nothing.

**The date decides the order, and a missing date is not a zero.**
`field_3298` (Domain Renewal Billing Date) sorts the table. A row with no date
cannot be placed in a month, so it goes in its own group and says so, rather
than sorting to the top as if it renewed in 1970 — an absent date has to read
as "not recorded", never as "overdue".

**This month and the next three, then search.** A renewal calendar is read to
answer "what do I have to bill now"; the rest of the year is a search, not a
scroll. Months are computed from the clock every request — a hard-coded window
is right the month it is written and quietly wrong afterwards.

## Billed

There is no "billed" field in Knack, so the tick is the Hub's, kept in
`hub/jsonstore` beside the rest of the module data. It is stored **against the
renewal billing date it was ticked for**, not against the record: a domain
renews every year, and a tick that stays green when next year's date arrives is
a confident wrong answer of exactly the kind this codebase keeps having to
undo. When the date moves on, the row reads as unbilled again and says when it
was last billed.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone

from hub import jsonstore

MONTHS_AHEAD = 3

_YES = {"yes", "y", "true", "1", "checked", "on"}


def is_ours(row: dict) -> bool:
    """Did Smart 1 buy this domain for the client? (field_2964)"""
    raw = (row or {}).get("domain_bought_raw", (row or {}).get("domain_bought"))
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in _YES


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
_DATE_PATTERNS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d/%m/%Y",
                  "%B %d, %Y", "%b %d, %Y")


def parse_date(value) -> date | None:
    """Knack hands dates back in whatever the field was configured with.

    Returns None rather than guessing. A date we could not read is reported as
    unreadable further up; silently treating it as today would put a renewal in
    the current month that may be a year away.
    """
    if isinstance(value, dict):                 # {"date": "01/02/2026", ...}
        value = value.get("date") or value.get("iso_timestamp") or ""
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    if not s:
        return None
    s = s.split("T")[0].strip()
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_window(today: date | None = None, ahead: int = MONTHS_AHEAD) -> list[dict]:
    """This month and the next `ahead`, from the clock. Never hard-coded."""
    today = today or date.today()
    out, y, m = [], today.year, today.month
    for i in range(ahead + 1):
        out.append({"key": f"{y:04d}-{m:02d}",
                    "label": date(y, m, 1).strftime("%B %Y"),
                    "current": i == 0})
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


# ---------------------------------------------------------------------------
# The billed tick
# ---------------------------------------------------------------------------
def _store_path() -> str:
    return os.path.join(jsonstore.data_dir("domains"), "billed.json")


def billed_store() -> dict:
    rows = jsonstore.read_json(_store_path(), default={})
    return rows if isinstance(rows, dict) else {}


def set_billed(record_id: str, billed: bool, *, for_date: str = "",
               actor: str = "") -> dict:
    """Tick or untick one row, against the date it is being billed for."""
    rid = str(record_id or "").strip()
    if not rid:
        return {"ok": False, "error": "No website record id."}
    rows = billed_store()
    if billed:
        rows[rid] = {"billed": True, "for_date": str(for_date or "")[:40],
                     "by": str(actor or "")[:120],
                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:
        rows.pop(rid, None)
    jsonstore.write_json(_store_path(), rows, indent=1)
    return {"ok": True, "record_id": rid, "billed": bool(billed),
            "row": rows.get(rid)}


def _billed_state(rid: str, renewal_raw: str, store: dict) -> dict:
    """Whether this row counts as billed for the renewal date it shows now."""
    hit = store.get(str(rid)) or {}
    if not hit.get("billed"):
        return {"billed": False, "note": ""}
    was = str(hit.get("for_date") or "")
    now = str(renewal_raw or "")
    if was and now and was != now:
        # The renewal rolled. This is a new charge, so it is unbilled again —
        # and says when the last one was, rather than losing the history.
        return {"billed": False,
                "note": f"Billed for {was}; this renewal date is new."}
    return {"billed": True, "by": hit.get("by", ""), "at": hit.get("at", ""),
            "note": f"Ticked by {hit.get('by') or 'someone'}"
                    + (f" on {str(hit.get('at'))[:10]}" if hit.get("at") else "")}


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def report(q: str = "", today: date | None = None) -> dict:
    """The purchased-domain table: this month, the next three, then the rest."""
    today = today or date.today()
    try:
        from hub import knack_websites
        rows = knack_websites.rows()
        read_error = ""
    except Exception as exc:                            # noqa: BLE001
        rows, read_error = [], f"{type(exc).__name__}: {exc}"

    store = billed_store()
    ours, undated, unreadable = [], [], 0
    for r in rows:
        if not is_ours(r):
            continue
        raw = r.get("renewal_billing_date") or ""
        when = parse_date(raw)
        if raw and not when:
            unreadable += 1
        row = {
            "record_id": r.get("id", ""),
            "domain": r.get("domain", ""),
            "client": r.get("client", ""),
            "registrar": r.get("registrar", ""),
            "client_status": r.get("client_status", ""),
            "fee": r.get("domain_fee", 0),
            "renewal_billing_date": raw,
            "renewal_billing_iso": when.isoformat() if when else "",
            "renews": r.get("domain_renews", ""),
            "bought_on": r.get("domain_bought_on", ""),
            "month": _month_key(when) if when else "",
            **_billed_state(r.get("id", ""), raw, store),
        }
        (ours if when else undated).append(row)

    ours.sort(key=lambda x: (x["renewal_billing_iso"], x["domain"]))
    undated.sort(key=lambda x: x["domain"])

    window = month_window(today)
    keys = {w["key"] for w in window}
    groups = []
    for w in window:
        items = [x for x in ours if x["month"] == w["key"]]
        groups.append({**w, "rows": items, "count": len(items),
                       "fee_total": round(sum(x["fee"] or 0 for x in items), 2),
                       "unbilled": sum(1 for x in items if not x["billed"])})

    rest = [x for x in ours if x["month"] not in keys]
    needle = str(q or "").strip().lower()
    matches = []
    if needle:
        matches = [x for x in ours + undated
                   if needle in (x["domain"] or "").lower()
                   or needle in (x["client"] or "").lower()
                   or needle in (x["registrar"] or "").lower()]

    note = ("Every domain Smart 1 bought for a client, by the renewal billing "
            "date Knack holds (field_3298). Only records where “S1M Purchase "
            "Domain for Client?” is yes are listed.")
    if read_error:
        note = ("The Knack website registry could not be read (" + read_error +
                "), so this is empty rather than complete.")
    else:
        why = _registry_error()
        if why:
            # Empty because Knack holds none, or empty because we could not
            # ask? Only one of those means "we have bought no domains".
            note += (" " + why + " Nothing was read, so this is not a total.")
    if unreadable:
        note += (f" {unreadable} record(s) carry a renewal billing date this "
                 "could not read; they are with the undated ones rather than "
                 "being placed in a month they may not belong to.")

    return {
        "today": today.isoformat(),
        "groups": groups,
        "later": rest, "later_count": len(rest),
        "undated": undated, "undated_count": len(undated),
        "results": matches, "q": q,
        "total": len(ours) + len(undated),
        "billed_count": sum(1 for x in ours + undated if x["billed"]),
        "fee_total": round(sum(x["fee"] or 0 for x in ours + undated), 2),
        "read_error": read_error,
        "note": note,
    }


def _registry_error() -> str:
    """Why the registry read came back empty, or "" if it genuinely was."""
    try:
        from hub import knack_websites
        return knack_websites.last_error()
    except Exception:                                   # noqa: BLE001
        return ""
