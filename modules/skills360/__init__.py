"""360 Skills -- per-client skills a rep switches on, and the Client 360
cards and client-facing links those skills add.

Three blueprints, one module (see app.py): the staff tool at
``/tools/360-skills``, the ``/api/client/skills/...`` routes Client 360's
gated cards use, and ``/hot/<token>`` -- the client's own hotsheet, no
login, no chrome (hub/__init__.CHROMELESS carries the prefix).
"""
from __future__ import annotations

from .app import HOT_MOUNT, MOUNT, bp, bp_client, bp_hot, skills_for

__all__ = ["register_skills360", "MOUNT", "HOT_MOUNT", "bp", "bp_client", "bp_hot", "skills_for"]


def register_skills360(app) -> None:
    if "skills360" not in app.blueprints:
        app.register_blueprint(bp, url_prefix=MOUNT)
    if "skills360_client" not in app.blueprints:
        app.register_blueprint(bp_client)
    if "skills360_hot" not in app.blueprints:
        app.register_blueprint(bp_hot, url_prefix=HOT_MOUNT)
