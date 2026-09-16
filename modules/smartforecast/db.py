"""SmartForecast's tables, bound to the Hub's shared database driver.

The translation layer this module used to hold is `hub/dbshim.py` now. It was
written here for this module's move off its own SQLite file and was right
where it was needed -- until `modules/google_finder` needed the same thing for
its OAuth token store, at which point the choice was to copy it or to share
it, and a second copy of a translation layer is the drift `hub/jsonstore.py`
and `hub/storage.py` exist to stop.

Nothing that imports this module changed. The names it exported it still
exports; what is here is the part that was never generic -- **which tables
this module owns, and in what order** -- bound to the shared functions that
take that list.

The order is load-bearing twice over. Parents before children, because
Postgres refuses a foreign key to a table that does not exist yet and SQLite
resolves them lazily, which is how `engagement_events` sat four tables ahead
of the `embed_tokens` it references for the life of the module. And reversed
for a drop, children first.
"""
from __future__ import annotations

from hub.dbshim import (  # noqa: F401 -- re-exported; this is the module's API
    Connection,
    Cursor,
    Row,
    autoid,
    bytes_scope,
    check_declaration_order,
    connect,
    dialect,
    engine,
    engine_error,
    in_managed_backup,
    portable,
    sqlite_dump,
    statements,
    to_named,
)
from hub import dbshim as _shim

TABLES = (
    "schema_migrations", "clients", "sites", "locations", "weather_snapshots",
    "trigger_templates", "site_triggers", "content_slots", "content_variants",
    "content_publications", "trigger_events", "trigger_event_history",
    "embed_tokens", "manual_overrides", "engagement_events",
)


# Every table whose id is generated. After an id-preserving copy a Postgres
# sequence still sits at 1, so the FIRST row anybody creates collides -- the
# import verifies, the row counts match, every screen looks right, and the
# next write fails. SQLite has no sequence, so no test would ever see it.
GENERATED_ID = tuple(t for t in TABLES
                     if t not in ("schema_migrations", "trigger_templates"))


def database_bytes(con: "Connection") -> int | None:
    """How many bytes this module's rows occupy, or None if it cannot be read."""
    return _shim.database_bytes(con, TABLES)


def drop_all_for_tests() -> None:
    """Drop this module's tables. A test harness only."""
    _shim.drop_all_for_tests(TABLES)


def fix_sequences(con: "Connection") -> dict:
    """Move every generated-id sequence past the highest id in its table."""
    return _shim.fix_sequences(con, GENERATED_ID)


def sequence_state(con: "Connection") -> dict:
    """What each generated-id sequence would hand out next, against MAX(id)."""
    return _shim.sequence_state(con, GENERATED_ID)


def verify(con: "Connection", counts: dict) -> dict:
    """Row counts and sequences, compared against what was expected."""
    return _shim.verify(con, counts, GENERATED_ID)


def _reset_engine_for_tests() -> None:
    _shim._reset_engine_for_tests()
