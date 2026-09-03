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
from .routes import (audio, clients, projects, scripts, stock, voices, heygen, render, assets,
                     providers, pages, review)

# Login. This module is blueprint-registered on the hub app, NOT dispatcher-
# mounted, so wsgi.py's AuthGuard never sees it — that wrapper only covers
# modules mounted under a URL prefix. Nothing else was guarding it either, so
# every page and every API route here answered 200 to anyone with the URL,
# serving client names, briefs and projects without a login. hub/auth.py names
# this exact failure ("modules ... fall back to a no-op when it's missing —
# which silently serves an admin page to anyone").
#
# The guard goes on the blueprint rather than on each view: there are ~40
# routes across nine route modules and the next one added must not have to
# remember. Standalone (outside the Hub) there is no Hub cookie to check, and
# the module is then only reachable on a developer's own machine.
try:
    from hub import auth as _hub_auth
except ImportError:  # noqa: BLE001 — standalone development, no Hub to log into
    _hub_auth = None

# Activity logging — so this tool's output appears on the client's
# 360 record. This module produces client commercials, and work that isn't logged is work
# nobody can point to later.
try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001
    def _audit(*a, **k):  # no-op outside the Hub
        return None



def _install_login_guard(bp):
    """One check in front of every route on this blueprint.

    Except the client review link. A client has no Hub account at all, so a
    guard in front of that page is a login form shown to somebody who cannot
    fill it in — and the rep then emails the file instead, which loses the
    record the whole review feature exists to keep.

    The exempt list is `routes/review.PUBLIC_PATHS`, read from there rather
    than restated here: a route added to that file must not be able to be
    public in one place and refused in the other. `modules/ads_builder` and
    `modules/scans` get this from `wsgi.py`'s `PUBLIC_PREFIXES`, which is
    handed to `AuthGuard` by `_mount()` — and this module is a blueprint on
    the hub app rather than a mounted one, so nothing in `wsgi.py` ever sees
    it. That difference is the whole reason a guard is needed here at all.

    The guard itself is `hub/blueprint_guard.py`. It was written out in this
    file first and then copied into `modules/calculators`, and three further
    blueprints turned out to need it — so it is shared now, and the JSON-401
    this file worked out went into it rather than being left behind here.
    `public` there is matched under the mount, so moving the mount cannot
    silently un-publish the review page, and `/review/` keeps its trailing
    slash so a route called `/reviewers` is not accidentally public.

    Exempting it from the login is only half. The hub app's own
    `after_request` injects the sidebar, the help layer and the feedback tab
    into any HTML it returns, so the path is in `CHROMELESS` in
    `hub/__init__.py` too; without that the client gets the staff nav.
    """
    if _hub_auth is None:
        return
    from hub.blueprint_guard import install as _install_guard
    _install_guard(bp, mount=bp.url_prefix or "",
                   public=tuple(review.PUBLIC_PATHS))


def create_blueprint():
    bp = Blueprint(
        "commercial_builder", __name__,
        url_prefix="/tools/commercial-builder",
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    _install_login_guard(bp)
    bp.register_blueprint(pages.bp)
    bp.register_blueprint(clients.bp)
    bp.register_blueprint(projects.bp)
    bp.register_blueprint(scripts.bp)
    bp.register_blueprint(stock.bp)
    bp.register_blueprint(voices.bp)
    bp.register_blueprint(heygen.bp)
    bp.register_blueprint(render.bp)
    bp.register_blueprint(assets.bp)
    bp.register_blueprint(audio.bp)
    bp.register_blueprint(providers.bp)
    bp.register_blueprint(review.bp)
    return bp


def register_commercial_builder(app):
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
