"""
Commercial Builder — a module inside Smart 1 Creative Hub.

Mounts at /tools/commercial-builder, following the same convention as the
other Hub tools (/tools/seo-images, /tools/image-creator, /tools/bg-remover,
/tools/utm — see smart1-hub-v1.6.0-handoff.md).

Usage from the main Hub app.py:

    from modules.commercial_builder import register_commercial_builder
    register_commercial_builder(app)

That single call creates all database tables (first run only — safe to call
every boot, it no-ops if they already exist) and mounts every page + API
route under one blueprint.
"""

from flask import Blueprint

from .db import db, STANDALONE
from .routes import clients, projects, scripts, stock, voices, heygen, render, assets, pages

# Activity logging — so this tool's output appears on the client's
# 360 record. This module produces client commercials, and work that isn't logged is work
# nobody can point to later.
try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001
    def _audit(*a, **k):  # no-op outside the Hub
        return None



def create_blueprint():
    bp = Blueprint(
        "commercial_builder", __name__,
        url_prefix="/tools/commercial-builder",
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    bp.register_blueprint(pages.bp)
    bp.register_blueprint(clients.bp)
    bp.register_blueprint(projects.bp)
    bp.register_blueprint(scripts.bp)
    bp.register_blueprint(stock.bp)
    bp.register_blueprint(voices.bp)
    bp.register_blueprint(heygen.bp)
    bp.register_blueprint(render.bp)
    bp.register_blueprint(assets.bp)
    return bp


def register_commercial_builder(app):
    if STANDALONE:
        db.init_app(app)
    app.register_blueprint(create_blueprint())
    if STANDALONE:
        with app.app_context():
            db.create_all()
    return app
