## A comparison keyed on a string Google does not send

`hub/analytics_ask.py` turns a plain-English question into a GA4 report, and
its docstring opens by saying what it replaced: a keyword matcher that
answered *"how did conversions do in July versus June?"* with a thirty-day
source/medium table, **"which is worse than refusing — it answers confidently
with the wrong report."** It was doing the same thing one layer down.

`shape()` decided which period a row belonged to with `tag.endswith("_1")`.
GA4 values the `dateRange` dimension with the range's **name** where one was
given, and only falls back to `date_range_0` / `date_range_1` where none was
— and `_PLAN_SCHEMA_NOTE` *requires* names: *"for a comparison, give exactly
two dateRanges, each with a name."* So "July" and "June" both tested false,
both rows landed in the same bucket, and the second overwrote the first.

Dublin at 900 sessions in July against 600 in June rendered as **600**, with
no previous and no change, and the totals row read **"600, up 100% on 0"**.
Every figure on the page wrong, and every one of them a real number from the
property. The identical data with unnamed ranges worked perfectly, so **the
path that works is the one the planner is told never to take** — which is why
nothing ever looked broken in development.

`range_index()` reads the name first and the index tag second, and a tag it
can place in neither is **counted rather than folded into the first**:
`compared` is the answer to *were the two periods told apart*, `comparing` is
the answer to *were two asked for*, and only the first may draw a change. The
old code answered the second question and printed a percentage.

**A time series re-sorted into a ranking.** `shape()` ended with an
unconditional sort by the first metric, discarding the `orderBys` this module
had just sent to GA4 and GA4 had honoured — so "sessions by day for July"
came back in date order and was rendered 2nd, 3rd, 4th, 1st. Every number
right, and the one thing a time series is for gone. It sorts only when
nothing was asked for, which is what that default was written to cover.

**And "total" was the total of whatever came back.** GA4 returns totals only
where `metricAggregations` was requested, which this module does not request,
so the fallback sums the rows — and under `limit: 25` on a property with
three hundred cities that is the top 25 presented as the whole. `totals_of`
says which it is, and Google's own totals row is read where one is there
rather than being summed over. It reaches the **model's payload** too, with
`compared` in place of `comparing`: `narrate()` is handed the shaped numbers
precisely so it cannot introduce a figure the table does not show, and
handing it a comparison flag for a comparison nothing computed is the
invented-figure failure `hub/audit_summary.py` exists to refuse, one module
over. `test_analytics_ask.py` asserts all of it.
