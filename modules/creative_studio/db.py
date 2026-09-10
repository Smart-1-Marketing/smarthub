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
