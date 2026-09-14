"""Monthly promises as a schedule, checked against the work log.

`hub/proposal_plan.py` builds the list of what a proposal promises every
month -- the report, the month's SEO work, the sales video, the posts -- and
a person keeps each one once. Then nothing asked about month two. A kept
promise was a fact about the plan and never a question about the calendar,
and the things a client notices when they stop are exactly these.

This module turns each kept monthly item into a row per month since launch
and says, for each, one of five things:

* **landed** -- the activity log holds work of that kind for this client in
  that month, and the row names it (which tool, which day);
* **marked** -- a person recorded the month done, with who and when, because
  nothing here logs that a report was sent to a client;
* **due** -- this month, before the ``DUE_DAY``, nothing yet;
* **missed** -- the month is over, or this month is past the ``DUE_DAY``, and
  nothing landed and nobody marked it;
* **not measured** -- the log does not reach back that far, or could not be
  read at all, so the absence of a row means nothing.

Four rules hold it up.

**Derived on read, never stored.** The schedule is arithmetic over the plan's
launch date, the calendar and the log; a stored copy would outlive the
launch date being corrected and the two gunicorn workers would disagree
about which copy is current -- `hub/creative_evergreen.py`'s rule. The one
thing written is a mark, keyed on the run, the item and the month, applied
on every read like the evergreen overlay.

**What proves a promise landed is a table, and every module in it is one
the work log can name.** `proposal_plan.PROMISE_KINDS` maps a kind to the
modules and events that count; `test_proposal_promises.py` holds every
module there to `client_brand.WORK_KINDS`, because a row the work log drops
on the way to the client's record is one this cannot see either -- the
`display_ads` failure, one reader over. A kind with no evidence modules is
recorded by hand only, and the row says so rather than reading a missing
mark as a missing report.

**Housekeeping is drawn and never raised.** Reviewing bids and checking
frequency are on the plan and get their month strip; only a *deliverable*
kind reaches the QA report, the client health issue and the Client 360
count. A report that fires on "review search terms" for every client every
month is one people stop reading, and it takes the missed sales video with
it.

**A month the log cannot answer for is not a miss.** `client_brand.work_index()`
reports how far back it read; a tracked promise's month before that horizon
is *not measured*, and a log that could not be read at all makes every
tracked month not measured. Reading either as "nothing landed" would be a
report accusing the whole team on the strength of a rotated file.
"""
from __future__ import annotations

import calendar
import os
import re
from datetime import date, datetime, timezone

from hub import jsonstore

# Nothing landing by this day of the month is a promise not kept this month;
# before it the month is still in play. The 25th because a month's work
# ordinarily ships in its last week, and a report that fires on the 3rd is a
# report about work nobody has had time to do.
DUE_DAY = 25
# How far back the plan page walks. Twelve months is the longest term a
# proposal here sells; older than that is history, not a schedule.
MAX_MONTHS = 12
# How far back the QA report and the client health issue look. This month and
# last: "promised this month, nothing landed by the 25th" is the question, and
# a plan nobody has marked for a year would otherwise raise twelve rows per
# promise on a report whose job is to say what to act on this week. Older
# misses are counted and named, and the plan page still shows them.
REPORT_MONTHS = 2
REPORT_KEY = "monthly-promises"
MAX_NOTE = 400

LANDED, MARKED, DUE, MISSED, NOT_MEASURED = ("landed", "marked", "due", "missed", "not_measured")
STATE_LABELS = {
    LANDED: "landed",
    MARKED: "marked done",
    DUE: f"due by the {DUE_DAY}th",
    MISSED: "missed",
    NOT_MEASURED: "not measured",
}
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ---------------------------------------------------------------------------
# Months
# ---------------------------------------------------------------------------
def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_label(key: str) -> str:
    """"September 2026" for "2026-09"; the key itself if it will not parse."""
    try:
        y, m = int(key[:4]), int(key[5:7])
        return f"{calendar.month_name[m]} {y}"
    except (ValueError, IndexError):
        return str(key or "")


def month_short(key: str, today: date | None = None) -> str:
    """"Sep", or "Sep 2025" where the year is not this one."""
    today = today or date.today()
    try:
        y, m = int(key[:4]), int(key[5:7])
    except (ValueError, IndexError):
        return str(key or "")
    return calendar.month_abbr[m] + (f" {y}" if y != today.year else "")


def next_month(key: str) -> str:
    y, m = int(key[:4]), int(key[5:7])
    return f"{y + 1:04d}-01" if m == 12 else f"{y:04d}-{m + 1:02d}"


def first_month_after(launch: date) -> str:
    """The first month a monthly promise is due: the month after launch, the
    same reading `proposal_plan.resolve()` puts on the items as `starts`."""
    return next_month(month_key(launch))


def months_between(first: str, today: date) -> list[str]:
    """Every month from `first` through the current one, oldest first, capped
    to the most recent MAX_MONTHS. Empty when the first month is still ahead."""
    current = month_key(today)
    if not first or first > current:
        return []
    out, cursor = [], first
    while cursor <= current and len(out) < 400:
        out.append(cursor)
        cursor = next_month(cursor)
    return out[-MAX_MONTHS:]


# ---------------------------------------------------------------------------
# Marks -- the one thing written
# ---------------------------------------------------------------------------
def _marks_path() -> str:
    return os.path.join(jsonstore.data_dir("hub"), "promise_marks.json")


def mark_key(run_id, item_id: str, month: str) -> str:
    """The key a mark is filed under, and the subject a client-health issue
    about the same promise-month carries -- one spelling, so the health
    report can tell a marked month from an open one without a second table."""
    return f"{int(run_id)}|{str(item_id or '')}|{str(month or '')}"


def marks() -> dict:
    """`{mark key: mark}`. Never raises -- a caller is mid-render."""
    try:
        rows = jsonstore.read_json(_marks_path(), default={})
    except Exception:                                   # noqa: BLE001
        return {}
    return rows if isinstance(rows, dict) else {}


def _forget_report() -> None:
    """A mark takes a row off the QA report, so the day's stored copy goes
    with it -- beside the write, the `qa.forget()` rule, or the row is still
    there on the next open and the button reads as having done nothing."""
    try:
        from hub import qa
        qa.forget(REPORT_KEY)
    except Exception:                                   # noqa: BLE001
        pass


def mark(run_id, item_id: str, month: str, *, actor: str = "", note: str = "") -> dict:
    """Record a promise-month as done by hand. Never raises."""
    item_id = str(item_id or "").strip()
    month = str(month or "").strip()
    if not item_id:
        return {"ok": False, "error": "No promise named."}
    if not _MONTH_RE.match(month):
        return {"ok": False, "error": "The month must be written YYYY-MM."}
    try:
        key = mark_key(run_id, item_id, month)
    except (TypeError, ValueError):
        return {"ok": False, "error": "No run named."}
    row = {
        "run": int(run_id), "item": item_id, "month": month,
        # Somebody's statement that a promise was kept, and one nobody can
        # attribute is one nobody can revisit.
        "by": str(actor or "")[:120],
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": str(note or "")[:MAX_NOTE],
    }

    def _put(data):
        data = data if isinstance(data, dict) else {}
        data[key] = row
        return data

    try:
        jsonstore.update_json(_marks_path(), _put, default={})
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"The mark could not be saved ({type(exc).__name__})."}
    _forget_report()
    return {"ok": True, "key": key, "mark": row}


def unmark(run_id, item_id: str, month: str) -> dict:
    """Take a hand mark back. Never raises; a mark that was not there is
    said so rather than reported removed."""
    try:
        key = mark_key(run_id, item_id, month)
    except (TypeError, ValueError):
        return {"ok": False, "error": "No run named."}
    found = {"hit": False}

    def _drop(data):
        data = data if isinstance(data, dict) else {}
        if key not in data:
            return None
        found["hit"] = True
        data.pop(key, None)
        return data

    try:
        jsonstore.update_json(_marks_path(), _drop, default={})
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"The mark could not be removed ({type(exc).__name__})."}
    if not found["hit"]:
        return {"ok": False, "error": "That month was not marked."}
    _forget_report()
    return {"ok": True, "key": key}


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def client_rows(client: str, work: dict | None = None) -> tuple[list[dict], dict]:
    """This client's work rows -- the group's too, the way Client 360's work
    card reads across a group -- out of one `work_index()` read. Returns
    `(rows, index)` so a caller that passed no index gets the one built."""
    from hub import client_brand
    index = work if isinstance(work, dict) else client_brand.work_index()
    names = [str(client or "")]
    try:
        from hub import client_groups
        for n in client_groups.member_names(str(client or "")):
            if n and n not in names:
                names.append(n)
    except Exception:                                   # noqa: BLE001
        pass
    by = index.get("rows") or {}
    rows: list[dict] = []
    for n in names:
        rows.extend(by.get(_norm(n)) or [])
    return rows, index


def _matches(evidence, row: dict) -> bool:
    module = str(row.get("module") or "")
    action = str(row.get("action") or "")
    for mod, fragments in evidence:
        if module != mod:
            continue
        if not fragments or any(f in action for f in fragments):
            return True
    return False


def evidence_in(kind: str, rows: list[dict], month: str, cap: int = 5) -> list[dict]:
    """The work rows that prove a promise of this kind landed in a month,
    newest first, capped -- a cell is a sentence, not a log."""
    from hub import proposal_plan
    rule = proposal_plan.promise_kind(kind)
    if not rule["evidence"]:
        return []
    hits = [r for r in rows
            if str(r.get("when") or "")[:7] == month and _matches(rule["evidence"], r)]
    hits.sort(key=lambda r: str(r.get("when") or ""), reverse=True)
    return [{"when": str(r.get("when") or "")[:10], "source": r.get("source") or "",
             "action": r.get("action") or "", "detail": r.get("detail") or ""}
            for r in hits[:cap]]


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------
def _month_state(*, month: str, current: str, today: date, tracked: bool,
                 evidence: list, horizon: str, log_error: str) -> tuple[str, str]:
    """One month's state for one promise, and the reason where the state is
    not measured. Marks are decided before this is asked."""
    if tracked:
        if log_error:
            return NOT_MEASURED, log_error
        if evidence:
            return LANDED, ""
        if horizon and horizon[:7] > month:
            return NOT_MEASURED, (f"The activity log only reaches back to "
                                  f"{horizon[:10]}, so nothing can be said about this month.")
    if month == current:
        return (DUE if today.day <= DUE_DAY else MISSED), ""
    return MISSED, ""


def schedule(plan: dict, *, run_id, client: str, today: date | None = None,
             work: dict | None = None, marks_index: dict | None = None) -> dict:
    """Every kept monthly promise on one plan, month by month since launch.

    `measured` is False only where there is no launch date to measure from --
    that is the plan's own gap and the answer names it. A log that could not
    be read is `measured` with every tracked month *not measured*, because
    the marks and the hand-recorded promises still answer.
    """
    from hub import proposal_plan
    today = today or date.today()
    plan = plan or {}
    resolved = plan.get("resolved") if isinstance(plan.get("resolved"), dict) else None
    if resolved is None:
        resolved = proposal_plan.resolve(plan).get("resolved") or {}
    launch = proposal_plan.parse_day(resolved.get("launch_date"))
    kept = proposal_plan.kept_items(plan, "monthly")
    out = {
        "measured": True, "why": "", "not_started": False,
        "launch_date": launch.isoformat() if launch else "",
        "first_month": "", "current_month": month_key(today), "due_day": DUE_DAY,
        "months": [], "items": [], "horizon": "", "log_error": "",
        "counts": {"kept": len(kept), "deliverables": 0, "due": 0, "missed": 0,
                   "missed_recent": 0, "missed_older": 0, "landed": 0, "marked": 0,
                   "housekeeping_open": 0},
        "missed_items": [],
    }
    if not launch:
        out["measured"] = False
        out["why"] = ("The launch date has not been answered, so there is no month to "
                      "measure the promises from.")
        out["items"] = [_bare_item(it) for it in kept]
        return out
    first = first_month_after(launch)
    out["first_month"] = first
    months = months_between(first, today)
    if not months:
        out["not_started"] = True
        out["why"] = f"The first monthly promises are due in {month_label(first)}."
        out["items"] = [_bare_item(it) for it in kept]
        return out
    out["months"] = [{"month": m, "label": month_label(m), "short": month_short(m, today)}
                     for m in months]
    rows, index = client_rows(client, work)
    horizon = str(index.get("horizon") or "")
    log_error = str(index.get("error") or "")
    out["horizon"] = horizon[:10]
    out["log_error"] = log_error
    marks_index = marks_index if isinstance(marks_index, dict) else marks()
    current = month_key(today)
    recent = set(months[-REPORT_MONTHS:])
    counts = out["counts"]
    for it in kept:
        entry = _bare_item(it)
        kind = entry["kind"]
        tracked = entry["tracked"]
        deliverable = entry["deliverable"]
        if deliverable:
            counts["deliverables"] += 1
        for m in months:
            mk = marks_index.get(mark_key(run_id, it.get("id") or "", m))
            ev: list = []
            if mk:
                state, reason = MARKED, ""
            else:
                ev = evidence_in(kind, rows, m) if tracked else []
                state, reason = _month_state(month=m, current=current, today=today,
                                             tracked=tracked, evidence=ev,
                                             horizon=horizon, log_error=log_error)
            cell = {"month": m, "label": month_label(m), "short": month_short(m, today),
                    "state": state, "state_label": STATE_LABELS[state],
                    "evidence": ev, "mark": dict(mk) if mk else None, "reason": reason}
            entry["months"].append(cell)
            if m == current:
                entry["this_month"] = state
            if state == MISSED:
                entry["missed"].append(m)
                if deliverable:
                    counts["missed"] += 1
                    if m in recent:
                        counts["missed_recent"] += 1
                        out["missed_items"].append({
                            "item": it.get("id") or "", "title": entry["title"],
                            "kind": kind, "kind_label": entry["kind_label"],
                            "month": m, "month_label": month_label(m),
                            "key": mark_key(run_id, it.get("id") or "", m),
                        })
                    else:
                        counts["missed_older"] += 1
                else:
                    counts["housekeeping_open"] += 1
            elif state == DUE and deliverable:
                counts["due"] += 1
            elif state == LANDED:
                counts["landed"] += 1
            elif state == MARKED:
                counts["marked"] += 1
        out["items"].append(entry)
    return out


def _bare_item(it: dict) -> dict:
    from hub import proposal_plan
    kind = str(it.get("kind") or "")
    rule = proposal_plan.promise_kind(kind)
    return {
        "id": it.get("id") or "", "title": it.get("title") or "",
        "channel": it.get("channel") or "", "channel_name": it.get("channel_name") or "",
        "kind": kind, "kind_label": rule["label"],
        "deliverable": bool(rule["deliverable"]),
        # Tracked means the log can prove it; untracked is recorded by hand.
        "tracked": bool(rule["evidence"]),
        "months": [], "missed": [], "this_month": None,
    }


def counts(sched: dict) -> dict:
    """The numbers a record or a report reads off a schedule -- never the
    months. What `_run_plan_summary()` carries as `promises`."""
    c = (sched or {}).get("counts") or {}
    return {
        "measured": bool((sched or {}).get("measured")),
        "why": (sched or {}).get("why") or "",
        "not_started": bool((sched or {}).get("not_started")),
        "month": (sched or {}).get("current_month") or "",
        "due": int(c.get("due") or 0),
        "missed": int(c.get("missed_recent") or 0),
        "missed_older": int(c.get("missed_older") or 0),
        "landed": int(c.get("landed") or 0),
        "marked": int(c.get("marked") or 0),
        "log_error": (sched or {}).get("log_error") or "",
        "missed_items": list((sched or {}).get("missed_items") or []),
    }


# ---------------------------------------------------------------------------
# The book
# ---------------------------------------------------------------------------
def report(today: date | None = None) -> dict:
    """Every deliverable promise-month across the open plans, for the QA
    report. One work-log read for the whole book, one marks read, one plan
    per run. `measured` is False only where the runs table would not answer;
    a log that could not be read is named and its months read not measured."""
    from hub import client_brand, proposal_execution as pe
    today = today or date.today()
    try:
        runs = pe.open_runs()
    except Exception as exc:                            # noqa: BLE001
        return {"measured": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                "rows": [], "no_launch": [], "not_started": [], "older_missed": 0,
                "runs": 0, "horizon": "", "log_error": ""}
    work = client_brand.work_index()
    mk = marks()
    rows, no_launch, not_started = [], [], []
    older = 0
    for run in runs:
        try:
            plan = pe.plan_for(run)
            sched = schedule(plan, run_id=run.id, client=run.client, today=today,
                             work=work, marks_index=mk)
        except Exception as exc:                        # noqa: BLE001
            no_launch.append({"run": run.id, "client": run.client,
                              "title": run.proposal_title or "Proposal",
                              "url": f"/proposal-execution?run={run.id}",
                              "why": f"The plan could not be read ({type(exc).__name__})."})
            continue
        base = {"run": run.id, "client": run.client, "title": run.proposal_title or "Proposal",
                "url": f"/proposal-execution?run={run.id}"}
        if not sched["measured"]:
            if sched["counts"]["kept"]:
                no_launch.append(dict(base, why=sched["why"]))
            continue
        if sched["not_started"]:
            not_started.append(dict(base, why=sched["why"]))
            continue
        older += int(sched["counts"].get("missed_older") or 0)
        recent = {m["month"] for m in sched["months"][-REPORT_MONTHS:]}
        for it in sched["items"]:
            if not it["deliverable"]:
                continue
            for cell in it["months"]:
                if cell["month"] not in recent:
                    continue
                rows.append(dict(base, item=it["id"], promise=it["title"],
                                 kind=it["kind"], kind_label=it["kind_label"],
                                 tracked=it["tracked"], month=cell["month"],
                                 month_label=cell["label"], state=cell["state"],
                                 state_label=cell["state_label"], evidence=cell["evidence"],
                                 mark=cell["mark"], reason=cell["reason"],
                                 key=mark_key(run.id, it["id"], cell["month"])))
    return {"measured": True, "error": "", "rows": rows, "no_launch": no_launch,
            "not_started": not_started, "older_missed": older, "runs": len(runs),
            "horizon": str(work.get("horizon") or "")[:10],
            "log_error": str(work.get("error") or ""), "today": today.isoformat()}


def note(data: dict) -> str:
    """The sentence the report carries: what was read, how far back, and what
    is deliberately not on it."""
    if not data.get("measured"):
        return ("The execution runs could not be read"
                + (f" ({data.get('error')})" if data.get("error") else "") + ".")
    parts = [f"Every promise the open plans keep on the monthly list, for this month and last, "
             f"against the activity log. A month is missed once nothing has landed by the "
             f"{DUE_DAY}th, or once it is over; a promise nothing here logs (a report sent to "
             f"the client) is recorded by hand with Mark done. Housekeeping items -- reviewing "
             f"bids, checking frequency -- are on each plan and are never raised here."]
    if data.get("log_error"):
        parts.append(f"{data['log_error']} Tracked promises read as not measured, not as missed.")
    elif data.get("horizon"):
        parts.append(f"The activity log was read back to {data['horizon']}.")
    if data.get("older_missed"):
        n = data["older_missed"]
        parts.append(f"{n} older missed month{'' if n == 1 else 's'} "
                     f"{'is' if n == 1 else 'are'} on the plans and not listed here.")
    if data.get("no_launch"):
        n = len(data["no_launch"])
        parts.append(f"{n} plan{'' if n == 1 else 's'} with kept promises "
                     f"{'has' if n == 1 else 'have'} no launch date answered, so "
                     f"{'its' if n == 1 else 'their'} months cannot be measured.")
    return " ".join(parts)


__all__ = [
    "DUE_DAY", "MAX_MONTHS", "REPORT_MONTHS", "REPORT_KEY", "STATE_LABELS",
    "LANDED", "MARKED", "DUE", "MISSED", "NOT_MEASURED",
    "month_key", "month_label", "month_short", "first_month_after", "months_between",
    "mark_key", "marks", "mark", "unmark", "client_rows", "evidence_in",
    "schedule", "counts", "report", "note",
]
