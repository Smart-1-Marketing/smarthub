"""Where each platform's raw rows sit in the provider schema, and which
columns become the fact table.

The managed data provider (Windsor.ai) writes one raw table per platform
into a schema of its own in the reports database -- ``REPORTS_PROVIDER_SCHEMA``,
default ``raw_windsor``. ``normalize.py`` reads those tables through this map
and writes ``AdPerfDaily`` rows; nothing else in the module knows a provider
column name, so when the provider renames one it is corrected here and
nowhere else.

**The column names below are PLACEHOLDERS.** The real provider columns are
not known until the first sync has landed. Todd fills them in from
``/reports/provider-check``, which lists every table actually present in the
schema with its columns and says, per platform, whether this map resolves
against it -- a table missing, or these columns missing. Until a platform
resolves, the normalize job skips it and says so on ``/reports/``; a guess
that happened to match a column of the wrong meaning would file the wrong
number under the right name, which is the failure nothing on screen would
show.

Each entry:

* ``table`` -- the raw table's name inside the schema.
* ``date``, ``account_id``, ``campaign_id``, ``campaign_name``, ``spend``,
  ``impressions``, ``clicks``, ``conversions`` -- the column carrying each
  fact-table field. ``conversions`` may be ``None`` for a platform that
  reports none.
* ``extras`` -- columns copied into ``extras_json`` as they are: whatever the
  platform reports that no fact column names (video views, completes,
  listens), read by the client dashboard where a platform has them.
* ``spend_divisor`` -- what raw spend is divided by on the way in. Google Ads
  and Microsoft report cost in micros through some connectors (1,000,000);
  most report dollars (1). Wrong here is wrong by six orders of magnitude on
  every report, so it is a number per platform rather than a rule.
* ``restate_days`` -- how far back a sync re-reads. Platforms restate
  attribution for a while after the day: 7 is enough for most, and the
  Trade Desk and both Amazon feeds restate for up to four weeks.

``suite`` is deliberately absent: Smart 1 Suite outcomes (leads, bookings)
come from a later job that reads Suite rather than a provider table.
"""
from __future__ import annotations

import os

from .store import PLATFORMS

DEFAULT_SCHEMA = "raw_windsor"

# The one reader of the schema name. Read at call time rather than at import:
# it is the variable somebody corrects after provider-check names the schema
# the tables actually landed in, and an import-time snapshot would need a
# redeploy to take effect. Empty means "no schema prefix", which is how the
# tests run on SQLite -- SQLite has no schemas, so the raw tables sit beside
# the fact table under their bare names.
def schema() -> str:
    return (os.environ.get("REPORTS_PROVIDER_SCHEMA", DEFAULT_SCHEMA) or "").strip()


def qualified(table: str) -> str:
    """The table name as it goes into a SELECT: schema-prefixed on Postgres,
    bare where no schema is set."""
    s = schema()
    return f"{s}.{table}" if s else table


def _source(table: str, *, spend_divisor: int = 1, restate_days: int = 7,
            conversions: str | None = "conversions", extras: tuple = ()) -> dict:
    # PLACEHOLDER column names -- see the module docstring.
    return {
        "table": table,
        "date": "date",
        "account_id": "account_id",
        "campaign_id": "campaign_id",
        "campaign_name": "campaign_name",
        "spend": "spend",
        "impressions": "impressions",
        "clicks": "clicks",
        "conversions": conversions,
        "extras": list(extras),
        "spend_divisor": spend_divisor,
        "restate_days": restate_days,
    }


PLATFORM_SOURCES: dict[str, dict] = {
    "ttd":        _source("ttd", restate_days=28, extras=("video_views", "completes")),
    "google":     _source("google_ads"),
    "bing":       _source("bing_ads"),
    "linkedin":   _source("linkedin_ads"),
    "tiktok":     _source("tiktok_ads", extras=("video_views",)),
    "audiogo":    _source("audiogo", conversions=None, extras=("listens", "completes")),
    "stackadapt": _source("stackadapt"),
    "meta":       _source("facebook_ads", extras=("video_views",)),
    "groundtruth": _source("groundtruth", extras=("visits",)),
    "x":          _source("twitter_ads"),
    "amazon_sa":  _source("amazon_sponsored_ads", restate_days=28),
    "amazon_dsp": _source("amazon_dsp", restate_days=28, extras=("video_views", "completes")),
}

# Every platform except suite is mapped, and nothing else is: a platform
# added to PLATFORMS without a source here is a sync that never runs, and a
# source for a platform the store refuses is a sync that writes nothing.
assert set(PLATFORM_SOURCES) == set(PLATFORMS) - {"suite"}, \
    "provider_map.PLATFORM_SOURCES must cover every platform except suite"

# The columns a source has to name for the fact table to be written at all.
REQUIRED_FIELDS = ("date", "account_id", "campaign_id", "campaign_name",
                   "spend", "impressions", "clicks")


def required_columns(source: dict) -> list[str]:
    """Every column a source reads: the required ones, conversions where the
    platform reports them, and the extras."""
    cols = [source[f] for f in REQUIRED_FIELDS]
    if source.get("conversions"):
        cols.append(source["conversions"])
    cols.extend(source.get("extras") or [])
    return cols
