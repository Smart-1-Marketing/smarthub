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

Five things it holds:

* the snapshot is keyed on the month the Hub is in, not the export's;
* a second month therefore resolves, and each comparison names the month it
  is against — a comparison that will not say what it compared to is one
  nobody can check;
* a month nobody recorded is rebuilt from the insertion-order dates, so last
  month and last year answer today rather than in a year's time;
* the rebuild is Knack's own definition of a live month and not a new one —
  reconstructing the two months Knack flags itself (`thisM` / `lastM`)
  reproduces both exactly, which is the check that makes the rest of it
  trustworthy;
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
    check("website movement has no earlier month either",
          s1["website_movement"], None)

    print()
    print("with no reading of last month, it is rebuilt from the IO dates")
    lm = s1["trends"]["clients_live"]["last_month"]
    ok("last month answers on the first day the Hub is opened", lm["available"],
       "this is the whole complaint: every card read '– vs last mo'")
    check("and says how it was measured", lm["basis"], "io_terms")
    check("naming the month it is against", lm["period"], "Jul 2026")
    ly = s1["trends"]["live_budget_monthly"]["last_year"]
    ok("so does the same month last year", ly["available"])
    check("on the same basis", ly["basis"], "io_terms")
    check("and it names that month too", ly["period"], "Aug 2025")

    print()
    print("both ends of a rebuilt comparison are rebuilt, never mixed")
    ok("it carries the value it measured now, not the headline",
       lm["now"] == knack_data.period_totals("202608")["clients_live"]
       and lm["now"] != s1["clients_live"],
       "the headline counts every IO still marked Live whatever its dates say")
    check("and the movement is the difference between the two",
          lm["diff"], lm["now"] - lm["from"])

    print()
    print("the rebuild is Knack's own definition of a live month")
    # The export flags each row lastM / thisM itself. Rebuilding those two
    # months from the start and end dates has to reproduce them exactly, or
    # this is a new definition of "live" wearing Knack's clothes.
    rows = knack_data.products()

    def flagged(flag):
        sel = [r for r in rows if r.get(flag)]
        return {
            "clients_live": len({str(r.get("client", "")).strip()
                                 for r in sel if r.get("client")}),
            "live_products": len(sel),
            "live_budget_monthly": round(sum(knack_data._num(r.get("monthly"))
                                             for r in sel)),
        }

    check("this month matches Knack's own thisM flag",
          knack_data.period_totals("202608"), flagged("thisM"))
    check("last month matches Knack's own lastM flag",
          knack_data.period_totals("202607"), flagged("lastM"))

    print()
    print("a month outside the book is not a month with nothing in it")
    check("no IO ran in 1999, so there is nothing to report",
          knack_data.period_totals("199901"), None)
    old = knack_data._compare("live_products", {"live_products": 1}, {},
                              "199901", "202608")
    ok("and it comes back not measured, never a 100% collapse",
       not old["available"])
    ok("saying why", "no insertion order" in old["why"])

    print()
    print("a website metric has nothing to rebuild from, and says so")
    wm = s1["trends"]["websites_active"]["last_month"]
    ok("so it stays not measured", not wm["available"])
    ok("and names the reason rather than the month alone",
       "website export carries no dates" in wm["why"])

    print()
    print("next month, the comparison resolves")
    at("202609")
    s2 = knack_data.summary()
    hist = jsonstore.read_json(knack_data._history_path(), default={})
    check("two buckets now", sorted(hist), ["202608", "202609"])
    lm = s2["trends"]["clients_live"]["last_month"]
    ok("last month is available", lm["available"],
       "this is the bug: it was never available, on any card, ever")
    check("a recorded reading outranks a rebuild", lm["basis"], "snapshot")
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
    check("and on the reading that was taken, not a rebuild", ly["basis"], "snapshot")
    check("the month nobody opened the Hub in is still named",
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
