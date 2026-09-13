"""The provider normalize: raw provider tables in, fact rows out.

``run()`` is what ``hub/scheduler.job_reports_normalize`` calls, hourly. For
every platform ``provider_map.PLATFORM_SOURCES`` names whose raw table is
actually present, it selects the last ``restate_days`` of rows, maps them to
``AdPerfDaily`` dicts and upserts them in batches of a thousand. Then the
auto-mapper files any campaign whose name follows the rename shape.

Three rules, each a way this goes quietly wrong:

* **One platform's failure is isolated from the others.** A renamed column
  on the Trade Desk table must not cost Google its sync; each platform runs
  in its own try and reports its own error, and the watermark
  (``store.ReportsSync``) records the failure per platform so ``/reports/``
  can say which one is broken rather than drawing "last synced" off the
  fact table -- which would go on reporting the last good run for ever.
* **A table that is not there is skipped and said**, never invented. The
  provider lands tables as syncs are switched on, so a missing one is the
  ordinary state on day one and ``/reports/provider-check`` is where it is
  looked at.
* **A map nobody has confirmed is not read.** The column names in
  ``provider_map.py`` are guesses until somebody has looked at a raw row
  under them, and a guess that resolves is the dangerous one: it files a
  number under a name with nothing on any screen saying the number is the
  wrong one. So a resolved platform is skipped until a person confirms its
  map on the provider-check page (``store.ProviderConfirmation``, keyed on
  ``provider_map.fingerprint()``), and a platform whose map has changed
  since it was confirmed is skipped again and -- because it HAS synced --
  says so on its watermark, where ``/reports/`` and ``/status`` read it. A
  never-synced platform awaiting its first confirmation records nothing:
  on a fresh deployment that is every platform, and twelve red rows on
  the status page for a queue somebody is about to work is the check that
  gets switched off.
* **Spend is divided on the way in**, by the platform's own
  ``spend_divisor``, and nowhere else. The fact table holds dollars; a
  connector reporting micros is a per-platform fact recorded in the map.
* **Native wins.** A platform whose watermark (``store.ReportsSync``) was
  written by its own API (``source="native"``, from ``ttd.py`` or
  ``google_ads_perf.py`` through ``hub/scheduler.job_reports_native_pull``)
  within the last ``NATIVE_WINS_HOURS`` is skipped here and said: the
  platform's own figure for a (platform, account, campaign, day) must not be
  overwritten by the provider's copy of it, and the two syncs write the same
  key. ``store.native_is_current()`` is the one reading of that rule.

``schema_tables()`` is also what the provider-check page reads: the tables
present in the provider schema with their columns, through SQLAlchemy's
inspector, which asks ``information_schema`` on Postgres and
``sqlite_master`` / ``PRAGMA table_info`` on SQLite.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import inspect, text

from . import automap, provider_map, store

log = logging.getLogger(__name__)

BATCH = 1000
SOURCE = "windsor"
NATIVE_WINS_HOURS = 24


# ---------------------------------------------------------------------------
# What the provider schema actually holds
# ---------------------------------------------------------------------------

def schema_tables() -> dict[str, list[str]]:
    """{table: [column, ...]} for every table in the provider schema.

    An empty schema name (SQLite, or a deployment that has cleared it) reads
    the default schema, which on SQLite is the one database. The fact tables
    of this module live there too on SQLite and are listed like any other --
    provider-check shows what is there, and what is there includes them.
    """
    insp = inspect(store.engine)
    schema = _schema()
    try:
        names = insp.get_table_names(schema=schema)
    except Exception as exc:              # noqa: BLE001 - a schema that is not there
        log.warning("reports: provider schema %r unreadable: %s", schema, exc)
        return {}
    out = {}
    for name in sorted(names):
        try:
            out[name] = [c["name"] for c in insp.get_columns(name, schema=schema)]
        except Exception:                 # noqa: BLE001
            out[name] = []
    return out


CONFIRMATION_STATES = ("confirmed", "unconfirmed", "superseded")


def confirmation_for(platform: str, confirmations: dict | None = None) -> dict:
    """One platform's confirmation state against the map as it stands:
    ``confirmed`` (somebody confirmed this very map), ``superseded`` (somebody
    confirmed an earlier map and it has changed since) or ``unconfirmed``
    (nobody has). Who and when ride along where there is a row."""
    confirmations = store.provider_confirmations() if confirmations is None else confirmations
    row = confirmations.get(platform)
    fp = provider_map.fingerprint(platform)
    if row is None:
        return {"state": "unconfirmed", "by": "", "at": None, "fingerprint": fp}
    state = "confirmed" if row.get("fingerprint") == fp else "superseded"
    return {"state": state, "by": row.get("by") or "", "at": row.get("at"), "fingerprint": fp}


def check_sources(tables: dict[str, list[str]] | None = None) -> list[dict]:
    """Per platform: does the map resolve against what is in the schema,
    and has a person confirmed it?

    ``status`` is one of ``resolved``, ``table_missing`` or
    ``columns_missing`` (with ``missing`` naming the columns);
    ``confirmation`` is ``confirmation_for()``'s answer; ``readable`` is
    the one bit the normalize acts on -- resolved AND confirmed. Every
    platform in the map is listed, because the platform that is absent from
    the schema is the finding.
    """
    tables = schema_tables() if tables is None else tables
    lower = {t.lower(): cols for t, cols in tables.items()}
    confirmations = store.provider_confirmations()
    out = []
    for platform, src in provider_map.PLATFORM_SOURCES.items():
        cols = lower.get(src["table"].lower())
        if cols is None:
            status, missing = "table_missing", []
        else:
            have = {c.lower() for c in cols}
            missing = [c for c in provider_map.required_columns(src)
                       if c.lower() not in have]
            status = "columns_missing" if missing else "resolved"
        conf = confirmation_for(platform, confirmations)
        out.append({"platform": platform, "label": store.platform_label(platform),
                    "table": provider_map.qualified(src["table"]),
                    "status": status, "missing": missing,
                    "columns": provider_map.required_columns(src),
                    "spend_divisor": src["spend_divisor"],
                    "restate_days": src["restate_days"],
                    "confirmation": conf,
                    "readable": status == "resolved" and conf["state"] == "confirmed"})
    return out


def sample_row(platform: str) -> dict:
    """The newest raw row of one platform's table, as the map reads it:
    ``{"measured": bool, "fields": [{"field", "column", "value"}, ...],
    "spend_filed": str | None, "note": str}``. What the provider-check page
    prints under each mapped column so a person confirms against real
    values rather than plausible names -- the spend is shown as it would be
    FILED, after the divisor, because a divisor wrong by six orders of
    magnitude is the mistake that survives every other look. Never raises:
    a table that will not answer is ``measured: False`` with the reason."""
    src = provider_map.PLATFORM_SOURCES[platform]
    cols = provider_map.required_columns(src)
    schema = _schema()
    table = (f"{_q(schema)}." if schema else "") + _q(src["table"])
    sql = (f"SELECT {', '.join(_q(c) for c in cols)} FROM {table} "
           f"ORDER BY {_q(src['date'])} DESC LIMIT 1")
    try:
        with store.engine.connect() as conn:
            row = conn.execute(text(sql)).fetchone()
    except Exception as exc:              # noqa: BLE001 - a column that is not there
        return {"measured": False, "fields": [], "spend_filed": None,
                "note": f"the table could not be read ({type(exc).__name__}: {str(exc)[:160]})"}
    if row is None:
        return {"measured": False, "fields": [], "spend_filed": None,
                "note": "the table is present and has no rows yet"}
    r = row._mapping
    fields = []
    for field in provider_map.REQUIRED_FIELDS + ("conversions",):
        col = src.get(field)
        if not col:
            continue
        fields.append({"field": field, "column": col, "value": r.get(col)})
    for col in src.get("extras") or []:
        fields.append({"field": f"extras.{col}", "column": col, "value": r.get(col)})
    raw = r.get(src["spend"])
    divisor = src.get("spend_divisor") or 1
    try:
        filed = f"{float(raw) / divisor:,.2f}" if raw not in (None, "") else None
    except (TypeError, ValueError):
        filed = None
    return {"measured": True, "fields": fields, "spend_filed": filed,
            "note": "" if filed is not None else "the spend column of the newest row is not a number"}


# ---------------------------------------------------------------------------
# One platform
# ---------------------------------------------------------------------------

def _schema() -> str | None:
    """The schema to read, or None: SQLite has no schemas, so a name set for
    the Postgres deployment is ignored there rather than asked for a table
    called raw_windsor.sqlite_master."""
    if not store.is_postgres():
        return None
    return provider_map.schema() or None


def _q(name: str) -> str:
    """A column or table name quoted for the engine's dialect."""
    return store.engine.dialect.identifier_preparer.quote(name)


def _select(src: dict) -> str:
    cols = provider_map.required_columns(src)
    table = _schema()
    table = (f"{_q(table)}." if table else "") + _q(src["table"])
    return (f"SELECT {', '.join(_q(c) for c in cols)} FROM {table} "
            f"WHERE {_q(src['date'])} >= :since")


def _fact(platform: str, src: dict, row) -> dict:
    """One raw row as the dict upsert_rows() validates."""
    r = row._mapping
    divisor = src.get("spend_divisor") or 1
    raw_spend = r.get(src["spend"])
    spend = (float(raw_spend) / divisor) if raw_spend not in (None, "") else 0
    out = {
        "platform": platform, "source": SOURCE,
        "date": r.get(src["date"]),
        "account_id": r.get(src["account_id"]),
        "campaign_id": r.get(src["campaign_id"]),
        "campaign_name": r.get(src["campaign_name"]),
        "spend": spend,
        "impressions": r.get(src["impressions"]),
        "clicks": r.get(src["clicks"]),
        "conversions": r.get(src["conversions"]) if src.get("conversions") else 0,
        "extras": {c: r.get(c) for c in (src.get("extras") or [])
                   if r.get(c) is not None},
    }
    extras = out["extras"]
    # The two columns the fact table names, when the platform reports them
    # under those names: a completes count on a video or audio platform is
    # what the client dashboard's completion tile reads.
    for key in ("video_views", "completes"):
        if key in extras:
            out[key] = extras[key]
    if "listens" in extras and "completes" not in extras:
        out["completes"] = extras["listens"]
    return out


def normalize_platform(platform: str, today: date | None = None,
                       touched: set | None = None) -> int:
    """Sync one platform. Returns the row count; raises on any failure, and
    the caller isolates it. ``touched`` collects the (platform, account,
    campaign) keys written, so the run can say which clients it reached."""
    src = provider_map.PLATFORM_SOURCES[platform]
    since = (today or date.today()) - timedelta(days=int(src["restate_days"]))
    written = 0
    # SQLite has no date type: a raw table there holds ISO text, and the ISO
    # form of a date compares correctly as text. Postgres takes the date.
    bound = since.isoformat() if not store.is_postgres() else since
    with store.engine.connect() as conn:
        result = conn.execute(text(_select(src)), {"since": bound})
        while True:
            chunk = result.fetchmany(BATCH)
            if not chunk:
                break
            facts = [_fact(platform, src, row) for row in chunk]
            written += store.upsert_rows(facts)
            if touched is not None:
                touched.update((f["platform"], str(f["account_id"] or "").strip(),
                                str(f["campaign_id"] or "").strip()) for f in facts)
    return written


# ---------------------------------------------------------------------------
# The whole job
# ---------------------------------------------------------------------------

def run(today: date | None = None, actor: str = "scheduler") -> dict:
    """Every platform whose table is present, each isolated; then automap.

    Returns ``{platform: {"rows": n, "error": str | None}}`` with the
    automap outcome under ``"automap"``. A platform whose table is absent is
    reported as skipped (``rows`` 0, ``error`` naming the table) rather than
    left out, so the answer lists every platform the map knows about.
    """
    tables = schema_tables()
    checks = {c["platform"]: c for c in check_sources(tables)}
    out: dict = {}
    synced = 0
    touched: set = set()
    for platform in provider_map.PLATFORM_SOURCES:
        c = checks[platform]
        if store.native_is_current(platform, NATIVE_WINS_HOURS):
            # Not recorded on the watermark: the native row there is the
            # one this skip defers to, and stamping over it would retire it.
            out[platform] = {"rows": 0, "skipped": True, "native": True,
                             "error": (f"{store.platform_label(platform)} was pulled from "
                                       f"its own API within the last {NATIVE_WINS_HOURS} "
                                       "hours, so the provider copy is not read")}
            continue
        if c["status"] == "table_missing":
            msg = f"table {c['table']} is not in the schema"
            out[platform] = {"rows": 0, "error": msg, "skipped": True}
            # A platform that HAS synced and whose table is now gone is a
            # finding, and the watermark is where /reports/ reads it from;
            # left standing, the last good run reads as healthy for ever.
            # One that has never synced records nothing: on a fresh
            # deployment most tables are absent by design.
            if store.has_synced(platform):
                store.record_sync(platform, rows=0, error=msg)
            continue
        if c["status"] == "columns_missing":
            msg = f"{c['table']} is missing columns: " + ", ".join(c["missing"])
            out[platform] = {"rows": 0, "error": msg, "skipped": True}
            store.record_sync(platform, rows=0, error=msg)
            continue
        conf = c["confirmation"]
        if conf["state"] != "confirmed":
            label = store.platform_label(platform)
            if conf["state"] == "superseded":
                msg = (f"{label}'s column map has changed since {conf['by']} confirmed it; "
                       f"confirm the new map on /reports/provider-check")
            else:
                msg = f"{label}'s column map has not been confirmed on /reports/provider-check"
            out[platform] = {"rows": 0, "error": msg, "skipped": True, "unconfirmed": True}
            # A platform that HAS synced and now cannot is a finding, and
            # the watermark is where /reports/ and /status read it from.
            # One awaiting its first confirmation records nothing: on a
            # fresh deployment that is every platform, and a status page
            # red across the board for a queue about to be worked is the
            # check that gets switched off.
            if store.has_synced(platform):
                store.record_sync(platform, rows=0, error=msg)
            continue
        try:
            n = normalize_platform(platform, today=today, touched=touched)
        except Exception as exc:          # noqa: BLE001 - one platform, not the job
            msg = f"{type(exc).__name__}: {exc}"[:500]
            log.exception("reports: normalize %s failed", platform)
            out[platform] = {"rows": 0, "error": msg}
            store.record_sync(platform, rows=0, error=msg)
            continue
        out[platform] = {"rows": n, "error": None}
        synced += 1
        store.record_sync(platform, rows=n, error="")

    try:
        out["automap"] = automap.run(actor=actor)
    except Exception as exc:              # noqa: BLE001 - mapping is after the facts
        log.exception("reports: automap failed")
        out["automap"] = {"mapped": 0, "error": f"{type(exc).__name__}: {exc}"[:300]}

    # One activity row per client touched in this run: the clients whose
    # campaigns received rows (the automap above may have just filed some of
    # them), so the sync shows on that client's record and nobody else's.
    total_rows = sum(v.get("rows", 0) for k, v in out.items() if k != "automap")
    _log_clients(touched, synced, total_rows, actor)
    return out


# Which clients a reports_sync row was written for today. The provider
# restates a week of days every hour, so every client is "touched" every
# run, and a row per run is twenty-four identical rows a day on Client 360
# -- a state logged every run, hub/google_index.py's rule. Once a day per
# client; per process, and the scheduler runs on the leader alone.
_SYNC_LOGGED: dict[str, date] = {}


def _log_clients(touched: set, platforms: int, rows: int, actor: str) -> None:
    """audit.log(action="reports_sync") once per client whose campaigns
    received rows in this run -- and not more than once a day."""
    if not rows or not touched:
        return
    try:
        from hub import audit as hub_audit
    except Exception:                     # noqa: BLE001 - standalone
        return
    seen = {}
    for m in store.mapped_campaigns(limit=5000):
        # A pending auto-mapping is a proposal, not yet a fact about whose
        # campaign it is: a sync row on that client's record would say we
        # synced their campaigns before anybody had agreed they were theirs.
        if m.get("pending"):
            continue
        if (m["platform"], m["account_id"], m["campaign_id"]) in touched:
            seen.setdefault(m["client"], m["client_name"] or m["client"])
    for key, name in seen.items():
        if _SYNC_LOGGED.get(key) == date.today():
            continue
        _SYNC_LOGGED[key] = date.today()
        try:
            hub_audit.log("reports", "reports_sync", actor=actor, client=name,
                          client_key=key, action="reports_sync",
                          detail=f"synced {platforms} platforms, {rows} rows")
        except Exception:                 # noqa: BLE001 - a log line is not the sync
            pass
