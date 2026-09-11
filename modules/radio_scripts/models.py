"""One table: a set of radio/audio scripts written for one brief.

Prefixed ``rs_`` for the reason modules/commercial_builder/models.py gives
about ``cb_``: this shares a database with twenty other modules and a table
called ``sets`` is a collision waiting for whichever module adds one next.

A row is the unit a rep works with -- three concepts, each at :60/:30/:15,
generated from one brief in one sitting. The brief and the concepts are kept
as JSON blobs rather than normalised out into their own tables: nothing here
is queried by an individual field (there is no report keyed on "which scripts
mention this offer"), and a normalised shape would need a migration the day a
new concept field is added, where a blob does not.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .db import db


class RadioScriptSet(db.Model):
    """One generation: a brief in, three concepts out."""

    __tablename__ = "rs_script_sets"

    id = db.Column(db.Integer, primary_key=True)

    # Who this was written for, by NAME rather than any id of ours -- the
    # same rule modules/video_tools/models.py and the Commercial Builder's
    # own client join are written to. Blank means nobody said.
    client_name = db.Column(db.String(200), default="")

    # A free-form reference into whatever captured the lead this was written
    # for (the Stadium-to-Screen flow, eventually) -- set by a caller that
    # has one, read by nothing here yet. Present now rather than added later
    # because create_all() never adds a column to an existing table, and this
    # is the one column a future lead-triggered job needs to find its own
    # rows again.
    lead_id = db.Column(db.String(80), default="")

    # The brief as it was asked -- market, team, package, local shows, the
    # funnel figures, the offer if any. Kept verbatim so a concept can be
    # regenerated later against the same facts rather than whatever a rep
    # might retype.
    brief_json = db.Column(db.Text, default="{}")

    # The three concepts, each carrying its :60/:30/:15 scripts, talent
    # direction, SFX/bed notes, a tag block and an empty legal-line slot.
    concepts_json = db.Column(db.Text, default="[]")

    # Flags that apply to the set as a whole -- today, only whether the
    # market/team/local-show coverage rule was satisfied. Per-script flags
    # (word budget, price, superlative, ...) live inside concepts_json
    # because they are about one script, not the set.
    flags_json = db.Column(db.Text, default="[]")

    actor = db.Column(db.String(120), default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def brief(self) -> dict:
        try:
            return json.loads(self.brief_json or "{}") or {}
        except (TypeError, ValueError):
            return {}

    def concepts(self) -> list:
        try:
            return json.loads(self.concepts_json or "[]") or []
        except (TypeError, ValueError):
            return []

    def flags(self) -> list:
        try:
            return json.loads(self.flags_json or "[]") or []
        except (TypeError, ValueError):
            return []

    def to_dict(self, *, full: bool = True) -> dict:
        row = {
            "id": self.id,
            "client_name": self.client_name or "",
            "lead_id": self.lead_id or "",
            "market": self.brief().get("market", ""),
            "package": self.brief().get("package", ""),
            "actor": self.actor or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "flags": self.flags(),
        }
        if full:
            row["brief"] = self.brief()
            row["concepts"] = self.concepts()
        return row


class RadioReviewShare(db.Model):
    """One client-facing review link for one script set, and what came back.

    Its own table rather than columns on `rs_script_sets`, for the reason
    `modules/commercial_builder/models.py`'s `ReviewShare` gives at length:
    `create_all()` creates a missing TABLE and never adds a column to an
    existing one, so a `review_token` column here would exist on every local
    SQLite run and be silently absent on the live Postgres — every test
    green, every read of it None.

    It also keeps what a **client** wrote out of the row a rep is editing at
    the same moment: the two are written by different people through
    different doors, and one of those people has no Hub login at all.

    A link is per set and per round. Sending a new round issues a NEW token
    rather than reopening this one — a link that has been answered is the
    record of that answer, and handing the same URL out again would
    overwrite the first round's decision with no trace there had been one.
    """
    __tablename__ = "rs_review_shares"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, index=True, nullable=False)
    set_id = db.Column(db.Integer, db.ForeignKey("rs_script_sets.id"), nullable=False)
    round_no = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_by = db.Column(db.String(200), default="")
    revoked = db.Column(db.Boolean, default=False)

    # A note the rep writes to the client when sending it — "here's the
    # first pass, the legal line is still a placeholder". Optional, shown
    # above the concepts.
    message = db.Column(db.Text, default="")

    # How many times the page has been opened. "Sent and ignored" and
    # "opened three times and still not answered" are different situations.
    opened_count = db.Column(db.Integer, default=0)
    last_opened_at = db.Column(db.DateTime, nullable=True)

    decisions = db.relationship("RadioReviewDecision", backref="share", lazy="dynamic",
                                cascade="all, delete-orphan")
    comments = db.relationship("RadioReviewComment", backref="share", lazy="dynamic",
                               cascade="all, delete-orphan")

    def to_dict(self, include_children=True):
        out = {
            "id": self.id, "token": self.token, "set_id": self.set_id,
            "round": self.round_no or 1,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by or "",
            "revoked": bool(self.revoked),
            "message": self.message or "",
            "opened_count": self.opened_count or 0,
            "last_opened_at": (self.last_opened_at.isoformat()
                               if self.last_opened_at else None),
        }
        if include_children:
            out["decisions"] = [d.to_dict() for d in self.decisions.all()]
            out["comments"] = [c.to_dict() for c in self.comments.all()]
        return out


class RadioReviewDecision(db.Model):
    """One person's answer on one review link.

    A row per reviewer rather than one answer on the share, because a link
    gets forwarded and two people at the client answer it — collapsing that
    to one column means the second answer overwrites the first, and the case
    that matters is a colleague's "sounds good" overwriting the first
    reviewer's refusal, after which the wrong script goes to production.

    `review_spec.verdict()` resolves the rows into the one answer a screen
    shows.
    """
    __tablename__ = "rs_review_decisions"

    id = db.Column(db.Integer, primary_key=True)
    share_id = db.Column(db.Integer, db.ForeignKey("rs_review_shares.id"), nullable=False)
    outcome = db.Column(db.String(40), default="")
    reviewer_name = db.Column(db.String(200), default="")
    reviewer_email = db.Column(db.String(200), default="")
    note = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id, "outcome": self.outcome or "",
            "reviewer_name": self.reviewer_name or "",
            "reviewer_email": self.reviewer_email or "",
            "note": self.note or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RadioReviewComment(db.Model):
    """A note the client left, optionally against one script in the set.

    `concept_index` / `length_key` are what a video proof's timecode is for
    a video — "the :15 is too pushy" is a different piece of work from a
    general note about the set, and only the first can be actioned without
    guessing which of the nine scripts it means. Both nullable on purpose: a
    comment about the whole set is a real thing to leave, and forcing a
    concept/length onto it would file a general note against the wrong one.
    """
    __tablename__ = "rs_review_comments"

    id = db.Column(db.Integer, primary_key=True)
    share_id = db.Column(db.Integer, db.ForeignKey("rs_review_shares.id"), nullable=False)
    text = db.Column(db.Text, default="")
    reviewer_name = db.Column(db.String(200), default="")
    reviewer_email = db.Column(db.String(200), default="")
    concept_index = db.Column(db.Integer, nullable=True)
    length_key = db.Column(db.String(4), default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        from .review_spec import concept_label, length_label
        return {
            "id": self.id, "text": self.text or "",
            "reviewer_name": self.reviewer_name or "",
            "reviewer_email": self.reviewer_email or "",
            "concept_index": self.concept_index,
            "concept_label": concept_label(self.concept_index),
            "length_key": self.length_key or "",
            "length_label": length_label(self.length_key),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
