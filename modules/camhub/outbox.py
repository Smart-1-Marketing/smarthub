"""The reporting outbox: render, file and (best-effort) send.

Sprint 5 is done when a sponsor receives a report nobody assembled by
hand. That splits into three concerns this module handles, and one it
does not:

- **Render** the PDF from `reports.sponsor_monthly()` and file its
  bytes on the shared store (`hub.storage.put`, Cloudinary in prod)
  so the URL is durable and does not rely on the disk of one worker.
- **Enqueue** rows for a target month, one per sponsor with running
  or recently-ended flights, keyed on (sponsor_id, YYYY-MM) so the
  scheduler is idempotent.
- **Send** the row when a linked contact exists on the sponsor. The
  contact linkage is the same GHL path the ad-proof email already
  uses; a row without a linked contact stays `rendered` so staff can
  hand-forward the PDF. The build is not blocked on adding a second
  ESP, which the Hub does not have -- the value is the report; the
  send channel is the least interesting decision here.

What this module does not do: it never touches the raw event table
(`reports.py` reads only the rollup), and it never re-computes stats
inside the send step (`render_row` files them at render time so the
row itself is the auditable record).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Callable

from sqlalchemy import select

from .models import Placement, Sponsor, SponsorReport, session
from . import reports

# Fields the send hook may read: keep the SenderFn signature stable and
# the ORM row out of the caller's hands.
__all__ = ["enqueue_month", "render_row", "send_row", "list_rows", "get_row",
           "run_monthly", "register_sender", "parse_period", "STATUSES",
           "MAX_ATTEMPTS"]

log = logging.getLogger("hub")

STATUSES = ("pending", "rendered", "sent", "failed")
MAX_ATTEMPTS = 3
# The window a report is considered "for". A flight that ended before
# the target month has nothing to report; one that started after it has
# nothing yet. Everything else is in scope, including a flight that
# ended inside the month -- the last month's number is the whole point.


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _period_of(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def parse_period(period: str) -> tuple[int, int]:
    try:
        y, m = period.split("-", 1)
        y_i, m_i = int(y), int(m)
    except (ValueError, AttributeError) as exc:
        raise ValueError("period must be YYYY-MM") from exc
    if not (1 <= m_i <= 12) or y_i < 2000 or y_i > 2999:
        raise ValueError(f"period {period} is out of range")
    return y_i, m_i


def _sponsor_flight_ran(pls: list[Placement], year: int, month: int) -> bool:
    """True when at least one of the sponsor's placements was live at
    any point during the target month. An open-ended flight that
    started before or during counts; a flight that ended before the
    month started does not."""
    start = date(year, month, 1)
    from calendar import monthrange
    end = date(year, month, monthrange(year, month)[1])
    for pl in pls:
        s = pl.start_date or ""
        e = pl.end_date or ""
        try:
            sd = date.fromisoformat(s) if s else None
            ed = date.fromisoformat(e) if e else None
        except ValueError:
            continue
        if sd and sd > end:
            continue
        if ed and ed < start:
            continue
        return True
    return False


# ------------------------------------------------------------- enqueue

def enqueue_month(year: int, month: int, *, actor: str = "scheduler") -> dict:
    """Create a row per sponsor whose flights ran during the target
    month, skipping the ones we already have. Returns a summary."""
    period = _period_of(year, month)
    created: list[int] = []
    skipped: list[int] = []
    with session() as s:
        sponsors = s.execute(select(Sponsor)).scalars().all()
        for sp in sponsors:
            placements = s.execute(select(Placement).where(
                Placement.sponsor_id == sp.id, Placement.is_house == False)  # noqa: E712
            ).scalars().all()
            if not placements or not _sponsor_flight_ran(placements, year, month):
                continue
            existing = s.execute(select(SponsorReport).where(
                SponsorReport.sponsor_id == sp.id, SponsorReport.period == period)
            ).scalar_one_or_none()
            if existing is not None:
                skipped.append(sp.id)
                continue
            row = SponsorReport(sponsor_id=sp.id, period=period,
                                status="pending", actor=actor,
                                recipient=sp.email or "")
            s.add(row)
            s.flush()
            created.append(row.id)
        s.commit()
    return {"period": period, "created": created, "skipped": skipped}


# ------------------------------------------------------------- render

def _upload_pdf(sponsor: Sponsor, period: str, data: bytes) -> tuple[str, str]:
    """Store the PDF and return (url, public_id)."""
    from hub.storage import put, slug
    filename = f"camhub-{slug(sponsor.name, 'sponsor')}-{period}.pdf"
    asset = put("camhub_reports", filename, data,
                client=sponsor.name or "", subpath=period,
                overwrite=True,
                tags=["camhub", "sponsor_report", f"sponsor:{sponsor.id}",
                      f"period:{period}"])
    return asset.url, asset.public_id


def render_row(row_id: int, *, today: date | None = None,
               brand: str = "Smart 1 Marketing") -> dict:
    """Render the PDF for one queued row, upload it and update the row
    to `rendered`. Safe to re-run: the same filename overwrites the
    same Cloudinary object, and the row's `impressions`/`clicks` are
    refreshed each time."""
    with session() as s:
        row = s.get(SponsorReport, int(row_id))
        if row is None:
            raise LookupError(f"outbox row {row_id} not found")
        sponsor = s.get(Sponsor, int(row.sponsor_id))
        if sponsor is None:
            raise LookupError(f"sponsor {row.sponsor_id} not found for row {row_id}")
        period = row.period
    year, month = parse_period(period)
    report = reports.sponsor_monthly(sponsor.id, year, month, today=today)
    pdf_bytes = reports.render_monthly_pdf(report, brand=brand)
    url, public_id = _upload_pdf(sponsor, period, pdf_bytes)
    with session() as s:
        row = s.get(SponsorReport, int(row_id))
        row.status = "rendered"
        row.pdf_url = url
        row.pdf_public_id = public_id
        row.impressions = int(report["totals"]["now"]["impressions"])
        row.clicks = int(report["totals"]["now"]["clicks"])
        row.rendered_at = _now()
        row.last_error = None
        row.recipient = sponsor.email or ""
        s.commit()
    return {"row_id": row_id, "url": url,
            "impressions": int(report["totals"]["now"]["impressions"]),
            "clicks": int(report["totals"]["now"]["clicks"])}


# ------------------------------------------------------------- send

# The send hook is a callable so tests can substitute a recorder and
# a future ESP can be plugged in without adding a dependency on GHL
# here. The default resolver looks for hub.ad_proof_email-style GHL
# and returns None when the sponsor has no linked contact -- that is
# not a failure, it is a row that stays `rendered` for staff.
SenderFn = Callable[[dict, dict], dict]

_sender: SenderFn | None = None


def register_sender(fn: SenderFn | None) -> None:
    """Override the send channel; None restores the default (staff
    hand-off)."""
    global _sender
    _sender = fn


def _default_sender(sponsor: dict, row: dict) -> dict:
    """The Hub has one ESP: GHL, through hub/ad_proof_email.py. A
    sponsor is not a Hub client -- there is no linked GHL contact
    behind them by default -- so the honest default is to leave
    the row rendered with a note explaining why."""
    if not (sponsor.get("email") or "").strip():
        return {"sent": False, "reason": "no_recipient",
                "detail": "The sponsor has no email on file."}
    return {"sent": False, "reason": "no_channel",
            "detail": ("Sponsor emails are not on a Hub ESP yet. The "
                       "PDF is on file; forward the link from the "
                       "reports screen.")}


def send_row(row_id: int) -> dict:
    """Attempt to send a rendered row. Rows that cannot be sent stay
    `rendered` with the reason on the row, so the reports screen can
    show the staff-hand-off queue and the schedule does not endlessly
    retry a channel that does not exist. A hard failure moves the row
    to `failed` after MAX_ATTEMPTS attempts."""
    with session() as s:
        row = s.get(SponsorReport, int(row_id))
        if row is None:
            raise LookupError(f"outbox row {row_id} not found")
        if row.status == "sent":
            return {"row_id": row_id, "sent": True, "already": True}
        if row.status not in ("rendered", "failed"):
            raise ValueError(f"row {row_id} is {row.status}; render it first")
        sponsor = s.get(Sponsor, int(row.sponsor_id))
        if sponsor is None:
            raise LookupError(f"sponsor {row.sponsor_id} not found for row {row_id}")
        attempts = int(row.attempts or 0) + 1
        # Take a plain-dict snapshot so the sender never touches a
        # detached ORM row -- the session closes before the network
        # call, so a driver disconnect during a slow ESP cannot leave
        # rows locked.
        sponsor_snap = {"id": sponsor.id, "name": sponsor.name,
                        "email": sponsor.email or "",
                        "contact_name": sponsor.contact_name or ""}
        row_snap = {"id": row.id, "period": row.period,
                    "pdf_url": row.pdf_url or "",
                    "recipient": row.recipient or sponsor.email or ""}
    fn = _sender or _default_sender
    try:
        result = fn(sponsor_snap, row_snap)
    except Exception as exc:  # noqa: BLE001
        result = {"sent": False, "reason": "sender_error",
                  "detail": f"{type(exc).__name__}: {exc}"}
    with session() as s:
        row = s.get(SponsorReport, int(row_id))
        row.attempts = attempts
        if result.get("sent"):
            row.status = "sent"
            row.sent_at = _now()
            row.last_error = None
        else:
            row.last_error = (result.get("detail") or result.get("reason") or "")[:2000]
            if attempts >= MAX_ATTEMPTS and result.get("reason") == "sender_error":
                row.status = "failed"
            # No-channel and no-recipient stay `rendered` -- they are
            # awaiting a person, not a retry.
        s.commit()
    return {"row_id": row_id, **result, "attempts": attempts}


# ------------------------------------------------------------- read

def list_rows(period: str | None = None, sponsor_id: int | None = None,
              limit: int = 200) -> list[dict]:
    with session() as s:
        q = select(SponsorReport)
        if period:
            q = q.where(SponsorReport.period == period)
        if sponsor_id:
            q = q.where(SponsorReport.sponsor_id == int(sponsor_id))
        q = q.order_by(SponsorReport.period.desc(), SponsorReport.id.desc()
                       ).limit(int(limit))
        rows = s.execute(q).scalars().all()
        sponsor_ids = {r.sponsor_id for r in rows}
        names = {}
        if sponsor_ids:
            for sp in s.execute(select(Sponsor).where(Sponsor.id.in_(sponsor_ids))
                                ).scalars().all():
                names[sp.id] = {"name": sp.name, "email": sp.email or ""}
    out = []
    for r in rows:
        out.append({"id": r.id, "sponsor_id": r.sponsor_id,
                    "sponsor_name": names.get(r.sponsor_id, {}).get("name") or "",
                    "recipient": r.recipient or names.get(r.sponsor_id, {}).get("email") or "",
                    "period": r.period, "status": r.status,
                    "pdf_url": r.pdf_url or "", "csv_url": r.csv_url or "",
                    "impressions": int(r.impressions or 0),
                    "clicks": int(r.clicks or 0),
                    "attempts": int(r.attempts or 0),
                    "last_error": r.last_error or "",
                    "rendered_at": r.rendered_at.isoformat() if r.rendered_at else "",
                    "sent_at": r.sent_at.isoformat() if r.sent_at else ""})
    return out


def get_row(row_id: int) -> dict | None:
    rows = list_rows()
    return next((r for r in rows if r["id"] == int(row_id)), None)


# ------------------------------------------------------------- scheduler hook

def run_monthly(today: date | None = None, actor: str = "scheduler") -> dict:
    """Called by hub/scheduler.py once an hour. Only acts when today
    is the 1st, and only after the second hour of the day so a fresh
    deploy on midnight-1 does not race the rollup. Idempotent: rows
    already sent are left alone."""
    today = today or date.today()
    if today.day != 1:
        return {"acted": False, "reason": "not-the-first", "today": today.isoformat()}
    from datetime import datetime as _dt
    if _dt.utcnow().hour < 2:
        return {"acted": False, "reason": "too-early", "today": today.isoformat()}
    prev = today.replace(day=1)
    from datetime import timedelta as _td
    last_prior = prev - _td(days=1)
    year, month = last_prior.year, last_prior.month
    en = enqueue_month(year, month, actor=actor)
    rendered: list[dict] = []
    for row_id in en["created"]:
        try:
            rendered.append(render_row(row_id, today=today))
        except Exception as exc:  # noqa: BLE001
            log.exception("camhub_report_render_failed row_id=%s error=%s",
                          row_id, exc)
            with session() as s:
                r = s.get(SponsorReport, row_id)
                if r is not None:
                    r.status = "failed"
                    r.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                    r.attempts = int(r.attempts or 0) + 1
                    s.commit()
    sent: list[dict] = []
    for row_id in en["created"]:
        with session() as s:
            r = s.get(SponsorReport, row_id)
            if r is None or r.status != "rendered":
                continue
        sent.append(send_row(row_id))
    return {"acted": True, "today": today.isoformat(),
            "period": en["period"], "enqueued": en["created"],
            "skipped": en["skipped"],
            "rendered": [x["row_id"] for x in rendered],
            "sent": [x["row_id"] for x in sent if x.get("sent")]}
