"""
Smart 1 Hub -- Google Access.

Lets a client grant Smart 1 Marketing access to their Google properties from
a single link, without ever sharing a password and without us storing a
credential.

Mount from the Hub's app factory:

    from modules.google_access import register_google_access
    register_google_access(app)

Then add `/tools/google-access` to the HubBar and exempt `/connect` from the
AuthGuard -- that prefix is deliberately public and is what clients open.
"""

import logging

log = logging.getLogger(__name__)

__version__ = "1.0.0"


def register_google_access(app, create_tables=True):
    """Attach both blueprints. Never raises; a bad DB must not take the Hub down."""
    from .app import admin_bp, public_bp
    from .models import db

    if "google_access_public" in app.blueprints:
        return app

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)

    # Tell the Hub's AuthGuard this prefix is intentionally open.
    exempt = app.config.setdefault("AUTH_EXEMPT_PREFIXES", [])
    if "/connect" not in exempt:
        exempt.append("/connect")

    if create_tables:
        # Guarded, per the Scans post-mortem: a database slow to wake must not
        # take the module offline for the life of the worker.
        try:
            # Use the Hub's locked helper when available: two gunicorn workers
            # issuing CREATE TABLE at once is what produced the
            # pg_type_typname_nsp_index UniqueViolation on every deploy.
            from hub.extensions import create_all as _hub_create_all
            err = _hub_create_all(app)
            if err:
                app.config["GOOGLE_ACCESS_DB_BOOT_ERROR"] = err
                log.warning("google_access: create_all reported: %s", err)
        except ImportError:
            try:
                with app.app_context():
                    db.create_all()
            except Exception as exc:  # pragma: no cover
                app.config["GOOGLE_ACCESS_DB_BOOT_ERROR"] = str(exc)
                log.warning("google_access: create_all failed, continuing: %s", exc)

    return app
