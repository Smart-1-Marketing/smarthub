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

__version__ = "1.2.0"


_QA_GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/analytics.edit",
    "https://www.googleapis.com/auth/tagmanager.delete.containers",
    # Google Finder already requests webmasters for Search Console. Keeping it
    # here makes the dependency explicit for new/reconnected agency logins used
    # by SEO Intelligence, including sitemap actions.
    "https://www.googleapis.com/auth/webmasters",
)


def _install_qa_google_scopes():
    """Make Google Finder request the permissions Hub Google tools need.

    Existing refresh tokens keep their old grants until that Google login is
    reconnected; the screens detect that case and explain it. New/reconnected
    logins request these scopes alongside Google Finder's existing read scopes.
    """
    try:
        from modules.google_finder import app as gf
        for scope in _QA_GOOGLE_SCOPES:
            if scope not in gf.SCOPES:
                gf.SCOPES.append(scope)
    except Exception as exc:  # noqa: BLE001 -- never make Hub startup depend on this
        log.warning("google_access: could not extend Google Finder scopes: %s", exc)


def register_google_access(app, create_tables=True):
    """Attach Google Access, its QA screen, and shared SEO Intelligence.

    Never raises; a bad DB or one optional tool must not take the Hub down.
    """
    from .app import admin_bp, public_bp
    from .qa_inactive import qa_bp
    from .models import db

    _install_qa_google_scopes()

    if "google_access_public" not in app.blueprints:
        app.register_blueprint(public_bp)
        app.register_blueprint(admin_bp)

        # Tell the Hub's AuthGuard this prefix is intentionally open.
        exempt = app.config.setdefault("AUTH_EXEMPT_PREFIXES", [])
        if "/connect" not in exempt:
            exempt.append("/connect")

    if "google_inactive_qa" not in app.blueprints:
        app.register_blueprint(qa_bp)

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

    # SEO Intelligence shares the same Google connection lifecycle and is
    # mounted here so the Hub app factory does not need another fragile manual
    # registration entry. Failure is isolated like every other optional tool.
    try:
        from modules.seo_intelligence import register_seo_intelligence
        register_seo_intelligence(app, create_tables=create_tables)
    except Exception as exc:  # noqa: BLE001
        app.config["SEO_INTELLIGENCE_BOOT_ERROR"] = str(exc)
        log.warning("google_access: SEO Intelligence unavailable: %s", exc)

    return app
