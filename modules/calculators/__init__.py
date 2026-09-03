"""Smart 1 Hub — Media Calculators module.

Register from the Hub's app factory or wsgi.py:

    from modules.calculators import register_calculators
    register_calculators(app)

Mounts at /tools/calculators/. See README.md for the AuthGuard exemption the
public calculator routes need.
"""

from .app import MOUNT, PUBLIC_EXCLUDED, PUBLIC_PREFIXES, bp
from . import catalog, store

__all__ = ["register_calculators", "MOUNT", "PUBLIC_PREFIXES", "PUBLIC_EXCLUDED",
           "catalog", "store"]


def register_calculators(app, url_prefix=MOUNT):
    app.config.setdefault("CALCULATORS_DATA_DIR", app.config.get("DATA_DIR", "data"))
    app.config.setdefault("CALCULATORS_LEAD_WEBHOOK_URL", "")
    store.init(app)
    if "calculators" not in app.blueprints:
        app.register_blueprint(bp, url_prefix=url_prefix)
    return app


def public_paths(url_prefix=MOUNT):
    """Paths the Hub AuthGuard must let through unauthenticated."""
    return [url_prefix.rstrip("/") + p for p in PUBLIC_PREFIXES]


def public_excluded(url_prefix=MOUNT):
    """The staff paths hiding under those prefixes, absolute. One list, read
    by the login gate on the blueprint and by hub/suite_embed.py's framing
    allowlist, so the two cannot disagree about which route is staff-only."""
    return [url_prefix.rstrip("/") + p for p in PUBLIC_EXCLUDED]
