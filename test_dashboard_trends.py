"""The dashboard's month-over-month comparisons.

Every scorecard trend on the main dashboard read "– vs last mo – vs last yr"
and always would have, because the snapshot history was keyed on
products.json's `thisMonth` — a committed export refreshed by hand, carrying
one value since the day it was generated. Every load wrote into that one
bucket. A second bucket could never appear, so the comparison could never
resolve, and Website Movement said "building history — check back next month"
in a month that would never come.

The fix is one line of intent — key the history on the calendar — and this
file is what makes it checkable: the claim is "next month it resolves", so
next month is simulated rather than promised.

Three things it holds:

* the snapshot is keyed on the month the Hub is in, not the export's;
* a second month therefore resolves, and each comparison names the month it
  is against — a comparison that will not say what it compared to is one
  nobody can check;
* the export-derived counts (new / lost / up / down) say when they are from a
  month that has passed, instead of reading as this month's movement.

Run directly: ``python3 test_dashboard_trends.py``. No pytest and no network —
it points the store at a temporary directory and moves the clock by hand.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="hub-trends-")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))
os.environ.setdefault("SECRET_KEY", "test-not-a-secret")

from hub import knack_data

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        FAILURES.append(f"{label}: expected {want!r}, got {got!r}")


def ok(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'}  {label}"
          f"{(' — ' + detail) if detail and not condition else ''}")
    if not condition:
        FAILURES.append(f"{label}{(': ' + detail) if detail else ''}")


def at(period):
    """Run the Hub as if it were this month."""
    knack_data._current_period = lambda: period


def main():
    print("the snapshot is keyed on the month the Hub is in")
    at("202608")
    s1 = knack_data.summary()
    from hub import jsonstore
    hist = jsonstore.read_json(knack_data._history_path(), default={})
    check("one bucket, and it is this month", sorted(hist), ["202608"])
    ok("nothing to compare to yet",
       not s1["trends"]["clients_live"]["last_month"]["available"])
    check("but it says which month is missing",
          s1["trends"]["clients_live"]["last_month"]["period"], "Jul 2026")
    check("website movement has no earlier month either",
          s1["website_movement"], None)

    print()
    print("next month, the comparison resolves")
    at("202609")
    s2 = knack_data.summary()
    hist = jsonstore.read_json(knack_data._history_path(), default={})
    check("two buckets now", sorted(hist), ["202608", "202609"])
    lm = s2["trends"]["clients_live"]["last_month"]
    ok("last month is available", lm["available"],
       "this is the bug: it was never available, on any card, ever")
    check("and names the month it compared against", lm["period"], "Aug 2026")
    check("the movement is measured, not guessed", lm["diff"], 0)
    check("website movement resolves too", s2["website_movement"], 0)
    check("and names its month", s2["website_movement_from"], "Aug 2026")

    print()
    print("a year on, both comparisons resolve")
    at("202708")
    s3 = knack_data.summary()
    ly = s3["trends"]["live_budget_monthly"]["last_year"]
    ok("last year is available", ly["available"])
    check("against the right month", ly["period"], "Aug 2026")
    ok("last month is not, and says so",
       not s3["trends"]["live_budget_monthly"]["last_month"]["available"])
    check("naming the month with no snapshot",
          s3["trends"]["live_budget_monthly"]["last_month"]["period"], "Jul 2027")

    print()
    print("a gap does not become a silent 'last month'")
    check("movement compares to the newest month it holds",
          s3["website_movement_from"], "Sep 2026")

    print()
    print("the export's own counts say when they are history")
    at("202608")
    s4 = knack_data.summary()
    ok("in the export's month, they are current", not s4["export_stale"],
       "products.json reports thisMonth=202608")
    at("202611")
    s5 = knack_data.summary()
    ok("three months on, they are not", s5["export_stale"],
       "frozen export counts would read as this month's movement")
    check("and the card can name the month they are from",
          s5["this_period"], "Aug 2026")
    check("while the Hub knows what month it is", s5["period"], "Nov 2026")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("the dashboard comparisons accumulate, resolve, and name their months")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
