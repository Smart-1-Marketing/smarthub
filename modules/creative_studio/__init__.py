"""Creative Studio — a front door and a template layer over what the Hub
already has, not a new video product. See modules/creative_studio/api.py for
the routes and CLAUDE.md's Creative Studio section for why each table here
is new rather than a copy of something that already exists.

A blueprint on the hub app, the same shape as modules/commercial_builder and
modules/video_tools: /creative is not a prefix wsgi.py mounts through
DispatcherMiddleware, so a standalone Flask app here would never receive a
request -- the mount-shadowing trap CLAUDE.md opens with. It therefore needs
its own login guard (hub.blueprint_guard.install(), wired inside api.py)
rather than wsgi.py's AuthGuard, which only ever sees mounted modules.

Wiring:

    from modules.creative_studio import register_creative_studio
    register_creative_studio(app)
"""
from __future__ import annotations

from .db import db, STANDALONE


def register_creative_studio(app):
    if STANDALONE:
        db.init_app(app)
    from . import models  # noqa: F401 — register the tables before create_all
    from .api import bp
    app.register_blueprint(bp)
    # The client review page (WO-CS6). Its own blueprint, registered
    # unprefixed so the address is bare /review/<token> rather than
    # /creative-studio/review/<token> -- see review_routes.py's own
    # docstring for why it cannot simply live on `bp` above.
    from .review_routes import bp as review_bp
    app.register_blueprint(review_bp)
    if STANDALONE:
        with app.app_context():
            db.create_all()
            from .db import add_missing_columns
            add_missing_columns()
            from .seed_templates import seed
            seed()
            from .seed_ai_tools import seed as seed_ai_tools
            seed_ai_tools()
            from .campaign_spec import migrate_cb_campaigns
            migrate_cb_campaigns()
    # else: the Hub calls db.add_missing_columns() and
    # campaign_spec.migrate_cb_campaigns() itself, after its own shared
    # create_all() -- see hub/__init__.py's Creative Studio section.
