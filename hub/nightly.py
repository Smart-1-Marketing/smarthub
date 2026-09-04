"""One nightly-window helper, shared by every job that wants "run once after
a fixed hour of the day, whichever timezone Smart 1 actually keeps" rather
than each restating it.

## Why this exists

`hub/domain_purchase.py` had this first — `refresh_hour()`, `_last_window()`,
`due_for_refresh()` — and got the two hard parts right: a redeploy landing
inside the nightly window must still pick the pull up rather than skip a
whole day in silence, and a clock that runs backwards (a restored snapshot,
a machine whose time moved) must read as due rather than as fresh for ever —
too often is recoverable, never running again is not. A second nightly job
would either copy that or invent a worse version of it, which is the drift
`hub/storage.py` and `hub/images.py` exist to stop one shelf over.

## Why a real timezone rather than a fixed UTC hour

The original picked a fixed UTC hour and described it in a comment as
"around 3-4am US Eastern" — because a fixed UTC offset cannot mean a fixed
*local* hour across a Daylight Saving transition, and pinning to the wrong
side of that trade quietly moves a "2am Eastern" job to 1am or 3am Eastern
twice a year, with nothing on any screen saying why the timing drifted.
`zoneinfo.ZoneInfo("America/New_York")` is the standard library's own answer
(Python 3.9+, no new dependency, the house rule) — it reads the actual local
wall clock in the zone Smart 1 operates in, DST included, rather than an
offset somebody chose to approximate it.

## What is deliberately not on this

This decides *when* a job is due, once a day. It does not run anything, log
anything, or know what a caller's "due" means to do about it — that stays
with the caller, the way `hub/domain_purchase.refresh()` decides what a
failed pull does to a stored snapshot and this module has no opinion on it.
And it is only for jobs that genuinely want once-a-day: `hub/google_index.py`
and `hub/knack_products.py` refresh several times a day on purpose, because
a GA4 property or a live insertion order is meant to be findable the same
day it was created — collapsing either onto a nightly-only window would
trade that promise for a schedule nobody asked to give up, so neither reads
this module. A job that wants "no more than once a night" is what this is
for.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

# 2am Eastern: after close of business and before the earliest anyone here
# is reading a screen. Callers override per-job through their own env var.
DEFAULT_HOUR = 2


def hour_for(env_var: str, default: int = DEFAULT_HOUR) -> int:
    """The configured hour (0-23, Eastern, DST-aware), read from `env_var`.

    Never raises on a bad value — a typo'd override must not crash the
    scheduler thread it decides for; it falls back to the default the way
    every other config read in this codebase does.
    """
    try:
        return max(0, min(23, int(os.environ.get(env_var) or default)))
    except (TypeError, ValueError):
        return default


def last_window(now: datetime, hour: int) -> datetime:
    """The most recent Eastern-time moment the nightly job was due at.

    `now` is converted to Eastern before the hour is compared, so the window
    is genuinely "2am in New York" on both sides of a Daylight Saving
    change — not a fixed offset from UTC that silently drifts an hour twice
    a year.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(EASTERN)
    mark = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    return mark if mark <= local else mark - timedelta(days=1)


def next_window(now: datetime, hour: int) -> datetime:
    """When the window after `last_window` opens — for a status line."""
    return last_window(now, hour) + timedelta(days=1)


def due(last_success: datetime | None, *, env_var: str,
       default_hour: int = DEFAULT_HOUR, now: datetime | None = None) -> bool:
    """Has the nightly window passed since the last successful run?

    `last_success=None` is "never run" — always due, the same reading
    `hub/google_index.py` gives a never-built index. A `last_success` in the
    future (a restored snapshot, a clock that moved) is due too, for the
    stated reason: too often is recoverable, never again is not.
    """
    now = now or datetime.now(timezone.utc)
    if last_success is None:
        return True
    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)
    if last_success > now:
        return True
    hour = hour_for(env_var, default_hour)
    return last_success < last_window(now, hour)
