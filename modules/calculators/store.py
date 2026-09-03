"""Lead storage for the calculators.

Design note: the lead is written to the database *before* anything is sent, and
a delivery failure never loses it. The Smart 1 Suite audit found nine of ten
apps using a fire-and-forget POST as their only persistence, which silently
binned every lead whenever the webhook URL was unset or GoHighLevel returned a
500. Rows here carry a delivery status so a failed send can be retried or
exported.

## Who delivers, and why the page has to say so honestly

Delivery moved to the Hub's lead panel (`hub/leads.py`), which stores every
lead centrally and pushes it to Smart 1 Suite over the Contacts API. The page
here kept reporting on `CALCULATORS_LEAD_WEBHOOK_URL` — a variable that stopped
being the route on the day that moved — so it announced "nothing is reaching
Smart 1 Suite" while every lead was arriving. A banner that cries wolf is worse
than no banner: the next one nobody reads is the real one.

So `delivery_status()` reports what the code actually does, and the page draws
its banners from that. The legacy per-calculator override still works, and is
now named as the hazard it is rather than as the thing to go and set.
"""

import json
import os
import secrets
import threading
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, select
from sqlalchemy.orm import declarative_base
from hub.extensions import create_all_metadata, engine_for, session_factory
from hub.webargs import clamp_int

# The engine comes from hub/extensions, so this shares the Hub's connection
# pool. CALCULATORS_DATABASE_URL still points the module at a database of its
# own if it is ever needed; that gets its own pool, one per database rather
# than one per module.
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
    # Blank unless someone has deliberately split this module off; the Hub
    # database is the default, which is what DATABASE_URL used to give it.
    url = (app.config.get("CALCULATORS_DATABASE_URL") or "").strip() or None
    try:
        if url and url.startswith("sqlite:///"):
            path = url[len("sqlite:///"):]
            if path:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        _engine = engine_for(url)
        _Session = session_factory(url, expire_on_commit=True)
        DB_BOOT_ERROR = create_all_metadata(Base.metadata, url) or None
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

def _override_env(slug):
    return "CALC_WEBHOOK_" + slug.upper().replace("-", "_")


# There used to be a `webhook_url(app, slug)` helper here that nothing
# called: `send_webhook()` re-derived the same environment name inline.
# One reader now, and the shared `CALCULATORS_LEAD_WEBHOOK_URL` is
# deliberately not read for delivery at all -- it was only ever reached when
# the panel raised, and it would then have posted a lead the panel had
# already stored, the double write the panel exists to prevent.


def delivery_status(app, slugs=()):
    """What actually happens to a calculator lead, in the words of the code.

    `route` is the Hub panel's own delivery mode ("api", "none", or
    "unavailable" if hub.leads could not be imported at all). `overrides` are
    the calculators still pointed at a webhook of their own — those bypass the
    panel completely, so they get no contact id, no central record and no
    retry. `legacy_fallback` says the shared URL is still set on this
    deployment. Nothing reads it any more, so it is reported in order to be
    cleared, not because it does anything.
    """
    route, reason = "unavailable", ""
    try:
        from hub import leads as hub_leads
        route = hub_leads.delivery_mode()
        if route != "api":
            reason = hub_leads.route_status().get("note", "")
    except Exception as exc:                        # noqa: BLE001
        reason = f"hub.leads is not importable here ({type(exc).__name__})."
    return {
        "route": route,
        "reason": reason,
        "overrides": sorted(s for s in slugs if os.environ.get(_override_env(s))),
        "legacy_fallback": (app.config.get("CALCULATORS_LEAD_WEBHOOK_URL")
                            or os.environ.get("CALCULATORS_LEAD_WEBHOOK_URL")
                            or "").strip() != "",
        "ok": route == "api",
    }


def send_webhook(app, row, calc_title):
    """Send the lead to Smart 1 Suite via the Hub's shared lead panel.

    Each calculator used to need its own webhook URL — one environment
    variable per tool, each able to be unset or wrong with no visible symptom,
    because the visitor sees success either way. The panel stores every lead
    first, forwards from one place, and shows what hasn't landed.

    The per-calculator URL still wins if one is set, so an existing override
    keeps working. There is nothing beyond it: the shared inbound webhook is
    retired, and posting there after the panel had already stored the lead is
    exactly the second contact the panel exists to prevent.
    """
    per = (os.environ.get(_override_env(row.slug)) or "").strip()
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
        except Exception as exc:                        # noqa: BLE001
            # No second route. The lead is in this module's own table either
            # way — that is what the CSV export is for — and saying so beats
            # posting it somewhere nothing answers.
            set_webhook_status(
                row.token, "failed",
                "The Hub lead panel couldn't take this lead ({}), so it is "
                "stored here only. Check /sales/leads.".format(type(exc).__name__))
            return False

    url = per

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
    """Re-send anything that never landed. Called from the admin page.

    A lead already handed to the Hub panel is **not** re-sent from here.
    `send_webhook()` captures a *new* panel row every time it runs, so a second
    call would file the same person twice and there would be nothing to
    reconcile the two against — the exact failure the panel was built to stop.
    The panel owns those rows, and its own retry goes back down the same route
    and is a no-op for anything that already landed, so this delegates to it
    and reports what came back.
    """
    sent = 0
    with _session() as s:
        stmt = select(CalculatorLead).where(
            CalculatorLead.unlocked_at.isnot(None),
            CalculatorLead.webhook_status.in_(("failed", "pending", "no_url")))
        rows = s.scalars(stmt.limit(500)).all()
        queued = s.scalars(select(CalculatorLead).where(
            CalculatorLead.webhook_status == "queued").limit(500)).all()
        queued_n = len(queued)
        for r in rows:
            s.expunge(r)
    for r in rows:
        if send_webhook(app, r, titles.get(r.slug, r.slug)):
            sent += 1

    panel = {}
    if queued_n:
        try:
            from hub import leads as hub_leads
            panel = hub_leads.retry_undelivered()
        except Exception as exc:                    # noqa: BLE001
            panel = {"note": f"The lead panel's retry didn't run "
                             f"({type(exc).__name__})."}
    return {"sent": sent, "attempted": len(rows), "queued_in_panel": queued_n,
            "panel": panel}
