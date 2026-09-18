"""CamHub's tables, on the shared Hub engine, all prefixed `camhub_`.

All of it in Postgres and none of it on the /var/data disk: a new module must
not reintroduce the dependency the rest of the Hub is being moved off. The
spec names `cam_pages`, `cam_sources`, `conditions_cache` and
`scrape_targets`; every other module here prefixes its tables (`ads_`, `cb_`,
`image_picker_`) because `docs/claude/57` records what unprefixed names cost
SmartForecast, so these carry the module name. A scrape target is a source
row whose adapter is `scrape` -- it has a cadence, a last success and a last
error like any other feed, which is exactly the spec's point about it.

Booted the way `modules/image_picker/models.py` is: advisory-locked DDL
through `create_all_metadata`, the verdict held by a `BootProbe` so a
database that was briefly unreachable at deploy does not take the cam page
offline for the life of the worker.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import declarative_base

from hub.extensions import (BootProbe, create_all_metadata, session_factory,
                            shared_engine)

log = logging.getLogger("hub")
Base = declarative_base()

_Session = None
_PROBE = None
DB_BOOT_ERROR: str | None = None


def _now():
    return datetime.now(timezone.utc)


class CamPage(Base):
    __tablename__ = "camhub_pages"
    id = Column(Integer, primary_key=True)
    slug = Column(String(80), unique=True, nullable=False)
    title = Column(String(200), nullable=False)
    client_name = Column(String(200))
    business_name = Column(String(200))
    location_name = Column(String(200))
    address = Column(String(300))
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    timezone = Column(String(60), default="America/New_York")
    location_type = Column(String(40), default="inland_lake")
    cam_embed_url = Column(String(600))
    cam_embed_type = Column(String(20), default="youtube")
    cam_caption = Column(String(300))
    seo_html = Column(Text)
    config = Column(Text)            # JSON: theme, business, lake, links, house ads
    status = Column(String(20), default="live")
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class CamSource(Base):
    __tablename__ = "camhub_sources"
    __table_args__ = (UniqueConstraint("page_id", "key", name="uq_camhub_source_key"),)
    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey("camhub_pages.id"), nullable=False, index=True)
    key = Column(String(40), nullable=False)
    adapter = Column(String(20), nullable=False)
    label = Column(String(200))
    config = Column(Text)            # JSON: the gauge id, the beach id, the grid
    cadence_minutes = Column(Integer, default=60)
    tolerance_minutes = Column(Integer, default=90)
    enabled = Column(Boolean, default=True)
    last_attempt = Column(DateTime(timezone=True))
    last_success = Column(DateTime(timezone=True))
    last_error = Column(Text)
    error_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=_now)


class ConditionsCache(Base):
    __tablename__ = "camhub_conditions_cache"
    __table_args__ = (UniqueConstraint("page_id", "key", name="uq_camhub_cache_key"),)
    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey("camhub_pages.id"), nullable=False, index=True)
    key = Column(String(40), nullable=False)
    payload = Column(Text)           # the last GOOD payload, JSON
    fetched_at = Column(DateTime(timezone=True))
    status = Column(String(20), default="ok")   # ok | error | seeded
    error = Column(Text)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class Sponsor(Base):
    """The advertiser as a business entity. Separate from the placement so a
    sponsor can hold the presenting slot in summer and a supporting tile in
    winter, renew without re-entering creative, and run different creative
    on different cam pages."""
    __tablename__ = "camhub_sponsors"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    category = Column(String(80))         # exclusivity is judged on this
    contact_name = Column(String(120))
    email = Column(String(200))
    phone = Column(String(60))
    website = Column(String(300))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class Placement(Base):
    """The sellable unit: a sponsor (or the house) in a position on a page,
    with its creative and its flight. Status is what a person set -- draft,
    active, paused -- and what the page shows is derived from the flight
    dates on read (scheduled / live / ended), so a flight that ends reverts
    its slot without anybody remembering to."""
    __tablename__ = "camhub_placements"
    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey("camhub_pages.id"), nullable=False, index=True)
    sponsor_id = Column(Integer, ForeignKey("camhub_sponsors.id"), index=True)
    position = Column(String(20), nullable=False, default="supporting")  # presenting | supporting
    is_house = Column(Boolean, default=False)
    status = Column(String(20), default="draft")                         # draft | active | paused
    animation = Column(String(20), default="static")  # lower_third | wipe | crossfade | shimmer | static
    name = Column(String(120))            # the name shown; defaults to the sponsor's
    headline = Column(String(120))
    body = Column(Text)
    cta_label = Column(String(60))
    url = Column(String(600))
    logo_url = Column(String(600))
    image_url = Column(String(600))
    alt_text = Column(String(200))
    tagline_2 = Column(String(120))       # the rotating-tagline treatment's extra lines
    tagline_3 = Column(String(120))
    start_date = Column(String(10))       # ISO date, or empty for open
    end_date = Column(String(10))
    weight = Column(Integer, default=1)
    sort_order = Column(Integer, default=0)
    updated_by = Column(String(120))
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class Event(Base):
    """One raw impression, click or pageview. Written in batches from the
    page, read only by the rollup. The IP is hashed with the Hub secret and
    truncated; the raw address is never stored, because there is no reason
    to hold it and it creates an obligation nobody wants. A filtered row is
    kept with its reason rather than deleted, so a sponsor who asks what was
    excluded can be shown."""
    __tablename__ = "camhub_events"
    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey("camhub_pages.id"), nullable=False, index=True)
    placement_id = Column(Integer, index=True)       # null for a pageview
    sponsor_id = Column(Integer)
    kind = Column(String(16), nullable=False)        # pageview | impression | click
    at = Column(DateTime(timezone=True), default=_now, index=True)
    day = Column(String(10), index=True)             # ISO date, in the page's timezone
    position = Column(Integer)                       # 0 presenting, 1-4 supporting
    session_hash = Column(String(32), index=True)
    ip_hash = Column(String(24))
    device = Column(String(12))                      # desktop | mobile | tablet
    referrer_host = Column(String(120))
    user_agent = Column(String(200))
    scrolled = Column(Boolean)
    ms_on_page = Column(Integer)
    filtered = Column(Boolean, default=False)
    filter_reason = Column(String(40))


class DailyStat(Base):
    """One row per placement per day, plus one per page per day with the
    placement blank for the page's own totals. Reports read this, never
    the raw table, so reporting stays instant as the raw table grows; raw
    events are kept ninety days and these for ever."""
    __tablename__ = "camhub_daily_stats"
    __table_args__ = (UniqueConstraint("page_id", "placement_id", "day", name="uq_camhub_daily"),)
    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey("camhub_pages.id"), nullable=False, index=True)
    placement_id = Column(Integer, index=True)
    sponsor_id = Column(Integer)
    day = Column(String(10), nullable=False, index=True)
    pageviews = Column(Integer, default=0)
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    unique_sessions = Column(Integer, default=0)
    filtered = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


def _create_tables() -> str:
    return create_all_metadata(Base.metadata)


def init_db() -> str:
    """Create the engine and tables; returns the boot error text or ""."""
    global _Session, _PROBE, DB_BOOT_ERROR
    try:
        shared_engine()
        _Session = session_factory(scoped=True)
        if _PROBE is None:
            _PROBE = BootProbe(_create_tables, label="camhub")
        DB_BOOT_ERROR = _PROBE.record(_create_tables()) or None
    except Exception as exc:  # noqa: BLE001 -- never block boot
        DB_BOOT_ERROR = f"{type(exc).__name__}: {exc}"
    return DB_BOOT_ERROR or ""


def boot_error() -> str:
    if _PROBE is None:
        return DB_BOOT_ERROR or "camhub: database not initialized"
    return _PROBE.error()


def session():
    if _Session is None:
        init_db()
    err = boot_error()
    if err:
        raise RuntimeError(f"camhub database unavailable: {err}")
    return _Session()
