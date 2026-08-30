"""
Persistence for the Image Picker.

The engine comes from hub/extensions, so this module shares the Hub's single
connection pool rather than opening a second one against the same Postgres.
Table names are prefixed `image_picker_` so they cannot collide on the shared
DATABASE_URL -- the Scans module review flagged exactly that risk.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, select,
)
from sqlalchemy.orm import declarative_base, relationship

from hub.extensions import (BootProbe, create_all_metadata, session_factory,
                            shared_engine)

Base = declarative_base()

_ENGINE = None
_Session = None
DB_BOOT_ERROR: str | None = None
# Re-asks a boot verdict that came from a failure to *connect*. Without it,
# session() raised for the life of the worker -- and the link this module
# hands a client to upload their own photographs is on the far side of it, so
# a six-second blip during a deploy read to the client as a broken page.
_PROBE = None


def init_db(app=None) -> None:
    """Create the engine and tables.

    Guarded exactly like the Scans module was fixed to be: a database that is
    slow to wake must not take the whole module offline for the life of the
    worker. The error is captured and surfaced per request instead.
    """
    global _ENGINE, _Session, DB_BOOT_ERROR, _PROBE
    try:
        _ENGINE = shared_engine()
        _Session = session_factory(scoped=True)
        if _PROBE is None:
            _PROBE = BootProbe(_create_tables, label="image_picker")
        DB_BOOT_ERROR = _PROBE.record(_create_tables()) or None
    except Exception as exc:  # noqa: BLE001
        DB_BOOT_ERROR = str(exc)


def _create_tables() -> str:
    """This module's whole boot DDL, as one answer.

    One function rather than two statements, because BootProbe re-runs it and
    the late columns have to be inside that: a module which recovers from a
    database that was briefly unreachable must not come back missing them.
    """
    # Advisory-locked so the two gunicorn workers don't race the DDL.
    err = create_all_metadata(Base.metadata)
    if err:
        return err
    try:
        _add_missing_columns()
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return ""


# Columns added after their table was first created, as
# (table, column, type). create_all() creates missing TABLES, never missing
# columns, so a database that predates one of these keeps working right up
# until the first query that mentions it.
_LATE_COLUMNS = [
    ("image_picker_clients", "kind", "VARCHAR(20) DEFAULT 'prospect'"),
    ("image_picker_images", "resource_type", "VARCHAR(20) DEFAULT 'image'"),
    ("image_picker_images", "spec_result", "VARCHAR(10)"),
    ("image_picker_images", "spec_summary", "TEXT"),
    ("image_picker_images", "spec_unit", "VARCHAR(60)"),
    ("image_picker_clients", "business_category", "VARCHAR(120)"),
    ("image_picker_clients", "business_profile", "TEXT"),
    ("image_picker_clients", "ai_collections", "TEXT"),
]


def _add_missing_columns() -> None:
    """Add the columns above, asking first which ones are actually missing.

    This used to fire all five ALTERs unconditionally and swallow the failures,
    on the reasoning that "already exists" is the normal case after the first
    boot. It is worse than that: every one of these columns is declared on the
    model, so create_all() puts them on a fresh database too and ALL FIVE fail
    on EVERY boot, on every database, forever. Two gunicorn workers, so ten
    Postgres ERROR lines per deploy that mean nothing -- and a log that always
    carries ten fake errors is a log nobody reads the real one out of.

    Asking the inspector instead costs one catalogue query per table and
    normally issues no DDL at all. The try/except stays, but now it covers only
    what it was supposed to: a genuine race with the other worker between the
    inspection and the ALTER.

    ADD COLUMN IF NOT EXISTS would be shorter and is what modules/sites_admin
    uses -- but that module talks to Postgres directly, and this one shares the
    Hub engine, which is SQLite in local development. SQLite has no IF NOT
    EXISTS on ADD COLUMN. The inspector is the same answer on both.
    """
    from sqlalchemy import inspect as _inspect, text as _text
    insp = _inspect(_ENGINE)
    known: dict[str, set[str]] = {}
    for table, column, coltype in _LATE_COLUMNS:
        if table not in known:
            try:
                known[table] = {c["name"] for c in insp.get_columns(table)}
            except Exception:                           # noqa: BLE001
                known[table] = set()                    # no table: nothing to alter
        if not known[table] or column in known[table]:
            continue
        try:
            with _ENGINE.begin() as conn:
                conn.execute(_text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
            known[table].add(column)
        except Exception:                               # noqa: BLE001
            pass                                        # raced by the other worker


def db_error() -> str | None:
    """The verdict as it stands, re-asking a transient one on a cooldown."""
    global DB_BOOT_ERROR
    if _PROBE is not None:
        DB_BOOT_ERROR = _PROBE.error() or None
    return DB_BOOT_ERROR


def session():
    if _Session is None:
        init_db()
    err = db_error()
    if err:
        raise RuntimeError(f"database unavailable: {err}")
    return _Session()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """Always serialise with an offset.

    The Scans module stored naive UTC and serialised without one, so browsers
    read the timestamps as local time and displayed scans hours in the future.
    Not repeating that.
    """
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return s or "client"


def new_token() -> str:
    return secrets.token_urlsafe(32)


class PickerClient(Base):
    """A client the picker can save images for.

    `hub_client_id` is the join back to the Hub's own client registry. It is
    nullable so the module runs standalone; once merged, populate it and treat
    the registry as the source of truth for name and industry.
    """

    __tablename__ = "image_picker_clients"

    id = Column(Integer, primary_key=True)
    hub_client_id = Column(String(64), nullable=True, index=True)
    # "client" once it is attached to a record in the Hub registry, "prospect"
    # until then. A gallery often has to exist before the account does — the
    # whole point is to collect a prospect's photos while they are willing to
    # send them — so this is not derived from hub_client_id being set.
    kind = Column(String(20), nullable=False, default="prospect")
    name = Column(String(200), nullable=False)
    slug = Column(String(200), nullable=False, unique=True, index=True)
    industry_key = Column(String(60), nullable=False, default="general")

    # What a General Business client told us they do. The industry dropdown's
    # last entry is its busiest, and it hands out four generic chips; these two
    # answers are what turns that into a picker about their business. Kept on
    # the row so the next visit — and the next person from Smart 1 picking on
    # their behalf — starts from the same answers.
    business_category = Column(String(120), nullable=True)
    business_profile = Column(Text, nullable=True)
    # The topics and services built from those answers, as JSON. Deliberately a
    # blob rather than a table: they are a derivation of the two fields above,
    # re-made whenever somebody presses the button, and a table would invite
    # editing one row of a set that is only ever written whole.
    ai_collections = Column(Text, nullable=True)

    cloudinary_folder = Column(String(300), nullable=True)

    ghl_location_id = Column(String(120), nullable=True)
    # Optional per-location token. When blank we fall back to the agency-level
    # GHL_PRIVATE_TOKEN with altType=location.
    ghl_location_token = Column(Text, nullable=True)
    ghl_enabled = Column(Boolean, nullable=False, default=True)

    share_token = Column(String(64), nullable=False, unique=True, index=True, default=new_token)
    share_enabled = Column(Boolean, nullable=False, default=True)
    share_note = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    images = relationship("SavedImage", back_populates="client", cascade="all, delete-orphan")

    def folder(self) -> str:
        base = os.environ.get("IMAGE_PICKER_FOLDER", "smart1-client-images")
        return self.cloudinary_folder or f"{base}/{self.slug}"

    def client_key(self) -> str:
        """The Hub-wide client key for this gallery.

        `hub_client_id` is the join this module was built to have, and it is
        blank on nearly every row because filling it in is a manual step
        somebody has to remember. The derived key needs nobody to remember
        anything: it works off the name that is already there, and it keeps
        working when the gallery is renamed.
        """
        try:
            from hub.client_key import resolve
            return resolve(name=self.name or "")["key"]
        except Exception:                                 # noqa: BLE001
            return ""

    def to_dict(self, *, include_secrets: bool = False) -> dict:
        d = {
            "id": self.id,
            "hub_client_id": self.hub_client_id,
            "client_key": self.client_key(),
            "name": self.name,
            "slug": self.slug,
            "industry_key": self.industry_key,
            "cloudinary_folder": self.folder(),
            "ghl_location_id": self.ghl_location_id or "",
            "ghl_enabled": bool(self.ghl_enabled),
            "ghl_configured": bool(self.ghl_location_id),
            "business_category": self.business_category or "",
            "business_profile": self.business_profile or "",
            "share_enabled": bool(self.share_enabled),
            "share_note": self.share_note or "",
            "created_at": iso(self.created_at),
        }
        if include_secrets:
            d["share_token"] = self.share_token
            d["has_location_token"] = bool(self.ghl_location_token)
        return d


class ImageDescription(Base):
    """What a vision model saw in one saved image.

    Its own table, not columns on `image_picker_images`, for the reason
    CLAUDE.md gives at length: `create_all()` creates missing tables and never
    adds a column to an existing one, so a `description` column added there
    would exist on every local SQLite run and be silently absent on the live
    Postgres — every test green, every read `None` in production. That is why
    `hub_user_profiles` and `cb_render_approvals` are their own tables too.

    A row here is an **observation**, never the image's alt text. The client's
    own words, or a rep's, are the better source and are never written over:
    a description is offered into an empty `alt_text` and kept on a press, the
    overlay rule `hub/client_urls.py` works to and `scan_facts` applies to a
    logo it saw on a page. `accepted_at` says a person took it.
    """

    __tablename__ = "image_picker_descriptions"

    id = Column(Integer, primary_key=True)
    image_id = Column(Integer,
                      ForeignKey("image_picker_images.id", ondelete="CASCADE"),
                      nullable=False, unique=True, index=True)

    # "described" or "given_up". Written down rather than held in memory,
    # because a give-up that forgets itself on the next deploy costs a vision
    # call an hour for ever — the failure hub/video_library.py names.
    state = Column(String(20), nullable=False, default="described", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)

    # A sentence a person reads, and the closed vocabulary a search filters on.
    description = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)            # comma-separated, from VOCAB
    alt_suggestion = Column(Text, nullable=True)

    model = Column(String(60), nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    accepted_by = Column(String(200), nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    def to_dict(self) -> dict:
        return {
            "image_id": self.image_id,
            "state": self.state or "described",
            "attempts": int(self.attempts or 0),
            "description": self.description or "",
            "tags": [t for t in (self.tags or "").split(",") if t],
            "alt_suggestion": self.alt_suggestion or "",
            "accepted": bool(self.accepted_at),
            "accepted_by": self.accepted_by or "",
            "last_error": self.last_error or "",
        }


def _preview(url, resource_type=""):
    """A derived thumbnail where one is possible, and the original otherwise."""
    try:
        from hub import storage
        return storage.preview_url(url or "", resource_type or "")
    except Exception:                       # noqa: BLE001
        return url or ""


class SavedImage(Base):
    __tablename__ = "image_picker_images"
    __table_args__ = (
        # One provider photo lands in one client's gallery once. Without this a
        # double-tap or a back-button retry duplicates the Cloudinary asset and
        # the GHL upload.
        UniqueConstraint("client_id", "provider", "provider_image_id", name="uq_picker_client_photo"),
    )

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("image_picker_clients.id", ondelete="CASCADE"),
                       nullable=False, index=True)

    provider = Column(String(40), nullable=False)
    provider_image_id = Column(String(120), nullable=False)
    source_url = Column(Text, nullable=True)
    author = Column(String(200), nullable=True)
    author_url = Column(Text, nullable=True)

    alt_text = Column(Text, nullable=True)
    filename = Column(String(300), nullable=True)

    industry_key = Column(String(60), nullable=True)
    collection_kind = Column(String(20), nullable=True)   # topic | service | search
    collection_key = Column(String(80), nullable=True)
    collection_label = Column(String(200), nullable=True)

    # image | raw. A client sending "the photos" routinely sends a PDF of the
    # brochure too, and refusing it means they email it instead and it is lost.
    resource_type = Column(String(20), nullable=False, default="image")
    cloudinary_public_id = Column(String(400), nullable=True)
    cloudinary_url = Column(Text, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    bytes = Column(Integer, nullable=True)

    ghl_status = Column(String(20), nullable=False, default="pending")  # pending|sent|skipped|error
    ghl_file_id = Column(String(200), nullable=True)
    ghl_url = Column(Text, nullable=True)
    ghl_error = Column(Text, nullable=True)

    # Whether this file met the 2025 Creative Spec Kit, decided at upload time
    # by hub.creative_specs. Stored rather than recomputed because the verdict
    # belongs to the file as it was delivered: re-running the check later, after
    # the kit is revised, would silently rewrite history on work already sent.
    spec_result = Column(String(10), nullable=True)     # pass|warn|fail|unknown
    spec_summary = Column(Text, nullable=True)
    spec_unit = Column(String(60), nullable=True)       # e.g. medium_rectangle

    saved_by = Column(String(200), nullable=True)   # hub user email, or "client"
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)

    client = relationship("PickerClient", back_populates="images")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "client_id": self.client_id,
            "provider": self.provider,
            "resource_type": self.resource_type or "image",
            "provider_image_id": self.provider_image_id,
            "source_url": self.source_url,
            "author": self.author,
            "author_url": self.author_url,
            "alt_text": self.alt_text or "",
            "filename": self.filename or "",
            "industry_key": self.industry_key,
            "collection_kind": self.collection_kind,
            "collection_key": self.collection_key,
            "collection_label": self.collection_label,
            "url": self.cloudinary_url,
            # What a gallery draws. The full asset stays on `url` for the
            # link and the download; a tile that requested it was delivering
            # a phone photograph to fill a 64x48 box, on every row, every
            # load. Server-side because which URLs may be rewritten and which
            # resource types must not be is a storage rule, and a copy of it
            # in each of the twelve templates that draw a tile is the drift
            # `hub/storage.py` exists to stop. Never raises: a gallery that
            # cannot compute a preview shows the original, which is what it
            # showed before.
            "thumb": _preview(self.cloudinary_url, self.resource_type),
            "public_id": self.cloudinary_public_id,
            "width": self.width,
            "height": self.height,
            "bytes": self.bytes,
            "ghl_status": self.ghl_status,
            "ghl_file_id": self.ghl_file_id,
            "ghl_url": self.ghl_url,
            "ghl_error": self.ghl_error,
            "spec_result": self.spec_result,
            "spec_summary": self.spec_summary or "",
            "spec_unit": self.spec_unit,
            "saved_by": self.saved_by,
            "created_at": iso(self.created_at),
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def get_client(db, client_id: int) -> PickerClient | None:
    return db.get(PickerClient, client_id)


def get_client_by_token(db, token: str) -> PickerClient | None:
    if not token:
        return None
    return db.execute(
        select(PickerClient).where(PickerClient.share_token == token)
    ).scalar_one_or_none()


def unique_slug(db, name: str) -> str:
    base = slugify(name)
    slug = base
    n = 2
    while db.execute(select(PickerClient).where(PickerClient.slug == slug)).scalar_one_or_none():
        slug = f"{base}-{n}"
        n += 1
    return slug


def already_saved(db, client_id: int, provider: str, provider_image_id: str) -> SavedImage | None:
    return db.execute(
        select(SavedImage).where(
            SavedImage.client_id == client_id,
            SavedImage.provider == provider,
            SavedImage.provider_image_id == str(provider_image_id),
        )
    ).scalar_one_or_none()
