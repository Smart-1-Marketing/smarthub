"""
Storage for the Google Access module.

MERGE NOTE (one line to change when this lands in smarthub):
    replace the `db = SQLAlchemy()` fallback below with
        from hub.extensions import db
    and delete `init_standalone_db`. Everything else works unchanged.
"""

import datetime as _dt
import json
import secrets

from sqlalchemy import (
    Column, DateTime, Integer, String, Text, Boolean, ForeignKey, Index,
)
from sqlalchemy.orm import relationship

try:  # pragma: no cover - exercised only inside the Hub
    from hub.extensions import db  # type: ignore
except Exception:  # standalone fallback
    from flask_sqlalchemy import SQLAlchemy  # type: ignore
    db = SQLAlchemy()


def utcnow():
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def new_token(nbytes=32):
    return secrets.token_urlsafe(nbytes)


# Status vocabulary, shared by requests and individual grants.
PENDING = "pending"        # nothing has happened yet
WAITING = "waiting"        # we did our part, someone else must act
GRANTED = "granted"        # access confirmed
FAILED = "failed"          # tried and could not
SKIPPED = "skipped"        # client declined this one
REVOKED = "revoked"        # was granted, no longer is
EXPIRED = "expired"        # invite link timed out


class AccessRequest(db.Model):
    """One invitation sent to one client."""

    __tablename__ = "ga_access_request"

    id = Column(Integer, primary_key=True)

    # Random, not derived from the client name. A guessable invite link is a
    # stranger writing themselves into a client's Analytics account.
    token = Column(String(64), unique=True, nullable=False, default=new_token)

    hub_client_id = Column(String(64))          # link back to the Hub registry
    client_name = Column(String(255), nullable=False)
    client_email = Column(String(255))
    website = Column(String(512))

    # JSON list of service keys this invite asks for.
    requested_services = Column(Text, nullable=False, default="[]")

    status = Column(String(32), nullable=False, default=PENDING)
    created_by = Column(String(255))
    created_at = Column(DateTime, nullable=False, default=utcnow)
    expires_at = Column(DateTime)
    opened_at = Column(DateTime)
    completed_at = Column(DateTime)
    last_reminder_at = Column(DateTime)

    # Which Google account the client actually consented with. Useful when
    # someone signs in with a personal address that doesn't own the property.
    consent_email = Column(String(255))

    note = Column(Text)

    grants = relationship(
        "AccessGrant",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="AccessGrant.id",
    )

    # -- helpers ----------------------------------------------------------
    @property
    def services(self):
        try:
            return json.loads(self.requested_services or "[]")
        except (ValueError, TypeError):
            return []

    @services.setter
    def services(self, value):
        self.requested_services = json.dumps(list(value or []))

    def expired(self):
        return bool(self.expires_at and utcnow() > self.expires_at)

    def client_key(self):
        """The Hub-wide client key for this invitation.

        `hub_client_id` above is the intended join and it is optional, typed
        by hand into one admin field, and therefore usually empty — which is
        why the Client 360 access card showed nothing for clients who plainly
        had access granted. This is derived from `website` first (a domain is
        exact) and the client name second, so it is populated on every row
        without anybody filling anything in.
        """
        try:
            from hub.client_key import resolve
            return resolve(name=self.client_name or "",
                           url=self.website or "")["key"]
        except Exception:                                 # noqa: BLE001
            return ""

    def grant_for(self, service):
        for g in self.grants:
            if g.service == service:
                return g
        return None

    def rollup(self):
        """Overall status derived from the individual grants."""
        if self.status in (EXPIRED,):
            return EXPIRED
        active = [g for g in self.grants if g.status != SKIPPED]
        if not active:
            return PENDING
        if all(g.status == GRANTED for g in active):
            return GRANTED
        if any(g.status in (GRANTED, WAITING, FAILED) for g in active):
            return WAITING
        return PENDING


class AccessGrant(db.Model):
    """One service within one request."""

    __tablename__ = "ga_access_grant"

    id = Column(Integer, primary_key=True)
    request_id = Column(
        Integer, ForeignKey("ga_access_request.id", ondelete="CASCADE"), nullable=False
    )
    service = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default=PENDING)

    # What we were added to: "properties/123456", "accounts/ABC", customer id.
    resource_id = Column(String(255))
    resource_name = Column(String(255))
    role_granted = Column(String(64))

    # Message shown to staff when something went wrong. Never rendered to the
    # client verbatim -- the QA audit found raw API errors reaching customers.
    error_detail = Column(Text)

    granted_at = Column(DateTime)
    checked_at = Column(DateTime)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    request = relationship("AccessRequest", back_populates="grants")


Index("ix_ga_grant_request_service", AccessGrant.request_id, AccessGrant.service)


class OAuthState(db.Model):
    """Single-use CSRF state for the consent round trip."""

    __tablename__ = "ga_oauth_state"

    id = Column(Integer, primary_key=True)
    state = Column(String(64), unique=True, nullable=False, default=new_token)
    request_id = Column(Integer, ForeignKey("ga_access_request.id", ondelete="CASCADE"))
    services = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    used = Column(Boolean, nullable=False, default=False)

    @property
    def service_list(self):
        try:
            return json.loads(self.services or "[]")
        except (ValueError, TypeError):
            return []


def init_standalone_db(app):
    """Only used outside the Hub. Delete on merge."""
    db.init_app(app)
    with app.app_context():
        db.create_all()
