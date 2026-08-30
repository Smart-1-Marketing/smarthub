"""How a campaign will be judged — the one description, read by two tools.

## Why this exists

The insertion order has carried a **KPI Framework** for as long as it has
existed: a primary KPI, the secondary ones, the success metrics, and a table
saying what each product on the plan is actually measured on and what a normal
result for it looks like. The proposal — the document the client reads *first*,
and the one they decide from — ended instead with a table of impressions.

That is the wrong answer to the question the section's own title asks. A client
reading "Expected Results & ROI" wants to know what counts as this campaign
working, and an impression count is a delivery figure rather than a result: it
says what the money bought, not what the business gets. Worse, the two
documents then described the same campaign two different ways — the proposal in
impressions, the insertion order in KPIs — so what the client agreed to and
what the campaign was run against were not the same statement.

So the framework is here, and both read it. The IO's own JavaScript copy is
still the thing that draws the IO screen; `test_proposal_targeting.py` parses
that function out of the template and requires it to agree with this table, the
way `test_target_areas.py` does for the area helpers. A benchmark that says one
thing on the quote and another on the order is exactly the failure this file
exists to end.

## The rules

  * **A benchmark is a range, and it is named as an expectation.** Never a
    promise, never a single number: "95%–99%" is what this inventory normally
    delivers, and a client who reads a promise into a single figure has been
    misled by the formatting rather than by the copy.
  * **Nothing is invented for a product the table does not know.** It falls
    through to "track against the campaign objective", which is honest, rather
    than to the nearest-looking row — a display benchmark printed against an
    audio buy is a number nobody can hit.
  * **"Not measured" is an answer.** A campaign with no KPI chosen says so and
    names where it is chosen. A confident-looking framework built from nothing
    is the failure this codebase keeps having to undo.
"""
from __future__ import annotations

import re

from hub import creative_needs as _creative

# Transcribed from the IO builder's `benchmarkFor`, in its order — the first
# pattern that matches wins, and the order is load-bearing: "video" has to be
# tested after OTT, or every Connected TV line reads as YouTube.
#
# Kept as data rather than as a chain of ifs so the test that compares this
# with the IO's JavaScript has something to compare.
BENCHMARKS: tuple[tuple[str, str, str], ...] = (
    (r"ott|connected tv|advanced tv", "Video Completion Rate", "95%–99%"),
    (r"youtube|video", "Video Completion Rate", "70%–95%"),
    (r"radio|podcast|audio", "Audio Completion Rate", "95%–99%"),
    (r"search engine marketing|pay per click|local service",
     "Conversion Rate", "3%–10%"),
    (r"facebook|instagram|meta|social", "CTR / Engagement", "1%–3% CTR"),
    (r"email", "Open / Click Rate", "15%–35% open rate"),
    (r"seo|local business|listing", "Organic Visibility",
     "Positive trend over 3–6 months"),
    (r"display|mobile|retarget|location lookback|ip target|programmatic",
     "CTR", "0.08%–0.35%"),
)

FALLBACK = ("Delivery / Completion", "Track against campaign objective")

# Metrics that follow from what is on the plan rather than from what anybody
# ticked. A video buy is judged on completions whether or not somebody
# remembered to say so.
_BY_MEDIUM = {
    _creative.VIDEO: ("Completed video views", "Video completion rate"),
    _creative.AUDIO: ("Audio listen-through rate",),
    _creative.DISPLAY: ("Click-through rate", "Cost per click"),
    _creative.SOCIAL: ("Click-through rate", "Cost per click"),
}

# Every campaign ends here, whatever is on it: the Suite is what turns a
# delivery figure into a business result, and saying so is the point of the
# section.
_ALWAYS = ("Cost per lead", "Lead-to-close rate, reported in the Smart 1 Suite")

MAX_METRICS = 10


def benchmark_for(category: str = "", product: str = "") -> dict:
    """What one product is measured on, and what a normal result looks like."""
    subject = f"{category or ''} {product or ''}".lower()
    for pattern, kpi, expected in BENCHMARKS:
        if re.search(pattern, subject):
            return {"kpi": kpi, "expected": expected, "matched": True}
    return {"kpi": FALLBACK[0], "expected": FALLBACK[1], "matched": False}


def success_metrics(state) -> list[str]:
    """What we will report on: the campaign's own KPIs, plus what its media implies."""
    state = state or {}
    metrics = [str(k).strip() for k in (state.get("kpis") or []) if str(k).strip()]
    media = {_creative.medium_of(i) for i in (state.get("items") or [])}
    for medium, extras in _BY_MEDIUM.items():
        if medium in media:
            metrics += list(extras)
    metrics += list(_ALWAYS)
    seen, out = set(), []
    for metric in metrics:
        key = metric.lower()
        if key not in seen:
            seen.add(key)
            out.append(metric)
    return out[:MAX_METRICS]


def framework(state) -> dict:
    """The whole KPI framework for one campaign.

        {"primary": str, "secondary": [...], "metrics": [...],
         "rows": [{"product", "kpi", "expected"}], "measured": bool,
         "note": "..."}

    `measured` is false when nobody has chosen a KPI — the section then says
    which step chooses one rather than printing a confident framework built
    from an empty list.
    """
    state = state or {}
    kpis = [str(k).strip() for k in (state.get("kpis") or []) if str(k).strip()]
    rows = []
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("product") or "").strip()
        if not label:
            continue
        mark = benchmark_for(item.get("category") or "", item.get("product") or "")
        rows.append({"product": label, "kpi": mark["kpi"],
                     "expected": mark["expected"], "matched": mark["matched"]})
    metrics = success_metrics(state)
    # What is reported *beyond* the KPIs already named above it. Printing the
    # full list under "Secondary KPIs" repeated all four of them a line later,
    # which reads as padding on the one section a client studies -- and a
    # reader who sees the same four twice stops reading the second list.
    named = {k.lower() for k in kpis}
    return {
        "primary": kpis[0] if kpis else "",
        "secondary": kpis[1:],
        "metrics": metrics,
        "additional_metrics": [m for m in metrics if m.lower() not in named],
        "rows": rows,
        "measured": bool(kpis),
        "note": ("" if kpis else
                 "No primary KPI has been chosen for this campaign yet — the "
                 "Measurement step sets it."),
    }
