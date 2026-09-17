"""Pacing and cost: how each sold line is spending against its budget, and
what that costs against what the client pays.

Staff only, both halves. Nothing here reaches a client's page.

## Pacing

``compute(today)`` reads every ``BudgetLine`` that is **active** and whose
flight includes today, and for each answers the arithmetic the reporting
spec (§5) gives:

* **budget_period** -- ``monthly_budget`` prorated to the part of this
  month the flight covers: a flight starting on the 16th of a 30-day month
  paces against half the monthly figure, not all of it.
* **expected_to_date** = budget_period x days_elapsed / days_in_period.
* **actual_to_date** = raw spend this period of the client's mapped
  campaigns -- narrowed to the line's product where it names one (matched
  on the mapping's product, case-insensitively) and to its platform where
  it names one. Raw spend, never the client price: this is the media
  budget the campaigns pace against.
* **pace** = actual / expected; **band** under < 0.90, on 0.90-1.10, over
  > 1.10.
* **stalled** -- zero spend on each of the last two completed days while
  the flight is mid-way (after its second day, before its last), which is a
  campaign that stopped rather than one that has not started or has
  finished.
* **unmapped** -- no campaign mapped to the line's client (and product,
  and platform) at all, so there is nothing to pace: the line was sold and
  nothing has been filed against it.
* **projected_month_end** = actual + avg daily spend over the last seven
  days x days_remaining; **daily_needed** = (budget_period - actual) /
  days_remaining.

Stalled and unmapped are flags AND bands, because they are what the board
sorts and counts on: a line that is unmapped has no pace to be under.

**The alert is on the 3-day trend, never one day.** Google may spend up to
twice the daily budget on one day (capped at ~30.4x monthly), Meta up to
25% over a day inside a weekly cap, the Trade Desk, StackAdapt and
GroundTruth pace to flight budgets themselves, and Amazon DSP restates for
weeks -- so a single day off pace is ordinary on every platform here. Each
run stores its band; ``trend_days`` counts the consecutive days (today
included, one band per day from the stored history) the line has sat in
the same off-pace band, and ``alert`` is true from three.

## Snapshots, not live sums

``run(today)`` computes, writes one ``PacingSnapshot`` row per line stamped
with one ``computed_at``, prunes old rows, and writes one activity row per
client with its band summary ("2 lines on pace, 1 under") so the run shows
on Client 360. ``hub/scheduler.job_reports_pacing`` calls it hourly, after
the pulls. ``board()`` and ``cost()`` read the latest run.

## Cost

``cost(month)`` is the page Connie reads: per client, the month's raw
media spend by platform, what the client pays (the sum of ``sold_amount``
over lines whose flight overlaps the month -- a line marked ended counts
only through its flight_end, and one ended with no flight_end does not
count), gross margin in dollars and percent, and cost per lead and per
appointment where a Smart 1 Suite outcome row exists for the client that
month. Those two columns are OMITTED, not zeroed, when no client on the
book has a Suite row: a $0 cost per lead reads as a lead that cost nothing.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from . import store

log = logging.getLogger(__name__)

UNDER, OVER = 0.90, 1.10
BANDS = ("under", "on", "over", "stalled", "unmapped")
BAND_LABELS = {
    "under": ("▼", "Under pace"),
    "on": ("●", "On pace"),
    "over": ("▲", "Over pace"),
    "stalled": ("■", "Stalled"),
    "unmapped": ("?", "Unmapped"),
}
ALERT_DAYS = 3
# The largest pace the snapshot column can hold: ``PacingSnapshot.pace`` is
# Numeric(8, 4). A $1/month line spends $10 on day one and paces at
# 10,000x, which Postgres refuses -- and one refused row fails the whole
# run's insert, so NO line gets a snapshot. SQLite ignores the precision,
# which is why the tests could not see it. Past this the figure is
# meaningless anyway; the band is still computed from the true ratio.
PACE_CAP = 9999.9999
CENT = Decimal("0.01")


def _q(d) -> Decimal:
    return Decimal(d or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def period_for(line: dict, today: date) -> dict | None:
    """The part of this month the line's flight covers, or None when today
    is outside the flight."""
    fs = date.fromisoformat(line["flight_start"]) if line.get("flight_start") else None
    fe = date.fromisoformat(line["flight_end"]) if line.get("flight_end") else None
    if (fs and fs > today) or (fe and fe < today):
        return None
    month_start = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    month_end = today.replace(day=days_in_month)
    start = max(month_start, fs) if fs else month_start
    end = min(month_end, fe) if fe else month_end
    days_in_period = (end - start).days + 1
    return {"start": start, "end": end, "days_in_period": days_in_period,
            "days_in_month": days_in_month,
            "days_elapsed": (today - start).days + 1,
            "days_remaining": (end - today).days,
            # Carried for the daily average: a day before the flight began
            # is not a day the line could have spent on.
            "flight_start": fs}


def _matches(line: dict, mapping: dict) -> bool:
    if line.get("platform") and mapping["platform"] != line["platform"]:
        return False
    if line.get("product"):
        return (mapping.get("product") or "").strip().lower() == line["product"].strip().lower()
    return True


def band_for(pace: float | None) -> str:
    if pace is None:
        return "on"
    return "under" if pace < UNDER else "over" if pace > OVER else "on"


def compute_line(line: dict, today: date, mappings: list[dict], facts: list[dict],
                 history: list[tuple] | None = None, last_sync=None) -> dict | None:
    """One line's row, or None when the line is not pacing today. ``facts``
    are the client's fact rows for the month so far (and the seven days
    before it, for the daily average); ``history`` is
    ``store.band_history()`` for the line, newest first."""
    if (line.get("status") or "active") != "active":
        return None
    per = period_for(line, today)
    if per is None:
        return None
    mine = [m for m in mappings if _matches(line, m)]
    keys = {(m["platform"], m["account_id"], m["campaign_id"]) for m in mine}
    rows = [f for f in facts if (f["platform"], f["account_id"], f["campaign_id"]) in keys]
    in_period = [f for f in rows if per["start"] <= f["date"] <= today]
    actual = sum((Decimal(f["spend"]) for f in in_period), Decimal(0))
    budget = Decimal(line["monthly_budget"] or 0)
    budget_period = budget * per["days_in_period"] / per["days_in_month"]
    expected = budget_period * per["days_elapsed"] / per["days_in_period"] if per["days_in_period"] else Decimal(0)
    pace = float(actual / expected) if expected else None
    unmapped = not mine
    # Stalled: the last two completed days both zero, mid-flight.
    by_day: dict[date, Decimal] = {}
    for f in rows:
        by_day[f["date"]] = by_day.get(f["date"], Decimal(0)) + Decimal(f["spend"])
    d1, d2 = today - timedelta(days=1), today - timedelta(days=2)
    mid_flight = per["days_elapsed"] > 2 and per["days_remaining"] > 0
    stalled = (not unmapped and mid_flight and by_day.get(d1, Decimal(0)) == 0
               and by_day.get(d2, Decimal(0)) == 0)
    # The daily rate for the projection: the last seven completed days, or
    # as many of them as the flight has run. Dividing by seven regardless
    # projected a line three days into its flight at three-sevenths of its
    # real rate -- the first week, which is when a projection is read
    # hardest, was the week it understated. A line with no flight start
    # still takes the whole window: nothing says when it should have begun,
    # and a zero day inside the month is a real zero.
    window = [today - timedelta(days=i) for i in range(1, 8)]
    fs = per.get("flight_start")
    counted = [d for d in window if fs is None or d >= fs]
    week = [by_day.get(d, Decimal(0)) for d in counted]
    avg7 = (sum(week, Decimal(0)) / Decimal(len(counted))) if counted else Decimal(0)
    remaining = per["days_remaining"]
    projected = actual + avg7 * remaining
    daily_needed = ((budget_period - actual) / remaining) if remaining > 0 else None
    spent_days = [d for d, v in by_day.items() if v > 0]
    band = "unmapped" if unmapped else "stalled" if stalled else band_for(pace)
    # The trend: consecutive days, today included, in this same off-pace
    # band. Yesterday's band comes from the stored history, one per day.
    trend = 1
    if band != "on":
        prior = [(d, b) for d, b in (history or []) if d < today]
        expect = today - timedelta(days=1)
        for d, b in prior:
            if d != expect or b != band:
                break
            trend += 1
            expect -= timedelta(days=1)
    return {
        "as_of": today, "line_id": line["id"], "client": line["client"],
        "client_name": line.get("client_name") or line["client"],
        "product": line.get("product") or "", "platform": line.get("platform") or None,
        "platforms_json": sorted({m["platform"] for m in mine}),
        "owner": line.get("owner") or "",
        "monthly_budget": _q(budget), "sold_amount": _q(line["sold_amount"]) if line.get("sold_amount") is not None else None,
        "budget_period": _q(budget_period), "expected_to_date": _q(expected),
        "actual_to_date": _q(actual),
        "pace": Decimal(str(round(min(pace, PACE_CAP), 4))) if pace is not None else None,
        # The bar on the board: 2.00x fills it. Computed here rather than as
        # {{ [pace * 100, 200]|min / 2 }} in the template, which CodeQL reads
        # as the filter call (min / 2)([...]) and reports as invoking a number.
        "bar_pct": round(min(pace, 2.0) * 50, 1) if pace is not None else None,
        "band": band, "stalled": stalled, "unmapped": unmapped,
        "projected_month_end": _q(projected), "daily_needed": _q(daily_needed) if daily_needed is not None else None,
        "avg_daily_7": _q(avg7),
        "period_start": per["start"], "period_end": per["end"],
        "days_elapsed": per["days_elapsed"], "days_remaining": remaining,
        "days_in_period": per["days_in_period"],
        "last_spend_date": max(spent_days) if spent_days else None,
        "trend_days": trend, "alert": band != "on" and trend >= ALERT_DAYS,
        "last_sync": last_sync,
    }


def compute(today: date | None = None, client: str | None = None) -> list[dict]:
    """Every active, in-flight line's row, live. ``run()`` is what persists
    them; the pages read the persisted rows. ``client`` narrows it to one
    client's lines -- the staff client page's reading, so that page and
    the board cannot disagree about a line."""
    today = today or date.today()
    # Filtered in the database, uncapped. A global read capped at N and
    # filtered here drops the OLDEST lines first, so a sold, funded, spending
    # line of the longest-standing client is not wrong on this board -- it is
    # ABSENT from it, and a line nobody sees is a line nobody paces.
    lines = (store.budget_lines_for(client, active_only=True) if client is not None
             else store.all_budget_lines(active_only=True))
    if not lines:
        return []
    month_start = today.replace(day=1)
    since = min(month_start, today - timedelta(days=8))
    last_sync = store.last_synced_at()
    out = []
    facts_cache: dict[str, list] = {}
    maps_cache: dict[str, list] = {}
    pending_cache: dict[str, list] = {}
    for line in lines:
        c = line["client"]
        if c not in facts_cache:
            facts_cache[c] = store.facts_for(c, since, today)
            # Confirmed mappings pace the line; a pending auto-mapping is
            # a proposal and its spend reaches no figure -- facts_for()
            # does not return its rows either, so counting it here would
            # mark the line mapped and pace it against nothing. It is
            # counted apart so the board can say "unmapped, 2 waiting for
            # confirmation" rather than "unmapped" about a line whose
            # campaigns are sitting one press away.
            # Per client, filtered in the database. A global read capped at
            # N and filtered here drops this client's OLDEST mappings once
            # the book passes N -- and a line whose campaigns aged out reads
            # `unmapped` while facts_for still returns their spend.
            filed = store.campaign_maps_for(c)
            maps_cache[c] = [m for m in filed if not m.get("pending")]
            pending_cache[c] = [m for m in filed if m.get("pending")]
        row = compute_line(line, today, maps_cache[c], facts_cache[c],
                           history=store.band_history(line["id"]), last_sync=last_sync)
        if row is not None:
            row["pending_campaigns"] = sum(1 for m in pending_cache[c] if _matches(line, m))
            out.append(row)
    return out


def summary_line(rows: list[dict]) -> str:
    """"2 lines on pace, 1 under" -- the sentence the activity row carries."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["band"]] = counts.get(r["band"], 0) + 1
    parts = []
    for band, word in (("on", "on pace"), ("under", "under"), ("over", "over"),
                       ("stalled", "stalled"), ("unmapped", "unmapped")):
        if counts.get(band):
            n = counts[band]
            parts.append(f"{n} line{'' if n == 1 else 's'} {word}" if not parts else f"{n} {word}")
    return ", ".join(parts) or "no lines pacing"


# What was last written to the activity log per client: (as_of, summary).
# The run is hourly and the pages read the snapshot, so the log row is
# for Client 360 -- and twenty-four identical rows a day per client is
# the flood hub/google_index.py's rule exists to stop. A client is logged
# once a day, and again the moment its band summary changes. Per process;
# the scheduler runs on the leader alone, so that is one process.
_LAST_LOGGED: dict[str, tuple[date, str]] = {}


def run(today: date | None = None, actor: str = "scheduler") -> dict:
    """Compute, persist one run, prune, and log per client. Never raises
    past the store: the job's answer names the failure."""
    today = today or date.today()
    rows = compute(today)
    written = store.write_snapshots(rows) if rows else 0
    pruned = store.prune_snapshots()
    by_client: dict[str, list] = {}
    for r in rows:
        by_client.setdefault(r["client"], []).append(r)
    logged = 0
    try:
        from hub import audit as hub_audit
    except Exception:                                   # noqa: BLE001 - standalone
        hub_audit = None
    if hub_audit is not None:
        for key, crows in by_client.items():
            name = crows[0]["client_name"] or key
            summary = summary_line(crows)
            if _LAST_LOGGED.get(key) == (today, summary):
                continue
            _LAST_LOGGED[key] = (today, summary)
            try:
                hub_audit.log("reports", "reports_pacing", actor=actor, client=name,
                              client_key=key, action="reports_pacing",
                              lines=len(crows), alerts=sum(1 for r in crows if r["alert"]),
                              detail=summary)
                logged += 1
            except Exception:                           # noqa: BLE001 - a log line is not the run
                pass
    counts = {b: sum(1 for r in rows if r["band"] == b) for b in BANDS}
    return {"lines": len(rows), "written": written, "pruned": pruned, "clients": len(by_client),
            "logged": logged, "alerts": sum(1 for r in rows if r["alert"]), "bands": counts,
            "as_of": today.isoformat()}


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------

SORTS = {
    "client": lambda r: (r["client_name"].lower(), r["product"].lower()),
    "product": lambda r: (r["product"].lower(), r["client_name"].lower()),
    "owner": lambda r: (r["owner"].lower(), r["client_name"].lower()),
    "budget": lambda r: -r["monthly_budget"],
    "spent": lambda r: -r["actual_to_date"],
    "pace": lambda r: (r["pace"] if r["pace"] is not None else -1),
    "projected": lambda r: -(r["projected_month_end"] or 0),
    "needed": lambda r: -(r["daily_needed"] or 0),
    "band": lambda r: (BANDS.index(r["band"]) if r["band"] in BANDS else 9, r["client_name"].lower()),
}


def board(band: str = "", platform: str = "", owner: str = "", client: str = "",
          sort: str = "band") -> dict:
    """The latest run, filtered and sorted. Counts are of the WHOLE run,
    filters or not, so the page's headline figures do not shrink to fit a
    filter."""
    rows = store.latest_snapshots()
    run_at = rows[0]["computed_at"] if rows else None
    counts = {b: sum(1 for r in rows if r["band"] == b) for b in BANDS}
    counts["alerts"] = sum(1 for r in rows if r["alert"])
    _overlay_pending(rows)
    _overlay_likely(rows)
    shown = rows
    if band:
        shown = [r for r in shown if r["band"] == band]
    if platform:
        shown = [r for r in shown if platform in r["platforms"] or r["platform"] == platform]
    if owner:
        shown = [r for r in shown if r["owner"].lower() == owner.lower()]
    if client:
        q = client.lower()
        shown = [r for r in shown if q in r["client_name"].lower() or q in r["client"].lower()]
    key = SORTS.get(sort) or SORTS["band"]
    shown = sorted(shown, key=key)
    for r in shown:
        r["icon"], r["band_label"] = BAND_LABELS.get(r["band"], ("", r["band"]))
    return {
        "rows": shown, "total": len(rows), "shown": len(shown), "counts": counts,
        "run_at": run_at, "measured": bool(rows),
        "owners": sorted({r["owner"] for r in rows if r["owner"]}),
        "platforms": sorted({p for r in rows for p in r["platforms"]} | {r["platform"] for r in rows if r["platform"]}),
        "clients": sorted({r["client_name"] for r in rows}),
        "sort": sort if sort in SORTS else "band",
        "filters": {"band": band, "platform": platform, "owner": owner, "client": client},
    }


def _overlay_pending(rows: list[dict]) -> None:
    """How many auto-mapped campaigns are waiting for confirmation against
    each line, read live and laid over the snapshot rather than stored in
    it: a confirmation is a press that should change the board at once, and
    a snapshot is the hourly run's answer. Never raises -- a board that
    cannot count the pending ones still draws the pacing."""
    try:
        # Only the clients on the board, so this cannot truncate one of them
        # away the way a global capped read can.
        pending = [m for m in store.campaign_maps_for({r["client"] for r in rows})
                   if m.get("pending")]
    except Exception:                                   # noqa: BLE001 - the store refused
        pending = None
    for r in rows:
        if pending is None:
            r["pending_campaigns"] = None
            continue
        r["pending_campaigns"] = sum(1 for m in pending
                                     if m["client"] == r["client"] and _matches(r, m))


def _overlay_likely(rows: list[dict]) -> None:
    """How many unmapped campaigns look like each line's client, from the
    queue's likeness -- laid over the snapshot like the pending count, and
    for the same reason. An unmapped line whose campaigns are sitting on
    the queue under the client's own name is one press from paced, and
    the board should say so and point at them. Never raises: a reading
    that cannot be made is None, which the page draws as nothing rather
    than as none."""
    try:
        from . import automap
        likely = automap.likely_by_client()
    except Exception:                                   # noqa: BLE001 - the store or registry refused
        likely = None
    for r in rows:
        r["likely_campaigns"] = None if likely is None else likely.get(r["client"], 0)


BOARD_COLUMNS = ("client", "product", "platforms", "owner", "monthly_budget", "sold_amount",
                 "budget_period", "expected_to_date", "actual_to_date", "pace", "band",
                 "trend_days", "alert", "projected_month_end", "daily_needed",
                 "days_elapsed", "days_remaining", "last_spend_date", "last_sync", "computed_at")


def board_csv(data: dict) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(BOARD_COLUMNS)
    for r in data["rows"]:
        w.writerow([
            r["client_name"], r["product"], " ".join(r["platform_labels"]), r["owner"],
            r["monthly_budget"], r["sold_amount"] if r["sold_amount"] is not None else "",
            r["budget_period"], r["expected_to_date"], r["actual_to_date"],
            f"{r['pace']:.2f}" if r["pace"] is not None else "", r["band"],
            r["trend_days"], "yes" if r["alert"] else "",
            r["projected_month_end"] if r["projected_month_end"] is not None else "",
            r["daily_needed"] if r["daily_needed"] is not None else "",
            r["days_elapsed"], r["days_remaining"], r["last_spend_date"] or "",
            r["last_sync"] or "", r["computed_at"] or "",
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# The cost report
# ---------------------------------------------------------------------------

def month_range(month: str | None, today: date | None = None) -> dict:
    today = today or date.today()
    try:
        y, m = int(month[:4]), int(month[5:7])
        start = date(y, m, 1)
    except (TypeError, ValueError):
        start = today.replace(day=1)
    end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    return {"key": f"{start:%Y-%m}", "label": f"{start:%B %Y}", "start": start,
            "end": min(end, today) if start <= today else end, "month_end": end}


def _line_in_month(line: dict, start: date, end: date) -> bool:
    fs = date.fromisoformat(line["flight_start"]) if line.get("flight_start") else None
    fe = date.fromisoformat(line["flight_end"]) if line.get("flight_end") else None
    status = line.get("status") or "active"
    if status == "ended" and fe is None:
        return False
    if fs and fs > end:
        return False
    if fe and fe < start:
        return False
    return True


def cost(month: str | None = None, today: date | None = None) -> dict:
    """Per client for the month: media spend by platform (raw), sold, gross
    margin, and cost per lead / appointment where a Suite outcome row
    exists. Totals row. The two outcome columns are omitted when no client
    has a Suite row that month."""
    today = today or date.today()
    rng = month_range(month, today)
    # Spend is read to ``rng["end"]`` (today, mid-month) and the sold amount
    # is a MONTHLY figure. Compared whole, every margin is inflated by the
    # days not yet spent -- measured on the 20th, $2,600 spent against
    # $5,500 sold read as 53% margin -- with nothing on screen saying the
    # two halves covered different windows. Sold is prorated to the same
    # window (a completed month is the whole figure), and the month figure
    # rides beside it under its own name.
    days_in_month = (rng["month_end"] - rng["start"]).days + 1
    days_elapsed = (rng["end"] - rng["start"]).days + 1
    try:
        lines = store.all_budget_lines()
        seen_clients = store.clients_with_campaigns()
    except Exception as exc:                        # noqa: BLE001 - the store refused
        return _cost_unmeasured(rng, today, f"the reports database could not be read "
                                            f"({type(exc).__name__})")
    clients: dict[str, dict] = {}
    for c in seen_clients:
        clients[c["client"]] = {"client": c["client"], "client_name": c["client_name"]}
    for b in lines:
        clients.setdefault(b["client"], {"client": b["client"], "client_name": b["client_name"] or b["client"]})
    rows = []
    any_outcomes = False
    platforms_seen: set = set()
    for key, c in clients.items():
        try:
            facts = store.facts_for(key, rng["start"], rng["end"])
        except Exception as exc:                    # noqa: BLE001 - the store refused
            return _cost_unmeasured(rng, today, f"the fact table could not be read "
                                                f"({type(exc).__name__})")
        by_plat: dict[str, Decimal] = {}
        leads = appts = 0
        has_suite = False
        for f in facts:
            if f["platform"] == "suite":
                has_suite = True
                leads += int(f["leads"] or 0)
                ex = f.get("extras") or {}
                appts += int(ex.get("appointments") or ex.get("bookings") or 0)
                continue
            if f["platform"] in store.OUTCOME_PLATFORMS:
                # Phone calls are outcomes with no spend: a $0 platform
                # column on the cost report would read as a media buy that
                # cost nothing, which is not what a call is.
                continue
            by_plat[f["platform"]] = by_plat.get(f["platform"], Decimal(0)) + Decimal(f["spend"])
        platforms_seen.update(by_plat)
        spend = sum(by_plat.values(), Decimal(0))
        sold_lines = [b for b in lines if b["client"] == key and _line_in_month(b, rng["start"], rng["month_end"])
                      and b.get("sold_amount") is not None]
        sold_month = sum((Decimal(b["sold_amount"]) for b in sold_lines), Decimal(0)) if sold_lines else None
        sold = (sold_month * days_elapsed / days_in_month) if sold_month is not None else None
        margin = (sold - spend) if sold is not None else None
        margin_pct = (float(margin / sold * 100) if sold else None) if margin is not None else None
        any_outcomes = any_outcomes or has_suite
        rows.append({
            "client": key, "client_name": c["client_name"],
            "spend": _q(spend), "by_platform": {p: _q(v) for p, v in by_plat.items()},
            "sold": _q(sold) if sold is not None else None,
            "sold_month": _q(sold_month) if sold_month is not None else None,
            "sold_lines": len(sold_lines),
            "margin": _q(margin) if margin is not None else None,
            "margin_pct": round(margin_pct, 1) if margin_pct is not None else None,
            "has_suite": has_suite, "leads": leads if has_suite else None,
            "appointments": appts if has_suite else None,
            "cpl": _q(spend / leads) if has_suite and leads else None,
            "cpa": _q(spend / appts) if has_suite and appts else None,
        })
    rows.sort(key=lambda r: -r["spend"])
    total_spend = sum((r["spend"] for r in rows), Decimal(0))
    sold_rows = [r for r in rows if r["sold"] is not None]
    total_sold = sum((r["sold"] for r in sold_rows), Decimal(0)) if sold_rows else None
    total_sold_month = sum((r["sold_month"] for r in sold_rows), Decimal(0)) if sold_rows else None
    total_margin = (total_sold - sum((r["spend"] for r in sold_rows), Decimal(0))) if total_sold is not None else None
    total_leads = sum(r["leads"] or 0 for r in rows if r["has_suite"])
    total_appts = sum(r["appointments"] or 0 for r in rows if r["has_suite"])
    suite_spend = sum((r["spend"] for r in rows if r["has_suite"]), Decimal(0))
    totals = {
        "spend": _q(total_spend), "sold": _q(total_sold) if total_sold is not None else None,
        "sold_month": _q(total_sold_month) if total_sold_month is not None else None,
        "margin": _q(total_margin) if total_margin is not None else None,
        "margin_pct": round(float(total_margin / total_sold * 100), 1) if total_sold else None,
        "by_platform": {p: _q(sum((r["by_platform"].get(p, Decimal(0)) for r in rows), Decimal(0)))
                        for p in platforms_seen},
        "leads": total_leads if any_outcomes else None,
        "appointments": total_appts if any_outcomes else None,
        "cpl": _q(suite_spend / total_leads) if any_outcomes and total_leads else None,
        "cpa": _q(suite_spend / total_appts) if any_outcomes and total_appts else None,
    }
    return {
        "month": rng, "rows": rows, "totals": totals, "outcomes": any_outcomes,
        "platforms": sorted(platforms_seen), "platform_labels": {p: store.platform_label(p) for p in platforms_seen},
        "measured": True, "note": "", "last_sync": store.iso(store.last_synced_at()),
        "pacing_run_at": store.iso(store.latest_run_at()),
        "months": _month_options(today),
        "days_elapsed": days_elapsed, "days_in_month": days_in_month,
        "partial": days_elapsed < days_in_month,
    }


def _cost_unmeasured(rng: dict, today: date, why: str) -> dict:
    """The report's shape with nothing in it and ``measured`` False -- the
    page draws the reason rather than a complete table of noughts, and
    hub/report_cache.py would refuse to hold it."""
    return {"month": rng, "rows": [], "totals": {"spend": _q(0), "sold": None, "sold_month": None,
                                                "margin": None, "margin_pct": None, "by_platform": {},
                                                "leads": None, "appointments": None, "cpl": None, "cpa": None},
            "outcomes": False, "platforms": [], "platform_labels": {},
            "measured": False, "note": why, "last_sync": None, "pacing_run_at": None,
            "months": _month_options(today), "days_elapsed": 0, "days_in_month": 0, "partial": False}


def _month_options(today: date, n: int = 12) -> list[dict]:
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append({"key": f"{y:04d}-{m:02d}", "label": f"{calendar.month_name[m]} {y}"})
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def cost_csv(data: dict) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    plats = data["platforms"]
    head = ["client", "month", "media_spend"] + [f"spend_{p}" for p in plats] + ["sold_to_date", "sold_month", "gross_margin", "margin_pct"]
    if data["outcomes"]:
        head += ["leads", "cost_per_lead", "appointments", "cost_per_appointment"]
    w.writerow(head)
    for r in data["rows"]:
        row = [r["client_name"], data["month"]["key"], r["spend"]] + [r["by_platform"].get(p, "") for p in plats]
        row += [r["sold"] if r["sold"] is not None else "", r["sold_month"] if r["sold_month"] is not None else "",
                r["margin"] if r["margin"] is not None else "",
                r["margin_pct"] if r["margin_pct"] is not None else ""]
        if data["outcomes"]:
            row += [r["leads"] if r["leads"] is not None else "", r["cpl"] if r["cpl"] is not None else "",
                    r["appointments"] if r["appointments"] is not None else "", r["cpa"] if r["cpa"] is not None else ""]
        w.writerow(row)
    t = data["totals"]
    row = ["TOTAL", data["month"]["key"], t["spend"]] + [t["by_platform"].get(p, "") for p in plats]
    row += [t["sold"] if t["sold"] is not None else "", t["sold_month"] if t["sold_month"] is not None else "",
            t["margin"] if t["margin"] is not None else "",
            t["margin_pct"] if t["margin_pct"] is not None else ""]
    if data["outcomes"]:
        row += [t["leads"] if t["leads"] is not None else "", t["cpl"] if t["cpl"] is not None else "",
                t["appointments"] if t["appointments"] is not None else "", t["cpa"] if t["cpa"] is not None else ""]
    w.writerow(row)
    return buf.getvalue()
