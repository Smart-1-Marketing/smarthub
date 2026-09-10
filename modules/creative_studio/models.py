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

    # cs_templates.id -- a string slug rather than a numeric FK, so a
    # template can be named in code and in a fixture without a lookup. Not
    # declared as a real ForeignKey: a project must survive its template
    # being archived (or, on a fresh install, not yet seeded), and a strict
    # FK would refuse either.
    template_id = db.Column(db.String(80), default="")
    # Pinned at creation from the template's own `version` -- WO-CS2's rule
    # that a project is built from the template as it stood, not as it now
    # reads after somebody edits it in Template Admin.
    template_version = db.Column(db.Integer)

    duration = db.Column(db.Integer)          # seconds
    aspect_ratio = db.Column(db.String(20), default="16:9")

    status = db.Column(db.String(30), default="Draft", index=True)

    brief_json = db.Column(db.Text)
    # A rep's per-project variable overrides -- "manual" in the resolver's
    # priority order (manual -> brief -> Brand Kit -> template default ->
    # unresolved), keyed by variable name. Despite the column name this is
    # never a cached final answer: resolution is computed live on every read,
    # because the Brand Kit and the brief can both change after a project is
    # created and a stale snapshot would silently stop reflecting either.
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
            "variable_overrides": self.resolved_vars,
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


class CsTemplate(db.Model):
    """A template is a JSON document the gallery, the editor and (from
    WO-CS5) the renderer all read -- CLAUDE.md's "templates as data" section.
    The row here is that document's header; its scenes and variables are
    the two tables below, kept apart so an admin can add or reorder a scene
    without rewriting a blob that also holds the variable list.

    `id` is a string slug (`"hvac-30-general"`) rather than a numeric id on
    purpose: it is what `cs_projects.template_id` stores, what the seed
    fixtures name themselves, and what an admin reads back off the URL --
    a numeric id would need a second, meaningless label wherever a human
    reads one.
    """

    __tablename__ = "cs_templates"

    id = db.Column(db.String(80), primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    thumbnail_url = db.Column(db.String(1000), default="")

    # A broad grouping (commercial/social/brand/...) -- distinct from
    # `industry`, which narrows within it (hvac/restaurant/...). Gallery
    # filters read the DISTINCT values of both rather than a fixed list, so
    # adding an industry is a fixture, never a template edit -- CLAUDE.md's
    # rule for every filter in this Hub.
    category = db.Column(db.String(60), default="", index=True)
    industry = db.Column(db.String(60), default="general", index=True)
    duration = db.Column(db.Integer, default=30)
    aspect_ratio = db.Column(db.String(20), default="16:9", index=True)
    creative_type = db.Column(db.String(60), default="", index=True)

    tags_json = db.Column(db.Text)

    # draft -> published -> archived. A project may be created from a draft
    # (Template Admin's own preview does this constantly) but the public
    # gallery shows published only -- draft creative in front of a rep
    # picking a template for a client reads as this Hub's own work being
    # unfinished.
    status = db.Column(db.String(20), default="draft", index=True)

    # Bumped on every publish, never on a draft save -- a project pins the
    # version it was built from (`cs_projects.template_version`), so a
    # template edited after a dozen projects already exist must not silently
    # change what those dozen say they were built from.
    version = db.Column(db.Integer, default=1)

    created_by = db.Column(db.String(120), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tags = JSONField("tags_json")

    scenes = db.relationship("CsTemplateScene", backref="template",
                             lazy="dynamic", cascade="all, delete-orphan",
                             order_by="CsTemplateScene.position")
    variables = db.relationship("CsTemplateVariable", backref="template",
                                lazy="dynamic", cascade="all, delete-orphan",
                                order_by="CsTemplateVariable.name")

    def as_dict(self, *, with_children: bool = True) -> dict:
        out = {
            "id": self.id, "name": self.name, "description": self.description or "",
            "thumbnail_url": self.thumbnail_url or "",
            "category": self.category or "", "industry": self.industry or "general",
            "duration": self.duration, "aspect_ratio": self.aspect_ratio or "",
            "creative_type": self.creative_type or "", "tags": self.tags or [],
            "status": self.status, "version": self.version,
            "created_by": self.created_by or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else "",
        }
        if with_children:
            out["scenes"] = [s.as_dict() for s in self.scenes]
            out["variables"] = [v.as_dict() for v in self.variables]
        return out


class CsTemplateScene(db.Model):
    """One scene in a template's sequence -- a duration, a layout key (one
    of `modules.creative_studio.layouts.LAYOUTS`) and the layer content that
    layout accepts, e.g. `{"headline": "{{headline}}", "background":
    "slot:video"}`. `creatomate_fragment_json` is reserved and unused until
    WO-CS5 builds real Creatomate source from these rows -- writing the
    column now means a template edited today does not need a schema change
    the day rendering lands."""

    __tablename__ = "cs_template_scenes"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.String(80), db.ForeignKey("cs_templates.id"),
                            nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False, default=1)
    default_duration = db.Column(db.Float, default=5.0)
    layout_key = db.Column(db.String(40), nullable=False)
    layers_json = db.Column(db.Text)
    creatomate_fragment_json = db.Column(db.Text)

    layers = JSONField("layers_json")
    creatomate_fragment = JSONField("creatomate_fragment_json")

    def as_dict(self) -> dict:
        return {"id": self.id, "template_id": self.template_id,
                "position": self.position, "default_duration": self.default_duration,
                "layout_key": self.layout_key, "layers": self.layers}


class CsTemplateVariable(db.Model):
    """One variable a template's scenes reference as `{{name}}`.

    `source` is the variable's *declared* origin -- brand, brief, weather or
    manual -- read by the (future) brief-builder screen to decide which
    questions to ask; it does not restrict what `resolver.resolve()` tries,
    because a value from anywhere is still a value. `default` and `required`
    are what makes an unresolved required variable a visible finding on a
    project rather than a silently blank scene.
    """

    __tablename__ = "cs_template_variables"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.String(80), db.ForeignKey("cs_templates.id"),
                            nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    source = db.Column(db.String(20), default="brief")   # brand|brief|weather|manual
    default = db.Column(db.String(500), default="")
    required = db.Column(db.Boolean, default=False)

    def as_dict(self) -> dict:
        return {"id": self.id, "template_id": self.template_id, "name": self.name,
                "source": self.source or "brief", "default": self.default or "",
                "required": bool(self.required)}


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


class CsAiTool(db.Model):
    """One row per AI Tools tile. "Adding a tool is a row" -- WO-CS4's own
    words for the whole point of this table: `/creative-studio/ai-tools`
    renders from a query grouped by category, never a hand-typed block of
    HTML per tool, the rule CLAUDE.md states for `config.CREATIVE_TYPES` and
    every gallery filter in this Hub."""

    __tablename__ = "cs_ai_tools"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(60), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(40), nullable=False, index=True)
    description = db.Column(db.Text, default="")
    icon = db.Column(db.String(20), default="")
    # A URL this deployment already serves -- most tools here are a front
    # door onto something that exists, not a screen this module owns. Blank
    # only ever pairs with status="coming_soon", so a live tile always links
    # somewhere real.
    route = db.Column(db.String(300), default="")
    status = db.Column(db.String(20), default="coming_soon")  # live|coming_soon
    sort = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def as_dict(self) -> dict:
        return {"id": self.id, "key": self.key, "name": self.name,
                "category": self.category, "description": self.description or "",
                "icon": self.icon or "", "route": self.route or "",
                "status": self.status or "coming_soon", "sort": self.sort or 0}


class CsUsageLog(db.Model):
    """One row per external, billed call this module makes.

    A second, narrower ledger rather than a duplicate of `hub/quotas.py` --
    that module already meters spend across every module in the Hub for the
    account-wide usage page; this answers a question a rep actually asks on
    one project, which quotas.py cannot: what has *this commercial* cost so
    far. Every rate `config.PROVIDER_RATES` carries is a placeholder until
    Todd supplies real ones (WO-CS4's own words), and `estimated_cost` is
    `None` -- printed as *not measured*, never a silent zero -- wherever the
    rate is not on that table yet, the `HOUSE_LEGIBILITY` rule CLAUDE.md
    applies to every number nobody here has published.
    """

    __tablename__ = "cs_usage_logs"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("cs_projects.id"), index=True)
    client_name = db.Column(db.String(200), default="")

    # openai|runway|pexels|pixabay|unsplash|elevenlabs|heygen|creatomate
    provider = db.Column(db.String(40), nullable=False, index=True)
    # concepts|script|image|voice|video|render|stock_search
    service = db.Column(db.String(40), nullable=False)
    quantity = db.Column(db.Float, default=1.0)
    unit = db.Column(db.String(20), default="call")
    estimated_cost = db.Column(db.Float)   # None -- not measured against config.PROVIDER_RATES

    ok = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.String(120), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "project_id": self.project_id,
            "client_name": self.client_name or "", "provider": self.provider,
            "service": self.service, "quantity": self.quantity,
            "unit": self.unit or "call", "estimated_cost": self.estimated_cost,
            "ok": bool(self.ok), "created_by": self.created_by or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }


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
