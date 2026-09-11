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

    if "seo_intelligence" not in app.blueprints:
        app.register_blueprint(bp)

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

    # The Hub scheduler is started after modules are mounted. Add one job to
    # its registry so the existing leader lock, status screen and manual-run
    # endpoint handle SEO exactly like the other background work.
    try:
        from hub import scheduler
        from .scheduler import job_refresh_seo_intelligence
        scheduler.JOBS.setdefault(
            "seo_intelligence",
            (10080, job_refresh_seo_intelligence,
             "Refresh weekly Search Console intelligence for active SEO clients."),
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
