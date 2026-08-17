"""
Database handle for the Commercial Builder module.

Smart 1 Hub already has a shared `db = SQLAlchemy()` instance somewhere in
`hub/extensions.py` (referenced by the v1.6.0 handoff's mention of
`hub_audit.record()` and `create_all()`). This module tries to import that
shared instance first so its tables live in the same Postgres/SQLite
database as the rest of the Hub. When run standalone (outside the Hub, e.g.
for local development of this module on its own), it falls back to its own
SQLAlchemy instance backed by a local SQLite file.

To wire this into the real Hub: delete the `except ImportError` branch below
and just do `from hub.extensions import db`.
"""

try:
    from hub.extensions import db  # type: ignore  # real Smart 1 Hub import
    STANDALONE = False
except ImportError:
    from flask_sqlalchemy import SQLAlchemy

    db = SQLAlchemy()
    STANDALONE = True
