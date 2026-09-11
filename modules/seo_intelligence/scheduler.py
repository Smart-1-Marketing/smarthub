"""Scheduler integration for weekly SEO intelligence refreshes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .file_store import mirror_client
from .models import SEOProperty
from .service import refresh_property
from .tokens import token_for_property


def job_refresh_seo_intelligence(app):
    """Refresh active SEO clients that have not had a successful sync in 6 days.

    The Hub scheduler already guarantees one leader worker. The 6-day due
    threshold makes this safe around deploys while still producing a weekly
    snapshot even if the exact scheduled tick drifts. Each successful refresh
    also regenerates the client's compact SEO intelligence JSON file for reuse
    by AI tools throughout SmartHub.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=6)
    refreshed = 0
    skipped = 0
    files_written = 0
    errors = []
    with app.app_context():
        properties = SEOProperty.query.filter_by(active=True).all()
        for prop in properties:
            last = prop.last_sync_at
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last and last >= cutoff and prop.last_sync_status == "ok":
                skipped += 1
                # Keep the disk handoff self-healing after a deploy/disk restore.
                if mirror_client(prop.client_id):
                    files_written += 1
                continue
            try:
                token = token_for_property(prop)
                refresh_property(prop, token)
                if mirror_client(prop.client_id):
                    files_written += 1
                refreshed += 1
            except Exception as exc:
                errors.append({"property_id": prop.id, "site_url": prop.site_url, "error": f"{type(exc).__name__}: {exc}"[:500]})
    return {
        "refreshed": refreshed,
        "skipped_not_due": skipped,
        "files_written": files_written,
        "errors": errors,
        "active_properties": refreshed + skipped + len(errors),
    }
