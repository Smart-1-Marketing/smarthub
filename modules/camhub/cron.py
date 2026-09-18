"""The scheduled refresh, as a job for hub/scheduler.py.

The spec asks for Render cron entries so a restart never silently stops the
refresh. This repo runs its background work in `hub/scheduler.py` under a
leader lock instead -- two gunicorn workers, one leader, and a Diagnostics
panel that shows every job's last run, failure streak and a Run now button
-- and a second scheduling mechanism for one module would be a second place
to look when the page goes stale. So one job, every five minutes, that asks
the store for every source whose own cadence has elapsed. Weather lands
every ten minutes, the gauge every fifteen, the beach hourly, the pool
table once a year, all from the same tick.

Every outcome is written to the source row, so the health screen and not the
scheduler log is where a dead feed shows.
"""
from __future__ import annotations


def job_refresh(app=None) -> dict:
    try:
        from . import store
        from .models import boot_error, init_db
    except Exception as exc:  # noqa: BLE001
        return {"skipped": f"unavailable ({type(exc).__name__})"}
    init_db()
    err = boot_error()
    if err:
        return {"skipped": f"database: {err[:120]}"}
    try:
        return store.refresh_due()
    except Exception as exc:  # noqa: BLE001 -- reported as a failed run, never raised into the loop
        return {"error": f"{type(exc).__name__}: {exc}"}


def job_rollup(app=None) -> dict:
    """Today's and yesterday's impressions and clicks into camhub_daily_stats,
    then the ninety-day purge of raw events. Hourly, so the staff screens
    read near-live; a day is final once it is two days old."""
    try:
        from . import tracking
        from .models import boot_error, init_db
    except Exception as exc:  # noqa: BLE001
        return {"skipped": f"unavailable ({type(exc).__name__})"}
    init_db()
    err = boot_error()
    if err:
        return {"skipped": f"database: {err[:120]}"}
    try:
        return tracking.rollup_recent()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
