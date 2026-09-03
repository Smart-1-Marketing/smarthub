"""Database handle for Video Tools.

Same two-branch arrangement as modules/commercial_builder/db.py, and for the
same reason: inside the Hub the module's one table belongs in the Hub's
database beside everything else, and outside it there has to be *something*
to develop against. The Hub's shared instance is tried first, so a job written
by this module is visible to the same session everything else is written in.
"""

try:
    from hub.extensions import db  # type: ignore
    STANDALONE = False
except ImportError:                 # noqa: BLE001 — standalone development
    from flask_sqlalchemy import SQLAlchemy

    db = SQLAlchemy()
    STANDALONE = True
