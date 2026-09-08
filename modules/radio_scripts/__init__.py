"""Radio Scripts — writes broadcast and streaming-audio scripts on demand.

    /tools/radio-scripts/

A blueprint on the hub app, not a dispatcher-mounted module: `/tools/radio-scripts`
is not one of the prefixes `wsgi.py` mounts under `DispatcherMiddleware`, so a
route registered there directly would be unreachable -- the mount-shadowing
trap CLAUDE.md opens with. A blueprint means no `AuthGuard` from `wsgi.py`
either, which is why `hub/blueprint_guard.install()` is called below.

Built standalone first: reps will open this for any client on any day, and
seeding it from an inbound lead later (the Stadium-to-Screen flow) is the
same engine called with a prefilled brief, not a second code path.

Wiring:

    from modules.radio_scripts import register_radio_scripts
    register_radio_scripts(app)
"""
from __future__ import annotations

from flask import Blueprint, render_template

from . import api
from .db import db, STANDALONE

PREFIX = "/tools/radio-scripts"


def _guard(bp) -> None:
    """Staff only. Never raises -- standalone there is no Hub to log in to."""
    try:
        from hub.blueprint_guard import install as _install
    except Exception:                                 # noqa: BLE001
        return
    _install(bp, mount=PREFIX)


def create_blueprint() -> Blueprint:
    bp = Blueprint("radio_scripts", __name__, url_prefix=PREFIX,
                   template_folder="templates")
    _guard(bp)

    @bp.get("/")
    def page():                                       # noqa: ANN202
        from .engine_spec import WORD_BUDGETS
        return render_template("radio_scripts.html", word_budgets=WORD_BUDGETS)

    api.attach(bp)
    return bp


def register_radio_scripts(app):
    if STANDALONE:
        db.init_app(app)
    app.register_blueprint(create_blueprint())
    if STANDALONE:
        with app.app_context():
            try:
                from hub.extensions import create_all as _hub_create_all
                _hub_create_all(app)          # advisory-locked, race-safe
            except ImportError:
                db.create_all()
    return app
