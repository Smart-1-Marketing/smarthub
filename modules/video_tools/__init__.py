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
from flask import Blueprint, jsonify, render_template, request

from . import alerts, api, config
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


def create_alerts_blueprint() -> Blueprint:
    """The notice that an edit finished, readable from any Hub page.

    A third blueprint, off both tool mounts, because this is not part of
    either tool: the whole point is that it is read by the dashboard and by a
    popup on whatever page the person happens to be on when the edit lands.
    Hanging it off /tools/dead-air would make the reframe popup a request to
    the dead-air tool, which is the kind of thing that reads as a bug the
    first time somebody follows it in the network tab.
    """
    bp = Blueprint("video_tools_alerts", __name__, url_prefix="/video-tools")
    _guard(bp, "/video-tools")

    @bp.get("/api/ready")
    def api_ready():                                  # noqa: ANN202
        return jsonify({"items": alerts.ready_for(_actor())})

    @bp.post("/api/ready/seen")
    def api_ready_seen():                             # noqa: ANN202
        """Stamped when the popup is SHOWN, not when it is dismissed.

        hub/static/hub-cheers.js arrived at this rule first and it holds here
        for the same reason: a reload must not bring the same interruption
        back. The cost of getting it the other way round is a person who
        refreshes twice and is told three times.
        """
        body = request.get_json(silent=True) or {}
        return jsonify({"marked": alerts.mark_seen(body.get("ids"), _actor())})

    return bp


def _actor() -> str:
    """Who is asking, read the way api.py reads it.

    `hub.current_user()`, not `flask.session` -- nothing in this Hub has ever
    written a name into the session, so asking it returns "" on every call for
    ever. Here that is worse than an unattributed row: `alerts.ready_for("")`
    treats a nameless reader as the shared-password session and shows them
    EVERYBODY's finished edits, and `mark_seen("")` then lets any browser
    silence any other person's notice. Both would look like a working popup.
    """
    try:
        from hub import current_user
        return str(current_user() or "")[:60]
    except Exception:                                 # noqa: BLE001 — standalone
        return ""


def register_video_tools(app):
    if STANDALONE:
        db.init_app(app)
    for tool in TOOLS:
        app.register_blueprint(create_blueprint(tool))
    app.register_blueprint(create_alerts_blueprint())
    if STANDALONE:
        with app.app_context():
            try:
                from hub.extensions import create_all as _hub_create_all
                _hub_create_all(app)          # advisory-locked, race-safe
            except ImportError:
                db.create_all()
    return app
