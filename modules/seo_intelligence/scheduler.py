"""Scheduler integration for SEO intelligence discovery and refreshes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .discovery import discover_and_backfill_existing_clients
from .file_store import mirror_client
from .models import SEOProperty
from .service import refresh_property
from .tokens import token_for_property


def job_refresh_seo_intelligence(app):
    """Backfill current SEO clients, then refresh properties due this week.

    SmartHub's scheduler starts every registered job due after a fresh deploy,
    and only the leader worker executes it. That makes the discovery pass the
    rollout mechanism: existing SEO clients receive their first Search Console
    snapshot on the first scheduler tick after deployment rather than waiting
    for the weekly cadence.

    The discovery pass is idempotent. After the initial backfill, successfully
    synced clients fall through to the normal 6-day threshold below.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=6)
    refreshed = 0
    skipped = 0
    files_written = 0
    errors = []
    with app.app_context():
        try:
            backfill = discover_and_backfill_existing_clients()
        except Exception as exc:
            # Discovery is additive. A temporary Knack/Google problem must not
            # prevent already-registered properties from receiving their due
            # weekly refresh.
            backfill = {
                "active_seo_clients": 0,
                "matched": 0,
                "registered": 0,
                "refreshed_immediately": 0,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }

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
                errors.append({
                    "property_id": prop.id,
                    "site_url": prop.site_url,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                })
    return {
        "backfill": backfill,
        "refreshed": refreshed,
        "skipped_not_due": skipped,
        "files_written": files_written + int(backfill.get("files_written") or 0),
        "errors": errors,
        "active_properties": refreshed + skipped + len(errors),
    }
