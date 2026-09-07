"""What the Hub's own dashboard says about the live Google Ads accounts.

## Why this exists

`modules/ads_builder/monitoring.py` sweeps every deployed account twice a day
and `store.OptimizationRun` keeps each answer, so the optimization page opens
on a reading rather than on a Scan button. That closed half the gap. The other
half is that **nothing told anybody a reading had arrived**: a client's account
could start spending on nothing overnight, the sweep could find it at 06:00,
and the finding sat in a table nobody opens until a rep happened to walk into
that one tool and pick that one account. Worse, an account whose scan *failed*
looked exactly like an account nobody had got to yet.

There is no mailer and no staff-alerting channel in this Hub, which is the
constraint every version of this problem runs into here. So the honest route
is the one `hub/social_status.py` and `hub/sales_status.py` already take: put
the number where people already look, and make every figure open the rows
behind it.

## Why the Hub's half rather than the module's

The dashboard is a hub route and cannot be reached from inside a mounted
module -- the first trap `CLAUDE.md` names. So the join lives here, the
arrangement `hub/ad_builder_link.py` has for the renderer next door. Nothing
is re-derived: every number below is read from the module's own store.

## The rules

**Nothing here may raise.** It is called while rendering a dashboard half the
company opens. Every failure resolves to `measured: False` with the reason
named, because *no account has anything wrong with it* and *we could not read
the scans* are different answers and only the first means there is nothing to
do.

**Five signals, kept apart.** *Something was found*, *the scan failed*, *the
account has never been swept*, *the sweep has stopped reaching it* and
*something was changed with nobody clicking* send somebody to five different
places. One "needs attention" figure covering all five is a figure nobody can
act on -- the note `hub/sales_status.py` makes about its own five.

**It reads columns and never the scan blobs.** `high_severity_count` is a
column, so counting findings costs nothing. Totalling the *money* behind them
means parsing every account's whole `analyse_rows()` payload -- several
hundred kilobytes each -- on a page that loads on every visit, which is the
cost `hub/social_status.py` refuses for the same reason: a number that costs a
page load is a number somebody turns off. The money is one click away on the
optimization page, which has the blob open anyway.

**Overdue is measured against the scheduler's own interval**, read from
`hub/scheduler.JOBS` rather than restated here. Two descriptions of how often
this is supposed to run is how the tile and the scheduler panel come to
disagree about whether a sweep has stopped.

That reading is also *knowable from any worker*, which the scheduler's own
`_overdue()` deliberately is not: that one answers `None` on the standby
worker because `_state` is per-process. `scanned_at` is a column in a shared
table, so both workers see the same answer and there is no honest reason to
say "not measured" here.

**Each zero says which kind of zero it is.** "Nothing is flagged", "no
deployed proposal carries a Google customer id" and "the sweep has not run
yet" render identically as a nought, and only the first means the book is
clean.

**Nothing is written anywhere.** This is a reading. It does not scan, it does
not apply anything, and it does not record having looked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

MOUNT = "/tools/ads"

# The scheduler job whose cadence decides when a scan is overdue. Named rather
# than its number restated: JOBS is the one description of how often this runs.
JOB = "ads_optimization"

# Twice the interval, the rule `scheduler._overdue()` states -- a sweep is not
# late at one minute past due, and calling it so is how an amber pill stops
# being read. The fallback is this job's own registered cadence and is used
# only where the scheduler will not import at all.
OVERDUE_FACTOR = 2
FALLBACK_INTERVAL_MINUTES = 720

# How far back "changed with nobody clicking" looks. A week rather than a day,
# because this is the one figure on the card that is not a to-do: it is the
# record of unattended work, and somebody who was away on Monday should still
# meet it.
APPLIED_WINDOW_DAYS = 7

AUTO_APPLIED_EVENT = "OPTIMIZATION_AUTO_APPLIED"

# The worst few accounts, by findings. A card is not a report.
ROW_LIMIT = 4


def _unavailable(exc: Exception) -> dict:
    return {"measured": False,
            "error": f"The Google Ads scan history could not be read "
                     f"({type(exc).__name__})."}


def _module():
    from modules.ads_builder import store
    return store


def overdue_after_minutes() -> tuple[float, bool]:
    """How old a scan may be before it is late, and whether we read that.

    Public because the module's own sweep panel reads it too: the card
    counting four accounts as out of date while the page it opens shows three
    is the two-readings-of-one-question failure this repo names most often.

    `(minutes, from_scheduler)`. The second half is carried rather than
    dropped so the card can say the cadence is a fallback -- a threshold
    printed as though it were the configured one, when the configuration could
    not be read, is the confident wrong answer this file exists to avoid.
    """
    try:
        from hub.scheduler import JOBS
        every = float(JOBS[JOB][0])
        return every * OVERDUE_FACTOR, True
    except Exception:                                    # noqa: BLE001
        return FALLBACK_INTERVAL_MINUTES * OVERDUE_FACTOR, False


def _parsed(stamp: str):
    """A stored timestamp as an aware datetime, or None.

    SQLite hands back naive datetimes where Postgres hands back aware ones, so
    a bare comparison raises on one of the two backends -- and the raise would
    land inside the dashboard. Naive values are read as UTC, which is what
    ``store.now()`` writes.
    """
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _account_url(customer_id: str) -> str:
    """The optimization page, opened on one account.

    ``customer_id`` is the spelling that page's own ``boot()`` reads. It is
    worth naming: ``?customer=`` looks right, resolves, renders a 200 and
    selects whichever account came first -- a link that lands on the wrong
    client's scan while reporting nothing wrong, which is exactly what
    "a count is never a link to a page that cannot show it" is about.
    """
    page = f"{MOUNT}/optimization"
    return f"{page}?customer_id={customer_id}" if customer_id else page


def scoreboard(limit: int = ROW_LIMIT) -> dict:
    """What the twice-daily sweep found, for the dashboard.

    Five counts, each opening exactly the accounts it counted, plus the
    unattended changes of the last week.
    """
    try:
        store = _module()
    except Exception as exc:                             # noqa: BLE001
        return _unavailable(exc)

    try:
        accounts = store.deployed_accounts(limit=500)
    except Exception as exc:                             # noqa: BLE001
        return _unavailable(exc)

    if not accounts:
        # A state rather than a failure, and it is somebody's to fix: no
        # proposal has been deployed with a Google customer id on it, so there
        # is nothing for the sweep to reach. Saying "0 accounts need
        # attention" here would be true and would read as a clean book.
        return {"measured": True, "accounts": 0,
                "counts": {"attention": 0, "failed": 0, "never": 0,
                           "stale": 0, "clean": 0},
                "urls": _urls(), "rows": [], "applied": None,
                "line": "No deployed proposal carries a Google customer id, "
                        "so there is no live account to sweep.",
                "empty": "no_accounts"}

    try:
        runs = {r["customer_id"]: r
                for r in store.latest_optimization_runs(limit=len(accounts) + 20)}
    except Exception as exc:                             # noqa: BLE001
        return _unavailable(exc)

    overdue_minutes, from_scheduler = overdue_after_minutes()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=overdue_minutes)

    counts = {"attention": 0, "failed": 0, "never": 0, "stale": 0, "clean": 0}
    findings = 0
    rows: list[dict] = []

    for account in accounts:
        cid = account["customer_id"]
        run = runs.get(cid)
        card = {"customer_id": cid,
                "client": account.get("client_name") or "",
                "url": _account_url(cid),
                "high": 0, "items": 0, "scanned_at": None, "state": ""}

        if run is None:
            counts["never"] += 1
            card["state"] = "never scanned"
            rows.append(card)
            continue

        card["scanned_at"] = run.get("scanned_at")
        card["items"] = int(run.get("item_count") or 0)
        card["high"] = int(run.get("high_severity_count") or 0)

        if run.get("error"):
            # "We looked and it is clean" and "we could not look" are
            # different answers, and the second is fixed somewhere else
            # entirely -- access, a credential, a customer id that moved.
            counts["failed"] += 1
            card["state"] = "last scan failed"
            rows.append(card)
            continue

        when = _parsed(run.get("scanned_at"))
        if when is not None and when < cutoff:
            # Not a finding about the client: the sweep has stopped reaching
            # this account, whether because the scheduler is stuck or because
            # the daily operation budget keeps running out before it gets
            # here. Either way the number beside it describes an old reading.
            counts["stale"] += 1
            card["state"] = "reading is out of date"

        if card["high"]:
            counts["attention"] += 1
            findings += card["high"]
            if not card["state"]:
                card["state"] = "needs attention"
            rows.append(card)
        elif not card["state"]:
            counts["clean"] += 1
        else:
            rows.append(card)

    # Worst first: high-severity findings, then everything else by how long
    # ago we last had an answer. A never-scanned account sorts to the top of
    # that second group, because no reading at all is older than any reading.
    rows.sort(key=lambda r: (-r["high"], r["scanned_at"] or ""))

    applied = _applied(store)

    return {
        "measured": True,
        "accounts": len(accounts),
        "counts": counts,
        "findings": findings,
        "rows": rows[:max(1, int(limit))],
        "more": max(0, len(rows) - max(1, int(limit))),
        "applied": applied,
        "urls": _urls(),
        "overdue_after_hours": round(overdue_minutes / 60.0, 1),
        "cadence_measured": from_scheduler,
        "line": _line(counts, findings, applied, from_scheduler),
        "note": ("Findings are counted, not costed: totalling the spend behind "
                 "them means opening every account's whole scan on a page that "
                 "loads on every visit. The money is on the optimization page."),
    }


# The four states the sweep panel can narrow itself to. Written down here and
# served to the card rather than spelled into the template, because the panel
# reading `?filter=` and the tile building it are two halves of one agreement:
# a filter the page does not know is a link that quietly opens everything.
FILTERS = ("attention", "failed", "never", "stale")


def _urls() -> dict:
    """Every count opens the rows behind it.

    A figure that links to a tool the reader then has to filter themselves is
    the signpost `hub/stale_creative.py` had to stop being.
    """
    page = f"{MOUNT}/optimization"
    urls = {"all": page}
    urls.update({key: f"{page}?filter={key}" for key in FILTERS})
    return urls


def _applied(store) -> dict | None:
    """Changes made to client accounts with nobody having pressed anything.

    `None` is *not measured* and never zero: unattended writes are the one
    thing on this card that must not read as "none happened" when what
    actually happened is that we could not ask.

    It is deliberately a **sentence and not a linked figure**. Every applied
    change is mirrored into the Hub activity log, and `/activity` filters by a
    hand-typed three-entry module list that has no `ads_builder` in it -- so a
    link there opens the whole log unfiltered and the reader concludes the
    count was wrong rather than the filter absent, which is the failure this
    card's own `_urls()` rule exists to refuse. Pointing it somewhere means
    teaching that page to read `?module=` from the URL first.
    """
    try:
        since = datetime.now(timezone.utc) - timedelta(days=APPLIED_WINDOW_DAYS)
        return {"count": store.count_events(AUTO_APPLIED_EVENT, since=since),
                "days": APPLIED_WINDOW_DAYS}
    except Exception:                                    # noqa: BLE001
        return None


def _line(counts: dict, findings: int, applied, from_scheduler: bool) -> str:
    """One sentence, saying which kind of quiet it is.

    An account nobody has swept and an account with nothing wrong with it are
    both a nought in the attention column, and only one of them means the book
    is clean.
    """
    bits: list[str] = []
    if counts["attention"]:
        bits.append(f"{findings} high-severity finding"
                    f"{'' if findings == 1 else 's'} across "
                    f"{counts['attention']} account"
                    f"{'' if counts['attention'] == 1 else 's'}")
    if counts["failed"]:
        bits.append(f"{counts['failed']} account"
                    f"{'' if counts['failed'] == 1 else 's'} we could not scan")
    if counts["never"]:
        bits.append(f"{counts['never']} never swept")
    if counts["stale"]:
        bits.append(f"{counts['stale']} on an out-of-date reading")

    if not bits:
        line = "Every live account was swept and none of them flagged anything."
    else:
        line = bits[0][0].upper() + bits[0][1:]
        if len(bits) > 1:
            line += " · " + " · ".join(bits[1:])
        line += "."

    if applied is None:
        line += (" Unattended changes could not be counted, which is not the "
                 "same as none having been made.")
    elif applied["count"]:
        line += (f" {applied['count']} change"
                 f"{'' if applied['count'] == 1 else 's'} applied automatically "
                 f"in the last {applied['days']} days.")

    if not from_scheduler:
        line += (" The sweep's own cadence could not be read, so out-of-date "
                 "is judged against this job's registered interval.")
    return line
