"""What the Hub's own screens say about the sales pipeline.

Two readers, one description. The Hub dashboard asks "is there anything to do
today"; the Proposal Builder's own dashboard asks the same question of the same
rows. Both are answered here rather than each assembling its own, for the
reason this codebase gives at length about `/api/db/structure` and
`/api/integrity`: two checks asking one question will answer it differently,
and both answers end up on screen.

## Why this exists

Three phases of work gave the Hub real knowledge about every proposal -- who
opened it and how many times, whether the pricing still stands, whether the
client accepted, what the campaign actually costs -- and **all of it was
readable only inside the Proposal Builder**. The Hub dashboard, the page
everyone opens first, carried eleven KPIs about *live* business (clients, live
products, live budget, websites, billing) and not one figure about pipeline:
nothing quoted, nothing waiting on a client, nothing won and not yet
trafficked. There was no scheduled sweep either.

That is the shape `hub/social_status.py` already answers next door, and its
note applies word for word: there is no mailer in this Hub, so the honest
route is putting it where people already look.

## The rules

**Nothing here may raise.** It is called while rendering a dashboard half the
company opens. Every failure resolves to `measured: False` with the reason
named, because *nothing is waiting* and *we could not read the quotes* are
different answers and only the first means there is nothing to do.

**It reads the open book and nothing else.** Draft, Sent and Approved -- the
quotes something could still be done about. A Converted or Lost quote is
finished, and walking every quote ever written on a page that loads on every
visit is the cost that gets a number turned off.

**A count is never a link to a page that cannot show it.** Every figure
carries the address that opens exactly the rows it counted, so pressing one
lands on those rows rather than on a tool the reader then has to filter.

**Each zero says which kind of zero it is.** "Nothing is waiting on a client"
and "no client link has ever been sent" render identically as a nought, and
only the second is somebody's to fix.

**Nothing is written anywhere.** This is a reading. It does not touch a quote,
it does not write to Smart 1 Suite, and it does not record having looked.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

MOUNT = "/sales/builder"

# The statuses something can still be done about. Converted has an insertion
# order behind it and Lost is finished; counting either would make every
# figure grow forever and none of it actionable.
OPEN_STATUSES = ("Draft", "Sent", "Approved")

# A quote inside this many days of its expiry is worth re-sending before the
# client tries to accept it and cannot. A week is the shortest notice that
# still leaves room to re-quote and get an answer.
EXPIRING_WITHIN_DAYS = 7


def _unavailable(exc: Exception) -> dict:
    return {"measured": False,
            "error": f"The proposals could not be read "
                     f"({type(exc).__name__})."}


def _module():
    """The Proposal Builder, as the app actually loaded it.

    `wsgi.py` imports it under the name `salesb_app`, so a plain import here
    would create a second instance with its own declarative mapping of the
    same tables -- the arrangement `hub/ghl_hooks.py` settled.
    """
    import sys
    mod = sys.modules.get("salesb_app")
    if mod is not None:
        return mod
    from modules.sales_builder import app as mod        # noqa: PLC0415
    return mod


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _state(row) -> dict:
    try:
        state = json.loads(row.data or "{}")
    except (TypeError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def scoreboard(limit: int = 6) -> dict:
    """What the pipeline is worth and what needs doing about it.

    Five signals, each a different job. They are deliberately not folded into
    one "needs attention" number: *they have not opened it*, *they read it and
    said nothing*, *the price is about to lapse* and *they said yes and nobody
    has written the order* send somebody to four different actions, and a
    single figure covering all four is one nobody can act on.
    """
    try:
        mod = _module()
        db = mod.SessionLocal()
    except Exception as exc:                            # noqa: BLE001
        return _unavailable(exc)

    try:
        from hub import quote_validity as validity
    except Exception as exc:                            # noqa: BLE001
        return _unavailable(exc)

    try:
        rows = (db.query(mod.Quote)
                .filter(mod.Quote.status.in_(OPEN_STATUSES))
                .order_by(mod.Quote.updated_at.desc()).all())
        ids = [r.id for r in rows]

        shares, views, accepted = {}, {}, set()
        if ids:
            for share in (db.query(mod.QuoteShare)
                          .filter(mod.QuoteShare.quote_id.in_(ids)).all()):
                # The newest share per quote: a link is minted once and kept,
                # so there is normally one, and the last one wins if not.
                shares[share.quote_id] = share
            for view in (db.query(mod.QuoteView)
                         .filter(mod.QuoteView.quote_id.in_(ids)).all()):
                views[view.quote_id] = views.get(view.quote_id, 0) + 1
            for acc in (db.query(mod.QuoteAcceptance)
                        .filter(mod.QuoteAcceptance.quote_id.in_(ids)).all()):
                accepted.add(acc.quote_id)

        now = datetime.now(timezone.utc)
        buckets = {"unopened": [], "waiting": [], "expiring": [],
                   "expired": [], "to_convert": []}
        pipeline = live_sent = 0
        shared_any = False

        for row in rows:
            share = shares.get(row.id)
            if share is not None and not share.revoked_at:
                shared_any = True
            pipeline += int(row.monthly_budget or 0)
            card = {
                "id": row.id,
                "quote": row.quote_number or "",
                "client": row.client or "",
                "monthly": int(row.monthly_budget or 0),
                "status": row.status or "",
                "url": f"{MOUNT}/?quote={row.id}",
            }

            if (row.status or "") == "Approved" and not (row.io_number or ""):
                # They said yes and nobody has written the order. The gap
                # count is what a rep needs to know before opening it, and it
                # is the module's own reading rather than a second one.
                try:
                    gaps = len(mod.compute_gaps(_state(row)))
                except Exception:                       # noqa: BLE001
                    gaps = None
                buckets["to_convert"].append(dict(card, gaps=gaps))
                continue

            if (row.status or "") != "Sent":
                continue
            live_sent += 1

            if row.id in accepted:
                # Accepted but still marked Sent: the acceptance route sets
                # Approved, so this is only reachable if somebody moved it
                # back. It is not a chase, and it is not counted as one.
                continue

            win = {}
            try:
                win = validity.window(
                    row.status or "",
                    sent_at=(share.sent_at if share else None),
                    created_at=row.created_at, state=_state(row), now=now)
            except Exception:                           # noqa: BLE001
                win = {}
            if win.get("expired"):
                buckets["expired"].append(dict(card, expires_on=win.get("expires_on", "")))
                continue
            days_left = win.get("days_left")
            if isinstance(days_left, int) and days_left <= EXPIRING_WITHIN_DAYS:
                buckets["expiring"].append(dict(card, days_left=days_left,
                                                expires_on=win.get("expires_on", "")))
                continue
            # Sent and never opened is a different job from sent and ignored:
            # the first means the link never reached them, and a rep who
            # chases the second when it was the first is chasing the wrong
            # thing. A quote with no live link at all is neither -- nobody has
            # sent it anywhere, which the line below says in words.
            opened = int(views.get(row.id) or 0)
            if share is None or share.revoked_at:
                continue
            if opened:
                buckets["waiting"].append(dict(card, opens=opened,
                                               sent_at=_iso(share.sent_at)))
            else:
                buckets["unopened"].append(dict(card, sent_at=_iso(share.sent_at)))

        counts = {k: len(v) for k, v in buckets.items()}
        # The ids behind each figure, so the list a count links to can show
        # exactly the rows it counted rather than a status tab that is nearly
        # the same thing. Unbounded on purpose and safe to be: this is the
        # open book, not every quote ever written.
        ids_by = {k: [r["id"] for r in v] for k, v in buckets.items()}
        keep = max(1, int(limit))
        # Most at risk first: a lapsed price is the only one of these that
        # actively stops a client saying yes.
        ordered = (buckets["expired"] + buckets["expiring"]
                   + buckets["unopened"] + buckets["waiting"]
                   + buckets["to_convert"])
        return {
            "measured": True, "error": "",
            "open_count": len(rows),
            "pipeline_monthly": pipeline,
            "counts": counts,
            "ids": ids_by,
            "attention": sum(counts.values()),
            "rows": ordered[:keep],
            "more": max(0, len(ordered) - keep),
            "urls": {
                "all": f"{MOUNT}/",
                **{key: f"{MOUNT}/?focus={key}" for key in buckets},
            },
            "line": _line(counts, len(rows), live_sent, shared_any),
        }
    except Exception as exc:                            # noqa: BLE001
        return _unavailable(exc)
    finally:
        try:
            db.close()
        except Exception:                               # noqa: BLE001
            pass


def _iso(value) -> str:
    value = _aware(value)
    return value.isoformat() if value else ""


def _line(counts: dict, open_count: int, live_sent: int, shared_any: bool) -> str:
    """The sentence beside the figures. Every zero says which kind it is."""
    if not open_count:
        return ("No open proposals. Every quote on the book is converted, "
                "lost, or has not been started.")
    if not shared_any:
        return (f"{open_count} open proposal{'' if open_count == 1 else 's'}, "
                "and no client link has been sent for any of them — nothing "
                "here is waiting on a client yet.")
    bits = []
    if counts.get("expired"):
        bits.append(f"{counts['expired']} whose pricing has lapsed")
    if counts.get("expiring"):
        bits.append(f"{counts['expiring']} expiring within the week")
    if counts.get("unopened"):
        bits.append(f"{counts['unopened']} never opened")
    if counts.get("waiting"):
        bits.append(f"{counts['waiting']} read with no answer")
    if counts.get("to_convert"):
        bits.append(f"{counts['to_convert']} accepted with no insertion order")
    if not bits:
        if not live_sent:
            return (f"{open_count} open proposal"
                    f"{'' if open_count == 1 else 's'}, none of them out with "
                    "a client yet.")
        return (f"{open_count} open, and nothing needs chasing today — every "
                "proposal that is out has been read and is still in date.")
    return "Needs attention: " + ", ".join(bits) + "."
