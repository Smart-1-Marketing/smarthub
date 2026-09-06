"""Persistence for the client review link — a client-facing share token, its
rounds, and whatever came back on it.

The standalone SQLAlchemy pattern ``modules/ads_builder/store.py`` uses
(shared engine, its own declarative ``Base``), because Image Creator is a
plain dispatcher-mounted Flask app rather than a Flask-SQLAlchemy one — the
shape ``modules/commercial_builder/models.py`` uses is not available here.

Three tables rather than columns on ``ic_projects`` (which itself lives in
``hub.jsonstore``, not this database) or one table with one outcome column:
``create_all()`` creates a missing TABLE and never adds a column to an
existing one, and a link gets forwarded — more than one person can answer
it, so an answer is a *row*, the way ``modules/commercial_builder/models.py``
keeps ``ReviewDecision`` and ``ReviewComment`` apart from ``ReviewShare``.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import declarative_base, relationship

from hub.extensions import create_all_metadata, session_factory, shared_engine

log = logging.getLogger(__name__)

engine = shared_engine()
SessionLocal = session_factory()
Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class Share(Base):
    """One client-facing review link for one project, and its rounds.

    A new token every time a round is sent, never a reopened one: a link
    that has been answered is the record of that answer, and reusing the
    URL for round two would overwrite round one's decision with no trace
    there had been one. The previous round is revoked when a new one issues.
    """
    __tablename__ = "ic_shares"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(64), unique=True, index=True, nullable=False)
    project_id = Column(String(40), index=True, nullable=False)
    round_no = Column(Integer, default=1)
    created_at = Column(DateTime(timezone=True), default=now)
    created_by = Column(String(200), default="")
    revoked = Column(Integer, default=0)

    # A note the rep writes to the client when sending it.
    message = Column(Text, default="")

    # What the client is being shown. A list of {label, url, width, height} —
    # today that is one entry, the project's current export; once Magic
    # Resize's per-size output can be handed in, several. Kept as data on
    # the share rather than re-derived from the project at read time, so a
    # round's link keeps showing what was actually sent even if the project
    # is edited afterward.
    variants_json = Column(Text, default="[]")

    opened_count = Column(Integer, default=0)
    last_opened_at = Column(DateTime(timezone=True), nullable=True)

    @property
    def variants(self) -> list:
        try:
            return json.loads(self.variants_json or "[]")
        except (TypeError, ValueError):
            return []

    def to_dict(self, *, with_children: bool = True) -> dict:
        out = {
            "id": self.id, "token": self.token, "project_id": self.project_id,
            "round": self.round_no or 1,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by or "",
            "revoked": bool(self.revoked),
            "message": self.message or "",
            "variants": self.variants,
            "opened_count": self.opened_count or 0,
            "last_opened_at": self.last_opened_at.isoformat() if self.last_opened_at else None,
        }
        if with_children:
            with SessionLocal() as s:
                decisions = s.execute(select(Decision).where(
                    Decision.share_id == self.id).order_by(Decision.id)).scalars().all()
                comments = s.execute(select(Comment).where(
                    Comment.share_id == self.id).order_by(Comment.id)).scalars().all()
                out["decisions"] = [d.to_dict() for d in decisions]
                out["comments"] = [c.to_dict() for c in comments]
        return out


class Decision(Base):
    """One reviewer's answer on one round. A row per reviewer, not one
    column on ``Share``, so a link forwarded to two people at the client
    keeps both answers rather than the second overwriting the first."""
    __tablename__ = "ic_share_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    share_id = Column(Integer, ForeignKey("ic_shares.id"), nullable=False, index=True)
    outcome = Column(String(40), default="")
    reviewer_name = Column(String(200), default="")
    reviewer_email = Column(String(200), default="")
    note = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=now)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "outcome": self.outcome or "",
            "reviewer_name": self.reviewer_name or "",
            "reviewer_email": self.reviewer_email or "",
            "note": self.note or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Comment(Base):
    """A note a client left. Does not decide anything on its own — kept
    apart from a Decision, or the first note left over ten minutes would
    read as the answer before the client had actually pressed a button."""
    __tablename__ = "ic_share_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    share_id = Column(Integer, ForeignKey("ic_shares.id"), nullable=False, index=True)
    text = Column(Text, default="")
    reviewer_name = Column(String(200), default="")
    reviewer_email = Column(String(200), default="")
    created_at = Column(DateTime(timezone=True), default=now)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "text": self.text or "",
            "reviewer_name": self.reviewer_name or "",
            "reviewer_email": self.reviewer_email or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


DB_BOOT_ERROR = create_all_metadata(Base.metadata)


# ------------------------------------------------------------------- helpers
def create_share(project_id: str, *, created_by: str = "", message: str = "",
                 variants: list | None = None) -> dict:
    """Issue a new round. Revokes any previous live round on this project —
    see the ``Share`` docstring for why."""
    with SessionLocal() as s:
        previous = s.execute(select(Share).where(
            Share.project_id == project_id)).scalars().all()
        round_no = len(previous) + 1
        for old in previous:
            old.revoked = 1
        share = Share(token=secrets.token_urlsafe(24), project_id=project_id,
                      round_no=round_no, created_by=created_by[:200],
                      message=str(message or "").strip()[:2000],
                      variants_json=json.dumps(list(variants or [])))
        s.add(share)
        s.commit()
        s.refresh(share)
        return share.to_dict()


def list_shares(project_id: str) -> list[dict]:
    with SessionLocal() as s:
        rows = s.execute(select(Share).where(Share.project_id == project_id)
                         .order_by(Share.round_no.desc(), Share.id.desc())).scalars().all()
        return [r.to_dict() for r in rows]


def get_share(token: str) -> dict | None:
    with SessionLocal() as s:
        row = s.execute(select(Share).where(Share.token == str(token or ""))).scalar_one_or_none()
        return row.to_dict() if row else None


def live_share_row(session, token: str) -> Share | None:
    row = session.execute(select(Share).where(
        Share.token == str(token or ""))).scalar_one_or_none()
    if not row or row.revoked:
        return None
    return row


def note_opened(token: str) -> None:
    with SessionLocal() as s:
        row = live_share_row(s, token)
        if row:
            row.opened_count = (row.opened_count or 0) + 1
            row.last_opened_at = now()
            s.commit()


def revoke_share(project_id: str, share_id: int) -> bool:
    with SessionLocal() as s:
        row = s.execute(select(Share).where(
            Share.id == share_id, Share.project_id == project_id)).scalar_one_or_none()
        if not row:
            return False
        row.revoked = 1
        s.commit()
        return True


def add_comment(token: str, text: str, name: str, email: str) -> dict | None:
    with SessionLocal() as s:
        row = live_share_row(s, token)
        if not row:
            return None
        comment = Comment(share_id=row.id, text=str(text or "").strip()[:2000],
                          reviewer_name=str(name or "").strip()[:200],
                          reviewer_email=str(email or "").strip()[:200])
        s.add(comment)
        s.commit()
        return row.to_dict()


def record_decision(token: str, outcome: str, name: str, email: str, note: str = "") -> dict | None:
    """Answering again REPLACES that person's own previous answer (matched
    on email) and touches nobody else's — a reviewer who picked the wrong
    button can correct it without correcting a colleague's."""
    with SessionLocal() as s:
        row = live_share_row(s, token)
        if not row:
            return None
        email = str(email or "").strip()[:200]
        existing = s.execute(select(Decision).where(
            Decision.share_id == row.id,
            Decision.reviewer_email == email)).scalar_one_or_none()
        if existing:
            existing.outcome = str(outcome or "")[:40]
            existing.reviewer_name = str(name or "").strip()[:200]
            existing.note = str(note or "").strip()[:2000]
            existing.created_at = now()
        else:
            s.add(Decision(share_id=row.id, outcome=str(outcome or "")[:40],
                           reviewer_name=str(name or "").strip()[:200],
                           reviewer_email=email, note=str(note or "").strip()[:2000]))
        s.commit()
        return row.to_dict()


def reviews_waiting() -> list[dict]:
    """Every live round, across every project, with its verdict resolved —
    what the dashboard-style "who is waiting on whom" reading is built from.
    Never raises; a caller that cannot read this database gets an empty list
    and decides for itself what "not measured" means."""
    from . import review_spec
    with SessionLocal() as s:
        rows = s.execute(select(Share).where(Share.revoked == 0)
                         .order_by(Share.id.desc())).scalars().all()
        out = []
        for row in rows:
            data = row.to_dict()
            out.append({
                "share_id": row.id, "project_id": row.project_id,
                "round_no": row.round_no or 1,
                "sent_at": data["created_at"], "sent_by": data["created_by"],
                "answered": review_spec.verdict(data["decisions"])["answered"],
                "comments": len(data["comments"]),
                "verdict": review_spec.verdict(data["decisions"]),
            })
        return out
