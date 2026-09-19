"""The client snapshot: a one-page printable export of the Client 360 record.

Every figure on it is read from a function the record already calls for its
own cards -- `hub/next_action.py`'s headline, `hub/record_health.py`'s
strip, `hub/client_money.py`'s two figures, `hub/client_upcoming.py`'s
dates -- so a snapshot printed this morning cannot say something the
screen it was printed from does not. Nothing here computes money, reads
Knack, or calls a provider; it only lays four already-computed answers out
on a page, through `hub/pdf_doc.py`'s shared canvas.

**Absent is not zero, on paper the same as it is on screen.** A source that
answered `measured: False` gets a line in the "Not measured" footnote
rather than a blank tile or an invented dash reading like a real one --
the rule every other reader on this record keeps, carried onto the one
document somebody might print and hand to someone else.

**Never raises.** `build()` reads each of the four sources in its own
try/except, exactly as `hub/client_money.for_client()` reads its own two
halves apart -- a source that raises costs its own section of the page and
never the download.
"""
from __future__ import annotations

import datetime as _dt

from reportlab.lib.colors import HexColor

from .pdf_doc import BAD, Doc, GOOD, MUTED, SOFT, WARN, clean

__all__ = ["build", "filename"]

_STATE_COLOR = {"bad": BAD, "warn": WARN, "ok": GOOD}
# Soft tints for the next-action banner's background -- the same three
# states the health strip's pills draw, lightened for a filled block
# rather than a thin dot.
_STATE_BG = {"bad": HexColor("#FDECEA"), "warn": HexColor("#FEF6E7"), "ok": HexColor("#EAF7EF")}


def filename(name: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "client").lower()).strip("-")
    today = _dt.date.today().isoformat()
    return f"{slug or 'client'}-snapshot-{today}.pdf"


def _read(fn, *a, **kw):
    """Every source in its own try/except -- one raising must cost only its
    own section of the page, never the download."""
    try:
        return fn(*a, **kw), ""
    except Exception as exc:                              # noqa: BLE001
        return {}, f"{type(exc).__name__}"


def _next_action_section(d: Doc, na: dict):
    if not na or not na.get("measured") or not na.get("text"):
        d.banner("Not measured: the next-action line could not be read." if na and na.get("measured") is False
                 else "Nothing urgent on file for this client.", color=SOFT)
        return
    state = na.get("state") or "ok"
    d.banner(na.get("text") or "", color=_STATE_BG.get(state, SOFT),
             text_color=_STATE_COLOR.get(state, MUTED))


def _health_section(d: Doc, health: dict, unread: list):
    pills = (health or {}).get("pills") or []
    flagged = [p for p in pills if p.get("state") in ("bad", "warn")]
    d.text("What needs attention", size=12, bold=True)
    if not flagged:
        d.text("Nothing on the health strip is flagged bad or warn.", size=9.5, color=MUTED)
    else:
        for p in flagged:
            color = _STATE_COLOR.get(p.get("state"), MUTED)
            d.text(f"{clean(p.get('label') or '')}: {clean(p.get('value') or '')}", size=9.5, color=color)
            if p.get("detail"):
                d.text(clean(p["detail"]), size=8.5, color=MUTED, x=58, width=None)
    for u in (health or {}).get("unread") or []:
        unread.append(u)
    d.space(6)


def _money_section(d: Doc, money: dict, unread: list):
    b = (money or {}).get("billing") or {}
    o = (money or {}).get("owed") or {}
    d.text("Account value", size=12, bold=True)
    billed = "Not measured" if b.get("measured") is False else f"${float(b.get('monthly') or 0):,.0f}/mo"
    if o.get("state") == "connected":
        owed = f"${float(o.get('balance') or 0):,.0f}"
        owed_color = BAD if (o.get("balance") or 0) > 0 else GOOD
    elif o.get("state") == "not_connected":
        owed = "Not connected"
        owed_color = MUTED
    else:
        owed = "Not measured"
        owed_color = MUTED
    tiles = [
        {"label": "Billed monthly", "display": billed,
         "color": MUTED if b.get("measured") is False else None},
        {"label": "Outstanding balance", "display": owed, "color": owed_color},
    ]
    if o.get("state") == "connected" and (o.get("overdue_count") or 0) > 0:
        tiles.append({"label": "Overdue invoices", "display": str(o["overdue_count"]), "color": BAD})
    d.tiles(tiles)
    if b.get("measured") is False and b.get("error"):
        unread.append(b["error"])
    if o.get("state") != "connected" and o.get("error"):
        unread.append(o["error"])
    if o.get("state") == "connected" and (o.get("unmatched") or []):
        unread.append("No QuickBooks customer found for " + ", ".join(o["unmatched"]) + ".")


def _upcoming_section(d: Doc, upcoming: dict, unread: list):
    items = (upcoming or {}).get("items") or []
    d.text("Coming up", size=12, bold=True)
    if not items:
        horizon = (upcoming or {}).get("horizon_days") or 120
        d.text(f"Nothing ends or renews in the next {horizon} days.", size=9.5, color=MUTED)
    else:
        rows = []
        for i in items[:8]:
            days = i.get("days")
            when = ("date not on file" if days is None
                    else ("today" if days == 0
                          else (f"{abs(days)}d ago" if days < 0 else f"in {days}d")))
            rows.append([when, clean(i.get("label") or ""), clean(i.get("what") or "")])
        d.table([("When", 90, False), ("What", 150, False), ("Detail", 230, False)], rows)
        if len(items) > 8:
            d.text(f"+{len(items) - 8} more on the record.", size=8.5, color=MUTED)
    for u in (upcoming or {}).get("unread") or []:
        unread.append(u)


def build(name: str, url: str = "", today: "_dt.date | None" = None) -> bytes:
    """Never raises -- a source that could not answer costs its own section
    and is named in the footnote, never the whole download."""
    name = str(name or "").strip()
    today = today or _dt.date.today()

    from . import next_action, record_health, client_money, client_upcoming

    na, na_err = _read(next_action.for_client, name, url)
    health, health_err = _read(record_health.client360, name, today=today)
    money, money_err = _read(client_money.for_client, name, url)
    upcoming, upcoming_err = _read(client_upcoming.for_client, name, url, today)

    d = Doc(f"{name} -- Account snapshot")
    d.text(name or "Client", size=20, bold=True)
    d.text(f"Account snapshot · {today.strftime('%B %-d, %Y')}", size=10, color=MUTED)
    d.space(6)

    unread: list[str] = [e for e in (na_err, health_err, money_err, upcoming_err) if e]

    _next_action_section(d, na)
    d.space(4)
    _health_section(d, health, unread)
    d.rule()
    _money_section(d, money, unread)
    d.space(6)
    _upcoming_section(d, upcoming, unread)

    if unread:
        d.space(10)
        d.text("Not measured: " + "; ".join(clean(u) for u in unread)
               + ". Anything those would have raised is missing from this page, not absent from it.",
               size=8, color=MUTED)

    return d.bytes()
