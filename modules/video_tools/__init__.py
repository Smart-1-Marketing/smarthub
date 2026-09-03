"""Video Tools — two edits the Hub could not make before.

    /tools/dead-air/           Dead Air Cutter
    /tools/vertical-reframe/   Vertical Reframe

Blueprints on the hub app rather than dispatcher-mounted apps, for the reason
CLAUDE.md gives and modules/video_backgrounds repeats: the prefixes wsgi.py
mounts under /tools are a specific list, and a hub route under one of them is
unreachable. Neither of these is on that list, so both belong to the hub app —
and both therefore need their own login guard, because wsgi.py's AuthGuard
only ever sees mounted modules.

Two blueprints rather than one with two pages. They are one module because
they share a source picker, a job table, a submit-and-poll cycle and a save
path; they are two tools because they are two tiles, two help entries and two
different jobs a rep sits down to do. Splitting at the blueprint keeps the
first true without pretending the second is not.

Wiring:

    from modules.video_tools import register_video_tools
    register_video_tools(app)
"""
from flask import Blueprint, render_template

from . import api, config
from .db import db, STANDALONE

TOOLS = {
    "dead_air": {
        "prefix": "/tools/dead-air",
        "template": "dead_air.html",
        "title": "Dead Air Cutter",
    },
    "reframe": {
        "prefix": "/tools/vertical-reframe",
        "template": "vertical_reframe.html",
        "title": "Vertical Reframe",
    },
}


def _guard(bp, mount: str) -> None:
    """Staff only. Never raises — standalone there is no Hub to log in to."""
    try:
        from hub.blueprint_guard import install as _install
    except Exception:                                 # noqa: BLE001
        return
    _install(bp, mount=mount)


def create_blueprint(tool: str) -> Blueprint:
    spec = TOOLS[tool]
    bp = Blueprint(f"video_tools_{tool}", __name__,
                   url_prefix=spec["prefix"],
                   template_folder="templates")
    _guard(bp, spec["prefix"])

    @bp.get("/")
    def page():                                       # noqa: ANN202
        # The config tables go to the template rather than being written out
        # in it: the ratios, focus modes and sensitivity steps are decisions
        # with reasons attached in config.py, and a second copy in HTML is how
        # a tool comes to offer an option the server does not accept.
        return render_template(spec["template"], tool=tool,
                               title=spec["title"], cfg=config,
                               ratios=config.RATIOS, modes=config.MODES,
                               focus=config.FOCUS,
                               sensitivity=config.SENSITIVITY)

    api.attach(bp, tool)
    return bp


def register_video_tools(app):
    if STANDALONE:
        db.init_app(app)
    for tool in TOOLS:
        app.register_blueprint(create_blueprint(tool))
    if STANDALONE:
        with app.app_context():
            try:
                from hub.extensions import create_all as _hub_create_all
                _hub_create_all(app)          # advisory-locked, race-safe
            except ImportError:
                db.create_all()
    return app
