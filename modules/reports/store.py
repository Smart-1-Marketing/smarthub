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
                        Integer, Numeric, String, Text, and_, func, or_)
from sqlalchemy.orm import declarative_base

from hub.extensions import (BootProbe, create_all_metadata, engine_for,
                            normalise_url, session_factory)

log = logging.getLogger(__name__)

# Every platform a fact row may name. A row naming anything else is refused
# at the store rather than filed under a spelling no report will ever ask
# for -- "Google" and "google" and "google_ads" would be three platforms with
# one third of the spend each. "suite" is Smart 1 Suite's own campaigns;
# "callrail" is call tracking -- outcomes (phone calls by source), never
# delivery, and read the way suite rows are (docs/claude/79).
PLATFORMS = ("ttd", "google", "bing", "linkedin", "tiktok", "audiogo",
             "stackadapt", "meta", "groundtruth", "x", "amazon_sa",
             "amazon_dsp", "suite", "callrail")

# What each platform is called on a screen. The key is what the syncs write.
PLATFORM_LABELS = {
    "ttd": "The Trade Desk", "google": "Google Ads", "bing": "Microsoft Ads",
    "linkedin": "LinkedIn", "tiktok": "TikTok", "audiogo": "AudioGO",
    "stackadapt": "StackAdapt", "meta": "Meta", "groundtruth": "GroundTruth",
    "x": "X", "amazon_sa": "Amazon Sponsored Ads", "amazon_dsp": "Amazon DSP",
    "suite": "Smart 1 Suite", "callrail": "CallRail",
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


# Platforms whose rows are OUTCOMES rather than delivery: no spend, no
# impressions, no clicks, and never a bar beside the media products on a
# client's page, a table row, an investment line or a platform column on
# the cost report. Suite carries leads and bookings; CallRail carries phone
# calls by source. Each draws its own tile and nothing else.
OUTCOME_PLATFORMS = ("suite", "callrail")


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
    # Who confirmed the mapping, and when. A mapping a person made is
    # confirmed by the making; one the auto-mapper filed from the campaign
    # name is a PROPOSAL until somebody presses Confirm on it, and until then
    # facts_for() -- the one reader every client-facing figure goes through
    # -- does not return its rows. A name is a person's typing in somebody
    # else's platform, and a typo there files one client's spend under
    # another with every screen reading as working. Both LATE columns:
    # _LATE_COLUMNS adds them to a live table.
    confirmed_by = Column(String(160), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)


# What mapped_by carries on a row the auto-mapper wrote. automap.py reads it
# from here rather than the other way round -- the store cannot import the
# automap -- so the two cannot disagree about which rows are proposals.
AUTO_MAPPED_BY = "auto"


class MapRefusal(Base):
    """An auto-mapping a person refused, so the next run does not re-file it.

    The auto-mapper considers every campaign with no CampaignMap row, and
    refusing a proposal deletes the row -- so without this, a refused
    campaign would be filed again under the same client on the next hourly
    sync, and Not theirs would be a button that undoes itself. The refusal
    is keyed on the campaign AND remembers the name it was refused under:
    a campaign renamed since is a new decision, and the auto-mapper reads
    it again.
    """
    __tablename__ = "reports_map_refusals"

    platform = Column(String(20), primary_key=True)
    account_id = Column(String(80), primary_key=True)
    campaign_id = Column(String(120), primary_key=True)
    campaign_name = Column(String(400), default="")
    client = Column(String(200), default="")
    client_name = Column(String(300), default="")
    rule = Column(String(120), nullable=True)
    refused_by = Column(String(160), default="")
    refused_at = Column(DateTime(timezone=True), default=now)


class CampaignAlias(Base):
    """A name the campaigns call a client that the registry does not.

    Learned from people, never typed: when somebody maps, confirms or moves
    a campaign, the distinctive words of its name that are not the client's
    own name and not ad-ops noise (``automap.alias_phrase``) are filed here
    as a name for that client -- "blw" for Buckeye Lake Winery, "nxt" for
    Next Level Auto. ``count`` is how many campaigns taught it; the
    auto-mapper files on an alias only once it has been taught twice, and
    an alias taught for two clients is a question, not an answer (the
    suggester shows both as leads and files neither). Refusing a filing
    that rested on an alias forgets the alias for that client, and the
    unmapped queue lists every alias with a Forget button.
    """
    __tablename__ = "reports_campaign_aliases"

    alias = Column(String(200), primary_key=True)
    client = Column(String(200), primary_key=True)
    client_name = Column(String(300), default="")
    count = Column(Integer, default=1)
    learned_from = Column(String(400), default="")
    learned_by = Column(String(160), default="")
    learned_at = Column(DateTime(timezone=True), default=now)


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


class Quarantine(Base):
    """A fact row a sync proposed that cannot be true, or almost certainly
    is not, held apart from the fact table for a person to decide on.

    Keyed on the fact key, so an hourly re-sync of the same impossible row
    updates one entry (``times`` counts how often it has been proposed)
    rather than filing a copy per hour. ``row_json`` is the row exactly as
    it would have been written, so Accept writes that and not a re-read;
    ``fingerprint`` is a digest of its figures, because a decision is about
    the row AS IT WAS -- a different figure arriving later under the same
    key is a new proposal, whatever was decided about the old one.
    ``modules/reports/quarantine.py`` holds the rules and the arithmetic.
    """
    __tablename__ = "reports_quarantine"

    platform = Column(String(20), primary_key=True)
    account_id = Column(String(80), primary_key=True)
    campaign_id = Column(String(120), primary_key=True)
    date = Column(Date, primary_key=True)
    rule = Column(String(40), nullable=False)
    reason = Column(Text, default="")
    row_json = Column(JSON, default=dict)
    fingerprint = Column(String(40), nullable=False)
    source = Column(String(20), default="")
    status = Column(String(20), default="held", index=True)
    times = Column(Integer, default=1)
    seen_at = Column(DateTime(timezone=True), default=now)
    last_seen_at = Column(DateTime(timezone=True), default=now)
    decided_by = Column(String(160), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text, default="")


QUARANTINE_STATUSES = ("held", "accepted", "discarded", "superseded")


class Reconcile(Base):
    """One platform's month, our fact table's total beside the platform's
    own, as the nightly reconcile last measured it.

    The fact table is campaign-days summed; nothing else in the module can
    say whether that sum is the month the platform would invoice. This row
    is that comparison, kept per platform per month so the page can show
    the previous month closing as the platform restates it. ``independent``
    says whether ``theirs`` came from a different aggregation the platform
    computed (Google's customer-level query) or from the same feed re-read
    whole (a provider table summed), because the two catch different
    mistakes and only the first can catch a systematic one.
    """
    __tablename__ = "reports_reconcile"

    platform = Column(String(20), primary_key=True)
    month = Column(String(7), primary_key=True)
    state = Column(String(20), nullable=False)
    ours = Column(Numeric(14, 2), nullable=True)
    theirs = Column(Numeric(14, 2), nullable=True)
    drift_pct = Column(Numeric(8, 2), nullable=True)
    ours_impressions = Column(BigInteger, nullable=True)
    theirs_impressions = Column(BigInteger, nullable=True)
    held = Column(Integer, default=0)
    independent = Column(Boolean, nullable=True)
    source_label = Column(String(200), default="")
    reason = Column(Text, default="")
    through = Column(Date, nullable=True)
    computed_at = Column(DateTime(timezone=True), default=now)


class ProviderConfirmation(Base):
    """A person's confirmation that a platform's provider column map reads
    the right columns -- taken against a sample raw row on
    ``/reports/provider-check`` -- keyed on a fingerprint of the map as it
    stood. The normalize does not read a platform's raw table until one is
    here, because the map's column names are guesses until the first sync
    lands, and a guess that happens to match a column of the wrong meaning
    files the wrong number under the right name on every report. A change
    to the map (a column, the spend divisor) changes the fingerprint and so
    retires the confirmation: the row stays, says who confirmed what, and
    reads as superseded rather than as a confirmation of something nobody
    has looked at."""
    __tablename__ = "reports_provider_confirmations"

    platform = Column(String(20), primary_key=True)
    fingerprint = Column(String(40), nullable=False)
    table_name = Column(String(120), default="")
    confirmed_by = Column(String(160), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), default=now)


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
    # The executive summary a staff member GENERATED, READ and SAVED for one
    # month: {"<YYYY-MM>": {"text", "generated_at", "generated_by", "month"}}.
    # It is written by a staff press and read by the client's page; the
    # client's page never generates one. A public route that could reach an
    # AI call is a stranger with our credit card, and a paragraph about a
    # client's results that nobody read before it was published is the other
    # half of the same problem. A LATE column -- _LATE_COLUMNS adds it to a
    # live table.
    exec_summary_json = Column(JSON, default=dict)

    @property
    def exec_summaries(self) -> dict:
        return self.exec_summary_json if isinstance(self.exec_summary_json, dict) else {}

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
            "exec_summary_json": self.exec_summaries,
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
    ("reports_campaign_map", "confirmed_by", "VARCHAR(160)"),
    ("reports_campaign_map", "confirmed_at", "TIMESTAMP WITH TIME ZONE"),
    ("reports_links", "exec_summary_json", "JSON"),
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


def upsert_rows(rows: list[dict], *, report: dict | None = None, screen: bool = True,
                today: date | None = None) -> int:
    """Write fact rows, replacing any already there for the same key.

    Idempotent by construction: a sync that runs twice for the same day
    writes the same rows, not twice the spend. ``INSERT ... ON CONFLICT DO
    UPDATE`` on Postgres, one statement per row; a select-then-merge on
    SQLite, which is a local run or a test and does not need the throughput.
    Every row is validated **before** anything is written, so a bad row in
    the middle of a batch rejects the batch rather than half of it.

    A row that is well-formed and cannot be true -- more clicks than
    impressions, a negative figure, a day that has not happened, spend
    fifty times the campaign's own trailing average -- is not written and
    not refused: it is held in ``Quarantine`` for a person, the rest of the
    batch is written, and ``report`` (a dict the caller passes) receives
    ``written``, ``quarantined`` and the reasons. This is the one door
    every writer goes through, so the screen is here and not in each pull.
    ``screen=False`` is Accept's own path back in and nothing else's.
    ``today`` is the clock the "dated after today" rule reads -- the sync's
    own day where a caller has one, the wall clock otherwise -- so a test
    that drives the clock can write the days it is about.
    """
    values = [_fact_values(r) for r in rows]
    if not values:
        if report is not None:
            report.update({"written": 0, "quarantined": 0, "reasons": {}})
        return 0
    held = []
    if screen:
        from . import quarantine as _quarantine
        values, held = _quarantine.screen(values, today=today)
    written = _write_values(values)
    if screen:
        _quarantine.settle(held, values)
    if report is not None:
        reasons: dict[str, int] = {}
        for h in held:
            reasons[h["rule"]] = reasons.get(h["rule"], 0) + 1
        report.update({"written": written, "quarantined": len(held), "reasons": reasons})
    return written


def _write_values(values: list[dict]) -> int:
    """The write itself, over rows ``_fact_values()`` has already shaped."""
    if not values:
        return 0
    db = SessionLocal()
    try:
        if is_postgres():
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            table = AdPerfDaily.__table__
            unique = list({tuple(v[k] for k in _FACT_KEY): v for v in values}.values())
            # One database round trip per batch instead of per campaign-day.
            # Preserve the former last-row-wins behavior for repeated keys.
            for offset in range(0, len(unique), 500):
                batch = unique[offset:offset + 500]
                stmt = pg_insert(table).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=list(_FACT_KEY),
                    set_={k: stmt.excluded[k] for k in batch[0] if k not in _FACT_KEY})
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


def oldest_dates(source: str | None = None) -> dict[str, date]:
    """The earliest day on file per platform -- from every source, or from
    one (``source="native"`` is how far back the platform's own API has
    been read). {} when the table cannot be read."""
    db = SessionLocal()
    try:
        q = db.query(AdPerfDaily.platform, func.min(AdPerfDaily.date))
        if source:
            q = q.filter(AdPerfDaily.source == source)
        return {r[0]: r[1] for r in q.group_by(AdPerfDaily.platform).all() if r[1]}
    except Exception:                  # noqa: BLE001 - a missing table is no history
        return {}
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

def resolve_client(name: str, key: str = "") -> tuple[str, str]:
    """The (key, display name) a row is filed under -- the one reading for
    every writer, the module's own screens and the proposal adapter alike.

    The picker hands over both. A key typed by hand, or a name with no key
    beside it, is resolved through the client registry so the row carries
    the same key every other module's record uses -- and falls back to a
    name key when the registry cannot see the client, which is a row that
    still works and is marked as name-backed by its prefix.

    It lives here rather than in app.py because app.py is the Flask app:
    ``hub/proposal_adapters/reports.py`` mints a link and budget lines from
    the Proposal Execution queue, with no request in play, and for a
    release it filed them under the client's display name because the
    resolver was only reachable through the app. Two spellings of one
    client in the store is a client whose page reads their campaigns under
    one and their budget lines under the other.
    """
    name = (name or "").strip()
    key = (key or "").strip()
    if key and name:
        return key[:200], name[:300]
    try:
        from hub import client_key as ck
        from hub import clients_registry
        hit = clients_registry.find_client(name) if name else None
        if hit:
            return (ck.client_key(hit.get("name") or name, hit.get("url") or hit.get("domain") or "")
                    or ck.name_key(name), hit.get("name") or name)
        if key:
            return key[:200], (name or ck.key_label(key))[:300]
        return ck.name_key(name), name
    except Exception:                  # noqa: BLE001 - registry unavailable
        return (key or ("n:" + name.lower().replace(" ", "-")))[:200], name[:300]


def clients_with_campaigns() -> list[dict]:
    """Every client a campaign is filed under: key, name, campaigns, and
    whether a live link exists. What the staff index lists."""
    db = SessionLocal()
    try:
        rows = (db.query(CampaignMap.client, func.max(CampaignMap.client_name),
                         func.count())
                  .group_by(CampaignMap.client).all())
        pending = {c: int(k) for c, k in
                   (db.query(CampaignMap.client, func.count())
                      .filter(CampaignMap.confirmed_at.is_(None))
                      .group_by(CampaignMap.client).all())}
        links = {l.client: l.token for l in
                 db.query(ReportLink).filter(ReportLink.enabled.is_(True)).all()}
    finally:
        db.close()
    # ``campaigns`` counts every row filed under the client and ``pending``
    # the ones still waiting for confirmation, because a client with three
    # campaigns of which three are pending has nothing on their page yet.
    out = [{"client": c, "client_name": n or c, "campaigns": int(k),
            "pending": pending.get(c, 0), "token": links.get(c)} for c, n, k in rows]
    out.sort(key=lambda r: r["client_name"].lower())
    return out


def facts_for(client: str, start: date, end: date) -> list[dict]:
    """The fact rows of one client's CONFIRMED campaigns, in a date range
    (inclusive). Product and mapping come along, because the client page
    groups by them.

    Confirmed only, with no switch to widen it: this is the one reader every
    figure about a client goes through -- the public page, its PDF and
    data.json, the pacing board, the cost report -- and a mapping the
    auto-mapper proposed from a campaign name is not yet a fact about whose
    spend it is. A pending mapping is listed, flagged, on the staff screens
    through mapped_campaigns(); it reaches no figure until a person confirms
    it."""
    db = SessionLocal()
    try:
        maps = (db.query(CampaignMap)
                  .filter(CampaignMap.client == client, CampaignMap.confirmed_at.isnot(None))
                  .all())
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


def budget_lines_for(clients, *, active_only: bool = False) -> list[dict]:
    """Every budget line filed under these client keys, filtered IN THE
    DATABASE. One key or many.

    ``budget_lines()`` takes a global limit and orders by ``created_at``
    descending, so filtering its result by client drops that client's OLDEST
    lines once the book passes the cap -- and the oldest line of the
    longest-standing client is the one somebody has been pacing for a year.
    On the pacing board that line does not go wrong, it goes ABSENT: a sold,
    funded, spending line simply is not on the board, and a line nobody can
    see is a line nobody paces. Reproduced in ``test_reports_map_reads.py``,
    which holds this whole defect class.
    """
    keys = [clients] if isinstance(clients, str) else list(clients or [])
    keys = [_text(k, 200) for k in keys if _text(k, 200)]
    if not keys:
        return []
    return _budget_rows(lambda q: _active(q, active_only).filter(BudgetLine.client.in_(keys)))


def all_budget_lines(*, active_only: bool = False) -> list[dict]:
    """The whole book, uncapped -- for the readings that genuinely need every
    line and would be wrong about one client if they got most of them: the
    pacing board, the cost report, the client-key index, the name dedupe.

    Bounded by the number of lines sold, which is a number a person writes
    one at a time; ``budget_line_count()`` is what a screen should print.
    """
    return _budget_rows(lambda q: _active(q, active_only))


def budget_line_count(*, active_only: bool = False) -> int:
    """Counted in SQL. A ``len()`` over a capped read is a count that stops
    at the cap and goes on being printed as the total."""
    db = SessionLocal()
    try:
        q = db.query(func.count()).select_from(BudgetLine)
        return int(_active(q, active_only).scalar() or 0)
    finally:
        db.close()


def _same_name(a: str, b: str) -> bool:
    """Two display names for one client: exact on the normalised form, never
    a substring -- the `hub/client_key.py` rule."""
    try:
        from hub.client_key import normalise_name
        return bool(a) and normalise_name(a) == normalise_name(b)
    except Exception:                  # noqa: BLE001
        return bool(a) and str(a).strip().casefold() == str(b).strip().casefold()


def links_named(name: str) -> list[ReportLink]:
    """The live links whose stored display name is this client's, whatever
    key each sits under. What lets a writer that resolved the client to
    one key today find the link it minted under another spelling
    yesterday -- the display name is stored on every link and is the one
    field the spellings share."""
    name = _text(name, 300)
    if not name:
        return []
    db = SessionLocal()
    try:
        rows = (db.query(ReportLink).filter(ReportLink.enabled.is_(True))
                  .order_by(ReportLink.id.desc()).all())
        out = [r for r in rows if _same_name(r.client_name or "", name)]
        for r in out:
            db.expunge(r)
        return out
    finally:
        db.close()


def budget_lines_named(name: str) -> list[dict]:
    """Every budget line carrying this client's display name, under
    whichever key. The dedupe reading for a writer that must not double a
    line already filed under another spelling."""
    name = _text(name, 300)
    if not name:
        return []
    return [b for b in all_budget_lines() if _same_name(b.get("client_name") or "", name)]


def _map_row(db, m: "CampaignMap") -> dict:
    """One CampaignMap as the dict every screen reads it as."""
    from . import products as _products
    name = _latest_name(db, m.platform, m.account_id, m.campaign_id)
    return {
        "platform": m.platform, "platform_label": platform_label(m.platform),
        "account_id": m.account_id, "campaign_id": m.campaign_id,
        "campaign_name": name,
        "client": m.client, "client_name": m.client_name or "",
        "product": m.product or "", "product_set": bool(m.product),
        "display_name": m.display_name or _products.default_display_name(name, m.product),
        "mapped_by": m.mapped_by or "",
        "mapped_at": iso(m.mapped_at), "auto_rule": m.auto_rule or "",
        "confirmed_by": m.confirmed_by or "", "confirmed_at": iso(m.confirmed_at),
        "pending": m.confirmed_at is None,
    }


def campaign_maps_for(clients) -> list[dict]:
    """Every mapping filed under these client keys, filtered IN THE DATABASE.

    ``mapped_campaigns()`` takes a global limit and orders by ``mapped_at``
    descending, so filtering its result by client drops that client's OLDEST
    mappings once the table passes the cap -- and drops them silently, while
    ``facts_for`` keeps returning their spend because it queries by client.
    The money stayed right and the campaign count went wrong, which is the
    quiet kind: a funded, spending budget line read ``unmapped`` on the
    pacing board because the mapping covering it had aged out of a cap
    nobody could see.

    ``CampaignMap.client`` is indexed, so this is cheap, and it is bounded by
    one client's own campaigns rather than by the whole book.
    """
    keys = [clients] if isinstance(clients, str) else list(clients or [])
    keys = [_text(k, 200) for k in keys if _text(k, 200)]
    if not keys:
        return []
    db = SessionLocal()
    try:
        rows = (db.query(CampaignMap)
                  .filter(CampaignMap.client.in_(keys))
                  .order_by(CampaignMap.mapped_at.desc()).all())
        return [_map_row(db, m) for m in rows]
    finally:
        db.close()


def mapped_campaigns_for(client: str) -> list[dict]:
    """One client's mappings, complete. See campaign_maps_for."""
    return campaign_maps_for(client)


def campaign_map(platform: str, account_id: str, campaign_id: str) -> dict | None:
    """One mapping by its campaign key, or None.

    (platform, account_id, campaign_id) is CampaignMap's PRIMARY KEY, so this
    is one indexed lookup. Three callers used to sweep a capped
    ``mapped_campaigns()`` list looking for exactly this, which past the cap
    answers "not mapped" about a campaign that is -- and each of those
    callers reads that answer as "nothing to do".
    """
    db = SessionLocal()
    try:
        row = db.get(CampaignMap, (check_platform(platform), _text(account_id, 80),
                                   _text(campaign_id, 120)))
        return _map_row(db, row) if row is not None else None
    finally:
        db.close()


def campaign_maps_by_key(keys) -> list[dict]:
    """Every mapping among these (platform, account_id, campaign_id) keys.

    The reading for "which of the campaigns I just wrote rows for is mapped,
    and to whom" -- a CSV upload clearing a client's cached page, a sync run
    logging which clients it touched. Both used to sweep a capped
    ``mapped_campaigns()`` list and keep the members of a ``touched`` set,
    so past the cap a real mapping simply was not in the list: the client's
    page kept serving a stale answer, and the sync never appeared on their
    360 record. Nothing errored either time.

    Queried by primary key, in chunks, so it cannot truncate.
    """
    want = []
    seen = set()
    for k in keys or ():
        try:
            platform, account_id, campaign_id = k
        except (TypeError, ValueError):
            continue
        key = (_text(platform, 40), _text(account_id, 80), _text(campaign_id, 120))
        if key[0] and key[2] and key not in seen:
            seen.add(key)
            want.append(key)
    if not want:
        return []
    db = SessionLocal()
    try:
        out: list[dict] = []
        for i in range(0, len(want), 200):
            chunk = want[i:i + 200]
            rows = db.query(CampaignMap).filter(
                or_(*[and_(CampaignMap.platform == p,
                           CampaignMap.account_id == a,
                           CampaignMap.campaign_id == c) for p, a, c in chunk])).all()
            out.extend(_map_row(db, m) for m in rows)
        return out
    finally:
        db.close()


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
            out[p] = {"markup": str(check_markup(v["markup"]))}
        elif has_c:
            out[p] = {"cpm": str(check_cpm(v["cpm"]))}
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


# How long a saved summary may be, so one cannot become the whole page.
EXEC_SUMMARY_MAX = 4000


def _month_key(value) -> str:
    """A month as YYYY-MM, or "" -- never a guess at what was meant."""
    text = _text(value, 7)
    if len(text) == 7 and text[4] == "-":
        try:
            y, m = int(text[:4]), int(text[5:7])
            if 2000 <= y <= 2100 and 1 <= m <= 12:
                return f"{y:04d}-{m:02d}"
        except ValueError:
            return ""
    return ""


def save_exec_summary(token: str, *, month: str, text: str, by: str) -> dict:
    """Keep one month's executive summary, reviewed and saved by a person.

    ``by`` is required and is the record of WHO stood behind the words: the
    text is written by a model and published to a client, and a paragraph
    about somebody's results with nobody's name against it is the shape
    docs/claude/48 is about. Saving bumps ``updated_at``, which is in the
    client page's cache key, so the page picks it up on both workers rather
    than fifteen minutes later on one of them.
    """
    token = _text(token, 64)
    key = _month_key(month)
    if not key:
        raise ValueError("A summary needs the month it is about, as YYYY-MM")
    body = " ".join(str(text or "").split())[:EXEC_SUMMARY_MAX]
    if not body:
        raise ValueError("A summary needs some text")
    who = _text(by, 160)
    if not who:
        raise ValueError("A summary needs a name against it")
    db = SessionLocal()
    try:
        row = db.query(ReportLink).filter(ReportLink.token == token).first()
        if row is None:
            raise ValueError("No such report link")
        saved = dict(row.exec_summaries)
        entry = {"month": key, "text": body, "generated_by": who,
                 "generated_at": iso(now())}
        saved[key] = entry
        # Newest twelve months. A link that has run for years should not
        # carry every paragraph it ever published on every read of its row.
        row.exec_summary_json = {k: saved[k] for k in sorted(saved)[-12:]}
        row.updated_at = now()
        db.commit()
        return entry
    finally:
        db.close()


def clear_exec_summary(token: str, month: str) -> bool:
    """Take one month's summary down. The client's page then shows none,
    which is the state it was in before anybody pressed anything."""
    token = _text(token, 64)
    key = _month_key(month)
    if not key:
        return False
    db = SessionLocal()
    try:
        row = db.query(ReportLink).filter(ReportLink.token == token).first()
        if row is None or key not in row.exec_summaries:
            return False
        saved = dict(row.exec_summaries)
        saved.pop(key, None)
        row.exec_summary_json = saved
        row.updated_at = now()
        db.commit()
        return True
    finally:
        db.close()


def exec_summary_for(link, month: str) -> dict | None:
    """The saved summary for one month, or None. Never raises: a client's
    page must not be lost over a paragraph."""
    try:
        key = _month_key(month)
        if not key:
            return None
        entry = link.exec_summaries.get(key)
        return entry if isinstance(entry, dict) and entry.get("text") else None
    except Exception:                  # noqa: BLE001
        return None


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
        for p, a, c, name, when, extras in (
                db.query(AdPerfDaily.platform, AdPerfDaily.account_id,
                         AdPerfDaily.campaign_id, AdPerfDaily.campaign_name,
                         AdPerfDaily.date, AdPerfDaily.extras_json)
                  .order_by(AdPerfDaily.date.desc()).all()):
            key = (p, a, c)
            if key in mapped or key in out:
                continue
            # The channel type Google reports on the campaign, off its
            # newest row: what the queue's product box opens on and what
            # the auto-mapper files under when the name says no product.
            ex = extras if isinstance(extras, dict) else {}
            channel = str(ex.get("channel_type") or "")
            out[key] = {
                "platform": p, "platform_label": platform_label(p),
                "account_id": a, "campaign_id": c,
                "campaign_name": name or "",
                # What the platform calls the account the campaign sits in
                # (StackAdapt and Amazon DSP's advertiser, Microsoft's account
                # name, AudioGO's and GroundTruth's organization), off the
                # newest row: the auto-mapper reads it for a likeness too,
                # since it is the client's own name more often than the
                # campaign's is.
                "account_name": str(ex.get("advertiser_name") or ex.get("account_name") or "").strip(),
                "last_seen": when.isoformat() if when else None,
                "spend_30d": recent.get(key, Decimal(0)),
                "refused": None,
                "channel_type": channel,
                "default_product": _default_product(p, channel),
                "product_hint": _product_hint(name or ""),
            }
    finally:
        db.close()
    # A campaign whose auto-mapping somebody refused is still unmapped and
    # still listed -- filing it by hand is the way forward -- and the row
    # says who refused what, so the next person does not file it under the
    # same client from the same name. Only while the name is the one it was
    # refused under: renamed, it is a new decision.
    for key, ref in refusals().items():
        row = out.get(key)
        if row is not None and (row["campaign_name"] or "").strip() == (ref["campaign_name"] or "").strip():
            row["refused"] = ref
    rows = sorted(out.values(), key=lambda r: (-r["spend_30d"], r["platform"],
                                               r["campaign_name"]))
    return rows[:limit]


def _default_product(platform: str, channel: str = "") -> str:
    """``products.default_for`` without a hard import at the top: products
    reads nothing from here, so the lazy import is only about keeping this
    module's import order the way every other reader found it."""
    try:
        from . import products as _products
        return _products.default_for(platform, channel)
    except Exception:                  # noqa: BLE001
        return ""


def _product_hint(name: str) -> tuple[str, str]:
    """``products.product_hint`` without a hard import at the top, for the
    reason ``_default_product`` gives."""
    try:
        from . import products as _products
        return _products.product_hint(name)
    except Exception:                  # noqa: BLE001
        return "", ""


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
        # A person's press is its own confirmation; the auto-mapper's filing
        # is a proposal, confirmed by nobody until somebody presses Confirm.
        # A person re-mapping a pending row (through the client page's
        # Save, or the queue) confirms it by the same press.
        if row.mapped_by and row.mapped_by != AUTO_MAPPED_BY:
            row.confirmed_by = row.mapped_by
            row.confirmed_at = now()
        else:
            row.confirmed_by = None
            row.confirmed_at = None
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def confirm_mapping(platform: str, account_id: str, campaign_id: str, *,
                    by: str) -> CampaignMap | None:
    """A person's confirmation of a mapping the auto-mapper proposed. Sets
    who and when; the row is otherwise untouched, so the product and the
    display name the proposal carried stand. None when the campaign is
    not mapped; a row already confirmed keeps its FIRST confirmation --
    the record is who first stood behind it."""
    platform = check_platform(platform)
    by = _text(by, 160)
    if not by:
        raise ValueError("A confirmation needs a name against it")
    db = SessionLocal()
    try:
        row = db.get(CampaignMap, (platform, _text(account_id, 80), _text(campaign_id, 120)))
        if row is None:
            return None
        if row.confirmed_at is None:
            row.confirmed_by = by
            row.confirmed_at = now()
            db.commit()
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


def refuse_mapping(platform: str, account_id: str, campaign_id: str, *,
                   by: str) -> dict | None:
    """Not theirs: delete the mapping and remember the refusal, so the
    auto-mapper does not file the campaign under the same client again on
    the next sync. The campaign goes back to the unmapped queue, where a
    person can file it by hand. Returns what was refused for the activity
    row, or None when the campaign was not mapped."""
    platform = check_platform(platform)
    by = _text(by, 160)
    if not by:
        raise ValueError("A refusal needs a name against it")
    account_id, campaign_id = _text(account_id, 80), _text(campaign_id, 120)
    db = SessionLocal()
    try:
        row = db.get(CampaignMap, (platform, account_id, campaign_id))
        if row is None:
            return None
        name = _latest_name(db, platform, account_id, campaign_id)
        out = {"platform": platform, "account_id": account_id, "campaign_id": campaign_id,
               "campaign_name": name, "client": row.client, "client_name": row.client_name or "",
               "product": row.product or "", "auto_rule": row.auto_rule or "",
               "was_confirmed": row.confirmed_at is not None}
        ref = db.get(MapRefusal, (platform, account_id, campaign_id))
        if ref is None:
            ref = MapRefusal(platform=platform, account_id=account_id, campaign_id=campaign_id)
            db.add(ref)
        ref.campaign_name = name[:400]
        ref.client = row.client
        ref.client_name = row.client_name or ""
        ref.rule = row.auto_rule
        ref.refused_by = by
        ref.refused_at = now()
        db.delete(row)
        db.commit()
        return out
    finally:
        db.close()


AUTO_RULE_FAMILIES = ("name_v1", "account_v1", "account_name_v1", "alias_v1", "fuzzy_v1")


def automap_scorecard() -> dict:
    """How each of the auto-mapper's rules has fared with people: per rule
    family (the part of ``auto_rule`` before any ``+``), how many of its
    filings a person confirmed, how many are still waiting, how many were
    refused. Confirmed and pending are read off CampaignMap, refused off
    MapRefusal (whose ``rule`` is the filing's). A mapping a person made
    by hand carries no rule and is not a filing; a refusal of a hand
    mapping (rule None) is counted under ``"hand"`` so nothing is lost.
    Never raises past the store: no tables is an empty scorecard."""
    def family(rule) -> str:
        return (str(rule or "").split("+", 1)[0] or "hand")

    rows: dict[str, dict] = {}

    def slot(f):
        return rows.setdefault(f, {"rule": f, "confirmed": 0, "pending": 0, "refused": 0})

    db = None
    try:
        db = SessionLocal()
        for rule, confirmed, n in (db.query(CampaignMap.auto_rule, CampaignMap.confirmed_at.isnot(None), func.count())
                                     .filter(CampaignMap.auto_rule.isnot(None))
                                     .group_by(CampaignMap.auto_rule, CampaignMap.confirmed_at.isnot(None)).all()):
            slot(family(rule))["confirmed" if confirmed else "pending"] += int(n or 0)
        for rule, n in db.query(MapRefusal.rule, func.count()).group_by(MapRefusal.rule).all():
            slot(family(rule))["refused"] += int(n or 0)
    except Exception:                  # noqa: BLE001 - no table yet, or no database
        return {"rules": [], "total": {"confirmed": 0, "pending": 0, "refused": 0}, "measured": False}
    finally:
        if db is not None:
            db.close()
    out = []
    for f in list(AUTO_RULE_FAMILIES) + sorted(k for k in rows if k not in AUTO_RULE_FAMILIES):
        r = rows.get(f)
        if r is None:
            continue
        decided = r["confirmed"] + r["refused"]
        r["decided"] = decided
        r["confirmed_pct"] = int(round(100 * r["confirmed"] / decided)) if decided else None
        out.append(r)
    total = {k: sum(r[k] for r in out) for k in ("confirmed", "pending", "refused")}
    return {"rules": out, "total": total, "measured": True}


def refusals() -> dict[tuple, dict]:
    """{(platform, account_id, campaign_id): {...}} for every refused
    auto-mapping. The auto-mapper reads it before it files; the unmapped
    queue prints it beside the campaign."""
    db = SessionLocal()
    try:
        return {(r.platform, r.account_id, r.campaign_id): {
                    "campaign_name": r.campaign_name or "", "client": r.client or "",
                    "client_name": r.client_name or "", "refused_by": r.refused_by or "",
                    "refused_at": iso(r.refused_at)}
                for r in db.query(MapRefusal).all()}
    except Exception:                  # noqa: BLE001 - no table yet
        return {}
    finally:
        db.close()


def learn_alias(alias: str, *, client: str, client_name: str = "", learned_from: str = "",
                by: str = "") -> dict | None:
    """Teach one alias for one client, or reinforce it. Returns the row as a
    dict, or None for an empty alias. The caller derives the alias
    (``automap.alias_phrase``); this is the store."""
    alias = _text(alias, 200).strip().lower()
    client = _text(client, 200)
    if not (alias and client):
        return None
    db = SessionLocal()
    try:
        row = db.get(CampaignAlias, (alias, client))
        if row is None:
            row = CampaignAlias(alias=alias, client=client, count=0)
            db.add(row)
        row.count = int(row.count or 0) + 1
        row.client_name = _text(client_name, 300) or row.client_name or ""
        row.learned_from = _text(learned_from, 400) or row.learned_from or ""
        row.learned_by = _text(by, 160) or row.learned_by or ""
        row.learned_at = now()
        db.commit()
        return {"alias": row.alias, "client": row.client, "client_name": row.client_name or "",
                "count": int(row.count), "learned_from": row.learned_from or "",
                "learned_by": row.learned_by or "", "learned_at": iso(row.learned_at)}
    finally:
        db.close()


def forget_alias(alias: str, client: str) -> bool:
    """Drop one alias for one client. True when a row went."""
    alias = _text(alias, 200).strip().lower()
    db = SessionLocal()
    try:
        row = db.get(CampaignAlias, (alias, _text(client, 200)))
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True
    finally:
        db.close()


def campaign_aliases() -> list[dict]:
    """Every learned alias, most taught first. Never raises past the
    store: a table not there yet is an empty book."""
    db = None
    try:
        db = SessionLocal()
        rows = (db.query(CampaignAlias)
                  .order_by(CampaignAlias.count.desc(), CampaignAlias.alias).all())
        return [{"alias": r.alias, "client": r.client, "client_name": r.client_name or r.client,
                 "count": int(r.count or 0), "learned_from": r.learned_from or "",
                 "learned_by": r.learned_by or "", "learned_at": iso(r.learned_at)}
                for r in rows]
    except Exception:                  # noqa: BLE001 - no table yet, or no database
        return []
    finally:
        if db is not None:
            db.close()


def account_evidence() -> dict[tuple, dict]:
    """What the book already says about each ad account: per (platform,
    account_id), the clients its CONFIRMED campaigns are filed under with a
    count each, the clients with only pending proposals there, and the
    clients somebody refused a filing under on that account.

    The auto-mapper's account rule reads it: an account whose confirmed
    campaigns all belong to one client is that client's account, and a new
    campaign on it is theirs until a person says otherwise. Pending
    proposals are not evidence -- a proposal resting on a proposal is how
    one wrong filing becomes an account's worth -- and a refusal on the
    account counts against the client it named. Never raises past the
    store: a table not there yet is an empty book.
    """
    out: dict[tuple, dict] = {}

    def acct(platform, account_id):
        key = (platform, account_id)
        if key not in out:
            out[key] = {"confirmed": {}, "pending": {}, "refused": {}, "names": {}}
        return out[key]

    db = SessionLocal()
    try:
        rows = (db.query(CampaignMap.platform, CampaignMap.account_id, CampaignMap.client,
                         func.max(CampaignMap.client_name), func.count(),
                         func.count(CampaignMap.confirmed_at))
                  .group_by(CampaignMap.platform, CampaignMap.account_id, CampaignMap.client)
                  .all())
        for platform, account_id, client, name, total, confirmed in rows:
            a = acct(platform, account_id)
            a["names"][client] = name or client
            if int(confirmed or 0):
                a["confirmed"][client] = int(confirmed)
            if int(total or 0) - int(confirmed or 0):
                a["pending"][client] = int(total) - int(confirmed or 0)
        for r in db.query(MapRefusal.platform, MapRefusal.account_id, MapRefusal.client,
                          MapRefusal.client_name, MapRefusal.refused_by).all():
            platform, account_id, client, name, by = r
            if client:
                a = acct(platform, account_id)
                a["refused"][client] = by or ""
                a["names"].setdefault(client, name or client)
    except Exception:                  # noqa: BLE001 - no table yet
        return {}
    finally:
        db.close()
    return out


def pending_mappings(limit: int = 500) -> list[dict]:
    """The mappings the auto-mapper proposed and nobody has confirmed,
    newest first -- the queue the Confirm / Not theirs buttons work down.

    Filtered in the database. Taking the newest N mappings and keeping the
    pending ones is a queue whose OLDEST items fall off it as the book grows,
    and an item nobody can reach is an item nobody can confirm -- while
    ``pending_count()``, which counts in SQL, goes on reporting it.
    """
    db = SessionLocal()
    try:
        rows = (db.query(CampaignMap)
                  .filter(CampaignMap.confirmed_at.is_(None))
                  .order_by(CampaignMap.mapped_at.desc())
                  .limit(max(1, int(limit))).all())
        return [_map_row(db, m) for m in rows]
    finally:
        db.close()


def pending_count() -> int:
    db = SessionLocal()
    try:
        return int(db.query(func.count()).select_from(CampaignMap)
                     .filter(CampaignMap.confirmed_at.is_(None)).scalar() or 0)
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
    """The newest ``limit`` mappings across every client -- a BOUNDED read for
    a screen that shows recent activity. Never filter this by client: use
    ``campaign_maps_for``, which filters in the database and cannot truncate
    one client's oldest mappings away."""
    db = SessionLocal()
    try:
        rows = (db.query(CampaignMap).order_by(CampaignMap.mapped_at.desc())
                  .limit(max(1, int(limit))).all())
        return [_map_row(db, m) for m in rows]
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


def _budget_line_row(b: "BudgetLine") -> dict:
    """One BudgetLine as the dict every screen reads it as."""
    source = b.source_json if isinstance(b.source_json, dict) else {}
    return {
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
        # A row written before the column existed is active: nothing else
        # could have written a status then.
        "status": b.status or "active",
    }


def _active(q, active_only: bool):
    """The active filter, written once. A row predating the status column is
    active -- so NULL counts, and ``status == 'active'`` alone would drop
    every line filed before that migration."""
    if not active_only:
        return q
    return q.filter(or_(BudgetLine.status == "active", BudgetLine.status.is_(None)))


def _budget_rows(shape) -> list[dict]:
    db = SessionLocal()
    try:
        q = shape(db.query(BudgetLine))
        rows = q.order_by(BudgetLine.created_at.desc(), BudgetLine.id.desc()).all()
        return [_budget_line_row(b) for b in rows]
    finally:
        db.close()


def budget_lines(limit: int = 500) -> list[dict]:
    """The newest ``limit`` lines across every client -- a BOUNDED read for a
    screen that pages. Never filter this by client and never ``len()`` it:
    use ``budget_lines_for``, ``all_budget_lines`` and ``budget_line_count``,
    none of which can truncate one client's oldest lines away."""
    db = SessionLocal()
    try:
        rows = (db.query(BudgetLine)
                  .order_by(BudgetLine.created_at.desc(), BudgetLine.id.desc())
                  .limit(max(1, int(limit))).all())
        return [_budget_line_row(b) for b in rows]
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

# The range a pricing rule may take, and it is ours: no platform publishes
# one. A markup is typed as a percentage and stored as a fraction, so "15"
# mistyped as "1500" is sixteen times cost -- and a platform rule reaches
# every client page that reads it, live, the moment it is saved. Refused by
# name rather than saved. 300% is four times cost, well past anything this
# book has ever carried; $250 is many times the dearest CTV rate on the
# rate card. A rule that genuinely needs more is a rule somebody should
# have to widen here, with the reason beside these two lines.
MARKUP_MAX_PCT = Decimal("300")
CPM_MAX = Decimal("250")
BOUNDS_SOURCE = "house"


def _plain(d: Decimal) -> str:
    """A Decimal without trailing zeros or exponent, for a sentence."""
    return f"{d.normalize():f}"


def check_markup(fraction) -> Decimal:
    """The stored fraction, or a refusal naming the ceiling. One door for
    the platform rule, the per-link override and the percent box."""
    m = _dec(fraction, 4)
    if m < 0:
        raise ValueError("A markup cannot be negative")
    if m * 100 > MARKUP_MAX_PCT:
        raise ValueError(f"A markup of {_plain(m * 100)}% is outside the 0-"
                         f"{_plain(MARKUP_MAX_PCT)}% this Hub accepts; 15 means 15%")
    return m


def check_cpm(value) -> Decimal:
    """The stored CPM in dollars, or a refusal naming the ceiling."""
    c = _dec(value, 2)
    if c <= 0:
        raise ValueError("A fixed CPM has to be more than zero")
    if c > CPM_MAX:
        raise ValueError(f"A fixed CPM of ${c:,.2f} is outside the $0-${_plain(CPM_MAX)} "
                         "this Hub accepts")
    return c


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
    m = check_markup(markup) if has_markup else None
    c = check_cpm(cpm) if has_cpm else None
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
    return check_markup((_dec(pct, 4) / 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


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


def pricing_version() -> str:
    """When the platform pricing rule last changed, as one string -- part of
    the client page's cache key, so a markup saved on one worker is a cache
    miss on the other. Never raises: a version that cannot be read is the
    empty string, which still keys a page."""
    db = SessionLocal()
    try:
        return iso(db.query(func.max(PlatformMarkup.updated_at)).scalar()) or ""
    except Exception:                  # noqa: BLE001 - no table yet
        return ""
    finally:
        db.close()


def mapping_version(client: str) -> str:
    """A digest of one client's mappings as they affect the page -- the
    product and display name per campaign -- for the same cache key.
    A digest rather than a stamped column, because ``mapped_at`` means when
    the campaign was mapped and a corrected display name is not a
    re-mapping. Never raises."""
    import hashlib
    db = SessionLocal()
    try:
        rows = (db.query(CampaignMap.platform, CampaignMap.account_id, CampaignMap.campaign_id,
                         CampaignMap.product, CampaignMap.display_name, CampaignMap.confirmed_at)
                  .filter(CampaignMap.client == client)
                  .order_by(CampaignMap.platform, CampaignMap.account_id, CampaignMap.campaign_id).all())
        return hashlib.sha1("|".join(str(v) for r in rows for v in r).encode("utf-8")).hexdigest()[:16]
    except Exception:                  # noqa: BLE001 - no table yet
        return ""
    finally:
        db.close()


def has_synced(platform: str) -> bool:
    """Whether this platform carries a watermark at all -- it has been read
    at least once by either sync."""
    db = SessionLocal()
    try:
        return db.get(ReportsSync, platform) is not None
    except Exception:                  # noqa: BLE001 - no table yet
        return False
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

def month_totals(platform: str, start: date, end: date) -> dict:
    """Our side of a reconcile: every fact row of one platform in a date
    range summed, mapped or not, confirmed or not -- the platform's own
    total covers the whole account, so ours has to as well."""
    platform = check_platform(platform)
    db = SessionLocal()
    try:
        spend, imps, clicks, rows = (db.query(func.sum(AdPerfDaily.spend),
                                              func.sum(AdPerfDaily.impressions),
                                              func.sum(AdPerfDaily.clicks), func.count())
                                       .filter(AdPerfDaily.platform == platform,
                                               AdPerfDaily.date >= start, AdPerfDaily.date <= end)
                                       .one())
        return {"spend": Decimal(spend or 0), "impressions": int(imps or 0),
                "clicks": int(clicks or 0), "rows": int(rows or 0)}
    finally:
        db.close()


def write_reconcile(row: dict) -> None:
    """Upsert one platform-month's comparison. Never raises past the
    caller's own try: a ledger that fails must not cost the run its
    answer."""
    db = SessionLocal()
    try:
        r = db.get(Reconcile, (row["platform"], row["month"]))
        if r is None:
            r = Reconcile(platform=row["platform"], month=row["month"])
            db.add(r)
        for k in ("state", "ours", "theirs", "drift_pct", "ours_impressions", "theirs_impressions",
                  "held", "independent", "source_label", "reason", "through"):
            if k in row:
                setattr(r, k, row[k])
        r.computed_at = now()
        db.commit()
    finally:
        db.close()


def reconcile_rows(months: int = 3) -> list[dict]:
    """The latest comparison per platform per month, newest month first,
    every platform listed for each month the run has covered."""
    db = SessionLocal()
    try:
        rows = (db.query(Reconcile).order_by(Reconcile.month.desc(), Reconcile.platform).all())
    except Exception:                  # noqa: BLE001 - no table yet
        return []
    finally:
        db.close()
    keep = sorted({r.month for r in rows}, reverse=True)[:max(1, int(months))]
    out = []
    for r in rows:
        if r.month not in keep:
            continue
        out.append({
            "platform": r.platform, "label": platform_label(r.platform), "month": r.month,
            "state": r.state, "ours": r.ours, "theirs": r.theirs, "drift_pct": r.drift_pct,
            "ours_impressions": r.ours_impressions, "theirs_impressions": r.theirs_impressions,
            "held": int(r.held or 0), "independent": r.independent,
            "source_label": r.source_label or "", "reason": r.reason or "",
            "through": r.through.isoformat() if r.through else None,
            "computed_at": iso(r.computed_at),
        })
    return out


# ---------------------------------------------------------------------------
# ProviderConfirmation
# ---------------------------------------------------------------------------

def provider_confirmations() -> dict[str, dict]:
    """{platform: {"fingerprint", "table", "by", "at"}} for every platform
    somebody has confirmed a map for. Never raises: a table that will not
    answer reads as nothing confirmed, which is the safe direction -- the
    normalize then reads nothing rather than everything."""
    db = SessionLocal()
    try:
        return {r.platform: {"fingerprint": r.fingerprint, "table": r.table_name or "",
                             "by": r.confirmed_by, "at": iso(r.confirmed_at)}
                for r in db.query(ProviderConfirmation).all()}
    except Exception:                  # noqa: BLE001 - no table yet
        return {}
    finally:
        db.close()


def confirm_provider(platform: str, *, by: str, fingerprint: str, table: str = "") -> dict:
    """Record that ``by`` looked at a platform's map against its raw table
    and confirmed it. Replaces an earlier confirmation of that platform: the
    record is who last stood behind THIS map."""
    platform = check_platform(platform)
    by = _text(by, 160)
    fingerprint = _text(fingerprint, 40)
    if not by:
        raise ValueError("A confirmation needs a name against it")
    if not fingerprint:
        raise ValueError("A confirmation needs the map's fingerprint")
    db = SessionLocal()
    try:
        row = db.get(ProviderConfirmation, platform)
        if row is None:
            row = ProviderConfirmation(platform=platform)
            db.add(row)
        row.fingerprint = fingerprint
        row.table_name = _text(table, 120)
        row.confirmed_by = by
        row.confirmed_at = now()
        db.commit()
        return {"platform": platform, "fingerprint": fingerprint, "table": row.table_name,
                "by": by, "at": iso(row.confirmed_at)}
    finally:
        db.close()


def withdraw_provider(platform: str) -> dict | None:
    """Take a confirmation back. The normalize stops reading the platform
    on its next run. Returns what was withdrawn, or None."""
    platform = check_platform(platform)
    db = SessionLocal()
    try:
        row = db.get(ProviderConfirmation, platform)
        if row is None:
            return None
        out = {"platform": platform, "by": row.confirmed_by, "at": iso(row.confirmed_at)}
        db.delete(row)
        db.commit()
        return out
    finally:
        db.close()


def pages_on_platform_rule() -> dict[str, list[dict]]:
    """``{platform: [{client, client_name, token}]}`` -- the live client
    pages whose Investment figure for that platform reads the PLATFORM
    rule: an enabled link showing Investment, at least one confirmed
    campaign on that platform filed under its client, and no override of
    its own for the platform. What a change on /reports/markup reaches,
    said before it is saved rather than discovered on a client's page.

    Every platform is a key, so a caller can ask about one that no page
    reads and get an empty list rather than a KeyError. Never raises past
    the store: a count that cannot be taken is a count of nothing, and the
    caller decides whether that is grounds to refuse a save."""
    db = SessionLocal()
    try:
        links = db.query(ReportLink).filter(ReportLink.enabled.is_(True)).all()
        plats = (db.query(CampaignMap.client, CampaignMap.platform)
                   .filter(CampaignMap.confirmed_at.isnot(None)).distinct().all())
        rows = [(l.client, l.client_name or "", l.token, bool(l.show_spend),
                 l.markup_json if isinstance(l.markup_json, dict) else {}) for l in links]
    finally:
        db.close()
    by_client: dict[str, set] = {}
    for c, p in plats:
        by_client.setdefault(c, set()).add(p)
    out: dict[str, list[dict]] = {p: [] for p in PLATFORMS}
    for client, cname, token, show_spend, own in rows:
        if not show_spend:
            continue
        for p in sorted(by_client.get(client) or ()):
            rule = own.get(p) if isinstance(own, dict) else None
            if isinstance(rule, dict) and (rule.get("markup") not in (None, "")
                                           or rule.get("cpm") not in (None, "")):
                continue
            out.setdefault(p, []).append({"client": client, "client_name": cname or client,
                                          "token": token})
    return out


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
