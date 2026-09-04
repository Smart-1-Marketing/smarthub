"""What the grader keeps, which is deliberately not much.

Two tables and no credential in either. The whole point of this tool is that a
stranger grants read-only access for the length of one request; anything
persisted about that grant would outlive the reason they gave it.

* ``GraderSession`` is the handshake — a state token, the lead we already
  captured, and where they came from. Short-lived by design and expired rather
  than reused.
* ``GraderResult`` is the score and the findings, at an unguessable token, so
  the prospect can come back to their report and a rep can open the same page.

Neither carries an access token, a refresh token or an authorization code, and
``test_ads_grader.py`` asserts that from the model definitions rather than
trusting the sentence.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, select
from sqlalchemy.orm import declarative_base

from hub.extensions import create_all_metadata, session_factory, shared_engine

log = logging.getLogger(__name__)

engine = shared_engine()
SessionLocal = session_factory()
Base = declarative_base()

# A handshake nobody completed is not a handshake to complete tomorrow.
STATE_TTL_MINUTES = 30


def now():
    return datetime.now(timezone.utc)


class GraderSession(Base):
    """One in-flight OAuth handshake. No credential is ever written here."""
    __tablename__ = "ads_grader_sessions"

    state = Column(String(64), primary_key=True)
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    lead_id = Column(String(40), default="")
    name = Column(String(200), default="")
    email = Column(String(200), default="")
    phone = Column(String(60), default="")
    company = Column(String(300), default="")
    website = Column(String(400), default="")
    used = Column(Integer, default=0)

    def as_dict(self) -> dict:
        return {"state": self.state, "lead_id": self.lead_id or "",
                "name": self.name or "", "email": self.email or "",
                "phone": self.phone or "", "company": self.company or "",
                "website": self.website or "",
                "created_at": self.created_at.isoformat() if self.created_at else None,
                "used": bool(self.used)}


class GraderResult(Base):
    """One graded account, at an unguessable token."""
    __tablename__ = "ads_grader_results"

    token = Column(String(64), primary_key=True)
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    lead_id = Column(String(40), default="", index=True)
    company = Column(String(300), default="")
    website = Column(String(400), default="")
    customer_id = Column(String(20), default="")
    account_name = Column(String(300), default="")
    score = Column(Integer, nullable=True)
    grade = Column(String(4), default="")
    result_json = Column(Text, default="{}")
    revoked = Column(Integer, default=0)

    @property
    def result(self) -> dict:
        return json.loads(self.result_json or "{}")

    def as_dict(self, *, with_result: bool = False) -> dict:
        row = {"token": self.token, "lead_id": self.lead_id or "",
               "company": self.company or "", "website": self.website or "",
               "customer_id": self.customer_id or "",
               "account_name": self.account_name or "",
               "score": self.score, "grade": self.grade or "",
               "created_at": self.created_at.isoformat() if self.created_at else None,
               "revoked": bool(self.revoked)}
        if with_result:
            row["result"] = self.result
        return row


DB_BOOT_ERROR = create_all_metadata(Base.metadata)
if DB_BOOT_ERROR:
    log.error("ads_grader: table creation reported: %s", DB_BOOT_ERROR)


def start_session(*, lead_id="", name="", email="", phone="", company="",
                  website="") -> str:
    state = secrets.token_urlsafe(24)
    with SessionLocal() as s:
        s.add(GraderSession(state=state, lead_id=str(lead_id or "")[:40],
                            name=str(name or "")[:200], email=str(email or "")[:200],
                            phone=str(phone or "")[:60], company=str(company or "")[:300],
                            website=str(website or "")[:400]))
        s.commit()
    return state


def take_session(state) -> dict | None:
    """Read a handshake once and mark it used.

    Once, because a state token that can be replayed is one somebody can
    replay -- and expired rather than reused, since a handshake nobody
    completed in half an hour is not one to complete tomorrow.
    """
    with SessionLocal() as s:
        row = s.get(GraderSession, str(state or ""))
        if not row or row.used:
            return None
        created = row.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created and now() - created > timedelta(minutes=STATE_TTL_MINUTES):
            return None
        row.used = 1
        s.commit()
        return row.as_dict()


def save_result(*, lead_id="", company="", website="", customer_id="",
                account_name="", result=None) -> dict:
    result = result or {}
    token = secrets.token_urlsafe(24)
    with SessionLocal() as s:
        s.add(GraderResult(
            token=token, lead_id=str(lead_id or "")[:40],
            company=str(company or "")[:300], website=str(website or "")[:400],
            customer_id=str(customer_id or "")[:20],
            account_name=str(account_name or "")[:300],
            score=result.get("score"), grade=str(result.get("grade") or "")[:4],
            result_json=json.dumps(result, default=str)))
        s.commit()
    return get_result(token, with_result=True)


def get_result(token, *, with_result=False) -> dict | None:
    with SessionLocal() as s:
        row = s.scalar(select(GraderResult).where(GraderResult.token == str(token or "")))
        return row.as_dict(with_result=with_result) if row else None

