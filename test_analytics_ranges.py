"""The period picker, and the question that asks a better question back.

    python3 test_analytics_ranges.py

No pytest, no network. What is asserted here is what the templates wire up
and what the two server-side helpers do with what they are handed.

## Why this file exists

**Every period but one had to be typed.** The SEO client page offered two
buttons — "Month to date" and "Custom compare" — and four empty date boxes
behind the second. Last month, last week, the last 90 days: four dates, by
hand, every visit. Google Analytics and Google Ads both answer this with a
list. `hub/static/date-range.js` is that list, mounted by both analytics
pages rather than written twice.

**Client 360 had no picker at all.** `/api/ga4/monthly-summary` reported the
last full calendar month and nothing else could be asked of it, so "how are
they doing this month?" had no route through that card. It now takes the same
period parameters as `/api/ga4/seo-snapshot`, through the same
`_snapshot_ranges`, and both pages open on month to date — because a number
read on one page and then the other should be the same number.

**And a question back was a dead end.** Ask Analytics could reply "which
measure did you mean?", which is a fair question and useless to the one
person guaranteed not to know the answer: the one who just asked something it
could not read. The planner now returns suggestions with the question, and
the widget renders them as choices with a reply box under them.

The regression this last part guards is small and specific: the old widget
put the clarifying question in the input's *placeholder*, where it
disappeared the moment anyone typed a character. The question has to stay on
screen.
"""
import re
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


def head(title):
    print(f"\n{title}\n" + "-" * len(title))


JS = (ROOT / "hub" / "static" / "date-range.js").read_text(encoding="utf-8")
ASK_JS = (ROOT / "hub" / "static" / "ask-analytics.js").read_text(encoding="utf-8")
SEO = (ROOT / "hub" / "templates" / "seo_client.html").read_text(encoding="utf-8")
C360 = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")
GOOGLE = (ROOT / "modules" / "google_finder" / "app.py").read_text(encoding="utf-8")
HELP = (ROOT / "hub" / "help_routes.py").read_text(encoding="utf-8")
GUARDS = (ROOT / "test_blueprint_guards.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
head("1. One picker, served once, loaded by both pages")

check("the control is served from the hub root", '"/date-range.js"' in HELP, True)
check("and the guard list names it so a static asset is not read as a leak",
      '"/date-range.js"' in GUARDS, True)
check("the SEO client page loads it", '/date-range.js' in SEO, True)
check("Client 360 loads it", '/date-range.js' in C360, True)
check("neither page carries its own copy of the list",
      ('var PRESETS' in SEO) or ('var PRESETS' in C360), False)


# ---------------------------------------------------------------------------
head("2. The periods a person actually asks for")

# The list is read out of the file rather than restated here: a test that
# holds its own copy of the list passes while the page offers something else.
keys = re.findall(r'\{\s*key:\s*"([a-z0-9_]+)"', JS)
for want in ("today", "yesterday", "last7", "last30", "last90", "wtd",
             "last_week", "mtd", "last_month", "qtd", "last_quarter",
             "ytd", "last_year", "custom"):
    check(f"the list offers {want}", want in keys, True)
check("custom is last, where an escape hatch belongs", keys[-1], "custom")
check("month to date is the default the pages ask for",
      SEO.count("preset:'mtd'") + C360.count("preset:'mtd'"), 2)
check("and the comparison defaults with it",
      SEO.count("compare:'previous'") + C360.count("compare:'previous'"), 2)
check("a comparison against the same period a year earlier is offered",
      'key: "year"' in JS, True)
check("and a custom comparison is still possible",
      JS.count('key: "custom"'), 2)

check("'last N days' ends yesterday, as it does in GA4 and Ads",
      "var e = addDays(today, -1)" in JS, True)
check("dates are formatted in local time — toISOString is UTC, and west of "
      "Greenwich in the evening that reports yesterday as today",
      'return d.getFullYear() + "-"' in JS, True)


# ---------------------------------------------------------------------------
head("3. The one case the server does better than four dates")

check("month to date with the default comparison sends nothing",
      'pkey === "mtd" && ckey === "previous"' in JS, True)
check("the SEO page respects that rather than posting dates anyway",
      "!trafficRange.server_default" in SEO, True)
check("and the old two-button bar is gone", "seoc-rangebtn" in SEO, False)
check("along with the four hand-typed boxes behind it",
      "seoc-customrange" in SEO, False)
check("the AI card names the period it inherited rather than offering its own",
      "aiRangeLabel" in SEO, True)


# ---------------------------------------------------------------------------
head("4. Client 360 reads a period instead of assuming one")

check("the summary endpoint takes the periods the snapshot takes",
      "ranges, mode = _snapshot_ranges(data)" in GOOGLE, True)
check("and no longer computes last month on its own",
      "last_end = first_this - timedelta(days=1)" in GOOGLE, False)
check("the card posts what the picker resolved",
      "c360Range.params()" in GOOGLE + C360, True)
check("and re-reads when the period changes", "c360RangeReady" in C360, True)
check("a second client re-mounts the picker rather than firing into a "
      "detached one", "c360Range.el.parentNode===host" in C360, True)

_src = GOOGLE.split("def _range_label", 1)[1].split("@app.route", 1)[0]
_ns: dict = {}
exec("def _range_label" + _src, _ns)                     # noqa: S102
_range_label = _ns["_range_label"]

check("a whole calendar month is named as one", _range_label("2026-08-01", "2026-08-31"),
      "August 2026")
check("a whole calendar year is named as one", _range_label("2025-01-01", "2025-12-31"),
      "2025")
check("a part month is its dates, not the month it sits in — the label that "
      "used to say 'September 2026' about four days of it",
      _range_label("2026-09-01", "2026-09-04"), "Sep 1, 2026 – Sep 4, 2026")
check("a single day is that day", _range_label("2026-09-03", "2026-09-03"),
      "Sep 3, 2026")
check("February is not mistaken for a part month",
      _range_label("2024-02-01", "2024-02-29"), "February 2024")


# ---------------------------------------------------------------------------
head("5. A question back that can be answered")

import hub.analytics_ask as A                                    # noqa: E402

check("suggestions survive as strings",
      A._suggestions(["Sessions by channel", "Top pages last month"]),
      ["Sessions by channel", "Top pages last month"])
check("four at most, because they are buttons",
      len(A._suggestions([f"question {i}" for i in range(9)])), 4)
check("duplicates are dropped case-insensitively",
      A._suggestions(["Top pages", "top pages"]), ["Top pages"])
check("anything that is not a string is dropped rather than rendered",
      A._suggestions(["Top pages", 7, None, {"q": "x"}]), ["Top pages"])
check("a field that is not a list is not a suggestion",
      A._suggestions("Top pages"), [])
check("whitespace is collapsed so a button is one line",
      A._suggestions(["Top   pages\nlast month"]), ["Top pages last month"])
check("there is a fallback set for when the model offers none",
      len(A.FALLBACK_SUGGESTIONS) >= 3, True)

check("the planner is told to send them with a clarify",
      '"suggestions"' in A._PLAN_SCHEMA_NOTE, True)
check("the endpoint passes them to the page",
      '"suggestions": planned.get("suggestions") or []' in GOOGLE, True)


# ---------------------------------------------------------------------------
head("6. And the question stays on the screen")

check("the widget renders the suggestions", "renderClarify" in ASK_JS, True)
check("as one-click choices", "data-sugg" in ASK_JS, True)
check("with a reply box of its own for the answer that is none of them",
      "clarify-input" in ASK_JS, True)
check("the question is no longer hidden in the placeholder, where typing "
      "erased it", "input.placeholder = d.clarify" in ASK_JS, False)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
