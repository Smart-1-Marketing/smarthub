"""Persistence for the Reports module: the fact table and what sits beside it.

Six tables, and a database of their own.

* ``AdPerfDaily`` -- one row per platform, account, campaign and day. This is
  the fact table every report reads; the syncs (Windsor, a native API, a CSV
  upload) all write here through ``upsert_rows()``, so a second pull of the
  same day replaces the first rather than doubling it.
* ``CampaignMap`` -- which Hub client (and product) a campaign belongs to.
  Kept apart from the fact rows because a campaign is mapped once and synced
  every night; a mapping written into the fact row would be lost on the next
  upsert.
* ``BudgetLine`` -- what was sold, by client, product and month, so pacing
  has something to pace against.
* ``PlatformMarkup`` -- how a platform's raw spend becomes a billed figure:
  a markup percentage or a fixed CPM, never both.
* ``ReportsSync`` -- the normalize job's watermark per platform.
* ``ReportLink`` -- a client's live dashboard link and what it may show.
* ``PacingSnapshot`` -- one row per budget line per hourly pacing run; the
  pacing board and the cost report read the latest run, never a live sum.

## The database binding

These tables live in their **own** Postgres. The Hub database sits on a 1 GB
disk, and a fact table that grows by one row per campaign per day across
thirteen platforms is the first thing here that would fill it. So the engine
is bound to ``REPORTS_DATABASE_URL`` first -- the dedicated smart1-reports-db
instance -- and only falls back to ``DATABASE_URL`` (one database, for a
deploy that has not split them yet) and then to a SQLite file on the data
disk (a local run or a test, which needs nothing set). ``database_url()`` is
the one reader of that order.

Same conventions as ``modules/scans`` and ``modules/ads_builder``: the engine
comes from ``hub/extensions`` so there is one pool per database rather than
one per module, the boot DDL is captured as ``DB_BOOT_ERROR`` rather than
raised, and SQLite locally / Postgres on Render are the same code.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import secrets

from sqlalchemy import (JSON, BigInteger, Boolean, Column, Date, DateTime,
                        Integer, Numeric, String, Text, func)
from sqlalchemy.orm import declarative_base

from hub.extensions import (BootProbe, create_all_metadata, engine_for,
                            normalise_url, session_factory)

log = logging.getLogger(__name__)

# Every platform a fact row may name. A row naming anything else is refused
# at the store rather than filed under a spelling no report will ever ask
# for -- "Google" and "google" and "google_ads" would be three platforms with
# one third of the spend each. "suite" is Smart 1 Suite's own campaigns.
PLATFORMS = ("ttd", "google", "bing", "linkedin", "tiktok", "audiogo",
             "stackadapt", "meta", "groundtruth", "x", "amazon_sa",
             "amazon_dsp", "suite")

# What each platform is called on a screen. The key is what the syncs write.
PLATFORM_LABELS = {
    "ttd": "The Trade Desk", "google": "Google Ads", "bing": "Microsoft Ads",
    "linkedin": "LinkedIn", "tiktok": "TikTok", "audiogo": "AudioGO",
    "stackadapt": "StackAdapt", "meta": "Meta", "groundtruth": "GroundTruth",
    "x": "X", "amazon_sa": "Amazon Sponsored Ads", "amazon_dsp": "Amazon DSP",
    "suite": "Smart 1 Suite",
}

# Where a fact row came from. A CSV somebody uploaded and a nightly API pull
# are the same numbers with different trust, and a report has to be able to
# say which it is reading.
SOURCES = ("windsor", "native", "csv")

# The shape a campaign name needs for the auto-mapper to file it without
# anybody opening the unmapped queue. Shown beside every unmapped campaign as
# the rename hint, so the person who can rename it in the platform sees the
# exact form.
RENAME_SHAPE = "S1M | <ClientKey> | <Product> | <anything>"


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def database_url() -> str:
    """The reporting database, in order of preference.

    ``REPORTS_DATABASE_URL`` is the dedicated instance and the answer on
    Render. ``DATABASE_URL`` is one database for everything, which is how a
    deploy that has not split them yet still works. With neither set a
    SQLite file on the data disk is used, so a test or a local run needs
    nothing -- ``hub/extensions.database_url()``'s rule, and the same
    ``data_root()`` so the file lands where the backup sweep looks.
    """
    for name in ("REPORTS_DATABASE_URL", "DATABASE_URL"):
        url = (os.environ.get(name) or "").strip()
        if url:
            return normalise_url(url)
    try:
        from hub import jsonstore
        base = jsonstore.data_root()
    except Exception:  # noqa: BLE001 - a database URL must always resolve
        base = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "data")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        # A root that cannot be created is reported by the engine on first
        # use, in words; a database URL must still resolve here.
        pass
    return "sqlite:///" + os.path.join(base, "reports.sqlite3")


def binding() -> str:
    """Which of the three the engine is bound to, for the landing page and
    the status row: "reports" (its own database), "hub" (sharing the Hub's)
    or "sqlite" (a local file)."""
    if (os.environ.get("REPORTS_DATABASE_URL") or "").strip():
        return "reports"
    if (os.environ.get("DATABASE_URL") or "").strip():
        return "hub"
    return "sqlite"


DB_URL = database_url()
engine = engine_for(DB_URL)
SessionLocal = session_factory(DB_URL)
Base = declarative_base()


def is_postgres() -> bool:
    return engine.dialect.name.startswith("postgres")


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt) -> str | None:
    """UTC timestamps rendered with an explicit offset.

    The column is naive on SQLite, so a bare .isoformat() reads as *local*
    time in a browser and shows a sync hours in the future. Stamp the zone
    the value was stored in -- the rule ``modules/scans/app._iso()`` gives.
    """
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class AdPerfDaily(Base):
    """One campaign, one day, on one platform. The fact table."""
    __tablename__ = "reports_ad_perf_daily"

    platform = Column(String(20), primary_key=True)
    account_id = Column(String(80), primary_key=True)
    campaign_id = Column(String(120), primary_key=True)
    date = Column(Date, primary_key=True)
    campaign_name = Column(String(400), default="")
    spend = Column(Numeric(12, 2), default=0)
    impressions = Column(BigInteger, default=0)
    clicks = Column(BigInteger, default=0)
    conversions = Column(Numeric(12, 2), default=0)
    video_views = Column(BigInteger, nullable=True)
    completes = Column(BigInteger, nullable=True)
    leads = Column(BigInteger, nullable=True)
    # Whatever the platform reported that no column names: a JSON blob on the
    # row rather than a column per platform, the ``modules/ads_builder``
    # pattern, because create_all() never adds a column to a live table.
    extras_json = Column(JSON, default=dict)
    source = Column(String(20), default="native")
    synced_at = Column(DateTime(timezone=True), default=now)

    @property
    def extras(self) -> dict:
        return self.extras_json if isinstance(self.extras_json, dict) else {}


class CampaignMap(Base):
    """Which Hub client a campaign belongs to."""
    __tablename__ = "reports_campaign_map"

    platform = Column(String(20), primary_key=True)
    account_id = Column(String(80), primary_key=True)
    campaign_id = Column(String(120), primary_key=True)
    # The Hub-wide key from hub/client_key.py (d:example.com or n:slug) --
    # the join every other module's record uses -- and the name beside it,
    # because the activity log and Client 360 read a client by name.
    client = Column(String(200), nullable=False, index=True)
    client_name = Column(String(300), default="")
    product = Column(String(120), nullable=True)
    # What the campaign is called on the client's page. Defaults to the
    # platform's campaign name with the vendor words stripped
    # (products.default_display_name); staff override it where a name still
    # leaks a vendor. A LATE column -- _LATE_COLUMNS adds it to a live table.
    display_name = Column(String(400), nullable=True)
    mapped_by = Column(String(160), default="")
    mapped_at = Column(DateTime(timezone=True), default=now)
    # Set when the auto-mapper filed it from the campaign name; empty for a
    # mapping a person made. The two are told apart on the record.
    auto_rule = Column(String(120), nullable=True)


class BudgetLine(Base):
    """What was sold: a monthly figure for one client and product, flighted."""
    __tablename__ = "reports_budget_lines"

    # Integer rather than BigInteger: SQLite only autoincrements an INTEGER
    # primary key, and a budget book will not reach two billion lines.
    id = Column(Integer, primary_key=True, autoincrement=True)
    client = Column(String(200), nullable=False, index=True)
    client_name = Column(String(300), default="")
    product = Column(String(120), nullable=False)
    platform = Column(String(20), nullable=True)
    monthly_budget = Column(Numeric(12, 2), nullable=False)
    flight_start = Column(Date, nullable=True)
    flight_end = Column(Date, nullable=True)
    notes = Column(Text, default="")
    created_by = Column(String(160), default="")
    created_at = Column(DateTime(timezone=True), default=now)
    # What the client pays for the line per month (monthly_budget stays the
    # MEDIA budget the campaigns pace against); who on staff owns it; and
    # whether it is active, paused or ended. All three are LATE columns --
    # _LATE_COLUMNS adds them to a live table -- and pacing.py and the cost
    # report read them.
    sold_amount = Column(Numeric(12, 2), nullable=True)
    owner = Column(String(160), nullable=True)
    status = Column(String(20), nullable=True)
    # Where the figure came from. A line typed on the budgets page writes
    # {"manual": true}; a later import from an insertion order writes the
    # IO it was read from, so a hand-typed number and a sold one are told
    # apart on the row.
    source_json = Column(JSON, default=lambda: {"manual": True})


BUDGET_STATUSES = ("active", "paused", "ended")


class PacingSnapshot(Base):
    """One budget line's pacing as computed by one run of ``pacing.run()``.

    The pacing board and the cost report read the LATEST run, never a live
    aggregate: a page that summed the fact table on every open would answer
    differently on two workers and at two minutes past the hour, and the
    3-day trend the alert needs is a history nothing live can supply. One
    row per line per run, every field the board prints, so a row is the
    whole answer as it stood at ``computed_at``.
    """
    __tablename__ = "reports_pacing_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    computed_at = Column(DateTime(timezone=True), default=now, index=True)
    as_of = Column(Date, nullable=False, index=True)
    line_id = Column(Integer, nullable=False, index=True)
    client = Column(String(200), nullable=False, index=True)
    client_name = Column(String(300), default="")
    product = Column(String(120), default="")
    platform = Column(String(20), nullable=True)
    platforms_json = Column(JSON, default=list)
    owner = Column(String(160), default="")
    monthly_budget = Column(Numeric(12, 2), default=0)
    sold_amount = Column(Numeric(12, 2), nullable=True)
    budget_period = Column(Numeric(12, 2), default=0)
    expected_to_date = Column(Numeric(12, 2), default=0)
    actual_to_date = Column(Numeric(12, 2), default=0)
    pace = Column(Numeric(8, 4), nullable=True)
    band = Column(String(20), default="on")
    stalled = Column(Boolean, default=False)
    unmapped = Column(Boolean, default=False)
    projected_month_end = Column(Numeric(12, 2), nullable=True)
    daily_needed = Column(Numeric(12, 2), nullable=True)
    avg_daily_7 = Column(Numeric(12, 2), default=0)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    days_elapsed = Column(Integer, default=0)
    days_remaining = Column(Integer, default=0)
    days_in_period = Column(Integer, default=0)
    last_spend_date = Column(Date, nullable=True)
    trend_days = Column(Integer, default=1)
    alert = Column(Boolean, default=False)
    last_sync = Column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "computed_at": iso(self.computed_at),
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "line_id": self.line_id, "client": self.client, "client_name": self.client_name or "",
            "product": self.product or "", "platform": self.platform or "",
            "platforms": list(self.platforms_json or []),
            "platform_labels": [platform_label(p) for p in (self.platforms_json or [])],
            "owner": self.owner or "",
            "monthly_budget": Decimal(self.monthly_budget or 0),
            "sold_amount": Decimal(self.sold_amount) if self.sold_amount is not None else None,
            "budget_period": Decimal(self.budget_period or 0),
            "expected_to_date": Decimal(self.expected_to_date or 0),
            "actual_to_date": Decimal(self.actual_to_date or 0),
            "pace": float(self.pace) if self.pace is not None else None,
            "band": self.band or "on", "stalled": bool(self.stalled), "unmapped": bool(self.unmapped),
            "projected_month_end": Decimal(self.projected_month_end) if self.projected_month_end is not None else None,
            "daily_needed": Decimal(self.daily_needed) if self.daily_needed is not None else None,
            "avg_daily_7": Decimal(self.avg_daily_7 or 0),
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "days_elapsed": int(self.days_elapsed or 0), "days_remaining": int(self.days_remaining or 0),
            "days_in_period": int(self.days_in_period or 0),
            "last_spend_date": self.last_spend_date.isoformat() if self.last_spend_date else None,
            "trend_days": int(self.trend_days or 1), "alert": bool(self.alert),
            "last_sync": iso(self.last_sync),
        }


class PlatformMarkup(Base):
    """How a platform's raw spend is billed: a markup or a fixed CPM."""
    __tablename__ = "reports_platform_markup"

    platform = Column(String(20), primary_key=True)
    # A fraction, not a percentage: 0.1500 is 15%. The screen labels the box
    # "Markup %" and divides on the way in.
    markup = Column(Numeric(6, 4), nullable=True)
    cpm = Column(Numeric(8, 2), nullable=True)
    updated_by = Column(String(160), default="")
    updated_at = Column(DateTime(timezone=True), default=now)


class ReportsSync(Base):
    """The normalize job's watermark, one row per platform.

    What the last run of the provider normalize did for this platform: when,
    how many rows it wrote, and the error if it failed. Its own table rather
    than a column on AdPerfDaily because a platform whose sync is failing
    writes no fact rows at all, and "last synced" read off the fact table
    would then go on reporting the last GOOD run for ever -- which is the
    scheduler-panel trap hub/scheduler.py's docstring describes, one table
    down. ``error`` empty is a run that wrote; ``error`` set is the finding.

    ``source`` is which sync last wrote the platform -- ``windsor`` (the
    provider normalize) or ``native`` (the platform's own API, pulled by
    ``hub/scheduler.job_reports_native_pull``). Native wins: the normalize
    skips a platform whose watermark is native and less than a day old, so a
    provider row restating yesterday cannot overwrite the platform's own
    figure. It is a LATE column -- ``create_all()`` never adds a column to a
    live table, so ``_LATE_COLUMNS`` below adds it with ALTER TABLE on a
    database that already had this table before the column existed.
    """
    __tablename__ = "reports_sync"

    platform = Column(String(20), primary_key=True)
    last_run_at = Column(DateTime(timezone=True), default=now)
    rows = Column(Integer, default=0)
    error = Column(Text, default="")
    source = Column(String(20), default="windsor")


class ReportLink(Base):
    """A client's live dashboard link, and everything that decides what it shows.

    The token is the whole security model, the way ``ads_performance_reports``
    and the scan report work: an unguessable string, one live row per client.
    Regenerating a link **disables** the old row rather than deleting it, so an
    old token renders a short "this link has been replaced" page instead of a
    404 -- a client working from a six-month-old email should be told to ask
    for the new one, not left thinking the report is gone.

    ``show_spend`` is off by default. ``markup_json`` is the per-client
    override of ``PlatformMarkup`` -- ``{"<platform>": {"markup": 0.35} |
    {"cpm": 18.00}}`` -- and ``pricing.client_price()`` is the only reader of
    both. ``view_json`` is what the page is allowed to say: platform labels,
    hidden platforms, the headline metric per platform, a logo URL, the rep's
    name and email.
    """
    __tablename__ = "reports_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client = Column(String(200), nullable=False, index=True)
    client_name = Column(String(300), default="")
    token = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=now)
    created_by = Column(String(160), default="")
    updated_at = Column(DateTime(timezone=True), default=now)
    enabled = Column(Boolean, default=True, nullable=False)
    show_spend = Column(Boolean, default=False, nullable=False)
    markup_json = Column(JSON, default=dict)
    view_json = Column(JSON, default=dict)
    last_viewed_at = Column(DateTime(timezone=True), nullable=True)
    view_count = Column(Integer, default=0)

    @property
    def markups(self) -> dict:
        return self.markup_json if isinstance(self.markup_json, dict) else {}

    @property
    def view(self) -> dict:
        return self.view_json if isinstance(self.view_json, dict) else {}

    def as_dict(self) -> dict:
        return {
            "id": self.id, "client": self.client, "client_name": self.client_name or "",
            "token": self.token, "created_at": iso(self.created_at),
            "created_by": self.created_by or "", "updated_at": iso(self.updated_at),
            "enabled": bool(self.enabled), "show_spend": bool(self.show_spend),
            "markup_json": self.markups, "view_json": self.view,
            "last_viewed_at": iso(self.last_viewed_at),
            "view_count": int(self.view_count or 0),
        }


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------

# Columns added after their table first shipped. create_all() creates missing
# tables and never adds a column to an existing one, so each of these is
# asked for and then added -- the modules/scans arrangement -- or the live
# Postgres would be missing them with every local SQLite test green.
_LATE_COLUMNS = (
    ("reports_sync", "source", "VARCHAR(20)"),
    ("reports_campaign_map", "display_name", "VARCHAR(400)"),
    ("reports_budget_lines", "sold_amount", "NUMERIC(12, 2)"),
    ("reports_budget_lines", "owner", "VARCHAR(160)"),
    ("reports_budget_lines", "status", "VARCHAR(20)"),
)


def _add_missing_columns() -> None:
    from sqlalchemy import inspect as _inspect, text as _text
    inspector = _inspect(engine)
    for table, column, coltype in _LATE_COLUMNS:
        try:
            have = {c["name"] for c in inspector.get_columns(table)}
        except Exception:                           # noqa: BLE001 - no table yet
            continue
        if column in have:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(_text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
        except Exception:                           # noqa: BLE001 - raced by the other worker
            pass


def _create_tables() -> str:
    """The whole of this module's boot DDL, as one answer -- re-run by the
    probe when the first answer was a connection that had not woken yet.
    The late columns are inside it, or a module that recovers comes back
    missing them."""
    err = create_all_metadata(Base.metadata, DB_URL)
    if not err:
        _add_missing_columns()
    return err


_DB_PROBE = BootProbe(_create_tables, label="reports")
DB_BOOT_ERROR = _DB_PROBE.record(_create_tables())
if DB_BOOT_ERROR:
    log.error("reports: database not ready at boot: %s", DB_BOOT_ERROR)


def db_error() -> str:
    """The verdict now. A transient failure at boot is re-asked rather than
    carried for the life of the worker."""
    global DB_BOOT_ERROR
    DB_BOOT_ERROR = _DB_PROBE.error()
    return DB_BOOT_ERROR


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------

def _dec(value, places: int = 2) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    try:
        # Half-up, the way a person rounds money: Decimal's default is
        # banker's rounding, which turns 12.345% into 0.1234.
        return Decimal(str(value)).quantize(Decimal(1).scaleb(-places),
                                            rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{value!r} is not a number")


def _int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{value!r} is not a whole number")


def parse_date(value) -> date | None:
    """A date column from a date, a datetime or an ISO string; None from
    nothing. Anything else is a ValueError with the value in it."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise ValueError(f"{value!r} is not a date (YYYY-MM-DD)")


def check_platform(platform: str) -> str:
    p = str(platform or "").strip().lower()
    if p not in PLATFORMS:
        raise ValueError(f"Unknown platform {platform!r}; one of "
                         + ", ".join(PLATFORMS))
    return p


def _text(value, limit: int) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()[:limit]


# ---------------------------------------------------------------------------
# AdPerfDaily
# ---------------------------------------------------------------------------

def _fact_values(row: dict) -> dict:
    """One incoming row as the column values it becomes, validated."""
    source = str(row.get("source") or "native").strip().lower()
    if source not in SOURCES:
        raise ValueError(f"Unknown source {source!r}; one of " + ", ".join(SOURCES))
    when = parse_date(row.get("date"))
    if when is None:
        raise ValueError("A fact row needs a date")
    account_id = _text(row.get("account_id"), 80)
    campaign_id = _text(row.get("campaign_id"), 120)
    if not account_id or not campaign_id:
        raise ValueError("A fact row needs an account_id and a campaign_id")
    extras = row.get("extras_json")
    if extras is None:
        extras = row.get("extras") or {}
    if isinstance(extras, str):
        try:
            extras = json.loads(extras or "{}")
        except ValueError:
            raise ValueError("extras_json is not JSON")
    if not isinstance(extras, dict):
        raise ValueError("extras_json has to be an object")
    return {
        "platform": check_platform(row.get("platform")),
        "account_id": account_id,
        "campaign_id": campaign_id,
        "date": when,
        "campaign_name": _text(row.get("campaign_name"), 400),
        "spend": _dec(row.get("spend")),
        "impressions": _int(row.get("impressions")) or 0,
        "clicks": _int(row.get("clicks")) or 0,
        "conversions": _dec(row.get("conversions")),
        "video_views": _int(row.get("video_views")),
        "completes": _int(row.get("completes")),
        "leads": _int(row.get("leads")),
        "extras_json": extras,
        "source": source,
        "synced_at": now(),
    }


_FACT_KEY = ("platform", "account_id", "campaign_id", "date")


def upsert_rows(rows: list[dict]) -> int:
    """Write fact rows, replacing any already there for the same key.

    Idempotent by construction: a sync that runs twice for the same day
    writes the same rows, not twice the spend. ``INSERT ... ON CONFLICT DO
    UPDATE`` on Postgres, one statement per row; a select-then-merge on
    SQLite, which is a local run or a test and does not need the throughput.
    Every row is validated **before** anything is written, so a bad row in
    the middle of a batch rejects the batch rather than half of it.
    """
    values = [_fact_values(r) for r in rows]
    if not values:
        return 0
    db = SessionLocal()
    try:
        if is_postgres():
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            table = AdPerfDaily.__table__
            for v in values:
                stmt = pg_insert(table).values(**v)
                stmt = stmt.on_conflict_do_update(
                    index_elements=list(_FACT_KEY),
                    set_={k: stmt.excluded[k] for k in v if k not in _FACT_KEY})
                db.execute(stmt)
        else:
            for v in values:
                key = tuple(v[k] for k in _FACT_KEY)
                existing = db.get(AdPerfDaily, key)
                if existing is None:
                    db.add(AdPerfDaily(**v))
                else:
                    for k, val in v.items():
                        if k not in _FACT_KEY:
                            setattr(existing, k, val)
        db.commit()
        return len(values)
    finally:
        db.close()


def fact_count() -> int:
    db = SessionLocal()
    try:
        return int(db.query(func.count()).select_from(AdPerfDaily).scalar() or 0)
    finally:
        db.close()


def platform_status() -> list[dict]:
    """Per platform: when it was last synced and how many rows it holds.

    Every platform is listed, including the ones with nothing yet -- a
    platform absent from the table is the finding, and a list that only
    named the ones that had synced would hide it.
    """
    db = SessionLocal()
    try:
        rows = (db.query(AdPerfDaily.platform,
                         func.max(AdPerfDaily.synced_at),
                         func.max(AdPerfDaily.date),
                         func.count())
                  .group_by(AdPerfDaily.platform).all())
    finally:
        db.close()
    seen = {r[0]: r for r in rows}
    syncs = sync_status()
    out = []
    for p in PLATFORMS:
        r = seen.get(p)
        s = syncs.get(p) or {}
        out.append({
            "platform": p, "label": platform_label(p),
            "synced_at": iso(r[1]) if r else None,
            "latest_date": r[2].isoformat() if r and r[2] else None,
            "rows": int(r[3]) if r else 0,
            # The normalize job's own watermark, beside the fact table's: a
            # platform whose last run failed still shows its last good sync
            # above, and this column is what says the job is broken.
            "sync_run_at": s.get("last_run_at"),
            "sync_rows": s.get("rows"),
            "sync_error": s.get("error"),
            # Which sync wrote the watermark: the provider normalize or the
            # platform's own API. The index says so beside the result.
            "sync_source": s.get("source"),
        })
    return out


# ---------------------------------------------------------------------------
# ReportsSync
# ---------------------------------------------------------------------------

def record_sync(platform: str, *, rows: int = 0, error: str | None = None,
                source: str = "windsor") -> None:
    """Stamp a sync's outcome for one platform. Never raises: a watermark
    that fails must not cost the sync it describes. ``source`` says which
    sync -- the provider normalize (``windsor``) or the platform's own API
    (``native``) -- and is one of ``SOURCES``."""
    try:
        platform = check_platform(platform)
    except ValueError:
        return
    source = str(source or "windsor").strip().lower()
    if source not in SOURCES:
        source = "windsor"
    db = SessionLocal()
    try:
        row = db.get(ReportsSync, platform)
        if row is None:
            row = ReportsSync(platform=platform)
            db.add(row)
        row.last_run_at = now()
        row.rows = int(rows or 0)
        row.error = _text(error, 2000)
        row.source = source
        db.commit()
    except Exception:                  # noqa: BLE001 - a watermark is not the work
        db.rollback()
    finally:
        db.close()


def sync_status() -> dict[str, dict]:
    """The watermark per platform: {platform: {last_run_at, rows, error}}.
    Only platforms the job has run for are in it."""
    db = SessionLocal()
    try:
        return {r.platform: {"last_run_at": iso(r.last_run_at),
                             "rows": int(r.rows or 0),
                             "error": r.error or "",
                             # A row written before the column existed is
                             # the provider's: nothing else wrote one then.
                             "source": r.source or "windsor"}
                for r in db.query(ReportsSync).all()}
    except Exception:                  # noqa: BLE001 - a missing table is no watermark
        return {}
    finally:
        db.close()


def native_is_current(platform: str, hours: int = 24) -> bool:
    """Whether the platform's own API wrote this platform's watermark within
    ``hours`` -- the native-wins rule the provider normalize reads before it
    touches a platform. A pull that wrote no rows does not count, whatever
    its error says: a native pull that landed nothing has not superseded
    anything, while one that landed rows and named a refused account has."""
    try:
        platform = check_platform(platform)
    except ValueError:
        return False
    db = SessionLocal()
    try:
        row = db.get(ReportsSync, platform)
    except Exception:                  # noqa: BLE001 - a missing table is no watermark
        return False
    finally:
        db.close()
    if row is None or (row.source or "windsor") != "native" or not int(row.rows or 0):
        return False
    when = row.last_run_at
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now() - when) <= timedelta(hours=hours)


def last_synced_at() -> datetime | None:
    """When the fact table was last written, whichever platform did it --
    the "Updated ..." line on a client's page."""
    db = SessionLocal()
    try:
        return db.query(func.max(AdPerfDaily.synced_at)).scalar()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Per-client reads
# ---------------------------------------------------------------------------

def clients_with_campaigns() -> list[dict]:
    """Every client a campaign is filed under: key, name, campaigns, and
    whether a live link exists. What the staff index lists."""
    db = SessionLocal()
    try:
        rows = (db.query(CampaignMap.client, func.max(CampaignMap.client_name),
                         func.count())
                  .group_by(CampaignMap.client).all())
        links = {l.client: l.token for l in
                 db.query(ReportLink).filter(ReportLink.enabled.is_(True)).all()}
    finally:
        db.close()
    out = [{"client": c, "client_name": n or c, "campaigns": int(k),
            "token": links.get(c)} for c, n, k in rows]
    out.sort(key=lambda r: r["client_name"].lower())
    return out


def campaign_keys_for(client: str) -> list[tuple[str, str, str]]:
    db = SessionLocal()
    try:
        return [(m.platform, m.account_id, m.campaign_id) for m in
                db.query(CampaignMap).filter(CampaignMap.client == client).all()]
    finally:
        db.close()


def facts_for(client: str, start: date, end: date) -> list[dict]:
    """The fact rows of one client's mapped campaigns, in a date range
    (inclusive). Product and mapping come along, because the client page
    groups by them."""
    db = SessionLocal()
    try:
        maps = db.query(CampaignMap).filter(CampaignMap.client == client).all()
        if not maps:
            return []
        by_key = {(m.platform, m.account_id, m.campaign_id): m for m in maps}
        out = []
        for p, a in {(m.platform, m.account_id) for m in maps}:
            rows = (db.query(AdPerfDaily)
                      .filter(AdPerfDaily.platform == p, AdPerfDaily.account_id == a,
                              AdPerfDaily.date >= start, AdPerfDaily.date <= end).all())
            for r in rows:
                m = by_key.get((r.platform, r.account_id, r.campaign_id))
                if m is None:
                    continue
                out.append({
                    "platform": r.platform, "account_id": r.account_id,
                    "campaign_id": r.campaign_id, "campaign_name": r.campaign_name or "",
                    "date": r.date, "spend": Decimal(r.spend or 0),
                    "impressions": int(r.impressions or 0), "clicks": int(r.clicks or 0),
                    "conversions": Decimal(r.conversions or 0),
                    "video_views": r.video_views, "completes": r.completes,
                    "leads": r.leads, "extras": r.extras, "product": m.product or "",
                    "display_name": m.display_name or "",
                })
        return out
    finally:
        db.close()


def budget_lines_for(client: str) -> list[dict]:
    return [b for b in budget_lines(limit=5000) if b["client"] == client]


def mapped_campaigns_for(client: str) -> list[dict]:
    return [m for m in mapped_campaigns(limit=5000) if m["client"] == client]


# ---------------------------------------------------------------------------
# ReportLink
# ---------------------------------------------------------------------------

def get_link(token: str) -> ReportLink | None:
    """The row for a token, enabled or not. The caller decides what a
    disabled one renders -- the "replaced" page rather than a 404."""
    token = _text(token, 64)
    if not token:
        return None
    db = SessionLocal()
    try:
        return db.query(ReportLink).filter(ReportLink.token == token).first()
    finally:
        db.close()


def link_for_client(client: str) -> ReportLink | None:
    """The one live link for a client, or None."""
    db = SessionLocal()
    try:
        return (db.query(ReportLink)
                  .filter(ReportLink.client == client, ReportLink.enabled.is_(True))
                  .order_by(ReportLink.id.desc()).first())
    finally:
        db.close()


def create_link(client: str, *, client_name: str = "", created_by: str = "") -> ReportLink:
    """Mint a link for a client. Any live link is disabled first, so there
    is one live token per client and an old one renders the replaced page.
    The settings carry over: regenerating a link is about the token, not
    about undoing the markup somebody set."""
    client = _text(client, 200)
    if not client:
        raise ValueError("A report link needs a client")
    db = SessionLocal()
    try:
        old = (db.query(ReportLink)
                 .filter(ReportLink.client == client, ReportLink.enabled.is_(True)).all())
        carry = old[-1] if old else None
        for row in old:
            row.enabled = False
            row.updated_at = now()
        link = ReportLink(
            client=client, client_name=_text(client_name, 300) or (carry.client_name if carry else ""),
            token=secrets.token_urlsafe(24), created_by=_text(created_by, 160),
            enabled=True,
            show_spend=bool(carry.show_spend) if carry else False,
            markup_json=dict(carry.markups) if carry else {},
            view_json=dict(carry.view) if carry else {})
        db.add(link)
        db.commit()
        db.refresh(link)
        db.expunge(link)
        return link
    finally:
        db.close()


def update_link(token: str, *, show_spend: bool | None = None,
                markup_json: dict | None = None, view_json: dict | None = None) -> ReportLink:
    token = _text(token, 64)
    db = SessionLocal()
    try:
        row = db.query(ReportLink).filter(ReportLink.token == token).first()
        if row is None:
            raise ValueError("No such report link")
        if show_spend is not None:
            row.show_spend = bool(show_spend)
        if markup_json is not None:
            row.markup_json = _clean_markups(markup_json)
        if view_json is not None:
            row.view_json = _clean_view(view_json)
        row.updated_at = now()
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


def _clean_markups(raw: dict) -> dict:
    """{platform: {"markup": fraction} | {"cpm": dollars}}, validated the way
    PlatformMarkup is: one answer per platform, never both."""
    out = {}
    for p, v in (raw or {}).items():
        if not isinstance(v, dict):
            continue
        p = check_platform(p)
        has_m = v.get("markup") not in (None, "")
        has_c = v.get("cpm") not in (None, "")
        if has_m and has_c:
            raise ValueError(f"{platform_label(p)}: set a markup or a fixed CPM, not both")
        if has_m:
            m = _dec(v["markup"], 4)
            if m < 0:
                raise ValueError("A markup cannot be negative")
            out[p] = {"markup": str(m)}
        elif has_c:
            c = _dec(v["cpm"], 2)
            if c <= 0:
                raise ValueError("A fixed CPM has to be more than zero")
            out[p] = {"cpm": str(c)}
    return out


VIEW_FIELDS = ("logo_url", "rep_name", "rep_email")


def _clean_view(raw: dict) -> dict:
    out = {}
    labels = raw.get("platform_labels") or {}
    if isinstance(labels, dict):
        clean = {check_platform(p): _text(v, 80) for p, v in labels.items()
                 if _text(v, 80)}
        if clean:
            out["platform_labels"] = clean
    hidden = raw.get("hidden_platforms") or []
    if isinstance(hidden, (list, tuple)):
        clean = sorted({check_platform(p) for p in hidden if _text(p, 20)})
        if clean:
            out["hidden_platforms"] = clean
    headline = raw.get("headline_metric") or {}
    if isinstance(headline, dict):
        clean = {check_platform(p): _text(v, 40) for p, v in headline.items()
                 if _text(v, 40)}
        if clean:
            out["headline_metric"] = clean
    for f in VIEW_FIELDS:
        v = _text(raw.get(f), 300)
        if v:
            out[f] = v
    # Whether the organic section may name Google's products (GA4, Search
    # Console) rather than saying "Google search". Off unless asked for.
    if raw.get("name_products"):
        out["name_products"] = True
    return out


def note_view(token: str) -> None:
    """A client opened the page. Never raises: a counter must not cost the
    page it counts."""
    db = SessionLocal()
    try:
        row = db.query(ReportLink).filter(ReportLink.token == _text(token, 64)).first()
        if row is not None:
            row.view_count = int(row.view_count or 0) + 1
            row.last_viewed_at = now()
            db.commit()
    except Exception:                  # noqa: BLE001
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CampaignMap
# ---------------------------------------------------------------------------

def unmapped_campaigns(days: int = 30, limit: int = 200) -> list[dict]:
    """Campaigns in the fact table with no mapping, biggest recent spend first.

    Spend is summed over the last ``days`` days so a campaign that stopped a
    year ago does not sit above one running now; the campaign name is the
    latest one the platform reported, because campaigns get renamed and the
    hint beside it should describe the name somebody will see.
    """
    days = max(1, int(days))
    limit = max(1, int(limit))
    since = date.fromordinal(date.today().toordinal() - days)
    db = SessionLocal()
    try:
        mapped = {(m.platform, m.account_id, m.campaign_id)
                  for m in db.query(CampaignMap.platform, CampaignMap.account_id,
                                    CampaignMap.campaign_id).all()}
        recent = dict()
        for p, a, c, spend in (
                db.query(AdPerfDaily.platform, AdPerfDaily.account_id,
                         AdPerfDaily.campaign_id, func.sum(AdPerfDaily.spend))
                  .filter(AdPerfDaily.date >= since)
                  .group_by(AdPerfDaily.platform, AdPerfDaily.account_id,
                            AdPerfDaily.campaign_id).all()):
            recent[(p, a, c)] = Decimal(spend or 0)
        out = {}
        # Latest name per campaign: the rows come newest-first, so the first
        # one seen wins.
        for p, a, c, name, when in (
                db.query(AdPerfDaily.platform, AdPerfDaily.account_id,
                         AdPerfDaily.campaign_id, AdPerfDaily.campaign_name,
                         AdPerfDaily.date)
                  .order_by(AdPerfDaily.date.desc()).all()):
            key = (p, a, c)
            if key in mapped or key in out:
                continue
            out[key] = {
                "platform": p, "platform_label": platform_label(p),
                "account_id": a, "campaign_id": c,
                "campaign_name": name or "",
                "last_seen": when.isoformat() if when else None,
                "spend_30d": recent.get(key, Decimal(0)),
            }
    finally:
        db.close()
    rows = sorted(out.values(), key=lambda r: (-r["spend_30d"], r["platform"],
                                               r["campaign_name"]))
    return rows[:limit]


def unmapped_count() -> int:
    db = SessionLocal()
    try:
        mapped = {(m.platform, m.account_id, m.campaign_id)
                  for m in db.query(CampaignMap.platform, CampaignMap.account_id,
                                    CampaignMap.campaign_id).all()}
        seen = {(p, a, c) for p, a, c in
                db.query(AdPerfDaily.platform, AdPerfDaily.account_id,
                         AdPerfDaily.campaign_id).distinct().all()}
    finally:
        db.close()
    return len(seen - mapped)


def map_campaign(platform: str, account_id: str, campaign_id: str, *,
                 client: str, client_name: str = "", product: str | None = None,
                 mapped_by: str = "", auto_rule: str | None = None,
                 display_name: str | None = None, campaign_name: str | None = None) -> CampaignMap:
    """File a campaign under a client. Re-mapping replaces the row.

    ``display_name`` is what the client's page calls the campaign; left
    None it defaults to ``campaign_name`` (or the latest name the fact
    table holds) with the vendor words stripped. The product is matched
    against the catalog case-insensitively and kept as typed otherwise.

    The caller writes the activity row: this is the store, and the audit
    entry needs the actor and the client's name, which the route has.
    """
    from . import products as _products
    platform = check_platform(platform)
    account_id = _text(account_id, 80)
    campaign_id = _text(campaign_id, 120)
    client = _text(client, 200)
    if not (account_id and campaign_id):
        raise ValueError("A mapping needs an account_id and a campaign_id")
    if not client:
        raise ValueError("A mapping needs a client")
    db = SessionLocal()
    try:
        row = db.get(CampaignMap, (platform, account_id, campaign_id))
        if row is None:
            row = CampaignMap(platform=platform, account_id=account_id,
                              campaign_id=campaign_id)
            db.add(row)
        row.client = client
        row.client_name = _text(client_name, 300)
        row.product = _products.normalize(_text(product, 120)) or None
        if display_name is not None:
            row.display_name = _text(display_name, 400) or None
        if not row.display_name:
            name = _text(campaign_name, 400) or _latest_name(db, platform, account_id, campaign_id)
            row.display_name = _products.default_display_name(name, row.product)[:400] or None
        row.mapped_by = _text(mapped_by, 160)
        row.mapped_at = now()
        row.auto_rule = _text(auto_rule, 120) or None
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def _latest_name(db, platform: str, account_id: str, campaign_id: str) -> str:
    """The newest campaign name the fact table holds for a key, or ""."""
    row = (db.query(AdPerfDaily.campaign_name)
             .filter(AdPerfDaily.platform == platform, AdPerfDaily.account_id == account_id,
                     AdPerfDaily.campaign_id == campaign_id)
             .order_by(AdPerfDaily.date.desc()).first())
    return (row[0] or "") if row else ""


def set_display(platform: str, account_id: str, campaign_id: str, *,
                display_name: str | None = None, product: str | None = None) -> CampaignMap | None:
    """Staff override of what a mapped campaign is called on the client's
    page, and of its product. An empty display name goes back to the
    default; a product is normalized against the catalog. None when the
    campaign is not mapped."""
    from . import products as _products
    platform = check_platform(platform)
    db = SessionLocal()
    try:
        row = db.get(CampaignMap, (platform, _text(account_id, 80), _text(campaign_id, 120)))
        if row is None:
            return None
        if product is not None:
            row.product = _products.normalize(_text(product, 120)) or None
        if display_name is not None:
            row.display_name = _text(display_name, 400) or None
        if not row.display_name:
            row.display_name = _products.default_display_name(
                _latest_name(db, platform, row.account_id, row.campaign_id), row.product)[:400] or None
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


def mapped_campaigns(limit: int = 500) -> list[dict]:
    from . import products as _products
    db = SessionLocal()
    try:
        rows = (db.query(CampaignMap).order_by(CampaignMap.mapped_at.desc())
                  .limit(max(1, int(limit))).all())
        out = []
        for m in rows:
            name = _latest_name(db, m.platform, m.account_id, m.campaign_id)
            out.append({
                "platform": m.platform, "platform_label": platform_label(m.platform),
                "account_id": m.account_id, "campaign_id": m.campaign_id,
                "campaign_name": name,
                "client": m.client, "client_name": m.client_name or "",
                "product": m.product or "", "product_set": bool(m.product),
                "display_name": m.display_name or _products.default_display_name(name, m.product),
                "mapped_by": m.mapped_by or "",
                "mapped_at": iso(m.mapped_at), "auto_rule": m.auto_rule or "",
            })
        return out
    finally:
        db.close()


# ---------------------------------------------------------------------------
# BudgetLine
# ---------------------------------------------------------------------------

def check_status(status) -> str:
    st = str(status or "active").strip().lower() or "active"
    if st not in BUDGET_STATUSES:
        raise ValueError(f"Unknown status {status!r}; one of " + ", ".join(BUDGET_STATUSES))
    return st


def add_budget_line(*, client: str, product: str, monthly_budget,
                    platform: str | None = None, flight_start=None,
                    flight_end=None, notes: str = "", created_by: str = "",
                    client_name: str = "", source: dict | None = None,
                    sold_amount=None, owner: str = "", status: str = "active") -> BudgetLine:
    client = _text(client, 200)
    product = _text(product, 120)
    if not client:
        raise ValueError("A budget line needs a client")
    if not product:
        raise ValueError("A budget line needs a product")
    amount = _dec(monthly_budget)
    if amount <= 0:
        raise ValueError("The monthly budget has to be more than zero")
    sold = _dec(sold_amount) if sold_amount not in (None, "") else None
    if sold is not None and sold < 0:
        raise ValueError("The sold amount cannot be negative")
    st = check_status(status)
    start = parse_date(flight_start)
    end = parse_date(flight_end)
    if start and end and end < start:
        raise ValueError("The flight ends before it starts")
    plat = check_platform(platform) if _text(platform, 20) else None
    db = SessionLocal()
    try:
        row = BudgetLine(
            client=client, client_name=_text(client_name, 300), product=product,
            platform=plat, monthly_budget=amount, flight_start=start,
            flight_end=end, notes=_text(notes, 2000), created_by=_text(created_by, 160),
            sold_amount=sold, owner=_text(owner, 160) or None, status=st,
            source_json=source if source is not None else {"manual": True})
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def budget_lines(limit: int = 500) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (db.query(BudgetLine)
                  .order_by(BudgetLine.created_at.desc(), BudgetLine.id.desc())
                  .limit(max(1, int(limit))).all())
        out = []
        for b in rows:
            source = b.source_json if isinstance(b.source_json, dict) else {}
            out.append({
                "id": b.id, "client": b.client, "client_name": b.client_name or "",
                "product": b.product, "platform": b.platform or "",
                "platform_label": platform_label(b.platform) if b.platform else "",
                "monthly_budget": b.monthly_budget,
                "flight_start": b.flight_start.isoformat() if b.flight_start else "",
                "flight_end": b.flight_end.isoformat() if b.flight_end else "",
                "notes": b.notes or "", "created_by": b.created_by or "",
                "created_at": iso(b.created_at),
                "manual": bool(source.get("manual")),
                "sold_amount": b.sold_amount,
                "owner": b.owner or "",
                # A row written before the column existed is active: nothing
                # else could have written a status then.
                "status": b.status or "active",
            })
        return out
    finally:
        db.close()


def update_budget_line(line_id: int, *, status: str | None = None, owner: str | None = None,
                       sold_amount=None, clear_sold: bool = False) -> BudgetLine | None:
    """Staff edit of the three late columns. None when the line is gone."""
    db = SessionLocal()
    try:
        row = db.get(BudgetLine, int(line_id))
        if row is None:
            return None
        if status is not None:
            row.status = check_status(status)
        if owner is not None:
            row.owner = _text(owner, 160) or None
        if clear_sold:
            row.sold_amount = None
        elif sold_amount not in (None, ""):
            sold = _dec(sold_amount)
            if sold < 0:
                raise ValueError("The sold amount cannot be negative")
            row.sold_amount = sold
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


# ---------------------------------------------------------------------------
# PacingSnapshot
# ---------------------------------------------------------------------------

def write_snapshots(rows: list[dict], computed_at: datetime | None = None) -> int:
    """One run's rows, all stamped with one computed_at so the latest run
    is one timestamp. Returns the count written."""
    stamp = computed_at or now()
    db = SessionLocal()
    try:
        for r in rows:
            db.add(PacingSnapshot(computed_at=stamp, **{
                k: v for k, v in r.items() if k in PacingSnapshot.__table__.columns.keys()
                and k not in ("id", "computed_at")}))
        db.commit()
        return len(rows)
    finally:
        db.close()


def latest_run_at() -> datetime | None:
    db = SessionLocal()
    try:
        return db.query(func.max(PacingSnapshot.computed_at)).scalar()
    except Exception:                  # noqa: BLE001 - no table yet
        return None
    finally:
        db.close()


def latest_snapshots() -> list[dict]:
    """Every line's row from the newest run -- the board's whole answer."""
    db = SessionLocal()
    try:
        stamp = db.query(func.max(PacingSnapshot.computed_at)).scalar()
        if stamp is None:
            return []
        rows = (db.query(PacingSnapshot).filter(PacingSnapshot.computed_at == stamp)
                  .order_by(PacingSnapshot.client_name, PacingSnapshot.product).all())
        return [r.as_dict() for r in rows]
    except Exception:                  # noqa: BLE001 - no table yet
        return []
    finally:
        db.close()


def band_history(line_id: int, days: int = 7) -> list[tuple[date, str]]:
    """(as_of, band) for the newest snapshot of each of the last ``days``
    distinct days a line was computed on, newest first -- what the 3-day
    trend reads."""
    db = SessionLocal()
    try:
        rows = (db.query(PacingSnapshot.as_of, PacingSnapshot.band, PacingSnapshot.computed_at)
                  .filter(PacingSnapshot.line_id == int(line_id))
                  .order_by(PacingSnapshot.as_of.desc(), PacingSnapshot.computed_at.desc()).all())
    except Exception:                  # noqa: BLE001
        return []
    finally:
        db.close()
    out, seen = [], set()
    for as_of, band, _at in rows:
        if as_of in seen:
            continue
        seen.add(as_of)
        out.append((as_of, band or "on"))
        if len(out) >= days:
            break
    return out


def prune_snapshots(keep_days: int = 120) -> int:
    """Rows older than ``keep_days``: hourly snapshots across the book
    would otherwise be the second thing to fill the disk."""
    cutoff = now() - timedelta(days=int(keep_days))
    db = SessionLocal()
    try:
        n = db.query(PacingSnapshot).filter(PacingSnapshot.computed_at < cutoff).delete()
        db.commit()
        return int(n or 0)
    except Exception:                  # noqa: BLE001
        db.rollback()
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# PlatformMarkup
# ---------------------------------------------------------------------------

def set_markup(platform: str, *, markup=None, cpm=None, updated_by: str = "") -> PlatformMarkup:
    """Exactly one of ``markup`` (a fraction, 0.15 for 15%) or ``cpm``.

    Both set is refused here, not only on the form: a row carrying both is
    two answers to "what do we bill", and whichever the report happened to
    read first would be the one the client was charged.
    """
    platform = check_platform(platform)
    has_markup = markup is not None and str(markup).strip() != ""
    has_cpm = cpm is not None and str(cpm).strip() != ""
    if has_markup and has_cpm:
        raise ValueError(f"{platform_label(platform)}: set a markup or a fixed "
                         "CPM, not both")
    if not has_markup and not has_cpm:
        raise ValueError(f"{platform_label(platform)}: set a markup or a fixed CPM")
    m = _dec(markup, 4) if has_markup else None
    c = _dec(cpm, 2) if has_cpm else None
    if m is not None and m < 0:
        raise ValueError("A markup cannot be negative")
    if c is not None and c <= 0:
        raise ValueError("A fixed CPM has to be more than zero")
    db = SessionLocal()
    try:
        row = db.get(PlatformMarkup, platform)
        if row is None:
            row = PlatformMarkup(platform=platform)
            db.add(row)
        row.markup = m
        row.cpm = c
        row.updated_by = _text(updated_by, 160)
        row.updated_at = now()
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def markup_from_percent(pct) -> Decimal:
    """The fraction the column stores, from the percentage the screen asks
    for: "15" and "15.5" become 0.1500 and 0.1550."""
    return (_dec(pct, 4) / 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def clear_markup(platform: str) -> bool:
    platform = check_platform(platform)
    db = SessionLocal()
    try:
        row = db.get(PlatformMarkup, platform)
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True
    finally:
        db.close()


def markups() -> list[dict]:
    """One row per platform, set or not, so the screen is the whole list."""
    db = SessionLocal()
    try:
        have = {r.platform: r for r in db.query(PlatformMarkup).all()}
        out = []
        for p in PLATFORMS:
            r = have.get(p)
            out.append({
                "platform": p, "label": platform_label(p),
                "markup": r.markup if r else None,
                # The screen speaks in percent; the column is a fraction.
                "markup_pct": (r.markup * 100) if r and r.markup is not None else None,
                "cpm": r.cpm if r else None,
                "updated_by": r.updated_by if r else "",
                "updated_at": iso(r.updated_at) if r else None,
            })
        return out
    finally:
        db.close()
