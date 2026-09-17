"""The named reporting periods Ask SmartHub's tools resolve dates from.

    python3 test_periods.py

No pytest, no database, no new dependencies: hub/periods.py is pure
arithmetic and every date here is fixed, because a test that moves with the
clock is a test that passes on the day it was written.

What it holds:

  * every named period on a fixed today, to the day, including the rule the
    whole module exists for -- today is never in the window;
  * quarter boundaries on each of the four quarters, and on the first day of
    one, where the to-date window has nothing complete in it yet;
  * the leap day, going back a year from Feb 29;
  * `custom` validation: missing, malformed, inverted, and reaching into
    today, each refused by name rather than guessed at;
  * compare_window for each mode, including that previous_period is the same
    LENGTH as the window it compares -- a 31-day month against a 30-day one
    is the quiet wrong answer this is here to stop;
  * pct_change returning None, never zero and never infinity, when there is
    nothing to divide by.
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from hub import periods                                    # noqa: E402

_passed = _failed = 0


def check(label, got, want=True, note=None):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}"
              + (f"\n          note: {note!r}" if note is not None else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def raises(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
    except ValueError as exc:
        return str(exc)
    return ""


# Wednesday 16 September 2026. Yesterday is the 15th.
T = date(2026, 9, 16)


def win(period, **kw):
    w = periods.resolve(period, today=T, **kw)
    return (w.start.isoformat(), w.end.isoformat(), w.days)


section("Every named period on a fixed today")
check("last_7", win("last_7"), ("2026-09-09", "2026-09-15", 7))
check("last_14", win("last_14"), ("2026-09-02", "2026-09-15", 14))
check("last_30", win("last_30"), ("2026-08-17", "2026-09-15", 30))
check("last_90", win("last_90"), ("2026-06-18", "2026-09-15", 90))
check("this_month runs from the 1st to yesterday",
      win("this_month"), ("2026-09-01", "2026-09-15", 15))
check("last_month is the whole calendar month",
      win("last_month"), ("2026-08-01", "2026-08-31", 31))
check("this_quarter", win("this_quarter"), ("2026-07-01", "2026-09-15", 77))
check("last_quarter is the whole calendar quarter",
      win("last_quarter"), ("2026-04-01", "2026-06-30", 91))
check("this_year", win("this_year"), ("2026-01-01", "2026-09-15", 258))
check("last_year", win("last_year"), ("2025-01-01", "2025-12-31", 365))
check("the default period is last_30",
      periods.resolve(today=T).period, "last_30")

section("Today is never in the window")
for name in periods.PERIODS:
    if name == "custom":
        continue
    check(f"{name} ends before today",
          periods.resolve(name, today=T).end < T)

section("The label says which window was read")
check("this_month reads as a sentence",
      periods.resolve("this_month", today=T).label,
      "Sep 1 - Sep 15, 2026 (this month to date)")
check("last_month is not labeled 'to date'",
      "to date" not in periods.resolve("last_month", today=T).label)
check("a window spanning two years names both",
      periods.resolve("custom", today=date(2026, 3, 1),
                      start="2025-12-30", end="2026-01-02").label,
      "Dec 30, 2025 - Jan 2, 2026 (custom range)")

section("Quarter boundaries")
for month, q_start, prev in ((2, "2026-01-01", ("2025-10-01", "2025-12-31")),
                             (5, "2026-04-01", ("2026-01-01", "2026-03-31")),
                             (8, "2026-07-01", ("2026-04-01", "2026-06-30")),
                             (11, "2026-10-01", ("2026-07-01", "2026-09-30"))):
    day = date(2026, month, 10)
    check(f"this_quarter from month {month} starts {q_start}",
          periods.resolve("this_quarter", today=day).start.isoformat(), q_start)
    got = periods.resolve("last_quarter", today=day)
    check(f"last_quarter from month {month}",
          (got.start.isoformat(), got.end.isoformat()), prev)

section("The first day of a period, where nothing complete has happened yet")
first = date(2026, 7, 1)
q_first = periods.resolve("this_quarter", today=first)
check("this_quarter on its own first day is yesterday alone",
      (q_first.start.isoformat(), q_first.end.isoformat()),
      ("2026-06-30", "2026-06-30"))
check("...and is one day, never an inverted range", q_first.days, 1)
check("this_month on the 1st is yesterday alone",
      periods.resolve("this_month", today=date(2026, 9, 1)).days, 1)
check("this_year on Jan 1 is yesterday alone",
      periods.resolve("this_year", today=date(2026, 1, 1)).start.isoformat(),
      "2025-12-31")

section("custom: validated, never guessed")
check("custom resolves an explicit range",
      win("custom", start="2026-08-03", end="2026-08-09"),
      ("2026-08-03", "2026-08-09", 7))
check("a missing start is refused by name",
      "start_date" in raises(periods.resolve, "custom", today=T, end="2026-08-09"))
check("a missing end is refused by name",
      "end_date" in raises(periods.resolve, "custom", today=T, start="2026-08-03"))
check("a malformed date is refused",
      "ISO date" in raises(periods.resolve, "custom", today=T,
                           start="August 3", end="2026-08-09"))
check("an inverted range is refused",
      "after" in raises(periods.resolve, "custom", today=T,
                        start="2026-08-09", end="2026-08-03"))
check("a range ending today is refused as incomplete",
      "incomplete" in raises(periods.resolve, "custom", today=T,
                             start="2026-09-01", end=T.isoformat()))
check("an unknown period names the ones that work",
      "last_30" in raises(periods.resolve, "last_month_ish", today=T))
check("named periods ignore stray dates rather than blending them",
      win("last_7", start="2020-01-01", end="2020-01-09"),
      ("2026-09-09", "2026-09-15", 7))

section("compare_window")
this_month = periods.resolve("this_month", today=T)
prev = periods.compare_window(this_month, "previous_period")
check("previous_period ends the day before the window starts",
      prev.end.isoformat(), "2026-08-31")
check("...and is the same number of days", prev.days, this_month.days)
check("...and says so in its label", prev.label,
      "Aug 17 - Aug 31, 2026 (previous period)")
last_month = periods.resolve("last_month", today=T)
check("a 31-day month compares against 31 days, not 30",
      periods.compare_window(last_month, "previous_period").days, 31)
yoy = periods.compare_window(this_month, "same_period_last_year")
check("same_period_last_year shifts the whole window back",
      (yoy.start.isoformat(), yoy.end.isoformat()), ("2025-09-01", "2025-09-15"))
check("none is None", periods.compare_window(this_month, "none"), None)
check("the default compare is previous_period",
      periods.compare_window(this_month).label, prev.label)
check("an unknown compare is refused by name",
      "previous_period" in raises(periods.compare_window, this_month, "vs_target"))

section("The leap day")
leap = periods.resolve("custom", today=date(2024, 3, 5),
                       start="2024-02-29", end="2024-02-29")
check("Feb 29 back a year lands on Feb 28",
      periods.compare_window(leap, "same_period_last_year").start.isoformat(),
      "2023-02-28")
feb = periods.resolve("last_month", today=date(2024, 3, 10))
check("February 2024 is 29 days", feb.days, 29)
check("...and its previous period is 29 days too",
      periods.compare_window(feb, "previous_period").days, 29)

section("pct_change never invents a move")
check("a real move", periods.pct_change(110, 100), 10.0)
check("a fall", periods.pct_change(90, 100), -10.0)
check("from zero is not measured", periods.pct_change(50, 0), None)
check("from None is not measured", periods.pct_change(50, None), None)
check("of None is not measured", periods.pct_change(None, 50), None)
check("zero against zero is not measured", periods.pct_change(0, 0), None)
check("a fall to zero is still a real move", periods.pct_change(0, 40), -100.0)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
