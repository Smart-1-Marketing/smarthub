"""The insertion order as a record, not just a PDF and a webhook.

## Why this exists

Submitting an insertion order allocated a number from a Postgres sequence,
built two PDFs into Cloudinary, wrote one line in the activity log, and POSTed
the whole campaign to Smart 1 Suite. **Then it kept nothing.** There was no
insertion-order table, no list of what had been sent, no way to reopen one,
and no way to answer "what have we written for this client" until the campaign
appeared in Knack weeks later.

Three things follow from that, and all three were live:

  * `hub/io_reconcile.py` had to be assembled out of the **activity log**,
    which rotates — so the one report about orders that were never trafficked
    could only see as far back as the log did, and said so because there was
    nothing better to read.
  * That log line carried an **empty order number** on every entry the route
    had ever written, and nothing noticed for months, because nothing read it.
  * A rep who is asked "what did we send them in July?" opens Cloudinary, or
    asks the person who built it.

So the order is written down. One row per order number, carrying what was
agreed rather than the whole wizard state.

## The rules

**One file per order, never one file holding all of them.** Two reps
submitting at the same moment would each write the whole collection back and
the second write would drop the first one's order — the `hub/drafts.py` rule,
and the stakes here are a signed document rather than a draft.

**A resubmission updates the order, it does not add a second one.** A
correction sent an hour later is the same order at a new revision, and two
rows under one number is how a client record grows three identical entries
with no way to tell which is current — what `upsert_from_ghl` learned from
GoHighLevel first. Each submission is appended to a short history so the
correction is visible without the row splitting.

**The row is written whether or not Suite took it.** An order the client has
been sent is an order, and "delivered to Suite" and "built, and Suite refused
it" are different states that send somebody to different places — only the
second needs re-sending. Recording only the successes is how the ones that
need chasing become the ones nobody can see.

**Nothing here may raise.** An insertion order must never fail to submit over
its own bookkeeping — the rule `submit_io` already applies to the activity log
and the client overlay beside it. Every entry point returns a value.

**What is stored is the agreement, not the wizard.** The campaign state is
tens of kilobytes of answers, working notes and generated copy; what a record
has to answer is who, what, when, how much, and where the document is. Lines
are capped and so is the row, because this is the only copy and it lives on a
5 GB disk shared with everything else.

**An allocated number that never became an order is recorded too.** The
sequence hands a number out at the start of the wizard, so an abandoned IO
burns one and leaves a gap somebody in accounting eventually asks about.
`note_allocated()` is what makes that answerable, and it is deliberately a
*note* rather than a row of its own: an allocation is not an order, and a
listing that mixed them would report work that was never sent.

**Nothing is written to Knack, to Smart 1 Suite or to a quote.** This is the
Hub writing down what the Hub did.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

from hub import jsonstore

log = logging.getLogger(__name__)

# A campaign with more lines than this is not a campaign, it is a paste
# accident. Kept rather than refused, because the order still went.
MAX_LINES = 60
# One order's record is a few kilobytes. This is a firm stop well short of
# anything that could fill the disk, with room for the largest real one.
MAX_BYTES = 256_000
# How many submissions of one order to remember. A correction or two is
# ordinary; a hundred is not a history anybody reads.
MAX_HISTORY = 20
# Allocated numbers to keep. The sequence only goes up, so this is a window on
# the recent ones rather than a ledger of every number ever issued.
MAX_ALLOCATIONS = 500

_KEY_RE = re.compile(r"[^0-9A-Za-z_-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dir() -> str:
    return jsonstore.data_dir("io_orders")


def key_for(order) -> str:
    """A file-safe key for an order number, or "".

    Never a path fragment from a request: the number reaches this from a
    browser payload, and a key built by concatenation is how a store comes to
    write outside its own directory.
    """
    raw = _KEY_RE.sub("", str(order or "").strip())
    return raw[:40]


def _path(order) -> str:
    return os.path.join(_dir(), f"{key_for(order)}.json")


def _text(value, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def _num(value) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _lines(items) -> list[dict]:
    """The products on the order, in the shape a person reads them.

    Deliberately not the raw wizard items: those carry working notes, AI
    rationale and per-product flags nothing downstream reads, and this is the
    only copy of the record on a disk shared with everything else.
    """
    out = []
    for item in (items or [])[:MAX_LINES]:
        if not isinstance(item, dict):
            continue
        out.append({
            "product": _text(item.get("product") or item.get("name"), 160),
            "category": _text(item.get("category"), 120),
            "rate": _text(item.get("rate"), 40),
            "rate_type": _text(item.get("rateType") or item.get("rate_type"), 20),
            "budget": _num(item.get("budget")),
            "campaign_budget": _num(item.get("campaignBudget")
                                    or item.get("campaign_budget")),
            "start": _text(item.get("start"), 40),
            "end": _text(item.get("end"), 40),
        })
    return out


def _summary(payload: dict) -> dict:
    """One submitted order, reduced to what a record has to answer."""
    p = payload if isinstance(payload, dict) else {}
    items = p.get("items") or []
    return {
        "order": _text(p.get("orderNumber") or p.get("order_number"), 40),
        "client": _text(p.get("client") or p.get("client_name"), 200),
        "url": _text(p.get("url") or p.get("client_website"), 300),
        "partner": _text(p.get("partner"), 160),
        "sales_contact": _text(p.get("salesContact") or p.get("sales_contact"), 120),
        "sales_email": _text(p.get("salesEmail") or p.get("sales_email"), 160),
        "io_type": _text(p.get("ioType") or p.get("io_type"), 80),
        "start": _text(p.get("start"), 40),
        "end": _text(p.get("end"), 40),
        "monthly": round(sum(_num(i.get("budget")) for i in items
                             if isinstance(i, dict)), 2),
        "campaign_total": round(sum(_num(i.get("campaignBudget")) for i in items
                                    if isinstance(i, dict)), 2),
        "lines": _lines(items),
        "line_count": len([i for i in items if isinstance(i, dict)]),
        "client_pdf": _text(p.get("client_pdf_url"), 500),
        "internal_pdf": _text(p.get("internal_pdf_url"), 500),
        "replaces_io": _text(p.get("replacesIo") or p.get("replaces_io"), 40),
        "replaces_io_end": _text(p.get("replacesIoEnd")
                                 or p.get("replaces_io_end"), 40),
    }


def _domain(url: str) -> str:
    try:
        from hub.client_context import canonical_domain
        return canonical_domain(url) or ""
    except Exception:                                   # noqa: BLE001
        return ""


def record(payload: dict, *, delivered: bool = False, error: str = "",
           status: int | None = None, actor: str = "") -> dict:
    """Write down one submitted insertion order. Never raises.

    `delivered` is what Smart 1 Suite actually did with it, so the record can
    tell an order that reached the CRM from one that was built and refused —
    the second is the only one anybody has to do something about.
    """
    try:
        summary = _summary(payload)
        order = summary["order"]
        key = key_for(order)
        if not key:
            # There is nothing to file it under and nothing to look it up by.
            # Said rather than swallowed: an order with no number is exactly
            # the case `hub/io_reconcile.py` reports as unreconcilable.
            return {"ok": False, "reason": "no order number",
                    "note": "The order carried no number, so there is nothing "
                            "to file the record under."}

        row = get(order) or {}
        # When the number was taken and by whom. note_allocated() files that
        # in _allocations.json, and these two fields sat on every record with
        # nothing ever writing them — "" twice, reading as "not recorded"
        # about a fact the store was holding. Joined on the first write only:
        # a row that already carries an allocation keeps it, and a lookup
        # that fails costs the two fields and never the record.
        alloc = {}
        if not row.get("allocated_at"):
            try:
                alloc = allocations().get(key) or {}
            except Exception:                           # noqa: BLE001
                alloc = {}
        submissions = list(row.get("submissions") or [])
        submissions.append({"at": _now(), "by": _text(actor, 60),
                            "delivered": bool(delivered),
                            "error": _text(error, 300)})
        summary["domain"] = _domain(summary["url"])
        summary.update({
            # The first submission is when the client got the document; the
            # last is when it was corrected. Both are on the row, because a
            # record that only kept the latest would date an order written in
            # March to the day somebody fixed a typo in September.
            "submitted_at": row.get("submitted_at") or _now(),
            "submitted_by": row.get("submitted_by") or _text(actor, 60),
            "last_submitted_at": _now(),
            "resubmitted": len(submissions) > 1,
            "submissions": submissions[-MAX_HISTORY:],
            # Three facts, not one. `delivered` is what the latest attempt
            # did; `ever_delivered` is whether Suite holds any version of
            # this order at all. A correction that failed after a first
            # submission that landed leaves Suite holding the *old* version,
            # which is a real state and not the same as an order that never
            # reached it — and only the second means the order is in neither
            # system.
            "suite": {"delivered": bool(delivered),
                      "ever_delivered": bool(delivered) or bool(
                          (row.get("suite") or {}).get("ever_delivered")
                          or (row.get("suite") or {}).get("delivered")),
                      "delivered_at": (_now() if delivered else
                                       (row.get("suite") or {}).get("delivered_at")
                                       or (row.get("suite") or {}).get("at", "")),
                      "error": _text(error, 300),
                      "status": status},
            "allocated_at": row.get("allocated_at") or _text(alloc.get("at"), 40),
            "allocated_by": row.get("allocated_by") or _text(alloc.get("by"), 60),
        })

        import json
        blob = json.dumps(summary)
        if len(blob.encode("utf-8")) > MAX_BYTES:
            # Drop the lines rather than the record: who, when and how much
            # are what the row exists for, and a row refused for size is an
            # order with no trace at all.
            summary["lines"] = []
            summary["lines_dropped"] = True

        jsonstore.write_json(_path(order), summary)
        return {"ok": True, "order": order, "row": summary,
                "resubmitted": summary["resubmitted"]}
    except Exception as exc:                            # noqa: BLE001
        log.warning("io record write failed: %s", exc)
        return {"ok": False, "reason": f"{type(exc).__name__}",
                "note": "The order went; the Hub could not write its own "
                        "record of it."}


def get(order) -> dict | None:
    """One order's record, or None."""
    key = key_for(order)
    if not key:
        return None
    try:
        row = jsonstore.read_json(_path(order), default=None)
    except Exception as exc:                            # noqa: BLE001
        log.warning("io record read failed: %s", exc)
        return None
    return row if isinstance(row, dict) and row.get("order") else None


def _rows() -> list[dict]:
    try:
        names = sorted(os.listdir(_dir()))
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".json") or name == "_allocations.json":
            continue
        row = get(name[:-5])
        if row:
            out.append(row)
    return out


def flight_start(record: dict) -> str:
    """When this order's campaign actually starts.

    The stored `start` is the *shared* campaign start, and an order whose
    products run their own dates genuinely has none: the wizard clears it the
    moment one product is given its own term ("Because at least one product
    runs its own dates, I'll ask for dates product by product"), so `start`
    is `""` on every such order. That is right for the record — what is stored
    is the agreement — and wrong for anybody asking whether the flight has
    begun. `io_reconcile`'s "should be running right now" bucket read the bare
    field and was therefore blind to a whole class of orders: the headline
    urgency on that report, silently never firing for a multi-product IO.

    So the campaign start when there is one, and otherwise the earliest date
    any line starts on, because that is when some of this order is live.
    Returns "" when nothing carries a date rather than inventing one; a row
    with no date at all is reported as waiting rather than as late.
    """
    if not isinstance(record, dict):
        return ""
    shared = _text(record.get("start"), 40)
    if shared:
        return shared
    dates = sorted(d for d in (_text(l.get("start"), 40)
                               for l in record.get("lines") or []
                               if isinstance(l, dict)) if d)
    return dates[0] if dates else ""


def listing(client: str = "") -> dict:
    """Orders we have sent, newest first. `(rows, measured, error)` in a dict.

    "Nobody has sent an order" and "we could not read the store" are different
    answers and only the first means there is nothing to look at — the rule
    `connected_accounts_result()` gives in Google Finder.
    """
    try:
        rows = _rows()
    except Exception as exc:                            # noqa: BLE001
        return {"rows": [], "measured": False,
                "error": f"The insertion order records could not be read "
                         f"({type(exc).__name__})."}
    # Exact normalised name or nothing. The record carries the name the
    # wizard was given, and a substring pass here would put one company's
    # insertion order on another company's record.
    want = _match_key(client)
    if want:
        rows = [r for r in rows if _match_key(r.get("client")) == want]
    rows.sort(key=lambda r: str(r.get("last_submitted_at")
                                or r.get("submitted_at") or ""), reverse=True)
    return {"rows": rows, "measured": True, "error": ""}


def _match_key(name) -> str:
    """A client name as a comparable key — exact or not at all.

    `hub/client_key.py`'s rule: "Riverside HVAC" must not collect "Riverside
    HVAC Supply", because attributing one company's insertion order to another
    is the worst outcome available to a client record.
    """
    try:
        from hub.client_key import normalise_name
        return normalise_name(str(name or ""))
    except Exception:                                   # noqa: BLE001
        return str(name or "").strip().lower()


# ------------------------------------------------------- allocated, not sent
def _alloc_path() -> str:
    return os.path.join(_dir(), "_allocations.json")


def note_allocated(order, actor: str = "") -> bool:
    """Remember that this number was handed out. Never raises.

    The sequence issues a number at the *start* of the wizard, so an
    abandoned IO burns one and leaves a gap in the numbering that nobody can
    explain later. Recording the allocation is what makes that answerable —
    and it stays a note rather than an order row, because an allocation is not
    an order and a listing that mixed them would report work nobody sent.
    """
    key = key_for(order)
    if not key:
        return False
    try:
        rows = jsonstore.read_json(_alloc_path(), default={})
        if not isinstance(rows, dict):
            rows = {}
        if key not in rows:
            rows[key] = {"order": _text(order, 40), "at": _now(),
                         "by": _text(actor, 60)}
        if len(rows) > MAX_ALLOCATIONS:
            keep = sorted(rows.items(), key=lambda kv: str(kv[1].get("at") or ""),
                          reverse=True)[:MAX_ALLOCATIONS]
            rows = dict(keep)
        # Durable: an allocation cannot be rebuilt from anything. The
        # sequence only reports the next number, never what became of the
        # last one.
        jsonstore.write_json(_alloc_path(), rows)
        return True
    except Exception as exc:                            # noqa: BLE001
        log.warning("io allocation note failed: %s", exc)
        return False


def allocations() -> dict:
    rows = jsonstore.read_json(_alloc_path(), default={})
    return rows if isinstance(rows, dict) else {}


def unused_allocations() -> list[dict]:
    """Numbers handed out that never became an order.

    Not a finding on its own — a rep who starts an IO and thinks better of it
    is doing nothing wrong — but it is the only answer there is to "why is
    there no order 10407", and without it the gap is unexplainable.
    """
    sent = {key_for(r.get("order")) for r in _rows()}
    out = [dict(v) for k, v in allocations().items() if k not in sent]
    out.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    return out
