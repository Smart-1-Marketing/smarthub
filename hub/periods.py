"""Named reporting periods, resolved in Python so no model ever invents a date.

Ask SmartHub's planner used to be asked for ISO dates. A model that is asked
for a date will always produce one, and the ones it produces are plausible
rather than measured -- "last month" landing on the wrong month boundary, a
quarter that starts in the wrong place, a range that quietly includes today.
The fix is to take the arithmetic away from it: the planner picks a *name*
from a fixed list and this module turns the name into days.

All periods are **complete days**: today is excluded, matching the LSA and
YouTube Ads report boundaries already in the Hub, and matching the fact table,
which is still filling today's row while today is running. Account-timezone
nuance is the caller's problem -- `modules/reports` stores a fact row under
the platform-local date the platform reported it under.

`label` is the human sentence every tool echoes back, so an answer can always
say which window it read rather than leaving the reader to assume.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

# Every period a caller may name. A caller naming anything else gets a
# ValueError, which the tool turns into an error payload -- never a guess at
# what they might have meant.
PERIODS = ("last_7", "last_14", "last_30", "last_90",
           "this_month", "last_month", "this_quarter", "last_quarter",
           "this_year", "last_year", "custom")
DEFAULT_PERIOD = "last_30"

COMPARES = ("previous_period", "same_period_last_year", "none")
DEFAULT_COMPARE = "previous_period"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# What each period is called in the sentence a person reads. "to date" is
# said out loud on the periods that are still running, because a month that
# is half over and a month that is finished are different numbers and a
# reader who cannot tell them apart will compare one against the other.
_LABELS = {
    "last_7": "last 7 days", "last_14": "last 14 days",
    "last_30": "last 30 days", "last_90": "last 90 days",
    "this_month": "this month to date", "last_month": "last month",
    "this_quarter": "this quarter to date", "last_quarter": "last quarter",
    "this_year": "this year to date", "last_year": "last year",
    "custom": "custom range",
}


def _day(d: date) -> str:
    return f"{_MONTHS[d.month - 1]} {d.day}"


def human_range(start: date, end: date) -> str:
    """"Sep 1 - Sep 15, 2026", or one date when the window is a single day."""
    if start == end:
        return f"{_day(start)}, {start.year}"
    if start.year != end.year:
        return f"{_day(start)}, {start.year} - {_day(end)}, {end.year}"
    return f"{_day(start)} - {_day(end)}, {end.year}"


@dataclass(frozen=True)
class Window:
    start: date
    end: date            # inclusive
    label: str
    period: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict:
        return {"period": self.period, "label": self.label,
                "start": self.start.isoformat(), "end": self.end.isoformat(),
                "days": self.days}


def _iso(value: str, field: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"A custom period needs {field}.")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD).") from None


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def resolve(period: str = DEFAULT_PERIOD, *, today: date | None = None,
            start: str = "", end: str = "") -> Window:
    """One named period as a concrete, inclusive window of complete days.

    `custom` requires ISO `start`/`end`; every other period ignores them, so a
    caller that sends both a name and stale dates gets the name it asked for
    rather than a silent blend of the two.

    Raises ValueError on an unknown period, a custom range missing a date, an
    inverted custom range, or a custom range that reaches into the future.
    The caller turns that into an `{"error": ...}` payload, never a guess.
    """
    name = str(period or DEFAULT_PERIOD).strip().lower() or DEFAULT_PERIOD
    if name not in PERIODS:
        raise ValueError(
            f"Unknown period {name!r}. Use one of: {', '.join(PERIODS)}.")
    today = today or date.today()
    yesterday = today - timedelta(days=1)

    if name == "custom":
        first, last = _iso(start, "start_date"), _iso(end, "end_date")
        if first > last:
            raise ValueError("start_date is after end_date.")
        if last >= today:
            # Today's row is still being written. A caller who asks for it
            # gets told, rather than getting a part-day counted as a day.
            raise ValueError(
                "A custom range must end before today; today is still incomplete.")
        return Window(first, last, f"{human_range(first, last)} (custom range)", name)

    if name.startswith("last_") and name.split("_")[-1].isdigit():
        span = int(name.split("_")[-1])
        first = yesterday - timedelta(days=span - 1)
        return Window(first, yesterday,
                      f"{human_range(first, yesterday)} ({_LABELS[name]})", name)

    if name == "this_month":
        first = today.replace(day=1)
    elif name == "last_month":
        last_of_prev = today.replace(day=1) - timedelta(days=1)
        first = last_of_prev.replace(day=1)
        return Window(first, last_of_prev,
                      f"{human_range(first, last_of_prev)} ({_LABELS[name]})", name)
    elif name == "this_quarter":
        first = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    elif name == "last_quarter":
        q_first = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
        last_of_prev = q_first - timedelta(days=1)
        first = date(last_of_prev.year, 3 * ((last_of_prev.month - 1) // 3) + 1, 1)
        return Window(first, last_of_prev,
                      f"{human_range(first, last_of_prev)} ({_LABELS[name]})", name)
    elif name == "this_year":
        first = date(today.year, 1, 1)
    else:  # last_year
        first, last = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
        return Window(first, last,
                      f"{human_range(first, last)} ({_LABELS[name]})", name)

    # The to-date periods. On the first of a month, quarter or year the
    # window would start after it ended: that is a real state (nothing
    # complete has happened yet in this one) and it reads as a single day --
    # yesterday -- rather than as an inverted range no reader can interpret.
    if first > yesterday:
        first = yesterday
    return Window(first, yesterday,
                  f"{human_range(first, yesterday)} ({_LABELS[name]})", name)


def _shift_year(d: date, years: int = 1) -> date:
    """One year back, with Feb 29 landing on Feb 28 rather than raising."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:                       # Feb 29 in a non-leap year
        return d.replace(year=d.year - years, day=28)


def compare_window(win: Window, mode: str = DEFAULT_COMPARE) -> Window | None:
    """The window a period is measured against, or None.

    `previous_period` is the same number of days ending the day before the
    window starts -- the same length, so a 31-day month is never compared
    against a 30-day one without saying so. `same_period_last_year` shifts
    the whole window back a year. `none` is None.
    """
    name = str(mode or DEFAULT_COMPARE).strip().lower() or DEFAULT_COMPARE
    if name == "none":
        return None
    if name not in COMPARES:
        raise ValueError(
            f"Unknown compare {name!r}. Use one of: {', '.join(COMPARES)}.")
    if name == "previous_period":
        end = win.start - timedelta(days=1)
        start = end - timedelta(days=win.days - 1)
        return Window(start, end,
                      f"{human_range(start, end)} (previous period)", "custom")
    start, end = _shift_year(win.start), _shift_year(win.end)
    return Window(start, end,
                  f"{human_range(start, end)} (same period last year)", "custom")


def pct_change(current, previous) -> float | None:
    """The percent move between two measured figures, or None.

    None -- never zero, never infinity -- when there is nothing to divide by.
    A metric that went from nothing to something did not rise by a
    percentage, and the house rule is that a figure this Hub did not measure
    is not printed as one.
    """
    try:
        now_v, was_v = float(current), float(previous)
    except (TypeError, ValueError):
        return None
    if not was_v:
        return None
    return round((now_v - was_v) / abs(was_v) * 100, 2)
