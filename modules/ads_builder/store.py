"""Persistence for the Smart 1 Ads module.

SQLite locally, Postgres on Render via DATABASE_URL — the same dual-mode
pattern the scans and sales_builder modules use, so this is testable offline
and durable in production. All three now get that from hub/extensions rather
than each building an engine of its own; see the note in modules/scans/app.py.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, select
from sqlalchemy.orm import declarative_base

from hub.extensions import create_all_metadata, session_factory, shared_engine

log = logging.getLogger(__name__)

engine = shared_engine()
SessionLocal = session_factory()
Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class Proposal(Base):
    __tablename__ = "ads_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    public_id = Column(String(40), unique=True, nullable=False, index=True)
    client_name = Column(String(300), default="")
    google_customer_id = Column(String(20), default="")
    status = Column(String(30), default="DRAFT", index=True)
    created_by = Column(String(200), default="")
    created_at = Column(DateTime(timezone=True), default=now)
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now)
    campaign_json = Column(Text, default="{}")
    comments_json = Column(Text, default="[]")
    deployment_json = Column(Text, default="")

    # ------------------------------------------------------------ helpers
    @property
    def campaign(self) -> dict:
        return json.loads(self.campaign_json or "{}")

    @property
    def comments(self) -> list:
        return json.loads(self.comments_json or "[]")

    @property
    def deployment(self) -> dict | None:
        return json.loads(self.deployment_json) if self.deployment_json else None

    def as_dict(self) -> dict:
        campaign = self.campaign
        groups = campaign.get("adGroups") or []
        return {
            "id": self.public_id,
            "client_name": self.client_name,
            "google_customer_id": self.google_customer_id,
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "campaign": campaign,
            "comments": self.comments,
            "deployment": self.deployment,
            "ad_group_count": len(groups),
            "keyword_count": sum(len(g.get("keywords") or []) for g in groups),
            "negative_count": sum(
                len(v or []) for v in (campaign.get("negativeKeywordVault") or {}).values()
            ),
        }


class Setting(Base):
    __tablename__ = "ads_settings"
    key = Column(String(80), primary_key=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now)


class Event(Base):
    """Module-local audit trail. Mirrored into the Hub activity log when the
    Hub's own audit module is importable."""
    __tablename__ = "ads_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    action = Column(String(80), default="", index=True)
    actor = Column(String(200), default="")
    details_json = Column(Text, default="{}")

    def as_dict(self) -> dict:
        return {
            "timestamp": self.created_at.isoformat() if self.created_at else None,
            "action": self.action,
            "actor": self.actor,
            "details": json.loads(self.details_json or "{}"),
        }


# Advisory-locked (two gunicorn workers issue this concurrently on every
# deploy) and reported rather than raised.
DB_BOOT_ERROR = create_all_metadata(Base.metadata)
if DB_BOOT_ERROR:
    log.error("ads_builder: table creation reported: %s", DB_BOOT_ERROR)

STATUSES = ("DRAFT", "IN_REVIEW", "CHANGES_REQUESTED", "APPROVED", "DEPLOYED", "ARCHIVED")
OPEN_STATUSES = ("DRAFT", "IN_REVIEW", "CHANGES_REQUESTED")


# --------------------------------------------------------------- settings
def get_setting(key: str) -> str:
    with SessionLocal() as s:
        row = s.get(Setting, key)
        return row.value if row else ""


def set_setting(key: str, value: str):
    with SessionLocal() as s:
        row = s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))
        s.commit()


# -------------------------------------------------------------- proposals
def new_public_id() -> str:
    return "prop_" + secrets.token_hex(5)


def create_proposal(client_name, campaign, created_by="", google_customer_id="") -> dict:
    with SessionLocal() as s:
        row = Proposal(
            public_id=new_public_id(),
            client_name=client_name or "",
            google_customer_id=google_customer_id or "",
            created_by=created_by or "",
            status="DRAFT",
            campaign_json=json.dumps(campaign),
            comments_json="[]",
        )
        s.add(row)
        s.commit()
        return row.as_dict()


def list_proposals(limit=200) -> list:
    with SessionLocal() as s:
        rows = s.scalars(
            select(Proposal).order_by(Proposal.updated_at.desc()).limit(limit)
        ).all()
        return [r.as_dict() for r in rows]


def get_proposal(public_id) -> dict | None:
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        return row.as_dict() if row else None


def set_status(public_id, status) -> dict | None:
    if status not in STATUSES:
        raise ValueError(f'Invalid status "{status}".')
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return None
        row.status = status
        row.updated_at = now()
        s.commit()
        return row.as_dict()


def add_comment(public_id, author, text) -> dict | None:
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return None
        comments = json.loads(row.comments_json or "[]")
        comments.append({
            "author": author or "Team",
            "text": str(text)[:4000],
            "created_at": now().isoformat(),
        })
        row.comments_json = json.dumps(comments)
        row.updated_at = now()
        s.commit()
        return row.as_dict()


def set_customer_id(public_id, customer_id) -> dict | None:
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return None
        row.google_customer_id = customer_id or ""
        row.updated_at = now()
        s.commit()
        return row.as_dict()


def mark_deployed(public_id, deployment) -> dict | None:
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return None
        row.status = "DEPLOYED"
        row.deployment_json = json.dumps(deployment)
        row.updated_at = now()
        s.commit()
        return row.as_dict()


def delete_proposal(public_id) -> bool:
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return False
        s.delete(row)
        s.commit()
        return True


# ------------------------------------------------------------------ audit
try:  # the Hub's shared activity log, when we are running inside the Hub
    from hub import audit as hub_audit
except Exception:  # noqa: BLE001 — standalone / dev
    hub_audit = None


def log_event(action, actor="System", **details):
    with SessionLocal() as s:
        s.add(Event(action=action, actor=actor or "System",
                    details_json=json.dumps(details, default=str)))
        s.commit()

    if hub_audit is not None:
        for fn_name in ("log", "record", "write"):
            fn = getattr(hub_audit, fn_name, None)
            if callable(fn):
                try:
                    fn(f"ads.{action.lower()}", actor=actor, **details)
                except Exception:  # noqa: BLE001 — never break a request over logging
                    pass
                break


def list_events(limit=250) -> list:
    with SessionLocal() as s:
        rows = s.scalars(
            select(Event).order_by(Event.created_at.desc()).limit(limit)
        ).all()
        return [r.as_dict() for r in rows]
