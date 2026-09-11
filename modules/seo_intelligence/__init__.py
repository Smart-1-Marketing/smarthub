"""Smart 1 Hub SEO Intelligence.

Shared weekly Google Search Console memory, recommendation engine, and AI
context provider. Every AI feature can import ``get_seo_context`` or
``as_prompt_block`` without making its own Search Console calls.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
__version__ = "0.1.0"


def register_seo_intelligence(app, create_tables=True):
    from .app import bp
    from .actions import bp as actions_bp
    from .web import bp as web_bp

    if "seo_intelligence" not in app.blueprints:
        app.register_blueprint(bp)
    if "seo_intelligence_actions" not in app.blueprints:
        app.register_blueprint(actions_bp)
    if "seo_intelligence_web" not in app.blueprints:
        app.register_blueprint(web_bp)

    if create_tables:
        try:
            # Import models before create_all so their metadata is registered.
            from . import models  # noqa: F401
            from hub.extensions import create_all
            err = create_all(app)
            if err:
                app.config["SEO_INTELLIGENCE_DB_BOOT_ERROR"] = err
                log.warning("seo_intelligence: create_all reported %s", err)
        except Exception as exc:  # never take SmartHub down for one tool
            app.config["SEO_INTELLIGENCE_DB_BOOT_ERROR"] = str(exc)
            log.warning("seo_intelligence: create_all failed: %s", exc)

    # Check once per day, while the job itself refreshes only clients due for
    # their weekly snapshot. This is more reliable than a 7-day timer because
    # deploys/restarts do not postpone the next refresh by another full week.
    try:
        from hub import scheduler
        from .scheduler import job_refresh_seo_intelligence
        scheduler.JOBS.setdefault(
            "seo_intelligence",
            (1440, job_refresh_seo_intelligence,
             "Refresh due weekly Search Console intelligence for active SEO clients."),
        )
    except Exception as exc:
        log.warning("seo_intelligence: scheduler registration failed: %s", exc)

    # Production default: use the encrypted refresh-token store that Google
    # Finder already owns. The app hook remains replaceable for tests/future
    # credential providers.
    if not callable(app.config.get("SEO_GSC_TOKEN_PROVIDER")):
        try:
            from .tokens import token_for_property
            app.config["SEO_GSC_TOKEN_PROVIDER"] = token_for_property
        except Exception:
            pass
    return app


from .context import get_seo_context, as_prompt_block  # noqa: E402,F401
