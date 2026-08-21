"""Lead storage for the calculators.

Design note: the lead is written to the database *before* the webhook fires, and
a webhook failure never loses it. The Smart 1 Suite audit found nine of ten apps
using a fire-and-forget POST as their only persistence, which silently binned
every lead whenever the webhook URL was unset or GoHighLevel returned a 500.
Rows here carry a delivery status so a failed send can be retried or exported.
"""

import json
import os
import secrets
import threading
from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, Float, Integer, String, Text,
                        create_engine, select)
from sqlalchemy.orm import declarative_base, sessionmaker
from hub.webargs import clamp_int

# When merging into Smart 1 Hub, replace the standalone engine below with the
# Hub's shared session:  from hub.extensions import db
# and swap Base -> db.Model, _session() -> db.session. Nothing else changes.
Base = declarative_base()

_engine = None
_Session = None
_lock = threading.Lock()
DB_BOOT_ERROR = None


class CalculatorLead(Base):
    __tablename__ = "calculator_leads"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    slug = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False)
    unlocked_at = Column(DateTime)

    # Contact — null until the gate is passed. An anonymous row is still a
    # partial lead worth keeping: it tells you what was priced and where from.
    name = Column(String(200))
    email = Column(String(320))
    company = Column(String(200))
    phone = Column(String(64))

    source = Column(String(200))          # page the embed was on
    ip = Column(String(64))
    inputs_json = Column(Text)
    result_json = Column(Text)
    lead_value = Column(Float)

    webhook_status = Column(String(32), default="pending")
    webhook_detail = Column(String(500))

    def contact(self):
        return {
            "name": self.name or "",
            "email": self.email or "",
            "company": self.company or "",
            "phone": self.phone or "",
        }

    def as_row(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "created_at": _iso(self.created_at),
            "unlocked_at": _iso(self.unlocked_at),
            "name": self.name or "",
            "email": self.email or "",
            "company": self.company or "",
            "phone": self.phone or "",
            "source": self.source or "",
            "lead_value": self.lead_value or 0,
            "webhook_status": self.webhook_status or "",
            "inputs": json.loads(self.inputs_json or "{}"),
        }


def _iso(dt):
    """Serialise with an explicit UTC offset.

    Naive timestamps were being parsed by browsers as local time in the Scans
    module, which displayed scans hours in the future. Always send the offset.
    """
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def init(app):
    """Create the engine and table. Boot failures are captured, not raised.

    A database slow to wake used to take a whole Hub module offline for the life
    of the worker. Record the error and surface it per request instead.
    """
    global _engine, _Session, DB_BOOT_ERROR
    url = (app.config.get("CALCULATORS_DATABASE_URL")
           or os.environ.get("DATABASE_URL")
           or "sqlite:///" + os.path.join(app.config.get("CALCULATORS_DATA_DIR", "data"),
                                          "calculators.db"))
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    try:
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///"):]
            if path:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        _engine = create_engine(url, pool_pre_ping=True, future=True)
        _Session = sessionmaker(bind=_engine, future=True)
        Base.metadata.create_all(_engine)
        DB_BOOT_ERROR = None
    except Exception as exc:  # pragma: no cover - environment dependent
        DB_BOOT_ERROR = "{}: {}".format(type(exc).__name__, exc)
    return DB_BOOT_ERROR


def _session():
    if _Session is None:
        raise RuntimeError(DB_BOOT_ERROR or "Calculator storage is not initialised.")
    return _Session()


def new_token():
    """Unguessable. Never {slug}-{timestamp} — that pattern was enumerable."""
    return secrets.token_urlsafe(32)


def save_estimate(slug, inputs, result, ip, source):
    token = new_token()
    with _lock, _session() as s:
        row = CalculatorLead(
            token=token, slug=slug, created_at=_now(),
            ip=ip, source=(source or "")[:200],
            inputs_json=json.dumps(inputs)[:20000],
            result_json=json.dumps(result)[:100000],
            lead_value=float(result.get("lead_value") or 0),
            webhook_status="not_sent",
        )
        s.add(row)
        s.commit()
    return token


def load(token):
    if not token:
        return None
    with _session() as s:
        return s.scalars(select(CalculatorLead).where(CalculatorLead.token == token)).first()


def save_contact(token, contact):
    """Write the contact to the row. Returns the refreshed row, or None."""
    with _lock, _session() as s:
        row = s.scalars(select(CalculatorLead).where(CalculatorLead.token == token)).first()
        if not row:
            return None
        row.name = contact["name"][:200]
        row.email = contact["email"][:320]
        row.company = contact["company"][:200]
        row.phone = contact["phone"][:64]
        row.unlocked_at = _now()
        row.webhook_status = "pending"
        s.commit()
        s.refresh(row)
        s.expunge(row)
        return row


def set_webhook_status(token, status, detail=""):
    with _lock, _session() as s:
        row = s.scalars(select(CalculatorLead).where(CalculatorLead.token == token)).first()
        if row:
            row.webhook_status = status
            row.webhook_detail = (detail or "")[:500]
            s.commit()


def leads(slug=None, limit=200, only_unlocked=True):
    limit = clamp_int(limit, 200, 1, 1000)
    with _session() as s:                          # was a 500 on Postgres
        stmt = select(CalculatorLead).order_by(CalculatorLead.created_at.desc())
        if slug:
            stmt = stmt.where(CalculatorLead.slug == slug)
        if only_unlocked:
            stmt = stmt.where(CalculatorLead.unlocked_at.isnot(None))
        return [r.as_row() for r in s.scalars(stmt.limit(limit)).all()]


def counts():
    """Per-calculator totals for the Hub dashboard card."""
    out = {}
    with _session() as s:
        for row in s.scalars(select(CalculatorLead)).all():
            bucket = out.setdefault(row.slug, {"estimates": 0, "leads": 0})
            bucket["estimates"] += 1
            if row.unlocked_at:
                bucket["leads"] += 1
    return out


# --- Webhook -----------------------------------------------------------------

def webhook_url(app, slug):
    """Per-calculator override wins, then the shared URL."""
    per = os.environ.get("CALC_WEBHOOK_" + slug.upper().replace("-", "_"))
    return (per or app.config.get("CALCULATORS_LEAD_WEBHOOK_URL")
            or os.environ.get("CALCULATORS_LEAD_WEBHOOK_URL") or "").strip()


def send_webhook(app, row, calc_title):
    """Send the lead to Smart 1 Suite via the Hub's shared lead panel.

    Each calculator used to need its own webhook URL — one environment
    variable per tool, each able to be unset or wrong with no visible symptom,
    because the visitor sees success either way. The panel stores every lead
    first, forwards from one place, and shows what hasn't landed.

    The per-calculator URL still wins if one is set, so an existing override
    keeps working.
    """
    per = os.environ.get("CALC_WEBHOOK_" + row.slug.upper().replace("-", "_"))
    if not per:
        try:
            from hub import leads as hub_leads
            payload = dict(row.contact())
            out = hub_leads.capture_and_deliver(
                source="calculators", page=calc_title or row.slug,
                fields=payload,
                pdf_url=getattr(row, "pdf_url", "") or "",
                client=payload.get("company", ""),
                meta={"calculator": row.slug, "source_page": row.source or "",
                      "lead_value": row.lead_value or 0})
            set_webhook_status(
                row.token, "sent" if out.get("delivered") else "queued",
                out.get("note", ""))
            return bool(out.get("delivered"))
        except Exception:                               # noqa: BLE001
            pass          # fall through to the original path

    url = per or webhook_url(app, row.slug)
    if not url:
        set_webhook_status(row.token, "no_url",
                           "CALCULATORS_LEAD_WEBHOOK_URL is not set — the lead is stored "
                           "but was not sent to Smart 1 Suite.")
        return False

    payload = dict(row.contact())
    payload.update({
        "product": calc_title,
        "calculator": row.slug,
        "source_page": row.source or "",
        "lead_value": row.lead_value or 0,
        "inputs": json.loads(row.inputs_json or "{}"),
        "submitted_at": _iso(row.unlocked_at),
    })

    try:
        import requests
        resp = requests.post(url, json=payload, timeout=10)
        # An HTTP 500 back from GoHighLevel is not an exception. Check the code.
        if 200 <= resp.status_code < 300:
            set_webhook_status(row.token, "sent", "HTTP {}".format(resp.status_code))
            return True
        set_webhook_status(row.token, "failed",
                           "HTTP {} from webhook".format(resp.status_code))
    except Exception as exc:
        set_webhook_status(row.token, "failed", "{}: {}".format(type(exc).__name__, exc))
    return False


def retry_failed(app, titles):
    """Re-send anything that never landed. Called from the admin page."""
    sent = 0
    with _session() as s:
        stmt = select(CalculatorLead).where(
            CalculatorLead.unlocked_at.isnot(None),
            CalculatorLead.webhook_status.in_(("failed", "pending", "no_url")))
        rows = s.scalars(stmt.limit(500)).all()
        for r in rows:
            s.expunge(r)
    for r in rows:
        if send_webhook(app, r, titles.get(r.slug, r.slug)):
            sent += 1
    return sent, len(rows)
