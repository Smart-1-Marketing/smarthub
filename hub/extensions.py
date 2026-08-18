"""Shared Flask extensions — one database instance for the whole Hub.

Several modules (google_access, image_picker, commercial_builder) are written
against ``from hub.extensions import db`` and carry a standalone
``SQLAlchemy()`` fallback for when that import fails. The fallback lets them
run on their own, but inside the Hub it is actively harmful: each module ends
up with its *own* SQLAlchemy instance that was never ``init_app``-ed against
the running app, so the first query raises

    RuntimeError: The current Flask app is not registered with this
    'SQLAlchemy' instance.

which is precisely what happened when they were first dropped in. Providing
this module is what makes those fallbacks stop firing.

``init_db(app)`` is called once from the Hub factory. Modules should only ever
import ``db`` and define models against it.
"""
from __future__ import annotations

import os

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

_initialised: set[int] = set()


def database_url() -> str:
    """Postgres when configured, otherwise a file on the persistent disk.

    Falling back to in-memory SQLite would look like it worked and silently
    lose every row on restart, so the fallback is a real file — and it lands
    on /var/data when Render's disk is mounted, not in the container's
    ephemeral filesystem.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        # SQLAlchemy 2.x dropped the postgres:// alias that Render still emits.
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url
    base = "/var/data" if os.path.isdir("/var/data") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(base, exist_ok=True)
    return "sqlite:///" + os.path.join(base, "hub.sqlite3")


def init_db(app) -> bool:
    """Bind the shared instance to the app. Safe to call more than once."""
    if id(app) in _initialised:
        return True
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", database_url())
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {
        "pool_pre_ping": True,      # a Render Postgres that has gone to sleep
        "pool_recycle": 280,        # stays under most proxy idle timeouts
    })
    try:
        db.init_app(app)
        _initialised.add(id(app))
        return True
    except Exception:               # noqa: BLE001 — never block boot
        return False


# One arbitrary constant, shared by every worker. Postgres advisory locks are
# keyed on an integer and released when the session ends.
_DDL_LOCK_KEY = 728_314_905


def _is_benign_ddl_race(exc) -> bool:
    """Was this just two workers creating the same table at once?

    gunicorn runs 2 workers and create_all() is called from nine places, so
    several CREATE TABLE statements are issued concurrently on boot. Postgres
    serialises them on pg_type and the loser raises

        UniqueViolation: duplicate key value violates unique constraint
        "pg_type_typname_nsp_index"

    The table exists either way, so this is noise rather than a failure. Real
    schema errors still surface.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(sig in text for sig in (
        "pg_type_typname_nsp_index",     # concurrent CREATE TABLE
        "already exists",
        "duplicatetable",
        "uniqueviolation) duplicate key value violates unique constraint \"pg_type",
    ))


def create_all(app) -> str:
    """Create any tables that don't exist yet.

    Returns "" on success or the error text. Guarded rather than raising: a
    database slow to wake must not take a module offline for the life of the
    worker, which is the Scans post-mortem finding.

    On Postgres the DDL is wrapped in an advisory lock so only one worker runs
    it at a time. Without that the two workers race and one of them logs a
    stack trace on every single deploy — which trains everyone to ignore the
    deploy log, and that is how a real error gets missed.
    """
    try:
        with app.app_context():
            engine = db.engine
            is_pg = engine.dialect.name.startswith("postgres")
            if not is_pg:
                db.create_all()
                return ""
            from sqlalchemy import text as _sql
            with engine.connect() as cx:
                # Blocks until the other worker has finished, rather than
                # failing. Released automatically when the connection closes.
                cx.execute(_sql("SELECT pg_advisory_lock(:k)"),
                           {"k": _DDL_LOCK_KEY})
                try:
                    db.create_all()
                finally:
                    cx.execute(_sql("SELECT pg_advisory_unlock(:k)"),
                               {"k": _DDL_LOCK_KEY})
                    cx.commit()
        return ""
    except Exception as exc:        # noqa: BLE001
        if _is_benign_ddl_race(exc):
            return ""               # the table exists; nothing to report
        return str(exc)
