"""State for the public scan widget: placements, and one row per scan run.

Two tables, both on the Scans engine so they live wherever the scans do:

``ScanWidget``  one row per placement. A widget is a slug, a headline and a
                tag; the tag is what tells you six months later which site a
                lead came off. New placements are added from the Hub, not by a
                deploy, because the whole point is to drop these on client
                sites as fast as someone asks.

``ScanRun``     one row per person who ran a scan, keyed by an unguessable
                token. This is the widget's own state — which domain was
                checked, what the free pre-check found, which audit was bought
                for it — not a second lead book.

**The lead itself belongs to ``hub/leads.py``**, the single store every
landing page and calculator already writes to. This module does not own a
webhook, a delivery status or a leads page; v1.42.0 consolidated those for
exactly the reason the Suite audit found, and a second panel here would undo
it. What is kept locally is the state the shared store has no place for: the
token, the pre-check result, and the link to the paid audit.

The scan is deliberately *not* on the critical path. A lead is a lead whether
or not Insites ever answers; the audit attaches to the row when it completes.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import (Column, DateTime, Integer, String, Text, func, or_)
from sqlalchemy.orm import declarative_base

Base = declarative_base()
_LOCK = threading.Lock()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:                  # stored naive-UTC, as elsewhere here
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds")


def new_token() -> str:
    return secrets.token_urlsafe(24)


def slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return s[:48] or "scan"


# ==========================================================================
# Tables
# ==========================================================================

class ScanWidget(Base):
    """One embeddable placement."""

    __tablename__ = "scan_widgets"

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)      # internal, e.g. "Smart 1 home page"
    tag = Column(String(64), nullable=False, index=True)   # what the lead is tagged with
    headline = Column(String(300))
    subhead = Column(String(500))
    button_label = Column(String(80))
    accent = Column(String(16))                     # hex, for the host site's brand
    active = Column(Integer, default=1)
    created_at = Column(DateTime, nullable=False, default=_now)
    created_by = Column(String(120))

    def as_row(self, base_url: str = "") -> dict:
        return {
            "id": self.id, "slug": self.slug, "name": self.name,
            "tag": self.tag, "headline": self.headline or DEFAULTS["headline"],
            "subhead": self.subhead or DEFAULTS["subhead"],
            "button_label": self.button_label or DEFAULTS["button_label"],
            "accent": self.accent or DEFAULTS["accent"],
            "active": bool(self.active),
            "created_at": _iso(self.created_at),
            "created_by": self.created_by or "",
            "page_url": f"{base_url}/scans/w/{self.slug}",
            "embed_url": f"{base_url}/scans/embed/{self.slug}",
        }


DEFAULTS = {
    "headline": "Can AI find your business?",
    "subhead": "ChatGPT, Gemini and Perplexity now answer the questions your "
               "customers used to type into Google. Enter your website and "
               "see what they can — and can't — tell people about you.",
    "button_label": "Check my website",
    "accent": "#009ED2",
}


class ScanRun(Base):
    """One run of the widget: a token, a domain, a pre-check, maybe an audit."""

    __tablename__ = "scan_widget_runs"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    widget_slug = Column(String(64), index=True)
    tag = Column(String(64), index=True)

    created_at = Column(DateTime, nullable=False, default=_now)
    unlocked_at = Column(DateTime)

    # Contact. Null until the gate is passed — an anonymous row is still worth
    # keeping, because it says which domain was checked and from what page.
    name = Column(String(200))
    email = Column(String(320))
    phone = Column(String(64))
    company = Column(String(200))

    domain = Column(String(255), index=True)
    site_url = Column(String(500))
    source = Column(String(300))            # page the widget was embedded on
    ip = Column(String(64))
    user_agent = Column(String(300))

    precheck_score = Column(Integer)
    precheck_json = Column(Text)

    # The paid audit, attached when it lands. Never blocks the lead.
    scan_public_id = Column(String(64), index=True)
    scan_status = Column(String(32), default="pending")
    scan_reused = Column(Integer, default=0)   # 1 = no credit spent

    # Set once the row has been handed to hub.leads. Not a delivery status —
    # that lives with the lead, in the one place that owns delivery.
    lead_id = Column(String(32))

    def contact(self) -> dict:
        return {"name": self.name or "", "email": self.email or "",
                "phone": self.phone or "", "company": self.company or ""}

    def as_row(self) -> dict:
        d = self.contact()
        d.update({
            "id": self.id, "token": self.token,
            "widget_slug": self.widget_slug or "", "tag": self.tag or "",
            "created_at": _iso(self.created_at),
            "unlocked_at": _iso(self.unlocked_at),
            "domain": self.domain or "", "site_url": self.site_url or "",
            "source": self.source or "",
            "precheck_score": self.precheck_score,
            "scan_public_id": self.scan_public_id or "",
            "scan_status": self.scan_status or "",
            "scan_reused": bool(self.scan_reused),
            "lead_id": self.lead_id or "",
            "scan_url": (f"/scans/scan/{self.scan_public_id}"
                         if self.scan_public_id else ""),
            "report_url": (f"/scans/report/{self.scan_public_id}/seo_aeo"
                           if self.scan_public_id and
                           self.scan_status == "complete" else ""),
            "pdf_url": (f"/scans/report/{self.scan_public_id}/seo_aeo.pdf"
                        if self.scan_public_id and
                        self.scan_status == "complete" else ""),
        })
        return d


# ==========================================================================
# Validation
# ==========================================================================

def validate_contact(payload: dict) -> tuple[dict, dict]:
    """(contact, errors). Phone is required and checked for real digits.

    Phone was collected and validated in zero of the eleven Suite apps, which
    is why so many captured leads turned out not to be callable.
    """
    contact = {
        "name": str(payload.get("name") or "").strip()[:200],
        "email": str(payload.get("email") or "").strip()[:320],
        "phone": str(payload.get("phone") or "").strip()[:64],
        "company": str(payload.get("company") or "").strip()[:200],
    }
    errors: dict[str, str] = {}
    if len(contact["name"]) < 2:
        errors["name"] = "Enter your name."
    if not EMAIL_RE.match(contact["email"]):
        errors["email"] = "Enter a working email address."
    if len(re.sub(r"\D", "", contact["phone"])) < 10:
        errors["phone"] = "Enter a phone number with at least 10 digits."
    if len(contact["company"]) < 2:
        errors["company"] = "Enter your business name."
    return contact, errors


