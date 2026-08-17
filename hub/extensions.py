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


def create_all(app) -> str:
    """Create any tables that don't exist yet.

    Returns "" on success or the error text. Guarded rather than raising: a
    database slow to wake must not take a module offline for the life of the
    worker, which is the Scans post-mortem finding.
    """
    try:
        with app.app_context():
            db.create_all()
        return ""
    except Exception as exc:        # noqa: BLE001
        return str(exc)
