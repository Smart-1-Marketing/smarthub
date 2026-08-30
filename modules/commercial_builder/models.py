"""
Commercial Builder data model.

Table names are prefixed `cb_` so they can't collide with existing Hub
tables when this module is merged into the main app's shared database.

Notes on integration:
- `Client` here is a *brand profile* purpose-built for commercial
  production (fonts, voice/spokesperson preferences, pronunciation
  dictionary, etc.). If/when this module is merged into Smart 1 Hub proper,
  prefer adding a `hub_client_id` foreign key to the Hub's existing client
  record (see `hub/clients_registry.py` per the v1.6.0 handoff) rather than
  duplicating name/website/logo — that data already lives there. It's kept
  as a standalone table here so this module runs on its own.
- All "blob" fields (formats, brief, script, changes, qc_results) are
  stored as JSON text via a small helper property so this works identically
  on SQLite (dev) and Postgres (prod) without needing native JSON columns.
"""

import json
from datetime import datetime

from .db import db


class JSONField:
    """Descriptor that (de)serializes a Text column as JSON on access."""

    def __init__(self, column_name):
        self.column_name = column_name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        raw = getattr(obj, self.column_name)
        if not raw:
            return {} if self.column_name.endswith(("_json",)) else None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def __set__(self, obj, value):
        setattr(obj, self.column_name, json.dumps(value) if value is not None else None)


class Client(db.Model):
    __tablename__ = "cb_clients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), unique=True, nullable=False)
    website = db.Column(db.String(400))
    logo_url = db.Column(db.String(500))
    primary_color = db.Column(db.String(20))
    secondary_color = db.Column(db.String(20))
    fonts_json = db.Column(db.Text)
    phone = db.Column(db.String(40))
    address = db.Column(db.String(400))
    cta = db.Column(db.String(200))
    tagline = db.Column(db.String(300))
    industry = db.Column(db.String(120))
    service_area = db.Column(db.String(300))
    brand_voice = db.Column(db.Text)
    preferred_voiceover_id = db.Column(db.String(120))
    preferred_music_style = db.Column(db.String(60))
    preferred_spokesperson_id = db.Column(db.String(60))
    pronunciation_dict_json = db.Column(db.Text)  # {"Gahanna": "guh-HAN-uh", ...}
    cloudinary_folder = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    fonts = JSONField("fonts_json")
    pronunciation_dict = JSONField("pronunciation_dict_json")

    projects = db.relationship("CommercialProject", backref="client", lazy="dynamic",
                                cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "slug": self.slug, "website": self.website,
            "logo_url": self.logo_url, "primary_color": self.primary_color,
            "secondary_color": self.secondary_color, "fonts": self.fonts or [],
            "phone": self.phone, "address": self.address, "cta": self.cta,
            "tagline": self.tagline, "industry": self.industry,
            "service_area": self.service_area, "brand_voice": self.brand_voice,
            "preferred_voiceover_id": self.preferred_voiceover_id,
            "preferred_music_style": self.preferred_music_style,
            "preferred_spokesperson_id": self.preferred_spokesperson_id,
            "pronunciation_dict": self.pronunciation_dict or {},
            "cloudinary_folder": self.cloudinary_folder or f"clients/{self.slug}",
        }


class CommercialProject(db.Model):
    __tablename__ = "cb_projects"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("cb_clients.id"), nullable=False)
    title = db.Column(db.String(300))
    campaign_id = db.Column(db.Integer, db.ForeignKey("cb_campaigns.id"), nullable=True)

    length_seconds = db.Column(db.Integer, nullable=False, default=30)
    formats_json = db.Column(db.Text, default="[]")          # ["16:9", "9:16"]
    commercial_type = db.Column(db.String(60))
    # "ctv" | "youtube" | "both" — drives script structure, QC rules (QR
    # required for CTV, skippable-hook check for YouTube), and safe-area
    # sizing guidance. See config.PLATFORMS / config.STRUCTURE_TEMPLATES.
    platform = db.Column(db.String(20), default="both")

    brief_json = db.Column(db.Text)          # what_advertising, cta, landing_page, phone, audience, tone
    concepts_json = db.Column(db.Text)       # list of {id,title,angle,summary}
    selected_concept_id = db.Column(db.String(20))
    script_json = db.Column(db.Text)         # {duration, scenes:[{start,end,visual,voiceover}], word_count}
    music_json = db.Column(db.Text)          # {mood, level}
    cta_json = db.Column(db.Text)            # {style, headline, offer, website, phone}
    qc_results_json = db.Column(db.Text)

    status = db.Column(db.String(30), default="draft")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    formats = JSONField("formats_json")
    brief = JSONField("brief_json")
    concepts = JSONField("concepts_json")
    script = JSONField("script_json")
    music = JSONField("music_json")
    cta = JSONField("cta_json")
    qc_results = JSONField("qc_results_json")

    scenes = db.relationship("Scene", backref="project", lazy="dynamic",
                              order_by="Scene.order_index", cascade="all, delete-orphan")
    render_jobs = db.relationship("RenderJob", backref="project", lazy="dynamic",
                                   cascade="all, delete-orphan")

    def to_dict(self, include_scenes=True):
        d = {
            "id": self.id, "client_id": self.client_id, "title": self.title,
            "campaign_id": self.campaign_id,
            "length_seconds": self.length_seconds, "formats": self.formats or [],
            "commercial_type": self.commercial_type, "platform": self.platform or "both",
            "brief": self.brief or {},
            "concepts": self.concepts or [], "selected_concept_id": self.selected_concept_id,
            "script": self.script or {}, "music": self.music or {}, "cta": self.cta or {},
            "qc_results": self.qc_results or {}, "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_scenes:
            d["scenes"] = [s.to_dict() for s in self.scenes.all()]
        return d


class Scene(db.Model):
    __tablename__ = "cb_scenes"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("cb_projects.id"), nullable=False)
    order_index = db.Column(db.Integer, nullable=False, default=0)

    start = db.Column(db.Float, default=0)
    end = db.Column(db.Float, default=0)
    narration = db.Column(db.Text)
    visual_description = db.Column(db.Text)

    asset_type = db.Column(db.String(30))     # stock | ai_generated | spokesperson | upload | client_asset | cta
    asset_source = db.Column(db.String(30))   # pexels | pixabay | runway | heygen | upload | cloudinary
    asset_url = db.Column(db.String(600))
    asset_thumb_url = db.Column(db.String(600))
    asset_meta_json = db.Column(db.Text)      # provider id, author, license, generation prompt, options offered
    is_cta = db.Column(db.Boolean, default=False)
    locked = db.Column(db.Boolean, default=False)  # true once user approves — variations should skip locked scenes

    asset_meta = JSONField("asset_meta_json")

    def to_dict(self):
        return {
            "id": self.id, "project_id": self.project_id, "order_index": self.order_index,
            "start": self.start, "end": self.end, "duration": round((self.end or 0) - (self.start or 0), 2),
            "narration": self.narration, "visual_description": self.visual_description,
            "asset_type": self.asset_type, "asset_source": self.asset_source,
            "asset_url": self.asset_url, "asset_thumb_url": self.asset_thumb_url,
            "asset_meta": self.asset_meta or {}, "is_cta": self.is_cta, "locked": self.locked,
        }


class RenderJob(db.Model):
    __tablename__ = "cb_render_jobs"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("cb_projects.id"), nullable=False)
    format = db.Column(db.String(10))          # 16:9 | 9:16 | 1:1
    provider_render_id = db.Column(db.String(120))
    status = db.Column(db.String(30), default="queued")  # queued|rendering|succeeded|failed
    output_url = db.Column(db.String(600))
    error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "project_id": self.project_id, "format": self.format,
            "provider_render_id": self.provider_render_id, "status": self.status,
            "output_url": self.output_url, "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RenderApproval(db.Model):
    """A human said this rendered cut is good, and where it was filed.

    Its own table rather than columns on `cb_render_jobs`, because
    `create_all()` creates missing TABLES and never adds a column to an
    existing one — an `approved_by` column here would exist on every local
    SQLite run and be silently absent on the live Postgres, with every test
    green and every read of it None. `hub_user_profiles` and the presence
    table are here for the same reason.

    What it records is deliberately more than a boolean. Approving files the
    finished video in two places — the client's Cloudinary library and the
    Hub's activity log, which is what puts it on Client 360 — and those can
    succeed separately. `hub/domain_links.py` says at length why one tick over
    two writes is how somebody learns not to trust the tick, so each is
    recorded and each is reported.
    """
    __tablename__ = "cb_render_approvals"

    id = db.Column(db.Integer, primary_key=True)
    render_job_id = db.Column(db.Integer, db.ForeignKey("cb_render_jobs.id"),
                              nullable=False, unique=True)
    project_id = db.Column(db.Integer, db.ForeignKey("cb_projects.id"), nullable=False)
    approved_by = db.Column(db.String(200))
    approved_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Where it landed. Empty means that write did not happen, which is a
    # different answer from "it was never approved".
    stored_url = db.Column(db.String(600))
    stored_public_id = db.Column(db.String(300))
    filed_to_client = db.Column(db.Boolean, default=False)
    filing_error = db.Column(db.Text)

    def to_dict(self):
        return {
            "id": self.id, "render_job_id": self.render_job_id,
            "project_id": self.project_id, "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "stored_url": self.stored_url, "stored_public_id": self.stored_public_id,
            "filed_to_client": bool(self.filed_to_client),
            "filing_error": self.filing_error or "",
        }


class Campaign(db.Model):
    """Groups multiple length/aspect-ratio commercials under one master concept (spec section 15)."""
    __tablename__ = "cb_campaigns"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("cb_clients.id"), nullable=False)
    name = db.Column(db.String(300))
    master_concept = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    projects = db.relationship("CommercialProject", backref="campaign", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id, "client_id": self.client_id, "name": self.name,
            "master_concept": self.master_concept,
            "project_ids": [p.id for p in self.projects.all()],
        }


class Variation(db.Model):
    """Tracks a project spun off from another via 'Create Variation' (spec section 14)."""
    __tablename__ = "cb_variations"

    id = db.Column(db.Integer, primary_key=True)
    parent_project_id = db.Column(db.Integer, db.ForeignKey("cb_projects.id"), nullable=False)
    child_project_id = db.Column(db.Integer, db.ForeignKey("cb_projects.id"), nullable=False)
    variation_type = db.Column(db.String(40))   # offer|location|weather|cta|voice|footage|duration
    changes_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    changes = JSONField("changes_json")

    def to_dict(self):
        return {
            "id": self.id, "parent_project_id": self.parent_project_id,
            "child_project_id": self.child_project_id, "variation_type": self.variation_type,
            "changes": self.changes or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ReviewShare(db.Model):
    """One client-facing review link for one spot, and what came back on it.

    Its own table rather than columns on `cb_projects`, for the reason
    `RenderApproval` above gives and `modules/ads_builder/store.Share` gives
    again: `create_all()` creates a missing TABLE and never adds a column to
    an existing one, so a `review_token` on `cb_projects` would exist on every
    local SQLite run and be silently absent on the live Postgres — every test
    green, every read of it None.

    It also keeps what a **client** wrote out of the row a rep is editing at
    the same moment. The two are written by different people through different
    doors, and one of those people has no Hub login at all.

    A link is per project and per round. Sending a new round issues a NEW
    token rather than reopening this one: a link that has been answered is the
    record of that answer, and handing the same URL out again would overwrite
    the first round's decision with no trace that there had been one.
    """
    __tablename__ = "cb_shares"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, index=True, nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("cb_projects.id"), nullable=False)
    round_no = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(200), default="")
    revoked = db.Column(db.Boolean, default=False)

    # A note the rep writes to the client when sending it — "here is the :30,
    # the music is a placeholder". Optional, and shown above the video.
    message = db.Column(db.Text, default="")

    # How many times the page has been opened. "Sent and ignored" and "opened
    # four times and still not answered" are different conversations.
    opened_count = db.Column(db.Integer, default=0)
    last_opened_at = db.Column(db.DateTime, nullable=True)

    decisions = db.relationship("ReviewDecision", backref="share", lazy="dynamic",
                                cascade="all, delete-orphan")
    comments = db.relationship("ReviewComment", backref="share", lazy="dynamic",
                               cascade="all, delete-orphan")

    def to_dict(self, include_children=True):
        out = {
            "id": self.id, "token": self.token, "project_id": self.project_id,
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


class ReviewDecision(db.Model):
    """One person's answer on one review link.

    A row per reviewer rather than one answer on the share, because a link
    gets forwarded and two people at the client answer it. Collapsing that to
    one column means the second answer overwrites the first — and the case
    that matters is the compliance officer's refusal being overwritten by a
    colleague's "looks good", after which the cut ships.

    `review.verdict()` resolves the rows into the one answer a screen shows.
    """
    __tablename__ = "cb_share_decisions"

    id = db.Column(db.Integer, primary_key=True)
    share_id = db.Column(db.Integer, db.ForeignKey("cb_shares.id"), nullable=False)
    outcome = db.Column(db.String(40), default="")
    reviewer_name = db.Column(db.String(200), default="")
    reviewer_email = db.Column(db.String(200), default="")
    note = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "outcome": self.outcome or "",
            "reviewer_name": self.reviewer_name or "",
            "reviewer_email": self.reviewer_email or "",
            "note": self.note or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ReviewComment(db.Model):
    """A note the client left, optionally at a point in the cut.

    The timecode is what a video proof needs and a written estimate does not:
    "the phone number is wrong" is a different piece of work from "the phone
    number at 0:12 is wrong", and the second one can be actioned without
    watching the spot again to find it.

    `at_seconds` is nullable on purpose. A comment about the whole cut — the
    music, the pace, the voice — is a real thing to leave, and storing it as
    0.0 would file every general note at the first frame where the reader
    looks for something that is not there.
    """
    __tablename__ = "cb_share_comments"

    id = db.Column(db.Integer, primary_key=True)
    share_id = db.Column(db.Integer, db.ForeignKey("cb_shares.id"), nullable=False)
    text = db.Column(db.Text, default="")
    reviewer_name = db.Column(db.String(200), default="")
    reviewer_email = db.Column(db.String(200), default="")
    at_seconds = db.Column(db.Float, nullable=True)
    # Which cut it was left on. A client sent a 16:9 and a 9:16 can leave a
    # note on either, and "the logo is cropped" is only true of one of them.
    format = db.Column(db.String(10), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        from .review_spec import timecode
        return {
            "id": self.id, "text": self.text or "",
            "reviewer_name": self.reviewer_name or "",
            "reviewer_email": self.reviewer_email or "",
            "at_seconds": self.at_seconds,
            "timecode": timecode(self.at_seconds),
            "format": self.format or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
