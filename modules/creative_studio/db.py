"""Database handle for Creative Studio.

Same two-branch arrangement as modules/video_tools/db.py and
modules/commercial_builder/db.py, and for the same reason: inside the Hub
this module's tables belong in the Hub's shared database beside everything
else, and outside it there has to be something to develop against.
"""

try:
    from hub.extensions import db  # type: ignore
    STANDALONE = False
except ImportError:                 # noqa: BLE001 — standalone development
    from flask_sqlalchemy import SQLAlchemy

    db = SQLAlchemy()
    STANDALONE = True


# Columns added to `cs_projects` after WO-CS1 shipped -- WO-CS7's aspect
# variations. `create_all()` creates missing tables and never adds a column
# to an existing one, so on the live Postgres these would be silently absent
# while every local test (a fresh SQLite database, `create_all()` puts them
# there directly) stayed green -- the trap CLAUDE.md names at length.
# Asked-then-added rather than fired blindly: the columns are declared on
# the model too, so a fresh database already has them, and an unconditional
# ALTER on every boot is two gunicorn workers each printing a Postgres ERROR
# for a column that is already there, on every deploy, forever.
_LATE_COLUMNS = [
    ("cs_projects", "parent_project_id", "INTEGER"),
    ("cs_projects", "variation_kind", "VARCHAR(20)"),
    ("cs_projects", "preview_url", "VARCHAR(1000)"),
]


def add_missing_columns() -> None:
    """Run once, after `create_all()`, inside an app context. Never raises
    past its own try/except: a database that cannot be altered right now
    must not take the module down for the life of the worker, and the
    other gunicorn worker racing the identical ALTER is the ordinary case,
    not a failure."""
    from sqlalchemy import inspect as _inspect, text as _text
    engine = db.engine
    inspector = _inspect(engine)
    for table, column, coltype in _LATE_COLUMNS:
        try:
            have = {c["name"] for c in inspector.get_columns(table)}
        except Exception:                               # noqa: BLE001
            continue                                    # no table: nothing to alter
        if column in have:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(_text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
        except Exception:                               # noqa: BLE001
            pass                                        # raced by the other worker
