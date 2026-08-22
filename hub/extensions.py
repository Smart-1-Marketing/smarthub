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

Modules that predate the Hub carry a classic ``declarative_base()`` rather than
``db.Model``, and each used to call ``create_engine()`` for itself. They now
take the engine from here too — ``shared_engine()`` / ``session_factory()`` for
the connection, ``create_all_metadata()`` for the advisory-locked DDL — so the
whole process runs one pool per database instead of one per module. See the
comment above ``engine_for`` for why that matters.
"""
from __future__ import annotations

import os
import threading

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy import make_url
from sqlalchemy.orm import scoped_session, sessionmaker

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
        return normalise_url(url)
    base = "/var/data" if os.path.isdir("/var/data") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(base, exist_ok=True)
    return "sqlite:///" + os.path.join(base, "hub.sqlite3")


def normalise_url(url: str) -> str:
    """SQLAlchemy 2.x dropped the postgres:// alias that Render still emits."""
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


# ---------------------------------------------------------------------------
# One engine per database URL, for the whole process
# ---------------------------------------------------------------------------
#
# Scans, Sales Builder, Ads, Calculators and Image Picker each used to call
# create_engine() themselves. Every one of them resolved DATABASE_URL with its
# own copy of the same five lines, so against the shared Postgres the Hub ran
# five connection pools where it needed one: nothing could share a transaction,
# and "back up the database" meant five different answers.
#
# The SQLite fallbacks were worse than untidy. Each module fell back to a file
# next to its own source -- inside the container image -- so a deploy without
# DATABASE_URL set wrote scans and quotes into a filesystem that Render throws
# away on the next deploy. database_url() puts the fallback on the mounted disk
# at /var/data instead, so the same code is durable either way.
#
# The registry is keyed on the URL rather than being a single global, because a
# module with a genuine reason to address a second database (Calculators can be
# pointed at its own via CALCULATORS_DATABASE_URL) should still get one pool per
# database rather than one per import.
_engines: dict[str, object] = {}
_engine_lock = threading.Lock()


def engine_options(url: str) -> dict:
    """Pool settings, in one place, for whichever backend is configured."""
    opts: dict = {
        "future": True,
        "pool_pre_ping": True,      # a Render Postgres that has gone to sleep
    }
    if url.startswith("sqlite"):
        # Background threads (the scheduler, the scans poller) touch the same
        # file the request handlers do.
        opts["connect_args"] = {"check_same_thread": False}
    else:
        opts["pool_recycle"] = 280  # stays under most proxy idle timeouts
        # Stated rather than left to the default, because the arithmetic is
        # the reason to share a pool at all: 2 gunicorn workers x (5 + 10) = 30
        # connections at full stretch. Per-module engines multiplied that by
        # the number of modules, and no single module looked unreasonable.
        opts["pool_size"] = 5
        opts["max_overflow"] = 10
    return opts


def engine_for(url: str | None = None):
    """The process-wide engine for ``url`` (the Hub database when omitted).

    Called twice with the same URL, this returns the same engine — that is the
    whole point. Modules should not call ``create_engine`` themselves.
    """
    resolved = normalise_url(url) if url else database_url()
    with _engine_lock:
        engine = _engines.get(resolved)
        if engine is None:
            engine = _sa_create_engine(resolved, **engine_options(resolved))
            _engines[resolved] = engine
        return engine


def shared_engine():
    """The Hub database engine. Shorthand for ``engine_for()``."""
    return engine_for()


def session_factory(url: str | None = None, *, scoped: bool = False,
                    expire_on_commit: bool = False):
    """A sessionmaker bound to the shared engine.

    Sessions are cheap and per-caller; the pool underneath them is the thing
    that has to be shared. Modules keep their own ``SessionLocal`` name and
    lose nothing but the duplicated engine.
    """
    factory = sessionmaker(bind=engine_for(url), future=True,
                           expire_on_commit=expire_on_commit)
    return scoped_session(factory) if scoped else factory


# Where each module put its SQLite file before the engines were merged. These
# only ever existed on a deploy with no DATABASE_URL set; the rows in them are
# not reachable from the merged database, so they are reported rather than
# quietly ignored — absent data has to read as absent, not as zero.
LEGACY_SQLITE_FILES = (
    ("scans", "modules/scans/smart1_scans.db"),
    ("sales_builder", "modules/sales_builder/smart1_sales_builder.db"),
    ("ads_builder", "modules/ads_builder/smart1_ads.db"),
    ("image_picker", "image_picker.db"),
    ("calculators", "data/calculators.db"),
)


def legacy_databases() -> list[dict]:
    """Per-module SQLite files still on disk after the engines were merged."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = []
    for module, rel in LEGACY_SQLITE_FILES:
        path = os.path.join(root, rel)
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                found.append({"module": module, "file": rel,
                              "bytes": os.path.getsize(path)})
        except OSError:
            continue
    return found


class _HubSQLAlchemy(SQLAlchemy):
    """Flask-SQLAlchemy, using the shared engine rather than a sixth one.

    Without this, ``db.session`` and every module that kept its own
    ``SessionLocal`` would still be talking to the same Postgres down two
    different pools. Overriding engine construction is the only hook
    Flask-SQLAlchemy offers for that, so it is written to fall back to the
    stock behaviour on any surprise rather than to take the Hub down.
    """

    def _make_engine(self, bind_key, options, app, *args, **kwargs):
        try:
            if bind_key is None:
                url = options.get("url")
                if url is not None:
                    # str(URL) masks the password, so two identical URLs would
                    # not compare equal. Rendered in full here, never logged.
                    given = make_url(url).render_as_string(hide_password=False)
                    if given == make_url(database_url()).render_as_string(
                            hide_password=False):
                        return engine_for()
        except Exception:           # noqa: BLE001 — never block boot
            pass
        return super()._make_engine(bind_key, options, app, *args, **kwargs)


# Flask-SQLAlchemy's engine factory is a private method; if a future release
# renames it, fall back to the stock class (a second pool, which is what the
# Hub had before) rather than failing to import and taking five modules with it.
db = _HubSQLAlchemy() if hasattr(SQLAlchemy, "_make_engine") else SQLAlchemy()


def init_db(app) -> bool:
    """Bind the shared instance to the app. Safe to call more than once."""
    if id(app) in _initialised:
        return True
    url = database_url()
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", url)
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", engine_options(url))
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


def _locked_create_all(engine, run) -> str:
    """Run ``run()`` — a create_all — with only one worker inside it at a time.

    Returns "" on success or the error text. Guarded rather than raising: a
    database slow to wake must not take a module offline for the life of the
    worker, which is the Scans post-mortem finding.

    On Postgres the DDL is wrapped in an advisory lock. Without that the two
    workers race and one of them logs a stack trace on every single deploy —
    which trains everyone to ignore the deploy log, and that is how a real
    error gets missed.
    """
    try:
        if not engine.dialect.name.startswith("postgres"):
            run()
            return ""
        from sqlalchemy import text as _sql
        with engine.connect() as cx:
            # Blocks until the other worker has finished, rather than failing.
            # Released automatically when the connection closes.
            cx.execute(_sql("SELECT pg_advisory_lock(:k)"), {"k": _DDL_LOCK_KEY})
            try:
                run()
            finally:
                cx.execute(_sql("SELECT pg_advisory_unlock(:k)"),
                           {"k": _DDL_LOCK_KEY})
                cx.commit()
        return ""
    except Exception as exc:        # noqa: BLE001
        if _is_benign_ddl_race(exc):
            return ""               # the table exists; nothing to report
        return str(exc)


def create_all(app) -> str:
    """Create any tables that don't exist yet, for Flask-SQLAlchemy models."""
    try:
        with app.app_context():
            return _locked_create_all(db.engine, db.create_all)
    except Exception as exc:        # noqa: BLE001
        if _is_benign_ddl_race(exc):
            return ""
        return str(exc)


def create_all_metadata(metadata, url: str | None = None) -> str:
    """The same protection for modules that use a classic declarative ``Base``.

    Scans, Sales Builder and Ads call ``Base.metadata.create_all(engine)`` at
    import time, which is exactly the concurrent-DDL race the advisory lock
    exists for; they were outside it only because they were also outside the
    shared engine. Returns "" on success or the error text — it never raises,
    so a slow database cannot stop the module from loading and explaining
    itself.
    """
    engine = engine_for(url)
    return _locked_create_all(engine, lambda: metadata.create_all(engine))
