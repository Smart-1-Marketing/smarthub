"""hub/nightly.py — one nightly-window helper, and why it reads a real clock.

    python3 test_nightly.py

No pytest, no new dependencies, no data directory: this module does no I/O
beyond reading an environment variable, so nothing here touches /var/data or
a database.

## Why this file exists

`hub/domain_purchase.py` picked a fixed UTC hour and described it in a
comment as "around 3-4am US Eastern" — because a fixed UTC offset cannot
mean a fixed *local* hour across a Daylight Saving change. hub/nightly.py
reads the actual Eastern wall clock instead, which is the one thing worth
proving here: a "2am Eastern" job has to land at 2am Eastern whether the
date is in January or in July, not drift an hour when the clocks change.

  1. hour_for reads the env var, clamps it, and never raises on garbage
  2. last_window is 2am *Eastern*, not 2am UTC — proven across a DST change
  3. a UTC "now" the wrong side of midnight Eastern still finds the right day
  4. due() — never run, run before the window, run inside it, and a clock
     that has gone backwards are four different answers
  5. a naive datetime is read as UTC, not as whatever the caller meant
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


import os                                                       # noqa: E402

from hub import nightly                                         # noqa: E402

EASTERN = ZoneInfo("America/New_York")


section("hour_for reads the env var, clamped, never raising on garbage")

os.environ.pop("NIGHTLY_TEST_HOUR", None)
check("with nothing set, the default", nightly.hour_for("NIGHTLY_TEST_HOUR"), 2)
check("...or a caller's own default", nightly.hour_for("NIGHTLY_TEST_HOUR", 5), 5)

os.environ["NIGHTLY_TEST_HOUR"] = "3"
check("a real value wins", nightly.hour_for("NIGHTLY_TEST_HOUR"), 3)

os.environ["NIGHTLY_TEST_HOUR"] = "not a number"
check("garbage falls back to the default rather than crashing",
      nightly.hour_for("NIGHTLY_TEST_HOUR"), 2)

os.environ["NIGHTLY_TEST_HOUR"] = "99"
check("an out-of-range hour is clamped, not wrapped",
      nightly.hour_for("NIGHTLY_TEST_HOUR"), 23)

os.environ["NIGHTLY_TEST_HOUR"] = "-5"
check("...on both ends", nightly.hour_for("NIGHTLY_TEST_HOUR"), 0)
os.environ.pop("NIGHTLY_TEST_HOUR", None)


section("last_window is 2am Eastern, not 2am UTC")

# January: Eastern is UTC-5 (EST). 2am EST is 7am UTC.
winter_noon_utc = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
w = nightly.last_window(winter_noon_utc, 2)
check("the window is 2am in New York...", w.hour, 2)
check("...on the day it was asked about", w.date(), winter_noon_utc.date())
check("...which in January is 7am UTC",
      w.astimezone(timezone.utc).hour, 7)

# July: Eastern is UTC-4 (EDT). 2am EDT is 6am UTC. A fixed-UTC-hour scheme
# tuned for January would answer 7am UTC here too — an hour off the clock
# Smart 1 actually keeps in summer.
summer_noon_utc = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
s = nightly.last_window(summer_noon_utc, 2)
check("the same wall-clock hour in July...", s.hour, 2)
check("...lands on a different UTC hour, because the clocks changed",
      s.astimezone(timezone.utc).hour, 6)
check("...never the January offset applied to a July date",
      s.astimezone(timezone.utc).hour != w.astimezone(timezone.utc).hour, True)


section("a UTC 'now' the wrong side of midnight Eastern still finds today")

# 1am UTC is 8pm the *previous* day in New York (EDT, summer) — so the most
# recent 2am-Eastern window is yesterday's, not today's.
early_utc = datetime(2026, 7, 16, 1, 0, tzinfo=timezone.utc)
local = early_utc.astimezone(EASTERN)
check("sanity: 1am UTC really is the evening before, in New York",
      local.date(), (early_utc - timedelta(days=1)).date())
w2 = nightly.last_window(early_utc, 2)
check("the window found is yesterday's 2am, not a 2am that has not "
      "happened yet today", w2.date(), local.date())
check("...and it is still 2am on the Eastern clock", w2.hour, 2)


section("next_window is the window after the most recent one")

nxt = nightly.next_window(winter_noon_utc, 2)
check("one day after the last window", nxt.date() - w.date(), timedelta(days=1))
check("...at the same Eastern hour", nxt.hour, 2)
check("...and it is genuinely in the future", nxt > winter_noon_utc, True)


section("due() tells four situations apart")

now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
check("never run is always due",
      nightly.due(None, env_var="NIGHTLY_TEST_HOUR", now=now), True)

just_after_window = nightly.last_window(now, 2) + timedelta(minutes=5)
check("run just inside tonight's window is not due again",
      nightly.due(just_after_window, env_var="NIGHTLY_TEST_HOUR", now=now), False)

before_window = nightly.last_window(now, 2) - timedelta(minutes=5)
check("run just before the window opened is due",
      nightly.due(before_window, env_var="NIGHTLY_TEST_HOUR", now=now), True)

yesterday = now - timedelta(days=2)
check("a run two days old is due", nightly.due(
    yesterday, env_var="NIGHTLY_TEST_HOUR", now=now), True)

# Too often is recoverable; never running again is not — the same rule
# hub/google_index.due_for_refresh() states for a clock that runs backwards.
future = now + timedelta(days=5)
check("a 'last success' in the future (a restored snapshot, a moved clock) "
      "is due, not treated as fresh for the next five days",
      nightly.due(future, env_var="NIGHTLY_TEST_HOUR", now=now), True)


section("a naive datetime is read as UTC, never as local time by accident")

naive_now = datetime(2026, 6, 1, 12, 0)             # no tzinfo
aware_now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
check("last_window treats a naive 'now' as UTC",
      nightly.last_window(naive_now, 2), nightly.last_window(aware_now, 2))

naive_success = nightly.last_window(aware_now, 2).astimezone(timezone.utc
                                                              ).replace(tzinfo=None)
check("and a naive 'last success' the same way",
      nightly.due(naive_success, env_var="NIGHTLY_TEST_HOUR", now=aware_now),
      nightly.due(naive_success.replace(tzinfo=timezone.utc),
                  env_var="NIGHTLY_TEST_HOUR", now=aware_now))


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
