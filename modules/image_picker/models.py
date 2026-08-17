"""
Persistence for the Image Picker.

MERGE NOTE (one line): when this module is dropped into the Hub, replace the
standalone engine below with `from hub.extensions import db` and delete the
`_standalone_*` block. Everything else in this file already uses the shared
declarative style the rest of the Hub uses. Table names are prefixed
`image_picker_` so they cannot collide on the shared DATABASE_URL -- the Scans
module review flagged exactly that risk.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, create_engine, select,
)
from sqlalchemy.orm import declarative_base, relationship, scoped_session, sessionmaker

Base = declarative_base()

# --- _standalone_engine: replace with hub.extensions.db on merge -------------
_ENGINE = None
_Session = None
DB_BOOT_ERROR: str | None = None


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///image_picker.db"
    # Render hands out postgres:// which SQLAlchemy 2.x no longer accepts.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def init_db(app=None) -> None:
    """Create the engine and tables.

    Guarded exactly like the Scans module was fixed to be: a database that is
    slow to wake must not take the whole module offline for the life of the
    worker. The error is captured and surfaced per request instead.
    """
    global _ENGINE, _Session, DB_BOOT_ERROR
    try:
        _ENGINE = create_engine(
            _database_url(), pool_pre_ping=True, future=True,
            connect_args={"check_same_thread": False} if _database_url().startswith("sqlite") else {},
        )
        _Session = scoped_session(sessionmaker(bind=_ENGINE, future=True, expire_on_commit=False))
        Base.metadata.create_all(_ENGINE)
        DB_BOOT_ERROR = None
    except Exception as exc:  # noqa: BLE001
        DB_BOOT_ERROR = str(exc)


def session():
    if _Session is None:
        init_db()
    if DB_BOOT_ERROR:
        raise RuntimeError(f"database unavailable: {DB_BOOT_ERROR}")
    return _Session()
# --- end _standalone_engine -------------------------------------------------


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
    name = Column(String(200), nullable=False)
    slug = Column(String(200), nullable=False, unique=True, index=True)
    industry_key = Column(String(60), nullable=False, default="general")

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

    def to_dict(self, *, include_secrets: bool = False) -> dict:
        d = {
            "id": self.id,
            "hub_client_id": self.hub_client_id,
            "name": self.name,
            "slug": self.slug,
            "industry_key": self.industry_key,
            "cloudinary_folder": self.folder(),
            "ghl_location_id": self.ghl_location_id or "",
            "ghl_enabled": bool(self.ghl_enabled),
            "ghl_configured": bool(self.ghl_location_id),
            "share_enabled": bool(self.share_enabled),
            "share_note": self.share_note or "",
            "created_at": iso(self.created_at),
        }
        if include_secrets:
            d["share_token"] = self.share_token
            d["has_location_token"] = bool(self.ghl_location_token)
        return d


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

    cloudinary_public_id = Column(String(400), nullable=True)
    cloudinary_url = Column(Text, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    bytes = Column(Integer, nullable=True)

    ghl_status = Column(String(20), nullable=False, default="pending")  # pending|sent|skipped|error
    ghl_file_id = Column(String(200), nullable=True)
    ghl_url = Column(Text, nullable=True)
    ghl_error = Column(Text, nullable=True)

    saved_by = Column(String(200), nullable=True)   # hub user email, or "client"
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)

    client = relationship("PickerClient", back_populates="images")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "client_id": self.client_id,
            "provider": self.provider,
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
            "public_id": self.cloudinary_public_id,
            "width": self.width,
            "height": self.height,
            "bytes": self.bytes,
            "ghl_status": self.ghl_status,
            "ghl_file_id": self.ghl_file_id,
            "ghl_url": self.ghl_url,
            "ghl_error": self.ghl_error,
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
