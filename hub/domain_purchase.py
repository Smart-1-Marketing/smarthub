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

There is no "billed" field in Knack. There is one in QuickBooks, though not
under that name: every renewal we invoice is a line carrying the product
**Website Domain Renewal**, and `hub/domain_renewals.py` reads those and
matches each one back to a website record. So "billed" is now an observation
with evidence behind it — the invoice number, the date, the amount and who it
went to — and the row says which invoice said so.

The Hub's own tick survives beside it, because QuickBooks is not the only way
a renewal gets paid for and a charge whose description names nobody may never
match. It is stored **against the renewal billing date it was ticked for**,
not against the record: a domain renews every year, and a tick that stays
green when next year's date arrives is a confident wrong answer of exactly the
kind this codebase keeps having to undo. When the date moves on, the row reads
as unbilled again and says when it was last billed. A QuickBooks charge is
held to the same rule — it counts for the renewal it is **near**
(`domain_renewals.WINDOW_DAYS`), so last year's invoice can never mark this
year's renewal billed.

`billed_source` is always said out loud: **quickbooks** (an invoice line),
**hub** (somebody ticked it here) or **""** — and where the two disagree,
both are printed rather than one quietly winning.

## Billed is this month's question; do-not-renew is next month's

Asking "did we bill this?" about a renewal three months out is asking about
something that has not happened. What is worth deciding that far ahead is the
opposite one — whether it should renew at all — so the current month carries
the billed tick and every later month carries **do not renew**.

That flag is deliberately *not* held against the renewal date the way the
billed tick is. "Do not renew" is a decision about the domain, and if the
renewal date later rolls forward the domain renewed anyway — which is a
finding, not a flag to quietly clear. `dnr_state()` keeps the mark and says
the date has moved, and `do_not_renew_report()` lists exactly those.

## The year-end reconciliation

`year_to_date()` asks the two questions nobody could ask before, in both
directions: renewals that came due this year with **no invoice** behind them
(money we spent and did not bill), and Website Domain Renewal charges that
match **no record here** (money we billed for a domain this Hub has never
heard of, or one whose description nobody can join up). Neither is a number
on its own — every row carries what it was matched on and what it was not, so
the answer can be acted on rather than merely counted.
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
# Do not renew
# ---------------------------------------------------------------------------
def _dnr_path() -> str:
    return os.path.join(jsonstore.data_dir("domains"), "do_not_renew.json")


def dnr_store() -> dict:
    rows = jsonstore.read_json(_dnr_path(), default={})
    return rows if isinstance(rows, dict) else {}


def set_do_not_renew(record_id: str, on: bool, *, for_date: str = "",
                     reason: str = "", actor: str = "") -> dict:
    """Mark one domain as not to be renewed, or clear the mark."""
    rid = str(record_id or "").strip()
    if not rid:
        return {"ok": False, "error": "No website record id."}
    rows = dnr_store()
    if on:
        rows[rid] = {"do_not_renew": True,
                     "for_date": str(for_date or "")[:40],
                     "reason": str(reason or "")[:400],
                     "by": str(actor or "")[:120],
                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:
        rows.pop(rid, None)
    jsonstore.write_json(_dnr_path(), rows, indent=1)
    return {"ok": True, "record_id": rid, "do_not_renew": bool(on),
            "row": rows.get(rid)}


def dnr_state(rid: str, renewal_raw: str, store: dict) -> dict:
    """Whether this row is marked do-not-renew, and whether it renewed anyway.

    Unlike the billed tick this is **not** retired when the renewal date rolls.
    Somebody said this domain should not renew; a renewal date that has since
    moved on means it renewed regardless, and clearing the mark would delete
    the only evidence of that. It is kept, and said.
    """
    hit = store.get(str(rid)) or {}
    if not hit.get("do_not_renew"):
        return {"do_not_renew": False, "dnr_note": "", "renewed_anyway": False}
    was = str(hit.get("for_date") or "")
    now = str(renewal_raw or "")
    who = hit.get("by") or "someone"
    when = f" on {str(hit.get('at'))[:10]}" if hit.get("at") else ""
    note = f"Marked do not renew by {who}{when}."
    if hit.get("reason"):
        note += f" Reason: {hit['reason']}"
    moved = bool(was and now and was != now)
    if moved:
        note += (f" It was marked against {was} and the renewal billing date "
                 f"now reads {now} — so it appears to have renewed anyway.")
    return {"do_not_renew": True, "dnr_note": note, "renewed_anyway": moved,
            "dnr_by": hit.get("by", ""), "dnr_at": hit.get("at", ""),
            "dnr_for_date": was, "dnr_reason": hit.get("reason", "")}


# ---------------------------------------------------------------------------
# Reading the two sides
# ---------------------------------------------------------------------------
def _registry() -> tuple[list[dict], str]:
    """Every website record, and why the read failed if it did."""
    try:
        from hub import knack_websites
        return list(knack_websites.rows()), ""
    except Exception as exc:                            # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"


def _registry_error() -> str:
    """Why the registry read came back empty, or "" if it genuinely was."""
    try:
        from hub import knack_websites
        return knack_websites.last_error()
    except Exception:                                   # noqa: BLE001
        return ""


def _quickbooks(year: int, refresh: bool = False) -> dict:
    """The Website Domain Renewal charges for one year. Never raises."""
    try:
        from hub import domain_renewals
        return domain_renewals.charges(year, refresh=refresh)
    except Exception as exc:                            # noqa: BLE001
        return {"lines": [], "error": f"QuickBooks could not be read "
                                      f"({type(exc).__name__}: {exc}).",
                "fetched_at": "", "age_hours": None, "cached": False,
                "item": ""}


def _matched(rows: list[dict], lines: list[dict]) -> list[dict]:
    try:
        from hub import domain_renewals
        return domain_renewals.match_charges(lines, rows)
    except Exception:                                   # noqa: BLE001
        return []


def _charge_brief(c: dict) -> dict:
    """One charge as a row on screen shows it."""
    return {"key": c.get("key", ""), "invoice_id": c.get("invoice_id", ""),
            "doc_number": c.get("doc_number", ""), "date": c.get("date", ""),
            "amount": c.get("amount", 0), "customer": c.get("customer", ""),
            "link": c.get("link", ""), "description": c.get("description", ""),
            "matched_on": c.get("matched_on", ""),
            "confidence": c.get("confidence", ""), "why": c.get("why", "")}


def _quickbooks_note(qb: dict) -> str:
    """What to say about the QuickBooks half, including when it said nothing."""
    if qb.get("error"):
        return (qb["error"] + " Billed is therefore whatever has been ticked "
                "here, and nothing more — it is not a statement about what "
                "was invoiced.")
    item = qb.get("item") or "Website Domain Renewal"
    when = ""
    if qb.get("fetched_at"):
        age = qb.get("age_hours")
        when = (f" Read from QuickBooks {qb['fetched_at'][:16].replace('T', ' ')}"
                + (f" ({age}h ago)" if age else "") + ".")
    return (f"Billed is read from QuickBooks: every invoice line carrying the "
            f"product “{item}”, matched back to a domain on the domain in its "
            f"description, or failing that on the client named in it.{when}")


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def report(q: str = "", today: date | None = None, refresh: bool = False) -> dict:
    """The purchased-domain table: this month, the next three, then the rest.

    The current month asks whether each renewal was **billed** — answered from
    QuickBooks where a charge can be matched, from the Hub's tick otherwise,
    and always saying which. Every later month asks the only question worth
    asking that far ahead instead: should this domain renew at all.
    """
    today = today or date.today()
    rows, read_error = _registry()
    qb = _quickbooks(today.year, refresh=refresh)
    charges_by_record = _by_record(_matched(rows, qb.get("lines") or []))

    store = billed_store()
    dnr = dnr_store()
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
            # The media partner the site is filed under. Asked for beside the
            # domain because a renewal is chased through the partner, not the
            # client — the invoice goes to them.
            "partner": r.get("media_partner", ""),
            "registrar": r.get("registrar", ""),
            "client_status": r.get("client_status", ""),
            "fee": r.get("domain_fee", 0),
            "renewal_billing_date": raw,
            "renewal_billing_iso": when.isoformat() if when else "",
            "renews": r.get("domain_renews", ""),
            "bought_on": r.get("domain_bought_on", ""),
            "month": _month_key(when) if when else "",
            **_billed_state(r.get("id", ""), raw, store),
            **dnr_state(r.get("id", ""), raw, dnr),
        }
        row.update(_billing_evidence(
            row, charges_by_record.get(row["record_id"]) or [], when,
            qb_error=qb.get("error") or ""))
        (ours if when else undated).append(row)

    ours.sort(key=lambda x: (x["renewal_billing_iso"], x["domain"]))
    undated.sort(key=lambda x: x["domain"])

    window = month_window(today)
    keys = {w["key"] for w in window}
    groups = []
    for w in window:
        items = [x for x in ours if x["month"] == w["key"]]
        groups.append({
            **w,
            # This month is a billing question; a month that has not happened
            # is a renew-or-not question. The column follows.
            "column": "billed" if w["current"] else "do_not_renew",
            "rows": items, "count": len(items),
            "fee_total": round(sum(x["fee"] or 0 for x in items), 2),
            "unbilled": sum(1 for x in items if not x["billed"]),
            "dnr_count": sum(1 for x in items if x["do_not_renew"]),
        })

    rest = [x for x in ours if x["month"] not in keys]
    needle = str(q or "").strip().lower()
    matches = []
    if needle:
        matches = [x for x in ours + undated
                   if needle in (x["domain"] or "").lower()
                   or needle in (x["client"] or "").lower()
                   or needle in (x["partner"] or "").lower()
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
        "dnr_total": sum(1 for x in ours + undated if x["do_not_renew"]),
        "fee_total": round(sum(x["fee"] or 0 for x in ours + undated), 2),
        "read_error": read_error,
        "quickbooks": {k: qb.get(k) for k in
                       ("error", "fetched_at", "age_hours", "cached", "item")},
        "quickbooks_charges": len(qb.get("lines") or []),
        "quickbooks_note": _quickbooks_note(qb),
        "note": note,
    }


def _by_record(matched: list[dict]) -> dict:
    try:
        from hub import domain_renewals
        return domain_renewals.by_record(matched)
    except Exception:                                   # noqa: BLE001
        return {}


def _billing_evidence(row: dict, charges: list[dict], when: date | None,
                      qb_error: str = "") -> dict:
    """What QuickBooks says about this renewal, folded in with the Hub's tick.

    Three rules, each of which is a way to be confidently wrong:

    * **A charge counts for the renewal it is near.** A domain renews every
      year and is invoiced once; without the window, last year's charge marks
      this year's renewal billed.
    * **A probable match does not mark anything billed.** A near name is a
      suggestion, and a suggestion that ticks a box is a fact nobody agreed
      to. It is shown, named as unconfirmed, and left out of the count.
    * **Where the tick and QuickBooks disagree, both are printed.** A tick
      with no invoice behind it may be a renewal paid another way, or it may
      be a renewal nobody billed — and only a person can tell which.

    And a QuickBooks we could not read never produces "no charge matches this
    renewal". That sentence is a finding; a failed read is not one, and
    printing the first when the second happened is the confident wrong answer
    this whole file is arranged against.
    """
    try:
        from hub.domain_renewals import within_window
    except Exception:                                   # noqa: BLE001
        return {"charges": [], "charges_year": 0, "billed_source":
                "hub" if row.get("billed") else "", "billed_to": "",
                "maybe_charges": []}

    in_window = [c for c in charges if within_window(c.get("date"), when)]
    firm = [c for c in in_window
            if c.get("confidence") in ("confirmed", "exact")]
    maybe = [c for c in in_window if c.get("confidence") == "probable"]

    hub_tick = bool(row.get("billed"))
    hub_note = row.get("note") or ""
    billed = bool(firm) or hub_tick
    source = "quickbooks" if firm else ("hub" if hub_tick else "")

    parts = []
    if firm:
        c = firm[0]
        doc = c.get("doc_number") or c.get("invoice_id") or "?"
        parts.append(f"Invoice {doc}"
                     + (f" to {c['customer']}" if c.get("customer") else "")
                     + (f" on {c['date']}" if c.get("date") else "")
                     + f" · ${float(c.get('amount') or 0):.2f}.")
        if len(firm) > 1:
            parts.append(f"{len(firm)} charges match this renewal.")
        if hub_tick and hub_note:
            parts.append("Also ticked here — " + hub_note.rstrip("."))
    elif hub_tick:
        parts.append(hub_note or "Ticked here.")
        parts.append("QuickBooks could not be read, so this is the tick alone "
                     "and nothing has been checked against it."
                     if qb_error else
                     "No Website Domain Renewal charge in QuickBooks matches "
                     "this renewal, so this is the tick and not an invoice.")
    elif hub_note:
        # A tick retired because the renewal date rolled still has to say what
        # it was ticked for, or the history goes with it.
        parts.append(hub_note)
    if maybe:
        c = maybe[0]
        doc = c.get("doc_number") or c.get("invoice_id") or "?"
        parts.append(f"Invoice {doc} may be this renewal — {c.get('why', '')} "
                     f"It is not counted as billed until it is confirmed.")

    return {
        "billed": billed,
        "billed_source": source,
        "billed_to": (firm[0].get("customer") if firm else ""),
        "charges": [_charge_brief(c) for c in in_window],
        "maybe_charges": [_charge_brief(c) for c in maybe],
        "charges_year": len(charges),
        "note": " ".join(p for p in parts if p).strip(),
    }


# ---------------------------------------------------------------------------
# Do not renew — the report
# ---------------------------------------------------------------------------
def do_not_renew_report(today: date | None = None) -> dict:
    """Every domain somebody has said should not renew.

    Two lists, because they call for opposite actions. **Standing** is the
    queue: these have not renewed yet and somebody has to cancel them at the
    registrar. **Renewed anyway** is the exception report: the mark was made
    against one renewal billing date and the record now carries a later one,
    so the domain renewed after we decided it should not — which is a charge
    to chase, not a queue item.

    A mark against a record that is no longer in the registry is named too,
    rather than dropped: it is the one case where the list quietly shrinks.
    """
    today = today or date.today()
    rows, read_error = _registry()
    by_id = {str(r.get("id") or ""): r for r in rows}
    store = dnr_store()

    standing, renewed, missing = [], [], []
    for rid, hit in sorted(store.items(), key=lambda kv: str(kv[1].get("at"))):
        rec = by_id.get(str(rid))
        if rec is None:
            missing.append({"record_id": rid, "for_date": hit.get("for_date", ""),
                            "by": hit.get("by", ""), "at": hit.get("at", ""),
                            "reason": hit.get("reason", ""),
                            "domain": hit.get("domain", "")})
            continue
        raw = rec.get("renewal_billing_date") or ""
        when = parse_date(raw)
        state = dnr_state(rid, raw, store)
        row = {
            "record_id": rid, "domain": rec.get("domain", ""),
            "client": rec.get("client", ""),
            "partner": rec.get("media_partner", ""),
            "registrar": rec.get("registrar", ""),
            "client_status": rec.get("client_status", ""),
            "fee": rec.get("domain_fee", 0),
            "renewal_billing_date": raw,
            "renewal_billing_iso": when.isoformat() if when else "",
            "renews": rec.get("domain_renews", ""),
            "is_ours": is_ours(rec),
            **state,
        }
        (renewed if state.get("renewed_anyway") else standing).append(row)

    standing.sort(key=lambda x: (x["renewal_billing_iso"] or "9999", x["domain"]))
    renewed.sort(key=lambda x: (x["renewal_billing_iso"] or "9999", x["domain"]))

    note = ("Domains somebody has marked as not to renew. The mark is kept "
            "against the renewal billing date it was made for and is never "
            "cleared automatically — a renewal date that has since moved on "
            "means the domain renewed anyway, which is the second list rather "
            "than a mark quietly disappearing.")
    if read_error:
        note = ("The Knack website registry could not be read (" + read_error +
                "), so these are the marks alone, with nothing to check them "
                "against.")
    return {"today": today.isoformat(), "standing": standing,
            "standing_count": len(standing),
            "renewed_anyway": renewed, "renewed_count": len(renewed),
            "orphaned": missing, "orphaned_count": len(missing),
            "total": len(store), "read_error": read_error, "note": note}


# ---------------------------------------------------------------------------
# Year to date: the two directions of the same question
# ---------------------------------------------------------------------------
def year_to_date(year: int | None = None, today: date | None = None,
                 refresh: bool = False) -> dict:
    """What renewed without being billed, and what was billed without a record.

    Both directions, because each hides a different kind of money:

    * **Not billed** — a renewal came due this year and no Website Domain
      Renewal charge in QuickBooks matches it. That is a domain we paid a
      registrar for and did not invoice. Domains marked do-not-renew are
      listed apart, since not billing one of those is correct.
    * **Billed with no record here** — a charge went out and nothing in the
      website registry carries that domain or that client. Either the record
      is missing, or the description names the business in a way nothing can
      join up, and the row shows what it was matched on so a person can say
      which and attach it.

    Neither list is presented as a total when either side failed to read: a
    Knack that would not answer makes every charge look unrecorded, and a
    QuickBooks that would not answer makes every renewal look unbilled. Both
    errors travel with the numbers.
    """
    today = today or date.today()
    yr = int(year or today.year)
    start = date(yr, 1, 1)
    end = min(today, date(yr, 12, 31))

    rows, read_error = _registry()
    qb = _quickbooks(yr, refresh=refresh)
    matched = _matched(rows, qb.get("lines") or [])
    by_rec = _by_record(matched)
    dnr = dnr_store()
    store = billed_store()

    not_billed, on_purpose, reconciled = [], [], []
    # Every purchased domain, so the page can offer a real record to attach an
    # unmatched charge to. A searchable list of records and never a text box,
    # for the reason `hub/client_key.py` gives at length: a typed name that
    # matches nothing files the charge against a domain nothing joins to and
    # still reads as a clean save.
    records = []
    undatable = 0
    for r in rows:
        if not is_ours(r):
            continue
        records.append({"record_id": str(r.get("id") or ""),
                        "domain": r.get("domain", ""),
                        "client": r.get("client", ""),
                        "partner": r.get("media_partner", ""),
                        "renewal_billing_date":
                            r.get("renewal_billing_date", "")})
        raw = r.get("renewal_billing_date") or ""
        when = parse_date(raw)
        if not when:
            undatable += 1
            continue
        if not (start <= when <= end):
            continue
        rid = str(r.get("id") or "")
        row = {
            "record_id": rid, "domain": r.get("domain", ""),
            "client": r.get("client", ""),
            "partner": r.get("media_partner", ""),
            "registrar": r.get("registrar", ""),
            "client_status": r.get("client_status", ""),
            "fee": r.get("domain_fee", 0),
            "renewal_billing_date": raw,
            "renewal_billing_iso": when.isoformat(),
            **_billed_state(rid, raw, store),
            **dnr_state(rid, raw, dnr),
        }
        row.update(_billing_evidence(row, by_rec.get(rid) or [], when,
                                     qb_error=qb.get("error") or ""))
        if row["charges"] and row["billed_source"] == "quickbooks":
            reconciled.append(row)
        elif row["do_not_renew"]:
            on_purpose.append(row)
        else:
            not_billed.append(row)

    unrecorded, mismatched = [], []
    for c in matched:
        if c.get("confidence") == "probable":
            # A near name is a suggestion. Until a person confirms it this
            # charge has no record here — counting it as reconciled would be
            # the Hub agreeing with its own guess, and the row would then
            # disappear from both directions of this report at once.
            unrecorded.append(_orphan_charge(c))
        elif not c.get("record_id"):
            unrecorded.append(_orphan_charge(c))
        elif c.get("is_ours") is False:
            # Matched, but to a record whose registry entry does not say we
            # bought this domain. Billed either way — the disagreement is the
            # finding, and flattening it would lose the only sign of it.
            mismatched.append(_orphan_charge(c))

    suggested = [c for c in unrecorded if c["confidence"] == "probable"]
    unrecorded.sort(key=lambda c: (c["confidence"] != "probable", c["date"]))
    not_billed.sort(key=lambda x: x["renewal_billing_iso"])
    on_purpose.sort(key=lambda x: x["renewal_billing_iso"])

    note = (f"1 January {yr} to {end.isoformat()}. Renewals are read from the "
            f"Knack website registry and charges from QuickBooks — the "
            f"product “{qb.get('item') or 'Website Domain Renewal'}” — and "
            f"matched on the domain in each line description, or on the "
            f"client named in it.")
    problems = []
    if read_error or _registry_error():
        problems.append("The website registry could not be read (" +
                        (read_error or _registry_error()) + "), so every "
                        "charge below reads as unrecorded whether it is or not.")
    if qb.get("error"):
        problems.append(qb["error"] + " Every renewal below therefore reads as "
                        "unbilled; that is not a finding, it is a failed read.")
    if undatable:
        problems.append(f"{undatable} purchased domain(s) carry no readable "
                        f"renewal billing date, so they cannot be placed in "
                        f"{yr} at all and are in neither list.")

    return {
        "year": yr, "from": start.isoformat(), "to": end.isoformat(),
        "not_billed": not_billed, "not_billed_count": len(not_billed),
        "not_billed_fees": round(sum(x["fee"] or 0 for x in not_billed), 2),
        "not_renewing": on_purpose, "not_renewing_count": len(on_purpose),
        "reconciled_count": len(reconciled),
        "unrecorded": unrecorded, "unrecorded_count": len(unrecorded),
        "suggested_count": len(suggested),
        "unrecorded_total": round(sum(c["amount"] or 0 for c in unrecorded), 2),
        "not_bought": mismatched, "not_bought_count": len(mismatched),
        "charges_total": len(matched),
        "charges_value": round(sum(float(c.get("amount") or 0)
                                   for c in matched), 2),
        "undatable": undatable,
        "records": sorted(records, key=lambda x: (x["domain"], x["client"])),
        "records_count": len(records),
        "read_error": read_error,
        "quickbooks": {k: qb.get(k) for k in
                       ("error", "fetched_at", "age_hours", "cached", "item")},
        "problems": problems,
        "measured": not problems,
        "note": note,
    }


def _orphan_charge(c: dict) -> dict:
    """A charge on the reconciliation list, with what it was matched on."""
    return {**_charge_brief(c),
            "parsed_domain": (c.get("parsed") or {}).get("domain", ""),
            "parsed_name": (c.get("parsed") or {}).get("name", ""),
            "record_id": c.get("record_id", ""),
            "client": c.get("client", ""),
            "record_domain": c.get("record_domain", ""),
            "partner": c.get("partner", ""),
            "is_ours": c.get("is_ours"),
            "candidates": c.get("candidates") or []}


# ---------------------------------------------------------------------------
# One record, for Client 360
# ---------------------------------------------------------------------------
def status_for_record(record_id: str, record: dict | None = None,
                      today: date | None = None) -> dict:
    """The renewal standing of one website record.

    Client 360 draws this under the domain record, so somebody looking at a
    client can see what their domain costs, whether this year's renewal has
    been invoiced and which invoice did it — without opening the renewal
    calendar and finding the row again.

    Returns `applies: False` for a domain we did not buy, rather than an empty
    panel: "we do not bill this one" and "nothing has been recorded" are
    different answers.
    """
    today = today or date.today()
    rid = str(record_id or "").strip()
    if not rid:
        return {"applies": False, "reason": "No website record."}

    rows, read_error = _registry()
    rec = record
    if rec is None:
        rec = next((r for r in rows if str(r.get("id") or "") == rid), None)
    if rec is None:
        return {"applies": False, "read_error": read_error,
                "reason": ("The website registry could not be read, so the "
                           "renewal standing is not measured."
                           if read_error or _registry_error() else
                           "No website record with that id.")}
    if not is_ours(rec):
        return {"applies": False, "record_id": rid,
                "reason": "Smart 1 did not buy this domain, so there is no "
                          "renewal for us to bill."}

    raw = rec.get("renewal_billing_date") or ""
    when = parse_date(raw)
    qb = _quickbooks(today.year)
    by_rec = _by_record(_matched(rows, qb.get("lines") or []))
    row = {
        "record_id": rid, "domain": rec.get("domain", ""),
        "client": rec.get("client", ""),
        "partner": rec.get("media_partner", ""),
        "registrar": rec.get("registrar", ""),
        "fee": rec.get("domain_fee", 0),
        "renewal_billing_date": raw,
        "renewal_billing_iso": when.isoformat() if when else "",
        "renews": rec.get("domain_renews", ""),
        "bought_on": rec.get("domain_bought_on", ""),
        **_billed_state(rid, raw, billed_store()),
        **dnr_state(rid, raw, dnr_store()),
    }
    row.update(_billing_evidence(row, by_rec.get(rid) or [], when,
                                 qb_error=qb.get("error") or ""))
    row["applies"] = True
    row["this_month"] = bool(when and _month_key(when) == _month_key(today))
    row["quickbooks_error"] = qb.get("error") or ""
    row["dated"] = bool(when)
    if not when:
        row["not_measured"] = ("No renewal billing date is recorded, so this "
                               "renewal cannot be placed in a month and "
                               "billed is not a question that can be answered "
                               "for it.")
    return row
