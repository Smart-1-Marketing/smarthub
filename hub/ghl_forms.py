"""Form submissions from Smart 1 Suite, compared against the prior period.

A client's Suite account may have twenty forms and three that anyone actually
fills in. Listing all twenty buries the signal, so **forms with no submissions
in the period are left out entirely** — an empty row tells you nothing you
couldn't infer from its absence.

The comparison is the point. "14 submissions" is a number; "14, down 30% from
20" is something you act on. Every period is compared against the equivalent
one before it — this month against last month, this quarter against last
quarter — so the shapes are alike. Comparing a part-finished month against a
whole one would show a fall every time, which is the classic way this kind of
panel misleads.

## Endpoints

    GET /forms/               list forms for a location
    GET /forms/submissions    submissions, filtered by form and date range

Both need the `forms.readonly` scope on the Private Integration Token.
"""
from __future__ import annotations

import os
from calendar import monthrange
from datetime import date, timedelta

BASE = "https://services.leadconnectorhq.com"
VERSION = "2021-07-28"
TIMEOUT = 25

PERIODS = ("this_month", "last_month", "this_quarter", "this_year", "last_30")


def _token() -> str:
    for name in ("GHL_PRIVATE_TOKEN", "SMART1SUITE_PRIVATE_TOKEN"):
        v = (os.environ.get(name) or "").strip().strip('"').strip("'")
        if v and v != "pit-...":
            return v
    return ""


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}", "Version": VERSION,
            "Accept": "application/json"}


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _month_end(d: date) -> date:
    return d.replace(day=monthrange(d.year, d.month)[1])


def window(period: str, today: date | None = None) -> tuple[date, date, date, date, str]:
    """(start, end, prev_start, prev_end, label) for a named period.

    The previous window is always the same *kind* of period, not simply the
    same number of days back. Last month against this month compares like
    with like even though one is 28 days and the other 31.
    """
    t = today or date.today()

    if period == "last_month":
        end = _month_start(t) - timedelta(days=1)
        start = _month_start(end)
        p_end = start - timedelta(days=1)
        return start, end, _month_start(p_end), p_end, start.strftime("%B %Y")

    if period == "this_quarter":
        q = (t.month - 1) // 3
        start = date(t.year, q * 3 + 1, 1)
        end = t
        p_start = (date(start.year - 1, 10, 1) if q == 0
                   else date(start.year, (q - 1) * 3 + 1, 1))
        # Same number of days INTO the previous quarter, not the whole of it.
        # 50 days of this quarter against 91 of the last would report a fall
        # every single time — the panel would be wrong more often than right.
        p_end = min(p_start + timedelta(days=(end - start).days),
                    start - timedelta(days=1))
        return start, end, p_start, p_end, f"Q{q + 1} {t.year}"

    if period == "this_year":
        start, end = date(t.year, 1, 1), t
        p_start = date(t.year - 1, 1, 1)
        # Year to date against the same span last year, for the same reason.
        p_end = min(p_start + timedelta(days=(end - start).days),
                    date(t.year - 1, 12, 31))
        return start, end, p_start, p_end, f"{t.year} to date"

    if period == "last_30":
        start, end = t - timedelta(days=29), t
        return start, end, start - timedelta(days=30), start - timedelta(days=1), "Last 30 days"

    # default: this month so far, against the same number of days last month.
    start, end = _month_start(t), t
    p_start = _month_start(start - timedelta(days=1))
    # Same day-count, so a part-month isn't compared against a whole one.
    p_end = min(p_start + timedelta(days=(end - start).days), _month_end(p_start))
    return start, end, p_start, p_end, t.strftime("%B %Y")


def _get(path: str, params: dict) -> dict:
    import requests
    try:
        r = requests.get(f"{BASE}{path}", headers=_headers(), params=params,
                         timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"Couldn't reach Smart 1 Suite ({type(exc).__name__}).")
    if r.status_code in (401, 403):
        raise RuntimeError(
            "Smart 1 Suite rejected the request. The Private Integration "
            "Token probably lacks the forms.readonly scope.")
    if not r.ok:
        raise RuntimeError(f"Smart 1 Suite returned HTTP {r.status_code}.")
    try:
        return r.json()
    except ValueError:
        return {}


def forms(location_id: str) -> list[dict]:
    data = _get("/forms/", {"locationId": location_id, "limit": 100, "skip": 0})
    return data.get("forms") or data.get("data") or []


def _count(location_id: str, form_id: str, start: date, end: date) -> int:
    """Submissions for one form in one window.

    Counts rather than collecting: the card shows totals, and pulling every
    submission body for twenty forms across two periods would be slow and
    would drag personal data into a page that doesn't display it.
    """
    total, page = 0, 1
    while page <= 20:
        data = _get("/forms/submissions", {
            "locationId": location_id, "formId": form_id,
            "startAt": start.isoformat(), "endAt": end.isoformat(),
            "page": page, "limit": 100})
        rows = data.get("submissions") or data.get("data") or []
        total += len(rows)
        meta = data.get("meta") or {}
        if meta.get("total") is not None and page == 1:
            return int(meta["total"])       # trust the API's own count
        if len(rows) < 100:
            break
        page += 1
    return total


def _delta(now: int, before: int | None) -> dict:
    """Direction and percentage change, honest about a zero baseline.

    `before` is None when the previous period could not be counted, which is
    a different thing from a previous period of zero: one means nobody filled
    the form in, the other means we could not look. Reading the second as the
    first prints "14 vs 0" and an up-arrow over a comparison that was never
    made -- the confident wrong answer this Hub keeps having to undo.
    """
    if before is None:
        return {"direction": "flat", "percent": None,
                "text": f"{now}, previous period not measured",
                "note": "The previous period could not be counted, so there "
                        "is nothing to compare against."}
    if before == 0:
        return {"direction": "up" if now else "flat",
                "percent": None,
                "text": f"{now} vs 0" if now else "none either period",
                "note": "No submissions in the previous period, so a "
                        "percentage would be meaningless."}
    pct = round((now - before) / before * 100)
    return {"direction": "up" if pct > 0 else "down" if pct < 0 else "flat",
            "percent": abs(pct),
            "text": f"{'+' if pct > 0 else ''}{pct}% vs {before}"}


def summary(client: str, location_id: str = "", period: str = "this_month",
            url: str = "") -> dict:
    """Forms with submissions in the period, against the one before it."""
    # The forms API keys on a *location*. This previously fell back to
    # GHL_COMPANY_ID / SUITE_COMPANY_ID, which are agency ids — so with no
    # explicit location it asked for the agency's forms and got an empty list
    # back, indistinguishable from a client who simply has no submissions.
    # Both ids come from hub.config, which knows every spelling each answers
    # to. Read here directly they knew two of three, and a Hub that had set
    # SMART1_MARKETING_LOCATION_ID reported "no location for this client"
    # against a location that was configured.
    from hub.config import settings as _cfg
    loc = str(location_id or "").strip()

    # A location the caller did not name is resolved from the CLIENT, never
    # from a configured default. It used to fall back to ghl_lead_location_id
    # -- which config.py describes as the sub-account "leads are written into",
    # meaning Smart 1's own. Client 360 passes no location at all, so every
    # client's card was answered with the agency's own form submissions under
    # that client's name: not an empty card but a wrong one, and wrong
    # identically for all of them, which is why it read as the feature simply
    # not working rather than as a bug.
    #
    # There is no default that is right here. A form belongs to exactly one
    # sub-account, so a client whose sub-account nobody has recorded has no
    # answer, and saying so is the answer.
    if not loc and str(client or "").strip():
        try:
            from hub.suite_accounts import location_for
            found = location_for(client, url)
        except Exception as exc:                              # noqa: BLE001
            found = {"state": "not_measured",
                     "detail": "The client-to-sub-account mapping could not "
                               f"be read ({type(exc).__name__})."}
        loc = str(found.get("location_id") or "").strip()
        if not loc:
            # Three states, three destinations. "Nobody recorded it" is a
            # setup step; "we could not look" is an outage; and neither is
            # "this client has no form submissions", which is what the card
            # said before.
            return {"error": found.get("detail")
                    or "No Smart 1 Suite sub-account is recorded for this "
                       "client, so there are no forms to read.",
                    "state": found.get("state", "not_connected"),
                    "measured": False, "forms": [], "period": period}

    company = _cfg.ghl_company_id.strip()
    if loc and company and loc == company:
        return {"error": "That location id is the agency company id, not a "
                         "sub-account. A company id here reads every form as "
                         "missing.",
                "measured": False, "forms": [], "period": period}
    if not _token():
        return {"error": "No Smart 1 Suite token is configured, so forms "
                         "cannot be read.",
                "measured": False, "forms": [], "period": period}
    if not loc:
        return {"error": "No Smart 1 Suite sub-account for this client.",
                "measured": False, "forms": [], "period": period}

    start, end, p_start, p_end, label = window(period)
    try:
        all_forms = forms(loc)
    except RuntimeError as exc:
        return {"error": str(exc), "forms": [], "period": period}

    rows, skipped, unreadable = [], 0, 0
    for f in all_forms:
        fid = f.get("id") or f.get("_id")
        if not fid:
            continue
        try:
            now = _count(loc, fid, start, end)
        except RuntimeError:
            # Counted, never dropped. This used to `continue` BEFORE the
            # skipped tally, so a form whose count failed left no trace at
            # all -- and when every form failed the card said "No form
            # submissions in <month>" with nothing anywhere reporting that
            # nothing had been read. A failure that renders as a zero is
            # worse than an error, because nobody goes looking for it.
            unreadable += 1
            continue
        if not now:
            # A form nobody filled in is noise, not information.
            skipped += 1
            continue
        try:
            before = _count(loc, fid, p_start, p_end)
        except RuntimeError:
            # None, not 0: see _delta. A previous period we could not count
            # is not a previous period of nothing.
            before = None
        rows.append({"id": fid, "name": f.get("name") or "(unnamed form)",
                     "submissions": now, "previous": before,
                     **_delta(now, before)})

    rows.sort(key=lambda r: -r["submissions"])
    total_now = sum(r["submissions"] for r in rows)
    # A total is only a total of what was counted. Any row whose baseline is
    # None makes the aggregate baseline unmeasured too, rather than quietly
    # summing the readable ones and comparing against a smaller number --
    # which would report a rise that is an artifact of the failure.
    total_before = (None if any(r["previous"] is None for r in rows)
                    else sum(r["previous"] for r in rows))

    # Nothing readable at all is a failure, not an empty period. Said as an
    # error so the card stops rather than drawing a confident nought.
    if unreadable and not rows and not skipped:
        return {"error": f"None of the {unreadable} form(s) on this "
                         "sub-account could be read, so there is no count to "
                         "show.",
                "measured": False, "forms": [], "period": period,
                "label": label, "unreadable": unreadable}

    note = []
    if skipped:
        note.append(f"{skipped} form(s) had no submissions this period and "
                    f"are left out.")
    if unreadable:
        # Named rather than folded into the skipped count: "nobody filled it
        # in" and "we could not ask" are different sentences.
        note.append(f"{unreadable} form(s) could not be read, so anything "
                    f"they received is missing from these totals.")
    return {
        "client": client, "period": period, "label": label,
        "range": f"{start.isoformat()} to {end.isoformat()}",
        "compared_to": f"{p_start.isoformat()} to {p_end.isoformat()}",
        "forms": rows,
        "total": total_now,
        "total_previous": total_before,
        **{f"total_{k}": v for k, v in _delta(total_now, total_before).items()},
        "with_submissions": len(rows),
        "no_submissions": skipped,
        "unreadable": unreadable,
        "measured": not unreadable,
        "note": " ".join(note),
    }
