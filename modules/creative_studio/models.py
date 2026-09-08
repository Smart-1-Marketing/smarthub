"""Creative Studio's own tables.

Prefixed `cs_`, the same reason `modules/commercial_builder/models.py` gives
about `cb_`: this shares a database with twenty other modules and a table
called `projects` or `jobs` is a collision waiting for whichever module adds
one next.

Client identity is a **name**, never a numeric id of this module's own —
`hub/client_key.py` is emphatic that a derived key must never be stored, and
the Hub's own convention (Video Tools, Commercial Builder, every landing
module) is to key on the client name as typed and resolve it against
`hub.clients_registry` at write time. `hub_client_id` in the build spec means
"the Hub's own idea of this client", which here is that name -- not a second
numeric id this module would have to keep in step with anything.

`creative_jobs` is deliberately one table for every asynchronous kind this
module runs (§0 of the build spec) rather than one table per kind, so the
`hub/scheduler.py` sweep and the `/creative/api/jobs/<id>` poll route have
one shape to read regardless of what is running.
"""
from __future__ import annotations

import json
from datetime import datetime

from .db import db


class JSONField:
    """Descriptor that (de)serializes a Text column as JSON on access.

    Text rather than a native JSON column so this behaves identically on
    SQLite (tests, standalone) and Postgres (production) -- the reason
    `modules/commercial_builder/models.py` gives for the same choice.
    """

    def __init__(self, column_name):
        self.column_name = column_name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        raw = getattr(obj, self.column_name)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}

    def __set__(self, obj, value):
        setattr(obj, self.column_name, json.dumps(value if value is not None else {}))


class CsProject(db.Model):
    __tablename__ = "cs_projects"

    id = db.Column(db.Integer, primary_key=True)

    # Blank means "Generic Smart 1 asset" -- the one allowed no-client path
    # the build spec names in §4, using the Smart 1 brand record rather than
    # a client's. Never a guessed name: the Create New flow resolves this
    # against hub.clients_registry.search_clients before it is stored.
    client_name = db.Column(db.String(200), default="", index=True)

    name = db.Column(db.String(300), nullable=False)
    creative_type = db.Column(db.String(60), nullable=False, index=True)

    # Free text today; becomes a foreign key into cs_templates once WO-CS2
    # ships the template database. Kept as a string column now so a project
    # created in this change is not silently orphaned by that migration --
    # the id simply starts resolving to a real row.
    template_id = db.Column(db.String(80), default="")
    template_version = db.Column(db.Integer)

    duration = db.Column(db.Integer)          # seconds
    aspect_ratio = db.Column(db.String(20), default="16:9")

    status = db.Column(db.String(30), default="Draft", index=True)

    brief_json = db.Column(db.Text)
    resolved_vars_json = db.Column(db.Text)

    # Set once this project's video is actually built through the Commercial
    # Builder pipeline (WO-CS3 onward). Nullable and unenforced here -- this
    # module never writes to modules.commercial_builder's tables, it only
    # remembers which row it handed the work to.
    cb_project_id = db.Column(db.Integer)

    created_by = db.Column(db.String(120), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_at = db.Column(db.DateTime)

    brief = JSONField("brief_json")
    resolved_vars = JSONField("resolved_vars_json")

    versions = db.relationship("CsProjectVersion", backref="project",
                               lazy="dynamic", cascade="all, delete-orphan",
                               order_by="CsProjectVersion.version")

    def as_dict(self) -> dict:
        return {
            "id": self.id, "client_name": self.client_name or "",
            "name": self.name, "creative_type": self.creative_type,
            "template_id": self.template_id or "",
            "template_version": self.template_version,
            "duration": self.duration, "aspect_ratio": self.aspect_ratio or "",
            "status": self.status, "brief": self.brief,
            "resolved_vars": self.resolved_vars,
            "cb_project_id": self.cb_project_id,
            "created_by": self.created_by or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else "",
            "approved_at": self.approved_at.isoformat() if self.approved_at else "",
            "version_count": self.versions.count(),
        }


class CsProjectVersion(db.Model):
    """A render that succeeded. Never updated in place -- CLAUDE.md's rule
    for this corner of the Hub: a version is written once and stands, and
    approving one files *that* version rather than whatever the row now
    happens to hold."""

    __tablename__ = "cs_project_versions"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("cs_projects.id"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)

    render_url = db.Column(db.String(1000))
    thumbnail_url = db.Column(db.String(1000))
    creatomate_render_id = db.Column(db.String(120))

    created_by = db.Column(db.String(120), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    notes = db.Column(db.Text)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "project_id": self.project_id, "version": self.version,
            "render_url": self.render_url or "", "thumbnail_url": self.thumbnail_url or "",
            "creatomate_render_id": self.creatomate_render_id or "",
            "created_by": self.created_by or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "notes": self.notes or "",
        }


class CsMediaAsset(db.Model):
    """One row per Cloudinary asset a client's Media Library can show.

    Storage stays Cloudinary -- this is the index over it, per house rule 3.
    `deleted` is a soft flag rather than a row removal: the Cloudinary asset
    itself is kept for 30 days by the retention sweep, and a row deleted
    outright here would make that grace period unrecoverable from this
    screen (the same shape `modules/ad_builder`'s retention.ts keeps for
    `deliveries/`)."""

    __tablename__ = "cs_media_assets"

    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(200), default="", index=True)

    asset_type = db.Column(db.String(20), nullable=False, index=True)  # config.ASSET_TYPE_KEYS
    filename = db.Column(db.String(400), default="")
    original_filename = db.Column(db.String(400), default="")
    mime_type = db.Column(db.String(120), default="")

    width = db.Column(db.Integer)
    height = db.Column(db.Integer)
    duration = db.Column(db.Float)
    file_size = db.Column(db.Integer)

    cloudinary_public_id = db.Column(db.String(500), index=True)
    cloudinary_url = db.Column(db.String(1000))
    thumbnail_url = db.Column(db.String(1000))

    created_by = db.Column(db.String(120), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    tags_json = db.Column(db.Text)
    # config.ASSET_SOURCES -- who or what put this here.
    source = db.Column(db.String(30), default="upload")
    provider_meta_json = db.Column(db.Text)

    project_id = db.Column(db.Integer, db.ForeignKey("cs_projects.id"))

    deleted = db.Column(db.Boolean, default=False, index=True)

    tags = JSONField("tags_json")
    provider_meta = JSONField("provider_meta_json")

    def as_dict(self) -> dict:
        return {
            "id": self.id, "client_name": self.client_name or "",
            "asset_type": self.asset_type,
            "filename": self.filename or "", "original_filename": self.original_filename or "",
            "mime_type": self.mime_type or "",
            "width": self.width, "height": self.height, "duration": self.duration,
            "file_size": self.file_size,
            "cloudinary_public_id": self.cloudinary_public_id or "",
            "cloudinary_url": self.cloudinary_url or "",
            "thumbnail_url": self.thumbnail_url or self.cloudinary_url or "",
            "created_by": self.created_by or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "tags": self.tags, "source": self.source or "upload",
            "provider_meta": self.provider_meta,
            "project_id": self.project_id,
        }


class CsMusicTrack(db.Model):
    """Metadata for a CsMediaAsset of type "music". Its own table rather than
    columns on CsMediaAsset, because only music carries them and a table of
    mostly-null columns is how a schema comes to look like documentation of
    what one row-shape used to need."""

    __tablename__ = "cs_music_tracks"

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("cs_media_assets.id"), nullable=False, index=True)
    title = db.Column(db.String(200), default="")
    artist = db.Column(db.String(200), default="")
    genre = db.Column(db.String(100), default="")
    mood = db.Column(db.String(100), default="")
    license_source = db.Column(db.String(200), default="")

    def as_dict(self) -> dict:
        return {"id": self.id, "asset_id": self.asset_id, "title": self.title or "",
                "artist": self.artist or "", "genre": self.genre or "",
                "mood": self.mood or "", "license_source": self.license_source or ""}


class CreativeJob(db.Model):
    """One asynchronous unit of work, of whatever kind. Nothing long-running
    ever happens inside a request (house rule 4) -- a route that starts work
    writes a row here and returns its id; the `hub/scheduler.py` sweep this
    module registers is what actually calls the provider."""

    __tablename__ = "creative_jobs"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(30), nullable=False, index=True)   # config.JOB_KINDS
    project_id = db.Column(db.Integer, db.ForeignKey("cs_projects.id"), index=True)
    client_name = db.Column(db.String(200), default="")

    state = db.Column(db.String(20), default="queued", index=True)
    # queued | processing | rendering | uploading | complete | failed
    stage = db.Column(db.String(80), default="")
    progress = db.Column(db.Integer, default=0)

    attempts = db.Column(db.Integer, default=0)
    max_attempts = db.Column(db.Integer, default=3)
    timeout_at = db.Column(db.DateTime)

    payload_json = db.Column(db.Text)
    output_json = db.Column(db.Text)
    error = db.Column(db.Text)

    created_by = db.Column(db.String(120), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)

    payload = JSONField("payload_json")
    output = JSONField("output_json")

    def as_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "project_id": self.project_id,
            "client_name": self.client_name or "",
            "state": self.state, "stage": self.stage or "", "progress": self.progress or 0,
            "attempts": self.attempts or 0, "max_attempts": self.max_attempts or 3,
            "timeout_at": self.timeout_at.isoformat() if self.timeout_at else "",
            "payload": self.payload, "output": self.output,
            "error": self.error or "",
            "created_by": self.created_by or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "started_at": self.started_at.isoformat() if self.started_at else "",
            "finished_at": self.finished_at.isoformat() if self.finished_at else "",
        }
