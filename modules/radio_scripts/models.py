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
