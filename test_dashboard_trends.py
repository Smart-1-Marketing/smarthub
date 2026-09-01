"""The dashboard's monthly readings, and why no comparison is drawn from them.

    python3 test_dashboard_trends.py

There were month-over-month comparisons under each headline number, and they
are gone. This file is what keeps that a decision rather than a regression.

## What happened

The snapshot history was keyed on products.json's `thisMonth` — a committed
export refreshed by hand, carrying one value since the day it was generated.
Every load wrote into that one bucket, a second bucket could never appear, and
every card read "– vs last mo – vs last yr" for ever. Keying it on the
calendar fixed that and, on its own, still showed a dash on every card: the
first reading is taken the month the Hub is opened.

The missing months were then rebuilt from the export's insertion-order dates,
which reproduced Knack's own `thisM` / `lastM` flags exactly — and that was
still removed. `is_running` is deliberately a **union**: an IO counts if its
term covers today *or* Knack still calls it Live, which takes in about 140
month-to-month rows nobody has closed out. A term rebuild cannot see those, so
the rebuilt month and the headline above it were measured differently and the
percentage could not be reproduced from the two numbers on the card. A figure
nobody can check is worse than no figure.

## What this file holds

* the reading is taken, every month, keyed on the calendar — that is the only
  thing that can ever produce a comparison measured the same way at both ends,
  and it cannot be taken retrospectively;
* past months keep the value they were given, and a new month is a new bucket;
* nothing on the scorecard claims a comparison — `summary()` carries no
  `trends`, and the template renders none;
* Website Movement, which is snapshot-derived and was never rebuilt, still
  resolves and still names the month it compared against;
* the export-derived counts (new / lost / up / down) say when they are from a
  month that has passed, instead of reading as this month's movement.

No pytest and no network — it points the store at a temporary directory and
moves the clock by hand.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="hub-trends-")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["CLIENTS_DATA_DIR"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures", "clients"
)
# Assigned, never setdefault: a fresh HUB_DATA_DIR is not
# isolation on its own. jsonstore keys its mirror *relative to
# the data root* by design -- so a production blob restores
# into a dev checkout -- which means an inherited DATABASE_URL
# (CI's Postgres, or a developer's own) refills this run's
# empty directory with the last run's rows. Owning both is
# what makes "throwaway" true, and what makes the file safe to
# run twice; test_blog_publish.py is the same pattern.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "test-not-a-secret")

from hub import knack_data                                          # noqa: E402

FAILURES = []


def check(label, got, want):
    ok_ = got == want
    print(f"  {'ok  ' if ok_ else 'FAIL'}  {label}: {got!r}")
    if not ok_:
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
    from hub import jsonstore

    print("the reading is keyed on the month the Hub is in, not the export's")
    at("202608")
    s1 = knack_data.summary()
    hist = jsonstore.read_json(knack_data._history_path(), default={})
    check("one bucket, and it is this month", sorted(hist), ["202608"])
    check("carrying every metric worth a reading",
          sorted(hist["202608"]), sorted(knack_data.TRENDED))
    check("website movement has no earlier month to compare against yet",
          s1["website_movement"], None)

    print()
    print("nothing on the scorecard claims a comparison")
    ok("summary() carries no trends", "trends" not in s1,
       "the rebuilt comparison was measured differently from the number it "
       "sat under, and could not be reproduced from the card")
    dash = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "hub", "templates", "dashboard.html"),
                encoding="utf-8").read()
    ok("and the dashboard renders none", "k-trend" not in dash)
    ok("nor the wording that explained the rebuild",
       "rebuilt from the insertion-order" not in dash)

    print()
    print("next month is a new bucket, and last month keeps its value")
    at("202609")
    knack_data.summary()
    hist = jsonstore.read_json(knack_data._history_path(), default={})
    check("two buckets now", sorted(hist), ["202608", "202609"])
    check("August still says what August said",
          hist["202608"]["live_products"], s1["live_products"])
    ok("which is the whole point of taking it — it cannot be taken later",
       hist["202609"]["live_products"] == s1["live_products"])

    print()
    print("website movement resolves, and names the month it used")
    s2 = knack_data.summary()
    check("it compares against the month it holds", s2["website_movement"], 0)
    check("and says which one", s2["website_movement_from"], "Aug 2026")

    at("202612")
    s3 = knack_data.summary()
    check("after a gap it names the newest month it holds, not 'last month'",
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
    print("the readings accumulate, and no card claims a comparison it cannot "
          "show the arithmetic for")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
