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
            "quote": "", "quote_id": None, "not_delivered": False,
            "line_count": None,
        })

    # The Hub's own record of every order it sent. This is the durable half:
    # it does not rotate, it carries the flight and the money, and it knows
    # whether Smart 1 Suite actually took the order. The activity log below is
    # what answers for orders written before that store existed.
    records = 0
    try:
        from hub import io_records
        got = io_records.listing()
        if not got.get("measured"):
            errors.append(got.get("error") or
                          "the insertion order records could not be read")
        for rec in got.get("rows") or []:
            order = str(rec.get("order") or "")
            key = _norm_io(order)
            if not key:
                continue
            records += 1
            row = _row(key, order)
            row["client"] = row["client"] or str(rec.get("client") or "")
            row["actor"] = row["actor"] or str(rec.get("submitted_by") or "")
            row["partner"] = row["partner"] or str(rec.get("partner") or "")
            # `flight_start()`, not the bare field: an order whose products
            # run their own dates has no shared campaign start, so this read
            # "" for every one of them and `started` could never be true --
            # the report's own headline bucket, blind to a whole class of IOs.
            row["start"] = row["start"] or _day(io_records.flight_start(rec))
            row["monthly"] = row["monthly"] or float(rec.get("monthly") or 0)
            if row["line_count"] is None and rec.get("line_count") is not None:
                row["line_count"] = rec.get("line_count")
            when = _aware(rec.get("submitted_at"))
            if when and (row["at"] is None or when < row["at"]):
                row["at"] = when
            # An order Suite never took is still an order the client has,
            # and it is a second thing to do about the same row. Asked of
            # `ever_delivered`, not of the latest attempt: an order Suite
            # holds an earlier version of has reached Suite, and calling that
            # "in neither system" would be the confident wrong answer.
            if not ((rec.get("suite") or {}).get("ever_delivered")):
                row["not_delivered"] = True
            if "order record" not in row["sources"]:
                row["sources"].append("order record")
    except Exception as exc:                            # noqa: BLE001
        errors.append(f"the insertion order records could not be read "
                      f"({type(exc).__name__})")

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
            "log_oldest": log_oldest, "blank": blank, "records": records}


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
    # Every product carrying each number, not the first one found: whether a
    # campaign was trafficked as one row or six is exactly what the delivery
    # comparison below has to be able to see.
    numbers: dict[str, list] = {}
    for r in (got.get("rows") or []):
        if not isinstance(r, dict):
            continue
        key = _norm_io(r.get("io"))
        if key:
            numbers.setdefault(key, []).append(r)
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
                "log_oldest": sent["log_oldest"],
                "has_records": bool(sent.get("records"))}

    io_only = _io_only_orders()
    # Numbers the sequence handed out that never became an order. Not a
    # finding — a rep who starts an IO and thinks better of it is doing
    # nothing wrong — but it is the only answer there is to "why is there no
    # order 10407", and the gap is otherwise unexplainable.
    try:
        from hub import io_records
        unused = len(io_records.unused_allocations())
    except Exception:                                   # noqa: BLE001
        unused = 0
    marks = settled()
    grace = timedelta(days=GRACE_DAYS)
    read_at = knack.get("read_at")

    outstanding, unreconcilable, settled_rows = [], [], []
    waiting = after_read = running = undelivered = 0

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
        if out.get("not_delivered"):
            undelivered += 1
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
        "has_records": bool(sent.get("records")),
        "not_delivered": undelivered,
        "unused_numbers": unused,
        "error": "",
    }


# ---------------------------------------------------------- what was bought
#
# `report()` above asks whether a campaign exists. This asks whether it is the
# campaign we sold — the next link, and the one the order record made
# possible: before `hub/io_records.py` there was nothing on our side to
# compare against, because the only trace of an order was a log line carrying
# a number and a client name.
#
# **The finding is the money, and the counts are never the finding.** An
# insertion order of six lines may be trafficked in Knack as six product rows
# or as one, and nothing readable from here says which convention this book
# follows. A check that fired on every order because the shop writes one row
# per campaign is a check somebody switches off within a week — the note
# `hub/qr_codes.py` makes about a warning that fires on every social spot. So
# the line counts are printed beside each row as context and no row is ever
# raised for them; what is compared is the monthly, which is the same number
# however many rows it was split across.
#
# **Both figures are always shown, and so is the difference.** A report that
# says "discrepancy" without printing the two numbers behind it is one nobody
# can check, and the first person to find it wrong stops reading the rest.
#
# **Over and under are different conversations.** A campaign trafficked for
# less than the order is delivery somebody is not getting; one trafficked for
# more is billing nobody wrote an order for. They are counted apart and the
# row says which.

# House figures. Nobody publishes a tolerance for this, so these are ours and
# are named as ours on the page: a campaign trafficked to the exact dollar is
# not the normal case — a rounded rate, a part month at the start of a flight —
# and calling every one of those a finding is how the list stops being read.
MONEY_TOLERANCE_PCT = 5.0
MONEY_TOLERANCE_MIN = 50.0
TOLERANCE_SOURCE = "house"


def _knack_monthly(rows) -> tuple[float, bool, int]:
    """(total monthly, every row carried one, how many did not).

    A product row with no monthly cost is **not** counted as zero. A blank
    there would drag the campaign's total down and read as a campaign
    delivering less than it was sold, which is a finding invented out of a
    field nobody filled in.
    """
    total, blank = 0.0, 0
    for row in rows or []:
        raw = row.get("monthly")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        if raw in (None, "") or value is None:
            blank += 1
            continue
        total += value
    return round(total, 2), blank == 0, blank


def _tolerance(sold: float) -> float:
    return max(MONEY_TOLERANCE_MIN, abs(sold) * MONEY_TOLERANCE_PCT / 100.0)


def delivery(now=None) -> dict:
    """Orders whose campaign in Knack is not the campaign that was sold."""
    now = _aware(now) or datetime.now(timezone.utc)
    sent = submitted()
    knack = landed()

    if not knack["measured"]:
        return {"measured": False, "rows": [], "checked": 0, "matched": 0,
                "under": 0, "over": 0, "unmeasured": [], "waiting": 0,
                "error": knack["error"], "errors": sent["errors"]}

    marks = settled()
    grace = timedelta(days=GRACE_DAYS)
    read_at = knack.get("read_at")
    rows, unmeasured = [], []
    checked = matched = under = over = waiting = 0

    for order in sent["orders"]:
        key = _norm_io(order.get("order"))
        if not key or key in marks:
            continue
        products = knack["numbers"].get(key)
        if not products:
            # No campaign at all. That is `report()`'s finding, not this one,
            # and raising it twice on two screens is how a reader learns the
            # two reports disagree.
            continue

        when = order.get("at")
        if when and read_at is not None and when > read_at:
            waiting += 1
            continue
        if when and (now - when) < grace:
            # A campaign part-entered is not a campaign short-delivered.
            waiting += 1
            continue

        sold = round(float(order.get("monthly") or 0), 2)
        got_total, complete, blank = _knack_monthly(products)
        row = {
            "order": order.get("order", ""),
            "client": order.get("client", ""),
            "partner": order.get("partner", ""),
            "at": when,
            "start": order.get("start"),
            "sold": sold,
            "trafficked": got_total,
            "difference": round(got_total - sold, 2),
            "lines_sold": order.get("line_count"),
            "products": len(products),
            "blank_monthly": blank,
            "sources": order.get("sources") or [],
        }

        if not sold:
            # The order carried no monthly to compare against — an entry the
            # activity log wrote before the Hub kept its own records, or an
            # order whose lines were all one-time. Not measured, never a
            # finding of zero delivery.
            row["reason"] = ("no monthly was recorded on our side of this "
                             "order, so there is nothing to compare the "
                             "campaign against")
            unmeasured.append(row)
            continue
        if not complete:
            row["reason"] = (
                f"{blank} of the {len(products)} product"
                f"{'' if len(products) == 1 else 's'} under this number "
                f"{'carries' if blank == 1 else 'carry'} no monthly cost in "
                "Knack, so the campaign's total is not measurable — a blank "
                "counted as zero would read as under-delivery")
            unmeasured.append(row)
            continue

        checked += 1
        row["tolerance"] = round(_tolerance(sold), 2)
        if abs(row["difference"]) <= row["tolerance"]:
            matched += 1
            continue
        row["direction"] = "over" if row["difference"] > 0 else "under"
        if row["direction"] == "over":
            over += 1
        else:
            under += 1
        rows.append(row)

    # Worst gap first, in money rather than in percentage: a 4% gap on a
    # $40,000 campaign is the row to open before a 30% gap on $300.
    rows.sort(key=lambda r: -abs(r["difference"]))
    return {
        "measured": True, "error": "",
        "rows": rows, "unmeasured": unmeasured,
        "checked": checked, "matched": matched,
        "under": under, "over": over, "waiting": waiting,
        "knack_source": knack["source"],
        "knack_age_minutes": knack.get("age_minutes"),
        "errors": sent["errors"],
    }


def delivery_note(data: dict) -> str:
    """The sentence under the delivery table."""
    data = data or {}
    if not data.get("measured"):
        return (data.get("error")
                or "The campaigns could not be compared with the orders.")
    bits = []
    n = len(data.get("rows") or [])
    checked = data.get("checked", 0)
    if n and n == checked == 1:
        bits.append("The one campaign this could compare is not trafficked at "
                    "the money its insertion order was written for")
    elif n and n == checked:
        bits.append(f"None of the {checked} campaigns this could compare is "
                    "trafficked at the money its insertion order was written "
                    "for")
    elif n:
        bits.append(f"{n} of the {checked} campaigns this could compare "
                    f"{'is' if n == 1 else 'are'} not trafficked at the money "
                    "the insertion order was written for")
        if data.get("under"):
            u = data["under"]
            bits.append(f"{u} {'is' if u == 1 else 'are'} running for less "
                        "than the order, which is delivery the client is not "
                        "getting")
        if data.get("over"):
            o = data["over"]
            bits.append(f"{o} {'is' if o == 1 else 'are'} running for more, "
                        "which is billing nobody wrote an order for")
    elif checked == 1:
        bits.append("The one campaign this could compare is trafficked at the "
                    "money its insertion order was written for")
    else:
        bits.append(f"Every one of the {checked} campaigns this could compare "
                    "is trafficked at the money its insertion order was "
                    "written for")
    bits.append(f"“At the money” means within {MONEY_TOLERANCE_PCT:g}% or "
                f"${MONEY_TOLERANCE_MIN:,.0f}, whichever is larger — our own "
                "figure, not a published one, because a part first month and a "
                "rounded rate are ordinary")
    if data.get("unmeasured"):
        m = len(data["unmeasured"])
        bits.append(f"{m} could not be compared at all and "
                    f"{'is' if m == 1 else 'are'} listed with the reason")
    if data.get("waiting"):
        bits.append(f"{data['waiting']} were submitted too recently to judge — "
                    "a campaign part-entered is not a campaign short-delivered")
    bits.append("How many product rows a campaign was split into is shown "
                "beside each order and is never itself a finding: an order of "
                "six lines may be trafficked as six rows or as one, and "
                "nothing here can tell which this book does")
    for err in data.get("errors") or []:
        bits.append(err)
    return ". ".join(b[:1].upper() + b[1:] for b in bits if b) + "."


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
    else:
        # "Every order has a campaign" is only true when every order this saw
        # actually landed. An order still inside its grace period, one newer
        # than the product read, one settled and one with no number to look up
        # are each a reason there is nothing outstanding that is *not* a
        # campaign in Knack — and the bits below say which. Claiming the
        # stronger sentence over them is the confident wrong answer.
        accounted = (int(data.get("waiting") or 0)
                     + int(data.get("after_read") or 0)
                     + len(data.get("settled") or [])
                     + len(data.get("unreconcilable") or []))
        c = data.get("checked", 0)
        if accounted:
            bits.append("Nothing outstanding")
        elif c == 1:
            bits.append("The one order this can see has a campaign in Knack "
                        "carrying its number")
        else:
            bits.append(f"Every one of the {c} orders this can see has a "
                        "campaign in Knack carrying its number")
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
    if data.get("not_delivered"):
        n = data["not_delivered"]
        bits.append(f"{n} of {'them' if n > 1 else 'those'} "
                    f"{'was' if n == 1 else 'were'} never taken by Smart 1 "
                    "Suite either, so the order reached neither system")
    if data.get("unused_numbers"):
        u = data["unused_numbers"]
        bits.append(f"{u} order number{'' if u == 1 else 's'} "
                    f"{'was' if u == 1 else 'were'} handed out and never "
                    "became an order, which is why the numbering has gaps in "
                    "it")
    oldest = data.get("log_oldest")
    if oldest and not data.get("has_records"):
        # Only said while the activity log is genuinely the horizon. Once the
        # Hub keeps its own order records — which do not rotate — going on
        # saying this would understate what the report can see.
        bits.append("the Hub began keeping its own record of each order only "
                    "recently, so before that this reads the activity log, "
                    "which rotates: it sees orders back to "
                    + oldest.strftime("%B %-d, %Y")
                    + " plus every converted proposal, which do not rotate")
    for err in data.get("errors") or []:
        bits.append(err)
    # Each bit is a sentence, so each starts with a capital. Joined raw, the
    # ones that begin with a lowercase word ("the activity log rotates…", and
    # every error line) read as a sentence that lost its beginning.
    return ". ".join(b[:1].upper() + b[1:] for b in bits if b) + "."
