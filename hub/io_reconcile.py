"""The insertion orders we sent, against the campaigns Knack actually has.

## Why this exists

Submitting an insertion order does three things: it writes an activity-log
entry, it registers the client as an overlay when nobody has heard of them
(`hub/io_clients.py`), and it POSTs the order to Smart 1 Suite. Then the IO
Builder's job is over.

**Nothing ever checked that the campaign was set up.** An order signed in March
whose products were never written into Knack looks exactly like one that was:
the log says it went, the client's overlay row still stands in for a record
that never arrived, and Client 360 goes on saying the cards are empty because
there is nothing to read -- which is the sentence `io_clients.py` added for a
client who is *new*, not for one whose campaign was dropped. Nobody is billed,
nothing is trafficked, and the first person to find out is whoever eventually
asks why a client we wrote an order for has no products.

Both halves were already here. What we sent is in the activity log and, for a
proposal that was converted, on the quote itself. What landed is on Knack's
products, each carrying its IO number. Nothing compared them.

## The rules

**A source that could not be read is not measured, and this is the strongest
case of that rule in the Hub.** `knack_products.rows()` never raises: it falls
back to a stale cache, then to the committed export, then to nothing. Read
against the export -- a snapshot nobody refreshes, whose rows are not even
flattened by `_row()` and so carry no `io` at all -- *every* order would read
as never trafficked, which is a report accusing the whole traffic team on the
strength of a stale file. So the products must have come from Knack itself, or
this answers `measured: False` and says why.

**An order newer than the product read is not judged at all.** A stale cache
is a real Knack read of an earlier day, and an order written after it was
taken could not appear in it however long ago it was sent. Those are counted
as waiting with the reason named, rather than refusing the whole report over a
cache that is perfectly good for everything older than itself.

**An order submitted this morning is not late.** Setting a campaign up takes
days, so an order inside `GRACE_DAYS` is counted and not chased. A report that
fires on every order the day it is written is one nobody reads.

**An order whose flight has already started is its own urgency.** A campaign
whose start date has passed with nothing in Knack is running in nobody's
system -- not trafficked, not billed, and the client is expecting it. Those
sort to the top and are counted separately, because "late to be set up" and
"should be live right now" send somebody to two different conversations.

**The activity log rotates, so this cannot claim to be complete.** An order old
enough to have aged out is invisible here unless it came from a converted
proposal -- those live in the quotes table, which does not rotate. The report
says how far back it can see rather than implying it looked at everything.

**An order with no number cannot be reconciled at all**, and is its own row
rather than being counted as missing: there is nothing to look up for it, which
is a different finding from a campaign nobody set up. Every entry written
before `submit_io()` was corrected is one of these -- it read `order_number`
from a payload whose key is `orderNumber`, so the number was blank on every
`io_submitted` entry the route has ever written, while the `client_registered`
entry beside it read the real key and got it right.

**A row somebody has settled leaves the list, and the mark is applied on
read.** Some orders are never going to appear in Knack: one cancelled before
it was trafficked, one superseded by a renumbered order, one that turned out
to be a test. Left in, those are permanent red on a report whose whole job is
to say what to act on this week -- the failure `hub/creative_evergreen.py` was
written for, and the reason the mark is applied on every read rather than
baked into the cached rows: there are two gunicorn workers, so a mark taken in
one of them would go on being ignored by the other until its own copy expired,
which is a button that appears to do nothing.

**Nothing is written to Knack, to Smart 1 Suite or to a quote.** The settle
mark is a small Hub overlay and everything else here is a reading.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

from hub import jsonstore

# How long a campaign is given to appear in Knack before the order is called
# outstanding. A week covers the ordinary case -- trafficking is not same-day
# work -- without letting a dropped order sit for a month.
GRACE_DAYS = 7

# How much of the activity log to read. `audit.tail` sizes a byte window from
# this and then filters, so it is a window into the recent log rather than a
# scan of the whole file.
LOG_LIMIT = 4000

# Product sources this report may be measured against. The committed export is
# excluded deliberately: it is a hand-refreshed snapshot whose rows are the raw
# Knack records rather than `_row()` output, so it carries no IO number to
# compare and every order would read as a campaign nobody set up.
LIVE_SOURCES = ("knack", "knack (stale)")

# Why an order is never going to appear in Knack. Free text is allowed beside
# it, but the reason is picked from a list so the report can say how many rows
# left for each -- "cancelled" and "renumbered" are different stories about
# how we work, and one of them is worth fixing upstream.
SETTLE_REASONS = {
    "cancelled": "Cancelled before it was trafficked",
    "renumbered": "The campaign is in Knack under a different order number",
    "test": "A test or duplicate order that was never meant to run",
    "other": "Something else — see the note",
}


def _norm_io(value) -> str:
    """An IO number as a comparable key. Blank where there is nothing to
    compare -- punctuation and case differ between what a rep types on an
    order and what is keyed into Knack, and neither spelling is wrong."""
    return re.sub(r"[^0-9a-z]+", "", str(value or "").lower())


def _aware(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _day(value):
    """A start date as a date, from whatever the wizard stored. The IO's own
    field is free text a rep types, so an unparseable one is None rather than
    an exception -- a start nobody can read is not a flight that has begun."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return _aware(text)


# ------------------------------------------------------------------ settling
def _settle_path() -> str:
    return os.path.join(jsonstore.data_dir("hub"), "io_reconcile_settled.json")


def settled() -> dict:
    """{normalised order: row}. Never raises — a caller is mid-render."""
    rows = jsonstore.read_json(_settle_path(), default={})
    return rows if isinstance(rows, dict) else {}


def settle(order: str, reason: str = "other", note: str = "",
           actor: str = "") -> dict:
    """Mark an order as one that is never going to appear in Knack."""
    key = _norm_io(order)
    if not key:
        return {"ok": False, "error": "That order has no number to settle."}
    if reason not in SETTLE_REASONS:
        return {"ok": False,
                "error": f"{reason!r} is not one of the reasons this offers."}
    rows = settled()
    rows[key] = {
        "order": str(order or ""),
        "reason": reason,
        "note": str(note or "")[:400],
        # Somebody's decision about a campaign, and one nobody can attribute
        # is one nobody can revisit.
        "by": str(actor or "")[:60],
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    jsonstore.write_json(_settle_path(), rows)
    return {"ok": True, "order": str(order or ""), "row": rows[key]}


def unsettle(order: str) -> bool:
    """Put an order back on the list."""
    rows = settled()
    key = _norm_io(order)
    if key not in rows:
        return False
    rows.pop(key, None)
    jsonstore.write_json(_settle_path(), rows)
    return True


# ------------------------------------------------------------------- reading
def submitted() -> dict:
    """Every insertion order this Hub sent, from both places that remember one.

    The activity log is what the IO Builder writes on submit; the quotes table
    is what the Proposal Builder writes when a quote is converted. They overlap
    for an order that began as a proposal, and each sees orders the other does
    not, so both are read and the row says which knew about it.
    """
    out: dict[str, dict] = {}
    errors: list[str] = []
    log_oldest = None
    blank = 0

    def _row(key: str, order: str) -> dict:
        return out.setdefault(key, {
            "order": order, "client": "", "at": None, "sources": [],
            "actor": "", "partner": "", "start": None, "monthly": 0.0,
            "quote": "", "quote_id": None,
        })

    try:
        from hub import audit
        for entry in audit.tail(limit=LOG_LIMIT, module="io_builder",
                                type_="io_submitted"):
            when = _aware(entry.get("time") or entry.get("at")
                          or entry.get("ts"))
            if when and (log_oldest is None or when < log_oldest):
                log_oldest = when
            order = str(entry.get("order") or "")
            key = _norm_io(order)
            if not key:
                # Its own finding, and it must not collide with the next one:
                # nothing about a blank order identifies it.
                blank += 1
                key = f"!noorder:{blank}"
            row = _row(key, order)
            row["client"] = row["client"] or str(entry.get("client") or "")
            row["actor"] = row["actor"] or str(entry.get("actor") or "")
            row["partner"] = row["partner"] or str(entry.get("partner") or "")
            row["start"] = row["start"] or _day(entry.get("start"))
            try:
                row["monthly"] = row["monthly"] or float(entry.get("monthly") or 0)
            except (TypeError, ValueError):
                pass
            # The earliest submission of a number is when the clock started:
            # a re-submitted order is the same order, and dating it from the
            # re-send would reset a campaign that has been outstanding for a
            # month back to nothing.
            if when and (row["at"] is None or when < row["at"]):
                row["at"] = when
            if "activity log" not in row["sources"]:
                row["sources"].append("activity log")
    except Exception as exc:                            # noqa: BLE001
        errors.append(f"the activity log could not be read ({type(exc).__name__})")

    try:
        mod = _quote_module()
        db = mod.SessionLocal()
        try:
            for q in db.query(mod.Quote).filter(mod.Quote.status == "Converted").all():
                order = str(q.io_number or "")
                if not _norm_io(order):
                    # A converted quote with no order number on it is a
                    # different gap -- the conversion form asks for one -- and
                    # it is not an order this can look anything up for.
                    continue
                row = _row(_norm_io(order), order)
                row["client"] = row["client"] or (q.client or "")
                row["quote"] = q.quote_number or ""
                row["quote_id"] = q.id
                row["monthly"] = row["monthly"] or float(q.monthly_budget or 0)
                when = _aware(q.converted_at)
                if when and (row["at"] is None or when < row["at"]):
                    row["at"] = when
                if "converted proposal" not in row["sources"]:
                    row["sources"].append("converted proposal")
        finally:
            db.close()
    except Exception as exc:                            # noqa: BLE001
        errors.append(f"the proposal builder could not be read "
                      f"({type(exc).__name__})")

    return {"orders": list(out.values()), "errors": errors,
            "log_oldest": log_oldest, "blank": blank}


def _quote_module():
    """The Proposal Builder, as the app actually loaded it.

    `wsgi.py` imports it under the name `salesb_app`, so a plain import here
    would create a second instance with its own declarative mapping of the
    same tables — the arrangement `hub/sales_status.py` and `hub/ghl_hooks.py`
    both settled.
    """
    import sys
    mod = sys.modules.get("salesb_app")
    if mod is not None:
        return mod
    from modules.sales_builder import app as mod        # noqa: PLC0415
    return mod


def landed() -> dict:
    """The IO numbers Knack's products carry, and whether we could ask."""
    try:
        from hub import knack_products
        got = knack_products.rows()
    except Exception as exc:                            # noqa: BLE001
        return {"numbers": {}, "measured": False, "source": "none",
                "read_at": None,
                "error": f"Knack products could not be read "
                         f"({type(exc).__name__})."}
    source = str(got.get("source") or "none")
    numbers: dict[str, dict] = {}
    for r in (got.get("rows") or []):
        if not isinstance(r, dict):
            continue
        key = _norm_io(r.get("io"))
        if key:
            numbers.setdefault(key, r)
    if source not in LIVE_SOURCES:
        return {"numbers": numbers, "measured": False, "source": source,
                "read_at": None,
                "error": ("The products came from " + source +
                          ", which nothing refreshes — an order written since "
                          "it was generated could not appear in it, so every "
                          "recent one would read as a campaign nobody set up.")}
    age = got.get("age_minutes")
    read_at = None
    try:
        read_at = datetime.now(timezone.utc) - timedelta(minutes=float(age or 0))
    except (TypeError, ValueError):
        read_at = None
    return {"numbers": numbers, "measured": True, "source": source,
            "error": "", "age_minutes": age, "read_at": read_at}


def _io_only_orders() -> dict:
    """{normalised order: client row} for clients still on an IO overlay.

    The same gap seen from the other end: a client standing on an overlay is
    one Knack has never heard of, so their campaign was not written either.
    """
    out = {}
    try:
        from hub import io_clients
        for row in io_clients.overlay().values():
            for order in (row.get("orders") or []):
                key = _norm_io(order)
                if key:
                    out[key] = row
    except Exception:                                   # noqa: BLE001
        pass
    return out


def report(now=None) -> dict:
    """Orders we sent that Knack has no campaign for."""
    now = _aware(now) or datetime.now(timezone.utc)
    sent = submitted()
    knack = landed()

    if not knack["measured"]:
        # Never a clean list: with no live product read, every order would be
        # reported as never trafficked.
        return {"measured": False, "outstanding": [], "unreconcilable": [],
                "settled": [], "checked": 0, "waiting": 0, "running": 0,
                "error": knack["error"], "errors": sent["errors"],
                "log_oldest": sent["log_oldest"]}

    io_only = _io_only_orders()
    marks = settled()
    grace = timedelta(days=GRACE_DAYS)
    read_at = knack.get("read_at")

    outstanding, unreconcilable, settled_rows = [], [], []
    waiting = after_read = running = 0

    for row in sent["orders"]:
        key = _norm_io(row.get("order"))
        if not key:
            unreconcilable.append(dict(row))
            continue
        if key in knack["numbers"]:
            continue

        mark = marks.get(key)
        if mark:
            settled_rows.append(dict(row, settled=mark))
            continue

        when = row.get("at")
        if when and read_at is not None and when > read_at:
            # Newer than the products we are comparing against. Not a finding
            # about the order — a fact about the snapshot.
            after_read += 1
            continue
        if when and (now - when) < grace:
            waiting += 1
            continue

        out = dict(row)
        out["days"] = int((now - when).days) if when else None
        out["client_is_io_only"] = key in io_only
        start = row.get("start")
        out["started"] = bool(start and start <= now)
        if out["started"]:
            running += 1
        outstanding.append(out)

    # Most urgent first: a flight that has already started is live in nobody's
    # system, which is a different conversation from one that is merely late.
    outstanding.sort(key=lambda r: (not r["started"], r["days"] is None,
                                    -(r["days"] or 0)))
    settled_rows.sort(key=lambda r: r["settled"].get("at", ""), reverse=True)

    return {
        "measured": True,
        "outstanding": outstanding,
        "unreconcilable": unreconcilable,
        "settled": settled_rows,
        "checked": len(sent["orders"]),
        "waiting": waiting,
        "after_read": after_read,
        "running": running,
        "knack_source": knack["source"],
        "knack_age_minutes": knack.get("age_minutes"),
        "errors": sent["errors"],
        "log_oldest": sent["log_oldest"],
        "error": "",
    }


def note(data: dict) -> str:
    """The sentence under the table. Says what it could not see."""
    data = data or {}
    if not data.get("measured"):
        return (data.get("error")
                or "The insertion orders could not be reconciled.")
    bits = []
    n = len(data.get("outstanding") or [])
    if n:
        bits.append(f"{n} insertion order{'' if n == 1 else 's'} we sent "
                    f"{'has' if n == 1 else 'have'} no campaign in Knack "
                    f"carrying {'its' if n == 1 else 'their'} number")
        run = data.get("running") or 0
        if run:
            which = ("It" if run == n == 1 else
                     "All of them" if run == n else f"{run} of them")
            bits.append(f"{which} should be running now — the flight has "
                        "started and nothing is trafficked against "
                        f"{'it' if run == 1 else 'them'}")
    elif data.get("settled"):
        # "Every order has a campaign" would be false: one of them has a
        # settle mark instead, which is the opposite of a campaign.
        bits.append("Nothing outstanding — every order this can see either "
                    "has a campaign in Knack carrying its number or has been "
                    "settled as one that never will")
    else:
        c = data.get("checked", 0)
        bits.append("The one order this can see has a campaign in Knack "
                    "carrying its number" if c == 1 else
                    f"Every one of the {c} orders this can see has a campaign "
                    "in Knack carrying its number")
    if data.get("waiting"):
        w = data["waiting"]
        bits.append(f"{w} submitted within the last {GRACE_DAYS} days "
                    f"{'is' if w == 1 else 'are'} not counted yet — setting a "
                    "campaign up is not same-day work")
    if data.get("after_read"):
        a = data["after_read"]
        bits.append(f"{a} {'was' if a == 1 else 'were'} submitted after the "
                    "products were last read, so there is nothing yet to find "
                    f"{'it' if a == 1 else 'them'} in")
    if data.get("unreconcilable"):
        u = len(data["unreconcilable"])
        bits.append(f"{u} went out with no order number recorded, so there is "
                    "nothing to look up for "
                    f"{'it' if u == 1 else 'them'}")
    if data.get("settled"):
        bits.append(f"{len(data['settled'])} settled as never expected in Knack")
    oldest = data.get("log_oldest")
    if oldest:
        bits.append("the activity log rotates, so this sees submitted orders "
                    "back to " + oldest.strftime("%B %-d, %Y")
                    + " plus every converted proposal, which do not rotate")
    for err in data.get("errors") or []:
        bits.append(err)
    # Each bit is a sentence, so each starts with a capital. Joined raw, the
    # ones that begin with a lowercase word ("the activity log rotates…", and
    # every error line) read as a sentence that lost its beginning.
    return ". ".join(b[:1].upper() + b[1:] for b in bits if b) + "."
