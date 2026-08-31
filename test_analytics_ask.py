"""July versus June, and the comparison that answered with June.

    python3 test_analytics_ask.py

No pytest, no new dependencies and no network: the model and Google are both
stubbed, because what is worth asserting is what this module does with what
they return.

## Why this file exists

`hub/analytics_ask.py` turns a plain-English question into a GA4 report. Its
docstring says what it replaced and why: a keyword matcher that answered "how
did conversions do in July versus June?" with a thirty-day source/medium
table, *"which is worse than refusing — it answers confidently with the wrong
report."* No test named it, and it was doing the same thing one layer down.

**The comparison was keyed on a string GA4 does not send.** `shape()` decided
which period a row belonged to with `tag.endswith("_1")`. GA4 values the
`dateRange` dimension with the range's **name** where one was given, and only
falls back to `date_range_0` / `date_range_1` where none was — and this
module's own prompt *requires* names: "for a comparison, give exactly two
dateRanges, each with a name". So "July" and "June" both tested false, both
rows landed in the same bucket, and the second overwrote the first.

Dublin at 900 sessions in July against 600 in June rendered as **600**, with
no previous and no change, and the totals row read **"600, up 100% on 0"**.
Every figure on the page wrong, and every one of them a real number from the
property. The same data with unnamed ranges worked perfectly — so the path
that works is the one the planner is told never to take.

**A time series was re-sorted into a ranking.** `shape()` ended with an
unconditional sort by the first metric, discarding the `orderBys` this module
had just sent to GA4 and GA4 had honoured. "Sessions by day for July" came
back in date order and was rendered 2nd, 3rd, 4th, 1st.

**And "total" was the total of whatever came back.** GA4 returns totals only
where `metricAggregations` was asked for, which this module does not ask for,
so the fallback sums the rows — and with `limit: 25` on a property with three
hundred cities that is the top 25 presented as the whole. The SEO gallery's
"Showing 1 of 7", one report along.
"""
import sys
from pathlib import Path

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


from hub import analytics_ask as A                             # noqa: E402


def shaped(report, request):
    """Guarded: a regression must name itself rather than ending the run."""
    try:
        return A.shape(report, request)
    except Exception as exc:                                   # noqa: BLE001
        return {"raised": f"{type(exc).__name__}: {exc}"}


def row(dims, *metrics):
    return {"dimensionValues": [{"value": d} for d in dims],
            "metricValues": [{"value": str(m)} for m in metrics]}


def cell(s, r=0, c=0):
    try:
        return s["rows"][r]["cells"][c]
    except Exception:                                          # noqa: BLE001
        return {}


# =====================================================================
section("July versus June")
# =====================================================================
REQ, _err = A.validate({
    "metrics": ["sessions"], "dimensions": ["city"],
    "dateRanges": [
        {"startDate": "2026-07-01", "endDate": "2026-07-31", "name": "July"},
        {"startDate": "2026-06-01", "endDate": "2026-06-30", "name": "June"}]})
check("the names the prompt asks for survive validation",
      [r.get("name") for r in REQ["dateRanges"]], ["July", "June"])

# GA4 values the dateRange dimension with the NAME when one is given.
NAMED = {"rows": [row(["Dublin", "July"], 900), row(["Dublin", "June"], 600)]}
s = shaped(NAMED, REQ)
check("the current period is the first range", cell(s).get("value"), 900.0)
check("and the previous one is the second", cell(s).get("previous"), 600.0)
check("so the change is the change", cell(s).get("change_pct"), 50.0)
check("one city is one row", s.get("row_count"), 1)
check("and the totals agree with it",
      [s["totals"][0][k] for k in ("value", "previous", "change_pct")],
      [900.0, 600.0, 50.0])

# The unnamed fallback is the shape that used to work, and still must.
INDEXED = {"rows": [row(["Dublin", "date_range_0"], 900),
                    row(["Dublin", "date_range_1"], 600)]}
t = shaped(INDEXED, REQ)
check("GA4's unnamed tags read the same way",
      [cell(t).get("value"), cell(t).get("previous")], [900.0, 600.0])

# Order in the response is Google's business, not ours: a comparison must not
# depend on which period happened to come back first.
REVERSED = {"rows": [row(["Dublin", "June"], 600), row(["Dublin", "July"], 900)]}
u = shaped(REVERSED, REQ)
check("and the rows may arrive in either order",
      [cell(u).get("value"), cell(u).get("previous")], [900.0, 600.0])

check("range_index reads a name", A.range_index("June", REQ["dateRanges"]), 1)
check("and an index tag", A.range_index("date_range_1", REQ["dateRanges"]), 1)
check("a tag naming neither is not guessed at",
      A.range_index("date_range_9", REQ["dateRanges"]), None)
check("nor is a name nobody asked for",
      A.range_index("August", REQ["dateRanges"]), None)


# =====================================================================
section("A comparison it could not align is not a comparison")
# =====================================================================
# The old code folded an unplaceable row into the first period silently, which
# is how the wrong number got printed with a percentage beside it.
STRANGE = {"rows": [row(["Dublin", "Q3"], 900), row(["Dublin", "Q2"], 600)]}
v = shaped(STRANGE, REQ)
check("the report still renders", v.get("row_count"), 1)
check("but claims no previous figure", "previous" in cell(v), False)
check("and no change", "change_pct" in cell(v), False)
check("it says so", "not a comparison" in v.get("note", ""), True)
check("naming how many rows it could not place", v.get("unaligned_rows"), 2)
check("`comparing` still says what was asked for", v.get("comparing"), True)
check("and `compared` says what was answered", v.get("compared"), False)
check("a comparison that worked says both", [s.get("comparing"), s.get("compared")],
      [True, True])

# A single-period report is not a failed comparison.
SOLO, _ = A.validate({"metrics": ["sessions"], "dimensions": ["city"],
                      "dateRanges": [{"startDate": "28daysAgo",
                                      "endDate": "yesterday"}]})
w = shaped({"rows": [row(["Dublin"], 900)]}, SOLO)
check("one period is not comparing", w.get("comparing"), False)
check("and raises no note", w.get("note"), "")
check("and still reports its number", cell(w).get("value"), 900.0)


# =====================================================================
section("A time series stays in order")
# =====================================================================
SERIES, _ = A.validate({"metrics": ["sessions"], "dimensions": ["date"],
                        "dateRanges": [{"startDate": "2026-07-01",
                                        "endDate": "2026-07-04"}]})
check("this module asks GA4 for date order",
      SERIES.get("orderBys"), [{"dimension": {"dimensionName": "date"}}])
DAYS = {"rows": [row(["20260701"], 10), row(["20260702"], 90),
                 row(["20260703"], 40), row(["20260704"], 20)]}
d = shaped(DAYS, SERIES)
check("and renders what GA4 returned, in the order it returned it",
      [r["dims"][0] for r in d["rows"]],
      ["20260701", "20260702", "20260703", "20260704"])

# Where nothing was asked for, biggest first is the sensible default and is
# what this always did.
NOORDER, _ = A.validate({"metrics": ["sessions"], "dimensions": ["city"],
                         "dateRanges": [{"startDate": "28daysAgo",
                                         "endDate": "yesterday"}]})
check("no order asked for", NOORDER.get("orderBys"), None)
CITIES = {"rows": [row(["Cork"], 400), row(["Dublin"], 900), row(["Galway"], 300)]}
c = shaped(CITIES, NOORDER)
check("so the biggest leads", [r["dims"][0] for r in c["rows"]],
      ["Dublin", "Cork", "Galway"])

# A metric order was honoured by GA4 already, so re-sorting is at best a
# no-op and at worst undoes an ascending one.
ASC, _ = A.validate({"metrics": ["bounceRate"], "dimensions": ["city"],
                     "orderBy": {"metric": "bounceRate", "desc": False},
                     "dateRanges": [{"startDate": "28daysAgo",
                                     "endDate": "yesterday"}]})
check("an ascending metric order is sent as one",
      ASC["orderBys"][0]["desc"], False)
a = shaped({"rows": [row(["Cork"], 10), row(["Dublin"], 50)]}, ASC)
check("and is not reversed on the way out",
      [r["dims"][0] for r in a["rows"]], ["Cork", "Dublin"])


# =====================================================================
section("What the total is the total of")
# =====================================================================
CAPPED, _ = A.validate({"metrics": ["sessions"], "dimensions": ["city"],
                        "limit": 3,
                        "dateRanges": [{"startDate": "28daysAgo",
                                        "endDate": "yesterday"}]})
top3 = {"rows": [row(["Dublin"], 900), row(["Cork"], 400), row(["Galway"], 300)]}
p = shaped(top3, CAPPED)
check("a report that filled its limit sums what came back",
      p["totals"][0]["value"], 1600.0)
check("and says that is what it is", p.get("totals_of"), "the rows shown")

UNDER, _ = A.validate({"metrics": ["sessions"], "dimensions": ["city"],
                       "limit": 25,
                       "dateRanges": [{"startDate": "28daysAgo",
                                       "endDate": "yesterday"}]})
q = shaped(top3, UNDER)
check("a report that did not fill its limit has them all",
      q.get("totals_of"), "all the rows")

# Where GA4 supplies its own totals they are the property's, and are used
# rather than a sum of the page.
WITH_TOTALS = dict(top3, totals=[{"metricValues": [{"value": "50000"}]}])
r_ = shaped(WITH_TOTALS, CAPPED)
check("Google's own total wins over our sum", r_["totals"][0]["value"], 50000.0)
check("and is named as everything measured",
      r_.get("totals_of"), "everything measured")
check("a totals block we cannot read is not invented",
      shaped(dict(top3, totals=[{"metricValues": [{"value": "n/a"}]}]),
             CAPPED)["totals"][0]["value"], 1600.0)


# =====================================================================
section("The model is handed what was measured, and nothing else")
# =====================================================================
import hub.ai as _ai                                           # noqa: E402

_sent = {}
_ai.chat = lambda messages, **kw: _sent.update(
    {"system": messages[0]["content"], "payload": messages[1]["content"]}) or "fine"

A.narrate("how did July compare?", "July vs June", v)      # the unaligned one
import json                                                    # noqa: E402
_p = json.loads(_sent["payload"])
check("a comparison that could not be aligned is not offered as one",
      _p.get("comparing"), False)
A.narrate("top cities?", "Top cities", p)                  # the capped one
_p = json.loads(_sent["payload"])
check("and the model is told what the totals cover",
      _p.get("totals_cover"), "the rows shown")
check("in a prompt that tells it to say so",
      "the rows shown" in _sent["system"], True)
check("and not to describe a change nothing computed",
      "do not describe a change" in _sent["system"], True)


# =====================================================================
section("A plan is rebuilt, never filtered")
# =====================================================================
# The existing guarantees, which nothing asserted either.
bad, err = A.validate({"metrics": ["sessions", "notARealMetric"],
                       "dimensions": ["city", "notARealDimension"],
                       "propertyId": "properties/999",
                       "dateRanges": [{"startDate": "2026-07-01",
                                       "endDate": "2026-07-31"}]})
check("an invented metric does not survive",
      [m["name"] for m in bad["metrics"]], ["sessions"])
check("nor an invented dimension",
      [d["name"] for d in bad["dimensions"]], ["city"])
check("and a property it was never given cannot be asked for",
      "propertyId" in bad, False)
check("a plan with no usable measure is refused rather than defaulted",
      A.validate({"metrics": ["notARealMetric"]})[0], None)
check("naming what it asked for",
      "notARealMetric" in A.validate({"metrics": ["notARealMetric"]})[1], True)
check("a limit beyond the cap is brought back to it",
      A.validate({"metrics": ["sessions"], "limit": 99999})[0]["limit"],
      A.MAX_LIMIT)
check("and a limit that is not a number does not raise",
      A.validate({"metrics": ["sessions"], "limit": "lots"})[0]["limit"], 25)
check("an unreadable date range falls back rather than reaching GA4",
      A.validate({"metrics": ["sessions"],
                  "dateRanges": [{"startDate": "whenever",
                                  "endDate": "later"}]})[0]["dateRanges"],
      [{"startDate": "28daysAgo", "endDate": "yesterday"}])
check("a plan that is not a plan is refused", A.validate("sessions please")[0],
      None)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
