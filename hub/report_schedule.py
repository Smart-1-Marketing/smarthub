"""3 AM Eastern reporting refresh, with restart-safe progress and retries.

Called by the existing elected scheduler leader. Manual runs stay independent.
The ledger uses the same persistent disk and database mirror as other Hub state.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
ZONE = ZoneInfo('America/New_York')
RETRY_SECONDS = 30 * 60


def target_day(now):
    local = now.astimezone(ZONE)
    day = local.date()
    if local.hour < 3:
        day -= timedelta(days=1)
    return day.isoformat()


def ready(state, now):
    # Before the first scheduled 3 AM, do not backfill an invented previous run.
    if not state and now.astimezone(ZONE).hour < 3:
        return False
    if state.get('completed_day', '') >= target_day(now):
        return False
    attempt = state.get('attempted_at')
    if attempt:
        try:
            previous = datetime.fromisoformat(attempt)
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            if (now - previous).total_seconds() < RETRY_SECONDS:
                return False
        except (ValueError, TypeError):
            pass
    return True


def successful(run):
    result = run.get('result') or {}
    return bool(run.get('ok') and not result.get('errors')
                and not result.get('pending')
                and any(p.get('ok') for p in result.get('platforms', {}).values()))


def run_due(callback, now=None):
    from . import jsonstore
    now = now or datetime.now(timezone.utc)
    path = os.path.join(jsonstore.data_root(), 'reports-nightly.json')
    try:
        state = jsonstore.read_json(path, {})
        if not isinstance(state, dict):
            state = {}
        if not ready(state, now):
            return False
        day = target_day(now)
        if state.get('progress_day') != day:
            state['progress_day'] = day
            state['completed_platforms'] = []
        done = set(state.get('completed_platforms') or [])
        state['attempted_at'] = now.isoformat()
        if not jsonstore.write_json(path, state):
            raise OSError('could not persist reporting refresh attempt')
        run = callback(sorted(done))
        result = run.get('result') or {}
        done.update(name for name, outcome in result.get('platforms', {}).items()
                    if outcome.get('ok') and not outcome.get('error')
                    and name not in result.get('errors', {}) and not outcome.get('pending'))
        state['completed_platforms'] = sorted(done)
        if successful(run):
            state['completed_day'] = day
        if not jsonstore.write_json(path, state):
            raise OSError('could not persist reporting refresh progress')
        return True
    except Exception:
        # A failed ledger or pull must not stop every other scheduler job.
        log.exception('nightly reporting refresh failed; retry on the next eligible tick')
        return False
