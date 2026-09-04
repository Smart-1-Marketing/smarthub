"""One table: a video edit that was asked for, and what became of it.

Prefixed `vt_` for the reason modules/commercial_builder/models.py gives about
`cb_`: this shares a database with twenty other modules and a table called
`jobs` is a collision waiting for whichever module adds one next.

Why a table at all, when both tools are a URL:

  * A derived Cloudinary asset is submitted asynchronously and finishes
    minutes later. Something has to hold the request across that gap, and a
    page that holds it in JavaScript loses the job when the tab closes.
  * The plan a person approved and the transformation that was submitted must
    be the same thing, afterwards, on the record. A cut list that exists only
    in the browser cannot be re-read when a client asks why a line is missing.
  * A finished edit is filed in a client's library and appears on their 360
    record. That filing needs somewhere to record that it happened, so a job
    is not filed twice and a job that failed to file is visible as such.
"""
from __future__ import annotations

import json
from datetime import datetime

from .db import db


class VideoJob(db.Model):
    """One requested edit. Rows are append-mostly: status and result move."""

    __tablename__ = "vt_jobs"

    id = db.Column(db.Integer, primary_key=True)

    # "dead_air" or "reframe". Both tools write here so the tool pages can
    # each show their own recent work without a second table whose columns
    # would be the same columns.
    tool = db.Column(db.String(20), nullable=False, index=True)

    # The source, as Cloudinary knows it. Not a URL: a URL carries a version
    # and a transformation and neither is the asset's identity, and re-running
    # a job from a stored URL is how an edit comes to be applied to an edit.
    source_public_id = db.Column(db.String(500), nullable=False)
    source_duration = db.Column(db.Float)
    source_width = db.Column(db.Integer)
    source_height = db.Column(db.Integer)

    # Who it is for, by NAME rather than by any id of ours -- the same rule
    # modules/commercial_builder/services/cloudinary_service._file_in_gallery
    # is written to. Blank means nobody said, and the result is not filed
    # against a client rather than being filed against a guessed one.
    client_name = db.Column(db.String(200), default="")
    actor = db.Column(db.String(120), default="")

    # The options the person chose, and the plan those options produced. Kept
    # apart on purpose: the options are what to ask for again, the plan is
    # what was actually done and is what a disagreement is settled against.
    options_json = db.Column(db.Text)
    plan_json = db.Column(db.Text)

    # The transformation submitted, verbatim. This is the single most useful
    # column on the table when something looks wrong -- it can be pasted onto
    # a delivery URL and watched.
    transformation = db.Column(db.Text)

    status = db.Column(db.String(20), default="pending", index=True)
    error = db.Column(db.Text)

    # Where the finished edit lives. `result_url` is the derived delivery URL
    # (immediate, and tied to the source); `saved_public_id` is set only once
    # the edit has been stored as an asset of its own, which is what makes it
    # survive the source being replaced.
    result_url = db.Column(db.String(1000))
    saved_public_id = db.Column(db.String(500))
    saved_url = db.Column(db.String(1000))

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    finished_at = db.Column(db.DateTime)

    # When the person who started this was told it had finished. Stamped on
    # the row rather than kept in the browser, unlike the birthday popup: a
    # birthday is everybody's and can be re-shown harmlessly, and "your edit
    # is ready" is one person's and has to survive them opening the Hub on a
    # different machine. NULL means still to tell them.
    seen_at = db.Column(db.DateTime)

    # -- JSON columns as dicts ------------------------------------------
    # Text rather than a native JSON column so the module behaves identically
    # on SQLite and Postgres, which is the arrangement models.py in
    # commercial_builder settled on for the same pair of databases.

    @property
    def options(self) -> dict:
        return _loads(self.options_json)

    @options.setter
    def options(self, value) -> None:
        self.options_json = json.dumps(value or {})

    @property
    def plan(self) -> dict:
        return _loads(self.plan_json)

    @plan.setter
    def plan(self, value) -> None:
        self.plan_json = json.dumps(value or {})

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "tool": self.tool,
            "source_public_id": self.source_public_id,
            "source_duration": self.source_duration,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "client_name": self.client_name or "",
            "options": self.options,
            "plan": self.plan,
            "transformation": self.transformation or "",
            "status": self.status,
            "error": self.error or "",
            "result_url": self.result_url or "",
            "saved_public_id": self.saved_public_id or "",
            "saved_url": self.saved_url or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "finished_at": self.finished_at.isoformat() if self.finished_at else "",
            "seen_at": self.seen_at.isoformat() if self.seen_at else "",
        }


def _loads(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}
